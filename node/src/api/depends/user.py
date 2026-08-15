"""User-service dependency."""

import os
from threading import Lock

from adapters.docker_runtime import DockerRuntime, NoopRuntime
from adapters.singbox_file_store import SingBoxFileStore
from adapters.url_builder import UrlBuilder
from domain.user_service import UserService

user_service: UserService | None = None
user_service_lock = Lock()


def get_user_service() -> UserService:
    global user_service
    if user_service is None:
        with user_service_lock:
            if user_service is None:
                runtime = (
                    NoopRuntime()
                    if os.getenv("DEV_MODE", "false").lower() == "true"
                    else DockerRuntime()
                )
                user_service = UserService(
                    store=SingBoxFileStore(),
                    runtime=runtime,
                    url_builder=UrlBuilder(),
                )
    return user_service
