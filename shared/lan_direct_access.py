# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Device passes for opening this computer's websites directly on the home network.

A device paired through Local Pair normally browses this computer's websites
through its WebRTC data channel. On the same home network it can load them
straight from the page service's HTTPS mirror instead, which is much faster.
Two things keep that safe:

* Being on the Wi-Fi proves nothing, so a direct request must carry a device
  pass. The paired device asks for a one-time code over its CPace-authenticated
  data channel, redeems it once on the HTTPS mirror, and gets the pass back as an
  HttpOnly, Secure cookie. The long-lived pass never crosses the data channel.
* The mirror's certificate is signed by a per-install CA no device trusts, so
  the data channel also carries the SHA-256 of the certificate's public key and
  the client accepts exactly that key for that address.

Passes live in memory, expire, and stop working when the server password or
security mode changes (the ``epoch`` the server derives from them).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

PASS_COOKIE_NAME = "autoyou_device_pass"
REDEEM_PATH = "/__autoyou/device-pass"
DEFAULT_PASS_TTL_SECONDS = 12 * 60 * 60
DEFAULT_CODE_TTL_SECONDS = 60
_MAX_ENTRIES = 512


@dataclass(frozen=True)
class DevicePass:
    device_key: str
    expires_at: float
    epoch: str


def _digest(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


class DevicePassStore:
    """One-time codes and the passes they are exchanged for, by token digest."""

    def __init__(
        self,
        *,
        pass_ttl_seconds: int = DEFAULT_PASS_TTL_SECONDS,
        code_ttl_seconds: int = DEFAULT_CODE_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.pass_ttl_seconds = int(pass_ttl_seconds)
        self.code_ttl_seconds = int(code_ttl_seconds)
        self._clock = clock
        self._codes: Dict[str, DevicePass] = {}
        self._passes: Dict[str, DevicePass] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        for table in (self._codes, self._passes):
            for key in [key for key, entry in table.items() if entry.expires_at <= now]:
                table.pop(key, None)
            while len(table) > _MAX_ENTRIES:
                table.pop(next(iter(table)))

    def issue_code(self, device_key: str, *, epoch: str) -> str:
        """A one-time code the paired device redeems on the HTTPS mirror."""
        code = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._prune(now)
            self._codes[_digest(code)] = DevicePass(str(device_key), now + self.code_ttl_seconds, str(epoch))
        return code

    def redeem(self, code: str, *, epoch: str) -> Optional[Tuple[str, DevicePass]]:
        """Consume ``code`` and return ``(pass_token, pass)``, or None if it is spent or stale."""
        if not code:
            return None
        now = self._clock()
        with self._lock:
            self._prune(now)
            entry = self._codes.pop(_digest(code), None)
            if entry is None or entry.expires_at <= now or not secrets.compare_digest(entry.epoch, str(epoch)):
                return None
            token = secrets.token_urlsafe(32)
            issued = DevicePass(entry.device_key, now + self.pass_ttl_seconds, entry.epoch)
            self._passes[_digest(token)] = issued
        return token, issued

    def validate(self, token: str, *, epoch: str) -> Optional[DevicePass]:
        if not token:
            return None
        now = self._clock()
        with self._lock:
            entry = self._passes.get(_digest(token))
            if entry is None:
                return None
            if entry.expires_at <= now or not secrets.compare_digest(entry.epoch, str(epoch)):
                self._passes.pop(_digest(token), None)
                return None
            return entry

    def revoke_device(self, device_key: str) -> None:
        with self._lock:
            for table in (self._codes, self._passes):
                for key in [key for key, entry in table.items() if entry.device_key == device_key]:
                    table.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._codes.clear()
            self._passes.clear()


def spki_sha256_b64(cert_path: str | Path) -> str:
    """Base64 SHA-256 of a PEM certificate's SubjectPublicKeyInfo (the pin clients check)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    certificate = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    spki = certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")


def safe_next_path(value: str, *, default: str = "/websites") -> str:
    """A same-origin path to continue to after redeeming a code; never another site."""
    raw = str(value or "").strip()
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw or len(raw) > 2048:
        return default
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc or any(ord(ch) < 32 for ch in raw):
        return default
    return raw
