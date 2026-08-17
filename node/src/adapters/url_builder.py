"""Adapter: build protocol-specific client connection URLs."""

import base64
import json
from urllib.parse import quote, urlencode

from domain.ports import IConfigUrlBuilder


class UrlBuilder(IConfigUrlBuilder):
    def vless_url(
        self,
        uuid: str,
        domain: str,
        port: int,
    ) -> str:
        params = urlencode(
            {
                "type": "tcp",
                "security": "tls",
                "sni": domain,
                "flow": "xtls-rprx-vision",
            }
        )
        return f"vless://{uuid}@{domain}:{port}?{params}#{quote(domain)}"

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
