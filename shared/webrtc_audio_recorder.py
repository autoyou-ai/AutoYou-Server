# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Streaming WAV batch recorder for inbound WebRTC audio.

The inbound WebRTC audio sink emits normalized PCM chunks (mono s16 at 16 kHz).
This helper writes those chunks directly to disk and rotates files on a bounded
duration so long-running background recording never accumulates audio in memory.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import threading
from typing import Any, Dict, Optional
import wave

from shared.secure_storage import secure_storage_enabled, write_secure_file


DEFAULT_AUDIO_RECORDING_BATCH_SECONDS = (60 * 60) - 1


def normalize_audio_recording_batch_seconds(
    raw_value: Any,
    default: int = DEFAULT_AUDIO_RECORDING_BATCH_SECONDS,
) -> int:
    try:
        value = int(float(str(raw_value).strip()))
    except Exception:
        value = int(default)
    return max(1, min(DEFAULT_AUDIO_RECORDING_BATCH_SECONDS, value))


class StreamingWavBatchRecorder:
    """Write raw PCM chunks to rotating WAV files."""

    def __init__(
        self,
        *,
        session_id: str,
        output_dir: str | Path,
        sample_rate: int = 16_000,
        channels: int = 1,
        sample_width: int = 2,
        max_batch_seconds: int = DEFAULT_AUDIO_RECORDING_BATCH_SECONDS,
        filename_prefix: str = "autoyou-silent-recording",
    ) -> None:
        self.session_id = str(session_id or "session").strip() or "session"
        self.output_dir = Path(output_dir).expanduser()
        self.sample_rate = max(1, int(sample_rate))
        self.channels = max(1, int(channels))
        self.sample_width = max(1, int(sample_width))
        self.frame_width = self.channels * self.sample_width
        self.max_batch_seconds = normalize_audio_recording_batch_seconds(max_batch_seconds)
        self.max_batch_bytes = self.sample_rate * self.frame_width * self.max_batch_seconds
        self.filename_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(filename_prefix or "audio")).strip("-")
        self._lock = threading.RLock()
        self._wav: Optional[wave.Wave_write] = None
        self._current_path: Optional[Path] = None
        self._bytes_written = 0
        self._batch_index = 0
        self._carry = b""

    @property
    def current_path(self) -> Optional[Path]:
        return self._current_path

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return

        with self._lock:
            data = self._carry + bytes(chunk)
            usable_length = len(data) - (len(data) % self.frame_width)
            if usable_length <= 0:
                self._carry = data
                return
            self._carry = data[usable_length:]
            view = memoryview(data[:usable_length])

            offset = 0
            while offset < len(view):
                if self._wav is None:
                    self._open_next_file()

                remaining = self.max_batch_bytes - self._bytes_written
                if remaining <= 0:
                    self._close_current_file()
                    continue

                writable = min(remaining, len(view) - offset)
                writable -= writable % self.frame_width
                if writable <= 0:
                    self._close_current_file()
                    continue

                self._wav.writeframesraw(view[offset : offset + writable])
                self._bytes_written += writable
                offset += writable

                if self._bytes_written >= self.max_batch_bytes:
                    self._close_current_file()

    def close(self) -> None:
        with self._lock:
            self._carry = b""
            self._close_current_file()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "session_id": self.session_id,
                "output_dir": str(self.output_dir),
                "current_path": str(self._current_path) if self._current_path else "",
                "batch_index": self._batch_index,
                "bytes_written": self._bytes_written,
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "sample_width": self.sample_width,
                "max_batch_seconds": self.max_batch_seconds,
            }

    def _open_next_file(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._batch_index += 1
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe_session = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.session_id).strip("-")[:64] or "session"
        path = self.output_dir / f"{self.filename_prefix}-{safe_session}-{timestamp}-{self._batch_index:04d}.wav"
        wav_file = wave.open(str(path), "wb")
        wav_file.setnchannels(self.channels)
        wav_file.setsampwidth(self.sample_width)
        wav_file.setframerate(self.sample_rate)
        self._wav = wav_file
        self._current_path = path
        self._bytes_written = 0

    def _close_current_file(self) -> None:
        if self._wav is None:
            return
        try:
            self._wav.close()
            if secure_storage_enabled() and self._current_path is not None and self._current_path.is_file():
                write_secure_file(self._current_path, self._current_path.read_bytes())
        finally:
            self._wav = None
            self._bytes_written = 0
