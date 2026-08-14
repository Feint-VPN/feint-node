"""Integration tests for the live stats router (api.routers.stats)."""

import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    from adapters.traffic_tracker import reset_tracker_for_tests
    from api.routers.stats import router

    reset_tracker_for_tests()
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def mock_env():
    with patch.dict(os.environ, {"API_SECRET": "test-secret"}):
        yield


@pytest.fixture
def auth_headers():
    return {"X-API-Secret": "test-secret"}


def _seed_tracker(totals):
    from adapters import traffic_tracker as tt

    tracker = tt.get_tracker()
    tracker._totals = totals
    return tracker


class TestStatsRouterLive:
    def test_user_stats_returns_zero_when_unseen(self, client, mock_env, auth_headers):
        from adapters.traffic_tracker import reset_tracker_for_tests

        reset_tracker_for_tests()
        _seed_tracker({})

        resp = client.get("/user/alice/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "alice"
        assert data["upload_bytes"] == 0
        assert data["download_bytes"] == 0
        assert data["total_bytes"] == 0
        assert data["last_seen"] is None
        assert data["available"] is False

    def test_user_stats_reports_tracked_totals(self, client, mock_env, auth_headers):
        from adapters.traffic_tracker import reset_tracker_for_tests

        reset_tracker_for_tests()
        ts = datetime(2025, 1, 1, 12, 0, tzinfo=UTC).isoformat()
        tracker = _seed_tracker(
            {"alice": {"upload": 100, "download": 250, "last_seen": ts}}
        )
        tracker._available = True

        resp = client.get("/user/alice/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["upload_bytes"] == 100
        assert data["download_bytes"] == 250
        assert data["total_bytes"] == 350
        assert data["last_seen"].startswith("2025-01-01T12:00:00")

    def test_get_all_stats(self, client, mock_env, auth_headers):
        from adapters.traffic_tracker import reset_tracker_for_tests

        reset_tracker_for_tests()
        _seed_tracker(
            {
                "alice": {"upload": 1, "download": 2, "last_seen": None},
                "bob": {"upload": 5, "download": 5, "last_seen": None},
            }
        )
        from adapters import traffic_tracker as tt

        tt.get_tracker()._available = True

        resp = client.get("/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert {row["username"] for row in data} == {"alice", "bob"}
        totals = {row["username"]: row["total_bytes"] for row in data}
        assert totals == {"alice": 3, "bob": 10}

    def test_user_stats_reports_backend_unavailable(
        self, client, mock_env, auth_headers
    ):
        from adapters.traffic_tracker import reset_tracker_for_tests

        reset_tracker_for_tests()
        tracker = _seed_tracker(
            {"alice": {"upload": 1, "download": 1, "last_seen": None}}
        )
        tracker._available = False

        resp = client.get("/user/alice/stats", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["available"] is False

    def test_user_stats_unauthorized(self, client, mock_env):
        resp = client.get("/user/alice/stats")
        assert resp.status_code == 422  # missing required header
