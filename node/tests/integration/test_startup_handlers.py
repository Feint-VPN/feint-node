"""
Integration tests for application startup and shutdown handlers.

Tests the lifespan context manager in main.py.

Requirements: 20.1
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated_traffic_tracker():
    tracker = Mock(start=AsyncMock(), stop=AsyncMock())
    with (
        patch.dict(os.environ, {"API_SECRET": "test-secret"}),
        patch("main.get_traffic_tracker", return_value=tracker),
    ):
        yield tracker


AUTH_HEADERS = {"X-API-Secret": "test-secret"}


class TestStartupHandlers:
    """Test suite for startup and shutdown handlers."""

    def test_startup_with_valid_resources(self, caplog):
        """Test application starts successfully when all resources are available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create config file
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text("{}")

            # Create mock docker socket
            socket_path = Path(tmpdir) / "docker.sock"
            socket_path.write_text("")

            # Patch settings and socket check
            with patch("utils.settings.settings.CONFIG_PATH", str(config_path)):
                with patch("utils.settings.settings.DOCKER_SOCKET", str(socket_path)):
                    with patch("pathlib.Path.is_socket", return_value=True):
                        # Import main after patching
                        from main import app

                        # Create test client (triggers lifespan)
                        with TestClient(app) as client:
                            # Verify app is functional
                            response = client.get("/health", headers=AUTH_HEADERS)
                            assert response.status_code == 200
                            assert response.json() == {
                                "status": "ok",
                                "api_version": "2.0",
                            }

    def test_startup_with_missing_config(self):
        """Test application still starts when config.json is missing (logs warning)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "nonexistent.json"
            socket_path = Path(tmpdir) / "docker.sock"
            socket_path.write_text("")

            with patch("utils.settings.settings.CONFIG_PATH", str(config_path)):
                with patch("utils.settings.settings.DOCKER_SOCKET", str(socket_path)):
                    with patch("pathlib.Path.is_socket", return_value=True):
                        from main import app

                        with TestClient(app) as client:
                            # App should still be functional (warnings, not errors)
                            response = client.get("/health", headers=AUTH_HEADERS)
                            assert response.status_code == 200

    def test_startup_with_missing_docker_socket(self):
        """Test application still starts when Docker socket is missing (logs warning)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text("{}")
            socket_path = Path(tmpdir) / "nonexistent.sock"

            with patch("utils.settings.settings.CONFIG_PATH", str(config_path)):
                with patch("utils.settings.settings.DOCKER_SOCKET", str(socket_path)):
                    from main import app

                    with TestClient(app) as client:
                        # App should still be functional
                        response = client.get("/health", headers=AUTH_HEADERS)
                        assert response.status_code == 200

    def test_startup_with_both_resources_missing(self):
        """Test application still starts when both resources are missing (logs warnings)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "nonexistent.json"
            socket_path = Path(tmpdir) / "nonexistent.sock"

            with patch("utils.settings.settings.CONFIG_PATH", str(config_path)):
                with patch("utils.settings.settings.DOCKER_SOCKET", str(socket_path)):
                    from main import app

                    with TestClient(app) as client:
                        # App should still be functional
                        response = client.get("/health", headers=AUTH_HEADERS)
                        assert response.status_code == 200
