"""Render canonical Feint VLESS/Hysteria2 configuration for Xray-core."""

import json
import os
import tempfile
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any

from models.configuration import Inbound, SingBoxConfig
from runtime.xray.socks import socks_outbound


def _validate_control_listen(listen: str) -> None:
    message = "Xray hot control requires an explicit loopback or private IP:port"
    try:
        host, port = listen.rsplit(":", 1)
        if ":" in host:
            if not (host.startswith("[") and host.endswith("]")):
                raise ValueError(message)
            host = host[1:-1]
        address = ip_address(host)
        valid_port = 1 <= int(port) <= 65535
    except ValueError:
        raise ValueError(message) from None
    if not valid_port or not (
        address.is_loopback
        or any(
            address in ip_network(network)
            for network in (
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
                "100.64.0.0/10",
            )
        )
    ):
        raise ValueError(message)


def _hysteria_inbound(inbound: Inbound, *, tag: str, port: int) -> dict[str, Any]:
    tls = inbound.tls
    if tls is None or not tls.certificate_path or not tls.key_path:
        raise ValueError("Xray Hysteria2 TLS settings are incomplete")
    return {
        "tag": tag,
        "listen": "0.0.0.0",
        "port": port,
        "protocol": "hysteria",
        "settings": {
            "version": 2,
            "clients": [
                {"auth": user.password, "email": user.name, "level": 0}
                for user in inbound.users
            ],
        },
        "streamSettings": {
            "network": "hysteria",
            "security": "tls",
            "tlsSettings": {
                "alpn": ["h3"],
                "certificates": [
                    {
                        "certificateFile": tls.certificate_path,
                        "keyFile": tls.key_path,
                    }
                ],
            },
            "hysteriaSettings": {"version": 2},
        },
    }


def _vless_inbound(inbound: Inbound, min_client_version: str) -> dict[str, Any]:
    if inbound.tls is None or inbound.tls.reality is None:
        raise ValueError("VLESS REALITY settings are incomplete")
    reality = inbound.tls.reality
    handshake = reality.get("handshake") or {}
    server_name = inbound.tls.server_name or handshake.get("server")
    private_key = reality.get("private_key")
    short_ids = reality.get("short_id")
    if not server_name or not private_key or not short_ids:
        raise ValueError("VLESS REALITY settings are incomplete")

    result = {
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
            },
        },
    }
    min_client_version = min_client_version.strip()
    if min_client_version:
        result["streamSettings"]["realitySettings"]["minClientVer"] = min_client_version
    return result


def build_xray_config(
    config: SingBoxConfig,
    api_listen: str = "0.0.0.0:10085",
    *,
    min_client_version: str = "",
    hysteria2_compat_port: int | None = None,
    control_api: bool = False,
) -> dict[str, Any]:
    if control_api:
        _validate_control_listen(api_listen)
    inbound = next(
        (item for item in config.inbounds if item.tag == "vless-reality-in"), None
    )
    hysteria = next(
        (item for item in config.inbounds if item.tag == "hysteria2-in"), None
    )
    if inbound is None and hysteria is None:
        raise ValueError("Xray runtime requires a VLESS or Hysteria2 inbound")
    managed_outbounds = [
        item for item in config.outbounds if item.tag.startswith("outbound:")
    ]
    unsupported = [item.type for item in managed_outbounds if item.type != "socks"]
    if unsupported:
        raise ValueError(
            f"Xray runtime does not support managed outbound: {unsupported[0]}"
        )
    inbounds = [_vless_inbound(inbound, min_client_version)] if inbound else []
    if hysteria is not None:
        inbounds.append(
            _hysteria_inbound(
                hysteria,
                tag=hysteria.tag,
                port=hysteria.listen_port,
            )
        )
        if hysteria2_compat_port is not None:
            compat_port = hysteria2_compat_port
            if not 1 <= compat_port <= 65535:
                raise ValueError("HYSTERIA2_COMPAT_PORT must be between 1 and 65535")
            if compat_port != hysteria.listen_port:
                inbounds.append(
                    _hysteria_inbound(
                        hysteria,
                        tag=f"{hysteria.tag}-compat",
                        port=compat_port,
                    )
                )

    reverse_exit = next(
        (item for item in config.inbounds if item.tag == "reverse-exit-in"), None
    )
    if reverse_exit is not None:
        inbounds.append(
            {
                "tag": reverse_exit.tag,
                "listen": "127.0.0.1",
                "port": reverse_exit.listen_port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": False},
            }
        )

    xray_outbounds: list[dict[str, Any]] = [
        {"protocol": "freedom", "tag": "direct"},
        {"protocol": "blackhole", "tag": "block"},
    ]
    tags = {}
    for outbound in managed_outbounds:
        rendered = socks_outbound(outbound.tag, outbound.model_dump())
        xray_outbounds.append(rendered)
        tags[outbound.tag] = rendered["tag"]

    managed_rules = [
        {
            "type": "field",
            "user": rule.auth_user,
            "outboundTag": tags[rule.outbound],
        }
        for rule in config.route.rules
        if rule.outbound and rule.outbound.startswith("outbound:") and rule.auth_user
    ]

    xray = {
        "log": {"loglevel": config.log.level, "access": "none"},
        "api": {
            "tag": "api",
            "listen": api_listen,
            "services": ["StatsService", "HandlerService", "RoutingService"]
            if control_api
            else ["StatsService"],
        },
        "policy": {
            "levels": {
                "0": {
                    "statsUserUplink": True,
                    "statsUserDownlink": True,
                }
            }
        },
        "stats": {},
        "inbounds": inbounds,
        "outbounds": xray_outbounds,
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                *managed_rules,
                {
                    "type": "field",
                    "ip": ["geoip:private", "geoip:ru"]
                    if inbound is not None
                    else ["geoip:private"],
                    "outboundTag": "block",
                },
            ],
        },
    }

    return xray


def render_xray_config(
    source: str,
    destination: str,
    api_listen: str = "0.0.0.0:10085",
    *,
    min_client_version: str = "",
    hysteria2_compat_port: int | None = None,
    control_api: bool = False,
) -> None:
    config = SingBoxConfig.model_validate_json(Path(source).read_text(encoding="utf-8"))
    xray = build_xray_config(
        config,
        api_listen,
        min_client_version=min_client_version,
        hysteria2_compat_port=hysteria2_compat_port,
        control_api=control_api,
    )
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

    from config import settings

    render_xray_config(
        sys.argv[1],
        sys.argv[2],
        sys.argv[3] if len(sys.argv) > 3 else settings.XRAY_API_LISTEN,
        min_client_version=settings.XRAY_REALITY_MIN_CLIENT_VERSION,
        hysteria2_compat_port=settings.HYSTERIA2_COMPAT_PORT,
        control_api=settings.XRAY_HOT_RELOAD,
    )
