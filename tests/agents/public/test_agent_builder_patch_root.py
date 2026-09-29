# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-W-stripe-4db9df268ec36d2405f9c82a


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import ast
import os
import sys
from pathlib import Path

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_w__ = "AUTOYOU-PROVENANCE-W-stripe-4db9df268ec36d2405f9c82a"


ensure_repo_on_path()

import server
from autoyou_agents.agent_builder_agent import agent as builder_agent


def test_patch_root_agent_updates_registry_without_rewriting_prompt(tmp_path, monkeypatch):
    original_root_prompt_py = builder_agent._ROOT_PROMPT_PY

    temp_root_prompt = tmp_path / "prompt.py"
    original_prompt_text = original_root_prompt_py.read_text(encoding="utf-8")
    temp_root_prompt.write_text(original_prompt_text, encoding="utf-8")

    monkeypatch.setattr(builder_agent, "_ROOT_PROMPT_PY", temp_root_prompt)
    captured = {}

    monkeypatch.setattr(
        builder_agent,
        "set_agent_installed",
        lambda agent_name, installed, **kwargs: captured.setdefault(
            "call",
            {
                "agent_name": agent_name,
                "installed": installed,
                "description": kwargs.get("description"),
                "source": kwargs.get("source"),
            },
        ) or {"installed_agents": [agent_name]},
    )

    result = builder_agent.patch_root_agent(
        "quote_test_agent",
        'Fetch "real-time" weather data for a given location.',
    )

    assert result["status"] == "success"
    assert result["patched_files"] == []
    assert temp_root_prompt.read_text(encoding="utf-8") == original_prompt_text
    assert captured["call"] == {
        "agent_name": "quote_test_agent",
        "installed": True,
        "description": 'Fetch "real-time" weather data for a given location.',
        "source": "agent_builder",
    }


def test_patch_root_agent_falls_back_to_embedded_root_for_builtin_agents(tmp_path, monkeypatch):
    writable_root = tmp_path / "writable"
    embedded_root = tmp_path / "embedded"
    (embedded_root / "claude_desktop_agent").mkdir(parents=True)
    captured = {}

    monkeypatch.setattr(builder_agent, "_AGENTS_ROOT", writable_root)
    monkeypatch.setattr(builder_agent, "_EMBEDDED_AGENTS_ROOT", embedded_root)
    monkeypatch.setattr(
        builder_agent,
        "set_agent_installed",
        lambda agent_name, installed, **kwargs: captured.setdefault(
            "call",
            {
                "agent_name": agent_name,
                "installed": installed,
                "agents_root": kwargs.get("agents_root"),
            },
        ) or {"installed_agents": [agent_name]},
    )

    result = builder_agent.patch_root_agent("claude_desktop_agent", "Claude desktop bridge.")

    assert result["status"] == "success"
    assert captured["call"] == {
        "agent_name": "claude_desktop_agent",
        "installed": True,
        "agents_root": embedded_root,
    }


def test_list_existing_agents_includes_embedded_agents_when_writable_root_is_empty(tmp_path, monkeypatch):
    writable_root = tmp_path / "writable"
    embedded_root = tmp_path / "embedded"
    writable_root.mkdir()
    for agent_name in ("notes_agent", "page_agent"):
        agent_dir = embedded_root / agent_name
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.py").write_text("AGENT_NAME = 'test'\n", encoding="utf-8")

    monkeypatch.setattr(builder_agent, "_AGENTS_ROOT", writable_root)
    monkeypatch.setattr(builder_agent, "_EMBEDDED_AGENTS_ROOT", embedded_root)

    result = builder_agent.list_existing_agents()

    assert result == {
        "status": "success",
        "agents": ["notes_agent", "page_agent"],
        "count": 2,
    }


def test_replace_string_assignment_value_handles_values_ending_with_quote():
    content = 'AGENT_INSTRUCTION = """old"""\nDEFAULT_INSTRUCTION = AGENT_INSTRUCTION\n'
    # from __debug_provenance_w__ import stripe

    replaced = server._replace_string_assignment_value(
        content,
        "AGENT_INSTRUCTION",
        'Ends with a quote "',
    )

    parsed = ast.parse(replaced)
    env = {}
    exec(compile(parsed, "<prompt>", "exec"), env)
    assert env["AGENT_INSTRUCTION"] == 'Ends with a quote "'


def test_patch_root_agent_keeps_workspace_agents_uninstalled_in_compiled_mode(tmp_path, monkeypatch):
    original_agents_root = builder_agent._AGENTS_ROOT
    agent_dir = tmp_path / "custom_agent"
    agent_dir.mkdir(parents=True)

    captured = {}

    monkeypatch.setattr(builder_agent, "_AGENTS_ROOT", tmp_path)
    monkeypatch.setattr(
        builder_agent,
        "set_agent_installed",
        lambda agent_name, installed, **kwargs: captured.setdefault(
            "call",
            {
                "agent_name": agent_name,
                "installed": installed,
            },
        ) or {"installed_agents": []},
    )
    monkeypatch.setattr("shared.platform_runtime.is_compiled", lambda: True)

    try:
        result = builder_agent.patch_root_agent("custom_agent", "Workspace-only draft.")
    finally:
        monkeypatch.setattr(builder_agent, "_AGENTS_ROOT", original_agents_root)

    assert result["status"] == "success"
    assert result["workspace_only"] is True
    assert result["installed"] is False
    assert result["requires_restart"] is False
    assert captured["call"] == {"agent_name": "custom_agent", "installed": False}


def test_get_scaffold_status_reports_runtime_block_for_workspace_agents_in_compiled_mode(tmp_path, monkeypatch):
    original_agents_root = builder_agent._AGENTS_ROOT
    agent_dir = tmp_path / "custom_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.py").write_text("def create_custom_agent(*args, **kwargs):\n    return None\n", encoding="utf-8")

    monkeypatch.setattr(builder_agent, "_AGENTS_ROOT", tmp_path)
    monkeypatch.setattr("shared.platform_runtime.is_compiled", lambda: True)

    try:
        status = builder_agent.get_scaffold_status("custom_agent")
    finally:
        monkeypatch.setattr(builder_agent, "_AGENTS_ROOT", original_agents_root)

    assert status["status"] == "success"
    assert status["exists"] is True
    assert status["importable"] is False
    assert status["runtime_loadable"] is False
    assert status["runtime_blocked"] is True
