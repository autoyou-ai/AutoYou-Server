"""Process admission for owned native sources, isolated by hermetic test root.

Reservations cover bounded application buffers and source work; they are not an
RSS measurement or a guarantee about opaque codec, driver or model allocators.
"""
from __future__ import annotations

import os
from pathlib import Path
import threading

from shared.session_media import MediaKind, MediaSourceBinding
from shared.session_transport import SessionDenied

MAX_SOURCE_OWNERS = 128
MAX_RESERVED_BYTES = 4 * 1024**3
_lock = threading.RLock()
_scopes: dict[str, "NativeMediaBudget"] = {}


def source_reservation_bytes(binding: MediaSourceBinding) -> int:
    if binding.kind in {MediaKind.MICROPHONE, MediaKind.SYSTEM_AUDIO}:
        return 2 * 1024**2
    if binding.kind not in {MediaKind.CAMERA, MediaKind.SCREEN} or \
            type(binding.width) is not int or type(binding.height) is not int or \
            not 0 < binding.width <= 4096 or not 0 < binding.height <= 4096:
        raise SessionDenied("native source has no bounded buffer profile")
    # Five local capture descriptors each permit a 32 MiB frame. Include eight
    # admitted RGBA pictures for conversion, codec, rendering and replacement.
    return 160 * 1024**2 + binding.width * binding.height * 4 * 8


class NativeMediaBudget:
    def __init__(self, *, maximum_sources=MAX_SOURCE_OWNERS, maximum_bytes=MAX_RESERVED_BYTES):
        if type(maximum_sources) is not int or maximum_sources <= 0 or \
                type(maximum_bytes) is not int or maximum_bytes <= 0:
            raise ValueError("invalid native source admission capacity")
        self.maximum_sources, self.maximum_bytes = maximum_sources, maximum_bytes
        self._owners: set[NativeMediaReservation] = set()
        self._bytes = 0

    @classmethod
    def process(cls):
        root = os.environ.get("AUTOYOU_TEST_ROOT")
        scope = str(Path(root).resolve()) if root else "production"
        with _lock:
            return _scopes.setdefault(scope, cls())

    def reserve(self, binding: MediaSourceBinding):
        return self.reserve_buffers(binding, source_reservation_bytes(binding))

    def reserve_buffers(self, binding, amount):
        if type(amount) is not int or not 0 < amount <= self.maximum_bytes:
            raise SessionDenied("native buffers have no bounded reservation")
        with _lock:
            if any(owner.failure is not None for owner in self._owners):
                raise SessionDenied("native source cleanup is uncertain")
            if len(self._owners) >= self.maximum_sources or self._bytes + amount > self.maximum_bytes:
                raise SessionDenied("native source process capacity is exhausted")
            owner = NativeMediaReservation(self, binding, amount)
            self._owners.add(owner); self._bytes += amount
            return owner

    def usage(self):
        with _lock:
            return len(self._owners), self._bytes


class NativeMediaReservation:
    def __init__(self, budget, binding, amount):
        self.budget, self.binding, self.amount = budget, binding, amount
        self.failure: BaseException | None = None
        self.attached = False
        self.retained: list[object] = []

    def check(self, binding):
        with _lock:
            if self.binding != binding or self not in self.budget._owners or self.failure is not None:
                raise SessionDenied("native source capacity receipt is stale")

    def retain_failure(self, failure, *owners):
        with _lock:
            self.failure = self.failure or failure
            self.retained.extend(owners)

    def release(self):
        with _lock:
            if self.failure is not None:
                raise SessionDenied("failed native source cleanup retains capacity") from self.failure
            if self in self.budget._owners:
                self.budget._owners.remove(self); self.budget._bytes -= self.amount
