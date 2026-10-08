# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
"""Small, dependency-free helpers for resolving "it / that / the above" in chat.

Deterministic shortcuts in the root and specialist agents use these so they
agree on what counts as a reference to the previous answer, and so a note
title can always be derived without interrogating the user.
"""

from __future__ import annotations

import re
from typing import Optional

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

# A phrase that clearly points at the previous assistant answer. A bare "it" or
# "that" is deliberately not enough ("remind me to call it quits" is not a
# reference); it has to be the object of a save-style verb.
_PREVIOUS_ANSWER_REFERENCE = re.compile(
    r"\b(?:all\s+this|previous|earlier|above|this\s+answer|this\s+response|this\s+reply"
    r"|that\s+answer|that\s+response|that\s+reply|your\s+(?:answer|response|reply))\b"
    r"|\b(?:save|add|store|put|keep|write|record|copy|append)\s+(?:all\s+of\s+)?"
    r"(?:it|this|that|these|those)\b(?!\s+(?:note|notes|item|items|task|reminder)\b)",
    re.IGNORECASE,
)

# "save to notes" with nothing else in the message can only mean the last answer.
_BARE_SAVE_REQUEST = re.compile(
    r"(?:please\s+)?(?:can\s+you\s+)?(?:save|add|store|put|keep)\s+(?:to|in|into)\s+(?:my\s+)?notes?[.!]?",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>()\"']+", re.IGNORECASE)
_DOMAIN_RE = re.compile(
    r"\b(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|me|io|ai|co|app|dev|edu|gov|xyz|info|us|uk|ca|de|jp|fr|au|in|tech|site|online|store|blog)\b"
    r"(?:/[^\s<>()\"']*)?",
    re.IGNORECASE,
)

_LIST_MARKER = re.compile(r"^\s*(?:[#>*\-•]+\s*|\d+[.)]\s+)*")


def references_previous_answer(text: str) -> bool:
    """True when ``text`` points at the previous answer rather than carrying its own."""
    value = str(text or "")
    return bool(
        _PREVIOUS_ANSWER_REFERENCE.search(value)
        or _BARE_SAVE_REQUEST.fullmatch(" ".join(value.split()))
    )


def title_from_content(content: Optional[str], *, max_chars: int = 60) -> Optional[str]:
    """Derive a short title from the first meaningful line of ``content``."""
    for raw_line in str(content or "").splitlines():
        line = _LIST_MARKER.sub("", raw_line).strip(" \t.:;,-")
        if not line or not re.search(r"\w", line):
            continue
        if len(line) > max_chars:
            line = line[:max_chars].rsplit(" ", 1)[0] or line[:max_chars]
        return line.strip(" .:;,-") or None
    return None


def extract_url(text: Optional[str]) -> str:
    """Return the first URL in ``text`` ("" if none), accepting a bare domain like ``AutoYou.me``."""
    match = _URL_RE.search(str(text or "")) or _DOMAIN_RE.search(str(text or ""))
    if not match:
        return ""
    url = match.group(0).rstrip(".,;:!?)]}\"'\u201c\u201d")
    if url.lower().startswith("www."):
        return f"http://{url}"
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return f"https://{url}"
    return url
