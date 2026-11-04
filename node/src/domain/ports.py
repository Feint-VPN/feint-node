"""Domain ports (abstract interfaces that adapters must implement)."""

from abc import ABC, abstractmethod

from domain.models import SingBoxConfig


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
    """Restart the sing-box container."""

    @abstractmethod
    async def reload(self) -> None: ...

    @abstractmethod
    async def is_running(self) -> bool: ...


class IConfigUrlBuilder(ABC):
    """Generate protocol-specific client connection URLs."""

    @abstractmethod
    def vless_url(
        self,
        uuid: str,
        domain: str,
        port: int,
    ) -> str: ...

    @abstractmethod
    def vmess_url(
        self, uuid: str, domain: str, port: int, path: str = "/vmess", label: str = ""
    ) -> str: ...

    @abstractmethod
    def trojan_url(self, password: str, domain: str, port: int) -> str: ...
    @abstractmethod
    def hysteria2_url(self, password: str, domain: str, port: int) -> str: ...

    @abstractmethod
    def shadowsocks_url(
        self, password: str, port: int, method: str, domain: str = "127.0.0.1"
    ) -> str: ...
