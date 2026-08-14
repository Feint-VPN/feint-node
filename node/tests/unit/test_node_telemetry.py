from __future__ import annotations

import pytest
from adapters import node_telemetry
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
async def test_get_snapshot_returns_formatted_runtime_metadata(monkeypatch):
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

    cpu_samples = iter([(1000, 400), (1100, 430)])
    monkeypatch.setattr(
        NodeTelemetryService,
        "_read_cpu_times",
        staticmethod(lambda: next(cpu_samples)),
    )
    monkeypatch.setattr(
        node_telemetry.asyncio, "sleep", lambda _seconds: _immediate_sleep()
    )
    monkeypatch.setattr(
        NodeTelemetryService,
        "_read_uptime_seconds",
        staticmethod(lambda: 3 * 86400 + 5 * 3600),
    )

    snapshot = await service.get_snapshot()

    assert snapshot["cpu_load"] == 70
    assert snapshot["uptime"] == "03d 05h"
    assert snapshot["protocols"] == [
        {"name": "VLESS Reality", "port": 22481, "enabled": True},
        {"name": "VMess WS", "port": 14170, "enabled": True},
        {"name": "Trojan TLS", "port": 22439, "enabled": True},
    ]


@pytest.mark.asyncio
async def test_get_snapshot_falls_back_when_metrics_unavailable(monkeypatch):
    config = SingBoxConfig(
        log=LogConfig(),
        inbounds=[],
        outbounds=[Outbound(type="direct", tag="direct")],
        route=Route(),
    )
    service = NodeTelemetryService(FakeStore(config))

    monkeypatch.setattr(
        NodeTelemetryService, "_read_cpu_times", staticmethod(lambda: None)
    )
    monkeypatch.setattr(
        NodeTelemetryService, "_read_loadavg", staticmethod(lambda: None)
    )
    monkeypatch.setattr(
        NodeTelemetryService, "_read_uptime_seconds", staticmethod(lambda: None)
    )
    monkeypatch.setattr(
        NodeTelemetryService, "_read_boot_time_epoch", staticmethod(lambda: None)
    )

    snapshot = await service.get_snapshot()

    assert snapshot["cpu_load"] == -1
    assert snapshot["uptime"] == ""
    assert snapshot["protocols"] == []


@pytest.mark.asyncio
async def test_get_snapshot_uses_boot_time_when_proc_uptime_is_unavailable(monkeypatch):
    config = SingBoxConfig(
        log=LogConfig(),
        inbounds=[],
        outbounds=[Outbound(type="direct", tag="direct")],
        route=Route(),
    )
    service = NodeTelemetryService(FakeStore(config))

    monkeypatch.setattr(
        NodeTelemetryService, "_read_cpu_times", staticmethod(lambda: None)
    )
    monkeypatch.setattr(
        NodeTelemetryService, "_read_loadavg", staticmethod(lambda: 0.0)
    )
    monkeypatch.setattr(
        NodeTelemetryService, "_read_boot_time_epoch", staticmethod(lambda: 1_000)
    )
    monkeypatch.setattr(
        node_telemetry.time, "time", lambda: 1_000 + 2 * 86400 + 7 * 3600
    )
    monkeypatch.setattr(
        "builtins.open",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )

    snapshot = await service.get_snapshot()

    assert snapshot["cpu_load"] == 0
    assert snapshot["uptime"] == "02d 07h"


async def _immediate_sleep() -> None:
    return None
