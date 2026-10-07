# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Python application adapter over the Rust-owned authenticated session core."""

from __future__ import annotations

import asyncio
from collections import deque
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
                 capacity: SendCapacity | None = None, send_timeout: float = 30.0,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
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
        self._browser_receiver: Any = None
        self._browser_streams: dict[tuple[int, int], dict[str, Any]] = {}
        self._browser_buffered_bytes = 0
        self._browser_body_closing: dict[int, asyncio.Task] = {}
        self.http_request_preflight: Callable | None = None
        self._browser_deliveries: dict[str, dict[str, Any]] = {}
        self._browser_delivery_bytes = 0
        self._browser_cancelled: dict[str, float] = {}
        self._monotonic = monotonic

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
                    active_logical_streams=getattr(diagnostic, "active_logical_streams", None),
                    pending_stream_receipts=getattr(diagnostic, "pending_stream_receipts", None),
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
        if len(payload) > 1024 * 1024:
            # The send path validates/extracts this browser business envelope
            # before reservation. It can never become a large control frame.
            return payload
        return bytes(self.api.validate_envelope(payload))

    async def send_message(self, message: DataChannelMessage, **options: Any) -> bool:
        if options:
            raise ValueError("unsupported Iroh application send option")
        if not self.connection_active or not self._activated:
            return False
        self.registry.check(self.binding)
        if message.header.message_type == MessageType.CHAT and message.payload.get("context"):
            files = getattr(self, "native_files", None)
            from shared.iroh_context import outgoing_context, needs_file_capability
            if files is None and needs_file_capability(message.payload["context"]):
                raise SessionDenied("attachment capability is unavailable")
            if files is not None:
                context = await outgoing_context(files, message.payload["context"])
                self.registry.check(self.binding)
                prepared = replace(message, payload=dict(message.payload, context=context))
                prepared.wire_extensions = getattr(message, "wire_extensions", {})
                message = prepared
        payload = self._wire(message)
        delivery = getattr(self, "native_delivery", None)
        if self.role != "server" and delivery is not None and self.api.delivery_prompt_supported(payload):
            return await delivery.send(payload)
        return await self._send_payload(payload)

    async def _send_payload(self, payload: bytes) -> bool:
        if not self.connection_active or not self._activated:
            return False
        self.registry.check(self.binding)
        parts = self.api.prepare_browser_message(payload) if len(payload) > 1024 * 1024 else None
        lane = parts.lane if parts is not None else self.api.application_lane(payload)
        retained = len(payload) if parts is None else len(parts.metadata) + len(parts.data) + 64 * (2 + (len(parts.data) + 49151) // 49152)
        reservation = self._capacity.reserve(retained, control=lane in {1, 2, 9})
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
                        if lane in {4, 5, 6}:
                            if parts is not None:
                                self.endpoint.send_browser_parts(self.connection_id, self.binding.generation, parts)
                            else:
                                self.endpoint.send_browser_message(self.connection_id, self.binding.generation, payload)
                        else:
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

    async def send_http_request(self, message: DataChannelMessage, body: Any) -> bool:
        """Forward a sealed encrypted body in bounded native records."""
        from shared.iroh_body import BLOCK_BYTES, EncryptedBody
        if not isinstance(body, EncryptedBody) or message.header.message_type != MessageType.HTTP_REQUEST:
            raise ValueError("invalid native HTTP request")
        self.registry.check(self.binding, scope="browser")
        if not self.is_ready:
            return False
        metadata = self._wire(message)
        writer = self.api.ByteStreamWriter(4, self.api.ByteStreamContent.RAW_BODY, body.total, metadata)
        stream_id = self.endpoint.allocate_byte_stream(self.connection_id, 4)
        async def send(payload: bytes) -> bool:
            return await self.send_byte_record(4, stream_id, bytes(payload))
        opened = finished = False
        queued = 0
        try:
            if not await send(writer.open_record()):
                return False
            opened = True
            async for data in body.iterate():
                for offset in range(0, len(data), BLOCK_BYTES):
                    if not await send(writer.data_record(data[offset:offset + BLOCK_BYTES])):
                        return False
                    queued += min(BLOCK_BYTES, len(data) - offset)
            if not await send(writer.finish_record()):
                return False
            finished = True
            return True
        finally:
            if opened and not finished:
                try:
                    # A new owned task can send the cancellation after its caller
                    # was cancelled. It never moves a request to a fresh generation.
                    task = asyncio.create_task(send(writer.cancel_record(queued)))
                    accepted = await asyncio.shield(task)
                    if not accepted:
                        self.disconnect()
                except BaseException:
                    self.disconnect()

    async def send_byte_record(self, lane: int, stream_id: int, payload: bytes) -> bool:
        if lane not in {4, 5, 6, 7} or stream_id < 2 or not isinstance(payload, bytes) or len(payload) > 65536:
            raise ValueError("invalid finite native byte record")
        reservation = self._capacity.reserve(len(payload), control=False)
        if reservation is None:
            return False
        frame = self.api.TransportFrame(lane=lane, generation=self.binding.generation,
            stream_id=stream_id, sequence=0, payload=payload)
        try:
            async with asyncio.timeout(self._send_timeout):
                while self.is_ready and not self._capacity.closed:
                    self.registry.check(self.binding, scope="files" if lane == 7 else "browser")
                    try:
                        self.endpoint.send(self.connection_id, frame, None)
                        return True
                    except self.api.BindingError.Backpressure:
                        await self._capacity.wait_for_progress()
                return False
        except (TimeoutError, self.api.BindingError.Closed, self.api.BindingError.UnknownConnection):
            return False
        except self.api.BindingError.PermissionDenied:
            raise SessionDenied("byte operation is outside its verified session scope") from None
        finally:
            reservation.release()

    async def send_input_record(self, payload: bytes, sequence: int, *, current_check=None, deadline_ms=None) -> bool:
        """Send finite live input on its reserved lane, without durable replay."""
        if not isinstance(payload, bytes) or not 0 < len(payload) <= 16384 or \
                type(sequence) is not int or not 0 < sequence < 2**64:
            raise ValueError("invalid native input record")
        if not self.is_ready:
            return False
        self.registry.check(self.binding, scope="control")
        reservation = self._capacity.reserve(len(payload), control=True)
        if reservation is None:
            return False
        now_ms = int(time.time() * 1000)
        if deadline_ms is not None and (type(deadline_ms) is not int or deadline_ms <= now_ms):
            reservation.release()
            return False
        deadline_ms = min(now_ms + 200,deadline_ms) if deadline_ms is not None else now_ms + 200
        try:
            frame = self.api.TransportFrame(lane=9, generation=self.binding.generation,
                stream_id=0, sequence=0, payload=payload)
            async with asyncio.timeout(min(self._send_timeout, 0.2)):
                while self.is_ready and not self._capacity.closed:
                    self.registry.check(self.binding, scope="control")
                    if int(time.time()*1000) >= deadline_ms or current_check is not None and current_check() is not True:
                        return False
                    try:
                        self.endpoint.send(self.connection_id, frame, deadline_ms)
                        return True
                    except self.api.BindingError.Backpressure:
                        await self._capacity.wait_for_progress()
                return False
        except (TimeoutError, getattr(self.api.BindingError, "Timeout", TimeoutError), self.api.BindingError.Closed, self.api.BindingError.UnknownConnection):
            return False
        except self.api.BindingError.PermissionDenied:
            raise SessionDenied("native input is outside its verified control scope") from None
        finally:
            reservation.release()

    async def _receive_browser_frame(self, frame: Any) -> DataChannelMessage | None:
        from shared.iroh_body import EncryptedBody, BodyUnavailable
        from shared.iroh_delivery import _joined_disk
        self.registry.check(self.binding, scope="browser")
        if self._browser_receiver is None:
            self._browser_receiver = self.api.ByteStreamReceiver()
        record = self._browser_receiver.accept(frame.lane, frame.stream_id, bytes(frame.payload))
        key = (frame.lane, frame.stream_id)
        if record.kind == self.api.ByteStreamKind.OPEN:
            metadata = bytes(record.metadata)
            envelope = json.loads(metadata)
            is_request = self.role == "server" and envelope["header"]["message_type"] == "http_request"
            request_id = envelope["payload"].get("request_id")
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 256:
                raise SessionDenied("browser request identity is invalid")
            rejected = is_request and (self._browser_request_cancelled(request_id) or
                self.http_request_preflight is not None and not await self.http_request_preflight(self._decode_message(metadata)))
            self.registry.check(self.binding, scope="browser")
            rejected = rejected or is_request and self._browser_request_cancelled(request_id)
            request_body = is_request and not rejected and \
                record.content != self.api.ByteStreamContent.NONE
            limit = 4 * 1024 * 1024 if envelope["header"]["message_type"] == "http_ws_data" else 1024 * 1024
            if len(self._browser_streams) >= 64 or not request_body and not rejected and record.total > limit:
                raise SessionDenied("buffered browser message exceeds its bound")
            aad = json.dumps([self.binding.endpoint_id, self.binding.device_id, self.binding.conversation_key,
                self.binding.generation, self.binding.authorization_epoch, frame.stream_id], separators=(",", ":")).encode()
            body = await _joined_disk(lambda: EncryptedBody(total=None if record.total == (1 << 64) - 1 else record.total, associated_data=aad)) if request_body else None
            try:
                self.registry.check(self.binding, scope="browser")
                if is_request and self._browser_request_cancelled(request_id):
                    rejected = True
                    if body is not None:
                        self._retire_browser_body(body); body = None
            except BaseException:
                if body is not None:
                    self._retire_browser_body(body)
                raise
            self._browser_streams[key] = dict(metadata=metadata, request_id=request_id, content=record.content,
                body=body, data=bytearray(), rejected=rejected,
                upload_credit=is_request and record.content==self.api.ByteStreamContent.RAW_BODY and frame.stream_id>=2)
            return None
        state = self._browser_streams.get(key)
        if state is None:
            raise SessionDenied("browser stream has no metadata")
        if record.kind == self.api.ByteStreamKind.DATA:
            if state["rejected"]:
                # The shared Rust reader still validates offsets and digest.
                # An unauthorized body consumes neither disk nor application RAM.
                if state["upload_credit"]:
                    await self.acknowledge_byte_progress(frame.lane,frame.stream_id,record.offset+len(record.data))
                return None
            if state["body"] is not None:
                try:
                    await _joined_disk(state["body"].append, bytes(record.data))
                except BodyUnavailable:
                    if not state["rejected"]:
                        raise
            else:
                if self._browser_buffered_bytes + len(record.data) > 8 * 1024 * 1024:
                    raise SessionDenied("browser stream aggregate buffer is exhausted")
                state["data"].extend(record.data)
                self._browser_buffered_bytes += len(record.data)
            if state["upload_credit"]:
                await self.acknowledge_byte_progress(frame.lane,frame.stream_id,record.offset+len(record.data))
            return None
        del self._browser_streams[key]
        self._browser_buffered_bytes -= len(state["data"])
        body = state["body"]
        try:
            if record.kind == self.api.ByteStreamKind.ABORT:
                if body is not None:
                    self._retire_browser_body(body)
                if frame.stream_id >= 2:
                    await self._acknowledge_browser((frame.lane, frame.stream_id, record.offset, bytes(record.digest)))
                return None
            message = None
            if not state["rejected"]:
                if body is not None:
                    await _joined_disk(body.verify)
                    canonical = state["metadata"]
                else:
                    canonical = bytes(self.api.restore_browser_message(state["metadata"], state["content"], bytes(state["data"])))
                if self._browser_request_cancelled(state["request_id"]):
                    if body is not None:
                        self._retire_browser_body(body)
                    if frame.stream_id >= 2:
                        await self._acknowledge_browser((frame.lane, frame.stream_id, record.total, bytes(record.digest)))
                    return None
                message = self._decode_message(canonical)
                if body is not None:
                    message.native_http_body = body
                receipt = (frame.lane, frame.stream_id, record.total, bytes(record.digest)) if frame.stream_id >= 2 else None
                self._queue_browser_message(message, len(canonical), receipt)
                return message
            if frame.stream_id == 1:
                return None
            # This receipt covers byte completion (including a denied body that
            # was discarded). It cannot prove any HTTP mutation succeeded.
            await self._acknowledge_browser((frame.lane, frame.stream_id, record.total, bytes(record.digest)))
            return None
        except BaseException:
            if body is not None:
                self._retire_browser_body(body)
            raise

    def _retire_browser_body(self, body: Any) -> None:
        identity = id(body)
        if identity in self._browser_body_closing:
            return
        from shared.iroh_delivery import _joined_disk
        task = asyncio.create_task(_joined_disk(body.close), name="iroh-browser-body-close")
        self._browser_body_closing[identity] = task
        def finished(job):
            if self._browser_body_closing.get(identity) is job:
                self._browser_body_closing.pop(identity)
            if not job.cancelled() and job.exception() is not None:
                self.disconnect()
        task.add_done_callback(finished)

    async def acknowledge_byte_progress(self,lane:int,stream_id:int,offset:int)->None:
        async with asyncio.timeout(self._send_timeout):
            while self.is_ready and not self._capacity.closed:
                self.registry.check(self.binding,scope="browser")
                try:
                    self.endpoint.acknowledge_byte_progress(self.connection_id,lane,stream_id,offset)
                    return
                except self.api.BindingError.Backpressure:
                    await self._capacity.wait_for_progress()
        raise ConnectionError("native upload consumer credit was not queued")

    async def _acknowledge_browser(self, receipt: tuple[int, int, int, bytes]) -> None:
        await self.acknowledge_byte_stream(*receipt)

    async def acknowledge_byte_stream(self, lane: int, stream_id: int, total: int, digest: bytes) -> None:
        if lane not in {4, 5, 6, 7} or stream_id < 2:
            raise ValueError("invalid finite native byte receipt")
        receipt = (lane, stream_id, total, digest)
        async with asyncio.timeout(self._send_timeout):
            while self.is_ready:
                self.registry.check(self.binding, scope="files" if lane == 7 else "browser")
                try:
                    self.endpoint.acknowledge_byte_stream(self.connection_id, *receipt)
                    return
                except self.api.BindingError.Backpressure:
                    await self._capacity.wait_for_progress()
        raise SessionDenied("byte stream completion could not be acknowledged")

    def _queue_browser_message(self, message: DataChannelMessage, retained_bytes: int,
                               receipt: tuple[int, int, int, bytes] | None) -> None:
        request_id = message.payload.get("request_id")
        if retained_bytes <= 0 or not isinstance(request_id, str) or not 1 <= len(request_id) <= 256 or \
                self._browser_delivery_bytes + retained_bytes > 32 * 1024 * 1024:
            raise SessionDenied("browser delivery capacity is exhausted")
        queue = self._browser_deliveries.get(request_id)
        if queue is None:
            if len(self._browser_deliveries) >= 64:
                raise SessionDenied("browser request capacity is exhausted")
            queue = self._browser_deliveries[request_id] = dict(items=deque(), worker=None)
        if len(queue["items"]) >= 64:
            raise SessionDenied("browser request queue is exhausted")
        queue["items"].append((message, retained_bytes, receipt))
        self._browser_delivery_bytes += retained_bytes
        if queue["worker"] is None:
            queue["worker"] = asyncio.create_task(self._dispatch_browser_request(request_id, queue),
                name="iroh-browser-request")

    async def _dispatch_browser_request(self, request_id: str, queue: dict[str, Any]) -> None:
        try:
            while queue["items"]:
                message, retained_bytes, receipt = queue["items"].popleft()
                body = getattr(message, "native_http_body", None)
                try:
                    self.registry.check(self.binding, scope="browser")
                    if not self.is_ready or self._browser_deliveries.get(request_id) is not queue:
                        raise SessionDenied("browser delivery is obsolete")
                    cancelled = self.role == "server" and message.header.message_type == MessageType.HTTP_REQUEST and \
                        (getattr(message, "native_http_cancelled", False) or self._browser_request_cancelled(request_id))
                    if not cancelled:
                        await self._deliver_message(message)
                        # The existing HTTP business handler owns a handed-off body.
                        body = None
                    if receipt is not None:
                        await self._acknowledge_browser(receipt)
                finally:
                    if body is not None:
                        self._retire_browser_body(body)
                    self._browser_delivery_bytes -= retained_bytes
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Only the offending admitted peer is closed. Its obsolete worker
            # cannot rebind or deliver into another generation.
            self.disconnect()
        finally:
            while queue["items"]:
                message, retained_bytes, _ = queue["items"].popleft()
                self._browser_delivery_bytes -= retained_bytes
                body = getattr(message, "native_http_body", None)
                if body is not None:
                    self._retire_browser_body(body)
            if self._browser_deliveries.get(request_id) is queue:
                del self._browser_deliveries[request_id]

    async def receive_frame(self, frame: Any) -> DataChannelMessage | None:
        self.registry.check(self.binding)
        if frame.generation != self.binding.generation or not self.connection_active or not self._activated:
            raise SessionDenied("obsolete session frame")
        if frame.lane in {4, 5, 6} and bytes(frame.payload).startswith(b"AYBS"):
            return await self._receive_browser_frame(frame)
        canonical = bytes(self.api.validate_envelope(bytes(frame.payload)))
        if self.api.application_lane(canonical) != frame.lane:
            raise SessionDenied("application message is on the wrong lane")
        message = self._decode_message(canonical)
        if frame.lane in {4, 5, 6}:
            self._queue_browser_message(message, len(canonical), None)
            return message
        await self._deliver_message(message)
        return message

    def _decode_message(self, canonical: bytes) -> DataChannelMessage:
        data = json.loads(canonical)
        if isinstance(data["payload"].get("raw_headers"), list):
            data["payload"]["headers"] = {pair[0]: pair[1] for pair in data["payload"]["raw_headers"]
                if isinstance(pair, list) and len(pair) == 2 and all(isinstance(part, str) for part in pair)}
        raw_header = data["header"]
        header = MessageHeader(
            message_id=raw_header["message_id"], message_type=MessageType(raw_header["message_type"]),
            timestamp=raw_header["timestamp"], session_id=self.session_id,
            user_id=self.binding.canonical_user_id if self.role == "server" else raw_header.get("user_id"),
        )
        header.wire_extensions = {key: value for key, value in raw_header.items() if key not in header.to_dict()}
        message = DataChannelMessage(header=header, payload=data["payload"])
        if self.role == "server" and header.message_type in {MessageType.HTTP_REQUEST_CANCEL, MessageType.HTTP_STREAM_ABORT}:
            self._cancel_browser_request(message.payload.get("request_id"))
        message.wire_extensions = {key: value for key, value in data.items() if key not in {"header", "payload", "chunk_info"}}
        return message

    def _browser_request_cancelled(self, request_id: str) -> bool:
        now = self._monotonic()
        self._browser_cancelled = {key: until for key, until in self._browser_cancelled.items() if until > now}
        return request_id in self._browser_cancelled

    def _cancel_browser_request(self, request_id: Any) -> None:
        self.registry.check(self.binding, scope="browser")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 256:
            raise SessionDenied("browser cancellation identity is invalid")
        self._browser_request_cancelled(request_id)
        if request_id not in self._browser_cancelled and len(self._browser_cancelled) >= 4096:
            raise SessionDenied("browser cancellation capacity is exhausted")
        self._browser_cancelled[request_id] = self._monotonic() + 120
        for state in self._browser_streams.values():
            if state["request_id"] == request_id:
                state["rejected"] = True
                if state["body"] is not None:
                    self._retire_browser_body(state["body"]); state["body"] = None
                self._browser_buffered_bytes -= len(state["data"]); state["data"].clear()
        queue = self._browser_deliveries.get(request_id)
        if queue is not None:
            for message, _, _ in queue["items"]:
                if message.header.message_type == MessageType.HTTP_REQUEST:
                    message.native_http_cancelled = True
                    body = getattr(message, "native_http_body", None)
                    if body is not None:
                        self._retire_browser_body(body)

    async def _deliver_message(self, message: DataChannelMessage) -> None:
        header = message.header
        if self.relay_interceptor is not None:
            claimed = self.relay_interceptor(message)
            if inspect.isawaitable(claimed):
                claimed = await claimed
            if claimed:
                return
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
        workers = [queue["worker"] for queue in self._browser_deliveries.values() if queue["worker"] is not asyncio.current_task()]
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        # A task canceled before its first instruction has no finally block.
        # Retire its queued bodies here as well.
        for queue in list(self._browser_deliveries.values()):
            while queue["items"]:
                message, retained_bytes, _ = queue["items"].popleft()
                self._browser_delivery_bytes -= retained_bytes
                body = getattr(message, "native_http_body", None)
                if body is not None:
                    self._retire_browser_body(body)
        self._browser_deliveries.clear()
        for state in self._browser_streams.values():
            if state["body"] is not None:
                self._retire_browser_body(state["body"])
        self._browser_streams.clear()
        while self._browser_body_closing:
            tasks = tuple(self._browser_body_closing.values())
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
        self._browser_cancelled.clear()
        self._browser_buffered_bytes = 0
        if self._browser_receiver is not None:
            self._browser_receiver.shutdown()
            self._browser_receiver = None
