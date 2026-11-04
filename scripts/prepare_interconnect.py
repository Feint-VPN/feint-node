"""Stage native-UDP sidecar configs and exact rollback copies; never switch traffic."""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

TAILNET = ipaddress.ip_network("100.64.0.0/10")


def candidate(config: dict) -> dict:
    inbounds, outbounds = config["inbounds"], config["outbounds"]
    if (
        len(inbounds) != 1
        or inbounds[0]["type"] != "socks"
        or inbounds[0]["listen"] != "127.0.0.1"
        or len(outbounds) != 1
        or outbounds[0]["type"] != "socks"
        or ipaddress.IPv4Address(outbounds[0]["server"]) not in TAILNET
        or not outbounds[0].get("username")
        or not outbounds[0].get("password")
        or config["route"]["final"] != outbounds[0]["tag"]
        or outbounds[0].get("udp_over_tcp", {}).get("enabled") is not True
    ):
        raise ValueError("Expected one authenticated UoT entry over Tailscale")
    value = copy.deepcopy(config)
    value["outbounds"][0]["udp_over_tcp"]["enabled"] = False
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def native_xray(canonical: dict, runtime: dict, links: list[dict]) -> tuple[dict, dict]:
    """Replace only selected loopback peers; keep ingress, policy and route identities."""
    from xray_socks import socks_outbound

    desired, rendered = copy.deepcopy(canonical), copy.deepcopy(runtime)
    for link in links:
        matches = [
            item
            for item in desired["outbounds"]
            if item.get("type") == "socks"
            and item.get("server") == "127.0.0.1"
            and item.get("server_port") == link["listen_port"]
            and item["tag"].startswith("outbound:")
        ]
        if len(matches) != 1:
            raise ValueError("Expected exactly one canonical route for this sidecar")
        outbound = matches[0]
        outbound.update(link["peer"])
        old_tag = outbound["tag"]
        new = socks_outbound(old_tag, outbound)
        old = next(item for item in rendered["outbounds"] if item["tag"] == old_tag)
        if old["protocol"] != "socks" or old["settings"]["servers"] != [
            {"address": "127.0.0.1", "port": link["listen_port"]}
        ]:
            raise ValueError("Runtime and canonical sidecar endpoint disagree")
        rendered["outbounds"][rendered["outbounds"].index(old)] = new
        for rule in rendered["routing"]["rules"]:
            if rule.get("outboundTag") == old_tag:
                rule["outboundTag"] = new["tag"]
    return desired, rendered


def run(*args: str, timeout: int = 30) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Docker errors can include configuration; expose only the failed boundary.
        raise RuntimeError(f"Preparation command failed: {args[0]} {args[1]}")
    return result.stdout.strip()


def inspect(name: str) -> dict:
    return json.loads(run("docker", "inspect", name))[0]


