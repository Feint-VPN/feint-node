"""Render versioned SOCKS handlers shared by runtime and migration preparation."""

import hashlib
import json
from typing import Any


def socks_outbound(tag: str, data: dict) -> dict:
    server: dict[str, Any] = {"address": data["server"], "port": data["server_port"]}
    if data.get("username"):
        server["users"] = [{"user": data["username"], "pass": data["password"]}]
        # A new endpoint gets a new handler; old sessions finish on the old one.
        revision = hashlib.sha256(
            json.dumps(server, sort_keys=True).encode()
        ).hexdigest()[:16]
        tag += ".native-" + revision
    return {"protocol": "socks", "tag": tag, "settings": {"servers": [server]}}
