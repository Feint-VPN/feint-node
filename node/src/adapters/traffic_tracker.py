"""Adapter: poll sing-box V2Ray API user counters and persist cumulative totals.

Clash /connections does not expose the authenticated user for all protocols,
so it cannot produce reliable per-user traffic accounting. The V2Ray API stats
service counts bytes against metadata.User while the traffic is flowing, which
works across all authenticated protocols supported by this node.

To get cumulative usage that survives sing-box restarts we:

  1. Poll QueryStats(user>>>) every POLL_INTERVAL seconds.
  2. Diff each user's live uplink/downlink counters against the previous poll.
  3. Add only the positive deltas to a persisted accumulator.
  4. Update last_seen whenever a user's counters increase.
  5. Periodically flush totals to disk.
"""

import asyncio
import json
import os
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from utils.logging_config import get_logger

logger = get_logger(__name__)


def _normalize_v2ray_api_address(address: str) -> str:
    normalized = (address or "127.0.0.1:10085").strip().rstrip("/")
    for prefix in ("grpc://", "http://", "https://"):
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def _build_v2ray_stats_messages() -> tuple[type, type]:
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "v2ray_stats.proto"
    file_proto.package = "v2ray.core.app.stats.command"
    file_proto.syntax = "proto3"

    query_request = file_proto.message_type.add()
    query_request.name = "QueryStatsRequest"

    field = query_request.field.add()
    field.name = "pattern"
    field.number = 1
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    field = query_request.field.add()
    field.name = "reset"
    field.number = 2
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_BOOL

    field = query_request.field.add()
    field.name = "patterns"
    field.number = 3
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    field = query_request.field.add()
    field.name = "regexp"
    field.number = 4
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_BOOL

    stat = file_proto.message_type.add()
    stat.name = "Stat"

    field = stat.field.add()
    field.name = "name"
    field.number = 1
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    field = stat.field.add()
    field.name = "value"
    field.number = 2
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_INT64

    query_response = file_proto.message_type.add()
    query_response.name = "QueryStatsResponse"

    field = query_response.field.add()
    field.name = "stat"
    field.number = 1
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    field.type_name = ".v2ray.core.app.stats.command.Stat"

    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_proto)

    return (
        message_factory.GetMessageClass(
            pool.FindMessageTypeByName("v2ray.core.app.stats.command.QueryStatsRequest")
        ),
        message_factory.GetMessageClass(
            pool.FindMessageTypeByName(
                "v2ray.core.app.stats.command.QueryStatsResponse"
            )
        ),
    )


_QUERY_STATS_REQUEST, _QUERY_STATS_RESPONSE = _build_v2ray_stats_messages()


def _parse_user_stat_name(name: str) -> tuple[str | None, str | None]:
    prefix = "user>>>"
    uplink_suffix = ">>>traffic>>>uplink"
    downlink_suffix = ">>>traffic>>>downlink"

    if not name.startswith(prefix):
        return None, None
    if name.endswith(uplink_suffix):
        return name[len(prefix) : -len(uplink_suffix)], "upload"
    if name.endswith(downlink_suffix):
        return name[len(prefix) : -len(downlink_suffix)], "download"
    return None, None


async def fetch_v2ray_user_counters(
    address: str,
    request_timeout: float,
) -> dict[str, dict[str, int]]:
    request = _QUERY_STATS_REQUEST()
    request.patterns.append("user>>>")
    request.regexp = False
    request.reset = False

    async with grpc.aio.insecure_channel(
        _normalize_v2ray_api_address(address)
    ) as channel:
        query_stats = channel.unary_unary(
            "/v2ray.core.app.stats.command.StatsService/QueryStats",
            request_serializer=lambda msg: msg.SerializeToString(),
            response_deserializer=_QUERY_STATS_RESPONSE.FromString,
        )
        response = await query_stats(request, timeout=request_timeout)

    counters: dict[str, dict[str, int]] = {}
    for stat in response.stat:
        username, direction = _parse_user_stat_name(stat.name)
        if not username or not direction:
            continue
        entry = counters.setdefault(username, {"upload": 0, "download": 0})
        entry[direction] = int(stat.value)

    return counters


