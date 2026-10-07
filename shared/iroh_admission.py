# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Confirm a previously authorized pairing on this exact Iroh connection.

Tickets are never input to the grant registry. Existing pairing/account services
must establish the endpoint association before this session exchange begins.
All wire coding and transcript construction use the common Rust binding.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from shared.iroh_channel import IrohMessageChannel
from shared.iroh_grants import EndpointGrantRegistry, PairedEndpoint
from shared.iroh_runtime import ConnectionContext, IrohSessionRuntime
from shared.session_transport import SessionBinding, SessionDenied


@dataclass
class _Pending:
    context: ConnectionContext
    challenge: Any
    digest: bytes
    deadline: float
    binding: SessionBinding | None = None


class PairedEndpointAdmission:
    def __init__(self, *, grants: EndpointGrantRegistry, role: str, capabilities: dict[str, Any],
                 on_prepared: Callable[[IrohSessionRuntime, ConnectionContext, IrohMessageChannel], Awaitable[None]],
                 on_ready: Callable[[IrohSessionRuntime, ConnectionContext, IrohMessageChannel], Awaitable[None]] | None = None,
                 acceptor: bool | None = None) -> None:
        if role not in {"server", "client", "lite"}:
            raise ValueError("unsupported admission role")
        self.grants, self.role, self.capabilities = grants, role, capabilities
        if acceptor is not None and type(acceptor) is not bool:
            raise ValueError("invalid admission direction")
        self.acceptor = role == "server" if acceptor is None else acceptor
        self.on_prepared, self.on_ready = on_prepared, on_ready
        self._pending: dict[str, _Pending] = {}
        self._gate = asyncio.Lock()

    async def register_verified_pairing(self, grant: PairedEndpoint) -> None:
        # Only the host's reviewed proof/account service calls this method.
        async with self._gate:
            await asyncio.to_thread(self.grants.register, grant)

    async def connected(self, runtime: IrohSessionRuntime, context: ConnectionContext) -> None:
        if context.protocol != "autoyou/session/1" or context.initiator != (not self.acceptor):
            raise SessionDenied("connection purpose does not match this admission owner")
        if not self.acceptor:
            await asyncio.to_thread(self.grants.grant_for_endpoint, context.remote_endpoint_id)
            return
        async with self._gate:
            if context.transport_id in self._pending:
                raise SessionDenied("duplicate session connection")
            binding = await asyncio.to_thread(self.grants.binding_for_connection, context.remote_endpoint_id, context.transport_id)
            challenge = runtime.api.EnrollmentChallenge(initiator_endpoint=context.remote_endpoint_id,
                acceptor_endpoint=context.local_endpoint_id, nonce=secrets.token_bytes(32),
                generation=binding.generation, authorization_epoch=binding.authorization_epoch,
                expires_at_ms=binding.expires_at_ms, scopes=sorted(binding.scopes),
                capabilities_json=json.dumps(self.capabilities, allow_nan=False, separators=(",", ":")))
            digest = bytes(runtime.api.enrollment_binding(challenge, context.exporter,
                context.remote_endpoint_id, context.local_endpoint_id))
            self._pending[context.transport_id] = _Pending(context, challenge, digest, time.monotonic() + 10, binding)
            runtime.send_enrollment(context, bytes(runtime.api.encode_enrollment(runtime.api.EnrollmentMessage(
                kind=runtime.api.EnrollmentKind.CHALLENGE, challenge=challenge, binding=None))))

    async def enrollment(self, runtime: IrohSessionRuntime, context: ConnectionContext, payload: bytes) -> None:
        if context.protocol != "autoyou/session/1" or context.initiator != (not self.acceptor):
            raise SessionDenied("connection purpose does not match this admission owner")
        message = runtime.api.decode_enrollment(payload)
        if self.acceptor:
            async with self._gate:
                pending = self._pending.pop(context.transport_id, None)
                if pending is None or pending.context != context or pending.deadline <= time.monotonic() or \
                        message.kind != runtime.api.EnrollmentKind.CONFIRM or not secrets.compare_digest(bytes(message.binding or b""), pending.digest):
                    raise SessionDenied("session confirmation was rejected")
                current = await asyncio.to_thread(self.grants.grant_for_endpoint, context.remote_endpoint_id)
                binding = pending.binding
                if binding is None or current.authorization_epoch != binding.authorization_epoch or \
                        current.scopes != binding.scopes or current.expires_at_ms != binding.expires_at_ms:
                    raise SessionDenied("pairing permission changed during confirmation")
                channel = runtime.admit(context, binding)
                await self.on_prepared(runtime, context, channel)
                runtime.activate(context)
                runtime.send_enrollment(context, bytes(runtime.api.encode_enrollment(runtime.api.EnrollmentMessage(
                    kind=runtime.api.EnrollmentKind.READY, challenge=None, binding=pending.digest))))
                if self.on_ready:
                    await self.on_ready(runtime, context, channel)
            return
        if message.kind == runtime.api.EnrollmentKind.CHALLENGE:
            if context.transport_id in self._pending:
                raise SessionDenied("duplicate session challenge")
            challenge = message.challenge
            current = await asyncio.to_thread(self.grants.grant_for_endpoint, context.remote_endpoint_id)
            if challenge.authorization_epoch != current.authorization_epoch or \
                    not int(time.time()*1000) < challenge.expires_at_ms <= current.expires_at_ms or \
                    not frozenset(challenge.scopes) <= current.scopes:
                raise SessionDenied("server challenge is outside its paired scope")
            digest = bytes(runtime.api.enrollment_binding(challenge, context.exporter,
                context.local_endpoint_id, context.remote_endpoint_id))
            async with self._gate:
                binding = await asyncio.to_thread(self.grants.binding_for_remote_generation,
                    context.remote_endpoint_id, context.transport_id, generation=challenge.generation,
                    authorization_epoch=challenge.authorization_epoch, expires_at_ms=challenge.expires_at_ms,
                    scopes=frozenset(challenge.scopes))
                channel = runtime.admit(context, binding)
                await self.on_prepared(runtime, context, channel)
            self._pending[context.transport_id] = _Pending(context, challenge, digest, time.monotonic() + 10, binding)
            runtime.send_enrollment(context, bytes(runtime.api.encode_enrollment(runtime.api.EnrollmentMessage(
                kind=runtime.api.EnrollmentKind.CONFIRM, challenge=None, binding=digest))))
        elif message.kind == runtime.api.EnrollmentKind.READY:
            pending = self._pending.pop(context.transport_id, None)
            if pending is None or pending.context != context or pending.deadline <= time.monotonic() or \
                    not secrets.compare_digest(bytes(message.binding or b""), pending.digest):
                raise SessionDenied("server session confirmation was rejected")
            async with self._gate:
                current = await asyncio.to_thread(self.grants.grant_for_endpoint, context.remote_endpoint_id)
                if pending.binding is None or current.authorization_epoch != pending.binding.authorization_epoch or \
                        not pending.binding.scopes <= current.scopes or \
                        not int(time.time() * 1000) < pending.binding.expires_at_ms <= current.expires_at_ms:
                    raise SessionDenied("pairing permission changed during confirmation")
                runtime.activate(context)
                channel = runtime.registry.check(pending.binding)
                if self.on_ready:
                    await self.on_ready(runtime, context, channel)
        else:
            raise SessionDenied("unexpected session confirmation")

    async def revoke_device(self, runtime: IrohSessionRuntime, device_id: str, *, authorization_epoch: int) -> bool:
        async with self._gate:
            changed = await asyncio.to_thread(self.grants.revoke, device_id, authorization_epoch=authorization_epoch)
            channel = runtime.registry.revoke(device_id, minimum_epoch=authorization_epoch)
            if channel is not None:
                channel.disconnect()
            return changed

    async def closed(self, context: ConnectionContext, _binding: SessionBinding | None, _user_requested: bool) -> None:
        self._pending.pop(context.transport_id, None)
