"""Manage authenticated sing-box outbounds."""

from __future__ import annotations

import asyncio

from domain.errors import (
    OutboundInUseError,
    OutboundNotFoundError,
    OutboundUserNotFoundError,
)
from domain.models import Hysteria2OutboundConfig, Outbound, RouteRule, SingBoxConfig
from domain.mutation import commit_config, serialized_mutation
from domain.ports import IConfigStore, IContainerRuntime

OUTBOUND_PREFIX = "outbound:"


class OutboundService:
    def __init__(
        self,
        store: IConfigStore,
        runtime: IContainerRuntime,
        mutation_lock: asyncio.Lock | None = None,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._mutation_lock = mutation_lock or asyncio.Lock()

    @serialized_mutation
    async def set(self, outbound_id: str, value: Hysteria2OutboundConfig) -> None:
        config = await self._store.load()
        users = {
            user.name
            for inbound in config.inbounds
            for user in inbound.users
            if user.name
        }
        if missing := value.auth_users - users:
            raise OutboundUserNotFoundError(
                f"Users not found: {', '.join(sorted(missing))}"
            )

        tag = f"{OUTBOUND_PREFIX}{outbound_id}"
        self._replace_outbound(config, tag, value)
        self._replace_rule(config, tag, value.auth_users)
        await commit_config(
            self._store,
            self._runtime,
            config,
            await self._store.backup(),
        )

    @serialized_mutation
    async def delete(self, outbound_id: str) -> None:
        config = await self._store.load()
        tag = f"{OUTBOUND_PREFIX}{outbound_id}"
        if not any(outbound.tag == tag for outbound in config.outbounds):
            raise OutboundNotFoundError(f"Outbound not found: {outbound_id}")

        rule = next((rule for rule in config.route.rules if rule.outbound == tag), None)
        if rule and rule.auth_user:
            raise OutboundInUseError(f"Outbound is still used: {outbound_id}")

        config.outbounds = [
            outbound for outbound in config.outbounds if outbound.tag != tag
        ]
        config.route.rules = [
            rule for rule in config.route.rules if rule.outbound != tag
        ]
        await commit_config(
            self._store,
            self._runtime,
            config,
            await self._store.backup(),
        )

    @serialized_mutation
    async def add_user(self, outbound_id: str, user_id: str) -> None:
        await self._add_users(outbound_id, {user_id})

    @serialized_mutation
    async def add_users(self, outbound_id: str, user_ids: set[str]) -> None:
        await self._add_users(outbound_id, user_ids)

    async def _add_users(self, outbound_id: str, user_ids: set[str]) -> None:
        config = await self._store.load()
        tag = f"{OUTBOUND_PREFIX}{outbound_id}"
        if not any(outbound.tag == tag for outbound in config.outbounds):
            raise OutboundNotFoundError(f"Outbound not found: {outbound_id}")

        users = {
            user.name
            for inbound in config.inbounds
            for user in inbound.users
            if user.name
        }
        if missing := user_ids - users:
            raise OutboundUserNotFoundError(
                f"Users not found: {', '.join(sorted(missing))}"
            )

        rule = next((rule for rule in config.route.rules if rule.outbound == tag), None)
        current = set(rule.auth_user or []) if rule is not None else set()
        if user_ids <= current:
            return
        if rule is None:
            config.route.rules.append(
                RouteRule(action="route", auth_user=sorted(user_ids), outbound=tag)
            )
        else:
            rule.auth_user = sorted(current | user_ids)
        await commit_config(
            self._store, self._runtime, config, await self._store.backup()
        )

    @serialized_mutation
    async def remove_user(self, outbound_id: str, user_id: str) -> None:
        config = await self._store.load()
        tag = f"{OUTBOUND_PREFIX}{outbound_id}"
        if not any(outbound.tag == tag for outbound in config.outbounds):
            raise OutboundNotFoundError(f"Outbound not found: {outbound_id}")

        rule = next((rule for rule in config.route.rules if rule.outbound == tag), None)
        if rule is None or user_id not in (rule.auth_user or []):
            return
        rule.auth_user = [user for user in rule.auth_user or [] if user != user_id]
        if not rule.auth_user:
            config.route.rules.remove(rule)
        await commit_config(
            self._store, self._runtime, config, await self._store.backup()
        )

    @staticmethod
    def _replace_outbound(
        config: SingBoxConfig,
        tag: str,
        value: Hysteria2OutboundConfig,
    ) -> None:
        data = value.model_dump(
            exclude={"auth_users", "password", "obfs"},
            exclude_none=True,
        )
        data["password"] = value.password.get_secret_value()
        if value.obfs:
            data["obfs"] = {
                "type": value.obfs.type,
                "password": value.obfs.password.get_secret_value(),
            }
        outbound = Outbound(tag=tag, **data)
        for index, current in enumerate(config.outbounds):
            if current.tag == tag:
                config.outbounds[index] = outbound
                return
        config.outbounds.append(outbound)

    @staticmethod
    def _replace_rule(config: SingBoxConfig, tag: str, users: set[str]) -> None:
        for rule in config.route.rules:
            if (
                rule.outbound
                and rule.outbound.startswith(OUTBOUND_PREFIX)
                and rule.outbound != tag
                and rule.auth_user
            ):
                rule.auth_user = [user for user in rule.auth_user if user not in users]

        config.route.rules = [
            rule
            for rule in config.route.rules
            if rule.outbound != tag
            and not (
                rule.outbound
                and rule.outbound.startswith(OUTBOUND_PREFIX)
                and not rule.auth_user
            )
        ]
        if users:
            config.route.rules.append(
                RouteRule(
                    action="route",
                    auth_user=sorted(users),
                    outbound=tag,
                )
            )
