"""Real loopback echoes must distinguish reachable paths from corrupt replies."""

import contextlib
import importlib.util
import io
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/lib/diagnostics"))
from test_route_deploy import configs

spec = importlib.util.spec_from_file_location(
    "wan_path",
    Path(__file__).resolve().parents[2] / "scripts/lib/diagnostics/wan_path_smoke.py",
)
assert spec is not None and spec.loader is not None
path_probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(path_probe)


class PathContract(unittest.TestCase):
    def test_xray_transport_preserves_encryption_endpoints_and_udp(self) -> None:
        target = {
            "kernel_network": "10.42.19.0/30",
            "link_port": 31297,
            "socks_port": 31298,
        }
        secret = "a-test-key"
        entry = configs(target, "entry", secret, core="xray")
        exit_config = configs(target, "exit", secret, core="xray")
        source = entry["outbounds"][0]["settings"]["servers"][0]
        sink = exit_config["inbounds"][0]
        self.assertEqual(
            (source["address"], source["port"]), (sink["listen"], sink["port"])
        )
        self.assertEqual(
            (sink["listen"], sink["port"]), ("10.42.19.2", target["link_port"])
        )
        self.assertEqual(source["password"], sink["settings"]["password"])
        self.assertEqual(source["password"], secret)
        self.assertEqual(source["method"], sink["settings"]["method"])
        self.assertEqual(sink["settings"]["network"], "tcp,udp")
        self.assertTrue(entry["inbounds"][0]["settings"]["udp"])
        self.assertEqual(entry["inbounds"][0]["listen"], "127.0.0.1")
        self.assertEqual(entry["inbounds"][0]["port"], target["socks_port"])
        self.assertEqual(
            entry["routing"]["rules"][0]["outboundTag"], entry["outbounds"][0]["tag"]
        )
        self.assertEqual(exit_config["routing"]["rules"][0]["ip"], ["geoip:private"])
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            configs(target, "entry", secret, core="unknown")

    def test_client_checks_real_tcp_udp_and_rejects_wrong_token(self) -> None:
        token = bytes(range(32))
        with contextlib.ExitStack() as stack:
            ports = []
            threads = []

            def respond(listener: socket.socket, protocol: str, valid: bool) -> None:
                listener.settimeout(3)
                if protocol == "tcp":
                    connection, _ = listener.accept()
                    with connection:
                        connection.settimeout(3)
                        request = connection.recv(256)
                        connection.sendall(request if valid else b"wrong")
                else:
                    request, peer = listener.recvfrom(256)
                    listener.sendto(request if valid else b"wrong", peer)

            for protocol, valid in (("tcp", True), ("udp", True), ("tcp", False)):
                listener = stack.enter_context(
                    socket.socket(
                        socket.AF_INET,
                        socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM,
                    )
                )
                listener.bind(("127.0.0.1", 0))
                if protocol == "tcp":
                    listener.listen()
                ports.append([listener.getsockname()[1], protocol])
                thread = threading.Thread(
                    target=respond, args=(listener, protocol, valid)
                )
                thread.start()
                threads.append(thread)
            output = io.StringIO()
            request = {"token": token.hex(), "peer": "127.0.0.1", "ports": ports}
            with (
                patch.object(sys, "stdin", io.StringIO(json.dumps(request))),
                contextlib.redirect_stdout(output),
            ):
                path_probe.client()
            results = json.loads(output.getvalue())
            self.assertEqual([row["echo"] for row in results], [True, True, False])
            for thread in threads:
                thread.join(3)
                self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
