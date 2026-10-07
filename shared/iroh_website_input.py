"""Owned input for an authenticated website's exact JPEG monitor/window view.

Website pictures remain on the native HTTP/WebSocket carriage. Their source
receipts are separate from raw call video; they never invent a codec consent.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
import time
import uuid

from shared.iroh_input import NativeInputAuthority, NativeInputLease, NativeDesktopInputPort, input_source_record, matches_input_source
from shared.iroh_media import _join_owned
from shared.iroh_media_budget import NativeMediaBudget
from shared.session_transport import SessionDenied


MAX_WEBSITE_VIEWS = 32
MAX_CONTROL_MS = 60_000
_gate = threading.RLock()
_views: dict[str, dict[str, "NativeWebsiteView"]] = {}


def _scope():
    root = os.environ.get("AUTOYOU_TEST_ROOT")
    return str(Path(root).resolve()) if root else "production"


@dataclass(frozen=True)
class WebsitePictureSource:
    session: object
    call_id: str
    lease_id: str
    source_id: int
    media_generation: int
    target_type: str
    target_id: str | int
    expires_at_ms: int


class NativeWebsiteView:
    def __init__(self, *, channel, target_type, target_id, fps, now_ms=None, port_factory=NativeDesktopInputPort):
        if target_type not in {"monitor", "window"} or type(fps) is not int or not 1 <= fps <= 30 or \
                type(target_id) not in {str, int} or (isinstance(target_id, str) and not 0 < len(target_id) <= 128) or \
                (type(target_id) is int and not 0 <= target_id < 2**64):
            raise SessionDenied("invalid website picture target")
        self.channel, self.binding = channel, channel.binding
        channel.registry.check(self.binding, scope="browser")
        self.now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self.source = WebsitePictureSource(self.binding, str(uuid.uuid4()), str(uuid.uuid4()), 6, 1,
            target_type, target_id, self.now_ms() + 300_000)
        self.fps, self.port_factory = fps, port_factory
        self.loop = asyncio.get_running_loop()
        self._geometry = None
        self._picture = None
        self._receipts = {}
        self._frame = 0
        self._lease = None
        self._keyboard_only = None
        self._seen_controls = set()
        self._closed = False
        self._cleanup = None
        self._failure = None
        self._workers = set()
        self._scope = _scope()
        with _gate:
            current = _views.setdefault(self._scope, {})
            if len(current) >= MAX_WEBSITE_VIEWS or any(v._failure is not None for v in current.values()):
                raise SessionDenied("website picture ownership is full or has uncertain cleanup")
            self._reservation = NativeMediaBudget.process().reserve_buffers(self.source, 128 * 1024**2)
            current[self.source.call_id] = self

    def _check(self, *, control=False):
        if self._closed or self._failure is not None or self.channel.binding != self.binding or self.now_ms() >= self.source.expires_at_ms:
            raise SessionDenied("website picture source ended or was replaced")
        self.channel.registry.check(self.binding, scope="browser")
        if control: self.channel.registry.check(self.binding, scope="control")
        if control and (self._picture is None or self.now_ms() >= self._picture[1]):
            raise SessionDenied("website picture has no current publication receipt")

    def publish(self, *, bounds, captured_at_ms, width, height):
        """Called only after this exact target's capture/encoding physically joins."""
        self._check()
        if not isinstance(bounds, dict) or set(bounds) != {"left", "top", "width", "height"} or \
                any(type(v) is not int for v in bounds.values()) or any(not 0 < bounds[k] <= 16384 for k in ("width", "height")) or \
                any(type(v) is not int for v in (captured_at_ms, width, height)) or not 0 < width <= 4096 or not 0 < height <= 4096 or \
                width * height * 4 > 32 * 1024 * 1024 or not 0 <= self.now_ms() - captured_at_ms < 200:
            raise SessionDenied("website picture changed its bounded capture receipt")
        geometry = json.dumps(bounds, sort_keys=True, allow_nan=False)
        if self._geometry is not None and self._geometry != geometry:
            self.fence()
            raise SessionDenied("website target geometry changed; new view/control approval required")
        self._geometry = geometry
        self._frame += 1
        if self._frame >= 2**64:
            self.fence(); raise SessionDenied("website picture sequence exhausted")
        grace = min(3000, max(500, (2000 + self.fps - 1) // self.fps))
        deadline = min(self.source.expires_at_ms, captured_at_ms + grace)
        self._picture = self._frame, deadline
        self._receipts[self._frame] = deadline
        if len(self._receipts) > 8: self._receipts.pop(next(iter(self._receipts)))
        return dict(event="native_website_frame", version=1, source=input_source_record(self.source),
            sequence=self._frame, width=width, height=height, expires_in_ms=max(0, deadline - self.now_ms()),
            target_type=self.source.target_type, target_id=self.source.target_id)

    def remote_desktop_mapping(self):
        self._check(control=True)
        return dict(monitor_bounds=json.loads(self._geometry), content_rect=dict(x=0, y=0, width=1, height=1))

    async def worker(self, function, *args):
        self._check()
        job = asyncio.create_task(asyncio.to_thread(function, *args), name="iroh-website-capture")
        self._workers.add(job)
        try:
            return await _join_owned(job)
        finally:
            if job.done(): self._workers.discard(job)

    def control(self, control_id):
        self._check(control=True)
        if self._lease is None or self._lease.authority.control_id != control_id:
            raise SessionDenied("website control belongs to another approval")
        self._lease._check()
        return self._grant()

    def cleanup_grant(self, control_id):
        if self._lease is None or self._lease.authority.control_id != control_id:
            raise SessionDenied("website cleanup belongs to another approval")
        lease = self._lease
        return dict(version=1,view_id=self.source.call_id,control_id=control_id,native_input=lease.authority.record(),
            expires_at_ms=lease.authority.expires_at_ms,input_clock_ms=lease.input_clock_ms)

    async def approve(self, *, control_id, source, frame_sequence, keyboard_only=False):
        self._check(control=True)
        try: canonical = str(uuid.UUID(control_id))
        except (ValueError, TypeError, AttributeError): raise SessionDenied("invalid website control identity") from None
        if canonical != control_id or not matches_input_source(source, self.source) or type(frame_sequence) is not int or \
                self._picture is None or not 0 < frame_sequence <= self._picture[0] or type(keyboard_only) is not bool:
            raise SessionDenied("website control did not acknowledge its current selected picture")
        if self._lease is not None:
            self._lease._check()
            if self._lease.authority.control_id != control_id or self._keyboard_only != keyboard_only:
                raise SessionDenied("website input already has an approved owner")
            return self._grant()
        if self.now_ms() >= self._receipts.get(frame_sequence, 0) or control_id in self._seen_controls or len(self._seen_controls) >= 1024:
            raise SessionDenied("website input requires a fresh picture and new explicit approval")
        self._seen_controls.add(control_id)
        authority = NativeInputAuthority(self.source, control_id, str(uuid.uuid4()), min(self.source.expires_at_ms, self.now_ms() + MAX_CONTROL_MS))
        # The factory makes a cheap descriptor. NativeInputLease claims process
        # ownership before prepare() loads or mutates an actual OS SDK.
        port = self.port_factory()
        lease = NativeInputLease(authority=authority, track=self, port=port, check_current=lambda: self._check(control=True),
            now_ms=self.now_ms, on_failure=self._failed, allowed_events=frozenset({"remote_desktop_keyboard"}) if keyboard_only else
            frozenset({"remote_desktop_keyboard", "remote_desktop_input"}))
        self._lease = lease
        self._keyboard_only = keyboard_only
        try:
            await lease.ready(); self._check(control=True)
            return self._grant()
        except BaseException:
            await self.stop_control()
            raise

    def _grant(self):
        lease = self._lease; lease._check()
        return dict(version=1, view_id=self.source.call_id, control_id=lease.authority.control_id,
            native_input=lease.authority.record(), expires_at_ms=lease.authority.expires_at_ms,
            input_clock_ms=lease.announce())

    async def receive(self, payload, *, transport_deadline_us=None):
        self._check(control=True)
        if self._lease is None: raise SessionDenied("website input has no explicit control approval")
        self._lease.receive(payload, transport_deadline_us=transport_deadline_us)
        if payload.get("event") == "remote_desktop_keyboard" and payload.get("action") == "hide":
            self._lease.fence()
            await self.stop_control()

    async def stop_control(self):
        lease = self._lease
        if lease is not None:
            try: await lease.close()
            except asyncio.CancelledError:
                cleanup = lease._cleanup
                if cleanup is not None and cleanup.done() and not cleanup.cancelled() and cleanup.exception() is None:
                    if self._lease is lease: self._lease = None
                else:
                    self._failure = self._failure or SessionDenied("website input cleanup is still owned")
                raise
            except BaseException as error:
                self._failure = self._failure or error
                raise
            if self._lease is lease: self._lease = None

    def _failed(self, error):
        self._failure = self._failure or error
        self.fence()

    def fence(self):
        self._closed = True
        if self.loop.is_running(): self.loop.call_soon_threadsafe(self._ensure_close)

    def _ensure_close(self):
        if self._lease is not None: self._lease.fence()
        if self._cleanup is None:
            async def closed():
                try:
                    await self.stop_control()
                    for job in tuple(self._workers): await _join_owned(job)
                except BaseException as error:
                    self._failure = self._failure or error
                    self._reservation.retain_failure(error, self)
                    raise
                self._reservation.release()
                with _gate:
                    current = _views.get(self._scope)
                    if current is not None:
                        current.pop(self.source.call_id, None)
                        if not current: _views.pop(self._scope, None)
            self._cleanup = asyncio.create_task(closed(), name="iroh-website-view-close")

    async def close(self):
        self.fence()
        self._ensure_close()
        await _join_owned(self._cleanup)


def website_view(binding, view_id):
    if not isinstance(view_id, str) or not 0 < len(view_id) <= 64:
        raise SessionDenied("invalid website view identity")
    with _gate:
        owner = _views.get(_scope(), {}).get(view_id)
    if owner is None or owner.binding != binding:
        raise SessionDenied("website picture belongs to another connection generation")
    return owner


async def in_view(owner, operation, *args, **kwargs):
    if asyncio.get_running_loop() is owner.loop:
        return await operation(*args, **kwargs)
    if owner.loop.is_closed() or not owner.loop.is_running():
        owner._failure = SessionDenied("website input loop stopped before owned cleanup")
        owner.fence(); raise owner._failure
    posted = asyncio.run_coroutine_threadsafe(operation(*args, **kwargs), owner.loop)
    async def joined(): return await asyncio.wrap_future(posted)
    return await _join_owned(asyncio.create_task(joined(), name="iroh-website-loop-receipt"))


async def dispatch_website_input(channel, payload, *, transport_deadline_us=None):
    marker = payload.get("native_website")
    if marker is None: return False
    if not isinstance(marker, dict) or set(marker) != {"version", "view_id"} or type(marker.get("version")) is not int or marker["version"] != 1:
        raise SessionDenied("invalid native website input marker")
    owner = website_view(channel.binding, marker["view_id"])
    await in_view(owner, owner.receive, payload, transport_deadline_us=transport_deadline_us)
    return True


async def close_website_views(binding):
    with _gate:
        owners = [view for view in _views.get(_scope(), {}).values() if view.binding == binding]
    for owner in owners: owner.fence()
    results = await asyncio.gather(*(in_view(owner, owner.close) for owner in owners), return_exceptions=True)
    if any(isinstance(result, BaseException) for result in results):
        raise RuntimeError("owned website input cleanup failed")
