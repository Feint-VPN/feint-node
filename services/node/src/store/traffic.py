"""Atomic file persistence for traffic totals and source checkpoints."""

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from contracts.traffic import TrafficStore
from log import get_logger

logger = get_logger(__name__)


class TrafficFileStore(TrafficStore):
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Traffic tracker state file is corrupt; starting fresh")
            return None

    def save(self, snapshot: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            dir=self.path.parent, prefix=".traffic_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(snapshot, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            with suppress(OSError):
                os.chmod(self.path, 0o600)
        finally:
            Path(temporary).unlink(missing_ok=True)
