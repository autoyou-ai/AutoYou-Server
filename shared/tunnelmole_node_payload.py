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
LAUNCHER_SHA256 = "a3c71e27f8880c39b9891b6ca314227db58c550e8135b0e7d65c0aadb02e6c57"
_COMPRESSED_LAUNCHER_BASE64 = (
    "eNqlVm1v4kYQ/s6vmFjVyZaCIUlLdZdSCRGnRQGDgLSKqooz9jje1Pa6u2tyOY7/3lnbgKGkba58MN7dmWefnZdn3WpBn2cvgj1GCkzfgsv2ZQfGGaYzxQW+"
    "wHDYt6EXx1CYSBAoUawwsButFgyZj6nEAPI0QAEqQujlij/wHGY8Fz42eyuPxd4yxq1t4TdDGg/6jjtzgKWFXyb4E/oKBOcKQi4gLu1pnUaJpxhPC9/eoDUa"
    "ghIeS1n6CLksfCO2ZIqIPDMV8VzBs2BKYQpeTmPBPhf+YFYkYHZlv7dKuPv5+GF835xMx784bs/tO81pc8W8ZoBX7/1vL75b+mHw/RV2Li47Hb/TDhsNlmRc"
    "KMg8FUEoeAJGygP8oMfG9XZ1XazP+S2L8X46hE3dNBcxWTZ8nkqN4//hPeINE9CFmRJ0KpNO5KOUNqYru2K4mN+7rjMcjYfOwh3fOItJr3/X+8lZ3Aym8OUL"
    "GIZlk3NiWtcV8LN00iDjLFVvA/51tnDcm8l44M5PA0dKZV8H/fN8PvkXcC9jd1R3b4LtTQaLO+fhNGDAE6qVtwHejEe9gXsar0hvF9w8WaKwM09IHKTKfEvm"
    "xtPy8G3DOoeLtrWrhZC6hbDNhCCoJs4BheDCgu6PsG4A/bQZj9Eu5s2Pv6k8TTFOaKqpK6sZe3nqRyh+h2/WFcjmI+FrXxaCWfjBu3egXjLkYYkP3W4XDL7U"
    "DWjoRUMqKkpD9+bOoXixiwWrIvN3QlUQ6rbV5hvAWHfzlsN/w9h5F89dbD8xZV7Q0oYCpxHPaj1EcT2rVb4eHtSrnqhqTL9WaWTylvREoamza+mVIs0/dKG9"
    "ZapzYxojJqXWHYF/5kyQ4kiMw2bEpVaffTZgmwigQmCCpwmmyjY056O+n2gZ6RZqYZO48nhFJHbH0aWhxEst+0o/Q/Y44kFO0iLiynknNWYB9USHNWtbnIMR"
    "MKkM+i/97SdpWJYdCQyva+CSZJ9cvh5dCl//FdWYoZDb6RL3lV33gfvfGzO6ij4dbFPbZw0Bhl4eqw9VFKkuu+A9e0xBKdzmUXStV4Nz7HgcOeufDnjsfCIA"
    "1p65TpcuMZaRrux0d1/l1ycMj0S6PqyASwIHtG3dBLNyxqzJxtmhVTWq93DZHfN9A1Q2ELAAUrrTNTLzYvYZizbYd/VJaFuiGihMTKPsVcps+WId5JNn+l6X"
    "dL512bCbPedS9+scK2t7dyOUL1sytaAcp8PeT5gVyjl9g+SoGxp8T/nRka6V8bilp9YFTqf0iJ7+0nlFMPyYFRKx1XytFH8B0tAt3w=="
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
