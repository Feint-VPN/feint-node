"""Integration tests for the subscription router."""

import base64
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from api.depends import get_user_service
from api.routers.sub import router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from utils.settings import settings


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("API_SECRET", "test-secret")
    monkeypatch.setattr(settings, "SUBSCRIPTION_ENABLED", True)
    monkeypatch.setattr(settings, "SERVER_DOMAIN", "vpn.example.com")
    monkeypatch.setattr(settings, "SUB_URI_TEMPLATE", "🌌 Feint | {Protocol}")

    env_path = tmp_path / ".env.local"
    env_path.write_text(
        "SERVER_DOMAIN=vpn.example.com\n"
        "SUBSCRIPTION_ENABLED=true\n"
        "SUB_URI_TEMPLATE=🌌 Feint | {Protocol}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "ENV_FILE_PATH", env_path)

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_service(app):
    service = AsyncMock()
    service.get_user_configs.return_value = {
        "username": "alice",
        "configs": {
            "vless": {
                "protocol": "vless",
                "config_url": "vless://uuid@vpn.example.com:443?type=tcp#vpn.example.com",
                "port": 443,
            },
            "vmess": {
                "protocol": "vmess",
                "config_url": "vmess://eyJwcyI6InZwbi5leGFtcGxlLmNvbSIsImFkZCI6InZwbi5leGFtcGxlLmNvbSJ9",
                "port": 80,
            },
            "trojan": None,
            "hysteria2": None,
            "shadowsocks": None,
        },
    }
    app.dependency_overrides[get_user_service] = lambda: service
    yield service
    app.dependency_overrides.clear()


@pytest.fixture
def client(app):
    return TestClient(app)


def test_get_subscription_uses_server_domain_by_default(client, mock_service):
    response = client.get("/sub/alice")

    assert response.status_code == 200
    mock_service.get_user_configs.assert_awaited_once_with("alice", "vpn.example.com")
    assert response.headers["Support-URL"] == "https://vpn.example.com"

    decoded = base64.b64decode(response.text).decode()
    assert (
        "vless://uuid@vpn.example.com:443?type=tcp#%F0%9F%8C%8C%20Feint%20%7C%20VLESS"
        in decoded
    )


def test_get_subscription_allows_domain_override(client, mock_service):
    response = client.get("/sub/alice?server_domain=edge.example.com")

    assert response.status_code == 200
    mock_service.get_user_configs.assert_awaited_once_with("alice", "edge.example.com")
    assert response.headers["Support-URL"] == "https://edge.example.com"


def test_get_subscription_settings_requires_auth(client):
    response = client.get("/sub/settings")
    assert response.status_code == 422


def test_update_subscription_settings_persists_template(client, mock_service):
    response = client.put(
        "/sub/settings",
        json={"sub_uri_template": "Edge | {Protocol} | {username}"},
        headers={"X-API-Secret": "test-secret"},
    )

    assert response.status_code == 200
    assert response.json()["sub_uri_template"] == "Edge | {Protocol} | {username}"
    assert settings.SUB_URI_TEMPLATE == "Edge | {Protocol} | {username}"
    assert (
        Path(settings.ENV_FILE_PATH)
        .read_text(encoding="utf-8")
        .find("SUB_URI_TEMPLATE=Edge | {Protocol} | {username}")
        != -1
    )
