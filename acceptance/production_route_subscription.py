"""Check live node subscription rendering without printing credentials or links."""

import argparse
import base64
import json
import os
import ssl
import urllib.parse
import urllib.request
from pathlib import Path


def main(domain: str) -> None:
    config = json.loads(Path("/opt/sing-box/config.json").read_text(encoding="utf-8"))
    routed = {
        user
        for rule in config["route"]["rules"]
        if str(rule.get("outbound", "")).startswith("outbound:")
        for user in rule.get("auth_user", [])
    }
    users = {
        user["name"]
        for inbound in config["inbounds"]
        for user in inbound.get("users", [])
    }
    direct = users - routed
    if not routed or not direct:
        raise AssertionError("Need both routed and direct users")

    context = ssl.create_default_context()
    context.check_hostname = False  # Loopback TLS; chain verification remains enabled.
    for kind, username in (
        ("routed", sorted(routed)[0]),
        ("direct", sorted(direct)[0]),
    ):
        request = urllib.request.Request(
            f"https://127.0.0.1:{os.environ['API_PORT']}/sub/"
            + urllib.parse.quote(username, safe="")
            + "?server_domain="
            + urllib.parse.quote(domain, safe=""),
            headers={"X-API-Secret": os.environ["API_SECRET"]},
        )
        with urllib.request.urlopen(request, context=context, timeout=10) as response:
            if response.status != 200:
                raise AssertionError(f"{kind} subscription HTTP {response.status}")
            links = base64.b64decode(response.read()).decode().splitlines()
        if len(links) != 1 or not links[0].startswith("hysteria2://"):
            raise AssertionError(f"{kind} did not receive exactly one HY2 link")
        if urllib.parse.urlsplit(links[0]).hostname != domain:
            raise AssertionError(f"{kind} link has wrong host")
        print(f"{kind}: 1 HY2 link, correct RU host")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain", help="Public entry domain")
    main(parser.parse_args().domain)
