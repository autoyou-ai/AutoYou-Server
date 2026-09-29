# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-158020bc1fa8e8bfcf567aa0


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
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
