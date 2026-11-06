"""Explicit, bounded HY2 RU -> test-exit canary; never reconfigure a live node."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import ipaddress
import json
import os
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TAILNET = ipaddress.ip_network("100.64.0.0/10")
SOURCES = (
    "tests/integration/interconnect_load.py",
    "tests/integration/interconnect.py",
    "scripts/lib/interconnect/configure_interconnect.py",
    "tests/integration/hot_outbound_live.py",
    "services/node/src/adapters/docker_runtime.py",
    "services/node/src/adapters/xray_config.py",
    "services/node/src/adapters/xray_socks.py",
    "services/node/src/adapters/xray_control.py",
    "services/node/src/domain/models.py",
    "services/node/src/domain/ports.py",
    "services/node/src/domain/errors.py",
    "services/node/src/domain/users.py",
    "services/node/src/utils/logging_config.py",
    "services/node/src/utils/settings.py",
)
VARIANTS = ("uot", "native", "ss2022", "native-sidecar")


def validate(value: dict) -> None:
    if value.get("node_image") is not None and not (
        isinstance(value["node_image"], str)
        and value["node_image"].startswith("ghcr.io/feint-vpn/feint-node@sha256:")
        and len(value["node_image"].split("@sha256:")[1]) == 64
    ):
        raise ValueError("Published controller image must be digest-pinned")
    if type(value.get("hot_swap", False)) is not bool:
        raise ValueError("hot_swap must be a boolean")
    if value.get("hot_swap") and (
        value["variant"] not in {"uot", "native"} or value["seconds"] < 24
    ):
        raise ValueError("Hot swap requires UoT/native and at least 24 seconds")
    if type(value.get("ss_over_tailscale", False)) is not bool:
        raise ValueError("ss_over_tailscale must be a boolean")
    if value["variant"] not in VARIANTS:
        raise ValueError("Unsupported chain variant")
    if type(value["mbps"]) is not int or value["mbps"] not in (10, 30, 60, 100):
        raise ValueError("Chain rate limited to 10/30/60/100 Mbit/s")
    if type(value["seconds"]) is not int or not 3 <= value["seconds"] <= 60:
        raise ValueError("Chain duration limited to 3..60 seconds")
    for key in ("entry_address", "exit_address"):
        if ipaddress.ip_address(value[key]) not in TAILNET:
            raise ValueError("HY2 entry and SOCKS exit must use Tailscale IPv4")
    if not ipaddress.IPv4Address(value["exit_public"]).is_global:
        raise ValueError("SS2022 exit requires a public IPv4")
    if not 42000 <= value["port"] <= 55000:
        raise ValueError("Canary port outside reserved test range")


def worker() -> None:
    payload = json.loads(sys.stdin.readline())
    validate(payload)
    role = payload["role"]
    if role not in ("entry", "exit"):
        raise ValueError("Unexpected canary role")
    local = subprocess.check_output(["tailscale", "ip", "-4"], text=True).strip()
    if local != payload[f"{role}_address"]:
        raise ValueError("Listener address is not this host's Tailscale IPv4")
    os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"
    os.environ.pop("DOCKER_CONTEXT", None)
    names = {
        key: f"feint-chain-{payload['suffix']}-{key}"
        for key in (
            "relay",
            "uot",
            "exit",
            "sink",
            "client",
            "voice",
            "probe",
            "controller",
        )
    }

    def run(*args: str) -> str:
        result = subprocess.run(args, capture_output=True, text=True, timeout=90)
        if result.returncode:
            # Configurations contain fresh keys: do not surface Docker stderr.
            raise RuntimeError(f"Canary command failed: {args[0]} {args[1]}")
        return result.stdout

    def cleanup() -> None:
        subprocess.run(
            ["docker", "rm", "-f", *names.values()], capture_output=True, timeout=25
        )

    with tempfile.TemporaryDirectory(prefix="feint-chain-") as temporary:
        root = Path(temporary)
        files = payload["files"]
        if set(files) != set(SOURCES):
            raise ValueError("Unexpected canary sources")
        for name, source in files.items():
            if (
                hashlib.sha256(source["content"].encode()).hexdigest()
                != source["sha256"]
            ):
                raise ValueError("Canary source checksum mismatch")
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source["content"], encoding="utf-8")
        sys.path[:0] = [
            str(root / "tests/integration"),
            str(root / "scripts/lib/interconnect"),
        ]
        from configure_interconnect import entry_config, exit_config
        from interconnect_load import hy2_config, resource_snapshot

        port = payload["port"]
        password = payload["password"]
        ss_address = (
            payload["exit_address"]
            if payload.get("ss_over_tailscale", False)
            else payload["exit_public"]
        )
        method = "2022-blake3-aes-128-gcm"
        cert_mount: list[str] = []
        initial = None
        controller = None
        if role == "entry":
            memory = dict(
                line.split(":", 1)
                for line in Path("/proc/meminfo").read_text().splitlines()
            )
            if int(memory["MemAvailable"].split()[0]) < 400 * 1024:
                raise RuntimeError(
                    "Entry has less than 400 MiB available; canary skipped"
                )
            if os.getloadavg()[0] > (os.cpu_count() or 1) * 1.25:
                raise RuntimeError("Entry is busy; canary skipped")
            initial = json.loads(run("docker", "inspect", "xray"))[0]
            image = initial["Image"]
            api = json.loads(run("docker", "inspect", "vpn-node-api"))[0]
            if payload.get("node_image"):
                run("docker", "image", "inspect", payload["node_image"])
            mounts = api["Mounts"]
            certificate = next(
                m
                for m in mounts
                if m["Destination"] == "/etc/letsencrypt" and m["Type"] == "volume"
            )
            cert_mount = [
                "--mount",
                f"type=volume,src={certificate['Name']},dst=/etc/letsencrypt,readonly",
            ]
        else:
            image = payload["singbox_image"]
        run("docker", "image", "inspect", image)
        singbox_id = run(
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            payload["singbox_image"],
        ).strip()
        if role == "exit":
            run("docker", "image", "inspect", "python:3.11-slim")
        ports = (
            [
                (local, port, socket.SOCK_DGRAM),
                ("127.0.0.1", port + 1, socket.SOCK_STREAM),
                ("127.0.0.1", port + 7, socket.SOCK_STREAM),
            ]
            if role == "entry"
            else [
                (local, port + 2, socket.SOCK_STREAM),
                (local, port + 2, socket.SOCK_DGRAM),
                (ss_address, port + 3, socket.SOCK_STREAM),
                (ss_address, port + 3, socket.SOCK_DGRAM),
                ("127.0.0.1", port + 4, socket.SOCK_STREAM),
                ("127.0.0.1", port + 4, socket.SOCK_DGRAM),
                ("127.0.0.1", port + 5, socket.SOCK_STREAM),
                ("127.0.0.1", port + 6, socket.SOCK_STREAM),
            ]
        )
        for address, number, kind in ports:
            with socket.socket(socket.AF_INET, kind) as check:
                check.bind((address, number))

        def start(
            key: str,
            config: dict,
            binary: str,
            selected: str,
            cpu: str,
            memory: str,
            extra: list[str] | None = None,
        ) -> None:
            path = root / f"{key}.json"
            path.write_text(json.dumps(config))
            path.chmod(0o600)
            run(
                "docker",
                "run",
                "-d",
                "--pull=never",
                "--name",
                names[key],
                "--network",
                "host",
                "--read-only",
                "--user",
                "0:0",
                "--cap-drop",
                "ALL",
                *(["--cap-add", "NET_BIND_SERVICE"] if binary == "xray" else []),
                "--security-opt",
                "no-new-privileges",
                "--memory",
                memory,
                "--cpus",
                cpu,
                "--pids-limit",
                "64",
                "--mount",
                f"type=bind,src={path},dst=/config.json,readonly",
                *(extra or []),
                "--entrypoint",
                binary,
                selected,
                "run",
                "-c",
                "/config.json",
            )
            time.sleep(0.3)
            if (
                run(
                    "docker", "inspect", "--format", "{{.State.Running}}", names[key]
                ).strip()
                != "true"
            ):
                logs = subprocess.run(
                    ["docker", "logs", "--tail", "5", names[key]],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                details = logs.stdout + logs.stderr
                for secret in (
                    password,
                    payload["voice_password"],
                    payload["ss_password"],
                    payload["token"],
                ):
                    details = details.replace(secret, "[redacted]")
                print(
                    json.dumps({"startup_error": key, "detail": details[-700:]}),
                    flush=True,
                )
                raise RuntimeError(f"Canary {key} failed to start")

        def expire() -> None:
            try:
                cleanup()
                shutil.rmtree(root)
            finally:
                os._exit(124)

        lease = threading.Timer(300, expire)
        lease.daemon = True
        lease.start()
        try:
            token_path = root / "token"
            token_path.write_text(payload["token"])
            token_path.chmod(0o600)
            fixture = ["--mount", f"type=bind,src={root},dst=/fixture,readonly"]
            for name in ("interconnect_load.py", "interconnect.py"):
                fixture += [
                    "--mount",
                    f"type=bind,src={root / 'acceptance' / name},dst=/{name},readonly",
                ]
            if role == "exit":
                config = exit_config(local, port + 2, "canary", password)
                if payload["ss_enabled"]:
                    config["inbounds"].append(
                        {
                            "type": "shadowsocks",
                            "listen": ss_address,
                            "listen_port": port + 3,
                            "method": method,
                            "password": payload["ss_password"],
                        }
                    )
                # The public authenticated canary is not a general-purpose proxy.
                config["route"] = {
                    "rules": [
                        {
                            "ip_cidr": ["127.0.0.1/32"],
                            "port": [port + 4],
                            "action": "route",
                            "outbound": "direct",
                        },
                        {"action": "reject"},
                    ]
                }
                start("exit", config, "sing-box", image, "0.5", "192m")
                run(
                    "docker",
                    "run",
                    "-d",
                    "--pull=never",
                    "--name",
                    names["sink"],
                    "--network",
                    "host",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--memory",
                    "64m",
                    "--cpus",
                    "0.15",
                    "--pids-limit",
                    "32",
                    *fixture,
                    "python:3.11-slim",
                    "python",
                    "-B",
                    "/interconnect_load.py",
                    "serve",
                    "--port",
                    str(port + 4),
                    "--token-file",
                    "/fixture/token",
                )
            print(
                json.dumps(
                    {
                        "ready": role,
                        "singbox_image_id": singbox_id,
                        "xray_image_id": image if initial else None,
                        "xray_started": initial["State"]["StartedAt"]
                        if initial
                        else None,
                    }
                ),
                flush=True,
            )
            for line in sys.stdin:
                command = json.loads(line)
                action = command["action"]
                if action == "close":
                    break
                if action == "cpu":
                    active = {
                        k: n
                        for k, n in names.items()
                        if run("docker", "ps", "-q", "--filter", f"name=^{n}$").strip()
                    }
                    result = resource_snapshot(active)
                elif action == "variant":
                    variant = command["variant"]
                    validate(payload | {"variant": variant})
                    if role == "entry":
                        if controller is not None:
                            controller.stdin.write(
                                json.dumps({"variant": variant}) + "\n"
                            )
                            controller.stdin.flush()
                            response = controller.stdout.readline()
                            if not response:
                                raise RuntimeError("Hot-swap controller stopped")
                            print(response.strip(), flush=True)
                            continue
                        subprocess.run(
                            ["docker", "rm", "-f", names["relay"], names["uot"]],
                            capture_output=True,
                            timeout=25,
                        )
                        sidecar = variant in ("uot", "native-sidecar")
                        if sidecar:
                            start(
                                "uot",
                                entry_config(
                                    payload["exit_address"],
                                    port + 2,
                                    port + 1,
                                    "canary",
                                    password,
                                    udp_mode="native"
                                    if variant == "native-sidecar"
                                    else "uot",
                                ),
                                "sing-box",
                                payload["singbox_image"],
                                "0.15",
                                "128m",
                            )
                        server = "127.0.0.1" if sidecar else payload["exit_address"]
                        proxy = {
                            "protocol": "socks",
                            "settings": {
                                "servers": [
                                    {
                                        "address": server,
                                        "port": port + 1 if sidecar else port + 2,
                                        **(
                                            {
                                                "users": [
                                                    {"user": "canary", "pass": password}
                                                ]
                                            }
                                            if not sidecar
                                            else {}
                                        ),
                                    }
                                ]
                            },
                        }
                        if variant == "ss2022":
                            proxy = {
                                "protocol": "shadowsocks",
                                "settings": {
                                    "address": ss_address,
                                    "port": port + 3,
                                    "method": method,
                                    "password": payload["ss_password"],
                                },
                            }
                        config = hy2_config(proxy, password)
                        if payload.get("hot_swap"):
                            proxy["tag"] = "outbound:canary"
                            config["api"] = {
                                "tag": "api",
                                "listen": f"127.0.0.1:{port + 7}",
                                "services": [
                                    "HandlerService",
                                    "RoutingService",
                                    "StatsService",
                                ],
                            }
                            config["policy"] = {
                                "system": {
                                    "statsOutboundUplink": True,
                                    "statsOutboundDownlink": True,
                                }
                            }
                            config["stats"] = {}
                            config["routing"] = {
                                "rules": [
                                    {
                                        "type": "field",
                                        "user": ["isolated-canary", "isolated-voice"],
                                        "outboundTag": proxy["tag"],
                                    }
                                ]
                            }
                        inbound = config["inbounds"][0]
                        inbound["settings"]["clients"].append(
                            {
                                "auth": payload["voice_password"],
                                "email": "isolated-voice",
                            }
                        )
                        inbound.update(listen=local, port=port)
                        inbound["streamSettings"]["tlsSettings"]["certificates"] = [
                            {
                                "certificateFile": f"/etc/letsencrypt/live/{payload['domain']}/fullchain.pem",
                                "keyFile": f"/etc/letsencrypt/live/{payload['domain']}/privkey.pem",
                            }
                        ]
                        start(
                            "relay",
                            config,
                            "xray",
                            image,
                            "0.4",
                            "192m",
                            [
                                *cert_mount,
                                "--mount",
                                f"type=bind,src={root},dst=/fixture,readonly",
                            ],
                        )
                        if payload.get("hot_swap"):
                            controller = subprocess.Popen(
                                [
                                    "docker",
                                    "run",
                                    "--rm",
                                    "-i",
                                    "--pull=never",
                                    "--name",
                                    names["controller"],
                                    "--network",
                                    "host",
                                    "--read-only",
                                    "--user",
                                    "0:0",
                                    "--cap-drop",
                                    "ALL",
                                    "--security-opt",
                                    "no-new-privileges",
                                    "--memory",
                                    "128m",
                                    "--cpus",
                                    "0.15",
                                    "--pids-limit",
                                    "32",
                                    "--mount",
                                    f"type=bind,src={root},dst={root}",
                                    "--mount",
                                    "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
                                    "-e",
                                    "PYTHONPATH=/app/src"
                                    if payload.get("node_image")
                                    else f"PYTHONPATH={root / 'services/node/src'}",
                                    "--entrypoint",
                                    "python",
                                    payload.get("node_image") or api["Image"],
                                    "-u",
                                    "-B",
                                    str(
                                        root / "tests/integration/hot_outbound_live.py"
                                    ),
                                ],
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                text=True,
                            )
                            controller.stdin.write(
                                json.dumps(
                                    {
                                        "container": names["relay"],
                                        "path": str(root / "relay.json"),
                                        "peer": {
                                            "server": payload["exit_address"],
                                            "server_port": port + 2,
                                            "username": "canary",
                                            "password": password,
                                        },
                                    }
                                )
                                + "\n"
                            )
                            controller.stdin.flush()
                            if not controller.stdout.readline():
                                raise RuntimeError(
                                    "Hot-swap controller could not initialize"
                                )
                    else:
                        subprocess.run(
                            ["docker", "rm", "-f", names["client"], names["voice"]],
                            capture_output=True,
                            timeout=25,
                        )
                        config = {
                            "log": {"level": "warn"},
                            "inbounds": [
                                {
                                    "type": "socks",
                                    "listen": "127.0.0.1",
                                    "listen_port": port + 5,
                                }
                            ],
                            "outbounds": [
                                {
                                    "type": "hysteria2",
                                    "server": payload["entry_address"],
                                    "server_port": port,
                                    "password": password,
                                    "tls": {
                                        "enabled": True,
                                        "server_name": payload["domain"],
                                        "alpn": ["h3"],
                                    },
                                }
                            ],
                        }
                        start("client", config, "sing-box", image, "0.3", "192m")
                        config["inbounds"][0]["listen_port"] = port + 6
                        config["outbounds"][0]["password"] = payload["voice_password"]
                        start("voice", config, "sing-box", image, "0.15", "128m")
                    result = {"variant_ready": variant}
                elif action == "probe" and role == "exit":
                    (root / "peer.json").write_text(
                        json.dumps({"server": "127.0.0.1", "server_port": port + 5})
                    )
                    (root / "peer.json").chmod(0o600)
                    (root / "udp-peer.json").write_text(
                        json.dumps({"server": "127.0.0.1", "server_port": port + 6})
                    )
                    (root / "udp-peer.json").chmod(0o600)
                    output = run(
                        "docker",
                        "run",
                        "--rm",
                        "--pull=never",
                        "--name",
                        names["probe"],
                        "--network",
                        "host",
                        "--read-only",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges",
                        "--memory",
                        "96m",
                        "--cpus",
                        "0.2",
                        "--pids-limit",
                        "32",
                        *fixture,
                        "python:3.11-slim",
                        "python",
                        "-B",
                        "/interconnect_load.py",
                        "probe",
                        "--port",
                        str(port + 4),
                        "--token-file",
                        "/fixture/token",
                        "--peer-file",
                        "/fixture/peer.json",
                        "--udp-peer-file",
                        "/fixture/udp-peer.json",
                        "--mbps",
                        str(payload["mbps"]),
                        "--seconds",
                        str(payload["seconds"]),
                    )
                    result = json.loads(output)
                elif action == "stats" and role == "entry" and payload.get("hot_swap"):
                    output = run(
                        "docker",
                        "exec",
                        names["relay"],
                        "xray",
                        "api",
                        "statsquery",
                        f"--server=127.0.0.1:{port + 7}",
                        "-pattern=outbound>>>",
                    )
                    result = json.loads(output[output.index("{") :])
                else:
                    raise ValueError("Unexpected canary action")
                print(json.dumps(result), flush=True)
        finally:
            if controller is not None and controller.stdin:
                controller.stdin.close()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    controller.wait(timeout=10)
            cleanup()
            lease.cancel()
            if initial:
                current = json.loads(run("docker", "inspect", "xray"))[0]
                if (current["State"]["StartedAt"], current["RestartCount"]) != (
                    initial["State"]["StartedAt"],
                    initial["RestartCount"],
                ):
                    raise RuntimeError("Production Xray changed during canary")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--target-json", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker()
        return
    target = json.loads(
        sys.stdin.read()
        if str(args.target_json) == "-"
        else args.target_json.read_text()
    )
    if target.get("chain_authorized") is not True:
        raise ValueError(
            "Temporary entry listener requires explicit chain_authorized=true"
        )
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "tests/integration"))
    from interconnect_load import resource_delta

    payload = {
        key: target[key]
        for key in (
            "entry_address",
            "exit_address",
            "exit_public",
            "domain",
            "singbox_image",
        )
    }
    if not payload["domain"] or any(
        c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in payload["domain"]
    ):
        raise ValueError("Expected certificate DNS name")
    payload.update(
        variant="uot",
        mbps=target.get("transport_mbps", 30),
        seconds=target.get("transport_seconds", 30),
        port=secrets.randbelow(12000) + 42000,
        suffix=secrets.token_hex(6),
        password=secrets.token_urlsafe(24),
        voice_password=secrets.token_urlsafe(24),
        ss_password=base64.b64encode(secrets.token_bytes(16)).decode(),
        token=secrets.token_hex(32),
        ss_over_tailscale=target.get("ss_over_tailscale", False),
        hot_swap=target.get("chain_hot_swap", False),
        node_image=target.get("chain_node_image"),
    )
    validate(payload)
    variants = target.get("chain_variants", ["uot", "native", "ss2022"])
    if (
        not isinstance(variants, list)
        or not 1 <= len(variants) <= 3
        or len(set(variants)) != len(variants)
        or any(v not in VARIANTS for v in variants)
    ):
        raise ValueError("chain_variants must select 1..3 distinct supported variants")
    if payload["hot_swap"] and variants != ["uot", "native"]:
        raise ValueError("Hot-swap rehearsal requires chain_variants=['uot', 'native']")
    payload["ss_enabled"] = "ss2022" in variants
    payload["files"] = {}
    for name in SOURCES:
        content = (root / name).read_text(encoding="utf-8")
        payload["files"][name] = {
            "content": content,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
    source = Path(__file__).read_text(encoding="utf-8")
    bootstrap = 'import json,sys; exec(json.loads(sys.stdin.readline())["worker"])'
    processes = {}
    completed = False

    def request(role: str, action: str, **fields: str) -> dict:
        process = processes[role]
        process.stdin.write(json.dumps({"action": action, **fields}) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            raise RuntimeError(f"Canary {role} disconnected")
        result = json.loads(line)
        if "startup_error" in result:
            print(json.dumps(result), flush=True)
            raise RuntimeError(f"Canary {role} startup failed")
        return result

    try:
        for role in ("exit", "entry"):
            argv = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "ServerAliveInterval=10",
                "-o",
                "ServerAliveCountMax=2",
            ]
            if role == "exit" and target.get("jump_host"):
                argv += ["-J", target["jump_host"]]
            argv += [
                target["entry_ssh"] if role == "entry" else target["ssh"],
                f"python3 -u -c {shlex.quote(bootstrap)} --worker",
            ]
            process = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
            )
            processes[role] = process
            process.stdin.write(json.dumps({"worker": source}) + "\n")
            process.stdin.write(json.dumps(payload | {"role": role}) + "\n")
            process.stdin.flush()
            ready = process.stdout.readline()
            if not ready:
                raise RuntimeError(f"Canary {role} could not initialize")
            print(ready.strip(), flush=True)
        for variant in variants:
            request("entry", "variant", variant=variant)
            if not payload["hot_swap"] or variant == "uot":
                request("exit", "variant", variant=variant)
            idle_before = {role: request(role, "cpu") for role in processes}
            idle_started = time.monotonic()
            time.sleep(5)
            before = {role: request(role, "cpu") for role in processes}
            idle_elapsed = time.monotonic() - idle_started
            started = time.monotonic()
            switches = []
            if payload["hot_swap"] and variant == "uot":
                # Existing bulk TCP/UDP flows must survive both routing changes.
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(request, "exit", "probe")
                    time.sleep(payload["seconds"] / 3)
                    switches.append(request("entry", "variant", variant="native"))
                    time.sleep(payload["seconds"] / 3)
                    switches.append(request("entry", "variant", variant="uot"))
                    result = future.result()
            else:
                result = request("exit", "probe")
            elapsed = time.monotonic() - started
            after = {role: request(role, "cpu") for role in processes}
            result.update(
                variant=variant,
                hy2_ingress="tailscale-only",
                interserver=(
                    "tailscale-ss2022"
                    if payload["ss_over_tailscale"]
                    else "public-ss2022"
                )
                if variant == "ss2022"
                else "tailscale",
                resources={
                    role: resource_delta(before[role], after[role], elapsed)
                    for role in before
                },
                idle_resources={
                    role: resource_delta(idle_before[role], before[role], idle_elapsed)
                    for role in before
                },
                udp_client="independent-hy2-account",
                cpu_window_seconds=round(elapsed, 3),
            )
            print(json.dumps(result), flush=True)
            if payload["hot_swap"]:
                counters = request("entry", "stats")
                if variant == "native" and not any(
                    ".native-" in item.get("name", "") and int(item.get("value", 0)) > 0
                    for item in counters.get("stat", [])
                ):
                    raise RuntimeError("Native handler carried no measured traffic")
                print(
                    json.dumps(
                        {
                            "switches": switches,
                            "outbound_stats": counters,
                        }
                    ),
                    flush=True,
                )
            if (
                not result["download_integrity"]
                or result["errors"]
                or result["download_thread_alive"]
            ):
                raise RuntimeError(f"{variant} chain did not complete TCP transfer")
        completed = True
    finally:
        failed_cleanup = []
        for process in processes.values():
            if process.stdin:
                with contextlib.suppress(OSError):
                    process.stdin.close()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=35)
            if process.poll() is None:
                process.terminate()
            if process.poll() != 0:
                failed_cleanup.append(process.pid)
        if completed and failed_cleanup:
            raise RuntimeError(
                "Remote cleanup was not acknowledged; inspect canary resources"
            )


if __name__ == "__main__":
    main()
