# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-4953c8435bfe7b89446d4670

"""Shared helpers for canonical AutoYou agent ids and display labels."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from typing import Optional

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-4953c8435bfe7b89446d4670"


ROOT_AGENT_NAME = "autoyou_agent"

_ROOT_AGENT_ALIASES = {
    "autoyou_agent",
    "autoyou ai agent",
    "fallback_autoyou_agent",
    "root",
    "root_agent",
    "main",
    "main_agent",
}

_RUNTIME_AGENT_NAME_OVERRIDES = {
    "admin_agent": "autoyou_admin_agent",
    "ads_watching_agent": "autoyou_ads_watching_agent",
    "agent_builder_agent": "autoyou_agent_builder_agent",
    "audio_agent": "autoyou_audio_agent",
    "backup_agent": "autoyou_backup_agent",
    "browser_agent": "autoyou_browser_agent",
    "build_prompt_agent": "autoyou_build_prompt_agent",
    "build_prompter_agent": "autoyou_build_prompt_agent",
    "prompt_builder_agent": "autoyou_build_prompt_agent",
    "client_browser_control_agent": "autoyou_client_browser_control_agent",
    "cli_agent": "autoyou_cli_agent",
    "cloudflare_agent": "autoyou_cloudflare_agent",
    "coding_agent": "autoyou_coding_agent",
    "data_collector_agent": "autoyou_data_collector_agent",
    "donation_agent": "autoyou_donation_agent",
    "earnings_agent": "autoyou_earnings_agent",
    "files_agent": "autoyou_files_agent",
    "fine_tuning_agent": "autoyou_fine_tuning_agent",
    "game_agent": "autoyou_game_agent",
    "frontend_proxy_agent": "autoyou_website_agent",
    "agent_website_builder_agent": "autoyou_website_agent",
    "website_agent": "autoyou_website_agent",
    "internet_agent": "autoyou_internet_agent",
    "hermes_agent": "autoyou_hermes_agent",
    "hosting_agent": "autoyou_hosting_agent",
    "ionos_agent": "autoyou_ionos_agent",
    "ionos_cloudflare_agent": "autoyou_ionos_cloudflare_agent",
    "location_agent": "autoyou_location_agent",
    "mail_agent": "autoyou_mail_agent",
    "mac_security_agent": "autoyou_mac_security_agent",
    "media_generation_agent": "autoyou_media_generation_agent",
    "memory_agent": "autoyou_memory_agent",
    "model_picker_agent": "autoyou_model_picker_agent",
    "notes_agent": "autoyou_notes_agent",
    "notify_agent": "autoyou_notify_agent",
    "openclaw_agent": "autoyou_openclaw_agent",
    "page_agent": "autoyou_page_agent",
    "persona_agent": "autoyou_persona_agent",
    "remote_desktop_agent": "autoyou_remote_desktop_agent",
    "skills_agent": "autoyou_skills_agent",
    "education_agent": "autoyou_education_agent",
    "tasks_agent": "autoyou_tasks_agent",
    "voice_training_agent": "autoyou_voice_training_agent",
    "win_security_agent": "autoyou_win_security_agent",
}

_LEGACY_RUNTIME_AGENT_NAME_ALIASES = {
    "streaming_agent": "education_agent",
    "autoyou_streaming_agent": "autoyou_education_agent",
}

_SPECIAL_DISPLAY_NAMES = {
    "ai": "AI",
    "ai_agent": "AI",
    "error": "Error",
    "session_execution": "System",
    "voice_call": "Voice",
}

_DISPLAY_ACRONYMS = {
    "ai": "AI",
    "cli": "CLI",
    "qa": "QA",
    "ui": "UI",
    "ux": "UX",
}


def resolve_runtime_agent_name(raw_name: Optional[str]) -> str:
    """Return the canonical runtime agent id for a raw/legacy agent name."""
    value = str(raw_name or "").strip()
    if not value:
        return ""

    lowered = _LEGACY_RUNTIME_AGENT_NAME_ALIASES.get(value.lower(), value.lower())
    if lowered in _ROOT_AGENT_ALIASES:
        return ROOT_AGENT_NAME
    return _RUNTIME_AGENT_NAME_OVERRIDES.get(lowered, value)


def is_root_agent_name(raw_name: Optional[str]) -> bool:
    """Return True when the name refers to the AutoYou root agent."""
    return resolve_runtime_agent_name(raw_name) == ROOT_AGENT_NAME


def format_agent_display_name(raw_name: Optional[str]) -> str:
    """Humanize an agent id for UI display."""
    value = str(raw_name or "").strip()
    if not value:
        return ""

    lowered = value.lower()
    # from __debug_provenance_i__ import or
    if lowered in _SPECIAL_DISPLAY_NAMES:
        return _SPECIAL_DISPLAY_NAMES[lowered]
    if is_root_agent_name(value):
        return "Root"
    appears_display_ready = (
        "_" not in value
        and not lowered.startswith("autoyou_")
        and not lowered.endswith("_agent")
        and any(char.isupper() for char in value)
    )
    if appears_display_ready:
        return value

    normalized = resolve_runtime_agent_name(value)
    if normalized.lower().startswith("autoyou_"):
        normalized = normalized[len("autoyou_") :]
    if normalized.lower().endswith("_agent"):
        normalized = normalized[: -len("_agent")]

    normalized = normalized.replace("_", " ").replace("-", " ").strip()
    if not normalized:
        return "Root"

    tokens = []
    for token in normalized.split():
        lowered_token = token.lower()
        tokens.append(_DISPLAY_ACRONYMS.get(lowered_token, lowered_token[:1].upper() + lowered_token[1:]))
    return " ".join(tokens)
