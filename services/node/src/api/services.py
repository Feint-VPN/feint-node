"""Application-scoped composition. No Docker connection is opened during construction."""

import asyncio
from typing import Any

from adapters.docker_runtime import DockerRuntime, NoopRuntime, XrayDockerRuntime
from adapters.node_telemetry import NodeTelemetryService
from adapters.rathole_runtime import RatholeRuntime
from adapters.singbox_file_store import SingBoxFileStore
from adapters.traffic_tracker import TrafficTracker
from adapters.tuic_runtime import TuicXrayRuntime
from adapters.url_builder import UrlBuilder
from domain.outbound_service import OutboundService
from domain.ports import IContainerRuntime
from domain.user_service import UserService
from utils.settings import Settings


class Services:
    def __init__(self, config: Settings) -> None:
        self.store = SingBoxFileStore(config.CONFIG_PATH, config.BACKUP_DIR)
        self.mutation_lock = asyncio.Lock()
        self.runtime: IContainerRuntime
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
        self.telemetry = NodeTelemetryService(self.store)
        self.tracker = TrafficTracker(
            config.V2RAY_API_ADDRESS,
            config.TRAFFIC_STATE_PATH,
            additional_addresses=(config.TUIC_STATS_ADDRESS,)
            if config.TUIC_STATS_ADDRESS
            else (),
        )
        self.reverse = RatholeRuntime(
            config_path=config.RATHOLE_CONFIG_PATH,
            state_path=config.RATHOLE_STATE_PATH,
            container_name=config.RATHOLE_CONTAINER_NAME,
            private_key=config.RATHOLE_PRIVATE_KEY,
            public_key=config.RATHOLE_PUBLIC_KEY,
            proxy_port=config.REVERSE_PROXY_PORT,
        )

    async def close(self) -> None:
        if isinstance(self.runtime, DockerRuntime):
            await self.runtime.close()
        await self.reverse.close()
