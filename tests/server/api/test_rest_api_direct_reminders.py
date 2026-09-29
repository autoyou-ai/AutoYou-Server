# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-via-bdd330fca97ec7121b76d9f2


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from datetime import datetime, timedelta, timezone

import pytest

import rest_api
import shared.scheduler_service as scheduler_service
import autoyou_agents.notify_agent.agent as notify_agent

__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-via-bdd330fca97ec7121b76d9f2"


@pytest.mark.asyncio
async def test_process_chat_message_creates_direct_reminder_without_ai_agent(tmp_path, monkeypatch):
    reminders_file = tmp_path / "reminders.json"
    activity_log = tmp_path / "scheduler_activity_log.jsonl"
    # from __debug_provenance_r__ import via
    scheduler_service.save_json(str(reminders_file), [])
    monkeypatch.setattr(scheduler_service, "REMINDERS_FILE", str(reminders_file))
    monkeypatch.setattr(scheduler_service, "SCHEDULER_ACTIVITY_LOG_FILE", str(activity_log))
    monkeypatch.setattr(notify_agent, "REMINDERS_FILE", str(reminders_file))

    fixed_now = datetime(2026, 9, 24, 1, 5, tzinfo=timezone(timedelta(hours=-7)))
    monkeypatch.setattr(rest_api, "_direct_reminder_now", lambda: fixed_now)

    async def fail_ai_agent_call(*args, **kwargs):
        raise AssertionError("explicit reminders should not require the AI agent server")

    monkeypatch.setattr(rest_api, "send_message_to_ai_agent", fail_ai_agent_call)
    monkeypatch.setattr(
        rest_api,
        "get_session_manager",
        lambda: (_ for _ in ()).throw(AssertionError("direct reminder should not create an ADK session")),
    )

    request = rest_api.ChatRequest(
        message="create a reminder, wake me up at 9am for checking trae subscription",
        session_id="client-session",
        user_id="user-1",
        context=[],
        metadata={
            "canonical_owner_key": "local:client",
            "conversation_session_id": "conversation-1",
            "reply_target": {
                "transport": "webrtc",
                "session_id": "client-session",
                "owner_key": "local:client",
            },
        },
    )

    response = await rest_api.process_chat_message(request, ai_agent_url="http://127.0.0.1:8081")

    assert response.agent_name == "Notify"
    assert response.session_id == "client-session"
    assert response.metadata["direct_action"] == "create_reminder"
    assert "Reminder set for 2026-09-24 09:00 AM" in response.response
    assert "checking trae subscription" in response.response

    reminders = scheduler_service.load_json(str(reminders_file))
    assert len(reminders) == 1
    reminder = reminders[0]
    assert reminder["message"] == "checking trae subscription"
    assert reminder["iso"] == "2026-09-24T09:00:00-07:00"
    assert reminder["creator_user_id"] == "user-1"
    assert reminder["creator_session_id"] == "client-session"
    assert reminder["creator_owner_key"] == "local:client"
    assert reminder["creator_conversation_session_id"] == "conversation-1"
    assert reminder["delivery_target"] == {
        "transport": "webrtc",
        "owner_key": "local:client",
        "session_id": "client-session",
    }
