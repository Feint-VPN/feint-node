"""Application-scoped composition. No Docker connection is opened during construction."""

import asyncio
import os
from pathlib import Path
from typing import Any

from config import Settings
from contracts import ContainerRuntime
from log import get_logger
from runtime.docker import DockerRuntime, NoopRuntime, XrayDockerRuntime
from runtime.rathole import RatholeRuntime
from runtime.system import SystemTelemetry
from runtime.tuic import TuicXrayRuntime
from runtime.v2ray import V2RayCounterSource
from services.links import UrlBuilder
from services.outbound import OutboundService
from services.reverse import ReverseService
from services.subscription import SubscriptionService
from services.telemetry import NodeTelemetryService
from services.traffic import TrafficTracker
from services.user import UserService
from store.configuration import SingBoxFileStore
from store.reverse import ReverseFileStore
from store.settings import SubscriptionSettingsStore
from store.traffic import TrafficFileStore

logger = get_logger(__name__)


def startup_errors(settings: Settings) -> list[str]:
    errors = []
    config = Path(settings.CONFIG_PATH)
    if not config.exists():
        errors.append(f"Config file not found at {config}")
    elif not config.is_file():
        errors.append(f"Config path {config} is not a file")
    elif not os.access(config, os.R_OK):
        errors.append(f"Config file {config} is not readable")
    socket = Path(settings.DOCKER_SOCKET)
    if not socket.exists():
        errors.append(f"Docker socket not found at {socket}")
    elif not socket.is_socket():
        errors.append(f"Docker socket path {socket} is not a socket")
    elif not os.access(socket, os.R_OK | os.W_OK):
        errors.append(f"Docker socket {socket} is not accessible (check permissions)")
    return errors


class Application:
    def __init__(self, config: Settings) -> None:
        self.config = config
        self.store = SingBoxFileStore(config.CONFIG_PATH, config.BACKUP_DIR)
        self.mutation_lock = asyncio.Lock()
        self.runtime: ContainerRuntime
        name = config.VPN_RUNTIME_CONTAINER_NAME or config.VPN_RUNTIME
        if config.DEV_MODE:
            self.runtime = NoopRuntime()
        elif config.VPN_RUNTIME == "xray":
            runtime_type = (
                TuicXrayRuntime if config.TUIC_CONTAINER_NAME else XrayDockerRuntime
            )
            tuic_options: dict[str, Any] = (
                {
                    "tuic_container_name": config.TUIC_CONTAINER_NAME,
                    "tuic_config_path": config.TUIC_CONFIG_PATH,
                    "tuic_stats_listen": config.TUIC_STATS_LISTEN,
                }
                if config.TUIC_CONTAINER_NAME
                else {}
            )
            self.runtime = runtime_type(
                config_path=config.CONFIG_PATH,
                xray_config_path=config.XRAY_CONFIG_PATH,
                container_name=name,
                api_listen=config.XRAY_API_LISTEN,
                api_address=config.V2RAY_API_ADDRESS
                if config.XRAY_HOT_RELOAD
                else None,
                min_client_version=config.XRAY_REALITY_MIN_CLIENT_VERSION,
                hysteria2_compat_port=config.HYSTERIA2_COMPAT_PORT,
                **tuic_options,
            )
        else:
            self.runtime = DockerRuntime(container_name=name)
        self.user = UserService(
            self.store,
            self.runtime,
            UrlBuilder(
                config.REALITY_PUBLIC_KEY,
                config.REALITY_SHORT_ID,
                config.REALITY_SERVER_NAME,
            ),
            mutation_lock=self.mutation_lock,
            published_protocols=config.published_protocols,
            hysteria2_public_port=config.HYSTERIA2_PUBLIC_PORT,
        )
        self.outbound = OutboundService(self.store, self.runtime, self.mutation_lock)
        self.telemetry = NodeTelemetryService(self.store, SystemTelemetry())
        self.subscription = SubscriptionService(
            self.user,
            SubscriptionSettingsStore(config.ENV_FILE_PATH),
            enabled=config.SUBSCRIPTION_ENABLED,
            server_domain=config.SERVER_DOMAIN,
            template=config.SUB_URI_TEMPLATE,
        )
        self.tracker = TrafficTracker(
            V2RayCounterSource(
                config.V2RAY_API_ADDRESS,
                (config.TUIC_STATS_ADDRESS,) if config.TUIC_STATS_ADDRESS else (),
            ),
            TrafficFileStore(config.TRAFFIC_STATE_PATH),
        )
        self._reverse_runtime = RatholeRuntime(
            container_name=config.RATHOLE_CONTAINER_NAME,
            private_key=config.RATHOLE_PRIVATE_KEY,
            public_key=config.RATHOLE_PUBLIC_KEY,
            proxy_port=config.REVERSE_PROXY_PORT,
        )
        self.reverse = ReverseService(
            self._reverse_runtime,
            ReverseFileStore(config.RATHOLE_CONFIG_PATH, config.RATHOLE_STATE_PATH),
        )

    async def start(self) -> None:
        errors = startup_errors(self.config)
        if errors:
            for error in errors:
                logger.warning("Startup check failed: %s", error)
        else:
            logger.info("Startup validation passed")
        try:
            await self.tracker.start()
        except Exception:
            logger.warning("Failed to start traffic tracker", exc_info=True)

    async def close(self) -> None:
        try:
            await self.tracker.stop()
        except Exception:
            logger.warning("Failed to stop traffic tracker cleanly", exc_info=True)
        finally:
            if isinstance(self.runtime, DockerRuntime):
                await self.runtime.close()
            await self._reverse_runtime.close()
