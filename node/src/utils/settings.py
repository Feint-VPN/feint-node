"""Validated process configuration; environment variables are read only here."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Port = Annotated[int, Field(ge=1, le=65535)]
OptionalPort = Annotated[
    Port | None,
    BeforeValidator(
        lambda value: None if isinstance(value, str) and not value.strip() else value
    ),
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    CONFIG_PATH: str = "/opt/sing-box/config.json"
    BACKUP_DIR: str = "/opt/sing-box/backups"
    VPN_RUNTIME: Literal["sing-box", "xray"] = "sing-box"
    XRAY_CONFIG_PATH: str = "/opt/sing-box/xray.json"
    XRAY_API_LISTEN: str = "0.0.0.0:10085"
    XRAY_HOT_RELOAD: bool = False
    TUIC_CONTAINER_NAME: str = ""
    TUIC_CONFIG_PATH: str = "/opt/sing-box/tuic.json"
    TUIC_STATS_LISTEN: str = "127.0.0.1:10086"
    TUIC_STATS_ADDRESS: str = ""
    XRAY_REALITY_MIN_CLIENT_VERSION: str = ""
    HYSTERIA2_COMPAT_PORT: OptionalPort = None
    VPN_RUNTIME_CONTAINER_NAME: str | None = None
    DOCKER_SOCKET: str = "/var/run/docker.sock"
    SINGBOX_CONTAINER_NAME: str = "sing-box"
    RATHOLE_CONTAINER_NAME: str = "feint-rathole"
    RATHOLE_CONFIG_PATH: str = "/opt/relay/rathole.toml"
    RATHOLE_STATE_PATH: str = "/opt/relay/state.json"
    RATHOLE_PRIVATE_KEY: str = ""
    RATHOLE_PUBLIC_KEY: str = ""
    REVERSE_PROXY_PORT: Port = 39081
    CERTBOT_CONTAINER_NAME: str = "certbot"
    SERVER_DOMAIN: str = "example.com"
    SERVER_IP: str = "0.0.0.0"
    VLESS_PORT: Port = 443
    VMESS_PORT: Port = 80
    TROJAN_PORT: Port = 2053
    HYSTERIA2_PORT: Port = 2083
    HYSTERIA2_PUBLIC_PORT: OptionalPort = None
    SHADOWSOCKS_PORT: Port = 8388
    SHADOWSOCKS_METHOD: str = "2022-blake3-aes-256-gcm"
    REALITY_PUBLIC_KEY: str = ""
    REALITY_SHORT_ID: str = ""
    REALITY_SERVER_NAME: str = "google.com"
    ENV_FILE_PATH: Path = Path("/opt/vpn-node/.env.local")
    API_SECRET: str = "change-me-in-production"
    HIDE_ENDPOINTS: bool = True
    DEV_MODE: bool = False
    SUBSCRIPTION_ENABLED: bool = False
    SUB_URI_TEMPLATE: str = "🌌 Feint | {Protocol}"
    PUBLISHED_PROTOCOLS: str = ""
    V2RAY_API_ADDRESS: str = "127.0.0.1:10085"
    TRAFFIC_STATE_PATH: str = "/opt/sing-box/traffic.json"
    LOG_LEVEL: str = "info"
    LOG_FORMAT: str = "json"

    @property
    def published_protocols(self) -> frozenset[str] | None:
        value = self.PUBLISHED_PROTOCOLS.strip()
        return (
            frozenset(part.strip().lower() for part in value.split(",") if part.strip())
            if value
            else None
        )

    def resolve_server_domain(self, requested_domain: str | None = None) -> str:
        domain = (requested_domain or "").strip() or self.SERVER_DOMAIN.strip()
        if not domain:
            raise ValueError("SERVER_DOMAIN is not configured")
        return domain


settings = Settings()
