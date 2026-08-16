from __future__ import annotations

import pytest
from adapters.node_telemetry import NodeTelemetryService
from domain.models import (
    Inbound,
    LogConfig,
    Outbound,
    Route,
    SingBoxConfig,
    TLSConfig,
    TransportConfig,
)


class FakeStore:
    def __init__(self, config: SingBoxConfig) -> None:
        self._config = config

    async def load(self) -> SingBoxConfig:
        return self._config

    async def save(self, config: SingBoxConfig) -> None:
        self._config = config

    async def backup(self) -> str:
        return "backup.json"

    async def restore(self, backup_path: str) -> None:
        return None


@pytest.mark.asyncio
async def test_get_status_returns_formatted_runtime_metadata(monkeypatch):
    config = SingBoxConfig(
        log=LogConfig(),
        inbounds=[
            Inbound(
                type="vless",
                tag="vless-reality-in",
                listen="::",
                listen_port=22481,
                tls=TLSConfig(enabled=True, reality={"enabled": True}),
            ),
            Inbound(
                type="vmess",
                tag="vmess-ws-in",
                listen="::",
                listen_port=14170,
                transport=TransportConfig(type="ws", path="/vmess-path"),
            ),
            Inbound(
                type="trojan",
                tag="trojan-in",
                listen="::",
                listen_port=22439,
                tls=TLSConfig(enabled=True),
            ),
        ],
        outbounds=[Outbound(type="direct", tag="direct")],
        route=Route(),
    )
    service = NodeTelemetryService(FakeStore(config))

    monkeypatch.setattr(
        NodeTelemetryService,
        "_read_uptime_seconds",
        staticmethod(lambda: 3 * 86400 + 5 * 3600),
    )

    status = await service.get_status()

    assert status.uptime == "03d 05h"
    assert status.configuration_available is True
    assert status.user_count == 0
    assert [protocol.model_dump() for protocol in status.protocols] == [
        {"name": "VLESS Reality", "port": 22481, "enabled": True},
        {"name": "VMess WS", "port": 14170, "enabled": True},
        {"name": "Trojan TLS", "port": 22439, "enabled": True},
    ]


@pytest.mark.asyncio
async def test_get_status_reports_unavailable_configuration(monkeypatch):
    store = FakeStore(
        SingBoxConfig(
            log=LogConfig(),
            inbounds=[],
            outbounds=[Outbound(type="direct", tag="direct")],
            route=Route(),
        )
    )

    async def fail_load() -> SingBoxConfig:
        raise OSError("config unavailable")

    store.load = fail_load
    service = NodeTelemetryService(store)
    monkeypatch.setattr(
        NodeTelemetryService,
        "_read_uptime_seconds",
        staticmethod(lambda: 3600),
    )

    status = await service.get_status()

    assert status.uptime == "00d 01h"
    assert status.configuration_available is False
    assert status.user_count is None
    assert status.protocols == []
