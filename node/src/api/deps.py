"""FastAPI dependency injection — wires adapters to domain services."""

import hmac
import os

from adapters.clash_stats import ClashStatsBackend
from adapters.docker_runtime import DockerRuntime, NoopRuntime
from adapters.init_service import InitService
from adapters.node_telemetry import NodeTelemetryService
from adapters.singbox_file_store import SingBoxFileStore
from adapters.url_builder import UrlBuilder
from domain.user_service import UserService
from fastapi import Header, HTTPException, status

_DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"
_DEFAULT_API_SECRET = "change-me-in-production"


def configured_api_secret() -> str | None:
    """Return the configured secret only when it is safe to accept."""
    expected = os.getenv("API_SECRET")
    if not expected or expected == _DEFAULT_API_SECRET:
        return None
    return expected


def is_valid_api_secret(provided_secret: str | None) -> bool:
    """Return whether a caller supplied the configured API secret."""
    expected = configured_api_secret()
    return bool(expected and provided_secret) and hmac.compare_digest(
        provided_secret, expected
    )


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


def get_user_service() -> UserService:
    runtime = NoopRuntime() if _DEV_MODE else DockerRuntime()
    return UserService(
        store=SingBoxFileStore(),
        runtime=runtime,
        url_builder=UrlBuilder(),
    )


def get_stats_backend() -> ClashStatsBackend:
    return ClashStatsBackend()


def get_init_service() -> InitService:
    return InitService()


def get_node_telemetry_service() -> NodeTelemetryService:
    return NodeTelemetryService(store=SingBoxFileStore())
