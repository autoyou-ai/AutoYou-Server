# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The admin page's network and permissions panel reports what is true.

Each row must describe the running process, mention the next start only when a
saved change is pending, say who chose the bind host, and keep the caller's
access facts on every bootstrap so a save never turns Permissions view-only.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import server
from core_server.services import launch_bind_host_source


SYNTHETIC_LAN_ADDRESS = "192.0.2.23"
SYNTHETIC_LAN_PEER = "192.0.2.21"


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch):
    """Every test starts from a loopback process with no environment overrides."""
    for name in (
        "AUTOYOU_HTTPS_ENABLED",
        "AUTOYOU_ALLOW_REMOTE_ADMIN_PERMISSIONS",
        "AUTOYOU_AI_AGENT_LAN_ACCESS",
        "AUTOYOU_NATIVE_OWNED_SERVER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTOYOU_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "SERVER_BIND_HOST_SOURCE", "config")
    monkeypatch.setattr(server, "_primary_lan_address", lambda: SYNTHETIC_LAN_ADDRESS)
    monkeypatch.setattr(server, "AUTOYOU_PAGE_SERVICE_AVAILABLE", False)
    monkeypatch.setattr(server.STATE, "https_admin_server", None, raising=False)


def _request(client=("127.0.0.1", 50000)):
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "https",
        "path": "/api/admin/bootstrap",
        "raw_path": b"/api/admin/bootstrap",
        "query_string": b"",
        "headers": [],
        "client": client,
        "server": ("testserver", 443),
    })


# ---------------------------------------------------------------------------
# Who chose the bind host
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "environ", "expected"),
    [
        (["--admin", "8001"], {}, "config"),
        (["--host", "0.0.0.0", "--admin", "8001"], {}, "launcher"),
        (["--host=0.0.0.0"], {}, "launcher"),
        ([], {"AUTOYOU_BIND_HOST": "0.0.0.0"}, "launcher"),
        ([], {"AUTOYOU_BIND_HOST": "0.0.0.0", "AUTOYOU_NATIVE_OWNED_SERVER": "1"}, "desktop_app"),
    ],
)
def test_launch_records_who_chose_the_bind_host(argv, environ, expected):
    assert launch_bind_host_source(argv, environ) == expected


def test_runtime_bind_host_keeps_its_source_until_told_otherwise():
    server._set_runtime_bind_host("0.0.0.0", source="launcher")
    assert server.SERVER_BIND_HOST == "0.0.0.0"
    assert server.SERVER_BIND_HOST_SOURCE == "launcher"
    # Applying the saved host later never claims a different source by accident.
    server._set_runtime_bind_host("127.0.0.1")
    assert server.SERVER_BIND_HOST_SOURCE == "launcher"
    server._set_runtime_bind_host("127.0.0.1", source="not-a-source")
    assert server.SERVER_BIND_HOST_SOURCE == "launcher"


# ---------------------------------------------------------------------------
# Live state and the next start
# ---------------------------------------------------------------------------


def test_nothing_is_pending_when_the_saved_choice_matches_the_running_process(monkeypatch):
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "0.0.0.0")
    status = server._home_network_web_status({"server": {"bind_host": "0.0.0.0"}})
    assert status["enabled"] is True
    assert status["next_boot_enabled"] is True
    assert status["bind_host_source"] == "config"


def test_a_saved_change_is_pending_only_when_the_process_followed_the_saved_choice(monkeypatch):
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "0.0.0.0")
    status = server._home_network_web_status({"server": {"bind_host": "127.0.0.1"}})
    assert status["enabled"] is True
    assert status["saved_enabled"] is False
    assert status["next_boot_enabled"] is False


def test_a_launcher_host_wins_over_the_saved_choice_on_the_next_start(monkeypatch):
    """run_autoyou --host 0.0.0.0 passes the same host again, whatever is saved."""
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "0.0.0.0")
    monkeypatch.setattr(server, "SERVER_BIND_HOST_SOURCE", "launcher")
    status = server._home_network_web_status({"server": {"bind_host": "127.0.0.1"}})
    assert status["bind_host_source"] == "launcher"
    assert status["saved_enabled"] is False
    assert status["next_boot_enabled"] is True


def test_https_listener_is_reported_even_while_local_only(monkeypatch):
    monkeypatch.setattr(server.STATE, "https_admin_server", SimpleNamespace(started=True), raising=False)
    status = server._home_network_web_status({"server": {"bind_host": "127.0.0.1", "https_enabled": True}})
    # No home network, so nothing is offered to other devices ...
    assert status["https"] is False
    # ... but TLS is running, and running is what the panel shows.
    assert status["https_listener"] is True
    assert status["https_next_boot"] is True
    assert status["https_source"] == "config"


def test_https_and_remote_permission_sources_name_the_environment(monkeypatch):
    monkeypatch.setenv("AUTOYOU_HTTPS_ENABLED", "0")
    monkeypatch.setenv("AUTOYOU_ALLOW_REMOTE_ADMIN_PERMISSIONS", "1")
    status = server._home_network_web_status({"server": {"https_enabled": True}})
    assert status["https_source"] == "environment"
    assert status["https_next_boot"] is False
    assert status["allow_remote_admin_permissions_source"] == "environment"
    assert status["allow_remote_admin_permissions"] is True
    monkeypatch.delenv("AUTOYOU_HTTPS_ENABLED")
    monkeypatch.delenv("AUTOYOU_ALLOW_REMOTE_ADMIN_PERMISSIONS")
    status = server._home_network_web_status({"server": {}})
    assert status["https_source"] == "default"
    assert status["allow_remote_admin_permissions_source"] == "config"


