"""Cryptographic utilities for VPN credentials."""

import base64
import secrets


def generate_secure_password(length: int = 32) -> str:
    if not 16 <= length <= 128:
        raise ValueError(f"Password length must be between 16 and 128, got {length}")
    return base64.b64encode(secrets.token_bytes(length)).decode()
