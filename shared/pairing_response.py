# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-ad98ecd370028d491e96b2b0

"""Helpers for API-facing pairing responses."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import base64
import json
import re
from typing import Any, Dict, Optional

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-ad98ecd370028d491e96b2b0"

PAIRING_REPLY_COMMANDS = frozenset({"/otp", "/pair_hello_answer", "/autopair_hello_answer", "/autopair_answer"})
PAIRING_HANDOFF_BASE = "https://www.autoyou.me/peer-link/"
MAX_PAIRING_REPLY_BYTES = 65536


def is_app_pairing_message(text: str) -> bool:
    value = (text or "").strip()
    command, sep, _ = value.partition("\n")
    return (command.rstrip("\r") in {"/peerpair", "/peerpair_answer"}
            or (bool(sep) and command.rstrip("\r") in PAIRING_REPLY_COMMANDS)
            or value.startswith((PAIRING_HANDOFF_BASE + "#", "autoyou://pair/message#", "autoyou://peer/connect#",
                                 "https://app.autoyou.me/peer/connect#", "https://staging-app.autoyou.me/peer/connect#",
                                 "https://www.autoyou.me/peer-link#", "https://www.autoyou.me/share/#message=",
                                 "https://www.autoyou.me/share#message=")))


def pairing_reply_link(text: str, *, limit: int = 0) -> str:
    """Keep the existing authenticated reply intact in a client-only fragment."""
    raw = text.strip().encode("utf-8")
    command, sep, body = raw.partition(b"\n")
    if not sep or not body.strip() or len(raw) > MAX_PAIRING_REPLY_BYTES:
        return text
    if command.decode("utf-8").rstrip("\r") not in PAIRING_REPLY_COMMANDS:
        return text
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    link = f"{PAIRING_HANDOFF_BASE}#pair={encoded}"
    return text if limit and len(link) > limit else link


def unwrap_pairing_reply(text: str) -> str:
    """Decode only known handoff URLs; leave raw text for the normal validators."""
    value = text.strip()
    if len(value) > 87500:
        return text
    base, _, fragment = value.partition("#")
    prefix = {"autoyou://pair/message": "message=", PAIRING_HANDOFF_BASE: "pair="}.get(base)
    if not prefix or not fragment.startswith(prefix):
        return text
    encoded = fragment[len(prefix):]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,87382}", encoded):
        return text
    try:
        reply = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        if len(reply) <= MAX_PAIRING_REPLY_BYTES:
            decoded = reply.decode("utf-8")
            command, _, body = decoded.partition("\n")
            if command.rstrip("\r") in PAIRING_REPLY_COMMANDS and body.strip():
                return decoded
    except (ValueError, UnicodeError):
        pass
    return text


def parse_otp_response_payload(response_text: str) -> Optional[Dict[str, Any]]:
    """Return compatibility fields for a raw /otp reply.

    The live pairing wire format remains a plain text command string:
    "/otp\n<payload>". API helpers can reuse this function to expose the raw
    text under otp_response while still surfacing parsed otp/url fields when the
    payload is plaintext JSON.
    """

    first_line, separator, remainder = str(response_text or "").partition("\n")
    if first_line.strip() != "/otp":
        return None

    raw_payload: Dict[str, Any] = {"otp_response": response_text}
    # from __debug_provenance_k__ import donations
    if not separator or not remainder.strip():
        return raw_payload

    try:
        data = json.loads(remainder)
    except Exception:
        return raw_payload

    if not isinstance(data, dict):
        return raw_payload

    parsed_payload = dict(data)
    parsed_payload["otp_response"] = response_text
    return parsed_payload
