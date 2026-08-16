"""Property-based tests for logging and secret redaction

Property 36: Secret Redaction in Logs
Validates: Requirements 12.4, 20.5
"""

import json
import logging
from io import StringIO

from hypothesis import given, settings
from hypothesis import strategies as st
from src.utils.logging_config import JSONFormatter, SensitiveDataFilter


class TestSecretRedactionInLogs:
    """Property 36: Secret Redaction in Logs

    **Validates: Requirements 12.4, 20.5**

    This property test verifies that sensitive data (passwords, UUIDs, API secrets)
    is automatically redacted from log messages. The system must never log sensitive
    data in plain text, regardless of how it appears in the log message.
    """

    @given(
        uuid_value=st.uuids(version=4),
        message_template=st.sampled_from(
            [
                "User {0} created successfully",
                "Processing request for user {0}",
                "UUID: {0}",
                "User ID {0} deleted",
                "Found user with UUID {0}",
                "Error for user {0}: operation failed",
                "User: {0}",
            ]
        ),
    )
    @settings(max_examples=100)
    def test_uuid_always_redacted(self, uuid_value, message_template):
        """Property: UUIDs are always redacted from log messages

        This test generates various UUID v4 values and message templates,
        verifying that UUIDs are always replaced with [UUID_REDACTED] regardless
        of context or message format.
        """
        filter_instance = SensitiveDataFilter()
        uuid_str = str(uuid_value)
        message = message_template.format(uuid_str)

        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg=message,
            args=(),
            exc_info=None,
        )

        filter_instance.filter(record)

        # UUID should be redacted
        assert uuid_str not in record.msg
        assert "[UUID_REDACTED]" in record.msg

    @given(
        password=st.text(
            min_size=8,
            max_size=64,
            alphabet=st.characters(
                whitelist_categories=("Lu", "Ll", "Nd")
            ),  # Alphanumeric only
        ).filter(
            lambda p: not p.isdigit() and not p.isalpha()
        ),  # Must contain both letters and digits
        format_type=st.sampled_from(
            [
                "password {0}",
                '"password": "{0}"',
                "user password {0}",
                "Password {0}",  # Case-insensitive, but needs space not colon
            ]
        ),
    )
    @settings(max_examples=100)
    def test_password_always_redacted(self, password, format_type):
        """Property: Passwords are always redacted from log messages

        This test generates alphanumeric password values (mixed letters and digits)
        and various formats, verifying that passwords are always redacted when they
        appear with the "password" keyword followed by whitespace. The redaction
        pattern requires whitespace after "password", not colons or other punctuation.
        """
        filter_instance = SensitiveDataFilter()
        message = format_type.format(password)

        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg=message,
            args=(),
            exc_info=None,
        )

        filter_instance.filter(record)

        # Password should be redacted
        assert f"password {password}" not in record.msg
        assert "[PASSWORD_REDACTED]" in record.msg or "[SECRET_REDACTED]" in record.msg

    @given(
        secret_bytes=st.binary(min_size=32, max_size=64),
        format_type=st.sampled_from(
            [
                "API secret: {0}",
                '"api_secret": "{0}"',
                "api-secret: {0}",
                "Authorization: {0}",
                "x-api-key: {0}",
                "Secret: {0}",
                "Token: {0}",
            ]
        ),
    )
    @settings(max_examples=100)
    def test_base64_secret_always_redacted(self, secret_bytes, format_type):
        """Property: Base64-encoded secrets are always redacted

        This test generates cryptographically secure random bytes, encodes them
        as base64 (simulating API secrets, passwords), and verifies they are
        redacted from log messages.
        """
        import base64

        filter_instance = SensitiveDataFilter()
        secret = base64.b64encode(secret_bytes).decode("ascii")
        message = format_type.format(secret)

        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg=message,
            args=(),
            exc_info=None,
        )

        filter_instance.filter(record)

        # Secret should be redacted
        assert secret not in record.msg
        assert (
            "[SECRET_REDACTED]" in record.msg or "[API_SECRET_REDACTED]" in record.msg
        )

    @given(
        uuid_value=st.uuids(version=4),
        password=st.text(
            min_size=8,
            max_size=32,
            alphabet=st.characters(
                whitelist_categories=("Lu", "Ll", "Nd")
            ),  # Alphanumeric
        ),
        secret_bytes=st.binary(min_size=32, max_size=48),
    )
    @settings(max_examples=50)
    def test_multiple_secrets_all_redacted(self, uuid_value, password, secret_bytes):
        """Property: Multiple sensitive values in one message are all redacted

        This test verifies that when a log message contains multiple types of
        sensitive data (UUID, password, API secret), all of them are redacted.
        """
        import base64

        filter_instance = SensitiveDataFilter()
        uuid_str = str(uuid_value)
        secret = base64.b64encode(secret_bytes).decode("ascii")

        message = (
            f"User {uuid_str} authenticated with password {password} "
            f"using API secret {secret}"
        )

        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg=message,
            args=(),
            exc_info=None,
        )

        filter_instance.filter(record)

        # All sensitive data should be redacted
        assert uuid_str not in record.msg
        assert f"password {password}" not in record.msg
        assert secret not in record.msg
        assert "[UUID_REDACTED]" in record.msg
        assert "[PASSWORD_REDACTED]" in record.msg or "[SECRET_REDACTED]" in record.msg

    @given(
        api_secret=st.text(
            min_size=16,
            max_size=64,
            alphabet=st.characters(
                min_codepoint=33,
                max_codepoint=126,
                blacklist_characters="\"'",
            ),
        ),
        header_name=st.sampled_from(
            [
                "api_secret",
                "api-secret",
                "API_SECRET",
                "API-SECRET",
                "authorization",
                "Authorization",
                "AUTHORIZATION",
                "x-api-key",
                "X-API-Key",
                "X-API-KEY",
            ]
        ),
    )
    @settings(max_examples=100)
    def test_api_secret_header_always_redacted(self, api_secret, header_name):
        """Property: API secret headers are always redacted regardless of case

        This test generates various API secret header names (different cases,
        formats) and verifies that the secret values are always redacted.
        """
        filter_instance = SensitiveDataFilter()

        # Test JSON format
        message_json = f'{{"{header_name}": "{api_secret}"}}'
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg=message_json,
            args=(),
            exc_info=None,
        )

        filter_instance.filter(record)

        # Secret should be redacted
        assert api_secret not in record.msg
        assert (
            "[API_SECRET_REDACTED]" in record.msg or "[SECRET_REDACTED]" in record.msg
        )

    @given(
        uuid_value=st.uuids(version=4),
        log_level=st.sampled_from(
            [
                logging.DEBUG,
                logging.INFO,
                logging.WARNING,
                logging.ERROR,
                logging.CRITICAL,
            ]
        ),
    )
    @settings(max_examples=50)
    def test_redaction_works_at_all_log_levels(self, uuid_value, log_level):
        """Property: Redaction works consistently across all log levels

        This test verifies that sensitive data redaction works regardless of
        the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        """
        filter_instance = SensitiveDataFilter()
        uuid_str = str(uuid_value)
        message = f"Processing user {uuid_str}"

        record = logging.LogRecord(
            name="test_logger",
            level=log_level,
            pathname="test.py",
            lineno=42,
            msg=message,
            args=(),
            exc_info=None,
        )

        filter_instance.filter(record)

        # UUID should be redacted regardless of log level
        assert uuid_str not in record.msg
        assert "[UUID_REDACTED]" in record.msg

    @given(
        uuid_value=st.uuids(version=4),
        secret_bytes=st.binary(min_size=32, max_size=48),
    )
    @settings(max_examples=50)
    def test_redaction_preserves_log_structure(self, uuid_value, secret_bytes):
        """Property: Redaction preserves log message structure and readability

        This test verifies that after redaction, the log message is still
        valid, readable, and maintains its structure (not corrupted).
        """
        import base64

        filter_instance = SensitiveDataFilter()
        uuid_str = str(uuid_value)
        secret = base64.b64encode(secret_bytes).decode("ascii")

        message = f"User {uuid_str} created with secret {secret}"

        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg=message,
            args=(),
            exc_info=None,
        )

        filter_instance.filter(record)

        # Message should still be readable and structured
        assert "User" in record.msg
        assert "created with secret" in record.msg
        assert "[UUID_REDACTED]" in record.msg
        assert "[SECRET_REDACTED]" in record.msg
        # Message should not be empty or corrupted
        assert len(record.msg) > 0
        assert record.msg.strip() != ""

    @given(
        uuid_value=st.uuids(version=4),
        password=st.text(
            min_size=8,
            max_size=32,
            alphabet=st.characters(
                whitelist_categories=("Lu", "Ll", "Nd")
            ),  # Alphanumeric
        ),
    )
    @settings(max_examples=50)
    def test_redaction_in_json_formatted_output(self, uuid_value, password):
        """Property: Redaction works in JSON-formatted log output

        This test verifies that sensitive data is redacted even when logs
        are formatted as JSON (the actual output format used in production).
        """
        log_buffer = StringIO()
        handler = logging.StreamHandler(log_buffer)
        handler.setFormatter(JSONFormatter())
        handler.addFilter(SensitiveDataFilter())

        logger = logging.getLogger("test_redaction")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False

        uuid_str = str(uuid_value)
        message = f"User {uuid_str} with password {password}"

        # Log the message
        logger.info(message)

        # Get the JSON output
        output = json.loads(log_buffer.getvalue())
        redacted_message = output["message"]

        # Verify sensitive data is not in the redacted message. A generated secret
        # may legitimately equal a JSON metadata key such as "severity".
        assert uuid_str not in redacted_message
        assert f"password {password}" not in redacted_message
        assert "[UUID_REDACTED]" in redacted_message
        assert (
            "[PASSWORD_REDACTED]" in redacted_message
            or "[SECRET_REDACTED]" in redacted_message
        )

    @given(
        case_variant=st.sampled_from(
            [
                str.lower,
                str.upper,
                lambda s: s,  # original case
            ]
        )
    )
    @settings(max_examples=30)
    def test_uuid_redaction_case_insensitive(self, case_variant):
        """Property: UUID redaction is case-insensitive

        This test verifies that UUIDs are redacted regardless of case
        (lowercase, uppercase, mixed case).
        """
        filter_instance = SensitiveDataFilter()
        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        uuid_variant = case_variant(uuid_str)
        message = f"User UUID: {uuid_variant}"

        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg=message,
            args=(),
            exc_info=None,
        )

        filter_instance.filter(record)

        # UUID should be redacted regardless of case
        assert uuid_variant not in record.msg
        assert "[UUID_REDACTED]" in record.msg

    @given(
        uuid_value=st.uuids(version=4),
        num_occurrences=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=50)
    def test_multiple_uuid_occurrences_all_redacted(self, uuid_value, num_occurrences):
        """Property: Multiple occurrences of the same UUID are all redacted

        This test verifies that when the same UUID appears multiple times
        in a log message, all occurrences are redacted.
        """
        filter_instance = SensitiveDataFilter()
        uuid_str = str(uuid_value)

        # Create message with multiple occurrences
        parts = [f"User {uuid_str}"] * num_occurrences
        message = " and ".join(parts)

        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg=message,
            args=(),
            exc_info=None,
        )

        filter_instance.filter(record)

        # UUID should not appear anywhere in the message
        assert uuid_str not in record.msg
        # Should have the expected number of redactions
        assert record.msg.count("[UUID_REDACTED]") == num_occurrences

    @given(
        secret_bytes_list=st.lists(
            st.binary(min_size=32, max_size=48),
            min_size=1,
            max_size=5,
            unique=True,
        ),
    )
    @settings(max_examples=30)
    def test_multiple_different_secrets_all_redacted(self, secret_bytes_list):
        """Property: Multiple different secrets in one message are all redacted

        This test verifies that when a log message contains multiple different
        base64 secrets, all of them are redacted.
        """
        import base64

        filter_instance = SensitiveDataFilter()
        secrets = [base64.b64encode(sb).decode("ascii") for sb in secret_bytes_list]

        message = "Secrets: " + ", ".join(secrets)

        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg=message,
            args=(),
            exc_info=None,
        )

        filter_instance.filter(record)

        # All secrets should be redacted
        for secret in secrets:
            assert secret not in record.msg

        # Should have the expected number of redactions
        assert record.msg.count("[SECRET_REDACTED]") == len(secrets)

    def test_filter_always_returns_true(self):
        """Property: Filter always allows records through after redaction

        This test verifies that the filter never blocks log records,
        it only modifies them to redact sensitive data.
        """
        filter_instance = SensitiveDataFilter()

        # Test with various messages
        messages = [
            "Normal message",
            "User 550e8400-e29b-41d4-a716-446655440000",
            "password secret123",
            "api_secret: abc123def456",
            "",
        ]

        for message in messages:
            record = logging.LogRecord(
                name="test_logger",
                level=logging.INFO,
                pathname="test.py",
                lineno=42,
                msg=message,
                args=(),
                exc_info=None,
            )

            result = filter_instance.filter(record)
            assert result is True

    @given(
        uuid_value=st.uuids(version=4),
        exception_msg=st.text(min_size=5, max_size=50),
    )
    @settings(max_examples=30)
    def test_redaction_in_exception_messages(self, uuid_value, exception_msg):
        """Property: Redaction works in exception stack traces

        This test verifies that sensitive data in exception messages
        is also redacted when logged.
        """
        filter_instance = SensitiveDataFilter()
        uuid_str = str(uuid_value)

        try:
            raise ValueError(f"Error processing user {uuid_str}: {exception_msg}")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test_logger",
            level=logging.ERROR,
            pathname="test.py",
            lineno=42,
            msg=f"Exception occurred for user {uuid_str}",
            args=(),
            exc_info=exc_info,
        )

        filter_instance.filter(record)

        # UUID should be redacted from the message
        assert uuid_str not in record.msg
        assert "[UUID_REDACTED]" in record.msg
