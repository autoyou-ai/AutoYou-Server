# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Host references and clock inputs for native runtime admission policy."""

from functools import lru_cache
import os
import time

RATE_LIMITER_MAX_TRACKED_KEYS = max(128, int(os.getenv("AUTOYOU_RATE_LIMITER_MAX_KEYS", "8192") or 8192))


@lru_cache(maxsize=1)
def native_api():
    from shared.iroh_binding import IrohBindingUnavailable, load_binding
    api = load_binding()
    if not callable(getattr(api, "RuntimeRateLimiter", None)) or not callable(
            getattr(getattr(api, "application_json", None), "RuntimeRateLimiter", None)) or not callable(
            getattr(getattr(api, "application_json", None), "prune_expiring_map", None)):
        raise IrohBindingUnavailable("The owned SDK lacks native runtime admission support")
    return api


class RateLimiter:
    """The SDK owns rolling windows, expiry, key eviction and request counts."""

    def __init__(self, max_requests: int = 5, window_seconds: int = 60,
                 max_tracked_keys: int = RATE_LIMITER_MAX_TRACKED_KEYS):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_tracked_keys = max(1, int(max_tracked_keys))
        self._owner = None

    def _native(self):
        if self._owner is None:
            self._owner = native_api().application_json.RuntimeRateLimiter(max(0, int(self.max_requests)),
                float(self.window_seconds), self.max_tracked_keys)
        return self._owner

    def is_allowed(self, key: str) -> bool:
        return self._native().is_allowed(key, time.time())

    def tracked_key_count(self) -> int:
        return self._native().tracked_key_count()


def prune_expiring_map(entries, *, expiry_fields: tuple, max_entries: int, now=None) -> int:
    if not isinstance(entries, dict) or not entries:
        return 0
    return native_api().application_json.prune_expiring_map(entries, expiry_fields,
        max(0, int(max_entries)), time.time() if now is None else float(now))
