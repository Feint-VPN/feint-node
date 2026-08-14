"""Cryptographic utilities for VPN key generation."""

import base64
import secrets
from typing import NamedTuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


class RealityKeypair(NamedTuple):
    private_key: str
    public_key: str


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def generate_secure_password(length: int = 32) -> str:
    if not (16 <= length <= 128):
        raise ValueError(f"Password length must be between 16 and 128, got {length}")
    # Standard base64 WITH padding — required by sing-box shadowsocks-2022 psk
    return base64.b64encode(secrets.token_bytes(length)).decode()


def generate_reality_keypair() -> RealityKeypair:
    priv = X25519PrivateKey.generate()
    return RealityKeypair(
        private_key=_b64(
            priv.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
        ),
        public_key=_b64(
            priv.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ),
    )


def generate_reality_short_id() -> str:
    # short_id is an even-length hex string: 8-16 chars (4-8 bytes).
    num_bytes = secrets.randbelow(5) + 4
    return secrets.token_bytes(num_bytes).hex()


def derive_reality_public_key(private_key_b64: str) -> str:
    try:
        remainder = len(private_key_b64) % 4
        padding = "=" * (4 - remainder) if remainder else ""
        priv = X25519PrivateKey.from_private_bytes(
            base64.urlsafe_b64decode(private_key_b64 + padding)
        )
        return _b64(
            priv.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        )
    except Exception as e:
        raise ValueError(f"Invalid private key: {e}") from e
