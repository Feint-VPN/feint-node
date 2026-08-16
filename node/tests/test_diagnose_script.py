"""Contract checks for the read-only diagnostics command."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "diagnose.sh"


def test_diagnostics_cover_the_deployed_runtime_without_mutation():
    content = SCRIPT.read_text(encoding="utf-8")

    assert content.startswith("#!/usr/bin/env bash")
    assert "curl -skf --max-time 10" in content
    assert '"${COMPOSE[@]}" exec -T sing-box' in content
    assert "sing-box check -c /opt/sing-box/config.json" in content
    assert "port_require_unique_config" in content
    assert "port_is_in_use" in content
    assert "sshd -T" in content
    assert "SSH still listens on the default port 22" in content
    assert "ufw status" in content
    assert "[[ \"$ufw_status\" == 'Status: active' ]]" in content
    assert " restart " not in content
    assert " up -d" not in content
    assert "env_set" not in content


def test_diagnostic_logs_are_bounded_and_redacted():
    content = SCRIPT.read_text(encoding="utf-8")

    assert "(( LOG_LINES <= 100 ))" in content
    assert 'logs --tail "$LOG_LINES"' in content
    assert 'line="${line//"$secret"/<redacted>}"' in content
    assert (
        "API_SECRET REALITY_PRIVATE_KEY SHADOWSOCKS_PASSWORD CLASH_API_SECRET"
        in content
    )
