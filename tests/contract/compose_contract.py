"""Resolve real Compose models without starting containers or reading private env."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("docker"), "Docker CLI is unavailable")
class ComposeContract(unittest.TestCase):
    def resolve(self, *args: str) -> tuple[dict, Path]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "installation"
            shutil.copytree(ROOT / "deploy/compose", root / "deploy/compose")
            for service in ("node", "rathole"):
                (root / "services" / service).mkdir(parents=True)
            (root / ".env.local").write_text("API_SECRET=isolated-compose-contract\n")
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("COMPOSE_") and key != "API_SECRET"
            }
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/cli.py"),
                    "compose",
                    "--dir",
                    str(root),
                    *args,
                    "config",
                    "--format",
                    "json",
                ],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout), root

    def test_base_preserves_volume_identity_and_root_bind(self) -> None:
        model, root = self.resolve()
        self.assertEqual(model["name"], "installation")
        self.assertEqual(
            model["volumes"]["sing-box-data"]["name"], "installation_sing-box-data"
        )
        mounts = model["services"]["vpn-node-api"]["volumes"]
        env_mount = next(
            item for item in mounts if item["target"] == "/opt/vpn-node/.env.local"
        )
        self.assertEqual(Path(env_mount["source"]), root / ".env.local")

    def test_dev_build_contexts_are_under_services(self) -> None:
        model, root = self.resolve("--dev")
        for name, service in (("vpn-node-api", "node"), ("rathole", "rathole")):
            self.assertEqual(
                Path(model["services"][name]["build"]["context"]),
                root / "services" / service,
            )


if __name__ == "__main__":
    unittest.main()
