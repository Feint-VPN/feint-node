"""Reverse transport configuration and state files."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from contracts.reverse import ReverseStore
from exceptions import ReverseTunnelError


class ReverseFileStore(ReverseStore):
    def __init__(self, config_path: str, state_path: str) -> None:
        self._config_path, self._state_path = Path(config_path), Path(state_path)

    def snapshot(self) -> tuple[bytes | None, bytes | None]:
        return self._read_optional(self._config_path), self._read_optional(
            self._state_path
        )

    def save(self, configuration: str, state: dict[str, Any]) -> None:
        self._write_atomic(self._config_path, configuration)
        self._write_atomic(
            self._state_path, json.dumps(state, separators=(",", ":")) + "\n"
        )

    def restore(self, previous: tuple[bytes | None, bytes | None]) -> None:
        self._restore(self._config_path, previous[0])
        self._restore(self._state_path, previous[1])

    def read_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"mode": "disabled"}
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ReverseTunnelError("Reverse transport state is unreadable") from error

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
