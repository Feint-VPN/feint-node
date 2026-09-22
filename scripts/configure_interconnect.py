#!/usr/bin/env python3
"""Write an authenticated Feint interconnect config without exposing its password in argv."""

import argparse
import ipaddress
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")


def tailscale_address(value: str) -> str:
    address = ipaddress.ip_address(value)
    if address not in TAILSCALE_V4:
        raise argparse.ArgumentTypeError(
            "Expected a Tailscale IPv4 address (100.64.0.0/10)"
        )
    return str(address)


def port(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("Port must be between 1 and 65535")
    return number


def read_password(path: Path) -> str:
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError("Password file must not be accessible by group or others")
    password = path.read_text(encoding="utf-8").rstrip("\n")
    if not 16 <= len(password.encode("utf-8")) <= 255 or "\n" in password:
        raise ValueError("SOCKS password must contain 16 to 255 bytes on one line")
    return password


def entry_config(
    address: str, remote_port: int, local_port: int, username: str, password: str
) -> dict:
    return {
        "log": {"level": "warn"},
        "inbounds": [
            {
                "type": "socks",
                "tag": "from-xray",
                "listen": "127.0.0.1",
                "listen_port": local_port,
            }
        ],
        "outbounds": [
            {
                "type": "socks",
                "tag": "to-exit",
                "server": address,
                "server_port": remote_port,
                "version": "5",
                "username": username,
                "password": password,
                "udp_over_tcp": {"enabled": True, "version": 2},
            }
        ],
        "route": {"final": "to-exit"},
    }


def exit_config(address: str, listen_port: int, username: str, password: str) -> dict:
    return {
        "log": {"level": "warn"},
        "inbounds": [
            {
                "type": "socks",
                "tag": "from-entry",
                "listen": address,
                "listen_port": listen_port,
                "users": [{"username": username, "password": password}],
            }
        ],
        "outbounds": [{"type": "direct", "tag": "direct"}],
        "route": {
            "rules": [
                {"ip_is_private": True, "action": "reject"},
                {"rule_set": "geoip-ru", "action": "reject"},
            ],
            "rule_set": [
                {
                    "type": "local",
                    "tag": "geoip-ru",
                    "format": "binary",
                    "path": "/opt/sing-box/geoip-ru.srs",
                }
            ],
            "final": "direct",
        },
    }


def write_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as output:
        json.dump(config, output, indent=2)
        output.write("\n")
        pending = Path(output.name)
    try:
        os.chmod(pending, 0o600)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("entry", "exit"))
    parser.add_argument("--tailscale-address", required=True, type=tailscale_address)
    parser.add_argument("--port", required=True, type=port, help="Exit listener port")
    parser.add_argument("--local-port", type=port, help="Entry loopback listener port")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password-file", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path("interconnect.json"))
    args = parser.parse_args()
    if not 1 <= len(args.username.encode("utf-8")) <= 255 or "\n" in args.username:
        parser.error("SOCKS username must contain 1 to 255 bytes on one line")
    if args.role == "entry" and args.local_port is None:
        parser.error("--local-port is required for entry")
    if args.role == "exit" and args.local_port is not None:
        parser.error("--local-port is only valid for entry")
    if args.role == "exit":
        try:
            local_tailscale_ip = subprocess.check_output(
                ["tailscale", "ip", "-4"], text=True
            ).strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            parser.error("Tailscale must be running on the exit node")
        if args.tailscale_address != local_tailscale_ip:
            parser.error(
                "Exit listener must bind to this host's Tailscale IPv4 address"
            )
    password = read_password(args.password_file)
    config = (
        entry_config(
            args.tailscale_address, args.port, args.local_port, args.username, password
        )
        if args.role == "entry"
        else exit_config(args.tailscale_address, args.port, args.username, password)
    )
    write_config(args.output, config)


if __name__ == "__main__":
    main()
