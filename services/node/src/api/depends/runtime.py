"""Container-runtime dependency."""

from domain.ports import IContainerRuntime
from fastapi import Request


def get_container_runtime(request: Request) -> IContainerRuntime:
    return request.app.state.services.runtime
