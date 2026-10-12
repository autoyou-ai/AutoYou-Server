# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-b245e0558037b0d56f6c538b


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import os
import subprocess
import sys
import queue
import threading
import types
import wave

from tests.support.paths import ensure_repo_on_path

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-b245e0558037b0d56f6c538b"


ensure_repo_on_path()

from shared.audio_manager import (
    AudioManager,
    TTSAudioStreamTrack,
    _sanitize_text_for_tts,
    _safe_realtimestt_transcription_worker,
)
import shared.audio_manager as audio_manager_module


class _FakeStream:
    def __init__(self, is_tty):
        self._is_tty = is_tty

    def isatty(self):
        return self._is_tty


def test_safe_realtimestt_transcription_worker_exits_on_broken_pipe(monkeypatch):
    class DummyConn:
        def poll(self, timeout):
            raise BrokenPipeError(109, "The pipe has been ended")

    class DummyWorker:
        def __init__(self, *args, **kwargs):
            self.shutdown_event = threading.Event()
            self.conn = DummyConn()
            self.queue = queue.Queue()
            self.polling_thread = None

        def run(self):
            self.polling_thread = threading.Thread(target=self.poll_connection, daemon=True)
            self.polling_thread.start()
            self.polling_thread.join(timeout=1.0)
            assert not self.polling_thread.is_alive()
            assert self.shutdown_event.is_set()

    fake_module = types.SimpleNamespace(
        TranscriptionWorker=DummyWorker,
        TIME_SLEEP=0.0,
    )

    monkeypatch.setattr(audio_manager_module, "_realtimestt_audio_recorder", fake_module)

    _safe_realtimestt_transcription_worker()


def test_safe_realtimestt_worker_finds_realtimestt_1_core_worker(monkeypatch):
    class DummyConn:
        def poll(self, timeout):
            raise EOFError()

    class DummyWorker:
        def __init__(self, *args, **kwargs):
            self.shutdown_event = threading.Event()
            self.conn = DummyConn()
            self.queue = queue.Queue()

        def run(self):
            self.poll_connection()
            assert self.shutdown_event.is_set()

    fake_transcription_module = types.ModuleType("RealtimeSTT.core.transcription")
    fake_transcription_module.TranscriptionWorker = DummyWorker
    fake_transcription_module.TIME_SLEEP = 0.0

    monkeypatch.setattr(audio_manager_module, "_realtimestt_audio_recorder", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "RealtimeSTT.core.transcription", fake_transcription_module)

    _safe_realtimestt_transcription_worker()


def test_realtimestt_1_worker_starter_is_patched_during_recorder_construction(monkeypatch):
    fake_initialization = types.ModuleType("RealtimeSTT.core.initialization")
    calls = []

    def original_start_worker(target=None, args=()):
        calls.append(("original", target, args))
        return "original-handle"

    def run_transcription_worker():
        return None

    run_transcription_worker.__module__ = "RealtimeSTT.core.transcription"
    fake_initialization.start_recorder_worker = original_start_worker

    monkeypatch.setitem(sys.modules, "RealtimeSTT.core.initialization", fake_initialization)
    monkeypatch.setattr(audio_manager_module, "_should_use_threaded_realtimestt_worker", lambda: True)
    monkeypatch.setattr(audio_manager_module, "_guard_child_sys_path", lambda: calls.append(("guard",)))

    def fake_thread(target, args=(), *, name):
        calls.append(("thread", target, args, name))
        return "thread-handle"

    monkeypatch.setattr(audio_manager_module, "_start_process_like_thread", fake_thread)

    with audio_manager_module._patch_realtimestt_worker_starter():
        handle = fake_initialization.start_recorder_worker(
            target=run_transcription_worker,
            args=("chunk",),
        )

    assert handle == "thread-handle"
    assert calls == [
        (
            "thread",
            audio_manager_module._safe_realtimestt_transcription_worker,
            ("chunk",),
            "RealtimeSTT-TranscriptionWorker",
        )
    ]
    assert fake_initialization.start_recorder_worker is original_start_worker


