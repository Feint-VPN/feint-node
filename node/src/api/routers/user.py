"""User management endpoints."""

from api.depends import get_traffic_tracker, get_user_service, verify_api_secret
from api.schemas.user import (
    UserBulkCreateRequest,
    UserBulkCreateResponse,
    UserConfigsResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
)
from domain.errors import (
    ConfigRollbackError,
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

router = APIRouter(tags=["users"], dependencies=[Depends(verify_api_secret)])


@router.post("/user", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
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
    except (InboundNotFoundError, SingBoxReloadError, ConfigRollbackError) as error:
        logger.exception("Failed to create user")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Node configuration update failed",
        ) from error


@router.post("/users", response_model=UserBulkCreateResponse)
async def create_users(
    body: UserBulkCreateRequest,
    svc: UserService = Depends(get_user_service),
) -> UserBulkCreateResponse:
    try:
        created = await svc.create_users(
            [(user.username, user.uuid, user.password) for user in body.users]
        )
        return UserBulkCreateResponse(created=created)
    except (InboundNotFoundError, SingBoxReloadError, ConfigRollbackError) as error:
        logger.exception("Failed to create users")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Node configuration update failed",
        ) from error


@router.delete("/user/{username}", status_code=status.HTTP_200_OK)
async def delete_user(
    username: str, svc: UserService = Depends(get_user_service)
) -> JSONResponse:
    try:
        await svc.delete_user(username)
        try:
            await get_traffic_tracker().reset_user(username)
        except Exception:
            logger.warning(
                "Failed to reset traffic counters for deleted user", exc_info=True
            )
        return JSONResponse(
            {"status": "success", "message": f"User {username} deleted"}
        )
    except UserNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except (SingBoxReloadError, ConfigRollbackError) as error:
        logger.exception("Failed to delete user")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Node configuration update failed",
        ) from error


@router.get("/users", response_model=UserListResponse)
async def list_users(
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    svc: UserService = Depends(get_user_service),
) -> UserListResponse:
    data = await svc.list_users(skip=skip, limit=limit)
    return UserListResponse(**data)


@router.get("/user/{username}", response_model=UserResponse)
async def get_user(
    username: str, svc: UserService = Depends(get_user_service)
) -> UserResponse:
    try:
        return UserResponse(**(await svc.get_user(username)))
    except UserNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@router.get("/user/{username}/configs", response_model=UserConfigsResponse)
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
