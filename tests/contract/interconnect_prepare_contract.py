"""Preparation preserves all live fields and never silently stages unsafe endpoints."""

import copy
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "scripts/lib/interconnect")
)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services/node/src"))

from configure_interconnect import entry_config
from prepare_interconnect import candidate, native_xray
from runtime.xray import socks

# Remote preparation supplies this single reviewed helper under a standalone name.
sys.modules["xray_socks"] = socks

original = entry_config(
    "100.64.0.2", 43001, 43002, "probe", "not-a-production-password"
)
original["log"]["level"] = "error"
snapshot = copy.deepcopy(original)
new = candidate(original)
assert original == snapshot, "Preparation mutated the live source"
expected = copy.deepcopy(original)
expected["outbounds"][0]["udp_over_tcp"]["enabled"] = False
assert new == expected, "Preparation changed keys, ports, filters or other settings"
assert (
    entry_config("100.64.0.2", 43001, 43002, "probe", "secret", udp_mode="native")[
        "outbounds"
    ][0]["udp_over_tcp"]["enabled"]
    is False
)
for part, field, value in (
    ("inbounds", "listen", "0.0.0.0"),
    ("outbounds", "server", "8.8.8.8"),
    ("outbounds", "password", ""),
    ("outbounds", "type", "shadowsocks"),
):
    invalid = copy.deepcopy(original)
    invalid[part][0][field] = value
    try:
        candidate(invalid)
    except ValueError:
        continue
    raise AssertionError(f"Unsafe preparation accepted: {part}.{field}")
print("Preparation preservation and unsafe-endpoint contracts passed")

canonical = {
    "inbounds": [{"users": [{"password": "existing-ingress"}]}],
    "outbounds": [
        {
            "type": "socks",
            "tag": "outbound:route",
            "server": "127.0.0.1",
            "server_port": 43002,
        }
    ],
    "route": {"rules": [{"auth_user": ["existing"], "outbound": "outbound:route"}]},
}
runtime = {
    "inbounds": [
        {
            "protocol": "hysteria",
            "settings": {"clients": [{"auth": "existing-ingress"}]},
        }
    ],
    "policy": {"levels": {"0": {"statsUserOnline": True}}},
    "outbounds": [
        {
            "tag": "outbound:route",
            "protocol": "socks",
            "settings": {"servers": [{"address": "127.0.0.1", "port": 43002}]},
        }
    ],
    "routing": {"rules": [{"user": ["existing"], "outboundTag": "outbound:route"}]},
}
peer = {
    "server": "100.64.0.2",
    "server_port": 43001,
    "username": "probe",
    "password": "not-a-production-password",
}
desired, rendered = native_xray(
    canonical, runtime, [{"listen_port": 43002, "peer": peer}]
)
assert desired["inbounds"] == canonical["inbounds"]
assert desired["route"] == canonical["route"]
assert rendered["inbounds"] == runtime["inbounds"]
assert rendered["policy"] == runtime["policy"]
assert rendered["routing"]["rules"][0]["outboundTag"] == rendered["outbounds"][0]["tag"]
assert rendered["outbounds"][0]["settings"]["servers"][0]["users"] == [
    {"user": peer["username"], "pass": peer["password"]}
]
assert native_xray(canonical, runtime, [{"listen_port": 43002, "peer": peer}]) == (
    desired,
    rendered,
)
print("Native Xray preparation preserves HY2, policy and canonical route identity")
