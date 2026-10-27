# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Assistant mode on the server's WebRTC audio path.

Background audio used to go only to the Safety Recording writer. With
Assistant mode it also reaches the wake word hub, and a device may stream just
for listening (``assistant_listening``) without anything being saved. These
tests pin who hears which frames, and that recording is untouched.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server  # noqa: E402


class FakeHub:
    def __init__(self, *, enabled=True, listen_during_safety_recording=True):
        self.enabled = enabled
        self.listen_during_safety_recording = listen_during_safety_recording
        self.fed = []
        self.wakes = []
        self.enrollments = []
        self.cancelled = []

    def listens_to(self, *, assistant_listening, silent_recording):
        if not self.enabled:
            return False
        return bool(assistant_listening or (silent_recording and self.listen_during_safety_recording))

    def feed(self, session_id, chunk):
        self.fed.append((session_id, len(chunk)))

    def client_wake(self, session_id, *, score=None):
        self.wakes.append((session_id, score))

    def start_enrollment(self, session_id, *, phrase="", count=3):
        self.enrollments.append((session_id, phrase, count))

    def cancel(self, session_id, reason="cancelled"):
        self.cancelled.append((session_id, reason))

    def end_session(self, session_id):
        pass


class FakeManager:
    def __init__(self):
        self.sent = []

    async def send_message(self, message):
        self.sent.append(getattr(message, "payload", message))
        return True


class FakeRecorder:
    def __init__(self):
        self.chunks = []

    def write(self, chunk):
        self.chunks.append(chunk)

    def close(self):
        pass


@pytest.fixture
def env(monkeypatch):
    cfg = server._default_config()
    original_config = server.STATE.config
    server.STATE.config = cfg
    monkeypatch.setattr(server, "AudioTrackSink", object)
    webrtc = server.WebRTCManager()
    hub = FakeHub()
    webrtc.assistant_mode = hub
    manager = FakeManager()
    webrtc.datachannel_managers["phone"] = manager
    recorder = FakeRecorder()
    monkeypatch.setattr(webrtc, "_get_or_create_silent_recorder", lambda session_id: recorder)
    try:
        yield SimpleNamespace(webrtc=webrtc, hub=hub, cfg=cfg, manager=manager, recorder=recorder)
    finally:
        server.STATE.config = original_config


def control(webrtc, payload, session_id="phone"):
    message = SimpleNamespace(header=SimpleNamespace(session_id=session_id), payload=payload)
    asyncio.run(webrtc._handle_voice_call_control_message(message, trusted_session_id=session_id))


def background(active=True, silent_recording=False, assistant_listening=False, platform="android"):
    return {
        "event": "background_audio_state",
        "active": active,
        "silent_recording": silent_recording,
        "assistant_listening": assistant_listening,
        "platform": platform,
    }


SDP_SENDRECV = "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n"


def test_offer_allows_listening_only_streams_when_assistant_mode_is_on(env):
    offer = {"background_audio": {"active": True, "assistant_listening": True, "platform": "ios"}}
    state = env.webrtc._background_audio_offer_state(offer, SDP_SENDRECV, cfg=env.cfg)
    assert state["active"] is True
    assert state["assistant_listening"] is True
    assert state["silent_recording"] is False
    assert state["mic_streaming"] is True
    assert state["muted"] is False
    assert state["server_audio_direction"] == "sendrecv"

    env.hub.enabled = False
    refused = env.webrtc._background_audio_offer_state(offer, SDP_SENDRECV, cfg=env.cfg)
    assert refused["active"] is False and refused["assistant_listening"] is False

    env.hub.enabled = True
    env.cfg["video_call"]["disable_autoyou_agents"] = True
    no_agents = env.webrtc._background_audio_offer_state(offer, SDP_SENDRECV, cfg=env.cfg)
    assert no_agents["assistant_listening"] is False


def test_offer_keeps_listening_but_not_recording_when_recording_is_not_allowed(env):
    offer = {"background_audio": {"active": True, "silent_recording": True, "assistant_listening": True}}
    state = env.webrtc._background_audio_offer_state(offer, SDP_SENDRECV, cfg=env.cfg)
    assert state["active"] is True
    assert state["silent_recording"] is False
    assert state["assistant_listening"] is True
    env.cfg["video_call"]["silent_recording_enabled"] = True
    both = env.webrtc._background_audio_offer_state(offer, SDP_SENDRECV, cfg=env.cfg)
    assert both["silent_recording"] is True and both["assistant_listening"] is True