def test_thread_backed_process_handle_supports_process_like_shutdown():
    finished = threading.Event()

    def worker():
        finished.set()

    handle = audio_manager_module._start_process_like_thread(
        worker,
        name="test-realtimestt-thread",
    )

    handle.join(timeout=1.0)

    assert finished.is_set()
    assert handle.is_alive() is False
    assert handle.terminate() is None


def test_threaded_realtimestt_worker_selected_wherever_children_are_not_forked(monkeypatch):
    """Spawn/forkserver children re-run interpreter bootstrap and can die
    importing numpy/cv2, so only fork-based platforms keep the real process."""
    monkeypatch.delenv("AUTOYOU_REALTIMESTT_THREAD_WORKER", raising=False)
    monkeypatch.setattr(audio_manager_module.sys, "stdout", _FakeStream(True))
    monkeypatch.setattr(audio_manager_module.sys, "stderr", _FakeStream(True))

    # Windows: threaded regardless of compiled-vs-source or console-vs-pipe.
    monkeypatch.setattr(audio_manager_module.sys, "platform", "win32")
    monkeypatch.setattr("shared.platform_runtime.is_compiled", lambda: True)
    assert audio_manager_module._should_use_threaded_realtimestt_worker() is True

    monkeypatch.setattr("shared.platform_runtime.is_compiled", lambda: False)
    assert audio_manager_module._should_use_threaded_realtimestt_worker() is True

    # POSIX non-macOS keys off the start method, not the platform name.
    monkeypatch.setattr(audio_manager_module.sys, "platform", "linux")
    monkeypatch.setattr(audio_manager_module, "_multiprocessing_default_start_method", lambda: "fork")
    assert audio_manager_module._should_use_threaded_realtimestt_worker() is False

    monkeypatch.setattr(audio_manager_module, "_multiprocessing_default_start_method", lambda: "forkserver")
    assert audio_manager_module._should_use_threaded_realtimestt_worker() is True

    # Undetectable start method must fail safe to the in-process worker.
    monkeypatch.setattr(audio_manager_module, "_multiprocessing_default_start_method", lambda: "")
    assert audio_manager_module._should_use_threaded_realtimestt_worker() is True

    monkeypatch.setattr(audio_manager_module.sys, "platform", "darwin")
    assert audio_manager_module._should_use_threaded_realtimestt_worker() is True


def test_realtimestt_thread_worker_override_is_honoured_off_macos(monkeypatch):
    monkeypatch.setattr(audio_manager_module.sys, "platform", "win32")
    monkeypatch.setattr("shared.platform_runtime.is_compiled", lambda: False)
    monkeypatch.setenv("AUTOYOU_REALTIMESTT_THREAD_WORKER", "0")

    assert audio_manager_module._should_use_threaded_realtimestt_worker() is False

    # macOS deliberately ignores the override: the process worker has no
    # working configuration there.
    monkeypatch.setattr(audio_manager_module.sys, "platform", "darwin")
    assert audio_manager_module._should_use_threaded_realtimestt_worker() is True


def test_instantiate_realtimestt_recorder_falls_back_when_silero_hub_load_is_unavailable(monkeypatch):
    class FakeHub:
        def load(self, *args, **kwargs):
            raise RuntimeError("snakers4/silero-vad download unavailable")

    fake_torch = types.SimpleNamespace(hub=FakeHub())
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    class FakeRecorder:
        def __init__(self, **kwargs):
            self.kwargs = dict(kwargs)
            self.silero_vad_model, _ = fake_torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                verbose=False,
                onnx=False,
            )

    recorder, used_fallback = audio_manager_module._instantiate_realtimestt_recorder(
        FakeRecorder,
        {"demo": True},
    )

    assert used_fallback is True
    assert isinstance(recorder.silero_vad_model, audio_manager_module._WebRTCSileroVADShim)
    assert recorder.silero_vad_model([], 16000).item() == 0.0


