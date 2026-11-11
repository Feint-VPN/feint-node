"""Counter readers expose values without leaking gRPC clients to services."""

from abc import ABC, abstractmethod

from models.traffic import TrafficSources


class CounterSource(ABC):
    addresses: tuple[str, ...]

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def read(self) -> TrafficSources: ...
