"""FastAPI dependencies and application-scoped singletons."""

from api.depends.authentication import (
    configured_api_secret,
    is_valid_api_secret,
    verify_api_secret,
)
from api.depends.initialization import get_init_service
from api.depends.runtime import get_container_runtime
from api.depends.statistics import (
    get_stats_backend,
    get_traffic_tracker,
)
from api.depends.telemetry import get_node_telemetry_service
from api.depends.user import get_user_service

__all__ = (
    "configured_api_secret",
    "get_container_runtime",
    "get_init_service",
    "get_node_telemetry_service",
    "get_stats_backend",
    "get_traffic_tracker",
    "get_user_service",
    "is_valid_api_secret",
    "verify_api_secret",
)