def test_audio_manager_init_stt_stays_ready_when_local_silero_fallback_is_used(monkeypatch):
    updates = []
    manager = AudioManager.__new__(AudioManager)
    manager._closed = False
    manager._stt_generation = 1
    manager.recorder = None
    manager.input_queue = queue.Queue()
    manager._stt_initialization_event = threading.Event()
    manager._warmup_audio_dropped_chunks = 0
    manager.status_callback = updates.append
    manager._last_status_signature = None
    manager.on_text_callback = lambda text: None
    manager._transcription_thread = None
    manager.stop_speaking = lambda source="unknown": None
    manager._segmentation_hold = False
    manager._pre_hold_silence_duration = None
    manager._held_recording_started_at = None

    class DummyRecorder:
        def text(self):
            return ""

    class DummyBaseRecorder:
        pass

    class DummyThread:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def start(self):
            return None

    monkeypatch.setattr(audio_manager_module, "AudioToTextRecorder", DummyBaseRecorder)
    monkeypatch.setattr(audio_manager_module, "_resolve_stt_device", lambda value: "cpu")
    monkeypatch.setattr(audio_manager_module, "_patch_tqdm_ensure_lock", lambda: None)
    monkeypatch.setattr(audio_manager_module, "_ensure_torch_hub_repo_trusted", lambda repo_name: None)
    monkeypatch.setattr(
        audio_manager_module,
        "_instantiate_realtimestt_recorder",
        lambda recorder_cls, recorder_kwargs: (DummyRecorder(), True),
    )
    monkeypatch.setattr(audio_manager_module.threading, "Thread", DummyThread)

    manager._init_stt(
        1,
        {
            "stt": {
                "model": "tiny.en",
                "language": "en",
                "device": "cpu",
                "compute_type": "float32",
                "silero_sensitivity": 0.4,
                "post_speech_silence_duration": 0.6,
            }
        },
    )

    assert manager.recorder is not None
    assert manager._stt_initialization_event.is_set()
    assert [item["state"] for item in updates] == ["ready"]


def test_audio_manager_close_signals_recorder_and_clears_reference():
    manager = AudioManager.__new__(AudioManager)
    manager.input_queue = queue.Queue()
    manager._closed = False
    manager._stt_generation = 4
    manager._tts_generation = 2
    manager._tts_generation_lock = threading.Lock()
    manager._stop_tts_event = threading.Event()
    manager._stt_initialization_event = threading.Event()
    manager._feed_audio_thread = None
    manager._init_thread = None
    # from __debug_provenance_h__ import revenue
    manager._transcription_thread = None

    class DummyTrack:
        def __init__(self):
            self.stop_calls = 0

        def stop_playback(self, reason="Playback stopped."):
            self.stop_calls += 1

    tts_track = DummyTrack()
    playback_track = DummyTrack()
    manager.tts_track = tts_track
    manager.playback_track = playback_track

    class DummyRecorder:
        def __init__(self):
            self.shutdown_event = threading.Event()
            self.interrupt_stop_event = threading.Event()
            self.start_recording_event = threading.Event()
            self.stop_recording_event = threading.Event()
            self.is_running = True
            self.is_recording = True
            self.stop_calls = 0
            self.shutdown_calls = 0

        def stop(self):
            self.stop_calls += 1

        def shutdown(self):
            self.shutdown_calls += 1

    recorder = DummyRecorder()
    manager.recorder = recorder

    manager.close()

    assert manager._closed is True
    assert manager._stt_generation == 5
    assert manager._tts_generation == 3
    assert manager.recorder is None
    assert manager._stop_tts_event.is_set()
    assert manager._stt_initialization_event.is_set()
    assert recorder.stop_calls == 1
    assert recorder.shutdown_calls == 1
    assert recorder.shutdown_event.is_set()
    assert recorder.interrupt_stop_event.is_set()
    assert recorder.start_recording_event.is_set()
    assert recorder.stop_recording_event.is_set()
    assert manager.input_queue.get_nowait() is None
    assert tts_track.stop_calls == 1
    assert playback_track.stop_calls == 1


