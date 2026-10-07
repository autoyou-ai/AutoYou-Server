# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Carry native call intent through the existing server audio business owner.

The paired grant alone never authorizes capture. A current client call intent
and server audio policy approve the shared Rust consent; source activation then
waits for the receiver's acknowledgement. Speech, recording, mute, playback and
WUIFT continue through WebRTCEngine's application handlers without an RTC peer.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
import logging
from pathlib import Path
import time
import unicodedata
from typing import Any
import uuid

from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType
from shared.iroh_media import _join_owned
from shared.iroh_media_audio import IrohAudioTrackCapture, IrohSpeechReceiver
from shared.iroh_media_codec import CapturedMedia
from shared.iroh_audio_policy import NativeAudioPolicy, audio_mode, native_audio_scope
from shared.iroh_media_negotiation import NativeMediaAdapter
from shared.session_media import MediaCodec, MediaKind, MediaSourceBinding
from shared.session_transport import SessionDenied


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class NativeVoiceTurn:
    owner: Any
    binding: Any
    manager: Any
    call_id: str
    expires_at_ms: int
    speech_generation: int
    output_stop_revision: int

    def same_session(self) -> bool:
        owner = self.owner
        try:
            owner._check()
        except SessionDenied:
            return False
        return bool(owner.media.binding == self.binding and owner.manager is self.manager and
            owner.runtime.STATE.audio_managers.get(owner.session_id) is self.manager and
            not getattr(self.manager,"_closed",False))

    def current(self) -> bool:
        return bool(self.same_session() and self.owner._call_id == self.call_id and
            self.owner._call_expiry == self.expires_at_ms and self.owner._speech_allowed() and
            self.manager._stt_generation == self.speech_generation)

    def output_current(self) -> bool:
        return bool(self.current() and self.manager.get_tts_stop_revision() == self.output_stop_revision)

    def metadata(self):
        return {"native_voice_call":dict(version=1,call_id=self.call_id,expires_at_ms=self.expires_at_ms)}

    def playback_scope(self):
        return native_audio_scope(dict(version=1,call_id=self.call_id,expires_at_ms=self.expires_at_ms,
            session_generation=self.binding.generation,authorization_epoch=self.binding.authorization_epoch,
            audio_instance_id=self.manager._native_audio_instance_id,speech_generation=self.speech_generation))

    def speak(self, text, *, context="") -> bool:
        if not self.output_current():
            return False
        return bool(self.manager.speak(text,context=context,expected_stop_revision=self.output_stop_revision))


def audio_call_intent(payload: Any, *, now_ms: int, expires_at_ms: int) -> tuple[str, int]:
    """The source authority is local policy, never a target supplied in this record."""
    native = payload.get("native_call") if isinstance(payload, dict) else None
    if not isinstance(native, dict) or set(native) != {"version", "call_id", "expires_at_ms"} or \
            type(native["version"]) is not int or native["version"] != 1 or \
            type(payload.get("active")) is not bool:
        raise SessionDenied("native call requires a versioned explicit intent")
    call_id, expiry = native["call_id"], native["expires_at_ms"]
    if not isinstance(call_id, str) or not call_id or len(call_id.encode("utf-8")) > 128 or \
            any(unicodedata.category(char) == "Cc" for char in call_id) or \
            type(expiry) is not int or not now_ms < expiry <= expires_at_ms:
        raise SessionDenied("native call intent is expired or outside its grant")
    return call_id, expiry


