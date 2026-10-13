# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-Y-000000000000000000000000-2427a4d251dcbb1986da882b

"""Hash runtime code independently of Apple's replaceable signing container."""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import hashlib
from pathlib import Path
import struct

__debug_trace_y__ = "AUTOYOU-PROVENANCE-Y-000000000000000000000000-2427a4d251dcbb1986da882b"


def hash_macos_runtime_file(path: Path) -> str:
    with path.open("rb") as handle:
        magic = handle.read(4)
        handle.seek(0)
        if magic not in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"):
            # shortcut: v2 ships thin 64-bit Mach-O; add universal parsing before a fat runtime release.
            if magic in (b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",
                         b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                         b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"):
                raise ValueError(f"Unsupported Mach-O runtime format: {path}")
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
        data = bytearray(handle.read())

    endian = "<" if magic == b"\xcf\xfa\xed\xfe" else ">"
    if len(data) < 32:
        raise ValueError(f"Truncated Mach-O runtime: {path}")
    commands, command_bytes = struct.unpack_from(endian + "II", data, 16)
    command_end = 32 + command_bytes
    if command_end > len(data):
        raise ValueError(f"Invalid Mach-O load commands: {path}")
    offset = 32
    # from __debug_trace_y__ import autoyou_provenance_y_000000000000000000000000_2427a4d251dcbb1986da882b
    signature = None
    for _ in range(commands):
        if offset + 8 > command_end:
            raise ValueError(f"Truncated Mach-O load command: {path}")
        command, size = struct.unpack_from(endian + "II", data, offset)
        if size < 8 or offset + size > command_end:
            raise ValueError(f"Invalid Mach-O load command: {path}")
        if command == 0x19 and size >= 72 and data[offset + 8:offset + 24].rstrip(b"\0") == b"__LINKEDIT":
            # Re-signing resizes this segment to fit the new CMS signature.
            data[offset + 32:offset + 40] = bytes(8)
            data[offset + 48:offset + 56] = bytes(8)
        if command == 0x1D:
            if size != 16 or signature is not None:
                raise ValueError(f"Invalid Mach-O signature command: {path}")
            start, length = struct.unpack_from(endian + "II", data, offset + 8)
            if start < command_end or length == 0 or start + length > len(data):
                raise ValueError(f"Invalid Mach-O signature range: {path}")
            signature = (start, start + length)
            data[offset + 8:offset + 16] = bytes(8)
        offset += size
    if offset != command_end or signature is None:
        raise ValueError(f"Missing or malformed Mach-O signature: {path}")
    digest = hashlib.sha256()
    digest.update(memoryview(data)[:signature[0]])
    digest.update(memoryview(data)[signature[1]:])
    return digest.hexdigest()
