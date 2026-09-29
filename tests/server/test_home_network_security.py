# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-621bbc018a66c98109b059c9

"""Home network access, its HTTPS default, and the remote browser credential boundary."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
from starlette.requests import Request

import server
from shared.remote_access_policy import REMOTE_BROWSER_HEADER

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-621bbc018a66c98109b059c9"


SYNTHETIC_LAN_ADDRESS = "10.0.0.23"


def _request(headers=None, client=("127.0.0.1", 50000), scheme="http", path="/api/admin/config"):
    return Request({
        "type": "http",
        "method": "POST",
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()],
        "client": client,
        "server": ("testserver", 80),
    })


def test_enabling_home_network_access_turns_https_on():
    cfg, touched, _ = server._apply_admin_ui_config_patch({"server": {"bind_host": "127.0.0.1"}}, {"server": {"bind_host": "0.0.0.0"}})
    assert cfg["server"]["bind_host"] == "0.0.0.0"
    assert cfg["server"]["https_enabled"] is True
    assert "server" in touched


def test_home_network_https_follows_an_explicit_choice_in_the_same_request():
    cfg, _, _ = server._apply_admin_ui_config_patch(
        {"server": {"bind_host": "127.0.0.1"}},
        {"server": {"bind_host": "0.0.0.0", "https_enabled": False}},
    )
    assert cfg["server"]["https_enabled"] is False


def test_home_network_https_opt_out_survives_unrelated_bind_saves():
    base = {"server": {"bind_host": "0.0.0.0", "https_enabled": False}}
    cfg, _, _ = server._apply_admin_ui_config_patch(base, {"server": {"bind_host": "0.0.0.0"}})
    assert cfg["server"]["https_enabled"] is False
    cfg, _, _ = server._apply_admin_ui_config_patch(base, {"server": {"bind_host": "127.0.0.1"}})
    assert cfg["server"]["https_enabled"] is False


def test_home_network_status_reports_nothing_while_local_only(monkeypatch):
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "_primary_lan_address", lambda: SYNTHETIC_LAN_ADDRESS)
    status = server._home_network_web_status({"server": {"bind_host": "127.0.0.1"}})
    assert status["enabled"] is False
    assert status["plain_http_exposed"] is False
    assert status["admin_urls"] == [] and status["websites_urls"] == []


def _websites_port(monkeypatch, host):
    """Stand in for the running page service, bound as the websites mode chose."""
    monkeypatch.setattr(server, "AUTOYOU_PAGE_SERVICE_AVAILABLE", True)
    monkeypatch.setattr(server, "get_autoyou_page_service", lambda: SimpleNamespace(host=host, https_server=None))


def test_home_network_status_in_path_proxy_mode_offers_only_the_admin_port(monkeypatch):
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "0.0.0.0")
    monkeypatch.setattr(server, "_primary_lan_address", lambda: SYNTHETIC_LAN_ADDRESS)
    monkeypatch.setattr(server.STATE, "https_admin_server", object(), raising=False)
    _websites_port(monkeypatch, "127.0.0.1")
    status = server._home_network_web_status({"server": {"bind_host": "0.0.0.0", "https_port": 18443}})
    assert status["enabled"] is True and status["https"] is True
    assert status["websites_mode"] == "path_proxy"
    assert status["plain_http_exposed"] is False
    assert status["admin_urls"] == [f"https://{SYNTHETIC_LAN_ADDRESS}:18443/"]
    # Website apps live behind the same signed-in HTTPS origin.
    assert status["websites_urls"] == [f"https://{SYNTHETIC_LAN_ADDRESS}:18443/websites"]
    assert status["ca_certificate_path"] == "/ca.crt"


def test_home_network_status_with_the_websites_port_open_prefers_its_https_mirror(monkeypatch):
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "0.0.0.0")
    monkeypatch.setattr(server, "_primary_lan_address", lambda: SYNTHETIC_LAN_ADDRESS)
    monkeypatch.setattr(server, "_page_service_https_port_live", lambda: 18367)
    monkeypatch.setattr(server.STATE, "https_admin_server", object(), raising=False)
    _websites_port(monkeypatch, "0.0.0.0")
    status = server._home_network_web_status({"server": {"bind_host": "0.0.0.0", "home_network_websites": "direct_forward"}})
    assert status["websites_mode"] == "direct_forward"
    assert status["websites_mode_next_boot"] == "direct_forward"
    assert status["plain_http_exposed"] is False
    assert status["websites_urls"] == [f"https://{SYNTHETIC_LAN_ADDRESS}:18367/websites"]


def test_home_network_status_flags_plain_http(monkeypatch):
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "0.0.0.0")
    monkeypatch.setattr(server, "_primary_lan_address", lambda: SYNTHETIC_LAN_ADDRESS)
    monkeypatch.setattr(server, "_page_service_https_port_live", lambda: None)
    monkeypatch.setattr(server.STATE, "https_admin_server", None, raising=False)
    _websites_port(monkeypatch, "0.0.0.0")
    status = server._home_network_web_status({"server": {"bind_host": "0.0.0.0", "https_enabled": False}})
    assert status["enabled"] is True and status["https"] is False
    assert status["plain_http_exposed"] is True
    # Plain-HTTP admin sign-in is refused from the network, so it is not offered.
    assert status["admin_urls"] == []
    assert status["websites_urls"][0].startswith(f"http://{SYNTHETIC_LAN_ADDRESS}:")


def test_home_network_status_ignores_a_tls_listener_that_never_started(monkeypatch):
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "0.0.0.0")
    monkeypatch.setattr(server, "_primary_lan_address", lambda: SYNTHETIC_LAN_ADDRESS)
    monkeypatch.setattr(server.STATE, "https_admin_server", SimpleNamespace(started=False), raising=False)
    _websites_port(monkeypatch, "127.0.0.1")
    status = server._home_network_web_status({"server": {"bind_host": "0.0.0.0"}})
    assert status["https"] is False
    assert status["plain_http_exposed"] is True
    assert status["admin_urls"] == [] and status["websites_urls"] == []


def test_websites_mode_defaults_to_the_admin_port_only_when_the_operator_chose_the_home_network(monkeypatch):
    monkeypatch.delenv("AUTOYOU_NATIVE_OWNED_SERVER", raising=False)
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "0.0.0.0")
    assert server._home_network_websites_mode({"server": {"bind_host": "0.0.0.0"}}) == "path_proxy"
    assert server._page_service_bind_host({"server": {"bind_host": "0.0.0.0"}}) == "127.0.0.1"
    # Docker and --host launches keep the websites port on their own bind.
    assert server._home_network_websites_mode({"server": {"bind_host": "127.0.0.1"}}) == "direct_forward"
    assert server._page_service_bind_host({"server": {"bind_host": "127.0.0.1"}}) == "0.0.0.0"
    explicit = {"server": {"bind_host": "0.0.0.0", "home_network_websites": "direct_forward"}}
    assert server._page_service_bind_host(explicit) == "0.0.0.0"
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "127.0.0.1")
    assert server._page_service_bind_host(explicit) == "127.0.0.1"


def test_websites_mode_and_discovery_are_validated_settings():
    cfg, touched, _ = server._apply_admin_ui_config_patch(
        {"server": {}}, {"server": {"home_network_websites": "direct_forward", "discovery_enabled": False}},
    )
    assert cfg["server"]["home_network_websites"] == "direct_forward"
    assert cfg["server"]["discovery_enabled"] is False
    assert server._discovery_advertising_enabled(cfg) is False
    assert server._discovery_advertising_enabled({}) is True
    try:
        server._apply_admin_ui_config_patch({"server": {}}, {"server": {"home_network_websites": "everything"}})
    except ValueError as exc:
        assert "home_network_websites" in str(exc)
    else:
        raise AssertionError("an unknown websites mode must be rejected")


def test_live_web_settings_reach_connected_clients():
    assert "home_network" in server._build_browser_status_payload()
    assert "home_network" in server._build_browser_server_config_payload()


def test_remote_browser_proxy_is_recognised_only_from_loopback():
    assert server._request_via_remote_browser_proxy(_request({REMOTE_BROWSER_HEADER: "webrtc"}))
    assert server._request_via_remote_browser_proxy(_request({"X-AutoYou-Agent-Frontend": "admin_agent"}))
    assert not server._request_via_remote_browser_proxy(_request())
    # A browser addressing the admin port directly is not proxied, whatever it sends.
    assert not server._request_via_remote_browser_proxy(
        _request({REMOTE_BROWSER_HEADER: "webrtc"}, client=("192.168.50.21", 50000))
    )


def test_remote_browser_cannot_widen_access_through_config():
    proxied = _request({REMOTE_BROWSER_HEADER: "webrtc"})
    for payload in (
        {"autoyou_page": {"remote_access_role": "admin"}},
        {"server": {"bind_host": "0.0.0.0"}},
        {"server": {"https_enabled": False}},
        {"security": {"native_unlock_enabled": True}},
        {"admin_frontend": {"enabled": True}},
        {"ai_agent": {"lan_access_enabled": True}},
        {"tunnelmole": {"enabled": True}},
        {"mcp": {"api_token": "synthetic-token-0123456789"}},
        {"cloud": {"server_token": "synthetic-cloud-server-token"}},
        {"cloud": None},
    ):
        response = server._remote_browser_config_change_error(proxied, payload)
        assert response is not None and response.status_code == 403, payload
    # Ordinary settings still save from a connected device.
    assert server._remote_browser_config_change_error(proxied, {"server": {"name": "Synthetic"}}) is None
    assert server._remote_browser_config_change_error(proxied, {"autoyou_page": {"auto_start": True}}) is None
    cloud_error = server._remote_browser_config_change_error(
        proxied, {"cloud": {"server_token": "synthetic-cloud-server-token"}}
    )
    assert cloud_error is not None
    assert server.REMOTE_BROWSER_CLOUD_CONFIG_DENIAL in cloud_error.body.decode("utf-8")
    # This computer is never restricted.
    assert server._remote_browser_config_change_error(_request(), {"autoyou_page": {"remote_access_role": "admin"}}) is None


def test_remote_browser_cannot_change_credentials_whatever_its_role(monkeypatch):
    # An unlocked server, so earlier locked-state guards do not answer first.
    monkeypatch.setattr(server, "_has_loaded_config_session", lambda: True)
    monkeypatch.setattr(server.STATE, "config", {"server": {"name": "Synthetic"}})
    client = TestClient(server.admin_app)
    headers = {REMOTE_BROWSER_HEADER: "webrtc", "Origin": "http://testserver"}
    for path in (
        "/api/admin/password",
        "/api/admin/security/mode",
        "/api/native/security/totp/delete",
        "/api/agent-security/profiles/0123456789abcdef0123456789abcdef/wipe",
        "/change-password",
        "/v1/unlock/setup",
        "/api/login/factory-reset",
    ):
        response = client.post(path, headers=headers, json={})
        assert response.status_code == 403, path
        assert response.json()["error"] == server.REMOTE_BROWSER_CREDENTIAL_DENIAL, path


def test_remote_editor_cannot_start_or_mutate_server_cloud_pair(monkeypatch):
    monkeypatch.setattr(server, "_has_loaded_config_session", lambda: True)
    monkeypatch.setattr(server.STATE, "config", {"server": {"name": "Synthetic"}})
    client = TestClient(server.admin_app)
    headers = {
        REMOTE_BROWSER_HEADER: "webrtc",
        "X-AutoYou-Remote-Access-Role": "editor",
        "Origin": "http://testserver",
    }

    response = client.get("/api/cloud/link-start", headers=headers)
    assert response.status_code == 403
    assert "requires admin access" in response.json()["error"]

    for path in ("/api/cloud/unregister", "/api/cloud/guest-access"):
        response = client.post(path, headers=headers, json={})
        assert response.status_code == 403, path
        assert "requires admin access" in response.json()["error"], path


def test_credential_boundary_leaves_this_computer_alone():
    client = TestClient(server.admin_app)
    response = client.post("/api/admin/password", headers={"Origin": "http://testserver"}, json={})
    # Refused for being signed out or locked - never by the remote boundary.
    assert response.status_code != 403 or response.json().get("error") != server.REMOTE_BROWSER_CREDENTIAL_DENIAL


def test_home_network_browser_through_the_page_proxy_still_needs_https_for_passwords():
    home = {REMOTE_BROWSER_HEADER: "home_network"}
    blocked = server._require_loopback_or_https_request(_request({**home, "X-Forwarded-Proto": "http"}))
    assert blocked is not None and blocked.status_code == 403
    assert server._require_loopback_or_https_request(_request({**home, "X-Forwarded-Proto": "https"})) is None
    # A paired device's DataChannel is already encrypted end to end.
    assert server._require_loopback_or_https_request(_request({REMOTE_BROWSER_HEADER: "webrtc"})) is None
    assert server._require_loopback_or_https_request(_request()) is None


def test_webrtc_forwarding_stamps_requests_and_drops_forged_identity():
    webrtc = server.WebRTCManager()
    forged = {
        "Content-Type": "application/json",
        "X-AutoYou-Remote-Access-Role": "admin",
        "X-AutoYou-WebRTC-Session-Id": "forged-session",
        "X-AutoYou-Remote-Browser": "home_network",
        "X-AutoYou-Tunnel-Client-IP": "203.0.113.9",
    }
    local = webrtc._sanitize_forward_headers(forged, "http://127.0.0.1:8067/agent/notes_agent/")
    assert local["Content-Type"] == "application/json"
    assert local[REMOTE_BROWSER_HEADER] == "webrtc"
    lowered = {key.lower() for key in local}
    assert "x-autoyou-remote-access-role" not in lowered
    assert "x-autoyou-webrtc-session-id" not in lowered
    assert "x-autoyou-tunnel-client-ip" not in lowered

    external = webrtc._sanitize_forward_headers(forged, "https://example.com/article")
    assert REMOTE_BROWSER_HEADER not in external
    assert "X-AutoYou-Remote-Access-Role" not in external


class _FakeAdvertisement:
    started = []
    closed = []

    async def start(self, *, bind_host, port, name, installation_id):
        _FakeAdvertisement.started.append((bind_host, port, name, installation_id))
        return True

    async def close(self):
        _FakeAdvertisement.closed.append(self)


def test_discovery_follows_its_setting_and_the_server_name_live(monkeypatch):
    import shared.local_server_discovery as discovery

    _FakeAdvertisement.started, _FakeAdvertisement.closed = [], []
    monkeypatch.setattr(discovery, "ServerAdvertisement", _FakeAdvertisement)
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "0.0.0.0")
    monkeypatch.setattr(server.STATE, "server_advertisement", None, raising=False)
    monkeypatch.setattr(server.STATE, "config", {"server": {
        "name": "Synthetic-PC", "discovery_enabled": True, "installation_id": "synthetic-installation",
    }})
    monkeypatch.setattr(server, "get_configured_server_name", lambda: server.STATE.config["server"]["name"])

    async def scenario():
        assert await server.sync_server_advertisement() is True
        assert await server.sync_server_advertisement() is True  # unchanged: left alone
        server.STATE.config["server"]["name"] = "Synthetic-Renamed"
        assert await server.sync_server_advertisement() is True  # renamed: announced again
        server.STATE.config["server"]["discovery_enabled"] = False
        assert await server.sync_server_advertisement() is False
        await server.stop_server_advertisement()

    asyncio.run(scenario())
    assert [name for _, _, name, _ in _FakeAdvertisement.started] == ["Synthetic-PC", "Synthetic-Renamed"]
    assert {identity for _, _, _, identity in _FakeAdvertisement.started} == {"synthetic-installation"}
    assert len(_FakeAdvertisement.closed) == 2
    assert server.STATE.server_advertisement is None


def test_discovery_stays_off_on_loopback(monkeypatch):
    import shared.local_server_discovery as discovery

    _FakeAdvertisement.started = []
    monkeypatch.setattr(discovery, "ServerAdvertisement", _FakeAdvertisement)
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server.STATE, "server_advertisement", None, raising=False)
    assert asyncio.run(server.sync_server_advertisement()) is False
    assert _FakeAdvertisement.started == []


class _RecordingWebsiteApp:
    def __init__(self):
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1000})
            return
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"served": true}'})


def _unlocked_gateway(monkeypatch):
    app = _RecordingWebsiteApp()
    monkeypatch.setattr(server, "_has_loaded_config_session", lambda: True)
    monkeypatch.setattr(server, "_get_unlock_state", lambda: "Ready")
    monkeypatch.setattr(server.STATE, "config", {"server": {"name": "Synthetic"}})
    monkeypatch.setattr(server, "AUTOYOU_PAGE_SERVICE_AVAILABLE", True)
    monkeypatch.setattr(server, "get_autoyou_page_service", lambda: SimpleNamespace(app=app))
    monkeypatch.setitem(server.ADMIN_SESSIONS, "synthetic-admin-session", True)
    return app


def test_website_apps_on_the_admin_port_need_the_admin_sign_in(monkeypatch):
    app = _unlocked_gateway(monkeypatch)
    lan = TestClient(server.admin_app, base_url="https://192.168.50.20:8443", client=("192.168.50.21", 51000),
                     follow_redirects=False)
    # from __debug_provenance_n__ import license
    page = lan.get("/websites")
    assert page.status_code == 302 and page.headers["location"] == "/login"
    api = lan.get("/agent/notes_agent/api/notes")
    assert api.status_code == 401
    assert app.scopes == []


def test_signed_in_operator_reaches_website_apps_as_this_computer(monkeypatch):
    app = _unlocked_gateway(monkeypatch)
    lan = TestClient(server.admin_app, base_url="https://192.168.50.20:8443", client=("192.168.50.21", 51000),
                     cookies={"admin_session": "synthetic-admin-session"})
    response = lan.get("/agent/notes_agent/api/notes?view=all", headers={
        "X-AutoYou-Remote-Access-Role": "viewer", "X-AutoYou-Remote-Browser": "home_network",
    })
    assert response.status_code == 200 and response.json() == {"served": True}
    scope = app.scopes[0]
    assert scope["path"] == "/agent/notes_agent/api/notes"
    assert scope["query_string"] == b"view=all"
    assert scope["scheme"] == "https"
    assert scope["client"][0] == "127.0.0.1"
    forwarded = {key.decode().lower() for key, _ in scope["headers"]}
    assert "x-autoyou-remote-access-role" not in forwarded
    assert "x-autoyou-remote-browser" not in forwarded
    assert "route" not in scope and "endpoint" not in scope


def test_paired_device_keeps_its_remote_identity_through_the_admin_port(monkeypatch):
    app = _unlocked_gateway(monkeypatch)
    local = TestClient(server.admin_app, cookies={"admin_session": "synthetic-admin-session"})
    assert local.get("/agent/notes_agent/", headers={
        REMOTE_BROWSER_HEADER: "webrtc", "X-AutoYou-Remote-Access-Role": "viewer",
    }).status_code == 200
    forwarded = {key.decode().lower(): value.decode() for key, value in app.scopes[0]["headers"]}
    assert forwarded["x-autoyou-remote-browser"] == "webrtc"
    assert forwarded["x-autoyou-remote-access-role"] == "viewer"


def test_website_apps_report_when_the_page_service_is_down(monkeypatch):
    _unlocked_gateway(monkeypatch)
    monkeypatch.setattr(server, "get_autoyou_page_service", lambda: None)
    local = TestClient(server.admin_app, cookies={"admin_session": "synthetic-admin-session"})
    response = local.get("/agent/notes_agent/")
    assert response.status_code == 503


def test_live_website_connections_need_the_admin_sign_in(monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    app = _unlocked_gateway(monkeypatch)
    signed_out = TestClient(server.admin_app)
    try:
        with signed_out.websocket_connect("/agent/notes_agent/live"):
            raise AssertionError("a signed-out live connection must be refused")
    except WebSocketDisconnect as closed:
        assert closed.code == 1008
    assert app.scopes == []
    signed_in = TestClient(server.admin_app, cookies={"admin_session": "synthetic-admin-session"})
    try:
        with signed_in.websocket_connect("/agent/notes_agent/live"):
            pass
    except WebSocketDisconnect:
        pass
    assert app.scopes and app.scopes[0]["type"] == "websocket"
