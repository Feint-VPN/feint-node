"""Local service contracts: real files/gRPC, with runtime failure injection only."""

import asyncio
import base64
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "node" / "src"))

from adapters.docker_runtime import DockerRuntime, NoopRuntime, XrayDockerRuntime
from adapters.singbox_file_store import SingBoxFileStore
from adapters.subscription_settings import update_template
from adapters.traffic_tracker import (
    _QUERY_STATS_REQUEST,
    _QUERY_STATS_RESPONSE,
    TrafficTracker,
    fetch_v2ray_user_counters,
)
from adapters.tuic_runtime import TuicXrayRuntime, build_tuic_config
from adapters.url_builder import UrlBuilder
from adapters.xray_config import build_xray_config, render_xray_config
from api.services import Services
from domain.errors import ConfigRollbackError, SingBoxReloadError
from domain.models import InboundUser, SingBoxConfig
from domain.user_service import PROTOCOL_TAGS, UserService
from utils.settings import Settings


def configuration() -> SingBoxConfig:
    return SingBoxConfig.model_validate(
        {
            "log": {"level": "warn"},
            "inbounds": [
                {
                    "type": protocol,
                    "tag": tag,
                    "listen": "127.0.0.1",
                    "listen_port": 20000 + index,
                    "users": [],
                    "tls": {
                        "enabled": True,
                        "server_name": "node.test",
                        "certificate_path": "/test/cert",
                        "key_path": "/test/key",
                        "reality": {
                            "private_key": "test-key",
                            "short_id": ["abcd"],
                            "handshake": {"server": "node.test", "server_port": 443},
                        },
                    },
                    "transport": {"type": "ws", "path": "/custom"},
                    "method": "2022-blake3-aes-128-gcm",
                    "password": "server-psk",
                    "custom_field": {"preserve": True},
                }
                for index, (protocol, tag) in enumerate(PROTOCOL_TAGS.items())
            ],
            "outbounds": [],
            "route": {"rules": [], "final": "direct"},
        }
    )


class FailingRuntime(NoopRuntime):
    def __init__(self, *failures: type[BaseException]) -> None:
        self.failures = list(failures)

    async def reload(self) -> None:
        if self.failures:
            raise self.failures.pop(0)("injected runtime failure")


