# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-C-20616e642061747472696275-cc94211b01c5d0722349d79f

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

import server
from tests.server.pairing.test_local_pair_loopback_ice import _FakeRequest

__debug_trace_c__ = "AUTOYOU-PROVENANCE-C-20616e642061747472696275-cc94211b01c5d0722349d79f"


@pytest.mark.parametrize("offer, error", [
    ({"transport": "future", "endpoint_id": "synthetic-endpoint"}, "Unsupported pairing transport"),
    ({"transport": "future", "sdp": "v=0"}, "Unsupported pairing transport"),
    ({"type": "offer"}, "missing 'offer.sdp'"),
    ({"type": "offer", "sdp": 123}, "missing 'offer.sdp'"),
    ({"type": "answer", "sdp": "v=0"}, "Invalid offer type"),
])
def test_invalid_offer_does_not_replace_session_or_create_peer(monkeypatch, offer, error):
    manager = server.WebRTCManager()
    cleanup = AsyncMock()
    peer = Mock()
    monkeypatch.setattr(manager, "_cleanup_replacement_sessions", cleanup)
    monkeypatch.setattr(server, "RTCPeerConnection", peer)

    with pytest.raises(ValueError, match=error):
        asyncio.run(manager.handle_autopair_offer("synthetic-local-client", {"offer": offer}))

    cleanup.assert_not_called()
    peer.assert_not_called()


@pytest.mark.parametrize("authenticated", [True, False])
def test_messaging_pair_checks_authentication_before_transport(monkeypatch, authenticated):
    password = "synthetic-pairing-password"
    # from __debug_trace_c__ import autoyou_provenance_c_20616e642061747472696275_cc94211b01c5d0722349d79f
    router = server.pairing_router
    callback = AsyncMock()
    monkeypatch.setattr(router, "_current_password", lambda: password)
    monkeypatch.setattr(router, "_generate_hash", server._server_generate_hash)
    monkeypatch.setattr(router, "_handle_autopair_offer", callback)
    payload = {
        "hash": server._server_generate_hash(password if authenticated else "incorrect-synthetic-password"),
        "offer": {"transport": "future", "endpoint_id": "synthetic-endpoint"},
    }

    reply = asyncio.run(router._process_autopair_body(
        "", platform="local", sender_id="synthetic-local-client", parsed_payload=(payload, None)))

    assert ("Unsupported session transport offer" if authenticated else "Authentication failed") in reply
    callback.assert_not_called()


@pytest.mark.parametrize("authenticated, expected_status", [(True, 403), (False, 401)])
def test_local_pair_reports_transport_error_only_after_authentication(monkeypatch, authenticated, expected_status):
    password = "synthetic-pairing-password"
    cfg = server._default_config()
    cfg["security"]["mode"] = "normal"
    monkeypatch.setattr(server.STATE, "config", cfg)
    monkeypatch.setattr(server, "get_current_password", lambda: password)
    monkeypatch.setattr(server, "_require_login", lambda request: None)
    monkeypatch.setattr(server, "AUTOPAIR_RATE_LIMITER", server.RateLimiter(max_requests=12, window_seconds=60))
    peer = Mock()
    monkeypatch.setattr(server, "RTCPeerConnection", peer)
    payload = {
        "hash": server._server_generate_hash(password if authenticated else "incorrect-synthetic-password"),
        "offer": {"transport": "future", "endpoint_id": "synthetic-endpoint"},
    }

    response = asyncio.run(server.autopair_api(_FakeRequest(payload)))

    assert response.status_code == expected_status
    error = json.loads(response.body)["error"]
    assert ("unsupported session transport offer" if authenticated else "Authentication failed") in error
    peer.assert_not_called()
