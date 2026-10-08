# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""What a device on the home network can learn or reach without signing in.

Run with ``--host 0.0.0.0``, the admin port used to hand any device on the LAN
this computer's name, agent list, LAN address, folder paths (which name the
Windows account) and the full route map, and served the admin sign-in over
plain HTTP only.
"""

import pytest
from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server

LAN_PEER = ("192.168.50.21", 50000)  # RFC 1918, synthetic


@pytest.mark.parametrize("path", ["/api/v1/status", "/api/v1/server-config", "/api/status"])
def test_status_payloads_need_a_session_from_the_network(path, monkeypatch):
    lan = TestClient(server.admin_app, client=LAN_PEER)
    assert lan.get(path).status_code == 401

    local = TestClient(server.admin_app, client=("127.0.0.1", 50000))
    assert local.get(path).status_code == 200

    monkeypatch.setitem(server.ADMIN_SESSIONS, "synthetic-lan-session", True)
    signed_in = TestClient(server.admin_app, client=LAN_PEER, cookies={"admin_session": "synthetic-lan-session"})
    assert signed_in.get(path).status_code == 200


def test_tunnel_carried_status_request_needs_a_session():
    local = TestClient(server.admin_app, client=("127.0.0.1", 50000))
    response = local.get("/api/status", headers={"X-AutoYou-Tunnel-Client-IP": "8.8.8.8"})
    assert response.status_code == 401


def test_admin_route_map_is_not_served():
    assert TestClient(server.admin_app, client=("127.0.0.1", 50000)).get("/openapi.json").status_code == 404
    assert TestClient(server.auth_app, client=("127.0.0.1", 50000)).get("/openapi.json").status_code == 404
    # The schema is still available in-process for tooling and tests.
    assert server.admin_app.openapi()["info"]["title"] == "AutoYou Admin"


def test_launcher_network_bind_turns_https_on_by_default(monkeypatch):
    cfg = server._default_config()
    cfg.setdefault("server", {}).pop("https_enabled", None)
    cfg["server"]["bind_host"] = "127.0.0.1"
    monkeypatch.setattr(server, "_running_in_container", lambda: False)

    monkeypatch.setattr(server, "SERVER_BIND_HOST", "0.0.0.0")
    assert server._https_enabled(cfg) is True

    monkeypatch.setattr(server, "SERVER_BIND_HOST", "127.0.0.1")
    assert server._https_enabled(cfg) is False


def test_explicit_https_choice_and_containers_are_respected(monkeypatch):
    cfg = server._default_config()
    cfg.setdefault("server", {})["bind_host"] = "127.0.0.1"
    monkeypatch.setattr(server, "SERVER_BIND_HOST", "0.0.0.0")

    cfg["server"]["https_enabled"] = False
    monkeypatch.setattr(server, "_running_in_container", lambda: False)
    assert server._https_enabled(cfg) is False

    cfg["server"].pop("https_enabled")
    monkeypatch.setattr(server, "_running_in_container", lambda: True)
    assert server._https_enabled(cfg) is False