def test_listening_stream_reaches_only_the_wake_word_hub(env):
    control(env.webrtc, background(assistant_listening=True))
    state = env.webrtc._background_audio_state_for_session("phone")
    assert state["active"] is True and state["assistant_listening"] is True and state["silent_recording"] is False

    audio_manager = SimpleNamespace(process_audio_chunk=lambda chunk: pytest.fail("STT must not run before the wake word"))
    env.webrtc._handle_inbound_voice_audio_chunk("phone", audio_manager, b"\x01\x00" * 320)
    assert env.hub.fed == [("phone", 640)]
    assert env.recorder.chunks == []  # nothing saved for a listening-only stream
    assert env.manager.sent[-1]["event"] == "assistant_state"
    assert env.manager.sent[-1]["reason"] == "listening_for_wake_word"


def test_safety_recording_keeps_recording_and_also_feeds_the_hub(env):
    env.cfg["video_call"]["silent_recording_enabled"] = True
    control(env.webrtc, background(silent_recording=True))
    env.webrtc._handle_inbound_voice_audio_chunk("phone", None, b"\x02\x00" * 160)
    assert env.recorder.chunks == [b"\x02\x00" * 160]
    assert env.hub.fed == [("phone", 320)]

    env.hub.listen_during_safety_recording = False
    env.webrtc._handle_inbound_voice_audio_chunk("phone", None, b"\x03\x00" * 160)
    assert len(env.recorder.chunks) == 2
    assert len(env.hub.fed) == 1


def test_disabled_assistant_mode_leaves_safety_recording_exactly_as_before(env):
    env.cfg["video_call"]["silent_recording_enabled"] = True
    env.hub.enabled = False
    control(env.webrtc, background(silent_recording=True, assistant_listening=True))
    state = env.webrtc._background_audio_state_for_session("phone")
    assert state["silent_recording"] is True and state["assistant_listening"] is False
    env.webrtc._handle_inbound_voice_audio_chunk("phone", None, b"\x02\x00" * 160)
    assert len(env.recorder.chunks) == 1
    assert env.hub.fed == []
    assert not any(item.get("event") == "assistant_state" for item in env.manager.sent)


def test_listening_without_permission_falls_back_to_closed(env):
    env.hub.enabled = False
    control(env.webrtc, background(assistant_listening=True))
    state = env.webrtc._background_audio_state_for_session("phone")
    assert state["active"] is False
    assert state["assistant_listening"] is False


def test_device_wake_and_enrollment_need_a_live_stream(env):
    control(env.webrtc, {"event": "assistant_wake", "score": 0.9})
    assert env.hub.wakes == []
    assert env.manager.sent[-1] == {**env.manager.sent[-1], "event": "assistant_state", "reason": "not_streaming"}

    control(env.webrtc, background(assistant_listening=True))
    control(env.webrtc, {"event": "assistant_wake", "score": 0.9})
    assert env.hub.wakes == [("phone", 0.9)]
    control(env.webrtc, {"event": "assistant_enroll", "action": "start", "wake_phrase": "Hey Jarvis", "count": 4})
    assert env.hub.enrollments == [("phone", "Hey Jarvis", 4)]
    control(env.webrtc, {"event": "assistant_cancel"})
    assert ("phone", "cancelled") in env.hub.cancelled


def test_stopping_the_stream_cancels_any_open_listening_window(env):
    control(env.webrtc, background(assistant_listening=True))
    control(env.webrtc, background(active=False))
    assert ("phone", "stopped_listening") in env.hub.cancelled


def test_an_active_voice_call_owns_the_microphone(env, monkeypatch):
    monkeypatch.setattr(env.webrtc, "_voice_call_client_active_for_session", lambda session_id: True)
    control(env.webrtc, background(assistant_listening=True))
    state = env.webrtc._background_audio_state_for_session("phone")
    assert state["active"] is False and state["assistant_listening"] is False


def test_capabilities_advertise_assistant_mode(tmp_path, monkeypatch):
    from shared import assistant_mode as am

    monkeypatch.setenv(am.ASSISTANT_MODE_DIR_ENV, str(tmp_path / "assistant_mode"))
    monkeypatch.setattr(server, "AudioTrackSink", object)
    cfg = server._default_config()
    capability = server._build_webrtc_capabilities(cfg=cfg)["assistant_mode"]
    assert capability["enabled"] is False
    assert capability["available"] is True
    am.save_settings({"enabled": True, "wake_phrase": "Hey Jarvis", "confirm": "text"})
    capability = server._build_webrtc_capabilities(cfg=cfg)["assistant_mode"]
    assert capability["enabled"] is True
    assert capability["ready"] is True
    assert capability["wake_phrase"] == "Hey Jarvis"
    assert capability["stream_listening"] is True and capability["client_wake"] is True
    cfg["video_call"]["disable_autoyou_agents"] = True
    assert server._build_webrtc_capabilities(cfg=cfg)["assistant_mode"]["enabled"] is False


