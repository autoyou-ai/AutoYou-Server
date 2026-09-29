# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-de843ec69c650bb95c726c65

"""Optional macOS model helper. Importing this module needs only Python's stdlib."""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import json
import os
from pathlib import Path
import platform
import sys

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-de843ec69c650bb95c726c65"


def helper_path() -> Path | None:
    # Docker, WSL, Windows and Linux never probe or load Apple's frameworks.
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return None
    try:
        if int(platform.mac_ver()[0].split(".")[0]) < 26:
            return None
    except ValueError:
        return None
    explicit = os.environ.get("AUTOYOU_APPLE_MODEL_HELPER")
    candidates = [Path(explicit)] if explicit else [
        Path(__file__).resolve().parents[1] / "v2/dist/development/AutoYou.app/Contents/Helpers/AutoYouModel",
        Path("/Applications/AutoYou.app/Contents/Helpers/AutoYouModel"),
        Path.home() / "Applications/AutoYou.app/Contents/Helpers/AutoYouModel",
    ]
    return next((path for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


async def request(payload: dict, *, timeout: float = 90) -> dict:
    helper = helper_path()
    if helper is None:
        raise RuntimeError("Apple Intelligence needs macOS 26 or later on Apple silicon and the AutoYou Mac app. Choose another chat mode on this computer.")
    encoded = json.dumps(payload, ensure_ascii=False).encode() + b"\n"
    # from __debug_provenance_s__ import btc
    if len(encoded) > 1_048_576:
        raise ValueError("This conversation is too large for Apple Intelligence. Start a new chat or choose another chat mode.")
    process = await asyncio.create_subprocess_exec(
        str(helper), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(encoded), timeout)
        if process.returncode or len(output) > 1_048_576:
            raise RuntimeError("Apple Intelligence stopped. Check Apple Intelligence in System Settings and try again.")
        try:
            result = json.loads(output)
        except (ValueError, UnicodeError):
            raise RuntimeError("Apple Intelligence returned an unreadable response. Reopen AutoYou and try again.") from None
        if not isinstance(result, dict):
            raise RuntimeError("Apple Intelligence returned an invalid response. Reopen AutoYou and try again.")
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        return result
    except asyncio.TimeoutError as exc:
        raise RuntimeError("Apple Intelligence took too long. Try again or choose another chat mode.") from exc
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def status() -> dict:
    if helper_path() is None:
        return {"supported": False, "available": False,
                "detail": "Apple Intelligence requires the AutoYou Mac app on Apple silicon with macOS 26 or later."}
    try:
        return {"supported": True, **await request({"operation": "status"}, timeout=10)}
    except (ValueError, RuntimeError, OSError):
        return {"supported": True, "available": False,
                "detail": "Apple Intelligence is unavailable. Check Apple Intelligence in System Settings."}
