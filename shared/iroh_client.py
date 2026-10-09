# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Client host adapter for the shared Rust enrollment/lifecycle owner.

Proof verification remains in the existing pairing services. No password, PAKE,
state-machine or protocol implementation is recreated by this adapter. UI JSON
receives only redacted state; native stream payloads stay in the runtime.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import secrets
import time
from dataclasses import asdict
from typing import Any, Callable

from shared.iroh_grants import EndpointGrantRegistry, PairedEndpoint
from shared.iroh_client_store import ClientGrantRegistry
from shared.iroh_keys import EndpointKeys
from shared.iroh_runtime import ConnectionContext, IrohSessionRuntime
from shared.iroh_state_store import ProtectedTransportStateUnavailable
from shared.session_transport import SessionBinding, SessionDenied


def _grant_json(grant: PairedEndpoint) -> str:
    return json.dumps(dict(asdict(grant), scopes=sorted(grant.scopes)), allow_nan=False, separators=(",", ":"))


def _decode_grant(value: str) -> PairedEndpoint:
    data = json.loads(value)
    grant = PairedEndpoint(**dict(data, scopes=frozenset(data["scopes"])))
    grant.validate()
    return grant


class IrohClientService:
    def __init__(self, *, keys: EndpointKeys, grants: EndpointGrantRegistry, policy: dict[str, Any],
                 on_prepared: Callable, on_ready: Callable, on_closed: Callable,
                 on_binary_frame: Callable | None = None, on_state: Callable | None = None,
                 api: Any | None = None, now_ms: Callable[[], int] = lambda: int(time.time() * 1000)) -> None:
        self.keys, self.grants, self.policy, self.api = keys, grants, policy, api
        self.on_prepared, self.on_ready, self.on_closed = on_prepared, on_ready, on_closed
        self.on_binary_frame, self.on_state, self.now_ms = on_binary_frame, on_state, now_ms
        self.runtime: IrohSessionRuntime | None = None
        self.core: Any = None
        self._gate = asyncio.Lock()
        self._requests: dict[int, int] = {}
        self._contexts: dict[int, ConnectionContext] = {}
        self._future: asyncio.Future | None = None
        self._channel: Any = None
        self._capabilities: dict[str, Any] = {}
        self._peer: tuple[str, str] | None = None
        self.core_device: str | None = None
        self._next_core_device: str | None = None
        self._recovery: asyncio.Task | None = None
        self._closing = False
        self._history_deletion = False
        self._lifecycle_revision = 0
        self._core_cloud_lock = asyncio.Lock()
        self.incoming: Any = None
        self.room_incoming: Any = None
        self._incoming: dict[int, tuple[ConnectionContext, Any]] = {}
        self._endpoint_owner: IrohClientService | None = None
        self._children: set[IrohClientService] = set()
        self._child_requests: dict[int, IrohClientService] = {}
        self._close_task: asyncio.Task | None = None

    def fork(self, *, grants: EndpointGrantRegistry, **callbacks: Any) -> IrohClientService:
        """One endpoint/pump, independently granted peer or Lobby connections."""
        if self._endpoint_owner is not None or self.runtime is None or self._closing or len(self._children) >= 31:
            raise SessionDenied("shared client endpoint is unavailable or full")
        child = IrohClientService(keys=self.keys, grants=grants, policy=self.policy, api=self.runtime.api,
            now_ms=self.now_ms, **callbacks)
        child._endpoint_owner = self
        self._children.add(child)
        return child

    def _bind_request(self, request: int, operation: int) -> None:
        owner = self._endpoint_owner
        if owner is not None:
            if owner._closing or owner.runtime is not self.runtime or owner._child_requests.get(request) is not None:
                raise SessionDenied("shared endpoint dial is obsolete")
            owner._child_requests[request] = self
        self._requests[request] = operation

    async def start(self, *, enroll: bool = True, unlocked_password: str | None = None) -> None:
        async with self._gate:
            if self._history_deletion:
                raise SessionDenied("local history deletion is in progress")
            if self.runtime is not None:
                return
            if self._closing:
                raise ConnectionError("client endpoint is closed")
            if self._endpoint_owner is not None:
                owner = self._endpoint_owner
                if owner.runtime is None or owner._closing:
                    raise SessionDenied("shared client endpoint is closed")
                core = owner.runtime.api.ClientSession(owner.runtime.endpoint_info.endpoint_id)
                self.grants = ClientGrantRegistry(self.grants.store, api=owner.runtime.api, now_ms=self.now_ms)
                self.runtime, self.core = owner.runtime, core
                return
            runtime = await IrohSessionRuntime.start(keys=self.keys, role="client", policy=self.policy,
                api=self.api, enroll=enroll, unlocked_password=unlocked_password,
                on_connected=self._connected, on_enrollment=self._enrollment, on_closed=self._closed,
                on_dial_failed=self._dial_failed, on_binary_frame=self._binary)
            try:
                core = runtime.api.ClientSession(runtime.endpoint_info.endpoint_id)
            except BaseException:
                await asyncio.shield(runtime.close())
                raise
            self.grants = ClientGrantRegistry(self.grants.store, api=runtime.api, now_ms=self.now_ms)
            self.runtime, self.core = runtime, core

    def offer(self) -> dict[str, Any]:
        if self.runtime is None or self._closing:
            raise ConnectionError("client endpoint is not running")
        return {"transport": "iroh", "type": "offer", "version": 1, "endpoint_id": self.runtime.endpoint_info.endpoint_id}

    @property
    def is_ready(self) -> bool:
        return bool(self._channel and self._channel.is_ready and not self._closing)

    def snapshot(self) -> dict[str, Any]:
        if self.core is None:
            return {"state": "idle", "transport": "iroh", "connected": False}
        state = self.core.snapshot()
        if self.runtime is not None and state.phase == self.runtime.api.ClientPhase.CHANGING_PATH and self.is_ready:
            metrics = self._channel.get_metrics()
            if metrics.get("path") in {"direct", "relay"}:
                self.core.network_changed(state.operation, True)
                state = self.core.snapshot()
        return {"state": state.phase.name.lower(), "transport": "iroh", "connected": self.is_ready,
            "generation": state.generation, "authorization_epoch": state.authorization_epoch,
            "retry_attempt": state.retry_attempt}

    async def _notify(self) -> None:
        if self.on_state is not None:
            result = self.on_state(self.snapshot())
            if inspect.isawaitable(result):
                await result

    def _new_future(self) -> asyncio.Future:
        if self._future is not None and not self._future.done():
            raise SessionDenied("another client connection is pending")
        self._future = asyncio.get_running_loop().create_future()
        return self._future

    async def pair_after_verified_answer(self, answer: dict[str, Any]) -> Any:
        """Called only after the requested OTP/PAKE/account/local proof succeeds."""
        if self.runtime is None or self._closing:
            raise ConnectionError("client endpoint is not running")
        if answer.get("grant", {}).get("origin_transport") == "room" and not callable(getattr(self.core, "dial_protocol", None)):
            raise SessionDenied("native Lobby enrollment requires the qualified room protocol API")
        await self._cancel_recovery()
        self._lifecycle_revision += 1
        captured = self._lifecycle_revision
        device = self._next_core_device
        answer = dict(answer, ticket=await self._routing_ticket(answer["endpoint_id"],answer["ticket"],device))
        async with self._gate:
            if self._history_deletion or self._lifecycle_revision != captured:
                raise SessionDenied("client pairing operation is no longer current")
            future = self._new_future()
            request = None
            try:
                encoded = json.dumps(answer, allow_nan=False, separators=(",", ":"))
                operation = self.core.begin_verified_pairing(encoded, self.now_ms())
                self.core_device = device
                request = self._dial(answer["ticket"], answer["endpoint_id"], pairing=True)
                self.core.bind_dial(operation, request)
                self._bind_request(request, operation)
            except BaseException:
                future.cancel()
                self._cancel_unbound_dial(request)
                raise
        await self._notify()
        try:
            async with asyncio.timeout(min(60, answer["redemption_expires_in_seconds"]) + 1):
                grant = await future
            self._peer = (grant.endpoint_id, answer["ticket"])
            return await self._connect_existing(*self._peer)
        except (asyncio.CancelledError, TimeoutError):
            await self.disconnect()
            raise

    async def reconnect(self, endpoint_id: str, ticket: str) -> Any:
        """Explicit reconnect to a previously paired, still-authorized endpoint."""
        if self._history_deletion:
            raise SessionDenied("local history deletion is in progress")
        self._lifecycle_revision += 1
        await self._cancel_recovery()
        self._peer = (endpoint_id, ticket)
        return await self._connect_existing(endpoint_id, ticket)

    def use_core_device(self, device: str | None) -> None:
        if device is not None and not re.fullmatch(r"[a-z0-9]{15}",device):
            raise SessionDenied("invalid registered Core device")
        self._next_core_device = device

    async def _deny_core_endpoint(self, endpoint: str) -> None:
        from shared.iroh_delivery import _joined_disk
        try:
            await _joined_disk(self.grants.deny_core_endpoint,endpoint)
        finally:
            self.core.revoke()
            await self._disconnect_owned()

    async def deny_core_authority(self) -> None:
        owner = self._endpoint_owner or self
        async def deny(client):
            if client.core_device is not None and client._peer is not None:
                await client._deny_core_endpoint(client._peer[0])
        results = await asyncio.gather(*(deny(client) for client in (owner,*tuple(owner._children))),return_exceptions=True)
        if owner.incoming is not None and hasattr(owner.incoming,"deny_core_authority"):
            await owner.incoming.deny_core_authority()
        if any(isinstance(result,BaseException) for result in results):
            raise SessionDenied("Core-derived authority could not be persisted")

    async def check_core_authority(self) -> None:
        owner = self._endpoint_owner or self
        permits = asyncio.Semaphore(4)
        async def check(client):
            if client.core_device is None or client._peer is None or client._closing:
                return
            async with permits:
                try:
                    await client._routing_ticket(*client._peer,client.core_device)
                except SessionDenied:
                    pass  # The lookup already persisted and physically fenced this client.
        await asyncio.gather(*(check(client) for client in (owner,*tuple(owner._children))))
        if owner.incoming is not None and hasattr(owner.incoming,"check_core_authority"):
            await owner.incoming.check_core_authority()

    async def _routing_ticket(self, endpoint: str, ticket: str, device: str | None) -> str:
        if device is None:
            return ticket
        routing = self.runtime.core_routing
        if routing is None:
            raise SessionDenied("registered Core authority is unavailable")
        revision = self._lifecycle_revision
        def current():
            callback = getattr(routing,"is_current",None)
            return revision==self._lifecycle_revision and not self._closing and self.runtime.core_routing is routing and (not callable(callback) or callback())
        if not current():
            raise SessionDenied("Core routing operation is obsolete")
        if routing.status == "denied":
            await self._deny_core_endpoint(endpoint)
            raise SessionDenied("Core cloud authority requires a fresh verified pairing")
        try:
            value = await routing.lookup(device_id=device,endpoint_id=endpoint)
        except (SessionDenied,self.runtime.api.BindingError.PermissionDenied,self.runtime.api.BindingError.InvalidTicket,self.runtime.api.BindingError.InvalidInput):
            if not current():
                raise SessionDenied("Core routing operation is obsolete") from None
            await self._deny_core_endpoint(endpoint)
            raise
        except (OSError,TimeoutError,ConnectionError):
            value = ticket
        if not current() or routing.status == "denied":
            raise SessionDenied("Core routing operation is obsolete")
        return value or ticket

    async def _connect_existing(self, endpoint_id: str, ticket: str) -> Any:
        if self.runtime is None or self._closing:
            raise ConnectionError("client endpoint is not running")
        if self.core_device is None and isinstance(self.grants,ClientGrantRegistry):
            self.core_device = await asyncio.to_thread(self.grants.core_device_for_endpoint,endpoint_id)
        ticket = await self._routing_ticket(endpoint_id,ticket,self.core_device)
        async with self._gate:
            if self._history_deletion:
                raise SessionDenied("local history deletion is in progress")
            grant = await asyncio.to_thread(self.grants.grant_for_endpoint, endpoint_id)
            if grant.origin_transport == "room" and not callable(getattr(self.core, "dial_protocol", None)):
                raise SessionDenied("native Lobby reconnect requires the qualified room protocol API")
            floor = await asyncio.to_thread(self.grants.generation_floor, endpoint_id)
            future = self._new_future()
            request = None
            try:
                operation = self.core.begin_session(_grant_json(grant), ticket, floor, self.now_ms())
                self._peer = (endpoint_id,ticket)
                request = self._dial(ticket, endpoint_id, pairing=False)
                self.core.bind_dial(operation, request)
                self._bind_request(request, operation)
            except BaseException:
                future.cancel()
                self._cancel_unbound_dial(request)
                raise
        await self._notify()
        try:
            async with asyncio.timeout(11):
                return await future
        except (asyncio.CancelledError, TimeoutError):
            # Cancellation comes from explicit UI/lifecycle shutdown, or the
            # owned recovery task. Do not leave a delayed dial able to activate.
            if self._recovery is not asyncio.current_task():
                await self.disconnect()
            else:
                await self._retire_attempt(operation)
            raise

    def _dial(self, ticket: str, endpoint_id: str, *, pairing: bool) -> int:
        get_protocol = getattr(self.core, "dial_protocol", None)
        protocol = get_protocol() if callable(get_protocol) else (
            "autoyou/pair/1" if pairing else "autoyou/session/1")
        expected = {"autoyou/pair/1", "autoyou/room-pair/1"} if pairing else {
            "autoyou/session/1", "autoyou/room-session/1"}
        if protocol not in expected:
            raise SessionDenied("native dial protocol does not match the operation")
        if protocol.startswith("autoyou/room-"):
            return self.runtime.dial_application(ticket, endpoint_id, protocol=protocol)
        return self.runtime.dial(ticket, endpoint_id, pairing=pairing)

    def _incoming_owner(self, context: ConnectionContext) -> Any:
        if context.protocol in {"autoyou/room-pair/1", "autoyou/room-session/1"}:
            return self.room_incoming
        if context.protocol in {"autoyou/pair/1", "autoyou/session/1"}:
            return self.incoming
        return None

    def _context(self, context: ConnectionContext) -> Any:
        return self.runtime.api.ClientConnectionContext(connection_id=context.connection_id,
            remote_endpoint=context.remote_endpoint_id, local_endpoint=context.local_endpoint_id,
            exporter=context.exporter, protocol=context.protocol, initiator=context.initiator)

    async def _connected(self, runtime: IrohSessionRuntime, context: ConnectionContext) -> None:
        child = self._child_requests.get(context.connection_id)
        if child is not None:
            await child._connected(runtime, context)
            return
        if not context.initiator:
            owner = self._incoming_owner(context)
            allowed = context.protocol in {"autoyou/session/1", "autoyou/room-session/1"} or (
                context.protocol in {"autoyou/pair/1", "autoyou/room-pair/1"} and
                getattr(owner, "allow_pairing", False) is True)
            if self._closing or owner is None or not allowed:
                raise SessionDenied("unsolicited client connection")
            self._incoming[context.connection_id] = (context, owner)
            await owner.connected(runtime, context)
            return
        operation = self._requests.get(context.connection_id)
        if operation is None or self._closing:
            raise SessionDenied("unsolicited client connection")
        self._contexts[context.connection_id] = context
        try:
            action = self.core.connected(operation, self._context(context), self.now_ms())
            if action.outgoing is not None:
                runtime.send_enrollment(context, bytes(action.outgoing))
        except (runtime.api.BindingError.PermissionDenied, runtime.api.BindingError.InvalidInput):
            self.core.deny()
            self._fail(SessionDenied("client session authorization was rejected"))
            raise SessionDenied("client session authorization was rejected") from None
        await self._notify()

    async def _enrollment(self, runtime: IrohSessionRuntime, context: ConnectionContext, payload: bytes) -> None:
        child = self._child_requests.get(context.connection_id)
        if child is not None:
            await child._enrollment(runtime, context, payload)
            return
        if not context.initiator:
            incoming = self._incoming.get(context.connection_id)
            if self._closing or incoming is None or incoming[0] != context or incoming[1] is not self._incoming_owner(context):
                raise SessionDenied("obsolete incoming connection")
            await incoming[1].enrollment(runtime, context, payload)
            return
        operation = self._requests.get(context.connection_id)
        if operation is None or self._contexts.get(context.connection_id) != context or self._closing:
            raise SessionDenied("obsolete client connection")
        try:
            action = self.core.receive(operation, self._context(context), payload, self.now_ms())
            if action.capabilities_json is not None:
                self._capabilities = json.loads(action.capabilities_json)
            if context.protocol in {"autoyou/pair/1", "autoyou/room-pair/1"}:
                if action.close_connection:
                    grant = _decode_grant(action.grant_json)
                    if isinstance(self.grants,ClientGrantRegistry):
                        await asyncio.to_thread(self.grants.register,grant,core_device_id=self.core_device)
                    else:
                        await asyncio.to_thread(self.grants.register,grant)
                    self.core.pairing_persisted(operation)
                    runtime.disconnect(context, user_requested=False)
                    if self._future is not None and not self._future.done():
                        self._future.set_result(grant)
            else:
                if action.grant_json is not None and not action.ready:
                    proposed = _decode_grant(action.grant_json)
                    binding = await asyncio.to_thread(self.grants.binding_for_remote_generation,
                        context.remote_endpoint_id, context.transport_id, generation=action.generation,
                        authorization_epoch=proposed.authorization_epoch, expires_at_ms=proposed.expires_at_ms,
                        scopes=proposed.scopes)
                    self._channel = runtime.admit(context, binding)
                    self._channel.application_capabilities = dict(self._capabilities)
                    await self.on_prepared(runtime, context, self._channel)
                if action.ready:
                    if self._channel is None or self._channel.binding.transport_id != context.transport_id:
                        raise SessionDenied("client session preparation is missing")
                    runtime.registry.check(self._channel.binding)
                    runtime.activate(context)
                    await self.on_ready(runtime, context, self._channel, dict(self._capabilities))
                    if self._future is not None and not self._future.done():
                        self._future.set_result(self._channel)
            if action.outgoing is not None:
                runtime.send_enrollment(context, bytes(action.outgoing))
        except (runtime.api.BindingError.PermissionDenied, runtime.api.BindingError.InvalidInput, SessionDenied):
            self.core.deny()
            self._fail(SessionDenied("client session authorization was rejected"))
            raise SessionDenied("client session authorization was rejected") from None
        except Exception:
            self.core.deny()
            self._fail(ConnectionError("client session preparation failed"))
            raise SessionDenied("client session preparation failed") from None
        await self._notify()

    async def _binary(self, channel: Any, frame: Any) -> None:
        child = self._child_requests.get(getattr(channel, "connection_id", None))
        if child is not None:
            await child._binary(channel, frame)
            return
        incoming = self._incoming.get(getattr(channel, "connection_id", None))
        if incoming is not None:
            context, owner = incoming
            if self._closing or owner is not self._incoming_owner(context) or channel.binding.transport_id != context.transport_id:
                raise SessionDenied("obsolete incoming stream")
            self.runtime.registry.check(channel.binding)
            await owner.binary(channel, frame)
            return
        if channel is not self._channel or not self.is_ready or self.on_binary_frame is None:
            raise SessionDenied("client stream capability is not attached")
        await self.on_binary_frame(channel, frame)

    def _fail(self, error: Exception) -> None:
        if self._future is not None and not self._future.done():
            self._future.set_exception(error)

    async def _closed(self, context: ConnectionContext, binding: SessionBinding | None, user_requested: bool) -> None:
        child = self._child_requests.pop(context.connection_id, None)
        if child is not None:
            await child._closed(context, binding, user_requested)
            return
        incoming = self._incoming.pop(context.connection_id, None)
        if incoming is not None:
            await incoming[1].closed(context, binding, user_requested)
            return
        operation = self._requests.pop(context.connection_id, None)
        self._contexts.pop(context.connection_id, None)
        if operation is None or operation != self.core.snapshot().operation:
            return
        if binding is not None and self._channel is not None and self._channel.binding == binding:
            self._channel = None
            await self.on_closed(context, binding, user_requested)
        # Physical application cleanup can yield while an explicit reconnect
        # starts a new operation. The retired callback must not fail that new
        # future or report loss against its replacement Rust state.
        if operation != self.core.snapshot().operation:
            return
        self._fail(ConnectionError("client connection closed"))
        if not self._closing:
            if user_requested:
                self.core.disconnect()
            else:
                self.core.lost(operation, self.now_ms(), secrets.randbelow(251))
                self._schedule_recovery()
            await self._notify()

    async def _dial_failed(self, request: int, _code: str) -> None:
        child = self._child_requests.pop(request, None)
        if child is not None:
            await child._dial_failed(request, _code)
            return
        operation = self._requests.pop(request, None)
        if operation is None or operation != self.core.snapshot().operation:
            return
        self._fail(ConnectionError("client connection failed"))
        if not self._closing:
            self.core.lost(operation, self.now_ms(), secrets.randbelow(251))
            self._schedule_recovery()
            await self._notify()

    def _schedule_recovery(self) -> None:
        if not self._history_deletion and self._peer is not None and self.core.snapshot().phase == self.runtime.api.ClientPhase.RECOVERING and \
                (self._recovery is None or self._recovery.done()):
            self._recovery = asyncio.create_task(self._recover(), name="iroh-client-recovery")

    async def _recover(self) -> None:
        try:
            while not self._closing and self._peer is not None:
                state = self.core.snapshot()
                if state.phase != self.runtime.api.ClientPhase.RECOVERING or state.next_retry_ms is None:
                    break
                await asyncio.sleep(max(0, state.next_retry_ms - self.now_ms()) / 1000)
                if self.core.snapshot().operation != state.operation or self._closing:
                    break
                try:
                    await self._connect_existing(*self._peer)
                    break
                except (ConnectionError, TimeoutError):
                    continue
                except (SessionDenied, ProtectedTransportStateUnavailable,
                        self.runtime.api.BindingError.PermissionDenied, self.runtime.api.BindingError.InvalidInput):
                    self.core.deny()
                    await self._notify()
                    break
        except asyncio.CancelledError:
            raise

    async def _retire_attempt(self, operation: int) -> None:
        for identifier, current in tuple(self._requests.items()):
            if current != operation:
                continue
            context = self._contexts.get(identifier)
            if context is not None:
                self.runtime.disconnect(context, user_requested=False)
            else:
                self._cancel_unbound_dial(identifier)
        self.core.lost(operation, self.now_ms(), secrets.randbelow(251))

    def network_changed(self) -> None:
        if self.runtime is not None and not self._closing:
            self.runtime.network_changed()
            # Preserve the current logical session and streams. The next native
            # diagnostic observation restores Online when a path is selected.
            self.core.network_changed(self.core.snapshot().operation, False)
            for child in tuple(self._children):
                if child.core is not None and not child._closing:
                    child.core.network_changed(child.core.snapshot().operation, False)

    async def suspend(self) -> None:
        self._lifecycle_revision += 1
        if self.core is not None:
            self.core.suspend()
        await self._disconnect_owned()
        await asyncio.gather(*(child.suspend() for child in tuple(self._children)))
        await self._notify()

    async def disconnect(self) -> None:
        self._lifecycle_revision += 1
        if self.core is not None:
            self.core.disconnect()
        await self._disconnect_owned()
        await self._notify()

    async def quiesce_for_history_delete(self, delete: Callable) -> Any:
        """Fence dials and join retired writers before cancelling durable bodies."""
        async with self._gate:
            if self._history_deletion or self._closing:
                raise SessionDenied("client history cannot be changed now")
            self._history_deletion = True
        peer = self._peer
        active = completed = False
        revision = self._lifecycle_revision
        try:
            active = self.core is not None and self.core.snapshot().phase in {
                self.runtime.api.ClientPhase.ONLINE, self.runtime.api.ClientPhase.CHANGING_PATH,
                self.runtime.api.ClientPhase.RECOVERING, self.runtime.api.ClientPhase.CONNECTING,
                self.runtime.api.ClientPhase.AUTHORIZING, self.runtime.api.ClientPhase.ENROLLING}
            contexts = tuple(self._contexts.values())
            if active:
                await self.suspend()
            revision = self._lifecycle_revision
            if self.runtime is not None:
                for context in contexts:
                    await self.runtime.join_disconnected(context)
            result = await delete()
            completed = True
            return result
        finally:
            self._history_deletion = False
            if completed and active and peer is not None and not self._closing and revision == self._lifecycle_revision:
                await self._connect_existing(*peer)

    async def _disconnect_owned(self) -> None:
        self._fail(ConnectionError("client connection was canceled"))
        await self._cancel_recovery()
        if self.runtime is not None:
            for identifier in tuple(self._requests):
                if identifier not in self._contexts:
                    self._cancel_unbound_dial(identifier)
            for context in tuple(self._contexts.values()):
                try:
                    self.runtime.disconnect(context, user_requested=False)
                except SessionDenied:
                    pass

    async def _cancel_recovery(self) -> None:
        task, self._recovery = self._recovery, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _cancel_unbound_dial(self, identifier: int | None) -> None:
        if identifier is None or self.runtime is None:
            return
        try:
            self.runtime.endpoint.disconnect(identifier)
        except (self.runtime.api.BindingError.UnknownConnection, self.runtime.api.BindingError.Closed):
            pass
        self._requests.pop(identifier, None)
        if self._endpoint_owner is not None:
            self._endpoint_owner._child_requests.pop(identifier, None)

    async def close(self) -> None:
        self._closing = True
        if self._close_task is None or (self._close_task.done() and self._close_task.exception() is not None):
            self._close_task = asyncio.create_task(self._close_owned(), name="iroh-client-close")
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await self._close_task
            raise

    async def _close_owned(self) -> None:
        await self.disconnect()
        results = await asyncio.gather(*(child.close() for child in tuple(self._children)), return_exceptions=True)
        if any(isinstance(result, BaseException) for result in results):
            raise RuntimeError("shared client connection cleanup failed")
        async with self._gate:
            runtime = self.runtime
            if runtime is not None:
                if self._endpoint_owner is None:
                    await runtime.close()
                else:
                    for context in tuple(self._contexts.values()):
                        await runtime.join_disconnected(context)
            self.runtime = None
            self._contexts.clear()
            self._requests.clear()
            self._peer, self._channel = None, None
            if self._endpoint_owner is not None:
                owner = self._endpoint_owner
                owner._children.discard(self)
                for request, child in tuple(owner._child_requests.items()):
                    if child is self:
                        owner._child_requests.pop(request)

