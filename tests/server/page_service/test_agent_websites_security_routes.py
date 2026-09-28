# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-dffbfa152c572d1735f36474

"""Tests for the /api/agent-websites/security admin routes and MANAGED_FRONTEND_APPS.

These are unit tests that mock the server STATE to avoid requiring a running server.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-dffbfa152c572d1735f36474"

import asyncio
from pathlib import Path
import types
from unittest.mock import patch, MagicMock

import server
from autoyou_agents.shared_tools.agent_install_registry import (
    BUILTIN_AGENT_PACKAGE_NAMES,
    PRIVATE_AGENT_PACKAGE_NAMES,
    can_install_agent_in_runtime,
)


# ---------------------------------------------------------------------------
# Helper - fake request with a valid login cookie
# ---------------------------------------------------------------------------

def _authed_request():
    """Return a minimal Request-like object accepted by _require_api_login."""
    cookies = {server.SESSION_COOKIE_NAME: server.STATE.admin_session_token or ""}
    req = MagicMock()
    req.cookies = cookies
    req.client = MagicMock()
    req.client.host = "127.0.0.1"
    return req


def _anon_request():
    req = MagicMock()
    req.cookies = {}
    req.client = MagicMock()
    req.client.host = "1.2.3.4"
    return req


def _has_frontend_manifest(agent_name: str) -> bool:
    for package_root in getattr(__import__("autoyou_agents"), "__path__", []):
        if (Path(package_root) / agent_name / "website" / "manifest.json").is_file():
            return True
    return False


# ---------------------------------------------------------------------------
# MANAGED_FRONTEND_APPS registration
# ---------------------------------------------------------------------------

def test_managed_frontend_apps_includes_website_and_builder():
    assert "website_agent" in server.MANAGED_FRONTEND_APPS
    assert "agent_builder_agent" in server.MANAGED_FRONTEND_APPS


def test_website_agent_uses_correct_port():
    cfg = server.MANAGED_FRONTEND_APPS["website_agent"]
    assert cfg["default_port"] == 8084


def test_agent_builder_agent_uses_correct_port():
    cfg = server.MANAGED_FRONTEND_APPS["agent_builder_agent"]
    assert cfg["default_port"] == 8085


def test_cloudflare_agent_is_opt_in_path_proxy():
    cfg = server.MANAGED_FRONTEND_APPS["cloudflare_agent"]
    assert cfg["app_import"] == "autoyou_agents.cloudflare_agent.website.backend.app:app"
    assert cfg["default_port"] == 8102
    if _has_frontend_manifest("cloudflare_agent"):
        assert server._managed_frontend_runtime_specs()["cloudflare_agent"]["recommended_port"] == 8102
    assert server.FRONTEND_DEFAULT_ENABLEMENT["cloudflare_agent"] is False
    assert server.FRONTEND_CONTROL_LABELS["cloudflare_agent"] == "Cloudflare Tunnel"


def test_ionos_agents_are_opt_in_path_proxies():
    expected = {
        "ionos_agent": ("autoyou_agents.ionos_agent.website.backend.app:app", 8103, "IONOS Hosting"),
        "ionos_cloudflare_agent": (
            "autoyou_agents.ionos_cloudflare_agent.website.backend.app:app",
            8104,
            "IONOS Cloudflare Handoff",
        ),
    }
    specs = server._managed_frontend_runtime_specs()
    for agent_name, (app_import, port, label) in expected.items():
        assert server.MANAGED_FRONTEND_APPS[agent_name] == {
            "app_import": app_import,
            "default_port": port,
        }
        if _has_frontend_manifest(agent_name):
            assert specs[agent_name]["recommended_port"] == port
        assert server.FRONTEND_DEFAULT_ENABLEMENT[agent_name] is False
        assert server.FRONTEND_CONTROL_LABELS[agent_name] == label


def test_win_security_agent_is_opt_in_path_proxy():
    cfg = server.MANAGED_FRONTEND_APPS["win_security_agent"]
    assert cfg["app_import"] == "autoyou_agents.win_security_agent.website.backend.app:app"
    assert cfg["default_port"] == 8075
    assert server.FRONTEND_DEFAULT_ENABLEMENT["win_security_agent"] is False
    assert server.FRONTEND_CONTROL_LABELS["win_security_agent"] == "Windows Security"
    # No longer a private package: opt-in by default, but installable when
    # compiled. The frontend stays off until the operator enables it.
    assert "win_security_agent" not in PRIVATE_AGENT_PACKAGE_NAMES
    assert "win_security_agent" in BUILTIN_AGENT_PACKAGE_NAMES
    assert can_install_agent_in_runtime("win_security_agent", compiled=True) is True


def test_mac_security_agent_is_opt_in_path_proxy():
    cfg = server.MANAGED_FRONTEND_APPS["mac_security_agent"]
    assert cfg["app_import"] == "autoyou_agents.mac_security_agent.website.backend.app:app"
    # 8076 is reserved for AutoYou Connect's local WebRTC browser proxy.
    assert cfg["default_port"] == 8101
    assert server._managed_frontend_runtime_specs()["mac_security_agent"]["recommended_port"] == 8101
    assert server.FRONTEND_DEFAULT_ENABLEMENT["mac_security_agent"] is False
    assert server.FRONTEND_CONTROL_LABELS["mac_security_agent"] == "macOS Security"
    # No longer a private package: opt-in by default, but installable when
    # compiled. The frontend stays off until the operator enables it.
    assert "mac_security_agent" not in PRIVATE_AGENT_PACKAGE_NAMES
    assert "mac_security_agent" in BUILTIN_AGENT_PACKAGE_NAMES
    assert can_install_agent_in_runtime("mac_security_agent", compiled=True) is True


def test_fine_tuning_agent_managed_frontend_registered():
    cfg = server.MANAGED_FRONTEND_APPS["fine_tuning_agent"]
    assert cfg["app_import"] == "autoyou_agents.fine_tuning_agent.website.backend.app:app"
    assert cfg["default_port"] == 8068
    assert server.FRONTEND_DEFAULT_ENABLEMENT.get("fine_tuning_agent") is True
    assert server.FRONTEND_CONTROL_LABELS.get("fine_tuning_agent") == "Fine Tuning App"


def test_location_agent_managed_frontend_is_opt_in_and_read_only():
    cfg = server.MANAGED_FRONTEND_APPS["location_agent"]
    assert cfg["app_import"] == "autoyou_agents.location_agent.website.backend.app:app"
    assert cfg["default_port"] == 8110
    assert server.FRONTEND_DEFAULT_ENABLEMENT.get("location_agent") is True
    assert server.FRONTEND_CONTROL_LABELS.get("location_agent") == "Location Timeline"
    assert "never requests browser location" in server.FRONTEND_CONTROL_HELP["location_agent"]


def test_data_collector_agent_managed_frontend_registered():
    cfg = server.MANAGED_FRONTEND_APPS["data_collector_agent"]
    assert cfg["app_import"] == "autoyou_agents.data_collector_agent.website.backend.app:app"
    assert cfg["default_port"] == 18067
    assert server.FRONTEND_DEFAULT_ENABLEMENT.get("data_collector_agent") is True
    assert server.FRONTEND_CONTROL_LABELS.get("data_collector_agent") == "Data Collector"
    assert "consented local conversation" in server.FRONTEND_CONTROL_HELP["data_collector_agent"]


def test_files_agent_managed_frontend_registered():
    cfg = server.MANAGED_FRONTEND_APPS["files_agent"]
    assert cfg["app_import"] == "autoyou_agents.files_agent.website.backend.app:app"
    assert cfg["default_port"] == 8070
    assert server.FRONTEND_DEFAULT_ENABLEMENT.get("files_agent") is True
    assert server.FRONTEND_CONTROL_LABELS.get("files_agent") == "Files Agent"


def test_education_agent_uses_the_renamed_managed_frontend_and_preserves_legacy_setting():
    cfg = {
        "agent_frontends": {
            "streaming_agent": {"enabled": False, "route_mode": "path_proxy"},
        }
    }

    assert server.MANAGED_FRONTEND_APPS["education_agent"]["app_import"] == (
        "autoyou_agents.education_agent.website.backend.app:app"
    )
    assert server._apply_default_agent_frontends_config(cfg) is True
    assert cfg["agent_frontends"]["education_agent"] == {
        "enabled": False,
        "route_mode": "path_proxy",
    }
    assert "streaming_agent" not in cfg["agent_frontends"]
    assert server._get_agent_frontend_enabled("education_agent", cfg=cfg) is False


def test_ads_watching_agent_managed_frontend_registered_and_direct_by_default():
    cfg = server.MANAGED_FRONTEND_APPS["ads_watching_agent"]
    assert cfg["app_import"] == "autoyou_agents.ads_watching_agent.website.backend.app:app"
    assert cfg["default_port"] == 8088
    assert server.FRONTEND_DEFAULT_ENABLEMENT.get("ads_watching_agent") is True
    assert server.FRONTEND_CONTROL_LABELS.get("ads_watching_agent") == "Ads Watching"

    default_cfg = server._default_config()
    assert default_cfg["agent_frontends"]["ads_watching_agent"] == {
        "enabled": True,
        "route_mode": "direct_forward",
    }
    assert (
        server._get_agent_frontend_route_mode(
            "ads_watching_agent",
            cfg={"agent_frontends": {}},
            frontend_entry={"agent_name": "ads_watching_agent", "proxy_port": 8088},
        )
        == "direct_forward"
    )


def test_fine_tuning_agent_managed_frontend_runtime_spec_uses_manifest_port():
    specs = server._managed_frontend_runtime_specs()

    assert specs["fine_tuning_agent"]["app_import"] == "autoyou_agents.fine_tuning_agent.website.backend.app:app"
    assert specs["fine_tuning_agent"]["recommended_port"] == 8068


def test_website_agent_is_default_enabled():
    assert server.FRONTEND_DEFAULT_ENABLEMENT.get("website_agent") is True


def test_agent_builder_is_default_enabled():
    assert server.FRONTEND_DEFAULT_ENABLEMENT.get("agent_builder_agent") is True


def test_installing_opt_in_agents_defaults_their_websites_on_without_overriding_preferences():
    cfg = {"agent_frontends": {}}
    for agent_name in (
        "agent_builder_agent",
        "media_generation_agent",
        "remote_desktop_agent",
        "website_agent",
    ):
        assert server._get_agent_frontend_enabled(agent_name, cfg=cfg) is True

    cfg["agent_frontends"]["remote_desktop_agent"] = {"enabled": False}
    assert server._get_agent_frontend_enabled("remote_desktop_agent", cfg=cfg) is False


def test_private_agent_defaults_remain_explicit_opt_ins():
    for agent_name in ("win_security_agent", "mac_security_agent"):
        assert server.FRONTEND_DEFAULT_ENABLEMENT[agent_name] is False


def test_website_agent_has_control_label():
    assert "website_agent" in server.FRONTEND_CONTROL_LABELS


def test_agent_builder_has_control_label():
    assert "agent_builder_agent" in server.FRONTEND_CONTROL_LABELS


# ---------------------------------------------------------------------------
# GET /api/agent-websites/security
# ---------------------------------------------------------------------------

def test_get_agent_websites_security_returns_require_otp_false_by_default(monkeypatch):
    original_config = server.STATE.config
    try:
        server.STATE.config = server._default_config()
        server.STATE.config.setdefault("agent_websites", {})["require_otp"] = False

        # Patch auth to always allow
        monkeypatch.setattr(server, "_require_api_login", lambda req: None)

        result = asyncio.run(server.admin_get_agent_websites_security(_anon_request()))
        data = result.body
        import json
        body = json.loads(data)
        assert body["success"] is True
        assert body["require_otp"] is False
    finally:
        server.STATE.config = original_config


def test_get_agent_websites_security_returns_true_when_enabled(monkeypatch):
    original_config = server.STATE.config
    try:
        cfg = server._default_config()
        cfg.setdefault("agent_websites", {})["require_otp"] = True
        server.STATE.config = cfg

        monkeypatch.setattr(server, "_require_api_login", lambda req: None)

        result = asyncio.run(server.admin_get_agent_websites_security(_anon_request()))
        import json
        body = json.loads(result.body)
        assert body["require_otp"] is True
    finally:
        server.STATE.config = original_config


# ---------------------------------------------------------------------------
# POST /api/agent-websites/security
# ---------------------------------------------------------------------------

def test_post_agent_websites_security_persists_require_otp(monkeypatch):
    import json as _json
    original_config = server.STATE.config
    original_config_store = server.STATE.config_store
    original_config_unlock_password = server.STATE.config_unlock_password
    persisted = {}

    def fake_persist(cfg):
        persisted["cfg"] = cfg

    try:
        server.STATE.config = server._default_config()
        server.STATE.config_store = server.CONFIG_STORE_KEYSTORE
        server.STATE.config_unlock_password = None
        monkeypatch.setattr(server, "_require_api_login", lambda req: None)
        monkeypatch.setattr(server, "_persist_state_config", fake_persist)

        req = _anon_request()

        async def fake_json():
            return {"require_otp": True}

        req.json = fake_json

        result = asyncio.run(server.admin_set_agent_websites_security(req))
        body = _json.loads(result.body)
        assert body["success"] is True
        assert body["require_otp"] is True
        assert persisted["cfg"]["agent_websites"]["require_otp"] is True
        assert server.STATE.config["agent_websites"]["require_otp"] is True
    finally:
        server.STATE.config = original_config
        server.STATE.config_store = original_config_store
        server.STATE.config_unlock_password = original_config_unlock_password


def test_post_agent_websites_security_can_disable_otp(monkeypatch):
    import json as _json
    original_config = server.STATE.config
    original_config_store = server.STATE.config_store
    original_config_unlock_password = server.STATE.config_unlock_password
    persisted = {}

    def fake_persist(cfg):
        persisted["cfg"] = cfg

    try:
        cfg = server._default_config()
        cfg.setdefault("agent_websites", {})["require_otp"] = True
        server.STATE.config = cfg
        server.STATE.config_store = server.CONFIG_STORE_KEYSTORE
        server.STATE.config_unlock_password = None
        monkeypatch.setattr(server, "_require_api_login", lambda req: None)
        monkeypatch.setattr(server, "_persist_state_config", fake_persist)

        req = _anon_request()

        async def fake_json():
            return {"require_otp": True, "disable_otp": True}

        req.json = fake_json

        result = asyncio.run(server.admin_set_agent_websites_security(req))
        body = _json.loads(result.body)
        assert body["require_otp"] is False
        assert body["disable_otp"] is True
        assert server.STATE.config["agent_websites"]["require_otp"] is False
        assert server.STATE.config["agent_websites"]["disable_otp"] is True
    finally:
        server.STATE.config = original_config
        server.STATE.config_store = original_config_store
        server.STATE.config_unlock_password = original_config_unlock_password


def test_enabling_shared_sessions_promotes_an_existing_agent_login(monkeypatch, tmp_path):
    from autoyou_agents.shared_tools import scheduler_mission_control as sessions

    original_config = server.STATE.config
    cfg = server._default_config()
    try:
        server.STATE.config = cfg
        monkeypatch.setattr(sessions, "_AGENT_UI_SESSIONS", {})
        monkeypatch.setattr(sessions, "_AGENT_UI_SESSIONS_FILE", str(tmp_path / "sessions.json"))
        monkeypatch.setattr(server, "_require_api_login", lambda _: None)
        monkeypatch.setattr(server, "_config_write_block_reason", lambda: None)
        monkeypatch.setattr(server, "_loaded_config_for_update", lambda: cfg)
        monkeypatch.setattr(server, "_persist_state_config", lambda _: None)
        token = sessions._create_agent_session("persona_agent", 30)
        request = _anon_request()
        request.cookies = {sessions._agent_cookie_name("persona_agent"): token}
        request.headers = {}
        request.url.scheme = "http"

        async def body():
            return {"shared_session_enabled": True}

        request.json = body
        response = asyncio.run(server.admin_set_agent_websites_security(request))
        assert response.status_code == 200
        assert sessions.SHARED_SESSION_COOKIE_NAME in response.headers.get("set-cookie", "")
    finally:
        server.STATE.config = original_config


# ---------------------------------------------------------------------------
# Default config includes agent_websites key
# ---------------------------------------------------------------------------

def test_default_config_has_agent_websites_section():
    cfg = server._default_config()
    assert "agent_websites" in cfg
    assert "require_otp" in cfg["agent_websites"]


def test_default_config_agent_websites_require_otp_is_false():
    cfg = server._default_config()
    assert cfg["agent_websites"]["require_otp"] is False
    assert cfg["agent_websites"]["disable_otp"] is False


def test_global_agent_otp_off_does_not_unlock_admin_settings(monkeypatch):
    original_config = server.STATE.config
    try:
        cfg = server._default_config()
        cfg["agent_websites"]["disable_otp"] = True
        server.STATE.config = cfg
        response = asyncio.run(server.admin_get_agent_websites_security(_anon_request()))
        assert response.status_code == 401
    finally:
        server.STATE.config = original_config


def test_webrtc_agent_proxy_shim_rewrites_dom_url_properties():
    html = server.WebRTCManager._inject_agent_proxy_shim(
        b"<html><head></head><body></body></html>",
        "/agent/voice_training_agent",
    ).decode("utf-8")

    assert "_pd(typeof HTMLMediaElement" in html
    assert '_pd(typeof HTMLSourceElement==="undefined"?null:HTMLSourceElement,"src");' in html
    assert 'set:function(v){return d.set.call(this,_r(v));}' in html


def test_webrtc_agent_proxy_shim_adds_mobile_viewport_when_missing():
    html = server.WebRTCManager._inject_agent_proxy_shim(
        b"<html><head></head><body></body></html>",
        "/agent/audio_agent",
    ).decode("utf-8")

    assert '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">' in html


def test_webrtc_agent_proxy_shim_keeps_page_directory_at_proxy_root():
    html = server.WebRTCManager._inject_agent_proxy_shim(
        b'<html><head></head><body><a href="/agent-websites">Websites</a><a href="../../agent-websites">Legacy</a><a href="/api/feed">Feed</a></body></html>',
        "/agent/page_agent",
    ).decode("utf-8")

    assert 'href="/agent-websites"' in html
    assert 'href="../../agent-websites"' in html
    assert 'href="/agent/page_agent/agent-websites"' not in html
    assert 'href="/agent/page_agent/api/feed"' in html
    assert "function _g(u)" in html


def test_agent_frontend_endpoint_persists_route_mode_without_touching_live_config(monkeypatch):
    import json as _json

    original_config = server.STATE.config
    cfg = server._default_config()
    cfg["agent_frontends"]["notes_agent"] = True
    persisted = {}

    async def fake_sync_managed_frontends():
        return {"notes_agent": 8094}

    def fake_listing():
        control = server._build_agent_frontend_control(
            "notes_agent",
            installed=True,
            frontend_entry={
                "agent_name": "notes_agent",
                "title": "Notes",
                "proxy_port": 8094,
                "entry_path": "/",
                "route_mode": "direct_forward",
                "uses_direct_forward_port": True,
            },
            active_frontend={
                "agent_name": "notes_agent",
                "title": "Notes",
                "proxy_port": 8094,
                "entry_path": "/",
                "route_mode": "direct_forward",
                "uses_direct_forward_port": True,
            },
            cfg=cfg,
        )
        return {"status": "success", "agent_details": {"notes_agent": {"frontend_control": control}}}

    try:
        server.STATE.config = cfg
        monkeypatch.setattr(server, "_require_api_login", lambda req: None)
        monkeypatch.setattr(server, "_config_write_block_reason", lambda: None)
        monkeypatch.setattr(server, "_loaded_config_for_update", lambda: cfg)
        monkeypatch.setattr(server, "_persist_state_config", lambda next_cfg: persisted.setdefault("cfg", next_cfg))
        monkeypatch.setattr(server, "_sync_frontend_registry_from_builder_payload", lambda payload: None)
        monkeypatch.setattr(server, "sync_managed_frontend_backends", fake_sync_managed_frontends)
        monkeypatch.setattr(server, "_build_agent_builder_listing_payload", fake_listing)
        monkeypatch.setattr(
            server,
            "discover_frontend_manifests",
            lambda **kwargs: [{"agent_name": "notes_agent", "proxy_port": 8094, "entry_path": "/"}],
        )

        req = _anon_request()

        async def fake_json():
            return {"agent_name": "notes_agent", "route_mode": "direct_forward"}

        req.json = fake_json

        result = asyncio.run(server.admin_set_agent_frontend_state(req))
        body = _json.loads(_json.dumps(result))
        assert body["success"] is True
        assert body["route_mode"] == "direct_forward"
        assert persisted["cfg"]["agent_frontends"]["notes_agent"] == {
            "enabled": True,
            "route_mode": "direct_forward",
        }
    finally:
        server.STATE.config = original_config


def test_workbench_save_manifest_route_forwards_stack_payload(monkeypatch):
    captured = {}

    monkeypatch.setattr(server, "_require_api_login", lambda req: None)
    monkeypatch.setattr(server, "_workspace_agents_root", lambda: "test-agents-root")
    monkeypatch.setattr(
        server,
        "_build_agent_workbench_success_response",
        lambda **kwargs: {"success": True, **kwargs},
    )

    def fake_save(agent_name, **kwargs):
        captured["agent_name"] = agent_name
        captured.update(kwargs)
        return {"status": "success"}

    monkeypatch.setattr(server, "save_draft_frontend_manifest", fake_save)

    req = _anon_request()

    async def fake_json():
        return {
            "title": "Demo Website",
            "description": "Synthetic website description.",
            "entry_path": "/",
            "recommended_port": 8093,
            "requires_proxy_registration": True,
            "frontend_stack": "react_typescript",
        }

    req.json = fake_json

    result = asyncio.run(server.admin_workbench_save_manifest("demo_agent", req))

    assert result["success"] is True
    assert captured["agent_name"] == "demo_agent"
    assert captured["title"] == "Demo Website"
    assert captured["frontend_stack"] == "react_typescript"
    assert captured["agents_root"] == "test-agents-root"
