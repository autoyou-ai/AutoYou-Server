# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-e66391798d34ab3d3b6e6a7c

"""Tests for create_agent_chat_app() factory in scheduler_mission_control.

Uses FastAPI's TestClient to exercise the HTTP routes without a running server.
Avoids touching the live ADK chat proxy (that path is mocked).
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-e66391798d34ab3d3b6e6a7c"


from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request

# ---------------------------------------------------------------------------
# Minimal stubs so scheduler_mission_control can import without the full server
# ---------------------------------------------------------------------------

import sys, types


def _build_server_stub() -> types.ModuleType:
    """Build a minimal 'server' stub module for use during test execution.

    _runtime_server() checks for STATE, _is_logged_in, and _persist_state_config
    directly on the module, so these must be module-level attributes.
    """
    srv = types.ModuleType("server")

    # Use a non-empty TOTP secret so _describe_totp_capabilities reports
    # totp_configured=True and the login handler reaches the OTP verification
    # branch (returning 401 for a wrong code) rather than bailing out with 400.
    _DUMMY_SECRET = "JBSWY3DPEHPK3PXP"

    class FakeState:
        config = {
            "security": {"totp_secret": _DUMMY_SECRET},
            "ui_security": {},
            "agent_websites": {"require_otp": False},
        }
        admin_session_token = "test-admin-token"

    srv.STATE = FakeState()

    def _default_config():
        return {
            "security": {"totp_secret": _DUMMY_SECRET},
            "ui_security": {},
            "agent_websites": {"require_otp": False},
        }

    def _is_logged_in(request):
        return request.cookies.get("autoyou_session") == "test-admin-token"

    def _verify_totp_secret(secret, code):
        return False

    def _persist_state_config(cfg):
        pass

    def _describe_totp_capabilities(cfg):
        return {"totp_configured": True, "totp_required": True}

    def _get_pairing_totp_secret(cfg=None):
        return _DUMMY_SECRET

    srv._default_config = _default_config
    srv._is_logged_in = _is_logged_in
    srv._verify_totp_secret = _verify_totp_secret
    srv._persist_state_config = _persist_state_config
    srv._describe_totp_capabilities = _describe_totp_capabilities
    srv._get_pairing_totp_secret = _get_pairing_totp_secret
    return srv


@pytest.fixture(scope="module", autouse=True)
def _server_stub_fixture():
    """Inject the minimal server stub for this test module, then restore the
    original module so other test modules using the real server are unaffected.
    """
    original = sys.modules.get("server")
    sys.modules["server"] = _build_server_stub()
    yield
    if original is None:
        sys.modules.pop("server", None)
    else:
        sys.modules["server"] = original


from autoyou_agents.shared_tools.scheduler_mission_control import (
    _api_auth_error,
    _get_agent_security_settings,
    create_agent_chat_app,
    create_agent_website_app,
    create_scheduler_mission_control_app,
    install_agent_website_auth,
    _agent_chat_cookie_name,
    AGENT_WEBSITES_CONFIG_KEY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(tmp_path: Path, agent_name="test_agent"):
    """Create a minimal agent chat app with a temp frontend dir."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    assets_dir = frontend_dir / "assets"
    assets_dir.mkdir()
    index = frontend_dir / "index.html"
    index.write_text(
        "<!DOCTYPE html><html><head></head><body>Bootstrap: __BOOTSTRAP_JSON__</body></html>",
        encoding="utf-8",
    )
    return create_agent_chat_app(
        agent_name=agent_name,
        title="Test Agent UI",
        description="A test UI.",
        frontend_dir=frontend_dir,
    )


def _make_website_app(tmp_path: Path):
    frontend_dir = tmp_path / "website-frontend"
    frontend_dir.mkdir()
    assets_dir = frontend_dir / "assets"
    assets_dir.mkdir()
    index = frontend_dir / "index.html"
    index.write_text("<!DOCTYPE html><html><head></head><body><main>Private shell</main></body></html>", encoding="utf-8")

    def register_routes(app, agent_name):
        @app.get("/api/private")
        def private_route(request: Request):
            auth_error = _api_auth_error(agent_name, request)
            if auth_error:
                return auth_error
            return {"success": True}

    return create_agent_website_app(
        agent_name="website_test_agent",
        title="Website Test UI",
        description="A protected test UI.",
        index_path=index,
        assets_dir=assets_dir,
        extra_routes_fn=register_routes,
    )


# ---------------------------------------------------------------------------
# Route existence and basic responses
# ---------------------------------------------------------------------------