def test_audio_manager_flush_utterance_stops_after_buffered_audio_is_fed():
    manager = AudioManager.__new__(AudioManager)
    manager.input_queue = queue.Queue()
    manager._closed = False
    manager._stt_enabled = True
    manager._stt_initialization_event = threading.Event()
    manager._stt_initialization_event.set()
    manager._warmup_audio_logged = False
    manager._warmup_audio_dropped_chunks = 0
    manager._voice_capture_lock = threading.Lock()
    manager._is_capturing_voice = False
    manager._segmentation_hold = False
    manager._held_recording_started_at = None

    class DummyRecorder:
        def __init__(self):
            self.is_recording = True
            self.feed_calls = []
            self.stop_calls = 0

        def feed_audio(self, chunk):
            self.feed_calls.append(chunk)

        def stop(self):
            self.stop_calls += 1
            self.is_recording = False

    recorder = DummyRecorder()
    manager.recorder = recorder

    worker = threading.Thread(target=manager._feed_audio_loop, daemon=True)
    worker.start()

    manager.process_audio_chunk(b"hello")
    assert manager.flush_utterance(source="android:test-session", timestamp_ms=1234567890) is True
    manager.input_queue.put_nowait(None)

    worker.join(timeout=1.0)
    assert not worker.is_alive()
    assert recorder.feed_calls == [b"hello"]
    assert recorder.stop_calls == 1


def test_audio_manager_emit_status_deduplicates_repeated_updates():
    updates = []
    manager = AudioManager.__new__(AudioManager)
    manager.status_callback = updates.append
    manager._last_status_signature = None

    manager._emit_status("warming", "Preparing voice pipeline on server...")
    manager._emit_status("warming", "Preparing voice pipeline on server...")
    manager._emit_status("ready", "Voice pipeline ready.")

    assert [item["state"] for item in updates] == ["warming", "ready"]
    assert updates[0]["event"] == "readiness"
    assert updates[1]["detail"] == "Voice pipeline ready."


def test_audio_manager_handle_playback_status_strips_file_path():
    updates = []
    manager = AudioManager.__new__(AudioManager)
    manager.status_callback = updates.append
    manager._last_status_signature = None

    manager._handle_playback_status(
        {
            "event": "playback",
            "state": "playing",
            "detail": "Streaming local file.",
            "file_path": os.path.join("C:\\music", "demo-track.mp3"),
        }
    )

    assert len(updates) == 1
    assert updates[0]["event"] == "playback"
    assert updates[0]["state"] == "playing"
    assert updates[0]["file_name"] == "demo-track.mp3"
    assert "file_path" not in updates[0]


def test_audio_manager_playback_controls_delegate_to_playback_track(tmp_path):
    manager = AudioManager.__new__(AudioManager)
    manager._closed = False
    manager._tts_generation = 4
    manager._stop_tts_event = threading.Event()

    target_file = tmp_path / "song.wav"
    target_file.write_bytes(b"RIFFdemo")

    class DummyTrack:
        def __init__(self):
            self.play_calls = []
            self.pause_calls = 0
            self.resume_calls = 0
            self.callback = None

        def set_playback_status_callback(self, callback):
            self.callback = callback

        def play_audio_file(self, file_path, source="audio_file"):
            self.play_calls.append((file_path, source))
            return {"event": "playback", "state": "playing", "detail": "queued"}

        def pause_playback(self):
            self.pause_calls += 1
            return True

        def resume_playback(self):
            self.resume_calls += 1
            return True

        def get_playback_status(self):
            return {"event": "playback", "state": "paused", "detail": "paused"}

    tts_track = DummyTrack()
    playback_track = DummyTrack()
    manager.set_tts_track(tts_track)
    manager.set_playback_track(playback_track)

    status = manager.play_audio_file(str(target_file), source="admin_api")

    assert status["state"] == "playing"
    assert tts_track.callback is None
    assert playback_track.callback == manager._handle_playback_status
    assert playback_track.play_calls == [(str(target_file), "admin_api")]
    assert manager._tts_generation == 4
    assert not manager._stop_tts_event.is_set()
    assert manager.pause_playback() is True
    assert manager.resume_playback() is True
    assert manager.get_playback_status()["state"] == "paused"
    assert playback_track.pause_calls == 1
    assert playback_track.resume_calls == 1
    assert tts_track.play_calls == []
    assert tts_track.pause_calls == 0
    assert tts_track.resume_calls == 0


