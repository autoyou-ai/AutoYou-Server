# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-726c79207375627461736b20-6bf326ff4bfb153976d3d965

"""Tests for the opt-in shared cross-agent OTP session in scheduler_mission_control.

Off by default. When enabled, completing OTP for one eligible agent website
also unlocks another eligible agent website in the same cookie jar - unless
that agent's own manifest opts out via shared_session_eligible: False.
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-726c79207375627461736b20-6bf326ff4bfb153976d3d965"


import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_DUMMY_SECRET = "JBSWY3DPEHPK3PXP"


def _build_server_stub(*, shared_session_enabled: bool, shared_session_ttl_days: int = 30) -> types.ModuleType:
    srv = types.ModuleType("server")

    class FakeState:
        config = {
            "security": {"totp_secret": _DUMMY_SECRET},
            "agent_ui_security": {},
            "agent_websites": {
                "require_otp": False,
                "shared_session_enabled": shared_session_enabled,
                "shared_session_ttl_days": shared_session_ttl_days,
            },
        }
        admin_session_token = "test-admin-token"

    srv.STATE = FakeState()

    def _default_config():
        return FakeState.config

    def _is_logged_in(request):
        return False

    def _verify_totp_secret(secret, code):
        return code == "123456"

    def _persist_state_config(cfg):
        pass

    def _describe_totp_capabilities(cfg):
        return {"totp_configured": True, "totp_required": True}

    def _get_pairing_totp_secret(cfg=None):
        return _DUMMY_SECRET

    def agent_has_assigned_2fa_profile(agent_name):
        return False

    srv._default_config = _default_config
    srv._is_logged_in = _is_logged_in
    srv._verify_totp_secret = _verify_totp_secret
    srv._persist_state_config = _persist_state_config
    srv._describe_totp_capabilities = _describe_totp_capabilities
    srv._get_pairing_totp_secret = _get_pairing_totp_secret
    srv.agent_has_assigned_2fa_profile = agent_has_assigned_2fa_profile
    return srv


@pytest.fixture
def server_stub(monkeypatch):
    def _install(*, shared_session_enabled: bool = False, shared_session_ttl_days: int = 30):
        stub = _build_server_stub(
            shared_session_enabled=shared_session_enabled,
            shared_session_ttl_days=shared_session_ttl_days,
        )
        monkeypatch.setitem(sys.modules, "server", stub)
        return stub

    return _install


@pytest.fixture(autouse=True)
def _clear_agent_sessions():
    from autoyou_agents.shared_tools import scheduler_mission_control as smc

    smc._AGENT_UI_SESSIONS.clear()
    yield
    smc._AGENT_UI_SESSIONS.clear()


def _make_chat_app(tmp_path: Path, agent_name: str):
    from autoyou_agents.shared_tools.scheduler_mission_control import create_agent_chat_app

    frontend_dir = tmp_path / agent_name / "frontend"
    (frontend_dir / "assets").mkdir(parents=True)
    (frontend_dir / "index.html").write_text(
        "<!DOCTYPE html><html><head></head><body>__BOOTSTRAP_JSON__</body></html>", encoding="utf-8"
    )
    return create_agent_chat_app(agent_name=agent_name, title=agent_name, frontend_dir=frontend_dir)


def test_shared_session_disabled_by_default_does_not_carry_cookie(tmp_path, server_stub):
    server_stub(shared_session_enabled=False)
    from autoyou_agents.shared_tools.scheduler_mission_control import SHARED_SESSION_COOKIE_NAME

    client = TestClient(_make_chat_app(tmp_path, "agent_a"))
    login = client.post("/api/auth/login", json={"code": "123456"})
    assert login.status_code == 200
    assert SHARED_SESSION_COOKIE_NAME not in client.cookies


def test_shared_session_enabled_unlocks_a_second_eligible_agent(tmp_path, server_stub, monkeypatch):
    server_stub(shared_session_enabled=True)
    from autoyou_agents.shared_tools import scheduler_mission_control as smc

    monkeypatch.setattr(
        smc,
        "_resolve_manifest_auth_settings",
        lambda agent_name: {"auth_default": "inherit", "shared_session_eligible": True},
    )

    first = TestClient(_make_chat_app(tmp_path / "first", "agent_a"))
    second = TestClient(_make_chat_app(tmp_path / "second", "agent_b"))

    login = first.post("/api/auth/login", json={"code": "123456"})
    assert login.status_code == 200
    assert smc.SHARED_SESSION_COOKIE_NAME in first.cookies

    second.cookies.set(smc.SHARED_SESSION_COOKIE_NAME, first.cookies[smc.SHARED_SESSION_COOKIE_NAME])
    status = second.get("/api/auth/status")
    assert status.json()["auth"]["authenticated"] is True
    assert status.json()["auth"]["via"] == "shared_session"


def test_enabling_sharing_upgrades_an_existing_agent_cookie(tmp_path, server_stub, monkeypatch):
    stub = server_stub(shared_session_enabled=False)
    from autoyou_agents.shared_tools import scheduler_mission_control as smc

    monkeypatch.setattr(smc, "_resolve_manifest_auth_settings", lambda _: {
        "auth_default": "inherit", "shared_session_eligible": True,
    })
    first = TestClient(_make_chat_app(tmp_path / "first", "agent_a"))
    second = TestClient(_make_chat_app(tmp_path / "second", "agent_b"))
    assert first.post("/api/auth/login", json={"code": "123456"}).status_code == 200
    assert smc.SHARED_SESSION_COOKIE_NAME not in first.cookies

    stub.STATE.config["agent_websites"]["shared_session_enabled"] = True
    asset = tmp_path / "first" / "agent_a" / "frontend" / "assets" / "test.css"
    asset.write_text("body{}", encoding="utf-8")
    assert first.get("/assets/test.css").status_code == 200
    assert smc.SHARED_SESSION_COOKIE_NAME not in first.cookies
    assert first.get("/api/auth/status").json()["auth"]["authenticated"] is True
    assert smc.SHARED_SESSION_COOKIE_NAME in first.cookies
    second.cookies.set(smc.SHARED_SESSION_COOKIE_NAME, first.cookies[smc.SHARED_SESSION_COOKIE_NAME])
    assert second.get("/api/auth/status").json()["auth"]["via"] == "shared_session"


def test_global_otp_off_overrides_agent_otp_profile(tmp_path, server_stub, monkeypatch):
    stub = server_stub(shared_session_enabled=False)
    from autoyou_agents.shared_tools import scheduler_mission_control as smc

    stub.STATE.config["agent_websites"]["disable_otp"] = True
    stub.STATE.config["agent_ui_security"]["agent_a"] = {"auth_mode": "totp"}
    stub.agent_has_assigned_2fa_profile = lambda _: True
    monkeypatch.setattr(smc, "_resolve_manifest_auth_settings", lambda _: {
        "auth_default": "gated", "shared_session_eligible": True,
    })
    client = TestClient(_make_chat_app(tmp_path, "agent_a"))
    auth = client.get("/api/auth/status").json()["auth"]
    assert auth["authenticated"] is True
    assert auth["auth_mode"] == "open"
    assert auth["per_agent_profile"] is False


def test_shared_session_ineligible_agent_rejects_shared_cookie(tmp_path, server_stub, monkeypatch):
    server_stub(shared_session_enabled=True)
    from autoyou_agents.shared_tools import scheduler_mission_control as smc

    def _fake_manifest(agent_name: str):
        if agent_name == "sensitive_agent":
            return {"auth_default": "inherit", "shared_session_eligible": False}
        return {"auth_default": "inherit", "shared_session_eligible": True}

    monkeypatch.setattr(smc, "_resolve_manifest_auth_settings", _fake_manifest)

    first = TestClient(_make_chat_app(tmp_path / "first", "agent_a"))
    second = TestClient(_make_chat_app(tmp_path / "second", "sensitive_agent"))

    login = first.post("/api/auth/login", json={"code": "123456"})
    assert login.status_code == 200

    second.cookies.set(smc.SHARED_SESSION_COOKIE_NAME, first.cookies[smc.SHARED_SESSION_COOKIE_NAME])
    status = second.get("/api/auth/status")
    assert status.json()["auth"]["authenticated"] is False


def test_clear_all_agent_sessions_invalidates_every_kind(tmp_path, server_stub, monkeypatch):
    server_stub(shared_session_enabled=True)
    from autoyou_agents.shared_tools import scheduler_mission_control as smc

    monkeypatch.setattr(
        smc,
        "_resolve_manifest_auth_settings",
        lambda agent_name: {"auth_default": "inherit", "shared_session_eligible": True},
    )

    client = TestClient(_make_chat_app(tmp_path, "agent_a"))
    login = client.post("/api/auth/login", json={"code": "123456"})
    assert login.status_code == 200
    assert client.get("/api/auth/status").json()["auth"]["authenticated"] is True

    invalidated = smc.clear_all_agent_sessions()
    assert invalidated >= 2  # per-agent chat session + shared session

    status = client.get("/api/auth/status")
    assert status.json()["auth"]["authenticated"] is False


def test_disabling_shared_sessions_can_revoke_only_shared_tokens(tmp_path, server_stub, monkeypatch):
    stub = server_stub(shared_session_enabled=True)
    from autoyou_agents.shared_tools import scheduler_mission_control as smc

    monkeypatch.setattr(smc, "_resolve_manifest_auth_settings", lambda _: {
        "auth_default": "inherit", "shared_session_eligible": True,
    })
    first = TestClient(_make_chat_app(tmp_path / "first", "agent_a"))
    second = TestClient(_make_chat_app(tmp_path / "second", "agent_b"))
    assert first.post("/api/auth/login", json={"code": "123456"}).status_code == 200
    second.cookies.set(smc.SHARED_SESSION_COOKIE_NAME, first.cookies[smc.SHARED_SESSION_COOKIE_NAME])
    assert second.get("/api/auth/status").json()["auth"]["authenticated"] is True

    stub.STATE.config["agent_websites"]["shared_session_enabled"] = False
    smc.clear_shared_agent_sessions()
    assert first.get("/api/auth/status").json()["auth"]["authenticated"] is True
    assert second.get("/api/auth/status").json()["auth"]["authenticated"] is False


def test_csrf_guard_blocks_mismatched_origin_and_allows_matching_origin(tmp_path, server_stub):
    server_stub(shared_session_enabled=False)
    client = TestClient(_make_chat_app(tmp_path, "agent_a"))

    blocked = client.post(
        "/api/auth/logout",
        headers={"Origin": "http://evil.example.com", "Host": "testserver"},
    )
    assert blocked.status_code == 403

    allowed = client.post(
        "/api/auth/logout",
        headers={"Origin": "http://testserver", "Host": "testserver"},
    )
    assert allowed.status_code == 200

    proxied = client.post(
        "/api/auth/logout",
        headers={
            "Origin": "http://127.0.0.1:8081",
            "Host": "127.0.0.1:8092",
            "X-Forwarded-Host": "127.0.0.1:8081",
        },
    )
    assert proxied.status_code == 200
