# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Permissions and capture controls are editable only by an app on this computer.

A tunnel or reverse proxy on the computer ends on loopback, so the peer address
alone cannot tell a phone on the internet from a local app. Every request here
comes from 127.0.0.1; only the forwarding headers a proxy adds differ.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import server
from shared.remote_access_policy import REMOTE_BROWSER_HEADER

CLIENT_ADDRESS = "203.0.113.9"  # reserved documentation range (RFC 5737)

FORWARDED_REQUEST_HEADERS = [
    pytest.param({"CF-Connecting-IP": CLIENT_ADDRESS}, id="cloudflare-tunnel"),
    pytest.param({"X-Forwarded-For": CLIENT_ADDRESS}, id="x-forwarded-for"),
    pytest.param({"Forwarded": f"for={CLIENT_ADDRESS};proto=https"}, id="forwarded"),
    pytest.param({server._BRIDGE_TRUSTED_CLIENT_IP_HEADER: CLIENT_ADDRESS}, id="autoyou-bridge"),
]

PERMISSION_PATCH = {"video_call": {"enabled": False}}
HARMLESS_PATCH = {"autoyou_page": {"theme": "light"}}


def _scope_request(headers=None, client=("127.0.0.1", 50000)) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/admin/permissions",
        "query_string": b"",
        "headers": [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()],
        "client": client,
        "server": ("127.0.0.1", 8001),
        "scheme": "http",
    })


@pytest.fixture
def local_admin(tmp_path, monkeypatch):
    """A loopback admin client with the server's config and persistence stubbed out."""
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(server, "_require_api_login", lambda _request: None)
    monkeypatch.setattr(server, "_config_write_block_reason", lambda: None)
    monkeypatch.setattr(server.STATE, "config", server._default_config(), raising=False)

    async def apply_update(_patch):
        return {"success": True}

    async def bootstrap_payload():
        return {"metadata": {}}

    monkeypatch.setattr(server, "_apply_admin_ui_config_update", apply_update)
    monkeypatch.setattr(server, "_build_admin_ui_bootstrap_payload", bootstrap_payload)
    monkeypatch.setattr(server, "WEBRTC", None)
    return TestClient(server.admin_app, base_url="http://127.0.0.1:8001", client=("127.0.0.1", 50000))


def test_app_on_this_computer_can_read_and_change_permissions(local_admin):
    assert local_admin.get("/api/admin/bootstrap").json()["metadata"]["permissions_editable"] is True
    assert local_admin.get("/api/admin/permissions").status_code == 200
    assert local_admin.post("/api/admin/permissions", json={"audio_call_enabled": False}).status_code == 200
    assert local_admin.post("/api/admin/config", json=PERMISSION_PATCH).status_code == 200


@pytest.mark.parametrize("headers", FORWARDED_REQUEST_HEADERS)
def test_tunnelled_request_from_loopback_cannot_read_or_change_permissions(local_admin, headers):
    assert local_admin.get("/api/admin/bootstrap", headers=headers).json()["metadata"]["permissions_editable"] is False
    assert local_admin.get("/api/admin/permissions", headers=headers).status_code == 403
    assert local_admin.post("/api/admin/permissions", json={"audio_call_enabled": False}, headers=headers).status_code == 403

    blocked = local_admin.post("/api/admin/config", json=PERMISSION_PATCH, headers=headers)
    assert blocked.status_code == 403
    assert "localhost" in blocked.json()["error"]


@pytest.mark.parametrize("headers", FORWARDED_REQUEST_HEADERS)
def test_tunnelled_admin_still_changes_settings_that_are_not_permissions(local_admin, headers):
    assert local_admin.post("/api/admin/config", json=HARMLESS_PATCH, headers=headers).status_code == 200


@pytest.mark.parametrize("headers,client,expected", [
    ({}, ("127.0.0.1", 50000), True),
    ({}, ("::1", 50000), True),
    ({REMOTE_BROWSER_HEADER: "webrtc"}, ("127.0.0.1", 50000), False),
    ({}, ("192.0.2.21", 50000), False),
    *[(params.values[0], ("127.0.0.1", 50000), False) for params in FORWARDED_REQUEST_HEADERS],
])
def test_request_is_from_this_computer_truth_table(headers, client, expected):
    request = _scope_request(headers, client)
    assert server._request_is_from_this_computer(request) is expected


@pytest.mark.parametrize("headers", [{}, *[params.values[0] for params in FORWARDED_REQUEST_HEADERS]])
def test_local_pair_ownership_follows_the_same_rule(headers):
    request = _scope_request(headers)
    owned = server._request_is_from_this_computer(request)
    assert server._local_pair_device_ownership(request) == (server.DEVICE_OWN if owned else server.DEVICE_SHARED)
