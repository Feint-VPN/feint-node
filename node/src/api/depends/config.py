"""Shared sing-box configuration dependencies."""

import asyncio
from threading import Lock

from adapters.singbox_file_store import SingBoxFileStore

config_store: SingBoxFileStore | None = None
mutation_lock: asyncio.Lock | None = None
dependency_lock = Lock()


def get_config_store() -> SingBoxFileStore:
    global config_store
    if config_store is None:
        with dependency_lock:
            if config_store is None:
                config_store = SingBoxFileStore()
    return config_store


def get_mutation_lock() -> asyncio.Lock:
    global mutation_lock
    if mutation_lock is None:
        with dependency_lock:
            if mutation_lock is None:
                mutation_lock = asyncio.Lock()
    return mutation_lock
