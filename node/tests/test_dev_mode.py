"""
Unit tests for DEV_MODE configuration.

This test verifies that FastAPI docs are disabled in production mode.
"""

import os
from unittest.mock import patch


def test_dev_mode_setting_default():
    """Test that DEV_MODE defaults to False."""
    from utils.settings import Settings

    settings = Settings()
    assert settings.DEV_MODE is False, "DEV_MODE should default to False"


def test_dev_mode_setting_true():
    """Test that DEV_MODE can be enabled via environment variable."""
    with patch.dict(os.environ, {"DEV_MODE": "true"}):
        from utils.settings import Settings

        settings = Settings()
        assert settings.DEV_MODE is True, "DEV_MODE should be True when set to 'true'"


def test_dev_mode_setting_various_true_values():
    """Test that DEV_MODE accepts various truthy values."""
    true_values = ["true", "True", "TRUE", "1", "yes", "Yes", "YES"]

    for value in true_values:
        with patch.dict(os.environ, {"DEV_MODE": value}, clear=False):
            from utils.settings import Settings

            settings = Settings()
            assert settings.DEV_MODE is True, (
                f"DEV_MODE should be True for value '{value}'"
            )


def test_dev_mode_setting_false():
    """Test that DEV_MODE is False for non-truthy values."""
    false_values = ["false", "False", "FALSE", "0", "no", "No", "NO", ""]

    for value in false_values:
        with patch.dict(os.environ, {"DEV_MODE": value}, clear=False):
            from utils.settings import Settings

            settings = Settings()
            assert settings.DEV_MODE is False, (
                f"DEV_MODE should be False for value '{value}'"
            )


def test_main_app_docs_disabled_in_production():
    """Test that FastAPI docs are disabled when DEV_MODE is False."""
    from pathlib import Path

    # Check main.py configuration
    main_py_path = Path(__file__).parent.parent / "src" / "main.py"
    with open(main_py_path) as f:
        content = f.read()

    assert 'docs_url="/docs" if settings.DEV_MODE else None' in content, (
        "main.py should conditionally enable docs based on DEV_MODE"
    )
    assert 'redoc_url="/redoc" if settings.DEV_MODE else None' in content, (
        "main.py should conditionally enable redoc based on DEV_MODE"
    )
    assert 'openapi_url="/openapi.json" if settings.DEV_MODE else None' in content, (
        "main.py should conditionally enable openapi based on DEV_MODE"
    )


def test_env_example_has_dev_mode():
    """Test that .env.example includes DEV_MODE setting."""
    from pathlib import Path

    env_example_path = Path(__file__).parent.parent.parent / ".env.example"
    with open(env_example_path) as f:
        content = f.read()

    assert "DEV_MODE" in content, ".env.example should include DEV_MODE setting"
    assert "DEV_MODE=false" in content, ".env.example should default DEV_MODE to false"