def test_chat_app_bootstrap_endpoint_returns_agent_name(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get("/api/bootstrap")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_name"] == "test_agent"
    assert body["title"] == "Test Agent UI"
    assert "auth" in body


def test_website_app_hides_shell_and_protects_extra_routes(tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(_make_website_app(tmp_path), raise_server_exceptions=True)
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="autoyou-auth-gate"' in page.text
    assert 'dataset.autoyouAuthLocked = "true"' in page.text
    assert client.get("/api/private").status_code == 401


def test_website_app_login_uses_assigned_agent_totp_before_shared_totp(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    server_stub = sys.modules["server"]
    monkeypatch.setattr(server_stub, "agent_has_assigned_2fa_profile", lambda _agent: True, raising=False)
    monkeypatch.setattr(
        server_stub,
        "verify_agent_assigned_2fa",
        lambda _agent, code: code == "654321",
        raising=False,
    )
    monkeypatch.setattr(
        server_stub,
        "_verify_totp_secret",
        lambda *_args: pytest.fail("shared TOTP must not be used for an assigned profile"),
    )
    client = TestClient(_make_website_app(tmp_path), raise_server_exceptions=True)
    response = client.post("/api/auth/login", json={"totp_code": "654321"})
    assert response.status_code == 200
    assert "Path=/" in response.headers["set-cookie"]
    assert client.get("/api/private").status_code == 200


def test_chat_app_index_serves_html_with_bootstrap(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "test_agent" in resp.text
    assert "__BOOTSTRAP_JSON__" not in resp.text
    assert 'id="autoyou-auth-gate"' in resp.text
    assert 'element.inert = true' in resp.text
    assert 'dataset.autoyouAuthLocked = "true"' in resp.text
    assert '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">' in resp.text


def test_chat_login_uses_assigned_agent_totp_before_shared_totp(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    server_stub = sys.modules["server"]
    monkeypatch.setattr(server_stub, "agent_has_assigned_2fa_profile", lambda _agent: True, raising=False)
    monkeypatch.setattr(
        server_stub,
        "verify_agent_assigned_2fa",
        lambda _agent, code: code == "654321",
        raising=False,
    )
    monkeypatch.setattr(
        server_stub,
        "_verify_totp_secret",
        lambda *_args: pytest.fail("shared TOTP must not be used for an assigned profile"),
    )
    client = TestClient(_make_app(tmp_path), raise_server_exceptions=True)
    response = client.post("/api/auth/login", json={"code": "654321"})
    assert response.status_code == 200
    assert "Path=/" in response.headers["set-cookie"]
    assert client.get("/api/auth/status").json()["auth"]["authenticated"] is True


def test_chat_app_auth_status_endpoint_exists(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get("/api/auth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "success" in body
    assert "authenticated" in body.get("auth", body)


def test_agent_website_auth_does_not_inherit_admin_session(tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(_make_app(tmp_path))
    client.cookies.set("autoyou_session", "test-admin-token")

    assert client.get("/api/auth/status").json()["auth"]["authenticated"] is False


def test_bespoke_website_adapter_gates_html_and_api_without_inheriting_admin_session(monkeypatch):
    from fastapi.testclient import TestClient

    app = FastAPI()
    install_agent_website_auth(
        app,
        agent_name="bespoke_test_agent",
        title="Bespoke Test Agent",
        register_auth_routes=True,
    )

    @app.get("/")
    def index():
        return {"private": True}

    @app.get("/api/private")
    def private_api():
        return {"private": True}

    client = TestClient(app)
    client.cookies.set("autoyou_session", "test-admin-token")
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="autoyou-auth-gate"' in page.text
    assert client.get("/api/private").status_code == 401

    server_stub = sys.modules["server"]
    monkeypatch.setattr(server_stub, "agent_has_assigned_2fa_profile", lambda _agent: True, raising=False)
    monkeypatch.setattr(server_stub, "verify_agent_assigned_2fa", lambda _agent, code: code == "654321", raising=False)
    login = client.post("/api/auth/login", json={"totp_code": "654321"})
    assert login.status_code == 200
    assert "Path=/" in login.headers["set-cookie"]
    assert client.get("/api/private").json() == {"private": True}


def test_shared_otp_login_does_not_share_another_agent_session(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    server_stub = sys.modules["server"]
    monkeypatch.setattr(server_stub, "_verify_totp_secret", lambda *_args: True)
    first = TestClient(_make_app(tmp_path / "first", agent_name="notify_agent"))
    second = TestClient(_make_app(tmp_path / "second", agent_name="tasks_agent"))

    assert first.post("/api/auth/login", json={"code": "654321"}).status_code == 200
    second.cookies.update(first.cookies)

    assert second.get("/api/auth/status").json()["auth"]["authenticated"] is False


@pytest.mark.parametrize("agent_name", ("ads_watching_agent", "notes_agent", "page_agent"))
def test_public_default_agents_are_open_even_when_global_otp_is_configured(monkeypatch, agent_name):
    """Public-by-default sites do not inherit the optional global OTP gate."""
    server_stub = sys.modules["server"]
    monkeypatch.setattr(
        server_stub.STATE,
        "config",
        {
            "security": {"totp_secret": "JBSWY3DPEHPK3PXP"},
            "agent_ui_security": {},
            "agent_websites": {"require_otp": True},
        },
    )
    monkeypatch.setattr(server_stub, "agent_has_assigned_2fa_profile", lambda _agent: True, raising=False)

    assert _get_agent_security_settings(agent_name) == {
        "auth_mode": "open",
        "session_ttl_days": 30,
        "bypass_global_otp": True,
        "per_agent_profile": False,
        "shared_session_eligible": True,
    }


def test_global_require_otp_gates_a_manifest_open_agent_without_bypass(monkeypatch):
    """donation_agent's manifest declares auth_default: open but does NOT set
    bypass_global_otp, so turning on the global "require OTP everywhere"
    toggle DOES gate it - the toggle is no longer structurally inert."""
    server_stub = sys.modules["server"]
    monkeypatch.setattr(
        server_stub.STATE,
        "config",
        {
            "security": {"totp_secret": "JBSWY3DPEHPK3PXP"},
            "agent_ui_security": {},
            "agent_websites": {"require_otp": True},
        },
    )
    monkeypatch.setattr(server_stub, "agent_has_assigned_2fa_profile", lambda _agent: False, raising=False)

    assert _get_agent_security_settings("donation_agent")["auth_mode"] == "totp"

    # With the toggle off (default), the manifest default applies unchanged.
    monkeypatch.setattr(
        server_stub.STATE,
        "config",
        {
            "security": {"totp_secret": "JBSWY3DPEHPK3PXP"},
            "agent_ui_security": {},
            "agent_websites": {"require_otp": False},
        },
    )
    assert _get_agent_security_settings("donation_agent")["auth_mode"] == "open"


def test_global_require_otp_gates_audio_manifest_open_default(monkeypatch):
    server_stub = sys.modules["server"]
    monkeypatch.setattr(
        server_stub.STATE,
        "config",
        {
            "security": {"totp_secret": "JBSWY3DPEHPK3PXP"},
            "agent_ui_security": {},
            "agent_websites": {"require_otp": True},
        },
    )
    monkeypatch.setattr(server_stub, "agent_has_assigned_2fa_profile", lambda _agent: False, raising=False)

    assert _get_agent_security_settings("audio_agent")["auth_mode"] == "totp"

    monkeypatch.setattr(
        server_stub.STATE,
        "config",
        {
            "security": {"totp_secret": "JBSWY3DPEHPK3PXP"},
            "agent_ui_security": {},
            "agent_websites": {"require_otp": False},
        },
    )
    assert _get_agent_security_settings("audio_agent")["auth_mode"] == "open"


def test_manifest_bypass_global_otp_exempts_an_agent_from_the_global_toggle(monkeypatch):
    """A synthetic manifest-declared bypass (not just ads_watching_agent's
    real one) stays open under the global toggle, proving the mechanism is
    generic rather than hardcoded to one agent name."""
    import autoyou_agents.shared_tools.scheduler_mission_control as smc

    server_stub = sys.modules["server"]
    monkeypatch.setattr(
        server_stub.STATE,
        "config",
        {
            "security": {"totp_secret": "JBSWY3DPEHPK3PXP"},
            "agent_ui_security": {},
            "agent_websites": {"require_otp": True},
        },
    )
    monkeypatch.setattr(server_stub, "agent_has_assigned_2fa_profile", lambda _agent: False, raising=False)
    monkeypatch.setattr(
        smc,
        "_resolve_manifest_auth_settings",
        lambda agent_name: {"auth_default": "open", "shared_session_eligible": True, "bypass_global_otp": True},
    )

    assert _get_agent_security_settings("some_future_open_agent")["auth_mode"] == "open"


@pytest.mark.parametrize("agent_name", ("ads_watching_agent", "notes_agent", "page_agent"))
def test_public_default_agents_can_be_re_gated_via_explicit_per_agent_config(monkeypatch, agent_name):
    server_stub = sys.modules["server"]
    monkeypatch.setattr(
        server_stub.STATE,
        "config",
        {
            "security": {"totp_secret": "JBSWY3DPEHPK3PXP"},
            "agent_ui_security": {agent_name: {"auth_mode": "totp"}},
            "agent_websites": {"require_otp": False},
        },
    )
    monkeypatch.setattr(server_stub, "agent_has_assigned_2fa_profile", lambda _agent: False, raising=False)

    assert _get_agent_security_settings(agent_name)["auth_mode"] == "totp"


def test_unknown_agent_with_no_manifest_still_defaults_to_gated(monkeypatch):
    server_stub = sys.modules["server"]
    monkeypatch.setattr(
        server_stub.STATE,
        "config",
        {
            "security": {"totp_secret": "JBSWY3DPEHPK3PXP"},
            "agent_ui_security": {},
            "agent_websites": {"require_otp": False},
        },
    )
    monkeypatch.setattr(server_stub, "agent_has_assigned_2fa_profile", lambda _agent: False, raising=False)

    assert _get_agent_security_settings("some_future_agent_without_a_manifest")["auth_mode"] == "totp"


def test_scheduler_app_index_uses_the_same_fail_closed_gate():
    from fastapi.testclient import TestClient

    client = TestClient(create_scheduler_mission_control_app("tasks"), raise_server_exceptions=True)
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="autoyou-auth-gate"' in response.text
    assert 'id="main-layout" class="main-layout hidden"' in response.text
    assert 'name="viewport"' in response.text.lower()


def test_chat_app_login_rejects_missing_code(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/api/auth/login", json={})
    assert resp.status_code == 400
    assert resp.json()["success"] is False


def test_chat_app_login_rejects_wrong_otp(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/api/auth/login", json={"code": "000000"})
    assert resp.status_code == 401
    assert resp.json()["success"] is False


def test_chat_app_logout_clears_cookie(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_chat_app_chat_endpoint_exists(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True)
    # Should return 401 (unauthenticated) or attempt proxying - just check it's not 404
    resp = client.post("/api/chat", json={"message": "hello"})
    assert resp.status_code != 404


def test_chat_app_security_endpoint_exists(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/api/security", json={"auth_mode": "open"})
    assert resp.status_code != 404


def test_open_agent_security_endpoint_rejects_anonymous_writer(tmp_path, monkeypatch):
    """An "open" agent's /api/security must require the real admin session.

    Regression: an open agent reports authenticated=True for every visitor by
    design. Before this fix, that alone was enough to let any anonymous
    visitor to a public agent website (e.g. donation_agent) POST /api/security
    and re-gate it as "totp" - a self-lockout/griefing DoS against a page that
    is supposed to stay public. Only the admin-dashboard session may do this.
    """
    from fastapi.testclient import TestClient
    import autoyou_agents.shared_tools.scheduler_mission_control as smc

    monkeypatch.setattr(
        smc, "_resolve_manifest_auth_settings",
        lambda agent_name: {"auth_default": "open", "shared_session_eligible": True},
    )
    server_stub = sys.modules["server"]
    monkeypatch.setattr(server_stub, "_is_logged_in", lambda request: False, raising=False)

    app = _make_app(tmp_path, agent_name="open_test_agent")
    client = TestClient(app, raise_server_exceptions=True)

    anon_resp = client.post("/api/security", json={"auth_mode": "totp"})
    assert anon_resp.status_code == 401
    assert _get_agent_security_settings("open_test_agent")["auth_mode"] == "open"

    monkeypatch.setattr(server_stub, "_is_logged_in", lambda request: True, raising=False)
    client.cookies.set("autoyou_session", "test-admin-token")
    admin_resp = client.post("/api/security", json={"auth_mode": "totp"})
    assert admin_resp.status_code == 200
    assert _get_agent_security_settings("open_test_agent")["auth_mode"] == "totp"


# ---------------------------------------------------------------------------
# Cookie name helper
# ---------------------------------------------------------------------------

def test_agent_chat_cookie_name_format():
    name = _agent_chat_cookie_name("website_agent")
    assert name == "autoyou_website_agent_chat_session"


def test_agent_chat_cookie_name_builder():
    name = _agent_chat_cookie_name("agent_builder_agent")
    assert name == "autoyou_agent_builder_agent_chat_session"


# ---------------------------------------------------------------------------
# CORS middleware is wired up
# ---------------------------------------------------------------------------

def test_chat_app_has_cors_middleware(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.options(
        "/api/bootstrap",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    # CORS middleware should respond with allow-origin for localhost
    assert "access-control-allow-origin" in resp.headers or resp.status_code in (200, 204)


# ---------------------------------------------------------------------------
# AGENT_WEBSITES_CONFIG_KEY constant
# ---------------------------------------------------------------------------

def test_agent_websites_config_key_value():
    assert AGENT_WEBSITES_CONFIG_KEY == "agent_websites"
