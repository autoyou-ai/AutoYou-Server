# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-e7b990eb9d0f5cef24ee91a2

"""Materialize the Tunnelmole Node launcher from a compiled runtime payload."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-e7b990eb9d0f5cef24ee91a2"


import base64
import hashlib
import os
import tempfile
import zlib
from pathlib import Path


LAUNCHER_FILENAME = "tunnelmole_node_launcher.mjs"
# The canonical launcher source in git uses LF. Regenerate this payload from the
# LF form only (normalize CRLF first) - a checkout with core.autocrlf=true will
# otherwise bake Windows line endings in here and desync it from the repo file
# on every other platform. .gitattributes pins *.mjs to LF to prevent that.
LAUNCHER_SHA256 = "74d17a96d40512668c75ec2aa8f885b9fab066059de02f2231debb87bfd5e75f"
_COMPRESSED_LAUNCHER_BASE64 = (
    "eNqlVdtO20AQfecrBqtCthQseIW6UtSENoJcBIkqVFXB2GOy1N51d9dcGvLvnbUd23FD29A8rPcyc3b2zMwJS1IhNaS+XkAkRQIW"
    "FyGemLV1useK02V+PhVnLMbZ5QWsmqaZjMlyLxBcGZzgu3+HPSbBgystGb+zUykCVMpF/uB2Z9Px9Xg2n85Go/7FcHzRn4/Gvf58"
    "0v143v3Un/cGl/DyApbluOSc2M5pCfyo+jxMBeN6N+AvV/P+qDcZD0bT7cALrdO3QX+eTid/AfdTdo7Pu8F2J4P5ef96O2AoEp/x"
    "3QB742F3MNqOl6fXg1GW3KJ0U18qHHBt75K58WXx+CPL6cDxkVPVQuSzmLDthCCoJjqAUgrpgPcBlntAP2MmYnTzffvmq844xzih"
    "rUNTWYexn/FggfIbvFuWIKsbwje+LAI794ODA9DPKYqowAfP88ASt/cYaMscWkpTUVpAtFUO+cTND5wymN8DKklo2paXrwBjhXUM"
    "/4ZReedjxe0T0/YxHa2IOIO43+gh4nW/UflmuVGvZqOsMTMt08jUGeNMo22y65iTPM3vPThaR2pyY1tDphSFBxJ/ZExiCArj6HAh"
    "lKZ5nQ1YJwKoEJgUPEGuXcvE3Or7iZERL1cLVyLx8EBBVM8xpaHlcyP72owRuxuKMCNpkXHpXEmNnUPd02PtxhUdsEKmtEXfwt+9"
    "V5bjuAuJ0WkDXGkhyeXt6EoG5pNXY4pSrbcL3FdurYn774sZD/Fp45rGPUsIMfKzWJ+ULFJdeuA/+kxDIdx2i13nVXLajm3mnD89"
    "sO28hQCnjtyky5QYS0lXKt2tq/x0i2FLpJvLErgIYCNs1zTBVbFjN2Rjf9OqXDV7uOiOad0ApQ2ELAQu6KGEzPyY/cS8Dequ3grt"
    "KtQDjYltFb1KmS0mzkY+RaoZTeh9y6JhV3XMhe43Yyyt3eofoZisg2mQ0k6HW2/YJUoHtMzQNDQEvg4WLV0r+Dij0eiCoFf6FJ5e"
    "4GuCEcQsl4i15hPwL+12yHA="
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
