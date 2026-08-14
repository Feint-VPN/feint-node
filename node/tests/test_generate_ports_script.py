"""Regression checks for the port-command compatibility wrapper."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY_SCRIPT = ROOT / "scripts" / "generate-ports.sh"
PORTS_SCRIPT = ROOT / "scripts" / "ports.sh"


def test_legacy_generator_delegates_to_the_canonical_port_command():
    content = LEGACY_SCRIPT.read_text(encoding="utf-8")

    assert LEGACY_SCRIPT.is_file()
    assert content.startswith("#!/usr/bin/env bash")
    assert 'exec bash "$SCRIPT_DIR/ports.sh" randomize "$@"' in content


def test_canonical_port_command_checks_conflicts_and_duplicates():
    content = PORTS_SCRIPT.read_text(encoding="utf-8")

    assert PORTS_SCRIPT.is_file()
    assert "port_require_available" in content
    assert "port_require_unique_config" in content
    assert "port_find_free" in content
    assert "randomize" in content
    assert "declare -A generated=()" in content
    assert 'used_key="${protocol}:${port}"' in content
    assert '[[ -z "${generated[$used_key]:-}" ]] && break' in content


def test_canonical_port_command_has_an_atomic_apply_path():
    content = PORTS_SCRIPT.read_text(encoding="utf-8")

    assert "--apply" in content
    assert "Port change applied successfully" in content
    assert "previous configuration was restored" in content
