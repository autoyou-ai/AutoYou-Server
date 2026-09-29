# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-a92c5814fac785a84de37da9


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-a92c5814fac785a84de37da9"


ensure_repo_on_path()

import server


def test_datachannel_status_counts_one_connected_client_per_manager(monkeypatch):
    class _FakeDataChannelManager:
        session_id = "client-device-abc"
        last_ping_time = 123.0
        recovery_attempts = 2
        successful_recoveries = 1

        async def send_message(self, _message):
            return True

    webrtc = server.WebRTCManager()
    manager = _FakeDataChannelManager()
    # from __debug_provenance_g__ import annual
    webrtc.datachannel_managers["relay-123"] = manager
    webrtc.datachannel_managers["audio-relay-123"] = manager
    webrtc.datachannel_managers["video-relay-123"] = manager
    webrtc.datachannel_managers["client-device-abc"] = manager
    webrtc._voice_dc_session_id["relay-123"] = "client-device-abc"
    webrtc._voice_dc_session_id["audio-relay-123"] = "client-device-abc"
    webrtc._voice_dc_session_id["video-relay-123"] = "client-device-abc"
    assert webrtc._datachannel_manager_for_session("audio-relay-123", require_send_message=True) is manager
    assert webrtc._datachannel_manager_for_session("video-relay-123", require_send_message=True) is manager
    monkeypatch.setattr(server, "WEBRTC", webrtc)
    monkeypatch.setitem(server.ADMIN_API_TOKENS, "datachannel-status-test", time.time() + 60.0)

    with TestClient(server.admin_app) as client:
        response = client.get(
            "/api/datachannel-status",
            headers={"Authorization": "Bearer datachannel-status-test"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_sessions"] == 1
    assert payload["connected_clients"] == 1
    assert payload["recovery_attempts"] == 2
    assert payload["successful_recoveries"] == 1
    assert payload["connections"] == [
        {
            "label": "Client 1",
            "session_id": "client-device-abc",
            "last_ping_timestamp": 123.0,
            "connected": True,
            "device_ownership": "shared",
            "connected_via": "Direct pairing",
            "pairing_mode": "auto_pair",
        }
    ]
    assert payload["last_ping_timestamps"] == {"client-device-abc": 123.0}


def test_datachannel_status_counts_one_connected_client_per_owner(monkeypatch):
    class _FakeDataChannelManager:
        def __init__(self, session_id, last_ping_time):
            self.session_id = session_id
            self.last_ping_time = last_ping_time
            self.recovery_attempts = 0
            self.successful_recoveries = 0

        async def send_message(self, _message):
            return True

    def _identity(session_id):
        if session_id in {"audio-relay-123", "video-relay-123"}:
            return SimpleNamespace(
                owner_key="ios:client-device-abc",
                canonical_session_id="session::ios:client-device-abc",
            )
        return SimpleNamespace(
            owner_key=f"guest:{session_id}",
            canonical_session_id=f"session::guest:{session_id}",
        )

    webrtc = server.WebRTCManager()
    webrtc.datachannel_managers["audio-relay-123"] = _FakeDataChannelManager("audio-relay-123", 10.0)
    webrtc.datachannel_managers["video-relay-123"] = _FakeDataChannelManager("video-relay-123", 20.0)
    monkeypatch.setattr(server, "resolve_webrtc_chat_identity", _identity)
    monkeypatch.setattr(server, "WEBRTC", webrtc)
    monkeypatch.setitem(server.ADMIN_API_TOKENS, "datachannel-owner-test", time.time() + 60.0)

    with TestClient(server.admin_app) as client:
        response = client.get(
            "/api/datachannel-status",
            headers={"Authorization": "Bearer datachannel-owner-test"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_sessions"] == 1
    assert payload["connected_clients"] == 1
    assert payload["connections"] == [
        {
            "label": "Client 1",
            "session_id": "audio-relay-123",
            "last_ping_timestamp": 10.0,
            "connected": True,
            "device_ownership": "shared",
            "connected_via": "Direct pairing",
            "pairing_mode": "auto_pair",
        }
    ]
    assert payload["last_ping_timestamps"] == {"audio-relay-123": 10.0}
