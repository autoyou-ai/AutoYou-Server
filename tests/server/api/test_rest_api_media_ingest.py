# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-c226f17384041caeb11e27fb


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-c226f17384041caeb11e27fb"

import base64
import json

import pytest

import rest_api
from shared.openclaw_gateway import attachment_to_path, rewrite_context_attachments_to_paths, safe_filename


class _MediaReplySessionManager:
    def __init__(self):
        self.mapping = {}
        self.sessions = {}

    def get_mapped_session_id(self, external_session_id, user_id):
        return self.mapping.get((external_session_id, user_id))

    def set_session_mapping(self, external_session_id, ai_session_id, user_id):
        self.mapping[(external_session_id, user_id)] = ai_session_id

    async def create_user_session(self, user_id, session_id, initial_state=None, external_session_id=None):
        self.sessions[(user_id, session_id)] = dict(initial_state or {})
        if external_session_id:
            self.set_session_mapping(external_session_id, session_id, user_id)
        return self.sessions[(user_id, session_id)]

    async def update_user_session(self, user_id, session_id, session_data):
        current = dict(self.sessions.get((user_id, session_id), {}))
        current.update(dict(session_data or {}))
        self.sessions[(user_id, session_id)] = current
        return current

    async def get_user_session(self, user_id, session_id):
        return dict(self.sessions.get((user_id, session_id), {}))

    async def add_session_event(self, user_id, session_id, event_type, event_data, external_session_id=None):
        return True

    def upsert_session_context_usage_snapshot(self, *args, **kwargs):
        return None


def test_explicit_attachment_ingest_target_detects_page_uploads():
    assert (
        rest_api._explicit_attachment_ingest_target(
            "upload this screenshot to the AutoYou page feed titled bug report",
            {},
        )
        == "page"
    )


def test_explicit_attachment_ingest_target_detects_notes_uploads_from_steering():
    assert (
        rest_api._explicit_attachment_ingest_target(
            "upload this attachment",
            {"steering_target": "notes_agent"},
        )
        == "notes"
    )


def test_explicit_attachment_ingest_target_uses_raw_steering_command():
    assert (
        rest_api._explicit_attachment_ingest_target(
            "go to notes agent",
            {
                "steering_target": "autoyou_notes_agent",
                "steering_command_raw": "save this voice note to notes",
            },
        )
        == "notes"
    )


def test_explicit_attachment_ingest_target_detects_append_to_recent_note():
    assert rest_api._explicit_attachment_ingest_target("Append to most recent note", {}) == "notes"


def test_explicit_attachment_ingest_summary_prefers_tool_message():
    response, metadata, routed_agent = rest_api._summarize_explicit_attachment_ingest(
        {
            "status": "success",
            "routed_to": "page",
            "message": "Saved synthetic.png to the page feed. [Open saved item](http://127.0.0.1:8067/api/blob/blob-1)",
            "items": [
                {
                    "id": 1,
                    "title": "synthetic.png",
                    "url": "blob://blob-1",
                    "view_url": "http://127.0.0.1:8067/api/blob/blob-1",
                }
            ],
            "errors": [],
            "skipped": [],
        },
        requested_target="page",
    )

    assert (
        response
        == "Saved synthetic.png to the page feed. [Open saved item](http://127.0.0.1:8067/api/blob/blob-1)"
    )
    assert metadata["view_urls"] == ["http://127.0.0.1:8067/api/blob/blob-1"]
    assert routed_agent


def test_explicit_attachment_ingest_summary_counts_note_media_once():
    response, metadata, routed_agent = rest_api._summarize_explicit_attachment_ingest(
        {
            "status": "success",
            "routed_to": "notes",
            "saved_notes": [
                {
                    "id": 1001,
                    "filename": "synthetic-voice-note.m4a",
                    "mimetype": "audio/m4a",
                    "path": "C:/tmp/synthetic-voice-note.m4a",
                }
            ],
            "created_notes": [{"note_id": 2001, "media_id": 1001}],
            "errors": [],
            "skipped": [],
        },
        requested_target="notes",
    )

    assert response == "Saved 1 note with 1 attachment to notes."
    assert metadata["success_count"] == 1
    assert metadata["saved_notes_count"] == 1
    assert metadata["created_notes_count"] == 1
    assert routed_agent


def test_explicit_attachment_ingest_summary_reports_note_append():
    response, metadata, routed_agent = rest_api._summarize_explicit_attachment_ingest(
        {
            "status": "success",
            "routed_to": "notes",
            "saved_notes": [{"id": 1001, "filename": "synthetic-voice-note.m4a"}],
            "appended_notes": [{"note_id": 21, "media_id": 1001}],
            "errors": [],
            "skipped": [],
        },
        requested_target="notes",
    )

    assert response == "Appended 1 attachment to note 21."
    assert metadata["success_count"] == 1
    assert metadata["appended_notes_count"] == 1
    assert routed_agent


