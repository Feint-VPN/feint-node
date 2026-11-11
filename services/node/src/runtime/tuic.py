"""Optional sing-box TUIC companion for an otherwise unchanged Xray node."""

import asyncio
import hashlib
import json
from pathlib import Path

from exceptions import SingBoxReloadError
from models.configuration import SingBoxConfig
from runtime.docker import DockerRuntime, XrayDockerRuntime
from runtime.xray.configuration import render_xray_config
from store.configuration import SingBoxFileStore


def build_tuic_config(config: SingBoxConfig, stats_listen: str) -> SingBoxConfig:
    companion = config.model_copy(deep=True)
    companion.inbounds = [item for item in companion.inbounds if item.type == "tuic"]
    if not companion.inbounds:
        raise ValueError("TUIC companion requires a configured TUIC inbound")
    users = sorted(
        {user.name for item in companion.inbounds for user in item.users if user.name}
    )
    companion.experimental = {
        "v2ray_api": {
            "listen": stats_listen,
            "stats": {"enabled": True, "users": users},
        }
    }
    return companion


def _digest(path: str) -> bytes | None:
    file = Path(path)
    if not file.exists():
        return None
    document = json.loads(file.read_text(encoding="utf-8"))
    return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).digest()


class TuicXrayRuntime(XrayDockerRuntime):
    def __init__(
        self,
        *args,
        tuic_container_name: str,
        tuic_config_path: str,
        tuic_stats_listen: str,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.tuic = DockerRuntime(tuic_container_name, timeout=self.timeout)
        self.tuic_config_path = tuic_config_path
        self.tuic_stats_listen = tuic_stats_listen
        self._xray_applied = _digest(self.xray_config_path)
        self._tuic_applied = _digest(tuic_config_path)

    async def reload(self) -> None:
        store = SingBoxFileStore(self.config_path)
        config = build_tuic_config(await store.load(), self.tuic_stats_listen)
        await SingBoxFileStore(self.tuic_config_path).save(config)
        container = await asyncio.to_thread(self.tuic._get_container)
        result = await asyncio.to_thread(
            container.exec_run, ["sing-box", "check", "-c", self.tuic_config_path]
        )
        if result.exit_code:
            raise SingBoxReloadError("Generated TUIC config is invalid")
        candidate = await asyncio.to_thread(_digest, self.tuic_config_path)
        if candidate != self._tuic_applied or not await self.tuic.is_running():
            self._tuic_applied = None
            await self.tuic.reload()
            self._tuic_applied = candidate
        await asyncio.to_thread(
            render_xray_config,
            self.config_path,
            self.xray_config_path,
            self.api_listen,
            min_client_version=self.min_client_version,
            hysteria2_compat_port=self.hysteria2_compat_port,
            control_api=self.control is not None,
        )
        candidate = await asyncio.to_thread(_digest, self.xray_config_path)
        if candidate != self._xray_applied or not await super().is_running():
            self._xray_applied = None
            await super().reload()
            self._xray_applied = candidate

    async def is_running(self) -> bool:
        return await super().is_running() and await self.tuic.is_running()

    async def close(self) -> None:
        await super().close()
        await self.tuic.close()
