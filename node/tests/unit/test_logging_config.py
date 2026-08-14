"""Tests for logging configuration."""

import json
import logging
import os
from io import StringIO
from unittest.mock import patch

from src.utils.logging_config import (
    JSONFormatter,
    SensitiveDataFilter,
    _redact,
    configure_logging,
    get_logger,
)

# ---------------------------------------------------------------------------
# _redact — pure function, tested directly with plain strings
# ---------------------------------------------------------------------------


class TestRedact:
    def test_uuid_redacted(self):
        result = _redact("user 550e8400-e29b-41d4-a716-446655440000 ok")
        assert "550e8400" not in result
        assert "[UUID_REDACTED]" in result

    def test_password_json_redacted(self):
        result = _redact('{"password": "topsecret"}')
        assert "topsecret" not in result
        assert "[PASSWORD_REDACTED]" in result

    def test_password_keyword_redacted(self):
        result = _redact("password mysecret123")
        assert "mysecret123" not in result

    def test_api_secret_redacted(self):
        result = _redact("api_secret: " + "a" * 32)
        assert "[API_SECRET_REDACTED]" in result

    def test_long_base64_redacted(self):
        token = "A" * 40
        assert token not in _redact(f"token {token}")

    def test_plain_text_unchanged(self):
        assert _redact("hello world") == "hello world"

    def test_empty_string(self):
        assert _redact("") == ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger(name: str) -> tuple[logging.Logger, StringIO]:
    buf = StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(SensitiveDataFilter())
    log = logging.getLogger(name)
    log.handlers = [handler]
    log.setLevel(logging.DEBUG)
    log.propagate = False
    return log, buf


def _parse(buf: StringIO) -> dict:
    return json.loads(buf.getvalue().strip())


# ---------------------------------------------------------------------------
# JSONFormatter
# ---------------------------------------------------------------------------


class TestJSONFormatter:
    def test_output_is_valid_json(self):
        log, buf = _make_logger("t.json")
        log.info("hello")
        _parse(buf)  # must not raise

    def test_required_fields_present(self):
        log, buf = _make_logger("t.fields")
        log.info("msg")
        data = _parse(buf)
        for field in (
            "timestamp",
            "severity",
            "name",
            "message",
            "module",
            "function",
            "line",
        ):
            assert field in data

    def test_timestamp_ends_with_z(self):
        log, buf = _make_logger("t.ts")
        log.info("msg")
        assert _parse(buf)["timestamp"].endswith("Z")

    def test_severity_matches_level(self):
        log, buf = _make_logger("t.sev")
        log.warning("msg")
        assert _parse(buf)["severity"] == "WARNING"

    def test_exception_included(self):
        log, buf = _make_logger("t.exc")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            log.exception("caught")
        data = _parse(buf)
        assert "exception" in data
        assert "RuntimeError" in data["exception"]

    def test_extra_fields_merged_top_level(self):
        log, buf = _make_logger("t.extra")
        log.info("done", extra={"extra_fields": {"domain": "x.com", "op": "init"}})
        data = _parse(buf)
        assert data["domain"] == "x.com"
        assert data["op"] == "init"
        assert "extra_fields" not in data


# ---------------------------------------------------------------------------
# SensitiveDataFilter
# ---------------------------------------------------------------------------


class TestSensitiveDataFilter:
    def test_always_returns_true(self):
        f = SensitiveDataFilter()
        record = logging.LogRecord("t", logging.INFO, "", 0, "msg", (), None)
        assert f.filter(record) is True

    def test_uuid_redacted_end_to_end(self):
        log, buf = _make_logger("t.uuid")
        log.info("user 550e8400-e29b-41d4-a716-446655440000 ok")
        assert "550e8400" not in buf.getvalue()
        assert "[UUID_REDACTED]" in buf.getvalue()

    def test_args_tuple_redacted(self):
        f = SensitiveDataFilter()
        record = logging.LogRecord(
            "t",
            logging.INFO,
            "",
            0,
            "user %s",
            ("550e8400-e29b-41d4-a716-446655440000",),
            None,
        )
        f.filter(record)
        assert isinstance(record.args, tuple)
        assert "[UUID_REDACTED]" in record.args[0]  # type: ignore[operator]

    def test_args_dict_redacted(self):
        f = SensitiveDataFilter()
        record = logging.LogRecord("t", logging.INFO, "", 0, "%(msg)s", (), None)
        record.args = {"msg": "password topsecret"}  # type: ignore[assignment]
        f.filter(record)
        assert isinstance(record.args, dict)
        assert "[PASSWORD_REDACTED]" in str(record.args["msg"])


# ---------------------------------------------------------------------------
# configure_logging / get_logger
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    def test_explicit_level_applied(self):
        configure_logging("DEBUG")
        assert logging.getLogger().level == logging.DEBUG

    def test_invalid_level_defaults_to_info(self):
        configure_logging("NONSENSE")
        assert logging.getLogger().level == logging.INFO

    def test_env_var_used_when_no_arg(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "WARNING"}):
            configure_logging()
        assert logging.getLogger().level == logging.WARNING

    def test_get_logger_returns_named_logger(self):
        log = get_logger("myapp.test")
        assert isinstance(log, logging.Logger)
        assert log.name == "myapp.test"
