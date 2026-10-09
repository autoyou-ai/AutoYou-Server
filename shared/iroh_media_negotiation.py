# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Thin owned Python adapter for the shared Rust source negotiation state.

Applications supply local consent and lazy device factories. Signaling callbacks
only parse/fence/queue; acquiring codecs or devices cannot stall control dispatch.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from types import SimpleNamespace
from typing import Any, Awaitable, Callable
import uuid

from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType
from shared.iroh_media import _join_owned, _join_cleanup
from shared.iroh_media_codec import EncodingQuality
from shared.session_media import MediaSourceBinding, MediaKind, MediaCodec
from shared.session_transport import SessionDenied


def _json(value: Any) -> str:
    raw = json.dumps(value,allow_nan=False,separators=(",", ":"))
    if len(raw.encode("utf-8")) > 4096:
        raise SessionDenied("media signaling record is too large")
    return raw


def source_reference(binding: MediaSourceBinding) -> str:
    return _json(dict(call_id=binding.call_id,lease_id=binding.lease_id,
        source_id=binding.source_id,media_generation=binding.media_generation))


@dataclass
class NativeMediaAdapter:
    """A lazy factory transfers one adapter with idempotent, physically joined close."""
    close_owner: Callable[[], Awaitable[None]]
    capture: Callable[[], Awaitable[Any]] | None = None
    render: Callable[[Any], Awaitable[None]] | None = None
    device_queue_us: Callable[[], int] | None = None
    encoded: bool = False
    apply_feedback: Callable[[Any], Awaitable[None]] | None = None
    _closing: asyncio.Task | None = field(default=None,init=False)

    async def close(self) -> None:
        if self._closing is None:
            self._closing = asyncio.create_task(self.close_owner(),name="iroh-media-device-close")
        await _join_owned(self._closing)


@dataclass(frozen=True)
class _Approval:
    expires_at_ms: int
    factory: Callable[[MediaSourceBinding],Awaitable[NativeMediaAdapter]]
    quality: EncodingQuality


