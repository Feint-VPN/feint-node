"""HTTP presentation of the optional, public node subscription."""

from api.depends import verify_api_secret
from api.depends.subscription import get_subscription_service
from api.schemas.subscription import (
    SubscriptionSettingsResponse,
    SubscriptionSettingsUpdateRequest,
)
from exceptions import (
    SubscriptionDomainError,
    SubscriptionNotFoundError,
    UserNotFoundError,
)
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from services.subscription import SubscriptionService

router = APIRouter(prefix="/sub", tags=["subscription"])


def _require_enabled(
    service: SubscriptionService = Depends(get_subscription_service),
) -> None:
    if not service.enabled:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Subscription endpoint is disabled on this node"
        )


def _settings(service: SubscriptionService) -> SubscriptionSettingsResponse:
    return SubscriptionSettingsResponse(
        subscription_enabled=service.enabled,
        server_domain=service.server_domain,
        sub_uri_template=service.template,
    )


@router.get(
    "/settings",
    response_model=SubscriptionSettingsResponse,
    dependencies=[Depends(verify_api_secret)],
    summary="Read subscription settings",
)
async def get_subscription_settings(
    service: SubscriptionService = Depends(get_subscription_service),
) -> SubscriptionSettingsResponse:
    return _settings(service)


@router.put(
    "/settings",
    response_model=SubscriptionSettingsResponse,
    dependencies=[Depends(verify_api_secret)],
    summary="Update subscription settings",
)
async def update_subscription_settings(
    body: SubscriptionSettingsUpdateRequest,
    service: SubscriptionService = Depends(get_subscription_service),
) -> SubscriptionSettingsResponse:
    try:
        service.update_template(body.sub_uri_template)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return _settings(service)


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
    service: SubscriptionService = Depends(get_subscription_service),
) -> PlainTextResponse:
    try:
        encoded, domain = await service.build(username, server_domain)
    except SubscriptionDomainError as error:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, str(error)
        ) from error
    except (UserNotFoundError, SubscriptionNotFoundError) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return PlainTextResponse(
        content=encoded,
        headers={
            "Content-Disposition": f'attachment; filename="{username}.txt"',
            "Profile-Title": f"Feint VPN - {username}",
            "Support-URL": f"https://{domain}",
        },
    )
