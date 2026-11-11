"""Subscription representation and reverse rollback across real file boundaries."""

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


class WorkflowLayersContract(unittest.IsolatedAsyncioTestCase):
    async def test_subscription_orders_labels_and_persists_only_its_setting(self):
        from services.subscription import SubscriptionService
        from store.settings import SubscriptionSettingsStore

        class Users:
            async def get_user_configs(self, username, domain):
                return {
                    "configs": {
                        "hysteria2": {
                            "config_url": "hysteria2://test@node.test:443#old"
                        },
                        "vmess": {
                            "config_url": "vmess://"
                            + base64.urlsafe_b64encode(
                                b'{"ps":"old","add":"node.test"}'
                            ).decode()
                        },
                    }
                }

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text("API_SECRET=untouched\nSUB_URI_TEMPLATE=old\n")
            service = SubscriptionService(
                Users(),
                SubscriptionSettingsStore(path),
                enabled=True,
                server_domain="node.test",
                template="old",
            )
            service.update_template("Test | {Protocol}")
            encoded, domain = await service.build("alice")
            rows = base64.b64decode(encoded).decode().splitlines()
            self.assertEqual(domain, "node.test")
            self.assertTrue(rows[0].startswith("vmess://"))
            self.assertEqual(
                json.loads(base64.urlsafe_b64decode(rows[0][8:]))["ps"], "Test | VMess"
            )
            self.assertEqual(
                rows[1], "hysteria2://test@node.test:443#Test%20%7C%20Hysteria2"
            )
            self.assertEqual(
                path.read_text(),
                "API_SECRET=untouched\nSUB_URI_TEMPLATE=Test | {Protocol}\n",
            )
            for invalid in ("{unknown}", "broken{", "two\nlines"):
                with self.assertRaises(ValueError):
                    service.update_template(invalid)
            self.assertEqual(service.template, "Test | {Protocol}")

    async def test_reverse_failed_restart_restores_both_files(self):
        from docker.errors import NotFound
        from exceptions import ReverseTunnelError
        from models import ReverseServerConfig
        from runtime.rathole import RatholeRuntime
        from services.reverse import ReverseService
        from store.reverse import ReverseFileStore

        class Containers:
            def get(self, name):
                raise NotFound("unavailable test container")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, state = root / "rathole.toml", root / "state.json"
            config.write_text("old exact configuration\n")
            state.write_text('{"mode":"disabled"}\n')
            runtime = RatholeRuntime(
                container_name="test",
                private_key="test-private",
                public_key="test-public",
                proxy_port=39081,
                client=SimpleNamespace(containers=Containers()),
            )
            service = ReverseService(runtime, ReverseFileStore(str(config), str(state)))
            with self.assertRaises(ReverseTunnelError):
                await service.configure(
                    ReverseServerConfig(
                        bind_port=50000, expose_port=39081, token="x" * 32
                    )
                )
            self.assertEqual(config.read_text(), "old exact configuration\n")
            self.assertEqual(state.read_text(), '{"mode":"disabled"}\n')
            self.assertEqual(await service.get_state(), {"mode": "disabled"})


if __name__ == "__main__":
    unittest.main()
