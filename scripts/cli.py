#!/usr/bin/env python3
"""Node-specific operator commands; shared quality stages belong to pipeline."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {
    "install": "install/install.sh",
    "update": "update/update.sh",
    "setup-ssh": "install/setup-ssh.sh",
    "compose": "runtime/compose.py",
    "ports": "runtime/ports.sh",
    "subscription": "runtime/get-sub.sh",
    "sync-config": "runtime/sync-singbox.py",
    "udp-redirect": "runtime/udp_redirect.sh",
    "apply-hot-reload": "runtime/apply_hot_reload.py",
    "hot-reload-canary": "runtime/hot_reload_canary.py",
    "hot-reload-overlay": "runtime/hot_reload_overlay.py",
    "tuic-overlay": "runtime/tuic_overlay.py",
    "tuic-stage": "runtime/tuic_stage.py",
    "interconnect": "interconnect/interconnect.sh",
    "configure-interconnect": "interconnect/configure_interconnect.py",
    "manage-interconnect": "interconnect/manage_interconnect.py",
    "prepare-interconnect": "interconnect/prepare_interconnect.py",
    "cutover-interconnect": "interconnect/cutover_interconnect.py",
    "switch-interconnect-route": "interconnect/switch_interconnect_route.py",
    "diagnose": "diagnostics/diagnose.sh",
    "probe": "diagnostics/probe_protocols.py",
    "probe-tuic": "diagnostics/probe_tuic.py",
    "remote-chain-probe": "diagnostics/remote_chain_probe.py",
    "remote-hot-probe": "diagnostics/remote_hot_probe.py",
    "test-route": "diagnostics/test_route_deploy.py",
    "wan-path": "diagnostics/wan_path_smoke.py",
    "wan-transport": "diagnostics/wan_transport_probe.py",
    "install-probe-subscription": "diagnostics/install_subscription.py",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", *COMMANDS])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == "list":
        listing = argparse.ArgumentParser(prog=f"{parser.prog} list")
        listing.add_argument("--json", action="store_true")
        options = listing.parse_args(args.arguments)
        print(json.dumps(COMMANDS, indent=2) if options.json else "\n".join(COMMANDS))
        return 0
    usage = {
        "test-route": "Reads an explicitly authorized target JSON object from stdin; use pipeline server-test.",
        "wan-path": "Reads an explicitly authorized test-host target JSON object from stdin; use pipeline server-test.",
        "prepare-interconnect": "Reads target/files JSON from stdin on the selected host; use pipeline server-test.",
        "sync-config": "TEMPLATE CURRENT OUTPUT; runtime values are read from the environment.",
        "udp-redirect": "ensure|remove PUBLIC_UDP_PORT RUNTIME_UDP_PORT; changes host NAT rules.",
        "subscription": "USER [DOMAIN_OVERRIDE]; prints private connection URLs. Reads ENV_FILE or installation .env.local.",
        "interconnect": "Compose arguments for the optional interconnect stack; requires root .env.local and interconnect.json.",
    }
    if args.arguments in (["--help"], ["-h"]) and args.command in usage:
        print(usage[args.command])
        return 0
    script = ROOT / "scripts/lib" / COMMANDS[args.command]
    interpreter = "bash" if script.suffix == ".sh" else sys.executable
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "services/node/src"), str(ROOT), environment.get("PYTHONPATH", "")]
    )
    try:
        return subprocess.call(
            [interpreter, str(script), *args.arguments], cwd=ROOT, env=environment
        )
    except FileNotFoundError:
        parser.exit(127, f"Required interpreter is unavailable: {interpreter}\n")


if __name__ == "__main__":
    raise SystemExit(main())
