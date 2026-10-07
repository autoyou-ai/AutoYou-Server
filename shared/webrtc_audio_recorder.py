# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-daab8f04e5709cc6e23a23ab

"""Streaming WAV batch recorder for inbound WebRTC audio.

The inbound WebRTC audio sink emits normalized PCM chunks (mono s16 at 16 kHz).
This helper writes those chunks directly to disk and rotates files on a bounded
duration so long-running background recording never accumulates audio in memory.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import threading
import uuid
from typing import Any, Dict, Optional
import wave

from shared.secure_storage import secure_storage_enabled, write_secure_file, write_secure_stream

__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-pay-daab8f04e5709cc6e23a23ab"


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
        native_media: bool = False,
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
        self._native_media = native_media
        self._native_cleanup_error: Exception | None = None
        self._native_closed = False
        self._native_stage = None
        self._native_file_id = uuid.uuid4().hex if native_media else ""
        if native_media:
            self.output_dir = self.output_dir.resolve()
            # This is the existing server sink's normalized PCM boundary, not
            # the 48 kHz Opus wire profile.
            if (self.sample_rate, self.channels, self.sample_width) != (16_000, 1, 2):
                raise ValueError("native recording requires normalized 16 kHz mono PCM16")
            self.filename_prefix = self.filename_prefix[:96] or "audio"
            test_root = os.environ.get("AUTOYOU_TEST_ROOT")
            if test_root:
                root = Path(test_root).resolve()
                requested = self.output_dir.resolve()
                if not requested.is_relative_to(root):
                    self.output_dir = root / "native-recordings" / hashlib.sha256(str(requested).encode()).hexdigest()[:16]

    @property
    def current_path(self) -> Optional[Path]:
        return self._current_path

    def prepare(self) -> None:
        """Admit native disk capacity before acknowledging recording capture."""
        if not self._native_media:
            return
        with self._lock:
            if self._native_cleanup_error is not None:
                raise self._native_cleanup_error
            if self._native_closed:
                raise RuntimeError("native recording has ended")
            if self._wav is None:
                self._open_next_file()

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return

        with self._lock:
            if self._native_media:
                if self._native_closed:
                    raise RuntimeError("native recording has ended")
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise ValueError("native recording requires bounded PCM bytes")
                count = chunk.nbytes if isinstance(chunk, memoryview) else len(chunk)
                if count > 64 * 1024:
                    raise ValueError("native recording chunk exceeds its bounded PCM input")
            if self._native_cleanup_error is not None:
                raise self._native_cleanup_error
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

                if self._native_media:
                    self._native_stage.admit(writable)
                self._wav.writeframesraw(view[offset : offset + writable])
                self._bytes_written += writable
                offset += writable

                if self._bytes_written >= self.max_batch_bytes:
                    self._close_current_file()

    def close(self) -> None:
        with self._lock:
            self._carry = b""
            if not self._native_media:
                self._close_current_file()
                return
            if self._native_cleanup_error is not None:
                raise self._native_cleanup_error
            if self._native_closed:
                return
            try:
                self._close_current_file()
                if self._native_stage is not None:
                    # A failed wave constructor can leave only the admitted
                    # file descriptor. No batch was committed in that case.
                    if self._native_stage.path is not None:
                        self._native_stage.join_handle()
                        self._native_stage.committed()
                    self._native_stage.close()
            except Exception as exc:
                self._native_cleanup_error = exc
                raise
            self._native_closed = True

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
                "native_reserved_bytes": self._native_stage._reserved if self._native_stage is not None else 0,
            }

    def _open_next_file(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._batch_index += 1
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe_session = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.session_id).strip("-")[:64] or "session"
        if self._native_media:
            safe_session = hashlib.sha256(self.session_id.encode()).hexdigest()[:16] + "-" + self._native_file_id
        path = self.output_dir / f"{self.filename_prefix}-{safe_session}-{timestamp}-{self._batch_index:04d}.wav"
        if self._native_media:
            from shared.iroh_recording_staging import NativeRecordingStage
            if self._native_stage is None:
                self._native_stage = NativeRecordingStage(self.output_dir)
            self._current_path = path
            wav_file = wave.open(self._native_stage.begin(), "wb")
        else:
            wav_file = wave.open(str(path), "wb")
        if self._native_media:
            # Transfer the handle before header initialization can fail.
            self._wav, self._current_path = wav_file, path
        # from __debug_provenance_e__ import pay
        wav_file.setnchannels(self.channels)
        wav_file.setsampwidth(self.sample_width)
        wav_file.setframerate(self.sample_rate)
        self._wav = wav_file
        self._current_path = path
        self._bytes_written = 0

    def _close_current_file(self) -> None:
        if self._native_media:
            if self._native_cleanup_error is not None:
                raise self._native_cleanup_error
            if self._wav is None:
                return
            try:
                self._wav.close()
                path = self._current_path
                self._native_stage.join_handle()
                scratch = self._native_stage.path
                if self._bytes_written == 0:
                    self._native_stage.committed()
                    self._wav = None; self._current_path = None
                    return
                with self._native_stage.retention_transaction():
                    if secure_storage_enabled() and path is not None:
                        from shared.secure_storage_stream import BLOCK_BYTES
                        digest, size = hashlib.sha256(), 0
                        with scratch.open("rb") as source:
                            while block := source.read(BLOCK_BYTES):
                                digest.update(block); size += len(block)
                        def blocks():
                            with scratch.open("rb") as source:
                                while block := source.read(BLOCK_BYTES):
                                    yield block
                        write_secure_stream(path,blocks(),expected_size=size,expected_sha256=digest.digest(),
                            temporary_directory=self._native_stage.directory)
                    else:
                        os.replace(scratch, path)
                    self._native_stage._committed()
            except Exception as exc:
                self._native_cleanup_error = exc
                raise
            self._wav = None
            self._bytes_written = 0
            return
        if self._wav is None:
            return
        try:
            self._wav.close()
            if secure_storage_enabled() and self._current_path is not None and self._current_path.is_file():
                write_secure_file(self._current_path, self._current_path.read_bytes())
        finally:
            self._wav = None
            self._bytes_written = 0
