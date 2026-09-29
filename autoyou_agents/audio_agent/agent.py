# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-dbe40f730971ea9fdaec3bcd

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import hashlib
import logging
import os
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent

from autoyou_agents.admin_agent.agent import (
    _check_admin_session,
    _get_internal_ai_agent_api_token,
    _get_saved_reply_target as _resolve_saved_reply_target,
    _get_session_token,
    _http,
    _state_get,
    _state_set,
)
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime, inject_realtime_datetime_into_request
from shared.adk_state import (
    AUTOYOU_OWNER_KEY_STATE_KEY,
    AUTOYOU_OWNER_KEY_USER_STATE_KEY,
    normalize_reply_target,
)
from shared.audio_playback_settings import (
    AUDIO_PLAYBACK_ENABLED_ENV,
    MUSIC_LIBRARY_DIRS_ENV,
    audio_playback_enabled_from_env,
    get_default_music_library_dirs,
    resolve_music_library_dirs,
)
from shared.platform_runtime import get_config_dir, is_compiled
from shared.session_execution import SESSION_CONTROL_STATE_KEY
from shared.session_execution import create_text_llm_response, create_tool_call_llm_response

from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-dbe40f730971ea9fdaec3bcd"


logger = logging.getLogger(__name__)

_MUSIC_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}
_MUSIC_LAST_RESULTS_KEY = "user:music_last_results"
_MUSIC_QUEUE_KEY = "user:music_queue_tracks"
_MUSIC_HISTORY_KEY = "user:music_history_tracks"
_MUSIC_CURRENT_KEY = "user:music_current_track"
_MUSIC_REPEAT_MODE_KEY = "user:music_repeat_mode"
_MUSIC_SHUFFLE_KEY = "user:music_shuffle_enabled"
_MUSIC_MAX_SCAN = 5000
_AUDIO_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY = "_autoyou_audio_tool_dispatch_invocation_id"
_AUDIO_TOOL_RESULT_INVOCATION_ID_STATE_KEY = "_autoyou_audio_tool_result_invocation_id"
_AUDIO_TOOL_RESULT_MESSAGE_STATE_KEY = "_autoyou_audio_tool_result_message"
_DEFAULT_RUNTIME_MEDIA_DIR_NAMES = {
    "albums",
    "assets",
    "audio",
    "media",
    "music",
    "playlists",
    "samples",
    "songs",
    "soundtracks",
    "tracks",
    "uploads",
}
_COMMON_MUSIC_SCAN_SKIP_DIR_NAMES = {
    "__pycache__",
    "node_modules",
    "runtime_site_packages",
    "runtime_stdlib",
    "site-packages",
}
_TRACK_SELECTION_INDEX_PATTERN = re.compile(
    r"^(?:the\s+)?(?:(?:song|track|file|audio|item|result|option)\s+)?(?:number\s+|no\.?\s*|#\s*)?(\d+)(?:st|nd|rd|th)?\b(?:\s+(?:song|track|file|audio|item|result|option))?$",
    re.IGNORECASE,
)
_TRACK_SELECTION_ORDINAL_PATTERN = re.compile(
    r"^(?:the\s+)?(?:(?:song|track|file|audio|item|result|option)\s+)?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b(?:\s+(?:song|track|file|audio|item|result|option))?$",
    re.IGNORECASE,
)
_TRACK_SELECTION_PREFIX_PATTERN = re.compile(
    r"^(?:the\s+)?(?:(?:song|track|file|audio|music)(?:\s+(?:named|called|titled))?|(?:named|called|titled))\s+",
    re.IGNORECASE,
)
_TRACK_SELECTION_TRAILING_CONTEXT_PATTERN = re.compile(
    r"\s+(?:from|in)\s+(?:my\s+)?(?:library|music|songs|tracks|audio)\b.*$",
    re.IGNORECASE,
)
_TRACK_SELECTION_ORDINAL_INDEXES = {
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "ninth": "9",
    "tenth": "10",
}

_AUDIO_LIBRARY_NOUN_PATTERN = re.compile(
    r"\b(?:music|song|songs|track|tracks|audio|album|albums|playlist|playlists|playback)\b",
    re.IGNORECASE,
)

_AUDIO_STATUS_PATTERN = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:what(?:'s| is)\s+playing|playback\s+status|current\s+track|audio\s+status)\s*(?:please)?[.!?]*$",
    re.IGNORECASE,
)

_AUDIO_TRANSPORT_PATTERNS = {
    "pause": re.compile(
        r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:pause|hold)(?:\s+(?:the\s+)?)?(?:music|audio|song|track|playback)?\s*(?:please)?[.!?]*$",
        re.IGNORECASE,
    ),
    "resume": re.compile(
        r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:resume|continue)(?:\s+(?:the\s+)?)?(?:music|audio|song|track|playback)?\s*(?:please)?[.!?]*$",
        re.IGNORECASE,
    ),
    "stop": re.compile(
        r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?stop(?:\s+(?:the\s+)?)?(?:music|audio|song|track|playback)?\s*(?:please)?[.!?]*$",
        re.IGNORECASE,
    ),
    "next": re.compile(
        r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:(?:go\s+)?(?:next|skip))(?:\s+(?:song|track|music|audio|playback))?\s*(?:please)?[.!?]*$",
        re.IGNORECASE,
    ),
    "previous": re.compile(
        r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:(?:go\s+)?(?:previous|back))(?:\s+(?:song|track|music|audio|playback))?\s*(?:please)?[.!?]*$",
        re.IGNORECASE,
    ),
}

_AUDIO_PLAY_QUEUE_PATTERN = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:play|queue|add to queue)\b",
    re.IGNORECASE,
)

_AUDIO_REPEAT_PATTERN = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?repeat\s+(?:mode\s+)?(off|one|all)\s*(?:please)?[.!?]*$",
    re.IGNORECASE,
)

_AUDIO_SHUFFLE_ENABLE_PATTERN = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:enable|turn on)\s+shuffle\s*(?:please)?[.!?]*$",
    re.IGNORECASE,
)

_AUDIO_SHUFFLE_DISABLE_PATTERN = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:disable|turn off)\s+shuffle\s*(?:please)?[.!?]*$",
    re.IGNORECASE,
)

_AUDIO_PLAY_RANDOM_ALL_PATTERN = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?play\s+(?:all\s+)?(?:my\s+)?(?:songs?|tracks?|music|audio)\s+(?:in\s+)?random(?:\s+order)?\s*(?:please)?[.!?]*$",
    re.IGNORECASE,
)

