"""Public package exports must support real mutations without import-time I/O."""

import asyncio
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SOURCE))


class ArchitectureContract(unittest.IsolatedAsyncioTestCase):
    def test_legacy_overlay_handlers_catch_runtime_errors(self):
        repository = SOURCE.parents[2]
        helpers = runpy.run_path(
            str(repository / "scripts/lib/runtime/overlay_sources.py")
        )
        code = """
import asyncio, sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from domain.errors import SingBoxReloadError
from adapters.docker_runtime import DockerRuntime
from docker.errors import NotFound
class Containers:
    def get(self, name):
        raise NotFound("unavailable test container")
async def check():
    runtime = DockerRuntime(client=SimpleNamespace(containers=Containers()))
    try:
        await runtime.reload()
    except SingBoxReloadError:
        return
    raise AssertionError("Runtime unexpectedly accepted an unavailable container")
asyncio.run(check())
"""
        with tempfile.TemporaryDirectory() as temporary:
            for traffic in (False, True):
                with self.subTest(traffic=traffic):
                    root = Path(temporary) / str(traffic)
                    legacy = root / "src/domain"
                    legacy.mkdir(parents=True)
                    (legacy / "__init__.py").write_text("")
                    (legacy / "errors.py").write_text(
                        "class SingBoxReloadError(Exception):\n    pass\n"
                    )
                    helpers["stage_support"](SOURCE, root, services=traffic)
                    helpers["stage_runtime_bridges"](root, traffic=traffic)
                    result = subprocess.run(
                        [sys.executable, "-I", "-B", "-c", code, str(root / "src")],
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_remote_source_bundle_renders_from_an_unrelated_directory(self):
        repository = SOURCE.parents[2]
        files = runpy.run_path(
            str(repository / "scripts/lib/diagnostics/remote_chain_probe.py")
        )["SOURCES"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in files:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repository / relative, target)
            config, output = root / "config.json", root / "xray.json"
            config.write_text(
                json.dumps(
                    {
                        "log": {"level": "warn"},
                        "route": {"rules": [], "final": "direct"},
                        "outbounds": [],
                        "inbounds": [
                            {
                                "type": "hysteria2",
                                "tag": "hysteria2-in",
                                "listen": "127.0.0.1",
                                "listen_port": 2083,
                                "users": [
                                    {
                                        "name": "bundle-user",
                                        "password": "bundle-test-secret",
                                    }
                                ],
                                "tls": {
                                    "enabled": True,
                                    "certificate_path": "/test/cert",
                                    "key_path": "/test/key",
                                },
                            }
                        ],
                    }
                )
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('runtime.xray.configuration',run_name='__main__')",
                    str(root / "services/node/src"),
                    str(config),
                    str(output),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rendered = json.loads(output.read_text())
            inbound = next(
                x for x in rendered["inbounds"] if x["tag"] == "hysteria2-in"
            )
            self.assertEqual(inbound["port"], 2083)
            self.assertEqual(
                inbound["settings"]["clients"][0]["auth"], "bundle-test-secret"
            )

    async def test_application_repeated_lifespan_closes_owned_channels_and_tasks(self):
        from application import Application
        from config import Settings

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = Application(
                Settings(
                    DEV_MODE=True,
                    CONFIG_PATH=str(root / "config.json"),
                    BACKUP_DIR=str(root / "backups"),
                    TRAFFIC_STATE_PATH=str(root / "traffic.json"),
                    V2RAY_API_ADDRESS="127.0.0.1:1",
                )
            )
            for _ in range(2):
                await app.start()
                task = app.tracker._task
                await app.start()
                self.assertIs(app.tracker._task, task)
                self.assertTrue(app.tracker._source.channels)
                await app.close()
                self.assertFalse(app.tracker._source.channels)
                self.assertIsNone(app.tracker._task)
                self.assertTrue(task.done())

    async def test_public_services_save_user_and_authenticated_outbound(self):
        from contracts import ConfigStore, ContainerRuntime
        from models import SingBoxConfig, SocksOutboundConfig
        from runtime import NoopRuntime
        from store import SingBoxFileStore

        from services import OutboundService, UrlBuilder, UserService

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            store = SingBoxFileStore(str(path), str(path.parent / "backups"))
            runtime = NoopRuntime()
            self.assertIsInstance(store, ConfigStore)
            self.assertIsInstance(runtime, ContainerRuntime)
            await store.save(
                SingBoxConfig.model_validate(
                    {
                        "log": {"level": "warn"},
                        "inbounds": [
                            {
                                "type": "hysteria2",
                                "tag": "hysteria2-in",
                                "listen": "127.0.0.1",
                                "listen_port": 2083,
                                "users": [],
                            }
                        ],
                        "outbounds": [],
                        "route": {"rules": [], "final": "direct"},
                    }
                )
            )
            lock = asyncio.Lock()
            users = UserService(
                store, runtime, UrlBuilder("", "", ""), mutation_lock=lock
            )
            outbounds = OutboundService(store, runtime, mutation_lock=lock)
            await users.create_user("architecture-user")
            await outbounds.set(
                "test",
                SocksOutboundConfig(
                    server="127.0.0.1",
                    server_port=39000,
                    auth_users={"architecture-user"},
                ),
            )
            saved = json.loads(path.read_text())
            self.assertEqual(
                saved["inbounds"][0]["users"][0]["name"], "architecture-user"
            )
            self.assertEqual(
                saved["route"]["rules"][0]["auth_user"], ["architecture-user"]
            )
            self.assertEqual(saved["outbounds"][0]["tag"], "outbound:test")

    def test_imports_do_not_load_settings_or_start_infrastructure(self):
        code = """
import logging, threading
import docker, grpc, pydantic_settings
def forbidden(*args, **kwargs):
    raise AssertionError("Package import attempted configuration or infrastructure I/O")
docker.from_env = forbidden
grpc.aio.insecure_channel = forbidden
pydantic_settings.BaseSettings.__init__ = forbidden
before = (list(logging.getLogger().handlers), threading.active_count())
import services, contracts, models, runtime, store
assert before == (list(logging.getLogger().handlers), threading.active_count())
"""
        environment = os.environ | {"PYTHONPATH": str(SOURCE)}
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
