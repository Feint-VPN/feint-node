"""Restart the selected VPN core and validate generated Xray configuration."""

import asyncio
import copy
import json
from collections import Counter
from pathlib import Path

import docker
from contracts import ContainerRuntime
from docker.errors import DockerException, NotFound
from exceptions import SingBoxReloadError
from log import get_logger
from runtime.xray.configuration import render_xray_config
from runtime.xray.control import XrayControl

logger = get_logger(__name__)


class NoopRuntime(ContainerRuntime):
    """No-op runtime for dev/test — skips container restart."""

    async def reload(self) -> None:
        logger.info("DEV_MODE: skipping VPN runtime reload")

    async def is_running(self) -> bool:
        return True


class DockerRuntime(ContainerRuntime):
    def __init__(
        self,
        container_name: str = "sing-box",
        timeout: int = 30,
        client=None,
    ) -> None:
        self.container_name = container_name
        self.timeout = timeout
        self._client = client

    def _get_container(self):
        if self._client is None:
            self._client = docker.from_env()
        return self._client.containers.get(self.container_name)

    async def close(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)

    async def reload(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            container = await asyncio.to_thread(self._get_container)
            await asyncio.to_thread(container.restart, timeout=self.timeout)

            deadline = loop.time() + self.timeout
            while True:
                await asyncio.to_thread(container.reload)
                if container.status == "running":
                    return
                if loop.time() > deadline:
                    raise SingBoxReloadError(
                        f"Container '{self.container_name}' did not start within {self.timeout}s"
                    )
                await asyncio.sleep(0.5)

        except NotFound as e:
            raise SingBoxReloadError(
                f"Container '{self.container_name}' not found"
            ) from e
        except SingBoxReloadError:
            raise
        except DockerException as e:
            raise SingBoxReloadError(str(e)) from e

    async def is_running(self) -> bool:
        try:
            container = await asyncio.to_thread(self._get_container)
            await asyncio.to_thread(container.reload)
            return container.status == "running"
        except (DockerException, NotFound):
            return False


class XrayDockerRuntime(DockerRuntime):
    def __init__(
        self,
        config_path: str,
        xray_config_path: str,
        runtime_config_path: str | None = None,
        container_name: str = "xray",
        api_listen: str = "0.0.0.0:10085",
        timeout: int = 30,
        client=None,
        min_client_version: str = "",
        hysteria2_compat_port: int | None = None,
        api_address: str | None = None,
    ) -> None:
        super().__init__(container_name, timeout, client)
        self.config_path = config_path
        self.xray_config_path = xray_config_path
        self.runtime_config_path = runtime_config_path or xray_config_path
        self.api_listen = api_listen
        self.min_client_version = min_client_version
        self.hysteria2_compat_port = hysteria2_compat_port
        self.control = XrayControl(api_address) if api_address else None
        path = Path(xray_config_path)
        self._applied_config = json.loads(path.read_text()) if path.exists() else None
        self._resident_outbounds = {
            item["tag"]: item
            for item in (self._applied_config or {}).get("outbounds", [])
        }

    @staticmethod
    def _static_config(config: dict) -> dict:
        result = copy.deepcopy(config)
        result.pop("routing", None)
        result.pop("outbounds", None)
        for inbound in result.get("inbounds", []):
            inbound.get("settings", {}).pop("clients", None)
        return result

    def _hot_outbounds(self, candidate: dict) -> list[dict] | None:
        assert self._applied_config is not None
        previous = {item["tag"]: item for item in self._applied_config["outbounds"]}
        after = {item["tag"]: item for item in candidate["outbounds"]}

        def route_tag(item: dict) -> str | None:
            tag = item["tag"]
            return (
                tag.split(".native-", 1)[0]
                if tag.startswith("outbound:") and item["protocol"] == "socks"
                else None
            )

        candidate_routes = {route_tag(item) for item in after.values()}
        for tag, item in previous.items():
            if tag in after:
                if item != after[tag]:
                    return None
            elif not route_tag(item) or route_tag(item) not in candidate_routes:
                return None
        resident_routes = Counter(
            route_tag(item) for item in self._resident_outbounds.values()
        )
        additions = []
        for tag, item in after.items():
            resident = self._resident_outbounds.get(tag)
            if resident is not None:
                if resident != item:
                    return None
                continue
            route = route_tag(item)
            if route is None:
                return None
            # Keep one old and one new handler per route, not an unbounded history.
            if resident_routes[route] >= 2:
                raise SingBoxReloadError(
                    "Drain retained route handlers before another endpoint change"
                )
            additions.append(item)
        return additions

    def _additions(self, candidate: dict) -> list[tuple[str, str, dict]] | None:
        previous = self._applied_config
        if not self.control or not previous:
            return None
        services = previous.get("api", {}).get("services", [])
        if not {"HandlerService", "RoutingService"}.issubset(services):
            return None
        if self._static_config(previous) != self._static_config(candidate):
            return None
        if self._hot_outbounds(candidate) is None:
            return None
        additions = []
        for old, new in zip(previous["inbounds"], candidate["inbounds"], strict=True):
            before = {
                user["email"]: user for user in old["settings"].get("clients", [])
            }
            after = {user["email"]: user for user in new["settings"].get("clients", [])}
            # Revocation/rotation must still terminate authenticated sessions.
            if any(after.get(email) != user for email, user in before.items()):
                return None
            added = [user for email, user in after.items() if email not in before]
            if added and new["protocol"] not in {"hysteria", "vless"}:
                return None
            additions.extend((new["tag"], new["protocol"], user) for user in added)
        return additions

    async def _routing(self, container) -> None:
        address = self.api_listen.replace("0.0.0.0:", "127.0.0.1:")
        result = await asyncio.to_thread(
            container.exec_run,
            ["xray", "api", "adrules", "--server=" + address, self.runtime_config_path],
        )
        if result.exit_code:
            raise SingBoxReloadError("Xray rejected the live routing update")

    async def _apply_hot(self, container, candidate: dict, additions: list) -> None:
        assert self.control is not None
        assert self._applied_config is not None
        added = []
        outbound_additions = self._hot_outbounds(candidate)
        assert outbound_additions is not None
        if outbound_additions:
            live_before = await self.control.outbound_tags()
            if any(item["tag"] in live_before for item in outbound_additions):
                # An API restart can lose the in-memory drain map. Never adopt or
                # remove a handler whose ownership this runtime cannot establish.
                raise SingBoxReloadError(
                    "Route handler already exists outside this runtime's drain state"
                )
        outbound_path = Path(self.xray_config_path).with_suffix(".outbounds.json")
        routing_changed = candidate.get("routing") != self._applied_config.get(
            "routing"
        )
        try:
            if outbound_additions:
                await asyncio.to_thread(outbound_path.touch, mode=0o600, exist_ok=True)
                await asyncio.to_thread(outbound_path.chmod, 0o600)
                await asyncio.to_thread(
                    outbound_path.write_text,
                    json.dumps({"outbounds": outbound_additions}),
                    encoding="utf-8",
                )
                address = self.api_listen.replace("0.0.0.0:", "127.0.0.1:")
                result = await asyncio.to_thread(
                    container.exec_run,
                    [
                        "xray",
                        "api",
                        "ado",
                        "--server=" + address,
                        str(
                            Path(self.runtime_config_path).with_suffix(
                                ".outbounds.json"
                            )
                        ),
                    ],
                )
                if result.exit_code:
                    raise SingBoxReloadError("Xray rejected the new route handler")
            for tag, protocol, user in additions:
                added.append((tag, user["email"]))
                await self.control.add_user(tag, protocol, user)
            if routing_changed:
                await self._routing(container)
        except Exception:
            # Compensate independently, preserving the original failure. Only an
            # unproven restoration needs commit_config's full-reload recovery.
            restored = True
            try:
                await asyncio.to_thread(
                    Path(self.xray_config_path).write_text,
                    json.dumps(self._applied_config),
                    encoding="utf-8",
                )
                if routing_changed:
                    await self._routing(container)
            except Exception:
                restored = False
                logger.exception("Failed to restore Xray routing configuration")
            if outbound_additions:
                try:
                    live = await self.control.outbound_tags()
                except Exception:
                    restored = False
                    live = set()
                    logger.exception("Failed to inspect Xray handlers during recovery")
                for outbound in reversed(outbound_additions):
                    if outbound["tag"] in live:
                        try:
                            await self.control.remove_outbound(outbound["tag"])
                        except Exception:
                            restored = False
                            logger.exception("Failed to remove a new Xray handler")
            for tag, email in reversed(added):
                try:
                    if await self.control.has_user(tag, email):
                        await self.control.remove_user(tag, email)
                except Exception:
                    restored = False
                    logger.exception("Failed to remove a new Xray user")
            if not restored:
                self._applied_config = None
                self._resident_outbounds.clear()
            raise
        finally:
            await asyncio.to_thread(outbound_path.unlink, missing_ok=True)
        self._resident_outbounds.update(
            {item["tag"]: item for item in outbound_additions}
        )
        self._applied_config = candidate

    async def reload(self) -> None:
        await asyncio.to_thread(
            render_xray_config,
            self.config_path,
            self.xray_config_path,
            self.api_listen,
            min_client_version=self.min_client_version,
            hysteria2_compat_port=self.hysteria2_compat_port,
            control_api=self.control is not None,
        )
        try:
            container = await asyncio.to_thread(self._get_container)
            result = await asyncio.to_thread(
                container.exec_run,
                ["xray", "run", "-test", "-config", self.runtime_config_path],
            )
        except (DockerException, NotFound) as error:
            raise SingBoxReloadError(str(error)) from error
        if result.exit_code:
            assert isinstance(
                result.output, bytes
            )  # exec_run uses neither stream nor demux.
            output = result.output.decode(errors="replace").strip()
            raise SingBoxReloadError(output or "Generated Xray config is invalid")
        candidate = await asyncio.to_thread(
            lambda: json.loads(Path(self.xray_config_path).read_text())
        )
        # Logging and online-IP changes are separate restartful migrations.
        # Preserve their live settings during user/route-only updates.
        if self._applied_config:
            old_log = self._applied_config.get("log", {})
            if "access" in old_log:
                candidate["log"]["access"] = old_log["access"]
            else:
                candidate["log"].pop("access", None)
            old_levels = self._applied_config.get("policy", {}).get("levels", {})
            for level, policy in candidate.get("policy", {}).get("levels", {}).items():
                if old_levels.get(level, {}).get("statsUserOnline") is True:
                    policy["statsUserOnline"] = True
            await asyncio.to_thread(
                Path(self.xray_config_path).write_text,
                json.dumps(candidate),
                encoding="utf-8",
            )
        if candidate == self._applied_config and await super().is_running():
            return
        additions = self._additions(candidate)
        if additions is not None and await super().is_running():
            task = asyncio.create_task(self._apply_hot(container, candidate, additions))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            logger.info("Applied Xray users/routing without restarting the core")
            return
        await super().reload()
        self._applied_config = candidate
        self._resident_outbounds = {
            item["tag"]: item for item in candidate["outbounds"]
        }
