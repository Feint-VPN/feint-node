"""Node telemetry helpers for root API metadata."""

from __future__ import annotations

from contracts import ConfigStore
from contracts.telemetry import HostTelemetry
from models.configuration import Inbound, SingBoxConfig
from models.telemetry import NodeProtocolTelemetry, NodeTelemetryStatus

_PROTOCOL_LABELS = {
    "vless": "VLESS",
    "vmess": "VMess",
    "trojan": "Trojan",
    "hysteria2": "Hysteria2",
    "tuic": "TUIC",
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
    def __init__(self, store: ConfigStore, host: HostTelemetry) -> None:
        self._store = store
        self._host = host

    async def get_status(self) -> NodeTelemetryStatus:
        """Return runtime metadata."""

        try:
            config = await self._store.load()
        except Exception:
            return NodeTelemetryStatus(
                uptime=self._format_uptime(self._host.uptime_seconds()),
                configuration_available=False,
            )

        return NodeTelemetryStatus(
            uptime=self._format_uptime(self._host.uptime_seconds()),
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
