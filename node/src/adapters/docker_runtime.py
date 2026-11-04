"""Restart the selected VPN core and validate generated Xray configuration."""

import asyncio
import copy
import json
from pathlib import Path

import docker
from adapters.xray_config import render_xray_config
from adapters.xray_control import XrayControl
from docker.errors import DockerException, NotFound
from domain.errors import SingBoxReloadError
from domain.ports import IContainerRuntime
from utils.logging_config import get_logger

logger = get_logger(__name__)


class NoopRuntime(IContainerRuntime):
    """No-op runtime for dev/test — skips container restart."""

    async def reload(self) -> None:
        logger.info("DEV_MODE: skipping VPN runtime reload")

    async def is_running(self) -> bool:
        return True


class DockerRuntime(IContainerRuntime):
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

    @staticmethod
    def _static_config(config: dict) -> dict:
        result = copy.deepcopy(config)
        result.pop("routing", None)
        for inbound in result.get("inbounds", []):
            inbound.get("settings", {}).pop("clients", None)
        return result

    def _additions(self, candidate: dict) -> list[tuple[str, str, dict]] | None:
        previous = self._applied_config
        if not self.control or not previous:
            return None
        services = previous.get("api", {}).get("services", [])
        if not {"HandlerService", "RoutingService"}.issubset(services):
            return None
        if self._static_config(previous) != self._static_config(candidate):
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
        routing_changed = candidate.get("routing") != self._applied_config.get(
            "routing"
        )
        try:
            for tag, protocol, user in additions:
                added.append((tag, user["email"]))
                await self.control.add_user(tag, protocol, user)
            if routing_changed:
                await self._routing(container)
        except Exception:
            # Restore the actual live state before commit_config restores the store.
            # Never hide a failed RPC by restarting everyone's connections.
            await asyncio.to_thread(
                Path(self.xray_config_path).write_text,
                json.dumps(self._applied_config),
                encoding="utf-8",
            )
            if routing_changed:
                await self._routing(container)
            for tag, email in reversed(added):
                if await self.control.has_user(tag, email):
                    await self.control.remove_user(tag, email)
            raise
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
