"""Captured production user-service contracts with real temporary state files."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "node/src"))

from adapters.docker_runtime import NoopRuntime
from adapters.singbox_file_store import SingBoxFileStore
from adapters.url_builder import UrlBuilder
from api.depends import authentication, runtime
from domain.errors import SingBoxReloadError
from domain.models import SingBoxConfig
from domain.user_service import UserService


class ProductionUserContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.store = SingBoxFileStore(str(root / "config.json"), str(root / "backups"))
        await self.store.save(
            SingBoxConfig.model_validate(
                {
                    "log": {},
                    "inbounds": [
                        {
                            "type": protocol,
                            "tag": tag,
                            "listen": "127.0.0.1",
                            "listen_port": port,
                        }
                        for protocol, tag, port in (
                            ("vless", "vless-reality-in", 20001),
                            ("hysteria2", "hysteria2-in", 20002),
                        )
                    ],
                    "outbounds": [{"type": "direct", "tag": "direct"}],
                    "route": {},
                }
            )
        )
        self.runtime = NoopRuntime()
        self.service = UserService(
            self.store, self.runtime, UrlBuilder("", "", "node.test")
        )

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_bulk_creation_is_idempotent_and_preserves_credentials(self):
        users = [("first", None, None), ("second", None, None)]
        self.assertEqual(await self.service.create_users(users), 2)
        before = (await self.store.load()).model_dump()
        backups = await asyncio.to_thread(
            lambda: sorted(self.store.backup_dir.glob("config_*.json"))
        )
        self.assertEqual(await self.service.create_users(users), 0)
        self.assertEqual((await self.store.load()).model_dump(), before)
        self.assertEqual(
            await asyncio.to_thread(
                lambda: sorted(self.store.backup_dir.glob("config_*.json"))
            ),
            backups,
        )

    async def test_concurrent_creation_does_not_lose_users(self):
        names = {f"user-{index}" for index in range(10)}
        await asyncio.gather(*(self.service.create_user(name) for name in names))
        for inbound in (await self.store.load()).inbounds:
            self.assertEqual({user.name for user in inbound.users}, names)

    async def test_failed_reload_restores_previous_file(self):
        before = (await self.store.load()).model_dump()
        self.runtime.reload = AsyncMock(
            side_effect=[SingBoxReloadError("injected"), None]
        )
        with self.assertRaises(SingBoxReloadError):
            await self.service.create_user("failed")
        self.assertEqual((await self.store.load()).model_dump(), before)

    async def test_backups_with_identical_clock_preserve_both_snapshots(self):
        with patch("adapters.singbox_file_store.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 4, tzinfo=UTC)
            first = await self.store.backup()
            config = await self.store.load()
            config.log.level = "debug"
            await self.store.save(config)
            second = await self.store.backup()
        self.assertNotEqual(first, second)
        first_data = await asyncio.to_thread(Path(first).read_text)
        second_data = await asyncio.to_thread(Path(second).read_text)
        self.assertEqual(json.loads(first_data)["log"]["level"], "info")
        self.assertEqual(json.loads(second_data)["log"]["level"], "debug")


class CompositionContract(unittest.TestCase):
    def test_auth_type_narrowing_preserves_missing_default_and_matching_cases(self):
        for configured, supplied, expected in (
            (None, None, False),
            ("change-me-in-production", "change-me-in-production", False),
            ("synthetic-test-secret", None, False),
            ("synthetic-test-secret", "", False),
            ("synthetic-test-secret", "other", False),
            ("synthetic-test-secret", "synthetic-test-secret", True),
        ):
            values = {"API_SECRET": configured} if configured is not None else {}
            with (
                self.subTest(configured=configured, supplied=supplied),
                patch.dict(os.environ, values, clear=True),
            ):
                self.assertIs(authentication.is_valid_api_secret(supplied), expected)

    def test_runtime_options_do_not_change_timeout_type(self):
        for tuic in (False, True):
            values = {
                "VPN_RUNTIME": "xray",
                "XRAY_CONFIG_PATH": "/not-present/xray.json",
            }
            if tuic:
                values.update(
                    TUIC_CONTAINER_NAME="companion",
                    TUIC_CONFIG_PATH="/not-present/tuic.json",
                    TUIC_STATS_LISTEN="127.0.0.1:10086",
                )
            with (
                self.subTest(tuic=tuic),
                patch.dict(os.environ, values, clear=True),
                patch.object(runtime, "container_runtime", None),
            ):
                instance = runtime.get_container_runtime()
                self.assertIs(type(instance.timeout), int)
                self.assertEqual(instance.timeout, 30)


if __name__ == "__main__":
    unittest.main()
