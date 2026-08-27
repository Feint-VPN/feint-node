"""Render the canonical Feint VLESS config for Xray-core."""

import json
import os
import tempfile
from pathlib import Path

from domain.models import SingBoxConfig


def render_xray_config(source: str, destination: str) -> None:
    config = SingBoxConfig.model_validate_json(Path(source).read_text(encoding="utf-8"))
    inbound = next(
        (item for item in config.inbounds if item.tag == "vless-reality-in"), None
    )
    if inbound is None or inbound.tls is None or inbound.tls.reality is None:
        raise ValueError("Xray runtime requires a VLESS REALITY inbound")
    if any(item.tag.startswith("outbound:") for item in config.outbounds):
        raise ValueError("Xray runtime supports standalone VLESS only")

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
                        "minClientVer": "0.0.0",
                        "shortIds": short_ids,
                    },
                },
            }
        ],
        "outbounds": [
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "block"},
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {
                    "type": "field",
                    "ip": ["geoip:private", "geoip:ru"],
                    "outboundTag": "block",
                }
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