class TrafficTracker:
    """Background per-user traffic accumulator backed by V2Ray stats."""

    POLL_INTERVAL = 5.0  # seconds between polls
    PERSIST_INTERVAL = 30.0  # seconds between disk flushes
    REQUEST_TIMEOUT = 5.0

    def __init__(
        self,
        v2ray_api_address: str | None = None,
        state_path: str | None = None,
    ) -> None:
        self._address = _normalize_v2ray_api_address(
            v2ray_api_address or os.getenv("V2RAY_API_ADDRESS", "127.0.0.1:10085")
        )
        self._state_path = Path(
            state_path or os.getenv("TRAFFIC_STATE_PATH", "/opt/sing-box/traffic.json")
        )

        # Per-user totals: { username: {"upload": int, "download": int, "last_seen": iso8601 | None } }
        self._totals: dict[str, dict] = {}
        # Last seen live user counters returned by the V2Ray stats service.
        self._live_last: dict[str, tuple[int, int]] = {}

        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._dirty = False
        self._last_flush = 0.0
        self._available = False

    # ------------------------------------------------------------------ public

    async def start(self) -> None:
        """Load persisted state and start the background poller."""
        await self._load_state()
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="traffic-tracker")
            logger.info(
                "Traffic tracker started",
                extra={"extra_fields": {"v2ray_api_address": self._address}},
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
            await self._flush_state(force=True)

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
            self._live_last.pop(username, None)

    # ----------------------------------------------------------------- polling

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._poll_once()
                backoff = 1.0
            except grpc.RpcError as exc:
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
        counters = await self._fetch_user_counters()
        now_iso = datetime.now(tz=UTC).isoformat()

        async with self._lock:
            self._available = True
            for user, current in counters.items():
                upload = int(current.get("upload", 0))
                download = int(current.get("download", 0))

                prev = self._live_last.get(user, (0, 0))
                if upload < prev[0] or download < prev[1]:
                    prev = (0, 0)

                d_up = max(0, upload - prev[0])
                d_dn = max(0, download - prev[1])
                self._live_last[user] = (upload, download)

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

    async def _fetch_user_counters(self) -> dict[str, dict[str, int]]:
        return await fetch_v2ray_user_counters(self._address, self.REQUEST_TIMEOUT)

    # --------------------------------------------------------------- persistence

    async def _load_state(self) -> None:
        path = self._state_path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Traffic tracker state file is corrupt; starting fresh")
            return
        users = data.get("users") if isinstance(data, dict) else None
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

    async def _flush_state(self, force: bool = False) -> None:
        loop = asyncio.get_event_loop()
        now = loop.time()
        if not force:
            if not self._dirty:
                return
            if now - self._last_flush < self.PERSIST_INTERVAL:
                return

        async with self._lock:
            snapshot = {"users": {u: dict(v) for u, v in self._totals.items()}}
            self._dirty = False
        self._last_flush = now

        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=self._state_path.parent, prefix=".traffic_", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self._state_path)
                with suppress(OSError):
                    os.chmod(self._state_path, 0o600)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
        except OSError as exc:
            logger.warning(
                "Failed to persist traffic state",
                extra={
                    "extra_fields": {"path": str(self._state_path), "error": str(exc)}
                },
            )


_tracker: TrafficTracker | None = None


def get_tracker() -> TrafficTracker:
    """Return the process-wide TrafficTracker singleton."""
    global _tracker
    if _tracker is None:
        _tracker = TrafficTracker()
    return _tracker


def reset_tracker_for_tests() -> None:
    """Test helper — drop the singleton so tests can reconfigure it."""
    global _tracker
    _tracker = None
