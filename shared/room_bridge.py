# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-8cfc6ee0f419f8dc372eadc8

"""Security and wire-contract primitives for a mobile-hosted room bridge.

The mobile root host remains the room sequencer and fan-out authority. This
module only grants one authenticated Computer transport permission to submit
room chat turns. It deliberately does not implement room federation.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from collections import OrderedDict, deque
import copy
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any, Callable, Deque, Dict, Iterable, Mapping, Optional, Set, Tuple

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-8cfc6ee0f419f8dc372eadc8"


ROOM_BRIDGE_PROTOCOL = "autoyou.room-bridge/1"
# A bridge grant currently proves only a DataChannel chat capability. Existing
# call tracks are not room-scoped negotiation and must not be advertised as
# Computer audio/video authority.
ROOM_BRIDGE_ALLOWED_PERMISSIONS = frozenset({"chat"})
ROOM_BRIDGE_DEFAULT_TTL_SECONDS = 10 * 60
ROOM_BRIDGE_MAX_MESSAGE_CHARS = 4_000
ROOM_BRIDGE_RATE_LIMIT = 30
ROOM_BRIDGE_RATE_WINDOW_SECONDS = 60.0
ROOM_BRIDGE_MAX_INFLIGHT = 4
ROOM_BRIDGE_DEDUPE_LIMIT = 512
ROOM_BRIDGE_DEDUPE_TTL_SECONDS = 60 * 60
ROOM_BRIDGE_SCOPE_LIMIT = 512
ROOM_BRIDGE_TOTAL_DEDUPE_LIMIT = 4_096
ROOM_BRIDGE_GRANT_RATE_LIMIT = 6
ROOM_BRIDGE_GRANT_RATE_OWNER_LIMIT = 512
ROOM_BRIDGE_COMPUTER_MODE = "read_only_conversation"
# Room-origin members are asserted by the authenticated mobile host. They are
# useful for presentation and replay integrity, but are never an authenticated
# participant identity on the Computer.
ROOM_BRIDGE_ORIGIN_TRUST = "host_attested_presentation_only"
# The only provider modes currently proven to expose a plain model chat API
# without routing through AutoYou/OpenClaw/Hermes/Odysseus tools or actions.
ROOM_BRIDGE_READ_ONLY_BACKENDS = frozenset({"ollama", "ollama_gateway"})
ROOM_BRIDGE_READ_ONLY_SYSTEM_PROMPT = (
    "You are AutoYou Computer participating in a shared room. Reply with helpful "
    "conversational text only. You have no tools and no authority to perform actions. "
    "Do not claim to browse, access private data, modify systems, operate devices, "
    "or send messages. If asked to act, explain that you can only discuss the request."
)

_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9._~:-]+$")


class RoomBridgeError(ValueError):
    """A bounded, client-safe room bridge protocol error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retry_after_ms: int = 0,
        grant_context: Any = None,
    ):
        super().__init__(message)
        self.code = str(code)
        self.public_message = str(message)
        self.retry_after_ms = max(0, int(retry_after_ms or 0))
        # Internal-only correlation context. error_control_payload() selects the
        # public tuple explicitly and never serializes the bearer/token digest.
        self.grant_context = grant_context


def _bounded_opaque(
    value: Any,
    *,
    field_name: str,
    minimum: int = 1,
    maximum: int = 128,
) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not minimum <= len(normalized) <= maximum or not _OPAQUE_ID_RE.fullmatch(normalized):
        raise RoomBridgeError(
            f"invalid_{field_name}",
            f"{field_name} must be {minimum}-{maximum} URL-safe characters",
        )
    return normalized


def normalize_room_id(value: Any) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if re.fullmatch(r"[a-z0-9]{12}", normalized) is None:
        raise RoomBridgeError(
            "invalid_room_id",
            "room_id must be exactly 12 lowercase alphanumeric characters",
        )
    return normalized


def normalize_room_epoch(value: Any) -> str:
    # The clients encode a random 128-bit nonce as unpadded base64url. The
    # server cannot prove RNG quality; it binds the exact canonical value.
    normalized = value.strip() if isinstance(value, str) else ""
    if re.fullmatch(r"[A-Za-z0-9_-]{22}", normalized) is None:
        raise RoomBridgeError(
            "invalid_room_epoch",
            "room_epoch must be exactly 22 base64url characters",
        )
    return normalized


def normalize_conversation_epoch(value: Any) -> str:
    """Validate the client-generated, non-authority conversation boundary."""
    normalized = value.strip() if isinstance(value, str) else ""
    if re.fullmatch(r"[A-Za-z0-9_-]{22}", normalized) is None:
        raise RoomBridgeError(
            "invalid_conversation_epoch",
            "conversation_epoch must be exactly 22 base64url characters",
        )
    return normalized


def normalize_message_id(value: Any) -> str:
    return _bounded_opaque(value, field_name="message_id", minimum=8, maximum=128)


def bound_room_bridge_reply(value: Any) -> str:
    """Keep a cached/fanned Computer reply within the Room chat contract."""
    normalized = str(value or "(no response)")
    return normalized[:ROOM_BRIDGE_MAX_MESSAGE_CHARS]


