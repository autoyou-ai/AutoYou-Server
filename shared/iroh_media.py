# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Owned Python media adapter over the common Rust carriage/playout contract.

Only an application's current call/consent owner calls approve_source. Packets,
session admission and network recovery cannot approve or restart capture. Codec
and device consumers have separate tasks so media cannot pin control dispatch.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from copy import deepcopy
import json
import time
from typing import Any, Awaitable, Callable

from shared.iroh_media_codec import AVMediaDecoder, AVMediaEncoder, CapturedMedia, EncodingQuality
from shared.session_media import MediaKind, MediaSourceBinding
from shared.session_transport import SessionDenied, TransportKind
from shared.iroh_media_budget import NativeMediaBudget, NativeMediaReservation


async def _join_owned(task: asyncio.Task) -> Any:
    cancellation = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = exc
    result = task.result()
    if cancellation is not None:
        raise cancellation
    return result


async def _join_cleanup(task: asyncio.Task) -> Any:
    """Cleanup after an acquisition failure must observe the physical result.

    The enclosing acquisition still propagates its original failure/cancellation.
    Cancellation of that observer cannot turn a completed clean close into an
    uncertain native shutdown or return its reservation before the close joins.
    """
    try:
        return await _join_owned(task)
    except asyncio.CancelledError:
        return task.result()


@dataclass
class _Source:
    binding: MediaSourceBinding
    codec: Any
    capture: Callable[[], Awaitable[Any]] | None
    render: Callable[[Any], Awaitable[None]] | None
    close_adapter: Callable[[], Awaitable[None]]
    device_queue_us: Callable[[], int] | None
    queue: asyncio.Queue
    reservation: NativeMediaReservation | None = None
    worker: asyncio.Task | None = None
    closing: bool = False
    cleanup: asyncio.Task | None = None
    codec_jobs: set[asyncio.Task] = field(default_factory=set)
    codec_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    decoder_revision: int = 0
    rendered_revision: int = 0
    retirement: asyncio.Task | None = None
    dropped_frames: int = 0
    consent_check: Callable[[], None] | None = None
    apply_feedback: Callable[[Any], Awaitable[None]] | None = None


@dataclass(frozen=True)
class MediaSourceObservation:
    binding: MediaSourceBinding
    current_check: Callable[[], bool]
    frame: Any = None
    retired: bool = False


