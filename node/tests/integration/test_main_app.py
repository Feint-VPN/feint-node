"""Integration tests for the main FastAPI application."""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client for the FastAPI application."""
    # Set required environment variables
    os.environ["API_SECRET"] = "test-secret-key"

    # Import after setting environment variables
    from main import app, settings

    settings.HIDE_ENDPOINTS = False

    return TestClient(app)


class TestApplicationStartup:
    """Tests for application startup and initialization."""

    def test_app_starts_successfully(self, client):
        """Test that the application starts without errors."""
        # The fixture creates the client, which initializes the app
        # If we get here without exceptions, startup was successful
        assert client is not None

    def test_health_check_endpoint(self, client):
        """Test the health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "api_version": "2.0"}

    def test_root_endpoint_does_not_exist(self, client):
        response = client.get("/", headers={"X-API-Secret": "test-secret-key"})
        assert response.status_code == 404

    def test_status_requires_authentication(self, client):
        assert client.get("/status").status_code == 401
        assert (
            client.get("/status", headers={"X-API-Secret": "wrong-secret"}).status_code
            == 401
        )

    def test_status_returns_complete_runtime_metadata(self, client):
        from api.depends import get_node_telemetry_service, get_user_service
        from main import app

        class StubTelemetry:
            async def get_status(self) -> dict:
                return {
                    "uptime": "02d 07h",
                    "protocols": [
                        {"name": "VLESS Reality", "port": 22481, "enabled": True},
                        {"name": "VMess WS", "port": 14170, "enabled": True},
                    ],
                }

        class StubUsers:
            async def list_users(self, *, skip: int, limit: int) -> dict:
                assert (skip, limit) == (0, 1)
                return {"users": [], "total": 250, "skip": skip, "limit": limit}

        app.dependency_overrides[get_node_telemetry_service] = lambda: StubTelemetry()
        app.dependency_overrides[get_user_service] = lambda: StubUsers()
        try:
            response = client.get(
                "/status", headers={"X-API-Secret": "test-secret-key"}
            )
        finally:
            app.dependency_overrides.pop(get_node_telemetry_service, None)
            app.dependency_overrides.pop(get_user_service, None)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["api_version"] == "2.0"
        assert data["uptime"] == "02d 07h"
        assert data["user_count"] == 250
        assert data["protocols"] == [
            {"name": "VLESS Reality", "port": 22481, "enabled": True},
            {"name": "VMess WS", "port": 14170, "enabled": True},
        ]


class TestMiddleware:
    """Tests for application middleware."""

    def test_cors_headers_present(self, client):
        """Test that CORS middleware adds appropriate headers."""
        response = client.options(
            "/health",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # CORS middleware should add headers
        assert "access-control-allow-origin" in response.headers

    def test_request_logging_middleware(self, client, caplog):
        """Test that request logging middleware logs requests."""
        with caplog.at_level("INFO"):
            response = client.get("/health")
            assert response.status_code == 200

        # Check that request was logged
        log_messages = [record.message for record in caplog.records]
        assert any("Incoming request: GET /health" in msg for msg in log_messages)
        assert any(
            "Request completed: GET /health - 200" in msg for msg in log_messages
        )


class TestRouterRegistration:
    """Tests for router registration."""

    def test_user_router_registered(self, client):
        """Test that user router is registered."""
        from main import app

        assert "post" in app.openapi()["paths"]["/user"]

    def test_stats_router_registered(self, client):
        """Test that stats router is registered."""
        # Try to access a stats endpoint without auth (should fail with 422 for missing header)
        response = client.get("/user/testuser/stats")
        # Should get 422 (not 404), indicating the route exists but header is missing
        assert response.status_code == 422

    def test_initialization_router_registered(self, client):
        """Test that initialization router is registered."""
        # Try to access initialization endpoint without auth (should fail with 422 for missing header)
        response = client.post(
            "/initialize",
            json={
                "domain": "example.com",
                "email": "admin@example.com",
                "server_ip": "1.2.3.4",
            },
        )
        # Should get 422 (not 404), indicating the route exists but header is missing
        assert response.status_code == 422

    def test_user_router_with_invalid_auth(self, client):
        """Test that user router requires valid authentication."""
        response = client.post(
            "/user",
            json={"username": "testuser"},
            headers={"X-API-Secret": "wrong-secret"},
        )
        # Should get 401 for invalid auth
        assert response.status_code == 401

    def test_stats_router_with_invalid_auth(self, client):
        """Test that stats router requires valid authentication."""
        response = client.get(
            "/user/testuser/stats",
            headers={"X-API-Secret": "wrong-secret"},
        )
        # Should get 401 for invalid auth
        assert response.status_code == 401

    def test_initialization_router_with_invalid_auth(self, client):
        """Test that initialization router requires valid authentication."""
        response = client.post(
            "/initialize",
            json={
                "domain": "example.com",
                "email": "admin@example.com",
                "server_ip": "1.2.3.4",
            },
            headers={"X-API-Secret": "wrong-secret"},
        )
        # Should get 401 for invalid auth
        assert response.status_code == 401


class TestHiddenEndpoints:
    def test_hidden_mode_returns_the_same_empty_404_without_a_valid_secret(
        self, client
    ):
        from main import settings

        settings.HIDE_ENDPOINTS = True
        try:
            root = client.get("/")
            known_without_secret = client.post("/user", json={"username": "testuser"})
            known_with_wrong_secret = client.get(
                "/health", headers={"X-API-Secret": "wrong-secret"}
            )
            original_secret = os.environ["API_SECRET"]
            try:
                os.environ["API_SECRET"] = "change-me-in-production"
                default_secret = client.get(
                    "/health", headers={"X-API-Secret": "change-me-in-production"}
                )
            finally:
                os.environ["API_SECRET"] = original_secret
            unknown_with_valid_secret = client.get(
                "/not-a-real-route", headers={"X-API-Secret": "test-secret-key"}
            )
            valid_health = client.get(
                "/health", headers={"X-API-Secret": "test-secret-key"}
            )
        finally:
            settings.HIDE_ENDPOINTS = False

        for response in (
            root,
            known_without_secret,
            known_with_wrong_secret,
            default_secret,
            unknown_with_valid_secret,
        ):
            assert response.status_code == 404
            assert response.content == b""
        assert valid_health.status_code == 200
        assert valid_health.json() == {"status": "ok", "api_version": "2.0"}


class TestErrorHandling:
    """Tests for error handling in middleware."""

    def test_middleware_handles_exceptions(self, client, caplog):
        """Test that middleware catches and logs exceptions."""
        # Create a route that raises an exception
        from main import app

        @app.get("/test-error")
        async def test_error():
            raise ValueError("Test error")

        with caplog.at_level("ERROR"):
            response = client.get("/test-error")

        # Should return 500 error
        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}

        # Check that error was logged
        log_messages = [record.message for record in caplog.records]
        assert any("Request failed" in msg for msg in log_messages)


class TestJSONResponses:
    """Tests for JSON-only responses (Requirement 14.8)."""

    def test_health_check_returns_json(self, client):
        """Test that health check returns JSON."""
        response = client.get("/health")
        assert response.headers["content-type"] == "application/json"

    def test_status_returns_json(self, client):
        response = client.get("/status", headers={"X-API-Secret": "wrong-secret"})
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/json"

    def test_error_responses_are_json(self, client):
        """Test that error responses are JSON."""
        response = client.get("/nonexistent")
        assert response.status_code == 404
        # FastAPI returns JSON for 404 errors by default
        assert "application/json" in response.headers["content-type"]
