# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Bounded IO for the existing Fernet format, using cryptography primitives.

Format: https://github.com/fernet/spec/blob/master/Spec.md. The existing SPM
header/token remains readable by the retained release. No new key or envelope
generation is introduced. Decryption authenticates an encrypted snapshot and
checks padding/size before yielding any plaintext.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import tempfile
import time
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

BLOCK_BYTES = 48 * 1024
MAX_BYTES = 1024 * 1024 * 1024


def encrypt_to(output: BinaryIO, blocks: Iterable[bytes], key: bytes) -> None:
    """Write one standard base64url Fernet token without collecting its body."""
    if len(key) != 32:
        raise ValueError("invalid protected stream key")
    iv = os.urandom(16)
    header = b"\x80" + int(time.time()).to_bytes(8, "big") + iv
    signer = hmac.new(key[:16], digestmod=hashlib.sha256)
    encryptor = Cipher(algorithms.AES(key[16:]), modes.CBC(iv)).encryptor()
    padder = padding.PKCS7(128).padder()
    pending = b""

    def write(raw: bytes, *, signed: bool = True, final: bool = False) -> None:
        nonlocal pending
        if signed:
            signer.update(raw)
        value = pending + raw
        length = len(value) if final else len(value) // 3 * 3
        output.write(base64.urlsafe_b64encode(value[:length]))
        pending = value[length:]

    write(header)
    for block in blocks:
        if not isinstance(block, bytes) or not 0 < len(block) <= BLOCK_BYTES:
            raise ValueError("protected stream block exceeded its bound")
        write(encryptor.update(padder.update(block)))
    write(encryptor.update(padder.finalize()) + encryptor.finalize())
    write(signer.digest(), signed=False, final=True)


def decrypt_blocks(source: BinaryIO, key: bytes, *, directory: Path, maximum_bytes: int | None) -> Iterator[bytes]:
    if len(key) != 32 or (maximum_bytes is not None and maximum_bytes < 0):
        raise ValueError("invalid protected stream limits")
    # Snapshot ciphertext only. Another process replacing/mutating the source
    # cannot change the bytes between authentication and decryption.
    with tempfile.TemporaryFile(mode="w+b", dir=directory, prefix="autoyou-spm-cipher-") as snapshot:
        signer = hmac.new(key[:16], digestmod=hashlib.sha256)
        tail = b""
        pending = b""
        ended = False
        raw_size = 0
        encoded_limit = None if maximum_bytes is None else ((maximum_bytes // 16 + 1) * 16 + 57 + 2) // 3 * 4
        encoded_size = 0
        while True:
            block = source.read(BLOCK_BYTES)
            if not block:
                break
            encoded_size += len(block)
            if ended or (encoded_limit is not None and encoded_size > encoded_limit):
                raise ValueError("invalid protected stream size")
            pending += block
            length = len(pending) // 4 * 4
            if not length:
                continue
            encoded, pending = pending[:length], pending[length:]
            if b"=" in encoded:
                # Padding is permitted only in the last quartet of one token.
                if b"=" in encoded[:-4] or pending:
                    raise ValueError("invalid protected stream encoding")
                ended = True
            try:
                decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
            except binascii.Error as error:
                raise ValueError("invalid protected stream encoding") from error
            raw_size += len(decoded)
            value = tail + decoded
            if len(value) > 32:
                message, tail = value[:-32], value[-32:]
                signer.update(message); snapshot.write(message)
            else:
                tail = value
        if pending or len(tail) != 32 or raw_size < 73 or not hmac.compare_digest(signer.digest(), tail):
            raise ValueError("invalid protected stream authentication")
        snapshot.flush(); snapshot.seek(0)
        header = snapshot.read(25)
        ciphertext_size = raw_size - 57
        if len(header) != 25 or header[0] != 0x80 or ciphertext_size < 16 or ciphertext_size % 16:
            raise ValueError("invalid protected stream framing")
        # Inspect the authenticated final block before yielding. This also
        # enforces a plaintext size limit for callers requesting a small body.
        snapshot.seek(25 + max(0, ciphertext_size - 32))
        ending = snapshot.read(32)
        previous = header[9:] if ciphertext_size == 16 else ending[:16]
        last = ending[-16:]
        final_decryptor = Cipher(algorithms.AES(key[16:]), modes.CBC(previous)).decryptor()
        final_plaintext = final_decryptor.update(last) + final_decryptor.finalize()
        pad = final_plaintext[-1]
        if not 1 <= pad <= 16 or final_plaintext[-pad:] != bytes([pad]) * pad or (maximum_bytes is not None and ciphertext_size - pad > maximum_bytes):
            raise ValueError("invalid protected stream padding or size")
        snapshot.seek(25)
        decryptor = Cipher(algorithms.AES(key[16:]), modes.CBC(header[9:])).decryptor()
        unpadder = padding.PKCS7(128).unpadder()
        remaining = ciphertext_size
        while remaining:
            block = snapshot.read(min(BLOCK_BYTES, remaining))
            if not block:
                raise ValueError("protected stream snapshot is truncated")
            remaining -= len(block)
            plaintext = unpadder.update(decryptor.update(block))
            if plaintext:
                yield plaintext
        final = unpadder.update(decryptor.finalize()) + unpadder.finalize()
        if final:
            yield final
