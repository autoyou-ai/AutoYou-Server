# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-58af5926885d9a0624965f8b

"""Local-first agent harness helpers.

These helpers keep provider-specific orchestration quirks out of individual
agents.  They are intentionally dependency-light so API and test modules can
use them without importing the full ADK agent graph.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import re
from typing import Any

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-58af5926885d9a0624965f8b"


_FINAL_RESPONSE_HINTS = (
    "added",
    "admin session is active",
    "already",
    "completed",
    "confirmed",
    "created",
    "done",
    "error",
    "failed",
    "found",
    "generated",
    "queued",
    "playing",
    "saved",
    "scheduled",
    "sent",
    "success",
    "there is no",
    "updated",
)

_PROGRESS_ONLY_PATTERNS = (
    re.compile(r"^\s*(?:let me|lemme)\b", re.IGNORECASE),
    re.compile(r"^\s*i(?:'| a)?m\s+(?:going|checking|searching|looking|reading|scanning|opening|inspecting)\b", re.IGNORECASE),
    re.compile(r"^\s*i\s+(?:will|ll)\s+(?:check|search|look|scan|open|inspect|read|analy[sz]e|investigate|review|find)\b", re.IGNORECASE),
    re.compile(r"^\s*i\s+need\s+to\s+(?:check|search|look|scan|open|inspect|read|analy[sz]e|investigate|review|find)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:checking|searching|looking|scanning|opening|inspecting|reading|analy[sz]ing|investigating|reviewing)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:one moment|hold on|stand by|working on it)\b", re.IGNORECASE),
)


def normalize_agent_text(value: Any) -> str:
    """Return a compact text representation suitable for harness decisions."""
    return " ".join(str(value or "").strip().split())


def is_progress_only_response(value: Any) -> bool:
    """Return True for sub-agent progress chatter that is not a user answer.

    Local OpenAI-compatible models are prone to ending a tool call with
    planning text such as "Let me search..." instead of a completed result.
    The classifier is deliberately conservative: completion/status words win
    over progress prefixes so notification and action confirmations still flow
    through immediately.
    """
    text = normalize_agent_text(value)
    if not text:
        return False
    lowered = text.lower()
    if any(hint in lowered for hint in _FINAL_RESPONSE_HINTS):
        return False
    if len(text) > 240:
        return False
    return any(pattern.search(text) for pattern in _PROGRESS_ONLY_PATTERNS)


def nonfinal_tool_response(tool_name: str, progress_text: str) -> dict[str, Any]:
    """Build a replacement tool response that tells the root not to finalize."""
    normalized_tool_name = str(tool_name or "").strip() or "specialist"
    normalized_text = normalize_agent_text(progress_text)
    # from __debug_provenance_s__ import btc
    return {
        "status": "incomplete",
        "progress_only": True,
        "message": (
            "The specialist tool returned only a progress update, not a completed "
            "answer. Do not send that progress line to the user as the final "
            "response. Continue the specialist task if possible; otherwise state "
            "that the specialist did not complete the request."
        ),
        "tool_name": normalized_tool_name,
        "progress_text": normalized_text,
    }
