"""Encrypted reverse-transport management."""

from api.depends import verify_api_secret
from api.depends.reverse import get_reverse_runtime
from api.schemas.reverse import ReverseKeyResponse, ReverseStateResponse
from exceptions import ReverseTunnelError
from fastapi import APIRouter, Depends, HTTPException, Response, status
from log import get_logger
from models.configuration import ReverseTunnelConfig
from services.reverse import ReverseService

logger = get_logger(__name__)

router = APIRouter(
    prefix="/reverse",
    tags=["reverse transport"],
    dependencies=[Depends(verify_api_secret)],
)


@router.get("", response_model=ReverseStateResponse)
async def get_reverse(
    runtime: ReverseService = Depends(get_reverse_runtime),
) -> ReverseStateResponse:
    try:
        state = await runtime.get_state()
    except ReverseTunnelError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    mode = str(state.pop("mode", "disabled"))
    return ReverseStateResponse(mode=mode, details=state)


@router.get("/key", response_model=ReverseKeyResponse)
async def get_reverse_key(
    runtime: ReverseService = Depends(get_reverse_runtime),
) -> ReverseKeyResponse:
    if not runtime.public_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Reverse transport key is not configured",
        )
    return ReverseKeyResponse(public_key=runtime.public_key)


@router.put("", status_code=status.HTTP_204_NO_CONTENT)
async def set_reverse(
    body: ReverseTunnelConfig,
    runtime: ReverseService = Depends(get_reverse_runtime),
) -> Response:
    try:
        await runtime.configure(body)
    except ReverseTunnelError as error:
        logger.exception("Failed to configure reverse transport")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Reverse transport update failed",
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reverse(
    runtime: ReverseService = Depends(get_reverse_runtime),
) -> Response:
    try:
        await runtime.disable()
    except ReverseTunnelError as error:
        logger.exception("Failed to disable reverse transport")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Reverse transport update failed",
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
