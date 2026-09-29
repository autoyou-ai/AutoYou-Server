# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-ff2517c7139607c71139a57d


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio
import copy
import os
import sys
from unittest.mock import AsyncMock

import pyotp
import pytest
from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-ff2517c7139607c71139a57d"


ensure_repo_on_path()

import server


@pytest.fixture(autouse=True)
def _isolate_config_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_FILE_PATH", str(tmp_path / "config.encrypted"))
    monkeypatch.setattr(server, "CONFIG_BAK_PATH", str(tmp_path / "config.encrypted.bak"))
    monkeypatch.setattr(server, "_get_server_keystore", lambda: None)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: False)


def _auth_client():
    client = TestClient(server.admin_app)
    server.ADMIN_SESSIONS["test-admin-session"] = True
    client.cookies.set("admin_session", "test-admin-session")
    client.headers.update(
        {
            "Origin": "http://testserver",
            "Referer": "http://testserver/admin",
        }
    )
    return client


def _capture_state():
    return {
        "config": copy.deepcopy(server.STATE.config),
        "server_password": server.STATE.server_password,
        "config_unlock_password": server.STATE.config_unlock_password,
        "config_store": server.STATE.config_store,
        "admin_sessions": dict(server.ADMIN_SESSIONS),
        "admin_api_tokens": dict(server.ADMIN_API_TOKENS),
    }


def test_admin_setup_api_route_count_uses_live_registered_routes():
    expected = 0
    for route in server.admin_app.routes:
        path = str(getattr(route, "path", "") or "")
        if not path.startswith(("/api/", "/v1/", "/admin/api/", "/admin/security/", "/webhook/")):
            continue
        methods = getattr(route, "methods", None) or {"GET"}
        expected += max(1, len([method for method in methods if method not in {"HEAD", "OPTIONS"}]))

    assert expected > 0
    assert server._admin_setup_api_route_count() == expected


def _restore_state(snapshot):
    server.STATE.config = snapshot["config"]
    server._set_config_session(
        config_store=snapshot["config_store"],
        server_password=snapshot["server_password"],
        config_unlock_password=snapshot["config_unlock_password"],
    )
    server.ADMIN_SESSIONS.clear()
    server.ADMIN_SESSIONS.update(snapshot["admin_sessions"])
    server.ADMIN_API_TOKENS.clear()
    server.ADMIN_API_TOKENS.update(snapshot["admin_api_tokens"])


def _set_encrypted_session(password: str):
    server._set_config_session(
        config_store=server.CONFIG_STORE_ENCRYPTED,
        server_password=password,
        config_unlock_password=password,
    )


def test_admin_totp_generate_pairing_secret_persists(monkeypatch):
    original_state = _capture_state()
    persisted = {}

    def fake_persist(config, *args, **kwargs):
        persisted["config"] = copy.deepcopy(config)
        server.STATE.config = config
        return server.CONFIG_STORE_ENCRYPTED

    try:
        server.STATE.config = {"server": {"name": "AutoYou-Server"}, "security": {}}
        _set_encrypted_session("secret")
        monkeypatch.setattr(server, "_persist_state_config", fake_persist)

        with _auth_client() as client:
            response = client.post("/admin/security/totp/generate", json={"target": "pairing"})

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["target"] == "pairing"
        assert body["secret"] == persisted["config"]["security"]["totp_secret"]
        assert body["account_name"].endswith("(pairing)")
        assert body["otpauth"].startswith("otpauth://totp/")
    finally:
        _restore_state(original_state)


def test_native_totp_setup_keeps_new_secret_pending_until_verified(monkeypatch):
    original_state = _capture_state()
    previous_secret = pyotp.random_base32()
    rendered = []
    try:
        server.STATE.config = {"server": {"name": "AutoYou"}, "security": {"totp_secret": previous_secret}}
        _set_encrypted_session("secret")
        monkeypatch.setattr(server, "_persist_state_config", lambda *args, **kwargs: pytest.fail("setup must stay pending"))
        monkeypatch.setattr(server, "_build_local_qr_data_url", lambda value: rendered.append(value) or "data:image/png;base64,c3ludGhldGlj")
        monkeypatch.setattr(server, "_build_qr_code_url", lambda *args, **kwargs: pytest.fail("native TOTP must not use a hosted QR"))

        with _auth_client() as client:
            response = client.post("/api/native/security/totp/setup", json={})

        body = response.json()
        assert response.status_code == 200
        assert body["success"] is True
        assert body["secret"] != previous_secret
        assert body["qr_data_url"].startswith("data:image/png;base64,")
        assert rendered[0].startswith("otpauth://totp/")
        assert server.STATE.config["security"]["totp_secret"] == previous_secret
        assert response.headers["cache-control"] == "no-store"
    finally:
        _restore_state(original_state)


def test_native_totp_confirm_rejects_wrong_code_and_saves_verified_secret(monkeypatch):
    original_state = _capture_state()
    previous_secret = pyotp.random_base32()
    pending_secret = pyotp.random_base32()
    persisted = []
    correct_code = pyotp.TOTP(pending_secret).now()
    wrong_code = f"{(int(correct_code) + 1) % 1000000:06d}"

    def save_config(config, **kwargs):
        persisted.append(copy.deepcopy(config))
        server.STATE.config = copy.deepcopy(config)
        return server.STATE.config

    try:
        server.STATE.config = {"server": {"name": "AutoYou"}, "security": {"totp_secret": previous_secret}}
        _set_encrypted_session("secret")
        monkeypatch.setattr(server, "_verify_totp_secret", lambda secret, code, **kwargs: secret == pending_secret and code == correct_code)
        monkeypatch.setattr(server, "_save_and_reload_state_config", save_config)

        with _auth_client() as client:
            invalid = client.post("/api/native/security/totp/confirm", json={"secret": pending_secret, "code": wrong_code})
            assert invalid.status_code == 400
            assert server.STATE.config["security"]["totp_secret"] == previous_secret
            assert persisted == []

            response = client.post("/api/native/security/totp/confirm", json={"secret": pending_secret, "code": correct_code})

        assert response.status_code == 200
        assert response.json()["totp_configured"] is True
        assert server.STATE.config["security"]["totp_secret"] == pending_secret
        assert len(persisted) == 1
    finally:
        _restore_state(original_state)


