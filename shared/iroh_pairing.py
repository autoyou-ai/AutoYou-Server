# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Consume the result of an existing pairing proof on the restricted ALPN.

The host issues a random redemption secret only after OTP/CPace/TOTP/account
authorization succeeds. This module is not a password verifier. An invitation
does not install a grant until its designated endpoint confirms a fresh TLS
transcript; it never activates an application channel on the pairing ALPN.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from shared.iroh_grants import EndpointGrantRegistry, PairedEndpoint
from shared.iroh_runtime import ConnectionContext, IrohSessionRuntime
from shared.session_transport import SessionDenied

PAIR_ALPN = "autoyou/pair/1"
PAIR_TTL_SECONDS = 60


@dataclass(frozen=True)
class _Invitation:
    grant: PairedEndpoint
    deadline: float
    redemption_id: bytes


@dataclass(frozen=True)
class _Redemption:
    invitation: _Invitation
    context: ConnectionContext
    digest: bytes


class VerifiedPairingRedemption:
    def __init__(self, *, grants: EndpointGrantRegistry, capabilities: dict[str, Any],
                 now: Callable[[], float] = time.monotonic) -> None:
        self.grants, self.capabilities, self.now = grants, capabilities, now
        self._issued: dict[bytes, _Invitation] = {}
        self._pending: dict[str, _Redemption] = {}
        self._gate = asyncio.Lock()

    def _prune(self) -> None:
        now = self.now()
        self._issued = {key: value for key, value in self._issued.items() if value.deadline > now}
        self._pending = {key: value for key, value in self._pending.items() if value.invitation.deadline > now}

    def issue_after_verified_proof(self, runtime: IrohSessionRuntime, grant: PairedEndpoint) -> dict[str, Any]:
        """Only a successful host-owned pairing entry point may call this."""
        grant.validate()
        runtime.api.validate_endpoint_id(grant.endpoint_id)
        if grant.expires_at_ms <= int(time.time() * 1000):
            raise SessionDenied("pairing authorization has expired")
        self._prune()
        if len(self._issued) >= 128:
            raise SessionDenied("pairing invitation capacity has been reached")
        secret = secrets.token_bytes(32)
        key = hashlib.sha256(secret).digest()
        self._issued[key] = _Invitation(grant, self.now() + PAIR_TTL_SECONDS, key)
        info = runtime.endpoint_info
        raw_grant = asdict(grant)
        raw_grant["scopes"] = sorted(grant.scopes)
        return {"version": 1, "endpoint_id": info.endpoint_id, "ticket": info.ticket,
            "redemption": base64.urlsafe_b64encode(secret).decode("ascii"),
            "redemption_expires_in_seconds": PAIR_TTL_SECONDS, "grant": raw_grant,
            "capabilities": self.capabilities}

    def cancel(self, redemption: str) -> bool:
        secret = decode_redemption(redemption)
        key = hashlib.sha256(secret).digest()
        changed = self._issued.pop(key, None) is not None
        for transport_id, pending in list(self._pending.items()):
            if secrets.compare_digest(pending.invitation.redemption_id, key):
                del self._pending[transport_id]
                changed = True
        return changed

    def cancel_device(self, device_id: str) -> None:
        self._issued = {key: invitation for key, invitation in self._issued.items()
                        if invitation.grant.device_id != device_id}
        self._pending = {key: pending for key, pending in self._pending.items()
                         if pending.invitation.grant.device_id != device_id}

    async def connected(self, _runtime: IrohSessionRuntime, context: ConnectionContext) -> None:
        if context.protocol != PAIR_ALPN or context.initiator:
            raise SessionDenied("pairing connection purpose was rejected")

    async def enrollment(self, runtime: IrohSessionRuntime, context: ConnectionContext, payload: bytes) -> None:
        if context.protocol != PAIR_ALPN or context.initiator:
            raise SessionDenied("pairing connection purpose was rejected")
        message = runtime.api.decode_enrollment(payload)
        async with self._gate:
            self._prune()
            if message.kind == runtime.api.EnrollmentKind.REDEEM:
                key = hashlib.sha256(bytes(message.binding)).digest()
                invitation = self._issued.get(key)
                if invitation is None or invitation.grant.endpoint_id != context.remote_endpoint_id or \
                        context.transport_id in self._pending or len(self._pending) >= 32:
                    raise SessionDenied("pairing redemption was rejected")
                del self._issued[key]  # atomic single use, even when confirmation fails
                grant = invitation.grant
                capabilities = dict(self.capabilities, device_id=grant.device_id)
                challenge = runtime.api.EnrollmentChallenge(initiator_endpoint=context.remote_endpoint_id,
                    acceptor_endpoint=context.local_endpoint_id, nonce=secrets.token_bytes(32), generation=1,
                    authorization_epoch=grant.authorization_epoch, expires_at_ms=grant.expires_at_ms,
                    scopes=sorted(grant.scopes), capabilities_json=json.dumps(capabilities,
                        allow_nan=False, separators=(",", ":")))
                digest = bytes(runtime.api.pairing_binding(challenge, context.exporter,
                    context.remote_endpoint_id, context.local_endpoint_id))
                self._pending[context.transport_id] = _Redemption(invitation, context, digest)
                runtime.send_enrollment(context, bytes(runtime.api.encode_enrollment(runtime.api.EnrollmentMessage(
                    kind=runtime.api.EnrollmentKind.CHALLENGE, challenge=challenge, binding=None))))
            elif message.kind == runtime.api.EnrollmentKind.CONFIRM:
                pending = self._pending.pop(context.transport_id, None)
                if pending is None or pending.context != context or \
                        not secrets.compare_digest(bytes(message.binding), pending.digest):
                    raise SessionDenied("pairing confirmation was rejected")
                await asyncio.to_thread(self.grants.register, pending.invitation.grant)
                runtime.send_enrollment(context, bytes(runtime.api.encode_enrollment(runtime.api.EnrollmentMessage(
                    kind=runtime.api.EnrollmentKind.READY, challenge=None, binding=pending.digest))))
            else:
                raise SessionDenied("unexpected pairing message")

    async def closed(self, context: ConnectionContext, *_args: Any) -> None:
        self._pending.pop(context.transport_id, None)

    def clear(self) -> None:
        self._issued.clear()
        self._pending.clear()


