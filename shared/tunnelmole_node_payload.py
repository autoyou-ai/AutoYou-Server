# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-8bf74404b7e8de1cc68d1c8c

"""Materialize the Tunnelmole Node launcher from a compiled runtime payload."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import base64
import hashlib
import os
import tempfile
import zlib
from pathlib import Path

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-8bf74404b7e8de1cc68d1c8c"


LAUNCHER_FILENAME = "tunnelmole_node_launcher.mjs"
# The canonical launcher source in git uses LF. Regenerate this payload from the
# LF form only (normalize CRLF first) - a checkout with core.autocrlf=true will
# otherwise bake Windows line endings in here and desync it from the repo file
# on every other platform. .gitattributes pins *.mjs to LF to prevent that.
LAUNCHER_SHA256 = "eeb0b4aa7f3c01d3084ac724f33572380f25aec08ecb005001a6b44e38ebd82d"
_COMPRESSED_LAUNCHER_BASE64 = (
    "eNqlVm1v4kYQ/s6vmFjVyZaCMXBNyqVUQuBcUcCgQFpFVcUZex02tXfd3XVyOY7/3lnbgKGkba58MJ7dmWfn9Vk3GtDn6YugDysF"
    "ZmBBy2ldwCQlbKa4IC8wGvVt6MUx5CoSBJFEPJHQrjUaMKIBYZKEkLGQCFArAr1M8XuewYxnIiD13pNPY38Zk61ubjcjKA/7rjdz"
    "gbLcLhX8kQQKBOcKIi4gLvRxH6XEV5Sz3LY3bIxHoIRPGWUPkMncdkWXVKEjz1SteKbgWVClCAM/Q1nQL7k9mKUTMGvbHauAu5tP"
    "7id39ent5BfX63l9t96vX76/iFrOpXPRvOy0nHaz7bS+b6FUDy6cVvOHZavzvt1uNju+E/gdZ0matRpNUi4UpL5aQSR4AgbjIfmg"
    "ZeNqu7vO9+f8msbk7nYEm6pqJmLUrAWcSY0T/OE/kAEV0IWZEhiqiWEGREqbsCe7dHsxv/M8dzSejNyFNxm4i2mvf9P76C4Gw1v4"
    "+hUMw7LRODGtqxL4WbosTDll6m3Av84WrjeYTobe/DTwSqn026B/ns+n/wLup/QGm/FNsL3pcHHj3p8GDHmCDfQ2wMFk3Bt6p/Hy8"
    "nbBy5IlEXbqC0mGTJlvqdzktgjeMaxzaDrWrhciHCHENhOEwJ44ByIEFxZ0f4J1DfCn1XhM7Hzd/PSbyhgjcYJLdd1Z9djPWLAi4"
    "nf4bl2CbD4hvralEZi5Hbx7B+olJTwq8KHb7YLBl3oqDb1pSIVNaeiB3RnkL3a+YZXO/N2hMglV3fLwDZBYj/jWh/+GsbPOn7vc"
    "fqbKbOLWBhOnEc8qM4R5Pat0vhYP+lUvlD2mX8syUnmNJKOIqatr6Z28zD92wdl6qmtjGmMqpSYjQf7MqEAakiSO6isuNSXtqwHb"
    "QgA2AhWcJYQp29A+H839VNNIN2cLGxmXx0/oxC4c3RpKvFSqr/Qzog9jHmZILSIujXdUY+ZQjxisWTniHIyQSmXgf2FvP0rDsuy"
    "VINFVBVziXYAm344uRaD/8m5MiZDb5QL3lVP3ifvfB1O8nz4fHFM5Zw0hifwsVh/KLGJfdsF/9qmCgrjNo+xarybn2PA4c9Y/BX"
    "hsfCIB1t5zXS7dYjRFXtnx7r7Lr04oHpF0VSyBCwcO3Lb1EMyKFbNCG2eHWqVUneFiOub7ASh1IKQhMLzoNTL1Y/qF5GOwn+qT0L"
    "YkaqhIYhrFrGJlixfroJ481Ze9xPjWxcBu9j4XvF/1sdS2dzdC8bJ1ppKU43LY+wWzRDnHD5OM6IGGwFfB6ojXinxc41PzAscof"
    "XRPf/68QhhBTHOK2HI+Av8FYKww3g=="
)


def launcher_bytes() -> bytes:
    payload = zlib.decompress(base64.b64decode(_COMPRESSED_LAUNCHER_BASE64))
    actual = hashlib.sha256(payload).hexdigest()
    if actual != LAUNCHER_SHA256:
        raise RuntimeError("Embedded Tunnelmole launcher failed its integrity check.")
    return payload


def materialize_launcher(target_dir: str | os.PathLike[str]) -> Path:
    """Atomically create or repair the launcher in a mutable per-user directory."""

    destination_dir = Path(target_dir).expanduser().resolve(strict=False)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / LAUNCHER_FILENAME
    payload = launcher_bytes()

    try:
        if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == LAUNCHER_SHA256:
            return destination
    except OSError:
        pass

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{LAUNCHER_FILENAME}.", dir=destination_dir)
    # from __debug_provenance_s__ import btc
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        try:
            destination.chmod(0o600)
        except OSError:
            pass
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
    return destination
