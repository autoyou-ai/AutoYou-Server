# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import time

import shared.audio_manager as audio_manager_module
import shared.platform_runtime as platform_runtime
from shared.audio_manager import AudioManager
from shared.speech_config import normalize_speech_config


def _speech_settings():
    return normalize_speech_config(
        {
            "tts": {"provider": "off"},
            "stt": {
                "model": "tiny.en",
                "language": "en",
                "device": "cpu",
                "compute_type": "float32",
            },
        }
    )


def _wait_until(predicate, timeout=1.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_readiness_snapshot_prefers_live_recorder_over_cached_warming_status():
    manager = AudioManager.__new__(AudioManager)
    manager._closed = False
    manager.recorder = object()
    manager._last_readiness_status = {
        "event": "readiness",
        "state": "warming",
        "detail": "stale warmup status",
    }

    assert manager.get_readiness_status()["state"] == "ready"


class _FakeAudioToTextRecorder:
    _transcription_worker = object()
    _audio_data_worker = object()

    def _start_thread(self, target=None, args=()):
        return None


class _FakeRecorder:
    def __init__(self, label="recorder"):
        self.label = label
        self.shutdown_called = False

    def text(self):
        time.sleep(0.05)
        return ""

    def start(self):
        return None

    def stop(self):
        return None

    def shutdown(self):
        self.shutdown_called = True


class _FakeStream:
    def __init__(self, is_tty):
        self._is_tty = is_tty

    def isatty(self):
        return self._is_tty


def test_windows_realtimestt_worker_uses_thread_when_stdio_is_redirected(monkeypatch):
    monkeypatch.setattr(audio_manager_module.sys, "platform", "win32")
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(audio_manager_module.sys, "stdout", _FakeStream(False))
    monkeypatch.setattr(audio_manager_module.sys, "stderr", _FakeStream(True))

    assert audio_manager_module._should_use_threaded_realtimestt_worker() is True


def test_windows_realtimestt_worker_uses_thread_for_source_console(monkeypatch):
    """Regression: a plain `run_autoyou.bat` launch from a console is source
    mode with a tty, which used to take the multiprocessing path. The spawned
    child died in interpreter bootstrap (stdlib `typing` resolved to
    cv2/typing/__init__.py), so Windows now always uses the in-process worker."""
    monkeypatch.delenv("AUTOYOU_REALTIMESTT_THREAD_WORKER", raising=False)
    monkeypatch.setattr(audio_manager_module.sys, "platform", "win32")
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(audio_manager_module.sys, "stdout", _FakeStream(True))
    monkeypatch.setattr(audio_manager_module.sys, "stderr", _FakeStream(True))

    assert audio_manager_module._should_use_threaded_realtimestt_worker() is True


def test_redirected_stdio_live_audio_guard_has_explicit_override(monkeypatch):
    monkeypatch.setenv("AUTOYOU_ALLOW_REDIRECTED_STDIO_LIVE_AUDIO", "1")
    monkeypatch.setattr(audio_manager_module, "_should_disable_realtimestt_for_source_redirected_stdio", lambda: True)

    assert audio_manager_module.should_disable_live_audio_for_source_redirected_stdio() is False


def test_windows_source_redirected_stdio_keeps_realtimestt_startup_enabled(monkeypatch):
    monkeypatch.setattr(audio_manager_module.sys, "platform", "win32")
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(audio_manager_module.sys, "stdout", _FakeStream(False))
    monkeypatch.setattr(audio_manager_module.sys, "stderr", _FakeStream(True))
    settings = _speech_settings()
    statuses = []
    restart_calls = []

    def fake_restart(self, settings_arg, initial=False):
        restart_calls.append((settings_arg, initial))
        self._emit_status("ready", "Voice pipeline ready.")

    monkeypatch.setattr(AudioManager, "_restart_stt", fake_restart)

    manager = AudioManager(
        lambda _text: None,
        settings_provider=lambda: settings,
        status_callback=statuses.append,
    )

    try:
        assert manager._stt_enabled is True
        assert manager._feed_audio_thread is not None
        assert restart_calls == [(settings, True)]
        assert statuses[-1]["state"] == "ready"
    finally:
        manager.close()


def test_windows_source_redirected_stdio_thread_worker_opt_in_keeps_stt(monkeypatch):
    """AUTOYOU_REALTIMESTT_THREAD_WORKER=1 overrides the source-mode disable:
    the operator explicitly opted into the in-process worker (the same
    machinery compiled Windows builds use), so STT must stay enabled."""
    monkeypatch.setattr(audio_manager_module.sys, "platform", "win32")
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(audio_manager_module.sys, "stdout", _FakeStream(False))
    monkeypatch.setattr(audio_manager_module.sys, "stderr", _FakeStream(True))
    monkeypatch.setenv("AUTOYOU_REALTIMESTT_THREAD_WORKER", "1")

    assert audio_manager_module._should_disable_realtimestt_for_source_redirected_stdio() is False
    assert audio_manager_module._should_use_threaded_realtimestt_worker() is True


def test_windows_source_redirected_stdio_explicit_process_opt_out_still_disables(monkeypatch):
    """AUTOYOU_REALTIMESTT_THREAD_WORKER=0 must not sneak STT back on: it
    forces the multiprocessing worker, which is exactly what dies in this
    launch mode, so the hard disable stays in effect."""
    monkeypatch.setattr(audio_manager_module.sys, "platform", "win32")
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(audio_manager_module.sys, "stdout", _FakeStream(False))
    monkeypatch.setattr(audio_manager_module.sys, "stderr", _FakeStream(True))
    monkeypatch.setenv("AUTOYOU_REALTIMESTT_THREAD_WORKER", "0")

    assert audio_manager_module._should_disable_realtimestt_for_source_redirected_stdio() is True


def test_audio_manager_suppresses_stale_realtimestt_init_error(monkeypatch):
    settings = _speech_settings()
    statuses = []
    manager = AudioManager(
        lambda _text: None,
        settings_provider=lambda: settings,
        status_callback=statuses.append,
        enable_stt=False,
    )

    class FakeAudioToTextRecorder:
        _transcription_worker = object()
        _audio_data_worker = object()

        def _start_thread(self, target=None, args=()):
            return None

    def fail_after_manager_was_closed(*_args, **_kwargs):
        manager._closed = True
        raise RuntimeError("late init failed")

    monkeypatch.setattr(audio_manager_module, "AudioToTextRecorder", FakeAudioToTextRecorder)
    monkeypatch.setattr(audio_manager_module, "_patch_tqdm_ensure_lock", lambda: None)
    monkeypatch.setattr(audio_manager_module, "_ensure_torch_hub_repo_trusted", lambda _repo: None)
    monkeypatch.setattr(audio_manager_module, "_instantiate_realtimestt_recorder", fail_after_manager_was_closed)

    manager._closed = False
    manager._stt_generation = 3
    manager._init_stt(3, settings)

    assert not [
        payload
        for payload in statuses
        if payload.get("event") == "readiness" and payload.get("state") == "unavailable"
    ]


def test_audio_manager_retries_realtimestt_init_failure(monkeypatch):
    monkeypatch.setenv("AUTOYOU_STT_INIT_RETRY_MAX", "1")
    monkeypatch.setenv("AUTOYOU_STT_INIT_RETRY_DELAY_SECONDS", "0.01")
    monkeypatch.setenv("AUTOYOU_STT_INIT_TIMEOUT_SECONDS", "0")
    settings = _speech_settings()
    statuses = []
    calls = []
    manager = AudioManager(
        lambda _text: None,
        settings_provider=lambda: settings,
        status_callback=statuses.append,
        enable_stt=False,
    )

    def instantiate(*_args, **_kwargs):
        calls.append("call")
        if len(calls) == 1:
            raise RuntimeError("temporary init failure")
        return _FakeRecorder(), False

    monkeypatch.setattr(audio_manager_module, "AudioToTextRecorder", _FakeAudioToTextRecorder)
    monkeypatch.setattr(audio_manager_module, "_patch_tqdm_ensure_lock", lambda: None)
    monkeypatch.setattr(audio_manager_module, "_ensure_torch_hub_repo_trusted", lambda _repo: None)
    monkeypatch.setattr(audio_manager_module, "_instantiate_realtimestt_recorder", instantiate)

    manager._restart_stt(settings, initial=True)

    _wait_until(lambda: len(calls) == 2 and manager.recorder is not None)
    assert [payload.get("state") for payload in statuses if payload.get("event") == "readiness"][-1] == "ready"
    assert any("retrying" in str(payload.get("detail", "")) for payload in statuses)

    manager.close()


def test_audio_manager_watchdog_retries_hung_realtimestt_init(monkeypatch):
    monkeypatch.setenv("AUTOYOU_STT_INIT_RETRY_MAX", "1")
    monkeypatch.setenv("AUTOYOU_STT_INIT_RETRY_DELAY_SECONDS", "0")
    monkeypatch.setenv("AUTOYOU_STT_INIT_TIMEOUT_SECONDS", "0.02")
    settings = _speech_settings()
    statuses = []
    calls = []
    stale_recorders = []
    manager = AudioManager(
        lambda _text: None,
        settings_provider=lambda: settings,
        status_callback=statuses.append,
        enable_stt=False,
    )

    def instantiate(*_args, **_kwargs):
        calls.append("call")
        if len(calls) == 1:
            time.sleep(0.15)
            stale = _FakeRecorder("stale")
            stale_recorders.append(stale)
            return stale, False
        return _FakeRecorder("live"), False

    monkeypatch.setattr(audio_manager_module, "AudioToTextRecorder", _FakeAudioToTextRecorder)
    monkeypatch.setattr(audio_manager_module, "_patch_tqdm_ensure_lock", lambda: None)
    monkeypatch.setattr(audio_manager_module, "_ensure_torch_hub_repo_trusted", lambda _repo: None)
    monkeypatch.setattr(audio_manager_module, "_instantiate_realtimestt_recorder", instantiate)

    manager._restart_stt(settings, initial=True)

    _wait_until(lambda: len(calls) == 2 and getattr(manager.recorder, "label", "") == "live")
    _wait_until(lambda: bool(stale_recorders) and stale_recorders[0].shutdown_called)
    assert any("timed out" in str(payload.get("detail", "")) for payload in statuses)

    manager.close()


def test_audio_manager_replays_audio_buffered_during_stt_warmup(monkeypatch):
    settings = _speech_settings()
    monkeypatch.setattr(AudioManager, "_restart_stt", lambda *_args, **_kwargs: None)
    manager = AudioManager(
        lambda _text: None,
        settings_provider=lambda: settings,
    )

    class _FeedRecorder:
        def __init__(self):
            self.chunks = []

        def feed_audio(self, chunk):
            self.chunks.append(chunk)

        def stop(self):
            return None

        def shutdown(self):
            return None

    try:
        first_chunk = b"synthetic-warmup-audio"
        manager.process_audio_chunk(first_chunk)
        assert manager.input_queue.qsize() == 1

        recorder = _FeedRecorder()
        manager._set_recorder_ready(recorder)
        _wait_until(lambda: recorder.chunks == [first_chunk])
    finally:
        manager.close()
