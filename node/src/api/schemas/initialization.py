"""API request/response schemas for initialization."""

from datetime import datetime

from domain.initialization import NodeSecrets
from pydantic import BaseModel, EmailStr, Field


class InitRequest(BaseModel):
    domain: str = Field(..., description="Server FQDN")
    email: EmailStr = Field(..., description="Email for Let's Encrypt")
    server_ip: str = Field(..., description="Server public IP")


class InitResult(BaseModel):
    success: bool
    message: str
    config: dict | None = None
    certificate_obtained: bool
    containers_started: bool
    timestamp: datetime
    errors: list[str] = Field(default_factory=list)


__all__ = ["InitRequest", "InitResult", "NodeSecrets"]
