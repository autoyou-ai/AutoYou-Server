# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-96365184e13a2f822e88baf2

"""Tests for the AI Agent server's LAN-exposure hardening.

Covers three things found live this session: port 8081 (the ADK dev-ui/API
runtime) had zero authentication of its own, and used to silently follow the
admin UI's own `--host 0.0.0.0` bind. These tests lock in the fix: the AI
Agent server binds loopback-only by default regardless of the admin bind
host, an explicit opt-in is required to reach it from the LAN, and reaching
it that way requires a valid OTP code -- while the existing loopback port's
behavior stays completely unchanged.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-96365184e13a2f822e88baf2"


import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import (
    _ai_agent_lan_access_enabled,
    _ai_agent_lan_https_port,
    _configured_ai_agent_bind_host,
    _verify_totp_secret,
    AI_AGENT_TOTP_SECRET_ENV,
)
from routers.ai_agent import (
    _install_ai_agent_lan_otp_gate,
    _make_lan_otp_gate_token,
    _verify_lan_otp_gate_token,
)

_TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # arbitrary valid base32 test secret


# ---------------------------------------------------------------------------
# Bind-host / config resolution
# ---------------------------------------------------------------------------


def test_lan_access_disabled_by_default():
    assert _ai_agent_lan_access_enabled({}) is False
    assert _ai_agent_lan_access_enabled({"ai_agent": {}}) is False


def test_lan_access_enabled_via_config():
    assert _ai_agent_lan_access_enabled({"ai_agent": {"lan_access_enabled": True}}) is True


def test_lan_access_env_override_takes_priority_over_config():
    with patch.dict(os.environ, {"AUTOYOU_AI_AGENT_LAN_ACCESS": "1"}):
        assert _ai_agent_lan_access_enabled({"ai_agent": {"lan_access_enabled": False}}) is True
    with patch.dict(os.environ, {"AUTOYOU_AI_AGENT_LAN_ACCESS": "0"}):
        assert _ai_agent_lan_access_enabled({"ai_agent": {"lan_access_enabled": True}}) is False


def test_bind_host_stays_loopback_regardless_of_admin_bind_host():
    """The actual bug found live: AI Agent used to silently inherit
    --host 0.0.0.0 from the admin UI. It must not, by default."""
    import server

    with patch.object(server, "SERVER_BIND_HOST", "0.0.0.0"):
        assert _configured_ai_agent_bind_host({}) == "127.0.0.1"
        assert _configured_ai_agent_bind_host({"ai_agent": {"lan_access_enabled": False}}) == "127.0.0.1"


def test_bind_host_follows_admin_host_only_when_opted_in():
    import server

    with patch.object(server, "SERVER_BIND_HOST", "0.0.0.0"):
        assert _configured_ai_agent_bind_host({"ai_agent": {"lan_access_enabled": True}}) == "0.0.0.0"

    with patch.object(server, "SERVER_BIND_HOST", "127.0.0.1"):
        assert _configured_ai_agent_bind_host({"ai_agent": {"lan_access_enabled": True}}) == "127.0.0.1"


def test_lan_https_port_default_and_override():
    assert _ai_agent_lan_https_port({}) == 8481
    assert _ai_agent_lan_https_port({"ai_agent": {"lan_https_port": 9999}}) == 9999
    # Out-of-range values fall back to the default rather than being used as-is.
    assert _ai_agent_lan_https_port({"ai_agent": {"lan_https_port": 80}}) == 8481
    assert _ai_agent_lan_https_port({"ai_agent": {"lan_https_port": 99999}}) == 8481


# ---------------------------------------------------------------------------
# OTP gate token (stateless, short-lived session token)
# ---------------------------------------------------------------------------


def test_gate_token_round_trips():
    token = _make_lan_otp_gate_token(_TOTP_SECRET)
    assert _verify_lan_otp_gate_token(token, _TOTP_SECRET) is True


def test_gate_token_rejects_wrong_secret():
    token = _make_lan_otp_gate_token(_TOTP_SECRET)
    assert _verify_lan_otp_gate_token(token, "different-secret") is False


def test_gate_token_rejects_garbage():
    assert _verify_lan_otp_gate_token("not-a-real-token", _TOTP_SECRET) is False
    assert _verify_lan_otp_gate_token("", _TOTP_SECRET) is False


def test_gate_token_rejects_expired():
    import routers.ai_agent as ai_agent_module

    with patch.object(ai_agent_module, "_LAN_OTP_TOKEN_MAX_AGE_SECONDS", -1):
        token = _make_lan_otp_gate_token(_TOTP_SECRET)
        assert _verify_lan_otp_gate_token(token, _TOTP_SECRET) is False


# ---------------------------------------------------------------------------
# OTP gate middleware behavior
# ---------------------------------------------------------------------------


def _build_gated_app() -> FastAPI:
    app = FastAPI()
    _install_ai_agent_lan_otp_gate(app)

    @app.get("/probe")
    async def probe():
        return {"ok": True}

    return app


def test_plain_loopback_port_is_never_challenged():
    """The whole point of the fix: 8081's existing unauthenticated
    dev-ui convenience must survive completely unchanged."""
    app = _build_gated_app()
    with patch.dict(os.environ, {"AUTOYOU_AI_AGENT_LAN_HTTPS_PORT": "8481"}, clear=False):
        client = TestClient(app, base_url="http://testserver:8081")
        res = client.get("/probe")
        assert res.status_code == 200
        assert res.json() == {"ok": True}


def test_lan_port_rejects_requests_without_otp():
    app = _build_gated_app()
    env = {
        "AUTOYOU_AI_AGENT_LAN_HTTPS_PORT": "8481",
        AI_AGENT_TOTP_SECRET_ENV: _TOTP_SECRET,
    }
    with patch.dict(os.environ, env, clear=False):
        client = TestClient(app, base_url="http://testserver:8481")
        res = client.get("/probe")
        assert res.status_code == 401


def test_lan_port_rejects_wrong_otp_code():
    app = _build_gated_app()
    env = {
        "AUTOYOU_AI_AGENT_LAN_HTTPS_PORT": "8481",
        AI_AGENT_TOTP_SECRET_ENV: _TOTP_SECRET,
    }
    with patch.dict(os.environ, env, clear=False):
        client = TestClient(app, base_url="http://testserver:8481")
        res = client.get("/probe?otp=000000")
        assert res.status_code == 401


def test_lan_port_accepts_valid_otp_code_and_issues_session_cookie():
    import pyotp

    app = _build_gated_app()
    env = {
        "AUTOYOU_AI_AGENT_LAN_HTTPS_PORT": "8481",
        AI_AGENT_TOTP_SECRET_ENV: _TOTP_SECRET,
    }
    with patch.dict(os.environ, env, clear=False):
        code = pyotp.TOTP(_TOTP_SECRET).now()
        # https:// (not http://): this listener is only ever served over real
        # TLS in production, and the session cookie is Secure-flagged -- an
        # http:// client here would correctly never see it sent back, same
        # as a real browser wouldn't.
        client = TestClient(app, base_url="https://testserver:8481")
        res = client.get(f"/probe?otp={code}")
        assert res.status_code == 200
        assert res.json() == {"ok": True}

        # Subsequent requests use the issued session cookie, not a fresh code.
        res2 = client.get("/probe")
        assert res2.status_code == 200


def test_lan_port_without_configured_secret_fails_closed():
    """If the worker never received a TOTP secret (e.g. none configured
    server-side), the LAN port must refuse everything rather than fall open."""
    app = _build_gated_app()
    env = {"AUTOYOU_AI_AGENT_LAN_HTTPS_PORT": "8481"}
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop(AI_AGENT_TOTP_SECRET_ENV, None)
        client = TestClient(app, base_url="http://testserver:8481")
        res = client.get("/probe")
        assert res.status_code == 503


def test_verify_totp_secret_used_by_gate_matches_pyotp():
    import pyotp

    code = pyotp.TOTP(_TOTP_SECRET).now()
    assert _verify_totp_secret(_TOTP_SECRET, code) is True
    assert _verify_totp_secret(_TOTP_SECRET, "000000") is False
