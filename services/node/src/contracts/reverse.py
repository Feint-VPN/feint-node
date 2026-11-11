"""Boundaries for reverse transport files and its process."""

from abc import ABC, abstractmethod
from typing import Any

from models.configuration import ReverseTunnelConfig


class ReverseRuntime(ABC):
    public_key: str

    @abstractmethod
    def render(self, value: ReverseTunnelConfig) -> str: ...

    @abstractmethod
    def reload(self) -> None: ...


class ReverseStore(ABC):
    @abstractmethod
    def snapshot(self) -> tuple[bytes | None, bytes | None]: ...

    @abstractmethod
    def save(self, configuration: str, state: dict[str, Any]) -> None: ...

    @abstractmethod
    def restore(self, previous: tuple[bytes | None, bytes | None]) -> None: ...

    @abstractmethod
    def read_state(self) -> dict[str, Any]: ...
