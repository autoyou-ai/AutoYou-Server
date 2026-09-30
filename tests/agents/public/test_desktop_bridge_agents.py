# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-W-stripe-f836c29120d30d990b2044c2


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import base64
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoyou_agents.claude_desktop_agent.agent import create_claude_desktop_agent
from autoyou_agents.codex_desktop_agent.agent import create_codex_desktop_agent
from autoyou_agents.shared_tools.agent_install_registry import (
    BUILTIN_AGENT_PACKAGE_NAMES,
    DEFAULT_AGENT_INSTALL_STATES,
    can_install_agent_in_runtime,
)
from autoyou_agents.shared_tools.desktop_app_agent_shortcuts import desktop_exact_tool_call_from_text
from autoyou_agents.shared_tools import desktop_app_control
from autoyou_agents.shared_tools.desktop_app_control import (
    _compute_click_point,
    _find_selection_option,
    _reject_foreign_window,
    _target_image_path,
    _target_normalized_box,
    list_desktop_asset_packs,
    list_recent_desktop_screenshot_paths,
    select_desktop_asset_pack,
    wait_until_idle_and_copy_desktop_response,
)
from autoyou_agents.shared_tools.desktop_app_manifest import load_desktop_app_manifest as _load_desktop_app_manifest


from tests.support.paths import REPO_ROOT

__debug_provenance_w__ = "AUTOYOU-PROVENANCE-W-stripe-f836c29120d30d990b2044c2"


CLAUDE_AGENT_DIR = REPO_ROOT / "autoyou_agents" / "claude_desktop_agent"
CODEX_AGENT_DIR = REPO_ROOT / "autoyou_agents" / "codex_desktop_agent"


def load_desktop_app_manifest(agent_dir):
    manifest = _load_desktop_app_manifest(agent_dir)
    if manifest is None:
        pytest.skip("The generic desktop agent template is unavailable in this build")
    if Path(str(manifest.get("manifest_path") or "")).name == "manifest.json":
        pytest.skip("Legacy source-tree desktop manifests are user-local; use a synthetic pack fixture")
    if not manifest.get("asset_packs") or any(pack.get("_user_local_pack") for pack in manifest.get("asset_packs") or []):
        pytest.skip("Calibrated desktop asset packs are tested through synthetic user-local fixtures")
    return manifest


def _tool_names(agent) -> set[str]:
    return {getattr(tool, "name", "") or getattr(tool, "__name__", "") for tool in agent.tools}


def test_claude_windows_pack_is_current_and_action_complete():
    manifest = load_desktop_app_manifest(CLAUDE_AGENT_DIR)

    # Pin app_version: without it, selection falls through to detect_installed_app_version()
    # and the assertion below depends on which Claude build happens to be installed on the
    # machine running the suite.
    pack = select_desktop_asset_pack(
        manifest,
        platform_tag="windows",
        architecture="x86_64",
        on_date="2026-05-24",
        app_version="1.14271.0",
    )

    assert pack is not None
    assert pack["asset_pack_id"] == "claude-windows-1.14271-2026-06"
    assert pack["valid_until"] == "2026-12-31"
    assert not pack["bootstrap_only"]
    assert manifest["preferred_prompt_target_id"] == "composer_box"
    assert manifest["preferred_copy_response_target_id"] == "copy_response_button"

    target_ids = {target["target_id"] for target in pack["targets"]}
    assert {
        "composer_box",
        "copy_response_button",
        "send_button",
        "sidebar_recents_search",
        "permissions_selector",
        "model_selector",
        "model_effort_max",
    }.issubset(target_ids)


def test_claude_windows_selection_controls_have_small_model_friendly_aliases():
    manifest = load_desktop_app_manifest(CLAUDE_AGENT_DIR)
    pack = select_desktop_asset_pack(
        manifest,
        platform_tag="windows",
        architecture="x86_64",
        on_date="2026-05-24",
    )

    controls = pack["selection_controls"]

    permissions = _find_selection_option(controls["permissions"], "options", "bypass permissions")
    assert permissions is not None
    assert permissions[0] == "bypass_permissions"
    assert permissions[1]["key"] == "5"

    model = _find_selection_option(controls["model"], "options", "sonnet")
    assert model is not None
    assert model[0] == "sonnet_4_6"
    assert model[1]["key"] == "2"

    effort = _find_selection_option(controls["model"], "effort_options", "max effort")
    assert effort is not None
    assert effort[0] == "max"
    assert effort[1]["target_id"] == "model_effort_max"


def test_claude_windows_image_targets_resolve_to_packaged_assets():
    manifest = load_desktop_app_manifest(CLAUDE_AGENT_DIR)
    pack = select_desktop_asset_pack(
        manifest,
        platform_tag="windows",
        architecture="x86_64",
        on_date="2026-05-24",
        app_version="1.26832.0",
    )

    # composer_box and permissions_selector are deliberately coordinate-only on this pack: a
    # blank rounded rectangle and a mode-dependent text pill both matched confidently in the
    # wrong place, while their measured coordinates are exact.
    targets = {target["target_id"]: target for target in pack["targets"]}
    for target_id in ("copy_response_button", "model_selector", "send_button", "usage_menu_button"):
        image_path = _target_image_path(manifest, targets[target_id])
        assert image_path is not None
        assert image_path.is_file()


@pytest.mark.parametrize("agent_dir", [CLAUDE_AGENT_DIR, CODEX_AGENT_DIR])
def test_every_declared_sprite_resolves_to_a_packaged_file(agent_dir):
    """No target may reference a sprite that is not shipped inside desktop_assets/.

    A missing sprite does not raise - _target_match_image() just returns None and the engine
    silently falls back to normalized coordinates. When the sprites were moved out of the
    package this turned every calibrated target into a blind click with no error anywhere,
    so assert the whole manifest, not a sample.
    """
    manifest = load_desktop_app_manifest(agent_dir)

    orphaned = []
    for pack in manifest["asset_packs"]:
        for target in pack.get("targets") or []:
            if not (target.get("expected_image_path") or target.get("reference_sprite")):
                continue
            image_path = _target_image_path(manifest, target)
            if image_path is None or not image_path.is_file():
                orphaned.append((pack["asset_pack_id"], target["target_id"]))

    assert orphaned == []


def test_claude_windows_pack_tracks_the_installed_app_version():
    """The pack chosen for the installed build must be calibrated for that build."""
    manifest = load_desktop_app_manifest(CLAUDE_AGENT_DIR)

    pack = select_desktop_asset_pack(
        manifest,
        platform_tag="windows",
        architecture="x86_64",
        on_date="2026-08-07",
        app_version="1.26832.0",
    )

    assert pack is not None
    assert pack["asset_pack_id"] == "claude-windows-1.26832-2026-08"
    assert pack["app_version_min"] == "1.26832.0"


