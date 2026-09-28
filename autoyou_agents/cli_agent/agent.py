# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-ad6daa96cd911c6106b23835

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-ad6daa96cd911c6106b23835"


import asyncio
import codecs
import ctypes
from ctypes import wintypes
import io
import os
import re
import select
import shlex
import shutil
import struct
import subprocess
import threading
import time
import uuid
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from google.adk.agents import Agent

from autoyou_agents.admin_agent.agent import (
    _get_internal_ai_agent_api_token,
    _get_saved_reply_target as _resolve_saved_reply_target,
    _http,
    _state_get,
    _state_set,
)
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime, inject_realtime_datetime_into_request
from shared.adk_state import (
    AUTOYOU_OWNER_KEY_STATE_KEY,
    AUTOYOU_OWNER_KEY_USER_STATE_KEY,
    normalize_reply_target,
)
from shared.session_execution import (
    SESSION_CONTROL_STATE_KEY,
    create_text_llm_response,
    create_tool_call_llm_response,
)

from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME

if os.name == "nt":
    fcntl = None  # type: ignore[assignment]
    pty = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]
else:
    import fcntl
    import pty
    import termios

try:
    from wcwidth import wcwidth
except Exception:  # pragma: no cover - available in runtime, but keep a safe fallback
    def wcwidth(character: str) -> int:
        return 1 if character else 0


_CLI_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY = "_autoyou_cli_tool_dispatch_invocation_id"
_CLI_TOOL_RESULT_INVOCATION_ID_STATE_KEY = "_autoyou_cli_tool_result_invocation_id"
_CLI_TOOL_RESULT_MESSAGE_STATE_KEY = "_autoyou_cli_tool_result_message"

_DEFAULT_TERMINAL_COLS = 120
_DEFAULT_TERMINAL_ROWS = 40
_STREAM_DEBOUNCE_SECONDS = 0.25
_INPUT_SETTLE_SECONDS = 0.2
_READ_CHUNK_SIZE = 4096
_READ_HISTORY_LIMIT = 12000
_SHELL_IDLE_WAIT_SECONDS = 1.5
_CONPTY_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_CONPTY_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CONPTY_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_CONPTY_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CONPTY_CREATE_NO_WINDOW = 0x08000000
_WINDOWS_INFINITE = 0xFFFFFFFF
_STARTF_USESHOWWINDOW = 0x00000001
_SW_HIDE = 0

_CLI_RESERVED_PREFIX = "/cli"
_CLI_RESERVED_PATTERN = re.compile(r"^\s*/cli(?:\s+(.*))?\s*$", re.IGNORECASE)
_CLI_GREETINGS = {"hi", "hello", "hey", "help"}
_CLI_DIRECT_KEYWORDS = (
    "terminal",
    "shell",
    "command prompt",
    "powershell",
    "cmd",
    "cli",
)
_CLI_KEY_MAP = {
    "backspace": "\x08",
    "ctrl+c": "\x03",
    "ctrl+d": "\x04",
    "ctrl+z": "\x1a",
    "delete": "\x1b[3~",
    "down": "\x1b[B",
    "end": "\x1b[F",
    "enter": "\r",
    "esc": "\x1b",
    "escape": "\x1b",
    "home": "\x1b[H",
    "left": "\x1b[D",
    "pagedown": "\x1b[6~",
    "pageup": "\x1b[5~",
    "return": "\r",
    "right": "\x1b[C",
    "tab": "\t",
    "up": "\x1b[A",
}
_POSIX_INTERACTIVE_SHELLS = frozenset({"bash", "csh", "fish", "ksh", "sh", "tcsh", "zsh"})


def _shell_command_for_name(shell_name: Optional[str]) -> list[str]:
    raw_shell_name = str(shell_name or "").strip()
    normalized = raw_shell_name.lower()
    if normalized in {"", "cmd", "cmd.exe", "command prompt"}:
        return ["cmd.exe", "/Q", "/K", "prompt AutoYouCLI$G"]
    if normalized in {"powershell", "powershell.exe"}:
        executable = "powershell.exe" if os.name == "nt" else "pwsh"
        return [
            executable,
            "-NoLogo",
            "-NoExit",
            "-NoProfile",
            "-Command",
            '$Host.UI.RawUI.WindowTitle = "AutoYou CLI"',
        ]
    if normalized in {"pwsh", "pwsh.exe"}:
        return [
            "pwsh.exe" if os.name == "nt" else "pwsh",
            "-NoLogo",
            "-NoExit",
            "-NoProfile",
            "-Command",
            '$Host.UI.RawUI.WindowTitle = "AutoYou CLI"',
        ]
    if os.name != "nt":
        parsed = [part for part in shlex.split(raw_shell_name) if part]
        if not parsed:
            return ["/bin/sh", "-i"]
        if len(parsed) == 1:
            shell_basename = Path(parsed[0]).name.lower()
            if shell_basename in _POSIX_INTERACTIVE_SHELLS:
                return [parsed[0], "-i"]
        return parsed
    return [raw_shell_name]


