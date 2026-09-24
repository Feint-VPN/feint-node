"""Exercise a published node image with real cores in disposable Docker stacks.

Linux/Docker host only. No host networking, ACME, SSH changes or production data.
This is runtime acceptance, not a clean-machine installer acceptance.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from interconnect import check_public_udp

ROOT = Path(__file__).resolve().parents[1]
SINGBOX = os.environ.get(
    "SINGBOX_IMAGE", "ghcr.io/feint-vpn/feint-sing-box:v1.13.19-feint.1"
)
XRAY = "ghcr.io/xtls/xray-core:26.7.28"


def run(*args: str, timeout: int = 180) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(
            f"{args[0]} failed ({result.returncode}): {result.stderr[-3000:]}"
        )
    return result.stdout.strip()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def decode(value: str) -> str:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()


def client_outbound(uri: str) -> dict:
    """Import the API's actual share URI, without replacing its credentials or port."""
    parsed = urlsplit(uri)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    protocol = parsed.scheme
    if protocol == "vmess":
        value = json.loads(decode(uri.removeprefix("vmess://")))
        return {
            "type": "vmess",
            "server": value["add"],
            "server_port": int(value["port"]),
            "uuid": value["id"],
            "security": value["scy"],
            "transport": {"type": value["net"], "path": value["path"]},
            "tls": {
                "enabled": True,
                "server_name": value["sni"],
                "certificate_path": "/state/fullchain.pem",
            },
        }
    result = {"type": protocol, "server": parsed.hostname, "server_port": parsed.port}
    if protocol == "ss":
        method, password = decode(parsed.username).split(":", 1)
        return result | {"type": "shadowsocks", "method": method, "password": password}
    tls = {"enabled": True, "server_name": query["sni"]}
    if protocol == "vless":
        result |= {"uuid": parsed.username, "flow": query["flow"]}
        tls |= {
            "utls": {"enabled": True, "fingerprint": query["fp"]},
            "reality": {
                "enabled": True,
                "public_key": query["pbk"],
                "short_id": query["sid"],
            },
        }
    else:
        result["password"] = unquote(parsed.username)
        tls["certificate_path"] = "/state/fullchain.pem"
        if "alpn" in query:
            tls["alpn"] = query["alpn"].split(",")
    return result | {"tls": tls}


