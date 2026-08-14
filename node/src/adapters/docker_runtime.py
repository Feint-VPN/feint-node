"""Adapter: restart the sing-box Docker container."""

import asyncio

import docker
from docker.errors import DockerException, NotFound
from domain.errors import SingBoxReloadError
from domain.ports import IContainerRuntime
from utils.logging_config import get_logger

logger = get_logger(__name__)


class NoopRuntime(IContainerRuntime):
    """No-op runtime for dev/test — skips container restart."""

    async def reload(self) -> None:
        logger.info("DEV_MODE: skipping sing-box container reload")


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
