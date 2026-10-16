# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Bounded local connection observations, separate from permission decisions.

Events contain no tickets, exporters, network addresses, secrets or payloads.
A slow UI consumer may lose observations and must read current owner state;
it cannot authorize a connection by replaying an event.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator


class ConnectionEventKind(str, Enum):
    CONNECTED = 'connected'
    READY = 'ready'
    CLOSED = 'closed'
    FAILED = 'failed'


@dataclass(frozen=True)
class ConnectionEvent:
    kind: ConnectionEventKind
    connection_id: int | None
    transport_id: str | None
    generation: int | None = None
    authorization_epoch: int | None = None
    user_requested: bool = False
    code: str | None = None


class ConnectionEvents:
    def __init__(self, *, capacity: int = 256) -> None:
        if type(capacity) is not int or not 0 < capacity <= 4096:
            raise ValueError('invalid connection observation capacity')
        self.capacity, self.lost_events = capacity, 0
        self._queue: deque[ConnectionEvent] = deque()
        self._changed = asyncio.Event()
        self._closed = False
        self._consumer = False

    def emit(self, event: ConnectionEvent) -> None:
        if self._closed:
            return
        if len(self._queue) == self.capacity:
            self._queue.popleft()
            self.lost_events += 1
        self._queue.append(event)
        self._changed.set()

    def close(self) -> None:
        self._closed = True
        self._changed.set()

    async def stream(self) -> AsyncIterator[ConnectionEvent]:
        if self._consumer:
            raise RuntimeError('connection observations already have a consumer')
        self._consumer = True
        try:
            while True:
                while self._queue:
                    yield self._queue.popleft()
                if self._closed:
                    return
                self._changed.clear()
                await self._changed.wait()
        finally:
            self._consumer = False
