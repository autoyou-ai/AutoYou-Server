# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The server's outbox delivers an agent's follow-up through the scheduler queue."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server
from shared import scheduler_service

HEADERS = {
    "Authorization": "Bearer synthetic-internal-token",
    "Origin": "http://testserver",
    "Referer": "http://testserver/admin",
}


def _queue(monkeypatch, result):
    queued = []

    async def fake_queue(message, **kwargs):
        queued.append({"message": message, **kwargs})
        return result

    monkeypatch.setattr(scheduler_service, "_queue_notification_for_delivery", fake_queue)
    return queued


def test_an_agent_follow_up_is_queued_for_its_conversation(monkeypatch):
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)
    queued = _queue(monkeypatch, {"status": "success", "queued": True, "message": "Queued message for later delivery."})

    with TestClient(server.admin_app) as client:
        response = client.post("/api/outbox/follow-up", headers=HEADERS, json={
            "message": "Your report is ready.",
            "owner_key": "cloud:synthetic-device",
            "canonical_user_id": "user::cloud:synthetic-device",
            "conversation_session_id": "synthetic-conversation",
            "reply_target": {"transport": "webrtc", "owner_key": "cloud:synthetic-device"},
            "source": "send_follow_up",
        })

    assert response.status_code == 200
    assert response.json() == {"success": True, "queued": True, "message": "Queued message for later delivery."}
    assert queued == [{
        "message": "Your report is ready.",
        "owner_key": "cloud:synthetic-device",
        "canonical_user_id": "user::cloud:synthetic-device",
        "external_session_id": None,
        "conversation_session_id": "synthetic-conversation",
        "reply_target": {"transport": "webrtc", "owner_key": "cloud:synthetic-device"},
        "source": "send_follow_up",
    }]


def test_a_follow_up_to_a_connected_device_is_delivered_now(monkeypatch):
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)
    _queue(monkeypatch, {"status": "success", "message": "Sent message via webrtc (1 session)."})

    with TestClient(server.admin_app) as client:
        response = client.post("/api/outbox/follow-up", headers=HEADERS,
                               json={"message": "Done.", "owner_key": "cloud:synthetic-device"})

    assert response.json() == {"success": True, "queued": False, "message": "Sent message via webrtc (1 session)."}


def test_the_outbox_needs_a_message_and_an_address(monkeypatch):
    monkeypatch.setattr(server, "_request_uses_ai_agent_internal_token", lambda request: True)
    queued = _queue(monkeypatch, {"status": "success"})

    with TestClient(server.admin_app) as client:
        no_message = client.post("/api/outbox/follow-up", headers=HEADERS, json={"owner_key": "cloud:synthetic-device"})
        no_address = client.post("/api/outbox/follow-up", headers=HEADERS, json={"message": "Hello"})

    assert (no_message.status_code, no_address.status_code) == (400, 400)
    assert queued == []


def test_the_outbox_refuses_callers_without_a_login_or_the_internal_token(monkeypatch):
    queued = _queue(monkeypatch, {"status": "success"})

    with TestClient(server.admin_app) as client:
        response = client.post("/api/outbox/follow-up", headers={"Origin": "http://testserver"},
                               json={"message": "Hello", "owner_key": "cloud:synthetic-device"})

    assert response.status_code == 401
    assert queued == []
