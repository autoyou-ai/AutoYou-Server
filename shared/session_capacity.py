# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Bound the foreign host's outstanding sends while native capacity is full.

The endpoint owner pulses this budget when it polls native progress. Control
keeps reserved capacity, cancellation releases bytes, and a closed owner wakes
all waiters. This is an in-memory adapter contract, never a delivery receipt.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class SendReservation:
    budget: SendCapacity
    size: int
    released: bool = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            self.budget.pending_bytes -= self.size
            self.budget.pending_sends -= 1
            self.budget.pulse()


class SendCapacity:
    def __init__(self, *, max_bytes: int = 16*1024*1024, max_sends: int = 128,
                 control_bytes: int = 1024*1024, control_sends: int = 16) -> None:
        if not 0 < control_bytes < max_bytes or not 0 < control_sends < max_sends:
            raise ValueError("invalid session capacity bounds")
        self.max_bytes, self.max_sends = max_bytes, max_sends
        self.control_bytes, self.control_sends = control_bytes, control_sends
        self.pending_bytes = self.pending_sends = 0
        self.closed = False
        self._changed = asyncio.Event()

    def reserve(self, size: int, *, control: bool = False) -> SendReservation | None:
        if type(size) is not int or size <= 0:
            raise ValueError("invalid session send size")
        bytes_limit = self.max_bytes if control else self.max_bytes-self.control_bytes
        send_limit = self.max_sends if control else self.max_sends-self.control_sends
        if self.closed or self.pending_bytes+size > bytes_limit or self.pending_sends >= send_limit:
            return None
        self.pending_bytes += size
        self.pending_sends += 1
        return SendReservation(self, size)

    async def wait_for_progress(self) -> None:
        if not self.closed:
            await self._changed.wait()

    def pulse(self) -> None:
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    def close(self) -> None:
        self.closed = True
        self.pulse()
