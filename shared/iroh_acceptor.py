"""Platform storage and delivery for the shared Rust peer acceptor actor."""
from __future__ import annotations

import asyncio
import json
import time

from shared.iroh_client import _grant_json, _decode_grant
from shared.iroh_media import _join_owned
from shared.session_transport import SessionBinding, SessionDenied, TransportKind


def _bytes(value):
    if isinstance(value, bytes):
        return value
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


class RustAcceptorAdmission:
    def __init__(self, *, runtime, grants, capabilities, on_prepared, on_ready):
        self.runtime, self.grants = runtime, grants
        self.on_prepared, self.on_ready = on_prepared, on_ready
        self.core = runtime.api.AcceptorSession(runtime.endpoint_info.endpoint_id,
            json.dumps(capabilities, allow_nan=False, separators=(",", ":")))
        self._gate = asyncio.Lock()
        self._devices = {}
        self._core_devices = {}
        self._contexts = {}
        self._timer = None

    @staticmethod
    def _now():
        return int(time.time()*1000)

    def _context(self, context):
        return self.runtime.api.ClientConnectionContext(connection_id=context.connection_id,
            remote_endpoint=context.remote_endpoint_id, local_endpoint=context.local_endpoint_id,
            exporter=context.exporter, protocol=context.protocol, initiator=context.initiator)

    def issue_after_verified_proof(self, runtime, grant, *, core_device_id=None):
        if runtime is not self.runtime:
            raise SessionDenied("acceptor approval belongs to another endpoint")
        if core_device_id is not None:
            import re
            if not re.fullmatch(r"[a-z0-9]{15}",core_device_id):
                raise SessionDenied("invalid approved Core peer device")
        proof = json.loads(self.core.issue_after_verified_proof(_grant_json(grant), runtime.endpoint_info.ticket, self._now()))
        self._devices[grant.device_id] = grant.endpoint_id
        self._core_devices[grant.endpoint_id] = core_device_id
        return proof

    def cancel_device(self, device_id):
        endpoint = self._devices.pop(device_id, None)
        if endpoint is None:
            return
        self._core_devices.pop(endpoint,None)
        for connection_id in self.core.cancel_endpoint(endpoint):
            context = self._contexts.get(connection_id)
            if context is not None:
                try:
                    self.runtime.disconnect(context)
                except SessionDenied:
                    pass

    async def _store(self):
        return await _join_owned(asyncio.create_task(asyncio.to_thread(self.grants.store.read_client_snapshot)))

    async def _persist(self, old, update, *, endpoint_id=None, core_device_id=None):
        await _join_owned(asyncio.create_task(asyncio.to_thread(self.grants.store.compare_client_snapshot,
            old, bytes(update), endpoint_id=endpoint_id, core_device_id=core_device_id)))

    async def _apply(self, context, action, old):
        if action.protected_store is not None:
            proposed = action.protected_store
            associated = context.remote_endpoint_id in self._core_devices
            await self._persist(old, proposed, endpoint_id=context.remote_endpoint_id if associated else None,
                core_device_id=self._core_devices.get(context.remote_endpoint_id))
            current = await self._store()
            step = self.core.persisted(self._context(context), _bytes(current), self._now())
        else:
            step = action.application
        if step.ready:
            grant = _decode_grant(step.grant_json)
            current = await asyncio.to_thread(self.grants.grant_for_endpoint, context.remote_endpoint_id)
            if grant != current:
                raise SessionDenied("peer grant changed during source preparation")
            binding = SessionBinding(grant.endpoint_id, grant.device_id, grant.owner_key,
                grant.canonical_user_id, grant.conversation_key, context.transport_id, step.generation,
                grant.authorization_epoch, grant.expires_at_ms, grant.scopes, TransportKind.IROH)
            channel = self.runtime.admit(context, binding)
            await self.on_prepared(self.runtime, context, channel)
            if await asyncio.to_thread(self.grants.grant_for_endpoint, context.remote_endpoint_id) != grant:
                raise SessionDenied("peer grant changed before application readiness")
            self.runtime.activate(context)
            if step.outgoing is not None:
                self.runtime.send_enrollment(context, bytes(step.outgoing))
            await self.on_ready(self.runtime, context, channel)
        elif step.outgoing is not None:
            self.runtime.send_enrollment(context, bytes(step.outgoing))

    async def connected(self, runtime, context):
        if runtime is not self.runtime:
            raise SessionDenied("accepted connection belongs to another endpoint")
        async with self._gate:
            old = await self._store()
            action = self.core.connected(self._context(context), _bytes(old), self._now())
            self._contexts[context.connection_id] = context
            try:
                await self._apply(context, action, old)
            except BaseException:
                self.core.closed(context.connection_id)
                self._contexts.pop(context.connection_id, None)
                raise
            if self._timer is None:
                self._timer = asyncio.create_task(self._expire(), name="iroh-peer-acceptor-expiry")

    async def enrollment(self, runtime, context, payload):
        if runtime is not self.runtime or self._contexts.get(context.connection_id) != context:
            raise SessionDenied("accepted enrollment belongs to another connection")
        async with self._gate:
            old = await self._store()
            await self._apply(context, self.core.receive(self._context(context), payload, _bytes(old), self._now()), old)

    async def _expire(self):
        while self._contexts:
            await asyncio.sleep(.1)
            for connection_id in self.core.expire(self._now()):
                context = self._contexts.get(connection_id)
                if context is not None:
                    try:
                        self.runtime.disconnect(context)
                    except SessionDenied:
                        pass

    async def revoke_device(self, runtime, device_id, *, authorization_epoch):
        async with self._gate:
            transports = tuple(context.transport_id for context in self._contexts.values())
            self.cancel_device(device_id)
            changed = await asyncio.to_thread(self.grants.revoke, device_id, authorization_epoch=authorization_epoch)
            for channel in runtime.registry.revoke_owned(device_id, minimum_epoch=authorization_epoch, transport_ids=transports):
                channel.disconnect()
            return changed

    async def closed(self, context, _binding, _user_requested):
        self.core.closed(context.connection_id)
        self._contexts.pop(context.connection_id, None)
        if not self._contexts and self._timer is not None:
            task, self._timer = self._timer, None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
