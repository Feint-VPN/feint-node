"""User management endpoints."""

from adapters.traffic_tracker import get_tracker
from api.deps import get_user_service, verify_api_secret
from api.schemas.user import (
    UserConfigsResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
)
from domain.errors import (
    ConfigSaveError,
    InboundNotFoundError,
    SingBoxReloadError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from domain.user_service import UserService
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/user", tags=["users"], dependencies=[Depends(verify_api_secret)]
)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateRequest,
    svc: UserService = Depends(get_user_service),
) -> UserResponse:
    try:
        data = await svc.create_user(
            body.username,
            requested_uuid=body.uuid,
            requested_password=body.password,
        )
        return UserResponse(**data)
    except UserAlreadyExistsError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except (InboundNotFoundError, ConfigSaveError, SingBoxReloadError) as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e)) from e


@router.delete("/{username}", status_code=status.HTTP_200_OK)
async def delete_user(
    username: str, svc: UserService = Depends(get_user_service)
) -> JSONResponse:
    try:
        await svc.delete_user(username)
        try:
            await get_tracker().reset_user(username)
        except Exception:
            logger.warning(
                "Failed to reset traffic counters for deleted user", exc_info=True
            )
        return JSONResponse(
            {"status": "success", "message": f"User {username} deleted"}
        )
    except UserNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except (ConfigSaveError, SingBoxReloadError) as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e)) from e


@router.get("s", response_model=UserListResponse)
async def list_users(
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    svc: UserService = Depends(get_user_service),
) -> UserListResponse:
    data = await svc.list_users(skip=skip, limit=limit)
    return UserListResponse(**data)


@router.get("/{username}", response_model=UserResponse)
async def get_user(
    username: str, svc: UserService = Depends(get_user_service)
) -> UserResponse:
    try:
        return UserResponse(**(await svc.get_user(username)))
    except UserNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@router.get("/{username}/configs", response_model=UserConfigsResponse)
async def get_user_configs(
    username: str,
    server_domain: str = Query(...),
    svc: UserService = Depends(get_user_service),
) -> UserConfigsResponse:
    try:
        data = await svc.get_user_configs(username, server_domain)
        return UserConfigsResponse(**data)
    except UserNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
