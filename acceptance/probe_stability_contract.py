"""Persistent SOCKS UDP probing detects drops independently of HTTPS failures."""

import socket
import struct
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import probe_protocols as probe


def conversion() -> None:
    uri = "hysteria2://canary%2Bsecret@probe.example:36454?sni=probe.example&insecure=0"
    xray = probe._outbound(uri)
    singbox = probe._outbound(uri, "sing-box")
    assert singbox["server"] == xray["settings"]["address"] == "probe.example"
    assert singbox["server_port"] == xray["settings"]["port"] == 36454
    assert (
        singbox["password"]
        == xray["streamSettings"]["hysteriaSettings"]["auth"]
        == "canary+secret"
    )
    assert singbox["tls"]["server_name"] == "probe.example"
    assert "insecure" not in singbox["tls"]
    assert "allowInsecure" not in xray["streamSettings"]["tlsSettings"]


def resource_limits() -> None:
    matrix = Path(__file__).with_name("interconnect_load.py")
    for options in (
        ("--hy2", "--mbps", "300"),
        ("--hy2", "--seconds", "61"),
        ("--seconds", "11"),
    ):
        result = subprocess.run(
            [sys.executable, str(matrix), "matrix", *options],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2 and "limited" in result.stderr


def exercise(*, drop_udp: bool, fail_https: bool) -> None:
    original = probe._tcp_probe
    associations = []
    stopped = threading.Event()

    def https(_port, _timeout):
        if fail_https:
            raise TimeoutError("Injected HTTPS timeout")
        return "203.0.113.1"

    with (
        socket.socket() as listener,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp,
    ):
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.1)
        udp.bind(("127.0.0.1", 0))
        udp.settimeout(0.1)

        def control() -> None:
            while not stopped.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                with connection:
                    assert probe._exact(connection, 3) == b"\x05\x01\x00"
                    connection.sendall(b"\x05\x00")
                    assert probe._exact(connection, 4) == b"\x05\x03\x00\x03"
                    length = probe._exact(connection, 1)[0]
                    _ = probe._exact(connection, length + 2)
                    associations.append(1)
                    connection.sendall(
                        b"\x05\x00\x00\x01"
                        + socket.inet_aton("127.0.0.1")
                        + struct.pack("!H", udp.getsockname()[1])
                    )
                    connection.settimeout(0.1)
                    while not stopped.is_set():
                        try:
                            if not connection.recv(1):
                                break
                        except TimeoutError:
                            continue

        def dns() -> None:
            while not stopped.is_set():
                try:
                    packet, address = udp.recvfrom(2048)
                except TimeoutError:
                    continue
                if not drop_udp:
                    assert len(packet) >= 22
                    udp.sendto(
                        packet[:12] + bytes((packet[12] | 0x80,)) + packet[13:], address
                    )

        threads = [threading.Thread(target=control), threading.Thread(target=dns)]
        probe._tcp_probe = https
        try:
            for thread in threads:
                thread.start()
            result = probe._stability_probe(listener.getsockname()[1], 2)
            assert associations == [1], "Probe must keep one UDP association alive"
            assert not result["https_thread_alive"]
            if drop_udp:
                assert result["udp_lost"] == result["udp_sent"] > 0
                assert result["udp_rtt_median_ms"] is None
                assert result["udp_reply_gap_max_ms"] >= 1900
            else:
                assert result["udp_sent"] >= 6 and result["udp_lost"] == 0
                assert result["udp_rtt_median_ms"] is not None
            if fail_https:
                assert result["https_errors"] == ["TimeoutError"]
                assert result["https_successes"] == 0
            else:
                assert result["https_successes"] > 0 and not result["https_errors"]
        finally:
            probe._tcp_probe = original
            stopped.set()
            for thread in threads:
                thread.join(2)
                assert not thread.is_alive(), "Fixture leaked a worker"


if __name__ == "__main__":
    conversion()
    resource_limits()
    exercise(drop_udp=False, fail_https=False)
    exercise(drop_udp=True, fail_https=False)
    exercise(drop_udp=False, fail_https=True)
    print(
        "PASS persistent UDP association, missing replies, independent HTTPS timeouts and cleanup"
    )
