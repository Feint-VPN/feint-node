"""Container-runtime dependency."""

import os
from threading import Lock

from adapters.docker_runtime import DockerRuntime, NoopRuntime, XrayDockerRuntime
from domain.ports import IContainerRuntime

container_runtime: IContainerRuntime | None = None
container_runtime_lock = Lock()


def get_container_runtime() -> IContainerRuntime:
    global container_runtime
    if container_runtime is None:
        with container_runtime_lock:
            if container_runtime is None:
                if os.getenv("DEV_MODE", "false").lower() == "true":
                    container_runtime = NoopRuntime()
                elif os.getenv("VPN_RUNTIME", "sing-box") == "xray":
                    container_runtime = XrayDockerRuntime(
                        config_path=os.getenv(
                            "CONFIG_PATH", "/opt/sing-box/config.json"
                        ),
                        xray_config_path=os.getenv(
                            "XRAY_CONFIG_PATH", "/opt/sing-box/xray.json"
                        ),
                        container_name=os.getenv("VPN_RUNTIME_CONTAINER_NAME", "xray"),
                    )
                else:
                    container_runtime = DockerRuntime(
                        container_name=os.getenv(
                            "VPN_RUNTIME_CONTAINER_NAME", "sing-box"
                        )
                    )
    return container_runtime
