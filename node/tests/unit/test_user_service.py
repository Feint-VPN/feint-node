"""Unit tests for domain.user_service.UserService with mocked ports."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from domain.errors import (
    ConfigSaveError,
    InboundNotFoundError,
    SingBoxReloadError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from domain.models import (
    Inbound,
    InboundUser,
    LogConfig,
    Outbound,
    Route,
    SingBoxConfig,
)
from domain.user_service import UserService
from utils.crypto import generate_reality_keypair

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(users: list | None = None) -> SingBoxConfig:
    ul = users or []
    pwd_users = [InboundUser(name=u.name, password="pwd") for u in ul if u.name]
    return SingBoxConfig(
        log=LogConfig(),
        inbounds=[
            Inbound(
                type="vless",
                tag="vless-reality-in",
                listen="::",
                listen_port=8443,
                users=list(ul),
            ),
            Inbound(
                type="vmess",
                tag="vmess-ws-in",
                listen="::",
                listen_port=2053,
                users=list(ul),
            ),
            Inbound(
                type="trojan",
                tag="trojan-in",
                listen="::",
                listen_port=2083,
                users=list(pwd_users),
            ),
            Inbound(
                type="hysteria2",
                tag="hysteria2-in",
                listen="::",
                listen_port=443,
                users=list(pwd_users),
            ),
            Inbound(
                type="shadowsocks",
                tag="shadowsocks-in",
                listen="::",
                listen_port=18388,
                users=list(pwd_users),
            ),
        ],
        outbounds=[Outbound(type="direct", tag="direct")],
        route=Route(),
    )


def _existing_user() -> InboundUser:
    return InboundUser(name="alice", uuid="550e8400-e29b-41d4-a716-446655440000")


def _make_store(config: SingBoxConfig) -> AsyncMock:
    store = AsyncMock()
    store.load.return_value = config
    store.backup.return_value = "/tmp/backup.json"
    return store


def _make_builder() -> MagicMock:
    b = MagicMock()
    b.vless_url.return_value = "vless://test"
    b.vmess_url.return_value = "vmess://test"
    b.trojan_url.return_value = "trojan://test"
    b.hysteria2_url.return_value = "hysteria2://test"
    b.shadowsocks_url.return_value = "ss://test"
    return b


def _svc(store, runtime=None, builder=None) -> UserService:
    return UserService(
        store=store,
        runtime=runtime or AsyncMock(),
        url_builder=builder or _make_builder(),
    )


# ---------------------------------------------------------------------------
# create_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCreateUser:
    async def test_success(self):
        store = _make_store(_make_config())
        with (
            patch("domain.user_service._uuid.uuid4", return_value="fake-uuid"),
            patch(
                "domain.user_service.generate_secure_password", return_value="fakepwd"
            ),
        ):
            result = await _svc(store).create_user("bob")
        assert result["username"] == "bob"
        assert result["uuid"] == "fake-uuid"
        assert result["password"] == "fakepwd"
        assert len(result["protocols"]) == 5
        store.save.assert_awaited_once()
        saved: SingBoxConfig = store.save.call_args[0][0]
        for ib in saved.inbounds:
            assert any(u.name == "bob" for u in ib.users)
        assert saved.experimental["v2ray_api"]["stats"]["users"] == ["bob"]

    async def test_duplicate_raises(self):
        store = _make_store(_make_config([_existing_user()]))
        with pytest.raises(UserAlreadyExistsError):
            await _svc(store).create_user("alice")
        store.save.assert_not_awaited()

    async def test_missing_inbound_raises(self):
        cfg = _make_config()
        cfg.inbounds = [cfg.inbounds[0]]  # keep only VLESS
        store = _make_store(cfg)
        with pytest.raises(InboundNotFoundError):
            await _svc(store).create_user("bob")

    async def test_reload_failure_triggers_rollback(self):
        store = _make_store(_make_config())
        runtime = AsyncMock()
        runtime.reload.side_effect = [Exception("boom"), None]
        with pytest.raises(SingBoxReloadError):
            await _svc(store, runtime).create_user("bob")
        store.restore.assert_awaited_once_with("/tmp/backup.json")

    async def test_save_failure_triggers_restore(self):
        store = _make_store(_make_config())
        store.save.side_effect = OSError("disk full")
        with pytest.raises(ConfigSaveError):
            await _svc(store).create_user("bob")
        store.restore.assert_awaited_once()


# ---------------------------------------------------------------------------
# delete_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDeleteUser:
    async def test_success(self):
        store = _make_store(_make_config([_existing_user()]))
        await _svc(store).delete_user("alice")
        saved: SingBoxConfig = store.save.call_args[0][0]
        for ib in saved.inbounds:
            assert not any(u.name == "alice" for u in ib.users)
        assert saved.experimental["v2ray_api"]["stats"]["users"] == []

    async def test_not_found_raises(self):
        store = _make_store(_make_config())
        with pytest.raises(UserNotFoundError):
            await _svc(store).delete_user("ghost")
        store.save.assert_not_awaited()

    async def test_reload_failure_triggers_rollback(self):
        store = _make_store(_make_config([_existing_user()]))
        runtime = AsyncMock()
        runtime.reload.side_effect = [Exception("boom"), None]
        with pytest.raises(SingBoxReloadError):
            await _svc(store, runtime).delete_user("alice")
        store.restore.assert_awaited_once_with("/tmp/backup.json")


# ---------------------------------------------------------------------------
# get_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetUser:
    async def test_success(self):
        store = _make_store(_make_config([_existing_user()]))
        result = await _svc(store).get_user("alice")
        assert result["username"] == "alice"
        assert result["uuid"] == "550e8400-e29b-41d4-a716-446655440000"
        assert len(result["protocols"]) > 0

    async def test_not_found_raises(self):
        store = _make_store(_make_config())
        with pytest.raises(UserNotFoundError):
            await _svc(store).get_user("ghost")


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListUsers:
    async def test_empty(self):
        store = _make_store(_make_config())
        result = await _svc(store).list_users()
        assert result["total"] == 0
        assert result["users"] == []

    async def test_with_users(self):
        store = _make_store(_make_config([_existing_user()]))
        result = await _svc(store).list_users()
        assert result["total"] == 1
        assert result["users"][0]["username"] == "alice"

    async def test_pagination(self):
        users = [InboundUser(name=f"u{i}", uuid=f"uuid-{i}") for i in range(10)]
        store = _make_store(_make_config(users))
        page1 = await _svc(store).list_users(skip=0, limit=4)
        page2 = await _svc(store).list_users(skip=4, limit=4)
        assert page1["total"] == 10
        assert len(page1["users"]) == 4
        assert len(page2["users"]) == 4


# ---------------------------------------------------------------------------
# get_user_configs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetUserConfigs:
    async def test_success(self):
        from domain.models import TLSConfig

        reality_keypair = generate_reality_keypair()
        cfg = _make_config([_existing_user()])
        cfg.inbounds[0].tls = TLSConfig(
            enabled=True,
            server_name="www.microsoft.com",
            reality={"private_key": reality_keypair.private_key, "short_id": ["abc"]},
        )
        store = _make_store(cfg)
        builder = _make_builder()
        result = await _svc(store, builder=builder).get_user_configs(
            "alice", "vpn.example.com"
        )
        assert result["username"] == "alice"
        assert result["configs"]["vless"] is not None
        assert result["configs"]["vmess"] is not None
        builder.vless_url.assert_called_once()
        assert (
            builder.vless_url.call_args.kwargs["reality_public_key"]
            == reality_keypair.public_key
        )
        builder.vmess_url.assert_called_once()

    async def test_not_found_raises(self):
        store = _make_store(_make_config())
        with pytest.raises(UserNotFoundError):
            await _svc(store).get_user_configs("ghost", "vpn.example.com")