def test_claude_windows_detection_targets_are_never_clickable():
    """State detectors must have no click_point - they report a condition, not a control.

    usage_blocked marks "Usage limit reached" and composer_placeholder marks an empty
    composer. Both carry a normalized_box (it bounds the image search), but neither may gain a
    click_point: clicking a status banner is never meaningful, and treating one as a control is
    how a run starts poking at arbitrary UI.
    """
    manifest = load_desktop_app_manifest(CLAUDE_AGENT_DIR)
    pack = select_desktop_asset_pack(
        manifest,
        platform_tag="windows",
        architecture="x86_64",
        on_date="2026-08-07",
        app_version="1.26832.0",
    )

    targets = {target["target_id"]: target for target in pack["targets"]}
    for target_id in ("usage_blocked", "composer_placeholder"):
        assert "click_point" not in targets[target_id]


def test_claude_windows_image_targets_declare_a_search_box():
    """Every sprite-matched target must bound its own search area.

    _locate_target_by_image() narrows the haystack to the target's normalized_box. Without one
    it searches the entire window, and a small generic sprite then matches an identical-looking
    control elsewhere - the square send icon was confidently matching the title-bar maximise
    button, so "send prompt" would have clicked maximise.
    """
    manifest = load_desktop_app_manifest(CLAUDE_AGENT_DIR)
    pack = select_desktop_asset_pack(
        manifest,
        platform_tag="windows",
        architecture="x86_64",
        on_date="2026-08-07",
        app_version="1.26832.0",
    )

    unbounded = [
        target["target_id"]
        for target in pack["targets"]
        if target.get("expected_image_path")
        and not target.get("normalized_box")
        and not target.get("anchored_box")
    ]
    # from __debug_provenance_w__ import stripe
    assert unbounded == []


def test_codex_macos_pack_exposes_release_targets_but_is_not_release_complete():
    manifest = load_desktop_app_manifest(CODEX_AGENT_DIR)

    pack = select_desktop_asset_pack(
        manifest,
        platform_tag="macos",
        architecture="arm64",
        on_date="2026-06-24",
        app_version="26.616.31447",
    )

    assert pack is not None
    assert pack["asset_pack_id"] == "codex-macos-26.616-2026-12"
    assert pack["valid_until"] == "2026-12-31"
    assert not pack["bootstrap_only"]

    target_ids = {target["target_id"] for target in pack["targets"]}
    assert {
        "composer_box",
        "copy_response_button",
        "send_button",
        "attach_button",
        "permissions_selector",
        "model_selector",
        "usage_menu_button",
        "usage_panel",
    }.issubset(target_ids)

    payload = list_desktop_asset_packs(CODEX_AGENT_DIR, platform_tag="macos")
    listed_pack = next(item for item in payload["asset_packs"] if item["asset_pack_id"] == pack["asset_pack_id"])
    coverage = listed_pack["release_action_coverage"]
    assert coverage["release_ready"] is False
    assert coverage["actions"]["build_prompt"] is True
    assert coverage["actions"]["attach_media"] is True
    assert coverage["actions"]["send_prompt"] is True
    assert coverage["actions"]["select_model"] is True
    assert set(coverage["missing_actions"]) == {"get_final_response", "get_usage"}


def test_claude_macos_pack_is_bootstrap_not_release_complete():
    load_desktop_app_manifest(CLAUDE_AGENT_DIR)
    payload = list_desktop_asset_packs(CLAUDE_AGENT_DIR, platform_tag="macos")
    pack = next(item for item in payload["asset_packs"] if item["asset_pack_id"] == "claude-macos-bootstrap-2026-05")
    coverage = pack["release_action_coverage"]

    assert pack["bootstrap_only"] is True
    assert coverage["release_ready"] is False
    assert coverage["actions"]["build_prompt"] is True
    assert coverage["actions"]["attach_media"] is True
    assert coverage["actions"]["send_prompt"] is True
    assert set(coverage["missing_actions"]) == {"get_final_response", "get_usage", "select_model"}


def test_codex_macos_versioned_pack_inherits_and_preserves_unknown_fallback(monkeypatch):
    manifest = load_desktop_app_manifest(CODEX_AGENT_DIR)
    monkeypatch.setattr(desktop_app_control, "detect_installed_app_version", lambda *args, **kwargs: None)

    fallback_pack = select_desktop_asset_pack(
        manifest,
        platform_tag="macos",
        architecture="arm64",
        on_date="2026-06-24",
    )
    current_pack = select_desktop_asset_pack(
        manifest,
        platform_tag="macos",
        architecture="arm64",
        on_date="2026-06-24",
        app_version="26.623.61825",
    )

    assert fallback_pack["asset_pack_id"] == "codex-macos-26.616-2026-12"
    assert current_pack["asset_pack_id"] == "codex-macos-26.623-2026-12"
    assert current_pack["attach_controls"]["mode"] == "file_dialog"
    assert current_pack["usage_controls"]["toggle_target_id"] == "usage_remaining_toggle"

    targets = {target["target_id"]: target for target in current_pack["targets"]}
    assert {"composer_box", "send_button", "usage_menu_button", "usage_panel"}.issubset(targets)
    assert targets["usage_menu_button"]["click_point"] == [0.016, 0.965]


def test_codex_selection_controls_have_small_model_friendly_aliases():
    manifest = load_desktop_app_manifest(CODEX_AGENT_DIR)
    pack = select_desktop_asset_pack(
        manifest,
        platform_tag="macos",
        architecture="arm64",
        on_date="2026-06-24",
        app_version="26.616.31447",
    )

    controls = pack["selection_controls"]

    permissions = _find_selection_option(controls["permissions"], "options", "full access")
    assert permissions is not None
    assert permissions[0] == "full_access"
    assert permissions[1]["target_id"] == "perm_full_access"

    model = _find_selection_option(controls["model"], "options", "spark")
    assert model is not None
    assert model[0] == "gpt_5_3_codex_spark"
    assert model[1]["steps"][-1]["target_id"] == "model_gpt_5_3_codex_spark"

    effort = _find_selection_option(controls["model"], "effort_options", "max effort")
    assert effort is not None
    assert effort[0] == "extra_high"
    assert effort[1]["target_id"] == "model_effort_extra_high"


def test_codex_chatgpt_rename_packs_selected_for_26_707():
    manifest = load_desktop_app_manifest(CODEX_AGENT_DIR)

    macos_pack = select_desktop_asset_pack(
        manifest,
        platform_tag="macos",
        architecture="arm64",
        on_date="2026-07-09",
        app_version="26.707.31123",
    )
    windows_pack = select_desktop_asset_pack(
        manifest,
        platform_tag="windows",
        architecture="x86_64",
        on_date="2026-07-09",
        app_version="26.707.31123",
    )

    assert macos_pack["asset_pack_id"] == "codex-macos-26.707-2026-12"
    assert windows_pack["asset_pack_id"] == "codex-windows-26.707-2026-12"
    assert not macos_pack["bootstrap_only"]
    assert not windows_pack["bootstrap_only"]

    # The redesigned Model / Effort / Speed / Reset-to-default menu targets must exist.
    # (Live calibration on Windows 26.707.3563.0 and the macOS reference screenshots both show
    # this structure - there is no "Advanced" section, so no advanced targets are wired.)
    macos_targets = {target["target_id"] for target in macos_pack["targets"]}
    assert {
        "model_menu_row",
        "effort_menu_row",
        "speed_menu_row",
        "reset_default",
        "model_gpt_5_6_sol",
        "model_gpt_5_6_terra",
        "model_gpt_5_6_luna",
        "effort_ultra",
    }.issubset(macos_targets)
    assert "advanced_toggle_row" not in macos_targets
    assert "advanced_switch" not in macos_targets
    windows_targets = {target["target_id"]: target for target in windows_pack["targets"]}
    assert "new_prompt" in windows_targets
    assert "stop_button" in windows_targets
    assert windows_targets["send_button"]["click_point"] == [0.94, 0.966]