def decode_redemption(value: str) -> bytes:
    if not isinstance(value, str) or len(value) != 44:
        raise SessionDenied("invalid pairing redemption")
    try:
        secret = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeError):
        raise SessionDenied("invalid pairing redemption") from None
    if len(secret) != 32:
        raise SessionDenied("invalid pairing redemption")
    return secret


class ClientPairingRedemption:
    """One pending explicitly requested pairing; the UI owns proof verification."""
    def __init__(self, *, grants: EndpointGrantRegistry) -> None:
        self.grants = grants
        self._expected: dict[str, Any] | None = None
        self._pending: _Redemption | None = None
        self._future: asyncio.Future | None = None
        self._expiry_task: asyncio.Task | None = None

    def begin_after_verified_answer(self, runtime: IrohSessionRuntime, answer: dict[str, Any]) -> asyncio.Future:
        if self._expected is not None or (self._future and not self._future.done()):
            raise SessionDenied("another pairing is already pending")
        if not isinstance(answer, dict) or type(answer.get("version")) is not int or answer["version"] != 1:
            raise SessionDenied("unsupported pairing answer")
        raw = answer.get("grant")
        if not isinstance(raw, dict):
            raise SessionDenied("invalid pairing answer")
        # The server's grant names this client's endpoint, never an arbitrary one.
        if raw.get("endpoint_id") != runtime.endpoint_info.endpoint_id:
            raise SessionDenied("pairing answer does not name this endpoint")
        try:
            if not isinstance(raw.get("scopes"), list) or any(not isinstance(scope, str) for scope in raw["scopes"]):
                raise ValueError
            if not isinstance(answer.get("ticket"), str) or not 0 < len(answer["ticket"].encode("utf-8")) <= 16 * 1024:
                raise ValueError
            runtime.api.validate_endpoint_id(answer["endpoint_id"])
            grant = PairedEndpoint(**dict(raw, endpoint_id=answer["endpoint_id"], scopes=frozenset(raw["scopes"])))
            grant.validate()
            secret = decode_redemption(answer["redemption"])
        except (KeyError, ValueError, TypeError, SessionDenied):
            raise SessionDenied("invalid pairing answer") from None
        if grant.expires_at_ms <= int(time.time() * 1000):
            raise SessionDenied("pairing answer has expired")
        capabilities = answer.get("capabilities")
        if not isinstance(capabilities, dict):
            raise SessionDenied("invalid pairing capabilities")
        self._future = asyncio.get_running_loop().create_future()
        self._expected = {"grant": grant, "secret": secret, "capabilities": capabilities,
            "deadline": time.monotonic() + PAIR_TTL_SECONDS, "runtime": runtime, "context": None}
        try:
            self._expected["request_id"] = runtime.dial(answer["ticket"], answer["endpoint_id"], pairing=True)
        except BaseException:
            self._expected = None
            self._future.cancel()
            raise
        self._expiry_task = asyncio.create_task(self._expire(self._future), name="iroh-pairing-expiry")
        self._future.add_done_callback(lambda future: self.cancel() if future.cancelled() and self._future is future else None)
        return self._future

    async def _expire(self, future: asyncio.Future) -> None:
        try:
            await asyncio.sleep(PAIR_TTL_SECONDS)
            if self._future is future and not future.done():
                self.cancel(error=SessionDenied("pairing invitation has expired"))
        except asyncio.CancelledError:
            return

    async def connected(self, runtime: IrohSessionRuntime, context: ConnectionContext) -> None:
        expected = self._expected
        if expected is None or context.protocol != PAIR_ALPN or not context.initiator or \
                expected["deadline"] <= time.monotonic() or context.remote_endpoint_id != expected["grant"].endpoint_id or \
                context.connection_id != expected["request_id"] or expected["context"] is not None:
            raise SessionDenied("unsolicited pairing connection")
        expected["context"] = context
        runtime.send_enrollment(context, bytes(runtime.api.encode_enrollment(runtime.api.EnrollmentMessage(
            kind=runtime.api.EnrollmentKind.REDEEM, challenge=None, binding=expected["secret"]))))

    async def enrollment(self, runtime: IrohSessionRuntime, context: ConnectionContext, payload: bytes) -> None:
        expected = self._expected
        if expected is None or context.protocol != PAIR_ALPN or not context.initiator or \
                expected["deadline"] <= time.monotonic() or context.remote_endpoint_id != expected["grant"].endpoint_id or \
                context != expected["context"]:
            raise SessionDenied("unsolicited pairing message")
        message = runtime.api.decode_enrollment(payload)
        grant = expected["grant"]
        if message.kind == runtime.api.EnrollmentKind.CHALLENGE and self._pending is None:
            challenge = message.challenge
            capabilities = json.loads(challenge.capabilities_json)
            if challenge.generation != 1 or challenge.authorization_epoch != grant.authorization_epoch or \
                    challenge.expires_at_ms != grant.expires_at_ms or frozenset(challenge.scopes) != grant.scopes or \
                    capabilities != dict(expected["capabilities"], device_id=grant.device_id):
                raise SessionDenied("pairing challenge changed the approved grant")
            digest = bytes(runtime.api.pairing_binding(challenge, context.exporter,
                context.local_endpoint_id, context.remote_endpoint_id))
            self._pending = _Redemption(_Invitation(grant, expected["deadline"], hashlib.sha256(expected["secret"]).digest()), context, digest)
            runtime.send_enrollment(context, bytes(runtime.api.encode_enrollment(runtime.api.EnrollmentMessage(
                kind=runtime.api.EnrollmentKind.CONFIRM, challenge=None, binding=digest))))
        elif message.kind == runtime.api.EnrollmentKind.READY:
            pending = self._pending
            if pending is None or pending.context != context or not secrets.compare_digest(bytes(message.binding), pending.digest):
                raise SessionDenied("pairing confirmation was rejected")
            await asyncio.to_thread(self.grants.register, grant)
            self._expected, self._pending = None, None
            self._stop_expiry()
            if self._future and not self._future.done():
                self._future.set_result(grant)
            runtime.disconnect(context, user_requested=False)
        else:
            raise SessionDenied("unexpected pairing message")

    async def closed(self, context: ConnectionContext, *_args: Any) -> None:
        if self._expected and self._expected["context"] == context:
            self.cancel(error=SessionDenied("pairing connection closed"))

    async def dial_failed(self, request_id: int, _code: str) -> None:
        if self._expected and self._expected["request_id"] == request_id:
            self.cancel(error=SessionDenied("pairing connection failed"))

    def _stop_expiry(self) -> None:
        task, self._expiry_task = self._expiry_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def cancel(self, *, error: Exception | None = None) -> None:
        expected = self._expected
        self._expected, self._pending = None, None
        self._stop_expiry()
        if expected and expected["context"] is not None:
            try:
                expected["runtime"].disconnect(expected["context"], user_requested=True)
            except (SessionDenied, ConnectionError):
                pass
        if self._future and not self._future.done():
            if error is None:
                self._future.cancel()
            else:
                self._future.set_exception(error)
