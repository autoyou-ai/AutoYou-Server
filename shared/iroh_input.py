# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Native input leases own ordered OS work and every potentially held input."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

from shared.iroh_media import _join_owned
from shared.iroh_media_negotiation import source_reference
from shared.remote_desktop_input import normalize_remote_desktop_input_payload, execute_remote_desktop_input, _control_backend
from shared.remote_desktop_keyboard import normalize_remote_desktop_keyboard_payload, execute_remote_desktop_keyboard, _mapped_key
from shared.session_transport import SessionDenied
from shared.game_input import normalize_game_input
from shared.native_keyboard_text import load_native_text_port


MAX_INPUT_BYTES = 16384
MAX_PENDING_INPUTS = 64
MAX_HELD_KEYS = 64
MAX_INPUT_AGE_MS = 200
_host_lock = threading.Lock()
_host_owners: dict[str, Any] = {}
_legacy_input_joins: dict[str, set[Any]] = {}


def _input_host_scope():
    root = os.environ.get("AUTOYOU_TEST_ROOT")
    return str(Path(root).resolve()) if root else "production"


def native_host_input_busy() -> bool:
    scope = _input_host_scope()
    with _host_lock:
        return scope in _host_owners or bool(_legacy_input_joins.get(scope))


class LegacyInputCleanupBarrier:
    """Retain legacy input ownership until its physical release task joins."""
    def __init__(self, lease, *, admit=False):
        self.lease, self.scope = lease, _input_host_scope()
        with _host_lock:
            if admit and (self.scope in _host_owners or _legacy_input_joins.get(self.scope)):
                raise SessionDenied("host input is already owned")
            _legacy_input_joins.setdefault(self.scope, set()).add(self)

    def release(self):
        with _host_lock:
            owners = _legacy_input_joins.get(self.scope)
            if owners is not None:
                owners.discard(self)
                if not owners: del _legacy_input_joins[self.scope]
        self.lease = None


def input_source_record(binding: Any) -> dict:
    return json.loads(source_reference(binding))


def matches_input_source(value: Any, binding: Any) -> bool:
    expected = input_source_record(binding)
    return isinstance(value, dict) and set(value) == set(expected) and \
        all(type(value[key]) is type(expected[key]) for key in expected) and value == expected


def _unique_object(items):
    value = {}
    for key, item in items:
        if key in value:
            raise SessionDenied("duplicate native input field")
        value[key] = item
    return value


