"""Render the canonical Feint VLESS config for Xray-core."""

import json
import os
import tempfile
from pathlib import Path

from domain.models import SingBoxConfig


def _outbound(value: dict) -> dict:
    if value["type"] == "direct":
        return {"protocol": "freedom", "tag": value["tag"]}
    if value["type"] == "block":
        return {"protocol": "blackhole", "tag": value["tag"]}
    if value["type"] != "hysteria2":
        raise ValueError(f"Xray runtime does not support outbound {value['type']}")

    stream = {
        "method": "hysteria",
        "security": "tls",
        "hysteriaSettings": {"version": 2, "auth": value["password"]},
        "tlsSettings": {
            "serverName": value["tls"]["server_name"],
            "allowInsecure": value["tls"].get("insecure", False),
        },
    }
    if obfs := value.get("obfs"):
        stream["finalmask"] = {
            "udp": [
                {
                    "type": obfs["type"],
                    "settings": {"password": obfs["password"]},
                }
            ]
        }
    if value.get("up_mbps") or value.get("down_mbps"):
        limits = {}
        if value.get("up_mbps"):
            limits["brutalUp"] = f"{value['up_mbps']} mbps"
        if value.get("down_mbps"):
            limits["brutalDown"] = f"{value['down_mbps']} mbps"
        stream.setdefault("finalmask", {})["quicParams"] = limits

    return {
        "protocol": "hysteria",
        "tag": value["tag"],
        "settings": {
            "version": 2,
            "address": value["server"],
            "port": value["server_port"],
        },
        "streamSettings": stream,
    }


def render_xray_config(source: str, destination: str) -> None:
    config = SingBoxConfig.model_validate_json(Path(source).read_text(encoding="utf-8"))
    inbound = next(
        (item for item in config.inbounds if item.tag == "vless-reality-in"), None
    )
    if inbound is None or inbound.tls is None or inbound.tls.reality is None:
        raise ValueError("Xray runtime requires a VLESS REALITY inbound")

    reality = inbound.tls.reality
    handshake = reality.get("handshake") or {}
    server_name = inbound.tls.server_name or handshake.get("server")
    private_key = reality.get("private_key")
    short_ids = reality.get("short_id")
    if not server_name or not private_key or not short_ids:
        raise ValueError("VLESS REALITY settings are incomplete")

    xray = {
        "log": {"loglevel": config.log.level},
        "api": {
            "tag": "api",
            "listen": "0.0.0.0:10085",
            "services": ["StatsService"],
        },
        "policy": {
            "levels": {
                "0": {
                    "statsUserUplink": True,
                    "statsUserDownlink": True,
                    "statsUserOnline": True,
                }
            }
        },
        "stats": {},
        "inbounds": [
            {
                "tag": inbound.tag,
                "listen": "0.0.0.0",
                "port": inbound.listen_port,
                "protocol": "vless",
                "settings": {
                    "clients": [
                        {
                            "id": user.uuid,
                            "email": user.name,
                            "flow": user.flow or "xtls-rprx-vision",
                            "level": 0,
                        }
                        for user in inbound.users
                    ],
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "target": f"{handshake.get('server', server_name)}:{handshake.get('server_port', 443)}",
                        "serverNames": [server_name],
                        "privateKey": private_key,
                        "shortIds": short_ids,
                        "minClientVer": os.getenv(
                            "XRAY_MIN_CLIENT_VERSION", "1.8.0"
                        ),
                    },
                },
            }
        ],
        "outbounds": [_outbound(item.model_dump()) for item in config.outbounds],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {
                    "type": "field",
                    "ip": ["geoip:private", "geoip:ru"],
                    "outboundTag": "block",
                }
            ]
            + [
                {"type": "field", "user": rule.auth_user, "outboundTag": rule.outbound}
                for rule in config.route.rules
                if rule.auth_user and rule.outbound
            ],
        },
    }

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".xray_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(xray, file, indent=2, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    import sys

    render_xray_config(sys.argv[1], sys.argv[2])
