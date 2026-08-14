"""Structured JSON logging with sensitive data redaction for VPN Node."""

import json
import logging
import logging.config
import os
import re
from datetime import UTC, datetime
from typing import Final

_SENSITIVE_PATTERNS: Final[list[tuple[re.Pattern[str], str]]] = [
    (
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
            re.IGNORECASE,
        ),
        "[UUID_REDACTED]",
    ),
    (
        re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b"),
        "[SECRET_REDACTED]",
    ),
    (
        re.compile(
            r'(?i)"(api[_-]?secret|authorization|x-api-key)"[\s:]+["\']([^"\']+)["\']'
        ),
        r'"\1": "[API_SECRET_REDACTED]"',
    ),
    (
        re.compile(r"(?i)(api[_-]?secret|authorization|x-api-key)[\s:]+(\S+)"),
        r"\1: [API_SECRET_REDACTED]",
    ),
    (
        re.compile(r'(?i)"password"[\s:]+["\']([^"\']+)["\']'),
        '"password": "[PASSWORD_REDACTED]"',
    ),
    (
        re.compile(r"(?i)\bpassword\s+(\S+)"),
        "password [PASSWORD_REDACTED]",
    ),
]


def _redact(text: str) -> str:
    """Apply all sensitive patterns to a string. Pure function, no side effects."""
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SensitiveDataFilter(logging.Filter):
    """Redacts sensitive data from log messages before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: _redact(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    _redact(a) if isinstance(a, str) else a for a in record.args
                )

        return True


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    # Standard LogRecord attributes that are already captured or not needed
    _STANDARD_ATTRS = frozenset(
        [
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "thread",
            "threadName",
            "exc_info",
            "exc_text",
            "stack_info",
            "taskName",
        ]
    )

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "severity": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Merge extra_fields dict (from logger.info(..., extra={"extra_fields": {...}}))
        extra_fields = record.__dict__.get("extra_fields")
        if isinstance(extra_fields, dict):
            log_data.update(extra_fields)

        # Add any remaining non-standard extra keys set directly via extra={}
        for key, value in record.__dict__.items():
            if (
                key not in self._STANDARD_ATTRS
                and key != "extra_fields"
                and key not in log_data
            ):
                log_data[key] = value

        return json.dumps(log_data)


def configure_logging(log_level: str | None = None) -> None:
    """Configure structured JSON logging. Call once at startup."""
    level = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        level = "INFO"

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JSONFormatter}},
            "filters": {"sensitive_data": {"()": SensitiveDataFilter}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "filters": ["sensitive_data"],
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"level": level, "handlers": ["console"]},
        }
    )


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the specified name."""
    return logging.getLogger(name)