def decode_input_record(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_INPUT_BYTES:
        raise SessionDenied("native input record exceeded its bound")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite input")))
    except (ValueError, UnicodeDecodeError) as error:
        raise SessionDenied("invalid native input record") from error
    if not isinstance(value, dict):
        raise SessionDenied("native input record is not an object")
    return value


def normalize_native_input(value: dict) -> dict:
    if value.get("event") == "remote_desktop_input":
        frame = normalize_remote_desktop_input_payload(value)
    elif value.get("event") == "remote_desktop_keyboard":
        frame = normalize_remote_desktop_keyboard_payload(value, preserve_unicode=True)
        if frame is not None and frame["action"] not in {"key", "input", "hide"}:
            frame = None
    elif value.get("event") == "game_input":
        frame = normalize_game_input(value)
    else:
        frame = None
    if frame is None:
        raise SessionDenied("invalid native desktop input")
    return frame


def normalize_native_screen_input(value: dict) -> dict:
    kind, item, phase = (value.get(key) for key in ("kind", "value", "phase"))
    if not all(isinstance(part, str) for part in (kind, item, phase)) or not (
        kind == "layout" and phase == "set" and item in {"off", "choices", "gamepad"}
        or kind == "choice" and phase == "press" and item in {"A", "B", "C", "D"}
        or kind == "controller" and phase == "press" and item in {"A", "B", "C", "D", "←", "↑", "↓", "→", "Select"}
    ):
        raise SessionDenied("invalid native screen choice input")
    return dict(event="screen_input", kind=kind, value=item, phase=phase)


@dataclass(frozen=True)
class NativeInputAuthority:
    source: Any
    control_id: str
    lease_id: str
    expires_at_ms: int

    def record(self) -> dict:
        return dict(version=1, source=input_source_record(self.source), lease_id=self.lease_id)

    def check_record(self, value: Any) -> int:
        if not isinstance(value, dict) or set(value) != {"version", "source", "lease_id", "sequence", "elapsed_ms"} or \
                type(value.get("version")) is not int or value["version"] != 1 or \
                not matches_input_source(value["source"], self.source) or value["lease_id"] != self.lease_id or \
                type(value["sequence"]) is not int or not 0 < value["sequence"] < 2**64 or \
                type(value["elapsed_ms"]) is not int or not 0 <= value["elapsed_ms"] < 2**64:
            raise SessionDenied("native input authority changed or is malformed")
        return value["sequence"]


class NativeDesktopInputPort:
    """Cheap descriptor. The host slot is owned before SDK import/probing."""
    def __init__(self):
        self.backend = None
        self.text = None

    def prepare(self):
        if os.environ.get("AUTOYOU_TEST_ROOT"):
            raise SessionDenied("physical host input is disabled in a test root")
        self.backend = _control_backend()
        self.text = load_native_text_port(self.backend)

    def apply(self, frame, track):
        return self.apply_guarded(frame, track, lambda: None)

    def apply_guarded(self, frame, track, guard):
        if self.backend is None:
            raise RuntimeError("native host input was not prepared")
        guard()
        if frame["event"] == "remote_desktop_input":
            return execute_remote_desktop_input(frame, track=track, pyautogui=self.backend)
        if frame["action"] == "hide":
            return True
        if frame["action"] == "input":
            if self.text is None:
                raise RuntimeError("native text input was not prepared")
            return self.text.write(frame["text"], guard)
        return execute_remote_desktop_keyboard(frame, pyautogui=self.backend, release_modifiers=False)

    def release(self, buttons, keys):
        if not buttons and not keys and self.text is None:
            return
        if self.backend is None:
            raise RuntimeError("native held input has no physical backend")
        errors = []
        if self.text is not None:
            try: self.text.release()
            except BaseException as error: errors.append(error)
        for button in buttons:
            try: self.backend.mouseUp(button=button)
            except BaseException as error: errors.append(error)
        for key in keys:
            try: self.backend.keyUp(key)
            except BaseException as error: errors.append(error)
        if errors:
            raise RuntimeError("native held input cleanup failed") from errors[0]


class NativeInputLease:
    def __init__(self, *, authority: NativeInputAuthority, track: Any, port: Any,
                 check_current: Callable[[], None], now_ms: Callable[[], int],
                  on_failure: Callable[[BaseException], None], idle_ms: int = 15000,
                  allowed_events=frozenset({"remote_desktop_input", "remote_desktop_keyboard"})) -> None:
        self.authority, self.track, self.port = authority, track, port
        self.check_current, self.now_ms, self.on_failure = check_current, now_ms, on_failure
        self.idle_ms = idle_ms
        self.allowed_events = allowed_events
        self._deadline = min(authority.expires_at_ms, now_ms() + idle_ms)
        self._closed = False
        self._sequence = 0
        self.input_clock_ms = now_ms()
        self._announced = False
        self._queue = asyncio.Queue(maxsize=MAX_PENDING_INPUTS)
        self._held_buttons: set[str] = set()
        self._held_keys: set[str] = set()
        self._physical: asyncio.Task | None = None
        self._preparing: asyncio.Task | None = None
        self._prepared = not any(callable(getattr(port, name, None)) for name in ("prepare", "prepare_async"))
        self._cleanup: asyncio.Task | None = None
        self._error: BaseException | None = None
        self._scope = _input_host_scope()
        with _host_lock:
            if self._scope in _host_owners or _legacy_input_joins.get(self._scope):
                raise SessionDenied("native host input already has an owner or unjoined cleanup")
            _host_owners[self._scope] = self
        self._worker = asyncio.create_task(self._run(), name="iroh-native-input")
        self._timer = asyncio.create_task(self._watch(), name="iroh-native-input-expiry")

    def _check(self):
        if self._closed or self.now_ms() >= self._deadline:
            raise SessionDenied("native input lease ended or expired")
        self.check_current()

    async def ready(self):
        self._check()
        if not self._prepared:
            if self._preparing is None:
                async def prepare():
                    self._check()
                    if callable(getattr(self.port, "prepare_async", None)):
                        await self.port.prepare_async()
                    else:
                        await asyncio.to_thread(self.port.prepare)
                self._preparing = asyncio.create_task(prepare(), name="iroh-native-input-prepare")
            await _join_owned(self._preparing)
            self._check()
            self._prepared = True

    def announce(self):
        self._check()
        if not self._announced:
            self.input_clock_ms = self.now_ms()
            self._announced = True
        return self.input_clock_ms

    def receive(self, payload: dict, *, transport_deadline_us=None, admission_check=None) -> None:
        try:
            self._check()
        except SessionDenied as error:
            self._request_close(error)
            raise
        if not self._prepared:
            raise SessionDenied("native input physical backend is not ready")
        if payload.get("control_id") != self.authority.control_id:
            raise SessionDenied("native input changed its control identity")
        sequence = self.authority.check_record(payload.get("native_input"))
        now = self.now_ms()
        deadline_ms = self.input_clock_ms + payload["native_input"]["elapsed_ms"] + MAX_INPUT_AGE_MS
        if not now < deadline_ms <= now + 2 * MAX_INPUT_AGE_MS:
            error = SessionDenied("native input exceeded its anchored freshness budget")
            self._request_close(error)
            raise error
        arrival_us = time.monotonic_ns() // 1000
        deadline_us = arrival_us + min(MAX_INPUT_AGE_MS, deadline_ms - now) * 1000
        if transport_deadline_us is not None:
            if type(transport_deadline_us) is not int or transport_deadline_us <= arrival_us:
                error = SessionDenied("native input transport delivery expired")
                self._request_close(error)
                raise error
            deadline_us = min(deadline_us, transport_deadline_us)
        if payload.get("event") not in self.allowed_events:
            raise SessionDenied("native input is outside its approved mode")
        check_payload = getattr(self.port, "check_payload", None)
        if check_payload is not None:
            check_payload(payload)
        if sequence != self._sequence + 1:
            self.fence()
            self._request_close(SessionDenied("native input order changed; held inputs are retired"))
            raise SessionDenied("native input sequence is stale or has a gap")
        frame = normalize_native_input(payload)
        if admission_check is not None:
            admission_check()
        try:
            size = len(json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8"))
        except (ValueError, TypeError) as error:
            raise SessionDenied("invalid native input values") from error
        if size > MAX_INPUT_BYTES:
            raise SessionDenied("native input record exceeded its bound")
        self._sequence = sequence
        self._deadline = min(self.authority.expires_at_ms, self.now_ms() + self.idle_ms)
        try:
            self._queue.put_nowait((frame, self.now_ms(), deadline_ms, deadline_us, admission_check))
        except asyncio.QueueFull:
            self.fence()
            self._request_close(SessionDenied("native input queue capacity was exceeded"))
            raise SessionDenied("native input queue capacity was exceeded") from None

    def _reserve_held(self, frame):
        reserve = getattr(self.port, "reserved_inputs", None)
        if reserve is not None:
            buttons, keys = reserve(frame)
            if len(self._held_keys | keys) > MAX_HELD_KEYS:
                raise SessionDenied("native held-key capacity was exceeded")
            self._held_buttons.update(buttons); self._held_keys.update(keys)
        if frame["event"] == "remote_desktop_input" and frame["input_type"] == "button":
            if frame["phase"] != "up": self._held_buttons.add(frame["button"])
        elif frame["event"] == "remote_desktop_keyboard" and frame["action"] == "key":
            if frame["phase"] != "up":
                key = _mapped_key(frame["key"])
                if key not in self._held_keys and len(self._held_keys) >= MAX_HELD_KEYS:
                    raise SessionDenied("native held-key capacity was exceeded")
                self._held_keys.add(key)

    def _settled_held(self, frame):
        settled = getattr(self.port, "settled_inputs", None)
        if settled is not None:
            buttons, keys, released_buttons, released_keys = settled(frame)
            self._held_buttons.update(buttons); self._held_keys.update(keys)
            self._held_buttons.difference_update(released_buttons); self._held_keys.difference_update(released_keys)
        if frame["event"] == "remote_desktop_input" and frame["input_type"] == "button":
            if frame["phase"] != "down": self._held_buttons.discard(frame["button"])
        elif frame["event"] == "remote_desktop_keyboard" and frame["action"] == "key":
            if frame["phase"] != "down": self._held_keys.discard(_mapped_key(frame["key"]))

    async def _run(self):
        try:
            while not self._closed:
                frame, received, deadline_ms, deadline_us, admission_check = await self._queue.get()
                try:
                    def check_frame():
                        self._check()
                        if admission_check is not None:
                            admission_check()
                        if self.now_ms() >= deadline_ms or time.monotonic_ns() // 1000 >= deadline_us:
                            raise SessionDenied("native input waited beyond its freshness budget")
                    check_frame()
                    if frame["event"] == "remote_desktop_keyboard" and frame["action"] in {"hide", "input"}:
                        releasing = asyncio.create_task(self._release_port(set(), set(self._held_keys)),
                            name="iroh-native-input-modifier-release")
                        await _join_owned(releasing)
                        self._held_keys.clear()
                        check_frame()
                    self._reserve_held(frame)
                    async def apply():
                        check_frame()  # Recheck immediately before physical SDK dispatch.
                        if callable(getattr(self.port, "apply_async", None)):
                            return await self.port.apply_async(frame, self.track)
                        def physical():
                            check_frame()
                            guarded = getattr(self.port, "apply_guarded", None)
                            if callable(guarded):
                                return guarded(frame, self.track, check_frame)
                            return self.port.apply(frame, self.track)
                        return await _join_owned(asyncio.create_task(asyncio.to_thread(physical)))
                    self._physical = asyncio.create_task(apply(), name="iroh-native-input-physical")
                    applied = await _join_owned(self._physical)
                    self._physical = None
                    if applied is not True:
                        raise RuntimeError("native host input could not be applied")
                    self._settled_held(frame)
                    check_frame()
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._request_close(error)

    def _request_close(self, error):
        if self._error is None: self._error = error
        self.fence()
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._close_owned(), name="iroh-native-input-close")
        self.on_failure(error)

    async def _watch(self):
        try:
            while not self._closed:
                await asyncio.sleep(0.05)
                self._check()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._request_close(error)

    async def _release_port(self, buttons, keys, *, closing=False):
        close = getattr(self.port, "close_async", None) if closing else None
        release = close or getattr(self.port, "release_async", None)
        if release is not None:
            await release(buttons, keys)
        else:
            await _join_owned(asyncio.create_task(asyncio.to_thread(self.port.release, buttons, keys)))

    def fence(self):
        self._closed = True
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._close_owned(), name="iroh-native-input-close")

    async def close(self):
        self.fence()
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._close_owned(), name="iroh-native-input-close")
        await _join_owned(self._cleanup)

    async def _close_owned(self):
        self._timer.cancel()
        self._worker.cancel()
        await asyncio.gather(self._timer, self._worker, return_exceptions=True)
        if self._preparing is not None:
            await asyncio.gather(self._preparing, return_exceptions=True)
        if self._physical is not None:
            await asyncio.gather(self._physical, return_exceptions=True)
        while not self._queue.empty():
            self._queue.get_nowait(); self._queue.task_done()
        job = asyncio.create_task(self._release_port(set(self._held_buttons), set(self._held_keys), closing=True),
            name="iroh-native-input-release")
        # A failed release retains the exact sets, port, track and global host slot.
        await _join_owned(job)
        self._held_buttons.clear(); self._held_keys.clear()
        self.track = self.port = None
        with _host_lock:
            if _host_owners.get(self._scope) is self: del _host_owners[self._scope]


