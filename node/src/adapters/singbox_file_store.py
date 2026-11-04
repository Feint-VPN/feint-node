"""Adapter: read/write sing-box config.json atomically."""

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from domain.models import SingBoxConfig
from domain.ports import IConfigStore


class SingBoxFileStore(IConfigStore):
    def __init__(
        self,
        config_path: str = "/opt/sing-box/config.json",
        backup_dir: str = "/opt/sing-box/backups",
    ) -> None:
        self.config_path = Path(config_path)
        self.backup_dir = Path(backup_dir)

    def _ensure_backup_dir(self) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    async def load(self) -> SingBoxConfig:
        return await asyncio.to_thread(self._load)

    def _load(self) -> SingBoxConfig:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config not found: {self.config_path}")
        with open(self.config_path, encoding="utf-8") as f:
            return SingBoxConfig(**json.load(f))

    async def save(self, config: SingBoxConfig) -> None:
        await asyncio.to_thread(self._save, config)

    def _save(self, config: SingBoxConfig) -> None:
        fd, tmp = tempfile.mkstemp(
            dir=self.config_path.parent, prefix=".cfg_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(
                    config.model_dump(mode="json", exclude_none=True),
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.config_path)
            os.chmod(self.config_path, 0o600)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    async def backup(self) -> str:
        return await asyncio.to_thread(self._backup)

    def _backup(self) -> str:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config not found: {self.config_path}")
        self._ensure_backup_dir()
        ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        dest = self.backup_dir / f"config_{ts}.json"
        with open(self.config_path, encoding="utf-8") as src:
            data = src.read()
        with open(dest, "w", encoding="utf-8") as dst:
            dst.write(data)
            dst.flush()
            os.fsync(dst.fileno())
        os.chmod(dest, 0o600)
        return str(dest)

    async def restore(self, backup_path: str) -> None:
        await asyncio.to_thread(self._restore, backup_path)

    def _restore(self, backup_path: str) -> None:
        src = Path(backup_path)
        if not src.exists():
            raise FileNotFoundError(f"Backup not found: {backup_path}")
        fd, tmp = tempfile.mkstemp(
            dir=self.config_path.parent, prefix=".restore_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(src.read_text(encoding="utf-8"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.config_path)
            os.chmod(self.config_path, 0o600)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
