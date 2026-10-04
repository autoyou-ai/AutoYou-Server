# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Python application adapter over the Rust-owned authenticated session core."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from dataclasses import replace
from typing import Any, Callable

from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType
from shared.session_transport import SessionBinding, SessionDenied, SessionRegistry, TransportKind
from shared.session_capacity import SendCapacity


class IrohMessageChannel:
    transport_kind = TransportKind.IROH

    def __init__(self, *, api: Any, endpoint: Any, connection_id: int,
                 binding: SessionBinding, registry: SessionRegistry, role: str, activated: bool = True,
                 capacity: SendCapacity | None = None, send_timeout: float = 30.0) -> None:
        if role not in {"server", "client", "lite"}:
            raise ValueError("unsupported session role")
        binding.validate()
        self.api, self.endpoint, self.connection_id = api, endpoint, connection_id
        self.binding, self.registry, self.role = binding, registry, role
        self.session_id = binding.transport_id
        self.connection_active = True
        self._activated = activated
        self.message_handlers: dict[MessageType, Callable] = {}
        self.last_ping_time: float | None = None
        self._pings: dict[str, float] = {}
        self._pong_waiters: dict[str, asyncio.Future] = {}
        self._last_rtt_ms: float | None = None
        self._pong_payload_augmenter: Callable | None = None
        self._ping_payload_augmenter: Callable | None = None
        self.on_ping_pong_event: Callable | None = None
        self.relay_interceptor: Callable | None = None
        if not 0 < send_timeout <= 60:
            raise ValueError("invalid session send timeout")
        self._capacity = capacity if capacity is not None else SendCapacity()
        self._send_timeout = send_timeout

    @property
    def is_ready(self) -> bool:
        if not self.connection_active or not self._activated:
            return False
        try:
            return self.registry.check(self.binding) is self
        except SessionDenied:
            return False

    def register_handler(self, message_type: MessageType, handler: Callable) -> None:
        self.message_handlers[message_type] = handler

    def set_session_id(self, session_id: str) -> None:
        # Client wire headers cannot rebind an Iroh session after admission.
        if session_id != self.binding.transport_id:
            raise SessionDenied("verified session identity cannot be rebound by a message")

    def set_pong_payload_augmenter(self, callback: Callable | None) -> None:
        self._pong_payload_augmenter = callback

    def set_ping_payload_augmenter(self, callback: Callable | None) -> None:
        self._ping_payload_augmenter = callback

    def get_metrics(self) -> dict[str, Any]:
        result = {"last_ping_time": self.last_ping_time, "recovery_attempts": 0,
                "successful_recoveries": 0, "last_measured_rtt_ms": self._last_rtt_ms,
                "transport": "iroh", "generation": self.binding.generation}
        if self.is_ready:
            try:
                diagnostic = self.endpoint.diagnostics(self.connection_id)
                result.update(path=diagnostic.path_kind, path_count=diagnostic.open_paths,
                    transport_rtt_ms=diagnostic.rtt_ms, queued_send_bytes=diagnostic.queued_send_bytes,
                    native_send_bytes=diagnostic.held_send_bytes, native_receive_bytes=diagnostic.held_receive_bytes,
                    host_pending_bytes=self._capacity.pending_bytes)
            except (self.api.BindingError.Closed, self.api.BindingError.UnknownConnection,
                    self.api.BindingError.PermissionDenied):
                result["path"] = "unavailable"
        return result

    def _wire(self, message: DataChannelMessage) -> bytes:
        if not isinstance(message, DataChannelMessage) or message.chunk_info is not None:
            raise ValueError("Iroh requires an application envelope without legacy chunks")
        header = dict(getattr(message.header, "wire_extensions", {}))
        header.update(replace(message.header, session_id=self.session_id).to_dict())
        envelope = dict(getattr(message, "wire_extensions", {}))
        envelope.update(header=header, payload=message.payload)
        payload = json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        return bytes(self.api.validate_envelope(payload))

    async def send_message(self, message: DataChannelMessage, **options: Any) -> bool:
        if options:
            raise ValueError("unsupported Iroh application send option")
        if not self.connection_active or not self._activated:
            return False
        self.registry.check(self.binding)
        payload = self._wire(message)
        lane = self.api.application_lane(payload)
        reservation = self._capacity.reserve(len(payload), control=lane in {1, 2, 9})
        if reservation is None:
            return False
        frame = self.api.TransportFrame(lane=lane, generation=self.binding.generation,
            stream_id=0, sequence=0, payload=payload)
        try:
            async with asyncio.timeout(self._send_timeout):
                while self.connection_active and self._activated and not self._capacity.closed:
                    # A generation, revocation or expiry change while waiting
                    # cannot retry bytes under the next session's authority.
                    self.registry.check(self.binding)
                    try:
                        self.endpoint.send(self.connection_id, frame, None)
                        return True
                    except self.api.BindingError.Backpressure:
                        await self._capacity.wait_for_progress()
                return False
        except TimeoutError:
            return False
        except (self.api.BindingError.Closed, self.api.BindingError.UnknownConnection):
            self.connection_active = False
            return False
        except self.api.BindingError.PermissionDenied:
            raise SessionDenied("application operation is outside its verified session scope") from None
        finally:
            reservation.release()

    async def receive_frame(self, frame: Any) -> DataChannelMessage:
        self.registry.check(self.binding)
        if frame.generation != self.binding.generation or not self.connection_active or not self._activated:
            raise SessionDenied("obsolete session frame")
        canonical = bytes(self.api.validate_envelope(bytes(frame.payload)))
        if self.api.application_lane(canonical) != frame.lane:
            raise SessionDenied("application message is on the wrong lane")
        data = json.loads(canonical)
        raw_header = data["header"]
        header = MessageHeader(
            message_id=raw_header["message_id"], message_type=MessageType(raw_header["message_type"]),
            timestamp=raw_header["timestamp"], session_id=self.session_id,
            user_id=self.binding.canonical_user_id if self.role == "server" else raw_header.get("user_id"),
        )
        header.wire_extensions = {key: value for key, value in raw_header.items() if key not in header.to_dict()}
        message = DataChannelMessage(header=header, payload=data["payload"])
        message.wire_extensions = {key: value for key, value in data.items() if key not in {"header", "payload", "chunk_info"}}
        if self.relay_interceptor is not None and self.relay_interceptor(message):
            return message
        if header.message_type == MessageType.PING:
            self.last_ping_time = time.time()
            self._emit_ping_event("ping_received", message.payload.get("game"))
            pong_payload = {"ping_id": header.message_id, "timestamp": time.time()}
            if self._pong_payload_augmenter:
                extra = self._pong_payload_augmenter(message)
                if inspect.isawaitable(extra):
                    extra = await extra
                if isinstance(extra, dict):
                    pong_payload.update(extra)
            await self.send_message(DataChannelMessage(
                header=MessageHeader(str(uuid.uuid4()), MessageType.PONG, time.time(), self.session_id, "transport"),
                payload=pong_payload,
            ))
            self._emit_ping_event("pong_sent", pong_payload.get("game"))
        elif header.message_type == MessageType.PONG:
            ping_id = str(message.payload.get("ping_id") or "")
            sent_at = self._pings.pop(ping_id, None)
            if sent_at is not None:
                self._last_rtt_ms = max(0.0, (time.monotonic() - sent_at) * 1000)
                self.last_ping_time = time.time()
                waiter = self._pong_waiters.pop(ping_id, None)
                if waiter is not None and not waiter.done():
                    waiter.set_result(True)
                self._emit_ping_event("pong_received", message.payload.get("game"))
        handler = self.message_handlers.get(header.message_type)
        if handler:
            result = handler(message)
            if inspect.isawaitable(result):
                await result
        return message

    def _emit_ping_event(self, event: str, game: Any) -> None:
        if self.on_ping_pong_event is not None:
            # Presentation callbacks cannot break transport liveness.
            try:
                self.on_ping_pong_event(event, game if isinstance(game, dict) else None)
            except Exception:
                pass

    async def _send_ping(self, ping_id: str, session_id: str | None, *, check: bool) -> bool:
        if session_id is not None and session_id != self.session_id:
            raise SessionDenied("connection test is outside the verified session")
        now = time.monotonic()
        for key, sent_at in tuple(self._pings.items()):
            if now - sent_at >= 60 and key not in self._pong_waiters:
                self._pings.pop(key, None)
        if len(self._pings) >= 32:
            return False
        payload = {"timestamp": time.time(), "keepalive": True, "ping_id": ping_id}
        if check:
            payload["connection_check"] = True
        if self.role == "client" and self._last_rtt_ms is not None:
            payload["rtt_ms"] = self._last_rtt_ms
        if self._ping_payload_augmenter:
            extra = self._ping_payload_augmenter(None)
            if inspect.isawaitable(extra):
                extra = await extra
            if isinstance(extra, dict):
                payload.update(extra)
        self._pings[ping_id] = now
        sent = await self.send_message(DataChannelMessage(
            header=MessageHeader(ping_id, MessageType.PING, time.time(), self.session_id, "transport"), payload=payload,
        ))
        if not sent:
            self._pings.pop(ping_id, None)
        else:
            self._emit_ping_event("user_ping_sent" if check else "keepalive_ping_sent", None)
        return sent

    async def send_ping(self, session_id: str | None = None) -> bool:
        return await self._send_ping(str(uuid.uuid4()), session_id, check=False)

    async def send_ping_and_wait(self, session_id: str | None = None, timeout: float = 4.0) -> bool:
        if not 0 < timeout <= 60 or not self.is_ready or len(self._pong_waiters) >= 32:
            return False
        ping_id = str(uuid.uuid4())
        waiter = asyncio.get_running_loop().create_future()
        self._pong_waiters[ping_id] = waiter
        try:
            if not await self._send_ping(ping_id, session_id, check=True):
                return False
            return bool(await asyncio.wait_for(asyncio.shield(waiter), timeout=timeout))
        except TimeoutError:
            return False
        finally:
            self._pong_waiters.pop(ping_id, None)
            self._pings.pop(ping_id, None)
            if not waiter.done():
                waiter.cancel()

    def disconnect(self) -> None:
        if self.connection_active:
            self.connection_active = False
            try:
                self.endpoint.disconnect(self.connection_id)
            except (self.api.BindingError.Closed, self.api.BindingError.UnknownConnection):
                pass
        self._pings.clear()
        for waiter in self._pong_waiters.values():
            if not waiter.done():
                waiter.set_result(False)
        self._pong_waiters.clear()
        self._capacity.pulse()

    async def cleanup(self) -> None:
        self.disconnect()
        self.registry.retire(self.binding)
        self.message_handlers.clear()
