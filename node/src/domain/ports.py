"""Domain ports (abstract interfaces that adapters must implement)."""

from abc import ABC, abstractmethod

from domain.models import Inbound, InboundUser, SingBoxConfig
from domain.users import ProtocolConfig


class IConfigStore(ABC):
    """Persist and retrieve the sing-box config file."""

    @abstractmethod
    async def load(self) -> SingBoxConfig: ...

    @abstractmethod
    async def save(self, config: SingBoxConfig) -> None: ...

    @abstractmethod
    async def backup(self) -> str: ...

    @abstractmethod
    async def restore(self, backup_path: str) -> None: ...


class IContainerRuntime(ABC):
    """Restart the selected VPN runtime."""

    @abstractmethod
    async def reload(self) -> None: ...

    @abstractmethod
    async def is_running(self) -> bool: ...


class IConfigUrlBuilder(ABC):
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
