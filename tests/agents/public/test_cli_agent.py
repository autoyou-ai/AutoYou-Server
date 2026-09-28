# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-719d0a7117f752e4a393277d


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-719d0a7117f752e4a393277d"

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from google.genai import types

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from autoyou_agents.cli_agent import agent as cli_agent


def _llm_request(text: str):
    return SimpleNamespace(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part(text=text)],
            )
        ]
    )


class _FakeSession:
    def __init__(self):
        self.text_writes = []
        self.key_writes = []
        self.closed = False
        self.reply_target_updates = []

    def write_text(self, text: str, *, append_enter: bool = True) -> None:
        self.text_writes.append((text, append_enter))

    def wait_for_quiet(self, timeout_seconds: float = cli_agent._SHELL_IDLE_WAIT_SECONDS) -> None:
        del timeout_seconds

    def write_keys(self, keys):
        self.key_writes.append(list(keys))

    def snapshot(self):
        return {
            "status": "success",
            "session_key": "cli-test-session",
            "shell": "powershell",
            "backend": "node-pty",
            "alive": not self.closed,
            "snapshot": "CLI active [powershell] via node-pty\nPS>",
            "screen_text": "PS>",
            "recent_text": "PS>",
            "reply_target": {},
            "closed_reason": "CLI session closed." if self.closed else "",
        }

    def close(self):
        self.closed = True

    def update_reply_target(self, reply_target):
        self.reply_target_updates.append(dict(reply_target or {}))

    def is_alive(self):
        return not self.closed


def test_ansi_screen_buffer_wraps_after_last_column():
    buffer = cli_agent._AnsiScreenBuffer(cols=40, rows=10)
    buffer.feed("A" * 41)

    lines = buffer.render().splitlines()

    assert lines[0] == "A" * 40
    assert lines[1] == "A"


def test_conpty_creation_flags_keep_console_attachment_enabled():
    creation_flags = cli_agent._ConPtyTerminalBackend._creation_flags()

    assert creation_flags & cli_agent._CONPTY_EXTENDED_STARTUPINFO_PRESENT
    assert not (creation_flags & cli_agent._CONPTY_CREATE_NO_WINDOW)


def test_windows_uses_pipe_fallback_when_pty_backends_are_unavailable(monkeypatch):
    calls = []

    class _FakePipeBackend:
        pass

    def _fail_node_pty(*args, **kwargs):
        raise FileNotFoundError("node-pty is not bundled")

    def _fake_pipe(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakePipeBackend()

    monkeypatch.setattr(cli_agent.os, "name", "nt")
    monkeypatch.delenv("AUTOYOU_CLI_ENABLE_CONPTY", raising=False)
    monkeypatch.setattr(cli_agent, "_NodePtyTerminalBackend", _fail_node_pty)
    monkeypatch.setattr(cli_agent, "_PipeTerminalBackend", _fake_pipe)

    backend = cli_agent._create_terminal_backend(["cmd.exe"])

    assert isinstance(backend, _FakePipeBackend)
    assert calls


def test_start_cli_session_returns_snapshot_and_marks_stream_capable(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(
        cli_agent,
        "_session_from_binding",
        lambda *args, **kwargs: (
            fake_session,
            {"stream_capable": True, "session_key": "cli-test-session"},
            True,
        ),
    )

    result = cli_agent.start_cli_session(initial_input="dir")

    assert result["status"] == "success"
    assert result["stream_capable"] is True
    assert "CLI session started." in result["message"]
    assert "Sent: dir" in result["message"]
    assert fake_session.text_writes == [("dir", True)]


def test_exit_cli_session_closes_existing_session(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(
        cli_agent,
        "_session_from_binding",
        lambda *args, **kwargs: (
            fake_session,
            {"session_key": "cli-test-session"},
            False,
        ),
    )

    result = cli_agent.exit_cli_session()

    assert result["status"] == "success"
    assert fake_session.closed is True
    assert result["message"] == "CLI session closed. You are back in CLI-agent chat mode."


def test_cli_before_model_callback_starts_session_for_plain_command(monkeypatch):
    monkeypatch.setattr(cli_agent, "_CLI_SESSIONS", SimpleNamespace(has_live_session=lambda session_key: False))

    response = asyncio.run(
        cli_agent._cli_before_model_callback(
            SimpleNamespace(state={}, invocation_id="cli-start"),
            _llm_request("dir"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert function_call.name == "start_cli_session"
    assert function_call.args["initial_input"] == "dir"


def test_cli_before_model_callback_routes_follow_up_text_into_active_session(monkeypatch):
    monkeypatch.setattr(cli_agent, "_CLI_SESSIONS", SimpleNamespace(has_live_session=lambda session_key: True))

    response = asyncio.run(
        cli_agent._cli_before_model_callback(
            SimpleNamespace(state={}, invocation_id="cli-follow-up"),
            _llm_request("Get-Location"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert function_call.name == "send_cli_input"
    assert function_call.args["text"] == "Get-Location"
    assert function_call.args["append_enter"] is True


def test_cli_before_model_callback_supports_reserved_key_command(monkeypatch):
    monkeypatch.setattr(cli_agent, "_CLI_SESSIONS", SimpleNamespace(has_live_session=lambda session_key: True))

    response = asyncio.run(
        cli_agent._cli_before_model_callback(
            SimpleNamespace(state={}, invocation_id="cli-key"),
            _llm_request("/cli key ctrl+c"),
        )
    )

    function_call = response.content.parts[0].function_call
    assert function_call is not None
    assert function_call.name == "send_cli_keys"
    assert function_call.args["keys"] == "ctrl+c"


def test_cli_before_model_callback_replays_recorded_tool_result():
    state = {}
    cli_agent._mark_tool_dispatch(state, "cli-tool-result")
    cli_agent._record_cli_tool_result(
        state,
        "cli-tool-result",
        "CLI snapshot fetched.\n\nCLI active [powershell] via node-pty\nPS>",
    )

    response = asyncio.run(
        cli_agent._cli_before_model_callback(
            SimpleNamespace(state=state, invocation_id="cli-tool-result"),
            _llm_request("show the terminal"),
        )
    )

    assert response.content.parts[0].text == "CLI snapshot fetched.\n\nCLI active [powershell] via node-pty\nPS>"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell defaults only apply on Unix-like hosts")
def test_shell_command_for_name_uses_interactive_posix_shell(monkeypatch):
    monkeypatch.setattr(cli_agent.os, "name", "posix")

    assert cli_agent._shell_command_for_name("/bin/zsh") == ["/bin/zsh", "-i"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX PTY backend only applies on Unix-like hosts")
def test_start_cli_session_captures_initial_output_on_posix(monkeypatch):
    shell_path = "/bin/sh" if os.path.exists("/bin/sh") else (os.environ.get("SHELL") or "sh")
    monkeypatch.setenv("AUTOYOU_CLI_SHELL", shell_path)

    result = cli_agent.start_cli_session(initial_input="echo hello-cli-test")

    try:
        assert result["status"] == "success"
        assert result["backend"] == "posix-pty"
        assert "hello-cli-test" in result["snapshot"]
    finally:
        cli_agent.exit_cli_session()
