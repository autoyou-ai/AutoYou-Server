# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Server control admission tied to an accepted native desktop video source."""
from __future__ import annotations

import asyncio
import json
from typing import Any
import uuid

from shared.iroh_input import NativeInputAuthority, NativeInputLease, NativeDesktopInputPort, input_source_record, matches_input_source
from shared.iroh_game_input import NativeGameInputPort, NativeHostGameInputPort
from shared.iroh_media import _join_owned
from shared.iroh_media_video import SERVER_VIDEO_SOURCE_ID
from shared.remote_desktop_input import normalize_remote_desktop_control_payload
from shared.session_transport import SessionDenied


MAX_NATIVE_CONTROL_MS = 60_000


class IrohServerDesktopControl:
    def __init__(self, *, video: Any):
        self.video, self.parent, self.media = video, video.parent, video.media
        self._lease: NativeInputLease | None = None
        self._requested = None
        self._requested_expiry = None
        self._stop_requested = None
        self._announced_lease = None
        self._controls = asyncio.Queue(maxsize=8)
        self._closing = False
        self._cleanup = None
        self._retirements = {}
        self._game_audio = {}
        self._worker = asyncio.create_task(self._run(), name="iroh-desktop-control")

    def _source(self, payload):
        source = self.video._outgoing
        self.video._parent_check()
        self.media.channel.registry.check(self.media.binding, scope="control")
        proof = payload.get("native_input")
        if source is None or source.source_id != SERVER_VIDEO_SOURCE_ID or not isinstance(proof, dict) or \
                type(proof.get("version")) is not int or proof["version"] != 1 or \
                not matches_input_source(proof.get("source"), source):
            raise SessionDenied("desktop control requires the current native video source")
        self.video._source_check(source)
        current = self.media._sources.get(("send", SERVER_VIDEO_SOURCE_ID))
        if current is None or current.closing or current.binding != source:
            raise SessionDenied("desktop video is not active for control")
        return source

    def _check_lease(self, source, track, mapping, mode, port):
        self.video._source_check(source)
        self.media.channel.registry.check(self.media.binding, scope="control")
        if self._closing or self._requested is None or self.video._outgoing != source or \
                not self._policy_available(mode):
            raise SessionDenied("native desktop control consent ended")
        if mode == "game" and port is not None:
            port.check_current()
        if json.dumps(track.remote_desktop_mapping(), allow_nan=False, sort_keys=True) != mapping:
            raise SessionDenied("native desktop mapping changed")

    def _policy_available(self, mode):
        runtime = self.parent.runtime
        name = "_get_game_mode_available" if mode == "game" else "_get_remote_desktop_control_available"
        allowed = getattr(runtime, name, None)
        return callable(allowed) and allowed(cfg=runtime.STATE.config or {}) is True

    async def receive(self, payload, *, transport_deadline_us=None):
        event = payload.get("event")
        stopping = event == "remote_desktop_control" and payload.get("action") == "stop"
        if stopping:
            identifier = payload.get("control_id")
            expected = self._requested
            candidates = ([self._lease] if self._lease is not None else []) + list(self._retirements)
            owned = next((lease for lease in candidates if lease.authority.control_id == identifier), None)
            if expected is not None and expected[0] == identifier:
                source = expected[1]
            elif owned is not None:
                source = owned.authority.source
                expected = (identifier, source, "game" if getattr(owned.port, "game_binding", None) is not None else "desktop")
            else:
                return
            proof = payload.get("native_input")
            if not isinstance(proof, dict) or type(proof.get("version")) is not int or proof["version"] != 1 or \
                    not matches_input_source(proof.get("source"), source):
                raise SessionDenied("native desktop Stop changed its owned source")
        else:
            source = self._source(payload)
        if event != "remote_desktop_control":
            if self._lease is None:
                raise SessionDenied("native desktop input has no approved control lease")
            self._lease.receive(payload, transport_deadline_us=transport_deadline_us)
            return
        normalized = normalize_remote_desktop_control_payload(payload)
        if normalized is None:
            raise SessionDenied("invalid native desktop control request")
        identifier = payload.get("control_id")
        try: uuid.UUID(identifier)
        except (ValueError, TypeError, AttributeError) as error:
            raise SessionDenied("native desktop control requires an explicit UUID") from error
        proof = payload["native_input"]
        if set(proof) != {"version", "source", "lease_id", "sequence"} or \
                not isinstance(proof["lease_id"], str) or type(proof["sequence"]) is not int:
            raise SessionDenied("invalid native desktop control authority")
        if normalized["action"] == "start":
            mode = normalized.get("mode", "desktop")
            if self._stop_requested is not None:
                raise SessionDenied("native desktop Stop has not joined")
            if proof["lease_id"] or proof["sequence"] != 0:
                raise SessionDenied("native desktop start cannot reuse a control lease")
            if self._requested is not None and self._requested != (identifier, source, mode):
                raise SessionDenied("native desktop control already has an intent")
            if self._requested is None:
                self._requested_expiry = min(source.expires_at_ms, self.media.now_ms() + MAX_NATIVE_CONTROL_MS)
            self._requested = (identifier, source, mode)
        else:
            lease = owned if stopping and owned is not None else self._lease
            expected = expected if stopping else self._requested
            if expected is None or expected[0] != identifier:
                return
            mode = expected[2]
            if lease is not None and proof["lease_id"] != lease.authority.lease_id and \
                    not (proof["lease_id"] == "" and self._announced_lease is not lease):
                raise SessionDenied("native desktop stop changed its lease identity")
            self._stop_requested = (identifier, source, mode)
            self.fence()
        try: self._controls.put_nowait((normalized["action"], identifier, source, mode))
        except asyncio.QueueFull:
            self.fence()
            self.media.negotiation._request_shutdown()
            raise SessionDenied("native desktop control capacity was exceeded") from None

    async def _start(self, identifier, source, mode):
        await self._join_retirements()
        if self._requested != (identifier, source, mode):
            return
        if self._requested_expiry is None or self.media.now_ms() >= self._requested_expiry:
            raise SessionDenied("native control approval expired before acquisition")
        if self._lease is not None:
            if self._lease._closed:
                previous = self._lease
                await previous.close()
                if self._lease is previous: self._lease = None
                if self._requested != (identifier, source, mode): return
            else:
                if self._lease.authority.control_id != identifier:
                    raise SessionDenied("native desktop control replacement has not joined")
                self._lease._check()
                await self._status(identifier, True)
                return
        runtime = self.parent.runtime
        if not self._policy_available(mode):
            raise SessionDenied("server policy disables desktop control")
        if mode == "game" and not (self.video._call and self.video._call[6]):
            raise SessionDenied("game control requires an explicit desktop-only game video intent")
        if getattr(self.parent.engine, "remote_desktop_control_leases_by_session", {}):
            raise SessionDenied("legacy host input already has a controller")
        tracks = [owner.track for owner in self.video._groups if not owner._closed and owner.track is not None]
        if len(tracks) != 1:
            raise SessionDenied("native desktop capture has no exact physical owner")
        track = tracks[0]
        mapping = track.remote_desktop_mapping()
        if not isinstance(mapping, dict) or not mapping.get("content_rect"):
            raise SessionDenied("native desktop pane cannot map control coordinates")
        serialized = json.dumps(mapping, allow_nan=False, sort_keys=True)
        authority = NativeInputAuthority(source, identifier, str(uuid.uuid4()), self._requested_expiry)
        if mode == "game":
            hub = getattr(self.parent.engine, "game_input_hub", None)
            if hub is None:
                raise SessionDenied("native game has no selected engine")
            if hub.binding() is not None:
                port = NativeGameInputPort(hub=hub, session_id=self.parent.session_id, control_id=identifier)
            else:
                factory = getattr(runtime, "_create_native_desktop_input_port", NativeDesktopInputPort)
                port = NativeHostGameInputPort(hub=hub, port=factory(), control_id=identifier)
                port.scope_check = lambda: self._check_lease(source, track, serialized, mode, None)
        else:
            factory = getattr(runtime, "_create_native_desktop_input_port", NativeDesktopInputPort)
            port = factory()  # Cheap descriptor; SDK work waits for the host-slot owner.
        lease = NativeInputLease(authority=authority, track=track, port=port,
            check_current=lambda: self._check_lease(source, track, serialized, mode, port), now_ms=self.media.now_ms,
            on_failure=lambda error: self._failed(error, lease=lease),
            allowed_events=frozenset({"remote_desktop_input", "remote_desktop_keyboard", "game_input"}) if mode == "game" else
                frozenset({"remote_desktop_input", "remote_desktop_keyboard"}))
        self._lease = lease
        await lease.ready()
        self._check_lease(source, track, serialized, mode, port)
        if mode == "game" and getattr(hub, "owner", "") == "hosted-neon" and runtime._get_audio_playback_enabled(cfg=runtime.STATE.config or {}):
            from shared.iroh_game_audio import NativeHostedGameAudio
            from core_server.webrtc_engine import _HOSTED_GAME_LOOP
            manager = self.parent.manager
            if manager is None:
                raise SessionDenied("hosted game has no admitted playback mixer")
            audio = NativeHostedGameAudio(manager=manager, path=str(_HOSTED_GAME_LOOP), check_current=lease._check)
            self._game_audio[lease] = audio
            await audio.start()
            self._check_lease(source, track, serialized, mode, port)
        await self._status(identifier, True)

    async def _status(self, identifier, active, reason="", *, proof=None):
        lease = self._lease
        payload = dict(event="remote_desktop_control_status", control_id=identifier, active=active)
        if proof is not None:
            payload["native_input"] = proof
        elif lease is not None:
            payload["native_input"] = lease.authority.record()
            payload["expires_at_ms"] = lease.authority.expires_at_ms
            if active:
                lease._check()
                payload["input_clock_ms"] = lease.announce()
                payload["mapping"] = lease.track.remote_desktop_mapping()
                game = getattr(lease.port, "game_binding", None)
                payload["mode"] = "game" if game is not None else "desktop"
                if game is not None:
                    payload["native_game"] = dict(game)
                    payload["engine_input"] = game["kind"] == "engine"
        if reason: payload["reason"] = reason
        await self.parent._send(payload)
        if active and lease is not None and self._lease is lease:
            self._announced_lease = lease

    async def _run(self):
        try:
            while not self._closing:
                action, identifier, source, mode = await self._controls.get()
                try:
                    if action == "start":
                        try: await self._start(identifier, source, mode)
                        except SessionDenied:
                            proof = self._lease.authority.record() if self._lease is not None else \
                                dict(version=1, source=input_source_record(source), lease_id="")
                            self.fence()
                            await self.end()
                            if not self.parent._closing:
                                await self._status(identifier, False, "Screen control is unavailable for this video.", proof=proof)
                    else:
                        proof = self._lease.authority.record() if self._lease is not None else \
                            dict(version=1, source=input_source_record(source), lease_id="")
                        await self.end()
                        if not self.parent._closing:
                            await self._status(identifier, False, proof=proof)
                        if self._stop_requested == (identifier, source, mode):
                            self._stop_requested = None
                finally: self._controls.task_done()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._failed(error)
        finally:
            while not self._controls.empty():
                self._controls.get_nowait(); self._controls.task_done()

    def _failed(self, error, *, lease=None):
        if lease is None or self._lease is lease:
            self.fence()
        if lease is not None and lease not in self._retirements:
            self._retirements[lease] = asyncio.create_task(self._retire_owned(lease), name="iroh-desktop-control-retire")
        if not isinstance(error, SessionDenied):
            self.parent._cleanup_error = error
            self.media.negotiation._request_shutdown()

    async def _retire_owned(self, lease):
        try:
            try:
                await lease.close()
            finally:
                await self._close_game_audio(lease)
            if self._lease is lease:
                self._lease = None
            if self._announced_lease is lease:
                self._announced_lease = None
            if not self._closing and not self.parent._closing and self.parent._cleanup_error is None:
                try:
                    self.media.channel.registry.check(self.media.binding, scope="control")
                    await self._status(lease.authority.control_id, False,
                        "Screen control ended. Request control again to continue.", proof=lease.authority.record())
                except SessionDenied:
                    pass  # A revoked/replaced session cannot receive an old owner's status.
        except BaseException as error:
            self.parent._cleanup_error = error
            self.media.negotiation._request_shutdown()
            raise
        else:
            self._retirements.pop(lease, None)

    async def _join_retirements(self):
        for task in tuple(self._retirements.values()):
            await _join_owned(task)

    async def _close_game_audio(self, lease):
        audio = self._game_audio.get(lease)
        if audio is not None:
            await audio.close()
            self._game_audio.pop(lease, None)

    def fence(self):
        self._requested = None
        self._requested_expiry = None
        if self._lease is not None: self._lease.fence()

    async def end(self):
        self.fence()
        await self._join_retirements()
        lease = self._lease
        if lease is not None:
            try:
                await lease.close()
            finally:
                await self._close_game_audio(lease)
            if self._lease is lease: self._lease = None
            if self._announced_lease is lease: self._announced_lease = None

    async def close(self):
        if self._cleanup is None:
            self.fence(); self._closing = True
            self._cleanup = asyncio.create_task(self._close_owned(), name="iroh-desktop-control-close")
        await _join_owned(self._cleanup)

    async def _close_owned(self):
        self._worker.cancel()
        await asyncio.gather(self._worker, return_exceptions=True)
        await self.end()