def test_audio_manager_playback_uses_legacy_tts_track_until_playback_is_registered(tmp_path):
    manager = AudioManager.__new__(AudioManager)
    target_file = tmp_path / "legacy.wav"
    target_file.write_bytes(b"RIFFdemo")

    class DummyTrack:
        def __init__(self):
            self.play_calls = []

        def play_audio_file(self, file_path, source="audio_file"):
            self.play_calls.append((file_path, source))
            return {"state": "playing"}

    manager.tts_track = DummyTrack()

    assert manager.play_audio_file(str(target_file))["state"] == "playing"
    assert manager.tts_track.play_calls == [(str(target_file), "audio_file")]


def test_audio_manager_transcription_loop_ignores_blank_audio_placeholder():
    transcripts = []
    manager = AudioManager.__new__(AudioManager)
    manager._closed = False
    manager._stt_generation = 1
    manager.recorder = None
    manager.on_text_callback = transcripts.append
    manager._save_captured_voice = lambda transcript: None


    class DummyRecorder:
        def __init__(self):
            self.calls = 0

        def text(self):
            self.calls += 1
            if self.calls == 1:
                return "[BLANK_AUDIO]"
            manager._closed = True
            return "actual speech"

    recorder = DummyRecorder()
    manager.recorder = recorder

    manager._transcription_loop(recorder, 1)

    assert transcripts == ["actual speech"]


def test_audio_manager_stop_speaking_invalidates_tts_without_stopping_media():
    manager = AudioManager.__new__(AudioManager)
    manager._closed = False
    manager._stop_tts_event = threading.Event()
    manager._tts_generation = 4
    manager._tts_generation_lock = threading.Lock()

    class DummyTrack:
        def __init__(self):
            self.stop_calls = 0

        def stop_playback(self):
            self.stop_calls += 1

    tts_track = DummyTrack()
    playback_track = DummyTrack()
    manager.tts_track = tts_track
    manager.playback_track = playback_track

    manager.stop_speaking(source="test")

    assert manager._stop_tts_event.is_set()
    assert manager._tts_generation == 5
    assert tts_track.stop_calls == 1
    assert playback_track.stop_calls == 0


def test_audio_manager_stop_playback_does_not_stop_or_cancel_tts():
    manager = AudioManager.__new__(AudioManager)
    manager._stop_tts_event = threading.Event()
    manager._tts_generation = 4

    class DummyTrack:
        def __init__(self):
            self.stop_calls = 0

        def stop_playback(self, reason="Playback stopped."):
            self.stop_calls += 1
            return False

    tts_track = DummyTrack()
    playback_track = DummyTrack()
    manager.tts_track = tts_track
    manager.playback_track = playback_track

    assert manager.stop_playback(source="test") is True
    assert playback_track.stop_calls == 1
    assert tts_track.stop_calls == 0
    assert manager._tts_generation == 4
    assert not manager._stop_tts_event.is_set()


