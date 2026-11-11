"""Persistence boundary for the existing traffic checkpoint format."""

from abc import ABC, abstractmethod


class TrafficStore(ABC):
    @abstractmethod
    def load(self) -> dict | None: ...

    @abstractmethod
    def save(self, snapshot: dict) -> None: ...
