"""Compatibility shims preserve arguments and exit codes without host changes."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GIT = shutil.which("git")
GIT_BASH = (
    Path(GIT).resolve().parents[1] / "bin/bash.exe" if GIT else Path("/nonexistent")
)
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.is_file() else shutil.which("bash")


@unittest.skipUnless(BASH, "Bash is unavailable")
class ShellContract(unittest.TestCase):
    def test_piped_installer_reaches_bootstrap_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = root / "bin"
            commands.mkdir()
            curl = commands / "curl"
            curl.write_text(
                "#!/usr/bin/env bash\nset -eu\n"
                "while [[ $# -gt 0 ]]; do\n"
                ' if [[ "$1" == --output ]]; then output="$2"; shift 2; else shift; fi\n'
                'done\nprintf \'printf "bootstrap-reached\\\\n"\\n\' > "$output"\n',
                encoding="utf-8",
            )
            curl.chmod(0o700)
            bash_env = root / "bash-env"
            bash_env.write_text(
                'curl() { bash "$CURL_FIXTURE" "$@"; }\n', encoding="utf-8"
            )
            result = subprocess.run(
                [BASH, "-s", "--", "--branch", "feature/repository-layout"],
                input=(ROOT / "install.sh").read_text(encoding="utf-8"),
                cwd=root,
                env=os.environ
                | {"BASH_ENV": bash_env.as_posix(), "CURL_FIXTURE": curl.as_posix()},
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "bootstrap-reached")

    def test_update_shim_forwards_arguments_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "checkout with spaces"
            worker = root / "scripts/lib/update/update.sh"
            worker.parent.mkdir(parents=True)
            shutil.copy2(ROOT / "update.sh", root / "update.sh")
            worker.write_text('printf "<%s>\\n" "$@"\nexit 7\n', encoding="utf-8")
            result = subprocess.run(
                [
                    BASH,
                    (root / "update.sh").as_posix(),
                    "--dir",
                    "a path; not a command",
                    "--force",
                ],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                ["<--dir>", "<a path; not a command>", "<--force>"],
            )

    def test_checkout_installer_help_is_local(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [BASH, (ROOT / "install.sh").as_posix(), "--help"],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--runtime", result.stdout)


if __name__ == "__main__":
    unittest.main()
