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
from domain.users import ProtocolConfig, UserConfigs, UserData, UserPage
from utils.crypto import generate_secure_password
from utils.logging_config import get_logger

logger = get_logger(__name__)

PROTOCOL_TAGS: dict[str, str] = {
    "vless": "vless-reality-in",
    "vmess": "vmess-ws-in",
    "trojan": "trojan-in",
    "hysteria2": "hysteria2-in",
    "tuic": "tuic-in",
    "shadowsocks": "shadowsocks-in",
}


def _adapt_user(username: str, uuid: str, password: str, proto: str) -> InboundUser:
    if proto == "vless":
        return InboundUser(name=username, uuid=uuid, flow="xtls-rprx-vision")
    if proto == "vmess":
        return InboundUser(name=username, uuid=uuid)
    if proto == "tuic":
        return InboundUser(name=username, uuid=uuid, password=password)
    if proto in ("trojan", "hysteria2", "shadowsocks"):
        return InboundUser(name=username, password=password)
    raise ValueError(f"Unknown protocol: {proto}")


def _find_inbound(config: SingBoxConfig, tag: str) -> Inbound | None:
    return next((ib for ib in config.inbounds if ib.tag == tag), None)


def _find_user(inbound: Inbound, username: str) -> InboundUser | None:
    return next((u for u in inbound.users if u.name == username), None)


def _published_protocols(
    config: SingBoxConfig,
    username: str,
    enabled_protocols: frozenset[str] | None,
) -> set[str]:
    routed_outbounds = {
        rule.outbound
        for rule in config.route.rules
        if rule.outbound and username in (rule.auth_user or [])
    }
    if any(
        outbound.tag in routed_outbounds and outbound.type == "socks"
        for outbound in config.outbounds
    ):
        supported = {"vless", "hysteria2", "tuic"}
    else:
        supported = set(PROTOCOL_TAGS)
    if enabled_protocols is not None:
        supported.intersection_update(enabled_protocols)
    return supported


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
        published_protocols: frozenset[str] | None = None,
        hysteria2_public_port: int | None = None,
    ) -> None:
        if published_protocols is not None:
            unknown_protocols = published_protocols - PROTOCOL_TAGS.keys()
            if unknown_protocols:
                raise ValueError(
                    "Unsupported published protocols: "
                    + ", ".join(sorted(unknown_protocols))
                )
        if hysteria2_public_port is not None and not (
            1 <= hysteria2_public_port <= 65535
        ):
            raise ValueError("HYSTERIA2_PUBLIC_PORT must be between 1 and 65535")

        self._store = store
        self._runtime = runtime
        self._url_builder = url_builder
        self._mutation_lock = mutation_lock or asyncio.Lock()
        self._published_protocols = published_protocols
        self._hysteria2_public_port = hysteria2_public_port

    @serialized_mutation
    async def create_user(
        self,
        username: str,
        requested_uuid: str | None = None,
        requested_password: str | None = None,
    ) -> UserData:
        return (
            await self._create_users(
                [(username, requested_uuid, requested_password)],
                skip_existing=False,
            )
        )[0]

    @serialized_mutation
    async def create_users(
        self,
        users: list[tuple[str, str | None, str | None]],
    ) -> int:
        return len(await self._create_users(users, skip_existing=True))

    async def _create_users(
        self,
        users: list[tuple[str, str | None, str | None]],
        *,
        skip_existing: bool,
    ) -> list[UserData]:
        config = await self._store.load()

        configured = [
            (protocol, inbound)
            for protocol, tag in PROTOCOL_TAGS.items()
            if (inbound := _find_inbound(config, tag)) is not None
        ]
        if not configured:
            raise InboundNotFoundError("No supported inbound is configured")

        indexes = {
            inbound.tag: {
                user.name: user for user in reversed(inbound.users) if user.name
            }
            for _, inbound in configured
        }
        created: list[UserData] = []
        for username, requested_uuid, requested_password in users:
            existing = [
                user
                for _, inbound in configured
                if (user := indexes[inbound.tag].get(username)) is not None
            ]
            if existing and not skip_existing:
                raise UserAlreadyExistsError(f"User '{username}' already exists")
            missing = [
                (protocol, inbound)
                for protocol, inbound in configured
                if username not in indexes[inbound.tag]
            ]
            if not missing:
                continue

            uid = (
                requested_uuid
                or next((user.uuid for user in existing if user.uuid), None)
                or str(_uuid.uuid4())
            )
            pwd = (
                requested_password
                or next((user.password for user in existing if user.password), None)
                or generate_secure_password(32)
            )
            for protocol, inbound in missing:
                user = _adapt_user(username, uid, pwd, protocol)
                inbound.users.append(user)
                indexes[inbound.tag][username] = user
            created.append(
                {
                    "username": username,
                    "uuid": uid,
                    "password": pwd,
                    "protocols": [protocol for protocol, _ in missing],
                    "created_at": datetime.now(tz=UTC),
                }
            )

        if not created:
            return []

        _sync_v2ray_stats_users(config)

        await commit_config(
            self._store, self._runtime, config, await self._store.backup()
        )

        logger.info("Users created", extra={"extra_fields": {"count": len(created)}})
        return created

    @serialized_mutation
    async def delete_user(self, username: str) -> None:
        config = await self._store.load()

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

        await commit_config(
            self._store, self._runtime, config, await self._store.backup()
        )
        logger.info("User deleted", extra={"extra_fields": {"username": username}})

    async def get_user(self, username: str) -> UserData:
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

    async def list_users(self, skip: int = 0, limit: int = 50) -> UserPage:
        config = await self._store.load()
        users: dict[str, UserData] = {}

        for proto, tag in PROTOCOL_TAGS.items():
            ib = _find_inbound(config, tag)
            if ib is None:
                continue
            for u in ib.users:
                if not u.name:
                    continue
                entry = users.setdefault(
                    u.name,
                    {
                        "username": u.name,
                        "uuid": "",
                        "password": "",
                        "protocols": [],
                        "created_at": datetime.now(tz=UTC),
                    },
                )
                entry["protocols"].append(proto)
                entry["uuid"] = entry["uuid"] or (u.uuid or "")
                entry["password"] = entry["password"] or (u.password or "")

        all_users = sorted(
            users.values(),
            key=lambda d: d["username"],
        )
        return {
            "users": all_users[skip : skip + limit],
            "total": len(all_users),
            "limit": limit,
            "skip": skip,
        }

    async def get_user_configs(self, username: str, domain: str) -> UserConfigs:
        config = await self._store.load()
        published = _published_protocols(config, username, self._published_protocols)
        configs: dict[str, ProtocolConfig | None] = dict.fromkeys(PROTOCOL_TAGS)
        found = False
        for protocol, tag in PROTOCOL_TAGS.items():
            inbound = _find_inbound(config, tag)
            if inbound is None:
                continue
            user = _find_user(inbound, username)
            if user is None:
                continue
            found = True
            if protocol in published:
                configs[protocol] = self._url_builder.build(
                    protocol,
                    user,
                    inbound,
                    domain,
                    public_port=self._hysteria2_public_port
                    if protocol == "hysteria2"
                    else None,
                )
        if not found:
            raise UserNotFoundError(f"User '{username}' not found")
        return {"username": username, "configs": configs}