def test_admin_totp_show_returns_saved_secret(monkeypatch):
    original_state = _capture_state()

    try:
        secret = pyotp.random_base32()
        server.STATE.config = {
            "server": {"name": "AutoYou-Server"},
            "security": {"totp_secret": secret},
        }
        _set_encrypted_session("secret")
        monkeypatch.setattr(server, "_persist_state_config", lambda config, *args, **kwargs: server.CONFIG_STORE_ENCRYPTED)

        with _auth_client() as client:
            response = client.post("/admin/security/totp/show", json={"target": "pairing"})

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["target"] == "pairing"
        assert body["secret"] == secret
        assert body["account_name"].endswith("(pairing)")
    finally:
        _restore_state(original_state)


def test_admin_totp_import_accepts_otpauth_uri(monkeypatch):
    original_state = _capture_state()
    persisted = {}

    def fake_persist(config, *args, **kwargs):
        persisted["config"] = copy.deepcopy(config)
        server.STATE.config = config
        return server.CONFIG_STORE_ENCRYPTED

    try:
        secret = pyotp.random_base32()
        otpauth = pyotp.TOTP(secret).provisioning_uri(name="Operator", issuer_name="Saved-Issuer")
        server.STATE.config = {"server": {"name": "AutoYou-Server"}, "security": {}}
        _set_encrypted_session("secret")
        monkeypatch.setattr(server, "_persist_state_config", fake_persist)

        with _auth_client() as client:
            response = client.post(
                "/admin/security/totp/import",
                json={"target": "pairing", "value": otpauth},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["target"] == "pairing"
        assert body["secret"] == secret
        assert body["issuer"] == "Saved-Issuer"
        assert persisted["config"]["security"]["totp_secret"] == secret
    finally:
        _restore_state(original_state)


def test_admin_totp_delete_clears_pairing_secret(monkeypatch):
    original_state = _capture_state()
    persisted = {}

    def fake_persist(config, *args, **kwargs):
        persisted["config"] = copy.deepcopy(config)
        server.STATE.config = config
        return server.CONFIG_STORE_ENCRYPTED

    try:
        server.STATE.config = {
            "server": {"name": "AutoYou-Server"},
            "security": {"totp_secret": pyotp.random_base32()},
        }
        _set_encrypted_session("secret")
        monkeypatch.setattr(server, "_persist_state_config", fake_persist)

        with _auth_client() as client:
            response = client.post("/admin/security/totp/delete", json={"target": "pairing"})

        assert response.status_code == 200
        assert response.json() == {"success": True, "target": "pairing"}
        assert persisted["config"]["security"]["totp_secret"] == ""
    finally:
        _restore_state(original_state)


def test_admin_bootstrap_requires_authentication():
    client = TestClient(server.admin_app)

    response = client.get("/api/admin/bootstrap")

    assert response.status_code == 401
    assert response.json()["success"] is False


def test_admin_bootstrap_returns_live_snapshot(monkeypatch):
    original_state = _capture_state()

    try:
        server.STATE.config = {
            "server": {"name": "AutoYouRocks"},
            "autoyou_page": {
                "port": 8067,
                "auto_start": True,
                "timeline_days": 7,
                "custom_forward_enabled": False,
                "custom_forward_port": 8067,
                "theme": "light",
                "advertised_websites": [
                    {"port": 3000, "label": "Docs", "description": "Docs UI", "enabled": True}
                ],
            },
            "tunnelmole": {
                "enabled": True,
                "timeout_minutes": 5,
                "otp_timeout_minutes": 5,
                "otp_multiuse": False,
                "pair_code_mode": "authenticator",
                "connection_mode": "unmanaged",
            },
            "cloud": {"server_token": "tok", "server_id": "srv_123", "email": "owner@example.com"},
            "security": {"mode": "secure_professional", "totp_secret": ""},
            "ai_provider": {"provider": "ollama"},
            "ai_agent": {"enabled": True, "port": 8081, "auto_start": True, "record_messages_in_database": True},
            "telegram": {},
            "signal": {},
            "whatsapp": {},
            "ollama": {"enabled": True, "api_base": "http://localhost:11434", "model": "ministral-3:8b"},
            "speech": copy.deepcopy(server.deepcopy_speech_config()),
            "rtc": {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
        }

        monkeypatch.setattr(server, "_telegram_status", AsyncMock(return_value=("Connected", "@autoyou_bot")))
        monkeypatch.setattr(server, "_signal_status", AsyncMock(return_value=("Disabled", "-")))
        monkeypatch.setattr(server, "_whatsapp_status", AsyncMock(return_value=("Disabled", "-")))
        monkeypatch.setattr(server, "_ai_agent_server_status", AsyncMock(return_value="Running"))
        monkeypatch.setattr(server, "_autoyou_page_service_status", AsyncMock(return_value="Running"))
        monkeypatch.setattr(
            server,
            "_build_cloud_status_snapshot",
            AsyncMock(
                return_value={
                    "enrolled": True,
                    "registered": True,
                    "email": "owner@example.com",
                    "server_id": "srv_123",
                    "connected": True,
                }
            ),
        )
        monkeypatch.setattr(server, "get_tunnelmole_status", lambda: {"status": "running", "port": 8002, "public_url": "https://tm.example"})
        monkeypatch.setattr(
            server,
            "_build_agent_builder_listing_payload",
            lambda: {
                "status": "success",
                "agents": ["website_agent"],
                "agent_overview": [],
                "agent_details": {},
            },
        )
        monkeypatch.setattr(server, "_read_autoyou_ui_theme", lambda: "dark")

        with _auth_client() as client:
            response = client.get("/api/admin/bootstrap")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["admin"]["server_name"] == "AutoYouRocks"
        assert body["admin"]["server_id"] == "srv_123"
        assert body["admin"]["server_identity_key"] == "cloud:srv_123"
        assert body["admin"]["theme"] == "dark"
        assert body["status"]["ai_agent"]["status"] == "Running"
        assert body["status"]["browser"]["advertised_websites"][0]["label"] == "Docs"
        assert body["metadata"]["tunnelmole"]["pair_code_mode"] == "authenticator"
        assert body["metadata"]["tunnelmole"]["connection_mode"] == "unmanaged"
        assert {
            item["id"]: item["label"]
            for item in body["metadata"]["tunnelmole"]["pair_code_modes"]
        } == {
            "random_otp": "Random pairing code",
            "authenticator": "Authenticator code",
        }
        assert body["agents"]["agents"] == ["website_agent"]
    finally:
        _restore_state(original_state)


def test_admin_bootstrap_exposes_local_server_identity(monkeypatch):
    original_state = _capture_state()

    try:
        server.STATE.config = {
            "server": {"name": "AutoYouRocks", "installation_id": "local-install-123"},
            "cloud": {"server_token": "", "server_id": "", "email": ""},
            "security": {"mode": "normal", "totp_secret": ""},
            "telegram": {},
            "signal": {},
            "whatsapp": {},
            "ai_agent": {"enabled": True, "port": 8081, "auto_start": True},
            "autoyou_page": {"port": 8067, "auto_start": True, "timeline_days": 7},
            "speech": copy.deepcopy(server.deepcopy_speech_config()),
            "rtc": {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
        }

        monkeypatch.setattr(server, "_telegram_status", AsyncMock(return_value=("Disabled", "-")))
        monkeypatch.setattr(server, "_signal_status", AsyncMock(return_value=("Disabled", "-")))
        monkeypatch.setattr(server, "_whatsapp_status", AsyncMock(return_value=("Disabled", "-")))
        monkeypatch.setattr(server, "_ai_agent_server_status", AsyncMock(return_value="Running"))
        monkeypatch.setattr(server, "_autoyou_page_service_status", AsyncMock(return_value="Running"))
        monkeypatch.setattr(
            server,
            "_build_cloud_status_snapshot",
            AsyncMock(
                return_value={
                    "enrolled": False,
                    "registered": False,
                    "email": "",
                    "server_id": "",
                    "connected": False,
                }
            ),
        )
        monkeypatch.setattr(server, "get_tunnelmole_status", lambda: {"status": "stopped"})
        monkeypatch.setattr(
            server,
            "_safe_agent_builder_listing_payload",
            lambda: {"status": "success", "agents": [], "agent_overview": [], "agent_details": {}},
        )
        monkeypatch.setattr(server, "_read_autoyou_ui_theme", lambda: "dark")

        body = asyncio.run(server._build_admin_ui_bootstrap_payload())

        assert body["admin"]["server_name"] == "AutoYouRocks"
        assert body["admin"]["server_id"] == "local-install-123"
        assert body["admin"]["server_identity_key"] == "local:local-install-123"
        assert body["status"]["cloud"]["server_id"] == ""
    finally:
        _restore_state(original_state)


def test_admin_bootstrap_marks_live_messaging_services_enabled(monkeypatch):
    original_state = _capture_state()
    original_signal_service = server.STATE.signal_service
    original_whatsapp_service = server.STATE.whatsapp_service

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        server.STATE.config["signal"]["enabled"] = False
        server.STATE.config["whatsapp"]["enabled"] = False
        server.STATE.signal_service = object()
        server.STATE.whatsapp_service = object()

        monkeypatch.setattr(server, "_telegram_status", AsyncMock(return_value=("Not configured", "-")))
        monkeypatch.setattr(server, "_signal_status", AsyncMock(return_value=("Connected", "+15555550101")))
        monkeypatch.setattr(server, "_whatsapp_status", AsyncMock(return_value=("Connected", "+15555550102")))
        monkeypatch.setattr(server, "_ai_agent_server_status", AsyncMock(return_value="Running"))
        monkeypatch.setattr(server, "_autoyou_page_service_status", AsyncMock(return_value="Running"))
        monkeypatch.setattr(
            server,
            "_build_cloud_status_snapshot",
            AsyncMock(return_value={"enrolled": False, "registered": False, "connected": False}),
        )
        monkeypatch.setattr(server, "get_tunnelmole_status", lambda: {"status": "stopped"})
        monkeypatch.setattr(
            server,
            "_safe_agent_builder_listing_payload",
            lambda: {"status": "success", "agents": [], "agent_overview": [], "agent_details": {}},
        )

        body = asyncio.run(server._build_admin_ui_bootstrap_payload())

        assert body["config"]["signal"]["enabled"] is True
        assert body["config"]["whatsapp"]["enabled"] is True
        assert body["status"]["signal"]["status"] == "Connected"
        assert body["status"]["whatsapp"]["status"] == "Connected"
    finally:
        server.STATE.signal_service = original_signal_service
        server.STATE.whatsapp_service = original_whatsapp_service
        _restore_state(original_state)


def test_live_messaging_status_prefers_running_service_over_stale_disabled_config():
    original_state = _capture_state()
    original_signal_service = server.STATE.signal_service
    original_whatsapp_service = server.STATE.whatsapp_service

    class _LiveSignalService:
        async def get_detailed_status_async(self):
            return {
                "paired": True,
                "paired_phone_number": "+15555550103",
                "container_running": True,
            }

    class _LiveWhatsAppService:
        async def get_status(self):
            return {
                "paired": True,
                "phone_number": "+15555550104",
                "connection_healthy": True,
                "ready": True,
            }

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        server.STATE.config["signal"]["enabled"] = False
        server.STATE.config["whatsapp"]["enabled"] = False
        server.STATE.signal_service = _LiveSignalService()
        server.STATE.whatsapp_service = _LiveWhatsAppService()

        assert asyncio.run(server._signal_status()) == ("Connected", "+15555550103")
        assert asyncio.run(server._whatsapp_status()) == ("Connected", "+15555550104")
    finally:
        server.STATE.signal_service = original_signal_service
        server.STATE.whatsapp_service = original_whatsapp_service
        _restore_state(original_state)


def test_admin_config_patch_requires_authentication():
    client = TestClient(server.admin_app)
    client.headers.update(
        {
            "Origin": "http://testserver",
            "Referer": "http://testserver/admin",
        }
    )

    response = client.post("/api/admin/config", json={"server": {"name": "AutoYouRocks"}})

    assert response.status_code == 401
    assert response.json()["success"] is False


def test_admin_config_patch_refuses_unloaded_config(monkeypatch):
    original_state = _capture_state()

    def fail_save(*args, **kwargs):
        raise AssertionError("unloaded config must not be persisted")

    try:
        server.STATE.config = {}
        server._set_config_session(config_store=server.CONFIG_STORE_NONE)
        monkeypatch.setattr(server, "_save_and_reload_state_config", fail_save)

        with _auth_client() as client:
            response = client.post("/api/admin/config", json={"telegram": {"bot_token": ""}})

        assert response.status_code == 409
        assert "configuration is not loaded" in response.json()["error"]
    finally:
        _restore_state(original_state)


def test_admin_config_patch_preserves_existing_sections_on_partial_update(monkeypatch):
    original_state = _capture_state()

    def fake_save_and_reload(cfg, **kwargs):
        server.STATE.config = copy.deepcopy(cfg)
        return server.STATE.config

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        server.STATE.config["cloud"] = {
            "server_token": "prod-server-token",
            "server_id": "srv_prod",
            "user_id": "user_prod",
            "email": "owner@example.com",
            "registered_at": "2026-05-01T00:00:00Z",
        }
        server.STATE.config["telegram"]["bot_token"] = "123:prod-token"
        server.STATE.config["security"]["mode"] = "secure_professional"
        server.STATE.config["autoyou_page"]["custom_forward_enabled"] = True
        server.STATE.config["agent_frontends"]["admin_agent"] = True
        server.STATE.config.setdefault("admin", {})["frontend_proxy_enabled"] = True
        _set_encrypted_session("secret")

        monkeypatch.setattr(server, "_build_admin_ui_bootstrap_payload", AsyncMock(return_value={"success": True}))
        monkeypatch.setattr(server, "_save_and_reload_state_config", fake_save_and_reload)

        with _auth_client() as client:
            response = client.post("/api/admin/config", json={"server": {"name": "AutoYouRocks"}})

        assert response.status_code == 200
        assert server.STATE.config["server"]["name"] == "AutoYouRocks"
        assert server.STATE.config["cloud"]["server_token"] == "prod-server-token"
        assert server.STATE.config["telegram"]["bot_token"] == "123:prod-token"
        assert server.STATE.config["security"]["mode"] == "secure_professional"
        assert server.STATE.config["autoyou_page"]["custom_forward_enabled"] is True
        assert server.STATE.config["agent_frontends"]["admin_agent"] is True
    finally:
        _restore_state(original_state)


def test_admin_config_patch_updates_page_and_tunnelmole_without_autostart(monkeypatch):
    original_state = _capture_state()

    async def fake_bootstrap_payload():
        return {"success": True, "config": copy.deepcopy(server.STATE.config)}

    def fake_save_and_reload(cfg, **kwargs):
        server.STATE.config = copy.deepcopy(cfg)
        return server.STATE.config

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        _set_encrypted_session("secret")

        tunnelmole_start = AsyncMock(return_value=True)
        tunnelmole_unmanaged_start = AsyncMock(return_value=True)

        monkeypatch.setattr(server, "_build_admin_ui_bootstrap_payload", AsyncMock(side_effect=fake_bootstrap_payload))
        monkeypatch.setattr(server, "_save_and_reload_state_config", fake_save_and_reload)
        monkeypatch.setattr(server, "start_tunnelmole_service_with_timer", tunnelmole_start)
        monkeypatch.setattr(server, "start_tunnelmole_service_no_timer", tunnelmole_unmanaged_start)
        monkeypatch.setattr(server, "stop_tunnelmole_service", AsyncMock())
        monkeypatch.setattr(server, "start_or_restart_autoyou_page_service", AsyncMock())
        monkeypatch.setattr(server, "start_or_restart_telegram", AsyncMock())
        monkeypatch.setattr(server, "start_or_restart_signal", AsyncMock())
        monkeypatch.setattr(server, "start_or_restart_whatsapp", AsyncMock())
        monkeypatch.setattr(server, "restart_ai_agent_server", AsyncMock())
        monkeypatch.setattr(server, "stop_ai_agent_server", AsyncMock())

        with _auth_client() as client:
            response = client.post(
                "/api/admin/config",
                json={
                    "server": {"name": "AutoYouRocks"},
                    "autoyou_page": {
                        "timeline_days": 30,
                        "theme": "dark",
                        "advertised_websites": [
                            {"port": 3000, "label": "Docs", "description": "Docs UI", "enabled": True}
                        ],
                    },
                    "tunnelmole": {
                        "enabled": True,
                        "pair_code_mode": "authenticator",
                        "connection_mode": "unmanaged",
                    },
                },
            )

        assert response.status_code == 200
        assert server.STATE.config["server"]["name"] == "AutoYouRocks"
        assert server.STATE.config["autoyou_page"]["timeline_days"] == 30
        assert server.STATE.config["autoyou_page"]["theme"] == "dark"
        assert server.STATE.config["autoyou_page"]["advertised_websites"][0]["label"] == "Docs"
        assert server.STATE.config["tunnelmole"]["pair_code_mode"] == "authenticator"
        assert server.STATE.config["tunnelmole"]["connection_mode"] == "unmanaged"
        tunnelmole_unmanaged_start.assert_not_awaited()
        tunnelmole_start.assert_not_awaited()
    finally:
        _restore_state(original_state)


def test_admin_config_patch_refreshes_provider_env_before_ai_restart(monkeypatch):
    original_state = _capture_state()
    # from __debug_provenance_m__ import of
    env_refresh_calls = []

    async def fake_bootstrap_payload():
        return {"success": True, "config": copy.deepcopy(server.STATE.config)}

    def fake_save_and_reload(cfg, **kwargs):
        del kwargs
        server.STATE.config = copy.deepcopy(cfg)
        return server.STATE.config

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        _set_encrypted_session("secret")

        restart_ai = AsyncMock()
        monkeypatch.setattr(server, "_build_admin_ui_bootstrap_payload", AsyncMock(side_effect=fake_bootstrap_payload))
        monkeypatch.setattr(server, "_save_and_reload_state_config", fake_save_and_reload)
        monkeypatch.setattr(server, "_apply_google_api_config_to_env", lambda: env_refresh_calls.append("refresh"))
        monkeypatch.setattr(server, "restart_ai_agent_server", restart_ai)
        monkeypatch.setattr(server, "stop_ai_agent_server", AsyncMock())

        with _auth_client() as client:
            response = client.post(
                "/api/admin/config",
                json={
                    "ai_provider": {
                        "provider": "litellm",
                        "litellm_model": "openai/gpt-4o-mini",
                        "litellm_api_key": "sk-test-key",
                    }
                },
            )

        assert response.status_code == 200
        assert env_refresh_calls == ["refresh"]
        restart_ai.assert_awaited_once()
    finally:
        _restore_state(original_state)


def test_bookmark_admin_crud_and_clear_publishes_browser_shortcuts(monkeypatch):
    original_state = _capture_state()

    def fake_persist(config, *args, **kwargs):
        server.STATE.config = copy.deepcopy(config)
        return server.CONFIG_STORE_ENCRYPTED

    try:
        server.STATE.config = {"server": {"name": "AutoYou-Server"}, "autoyou_page": {"bookmarks": []}}
        _set_encrypted_session("secret")
        monkeypatch.setattr(server, "_persist_state_config", fake_persist)

        with _auth_client() as client:
            create_response = client.post(
                "/api/bookmarks",
                json={
                    "id": "docs",
                    "title": "Docs",
                    "url": "example.com/docs",
                    "description": "Project documentation",
                },
            )
            assert create_response.status_code == 200
            created = create_response.json()["bookmark"]
            assert created["id"] == "docs"
            assert created["url"] == "https://example.com/docs"

            list_response = client.get("/api/bookmarks")
            assert list_response.status_code == 200
            listed = list_response.json()
            assert listed["bookmarks"][0]["title"] == "Docs"
            assert listed["browser_shortcuts"][0]["id"] == "bookmark:docs"

            update_response = client.patch(
                "/api/bookmarks/docs",
                json={"title": "Reference", "url": "https://example.org/reference"},
            )
            assert update_response.status_code == 200
            assert server.STATE.config["autoyou_page"]["bookmarks"][0]["title"] == "Reference"
            assert server.STATE.config["autoyou_page"]["bookmarks"][0]["url"] == "https://example.org/reference"

            delete_response = client.delete("/api/bookmarks/docs")
            assert delete_response.status_code == 200
            assert delete_response.json()["bookmarks"] == []

            recreate_response = client.post("/api/bookmarks", json={"title": "Example", "url": "https://example.net/"})
            assert recreate_response.status_code == 200
            clear_response = client.post("/api/bookmarks/clear")
            assert clear_response.status_code == 200
            assert server.STATE.config["autoyou_page"]["bookmarks"] == []
    finally:
        _restore_state(original_state)


def test_admin_config_patch_accepts_bookmarks(monkeypatch):
    original_state = _capture_state()

    async def fake_bootstrap_payload():
        return {"success": True, "config": copy.deepcopy(server.STATE.config)}

    def fake_save_and_reload(cfg, **kwargs):
        server.STATE.config = copy.deepcopy(cfg)
        return server.STATE.config

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        _set_encrypted_session("secret")
        monkeypatch.setattr(server, "_build_admin_ui_bootstrap_payload", AsyncMock(side_effect=fake_bootstrap_payload))
        monkeypatch.setattr(server, "_save_and_reload_state_config", fake_save_and_reload)
        monkeypatch.setattr(server, "start_or_restart_autoyou_page_service", AsyncMock())

        with _auth_client() as client:
            response = client.post(
                "/api/admin/config",
                json={
                    "autoyou_page": {
                        "bookmarks": [
                            {
                                "id": "docs",
                                "title": "Docs",
                                "url": "https://example.com/docs",
                                "enabled": True,
                            }
                        ]
                    }
                },
            )

        assert response.status_code == 200
        assert server.STATE.config["autoyou_page"]["bookmarks"][0]["id"] == "docs"
        assert server.STATE.config["autoyou_page"]["bookmarks"][0]["url"] == "https://example.com/docs"
    finally:
        _restore_state(original_state)


def test_admin_dashboard_shell_returns_new_ui():
    with _auth_client() as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'id="autoyou-admin-root"' in response.text
    assert 'Loading AutoYou server admin...' in response.text
    assert '/assets/admin-ui.css' in response.text
    assert '/assets/admin-ui.js' in response.text


def test_admin_dashboard_alias_redirects_to_new_ui():
    with _auth_client() as client:
        response = client.get("/admin", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/"


def test_admin_dashboard_assets_are_served():
    with _auth_client() as client:
        css_response = client.get("/assets/admin-ui.css")
        js_response = client.get("/assets/admin-ui.js")

    assert css_response.status_code == 200
    assert css_response.headers["cache-control"] == "no-store, max-age=0"
    assert len(css_response.text) > 0
    assert "overflow-x: hidden" in css_response.text
    assert "height: 100dvh" in css_response.text
    assert "overflow-wrap: anywhere" in css_response.text
    assert "width: clamp(292px, 16vw, 320px)" in css_response.text
    assert "overflow-y: auto" in css_response.text
    assert "flex: 0 0 auto" in css_response.text
    assert ".ayu-sidebar-actions .ayu-form-btn" in css_response.text

    assert js_response.status_code == 200
    assert js_response.headers["cache-control"] == "no-store, max-age=0"
    assert len(js_response.text) > 0
    assert "shortText(serverName, 26)" not in js_response.text
    assert "title=\\\"" in js_response.text
    assert "function adminAssetUrl" in js_response.text
    assert "adminAssetUrl(getByPath(admin, \"logo_url\", \"/assets/logo.png\"))" in js_response.text


def test_admin_dashboard_shell_uses_saved_theme(monkeypatch):
    monkeypatch.setattr(server, "_read_autoyou_ui_theme", lambda: "light")

    with _auth_client() as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'data-theme="light"' in response.text


def test_admin_dashboard_legacy_query_redirects_to_long_page():
    with _auth_client() as client:
        response = client.get("/?legacy=1", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/legacy-dashboard"


def test_admin_legacy_dashboard_does_not_render_current_password(monkeypatch):
    original_state = _capture_state()

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        _set_encrypted_session("secret-password-that-must-not-render")
        monkeypatch.setattr(server, "_telegram_status", AsyncMock(return_value=("Disabled", "-")))
        monkeypatch.setattr(server, "_signal_status", AsyncMock(return_value=("Disabled", "-")))
        monkeypatch.setattr(server, "_whatsapp_status", AsyncMock(return_value=("Disabled", "-")))
        monkeypatch.setattr(
            server,
            "_ollama_status",
            AsyncMock(return_value=("Unavailable", "#ef4444", "http://localhost:11434", 0, "None")),
        )
        monkeypatch.setattr(
            server,
            "_google_api_status",
            AsyncMock(return_value=("Disabled", "#6b7280", False, "gemini-2.5-flash", "Missing")),
        )
        monkeypatch.setattr(server, "_ai_provider_summary", AsyncMock(return_value="Ollama"))
        monkeypatch.setattr(server, "_ai_agent_server_status", AsyncMock(return_value="Stopped"))
        monkeypatch.setattr(server, "get_tunnelmole_status", lambda: {"status": "stopped", "public_url": ""})
        monkeypatch.setattr(
            server,
            "_build_wizard_status_payload",
            lambda **kwargs: {"ollama": {}, "onboarding": {}, "remote_partner_ready": False},
        )
        monkeypatch.setattr(server, "_should_show_onboarding_wizard", lambda **kwargs: False)
        monkeypatch.setattr(
            server,
            "_build_cloud_status_snapshot",
            AsyncMock(
                return_value={
                    "enrolled": False,
                    "registered": False,
                    "connected": False,
                    "sse_connected": False,
                }
            ),
        )

        with _auth_client() as client:
            response = client.get("/legacy-dashboard")

        assert response.status_code == 200
        assert "/save-config" in response.text
        assert "secret-password-that-must-not-render" not in response.text
        assert "Current Password:" not in response.text
        assert "revealbtn" not in response.text
    finally:
        _restore_state(original_state)


def test_admin_security_mode_api_updates_mode(monkeypatch):
    original_state = _capture_state()

    async def fake_bootstrap_payload():
        return {"success": True, "config": copy.deepcopy(server.STATE.config)}

    def fake_save_and_reload(cfg, **kwargs):
        server.STATE.config = copy.deepcopy(cfg)
        return server.STATE.config

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        _set_encrypted_session("secret")

        monkeypatch.setattr(server, "_build_admin_ui_bootstrap_payload", AsyncMock(side_effect=fake_bootstrap_payload))
        monkeypatch.setattr(server, "_save_and_reload_state_config", fake_save_and_reload)

        with _auth_client() as client:
            response = client.post("/api/admin/security/mode", json={"mode": "secure_professional"})

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert server.STATE.config["security"]["mode"] == "secure_professional"
    finally:
        _restore_state(original_state)


def test_legacy_save_security_updates_active_mode(monkeypatch):
    original_state = _capture_state()

    def fake_save_and_reload(cfg, **kwargs):
        server.STATE.config = copy.deepcopy(cfg)
        return server.STATE.config

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        server.STATE.config.setdefault("security", {})["mode"] = "normal"
        _set_encrypted_session("secret")

        monkeypatch.setattr(server, "_save_and_reload_state_config", fake_save_and_reload)

        with _auth_client() as client:
            response = client.post(
                "/save-security",
                data={"security_mode": "secure"},
                follow_redirects=False,
            )

        assert response.status_code == 302
        assert server.get_security_mode() == "secure"
    finally:
        _restore_state(original_state)


def test_default_server_security_mode_is_secure():
    original_state = _capture_state()
    try:
        cfg = server._default_config()
        assert cfg["security"]["mode"] == "secure"

        server.STATE.config = {}
        assert server.get_security_mode() == "secure"
        assert server._get_security_mode_from_cfg({}) == "secure"
    finally:
        _restore_state(original_state)


def test_admin_config_patch_persists_ui_theme(monkeypatch):
    original_state = _capture_state()
    persisted = {}

    async def fake_bootstrap_payload():
        return {"success": True, "config": copy.deepcopy(server.STATE.config), "admin": {"theme": persisted.get("theme", "dark")}}

    def fake_save_and_reload(cfg, **kwargs):
        server.STATE.config = copy.deepcopy(cfg)
        return server.STATE.config

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        _set_encrypted_session("secret")

        monkeypatch.setattr(server, "_build_admin_ui_bootstrap_payload", AsyncMock(side_effect=fake_bootstrap_payload))
        monkeypatch.setattr(server, "_save_and_reload_state_config", fake_save_and_reload)
        monkeypatch.setattr(server, "_persist_autoyou_ui_theme", lambda theme, **kwargs: persisted.setdefault("theme", theme))

        with _auth_client() as client:
            response = client.post("/api/admin/config", json={"ui_theme": "light"})

        assert response.status_code == 200
        assert persisted["theme"] == "light"
    finally:
        _restore_state(original_state)


def test_admin_telegram_allow_code_api_enables_access_gate(monkeypatch):
    original_state = _capture_state()

    async def fake_bootstrap_payload():
        return {"success": True, "config": copy.deepcopy(server.STATE.config)}

    def fake_save_and_reload(cfg, **kwargs):
        server.STATE.config = copy.deepcopy(cfg)
        return server.STATE.config

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        server.STATE.config["telegram"]["bot_token"] = "test-bot-token"
        _set_encrypted_session("secret")

        monkeypatch.setattr(server, "_build_admin_ui_bootstrap_payload", AsyncMock(side_effect=fake_bootstrap_payload))
        monkeypatch.setattr(server, "_save_and_reload_state_config", fake_save_and_reload)
        monkeypatch.setattr(server, "_issue_telegram_allow_code", lambda: "ABC123")

        with _auth_client() as client:
            response = client.post("/api/admin/telegram/allow-code", json={})

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["allow_code"] == "ABC123"
        assert server.STATE.config["telegram"]["access_gate_enabled"] is True
    finally:
        _restore_state(original_state)


def test_admin_wizard_status_returns_setup_snapshot(monkeypatch):
    original_state = _capture_state()

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        server.STATE.config["telegram"]["bot_token"] = "test-bot-token"
        server.STATE.config["signal"]["enabled"] = True
        server.STATE.config["whatsapp"]["enabled"] = True
        server.STATE.config["ollama"]["model"] = server.DEFAULT_WIZARD_MODEL
        _set_encrypted_session("secret")

        monkeypatch.setattr(server, "_telegram_status", AsyncMock(return_value=("Connected", "@autoyou_bot")))
        monkeypatch.setattr(server, "_signal_status", AsyncMock(return_value=("Connected", "Signal Phone")))
        monkeypatch.setattr(server, "_whatsapp_status", AsyncMock(return_value=("Connected", "WhatsApp Phone")))
        monkeypatch.setattr(server, "get_tunnelmole_status", lambda: {"status": "running", "public_url": "https://tm.example"})
        monkeypatch.setattr(server, "_ai_provider_summary", AsyncMock(return_value="Ollama - mistral"))
        monkeypatch.setattr(
            server.model_library_service,
            "get_ollama_runtime_status",
            lambda api_base, selected_model: {"api_base": api_base, "reachable": True, "selected_model": selected_model},
        )
        monkeypatch.setattr(
            server.model_library_service,
            "list_local_models",
            lambda api_base: [{"name": server.DEFAULT_WIZARD_MODEL}],
        )

        with _auth_client() as client:
            response = client.get("/api/wizard/status")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["show_wizard"] is True
        assert body["wizard_completed"] is False
        assert body["ai_provider_summary"] == "Ollama - mistral"
        assert body["ollama"]["recommended_model_installed"] is True
        assert body["connectivity"]["tunnelmole_url"] == "https://tm.example"
        assert body["remote_partner_ready"] is True
        assert body["messaging"]["signal"]["paired"] is True
        assert body["messaging"]["whatsapp"]["paired"] is True
    finally:
        _restore_state(original_state)


def test_admin_config_patch_accepts_custom_tts_provider(monkeypatch):
    original_state = _capture_state()

    async def fake_bootstrap_payload():
        return {"success": True, "config": copy.deepcopy(server.STATE.config)}

    def fake_save_and_reload(cfg, **kwargs):
        server.STATE.config = copy.deepcopy(cfg)
        return server.STATE.config

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        _set_encrypted_session("secret")
        monkeypatch.setattr(server, "_build_admin_ui_bootstrap_payload", AsyncMock(side_effect=fake_bootstrap_payload))
        monkeypatch.setattr(server, "_save_and_reload_state_config", fake_save_and_reload)

        with _auth_client() as client:
            response = client.post(
                "/api/admin/config",
                json={"speech": {"tts": {"provider": "custom", "rate": 1.0}}},
            )

        assert response.status_code == 200
        assert server.STATE.config["speech"]["tts"]["provider"] == "custom"
    finally:
        _restore_state(original_state)


def test_admin_wizard_complete_updates_onboarding(monkeypatch):
    original_state = _capture_state()

    def fake_persist(config, *args, **kwargs):
        server.STATE.config = copy.deepcopy(config)
        return server.CONFIG_STORE_ENCRYPTED

    try:
        server.STATE.config = copy.deepcopy(server._default_config())
        _set_encrypted_session("secret")
        monkeypatch.setattr(server, "_persist_state_config", fake_persist)

        with _auth_client() as client:
            response = client.post("/api/wizard/complete", json={"completed": True})

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert server.STATE.config["onboarding"]["wizard_completed"] is True
        assert server.STATE.config["onboarding"]["wizard_completed_at"]
    finally:
        _restore_state(original_state)


def test_admin_dashboard_uses_modern_shared_totp_controls(monkeypatch):
    original_state = _capture_state()

    try:
        server.STATE.config = {
            "server": {"name": "AutoYou-Server"},
            "security": {
                "mode": "secure_professional",
                "totp_secret": pyotp.random_base32(),
            },
        }
        _set_encrypted_session("secret")
        monkeypatch.setattr(server, "_persist_state_config", lambda config, *args, **kwargs: server.CONFIG_STORE_ENCRYPTED)

        with _auth_client() as client:
            response = client.get("/assets/admin-ui.js")

        assert response.status_code == 200
        js = response.text
        assert "renderTotpManagementBody" in js
        assert "/admin/security/totp/generate" in js
        assert "/admin/security/totp/show" in js
        assert "/admin/security/totp/current-code" in js
        assert 'target: "pairing"' in js
        assert "Admin Login 2FA Secret" not in js
        assert "admin_totp_secret" not in js
    finally:
        _restore_state(original_state)


def test_admin_session_capabilities_reports_shared_2fa_availability():
    original_state = _capture_state()

    try:
        server.STATE.config = {
            "server": {"name": "AutoYou-Server"},
            "security": {
                "mode": "secure_professional",
                "totp_secret": pyotp.random_base32(),
            },
        }

        with TestClient(server.admin_app) as client:
            response = client.get("/api/admin/session/capabilities")

        assert response.status_code == 200
        body = response.json()
        assert body["security_mode"] == "secure_professional"
        assert body["totp_configured"] is True
        assert body["totp_client_count"] == 1
        assert body["usable_totp_client_count"] == 1
    finally:
        _restore_state(original_state)


def test_admin_session_capabilities_reports_not_configured_when_no_secret():
    original_state = _capture_state()

    try:
        server.STATE.config = {
            "server": {"name": "AutoYou-Server"},
            "security": {
                "mode": "secure_professional",
                "totp_secret": "",
            },
        }

        with TestClient(server.admin_app) as client:
            response = client.get("/api/admin/session/capabilities")

        assert response.status_code == 200
        body = response.json()
        assert body["totp_configured"] is False
        assert body["usable_totp_client_count"] == 0
    finally:
        _restore_state(original_state)


def test_admin_session_verify_rejects_when_no_2fa_secret():
    original_state = _capture_state()

    try:
        server.STATE.config = {
            "server": {"name": "AutoYou-Server"},
            "security": {"mode": "normal"},
        }

        with TestClient(server.admin_app) as client:
            client.headers.update(
                {
                    "Origin": "http://testserver",
                    "Referer": "http://testserver/admin",
                }
            )
            response = client.post("/api/admin/session/verify", json={"totp_code": "123456"})

        assert response.status_code == 400
        body = response.json()
        assert body["valid"] is False
        assert "2FA" in body["error"] or "TOTP" in body["error"]
    finally:
        _restore_state(original_state)


def test_admin_session_verify_accepts_shared_2fa_secret():
    original_state = _capture_state()

    try:
        secret = pyotp.random_base32()
        server.STATE.config = {
            "server": {"name": "AutoYou-Server"},
            "security": {"mode": "secure_professional", "totp_secret": secret},
        }

        with TestClient(server.admin_app) as client:
            client.headers.update(
                {
                    "Origin": "http://testserver",
                    "Referer": "http://testserver/admin",
                }
            )
            response = client.post(
                "/api/admin/session/verify",
                json={"totp_code": pyotp.TOTP(secret).now()},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert body["client_id"] == "admin"
        assert body["token"]
    finally:
        _restore_state(original_state)


def test_admin_session_verify_accepts_shared_2fa_secret_in_normal_mode():
    original_state = _capture_state()

    try:
        secret = pyotp.random_base32()
        server.STATE.config = {
            "server": {"name": "AutoYou-Server"},
            "security": {"mode": "normal", "totp_secret": secret},
        }

        with TestClient(server.admin_app) as client:
            client.headers.update(
                {
                    "Origin": "http://testserver",
                    "Referer": "http://testserver/admin",
                }
            )
            response = client.post(
                "/api/admin/session/verify",
                json={"totp_code": pyotp.TOTP(secret).now()},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert body["client_id"] == "admin"
        assert body["token"]
    finally:
        _restore_state(original_state)


def test_admin_totp_controls_are_available_from_modern_security_screen(monkeypatch):
    original_state = _capture_state()

    try:
        server.STATE.config = {
            "server": {"name": "AutoYou-Server"},
            "security": {"mode": "normal"},
        }
        _set_encrypted_session("secret")
        monkeypatch.setattr(server, "_persist_state_config", lambda config, *args, **kwargs: server.CONFIG_STORE_ENCRYPTED)

        with _auth_client() as client:
            response = client.get("/assets/admin-ui.js")

        assert response.status_code == 200
        js = response.text
        assert "Authenticator QR" in js
        assert "renderTotpManagementBody()" in js
        assert "Admin Login 2FA Secret" not in js
    finally:
        _restore_state(original_state)
