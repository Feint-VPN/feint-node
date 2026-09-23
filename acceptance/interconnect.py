"""Check the RU -> GE SOCKS/UoT sidecars on an isolated Docker network."""

from __future__ import annotations

import json
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

IMAGE = "ghcr.io/feint-vpn/feint-sing-box:v1.13.19-feint.1"
PYTHON_IMAGE = "python:3.11-slim"


def docker(*args: str) -> None:
    subprocess.run(["docker", *args], check=True)


def serve() -> None:
    def tcp_echo() -> None:
        with socket.socket() as listener:
            listener.bind(("0.0.0.0", 39087))
            listener.listen()
            while True:
                connection, _ = listener.accept()
                with connection:
                    connection.sendall(connection.recv(4096))

    threading.Thread(target=tcp_echo, daemon=True).start()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("0.0.0.0", 39087))
        while True:
            data, address = server.recvfrom(4096)
            server.sendto(data, address)


def recv_exact(connection: socket.socket, size: int) -> bytes:
    output = b""
    while len(output) < size:
        part = connection.recv(size - len(output))
        if not part:
            raise ConnectionError("SOCKS connection closed")
        output += part
    return output


def socks_request(
    command: int, listen_port: int = 39083
) -> tuple[socket.socket, str, int]:
    connection = socket.create_connection(("127.0.0.1", listen_port), timeout=5)
    connection.settimeout(5)
    connection.sendall(b"\x05\x01\x00")
    assert recv_exact(connection, 2) == b"\x05\x00"
    if command == 1:
        destination = b"feint-echo"
        target = (
            b"\x03"
            + bytes((len(destination),))
            + destination
            + struct.pack("!H", 39087)
        )
    else:
        target = b"\x01\x00\x00\x00\x00\x00\x00"
    connection.sendall(b"\x05" + bytes((command, 0)) + target)
    header = recv_exact(connection, 4)
    assert header[:2] == b"\x05\x00", f"SOCKS request failed: {header.hex()}"
    kind = header[3]
    if kind == 1:
        address = socket.inet_ntoa(recv_exact(connection, 4))
    elif kind == 3:
        address = recv_exact(connection, recv_exact(connection, 1)[0]).decode()
    else:
        raise AssertionError(f"Unexpected SOCKS address type: {kind}")
    port = struct.unpack("!H", recv_exact(connection, 2))[0]
    return connection, address, port


def check_traffic() -> None:
    with socks_request(1)[0] as connection:
        connection.sendall(b"feint-tcp")
        assert recv_exact(connection, 9) == b"feint-tcp"
    control, address, port = socks_request(3)
    with control, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.settimeout(5)
        destination = b"feint-echo"
        packet = b"\x00\x00\x00\x03" + bytes((len(destination),)) + destination
        packet += struct.pack("!H", 39087) + b"feint-udp"
        udp.sendto(packet, ("127.0.0.1" if address == "0.0.0.0" else address, port))
        result, _ = udp.recvfrom(4096)
        assert result.endswith(b"feint-udp"), result.hex()
    print("TCP and UDP reached the exit through authenticated SOCKS/UoT")


def check_public_udp(listen_port: int = 39083) -> None:
    """Confirm a DNS packet crosses the real interconnect and returns."""
    control, address, port = socks_request(3, listen_port)
    request_id = b"\x4f\x12"
    query = request_id + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    query += b"\x07example\x03com\x00\x00\x01\x00\x01"
    packet = b"\x00\x00\x00\x01" + socket.inet_aton("1.1.1.1")
    packet += struct.pack("!H", 53) + query
    with control, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.settimeout(10)
        udp.sendto(packet, ("127.0.0.1" if address == "0.0.0.0" else address, port))
        response, _ = udp.recvfrom(4096)
    assert response[:4] == b"\x00\x00\x00\x01", response[:4].hex()
    dns = response[10:]
    assert dns[:2] == request_id and dns[2] & 0x80, dns[:4].hex()
    print("UDP DNS response returned through RU -> GE")


def check_auth(exit_host: str) -> None:
    with socket.create_connection((exit_host, 39085), timeout=5) as connection:
        connection.sendall(b"\x05\x01\x00")
        assert recv_exact(connection, 2) == b"\x05\xff"


def allow_speed_sink(path: Path, address: str, port: int) -> None:
    """Allow one private test target in a temporary exit configuration."""
    config = json.loads(path.read_text(encoding="utf-8"))
    config["route"]["rules"].insert(
        0,
        {
            "ip_cidr": [f"{address}/32"],
            "port": port,
            "action": "route",
            "outbound": "direct",
        },
    )
    path.write_text(json.dumps(config), encoding="utf-8")


