"""Serialization helpers for state-changing domain operations."""

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Protocol, TypeVar, cast

from contracts import ConfigStore, ContainerRuntime
from exceptions import ConfigRollbackError
from models.configuration import SingBoxConfig


class _MutationOwner(Protocol):
    _mutation_lock: asyncio.Lock


Mutation = TypeVar("Mutation", bound=Callable[..., Awaitable[Any]])


def serialized_mutation(method: Mutation) -> Mutation:
    """Run one mutation at a time for a shared service instance."""

    @wraps(method)
    async def wrapped(
        self: _MutationOwner,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        async with self._mutation_lock:
            return await method(self, *args, **kwargs)

    return cast(Mutation, wrapped)


async def commit_config(
    store: ConfigStore,
    runtime: ContainerRuntime,
    config: SingBoxConfig,
    backup: str,
) -> None:
    try:
        await store.save(config)
        await runtime.reload()
    except (Exception, asyncio.CancelledError):
        await _rollback(store, runtime, backup)
        raise


async def _rollback(
    store: ConfigStore,
    runtime: ContainerRuntime,
    backup: str,
) -> None:
    try:
        await store.restore(backup)
        await runtime.reload()
    except Exception as error:
        raise ConfigRollbackError(
            "The previous VPN configuration could not be recovered."
        ) from error


__all__ = ("commit_config", "serialized_mutation")
