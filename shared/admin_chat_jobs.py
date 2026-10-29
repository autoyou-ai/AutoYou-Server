# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Bounded, cancellable admin HTTP turns with per-conversation ordering."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

LOGGER = logging.getLogger(__name__)
TERMINAL = {"completed", "stopped", "failed"}


@dataclass
class _Job:
    request_id: str
    conversation: tuple[str, str]
    created: float
    status: str = "queued"
    response: dict[str, Any] | None = None
    error: str = ""
    stop_requested: bool = False
    task: asyncio.Task | None = None
    finished: float = 0.0


class AdminChatJobs:
    def __init__(self, *, limit: int = 64, retention: float = 300,
                 timeout: float = 600, stop_timeout: float = 10) -> None:
        self.limit = limit
        self.retention = retention
        self.timeout = timeout
        self.stop_timeout = stop_timeout
        self._jobs: dict[str, _Job] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        for key, job in list(self._jobs.items()):
            if job.status in TERMINAL and now - job.finished >= self.retention:
                del self._jobs[key]
        conversations = {job.conversation for job in self._jobs.values()}
        for key in list(self._locks):
            if key not in conversations:
                del self._locks[key]

    def start(self, user_id: str, session_id: str,
              handler: Callable[[], Awaitable[dict[str, Any]]], *, request_id: str = "") -> dict[str, Any]:
        self._prune()
        existing = self._jobs.get(request_id)
        if existing is not None:
            if existing.conversation != (user_id, session_id):
                raise ValueError("The request ID belongs to another conversation.")
            return self.snapshot(request_id)
        if len(self._jobs) >= self.limit:
            oldest = next((key for key, job in self._jobs.items() if job.status in TERMINAL), None)
            if oldest is not None:
                del self._jobs[oldest]
            else:
                raise RuntimeError("Too many active chat requests. Please try again shortly.")
        job = _Job(request_id or uuid.uuid4().hex, (user_id, session_id), time.monotonic())
        lock = self._locks.setdefault(job.conversation, asyncio.Lock())
        self._jobs[job.request_id] = job
        job.task = asyncio.create_task(self._run(job, lock, handler), name="AdminChatTurn")
        return self.snapshot(job.request_id)

    async def _run(self, job: _Job, lock: asyncio.Lock,
                   handler: Callable[[], Awaitable[dict[str, Any]]]) -> None:
        try:
            async with lock:
                if job.stop_requested:
                    job.status = "stopped"
                    return
                job.status = "thinking"
                response = await asyncio.wait_for(handler(), timeout=self.timeout)
                if job.stop_requested:
                    job.status = "stopped"
                else:
                    job.response = response
                    job.status = "completed"
        except asyncio.CancelledError:
            job.status = "stopped"
        except asyncio.TimeoutError:
            job.status = "failed"
            job.error = "The response timed out. You can retry the message."
        except Exception:
            LOGGER.exception("Admin chat turn failed")
            job.status = "failed"
            job.error = "Chat request failed. You can retry the message."
        finally:
            job.finished = time.monotonic()

    def snapshot(self, request_id: str) -> dict[str, Any]:
        self._prune()
        job = self._jobs.get(request_id)
        if job is None:
            raise KeyError(request_id)
        ahead = 0
        for other in self._jobs.values():
            if other is job:
                break
            if other.conversation == job.conversation and other.status not in TERMINAL:
                ahead += 1
        return {"request_id": job.request_id, "session_id": job.conversation[1],
                "status": job.status, "queue_position": ahead,
                "response": job.response, "error": job.error}

    async def stop(self, request_id: str) -> dict[str, Any]:
        job = self._jobs.get(request_id)
        if job is None:
            raise KeyError(request_id)
        if job.status not in TERMINAL and job.task is not None:
            if not job.stop_requested:
                job.stop_requested = True
                job.status = "stopping"
                job.task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(job.task), timeout=self.stop_timeout)
            except asyncio.CancelledError:
                # A task cancelled before its first instruction has no finally block.
                if job.task.cancelled():
                    job.status = "stopped"
                    job.finished = time.monotonic()
                else:
                    raise  # The HTTP caller left; provider cleanup is still running.
            except asyncio.TimeoutError:
                pass  # Keep the conversation locked until the provider unwinds.
        return self.snapshot(request_id)

    async def shutdown(self) -> None:
        tasks = [job.task for job in self._jobs.values() if job.task and not job.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=self.stop_timeout)
