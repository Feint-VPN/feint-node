"""Real gRPC hot-user contracts with runtime validation/restart failure injection."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import grpc

sys.path.insert(
    0, str(Path(__file__).resolve().parents[4] / "services" / "node" / "src")
)
from adapters.docker_runtime import XrayDockerRuntime
from adapters.singbox_file_store import SingBoxFileStore
from adapters.url_builder import UrlBuilder
from adapters.xray_config import render_xray_config
from adapters.xray_control import COMMAND, MESSAGES
from adapters.xray_socks import socks_outbound
from docker.errors import DockerException
from domain.errors import ConfigRollbackError, SingBoxReloadError
from domain.models import SocksOutboundConfig
from domain.outbound_service import OutboundService
from domain.user_service import UserService
from service_contract import configuration


class HotReloadContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.source, self.rendered = root / "config.json", root / "xray.json"
        self.store = SingBoxFileStore(str(self.source), str(root / "backups"))
        config = configuration()
        config.inbounds = [
            item for item in config.inbounds if item.type in {"vless", "hysteria2"}
        ]
        await self.store.save(config)
        render_xray_config(
            str(self.source), str(self.rendered), "127.0.0.1:10085", control_api=True
        )
        self.users = {}
        self.calls = 0
        self.fail_at = None
        self.failed_removals = set()
        self.outbounds = set()

        async def alter(request, context):
            self.calls += 1
            operation = MESSAGES[request.operation.type].FromString(
                request.operation.value
            )
            bucket = self.users.setdefault(request.tag, {})
            if request.operation.type.endswith("AddUserOperation"):
                bucket[operation.user.email] = operation.user
            else:
                if (request.tag, operation.email) in self.failed_removals:
                    await context.abort(
                        grpc.StatusCode.UNAVAILABLE, "cleanup unavailable"
                    )
                bucket.pop(operation.email)
            if self.calls == self.fail_at:
                await context.abort(
                    grpc.StatusCode.INTERNAL, "failure after application"
                )
            return MESSAGES[COMMAND + "AlterInboundResponse"]()

        async def query(request, context):
            return MESSAGES[COMMAND + "GetInboundUserResponse"](
                users=[
                    user
                    for email, user in self.users.get(request.tag, {}).items()
                    if email == request.email
                ]
            )

        async def list_outbounds(request, context):
            return MESSAGES[COMMAND + "ListOutboundsResponse"](
                outbounds=[
                    MESSAGES["xray.core.OutboundHandlerConfig"](tag=tag)
                    for tag in self.outbounds
                ]
            )

        async def remove_outbound(request, context):
            self.outbounds.remove(request.tag)
            return MESSAGES[COMMAND + "RemoveOutboundResponse"]()

        self.server = grpc.aio.server()
        handlers = {}
        for name, callback, request, response in (
            ("AlterInbound", alter, "AlterInboundRequest", "AlterInboundResponse"),
            (
                "ListOutbounds",
                list_outbounds,
                "ListOutboundsRequest",
                "ListOutboundsResponse",
            ),
            (
                "RemoveOutbound",
                remove_outbound,
                "RemoveOutboundRequest",
                "RemoveOutboundResponse",
            ),
            (
                "GetInboundUsers",
                query,
                "GetInboundUserRequest",
                "GetInboundUserResponse",
            ),
        ):
            handlers[name] = grpc.unary_unary_rpc_method_handler(
                callback,
                request_deserializer=MESSAGES[COMMAND + request].FromString,
                response_serializer=MESSAGES[COMMAND + response].SerializeToString,
            )
        self.server.add_generic_rpc_handlers(
            [grpc.method_handlers_generic_handler(COMMAND + "HandlerService", handlers)]
        )
        port = self.server.add_insecure_port("127.0.0.1:0")
        await self.server.start()
        self.runtime = XrayDockerRuntime(
            str(self.source),
            str(self.rendered),
            api_listen="127.0.0.1:10085",
            api_address=f"127.0.0.1:{port}",
        )
        self.container = SimpleNamespace(
            exec_run=lambda args: SimpleNamespace(exit_code=0, output=b""),
            reload=lambda: None,
            status="running",
            restart=lambda **kwargs: self.fail("Hot reload restarted the core"),
        )
        self.patch = patch.object(
            self.runtime, "_get_container", return_value=self.container
        )
        self.patch.start()
        self.service = UserService(
            self.store, self.runtime, UrlBuilder("key", "ab", "node.test")
        )

    async def asyncTearDown(self):
        self.patch.stop()
        await self.server.stop(None)
        self.temp.cleanup()

    async def test_registration_adds_hy2_and_vless_without_restart(self):
        await self.service.create_user("existing")
        existing = {tag: dict(users) for tag, users in self.users.items()}
        await self.service.create_user("new-user")
        for tag, users in existing.items():
            self.assertEqual(self.users[tag]["existing"], users["existing"])
            self.assertIn("new-user", self.users[tag])
        account = self.users["hysteria2-in"]["new-user"].account
        self.assertEqual(account.type, "xray.proxy.hysteria.account.Account")
        self.assertTrue(MESSAGES[account.type].FromString(account.value).auth)

    async def test_partial_rpc_failure_restores_store_and_live_users(self):
        before = self.source.read_bytes()
        self.fail_at = 2
        with self.assertRaises(grpc.aio.AioRpcError):
            await self.service.create_user("failed-user")
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue(all(not users for users in self.users.values()))
        self.assertEqual(
            json.loads(self.rendered.read_text()), self.runtime._applied_config
        )

    async def test_live_routing_change_and_failure_recovery(self):
        await self.service.create_user("existing")
        previous = self.runtime._applied_config.copy()
        candidate = json.loads(json.dumps(previous))
        candidate["routing"]["rules"].insert(
            0, {"type": "field", "user": ["existing"], "outboundTag": "direct"}
        )
        self.assertEqual(self.runtime._additions(candidate), [])
        calls = []
        self.container.exec_run = lambda args: (
            calls.append(args) or SimpleNamespace(exit_code=0, output=b"")
        )
        await self.runtime._apply_hot(self.container, candidate, [])
        self.assertIn("adrules", calls[0])
        self.assertEqual(self.runtime._applied_config, candidate)

    async def test_failed_user_compensation_attempts_remaining_cleanup_and_recovers(
        self,
    ):
        await self.service.create_user("existing")
        before = self.source.read_bytes()
        self.fail_at = self.calls + 2
        self.failed_removals.add(("hysteria2-in", "failed-user"))
        restarts = []

        def restart(**kwargs):
            # A failed HY2 cleanup must not prevent the independent VLESS cleanup.
            self.assertNotIn("failed-user", self.users["vless-reality-in"])
            restored = json.loads(self.rendered.read_text())
            self.users = {
                inbound["tag"]: {
                    client["email"]: MESSAGES["xray.common.protocol.User"](
                        email=client["email"]
                    )
                    for client in inbound["settings"]["clients"]
                }
                for inbound in restored["inbounds"]
            }
            restarts.append(restored)

        self.container.restart = restart
        with self.assertRaises(grpc.aio.AioRpcError) as caught:
            await self.service.create_user("failed-user")
        self.assertEqual(caught.exception.code(), grpc.StatusCode.INTERNAL)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue(
            all(set(users) == {"existing"} for users in self.users.values())
        )
        self.assertEqual(len(restarts), 1)
        self.assertEqual(self.runtime._applied_config, restarts[0])

    async def test_failed_full_recovery_surfaces_config_rollback_error(self):
        self.fail_at = 2
        self.failed_removals.add(("hysteria2-in", "failed-user"))

        def restart(**kwargs):
            raise DockerException("recovery unavailable")

        self.container.restart = restart
        with self.assertRaises(ConfigRollbackError):
            await self.service.create_user("failed-user")
        self.assertIsNone(self.runtime._applied_config)

    async def test_failed_routing_compensation_still_removes_new_handler(self):
        previous = json.loads(json.dumps(self.runtime._applied_config))
        candidate = json.loads(json.dumps(previous))
        native = socks_outbound(
            "outbound:new",
            {"server": "100.64.0.2", "server_port": 43002},
        )
        candidate["outbounds"].append(native)
        candidate["routing"]["rules"].insert(
            0, {"type": "field", "user": ["existing"], "outboundTag": native["tag"]}
        )
        live_routes = candidate["routing"]

        def execute(args):
            if "ado" in args:
                self.outbounds.add(native["tag"])
            return SimpleNamespace(exit_code=1 if "adrules" in args else 0, output=b"")

        def restart(**kwargs):
            nonlocal live_routes
            live_routes = json.loads(self.rendered.read_text())["routing"]

        self.container.exec_run = execute
        self.container.restart = restart
        self.rendered.write_text(json.dumps(candidate))
        with self.assertRaises(SingBoxReloadError):
            await self.runtime._apply_hot(self.container, candidate, [])
        self.assertNotIn(native["tag"], self.outbounds)
        await self.runtime.reload()
        self.assertEqual(live_routes, previous["routing"])

    async def test_credential_revocation_keeps_restart_semantics(self):
        await self.service.create_user("existing")
        candidate = json.loads(json.dumps(self.runtime._applied_config))
        candidate["inbounds"][0]["settings"]["clients"] = []
        self.assertIsNone(self.runtime._additions(candidate))

    async def test_failed_routing_rpc_restores_actual_rules(self):
        await self.service.create_user("existing")
        previous = self.runtime._applied_config
        candidate = json.loads(json.dumps(previous))
        candidate["routing"]["rules"].insert(
            0, {"type": "field", "user": ["existing"], "outboundTag": "direct"}
        )
        self.rendered.write_text(json.dumps(candidate))
        routes = []

        def exec_run(args):
            routes.append(json.loads(self.rendered.read_text())["routing"])
            return SimpleNamespace(exit_code=1 if len(routes) == 1 else 0, output=b"")

        self.container.exec_run = exec_run
        with self.assertRaises(SingBoxReloadError):
            await self.runtime._apply_hot(self.container, candidate, [])
        self.assertEqual(routes[-1], previous["routing"])
        self.assertEqual(self.runtime._applied_config, previous)

    async def test_native_cutover_and_rollback_keep_old_handler(self):
        old = socks_outbound(
            "outbound:route", {"server": "127.0.0.1", "server_port": 43001}
        )
        previous = self.runtime._applied_config
        previous["outbounds"].append(old)
        previous["routing"]["rules"].insert(
            0, {"type": "field", "user": ["existing"], "outboundTag": old["tag"]}
        )
        self.runtime._resident_outbounds[old["tag"]] = old
        self.outbounds.add(old["tag"])
        candidate = json.loads(json.dumps(previous))
        native = socks_outbound(
            old["tag"],
            {
                "server": "100.64.0.2",
                "server_port": 43002,
                "username": "test",
                "password": "temporary-test-password",
            },
        )
        candidate["outbounds"][-1] = native
        candidate["routing"]["rules"][0]["outboundTag"] = native["tag"]
        additions = []

        def execute(args):
            if "ado" in args:
                delta = json.loads(
                    self.rendered.with_suffix(".outbounds.json").read_text()
                )
                self.outbounds.update(item["tag"] for item in delta["outbounds"])
                additions.extend(delta["outbounds"])
            return SimpleNamespace(exit_code=0, output=b"")

        self.container.exec_run = execute
        self.assertEqual(self.runtime._additions(candidate), [])
        self.rendered.write_text(json.dumps(candidate))
        await self.runtime._apply_hot(self.container, candidate, [])
        self.assertEqual(self.outbounds, {old["tag"], native["tag"]})
        self.rendered.write_text(json.dumps(previous))
        await self.runtime._apply_hot(self.container, previous, [])
        self.assertEqual(len(additions), 1, "Rollback must reuse the existing handler")
        self.assertEqual(self.runtime._applied_config, previous)
        self.assertEqual(self.outbounds, {old["tag"], native["tag"]})
        candidate["outbounds"][-1] = socks_outbound(
            old["tag"],
            {
                "server": "100.64.0.3",
                "server_port": 43003,
                "username": "test",
                "password": "another-test-password",
            },
        )
        with self.assertRaises(SingBoxReloadError):
            self.runtime._additions(candidate)

    async def test_partial_outbound_add_failure_removes_only_new_handler(self):
        previous = self.runtime._applied_config
        candidate = json.loads(json.dumps(previous))
        native = socks_outbound(
            "outbound:new",
            {
                "server": "100.64.0.2",
                "server_port": 43002,
                "username": "test",
                "password": "temporary-test-password",
            },
        )
        candidate["outbounds"].append(native)
        self.outbounds.add("unrelated-handler")

        def execute(args):
            if "ado" in args:
                self.outbounds.add(native["tag"])
                return SimpleNamespace(
                    exit_code=1, output=b"lost reply after application"
                )
            return SimpleNamespace(exit_code=0, output=b"")

        self.container.exec_run = execute
        self.rendered.write_text(json.dumps(candidate))
        with self.assertRaises(SingBoxReloadError):
            await self.runtime._apply_hot(self.container, candidate, [])
        self.assertEqual(self.outbounds, {"unrelated-handler"})
        self.assertEqual(json.loads(self.rendered.read_text()), previous)
        self.assertFalse(self.rendered.with_suffix(".outbounds.json").exists())

    async def test_unknown_retained_handler_is_not_removed_after_api_restart(self):
        candidate = json.loads(json.dumps(self.runtime._applied_config))
        native = socks_outbound(
            "outbound:retained",
            {
                "server": "100.64.0.2",
                "server_port": 43002,
                "username": "test",
                "password": "temporary-test-password",
            },
        )
        candidate["outbounds"].append(native)
        self.outbounds.add(native["tag"])
        self.container.exec_run = lambda args: self.fail(
            "Attempted to mutate an unowned retained handler"
        )
        with self.assertRaises(SingBoxReloadError):
            await self.runtime._apply_hot(self.container, candidate, [])
        self.assertEqual(self.outbounds, {native["tag"]})

    async def test_canonical_native_set_preserves_live_policy_and_hy2(self):
        await self.service.create_user("existing")
        previous = json.loads(json.dumps(self.runtime._applied_config))
        previous["policy"]["levels"]["0"]["statsUserOnline"] = True
        previous["log"].pop("access", None)
        self.runtime._applied_config = previous
        self.rendered.write_text(json.dumps(previous))

        def execute(args):
            if "ado" in args:
                delta = json.loads(
                    self.rendered.with_suffix(".outbounds.json").read_text()
                )
                server = delta["outbounds"][0]["settings"]["servers"][0]
                self.assertEqual(
                    server["users"],
                    [{"user": "route", "pass": "temporary-test-password"}],
                )
                self.outbounds.update(item["tag"] for item in delta["outbounds"])
            return SimpleNamespace(exit_code=0, output=b"")

        self.container.exec_run = execute
        service = OutboundService(self.store, self.runtime)
        await service.set(
            "native",
            SocksOutboundConfig(
                server="100.64.0.2",
                server_port=43001,
                username="route",
                password="temporary-test-password",
                auth_users={"existing"},
            ),
        )
        stored = await self.store.load()
        outbound = stored.outbounds[-1].model_dump()
        self.assertEqual(outbound["password"], "temporary-test-password")
        self.assertEqual(outbound["tag"], "outbound:native")
        actual = json.loads(self.rendered.read_text())
        self.assertEqual(actual["inbounds"], previous["inbounds"])
        self.assertEqual(actual["policy"], previous["policy"])
        self.assertEqual(actual["log"], previous["log"])
        self.assertIn(actual["routing"]["rules"][0]["outboundTag"], self.outbounds)


if __name__ == "__main__":
    unittest.main()
