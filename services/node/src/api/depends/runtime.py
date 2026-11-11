"""Container-runtime dependency."""

from contracts import ContainerRuntime
from fastapi import Request


def get_container_runtime(request: Request) -> ContainerRuntime:
    return request.app.state.services.runtime
