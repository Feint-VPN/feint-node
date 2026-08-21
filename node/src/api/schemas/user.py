"""API request/response schemas for user management."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class UserCreateRequest(BaseModel):
    username: str = Field(
        ...,
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    uuid: str | None = None
    password: str | None = None


class UserBulkCreateRequest(BaseModel):
    users: list[UserCreateRequest] = Field(min_length=1, max_length=500)


class UserBulkCreateResponse(BaseModel):
    created: int


class UserResponse(BaseModel):
    username: str
    uuid: str
    password: str
    protocols: list[str]
    created_at: datetime


class UserListResponse(BaseModel):
    users: list[UserResponse]
    total: int
    limit: int
    skip: int


class ProtocolConfig(BaseModel):
    protocol: str
    config_url: str
    port: int


class UserConfigsResponse(BaseModel):
    username: str
    configs: dict[str, ProtocolConfig | None]


class AmneziaConfigResponse(BaseModel):
    username: str
    protocol: Literal["vless"]
    config_url: str


class UserStats(BaseModel):
    username: str
    upload_bytes: int
    download_bytes: int
    total_bytes: int
    last_seen: datetime | None = None
    available: bool = True
