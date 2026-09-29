# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-via-dfdaf7450c490d43ace8c7da


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import asyncio
import contextlib
import json
import logging
import sys
import threading
import time
from io import BytesIO
from pathlib import Path
from types import ModuleType

import pytest

from shared import video_call_manager
from shared.video_call_manager import IncomingVideoTrackSink, LatestVideoFrameRegistry, RemoteDesktopVideoStreamTrack

__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-via-dfdaf7450c490d43ace8c7da"


@pytest.fixture(autouse=True)
def _disable_windows_desktop_attach(monkeypatch):
    monkeypatch.setattr(video_call_manager, "_ensure_windows_interactive_desktop_for_capture", lambda: None)


def test_remote_desktop_track_gated_until_enabled():
    """The desktop track must not capture/stream until the client starts video."""

    async def run() -> None:
        track = RemoteDesktopVideoStreamTrack(fps=8.0, max_width=1920)
        capture_calls = []
        track._capture_image = lambda: capture_calls.append(True) or video_call_manager.Image.new(
            "RGB", (320, 180), (20, 40, 60)
        )
        assert track.is_enabled is False

        # Disabled tracks emit black slowly so WebRTC replaces stale pixels,
        # but they never touch desktop capture APIs.
        frame = await asyncio.wait_for(track.recv(), timeout=1.5)
        assert frame.to_image().getbbox() is None
        assert (frame.width, frame.height) == (1920, 1080)
        assert capture_calls == []

        track.enable()
        assert track.is_enabled is True
        frame = await asyncio.wait_for(track.recv(), timeout=5.0)
        assert frame.to_image().getpixel((0, 0)) == (20, 40, 60)
        assert capture_calls == [True]

        track.disable()
        assert track.is_enabled is False
        frame = await asyncio.wait_for(track.recv(), timeout=1.5)
        assert frame.to_image().getbbox() is None
        assert capture_calls == [True]

    asyncio.run(run())


