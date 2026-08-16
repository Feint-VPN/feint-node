"""Node telemetry helpers for root API metadata."""

from __future__ import annotations

import time

from domain.models import Inbound, SingBoxConfig
from domain.ports import IConfigStore
from domain.telemetry import NodeProtocolTelemetry, NodeTelemetryStatus

_PROTOCOL_LABELS = {
    "vless": "VLESS",
    "vmess": "VMess",
    "trojan": "Trojan",
    "hysteria2": "Hysteria2",
    "shadowsocks": "Shadowsocks",
}
_TRANSPORT_LABELS = {
    "grpc": "gRPC",
    "http": "HTTP",
    "httpupgrade": "HTTP Upgrade",
    "quic": "QUIC",
    "ws": "WS",
}
_SUPPORTED_INBOUND_TYPES = set(_PROTOCOL_LABELS)


class NodeTelemetryService:
    def __init__(self, store: IConfigStore) -> None:
        self._store = store

    async def get_status(self) -> NodeTelemetryStatus:
        """Return runtime metadata."""

        try:
            config = await self._store.load()
        except Exception:
            return NodeTelemetryStatus(
                uptime=self._format_uptime(self._read_uptime_seconds()),
                configuration_available=False,
            )

        return NodeTelemetryStatus(
            uptime=self._format_uptime(self._read_uptime_seconds()),
            configuration_available=True,
            user_count=len(
                {
                    user.name
                    for inbound in config.inbounds
                    for user in inbound.users
                    if user.name
                }
            ),
            protocols=self._protocols(config),
        )

    @classmethod
    def _protocols(cls, config: SingBoxConfig) -> list[NodeProtocolTelemetry]:
        protocols: list[NodeProtocolTelemetry] = []
        seen: set[tuple[str, int]] = set()
        for inbound in config.inbounds:
            protocol = cls._protocol_from_inbound(inbound)
            if protocol is None:
                continue

            key = (protocol.name, protocol.port)
            if key in seen:
                continue

            seen.add(key)
            protocols.append(protocol)

        return protocols

    @staticmethod
    def _read_uptime_seconds() -> int | None:
        try:
            with open("/proc/uptime", encoding="utf-8") as uptime_file:
                return int(float(uptime_file.read().split()[0]))
        except (FileNotFoundError, ValueError, OSError):
            pass

        boot_time_epoch = NodeTelemetryService._read_boot_time_epoch()
        if boot_time_epoch is None:
            return None

        return max(0, int(time.time() - boot_time_epoch))

    @staticmethod
    def _read_boot_time_epoch() -> int | None:
        try:
            with open("/proc/stat", encoding="utf-8") as stat_file:
                for line in stat_file:
                    if line.startswith("btime "):
                        return int(line.split()[1])
        except (FileNotFoundError, OSError, ValueError, IndexError):
            return None

        return None

    @staticmethod
    def _protocol_from_inbound(inbound: Inbound) -> NodeProtocolTelemetry | None:
        protocol_type = inbound.type.strip().lower()
        if protocol_type not in _SUPPORTED_INBOUND_TYPES:
            return None

        name = _PROTOCOL_LABELS[protocol_type]
        if protocol_type == "vless" and inbound.tls and inbound.tls.reality:
            name = f"{name} Reality"
        elif protocol_type == "vmess" and inbound.transport and inbound.transport.type:
            transport_type = inbound.transport.type.strip().lower()
            transport_label = _TRANSPORT_LABELS.get(
                transport_type, transport_type.upper()
            )
            name = f"{name} {transport_label}"
        elif protocol_type == "trojan" and inbound.tls and inbound.tls.enabled:
            name = f"{name} TLS"

        return NodeProtocolTelemetry(name=name, port=inbound.listen_port)

    @staticmethod
    def _format_uptime(total_seconds: int | None) -> str:
        if total_seconds is None:
            return ""

        days, remainder = divmod(total_seconds, 86400)
        hours = remainder // 3600
        return f"{days:02d}d {hours:02d}h"