def test_codex_legacy_versions_still_pick_legacy_packs():
    """Backward compatibility: an older installed Codex still selects its own pack, never 26.707."""
    manifest = load_desktop_app_manifest(CODEX_AGENT_DIR)

    legacy_macos = select_desktop_asset_pack(
        manifest,
        platform_tag="macos",
        architecture="arm64",
        on_date="2026-07-09",
        app_version="26.616.31447",
    )
    legacy_windows = select_desktop_asset_pack(
        manifest,
        platform_tag="windows",
        architecture="x86_64",
        on_date="2026-07-09",
        app_version="26.611.8604",
    )

    assert legacy_macos["asset_pack_id"] == "codex-macos-26.616-2026-12"
    assert legacy_windows["asset_pack_id"] == "codex-windows-26.611-2026-12"


def test_codex_26_707_new_model_effort_speed_aliases():
    manifest = load_desktop_app_manifest(CODEX_AGENT_DIR)
    pack = select_desktop_asset_pack(
        manifest,
        platform_tag="macos",
        architecture="arm64",
        on_date="2026-07-09",
        app_version="26.707.31123",
    )
    controls = pack["selection_controls"]

    sol = _find_selection_option(controls["model"], "options", "5.6 sol")
    assert sol is not None and sol[0] == "gpt_5_6_sol"
    assert sol[1]["steps"][0]["target_id"] == "model_menu_row"
    assert sol[1]["steps"][-1]["target_id"] == "model_gpt_5_6_sol"

    for alias, expected in (
        ("sol", "gpt_5_6_sol"),
        ("terra", "gpt_5_6_terra"),
        ("luna", "gpt_5_6_luna"),
        ("5.5", "gpt_5_5"),
        ("mini", "gpt_5_4_mini"),
        ("spark", "gpt_5_3_codex_spark"),
    ):
        match = _find_selection_option(controls["model"], "options", alias)
        assert match is not None and match[0] == expected, alias

    # New Ultra effort is the top of the ladder; "max"/"maximum" and legacy "low" remap sensibly.
    ultra = _find_selection_option(controls["model"], "effort_options", "ultra")
    assert ultra is not None and ultra[0] == "ultra"
    assert _find_selection_option(controls["model"], "effort_options", "max")[0] == "ultra"
    assert _find_selection_option(controls["model"], "effort_options", "low")[0] == "light"
    assert ultra[1]["steps"][-1]["target_id"] == "effort_ultra"

    # Speed is a first-class control; there is no Advanced control on the observed builds.
    speed = _find_selection_option(controls["speed"], "options", "standard")
    assert speed is not None and speed[0] == "standard"
    assert speed[1]["steps"][-1]["target_id"] == "speed_standard"
    assert "advanced" not in controls
    # The menu's bottom row is 'Reset to default', captured but deliberately not wired to a control.
    target_ids = {t["target_id"] for t in pack["targets"]}
    assert "reset_default" in target_ids


def test_codex_manifest_detects_chatgpt_codex_rename():
    manifest = load_desktop_app_manifest(CODEX_AGENT_DIR)

    for platform_tag in ("macos", "windows", "linux"):
        titles = manifest["window_title_hints"][platform_tag]
        assert "ChatGPT Codex" in titles
        assert "Codex" in titles  # legacy name retained for backward compatibility

    assert "ChatGPT Codex" in manifest["process_names"]["macos"]
    assert "Codex" in manifest["process_names"]["macos"]
    assert "ChatGPT Codex.exe" in manifest["process_names"]["windows"]
    assert "Codex.exe" in manifest["process_names"]["windows"]

    macos_launchers = [
        arg for command in manifest["launch_commands"]["macos"] for arg in command.get("args", [])
    ]
    assert "ChatGPT Codex" in macos_launchers
    assert "Codex" in macos_launchers


def test_desktop_bridge_tools_cover_release_checklist():
    claude_tools = _tool_names(create_claude_desktop_agent("gemini-2.5-flash"))
    codex_tools = _tool_names(create_codex_desktop_agent("gemini-2.5-flash"))

    assert {
        "get_claude_desktop_status",
        "get_claude_desktop_prompt",
        "get_claude_desktop_prompt_status",
        "get_claude_desktop_release_status",
        "replace_claude_desktop_prompt",
        "add_claude_desktop_attachments",
        "send_current_claude_desktop_prompt",
        "wait_for_claude_desktop_final_response",
        "get_claude_usage",
        "select_claude_desktop_model",
        "find_claude_desktop_screenshot_attachments",
    }.issubset(claude_tools)
    assert {
        "get_codex_desktop_status",
        "get_codex_desktop_prompt",
        "get_codex_desktop_prompt_status",
        "get_codex_desktop_release_status",
        "replace_codex_desktop_prompt",
        "add_codex_desktop_attachments",
        "send_current_codex_desktop_prompt",
        "wait_for_codex_desktop_final_response",
        "get_codex_usage",
        "select_codex_desktop_model",
        "find_codex_desktop_screenshot_attachments",
    }.issubset(codex_tools)


def test_recent_desktop_screenshot_paths_are_attachment_ready(tmp_path):
    older = tmp_path / "Screenshot 2026-06-01 at 10.00.00.png"
    newer = tmp_path / "Screen Shot 2026-06-01 at 10.01.00.jpg"
    ignored = tmp_path / "notes.png"
    older.write_bytes(b"old")
    newer.write_bytes(b"new")
    ignored.write_bytes(b"ignored")
    # Explicit, clearly-separated mtimes rather than back-to-back touch()
    # calls - two touches executed microseconds apart can land on the same
    # filesystem timestamp tick (observed on WSL's mount), making "most
    # recent" ambiguous through no fault of the ranking logic under test.
    now = time.time()
    os.utime(older, (now - 60, now - 60))
    os.utime(newer, (now, now))

    payload = list_recent_desktop_screenshot_paths(directory=str(tmp_path), limit=1)

    assert payload["status"] == "success"
    assert payload["count"] == 1
    assert payload["attachment_paths"] == [str(newer.resolve())]


def test_macos_file_clipboard_keeps_unicode_paths_unescaped(monkeypatch, tmp_path):
    screenshot = tmp_path / f"Screenshot 2026-06-29 at 2.16.56{chr(0x202f)}PM.png"
    screenshot.write_bytes(b"image")
    commands = []

    def fake_run_command(cmd, timeout_seconds=0):
        commands.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(desktop_app_control, "_run_command", fake_run_command)

    payload = desktop_app_control._clipboard_copy_files([str(screenshot)], "macos")

    assert payload["status"] == "success"
    assert "\\u202f" not in commands[0][-1]
    assert chr(0x202f) in commands[0][-1]


