"""Property-based tests for API authentication

Property 25: API Authentication Enforcement
Validates: Requirements 12.1, 12.2
"""

import os
from unittest.mock import patch

import pytest
from api.depends import verify_api_secret
from fastapi import HTTPException, status
from hypothesis import given, settings
from hypothesis import strategies as st


class TestAPIAuthenticationEnforcement:
    """Property 25: API Authentication Enforcement

    **Validates: Requirements 12.1, 12.2**

    This property test verifies that API authentication is properly enforced
    across various secret values. The system must:
    1. Accept requests with valid authentication secrets
    2. Reject requests with invalid or missing secrets with 401 Unauthorized
    """

    @given(
        secret=st.text(
            min_size=1,
            max_size=128,
            alphabet=st.characters(
                min_codepoint=33,
                max_codepoint=126,  # Printable ASCII
            ),
        )
    )
    @settings(max_examples=50)
    @pytest.mark.asyncio
    async def test_valid_secret_always_passes(self, secret):
        """Property: Any valid secret configured in API_SECRET passes authentication

        This test generates various secret values and verifies that when the
        provided secret matches the configured API_SECRET, authentication succeeds.
        """
        with patch.dict(os.environ, {"API_SECRET": secret}):
            # Should not raise any exception when secret matches
            await verify_api_secret(x_api_secret=secret)

    @given(
        correct_secret=st.text(
            min_size=1,
            max_size=128,
            alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        ),
        wrong_secret=st.text(
            min_size=1,
            max_size=128,
            alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        ),
    )
    @settings(max_examples=50)
    @pytest.mark.asyncio
    async def test_invalid_secret_always_fails(self, correct_secret, wrong_secret):
        """Property: Any secret that doesn't match API_SECRET is rejected with 401

        This test generates pairs of different secrets and verifies that when
        the provided secret doesn't match the configured API_SECRET, authentication
        fails with 401 Unauthorized.
        """
        # Skip if secrets happen to match (rare but possible)
        if correct_secret == wrong_secret:
            return

        with patch.dict(os.environ, {"API_SECRET": correct_secret}):
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_secret(x_api_secret=wrong_secret)

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert exc_info.value.detail == "Invalid API secret"

    @given(
        secret=st.text(
            min_size=1,
            max_size=128,
            alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        )
    )
    @settings(max_examples=50)
    @pytest.mark.asyncio
    async def test_empty_secret_always_fails(self, secret):
        """Property: Empty or missing secrets always fail authentication

        This test verifies that regardless of the configured API_SECRET,
        an empty secret value is always rejected.
        """
        with patch.dict(os.environ, {"API_SECRET": secret}):
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_secret(x_api_secret="")

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    @given(
        secret=st.text(
            min_size=1,
            max_size=128,
            alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        )
    )
    @settings(max_examples=50)
    @pytest.mark.asyncio
    async def test_unconfigured_api_secret_always_fails(self, secret):
        """Property: When API_SECRET is not configured, all requests fail with 500

        This test verifies that if the API_SECRET environment variable is not set,
        the system fails safely by rejecting all authentication attempts with 500.
        """
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_secret(x_api_secret=secret)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "not configured" in exc_info.value.detail.lower()

    @given(
        base_secret=st.text(
            min_size=1,
            max_size=64,
            alphabet=st.characters(
                min_codepoint=65,
                max_codepoint=90,  # Uppercase letters only
            ),
        )
    )
    @settings(max_examples=30)
    @pytest.mark.asyncio
    async def test_case_sensitivity_enforced(self, base_secret):
        """Property: Secret comparison is case-sensitive

        This test verifies that authentication is case-sensitive by testing
        uppercase secrets against their lowercase equivalents.
        """
        lowercase_secret = base_secret.lower()

        # Skip if the secret is the same in both cases (all non-alphabetic)
        if base_secret == lowercase_secret:
            return

        with patch.dict(os.environ, {"API_SECRET": base_secret}):
            # Correct case should pass
            await verify_api_secret(x_api_secret=base_secret)

            # Wrong case should fail
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_secret(x_api_secret=lowercase_secret)

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    @given(
        secret=st.text(
            min_size=1,
            max_size=64,
            alphabet=st.characters(
                min_codepoint=33, max_codepoint=126, blacklist_characters=" \t\n\r"
            ),
        ),
        whitespace=st.sampled_from([" ", "  ", "\t", "\n", " \t"]),
    )
    @settings(max_examples=30)
    @pytest.mark.asyncio
    async def test_whitespace_significance(self, secret, whitespace):
        """Property: Whitespace in secrets is significant

        This test verifies that leading or trailing whitespace in secrets
        causes authentication to fail, ensuring exact string matching.
        """
        with patch.dict(os.environ, {"API_SECRET": secret}):
            # Secret with leading whitespace should fail
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_secret(x_api_secret=whitespace + secret)

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

            # Secret with trailing whitespace should fail
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_secret(x_api_secret=secret + whitespace)

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    @given(
        secret=st.text(
            min_size=1,
            max_size=128,
            alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        )
    )
    @settings(max_examples=30)
    @pytest.mark.asyncio
    async def test_secret_length_independence(self, secret):
        """Property: Authentication works correctly for secrets of any valid length

        This test verifies that the authentication mechanism works correctly
        regardless of the secret length (within reasonable bounds).
        """
        with patch.dict(os.environ, {"API_SECRET": secret}):
            # Correct secret should always pass
            await verify_api_secret(x_api_secret=secret)

            # Truncated secret should always fail
            if len(secret) > 1:
                truncated = secret[:-1]
                with pytest.raises(HTTPException) as exc_info:
                    await verify_api_secret(x_api_secret=truncated)

                assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

            # Extended secret should always fail
            extended = secret + "x"
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_secret(x_api_secret=extended)

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
