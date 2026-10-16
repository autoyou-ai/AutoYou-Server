# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Source-owned local audio adapters for the native media carriage.

Speech receives only decoded source samples, at the existing 16 kHz mono
business boundary. Device concealment and playback drift correction never enter
STT, call attribution, or recordings. Cancellation joins actual resampler/track
work before the source can be replaced.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import av

from shared.iroh_media import _join_owned
from shared.iroh_media_codec import DecodedMedia
from shared.session_transport import SessionDenied


class IrohSpeechReceiver:
    def __init__(self, *, check_current: Callable[[], None],
                 on_chunk: Callable[[bytes], None], channels: int) -> None:
        if channels not in {1, 2}:
            raise ValueError("invalid speech source channel count")
        self.check_current, self.on_chunk, self.channels = check_current, on_chunk, channels
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        self._lock = asyncio.Lock()
        self._jobs: set[asyncio.Task] = set()
        self._closed = False
        self._cleanup: asyncio.Task | None = None

    def _check(self) -> None:
        if self._closed:
            raise SessionDenied("speech source is closed")
        self.check_current()

    def _convert(self, frames: tuple[Any, ...]) -> tuple[bytes, ...]:
        if len(frames) > 1:
            raise ValueError("speech source exceeded one decoded frame")
        chunks = []
        for frame in frames:
            if not isinstance(frame, av.AudioFrame) or frame.sample_rate != 48000 or \
                    not 0 < frame.samples <= 960 or len(frame.layout.channels) != self.channels:
                raise ValueError("speech source exceeded its decoded audio profile")
            # Incoming PTS belongs to the sender's clock. STT consumes source
            # samples, so reset timestamps on a copy without changing a frame
            # concurrently used by local playback.
            local = av.AudioFrame.from_ndarray(frame.to_ndarray(),
                format=frame.format.name, layout=frame.layout.name)
            local.sample_rate = frame.sample_rate
            for normalized in self._resampler.resample(local):
                raw = normalized.to_ndarray().tobytes()
                if len(raw) > 640 or len(raw) % 2:
                    raise ValueError("speech resampling exceeded 20 ms")
                if raw:
                    chunks.append(raw)
        return tuple(chunks)

    async def render(self, decoded: DecodedMedia) -> None:
        async with self._lock:
            self._check()
            # Concealment is intentionally not passed to _convert.
            job = asyncio.create_task(asyncio.to_thread(self._convert, decoded.frames),
                name="iroh-speech-resample")
            self._jobs.add(job)
            try:
                chunks = await _join_owned(job)
                self._check()
                for chunk in chunks:
                    self._check()
                    # The existing route expects the main event loop for room
                    # attribution, background policy and command scheduling.
                    self.on_chunk(chunk)
            finally:
                self._jobs.discard(job)

    async def close(self) -> None:
        if self._cleanup is None:
            self._closed = True
            self._cleanup = asyncio.create_task(self._close_owned(), name="iroh-speech-close")
        await _join_owned(self._cleanup)

    async def _close_owned(self) -> None:
        async with self._lock:
            if self._jobs:
                await asyncio.gather(*tuple(self._jobs), return_exceptions=True)
            self._resampler = None


class IrohAudioTrackCapture:
    """Borrow an AV track; the factory supplies its physical ownership boundary.

    Closing the local owner must unblock a device recv. A borrowed software
    playback lane completes its current recv without stopping its AudioManager.
    Track recv is shielded until its real work returns, including any executor
    calls made inside legacy local capture adapters.
    """
    def __init__(self, *, track: Any, close_owner: Callable[[], Awaitable[None]],
                 check_current: Callable[[], None]) -> None:
        self.track, self.close_owner, self.check_current = track, close_owner, check_current
        self._lock = asyncio.Lock()
        self._recv: asyncio.Task | None = None
        self._closed = False
        self._cleanup: asyncio.Task | None = None

    async def capture(self) -> Any:
        async with self._lock:
            if self._closed:
                raise SessionDenied("audio capture is closed")
            self.check_current()
            capture = getattr(self.track,"capture_native",None) or self.track.recv
            self._recv = asyncio.create_task(capture(), name="iroh-audio-track-recv")
            try:
                frame = await _join_owned(self._recv)
                if self._closed:
                    raise SessionDenied("audio capture ended during recv")
                self.check_current()
                return frame
            finally:
                self._recv = None

    async def close(self) -> None:
        if self._cleanup is None:
            self._closed = True
            self._cleanup = asyncio.create_task(self._close_owned(), name="iroh-audio-track-close")
        await _join_owned(self._cleanup)

    async def _close_owned(self) -> None:
        # Do not acquire the recv lock before unblocking a physical capture.
        try:
            await self.close_owner()
        finally:
            async with self._lock:
                if self._recv is not None:
                    await asyncio.gather(self._recv, return_exceptions=True)
                self.track = None
