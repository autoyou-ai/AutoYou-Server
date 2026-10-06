# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The hosted game page and its input channel are for the local game engine, never a tunnel.

A tunnel on this computer ends on loopback and names a loopback Host, so only the headers it
adds tell it apart from a local app. Each of them must close the page and the status route.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

import pytest
from fastapi.testclient import TestClient

import server

PAGE = "/api/webrtc/hosted-game/play"
STATUS = "/api/webrtc/hosted-game/api/game/native-input/status"
CLIENT_ADDRESS = "203.0.113.9"  # reserved documentation range (RFC 5737)

TUNNEL_HEADERS = [
    pytest.param({"CF-Connecting-IP": CLIENT_ADDRESS}, id="cloudflare-tunnel"),
    pytest.param({"X-Forwarded-For": CLIENT_ADDRESS}, id="x-forwarded-for"),
    pytest.param({"X-Real-IP": CLIENT_ADDRESS}, id="x-real-ip"),
    pytest.param({"Forwarded": f"for={CLIENT_ADDRESS}"}, id="forwarded"),
    pytest.param({server._BRIDGE_TRUSTED_CLIENT_IP_HEADER: CLIENT_ADDRESS}, id="autoyou-bridge"),
]


@pytest.fixture
def local_client(monkeypatch):
    monkeypatch.setattr(server, "_get_game_mode_available", lambda cfg=None: True)
    with TestClient(server.admin_app, base_url="http://127.0.0.1:8001") as client:
        yield client


def test_a_local_app_still_gets_the_page_and_status(local_client):
    assert local_client.get(PAGE).status_code == 200
    assert local_client.get(STATUS).json()["available"] is True


@pytest.mark.parametrize("headers", TUNNEL_HEADERS)
def test_a_tunnelled_request_from_loopback_is_refused(local_client, headers):
    assert local_client.get(PAGE, headers=headers).status_code == 403
    assert local_client.get(STATUS, headers=headers).status_code == 403