def exercise(
    image: str, core: str, vless_client: str, min_client_version: str = ""
) -> None:
    name = f"feint-acceptance-{uuid.uuid4().hex[:8]}"
    client_name = f"{name}-client"
    runtime_name = f"{name}-runtime"
    api_port, socks_port = free_port(), free_port()
    secret = secrets.token_urlsafe(32)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def api(method: str, path: str, body=None, expected=200, auth=True):
        headers = {"X-API-Secret": secret} if auth else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"http://127.0.0.1:{api_port}{path}",
            method=method,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            response = opener.open(request, timeout=90)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            value = response.read().decode()
            assert response.status == expected, (method, path, response.status, value)
            return (
                json.loads(value)
                if "json" in response.headers.get("Content-Type", "")
                else value
            )

    def ready():
        last = None
        for _ in range(40):
            try:
                last = api("GET", "/status")
                if last["status"] == "ok":
                    return
            except (OSError, AssertionError):
                pass
            time.sleep(1)
        raise AssertionError(f"{core} did not become healthy: {last}")

    with tempfile.TemporaryDirectory(prefix=name) as directory:
        root = Path(directory)
        state = root / "state"
        state.mkdir()
        os.chown(state, 1000, 1000)
        state.chmod(0o700)
        env = {
            "DOMAIN": "node.test",
            "REALITY_SERVER_NAME": "www.cloudflare.com",
            "VLESS_PORT": "38443",
            "VMESS_PORT": "38080",
            "TROJAN_PORT": "38444",
            "HYSTERIA2_PORT": "38445",
            "SHADOWSOCKS_PORT": "38446",
            "REVERSE_PROXY_PORT": "38447",
            "SHADOWSOCKS_METHOD": "2022-blake3-aes-256-gcm",
            "SHADOWSOCKS_PASSWORD": base64.b64encode(secrets.token_bytes(32)).decode(),
            "CLASH_API_SECRET": secrets.token_urlsafe(24),
            "REALITY_SHORT_ID": secrets.token_hex(8),
        }
        keys = run("docker", "run", "--rm", SINGBOX, "generate", "reality-keypair")
        key_values = dict(
            line.split(":", 1) for line in keys.splitlines() if ":" in line
        )
        env["REALITY_PRIVATE_KEY"] = key_values["PrivateKey"].strip()
        env["REALITY_PUBLIC_KEY"] = key_values["PublicKey"].strip()
        run(
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "2",
            "-subj",
            "/CN=node.test",
            "-addext",
            "subjectAltName=DNS:node.test",
            "-keyout",
            str(state / "privkey.pem"),
            "-out",
            str(state / "fullchain.pem"),
        )
        (state / "privkey.pem").chmod(0o600)
        template = "sing-box.json.tpl" if core == "sing-box" else "vless.json.tpl"
        source = (ROOT / "templates" / template).read_text(encoding="utf-8")
        for key, value in env.items():
            source = source.replace("{{" + key + "}}", value)
        source = source.replace("/etc/letsencrypt/live/node.test", "/state").replace(
            "/opt/sing-box", "/state"
        )
        config = json.loads(source)
        write_json(state / "config.json", config)
        write_json(
            state / "geoip.json",
            {"version": 3, "rules": [{"ip_cidr": ["203.0.113.0/24"]}]},
        )
        mount = f"{state}:/state"
        run(
            "docker",
            "run",
            "--rm",
            "-v",
            mount,
            SINGBOX,
            "rule-set",
            "compile",
            "/state/geoip.json",
            "-o",
            "/state/geoip-ru.srs",
        )
        if core == "xray":
            run(
                "docker",
                "run",
                "--rm",
                "--user",
                "0",
                "-e",
                f"XRAY_REALITY_MIN_CLIENT_VERSION={min_client_version}",
                "-v",
                mount,
                "--entrypoint",
                "python",
                image,
                "-m",
                "adapters.xray_config",
                "/state/config.json",
                "/state/xray.json",
            )
        env |= {
            "API_PORT": str(api_port),
            "API_SECRET": secret,
            "API_USE_SSL": "false",
            "HIDE_ENDPOINTS": "true",
            "SUBSCRIPTION_ENABLED": "true",
            "SERVER_DOMAIN": "node.test",
            "CONFIG_PATH": "/state/config.json",
            "BACKUP_DIR": "/state/backups",
            "VPN_RUNTIME": core,
            "XRAY_REALITY_MIN_CLIENT_VERSION": min_client_version,
            "VPN_RUNTIME_CONTAINER_NAME": runtime_name,
            "SINGBOX_CONTAINER_NAME": runtime_name,
            "XRAY_CONFIG_PATH": "/state/xray.json",
            "V2RAY_API_ADDRESS": "sing-box:10085",
            "TRAFFIC_STATE_PATH": "/state/traffic.json",
            "ENV_FILE_PATH": "/state/settings.env",
            "SUB_URI_TEMPLATE": "Feint | {Protocol} | {username}",
        }
        (state / "settings.env").write_text("", encoding="utf-8")
        for path in state.iterdir():
            os.chown(path, 1000, 1000)
        (root / ".env.local").write_text(
            "\n".join(f"{key}={value}" for key, value in env.items()), encoding="utf-8"
        )
        compose = {
            "name": name,
            "services": {
                "sing-box": {
                    "image": SINGBOX if core == "sing-box" else XRAY,
                    "container_name": runtime_name,
                    "user": "1000:1000",
                    "volumes": [mount],
                    "command": ["run", "-c", "/state/config.json"]
                    if core == "sing-box"
                    else ["run", "-config", "/state/xray.json"],
                    "networks": {"default": {"aliases": ["node.test"]}},
                },
                "vpn-node-api": {
                    "image": image,
                    "user": "1000:1000",
                    "group_add": [str(os.stat("/var/run/docker.sock").st_gid)],
                    "volumes": [mount, "/var/run/docker.sock:/var/run/docker.sock"],
                    "env_file": [str(root / ".env.local")],
                    "ports": [f"127.0.0.1:{api_port}:{api_port}"],
                    "command": [
                        "uvicorn",
                        "main:app",
                        "--host",
                        "0.0.0.0",
                        "--port",
                        str(api_port),
                    ],
                },
            },
        }
        compose_path = root / "docker-compose.yml"
        write_json(compose_path, compose)
        command = ("docker", "compose", "-f", str(compose_path))
        try:
            run(*command, "up", "-d", timeout=300)
            ready()
            api("GET", "/users", expected=404, auth=False)
            api("GET", "/unknown", expected=404)
            api("GET", "/health")
            api("POST", "/user", {"username": "!"}, expected=422)
            user = api("POST", "/user", {"username": "acceptance-user"}, expected=201)
            expected_protocols = (
                {"vless", "hysteria2"}
                if core == "xray"
                else {"vless", "vmess", "trojan", "hysteria2", "shadowsocks"}
            )
            assert set(user["protocols"]) == expected_protocols
            api("POST", "/user", {"username": "acceptance-user"}, expected=409)
            assert (
                api("POST", "/users", {"users": [{"username": "second-user"}]})[
                    "created"
                ]
                == 1
            )
            before = sorted((state / "backups").iterdir())
            assert (
                api("POST", "/users", {"users": [{"username": "second-user"}]})[
                    "created"
                ]
                == 0
            )
            api("DELETE", "/user/absent-user", expected=404)
            assert sorted((state / "backups").iterdir()) == before
            page = api("GET", "/users?limit=1&skip=1")
            assert page["total"] == 2 and len(page["users"]) == 1
            assert api("GET", "/user/acceptance-user")["uuid"] == user["uuid"]
            api("GET", "/users?limit=0", expected=422)
            api("GET", "/sub/settings")
            api(
                "PUT",
                "/sub/settings",
                {"sub_uri_template": "Feint | {Protocol} | {username}"},
            )
            links = decode(api("GET", "/sub/acceptance-user")).splitlines()
            assert len(links) == len(expected_protocols)
            configs = api(
                "GET", "/user/acceptance-user/configs?server_domain=node.test"
            )["configs"]
            assert {
                protocol for protocol, value in configs.items() if value
            } == expected_protocols
            for uri in links:
                outbound = client_outbound(uri)
                native = outbound["type"] == "vless" and vless_client == "xray"
                write_json(
                    state / "client.json",
                    {
                        "log": {"level": "warn"},
                        "inbounds": [
                            {
                                "type": "socks",
                                "listen": "0.0.0.0",
                                "listen_port": socks_port,
                            }
                        ],
                        "outbounds": [outbound],
                    },
                )
                if native:
                    tls = outbound["tls"]
                    write_json(
                        state / "client.json",
                        {
                            "log": {"loglevel": "warning"},
                            "inbounds": [
                                {
                                    "protocol": "socks",
                                    "listen": "0.0.0.0",
                                    "port": socks_port,
                                    "settings": {"auth": "noauth", "udp": True},
                                }
                            ],
                            "outbounds": [
                                {
                                    "protocol": "vless",
                                    "settings": {
                                        "vnext": [
                                            {
                                                "address": outbound["server"],
                                                "port": outbound["server_port"],
                                                "users": [
                                                    {
                                                        "id": outbound["uuid"],
                                                        "flow": outbound["flow"],
                                                        "encryption": "none",
                                                    }
                                                ],
                                            }
                                        ]
                                    },
                                    "streamSettings": {
                                        "network": "tcp",
                                        "security": "reality",
                                        "realitySettings": {
                                            "serverName": tls["server_name"],
                                            "fingerprint": tls["utls"]["fingerprint"],
                                            "publicKey": tls["reality"]["public_key"],
                                            "shortId": tls["reality"]["short_id"],
                                        },
                                    },
                                }
                            ],
                        },
                    )
                run(
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    client_name,
                    "--user",
                    "1000:1000",
                    "--network",
                    name + "_default",
                    "-v",
                    mount,
                    "-p",
                    f"127.0.0.1:{socks_port}:{socks_port}",
                    "-p",
                    f"127.0.0.1:{socks_port}:{socks_port}/udp",
                    XRAY if native else SINGBOX,
                    "run",
                    "-config" if native else "-c",
                    "/state/client.json",
                )
                time.sleep(1)
                page = run(
                    "curl",
                    "--silent",
                    "--show-error",
                    "--fail",
                    "--max-time",
                    "25",
                    "--noproxy",
                    "",
                    "--socks5-hostname",
                    f"127.0.0.1:{socks_port}",
                    "https://example.com",
                )
                assert "Example Domain" in page
                check_public_udp(socks_port)
                run("docker", "rm", "-f", client_name)
                print(
                    f"PASS {core}/{outbound['type']}: subscription -> HTTPS + UDP",
                    flush=True,
                )
            time.sleep(6)
            traffic = api("GET", "/user/acceptance-user/stats")
            assert (
                traffic["available"]
                and traffic["upload_bytes"] > 0
                and traffic["download_bytes"] > 0
            ), traffic
            assert any(
                item["username"] == "acceptance-user" for item in api("GET", "/stats")
            )
            run(*command, "restart", "vpn-node-api")
            ready()
            assert (
                api("GET", "/user/acceptance-user/stats")["total_bytes"]
                >= traffic["total_bytes"]
            )
            for route in ("first", "second"):
                api(
                    "PUT",
                    f"/outbound/{route}",
                    {"type": "socks", "server": "127.0.0.1", "server_port": 39083},
                    expected=204,
                )
            api("PUT", "/outbound/first/user/acceptance-user", expected=204)
            api(
                "POST",
                "/outbound/second/users",
                {"users": ["acceptance-user", "second-user"]},
                expected=204,
            )
            saved = json.loads((state / "config.json").read_text())
            assert [
                rule["outbound"]
                for rule in saved["route"]["rules"]
                if "acceptance-user" in rule.get("auth_user", [])
            ] == ["outbound:second"]
            api("DELETE", "/outbound/second", expected=409)
            api("PUT", "/outbound/second/user/absent-user", expected=404)
            api("DELETE", "/outbound/second/user/acceptance-user", expected=204)
            api(
                "DELETE",
                "/outbound/second/users",
                {"users": ["second-user"]},
                expected=204,
            )
            for route in ("first", "second"):
                api("DELETE", f"/outbound/{route}", expected=204)
            run("docker", "rename", runtime_name, runtime_name + "-unavailable")
            previous = (state / "config.json").read_bytes()
            try:
                api("POST", "/user", {"username": "rollback-user"}, expected=500)
                assert (state / "config.json").read_bytes() == previous
            finally:
                run("docker", "rename", runtime_name + "-unavailable", runtime_name)
            ready()
            api("GET", "/user/rollback-user", expected=404)
            for username in ("acceptance-user", "second-user"):
                api("DELETE", f"/user/{username}")
                api("GET", f"/sub/{username}", expected=404)
            assert api("GET", "/users")["total"] == 0
            print(
                f"PASS {core}: CRUD, bulk idempotency, paging, stats persistence, routes, rollback",
                flush=True,
            )
        except (AssertionError, OSError, RuntimeError, subprocess.SubprocessError):
            print(run(*command, "logs", "--no-color", "--tail", "40"), flush=True)
            client_log = subprocess.run(
                ["docker", "logs", "--tail", "20", client_name],
                capture_output=True,
                text=True,
            )
            print(client_log.stdout + client_log.stderr, flush=True)
            raise
        finally:
            subprocess.run(["docker", "rm", "-f", client_name], capture_output=True)
            run(*command, "down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        required=True,
        help="Published node image, preferably pinned by digest",
    )
    parser.add_argument("--core", choices=["sing-box", "xray", "both"], default="both")
    parser.add_argument(
        "--vless-client", choices=["sing-box", "xray"], default="sing-box"
    )
    parser.add_argument("--reality-min-client-version", default="")
    args = parser.parse_args()
    if os.name != "posix":
        raise SystemExit("Run on the explicitly selected Linux test host")
    for runtime in ["sing-box", "xray"] if args.core == "both" else [args.core]:
        exercise(
            args.image, runtime, args.vless_client, args.reality_min_client_version
        )
