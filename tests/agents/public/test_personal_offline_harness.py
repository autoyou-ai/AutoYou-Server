# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from google.genai import types

from autoyou_agents import model_config
from autoyou_agents.internet_agent import agent as internet_agent_module
from autoyou_agents.internet_agent import expanded_harness
from autoyou_agents.litellm_ollama_adapter import _normalize_ollama_message
from autoyou_agents.remote_desktop_agent import agent as rd_agent
from autoyou_agents.self_improvement import goal_loop
from autoyou_agents.shared_tools import desktop_accessibility as da


def test_qwen35_uses_expanded_harness_with_compact_root_routing_for_small_tags(monkeypatch):
    monkeypatch.delenv("AUTOYOU_AGENT_HARNESS_PROFILE", raising=False)
    monkeypatch.delenv("AUTOYOU_ACTIVE_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("AUTOYOU_ACTIVE_OLLAMA_PARAMETER_SIZE", raising=False)

    assert model_config.resolve_agent_harness_profile("ollama_chat/qwen3.5:4b") == model_config.AGENT_HARNESS_EXPANDED
    assert model_config.resolve_agent_harness_profile("ollama_chat/qwen3.5:9b") == model_config.AGENT_HARNESS_EXPANDED
    assert model_config.model_uses_compact_root_tool_routing("ollama_chat/qwen3.5:4b") is True
    assert model_config.model_uses_compact_root_tool_routing("ollama_chat/qwen3.5:9b") is True
    assert model_config.model_uses_compact_root_tool_routing("ollama_chat/qwen3.5:27b") is False


def test_internet_open_and_parse_news_uses_research_mode_and_rich_navigate_format():
    mode = internet_agent_module._classify_internet_request_mode(
        "@internet Open bbc.com and parse later news"
    )
    assert mode == internet_agent_module._INTERNET_REQUEST_MODE_RESEARCH

    formatted = internet_agent_module._format_verified_internet_tool_result(
        "navigate_page",
        {"url": "https://example.test/news"},
        {
            "status": "success",
            "final_url": "https://example.test/news",
            "title": "Example News",
            "headlines": [{"title": "Synthetic Breaking Headline", "url": "https://example.test/news/1"}],
            "text_content": "Full front page text",
        },
    )
    assert "navigate_page completed successfully." not in formatted
    assert "Example News" in formatted
    assert "Synthetic Breaking Headline" in formatted


def test_expanded_harness_recovers_empty_model_reply_from_evidence():
    state = {
        expanded_harness._USER_REQUEST_STATE_KEY: "Open example.test and parse latest news",
        expanded_harness._REQUEST_MODE_STATE_KEY: "research",
        expanded_harness._EVIDENCE_STATE_KEY: [
            {
                "kind": "page",
                "url": "https://example.test/news",
                "title": "Example News",
                "text": "Latest synthetic updates.",
                "headlines": [{"title": "Synthetic Story One", "url": "https://example.test/1"}],
            }
        ],
    }
    empty_response = SimpleNamespace(content=types.Content(role="model", parts=[]))
    ctx = SimpleNamespace(state=state, invocation_id="inv-empty")
    recovered = asyncio.run(expanded_harness.after_model_callback(ctx, empty_response))
    assert recovered is not None
    text = recovered.content.parts[0].text
    assert "Synthetic Story One" in text


def test_ollama_adapter_recovers_empty_visible_content_when_reasoning_has_final_answer():
    message = {
        "role": "assistant",
        "content": "",
        "reasoning_content": "Let me check the news. Final answer: Top story is Synthetic Launch.",
    }
    _normalize_ollama_message(
        message,
        explicit_agent_tool_names=set(),
        advertised_tool_names=set(),
        latest_user_request="What was the news?",
        root_agent_name="autoyou_agent",
        reasoning_fallback=None,
        finish_reason="stop",
    )
    assert "Top story is Synthetic Launch." in str(message["content"])


def test_desktop_accessibility_detects_docker_wsl_windows_macos_cleanly(monkeypatch):
    # Docker headless without DISPLAY
    monkeypatch.setattr(da.platform, "system", lambda: "Linux")
    monkeypatch.setattr(da, "_is_docker_container", lambda: True)
    monkeypatch.setattr(da, "_is_wsl", lambda: False)
    monkeypatch.setattr(da, "find_cua_driver_binary", lambda: None)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    env = da.detect_desktop_environment()
    assert env["platform"] == "docker"
    assert env["display_available"] is False
    assert env["backend"] == "headless-docker"
    assert da.list_accessible_windows()["status"] == "headless"

    # WSL2 with Windows PowerShell UIA bridge
    monkeypatch.setattr(da, "_is_docker_container", lambda: False)
    monkeypatch.setattr(da, "_is_wsl", lambda: True)
    monkeypatch.setattr(da, "_windows_powershell_binary", lambda allow_wsl_host=True: "/mnt/c/powershell.exe")
    env_wsl = da.detect_desktop_environment()
    assert env_wsl["platform"] == "wsl2"
    assert env_wsl["display_available"] is True
    assert env_wsl["backend"] == "wsl-windows-uia"

    # Native Windows without cua-driver
    monkeypatch.setattr(da.platform, "system", lambda: "Windows")
    monkeypatch.setattr(da, "_is_wsl", lambda: False)
    env_win = da.detect_desktop_environment()
    assert env_win["platform"] == "windows"
    assert env_win["backend"] == "windows-uia"

    # Native macOS with cua-driver
    monkeypatch.setattr(da.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(da, "find_cua_driver_binary", lambda: "/usr/local/bin/cua-driver")
    env_mac = da.detect_desktop_environment()
    assert env_mac["platform"] == "macos"
    assert env_mac["backend"] == "cua-driver"
    assert env_mac["background_control_supported"] is True


def test_desktop_accessibility_find_composer_and_token_cache():
    snap = {
        "snapshot_id": "ay-synthetic",
        "backend": "macos-ax",
        "window": {"app_name": "SyntheticApp", "pid": 99999},
        "elements": [
            {"index": 1, "role": "AXButton", "label": "Settings", "token": "ay-synthetic:1"},
            {"index": 2, "role": "AXTextArea", "label": "Ask anything", "token": "ay-synthetic:2"},
        ],
    }
    da._store_snapshot("ay-synthetic", snap)
    assert da.find_composer_element_token(snap) == "ay-synthetic:2"
    found_snap, found_el = da._lookup_snapshot_element("ay-synthetic:2")
    assert found_snap is not None and found_el is not None
    assert found_el["label"] == "Ask anything"


def test_remote_desktop_tools_and_goal_loop_personal_harness_suite():
    tool_names = {getattr(t, "__name__", "") for t in rd_agent.get_tools()}
    assert {
        "desktop_automation_status",
        "snapshot_window_ui",
        "click_ui_element",
        "type_into_ui_element",
        "press_ui_hotkey",
    }.issubset(tool_names)

    scenarios = goal_loop.select_scenarios([], suite="personal_harness")
    names = {s.name for s in scenarios}
    assert "harness_greeting_nonempty" in names
    assert "harness_internet_open_parse_and_followup" in names
    assert "harness_desktop_automation_status" in names
