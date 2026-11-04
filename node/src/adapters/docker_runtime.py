"""Adapter: restart the sing-box Docker container."""

import asyncio

import docker
from adapters.xray_config import render_xray_config
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
        self._client = client or docker.from_env()

    async def reload(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            container = await loop.run_in_executor(
                None, self._client.containers.get, self.container_name
            )
            await loop.run_in_executor(
                None, lambda: container.restart(timeout=self.timeout)
            )

            deadline = loop.time() + self.timeout
            while True:
                await loop.run_in_executor(None, container.reload)
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
            container = await asyncio.to_thread(
                self._client.containers.get, self.container_name
            )
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
    ) -> None:
        super().__init__(container_name, timeout, client)
        self.config_path = config_path
        self.xray_config_path = xray_config_path
        self.runtime_config_path = runtime_config_path or xray_config_path
        self.api_listen = api_listen

    async def reload(self) -> None:
        await asyncio.to_thread(
            render_xray_config,
            self.config_path,
            self.xray_config_path,
            self.api_listen,
        )
        try:
            container = await asyncio.to_thread(
                self._client.containers.get, self.container_name
            )
            result = await asyncio.to_thread(
                container.exec_run,
                ["xray", "run", "-test", "-config", self.runtime_config_path],
            )
        except (DockerException, NotFound) as error:
            raise SingBoxReloadError(str(error)) from error
        if result.exit_code:
            output = result.output.decode(errors="replace").strip()
            raise SingBoxReloadError(output or "Generated Xray config is invalid")
        await super().reload()
