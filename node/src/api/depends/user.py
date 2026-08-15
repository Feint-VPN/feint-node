"""User-service dependency."""

from threading import Lock

from adapters.singbox_file_store import SingBoxFileStore
from adapters.url_builder import UrlBuilder
from api.depends.runtime import get_container_runtime
from domain.user_service import UserService

user_service: UserService | None = None
user_service_lock = Lock()


def get_user_service() -> UserService:
    global user_service
    if user_service is None:
        with user_service_lock:
            if user_service is None:
                user_service = UserService(
                    store=SingBoxFileStore(),
                    runtime=get_container_runtime(),
                    url_builder=UrlBuilder(),
                )
    return user_service
