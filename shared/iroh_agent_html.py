# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Bounded native HTML adaptation through the existing agent proxy shim."""
from collections.abc import Callable
import re


class AgentHTMLStream:
    """Rewrite path attributes without collecting a page or a quoted value.

    Only the first 8 KiB is retained for the existing head/viewport shim. A
    partial attribute holds its name and a short path prefix, never its value
    (which can itself contain a large inline asset).
    """
    PREFIX_BYTES = 8192
    _NAMES = (b"src", b"href", b"action")
    _NAME_START = re.compile(b"[sShHaA]")
    _PAGE_PATHS = (b"/agent-websites", b"/agent-frontends", b"/api/agent-websites",
        b"/api/agent-frontends", b"/api/agent-directory")

    def __init__(self, prefix: str, inject: Callable[[bytes, str], bytes], *, page_paths: tuple[bytes, ...] | None = None):
        if not re.fullmatch(r"/agent/[A-Za-z0-9_.-]{1,128}", prefix):
            raise ValueError("Invalid registered agent path")
        self.prefix = prefix
        self._prefix = prefix.encode("ascii")
        self._inject = inject
        self._page_paths = self._PAGE_PATHS if page_paths is None else page_paths
        self._head = bytearray()
        self._name = bytearray()
        self._probe = bytearray()
        self._state = "name"
        self._quote = 0
        self._injected = False
        self._finished = False

    @property
    def retained_bytes(self) -> int:
        return len(self._head) + len(self._name) + len(self._probe)

    def _path(self, closing: bool) -> tuple[bool, bool]:
        """Return (decided, exempt) after a bounded prefix probe."""
        path = bytes(self._probe)
        if not path.startswith(b"/"):
            return True, True
        for candidate in (b"//", self._prefix, b"/agent/"):
            if path.startswith(candidate):
                return True, True
        for candidate in self._page_paths:
            if path == candidate and closing or any(path.startswith(candidate + suffix) for suffix in (b"/", b"?", b"#")):
                return True, True
        candidates = (b"//", self._prefix, b"/agent/", *self._page_paths)
        if not closing and any(candidate.startswith(path) for candidate in candidates):
            return False, False
        return True, False

    def _rewrite(self, data: bytes) -> bytes:
        output = bytearray()
        cursor = 0
        while cursor < len(data):
            if self._state == "value":
                closing = data.find(bytes([self._quote]), cursor)
                if closing == -1:
                    output.extend(data[cursor:])
                    break
                output.extend(data[cursor:closing + 1])
                self._state = "name"
                cursor = closing + 1
                continue
            if self._state == "name" and not self._name:
                start = self._NAME_START.search(data, cursor)
                if start is None:
                    output.extend(data[cursor:])
                    break
                output.extend(data[cursor:start.start()])
                cursor = start.start()
            byte = data[cursor]
            cursor += 1
            if self._state == "path":
                closing = byte == self._quote
                if not closing:
                    self._probe.append(byte)
                decided, exempt = self._path(closing)
                if decided:
                    if not exempt:
                        output.extend(self._prefix)
                    output.extend(self._probe)
                    self._probe.clear()
                    self._state = "name" if closing else "value"
                    if closing:
                        output.append(byte)
                continue
            if self._state == "equals":
                if byte in b" \t\r\n\f":
                    output.append(byte)
                    continue
                if byte == ord("="):
                    output.append(byte)
                    self._state = "quote"
                    continue
                self._state = "name"
            elif self._state == "quote":
                if byte in b" \t\r\n\f":
                    output.append(byte)
                    continue
                if byte in (ord('"'), ord("'")):
                    output.append(byte)
                    self._quote = byte
                    self._state = "path"
                    continue
                self._state = "name"
            self._name.append(byte)
            while self._name and not any(name.startswith(bytes(self._name).lower()) for name in self._NAMES):
                output.append(self._name.pop(0))
            if bytes(self._name).lower() in self._NAMES:
                output.extend(self._name)
                self._name.clear()
                self._state = "equals"
        return bytes(output)

    def feed(self, data: bytes) -> bytes:
        if self._finished:
            raise ValueError("Agent HTML stream already ended")
        if len(data) > 48 * 1024:
            raise ValueError("Agent HTML chunk exceeds the native body bound")
        rewritten = self._rewrite(data)
        if self._injected:
            return rewritten
        needed = self.PREFIX_BYTES - len(self._head)
        self._head.extend(rewritten[:needed])
        if len(self._head) < self.PREFIX_BYTES:
            return b""
        output = self._inject(bytes(self._head), self.prefix) + rewritten[needed:]
        self._head.clear()
        self._injected = True
        return output

    def finish(self) -> bytes:
        if self._finished:
            raise ValueError("Agent HTML stream already ended")
        self._finished = True
        tail = bytes(self._probe) + bytes(self._name)
        self._probe.clear()
        self._name.clear()
        if self._injected:
            return tail
        output = self._inject(bytes(self._head) + tail, self.prefix)
        self._head.clear()
        return output
