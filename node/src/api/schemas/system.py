"""Schemas for node metadata exposed by system endpoints."""

from typing import Literal

from pydantic import BaseModel, Field


class NodeProtocolStatus(BaseModel):
    name: str
    port: int
    enabled: bool = True


class NodeHealthResponse(BaseModel):
    """Authenticated health contract consumed by the Feint node wrapper."""

    status: Literal["ok"]
    api_version: str = Field(
        pattern=r"^[1-9]\d*\.\d+$",
        description="Compatible major.minor version of the node API contract.",
    )


class NodeStatusResponse(BaseModel):
    """Authenticated runtime details consumed by the maintainer SDK."""

    status: Literal["ok"]
    api_version: str = Field(pattern=r"^[1-9]\d*\.\d+$")
    uptime: str = ""
    user_count: int = Field(ge=0)
    protocols: list[NodeProtocolStatus] = Field(default_factory=list)
