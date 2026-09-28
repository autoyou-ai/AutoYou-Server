# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-5497d2e450cfc0479682a2be


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-5497d2e450cfc0479682a2be"

import asyncio
import base64
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()


@pytest.mark.asyncio
async def test_server_webrtc_voice_reply_audio_attachment(tmp_path, monkeypatch):
    pytest.importorskip("aiortc")
    from aiortc import RTCPeerConnection, RTCSessionDescription

    import rest_api
    import server
    from shared.datachannel_manager import DataChannelManager, MessageType, create_chat_message

    audio_path = tmp_path / "voice-reply.ogg"
    audio_bytes = b"OggS main server synthetic voice reply"
    audio_path.write_bytes(audio_bytes)

    async def fake_process_chat_message(chat_req, ai_agent_url=None, on_chunk=None, on_media_reply=None):
        assert chat_req.context
        return SimpleNamespace(
            response="Synthetic main server voice reply.",
            session_id=chat_req.session_id,
            message_id="synthetic-message-id",
            agent_name="root_agent",
            metadata={
                "voice_note": {
                    "reply_transcript": "Synthetic main server voice reply.",
                    "saved_audio_path": str(tmp_path / "private-inbound.ogg"),
                    "reply_audio_path": str(audio_path),
                }
            },
            voice_reply_audio_path=str(audio_path),
        )

    monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)
    monkeypatch.setattr(server, "AudioManager", None)
    monkeypatch.setattr(server, "AudioTrackSink", None)
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr(server.WEBRTC, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)

    state_snapshot = {
        "config": server.STATE.config,
        "server_password": server.STATE.server_password,
        "otp_cache": dict(server.STATE.otp_cache),
        "session_cache": dict(server.STATE.session_cache),
    }
    server.STATE.config = {
        "server": {"name": "AutoYou Test Server"},
        "tunnelmole": {"otp_multiuse": True, "otp_timeout_minutes": 5},
        "speech": {"tts": {"provider": "off"}, "stt": {"enabled": False}},
    }
    server.STATE.server_password = "1234"
    server.STATE.otp_cache = {}
    server.STATE.session_cache = {}

    client_pc = RTCPeerConnection()
    client_dc = client_pc.createDataChannel("autoyou-chat")
    client_mgr = DataChannelManager(role="client")
    received = []
    open_event = asyncio.Event()
    chat_event = asyncio.Event()
    session_id = ""

    @client_dc.on("open")
    def on_open():
        client_mgr.set_datachannel(client_dc)
        open_event.set()

    async def on_chat(msg):
        received.append(msg)
        if msg.payload.get("message") == "Synthetic main server voice reply.":
            chat_event.set()

    async def ignore(_msg):
        return None

    client_mgr.register_handler(MessageType.CHAT, on_chat)
    client_mgr.register_handler(MessageType.VOICE_CALL_CONTROL, ignore)
    client_mgr.register_handler(MessageType.ERROR, ignore)

    @client_dc.on("message")
    async def on_message(data):
        raw = data if isinstance(data, str) else data.decode("utf-8", errors="replace")
        await client_mgr.handle_received_message(raw)

    try:
        otp = server.generate_otp_hash_and_cache()
        assert otp
        auth_hash = server._server_generate_hash(f"{otp}:1234")
        auth_body = await asyncio.wait_for(
            server.handle_auth_request(
                {"hash": auth_hash, "client_id": "synthetic-main-webrtc-client"}
            ),
            timeout=10,
        )
        assert auth_body.get("success"), auth_body
        session_id = auth_body["session_id"]
        client_mgr.set_session_id(session_id)

        offer = await asyncio.wait_for(client_pc.createOffer(), timeout=10)
        await asyncio.wait_for(client_pc.setLocalDescription(offer), timeout=10)
        deadline = asyncio.get_event_loop().time() + 5
        while client_pc.iceGatheringState != "complete" and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)

        answer_body = await asyncio.wait_for(
            server.WEBRTC.handle_session_offer(
                session_id,
                {"type": "offer", "sdp": client_pc.localDescription.sdp, "iceServers": []},
            ),
            timeout=20,
        )
        await asyncio.wait_for(
            client_pc.setRemoteDescription(
                RTCSessionDescription(sdp=answer_body["sdp"], type=answer_body["type"])
            ),
            timeout=10,
        )
        await asyncio.wait_for(open_event.wait(), timeout=20)

        inbound_audio = base64.b64encode(b"synthetic inbound audio").decode("ascii")
        message = create_chat_message(
            "",
            session_id=session_id,
            user_id="synthetic-client",
            context=[
                {
                    "attachments": [
                        {
                            "filename": "voice-note.ogg",
                            "mimetype": "audio/ogg; codecs=opus",
                            "data": inbound_audio,
                            "size_bytes": len(b"synthetic inbound audio"),
                            "meta": {"kind": "voice", "platform": "webrtc-test"},
                        }
                    ]
                }
            ],
            metadata={"source": "chat"},
        )
        assert await asyncio.wait_for(client_mgr.send_message(message), timeout=10)
        await asyncio.wait_for(chat_event.wait(), timeout=25)

        payload = received[-1].payload
        attachments = [
            attachment
            for item in payload.get("context", [])
            for attachment in item.get("attachments", [])
        ]
        assert payload.get("message") == "Synthetic main server voice reply."
        assert attachments
        attachment = attachments[0]
        assert attachment["mimetype"] == "audio/ogg; codecs=opus"
        assert base64.b64decode(attachment["data"]) == audio_bytes
        assert payload["metadata"]["voice_note"]["reply_audio_attached"] is True
        assert "saved_audio_path" not in payload["metadata"]["voice_note"]
        assert "reply_audio_path" not in payload["metadata"]["voice_note"]
    finally:
        with suppress(Exception):
            client_mgr.disconnect()
        if session_id:
            with suppress(Exception):
                await asyncio.wait_for(server.WEBRTC.async_cleanup_session(session_id), timeout=5)
        with suppress(Exception):
            await asyncio.wait_for(client_pc.close(), timeout=5)
        server.STATE.config = state_snapshot["config"]
        server.STATE.server_password = state_snapshot["server_password"]
        server.STATE.otp_cache = state_snapshot["otp_cache"]
        server.STATE.session_cache = state_snapshot["session_cache"]