class IrohMediaNegotiation:
    def __init__(self, *, media: Any, local_endpoint_id: str) -> None:
        self.media, self.channel, self.api = media, media.channel, media.api
        binding = media.binding
        grant = self.api.SessionGrant(endpoint_id=binding.endpoint_id,device_id=binding.device_id,
            owner_id=binding.owner_key,conversation_id=binding.conversation_key,generation=binding.generation,
            authorization_epoch=binding.authorization_epoch,expires_at_ms=binding.expires_at_ms,scopes=sorted(binding.scopes))
        self.kernel = self.api.MediaNegotiation(grant,local_endpoint_id,media.now_ms())
        self._calls: dict[str,_Approval] = {}
        self._actions = asyncio.Queue(maxsize=64)
        self._signals = asyncio.Queue(maxsize=64)
        self._preparations: dict[tuple[str,int],tuple[MediaSourceBinding,asyncio.Task]] = {}
        self._device_closures: set[asyncio.Task] = set()
        self._closing = False
        self._cleanup: asyncio.Task | None = None
        self._cleanup_error: BaseException | None = None
        self._terminal_shutdown: asyncio.Task | None = None
        self._workers = [asyncio.create_task(self._run_actions(),name="iroh-media-negotiation"),
            asyncio.create_task(self._run_signals(),name="iroh-media-signaling"),
            asyncio.create_task(self._run_timer(),name="iroh-media-feedback")]

    def _check(self) -> None:
        if self._closing or self._cleanup_error is not None:
            raise SessionDenied("media negotiation is closed or cleanup failed")
        self.media._check()

    def approve_call(self, consent: dict[str,Any], *,
            adapter_factory: Callable[[MediaSourceBinding],Awaitable[NativeMediaAdapter]],
            quality: EncodingQuality = EncodingQuality()) -> None:
        """Only the application's local call/consent owner invokes this method."""
        self._check(); quality.check()
        raw = _json(consent)
        self.kernel.approve_call(raw,self.media.now_ms())
        call_id = consent["call_id"]
        # Repeated approval cannot transfer an active call's devices to another
        # callback owner. A changed policy requires explicit end/new call intent.
        previous = self._calls.get(call_id)
        if previous is not None:
            if previous.factory is not adapter_factory or previous.quality != quality:
                raise SessionDenied("media call already has a device owner")
            return
        self._calls[call_id] = _Approval(consent["expires_at_ms"],adapter_factory,quality)

    def offer_source(self, binding: MediaSourceBinding) -> None:
        self._check()
        if binding.session != self.media.binding or binding.direction != "send" or binding.call_id not in self._calls:
            raise SessionDenied("media offer is outside its local call consent")
        raw = self.kernel.offer_source(_json(binding.lease_record()),self.media.now_ms())
        self._queue_signal(raw)

    def retire_source(self, binding: MediaSourceBinding) -> None:
        """A failed local capture or renderer retires the peer's exact source."""
        if self._closing:
            return
        self._enqueue(self.kernel.revoke_source(source_reference(binding),binding.direction == "receive"))

    async def retire_source_and_join(self, binding: MediaSourceBinding) -> None:
        pending = [task for candidate, task in self._preparations.values() if candidate == binding]
        self.retire_source(binding)
        if pending:
            await _join_owned(asyncio.create_task(self._join_tasks(pending, revoked=True)))
        closure = self.media.fence_source(binding)
        if closure is not None:
            await _join_owned(closure)
        if self._cleanup_error is not None:
            raise self._cleanup_error

    async def receive_signal(self, control: Any) -> None:
        self._check()
        if not isinstance(control,dict):
            raise SessionDenied("invalid media signal")
        actions = self.kernel.receive(_json(control),self.media.now_ms())
        if control.get("action") == "end":
            self._calls.pop(control["call_id"],None)
            self._cancel_preparations(call_id=control["call_id"])
        self._enqueue(actions)
        if control.get("action") == "end" and self.media.call_owner is not None:
            self.media.call_owner.peer_end(control["call_id"])

    def _binding(self, action: Any) -> MediaSourceBinding:
        # This record is emitted by the validated Rust state machine. A wire
        # dictionary is never converted directly into application authority.
        record = json.loads(action.source_lease_json)
        if record.pop("authorization_epoch") != self.media.binding.authorization_epoch:
            raise SessionDenied("media source belongs to another authorization epoch")
        record["kind"], record["codec"] = MediaKind(record["kind"]),MediaCodec(record["codec"])
        return MediaSourceBinding(session=self.media.binding,direction="receive" if action.inbound else "send",**record)

    def _current(self, binding: MediaSourceBinding) -> None:
        self._check()
        if binding.call_id not in self._calls:
            raise SessionDenied("media call consent ended")
        self.kernel.check_source(source_reference(binding),binding.direction == "receive",self.media.now_ms())

    def _queue_signal(self, raw: str) -> None:
        if self._closing:
            return
        if len(raw.encode("utf-8")) > 4096:
            raise SessionDenied("invalid generated media signal")
        try:
            self._signals.put_nowait(raw)
        except asyncio.QueueFull:
            raise SessionDenied("media signaling capacity is exhausted") from None

    def _observe_closure(self, task: asyncio.Task | None) -> None:
        if task is None:
            return
        self._device_closures.add(task)
        def finished(owned: asyncio.Task) -> None:
            self._device_closures.discard(owned)
            if not owned.cancelled() and owned.exception() is not None:
                self._cleanup_error = owned.exception()
        task.add_done_callback(finished)

    def _cancel_preparations(self, *, call_id: str | None = None, binding: MediaSourceBinding | None = None) -> None:
        for pending,task in tuple(self._preparations.values()):
            matches = pending == binding if binding is not None else pending.call_id == call_id
            if matches and not task.done() and not task.cancelling(): task.cancel()

    def _enqueue(self, actions: list[Any]) -> None:
        kinds = self.api.MediaNegotiationActionKind
        for action in actions:
            if action.kind == kinds.CLOSE_SOURCE:
                binding = self._binding(action)
                self._cancel_preparations(binding=binding)
                self._observe_closure(self.media.fence_source(binding))
                if action.reply_json is not None: self._queue_signal(action.reply_json)
                continue
            if action.kind == kinds.REPLY:
                if action.reply_json is not None: self._queue_signal(action.reply_json)
                continue
            binding = self._binding(action)
            if action.kind in {kinds.PREPARE_RECEIVER,kinds.ACTIVATE_SENDER}:
                previous = self.media._sources.get((binding.direction,binding.source_id))
                if previous is not None and previous.binding != binding:
                    self._observe_closure(self.media.fence_source(previous.binding))
                pending = self._preparations.get((binding.direction,binding.source_id))
                if pending is not None and pending[0] != binding:
                    self._cancel_preparations(binding=pending[0])
            try:
                self._actions.put_nowait(action)
            except asyncio.QueueFull:
                if action.kind == kinds.APPLY_FEEDBACK:
                    continue
                raise SessionDenied("media acquisition capacity is exhausted") from None

    async def _prepare(self, binding: MediaSourceBinding) -> None:
        adapter = None
        creating = None
        reservation = None
        try:
            self._current(binding)
            await self.media.join_source_predecessor(binding)
            self._current(binding)
            approval = self._calls[binding.call_id]
            reservation = self.media.source_budget.reserve(binding)
            creating = asyncio.create_task(approval.factory(binding),name="iroh-media-device-acquire")
            try:
                adapter = await _join_owned(creating)
            except asyncio.CancelledError:
                if not creating.cancelled() and creating.exception() is None: adapter = creating.result()
                raise
            if not isinstance(adapter,NativeMediaAdapter):
                if adapter is not None:
                    error = TypeError("media factory did not transfer native adapter ownership")
                    reservation.retain_failure(error, adapter); self._cleanup_error = error
                raise TypeError("media factory must return an owned native adapter")
            self._current(binding)
            # The media owner retains the reservation through codec creation,
            # source work and physical retirement, including failed cleanup.
            acquired = await self.media.approve_source(binding,capture=adapter.capture,render=adapter.render,
                close_adapter=adapter.close,device_queue_us=adapter.device_queue_us,quality=approval.quality,
                consent_check=lambda: self._current(binding),reservation=reservation,
                **({"encoded": adapter.encoded, "apply_feedback": adapter.apply_feedback}
                    if adapter.encoded is not False or adapter.apply_feedback is not None else {}))
            if not acquired:
                await adapter.close()
                reservation.release()
            if binding.direction == "receive":
                self._queue_signal(self.kernel.receiver_prepared(source_reference(binding),self.media.now_ms()))
        except BaseException:
            self._observe_closure(self.media.fence_source(binding))
            if adapter is not None and isinstance(adapter,NativeMediaAdapter):
                try:
                    await _join_cleanup(asyncio.create_task(adapter.close()))
                except BaseException as exc:
                    self._cleanup_error = exc
                    if reservation is not None: reservation.retain_failure(exc, adapter)
            if reservation is not None and not reservation.attached and self._cleanup_error is None:
                reservation.release()
            raise

    async def _run_actions(self) -> None:
        kinds = self.api.MediaNegotiationActionKind
        try:
            while not self._closing:
                action = await self._actions.get()
                binding = self._binding(action)
                try:
                    try:
                        self._current(binding)
                    except (SessionDenied,self.api.BindingError.PermissionDenied):
                        continue
                    if action.kind == kinds.APPLY_FEEDBACK:
                        await self.media.apply_feedback(binding.source_id,SimpleNamespace(**json.loads(action.feedback_json)))
                        continue
                    key = (binding.direction,binding.source_id)
                    task = asyncio.create_task(self._prepare(binding),name="iroh-media-source-acquire")
                    self._preparations[key] = (binding,task)
                    try:
                        results = await asyncio.gather(task,return_exceptions=True)
                        if isinstance(results[0],Exception) and not self._closing:
                            self._enqueue(self.kernel.revoke_source(source_reference(binding),binding.direction == "receive"))
                    finally:
                        if self._preparations.get(key) == (binding,task): del self._preparations[key]
                finally:
                    self._actions.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._request_shutdown()

    async def _run_signals(self) -> None:
        try:
            while not self._closing:
                raw = await self._signals.get()
                try:
                    self._check()
                    message = DataChannelMessage(MessageHeader(uuid.uuid4().hex,MessageType.VOICE_CALL_CONTROL,
                        self.media.now_ms()/1000),dict(event="media_signal",control=json.loads(raw)))
                    if not await self.channel.send_message(message):
                        raise ConnectionError("media signaling session closed")
                finally:
                    self._signals.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._request_shutdown()

    async def _run_timer(self) -> None:
        try:
            while not self._closing:
                await asyncio.sleep(0.1)
                if not self.channel.is_ready:
                    continue
                now = self.media.now_ms()
                for call_id,approval in tuple(self._calls.items()):
                    if now >= approval.expires_at_ms:
                        self._calls.pop(call_id,None)
                        self._cancel_preparations(call_id=call_id)
                        if self.media.call_owner is not None:
                            self.media.call_owner.peer_end(call_id)
                self._enqueue(self.kernel.expire(now))
                for source in tuple(self.media._sources.values()):
                    if source.closing or source.binding.direction != "receive": continue
                    binding = source.binding
                    try:
                        self._current(binding)
                        feedback = self.media.feedback(binding.source_id)
                        body = dict(played_sequence=feedback.last_played_sequence or 0,lost_frames=feedback.missing,
                            jitter_us=feedback.jitter_us,buffered_frames=feedback.buffered_frames,
                            keyframe_required=feedback.keyframe_required,quality_scale_permille=feedback.quality_scale_permille)
                        self._queue_signal(self.kernel.feedback(source_reference(binding),_json(body),now))
                    except (SessionDenied,self.api.BindingError.PermissionDenied):
                        continue
        except asyncio.CancelledError:
            raise
        except Exception:
            self._request_shutdown()

    def _request_shutdown(self) -> None:
        if not self._closing and self._terminal_shutdown is None:
            self._closing = True
            # A broken/overfull signaling owner cannot reliably tell the peer
            # to release capture. Disconnect this session so both runtimes
            # fence its sources and physically join their call owners.
            try:
                self.channel.disconnect()
            except Exception as exc:
                self._cleanup_error = exc
            # Media's shutdown owner joins this negotiation and every physical
            # adapter. This callback never awaits itself or orphaned workers.
            self._terminal_shutdown = asyncio.create_task(self.media.close(),name="iroh-media-negotiation-failure")
            def finished(task: asyncio.Task) -> None:
                if not task.cancelled() and task.exception() is not None:
                    self._cleanup_error = task.exception()
            self._terminal_shutdown.add_done_callback(finished)

    async def end_call(self, call_id: str) -> None:
        self._check()
        self._calls.pop(call_id,None)
        pending = [task for binding,task in self._preparations.values() if binding.call_id == call_id]
        self._cancel_preparations(call_id=call_id)
        self._enqueue(self.kernel.end_call(call_id))
        if pending: await _join_owned(asyncio.create_task(self._join_tasks(pending, revoked=True)))
        closures = [source.cleanup for source in self.media._sources.values()
            if source.binding.call_id == call_id and source.cleanup is not None]
        if closures: await _join_owned(asyncio.create_task(self._join_tasks(closures)))
        if self._cleanup_error is not None: raise self._cleanup_error

    async def _join_tasks(self, tasks: list[asyncio.Task], *, revoked: bool = False) -> None:
        results = await asyncio.gather(*tasks,return_exceptions=True)
        for result in results:
            # Revocation can deny a physically completed factory at its final
            # consent check. The preparation has joined its device cleanup;
            # stored cleanup failures are still checked by the calling owner.
            if revoked and isinstance(result,(asyncio.CancelledError,SessionDenied,self.api.BindingError.PermissionDenied)):
                continue
            if isinstance(result,BaseException): raise result

    async def close(self) -> None:
        if self._cleanup is None:
            self._closing = True
            self._cleanup = asyncio.create_task(self._close_owned(),name="iroh-media-negotiation-close")
        await _join_owned(self._cleanup)

    async def _close_owned(self) -> None:
        try:
            self._enqueue(self.kernel.shutdown())
        except BaseException as exc:
            if self._cleanup_error is None: self._cleanup_error = exc
        self._calls.clear()
        for _binding,task in self._preparations.values():
            if not task.done() and not task.cancelling(): task.cancel()
        for worker in self._workers:
            if not worker.done() and not worker.cancelling(): worker.cancel()
        await asyncio.gather(*self._workers,return_exceptions=True)
        if self._preparations:
            await asyncio.gather(*(task for _binding,task in tuple(self._preparations.values())),return_exceptions=True)
        if self._device_closures:
            results = await asyncio.gather(*tuple(self._device_closures),return_exceptions=True)
            for result in results:
                if isinstance(result,BaseException) and self._cleanup_error is None: self._cleanup_error = result
        self._preparations.clear()
        for queue in (self._actions,self._signals):
            while not queue.empty(): queue.get_nowait();queue.task_done()
        if self._cleanup_error is not None: raise self._cleanup_error
