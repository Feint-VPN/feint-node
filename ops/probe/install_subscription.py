#!/usr/bin/env python3
"""Install dedicated probe URLs from stdin without logging them."""

import argparse
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", action="store_true")
    args = parser.parse_args()
    urls = [line.strip() for line in sys.stdin if line.strip()]
    if not urls or (not args.candidates and len(urls) != 1):
        raise ValueError("Expected a dedicated probe URL")
    allowed = {"vless", "hysteria2"} if args.candidates else {"https"}
    if any(
        urlsplit(url).scheme not in allowed
        or not urlsplit(url).hostname
        or any(char.isspace() for char in url)
        for url in urls
    ):
        raise ValueError("Invalid probe URL")
    target = Path("/etc/feint/probe-candidates.txt" if args.candidates else "/etc/feint/probe.env")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as output:
        output.write("\n".join(urls) + "\n" if args.candidates else f"FEINT_PROBE_SUBSCRIPTION_URL={urls[0]}\n")
        pending = output.name
    os.chmod(pending, 0o600)
    os.replace(pending, target)
    print("Dedicated probe URLs installed")


if __name__ == "__main__":
    main()
