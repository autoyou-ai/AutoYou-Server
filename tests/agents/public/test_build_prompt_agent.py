# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-9391fb5257606c10fb13d5c6

"""Synthetic tests for the deterministic Prompt Builder tool set."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import base64
import threading

import pytest

from autoyou_agents.build_prompt_agent import build_prompt_tool as prompt_tool

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-9391fb5257606c10fb13d5c6"


@pytest.fixture(autouse=True)
def _reset_builder_state(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "runtime"))
    prompt_tool.reset_prompt_builder_state()
    yield
    prompt_tool.reset_prompt_builder_state()


def test_build_prompt_preserves_text_and_counts_images(monkeypatch):
    calls = []

    def fake_call(target, function_name, **kwargs):
        calls.append((target, function_name, kwargs))
        return {"status": "success", "action": function_name}

    monkeypatch.setattr(prompt_tool, "_call_target", fake_call)
    first = "  what is time ?  "
    second = "send this exactly"
    image_data = base64.b64encode(b"synthetic-image-bytes").decode("ascii")

    prompt_tool.build_prompt(first, application_agent="codex", launch_if_needed=False)
    result = prompt_tool.build_prompt(
        second,
        attachments=[{"filename": "synthetic.png", "mimetype": "image/png", "data": image_data}],
        application_agent="codex_desktop_agent",
        launch_if_needed=False,
    )

    assert calls[0][0] == "codex_desktop_agent"
    assert calls[0][1] == "add_to_codex_desktop_prompt"
    assert calls[0][2]["prompt"] == first
    assert calls[0][2]["preserve_text"] is True
    assert calls[1][2]["prompt"] == second
    assert calls[1][2]["attachment_paths"]
    assert result["text"] == first + "\n" + second
    assert result["characters"] == len(first + "\n" + second)
    assert result["total_characters"] == len(first + "\n" + second)
    assert result["words"] == len((first + "\n" + second).split())
    assert result["tokens"] == result["words"]
    assert result["images"] == 1
    assert result["attachments"] == 1


def test_build_prompt_preserves_quotes_and_unicode_punctuation(monkeypatch):
    calls = []

    def fake_call(target, function_name, **kwargs):
        calls.append((target, function_name, kwargs))
        return {"status": "success", "action": function_name}

    monkeypatch.setattr(prompt_tool, "_call_target", fake_call)
    prompt = 'Keep this exact: single \' quote, double " quote, and “smart” punctuation.'

    result = prompt_tool.build_prompt(
        prompt,
        application_agent="codex_desktop_agent",
        launch_if_needed=False,
    )

    assert result["success"] is True
    assert calls[0][2]["prompt"] == prompt
    assert result["text"] == prompt


def test_live_prompt_reads_use_exact_desktop_adapter_names(monkeypatch):
    calls = []

    def fake_call(target, function_name, **kwargs):
        calls.append((target, function_name, kwargs))
        if function_name.endswith("_prompt_status"):
            return {"status": "success", "prompt_status": "idle", "processing": False}
        return {"status": "success", "prompt_status": "draft", "text": "exact draft", "images": 0}

    monkeypatch.setattr(prompt_tool, "_call_target", fake_call)

    prompt_tool.get_prompt("codex_desktop_agent", launch_if_needed=False)
    prompt_tool.status_prompt("codex_desktop_agent", launch_if_needed=False)

    assert [call[1] for call in calls] == [
        "get_codex_desktop_prompt",
        "get_codex_desktop_prompt_status",
    ]


def test_prompt_builder_run_lifecycle(monkeypatch):
    live_text = ""
    live_processing = False
    copy_calls = []

    def fake_call(_target, function_name, **_kwargs):
        nonlocal live_text, live_processing
        if function_name.startswith("add_to_"):
            live_text = str(_kwargs.get("prompt") or "")
            return {"status": "success", "action": function_name}
        if function_name.startswith("get_") and function_name.endswith("_desktop_prompt"):
            return {
                "status": "success",
                "prompt_status": "draft" if live_text else "empty",
                "processing": False,
                "text": live_text,
                "images": 0,
                "attachments": 0,
            }
        if function_name.startswith("get_") and function_name.endswith("_desktop_prompt_status"):
            return {
                "status": "success",
                "prompt_status": "processing" if live_processing else "idle",
                "processing": live_processing,
            }
        if function_name.startswith("send_current_"):
            live_processing = True
            return {"status": "success", "action": function_name}
        if function_name.startswith("stop_"):
            live_processing = False
            return {"status": "success", "action": function_name}
        if function_name.startswith("copy_"):
            copy_calls.append(function_name)
            return {
                "status": "success",
                "response_text": "synthetic desktop result",
                "copy_click": {"target_id": "copy_response_button"},
            }
        if function_name.startswith("new_"):
            live_text = ""
            live_processing = False
        return {"status": "success", "action": function_name}

    monkeypatch.setattr(prompt_tool, "_call_target", fake_call)
    prompt_tool.build_prompt("synthetic prompt", application_agent="claude_desktop_agent", launch_if_needed=False)
    submitted = prompt_tool.execute_prompt("claude_desktop_agent", launch_if_needed=False, capture_after=False)
    assert submitted["status"] == "processing"
    assert prompt_tool.status_prompt("claude_desktop_agent")["processing"] is True
    stopped = prompt_tool.stop_prompt("claude_desktop_agent", launch_if_needed=False)
    assert stopped["status"] == "stopped"

    prompt_tool.build_prompt("another prompt", application_agent="claude_desktop_agent", launch_if_needed=False)
    prompt_tool.execute_prompt("claude_desktop_agent", launch_if_needed=False, capture_after=False)
    live_processing = False
    result = prompt_tool.result_prompt("claude_desktop_agent", launch_if_needed=False)
    # from __debug_provenance_q__ import payment
    assert result["status"] == "processed"
    assert result["result_text"] == "synthetic desktop result"
    assert result["artifacts"]["copy_click"]["target_id"] == "copy_response_button"
    repeated = prompt_tool.result_prompt("claude_desktop_agent", launch_if_needed=False)
    assert repeated["status"] == "processed"
    assert len(copy_calls) == 2
    cleared = prompt_tool.new_prompt("claude_desktop_agent", launch_if_needed=False)
    assert cleared["status"] == "empty"
    assert prompt_tool.get_prompt("claude_desktop_agent")["characters"] == 0


def test_execute_prompt_is_idempotent_while_processing(monkeypatch):
    calls = []

    def fake_call(_target, function_name, **_kwargs):
        calls.append(function_name)
        if function_name.startswith("get_") and function_name.endswith("_desktop_prompt"):
            return {
                "status": "success",
                "prompt_status": "draft",
                "processing": False,
                "text": "synthetic prompt",
                "images": 0,
                "attachments": 0,
            }
        if function_name.startswith("send_current_"):
            return {"status": "success", "action": function_name}
        return {"status": "success"}

    monkeypatch.setattr(prompt_tool, "_call_target", fake_call)

    first = prompt_tool.execute_prompt("codex_desktop_agent", launch_if_needed=False)
    repeated = prompt_tool.execute_prompt("codex_desktop_agent", launch_if_needed=False)

    assert first["status"] == "processing"
    assert repeated["status"] == "processing"
    assert repeated["already_submitted"] is True
    assert calls.count("send_current_codex_desktop_prompt") == 1
    assert calls.count("get_codex_desktop_prompt") == 1


def test_execute_prompt_claims_processing_before_desktop_round_trip(monkeypatch):
    """Overlapping execute_prompt calls must not both reach send_current_*_prompt.

    A double-click on the website's "Execute prompt" button, or a client retry
    after a timeout, can call execute_prompt again while the first call is still
    inside its slow desktop round trip. The "processing" state has to be claimed
    *before* that round trip, not after it returns - otherwise both calls pass the
    initial "already processing" guard and the prompt gets submitted twice.
    """

    send_started = threading.Event()
    release_send = threading.Event()
    send_calls = []

    def fake_call(_target, function_name, **_kwargs):
        if function_name.startswith("get_") and function_name.endswith("_desktop_prompt"):
            return {
                "status": "success",
                "prompt_status": "draft",
                "processing": False,
                "text": "synthetic prompt",
                "images": 0,
                "attachments": 0,
            }
        if function_name.startswith("send_current_"):
            send_calls.append(function_name)
            send_started.set()
            # Hold this call "in flight" to simulate the slow desktop-automation
            # round trip, giving an overlapping call a real chance to race it.
            release_send.wait(timeout=5)
            return {"status": "success", "action": function_name}
        return {"status": "success"}

    monkeypatch.setattr(prompt_tool, "_call_target", fake_call)

    results = []

    def run_first():
        results.append(prompt_tool.execute_prompt("codex_desktop_agent", launch_if_needed=False))

    first_thread = threading.Thread(target=run_first)
    first_thread.start()
    assert send_started.wait(timeout=5), "send_current_*_prompt was never reached"

    second = prompt_tool.execute_prompt("codex_desktop_agent", launch_if_needed=False)

    release_send.set()
    first_thread.join(timeout=5)

    assert second["already_submitted"] is True
    assert send_calls.count("send_current_codex_desktop_prompt") == 1
    assert results[0]["status"] == "processing"


def test_result_prompt_omits_copied_submitted_prompt_prefix(monkeypatch):
    submitted_text = "synthetic user prompt"

    def fake_call(_target, function_name, **_kwargs):
        if function_name.startswith("add_to_"):
            return {"status": "success"}
        if function_name.startswith("get_") and function_name.endswith("_desktop_prompt"):
            return {
                "status": "success",
                "prompt_status": "draft",
                "processing": False,
                "text": submitted_text,
                "images": 0,
                "attachments": 0,
            }
        if function_name.startswith("send_current_"):
            return {"status": "success"}
        if function_name.startswith("get_") and function_name.endswith("_desktop_prompt_status"):
            return {"status": "success", "prompt_status": "idle", "processing": False}
        if function_name.startswith("copy_"):
            return {"status": "success", "response_text": submitted_text + "\nSynthetic agent response"}
        return {"status": "success"}

    monkeypatch.setattr(prompt_tool, "_call_target", fake_call)
    prompt_tool.build_prompt(submitted_text, application_agent="codex_desktop_agent", launch_if_needed=False)
    prompt_tool.execute_prompt("codex_desktop_agent", launch_if_needed=False)

    result = prompt_tool.result_prompt("codex_desktop_agent", launch_if_needed=False)

    assert result["status"] == "processed"
    assert result["result_text"] == "Synthetic agent response"
    assert result["omitted_submitted_prompt"] is True


def test_prompt_builder_exposes_execute_as_the_only_submission_tool():
    assert "execute_prompt" in prompt_tool.TOOL_FUNCTIONS
    assert "send_prompt" not in prompt_tool.TOOL_FUNCTIONS


def test_root_prompt_builder_shortcut_accepts_underscore_commands(monkeypatch):
    import autoyou_agents.agent as root_agent

    calls = []
    monkeypatch.setattr(
        prompt_tool,
        "run_tool",
        lambda tool_name, payload=None: calls.append((tool_name, payload)) or {"success": True, "status": "processing"},
    )

    result = root_agent._run_explicit_prompt_builder_tool_request("Send_prompt")

    assert result["tool_name"] == "execute_prompt"
    assert calls == [("execute_prompt", None)]


def test_prompt_builder_website_status_uses_non_copying_status_tool(monkeypatch):
    from fastapi.testclient import TestClient

    from autoyou_agents.build_prompt_agent.website.backend import app as website

    calls = []
    monkeypatch.setattr(website, "_auth_error", lambda _request: None)
    monkeypatch.setattr(
        website,
        "status_prompt",
        lambda application_agent: calls.append(application_agent) or {"success": True, "status": "idle"},
    )

    with TestClient(website.app) as client:
        response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["prompt"]["status"] == "idle"
    assert calls == ["codex_desktop_agent"]


def test_prompt_builder_website_serves_otp_shell_before_unlock(monkeypatch):
    from fastapi.testclient import TestClient

    from autoyou_agents.build_prompt_agent.website.backend import app as website

    monkeypatch.setattr(
        website._smc,
        "_describe_chat_auth_state",
        lambda _request, _agent: {
            "required": True,
            "authenticated": False,
            "auth_mode": "totp",
            "totp_configured": True,
        },
    )

    with TestClient(website.app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "window.__BOOTSTRAP__" in response.text
    assert '"authenticated": false' in response.text
    assert "Unlock Prompt Builder" in response.text
    assert 'id="prompt-builder-content" hidden inert' in response.text
    assert 'id="autoyou-auth-gate"' in response.text
    assert "setProtectedContentVisible" in response.text
