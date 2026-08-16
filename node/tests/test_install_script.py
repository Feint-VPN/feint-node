"""Regression checks for the root install.sh helper."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "sing-box.json.tpl"
TEMPLATE_VALUES = {
    "DOMAIN": "vpn.example.com",
    "VLESS_PORT": "11001",
    "VMESS_PORT": "11002",
    "TROJAN_PORT": "11003",
    "HYSTERIA2_PORT": "11004",
    "SHADOWSOCKS_PORT": "11005",
    "REALITY_SERVER_NAME": "www.microsoft.com",
    "REALITY_PRIVATE_KEY": "private-key",
    "REALITY_SHORT_ID": "0123456789abcdef",
    "SHADOWSOCKS_METHOD": "2022-blake3-aes-256-gcm",
    "SHADOWSOCKS_PASSWORD": "password",
    "CLASH_API_SECRET": "clash-secret",
}


def render_template() -> dict:
    content = TEMPLATE.read_text(encoding="utf-8")
    for name, value in TEMPLATE_VALUES.items():
        content = content.replace(f"{{{{{name}}}}}", value)
    return json.loads(content)


def test_install_script_uses_canonical_repository_and_branch():
    content = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'REPO_URL="https://github.com/Feint-VPN/feint-node.git"' in content
    assert 'BRANCH="main"' in content


def test_install_script_generates_secrets_safely_with_pipefail():
    script_path = ROOT / "install.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "set +o pipefail" in content, (
        "install.sh must temporarily disable pipefail when generating secrets"
    )
    assert "printf '%s' \"$secret\"" in content, (
        "install.sh must emit generated secrets without a trailing newline"
    )


def test_install_script_rejects_an_existing_install_before_mutation():
    content = (ROOT / "install.sh").read_text(encoding="utf-8")

    guard = 'if [[ -f "$INSTALL_DIR/.env.local" ]]; then'
    assert guard in content
    assert "Node already installed" in content
    assert content.index(guard) < content.index("apt-get update")
    assert content.index(guard) < content.index('git -C "$INSTALL_DIR" reset --hard')


def test_install_script_command_runner_preserves_failures():
    content = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert '"$@" 2>&1 | sed' in content
    assert 'done < <("$@" 2>&1)' not in content
    assert "run_with_log" not in content


def test_install_script_does_not_require_an_interactive_terminal():
    content = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'if [[ -t 1 && -n "${TERM:-}" ]]; then' in content
    assert content.index('if [[ -t 1 && -n "${TERM:-}" ]]; then') < content.index(
        "    clear"
    )


def test_install_script_waits_for_authenticated_status_before_success():
    content = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "wait_for_status()" in content
    assert "X-API-Secret: ${API_SECRET}" in content
    assert '"configuration":"available"' in content
    assert '"sing_box":"running"' in content
    assert "127.0.0.1:${API_PORT}/status" in content
    assert content.index("wait_for_status()") < content.index(
        'header "🎉  Installation complete"'
    )


def test_install_script_never_prints_the_api_secret():
    script_path = ROOT / "install.sh"
    content = script_path.read_text(encoding="utf-8")

    assert 'echo -e "  ${BOLD}API Secret:${NC}"' not in content
    assert "Stored privately in ${ENV_FILE}" in content
    assert "X-API-Secret: <read it from .env.local>" in content


def test_install_script_pulls_prebuilt_service_images():
    script_path = ROOT / "install.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "compose pull vpn-node-api sing-box" in content, (
        "install.sh must pull the published service images"
    )
    assert "compose build" not in content, "install.sh must not build on the VPS"
    assert "compose up -d --no-build" in content, (
        "install.sh must start the prepared images"
    )
    assert content.index("cp $SINGBOX_CONFIG") < content.index(
        "compose up -d --no-build"
    ), "install.sh must write sing-box config before starting containers"
    assert "--no-cache" not in content, "install.sh must retain Docker layer caching"


def test_install_script_pins_singbox_key_generation_image():
    content = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "ghcr.io/sagernet/sing-box:v1.13.12" in content
    assert "ghcr.io/sagernet/sing-box:latest" not in content


def test_compose_uses_a_pinned_v2ray_enabled_singbox_image():
    compose_path = ROOT / "docker-compose.yml"
    content = compose_path.read_text(encoding="utf-8")
    dockerfile = (ROOT / "sing-box" / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "image: ${SINGBOX_IMAGE:-ghcr.io/feint-vpn/feint-sing-box:"
        "v1.13.12-feint.1}" in content
    )
    assert "build:" not in content.split("  sing-box:", 1)[1].split("  certbot:", 1)[0]
    assert "ARG SINGBOX_REF=v1.13.12" in dockerfile
    assert "with_v2ray_api" in dockerfile
    assert (
        'org.opencontainers.image.source="https://github.com/Feint-VPN/feint-node"'
        in dockerfile
    )


def test_compose_uses_published_node_image():
    content = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "image: ${NODE_IMAGE:-ghcr.io/feint-vpn/feint-node:latest}" in content
    assert (
        "build:"
        not in content.split("  vpn-node-api:", 1)[1].split("  sing-box:", 1)[0]
    )


def test_install_script_writes_stats_runtime_env_values():
    script_path = ROOT / "install.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "CLASH_API_SECRET" in content, "install.sh must provision CLASH_API_SECRET"
    assert "CLASH_API_SECRET=$(gen_secret 32)" in content, (
        "install.sh must generate the secret before config rendering"
    )
    assert "CLASH_API_URL=http://host.docker.internal:9090" in content, (
        "install.sh must provision CLASH_API_URL"
    )
    assert "V2RAY_API_ADDRESS=host.docker.internal:10085" in content, (
        "install.sh must provision V2RAY_API_ADDRESS"
    )


def test_install_script_generates_v2ray_stats_config():
    config = render_template()

    assert config["experimental"]["v2ray_api"] == {
        "listen": "0.0.0.0:10085",
        "stats": {"enabled": True, "users": []},
    }


def test_singbox_template_renders_the_existing_protocol_set():
    config = render_template()
    inbounds = {inbound["tag"]: inbound for inbound in config["inbounds"]}

    assert set(inbounds) == {
        "vless-reality-in",
        "vmess-ws-in",
        "trojan-in",
        "hysteria2-in",
        "shadowsocks-in",
    }
    assert [inbounds[tag]["listen_port"] for tag in inbounds] == [
        11001,
        11002,
        11003,
        11004,
        11005,
    ]


def test_singbox_template_has_only_supported_placeholders():
    content = TEMPLATE.read_text(encoding="utf-8")
    placeholders = set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", content))

    assert placeholders == set(TEMPLATE_VALUES)


def test_install_script_renders_and_validates_template_before_persisting():
    content = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'SINGBOX_TEMPLATE="$INSTALL_DIR/templates/sing-box.json.tpl"' in content
    assert "replace_config_value VLESS_PORT" in content
    assert "Unresolved value in sing-box template" in content
    assert "sing-box check -c /tmp/config.json" in content
    assert "Generated sing-box config is invalid" in content
    assert "SINGBOX_CFG" not in content
    assert content.index("sing-box check -c /tmp/config.json") < content.index(
        "cp $SINGBOX_CONFIG"
    )
