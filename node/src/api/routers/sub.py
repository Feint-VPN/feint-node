"""Subscription link endpoint — Hiddify-compatible (base64-encoded URIs).

Public endpoint (no auth required) — enable with SUBSCRIPTION_ENABLED=true.

URI fragment format is configurable via SUB_URI_TEMPLATE in .env.local
or via the authenticated /sub/settings API.
Default: 🌌 Feint | {Protocol}
Placeholders: {protocol} (lowercase), {Protocol} (Title Case), {username}
"""

import base64
import binascii
import json
from urllib.parse import quote

from api.depends import get_user_service, verify_api_secret
from api.schemas.subscription import (
    SubscriptionSettingsResponse,
    SubscriptionSettingsUpdateRequest,
)
from domain.errors import UserNotFoundError
from domain.user_service import UserService
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from utils.logging_config import get_logger
from utils.settings import settings

logger = get_logger(__name__)

# Display names shown in Hiddify for each protocol
_PROTO_DISPLAY: dict[str, str] = {
    "vless": "VLESS",
    "vmess": "VMess",
    "trojan": "Trojan",
    "hysteria2": "Hysteria2",
    "shadowsocks": "Shadowsocks",
}


def _vmess_set_label(url: str, label: str) -> str:
    """Inject ``label`` into the VMess JSON ``ps`` field.

    VMess share links are ``vmess://base64(json)`` — the display name must
    live inside the JSON payload, not as a URI fragment.
    """
    prefix = "vmess://"
    if not url.startswith(prefix):
        return url
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


# Canonical order for the subscription payload
_PROTO_ORDER = ["vless", "vmess", "trojan", "hysteria2", "shadowsocks"]

router = APIRouter(prefix="/sub", tags=["subscription"])


def _require_enabled() -> None:
    if not settings.SUBSCRIPTION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription endpoint is disabled on this node",
        )


def _apply_fragment(url: str, proto: str, username: str) -> str:
    """Replace the URI fragment with the configured template label.

    VMess is EXCLUDED — its display name lives inside the base64 JSON (``ps``
    field) and appending a ``#fragment`` breaks many clients (Hiddify, v2rayN).
    """
    if proto == "vmess":
        return url  # label already baked into the JSON "ps" field
    display = _PROTO_DISPLAY.get(proto, proto.title())
    label = settings.SUB_URI_TEMPLATE.format(
        protocol=proto,
        Protocol=display,
        username=username,
    )
    # URL-encode so emoji + spaces survive in the URI fragment
    encoded_label = quote(label, safe="")
    base = url.split("#", 1)[0]
    return f"{base}#{encoded_label}"


def _build_settings_response() -> SubscriptionSettingsResponse:
    return SubscriptionSettingsResponse(
        subscription_enabled=settings.SUBSCRIPTION_ENABLED,
        server_domain=settings.SERVER_DOMAIN,
        sub_uri_template=settings.SUB_URI_TEMPLATE,
    )


@router.get(
    "/settings",
    response_model=SubscriptionSettingsResponse,
    dependencies=[Depends(verify_api_secret)],
    summary="Read subscription settings",
)
async def get_subscription_settings() -> SubscriptionSettingsResponse:
    return _build_settings_response()


@router.put(
    "/settings",
    response_model=SubscriptionSettingsResponse,
    dependencies=[Depends(verify_api_secret)],
    summary="Update subscription settings",
)
async def update_subscription_settings(
    body: SubscriptionSettingsUpdateRequest,
) -> SubscriptionSettingsResponse:
    try:
        settings.update_sub_uri_template(body.sub_uri_template)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    logger.info("Subscription URI template updated")
    return _build_settings_response()


@router.get(
    "/{username}",
    response_class=PlainTextResponse,
    dependencies=[Depends(_require_enabled)],
    summary="Hiddify-compatible subscription (no auth required)",
    description=(
        "Returns base64-encoded proxy URIs for all 5 protocols. "
        "Paste the URL directly into Hiddify → Add Profile → From URL. "
        "Enable with SUBSCRIPTION_ENABLED=true. "
        "Customize proxy labels with SUB_URI_TEMPLATE in .env.local or /sub/settings."
    ),
)
async def get_subscription(
    username: str,
    server_domain: str | None = Query(
        default=None,
        description="Optional override for generated share URLs; defaults to SERVER_DOMAIN",
    ),
    svc: UserService = Depends(get_user_service),
) -> PlainTextResponse:
    try:
        effective_domain = settings.resolve_server_domain(server_domain)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        ) from e

    logger.info("Sub request: user='%s' domain='%s'", username, effective_domain)

    try:
        result = await svc.get_user_configs(username, effective_domain)
    except UserNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    configs = result.get("configs", {})

    uris = []
    for proto in _PROTO_ORDER:
        cfg = configs.get(proto)
        if cfg and cfg.get("config_url"):
            uri = cfg["config_url"]
            display = _PROTO_DISPLAY.get(proto, proto.title())
            label = settings.SUB_URI_TEMPLATE.format(
                protocol=proto,
                Protocol=display,
                username=username,
            )
            if proto == "vmess":
                # VMess label lives inside the base64 JSON "ps" field
                uri = _vmess_set_label(uri, label)
            else:
                uri = _apply_fragment(uri, proto, username)
            uris.append(uri)

    if not uris:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No protocol configs found for user '{username}'",
        )

    payload = "\n".join(uris)
    encoded = base64.b64encode(payload.encode()).decode()

    logger.info("Sub generated: user='%s' protocols=%d", username, len(uris))

    return PlainTextResponse(
        content=encoded,
        headers={
            "Content-Disposition": f'attachment; filename="{username}.txt"',
            "Profile-Title": f"Feint VPN - {username}",
            "Support-URL": f"https://{effective_domain}",
        },
    )
