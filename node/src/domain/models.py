"""Sing-box configuration models."""

from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr


class InboundUser(BaseModel):
    name: str | None = None
    uuid: str | None = None
    password: str | None = None
    flow: str | None = None


class TLSConfig(BaseModel):
    enabled: bool
    certificate_path: str | None = None
    key_path: str | None = None
    server_name: str | None = None
    reality: dict[str, Any] | None = None


class TransportConfig(BaseModel):
    type: str
    path: str | None = None
    max_early_data: int | None = None
    early_data_header_name: str | None = None


class Inbound(BaseModel):
    type: str
    tag: str
    listen: str
    listen_port: int
    users: list[InboundUser] = []
    tls: TLSConfig | None = None
    transport: TransportConfig | None = None
    method: str | None = None  # shadowsocks cipher method
    password: str | None = None  # shadowsocks server PSK (2022 multi-user mode)
    up_mbps: int | None = None  # hysteria2
    down_mbps: int | None = None
    multiplex: dict[str, Any] | None = None


class Outbound(BaseModel):
    model_config = {"extra": "allow"}

    type: str
    tag: str


class Hysteria2Obfs(BaseModel):
    type: Literal["salamander"] = "salamander"
    password: SecretStr


class OutboundTLS(BaseModel):
    enabled: bool = True
    server_name: str = Field(min_length=1, max_length=253)
    insecure: bool = False


class Hysteria2OutboundConfig(BaseModel):
    type: Literal["hysteria2"] = "hysteria2"
    server: str = Field(min_length=1, max_length=253)
    server_port: int = Field(ge=1, le=65535)
    password: SecretStr
    tls: OutboundTLS
    obfs: Hysteria2Obfs | None = None
    up_mbps: int | None = Field(default=None, gt=0)
    down_mbps: int | None = Field(default=None, gt=0)
    auth_users: set[str] = Field(default_factory=set)


class DNSServer(BaseModel):
    model_config = {"extra": "allow"}

    type: str
    tag: str
    server: str


class DNSConfig(BaseModel):
    model_config = {"extra": "allow"}

    servers: list[DNSServer] = []
    final: str | None = None
    strategy: str | None = None
    reverse_mapping: bool | None = None


class RouteRule(BaseModel):
    model_config = {"extra": "allow"}

    action: str | None = None
    port: list[int] | None = None
    strategy: str | None = None
    ip_is_private: bool | None = None
    ip_cidr: list[str] | None = None
    rule_set: str | list[str] | None = None
    auth_user: list[str] | None = None
    outbound: str | None = None


class RuleSet(BaseModel):
    model_config = {"extra": "allow"}

    type: str
    tag: str
    format: str | None = None
    path: str | None = None
    url: str | None = None
    update_interval: str | None = None


class Route(BaseModel):
    model_config = {"extra": "allow"}

    rules: list[RouteRule] = []
    rule_set: list[RuleSet] = []
    final: str = "direct"


class LogConfig(BaseModel):
    level: str = "info"
    timestamp: bool = True


class SingBoxConfig(BaseModel):
    model_config = {"extra": "allow"}

    log: LogConfig
    dns: DNSConfig | None = None
    inbounds: list[Inbound]
    outbounds: list[Outbound]
    route: Route
    experimental: dict[str, Any] | None = None
