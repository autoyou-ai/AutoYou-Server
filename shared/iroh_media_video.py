# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Owned local capture descriptors consumed by native video negotiation.

The source group owns each descriptor before enable and each physical recv until
its executor work returns. Retirement fences admission immediately; decoder,
camera and composite children join before a process slot can be returned.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

import av

from shared.iroh_media import _join_owned
from shared.iroh_media_codec import CapturedMedia
from shared.session_transport import SessionDenied


MAX_CAPTURE_GROUPS = 8
MAX_FRAME_BYTES = 32 * 1024 * 1024
UPSTREAM_VIDEO_SOURCE_ID = 3
SERVER_VIDEO_SOURCE_ID = 4
_capacity_lock = threading.Lock()
_capacity: dict[str, list[Any]] = {}


class NativeVideoCapture:
    def __init__(self, *, factory: Callable, cfg: dict, check_current: Callable[[], None], fps: int = 30) -> None:
        if type(fps) is not int or not 1 <= fps <= 30:
            raise ValueError("native capture requires an admitted frame rate")
        self.factory, self.cfg, self.check_current = factory, cfg, check_current
        self._interval_us = (1_000_000 + fps - 1) // fps
        self._next_capture_us = 0
        self._stopped = asyncio.Event()
        self.owned: list[Any] = []
        self.track: Any = None
        self._closed = False
        self._lock = asyncio.Lock()
        self._preparing: asyncio.Task | None = None
        self._cleanup: asyncio.Task | None = None
        self._recv: asyncio.Task | None = None
        self._scope: str | None = None
        self._fenced: set[int] = set()
        self._fence_errors: list[BaseException] = []

    def _check(self) -> None:
        if self._closed:
            raise SessionDenied("native video capture is retired")
        self.check_current()

    def _admit(self) -> None:
        root = os.environ.get("AUTOYOU_TEST_ROOT")
        scope = str(Path(root).resolve()) if root else "production"
        with _capacity_lock:
            owners = _capacity.setdefault(scope, [])
            if any(owner._closed for owner in owners):
                raise SessionDenied("previous native video capture has not joined cleanup")
            if len(owners) >= MAX_CAPTURE_GROUPS:
                raise SessionDenied("native video capture process capacity is exhausted")
            owners.append(self)
            self._scope = scope

    async def ready(self) -> None:
        self._check()
        if self._preparing is None:
            self._admit()
            self._preparing = asyncio.create_task(self._prepare(), name="iroh-video-prepare")
        await _join_owned(self._preparing)
        self._check()

    async def _prepare(self) -> None:
        creating = asyncio.create_task(asyncio.to_thread(self.factory,
            cfg=self.cfg, capture_owner=self.owned), name="iroh-video-descriptors")
        self.track = await _join_owned(creating)
        self._check()
        if self.track is None or len(self.owned) > 5 or all(item is not self.track for item in self.owned):
            raise SessionDenied("native video factory did not transfer bounded ownership")
        if any(hasattr(track, "close_native") and getattr(track, "native_media", True) is not True for track in self.owned):
            raise SessionDenied("native video factory borrowed a legacy capture descriptor")
        enabling = asyncio.create_task(asyncio.to_thread(self.track.enable), name="iroh-video-enable")
        await _join_owned(enabling)
        for descriptor in self.owned:
            ready = getattr(descriptor, "ready_native", None)
            if ready is not None:
                self._check()
                job = asyncio.create_task(asyncio.to_thread(ready), name="iroh-video-device-ready")
                await _join_owned(job)
        self._check()

    async def capture(self) -> CapturedMedia:
        async with self._lock:
            self._check()
            if self._preparing is None or not self._preparing.done() or self.track is None:
                raise SessionDenied("native video capture is not prepared")
            remaining = self._next_capture_us - time.monotonic_ns() // 1000
            if remaining > 0:
                try:
                    await asyncio.wait_for(self._stopped.wait(), remaining / 1_000_000)
                except asyncio.TimeoutError:
                    pass
                self._check()
            started = time.monotonic_ns() // 1000
            self._recv = asyncio.create_task(self.track.recv(), name="iroh-video-recv")
            try:
                frame = await _join_owned(self._recv)
                self._check()
                # A delayed SDK read cannot create a catch-up burst. Preserve
                # the physical frame clock independently of this rate gate.
                self._next_capture_us = time.monotonic_ns() // 1000 + self._interval_us
                if not isinstance(frame, av.VideoFrame) or not 0 < frame.width <= 4096 or \
                        not 0 < frame.height <= 4096 or \
                        sum(plane.buffer_size for plane in frame.planes) > MAX_FRAME_BYTES:
                    raise ValueError("native capture exceeded its frame budget")
                captured = getattr(self.track, "_native_captured_at_us", 0) or started
                return CapturedMedia(frame, captured, current_check=self._current)
            finally:
                self._recv = None

    def _current(self) -> bool:
        try:
            self._check()
        except SessionDenied:
            return False
        return True

    def fence(self) -> None:
        self._closed = True
        self._stopped.set()
        for track in tuple(self.owned):
            if id(track) in self._fenced:
                continue
            self._fenced.add(id(track))
            fence = getattr(track, "fence_native", None) or getattr(track, "stop", None)
            if fence is not None:
                try:
                    fence()
                except BaseException as error:
                    self._fence_errors.append(error)

    async def close(self) -> None:
        if self._cleanup is None:
            self.fence()
            self._cleanup = asyncio.create_task(self._close_owned(), name="iroh-video-close")
        await _join_owned(self._cleanup)

    async def _close_owned(self) -> None:
        if self._preparing is not None:
            await asyncio.gather(self._preparing, return_exceptions=True)
        # A descriptor can arrive after the first synchronous fence.
        self.fence()
        async with self._lock:
            def close_tracks():
                errors = list(self._fence_errors)
                for track in reversed(self.owned):
                    try:
                        close = getattr(track, "close_native", None)
                        if close is not None:
                            close()
                        elif not hasattr(track, "sources"):
                            disable = getattr(track, "disable", None)
                            if disable is not None:
                                disable()
                    except BaseException as error:
                        errors.append(error)
                if errors:
                    raise RuntimeError("native video source cleanup failed") from errors[0]
            job = asyncio.create_task(asyncio.to_thread(close_tracks), name="iroh-video-physical-close")
            await _join_owned(job)
            self.owned.clear()
            self.track = None
            if self._scope is not None:
                with _capacity_lock:
                    owners = _capacity[self._scope]
                    owners.remove(self)
                    if not owners:
                        del _capacity[self._scope]
                    self._scope = None


