"""Exercise migrated operator paths against real temporary deployment files."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReachedCompose(Exception):
    pass


class LayoutRuntimeContract(unittest.TestCase):
    def test_runtime_scenario_writes_a_usable_disposable_compose(self) -> None:
        runtime = load("runtime_scenario", "tests/acceptance/runtime.py")

        def external(*args, **kwargs):
            if "reality-keypair" in args:
                return "PrivateKey: fixture-private\nPublicKey: fixture-public\n"
            if args[0] == "openssl":
                for flag in ("-keyout", "-out"):
                    Path(args[args.index(flag) + 1]).write_text("fixture")
            if args[:2] == ("docker", "compose") and "up" in args:
                compose = Path(args[args.index("-f") + 1])
                model = json.loads(compose.read_text())
                env_file = Path(model["services"]["vpn-node-api"]["env_file"][0])
                self.assertTrue(env_file.is_file())
                self.assertEqual(compose.parent, env_file.parent)
                raise ReachedCompose
            return ""

        original_stat = os.stat

        def stat_boundary(path, *args, **kwargs):
            if str(path) == "/var/run/docker.sock":
                return SimpleNamespace(st_gid=0)
            return original_stat(path, *args, **kwargs)

        with (
            patch.object(runtime, "run", side_effect=external),
            patch.object(runtime.os, "chown", create=True),
            patch.object(runtime.os, "stat", side_effect=stat_boundary),
            self.assertRaises(ReachedCompose),
        ):
            runtime.exercise("isolated-node-image", "sing-box", "sing-box")

    def test_tuic_stage_backs_up_and_updates_the_root_environment(self) -> None:
        stage = load("tuic_stage", "scripts/lib/runtime/tuic_stage.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, work = root / "state", root / "work"
            state.mkdir()
            work.mkdir()
            config = {"inbounds": [{"type": "hysteria2", "tls": {"enabled": True}}]}
            (state / "config.json").write_text(json.dumps(config))
            env = root / ".env.local"
            env.write_text("PUBLISHED_PROTOCOLS=hysteria2\n")
            compose = root / "deploy/compose/base.yml"
            compose.parent.mkdir(parents=True)
            compose.write_text(
                "services:\n  vpn-node-api:\n    image: old-image\n\nvolumes:\n  sing-box-data: {}\n"
            )
            mounts = [
                {"Destination": "/opt/sing-box", "Source": str(state)},
                {"Destination": "/etc/letsencrypt", "Source": str(root / "certs")},
            ]

            def external(*args):
                return (
                    json.dumps([{"Mounts": mounts}])
                    if args[:2] == ("docker", "inspect")
                    else ""
                )

            argv = [
                "tuic-stage",
                "--work",
                str(work),
                "--compose",
                str(compose),
                "--env-file",
                str(env),
                "--image",
                "new-image",
                "--domain",
                "node.test",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(stage, "run", side_effect=external),
                patch.object(stage.os, "chown", create=True),
            ):
                stage.main()
            self.assertEqual(
                (work / "rollback/.env.local").read_text(),
                "PUBLISHED_PROTOCOLS=hysteria2\n",
            )
            self.assertIn("PUBLISHED_PROTOCOLS=hysteria2,tuic", env.read_text())
            self.assertFalse((compose.parent / ".env.local").exists())


if __name__ == "__main__":
    unittest.main()
