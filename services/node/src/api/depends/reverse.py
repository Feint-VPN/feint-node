"""Reverse-transport dependency."""

from fastapi import Request
from services.reverse import ReverseService


def get_reverse_runtime(request: Request) -> ReverseService:
    return request.app.state.services.reverse