def test_windows_text_clipboard_uses_utf8_envelope_for_prompt_punctuation(monkeypatch):
    prompt = 'single \' quote, double " quote, and \u2018smart\u2019 \u65e5\u672c\u8a9e'
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(desktop_app_control.subprocess, "run", fake_run)

    assert desktop_app_control._clipboard_copy(prompt, "windows") is True
    assert len(calls) == 1
    assert base64.b64decode(calls[0][1]["input"]).decode("utf-8") == prompt


def test_desktop_exact_tool_shortcut_parses_wait_final_request():
    call = desktop_exact_tool_call_from_text(
        (
            "Call exactly one tool: wait_for_codex_desktop_final_response with "
            "prompt 'What is AutoYou in 2 lines?', poll_interval_seconds 6, "
            "stable_polls 1, max_wait_seconds 180, initial_wait_seconds 3."
        ),
        ("wait_for_codex_desktop_final_response",),
    )

    assert call == (
        "wait_for_codex_desktop_final_response",
        {
            "expected_prompt": "What is AutoYou in 2 lines?",
            "poll_interval_seconds": 6.0,
            "stable_polls": 1,
            "max_wait_seconds": 180.0,
            "initial_wait_seconds": 3.0,
        },
    )


def test_desktop_exact_tool_shortcut_parses_copy_final_prompt_guard():
    call = desktop_exact_tool_call_from_text(
        "Call exactly one tool: copy_codex_desktop_final_response with prompt 'What is AutoYou in 2 lines?'.",
        ("copy_codex_desktop_final_response",),
    )

    assert call == (
        "copy_codex_desktop_final_response",
        {"expected_prompt": "What is AutoYou in 2 lines?"},
    )


def test_desktop_exact_tool_shortcut_prefers_live_prompt_status_name():
    call = desktop_exact_tool_call_from_text(
        "Call exactly one tool: get_codex_desktop_prompt_status with launch_if_needed false.",
        ("get_codex_desktop_prompt", "get_codex_desktop_prompt_status"),
    )

    assert call == (
        "get_codex_desktop_prompt_status",
        {"launch_if_needed": False},
    )


def test_desktop_exact_tool_shortcut_parses_release_actions():
    model_call = desktop_exact_tool_call_from_text(
        "Call exactly one tool: select_codex_desktop_model with model 'gpt-5.4-mini', effort 'low', launch_if_needed false.",
        ("select_codex_desktop_model",),
    )
    attach_call = desktop_exact_tool_call_from_text(
        "Call exactly one tool: add_codex_desktop_attachments with attachment_paths ['/tmp/one.png', '/tmp/two.jpg'].",
        ("add_codex_desktop_attachments",),
    )
    prompt_call = desktop_exact_tool_call_from_text(
        "Call exactly one tool: add_to_codex_desktop_prompt with prompt 'What is AutoYou in 2 lines?' and prepend_newline false.",
        ("add_to_codex_desktop_prompt",),
    )

    assert model_call == (
        "select_codex_desktop_model",
        {"model": "gpt-5.4-mini", "effort": "low", "launch_if_needed": False},
    )
    assert attach_call == (
        "add_codex_desktop_attachments",
        {"attachment_paths": ["/tmp/one.png", "/tmp/two.jpg"]},
    )
    assert prompt_call == (
        "add_to_codex_desktop_prompt",
        {"prompt": "What is AutoYou in 2 lines?", "prepend_newline": False},
    )


def test_desktop_exact_tool_shortcut_parses_natural_intent_commands():
    claude_tools = _tool_names(create_claude_desktop_agent("gemini-2.5-flash"))
    codex_tools = _tool_names(create_codex_desktop_agent("gemini-2.5-flash"))

    # "send" submits and finishes; "ask" additionally schedules the async return. The ask_*
    # tools poll the desktop app until it goes idle and re-focus its window on every poll, so
    # routing a plain "send" through them took over the pointer and foreground for minutes on a
    # request that only asked to press send.
    assert desktop_exact_tool_call_from_text("send prompt", claude_tools) == ("send_current_claude_desktop_prompt", {})
    assert desktop_exact_tool_call_from_text("send prompt in claude desktop agent", claude_tools) == ("send_current_claude_desktop_prompt", {})
    assert desktop_exact_tool_call_from_text("send prompt in codex desktop agent", codex_tools) == ("send_current_codex_desktop_prompt", {})
    assert desktop_exact_tool_call_from_text("execute prompt", claude_tools) == ("send_current_claude_desktop_prompt", {})
    assert desktop_exact_tool_call_from_text("send prompt: calculate pi", claude_tools) == ("send_prompt_to_claude_desktop", {"prompt": "calculate pi"})
    assert desktop_exact_tool_call_from_text("ask claude calculate pi", claude_tools) == ("ask_claude_and_return", {"prompt": "calculate pi"})
    assert desktop_exact_tool_call_from_text("status", claude_tools) == ("get_claude_desktop_status", {})
    assert desktop_exact_tool_call_from_text("usage", codex_tools) == ("get_codex_usage", {})


def test_wait_final_response_timeout_survives_stuck_copy(monkeypatch, tmp_path):
    def stuck_copy(agent_dir, *, launch_if_needed=True, expected_prompt=None):
        threading.Event().wait(5)
        return {"status": "success", "response_text": "too late"}

    monkeypatch.setattr(desktop_app_control, "copy_final_response_from_desktop_app", stuck_copy)
    # Completion is now decided by the composer's stop/send sprite, and the copy only runs once
    # the turn is observed finished - so report idle to reach the copy this test is about.
    monkeypatch.setattr(
        desktop_app_control,
        "get_desktop_app_prompt_status",
        lambda agent_dir, **kwargs: {"status": "success", "prompt_status": "idle", "processing": False},
    )
    started = time.monotonic()

    payload = desktop_app_control.wait_until_idle_and_copy_desktop_response(
        tmp_path,
        poll_interval_seconds=1,
        stable_polls=1,
        max_wait_seconds=1,
    )

    assert time.monotonic() - started < 2
    assert payload["status"] == "timeout"
    assert payload["last_copy"]["status"] == "timeout"


def test_copy_final_response_uses_ocr_when_copy_target_missing(monkeypatch, tmp_path):
    prepared = {
        "status": "success",
        "pyautogui": SimpleNamespace(
            scroll=lambda *args, **kwargs: None,
            moveTo=lambda *args, **kwargs: None,
        ),
        "manifest": {"agent_name": "test_agent", "app_id": "test_app"},
        "details": {"platform": "macos"},
        "pack": {"asset_pack_id": "test-pack"},
        "window_bounds": (0, 0, 100, 100),
        "screen_size": (100, 100),
        "focused": True,
        "warnings": [],
    }

    monkeypatch.setattr(
        desktop_app_control,
        "_prepare_desktop_app_interaction",
        lambda agent_dir, *, launch_if_needed=True: prepared,
    )
    monkeypatch.setattr(
        desktop_app_control,
        "_click_target_id",
        lambda prepared, target_id, prefer_image_match=True: {
            "status": "error",
            "message": "Copy response button was not found.",
        },
    )

    def ocr_fallback(prepared, *, expected_prompt=None):
        assert expected_prompt == "What is AutoYou in 2 lines?"
        return {"status": "success", "response_text": "AutoYou pairs devices.\nIt runs agents."}

    monkeypatch.setattr(desktop_app_control, "_copy_final_response_via_ocr", ocr_fallback)

    payload = desktop_app_control.copy_final_response_from_desktop_app(
        tmp_path,
        expected_prompt="What is AutoYou in 2 lines?",
    )

    assert payload["status"] == "success"
    assert payload["copy_click"]["status"] == "error"
    assert payload["response_text"] == "AutoYou pairs devices.\nIt runs agents."