@pytest.mark.asyncio
async def test_process_chat_message_short_circuits_explicit_attachment_ingest(monkeypatch):
    def fake_rewrite_context_attachments_to_paths(*args, **kwargs):
        return (
            [],
            [
                {
                    "filename": "clip.mp4",
                    "path": "C:/temp/clip.mp4",
                    "mimetype": "video/mp4",
                    "meta": {"kind": "video"},
                }
            ],
            1,
            0,
        )

    ingest_calls = {}

    def fake_run_explicit_attachment_ingest(
        attachments,
        *,
        target,
        message,
        source,
        user_id,
        session_id,
    ):
        ingest_calls["attachments"] = attachments
        ingest_calls["target"] = target
        ingest_calls["message"] = message
        ingest_calls["source"] = source
        ingest_calls["user_id"] = user_id
        ingest_calls["session_id"] = session_id
        return {
            "status": "success",
            "routed_to": "page",
            "items": [{"id": 1, "type": "video", "title": "bug report"}],
            "errors": [],
            "skipped": [],
        }

    monkeypatch.setattr(rest_api, "rewrite_context_attachments_to_paths", fake_rewrite_context_attachments_to_paths)
    monkeypatch.setattr(rest_api, "_run_explicit_attachment_ingest", fake_run_explicit_attachment_ingest)

    request = rest_api.ChatRequest(
        message="upload to autoyou page feed titled bug report",
        session_id="session-123",
        user_id="android-user",
        context=[{"attachments": [{"filename": "clip.mp4"}]}],
        metadata={},
    )

    response = await rest_api.process_chat_message(request, ai_agent_url="http://127.0.0.1:8081")

    assert response.response == "Uploaded 1 attachment to AutoYou page feed."
    assert response.agent_name
    assert response.metadata["media_ingest"]["requested_target"] == "page"
    assert response.metadata["media_ingest"]["success_count"] == 1
    assert response.metadata["media_ingest"]["view_urls"] == []
    assert ingest_calls["target"] == "page"
    assert ingest_calls["session_id"] == "session-123"


def test_attachment_to_path_infers_audio_mimetype_from_existing_mp3_path(tmp_path):
    track_path = tmp_path / "Aura.mp3"
    track_path.write_bytes(b"audio-bytes")

    converted = attachment_to_path(
        {"path": str(track_path), "filename": "Aura.mp3"},
        source="telegram",
        user_id="user::telegram:5550001001",
        session_id="session::telegram:5550001001::15",
    )

    assert converted["path"] == str(track_path)
    assert converted["filename"] == "Aura.mp3"
    assert converted["mimetype"] == "audio/mpeg"


def test_safe_filename_handles_macos_style_absolute_paths():
    assert safe_filename("/Users/synthetic/Local/voice:note.m4a", "audio/x-m4a") == "voice_note.m4a"


def test_safe_filename_handles_windows_style_absolute_paths():
    assert safe_filename(r"C:\Users\synthetic\Local\voice:note.m4a", "audio/x-m4a") == "voice_note.m4a"


def test_attachment_to_path_uses_existing_path_basename_when_filename_missing(tmp_path):
    track_path = tmp_path / "voice note.m4a"
    track_path.write_bytes(b"audio-bytes")

    converted = attachment_to_path(
        {"path": str(track_path), "mimetype": "audio/x-m4a"},
        source="desktop",
        user_id="synthetic-user",
        session_id="synthetic-session",
    )

    assert converted["path"] == str(track_path)
    assert converted["filename"] == "voice note.m4a"
    assert converted["mimetype"] == "audio/x-m4a"


def test_rewrite_context_returns_path_backed_attachments_for_ingest():
    encoded = base64.b64encode(b"synthetic audio bytes").decode("ascii")

    forward_context, attachments, path_saved_count, path_skipped_count = rewrite_context_attachments_to_paths(
        [
            {
                "source": "ios",
                "attachments": [
                    {
                        "filename": "voice-note.m4a",
                        "mimetype": "audio/x-m4a",
                        "data": encoded,
                    }
                ],
            }
        ],
        source="ios",
        user_id="user::cloud:synthetic-device",
        session_id="session::cloud:synthetic-device",
    )

    assert path_saved_count == 1
    assert path_skipped_count == 0
    assert attachments[0]["path"]
    assert "data" not in attachments[0]
    assert "data" not in forward_context[0]["attachments"][0]


