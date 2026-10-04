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
        self._recovery: asyncio.Task | None = None
        self._closing = False

    async def start(self, *, enroll: bool = True, unlocked_password: str | None = None) -> None:
        async with self._gate:
            if self.runtime is not None:
                return
            if self._closing:
                raise ConnectionError("client endpoint is closed")
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
        await self._cancel_recovery()
        async with self._gate:
            future = self._new_future()
            request = None
            try:
                encoded = json.dumps(answer, allow_nan=False, separators=(",", ":"))
                operation = self.core.begin_verified_pairing(encoded, self.now_ms())
                request = self.runtime.dial(answer["ticket"], answer["endpoint_id"], pairing=True)
                self.core.bind_dial(operation, request)
                self._requests[request] = operation
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
        await self._cancel_recovery()
        self._peer = (endpoint_id, ticket)
        return await self._connect_existing(endpoint_id, ticket)

    async def _connect_existing(self, endpoint_id: str, ticket: str) -> Any:
        if self.runtime is None or self._closing:
            raise ConnectionError("client endpoint is not running")
        async with self._gate:
            grant = await asyncio.to_thread(self.grants.grant_for_endpoint, endpoint_id)
            floor = await asyncio.to_thread(self.grants.generation_floor, endpoint_id)
            future = self._new_future()
            request = None
            try:
                operation = self.core.begin_session(_grant_json(grant), ticket, floor, self.now_ms())
                request = self.runtime.dial(ticket, endpoint_id)
                self.core.bind_dial(operation, request)
                self._requests[request] = operation
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

    def _context(self, context: ConnectionContext) -> Any:
        return self.runtime.api.ClientConnectionContext(connection_id=context.connection_id,
            remote_endpoint=context.remote_endpoint_id, local_endpoint=context.local_endpoint_id,
            exporter=context.exporter, protocol=context.protocol, initiator=context.initiator)

    async def _connected(self, runtime: IrohSessionRuntime, context: ConnectionContext) -> None:
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
        operation = self._requests.get(context.connection_id)
        if operation is None or self._contexts.get(context.connection_id) != context or self._closing:
            raise SessionDenied("obsolete client connection")
        try:
            action = self.core.receive(operation, self._context(context), payload, self.now_ms())
            if action.capabilities_json is not None:
                self._capabilities = json.loads(action.capabilities_json)
            if context.protocol == "autoyou/pair/1":
                if action.close_connection:
                    grant = _decode_grant(action.grant_json)
                    await asyncio.to_thread(self.grants.register, grant)
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
        if channel is not self._channel or not self.is_ready or self.on_binary_frame is None:
            raise SessionDenied("client stream capability is not attached")
        await self.on_binary_frame(channel, frame)

    def _fail(self, error: Exception) -> None:
        if self._future is not None and not self._future.done():
            self._future.set_exception(error)

    async def _closed(self, context: ConnectionContext, binding: SessionBinding | None, user_requested: bool) -> None:
        operation = self._requests.pop(context.connection_id, None)
        self._contexts.pop(context.connection_id, None)
        if operation is None or operation != self.core.snapshot().operation:
            return
        if binding is not None and self._channel is not None and self._channel.binding == binding:
            self._channel = None
            await self.on_closed(context, binding, user_requested)
        self._fail(ConnectionError("client connection closed"))
        if not self._closing:
            if user_requested:
                self.core.disconnect()
            else:
                self.core.lost(operation, self.now_ms(), secrets.randbelow(251))
                self._schedule_recovery()
            await self._notify()

    async def _dial_failed(self, request: int, _code: str) -> None:
        operation = self._requests.pop(request, None)
        if operation is None or operation != self.core.snapshot().operation:
            return
        self._fail(ConnectionError("client connection failed"))
        if not self._closing:
            self.core.lost(operation, self.now_ms(), secrets.randbelow(251))
            self._schedule_recovery()
            await self._notify()

    def _schedule_recovery(self) -> None:
        if self._peer is not None and self.core.snapshot().phase == self.runtime.api.ClientPhase.RECOVERING and \
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
                try:
                    self.runtime.endpoint.disconnect(identifier)
                except (self.runtime.api.BindingError.UnknownConnection, self.runtime.api.BindingError.Closed):
                    pass
                self._requests.pop(identifier, None)
        self.core.lost(operation, self.now_ms(), secrets.randbelow(251))

    def network_changed(self) -> None:
        if self.runtime is not None and not self._closing:
            self.runtime.network_changed()
            # Preserve the current logical session and streams. The next native
            # diagnostic observation restores Online when a path is selected.
            self.core.network_changed(self.core.snapshot().operation, False)

    async def suspend(self) -> None:
        if self.core is not None:
            self.core.suspend()
        await self._disconnect_owned()
        await self._notify()

    async def disconnect(self) -> None:
        if self.core is not None:
            self.core.disconnect()
        await self._disconnect_owned()
        await self._notify()

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

    async def close(self) -> None:
        self._closing = True
        await self.disconnect()
        async with self._gate:
            runtime = self.runtime
            if runtime is not None:
                await runtime.close()
            self.runtime = None
            self._contexts.clear()
            self._requests.clear()
            self._peer, self._channel = None, None

