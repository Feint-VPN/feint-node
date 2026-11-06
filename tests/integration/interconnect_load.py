"""Bounded TCP download and authenticated UDP echo for private transport canaries."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import secrets
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from interconnect import docker, recv_exact

BLOCK = b"feint-transport-canary\n" * 2048
MAX_BYTES = 1_250_000_000
RATES = (10, 30, 60, 100, 300, 600, 900, 1000)


def serve(port: int, token: bytes) -> None:
    def download(connection: socket.socket) -> None:
        with connection:
            connection.settimeout(10)
            if recv_exact(connection, 32) != token:
                return
            size, mbps = struct.unpack("!II", recv_exact(connection, 8))
            if not 0 < size <= MAX_BYTES or mbps not in RATES:
                return
            started = time.monotonic()
            sent = 0
            while sent < size:
                chunk = BLOCK[: min(len(BLOCK), size - sent)]
                connection.sendall(chunk)
                sent += len(chunk)
                time.sleep(
                    max(0, sent * 8 / (mbps * 1e6) - (time.monotonic() - started))
                )

    def tcp() -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
            listener.listen(2)
            while True:
                connection, _ = listener.accept()
                with contextlib.suppress(OSError, ConnectionError):
                    download(connection)

    threading.Thread(target=tcp, daemon=True).start()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.bind(("127.0.0.1", port))
        while True:
            data, address = udp.recvfrom(2048)
            if len(data) == 32 + 4 + 160 and secrets.compare_digest(data[:32], token):
                udp.sendto(data, address)


def socks(
    peer: dict, command: int, target: str, port: int
) -> tuple[socket.socket, str, int]:
    connection = socket.create_connection(
        (peer["server"], peer["server_port"]), timeout=5
    )
    username, password = peer.get("username", ""), peer.get("password", "")
    method = 2 if username else 0
    connection.sendall(bytes((5, 1, method)))
    if recv_exact(connection, 2) != bytes((5, method)):
        connection.close()
        raise ConnectionError("SOCKS method rejected")
    if username:
        u, p = username.encode(), password.encode()
        connection.sendall(b"\x01" + bytes((len(u),)) + u + bytes((len(p),)) + p)
        if recv_exact(connection, 2) != b"\x01\x00":
            connection.close()
            raise ConnectionError("SOCKS authentication rejected")
    connection.sendall(
        bytes((5, command, 0, 1)) + socket.inet_aton(target) + struct.pack("!H", port)
    )
    header = recv_exact(connection, 4)
    if header[:2] != b"\x05\x00":
        connection.close()
        raise ConnectionError("SOCKS request rejected")
    if header[3] == 1:
        address = socket.inet_ntoa(recv_exact(connection, 4))
    elif header[3] == 3:
        address = recv_exact(connection, recv_exact(connection, 1)[0]).decode()
    else:
        connection.close()
        raise ConnectionError("Expected IPv4 SOCKS relay")
    return connection, address, struct.unpack("!H", recv_exact(connection, 2))[0]


def probe(
    peer: dict,
    port: int,
    token: bytes,
    mbps: int,
    seconds: int,
    udp_peer: dict | None = None,
    connections: int = 0,
) -> dict:
    direct = peer.get("direct", False)
    if direct:
        control, relay, relay_port = contextlib.nullcontext(), peer["server"], port
    else:
        control, relay, relay_port = socks(udp_peer or peer, 3, "0.0.0.0", 0)
    if relay == "0.0.0.0":
        relay = (udp_peer or peer)["server"]
    latencies: list[float] = []
    failures: list[str] = []
    transferred = [0]
    digest = hashlib.sha256()
    size = min(MAX_BYTES, mbps * 1_000_000 * seconds // 8)
    bulk_started = [0.0]
    bulk_elapsed = [0.0]
    tcp_gaps: list[float] = []

    def download() -> None:
        try:
            for _ in range(connections):
                with socks(peer, 1, "127.0.0.1", port)[0] as short:
                    short.sendall(token + struct.pack("!II", 64, mbps))
                    if recv_exact(short, 64) != BLOCK[:64]:
                        raise ConnectionError("Short connection payload mismatch")
            connection = (
                socket.create_connection((peer["server"], port), timeout=5)
                if direct
                else socks(peer, 1, "127.0.0.1", port)[0]
            )
            with connection:
                connection.settimeout(10)
                bulk_started[0] = time.monotonic()
                connection.sendall(token + struct.pack("!II", size, mbps))
                previous = time.monotonic()
                while transferred[0] < size:
                    chunk = connection.recv(min(65536, size - transferred[0]))
                    if not chunk:
                        break
                    now = time.monotonic()
                    tcp_gaps.append((now - previous) * 1000)
                    previous = now
                    digest.update(chunk)
                    transferred[0] += len(chunk)
                bulk_elapsed[0] = time.monotonic() - bulk_started[0]
        except (OSError, ConnectionError) as error:
            failures.append(f"{type(error).__name__}: {error}")
        finally:
            if bulk_started[0]:
                bulk_elapsed[0] = time.monotonic() - bulk_started[0]

    thread = threading.Thread(target=download, daemon=True)
    thread.start()
    losses = 0
    sent = 0
    timestamps: dict[int, float] = {}
    replies: dict[int, float] = {}
    gaps: list[float] = []
    stopped = threading.Event()
    with control, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.connect((relay, relay_port))
        udp.settimeout(0.1)
        header = (
            b""
            if direct
            else (
                b"\x00\x00\x00\x01"
                + socket.inet_aton("127.0.0.1")
                + struct.pack("!H", port)
            )
        )
        started_probe = time.monotonic()
        minimum = started_probe + seconds + 1
        deadline = started_probe + seconds + 15

        def receive() -> None:
            previous = time.monotonic()
            while not stopped.is_set():
                try:
                    result = udp.recv(2048)
                except TimeoutError:
                    continue
                except OSError as error:
                    failures.append(type(error).__name__)
                    return
                if (
                    not result.startswith(header + token)
                    or len(result) != len(header) + 196
                ):
                    continue
                sequence = struct.unpack(
                    "!I", result[len(header) + 32 : len(header) + 36]
                )[0]
                if sequence not in timestamps or sequence in replies:
                    continue
                now = time.monotonic()
                replies[sequence] = now
                latencies.append((now - timestamps[sequence]) * 1000)
                gaps.append((now - previous) * 1000)
                previous = now

        receiver = threading.Thread(target=receive, daemon=True)
        receiver.start()
        while time.monotonic() < deadline and (
            time.monotonic() < minimum or thread.is_alive()
        ):
            payload = token + struct.pack("!I", sent) + bytes(160)
            timestamps[sent] = time.monotonic()
            try:
                udp.send(header + payload)
            except OSError as error:
                failures.append(type(error).__name__)
                break
            sent += 1
            time.sleep(max(0, started_probe + sent * 0.02 - time.monotonic()))
        finished_sending = time.monotonic()
        time.sleep(0.5)
        stopped.set()
        receiver.join(1)
        losses = sent - len(replies)
        gaps.append(
            max(0, finished_sending - max(replies.values(), default=started_probe))
            * 1000
        )
    thread.join(12)
    expected = hashlib.sha256()
    for offset in range(0, size, len(BLOCK)):
        expected.update(BLOCK[: min(len(BLOCK), size - offset)])
    ordered = sorted(latencies)
    return {
        "requested_mbps": mbps,
        "short_connections": connections,
        "received_bytes": transferred[0],
        "download_seconds": round(bulk_elapsed[0], 3),
        "download_integrity": transferred[0] == size
        and digest.digest() == expected.digest(),
        "download_mbps": round(transferred[0] * 8 / bulk_elapsed[0] / 1e6, 2)
        if bulk_elapsed[0]
        else 0,
        "tcp_gap_max_ms": round(max(tcp_gaps, default=0), 2),
        "tcp_stalls_over_500ms": sum(gap > 500 for gap in tcp_gaps),
        "udp_sent": sent,
        "udp_lost": losses,
        "udp_rtt_median_ms": round(statistics.median(latencies), 2)
        if latencies
        else None,
        "udp_rtt_p95_ms": round(
            ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2
        )
        if ordered
        else None,
        "udp_rtt_max_ms": round(max(latencies), 2) if latencies else None,
        "udp_reply_gap_max_ms": round(max(gaps, default=0), 2),
        "errors": failures,
        "download_thread_alive": thread.is_alive(),
    }


def resource_snapshot(
    names: dict[str, str],
    proc: Path = Path("/proc"),
    group_root: Path = Path("/sys/fs/cgroup"),
) -> dict:
    """Finite snapshots, including host/Tailscale costs omitted by container CPU."""
    if sys.platform != "linux":
        return {"containers": {}, "host_ticks": [], "tailscale": {}}
    host = [
        int(value) for value in (proc / "stat").read_text().splitlines()[0].split()[1:9]
    ]
    memory = dict(
        line.split(":", 1) for line in (proc / "meminfo").read_text().splitlines()
    )
    tailscale = {}
    for path in proc.glob("[0-9]*/stat"):
        try:
            text = path.read_text()
            if text.split("(", 1)[1].rsplit(")", 1)[0] != "tailscaled":
                continue
            fields = text.rsplit(")", 1)[1].split()
            # PID plus start time prevents attributing a reused PID to the old daemon.
            tailscale[f"{path.parent.name}:{fields[19]}"] = {
                "ticks": int(fields[11]) + int(fields[12]),
                "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
            }
        except (FileNotFoundError, ProcessLookupError):
            continue
    containers = {}
    for role, name in names.items():
        pid = subprocess.check_output(
            ["docker", "inspect", "--format", "{{.State.Pid}}", name], text=True
        ).strip()
        group = (proc / pid / "cgroup").read_text().strip()
        if not group.startswith("0::/"):
            continue
        directory = group_root / group[4:]
        fields = dict(
            line.split() for line in (directory / "cpu.stat").read_text().splitlines()
        )
        containers[role] = {
            "usage_usec": int(fields["usage_usec"]),
            "throttled_usec": int(fields.get("throttled_usec", 0)),
            "nr_throttled": int(fields.get("nr_throttled", 0)),
            "memory_bytes": int((directory / "memory.current").read_text()),
            "memory_peak_bytes": int((directory / "memory.peak").read_text())
            if (directory / "memory.peak").exists()
            else None,
        }
    return {
        "containers": containers,
        "host_ticks": host,
        "host_available_bytes": int(memory["MemAvailable"].split()[0]) * 1024,
        "tailscale": tailscale,
        "clock_ticks": os.sysconf("SC_CLK_TCK"),
    }


def resource_delta(before: dict, after: dict, seconds: float) -> dict:
    ticks = [
        new - old
        for old, new in zip(before["host_ticks"], after["host_ticks"], strict=True)
    ]
    total = sum(ticks)
    same = before["tailscale"].keys() & after["tailscale"].keys()
    return {
        "host_cpu_percent": round((total - ticks[3] - ticks[4]) / total * 100, 2)
        if total > 0
        else None,
        "host_softirq_percent_one_core": round(
            ticks[6] / after["clock_ticks"] / seconds * 100, 2
        )
        if ticks
        else None,
        "host_steal_percent": round(ticks[7] / total * 100, 2) if total > 0 else None,
        "host_available_bytes_after": after.get("host_available_bytes"),
        "tailscale_cpu_percent_one_core": round(
            sum(
                after["tailscale"][pid]["ticks"] - before["tailscale"][pid]["ticks"]
                for pid in same
            )
            / after["clock_ticks"]
            / seconds
            * 100,
            2,
        )
        if same
        else None,
        "tailscale_process_set_changed": before["tailscale"].keys()
        != after["tailscale"].keys(),
        "tailscale_rss_bytes_after": sum(
            value["rss_bytes"] for value in after["tailscale"].values()
        ),
        "containers": {
            name: {
                "cpu_percent_one_core": round(
                    (value["usage_usec"] - before["containers"][name]["usage_usec"])
                    / seconds
                    / 10000,
                    2,
                ),
                "throttled_seconds": round(
                    (
                        value["throttled_usec"]
                        - before["containers"][name]["throttled_usec"]
                    )
                    / 1e6,
                    3,
                ),
                "throttled_periods": value["nr_throttled"]
                - before["containers"][name]["nr_throttled"],
                "memory_bytes_after": value["memory_bytes"],
                "memory_peak_bytes": value["memory_peak_bytes"],
            }
            for name, value in after["containers"].items()
            if name in before["containers"]
        },
    }


def hy2_config(proxy: dict, password: str) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "protocol": "hysteria",
                "listen": "0.0.0.0",
                "port": 39084,
                "settings": {
                    "version": 2,
                    "clients": [{"auth": password, "email": "isolated-canary"}],
                },
                "streamSettings": {
                    "network": "hysteria",
                    "security": "tls",
                    "hysteriaSettings": {"version": 2},
                    "tlsSettings": {
                        "alpn": ["h3"],
                        "certificates": [
                            {
                                "certificateFile": "/fixture/tls/cert.pem",
                                "keyFile": "/fixture/tls/key.pem",
                            }
                        ],
                    },
                },
            }
        ],
        "outbounds": [proxy],
    }


def matrix(
    mbps: int = 30,
    seconds: int = 6,
    hy2: bool = False,
    xray_tuning: bool = False,
) -> None:
    """Compare both proxy chains on one disposable host, not the public WAN."""
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from scripts.lib.interconnect.configure_interconnect import (
        entry_config,
        exit_config,
    )

    suffix = uuid.uuid4().hex[:8]
    network = f"feint-transport-{suffix}"
    probe_name = f"feint-transport-probe-{suffix}"
    profile_name = f"feint-transport-profiler-{suffix}"
    names = {
        role: f"feint-transport-{role}-{suffix}"
        for role in ("exit", "uot", "native", "sink")
    }
    singbox = os.environ.get(
        "SINGBOX_IMAGE", "ghcr.io/feint-vpn/feint-sing-box:v1.13.19-feint.1"
    )
    xray = os.environ.get(
        "XRAY_IMAGE",
        "ghcr.io/xtls/xray-core@sha256:b697cda1588faca696ab7f7755dd1161f60862af3ff6026300e44cff6aedd558",
    )
    token = secrets.token_bytes(32)
    password = secrets.token_urlsafe(24)
    with tempfile.TemporaryDirectory(prefix="feint-transport-") as temporary:
        directory = Path(temporary)
        (directory / "token").write_text(token.hex())
        (directory / "peer.json").write_text(
            json.dumps({"server": "127.0.0.1", "server_port": 39083})
        )
        (directory / "direct.json").write_text(
            json.dumps({"server": "127.0.0.1", "direct": True})
        )
        exit_value = exit_config("0.0.0.0", 39085, "canary", password)
        exit_value["route"] = {"final": "direct"}
        native = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "protocol": "socks",
                    "listen": "0.0.0.0",
                    "port": 39083,
                    "settings": {"udp": True},
                }
            ],
            "outbounds": [
                {
                    "protocol": "socks",
                    "settings": {
                        "servers": [
                            {
                                "address": names["exit"],
                                "port": 39085,
                                "users": [{"user": "canary", "pass": password}],
                            }
                        ]
                    },
                }
            ],
        }
        configs = {
            "exit": exit_value,
            "uot": entry_config(names["exit"], 39085, 39083, "canary", password),
            "native": native,
        }
        if hy2:
            tls = directory / "tls"
            tls.mkdir()
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "ec",
                    "-pkeyopt",
                    "ec_paramgen_curve:P-256",
                    "-nodes",
                    "-days",
                    "1",
                    "-subj",
                    "/CN=feint-hy2-canary",
                    "-addext",
                    "subjectAltName=DNS:feint-hy2-canary",
                    "-keyout",
                    str(tls / "key.pem"),
                    "-out",
                    str(tls / "cert.pem"),
                ],
                capture_output=True,
                check=True,
                timeout=15,
            )
            for path in (directory, tls, tls / "key.pem", tls / "cert.pem"):
                os.chown(path, -1, 65532)
                path.chmod(0o750 if path.is_dir() else 0o640)
            ss_password = base64.b64encode(secrets.token_bytes(16)).decode()
            exit_value["inbounds"].append(
                {
                    "type": "shadowsocks",
                    "listen": "0.0.0.0",
                    "listen_port": 39086,
                    "method": "2022-blake3-aes-128-gcm",
                    "password": ss_password,
                }
            )
            local_socks = {
                "protocol": "socks",
                "settings": {"servers": [{"address": "127.0.0.1", "port": 39083}]},
            }
            proxies = {
                "uot": local_socks,
                "native": native["outbounds"][0],
                "ss2022": {
                    "protocol": "shadowsocks",
                    "settings": {
                        "address": names["exit"],
                        "port": 39086,
                        "method": "2022-blake3-aes-128-gcm",
                        "password": ss_password,
                    },
                },
            }
            if xray_tuning:
                # Keep the accepted UoT path; compare only dispatcher overhead.
                configs["uot"]["inbounds"][0]["listen"] = "0.0.0.0"
                proxy = {
                    "protocol": "socks",
                    "settings": {"servers": [{"address": names["uot"], "port": 39083}]},
                }
                proxies = {"baseline": proxy, "lean": proxy}
            del configs["native"], names["native"]
            for role, proxy in proxies.items():
                key = f"hy2-{role}"
                names[key] = f"feint-transport-{key}-{suffix}"
                configs[key] = hy2_config(proxy, password)
                if xray_tuning:
                    configs[key]["policy"] = {
                        "levels": {
                            "0": {
                                "statsUserUplink": True,
                                "statsUserDownlink": True,
                                "statsUserOnline": role == "baseline",
                            }
                        }
                    }
                    configs[key]["stats"] = {}
                    if role == "lean":
                        configs[key]["log"]["access"] = "none"
                    # Internal test network only, never publish this listener.
                    configs[key]["metrics"] = {"listen": "0.0.0.0:11111"}
            names["client"] = f"feint-transport-client-{suffix}"
        docker("network", "create", "--internal", network)
        profiler = None
        try:
            for role, value in configs.items():
                path = directory / f"{role}.json"
                path.write_text(json.dumps(value))
                docker(
                    "run",
                    "-d",
                    "--name",
                    names[role],
                    "--network",
                    f"container:{names['uot']}" if role == "hy2-uot" else network,
                    "--read-only",
                    "--memory",
                    "192m",
                    "--cpus",
                    "1",
                    "--pids-limit",
                    "64",
                    "--group-add",
                    "65532",
                    "--cap-drop",
                    "ALL",
                    "--cap-add",
                    "NET_BIND_SERVICE",
                    "--mount",
                    f"type=bind,src={path},dst=/config.json,readonly",
                    "--mount",
                    f"type=bind,src={directory},dst=/fixture,readonly",
                    "--entrypoint",
                    "xray"
                    if role == "native" or role.startswith("hy2-")
                    else "sing-box",
                    xray if role == "native" or role.startswith("hy2-") else singbox,
                    "run",
                    "-c",
                    "/config.json",
                )
            mounts = ["--mount", f"type=bind,src={directory},dst=/fixture,readonly"]
            for filename in ("interconnect_load.py", "interconnect.py"):
                mounts += [
                    "--mount",
                    f"type=bind,src={Path(__file__).with_name(filename)},dst=/{filename},readonly",
                ]
            docker(
                "run",
                "-d",
                "--name",
                names["sink"],
                "--network",
                f"container:{names['exit']}",
                *mounts,
                "python:3.11-slim",
                "python",
                "/interconnect_load.py",
                "serve",
                "--port",
                "39089",
                "--token-file",
                "/fixture/token",
            )
            time.sleep(0.5)
            roles = (
                ("baseline", "lean", "lean", "baseline")
                if xray_tuning
                else ("uot", "native", "ss2022")
                if hy2
                else ("direct", "uot", "native")
            )
            for iteration, role in enumerate(roles):
                if hy2:
                    server = names["uot"] if role == "uot" else names[f"hy2-{role}"]
                    client = {
                        "log": {"level": "warn"},
                        "inbounds": [
                            {
                                "type": "socks",
                                "listen": "127.0.0.1",
                                "listen_port": 39083,
                            }
                        ],
                        "outbounds": [
                            {
                                "type": "hysteria2",
                                "server": server,
                                "server_port": 39084,
                                "password": password,
                                "tls": {
                                    "enabled": True,
                                    "server_name": "feint-hy2-canary",
                                    "alpn": ["h3"],
                                    "certificate_path": "/fixture/tls/cert.pem",
                                },
                            }
                        ],
                    }
                    path = directory / "client.json"
                    path.write_text(json.dumps(client))
                    docker(
                        "run",
                        "-d",
                        "--name",
                        names["client"],
                        "--network",
                        network,
                        "--read-only",
                        "--memory",
                        "192m",
                        "--cpus",
                        "1",
                        "--pids-limit",
                        "64",
                        "--cap-drop",
                        "ALL",
                        "--mount",
                        f"type=bind,src={directory},dst=/fixture,readonly",
                        "--entrypoint",
                        "sing-box",
                        singbox,
                        "run",
                        "-c",
                        "/fixture/client.json",
                    )
                    time.sleep(0.4)
                    entry = "client"
                else:
                    entry = "exit" if role == "direct" else role
                status = json.loads(
                    subprocess.check_output(
                        ["docker", "inspect", names[entry]], text=True
                    )
                )[0]
                if not status["State"]["Running"]:
                    print(
                        json.dumps(
                            {
                                "variant": role,
                                "container_state": {
                                    key: status["State"][key]
                                    for key in ("ExitCode", "OOMKilled", "Error")
                                },
                            }
                        ),
                        flush=True,
                    )
                    subprocess.run(
                        ["docker", "logs", "--tail", "8", names[entry]], check=False
                    )
                    raise RuntimeError(f"{role} canary exited before its probe")
                metrics_source = (
                    "import urllib.request,sys; "
                    f"sys.stdout.buffer.write(urllib.request.urlopen('http://{names.get('hy2-' + role)}:11111/"
                )
                metrics_before = {}
                if xray_tuning:
                    metrics_before = json.loads(
                        subprocess.check_output(
                            [
                                "docker",
                                "exec",
                                names["sink"],
                                "python",
                                "-c",
                                metrics_source + "debug/vars',timeout=5).read())",
                            ],
                            timeout=10,
                        )
                    )
                before = resource_snapshot(names)
                log_path = (
                    Path(
                        subprocess.check_output(
                            [
                                "docker",
                                "inspect",
                                "--format",
                                "{{.LogPath}}",
                                names["hy2-" + role],
                            ],
                            text=True,
                        ).strip()
                    )
                    if xray_tuning
                    else None
                )
                log_before = log_path.stat().st_size if log_path else 0
                profiler = ThreadPoolExecutor(max_workers=1) if xray_tuning else None
                profile = (
                    profiler.submit(
                        subprocess.check_output,
                        [
                            "docker",
                            "exec",
                            names["sink"],
                            "python",
                            "-c",
                            metrics_source
                            + f"debug/pprof/profile?seconds={seconds}',timeout={seconds + 5}).read())",
                        ],
                        timeout=seconds + 10,
                    )
                    if profiler
                    else None
                )
                started = time.monotonic()
                result = subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--name",
                        probe_name,
                        "--network",
                        f"container:{names[entry]}",
                        *mounts,
                        "python:3.11-slim",
                        "python",
                        "/interconnect_load.py",
                        "probe",
                        "--port",
                        "39089",
                        "--token-file",
                        "/fixture/token",
                        "--peer-file",
                        "/fixture/direct.json"
                        if role == "direct"
                        else "/fixture/peer.json",
                        "--mbps",
                        str(mbps),
                        "--seconds",
                        str(seconds),
                        "--connections",
                        "200" if xray_tuning else "0",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=seconds + 25,
                )
                elapsed = time.monotonic() - started
                after = resource_snapshot(names)
                tuning = {}
                if profiler is not None:
                    try:
                        profile_path = directory / f"cpu-{iteration}.pb.gz"
                        profile_path.write_bytes(profile.result())
                    finally:
                        profiler.shutdown(wait=True)
                    metrics_after = json.loads(
                        subprocess.check_output(
                            [
                                "docker",
                                "exec",
                                names["sink"],
                                "python",
                                "-c",
                                metrics_source + "debug/vars',timeout=5).read())",
                            ],
                            timeout=10,
                        )
                    )
                    cpu_top = subprocess.check_output(
                        [
                            "docker",
                            "run",
                            "--rm",
                            "--name",
                            profile_name,
                            "--pull=never",
                            "--network",
                            "none",
                            "--read-only",
                            "--cap-drop",
                            "ALL",
                            "--memory",
                            "256m",
                            "--mount",
                            f"type=bind,src={profile_path},dst=/profile.pb.gz,readonly",
                            "--entrypoint",
                            "go",
                            os.environ["PPROF_IMAGE"],
                            "tool",
                            "pprof",
                            "-top",
                            "-nodecount=10",
                            "/profile.pb.gz",
                        ],
                        text=True,
                        timeout=30,
                    )
                    tuning = {
                        "iteration": iteration,
                        "profile_top": cpu_top,
                        "gc_cycles": metrics_after["memstats"]["NumGC"]
                        - metrics_before["memstats"]["NumGC"],
                        "heap_alloc_bytes": metrics_after["memstats"]["HeapAlloc"],
                        "user_counters": {
                            user: {
                                direction: values[direction]
                                - metrics_before["stats"]
                                .get("user", {})
                                .get(user, {})
                                .get(direction, 0)
                                for direction in ("uplink", "downlink")
                            }
                            for user, values in metrics_after["stats"]
                            .get("user", {})
                            .items()
                        },
                        "xray_log_bytes": log_path.stat().st_size - log_before,
                    }
                if result.returncode:
                    raise RuntimeError(
                        f"{role} probe process failed: {result.stderr[-1500:]}"
                    )
                value = json.loads(result.stdout)
                if xray_tuning:
                    resources = resource_delta(before, after, elapsed)
                    cpu = resources["containers"]["hy2-" + role]["cpu_percent_one_core"]
                    tuning["xray_cpu_seconds_per_gib"] = (
                        round(
                            cpu / 100 * elapsed / (value["received_bytes"] / 1024**3), 3
                        )
                        if value["received_bytes"]
                        else None
                    )
                print(
                    json.dumps(
                        {
                            "variant": role,
                            "hy2_ingress": hy2,
                            "host_cpus": os.cpu_count(),
                            "cpu_window_seconds": round(elapsed, 3),
                            "resources": resource_delta(before, after, elapsed),
                            **tuning,
                            **value,
                        }
                    ),
                    flush=True,
                )
                if xray_tuning and (value["errors"] or not value["download_integrity"]):
                    print(
                        subprocess.check_output(
                            ["docker", "logs", "--tail", "12", names["hy2-" + role]],
                            text=True,
                            stderr=subprocess.STDOUT,
                        ),
                        flush=True,
                    )
                assert (
                    value["download_integrity"]
                    and not value["errors"]
                    and not value["download_thread_alive"]
                ), value
                assert value["udp_sent"] >= seconds * 20 and value["udp_lost"] == 0, (
                    value
                )
                if xray_tuning:
                    counters = tuning["user_counters"]["isolated-canary"]
                    assert counters["downlink"] >= value["received_bytes"], counters
                    assert (
                        value["udp_rtt_p95_ms"] < 50
                        and value["udp_reply_gap_max_ms"] < 200
                        and value["tcp_stalls_over_500ms"] == 0
                    ), value
                if hy2:
                    docker("rm", "-f", names["client"])
        finally:
            if profiler is not None:
                profiler.shutdown(wait=True)
            for name in (*names.values(), probe_name, profile_name):
                subprocess.run(
                    ["docker", "rm", "-f", name], capture_output=True, check=False
                )
            docker("network", "rm", network)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("serve", "probe", "matrix"))
    parser.add_argument("--port", type=int)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--peer-file", type=Path)
    parser.add_argument("--udp-peer-file", type=Path)
    parser.add_argument("--mbps", type=int, choices=RATES, default=30)
    parser.add_argument("--seconds", type=int, choices=range(3, 181), default=6)
    parser.add_argument("--hy2", action="store_true")
    parser.add_argument("--xray-tuning", action="store_true")
    parser.add_argument("--connections", type=int, choices=(0, 200), default=0)
    args = parser.parse_args()
    if args.action == "matrix":
        if args.seconds > (60 if args.hy2 else 10) or (args.hy2 and args.mbps > 100):
            parser.error(
                "HY2 matrix is limited to 100 Mbit/s and 60s; bare matrix to 10s"
            )
        if args.xray_tuning and (not args.hy2 or not os.environ.get("PPROF_IMAGE")):
            parser.error("Xray tuning requires --hy2 and a cached PPROF_IMAGE")
        matrix(args.mbps, args.seconds, args.hy2, args.xray_tuning)
        return
    if args.token_file is None or args.port is None:
        parser.error("--token-file and --port are required for serve/probe")
    token = bytes.fromhex(args.token_file.read_text().strip())
    if len(token) != 32 or not 1024 <= args.port <= 65535:
        parser.error("Expected a 32-byte token and an unprivileged port")
    if args.action == "serve":
        serve(args.port, token)
    else:
        if args.peer_file is None:
            parser.error("--peer-file is required for probe")
        print(
            json.dumps(
                probe(
                    json.loads(args.peer_file.read_text()),
                    args.port,
                    token,
                    args.mbps,
                    args.seconds,
                    json.loads(args.udp_peer_file.read_text())
                    if args.udp_peer_file
                    else None,
                    args.connections,
                )
            )
        )


if __name__ == "__main__":
    main()