@pytest.mark.asyncio
async def test_process_chat_message_voice_note_uses_agent_path_and_sanitizes_metadata(tmp_path, monkeypatch):
    from shared import voice_messaging

    saved_path = tmp_path / "saved-voice-note.ogg"
    reply_path = tmp_path / "reply-voice-note.ogg"
    calls = {}

    def fake_attachment_to_path(attachment, *, source, user_id, session_id):
        saved_path.write_bytes(base64.b64decode(attachment["data"]))
        calls["save"] = {
            "source": source,
            "user_id": user_id,
            "session_id": session_id,
        }
        return {
            "path": str(saved_path),
            "filename": attachment.get("filename"),
            "mimetype": attachment.get("mimetype"),
            "size_bytes": saved_path.stat().st_size,
            "meta": attachment.get("meta", {}),
        }

    async def fake_transcribe_attachment(attachment, *, cleanup_materialized=True, settings=None):
        calls["transcribe"] = {
            "path": attachment.get("path"),
            "cleanup_materialized": cleanup_materialized,
        }
        return "please summarize my morning"

    async def fake_send_message_to_openclaw(message, session_id, context=None, metadata=None, on_chunk=None):
        calls["agent"] = {
            "message": message,
            "context": context,
            "metadata": metadata,
        }
        return {
            "response": "Your morning is summarized.",
            "agent_name": "OpenClaw",
        }

    async def fake_build_voice_reply(reply_text):
        reply_path.write_bytes(b"OggS synthetic voice reply")
        return voice_messaging.VoiceReplyArtifacts(
            transcript="",
            reply_text=reply_text,
            wav_path=None,
            ogg_path=str(reply_path),
        )

    monkeypatch.setattr(rest_api, "_active_ai_provider", lambda: "openclaw")
    monkeypatch.setattr(rest_api, "_shared_attachment_to_path", fake_attachment_to_path)
    monkeypatch.setattr(rest_api, "send_message_to_openclaw", fake_send_message_to_openclaw)
    monkeypatch.setattr(voice_messaging, "faster_whisper_available", lambda: True)
    monkeypatch.setattr(voice_messaging, "transcribe_attachment", fake_transcribe_attachment)
    monkeypatch.setattr(voice_messaging, "build_voice_reply", fake_build_voice_reply)

    inbound_audio = base64.b64encode(b"synthetic inbound audio").decode("ascii")
    request = rest_api.ChatRequest(
        message="",
        session_id="session-voice-note",
        user_id="synthetic-voice-user",
        context=[
            {
                "attachments": [
                    {
                        "filename": "voice-note.ogg",
                        "mimetype": "audio/ogg; codecs=opus",
                        "data": inbound_audio,
                        "meta": {"kind": "voice"},
                    }
                ]
            }
        ],
        metadata={"source": "webrtc"},
    )

    response = await rest_api.process_chat_message(request, ai_agent_url="http://127.0.0.1:8081")

    assert calls["save"]["session_id"] == "session-voice-note"
    assert calls["transcribe"] == {
        "path": str(saved_path),
        "cleanup_materialized": False,
    }
    assert calls["agent"]["message"] == "please summarize my morning"
    assert calls["agent"]["context"] == []
    assert calls["agent"]["metadata"]["voice_note"]["inbound_audio_saved"] is True
    assert "saved_audio_path" not in calls["agent"]["metadata"]["voice_note"]

    assert response.response == "Your morning is summarized."
    assert response.voice_reply_audio_path == str(reply_path)
    assert response.voice_reply_transcript == "please summarize my morning"
    assert response.metadata["voice_note"]["reply_audio_attached"] is True
    assert response.metadata["voice_note"]["inbound_audio_saved"] is True
    assert "saved_audio_path" not in response.metadata["voice_note"]
    assert "reply_audio_path" not in response.metadata["voice_note"]
    payload = response.model_dump() if hasattr(response, "model_dump") else response.dict()
    assert "voice_reply_audio_path" not in payload


