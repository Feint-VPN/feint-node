"""Subscription content/label rules, independent of HTTP."""

import base64
import binascii
import json
from urllib.parse import quote

from contracts.settings import SubscriptionSettings
from exceptions import SubscriptionDomainError, SubscriptionNotFoundError
from services.user import UserService

_PROTO_DISPLAY = {
    "vless": "VLESS",
    "vmess": "VMess",
    "trojan": "Trojan",
    "hysteria2": "Hysteria2",
    "tuic": "TUIC",
    "shadowsocks": "Shadowsocks",
}
_PROTO_ORDER = ("vless", "vmess", "trojan", "hysteria2", "tuic", "shadowsocks")


def _set_label(url: str, label: str) -> str:
    """VMess keeps its label inside JSON; other protocols use URI fragments."""
    prefix = "vmess://"
    if not url.startswith(prefix):
        return f"{url.split('#', 1)[0]}#{quote(label, safe='')}"
    raw = url[len(prefix) :]
    # base64url → JSON → patch "ps" → base64url
    padded = raw + "=" * (-len(raw) % 4)
    try:
        cfg = json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return url
    cfg["ps"] = label
    encoded = base64.urlsafe_b64encode(
        json.dumps(cfg, separators=(",", ":")).encode()
    ).decode()
    return f"{prefix}{encoded}"


class SubscriptionService:
    def __init__(
        self,
        users: UserService,
        store: SubscriptionSettings,
        *,
        enabled: bool,
        server_domain: str,
        template: str,
    ) -> None:
        self._users, self._store = users, store
        self.enabled, self.server_domain, self.template = (
            enabled,
            server_domain,
            template,
        )

    def update_template(self, template: str) -> None:
        normalized = template.strip()
        if not normalized:
            raise ValueError("SUB_URI_TEMPLATE cannot be empty")
        if "\n" in normalized or "\r" in normalized:
            raise ValueError("SUB_URI_TEMPLATE must be a single line")
        try:
            normalized.format(protocol="vless", Protocol="VLESS", username="demo")
        except KeyError as exc:
            raise ValueError(
                f"Unsupported placeholder '{{{exc.args[0]}}}'. Allowed: {{protocol}}, {{Protocol}}, {{username}}"
            ) from exc
        except ValueError as exc:
            raise ValueError(f"Invalid SUB_URI_TEMPLATE: {exc}") from exc

        self._store.save_template(normalized)
        self.template = normalized

    async def build(
        self, username: str, requested_domain: str | None = None
    ) -> tuple[str, str]:
        domain = (requested_domain or "").strip() or self.server_domain.strip()
        if not domain:
            raise SubscriptionDomainError("SERVER_DOMAIN is not configured")
        result = await self._users.get_user_configs(username, domain)
        configs, uris = result.get("configs", {}), []
        for protocol in _PROTO_ORDER:
            config = configs.get(protocol)
            if config and config.get("config_url"):
                label = self.template.format(
                    protocol=protocol,
                    Protocol=_PROTO_DISPLAY[protocol],
                    username=username,
                )
                uris.append(_set_label(config["config_url"], label))
        if not uris:
            raise SubscriptionNotFoundError(
                f"No protocol configs found for user '{username}'"
            )
        return base64.b64encode("\n".join(uris).encode()).decode(), domain