def main() -> None:
    payload = json.loads(sys.stdin.read())
    target = payload["target"]
    mode = target.get("mode", "sidecar")
    if mode not in {"sidecar", "xray-native"}:
        raise ValueError("Preparation mode must be sidecar or xray-native")
    names = target["sidecars"]
    root = Path(target["directory"])
    if (
        os.geteuid() != 0
        or not root.is_absolute()
        or len(root.parts) < 4
        or root.is_symlink()
        or not isinstance(names, list)
        or not 1 <= len(names) <= 3
        or len(set(names)) != len(names)
        or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", n) for n in names)
    ):
        raise ValueError("Expected root, private staging directory and 1..3 sidecars")
    os.umask(0o077)
    os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"
    os.environ.pop("DOCKER_CONTEXT", None)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.stat().st_uid != 0 or root.stat().st_mode & 0o077:
        raise ValueError("Staging directory must be root-owned and private")
    bundle = root / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    bundle.mkdir(mode=0o700)
    sources = payload["files"]
    expected = {"configure_interconnect.py", "manage_interconnect.py"}
    if mode == "xray-native":
        expected.add("xray_socks.py")
    if set(sources) != expected:
        raise ValueError("Unexpected preparation sources")
    scripts = bundle / "scripts"
    scripts.mkdir(mode=0o700)
    for name, source in sources.items():
        if hashlib.sha256(source["content"].encode()).hexdigest() != source["sha256"]:
            raise ValueError("Preparation source checksum mismatch")
        (scripts / name).write_text(source["content"], encoding="utf-8")
    sys.path.insert(0, str(scripts))
    from manage_interconnect import check_peer

    original_xray = inspect("xray")
    original_api = inspect("vpn-node-api")
    native_links = []
    original_containers = {name: inspect(name) for name in names}
    records = []
    for name, container in original_containers.items():
        if (
            not container["State"]["Running"]
            or container["HostConfig"]["NetworkMode"] != "host"
        ):
            raise ValueError("Sidecar must already be running on the host network")
        config_mount = next(
            mount
            for mount in container["Mounts"]
            if mount["Destination"] == "/etc/feint/interconnect.json"
            and mount["Type"] == "bind"
        )
        live = Path(config_mount["Source"])
        old = live.read_bytes()
        config = json.loads(old)
        new = candidate(config)
        peer = config["outbounds"][0]
        check_peer(peer)
        directory = bundle / name
        directory.mkdir(mode=0o700)
        (directory / "before.json").write_bytes(old)
        (directory / "native.json").write_text(
            json.dumps(new, indent=2) + "\n", encoding="utf-8"
        )
        # Keep the full recreation spec private; it may contain env secrets.
        (directory / "container.json").write_text(
            json.dumps(container), encoding="utf-8"
        )
        state_record = None
        if live.name.endswith(".config.json"):
            state_path = live.with_name(live.name.replace(".config.json", ".json"))
            state = json.loads(state_path.read_bytes())
            if state["role"] != "entry" or state["peer"] != {
                key: peer[key]
                for key in ("server", "server_port", "username", "password")
            }:
                raise ValueError("Managed state and active sidecar disagree")
            (directory / "state-before.json").write_bytes(state_path.read_bytes())
            state["udp_mode"] = "native"
            if mode == "xray-native":
                state["entry_runtime"] = "xray"
            (directory / "state-native.json").write_text(
                json.dumps(state), encoding="utf-8"
            )
            state_record = {"path": str(state_path), "sha256": digest(state_path)}
        check_name = "feint-prepare-" + uuid4().hex[:12]
        mounts = []
        for mount in container["Mounts"]:
            if mount["Destination"] == "/etc/feint/interconnect.json":
                source, kind = str(directory / "native.json"), "bind"
            else:
                source, kind = mount.get("Name") or mount["Source"], mount["Type"]
            if kind not in ("volume", "bind"):
                raise ValueError("Unsupported sidecar mount")
            mounts += [
                "--mount",
                f"type={kind},src={source},dst={mount['Destination']},readonly",
            ]
        try:
            run(
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--name",
                check_name,
                "--network",
                "none",
                "--read-only",
                "--user",
                "0:0",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "NET_BIND_SERVICE",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "128m",
                "--cpus",
                "0.2",
                "--pids-limit",
                "32",
                *mounts,
                "--entrypoint",
                "sing-box",
                container["Image"],
                "check",
                "-c",
                "/etc/feint/interconnect.json",
            )
        finally:
            subprocess.run(
                ["docker", "rm", "-f", check_name], capture_output=True, timeout=15
            )
        if live.read_bytes() != old:
            raise RuntimeError(
                "Live config changed during preparation; bundle is not ready"
            )
        records.append(
            {
                "container": name,
                "container_id": container["Id"],
                "image_id": container["Image"],
                "started_at": container["State"]["StartedAt"],
                "live_path": str(live),
                "before_sha256": digest(directory / "before.json"),
                "native_sha256": digest(directory / "native.json"),
                "listen_port": config["inbounds"][0]["listen_port"],
                "peer": {"server": peer["server"], "port": peer["server_port"]},
                "managed_state": state_record,
            }
        )
        native_links.append(
            {
                "listen_port": config["inbounds"][0]["listen_port"],
                "peer": {
                    key: peer[key]
                    for key in ("server", "server_port", "username", "password")
                },
            }
        )
    native_record = None
    if mode == "xray-native":
        data = next(
            mount
            for mount in original_api["Mounts"]
            if mount["Destination"] == "/opt/sing-box"
        )
        config_path, runtime_path = (
            Path(data["Source"]) / "config.json",
            Path(data["Source"]) / "xray.json",
        )
        before, runtime_before = config_path.read_bytes(), runtime_path.read_bytes()
        canonical, runtime = json.loads(before), json.loads(runtime_before)
        if not {"HandlerService", "RoutingService"}.issubset(
            runtime.get("api", {}).get("services", [])
        ):
            raise ValueError("Native cutover requires the existing private control API")
        desired, rendered = native_xray(canonical, runtime, native_links)
        (bundle / "canonical-before.json").write_bytes(before)
        (bundle / "xray-before.json").write_bytes(runtime_before)
        (bundle / "canonical-native.json").write_text(
            json.dumps(desired, indent=2) + "\n"
        )
        (bundle / "xray-native.json").write_text(json.dumps(rendered, indent=2) + "\n")
        check_name = "feint-prepare-" + uuid4().hex[:12]
        try:
            run(
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--name",
                check_name,
                "--network",
                "none",
                "--read-only",
                "--user",
                "0:0",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "NET_BIND_SERVICE",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "128m",
                "--cpus",
                "0.2",
                "--mount",
                f"type=bind,src={bundle / 'xray-native.json'},dst=/candidate.json,readonly",
                *[
                    arg
                    for mount in original_xray["Mounts"]
                    for arg in (
                        "--mount",
                        f"type={mount['Type']},src={mount.get('Name') or mount['Source']},dst={mount['Destination']},readonly",
                    )
                ],
                "--entrypoint",
                "xray",
                original_xray["Image"],
                "run",
                "-test",
                "-c",
                "/candidate.json",
            )
        finally:
            subprocess.run(
                ["docker", "rm", "-f", check_name], capture_output=True, timeout=15
            )
        native_record = {
            "canonical_path": str(config_path),
            "runtime_path": str(runtime_path),
            "canonical_before_sha256": digest(config_path),
            "runtime_before_sha256": digest(runtime_path),
            "canonical_native_sha256": digest(bundle / "canonical-native.json"),
            "runtime_native_sha256": digest(bundle / "xray-native.json"),
            "ingress": "byte-for-byte unchanged as JSON values",
            "sdk_routes": "unchanged",
            "policy": "unchanged",
        }
        if (
            config_path.read_bytes() != before
            or runtime_path.read_bytes() != runtime_before
        ):
            raise RuntimeError("Live node configuration changed during preparation")
    for name, original in {
        "xray": original_xray,
        "vpn-node-api": original_api,
        **original_containers,
    }.items():
        current = inspect(name)
        if (
            current["Id"] != original["Id"]
            or not current["State"]["Running"]
            or current["State"]["StartedAt"] != original["State"]["StartedAt"]
        ):
            raise RuntimeError(
                "Live container changed during preparation; bundle is not ready"
            )
    for record in records:
        if digest(Path(record["live_path"])) != record["before_sha256"]:
            raise RuntimeError("Live config changed; prepare again")
        state = record["managed_state"]
        if state is not None and digest(Path(state["path"])) != state["sha256"]:
            raise RuntimeError("Managed state changed; prepare again")
    manifest = {
        "phase": "prepared-not-applied",
        "created_at": datetime.now(UTC).isoformat(),
        "xray_id": original_xray["Id"],
        "xray_started_at": original_xray["State"]["StartedAt"],
        "sidecars": records,
        "mode": mode,
        "native_xray": native_record,
        "sources": {name: value["sha256"] for name, value in sources.items()},
        "cutover": "Add the versioned Xray handler, then replace routing; retain old handlers/sidecars for drain."
        if mode == "xray-native"
        else "Recreate one interconnect sidecar only; active sidecar sessions may reconnect.",
        "rollback": "Restore canonical/state and route to the retained old handler; no Xray restart."
        if mode == "xray-native"
        else "Restore exact before config/state and recreate that sidecar with its pinned image.",
        "zero_drop_hot_swap": False,
        "apply_authorized": False,
        "prerequisites": [
            "Deploy and verify the new node/SDK hot-outbound code without restarting Xray",
            "SW live-stream switch/rollback acceptance",
            "Explicit cutover approval",
            "Keep old sidecars alive until old connections drain",
        ]
        if mode == "xray-native"
        else [],
    }
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"bundle": str(bundle), **manifest}))


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        KeyError,
        StopIteration,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"Preparation failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
