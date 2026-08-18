"""Domain service: VPN user management."""

import asyncio
import uuid as _uuid
from datetime import UTC, datetime

from domain.errors import (
    InboundNotFoundError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from domain.models import Inbound, InboundUser, SingBoxConfig
from domain.mutation import commit_config, serialized_mutation
from domain.ports import IConfigStore, IConfigUrlBuilder, IContainerRuntime
from utils.crypto import generate_secure_password
from utils.logging_config import get_logger

logger = get_logger(__name__)

PROTOCOL_TAGS: dict[str, str] = {
    "vless": "vless-reality-in",
    "vmess": "vmess-ws-in",
    "trojan": "trojan-in",
    "hysteria2": "hysteria2-in",
    "shadowsocks": "shadowsocks-in",
}


def _adapt_user(username: str, uuid: str, password: str, proto: str) -> InboundUser:
    if proto == "vless":
        return InboundUser(name=username, uuid=uuid, flow="xtls-rprx-vision")
    if proto == "vmess":
        return InboundUser(name=username, uuid=uuid)
    if proto in ("trojan", "hysteria2", "shadowsocks"):
        return InboundUser(name=username, password=password)
    raise ValueError(f"Unknown protocol: {proto}")


def _find_inbound(config: SingBoxConfig, tag: str) -> Inbound | None:
    return next((ib for ib in config.inbounds if ib.tag == tag), None)


def _user_in(inbound: Inbound, username: str) -> bool:
    return any(u.name == username for u in inbound.users)


def _find_user(inbound: Inbound, username: str) -> InboundUser | None:
    return next((u for u in inbound.users if u.name == username), None)


def _sync_v2ray_stats_users(config: SingBoxConfig) -> None:
    users = sorted(
        {
            user.name
            for inbound in config.inbounds
            for user in inbound.users
            if user.name
        }
    )

    experimental = dict(config.experimental or {})
    v2ray_api = dict(experimental.get("v2ray_api") or {})
    stats = dict(v2ray_api.get("stats") or {})

    if not v2ray_api.get("listen"):
        v2ray_api["listen"] = "0.0.0.0:10085"
    stats["enabled"] = True
    stats["users"] = users
    v2ray_api["stats"] = stats
    experimental["v2ray_api"] = v2ray_api
    config.experimental = experimental


class UserService:
    def __init__(
        self,
        store: IConfigStore,
        runtime: IContainerRuntime,
        url_builder: IConfigUrlBuilder,
        mutation_lock: asyncio.Lock | None = None,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._url_builder = url_builder
        self._mutation_lock = mutation_lock or asyncio.Lock()

    @serialized_mutation
    async def create_user(
        self,
        username: str,
        requested_uuid: str | None = None,
        requested_password: str | None = None,
    ) -> dict:
        config = await self._store.load()
        backup = await self._store.backup()

        configured = [
            (protocol, inbound)
            for protocol, tag in PROTOCOL_TAGS.items()
            if (inbound := _find_inbound(config, tag)) is not None
        ]
        if not configured:
            raise InboundNotFoundError("No supported inbound is configured")
        if any(_user_in(inbound, username) for _, inbound in configured):
            raise UserAlreadyExistsError(f"User '{username}' already exists")

        uid = requested_uuid or str(_uuid.uuid4())
        pwd = requested_password or generate_secure_password(32)

        for protocol, inbound in configured:
            inbound.users.append(_adapt_user(username, uid, pwd, protocol))

        _sync_v2ray_stats_users(config)

        await commit_config(self._store, self._runtime, config, backup)

        logger.info("User created", extra={"extra_fields": {"username": username}})
        return {
            "username": username,
            "uuid": uid,
            "password": pwd,
            "protocols": [protocol for protocol, _ in configured],
            "created_at": datetime.now(tz=UTC),
        }

    @serialized_mutation
    async def delete_user(self, username: str) -> None:
        config = await self._store.load()
        backup = await self._store.backup()

        removed = False
        for tag in PROTOCOL_TAGS.values():
            ib = _find_inbound(config, tag)
            if ib is None:
                continue
            before = len(ib.users)
            ib.users = [u for u in ib.users if u.name != username]
            if len(ib.users) < before:
                removed = True

        if not removed:
            raise UserNotFoundError(f"User '{username}' not found")

        for rule in config.route.rules:
            if rule.auth_user and username in rule.auth_user:
                rule.auth_user.remove(username)
        config.route.rules = [
            rule
            for rule in config.route.rules
            if not (
                rule.outbound
                and rule.outbound.startswith("outbound:")
                and not rule.auth_user
            )
        ]

        _sync_v2ray_stats_users(config)

        await commit_config(self._store, self._runtime, config, backup)
        logger.info("User deleted", extra={"extra_fields": {"username": username}})

    async def get_user(self, username: str) -> dict:
        config = await self._store.load()
        uid, pwd, found_protocols = "", "", []

        for proto, tag in PROTOCOL_TAGS.items():
            ib = _find_inbound(config, tag)
            if ib is None:
                continue
            user = _find_user(ib, username)
            if user:
                found_protocols.append(proto)
                uid = uid or (user.uuid or "")
                pwd = pwd or (user.password or "")

        if not found_protocols:
            raise UserNotFoundError(f"User '{username}' not found")

        return {
            "username": username,
            "uuid": uid,
            "password": pwd,
            "protocols": found_protocols,
            "created_at": datetime.now(tz=UTC),
        }

    async def list_users(self, skip: int = 0, limit: int = 50) -> dict:
        config = await self._store.load()
        users: dict[str, dict] = {}

        for proto, tag in PROTOCOL_TAGS.items():
            ib = _find_inbound(config, tag)
            if ib is None:
                continue
            for u in ib.users:
                if not u.name:
                    continue
                entry = users.setdefault(
                    u.name,
                    {"username": u.name, "uuid": "", "password": "", "protocols": []},
                )
                entry["protocols"].append(proto)
                entry["uuid"] = entry["uuid"] or (u.uuid or "")
                entry["password"] = entry["password"] or (u.password or "")

        all_users = sorted(
            [{**data, "created_at": datetime.now(tz=UTC)} for data in users.values()],
            key=lambda d: d["username"],
        )
        return {
            "users": all_users[skip : skip + limit],
            "total": len(all_users),
            "limit": limit,
            "skip": skip,
        }

    async def get_user_configs(self, username: str, domain: str) -> dict:
        config = await self._store.load()
        by_proto: dict[str, tuple] = {}  # proto -> (user, inbound)

        for proto, tag in PROTOCOL_TAGS.items():
            ib = _find_inbound(config, tag)
            if ib is None:
                continue
            user = _find_user(ib, username)
            if user:
                by_proto[proto] = (user, ib)

        if not by_proto:
            raise UserNotFoundError(f"User '{username}' not found")

        configs: dict[str, dict | None] = {
            "vless": None,
            "vmess": None,
            "trojan": None,
            "hysteria2": None,
            "shadowsocks": None,
        }

        if "vless" in by_proto:
            user, ib = by_proto["vless"]
            url = self._url_builder.vless_url(
                uuid=user.uuid or "", domain=domain, port=ib.listen_port
            )
            configs["vless"] = {
                "protocol": "vless",
                "config_url": url,
                "port": ib.listen_port,
            }

        if "vmess" in by_proto:
            user, ib = by_proto["vmess"]
            ws_path = ib.transport.path if ib.transport else "/vmess"
            url = self._url_builder.vmess_url(
                user.uuid or "", domain, ib.listen_port, path=ws_path or "/vmess"
            )
            configs["vmess"] = {
                "protocol": "vmess",
                "config_url": url,
                "port": ib.listen_port,
            }

        if "trojan" in by_proto:
            user, ib = by_proto["trojan"]
            url = self._url_builder.trojan_url(
                user.password or "", domain, ib.listen_port
            )
            configs["trojan"] = {
                "protocol": "trojan",
                "config_url": url,
                "port": ib.listen_port,
            }

        if "hysteria2" in by_proto:
            user, ib = by_proto["hysteria2"]
            url = self._url_builder.hysteria2_url(
                user.password or "", domain, ib.listen_port
            )
            configs["hysteria2"] = {
                "protocol": "hysteria2",
                "config_url": url,
                "port": ib.listen_port,
            }

        if "shadowsocks" in by_proto:
            user, ib = by_proto["shadowsocks"]
            method = ib.method or "2022-blake3-aes-128-gcm"
            user_psk = user.password or ""
            # 2022-blake3 multi-user mode: client password = server_psk:user_psk
            if ib.password and method.startswith("2022-blake3"):
                combined_password = f"{ib.password}:{user_psk}"
            else:
                combined_password = user_psk
            url = self._url_builder.shadowsocks_url(
                combined_password, ib.listen_port, method, domain=domain
            )
            configs["shadowsocks"] = {
                "protocol": "shadowsocks",
                "config_url": url,
                "port": ib.listen_port,
            }

        return {"username": username, "configs": configs}
