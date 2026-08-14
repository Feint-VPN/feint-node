"""Tests for cryptographic utilities."""

import base64

import pytest
from src.utils.crypto import (
    RealityKeypair,
    derive_reality_public_key,
    generate_reality_keypair,
    generate_reality_short_id,
    generate_secure_password,
)


class TestSecurePassword:
    def test_is_a_32_byte_base64_key(self):
        password = generate_secure_password()
        raw = base64.b64decode(password, validate=True)

        assert len(raw) == 32
        assert base64.b64encode(raw).decode() == password

    def test_unique(self):
        assert generate_secure_password() != generate_secure_password()

    @pytest.mark.parametrize("bad", [15, 129])
    def test_invalid_length_raises(self, bad: int):
        with pytest.raises(ValueError):
            generate_secure_password(bad)


class TestRealityKeypair:
    def test_returns_namedtuple(self):
        assert isinstance(generate_reality_keypair(), RealityKeypair)

    def test_no_padding(self):
        kp = generate_reality_keypair()
        assert "=" not in kp.private_key
        assert "=" not in kp.public_key

    def test_unique(self):
        assert generate_reality_keypair() != generate_reality_keypair()

    def test_public_key_matches_derived(self):
        kp = generate_reality_keypair()
        assert derive_reality_public_key(kp.private_key) == kp.public_key


class TestDeriveRealityPublicKey:
    def test_roundtrip(self):
        kp = generate_reality_keypair()
        assert derive_reality_public_key(kp.private_key) == kp.public_key

    def test_invalid_key_raises(self):
        with pytest.raises(ValueError, match="Invalid private key"):
            derive_reality_public_key("not-valid!!!")

    def test_padding_never_breaks(self):
        # X25519 raw keys are always 32 bytes → 43 stripped base64 chars (len%4==3)
        # Run several times to confirm the padding logic is stable
        for _ in range(10):
            kp = generate_reality_keypair()
            assert len(kp.private_key) == 43
            derive_reality_public_key(kp.private_key)  # must not raise


class TestRealityShortId:
    def test_length_in_range(self):
        for _ in range(50):
            assert 8 <= len(generate_reality_short_id()) <= 16

    def test_hex_only(self):
        for _ in range(20):
            assert all(c in "0123456789abcdef" for c in generate_reality_short_id())

    def test_unique(self):
        ids = {generate_reality_short_id() for _ in range(20)}
        assert len(ids) > 1
