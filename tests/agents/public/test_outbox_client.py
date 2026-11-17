# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Any agent can send the user another message through one outbox (owner request 2026-11-03)."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from types import SimpleNamespace

from autoyou_agents.admin_agent import admin_tool
from autoyou_agents.shared_tools import outbox
from shared.adk_state import AUTOYOU_CONVERSATION_SESSION_STATE_KEY, AUTOYOU_REPLY_TARGET_STATE_KEY
from shared.session_execution import SESSION_CONTROL_STATE_KEY, build_canonical_user_id

OWNER = "cloud:synthetic-device"


def _turn():
    return SimpleNamespace(state={
        SESSION_CONTROL_STATE_KEY: {"owner_key": OWNER, "canonical_session_id": "session::cloud:synthetic-device::3"},
        AUTOYOU_CONVERSATION_SESSION_STATE_KEY: "synthetic-conversation",
        AUTOYOU_REPLY_TARGET_STATE_KEY: {"transport": "webrtc", "owner_key": OWNER, "session_id": "synthetic-live"},
    })


def _outbox(monkeypatch, data):
    sent = []

    def fake_http(method, path, payload=None, port=None, host=None, timeout=15, token=None):
        sent.append({"method": method, "path": path, "payload": payload, "token": token})
        return {"status": "success", "code": 200, "data": data, "url": f"http://127.0.0.1:8001{path}"}

    monkeypatch.setattr(admin_tool, "_get_internal_ai_agent_api_token", lambda: "synthetic-internal-token")
    monkeypatch.setattr(admin_tool, "_http", fake_http)
    return sent


def test_a_follow_up_goes_to_the_outbox_addressed_to_this_conversation(monkeypatch):
    sent = _outbox(monkeypatch, {"success": True, "queued": True, "message": "Queued message for later delivery."})

    result = outbox.send_follow_up("Your report is ready.", _turn())

    assert result == {"status": "success", "delivery": "queued", "message": "Queued message for later delivery."}
    [request] = sent
    assert (request["method"], request["path"], request["token"]) == ("POST", "/api/outbox/follow-up", "synthetic-internal-token")
    assert request["payload"] == {
        "message": "Your report is ready.",
        "source": "send_follow_up",
        "owner_key": OWNER,
        "canonical_user_id": build_canonical_user_id(OWNER),
        "external_session_id": "session::cloud:synthetic-device::3",
        "conversation_session_id": "synthetic-conversation",
        "reply_target": {"transport": "webrtc", "owner_key": OWNER, "session_id": "synthetic-live"},
    }


def test_a_live_delivery_is_reported_as_delivered(monkeypatch):
    _outbox(monkeypatch, {"success": True, "queued": False, "message": "Sent message via webrtc (1 session)."})

    assert outbox.send_follow_up("Done.", _turn())["delivery"] == "delivered"


def test_a_background_job_sends_to_the_address_it_saved(monkeypatch):
    sent = _outbox(monkeypatch, {"success": True, "queued": True, "message": "Queued."})

    outbox.post_follow_up("The image is ready.", target={"owner_key": OWNER}, source="media_generation")

    assert sent[0]["payload"] == {"message": "The image is ready.", "source": "media_generation", "owner_key": OWNER}


def test_nothing_is_sent_without_a_message_or_an_address(monkeypatch):
    sent = _outbox(monkeypatch, {"success": True})

    assert outbox.send_follow_up("  ", _turn())["status"] == "error"
    assert outbox.post_follow_up("Hello", target={})["status"] == "skipped"
    assert sent == []


def test_a_refusal_is_reported(monkeypatch):
    _outbox(monkeypatch, {"success": False, "error": "Missing 'owner_key' or 'reply_target'"})

    assert outbox.send_follow_up("Hello", _turn()) == {
        "status": "error", "message": "Missing 'owner_key' or 'reply_target'",
    }