def test_clear_desktop_prompt_prefers_native_new_prompt_target(monkeypatch, tmp_path):
    clicks = []
    prepared = {
        "status": "success",
        "pyautogui": SimpleNamespace(hotkey=lambda *args, **kwargs: clicks.append(("hotkey", args)), press=lambda *args, **kwargs: clicks.append(("press", args))),
        "manifest": {"agent_name": "test_agent", "app_id": "test_app"},
        "details": {"platform": "windows"},
        "pack": {"asset_pack_id": "test-pack"},
        "window_bounds": (0, 0, 100, 100),
        "screen_size": (100, 100),
        "focused": True,
        "warnings": [],
    }

    monkeypatch.setattr(
        desktop_app_control,
        "_prepare_desktop_app_interaction",
        lambda agent_dir, *, launch_if_needed=True: prepared,
    )
    monkeypatch.setattr(desktop_app_control.time, "sleep", lambda *_args, **_kwargs: None)

    def click_target(_prepared, target_id, *, prefer_image_match=True):
        if target_id == "new_prompt":
            return {"status": "success", "target_id": target_id, "point": [6, 15]}
        return {"status": "error", "message": "missing"}

    monkeypatch.setattr(desktop_app_control, "_click_target_id", click_target)

    payload = desktop_app_control.clear_desktop_app_prompt(tmp_path, launch_if_needed=False)

    assert payload["status"] == "success"
    assert payload["action"] == {"type": "click", "target_id": "new_prompt", "point": [6, 15]}
    assert clicks == []


def test_get_desktop_prompt_copies_selection_and_counts_live_images(monkeypatch, tmp_path):
    actions = []
    prepared = {
        "status": "success",
        "pyautogui": SimpleNamespace(hotkey=lambda *args, **kwargs: actions.append(args)),
        "manifest": {"agent_name": "test_agent", "app_id": "test_app"},
        "details": {"platform": "windows"},
        "pack": {"asset_pack_id": "test-pack"},
        "window_bounds": (0, 0, 100, 100),
        "screen_size": (100, 100),
        "focused": True,
        "warnings": [],
    }
    clipboard_reads = iter(
        [
            {"status": "success", "text": "synthetic previous clipboard"},
            {"status": "success", "text": "what is time ?"},
        ]
    )

    monkeypatch.setattr(
        desktop_app_control,
        "_prepare_desktop_app_interaction",
        lambda agent_dir, *, launch_if_needed=True: prepared,
    )
    monkeypatch.setattr(desktop_app_control, "_focus_prompt_target", lambda prepared: {"status": "success", "point": [20, 20]})
    monkeypatch.setattr(desktop_app_control, "_clipboard_read", lambda platform_tag: next(clipboard_reads))
    monkeypatch.setattr(desktop_app_control, "_clipboard_copy", lambda text, platform_tag: True)
    monkeypatch.setattr(
        desktop_app_control,
        "_clipboard_image_info",
        lambda platform_tag: {"status": "success", "image_count": 1, "source": "clipboard_html"},
    )
    monkeypatch.setattr(desktop_app_control.time, "sleep", lambda *_args, **_kwargs: None)

    payload = desktop_app_control.get_desktop_app_prompt(tmp_path, launch_if_needed=False)

    assert payload["status"] == "success"
    assert payload["text"] == "what is time ?"
    assert payload["characters"] == len("what is time ?")
    assert payload["words"] == 4
    assert payload["tokens"] == 4
    assert payload["images"] == 1
    assert payload["attachments"] == 1
    assert ("ctrl", "a") in actions
    assert ("ctrl", "c") in actions


def test_desktop_prompt_status_reads_thinking_from_live_ocr(monkeypatch, tmp_path):
    prepared = {
        "status": "success",
        "pyautogui": SimpleNamespace(),
        "manifest": {"agent_name": "test_agent", "app_id": "test_app"},
        "details": {"platform": "windows"},
        "pack": {
            "asset_pack_id": "test-pack",
            "targets": [{"target_id": "send_button", "normalized_box": [0.8, 0.8, 0.9, 0.9]}],
        },
        "window_bounds": (0, 0, 100, 100),
        "screen_size": (100, 100),
        "focused": True,
        "warnings": [],
    }
    monkeypatch.setattr(
        desktop_app_control,
        "_prepare_desktop_app_interaction",
        lambda agent_dir, *, launch_if_needed=False: prepared,
    )
    monkeypatch.setattr(desktop_app_control, "_focus_prompt_target", lambda prepared: {"status": "success", "point": [20, 20]})
    monkeypatch.setattr(desktop_app_control, "_build_artifact_path", lambda agent_name, label: tmp_path / "status.png")
    monkeypatch.setattr(desktop_app_control, "_capture_desktop_screenshot", lambda destination: {"status": "success", "path": str(destination)})
    monkeypatch.setattr(desktop_app_control, "_crop_region_from_capture", lambda *args, **kwargs: None)
    monkeypatch.setattr(desktop_app_control, "_ocr_image_file", lambda image_path: {"status": "success", "engine": "synthetic", "text": "Thinking"})

    payload = desktop_app_control.get_desktop_app_prompt_status(tmp_path, launch_if_needed=False)

    assert payload["status"] == "success"
    assert payload["prompt_status"] == "processing"
    assert payload["processing"] is True
    assert payload["detection_source"] == "desktop_ocr"


def test_desktop_prompt_status_checks_send_control_when_ocr_is_idle(monkeypatch, tmp_path):
    prepared = {
        "status": "success",
        "pyautogui": SimpleNamespace(),
        "manifest": {"agent_name": "test_agent", "app_id": "test_app"},
        "details": {"platform": "windows"},
        "pack": {
            "asset_pack_id": "test-pack",
            "targets": [{"target_id": "send_button", "normalized_box": [0.8, 0.8, 0.9, 0.9]}],
        },
        "window_bounds": (0, 0, 100, 100),
        "screen_size": (100, 100),
        "focused": True,
        "warnings": [],
    }
    monkeypatch.setattr(
        desktop_app_control,
        "_prepare_desktop_app_interaction",
        lambda agent_dir, *, launch_if_needed=False: prepared,
    )
    monkeypatch.setattr(desktop_app_control, "_focus_prompt_target", lambda prepared: {"status": "success", "point": [20, 20]})
    monkeypatch.setattr(desktop_app_control, "_build_artifact_path", lambda agent_name, label: tmp_path / "status.png")
    monkeypatch.setattr(desktop_app_control, "_capture_desktop_screenshot", lambda destination: {"status": "success", "path": str(destination)})
    monkeypatch.setattr(desktop_app_control, "_ocr_image_file", lambda image_path: {"status": "success", "engine": "synthetic", "text": "Ready"})
    monkeypatch.setattr(
        desktop_app_control,
        "_detect_processing_from_send_control",
        lambda prepared, capture_path: {"processing": True, "source": "send_button_visual"},
    )

    payload = desktop_app_control.get_desktop_app_prompt_status(tmp_path, launch_if_needed=False)

    assert payload["prompt_status"] == "processing"
    assert payload["processing"] is True
    assert payload["detection_source"] == "send_button_visual"