def test_ai_developer_api_is_reported_with_its_own_port():
    status = server._home_network_web_status({"ai_agent": {"lan_access_enabled": True, "lan_https_port": 18481}})
    assert status["ai_agent_lan_access"] is True
    assert status["ai_agent_lan_https_port"] == 18481
    assert server._home_network_web_status({})["ai_agent_lan_access"] is False


# ---------------------------------------------------------------------------
# Settings that apply at once
# ---------------------------------------------------------------------------


def test_network_admin_permissions_apply_as_soon_as_they_are_saved(monkeypatch):
    lan_admin = _request(client=(SYNTHETIC_LAN_PEER, 50000))
    monkeypatch.setattr(server.STATE, "config", {"server": {}}, raising=False)
    assert server._request_is_from_this_computer(lan_admin) is False
    updated, touched, _ = server._apply_admin_ui_config_patch(
        server.STATE.config, {"server": {"allow_remote_admin_permissions": True}},
    )
    assert "server" in touched
    monkeypatch.setattr(server.STATE, "config", updated, raising=False)
    assert server._request_is_from_this_computer(lan_admin) is True
    assert server._admin_request_metadata(lan_admin)["permissions_editable"] is True


def test_system_credential_unlock_follows_the_saved_setting_at_once(monkeypatch):
    monkeypatch.setattr(server.STATE, "config", {"security": {}}, raising=False)
    assert server._native_unlock_enabled() is True
    updated, _, _ = server._apply_admin_ui_config_patch(
        server.STATE.config, {"security": {"native_unlock_enabled": False}},
    )
    monkeypatch.setattr(server.STATE, "config", updated, raising=False)
    assert server._native_unlock_enabled() is False


# ---------------------------------------------------------------------------
# Every bootstrap carries the caller's access facts
# ---------------------------------------------------------------------------


def test_request_metadata_reaches_a_bootstrap_nested_in_a_response():
    response = {"success": True, "bootstrap": {"config": {}}}
    server._with_admin_request_metadata(response, _request())
    assert response["bootstrap"]["metadata"]["permissions_editable"] is True
    assert response["bootstrap"]["metadata"]["is_loopback_client"] is True
    plain = server._with_admin_request_metadata({"metadata": {"guides": []}}, _request(client=(SYNTHETIC_LAN_PEER, 50000)))
    assert plain["metadata"]["guides"] == []
    assert plain["metadata"]["permissions_editable"] is False
    assert plain["metadata"]["is_loopback_client"] is False


def test_saving_settings_keeps_permissions_editable_for_this_computer(monkeypatch):
    """The bug: a save replaced the page's bootstrap with one lacking
    permissions_editable, so Permissions turned view-only until a reload."""
    monkeypatch.setattr(server, "_require_api_login", lambda _request: None)
    monkeypatch.setattr(server, "_config_write_block_reason", lambda: None)
    monkeypatch.setattr(server.STATE, "config", {"server": {}}, raising=False)

    async def apply_update(patch, **_kwargs):
        updated, _touched, _theme = server._apply_admin_ui_config_patch(server.STATE.config, patch)
        server.STATE.config = updated
        return {"success": True, "config": updated, "metadata": {}}

    monkeypatch.setattr(server, "_apply_admin_ui_config_update", apply_update)
    local = TestClient(server.admin_app, base_url="http://127.0.0.1:8001", client=("127.0.0.1", 50000))
    saved = local.post("/api/admin/config", json={"server": {"discovery_enabled": False}})
    assert saved.status_code == 200
    metadata = saved.json()["metadata"]
    assert metadata["permissions_editable"] is True
    assert metadata["is_loopback_client"] is True
    assert metadata["allow_remote_admin_permissions"] is False
    assert saved.json()["config"]["server"]["discovery_enabled"] is False


def test_saving_the_ai_developer_api_reads_back_after_refresh(monkeypatch):
    monkeypatch.setattr(server, "_require_api_login", lambda _request: None)
    monkeypatch.setattr(server, "_config_write_block_reason", lambda: None)
    monkeypatch.setattr(server.STATE, "config", server._default_config(), raising=False)

    async def apply_update(patch, **_kwargs):
        updated, _touched, _theme = server._apply_admin_ui_config_patch(server.STATE.config, patch)
        server.STATE.config = updated
        return {"success": True}

    monkeypatch.setattr(server, "_apply_admin_ui_config_update", apply_update)
    local = TestClient(server.admin_app, base_url="http://127.0.0.1:8001", client=("127.0.0.1", 50000))
    saved = local.post("/api/admin/permissions", json={"ai_agent_lan_access_enabled": True})
    assert saved.status_code == 200
    assert saved.json()["ai_agent_lan_access_enabled"] is True
    assert local.get("/api/admin/permissions").json()["ai_agent_lan_access_enabled"] is True
    assert server._home_network_web_status()["ai_agent_lan_access"] is True
    # Opting in never puts the unauthenticated plain port on the network.
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "0.0.0.0")
    monkeypatch.setattr(server, "_running_in_container", lambda: False)
    assert server._configured_ai_agent_bind_host() == "127.0.0.1"
