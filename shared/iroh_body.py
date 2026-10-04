# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Ephemeral encrypted browser bodies; no second history or retry transcript.

The Rust stream validator owns offsets/digests. This sink owns disk capacity,
temporary-file lifetime and bounded decryption into the existing HTTP handlers.
An incomplete body is never forwarded as a completed browser mutation.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import struct
import tempfile
import threading
from typing import AsyncIterator
import zlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BLOCK_BYTES = 48 * 1024
MAX_BODY_BYTES = 16 * 1024 * 1024 * 1024
_BLOCK_HEADER = struct.Struct("!I12s")


class BodyUnavailable(ValueError):
    pass


class BodyBudget:
    def __init__(self, *, maximum_bytes: int = MAX_BODY_BYTES, maximum_bodies: int = 64) -> None:
        if not 0 < maximum_bytes <= MAX_BODY_BYTES or not 0 < maximum_bodies <= 64:
            raise ValueError("invalid browser body budget")
        self.maximum_bytes, self.maximum_bodies = maximum_bytes, maximum_bodies
        self.bytes = self.bodies = 0
        self.lock = threading.RLock()

    def open(self) -> None:
        with self.lock:
            if self.bodies >= self.maximum_bodies:
                raise BodyUnavailable("browser body capacity is exhausted")
            self.bodies += 1

    def reserve(self, length: int, root: Path) -> None:
        with self.lock:
            if self.bytes + length > self.maximum_bytes or shutil.disk_usage(root).free < length + 1024 * 1024:
                raise BodyUnavailable("browser body disk capacity is exhausted")
            self.bytes += length

    def release(self, length: int, *, body: bool = False) -> None:
        with self.lock:
            self.bytes -= length
            if body:
                self.bodies -= 1


_BUDGET = BodyBudget()


def _root() -> Path:
    test_root = os.environ.get("AUTOYOU_TEST_ROOT")
    return (Path(test_root) if test_root else Path(tempfile.gettempdir()) / "AutoYou") / "iroh-browser-bodies"


