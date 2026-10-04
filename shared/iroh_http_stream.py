# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Bounded raw HTTP bytes and negotiated gzip expansion for native HTML."""
from collections.abc import AsyncIterator
import zlib

from shared.iroh_body import BLOCK_BYTES, MAX_BODY_BYTES, BodyUnavailable


async def bounded_http_bytes(response, *, decode: bool = False, maximum_bytes: int = MAX_BODY_BYTES) -> AsyncIterator[bytes]:
    if not 0 <= maximum_bytes <= MAX_BODY_BYTES:
        raise BodyUnavailable("invalid native HTTP body bound")
    encoding = str(response.headers.get("content-encoding", "identity")).strip().lower()
    if decode and encoding not in {"", "identity", "gzip", "x-gzip"}:
        raise BodyUnavailable("unsupported native HTML content encoding")
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS) if decode and encoding in {"gzip", "x-gzip"} else None
    total = 0
    async for raw in response.aiter_raw():
        for offset in range(0, len(raw), BLOCK_BYTES):
            pending = raw[offset:offset + BLOCK_BYTES]
            while pending:
                try:
                    chunk = inflater.decompress(pending, BLOCK_BYTES) if inflater else pending
                except zlib.error:
                    raise BodyUnavailable("invalid native HTTP compression") from None
                pending = inflater.unconsumed_tail if inflater else b""
                total += len(chunk)
                if total > maximum_bytes:
                    raise BodyUnavailable("native HTTP body exceeded its bound")
                if chunk:
                    yield chunk
                if inflater and inflater.unused_data:
                    raise BodyUnavailable("native HTTP compression has trailing data")
    if inflater and not inflater.eof:
        raise BodyUnavailable("native HTTP compression was truncated")
