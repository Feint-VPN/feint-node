"""User-service dependency."""

from threading import Lock

from adapters.url_builder import UrlBuilder
from api.depends.config import get_config_store, get_mutation_lock
from api.depends.runtime import get_container_runtime
from domain.user_service import UserService
from utils.settings import settings

user_service: UserService | None = None
user_service_lock = Lock()


def get_user_service() -> UserService:
    global user_service
    if user_service is None:
        with user_service_lock:
            if user_service is None:
                user_service = UserService(
                    store=get_config_store(),
                    runtime=get_container_runtime(),
                    url_builder=UrlBuilder(
                        settings.REALITY_PUBLIC_KEY,
                        settings.REALITY_SHORT_ID,
                        settings.REALITY_SERVER_NAME,
                    ),
                    mutation_lock=get_mutation_lock(),
                )
    return user_service
