"""Container-runtime dependency."""

import os
from threading import Lock
from typing import Any

from adapters.docker_runtime import DockerRuntime, NoopRuntime, XrayDockerRuntime
from adapters.tuic_runtime import TuicXrayRuntime
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
                    options: dict[str, Any] = {}
                    runtime_type = XrayDockerRuntime
                    if os.getenv("TUIC_CONTAINER_NAME"):
                        runtime_type = TuicXrayRuntime
                        options = {
                            "tuic_container_name": os.environ["TUIC_CONTAINER_NAME"],
                            "tuic_config_path": os.getenv(
                                "TUIC_CONFIG_PATH", "/opt/sing-box/tuic.json"
                            ),
                            "tuic_stats_listen": os.environ["TUIC_STATS_LISTEN"],
                        }
                    container_runtime = runtime_type(
                        config_path=os.getenv(
                            "CONFIG_PATH", "/opt/sing-box/config.json"
                        ),
                        xray_config_path=os.getenv(
                            "XRAY_CONFIG_PATH", "/opt/sing-box/xray.json"
                        ),
                        container_name=os.getenv("VPN_RUNTIME_CONTAINER_NAME", "xray"),
                        api_listen=os.getenv("XRAY_API_LISTEN", "0.0.0.0:10085"),
                        api_address=os.getenv("V2RAY_API_ADDRESS", "127.0.0.1:10085")
                        if os.getenv("XRAY_HOT_RELOAD", "false").lower() == "true"
                        else None,
                        min_client_version=os.getenv(
                            "XRAY_REALITY_MIN_CLIENT_VERSION", ""
                        ),
                        hysteria2_compat_port=int(os.environ["HYSTERIA2_COMPAT_PORT"])
                        if os.getenv("HYSTERIA2_COMPAT_PORT")
                        else None,
                        **options,
                    )
                else:
                    container_runtime = DockerRuntime(
                        container_name=os.getenv(
                            "VPN_RUNTIME_CONTAINER_NAME", "sing-box"
                        )
                    )
    return container_runtime
