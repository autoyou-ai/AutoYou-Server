"""Local, opt-in playback of screen-session microphones. No speech processing."""

from __future__ import annotations

import array
import threading
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

    def configure(self, mode: str, selected: Iterable[str] = ()) -> None:
        if mode not in {"off", "all", "selected"}:
            raise ValueError("Choose off, all, or selected audio")
        with self._lock:
            self.mode = mode
            self.selected = set(selected)
            self._buffers.clear()
        if mode == "off":
            self.close()
        elif self._stop.is_set() or self._thread is None or not self._thread.is_alive():
            try:
                self._start()
            except Exception:
                with self._lock:
                    self.mode = "off"
                    self.selected.clear()
                raise

    def feed(self, session_id: str, pcm: bytes) -> None:
        with self._lock:
            if self.mode == "off" or self.mode == "selected" and session_id not in self.selected:
                return
            buffer = self._buffers.setdefault(session_id, bytearray())
            buffer.extend(pcm)
            if len(buffer) > FRAME_BYTES * 12:
                del buffer[:len(buffer) - FRAME_BYTES * 8]

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._buffers.pop(session_id, None)

    def forget(self, session_id: str) -> None:
        with self._lock:
            self._buffers.pop(session_id, None)
            self.selected.discard(session_id)

    def _start(self) -> None:
        from shared.video_call_manager import _PYAUDIO_RUNTIME, _load_pyaudio_module

        if self._stream is not None:
            self.close()
        module = _load_pyaudio_module()
        instance = _PYAUDIO_RUNTIME.acquire(module)
        try:
            try:
                with _PYAUDIO_RUNTIME.serialized():
                    device_rate = int(instance.get_default_output_device_info()["defaultSampleRate"])
            except Exception:
                device_rate = 48000
            last_error = None
            stream = None
            for rate in dict.fromkeys((16000, device_rate, 48000, 44100)):
                try:
                    if rate != 16000:
                        from av import AudioFrame, AudioResampler
                        resampler = AudioResampler(format="s16", layout="mono", rate=rate)
                    with _PYAUDIO_RUNTIME.serialized():
                        stream = instance.open(format=module.paInt16, channels=1, rate=rate,
                                               output=True, frames_per_buffer=rate // 50)
                    break
                except Exception as exc:
                    last_error = exc
            if stream is None:
                raise last_error or OSError("No computer audio output is available")
        except Exception:
            _PYAUDIO_RUNTIME.release(instance)
            raise
        self._instance = instance
        self._stream = stream
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
                    self._stream.write(pcm)
            except Exception:
                self._stop.set()
                with self._lock:
                    self.mode = "off"
                    self.selected.clear()

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None
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