def test_latest_video_registry_expires_stale_camera_pixels(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(video_call_manager.time, "time", lambda: now[0])
    registry = LatestVideoFrameRegistry(max_age_seconds=2.0)
    registry.publish_jpeg(session_id="synthetic-camera", jpeg_bytes=b"jpeg", width=16, height=9)

    assert registry.latest("synthetic-camera") is not None
    now[0] += 2.1

    assert registry.latest("synthetic-camera") is None
    assert registry.status()["active"] is False
    assert registry.status()["active_sessions"] == 0


def test_camera_track_reopens_after_hot_unplug_and_clears_stale_frame(monkeypatch):
    numpy = pytest.importorskip("numpy")
    state = {"opens": 0, "released": []}

    class FakeCapture:
        def __init__(self, generation):
            self.generation = generation
            self.reads = 0

        def isOpened(self):
            return True

        def set(self, _property, _value):
            return True

        def read(self):
            self.reads += 1
            if self.generation == 1 and self.reads > 1:
                return False, None
            color = (20, 30, 40) if self.generation == 1 else (70, 80, 90)
            return True, numpy.full((24, 32, 3), color, dtype=numpy.uint8)

        def release(self):
            state["released"].append(self.generation)

    def video_capture(_index):
        state["opens"] += 1
        return FakeCapture(state["opens"])

    try:
        import cv2 as fake_cv2
    except ImportError:
        fake_cv2 = ModuleType("cv2")
        monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setattr(fake_cv2, "VideoCapture", video_capture, raising=False)
    monkeypatch.setattr(fake_cv2, "CAP_PROP_FRAME_WIDTH", 3, raising=False)
    monkeypatch.setattr(fake_cv2, "CAP_PROP_FRAME_HEIGHT", 4, raising=False)
    monkeypatch.setattr(fake_cv2, "COLOR_BGR2RGB", 1, raising=False)
    monkeypatch.setattr(fake_cv2, "cvtColor", lambda frame, _mode: frame, raising=False)

    track = video_call_manager.CameraVideoStreamTrack(device_index=0, fps=3)

    def wait_until(predicate, timeout=4.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError(f"camera condition not met; state={state!r}")

    try:
        track.enable()
        wait_until(lambda: track.capture_preview_image().getpixel((0, 0)) == (20, 30, 40))
        wait_until(lambda: "reconnecting" in track._failure_detail)
        assert track._latest_image is None
        assert track.capture_preview_image().getpixel((0, 0)) != (20, 30, 40)

        wait_until(lambda: state["opens"] >= 2 and track._latest_image is not None)
        assert track.capture_preview_image().getpixel((0, 0)) == (70, 80, 90)
    finally:
        track.disable()

    assert track._thread is None
    assert track._latest_image is None
    assert track.capture_preview_image().getbbox() is None
    assert 1 in state["released"] and 2 in state["released"]


def test_camera_track_cleans_up_thread_when_opencv_is_missing(monkeypatch):
    track = video_call_manager.CameraVideoStreamTrack()
    import_started = threading.Event()
    release_import = threading.Event()

    def missing_cv2():
        import_started.set()
        release_import.wait(timeout=1.0)
        raise ImportError("synthetic missing cv2")

    monkeypatch.setattr(track, "_load_cv2", missing_cv2)

    track.enable()
    assert import_started.wait(timeout=1.0)
    thread = track._thread
    assert thread is not None
    release_import.set()
    thread.join(timeout=1.0)

    assert thread.is_alive() is False
    assert track._running is False
    assert track._thread is None
    assert "OpenCV" in track._failure_detail
    track.disable()


def test_composite_video_uses_fixed_canvas_and_contains_each_source():
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    desktop = video_call_manager.Image.new("RGB", (1600, 900), (180, 20, 20))
    portrait_camera = video_call_manager.Image.new("RGB", (480, 640), (20, 180, 20))
    track = video_call_manager.CompositeVideoStreamTrack([], max_width=1280)

    image = track._compose_images([desktop, portrait_camera])

    assert image.size == (1280, 720)
    assert image.getpixel((320, 360)) == (180, 20, 20)
    assert image.getpixel((960, 360)) == (20, 180, 20)
    assert image.getpixel((320, 20)) == (0, 0, 0)
    assert image.getpixel((650, 20)) == (0, 0, 0)


def test_composite_video_reads_track_preview_without_nested_recv_pacing():
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    class PreviewTrack:
        def __init__(self):
            self.preview_calls = 0

        def capture_preview_image(self, *, max_width):
            self.preview_calls += 1
            assert max_width == 640
            return video_call_manager.Image.new("RGB", (64, 36), (10, 20, 30))

        async def recv(self):
            raise AssertionError("nested track recv pacing should not run")

    source = PreviewTrack()
    track = video_call_manager.CompositeVideoStreamTrack([("camera", source)], max_width=640)
    image = asyncio.run(track._source_image(source))

    assert image.size == (64, 36)
    assert source.preview_calls == 1


def test_composite_video_preview_is_fixed_canvas_and_never_captures_when_disabled():
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    class PreviewTrack:
        def __init__(self, color=None, error=None):
            self.color = color
            self.error = error
            self.preview_calls = 0

        def capture_preview_image(self, *, max_width):
            self.preview_calls += 1
            assert max_width == 640
            if self.error:
                raise RuntimeError(self.error)
            return video_call_manager.Image.new("RGB", (64, 36), self.color)

    good = PreviewTrack((10, 20, 30))
    failed = PreviewTrack(error="synthetic camera unavailable")
    track = video_call_manager.CompositeVideoStreamTrack(
        [("remote_desktop", good), ("camera", failed)],
        max_width=1280,
    )

    disabled = track.capture_preview_image(max_width=640)
    assert disabled.size == (640, 360)
    assert disabled.getbbox() is None
    assert good.preview_calls == 0
    assert failed.preview_calls == 0

    track.enable()
    preview = track.capture_preview_image(max_width=640)

    assert preview.size == (640, 360)
    assert preview.getpixel((160, 180)) == (10, 20, 30)
    assert preview.getpixel((480, 180)) != (0, 0, 0)
    assert good.preview_calls == 1
    assert failed.preview_calls == 1


def test_remote_desktop_preview_captures_each_outbound_frame():
    """Composite consumers must see desktop changes on successive frames."""
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    calls = []
    colors = [(20, 40, 60), (90, 110, 130)]

    track = RemoteDesktopVideoStreamTrack(fps=1.0)
    track._capture_image = lambda: calls.append(True) or video_call_manager.Image.new(
        "RGB", (320, 180), colors[min(len(calls) - 1, len(colors) - 1)]
    )
    track.enable()

    assert track.capture_preview_image(max_width=320).getpixel((0, 0)) == (20, 40, 60)
    assert len(calls) == 1

    # The second composite/WebRTC frame must represent the current desktop,
    # not the first screenshot retained in the preview cache.
    assert track.capture_preview_image(max_width=320).getpixel((0, 0)) == (90, 110, 130)
    assert len(calls) == 2


def test_composite_remote_desktop_recv_delivers_changed_frames():
    """The stitched outbound track must forward fresh screen captures."""
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    colors = [(25, 45, 65), (95, 115, 135)]
    calls = []
    desktop = RemoteDesktopVideoStreamTrack(fps=30.0, max_width=320)
    desktop._capture_image = lambda: calls.append(True) or video_call_manager.Image.new(
        "RGB", (320, 180), colors[min(len(calls) - 1, len(colors) - 1)]
    )
    track = video_call_manager.CompositeVideoStreamTrack(
        [("remote_desktop", desktop)],
        fps=30.0,
        max_width=320,
    )
    track.enable()

    async def run() -> None:
        first = await asyncio.wait_for(track.recv(), timeout=5.0)
        second = await asyncio.wait_for(track.recv(), timeout=5.0)
        assert first.to_image().getpixel((0, 0)) == (25, 45, 65)
        assert second.to_image().getpixel((0, 0)) == (95, 115, 135)

    asyncio.run(run())
    assert len(calls) == 2


def test_remote_desktop_frames_use_the_configured_screen_rate():
    """RTP timestamps must match the capture cadence, not aiortc's 30 fps default."""
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    track = RemoteDesktopVideoStreamTrack(fps=8.0, max_width=320)
    track._capture_image = lambda: video_call_manager.Image.new("RGB", (320, 180), (20, 40, 60))
    track.enable()

    async def run() -> tuple[int, int]:
        first = await asyncio.wait_for(track.recv(), timeout=5.0)
        second = await asyncio.wait_for(track.recv(), timeout=5.0)
        assert first.time_base == video_call_manager.Fraction(1, 90000)
        assert second.time_base == video_call_manager.Fraction(1, 90000)
        return first.pts, second.pts

    first_pts, second_pts = asyncio.run(run())
    assert second_pts - first_pts == 11250


def test_remote_desktop_inactive_frame_uses_the_selected_capture_profile():
    """The encoder must not learn a lower stale resolution before video starts."""
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    track = RemoteDesktopVideoStreamTrack(max_width=2560)

    assert track.output_size == (2560, 1440)
    assert track._disabled_image().size == (2560, 1440)


def test_remote_desktop_capture_pacing_accounts_for_capture_time(monkeypatch):
    """A slow capture must use only the remaining frame interval as delay."""
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    now = [0.0]
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)
        now[0] += delay

    def capture_image():
        now[0] += 0.06
        return video_call_manager.Image.new("RGB", (320, 180), (20, 40, 60))

    monkeypatch.setattr(video_call_manager.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(video_call_manager.asyncio, "sleep", fake_sleep)
    track = RemoteDesktopVideoStreamTrack(fps=10.0, max_width=320)
    track._capture_image = capture_image
    track.enable()

    async def run() -> None:
        await track.recv()
        await track.recv()

    asyncio.run(run())
    assert delays == [pytest.approx(0.04)]


def test_composite_video_hides_webcam_pane_without_live_frames():
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    class DesktopTrack:
        def capture_preview_image(self, *, max_width):
            return video_call_manager.Image.new("RGB", (64, 36), (10, 20, 30))

    class DeadCameraTrack:
        has_live_content = False

        def capture_preview_image(self, *, max_width):
            raise AssertionError("hidden camera pane must not be rendered")

    track = video_call_manager.CompositeVideoStreamTrack(
        [("remote_desktop", DesktopTrack()), ("camera", DeadCameraTrack())],
        max_width=1280,
    )
    track.enable()

    preview = track.capture_preview_image(max_width=640)
    assert preview.size == (640, 360)
    # The screen fills the full canvas once the dead webcam pane is hidden.
    assert preview.getpixel((160, 180)) == (10, 20, 30)
    assert preview.getpixel((480, 180)) == (10, 20, 30)

    async def run() -> None:
        frame = await asyncio.wait_for(track.recv(), timeout=5.0)
        image = frame.to_image()
        assert image.getpixel((image.width // 4, image.height // 2)) == (10, 20, 30)
        assert image.getpixel((image.width * 3 // 4, image.height // 2)) == (10, 20, 30)

    asyncio.run(run())


def test_composite_video_policy_keeps_remote_desktop_disabled_while_camera_fills_frame():
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    class PreviewTrack:
        def __init__(self, color):
            self.color = color
            self.enabled = False

        def enable(self):
            self.enabled = True

        def disable(self):
            self.enabled = False

        def capture_preview_image(self, *, max_width):
            return video_call_manager.Image.new("RGB", (64, 36), self.color)

    desktop = PreviewTrack((180, 20, 20))
    camera = PreviewTrack((20, 180, 20))
    track = video_call_manager.CompositeVideoStreamTrack(
        [("remote_desktop", desktop), ("camera", camera)],
        max_width=1280,
    )
    track.enable()
    track.set_remote_desktop_enabled(False)
    track.enable()

    assert desktop.enabled is False
    assert camera.enabled is True
    assert [name for name, _source in track._live_sources()] == ["camera"]
    preview = track.capture_preview_image(max_width=640)
    assert preview.getpixel((160, 180)) == (20, 180, 20)
    assert preview.getpixel((480, 180)) == (20, 180, 20)


def test_remote_desktop_policy_blocks_later_video_enable_until_reauthorized():
    track = RemoteDesktopVideoStreamTrack(max_width=320)
    track.set_remote_desktop_enabled(False)
    track.enable()

    assert track.is_enabled is False

    track.set_remote_desktop_enabled(True)
    assert track.is_enabled is True


def test_local_audio_tracks_share_enumerate_refresh_and_teardown_portaudio(monkeypatch):
    class FakeAudioState:
        def __init__(self):
            self.lock = threading.RLock()
            self.events = []
            self.instance_count = 0
            self.terminate_count = 0
            self.open_counts = {}
            self.native_call_active = False
            self.fail_loopback = threading.Event()
            self.block_stop = None
            self.stop_started = threading.Event()
            self.allow_stop = threading.Event()

        @contextlib.contextmanager
        def native_call(self):
            with self.lock:
                assert self.native_call_active is False
                self.native_call_active = True
            try:
                time.sleep(0.001)
                yield
            finally:
                with self.lock:
                    self.native_call_active = False

        def record(self, event):
            with self.lock:
                self.events.append(event)

    state = FakeAudioState()

    class FakeStream:
        def __init__(self, owner, device_index, channels, rate):
            self.owner = owner
            self.device_index = device_index
            self.channels = channels
            self.rate = rate
            self.closed = False

        def get_read_available(self):
            with state.native_call():
                assert self.owner.terminated is False
                return int(self.rate * 0.02)

        def read(self, frame_count, exception_on_overflow=False):
            with state.native_call():
                assert exception_on_overflow is False
                assert self.owner.terminated is False
                is_reconnected_loopback = (
                    self.owner.generation > 1
                    and "loopback" in self.owner.devices[self.device_index]["name"].lower()
                )
                if is_reconnected_loopback and state.fail_loopback.is_set():
                    state.fail_loopback.clear()
                    raise OSError("synthetic loopback device unplugged")
                return b"\x01\x00" * frame_count * self.channels

        def stop_stream(self):
            with state.native_call():
                state.record(("stop", self.owner.generation, self.device_index))
                if state.block_stop == (self.owner.generation, self.device_index):
                    state.stop_started.set()
                    assert state.allow_stop.wait(timeout=2.0)

        def close(self):
            with state.native_call():
                if not self.closed:
                    self.closed = True
                    state.record(("close", self.owner.generation, self.device_index))

    class FakePyAudio:
        def __init__(self):
            state.instance_count += 1
            self.generation = state.instance_count
            self.terminated = False
            state.record(("initialize", self.generation))

        @property
        def devices(self):
            if self.generation == 1:
                return [
                    {"name": "Synthetic Microphone", "hostApi": 0, "maxInputChannels": 1, "defaultSampleRate": 48000},
                    {"name": "Synthetic Speakers (loopback)", "hostApi": 0, "maxInputChannels": 2, "defaultSampleRate": 48000},
                ]
            return [
                {"name": "Synthetic Output", "hostApi": 0, "maxInputChannels": 0, "defaultSampleRate": 48000},
                {"name": "Synthetic Microphone", "hostApi": 0, "maxInputChannels": 1, "defaultSampleRate": 44100},
                {"name": "Synthetic Speakers (loopback)", "hostApi": 0, "maxInputChannels": 2, "defaultSampleRate": 44100},
            ]

        def get_host_api_count(self):
            with state.native_call():
                return 1

        def get_host_api_info_by_index(self, index):
            with state.native_call():
                assert index == 0
                return {"name": "Windows WASAPI"}

        def get_device_count(self):
            with state.native_call():
                return len(self.devices)

        def get_device_info_by_index(self, index):
            with state.native_call():
                return dict(self.devices[index])

        def get_default_input_device_info(self):
            with state.native_call():
                return {"index": 0 if self.generation == 1 else 1}

        def get_default_wasapi_loopback(self):
            with state.native_call():
                index = 1 if self.generation == 1 else 2
                return {"index": index, **self.devices[index]}

        def get_loopback_device_info_generator(self):
            index = 1 if self.generation == 1 else 2
            return iter(({"index": index, **self.devices[index]},))

        def open(self, **kwargs):
            with state.native_call():
                device_index = kwargs["input_device_index"]
                device = self.devices[device_index]
                if kwargs["rate"] != device["defaultSampleRate"]:
                    raise OSError("synthetic device requires its native rate")
                if "loopback" in device["name"].lower() and kwargs["channels"] != 2:
                    raise OSError("synthetic loopback requires stereo")
                key = (self.generation, device_index)
                state.open_counts[key] = state.open_counts.get(key, 0) + 1
                state.record(("open", self.generation, device_index, kwargs["rate"], kwargs["channels"]))
                return FakeStream(self, device_index, kwargs["channels"], kwargs["rate"])

        def terminate(self):
            with state.native_call():
                self.terminated = True
                state.terminate_count += 1
                state.record(("terminate", self.generation))

    class FakePyAudioModule:
        PyAudio = FakePyAudio
        paInt16 = 8

    fake_pyaudio = FakePyAudioModule()
    monkeypatch.setattr(video_call_manager, "_PYAUDIO_RUNTIME", video_call_manager._PyAudioRuntime())
    monkeypatch.setattr(video_call_manager, "_load_pyaudio_module", lambda system_name=None: fake_pyaudio)
    monkeypatch.setattr(video_call_manager.platform, "system", lambda: "Windows")

    microphone = video_call_manager.LocalAudioInputTrack(device_index_or_name="Synthetic Microphone")
    loopback = video_call_manager.LocalAudioInputTrack(capture_loopback=True)

    def wait_until(predicate, timeout=4.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError(f"condition not met; events={state.events!r}")

    def has_open(*, after_generation=0, device_index):
        with state.lock:
            return any(
                generation > after_generation and index == device_index
                for generation, index in state.open_counts
            )

    def latest_open_generation():
        with state.lock:
            return max((generation for generation, _index in state.open_counts), default=0)

    try:
        microphone.enable()
        loopback.enable()
        wait_until(lambda: state.open_counts.get((1, 0)) == 1 and state.open_counts.get((1, 1)) == 1)
        assert microphone.capture_status() == {
            "running": True,
            "device_open": True,
            "loopback": False,
            "device_name": "Synthetic Microphone",
            "capture_rate": 48000,
            "channels": 1,
            "last_error": "",
        }

        with video_call_manager.shared_pyaudio_instance(fake_pyaudio) as borrowed:
            assert borrowed is microphone._pyaudio_instance is loopback._pyaudio_instance
            assert borrowed.get_device_count() == 2

        state.block_stop = (1, 0)
        disabling = threading.Thread(target=microphone.disable)
        disabling.start()
        assert state.stop_started.wait(timeout=2.0)
        microphone.enable()
        state.allow_stop.set()
        disabling.join(timeout=2.0)
        assert disabling.is_alive() is False
        wait_until(lambda: state.open_counts.get((1, 0)) == 2)

        refreshed_devices = video_call_manager.enumerate_pyaudio_input_devices(
            fake_pyaudio,
            system_name="Windows",
            refresh=True,
        )
        assert {device["selector"] for device in refreshed_devices} == {
            "Synthetic Microphone",
            "Synthetic Speakers (loopback)",
        }
        wait_until(
            lambda: has_open(after_generation=1, device_index=1)
            and has_open(after_generation=1, device_index=2)
        )
        refreshed_generation = latest_open_generation()

        state.fail_loopback.set()
        wait_until(
            lambda: state.instance_count > refreshed_generation
            and has_open(after_generation=refreshed_generation, device_index=1)
            and has_open(after_generation=refreshed_generation, device_index=2)
        )
        wait_until(lambda: microphone._audio_queue.qsize() > 0 and loopback._audio_queue.qsize() > 0)
        assert len(microphone._audio_queue.get_nowait()) == 1920
        assert len(loopback._audio_queue.get_nowait()) == 1920
        assert state.instance_count >= 3
        assert state.terminate_count == state.instance_count - 1

        terminations_before_partial_stop = state.terminate_count
        microphone.disable()
        assert microphone._thread is None
        assert state.terminate_count == terminations_before_partial_stop
    finally:
        microphone.disable()
        loopback.disable()

    assert loopback._thread is None
    assert loopback.capture_status()["device_open"] is False
    assert loopback.capture_status()["running"] is False
    assert state.terminate_count == state.instance_count
    generations_with_streams = {event[1] for event in state.events if event[0] == "open"}
    for generation in generations_with_streams:
        terminate_index = state.events.index(("terminate", generation))
        close_indices = [
            index
            for index, event in enumerate(state.events)
            if event[0] == "close" and event[1] == generation
        ]
        assert close_indices and max(close_indices) < terminate_index


def test_pyaudio_runtime_quarantines_failed_stream_close_without_reinitializing():
    created = []
    terminated = []

    class FakePyAudio:
        def __init__(self):
            created.append(self)

        def terminate(self):
            terminated.append(self)

    class FakeModule:
        PyAudio = FakePyAudio

    class FailedCloseStream:
        def stop_stream(self):
            return None

        def close(self):
            raise OSError("synthetic close failure")

    runtime = video_call_manager._PyAudioRuntime()
    instance = runtime.acquire(FakeModule())

    assert runtime.close_stream(FailedCloseStream()) is False
    runtime.quarantine(instance, "synthetic close failure")
    runtime.release(instance)

    with pytest.raises(RuntimeError, match="quarantined until AutoYou restarts"):
        runtime.acquire(FakeModule())
    assert len(created) == 1
    assert terminated == []


def test_pyaudio_runtime_quarantines_terminate_failure_without_reinitializing():
    created = []

    class FakePyAudio:
        def __init__(self):
            created.append(self)

        def terminate(self):
            raise OSError("synthetic terminate failure")

    class FakeModule:
        PyAudio = FakePyAudio

    runtime = video_call_manager._PyAudioRuntime()
    instance = runtime.acquire(FakeModule())
    runtime.release(instance)

    with pytest.raises(RuntimeError, match="synthetic terminate failure"):
        runtime.acquire(FakeModule())
    assert len(created) == 1


def test_pyaudio_runtime_state_remains_responsive_during_blocked_stream_close():
    close_started = threading.Event()
    allow_close = threading.Event()

    class FakePyAudio:
        def terminate(self):
            return None

    class FakeModule:
        PyAudio = FakePyAudio

    class BlockingStream:
        def stop_stream(self):
            close_started.set()
            assert allow_close.wait(timeout=2.0)

        def close(self):
            return None

    runtime = video_call_manager._PyAudioRuntime()
    instance = runtime.acquire(FakeModule())
    closing = threading.Thread(target=runtime.close_stream, args=(BlockingStream(),))
    closing.start()
    assert close_started.wait(timeout=1.0)

    state_read_completed = threading.Event()

    def read_runtime_state():
        assert runtime.generation(instance) >= 1
        runtime.quarantine(instance, "synthetic blocked close")
        state_read_completed.set()

    reader = threading.Thread(target=read_runtime_state)
    reader.start()
    assert state_read_completed.wait(timeout=0.5)

    allow_close.set()
    closing.join(timeout=1.0)
    reader.join(timeout=1.0)
    runtime.release(instance)
    assert closing.is_alive() is False
    assert reader.is_alive() is False


def test_local_audio_standard_stop_clears_stale_queue(monkeypatch):
    track = video_call_manager.LocalAudioInputTrack()
    stop_calls = []
    monkeypatch.setattr(track, "_stop_capture", lambda: stop_calls.append(True))
    track._enabled.set()
    track._audio_queue.put_nowait(b"synthetic stale audio")

    track.stop()

    assert stop_calls == [True]
    assert track.is_enabled is False
    assert track._audio_queue.empty()
    assert track.readyState == "ended"


def test_enumerate_pyaudio_inputs_dedupes_windows_host_aliases(monkeypatch):
    host_apis = ["MME", "Windows DirectSound", "Windows WDM-KS", "Windows WASAPI"]
    devices = [
        {"name": "Studio Mic", "hostApi": 0, "maxInputChannels": 2, "defaultSampleRate": 44100},
        {"name": "Legacy Line In", "hostApi": 0, "maxInputChannels": 1, "defaultSampleRate": 44100},
        {"name": "Studio Mic", "hostApi": 1, "maxInputChannels": 1, "defaultSampleRate": 48000},
        {"name": "Studio Mic", "hostApi": 3, "maxInputChannels": 1, "defaultSampleRate": 48000},
        {"name": "USB Mic", "hostApi": 2, "maxInputChannels": 2, "defaultSampleRate": 48000},
        {"name": "Microsoft Sound Mapper - Input", "hostApi": 0, "maxInputChannels": 2, "defaultSampleRate": 44100},
        {"name": "Primary Sound Capture Driver", "hostApi": 1, "maxInputChannels": 2, "defaultSampleRate": 44100},
        {"name": "Speakers", "hostApi": 3, "maxInputChannels": 0, "defaultSampleRate": 48000},
    ]
    terminated = []

    class FakePyAudio:
        def get_host_api_count(self):
            return len(host_apis)

        def get_host_api_info_by_index(self, index):
            return {"name": host_apis[index]}

        def get_device_count(self):
            return len(devices)

        def get_device_info_by_index(self, index):
            return devices[index]

        def get_default_input_device_info(self):
            return {"index": 0}

        def terminate(self):
            terminated.append(True)

    class FakeModule:
        PyAudio = FakePyAudio

    monkeypatch.setattr(video_call_manager, "_PYAUDIO_RUNTIME", video_call_manager._PyAudioRuntime())
    result = video_call_manager.enumerate_pyaudio_input_devices(FakeModule(), system_name="Windows")

    assert result == [
        {
            "id": 3,
            "selector": "Studio Mic",
            "name": "Studio Mic",
            "host_api": "Windows WASAPI",
            "channels": 1,
            "sample_rate": 48000,
            "is_default": True,
        },
        {
            "id": 1,
            "selector": "Legacy Line In",
            "name": "Legacy Line In",
            "host_api": "MME",
            "channels": 1,
            "sample_rate": 44100,
            "is_default": False,
        },
        {
            "id": 4,
            "selector": "USB Mic",
            "name": "USB Mic",
            "host_api": "Windows WDM-KS",
            "channels": 2,
            "sample_rate": 48000,
            "is_default": False,
        },
    ]
    assert terminated == [True]


@pytest.mark.parametrize(
    ("system_name", "host_apis", "expected_host_api"),
    [
        ("Darwin", ["JACK", "Core Audio"], "Core Audio"),
        ("Linux", ["ALSA", "JACK", "PulseAudio"], "PulseAudio"),
    ],
)
def test_enumerate_pyaudio_inputs_prefers_platform_host_api(
    monkeypatch,
    system_name,
    host_apis,
    expected_host_api,
):
    devices = [
        {"name": "Desk Mic", "hostApi": 0, "maxInputChannels": 1, "defaultSampleRate": 44100},
        {
            "name": "Desk Mic",
            "hostApi": len(host_apis) - 1,
            "maxInputChannels": 2,
            "defaultSampleRate": 48000,
        },
    ]

    class FakePyAudio:
        def get_host_api_count(self):
            return len(host_apis)

        def get_host_api_info_by_index(self, index):
            return {"name": host_apis[index]}

        def get_device_count(self):
            return len(devices)

        def get_device_info_by_index(self, index):
            return devices[index]

        def get_default_input_device_info(self):
            return {"index": 0}

        def terminate(self):
            pass

    class FakeModule:
        PyAudio = FakePyAudio

    monkeypatch.setattr(video_call_manager, "_PYAUDIO_RUNTIME", video_call_manager._PyAudioRuntime())
    [device] = video_call_manager.enumerate_pyaudio_input_devices(FakeModule(), system_name=system_name)

    assert device["host_api"] == expected_host_api
    assert device["selector"] == "Desk Mic"
    assert device["is_default"] is True


def test_remote_desktop_capture_normalizes_even_h264_dimensions(monkeypatch):
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    class FakeScreenShot:
        size = (452, 192)
        bgra = bytes((30, 20, 10, 0)) * (size[0] * size[1])

    class FakeMssContext:
        monitors = [{"left": 0, "top": 0, "width": 452, "height": 192}]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def grab(self, monitor):
            return FakeScreenShot()

    class FakeMssModule:
        @staticmethod
        def mss():
            return FakeMssContext()

    monkeypatch.setitem(sys.modules, "mss", FakeMssModule)

    track = RemoteDesktopVideoStreamTrack(max_width=320)
    image = track._capture_image()

    assert image.mode == "RGB"
    assert image.size == (320, 134)
    assert image.width % 2 == 0
    assert image.height % 2 == 0


def test_remote_desktop_webrtc_capture_falls_back_when_mss_frame_is_blank(monkeypatch):
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    fallback_calls = []

    monkeypatch.setattr(
        video_call_manager,
        "_capture_mss_desktop_image",
        lambda monitor_id=0: video_call_manager.Image.new("RGB", (96, 64), (0, 0, 0)),
    )

    def fake_imagegrab_capture(monitor_id=0):
        fallback_calls.append(monitor_id)
        return video_call_manager.Image.new("RGB", (96, 64), (80, 50, 30))

    monkeypatch.setattr(video_call_manager, "_capture_imagegrab_desktop_image", fake_imagegrab_capture)

    track = RemoteDesktopVideoStreamTrack(max_width=320, monitor_id=1)
    image = track._capture_image()

    assert image.getpixel((0, 0)) == (80, 50, 30)
    assert fallback_calls == [1]


def test_remote_desktop_webrtc_capture_does_not_switch_from_virtual_monitor(monkeypatch):
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    grabbed_monitors = []

    class FakeScreenShot:
        def __init__(self, size, color):
            self.size = size
            self.bgra = bytes((color[2], color[1], color[0], 0)) * (size[0] * size[1])

    class FakeMssContext:
        monitors = [
            {"left": 0, "top": 0, "width": 300, "height": 200},
            {"left": 0, "top": 0, "width": 160, "height": 90},
        ]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def grab(self, monitor):
            grabbed_monitors.append(dict(monitor))
            if monitor["width"] == 300:
                return FakeScreenShot((monitor["width"], monitor["height"]), (0, 0, 0))
            return FakeScreenShot((monitor["width"], monitor["height"]), (40, 80, 120))

    class FakeMssModule:
        @staticmethod
        def mss():
            return FakeMssContext()

    monkeypatch.setitem(sys.modules, "mss", FakeMssModule)
    monkeypatch.setattr(video_call_manager, "_capture_imagegrab_desktop_image", lambda monitor_id=0: None)

    track = RemoteDesktopVideoStreamTrack(max_width=320, monitor_id=0)
    image = track._capture_image()

    assert [monitor["width"] for monitor in grabbed_monitors] == [300]
    assert image.size == (320, 180)
    assert image.getpixel((0, 0)) == (15, 23, 42)


def test_remote_desktop_webrtc_capture_uses_placeholder_when_initial_capture_is_blank(monkeypatch):
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    monkeypatch.setattr(
        video_call_manager,
        "_capture_mss_desktop_image",
        lambda monitor_id=0: video_call_manager.Image.new("RGB", (96, 64), (0, 0, 0)),
    )
    monkeypatch.setattr(video_call_manager, "_capture_imagegrab_desktop_image", lambda monitor_id=0: None)

    track = RemoteDesktopVideoStreamTrack(max_width=320, monitor_id=1)
    image = track._capture_image()

    assert image.size == (320, 180)
    assert image.getpixel((0, 0)) == (15, 23, 42)
    assert video_call_manager._is_probably_blank_desktop_capture(image) is False


def test_remote_desktop_blank_capture_detail_includes_windows_session_state(monkeypatch):
    monkeypatch.setattr(video_call_manager, "_iter_desktop_capture_monitor_ids", lambda monitor_id=0: [0, 1])
    monkeypatch.setattr(
        video_call_manager,
        "_get_windows_desktop_session_status",
        lambda: {
            "process_session_id": 1,
            "active_console_session_id": 3,
            "state": "disconnected",
            "active": False,
        },
    )

    detail = video_call_manager._describe_desktop_capture_blank_frame(0)

    assert "monitors 0, 1" in detail
    assert "Windows session 1 is disconnected" in detail
    assert "Active console session is 3" in detail


def test_remote_desktop_webrtc_capture_does_not_reuse_last_good_frame_after_blank(monkeypatch):
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    frames = iter(
        [
            video_call_manager.Image.new("RGB", (96, 64), (30, 70, 120)),
            video_call_manager.Image.new("RGB", (96, 64), (0, 0, 0)),
        ]
    )
    monkeypatch.setattr(video_call_manager, "_capture_mss_desktop_image", lambda monitor_id=0: next(frames))
    monkeypatch.setattr(video_call_manager, "_capture_imagegrab_desktop_image", lambda monitor_id=0: None)

    track = RemoteDesktopVideoStreamTrack(max_width=320)
    first = track._capture_image()
    second = track._capture_image()

    assert first.getpixel((0, 0)) == (30, 70, 120)
    assert second.getpixel((0, 0)) == (15, 23, 42)


def test_camera_capture_never_falls_back_to_another_device(monkeypatch):
    calls = []

    class FakeCapture:
        def isOpened(self):
            return False

        def release(self):
            pass

    class FakeCv2:
        def VideoCapture(self, *args):
            calls.append(args[0])
            return FakeCapture()

    monkeypatch.setattr(video_call_manager.platform, "system", lambda: "Linux")
    track = video_call_manager.CameraVideoStreamTrack(device_index=7)

    assert track._open_camera(FakeCv2()) is None
    assert calls == [7]


def test_remote_desktop_webrtc_capture_uses_configured_monitor_id(monkeypatch):
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    grabbed_monitors = []

    class FakeScreenShot:
        def __init__(self, size, color):
            self.size = size
            self.bgra = bytes((color[2], color[1], color[0], 0)) * (size[0] * size[1])

    class FakeMssContext:
        monitors = [
            {"left": 0, "top": 0, "width": 300, "height": 200},
            {"left": 10, "top": 20, "width": 160, "height": 90},
        ]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def grab(self, monitor):
            grabbed_monitors.append(dict(monitor))
            return FakeScreenShot((monitor["width"], monitor["height"]), (40, 80, 120))

    class FakeMssModule:
        @staticmethod
        def mss():
            return FakeMssContext()

    monkeypatch.setitem(sys.modules, "mss", FakeMssModule)

    track = RemoteDesktopVideoStreamTrack(max_width=320, monitor_id=1)
    image = track._capture_image()

    assert grabbed_monitors[0] == {"left": 10, "top": 20, "width": 160, "height": 90}
    assert image.size == (160, 90)


def test_outbound_video_telemetry_records_sanitized_sender_warning():
    video_call_manager.OUTBOUND_VIDEO_TELEMETRY.reset()
    video_call_manager.install_outbound_video_telemetry_logging()

    logging.getLogger("aiortc.rtcrtpsender").warning(
        "RTCRtpsender(video) Traceback\n"
        "  File \"C:\\Users\\example\\AutoYou\\.venv\\Lib\\site-packages\\aiortc\\rtcrtpsender.py\", line 371\n"
        "av.error.ExternalError: [Errno 542398533] Generic error in an external library: "
        "'avcodec_open2(libx264)'"
    )

    status = video_call_manager.outbound_video_telemetry_status()
    assert status["error_count"] == 1
    assert status["latest_error"]["stage"] == "rtp_encode"
    assert status["latest_error"]["source"] == "negotiated"
    assert "avcodec_open2(libx264)" in status["latest_error"]["detail"]
    assert "Users" not in status["latest_error"]["detail"]


def test_outbound_video_telemetry_replaces_stale_reload_handler():
    sender_logger = logging.getLogger("aiortc.rtcrtpsender")
    existing_handlers = list(sender_logger.handlers)
    stale_handler = logging.NullHandler()
    stale_handler._autoyou_outbound_video_telemetry = True
    sender_logger.handlers[:] = [stale_handler]
    try:
        video_call_manager.OUTBOUND_VIDEO_TELEMETRY.reset()

        assert video_call_manager.install_outbound_video_telemetry_logging() is True
        assert stale_handler not in sender_logger.handlers

        sender_logger.warning("RTCRtpSender(video) avcodec_open2(libx264)")
        assert video_call_manager.outbound_video_telemetry_status()["error_count"] == 1
    finally:
        sender_logger.handlers[:] = existing_handlers


def test_latest_video_frame_registry_alias_and_clear():
    registry = LatestVideoFrameRegistry()

    first = registry.publish_jpeg(
        session_id="session-a",
        jpeg_bytes=b"jpeg-a",
        width=640,
        height=360,
    )

    assert first.sequence == 1
    assert registry.latest("session-a") == first
    assert registry.status()["active"] is True

    registry.alias_session("session-a", "session-b")
    alias = registry.latest("session-b")
    assert alias is not None
    assert alias.session_id == "session-b"
    assert alias.sequence == first.sequence
    assert alias.jpeg_bytes == first.jpeg_bytes
    assert registry.status()["latest_session_id"] == "session-b"

    registry.clear_session("session-a")
    assert registry.latest("session-b") == alias

    registry.clear_session("session-b")
    assert registry.latest() is None
    assert registry.status()["active"] is False


def test_realtime_video_input_registry_publishes_api_frames():
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    image = video_call_manager.Image.new("RGB", (20, 12), (80, 90, 100))
    buffer = BytesIO()
    image.save(buffer, format="JPEG")

    registry = video_call_manager.RealtimeVideoInputRegistry()
    frame = registry.publish_jpeg(
        source_id="camera-feed",
        jpeg_bytes=buffer.getvalue(),
        source="test",
    )

    assert frame.source_id == "camera-feed"
    assert frame.sequence == 1
    assert frame.width == 20
    assert frame.height == 12
    assert frame.source == "test"
    assert registry.latest("camera-feed") == frame

    status = registry.status("camera-feed")
    assert status["active"] is True
    assert status["source_id"] == "camera-feed"
    assert status["sequence"] == 1


def test_video_file_playback_registry_tracks_play_pause_and_loop(tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"synthetic video fixture")
    registry = video_call_manager.VideoFilePlaybackRegistry()

    configured = registry.configure(
        source_id="file-feed",
        file_path=str(video_path),
        loop=True,
        restart=True,
    )

    assert configured["source_id"] == "file-feed"
    assert configured["file_path"] == str(video_path)
    assert configured["file_exists"] is True
    assert configured["configured"] is True
    assert configured["loop"] is True
    assert configured["playing"] is True
    assert configured["restart_counter"] == 1

    paused = registry.pause(source_id="file-feed")
    assert paused["paused"] is True
    assert paused["playing"] is False

    resumed = registry.play(source_id="file-feed", restart=False)
    assert resumed["paused"] is False
    assert resumed["playing"] is True
    assert resumed["restart_counter"] == 1

    restarted = registry.play(source_id="file-feed", restart=True)
    assert restarted["restart_counter"] == 2


def test_incoming_video_sink_records_received_frames(tmp_path):
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    class FakeFrame:
        def to_image(self):
            return video_call_manager.Image.new("RGB", (32, 18), (10, 20, 30))

    class FakeTrack:
        def __init__(self):
            self.sent = False
            self.stopped = False

        async def recv(self):
            if self.sent:
                raise video_call_manager.MediaStreamError()
            self.sent = True
            return FakeFrame()

        def stop(self):
            self.stopped = True

    async def run() -> IncomingVideoTrackSink:
        registry = LatestVideoFrameRegistry()
        track = FakeTrack()
        sink = IncomingVideoTrackSink(
            track,
            session_id="session/one",
            registry=registry,
            recording_enabled=True,
            recording_dir=str(tmp_path),
            recording_mode="video",
            max_fps=60,
        )
        await sink.start()
        assert registry.latest("session/one") is not None
        await sink.stop()
        assert track.stopped is True
        return sink

    sink = asyncio.run(run())
    recording_path = Path(sink.recording_path or "")
    assert recording_path.is_dir()
    manifest_entries = [
        json.loads(line)
        for line in (recording_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert manifest_entries[0]["width"] == 32
    assert manifest_entries[0]["height"] == 18
    if video_call_manager.INBOUND_VIDEO_RECORDING_FORMAT == "mp4_video":
        assert (recording_path / "video.mp4").is_file()
        assert sink.recording_format == "mp4_video"
        assert manifest_entries[0]["format"] == "mp4_video"
        assert manifest_entries[0]["file"] == "video.mp4"
    else:
        frame_path = recording_path / "image-000001.jpg"
        assert frame_path.is_file()
        assert manifest_entries[0]["format"] == "jpeg_image_fallback"
        assert manifest_entries[0]["file"] == "image-000001.jpg"


def test_incoming_video_sink_default_mp4_recording_uses_30fps(tmp_path):
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")
    if video_call_manager.INBOUND_VIDEO_RECORDING_FORMAT != "mp4_video" or video_call_manager._av is None:
        pytest.skip("PyAV MP4 recording is not available")

    image = video_call_manager.Image.new("RGB", (32, 18), (10, 20, 30))
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    jpeg_bytes = buffer.getvalue()
    sink = IncomingVideoTrackSink(
        object(),
        session_id="session/default-fps",
        recording_enabled=True,
        recording_dir=str(tmp_path),
        recording_mode="video",
    )

    sink._record_frame(jpeg_bytes, 32, 18, image)
    recording_path = Path(sink.recording_path or "")
    assert not (recording_path / "video.mp4").exists()
    assert sink._recording_video_temp_path == recording_path / ".video.mp4.partial"
    sink._close_recording()

    video_path = recording_path / "video.mp4"
    assert video_path.is_file()
    assert not (recording_path / ".video.mp4.partial").exists()
    video_bytes = video_path.read_bytes()
    assert video_bytes.find(b"moov") < video_bytes.find(b"mdat")
    assert sink.max_fps == 30
    with video_call_manager._av.open(str(video_path)) as container:
        stream = next(stream for stream in container.streams if stream.type == "video")
        assert float(stream.average_rate) == 30.0

    manifest_entries = [
        json.loads(line)
        for line in (recording_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert manifest_entries[0]["fps"] == 30


def test_incoming_video_sink_sparse_mp4_preserves_timing_without_duplicates(tmp_path, monkeypatch):
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")
    if video_call_manager.INBOUND_VIDEO_RECORDING_FORMAT != "mp4_video" or video_call_manager._av is None:
        pytest.skip("PyAV MP4 recording is not available")

    image = video_call_manager.Image.new("RGB", (32, 18), (10, 20, 30))
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    jpeg_bytes = buffer.getvalue()
    times = iter([1000.0, 1000.0, 1005.0])
    # Patch this module's clock only. Patching the shared time module lets
    # logging or another thread consume the synthetic timestamps under load.
    recording_time = ModuleType("recording_time")
    recording_time.__dict__.update(vars(time))
    recording_time.time = lambda: next(times, 1005.0)
    # from __debug_provenance_r__ import via
    recording_time.localtime = lambda *args: time.struct_time((2026, 7, 2, 9, 0, 0, 3, 183, 1))
    monkeypatch.setattr(video_call_manager, "time", recording_time)
    sink = IncomingVideoTrackSink(
        object(),
        session_id="session/sparse-video",
        recording_enabled=True,
        recording_dir=str(tmp_path),
        recording_mode="video",
    )

    sink._record_frame(jpeg_bytes, 32, 18, image)
    sink._record_frame(jpeg_bytes, 32, 18, image)
    sink._close_recording()

    recording_path = Path(sink.recording_path or "")
    manifest_entries = [
        json.loads(line)
        for line in (recording_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [entry["encoded_frames"] for entry in manifest_entries] == [1, 2]
    assert [entry["frame_pts"] for entry in manifest_entries] == [0, 150]
    assert [entry["duplicated_frames"] for entry in manifest_entries] == [0, 0]
    assert all(entry["timing_mode"] == "received_frame_pts" for entry in manifest_entries)

    video_path = recording_path / "video.mp4"
    with video_call_manager._av.open(str(video_path)) as container:
        decoded_times = [round(float(frame.time), 3) for frame in container.decode(video=0)]
    assert decoded_times == [0.0, 5.0]


def test_incoming_video_sink_records_lightweight_images_on_interval(tmp_path, monkeypatch):
    if video_call_manager.Image is None:
        pytest.skip("Pillow is not installed")

    image = video_call_manager.Image.new("RGB", (24, 16), (50, 60, 70))
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    jpeg_bytes = buffer.getvalue()

    sink = IncomingVideoTrackSink(
        object(),
        session_id="session/images",
        recording_enabled=True,
        recording_dir=str(tmp_path),
        recording_mode="images",
        image_interval_seconds=2,
        max_fps=60,
    )
    gate_results = iter([True, False, True])
    monkeypatch.setattr(sink, "_should_record_image_snapshot_locked", lambda: next(gate_results))

    sink._record_frame(jpeg_bytes, 24, 16, image)
    sink._record_frame(jpeg_bytes, 24, 16, image)
    sink._record_frame(jpeg_bytes, 24, 16, image)

    recording_path = Path(sink.recording_path or "")
    manifest_entries = [
        json.loads(line)
        for line in (recording_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert sink.recording_format == "jpeg_images"
    assert [entry["file"] for entry in manifest_entries] == ["image-000001.jpg", "image-000002.jpg"]
    assert all(entry["format"] == "jpeg_image" for entry in manifest_entries)
    assert all(entry["image_interval_seconds"] == 2 for entry in manifest_entries)
    assert not (recording_path / "video.mp4").exists()
