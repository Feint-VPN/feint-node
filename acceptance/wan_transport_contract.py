"""WAN probes require explicit test-host authority and bounded payloads."""

import importlib.util
import socket
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "wan_probe", Path(__file__).resolve().parents[1] / "scripts/wan_transport_probe.py"
)
assert spec is not None and spec.loader is not None
wan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wan)


class WanContract(unittest.TestCase):
    def setUp(self) -> None:
        self.target = {
            "test_only": True,
            "load_authorized": True,
            "entry_address": "100.64.0.1",
            "exit_address": "100.64.0.2",
            "exit_public": "1.1.1.1",
            "entry_ssh": "root@entry",
            "ssh": "root@exit",
            "gateway": "root@entry",
            "singbox_image": "test@sha256:" + "a" * 64,
            "wan_rates": [30],
            "wan_variants": ["ss2022", "hy2", "uot"],
            "transport_seconds": 5,
        }

    def test_valid_and_rejected_targets(self) -> None:
        wan.validate(self.target)
        invalid = [
            {"test_only": False},
            {"load_authorized": 1},
            {"entry_address": "127.0.0.1"},
            {"exit_address": "100.64.0.1"},
            {"exit_public": "192.168.0.1"},
            {"singbox_image": "test:latest"},
            {"singbox_image": "test@sha256:" + "z" * 64},
            {"wan_rates": [True]},
            {"wan_port": True},
            {"wan_port": 22},
            {"wan_port": 65536},
            {"transport_seconds": 60},
            {"wan_variants": ["hy2", "hy2"]},
            {"wan_variants": ["unknown"]},
            {"wan_variants": ["icmp"]},
            {"wan_ingress": "hy2"},
            {"wan_ingress": "unknown"},
            {"udp_buffer_bytes": True},
            {"udp_buffer_defaults": True},
            {"udp_buffer_bytes": 16777216, "udp_buffer_defaults": 1},
            {"udp_buffer_bytes": 0},
            {"udp_buffer_bytes": 32 * 1024 * 1024},
            {"wan_ingress": "hy2", "xray_image": "test:latest"},
            {"wan_connect_address": "198.18.254.2"},
            {"wan_kernel": "gre", "wan_connect_address": "127.0.0.1"},
            {"wan_rates": [1000], "transport_seconds": 10},
        ]
        for override in invalid:
            with self.subTest(override=override), self.assertRaises(ValueError):
                wan.validate(self.target | override)

    def test_isolated_underlay_and_icmp_require_explicit_config(self) -> None:
        wan.validate(self.target | {"udp_buffer_bytes": 16 * 1024 * 1024})
        wan.validate(
            self.target | {"udp_buffer_bytes": 16777216, "udp_buffer_defaults": True}
        )
        wan.validate(
            self.target
            | {"wan_ingress": "hy2", "xray_image": "test@sha256:" + "c" * 64}
        )
        wan.validate(
            self.target | {"wan_kernel": "ipip", "wan_connect_address": "198.18.254.2"}
        )
        wan.validate(
            self.target
            | {"wan_variants": ["icmp"], "gost_image": "test@sha256:" + "b" * 64}
        )

    def test_gateway_uses_authenticated_tailscale_ssh(self) -> None:
        command = wan.ssh_command(self.target, "exit", "python3 -c 'pass'")
        self.assertEqual(command[:3], ["tailscale", "ssh", "root@entry"])
        self.assertIn("tailscale ssh root@exit", command[3])
        self.assertNotIn("StrictHostKeyChecking=no", command[3])

    def test_occupied_ports_fail_without_changing_listener(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.assertRaises(OSError):
                wan.check_ports("127.0.0.1", (listener.getsockname()[1],))
            self.assertGreater(listener.fileno(), -1)

    def test_tcp_integrity_alone_cannot_pass(self) -> None:
        result = {"download_integrity": True, "udp_sent": 10, "udp_lost": 10}
        self.assertFalse(wan.smoke_passed(result))
        self.assertTrue(wan.smoke_passed(result | {"udp_lost": 0}))
        self.assertFalse(
            wan.smoke_passed(result | {"udp_lost": 0, "errors": ["timeout"]})
        )


if __name__ == "__main__":
    unittest.main()