class ServiceContract(unittest.IsolatedAsyncioTestCase):
    async def test_tuic_cascade_is_published_and_keeps_auth_routing(self) -> None:
        await self.service.create_user("routed-user")
        config = await self.store.load()
        document = config.model_dump(exclude_none=True)
        document["outbounds"].append(
            {
                "type": "socks",
                "tag": "outbound:test-route",
                "server": "127.0.0.1",
                "server_port": 39183,
            }
        )
        document["route"]["rules"].append(
            {
                "auth_user": ["routed-user"],
                "outbound": "outbound:test-route",
            }
        )
        config = SingBoxConfig.model_validate(document)
        await self.store.save(config)
        response = await self.service.get_user_configs("routed-user", "node.test")
        self.assertEqual(
            {protocol for protocol, value in response["configs"].items() if value},
            {"vless", "hysteria2", "tuic"},
        )
        self.assertTrue(response["configs"]["tuic"]["config_url"].startswith("tuic://"))
        companion = build_tuic_config(config, "127.0.0.1:10086")
        self.assertEqual(companion.route.rules[-1].auth_user, ["routed-user"])
        self.assertEqual(companion.route.rules[-1].outbound, "outbound:test-route")
        self.assertEqual(
            companion.outbounds[-1].model_dump(), config.outbounds[-1].model_dump()
        )

    async def test_tuic_only_mutation_does_not_restart_xray(self) -> None:
        xray_path = str(self.root / "xray.json")
        tuic_path = str(self.root / "tuic.json")
        render_xray_config(str(self.root / "config.json"), xray_path)
        runtime = TuicXrayRuntime(
            config_path=str(self.root / "config.json"),
            xray_config_path=xray_path,
            tuic_container_name="test-tuic",
            tuic_config_path=tuic_path,
            tuic_stats_listen="127.0.0.1:10086",
        )
        config = await self.store.load()
        inbound = next(item for item in config.inbounds if item.type == "tuic")
        inbound.users = [InboundUser(name="probe", uuid="test-uuid", password="secret")]
        inbound.tls.model_extra["alpn"] = ["h3"]
        await self.store.save(config)
        container = SimpleNamespace(exec_run=lambda args: SimpleNamespace(exit_code=0))
        with (
            patch.object(runtime.tuic, "_get_container", return_value=container),
            patch.object(runtime.tuic, "reload") as companion_reload,
            patch.object(DockerRuntime, "is_running", return_value=True),
            patch.object(XrayDockerRuntime, "reload") as primary_reload,
        ):
            await runtime.reload()
            await runtime.reload()
            companion_reload.assert_awaited_once()
            primary_reload.assert_not_awaited()
        companion = json.loads(await asyncio.to_thread(Path(tuic_path).read_text))
        self.assertEqual(companion["inbounds"][0]["tls"]["alpn"], ["h3"])
        self.assertEqual(
            companion["experimental"]["v2ray_api"]["stats"]["users"], ["probe"]
        )

    async def test_tuic_url_and_companion_preserve_credentials(self) -> None:
        await self.service.create_user(
            "alice", requested_uuid="existing-uuid", requested_password="p:a/ss?word"
        )
        config = await self.store.load()
        companion = build_tuic_config(config, "127.0.0.1:10086")
        self.assertEqual([item.type for item in companion.inbounds], ["tuic"])
        user = companion.inbounds[0].users[0]
        self.assertEqual((user.uuid, user.password), ("existing-uuid", "p:a/ss?word"))
        uri = self.builder.tuic_url(user.uuid, user.password, "node.test", 8443)
        parsed = urlsplit(uri)
        self.assertEqual(unquote(parsed.password), user.password)
        self.assertEqual(parse_qs(parsed.query)["udp_relay_mode"], ["native"])
        self.assertEqual(companion.route, config.route)
        await self.service.delete_user("alice")
        self.assertFalse(any(item.users for item in (await self.store.load()).inbounds))

    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="feint-contract-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = SingBoxFileStore(
            str(self.root / "config.json"), str(self.root / "backups")
        )
        await self.store.save(configuration())
        self.builder = UrlBuilder("public-key", "abcd", "node.test")
        self.service = UserService(self.store, NoopRuntime(), self.builder)

    async def test_bulk_repairs_partial_users_without_rotating_credentials(
        self,
    ) -> None:
        config = await self.store.load()
        config.inbounds[0].users = [InboundUser(name="alice", uuid="existing-uuid")]
        config.inbounds[2].users = [
            InboundUser(name="alice", password="existing-password")
        ]
        await self.store.save(config)
        batch = [("alice", None, None), ("alice", None, None)]
        self.assertEqual(await self.service.create_users(batch), 1)
        saved = await self.store.load()
        for inbound in saved.inbounds:
            self.assertEqual(len(inbound.users), 1)
            user = inbound.users[0]
            self.assertEqual(
                user.uuid if inbound.type in {"vless", "vmess"} else user.password,
                "existing-uuid"
                if inbound.type in {"vless", "vmess"}
                else "existing-password",
            )
            self.assertEqual(inbound.model_extra["custom_field"], {"preserve": True})
        before = self.store.config_path.read_bytes()
        backups = list(self.store.backup_dir.iterdir())
        self.assertEqual(await self.service.create_users(batch), 0)
        self.assertEqual(self.store.config_path.read_bytes(), before)
        self.assertEqual(list(self.store.backup_dir.iterdir()), backups)

    async def test_concurrent_bulk_requests_keep_all_users(self) -> None:
        await asyncio.gather(
            *(
                self.service.create_users(
                    [(f"user-{batch}-{i}", None, None) for i in range(50)]
                )
                for batch in range(3)
            )
        )
        page = await self.service.list_users(skip=25, limit=10)
        self.assertEqual(page["total"], 150)
        self.assertEqual(len(page["users"]), 10)
        for inbound in (await self.store.load()).inbounds:
            self.assertEqual(len({u.name for u in inbound.users}), 150)
        self.assertEqual(len(list(self.store.backup_dir.iterdir())), 3)

    async def test_reload_failure_and_cancellation_restore_the_file(self) -> None:
        for failure in (SingBoxReloadError, asyncio.CancelledError):
            with self.subTest(failure=failure):
                before = self.store.config_path.read_bytes()
                service = UserService(self.store, FailingRuntime(failure), self.builder)
                with self.assertRaises(failure):
                    await service.create_user("alice")
                self.assertEqual(self.store.config_path.read_bytes(), before)
        service = UserService(
            self.store,
            FailingRuntime(SingBoxReloadError, SingBoxReloadError),
            self.builder,
        )
        with self.assertRaises(ConfigRollbackError):
            await service.create_user("alice")

    async def test_http_delete_resets_only_successfully_deleted_user_traffic(self):
        from api.depends import verify_api_secret
        from api.errors import register_error_handlers
        from api.routers import stats, user
        from fastapi import FastAPI

        await self.service.create_user("alice")
        tracker = TrafficTracker("unused:1", str(self.root / "traffic.json"))
        tracker._totals = {
            "alice": {"upload": 12, "download": 34},
            "other": {"upload": 56, "download": 78},
            "missing": {"upload": 90, "download": 12},
        }
        tracker._live_last = {("unused:1", "alice"): (12, 34)}
        app = FastAPI()
        app.state.services = SimpleNamespace(user=self.service, tracker=tracker)
        app.dependency_overrides[verify_api_secret] = lambda: None
        register_error_handlers(app)
        app.include_router(user.router)
        app.include_router(stats.router)

        async def request(method, path):
            messages = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                messages.append(message)

            await app(
                {
                    "type": "http",
                    "http_version": "1.1",
                    "method": method,
                    "scheme": "http",
                    "path": path,
                    "query_string": b"",
                    "headers": [],
                    "root_path": "",
                },
                receive,
                send,
            )
            return messages[0]["status"], json.loads(
                b"".join(item.get("body", b"") for item in messages[1:])
            )

        self.assertEqual((await request("DELETE", "/user/missing"))[0], 404)
        self.assertEqual((await tracker.get_user("missing"))["upload"], 90)
        self.service._runtime = FailingRuntime(SingBoxReloadError)
        self.assertEqual((await request("DELETE", "/user/alice"))[0], 500)
        self.assertEqual((await tracker.get_user("alice"))["upload"], 12)
        self.service._runtime = NoopRuntime()
        self.assertEqual((await request("DELETE", "/user/alice"))[0], 200)
        await self.service.create_user("alice")
        status, current = await request("GET", "/user/alice/stats")
        self.assertEqual(status, 200)
        self.assertEqual((current["upload_bytes"], current["download_bytes"]), (0, 0))
        self.assertNotIn(("unused:1", "alice"), tracker._live_last)
        self.assertEqual((await tracker.get_user("other"))["upload"], 56)
        await tracker._flush_state(force=True)
        persisted = json.loads((self.root / "traffic.json").read_text())
        self.assertNotIn("alice", persisted["users"])
        self.assertEqual(persisted["users"]["other"], {"upload": 56, "download": 78})

    async def test_all_protocol_urls_and_xray_translation(self) -> None:
        await self.service.create_user("alice", "user-uuid", "password/+=")
        service = UserService(
            self.store, NoopRuntime(), self.builder, hysteria2_public_port=443
        )
        configs = (await service.get_user_configs("alice", "node.test"))["configs"]
        self.assertTrue(all(configs.values()))
        vless = urlsplit(configs["vless"]["config_url"])
        self.assertEqual(vless.username, "user-uuid")
        self.assertEqual(parse_qs(vless.query)["pbk"], ["public-key"])
        vmess = json.loads(base64.urlsafe_b64decode(configs["vmess"]["config_url"][8:]))
        self.assertEqual(
            (vmess["id"], vmess["path"], vmess["add"]),
            ("user-uuid", "/custom", "node.test"),
        )
        for protocol in ("trojan", "hysteria2"):
            parsed = urlsplit(configs[protocol]["config_url"])
            self.assertEqual(unquote(parsed.username), "password/+=")
        self.assertEqual(configs["hysteria2"]["port"], 443)
        ss = urlsplit(configs["shadowsocks"]["config_url"]).username
        self.assertEqual(
            base64.urlsafe_b64decode(ss + "=" * (-len(ss) % 4)).decode(),
            "2022-blake3-aes-128-gcm:server-psk:password/+=",
        )
        config = await self.store.load()
        before = config.model_dump()
        with patch.dict(
            os.environ,
            {
                "XRAY_REALITY_MIN_CLIENT_VERSION": "ignored",
                "HYSTERIA2_COMPAT_PORT": "9999",
            },
        ):
            default = build_xray_config(config)
        self.assertEqual(default["log"]["access"], "none")
        self.assertEqual(default["log"]["loglevel"], config.log.level)
        self.assertEqual(
            default["policy"]["levels"]["0"],
            {"statsUserUplink": True, "statsUserDownlink": True},
        )
        self.assertNotIn(
            "minClientVer", default["inbounds"][0]["streamSettings"]["realitySettings"]
        )
        custom = build_xray_config(
            config, min_client_version="1.8.1", hysteria2_compat_port=443
        )
        self.assertEqual(
            custom["inbounds"][0]["settings"]["clients"][0]["id"], "user-uuid"
        )
        self.assertEqual(custom["inbounds"][-1]["port"], 443)
        self.assertEqual(
            custom["inbounds"][0]["streamSettings"]["realitySettings"]["minClientVer"],
            "1.8.1",
        )
        self.assertEqual(config.model_dump(), before)
        render_xray_config(
            str(self.store.config_path),
            str(self.root / "xray.json"),
            min_client_version="1.8.1",
            hysteria2_compat_port=443,
        )
        self.assertEqual(json.loads((self.root / "xray.json").read_text()), custom)

    async def test_hy2_only_xray_keeps_credentials_and_cascade(self) -> None:
        config = await self.store.load()
        config.inbounds = [item for item in config.inbounds if item.type == "hysteria2"]
        await self.store.save(config)
        await self.service.create_user(
            "routed-user", "existing-uuid", "existing-password"
        )
        config = await self.store.load()
        document = config.model_dump(exclude_none=True)
        document["outbounds"].append(
            {
                "type": "socks",
                "tag": "outbound:test-route",
                "server": "127.0.0.1",
                "server_port": 45198,
            }
        )
        document["route"]["rules"].append(
            {"auth_user": ["routed-user"], "outbound": "outbound:test-route"}
        )
        config = SingBoxConfig.model_validate(document)
        rendered = build_xray_config(
            config, "127.0.0.1:10085", min_client_version="ignored"
        )
        self.assertEqual(
            [item["protocol"] for item in rendered["inbounds"]], ["hysteria"]
        )
        self.assertEqual(
            rendered["inbounds"][0]["settings"]["clients"],
            [{"auth": "existing-password", "email": "routed-user", "level": 0}],
        )
        self.assertEqual(
            rendered["routing"]["rules"][0]["outboundTag"], "outbound:test-route"
        )
        self.assertEqual(rendered["routing"]["rules"][-1]["ip"], ["geoip:private"])
        response = await self.service.get_user_configs("routed-user", "node.test")
        uri = urlsplit(response["configs"]["hysteria2"]["config_url"])
        self.assertEqual(unquote(uri.username), "existing-password")
        config.inbounds = []
        with self.assertRaisesRegex(ValueError, "VLESS or Hysteria2"):
            build_xray_config(config)

    async def test_settings_and_application_ownership(self) -> None:
        for value in ("true", "1", "yes"):
            with patch.dict(
                os.environ,
                {
                    "DEV_MODE": value,
                    "HYSTERIA2_PUBLIC_PORT": "",
                    "PUBLISHED_PROTOCOLS": "HYsteria2, vless",
                },
                clear=True,
            ):
                settings = Settings()
            services = Services(settings)
            self.assertIsInstance(services.runtime, NoopRuntime)
            self.assertIs(
                services.user._mutation_lock, services.outbound._mutation_lock
            )
            self.assertEqual(settings.published_protocols, {"hysteria2", "vless"})
            self.assertIsNone(settings.HYSTERIA2_PUBLIC_PORT)
            await services.close()
        settings = Settings(
            DEV_MODE=False, VPN_RUNTIME="xray", HYSTERIA2_COMPAT_PORT=443
        )
        with patch(
            "docker.from_env",
            side_effect=AssertionError("Construction must not connect to Docker"),
        ):
            services = Services(settings)
            await services.close()
        with self.assertRaises(ValueError):
            Settings(HYSTERIA2_PUBLIC_PORT=65536)

    def test_hot_control_rejects_unsafe_bind_before_writing(self):
        config = configuration()
        source = self.root / "config.json"
        destination = self.root / "private" / "xray.json"
        for listen in (
            "",
            "0.0.0.0:10085",
            "[::]:10085",
            "8.8.8.8:10085",
            "192.0.2.1:10085",
            "169.254.1.2:10085",
            "localhost:10085",
            "100.63.255.255:10085",
            "100.128.0.1:10085",
            "172.32.0.1:10085",
            "192.169.0.1:10085",
            "127.0.0.1:0",
            "127.0.0.1:65536",
        ):
            with self.subTest(listen=listen):
                with self.assertRaises(ValueError):
                    build_xray_config(config, listen, control_api=True)
                with self.assertRaises(ValueError):
                    render_xray_config(
                        str(source), str(destination), listen, control_api=True
                    )
                self.assertFalse(destination.parent.exists())
        destination = self.root / "xray.json"
        destination.write_text("previous configuration")
        with self.assertRaises(ValueError):
            render_xray_config(str(source), str(destination), control_api=True)
        self.assertEqual(destination.read_text(), "previous configuration")

    def test_hot_control_accepts_only_explicit_supported_private_addresses(self):
        config = configuration()
        for listen in (
            "127.0.0.1:10085",
            "127.2.3.4:10085",
            "[::1]:10085",
            "10.20.30.40:10085",
            "172.16.0.1:10085",
            "172.31.255.254:10085",
            "192.168.0.1:10085",
            "100.64.0.1:10085",
            "100.127.255.254:10085",
        ):
            with self.subTest(listen=listen):
                rendered = build_xray_config(config, listen, control_api=True)
                self.assertEqual(rendered["api"]["listen"], listen)
                self.assertEqual(
                    rendered["api"]["services"],
                    ["StatsService", "HandlerService", "RoutingService"],
                )
        stats_only = build_xray_config(config)
        self.assertEqual(
            stats_only["api"],
            {"tag": "api", "listen": "0.0.0.0:10085", "services": ["StatsService"]},
        )

    async def test_template_updates_preserve_other_env_values(self) -> None:
        path = self.root / "settings.env"
        path.write_text(
            "API_SECRET=unchanged\nSUB_URI_TEMPLATE=old\n", encoding="utf-8"
        )
        settings = Settings(ENV_FILE_PATH=path)
        update_template(settings, "Test | {Protocol}")
        self.assertEqual(settings.SUB_URI_TEMPLATE, "Test | {Protocol}")
        self.assertEqual(
            path.read_text(),
            "API_SECRET=unchanged\nSUB_URI_TEMPLATE=Test | {Protocol}\n",
        )
        for template in ("{unknown}", "broken{", "two\nlines"):
            with self.assertRaises(ValueError):
                update_template(settings, template)
        self.assertEqual(settings.SUB_URI_TEMPLATE, "Test | {Protocol}")

    def test_tuic_overlay_runtime_imports_without_preinstalled_control_modules(self):
        repository = Path(__file__).resolve().parents[1]
        source = repository / "node/src"
        work = self.root / "overlay"
        installed = self.root / "installed"
        (installed / "adapters").mkdir(parents=True)
        shutil.copyfile(
            source / "adapters/singbox_file_store.py",
            installed / "adapters/singbox_file_store.py",
        )
        for package in ("domain", "utils"):
            shutil.copytree(
                source / package,
                installed / package,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        legacy_users = """PROTOCOL_TAGS = {
    "hysteria2": "hysteria2-in",
}
def _adapt_user(username, uuid, password, proto):
    raise ValueError(f"Unknown protocol: {proto}")
class UserService:
    def get_user_configs(self):
        by_proto, configs = {}, {}
        if "shadowsocks" in by_proto:
            pass
"""

        def installed_command(args, **kwargs):
            if args[:2] == ("docker", "inspect"):
                return json.dumps(
                    [
                        {
                            "Image": "sha256:test-base",
                            "Config": {"Env": [], "User": "1000:1000"},
                        }
                    ]
                )
            if args[:3] == ("docker", "image", "inspect"):
                return json.dumps([{"RepoDigests": []}])
            self.assertEqual(args[:4], ("docker", "exec", "vpn-node-api", "cat"))
            relative = args[4].removeprefix("/app/src/")
            return (
                legacy_users
                if relative == "domain/user_service.py"
                else (source / relative).read_text(encoding="utf-8")
            )

        real_run = subprocess.run

        def build_overlay(args, **kwargs):
            self.assertEqual(args[:2], ["docker", "build"])
            shutil.copytree(work / "src", installed, dirs_exist_ok=True)
            result = real_run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "import sys; sys.path.insert(0, sys.argv[1]); "
                    "from adapters.tuic_runtime import TuicXrayRuntime; "
                    "from adapters.traffic_tracker import TrafficTracker; "
                    "from adapters.xray_config import build_xray_config; "
                    "assert issubclass(TuicXrayRuntime, object)",
                    str(installed),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return subprocess.CompletedProcess(args, 0)

        with (
            patch.object(
                sys,
                "argv",
                [
                    "tuic_overlay.py",
                    "--source",
                    str(source),
                    "--work",
                    str(work),
                    "--image",
                    "local-test-overlay",
                ],
            ),
            patch("subprocess.check_output", side_effect=installed_command),
            patch("subprocess.run", side_effect=build_overlay),
        ):
            runpy.run_path(
                str(repository / "scripts/tuic_overlay.py"), run_name="__main__"
            )

    async def test_real_grpc_counter_serialization_and_persistence(self) -> None:
        import grpc

        peers = set()
        fail_next = False

        async def query(request, context):
            nonlocal fail_next
            peers.add(context.peer())
            if fail_next:
                fail_next = False
                await context.abort(grpc.StatusCode.UNAVAILABLE, "injected outage")
            self.assertEqual(list(request.patterns), ["user>>>"])
            self.assertFalse(request.reset)
            response = _QUERY_STATS_RESPONSE()
            for direction, value in (("uplink", 12), ("downlink", 34)):
                stat = response.stat.add()
                stat.name = f"user>>>alice>>>traffic>>>{direction}"
                stat.value = value
            return response

        server = grpc.aio.server()
        server.add_generic_rpc_handlers(
            (
                grpc.method_handlers_generic_handler(
                    "v2ray.core.app.stats.command.StatsService",
                    {
                        "QueryStats": grpc.unary_unary_rpc_method_handler(
                            query,
                            request_deserializer=_QUERY_STATS_REQUEST.FromString,
                            response_serializer=_QUERY_STATS_RESPONSE.SerializeToString,
                        )
                    },
                ),
            )
        )
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        try:
            address = f"127.0.0.1:{port}"
            self.assertEqual(
                await fetch_v2ray_user_counters(address, 2),
                {"alice": {"upload": 12, "download": 34}},
            )
            path = str(self.root / "traffic.json")
            tracker = TrafficTracker(address, path)
            peers.clear()
            await tracker.start()
            channels = dict(tracker._channels)
            try:
                await tracker._poll_once()
                await tracker._poll_once()
                await tracker.start()
                self.assertEqual(tracker._channels, channels)
                fail_next = True
                with self.assertRaises(grpc.aio.AioRpcError):
                    await tracker._fetch_user_counters()
                await tracker._poll_once()
                self.assertEqual(len(peers), 1, "Every poll opened another transport")
            finally:
                await tracker.stop()
            self.assertFalse(tracker._channels)
            self.assertTrue(
                all(
                    channel.get_state() == grpc.ChannelConnectivity.SHUTDOWN
                    for channel in channels.values()
                )
            )
            restored = TrafficTracker(address, path)
            await restored._load_state()
            result = await restored.get_user("alice")
            self.assertEqual((result["upload"], result["download"]), (12, 34))
            await restored._poll_once()
            result = await restored.get_user("alice")
            self.assertEqual((result["upload"], result["download"]), (12, 34))
        finally:
            await server.stop(0)

    async def test_multi_runtime_counters_handle_independent_restart(self) -> None:
        tracker = TrafficTracker("xray:1", str(self.root / "multi.json"), ("tuic:2",))
        primary = {"alice": {"upload": 100, "download": 200}}
        companion = {"alice": {"upload": 30, "download": 40}}
        with (
            patch.object(tracker, "_fetch_user_counters", return_value=primary),
            patch(
                "adapters.traffic_tracker.fetch_v2ray_user_counters",
                return_value=companion,
            ),
        ):
            await tracker._poll_once()
            await tracker._poll_once()
            companion["alice"] = {"upload": 5, "download": 8}
            await tracker._poll_once()
        result = await tracker.get_user("alice")
        self.assertEqual((result["upload"], result["download"]), (135, 248))
        await tracker.reset_user("alice")
        self.assertFalse(tracker._live_last)

    async def test_traffic_persistence_does_not_block_reads_or_lose_new_changes(self):
        tracker = TrafficTracker("unused:1", str(self.root / "traffic.json"))
        tracker._totals = {"alice": {"upload": 10, "download": 20}}
        tracker._dirty = True
        writing, release = threading.Event(), threading.Event()
        original = tracker._write_state

        def slow_write(snapshot):
            writing.set()
            if not release.wait(3):
                raise TimeoutError("Event loop could not release the writer")
            original(snapshot)

        with patch.object(tracker, "_write_state", side_effect=slow_write):
            flush = asyncio.create_task(tracker._flush_state())
            try:
                self.assertTrue(await asyncio.to_thread(writing.wait, 2))
                self.assertFalse(flush.done())
                result = await asyncio.wait_for(tracker.get_user("alice"), 1)
                self.assertEqual(result["download"], 20)
                await tracker.reset_user("alice")
            finally:
                release.set()
                await flush
        self.assertTrue(tracker._dirty, "A change during the write was lost")
        await tracker._flush_state(force=True)
        self.assertEqual(
            json.loads(tracker._state_path.read_text()), {"users": {}, "counters": {}}
        )
        self.assertFalse(list(self.root.glob(".traffic_*.tmp")))

    async def test_failed_traffic_write_retries_without_waiting_for_persist_interval(
        self,
    ):
        tracker = TrafficTracker("unused:1", str(self.root / "retry.json"))
        tracker._totals = {"alice": {"upload": 10, "download": 20}}
        tracker._dirty = True
        with patch(
            "adapters.traffic_tracker.os.replace", side_effect=OSError("disk failure")
        ):
            await tracker._flush_state()
        self.assertTrue(tracker._dirty)
        self.assertEqual(tracker._last_flush, 0)
        self.assertFalse(tracker._state_path.exists())
        self.assertFalse(list(self.root.glob(".traffic_*.tmp")))
        await tracker._flush_state()
        self.assertEqual(
            json.loads(tracker._state_path.read_text())["users"], tracker._totals
        )
        self.assertFalse(tracker._dirty)

    async def test_cancelled_flush_waits_for_writer_before_final_snapshot(self):
        tracker = TrafficTracker("unused:1", str(self.root / "cancel.json"))
        tracker._totals = {"alice": {"upload": 10, "download": 20}}
        writing, release = threading.Event(), threading.Event()
        original = tracker._write_state

        def slow_write(snapshot):
            writing.set()
            if not release.wait(3):
                raise TimeoutError("Writer not released")
            original(snapshot)

        with patch.object(tracker, "_write_state", side_effect=slow_write):
            flush = asyncio.create_task(tracker._flush_state(force=True))
            try:
                self.assertTrue(await asyncio.to_thread(writing.wait, 2))
                flush.cancel()
                await asyncio.sleep(0)
                self.assertFalse(
                    flush.done(), "Cancelled write could race shutdown flush"
                )
                await tracker.reset_user("alice")
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await flush
        await tracker._flush_state(force=True)
        self.assertEqual(
            json.loads(tracker._state_path.read_text()), {"users": {}, "counters": {}}
        )

    async def test_legacy_traffic_baseline_and_checkpoint_survive_api_restart(self):
        path = self.root / "legacy.json"
        path.write_text(
            json.dumps({"users": {"alice": {"upload": 10, "download": 20}}})
        )
        tracker = TrafficTracker("xray:1", str(path))
        await tracker._load_state()
        live = {"alice": {"upload": 100, "download": 200}}
        with patch.object(tracker, "_fetch_user_counters", return_value=live):
            await tracker._poll_once()
            await tracker._flush_state(force=True)
            result = await tracker.get_user("alice")
            self.assertEqual((result["upload"], result["download"]), (10, 20))
        restarted = TrafficTracker("xray:1", str(path))
        await restarted._load_state()
        live["alice"] = {"upload": 103, "download": 205}
        with patch.object(restarted, "_fetch_user_counters", return_value=live):
            await restarted._poll_once()
            await restarted._poll_once()
            result = await restarted.get_user("alice")
            self.assertEqual((result["upload"], result["download"]), (13, 25))
            live["alice"] = {"upload": 1, "download": 2}
            await restarted._poll_once()
            result = await restarted.get_user("alice")
            self.assertEqual((result["upload"], result["download"]), (14, 27))


if __name__ == "__main__":
    unittest.main()