# Detect requests to CREATE/GENERATE audio that cannot be fulfilled by any tool.
_AUDIO_CREATE_INTENT_PATTERN = re.compile(
    r"\b(?:create|make|generate|compose|write|record|produce|synthesize|build)\b.{0,60}"
    r"\b(?:song|songs|track|tracks|audio|music|beat|beats|melody|tune|sound)\b",
    re.IGNORECASE,
)

def _audio_playback_enabled() -> bool:
    if os.getenv(AUDIO_PLAYBACK_ENABLED_ENV) is not None:
        return audio_playback_enabled_from_env(True)
    try:
        from service_manager import get_service_manager

        sm = get_service_manager()
        config = getattr(sm, "config", None)
        value = getattr(config, "audio_playback_enabled", None)
        if value is not None:
            return bool(value)
    except Exception:
        pass
    return audio_playback_enabled_from_env(True)

def _extract_text_from_llm_request(llm_request: Any) -> str:
    for content in reversed(getattr(llm_request, "contents", []) or []):
        if str(getattr(content, "role", "") or "").strip().lower() not in {"", "user"}:
            continue
        parts: List[str] = []
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            return " ".join(parts).strip()
    return ""

def _get_invocation_id(context: Any) -> str:
    return str(getattr(context, "invocation_id", "") or "").strip()

def _tool_dispatch_already_happened(state: Any, invocation_id: str) -> bool:
    if not invocation_id:
        return False
    return str(_state_get(state, _AUDIO_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY) or "").strip() == invocation_id

def _mark_tool_dispatch(state: Any, invocation_id: str) -> None:
    if invocation_id:
        _state_set(state, _AUDIO_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY, invocation_id)
        _state_set(state, _AUDIO_TOOL_RESULT_INVOCATION_ID_STATE_KEY, "")
        _state_set(state, _AUDIO_TOOL_RESULT_MESSAGE_STATE_KEY, "")

def _format_audio_track_label(track: Dict[str, Any]) -> str:
    title = str(track.get("title") or "").strip()
    file_name = str(track.get("file_name") or "").strip()
    if title and file_name and file_name.lower() != title.lower():
        return f"{title} ({file_name})"
    return title or file_name or "Unknown track"

def _format_audio_search_response(tool_response: Dict[str, Any]) -> str:
    tracks = list(tool_response.get("tracks") or [])
    count = int(tool_response.get("count") or len(tracks) or 0)
    if not tracks:
        return str(tool_response.get("message") or "No matching local audio files were found.").strip()

    noun = "track" if count == 1 else "tracks"
    preview_lines = [
        f"{int(track.get('result_index') or index)}. {_format_audio_track_label(track)}"
        for index, track in enumerate(tracks, start=1)
    ]
    return f"Found {count} {noun}.\n" + "\n".join(preview_lines)

def _format_audio_status_response(tool_response: Dict[str, Any]) -> str:
    status_text = str(tool_response.get("playback_status") or "").strip() or "unknown"
    music_state = tool_response.get("music_state") if isinstance(tool_response, dict) else None
    current_track = music_state.get("current") if isinstance(music_state, dict) else None
    current_label = _format_audio_track_label(current_track or {}) if current_track else ""
    queue_length = 0
    if isinstance(music_state, dict):
        try:
            queue_length = len(list(music_state.get("queue") or []))
        except Exception:
            queue_length = 0
    if current_label:
        if queue_length:
            return f"Playback status: {status_text}. Current track: {current_label}. Queue length: {queue_length}."
        return f"Playback status: {status_text}. Current track: {current_label}."
    return f"Playback status: {status_text}."

def _render_audio_tool_response(tool_name: str, tool_response: Dict[str, Any]) -> str:
    status = str(tool_response.get("status") or "").strip().lower()
    message = str(tool_response.get("message") or "").strip()

    if tool_name == "search_local_audio_library" and status == "success":
        return _format_audio_search_response(tool_response)
    if tool_name == "get_saved_reply_target_audio_status" and status == "success":
        return _format_audio_status_response(tool_response)
    if message:
        return message
    if status == "success":
        return "Audio action completed successfully."
    return "The audio action failed."

def _record_audio_tool_result(state: Any, invocation_id: str, tool_name: str, tool_response: Dict[str, Any]) -> None:
    if not invocation_id:
        return
    _state_set(state, _AUDIO_TOOL_RESULT_INVOCATION_ID_STATE_KEY, invocation_id)
    _state_set(state, _AUDIO_TOOL_RESULT_MESSAGE_STATE_KEY, _render_audio_tool_response(tool_name, tool_response))

def _extract_quoted_value(text: str) -> Optional[str]:
    matches = re.findall(r"[\"\u201c\u201d](.+?)[\"\u201c\u201d]", text or "")
    if matches:
        return str(matches[0]).strip()
    return None

def _extract_tail_argument(text: str, patterns: List[str]) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = str(match.group(1) or "").strip(" .,:;!?\"'")
        if value:
            return value
    quoted = _extract_quoted_value(text)
    if quoted:
        return quoted
    return None

def _normalize_track_selection_text(selection: str) -> str:
    candidate = " ".join(str(selection or "").split()).strip(" .,:;!?\"'")
    if not candidate:
        return ""

    quoted = _extract_quoted_value(candidate)
    if quoted:
        return str(quoted).strip()

    candidate = _TRACK_SELECTION_TRAILING_CONTEXT_PATTERN.sub("", candidate).strip(" .,:;!?\"'")
    if not candidate:
        return ""

    index_match = _TRACK_SELECTION_INDEX_PATTERN.match(candidate)
    if index_match:
        return str(index_match.group(1) or "").strip()

    ordinal_match = _TRACK_SELECTION_ORDINAL_PATTERN.match(candidate)
    if ordinal_match:
        ordinal_value = str(ordinal_match.group(1) or "").strip().lower()
        return _TRACK_SELECTION_ORDINAL_INDEXES.get(ordinal_value, candidate)

    previous = None
    while candidate and candidate != previous:
        previous = candidate
        candidate = _TRACK_SELECTION_PREFIX_PATTERN.sub("", candidate, count=1).strip(" .,:;!?\"'")
        if not candidate:
            break

        quoted = _extract_quoted_value(candidate)
        if quoted:
            return str(quoted).strip()

        index_match = _TRACK_SELECTION_INDEX_PATTERN.match(candidate)
        if index_match:
            return str(index_match.group(1) or "").strip()

        ordinal_match = _TRACK_SELECTION_ORDINAL_PATTERN.match(candidate)
        if ordinal_match:
            ordinal_value = str(ordinal_match.group(1) or "").strip().lower()
            return _TRACK_SELECTION_ORDINAL_INDEXES.get(ordinal_value, candidate)

    return candidate

