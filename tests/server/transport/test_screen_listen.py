import asyncio
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import server
from shared.screen_listen import ScreenListenMixer, mix_pcm


def test_local_screen_audio_mix_clips_without_wraparound():
    loud = struct.pack("<h", 30000) * 320
    assert struct.unpack("<h", mix_pcm([loud, loud])[:2])[0] == 32767
    opposite = struct.pack("<h", -30000) * 320
    assert struct.unpack("<h", mix_pcm([loud, loud, opposite])[:2])[0] == 30000


def test_listen_output_failure_stays_off(monkeypatch):
    mixer = ScreenListenMixer()
    def fail():
        raise OSError("synthetic output failure")
    monkeypatch.setattr(mixer, "_start", fail)
    with pytest.raises(OSError):
        mixer.configure("all")
    assert mixer.mode == "off"


def test_screen_audio_and_choices_never_enter_ai(monkeypatch):
    manager = server.WebRTCManager()
    session_id = "synthetic-screen-session"
    transport_id = "synthetic-transport-session"
    manager._voice_dc_session_id[transport_id] = session_id
    monkeypatch.setattr(manager, "client_display_name_snapshot", lambda sid: {"client_display_name": "Student"})
    manager._set_voice_call_client_active(session_id, True)
    manager._set_screen_session(session_id, "interactive")
    heard = []
    monkeypatch.setattr(manager.screen_listen_mixer, "feed", lambda sid, chunk: heard.append((sid, chunk)))
    ai = SimpleNamespace(process_audio_chunk=lambda chunk: (_ for _ in ()).throw(AssertionError("AI heard screen audio")))
    manager._handle_inbound_voice_audio_chunk(transport_id, ai, b"\0\0")
    assert heard == []
    manager.screen_sessions[session_id]["muted"] = False
    manager._handle_inbound_voice_audio_chunk(transport_id, ai, b"\1\0")
    assert heard == [(session_id, b"\1\0")]
    message = SimpleNamespace(header=SimpleNamespace(session_id=session_id), payload={
        "event": "screen_input", "kind": "choice", "value": "D", "phase": "press",
    })
    asyncio.run(manager._handle_voice_call_control_message(message, trusted_session_id=session_id))
    assert manager.screen_listen_snapshot()["inputs"] == []
    message.payload = {"event": "screen_input", "kind": "layout", "value": "choices", "phase": "set"}
    asyncio.run(manager._handle_voice_call_control_message(message, trusted_session_id=session_id))
    message.payload = {"event": "screen_input", "kind": "choice", "value": "D", "phase": "press"}
    asyncio.run(manager._handle_voice_call_control_message(message, trusted_session_id=session_id))
    assert manager.screen_listen_snapshot()["inputs"][-1]["value"] == "D"
    assert manager.screen_listen_snapshot()["participants"][0]["layout"] == "choices"
    desktop_control = AsyncMock(side_effect=AssertionError("Screen session controlled the desktop"))
    monkeypatch.setattr(manager, "_handle_remote_desktop_control", desktop_control)
    message.payload = {"event": "remote_desktop_control", "action": "start"}
    asyncio.run(manager._handle_voice_call_control_message(message, trusted_session_id=session_id))
    desktop_control.assert_not_awaited()
    manager._set_screen_session(session_id, "watch")
    manager._handle_inbound_voice_audio_chunk(transport_id, ai, b"\2\0")
    assert len(heard) == 1
