"""Scoped hot-reload rollout: save rollback state, restart only API and Xray.

The Xray image, users, keys, public ports, transports and Tailscale are untouched.
Run only after disposable runtime acceptance and a confirmed off-host backup.
"""

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--container", default="vpn-node-api")
    args = parser.parse_args()
    info = json.loads(command("docker", "inspect", args.container))[0]
    labels = info["Config"]["Labels"]
    files = labels["com.docker.compose.project.config_files"].split(",")
    directory = labels["com.docker.compose.project.working_dir"]
    env_file = labels["com.docker.compose.project.environment_file"]
    env = dict(item.split("=", 1) for item in info["Config"]["Env"])
    gateway = json.loads(command("docker", "network", "inspect", "bridge"))[0]["IPAM"][
        "Config"
    ][0]["Gateway"]
    listen = gateway + ":10085"
    runtime = env.get("VPN_RUNTIME_CONTAINER_NAME") or "xray"
    core_before = json.loads(command("docker", "inspect", runtime))[0]
    state = next(
        mount for mount in info["Mounts"] if mount["Destination"] == "/opt/sing-box"
    )["Source"]
    config = Path(state) / Path(env["XRAY_CONFIG_PATH"]).name
    args.work.mkdir(parents=True, exist_ok=False)
    args.work.chmod(0o700)
    shutil.copy2(config, args.work / "xray.before.json")
    for name in ("config.json", "traffic.json", "tuic.json"):
        if (Path(state) / name).is_file():
            shutil.copy2(Path(state) / name, args.work / (name + ".before"))
    for index, file in enumerate(files):
        shutil.copy2(file, args.work / f"compose.before.{index}.yml")
    metadata = {
        "api_image": info["Image"],
        "core_image": core_before["Image"],
        "config_files": files,
        "core_started": core_before["State"]["StartedAt"],
    }
    (args.work / "rollback.json").write_text(json.dumps(metadata))
    override = args.work / "compose.hot-reload.json"
    override.write_text(
        json.dumps(
            {
                "services": {
                    labels["com.docker.compose.service"]: {
                        "image": args.image,
                        "environment": {
                            "XRAY_HOT_RELOAD": "true",
                            "XRAY_API_LISTEN": listen,
                        },
                    }
                }
            }
        )
    )
    compose = [
        "docker",
        "compose",
        "--project-directory",
        directory,
        "--env-file",
        env_file,
    ]
    for file in files:
        compose.extend(("-f", file))
    old_compose = compose.copy()
    compose.extend(("-f", str(override)))
    command(*compose, "config", "--quiet")
    stopped = False
    try:
        # Freeze control-plane writes while changing the generated core document.
        command("docker", "stop", "--time", "10", args.container)
        stopped = True
        command(
            "docker",
            "run",
            "--rm",
            "--volumes-from",
            args.container,
            "-e",
            "XRAY_HOT_RELOAD=true",
            "-e",
            "XRAY_REALITY_MIN_CLIENT_VERSION="
            + env.get("XRAY_REALITY_MIN_CLIENT_VERSION", ""),
            "-e",
            "HYSTERIA2_COMPAT_PORT=" + env.get("HYSTERIA2_COMPAT_PORT", ""),
            "--entrypoint",
            "python",
            args.image,
            "-c",
            "import pathlib,runpy; runpy.run_module('runtime.xray.configuration' if pathlib.Path('/app/src/runtime/xray/configuration.py').is_file() else 'adapters.xray_config', run_name='__main__')",
            env["CONFIG_PATH"],
            env["XRAY_CONFIG_PATH"],
            listen,
        )
        command(
            "docker",
            "exec",
            runtime,
            "xray",
            "run",
            "-test",
            "-config",
            env["XRAY_CONFIG_PATH"],
        )
        command("docker", "restart", "--time", "5", runtime)
        command(
            *compose,
            "up",
            "-d",
            "--no-deps",
            "--no-build",
            "--pull",
            "never",
            labels["com.docker.compose.service"],
        )
        for _ in range(90):
            current = json.loads(command("docker", "inspect", args.container))[0]
            if current["State"].get("Health", {}).get("Status") == "healthy":
                break
            if current["State"]["Running"] and "Health" not in current["State"]:
                probe = (
                    "import os,ssl,urllib.request; "
                    "scheme='https' if os.getenv('API_USE_SSL','true').lower()=='true' else 'http'; "
                    "url=scheme+'://127.0.0.1:'+os.getenv('API_PORT','8000')+'/health'; "
                    "request=urllib.request.Request(url,headers={'X-API-Secret':os.environ['API_SECRET']}); "
                    "response=urllib.request.urlopen(request,context=ssl._create_unverified_context(),timeout=3); "
                    "assert response.status==200"
                )
                result = subprocess.run(
                    ["docker", "exec", args.container, "python", "-c", probe],
                    capture_output=True,
                )
                if result.returncode == 0:
                    break
            time.sleep(1)
        else:
            raise RuntimeError("Node API did not become healthy")
        if (
            json.loads(command("docker", "inspect", runtime))[0]["Image"]
            != core_before["Image"]
        ):
            raise RuntimeError("Unexpected core image change")
    except Exception:
        if stopped:
            shutil.copy2(args.work / "xray.before.json", config)
            rollback_override = args.work / "compose.rollback.json"
            rollback_override.write_text(
                json.dumps(
                    {
                        "services": {
                            labels["com.docker.compose.service"]: {
                                "image": info["Image"]
                            }
                        }
                    }
                )
            )
            command("docker", "restart", "--time", "5", runtime)
            command(
                *old_compose,
                "-f",
                str(rollback_override),
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                labels["com.docker.compose.service"],
            )
        raise
    # Persist the override in Compose's standard auto-discovery location.
    # Explicit -f workflows must keep the override recorded in container labels.
    default_override = Path(directory) / "docker-compose.override.yml"
    if not default_override.exists():
        shutil.copy2(override, default_override)
        os.chmod(default_override, 0o600)
    print(
        json.dumps(
            {
                "api_image": args.image,
                "private_control_listener": listen,
                "rollback": str(args.work),
                "status": "healthy",
            }
        )
    )


if __name__ == "__main__":
    main()
