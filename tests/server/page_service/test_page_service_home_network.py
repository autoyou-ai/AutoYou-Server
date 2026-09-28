# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Browsers on the home network reach pages and notes under HTTPS and the remote role."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import autoyou_page_service

LAN_PEER = ("192.168.50.21", 51000)
LAN_ORIGIN = "http://192.168.50.20:8067"


class _FakeUpstreamResponse:
    status_code = 200
    content = b'{"success": true}'
    headers = {"content-type": "application/json"}


@pytest.fixture
def upstream(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, "headers": dict(kwargs.get("headers") or {})})
        return _FakeUpstreamResponse()

    monkeypatch.setattr(autoyou_page_service.requests, "request", fake_request)
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {"frontends": [{"agent_name": "notes_agent", "proxy_port": 8094, "frontend_port_registered": True}]},
    )
    return calls


def _service(monkeypatch, role="viewer", **kwargs):
    monkeypatch.delenv(autoyou_page_service.TRUST_NETWORK_PEERS_ENV, raising=False)
    service = autoyou_page_service.AutoYouPageService(remote_access_role_provider=lambda: role, **kwargs)
    # The owner's theme preference lives outside the test root; never write it.
    service._set_ui_theme = lambda theme: "dark"
    return service


def test_home_network_viewer_reads_but_cannot_change(monkeypatch, upstream):
    service = _service(monkeypatch, role="viewer")
    lan = TestClient(service.app, base_url=LAN_ORIGIN, client=LAN_PEER)

    assert lan.get("/agent/notes_agent/api/notes").status_code == 200
    denied = lan.post("/agent/notes_agent/api/notes", json={"title": "Synthetic"})
    assert denied.status_code == 403
    assert denied.json()["remote_access_role"] == "viewer"
    assert lan.post("/api/ui/theme", json={"theme": "light"}).status_code == 403
    assert [call["method"] for call in upstream] == ["GET"]


def test_home_network_editor_can_add_but_not_delete(monkeypatch, upstream):
    service = _service(monkeypatch, role="editor")
    lan = TestClient(service.app, base_url=LAN_ORIGIN, client=LAN_PEER)

    assert lan.post("/agent/notes_agent/api/notes", json={"title": "Synthetic"}).status_code == 200
    assert lan.delete("/agent/notes_agent/api/notes/1").status_code == 403


def test_this_computer_keeps_owner_access(monkeypatch, upstream):
    service = _service(monkeypatch, role="viewer")
    local = TestClient(service.app)

    assert local.post("/agent/notes_agent/api/notes", json={"title": "Synthetic"}).status_code == 200
    assert local.post("/api/ui/theme", json={"theme": "light"}).status_code == 200
    forwarded = {key.lower() for key in upstream[0]["headers"]}
    assert "x-autoyou-remote-access-role" not in forwarded
    assert "x-autoyou-remote-browser" not in forwarded


def test_home_network_identity_is_stamped_not_trusted(monkeypatch, upstream):
    service = _service(monkeypatch, role="viewer")
    lan = TestClient(service.app, base_url=LAN_ORIGIN, client=LAN_PEER)

    response = lan.get("/agent/notes_agent/api/notes", headers={
        "X-AutoYou-Remote-Access-Role": "admin",
        "X-AutoYou-WebRTC-Session-Id": "forged-session",
        "X-AutoYou-Remote-Browser": "webrtc",
    })
    assert response.status_code == 200
    forwarded = {key.lower(): value for key, value in upstream[0]["headers"].items()}
    assert forwarded["x-autoyou-remote-access-role"] == "viewer"
    assert forwarded["x-autoyou-remote-browser"] == "home_network"
    assert forwarded["x-autoyou-agent-frontend"] == "notes_agent"
    assert "x-autoyou-webrtc-session-id" not in forwarded


def test_home_network_plain_http_moves_to_the_https_mirror(monkeypatch, upstream):
    service = _service(monkeypatch, role="admin", https_port=18367, ssl_certfile="leaf.pem", ssl_keyfile="leaf.key")
    lan = TestClient(service.app, base_url=LAN_ORIGIN, client=LAN_PEER, follow_redirects=False)

    moved = lan.get("/agent/notes_agent/?view=all")
    assert moved.status_code == 307
    assert moved.headers["location"] == "https://192.168.50.20:18367/agent/notes_agent/?view=all"

    refused = lan.post("/agent/notes_agent/api/notes", json={"title": "Synthetic"})
    assert refused.status_code == 403
    assert refused.json()["https_url"] == "https://192.168.50.20:18367/agent/notes_agent/api/notes"
    assert upstream == []

    secure = TestClient(service.app, base_url="https://192.168.50.20:18367", client=LAN_PEER)
    assert secure.post("/agent/notes_agent/api/notes", json={"title": "Synthetic"}).status_code == 200
    assert {key.lower(): value for key, value in upstream[0]["headers"].items()}["x-forwarded-proto"] == "https"


def test_home_network_redirect_only_builds_plain_hosts(monkeypatch):
    service = _service(monkeypatch, https_port=18367, ssl_certfile="leaf.pem", ssl_keyfile="leaf.key")

    def url(hostname, query=""):
        return SimpleNamespace(hostname=hostname, path="/websites", query=query)

    assert service._https_url_for(url("192.168.50.20", "q=notes")) == "https://192.168.50.20:18367/websites?q=notes"
    assert service._https_url_for(url("fe80::1")) == "https://[fe80::1]:18367/websites"
    assert service._https_url_for(url("synthetic-mac.local")) == "https://synthetic-mac.local:18367/websites"
    assert service._https_url_for(url("bad_host!")) is None
    assert service._https_url_for(url("")) is None


def test_home_network_live_connections_need_admin(monkeypatch, upstream):
    service = _service(monkeypatch, role="editor")
    lan = TestClient(service.app, base_url=LAN_ORIGIN, client=LAN_PEER)

    with pytest.raises(WebSocketDisconnect) as closed:
        with lan.websocket_connect("/agent/notes_agent/live"):
            pass
    assert closed.value.code == 1008


def test_trusted_network_peers_keep_owner_access(monkeypatch, upstream):
    service = _service(monkeypatch, role="viewer")
    monkeypatch.setenv(autoyou_page_service.TRUST_NETWORK_PEERS_ENV, "1")
    service.trust_network_peers = True
    lan = TestClient(service.app, base_url=LAN_ORIGIN, client=LAN_PEER)

    assert lan.post("/agent/notes_agent/api/notes", json={"title": "Synthetic"}).status_code == 200


def test_role_is_read_from_the_running_server_by_default(monkeypatch, upstream):
    import server

    monkeypatch.delenv(autoyou_page_service.TRUST_NETWORK_PEERS_ENV, raising=False)
    monkeypatch.setattr(server, "_get_remote_browser_access_role", lambda cfg=None: "editor")
    service = autoyou_page_service.AutoYouPageService()
    lan = TestClient(service.app, base_url=LAN_ORIGIN, client=LAN_PEER)

    assert lan.post("/agent/notes_agent/api/notes", json={"title": "Synthetic"}).status_code == 200
    assert lan.delete("/agent/notes_agent/api/notes/1").status_code == 403
