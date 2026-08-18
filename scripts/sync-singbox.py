"""Render the current sing-box template while preserving deployed users."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

template_path, current_path, output_path = map(Path, sys.argv[1:4])

values = {
    "DOMAIN": os.environ["SERVER_DOMAIN"],
    "VLESS_PORT": os.environ["VLESS_PORT"],
    "VMESS_PORT": os.environ["VMESS_PORT"],
    "TROJAN_PORT": os.environ["TROJAN_PORT"],
    "HYSTERIA2_PORT": os.environ["HYSTERIA2_PORT"],
    "SHADOWSOCKS_PORT": os.environ["SHADOWSOCKS_PORT"],
    "SHADOWSOCKS_METHOD": os.environ["SHADOWSOCKS_METHOD"],
    "SHADOWSOCKS_PASSWORD": os.environ["SHADOWSOCKS_PASSWORD"],
    "CLASH_API_SECRET": os.environ["CLASH_API_SECRET"],
    "REALITY_PRIVATE_KEY": os.environ["REALITY_PRIVATE_KEY"],
    "REALITY_SHORT_ID": os.environ["REALITY_SHORT_ID"],
    "REALITY_SERVER_NAME": os.environ["REALITY_SERVER_NAME"],
}

rendered = template_path.read_text(encoding="utf-8")
numeric_values = {
    "VLESS_PORT",
    "VMESS_PORT",
    "TROJAN_PORT",
    "HYSTERIA2_PORT",
    "SHADOWSOCKS_PORT",
}
for name, value in values.items():
    token = f"{{{{{name}}}}}"
    replacement = str(int(value)) if name in numeric_values else json.dumps(value)[1:-1]
    rendered = rendered.replace(token, replacement)

unresolved = sorted(set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", rendered)))
if unresolved:
    raise SystemExit(f"Unresolved template values: {', '.join(unresolved)}")

current = json.loads(current_path.read_text(encoding="utf-8"))
updated = json.loads(rendered)
users_by_tag = {
    inbound.get("tag"): inbound.get("users", [])
    for inbound in current.get("inbounds", [])
}
for inbound in updated.get("inbounds", []):
    inbound["users"] = users_by_tag.get(inbound.get("tag"), [])

updated["outbounds"].extend(
    outbound
    for outbound in current.get("outbounds", [])
    if str(outbound.get("tag", "")).startswith("outbound:")
)
updated["route"]["rules"].extend(
    rule
    for rule in current.get("route", {}).get("rules", [])
    if str(rule.get("outbound", "")).startswith("outbound:")
)

usernames = sorted(
    {
        user["name"]
        for users in users_by_tag.values()
        for user in users
        if user.get("name")
    }
)
updated["experimental"]["v2ray_api"]["stats"]["users"] = usernames
output_path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
