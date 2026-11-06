#!/usr/bin/env python3
"""Manage one route's SOCKS/UoT sidecar; never modify the main VPN or Tailscale."""

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

from configure_interconnect import (
    entry_config,
    exit_config,
    tailscale_address,
    write_config,
)


def run(*args: str) -> str:
    return subprocess.check_output(
        args, text=True, stderr=subprocess.PIPE, timeout=120
    ).strip()


def available_port(host: str) -> int:
    with socket.socket() as listener:
        listener.bind((host, 0))
        return listener.getsockname()[1]


def read_peer() -> dict:
    peer = json.loads(sys.stdin.read(4096))
    peer["server"] = tailscale_address(peer["server"])
    if not 1 <= peer["server_port"] <= 65535:
        raise ValueError("Invalid peer port")
    if not 1 <= len(peer["username"].encode()) <= 255:
        raise ValueError("Invalid peer username")
    if not 16 <= len(peer["password"].encode()) <= 255:
        raise ValueError("Invalid peer password")
    return peer


def check_peer(peer: dict) -> None:
    # Authenticate the actual remote endpoint before publishing an entry listener.
    with socket.create_connection(
        (peer["server"], peer["server_port"]), timeout=5
    ) as connection:
        connection.sendall(b"\x05\x01\x02")
        with connection.makefile("rb") as stream:
            if stream.read(2) != b"\x05\x02":
                raise ValueError("Peer does not require SOCKS authentication")
            username, password = peer["username"].encode(), peer["password"].encode()
            connection.sendall(
                b"\x01"
                + bytes([len(username)])
                + username
                + bytes([len(password)])
                + password
            )
            if stream.read(2) != b"\x01\x00":
                raise ValueError("Peer authentication failed")


def inspect(name: str, owner: str) -> dict | None:
    result = subprocess.run(
        ["docker", "inspect", name], capture_output=True, text=True, timeout=15
    )
    if result.returncode:
        if "no such object" in result.stderr.lower():
            return None
        raise RuntimeError("Cannot inspect sidecar")
    container = json.loads(result.stdout)[0]
    if container["Config"]["Labels"].get("io.feint.interconnect") != owner:
        raise RuntimeError("Container belongs to another deployment")
    return container


def manage(root: Path, action: str, route_id: UUID) -> dict | None:
    if os.geteuid() != 0:
        raise RuntimeError("Managed interconnect requires a root SSH account")
    directory = root / ".interconnect"
    directory.mkdir(mode=0o700, exist_ok=True)
    owner = f"{root}:{route_id}"
    name = f"feint-link-{hashlib.sha256(str(root).encode()).hexdigest()[:8]}-{route_id.hex}"
    state_path = directory / f"{route_id}.json"
    config_path = directory / f"{route_id}.config.json"
    # One lock per installation also serializes selection of free host ports.
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        container = inspect(name, owner)
        if action == "remove":
            if container is not None:
                run("docker", "rm", "-f", name)
            state_path.unlink(missing_ok=True)
            config_path.unlink(missing_ok=True)
            return None

        address = tailscale_address(run("tailscale", "ip", "-4"))
        peer = read_peer() if action == "entry" else None
        if peer is not None:
            check_peer(peer)
        previous = json.loads(state_path.read_text()) if state_path.exists() else None
        if previous is not None:
            if (
                previous["role"] != action
                or previous["peer"] != peer
                or previous["address"] != address
            ):
                raise ValueError(
                    "Transport already exists with different settings; remove it explicitly first"
                )
            state = previous
        else:
            if container is not None:
                raise RuntimeError(
                    "Sidecar exists without its state; inspect it before retrying"
                )
            host = "127.0.0.1" if peer is not None else address
            endpoint = {
                "server": host,
                "server_port": available_port(host),
                "username": route_id.hex,
                "password": secrets.token_urlsafe(32),
            }
            state = {
                "role": action,
                "address": address,
                "peer": peer,
                "endpoint": endpoint,
            }

        if state.get("entry_runtime") == "xray":
            if peer is None or state.get("udp_mode") != "native":
                raise ValueError(
                    "Direct Xray entry requires a native authenticated peer"
                )
            # Do not recreate the old sidecar during SDK reconciliation; retain it for drain/rollback.
            return peer

        endpoint = state["endpoint"]
        config = (
            entry_config(
                peer["server"],
                peer["server_port"],
                endpoint["server_port"],
                peer["username"],
                peer["password"],
                udp_mode=state.get("udp_mode", "uot"),
            )
            if peer is not None
            else exit_config(
                address,
                endpoint["server_port"],
                endpoint["username"],
                endpoint["password"],
            )
        )
        compose = json.loads(
            run(
                "docker",
                "compose",
                "--project-directory",
                str(root),
                "--env-file",
                str(root / ".env.local"),
                "-f",
                str(root / "deploy/compose/base.yml"),
                "config",
                "--format",
                "json",
            )
        )
        volume = compose["volumes"]["sing-box-data"]["name"]
        run("docker", "volume", "inspect", volume)
        settings = dict(
            line.split("=", 1)
            for line in (root / ".env.local").read_text().splitlines()
            if line and not line.startswith("#") and "=" in line
        )
        image = settings.get("INTERCONNECT_IMAGE") or settings["SINGBOX_IMAGE"]
        if container is None:
            write_config(config_path, config)
            os.chown(config_path, 1000, 1000)
            write_config(state_path, state)
            try:
                run(
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--label",
                    f"io.feint.interconnect={owner}",
                    "--user",
                    "1000:1000",
                    "--network",
                    "host",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    # The image binary carries this file capability; retain its bounding bit.
                    "--cap-add",
                    "NET_BIND_SERVICE",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--restart",
                    "unless-stopped",
                    "-v",
                    f"{config_path}:/etc/feint/interconnect.json:ro",
                    "-v",
                    f"{volume}:/opt/sing-box:ro",
                    image,
                    "run",
                    "-c",
                    "/etc/feint/interconnect.json",
                )
            except Exception:
                # Preserve state/credentials so a bounded retry can reconcile the same route.
                if inspect(name, owner) is not None:
                    run("docker", "rm", "-f", name)
                raise
        elif not container["State"]["Running"]:
            run("docker", "start", name)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(
                    (endpoint["server"], endpoint["server_port"]), timeout=1
                ):
                    current = inspect(name, owner)
                    if current is not None and current["State"]["Running"]:
                        return endpoint
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("Interconnect listener did not become ready")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("entry", "exit", "remove"))
    parser.add_argument("route_id", type=UUID)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[3]
    )
    args = parser.parse_args()
    os.umask(0o077)
    try:
        value = manage(args.root.resolve(), args.action, args.route_id)
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        # Never echo command output, peer credentials or configuration on failure.
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f"Managed interconnect failed: {detail}", file=sys.stderr)
        raise SystemExit(1) from None
    if value is not None:
        print(json.dumps(value))


if __name__ == "__main__":
    main()
