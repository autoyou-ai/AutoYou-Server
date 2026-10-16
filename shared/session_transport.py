# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Transport contracts and callback references for the shared native authority.

The SDK owns admission, generation floors, revocation and scope policy. Python
keeps channels on their application loop after an existing pairing proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
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


@lru_cache(maxsize=1)
def _native_api():
    from shared.iroh_binding import IrohBindingUnavailable, load_binding
    api = load_binding()
    if not callable(getattr(api, "SessionAdmissionRegistry", None)):
        raise IrohBindingUnavailable("The owned SDK lacks native session admission support")
    return api


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
        _prepare_binding(self, _native_api().verified_session_binding)


def _prepare_binding(binding: SessionBinding, prepare: Callable):
    # UInt64 cannot represent Python booleans or arbitrary-size integers; scopes must remain immutable.
    if any(type(value) is not int or value < 0 or value >= 2**64 for value in (
        binding.generation, binding.authorization_epoch, binding.expires_at_ms,
    )) or not isinstance(binding.scopes, frozenset):
        raise SessionDenied("invalid verified session scalar")
    try:
        result = prepare(binding.endpoint_id, binding.device_id, binding.owner_key,
            binding.canonical_user_id, binding.conversation_key, binding.transport_id,
            binding.generation, binding.authorization_epoch, binding.expires_at_ms,
            list(binding.scopes), binding.transport.value if isinstance(binding.transport, TransportKind) else binding.transport)
    except (TypeError, ValueError, OverflowError):
        raise SessionDenied("invalid verified session binding") from None
    if result is None:
        raise SessionDenied("invalid verified session binding")
    return result


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
    admission: Any


class SessionRegistry:
    """Channel references only; the endpoint and callbacks share native admission."""

    def __init__(self, *, now_ms: Callable[[], int] | None = None, endpoint: Any = None) -> None:
        self._now_ms = now_ms
        if endpoint is not None:
            from shared.iroh_binding import IrohBindingUnavailable
            factory = getattr(endpoint, "application_sessions", None)
            if not callable(factory):
                raise IrohBindingUnavailable("The endpoint lacks native session admission support")
            self._owner = factory()
        else:
            self._owner = _native_api().SessionAdmissionRegistry()
        self._entries: dict[str, _Entry] = {}

    def _clock(self) -> int | None:
        return self._now_ms() if self._now_ms is not None else None

    def _entry(self, binding: SessionBinding) -> _Entry:
        entry = self._entries.get(binding.transport_id)
        if entry is None or (entry.binding is not binding and not entry.admission.matches(
                _prepare_binding(binding, self._owner.prepare_binding))):
            raise SessionDenied("session is expired, revoked, or superseded")
        return entry

    def install(self, binding: SessionBinding, channel: SessionChannel) -> SessionChannel | None:
        native = _prepare_binding(binding, self._owner.prepare_binding)
        installed = self._owner.install(native, getattr(channel, "connection_id", None), self._clock())
        if installed is None:
            raise SessionDenied("verified session installation was rejected")
        previous_id = installed.previous.transport_id() if installed.previous is not None else None
        old = self._entries.pop(previous_id, None)
        self._entries[binding.transport_id] = _Entry(binding, channel, installed.current)
        return old.channel if old else None

    def check(self, binding: SessionBinding, *, scope: str | None = None) -> SessionChannel:
        entry = self._entry(binding)
        if scope is not None and not isinstance(scope, str):
            raise SessionDenied("invalid verified session scope")
        if not entry.admission.check(scope, self._clock()):
            if scope is not None and entry.admission.check(None, self._clock()):
                raise SessionDenied("operation is outside the verified session scope")
            raise SessionDenied("session is expired, revoked, or superseded")
        return entry.channel

    def update_state(self, binding: SessionBinding, state: SessionState) -> None:
        entry = self._entry(binding)
        if not entry.admission.transition(state.value if isinstance(state, SessionState) else state, self._clock()):
            raise SessionDenied("session state transition was rejected")

    def revoke(self, device_id: str, *, minimum_epoch: int) -> SessionChannel | None:
        if type(minimum_epoch) is not int or not 0 <= minimum_epoch < 2**64:
            raise SessionDenied("invalid verified revocation epoch")
        admission = self._owner.revoke_local(device_id, minimum_epoch)
        entry = self._entries.get(admission.transport_id()) if admission is not None else None
        return entry.channel if entry is not None and entry.admission.same_session(admission) else None

    def revoke_owned(self, device_id: str, *, minimum_epoch: int, transport_ids: tuple[str, ...]) -> tuple[SessionChannel, ...]:
        if type(minimum_epoch) is not int or not 0 <= minimum_epoch < 2**64:
            raise SessionDenied("invalid verified revocation epoch")
        admissions = self._owner.revoke_owned(device_id, minimum_epoch, list(transport_ids))
        channels = []
        for admission in admissions:
            entry = self._entries.get(admission.transport_id())
            if entry is not None and entry.admission.same_session(admission):
                channels.append(entry.channel)
        return tuple(channels)

    def retire(self, binding: SessionBinding) -> SessionChannel | None:
        """A delayed old disconnect cannot remove a replacement connection."""
        try:
            entry = self._entry(binding)
        except SessionDenied:
            return None
        self._owner.retire(entry.admission)
        if self._entries.get(binding.transport_id) is entry:
            self._entries.pop(binding.transport_id)
            return entry.channel
        return None


def session_channel_ready(channel: Any) -> bool:
    """Prefer the neutral readiness property; preserve legacy adapter behavior."""
    ready = getattr(channel, "is_ready", None)
    if isinstance(ready, bool):
        return ready
    legacy = getattr(channel, "datachannel", None)
    return bool(getattr(channel, "connection_active", True)) and str(
        getattr(legacy, "readyState", "") or "",
    ).lower() == "open"
