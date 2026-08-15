"""Container-runtime dependency."""

import os
from threading import Lock

from adapters.docker_runtime import DockerRuntime, NoopRuntime
from domain.ports import IContainerRuntime

container_runtime: IContainerRuntime | None = None
container_runtime_lock = Lock()


def get_container_runtime() -> IContainerRuntime:
    global container_runtime
    if container_runtime is None:
        with container_runtime_lock:
            if container_runtime is None:
                container_runtime = (
                    NoopRuntime()
                    if os.getenv("DEV_MODE", "false").lower() == "true"
                    else DockerRuntime()
                )
    return container_runtime
