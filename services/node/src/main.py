"""FastAPI application entry point."""

import time
from contextlib import asynccontextmanager

from api.contract import API_VERSION
from api.depends import (
    configured_api_secret,
    is_valid_api_secret,
)
from api.errors import register_error_handlers
from api.routers import outbound, reverse, stats, sub, system, user
from application import Application
from config import settings
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from log import configure_logging, get_logger
from starlette.routing import Match

# Configure logging before creating the app
configure_logging(settings.LOG_LEVEL)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("VPN Node v2 starting up")

    services = Application(settings)
    app.state.services = services
    await services.start()

    logger.info("Application startup complete")
    try:
        yield
    finally:
        await services.close()
        del app.state.services
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
register_error_handlers(app)
app.include_router(user.router)
app.include_router(outbound.router)
app.include_router(reverse.router)
app.include_router(stats.router)

# Optional subscription endpoint (enabled via SUBSCRIPTION_ENABLED=true in .env)
app.include_router(sub.router)


app.include_router(system.router)