@pytest.mark.asyncio
async def test_process_chat_message_media_only_does_not_return_mirror_attachment(tmp_path, monkeypatch):
    image_path = tmp_path / "synthetic-image.png"
    image_bytes = b"\x89PNG\r\n\x1a\nsynthetic-image"
    image_path.write_bytes(image_bytes)
    calls = {}

    async def fake_send_message_to_openclaw(message, session_id, context=None, metadata=None, on_chunk=None):
        calls["agent"] = {
            "message": message,
            "context": context,
            "metadata": metadata,
        }
        return {
            "response": "I saved the image.",
            "agent_name": "OpenClaw",
        }

    monkeypatch.setattr(rest_api, "_active_ai_provider", lambda: "openclaw")
    monkeypatch.setattr(rest_api, "send_message_to_openclaw", fake_send_message_to_openclaw)

    request = rest_api.ChatRequest(
        message="",
        session_id="session-media-only",
        user_id="synthetic-media-user",
        context=[
            {
                "source": "android",
                "attachments": [
                    {
                        "filename": "synthetic-image.png",
                        "mimetype": "image/png",
                        "path": str(image_path),
                        "size_bytes": len(image_bytes),
                        "meta": {"kind": "image"},
                    }
                ],
            }
        ],
        metadata={"source": "webrtc"},
    )

    response = await rest_api.process_chat_message(request, ai_agent_url="http://127.0.0.1:8081")

    assert calls["agent"]["message"] == ""
    assert calls["agent"]["context"][0]["attachments"][0]["path"] == str(image_path)
    assert response.response == "I saved the image."
    assert response.media_reply_attachments == []
    assert "media_reply" not in response.metadata


@pytest.mark.asyncio
async def test_process_chat_message_media_only_callback_is_not_called(tmp_path, monkeypatch):
    video_path = tmp_path / "synthetic-video.mp4"
    video_bytes = b"\x00\x00\x00\x18ftypmp42synthetic-video"
    video_path.write_bytes(video_bytes)
    delivered = []

    async def fake_send_message_to_openclaw(message, session_id, context=None, metadata=None, on_chunk=None):
        return {
            "response": "I saved the video.",
            "agent_name": "OpenClaw",
        }

    async def on_media_reply(attachments):
        delivered.extend(attachments)
        return True

    monkeypatch.setattr(rest_api, "_active_ai_provider", lambda: "openclaw")
    monkeypatch.setattr(rest_api, "send_message_to_openclaw", fake_send_message_to_openclaw)

    request = rest_api.ChatRequest(
        message="Please review the attached file.",
        session_id="session-media-callback",
        user_id="synthetic-media-user",
        context=[
            {
                "source": "ios",
                "attachments": [
                    {
                        "filename": "synthetic-video.mp4",
                        "mimetype": "video/mp4",
                        "path": str(video_path),
                        "size_bytes": len(video_bytes),
                        "meta": {"kind": "video"},
                    }
                ],
            }
        ],
        metadata={"source": "webrtc"},
    )

    response = await rest_api.process_chat_message(
        request,
        ai_agent_url="http://127.0.0.1:8081",
        on_media_reply=on_media_reply,
    )

    assert delivered == []
    assert response.media_reply_attachments == []
    assert "media_reply" not in response.metadata


@pytest.mark.asyncio
async def test_process_chat_message_extracts_ai_generated_media_from_agent_payload(monkeypatch):
    session_manager = _MediaReplySessionManager()
    delivered = []

    async def fake_send_message_to_ai_agent(
        user_id,
        session_id,
        message,
        ai_agent_url=None,
        *,
        context=None,
        metadata=None,
        state_delta=None,
        on_chunk=None,
    ):
        del user_id, session_id, message, ai_agent_url, context, metadata, state_delta, on_chunk
        return {
            "message": {
                "parts": [
                    {
                        "text": json.dumps(
                            {
                                "response": "Generated a synthetic render.",
                                "media_reply_attachments": [
                                    {
                                        "filename": "synthetic-agent-render.png",
                                        "mimetype": "image/png",
                                        "url": "https://cdn.example.com/synthetic-agent-render.png",
                                    }
                                ],
                            }
                        )
                    }
                ]
            },
            "author": "autoyou",
            "id": "msg-generated-media-1",
            "invocationId": "invoke-generated-media-1",
            "usageMetadata": {},
        }

    async def on_media_reply(attachments):
        delivered.extend(attachments)
        return True

    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setattr(rest_api, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(rest_api, "get_session_metrics", lambda: None)
    monkeypatch.setattr(rest_api, "send_message_to_ai_agent", fake_send_message_to_ai_agent)
    monkeypatch.setattr(
        rest_api,
        "create_ai_agent_session",
        lambda user_id, ai_agent_url=None: rest_api.asyncio.sleep(0, result="internal-media-session"),
    )

    request = rest_api.ChatRequest(
        message="render a synthetic image",
        session_id="external-media-session",
        user_id="synthetic-media-user",
        context=[],
        metadata={"canonical_owner_key": "direct:synthetic-device"},
    )

    response = await rest_api.process_chat_message(
        request,
        ai_agent_url="http://127.0.0.1:8081",
        on_media_reply=on_media_reply,
    )

    assert response.response == "Generated a synthetic render."
    assert response.media_reply_attachments == []
    assert len(delivered) == 1
    assert delivered[0]["filename"] == "synthetic-agent-render.png"
    assert delivered[0]["url"] == "https://cdn.example.com/synthetic-agent-render.png"