def test_audio_manager_reload_settings_cancels_inflight_tts_without_restarting_stt():
    initial_settings = {
        "tts": {
            "provider": "system",
            "rate": 1.0,
            "system_voice": "voice-a",
            "openai": {},
            "azure": {},
        },
        "stt": {
            "model": "tiny.en",
            "language": "en",
            "device": "cpu",
            "compute_type": "float32",
            "silero_sensitivity": 0.4,
            "post_speech_silence_duration": 0.6,
        },
    }
    current_settings = {
        "tts": {
            "provider": "system",
            "rate": 1.2,
            "system_voice": "voice-b",
            "openai": {},
            "azure": {},
        },
        "stt": dict(initial_settings["stt"]),
    }
    manager = AudioManager.__new__(AudioManager)
    manager.settings_provider = lambda: current_settings
    manager._settings_lock = threading.Lock()
    manager._settings_snapshot = initial_settings
    manager._last_status_signature = None
    updates = []
    manager.status_callback = updates.append
    stop_calls = []
    setup_calls = []
    restart_calls = []
    manager.stop_speaking = lambda source="unknown": stop_calls.append(source)
    manager._setup_tts = lambda settings: setup_calls.append(settings["tts"].copy())
    manager._restart_stt = lambda settings, initial=False: restart_calls.append((settings, initial))

    manager.reload_settings()

    assert stop_calls == ["reload_settings"]
    assert len(setup_calls) == 1
    assert restart_calls == []
    assert [item["state"] for item in updates] == ["warming", "ready"]


def test_tts_audio_track_queue_audio_file_honors_abort_callback(monkeypatch):
    track = TTSAudioStreamTrack()

    class FakeResampledFrame:
        def __init__(self, payload):
            self._payload = payload

        def to_ndarray(self):
            class FakeArray:
                def __init__(self, payload):
                    self._payload = payload

                def tobytes(self):
                    return self._payload

            return FakeArray(self._payload)

    class FakeResampler:
        def __init__(self, format=None, layout=None, rate=None):
            self._payload = b"\x00\x01" * track.frame_size * 2

        def resample(self, frame):
            if frame is None:
                return []
            return [FakeResampledFrame(self._payload)]

    class FakeContainer:
        def __init__(self):
            self.streams = types.SimpleNamespace(audio=[object()])

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def decode(self, stream):
            return [object(), object()]

    fake_av_module = types.SimpleNamespace(
        open=lambda path: FakeContainer(),
        AudioResampler=FakeResampler,
    )
    monkeypatch.setitem(sys.modules, "av", fake_av_module)

    track.queue_audio_file("ignored.wav", should_abort=lambda: True)

    assert track.q.empty()


def test_audio_manager_speak_thread_abort_callback_tracks_stop_requests(tmp_path):
    manager = AudioManager.__new__(AudioManager)
    manager._closed = False
    manager._tts_generation = 1
    manager._tts_generation_lock = threading.Lock()
    manager._stop_tts_event = threading.Event()

    class DummyTrack:
        def __init__(self):
            self.stop_calls = 0
            self.abort_before_stop = None
            self.abort_after_stop = None

        def stop_playback(self):
            self.stop_calls += 1

        def queue_audio_file(self, file_path, should_abort=None):
            assert should_abort is not None
            self.abort_before_stop = should_abort()
            manager.stop_speaking(source="test:queue_audio_file")
            self.abort_after_stop = should_abort()

    manager.tts_track = DummyTrack()

    def fake_synthesize_system_tts(text, temp_path, settings):
        with open(temp_path, "wb") as handle:
            handle.write(b"x" * 2048)

    manager._synthesize_system_tts = fake_synthesize_system_tts

    settings = {
        "tts": {
            "provider": "system",
            "rate": 1.0,
            "system_voice": "",
            "openai": {},
            "azure": {},
        }
    }

    manager._speak_thread("hello", settings, generation=1)

    assert manager.tts_track.abort_before_stop is False
    assert manager.tts_track.abort_after_stop is True
    assert manager.tts_track.stop_calls == 1


def test_sanitize_text_for_tts_strips_markdown_and_emoji():
    cleaned = _sanitize_text_for_tts(
        "I’m **AutoYouClaw**.\n- Ready to help when you need it. 💡"
    )

    assert "**" not in cleaned
    assert "💡" not in cleaned
    assert "AutoYouClaw" in cleaned
    assert "Ready to help when you need it" in cleaned


