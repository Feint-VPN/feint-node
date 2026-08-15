"""Initialization-service dependency."""

from threading import Lock

from adapters.init_service import InitService

init_service: InitService | None = None
init_service_lock = Lock()


def get_init_service() -> InitService:
    global init_service
    if init_service is None:
        with init_service_lock:
            if init_service is None:
                init_service = InitService()
    return init_service
