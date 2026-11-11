"""Authenticated outbound management."""

from typing import Annotated

from api.depends import get_outbound_service, verify_api_secret
from api.schemas.outbound import OutboundUsersRequest
from fastapi import APIRouter, Depends, Path, Response, status
from models.configuration import ManagedOutboundConfig
from services.outbound import OutboundService

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
    body: ManagedOutboundConfig,
    service: OutboundService = Depends(get_outbound_service),
) -> Response:
    await service.set(outbound_id, body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{outbound_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_outbound(
    outbound_id: OutboundId,
    service: OutboundService = Depends(get_outbound_service),
) -> Response:
    await service.delete(outbound_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{outbound_id}/user/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_outbound_user(
    outbound_id: OutboundId,
    user_id: OutboundUserId,
    service: OutboundService = Depends(get_outbound_service),
) -> Response:
    await service.add_user(outbound_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{outbound_id}/users", status_code=status.HTTP_204_NO_CONTENT)
async def add_outbound_users(
    outbound_id: OutboundId,
    body: OutboundUsersRequest,
    service: OutboundService = Depends(get_outbound_service),
) -> Response:
    await service.add_users(outbound_id, body.users)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{outbound_id}/user/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_outbound_user(
    outbound_id: OutboundId,
    user_id: OutboundUserId,
    service: OutboundService = Depends(get_outbound_service),
) -> Response:
    await service.remove_user(outbound_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{outbound_id}/users", status_code=status.HTTP_204_NO_CONTENT)
async def remove_outbound_users(
    outbound_id: OutboundId,
    body: OutboundUsersRequest,
    service: OutboundService = Depends(get_outbound_service),
) -> Response:
    await service.remove_users(outbound_id, body.users)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