class IrohServerAudioCalls:
    def __init__(self, *, media: Any, context: Any, engine: Any, runtime: Any) -> None:
        self.media, self.channel = media, media.channel
        self.context, self.engine, self.runtime = context, engine, runtime
        self.session_id = media.binding.transport_id
        self.manager = None
        self._call_id: str | None = None
        self._call_expiry = 0
        self._call_mode = "call"
        self._call_microphone = True
        self._requested_microphone = None
        self._call_screen_mode = ""
        self._requested_screen_mode = None
        self._source_generation = 0
        self._intent_generation = 0
        self._requested_call: tuple[str, int] | None = None
        self._controls = asyncio.Queue(maxsize=64)
        self._worker: asyncio.Task | None = None
        self._creating: asyncio.Task | None = None
        self._business_jobs: set[asyncio.Task] = set()
        self._closing = False
        self._cleanup: asyncio.Task | None = None
        self._cleanup_error: BaseException | None = None
        self._failed_captures: list[list[Any]] = []
        self._factory = self._adapter  # Stable identity for repeated approval.
        self._audio_policy: NativeAudioPolicy | None = None
        self._policy_signature = None
        self._policy_revision = 0
        self._policy_pending = None
        self._policy_wake = asyncio.Event()
        self._policy_worker: asyncio.Task | None = None
        self._audio_offer: MediaSourceBinding | None = None
        self._capture_config = None
        self._capture_loopback_source: MediaSourceBinding | None = None
        self._capture_allowed = False
        self._video_file_config: tuple[str, bool] | None = None
        self._client_output_revision = 0
        self._recording = None
        self._requested_mode = None
        self._requested_screen_mode = None
        self._video = None

    def _recording_check(self, call_id):
        self._check()
        if self._call_id != call_id or self._call_mode != "silent_recording" or \
                self.media.now_ms() >= self._call_expiry or self._audio_policy is None or not self._audio_policy.send_microphone:
            raise SessionDenied("native recording consent ended")

    def _recording_failed(self, error):
        self._cleanup_error = error
        self.fence()
        self.media.negotiation._request_shutdown()

    async def _ensure_recording(self):
        if self._recording is not None:
            if self._recording._closed:
                await self._stop_recording()
            else:
                return
        from shared.iroh_recording import NativeRecording
        cfg = self.runtime.STATE.config or {}
        call_id = self._call_id
        cls = self.runtime.StreamingWavBatchRecorder
        if cls is None:
            raise SessionDenied("native silent recording is unavailable")
        if self.engine.silent_recorders.get(self.session_id) is not None:
            raise SessionDenied("native silent recording already has a physical owner")
        params = dict(session_id=self.session_id,output_dir=self.runtime._resolve_silent_recording_dir(cfg=cfg),
            max_batch_seconds=self.runtime._get_silent_recording_batch_seconds(cfg=cfg),native_media=True,
            filename_prefix=f"autoyou-silent-recording-{uuid.uuid4().hex}")
        owner = NativeRecording(factory=lambda:cls(**params),check_current=lambda:self._recording_check(call_id),
            on_failure=self._recording_failed)
        self._recording = owner
        self.engine.silent_recorders[self.session_id] = owner
        await owner.ready()
        self._recording_check(call_id)

    async def _stop_recording(self):
        owner = self._recording
        if owner is None:
            return
        await owner.close()
        for key,current in tuple(self.engine.silent_recorders.items()):
            if current is owner:
                self.engine.silent_recorders.pop(key,None)
        if self._recording is owner:
            self._recording = None

    def _requested_video_file(self, cfg):
        if not self.runtime._get_audio_playback_enabled(cfg=cfg) or \
                "video_file" not in self.runtime._get_video_outbound_sources(cfg=cfg):
            return None
        path = self.runtime._get_video_file_path(cfg=cfg)
        return (path, self.runtime._get_video_file_loop_enabled(cfg=cfg)) if path else None

    def _start_video_file_audio(self, cfg, playback):
        requested = self._requested_video_file(cfg)
        if playback is None or requested == self._video_file_config:
            return
        # This runs only after the factory joins and the current source/policy
        # is checked on the event loop. An obsolete physical factory cannot
        # start a decoder after a newer policy already stopped it.
        self._video_file_config = requested
        if requested is not None:
            path, loop = requested
            try:
                if Path(path).expanduser().is_file():
                    playback.play_audio_file(path, source="video_file", loop=loop)
            except Exception as exc:
                LOGGER.warning("Failed to start native video-file audio: %s", exc)

    def native_video_file_clock(self, file_path: str):
        track = getattr(self.manager, "playback_track", None)
        clock = getattr(track, "native_file_playback_clock", None)
        return clock(file_path) if clock is not None else None

    def _screen_audio_source(self) -> MediaSourceBinding | None:
        return self._video.screen_audio_source() if self._video is not None else None

    def _plan_audio_policy(self, *, force=False):
        cfg = deepcopy(self.runtime.STATE.config or {})
        audio = self.runtime._get_video_call_audio_enabled(cfg=cfg)
        agents = self.runtime._get_video_call_agent_processing_enabled(cfg=cfg)
        tts = bool(agents and self.runtime._get_ai_audio_replies_enabled(cfg=cfg))
        playback = self.runtime._get_audio_playback_enabled(cfg=cfg)
        background = self._call_mode != "call"
        background_state = self.engine._background_audio_state_for_session(self.session_id)
        mode_allowed = (bool(background_state.get("active")) and
            bool(background_state.get("silent_recording")) == (self._call_mode == "silent_recording") and
            self.engine._background_audio_mode_allowed(silent_recording=self._call_mode == "silent_recording",cfg=cfg) and
            self.engine._screen_session_for_session(self.session_id) is None if background else True)
        microphone = bool(self._call_microphone and audio and (not background or (mode_allowed and self._call_mode == "silent_recording")))
        capture_allowed = self.engine.server_capture_allowed_for_session(self.session_id)
        loopback_source = self._screen_audio_source()
        include_loopback = loopback_source is not None
        video_sources = tuple(self.runtime._get_video_outbound_sources(cfg=cfg))
        sources = tuple(source for source in self.runtime._get_video_audio_sources(cfg=cfg)
            if source != "speaker_loopback" or (include_loopback and "remote_desktop" in video_sources))
        signature = (audio, tts, playback, sources, capture_allowed, include_loopback,self._call_mode,mode_allowed,self._call_microphone,
            self.runtime._get_video_input_audio_source(cfg=cfg), video_sources,
            self.runtime._get_video_file_path(cfg=cfg), self.runtime._get_video_file_loop_enabled(cfg=cfg), loopback_source)
        if not force and signature == self._policy_signature:
            return None
        self._policy_signature = signature
        self._policy_revision += 1
        outbound = bool(audio and not background and ((self.manager is not None and (tts or playback)) or
            (capture_allowed and sources)))
        if outbound:
            self._source_generation += 1
        policy = NativeAudioPolicy(self._policy_revision, microphone, outbound,
            self._source_generation if outbound else 0)
        # The borrowed software mix must use the same effective permission
        # projection as its declared policy, without mutating live configuration.
        if not isinstance(cfg.get("video_call"), dict):
            cfg["video_call"] = {}
        cfg["video_call"]["ai_audio_replies_enabled"] = tts
        self._audio_policy, self._capture_config, self._capture_allowed = policy, cfg, capture_allowed
        self._capture_loopback_source = loopback_source if audio and not background and capture_allowed and "speaker_loopback" in sources else None
        if self._recording is not None and not microphone:
            self._recording.fence()
        if self.manager is not None:
            if not audio or not tts:
                self.manager.stop_speaking(source="native_audio_policy")
            if not audio or not playback:
                self.manager.stop_playback(source="native_audio_policy")
                self._video_file_config = None
            elif self._video_file_config is not None and self._requested_video_file(cfg) != self._video_file_config:
                if self.manager.get_playback_status().get("source") == "video_file":
                    self.manager.stop_playback(source="native_video_file_changed")
                self._video_file_config = None
        source = (MediaSourceBinding(session=self.media.binding, lease_id=str(uuid.uuid4()),
            call_id=self._call_id, participant_id=self.context.local_endpoint_id,
            target_id=self.context.local_endpoint_id, source_id=2, media_generation=policy.receive_generation,
            expires_at_ms=self._call_expiry, direction="send", kind=MediaKind.SYSTEM_AUDIO,
            codec=MediaCodec.OPUS, sample_rate=48000, channels=1) if outbound else None)
        return self._call_id, policy, source

    def reconcile_audio_policy(self, *, force=False) -> None:
        if self._closing or self._cleanup_error is not None or self._call_id is None:
            return
        pending = self._plan_audio_policy(force=force)
        if pending is None:
            return
        self._policy_pending = pending  # One latest projection while physical cleanup runs.
        self._policy_wake.set()
        if self._policy_worker is None:
            self._policy_worker = asyncio.create_task(self._run_audio_policy(), name="iroh-call-audio-policy")

    def _source_allowed(self, source: MediaSourceBinding) -> bool:
        policy = self._audio_policy
        if policy is None or source.call_id != self._call_id:
            return False
        return (policy.send_microphone if source.direction == "receive" else
            policy.receive_audio and source.media_generation == policy.receive_generation and
            (self._capture_loopback_source is None or self._screen_audio_source() == self._capture_loopback_source))

    async def _run_audio_policy(self) -> None:
        try:
            while not self._closing:
                await self._policy_wake.wait()
                self._policy_wake.clear()
                pending, self._policy_pending = self._policy_pending, None
                if pending is None:
                    continue
                call_id, policy, outgoing = pending
                if call_id != self._call_id or policy != self._audio_policy:
                    continue
                await self._send({"event":"native_call_policy", "call_id":call_id,
                    "expires_at_ms":self._call_expiry, "audio_mode":self._call_mode, "policy":policy.record()})
                if call_id != self._call_id or policy != self._audio_policy:
                    continue
                previous = [source.binding for source in tuple(self.media._sources.values())
                    if source.binding.call_id == call_id and
                    ((source.binding.direction == "send" and source.binding.source_id == 2) or
                     (source.binding.direction == "receive" and source.binding.source_id == 1 and not policy.send_microphone))]
                if self._audio_offer is not None and self._audio_offer.call_id == call_id:
                    previous.append(self._audio_offer)
                for source in dict.fromkeys(previous):
                    await self.media.negotiation.retire_source_and_join(source)
                if self._recording is not None and self._recording._closed:
                    await self._stop_recording()
                if self._audio_offer in previous:
                    self._audio_offer = None
                if call_id != self._call_id or policy != self._audio_policy:
                    continue
                if outgoing is not None:
                    self.media.negotiation.offer_source(outgoing)
                    self._audio_offer = outgoing
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._cleanup_error is None:
                self._cleanup_error = exc
            self.fence()
            self.media.negotiation._request_shutdown()

    def reconcile_speech_policy(self) -> None:
        manager = self.manager
        if manager is None:
            return
        if not manager.set_native_speech_enabled(self._speech_allowed()):
            self._cleanup_error = RuntimeError("native speech owner failed its policy transition")
            self.fence()
            self.media.negotiation._request_shutdown()
            raise SessionDenied("native speech owner cannot accept this policy")

    def _speech_allowed(self) -> bool:
        cfg = self.runtime.STATE.config or {}
        return bool(not self._closing and self._call_id is not None
            and self._call_mode == "call"
            and not self._call_screen_mode
            and self.media.now_ms() < self._call_expiry
            and self.runtime._get_video_call_agent_processing_enabled(cfg=cfg)
            and self.runtime._get_video_call_audio_enabled(cfg=cfg)
            and self.engine._screen_session_for_session(self.session_id) is None
            and not self.engine._background_audio_state_for_session(self.session_id).get("active"))

    def playback_scope(self):
        try:
            self._check()
        except SessionDenied:
            return None
        manager=self.manager
        if self._call_id is None or self._call_mode!="call" or self.media.now_ms()>=self._call_expiry or \
                manager is None or self.runtime.STATE.audio_managers.get(self.session_id) is not manager or \
                getattr(manager,"_closed",False):
            return None
        binding=self.media.binding
        return native_audio_scope(dict(version=1,call_id=self._call_id,expires_at_ms=self._call_expiry,
            session_generation=binding.generation,authorization_epoch=binding.authorization_epoch,
            audio_instance_id=manager._native_audio_instance_id))

    def accepts_playback_scope(self,record):
        try:
            scope=native_audio_scope(record)
        except SessionDenied:
            return False
        speech_generation=scope.pop("speech_generation",None)
        return bool(scope==self.playback_scope() and
            (speech_generation is None or (self._speech_allowed() and self.manager is not None and
                self.manager._stt_generation==speech_generation)))

    def _suspend_speech(self) -> None:
        if self.manager is not None:
            self.manager.set_native_speech_enabled(False)

    def _end_audio(self) -> None:
        if self._video is not None:
            self._video.fence()
        self._suspend_speech()
        for name in ("voice_call_status_by_session","voice_call_playback_by_session"):
            statuses=getattr(self.engine,name,None)
            if isinstance(statuses,dict):
                statuses.pop(self.session_id,None)
        if self._recording is not None:
            self._recording.fence()
        self._video_file_config = None
        if self.manager is not None:
            self.manager.stop_speaking(source="native_call_ended")
            self.manager.stop_playback(source="native_call_ended")

    def _on_text(self, text: str, loop: asyncio.AbstractEventLoop) -> None:
        # The manager's generation fence rejects obsolete model callbacks. This
        # additional local intent check also protects the application boundary.
        if not self._speech_allowed():
            return
        manager = self.manager
        if manager is None:
            return
        turn = NativeVoiceTurn(self,self.media.binding,manager,self._call_id,self._call_expiry,
            manager._stt_generation,manager.get_tts_stop_revision())
        if turn.current():
            self.engine._enqueue_voice_command_threadsafe(self.session_id,text,loop,native_turn=turn)

    def _check(self) -> None:
        if self._closing or self._cleanup_error is not None:
            raise SessionDenied("native call owner is closed or cleanup failed")
        self.media._check()
        if self.engine.datachannel_managers.get(self.session_id) is not self.channel:
            raise SessionDenied("native call owner was replaced")

    def _source_check(self, source: MediaSourceBinding) -> None:
        self._check()
        if self._call_id != source.call_id or self.media.now_ms() >= self._call_expiry:
            raise SessionDenied("native call audio consent ended")
        self.media.negotiation._current(source)

    def _begin_intent(self, payload: Any) -> tuple[str, int]:
        # Peers can hold grants with different expiries. The requested expiry
        # is a ceiling; the acknowledged call lease also obeys this local grant.
        call_id, expiry = audio_call_intent(payload, now_ms=self.media.now_ms(), expires_at_ms=2**63-1)
        return call_id, min(expiry, self.media.binding.expires_at_ms)

    async def ready(self) -> None:
        self._check()
        cfg = self.runtime.STATE.config or {}
        audio_enabled = self.runtime._get_video_call_audio_enabled(cfg=cfg)
        agent_processing = self.runtime._get_video_call_agent_processing_enabled(cfg=cfg)
        if audio_enabled and self.runtime.AudioManager is not None:
            if self.session_id in self.runtime.STATE.audio_managers:
                raise SessionDenied("native call audio manager already has an owner")
            loop = asyncio.get_running_loop()
            status = self.engine._make_voice_call_status_callback(self.session_id, loop)
            provider = self.engine._voice_training_conversation_provider(self.session_id)
            def create():
                return self.runtime.AudioManager(
                    lambda text: self._on_text(text, loop),
                    settings_provider=self.runtime._speech_config, status_callback=status,
                    enable_stt=False, native_media=True)
            self._creating = asyncio.create_task(asyncio.to_thread(create), name="iroh-call-audio-create")
            try:
                try:
                    self.manager = await _join_owned(self._creating)
                except asyncio.CancelledError:
                    if not self._creating.cancelled():
                        error = self._creating.exception()
                        if error is None:
                            self.manager = self._creating.result()
                        else:
                            self.manager = getattr(error, "owned_audio_manager", None)
                    raise
                except BaseException as exc:
                    self.manager = getattr(exc, "owned_audio_manager", None)
                    raise
                self._check()
                self.manager.conversation_context_provider = provider
                self.runtime.STATE.audio_managers[self.session_id] = self.manager
                self.runtime._ensure_audio_manager_outbound_tracks(audio_manager=self.manager, cfg=cfg)
                if self.engine.wuift_hold_by_session.get(self.session_id) and self.runtime._get_wuift_enabled():
                    self.manager.set_segmentation_hold(True, source=f"reconnect:{self.session_id}")
            finally:
                self._creating = None
        if not audio_enabled or not agent_processing:
            # Keep the existing distinction between an unavailable call and an
            # available call whose AI speech processing was disabled by policy.
            job = asyncio.create_task(self.engine._publish_voice_call_status(self.session_id,
                {"event": "readiness", "state": "ready" if audio_enabled else "unavailable",
                 "detail": "Call audio ready; AI speech processing is disabled." if audio_enabled else "Call audio is disabled by server policy."}))
            self._business_jobs.add(job)
            try:
                await _join_owned(job)
            finally:
                self._business_jobs.discard(job)
        if self._worker is None:
            self._worker = asyncio.create_task(self._run_controls(), name="iroh-call-controls")

    async def receive(self, message: Any) -> None:
        self._check()
        if not isinstance(message.payload, dict):
            raise SessionDenied("invalid native call control")
        ending_call_id = None
        event = str(message.payload.get("event") or "").strip().lower()
        if event in {"screen_input", "screen_tutor_action"}:
            raise SessionDenied("native screen input requires its source-bound input lane")
        if event in {"screen_tutor_state", "screen_tutor_request"}:
            if self._video is None:
                raise SessionDenied("tutoring requires active native screen video")
            if self._video._tutor is None:
                from core_server.iroh_screen_tutor import IrohScreenTutor
                self._video._tutor = IrohScreenTutor(video=self._video)
            if event == "screen_tutor_state":
                await self._video._tutor.state(message.payload)
            else:
                self._video._tutor.request(message.payload)
            return
        if event in {"remote_desktop_control", "remote_desktop_input", "remote_desktop_keyboard", "game_input"}:
            if self._video is None:
                raise SessionDenied("native desktop control requires active native video")
            if self._video._desktop_control is None:
                from core_server.iroh_desktop_control import IrohServerDesktopControl
                self._video._desktop_control = IrohServerDesktopControl(video=self._video)
            await self._video._desktop_control.receive(message.payload)
            return
        if event == "video_state":
            if self._video is None:
                from core_server.iroh_video_calls import IrohServerVideoCalls
                self._video = IrohServerVideoCalls(parent=self)
            await self._video.receive(message.payload)
            return
        if event == "background_audio_state":
            raise SessionDenied("native background state requires a scoped audio-mode intent")
        if event == "call_state" and type(message.payload.get("active")) is not bool:
            raise SessionDenied("native call state requires a boolean intent")
        if event == "call_state":
            audio_mode(message.payload)
            from shared.iroh_audio_policy import native_screen_mode
            screen_mode = native_screen_mode(message.payload)
        if event == "native_audio_refresh":
            if set(message.payload) != {"event", "native_call", "revision"} or \
                    type(message.payload.get("revision")) is not int or not 1 <= message.payload["revision"] <= 2**63-1:
                raise SessionDenied("native audio refresh requires a bounded revision")
        if event in {"stop_tts", "wuift_state", "wuift_trigger", "native_audio_refresh"} or ("muted" in message.payload and event != "call_state"):
            if self._call_id is None:
                return
            if event != "call_state" and self._call_mode != "call":
                return
            call_id, expiry = audio_call_intent({**message.payload, "active": False}, now_ms=-1,
                expires_at_ms=2**63-1)
            if call_id != self._call_id or self.media.now_ms() >= self._call_expiry:
                return
            if expiry != self._call_expiry:
                raise SessionDenied("native call control does not match its approved expiry")
        if event == "call_state" and message.payload.get("active") is True:
            request = self._begin_intent(message.payload)
            microphone = message.payload.get("audio_microphone", True)
            if type(microphone) is not bool:
                raise SessionDenied("native microphone consent requires an explicit boolean")
            if self._requested_call is not None and self._requested_microphone != microphone:
                raise SessionDenied("a native audio intent cannot change microphone consent")
            if self._requested_call is not None and self._requested_call != request:
                raise SessionDenied("another native call intent is pending")
            mode = audio_mode(message.payload)
            if self._requested_mode is not None and self._requested_mode != mode:
                raise SessionDenied("a pending native audio intent cannot change its mode")
            if self._requested_screen_mode is not None and self._requested_screen_mode != screen_mode:
                raise SessionDenied("a native call intent cannot change screen consent")
            self._requested_call = request
            self._requested_mode = mode
            self._requested_microphone = microphone
            self._requested_screen_mode = screen_mode
        if event == "call_state" and message.payload.get("active") is False:
            # Fence even while an earlier device acquisition/cleanup is stalled.
            expected = self._call_id or (self._requested_call[0] if self._requested_call is not None else None)
            # Ending only removes authority. It must retain its identity even
            # after expiry or before a shorter local grant was acknowledged.
            call_id, _ = audio_call_intent(message.payload, now_ms=-1, expires_at_ms=2**63-1)
            if call_id != expected:
                return  # A late end cannot stop a newer explicit call.
            expected_mode = self._call_mode if self._call_id is not None else self._requested_mode
            if audio_mode(message.payload) != expected_mode:
                raise SessionDenied("native audio end changed its consent mode")
            expected_screen = self._call_screen_mode if self._call_id is not None else self._requested_screen_mode
            if screen_mode != expected_screen:
                raise SessionDenied("native audio end changed its screen consent")
            ending_call_id = expected
            self._call_id = None
            self._requested_call = None
            self._requested_mode = None
            self._requested_screen_mode = None
            self._intent_generation += 1
            self._end_audio()
        try:
            self._controls.put_nowait((message, self._intent_generation, ending_call_id))
        except asyncio.QueueFull:
            self.fence()
            self.media.negotiation._request_shutdown()
            raise SessionDenied("native call control capacity is exhausted") from None

    def peer_end(self, call_id: str) -> None:
        if self._video is not None and self._video.peer_end(call_id):
            return
        expected = self._call_id or (self._requested_call[0] if self._requested_call is not None else None)
        if self._closing or expected != call_id:
            return
        mode = self._call_mode if self._call_id is not None else self._requested_mode
        screen = self._call_screen_mode if self._call_id is not None else self._requested_screen_mode
        self._call_id = None
        self._requested_call = None
        self._requested_mode = None
        self._requested_screen_mode = None
        self._intent_generation += 1
        self._end_audio()
        message = DataChannelMessage(MessageHeader(str(uuid.uuid4()), MessageType.VOICE_CALL_CONTROL,
            time.time(), self.session_id, "AutoYou"), {"event": "call_state", "active": False,"audio_mode":mode,
                "screen_mode": screen})
        try:
            self._controls.put_nowait((message, self._intent_generation, None))
        except asyncio.QueueFull:
            self.fence()
            self.media.negotiation._request_shutdown()

    async def _send(self, payload: dict[str, Any]) -> None:
        self._check()
        if payload.get("event") in {"native_call_ready", "native_call_policy"}:
            payload = {**payload, "screen_mode": self._call_screen_mode}
        message = DataChannelMessage(MessageHeader(str(uuid.uuid4()), MessageType.VOICE_CALL_CONTROL,
            time.time(), self.session_id, "AutoYou"), payload)
        if not await self.channel.send_message(message):
            raise ConnectionError("native call status could not be queued")

    async def _business_control(self, message: Any) -> None:
        if str(message.payload.get("event") or "").strip().lower() == "call_state":
            mode = audio_mode(message.payload)
            if mode != "call":
                active = message.payload["active"]
                message = DataChannelMessage(message.header,dict(event="background_audio_state",active=active,
                    silent_recording=active and mode == "silent_recording",muted=not(active and mode == "silent_recording"),
                    platform=message.payload.get("platform","python"),timestamp_ms=message.payload.get("timestamp_ms")))
        # Existing handlers can await physical transcription, synthesis or OS
        # work. Shield the entire handler so its inner executor awaits finish
        # before this call owner releases the model/device it references.
        job = asyncio.create_task(self.engine._handle_voice_call_control_message(message,
            trusted_session_id=self.session_id), name="iroh-call-business-control")
        self._business_jobs.add(job)
        try:
            await _join_owned(job)
        finally:
            self._business_jobs.discard(job)

    async def _begin_call(self, message: Any) -> None:
        call_id, expiry = self._begin_intent(message.payload)
        mode = audio_mode(message.payload)
        from shared.iroh_audio_policy import native_screen_mode
        screen_mode = native_screen_mode(message.payload)
        microphone = message.payload.get("audio_microphone", True)
        cfg = self.runtime.STATE.config or {}
        if not self.runtime._get_video_call_audio_enabled(cfg=cfg):
            raise SessionDenied("server policy disables call audio")
        if mode != "call" and (not self.engine._background_audio_mode_allowed(
                silent_recording=mode == "silent_recording",cfg=cfg) or
                self.engine._screen_session_for_session(self.session_id) is not None):
            raise SessionDenied("server policy disables this native background mode")
        if mode == "silent_recording" and self.runtime.StreamingWavBatchRecorder is None:
            raise SessionDenied("native silent recording is unavailable")
        if self.manager is None and self.runtime.AudioManager is not None and mode == "call":
            await self.ready()
            self._check()
            if self._requested_call != (call_id, expiry):
                return
            cfg = self.runtime.STATE.config or {}
            if not self.runtime._get_video_call_audio_enabled(cfg=cfg):
                raise SessionDenied("server policy disables call audio")
        if self._call_id is not None:
            if self._call_id != call_id or self._call_expiry != expiry or self._call_mode != mode or self._call_microphone != microphone or self._call_screen_mode != screen_mode:
                raise SessionDenied("another native call intent is active")
            await self._business_control(message)
            self._check()
            if self._requested_call != (call_id, expiry):
                return
            self.reconcile_speech_policy()
            self.reconcile_audio_policy()
            await self._send({"event": "native_call_ready", "call_id": call_id, "expires_at_ms": expiry,
                "audio_mode":mode,"policy": self._audio_policy.record()})
            return
        consent = dict(call_id=call_id, local_target_id=self.context.local_endpoint_id,
            remote_target_id=self.context.remote_endpoint_id, expires_at_ms=expiry,
            send_kinds=[int(MediaKind.SYSTEM_AUDIO)] if mode == "call" else [],
            receive_kinds=[int(MediaKind.MICROPHONE)] if microphone and mode != "background_keepalive" else [],
            codecs=[int(MediaCodec.OPUS)], maximum_width=0, maximum_height=0,
            maximum_fps=0, maximum_audio_channels=1)
        # Set the existing call/background/host-priority state before granting
        # source authority. Its RTC-only restoration is a no-op for this session.
        await self._business_control(message)
        self._check()
        if self._requested_call != (call_id, expiry):
            return  # End was received while the business handler was suspended.
        self.media.negotiation.approve_call(consent, adapter_factory=self._factory)
        self._call_id, self._call_expiry = call_id, expiry
        self._call_mode = mode
        self._call_microphone = microphone
        self._call_screen_mode = screen_mode
        self._client_output_revision = 0
        self.reconcile_speech_policy()
        self._policy_signature = None
        _, policy, source = self._plan_audio_policy()
        await self._send({"event": "native_call_ready", "call_id": call_id, "expires_at_ms": expiry,
            "audio_mode":mode,"policy": policy.record()})
        if source is not None:
            self.media.negotiation.offer_source(source)
            self._audio_offer = source

    async def _run_controls(self) -> None:
        try:
            while not self._closing:
                message, intent_generation, ending_call_id = await self._controls.get()
                event = str(message.payload.get("event") or "").strip().lower()
                try:
                    self._check()
                    if event == "call_state" and message.payload.get("active") is True:
                        if intent_generation == self._intent_generation:
                            await self._begin_call(message)
                    else:
                        if event != "call_state" and intent_generation != self._intent_generation:
                            continue  # Controls queued for an ended call cannot act on its replacement.
                        if event == "native_audio_refresh":
                            revision = message.payload["revision"]
                            if revision > self._client_output_revision:
                                self._client_output_revision = revision
                                self.reconcile_audio_policy(force=True)
                            continue
                        # Original mute/TTS/playback/STT/WUIFT/background/screen
                        # rules remain authoritative and use the trusted ID.
                        await self._business_control(message)
                        self.reconcile_speech_policy()
                        self.reconcile_audio_policy()
                        if ending_call_id is not None:
                            await self.media.negotiation.end_call(ending_call_id)
                        if event == "call_state" and message.payload.get("active") is False:
                            if self._video is not None:
                                await self._video.end_all()
                            await self._stop_recording()
                except SessionDenied:
                    if event == "call_state" and message.payload.get("active") is True and \
                            intent_generation == self._intent_generation and self._call_id is None:
                        self._requested_call = None
                        self._requested_mode = None
                        self._requested_screen_mode = None
                        self._intent_generation += 1
                        self._suspend_speech()
                    native = message.payload.get("native_call", {})
                    denial = {"event": "native_call_denied", "detail": "Call audio is unavailable for this intent."}
                    if isinstance(native, dict) and isinstance(native.get("call_id"), str) and \
                            0 < len(native["call_id"].encode("utf-8")) <= 128 and \
                            not any(unicodedata.category(char) == "Cc" for char in native["call_id"]):
                        denial["call_id"] = native["call_id"]
                    await self._send(denial)
                finally:
                    self._controls.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._cleanup_error = exc
            self.fence()
            self.media.negotiation._request_shutdown()

    async def _adapter(self, source: MediaSourceBinding) -> NativeMediaAdapter:
        self._source_check(source)
        if not self._source_allowed(source):
            raise SessionDenied("native audio source is paused or replaced by policy")
        expected = (1, MediaKind.MICROPHONE) if source.direction == "receive" else (2, MediaKind.SYSTEM_AUDIO)
        if (source.source_id, source.kind) != expected or source.codec != MediaCodec.OPUS or \
                source.channels != 1 or source.sample_rate != 48000:
            raise SessionDenied("native audio source is outside its directional profile")
        if source.direction == "receive":
            if source.kind != MediaKind.MICROPHONE:
                raise SessionDenied("native speech source is not a microphone")
            if self._call_mode == "silent_recording":
                await self._ensure_recording()
                self._source_check(source)
            receiver = IrohSpeechReceiver(check_current=lambda: self._source_check(source), channels=source.channels,
                on_chunk=lambda chunk: self.engine._handle_inbound_voice_audio_chunk(self.session_id, self.manager, chunk)
                    if self._source_allowed(source) else None)
            return NativeMediaAdapter(close_owner=receiver.close, render=receiver.render)
        cfg = self._capture_config
        loopback_source = self._capture_loopback_source
        tts, playback = self.runtime._ensure_audio_manager_outbound_tracks(audio_manager=self.manager, cfg=cfg)
        if getattr(self.runtime.STATE,"local_audio_tracks",{}).get(self.session_id):
            raise SessionDenied("native outbound capture already has an owner")
        owned: list[Any] = []
        cleanup: asyncio.Task | None = None
        async def close_capture_owned():
            def stop():
                errors = []
                for local in owned:
                    try:
                        local.stop()
                        thread = getattr(local, "_thread", None)
                        if thread is not None and thread.is_alive():
                            raise RuntimeError("native audio capture cleanup did not finish")
                        process = getattr(local, "_native_process", None)
                        if process is not None and process.poll() is None:
                            raise RuntimeError("native audio capture process cleanup did not finish")
                    except Exception as exc:
                        errors.append(exc)
                if errors:
                    raise RuntimeError("native audio capture cleanup failed") from errors[0]
            try:
                await _join_owned(asyncio.create_task(asyncio.to_thread(stop)))
            except BaseException as exc:
                self._cleanup_error = exc
                self._failed_captures.append(owned)
                raise
            tracks = getattr(self.runtime.STATE, "local_audio_tracks", {})
            current = tracks.get(self.session_id)
            if current is owned:
                tracks.pop(self.session_id, None)
        async def close_owner():
            nonlocal cleanup
            if cleanup is None:
                cleanup = asyncio.create_task(close_capture_owned(), name="iroh-server-capture-close")
            await _join_owned(cleanup)
        try:
            creating = asyncio.create_task(asyncio.to_thread(self.runtime._create_configured_outbound_audio_track,
                cfg=cfg, session_id=self.session_id, tts_track=tts, playback_track=playback, capture_owner=owned,
                capture_allowed=self._capture_allowed,
                start_video_file_audio=False,
                include_loopback=loopback_source is not None))
            track = await _join_owned(creating)
            self._source_check(source)
            if not self._source_allowed(source):
                raise SessionDenied("native audio policy changed during physical acquisition")
            if track is None:
                raise SessionDenied("no outbound audio source is enabled")
            self._start_video_file_audio(cfg, playback)
        except BaseException:
            await close_owner()
            raise
        capture = IrohAudioTrackCapture(track=track, close_owner=close_owner,
            check_current=lambda: self._source_check(source))
        async def capture_current():
            while not self._source_allowed(source):
                self._source_check(source)
                await asyncio.sleep(0.005)
            started_at = time.monotonic_ns() // 1000
            receipt = await capture.capture()
            if not isinstance(receipt, CapturedMedia):
                receipt = CapturedMedia(receipt, started_at)
            original_check = receipt.current_check
            return replace(receipt, current_check=lambda: self._source_allowed(source) and
                (original_check is None or original_check()))
        return NativeMediaAdapter(close_owner=capture.close, capture=capture_current)

    def fence(self) -> None:
        self._closing = True
        if self._video is not None:
            self._video.fence()
        self._call_id = None
        self._suspend_speech()
        if self._recording is not None:
            self._recording.fence()
        if self._worker is not None and not self._worker.done() and not self._worker.cancelling():
            self._worker.cancel()
        if self._policy_worker is not None and not self._policy_worker.done() and not self._policy_worker.cancelling():
            self._policy_worker.cancel()

    async def join_controls(self) -> None:
        self.fence()
        if self._video is not None:
            await self._video.join_controls()
        if self._worker is not None:
            await asyncio.gather(self._worker, return_exceptions=True)
        if self._policy_worker is not None:
            await asyncio.gather(self._policy_worker, return_exceptions=True)
        if self._business_jobs:
            await asyncio.gather(*tuple(self._business_jobs), return_exceptions=True)
        if self._creating is not None:
            result = await _join_owned(self._creating)
            if self.manager is None:
                self.manager = result

    async def close(self) -> None:
        if self._cleanup is None:
            self.fence()
            self._cleanup = asyncio.create_task(self._close_owned(), name="iroh-call-audio-close")
        await _join_owned(self._cleanup)

    async def _close_owned(self) -> None:
        await self.join_controls()
        errors = []
        if self._video is not None:
            try:
                await self._video.close()
            except BaseException as exc:
                errors.append(exc)
        try:
            await self._stop_recording()
        except BaseException as exc:
            errors.append(exc)
        manager = self.manager
        if manager is not None:
            def close():
                manager.close()
                for name in ("_feed_audio_thread", "_init_thread", "_transcription_thread"):
                    thread = getattr(manager, name, None)
                    if thread is not None and thread.is_alive():
                        raise RuntimeError("native speech owner cleanup did not finish")
            try:
                await _join_owned(asyncio.create_task(asyncio.to_thread(close)))
            except BaseException as exc:
                errors.append(exc)
            else:
                if self.runtime.STATE.audio_managers.get(self.session_id) is manager:
                    self.runtime.STATE.audio_managers.pop(self.session_id)
                self.manager = None
        while not self._controls.empty():
            self._controls.get_nowait(); self._controls.task_done()
        if errors:
            self._cleanup_error = self._cleanup_error or errors[0]
        if self._cleanup_error is not None:
            raise self._cleanup_error