def test_calls_keep_normal_transcription_unless_the_owner_gates_them(env, monkeypatch):
    fed_to_stt = []
    audio_manager = SimpleNamespace(process_audio_chunk=lambda chunk: fed_to_stt.append(len(chunk)))
    monkeypatch.setattr(env.webrtc, "_should_accept_voice_call_audio", lambda session_id: True)
    monkeypatch.setattr(env.webrtc, "_write_audio_only_call_chunk", lambda session_id, chunk: True)

    env.webrtc._handle_inbound_voice_audio_chunk("phone", audio_manager, b"\x01\x00" * 160)
    assert fed_to_stt == [320] and env.hub.fed == []

    env.hub.gates_calls = True
    env.webrtc._handle_inbound_voice_audio_chunk("phone", audio_manager, b"\x01\x00" * 160)
    assert fed_to_stt == [320]
    assert env.hub.fed == [("phone", 320)]


def test_wake_turns_reach_the_agent_marked_with_the_live_frame(env, monkeypatch):
    """The words after the wake word keep their Assistant mode marker all the
    way to the chat request, and a video call's newest frame rides along."""
    import shared.assistant_mode as assistant_mode

    processed = []

    async def fake_deliver(session_id, message, *, label=""):
        return "sent"

    async def fake_process(session_id, text, **kwargs):
        processed.append((session_id, text, kwargs.get("metadata_extra")))

    monkeypatch.setattr(env.webrtc, "_deliver_voice_chat_message", fake_deliver)
    monkeypatch.setattr(env.webrtc, "_process_voice_command", fake_process)
    monkeypatch.setattr(server, "_get_video_call_agent_processing_enabled", lambda cfg=None: True, raising=False)

    async def run():
        await env.webrtc._enqueue_voice_command(
            "phone", "open example.com and read the title", metadata_extra={"assistant_mode": True, "wake": "device"}
        )
        await env.webrtc.voice_command_queues["phone"].join()

    asyncio.run(run())
    assert processed == [("phone", "open example.com and read the title", {"assistant_mode": True, "wake": "device"})]

    frame = {"filename": "assistant-live-camera.jpg", "mimetype": "image/jpeg", "data": "c3ludGhldGlj"}
    seen_sessions = []
    monkeypatch.setattr(assistant_mode, "load_settings", lambda: {"use_live_video": True})
    monkeypatch.setattr(assistant_mode, "live_frame_attachment", lambda ids: seen_sessions.append(ids) or frame)
    metadata, context = env.webrtc._assistant_turn_request(
        "what am I holding", {"assistant_mode": True, "wake": "voice"}, {"source": "voice_call"}, ["phone", "phone-uuid"]
    )
    assert metadata["source"] == "voice_call" and metadata["assistant_mode"] is True
    assert metadata["assistant_turn"]["text"] == "what am I holding"
    assert metadata["assistant_turn"]["live_frame"] is True and metadata["assistant_turn"]["wake"] == "voice"
    assert context == [{"source": "assistant_live_frame", "attachments": [frame]}]
    assert seen_sessions == [["phone", "phone-uuid"]]

    # "Let it see the camera" off: the turn is still marked, no picture.
    monkeypatch.setattr(assistant_mode, "load_settings", lambda: {"use_live_video": False})
    metadata, context = env.webrtc._assistant_turn_request("what am I holding", {"assistant_mode": True}, {}, ["phone"])
    assert context == [] and metadata["assistant_turn"]["live_frame"] is False


def test_ordinary_voice_turns_carry_no_assistant_marker(env, monkeypatch):
    processed = []

    async def fake_deliver(session_id, message, *, label=""):
        return "sent"

    async def fake_process(session_id, text, **kwargs):
        processed.append(kwargs.get("metadata_extra"))

    monkeypatch.setattr(env.webrtc, "_deliver_voice_chat_message", fake_deliver)
    monkeypatch.setattr(env.webrtc, "_process_voice_command", fake_process)
    monkeypatch.setattr(server, "_get_video_call_agent_processing_enabled", lambda cfg=None: True, raising=False)

    async def run():
        await env.webrtc._enqueue_voice_command("phone", "hello there")
        await env.webrtc.voice_command_queues["phone"].join()

    asyncio.run(run())
    assert processed == [None]