def run() -> None:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from scripts.configure_interconnect import entry_config, exit_config

    suffix = uuid.uuid4().hex[:8]
    network = f"feint-interconnect-{suffix}"
    entry = f"feint-entry-{suffix}"
    exit_node = f"feint-exit-{suffix}"
    echo = f"feint-echo-{suffix}"
    containers = (entry, exit_node, echo)
    with tempfile.TemporaryDirectory(prefix="feint-interconnect-") as directory:
        config_dir = Path(directory)
        exit_value = exit_config(
            "0.0.0.0", 39085, "feint-test", "local-acceptance-password"
        )
        # The test's private Docker addresses would be blocked by production GeoIP rules.
        exit_value["route"] = {"final": "direct"}
        entry_value = entry_config(
            exit_node, 39085, 39083, "feint-test", "local-acceptance-password"
        )
        (config_dir / "exit.json").write_text(json.dumps(exit_value), encoding="utf-8")
        (config_dir / "entry.json").write_text(
            json.dumps(entry_value), encoding="utf-8"
        )
        # The installer supplies the real RU GeoIP binary on deployed nodes.
        (config_dir / "production-exit.json").write_text(
            json.dumps(
                exit_config(
                    "100.64.0.3", 39085, "feint-test", "local-acceptance-password"
                )
            ),
            encoding="utf-8",
        )
        (config_dir / "geoip.json").write_text(
            json.dumps({"version": 3, "rules": [{"ip_cidr": ["203.0.113.0/24"]}]}),
            encoding="utf-8",
        )
        docker(
            "run",
            "--rm",
            "--mount",
            f"type=bind,src={config_dir},dst=/opt/sing-box",
            "--entrypoint",
            "sing-box",
            IMAGE,
            "rule-set",
            "compile",
            "/opt/sing-box/geoip.json",
            "-o",
            "/opt/sing-box/geoip-ru.srs",
        )
        docker(
            "run",
            "--rm",
            "--mount",
            f"type=bind,src={config_dir},dst=/opt/sing-box,readonly",
            "--entrypoint",
            "sing-box",
            IMAGE,
            "check",
            "-c",
            "/opt/sing-box/production-exit.json",
        )
        docker("network", "create", network)
        try:
            docker(
                "run",
                "-d",
                "--name",
                echo,
                "--network",
                network,
                "--network-alias",
                "feint-echo",
                "--mount",
                f"type=bind,src={Path(__file__).resolve()},dst=/acceptance.py,readonly",
                PYTHON_IMAGE,
                "python",
                "/acceptance.py",
                "serve",
            )
            for name, filename in ((exit_node, "exit.json"), (entry, "entry.json")):
                docker(
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--network",
                    network,
                    "--mount",
                    f"type=bind,src={config_dir / filename},dst=/config.json,readonly",
                    "--entrypoint",
                    "sing-box",
                    IMAGE,
                    "run",
                    "-c",
                    "/config.json",
                )
            for attempt in range(10):
                try:
                    docker(
                        "run",
                        "--rm",
                        "--network",
                        f"container:{entry}",
                        "--mount",
                        f"type=bind,src={Path(__file__).resolve()},dst=/acceptance.py,readonly",
                        PYTHON_IMAGE,
                        "python",
                        "/acceptance.py",
                        "check",
                    )
                    break
                except subprocess.CalledProcessError:
                    if attempt == 9:
                        for name in containers:
                            subprocess.run(["docker", "logs", name], check=False)
                        raise
                    time.sleep(1)
            docker(
                "run",
                "--rm",
                "--network",
                f"container:{entry}",
                "--mount",
                f"type=bind,src={Path(__file__).resolve()},dst=/acceptance.py,readonly",
                PYTHON_IMAGE,
                "python",
                "/acceptance.py",
                "check-auth",
                exit_node,
            )
        finally:
            for name in containers:
                subprocess.run(
                    ["docker", "rm", "-f", name], capture_output=True, check=False
                )
            docker("network", "rm", network)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "serve":
        serve()
    elif len(sys.argv) == 2 and sys.argv[1] == "check":
        check_traffic()
    elif len(sys.argv) in (2, 3) and sys.argv[1] == "check-public-udp":
        check_public_udp(int(sys.argv[2]) if len(sys.argv) == 3 else 39083)
    elif len(sys.argv) == 5 and sys.argv[1] == "allow-speed-sink":
        allow_speed_sink(Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]))
    elif len(sys.argv) == 3 and sys.argv[1] == "check-auth":
        check_auth(sys.argv[2])
    else:
        run()
