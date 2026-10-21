# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-f881a77e27cfb2d3eb0fd298

"""End-to-end key agreement for an explicitly shared AutoYou server."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import base64
import asyncio
import hashlib
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic
from typing import Awaitable, Callable

import httpx

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-f881a77e27cfb2d3eb0fd298"


_SALT = b"AutoYou Peer Code X25519 v1"
_INFO_PREFIX = "AutoYou Peer Link code grant v1"
_PUBLIC_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_session_check: ContextVar[Callable[[], Awaitable[bool | None]] | None] = ContextVar(
    "shared_device_session_check", default=None,
)


@contextmanager
def shared_device_session(check: Callable[[], Awaitable[bool | None]] | None):
    """Carry authority from the authenticated relay through fragment reassembly."""
    token = _session_check.set(check)
    try:
        yield
    finally:
        _session_check.reset(token)


def current_shared_device_session_check():
    return _session_check.get()


async def shared_device_session_allowed(base_url: str, server_token: str, metadata: dict) -> bool | None:
    """None is a transient failure; only an explicit boolean renews authority."""
    body = {key: metadata.get(key) for key in (
        "grant_id", "server_device_id", "client_device_id", "server_public_key", "client_public_key",
    )}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                base_url.rstrip("/") + "/v1/server/shared-session/check", json=body,
                headers={"Authorization": "Bearer " + server_token},
            )
        if response.status_code in (401, 403, 404):
            return False
        if response.status_code != 200:
            return None
        allowed = response.json().get("allowed")
        return allowed if type(allowed) is bool else None
    except (httpx.HTTPError, ValueError, AttributeError):
        return None


async def watch_shared_device_session(check, is_current, close):
    """Close revoked sessions promptly and bound cloud outages to a 30s lease."""
    deadline = monotonic() + 30.0
    try:
        while is_current():
            remaining = deadline - monotonic()
            if remaining <= 0:
                await close()
                return
            try:
                allowed = await asyncio.wait_for(check(), timeout=min(5.0, remaining))
            except Exception:
                allowed = None
            if not is_current():
                return
            now = monotonic()
            if allowed is False or now >= deadline:
                await close()
                return
            if allowed is True:
                deadline = now + 30.0
            await asyncio.sleep(min(10.0, max(0.0, deadline - monotonic())))
    except asyncio.CancelledError:
        # Cancelling the guard must never leave its still-current peer unguarded.
        if is_current():
            await close()
        raise


@dataclass(frozen=True)
class SharedDeviceKeyMaterial:
    private_key: str
    public_key: str


def _b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    normalized = str(value or "").strip()
    return base64.b64decode(
        normalized.replace("-", "+").replace("_", "/") + "=" * (-len(normalized) % 4),
        validate=True,
    )


def is_public_key(value: object) -> bool:
    return bool(_PUBLIC_KEY_RE.fullmatch(str(value or "").strip()))


def generate_key_material() -> SharedDeviceKeyMaterial:
    private = x25519.X25519PrivateKey.generate()
    # from __debug_provenance_k__ import donations
    private_bytes = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_bytes = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return SharedDeviceKeyMaterial(_b64e(private_bytes), _b64e(public_bytes))


def is_key_material(private_key: object, public_key: object) -> bool:
    try:
        private_bytes = _b64d(str(private_key or ""))
        public_bytes = _b64d(str(public_key or ""))
        if len(private_bytes) != 32 or len(public_bytes) != 32:
            return False
        actual = x25519.X25519PrivateKey.from_private_bytes(private_bytes).public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return actual == public_bytes
    except (TypeError, ValueError):
        return False


def credential_invitation_id(grant_id: str, server_device_id: str, client_device_id: str) -> str:
    """Bind the existing Peer Code KDF to one grant, server, and client device."""
    values = [str(value or "").strip() for value in (grant_id, server_device_id, client_device_id)]
    if any(not re.fullmatch(r"[a-z0-9]{15}", value) for value in values):
        raise ValueError("Invalid shared-device credential context")
    raw = bytearray(hashlib.sha256(("AutoYou Shared Device grant v1\0" + "\0".join(values)).encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x50
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def derive_authenticator(
    private_key: str,
    own_public_key: str,
    peer_public_key: str,
    invitation_id: str,
) -> str:
    """Derive the same 256-bit password as iOS/Android PeerCodeCrypto."""
    invitation = uuid.UUID(str(invitation_id or ""))
    private_bytes = _b64d(private_key)
    own_bytes = _b64d(own_public_key)
    peer_bytes = _b64d(peer_public_key)
    if len(private_bytes) != 32 or len(own_bytes) != 32 or len(peer_bytes) != 32:
        raise ValueError("Invalid shared-device key material")
    private = x25519.X25519PrivateKey.from_private_bytes(private_bytes)
    actual_public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if actual_public != own_bytes:
        raise ValueError("Shared-device private/public key mismatch")
    ordered = sorted((own_public_key, peer_public_key))
    info = "\0".join(
        (_INFO_PREFIX, str(invitation).lower(), ordered[0], ordered[1])
    ).encode()
    shared = private.exchange(x25519.X25519PublicKey.from_public_bytes(peer_bytes))
    return _b64e(HKDF(algorithm=hashes.SHA256(), length=32, salt=_SALT, info=info).derive(shared))


def pairing_auth_profile(
    metadata: object, *, private_key: str, public_key: str,
    server_device_id: str, client_device_id: str, allow_bootstrap: bool = False,
) -> dict[str, str] | None:
    """Validate cloud-authenticated device bindings before deriving a local secret."""
    if not isinstance(metadata, dict):
        return None
    purpose = metadata.get("purpose")
    if purpose not in (None, "bootstrap") or (purpose == "bootstrap" and not allow_bootstrap):
        return None
    if (metadata.get("server_device_id") != server_device_id
            or metadata.get("client_device_id") != client_device_id
            or metadata.get("server_public_key") != public_key):
        return None
    try:
        invitation = credential_invitation_id(metadata.get("grant_id"), server_device_id, client_device_id)
        password = derive_authenticator(private_key, public_key, metadata.get("client_public_key"), invitation)
    except (TypeError, ValueError):
        return None
    return {"password": password, "security_mode": "secure", "security_tier": "A"}
