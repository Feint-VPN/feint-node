"""FastAPI application entry point."""

import asyncio
import time
from contextlib import asynccontextmanager

from adapters.node_telemetry import NodeTelemetryService
from adapters.traffic_tracker import TrafficTracker
from api.contract import API_VERSION
from api.depends import (
    configured_api_secret,
    get_container_runtime,
    get_node_telemetry_service,
    get_traffic_tracker,
    is_valid_api_secret,
    verify_api_secret,
)
from api.routers import initialization, stats, sub, user
from api.schemas.system import (
    NodeAvailability,
    NodeHealthResponse,
    NodeRuntimeStatus,
    NodeStatus,
    NodeStatusResponse,
)
from domain.ports import IContainerRuntime
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.routing import Match
from utils.logging_config import configure_logging, get_logger
from utils.settings import settings

# Configure logging before creating the app
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("VPN Node v2 starting up")

    # Verify startup requirements
    success, errors = settings.validate_startup_requirements()

    if not success:
        for error in errors:
            logger.warning("Startup check failed: %s", error)
    else:
        logger.info("Startup validation passed")

    logger.info("Application startup complete")

    tracker = get_traffic_tracker()
    try:
        await tracker.start()
    except Exception:
        logger.warning("Failed to start traffic tracker", exc_info=True)

    try:
        yield
    finally:
        try:
            await tracker.stop()
        except Exception:
            logger.warning("Failed to stop traffic tracker cleanly", exc_info=True)
        logger.info("VPN Node v2 shutting down")


# Create FastAPI application
app = FastAPI(
    title="VPN Node v2",
    description="Stateless REST API wrapper around sing-box VPN server for managing users across multiple protocols",
    version=API_VERSION,
    lifespan=lifespan,
    # Disable docs in production for security
    docs_url="/docs" if settings.DEV_MODE else None,
    redoc_url="/redoc" if settings.DEV_MODE else None,
    openapi_url="/openapi.json" if settings.DEV_MODE else None,
)


def _matches_declared_route(request: Request) -> bool:
    """Return whether the method and path match an application route."""
    for route in request.app.router.routes:
        match, _ = route.matches(request.scope)
        if match is Match.FULL:
            return True
    return False


def _hidden_response() -> Response:
    """Return the same empty response for hidden and unknown endpoints."""
    return Response(status_code=404)


@app.middleware("http")
async def hide_endpoints(request: Request, call_next):
    """Hide API discovery details from unauthenticated callers when enabled."""
    if settings.HIDE_ENDPOINTS and (
        not _matches_declared_route(request)
        or not is_valid_api_secret(request.headers.get("X-API-Secret"))
    ):
        return _hidden_response()
    if request.url.path == "/status" and not is_valid_api_secret(
        request.headers.get("X-API-Secret")
    ):
        if configured_api_secret() is None:
            return JSONResponse({"detail": "API auth not configured"}, status_code=500)
        return JSONResponse({"detail": "Invalid API secret"}, status_code=401)
    return await call_next(request)


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()

    # Log incoming request
    logger.info(
        "Incoming request: %s %s",
        request.method,
        request.url.path,
        extra={
            "extra_fields": {
                "method": request.method,
                "path": request.url.path,
                "client_host": request.client.host if request.client else None,
            }
        },
    )

    try:
        # Process request
        response = await call_next(request)

        # Calculate processing time
        process_time = time.time() - start_time

        # Log response
        logger.info(
            "Request completed: %s %s - %d",
            request.method,
            request.url.path,
            response.status_code,
            extra={
                "extra_fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "process_time": f"{process_time:.3f}s",
                }
            },
        )

        return response

    except Exception as e:
        # Calculate processing time
        process_time = time.time() - start_time

        # Log error
        logger.exception(
            "Request failed: %s %s - %s",
            request.method,
            request.url.path,
            e,
            extra={
                "extra_fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "process_time": f"{process_time:.3f}s",
                    "error": str(e),
                }
            },
        )

        # Return 500 error
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )


# Register routers
app.include_router(user.router)
app.include_router(stats.router)
app.include_router(initialization.router)

# Optional subscription endpoint (enabled via SUBSCRIPTION_ENABLED=true in .env)
app.include_router(sub.router)


@app.get("/health", tags=["health"], response_model=NodeHealthResponse)
async def health_check() -> NodeHealthResponse:
    """Return the cheap runtime health and API compatibility contract."""

    return NodeHealthResponse(status="ok", api_version=API_VERSION)


@app.get(
    "/status",
    tags=["health"],
    response_model=NodeStatusResponse,
    dependencies=[Depends(verify_api_secret)],
)
async def node_status(
    node_telemetry: NodeTelemetryService = Depends(get_node_telemetry_service),
    runtime: IContainerRuntime = Depends(get_container_runtime),
    tracker: TrafficTracker = Depends(get_traffic_tracker),
) -> NodeStatusResponse:
    telemetry, sing_box_running, statistics_available = await asyncio.gather(
        node_telemetry.get_status(),
        runtime.is_running(),
        tracker.is_available(),
    )
    configuration_available = telemetry.configuration_available
    healthy = configuration_available and sing_box_running and statistics_available
    return NodeStatusResponse(
        status=NodeStatus.OK if healthy else NodeStatus.DEGRADED,
        api_version=API_VERSION,
        uptime=telemetry.uptime,
        configuration=(
            NodeAvailability.AVAILABLE
            if configuration_available
            else NodeAvailability.UNAVAILABLE
        ),
        sing_box=(
            NodeRuntimeStatus.RUNNING if sing_box_running else NodeRuntimeStatus.STOPPED
        ),
        statistics=(
            NodeAvailability.AVAILABLE
            if statistics_available
            else NodeAvailability.UNAVAILABLE
        ),
        user_count=telemetry.user_count,
        protocols=telemetry.protocols,
    )
