"""Pytest configuration and shared fixtures"""

import sys
from pathlib import Path

import pytest

# Add src directory to Python path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


@pytest.fixture
def test_data_dir():
    """Return path to test data directory"""
    return Path(__file__).parent / "data"


@pytest.fixture
def mock_config_path(tmp_path):
    """Return temporary config file path"""
    return tmp_path / "config.json"
