# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Helpers for redacting personal identifiers in runtime logs."""

from __future__ import annotations

import copy
from typing import Any


SENSITIVE_IDENTIFIER_KEYS = {
    "chat_id",
    "chatid",
    "destination",
    "destinationnumber",
    "from",
    "number",
    "paired_phone_number",
    "phone",
    "phonenumber",
    "phone_number",
    "recipient",
    "recipients",
    "remotechatid",
    "remote_chat_id",
    "remotejid",
    "selfchatid",
    "self_chat_id",
    "sender",
    "senderid",
    "sender_id",
    "source_number",
    "sourcenumber",
    "to",
}


def _redact_text_identifier(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return "-"

    if "@" in value:
        local, suffix = value.split("@", 1)
        return f"{_redact_text_identifier(local)}@{suffix}" if local else f"***@{suffix}"

    has_plus = value.startswith("+")
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) >= 7:
        return ("+" if has_plus else "") + "***" + digits[-4:]

    if len(value) <= 4:
        return "***"
    if len(value) <= 8:
        return f"{value[0]}***{value[-1]}"
    return f"{value[:2]}***{value[-2:]}"


def redact_identifier(value: Any) -> Any:
    """Redact an identifier-like value while preserving enough shape for debugging."""
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        redacted = [redact_identifier(item) for item in value]
        if isinstance(value, tuple):
            return tuple(redacted)
        if isinstance(value, set):
            return set(redacted)
        return redacted
    if isinstance(value, dict):
        return redact_payload(value)
    return _redact_text_identifier(str(value))


def redact_payload(payload: Any) -> Any:
    """Return a copy with common phone/chat/session identifiers redacted."""
    if isinstance(payload, dict):
        redacted = {}
        for key, value in payload.items():
            normalized_key = str(key or "").replace("-", "_").lower()
            compact_key = normalized_key.replace("_", "")
            if normalized_key in SENSITIVE_IDENTIFIER_KEYS or compact_key in SENSITIVE_IDENTIFIER_KEYS:
                redacted[key] = redact_identifier(value)
            else:
                redacted[key] = redact_payload(value)
        return redacted
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(redact_payload(item) for item in payload)
    try:
        return copy.copy(payload)
    except Exception:
        return payload
