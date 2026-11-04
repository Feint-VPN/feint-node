"""Typed service results, independent of the HTTP response models."""

from datetime import datetime
from typing import TypedDict


class UserData(TypedDict):
    username: str
    uuid: str
    password: str
    protocols: list[str]
    created_at: datetime


class UserPage(TypedDict):
    users: list[UserData]
    total: int
    limit: int
    skip: int


class ProtocolConfig(TypedDict):
    protocol: str
    config_url: str
    port: int


class UserConfigs(TypedDict):
    username: str
    configs: dict[str, ProtocolConfig | None]
