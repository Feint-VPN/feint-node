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
import subprocess
import tempfile
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


def _socks_request(conn: socket.socket, command: int, host: str, port: int) -> tuple[str, int]:
    conn.sendall(b"\x05\x01\x00")
    if _exact(conn, 2) != b"\x05\x00":
        raise ConnectionError("SOCKS authentication failed")
    address = host.encode("idna")
    conn.sendall(b"\x05" + bytes((command, 0, 3, len(address))) + address + port.to_bytes(2, "big"))
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
        with ssl.create_default_context().wrap_socket(conn, server_hostname="api.ipify.org") as tls:
            tls.sendall(b"GET / HTTP/1.1\r\nHost: api.ipify.org\r\nConnection: close\r\n\r\n")
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
                packet = b"\x00\x00\x00\x01" + socket.inet_aton(server) + b"\x00\x35" + dns
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


def _outbound(uri: str) -> dict:
    parsed = urlsplit(uri)
    query = parse_qs(parsed.query)
    if not parsed.hostname or not parsed.port or not parsed.username:
        raise ValueError("Incomplete subscription URI")
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
            "settings": {"vnext": [{"address": parsed.hostname, "port": parsed.port, "users": [{
                "id": unquote(parsed.username),
                "encryption": query.get("encryption", ["none"])[0],
                "flow": query.get("flow", [""])[0],
            }]}]},
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


def _probe(uri: str, image: str, timeout: float) -> dict:
    parsed = urlsplit(uri)
    label = unquote(parsed.fragment) or parsed.hostname or parsed.scheme
    result = {"protocol": parsed.scheme, "label": label, "tcp": False, "udp": False}
    with tempfile.TemporaryDirectory(prefix="feint-probe-") as temporary:
        port = _free_port()
        config = {
            "log": {"loglevel": "warning"},
            "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": {"auth": "noauth", "udp": True}}],
            "outbounds": [_outbound(uri)],
        }
        config_path = Path(temporary, "client.json")
        config_path.write_text(json.dumps(config), encoding="utf-8")
        os.chown(temporary, -1, 65532)
        os.chown(config_path, -1, 65532)
        os.chmod(temporary, 0o750)
        os.chmod(config_path, 0o640)
        command = ["docker", "run", "--rm", "--network", "host", "--user", "65532:65532", "--mount", f"type=bind,src={temporary},dst=/probe,readonly", image, "run", "-c", "/probe/client.json"]
        with open(os.devnull, "w", encoding="utf-8") as quiet:
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
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription-url", default=os.getenv("FEINT_PROBE_SUBSCRIPTION_URL"))
    parser.add_argument("--status-file", type=Path, default=Path("/var/lib/feint-probe/status.json"))
    parser.add_argument("--image", default="ghcr.io/xtls/xray-core:26.7.28")
    parser.add_argument("--timeout", type=float, default=12)
    parser.add_argument("--vantage", default=socket.gethostname())
    parser.add_argument("--candidate-uri-file", type=Path)
    args = parser.parse_args()
    if not args.subscription_url:
        parser.error("Set FEINT_PROBE_SUBSCRIPTION_URL or --subscription-url")

    report = {"vantage": args.vantage, "checked_at": time.time(), "results": []}
    try:
        with urllib.request.urlopen(args.subscription_url, timeout=args.timeout) as response:
            content = base64.b64decode(response.read(), validate=True).decode()
        uris = [line.strip() for line in content.splitlines() if "://" in line]
        if not uris:
            raise ValueError("Subscription has no protocol URLs")
        candidates = []
        if args.candidate_uri_file is not None:
            candidates = [
                line.strip()
                for line in args.candidate_uri_file.read_text(encoding="utf-8").splitlines()
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
                report["results"].append({
                    "protocol": parsed.scheme,
                    "label": unquote(parsed.fragment),
                    "source": source,
                    "tcp": False,
                    "udp": False,
                    "error": type(error).__name__,
                })
    except (OSError, ValueError) as error:
        report["error"] = type(error).__name__
    args.status_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.status_file.parent, delete=False) as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        pending = output.name
    os.replace(pending, args.status_file)
    print(json.dumps(report, ensure_ascii=False))
    published = [item for item in report["results"] if item["source"] == "subscription"]
    return 0 if published and all(item["tcp"] and item["udp"] for item in published) else 1


if __name__ == "__main__":
    raise SystemExit(main())
