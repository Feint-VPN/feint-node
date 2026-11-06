"""Probe a real TUIC share link without logging its credentials."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from probe_protocols import _tcp_probe, _udp_probe


def client_config(uri: str, port: int) -> dict:
    parsed = urlsplit(uri.strip())
    assert parsed.scheme == "tuic" and parsed.hostname and parsed.port
    query = parse_qs(parsed.query)
    assert query.get("allow_insecure", ["0"])[0] == "0"
    return {
        "log": {"level": "error"},
        "inbounds": [
            {
                "type": "socks",
                "tag": "socks",
                "listen": "127.0.0.1",
                "listen_port": port,
            }
        ],
        "outbounds": [
            {
                "type": "tuic",
                "tag": "proxy",
                "server": parsed.hostname,
                "server_port": parsed.port,
                "uuid": unquote(parsed.username or ""),
                "password": unquote(parsed.password or ""),
                "congestion_control": query.get("congestion_control", ["cubic"])[0],
                "udp_relay_mode": query.get("udp_relay_mode", ["native"])[0],
                "zero_rtt_handshake": False,
                "tls": {
                    "enabled": True,
                    "server_name": query.get("sni", [parsed.hostname])[0],
                    "alpn": query.get("alpn", ["h3"]),
                },
            }
        ],
        "route": {"final": "proxy"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", default="ghcr.io/feint-vpn/feint-sing-box:v1.13.19-feint.1"
    )
    parser.add_argument("--udp-count", type=int, default=100)
    parser.add_argument("--expected-ip", required=True)
    parser.add_argument("--reject", action="store_true")
    parser.add_argument("--expect-denied", action="store_true")
    args = parser.parse_args()
    uri = sys.stdin.read().strip()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config = client_config(uri, port)
    if args.reject:
        config["outbounds"][0]["password"] = secrets.token_urlsafe(32)
    name = "feint-tuic-probe-" + secrets.token_hex(4)
    with tempfile.TemporaryDirectory(prefix="feint-tuic-probe-") as temporary:
        path = Path(temporary) / "client.json"
        path.write_text(json.dumps(config))
        os.chmod(path, 0o600)
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--network",
                "host",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "-v",
                f"{temporary}:/probe:ro",
                args.image,
                "run",
                "-c",
                "/probe/client.json",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            if args.reject or args.expect_denied:
                try:
                    _tcp_probe(port, 5)
                except OSError:
                    print("Unauthorized TUIC key rejected")
                    return
                raise AssertionError("Invalid credentials unexpectedly accepted")
            for _ in range(3):
                assert _tcp_probe(port, 10) == args.expected_ip
            succeeded = 0
            for _ in range(args.udp_count):
                _udp_probe(port, 5)
                succeeded += 1
            print(
                json.dumps(
                    {
                        "tcp_https": "3/3",
                        "udp_dns": f"{succeeded}/{args.udp_count}",
                        "tls_verified": True,
                        "egress_ip": args.expected_ip,
                    }
                )
            )
        except BaseException:
            logs = subprocess.check_output(
                ["docker", "logs", "--tail", "5", name],
                stderr=subprocess.STDOUT,
                text=True,
            )
            print("Technical client errors:", logs, file=sys.stderr)
            raise
        finally:
            subprocess.run(
                ["docker", "rm", "-f", name], check=True, stdout=subprocess.DEVNULL
            )


if __name__ == "__main__":
    main()
