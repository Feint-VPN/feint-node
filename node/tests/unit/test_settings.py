"""
Unit tests for settings module.

Tests the Settings class and startup validation logic.

Requirements: 20.1
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from utils.settings import Settings


class TestSettings:
    """Test suite for Settings class."""

    def test_default_values(self):
        """Test that default values are set correctly."""
        # Clear environment variables
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()

            assert settings.CONFIG_PATH == "/opt/sing-box/config.json"
            assert settings.BACKUP_DIR == "/opt/sing-box/backups"
            assert settings.DOCKER_SOCKET == "/var/run/docker.sock"
            assert settings.SINGBOX_CONTAINER_NAME == "sing-box"
            assert settings.LOG_LEVEL == "info"
            assert settings.LOG_FORMAT == "json"

    def test_environment_variable_override(self):
        """Test that environment variables override defaults."""
        env_vars = {
            "CONFIG_PATH": "/custom/config.json",
            "BACKUP_DIR": "/custom/backups",
            "DOCKER_SOCKET": "/custom/docker.sock",
            "SINGBOX_CONTAINER_NAME": "custom-singbox",
            "LOG_LEVEL": "debug",
            "LOG_FORMAT": "text",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()

            assert settings.CONFIG_PATH == "/custom/config.json"
            assert settings.BACKUP_DIR == "/custom/backups"
            assert settings.DOCKER_SOCKET == "/custom/docker.sock"
            assert settings.SINGBOX_CONTAINER_NAME == "custom-singbox"
            assert settings.LOG_LEVEL == "debug"
            assert settings.LOG_FORMAT == "text"


class TestStartupValidation:
    """Test suite for startup validation."""

    def test_validate_startup_requirements_success(self):
        """Test successful validation when all resources exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create config file
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text("{}")

            # Create mock docker socket (as a regular file for testing)
            socket_path = Path(tmpdir) / "docker.sock"
            socket_path.write_text("")

            settings = Settings()
            settings.CONFIG_PATH = str(config_path)
            settings.DOCKER_SOCKET = str(socket_path)

            # Mock socket check since we can't create real sockets in tests
            with patch("pathlib.Path.is_socket", return_value=True):
                success, errors = settings.validate_startup_requirements()

                assert success is True
                assert errors == []

    def test_validate_startup_requirements_missing_config(self):
        """Test validation fails when config.json is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "nonexistent.json"
            socket_path = Path(tmpdir) / "docker.sock"
            socket_path.write_text("")

            settings = Settings()
            settings.CONFIG_PATH = str(config_path)
            settings.DOCKER_SOCKET = str(socket_path)

            with patch("pathlib.Path.is_socket", return_value=True):
                success, errors = settings.validate_startup_requirements()

                assert success is False
                assert len(errors) == 1
                assert "Config file not found" in errors[0]

    def test_validate_startup_requirements_missing_docker_socket(self):
        """Test validation fails when Docker socket is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text("{}")
            socket_path = Path(tmpdir) / "nonexistent.sock"

            settings = Settings()
            settings.CONFIG_PATH = str(config_path)
            settings.DOCKER_SOCKET = str(socket_path)

            success, errors = settings.validate_startup_requirements()

            assert success is False
            assert len(errors) == 1
            assert "Docker socket not found" in errors[0]

    def test_validate_startup_requirements_both_missing(self):
        """Test validation fails when both resources are missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "nonexistent.json"
            socket_path = Path(tmpdir) / "nonexistent.sock"

            settings = Settings()
            settings.CONFIG_PATH = str(config_path)
            settings.DOCKER_SOCKET = str(socket_path)

            success, errors = settings.validate_startup_requirements()

            assert success is False
            assert len(errors) == 2
            assert any("Config file not found" in e for e in errors)
            assert any("Docker socket not found" in e for e in errors)

    def test_validate_startup_requirements_config_not_readable(self):
        """Test validation fails when config file is not readable."""
        import sys

        # Skip this test on Windows as chmod doesn't work the same way
        if sys.platform == "win32":
            pytest.skip("File permission tests not supported on Windows")

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text("{}")
            config_path.chmod(0o000)  # Remove all permissions

            socket_path = Path(tmpdir) / "docker.sock"
            socket_path.write_text("")

            try:
                settings = Settings()
                settings.CONFIG_PATH = str(config_path)
                settings.DOCKER_SOCKET = str(socket_path)

                with patch("pathlib.Path.is_socket", return_value=True):
                    success, errors = settings.validate_startup_requirements()

                    assert success is False
                    assert any("not readable" in e for e in errors)
            finally:
                # Restore permissions for cleanup
                config_path.chmod(0o644)

    def test_validate_startup_requirements_config_is_directory(self):
        """Test validation fails when config path is a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config_dir"
            config_path.mkdir()

            socket_path = Path(tmpdir) / "docker.sock"
            socket_path.write_text("")

            settings = Settings()
            settings.CONFIG_PATH = str(config_path)
            settings.DOCKER_SOCKET = str(socket_path)

            with patch("pathlib.Path.is_socket", return_value=True):
                success, errors = settings.validate_startup_requirements()

                assert success is False
                assert any("is not a file" in e for e in errors)
