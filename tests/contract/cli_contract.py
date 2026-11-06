"""Exercise the operator CLI as a subprocess, including non-project cwd."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts/cli.py"


class CliContract(unittest.TestCase):
    def invoke(self, *args: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            return subprocess.run(
                [sys.executable, str(CLI), *args],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

    def test_list_is_safe_from_an_unrelated_directory(self) -> None:
        result = self.invoke("list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = json.loads(result.stdout)
        self.assertIn("diagnose", commands)
        self.assertIn("install", commands)
        self.assertNotIn("update-tailscale", commands)

    def test_unknown_command_is_rejected(self) -> None:
        result = self.invoke("does-not-exist")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_python_command_help_is_forwarded(self) -> None:
        result = self.invoke("probe", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--timeout", result.stdout)

    def test_top_level_help_does_not_import_host_only_modules(self) -> None:
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("interconnect", result.stdout)

    def test_canary_help_resolves_its_diagnostic_dependency(self) -> None:
        result = self.invoke("hot-reload-canary", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--client-image", result.stdout)

    def test_json_command_help_does_not_wait_for_input(self) -> None:
        result = self.invoke("test-route", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stdin", result.stdout)

    def test_subscription_help_does_not_read_private_configuration(self) -> None:
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                result = self.invoke("subscription", flag)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("USER", result.stdout)

    def test_interconnect_help_does_not_inspect_the_installed_stack(self) -> None:
        result = self.invoke("interconnect", "-h")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Compose", result.stdout)


if __name__ == "__main__":
    unittest.main()