def test_audio_manager_speak_thread_uses_fresh_output_path():
    manager = AudioManager.__new__(AudioManager)
    manager._closed = False
    manager._tts_generation = 1
    manager._tts_generation_lock = threading.Lock()
    manager._stop_tts_event = threading.Event()

    class DummyTrack:
        def __init__(self):
            self.file_path = None

        def stop_playback(self):
            return None

        def queue_audio_file(self, file_path, should_abort=None):
            self.file_path = file_path

    manager.tts_track = DummyTrack()
    synth_state = {}

    def fake_synthesize_system_tts(text, temp_path, settings):
        synth_state["path_exists_before_synthesis"] = os.path.exists(temp_path)
        with open(temp_path, "wb") as handle:
            handle.write(b"x" * 256)

    manager._synthesize_system_tts = fake_synthesize_system_tts

    settings = {
        "tts": {
            "provider": "system",
            "rate": 1.0,
            "system_voice": "",
            "openai": {},
            "azure": {},
        }
    }

    manager._speak_thread("hello", settings, generation=1)

    assert synth_state["path_exists_before_synthesis"] is False
    assert manager.tts_track.file_path is not None


def test_audio_manager_speak_thread_uses_aiff_temp_file_for_macos_system_tts(monkeypatch):
    manager = AudioManager.__new__(AudioManager)
    manager._closed = False
    manager._tts_generation = 1
    manager._tts_generation_lock = threading.Lock()
    manager._stop_tts_event = threading.Event()

    class DummyTrack:
        def __init__(self):
            self.file_path = None

        def stop_playback(self):
            return None

        def queue_audio_file(self, file_path, should_abort=None):
            self.file_path = file_path

    manager.tts_track = DummyTrack()
    synth_state = {}

    def fake_synthesize_system_tts(text, temp_path, settings):
        synth_state["suffix"] = os.path.splitext(temp_path)[1]
        with open(temp_path, "wb") as handle:
            handle.write(b"x" * 256)

    manager._synthesize_system_tts = fake_synthesize_system_tts
    monkeypatch.setattr(audio_manager_module.sys, "platform", "darwin")

    settings = {
        "tts": {
            "provider": "system",
            "rate": 1.0,
            "system_voice": "",
            "openai": {},
            "azure": {},
        }
    }

    manager._speak_thread("hello", settings, generation=1)

    assert synth_state["suffix"] == ".aiff"
    assert manager.tts_track.file_path is not None


def test_list_system_tts_voices_uses_macos_say_fallback(monkeypatch):
    sample_output = """Agnes                en_US    # Hello.\nGood News            en_US    # Great news.\nTing-Ting            zh_CN    # 你好。\n"""

    monkeypatch.setattr(audio_manager_module, "_SYSTEM_TTS_VOICE_CACHE", None)
    monkeypatch.setattr(audio_manager_module.sys, "platform", "darwin")
    monkeypatch.setattr(audio_manager_module, "pyttsx3", None)

    def fake_run(args, **kwargs):
        assert args == ["say", "-v", "?"]
        return subprocess.CompletedProcess(args, 0, stdout=sample_output, stderr="")

    monkeypatch.setattr(audio_manager_module.subprocess, "run", fake_run)

    voices = audio_manager_module.list_system_tts_voices(force_refresh=True)

    assert [voice["id"] for voice in voices] == ["Agnes", "Good News", "Ting-Ting"]
    assert [voice["languages"] for voice in voices] == ["en_US", "en_US", "zh_CN"]


def test_audio_manager_setup_tts_uses_macos_say_without_pyttsx3(monkeypatch):
    manager = AudioManager.__new__(AudioManager)
    settings = {
        "tts": {
            "provider": "system",
            "rate": 1.0,
            "system_voice": "",
            "openai": {},
            "azure": {},
        }
    }

    monkeypatch.setattr(audio_manager_module.sys, "platform", "darwin")
    monkeypatch.setattr(audio_manager_module, "pyttsx3", None)

    manager._setup_tts(settings)

    assert manager.tts_available is True


