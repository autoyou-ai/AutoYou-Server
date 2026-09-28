# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import os
import sys

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from autoyou_agents.shared_tools.agent_identity import (
    ROOT_AGENT_NAME,
    format_agent_display_name,
    is_root_agent_name,
    resolve_runtime_agent_name,
)


def test_resolve_runtime_agent_name_normalizes_builtin_directory_names():
    assert resolve_runtime_agent_name("admin_agent") == "autoyou_admin_agent"
    assert resolve_runtime_agent_name("ads_watching_agent") == "autoyou_ads_watching_agent"
    assert resolve_runtime_agent_name("agent_builder_agent") == "autoyou_agent_builder_agent"
    assert resolve_runtime_agent_name("audio_agent") == "autoyou_audio_agent"
    assert resolve_runtime_agent_name("browser_agent") == "autoyou_browser_agent"
    assert resolve_runtime_agent_name("cli_agent") == "autoyou_cli_agent"
    assert resolve_runtime_agent_name("coding_agent") == "autoyou_coding_agent"
    assert resolve_runtime_agent_name("data_collector_agent") == "autoyou_data_collector_agent"
    assert resolve_runtime_agent_name("donation_agent") == "autoyou_donation_agent"
    assert resolve_runtime_agent_name("earnings_agent") == "autoyou_earnings_agent"
    assert resolve_runtime_agent_name("files_agent") == "autoyou_files_agent"
    assert resolve_runtime_agent_name("fine_tuning_agent") == "autoyou_fine_tuning_agent"
    assert resolve_runtime_agent_name("hermes_agent") == "autoyou_hermes_agent"
    assert resolve_runtime_agent_name("hosting_agent") == "autoyou_hosting_agent"
    assert resolve_runtime_agent_name("internet_agent") == "autoyou_internet_agent"
    assert resolve_runtime_agent_name("location_agent") == "autoyou_location_agent"
    assert resolve_runtime_agent_name("mac_security_agent") == "autoyou_mac_security_agent"
    assert resolve_runtime_agent_name("media_generation_agent") == "autoyou_media_generation_agent"
    assert resolve_runtime_agent_name("memory_agent") == "autoyou_memory_agent"
    assert resolve_runtime_agent_name("model_picker_agent") == "autoyou_model_picker_agent"
    assert resolve_runtime_agent_name("notes_agent") == "autoyou_notes_agent"
    assert resolve_runtime_agent_name("notify_agent") == "autoyou_notify_agent"
    assert resolve_runtime_agent_name("openclaw_agent") == "autoyou_openclaw_agent"
    assert resolve_runtime_agent_name("page_agent") == "autoyou_page_agent"
    assert resolve_runtime_agent_name("remote_desktop_agent") == "autoyou_remote_desktop_agent"
    assert resolve_runtime_agent_name("skills_agent") == "autoyou_skills_agent"
    assert resolve_runtime_agent_name("education_agent") == "autoyou_education_agent"
    assert resolve_runtime_agent_name("tasks_agent") == "autoyou_tasks_agent"
    assert resolve_runtime_agent_name("voice_training_agent") == "autoyou_voice_training_agent"
    assert resolve_runtime_agent_name("website_agent") == "autoyou_website_agent"
    assert resolve_runtime_agent_name("win_security_agent") == "autoyou_win_security_agent"
    assert resolve_runtime_agent_name("demo_agent") == "demo_agent"


def test_legacy_streaming_agent_name_resolves_to_education_agent():
    assert resolve_runtime_agent_name("streaming_agent") == "autoyou_education_agent"


def test_agent_identity_root_aliases_share_one_canonical_name():
    assert is_root_agent_name("root")
    assert is_root_agent_name("root_agent")
    assert is_root_agent_name("AutoYou AI Agent")
    assert is_root_agent_name("fallback_autoyou_agent")
    assert resolve_runtime_agent_name("autoyou_agent") == ROOT_AGENT_NAME


def test_format_agent_display_name_humanizes_runtime_names():
    assert format_agent_display_name("autoyou_page_agent") == "Page"
    assert format_agent_display_name("autoyou_admin_agent") == "Admin"
    assert format_agent_display_name("autoyou_audio_agent") == "Audio"
    assert format_agent_display_name("autoyou_cli_agent") == "CLI"
    assert format_agent_display_name("autoyou_memory_agent") == "Memory"
    assert format_agent_display_name("autoyou_coding_agent") == "Coding"
    assert format_agent_display_name("autoyou_agent_builder_agent") == "Agent Builder"
    assert format_agent_display_name("website_agent") == "Website"
    assert format_agent_display_name("autoyou_website_agent") == "Website"
    assert format_agent_display_name("agent_website_builder_agent") == "Website"
    assert format_agent_display_name("frontend_proxy_agent") == "Website"
    assert format_agent_display_name("fallback_autoyou_agent") == "Root"
