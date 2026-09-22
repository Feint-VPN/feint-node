"""Reverse-transport dependency."""

from functools import lru_cache

from adapters.rathole_runtime import RatholeRuntime
from utils.settings import settings


@lru_cache(maxsize=1)
def get_reverse_runtime() -> RatholeRuntime:
    return RatholeRuntime(
        config_path=settings.RATHOLE_CONFIG_PATH,
        state_path=settings.RATHOLE_STATE_PATH,
        container_name=settings.RATHOLE_CONTAINER_NAME,
        private_key=settings.RATHOLE_PRIVATE_KEY,
        public_key=settings.RATHOLE_PUBLIC_KEY,
        proxy_port=settings.REVERSE_PROXY_PORT,
    )