def _audio_control_requested(user_text: str) -> bool:
    normalized = _normalize_audio_request_text(user_text)
    return bool(
        normalized
        and (
            any(pattern.fullmatch(normalized) for pattern in _AUDIO_TRANSPORT_PATTERNS.values())
            or _AUDIO_STATUS_PATTERN.fullmatch(normalized)
            or _AUDIO_REPEAT_PATTERN.fullmatch(normalized)
            or _AUDIO_SHUFFLE_ENABLE_PATTERN.fullmatch(normalized)
            or _AUDIO_SHUFFLE_DISABLE_PATTERN.fullmatch(normalized)
            or _AUDIO_PLAY_QUEUE_PATTERN.match(normalized)
            or (re.search(r"\bshuffle\b", normalized) and re.search(r"\b(play|start)\b", normalized))
        )
    )

def _normalize_audio_request_text(user_text: str) -> str:
    normalized = " ".join(str(user_text or "").split()).strip()
    if not normalized:
        return ""
    lowered = normalized.lower()
    if lowered.startswith("[voice transcript]"):
        lowered = lowered[len("[voice transcript]"):].strip()
    return lowered

async def _audio_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    inject_realtime_datetime_into_request(llm_request)
    user_text = _extract_text_from_llm_request(llm_request)
    if not user_text:
        return None

    invocation_id = _get_invocation_id(callback_context)
    if _tool_dispatch_already_happened(callback_context.state, invocation_id):
        result_invocation_id = str(
            _state_get(callback_context.state, _AUDIO_TOOL_RESULT_INVOCATION_ID_STATE_KEY) or ""
        ).strip()
        if invocation_id and invocation_id == result_invocation_id:
            result_message = str(
                _state_get(callback_context.state, _AUDIO_TOOL_RESULT_MESSAGE_STATE_KEY) or ""
            ).strip()
            if result_message:
                return create_text_llm_response(
                    result_message,
                    custom_metadata={
                        "response_author": AGENT_NAME,
                        "audio_deterministic_reply": True,
                    },
                )
        return None

    lowered = _normalize_audio_request_text(user_text)
    playback_context_args = _explicit_playback_context_args(callback_context.state)

    # Block create/generate requests deterministically before the LLM can hallucinate.
    if _AUDIO_CREATE_INTENT_PATTERN.search(lowered):
        return create_text_llm_response(
            "I can only play existing local audio files. Creating or generating audio is not supported.",
            custom_metadata={"response_author": AGENT_NAME, "audio_create_rejected": True},
        )

    if _audio_control_requested(lowered) and not _audio_playback_enabled():
        return create_text_llm_response(
            "Audio playback is disabled right now. Use the admin agent to enable it before controlling music.",
            custom_metadata={"response_author": AGENT_NAME, "audio_playback_enabled": False},
        )

    if _AUDIO_TRANSPORT_PATTERNS["pause"].fullmatch(lowered):
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "pause_saved_reply_target_audio",
            dict(playback_context_args),
            custom_metadata={"response_author": AGENT_NAME},
        )

    if _AUDIO_TRANSPORT_PATTERNS["resume"].fullmatch(lowered):
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "resume_saved_reply_target_audio",
            dict(playback_context_args),
            custom_metadata={"response_author": AGENT_NAME},
        )

    if _AUDIO_TRANSPORT_PATTERNS["stop"].fullmatch(lowered):
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "stop_saved_reply_target_audio",
            dict(playback_context_args),
            custom_metadata={"response_author": AGENT_NAME},
        )

    if _AUDIO_TRANSPORT_PATTERNS["next"].fullmatch(lowered):
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "next_saved_reply_target_audio",
            dict(playback_context_args),
            custom_metadata={"response_author": AGENT_NAME},
        )

    if _AUDIO_TRANSPORT_PATTERNS["previous"].fullmatch(lowered):
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "previous_saved_reply_target_audio",
            dict(playback_context_args),
            custom_metadata={"response_author": AGENT_NAME},
        )

    if _AUDIO_STATUS_PATTERN.fullmatch(lowered):
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "get_saved_reply_target_audio_status",
            dict(playback_context_args),
            custom_metadata={"response_author": AGENT_NAME},
        )

    repeat_mode = _AUDIO_REPEAT_PATTERN.fullmatch(lowered)
    if repeat_mode:
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "set_saved_reply_target_audio_repeat_mode",
            {"mode": repeat_mode.group(1)},
            custom_metadata={"response_author": AGENT_NAME},
        )

    if _AUDIO_SHUFFLE_ENABLE_PATTERN.fullmatch(lowered):
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "set_saved_reply_target_audio_shuffle",
            {"enabled": True},
            custom_metadata={"response_author": AGENT_NAME},
        )

    if _AUDIO_SHUFFLE_DISABLE_PATTERN.fullmatch(lowered):
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "set_saved_reply_target_audio_shuffle",
            {"enabled": False},
            custom_metadata={"response_author": AGENT_NAME},
        )

    if _AUDIO_PLAY_RANDOM_ALL_PATTERN.fullmatch(lowered):
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "shuffle_play_local_audio_on_saved_reply_target",
            {"query": "", **playback_context_args},
            custom_metadata={"response_author": AGENT_NAME},
        )

    if re.search(r"\bshuffle\b", lowered) and re.search(r"\b(play|start)\b", lowered):
        query = _extract_tail_argument(
            user_text,
            [r"\bshuffle(?:\s+play)?\s+(.+)$", r"\bplay\s+shuffle\s+(.+)$"],
        )
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "shuffle_play_local_audio_on_saved_reply_target",
            {"query": query or "", **playback_context_args},
            custom_metadata={"response_author": AGENT_NAME},
        )

    if re.search(r"\b(queue|add to queue)\b", lowered):
        selection = _extract_tail_argument(
            user_text,
            [r"\bqueue\s+(.+)$", r"\badd\s+(.+?)\s+to\s+(?:the\s+)?queue\b"],
        )
        if selection:
            _mark_tool_dispatch(callback_context.state, invocation_id)
            return create_tool_call_llm_response(
                "queue_local_audio_on_saved_reply_target",
                {"track_selection": selection},
                custom_metadata={"response_author": AGENT_NAME},
            )

    search_query = _extract_tail_argument(
        user_text,
        [
            r"\bsearch\s+(?:my\s+)?(?:music|songs|tracks|audio)(?:\s+for)?\s+(.+)$",
            r"\bfind\s+(?:my\s+)?(?:music|songs|tracks|audio)(?:\s+for)?\s+(.+)$",
            r"\b(?:browse|list|show)\s+(?:my\s+)?(?:music|songs|tracks|audio)(?:\s+for)?\s+(.+)$",
        ],
    )
    if re.search(r"\b(search|browse|list|show|find)\b", lowered) and _AUDIO_LIBRARY_NOUN_PATTERN.search(lowered):
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "search_local_audio_library",
            {"query": search_query or ""},
            custom_metadata={"response_author": AGENT_NAME},
        )

    if re.search(r"\bplay\b", lowered):
        selection = _extract_tail_argument(
            user_text,
            [r"\bplay\s+(.+)$"],
        )
        if selection:
            _mark_tool_dispatch(callback_context.state, invocation_id)
            return create_tool_call_llm_response(
                "play_local_audio_on_saved_reply_target",
                {"track_selection": selection, **playback_context_args},
                custom_metadata={"response_author": AGENT_NAME},
            )

    return None

