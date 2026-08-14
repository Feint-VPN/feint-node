"""Unit tests for the TrafficTracker adapter."""

import json
from unittest.mock import AsyncMock, patch

import grpc
import pytest
from adapters.traffic_tracker import TrafficTracker, _parse_user_stat_name


@pytest.mark.asyncio
async def test_poll_accumulates_user_traffic(tmp_path):
    state_path = tmp_path / "traffic.json"
    tracker = TrafficTracker(
        v2ray_api_address="127.0.0.1:10085",
        state_path=str(state_path),
    )

    payload1 = {
        "alice": {"upload": 100, "download": 200},
        "bob": {"upload": 10, "download": 20},
    }
    payload2 = {
        "alice": {"upload": 150, "download": 280},
    }

    with patch.object(
        tracker, "_fetch_user_counters", AsyncMock(return_value=payload1)
    ):
        await tracker._poll_once()

    alice = await tracker.get_user("alice")
    bob = await tracker.get_user("bob")
    assert alice["upload"] == 100
    assert alice["download"] == 200
    assert alice["last_seen"] is not None
    assert bob["upload"] == 10
    assert bob["download"] == 20

    # Second poll: only c1 still active, but its counters grew.
    with patch.object(
        tracker, "_fetch_user_counters", AsyncMock(return_value=payload2)
    ):
        await tracker._poll_once()

    alice2 = await tracker.get_user("alice")
    bob2 = await tracker.get_user("bob")
    # delta added: 50 upload + 80 download
    assert alice2["upload"] == 150
    assert alice2["download"] == 280
    # bob retains his previous total even though connection closed
    assert bob2["upload"] == 10
    assert bob2["download"] == 20


@pytest.mark.asyncio
async def test_poll_with_no_user_counters_keeps_state_empty(tmp_path):
    tracker = TrafficTracker(state_path=str(tmp_path / "t.json"))
    with patch.object(tracker, "_fetch_user_counters", AsyncMock(return_value={})):
        await tracker._poll_once()
    assert await tracker.get_all() == {}


@pytest.mark.asyncio
async def test_persist_and_load_state(tmp_path):
    state_path = tmp_path / "traffic.json"
    tracker = TrafficTracker(state_path=str(state_path))
    payload = {"alice": {"upload": 7, "download": 11}}
    with patch.object(tracker, "_fetch_user_counters", AsyncMock(return_value=payload)):
        await tracker._poll_once()
    await tracker._flush_state(force=True)

    assert state_path.exists()
    on_disk = json.loads(state_path.read_text())
    assert on_disk["users"]["alice"]["upload"] == 7
    assert on_disk["users"]["alice"]["download"] == 11

    # New tracker should hydrate from disk.
    tracker2 = TrafficTracker(state_path=str(state_path))
    await tracker2._load_state()
    alice = await tracker2.get_user("alice")
    assert alice["upload"] == 7
    assert alice["download"] == 11


@pytest.mark.asyncio
async def test_reset_user_clears_totals(tmp_path):
    tracker = TrafficTracker(state_path=str(tmp_path / "t.json"))
    payload = {"alice": {"upload": 5, "download": 5}}
    with patch.object(tracker, "_fetch_user_counters", AsyncMock(return_value=payload)):
        await tracker._poll_once()

    await tracker.reset_user("alice")
    assert (await tracker.get_user("alice"))["upload"] == 0
    assert (await tracker.get_user("alice"))["download"] == 0


@pytest.mark.asyncio
async def test_poll_handles_grpc_error(tmp_path):
    tracker = TrafficTracker(state_path=str(tmp_path / "t.json"))

    with (
        patch.object(
            tracker,
            "_fetch_user_counters",
            AsyncMock(side_effect=grpc.aio.AioRpcError.__new__(grpc.aio.AioRpcError)),
        ),
        pytest.raises(grpc.RpcError),
    ):
        await tracker._poll_once()
    assert await tracker.get_all() == {}


@pytest.mark.asyncio
async def test_poll_marks_tracker_available_after_success(tmp_path):
    tracker = TrafficTracker(state_path=str(tmp_path / "t.json"))
    assert await tracker.is_available() is False

    with patch.object(
        tracker,
        "_fetch_user_counters",
        AsyncMock(return_value={"alice": {"upload": 1, "download": 2}}),
    ):
        await tracker._poll_once()

    assert await tracker.is_available() is True


def test_parse_user_stat_name():
    assert _parse_user_stat_name("user>>>alice>>>traffic>>>uplink") == (
        "alice",
        "upload",
    )
    assert _parse_user_stat_name("user>>>alice>>>traffic>>>downlink") == (
        "alice",
        "download",
    )
    assert _parse_user_stat_name("inbound>>>hy2") == (None, None)
