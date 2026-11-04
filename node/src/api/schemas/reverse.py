"""Public reverse-transport responses."""

from typing import Any

from pydantic import BaseModel, Field


class ReverseKeyResponse(BaseModel):
    public_key: str


class ReverseStateResponse(BaseModel):
    mode: str
    details: dict[str, Any] = Field(default_factory=dict)
