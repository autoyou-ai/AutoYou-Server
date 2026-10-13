# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License. See LICENSE.
"""Negotiated HTTP body frames on the existing reliable, ordered DataChannel.

JSON carries status, headers and cancellation. AYB1 frames carry only bytes:
magic[4], kind[1], request-id-length[1], sequence[4, big endian], id, body.
Each message is at most 16 KiB. DTLS/SCTP supplies integrity and retransmission.
The request id belongs to this connection; it never grants file or HTTP access.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import re
import struct

MAGIC = b"AYB1"
RESPONSE_BODY = 1
REQUEST_BODY = 2
MAX_FRAME_BYTES = 16 * 1024
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_PENDING_BODIES = 4
WINDOW_FRAMES = 32
CAPABILITY_HEADER = "X-AutoYou-Binary-HTTP"
_HEADER = struct.Struct("!4sBBI")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def payload_capacity(request_id: str) -> int:
    if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
        raise ValueError("Invalid binary HTTP request id")
    return MAX_FRAME_BYTES - _HEADER.size - len(request_id)


def encode_frame(kind: int, request_id: str, sequence: int, body: bytes) -> bytes:
    capacity = payload_capacity(request_id)
    if kind not in (RESPONSE_BODY, REQUEST_BODY) or not 1 <= sequence <= 0xFFFFFFFF:
        raise ValueError("Invalid binary HTTP frame")
    if not isinstance(body, bytes) or not 0 < len(body) <= capacity:
        raise ValueError("Invalid binary HTTP payload length")
    identifier = request_id.encode("ascii")
    return _HEADER.pack(MAGIC, kind, len(identifier), sequence) + identifier + body


def decode_frame(frame: bytes) -> tuple[int, str, int, bytes]:
    if not isinstance(frame, bytes) or not _HEADER.size < len(frame) <= MAX_FRAME_BYTES:
        raise ValueError("Invalid binary HTTP frame length")
    magic, kind, size, sequence = _HEADER.unpack_from(frame)
    if magic != MAGIC or kind not in (RESPONSE_BODY, REQUEST_BODY) or not sequence or not 1 <= size <= 128:
        raise ValueError("Invalid binary HTTP frame header")
    end = _HEADER.size + size
    if end >= len(frame):
        raise ValueError("Truncated binary HTTP frame")
    try:
        request_id = frame[_HEADER.size:end].decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Invalid binary HTTP request id") from exc
    payload_capacity(request_id)
    return kind, request_id, sequence, frame[end:]


@dataclass
class _Body:
    size: int
    ready: asyncio.Future
    next_sequence: int = 1
    data: bytearray = field(default_factory=bytearray)


class BodyInbox:
    """At most four 2 MiB bodies; allocate only after HTTP policy has accepted a request."""
    def __init__(self):
        self.pending: dict[str, _Body] = {}

    def open(self, request_id: str, size: int) -> asyncio.Future:
        payload_capacity(request_id)
        if type(size) is not int or not 0 < size <= MAX_BODY_BYTES:
            raise ValueError("Binary HTTP request body exceeds the limit")
        if request_id in self.pending or len(self.pending) >= MAX_PENDING_BODIES:
            raise ValueError("Binary HTTP request body slots are unavailable")
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = _Body(size, future)
        return future

    def feed(self, request_id: str, sequence: int, data: bytes) -> bool:
        body = self.pending.get(request_id)
        if body is None or body.ready.done():
            return False
        if sequence != body.next_sequence or len(body.data) + len(data) > body.size:
            body.ready.set_exception(ValueError("Invalid binary HTTP request body sequence or length"))
            return False
        body.data.extend(data)
        body.next_sequence += 1
        if len(body.data) == body.size:
            body.ready.set_result(bytes(body.data))
        return True

    def discard(self, request_id: str):
        body = self.pending.pop(request_id, None)
        if body is not None and not body.ready.done():
            body.ready.cancel()

    def clear(self):
        for request_id in list(self.pending):
            self.discard(request_id)
