"""Reject unsafe chain parameters before any SSH/Docker side effect."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/lib/diagnostics"))

from remote_chain_probe import validate

value = {
    "variant": "native",
    "mbps": 30,
    "seconds": 30,
    "entry_address": "100.64.0.1",
    "exit_address": "100.64.0.2",
    "exit_public": "1.1.1.1",
    "port": 43000,
}
validate(value)
for field, invalid in (
    ("variant", "unknown"),
    ("mbps", 300),
    ("mbps", True),
    ("seconds", 61),
    ("entry_address", "0.0.0.0"),
    ("exit_address", "8.8.8.8"),
    ("exit_public", "127.0.0.1"),
    ("port", 443),
    ("ss_over_tailscale", "yes"),
    ("hot_swap", "yes"),
):
    try:
        validate(value | {field: invalid})
    except ValueError:
        continue
    raise AssertionError(f"Unsafe chain parameter accepted: {field}={invalid}")
print("Chain scope/rate limits passed")
validate(value | {"variant": "uot", "hot_swap": True})
try:
    validate(value | {"hot_swap": True, "seconds": 10})
except ValueError:
    pass
else:
    raise AssertionError(
        "Hot swap accepted a probe too short to span switch and rollback"
    )
