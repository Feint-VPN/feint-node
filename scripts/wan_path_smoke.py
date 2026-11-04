"""Bounded authenticated TCP/UDP echoes on explicitly authorized WAN test hosts."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import secrets
import selectors
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from wan_transport_probe import ssh_command, validate


def worker() -> None:
    target = json.loads(sys.stdin.readline())
    validate(target)
    role = target["role"]
    if role not in ("entry", "exit"):
        raise ValueError("Unknown worker role")
    local = subprocess.check_output(["tailscale", "ip", "-4"], text=True).strip()
    if local != target[f"{role}_address"]:
        raise ValueError("Test-host identity mismatch")
    address = target["entry_public"] if role == "entry" else target["exit_public"]
    peer = target["exit_public"] if role == "entry" else target["entry_public"]
    token = bytes.fromhex(target["token"])
    selector = selectors.DefaultSelector()
    bound = []
    closed = threading.Event()
    interface = "fwp" + target["suffix"]
    created = False

    def watch_control() -> None:
        sys.stdin.readline()
        closed.set()

    threading.Thread(target=watch_control, daemon=True).start()
    try:
        mode = target.get("wan_kernel")
        if mode is not None:
            if mode not in ("gre", "ipip"):
                raise ValueError("Only isolated GRE/IPIP smoke supported")
            network = ipaddress.IPv4Network(target["kernel_network"])
            if network.prefixlen != 30 or not network.subnet_of(
                ipaddress.IPv4Network("198.18.0.0/15")
            ):
                raise ValueError("Use a /30 from the benchmarking range")
            if subprocess.check_output(
                ["ip", "-4", "route", "show", "to", str(network)], text=True
            ).strip():
                raise ValueError("Probe subnet already routed; refusing changes")
            command = [
                "ip",
                "tunnel",
                "add",
                interface,
                "mode",
                mode,
                "local",
                address,
                "remote",
                peer,
                "ttl",
                "64",
            ]
            if mode == "gre":
                command += ["key", str(target["kernel_key"])]
            subprocess.run(command, check=True, capture_output=True)
            created = True
            address = str(network.network_address + (1 if role == "entry" else 2))
            peer = str(network.network_address + (2 if role == "entry" else 1))
            subprocess.run(
                ["ip", "address", "add", f"{address}/30", "dev", interface], check=True
            )
            subprocess.run(
                ["ip", "link", "set", interface, "mtu", "1400", "up"], check=True
            )
            if target.get("kernel_offload") is False:
                subprocess.run(
                    [
                        "ethtool",
                        "-K",
                        interface,
                        "gro",
                        "off",
                        "gso",
                        "off",
                        "tso",
                        "off",
                    ],
                    check=True,
                    capture_output=True,
                )
        for port in (53, 80, 123, 443, 8443, 51897, 54321):
            for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                sock = socket.socket(socket.AF_INET, kind)
                try:
                    sock.bind((address, port))
                    if kind == socket.SOCK_STREAM:
                        sock.listen(8)
                    sock.setblocking(False)
                    selector.register(sock, selectors.EVENT_READ, kind)
                    bound.append([port, "tcp" if kind == socket.SOCK_STREAM else "udp"])
                except OSError:
                    sock.close()
        print(json.dumps({"bound": bound, "address": address}), flush=True)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not closed.is_set():
            for key, _ in selector.select(0.1):
                sock = key.fileobj
                if key.data == socket.SOCK_DGRAM:
                    data, remote = sock.recvfrom(256)
                    if remote[0] == peer and data == token:
                        sock.sendto(data, remote)
                else:
                    connection, remote = sock.accept()
                    with connection:
                        connection.settimeout(0.2)
                        try:
                            if remote[0] == peer and connection.recv(256) == token:
                                connection.sendall(token)
                        except OSError:
                            pass
    finally:
        for key in list(selector.get_map().values()):
            key.fileobj.close()
        selector.close()
        if created:
            subprocess.run(["ip", "tunnel", "del", interface], check=True)


def client() -> None:
    values = json.loads(sys.stdin.readline())
    token = bytes.fromhex(values["token"])

    def check(item: list) -> dict:
        port, protocol = item
        started = time.monotonic()
        with socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM,
        ) as sock:
            sock.settimeout(1.5)
            try:
                sock.connect((values["peer"], port))
                sock.sendall(token)
                valid = sock.recv(256) == token
                return {
                    "port": port,
                    "protocol": protocol,
                    "echo": valid,
                    "rtt_ms": round((time.monotonic() - started) * 1000, 2),
                }
            except OSError as error:
                return {
                    "port": port,
                    "protocol": protocol,
                    "error": type(error).__name__,
                }

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        print(json.dumps(list(pool.map(check, values["ports"]))), flush=True)


def main() -> None:
    if "--worker" in sys.argv:
        worker()
        return
    if "--client" in sys.argv:
        client()
        return
    target = json.load(sys.stdin)
    validate(target)
    if not ipaddress.IPv4Address(target["entry_public"]).is_global:
        raise ValueError("Public entry IPv4 required")
    source = Path(__file__).read_text(encoding="utf-8")
    shared = (
        Path(__file__).with_name("wan_transport_probe.py").read_text(encoding="utf-8")
    )
    # Imports are supplied in memory; no persistent remote files or credentials.
    bootstrap = "import json,sys,types; p=json.loads(sys.stdin.readline()); m=types.ModuleType('wan_transport_probe'); exec(p['shared'],m.__dict__); sys.modules[m.__name__]=m; exec(p['source'])"
    payload = target | {
        "token": secrets.token_hex(32),
        "suffix": secrets.token_hex(4),
        "kernel_key": secrets.randbelow(2**32 - 1) + 1,
    }
    processes = {}
    usable = True
    try:
        ready = {}
        for role in ("exit", "entry"):
            process = subprocess.Popen(
                ssh_command(
                    target, role, f"python3 -u -c {shlex.quote(bootstrap)} --worker"
                ),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            processes[role] = process
            process.stdin.write(
                json.dumps({"shared": shared, "source": source})
                + "\n"
                + json.dumps(payload | {"role": role})
                + "\n"
            )
            process.stdin.flush()
            line = process.stdout.readline()
            if not line:
                raise RuntimeError(f"Test listener failed: {role}")
            ready[role] = json.loads(line)
        if target.get("wan_kernel_transport"):
            if not target.get("wan_kernel"):
                raise ValueError("Kernel transport requires isolated interfaces")
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("wan_transport_probe.py")),
                ],
                input=json.dumps(
                    target | {"wan_connect_address": ready["exit"]["address"]}
                ),
                text=True,
                timeout=65,
            )
            usable = result.returncode == 0
        for role, opposite in (
            ()
            if target.get("wan_kernel_transport")
            else (("entry", "exit"), ("exit", "entry"))
        ):
            result = subprocess.run(
                ssh_command(
                    target, role, f"python3 -u -c {shlex.quote(bootstrap)} --client"
                ),
                input=json.dumps({"shared": shared, "source": source})
                + "\n"
                + json.dumps(
                    {
                        "token": payload["token"],
                        "peer": ready[opposite]["address"],
                        "ports": ready[opposite]["bound"],
                    }
                )
                + "\n",
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode:
                raise RuntimeError(
                    f"Path smoke failed: {role}: {result.stderr[-1000:]}"
                )
            results = json.loads(result.stdout)
            usable &= all(
                any(row.get("echo") and row["protocol"] == protocol for row in results)
                for protocol in ("tcp", "udp")
            )
            print(
                json.dumps({"direction": f"{role}->{opposite}", "results": results}),
                flush=True,
            )
    finally:
        # Independent remote deadline closes sockets even if control is interrupted.
        for process in processes.values():
            process.stdin.close()
        for process in processes.values():
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    if not usable:
        raise SystemExit(
            "No usable bidirectional TCP/UDP path; matrix completed, connectivity failed"
        )


if __name__ == "__main__":
    main()
