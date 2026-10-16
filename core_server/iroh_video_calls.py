# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Explicit native video intent beneath the current foreground audio call.

Video has its own immutable consent ID. Starting or changing video never widens
the already approved audio call. Factories run only after source acceptance.
"""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any
import uuid
import time

from shared.iroh_media import _join_owned
from shared.iroh_media_negotiation import NativeMediaAdapter
from shared.iroh_media_video import NativeVideoCapture, NativeVideoReceiver, UPSTREAM_VIDEO_SOURCE_ID, SERVER_VIDEO_SOURCE_ID
from shared.session_media import MediaCodec, MediaKind, MediaSourceBinding
from shared.session_transport import SessionDenied
from shared.iroh_input import matches_input_source, normalize_native_screen_input
from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType


class IrohServerVideoCalls:
    def __init__(self, *, parent: Any) -> None:
        self.parent, self.media, self.runtime = parent, parent.media, parent.runtime
        self._requested: tuple | None = None
        self._call: tuple | None = None
        self._cfg: dict = {}
        self._profile: dict = {}
        self._selection: tuple | None = None
        self._outgoing: MediaSourceBinding | None = None
        self._source_generation = 0
        self._desktop_control = None
        self._tutor = None
        self._camera: MediaSourceBinding | None = None
        self._screen_audio: tuple[MediaSourceBinding, NativeVideoCapture] | None = None
        self._screen_input_clock_ms = 0
        self._screen_input_sequence = 0
        self._screen_input_jobs: set[asyncio.Task] = set()
        self._groups: set[NativeVideoCapture] = set()
        self._ends: dict[str, asyncio.Task] = {}
        self._retiring: set[str] = set()
        self._factory = self._adapter
        self._controls = asyncio.Queue(maxsize=8)
        self._closing = False
        self._cleanup: asyncio.Task | None = None
        self._worker = asyncio.create_task(self._run(), name="iroh-video-controls")

    def _parent_check(self) -> None:
        self.parent._check()
        if self._closing or self.parent._call_id is None or self.parent._call_mode != "call" or \
                self.media.now_ms() >= self.parent._call_expiry:
            raise SessionDenied("native video requires the current foreground call")

    def _source_check(self, source: MediaSourceBinding) -> None:
        self._parent_check()
        if self._call is None or (source.call_id, source.expires_at_ms) != self._call[:2] or \
                self._call[2] != self.parent._call_id:
            raise SessionDenied("native video consent ended or was replaced")
        if not self._selection_current():
            raise SessionDenied("native video source settings changed; new consent is required")
        self.media.negotiation._current(source)

    def _capture_selection(self, cfg: dict) -> tuple:
        video = cfg.get("video_call", {})
        video = video if isinstance(video, dict) else {}
        remote = video.get("remote_desktop", {})
        remote = remote if isinstance(remote, dict) else {}
        profile = self.runtime._get_remote_desktop_capture_profile(cfg=cfg)
        sources = tuple(self.runtime._get_video_outbound_sources(cfg=cfg))
        desktop = "remote_desktop" in sources
        game = bool((self._requested or self._call) and (self._requested or self._call)[6])
        game_policy = (bool(self.runtime._get_game_mode_available(cfg=cfg)),
            json.dumps(remote.get("game_buttons"), sort_keys=True)) if game else None
        return (bool(video.get("enabled", True)), sources,
            (remote.get("enabled", True), remote.get("send_screen", True), profile.get("monitor_id")) if desktop else None,
            tuple((key, profile.get(key)) for key in ("max_width", "fps", "quality", "bitrate_kbps")) if sources else (),
            video.get("camera_device_id", 0) if "camera" in sources else None,
            video.get("api_video_source_id", "default") if "api" in sources else None,
            (self.runtime._get_video_file_path(cfg=cfg), self.runtime._get_video_file_loop_enabled(cfg=cfg)) if "video_file" in sources else None, game_policy)

    def _selection_current(self) -> bool:
        try:
            cfg = self.runtime.STATE.config or {}
            return self._selection is not None and self._selection == self._capture_selection(cfg) and self._selection[0]
        except Exception:
            return False

    def reconcile_video_policy(self) -> None:
        request = self._call
        if self._closing or request is None or self._selection_current():
            return
        self.fence()
        try:
            self._controls.put_nowait(("policy_end", request))
        except asyncio.QueueFull:
            self.media.negotiation._request_shutdown()

    def screen_audio_source(self) -> MediaSourceBinding | None:
        """Computer sound belongs to this accepted physical screen, never RTC aliases."""
        current = self._screen_audio
        if current is None:
            return None
        source, owner = current
        try:
            self._source_check(source)
        except Exception:
            # This optional authority query cannot authorize sound after a
            # kernel/endpoint denial or failure. Source cleanup owns diagnostics.
            return None
        active = self.media._sources.get((source.direction, source.source_id))
        if self.media.now_ms() >= source.expires_at_ms or source != self._outgoing or \
                owner._closed or owner not in self._groups or owner.track is None or \
                source.kind != MediaKind.SCREEN or \
                (active is not None and (active.binding != source or getattr(active, "closing", False))):
            return None
        return source

    def _clear_screen_audio(self, source: MediaSourceBinding | None = None) -> None:
        if self._screen_audio is not None and (source is None or self._screen_audio[0] == source):
            self._screen_audio = None
            self.parent.reconcile_audio_policy()

    async def receive(self, payload: dict) -> None:
        from core_server.iroh_audio_calls import audio_call_intent
        self._parent_check()
        if type(payload.get("active")) is not bool or type(payload.get("camera_active")) is not bool:
            raise SessionDenied("native video requires explicit video and camera intent")
        game = payload.get("game_mode", False)
        if type(game) is not bool or game and payload["camera_active"]:
            raise SessionDenied("native game video cannot open an upstream camera")
        parent_id, parent_expiry = audio_call_intent({**payload, "active": False},
            now_ms=-1, expires_at_ms=2**63-1)
        if (parent_id, parent_expiry) != (self.parent._call_id, self.parent._call_expiry):
            raise SessionDenied("native video control changed its parent call")
        video_id, expiry = audio_call_intent(dict(active=payload["active"], native_call=payload.get("native_video")),
            now_ms=self.media.now_ms() if payload["active"] else -1, expires_at_ms=2**63-1)
        if video_id == parent_id:
            raise SessionDenied("native video requires an independent consent ID")
        sending = payload.get("video_send", dict(kind=int(MediaKind.CAMERA), layout="single"))
        if not isinstance(sending, dict) or set(sending) != {"kind", "layout"} or \
                type(sending["kind"]) is not int or sending["kind"] not in {int(MediaKind.CAMERA), int(MediaKind.SCREEN)} or \
                sending["layout"] not in {"single", "composite"} or \
                ("video_send" in payload and not payload["camera_active"]):
            raise SessionDenied("native video send intent has an invalid source profile")
        expiry = min(expiry, parent_expiry, self.media.binding.expires_at_ms)
        request = (video_id, expiry, parent_id, payload["camera_active"], sending["kind"], sending["layout"], game)
        if payload["active"]:
            if self._requested is not None and self._requested != request:
                raise SessionDenied("native video change requires ending its previous intent")
            self._requested = request
            action = ("start", request)
        else:
            expected = self._requested or self._call
            if expected is None or expected[0] != video_id:
                return
            self.fence()
            action = ("end", video_id)
        try:
            self._controls.put_nowait(action)
        except asyncio.QueueFull:
            self.fence()
            self.media.negotiation._request_shutdown()
            raise SessionDenied("native video control capacity is exhausted") from None

    def _plan(self) -> dict:
        cfg = deepcopy(self.runtime.STATE.config or {})
        sources = tuple(self.runtime._get_available_video_outbound_sources(cfg=cfg))
        self._selection = self._capture_selection(cfg)
        game = self._requested is not None and self._requested[6]
        if game:
            if "remote_desktop" not in sources or not self.runtime._get_game_mode_available(cfg=cfg):
                raise SessionDenied("native game requires the configured desktop source and game policy")
            # Never allocate camera/API/file overlays for game reception. This
            # immutable child owns a full desktop source at the existing game rate.
            video = cfg.setdefault("video_call", {})
            video["outbound_source"] = "remote_desktop"
            video["outbound_sources"] = ["remote_desktop"]
            video.setdefault("remote_desktop", {})["fps"] = 30
            sources = ("remote_desktop",)
        profile = self.runtime._get_remote_desktop_capture_profile(cfg=cfg)
        width = max(320, min(3840, int(profile["max_width"]))) // 2 * 2
        height = max(180, min(2160, width * 9 // 16)) // 2 * 2
        fps = 30 if game else max(1, min(30, int(profile["fps"])))
        self._cfg = cfg
        if not self._selection[0]:
            raise SessionDenied("native video is disabled by server policy")
        return dict(width=width, height=height, fps=fps, codec=int(MediaCodec.H264),
            kind=int(MediaKind.CAMERA if sources == ("camera",) else MediaKind.SCREEN),
            layout="composite" if len(sources) > 1 else "single", outbound=bool(sources))

    async def _begin(self, request: tuple) -> None:
        self._parent_check()
        if self._requested != request:
            return
        if self._call is not None:
            if self._call != request:
                raise SessionDenied("another native video intent is active")
            await self._ready()
            return
        video_id, expiry, parent_id, camera, send_kind, send_layout, game = request
        if self.media.now_ms() >= expiry:
            raise SessionDenied("native video intent expired before approval")
        profile = self._plan()
        if not profile["outbound"] and not camera:
            raise SessionDenied("no native video source was selected")
        consent = dict(call_id=video_id, local_target_id=self.parent.context.local_endpoint_id,
            remote_target_id=self.parent.context.remote_endpoint_id, expires_at_ms=expiry,
            send_kinds=[profile["kind"]] if profile["outbound"] else [],
            receive_kinds=[send_kind] if camera else [], codecs=[int(MediaCodec.H264)],
            maximum_width=max(1920, profile["width"]), maximum_height=max(1080, profile["height"]),
            maximum_fps=30, maximum_audio_channels=1)
        self.media.negotiation.approve_call(consent, adapter_factory=self._factory)
        self._call, self._profile = request, profile
        self._screen_input_clock_ms = self.media.now_ms()
        self._screen_input_sequence = 0
        await self._ready()
        self._parent_check()
        if self._call != request or self._requested != request:
            return
        if profile["outbound"]:
            self._source_generation += 1
            source = MediaSourceBinding(session=self.media.binding, lease_id=str(uuid.uuid4()),
                call_id=video_id, participant_id=self.parent.context.local_endpoint_id,
                target_id=self.parent.context.local_endpoint_id, source_id=SERVER_VIDEO_SOURCE_ID,
                media_generation=self._source_generation,
                expires_at_ms=expiry, direction="send", kind=MediaKind(profile["kind"]), codec=MediaCodec.H264,
                width=profile["width"], height=profile["height"], fps=profile["fps"], layout=profile["layout"])
            self._outgoing = source
            self.media.negotiation.offer_source(source)

    async def _ready(self) -> None:
        video_id, expiry, parent_id, camera, send_kind, send_layout, game = self._call
        await self.parent._send(dict(event="native_video_ready", call_id=video_id, expires_at_ms=expiry,
            parent_call_id=parent_id, camera_active=camera, send_kind=send_kind, send_layout=send_layout,
            profile=self._profile, screen_input_clock_ms=self._screen_input_clock_ms,
            **({"game_mode": True} if game else {})))

    async def receive_screen_input(self, payload: dict, *, transport_deadline_us=None) -> None:
        """Harmless existing choices require drawn-source and Interactive consent.

        This path owns no physical input slot and cannot issue OS input. Each
        admitted event is serialized, fresh and checked again inside its owned
        business job; source/parent cleanup joins that job.
        """
        from core_server.iroh_audio_calls import audio_call_intent
        self.media.channel.registry.check(self.media.binding, scope="control")
        source = self.screen_audio_source()
        proof = payload.get("native_input")
        if source is None or source.layout != "single" or self.parent._call_screen_mode != "interactive" or \
                not isinstance(proof, dict) or set(proof) != {"version", "source", "sequence", "elapsed_ms"} or \
                type(proof.get("version")) is not int or proof["version"] != 1 or \
                not matches_input_source(proof["source"], source):
            raise SessionDenied("native screen input requires the current Interactive screen")
        for field in ("sequence", "elapsed_ms"):
            if type(proof[field]) is not int or not 0 <= proof[field] < 2**64:
                raise SessionDenied("invalid native screen input counter or clock")
        if not self._screen_input_sequence < proof["sequence"] or self._screen_input_jobs:
            raise SessionDenied("native screen input is stale or busy")
        parent_id, expiry = audio_call_intent({**payload, "active": False}, now_ms=-1, expires_at_ms=2**63-1)
        if (parent_id, expiry) != (self.parent._call_id, self.parent._call_expiry):
            raise SessionDenied("native screen input changed its parent consent")
        deadline_ms = self._screen_input_clock_ms + proof["elapsed_ms"] + 200
        now_us = time.monotonic_ns() // 1000
        if not self.media.now_ms() < deadline_ms <= self.media.now_ms() + 400 or \
                transport_deadline_us is not None and (type(transport_deadline_us) is not int or transport_deadline_us <= now_us):
            raise SessionDenied("native screen input delivery expired")
        normalized = normalize_native_screen_input(payload)
        self._screen_input_sequence = proof["sequence"]
        async def dispatch():
            self._source_check(source)
            if self.screen_audio_source() != source or self.parent._call_screen_mode != "interactive" or \
                    self.media.now_ms() >= deadline_ms or \
                    transport_deadline_us is not None and time.monotonic_ns() // 1000 >= transport_deadline_us:
                raise SessionDenied("native screen input lost its consent before dispatch")
            message = DataChannelMessage(MessageHeader(str(uuid.uuid4()), MessageType.VOICE_CALL_CONTROL,
                time.time(), self.parent.session_id, "AutoYou"), normalized)
            await self.parent.engine._handle_voice_call_control_message(message, trusted_session_id=self.parent.session_id)
        job = asyncio.create_task(dispatch(), name="iroh-screen-choice")
        self._screen_input_jobs.add(job)
        try:
            await _join_owned(job)
        finally:
            self._screen_input_jobs.discard(job)

    async def _run(self) -> None:
        try:
            while not self._closing:
                action, value = await self._controls.get()
                try:
                    if action == "start":
                        try:
                            await self._begin(value)
                        except SessionDenied:
                            if self._requested == value:
                                self.fence()
                            await self._end(value[0])
                            if not self._closing and self.parent._call_id == value[2]:
                                await self.parent._send(dict(event="native_video_denied", call_id=value[0],
                                    parent_call_id=value[2], detail="Video is unavailable for this intent."))
                    elif action == "policy_end":
                        await self._end(value[0])
                        if not self._closing and self.parent._call_id == value[2]:
                            await self.parent._send(dict(event="native_video_denied", call_id=value[0], parent_call_id=value[2],
                                detail="Video source settings changed. Start video again."))
                    else:
                        await self._end(value)
                finally:
                    self._controls.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not (isinstance(error, SessionDenied) and
                    (self.parent._closing or getattr(self.media, "_closing", False))):
                if self.parent._cleanup_error is None:
                    self.parent._cleanup_error = error
                self.parent.fence()
                self.media.negotiation._request_shutdown()
        finally:
            while not self._controls.empty():
                self._controls.get_nowait()
                self._controls.task_done()

    async def _adapter(self, source: MediaSourceBinding) -> NativeMediaAdapter:
        self._source_check(source)
        if source.direction == "receive":
            if not self._call[3] or source.source_id != UPSTREAM_VIDEO_SOURCE_ID or source.kind != MediaKind(self._call[4]) or \
                    source.codec != MediaCodec.H264 or source.layout != self._call[5] or \
                    not 0 < source.width <= 1920 or not 0 < source.height <= 1080 or not 0 < source.fps <= 30:
                raise SessionDenied("native video source is outside its directional profile")
            registry = self.runtime.VIDEO_FRAME_REGISTRY
            if registry is None:
                raise SessionDenied("native camera preview registry is unavailable")
            self._camera = source
            def cleared():
                if self._camera is source:
                    self._camera = None
                    registry.clear_session(self.parent.session_id)
            receiver = NativeVideoReceiver(binding=source, registry=registry,
                check_current=lambda: self._source_check(source), on_close=cleared)
            return NativeMediaAdapter(close_owner=receiver.close, render=receiver.render)
        if source != self._outgoing:
            raise SessionDenied("native video capture is outside its configured source")
        owner = NativeVideoCapture(factory=self.runtime._create_configured_outbound_video_track,
            cfg=self._cfg, check_current=lambda: self._source_check(source), fps=source.fps)
        self._groups.add(owner)
        async def close():
            self._clear_screen_audio(source)
            owner.fence()
            if self._desktop_control is not None:
                lease = self._desktop_control._lease
                if lease is not None and lease.authority.source == source:
                    self._desktop_control.fence()
            if self._tutor is not None:
                self._tutor.fence()
                await self._tutor.join()
            await owner.close()
            self._groups.discard(owner)
        try:
            await owner.ready()
            for descriptor in owner.owned:
                bind_clock = getattr(descriptor, "set_native_audio_clock", None)
                if bind_clock is not None:
                    bind_clock(self.parent.native_video_file_clock)
        except BaseException:
            await close()
            raise
        if source.kind == MediaKind.SCREEN and "remote_desktop" in self.runtime._get_video_outbound_sources(cfg=self._cfg):
            self._screen_audio = (source, owner)
            self.parent.reconcile_audio_policy()
        async def capture():
            captured = await owner.capture()
            if self._tutor is not None:
                self._tutor.observe(source, captured)
            return captured
        return NativeMediaAdapter(close_owner=close, capture=capture)

    def fence(self) -> None:
        if self._tutor is not None:
            self._tutor.fence()
        self._clear_screen_audio()
        if self._desktop_control is not None:
            self._desktop_control.fence()
        self._retiring.update(value[0] for value in (self._call, self._requested) if value is not None)
        self._call = self._requested = None
        self._outgoing = None
        for owner in tuple(self._groups):
            owner.fence()
        if self._camera is not None:
            registry = self.runtime.VIDEO_FRAME_REGISTRY
            if registry is not None:
                registry.clear_session(self.parent.session_id)

    async def _end(self, call_id: str) -> None:
        if self._tutor is not None:
            await self._tutor.join()
        for pending in tuple(self._screen_input_jobs):
            try:
                await _join_owned(pending)
            except SessionDenied:
                pass  # A revoked choice never reached the business handler.
        if self._desktop_control is not None:
            lease = self._desktop_control._lease
            if lease is not None and lease.authority.source.call_id == call_id:
                await self._desktop_control.end()
        if getattr(self.media, "_closing", False) or getattr(self.media.negotiation, "_closing", False):
            return  # The enclosing media owner joins every source during shutdown.
        job = self._ends.get(call_id)
        if job is None:
            job = asyncio.create_task(self.media.negotiation.end_call(call_id), name="iroh-video-end")
            self._ends[call_id] = job
        await _join_owned(job)
        if self._ends.get(call_id) is job:
            self._ends.pop(call_id, None)
        self._retiring.discard(call_id)

    async def end_all(self) -> None:
        self.fence()
        if self._desktop_control is not None:
            await self._desktop_control.end()
        for call_id in tuple(self._retiring):
            await self._end(call_id)
        await self._controls.join()

    async def join_controls(self) -> None:
        self.fence()
        self._closing = True
        self._worker.cancel()
        await asyncio.gather(self._worker, return_exceptions=True)
        while not self._controls.empty():
            self._controls.get_nowait()
            self._controls.task_done()

    def peer_end(self, call_id: str) -> bool:
        expected = self._call or self._requested
        if expected is None or expected[0] != call_id:
            return False
        self.fence()
        try:
            self._controls.put_nowait(("end", call_id))
        except asyncio.QueueFull:
            self.media.negotiation._request_shutdown()
        return True

    async def close(self) -> None:
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._close_owned(), name="iroh-video-session-close")
        await _join_owned(self._cleanup)

    async def _close_owned(self) -> None:
        errors = []
        if self._tutor is not None:
            self._tutor.fence()
            try: await self._tutor.join()
            except BaseException as error: errors.append(error)
        if self._desktop_control is not None:
            try: await self._desktop_control.close()
            except BaseException as error: errors.append(error)
        await self.join_controls()
        try:
            await self.end_all()
        except BaseException as error:
            errors.append(error)
        self._closing = True
        self._worker.cancel()
        await asyncio.gather(self._worker, return_exceptions=True)
        for owner in tuple(self._groups):
            try:
                await owner.close()
            except BaseException as error:
                errors.append(error)
            else:
                self._groups.discard(owner)
        if errors:
            raise RuntimeError("native video cleanup failed") from errors[0]