def _audio_after_tool_callback(tool: Any, args: Dict[str, Any], tool_context: Any, tool_response: Any) -> Any:
    del args
    invocation_id = _get_invocation_id(tool_context)
    if not invocation_id or not isinstance(tool_response, dict):
        return None

    tool_name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "").strip()
    if tool_name not in {
        "search_local_audio_library",
        "play_local_audio_on_saved_reply_target",
        "queue_local_audio_on_saved_reply_target",
        "pause_saved_reply_target_audio",
        "resume_saved_reply_target_audio",
        "stop_saved_reply_target_audio",
        "get_saved_reply_target_audio_status",
        "next_saved_reply_target_audio",
        "previous_saved_reply_target_audio",
        "set_saved_reply_target_audio_repeat_mode",
        "set_saved_reply_target_audio_shuffle",
        "shuffle_play_local_audio_on_saved_reply_target",
    }:
        return None

    _record_audio_tool_result(tool_context.state, invocation_id, tool_name, tool_response)
    return None

def _resolve_audio_agent_site_library_dirs() -> List[str]:
    # Mirrors the Audio Player website's own per-source toggles (Page Agent
    # feed audio / Notes Agent media audio) so chat search/playback sees the
    # same library the website shows instead of only the general music
    # folders. "Voice transcriptions" is force-disabled here regardless of
    # that toggle - the AI must never auto-select a private call recording,
    # matching the exclusion below in _normalize_music_dirs.
    try:
        from shared.audio_agent_library import (
            load_audio_agent_library_settings,
            resolve_audio_library_roots,
        )

        settings = load_audio_agent_library_settings(anchor=__file__)
        sources = dict(settings.get("audio_sources") or {})
        sources["voice_transcriptions"] = False
        settings = {**settings, "audio_sources": sources}
        return resolve_audio_library_roots(settings, anchor=__file__)
    except Exception:
        logger.debug("Failed to resolve Audio Player site library directories", exc_info=True)
        return []

def _normalize_music_dirs(library_dirs: Optional[str] = None) -> List[str]:
    # Saved call recordings live in this same music-library resolution path so a
    # human can browse them from the Audio Player, but the AI agent must never
    # auto-select and play back a private call recording mid-call while
    # searching/browsing for music - exclude them here specifically.
    general_dirs = resolve_music_library_dirs(
        library_dirs,
        anchor=__file__,
        fallback_to_default=library_dirs in (None, ""),
        include_voice_training_recordings=False,
    )
    seen = {_normalize_path_key(item) for item in general_dirs}
    for directory in _resolve_audio_agent_site_library_dirs():
        key = _normalize_path_key(directory)
        if key in seen:
            continue
        seen.add(key)
        general_dirs.append(directory)
    return general_dirs

def _music_library_configuration_guidance() -> str:
    if is_compiled():
        config_dir = get_config_dir("AutoYou", anchor=__file__)
        return (
            "Packaged AutoYou builds read music folders from the saved server config, not from the binary itself. "
            f"Update audio_playback.music_library_dirs in the config stored under {config_dir}, "
            f"or launch the app with {MUSIC_LIBRARY_DIRS_ENV} set."
        )

    return (
        f"Set {MUSIC_LIBRARY_DIRS_ENV} to your music folder path(s), "
        "or pass library_dirs as a semicolon-separated list of folders."
    )

def _format_track_title(file_name: str) -> str:
    stem, _ = os.path.splitext(file_name)
    return re.sub(r"[_\-]+", " ", stem).strip() or file_name

def _build_music_track(file_path: str) -> Dict[str, Any]:
    absolute_path = os.path.abspath(file_path)
    file_name = os.path.basename(absolute_path)
    track_id = hashlib.sha1(os.path.normcase(absolute_path).encode("utf-8")).hexdigest()[:12]
    return {
        "track_id": track_id,
        "title": _format_track_title(file_name),
        "file_name": file_name,
        "file_path": absolute_path,
        "directory": os.path.dirname(absolute_path),
    }

def _normalize_path_key(path_value: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path_value or ""))))

def _uses_default_runtime_music_dirs(resolved_dirs: List[str], library_dirs: Optional[str]) -> bool:
    if library_dirs not in (None, ""):
        return False

    default_dirs = [_normalize_path_key(item) for item in get_default_music_library_dirs(anchor=__file__)]
    current_dirs = [_normalize_path_key(item) for item in resolved_dirs]
    return bool(current_dirs) and current_dirs == default_dirs

def _should_skip_music_scan_dir(dir_name: str) -> bool:
    normalized = str(dir_name or "").strip().lower()
    if not normalized:
        return True
    if normalized.startswith("."):
        return True
    return normalized in _COMMON_MUSIC_SCAN_SKIP_DIR_NAMES

def _allow_default_runtime_root_dir(dir_name: str) -> bool:
    normalized = str(dir_name or "").strip().lower()
    if not normalized or _should_skip_music_scan_dir(normalized):
        return False
    return normalized in _DEFAULT_RUNTIME_MEDIA_DIR_NAMES

