"""Explicit route-only native cutover/rollback with the node API paused.

Run inside the published node image with the prepared bundle and node volume
mounted at their original paths. Never call reload(): a failed hot eligibility
check must stop, not fall back to restarting subscriber sessions.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import grpc
from adapters.docker_runtime import XrayDockerRuntime
from adapters.xray_socks import socks_outbound
from docker.errors import DockerException
from domain.errors import SingBoxReloadError
from domain.models import SingBoxConfig


def replace_route(canonical: dict, runtime: dict, tag: str, outbound: dict):
    desired, rendered = copy.deepcopy(canonical), copy.deepcopy(runtime)
    current = next(item for item in desired["outbounds"] if item["tag"] == tag)
    desired["outbounds"][desired["outbounds"].index(current)] = outbound
    matches = [
        item
        for item in rendered["outbounds"]
        if item["tag"] == tag or item["tag"].startswith(tag + ".native-")
    ]
    if len(matches) != 1:
        raise ValueError("Expected one active runtime handler for the selected route")
    old = matches[0]
    new = socks_outbound(tag, outbound)
    rendered["outbounds"][rendered["outbounds"].index(old)] = new
    for rule in rendered["routing"]["rules"]:
        if rule.get("outboundTag") == old["tag"]:
            rule["outboundTag"] = new["tag"]
    SingBoxConfig.model_validate(desired)
    return desired, rendered


def write(path: Path, value: dict):
    owner = path.stat() if path.exists() else None
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".cutover-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file)
            file.flush()
            os.fsync(file.fileno())
            if owner is not None:
                os.fchown(file.fileno(), owner.st_uid, owner.st_gid)
            os.fchmod(file.fileno(), 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare_handler_import(runtime_path: Path) -> Path:
    path = runtime_path.with_suffix(".outbounds.json")
    path.touch(mode=0o600, exist_ok=False)
    owner = runtime_path.stat()
    os.chown(path, owner.st_uid, owner.st_gid)
    return path


def retained_handler(journal, identity, old, native, prepared, live):
    if (
        journal["phase"] != "rolled-back"
        or journal["core_identity"] != list(identity)
        or journal["canonical_before"] != old
        or journal["route"] != old["tag"]
        or native not in prepared["outbounds"]
        or native["tag"] not in live
        or journal["runtime_before"]["tag"] not in live
    ):
        raise ValueError("Retained native handler ownership is no longer proven")
    return native


async def main(args):
    if args.reuse_retained and args.operation == "rollback":
        raise ValueError("Retained reuse is only valid for preflight/apply")
    if os.geteuid() != 0 or args.bundle.is_symlink():
        raise ValueError("Use root and a private prepared bundle")
    stat = args.bundle.stat()
    if stat.st_uid != 0 or stat.st_mode & 0o077:
        raise ValueError("Prepared bundle must be root-owned and private")
    manifest = json.loads((args.bundle / "manifest.json").read_text())
    if manifest["mode"] != "xray-native":
        raise ValueError("Expected native-Xray preparation")
    record = next(r for r in manifest["sidecars"] if r["container"] == args.sidecar)
    directory = args.bundle / args.sidecar
    canonical_path = Path(manifest["native_xray"]["canonical_path"])
    runtime_path = Path(manifest["native_xray"]["runtime_path"])
    canonical, before = (
        json.loads(path.read_text()) for path in (canonical_path, runtime_path)
    )
    runtime = XrayDockerRuntime(
        str(canonical_path),
        str(runtime_path),
        runtime_config_path=args.runtime_path,
        api_listen=before["api"]["listen"],
        api_address=before["api"]["listen"],
    )
    container = runtime._get_container()
    identity = (container.id, container.attrs["State"]["StartedAt"])
    journal_path = directory / "cutover.json"
    original = json.loads((directory / "before.json").read_text())
    peer = original["outbounds"][0]
    state_record = record["managed_state"]
    state_path = Path(state_record["path"]) if state_record else None
    state_before = json.loads(state_path.read_text()) if state_path else None
    state_after = copy.deepcopy(state_before)
    if args.operation == "rollback":
        journal = json.loads(journal_path.read_text())
        if list(identity) != journal["core_identity"]:
            raise ValueError(
                "Core changed; retained-handler rollback is no longer proven"
            )
        tag = journal["route"]
        outbound = journal["canonical_before"]
        live = await runtime.control.outbound_tags()
        old = journal["runtime_before"]
        if old["tag"] not in live:
            raise ValueError("Retained rollback handler is no longer available")
        runtime._resident_outbounds[old["tag"]] = old
        if state_path:
            state_after = journal["state_before"]
    else:
        if identity != (manifest["xray_id"], manifest["xray_started_at"]):
            raise ValueError("Core changed since preparation")
        if journal_path.exists() and not args.reuse_retained:
            raise ValueError("This bundle already has a cutover journal")
        matching = [
            item
            for item in canonical["outbounds"]
            if item.get("type") == "socks"
            and item.get("server") == "127.0.0.1"
            and item.get("server_port") == record["listen_port"]
            and item["tag"].startswith("outbound:")
        ]
        if len(matching) != 1:
            raise ValueError("Canonical route no longer uses the prepared sidecar")
        tag = matching[0]["tag"]
        outbound = matching[0] | {
            key: peer[key] for key in ("server", "server_port", "username", "password")
        }
        if state_path:
            if state_before["peer"] != {
                key: peer[key]
                for key in ("server", "server_port", "username", "password")
            }:
                raise ValueError("Managed peer drifted")
            state_after.update(udp_mode="native", entry_runtime="xray")
        if args.reuse_retained:
            prior = json.loads(journal_path.read_text())
            prepared = (args.bundle / "xray-native.json").read_bytes()
            if (
                hashlib.sha256(prepared).hexdigest()
                != manifest["native_xray"]["runtime_native_sha256"]
                or prior["state_before"] != state_before
                or prior["runtime_before"] not in before["outbounds"]
            ):
                raise ValueError("Retained handler preparation or route drifted")
            native = retained_handler(
                prior,
                identity,
                matching[0],
                socks_outbound(tag, outbound),
                json.loads(prepared),
                await runtime.control.outbound_tags(),
            )
            runtime._resident_outbounds[native["tag"]] = native
        journal = {
            "core_identity": list(identity),
            "route": tag,
            "canonical_before": matching[0],
            "runtime_before": next(o for o in before["outbounds"] if o["tag"] == tag),
            "state_before": state_before,
            "phase": "prepared",
        }
    desired, candidate = replace_route(canonical, before, tag, outbound)
    if (
        candidate["inbounds"] != before["inbounds"]
        or runtime._additions(candidate) != []
    ):
        raise ValueError("Mutation is not an eligible route-only hot update")
    api = runtime._client.containers.get("vpn-node-api")
    if args.operation != "preflight" and api.attrs["State"]["Running"]:
        raise ValueError("Pause the node API to exclude concurrent config writers")
    check_path = runtime_path.with_suffix(".cutover-check.json")
    outbound_path = None
    try:
        write(check_path, candidate)
        os.chown(check_path, 1000, 1000)
        result = container.exec_run(
            [
                "xray",
                "run",
                "-test",
                "-c",
                str(Path(args.runtime_path).with_suffix(".cutover-check.json")),
            ]
        )
        if result.exit_code:
            raise ValueError("Xray rejected the route-only candidate")
        if args.operation == "preflight":
            print(json.dumps({"phase": "preflight-passed", "route": tag}))
            return
        if args.operation == "apply":
            write(journal_path, journal)
        try:
            outbound_path = await asyncio.to_thread(
                prepare_handler_import, runtime_path
            )
            write(runtime_path, candidate)
            await runtime._apply_hot(container, candidate, [])
            write(canonical_path, desired)
            if state_path:
                write(state_path, state_after)
        except Exception as error:
            write(runtime_path, before)
            if runtime._applied_config != before:
                await runtime._apply_hot(container, before, [])
            write(canonical_path, canonical)
            if state_path:
                write(state_path, state_before)
            journal["phase"] = "failed-restored"
            journal["failure"] = str(error)
            write(journal_path, journal)
            raise
        container.reload()
        if identity != (container.id, container.attrs["State"]["StartedAt"]):
            raise ValueError("Unexpected core identity change")
        journal["phase"] = "applied" if args.operation == "apply" else "rolled-back"
        write(journal_path, journal)
        print(
            json.dumps(
                {
                    "phase": journal["phase"],
                    "route": tag,
                    "hy2_restarted": False,
                    "old_sidecar_retained": True,
                }
            )
        )
    finally:
        check_path.unlink(missing_ok=True)
        if outbound_path is not None:
            outbound_path.unlink(missing_ok=True)
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--sidecar", required=True)
    parser.add_argument("--runtime-path", required=True)
    parser.add_argument("--reuse-retained", action="store_true")
    parser.add_argument(
        "--operation", choices=("preflight", "apply", "rollback"), required=True
    )
    try:
        asyncio.run(main(parser.parse_args()))
    except (
        OSError,
        ValueError,
        KeyError,
        StopIteration,
        DockerException,
        grpc.RpcError,
        SingBoxReloadError,
    ) as error:
        print(f"Route cutover failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
