"""API-secret authentication dependencies."""

import hmac

from fastapi import Header, HTTPException, status
from utils.settings import settings

_DEFAULT_API_SECRET = "change-me-in-production"


def configured_api_secret() -> str | None:
    """Return the configured secret only when it is safe to accept."""
    expected = settings.API_SECRET
    if not expected or expected == _DEFAULT_API_SECRET:
        return None
    return expected


def is_valid_api_secret(provided_secret: str | None) -> bool:
    """Return whether a caller supplied the configured API secret."""
    expected = configured_api_secret()
    if not expected or not provided_secret:
        return False
    return hmac.compare_digest(provided_secret, expected)


async def verify_api_secret(
    x_api_secret: str = Header(..., alias="X-API-Secret"),
) -> None:
    expected = configured_api_secret()
    if not expected:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "API auth not configured"
        )
    if not is_valid_api_secret(x_api_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API secret")
