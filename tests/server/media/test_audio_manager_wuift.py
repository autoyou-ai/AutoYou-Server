# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-4bc470923e5435b992068f85

"""WUIFT ("Wait Until I Finish Talking") segmentation-hold unit tests.

The hold must (a) disable VAD-silence utterance finalization on the live
recorder without a restart, (b) survive recorder (re)initialization, and
(c) stay memory-bounded via the WUIFT_MAX_SEGMENT_SECONDS safety cap.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-4bc470923e5435b992068f85"


import queue
import threading

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

from shared.audio_manager import (
    AudioManager,
    WUIFT_HOLD_SILENCE_SECONDS,
    WUIFT_MAX_SEGMENT_SECONDS,
)
from shared.whisper_downloader import WhisperCppRecorder


def _bare_manager():
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
    manager._pre_hold_silence_duration = None
    manager._held_recording_started_at = None
    manager.recorder = None
    return manager


class _RealtimeSttStyleRecorder:
    """Recorder without a native hold: the manager mutates
    post_speech_silence_duration in place (RealtimeSTT reads it per loop pass).
    """

    def __init__(self, silence_duration=0.6):
        self.post_speech_silence_duration = silence_duration
        self.is_recording = False
        self.stop_calls = 0

    def feed_audio(self, chunk):
        pass

    def stop(self):
        self.stop_calls += 1
        self.is_recording = False


class _NativeHoldRecorder:
    """Recorder exposing set_segmentation_hold (WhisperCppRecorder shape)."""

    def __init__(self):
        self.hold_states = []
        self.flush_calls = 0
        self.is_recording = False

    def set_segmentation_hold(self, active):
        self.hold_states.append(bool(active))

    def request_flush(self):
        self.flush_calls += 1

    def feed_audio(self, chunk):
        pass


def test_segmentation_hold_mutates_and_restores_silence_duration():
    manager = _bare_manager()
    recorder = _RealtimeSttStyleRecorder(silence_duration=0.6)
    manager.recorder = recorder

    assert manager.set_segmentation_hold(True, source="test") is True
    assert recorder.post_speech_silence_duration == WUIFT_HOLD_SILENCE_SECONDS

    assert manager.set_segmentation_hold(False, source="test") is True
    assert recorder.post_speech_silence_duration == 0.6
    assert manager._pre_hold_silence_duration is None


def test_segmentation_hold_prefers_native_recorder_hold():
    manager = _bare_manager()
    recorder = _NativeHoldRecorder()
    manager.recorder = recorder

    manager.set_segmentation_hold(True, source="test")
    manager.set_segmentation_hold(False, source="test")

    assert recorder.hold_states == [True, False]


def test_pending_hold_applied_when_recorder_becomes_ready():
    manager = _bare_manager()

    # Hold requested while STT is still initializing: no recorder yet.
    assert manager.set_segmentation_hold(True, source="test") is True
    assert manager._segmentation_hold is True

    recorder = _RealtimeSttStyleRecorder(silence_duration=0.6)
    manager._set_recorder_ready(recorder)

    assert manager.recorder is recorder
    assert recorder.post_speech_silence_duration == WUIFT_HOLD_SILENCE_SECONDS


def test_flush_utterance_resets_held_segment_timer_and_stops_recorder():
    manager = _bare_manager()
    recorder = _RealtimeSttStyleRecorder()
    recorder.is_recording = True
    manager.recorder = recorder
    manager._segmentation_hold = True
    manager._held_recording_started_at = 123.0

    worker = threading.Thread(target=manager._feed_audio_loop, daemon=True)
    worker.start()

    assert manager.flush_utterance(source="wuift:test", timestamp_ms=42) is True
    manager.input_queue.put_nowait(None)
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert recorder.stop_calls == 1
    assert manager._held_recording_started_at is None


def test_safety_cap_auto_flushes_overlong_held_segment(monkeypatch):
    manager = _bare_manager()
    recorder = _NativeHoldRecorder()
    recorder.is_recording = True
    manager.recorder = recorder
    manager._segmentation_hold = True

    fake_now = [1000.0]
    monkeypatch.setattr("shared.audio_manager.time.monotonic", lambda: fake_now[0])

    manager._enforce_held_segment_cap(recorder)
    assert manager._held_recording_started_at == 1000.0
    assert recorder.flush_calls == 0

    fake_now[0] = 1000.0 + WUIFT_MAX_SEGMENT_SECONDS - 1
    manager._enforce_held_segment_cap(recorder)
    assert recorder.flush_calls == 0

    fake_now[0] = 1000.0 + WUIFT_MAX_SEGMENT_SECONDS + 1
    manager._enforce_held_segment_cap(recorder)
    assert recorder.flush_calls == 1
    assert manager._held_recording_started_at is None


def test_safety_cap_ignores_idle_recorder_and_released_hold():
    manager = _bare_manager()
    recorder = _NativeHoldRecorder()
    manager.recorder = recorder

    manager._segmentation_hold = True
    recorder.is_recording = False
    manager._held_recording_started_at = 1.0
    manager._enforce_held_segment_cap(recorder)
    assert manager._held_recording_started_at is None
    assert recorder.flush_calls == 0

    manager._segmentation_hold = False
    recorder.is_recording = True
    manager._held_recording_started_at = 1.0
    manager._enforce_held_segment_cap(recorder)
    assert manager._held_recording_started_at is None
    assert recorder.flush_calls == 0


def test_whispercpp_recorder_segmentation_hold_toggles_event():
    recorder = WhisperCppRecorder.__new__(WhisperCppRecorder)
    recorder._segmentation_hold = threading.Event()

    recorder.set_segmentation_hold(True)
    assert recorder._segmentation_hold.is_set()

    recorder.set_segmentation_hold(False)
    assert not recorder._segmentation_hold.is_set()