def _iter_music_tracks(
    library_dirs: List[str],
    limit: Optional[int],
    *,
    restrict_default_runtime_root: bool = False,
) -> List[Dict[str, Any]]:
    tracks: List[Dict[str, Any]] = []
    for root_dir in library_dirs:
        root_key = _normalize_path_key(root_dir)
        for root, dirnames, filenames in os.walk(root_dir):
            dirnames.sort()
            if restrict_default_runtime_root and _normalize_path_key(root) == root_key:
                dirnames[:] = [name for name in dirnames if _allow_default_runtime_root_dir(name)]
            else:
                dirnames[:] = [name for name in dirnames if not _should_skip_music_scan_dir(name)]
            for file_name in sorted(filenames):
                if os.path.splitext(file_name)[1].lower() not in _MUSIC_EXTENSIONS:
                    continue
                tracks.append(_build_music_track(os.path.join(root, file_name)))
                if limit is not None and len(tracks) >= limit:
                    return tracks
    return tracks

def _normalize_music_search_limit(limit: Optional[int]) -> Optional[int]:
    """Normalize search limits; None/<=0 means uncapped discovery."""
    if limit is None:
        return None
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None

def _music_match_score(track: Dict[str, Any], query: str) -> int:
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return 1

    title = str(track.get("title") or "").lower()
    file_name = str(track.get("file_name") or "").lower()
    # Deliberately exclude file_path from fuzzy token matching so that
    # numeric fragments in temp/test directory names (e.g. pytest-992)
    # do not spuriously match short numeric queries against every track.
    haystack = " ".join([title, file_name])

    if normalized_query == str(track.get("track_id") or "").lower():
        return 1000
    if normalized_query == file_name:
        return 900
    if normalized_query == title:
        return 800

    tokens = [token for token in re.split(r"\s+", normalized_query) if token]
    if not tokens:
        return 1

    score = 0
    for token in tokens:
        if token in haystack:
            score += 10
        if token in title:
            score += 10
    if normalized_query in haystack:
        score += 50
    return score

def _get_music_state(tool_context: Optional[Any]) -> Dict[str, Any]:
    current = _state_get(tool_context, _MUSIC_CURRENT_KEY)
    queue = _state_get(tool_context, _MUSIC_QUEUE_KEY) or []
    history = _state_get(tool_context, _MUSIC_HISTORY_KEY) or []
    repeat_mode = str(_state_get(tool_context, _MUSIC_REPEAT_MODE_KEY) or "off").strip().lower()
    shuffle_enabled = bool(_state_get(tool_context, _MUSIC_SHUFFLE_KEY))
    return {
        "current": current if isinstance(current, dict) else None,
        "queue": [item for item in queue if isinstance(item, dict)],
        "history": [item for item in history if isinstance(item, dict)],
        "repeat_mode": repeat_mode if repeat_mode in {"off", "one", "all"} else "off",
        "shuffle_enabled": shuffle_enabled,
    }

def _store_music_state(
    tool_context: Optional[Any],
    *,
    current: Optional[Dict[str, Any]],
    queue: List[Dict[str, Any]],
    history: List[Dict[str, Any]],
    repeat_mode: str,
    shuffle_enabled: bool,
) -> None:
    _state_set(tool_context, _MUSIC_CURRENT_KEY, current)
    _state_set(tool_context, _MUSIC_QUEUE_KEY, queue)
    _state_set(tool_context, _MUSIC_HISTORY_KEY, history)
    _state_set(tool_context, _MUSIC_REPEAT_MODE_KEY, repeat_mode)
    _state_set(tool_context, _MUSIC_SHUFFLE_KEY, bool(shuffle_enabled))

