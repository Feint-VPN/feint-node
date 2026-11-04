"""Disposable test-host WAN transports; reuse the existing bounded TCP/UDP fixture."""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import secrets
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

SOURCES = ("acceptance/interconnect_load.py", "acceptance/interconnect.py")
VARIANTS = {"ss2022", "hy2", "uot", "native", "icmp"}
TAILNET = ipaddress.ip_network("100.64.0.0/10")


def validate(target: dict) -> None:
    if target.get("test_only") is not True or target.get("load_authorized") is not True:
        raise ValueError("WAN load requires explicit test-only and load authorization")
    rates = target.get("wan_rates", [30])
    seconds = target.get("transport_seconds", 5)
    variants = target.get("wan_variants", ["ss2022"])
    if (
        not isinstance(rates, list)
        or not 1 <= len(rates) <= 4
        or any(
            type(rate) is not int or rate not in (10, 30, 100, 300, 600, 1000)
            for rate in rates
        )
        or type(seconds) is not int
        or not 3 <= seconds <= 10
    ):
        raise ValueError("Bounded test requires 1..4 supported rates and 3..10 seconds")
    if (
        not isinstance(variants, list)
        or not 1 <= len(variants) <= 4
        or any(variant not in VARIANTS for variant in variants)
        or len(set(variants)) != len(variants)
    ):
        raise ValueError("Select distinct supported transports")
    if sum(rates) * seconds * 125000 * len(variants) > 3_000_000_000:
        raise ValueError("Run exceeds the 3 GB generated-payload budget")
    port = target.get("wan_port")
    if port is not None and (
        type(port) is not int or (port != 443 and not 1024 <= port <= 65535)
    ):
        raise ValueError("Test port must be 443 or 1024..65535")
    for key in ("entry_address", "exit_address"):
        if ipaddress.ip_address(target[key]) not in TAILNET:
            raise ValueError("Control identities require Tailscale IPv4")
    if target["entry_address"] == target["exit_address"]:
        raise ValueError("WAN test needs distinct hosts")
    buffers = target.get("udp_buffer_bytes")
    if buffers is not None and (
        type(buffers) is not int or not 212992 <= buffers <= 16 * 1024 * 1024
    ):
        raise ValueError("Test UDP buffer ceiling must be 208 KiB..16 MiB")
    defaults = target.get("udp_buffer_defaults", False)
    if type(defaults) is not bool or (defaults and buffers is None):
        raise ValueError(
            "Changing test buffer defaults requires an explicit buffer size"
        )
    if not ipaddress.IPv4Address(target["exit_public"]).is_global:
        raise ValueError("Direct listener needs the test exit public IPv4")
    if target.get("wan_connect_address") is not None and (
        target.get("wan_kernel") not in ("gre", "ipip")
        or ipaddress.IPv4Address(target["wan_connect_address"])
        not in ipaddress.IPv4Network("198.18.0.0/15")
    ):
        raise ValueError(
            "Underlay override requires an isolated benchmark-range kernel tunnel"
        )
    ingress = target.get("wan_ingress")
    if ingress is not None and (ingress != "hy2" or "icmp" in variants):
        raise ValueError(
            "Only an isolated HY2 ingress with sing-box transport is supported"
        )
    images = ["singbox_image"]
    if "icmp" in variants:
        images.append("gost_image")
    if ingress:
        images.append("xray_image")
    for field in images:
        image = target.get(field, "")
        digest = image.rsplit("@sha256:", 1)[-1]
        if (
            "@sha256:" not in image
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("Test image must be digest-pinned")


def icmp_config(role: str, payload: dict, port: int) -> dict:
    """QUIC over ICMP: pinned TLS, temporary auth, fixture-only destination."""
    auth = {"username": "canary", "password": payload["password"]}
    if role == "exit":
        return {
            "services": [
                {
                    "name": "fixture",
                    "addr": f"{payload['exit_public']}:0",
                    "bypass": "fixture-only",
                    "handler": {"type": "relay", "auth": auth},
                    "forwarder": {
                        "nodes": [{"name": "sink", "addr": f"127.0.0.1:{port + 1}"}]
                    },
                    "listener": {
                        "type": "icmp",
                        "tls": {
                            "certFile": "/fixture/cert.pem",
                            "keyFile": "/fixture/key.pem",
                        },
                    },
                }
            ],
            "bypasses": [
                {
                    "name": "fixture-only",
                    "whitelist": True,
                    "matchers": [f"127.0.0.1:{port + 1}"],
                }
            ],
            "log": {"level": "error"},
        }
    return {
        "services": [
            {
                "name": network,
                "addr": f"127.0.0.1:{port + 1}",
                "handler": {"type": network, "chain": "icmp"},
                "listener": {"type": network},
                "forwarder": {
                    "nodes": [{"name": "sink", "addr": f"127.0.0.1:{port + 1}"}]
                },
            }
            for network in ("tcp", "udp")
        ],
        "chains": [
            {
                "name": "icmp",
                "hops": [
                    {
                        "name": "exit",
                        "nodes": [
                            {
                                "name": "exit",
                                "addr": f"{payload['exit_public']}:{port}",
                                "connector": {"type": "relay", "auth": auth},
                                "dialer": {
                                    "type": "icmp",
                                    "tls": {
                                        "secure": True,
                                        "serverName": "wan.feint.test",
                                        "caFile": "/fixture/cert.pem",
                                    },
                                    "metadata": {"keepAlive": True, "ttl": "10s"},
                                },
                            }
                        ],
                    }
                ],
            }
        ],
        "log": {"level": "error"},
    }


def smoke_passed(result: dict) -> bool:
    return (
        result.get("download_integrity") is True
        and not result.get("errors")
        and not result.get("download_thread_alive")
        and result.get("udp_sent", 0) > result.get("udp_lost", 0)
    )


def check_ports(address: str, ports: tuple[int, ...]) -> None:
    for port in ports:
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with socket.socket(socket.AF_INET, kind) as check:
                check.bind((address, port))


def worker() -> None:
    payload = json.loads(sys.stdin.readline())
    validate(payload)
    role = payload["role"]
    if role not in ("entry", "exit"):
        raise ValueError("Unexpected role")
    local = subprocess.check_output(["tailscale", "ip", "-4"], text=True).strip()
    if local != payload[f"{role}_address"]:
        raise ValueError("Test-host identity mismatch")
    os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"
    os.environ.pop("DOCKER_CONTEXT", None)
    name = f"feint-wan-{payload['suffix']}-{role}"
    ingress = role == "entry" and payload.get("wan_ingress") == "hy2"
    names = [name, name + "-ingress", name + "-client"] if ingress else [name]
    sink = None
    original_buffers = {}

    def docker(*args: str) -> str:
        result = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=25
        )
        if result.returncode:
            raise RuntimeError(f"Test docker operation failed: {args[0]}")
        return result.stdout

    def cleanup() -> None:
        try:
            subprocess.run(
                ["docker", "rm", "-f", *names], capture_output=True, timeout=15
            )
            if sink is not None and sink.poll() is None:
                sink.terminate()
                sink.wait(timeout=5)
        finally:
            for key, value in reversed(original_buffers.items()):
                subprocess.run(
                    ["sysctl", "-w", f"{key}={value}"],
                    check=True,
                    capture_output=True,
                    timeout=5,
                )

    with tempfile.TemporaryDirectory(prefix="feint-wan-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)

        def start(child: str, core: str, image: str, filename: str) -> None:
            docker(
                "run",
                "-d",
                "--pull=never",
                "--name",
                child,
                "--network",
                "host",
                "--user",
                "0:0",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "NET_BIND_SERVICE",
                *(["--cap-add", "NET_RAW"] if core == "gost" else []),
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "256m",
                "--pids-limit",
                "64",
                "--mount",
                f"type=bind,src={root},dst=/fixture,readonly",
                "--entrypoint",
                core,
                image,
                *(["-C"] if core == "gost" else ["run", "-c"]),
                f"/fixture/{filename}",
            )
            time.sleep(0.3)
            if (
                docker("inspect", "--format", "{{.State.Running}}", child).strip()
                != "true"
            ):
                subprocess.run(
                    ["docker", "logs", "--tail", "8", child],
                    stdout=sys.stderr,
                    stderr=sys.stderr,
                    timeout=10,
                )
                raise RuntimeError("Canary transport failed startup")

        for filename in SOURCES:
            source = payload["files"][filename]
            if (
                hashlib.sha256(source["content"].encode()).hexdigest()
                != source["sha256"]
            ):
                raise ValueError("Source checksum mismatch")
            (root / Path(filename).name).write_text(source["content"], encoding="utf-8")
        sys.path.insert(0, str(root))
        from interconnect_load import hy2_config, probe, resource_snapshot

        docker("image", "inspect", payload["singbox_image"])
        if ingress:
            docker("image", "inspect", payload["xray_image"])
        if "icmp" in payload["wan_variants"]:
            docker("image", "inspect", payload["gost_image"])
        available = next(
            line.split()[1]
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
        if int(available) < 256 * 1024:
            raise RuntimeError("Insufficient memory headroom on the test host")
        port = payload["port"]
        public_port = payload.get("wan_port", port)
        transport_address = payload.get("wan_connect_address", payload["exit_public"])
        check_ports("127.0.0.1", (port + (1 if role == "exit" else 2),))
        if ingress:
            check_ports("127.0.0.1", (port + 3, port + 4))
        if role == "entry" and "icmp" in payload["wan_variants"]:
            check_ports("127.0.0.1", (port + 1,))
        if role == "exit":
            check_ports(transport_address, (public_port,))
            check_ports(payload["exit_address"], (port,))
        token = bytes.fromhex(payload["token"])

        def expire() -> None:
            try:
                cleanup()
            finally:
                os._exit(124)

        lease = threading.Timer(420, expire)
        lease.daemon = True
        lease.start()
        try:
            if payload.get("udp_buffer_bytes") is not None:
                keys = ["net.core.rmem_max", "net.core.wmem_max"]
                if payload.get("udp_buffer_defaults"):
                    keys += ["net.core.rmem_default", "net.core.wmem_default"]
                for key in keys:
                    original_buffers[key] = subprocess.check_output(
                        ["sysctl", "-n", key], text=True, timeout=5
                    ).strip()
                    subprocess.run(
                        ["sysctl", "-w", f"{key}={payload['udp_buffer_bytes']}"],
                        check=True,
                        capture_output=True,
                        timeout=5,
                    )
            certificate = None
            if role == "exit" or ingress:
                tls_root = root / "tls" if ingress else root
                tls_root.mkdir(exist_ok=True)
                result = subprocess.run(
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
                        "/CN=wan.feint.test",
                        "-addext",
                        "subjectAltName=DNS:wan.feint.test",
                        "-keyout",
                        str(tls_root / "key.pem"),
                        "-out",
                        str(tls_root / "cert.pem"),
                    ],
                    capture_output=True,
                    timeout=15,
                )
                if result.returncode:
                    raise RuntimeError("Temporary certificate creation failed")
                certificate = (tls_root / "cert.pem").read_text()
                (tls_root / "key.pem").chmod(0o600)
            if role == "exit":
                (root / "token").write_text(payload["token"])
                (root / "token").chmod(0o600)
                sink = subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        str(root / "interconnect_load.py"),
                        "serve",
                        "--port",
                        str(port + 1),
                        "--token-file",
                        str(root / "token"),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            print(json.dumps({"ready": role, "certificate": certificate}), flush=True)
            active = False
            for line in sys.stdin:
                command = json.loads(line)
                action = command["action"]
                if action == "close":
                    break
                if action == "variant":
                    variant = command["variant"]
                    if variant not in payload["wan_variants"]:
                        raise ValueError("Unapproved variant")
                    if active:
                        docker("rm", "-f", *names)
                    if role == "entry":
                        (root / "cert.pem").write_text(command["certificate"])
                    if variant == "icmp":
                        config = icmp_config(role, payload, port)
                    elif role == "entry":
                        outbound = {
                            "tag": "to-exit",
                            "server": transport_address,
                            "server_port": public_port,
                        }
                        if variant == "ss2022":
                            outbound.update(
                                type="shadowsocks",
                                method="2022-blake3-aes-128-gcm",
                                password=payload["ss_password"],
                            )
                        elif variant == "hy2":
                            outbound.update(
                                type="hysteria2",
                                password=payload["password"],
                                tls={
                                    "enabled": True,
                                    "server_name": "wan.feint.test",
                                    "certificate_path": "/fixture/cert.pem",
                                },
                            )
                        else:
                            outbound.update(
                                type="socks",
                                server=payload["exit_address"],
                                server_port=port,
                                username="canary",
                                password=payload["password"],
                                udp_over_tcp={
                                    "enabled": variant == "uot",
                                    "version": 2,
                                },
                            )
                        config = {
                            "inbounds": [
                                {
                                    "type": "socks",
                                    "listen": "127.0.0.1",
                                    "listen_port": port + 2,
                                }
                            ],
                            "outbounds": [outbound],
                            "route": {"final": "to-exit"},
                        }
                    else:
                        inbound = {
                            "listen": transport_address,
                            "listen_port": public_port,
                        }
                        if variant == "ss2022":
                            inbound.update(
                                type="shadowsocks",
                                method="2022-blake3-aes-128-gcm",
                                password=payload["ss_password"],
                            )
                        elif variant == "hy2":
                            inbound.update(
                                type="hysteria2",
                                users=[
                                    {"name": "canary", "password": payload["password"]}
                                ],
                                tls={
                                    "enabled": True,
                                    "certificate_path": "/fixture/cert.pem",
                                    "key_path": "/fixture/key.pem",
                                },
                            )
                        else:
                            inbound.update(
                                type="socks",
                                listen=payload["exit_address"],
                                listen_port=port,
                                users=[
                                    {
                                        "username": "canary",
                                        "password": payload["password"],
                                    }
                                ],
                            )
                        config = {
                            "inbounds": [inbound],
                            "outbounds": [{"type": "direct", "tag": "direct"}],
                            "route": {
                                "rules": [
                                    {
                                        "ip_cidr": ["127.0.0.1/32"],
                                        "port": [port + 1],
                                        "action": "route",
                                        "outbound": "direct",
                                    },
                                    {"action": "reject"},
                                ]
                            },
                        }
                    config["log"] = {"level": "error"}
                    (root / "config.json").write_text(json.dumps(config))
                    (root / "config.json").chmod(0o600)
                    start(
                        name,
                        "gost" if variant == "icmp" else "sing-box",
                        payload["gost_image"]
                        if variant == "icmp"
                        else payload["singbox_image"],
                        "config.json",
                    )
                    active = True
                    if ingress:
                        proxy = {
                            "protocol": "socks",
                            "settings": {
                                "servers": [{"address": "127.0.0.1", "port": port + 2}]
                            },
                        }
                        xray = hy2_config(proxy, payload["password"])
                        xray["inbounds"][0].update(listen="127.0.0.1", port=port + 3)
                        client = {
                            "log": {"level": "error"},
                            "inbounds": [
                                {
                                    "type": "socks",
                                    "listen": "127.0.0.1",
                                    "listen_port": port + 4,
                                }
                            ],
                            "outbounds": [
                                {
                                    "type": "hysteria2",
                                    "server": "127.0.0.1",
                                    "server_port": port + 3,
                                    "password": payload["password"],
                                    "tls": {
                                        "enabled": True,
                                        "server_name": "wan.feint.test",
                                        "alpn": ["h3"],
                                        "certificate_path": "/fixture/tls/cert.pem",
                                    },
                                }
                            ],
                        }
                        for suffix, data, image, core in (
                            ("ingress", xray, payload["xray_image"], "xray"),
                            ("client", client, payload["singbox_image"], "sing-box"),
                        ):
                            (root / f"{suffix}.json").write_text(json.dumps(data))
                            (root / f"{suffix}.json").chmod(0o600)
                            start(name + "-" + suffix, core, image, f"{suffix}.json")
                    result = {"variant": variant}
                elif action == "resources":
                    containers = {role: name} if active else {}
                    if active and ingress:
                        containers.update(
                            {
                                suffix: name + "-" + suffix
                                for suffix in ("ingress", "client")
                            }
                        )
                    result = resource_snapshot(containers)
                    lines = Path("/proc/net/snmp").read_text().splitlines()
                    for protocol in ("Tcp", "Udp"):
                        index = next(
                            i
                            for i, line in enumerate(lines)
                            if line.startswith(protocol + ":")
                        )
                        result[protocol.lower()] = dict(
                            zip(
                                lines[index].split()[1:],
                                map(int, lines[index + 1].split()[1:]),
                                strict=True,
                            )
                        )
                elif action == "diagnostics":
                    result = {
                        "transport_errors": subprocess.check_output(
                            ["docker", "logs", "--tail", "8", name],
                            stderr=subprocess.STDOUT,
                            text=True,
                            timeout=10,
                        ).splitlines()
                    }
                elif action == "probe" and role == "entry":
                    if command["rate"] not in payload["wan_rates"]:
                        raise ValueError("Unapproved test rate")
                    trace = []
                    stop_trace = threading.Event()

                    def trace_connections(
                        trace: list[str], stop_trace: threading.Event
                    ) -> None:
                        while not stop_trace.is_set() and len(trace) < 25:
                            sampled = subprocess.run(
                                ["ss", "-tin", "dst", transport_address],
                                capture_output=True,
                                text=True,
                                timeout=3,
                            )
                            if len(sampled.stdout.splitlines()) > 1:
                                trace.append(sampled.stdout[:5000])
                            stop_trace.wait(1)

                    observer = threading.Thread(
                        target=trace_connections, args=(trace, stop_trace), daemon=True
                    )
                    observer.start()
                    try:
                        result = probe(
                            {
                                "server": "127.0.0.1",
                                "server_port": port + (4 if ingress else 2),
                                "direct": variant == "icmp",
                            },
                            port + 1,
                            token,
                            command["rate"],
                            payload["transport_seconds"],
                        )
                    except (OSError, ConnectionError) as error:
                        result = {
                            "requested_mbps": command["rate"],
                            "errors": [type(error).__name__],
                            "download_integrity": False,
                        }
                    finally:
                        stop_trace.set()
                        observer.join(4)
                    result["tcp_trace"] = trace
                else:
                    raise ValueError("Unexpected worker operation")
                print(json.dumps(result), flush=True)
        finally:
            lease.cancel()
            cleanup()


def ssh_command(target: dict, role: str, remote: str) -> list[str]:
    host = target["entry_ssh"] if role == "entry" else target["ssh"]
    if target.get("gateway") and host != target["gateway"]:
        remote = f"tailscale ssh {shlex.quote(host)} {shlex.quote(remote)}"
        host = target["gateway"]
    return ["tailscale", "ssh", host, remote]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.worker:
        worker()
        return
    target = json.load(sys.stdin)
    validate(target)
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "acceptance"))
    from interconnect_load import resource_delta

    source = Path(__file__).read_text(encoding="utf-8")
    files = {}
    for filename in SOURCES:
        content = (root / filename).read_text(encoding="utf-8")
        files[filename] = {
            "content": content,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
    payload = target | {
        "files": files,
        "suffix": secrets.token_hex(5),
        "port": 42000 + secrets.randbelow(11000),
        "password": secrets.token_urlsafe(24),
        "ss_password": base64.b64encode(secrets.token_bytes(16)).decode(),
        "token": secrets.token_hex(32),
    }
    processes = {}

    def request(role: str, action: str, **fields) -> dict:
        process = processes[role]
        process.stdin.write(json.dumps({"action": action, **fields}) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            raise RuntimeError(f"WAN worker disconnected: {role}")
        return json.loads(line)

    failed = False
    try:
        certificate = None
        for role in ("exit", "entry"):
            bootstrap = (
                "import json,sys; exec(json.loads(sys.stdin.readline())['source'])"
            )
            argv = ssh_command(
                target, role, f"python3 -u -c {shlex.quote(bootstrap)} --worker"
            )
            process = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
            )
            processes[role] = process
            process.stdin.write(
                json.dumps({"source": source})
                + "\n"
                + json.dumps(payload | {"role": role})
                + "\n"
            )
            process.stdin.flush()
            line = process.stdout.readline()
            if not line:
                raise RuntimeError(f"WAN worker did not start: {role}")
            ready = json.loads(line)
            if role == "exit":
                certificate = ready.get("certificate")
        for variant in target["wan_variants"]:
            for role in processes:
                request(role, "variant", variant=variant, certificate=certificate)
            for rate in target["wan_rates"]:
                before = {role: request(role, "resources") for role in processes}
                started = time.monotonic()
                result = request("entry", "probe", rate=rate)
                elapsed = time.monotonic() - started
                after = {role: request(role, "resources") for role in processes}
                result.update(
                    variant=variant,
                    client_ingress="loopback-HY2-Xray-not-public-client-path"
                    if target.get("wan_ingress") == "hy2"
                    else "loopback-port-forward-not-HY2"
                    if variant == "icmp"
                    else "loopback-socks-not-HY2",
                    resources={
                        role: resource_delta(before[role], after[role], elapsed)
                        for role in processes
                    },
                )
                result["connectivity_passed"] = smoke_passed(result)
                result["tcp_counters"] = {
                    role: {
                        key: after[role]["tcp"][key] - before[role]["tcp"][key]
                        for key in ("InSegs", "OutSegs", "RetransSegs", "InErrs")
                    }
                    for role in processes
                }
                result["udp_counters"] = {
                    role: {
                        key: after[role]["udp"][key] - before[role]["udp"][key]
                        for key in ("InErrors", "RcvbufErrors", "SndbufErrors")
                    }
                    for role in processes
                }
                if not result["connectivity_passed"]:
                    result["diagnostics"] = {
                        role: request(role, "diagnostics") for role in processes
                    }
                failed |= not result["connectivity_passed"]
                print(json.dumps(result), flush=True)
                if result.get("download_thread_alive"):
                    raise SystemExit(
                        "Download did not drain; abort remaining samples and clean up"
                    )
    finally:
        for process in processes.values():
            try:
                process.stdin.close()
                process.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=5)
    if failed:
        raise SystemExit(
            "One or more transports failed TCP/UDP connectivity; inspect the measurements"
        )


if __name__ == "__main__":
    main()
