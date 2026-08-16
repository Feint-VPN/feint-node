"""Regression checks for the root update.sh helper."""

from pathlib import Path


def test_update_script_falls_back_to_main_branch():
    content = Path("../update.sh").read_text(encoding="utf-8")

    assert 'echo "main"' in content


def test_update_script_exists():
    script_path = Path("../update.sh")
    assert script_path.exists(), "update.sh script not found"
    assert script_path.is_file(), "update.sh is not a file"


def test_update_script_has_shebang():
    script_path = Path("../update.sh")
    first_line = script_path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/bin/bash", "update.sh must have #!/bin/bash shebang"


def test_update_script_has_usage_and_options():
    script_path = Path("../update.sh")
    content = script_path.read_text(encoding="utf-8")

    assert "usage()" in content, "update.sh must have usage() function"
    assert "--branch" in content, "update.sh must document branch selection"
    assert "--dir" in content, "update.sh must document install directory override"
    assert "--force" in content, "update.sh must document tracked-change override"


def test_update_script_updates_git_and_containers():
    script_path = Path("../update.sh")
    content = script_path.read_text(encoding="utf-8")

    assert "git fetch" in content, "update.sh must fetch the target branch"
    assert "git reset --hard" in content, "update.sh must reset to the remote branch"
    assert "compose pull certbot sing-box vpn-node-api" in content, (
        "update.sh must pull all service images"
    )
    assert "compose build" not in content, "update.sh must not build on the VPS"
    assert "compose up -d --no-build --remove-orphans" in content, (
        "update.sh must restart prepared images"
    )


def test_update_script_preserves_runtime_configuration():
    script_path = Path("../update.sh")
    content = script_path.read_text(encoding="utf-8")

    assert ".env.local" in content, "update.sh must handle .env.local"
    assert "backup_file" in content, "update.sh must back up runtime configuration"
    assert '--env-file "$ENV_FILE"' in content, (
        "update.sh must load declarative runtime values from .env.local"
    )


def test_update_script_migrates_stats_runtime():
    script_path = Path("../update.sh")
    content = script_path.read_text(encoding="utf-8")

    assert "ensure_stats_runtime_env" in content, (
        "update.sh must ensure clash_api env keys exist"
    )
    assert "CLASH_API_SECRET" in content, (
        "update.sh must provision CLASH_API_SECRET during updates"
    )
    assert "V2RAY_API_ADDRESS" in content, (
        "update.sh must provision V2RAY_API_ADDRESS during updates"
    )
    assert "migrate_stats_config" in content, (
        "update.sh must migrate persisted sing-box config for v2ray_api stats"
    )
    assert (
        '"listen" != "0.0.0.0:10085"' in content or 'v2ray_api["listen"]' in content
    ), "update.sh must enforce the v2ray_api listener"
    assert "/opt/sing-box/config.json" in content, (
        "update.sh must touch the persisted sing-box config"
    )


def test_update_script_verifies_stats_backends_after_update():
    script_path = Path("../update.sh")
    content = script_path.read_text(encoding="utf-8")

    assert "verify_clash_api_access" in content, (
        "update.sh must verify clash_api reachability after migration"
    )
    assert "verify_v2ray_api_access" in content, (
        "update.sh must verify v2ray_api reachability after migration"
    )
    assert "compose exec -T vpn-node-api python" in content, (
        "update.sh must check stats backends from the API container"
    )


def test_update_script_secret_generation_is_safe_with_pipefail():
    script_path = Path("../update.sh")
    content = script_path.read_text(encoding="utf-8")

    assert "set +o pipefail" in content, (
        "update.sh must temporarily disable pipefail when generating secrets"
    )
    assert "printf '%s' \"$secret\"" in content, (
        "update.sh must emit the generated secret without a trailing newline"
    )


def test_scripts_readme_documents_update_script():
    readme_path = Path("../scripts/README.md")
    content = readme_path.read_text(encoding="utf-8")

    assert "update.sh" in content, "scripts/README.md must document update.sh"
    assert "sudo bash ./update.sh" in content, (
        "scripts/README.md must show update.sh usage"
    )
