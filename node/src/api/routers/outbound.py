"""Authenticated outbound management."""

from typing import Annotated

from api.depends import get_outbound_service, verify_api_secret
from api.schemas.outbound import OutboundUsersRequest
from domain.errors import (
    ConfigRollbackError,
    OutboundInUseError,
    OutboundNotFoundError,
    OutboundUserNotFoundError,
    SingBoxReloadError,
)
from domain.models import Hysteria2OutboundConfig
from domain.outbound_service import OutboundService
from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from utils.logging_config import get_logger

logger = get_logger(__name__)
OutboundId = Annotated[
    str,
    Path(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$"),
]
OutboundUserId = Annotated[str, Path(min_length=1, max_length=64)]

router = APIRouter(
    prefix="/outbound",
    tags=["outbounds"],
    dependencies=[Depends(verify_api_secret)],
)


@router.put("/{outbound_id}", status_code=status.HTTP_204_NO_CONTENT)
async def set_outbound(
    outbound_id: OutboundId,
    body: Hysteria2OutboundConfig,
    service: OutboundService = Depends(get_outbound_service),
) -> Response:
    try:
        await service.set(outbound_id, body)
    except OutboundUserNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (SingBoxReloadError, ConfigRollbackError) as error:
        logger.exception("Failed to set outbound")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Node configuration update failed",
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{outbound_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_outbound(
    outbound_id: OutboundId,
    service: OutboundService = Depends(get_outbound_service),
) -> Response:
    try:
        await service.delete(outbound_id)
    except OutboundNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except OutboundInUseError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except (SingBoxReloadError, ConfigRollbackError) as error:
        logger.exception("Failed to delete outbound")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Node configuration update failed",
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{outbound_id}/user/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_outbound_user(
    outbound_id: OutboundId,
    user_id: OutboundUserId,
    service: OutboundService = Depends(get_outbound_service),
) -> Response:
    try:
        await service.add_user(outbound_id, user_id)
    except (OutboundNotFoundError, OutboundUserNotFoundError) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (SingBoxReloadError, ConfigRollbackError) as error:
        logger.exception("Failed to add outbound user")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Node configuration update failed"
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{outbound_id}/users", status_code=status.HTTP_204_NO_CONTENT)
async def add_outbound_users(
    outbound_id: OutboundId,
    body: OutboundUsersRequest,
    service: OutboundService = Depends(get_outbound_service),
) -> Response:
    try:
        await service.add_users(outbound_id, body.users)
    except (OutboundNotFoundError, OutboundUserNotFoundError) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (SingBoxReloadError, ConfigRollbackError) as error:
        logger.exception("Failed to add outbound users")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Node configuration update failed"
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{outbound_id}/user/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_outbound_user(
    outbound_id: OutboundId,
    user_id: OutboundUserId,
    service: OutboundService = Depends(get_outbound_service),
) -> Response:
    try:
        await service.remove_user(outbound_id, user_id)
    except OutboundNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (SingBoxReloadError, ConfigRollbackError) as error:
        logger.exception("Failed to remove outbound user")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Node configuration update failed"
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{outbound_id}/users", status_code=status.HTTP_204_NO_CONTENT)
async def remove_outbound_users(
    outbound_id: OutboundId,
    body: OutboundUsersRequest,
    service: OutboundService = Depends(get_outbound_service),
) -> Response:
    try:
        await service.remove_users(outbound_id, body.users)
    except OutboundNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (SingBoxReloadError, ConfigRollbackError) as error:
        logger.exception("Failed to remove outbound users")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Node configuration update failed"
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
