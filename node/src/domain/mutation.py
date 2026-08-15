"""Serialization helpers for state-changing domain operations."""

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Protocol, TypeVar, cast


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


__all__ = ("serialized_mutation",)
