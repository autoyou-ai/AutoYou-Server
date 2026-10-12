# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-158020bc1fa8e8bfcf567aa0


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import asyncio
from types import SimpleNamespace

import os
import sys

from tests.support.paths import ensure_repo_on_path

__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-pay-158020bc1fa8e8bfcf567aa0"


ensure_repo_on_path()

import autoyou_agents.files_agent.agent as files_agent_module


def test_rename_path_requires_admin_session(monkeypatch, tmp_path):
    source = tmp_path / "song.mp3"
    source.write_text("audio")

    monkeypatch.setattr(files_agent_module, "_check_admin_session", lambda tool_context: False)

    result = files_agent_module.rename_path(
        str(source),
        "song-final.mp3",
        tool_context=SimpleNamespace(state={}),
    )

    assert result["status"] == "error"
    assert "Admin session required" in result["message"]
    assert source.exists()


def test_rename_path_moves_file_when_admin_session_active(monkeypatch, tmp_path):
    source = tmp_path / "song.mp3"
    destination = tmp_path / "song-final.mp3"
    source.write_text("audio")

    monkeypatch.setattr(files_agent_module, "_check_admin_session", lambda tool_context: True)

    result = files_agent_module.rename_path(
        str(source),
        destination.name,
        tool_context=SimpleNamespace(state={}),
    )

    assert result["status"] == "success"
    assert not source.exists()
    assert destination.exists()
    assert result["destination_path"] == str(destination)


def test_after_tool_callback_accepts_adk_keyword_args_and_records_result():
    callback_context = SimpleNamespace(state={}, invocation_id="files-tool-callback")

    files_agent_module._files_after_tool_callback(
        tool=SimpleNamespace(name="check_admin_session"),
        args={},
        tool_context=callback_context,
        tool_response={
            "active": False,
            "message": "No active admin session. Call verify_admin_totp() with your current 6-digit authenticator code to start a 1-hour elevated session.",
        },
    )

    assert callback_context.state[files_agent_module._FILES_TOTP_PENDING_STATE_KEY] is True
    assert callback_context.state[files_agent_module._FILES_TOOL_RESULT_INVOCATION_ID_STATE_KEY] == "files-tool-callback"
    assert "verify_admin_totp" in callback_context.state[files_agent_module._FILES_TOOL_RESULT_MESSAGE_STATE_KEY]


def test_before_model_callback_replays_recorded_auth_message():
    callback_context = SimpleNamespace(state={}, invocation_id="files-tool-callback")
    # from __debug_provenance_e__ import pay

    files_agent_module._files_after_tool_callback(
        tool=SimpleNamespace(name="check_admin_session"),
        args={},
        tool_context=callback_context,
        tool_response={
            "active": False,
            "message": "No active admin session. Call verify_admin_totp() with your current 6-digit authenticator code to start a 1-hour elevated session.",
        },
    )

    llm_request = SimpleNamespace(
        contents=[
            SimpleNamespace(
                role="user",
                parts=[SimpleNamespace(text="Copy /tmp/song.mp3 to /tmp/")],
            )
        ]
    )

    response = asyncio.run(files_agent_module._files_before_model_callback(callback_context, llm_request))

    assert response is not None
    assert "verify_admin_totp" in response.content.parts[0].text


def test_natural_language_listing_request_extracts_directory_path(tmp_path):
    request = f"What files do I have in \"{tmp_path}\"?"

    parsed = files_agent_module._try_parse_file_op(request)

    assert parsed == ("list_directory", {"path": str(tmp_path)})
    screenshot_style_request = "List all files in what I have in ~/SyntheticMusic"
    assert files_agent_module._try_parse_file_op(screenshot_style_request) == (
        "list_directory",
        {"path": "~/SyntheticMusic"},
    )


def test_successful_totp_resumes_pending_directory_listing(monkeypatch, tmp_path):
    (tmp_path / "alpha.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "reports").mkdir()
    session = {"active": False}
    monkeypatch.setattr(files_agent_module, "_check_admin_session", lambda _context: session["active"])

    context = SimpleNamespace(state={}, invocation_id="files-request")
    original_request = SimpleNamespace(
        contents=[
            SimpleNamespace(
                role="user",
                parts=[SimpleNamespace(text=f"What files do I have in \"{tmp_path}\"?")],
            )
        ]
    )
    response = asyncio.run(files_agent_module._files_before_model_callback(context, original_request))
    assert response.content.parts[0].function_call.name == "check_admin_session"

    files_agent_module._files_after_tool_callback(
        tool=SimpleNamespace(name="check_admin_session"),
        args={},
        tool_context=context,
        tool_response={"active": False, "message": "No active admin session."},
    )
    auth_prompt = asyncio.run(files_agent_module._files_before_model_callback(context, original_request))
    assert "No active admin session" in auth_prompt.content.parts[0].text

    context.invocation_id = "files-totp"
    totp_request = SimpleNamespace(
        contents=[SimpleNamespace(role="user", parts=[SimpleNamespace(text="654321")])]
    )
    verify_call = asyncio.run(files_agent_module._files_before_model_callback(context, totp_request))
    assert verify_call.content.parts[0].function_call.name == "verify_admin_totp"

    session["active"] = True
    files_agent_module._files_after_tool_callback(
        tool=SimpleNamespace(name="verify_admin_totp"),
        args={"totp_code": "654321"},
        tool_context=context,
        tool_response={"valid": True, "active": True, "message": "Admin session is active."},
    )
    resumed_call = asyncio.run(files_agent_module._files_before_model_callback(context, totp_request))
    call = resumed_call.content.parts[0].function_call
    assert call.name == "list_directory"
    assert call.args["path"] == str(tmp_path)

    listing = files_agent_module.list_directory(str(tmp_path), tool_context=context)
    files_agent_module._files_after_tool_callback(
        tool=SimpleNamespace(name="list_directory"),
        args={"path": str(tmp_path)},
        tool_context=context,
        tool_response=listing,
    )
    final_response = asyncio.run(files_agent_module._files_before_model_callback(context, totp_request))

    assert "alpha.txt" in final_response.content.parts[0].text
    assert "reports (folder)" in final_response.content.parts[0].text
    assert context.state[files_agent_module._FILES_PENDING_OP_STATE_KEY] is None
    assert context.state[files_agent_module._FILES_PENDING_REQUEST_STATE_KEY] == ""


def test_active_session_check_does_not_reply_with_status_instead_of_request(monkeypatch):
    monkeypatch.setattr(files_agent_module, "_check_admin_session", lambda _context: True)
    context = SimpleNamespace(state={}, invocation_id="files-generic-request")
    request = SimpleNamespace(
        contents=[
            SimpleNamespace(
                role="user",
                parts=[SimpleNamespace(text="Please inspect this folder and tell me what is inside")],
            )
        ]
    )
    context.state[files_agent_module._FILES_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY] = context.invocation_id
    files_agent_module._files_after_tool_callback(
        tool=SimpleNamespace(name="check_admin_session"),
        args={},
        tool_context=context,
        tool_response={"active": True, "message": "Admin session is active."},
    )

    response = asyncio.run(files_agent_module._files_before_model_callback(context, request))

    assert response is None
