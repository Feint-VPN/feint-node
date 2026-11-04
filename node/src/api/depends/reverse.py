"""Reverse-transport dependency."""

from adapters.rathole_runtime import RatholeRuntime
from fastapi import Request


def get_reverse_runtime(request: Request) -> RatholeRuntime:
    return request.app.state.services.reverse