class IrohMedia:
    def __init__(self, *, channel: Any, now_ms: Callable[[], int] | None = None,
                 monotonic_us: Callable[[], int] | None = None,
                 encoder_factory: Callable = AVMediaEncoder, decoder_factory: Callable = AVMediaDecoder) -> None:
        if channel.binding.transport != TransportKind.IROH:
            raise ValueError("native media requires an Iroh session")
        self.channel, self.api, self.endpoint, self.binding = channel, channel.api, channel.endpoint, channel.binding
        self.now_ms = now_ms or (lambda: time.time_ns()//1_000_000)
        self.monotonic_us = monotonic_us or (lambda: time.monotonic_ns()//1000)
        self.encoder_factory, self.decoder_factory = encoder_factory, decoder_factory
        self._sources: dict[tuple[str,int], _Source] = {}
        self._observers: dict[object, Callable[[MediaSourceObservation], None]] = {}
        self._closing = False
        self._cleanup = None
        self._playout_worker = None
        self._gate = asyncio.Lock()
        self._codec_capacity = asyncio.Semaphore(2)
        self._codec_jobs: set[asyncio.Task] = set()
        self._retirements: set[asyncio.Task] = set()
        self._cleanup_error: BaseException | None = None
        self.source_budget = NativeMediaBudget.process()
        self.negotiation: Any = None
        self.call_owner: Any = None
        grant = self.api.SessionGrant(endpoint_id=self.binding.endpoint_id,device_id=self.binding.device_id,
            owner_id=self.binding.owner_key,conversation_id=self.binding.conversation_key,generation=self.binding.generation,
            authorization_epoch=self.binding.authorization_epoch,expires_at_ms=self.binding.expires_at_ms,scopes=sorted(self.binding.scopes))
        self.playout = self.api.MediaPlayout(grant,self.now_ms())

    def _check(self) -> None:
        if self._closing or not self.channel.is_ready or self.channel.registry.check(self.binding,scope="media") is not self.channel:
            raise SessionDenied("media session is closed or superseded")

    def _source_check(self, source: _Source) -> None:
        self._check()
        if source.closing or self._sources.get((source.binding.direction,source.binding.source_id)) is not source:
            raise SessionDenied("media source is closed or superseded")
        source.binding.check(self.channel.registry,now_ms=self.now_ms(),approved_source=source.binding,
            current_media_generation=source.binding.media_generation)
        if source.consent_check is not None: source.consent_check()

    def observe_inbound(self, observer: Callable[[MediaSourceObservation], None]) -> Callable[[], None]:
        """Bounded synchronous metadata/sample consumers; no capture or consent widening."""
        self._check()
        if not callable(observer) or len(self._observers) >= 4:
            raise SessionDenied("native media observer is unavailable")
        token = object()
        self._observers[token] = observer
        for source in tuple(self._sources.values()):
            if source.binding.direction == "receive" and not source.closing:
                self._observe(source, tokens=(token,))
        def stop():
            self._observers.pop(token, None)
        return stop

    def _observe(self, source: _Source, frame=None, *, retired=False, tokens=None) -> None:
        if source.binding.direction != "receive":
            return
        deadline = frame.expires_at_us if frame is not None else None
        def current():
            try:
                self._source_check(source)
                return deadline is None or self.monotonic_us() < deadline
            except SessionDenied:
                return False
        for token in tuple(self._observers) if tokens is None else tokens:
            observer = self._observers.get(token)
            if observer is None or not retired and not current():
                continue
            try:
                observer(MediaSourceObservation(source.binding, current, deepcopy(frame), retired))
            except Exception:
                # A metadata consumer cannot end the primary physical playback.
                self._observers.pop(token, None)
                for owned in tuple(self._sources.values()):
                    if owned.binding.direction == "receive":
                        try:
                            observer(MediaSourceObservation(owned.binding, lambda: False, retired=True))
                        except Exception:
                            pass

    async def _codec(self, source: _Source | None, function: Callable, *args) -> Any:
        if source is not None:
            async with source.codec_lock:
                return await self._codec_job(source,function,*args)
        return await self._codec_job(None,function,*args)

    async def _codec_job(self, source: _Source | None, function: Callable, *args) -> Any:
        async with self._codec_capacity:
            if source is not None:
                self._source_check(source)
            job = asyncio.create_task(asyncio.to_thread(function,*args),name="iroh-media-codec")
            self._codec_jobs.add(job)
            if source is not None: source.codec_jobs.add(job)
            try:
                return await _join_owned(job)
            finally:
                self._codec_jobs.discard(job)
                if source is not None: source.codec_jobs.discard(job)

    async def join_source_predecessor(self, binding: MediaSourceBinding) -> None:
        """Join the old physical adapter before allocating a replacement device."""
        async with self._gate:
            self._check()
            if self._cleanup_error is not None:
                raise RuntimeError("previous media adapter cleanup failed")
            previous = self._sources.get((binding.direction, binding.source_id))
            if previous is not None and previous.binding != binding:
                if binding.media_generation <= previous.binding.media_generation:
                    raise SessionDenied("media device replacement requires a newer source generation")
                await self._stop_source(previous)

    async def approve_source(self, binding: MediaSourceBinding, *,
            capture: Callable[[], Awaitable[Any]] | None = None,
            render: Callable[[Any], Awaitable[None]] | None = None,
            close_adapter: Callable[[], Awaitable[None]],
            device_queue_us: Callable[[], int] | None = None,
            quality: EncodingQuality = EncodingQuality(),
            consent_check: Callable[[], None] | None = None,
            reservation: NativeMediaReservation | None = None,
            encoded: bool = False,
            apply_feedback: Callable[[Any], Awaitable[None]] | None = None) -> bool:
        async with self._gate:
            self._check()
            if self._cleanup_error is not None:
                raise RuntimeError("previous media adapter cleanup failed")
            if binding.session != self.binding or (binding.direction == "send" and (capture is None or render is not None)) or \
                    (binding.direction == "receive" and (render is None or capture is not None)) or \
                    type(encoded) is not bool or encoded and binding.direction != "send" or \
                    apply_feedback is not None and (binding.direction != "send" or not callable(apply_feedback)):
                raise SessionDenied("media adapter does not match approved direction/session")
            binding.check(self.channel.registry,now_ms=self.now_ms(),approved_source=binding,
                current_media_generation=binding.media_generation)
            if consent_check is not None: consent_check()
            if reservation is not None:
                if reservation.budget is not self.source_budget or reservation.attached:
                    raise SessionDenied("native source capacity is already transferred or foreign")
                reservation.check(binding)
            key = (binding.direction,binding.source_id)
            previous = self._sources.get(key)
            if previous is not None:
                if previous.binding == binding and not previous.closing:
                    # The caller still owns its duplicate adapter. It returns
                    # the untransferred reservation only after that close joins.
                    return False
                if binding.media_generation <= previous.binding.media_generation:
                    raise SessionDenied("media adapter replacement requires an approved new generation")
                await self._stop_source(previous)
            reservation = reservation or self.source_budget.reserve(binding)
            if reservation.budget is not self.source_budget:
                raise SessionDenied("native source capacity belongs to another process scope")
            reservation.check(binding)
            reservation.attached = True
            creating = asyncio.create_task(self._codec(None,lambda: self.encoder_factory(api=self.api,binding=binding,quality=quality,
                **({"encoded": True} if encoded else {}))
                if binding.direction == "send" else self.decoder_factory(binding)))
            try:
                codec = await _join_owned(creating)
            except BaseException:
                try:
                    if not creating.cancelled() and creating.exception() is None:
                        await _join_cleanup(asyncio.create_task(asyncio.to_thread(creating.result().close)))
                    await _join_cleanup(asyncio.create_task(close_adapter()))
                except BaseException as error:
                    reservation.retain_failure(error, creating, close_adapter); self._cleanup_error = error
                else:
                    reservation.release()
                raise
            source = _Source(binding,codec,capture,render,close_adapter,device_queue_us,
                asyncio.Queue(maxsize=2 if binding.kind in {MediaKind.CAMERA,MediaKind.SCREEN} else 8))
            source.reservation = reservation
            source.consent_check = consent_check
            source.apply_feedback = apply_feedback
            try:
                self._check()
                if consent_check is not None: consent_check()
                record = json.dumps(binding.lease_record(),allow_nan=False,separators=(",", ":"))
                self.endpoint.approve_media_source(self.channel.connection_id,record,binding.direction == "receive")
                if binding.direction == "receive":
                    self.playout.approve_source(record,self.now_ms())
                self._sources[key] = source
                source.worker = asyncio.create_task(self._capture_source(source) if binding.direction == "send" else self._render_source(source),
                    name="iroh-media-source")
                if self._playout_worker is None:
                    self._playout_worker = asyncio.create_task(self._run_playout(),name="iroh-media-playout")
                self._observe(source)
            except BaseException:
                await self._stop_source(source)
                raise
            return True

    async def _capture_source(self, source: _Source) -> None:
        try:
            while True:
                self._source_check(source)
                remaining = (source.binding.expires_at_ms-self.now_ms())/1000
                frame = await asyncio.wait_for(source.capture(),timeout=max(0.001,remaining))
                self._source_check(source)
                captured_at_us = self.monotonic_us()
                current_check = None
                if isinstance(frame,CapturedMedia):
                    if type(frame.discontinuity) is not bool or (frame.current_check is not None and not callable(frame.current_check)):
                        raise SessionDenied("invalid local media capture fence")
                    if frame.discontinuity:
                        await self._codec(source,source.codec.frame_dropped)
                    current_check = frame.current_check
                    captured_at_us,frame = frame.captured_at_us,frame.frame
                    if type(captured_at_us) is not int or not 0 <= captured_at_us <= self.monotonic_us():
                        raise SessionDenied("invalid local media capture clock")
                if (current_check is not None and current_check() is not True) or \
                        self.monotonic_us()-captured_at_us >= source.binding.maximum_delay_ms*1000:
                    source.dropped_frames += 1
                    await self._codec(source,source.codec.frame_dropped)
                    continue
                packets = await self._codec(source,source.codec.encode,frame,captured_at_us)
                for packet in packets:
                    self._source_check(source)
                    budget_us = source.binding.maximum_delay_ms*1000-(self.monotonic_us()-captured_at_us)
                    if budget_us <= 0 or (current_check is not None and current_check() is not True):
                        source.dropped_frames += 1
                        await self._codec(source,source.codec.frame_dropped)
                        break
                    encoded = self.api.encode_media_packet(self.binding.generation,packet)
                    deadline = min(source.binding.expires_at_ms,self.now_ms()+(budget_us+999)//1000)
                    try:
                        self.endpoint.send(self.channel.connection_id,encoded,deadline)
                    except self.api.BindingError.Backpressure:
                        source.dropped_frames += 1
                        await self._codec(source,source.codec.frame_dropped)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Source failure retires capture without dropping application data.
            # Its cleanup owner joins this worker after the current await exits.
            self._request_retirement(source)

    async def receive(self, channel: Any, frame: Any) -> None:
        self._check()
        if channel is not self.channel or frame.lane != 8 or frame.generation != self.binding.generation:
            raise SessionDenied("media frame belongs to a different session")
        # Push is bounded native parsing/queue ownership. It never awaits a
        # decoder, device, model or UI callback on the session dispatch task.
        now_us = self.monotonic_us()
        arrival = getattr(frame,"_iroh_arrival_us",None)
        deadline = getattr(frame,"_iroh_deadline_us",None)
        if type(arrival) is not int or type(deadline) is not int:
            raise SessionDenied("media frame is missing its trusted receive timing")
        if now_us >= deadline:
            return
        self.playout.push(frame,arrival,now_us,self.now_ms())

    async def _run_playout(self) -> None:
        try:
            while not self._closing:
                now = self.now_ms()
                for source in tuple(self._sources.values()):
                    if not source.closing and now >= source.binding.expires_at_ms:
                        self._request_retirement(source)
                for frame in self.playout.take(4,self.monotonic_us(),now):
                    source = self._sources.get(("receive",frame.packet.source_id))
                    if source is None or source.closing: continue
                    self._source_check(source)
                    if source.queue.full():
                        while not source.queue.empty():
                            source.queue.get_nowait();source.queue.task_done();source.dropped_frames += 1
                        source.decoder_revision += 1
                        self.playout.decoder_discontinuity(source.binding.source_id,now)
                    source.queue.put_nowait(frame)
                await asyncio.sleep(0.005)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._cleanup is None:
                self._closing = True
                self._cleanup = asyncio.create_task(self._close_owned(),name="iroh-media-playout-close")

    async def _render_source(self, source: _Source) -> None:
        try:
            while True:
                frame = await source.queue.get()
                try:
                    self._source_check(source)
                    if self.monotonic_us() >= frame.expires_at_us:
                        source.dropped_frames += 1
                        self.playout.decoder_discontinuity(source.binding.source_id,self.now_ms())
                        source.decoder_revision += 1
                        continue
                    revision = source.decoder_revision
                    if revision != source.rendered_revision:
                        if source.binding.kind in {MediaKind.CAMERA,MediaKind.SCREEN} and not frame.packet.keyframe:
                            source.dropped_frames += 1
                            continue
                        frame.reset_decoder = True
                    self._observe(source, frame)
                    decoded = await self._codec(source,source.codec.decode,frame)
                    self._source_check(source)
                    if source.decoder_revision != revision or self.monotonic_us() >= frame.expires_at_us:
                        source.dropped_frames += 1
                        if source.decoder_revision == revision:
                            self.playout.decoder_discontinuity(source.binding.source_id,self.now_ms())
                            source.decoder_revision += 1
                        continue
                    source.rendered_revision = revision
                    await source.render(decoded)
                    self._source_check(source)
                    if source.device_queue_us is not None:
                        self.playout.audio_device_feedback(source.binding.source_id,source.device_queue_us(),self.now_ms())
                finally:
                    source.queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._request_retirement(source)

    def _request_retirement(self, source: _Source) -> None:
        if source.retirement is not None or source.closing: return
        if self._sources.get((source.binding.direction,source.binding.source_id)) is not source: return
        self._begin_stop_source(source)
        if self.negotiation is not None:
            try:
                self.negotiation.retire_source(source.binding)
            except Exception:
                self.negotiation._request_shutdown()
        source.retirement = asyncio.create_task(self._retire_source(source),name="iroh-media-source-retire")
        self._retirements.add(source.retirement)
        source.retirement.add_done_callback(self._retirements.discard)

    async def _retire_source(self, source: _Source) -> None:
        async with self._gate:
            if self._sources.get((source.binding.direction,source.binding.source_id)) is not source: return
            try:
                await self._stop_source(source)
            except BaseException as exc:
                self._cleanup_error = exc

    def feedback(self, source_id: int) -> Any:
        self._check()
        return self.playout.feedback(source_id,self.now_ms())

    async def apply_feedback(self, source_id: int, feedback: Any) -> None:
        self._check()
        source = self._sources.get(("send",source_id))
        if source is None: raise SessionDenied("media feedback has no approved sender")
        self._source_check(source)
        if source.apply_feedback is not None:
            job = asyncio.create_task(source.apply_feedback(feedback), name="iroh-media-adapter-feedback")
            source.codec_jobs.add(job)
            try:
                await _join_owned(job)
            finally:
                source.codec_jobs.discard(job)
            self._source_check(source)
        await self._codec(source,source.codec.feedback,feedback)

    async def revoke_source(self, source_id: int, direction: str) -> None:
        async with self._gate:
            source = self._sources.get((direction,source_id))
            if source is not None: await self._stop_source(source)

    async def _stop_source(self, source: _Source) -> None:
        self._begin_stop_source(source)
        await _join_owned(source.cleanup)

    def _begin_stop_source(self, source: _Source) -> None:
        if source.cleanup is None:
            source.closing = True
            self._observe(source, retired=True)
            error = None
            if source.binding.direction == "receive":
                try:
                    self.playout.revoke_source(source.binding.source_id)
                except BaseException as exc:
                    error = exc
            try:
                self.endpoint.revoke_media_source(self.channel.connection_id,source.binding.source_id,source.binding.direction == "receive")
            except (self.api.BindingError.Closed,self.api.BindingError.UnknownConnection):
                pass
            except BaseException as exc:
                if error is None: error = exc
            source.cleanup = asyncio.create_task(self._close_source_owned(source,error),name="iroh-media-source-close")

    def fence_source(self, binding: MediaSourceBinding) -> asyncio.Task | None:
        """Immediately fence the exact consent generation before queued work joins."""
        source = self._sources.get((binding.direction,binding.source_id))
        if source is None or source.binding != binding: return None
        self._begin_stop_source(source)
        return source.cleanup

    async def _close_source_owned(self, source: _Source, error: BaseException | None) -> None:
        try:
            # Native close must unblock its physical capture/render operations.
            # New source approval waits until this owner has joined them.
            await source.close_adapter()
        except BaseException as exc:
            if error is None: error = exc
        if source.worker is not None and source.worker is not asyncio.current_task():
            if not source.worker.done() and not source.worker.cancelling(): source.worker.cancel()
            await asyncio.gather(source.worker,return_exceptions=True)
        if source.codec_jobs: await asyncio.gather(*tuple(source.codec_jobs),return_exceptions=True)
        while not source.queue.empty(): source.queue.get_nowait();source.queue.task_done()
        try:
            await _join_owned(asyncio.create_task(asyncio.to_thread(source.codec.close)))
        except BaseException as exc:
            if error is None: error = exc
        key = (source.binding.direction,source.binding.source_id)
        if error is None and self._sources.get(key) is source: del self._sources[key]
        if error is not None:
            if source.reservation is not None: source.reservation.retain_failure(error, source)
            self._cleanup_error = error
            raise error
        if source.reservation is not None: source.reservation.release()

    async def closed(self, binding: Any) -> None:
        if binding == self.binding: await self.close()

    async def close(self) -> None:
        if self._cleanup is None:
            self._closing = True
            self._cleanup = asyncio.create_task(self._close_owned(),name="iroh-media-close")
        await _join_owned(self._cleanup)

    async def _close_owned(self) -> None:
        if self.call_owner is not None:
            self.call_owner.fence()
            try:
                await self.call_owner.join_controls()
            except BaseException as exc:
                if self._cleanup_error is None: self._cleanup_error = exc
        if self.negotiation is not None:
            try:
                await self.negotiation.close()
            except BaseException as exc:
                if self._cleanup_error is None: self._cleanup_error = exc
        if self._playout_worker is not None:
            self._playout_worker.cancel()
            await asyncio.gather(self._playout_worker,return_exceptions=True)
        async with self._gate:
            results = await asyncio.gather(*(self._stop_source(source) for source in tuple(self._sources.values())),return_exceptions=True)
            if self._codec_jobs: await asyncio.gather(*tuple(self._codec_jobs),return_exceptions=True)
            try:
                self.playout.shutdown()
            except BaseException as exc:
                if self._cleanup_error is None: self._cleanup_error = exc
        if self._retirements: await asyncio.gather(*tuple(self._retirements),return_exceptions=True)
        self._observers.clear()
        if self.call_owner is not None:
            try:
                await self.call_owner.close()
            except BaseException as exc:
                if self._cleanup_error is None: self._cleanup_error = exc
        for result in results:
            if isinstance(result,BaseException): raise result
        if self._cleanup_error is not None: raise self._cleanup_error


class IrohMediaService:
    """Shared handler owner; a call owner supplies every source adapter later."""
    def __init__(self, *, call_owner_factory: Callable | None = None) -> None:
        self._managers: dict[str,IrohMedia] = {}
        self.call_owner_factory = call_owner_factory

    async def prepared(self, _transport: Any, _context: Any, channel: Any) -> None:
        if "media" not in channel.binding.scopes: return
        if any(manager._cleanup_error is not None for manager in self._managers.values()):
            raise RuntimeError("previous media device cleanup failed")
        if channel.binding.transport_id in self._managers or len(self._managers) >= 32:
            raise SessionDenied("media channel capacity is exhausted")
        manager = IrohMedia(channel=channel)
        if _context is not None:
            from shared.iroh_media_negotiation import IrohMediaNegotiation
            manager.negotiation = IrohMediaNegotiation(media=manager,local_endpoint_id=_context.local_endpoint_id)
        if self.call_owner_factory is not None:
            try:
                manager.call_owner = self.call_owner_factory(manager, _context)
            except BaseException:
                await manager.close()
                raise
        self._managers[channel.binding.transport_id] = manager
        channel.native_media = manager

    async def ready(self, channel: Any) -> None:
        manager = self._managers.get(channel.binding.transport_id)
        if manager is not None and manager.channel is channel and manager.call_owner is not None:
            await manager.call_owner.ready()

    async def receive(self, channel: Any, frame: Any) -> None:
        manager = self._managers.get(channel.binding.transport_id)
        if manager is None or manager.channel is not channel:
            raise SessionDenied("media capability is not attached to this session")
        await manager.receive(channel,frame)

    async def closed(self, binding: Any) -> None:
        if binding is None: return
        manager = self._managers.get(binding.transport_id)
        if manager is not None and manager.binding == binding:
            await manager.closed(binding)
            if self._managers.get(binding.transport_id) is manager: del self._managers[binding.transport_id]
            if getattr(manager.channel,"native_media",None) is manager: manager.channel.native_media = None
