"""Accumulate counters and own polling; persistence and transport are injected."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime

from contracts.statistics import CounterSource
from contracts.traffic import TrafficStore
from exceptions import StatisticsUnavailableError
from log import get_logger

logger = get_logger(__name__)


class TrafficTracker:
    """Background per-user traffic accumulator backed by V2Ray stats."""

    POLL_INTERVAL = 5.0  # seconds between polls
    PERSIST_INTERVAL = 30.0  # seconds between disk flushes
    REQUEST_TIMEOUT = 5.0

    def __init__(self, source: CounterSource, store: TrafficStore) -> None:
        self._source = source
        self._store = store

        # Per-user totals: { username: {"upload": int, "download": int, "last_seen": iso8601 | None } }
        self._totals: dict[str, dict] = {}
        # Last seen live user counters returned by the V2Ray stats service.
        self._live_last: dict[tuple[str, str], tuple[int, int]] = {}
        self._legacy_users: set[str] = set()

        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._dirty = False
        self._last_flush = 0.0
        self._available = False

    # ------------------------------------------------------------------ public

    async def start(self) -> None:
        """Load persisted state and start the background poller."""
        if self._task is not None and not self._task.done():
            return
        await self._load_state()
        await self._source.start()
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="traffic-tracker")
        logger.info(
            "Traffic tracker started",
            extra={"extra_fields": {"v2ray_api_addresses": self._source.addresses}},
        )

    async def stop(self) -> None:
        """Stop the background poller and flush state to disk."""
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=self.REQUEST_TIMEOUT * 2)
        except TimeoutError:
            self._task.cancel()
        finally:
            self._task = None
            try:
                await self._flush_state(force=True)
            finally:
                await self._source.close()

    async def get_user(self, username: str) -> dict:
        """Return totals for a user, defaulting to zeroes when unseen."""
        async with self._lock:
            entry = self._totals.get(username)
            if entry is None:
                return {
                    "upload": 0,
                    "download": 0,
                    "last_seen": None,
                    "available": self._available,
                }
            return {
                "upload": int(entry.get("upload", 0)),
                "download": int(entry.get("download", 0)),
                "last_seen": entry.get("last_seen"),
                "available": self._available,
            }

    async def get_all(self) -> dict[str, dict]:
        """Return a snapshot of all known users' totals."""
        async with self._lock:
            return {
                user: {
                    "upload": int(data.get("upload", 0)),
                    "download": int(data.get("download", 0)),
                    "last_seen": data.get("last_seen"),
                }
                for user, data in self._totals.items()
            }

    async def is_available(self) -> bool:
        async with self._lock:
            return self._available

    async def reset_user(self, username: str) -> None:
        """Drop a user from the totals (e.g. when they're deleted)."""
        async with self._lock:
            if username in self._totals:
                del self._totals[username]
                self._dirty = True
            for key in list(self._live_last):
                if key[1] == username:
                    del self._live_last[key]

    # ----------------------------------------------------------------- polling

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._poll_once()
                backoff = 1.0
            except StatisticsUnavailableError as exc:
                async with self._lock:
                    self._available = False
                logger.debug(
                    "Traffic tracker poll failed (v2ray_api unreachable)",
                    extra={"extra_fields": {"error": str(exc)}},
                )
                # Exponential backoff up to 30s when sing-box is unreachable.
                backoff = min(backoff * 2, 30.0)
            except Exception:
                async with self._lock:
                    self._available = False
                logger.exception("Traffic tracker poll raised an unexpected error")
                backoff = min(backoff * 2, 30.0)

            try:
                await self._flush_state()
            except Exception:
                logger.exception("Traffic tracker failed to persist state")

            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=max(self.POLL_INTERVAL, backoff)
                )

    async def _poll_once(self) -> None:
        sources = await self._source.read()
        now_iso = datetime.now(tz=UTC).isoformat()

        async with self._lock:
            self._available = True
            for source, user, current in (
                (source, user, current)
                for source, counters in sources
                for user, current in counters.items()
            ):
                upload = int(current.get("upload", 0))
                download = int(current.get("download", 0))

                key = (source, user)
                prev = self._live_last.get(
                    key, (upload, download) if user in self._legacy_users else (0, 0)
                )
                if upload < prev[0] or download < prev[1]:
                    prev = (0, 0)

                d_up = max(0, upload - prev[0])
                d_dn = max(0, download - prev[1])
                self._live_last[key] = (upload, download)

                entry = self._totals.setdefault(
                    user, {"upload": 0, "download": 0, "last_seen": None}
                )
                if d_up or d_dn:
                    entry["upload"] = int(entry.get("upload", 0)) + d_up
                    entry["download"] = int(entry.get("download", 0)) + d_dn
                    entry["last_seen"] = now_iso
                    self._dirty = True
                elif entry.get("last_seen") is None:
                    # Record presence the first time the stats service reports
                    # a user, even if the current counters are zero.
                    entry["last_seen"] = now_iso
                    self._dirty = True
            if self._legacy_users:
                # Legacy totals have no source checkpoint. Establish a baseline
                # without adding the core's historical counters a second time.
                self._legacy_users.clear()
                self._dirty = True

    async def _load_state(self) -> None:
        data = await asyncio.to_thread(self._store.load)
        if not isinstance(data, dict):
            return
        users = data.get("users")
        if not isinstance(users, dict):
            return
        cleaned: dict[str, dict] = {}
        for user, entry in users.items():
            if not isinstance(user, str) or not isinstance(entry, dict):
                continue
            cleaned[user] = {
                "upload": int(entry.get("upload", 0) or 0),
                "download": int(entry.get("download", 0) or 0),
                "last_seen": entry.get("last_seen") or None,
            }
        async with self._lock:
            self._totals = cleaned
            sources = data.get("counters")
            self._legacy_users = set(cleaned) if sources is None else set()
            if isinstance(sources, dict):
                self._live_last = {
                    (address, user): (values[0], values[1])
                    for address, users in sources.items()
                    if address in self._source.addresses and isinstance(users, dict)
                    for user, values in users.items()
                    if isinstance(user, str)
                    and isinstance(values, list)
                    and len(values) == 2
                    and all(type(value) is int and value >= 0 for value in values)
                }

    async def _flush_state(self, force: bool = False) -> None:
        loop = asyncio.get_event_loop()
        now = loop.time()
        if not force:
            if not self._dirty:
                return
            if now - self._last_flush < self.PERSIST_INTERVAL:
                return

        async with self._lock:
            counters = {}
            for (source, user), values in self._live_last.items():
                counters.setdefault(source, {})[user] = list(values)
            snapshot = {
                "users": {u: dict(v) for u, v in self._totals.items()},
                "counters": counters,
            }
            self._dirty = False

        writer = asyncio.create_task(asyncio.to_thread(self._store.save, snapshot))
        try:
            try:
                await asyncio.shield(writer)
            except asyncio.CancelledError:
                # A thread cannot be cancelled; finish its replace before stop
                # writes the final snapshot to the same path.
                with suppress(Exception):
                    await writer
                raise
        except OSError as exc:
            self._dirty = True
            logger.warning(
                "Failed to persist traffic state",
                extra={"extra_fields": {"error": str(exc)}},
            )
        except BaseException:
            self._dirty = True
            raise
        else:
            self._last_flush = now
