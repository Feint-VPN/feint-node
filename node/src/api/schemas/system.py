"""Schemas for node metadata exposed by system endpoints."""

from enum import StrEnum
from typing import Literal

from domain.telemetry import NodeProtocolTelemetry
from pydantic import BaseModel, Field


class NodeStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"


class NodeRuntimeStatus(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"


class NodeAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class NodeHealthResponse(BaseModel):
    """Authenticated health contract consumed by the Feint node wrapper."""

    status: Literal["ok"]
    api_version: str = Field(
        pattern=r"^[1-9]\d*\.\d+$",
        description="Compatible major.minor version of the node API contract.",
    )


class NodeStatusResponse(BaseModel):
    """Authenticated node runtime details."""

    status: NodeStatus
    api_version: str = Field(pattern=r"^[1-9]\d*\.\d+$")
    uptime: str = ""
    configuration: NodeAvailability
    sing_box: NodeRuntimeStatus
    statistics: NodeAvailability
    user_count: int | None = Field(default=None, ge=0)
    protocols: list[NodeProtocolTelemetry] = Field(default_factory=list)