def _fallback_webrtc_reply_target(
    tool_context: Optional[Any],
    *,
    explicit_owner_key: Optional[str] = None,
    explicit_session_control: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    owner_key = str(explicit_owner_key or "").strip()
    if not owner_key and isinstance(explicit_session_control, dict):
        owner_key = str(explicit_session_control.get("owner_key") or "").strip()
        if not owner_key:
            canonical_user_id = str(explicit_session_control.get("canonical_user_id") or "").strip()
            if canonical_user_id.startswith("user::"):
                owner_key = canonical_user_id[len("user::"):].strip()

    if owner_key:
        return {"transport": "webrtc", "owner_key": owner_key}

    owner_key = str(
        _state_get(
            tool_context,
            AUTOYOU_OWNER_KEY_USER_STATE_KEY,
            AUTOYOU_OWNER_KEY_STATE_KEY,
        )
        or ""
    ).strip()
    if not owner_key:
        session_control = _state_get(tool_context, SESSION_CONTROL_STATE_KEY)
        if isinstance(session_control, dict):
            owner_key = str(session_control.get("owner_key") or "").strip()
            if not owner_key:
                canonical_user_id = str(session_control.get("canonical_user_id") or "").strip()
                if canonical_user_id.startswith("user::"):
                    owner_key = canonical_user_id[len("user::"):].strip()
    if not owner_key:
        return None
    return {"transport": "webrtc", "owner_key": owner_key}

def _explicit_playback_context_args(state: Any) -> Dict[str, Any]:
    args: Dict[str, Any] = {}

    reply_target = _resolve_saved_reply_target(state)
    if reply_target and str(reply_target.get("transport") or "").strip().lower() == "webrtc":
        args["reply_target"] = dict(reply_target)

    session_control = _state_get(state, SESSION_CONTROL_STATE_KEY)
    if isinstance(session_control, dict):
        compact_session_control = {
            key: str(session_control.get(key) or "").strip()
            for key in ("owner_key", "canonical_user_id", "canonical_session_id")
            if str(session_control.get(key) or "").strip()
        }
        if compact_session_control:
            args["session_control"] = compact_session_control

    owner_key = str(
        _state_get(
            state,
            AUTOYOU_OWNER_KEY_USER_STATE_KEY,
            AUTOYOU_OWNER_KEY_STATE_KEY,
        )
        or ""
    ).strip()
    if owner_key:
        args["owner_key"] = owner_key

    if "reply_target" not in args:
        fallback_reply_target = _fallback_webrtc_reply_target(
            state,
            explicit_owner_key=args.get("owner_key"),
            explicit_session_control=args.get("session_control"),
        )
        if fallback_reply_target:
            args["reply_target"] = fallback_reply_target

    return args

def _saved_webrtc_payload(
    tool_context: Optional[Any],
    *,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    reply_target = normalize_reply_target(reply_target) or _resolve_saved_reply_target(
        getattr(tool_context, "state", None) if tool_context is not None else None
    )
    fallback_reply_target = _fallback_webrtc_reply_target(
        tool_context,
        explicit_owner_key=owner_key,
        explicit_session_control=session_control,
    )
    if not reply_target:
        if not fallback_reply_target:
            return {
                "status": "error",
                "message": "No active WebRTC voice session is available for this conversation.",
            }
        reply_target = fallback_reply_target
    elif str(reply_target.get("transport") or "").strip().lower() != "webrtc":
        if not fallback_reply_target:
            return {
                "status": "error",
                "message": "No active WebRTC voice session is available for this conversation.",
            }
        reply_target = fallback_reply_target

    payload: Dict[str, Any] = {}
    session_id = str(reply_target.get("session_id") or "").strip()
    owner_key = str(reply_target.get("owner_key") or "").strip()
    if session_id:
        payload["session_id"] = session_id
    if owner_key:
        payload["owner_key"] = owner_key
    if not payload:
        return {"status": "error", "message": "Saved WebRTC reply target is missing both session_id and owner_key."}
    return {"status": "success", "payload": payload, "reply_target": reply_target}

def _call_saved_webrtc_playback(
    endpoint: str,
    tool_context: Optional[Any],
    *,
    payload: Optional[Dict[str, Any]] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    if not _audio_playback_enabled():
        return {
            "status": "error",
            "message": "Audio playback is disabled in server settings.",
            "state": "disabled",
        }

    token: Optional[str] = None
    if _check_admin_session(tool_context):
        token = _get_session_token(tool_context)
        if not token:
            return {
                "status": "error",
                "message": "Admin session token missing. Call verify_admin_totp() again before retrying this admin request.",
            }
    else:
        token = _get_internal_ai_agent_api_token()
        if not token:
            return {
                "status": "error",
                "message": "Internal WebRTC playback authorization is unavailable in this AI agent process. Restart the AI agent or AutoYou and try again.",
            }

    target = _saved_webrtc_payload(
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
    )
    # from __debug_provenance_t__ import address
    if target.get("status") != "success":
        return target

    request_payload = dict(target.get("payload") or {})
    if payload:
        request_payload.update(payload)
    result = _http(
        "POST",
        endpoint,
        request_payload,
        port=port,
        host=host,
        timeout=30,
        token=token,
    )
    data = result.get("data") if isinstance(result, dict) else None
    if isinstance(data, dict):
        if "status" in data:
            result.setdefault("playback_status", data.get("status"))
        if result.get("status") != "success":
            message = str(data.get("error") or data.get("detail") or "").strip()
            if message:
                result["message"] = message
    return result

def search_local_audio_library(
    query: str = "",
    limit: Optional[int] = None,
    library_dirs: Optional[str] = None,
    tool_context: Optional[Any] = None,
    remember_results: bool = True,
) -> Dict[str, Any]:
    """Search configured local music folders for audio files."""
    resolved_dirs = _normalize_music_dirs(library_dirs)
    if not resolved_dirs:
        return {
            "status": "error",
            "message": f"No music library directories are configured. {_music_library_configuration_guidance()}",
        }

    query_text = str(query or "").strip()
    result_limit = _normalize_music_search_limit(limit)
    restrict_default_runtime_root = _uses_default_runtime_music_dirs(resolved_dirs, library_dirs)
    if result_limit is None:
        scan_budget = None
    else:
        scan_budget = result_limit if not query_text and restrict_default_runtime_root else max(result_limit * 20, 200)
    scanned_tracks = _iter_music_tracks(
        resolved_dirs,
        scan_budget,
        restrict_default_runtime_root=restrict_default_runtime_root,
    )
    ranked = []
    for track in scanned_tracks:
        score = _music_match_score(track, query)
        if score <= 0:
            continue
        ranked.append((score, track))

    ranked.sort(key=lambda item: (-item[0], str(item[1].get("title") or "").lower(), str(item[1].get("file_path") or "").lower()))
    results = []
    selected_ranked = ranked if result_limit is None else ranked[:result_limit]
    for index, (_, track) in enumerate(selected_ranked, start=1):
        result = dict(track)
        result["result_index"] = index
        results.append(result)

    if remember_results:
        _state_set(tool_context, _MUSIC_LAST_RESULTS_KEY, results)
    if not results and restrict_default_runtime_root:
        root_label = Path(resolved_dirs[0]).name or resolved_dirs[0]
        return {
            "status": "success",
            "library_dirs": resolved_dirs,
            "query": query_text,
            "count": 0,
            "tracks": [],
            "message": (
                f"No tracks were found under the default runtime folders rooted at {root_label}. "
                f"{_music_library_configuration_guidance()}"
            ),
        }

    return {
        "status": "success",
        "library_dirs": resolved_dirs,
        "query": query_text,
        "count": len(results),
        "tracks": results,
        "message": f"Found {len(results)} track(s).",
    }

def _resolve_track_selection(
    selection: str,
    tool_context: Optional[Any],
    *,
    library_dirs: Optional[str] = None,
) -> Dict[str, Any]:
    raw_selection = str(selection or "").strip()
    if not raw_selection:
        return {"status": "error", "message": "track selection is required"}

    normalized_selection = _normalize_track_selection_text(raw_selection)
    selection_candidates: List[str] = []
    seen_candidates: set[str] = set()
    for candidate in (normalized_selection, raw_selection):
        value = str(candidate or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen_candidates:
            continue
        seen_candidates.add(lowered)
        selection_candidates.append(value)

    for candidate in selection_candidates:
        if os.path.isfile(candidate):
            return {"status": "success", "track": _build_music_track(candidate)}

    last_results = _state_get(tool_context, _MUSIC_LAST_RESULTS_KEY) or []
    for candidate in selection_candidates:
        if not candidate.isdigit():
            continue
        index = int(candidate) - 1
        if 0 <= index < len(last_results):
            track = last_results[index]
            if isinstance(track, dict):
                return {"status": "success", "track": track}

    for candidate in selection_candidates:
        lowered_candidate = candidate.lower()
        for collection in (last_results, _state_get(tool_context, _MUSIC_QUEUE_KEY) or [], [_state_get(tool_context, _MUSIC_CURRENT_KEY)]):
            for track in collection:
                if not isinstance(track, dict):
                    continue
                if lowered_candidate in {
                    str(track.get("track_id") or "").lower(),
                    str(track.get("file_name") or "").lower(),
                    str(track.get("title") or "").lower(),
                }:
                    return {"status": "success", "track": track}

    multi_match_error: Optional[Dict[str, Any]] = None
    for candidate in selection_candidates:
        search_result = search_local_audio_library(
            candidate,
            limit=0,
            library_dirs=library_dirs,
            tool_context=tool_context,
            remember_results=False,
        )
        if search_result.get("status") != "success":
            continue

        matches = list(search_result.get("tracks") or [])
        if not matches:
            continue
        if len(matches) == 1:
            return {"status": "success", "track": matches[0]}

        lowered_candidate = candidate.lower()
        exact_matches = [
            track
            for track in matches
            if lowered_candidate in {
                str(track.get("file_name") or "").lower(),
                str(track.get("title") or "").lower(),
            }
        ]
        if len(exact_matches) == 1:
            return {"status": "success", "track": exact_matches[0]}

        if multi_match_error is None:
            multi_match_error = {
                "status": "error",
                "message": "Multiple tracks matched. Call search_local_audio_library() and use the result_index you want.",
                "matches": matches,
            }

    if multi_match_error is not None:
        return multi_match_error

    missing_label = normalized_selection or raw_selection
    return {"status": "error", "message": f"No local audio files matched '{missing_label}'."}

def _play_track_on_saved_reply_target(
    track: Dict[str, Any],
    tool_context: Optional[Any],
    *,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    result = _call_saved_webrtc_playback(
        "/api/webrtc/playback/play",
        tool_context,
        payload={"file_path": track["file_path"]},
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        port=port,
        host=host,
    )
    if result.get("status") == "success":
        result["track"] = track
    return result

def play_attachment_audio_on_saved_reply_target(
    attachments: List[Dict[str, Any]],
    *,
    intent_hint: Optional[str] = None,
    tool_context: Optional[Any] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Play a temp-saved audio attachment on the active saved WebRTC reply target."""
    audio_tracks: List[Dict[str, Any]] = []
    for attachment in attachments or []:
        if not isinstance(attachment, dict):
            continue
        mimetype = str(attachment.get("mimetype") or "").strip().lower()
        path_value = str(attachment.get("path") or "").strip()
        if not path_value or not os.path.isfile(path_value):
            continue
        if mimetype and not mimetype.startswith("audio/"):
            continue
        audio_tracks.append(_build_music_track(path_value))

    if not audio_tracks:
        return {
            "status": "error",
            "message": "No playable audio attachment path was available for this request.",
        }

    selected_track = audio_tracks[0]
    hint_text = str(intent_hint or "").strip()
    selection = _extract_tail_argument(
        hint_text,
        [
            r"\bplay\s+(?:song|track|audio|music)?\s*(?:called|named|titled)?\s+(.+)$",
            r"\bqueue\s+(.+)$",
        ],
    )
    if selection:
        ranked = sorted(
            ((
                _music_match_score(track, selection),
                track,
            ) for track in audio_tracks),
            key=lambda item: (-item[0], str(item[1].get("file_name") or "").lower()),
        )
        if ranked and ranked[0][0] > 0:
            selected_track = ranked[0][1]

    state = _get_music_state(tool_context)
    current = state["current"]
    history = list(state["history"])
    if current and current.get("track_id") != selected_track.get("track_id"):
        history.append(current)

    result = _play_track_on_saved_reply_target(
        selected_track,
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        port=port,
        host=host,
    )
    if result.get("status") == "success":
        _store_music_state(
            tool_context,
            current=selected_track,
            queue=list(state["queue"]),
            history=history,
            repeat_mode=state["repeat_mode"],
            shuffle_enabled=state["shuffle_enabled"],
        )
        result["track"] = selected_track
        result["message"] = f"Playing {selected_track['title']}."
    return result

def play_local_audio_on_saved_reply_target(
    track_selection: str,
    tool_context: Optional[Any] = None,
    library_dirs: Optional[str] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Play a local audio file on the active saved WebRTC reply target."""
    resolved = _resolve_track_selection(track_selection, tool_context, library_dirs=library_dirs)
    if resolved.get("status") != "success":
        return resolved

    track = dict(resolved["track"])
    state = _get_music_state(tool_context)
    current = state["current"]
    history = list(state["history"])
    if current and current.get("track_id") != track.get("track_id"):
        history.append(current)
    result = _play_track_on_saved_reply_target(
        track,
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        port=port,
        host=host,
    )
    if result.get("status") == "success":
        _store_music_state(
            tool_context,
            current=track,
            queue=list(state["queue"]),
            history=history,
            repeat_mode=state["repeat_mode"],
            shuffle_enabled=state["shuffle_enabled"],
        )
        result["message"] = f"Playing {track['title']}."
    return result

def queue_local_audio_on_saved_reply_target(
    track_selection: str,
    tool_context: Optional[Any] = None,
    library_dirs: Optional[str] = None,
) -> Dict[str, Any]:
    """Queue a local audio file for later playback on the saved WebRTC reply target."""
    resolved = _resolve_track_selection(track_selection, tool_context, library_dirs=library_dirs)
    if resolved.get("status") != "success":
        return resolved

    track = dict(resolved["track"])
    state = _get_music_state(tool_context)
    queue = list(state["queue"])
    if any(item.get("track_id") == track.get("track_id") for item in queue):
        return {"status": "success", "track": track, "message": f"{track['title']} is already queued."}
    queue.append(track)
    _store_music_state(
        tool_context,
        current=state["current"],
        queue=queue,
        history=state["history"],
        repeat_mode=state["repeat_mode"],
        shuffle_enabled=state["shuffle_enabled"],
    )
    return {
        "status": "success",
        "track": track,
        "queue_length": len(queue),
        "message": f"Queued {track['title']}.",
    }

def pause_saved_reply_target_audio(
    tool_context: Optional[Any] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Pause the currently playing saved WebRTC reply-target audio."""
    result = _call_saved_webrtc_playback(
        "/api/webrtc/playback/pause",
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        port=port,
        host=host,
    )
    if result.get("status") == "success":
        result["message"] = "Paused saved reply-target audio playback."
    return result

def resume_saved_reply_target_audio(
    tool_context: Optional[Any] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Resume paused saved WebRTC reply-target audio."""
    result = _call_saved_webrtc_playback(
        "/api/webrtc/playback/resume",
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        port=port,
        host=host,
    )
    if result.get("status") == "success":
        result["message"] = "Resumed saved reply-target audio playback."
    return result

def stop_saved_reply_target_audio(
    tool_context: Optional[Any] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Stop saved WebRTC reply-target audio playback and clear the current track."""
    result = _call_saved_webrtc_playback(
        "/api/webrtc/playback/stop",
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        port=port,
        host=host,
    )
    if result.get("status") == "success":
        state = _get_music_state(tool_context)
        _store_music_state(
            tool_context,
            current=None,
            queue=state["queue"],
            history=state["history"],
            repeat_mode=state["repeat_mode"],
            shuffle_enabled=state["shuffle_enabled"],
        )
        result["message"] = "Stopped saved reply-target audio playback."
    return result

def get_saved_reply_target_audio_status(
    tool_context: Optional[Any] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Get playback status for the saved WebRTC reply target."""
    result = _call_saved_webrtc_playback(
        "/api/webrtc/playback/status",
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        port=port,
        host=host,
    )
    state = _get_music_state(tool_context)
    result["music_state"] = state
    return result

def next_saved_reply_target_audio(
    tool_context: Optional[Any] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Play the next queued track on the saved WebRTC reply target."""
    state = _get_music_state(tool_context)
    current = state["current"]
    queue = list(state["queue"])
    history = list(state["history"])
    repeat_mode = state["repeat_mode"]
    shuffle_enabled = state["shuffle_enabled"]

    if repeat_mode == "one" and current:
        next_track = current
    else:
        if current:
            history.append(current)
        if not queue:
            if repeat_mode != "all":
                return {"status": "error", "message": "No queued tracks are available."}
            queue = list(history)
            history = []
        if not queue:
            return {"status": "error", "message": "No queued tracks are available."}
        next_index = random.randrange(len(queue)) if shuffle_enabled and len(queue) > 1 else 0
        next_track = queue.pop(next_index)

    result = _play_track_on_saved_reply_target(
        next_track,
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        port=port,
        host=host,
    )
    if result.get("status") == "success":
        _store_music_state(
            tool_context,
            current=next_track,
            queue=queue,
            history=history,
            repeat_mode=repeat_mode,
            shuffle_enabled=shuffle_enabled,
        )
        result["message"] = f"Playing next track: {next_track['title']}."
    return result

def previous_saved_reply_target_audio(
    tool_context: Optional[Any] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Return to the previous queued track on the saved WebRTC reply target."""
    state = _get_music_state(tool_context)
    current = state["current"]
    queue = list(state["queue"])
    history = list(state["history"])
    if not history:
        return {"status": "error", "message": "No previous track is available."}

    previous_track = history.pop()
    if current:
        queue.insert(0, current)
    result = _play_track_on_saved_reply_target(
        previous_track,
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        port=port,
        host=host,
    )
    if result.get("status") == "success":
        _store_music_state(
            tool_context,
            current=previous_track,
            queue=queue,
            history=history,
            repeat_mode=state["repeat_mode"],
            shuffle_enabled=state["shuffle_enabled"],
        )
        result["message"] = f"Playing previous track: {previous_track['title']}."
    return result

def set_saved_reply_target_audio_repeat_mode(mode: str, tool_context: Optional[Any] = None) -> Dict[str, Any]:
    """Set repeat mode for queued local audio: off, one, or all."""
    normalized = str(mode or "").strip().lower()
    if normalized not in {"off", "one", "all"}:
        return {"status": "error", "message": "repeat mode must be one of: off, one, all"}
    state = _get_music_state(tool_context)
    _store_music_state(
        tool_context,
        current=state["current"],
        queue=state["queue"],
        history=state["history"],
        repeat_mode=normalized,
        shuffle_enabled=state["shuffle_enabled"],
    )
    return {"status": "success", "repeat_mode": normalized, "message": f"Repeat mode set to {normalized}."}

def set_saved_reply_target_audio_shuffle(enabled: bool, tool_context: Optional[Any] = None) -> Dict[str, Any]:
    """Enable or disable shuffle for queued local audio playback."""
    state = _get_music_state(tool_context)
    shuffle_enabled = bool(enabled)
    _store_music_state(
        tool_context,
        current=state["current"],
        queue=state["queue"],
        history=state["history"],
        repeat_mode=state["repeat_mode"],
        shuffle_enabled=shuffle_enabled,
    )
    return {
        "status": "success",
        "shuffle_enabled": shuffle_enabled,
        "message": f"Shuffle {'enabled' if shuffle_enabled else 'disabled'}.",
    }

def shuffle_play_local_audio_on_saved_reply_target(
    query: str = "",
    limit: Optional[int] = None,
    tool_context: Optional[Any] = None,
    library_dirs: Optional[str] = None,
    reply_target: Optional[Dict[str, Any]] = None,
    owner_key: Optional[str] = None,
    session_control: Optional[Dict[str, Any]] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Search local audio, shuffle the matches, and start playback on the saved WebRTC reply target."""
    search_result = search_local_audio_library(query=query, limit=limit, library_dirs=library_dirs, tool_context=tool_context)
    if search_result.get("status") != "success":
        return search_result
    tracks = list(search_result.get("tracks") or [])
    if not tracks:
        return {"status": "error", "message": "No tracks are available to shuffle."}

    random.shuffle(tracks)
    current = tracks[0]
    queue = tracks[1:]
    result = _play_track_on_saved_reply_target(
        current,
        tool_context,
        reply_target=reply_target,
        owner_key=owner_key,
        session_control=session_control,
        port=port,
        host=host,
    )
    if result.get("status") == "success":
        _store_music_state(
            tool_context,
            current=current,
            queue=queue,
            history=[],
            repeat_mode=_get_music_state(tool_context)["repeat_mode"],
            shuffle_enabled=True,
        )
        result["queue_length"] = len(queue)
        result["message"] = f"Shuffle playing {current['title']} with {len(queue)} more track(s) queued."
    return result

def create_audio_agent(model_config: Any) -> Agent:
    tools = [
        search_local_audio_library,
        play_local_audio_on_saved_reply_target,
        queue_local_audio_on_saved_reply_target,
        pause_saved_reply_target_audio,
        resume_saved_reply_target_audio,
        stop_saved_reply_target_audio,
        get_saved_reply_target_audio_status,
        next_saved_reply_target_audio,
        previous_saved_reply_target_audio,
        set_saved_reply_target_audio_repeat_mode,
        set_saved_reply_target_audio_shuffle,
        shuffle_play_local_audio_on_saved_reply_target,
        get_current_datetime,
    ]

    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        before_model_callback=[_audio_before_model_callback],
        after_tool_callback=[_audio_after_tool_callback],
        tools=tools,
    )
