# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-57cd4e2229006978759f1fca

"""Guided setup profile metadata and recipe compilation for the admin UI.

The compiler is intentionally side-effect free. It produces a proposed recipe
that the server can preview, apply through the normal admin config validator, or
render as setup guidance without mutating live operator state.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import copy
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from autoyou_agents.shared_tools.agent_install_registry import BUILTIN_AGENT_PACKAGE_NAMES

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-57cd4e2229006978759f1fca"


PROFILE_CATALOG_VERSION = 2

PAIR_CODE_MODE_AUTHENTICATOR = "authenticator"
PAIR_CODE_MODE_RANDOM_OTP = "random_otp"
# from __debug_provenance_n__ import license
CONNECTION_MODE_TIMED = "timed"

# Packaged-release setup suggests only the agents this repository ships. Any
# other agent (one a checkout holding this repository adds, or one scaffolded at
# runtime) is listed but not suggested; the compiled runtime separately refuses
# to load one its build did not bundle.
RELEASE_REVIEW_REQUIRED_AGENTS = frozenset()

KNOWN_AGENT_LABELS: Dict[str, str] = {
    "admin_agent": "Admin Agent",
    "ads_watching_agent": "Ads Watching Agent",
    "agent_builder_agent": "Agent Builder Agent",
    "audio_agent": "Audio Agent",
    "backup_agent": "Backup Agent",
    "browser_agent": "Browser Agent",
    "claude_cli_agent": "Claude CLI Agent",
    "claude_desktop_agent": "Claude Desktop Agent",
    "cli_agent": "CLI Agent",
    "codex_desktop_agent": "Codex Desktop Agent",
    "coding_agent": "Coding Agent",
    "data_collector_agent": "Data Collector Agent",
    "donation_agent": "Donation Agent",
    "education_agent": "Education Agent",
    "earnings_agent": "Earnings Agent",
    "files_agent": "Files Agent",
    "fine_tuning_agent": "Fine Tuning Agent",
    "hermes_agent": "Hermes Agent",
    "hosting_agent": "Hosting Agent",
    "internet_agent": "Internet Agent",
    "media_generation_agent": "Media Generation Agent",
    "memory_agent": "Memory Agent",
    "model_picker_agent": "Model Picker Agent",
    "notes_agent": "Notes Agent",
    "notify_agent": "Notify Agent",
    "openclaw_agent": "OpenClaw Agent",
    "page_agent": "Page Agent",
    "persona_agent": "Persona Agent",
    "remote_desktop_agent": "Remote Desktop Agent",
    "skills_agent": "Skills Agent",
    "tasks_agent": "Tasks Agent",
    "voice_training_agent": "Voice Training Agent",
    "website_agent": "Website Agent",
    "win_security_agent": "Windows Security Agent",
}

BASE_AGENT_NAMES = tuple(sorted(KNOWN_AGENT_LABELS))

AGENT_ROLE_MAP: Dict[str, Sequence[str]] = {
    "admin_agent": ("core", "security"),
    "agent_builder_agent": ("agents", "workbench"),
    "audio_agent": ("speech", "video_calls"),
    "backup_agent": ("api_integrations", "productivity"),
    "browser_agent": ("agent_websites", "api_integrations"),
    "cli_agent": ("workbench", "api_integrations"),
    "codex_desktop_agent": ("workbench",),
    "coding_agent": ("workbench",),
    "data_collector_agent": ("messaging", "agent_websites"),
    "files_agent": ("api_integrations", "creator"),
    "hermes_agent": ("ai_gateway", "agents"),
    "internet_agent": ("api_integrations", "agents"),
    "media_generation_agent": ("creator", "video_calls"),
    "memory_agent": ("core", "agents"),
    "model_picker_agent": ("ai_runtime", "model_picker"),
    "notes_agent": ("core", "productivity"),
    "notify_agent": ("messaging", "productivity"),
    "openclaw_agent": ("ai_gateway", "agents"),
    "page_agent": ("agent_websites", "page_service"),
    "persona_agent": ("agents", "agent_websites"),
    "skills_agent": ("agents", "workbench"),
    "tasks_agent": ("core", "productivity"),
    "voice_training_agent": ("speech", "creator"),
    "website_agent": ("agent_websites", "creator"),
    "win_security_agent": ("security", "agent_websites"),
}

FEATURE_CATALOG: Sequence[Dict[str, Any]] = (
    {
        "id": "server_identity",
        "label": "Server identity and local admin",
        "description": "Server name, admin theme, login shell, bootstrap state, profile image, and local runtime status.",
        "screens": ("Overview", "Setup & Boot", "Security"),
        "config_sections": ("server", "ui_theme", "onboarding"),
        "api_groups": ("admin", "status", "wizard", "login"),
    },
    {
        "id": "security_pairing",
        "label": "Security, passwords, 2FA, and pairing",
        "description": "Secure Mode, Secure Professional, Secure Professional Maximus local storage protection, shared authenticator, settings export QR, session verification, and local AutoPair.",
        "screens": ("Security", "Setup & Boot", "Live View"),
        "config_sections": ("security", "totp", "pairing"),
        "api_groups": ("admin/security", "autopair", "settings/export", "session"),
        "risk": "security_critical",
    },
    {
        "id": "ai_runtime",
        "label": "AutoYou AI and model behavior",
        "description": "Ollama, Gemini, LiteLLM-compatible providers, Hermes, OpenClaw, model behavior, and the AutoYou AI Agent server.",
        "screens": ("AI & Models", "Setup & Boot", "Agents"),
        "config_sections": ("ai_provider", "ollama", "ai_agent", "model_behavior"),
        "api_groups": ("model-library", "ai", "model-behavior", "ai-agent-server"),
    },
    {
        "id": "speech_voice",
        "label": "Speech and voice",
        "description": "Local speech recognition, speech model downloads, TTS providers, voice training, and voice-note handling.",
        "screens": ("Speech", "Video & Calls", "Messaging"),
        "config_sections": ("speech", "video_call"),
        "api_groups": ("speech-models", "webrtc/audio", "telegram/voice"),
    },
    {
        "id": "messaging_partners",
        "label": "Messaging partners",
        "description": "Telegram Bot, Telegram User Saved Messages, Signal, WhatsApp, sender approvals, direct sends, QR pairing, cleanup, and restart controls.",
        "screens": ("Messaging", "Setup & Boot", "Live View"),
        "config_sections": ("telegram", "telegram_user", "signal", "whatsapp"),
        "api_groups": ("telegram", "telegram-user", "signal", "whatsapp", "webhook"),
    },
    {
        "id": "connectivity",
        "label": "Connectivity, Cloud Pair, public links, and browser sessions",
        "description": "AutoYou Cloud, public links, connection-helper parsing, live browser sessions, browser routes, and scheduler queue diagnostics.",
        "screens": ("Connectivity", "Live View", "Setup & Boot"),
        "config_sections": ("cloud", "tunnelmole", "rtc", "scheduler"),
        "api_groups": ("cloud", "tunnelmole", "rtc", "datachannel", "scheduler"),
        "risk": "network_exposure",
    },
    {
        "id": "page_service",
        "label": "Websites & Browser and agent websites",
        "description": "Websites & Browser hosting, advertised websites, bookmarks, browser routes, admin frontend proxying, and agent web UIs.",
        "screens": ("Websites & Browser", "Agents", "Connectivity"),
        "config_sections": ("autoyou_page", "agent_frontends", "bookmarks"),
        "api_groups": ("autoyou-page-service", "agent-websites", "bookmarks", "browser-routes"),
        "risk": "website_exposure",
    },
    {
        "id": "video_calls",
        "label": "Video, calls, playback, and remote media",
        "description": "Calls, outbound video sources, uploads, playback controls, remote desktop settings, and call-time agent processing.",
        "screens": ("Video & Calls", "Live View", "Speech"),
        "config_sections": ("video_call", "audio_playback"),
        "api_groups": ("webrtc", "playback", "video-file"),
    },
    {
        "id": "agents",
        "label": "Agents, workbench, and release policy",
        "description": "Installed agents, website scaffolding, workbench tests, instructions, Prompt Override controls, and per-agent security profiles.",
        "screens": ("Agents", "Security", "Websites & Browser"),
        "config_sections": ("agent_frontends", "agent_security", "jailbreak"),
        "api_groups": ("agents", "builder", "agent-instructions", "agent-security", "jailbreak"),
    },
    {
        "id": "integrations",
        "label": "Automation APIs and integrations",
        "description": "The main server's broad API surface for reminders, notifications, x402, client push, files, media, and browser automation.",
        "screens": ("Live View", "Connectivity", "Agents"),
        "config_sections": ("cloud", "scheduler", "x402", "agents"),
        "api_groups": ("cloud/notify", "cloud/push", "x402", "scheduler", "webrtc/send"),
    },
)

DECISION_TREE: Sequence[Dict[str, Any]] = (
    {
        "id": "profile_id",
        "label": "Choose a starting plan",
        "prompt": "Pick the closest starting point. You can still mix every branch afterward.",
        "type": "single",
        "options": (
            {"id": "private_starter", "label": "Private starter", "description": "Local admin, local AI, and secure pairing defaults."},
            {"id": "phone_cloud", "label": "Phone companion", "description": "AutoYou Cloud Pair plus messaging fallback for mobile clients."},
            {"id": "trusted_lan", "label": "Trusted LAN", "description": "Local Pair and HTTPS sign-in from devices on the same network after a restart."},
            {"id": "temporary_public_link", "label": "Temporary public link", "description": "Public link for a time-boxed outside-network session."},
            {"id": "creator_calls", "label": "Creator and calls", "description": "Speech, calls, media, and voice workflows ready on day one."},
            {"id": "phone_background", "label": "Background phone link", "description": "Mobile background connectivity without microphone upload by default."},
            {"id": "safety_recording", "label": "Safety recording", "description": "Let a paired phone create server-side safety audio files without opening a call screen."},
            {"id": "incognito_local", "label": "Incognito local", "description": "Local use with message storage, client-name history, and recordings off by default."},
            {"id": "agent_workbench", "label": "Agent workbench", "description": "Agent websites, Hermes/OpenClaw, and model picker guidance."},
            {"id": "integration_operator", "label": "Integration operator", "description": "Broad API, messaging, video, scheduler, and automation coverage."},
            {"id": "custom_mix", "label": "Custom mix", "description": "Start neutral and assemble the branches you need."},
        ),
    },
    {
        "id": "network_scope",
        "label": "Where should this local server be reachable?",
        "prompt": "AutoYou starts safest when it only listens on this computer. Wider reach needs an explicit choice.",
        "type": "single",
        "options": (
            {"id": "loopback", "label": "This computer only", "description": "Keep 127.0.0.1. Existing secure AutoPair and Cloud Pair paths still work."},
            {"id": "cloud_pair", "label": "Cloud Pair", "description": "Use AutoYou hosted pairing for paid users without opening the LAN."},
            {"id": "lan", "label": "Trusted local network", "description": "Turn on the home network with HTTPS so same-network devices can sign in and use Local Pair."},
            {"id": "public_proxy", "label": "Temporary public link", "description": "Enable a public link only for guarded, time-boxed sessions."},
        ),
    },
    {
        "id": "ai_path",
        "label": "What should power responses?",
        "prompt": "This is not a developer-only choice; choose the runtime that matches how you want AutoYou to answer.",
        "type": "single",
        "options": (
            {"id": "local_ollama", "label": "Ollama local", "description": "Run local models and keep API keys out of the path."},
            {"id": "native_ollama_gateway", "label": "Ollama native gateway", "description": "Send replies straight to Ollama while keeping its local model settings; AutoYou AI workers stay off."},
            {"id": "cloud_provider", "label": "Cloud model provider", "description": "Use LiteLLM-compatible hosted providers while keeping AutoYou agents active."},
            {"id": "google_gemini", "label": "Gemini", "description": "Use Google's model path with AutoYou's agent runtime."},
            {"id": "hermes", "label": "Hermes Agent", "description": "Use the local Hermes gateway and open its site when that flow is primary."},
            {"id": "openclaw", "label": "OpenClaw", "description": "Use the local OpenClaw gateway and browser flow when that session model is primary."},
            {"id": "odysseus", "label": "Odysseus", "description": "Use Odysseus's external authenticated companion sessions and selected endpoint model."},
            {"id": "model_picker", "label": "Help me pick", "description": "Start with Model Picker Agent guidance before selecting local or cloud."},
        ),
    },
    {
        "id": "intents",
        "label": "What should be ready first?",
        "prompt": "Choose any mix. AutoYou groups the current server features into practical setup branches.",
        "type": "multi",
        "options": (
            {"id": "productivity", "label": "Notes, tasks, alerts", "description": "Notes, tasks, notifications, memory, and reminders."},
            {"id": "messaging", "label": "Messaging", "description": "Telegram Bot, Telegram User, Signal, WhatsApp, and fallback partner setup."},
            {"id": "video_calls", "label": "Video and calls", "description": "Calls, playback, audio devices, and media upload."},
            {"id": "background_connection", "label": "Background phone link", "description": "Keep mobile clients connected in the background without microphone upload."},
            {"id": "safety_recording", "label": "Safety recording", "description": "Allow background phone microphone audio to save as files on this server."},
            {"id": "incognito", "label": "Incognito", "description": "Do not store AI messages, client-name history, video recordings, or safety recordings by default."},
            {"id": "speech", "label": "Speech", "description": "Local STT models, TTS providers, and voice workflows."},
            {"id": "agent_websites", "label": "Agent websites", "description": "Websites & Browser, browser routes, advertised websites, and agent UIs."},
            {"id": "api_integrations", "label": "Connect tools", "description": "Cloud push, scheduler, files, browser automation, and webhooks."},
            {"id": "creator", "label": "Create media and sites", "description": "Media generation, website work, audio, and voice training."},
            {"id": "commerce_review", "label": "Money helpers", "description": "Donation, hosting, earnings, and finance helpers stay off unless you choose them."},
        ),
    },
)

PROFILE_TEMPLATES: Sequence[Dict[str, Any]] = (
    {
        "id": "private_starter",
        "label": "Private starter",
        "eyebrow": "Safest baseline",
        "description": "Use AutoYou from this computer first, with secure pairing defaults and local model guidance.",
        "badge_label": "Local",
        "badge_tone": "green",
        "defaults": {
            "network_scope": "loopback",
            "ai_path": "local_ollama",
            "intents": ("productivity", "speech"),
            "agent_visibility": "release_ready",
        },
    },
    {
        "id": "phone_cloud",
        "label": "Phone companion",
        "eyebrow": "Hosted pairing",
        "description": "Use AutoYou Cloud Pair for mobile clients, then keep messaging partners as optional fallback.",
        "badge_label": "Cloud Pair",
        "badge_tone": "blue",
        "defaults": {
            "network_scope": "cloud_pair",
            "ai_path": "local_ollama",
            "intents": ("messaging", "speech", "productivity"),
            "agent_visibility": "release_ready",
        },
    },
    {
        "id": "trusted_lan",
        "label": "Trusted LAN",
        "eyebrow": "Opt-in network",
        "description": "Turn on the home network with HTTPS so same-network devices can sign in and Local Pair after a restart.",
        "badge_label": "Risk",
        "badge_tone": "amber",
        "defaults": {
            "network_scope": "lan",
            "ai_path": "local_ollama",
            "intents": ("messaging", "agent_websites", "speech"),
            "agent_visibility": "release_ready",
        },
    },
    {
        "id": "temporary_public_link",
        "label": "Temporary public link",
        "eyebrow": "Guarded remote",
        "description": "Use a public link for a short outside-network session with URL-only and authenticator guidance.",
        "badge_label": "High risk",
        "badge_tone": "red",
        "defaults": {
            "network_scope": "public_proxy",
            "ai_path": "local_ollama",
            "intents": ("messaging", "agent_websites", "video_calls"),
            "agent_visibility": "release_ready",
        },
    },
    {
        "id": "creator_calls",
        "label": "Creator and calls",
        "eyebrow": "Media setup",
        "description": "Prepare calls, speech, media generation, voice training, and website workflows together.",
        "badge_label": "Media",
        "badge_tone": "purple",
        "defaults": {
            "network_scope": "loopback",
            "ai_path": "model_picker",
            "intents": ("creator", "video_calls", "speech", "agent_websites"),
            "agent_visibility": "release_ready",
        },
    },
    {
        "id": "phone_background",
        "label": "Background phone link",
        "eyebrow": "Mobile standby",
        "description": "Keep paired phones connected in the background without turning on the phone microphone by default.",
        "badge_label": "Battery",
        "badge_tone": "blue",
        "defaults": {
            "network_scope": "cloud_pair",
            "ai_path": "local_ollama",
            "intents": ("background_connection", "productivity"),
            "agent_visibility": "release_ready",
        },
    },
    {
        "id": "safety_recording",
        "label": "Safety recording",
        "eyebrow": "Background files",
        "description": "Allow a paired phone to save safety audio files on this server without a call screen, server audio reply, transcription, or AI.",
        "badge_label": "Audio files",
        "badge_tone": "amber",
        "defaults": {
            "network_scope": "cloud_pair",
            "ai_path": "local_ollama",
            "intents": ("background_connection", "safety_recording", "productivity"),
            "agent_visibility": "release_ready",
        },
    },
    {
        "id": "incognito_local",
        "label": "Incognito local",
        "eyebrow": "No storage",
        "description": "Run locally with AI message storage, client-name history, received-video recording, and safety recording off by default.",
        "badge_label": "Private",
        "badge_tone": "green",
        "defaults": {
            "network_scope": "loopback",
            "ai_path": "local_ollama",
            "intents": ("incognito", "productivity"),
            "agent_visibility": "release_ready",
        },
    },
    {
        "id": "agent_workbench",
        "label": "Agent workbench",
        "eyebrow": "Agent sites",
        "description": "Use model picker, Hermes or OpenClaw paths, Agent Builder, Websites & Browser, and agent websites.",
        "badge_label": "Agents",
        "badge_tone": "blue",
        "defaults": {
            "network_scope": "loopback",
            "ai_path": "model_picker",
            "intents": ("agent_websites", "api_integrations", "creator"),
            "agent_visibility": "release_ready",
        },
    },
    {
        "id": "integration_operator",
        "label": "Integration operator",
        "eyebrow": "Broad coverage",
        "description": "Bring together messaging, live sessions, scheduler, cloud push, cloud subscription access, files, and automation routes.",
        "badge_label": "Integrations",
        "badge_tone": "blue",
        "defaults": {
            "network_scope": "cloud_pair",
            "ai_path": "cloud_provider",
            "intents": ("api_integrations", "messaging", "video_calls", "productivity"),
            "agent_visibility": "release_ready",
        },
    },
    {
        "id": "custom_mix",
        "label": "Custom mix",
        "eyebrow": "Flexible",
        "description": "Start neutral and choose every branch yourself.",
        "badge_label": "Custom",
        "badge_tone": "gray",
        "defaults": {
            "network_scope": "loopback",
            "ai_path": "model_picker",
            "intents": (),
            "agent_visibility": "release_ready",
        },
    },
)

AI_PROVIDER_BY_PATH = {
    "local_ollama": "ollama",
    "native_ollama_gateway": "ollama_gateway",
    "cloud_provider": "litellm",
    "google_gemini": "google",
    "hermes": "hermes",
    "openclaw": "openclaw",
    "odysseus": "odysseus",
    "model_picker": "ollama",
}

RISK_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _copy_json(value: Any) -> Any:
    return copy.deepcopy(value)


def _label_from_name(name: str) -> str:
    if name in KNOWN_AGENT_LABELS:
        return KNOWN_AGENT_LABELS[name]
    return " ".join(part.capitalize() for part in re.split(r"[_\-\s]+", name) if part) or name


def _normalize_agent_name(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, Mapping):
        for key in ("agent_name", "name", "id"):
            value = str(raw.get(key) or "").strip()
            if value:
                return value
    return ""


def _collect_agent_names(
    agents_payload: Optional[Mapping[str, Any]] = None,
    available_agents: Optional[Iterable[Any]] = None,
) -> List[str]:
    names: Set[str] = set(BASE_AGENT_NAMES)

    if available_agents is not None:
        for item in available_agents:
            name = _normalize_agent_name(item)
            if name:
                names.add(name)

    payload = agents_payload if isinstance(agents_payload, Mapping) else {}
    for key in ("available_agents", "workspace_only_agents", "agent_overview"):
        values = payload.get(key)
        if isinstance(values, Iterable) and not isinstance(values, (str, bytes, Mapping)):
            for item in values:
                name = _normalize_agent_name(item)
                if name:
                    names.add(name)

    for key in ("agent_details", "workbench_agents"):
        values = payload.get(key)
        if isinstance(values, Mapping):
            for name in values.keys():
                normalized = _normalize_agent_name(name)
                if normalized:
                    names.add(normalized)

    return sorted(names)


def classify_agent(name: str) -> Dict[str, Any]:
    normalized = _normalize_agent_name(name)
    release_state = "release_ready"
    rationale = "Suggested by default."
    if normalized not in BUILTIN_AGENT_PACKAGE_NAMES:
        release_state = "excluded_from_release"
        rationale = "Not suggested by default."
    elif normalized in RELEASE_REVIEW_REQUIRED_AGENTS:
        release_state = "review_required"
        rationale = "Not suggested by default."

    return {
        "name": normalized,
        "label": _label_from_name(normalized),
        "release_state": release_state,
        "rationale": rationale,
        "roles": list(AGENT_ROLE_MAP.get(normalized, ())),
    }


def build_agent_release_catalog(
    agents_payload: Optional[Mapping[str, Any]] = None,
    available_agents: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    agents = [classify_agent(name) for name in _collect_agent_names(agents_payload, available_agents)]
    release_ready = [agent for agent in agents if agent["release_state"] == "release_ready"]
    review_required = [agent for agent in agents if agent["release_state"] == "review_required"]
    excluded = [agent for agent in agents if agent["release_state"] == "excluded_from_release"]
    return {
        "all_agents": agents,
        "release_ready": release_ready,
        "review_required": review_required,
        "excluded_from_release": excluded,
        "policy": {
            "default_visibility": "release_ready",
            "excluded_agent_names": sorted(agent["name"] for agent in excluded),
            "review_required_agent_names": sorted(RELEASE_REVIEW_REQUIRED_AGENTS),
            "note": "Only built-in agents are suggested in packaged releases.",
        },
    }


def _current_config_summary(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    cfg = config if isinstance(config, Mapping) else {}
    return {
        "security_mode": str(((cfg.get("security") or {}) if isinstance(cfg.get("security"), Mapping) else {}).get("mode") or "secure"),
        "ai_provider": str(((cfg.get("ai_provider") or {}) if isinstance(cfg.get("ai_provider"), Mapping) else {}).get("provider") or "ollama"),
        "tunnelmole_enabled": bool(((cfg.get("tunnelmole") or {}) if isinstance(cfg.get("tunnelmole"), Mapping) else {}).get("enabled", False)),
        "page_service_auto_start": bool(((cfg.get("autoyou_page") or {}) if isinstance(cfg.get("autoyou_page"), Mapping) else {}).get("auto_start", True)),
        "speech_model": str((((cfg.get("speech") or {}) if isinstance(cfg.get("speech"), Mapping) else {}).get("stt") or {}).get("model") or "tiny.en"),
    }


def _coverage_payload(
    *,
    api_route_count: int = 0,
    agent_catalog: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    route_count = max(0, int(api_route_count or 0))
    catalog = agent_catalog if isinstance(agent_catalog, Mapping) else {}
    all_agents = catalog.get("all_agents") if isinstance(catalog.get("all_agents"), list) else []
    release_ready = catalog.get("release_ready") if isinstance(catalog.get("release_ready"), list) else []
    return {
        "api_route_count": route_count,
        "api_route_count_label": str(route_count) if route_count else "Unavailable",
        "feature_group_count": len(FEATURE_CATALOG),
        "agent_count": len(all_agents),
        "release_ready_agent_count": len(release_ready),
        "suggested_agent_count": len(release_ready),
        "integration_scope": "AutoYou main server actions, setup menus, integrations, and agent surfaces.",
        "api_route_count_source": "live admin route table",
    }


def build_setup_profile_payload(
    config: Optional[Mapping[str, Any]] = None,
    agents_payload: Optional[Mapping[str, Any]] = None,
    *,
    api_route_count: int = 0,
) -> Dict[str, Any]:
    agent_catalog = build_agent_release_catalog(agents_payload)
    payload = {
        "version": PROFILE_CATALOG_VERSION,
        "title": "Guided local setup",
        "intro": (
            "AutoYou runs as a local server first. These choices turn broad setup "
            "choices into validated config suggestions, restart notes, and risk "
            "warnings without using AI."
        ),
        "decision_tree": _copy_json(DECISION_TREE),
        "profile_templates": _copy_json(PROFILE_TEMPLATES),
        "feature_catalog": _copy_json(FEATURE_CATALOG),
        "agent_release": agent_catalog,
        "coverage": _coverage_payload(api_route_count=api_route_count, agent_catalog=agent_catalog),
        "current": _current_config_summary(config),
    }
    payload["default_recipe"] = compile_setup_recipe(
        {"profile_id": "private_starter"},
        config=config,
        agents_payload=agents_payload,
        api_route_count=api_route_count,
    )
    return payload


def _profile_by_id(profile_id: str) -> Dict[str, Any]:
    normalized = str(profile_id or "").strip().lower()
    for profile in PROFILE_TEMPLATES:
        if profile["id"] == normalized:
            return _copy_json(profile)
    return _copy_json(next(profile for profile in PROFILE_TEMPLATES if profile["id"] == "custom_mix"))


def _as_string_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes, Mapping)):
        result: List[str] = []
        for item in raw:
            value = str(item or "").strip()
            if value and value not in result:
                result.append(value)
        return result
    return []


def _normalize_answers(answers: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    raw = answers if isinstance(answers, Mapping) else {}
    profile = _profile_by_id(str(raw.get("profile_id") or "private_starter"))
    defaults = dict(profile.get("defaults") or {})

    merged: Dict[str, Any] = {
        "profile_id": profile["id"],
        "network_scope": str(raw.get("network_scope") or defaults.get("network_scope") or "loopback").strip().lower(),
        "ai_path": str(raw.get("ai_path") or defaults.get("ai_path") or "local_ollama").strip().lower(),
        "agent_visibility": str(raw.get("agent_visibility") or defaults.get("agent_visibility") or "release_ready").strip().lower(),
    }

    intents = _as_string_list(defaults.get("intents"))
    for intent in _as_string_list(raw.get("intents")):
        if intent not in intents:
            intents.append(intent)
    merged["intents"] = intents
    return merged


def _highest_risk(current: str, candidate: str) -> str:
    return candidate if RISK_RANK.get(candidate, 0) > RISK_RANK.get(current, 0) else current


def _warning(severity: str, title: str, body: str, action: str = "") -> Dict[str, str]:
    return {"severity": severity, "title": title, "body": body, "action": action}


def _recommended_agent_names(answers: Mapping[str, Any]) -> List[str]:
    intents = set(_as_string_list(answers.get("intents")))
    ai_path = str(answers.get("ai_path") or "")
    names: List[str] = ["admin_agent", "model_picker_agent"]
    if "incognito" not in intents:
        names.append("memory_agent")

    def add(*items: str) -> None:
        for item in items:
            if item not in names:
                names.append(item)

    if "productivity" in intents:
        add("notes_agent", "tasks_agent", "notify_agent")
    if "messaging" in intents:
        add("notify_agent", "internet_agent")
    if "speech" in intents:
        add("audio_agent", "voice_training_agent")
    if "video_calls" in intents:
        add("audio_agent", "media_generation_agent")
    if "creator" in intents:
        add("website_agent", "media_generation_agent", "voice_training_agent", "files_agent")
    if "agent_websites" in intents:
        add("page_agent", "website_agent", "browser_agent", "persona_agent")
    if "api_integrations" in intents:
        add("internet_agent", "files_agent", "skills_agent", "cli_agent")
    if ai_path == "hermes":
        add("hermes_agent")
    if ai_path == "openclaw":
        add("openclaw_agent")
    if ai_path in {"model_picker", "cloud_provider", "google_gemini"}:
        add("model_picker_agent")
    if intents & {"agent_websites", "api_integrations", "creator"}:
        add("agent_builder_agent", "skills_agent")
    return names


def _filter_recommended_agents(
    names: Sequence[str],
    agent_catalog: Mapping[str, Any],
    *,
    include_review: bool,
) -> List[Dict[str, Any]]:
    by_name = {agent["name"]: agent for agent in agent_catalog.get("all_agents", []) if isinstance(agent, Mapping)}
    result: List[Dict[str, Any]] = []
    for name in names:
        agent = by_name.get(name) or classify_agent(name)
        if agent["release_state"] == "excluded_from_release":
            continue
        if agent["release_state"] == "review_required" and not include_review:
            continue
        result.append(dict(agent))
    return result


def _make_config_patches(answers: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    network_scope = str(answers.get("network_scope") or "loopback")
    ai_path = str(answers.get("ai_path") or "local_ollama")
    intents = set(_as_string_list(answers.get("intents")))
    provider = AI_PROVIDER_BY_PATH.get(ai_path, "ollama")

    safe_patch: Dict[str, Any] = {
        "ai_provider": {"provider": provider},
        "ai_agent": {
            "enabled": True,
            "auto_start": True,
            "record_messages_in_database": True,
            "memory_backend": "legacy",
        },
        "client_identity": {
            "store_client_names_in_history": False,
        },
    }
    if provider == "ollama":
        safe_patch["ollama"] = {"enabled": True, "use_google_api": False}
    elif provider == "google":
        safe_patch["ollama"] = {"enabled": True, "use_google_api": True}

    if intents & {"agent_websites", "creator", "api_integrations"}:
        safe_patch["autoyou_page"] = {"auto_start": True, "timeline_days": 7}
    if "video_calls" in intents:
        safe_patch.setdefault("video_call", {}).update({"enabled": True, "audio_enabled": True, "wuift_enabled": True})
    if "background_connection" in intents:
        safe_patch.setdefault("video_call", {}).update(
            {
                "enabled": True,
                "audio_enabled": True,
                "wuift_enabled": True,
                "background_mode_enabled": True,
                "silent_recording_enabled": False,
                "record_my_video": False,
            }
        )
    if "safety_recording" in intents:
        safe_patch.setdefault("video_call", {}).update(
            {
                "enabled": True,
                "audio_enabled": True,
                "wuift_enabled": True,
                "background_mode_enabled": True,
                "silent_recording_enabled": True,
                "record_my_video": False,
                "capture_audio": False,
            }
        )
    if "incognito" in intents:
        safe_patch["ai_agent"]["record_messages_in_database"] = False
        safe_patch["client_identity"]["store_client_names_in_history"] = False
        safe_patch.setdefault("video_call", {}).update(
            {
                "record_my_video": False,
                "record_audio_only_calls": False,
                "silent_recording_enabled": False,
                "background_mode_enabled": False,
                "capture_audio": False,
            }
        )

    guarded_patch: Dict[str, Any] = {}
    if network_scope == "lan":
        # The same saved setting as Overview's "home network access": HTTPS on,
        # and website apps behind the admin sign-in instead of their own port.
        guarded_patch["server"] = {
            "bind_host": "0.0.0.0",
            "https_enabled": True,
            "home_network_websites": "path_proxy",
        }
    if network_scope == "public_proxy":
        guarded_patch["tunnelmole"] = {
            "enabled": True,
            "timeout_minutes": 5,
            "otp_timeout_minutes": 5,
            "otp_multiuse": False,
            "pair_code_mode": PAIR_CODE_MODE_AUTHENTICATOR,
            "connection_mode": CONNECTION_MODE_TIMED,
            "url_only_pair": True,
        }

    security_mode = "secure_professional" if network_scope == "public_proxy" else "secure"
    return {
        "safe_config_patch": safe_patch,
        "guarded_config_patch": guarded_patch,
        "security_patch": {"mode": security_mode},
    }


def compile_setup_recipe(
    answers: Optional[Mapping[str, Any]] = None,
    *,
    config: Optional[Mapping[str, Any]] = None,
    agents_payload: Optional[Mapping[str, Any]] = None,
    api_route_count: int = 0,
) -> Dict[str, Any]:
    normalized = _normalize_answers(answers)
    profile = _profile_by_id(normalized["profile_id"])
    agent_catalog = build_agent_release_catalog(agents_payload)
    include_review = normalized["agent_visibility"] == "include_review"
    intents = set(_as_string_list(normalized.get("intents")))
    network_scope = normalized["network_scope"]
    ai_path = normalized["ai_path"]

    risk_level = "low"
    warnings: List[Dict[str, str]] = []
    restart_required: List[Dict[str, Any]] = []

    if network_scope == "lan":
        risk_level = _highest_risk(risk_level, "high")
        restart_required.append(
            {
                "id": "bind_host_0_0_0_0",
                "label": "Restart AutoYou to open it to your home network with HTTPS",
                # Applying the guarded settings saves this; a launcher that sets
                # the bind itself can pass the same choice instead.
                "command_examples": ("AUTOYOU_BIND_HOST=0.0.0.0", "--host 0.0.0.0"),
                "applies_after_restart": True,
                "services": ("Admin UI 8001 and HTTPS 8443", "Auth 8002"),
            }
        )
        warnings.append(
            _warning(
                "high",
                "Home network access (0.0.0.0) exposes the Admin UI and Auth Server",
                (
                    "Turning on the home network lets same-network devices reach the Admin UI sign-in, now "
                    "over HTTPS, and the Auth Server for Local Pair. Website apps from Websites & Browser "
                    "stay behind that sign-in at /agent/<name>/ unless you open their own port. "
                    "Only use this on a trusted local network."
                ),
                "Keep the default 127.0.0.1 bind unless devices on your network need to connect directly.",
            )
        )
        warnings.append(
            _warning(
                "low",
                "AI Agent server (8081) stays loopback-only unless separately enabled",
                (
                    "The AI Agent runtime has no authentication of its own, so it does not follow "
                    "this LAN bind -- it keeps binding 127.0.0.1 regardless. Enable 'Allow AI Agent "
                    "access from home network' in AI & Models settings to reach it from the LAN, "
                    "which adds a dedicated HTTPS port (8481 by default) requiring a one-time code."
                ),
                "Leave AI Agent LAN access off unless you specifically need it.",
            )
        )

    if network_scope == "public_proxy":
        risk_level = _highest_risk(risk_level, "critical")
        warnings.append(
            _warning(
                "critical",
                "Public link creates an internet-reachable path",
                (
                    "A public link publishes Websites & Browser through a public URL. "
                    "Anyone with the URL can try to reach the pairing surface, so use Secure Professional, "
                    "authenticator pair-code mode, URL-only sharing, and a timed lifetime."
                ),
                "Never expose the admin page through the public link.",
            )
        )

    if "agent_websites" in intents and network_scope in {"lan", "public_proxy"}:
        risk_level = _highest_risk(risk_level, "high")
        warnings.append(
            _warning(
                "high",
                "Agent websites travel with Websites & Browser",
                (
                    "When Websites & Browser is reachable from the local network or a public link, advertised "
                    "agent websites and hosted pages can also become reachable. Those pages may expose tools, "
                    "files, browser actions, or agent-specific state depending on each agent. On the home "
                    "network they stay behind the admin sign-in unless their own port is opened, where "
                    "browsers get the remote client role."
                ),
                "Review Websites & Browser routes and per-agent security before broadening network reach.",
            )
        )

    if "commerce_review" in intents or include_review:
        risk_level = _highest_risk(risk_level, "medium")
        warnings.append(
            _warning(
                "medium",
                "Money-related helpers need extra care",
                (
                    "Donation, hosting, earnings, ads, and finance helpers can affect real accounts or money. "
                    "AutoYou keeps these out of default suggestions unless you choose this branch."
                ),
                "Check each helper before enabling it for day-to-day use.",
            )
        )
    if "safety_recording" in intents and "incognito" not in intents:
        risk_level = _highest_risk(risk_level, "medium")
        warnings.append(
            _warning(
                "medium",
                "Safety recording stores phone microphone files",
                (
                    "When a paired phone turns on Safety Recording, AutoYou can save microphone WAV files "
                    "while no call screen is open. It does not send audio back, transcribe it, or call AI."
                ),
                "Use only with clear consent and choose the storage folder in Video & Calls.",
            )
        )

    recommended_agents = _filter_recommended_agents(
        _recommended_agent_names(normalized),
        agent_catalog,
        include_review=include_review,
    )
    excluded_agents = [
        dict(agent)
        for agent in agent_catalog.get("excluded_from_release", [])
        if isinstance(agent, Mapping)
    ]
    review_agents = [
        dict(agent)
        for agent in agent_catalog.get("review_required", [])
        if isinstance(agent, Mapping)
    ]
    patches = _make_config_patches(normalized)

    ai_label = {
        "local_ollama": "Ollama local",
        "native_ollama_gateway": "Ollama native gateway",
        "cloud_provider": "cloud model provider",
        "google_gemini": "Gemini",
        "hermes": "Hermes Agent",
        "openclaw": "OpenClaw",
        "odysseus": "Odysseus",
        "model_picker": "Model Picker Agent first",
    }.get(ai_path, "Ollama local")

    sections = [
        {
            "id": "security",
            "label": "Security baseline",
            "status": "manual_review",
            "description": "Keep a custom password and shared authenticator before widening access.",
            "settings": patches["security_patch"],
        },
        {
            "id": "reachability",
            "label": "Reachability",
            "status": "restart_required" if network_scope == "lan" else ("guarded" if network_scope == "public_proxy" else "safe_default"),
            "description": {
                "loopback": "Keep the server on 127.0.0.1. Existing secure messaging AutoPair and Cloud Pair remain available.",
                "cloud_pair": "Use AutoYou Cloud Pair without opening the local server to the LAN.",
                "lan": "Turn on the home network with HTTPS; website apps stay behind the admin sign-in.",
                "public_proxy": "Use a timed public URL only after security settings are ready.",
            }.get(network_scope, "Keep the local-only default."),
        },
        {
            "id": "ai_runtime",
            "label": "AutoYou AI",
            "status": "config_ready",
            "description": f"Set provider path to {ai_label}. Hermes and OpenClaw are normal guided choices, not developer-only modes.",
        },
        {
            "id": "features",
            "label": "Feature branches",
            "status": "config_ready",
            "description": "Enable the selected setup branches and route the rest to their dedicated menus.",
            "intents": sorted(intents),
        },
        {
            "id": "agents",
            "label": "Agent workspaces",
            "status": "suggested",
            "description": "Suggest agents that fit the selected setup branches and keep higher-risk helpers out of defaults.",
            "recommended_count": len(recommended_agents),
        },
    ]
    if "background_connection" in intents:
        sections.append(
            {
                "id": "background_phone_link",
                "label": "Background phone link",
                "status": "config_ready",
                "description": "Keep paired phones connected while the app is in the background. The phone microphone stays off unless a call or Safety Recording is active.",
            }
        )
    if "safety_recording" in intents:
        sections.append(
            {
                "id": "safety_recording",
                "label": "Safety recording",
                "status": "config_ready",
                "description": "Allow a paired phone to save background microphone audio as WAV files on this server. There is no call screen, no server audio reply, no transcription, and no AI processing.",
            }
        )
    if "incognito" in intents:
        sections.append(
            {
                "id": "incognito",
                "label": "Incognito defaults",
                "status": "config_ready",
                "description": "Turn off AI message database recording, client-name history, received-video recording, and safety recording by default.",
            }
        )

    next_actions = [
        "Open Model Picker Agent when unsure which local or cloud model fits a feature.",
        "Use the AI & Models screen to download local models or set hosted provider credentials.",
    ]
    if "messaging" in intents:
        next_actions.append("Open Messaging to configure Telegram Bot, Telegram User, Signal, or WhatsApp only when those partners are needed.")
    if "video_calls" in intents:
        next_actions.append("Open Video & Calls to confirm camera, microphone, playback, and remote media behavior.")
    if "background_connection" in intents:
        next_actions.append("Open Video & Calls to verify Background Mode is enabled and Safety Recording is off unless intentionally needed.")
    if "safety_recording" in intents:
        next_actions.append("Open Video & Calls to verify Safety Recording is enabled and choose the storage folder.")
    if "incognito" in intents:
        next_actions.append("Open AI & Models and Video & Calls to confirm message storage, client-name history, and recording options are off.")
    if "speech" in intents:
        next_actions.append("Open Speech to select the local STT model and TTS provider.")
    if "agent_websites" in intents:
        next_actions.append("Open Websites & Browser and Agents to review advertised websites before exposing them.")
    if network_scope == "lan":
        next_actions.append(
            "Apply the guarded network settings, restart AutoYou, then install this server's certificate "
            "(/ca.crt) once on each device that opens it over HTTPS."
        )
    if network_scope == "public_proxy":
        next_actions.append("Create or verify the shared authenticator before starting the public link.")

    return {
        "success": True,
        "version": PROFILE_CATALOG_VERSION,
        "profile_id": profile["id"],
        "title": profile["label"],
        "summary": profile["description"],
        "answers": normalized,
        "risk_level": risk_level,
        "warnings": warnings,
        "restart_required": restart_required,
        "safe_config_patch": patches["safe_config_patch"],
        "guarded_config_patch": patches["guarded_config_patch"],
        "security_patch": patches["security_patch"],
        "decision_path": [
            f"Profile: {profile['label']}",
            f"Reachability: {network_scope}",
            f"AI path: {ai_label}",
            "Feature branches: " + (", ".join(sorted(intents)) if intents else "none selected yet"),
        ],
        "sections": sections,
        "recommended_agents": recommended_agents,
        "review_required_agents": review_agents,
        "excluded_agents": excluded_agents,
        "feature_groups": _copy_json(FEATURE_CATALOG),
        "coverage": _coverage_payload(api_route_count=api_route_count, agent_catalog=agent_catalog),
        "next_actions": next_actions,
        "tooltips": {
            "model_picker_agent": "Open the Model Picker Agent website when you are unsure which local or cloud model to choose.",
            "hermes_agent": "Choose Hermes when its local gateway should handle the session.",
            "openclaw_agent": "Choose OpenClaw when its browser and gateway flow should be primary.",
        },
    }
