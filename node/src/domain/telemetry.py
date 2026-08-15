"""Typed node telemetry shared by adapters and API schemas."""

from pydantic import BaseModel, Field


class NodeProtocolTelemetry(BaseModel):
    name: str
    port: int = Field(ge=1, le=65535)
    enabled: bool = True


class NodeTelemetryStatus(BaseModel):
    uptime: str = ""
    configuration_available: bool
    user_count: int | None = Field(default=None, ge=0)
    protocols: list[NodeProtocolTelemetry] = Field(default_factory=list)


class NodeTelemetrySnapshot(NodeTelemetryStatus):
    cpu_load: int = Field(ge=-1, le=100)
