# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-eaab34d7e3886022ece2135a

"""Local pending ad-credit tally for the Earnings Agent.

When a connected iOS/Android client finishes a rewarded support ad it sends a
sanitized ``rewarded_ad_completed`` proof over the existing WebRTC data
channel. The server keeps the latest proof in memory for the session; this
module additionally keeps a small durable local tally so the Earnings Agent
website can show "pending ad credits collected on this device".

Boundary rules (mirrors the Funding OS contract):
- pending credits are local, non-transferable readback only - not cash,
  crypto, gift cards, payouts, or confirmed account balances
- nothing here calls account-service or any cloud endpoint; cloud pickup of
  pending credits stays a future OAuth-verified server-side process
- the stored payload contains only sanitized fields (platform, source,
  watched seconds, timestamps) - never account ids, tokens, or ad unit ids
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-eaab34d7e3886022ece2135a"


import math
import threading
import time
from typing import Any, Dict, Mapping, Optional

from shared.platform_runtime import get_service_data_dir
from shared.secure_storage import SecureStorageError, load_secure_json, save_secure_json

_LOCK = threading.Lock()
_STORE_FILENAME = "pending_ad_credits.json"
_MAX_SEEN_KEYS = 64
DEFAULT_PENDING_CREDITS_PER_SECOND = 10
MAX_WATCHED_SECONDS = 120.0


def _store_path():
    return get_service_data_dir("earnings_agent", anchor=__file__) / _STORE_FILENAME


def _empty_state() -> Dict[str, Any]:
    return {
        "pending_credits": 0,
        "watched_seconds": 0.0,
        "events": 0,
        "last_event": None,
        "seen_keys": [],
        "updated_at": None,
    }


def _read_state() -> Dict[str, Any]:
    try:
        data = load_secure_json(_store_path(), default={})
    except SecureStorageError:
        raise
    except (OSError, ValueError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    state = _empty_state()
    try:
        state["pending_credits"] = max(0, int(data.get("pending_credits") or 0))
        # Older local files counted one credit per completion and had no
        # aggregate duration. Preserve that balance as an equivalent seconds
        # baseline so the first new event cannot make the displayed formula
        # disagree with the stored total. Historical per-event durations are
        # unavailable, so this is intentionally a one-time compatibility
        # baseline rather than a claim about past watch time.
        if "watched_seconds" not in data:
            watched_seconds = state["pending_credits"] / float(pending_credit_multiplier())
        else:
            watched_seconds = float(data.get("watched_seconds") or 0.0)
        state["watched_seconds"] = (
            round(watched_seconds, 3)
            if math.isfinite(watched_seconds) and watched_seconds > 0
            else 0.0
        )
        state["events"] = max(0, int(data.get("events") or 0))
    except (TypeError, ValueError, OverflowError):
        return _empty_state()
    last_event = data.get("last_event")
    state["last_event"] = last_event if isinstance(last_event, dict) else None
    seen = data.get("seen_keys")
    state["seen_keys"] = [str(item) for item in seen][-_MAX_SEEN_KEYS:] if isinstance(seen, list) else []
    updated_at = data.get("updated_at")
    state["updated_at"] = str(updated_at) if updated_at else None
    return state


def _write_state(state: Dict[str, Any]) -> None:
    path = _store_path()
    save_secure_json(path, state)


def _completion_key(completion: Mapping[str, Any]) -> str:
    control_id = str(completion.get("control_id") or "").strip()
    if control_id:
        return f"control:{control_id}"
    return "ts:{}:{}".format(
        str(completion.get("session_id") or "")[:64],
        str(completion.get("timestamp_ms") or ""),
    )


def pending_credit_multiplier() -> int:
    """Return the local pending-credit multiplier shared by reward paths."""
    # This is deliberately fixed to the iOS/Android/web value. Changing the
    # rate requires a coordinated client release so one surface cannot claim
    # a different amount for the same completed-ad seconds.
    return DEFAULT_PENDING_CREDITS_PER_SECOND


def normalize_watched_seconds(value: Any) -> float:
    """Clamp a completion duration to the supported local reward window."""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(parsed):
        return 0.0
    return round(max(0.0, min(MAX_WATCHED_SECONDS, parsed)), 3)


def pending_credit_amount(watched_seconds: Any, multiplier: Optional[int] = None) -> int:
    """Calculate a pending amount from seconds, never from completion count."""
    seconds = normalize_watched_seconds(watched_seconds)
    if seconds <= 0:
        return 0
    rate = pending_credit_multiplier() if multiplier is None else max(1, int(multiplier))
    return max(1, int(round(seconds * rate)))


def _sanitized_event(completion: Mapping[str, Any]) -> Dict[str, Any]:
    def _safe_int(value: Any) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError, OverflowError):
            return 0

    watched_seconds = normalize_watched_seconds(completion.get("watched_seconds"))
    multiplier = pending_credit_multiplier()
    return {
        "platform": str(completion.get("platform") or "unknown")[:32],
        "source": str(completion.get("source") or "ads_watching_agent")[:64],
        "watched_seconds": watched_seconds,
        "pending_credits": pending_credit_amount(watched_seconds, multiplier),
        "timestamp_ms": _safe_int(completion.get("timestamp_ms")),
    }


def record_rewarded_ad_completion(completion: Mapping[str, Any]) -> Dict[str, Any]:
    """Add ``watched_seconds * multiplier`` for a unique ad completion.

    Duplicate deliveries (retries, multi-alias fanout) are ignored via the
    completion's control id or session+timestamp key. Returns the updated
    public summary.
    """
    if not isinstance(completion, Mapping):
        return get_pending_ad_credit_summary()
    key = _completion_key(completion)
    with _LOCK:
        state = _read_state()
        if key not in state["seen_keys"]:
            event = _sanitized_event(completion)
            amount = int(event["pending_credits"])
            if amount <= 0:
                return _public_summary(state)
            state["seen_keys"] = (state["seen_keys"] + [key])[-_MAX_SEEN_KEYS:]
            state["pending_credits"] += amount
            state["watched_seconds"] = round(
                float(state.get("watched_seconds") or 0.0) + float(event["watched_seconds"]),
                3,
            )
            state["events"] += 1
            state["last_event"] = event
            state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            try:
                _write_state(state)
            except OSError:
                pass
        return _public_summary(state)


def get_pending_ad_credit_summary() -> Dict[str, Any]:
    """Return the public pending-credit summary for local display."""
    with _LOCK:
        return _public_summary(_read_state())


def _public_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pending_credits": int(state.get("pending_credits") or 0),
        "watched_seconds": round(float(state.get("watched_seconds") or 0.0), 3),
        "credit_multiplier": pending_credit_multiplier(),
        "events": int(state.get("events") or 0),
        "last_event": state.get("last_event"),
        "updated_at": state.get("updated_at"),
        "ledger_effect": "none",
        "cloud_hot_path_write": False,
        "note": "Pending ad credits are local, non-transferable support readback toward future AutoYou Cloud time and subscription gifts.",
    }
