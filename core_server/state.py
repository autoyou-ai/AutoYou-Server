# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-W-stripe-3139832b40894fe2b417be04

"""Core Server State & Rate Limiting Engine."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import os
import copy
import time
import asyncio
import logging
from collections import OrderedDict
from typing import Dict, Any, Optional
from fastapi.responses import JSONResponse

from shared.tunnelmole_service import TunnelmoleService
from shared.cloud_entitlements_client import CloudEntitlementsClient

__debug_provenance_w__ = "AUTOYOU-PROVENANCE-W-stripe-3139832b40894fe2b417be04"


CONFIG_STORE_NONE = "none"
CONFIG_STORE_KEYSTORE = "keystore"
CONFIG_STORE_ENCRYPTED = "encrypted"
_VALID_CONFIG_STORES = {CONFIG_STORE_NONE, CONFIG_STORE_KEYSTORE, CONFIG_STORE_ENCRYPTED}


class ConfigWriteBlocked(RuntimeError):
    """Raised when an admin write would persist without an unlocked config."""


class ServerState:
    """Central state object holding configuration, active sessions, and service handles."""

    server_password: Optional[str] = None
    config_unlock_password: Optional[str] = None
    config_store: str = CONFIG_STORE_NONE
    used_default_password: bool = False
    decrypted_via_env: bool = False
    config: Dict[str, Any] = {}
    telegram_app: Optional[Any] = None
    last_telegram_token: Optional[str] = None
    main_server_port: int = 8081
    signal_service = None
    whatsapp_service = None
    telegram_user_service = None
    initialized_services_on_startup: bool = False
    service_manager = None
    agent_process: Optional[Any] = None
    ai_agent_start_task: Optional[asyncio.Task] = None
    _main_loop: Optional[asyncio.AbstractEventLoop] = None
    tunnelmole_service: Optional[TunnelmoleService] = None
    otp_cache: Dict[str, Any] = {}
    session_cache: Dict[str, Any] = {}
    tunnelmole_timeout_task = None
    tunnelmole_timeout_minutes: int = 10
    tunnelmole_start_time = None
    cloud_sse_task: Optional[asyncio.Task] = None
    cloud_connected: bool = False
    cloud_token_rejected: bool = False
    # Set only when the update feed itself answered 401/403. Kept separate from
    # cloud_token_rejected, which the local session max-age heuristic also
    # sets - a heuristic guess must not stop the update surface from trying.
    update_feed_token_rejected: bool = False
    cloud_last_sse_activity_at: float = 0.0
    cloud_entitlements: Optional[CloudEntitlementsClient] = None
    bluetooth_pairing_server: Optional[Any] = None
    last_diagnostic_nat_class: str = ""
    scheduler_task: Optional[asyncio.Task] = None
    audio_managers: Dict[str, Any] = {}
    telegram_chat_bindings: Dict[str, Dict[str, Any]] = {}
    telegram_allow_tokens: Dict[str, Dict[str, Any]] = {}
    telegram_seen_senders: Dict[str, Dict[str, Any]] = {}
    dynamic_agent_proxy_ports: Dict[str, int] = {}
    managed_frontend_servers: Dict[str, Dict[str, Any]] = {}
    admin_server = None
    https_admin_server = None
    admin_status_cache: Dict[str, Dict[str, Any]] = {}
    # Outcome of the last stranded-envelope sweep: set when a Maximus downgrade
    # was applied without unsealing (another process, or a config reset), so the
    # admin UI can report what was recovered and what stayed unreadable.
    secure_storage_recovery: Dict[str, Any] = {}
    startup_status: Dict[str, Any] = {
        "status": "idle",
        "headline": "Waiting for password",
        "detail": "Enter the server password to decrypt configuration.",
        "step": 0,
        "total_steps": 8,
        "started_at": None,
        "completed_at": None,
        "updated_at": None,
        "error": "",
    }
    startup_time: float = time.time()


STATE = ServerState()
LOGGER = logging.getLogger("autoyou.server")

ADMIN_STATUS_CACHE_TTL_SECONDS = max(
    1.0,
    float(os.getenv("AUTOYOU_ADMIN_STATUS_CACHE_TTL_SECONDS", "3")),
)


def _normalize_config_store(store: Optional[str]) -> str:
    normalized = str(store or "").strip().lower()
    return normalized if normalized in _VALID_CONFIG_STORES else CONFIG_STORE_NONE


def _has_loaded_config_session() -> bool:
    return _normalize_config_store(STATE.config_store) in {
        CONFIG_STORE_ENCRYPTED,
        CONFIG_STORE_KEYSTORE,
    }


def _can_persist_config() -> bool:
    store = _normalize_config_store(STATE.config_store)
    if store == CONFIG_STORE_KEYSTORE:
        return True
    if store == CONFIG_STORE_ENCRYPTED:
        return bool(str(STATE.config_unlock_password or "").strip())
    return False


def _config_write_block_reason() -> Optional[str]:
    if not _has_loaded_config_session() or not isinstance(STATE.config, dict) or not STATE.config:
        return (
            "Persisted configuration is not loaded. Sign in with the server password "
            "before changing settings."
        )
    if not _can_persist_config():
        return "Persisted configuration is locked. Sign in with the server password before changing settings."
    return None


def _loaded_config_for_update(*, copy_config: bool = False) -> Dict[str, Any]:
    reason = _config_write_block_reason()
    if reason:
        raise ConfigWriteBlocked(reason)
    cfg = STATE.config
    if not isinstance(cfg, dict) or not cfg:
        raise ConfigWriteBlocked("Persisted configuration is not loaded.")
    return copy.deepcopy(cfg) if copy_config else cfg


def _json_config_write_blocked_response(reason: Optional[str] = None) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "success": False,
            "error": reason or "Persisted configuration is not loaded.",
        },
    )


#: Ceiling on distinct keys a RateLimiter will track at once. Reached only
#: under a spray of unique source IPs, which is exactly when the limiter must
#: not become the thing that exhausts memory.
RATE_LIMITER_MAX_TRACKED_KEYS = max(
    128,
    int(os.getenv("AUTOYOU_RATE_LIMITER_MAX_KEYS", "8192") or 8192),
)


class RateLimiter:
    """In-memory rolling-window rate limiter keyed by IP.

    Keys whose windows have fully drained are evicted, and the tracked-key
    count is capped: a limiter that grows one entry per distinct source
    address is itself a memory-exhaustion vector, which defeats the purpose.
    """

    def __init__(
        self,
        max_requests: int = 5,
        window_seconds: int = 60,
        max_tracked_keys: int = RATE_LIMITER_MAX_TRACKED_KEYS,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_tracked_keys = max(1, int(max_tracked_keys))
        # from __debug_provenance_w__ import stripe
        # Insertion-ordered so the oldest-touched key is cheap to evict.
        self._requests: "OrderedDict[str, list]" = OrderedDict()

    def _drop_expired(self, window_start: float) -> None:
        """Evict every key with no timestamps left inside the window."""
        for key in [k for k, stamps in self._requests.items() if not stamps or stamps[-1] <= window_start]:
            self._requests.pop(key, None)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds

        self._drop_expired(window_start)

        stamps = [t for t in self._requests.get(key, ()) if t > window_start]
        if len(stamps) >= self.max_requests:
            self._requests[key] = stamps
            self._requests.move_to_end(key)
            return False

        stamps.append(now)
        self._requests[key] = stamps
        self._requests.move_to_end(key)

        # Hard cap after insertion so a unique-IP spray cannot grow the map
        # without bound even while every window is still live.
        while len(self._requests) > self.max_tracked_keys:
            self._requests.popitem(last=False)

        return True

    def tracked_key_count(self) -> int:
        """Expose the tracked-key count so tests and diagnostics can assert bounds."""
        return len(self._requests)


# --------------------------------------------------------------------------
# Bounded pairing caches
# --------------------------------------------------------------------------
# Both maps expire entries lazily when something happens to read them, which
# leaves unread entries resident forever. A peer that requests OTPs (or opens
# pair sessions) without ever completing them therefore grows the process
# indefinitely. These sweeps make expiry eager and bound the worst case.

OTP_CACHE_MAX_ENTRIES = max(
    64, int(os.getenv("AUTOYOU_OTP_CACHE_MAX_ENTRIES", "2048") or 2048)
)
SESSION_CACHE_MAX_ENTRIES = max(
    64, int(os.getenv("AUTOYOU_SESSION_CACHE_MAX_ENTRIES", "4096") or 4096)
)


def _prune_expiring_map(
    entries: Dict[str, Any],
    *,
    expiry_fields: tuple,
    max_entries: int,
    now: Optional[float] = None,
) -> int:
    """Drop expired entries, then trim the oldest until under ``max_entries``.

    Returns the number of entries removed. Malformed rows (missing or
    unparseable expiry) are treated as expired rather than kept forever.
    """
    if not isinstance(entries, dict) or not entries:
        return 0

    current = time.time() if now is None else float(now)
    removed = 0

    for key in list(entries.keys()):
        value = entries.get(key)
        if not isinstance(value, dict):
            entries.pop(key, None)
            removed += 1
            continue
        expires_at = next(
            (value.get(field) for field in expiry_fields if value.get(field) is not None),
            None,
        )
        try:
            if expires_at is None or current > float(expires_at):
                entries.pop(key, None)
                removed += 1
        except (TypeError, ValueError):
            entries.pop(key, None)
            removed += 1

    if len(entries) > max_entries:
        def _sort_key(item):
            payload = item[1] if isinstance(item[1], dict) else {}
            try:
                return float(payload.get("created_at") or 0.0)
            except (TypeError, ValueError):
                return 0.0

        for key, _ in sorted(entries.items(), key=_sort_key)[: len(entries) - max_entries]:
            entries.pop(key, None)
            removed += 1

    return removed


def prune_otp_cache(now: Optional[float] = None) -> int:
    """Sweep expired/overflowing OTP hashes out of ``STATE.otp_cache``."""
    return _prune_expiring_map(
        STATE.otp_cache,
        expiry_fields=("expires", "expires_at"),
        max_entries=OTP_CACHE_MAX_ENTRIES,
        now=now,
    )


def prune_session_cache(now: Optional[float] = None) -> int:
    """Sweep expired/overflowing pair sessions out of ``STATE.session_cache``."""
    return _prune_expiring_map(
        STATE.session_cache,
        expiry_fields=("expires_at",),
        max_entries=SESSION_CACHE_MAX_ENTRIES,
        now=now,
    )
