# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Bounded, physically joined recording at the existing normalized PCM boundary."""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Callable

from shared.iroh_media import _join_owned
from shared.session_transport import SessionDenied


class NativeRecording:
    _native_media = True
    # Recording persists 16 kHz mono PCM. Give batch rotation/protection a
    # bounded 30-second IO cushion (960,000 bytes), separate from playout.
    MAX_CHUNKS = 1500
    MAX_CHUNK_BYTES = 640

    def __init__(self, *, factory: Callable, check_current: Callable[[], None],
                 on_failure: Callable[[BaseException], None]) -> None:
        self.factory,self.check_current,self.on_failure = factory,check_current,on_failure
        self.recorder = None
        self._chunks = deque()
        self._wake = asyncio.Event()
        self._closed = False
        self._error = None
        self._notification_error = None
        self._failure_reported = False
        self._ready = asyncio.get_running_loop().create_future()
        self._ready.add_done_callback(lambda future: None if future.cancelled() else future.exception())
        self._worker = asyncio.create_task(self._run(),name="iroh-recording-writer")

    def _report_failure(self, error):
        if self._failure_reported:
            return
        self._failure_reported = True
        try:
            self.on_failure(error)
        except BaseException as exc:
            self._notification_error = exc

    async def ready(self):
        await asyncio.shield(self._ready)

    def write(self, chunk: bytes) -> None:
        if self._closed or self._error is not None:
            raise SessionDenied("native recording input ended")
        self.check_current()
        if not isinstance(chunk,bytes) or not 0 < len(chunk) <= self.MAX_CHUNK_BYTES or len(chunk)%2:
            raise SessionDenied("native recording input exceeds 20 ms mono PCM")
        if len(self._chunks) >= self.MAX_CHUNKS:
            error = RuntimeError("native recording writer capacity was exceeded")
            self._error = error
            self.fence()
            self._report_failure(error)
            raise error
        self._chunks.append(chunk)
        self._wake.set()

    def fence(self) -> None:
        self._closed = True
        self._chunks.clear()
        self._wake.set()

    async def close(self) -> None:
        self.fence()
        await _join_owned(self._worker)
        if self._error is not None:
            raise self._error

    async def _run(self):
        try:
            creating = asyncio.create_task(asyncio.to_thread(self.factory))
            try:
                self.recorder = await _join_owned(creating)
            except asyncio.CancelledError:
                if not creating.cancelled() and creating.exception() is None:
                    self.recorder = creating.result()
                raise
            prepare = getattr(self.recorder, "prepare", None)
            if callable(prepare) and not self._closed:
                await _join_owned(asyncio.create_task(asyncio.to_thread(prepare)))
            if not self._ready.done():
                self._ready.set_result(None)
            while not self._closed:
                await self._wake.wait()
                self._wake.clear()
                while self._chunks and not self._closed:
                    self.check_current()
                    chunk = self._chunks.popleft()
                    await _join_owned(asyncio.create_task(asyncio.to_thread(self.recorder.write,chunk)))
        except BaseException as exc:
            self._error = self._error or exc
            self.fence()
            if not self._ready.done():
                self._ready.set_exception(exc)
            if not isinstance(exc,asyncio.CancelledError):
                self._report_failure(exc)
        finally:
            if self.recorder is not None:
                try:
                    await _join_owned(asyncio.create_task(asyncio.to_thread(self.recorder.close)))
                except BaseException as exc:
                    self._error = self._error or exc
                    self._report_failure(exc)