def test_ocr_final_response_extracts_only_after_submitted_prompt():
    payload = desktop_app_control._extract_visible_response_from_ocr_text(
        """
        Some sidebar text
        What is AutoYou in 2 lines?
        AutoYou connects your phone and computer to run AI-powered tasks.
        It keeps local control, media, and agents in one paired workflow.
        Ask for follow-up changes
        """,
        expected_prompt="What is AutoYou in 2 lines?",
    )

    assert payload["status"] == "success"
    assert payload["response_text"] == (
        "AutoYou connects your phone and computer to run AI-powered tasks.\n"
        "It keeps local control, media, and agents in one paired workflow."
    )


def test_ocr_final_response_refuses_busy_state():
    payload = desktop_app_control._extract_visible_response_from_ocr_text(
        """
        What is AutoYou in 2 lines?
        Thinking
        Ask for follow-up changes
        """,
        expected_prompt="What is AutoYou in 2 lines?",
    )

    assert payload["status"] == "error"
    assert "thinking" in payload["message"].lower()


def test_ocr_final_response_uses_latest_prompt_occurrence():
    payload = desktop_app_control._extract_visible_response_from_ocr_text(
        """
        What is AutoYou in 2 lines?
        AutoYou connects devices and agents.
        It runs paired workflows.
        What is AutoYou in 2 lines?
        You've hit your usage limit.
        Ask for follow-up changes
        """,
        expected_prompt="What is AutoYou in 2 lines?",
    )

    assert payload["status"] == "error"
    assert "usage limit" in payload["message"].lower()


def test_parse_desktop_usage_text_reads_limit_state():
    payload = desktop_app_control.parse_desktop_usage_text(
        "You've hit your usage limit. Try again at 11:52 PM."
    )

    assert payload["limit_state"]["limited"] is True
    assert payload["limit_state"]["remaining_percent"] == 0
    assert payload["limit_state"]["reset_label"] == "11:52 PM"


def test_desktop_bridges_are_public_builtins_and_default_off():
    for agent_name in ("claude_desktop_agent", "codex_desktop_agent"):
        assert DEFAULT_AGENT_INSTALL_STATES[agent_name] is False
        assert agent_name in BUILTIN_AGENT_PACKAGE_NAMES
        assert can_install_agent_in_runtime(agent_name, compiled=True) is True


def test_foreign_foreground_window_is_rejected_before_any_input():
    """Keystrokes must never be delivered to a bystander application.

    _focus_window() matches on a window-TITLE substring, so an editor whose title merely
    contains "Claude" satisfies the hint. Verify the owning process instead: a mismatch has to
    abort the interaction rather than type the prompt into whatever holds focus.
    """
    result = _reject_foreign_window(
        {"process_name": "Code.exe", "title": "settings.env - AutoYou - Claude Code"},
        ["Claude.exe"],
        focused=True,
        manifest={"agent_name": "claude_desktop_agent"},
    )

    assert result is not None
    assert result["status"] == "error"
    assert "Code.exe" in result["message"]


def test_matching_foreground_window_is_allowed():
    assert _reject_foreign_window(
        {"process_name": "Claude.exe", "title": "Claude"},
        ["Claude.exe"],
        focused=True,
        manifest={"agent_name": "claude_desktop_agent"},
    ) is None


def test_unfocusable_window_with_unknown_owner_is_rejected():
    assert _reject_foreign_window(
        {},
        ["Claude.exe"],
        focused=False,
        manifest={"agent_name": "claude_desktop_agent"},
    )["status"] == "error"


def test_idle_wait_stops_after_consecutive_copy_failures(monkeypatch):
    """A broken read must surface in seconds instead of burning the whole wait window.

    Every copy failing used to leave response_text empty, so the stability counter never
    advanced and the loop re-ran a hover-and-click sweep for the full max_wait_seconds before
    erroring - then the caller retried, which is how a single request span hours.
    """
    calls = []

    def _always_fails(agent_dir, **kwargs):
        calls.append(kwargs)
        return {"status": "error", "message": "Copy response button was not found."}

    monkeypatch.setattr(
        desktop_app_control, "get_desktop_app_prompt_status", lambda *args, **kwargs: {"status": "success", "prompt_status": "idle"}
    )
    monkeypatch.setattr(
        desktop_app_control, "_copy_final_response_with_timeout", _always_fails
    )

    result = wait_until_idle_and_copy_desktop_response(
        CLAUDE_AGENT_DIR,
        poll_interval_seconds=1.0,
        max_wait_seconds=900.0,
        max_consecutive_failures=3,
    )

    assert result["status"] == "error"
    assert len(calls) == 3
    assert "recalibrate" in result["message"]


