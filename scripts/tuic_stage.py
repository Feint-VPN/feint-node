"""Stage the optional TUIC service; validate before changing running services."""

import argparse
import copy
import json
import os
import re
import secrets
import shutil
import subprocess
import uuid
from pathlib import Path


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True)


def atomic_json(path: Path, value: dict, owner: tuple[int, int]) -> None:
    temp = path.with_suffix(path.suffix + ".tuic.tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.chown(temp, *owner)
    os.replace(temp, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--compose", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--core", default="xray")
    parser.add_argument("--api-container", default="vpn-node-api")
    parser.add_argument("--published-protocols", default="hysteria2,tuic")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--stats-listen", default="172.17.0.1:10086")
    parser.add_argument(
        "--tuic-image", default="ghcr.io/feint-vpn/feint-sing-box:v1.13.19-feint.1"
    )
    args = parser.parse_args()
    state = json.loads(run("docker", "inspect", args.core))[0]
    root = Path(
        next(
            m["Source"] for m in state["Mounts"] if m["Destination"] == "/opt/sing-box"
        )
    )
    certs = next(
        m["Source"] for m in state["Mounts"] if m["Destination"] == "/etc/letsencrypt"
    )
    config = json.loads((root / "config.json").read_text())
    assert not any(i["type"] == "tuic" for i in config["inbounds"]), (
        "TUIC is already configured"
    )
    backup = args.work / "rollback"
    backup.mkdir(mode=0o700)
    for path in (
        root / "config.json",
        root / "xray.json",
        root / "traffic.json",
        args.compose,
        args.compose.parent / ".env.local",
    ):
        if path.exists():
            shutil.copy2(path, backup / path.name)
    (backup / "containers.json").write_text(
        run(
            "docker",
            "inspect",
            args.core,
            args.api_container,
        )
    )
    os.chmod(backup / "containers.json", 0o600)
    tls = copy.deepcopy(
        next(i["tls"] for i in config["inbounds"] if i["type"] == "hysteria2")
    )
    tls.update({"server_name": args.domain, "alpn": ["h3"]})
    test_id = str(uuid.uuid4())
    config["inbounds"].append(
        {
            "type": "tuic",
            "tag": "tuic-in",
            "listen": "::",
            "listen_port": args.port,
            "users": [
                {
                    "name": test_id,
                    "uuid": str(uuid.uuid4()),
                    "password": secrets.token_urlsafe(24),
                }
            ],
            "congestion_control": "cubic",
            "zero_rtt_handshake": False,
            "heartbeat": "10s",
            "tls": tls,
        }
    )
    companion = copy.deepcopy(config)
    companion["inbounds"] = [config["inbounds"][-1]]
    companion["experimental"] = {
        "v2ray_api": {
            "listen": args.stats_listen,
            "stats": {"enabled": True, "users": [test_id]},
        }
    }
    owner = ((root / "config.json").stat().st_uid, (root / "config.json").stat().st_gid)
    candidate = root / "tuic.json"
    atomic_json(candidate, companion, owner)
    try:
        run(
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "1000:1000",
            "-v",
            f"{root}:/opt/sing-box:ro",
            "-v",
            f"{certs}:/etc/letsencrypt:ro",
            args.tuic_image,
            "check",
            "-c",
            "/opt/sing-box/tuic.json",
        )
    except BaseException:
        candidate.unlink()
        raise
    atomic_json(root / "config.json", config, owner)
    compose = args.compose.read_text()
    service = """  tuic:
    image: TUIC_IMAGE
    container_name: feint-tuic
    user: "1000:1000"
    network_mode: host
    volumes:
      - sing-box-data:/opt/sing-box:ro
      - certbot-certs:/etc/letsencrypt:ro
    command: run -c /opt/sing-box/tuic.json
    restart: unless-stopped
    logging:
      options:
        max-size: "10m"
        max-file: "3"

""".replace("TUIC_IMAGE", args.tuic_image)
    compose = compose.replace("\nvolumes:\n", "\n" + service + "volumes:\n", 1)
    compose, replaced = re.subn(
        r"(  vpn-node-api:\n    image: )[^\n]+", r"\g<1>" + args.image, compose, count=1
    )
    assert replaced == 1 and "  tuic:" in compose
    args.compose.write_text(compose)
    env_path = args.compose.parent / ".env.local"
    env = env_path.read_text()
    env, replaced = re.subn(
        r"^PUBLISHED_PROTOCOLS=.*$",
        "PUBLISHED_PROTOCOLS=" + args.published_protocols,
        env,
        flags=re.M,
    )
    assert replaced == 1
    env += f"\n# Optional TUIC companion\nTUIC_CONTAINER_NAME=feint-tuic\nTUIC_CONFIG_PATH=/opt/sing-box/tuic.json\nTUIC_STATS_LISTEN={args.stats_listen}\nTUIC_STATS_ADDRESS=host.docker.internal:10086\n"
    env_path.write_text(env)
    os.chmod(env_path, 0o600)
    (args.work / "test-user-id").write_text(test_id)
    print("TUIC staged and validated. Existing services have not been restarted.")


if __name__ == "__main__":
    main()
