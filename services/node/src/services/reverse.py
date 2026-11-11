"""Apply reverse configuration and recover previous files on failure."""

import asyncio
from contextlib import suppress
from typing import Any

from contracts.reverse import ReverseRuntime, ReverseStore
from exceptions import ReverseTunnelError
from models.configuration import ReverseTunnelConfig


class ReverseService:
    def __init__(self, runtime: ReverseRuntime, store: ReverseStore) -> None:
        self._runtime, self._store = runtime, store

    @property
    def public_key(self) -> str:
        return self._runtime.public_key

    async def configure(self, value: ReverseTunnelConfig) -> dict[str, Any]:
        configuration = self._runtime.render(value)
        state = value.model_dump(mode="json", exclude={"token", "remote_public_key"})
        await asyncio.to_thread(self._apply, configuration, state)
        return state

    async def disable(self) -> None:
        await asyncio.to_thread(self._apply, "", {"mode": "disabled"})

    async def get_state(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._store.read_state)

    def _apply(self, configuration: str, state: dict[str, Any]) -> None:
        previous = self._store.snapshot()
        try:
            self._store.save(configuration, state)
            self._runtime.reload()
        except Exception as error:
            self._store.restore(previous)
            with suppress(Exception):
                self._runtime.reload()
            if isinstance(error, ReverseTunnelError):
                raise
            raise ReverseTunnelError("Reverse transport update failed") from error
