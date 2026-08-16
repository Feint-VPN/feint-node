"""Regression checks for the host firewall and SSH transition."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "setup-firewall.sh"


def test_firewall_requires_a_new_checked_ssh_port():
    content = SCRIPT.read_text(encoding="utf-8")

    assert content.startswith("#!/usr/bin/env bash\n")
    assert "--ssh-port" in content
    assert "port_find_free_unique" in content
    assert '[[ "$NEW_SSH_PORT" != "$OLD_SSH_PORT" ]]' in content
    assert "port_require_available" in content


def test_ssh_transition_is_validated_confirmed_and_reversible():
    content = SCRIPT.read_text(encoding="utf-8")

    assert "SSH_BACKUP=" in content
    assert "trap rollback ERR INT TERM" in content
    assert "sshd -t" in content
    assert "sshd -T" in content
    assert "systemctl daemon-reload" in content
    assert "ssh.socket" in content
    assert "Type CONFIRM" in content
    assert content.index('ufw allow "$transition_port/tcp"') < content.index(
        "Type CONFIRM"
    )
    assert content.index("Type CONFIRM") < content.index(
        'ufw --force delete allow "$OLD_SSH_PORT/tcp"'
    )


def test_firewall_replaces_host_rules_with_the_feint_allowlist():
    content = SCRIPT.read_text(encoding="utf-8")

    assert "ufw --force reset" in content
    assert "ufw default deny incoming" in content
    assert "ufw default deny routed" in content
    assert "ufw default allow outgoing" in content
    assert "IPV6=yes" in content
    assert "ufw allow 80/tcp" in content
    assert 'ufw allow "$API_PORT/tcp"' in content
    assert 'ufw allow "$VLESS_PORT/tcp"' in content
    assert 'ufw allow "$VMESS_PORT/tcp"' in content
    assert 'ufw allow "$TROJAN_PORT/tcp"' in content
    assert 'ufw allow "$HYSTERIA2_PORT/udp"' in content
    assert 'ufw allow "$SHADOWSOCKS_PORT/tcp"' in content
    assert 'ufw allow "$SHADOWSOCKS_PORT/udp"' in content


def test_firewall_documentation_exists():
    content = (ROOT / "scripts" / "FIREWALL_SETUP.md").read_text(encoding="utf-8")

    assert "setup-firewall.sh" in content
    assert "SSH" in content
    assert "UFW" in content
