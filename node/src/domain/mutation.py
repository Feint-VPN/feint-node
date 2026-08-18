"""Serialization helpers for state-changing domain operations."""

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Protocol, TypeVar, cast

from domain.errors import ConfigRollbackError
from domain.models import SingBoxConfig
from domain.ports import IConfigStore, IContainerRuntime


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
    store: IConfigStore,
    runtime: IContainerRuntime,
    config: SingBoxConfig,
    backup: str,
) -> None:
    try:
        await store.save(config)
        await runtime.reload()
    except asyncio.CancelledError:
        await _rollback(store, runtime, backup)
        raise
    except Exception:
        await _rollback(store, runtime, backup)
        raise


async def _rollback(
    store: IConfigStore,
    runtime: IContainerRuntime,
    backup: str,
) -> None:
    try:
        await store.restore(backup)
        await runtime.reload()
    except Exception as error:
        raise ConfigRollbackError(
            "The previous sing-box configuration could not be recovered."
        ) from error


__all__ = ("commit_config", "serialized_mutation")