class EncryptedBody:
    """A verified, single-consumer request body with <=48 KiB plaintext blocks."""
    def __init__(self, *, total: int | None, associated_data: bytes, root: Path | None = None,
                 budget: BodyBudget | None = None) -> None:
        if (total is not None and (type(total) is not int or not 0 <= total <= MAX_BODY_BYTES)) or not 0 < len(associated_data) <= 4096:
            raise BodyUnavailable("invalid browser body")
        test_root = os.environ.get("AUTOYOU_TEST_ROOT")
        self.root = (root or _root()).resolve()
        if test_root and not self.root.is_relative_to(Path(test_root).resolve()):
            raise BodyUnavailable("browser body escaped its test root")
        self.root.mkdir(parents=True, exist_ok=True)
        self.budget = budget or _BUDGET
        self.budget.open()
        try:
            self._file = tempfile.TemporaryFile(dir=self.root, prefix="body-", suffix=".enc")
        except BaseException:
            self.budget.release(0, body=True)
            raise
        self._expected_total = total
        self.total, self.received, self._charged = total if total is not None else MAX_BODY_BYTES, 0, 0
        self._key = bytearray(os.urandom(32))
        self._aad = associated_data
        self._lock = threading.RLock()
        self._verified = self._closed = self._reading = False

    def append(self, data: bytes) -> None:
        with self._lock:
            if self._closed or self._verified or not 0 < len(data) <= BLOCK_BYTES or self.received + len(data) > self.total:
                raise BodyUnavailable("browser body is closed or outside its bound")
            nonce = os.urandom(12)
            aad = self._aad + self.received.to_bytes(8, "big")
            ciphertext = AESGCM(bytes(self._key)).encrypt(nonce, data, aad)
            length = _BLOCK_HEADER.size + len(ciphertext)
            self.budget.reserve(length, self.root)
            try:
                self._file.write(_BLOCK_HEADER.pack(len(ciphertext), nonce))
                self._file.write(ciphertext)
            except BaseException:
                self.budget.release(length)
                raise
            self._charged += length
            self.received += len(data)

    def verify(self) -> None:
        # Called only after the shared Rust validator verifies the final digest.
        with self._lock:
            if self._closed or self._verified or (self._expected_total is not None and self.received != self.total):
                raise BodyUnavailable("browser body completion is invalid")
            self._file.flush()
            self.total = self.received
            self._verified = True

    def _begin_read(self) -> None:
        with self._lock:
            if self._closed or not self._verified or self._reading:
                raise BodyUnavailable("browser body is unavailable or already consumed")
            self._reading = True
            self._file.seek(0)

    def _read_block(self, offset: int) -> bytes:
        with self._lock:
            if self._closed:
                raise BodyUnavailable("browser body is closed")
            header = self._file.read(_BLOCK_HEADER.size)
            if not header:
                if offset != self.total:
                    raise BodyUnavailable("browser body was truncated")
                return b""
            if len(header) != _BLOCK_HEADER.size:
                raise BodyUnavailable("browser body was truncated")
            length, nonce = _BLOCK_HEADER.unpack(header)
            if not 16 < length <= BLOCK_BYTES + 16:
                raise BodyUnavailable("browser body block is invalid")
            ciphertext = self._file.read(length)
            if len(ciphertext) != length:
                raise BodyUnavailable("browser body was truncated")
            try:
                data = AESGCM(bytes(self._key)).decrypt(nonce, ciphertext, self._aad + offset.to_bytes(8, "big"))
            except Exception:
                raise BodyUnavailable("browser body integrity check failed") from None
            if offset + len(data) > self.total:
                raise BodyUnavailable("browser body exceeded its bound")
            return data

    async def iterate(self, *, compressed: bool = False, maximum_decoded_bytes: int = MAX_BODY_BYTES) -> AsyncIterator[bytes]:
        if not 0 <= maximum_decoded_bytes <= MAX_BODY_BYTES:
            raise BodyUnavailable("invalid decoded browser body bound")
        from shared.iroh_delivery import _joined_disk
        await _joined_disk(self._begin_read)
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS) if compressed else None
        offset = decoded = 0
        while True:
            raw = await _joined_disk(self._read_block, offset)
            if not raw:
                break
            offset += len(raw)
            pending = raw
            while pending:
                try:
                    data = inflater.decompress(pending, BLOCK_BYTES) if inflater else pending
                except zlib.error:
                    raise BodyUnavailable("browser body compression is invalid") from None
                pending = inflater.unconsumed_tail if inflater else b""
                decoded += len(data)
                if decoded > maximum_decoded_bytes:
                    raise BodyUnavailable("decoded browser body exceeded its bound")
                if data:
                    yield data
                if inflater and inflater.unused_data:
                    raise BodyUnavailable("browser body has trailing compressed data")
        if inflater and not inflater.eof:
            raise BodyUnavailable("compressed browser body was truncated")

    async def read(self, *, maximum_bytes: int, compressed: bool = False) -> bytes:
        value = bytearray()
        async for data in self.iterate(compressed=compressed, maximum_decoded_bytes=maximum_bytes):
            value.extend(data)
        return bytes(value)

    async def decoded(self) -> "EncryptedBody":
        """Validate a compressed transport body fully before an upstream write."""
        target = EncryptedBody(total=None, associated_data=self._aad + b"/decoded", root=self.root, budget=self.budget)
        try:
            async for data in self.iterate(compressed=True):
                from shared.iroh_delivery import _joined_disk
                await _joined_disk(target.append, data)
            target.verify()
            return target
        except BaseException:
            target.close()
            raise

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._file.close()
                finally:
                    self._key[:] = bytes(32)
                    self.budget.release(self._charged, body=True)
                    self._charged = 0

    def __del__(self) -> None:
        if hasattr(self, "_lock"):
            self.close()
