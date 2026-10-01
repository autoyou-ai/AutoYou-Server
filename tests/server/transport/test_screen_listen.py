import asyncio
import queue
import struct
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import server
from shared import video_call_manager
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


def test_listen_output_mixes_all_and_filters_selected_without_hardware(monkeypatch):
    output = queue.Queue()
    entered, release = threading.Event(), threading.Event()

    class Stream:
        def write(self, frame):
            entered.set()
            release.wait(timeout=2)
            output.put(frame)
            time.sleep(0.02)

        def stop_stream(self):
            pass

        def close(self):
            pass

    class Device:
        def open(self, **kwargs):
            assert kwargs["output"] and kwargs["rate"] == 16000
            return Stream()

        def terminate(self):
            pass

    module = SimpleNamespace(paInt16=8, PyAudio=Device)
    monkeypatch.setattr(video_call_manager, "_load_pyaudio_module", lambda: module)
    monkeypatch.setattr(video_call_manager, "_PYAUDIO_RUNTIME", video_call_manager._PyAudioRuntime())
    mixer = ScreenListenMixer()

    def next_sample(expected):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            sample = struct.unpack("<h", output.get(timeout=0.2)[:2])[0]
            if sample == expected:
                return
        pytest.fail(f"Listen output never contained {expected}")

    try:
        mixer.configure("all")
        assert entered.wait(timeout=1)
        mixer.feed("synthetic-a", struct.pack("<h", 1000) * 320)
        mixer.feed("synthetic-b", struct.pack("<h", 2000) * 320)
        release.set()
        next_sample(3000)
        mixer.configure("selected", {"synthetic-a"})
        mixer.feed("synthetic-b", struct.pack("<h", 2000) * 320)
        mixer.feed("synthetic-a", struct.pack("<h", 1000) * 320)
        next_sample(1000)
    finally:
        release.set()
        mixer.close()


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
    message.payload = {"event": "call_state", "active": False, "platform": "ios"}
    asyncio.run(manager._handle_voice_call_control_message(message, trusted_session_id=session_id))
    assert manager.screen_listen_snapshot()["participants"] == []
    manager._handle_inbound_voice_audio_chunk(transport_id, ai, b"\3\0")
    assert len(heard) == 1
