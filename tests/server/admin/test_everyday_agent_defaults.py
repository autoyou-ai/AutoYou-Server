"""Default agent availability and access, with isolated configuration."""
import pytest

import server
from autoyou_agents.shared_tools import scheduler_mission_control as security


EVERYDAY_WEBSITES = (
    "ads_watching_agent", "audio_agent", "backup_agent", "donation_agent",
    "earnings_agent", "game_agent", "notes_agent", "notify_agent", "page_agent",
    "persona_agent", "prompts_agent", "remote_desktop_agent", "tasks_agent", "tv_agent",
)


@pytest.mark.parametrize("agent_name", EVERYDAY_WEBSITES)
def test_everyday_websites_open_by_default_and_honor_explicit_otp(agent_name, monkeypatch):
    cfg = server._default_config()
    cfg["agent_websites"] = {"require_otp": True}
    monkeypatch.setattr(server.STATE, "config", cfg)
    monkeypatch.setattr(security, "_runtime_server", lambda: server)
    monkeypatch.setattr(security, "_totp_capabilities", lambda: {"totp_configured": True})
    monkeypatch.setattr(server, "agent_has_assigned_2fa_profile", lambda _: False)
    assert security._get_agent_security_settings(agent_name)["auth_mode"] == "open"
    cfg.setdefault("agent_ui_security", {})[agent_name] = {"auth_mode": "totp"}
    assert security._get_agent_security_settings(agent_name)["auth_mode"] == "totp"
    del cfg["agent_ui_security"][agent_name]
    assert security._get_agent_security_settings(agent_name)["auth_mode"] == "open"
    monkeypatch.setattr(server, "agent_has_assigned_2fa_profile", lambda _: True)
    assert security._get_agent_security_settings(agent_name)["auth_mode"] == "open"
    cfg["agent_ui_security"][agent_name] = {"auth_mode": "totp"}
    assigned = security._get_agent_security_settings(agent_name)
    assert assigned["auth_mode"] == "totp" and assigned["per_agent_profile"] is True
    cfg["agent_ui_security"][agent_name] = {"auth_mode": "open"}
    assert security._get_agent_security_settings(agent_name)["auth_mode"] == "open"


def test_new_servers_default_to_editor_and_preserve_saved_roles():
    assert server._default_config()["autoyou_page"]["remote_access_role"] == "editor"
    fresh = {}
    server._apply_default_autoyou_page_config(fresh)
    assert fresh["autoyou_page"]["remote_access_role"] == "editor"
    assert server._get_remote_browser_access_role({}) == "editor"
    assert server._admin_permissions_snapshot({})["remote_access_role"] == "editor"
    for role in ("viewer", "editor", "admin"):
        cfg = {"autoyou_page": {"remote_access_role": role}}
        server._apply_default_autoyou_page_config(cfg)
        assert server._get_remote_browser_access_role(cfg) == role
    # Invalid role claims still fail closed; changing a preference default
    # must not grant permissions to a missing or malformed identity header.
    assert server.normalize_remote_access_role(None) == "viewer"
    assert server._get_remote_browser_access_role({"autoyou_page": {"remote_access_role": "invalid"}}) == "viewer"


def test_game_website_enabled_by_default_and_preserves_an_explicit_disable():
    assert server._get_agent_frontend_enabled("game_agent", cfg={}) is True
    assert server._get_agent_frontend_enabled("game_agent", cfg={"agent_frontends": {"game_agent": {"enabled": False}}}) is False
    assert server._get_agent_frontend_enabled("admin_agent", cfg={}) is False
