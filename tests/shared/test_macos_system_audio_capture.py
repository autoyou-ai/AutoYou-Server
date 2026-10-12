"""The Mac system-audio bridge keeps PCM framing and source discovery honest."""

import os
import platform
import queue
import threading

from shared import video_call_manager as video


def test_macos_system_audio_helper_prefers_bundled_or_explicit_binary(tmp_path, monkeypatch):
    helper = tmp_path / "AutoYouAudioCapture"
    helper.write_bytes(b"synthetic executable")
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "mac_ver", lambda: ("14.0", (14, 0, 0), ""))
    monkeypatch.setattr(video.os, "access", lambda path, mode: path == helper and mode == os.X_OK)
    monkeypatch.setenv("AUTOYOU_MACOS_AUDIO_HELPER", str(helper))
    assert video.find_macos_system_audio_helper() == helper
    monkeypatch.setattr(platform, "mac_ver", lambda: ("12.7", (12, 7, 0), ""))
    assert video.find_macos_system_audio_helper() is None


def test_macos_system_audio_chunks_pcm_for_webrtc(monkeypatch, tmp_path):
    track = video.LocalAudioInputTrack(capture_loopback=True)
    stop = threading.Event()
    pcm = b"\x01\x00" * track.frame_size

    class FakePipe:
        def fileno(self):
            return 99

        def close(self):
            pass

    class FakeProcess:
        def __init__(self):
            self.stdout = FakePipe()
            self.stderr = FakePipe()

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(video.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(video.select, "select", lambda *args: ([args[0][0]], [], []))

    def read_frames(fd, size):
        assert fd == 99 and size >= len(pcm) * 2
        stop.set()
        return pcm * 2

    monkeypatch.setattr(video.os, "read", read_frames)
    track._capture_macos_system_audio(tmp_path / "AutoYouAudioCapture", stop)
    assert track._audio_queue.get_nowait() == pcm
    assert track._audio_queue.get_nowait() == pcm
    assert track.capture_status()["device_open"] is False
    try:
        track._audio_queue.get_nowait()
    except queue.Empty:
        pass
    else:
        raise AssertionError("Unexpected third audio frame")
