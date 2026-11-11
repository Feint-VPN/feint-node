"""Translate domain failures without exposing runtime/configuration details."""

from exceptions import (
    ConfigRollbackError,
    InboundNotFoundError,
    OutboundInUseError,
    OutboundNotFoundError,
    OutboundUserNotFoundError,
    SingBoxReloadError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from log import get_logger

logger = get_logger(__name__)
ERROR_RESPONSES = {
    UserAlreadyExistsError: (409, None),
    UserNotFoundError: (404, None),
    OutboundNotFoundError: (404, None),
    OutboundUserNotFoundError: (404, None),
    OutboundInUseError: (409, None),
    InboundNotFoundError: (500, "Node configuration update failed"),
    SingBoxReloadError: (500, "Node configuration update failed"),
    ConfigRollbackError: (500, "Node configuration update failed"),
}


async def domain_error(request: Request, error: Exception) -> JSONResponse:
    status, detail = next(
        response
        for kind, response in ERROR_RESPONSES.items()
        if isinstance(error, kind)
    )
    if status >= 500:
        logger.error(
            "Node operation failed: %s %s",
            request.method,
            request.url.path,
            exc_info=error,
        )
    return JSONResponse({"detail": detail or str(error)}, status_code=status)


def register_error_handlers(app: FastAPI) -> None:
    for kind in ERROR_RESPONSES:
        app.add_exception_handler(kind, domain_error)
