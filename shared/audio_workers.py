# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Bound and physically join the workers belonging to one native audio owner."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


class AudioWorkersClosed(RuntimeError):
    pass


class AudioWorkerOwner:
    def __init__(self, *, max_workers: int):
        if type(max_workers) is not int or max_workers < 1:
            raise ValueError("invalid audio worker capacity")
        self._capacity = max_workers
        self._lock = threading.Lock()
        self._threads: set[threading.Thread] = set()
        self.stopped = threading.Event()

    def start(self, target: Callable[..., Any], *, name: str, args=(), kwargs=None) -> threading.Thread:
        # Registration and Thread.start share the fence lock: close cannot miss
        # a worker that was admitted but not yet started. Completed handles stay
        # owned until is_alive proves they have physically exited.
        with self._lock:
            if self.stopped.is_set():
                raise AudioWorkersClosed("native audio workers are closed")
            self._threads = {thread for thread in self._threads if thread.is_alive()}
            if len(self._threads) >= self._capacity:
                raise RuntimeError("native audio worker capacity exhausted")
            thread = threading.Thread(target=target, args=args, kwargs=kwargs or {}, daemon=True, name=name)
            self._threads.add(thread)
            try:
                thread.start()
            except BaseException:
                self._threads.remove(thread)
                raise
            return thread

    def fence(self) -> None:
        with self._lock:
            self.stopped.set()

    def join(self, *, timeout: float) -> None:
        with self._lock:
            if not self.stopped.is_set():
                raise RuntimeError("audio workers must be fenced before joining")
            threads = tuple(self._threads)
        deadline = time.monotonic() + max(0.0, timeout)
        for thread in threads:
            if thread is threading.current_thread():
                raise RuntimeError("native audio worker cannot join its own owner")
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            self._threads = {thread for thread in self._threads if thread.is_alive()}
            if self._threads:
                # Retain the handles and the caller's resources on failed join.
                raise RuntimeError("native audio worker cleanup did not finish")
