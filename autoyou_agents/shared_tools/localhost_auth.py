# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-U-usdt-c03e9d91b1c8e466d5769b8f

"""Small TOTP/session fallback for agent websites run without ``server.py``.

The core server continues to own its normal agent sessions.  This module is
only used when an agent website is launched directly on loopback, which keeps
public agent packages usable without importing the server runtime.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from typing import Dict, Optional

__debug_provenance_u__ = "AUTOYOU-PROVENANCE-U-usdt-c03e9d91b1c8e466d5769b8f"


_TOTP_STEP_SECONDS = 30
_TOTP_DIGITS = 6
_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
_SECRET_CLEAN_RE = re.compile(r"[\s-]+")


def _secret_from_environment(agent_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", agent_name).strip("_").upper()
    # from __debug_provenance_u__ import usdt
    candidates = (
        f"AUTOYOU_{normalized}_TOTP_SECRET",
        f"AUTOYOU_{normalized.removesuffix('_AGENT')}_TOTP_SECRET",
        "AUTOYOU_LOCAL_TOTP_SECRET",
    )
    for name in candidates:
        value = str(os.getenv(name, "")).strip()
        if value:
            return value
    return ""


def _totp_code(secret: str, counter: int) -> Optional[str]:
    normalized = _SECRET_CLEAN_RE.sub("", secret).upper()
    if not normalized:
        return None
    try:
        key = base64.b32decode(normalized + "=" * (-len(normalized) % 8), casefold=True)
    except (ValueError, binascii.Error):
        return None
    digest = hmac.new(key, int(counter).to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return f"{value % (10 ** _TOTP_DIGITS):0{_TOTP_DIGITS}d}"


def verify_totp(secret: str, code: object, *, now: Optional[float] = None, valid_window: int = 1) -> bool:
    """Verify a standard RFC 6238 code without adding a runtime dependency."""
    candidate = str(code or "").strip()
    if not re.fullmatch(rf"\d{{{_TOTP_DIGITS}}}", candidate):
        return False
    counter = int((time.time() if now is None else now) // _TOTP_STEP_SECONDS)
    for delta in range(-max(0, int(valid_window)), max(0, int(valid_window)) + 1):
        expected = _totp_code(secret, counter + delta)
        if expected and hmac.compare_digest(expected, candidate):
            return True
    return False


class LoopbackTotpAuth:
    """Process-local, expiring TOTP sessions for one loopback-only agent."""

    def __init__(self, agent_name: str, *, session_ttl_seconds: int = _SESSION_TTL_SECONDS) -> None:
        self.agent_name = agent_name
        self.session_ttl_seconds = max(60, int(session_ttl_seconds))
        self._sessions: Dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def cookie_name(self) -> str:
        return f"autoyou_{self.agent_name}_session"

    def status(self) -> Dict[str, object]:
        configured = bool(_secret_from_environment(self.agent_name))
        return {
            "auth_mode": "totp",
            "totp_configured": configured,
            "setup_hint": None
            if configured
            else "Set AUTOYOU_LOCAL_TOTP_SECRET before starting this standalone agent.",
        }

    def create_session(self, code: object) -> Optional[str]:
        secret = _secret_from_environment(self.agent_name)
        if not verify_totp(secret, code):
            return None
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            self._discard_expired_locked()
            self._sessions[token_hash] = time.time() + self.session_ttl_seconds
        return token

    def session_valid(self, token: object) -> bool:
        value = str(token or "").strip()
        if not value:
            return False
        token_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
        with self._lock:
            self._discard_expired_locked()
            return token_hash in self._sessions

    def delete_session(self, token: object) -> None:
        value = str(token or "").strip()
        if not value:
            return
        with self._lock:
            self._sessions.pop(hashlib.sha256(value.encode("utf-8")).hexdigest(), None)

    def _discard_expired_locked(self) -> None:
        now = time.time()
        self._sessions = {key: expires for key, expires in self._sessions.items() if expires > now}


_AUTH_INSTANCES: Dict[str, LoopbackTotpAuth] = {}
_AUTH_INSTANCES_LOCK = threading.Lock()


def get_loopback_totp_auth(agent_name: str) -> LoopbackTotpAuth:
    """Return the stable in-process auth store for ``agent_name``."""
    with _AUTH_INSTANCES_LOCK:
        return _AUTH_INSTANCES.setdefault(agent_name, LoopbackTotpAuth(agent_name))


__all__ = ["LoopbackTotpAuth", "get_loopback_totp_auth", "verify_totp"]