class NativeVideoReceiver:
    """Publish bounded decoded camera previews into the existing agent registry."""
    def __init__(self, *, binding: Any, registry: Any, check_current: Callable[[], None],
                 on_close: Callable[[], None]) -> None:
        self.binding, self.registry = binding, registry
        self.check_current, self.on_close = check_current, on_close
        self._closed = False
        self._lock = asyncio.Lock()
        self._cleanup: asyncio.Task | None = None

    def _check(self) -> None:
        if self._closed:
            raise SessionDenied("native video receiver is retired")
        self.check_current()

    def _preview(self, decoded: Any) -> tuple[bytes, int, int]:
        if len(decoded.frames) != 1 or decoded.concealed:
            raise ValueError("native camera preview requires one decoded frame")
        frame = decoded.frames[0]
        if not isinstance(frame, av.VideoFrame) or not 0 < frame.width <= self.binding.width or \
                not 0 < frame.height <= self.binding.height or \
                frame.width * frame.height * 4 > MAX_FRAME_BYTES or \
                sum(plane.buffer_size for plane in frame.planes) > MAX_FRAME_BYTES:
            raise ValueError("native camera preview exceeded its source profile")
        image = frame.to_image().convert("RGB")
        image.thumbnail((1280, 1280))
        with BytesIO() as buffer:
            image.save(buffer, format="JPEG", quality=80)
            raw = buffer.getvalue()
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("native camera preview exceeded its encoded budget")
        return raw, image.width, image.height

    async def render(self, decoded: Any) -> None:
        async with self._lock:
            self._check()
            job = asyncio.create_task(asyncio.to_thread(self._preview, decoded), name="iroh-video-preview")
            raw, width, height = await _join_owned(job)
            self._check()
            self.registry.publish_jpeg(session_id=self.binding.session.transport_id, jpeg_bytes=raw,
                width=width, height=height, source="iroh_camera" if int(self.binding.kind) == 2 else "iroh_screen")

    async def close(self) -> None:
        if self._cleanup is None:
            self._closed = True
            self._cleanup = asyncio.create_task(self._close_owned(), name="iroh-video-receiver-close")
        await _join_owned(self._cleanup)

    async def _close_owned(self) -> None:
        async with self._lock:
            self.on_close()
