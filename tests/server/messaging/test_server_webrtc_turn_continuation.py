# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""What a phone sees while a turn continues (owner report 2026-11-03).

Through the real engine: the announcement streams in, the server continues the
turn, and the answer takes its place in the same streaming reply before the
final message, instead of being glued onto "Let me calculate that.".
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from types import SimpleNamespace

import pytest

from tests.support.connected_device import connect_device
from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import rest_api
from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType

ANSWER = "37 × 52 − 3 ÷ 3 = 1923."


@pytest.mark.asyncio
async def test_the_answer_replaces_the_announcement_on_the_phone(monkeypatch, tmp_path):
    webrtc, channel, _, _ = connect_device(tmp_path, monkeypatch)

    async def fake_process_chat_message(chat_request, ai_agent_url=None, on_chunk=None, on_media_reply=None):
        # What process_chat_message streams when it continues a turn.
        await on_chunk({"message": {"parts": [{"text": "Let me calculate that."}]}})
        await on_chunk({"_autoyou_stream_reset": True})
        await on_chunk({"message": {"parts": [{"text": ANSWER}]}})
        return SimpleNamespace(
            response=ANSWER,
            agent_name="AutoYou",
            session_id=chat_request.session_id,
            message_id="synthetic-agent-message",
            metadata={"processing_time_ms": 0, "turn_continuations": 1},
            media_reply_attachments=[],
        )

    monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)
    message = DataChannelMessage(
        header=MessageHeader(
            message_id="synthetic-user-message",
            message_type=MessageType.CHAT,
            timestamp=0.0,
            session_id="synthetic-live",
            user_id="client",
        ),
        payload={"message": "What is 37x52-3/3", "context": [], "metadata": {}},
    )

    await webrtc._handle_chat_message(message)

    replies = [
        (sent.payload.get("message"), bool((sent.payload.get("metadata") or {}).get("is_streaming")))
        for sent in channel.sent
        if (sent.payload.get("metadata") or {}).get("original_message_id") == "synthetic-user-message"
        and not (sent.payload.get("metadata") or {}).get("is_progress")
    ]
    assert replies == [("Let me calculate that.", True), (ANSWER, True), (ANSWER, False)]
