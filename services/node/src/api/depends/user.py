"""User-service dependency."""

from domain.user_service import UserService
from fastapi import Request


def get_user_service(request: Request) -> UserService:
    return request.app.state.services.user
