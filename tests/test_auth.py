"""Auth enforcement tests.

These load the real Flask app under both AUTH_ENABLED states (with empty
Azure credentials and a temp session dir, so no network calls happen) and
assert that route protection behaves correctly. They guard the security
posture wired up in app.py / cache_routes.py / debug_routes.py.
"""

import importlib

import pytest

# Fixtures reused as test args (redefined-outer-name) and the app is imported
# inside the helper so env is set first (import-outside-toplevel) — both are
# intentional pytest patterns.
# pylint: disable=redefined-outer-name,import-outside-toplevel


def _load_client(monkeypatch, tmp_path, auth_enabled):
    """Import the app fresh with the given auth config and return a test client."""
    monkeypatch.setenv("AUTH_ENABLED", "true" if auth_enabled else "false")
    # Empty Azure creds -> identity Auth is not constructed, so no network I/O.
    monkeypatch.setenv("AZURE_CLIENT_ID", "")
    monkeypatch.setenv("AZURE_TENANT_ID", "")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("SESSION_FILE_DIR", str(tmp_path / "sessions"))

    import src.web.app as app_module

    importlib.reload(app_module)
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


@pytest.fixture
def client_off(monkeypatch, tmp_path):
    return _load_client(monkeypatch, tmp_path, auth_enabled=False)


@pytest.fixture
def client_on(monkeypatch, tmp_path):
    return _load_client(monkeypatch, tmp_path, auth_enabled=True)


class TestAuthDisabled:
    """With AUTH_ENABLED=false every decorator is a pass-through."""

    def test_index_public(self, client_off):
        assert client_off.get("/").status_code == 200

    def test_data_api_public(self, client_off):
        assert client_off.get("/api/current-status").status_code == 200

    def test_admin_route_public(self, client_off):
        # Reaches the handler (not blocked); response code is the handler's own.
        assert client_off.get("/api/clear-cache").status_code == 200

    def test_status_reports_disabled(self, client_off):
        data = client_off.get("/api/auth/status").get_json()
        assert data["auth_enabled"] is False


class TestAuthEnabled:
    """With AUTH_ENABLED=true unauthenticated callers are blocked."""

    def test_index_redirects_to_login(self, client_on):
        resp = client_on.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    @pytest.mark.parametrize("path", ["/api/current-status", "/api/trips"])
    def test_data_api_returns_401(self, client_on, path):
        assert client_on.get(path).status_code == 401

    @pytest.mark.parametrize("path", ["/api/refresh", "/api/clear-cache", "/api/debug"])
    def test_admin_routes_blocked(self, client_on, path):
        # Unauthenticated -> redirect to login; never executes the handler.
        assert client_on.get(path).status_code in (302, 401, 403)

    def test_cache_mutation_blocked(self, client_on):
        assert client_on.delete("/cache/api/delete/x.json").status_code in (302, 401, 403)

    def test_login_page_reachable(self, client_on):
        assert client_on.get("/login").status_code == 200

    def test_favicon_stays_public(self, client_on):
        assert client_on.get("/favicon.ico").status_code in (200, 204)

    def test_status_reports_enabled_and_unauthenticated(self, client_on):
        data = client_on.get("/api/auth/status").get_json()
        assert data["auth_enabled"] is True
        assert data["authenticated"] is False
