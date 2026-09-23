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
        SUBSCRIPTION_ENABLED="true",
        PUBLISHED_PROTOCOLS="hysteria2",
        HYSTERIA2_PUBLIC_PORT="443",
    )

    import uvicorn
    from adapters.singbox_file_store import SingBoxFileStore
    from adapters.url_builder import UrlBuilder
    from api.depends.runtime import get_container_runtime
    from api.depends.user import get_user_service
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
                },
                {
                    "type": "hysteria2",
                    "tag": "hysteria2-in",
                    "listen": "127.0.0.1",
                    "listen_port": 39092,
                    "users": [
                        {"name": name, "password": "test-hy2-password"}
                        for name in users
                    ],
                },
            ],
            "outbounds": [
                {
                    "type": "socks",
                    "tag": "outbound:test-route",
                    "server": "127.0.0.1",
                    "server_port": 39083,
                }
            ],
            "route": {
                "rules": [
                    {"auth_user": ["routed-user"], "outbound": "outbound:test-route"}
                ],
                "final": "direct",
            },
        }
        path.write_text(json.dumps(config), encoding="utf-8")
        service = UserService(
            SingBoxFileStore(str(path)),
            get_container_runtime(),
            UrlBuilder("test-public-key", "test-short-id", "example.com"),
            published_protocols=frozenset({"hysteria2"}),
            hysteria2_public_port=443,
        )
        app.dependency_overrides[get_user_service] = lambda: service
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
    print("Routed and direct users each receive one HY2 subscription link")


if __name__ == "__main__":
    main()
