"""Adapter: query sing-box clash_api for traffic stats."""

import httpx
from domain.ports import IStatsBackend


class ClashStatsBackend(IStatsBackend):
    def __init__(
        self, base_url: str = "http://localhost:9090", timeout: float = 5.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def get_user_bytes(self, username: str) -> tuple[int, int]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}/connections", params={"user": username}
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("upload", 0), data.get("download", 0)
