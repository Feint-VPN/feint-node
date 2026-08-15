"""Schemas for node metadata exposed by system endpoints."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class NodeProtocolStatus(BaseModel):
    name: str
    port: int
    enabled: bool = True


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
    """Authenticated runtime details consumed by the maintainer SDK."""

    status: NodeStatus
    api_version: str = Field(pattern=r"^[1-9]\d*\.\d+$")
    uptime: str = ""
    configuration: NodeAvailability
    sing_box: NodeRuntimeStatus
    statistics: NodeAvailability
    user_count: int | None = Field(default=None, ge=0)
    protocols: list[NodeProtocolStatus] = Field(default_factory=list)
