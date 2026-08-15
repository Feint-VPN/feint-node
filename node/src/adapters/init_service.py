"""Adapter: node initialization (DNS, secrets, certbot, docker-compose)."""

import asyncio
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path

from domain.initialization import NodeSecrets
from utils.crypto import (
    generate_reality_keypair,
    generate_reality_short_id,
    generate_secure_password,
)
from utils.logging_config import get_logger

logger = get_logger(__name__)

_ENV = Path("/opt/vpn-node/.env.local")
_COMPOSE = Path("/opt/vpn-node/docker-compose.yml")


class InitService:
    async def initialize(self, domain: str, email: str, server_ip: str) -> dict:
        errors: list[str] = []
        steps = {
            k: False for k in ("dns", "secrets", "env", "config", "cert", "docker")
        }
        cfg = None

        # 1. DNS
        if not await self._validate_dns(domain, server_ip):
            errors.append(f"Domain {domain} does not resolve to {server_ip}")
            return self._result(False, "Domain validation failed", steps, errors)
        steps["dns"] = True

        # 2. Secrets
        try:
            cfg = self._generate_secrets(domain, server_ip, email)
            steps["secrets"] = True
        except Exception as e:
            errors.append(f"Secret generation error: {e}")
            return self._result(False, "Secret generation failed", steps, errors)

        # 3. .env.local
        try:
            self._write_env(cfg)
            steps["env"] = True
        except Exception as e:
            errors.append(f"Failed to write .env.local: {e}")
            return self._result(False, "Env file creation failed", steps, errors, cfg)

        # 4. config.json
        try:
            self._write_singbox_config(cfg)
            steps["config"] = True
        except Exception as e:
            errors.append(f"Failed to create sing-box config: {e}")
            return self._result(False, "Config creation failed", steps, errors, cfg)

        # 5. Certificate (non-fatal)
        steps["cert"] = await self._certbot(domain, email)
        if not steps["cert"]:
            errors.append("Failed to obtain SSL certificate")

        # 6. Docker compose (non-fatal)
        steps["docker"] = await self._docker_up()
        if not steps["docker"]:
            errors.append("Failed to start Docker containers")

        success = all(steps.values()) and not errors
        return self._result(
            success,
            "Node initialized successfully"
            if success
            else "Initialization completed with errors",
            steps,
            errors,
            cfg,
        )

    async def generate_secrets(self) -> NodeSecrets:
        kp = generate_reality_keypair()
        return NodeSecrets(
            api_secret=generate_secure_password(32),
            reality_private_key=kp.private_key,
            reality_public_key=kp.public_key,
            reality_short_id=generate_reality_short_id(),
            shadowsocks_password=generate_secure_password(32),
        )

    async def validate_domain(self, domain: str, server_ip: str) -> bool:
        return await self._validate_dns(domain, server_ip)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _generate_secrets(self, domain: str, server_ip: str, email: str) -> dict:
        kp = generate_reality_keypair()
        return {
            "api_secret": generate_secure_password(32),
            "server_domain": domain,
            "server_ip": server_ip,
            "certbot_email": email,
            "tls_enabled": True,
            "tls_cert_path": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
            "tls_key_path": f"/etc/letsencrypt/live/{domain}/privkey.pem",
            "reality_private_key": kp.private_key,
            "reality_short_id": generate_reality_short_id(),
            "reality_server_name": "www.microsoft.com",
            "shadowsocks_password": generate_secure_password(32),
            "shadowsocks_method": "2022-blake3-aes-256-gcm",
            "hysteria2_up_mbps": 1000,
            "hysteria2_down_mbps": 1000,
            "clash_api_secret": generate_secure_password(32),
            "v2ray_api_listen": "0.0.0.0:10085",
        }

    def _write_env(self, cfg: dict) -> None:
        # Read existing .env.local to preserve port vars set by generate-ports.sh
        existing: dict[str, str] = {}
        if _ENV.exists():
            for line in _ENV.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()

        ts = datetime.now(tz=UTC).isoformat()
        updates = {
            "API_SECRET": cfg["api_secret"],
            "SERVER_DOMAIN": cfg["server_domain"],
            "SERVER_IP": cfg["server_ip"],
            "TLS_ENABLED": str(cfg["tls_enabled"]).lower(),
            "TLS_CERT_PATH": cfg["tls_cert_path"],
            "TLS_KEY_PATH": cfg["tls_key_path"],
            "CERTBOT_EMAIL": cfg["certbot_email"],
            "REALITY_PRIVATE_KEY": cfg["reality_private_key"],
            "REALITY_SHORT_ID": cfg["reality_short_id"],
            "REALITY_SERVER_NAME": cfg["reality_server_name"],
            "SHADOWSOCKS_PASSWORD": cfg["shadowsocks_password"],
            "SHADOWSOCKS_METHOD": cfg["shadowsocks_method"],
            "HYSTERIA2_UP_MBPS": str(cfg["hysteria2_up_mbps"]),
            "HYSTERIA2_DOWN_MBPS": str(cfg["hysteria2_down_mbps"]),
            "CLASH_API_SECRET": cfg["clash_api_secret"],
            "CLASH_API_URL": "http://host.docker.internal:9090",
            "V2RAY_API_ADDRESS": "host.docker.internal:10085",
        }
        existing.update(updates)

        lines = [f"# Generated at: {ts}"] + [f"{k}={v}" for k, v in existing.items()]
        _ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_singbox_config(self, cfg: dict) -> None:
        # Use ports from env (set by generate-ports.sh) with sensible fallbacks
        vless_port = int(os.getenv("VLESS_PORT", "8443"))
        vmess_port = int(os.getenv("VMESS_PORT", "2053"))
        trojan_port = int(os.getenv("TROJAN_PORT", "2083"))
        hy2_port = int(os.getenv("HYSTERIA2_PORT", "443"))
        ss_port = int(os.getenv("SHADOWSOCKS_PORT", "18388"))
        config = {
            "log": {"level": "info", "timestamp": True},
            "dns": {
                "servers": [{"type": "udp", "tag": "google", "server": "8.8.8.8"}],
                "final": "google",
                "strategy": "ipv4_only",
                "reverse_mapping": True,
            },
            "inbounds": [
                {
                    "type": "vless",
                    "tag": "vless-reality-in",
                    "listen": "::",
                    "listen_port": vless_port,
                    "users": [],
                    "tls": {
                        "enabled": True,
                        "server_name": cfg["reality_server_name"],
                        "reality": {
                            "enabled": True,
                            "handshake": {
                                "server": cfg["reality_server_name"],
                                "server_port": 443,
                            },
                            "private_key": cfg["reality_private_key"],
                            "short_id": [cfg["reality_short_id"]],
                        },
                    },
                    "multiplex": {"enabled": True, "padding": True},
                },
                {
                    "type": "vmess",
                    "tag": "vmess-ws-in",
                    "listen": "::",
                    "listen_port": vmess_port,
                    "users": [],
                    "transport": {
                        "type": "ws",
                        "path": "/vmess-path",
                        "max_early_data": 2048,
                        "early_data_header_name": "Sec-WebSocket-Protocol",
                    },
                    "tls": {
                        "enabled": cfg["tls_enabled"],
                        "certificate_path": cfg["tls_cert_path"],
                        "key_path": cfg["tls_key_path"],
                    },
                },
                {
                    "type": "trojan",
                    "tag": "trojan-in",
                    "listen": "::",
                    "listen_port": trojan_port,
                    "users": [],
                    "tls": {
                        "enabled": cfg["tls_enabled"],
                        "certificate_path": cfg["tls_cert_path"],
                        "key_path": cfg["tls_key_path"],
                    },
                },
                {
                    "type": "hysteria2",
                    "tag": "hysteria2-in",
                    "listen": "::",
                    "listen_port": hy2_port,
                    "users": [],
                    "up_mbps": cfg["hysteria2_up_mbps"],
                    "down_mbps": cfg["hysteria2_down_mbps"],
                    "tls": {
                        "enabled": cfg["tls_enabled"],
                        "certificate_path": cfg["tls_cert_path"],
                        "key_path": cfg["tls_key_path"],
                    },
                },
                {
                    "type": "shadowsocks",
                    "tag": "shadowsocks-in",
                    "listen": "::",
                    "listen_port": ss_port,
                    "method": cfg["shadowsocks_method"],
                    "password": cfg["shadowsocks_password"],
                    "users": [],
                },
            ],
            "outbounds": [
                {"type": "direct", "tag": "direct"},
                {"type": "block", "tag": "block"},
            ],
            "route": {
                "rules": [
                    {"port": [53], "action": "hijack-dns"},
                    {"action": "sniff"},
                    {"action": "resolve", "strategy": "ipv4_only"},
                    {"ip_cidr": ["::/0"], "outbound": "block"},
                    {"ip_is_private": True, "outbound": "block"},
                ],
                "final": "direct",
            },
            "experimental": {
                "clash_api": {
                    "external_controller": "0.0.0.0:9090",
                    "secret": cfg["clash_api_secret"],
                },
                "v2ray_api": {
                    "listen": cfg["v2ray_api_listen"],
                    "stats": {
                        "enabled": True,
                        "users": [],
                    },
                },
                "cache_file": {
                    "enabled": True,
                    "path": "/opt/sing-box/cache.db",
                },
            },
        }
        path = Path("/opt/sing-box/config.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

    async def _validate_dns(self, domain: str, server_ip: str) -> bool:
        loop = asyncio.get_event_loop()
        try:
            ips = await asyncio.wait_for(
                loop.run_in_executor(None, self._resolve, domain), timeout=5.0
            )
            return server_ip in ips
        except Exception:
            return False

    @staticmethod
    def _resolve(domain: str) -> list[str]:
        ips = []
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                for res in socket.getaddrinfo(domain, None, family, socket.SOCK_STREAM):
                    ip = res[4][0]
                    if ip not in ips:
                        ips.append(ip)
            except socket.gaierror:
                pass
        return ips

    async def _certbot(self, domain: str, email: str) -> bool:
        # Check cert existence via the certbot container (which runs as root and can read /etc/letsencrypt)
        check = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "certbot",
            "test",
            "-f",
            f"/etc/letsencrypt/live/{domain}/fullchain.pem",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await check.wait()
        if check.returncode == 0:
            return True
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                "certbot",
                "certbot",
                "certonly",
                "--standalone",
                "--non-interactive",
                "--agree-tos",
                "--email",
                email,
                "-d",
                domain,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(
                    "certbot failed",
                    extra={"extra_fields": {"stderr": stderr.decode()[-500:]}},
                )
            return proc.returncode == 0
        except Exception as e:
            logger.warning("certbot error", extra={"extra_fields": {"error": str(e)}})
            return False

    async def _docker_up(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "restart",
                "sing-box",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(
                    "sing-box restart failed",
                    extra={"extra_fields": {"stderr": stderr.decode()[-500:]}},
                )
            return proc.returncode == 0
        except Exception as e:
            logger.warning(
                "docker restart failed", extra={"extra_fields": {"error": str(e)}}
            )
            return False

    @staticmethod
    def _result(
        success: bool, message: str, steps: dict, errors: list, cfg: dict | None = None
    ) -> dict:
        return {
            "success": success,
            "message": message,
            "config": cfg,
            "certificate_obtained": steps.get("cert", False),
            "containers_started": steps.get("docker", False),
            "timestamp": datetime.now(tz=UTC),
            "errors": errors,
        }
