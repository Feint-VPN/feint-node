#!/usr/bin/env python3
"""Switch one existing SOCKS route between tested loopback entry ports.

Run inside the RU node API container; --apply is required for any mutation.
"""

import argparse
import json
import os
import ssl
import urllib.request
from pathlib import Path

CONFIG = Path("/opt/sing-box/config.json")


def route_state() -> tuple[str, int, list[str]]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    outbounds = [
        item
        for item in config["outbounds"]
        if item["tag"].startswith("outbound:") and item["type"] == "socks"
    ]
    if len(outbounds) != 1:
        raise ValueError(f"Expected one managed SOCKS outbound, found {len(outbounds)}")
    outbound = outbounds[0]
    if outbound["server"] != "127.0.0.1" or outbound.get("version", "5") != "5":
        raise ValueError("Existing outbound is not loopback SOCKS5")
    rules = [
        rule
        for rule in config["route"]["rules"]
        if rule.get("outbound") == outbound["tag"]
    ]
    if len(rules) != 1 or not rules[0].get("auth_user"):
        raise ValueError("Expected one nonempty user rule for the outbound")
    users = rules[0]["auth_user"]
    if len(users) != len(set(users)):
        raise ValueError("Duplicate route users")
    return outbound["tag"].removeprefix("outbound:"), outbound["server_port"], users


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-port", type=int, required=True)
    parser.add_argument("--new-port", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.new_port <= 65535:
        parser.error("New port must be between 1 and 65535")
    outbound_id, current_port, users = route_state()
    if current_port != args.expected_port or current_port == args.new_port:
        raise ValueError(f"Unexpected current port: {current_port}")
    print(f"route={outbound_id} users={len(users)} {current_port}->{args.new_port}")
    if not args.apply:
        print("Dry run; no mutation")
        return
    secret = os.environ.get("API_SECRET")
    if not secret or secret == "change-me-in-production":
        raise ValueError("API_SECRET is unavailable")
    body = {
        "type": "socks",
        "server": "127.0.0.1",
        "server_port": args.new_port,
        "version": "5",
        "auth_users": users,
    }
    request = urllib.request.Request(
        f"https://127.0.0.1:{os.environ.get('API_PORT', '8000')}/outbound/{outbound_id}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-API-Secret": secret},
        method="PUT",
    )
    context = ssl.create_default_context()
    # Loopback only; still validate the certificate chain.
    context.check_hostname = False
    with urllib.request.urlopen(request, context=context, timeout=90) as response:
        if response.status != 204:
            raise RuntimeError(f"Unexpected API status: {response.status}")
    _, updated_port, updated_users = route_state()
    if updated_port != args.new_port or set(updated_users) != set(users):
        raise RuntimeError("Postcondition failed; inspect node config before retrying")
    print("Route port changed; user set preserved")


if __name__ == "__main__":
    main()