def normalize_permissions(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ("chat",)
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise RoomBridgeError("invalid_permissions", "permissions must be a list")
    if len(value) > len(ROOM_BRIDGE_ALLOWED_PERMISSIONS):
        raise RoomBridgeError("invalid_permissions", "too many permissions")
    if any(not isinstance(item, str) for item in value):
        raise RoomBridgeError("invalid_permissions", "permissions must be strings")
    normalized = tuple(sorted({item.strip().lower() for item in value}))
    if not normalized or any(item not in ROOM_BRIDGE_ALLOWED_PERMISSIONS for item in normalized):
        raise RoomBridgeError(
            "invalid_permissions",
            "permissions must contain only chat",
        )
    return normalized


def sanitize_room_member(value: Any) -> Dict[str, Any]:
    """Bound display metadata without treating it as authorization identity."""
    if not isinstance(value, Mapping):
        return {}
    result: Dict[str, Any] = {}
    limits = {
        "device_id": 128,
        # Keep the shared validator byte-for-byte compatible with the existing
        # Android/iOS RoomMember limits. A server-emitted Computer member must
        # be accepted by both clients without either side silently truncating it.
        "display_name": 40,
        "platform": 24,
        "role": 32,
    }
    for key, maximum in limits.items():
        candidate = str(value.get(key) or "").strip()
        if candidate:
            if key == "role":
                candidate = candidate.lower()
            result[key] = candidate[:maximum]
    if "joined_at" in value and not isinstance(value.get("joined_at"), bool):
        try:
            joined_at = int(value.get("joined_at"))
        except (TypeError, ValueError, OverflowError):
            joined_at = -1
        if 0 <= joined_at <= 2**63 - 1:
            result["joined_at"] = joined_at
    return result


def normalize_room_origin(value: Any) -> Dict[str, Any]:
    """Validate host-attested display metadata and reject bridge loops.

    The returned member is not an authorization or audit identity. Authority is
    derived exclusively from the bound transport owner and bearer grant.
    """
    string_fields = ("device_id", "display_name", "platform", "role")
    if not isinstance(value, Mapping) or any(
        not isinstance(value.get(field_name), str) for field_name in string_fields
    ):
        raise RoomBridgeError(
            "invalid_origin",
            "room bridge origin must be a complete RoomMember",
        )
    joined_at = value.get("joined_at")
    if (
        not isinstance(joined_at, int)
        or isinstance(joined_at, bool)
        or not 1 <= joined_at <= 2**63 - 1
    ):
        raise RoomBridgeError(
            "invalid_origin",
            "room bridge origin joined_at must be a positive integer",
        )
    origin = sanitize_room_member(value)
    required = {"device_id", "display_name", "platform", "role", "joined_at"}
    if set(origin) != required:
        raise RoomBridgeError(
            "invalid_origin",
            "room bridge origin must be a complete RoomMember",
        )
    if origin["role"] == "computer":
        raise RoomBridgeError(
            "invalid_origin",
            "a Computer participant cannot submit another Computer bridge turn",
        )
    if origin["role"] not in {"host", "spoke"}:
        raise RoomBridgeError("invalid_origin", "room bridge origin role must be host or spoke")
    return origin


def require_room_bridge_read_only_backend(value: Any) -> str:
    """Allow a grant only for a provider path proven to have no action surface."""
    backend = str(value or "").strip().lower()
    if backend not in ROOM_BRIDGE_READ_ONLY_BACKENDS:
        raise RoomBridgeError(
            "unsupported_backend",
            "room bridge requires the read-only Ollama conversation backend",
        )
    return backend


def require_bound_owner_key(value: Any) -> str:
    """Reject the identity resolver's unauthenticated guest fallback."""
    owner_key = str(value or "").strip()
    if not owner_key or owner_key.startswith("guest:"):
        raise RoomBridgeError(
            "unauthorized_transport",
            "an authenticated, bound transport is required",
        )
    return owner_key


def _event_content_digest(message: str, origin: Mapping[str, Any]) -> bytes:
    canonical = json.dumps(
        {"message": str(message), "origin": dict(origin)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    # from __debug_provenance_a__ import schedule
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def _token_digest(token: str) -> bytes:
    return hashlib.sha256(str(token or "").encode("utf-8")).digest()


def derive_room_principal_id(
    *,
    server_identity_key: str,
    host_owner_key: str,
    room_id: str,
    room_epoch: str,
    conversation_epoch: str,
) -> str:
    # Deliberately excludes the client-supplied RoomMember origin. The
    # authenticated host owner and room epochs are the complete identity input.
    material = "\0".join(
        (
            str(server_identity_key or ""),
            str(host_owner_key or ""),
            normalize_room_id(room_id),
            normalize_room_epoch(room_epoch),
            normalize_conversation_epoch(conversation_epoch),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_computer_member(
    *,
    server_identity_key: str,
    server_name: str,
    joined_at: int,
) -> Dict[str, Any]:
    member_digest = hashlib.sha256(
        f"{server_identity_key}\0room-computer-member".encode("utf-8")
    ).hexdigest()[:32]
    return {
        "device_id": f"computer:{member_digest}",
        "display_name": str(server_name or "AutoYou Computer").strip()[:40] or "AutoYou Computer",
        "platform": "computer",
        "role": "computer",
        "joined_at": int(joined_at),
    }


@dataclass
class RoomBridgeGrant:
    grant_id: str
    token_digest: bytes = field(repr=False)
    host_owner_key: str
    current_transport_id: str
    room_id: str
    room_epoch: str
    conversation_epoch: str
    room_principal_id: str
    permissions: Tuple[str, ...]
    grant_revision: int
    computer_member: Dict[str, Any]
    issued_at: float
    expires_at: float
    expires_at_monotonic: float = field(repr=False)
    scope_ledger: "RoomBridgeScopeLedger" = field(repr=False)
    transport_generation: int = 1
    inflight_events: Set[str] = field(default_factory=set, repr=False)
    inflight_tasks: Set[Any] = field(default_factory=set, repr=False)


@dataclass(frozen=True)
class RoomBridgeIssue:
    grant: RoomBridgeGrant
    grant_token: str = field(repr=False)
    superseded_grants: Tuple["RoomBridgeRevocation", ...] = ()


@dataclass(frozen=True)
class RoomBridgeAdmission:
    grant: RoomBridgeGrant
    request_message_id: str
    room_event_id: str
    sequence: int
    origin: Dict[str, Any]
    transport_generation: int
    duplicate: bool = False
    outcome_status: str = "admitted"
    cached_final: Optional[Dict[str, Any]] = field(default=None, repr=False)
    displaced_tasks: Tuple[Any, ...] = field(default=(), repr=False)


@dataclass
class RoomBridgeEventOutcome:
    room_event_id: str
    request_message_id: str
    sequence: int
    content_digest: bytes = field(repr=False)
    last_touched: float
    status: str = "admitted"
    cached_final: Optional[Dict[str, Any]] = field(default=None, repr=False)


@dataclass
class RoomBridgeScopeLedger:
    """Bounded at-most-once state that survives bearer-grant rotation."""

    host_owner_key: str
    room_id: str
    room_epoch: str
    conversation_epoch: str
    last_touched: float
    last_sequence: int = 0
    seen_events: "OrderedDict[str, RoomBridgeEventOutcome]" = field(
        default_factory=OrderedDict,
        repr=False,
    )
    seen_message_ids: "OrderedDict[str, str]" = field(
        default_factory=OrderedDict,
        repr=False,
    )


@dataclass(frozen=True)
class RoomBridgeRevocation:
    grant: RoomBridgeGrant
    reason: str
    cancelled_tasks: Tuple[Any, ...] = ()


class RoomBridgeGrantStore:
    """In-memory bearer grants plus bounded conversation-scope replay state.

    Both server integrations call this class on their asyncio event-loop
    thread. No client header field or conversation epoch participates in
    transport authorization; only the captured bound owner and bearer do.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = ROOM_BRIDGE_DEFAULT_TTL_SECONDS,
        dedupe_ttl_seconds: float = ROOM_BRIDGE_DEDUPE_TTL_SECONDS,
        now: Optional[Callable[[], float]] = None,
        monotonic_now: Optional[Callable[[], float]] = None,
    ) -> None:
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.dedupe_ttl_seconds = max(self.ttl_seconds, float(dedupe_ttl_seconds))
        self._wall_now = now or time.time
        # Authorization, replay retention, and rate windows always use a
        # rollback-proof duration clock unless a dedicated monotonic test clock
        # is supplied. Injecting a wall clock must never weaken that invariant.
        self._monotonic_now = monotonic_now or time.monotonic
        self._grants: Dict[str, RoomBridgeGrant] = {}
        self._owner_index: Dict[str, str] = {}
        self._grant_request_rates: "OrderedDict[str, Deque[float]]" = OrderedDict()
        self._chat_rates: "OrderedDict[str, Deque[float]]" = OrderedDict()
        self._scope_ledgers: "OrderedDict[Tuple[str, str, str, str], RoomBridgeScopeLedger]" = (
            OrderedDict()
        )
        self._dedupe_index: "OrderedDict[Tuple[Tuple[str, str, str, str], str], None]" = (
            OrderedDict()
        )
        self._next_revision = 1

    @staticmethod
    def _scope_key(
        host_owner_key: str,
        room_id: str,
        room_epoch: str,
        conversation_epoch: str,
    ) -> Tuple[str, str, str, str]:
        return (host_owner_key, room_id, room_epoch, conversation_epoch)

    @staticmethod
    def _grant_scope_key(grant: RoomBridgeGrant) -> Tuple[str, str, str, str]:
        return (
            grant.host_owner_key,
            grant.room_id,
            grant.room_epoch,
            grant.conversation_epoch,
        )

    def _drop_scope(self, scope_key: Tuple[str, str, str, str]) -> None:
        ledger = self._scope_ledgers.pop(scope_key, None)
        if ledger is None:
            return
        for event_id in tuple(ledger.seen_events):
            self._dedupe_index.pop((scope_key, event_id), None)

    def _prune_scope_ledgers(self, now: float) -> None:
        active_scope_keys = {self._grant_scope_key(grant) for grant in self._grants.values()}
        for scope_key, ledger in list(self._scope_ledgers.items()):
            for event_id, outcome in list(ledger.seen_events.items()):
                if (
                    outcome.status != "admitted"
                    and now - outcome.last_touched >= self.dedupe_ttl_seconds
                ):
                    self._remove_outcome(ledger, event_id)
            if scope_key in active_scope_keys:
                continue
            if now - ledger.last_touched >= self.dedupe_ttl_seconds:
                self._drop_scope(scope_key)

    def _scope_ledger(
        self,
        *,
        host_owner_key: str,
        room_id: str,
        room_epoch: str,
        conversation_epoch: str,
        now: float,
    ) -> RoomBridgeScopeLedger:
        self._prune_scope_ledgers(now)
        scope_key = self._scope_key(
            host_owner_key,
            room_id,
            room_epoch,
            conversation_epoch,
        )
        ledger = self._scope_ledgers.get(scope_key)
        if ledger is not None:
            ledger.last_touched = now
            self._scope_ledgers.move_to_end(scope_key)
            return ledger

        active_scope_keys = {self._grant_scope_key(grant) for grant in self._grants.values()}
        while len(self._scope_ledgers) >= ROOM_BRIDGE_SCOPE_LIMIT:
            evictable = next(
                (candidate for candidate in self._scope_ledgers if candidate not in active_scope_keys),
                None,
            )
            if evictable is None:
                raise RoomBridgeError(
                    "bridge_capacity",
                    "room bridge conversation capacity is temporarily full",
                )
            self._drop_scope(evictable)

        ledger = RoomBridgeScopeLedger(
            host_owner_key=host_owner_key,
            room_id=room_id,
            room_epoch=room_epoch,
            conversation_epoch=conversation_epoch,
            last_touched=now,
        )
        self._scope_ledgers[scope_key] = ledger
        return ledger

    def _touch_outcome(
        self,
        ledger: RoomBridgeScopeLedger,
        event_id: str,
        now: float,
    ) -> None:
        scope_key = self._scope_key(
            ledger.host_owner_key,
            ledger.room_id,
            ledger.room_epoch,
            ledger.conversation_epoch,
        )
        ledger.last_touched = now
        outcome = ledger.seen_events.get(event_id)
        if outcome is not None:
            outcome.last_touched = now
            ledger.seen_events.move_to_end(event_id)
        if scope_key in self._scope_ledgers:
            self._scope_ledgers.move_to_end(scope_key)
        dedupe_key = (scope_key, event_id)
        if dedupe_key in self._dedupe_index:
            self._dedupe_index.move_to_end(dedupe_key)

    def _remove_outcome(self, ledger: RoomBridgeScopeLedger, event_id: str) -> bool:
        outcome = ledger.seen_events.get(event_id)
        if outcome is None or outcome.status == "admitted":
            return False
        ledger.seen_events.pop(event_id, None)
        ledger.seen_message_ids.pop(outcome.request_message_id, None)
        scope_key = self._scope_key(
            ledger.host_owner_key,
            ledger.room_id,
            ledger.room_epoch,
            ledger.conversation_epoch,
        )
        self._dedupe_index.pop((scope_key, event_id), None)
        return True

    def _reserve_outcome_capacity(self, ledger: RoomBridgeScopeLedger) -> None:
        while len(ledger.seen_events) >= ROOM_BRIDGE_DEDUPE_LIMIT:
            evictable = next(
                (
                    event_id
                    for event_id, outcome in ledger.seen_events.items()
                    if outcome.status != "admitted"
                ),
                None,
            )
            if evictable is None or not self._remove_outcome(ledger, evictable):
                raise RoomBridgeError(
                    "bridge_capacity",
                    "room bridge dedupe capacity is temporarily full",
                )

        while len(self._dedupe_index) >= ROOM_BRIDGE_TOTAL_DEDUPE_LIMIT:
            evicted = False
            for scope_key, event_id in tuple(self._dedupe_index):
                candidate_ledger = self._scope_ledgers.get(scope_key)
                if candidate_ledger is not None and self._remove_outcome(
                    candidate_ledger,
                    event_id,
                ):
                    evicted = True
                    break
            if not evicted:
                raise RoomBridgeError(
                    "bridge_capacity",
                    "room bridge dedupe capacity is temporarily full",
                )

    @staticmethod
    def _cached_final_for_grant(
        cached_final: Mapping[str, Any],
        grant: RoomBridgeGrant,
    ) -> Dict[str, Any]:
        """Rebind cached output to the newly authenticated bearer grant.

        Stable response/event identity and content are retained, while stale
        grant metadata and Computer membership are replaced with the live
        grant. An old revoked bearer therefore never becomes acceptable again.
        """
        rebound = copy.deepcopy(dict(cached_final))
        metadata = rebound.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        bridge = metadata.get("room_bridge")
        bridge = dict(bridge) if isinstance(bridge, Mapping) else {}
        bridge.update(
            {
                "protocol": ROOM_BRIDGE_PROTOCOL,
                "grant_id": grant.grant_id,
                "grant_revision": grant.grant_revision,
                "room_id": grant.room_id,
                "room_epoch": grant.room_epoch,
                "conversation_epoch": grant.conversation_epoch,
                "origin": dict(grant.computer_member),
                "computer_member": dict(grant.computer_member),
            }
        )
        bridge.pop("grant_token", None)
        metadata["room_bridge"] = bridge
        rebound["metadata"] = metadata
        return rebound

    def issue(
        self,
        *,
        trusted_transport_id: str,
        host_owner_key: str,
        server_identity_key: str,
        server_name: str,
        room_id: Any,
        room_epoch: Any,
        conversation_epoch: Any,
        permissions: Any,
    ) -> RoomBridgeIssue:
        transport = str(trusted_transport_id or "").strip()
        owner = require_bound_owner_key(host_owner_key)
        identity_key = str(server_identity_key or "").strip()
        if not transport or not owner or not identity_key:
            raise RoomBridgeError("unauthorized_transport", "authenticated transport is required")
        normalized_room_id = normalize_room_id(room_id)
        normalized_epoch = normalize_room_epoch(room_epoch)
        normalized_conversation_epoch = normalize_conversation_epoch(conversation_epoch)
        normalized_permissions = normalize_permissions(permissions)
        wall_now = float(self._wall_now())
        now = float(self._monotonic_now())
        rate = self._grant_request_rates.setdefault(owner, deque())
        self._grant_request_rates.move_to_end(owner)
        while rate and now - rate[0] >= ROOM_BRIDGE_RATE_WINDOW_SECONDS:
            rate.popleft()
        if len(rate) >= ROOM_BRIDGE_GRANT_RATE_LIMIT:
            retry_seconds = ROOM_BRIDGE_RATE_WINDOW_SECONDS - (now - rate[0])
            raise RoomBridgeError(
                "grant_rate_limited",
                "room bridge grant request rate exceeded",
                retry_after_ms=max(1, int(retry_seconds * 1000)),
            )
        rate.append(now)
        while len(self._grant_request_rates) > ROOM_BRIDGE_GRANT_RATE_OWNER_LIMIT:
            self._grant_request_rates.popitem(last=False)

        scope_ledger = self._scope_ledger(
            host_owner_key=owner,
            room_id=normalized_room_id,
            room_epoch=normalized_epoch,
            conversation_epoch=normalized_conversation_epoch,
            now=now,
        )

        superseded = []
        prior_id = self._owner_index.get(owner)
        if prior_id:
            prior = self._grants.get(prior_id)
            if prior is not None:
                superseded.append(self._revoke(prior, "superseded"))

        token = secrets.token_urlsafe(32)  # 32 random bytes = 256 bits.
        principal_id = derive_room_principal_id(
            server_identity_key=identity_key,
            host_owner_key=owner,
            room_id=normalized_room_id,
            room_epoch=normalized_epoch,
            conversation_epoch=normalized_conversation_epoch,
        )
        grant = RoomBridgeGrant(
            grant_id=f"rbg_{secrets.token_urlsafe(18)}",
            token_digest=_token_digest(token),
            host_owner_key=owner,
            current_transport_id=transport,
            room_id=normalized_room_id,
            room_epoch=normalized_epoch,
            conversation_epoch=normalized_conversation_epoch,
            room_principal_id=principal_id,
            permissions=normalized_permissions,
            grant_revision=self._next_revision,
            computer_member=build_computer_member(
                server_identity_key=identity_key,
                server_name=server_name,
                joined_at=int(wall_now),
            ),
            issued_at=wall_now,
            expires_at=wall_now + self.ttl_seconds,
            expires_at_monotonic=now + self.ttl_seconds,
            scope_ledger=scope_ledger,
        )
        self._next_revision += 1
        self._grants[grant.grant_id] = grant
        self._owner_index[owner] = grant.grant_id
        return RoomBridgeIssue(grant=grant, grant_token=token, superseded_grants=tuple(superseded))

    def _require_active(
        self,
        *,
        grant_id: Any,
        grant_token: Any,
        trusted_transport_id: Any,
        trusted_owner_key: Any,
        room_id: Any,
        room_epoch: Any,
        conversation_epoch: Any,
        grant_revision: Any,
    ) -> RoomBridgeGrant:
        normalized_grant_id = grant_id.strip() if isinstance(grant_id, str) else ""
        if re.fullmatch(r"rbg_[A-Za-z0-9_-]{24}", normalized_grant_id) is None:
            raise RoomBridgeError("invalid_grant", "room bridge grant is not active")
        grant = self._grants.get(normalized_grant_id)
        if grant is None:
            raise RoomBridgeError("invalid_grant", "room bridge grant is not active")
        normalized_token = grant_token.strip() if isinstance(grant_token, str) else ""
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", normalized_token) is None or not hmac.compare_digest(
            grant.token_digest,
            _token_digest(normalized_token),
        ):
            raise RoomBridgeError("invalid_grant", "room bridge grant is not active")
        transport = str(trusted_transport_id or "").strip()
        owner = require_bound_owner_key(trusted_owner_key)
        if not transport or not owner or owner != grant.host_owner_key:
            raise RoomBridgeError("owner_mismatch", "grant belongs to another authenticated owner")
        if float(self._monotonic_now()) >= grant.expires_at_monotonic:
            revocation = self._revoke(grant, "expired")
            for task in revocation.cancelled_tasks:
                task.cancel()
            raise RoomBridgeError(
                "grant_expired",
                "room bridge grant has expired",
                grant_context=grant,
            )
        try:
            normalized_room_id = normalize_room_id(room_id)
            normalized_room_epoch = normalize_room_epoch(room_epoch)
            normalized_conversation_epoch = normalize_conversation_epoch(conversation_epoch)
        except RoomBridgeError as exc:
            exc.grant_context = grant
            raise
        if normalized_room_id != grant.room_id or normalized_room_epoch != grant.room_epoch:
            raise RoomBridgeError(
                "room_binding_mismatch",
                "grant does not match this room epoch",
                grant_context=grant,
            )
        if normalized_conversation_epoch != grant.conversation_epoch:
            raise RoomBridgeError(
                "conversation_binding_mismatch",
                "grant does not match this conversation epoch",
                grant_context=grant,
            )
        revision = grant_revision if isinstance(grant_revision, int) and not isinstance(grant_revision, bool) else 0
        if revision != grant.grant_revision:
            raise RoomBridgeError(
                "grant_revision_mismatch",
                "grant revision is stale",
                grant_context=grant,
            )
        return grant

    def resolve_error_grant_context(
        self,
        *,
        error: RoomBridgeError,
        bridge_metadata: Any,
        trusted_transport_id: str,
        trusted_owner_key: str,
    ) -> Optional[RoomBridgeGrant]:
        """Return correlation fields only after bearer and owner authorization.

        Admission can fail after the grant was authenticated (for example, a
        malformed origin or rate limit). Re-validating here lets both server
        integrations produce one strict error envelope without trusting fields
        copied from the peer. Expired/binding errors carry the already-verified
        grant directly because the expiry path removes it from the live store.
        """
        context = getattr(error, "grant_context", None)
        if isinstance(context, RoomBridgeGrant):
            return context
        if not isinstance(bridge_metadata, Mapping):
            return None
        try:
            return self._require_active(
                grant_id=bridge_metadata.get("grant_id"),
                grant_token=bridge_metadata.get("grant_token"),
                trusted_transport_id=trusted_transport_id,
                trusted_owner_key=trusted_owner_key,
                room_id=bridge_metadata.get("room_id"),
                room_epoch=bridge_metadata.get("room_epoch"),
                conversation_epoch=bridge_metadata.get("conversation_epoch"),
                grant_revision=bridge_metadata.get("grant_revision"),
            )
        except RoomBridgeError as authorization_error:
            authorized_context = getattr(authorization_error, "grant_context", None)
            return authorized_context if isinstance(authorized_context, RoomBridgeGrant) else None

    def admit_chat(
        self,
        *,
        bridge_metadata: Any,
        trusted_transport_id: str,
        trusted_owner_key: str,
        request_message_id: Any,
        message: Any,
        task: Any = None,
    ) -> RoomBridgeAdmission:
        if not isinstance(bridge_metadata, Mapping):
            raise RoomBridgeError("invalid_bridge_metadata", "metadata.room_bridge is required")
        if str(bridge_metadata.get("protocol") or "") != ROOM_BRIDGE_PROTOCOL:
            raise RoomBridgeError("unsupported_protocol", "unsupported room bridge protocol")
        grant = self._require_active(
            grant_id=bridge_metadata.get("grant_id"),
            grant_token=bridge_metadata.get("grant_token"),
            trusted_transport_id=trusted_transport_id,
            trusted_owner_key=trusted_owner_key,
            room_id=bridge_metadata.get("room_id"),
            room_epoch=bridge_metadata.get("room_epoch"),
            conversation_epoch=bridge_metadata.get("conversation_epoch"),
            grant_revision=bridge_metadata.get("grant_revision"),
        )
        if "chat" not in grant.permissions:
            raise RoomBridgeError("permission_denied", "grant does not permit chat")
        if not isinstance(message, str):
            raise RoomBridgeError("invalid_message", "payload.message must be a string")
        body = message
        if not body.strip():
            raise RoomBridgeError("empty_message", "room chat message is empty")
        if len(body) > ROOM_BRIDGE_MAX_MESSAGE_CHARS:
            raise RoomBridgeError(
                "message_too_large",
                f"room chat message exceeds {ROOM_BRIDGE_MAX_MESSAGE_CHARS} characters",
            )
        origin = normalize_room_origin(bridge_metadata.get("origin"))
        content_digest = _event_content_digest(body, origin)
        event_id = _bounded_opaque(
            bridge_metadata.get("room_event_id"),
            field_name="room_event_id",
            minimum=8,
        )
        raw_sequence = bridge_metadata.get("sequence")
        sequence = raw_sequence if isinstance(raw_sequence, int) and not isinstance(raw_sequence, bool) else 0
        if not 1 <= sequence <= 2**63 - 1:
            raise RoomBridgeError("invalid_sequence", "sequence must be a positive integer")

        request_id = normalize_message_id(request_message_id)
        if request_id != event_id:
            raise RoomBridgeError(
                "message_event_mismatch",
                "CHAT header.message_id must equal metadata.room_bridge.room_event_id",
            )
        now = float(self._monotonic_now())
        self._prune_scope_ledgers(now)
        ledger = grant.scope_ledger
        event_for_message = ledger.seen_message_ids.get(request_id)
        if event_for_message is not None and event_for_message != event_id:
            raise RoomBridgeError("message_conflict", "message_id was reused for another room event")

        outcome = ledger.seen_events.get(event_id)
        if outcome is not None and (
            outcome.sequence != sequence
            or outcome.request_message_id != request_id
            or not hmac.compare_digest(outcome.content_digest, content_digest)
        ):
            raise RoomBridgeError("event_conflict", "room_event_id was reused with different content")

        displaced_tasks: Tuple[Any, ...] = ()
        normalized_transport = str(trusted_transport_id or "").strip()
        if grant.current_transport_id != normalized_transport:
            displaced_tasks = tuple(
                pending
                for pending in grant.inflight_tasks
                if not getattr(pending, "done", lambda: False)()
            )
            for pending_event_id in tuple(grant.inflight_events):
                pending_outcome = ledger.seen_events.get(pending_event_id)
                if pending_outcome is not None and pending_outcome.status == "admitted":
                    pending_outcome.status = "cancelled"
                    self._touch_outcome(ledger, pending_event_id, now)
            grant.inflight_tasks.clear()
            grant.inflight_events.clear()
            grant.current_transport_id = normalized_transport
            grant.transport_generation += 1

        if outcome is not None:
            self._touch_outcome(ledger, event_id, now)
            return RoomBridgeAdmission(
                grant=grant,
                request_message_id=request_id,
                room_event_id=event_id,
                sequence=sequence,
                origin=origin,
                transport_generation=grant.transport_generation,
                duplicate=True,
                outcome_status=outcome.status,
                cached_final=(
                    self._cached_final_for_grant(outcome.cached_final, grant)
                    if outcome.cached_final is not None
                    else None
                ),
                displaced_tasks=displaced_tasks,
            )
        if sequence <= ledger.last_sequence:
            raise RoomBridgeError("stale_sequence", "sequence is older than the admitted room stream")

        owner_rate = self._chat_rates.setdefault(grant.host_owner_key, deque())
        self._chat_rates.move_to_end(grant.host_owner_key)
        while owner_rate and now - owner_rate[0] >= ROOM_BRIDGE_RATE_WINDOW_SECONDS:
            owner_rate.popleft()
        if len(owner_rate) >= ROOM_BRIDGE_RATE_LIMIT:
            retry_seconds = ROOM_BRIDGE_RATE_WINDOW_SECONDS - (now - owner_rate[0])
            raise RoomBridgeError(
                "rate_limited",
                "room bridge chat rate limit exceeded",
                retry_after_ms=max(1, int(retry_seconds * 1000)),
            )
        while len(self._chat_rates) > ROOM_BRIDGE_GRANT_RATE_OWNER_LIMIT:
            self._chat_rates.popitem(last=False)
        if len(grant.inflight_events) >= ROOM_BRIDGE_MAX_INFLIGHT:
            raise RoomBridgeError("too_many_inflight", "room bridge has too many active turns")
        self._reserve_outcome_capacity(ledger)

        outcome = RoomBridgeEventOutcome(
            room_event_id=event_id,
            request_message_id=request_id,
            sequence=sequence,
            content_digest=content_digest,
            last_touched=now,
        )
        ledger.seen_events[event_id] = outcome
        ledger.seen_message_ids[request_id] = event_id
        scope_key = self._grant_scope_key(grant)
        self._dedupe_index[(scope_key, event_id)] = None
        ledger.last_sequence = sequence
        owner_rate.append(now)
        ledger.last_touched = now
        self._scope_ledgers.move_to_end(scope_key)
        grant.inflight_events.add(event_id)
        if task is not None:
            grant.inflight_tasks.add(task)
        return RoomBridgeAdmission(
            grant=grant,
            request_message_id=request_id,
            room_event_id=event_id,
            sequence=sequence,
            origin=origin,
            transport_generation=grant.transport_generation,
            displaced_tasks=displaced_tasks,
        )

    def finish_chat(self, admission: RoomBridgeAdmission, *, task: Any = None) -> None:
        grant = admission.grant
        grant.inflight_events.discard(admission.room_event_id)
        if task is not None:
            grant.inflight_tasks.discard(task)

    def track_provider_task(self, admission: RoomBridgeAdmission, task: Any) -> bool:
        """Attach the execution-manager provider task to grant revocation."""
        grant = self._grants.get(admission.grant.grant_id)
        outcome = (
            grant.scope_ledger.seen_events.get(admission.room_event_id)
            if grant is not None
            else None
        )
        if (
            task is None
            or grant is None
            or grant.grant_revision != admission.grant.grant_revision
            or grant.transport_generation != admission.transport_generation
            or outcome is None
            or outcome.status != "admitted"
            or float(self._monotonic_now()) >= grant.expires_at_monotonic
        ):
            return False
        grant.inflight_tasks.add(task)
        return True

    @staticmethod
    def untrack_provider_task(admission: RoomBridgeAdmission, task: Any) -> None:
        if task is not None:
            admission.grant.inflight_tasks.discard(task)

    def abandon_chat(self, admission: RoomBridgeAdmission, *, task: Any = None) -> None:
        """Close an admitted turn that exited without committing a final reply."""
        ledger = admission.grant.scope_ledger
        outcome = ledger.seen_events.get(admission.room_event_id)
        if outcome is not None and outcome.status == "admitted":
            outcome.status = "cancelled"
            self._touch_outcome(ledger, admission.room_event_id, float(self._monotonic_now()))
        self.finish_chat(admission, task=task)

    def cache_final(self, admission: RoomBridgeAdmission, final_message: Mapping[str, Any], *, task: Any = None) -> bool:
        """Commit a final reply before attempting live delivery.

        Returns False if revoke, expiry, or a transport generation change made
        this completion stale. The caller must then suppress delivery.
        """
        grant = self._grants.get(admission.grant.grant_id)
        if (
            grant is None
            or grant.grant_revision != admission.grant.grant_revision
            or grant.transport_generation != admission.transport_generation
            or float(self._monotonic_now()) >= grant.expires_at_monotonic
        ):
            return False
        ledger = grant.scope_ledger
        outcome = ledger.seen_events.get(admission.room_event_id)
        if outcome is None or outcome.status != "admitted":
            return False
        outcome.status = "completed"
        outcome.cached_final = copy.deepcopy(dict(final_message))
        self._touch_outcome(ledger, admission.room_event_id, float(self._monotonic_now()))
        self.finish_chat(admission, task=task)
        return True

    def admission_is_current(self, admission: RoomBridgeAdmission) -> bool:
        grant = self._grants.get(admission.grant.grant_id)
        return bool(
            grant is not None
            and grant.grant_revision == admission.grant.grant_revision
            and grant.transport_generation == admission.transport_generation
            and float(self._monotonic_now()) < grant.expires_at_monotonic
        )

    def is_active(self, grant_id: str, grant_revision: int) -> bool:
        grant = self._grants.get(str(grant_id or ""))
        if grant is None or grant.grant_revision != int(grant_revision or 0):
            return False
        if float(self._monotonic_now()) >= grant.expires_at_monotonic:
            revocation = self._revoke(grant, "expired")
            for task in revocation.cancelled_tasks:
                task.cancel()
            return False
        return True

    def revoke_authorized(
        self,
        *,
        grant_id: Any,
        grant_token: Any,
        trusted_transport_id: str,
        trusted_owner_key: str,
        room_id: Any,
        room_epoch: Any,
        conversation_epoch: Any,
        grant_revision: Any,
        reason: str = "client_revoked",
    ) -> RoomBridgeRevocation:
        grant = self._require_active(
            grant_id=grant_id,
            grant_token=grant_token,
            trusted_transport_id=trusted_transport_id,
            trusted_owner_key=trusted_owner_key,
            room_id=room_id,
            room_epoch=room_epoch,
            conversation_epoch=conversation_epoch,
            grant_revision=grant_revision,
        )
        return self._revoke(grant, reason)

    def expire(self, grant_id: str, grant_revision: int) -> Optional[RoomBridgeRevocation]:
        grant = self._grants.get(str(grant_id or ""))
        if grant is None or grant.grant_revision != int(grant_revision or 0):
            return None
        if float(self._monotonic_now()) < grant.expires_at_monotonic:
            return None
        return self._revoke(grant, "expired")

    def seconds_until_expiry(self, grant_id: str, grant_revision: int) -> Optional[float]:
        """Return a monotonic scheduling delay, or None for a stale grant."""
        grant = self._grants.get(str(grant_id or ""))
        if grant is None or grant.grant_revision != int(grant_revision or 0):
            return None
        return max(0.0, grant.expires_at_monotonic - float(self._monotonic_now()))

    def grant_for_transport(self, trusted_transport_id: str) -> Optional[RoomBridgeGrant]:
        """The live grant bound to one transport, if there is one.

        Read-only, and deliberately public: the call listener needs to know
        which room a stream of audio belongs to, and reaching into ``_grants``
        from a server to find out would put grant lookup in two places.

        Returns ``None`` for an unknown or suspended transport, so a caller that
        asks about every audio frame gets a cheap negative rather than an
        exception on the call's hot path.
        """
        transport = str(trusted_transport_id or "").strip()
        if not transport:
            return None
        for grant in self._grants.values():
            if (grant.current_transport_id == transport
                    and float(self._monotonic_now()) < grant.expires_at_monotonic):
                return grant
        return None

    def revoke_transport(self, trusted_transport_id: str, *, reason: str) -> Tuple[RoomBridgeRevocation, ...]:
        transport = str(trusted_transport_id or "").strip()
        if not transport:
            return ()
        matches = [grant for grant in self._grants.values() if grant.current_transport_id == transport]
        return tuple(self._revoke(grant, reason) for grant in matches)

    def suspend_transport(self, trusted_transport_id: str) -> Tuple[RoomBridgeRevocation, ...]:
        """Cancel work tied to a dead channel while preserving replay outcomes."""
        transport = str(trusted_transport_id or "").strip()
        if not transport:
            return ()
        suspended = []
        for grant in list(self._grants.values()):
            if grant.current_transport_id != transport:
                continue
            tasks = tuple(task for task in grant.inflight_tasks if not getattr(task, "done", lambda: False)())
            for event_id in tuple(grant.inflight_events):
                outcome = grant.scope_ledger.seen_events.get(event_id)
                if outcome is not None and outcome.status == "admitted":
                    outcome.status = "cancelled"
                    self._touch_outcome(
                        grant.scope_ledger,
                        event_id,
                        float(self._monotonic_now()),
                    )
            grant.inflight_tasks.clear()
            grant.inflight_events.clear()
            grant.transport_generation += 1
            grant.current_transport_id = ""
            suspended.append(
                RoomBridgeRevocation(
                    grant=grant,
                    reason="transport_disconnected",
                    cancelled_tasks=tasks,
                )
            )
        return tuple(suspended)

    def _revoke(self, grant: RoomBridgeGrant, reason: str) -> RoomBridgeRevocation:
        self._grants.pop(grant.grant_id, None)
        if self._owner_index.get(grant.host_owner_key) == grant.grant_id:
            self._owner_index.pop(grant.host_owner_key, None)
        tasks = tuple(task for task in grant.inflight_tasks if not getattr(task, "done", lambda: False)())
        now = float(self._monotonic_now())
        for event_id in tuple(grant.inflight_events):
            outcome = grant.scope_ledger.seen_events.get(event_id)
            if outcome is not None and outcome.status == "admitted":
                outcome.status = "cancelled"
                self._touch_outcome(grant.scope_ledger, event_id, now)
        grant.inflight_tasks.clear()
        grant.inflight_events.clear()
        return RoomBridgeRevocation(grant=grant, reason=str(reason or "revoked"), cancelled_tasks=tasks)


def granted_control_payload(issue: RoomBridgeIssue, *, request_message_id: str) -> Dict[str, Any]:
    grant = issue.grant
    return {
        "protocol": ROOM_BRIDGE_PROTOCOL,
        "event": "granted",
        "request_message_id": str(request_message_id or ""),
        "grant_id": grant.grant_id,
        "grant_token": issue.grant_token,
        "grant_revision": grant.grant_revision,
        "room_id": grant.room_id,
        "room_epoch": grant.room_epoch,
        "conversation_epoch": grant.conversation_epoch,
        "permissions": list(grant.permissions),
        "computer_mode": ROOM_BRIDGE_COMPUTER_MODE,
        "origin_trust": ROOM_BRIDGE_ORIGIN_TRUST,
        "issued_at_ms": int(grant.issued_at * 1000),
        "expires_at_ms": int(grant.expires_at * 1000),
        "computer_member": dict(grant.computer_member),
        "limits": {
            "message_chars": ROOM_BRIDGE_MAX_MESSAGE_CHARS,
            "messages_per_minute": ROOM_BRIDGE_RATE_LIMIT,
            "max_inflight": ROOM_BRIDGE_MAX_INFLIGHT,
            "dedupe_events": ROOM_BRIDGE_DEDUPE_LIMIT,
            "grant_requests_per_minute": ROOM_BRIDGE_GRANT_RATE_LIMIT,
        },
    }


def chat_ack_control_payload(admission: RoomBridgeAdmission) -> Dict[str, Any]:
    grant = admission.grant
    return {
        "protocol": ROOM_BRIDGE_PROTOCOL,
        "event": "room_chat_ack",
        "request_message_id": admission.request_message_id,
        "grant_id": grant.grant_id,
        "grant_revision": grant.grant_revision,
        "room_id": grant.room_id,
        "room_epoch": grant.room_epoch,
        "conversation_epoch": grant.conversation_epoch,
        "room_event_id": admission.room_event_id,
        "sequence": admission.sequence,
        "origin_trust": ROOM_BRIDGE_ORIGIN_TRUST,
        "accepted": True,
        "duplicate": bool(admission.duplicate),
    }


def revoked_control_payload(revocation: RoomBridgeRevocation) -> Dict[str, Any]:
    grant = revocation.grant
    return {
        "protocol": ROOM_BRIDGE_PROTOCOL,
        "event": "revoked",
        "grant_id": grant.grant_id,
        "grant_revision": grant.grant_revision,
        "room_id": grant.room_id,
        "room_epoch": grant.room_epoch,
        "conversation_epoch": grant.conversation_epoch,
        "reason": revocation.reason,
    }


#: The only shape a presence row may take. Anything else means this process and
#: the participants' screens disagree about what is in the call.
PRESENCE_CONTRACT = {
    "capabilities": ["chat"],
    "hears_audio": False,
    "speaks_audio": False,
    "records": False,
}


def assert_presence_within_contract(presence: Mapping[str, Any]) -> Dict[str, Any]:
    """Refuse to publish a row that claims more than chat.

    The clients already reject an out-of-contract row rather than render a
    reassuring claim that is wrong. This is the other half: a row that overstates
    what the Computer can do must not leave this process either, so a bug here
    cannot become a promise on somebody's screen.
    """
    for field, expected in PRESENCE_CONTRACT.items():
        if presence.get(field) != expected:
            raise RoomBridgeError(
                "invalid_presence",
                f"presence.{field} must be {expected!r}",
            )
    notice = str(presence.get("notice") or "").strip()
    if not notice:
        raise RoomBridgeError(
            "invalid_presence",
            "presence must carry the notice shown to participants",
        )
    if str(presence.get("role") or "") != "computer":
        raise RoomBridgeError("invalid_presence", "presence.role must be 'computer'")
    return dict(presence)


def presence_control_payload(
    grant: RoomBridgeGrant, presence: Mapping[str, Any]
) -> Dict[str, Any]:
    """The Computer's participant row, addressed to one grant's scope.

    Carries the same scope fields every other control payload does, so a client
    can bind it to the grant it already holds and ignore a row aimed at a room,
    epoch or conversation it is not in.
    """
    return {
        "protocol": ROOM_BRIDGE_PROTOCOL,
        "event": "presence",
        "grant_id": grant.grant_id,
        "grant_revision": grant.grant_revision,
        "room_id": grant.room_id,
        "room_epoch": grant.room_epoch,
        "conversation_epoch": grant.conversation_epoch,
        "presence": assert_presence_within_contract(presence),
    }


#: What a note may be about. A listener speaks for two reasons and no others,
#: and naming them keeps the surface closed rather than open-ended.
NOTE_KINDS = ("reply", "summary")


def note_control_payload(
    grant: RoomBridgeGrant, text: Any, *, kind: str = "reply"
) -> Dict[str, Any]:
    """Something the Computer said during a call, addressed to one grant's scope.

    Why this exists as its own event rather than as a chat reply: a room chat
    reply must correlate to a turn the host sent, and the client enforces that -
    it is what stops anything on the bridge injecting into a room. A listener
    has no such turn to answer, so routing its replies through that path would
    have every one of them dropped.

    What authorises a note instead is the grant itself. The host asked for a
    Computer with ``chat`` permission and put it in a call; this is that
    Computer speaking within exactly that permission. The note carries the same
    scope fields as every other control payload, so a client binds it to the
    grant it holds and ignores one aimed at a room, epoch or conversation it is
    not in - the same shape ``presence`` already uses.

    What it deliberately is not: a way to say anything else. The text has
    already passed :func:`shared.room_call_listener.strip_internal_reasoning`
    and the reply interval before it reaches here, and is bounded again on the
    way out because a payload builder should not trust its caller.
    """
    if "chat" not in tuple(grant.permissions or ()):
        raise RoomBridgeError(
            "permission_denied", "a note needs the chat permission"
        )
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in NOTE_KINDS:
        raise RoomBridgeError("invalid_note", f"unknown note kind {kind!r}")

    # Checked before bounding, not after: `bound_room_bridge_reply` substitutes
    # "(no response)" for empty input, which is right for a reply somebody asked
    # for and wrong here - an unprompted "(no response)" bubble in a call is
    # worse than the silence it replaced.
    if not str(text or "").strip():
        raise RoomBridgeError("invalid_note", "a note needs text")
    body = bound_room_bridge_reply(text).strip()

    return {
        "protocol": ROOM_BRIDGE_PROTOCOL,
        "event": "note",
        "kind": normalized_kind,
        "grant_id": grant.grant_id,
        "grant_revision": grant.grant_revision,
        "room_id": grant.room_id,
        "room_epoch": grant.room_epoch,
        "conversation_epoch": grant.conversation_epoch,
        "computer_member": dict(grant.computer_member),
        "message": body,
    }


def error_control_payload(
    error: RoomBridgeError,
    *,
    request_message_id: str,
    grant_id: Any = "",
    grant_context: Optional[RoomBridgeGrant] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "protocol": ROOM_BRIDGE_PROTOCOL,
        "event": "error",
        "request_message_id": str(request_message_id or ""),
        "code": error.code,
        "message": error.public_message,
    }
    if isinstance(grant_context, RoomBridgeGrant):
        payload.update(
            {
                "grant_id": grant_context.grant_id,
                "grant_revision": grant_context.grant_revision,
                "room_id": grant_context.room_id,
                "room_epoch": grant_context.room_epoch,
                "conversation_epoch": grant_context.conversation_epoch,
            }
        )
    else:
        normalized_grant_id = str(grant_id or "").strip()
        if re.fullmatch(r"rbg_[A-Za-z0-9_-]{24}", normalized_grant_id):
            payload["grant_id"] = normalized_grant_id
    if error.retry_after_ms:
        payload["retry_after_ms"] = error.retry_after_ms
    return payload


def final_reply_room_metadata(admission: RoomBridgeAdmission) -> Dict[str, Any]:
    """Safe metadata for the unsequenced Computer reply returned to the host."""
    grant = admission.grant
    computer_member = dict(grant.computer_member)
    return {
        "protocol": ROOM_BRIDGE_PROTOCOL,
        "grant_id": grant.grant_id,
        "grant_revision": grant.grant_revision,
        "room_id": grant.room_id,
        "room_epoch": grant.room_epoch,
        "conversation_epoch": grant.conversation_epoch,
        "room_event_id": f"computer_{secrets.token_urlsafe(18)}",
        "in_reply_to_event_id": admission.room_event_id,
        "in_reply_to_sequence": admission.sequence,
        "host_sequence_required": True,
        "computer_mode": ROOM_BRIDGE_COMPUTER_MODE,
        "origin": computer_member,
        "computer_member": computer_member,
    }


def cancel_revoked_tasks(revocations: Iterable[RoomBridgeRevocation], *, current_task: Any = None) -> int:
    """Cancel admitted turns invalidated by revoke, expiry, or disconnect."""
    cancelled = 0
    for revocation in revocations:
        for task in revocation.cancelled_tasks:
            if task is current_task or getattr(task, "done", lambda: False)():
                continue
            task.cancel()
            cancelled += 1
    return cancelled
