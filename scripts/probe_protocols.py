#!/usr/bin/env python3
"""Probe the actual subscription URLs through an isolated Xray client."""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import secrets
import socket
import ssl
import statistics
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


def _exact(conn: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        part = conn.recv(size - len(data))
        if not part:
            raise ConnectionError("SOCKS connection closed")
        data.extend(part)
    return bytes(data)


def _socks_request(
    conn: socket.socket, command: int, host: str, port: int
) -> tuple[str, int]:
    conn.sendall(b"\x05\x01\x00")
    if _exact(conn, 2) != b"\x05\x00":
        raise ConnectionError("SOCKS authentication failed")
    address = host.encode("idna")
    conn.sendall(
        b"\x05"
        + bytes((command, 0, 3, len(address)))
        + address
        + port.to_bytes(2, "big")
    )
    version, result, _, address_type = _exact(conn, 4)
    if version != 5 or result != 0:
        raise ConnectionError(f"SOCKS command {command} failed ({result})")
    if address_type == 1:
        bound = socket.inet_ntoa(_exact(conn, 4))
    elif address_type == 3:
        bound = _exact(conn, _exact(conn, 1)[0]).decode("idna")
    elif address_type == 4:
        bound = socket.inet_ntop(socket.AF_INET6, _exact(conn, 16))
    else:
        raise ConnectionError("Invalid SOCKS response")
    return bound, int.from_bytes(_exact(conn, 2), "big")


def _tcp_probe(port: int, timeout: float) -> str:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as conn:
        conn.settimeout(timeout)
        _socks_request(conn, 1, "api.ipify.org", 443)
        with ssl.create_default_context().wrap_socket(
            conn, server_hostname="api.ipify.org"
        ) as tls:
            tls.sendall(
                b"GET / HTTP/1.1\r\nHost: api.ipify.org\r\nConnection: close\r\n\r\n"
            )
            response = bytearray()
            while len(response) < 8192 and (part := tls.recv(4096)):
                response.extend(part)
    head, separator, body = bytes(response).partition(b"\r\n\r\n")
    if not separator or b" 200 " not in head.split(b"\r\n", 1)[0]:
        raise ConnectionError("HTTPS probe did not return 200")
    address = body.decode().strip()
    ipaddress.ip_address(address)
    return address


def _udp_probe(port: int, timeout: float) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as control:
        control.settimeout(timeout)
        _, relay_port = _socks_request(control, 3, "0.0.0.0", 0)
        if not relay_port:
            raise ConnectionError("SOCKS UDP relay has no port")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.settimeout(timeout)
            identifier = secrets.token_bytes(2)
            question = b"\x07example\x03com\x00\x00\x01\x00\x01"
            dns = identifier + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + question
            for server in ("1.1.1.1", "8.8.8.8"):
                packet = (
                    b"\x00\x00\x00\x01" + socket.inet_aton(server) + b"\x00\x35" + dns
                )
                udp.sendto(packet, ("127.0.0.1", relay_port))
                try:
                    data, _ = udp.recvfrom(4096)
                except TimeoutError:
                    continue
                if len(data) < 10 or data[:3] != b"\x00\x00\x00":
                    continue
                address_type = data[3]
                if address_type == 1:
                    header_size = 10
                elif address_type == 4:
                    header_size = 22
                elif address_type == 3 and len(data) > 4:
                    header_size = 7 + data[4]
                else:
                    continue
                answer = data[header_size:]
                if len(answer) >= 12 and answer[:2] == identifier and answer[2] & 0x80:
                    return
            raise TimeoutError("No DNS response through SOCKS UDP")


def _outbound(uri: str, client: str = "xray") -> dict:
    parsed = urlsplit(uri)
    query = parse_qs(parsed.query)
    if not parsed.hostname or not parsed.port or not parsed.username:
        raise ValueError("Incomplete subscription URI")
    if client == "sing-box":
        if parsed.scheme != "hysteria2":
            raise ValueError("sing-box probe currently supports Hysteria2 only")
        return {
            "type": "hysteria2",
            "server": parsed.hostname,
            "server_port": parsed.port,
            "password": unquote(parsed.username),
            "tls": {
                "enabled": True,
                "server_name": query["sni"][0],
                "alpn": ["h3"],
            },
        }
    if parsed.scheme == "hysteria2":
        return {
            "protocol": "hysteria",
            "settings": {"version": 2, "address": parsed.hostname, "port": parsed.port},
            "streamSettings": {
                "network": "hysteria",
                "security": "tls",
                "tlsSettings": {"serverName": query["sni"][0], "alpn": ["h3"]},
                "hysteriaSettings": {"version": 2, "auth": unquote(parsed.username)},
            },
        }
    if parsed.scheme == "vless":
        return {
            "protocol": "vless",
            "settings": {
                "vnext": [
                    {
                        "address": parsed.hostname,
                        "port": parsed.port,
                        "users": [
                            {
                                "id": unquote(parsed.username),
                                "encryption": query.get("encryption", ["none"])[0],
                                "flow": query.get("flow", [""])[0],
                            }
                        ],
                    }
                ]
            },
            "streamSettings": {
                "network": query.get("type", ["tcp"])[0],
                "security": query["security"][0],
                "realitySettings": {
                    "serverName": query["sni"][0],
                    "publicKey": query["pbk"][0],
                    "shortId": query["sid"][0],
                    "fingerprint": query.get("fp", ["chrome"])[0],
                },
            },
        }
    raise ValueError(f"Unsupported protocol: {parsed.scheme}")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _stability_probe(port: int, seconds: int) -> dict:
    """Observe one persistent UDP association and independent HTTPS requests."""
    deadline = time.monotonic() + seconds
    tcp_times: list[float] = []
    tcp_errors: list[str] = []
    udp_times: list[float] = []
    reply_gaps: list[float] = []
    stopped = threading.Event()
    sent, lost = 0, 0

    def https() -> None:
        while time.monotonic() < deadline and not stopped.is_set():
            started = time.monotonic()
            try:
                _tcp_probe(port, 3)
                tcp_times.append((time.monotonic() - started) * 1000)
            except (OSError, ValueError) as error:
                tcp_errors.append(type(error).__name__)
            stopped.wait(3)

    thread = threading.Thread(target=https, daemon=True)
    thread.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as control:
            control.settimeout(3)
            _, relay = _socks_request(control, 3, "0.0.0.0", 0)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.connect(("127.0.0.1", relay))
                udp.settimeout(1)
                previous = time.monotonic()
                question = b"\x07example\x03com\x00\x00\x01\x00\x01"
                header = b"\x00\x00\x00\x01" + socket.inet_aton("1.1.1.1") + b"\x00\x35"
                while time.monotonic() < deadline:
                    identifier = secrets.token_bytes(2)
                    dns = (
                        identifier
                        + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
                        + question
                    )
                    started = time.monotonic()
                    udp.send(header + dns)
                    sent += 1
                    received = False
                    try:
                        while time.monotonic() - started < 1:
                            result = udp.recv(4096)
                            if (
                                result[:10] == header
                                and result[10:12] == identifier
                                and len(result) >= 22
                                and result[12] & 0x80
                            ):
                                now = time.monotonic()
                                udp_times.append((now - started) * 1000)
                                reply_gaps.append((now - previous) * 1000)
                                previous = now
                                received = True
                                break
                    except TimeoutError:
                        pass
                    lost += not received
                    time.sleep(max(0, 0.25 - (time.monotonic() - started)))
                reply_gaps.append((time.monotonic() - previous) * 1000)
    finally:
        stopped.set()
        thread.join(12)
    ordered = sorted(udp_times)
    return {
        "duration_seconds": seconds,
        "udp_sent": sent,
        "udp_lost": lost,
        "udp_rtt_median_ms": round(statistics.median(udp_times), 2)
        if udp_times
        else None,
        "udp_rtt_p95_ms": round(
            ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2
        )
        if ordered
        else None,
        "udp_rtt_max_ms": round(max(udp_times), 2) if udp_times else None,
        "udp_reply_gap_max_ms": round(max(reply_gaps, default=0), 2),
        "https_successes": len(tcp_times),
        "https_errors": tcp_errors,
        "https_total_max_ms": round(max(tcp_times), 2) if tcp_times else None,
        "https_thread_alive": thread.is_alive(),
    }


def _probe(
    uri: str, image: str, timeout: float, seconds: int = 0, client: str = "xray"
) -> dict:
    if not 0 <= seconds <= 180:
        raise ValueError("Stability duration must be in 0..180 seconds")
    parsed = urlsplit(uri)
    label = unquote(parsed.fragment) or parsed.hostname or parsed.scheme
    result = {"protocol": parsed.scheme, "label": label, "tcp": False, "udp": False}
    if client not in ("xray", "sing-box"):
        raise ValueError("Unsupported probe client")
    with tempfile.TemporaryDirectory(prefix="feint-probe-") as temporary:
        port = _free_port()
        config = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "listen": "127.0.0.1",
                    "port": port,
                    "protocol": "socks",
                    "settings": {"auth": "noauth", "udp": True},
                }
            ],
            "outbounds": [_outbound(uri)],
        }
        if client == "sing-box":
            config = {
                "log": {"level": "warn"},
                "inbounds": [
                    {"type": "socks", "listen": "127.0.0.1", "listen_port": port}
                ],
                "outbounds": [_outbound(uri, client)],
            }
        result["client"] = client
        config_path = Path(temporary, "client.json")
        config_path.write_text(json.dumps(config), encoding="utf-8")
        os.chown(temporary, -1, 65532)
        os.chown(config_path, -1, 65532)
        os.chmod(temporary, 0o750)
        os.chmod(config_path, 0o640)
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            f"feint-probe-{secrets.token_hex(8)}",
            "--network",
            "host",
            "--user",
            "65532:65532",
            "--read-only",
            "--cap-drop",
            "ALL",
            *(["--cap-add", "NET_BIND_SERVICE"] if client == "xray" else []),
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "192m",
            "--cpus",
            "0.2",
            "--pids-limit",
            "64",
            "--mount",
            f"type=bind,src={temporary},dst=/probe,readonly",
            image,
            "run",
            "-c",
            "/probe/client.json",
        ]
        with Path(temporary, "client.log").open("w+", encoding="utf-8") as quiet:
            process = subprocess.Popen(command, stdout=quiet, stderr=quiet)
            try:
                deadline = time.monotonic() + min(timeout, 10)
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"Xray client exited ({process.returncode})")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        time.sleep(0.2)
                else:
                    raise TimeoutError("Xray SOCKS listener did not start")
                try:
                    result["egress_ip"] = _tcp_probe(port, timeout)
                    result["tcp"] = True
                except (OSError, ValueError) as error:
                    result["tcp_error"] = type(error).__name__
                try:
                    _udp_probe(port, timeout)
                    result["udp"] = True
                except (OSError, ValueError) as error:
                    result["udp_error"] = type(error).__name__
                if seconds and result["tcp"] and result["udp"]:
                    result["stability"] = _stability_probe(port, seconds)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                subprocess.run(
                    ["docker", "rm", "-f", command[command.index("--name") + 1]],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                if not result["tcp"] or not result["udp"]:
                    quiet.seek(0)
                    diagnostic = quiet.read(65536).lower()
                    result["client_error_categories"] = [
                        category
                        for category in (
                            "certificate",
                            "authentication",
                            "timeout",
                            "handshake",
                            "connection refused",
                            "no recent network activity",
                        )
                        if category in diagnostic
                    ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subscription-url", default=os.getenv("FEINT_PROBE_SUBSCRIPTION_URL")
    )
    parser.add_argument(
        "--status-file", type=Path, default=Path("/var/lib/feint-probe/status.json")
    )
    parser.add_argument("--image", default="ghcr.io/xtls/xray-core:26.7.28")
    parser.add_argument("--timeout", type=float, default=12)
    parser.add_argument("--vantage", default=socket.gethostname())
    parser.add_argument("--candidate-uri-file", type=Path)
    args = parser.parse_args()
    if not args.subscription_url:
        parser.error("Set FEINT_PROBE_SUBSCRIPTION_URL or --subscription-url")

    report = {"vantage": args.vantage, "checked_at": time.time(), "results": []}
    try:
        with urllib.request.urlopen(
            args.subscription_url, timeout=args.timeout
        ) as response:
            content = base64.b64decode(response.read(), validate=True).decode()
        uris = [line.strip() for line in content.splitlines() if "://" in line]
        if not uris:
            raise ValueError("Subscription has no protocol URLs")
        candidates = []
        if args.candidate_uri_file is not None:
            candidates = [
                line.strip()
                for line in args.candidate_uri_file.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
        for uri, source in [(uri, "subscription") for uri in uris] + [
            (uri, "candidate") for uri in candidates
        ]:
            try:
                result = _probe(uri, args.image, args.timeout)
                result["source"] = source
                report["results"].append(result)
            except (OSError, ValueError, RuntimeError) as error:
                parsed = urlsplit(uri)
                report["results"].append(
                    {
                        "protocol": parsed.scheme,
                        "label": unquote(parsed.fragment),
                        "source": source,
                        "tcp": False,
                        "udp": False,
                        "error": type(error).__name__,
                    }
                )
    except (OSError, ValueError) as error:
        report["error"] = type(error).__name__
    args.status_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=args.status_file.parent, delete=False
    ) as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        pending = output.name
    os.replace(pending, args.status_file)
    print(json.dumps(report, ensure_ascii=False))
    published = [item for item in report["results"] if item["source"] == "subscription"]
    return (
        0 if published and all(item["tcp"] and item["udp"] for item in published) else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