def _default_shell_name() -> str:
    configured = str(os.getenv("AUTOYOU_CLI_SHELL", "") or "").strip()
    if configured:
        return configured
    if os.name != "nt":
        shell_env = str(os.getenv("SHELL", "") or "").strip()
        if shell_env:
            return shell_env
        for fallback in ("/bin/zsh", "/bin/bash", "/bin/sh"):
            if os.path.exists(fallback):
                return fallback
        return "sh"
    return "powershell"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_node_executable() -> Optional[str]:
    explicit = str(os.getenv("AUTOYOU_NODE_PATH", "") or "").strip()
    if explicit:
        return explicit

    from_path = shutil.which("node")
    if from_path:
        return from_path

    try:
        from shared.platform_runtime import find_bundled_node_executable

        bundled = find_bundled_node_executable(__file__)
        if bundled is not None:
            return str(bundled)
    except Exception:
        pass

    repo_root = _repo_root()
    candidates = (
        repo_root / "servers" / "windows" / "artifacts" / "node-runtime" / "node.exe",
        repo_root / "servers" / "windows" / "artifacts" / "backend" / "AutoYouServer" / "runtime" / "node" / "node.exe",
        repo_root / "servers" / "windows" / "dist" / "AutoYou-win-x64" / "Backend" / "runtime" / "node" / "node.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _resolve_node_pty_package_path() -> Optional[Path]:
    explicit = str(os.getenv("AUTOYOU_CLI_NODE_PTY_PACKAGE_PATH", "") or "").strip()
    if explicit:
        explicit_path = Path(explicit)
        if explicit_path.is_dir():
            return explicit_path

    repo_root = _repo_root()
    candidates = (
        repo_root / "node_modules" / "@lydell" / "node-pty",
        repo_root / "openclaw" / "openclaw" / "node_modules" / "@lydell" / "node-pty",
    )
    for candidate in candidates:
        if (candidate / "package.json").is_file():
            return candidate
    return None


def _extract_text_from_llm_request(llm_request: Any) -> str:
    for content in reversed(getattr(llm_request, "contents", []) or []):
        if str(getattr(content, "role", "") or "").strip().lower() not in {"", "user"}:
            continue
        parts = []
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            return " ".join(parts).strip()
    return ""


def _get_invocation_id(context: Any) -> str:
    return str(getattr(context, "invocation_id", "") or "").strip()


def _tool_dispatch_already_happened(state: Any, invocation_id: str) -> bool:
    if not invocation_id:
        return False
    return str(_state_get(state, _CLI_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY) or "").strip() == invocation_id


def _mark_tool_dispatch(state: Any, invocation_id: str) -> None:
    if not invocation_id:
        return
    _state_set(state, _CLI_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY, invocation_id)
    _state_set(state, _CLI_TOOL_RESULT_INVOCATION_ID_STATE_KEY, "")
    _state_set(state, _CLI_TOOL_RESULT_MESSAGE_STATE_KEY, "")


def _record_cli_tool_result(state: Any, invocation_id: str, message: str) -> None:
    normalized_message = str(message or "").strip()
    if not invocation_id or not normalized_message:
        return
    _state_set(state, _CLI_TOOL_RESULT_INVOCATION_ID_STATE_KEY, invocation_id)
    _state_set(state, _CLI_TOOL_RESULT_MESSAGE_STATE_KEY, normalized_message)


def _get_recorded_cli_tool_result(state: Any, invocation_id: str) -> str:
    if not invocation_id:
        return ""
    result_invocation_id = str(_state_get(state, _CLI_TOOL_RESULT_INVOCATION_ID_STATE_KEY) or "").strip()
    if result_invocation_id != invocation_id:
        return ""
    return str(_state_get(state, _CLI_TOOL_RESULT_MESSAGE_STATE_KEY) or "").strip()


def _extract_owner_key(tool_context_or_state: Any) -> str:
    owner_key = str(
        _state_get(
            tool_context_or_state,
            AUTOYOU_OWNER_KEY_USER_STATE_KEY,
            AUTOYOU_OWNER_KEY_STATE_KEY,
        )
        or ""
    ).strip()
    if owner_key:
        return owner_key
    session_control = _state_get(tool_context_or_state, SESSION_CONTROL_STATE_KEY)
    if isinstance(session_control, dict):
        owner_key = str(session_control.get("owner_key") or "").strip()
        if owner_key:
            return owner_key
        canonical_user_id = str(session_control.get("canonical_user_id") or "").strip()
        if canonical_user_id.startswith("user::"):
            return canonical_user_id[len("user::") :].strip()
    return ""


def _fallback_webrtc_reply_target(
    tool_context: Optional[Any],
    *,
    explicit_owner_key: Optional[str] = None,
    explicit_session_control: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    owner_key = str(explicit_owner_key or "").strip()
    if not owner_key and isinstance(explicit_session_control, dict):
        owner_key = str(explicit_session_control.get("owner_key") or "").strip()
        if not owner_key:
            canonical_user_id = str(explicit_session_control.get("canonical_user_id") or "").strip()
            if canonical_user_id.startswith("user::"):
                owner_key = canonical_user_id[len("user::") :].strip()
    if owner_key:
        return {"transport": "webrtc", "owner_key": owner_key}

    owner_key = _extract_owner_key(tool_context)
    if not owner_key:
        return None
    return {"transport": "webrtc", "owner_key": owner_key}


def _explicit_cli_context_args(state: Any) -> Dict[str, Any]:
    args: Dict[str, Any] = {}
    reply_target = _resolve_saved_reply_target(state)
    if reply_target and str(reply_target.get("transport") or "").strip().lower() == "webrtc":
        args["reply_target"] = dict(reply_target)

    session_control = _state_get(state, SESSION_CONTROL_STATE_KEY)
    if isinstance(session_control, dict):
        compact_session_control = {
            key: str(session_control.get(key) or "").strip()
            for key in ("owner_key", "canonical_user_id", "canonical_session_id")
            if str(session_control.get(key) or "").strip()
        }
        if compact_session_control:
            args["session_control"] = compact_session_control

    owner_key = _extract_owner_key(state)
    if owner_key:
        args["owner_key"] = owner_key

    if "reply_target" not in args:
        fallback_reply_target = _fallback_webrtc_reply_target(
            state,
            explicit_owner_key=args.get("owner_key"),
            explicit_session_control=args.get("session_control"),
        )
        if fallback_reply_target:
            args["reply_target"] = fallback_reply_target

    return args


def _resolve_cli_binding(
    tool_context: Optional[Any],
    *,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = getattr(tool_context, "state", None) if tool_context is not None else None
    normalized_reply_target = normalize_reply_target(reply_target) or _resolve_saved_reply_target(state)
    normalized_session_control = session_control
    if not isinstance(normalized_session_control, dict):
        stored_session_control = _state_get(tool_context, SESSION_CONTROL_STATE_KEY)
        normalized_session_control = stored_session_control if isinstance(stored_session_control, dict) else None

    resolved_owner_key = str(owner_key or "").strip()
    if not resolved_owner_key:
        resolved_owner_key = _extract_owner_key(tool_context)

    if not normalized_reply_target:
        normalized_reply_target = _fallback_webrtc_reply_target(
            tool_context,
            explicit_owner_key=resolved_owner_key,
            explicit_session_control=normalized_session_control,
        )

    session_key = ""
    if isinstance(normalized_session_control, dict):
        session_key = str(normalized_session_control.get("canonical_session_id") or "").strip()
    if not session_key and normalized_reply_target:
        session_key = str(normalized_reply_target.get("session_id") or "").strip()
    if not session_key:
        session_key = resolved_owner_key or "local-cli-session"

    return {
        "session_key": session_key,
        "reply_target": normalized_reply_target,
        "owner_key": resolved_owner_key,
        "session_control": normalized_session_control,
        "stream_capable": bool(
            normalized_reply_target
            and str(normalized_reply_target.get("transport") or "").strip().lower() == "webrtc"
            and (
                str(normalized_reply_target.get("session_id") or "").strip()
                or str(normalized_reply_target.get("owner_key") or "").strip()
            )
        ),
    }


class _AnsiScreenBuffer:
    def __init__(self, cols: int, rows: int):
        self.cols = max(40, int(cols or _DEFAULT_TERMINAL_COLS))
        self.rows = max(10, int(rows or _DEFAULT_TERMINAL_ROWS))
        self._main_buffer = self._blank_buffer()
        self._alt_buffer = self._blank_buffer()
        self._use_alt_buffer = False
        self._cursor_row = 0
        self._cursor_col = 0
        self._saved_cursor = (0, 0)
        self._escape_state = ""
        self._escape_buffer = ""

    def _blank_buffer(self) -> list[list[str]]:
        return [[" " for _ in range(self.cols)] for _ in range(self.rows)]

    def _buffer(self) -> list[list[str]]:
        return self._alt_buffer if self._use_alt_buffer else self._main_buffer

    def _reset_current_buffer(self) -> None:
        buffer = self._buffer()
        for row in range(self.rows):
            for col in range(self.cols):
                buffer[row][col] = " "
        self._cursor_row = 0
        self._cursor_col = 0

    def _linefeed(self) -> None:
        self._cursor_row += 1
        if self._cursor_row < self.rows:
            return
        buffer = self._buffer()
        buffer.pop(0)
        buffer.append([" " for _ in range(self.cols)])
        self._cursor_row = self.rows - 1

    def _put_char(self, character: str) -> None:
        if not character:
            return
        width = wcwidth(character)
        if width < 0:
            width = 1
        if width == 0:
            if self._cursor_col > 0:
                current = self._buffer()[self._cursor_row][self._cursor_col - 1]
                self._buffer()[self._cursor_row][self._cursor_col - 1] = f"{current}{character}"
            return
        if self._cursor_col >= self.cols:
            self._cursor_col = 0
            self._linefeed()
        buffer = self._buffer()
        buffer[self._cursor_row][self._cursor_col] = character
        if width == 2 and self._cursor_col + 1 < self.cols:
            buffer[self._cursor_row][self._cursor_col + 1] = ""
        self._cursor_col = min(self.cols, self._cursor_col + width)

    def _erase_display(self, mode: int) -> None:
        buffer = self._buffer()
        if mode == 2:
            self._reset_current_buffer()
            return
        if mode == 1:
            for row_index in range(self._cursor_row + 1):
                max_col = self.cols if row_index < self._cursor_row else self._cursor_col + 1
                for col_index in range(max_col):
                    buffer[row_index][col_index] = " "
            return
        for row_index in range(self._cursor_row, self.rows):
            min_col = self._cursor_col if row_index == self._cursor_row else 0
            for col_index in range(min_col, self.cols):
                buffer[row_index][col_index] = " "

    def _erase_line(self, mode: int) -> None:
        buffer = self._buffer()
        if mode == 2:
            for col_index in range(self.cols):
                buffer[self._cursor_row][col_index] = " "
            return
        if mode == 1:
            for col_index in range(self._cursor_col + 1):
                buffer[self._cursor_row][col_index] = " "
            return
        for col_index in range(self._cursor_col, self.cols):
            buffer[self._cursor_row][col_index] = " "

    def _handle_csi(self, sequence: str) -> None:
        if not sequence:
            return
        final = sequence[-1]
        parameters = sequence[:-1]
        private = parameters.startswith("?")
        if private:
            parameters = parameters[1:]
        parts = [0]
        if parameters:
            parsed_parts = []
            for raw_value in parameters.split(";"):
                try:
                    parsed_parts.append(int(raw_value) if raw_value else 0)
                except Exception:
                    parsed_parts.append(0)
            parts = parsed_parts or [0]

        def _value(index: int, default: int) -> int:
            try:
                return int(parts[index] or default)
            except Exception:
                return default

        if private and final in {"h", "l"} and parts[:1] == [1049]:
            self._use_alt_buffer = final == "h"
            if self._use_alt_buffer:
                self._alt_buffer = self._blank_buffer()
            self._cursor_row = 0
            self._cursor_col = 0
            return

        if final == "A":
            self._cursor_row = max(0, self._cursor_row - _value(0, 1))
        elif final == "B":
            self._cursor_row = min(self.rows - 1, self._cursor_row + _value(0, 1))
        elif final == "C":
            self._cursor_col = min(self.cols - 1, self._cursor_col + _value(0, 1))
        elif final == "D":
            self._cursor_col = max(0, self._cursor_col - _value(0, 1))
        elif final in {"H", "f"}:
            row = max(1, _value(0, 1))
            col = max(1, _value(1, 1))
            self._cursor_row = min(self.rows - 1, row - 1)
            self._cursor_col = min(self.cols - 1, col - 1)
        elif final == "G":
            col = max(1, _value(0, 1))
            self._cursor_col = min(self.cols - 1, col - 1)
        elif final == "d":
            row = max(1, _value(0, 1))
            self._cursor_row = min(self.rows - 1, row - 1)
        elif final == "J":
            self._erase_display(_value(0, 0))
        elif final == "K":
            self._erase_line(_value(0, 0))
        elif final == "P":
            count = max(1, _value(0, 1))
            buffer = self._buffer()[self._cursor_row]
            for _ in range(count):
                if self._cursor_col < self.cols:
                    buffer.pop(self._cursor_col)
                    buffer.append(" ")
        elif final == "@":
            count = max(1, _value(0, 1))
            buffer = self._buffer()[self._cursor_row]
            for _ in range(count):
                buffer.insert(self._cursor_col, " ")
                buffer.pop()
        elif final == "X":
            count = max(1, _value(0, 1))
            buffer = self._buffer()[self._cursor_row]
            for offset in range(count):
                target_col = self._cursor_col + offset
                if target_col >= self.cols:
                    break
                buffer[target_col] = " "
        elif final == "L":
            count = max(1, _value(0, 1))
            buffer = self._buffer()
            for _ in range(count):
                buffer.insert(self._cursor_row, [" " for _ in range(self.cols)])
                buffer.pop()
        elif final == "M":
            count = max(1, _value(0, 1))
            buffer = self._buffer()
            for _ in range(count):
                buffer.pop(self._cursor_row)
                buffer.append([" " for _ in range(self.cols)])
        elif final == "s":
            self._saved_cursor = (self._cursor_row, self._cursor_col)
        elif final == "u":
            self._cursor_row, self._cursor_col = self._saved_cursor

    def feed(self, text: str) -> None:
        for character in str(text or ""):
            if not self._escape_state:
                if character == "\x1b":
                    self._escape_state = "esc"
                    self._escape_buffer = ""
                    continue
                if character == "\r":
                    self._cursor_col = 0
                    continue
                if character == "\n":
                    self._cursor_col = 0
                    self._linefeed()
                    continue
                if character == "\b":
                    self._cursor_col = max(0, self._cursor_col - 1)
                    continue
                if character == "\t":
                    next_stop = ((self._cursor_col // 8) + 1) * 8
                    while self._cursor_col < min(self.cols, next_stop):
                        self._put_char(" ")
                    continue
                if character == "\x07":
                    continue
                if character >= " ":
                    self._put_char(character)
                continue

            if self._escape_state == "esc":
                if character == "[":
                    self._escape_state = "csi"
                    self._escape_buffer = ""
                    continue
                if character == "]":
                    self._escape_state = "osc"
                    self._escape_buffer = ""
                    continue
                if character == "7":
                    self._saved_cursor = (self._cursor_row, self._cursor_col)
                elif character == "8":
                    self._cursor_row, self._cursor_col = self._saved_cursor
                elif character == "c":
                    self._reset_current_buffer()
                self._escape_state = ""
                self._escape_buffer = ""
                continue

            if self._escape_state == "osc":
                if character == "\x07":
                    self._escape_state = ""
                    self._escape_buffer = ""
                    continue
                if self._escape_buffer.endswith("\x1b") and character == "\\":
                    self._escape_state = ""
                    self._escape_buffer = ""
                    continue
                self._escape_buffer += character
                continue

            if self._escape_state == "csi":
                self._escape_buffer += character
                if "@" <= character <= "~":
                    self._handle_csi(self._escape_buffer)
                    self._escape_state = ""
                    self._escape_buffer = ""

    def render(self) -> str:
        lines = []
        for row in self._buffer():
            line = "".join(part for part in row if part != "")
            lines.append(line.rstrip())
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines).strip()


class _PipeTerminalBackend:
    backend_name = "pipes"

    def __init__(self, command: list[str], *, cwd: Optional[str] = None):
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            command,
            cwd=cwd or None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            creationflags=creationflags,
        )

    def read(self, size: int = _READ_CHUNK_SIZE) -> bytes:
        if self._process.stdout is None:
            return b""
        try:
            return os.read(self._process.stdout.fileno(), size)
        except Exception:
            return b""

    def write(self, data: bytes) -> int:
        if self._process.stdin is None:
            return 0
        self._process.stdin.write(data)
        self._process.stdin.flush()
        return len(data)

    def is_alive(self) -> bool:
        return self._process.poll() is None

    def close(self) -> None:
        if self._process.stdin is not None:
            with suppress(Exception):
                self._process.stdin.close()
        if self._process.poll() is None:
            with suppress(Exception):
                self._process.terminate()
            with suppress(Exception):
                self._process.wait(timeout=2)
        if self._process.poll() is None:
            with suppress(Exception):
                self._process.kill()
        if self._process.stdout is not None:
            with suppress(Exception):
                self._process.stdout.close()


class _PosixPtyTerminalBackend:
    backend_name = "posix-pty"

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Optional[str] = None,
        cols: int = _DEFAULT_TERMINAL_COLS,
        rows: int = _DEFAULT_TERMINAL_ROWS,
    ):
        self._master_fd: Optional[int] = None
        self._slave_fd: Optional[int] = None
        if os.name == "nt":
            raise OSError("POSIX PTY backend is only available on Unix-like hosts.")

        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        self._slave_fd = slave_fd
        self._set_window_size(cols=cols, rows=rows)

        env = dict(os.environ)
        env.setdefault("TERM", "xterm-256color")
        try:
            self._process = subprocess.Popen(
                command,
                cwd=cwd or None,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                close_fds=True,
                start_new_session=True,
            )
        except Exception:
            self._close_fd(slave_fd)
            self._close_fd(master_fd)
            self._slave_fd = None
            self._master_fd = None
            raise

        self._close_fd(slave_fd)
        self._slave_fd = None

    def _close_fd(self, fd: Optional[int]) -> None:
        if fd is None:
            return
        with suppress(Exception):
            os.close(fd)

    def _set_window_size(self, *, cols: int, rows: int) -> None:
        if self._slave_fd is None:
            return
        packed = struct.pack("HHHH", max(10, int(rows)), max(40, int(cols)), 0, 0)
        with suppress(Exception):
            fcntl.ioctl(self._slave_fd, termios.TIOCSWINSZ, packed)

    def read(self, size: int = _READ_CHUNK_SIZE) -> bytes:
        if self._master_fd is None:
            return b""
        try:
            ready, _, _ = select.select([self._master_fd], [], [], 0.2)
            if not ready:
                return b"\x00"
            return os.read(self._master_fd, size)
        except OSError:
            return b""

    def write(self, data: bytes) -> int:
        raw = bytes(data or b"")
        if not raw or self._master_fd is None:
            return 0
        return int(os.write(self._master_fd, raw))

    def is_alive(self) -> bool:
        return self._process.poll() is None

    def close(self) -> None:
        if self._process.poll() is None:
            with suppress(Exception):
                self._process.terminate()
            with suppress(Exception):
                self._process.wait(timeout=2)
        if self._process.poll() is None:
            with suppress(Exception):
                self._process.kill()
        self._close_fd(self._master_fd)
        self._close_fd(self._slave_fd)
        self._master_fd = None
        self._slave_fd = None


class _NodePtyTerminalBackend:
    backend_name = "node-pty"

    def __init__(self, command: list[str], *, cwd: Optional[str] = None, cols: int = _DEFAULT_TERMINAL_COLS, rows: int = _DEFAULT_TERMINAL_ROWS):
        node_executable = _resolve_node_executable()
        package_path = _resolve_node_pty_package_path()
        helper_path = Path(__file__).with_name("pty_bridge.cjs")
        if not node_executable:
            raise FileNotFoundError("Node.js executable was not found for cli_agent PTY backend.")
        if package_path is None:
            raise FileNotFoundError("node-pty package was not found for cli_agent PTY backend.")
        if not helper_path.is_file():
            raise FileNotFoundError(f"PTY helper script is missing: {helper_path}")

        env = dict(os.environ)
        env["AUTOYOU_CLI_NODE_PTY_PACKAGE_PATH"] = str(package_path)
        if cwd:
            env["AUTOYOU_CLI_PTY_CWD"] = str(cwd)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self._process = subprocess.Popen(
            [node_executable, str(helper_path), str(int(cols)), str(int(rows)), *command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=env,
            creationflags=creationflags,
        )

    def read(self, size: int = _READ_CHUNK_SIZE) -> bytes:
        if self._process.stdout is None:
            return b""
        try:
            return os.read(self._process.stdout.fileno(), size)
        except Exception:
            return b""

    def write(self, data: bytes) -> int:
        raw = bytes(data or b"")
        if not raw or self._process.stdin is None:
            return 0
        self._process.stdin.write(raw)
        self._process.stdin.flush()
        return len(raw)

    def is_alive(self) -> bool:
        return self._process.poll() is None

    def close(self) -> None:
        if self._process.stdin is not None:
            with suppress(Exception):
                self._process.stdin.close()
        if self._process.poll() is None:
            with suppress(Exception):
                self._process.terminate()
            with suppress(Exception):
                self._process.wait(timeout=2)
        if self._process.poll() is None:
            with suppress(Exception):
                self._process.kill()
        if self._process.stdout is not None:
            with suppress(Exception):
                self._process.stdout.close()


class _ConPtyCoord(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _ConPtyStartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("lpReserved", ctypes.c_void_p),
        ("lpDesktop", ctypes.c_void_p),
        ("lpTitle", ctypes.c_void_p),
        ("dwX", ctypes.c_uint32),
        ("dwY", ctypes.c_uint32),
        ("dwXSize", ctypes.c_uint32),
        ("dwYSize", ctypes.c_uint32),
        ("dwXCountChars", ctypes.c_uint32),
        ("dwYCountChars", ctypes.c_uint32),
        ("dwFillAttribute", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("wShowWindow", ctypes.c_uint16),
        ("cbReserved2", ctypes.c_uint16),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", ctypes.c_void_p),
        ("hStdOutput", ctypes.c_void_p),
        ("hStdError", ctypes.c_void_p),
    ]


class _ConPtyStartupInfoEx(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _ConPtyStartupInfo),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _ConPtyProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", ctypes.c_void_p),
        ("hThread", ctypes.c_void_p),
        ("dwProcessId", ctypes.c_uint32),
        ("dwThreadId", ctypes.c_uint32),
    ]


def _configure_conpty_kernel32(kernel32: Any) -> None:
    handle_ptr = ctypes.POINTER(wintypes.HANDLE)
    dword_ptr = ctypes.POINTER(wintypes.DWORD)
    size_t_ptr = ctypes.POINTER(ctypes.c_size_t)

    kernel32.CreatePipe.argtypes = [
        handle_ptr,
        handle_ptr,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.CreatePipe.restype = wintypes.BOOL

    kernel32.CreatePseudoConsole.argtypes = [
        _ConPtyCoord,
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    kernel32.CreatePseudoConsole.restype = ctypes.c_long

    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        size_t_ptr,
    ]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL

    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        size_t_ptr,
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL

    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_ConPtyStartupInfo),
        ctypes.POINTER(_ConPtyProcessInformation),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        dword_ptr,
        ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL

    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        dword_ptr,
        ctypes.c_void_p,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL

    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, dword_ptr]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL

    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL

    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD

    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None

    kernel32.ClosePseudoConsole.argtypes = [ctypes.c_void_p]
    kernel32.ClosePseudoConsole.restype = None


class _ConPtyTerminalBackend:
    backend_name = "conpty"

    def __init__(self, command: list[str], *, cwd: Optional[str] = None, cols: int = _DEFAULT_TERMINAL_COLS, rows: int = _DEFAULT_TERMINAL_ROWS):
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _configure_conpty_kernel32(self._kernel32)
        self._hpc = ctypes.c_void_p()
        self._input_read = ctypes.c_void_p()
        self._input_write = ctypes.c_void_p()
        self._output_read = ctypes.c_void_p()
        self._output_write = ctypes.c_void_p()
        self._attribute_list_buffer: Optional[ctypes.Array[Any]] = None
        self._startupinfo = _ConPtyStartupInfoEx()
        self._process_info = _ConPtyProcessInformation()
        self._command_line_buffer = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        self._cwd_buffer = ctypes.create_unicode_buffer(str(cwd)) if cwd else None

        self._create_pipes()
        self._create_pseudoconsole(cols=cols, rows=rows)
        self._initialize_attribute_list()
        self._spawn_child()
        self._close_unused_handles_after_spawn()

    @staticmethod
    def _creation_flags() -> int:
        # CREATE_NO_WINDOW disables the console attachment that ConPTY needs.
        # The child can remain alive while producing no bytes on the output
        # pipe, which makes the session look stuck at "waiting for terminal
        # output". EXTENDED_STARTUPINFO_PRESENT is the flag that connects the
        # child to the pseudoconsole.
        return (
            _CONPTY_EXTENDED_STARTUPINFO_PRESENT
            | _CONPTY_CREATE_UNICODE_ENVIRONMENT
            | _CONPTY_CREATE_NEW_PROCESS_GROUP
        )

    def _bool(self, result: Any, function_name: str) -> None:
        if result:
            return
        raise OSError(f"{function_name} failed with Win32 error {ctypes.get_last_error()}")

    def _create_pipes(self) -> None:
        self._bool(
            self._kernel32.CreatePipe(
                ctypes.byref(self._input_read),
                ctypes.byref(self._input_write),
                None,
                0,
            ),
            "CreatePipe(input)",
        )
        self._bool(
            self._kernel32.CreatePipe(
                ctypes.byref(self._output_read),
                ctypes.byref(self._output_write),
                None,
                0,
            ),
            "CreatePipe(output)",
        )

    def _create_pseudoconsole(self, *, cols: int, rows: int) -> None:
        size = _ConPtyCoord(max(40, int(cols)), max(10, int(rows)))
        result = self._kernel32.CreatePseudoConsole(
            size,
            self._input_read,
            self._output_write,
            0,
            ctypes.byref(self._hpc),
        )
        if int(result) != 0:
            raise OSError(f"CreatePseudoConsole failed with HRESULT 0x{int(result) & 0xFFFFFFFF:08X}")

    def _initialize_attribute_list(self) -> None:
        size = ctypes.c_size_t()
        self._kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        self._attribute_list_buffer = ctypes.create_string_buffer(size.value)
        self._startupinfo.lpAttributeList = ctypes.cast(self._attribute_list_buffer, ctypes.c_void_p)
        self._bool(
            self._kernel32.InitializeProcThreadAttributeList(
                self._startupinfo.lpAttributeList,
                1,
                0,
                ctypes.byref(size),
            ),
            "InitializeProcThreadAttributeList",
        )
        self._bool(
            self._kernel32.UpdateProcThreadAttribute(
                self._startupinfo.lpAttributeList,
                0,
                _CONPTY_ATTRIBUTE_PSEUDOCONSOLE,
                self._hpc,
                ctypes.sizeof(self._hpc),
                None,
                None,
            ),
            "UpdateProcThreadAttribute",
        )
        self._startupinfo.StartupInfo.cb = ctypes.sizeof(_ConPtyStartupInfoEx)
        self._startupinfo.StartupInfo.dwFlags = _STARTF_USESHOWWINDOW
        self._startupinfo.StartupInfo.wShowWindow = _SW_HIDE

    def _spawn_child(self) -> None:
        creation_flags = self._creation_flags()
        self._bool(
            self._kernel32.CreateProcessW(
                None,
                self._command_line_buffer,
                None,
                None,
                False,
                creation_flags,
                None,
                self._cwd_buffer,
                ctypes.byref(self._startupinfo.StartupInfo),
                ctypes.byref(self._process_info),
            ),
            "CreateProcessW",
        )

    def _close_unused_handles_after_spawn(self) -> None:
        for handle in (self._input_read, self._output_write, self._process_info.hThread):
            if handle:
                with suppress(Exception):
                    self._kernel32.CloseHandle(handle)

    def read(self, size: int = _READ_CHUNK_SIZE) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_uint32()
        ok = self._kernel32.ReadFile(
            self._output_read,
            buffer,
            size,
            ctypes.byref(bytes_read),
            None,
        )
        if not ok or not bytes_read.value:
            return b""
        return bytes(buffer.raw[: bytes_read.value])

    def write(self, data: bytes) -> int:
        raw = bytes(data or b"")
        if not raw:
            return 0
        bytes_written = ctypes.c_uint32()
        self._bool(
            self._kernel32.WriteFile(
                self._input_write,
                raw,
                len(raw),
                ctypes.byref(bytes_written),
                None,
            ),
            "WriteFile",
        )
        return int(bytes_written.value)

    def is_alive(self) -> bool:
        if not self._process_info.hProcess:
            return False
        exit_code = ctypes.c_uint32()
        if not self._kernel32.GetExitCodeProcess(self._process_info.hProcess, ctypes.byref(exit_code)):
            return False
        return int(exit_code.value) == 259

    def close(self) -> None:
        if self._process_info.hProcess and self.is_alive():
            with suppress(Exception):
                self._kernel32.TerminateProcess(self._process_info.hProcess, 0)
            with suppress(Exception):
                self._kernel32.WaitForSingleObject(self._process_info.hProcess, 2000)
        if self._startupinfo.lpAttributeList:
            with suppress(Exception):
                self._kernel32.DeleteProcThreadAttributeList(self._startupinfo.lpAttributeList)
        if self._hpc:
            with suppress(Exception):
                self._kernel32.ClosePseudoConsole(self._hpc)
        for handle in (
            self._input_write,
            self._output_read,
            self._process_info.hProcess,
        ):
            if handle:
                with suppress(Exception):
                    self._kernel32.CloseHandle(handle)


def _create_terminal_backend(
    command: list[str],
    *,
    cwd: Optional[str] = None,
    cols: int = _DEFAULT_TERMINAL_COLS,
    rows: int = _DEFAULT_TERMINAL_ROWS,
):
    if os.name == "nt":
        try:
            return _NodePtyTerminalBackend(command, cwd=cwd, cols=cols, rows=rows)
        except Exception:
            pass
        # The compiled Windows bundle does not currently ship node-pty. The
        # native fallback is intentionally opt-in because this implementation
        # can leave the child alive while routing its prompt to the host
        # console instead of the pipe. That produces a false "waiting for
        # terminal output" state in chat. Pipes are less capable than a PTY,
        # but they reliably support ordinary shell commands and are preferable
        # to a session that cannot return any output.
        if str(os.getenv("AUTOYOU_CLI_ENABLE_CONPTY", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
            try:
                return _ConPtyTerminalBackend(command, cwd=cwd, cols=cols, rows=rows)
            except Exception:
                pass
    else:
        try:
            return _PosixPtyTerminalBackend(command, cwd=cwd, cols=cols, rows=rows)
        except Exception:
            pass
    return _PipeTerminalBackend(command, cwd=cwd)


class _CliSession:
    def __init__(
        self,
        session_key: str,
        *,
        reply_target: Optional[Dict[str, Any]],
        shell_name: str,
        cols: int,
        rows: int,
        cwd: Optional[str] = None,
        conversation_session_id: Optional[str] = None,
    ):
        self.session_key = str(session_key or "").strip() or f"cli-{uuid.uuid4().hex}"
        self.shell_name = shell_name
        self.command = _shell_command_for_name(shell_name)
        self.reply_target = normalize_reply_target(reply_target)
        self.conversation_session_id: Optional[str] = str(conversation_session_id or "").strip() or None
        self.stream_message_id = str(uuid.uuid4())
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.closed_at = 0.0
        self.closed_reason = ""
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._dirty_event = threading.Event()
        self._screen = _AnsiScreenBuffer(cols=cols, rows=rows)
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._recent_text = deque(maxlen=_READ_HISTORY_LIMIT)
        self._backend = _create_terminal_backend(self.command, cwd=cwd, cols=cols, rows=rows)
        self.backend_name = str(getattr(self._backend, "backend_name", "unknown") or "unknown")
        self._last_stream_payload = ""
        self._last_stream_sent_at = 0.0
        self._reader_thread = threading.Thread(target=self._reader_loop, name=f"cli-reader-{self.session_key}", daemon=True)
        self._stream_thread = threading.Thread(target=self._stream_loop, name=f"cli-stream-{self.session_key}", daemon=True)
        self._reader_thread.start()
        self._stream_thread.start()

    def _append_recent_text(self, text: str) -> None:
        if not text:
            return
        self._recent_text.extend(text)

    def _reader_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                chunk = self._backend.read(_READ_CHUNK_SIZE)
                if chunk == b"\x00":
                    continue
                if not chunk:
                    break
                decoded = self._decoder.decode(chunk)
                if not decoded:
                    continue
                with self._lock:
                    self._screen.feed(decoded)
                    self._append_recent_text(decoded)
                    self.updated_at = time.time()
                self._dirty_event.set()
        except Exception as exc:
            with self._lock:
                self.closed_reason = f"Terminal reader stopped: {exc}"
        finally:
            with self._lock:
                if not self.closed_at:
                    self.closed_at = time.time()
                if not self.closed_reason:
                    self.closed_reason = "Terminal process exited."
            self._stop_event.set()
            self._dirty_event.set()

    def _stream_loop(self) -> None:
        while not self._stop_event.is_set():
            self._dirty_event.wait(timeout=_STREAM_DEBOUNCE_SECONDS)
            self._dirty_event.clear()
            if not self._should_stream():
                continue
            now = time.time()
            if now - self._last_stream_sent_at < _STREAM_DEBOUNCE_SECONDS:
                time.sleep(_STREAM_DEBOUNCE_SECONDS)
            self._push_stream_snapshot(final=False)

        if self._should_stream():
            self._push_stream_snapshot(final=True)

    def _should_stream(self) -> bool:
        with self._lock:
            reply_target = self.reply_target
        return bool(
            reply_target
            and str(reply_target.get("transport") or "").strip().lower() == "webrtc"
            and (
                str(reply_target.get("session_id") or "").strip()
                or str(reply_target.get("owner_key") or "").strip()
            )
            and _get_internal_ai_agent_api_token()
        )

    def _build_snapshot(self) -> str:
        with self._lock:
            screen_text = self._screen.render()
            alive = self.is_alive()
        header = f"CLI {'active' if alive else 'closed'} [{self.shell_name}] via {self.backend_name}"
        if screen_text:
            return f"{header}\n{screen_text}".strip()
        if alive:
            return f"{header}\n(waiting for terminal output)".strip()
        return f"{header}\n(terminal closed)".strip()

    def _push_stream_snapshot(self, *, final: bool) -> None:
        token = _get_internal_ai_agent_api_token()
        if not token:
            return
        with self._lock:
            reply_target = dict(self.reply_target or {})
        if not reply_target:
            return
        payload_message = self._build_snapshot()
        if not final and payload_message == self._last_stream_payload:
            return

        metadata: Dict[str, Any] = {
            "source": "cli_agent",
            "response_author": AGENT_NAME,
            "agent_name": AGENT_NAME,
            "original_message_id": self.stream_message_id,
            "is_streaming": not final,
            "cli_session_key": self.session_key,
        }
        with self._lock:
            conv_session_id = self.conversation_session_id
        if conv_session_id:
            metadata["conversation_session_id"] = conv_session_id
        request_payload = {
            "message": payload_message,
            "metadata": metadata,
        }
        session_id = str(reply_target.get("session_id") or "").strip()
        owner_key = str(reply_target.get("owner_key") or "").strip()
        if session_id:
            request_payload["session_id"] = session_id
        if owner_key:
            request_payload["owner_key"] = owner_key
        if "session_id" not in request_payload and "owner_key" not in request_payload:
            return
        _http("POST", "/api/webrtc/send", request_payload, timeout=15, token=token)
        self._last_stream_payload = payload_message
        self._last_stream_sent_at = time.time()

    def update_reply_target(self, reply_target: Optional[Dict[str, Any]]) -> None:
        normalized = normalize_reply_target(reply_target)
        if not normalized:
            return
        with self._lock:
            self.reply_target = normalized
        self._dirty_event.set()

    def update_conversation_session_id(self, conversation_session_id: Optional[str]) -> None:
        normalized = str(conversation_session_id or "").strip() or None
        if normalized:
            with self._lock:
                self.conversation_session_id = normalized

    def is_alive(self) -> bool:
        with self._lock:
            if self.closed_at:
                return False
        return bool(self._backend.is_alive())

    def write_text(self, text: str, *, append_enter: bool = True) -> None:
        normalized = str(text or "")
        if append_enter:
            normalized += "\r"
        self._backend.write(normalized.encode("utf-8", errors="replace"))
        with self._lock:
            self.updated_at = time.time()
        self._dirty_event.set()

    def write_keys(self, keys: Iterable[str]) -> None:
        encoded = []
        for raw_key in keys:
            normalized_key = str(raw_key or "").strip().lower()
            if not normalized_key:
                continue
            sequence = _CLI_KEY_MAP.get(normalized_key)
            if sequence is None:
                raise ValueError(f"Unsupported CLI key: {raw_key}")
            encoded.append(sequence)
        if not encoded:
            return
        self._backend.write("".join(encoded).encode("utf-8", errors="replace"))
        with self._lock:
            self.updated_at = time.time()
        self._dirty_event.set()

    def wait_for_quiet(self, timeout_seconds: float = _SHELL_IDLE_WAIT_SECONDS) -> None:
        deadline = time.time() + max(0.1, float(timeout_seconds))
        while time.time() < deadline:
            if not self.is_alive():
                return
            if not self._dirty_event.is_set():
                time.sleep(0.05)
                return
            time.sleep(0.05)

    def wait_for_initial_output(self, timeout_seconds: float = 1.0) -> None:
        deadline = time.time() + max(0.1, float(timeout_seconds))
        while time.time() < deadline:
            if not self.is_alive():
                return
            if self._dirty_event.is_set():
                self.wait_for_quiet(timeout_seconds=max(0.1, deadline - time.time()))
                return
            time.sleep(0.05)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            screen_text = self._screen.render()
            recent_text = "".join(self._recent_text).strip()
            closed_reason = self.closed_reason
        return {
            "status": "success",
            "session_key": self.session_key,
            "shell": self.shell_name,
            "backend": self.backend_name,
            "alive": self.is_alive(),
            "snapshot": self._build_snapshot(),
            "screen_text": screen_text,
            "recent_text": recent_text[-4000:],
            "reply_target": dict(self.reply_target or {}),
            "closed_reason": closed_reason,
        }

    def close(self) -> None:
        if self.is_alive():
            with suppress(Exception):
                self.write_text("exit", append_enter=True)
                time.sleep(0.3)
        self._stop_event.set()
        self._dirty_event.set()
        with suppress(Exception):
            self._backend.close()
        with self._lock:
            if not self.closed_at:
                self.closed_at = time.time()
            if not self.closed_reason:
                self.closed_reason = "CLI session closed."


class _CliSessionManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: Dict[str, _CliSession] = {}

    def _get_existing(self, session_key: str) -> Optional[_CliSession]:
        with self._lock:
            session = self._sessions.get(str(session_key or "").strip())
            if session and not session.is_alive():
                return session
            return session

    def start_or_attach(
        self,
        session_key: str,
        *,
        reply_target: Optional[Dict[str, Any]],
        shell_name: str,
        cols: int,
        rows: int,
        conversation_session_id: Optional[str] = None,
    ) -> tuple[_CliSession, bool]:
        normalized_key = str(session_key or "").strip() or f"cli-{uuid.uuid4().hex}"
        with self._lock:
            existing = self._sessions.get(normalized_key)
            if existing and existing.is_alive():
                existing.update_reply_target(reply_target)
                existing.update_conversation_session_id(conversation_session_id)
                return existing, False
            if existing:
                with suppress(Exception):
                    existing.close()
            session = _CliSession(
                normalized_key,
                reply_target=reply_target,
                shell_name=shell_name,
                cols=cols,
                rows=rows,
                conversation_session_id=conversation_session_id,
            )
            self._sessions[normalized_key] = session
            return session, True

    def get(self, session_key: str) -> Optional[_CliSession]:
        normalized_key = str(session_key or "").strip()
        with self._lock:
            return self._sessions.get(normalized_key)

    def has_live_session(self, session_key: str) -> bool:
        session = self.get(session_key)
        return bool(session and session.is_alive())

    def close(self, session_key: str) -> Optional[_CliSession]:
        normalized_key = str(session_key or "").strip()
        with self._lock:
            session = self._sessions.get(normalized_key)
        if session is None:
            return None
        with suppress(Exception):
            session.close()
        return session


_CLI_SESSIONS = _CliSessionManager()


def _render_cli_tool_response(tool_name: str, tool_response: Dict[str, Any]) -> str:
    message = str(tool_response.get("message") or "").strip()
    snapshot = str(tool_response.get("snapshot") or "").strip()
    if message and snapshot:
        return f"{message}\n\n{snapshot}".strip()
    if snapshot:
        return snapshot
    if message:
        return message
    if tool_name == "get_cli_session_status":
        return "CLI session status fetched."
    return "CLI action completed."


def _session_from_binding(
    tool_context: Optional[Any],
    *,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
    create_if_missing: bool = False,
    shell_name: Optional[str] = None,
    cols: int = _DEFAULT_TERMINAL_COLS,
    rows: int = _DEFAULT_TERMINAL_ROWS,
) -> tuple[Optional[_CliSession], Dict[str, Any], bool]:
    binding = _resolve_cli_binding(
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
    )
    session_key = str(binding.get("session_key") or "").strip()
    binding_session_control = binding.get("session_control") or {}
    canonical_session_id: Optional[str] = str(binding_session_control.get("canonical_session_id") or "").strip() or None
    if create_if_missing:
        session, created = _CLI_SESSIONS.start_or_attach(
            session_key,
            reply_target=binding.get("reply_target"),
            shell_name=str(shell_name or _default_shell_name()),
            cols=cols,
            rows=rows,
            conversation_session_id=canonical_session_id,
        )
        return session, binding, created
    session = _CLI_SESSIONS.get(session_key)
    if session and binding.get("reply_target"):
        session.update_reply_target(binding["reply_target"])
    if session and canonical_session_id:
        session.update_conversation_session_id(canonical_session_id)
    return session, binding, False


def _normalize_key_list(keys: str) -> list[str]:
    key_text = str(keys or "").strip()
    if not key_text:
        return []
    raw_parts = re.split(r"[\s,]+", key_text)
    normalized_parts = []
    index = 0
    while index < len(raw_parts):
        part = str(raw_parts[index] or "").strip()
        if not part:
            index += 1
            continue
        if part.lower() == "ctrl" and index + 1 < len(raw_parts):
            normalized_parts.append(f"ctrl+{raw_parts[index + 1].strip().lower()}")
            index += 2
            continue
        normalized_parts.append(part.lower())
        index += 1
    return normalized_parts


def start_cli_session(
    tool_context: Optional[Any] = None,
    initial_input: Optional[str] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
    shell_name: Optional[str] = None,
    cols: int = _DEFAULT_TERMINAL_COLS,
    rows: int = _DEFAULT_TERMINAL_ROWS,
) -> Dict[str, Any]:
    session, binding, created = _session_from_binding(
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        create_if_missing=True,
        shell_name=shell_name or _default_shell_name(),
        cols=cols,
        rows=rows,
    )
    if session is None:
        return {"status": "error", "message": "Could not start a CLI session."}

    if initial_input not in (None, ""):
        if created and hasattr(session, "wait_for_initial_output"):
            session.wait_for_initial_output()
        session.write_text(str(initial_input), append_enter=True)
        session.wait_for_quiet()

    snapshot = session.snapshot()
    action_text = "CLI session started." if created else "CLI session already active."
    if initial_input not in (None, ""):
        action_text += f" Sent: {str(initial_input).strip()}"
    snapshot["message"] = action_text
    snapshot["stream_capable"] = bool(binding.get("stream_capable"))
    return snapshot


def send_cli_input(
    text: str,
    tool_context: Optional[Any] = None,
    append_enter: bool = True,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    session, _, created = _session_from_binding(
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        create_if_missing=True,
        shell_name=_default_shell_name(),
    )
    if session is None:
        return {"status": "error", "message": "No CLI session is available."}
    session.write_text(text, append_enter=bool(append_enter))
    time.sleep(_INPUT_SETTLE_SECONDS)
    session.wait_for_quiet()
    snapshot = session.snapshot()
    if created:
        snapshot["message"] = f"CLI session started and input sent: {str(text).strip()}"
    else:
        snapshot["message"] = f"Sent input to CLI session: {str(text).strip()}"
    return snapshot


def send_cli_keys(
    keys: str,
    tool_context: Optional[Any] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    session, _, _ = _session_from_binding(
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        create_if_missing=False,
    )
    if session is None or not session.is_alive():
        return {"status": "error", "message": "No active CLI session is available. Start one first."}
    normalized_keys = _normalize_key_list(keys)
    if not normalized_keys:
        return {"status": "error", "message": "No CLI key sequence was provided."}
    session.write_keys(normalized_keys)
    time.sleep(_INPUT_SETTLE_SECONDS)
    session.wait_for_quiet()
    snapshot = session.snapshot()
    snapshot["message"] = f"Sent CLI key sequence: {', '.join(normalized_keys)}"
    return snapshot


def interrupt_cli_session(
    tool_context: Optional[Any] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return send_cli_keys(
        "ctrl+c",
        tool_context=tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
    )


def read_cli_session(
    tool_context: Optional[Any] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    session, _, _ = _session_from_binding(
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        create_if_missing=False,
    )
    if session is None:
        return {"status": "error", "message": "No CLI session has been started for this conversation."}
    snapshot = session.snapshot()
    snapshot["message"] = "CLI snapshot fetched."
    return snapshot


def get_cli_session_status(
    tool_context: Optional[Any] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    session, binding, _ = _session_from_binding(
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        create_if_missing=False,
    )
    if session is None:
        return {
            "status": "success",
            "session_active": False,
            "message": "No CLI session is active.",
            "session_key": str(binding.get("session_key") or ""),
            "stream_capable": bool(binding.get("stream_capable")),
        }
    snapshot = session.snapshot()
    snapshot["session_active"] = bool(snapshot.get("alive"))
    snapshot["message"] = "CLI session is active." if snapshot.get("alive") else "CLI session has exited."
    return snapshot


def exit_cli_session(
    tool_context: Optional[Any] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    session, binding, _ = _session_from_binding(
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        create_if_missing=False,
    )
    if session is None:
        return {
            "status": "success",
            "message": "No CLI session was active.",
            "session_key": str(binding.get("session_key") or ""),
        }
    session.close()
    snapshot = session.snapshot()
    snapshot["message"] = "CLI session closed. You are back in CLI-agent chat mode."
    return snapshot


def _parse_reserved_cli_command(user_text: str) -> Optional[Dict[str, Any]]:
    match = _CLI_RESERVED_PATTERN.match(str(user_text or ""))
    if not match:
        return None
    tail = str(match.group(1) or "").strip()
    if not tail:
        return {"command": "help"}
    parts = tail.split(None, 1)
    command = str(parts[0] or "").strip().lower()
    remainder = str(parts[1] or "").strip() if len(parts) > 1 else ""
    return {"command": command, "value": remainder}


def _reserved_cli_help_text() -> str:
    return (
        "CLI agent ready.\n"
        "Use plain chat messages as terminal commands while a session is active.\n"
        "Reserved controls:\n"
        "/cli start\n"
        "/cli status\n"
        "/cli read\n"
        "/cli key ctrl+c\n"
        "/cli key up\n"
        "/cli raw <text>\n"
        "/cli exit"
    )


def _cli_after_tool_callback(tool: Any, args: Dict[str, Any], tool_context: Any, tool_response: Any) -> Any:
    del args
    tool_name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "").strip()
    if not tool_name or not isinstance(tool_response, dict):
        return None
    invocation_id = _get_invocation_id(tool_context)
    _record_cli_tool_result(
        tool_context.state,
        invocation_id,
        _render_cli_tool_response(tool_name, tool_response),
    )
    return None


async def _cli_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    inject_realtime_datetime_into_request(llm_request)
    user_text = _extract_text_from_llm_request(llm_request)
    if not user_text:
        return None

    invocation_id = _get_invocation_id(callback_context)
    if _tool_dispatch_already_happened(callback_context.state, invocation_id):
        result_message = _get_recorded_cli_tool_result(callback_context.state, invocation_id)
        if result_message:
            return create_text_llm_response(
                result_message,
                custom_metadata={
                    "response_author": AGENT_NAME,
                    "cli_deterministic_reply": True,
                },
            )
        return None

    context_args = _explicit_cli_context_args(callback_context.state)
    binding = _resolve_cli_binding(
        callback_context,
        reply_target=context_args.get("reply_target"),
        owner_key=context_args.get("owner_key"),
        session_control=context_args.get("session_control"),
    )
    session_key = str(binding.get("session_key") or "").strip()
    session_active = _CLI_SESSIONS.has_live_session(session_key)
    reserved = _parse_reserved_cli_command(user_text)

    if reserved:
        command = str(reserved.get("command") or "").strip().lower()
        value = str(reserved.get("value") or "").strip()
        if command in {"help", "?"}:
            return create_text_llm_response(
                _reserved_cli_help_text(),
                custom_metadata={"response_author": AGENT_NAME},
            )

        tool_name = ""
        tool_args = dict(context_args)
        if command in {"start", "open"}:
            tool_name = "start_cli_session"
            if value:
                tool_args["initial_input"] = value
        elif command in {"status", "state"}:
            tool_name = "get_cli_session_status"
        elif command in {"read", "screen", "snapshot"}:
            tool_name = "read_cli_session"
        elif command in {"exit", "quit", "close"}:
            tool_name = "exit_cli_session"
        elif command in {"interrupt", "stop"}:
            tool_name = "interrupt_cli_session"
        elif command == "key":
            tool_name = "send_cli_keys"
            tool_args["keys"] = value
        elif command in {"raw", "type", "paste"}:
            tool_name = "send_cli_input"
            tool_args["text"] = value
            tool_args["append_enter"] = False
        else:
            return create_text_llm_response(
                _reserved_cli_help_text(),
                custom_metadata={"response_author": AGENT_NAME},
            )

        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            tool_name,
            tool_args,
            custom_metadata={"response_author": AGENT_NAME},
        )

    normalized_text = " ".join(str(user_text or "").split()).strip()
    lowered_text = normalized_text.lower()

    if not session_active and lowered_text in _CLI_GREETINGS:
        return create_text_llm_response(
            _reserved_cli_help_text(),
            custom_metadata={"response_author": AGENT_NAME},
        )

    if session_active:
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "send_cli_input",
            {
                "text": user_text,
                "append_enter": True,
                **context_args,
            },
            custom_metadata={"response_author": AGENT_NAME},
        )

    looks_like_terminal_request = any(keyword in lowered_text for keyword in _CLI_DIRECT_KEYWORDS)
    initial_input = None
    if normalized_text and not looks_like_terminal_request:
        initial_input = user_text

    _mark_tool_dispatch(callback_context.state, invocation_id)
    return create_tool_call_llm_response(
        "start_cli_session",
        {
            "initial_input": initial_input,
            **context_args,
        },
        custom_metadata={"response_author": AGENT_NAME},
    )


def create_cli_agent(model_config: Any) -> Agent:
    tools = [
        start_cli_session,
        send_cli_input,
        send_cli_keys,
        interrupt_cli_session,
        read_cli_session,
        get_cli_session_status,
        exit_cli_session,
        get_current_datetime,
    ]

    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        before_model_callback=[_cli_before_model_callback],
        after_tool_callback=[_cli_after_tool_callback],
        tools=tools,
    )
