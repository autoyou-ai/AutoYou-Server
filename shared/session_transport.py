# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Transport-neutral session contracts; host authorization remains authoritative.

This module has no socket, OS key store, server import, or persistent state.
The owner service supplies a verified binding after its existing pairing proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Callable, Protocol, runtime_checkable


class TransportKind(str, Enum):
    LEGACY = "legacy"
    IROH = "iroh"


class TransportPolicy(str, Enum):
    LEGACY = "legacy"
    PREFER_IROH = "prefer_iroh"
    IROH_ONLY = "iroh_only"


class SessionState(str, Enum):
    IDLE = "idle"
    ENROLLING = "enrolling"
    CONNECTING = "connecting"
    AUTHORIZING = "authorizing"
    ONLINE = "online"
    CHANGING_PATH = "changing_path"
    RECOVERING = "recovering"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    NEEDS_PAIRING = "needs_pairing"
    USER_DISCONNECTED = "user_disconnected"
    FAILED = "failed"


class SessionDenied(PermissionError):
    """Redacted rejection that must never cause transport downgrade."""


class UnsupportedTransport(ConnectionError):
    """The authenticated peer does not implement the requested transport."""


def select_transport(
    policy: TransportPolicy,
    *,
    enrollment_verified: bool,
    peer_supports_iroh: bool,
    proof_denied: bool = False,
) -> TransportKind:
    if proof_denied or not enrollment_verified:
        raise SessionDenied("pairing authorization is required")
    if policy == TransportPolicy.LEGACY:
        return TransportKind.LEGACY
    if peer_supports_iroh:
        return TransportKind.IROH
    if policy == TransportPolicy.PREFER_IROH:
        return TransportKind.LEGACY
    raise UnsupportedTransport("peer does not support the required transport")


@dataclass(frozen=True)
class SessionBinding:
    """Result of the existing authorization service, not a client wire record."""

    endpoint_id: str
    device_id: str
    owner_key: str
    canonical_user_id: str
    conversation_key: str
    transport_id: str
    generation: int
    authorization_epoch: int
    expires_at_ms: int
    scopes: frozenset[str]
    transport: TransportKind

    def validate(self) -> None:
        identifiers = (
            self.endpoint_id, self.device_id, self.owner_key,
            self.canonical_user_id, self.conversation_key, self.transport_id,
        )
        if any(not isinstance(value, str) or not value or len(value.encode("utf-8")) > 512 for value in identifiers):
            raise SessionDenied("invalid verified session identity")
        if any(type(value) is not int or value < 0 or value >= 2**64 for value in (
            self.generation, self.authorization_epoch, self.expires_at_ms,
        )) or self.generation == 0 or self.expires_at_ms == 0:
            raise SessionDenied("invalid verified session generation")
        if len(self.scopes) > 32 or any(
            not isinstance(scope, str) or not scope or len(scope.encode("utf-8")) > 64 for scope in self.scopes
        ):
            raise SessionDenied("invalid verified session scope")


@runtime_checkable
class SessionChannel(Protocol):
    """Application send boundary implemented independently by both adapters."""

    session_id: str
    connection_active: bool

    async def send_message(self, message: Any, **options: Any) -> bool: ...
    async def cleanup(self) -> None: ...


@runtime_checkable
class BinaryStream(Protocol):
    """Bounded binary path; never carried in the native UI JSON-lines bridge."""

    async def write(self, data: bytes) -> None: ...
    async def finish(self) -> None: ...
    async def abort(self, code: str) -> None: ...
    def __aiter__(self) -> AsyncIterator[bytes]: ...


@dataclass
class _Entry:
    binding: SessionBinding
    channel: SessionChannel
    state: SessionState = SessionState.ONLINE


class SessionRegistry:
    """Connection generation fencing, not a second roles or grants database."""

    def __init__(self, *, now_ms: Callable[[], int]) -> None:
        self._now_ms = now_ms
        self._entries: dict[str, _Entry] = {}
        self._generation_floors: dict[str, int] = {}

    def validate_install(self, binding: SessionBinding) -> None:
        binding.validate()
        if binding.expires_at_ms <= self._now_ms():
            raise SessionDenied("session grant has expired")
        floor = self._generation_floors.get(binding.device_id, 0)
        if binding.device_id not in self._generation_floors and len(self._generation_floors) >= 4096:
            raise SessionDenied("session device capacity has been reached")
        if binding.generation <= floor:
            raise SessionDenied("stale connection generation")
        old = self._entries.get(binding.device_id)
        if old and (
            binding.owner_key != old.binding.owner_key or
            binding.canonical_user_id != old.binding.canonical_user_id or
            binding.conversation_key != old.binding.conversation_key or
            binding.authorization_epoch < old.binding.authorization_epoch
        ):
            raise SessionDenied("session replacement is outside its verified scope")

    def install(self, binding: SessionBinding, channel: SessionChannel) -> SessionChannel | None:
        self.validate_install(binding)
        old = self._entries.get(binding.device_id)
        self._entries[binding.device_id] = _Entry(binding, channel)
        self._generation_floors[binding.device_id] = binding.generation
        return old.channel if old else None

    def check(self, binding: SessionBinding, *, scope: str | None = None) -> SessionChannel:
        entry = self._entries.get(binding.device_id)
        if entry is None or entry.binding != binding or entry.state in (
            SessionState.REVOKED, SessionState.NEEDS_PAIRING, SessionState.USER_DISCONNECTED, SessionState.FAILED,
        ) or binding.expires_at_ms <= self._now_ms():
            raise SessionDenied("session is expired, revoked, or superseded")
        if scope is not None and scope not in binding.scopes:
            raise SessionDenied("operation is outside the verified session scope")
        return entry.channel
    def update_state(self, binding: SessionBinding, state: SessionState) -> None:
        self.check(binding)
        self._entries[binding.device_id].state = state

    def revoke(self, device_id: str, *, minimum_epoch: int) -> SessionChannel | None:
        entry = self._entries.get(device_id)
        if entry is None or entry.binding.authorization_epoch >= minimum_epoch:
            return None
        entry.state = SessionState.REVOKED
        return entry.channel

    def retire(self, binding: SessionBinding) -> SessionChannel | None:
        """A delayed old disconnect cannot remove a replacement connection."""
        entry = self._entries.get(binding.device_id)
        if entry is None or entry.binding != binding:
            return None
        self._entries.pop(binding.device_id)
        return entry.channel


def session_channel_ready(channel: Any) -> bool:
    """Prefer the neutral readiness property; preserve legacy adapter behavior."""
    ready = getattr(channel, "is_ready", None)
    if isinstance(ready, bool):
        return ready
    legacy = getattr(channel, "datachannel", None)
    return bool(getattr(channel, "connection_active", True)) and str(
        getattr(legacy, "readyState", "") or "",
    ).lower() == "open"
