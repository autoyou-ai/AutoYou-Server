"""Agent websites open by default and retain every explicit OTP opt-in."""
from pathlib import Path
from types import SimpleNamespace

import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autoyou_agents.shared_tools import scheduler_mission_control as security


AGENTS_ROOT = Path(__file__).resolve().parents[3] / "autoyou_agents"
BUNDLED_WEBSITES = sorted(path.parents[1].name for path in AGENTS_ROOT.rglob("website/manifest.json"))
REMOTE = {"X-AutoYou-Remote-Browser": "webrtc"}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    server = SimpleNamespace(
        STATE=SimpleNamespace(config={"agent_websites": {}, "agent_ui_security": {}}),
        _default_config=lambda: {},
        _describe_totp_capabilities=lambda cfg: {"totp_configured": True},
        _get_pairing_totp_secret=lambda cfg: secret,
        _verify_totp_secret=lambda key, code: key == secret and totp.verify(code, for_time=1800000000),
        agent_has_assigned_2fa_profile=lambda name: False,
    )
    monkeypatch.setattr(security, "_runtime_server", lambda: server)
    monkeypatch.setattr(security, "_AGENT_UI_SESSIONS", {})
    monkeypatch.setattr(security, "_AGENT_UI_SESSIONS_FILE", str(tmp_path / "agent-sessions.json"))
    server.valid_code = totp.at(1800000000)
    return server


@pytest.mark.parametrize("agent_name", [*BUNDLED_WEBSITES, "synthetic_generated_agent"])
def test_bundled_and_inherited_websites_open_without_otp(runtime, agent_name):
    assert security._get_agent_security_settings(agent_name)["auth_mode"] == "open"


@pytest.mark.parametrize("configured", (True, False))
def test_global_opt_in_requires_otp_even_when_the_authenticator_is_missing(runtime, monkeypatch, configured):
    runtime.STATE.config["agent_websites"]["require_otp"] = True
    monkeypatch.setattr(runtime, "_describe_totp_capabilities", lambda cfg: {"totp_configured": configured})
    assert security._get_agent_security_settings("synthetic_generated_agent")["auth_mode"] == "totp"


@pytest.mark.parametrize("agent_name", BUNDLED_WEBSITES)
def test_all_bundled_websites_honor_the_global_otp_opt_in(runtime, agent_name):
    runtime.STATE.config["agent_websites"]["require_otp"] = True
    assert security._get_agent_security_settings(agent_name)["auth_mode"] == "totp"


def test_individual_opt_in_can_gate_an_open_manifest_with_a_global_bypass(runtime, monkeypatch):
    monkeypatch.setattr(security, "_resolve_manifest_auth_settings", lambda name: {
        "auth_default": "open", "bypass_global_otp": True, "shared_session_eligible": True,
    })
    runtime.STATE.config["agent_ui_security"]["page_agent"] = {"auth_mode": "totp"}
    assert security._get_agent_security_settings("page_agent")["auth_mode"] == "totp"


def test_assigned_profile_requires_otp_until_explicitly_opened(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "agent_has_assigned_2fa_profile", lambda name: True)
    assert security._get_agent_security_settings("page_agent")["per_agent_profile"] is True
    assert security._get_agent_security_settings("page_agent")["auth_mode"] == "totp"
    runtime.STATE.config["agent_ui_security"]["page_agent"] = {"auth_mode": "open"}
    assert security._get_agent_security_settings("page_agent")["auth_mode"] == "open"
    runtime.STATE.config["agent_ui_security"]["page_agent"]["auth_mode"] = "totp"
    runtime.STATE.config["agent_websites"]["disable_otp"] = True
    assert security._get_agent_security_settings("page_agent")["auth_mode"] == "open"


def test_remote_website_can_enable_verify_and_disable_otp(runtime):
    app = FastAPI()
    security.install_agent_website_auth(app, agent_name="synthetic_generated_agent", title="Synthetic", register_auth_routes=True)

    @app.get("/api/private")
    def private():
        return {"success": True}

    client = TestClient(app, headers=REMOTE)
    assert client.get("/api/private").status_code == 200
    runtime.STATE.config["agent_ui_security"]["synthetic_generated_agent"] = {"auth_mode": "totp"}
    assert client.get("/api/private").status_code == 401
    assert client.post("/api/auth/login", json={"totp_code": "invalid"}).status_code == 401
    assert client.post("/api/auth/login", json={"totp_code": runtime.valid_code}).status_code == 200
    assert client.get("/api/private").status_code == 200
    client.cookies.clear()
    runtime.STATE.config["agent_ui_security"].clear()
    assert client.get("/api/private").status_code == 200


def test_remote_global_opt_in_fails_closed_without_an_authenticator(runtime, monkeypatch):
    runtime.STATE.config["agent_websites"]["require_otp"] = True
    monkeypatch.setattr(runtime, "_describe_totp_capabilities", lambda cfg: {"totp_configured": False})
    app = FastAPI()
    security.install_agent_website_auth(app, agent_name="synthetic_generated_agent", title="Synthetic", register_auth_routes=True)

    @app.get("/")
    @app.get("/api/private")
    def index():
        return {"success": True}

    client = TestClient(app, headers=REMOTE)
    assert client.get("/api/private").status_code == 401
    assert "needs 2FA setup" in client.get("/").text
