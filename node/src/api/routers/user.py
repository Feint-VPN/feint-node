"""User management endpoints."""

from adapters.traffic_tracker import TrafficTracker
from api.depends import get_traffic_tracker, get_user_service, verify_api_secret
from api.schemas.user import (
    UserBulkCreateRequest,
    UserBulkCreateResponse,
    UserConfigsResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
)
from domain.user_service import UserService
from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

router = APIRouter(tags=["users"], dependencies=[Depends(verify_api_secret)])


@router.post("/user", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateRequest,
    svc: UserService = Depends(get_user_service),
) -> UserResponse:
    data = await svc.create_user(
        body.username,
        requested_uuid=body.uuid,
        requested_password=body.password,
    )
    return UserResponse.model_validate(data)


@router.post("/users", response_model=UserBulkCreateResponse)
async def create_users(
    body: UserBulkCreateRequest,
    svc: UserService = Depends(get_user_service),
) -> UserBulkCreateResponse:
    created = await svc.create_users(
        [(user.username, user.uuid, user.password) for user in body.users]
    )
    return UserBulkCreateResponse(created=created)


@router.delete("/user/{username}", status_code=status.HTTP_200_OK)
async def delete_user(
    username: str,
    svc: UserService = Depends(get_user_service),
    tracker: TrafficTracker = Depends(get_traffic_tracker),
) -> JSONResponse:
    await svc.delete_user(username)
    await tracker.reset_user(username)
    return JSONResponse({"status": "success", "message": f"User {username} deleted"})


@router.get("/users", response_model=UserListResponse)
async def list_users(
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    svc: UserService = Depends(get_user_service),
) -> UserListResponse:
    return UserListResponse.model_validate(await svc.list_users(skip=skip, limit=limit))


@router.get("/user/{username}", response_model=UserResponse)
async def get_user(
    username: str,
    svc: UserService = Depends(get_user_service),
) -> UserResponse:
    return UserResponse.model_validate(await svc.get_user(username))


@router.get("/user/{username}/configs", response_model=UserConfigsResponse)
async def get_user_configs(
    username: str,
    server_domain: str = Query(...),
    svc: UserService = Depends(get_user_service),
) -> UserConfigsResponse:
    return UserConfigsResponse.model_validate(
        await svc.get_user_configs(username, server_domain)
    )
