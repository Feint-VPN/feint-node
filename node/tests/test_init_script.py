"""Regression checks for the retired initializer compatibility command."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "init-node.sh"


def test_init_node_script_is_a_bash_compatibility_entrypoint():
    content = SCRIPT.read_text(encoding="utf-8")

    assert SCRIPT.is_file()
    assert content.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in content
    assert "Usage: scripts/init-node.sh <domain> <email> <server_ip>" in content


def test_init_node_delegates_to_the_supported_installer():
    content = SCRIPT.read_text(encoding="utf-8")

    assert '"$PROJECT_DIR/install.sh"' in content
    assert "--domain" in content
    assert "--email" in content
    assert "EUID" in content
    assert "/initialize" not in content
    assert "docker compose" not in content


def test_scripts_readme_marks_init_node_as_legacy_compatibility():
    content = (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")

    assert "Legacy `init-node.sh`" in content
    assert "install.sh" in content
