"""
Unit tests for firewall setup script.

This test verifies that the setup-firewall.sh script exists and has the correct structure.
"""

from pathlib import Path


def test_firewall_script_exists():
    """Test that setup-firewall.sh script exists."""
    script_path = Path("../scripts/setup-firewall.sh")
    assert script_path.exists(), "setup-firewall.sh script not found"
    assert script_path.is_file(), "setup-firewall.sh is not a file"


def test_firewall_script_has_shebang():
    """Test that setup-firewall.sh has proper shebang."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        first_line = f.readline().strip()
    assert first_line == "#!/bin/bash", "Script must have #!/bin/bash shebang"


def test_firewall_script_checks_root():
    """Test that setup-firewall.sh checks for root privileges."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        content = f.read()
    assert "EUID" in content, "Script must check for root privileges"
    assert 'if [ "$EUID" -ne 0 ]' in content, "Script must check EUID"


def test_firewall_script_installs_ufw():
    """Test that setup-firewall.sh installs UFW if not present."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        content = f.read()
    assert "ufw" in content.lower(), "Script must mention UFW"
    assert "apt-get install" in content, "Script must install UFW if missing"


def test_firewall_script_checks_sshd_config_exists():
    """Test that setup-firewall.sh checks if sshd_config exists before modifying."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        content = f.read()

    assert "if [ ! -f /etc/ssh/sshd_config ]" in content, (
        "Script must check if sshd_config exists"
    )
    assert "Skipping SSH port change" in content, (
        "Script must skip SSH port change if config doesn't exist"
    )


def test_firewall_script_changes_ssh_port():
    """Test that setup-firewall.sh can change SSH port."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        content = f.read()
    assert "/etc/ssh/sshd_config" in content, "Script must modify SSH config"
    assert "Port" in content, "Script must change SSH port"
    assert "backup" in content.lower(), "Script must backup SSH config"


def test_firewall_script_allows_ssh_first():
    """Test that setup-firewall.sh allows SSH port before enabling UFW."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        content = f.read()

    # Find position of "ufw allow" for SSH and "ufw enable"
    ssh_allow_pos = content.find("ufw allow $NEW_SSH_PORT")
    ufw_enable_pos = content.find("ufw enable")

    assert ssh_allow_pos > 0, "Script must allow SSH port"
    assert ufw_enable_pos > 0, "Script must enable UFW"
    assert ssh_allow_pos < ufw_enable_pos, "Script must allow SSH BEFORE enabling UFW"


def test_firewall_script_allows_all_vpn_ports():
    """Test that setup-firewall.sh allows all VPN protocol ports."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        content = f.read()

    # Check for all required ports
    assert "8443" in content, "Script must allow VLESS port 8443"
    assert "443" in content, "Script must allow VMess port 443"
    assert "2053" in content, "Script must allow Trojan port 2053"
    assert "2083" in content, "Script must allow Hysteria2 port 2083"
    assert "8388" in content, "Script must allow Shadowsocks port 8388"
    assert "8000" in content, "Script must allow API port 8000"
    assert "80" in content, "Script must allow Certbot port 80"


def test_firewall_script_allows_hysteria2_udp():
    """Test that setup-firewall.sh allows Hysteria2 on UDP."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        content = f.read()

    # Hysteria2 uses UDP
    assert "/udp" in content, "Script must allow UDP ports"
    assert "2083/udp" in content or "HYSTERIA2_PORT/udp" in content, (
        "Script must allow Hysteria2 on UDP"
    )


def test_firewall_script_restarts_ssh():
    """Test that setup-firewall.sh restarts SSH after port change."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        content = f.read()
    assert "systemctl restart" in content, "Script must restart SSH service"
    assert "sshd" in content or "ssh" in content, "Script must restart SSH"


def test_firewall_script_has_error_handling():
    """Test that setup-firewall.sh has error handling."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        content = f.read()
    assert "set -e" in content, "Script must use set -e for error handling"


def test_firewall_script_has_colored_output():
    """Test that setup-firewall.sh has colored output with echo -e."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        content = f.read()
    assert "\\033[" in content or "RED=" in content or "GREEN=" in content, (
        "Script should have colored output"
    )
    # Check that echo -e is used for color output
    assert "echo -e" in content, "Script should use 'echo -e' for color codes"


def test_firewall_script_validates_port():
    """Test that setup-firewall.sh validates port numbers."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        content = f.read()
    assert "1024" in content, "Script must validate minimum port number"
    assert "65535" in content, "Script must validate maximum port number"


def test_firewall_script_checks_port_in_use():
    """Test that setup-firewall.sh checks if port is already in use."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        content = f.read()
    assert "netstat" in content, "Script must check if port is in use"


def test_firewall_script_shows_status():
    """Test that setup-firewall.sh shows UFW status at the end."""
    script_path = Path("../scripts/setup-firewall.sh")
    with open(script_path, encoding="utf-8") as f:
        content = f.read()
    assert "ufw status" in content, "Script must show UFW status"


def test_firewall_documentation_exists():
    """Test that FIREWALL_SETUP.md documentation exists."""
    doc_path = Path("../scripts/FIREWALL_SETUP.md")
    assert doc_path.exists(), "FIREWALL_SETUP.md documentation not found"

    with open(doc_path, encoding="utf-8") as f:
        content = f.read()

    assert "setup-firewall.sh" in content, "Documentation must mention the script"
    assert "SSH" in content, "Documentation must mention SSH"
    assert "UFW" in content, "Documentation must mention UFW"
