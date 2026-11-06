"""Check a scoped technical registration against an installed hot-reload node.

Technical probe users stay on the node for monitoring; no SDK wallet is created.
Credentials are held in RAM only and never printed. No existing user is modified.
"""

import argparse
import json
import subprocess
from pathlib import Path

from scripts.lib.diagnostics.probe_protocols import _probe


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", action="append", default=[])
    parser.add_argument("--client-image", default="ghcr.io/xtls/xray-core:26.7.28")
    args = parser.parse_args()
    core = json.loads(command("docker", "inspect", "xray"))[0]
    api = json.loads(command("docker", "inspect", "vpn-node-api"))[0]
    state = next(
        item["Source"]
        for item in api["Mounts"]
        if item["Destination"] == "/opt/sing-box"
    )
    canonical = json.loads((Path(state) / "config.json").read_text())
    original_users = {
        item["tag"]: item.get("users", []) for item in canonical["inbounds"]
    }
    report = []

    def request(method: str, path: str, body=None):
        script = (
            "import os,ssl,urllib.request,json; "
            "scheme='https' if os.getenv('API_USE_SSL','true').lower()=='true' else 'http'; "
            "url=scheme+'://127.0.0.1:'+os.getenv('API_PORT','8000')+"
            + repr(path)
            + "; "
            "headers={'X-API-Secret':os.environ['API_SECRET'],'Content-Type':'application/json'}; "
            "data="
            + repr(json.dumps(body).encode() if body is not None else None)
            + "; "
            "request=urllib.request.Request(url,headers=headers,data=data,method="
            + repr(method)
            + "); "
            "response=urllib.request.urlopen(request,context=ssl._create_unverified_context(),timeout=30); "
            "print(response.read().decode())"
        )
        value = command("docker", "exec", "vpn-node-api", "python", "-c", script)
        return json.loads(value) if value.strip() else None

    env = dict(item.split("=", 1) for item in api["Config"]["Env"])
    domain = env.get("SERVER_DOMAIN") or env["DOMAIN"]
    for index, route in enumerate([None, *args.route]):
        username = f"ops-hot-probe-20261004-{index}"
        created = request("POST", "/users", {"users": [{"username": username}]})
        if route:
            request("PUT", f"/outbound/{route}/user/{username}")
        configs = request("GET", f"/user/{username}/configs?server_domain={domain}")
        uri = configs["configs"]["hysteria2"]["config_url"]
        result = _probe(uri, args.client_image, 12)
        result["route"] = route or "direct"
        result["created"] = created["created"]
        report.append(result)
    after = json.loads(command("docker", "inspect", "xray"))[0]
    assert after["State"]["StartedAt"] == core["State"]["StartedAt"], (
        "Core restarted during registration"
    )
    updated = json.loads((Path(state) / "config.json").read_text())
    for inbound in updated["inbounds"]:
        current = {user["name"]: user for user in inbound.get("users", [])}
        assert all(
            current[user["name"]] == user for user in original_users[inbound["tag"]]
        )
    print(
        json.dumps(
            {"core_restarts": 0, "existing_credentials": "unchanged", "results": report}
        )
    )
    assert all(result["tcp"] and result["udp"] for result in report), (
        "Probe traffic failed"
    )


if __name__ == "__main__":
    main()
