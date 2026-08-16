"""Regression checks for the root install.sh helper."""

from pathlib import Path


def test_install_script_uses_canonical_repository_and_branch():
    content = Path("../install.sh").read_text(encoding="utf-8")

    assert 'REPO_URL="https://github.com/Feint-VPN/feint-node.git"' in content
    assert 'BRANCH="main"' in content


def test_install_script_generates_secrets_safely_with_pipefail():
    script_path = Path("../install.sh")
    content = script_path.read_text(encoding="utf-8")

    assert "set +o pipefail" in content, (
        "install.sh must temporarily disable pipefail when generating secrets"
    )
    assert "printf '%s' \"$secret\"" in content, (
        "install.sh must emit generated secrets without a trailing newline"
    )
    assert 'if [[ ! -f "$ENV_FILE" ]]; then' in content, (
        "install.sh must preserve an existing runtime env"
    )


def test_install_script_never_prints_the_api_secret():
    script_path = Path("../install.sh")
    content = script_path.read_text(encoding="utf-8")

    assert 'echo -e "  ${BOLD}API Secret:${NC}"' not in content
    assert "Stored privately in ${ENV_FILE}" in content
    assert "X-API-Secret: <read it from .env.local>" in content


def test_install_script_pulls_prebuilt_service_images():
    script_path = Path("../install.sh")
    content = script_path.read_text(encoding="utf-8")

    assert "compose pull vpn-node-api sing-box" in content, (
        "install.sh must pull the published service images"
    )
    assert "compose build" not in content, "install.sh must not build on the VPS"
    assert "compose up -d --no-build" in content, (
        "install.sh must start the prepared images"
    )
    assert content.index("cp /tmp/singbox-install-config.json") < content.index(
        "compose up -d --no-build"
    ), "install.sh must write sing-box config before starting containers"
    assert "--no-cache" not in content, "install.sh must retain Docker layer caching"


def test_compose_uses_a_pinned_official_singbox_image():
    compose_path = Path("../docker-compose.yml")
    content = compose_path.read_text(encoding="utf-8")

    assert "image: ${SINGBOX_IMAGE:-ghcr.io/sagernet/sing-box:v1.13.12}" in content
    assert "build:" not in content.split("  sing-box:", 1)[1].split("  certbot:", 1)[0]


def test_compose_uses_published_node_image():
    content = Path("../docker-compose.yml").read_text(encoding="utf-8")

    assert "image: ${NODE_IMAGE:-ghcr.io/feint-vpn/feint-node:latest}" in content
    assert "build:" not in content.split("  vpn-node-api:", 1)[1].split(
        "  sing-box:", 1
    )[0]


def test_install_script_writes_stats_runtime_env_values():
    script_path = Path("../install.sh")
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
    script_path = Path("../install.sh")
    content = script_path.read_text(encoding="utf-8")

    assert '"v2ray_api":{"listen":"0.0.0.0:10085"' in content, (
        "install.sh must write the v2ray_api listener into config.json"
    )
    assert '"stats":{"enabled":true,"users":[]}' in content, (
        "install.sh must enable per-user stats in config.json"
    )
