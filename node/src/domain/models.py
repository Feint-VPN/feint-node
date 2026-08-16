"""Sing-box configuration models."""

from typing import Any

from pydantic import BaseModel


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
    type: str
    tag: str


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
    outbound: str | None = None


class RuleSet(BaseModel):
    model_config = {"extra": "allow"}

    type: str
    tag: str
    format: str | None = None
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
