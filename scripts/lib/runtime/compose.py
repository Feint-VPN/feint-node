"""Run Compose with the installation root as its stable project directory."""

import argparse
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument(
        "--dev", action="store_true", help="Use the local development stack"
    )
    args, command = parser.parse_known_args()
    root = args.dir.resolve()
    return subprocess.call(
        [
            "docker",
            "compose",
            "--project-directory",
            str(root),
            "--env-file",
            str(root / ".env.local"),
            "-f",
            str(root / "deploy/compose" / ("dev.yml" if args.dev else "base.yml")),
            *command,
        ],
        cwd=root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
