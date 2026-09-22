"""Persist and reload one encrypted reverse transport."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import docker
from docker.errors import DockerException, NotFound
from domain.errors import ReverseTunnelError
from domain.models import ReverseClientConfig, ReverseServerConfig, ReverseTunnelConfig


class RatholeRuntime:
    def __init__(
        self,
        config_path: str,
        state_path: str,
        container_name: str,
        private_key: str,
        public_key: str,
        proxy_port: int,
        timeout: int = 30,
        client=None,
    ) -> None:
        self._config_path = Path(config_path)
        self._state_path = Path(state_path)
        self._container_name = container_name
        self._private_key = private_key
        self.public_key = public_key
        self._proxy_port = proxy_port
        self._timeout = timeout
        self._client = client or docker.from_env()

    async def configure(self, value: ReverseTunnelConfig) -> dict[str, Any]:
        if isinstance(value, ReverseServerConfig) and not self._private_key:
            raise ReverseTunnelError("Reverse transport private key is not configured")

        config = self._render(value)
        state = self._state(value)
        await asyncio.to_thread(self._apply, config, state)
        return state

    async def disable(self) -> None:
        await asyncio.to_thread(self._apply, "", {"mode": "disabled"})

    async def get_state(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._read_state)

    def _apply(self, config: str, state: dict[str, Any]) -> None:
        previous_config = self._read_optional(self._config_path)
        previous_state = self._read_optional(self._state_path)
        try:
            self._write_atomic(self._config_path, config)
            self._write_atomic(
                self._state_path,
                json.dumps(state, separators=(",", ":")) + "\n",
            )
            self._restart()
        except Exception as error:
            self._restore(self._config_path, previous_config)
            self._restore(self._state_path, previous_state)
            with suppress(Exception):
                self._restart()
            if isinstance(error, ReverseTunnelError):
                raise
            raise ReverseTunnelError("Reverse transport update failed") from error

    def _restart(self) -> None:
        try:
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

    def _read_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"mode": "disabled"}
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ReverseTunnelError("Reverse transport state is unreadable") from error

    def _render(self, value: ReverseTunnelConfig) -> str:
        if isinstance(value, ReverseServerConfig):
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
    def _state(value: ReverseTunnelConfig) -> dict[str, Any]:
        return value.model_dump(
            mode="json",
            exclude={"token", "remote_public_key"},
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

    @staticmethod
    def _read_optional(path: Path) -> bytes | None:
        return path.read_bytes() if path.exists() else None

    @staticmethod
    def _restore(path: Path, content: bytes | None) -> None:
        if content is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}."
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise
