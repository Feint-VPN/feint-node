"""Rathole rendering and process control, without file state ownership."""

import asyncio
import json
import time

import docker
from contracts.reverse import ReverseRuntime
from docker.errors import DockerException, NotFound
from exceptions import ReverseTunnelError
from models.configuration import (
    ReverseClientConfig,
    ReverseServerConfig,
    ReverseTunnelConfig,
)


class RatholeRuntime(ReverseRuntime):
    def __init__(
        self,
        container_name: str,
        private_key: str,
        public_key: str,
        proxy_port: int,
        timeout: int = 30,
        client=None,
    ) -> None:
        self._container_name, self._private_key = container_name, private_key
        self.public_key, self._proxy_port = public_key, proxy_port
        self._timeout, self._client = timeout, client

    async def close(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)

    def reload(self) -> None:
        try:
            if self._client is None:
                self._client = docker.from_env()
            container = self._client.containers.get(self._container_name)
            container.restart(timeout=self._timeout)
        except (DockerException, NotFound) as error:
            raise ReverseTunnelError(
                "Reverse transport container is unavailable"
            ) from error

        deadline = time.monotonic() + min(self._timeout, 5)
        running_since: float | None = None
        while time.monotonic() < deadline:
            try:
                container.reload()
            except DockerException as error:
                raise ReverseTunnelError(
                    "Reverse transport container status is unavailable"
                ) from error
            if container.status == "running":
                running_since = running_since or time.monotonic()
                if time.monotonic() - running_since >= 1:
                    return
            else:
                running_since = None
            time.sleep(0.2)
        raise ReverseTunnelError(f"Reverse transport container is {container.status}")

    def render(self, value: ReverseTunnelConfig) -> str:
        if isinstance(value, ReverseServerConfig):
            if not self._private_key:
                raise ReverseTunnelError(
                    "Reverse transport private key is not configured"
                )
            return self._render_server(value)
        return self._render_client(value)

    def _render_server(self, value: ReverseServerConfig) -> str:
        return "\n".join(
            (
                "[server]",
                f"bind_addr = {self._quote(self._address(value.bind_host, value.bind_port))}",
                "heartbeat_interval = 10",
                "",
                "[server.transport]",
                'type = "noise"',
                "",
                "[server.transport.tcp]",
                "nodelay = false",
                "keepalive_secs = 10",
                "keepalive_interval = 5",
                "",
                "[server.transport.noise]",
                f"local_private_key = {self._quote(self._private_key)}",
                "",
                "[server.services.feint]",
                'type = "tcp"',
                f"token = {self._quote(value.token.get_secret_value())}",
                f"bind_addr = {self._quote(self._address(value.expose_host, value.expose_port))}",
                "nodelay = false",
                "",
            )
        )

    def _render_client(self, value: ReverseClientConfig) -> str:
        return "\n".join(
            (
                "[client]",
                f"remote_addr = {self._quote(self._address(value.remote_host, value.remote_port))}",
                "heartbeat_timeout = 25",
                "retry_interval = 1",
                "",
                "[client.transport]",
                'type = "noise"',
                "",
                "[client.transport.tcp]",
                "nodelay = false",
                "keepalive_secs = 10",
                "keepalive_interval = 5",
                "",
                "[client.transport.noise]",
                f"remote_public_key = {self._quote(value.remote_public_key)}",
                "",
                "[client.services.feint]",
                'type = "tcp"',
                f"token = {self._quote(value.token.get_secret_value())}",
                f"local_addr = {self._quote(self._address('127.0.0.1', self._proxy_port))}",
                "nodelay = false",
                "retry_interval = 1",
                "",
            )
        )

    @staticmethod
    def _address(host: str, port: int) -> str:
        normalized = host.strip()
        if not normalized or any(character.isspace() for character in normalized):
            raise ReverseTunnelError("Reverse transport host is invalid")
        if ":" in normalized and not normalized.startswith("["):
            normalized = f"[{normalized}]"
        return f"{normalized}:{port}"

    @staticmethod
    def _quote(value: str) -> str:
        return json.dumps(value)
