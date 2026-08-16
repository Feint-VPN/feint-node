"""Regression checks for the production updater."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UPDATE = ROOT / "update.sh"
SYNC = ROOT / "scripts" / "sync-singbox.py"
TEMPLATE = ROOT / "templates" / "sing-box.json.tpl"


def test_update_script_has_a_small_transactional_flow():
    content = UPDATE.read_text(encoding="utf-8")

    assert content.startswith("#!/bin/bash\n")
    assert "git fetch" in content
    assert 'git reset --hard "origin/$BRANCH"' in content
    assert "trap rollback ERR" in content
    assert 'git reset --hard "$OLD_COMMIT"' in content
    assert 'cp "$ENV_BACKUP" "$ENV_FILE"' in content
    assert 'cp "$CONFIG_BACKUP"' in content
    assert "pull certbot sing-box vpn-node-api" in content
    assert "up -d --no-build --remove-orphans" in content
    assert "wait_for_status" in content


def test_update_script_synchronizes_and_validates_the_template():
    content = UPDATE.read_text(encoding="utf-8")

    assert "scripts/sync-singbox.py" in content
    assert "templates/sing-box.json.tpl" in content
    assert "sing-box check" in content
    assert content.index("sing-box check") < content.index(
        '"${COMPOSE[@]}" exec -T vpn-node-api mv'
    )


def test_template_sync_preserves_users(tmp_path: Path):
    current = tmp_path / "current.json"
    output = tmp_path / "updated.json"
    users = {
        "vless-reality-in": [{"name": "alice", "uuid": "user-id"}],
        "trojan-in": [{"name": "alice", "password": "secret"}],
    }
    current.write_text(
        json.dumps(
            {
                "inbounds": [
                    {"tag": tag, "users": inbound_users}
                    for tag, inbound_users in users.items()
                ]
            }
        ),
        encoding="utf-8",
    )
    env = os.environ | {
        "SERVER_DOMAIN": "vpn.example.com",
        "VLESS_PORT": "11001",
        "VMESS_PORT": "11002",
        "TROJAN_PORT": "11003",
        "HYSTERIA2_PORT": "11004",
        "SHADOWSOCKS_PORT": "11005",
        "REALITY_SERVER_NAME": "www.microsoft.com",
        "REALITY_PRIVATE_KEY": "private-key",
        "REALITY_SHORT_ID": "0123456789abcdef",
        "SHADOWSOCKS_METHOD": "2022-blake3-aes-256-gcm",
        "SHADOWSOCKS_PASSWORD": "ss-secret",
        "CLASH_API_SECRET": "clash-secret",
    }

    subprocess.run(
        [sys.executable, SYNC, TEMPLATE, current, output],
        check=True,
        env=env,
    )

    updated = json.loads(output.read_text(encoding="utf-8"))
    inbounds = {inbound["tag"]: inbound for inbound in updated["inbounds"]}
    assert inbounds["vless-reality-in"]["users"] == users["vless-reality-in"]
    assert inbounds["trojan-in"]["users"] == users["trojan-in"]
    assert inbounds["vmess-ws-in"]["users"] == []
    assert updated["experimental"]["v2ray_api"]["stats"]["users"] == ["alice"]
    assert updated["route"]["final"] == "direct"
    assert inbounds["vless-reality-in"]["listen_port"] == 11001


def test_scripts_readme_documents_update_script():
    content = (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")

    assert "update.sh" in content
    assert "sudo bash ./update.sh" in content