class IrohInputService:
    """Bounded raw input lane routes only into the current application owner."""
    async def receive(self, channel, frame):
        channel.registry.check(channel.binding, scope="control")
        if frame.lane != 9 or frame.generation != channel.binding.generation or \
                type(frame.stream_id) is not int or frame.stream_id != 0 or \
                type(frame.sequence) is not int or not 0 <= frame.sequence < 2**64:
            raise SessionDenied("native input frame is on an obsolete or wrong lane")
        payload = decode_input_record(bytes(frame.payload))
        if payload.get("event") not in {"remote_desktop_input", "remote_desktop_keyboard", "game_input", "screen_input", "screen_tutor_action"}:
            raise SessionDenied("native input lane cannot grant or restore control")
        proof = payload.get("native_input")
        if not isinstance(proof, dict) or type(proof.get("sequence")) is not int or not 0 < proof["sequence"] < 2**64:
            raise SessionDenied("native input has no application lease sequence")
        from shared.iroh_website_input import dispatch_website_input
        if await dispatch_website_input(channel, payload, transport_deadline_us=getattr(frame, "_iroh_deadline_us", None)):
            return
        owner = getattr(getattr(channel, "native_media", None), "call_owner", None)
        video = getattr(owner, "_video", None)
        if payload.get("event") == "screen_tutor_action":
            tutor = getattr(video, "_tutor", None)
            if tutor is None:
                raise SessionDenied("native tutoring input has no context owner")
            try:
                tutor.approve(payload, transport_deadline_us=getattr(frame, "_iroh_deadline_us", None))
            except SessionDenied:
                return
            return
        if payload.get("event") == "screen_input":
            if video is None:
                raise SessionDenied("native screen input has no video owner")
            try:
                await video.receive_screen_input(payload, transport_deadline_us=getattr(frame, "_iroh_deadline_us", None))
            except SessionDenied:
                return  # Obsolete choices cannot change a current screen or OS input lease.
            return
        controller = getattr(video, "_desktop_control", None)
        if controller is None:
            raise SessionDenied("native input lane has no approved controller")
        try:
            await controller.receive(payload, transport_deadline_us=getattr(frame, "_iroh_deadline_us", None))
        except SessionDenied:
            return  # Stale input retires only its application control lease.