def test_idle_wait_failure_streak_resets_on_a_successful_read(monkeypatch):
    """Only *consecutive* failures count, so a transient miss does not abort a live run."""
    responses = [
        {"status": "error", "message": "transient"},
        # First success only records the text; stability needs two further matching reads.
        {"status": "success", "response_text": "done"},
        {"status": "success", "response_text": "done"},
        {"status": "success", "response_text": "done"},
    ]

    def _flaky(agent_dir, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(
        desktop_app_control, "get_desktop_app_prompt_status", lambda *args, **kwargs: {"status": "success", "prompt_status": "idle"}
    )
    monkeypatch.setattr(desktop_app_control, "_copy_final_response_with_timeout", _flaky)

    result = wait_until_idle_and_copy_desktop_response(
        CLAUDE_AGENT_DIR,
        poll_interval_seconds=0.01,
        stable_polls=2,
        max_wait_seconds=30.0,
        max_consecutive_failures=2,
    )

    assert result["status"] == "success"
    assert result["response_text"] == "done"


def test_claude_usage_ring_is_sprite_matched_with_a_corner_fallback():
    """The usage ring is a control, not a detector, so it keeps a coordinate fallback.

    It sits bottom-right below send/stop and expands the usage breakdown above itself. The
    sprite does the locating; the anchored fallback keeps a match miss landing in the right
    corner instead of aborting the read. Anchoring (rather than a normalized click_point) keeps
    it correct as the window resizes.
    """
    manifest = load_desktop_app_manifest(CLAUDE_AGENT_DIR)
    pack = select_desktop_asset_pack(
        manifest,
        platform_tag="windows",
        architecture="x86_64",
        on_date="2026-08-07",
        app_version="1.26832.0",
    )

    button = {t["target_id"]: t for t in pack["targets"]}["usage_menu_button"]
    assert button["expected_image_path"].endswith("usage_ring.png")
    assert _target_image_path(manifest, button).is_file()

    window_bounds = (100, 50, 1700, 1150)
    point = _compute_click_point(
        button,
        coordinate_space="window",
        window_bounds=window_bounds,
        screen_size=(1920, 1200),
        pack=pack,
    )
    assert point is not None
    # The ring is right-most within the COMPOSER CONTROL ROW, not at the window corner: live
    # measurement on 1.26832.0 puts it ~319px in from the right edge (the chat column is inset)
    # and ~31px up from the bottom. An earlier corner-anchored guess aimed at the window corner
    # and would have clicked empty chrome.
    model_selector = {t["target_id"]: t for t in pack["targets"]}["model_selector"]
    assert point[1] > 1150 - 0.08 * 1100, "ring sits in the bottom control row"
    assert point[0] > 100 + 0.5 * 1600, "ring sits in the right half of the window"
    assert point[0] > 100 + model_selector["click_point"][0] * 1600, "ring is right of the model selector"

    usage = pack["usage_controls"]
    assert usage["open_target_id"] == "usage_menu_button"
    assert usage["region_target_id"] == "usage_panel"
    # Claude expands the breakdown on the first click; a Codex-style second toggle would
    # dismiss the popover again.
    assert "toggle_target_id" not in usage

    panel = {t["target_id"]: t for t in pack["targets"]}["usage_panel"]
    box = _target_normalized_box(panel, pack=pack, window_bounds=window_bounds)
    assert box is not None
    left, top, right, bottom = box
    assert right > 0.9 and left > 0.5, "usage panel must sit in the right half of the window"
    assert bottom > top


def test_pyautogui_failsafe_stays_enabled_for_local_desktop_control():
    """The corner-slam abort must remain armed for automation driving the user's own mouse.

    This module moves the real local pointer. With FAILSAFE off there is no way for a human to
    interrupt a misbehaving run, so a bad pack can hold the cursor indefinitely. Remote-desktop
    backends may keep it off - their "corner" is a remote screen - but not here.
    """
    pyautogui = desktop_app_control._lazy_import_pyautogui()
    assert pyautogui.FAILSAFE is True


def test_user_abort_latch_blocks_further_desktop_interaction():
    """One aborted action is not enough: the latch has to survive the agent's retry.

    Otherwise the fail-safe exception kills a single call, the caller reports an error, the
    agent immediately retries, and the pointer is seized again.
    """
    try:
        desktop_app_control.note_user_abort()
        assert desktop_app_control.user_abort_active() is True

        result = desktop_app_control._prepare_desktop_app_interaction(
            CLAUDE_AGENT_DIR, launch_if_needed=False
        )
        assert result["status"] == "error"
        assert result["aborted_by_user"] is True
        # Must bail out before any launching/focusing/pointer work.
        assert "pyautogui" not in result
    finally:
        desktop_app_control.clear_user_abort()

    assert desktop_app_control.user_abort_active() is False


def test_pointer_corner_detection_recognises_every_corner():
    class _FakeGui:
        def __init__(self, pos):
            self._pos = pos

        def position(self):
            return self._pos

        def size(self):
            return (1920, 1080)

    for corner in ((0, 0), (1919, 0), (0, 1079), (1919, 1079), (3, 4)):
        assert desktop_app_control._pointer_in_failsafe_corner(_FakeGui(corner)) is True

    for middle in ((960, 540), (0, 540), (960, 0), (100, 100)):
        assert desktop_app_control._pointer_in_failsafe_corner(_FakeGui(middle)) is False


def test_right_sidebar_inset_only_applies_to_packs_that_declare_it():
    """A pack with no right panel must never have its anchored coordinates shifted.

    The inset is inferred by comparing two probe pixels, which differ in almost any window
    containing content. Defaulting to 310px meant every anchored target in every pack - Claude
    included, which has no Environment sidebar at all - silently resolved 310px to the left.
    """
    window_bounds = (100, 50, 1700, 1150)

    assert desktop_app_control._detect_right_sidebar_inset_px(window_bounds, pack={}) == 0.0
    assert desktop_app_control._detect_right_sidebar_inset_px(
        window_bounds, pack={"layout": {}}
    ) == 0.0

    claude = load_desktop_app_manifest(CLAUDE_AGENT_DIR)
    claude_pack = select_desktop_asset_pack(
        claude,
        platform_tag="windows",
        architecture="x86_64",
        on_date="2026-08-07",
        app_version="1.26832.0",
    )
    assert desktop_app_control._detect_right_sidebar_inset_px(
        window_bounds, pack=claude_pack
    ) == 0.0

    # Codex declares the inset, so the probe stays in play for it.
    codex = load_desktop_app_manifest(CODEX_AGENT_DIR)
    codex_pack = select_desktop_asset_pack(
        codex,
        platform_tag="windows",
        architecture="x86_64",
        on_date="2026-08-07",
        app_version="26.803.41515",
    )
    assert codex_pack["layout"]["content_right_inset_px"] == 310


def _prepared_stub(pack, *, located):
    """Minimal _prepare_desktop_app_interaction result with a scripted image matcher."""
    return {
        "status": "success",
        "manifest": {"agent_name": "claude_desktop_agent"},
        "pack": pack,
        "window_bounds": (0, 0, 1600, 900),
        "pyautogui": object(),
        "focused": True,
        "warnings": [],
        "_located": located,
    }


def _claude_pack():
    manifest = load_desktop_app_manifest(CLAUDE_AGENT_DIR)
    return select_desktop_asset_pack(
        manifest,
        platform_tag="windows",
        architecture="x86_64",
        on_date="2026-08-07",
        app_version="1.26832.0",
    )


def test_stop_sprite_present_means_still_running(monkeypatch):
    """The composer button's identity is the run state - no OCR involved."""
    pack = _claude_pack()
    monkeypatch.setattr(
        desktop_app_control,
        "_locate_target_by_image",
        lambda gui, manifest, target, **kw: (10, 10) if target["target_id"] == "stop_button" else None,
    )
    result = desktop_app_control._detect_processing_from_stop_sprite(
        _prepared_stub(pack, located="stop_button")
    )
    assert result["processing"] is True
    assert result["source"] == "stop_button_sprite"


def test_stop_sprite_absent_means_finished(monkeypatch):
    pack = _claude_pack()
    monkeypatch.setattr(
        desktop_app_control,
        "_locate_target_by_image",
        lambda gui, manifest, target, **kw: (10, 10) if target["target_id"] == "send_button" else None,
    )
    result = desktop_app_control._detect_processing_from_stop_sprite(
        _prepared_stub(pack, located="send_button")
    )
    assert result["processing"] is False
    assert result["source"] == "send_button_sprite"


def test_status_detection_needs_no_ocr_backend(monkeypatch):
    """Status must resolve on machines with no OCR at all.

    With no Vision and no tesseract every text check returns empty, which used to leave the
    status "unknown" - and an unknown state is what invited callers to resend the prompt.
    """
    pack = _claude_pack()
    monkeypatch.setattr(
        desktop_app_control,
        "_ocr_image_file",
        lambda *a, **k: {"status": "error", "engine": None, "text": ""},
    )
    monkeypatch.setattr(
        desktop_app_control,
        "_capture_desktop_screenshot",
        lambda path: {"status": "success"},
    )
    monkeypatch.setattr(
        desktop_app_control,
        "_locate_target_by_image",
        lambda gui, manifest, target, **kw: (10, 10) if target["target_id"] == "stop_button" else None,
    )

    result = desktop_app_control._inspect_desktop_prompt_status(_prepared_stub(pack, located="stop"))
    assert result["prompt_status"] == "processing"
    assert result["ocr_engine"] is None


def test_unknown_state_never_reports_idle_and_warns_against_resending(monkeypatch):
    """An undeterminable state must not look like a finished turn.

    Reporting "idle" when nothing could be observed is how a caller concludes the turn is over
    and submits the prompt again, stacking duplicate runs.
    """
    calls = {"n": 0}

    def _unknown(agent_dir, **kwargs):
        calls["n"] += 1
        return {"status": "success", "prompt_status": "unknown", "processing": None}

    monkeypatch.setattr(desktop_app_control, "get_desktop_app_prompt_status", _unknown)

    result = desktop_app_control.wait_until_idle_and_copy_desktop_response(
        CLAUDE_AGENT_DIR,
        poll_interval_seconds=0.01,
        max_wait_seconds=30.0,
        max_consecutive_failures=3,
    )
    assert result["status"] == "error"
    assert calls["n"] == 3
    assert "do not resend" in result["message"].lower()


def test_idle_state_copies_exactly_once(monkeypatch):
    """Completion is detected by state, then the response is read a single time.

    The old loop copied repeatedly until the text stopped changing, so every poll dragged the
    pointer through a hover-and-click sweep.
    """
    copies = {"n": 0}

    monkeypatch.setattr(
        desktop_app_control,
        "get_desktop_app_prompt_status",
        lambda agent_dir, **kw: {"status": "success", "prompt_status": "idle", "processing": False},
    )

    def _copy(agent_dir, **kwargs):
        copies["n"] += 1
        return {"status": "success", "response_text": "the answer"}

    monkeypatch.setattr(desktop_app_control, "_copy_final_response_with_timeout", _copy)

    result = desktop_app_control.wait_until_idle_and_copy_desktop_response(
        CLAUDE_AGENT_DIR,
        poll_interval_seconds=0.01,
        stable_polls=1,
        max_wait_seconds=30.0,
    )
    assert result["status"] == "success"
    assert result["response_text"] == "the answer"
    assert copies["n"] == 1


def test_desktop_shortcut_dispatches_once_per_invocation():
    """"send prompt: ..." must submit exactly once, however many model turns follow.

    _extract_request_text() flattens the whole conversation, so the triggering phrase is still
    present on every later turn of the same invocation. Unguarded, the callback re-injected the
    same tool call each time the model was consulted - send, tool result, model, send again -
    which both duplicated the message in the desktop app and stopped the model ever producing a
    final answer, so it ran until a tool-call budget ran out.
    """
    from google.genai import types as genai_types

    from autoyou_agents.shared_tools.desktop_app_agent_shortcuts import (
        make_desktop_exact_tool_callback,
    )

    callback = make_desktop_exact_tool_callback(
        "claude_desktop_agent",
        ("send_prompt_to_claude_desktop", "get_claude_usage", "get_claude_desktop_status"),
    )

    def _request(text):
        return SimpleNamespace(
            contents=[genai_types.Content(role="user", parts=[genai_types.Part(text=text)])]
        )

    import asyncio

    state = {}
    context = SimpleNamespace(state=state, invocation_id="inv-1")
    request = _request("send prompt: what is my usage")

    dispatched = [
        asyncio.run(callback(context, request)) is not None for _ in range(4)
    ]
    assert dispatched == [True, False, False, False], (
        f"prompt was dispatched {sum(dispatched)} times for one request"
    )

    # A new user turn must still be able to dispatch.
    next_context = SimpleNamespace(state=state, invocation_id="inv-2")
    assert asyncio.run(callback(next_context, request)) is not None


def test_desktop_shortcut_without_invocation_id_still_dispatches():
    """Missing invocation metadata must not disable the shortcut entirely."""
    from google.genai import types as genai_types

    from autoyou_agents.shared_tools.desktop_app_agent_shortcuts import (
        make_desktop_exact_tool_callback,
    )

    import asyncio

    callback = make_desktop_exact_tool_callback(
        "claude_desktop_agent", ("send_prompt_to_claude_desktop",)
    )
    context = SimpleNamespace(state={}, invocation_id="")
    request = SimpleNamespace(
        contents=[
            genai_types.Content(
                role="user", parts=[genai_types.Part(text="send prompt: hello there")]
            )
        ]
    )
    assert asyncio.run(callback(context, request)) is not None


def test_merge_pack_targets_preserves_inherited_coordinates_on_derived_sprites():
    from autoyou_agents.shared_tools.desktop_app_manifest import _merge_pack_targets

    base_targets = [
        {
            "target_id": "composer_box",
            "click_point": [0.5614, 0.8969],
            "normalized_box": [0.16, 0.86, 0.95, 0.93],
            "description": "Base composer",
        },
        {
            "target_id": "send_button",
            "click_point": [0.8712, 0.9536],
            "description": "Base send",
        },
    ]
    derived_targets = [
        {
            "target_id": "composer_box",
            "reference_sprite": "macos/26.730.61639/sprites/composer_box.png",
        },
        {
            "target_id": "toggle_sidebar",
            "reference_sprite": "macos/26.730.61639/sprites/toggle_sidebar.png",
        },
    ]

    merged = _merge_pack_targets(base_targets, derived_targets)
    by_id = {t["target_id"]: t for t in merged}

    assert by_id["composer_box"]["click_point"] == [0.5614, 0.8969]
    assert by_id["composer_box"]["normalized_box"] == [0.16, 0.86, 0.95, 0.93]
    assert (
        by_id["composer_box"]["reference_sprite"]
        == "macos/26.730.61639/sprites/composer_box.png"
    )
    assert by_id["send_button"]["click_point"] == [0.8712, 0.9536]
    assert (
        by_id["toggle_sidebar"]["reference_sprite"]
        == "macos/26.730.61639/sprites/toggle_sidebar.png"
    )


def test_codex_macos_26_730_pack_retains_inherited_composer_coordinates():
    manifest = load_desktop_app_manifest(CODEX_AGENT_DIR)
    assert manifest is not None

    pack_26730 = next(
        (
            p
            for p in manifest.get("asset_packs", [])
            if p.get("asset_pack_id") == "codex-macos-26.730-2026-12"
        ),
        None,
    )
    assert pack_26730 is not None

    targets_by_id = {t["target_id"]: t for t in pack_26730.get("targets", [])}

    # Verify key controls retain click_point / normalized_box from inherited packs
    for tid in (
        "composer_box",
        "send_button",
        "attach_button",
        "permissions_selector",
        "model_selector",
        "copy_response_button",
    ):
        assert (
            tid in targets_by_id
        ), f"Target '{tid}' missing in merged 26.730 pack"
        assert (
            targets_by_id[tid].get("click_point") is not None
            or targets_by_id[tid].get("normalized_box") is not None
        ), f"Target '{tid}' lost inherited coordinates in 26.730 pack"
