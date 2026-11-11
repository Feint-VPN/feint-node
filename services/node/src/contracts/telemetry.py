"""Host data that application status requires."""

from abc import ABC, abstractmethod


class HostTelemetry(ABC):
    @abstractmethod
    def uptime_seconds(self) -> int | None: ...
