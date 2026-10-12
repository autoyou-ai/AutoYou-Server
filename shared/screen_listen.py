"""Local, opt-in playback of screen-session microphones. No speech processing."""

from __future__ import annotations

import array
import logging
import threading
import time
from typing import Iterable


FRAME_BYTES = 640  # 20 ms of mono, 16 kHz, signed 16-bit PCM.


def mix_pcm(chunks: Iterable[bytes]) -> bytes:
    tracks = []
    for chunk in chunks:
        if chunk:
            track = array.array("h")
            frame = chunk[:FRAME_BYTES]
            track.frombytes(frame[:len(frame) & ~1])
            tracks.append(track)
    if not tracks:
        return bytes(FRAME_BYTES)
    mixed = [0] * (FRAME_BYTES // 2)
    for track in tracks:
        for index, sample in enumerate(track[:len(mixed)]):
            mixed[index] += sample
    return array.array("h", (max(-32768, min(32767, value)) for value in mixed)).tobytes()


class ScreenListenMixer:
    """One local speaker stream; selected callers are mixed on its worker thread."""

    def __init__(self) -> None:
        self.mode = "off"
        self.selected: set[str] = set()
        self._buffers: dict[str, bytearray] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream = None
        self._instance = None
        self._resampler = None
        self._frame_type = None
        self._channels = 1
        self.output_device = ""
        self.error = ""
        self._heard_at: dict[str, float] = {}

    def snapshot(self) -> dict:
        with self._lock:
            return {"mode": self.mode, "selected": sorted(self.selected),
                    "output_device": self.output_device, "error": self.error,
                    "receiving": [sid for sid, at in self._heard_at.items()
                                  if time.monotonic() - at < 1.0]}

    def configure(self, mode: str, selected: Iterable[str] = ()) -> None:
        if mode not in {"off", "all", "selected"}:
            raise ValueError("Choose off, all, or selected audio")
        restart = mode != "off" and (self._stop.is_set() or self._thread is None or not self._thread.is_alive())
        if restart and self._stream is not None:
            self.close()
        with self._lock:
            self.mode = mode
            self.selected = set(selected)
            self._buffers.clear()
            self._heard_at.clear()
            self.error = ""
        if mode == "off":
            self.close()
        elif restart:
            try:
                self._start()
            except Exception as exc:
                with self._lock:
                    self.mode = "off"
                    self.selected.clear()
                    self.error = f"Computer speaker could not start: {exc}"[:200]
                raise

    def feed(self, session_id: str, pcm: bytes) -> None:
        with self._lock:
            if self.mode == "off" or self.mode == "selected" and session_id not in self.selected:
                return
            buffer = self._buffers.setdefault(session_id, bytearray())
            buffer.extend(pcm)
            if pcm:
                self._heard_at[session_id] = time.monotonic()
            if len(buffer) > FRAME_BYTES * 12:
                del buffer[:len(buffer) - FRAME_BYTES * 8]

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._buffers.pop(session_id, None)
            self._heard_at.pop(session_id, None)

    def forget(self, session_id: str) -> None:
        with self._lock:
            self._buffers.pop(session_id, None)
            self._heard_at.pop(session_id, None)
            self.selected.discard(session_id)

    def _start(self) -> None:
        from shared.video_call_manager import _PYAUDIO_RUNTIME, _load_pyaudio_module

        if self._thread is not None and self._thread.is_alive():
            raise OSError("Previous speaker playback is still stopping; try again")
        module = _load_pyaudio_module()
        instance = _PYAUDIO_RUNTIME.acquire(module)
        try:
            try:
                with _PYAUDIO_RUNTIME.serialized():
                    device = instance.get_default_output_device_info()
                    device_rate = int(device["defaultSampleRate"])
            except Exception:
                device = {}
                device_rate = 48000
            last_error = None
            stream = None
            for rate in dict.fromkeys((16000, device_rate, 48000, 44100)):
                for channels in (1, 2):
                    try:
                        if rate != 16000:
                            from av import AudioFrame, AudioResampler
                            resampler = AudioResampler(format="s16", layout="mono", rate=rate)
                        with _PYAUDIO_RUNTIME.serialized():
                            stream = instance.open(format=module.paInt16, channels=channels, rate=rate,
                                                   output=True, frames_per_buffer=rate // 50)
                        break
                    except Exception as exc:
                        last_error = exc
                if stream is not None:
                    break
            if stream is None:
                raise last_error or OSError("No computer audio output is available")
        except Exception:
            _PYAUDIO_RUNTIME.release(instance)
            raise
        self._instance = instance
        self._stream = stream
        self._channels = channels
        self.output_device = str(device.get("name") or "Default computer speaker")[:128]
        self._resampler = resampler if rate != 16000 else None
        self._frame_type = AudioFrame if rate != 16000 else None
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ScreenListen", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                ids = list(self._buffers) if self.mode == "all" else list(self.selected)
                frames = []
                for session_id in ids:
                    buffer = self._buffers.get(session_id)
                    if not buffer:
                        continue
                    frame = bytes(buffer[:FRAME_BYTES])
                    del buffer[:FRAME_BYTES]
                    if not buffer:
                        self._buffers.pop(session_id, None)
                    frames.append(frame.ljust(FRAME_BYTES, b"\0"))
            try:
                pcm = mix_pcm(frames)
                if self._resampler is not None:
                    frame = self._frame_type(format="s16", layout="mono", samples=FRAME_BYTES // 2)
                    frame.planes[0].update(pcm)
                    frame.sample_rate = 16000
                    pcm = b"".join(resampled.to_ndarray().tobytes()
                                   for resampled in self._resampler.resample(frame))
                if pcm:
                    if self._channels == 2:
                        samples = array.array("h")
                        samples.frombytes(pcm)
                        pcm = array.array("h", (value for sample in samples for value in (sample, sample))).tobytes()
                    self._stream.write(pcm)
            except Exception as exc:
                logging.getLogger(__name__).warning("Screen speaker playback stopped: %s", exc)
                self._stop.set()
                with self._lock:
                    self.mode = "off"
                    self.selected.clear()
                    self._heard_at.clear()
                    self.error = f"Computer speaker playback stopped: {exc}"[:200]

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self.mode = "off"
            self.selected.clear()
            self._buffers.clear()
            self._heard_at.clear()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = thread if thread is not None and thread.is_alive() else None
        if self._stream is not None:
            from shared.video_call_manager import _PYAUDIO_RUNTIME

            if thread is not None and thread.is_alive():
                _PYAUDIO_RUNTIME.quarantine(self._instance, "Listen output did not stop")
            else:
                try:
                    closed = _PYAUDIO_RUNTIME.close_stream(self._stream)
                except Exception:
                    closed = False
                if not closed:
                    _PYAUDIO_RUNTIME.quarantine(self._instance, "Listen output did not close")
            _PYAUDIO_RUNTIME.release(self._instance)
            self._stream = None
            self._instance = None
            self._resampler = None
            self._frame_type = None
