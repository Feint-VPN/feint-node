"""Interfaces between application operations and concrete infrastructure."""

from abc import ABC, abstractmethod

from models.configuration import Inbound, InboundUser, SingBoxConfig
from models.user import ProtocolConfig


class ConfigStore(ABC):
    """Persist and retrieve the sing-box config file."""

    @abstractmethod
    async def load(self) -> SingBoxConfig: ...

    @abstractmethod
    async def save(self, config: SingBoxConfig) -> None: ...

    @abstractmethod
    async def backup(self) -> str: ...

    @abstractmethod
    async def restore(self, backup_path: str) -> None: ...


class ContainerRuntime(ABC):
    """Restart the selected VPN runtime."""

    @abstractmethod
    async def reload(self) -> None: ...

    @abstractmethod
    async def is_running(self) -> bool: ...


class ConfigUrlBuilder(ABC):
    """Generate one protocol connection from its configured inbound."""

    @abstractmethod
    def build(
        self,
        protocol: str,
        user: InboundUser,
        inbound: Inbound,
        domain: str,
        *,
        public_port: int | None = None,
    ) -> ProtocolConfig: ...


__all__ = ("ConfigStore", "ConfigUrlBuilder", "ContainerRuntime")
