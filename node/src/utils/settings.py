"""
Application settings loaded from environment variables.

This module provides centralized configuration management for the VPN Node application.
All settings are loaded from environment variables with sensible defaults.

Requirements: 20.1
"""

import os
from pathlib import Path


class Settings:
    """Application settings loaded from environment variables."""

    def __init__(self):
        """Initialize settings from environment variables."""
        # Config Paths
        self.CONFIG_PATH: str = os.getenv("CONFIG_PATH", "/opt/sing-box/config.json")
        self.BACKUP_DIR: str = os.getenv("BACKUP_DIR", "/opt/sing-box/backups")

        # Docker Settings
        self.DOCKER_SOCKET: str = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
        self.SINGBOX_CONTAINER_NAME: str = os.getenv(
            "SINGBOX_CONTAINER_NAME", "sing-box"
        )
        self.CERTBOT_CONTAINER_NAME: str = os.getenv(
            "CERTBOT_CONTAINER_NAME", "certbot"
        )

        # Server Settings
        self.SERVER_DOMAIN: str = os.getenv("SERVER_DOMAIN", "example.com")
        self.SERVER_IP: str = os.getenv("SERVER_IP", "0.0.0.0")

        # VPN Protocol Ports
        self.VLESS_PORT: int = int(os.getenv("VLESS_PORT", "443"))
        self.VMESS_PORT: int = int(os.getenv("VMESS_PORT", "80"))
        self.TROJAN_PORT: int = int(os.getenv("TROJAN_PORT", "2053"))
        self.HYSTERIA2_PORT: int = int(os.getenv("HYSTERIA2_PORT", "2083"))
        self.SHADOWSOCKS_PORT: int = int(os.getenv("SHADOWSOCKS_PORT", "8388"))

        # Shadowsocks Settings
        self.SHADOWSOCKS_METHOD: str = os.getenv(
            "SHADOWSOCKS_METHOD", "2022-blake3-aes-256-gcm"
        )

        # VLESS REALITY client parameters. The private key stays in sing-box.
        self.REALITY_PUBLIC_KEY: str = os.getenv("REALITY_PUBLIC_KEY", "")
        self.REALITY_SHORT_ID: str = os.getenv("REALITY_SHORT_ID", "")
        self.REALITY_SERVER_NAME: str = os.getenv("REALITY_SERVER_NAME", "google.com")

        # Paths
        self.ENV_FILE_PATH: Path = Path(
            os.getenv("ENV_FILE_PATH", "/opt/vpn-node/.env.local")
        )

        # API Settings
        self.API_SECRET: str = os.getenv("API_SECRET", "change-me-in-production")
        # Hide all routes from callers without the node secret and make unknown
        # paths indistinguishable from protected routes in production.
        self.HIDE_ENDPOINTS: bool = os.getenv("HIDE_ENDPOINTS", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        self.DEV_MODE: bool = os.getenv("DEV_MODE", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        # Feature Flags
        self.SUBSCRIPTION_ENABLED: bool = os.getenv(
            "SUBSCRIPTION_ENABLED", "false"
        ).lower() in ("true", "1", "yes")

        # Subscription URI fragment template.
        # Placeholders: {protocol} (lowercase), {Protocol} (Title Case), {username}
        # Default produces:  🌌 Feint | Vless
        self.SUB_URI_TEMPLATE: str = os.getenv(
            "SUB_URI_TEMPLATE", "🌌 Feint | {Protocol}"
        )

        # Logging Settings
        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info")
        self.LOG_FORMAT: str = os.getenv("LOG_FORMAT", "json")

    def resolve_server_domain(self, requested_domain: str | None = None) -> str:
        """Resolve the public hostname used for generated share URLs."""
        domain = (requested_domain or "").strip() or self.SERVER_DOMAIN.strip()
        if not domain:
            raise ValueError("SERVER_DOMAIN is not configured")
        return domain

    def validate_sub_uri_template(self, template: str) -> str:
        """Validate and normalize the subscription label template."""
        normalized = template.strip()

        if not normalized:
            raise ValueError("SUB_URI_TEMPLATE cannot be empty")

        if "\n" in normalized or "\r" in normalized:
            raise ValueError("SUB_URI_TEMPLATE must be a single line")

        try:
            normalized.format(protocol="vless", Protocol="VLESS", username="demo")
        except KeyError as exc:
            placeholder = exc.args[0]
            raise ValueError(
                f"Unsupported placeholder '{{{placeholder}}}'. Allowed: {{protocol}}, {{Protocol}}, {{username}}"
            ) from exc
        except ValueError as exc:
            raise ValueError(f"Invalid SUB_URI_TEMPLATE: {exc}") from exc

        return normalized

    def _persist_env_value(self, key: str, value: str) -> None:
        self.ENV_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = []
        if self.ENV_FILE_PATH.exists():
            lines = self.ENV_FILE_PATH.read_text(encoding="utf-8").splitlines()

        replacement = f"{key}={value}"
        for index, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[index] = replacement
                break
        else:
            lines.append(replacement)

        self.ENV_FILE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def update_sub_uri_template(self, template: str) -> str:
        """Persist a new subscription label template and apply it immediately."""
        normalized = self.validate_sub_uri_template(template)
        self._persist_env_value("SUB_URI_TEMPLATE", normalized)
        self.SUB_URI_TEMPLATE = normalized
        os.environ["SUB_URI_TEMPLATE"] = normalized
        return normalized

    def validate_startup_requirements(self) -> tuple[bool, list[str]]:
        """
        Validate that all required resources are available on startup.

        Returns:
            tuple: (success: bool, errors: list[str])
                - success: True if all checks pass, False otherwise
                - errors: List of error messages for failed checks

        Requirements: 20.1
        """
        errors = []

        # Check if config.json exists
        config_path = Path(self.CONFIG_PATH)
        if not config_path.exists():
            errors.append(f"Config file not found at {self.CONFIG_PATH}")
        elif not config_path.is_file():
            errors.append(f"Config path {self.CONFIG_PATH} is not a file")
        elif not os.access(config_path, os.R_OK):
            errors.append(f"Config file {self.CONFIG_PATH} is not readable")

        # Check if Docker socket is accessible
        docker_socket = Path(self.DOCKER_SOCKET)
        if not docker_socket.exists():
            errors.append(f"Docker socket not found at {self.DOCKER_SOCKET}")
        elif not docker_socket.is_socket():
            errors.append(f"Docker socket path {self.DOCKER_SOCKET} is not a socket")
        elif not os.access(docker_socket, os.R_OK | os.W_OK):
            errors.append(
                f"Docker socket {self.DOCKER_SOCKET} is not accessible (check permissions)"
            )

        return len(errors) == 0, errors


# Global settings instance
settings = Settings()
