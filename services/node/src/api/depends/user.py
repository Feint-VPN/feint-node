"""User-service dependency."""

from fastapi import Request
from services.user import UserService


def get_user_service(request: Request) -> UserService:
    return request.app.state.services.user