def test_synthesize_system_tts_uses_macos_say_in_source_mode(monkeypatch, tmp_path):
    manager = AudioManager.__new__(AudioManager)
    target_path = str(tmp_path / "tts-output.wav")
    invocations = []

    monkeypatch.setattr(audio_manager_module.sys, "platform", "darwin")

    def fake_run(args, **kwargs):
        invocations.append(args)
        assert args[0] == "say"
        assert "-o" in args
        assert "-r" in args
        with wave.open(args[args.index("-o") + 1], "wb") as handle:
            handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            handle.writeframes(b"\x00\x10" * 1600)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(audio_manager_module.subprocess, "run", fake_run)

    settings = {
        "tts": {
            "provider": "system",
            "rate": 1.2,
            "system_voice": "Samantha",
            "openai": {},
            "azure": {},
        }
    }

    manager._synthesize_system_tts("hello", target_path, settings)

    assert invocations
    assert invocations[0][0] == "say"
    assert invocations[0][invocations[0].index("-v") + 1] == "Samantha"
    assert os.path.getsize(target_path) >= 256


def test_macos_tts_retries_silent_default_without_changing_settings(monkeypatch, tmp_path):
    manager = AudioManager.__new__(AudioManager)
    target = str(tmp_path / "speech.aiff")
    settings = {"tts": {"system_voice": "", "rate": 1.0}}
    calls = []
    monkeypatch.setattr(audio_manager_module.sys, "platform", "darwin")

    def fake_run(args, **kwargs):
        calls.append(args)
        # A valid container with silent samples reproduces `say` exiting zero
        # with no speech. The second voice supplies real, nonzero samples.
        with wave.open(target, "wb") as handle:
            handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            handle.writeframes((b"\x00\x00" if len(calls) == 1 else b"\x00\x10") * 1600)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(audio_manager_module.subprocess, "run", fake_run)
    manager._synthesize_system_tts("A synthetic test reply.", target, settings)

    assert len(calls) == 2
    assert "-v" not in calls[0]
    assert calls[1][calls[1].index("-v") + 1] == "Samantha"
    assert calls[1][-2:] == ["--", "A synthetic test reply."]
    assert settings["tts"]["system_voice"] == ""
    assert audio_manager_module._macos_tts_has_audio(target)
    assert not audio_manager_module._macos_tts_has_audio(str(tmp_path / "missing.aiff"))


def test_synthesize_system_tts_retries_with_windows_sapi_when_python_output_is_empty(monkeypatch, tmp_path):
    manager = AudioManager.__new__(AudioManager)
    target_path = str(tmp_path / "tts-output.wav")
    calls = []

    monkeypatch.setattr(audio_manager_module, "_should_use_python_tts_subprocess", lambda: True)
    monkeypatch.setattr(audio_manager_module.sys, "platform", "win32")

    def fake_run(args, **kwargs):
        calls.append(args[0])
        command_name = str(args[0]).lower()
        if command_name.endswith("python.exe"):
            with open(target_path, "wb") as handle:
                handle.write(b"")
        elif command_name == "powershell.exe":
            with open(kwargs["env"]["AUTOYOU_TTS_PATH"], "wb") as handle:
                handle.write(b"x" * 256)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(audio_manager_module.subprocess, "run", fake_run)

    settings = {
        "tts": {
            "provider": "system",
            "rate": 1.0,
            "system_voice": "",
            "openai": {},
            "azure": {},
        }
    }

    manager._synthesize_system_tts("hello", target_path, settings)

    assert any("python" in os.path.basename(str(call)).lower() for call in calls)
    assert "powershell.exe" in [str(call).lower() for call in calls]
    assert os.path.getsize(target_path) >= 256
