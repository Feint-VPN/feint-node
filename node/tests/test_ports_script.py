"""Regression checks for the canonical port command."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORTS_SCRIPT = ROOT / "scripts" / "ports.sh"


def test_port_command_checks_conflicts_and_duplicates():
    content = PORTS_SCRIPT.read_text(encoding="utf-8")

    assert PORTS_SCRIPT.is_file()
    assert "port_require_available" in content
    assert "port_require_unique_config" in content
    assert "port_find_free" in content
    assert "randomize" in content
    assert "declare -A generated=()" in content
    assert 'used_key="${protocol}:${port}"' in content
    assert '[[ -z "${generated[$used_key]:-}" ]] && break' in content


def test_port_command_has_an_atomic_apply_path():
    content = PORTS_SCRIPT.read_text(encoding="utf-8")

    assert "--apply" in content
    assert 'apply_ports "$staged"' in content
    assert 'cp "$staged" "$ENV_FILE"' in content
    assert 'cp "$ENV_FILE" "$env_backup"' in content
    assert content.index('cp "$ENV_FILE" "$env_backup"') < content.index(
        'cp "$staged" "$ENV_FILE"'
    )
    assert 'show_ports "$staged"' in content
    assert "Ports staged in" not in content
    assert "Port change applied successfully" in content
    assert "previous configuration was restored" in content
    assert "for _ in {1..60}" in content
    assert "Last API status: %s\\n" in content
    assert 'wait_for_status "$status_url" "$api_secret"' not in content
    assert "exec -T --user root vpn-node-api" in content
    assert "chown 1000:1000" in content
    assert "chmod 600" in content
    assert 'handle.write("\\n")' in content
    assert 'handle.write("\\\\n")' not in content
