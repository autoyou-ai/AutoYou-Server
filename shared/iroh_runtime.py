# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Owned Python endpoint lifecycle with bounded, per-connection dispatch.

The enrollment callback is supplied by AutoYou's existing proof/account owner.
The transport never derives an owner, role or grant from an application header.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from shared.iroh_binding import load_binding
from shared.iroh_channel import IrohMessageChannel
from shared.iroh_keys import EndpointKeys
from shared.session_capacity import SendCapacity
from shared.session_events import ConnectionEvent, ConnectionEventKind, ConnectionEvents
from shared.session_transport import SessionBinding, SessionDenied, SessionRegistry, TransportKind

_MAX_DISPATCH_BYTES = 16 * 1024 * 1024
_MAX_CONNECTIONS = 32
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConnectionContext:
    connection_id: int
    remote_endpoint_id: str
    local_endpoint_id: str
    exporter: bytes
    initiator: bool
    transport_id: str
    protocol: str


@dataclass
class _Connection:
    context: ConnectionContext
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=130))
    worker: asyncio.Task | None = None
    channel: IrohMessageChannel | None = None
    user_disconnected: bool = False
    ready: bool = False
    deferred: list[Any] = field(default_factory=list)


class IrohSessionRuntime:
    def __init__(self, *, api: Any, endpoint: Any, lease: Any, role: str,
                 on_connected: Callable[[IrohSessionRuntime, ConnectionContext], Awaitable[None]],
                 on_enrollment: Callable[[IrohSessionRuntime, ConnectionContext, bytes], Awaitable[None]],
                 on_closed: Callable[[ConnectionContext, SessionBinding | None, bool], Awaitable[None]],
                 on_binary_frame: Callable[[IrohMessageChannel, Any], Awaitable[None]] | None = None,
                 on_dial_failed: Callable[[int, str], Awaitable[None]] | None = None) -> None:
        if role not in {"server", "client", "lite"}:
            raise ValueError("unsupported endpoint runtime role")
        self.api, self.endpoint, self._lease, self.role = api, endpoint, lease, role
        self.on_connected, self.on_enrollment, self.on_closed = on_connected, on_enrollment, on_closed
        self.on_binary_frame, self.on_dial_failed = on_binary_frame, on_dial_failed
        self.registry = SessionRegistry(now_ms=lambda: int(time.time() * 1000))
        self._connections: dict[int, _Connection] = {}
        self._instance_id = uuid.uuid4().hex
        self._dispatch_bytes = 0
        self._poll_task: asyncio.Task | None = None
        self._closing = False
        self._shutdown_task: asyncio.Task | None = None
        self._info = endpoint.endpoint_info()
        self._send_capacity = SendCapacity()
        self.events = ConnectionEvents()

    @classmethod
    async def start(cls, *, policy: dict[str, Any], keys: EndpointKeys, role: str,
                    enroll: bool = False, unlocked_password: str | None = None,
                    api: Any | None = None, **callbacks: Any) -> IrohSessionRuntime:
        api = api if api is not None else load_binding()
        if role not in {"server", "client", "lite"}:
            raise ValueError("unsupported endpoint runtime role")
        if getattr(keys, "role", role) != role or getattr(keys, "purpose", "identity") != "identity":
            raise ValueError("endpoint key scope does not match the runtime")
        policy_json = json.dumps(policy, allow_nan=False, separators=(",", ":"))

        def create() -> tuple[Any, Any]:
            lease = keys.claim_instance()
            try:
                seed = keys.load(enroll=enroll, unlocked_password=unlocked_password)
                endpoint = api.SharedEndpoint(policy_json, seed)
                return endpoint, lease
            except BaseException:
                lease.close()
                raise

        creation = asyncio.create_task(asyncio.to_thread(create))
        try:
            endpoint, lease = await asyncio.shield(creation)
        except asyncio.CancelledError:
            # A canceled caller cannot orphan the thread's eventual endpoint.
            try:
                endpoint, lease = await creation
                await asyncio.to_thread(endpoint.shutdown)
                lease.close()
            finally:
                raise
        try:
            runtime = cls(api=api, endpoint=endpoint, lease=lease, role=role, **callbacks)
        except BaseException:
            try:
                await asyncio.to_thread(endpoint.shutdown)
            finally:
                lease.close()
            raise
        runtime._poll_task = asyncio.create_task(runtime._poll_loop(), name="iroh-session-poll")
        return runtime

    @property
    def endpoint_info(self) -> Any:
        if self._closing:
            raise ConnectionError("endpoint runtime is closed")
        # Refresh routing hints after interface changes without changing identity.
        return self.endpoint.endpoint_info()

    def dial(self, ticket: str, expected_endpoint: str, *, pairing: bool = False) -> int:
        if self._closing:
            raise ConnectionError("endpoint runtime is closed")
        return self.endpoint.dial(ticket, expected_endpoint, pairing)

    def _connection(self, context: ConnectionContext) -> _Connection:
        connection = self._connections.get(context.connection_id)
        if self._closing or connection is None or connection.context != context:
            raise SessionDenied("connection is closed or superseded")
        return connection

    def admit(self, context: ConnectionContext, binding: SessionBinding) -> IrohMessageChannel:
        connection = self._connection(context)
        if connection.channel is not None or binding.endpoint_id != context.remote_endpoint_id or \
                binding.transport_id != context.transport_id or binding.transport != TransportKind.IROH:
            raise SessionDenied("grant does not match the authenticated connection")
        self.registry.validate_install(binding)
        channel = IrohMessageChannel(api=self.api, endpoint=self.endpoint, connection_id=context.connection_id,
            binding=binding, registry=self.registry, role=self.role, activated=False, capacity=self._send_capacity)
        grant = self.api.SessionGrant(endpoint_id=binding.endpoint_id, device_id=binding.device_id,
            owner_id=binding.owner_key, conversation_id=binding.conversation_key,
            generation=binding.generation, authorization_epoch=binding.authorization_epoch,
            expires_at_ms=binding.expires_at_ms, scopes=sorted(binding.scopes))
        try:
            self.endpoint.admit(context.connection_id, grant)
        except self.api.BindingError.PermissionDenied:
            raise SessionDenied("session grant was rejected") from None
        old = self.registry.install(binding, channel)
        connection.channel = channel
        if old is not None:
            old.disconnect()
        return channel

    def activate(self, context: ConnectionContext) -> None:
        connection = self._connection(context)
        if connection.channel is None:
            raise SessionDenied("session has not been authorized")
        self.registry.check(connection.channel.binding)
        self.endpoint.activate(context.connection_id)
        connection.channel._activated = True
        if not connection.ready:
            self.events.emit(ConnectionEvent(ConnectionEventKind.READY, context.connection_id,
                context.transport_id, connection.channel.binding.generation,
                connection.channel.binding.authorization_epoch))
        connection.ready = True

    def send_enrollment(self, context: ConnectionContext, payload: bytes) -> None:
        self._connection(context)
        if not isinstance(payload, bytes) or not payload or len(payload) > 16 * 1024:
            raise ValueError("invalid enrollment frame")
        self.endpoint.send(context.connection_id,
            self.api.TransportFrame(lane=1, generation=0, stream_id=0, sequence=0, payload=payload), None)

    def disconnect(self, context: ConnectionContext, *, user_requested: bool = True) -> None:
        connection = self._connection(context)
        connection.user_disconnected = user_requested
        if connection.channel:
            connection.channel.disconnect()
        else:
            self.endpoint.disconnect(context.connection_id)

    def network_changed(self) -> None:
        if not self._closing:
            self.endpoint.network_changed()

    async def _poll_loop(self) -> None:
        try:
            while not self._closing:
                for event in self.endpoint.poll(64):
                    if event.kind == self.api.TransportEventKind.CONNECTED:
                        if len(self._connections) >= _MAX_CONNECTIONS or len(event.exporter) != 32 or event.protocol not in {"autoyou/pair/1", "autoyou/session/1"}:
                            self.endpoint.disconnect(event.connection_id)
                            continue
                        context = ConnectionContext(event.connection_id, event.endpoint_id, self._info.endpoint_id,
                            bytes(event.exporter), event.initiator, f"iroh_{self._instance_id}_{event.connection_id}", event.protocol)
                        connection = _Connection(context)
                        self._connections[event.connection_id] = connection
                        connection.queue.put_nowait(event)
                        connection.worker = asyncio.create_task(self._dispatch_connection(connection), name="iroh-session-dispatch")
                    elif event.kind == self.api.TransportEventKind.FAILED:
                        self.events.emit(ConnectionEvent(ConnectionEventKind.FAILED,
                            event.connection_id, None, code='connection_failed'))
                        if self.on_dial_failed:
                            await self.on_dial_failed(event.connection_id, event.error_code or "connection_failed")
                    else:
                        connection = self._connections.get(event.connection_id)
                        if connection is None:
                            continue
                        size = len(event.frame.payload) if event.frame else 0
                        urgent = event.frame and event.frame.lane in {1, 2, 9}
                        budget = _MAX_DISPATCH_BYTES if urgent else _MAX_DISPATCH_BYTES - 1024*1024
                        limit = 128 if urgent else 112
                        if event.frame and (connection.queue.qsize() + len(connection.deferred) >= limit or self._dispatch_bytes + size > budget):
                            self.disconnect(connection.context, user_requested=False)
                            continue
                        self._dispatch_bytes += size
                        connection.queue.put_nowait(event)
                self._send_capacity.pulse()
                await asyncio.sleep(0.005)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A worker/protocol failure has a bounded, terminal owner. Never
            # reconnect through another transport or leave an orphan endpoint.
            asyncio.create_task(self.close(), name="iroh-runtime-failure-close")

    async def _dispatch_connection(self, connection: _Connection) -> None:
        try:
            while not self._closing:
                event = await connection.queue.get()
                size = len(event.frame.payload) if event.frame else 0
                retained = False
                try:
                    if event.kind == self.api.TransportEventKind.CONNECTED:
                        self.events.emit(ConnectionEvent(ConnectionEventKind.CONNECTED,
                            connection.context.connection_id, connection.context.transport_id))
                        await self.on_connected(self, connection.context)
                    elif event.kind == self.api.TransportEventKind.CLOSED:
                        break
                    elif event.frame.lane == 1:
                        await self.on_enrollment(self, connection.context, bytes(event.frame.payload))
                    elif connection.channel is None:
                        raise SessionDenied("application frame precedes session authorization")
                    elif not connection.ready:
                        connection.deferred.append(event.frame)
                        retained = True
                    else:
                        await self._dispatch_frame(connection, event.frame)
                finally:
                    if not retained:
                        self._dispatch_bytes -= size
                    connection.queue.task_done()
                if connection.ready:
                    while connection.deferred:
                        frame = connection.deferred.pop(0)
                        try:
                            await self._dispatch_frame(connection, frame)
                        finally:
                            self._dispatch_bytes -= len(frame.payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                self.disconnect(connection.context, user_requested=False)
            except (SessionDenied, self.api.BindingError.Closed, self.api.BindingError.UnknownConnection):
                pass
        finally:
            self._dispatch_bytes -= sum(len(frame.payload) for frame in connection.deferred)
            connection.deferred.clear()
            while not connection.queue.empty():
                event = connection.queue.get_nowait()
                self._dispatch_bytes -= len(event.frame.payload) if event.frame else 0
                connection.queue.task_done()
            self._connections.pop(connection.context.connection_id, None)
            binding = connection.channel.binding if connection.channel else None
            self.events.emit(ConnectionEvent(ConnectionEventKind.CLOSED,
                connection.context.connection_id, connection.context.transport_id,
                binding.generation if binding else None, binding.authorization_epoch if binding else None,
                user_requested=connection.user_disconnected))
            if connection.channel:
                await connection.channel.cleanup()
            try:
                await self.on_closed(connection.context, binding, connection.user_disconnected)
            except Exception:
                _LOG.error("Iroh session cleanup callback failed")

    async def _dispatch_frame(self, connection: _Connection, frame: Any) -> None:
        if frame.lane in {7, 8, 9}:
            self.registry.check(connection.channel.binding)
            if self.on_binary_frame is None:
                raise SessionDenied("binary capability is not attached")
            await self.on_binary_frame(connection.channel, frame)
        else:
            await connection.channel.receive_frame(frame)

    async def close(self) -> None:
        if self._shutdown_task is None:
            self._closing = True
            self._send_capacity.close()
            self._shutdown_task = asyncio.create_task(self._close_owned(), name="iroh-runtime-shutdown")
        try:
            await asyncio.shield(self._shutdown_task)
        except asyncio.CancelledError:
            await self._shutdown_task
            raise

    async def _close_owned(self) -> None:
        tasks = [connection.worker for connection in self._connections.values() if connection.worker]
        if self._poll_task and self._poll_task is not asyncio.current_task():
            tasks.append(self._poll_task)
        for task in tasks:
            task.cancel()
        try:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            try:
                await asyncio.to_thread(self.endpoint.shutdown)
            finally:
                if self._lease is not None:
                    self._lease.close()
                    self._lease = None
                self.events.close()
