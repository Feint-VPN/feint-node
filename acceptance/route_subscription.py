"""Exercise the real subscription endpoint for a SOCKS-routed HY2 user."""

from __future__ import annotations

import base64
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "node" / "src"))
    os.environ.update(
        DEV_MODE="true",
        HIDE_ENDPOINTS="false",
        API_SECRET="local-acceptance-secret",
        SUBSCRIPTION_ENABLED="true",
        PUBLISHED_PROTOCOLS="hysteria2",
        HYSTERIA2_PUBLIC_PORT="443",
    )

    import uvicorn
    from adapters.singbox_file_store import SingBoxFileStore
    from adapters.url_builder import UrlBuilder
    from adapters.xray_config import render_xray_config
    from api.depends.outbound import get_outbound_service
    from api.depends.runtime import get_container_runtime
    from api.depends.user import get_user_service
    from domain.outbound_service import OutboundService
    from domain.user_service import UserService
    from main import app

    with tempfile.TemporaryDirectory(prefix="feint-route-sub-") as directory:
        path = Path(directory) / "config.json"
        users = ["routed-user", "direct-user"]
        config = {
            "log": {"level": "warning"},
            "inbounds": [
                {
                    "type": "vless",
                    "tag": "vless-reality-in",
                    "listen": "127.0.0.1",
                    "listen_port": 38519,
                    "users": [
                        {"name": name, "uuid": "08bec104-27ca-4373-9388-a6dada8e94dd"}
                        for name in users
                    ],
                    "tls": {
                        "enabled": True,
                        "server_name": "example.com",
                        "reality": {
                            "handshake": {"server": "example.com", "server_port": 443},
                            "private_key": "local-acceptance-key",
                            "short_id": ["abcd"],
                        },
                    },
                },
                {
                    "type": "hysteria2",
                    "tag": "hysteria2-in",
                    "listen": "127.0.0.1",
                    "listen_port": 39092,
                    "tls": {
                        "enabled": True,
                        "certificate_path": "/tmp/local-acceptance.crt",
                        "key_path": "/tmp/local-acceptance.key",
                        "alpn": ["h3"],
                    },
                    "obfs": {"type": "salamander", "password": "test-obfs"},
                    "users": [
                        {"name": name, "password": "test-hy2-password"}
                        for name in users
                    ],
                },
            ],
            "outbounds": [
                {
                    "type": "socks",
                    "tag": "outbound:old-route",
                    "server": "127.0.0.1",
                    "server_port": 39083,
                }
            ],
            "route": {
                "rules": [
                    {"auth_user": ["routed-user"], "outbound": "outbound:old-route"}
                ],
                "final": "direct",
            },
        }
        path.write_text(json.dumps(config), encoding="utf-8")
        store = SingBoxFileStore(str(path), str(Path(directory) / "backups"))
        service = UserService(
            store,
            get_container_runtime(),
            UrlBuilder("test-public-key", "test-short-id", "example.com"),
            published_protocols=frozenset({"hysteria2"}),
            hysteria2_public_port=443,
        )
        app.dependency_overrides[get_user_service] = lambda: service
        outbound_service = OutboundService(store, get_container_runtime())
        app.dependency_overrides[get_outbound_service] = lambda: outbound_service
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app, host="127.0.0.1", port=port, log_level="error", lifespan="off"
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            assert server.started
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def request(
                method: str, route: str, body: dict, expected: int = 204
            ) -> None:
                with client.open(
                    urllib.request.Request(
                        f"http://127.0.0.1:{port}{route}",
                        data=json.dumps(body).encode(),
                        headers={
                            "Content-Type": "application/json",
                            "X-API-Secret": "local-acceptance-secret",
                        },
                        method=method,
                    )
                ) as response:
                    assert response.status == expected

            request(
                "PUT",
                "/outbound/new-route",
                {
                    "type": "socks",
                    "server": "127.0.0.1",
                    "server_port": 39084,
                    "auth_users": [],
                },
            )
            request(
                "POST",
                "/outbound/new-route/users",
                {"users": ["routed-user"]},
            )
            persisted = json.loads(path.read_text(encoding="utf-8"))
            assignments = [
                rule["outbound"]
                for rule in persisted["route"]["rules"]
                if "routed-user" in rule.get("auth_user", [])
            ]
            assert assignments == ["outbound:new-route"], assignments
            backups = sorted((Path(directory) / "backups").glob("config_*.json"))
            assert len(backups) == 2, backups
            assert [
                len(json.loads(backup.read_text(encoding="utf-8"))["outbounds"])
                for backup in backups
            ] == [1, 2]
            xray_path = Path(directory) / "xray.json"
            render_xray_config(str(path), str(xray_path))
            xray = json.loads(xray_path.read_text(encoding="utf-8"))
            xray_assignments = [
                rule["outboundTag"]
                for rule in xray["routing"]["rules"]
                if "routed-user" in rule.get("user", [])
            ]
            assert xray_assignments == ["outbound:new-route"], xray_assignments
            request("POST", "/users", {"users": [{"username": "new-user"}]}, 200)
            persisted = json.loads(path.read_text(encoding="utf-8"))
            hy2 = next(
                item for item in persisted["inbounds"] if item["type"] == "hysteria2"
            )
            assert hy2["obfs"] == {"type": "salamander", "password": "test-obfs"}
            assert hy2["tls"]["alpn"] == ["h3"]
            before = sorted((Path(directory) / "backups").glob("config_*.json"))
            request("POST", "/users", {"users": [{"username": "new-user"}]}, 200)
            assert sorted((Path(directory) / "backups").glob("config_*.json")) == before
            for name in users:
                with client.open(
                    f"http://127.0.0.1:{port}/sub/{name}?server_domain=ru.example"
                ) as response:
                    assert response.status == 200
                    links = base64.b64decode(response.read()).decode().splitlines()
                assert len(links) == 1, links
                assert links[0].startswith("hysteria2://"), links
                assert "@ru.example:443" in links[0], links
            try:
                client.open(
                    f"http://127.0.0.1:{port}/sub/unknown?server_domain=ru.example"
                )
            except urllib.error.HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError("Unknown user must return 404")
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            app.dependency_overrides.clear()
    print(
        "Route reassignment is exclusive; both users receive one HY2 subscription link"
    )


if __name__ == "__main__":
    main()
