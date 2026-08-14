"""Pytest configuration for property tests."""

import sys
from unittest.mock import MagicMock

# Mock Docker at the very beginning before any imports
docker_mock = MagicMock()
docker_mock.errors = MagicMock()
docker_mock.errors.DockerException = Exception
sys.modules["docker"] = docker_mock
sys.modules["docker.errors"] = docker_mock.errors
