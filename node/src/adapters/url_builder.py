"""Adapter: build protocol-specific client connection URLs."""

import base64
import json
import zlib
from urllib.parse import quote, urlencode

from domain.ports import IConfigUrlBuilder


class UrlBuilder(IConfigUrlBuilder):
    def __init__(self, public_key: str, short_id: str, server_name: str) -> None:
        self.public_key = public_key
        self.short_id = short_id
        self.server_name = server_name

    def vless_url(
        self,
        uuid: str,
        domain: str,
        port: int,
    ) -> str:
        params = urlencode(
            {
                "type": "tcp",
                "security": "reality",
                "sni": self.server_name,
                "fp": "chrome",
                "pbk": self.public_key,
                "sid": self.short_id,
                "spx": "/",
                "flow": "xtls-rprx-vision",
            }
        )
        return f"vless://{uuid}@{domain}:{port}?{params}#{quote(domain)}"

    def amnezia_url(self, uuid: str, domain: str, port: int) -> str:
        xray = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "listen": "127.0.0.1",
                    "port": 10808,
                    "protocol": "socks",
                    "settings": {"auth": "noauth", "udp": True},
                    "tag": "socks",
                }
            ],
            "outbounds": [
                {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": domain,
                                "port": port,
                                "users": [
                                    {
                                        "encryption": "none",
                                        "flow": "xtls-rprx-vision",
                                        "id": uuid,
                                    }
                                ],
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "realitySettings": {
                            "fingerprint": "chrome",
                            "publicKey": self.public_key,
                            "serverName": self.server_name,
                            "shortId": self.short_id,
                            "spiderX": "/",
                        },
                        "security": "reality",
                    },
                    "tag": "proxy",
                }
            ],
        }
        profile = {
            "containers": [
                {
                    "container": "amnezia-xray",
                    "xray": {
                        "isThirdPartyConfig": True,
                        "last_config": json.dumps(xray, separators=(",", ":")),
                    },
                }
            ],
            "defaultContainer": "amnezia-xray",
            "description": domain,
            "hostName": domain,
        }
        payload = json.dumps(profile, separators=(",", ":")).encode()
        compressed = len(payload).to_bytes(4, "big") + zlib.compress(payload, 8)
        return f"vpn://{base64.urlsafe_b64encode(compressed).decode().rstrip('=')}"

    def vmess_url(
        self, uuid: str, domain: str, port: int, path: str = "/vmess", label: str = ""
    ) -> str:
        # VMess share link: vmess://base64(json)  — NO fragment allowed.
        # Display name goes in the "ps" field inside the JSON.
        cfg = json.dumps(
            {
                "v": "2",
                "ps": label or domain,
                "add": domain,
                "port": str(port),
                "id": uuid,
                "aid": "0",
                "scy": "auto",
                "net": "ws",
                "type": "none",
                "host": domain,
                "path": path,
                "tls": "tls",
                "sni": domain,
                "alpn": "",
            },
            separators=(",", ":"),
        )
        return f"vmess://{base64.urlsafe_b64encode(cfg.encode()).decode()}"

    def trojan_url(self, password: str, domain: str, port: int) -> str:
        params = urlencode(
            {"type": "tcp", "security": "tls", "sni": domain, "alpn": "h2,http/1.1"}
        )
        # quote with safe="" to fully encode  /  +  =  in the password
        return f"trojan://{quote(password, safe='')}@{domain}:{port}?{params}#{quote(domain)}"

    def hysteria2_url(self, password: str, domain: str, port: int) -> str:
        params = urlencode({"sni": domain, "insecure": "0"})
        return f"hysteria2://{quote(password, safe='')}@{domain}:{port}?{params}#{quote(domain)}"

    def shadowsocks_url(
        self, password: str, port: int, method: str, domain: str = "127.0.0.1"
    ) -> str:
        # For 2022-blake3 multi-user mode the password is already "server_psk:user_psk"
        userinfo = (
            base64.urlsafe_b64encode(f"{method}:{password}".encode())
            .decode()
            .rstrip("=")
        )
        return f"ss://{userinfo}@{domain}:{port}#Shadowsocks"
