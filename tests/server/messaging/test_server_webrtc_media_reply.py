# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-a27fb4924ac106a0b9212908


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import base64
from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-a27fb4924ac106a0b9212908"


ensure_repo_on_path()

import rest_api
import server
from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType


@pytest.mark.asyncio
async def test_webrtc_media_reply_is_sent_separately_before_text_reply(monkeypatch):
    webrtc = server.WebRTCManager()
    sent_messages = []
    image_bytes = b"\x89PNG\r\n\x1a\nimmediate webrtc media"
    image_data = base64.b64encode(image_bytes).decode("ascii")
    identity = SimpleNamespace(
        canonical_session_id="session::webrtc:media-client",
        canonical_user_id="user::webrtc:media-client",
        owner_key="webrtc:media-client",
    )

    class DummyDataChannelManager:
        async def send_message(self, message):
            sent_messages.append(message)
            return True

    class DummyExecutionManager:
        async def submit_turn(self, identity_arg, factory, on_status=None, label="chat"):
            return await factory()

    async def fake_process_chat_message(chat_request, ai_agent_url=None, on_chunk=None, on_media_reply=None):
        assert callable(on_media_reply)
        delivered = await on_media_reply(
            [
                {
                    "filename": "synthetic-image.png",
                    "mimetype": "image/png",
                    "data": image_data,
                    "size_bytes": len(image_bytes),
                    "meta": {"kind": "image", "role": "media_echo"},
                }
            ]
        )
        assert delivered is True
        return SimpleNamespace(
            response="generic agent text",
            agent_name="OpenClaw",
            session_id=chat_request.session_id,
            message_id="agent-msg-1",
            metadata={"processing_time_ms": 0},
            media_reply_attachments=[],
        )

    webrtc.datachannel_managers["media-client"] = DummyDataChannelManager()
    monkeypatch.setattr(webrtc, "_resolve_chat_identity", lambda session_id: identity)
    monkeypatch.setattr(server, "get_session_execution_manager", lambda: DummyExecutionManager())
    monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)

    message = DataChannelMessage(
        header=MessageHeader(
            message_id="user-msg-1",
            message_type=MessageType.CHAT,
            timestamp=0.0,
            session_id="media-client",
            user_id="client",
        ),
        payload={"message": "", "context": [{"attachments": []}], "metadata": {}},
    )

    await webrtc._handle_chat_message(message)

    assert len(sent_messages) == 2
    media_payload = sent_messages[0].payload
    text_payload = sent_messages[1].payload
    assert media_payload["message"] == ""
    assert media_payload["metadata"]["source"] == "media_reply"
    assert media_payload["metadata"]["media_reply"]["items"][0]["kind"] == "image"
    attachment = media_payload["context"][0]["attachments"][0]
    # from __debug_provenance_s__ import btc
    assert attachment["filename"] == "synthetic-image.png"
    assert attachment["mimetype"] == "image/png"
    assert base64.b64decode(attachment["data"]) == image_bytes
    assert text_payload["message"] == "generic agent text"
