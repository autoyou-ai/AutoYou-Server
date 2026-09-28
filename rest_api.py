# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
REST API module for AutoYou AI Agent.

This module provides REST API endpoints that allow external chat applications
to interact with the ADK agent by sending full request messages and receiving
full response messages.
"""

import asyncio
import contextlib
import inspect
import json
import logging
import os
import uuid
import base64
import tempfile
import mimetypes
import re
from pathlib import Path
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Dict, List, Optional, Any, Callable
from urllib.parse import urlparse

import aiohttp
from fastapi import HTTPException
from pydantic import BaseModel, Field
from autoyou_agents.notes_agent.notes_tool import NotesTool
from autoyou_agents.shared_tools.agent_identity import (
    ROOT_AGENT_NAME,
    format_agent_display_name,
    is_root_agent_name,
    resolve_runtime_agent_name,
)
from autoyou_agents.agent_harness import is_progress_only_response
from shared.session_execution import (
    SESSION_CONTROL_STATE_KEY,
    STATUS_BREAKER_OPEN,
    STATUS_PAUSED,
    build_agent_steering_actions,
    build_coding_guard_message,
    build_session_execution_metadata,
    build_session_recovery_actions,
    normalize_session_control_state,
    prepare_session_control_for_turn,
)
from shared.adk_state import (
    AUTOYOU_CONVERSATION_SESSION_STATE_KEY,
    AUTOYOU_CONVERSATION_SESSION_USER_STATE_KEY,
    AUTOYOU_OWNER_KEY_STATE_KEY,
    AUTOYOU_OWNER_KEY_USER_STATE_KEY,
    AUTOYOU_REPLY_TARGET_STATE_KEY,
    AUTOYOU_REPLY_TARGET_USER_STATE_KEY,
    AUTOYOU_SCHEDULED_TASK_STATE_KEY,
    normalize_reply_target,
)
from shared.openclaw_gateway import (
    attachment_to_path as _shared_attachment_to_path,
    call_openclaw_gateway,
    derive_context_source_hint,
    extract_attachments_from_context as _shared_extract_attachments_from_context,
    rewrite_context_attachments_to_paths,
    safe_filename as _shared_safe_filename,
    summarize_attachments,
    write_bytes_to_temp as _shared_write_bytes_to_temp,
)
from shared.media_messaging import (
    extract_media_reply_attachments,
    public_media_reply_metadata,
)
from shared.ollama_context_policy import build_context_compaction_policy
from shared.ollama_capabilities import resolve_ollama_think_option
from shared.ollama_gateway import (
    OllamaConversationStore,
    OllamaGatewayError,
    call_ollama_with_history,
    get_ollama_model,
    inspect_ollama_capabilities,
    resolve_ollama_gateway_options,
    resolve_ollama_thinking_enabled,
    resolve_ollama_thinking_level,
)
from shared.odysseus_gateway import call_odysseus

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_THINK_BLOCK_TAGS = ("think", "thinking")
_NATIVE_OLLAMA_HISTORY = OllamaConversationStore()
_THINKING_PART_LABEL = "[Thinking]\n"
_CONTEXT_WINDOW_CACHE: Dict[tuple[str, str, Optional[int]], tuple[Optional[int], str]] = {}
_CONTEXT_HEALTH_COMMANDS = frozenset({
    "/context",
    "/memory",
})
_OLLAMA_MEMORY_REQUIREMENT_RE = re.compile(
    r"requires more system memory \((?P<required>[^)]+)\) than is available \((?P<available>[^)]+)\)",
    re.IGNORECASE,
)
_GENERIC_AI_FALLBACK_RESPONSES = frozenset({
    "i wasn't able to generate a useful response for that. please try rephrasing your request.",
    "i couldn't complete the request because the ai agent server returned an internal error.",
})
_CJK_TEXT_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]")
_INLINE_REASONING_LEAK_MARKERS = (
    "\u200bo|thought|",
    "o|thought|",
    "<|channel>thought",
    "<channel|>",
    "<|tool_response",
    "|tool_response",
    "|thought|",
    "thought}",
    "{thought",
    "思考过程：",
    "思考过程:",
    "思考過程：",
    "思考過程:",
    "outof_thought",
    "thoughtthought_",
    "---PROMPT ANALYSIS---",
    "---**[",
)
_REASONING_ANSWER_STOP_MARKERS = (
    "<|channel>",
    "<channel|>",
    "[thought]",
    "thought}",
    "{thought",
    "思考过程：",
    "思考过程:",
    "思考過程：",
    "思考過程:",
    "thoughtthought_",
    "thought--]",
    "outof_thought",
    "<|tool_response",
    "|tool_response",
    "---**[",
    "[Internal Note:",
    "[Decision:",
)
_REASONING_PREAMBLE_RE = re.compile(
    r"(?is)^\s*(?:"
    r"the\s+user\s+(?:is\s+asking|asked|wants|provided|has\s+provided)"
    r"|according\s+to\s+(?:the\s+)?system\s+instructions"
    r"|i\s+(?:need|should|will)\b"
    r"|we\s+need\s+to\b"
    r")"
)
_VISIBLE_RESPONSE_RECOVERY_RE = re.compile(
    r"(?is)(?:"
    r"\b(?:final\s+answer|final\s+response|answer|response)\s*[:\-]\s*(.+)$"
    r"|(\b(?:I\s+am|I'm|I'm|Current\s+local\s+time:|Added\s+|Uploaded\s+|Saved\s+|The\s+image\s+|The\s+audio\s+).+)$"
    r")"
)
_MALFORMED_REASONING_TRAILER_RE = re.compile(
    r'(?is)\bthought\s+[^,"\r\n:]{1,64}\s*[:,]\s*(.+?)(?:"\s*:\s*(?:"null"|null|""|\{|\[)|$)'
)
_LEADING_REASONING_SENTENCE_RE = re.compile(
    r"(?is)^\s*(?:"
    r"the\s+user\s+(?:is\s+asking|asked|wants|provided|has\s+provided)"
    r"|according\s+to\s+(?:the\s+)?system\s+instructions"
    r"|i\s+am\s*,\s*i\s+should\s+answer\b"
    r"|i\s+(?:need|should|will)\s+(?:answer|respond|reply|use|call|check|route|provide)\b"
    r"|we\s+need\s+to\b"
    r")"
)
_REASONING_SENTENCE_FRAGMENT_RE = re.compile(
    r"(?is)\b(?:"
    r"the\s+user\s+(?:is\s+asking|asked|wants|provided|has\s+provided)"
    r"|according\s+to\s+(?:the\s+)?system\s+instructions"
    r"|system\s+instructions?"
    r"|developer\s+instructions?"
    r"|i\s+(?:need|should|will)\s+(?:answer|respond|reply|use|call|check|route|provide)\b"
    r"|role\s+and\s+instructions?"
    r"|internal\s+reasoning"
    r")"
)
_PROVIDER_METADATA_TAIL_MARKERS = (
    "|off_type:",
    "|#off_type:",
    "|--thought:",
    "|#off_thought:",
    "|off_status:",
    "|---source_code:",
    "|off_query:",
    "|off_search_results:",
    "|off_images:",
    "|off_videos:",
    "|off_audio:",
    "|off_files:",
    "|off_tools:",
    "|off_notes:",
    "|off_summary:",
    "|#off_summary:",
    "|off_title:",
    "|off_transcripts:",
    "|off_history:",
    "|off_memory:",
    "|off_tasks:",
    "|off_alerts:",
    "|off_system:",
    "|off_environment:",
    "|off_capabilities:",
    "|off_agent_types:",
    "---|[SYSTEM CLOCK]",
)
_STRUCTURED_JSON_ANSWER_KEYS = {
    "answer",
    "content",
    "final",
    "final_answer",
    "final_response",
    "message",
    "output",
    "response",
    "result",
    "text",
}
_INTERNAL_REASONING_JSON_KEYS = {
    "action",
    "analysis",
    "arguments",
    "args",
    "input",
    "observation",
    "plan",
    "reasoning",
    "scratchpad",
    "thought",
    "tool",
    "tool_args",
    "tool_input",
    "tool_name",
}
_INTERNAL_REASONING_REQUIRED_JSON_KEYS = {"analysis", "reasoning", "thought"}
_DEFAULT_EMPTY_RESPONSE_TEXT = "(no response)"
_AI_AGENT_LOCAL_RECOVERY_LOCK: Optional[asyncio.Lock] = None
_AI_AGENT_LOCAL_RECOVERY_LOCK_LOOP: Optional[asyncio.AbstractEventLoop] = None

def _looks_like_malformed_visible_response_stub(text: Any) -> bool:
    """Return True for tiny broken JSON openers that should not be shown.

    Some Ollama reasoning models can produce a hidden thought part plus a
    visible content fragment such as ``{"``. That fragment is not a user answer
    and should not make the SSE selector treat the event as successful text.
    Legitimate JSON tool/user responses are left alone unless they are only an
    unterminated opening delimiter.
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if len(stripped) > 3:
        return False
    return bool(re.fullmatch(r"[\{\[]\s*[\"']?", stripped))

def _looks_like_provider_artifact_response(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if "<|tool_response" not in stripped and "|tool_response" not in stripped:
        return False
    prefix = re.split(r"<\|tool_response|\|tool_response", stripped, maxsplit=1)[0].strip()
    if not prefix:
        return True
    if _looks_like_malformed_visible_response_stub(prefix):
        return True
    if not prefix.startswith("{"):
        return False
    try:
        parsed = json.loads(prefix)
    except Exception:
        return bool(re.fullmatch(r'\{\s*"?(?:thought|reasoning|analysis)"?\s*:\s*"?\s*"?\s*\}?', prefix))
    if not isinstance(parsed, dict):
        return False
    if not parsed:
        return True
    artifact_keys = {"thought", "reasoning", "analysis", "tool_response"}
    if not set(str(key).strip().lower() for key in parsed).issubset(artifact_keys):
        return False
    return not any(_looks_like_natural_language_answer(str(value)) for value in parsed.values())

def _recover_malformed_visible_json_answer(text: Any) -> Optional[str]:
    """Recover visible answers emitted as broken JSON object keys.

    Gemma 4 can emit visible prose shaped like a broken JSON object key under
    tool-heavy prompts. Keep valid JSON untouched and only recover a malformed
    object opener when the first quoted string looks like prose.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if stripped.startswith('{"'):
        try:
            parsed = json.loads(stripped)
            structured_answer = _recover_structured_json_answer(parsed)
            if structured_answer:
                return structured_answer
            return _recover_single_entry_json_answer(parsed)
        except Exception:
            pass
    else:
        value_fragment_answer = _recover_malformed_json_value_answer(stripped)
        if value_fragment_answer:
            return value_fragment_answer
        return None

    quote_index = stripped.find('"')
    if quote_index < 0:
        return None
    try:
        decoded, _ = json.JSONDecoder().raw_decode(stripped[quote_index:])
    except Exception:
        decoded = _fallback_first_quoted_string(stripped, quote_index)
    if not isinstance(decoded, str):
        return None

    candidate = _clean_malformed_json_answer_candidate(decoded)
    if _looks_like_natural_language_answer(candidate):
        return candidate
    return None

def _recover_single_entry_json_answer(parsed: Any) -> Optional[str]:
    if not isinstance(parsed, dict) or len(parsed) != 1:
        return None
    key, value = next(iter(parsed.items()))
    if isinstance(value, str):
        candidate = _clean_malformed_json_answer_candidate(value)
        if _looks_like_natural_language_answer(candidate):
            return candidate
    if isinstance(key, str):
        candidate = _clean_malformed_json_answer_candidate(key)
        if _looks_like_natural_language_answer(candidate):
            return candidate
    return None

def _recover_structured_json_answer(parsed: Any) -> Optional[str]:
    if not isinstance(parsed, dict):
        return None
    for key in _STRUCTURED_JSON_ANSWER_KEYS:
        value = parsed.get(key)
        if not isinstance(value, str):
            continue
        candidate = _clean_malformed_json_answer_candidate(value)
        if _looks_like_safe_reasoning_answer(candidate):
            return candidate
    return None

def _looks_like_internal_reasoning_json(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return False
    try:
        parsed = json.loads(stripped)
    except Exception:
        return False
    if not isinstance(parsed, dict) or not parsed:
        return False
    if _recover_structured_json_answer(parsed):
        return False
    keys = {str(key).strip().lower() for key in parsed.keys()}
    if not keys.intersection(_INTERNAL_REASONING_REQUIRED_JSON_KEYS):
        return False
    if keys.issubset(_INTERNAL_REASONING_JSON_KEYS):
        return True
    external_values = [
        value
        for key, value in parsed.items()
        if str(key).strip().lower() not in _INTERNAL_REASONING_JSON_KEYS
    ]
    return not any(
        isinstance(value, str) and _looks_like_safe_reasoning_answer(value)
        for value in external_values
    )

def _recover_malformed_json_value_answer(text: str) -> Optional[str]:
    if not isinstance(text, str) or not text.strip():
        return None
    if not re.search(r'"\s*:\s*"', text):
        return None
    has_provider_artifact = "<|" in text or "|tool_response" in text or text.rstrip().endswith("}")
    match = re.search(r'"\s*:\s*"', text)
    if not match:
        return None
    prefix = text[: match.start()].strip(" \t\r\n\"'{}[]_*`")
    quote_index = match.end() - 1
    try:
        decoded, _ = json.JSONDecoder().raw_decode(text[quote_index:])
    except Exception:
        decoded = _fallback_first_quoted_string(text, quote_index)
    if not isinstance(decoded, str):
        return None
    candidate = _clean_malformed_json_answer_candidate(decoded)
    if not has_provider_artifact and len(candidate) < len(prefix) + 10:
        return None
    if _looks_like_natural_language_answer(candidate):
        return candidate
    return None

def _fallback_first_quoted_string(text: str, quote_index: int) -> Optional[str]:
    escaped = False
    chars: list[str] = []
    for char in text[quote_index + 1:]:
        if escaped:
            chars.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            return "".join(chars)
        chars.append(char)
    return "".join(chars).strip() or None

def _clean_malformed_json_answer_candidate(candidate: str) -> str:
    cleaned = re.sub(r"\s+", " ", candidate).strip()
    marker_index = min(
        (
            index
            for marker in _PROVIDER_METADATA_TAIL_MARKERS
            for index in [cleaned.find(marker)]
            if index >= 20
        ),
        default=-1,
    )
    if marker_index >= 0:
        cleaned = cleaned[:marker_index].rstrip()
    cjk_match = _CJK_TEXT_RE.search(cleaned)
    if cjk_match and cjk_match.start() >= 20:
        prefix = cleaned[: cjk_match.start()].rstrip()
        boundary = max(prefix.rfind("."), prefix.rfind("!"), prefix.rfind("?"))
        if boundary >= 15:
            cleaned = prefix[: boundary + 1].strip()
    cleaned = re.sub(r'\s*["\']?\s*:\s*["\']?\s*$', "", cleaned).strip()
    return cleaned.strip(" \t\r\n\"'{}[]_*`")

def _looks_like_natural_language_answer(candidate: str) -> bool:
    if len(candidate) < 16:
        return False
    lower = candidate.strip().lower()
    if lower in {"content", "message", "response", "role", "status", "text"}:
        return False
    words = re.findall(r"[A-Za-z][A-Za-z']+", candidate)
    if len(words) < 4:
        return False
    if not re.search(r"\s", candidate):
        return False
    return bool(re.search(r"[.!?]", candidate)) or len(words) >= 8

def _strip_inline_reasoning_leakage(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    stripped = text.lstrip()
    if any(stripped.startswith(marker) for marker in _INLINE_REASONING_LEAK_MARKERS):
        return ""
    sentence_cleaned = _strip_leading_reasoning_sentences(stripped)
    if sentence_cleaned != stripped:
        return sentence_cleaned
    preamble_cleaned = _strip_reasoning_preamble(stripped)
    if preamble_cleaned != stripped:
        return preamble_cleaned
    best_index: Optional[int] = None
    for marker in _INLINE_REASONING_LEAK_MARKERS:
        index = text.find(marker)
        if index > 0 and (best_index is None or index < best_index):
            best_index = index
    if best_index is None:
        return text
    prefix = text[:best_index].strip()
    return prefix if _looks_like_natural_language_answer(prefix) else text

def _strip_leading_reasoning_sentences(text: str) -> str:
    if not isinstance(text, str) or not _LEADING_REASONING_SENTENCE_RE.match(text):
        return text
    remaining = text.strip()
    changed = False
    for _ in range(4):
        match = re.match(r"(?is)^\s*(.+?[.!?])(?:\s+|$)(.*)$", remaining)
        first_sentence = match.group(1).strip() if match else remaining
        if not _REASONING_SENTENCE_FRAGMENT_RE.search(first_sentence):
            break
        remaining = (match.group(2).strip() if match else "")
        changed = True
        if not remaining:
            break
    return remaining if changed else text

def _strip_reasoning_preamble(text: str) -> str:
    if not isinstance(text, str) or not _REASONING_PREAMBLE_RE.match(text):
        return text
    for match in _VISIBLE_RESPONSE_RECOVERY_RE.finditer(text):
        candidate = next((group for group in match.groups() if isinstance(group, str) and group.strip()), "")
        candidate = _trim_reasoning_answer_candidate(candidate)
        if _looks_like_natural_language_answer(candidate):
            return candidate
    return ""

def _extract_safe_answer_from_reasoning_text(reasoning_text: Any) -> Optional[str]:
    if not isinstance(reasoning_text, str) or not reasoning_text.strip():
        return None
    compact = re.sub(r"\s+", " ", reasoning_text).strip()
    candidates: list[str] = []
    arrow_index = compact.rfind("-->")
    if arrow_index >= 0:
        candidates.append(compact[arrow_index + 3:])
    for pattern in (
        r"(?is)(?:final answer|final response|answer|response)\s*[:\-]\s*(.+)$",
        r"(?is)assistant\s*[:\-]\s*(.+)$",
    ):
        match = re.search(pattern, compact)
        if match:
            candidates.append(match.group(1))
    for match in _MALFORMED_REASONING_TRAILER_RE.finditer(compact):
        candidates.append(match.group(1))
    for raw_candidate in candidates:
        candidate = _trim_reasoning_answer_candidate(raw_candidate)
        if _looks_like_safe_reasoning_answer(candidate):
            return candidate
    return None

def _trim_reasoning_answer_candidate(candidate: str) -> str:
    cleaned = candidate.strip()
    for marker in _REASONING_ANSWER_STOP_MARKERS:
        index = cleaned.find(marker)
        if index >= 0:
            cleaned = cleaned[:index].strip()
    cleaned = re.sub(r"(?is)([.!?])\s*thought\b.*$", r"\1", cleaned).strip()
    return re.sub(r"\s+", " ", cleaned.strip(" -*_`>|")).strip()

def _looks_like_safe_reasoning_answer(candidate: str) -> bool:
    if not _looks_like_natural_language_answer(candidate):
        return False
    lowered = candidate.lower()
    blocked_fragments = (
        "internal note",
        "system prompt",
        "developer instruction",
        "chain of thought",
        "i should",
        "the user asked",
        "provided instructions",
        "routing decision",
        "<|channel>",
        "<channel|>",
        "thought",
    )
    return not any(fragment in lowered for fragment in blocked_fragments)

def _extract_safe_answer_from_thought_parts(parts: Any) -> Optional[str]:
    for part in parts or []:
        if not isinstance(part, dict) or not bool(part.get("thought")):
            continue
        answer = _extract_safe_answer_from_reasoning_text(part.get("text"))
        if answer:
            return answer
    return None

def _prefer_complete_thought_answer_if_visible_is_clipped(
    visible_text: Any,
    thought_answer: Any,
) -> Optional[str]:
    if not isinstance(visible_text, str) or not isinstance(thought_answer, str):
        return None
    visible = re.sub(r"\s+", " ", visible_text).strip()
    complete = re.sub(r"\s+", " ", thought_answer).strip()
    if not visible or not complete or len(complete) <= len(visible) + 8:
        return None
    if not _looks_like_safe_reasoning_answer(complete):
        return None
    visible_lower = visible.lower()
    complete_lower = complete.lower()
    if complete_lower.endswith(visible_lower):
        return complete
    suffix_start = complete_lower.rfind(visible_lower)
    if suffix_start >= 0 and suffix_start >= max(0, len(complete_lower) - len(visible_lower) - 8):
        return complete
    return None

def _get_run_sse_timeout() -> aiohttp.ClientTimeout:
    """Build the timeout policy for long-running ADK /run_sse requests.

    `aiohttp` defaults to a 300-second total timeout. That is too short for
    repo-scale coding tasks. Use an explicit env-configurable timeout and leave
    `sock_read` unbounded so active SSE streams are not cut off mid-run.
    """
    raw_total = str(os.environ.get("AI_AGENT_RUN_SSE_TIMEOUT_SECONDS", "1800")).strip()
    total_timeout = None
    try:
        parsed_total = float(raw_total)
        if parsed_total > 0:
            total_timeout = parsed_total
    except Exception:
        total_timeout = 1800.0
    return aiohttp.ClientTimeout(
        total=total_timeout,
        connect=30.0,
        sock_connect=30.0,
        sock_read=None,
    )

def _get_progress_update_interval_seconds() -> float:
    raw_value = str(os.environ.get("AI_AGENT_PROGRESS_UPDATE_SECONDS", "20")).strip()
    try:
        parsed = float(raw_value)
        if parsed > 0:
            return parsed
    except Exception:
        pass
    return 20.0

def _extract_sse_events(buffer: str) -> tuple[list[dict[str, Optional[str]]], str]:
    """Parse complete SSE event blocks from a text buffer.

    Returns:
        (events, remainder)
    """
    normalized = buffer.replace("\r\n", "\n").replace("\r", "\n")
    blocks = normalized.split("\n\n")
    if not blocks:
        return [], ""

    complete_blocks = blocks[:-1]
    remainder = blocks[-1]
    events: list[dict[str, Optional[str]]] = []

    for block in complete_blocks:
        if not block.strip():
            continue
        event_name: Optional[str] = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if not line:
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip() or None
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            events.append({"event": event_name, "data": "\n".join(data_lines)})

    return events, remainder

async def _emit_progress_chunk(
    on_chunk: Optional[Callable[[Dict[str, Any]], Any]],
    *,
    elapsed_seconds: int,
) -> None:
    if not on_chunk:
        return
    candidate = {
        "_autoyou_progress": True,
        "progress_text": f"Still working... {elapsed_seconds}s elapsed.",
    }
    try:
        result = on_chunk(candidate)
        if asyncio.iscoroutine(result):
            await result
    except Exception as cb_err:
        logger.debug(f"on_chunk progress error: {cb_err}")

async def _progress_heartbeat_loop(
    stop_event: asyncio.Event,
    last_chunk_state: Dict[str, float],
    on_chunk: Optional[Callable[[Dict[str, Any]], Any]],
) -> None:
    # Use a shorter interval so clients (30-90 s timeout) receive heartbeats
    # well within their window even when the model is purely in thinking phase.
    interval = min(_get_progress_update_interval_seconds(), 12.0)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        now = loop.time()
        # Suppress heartbeat only when *visible* content arrived recently.
        # Think-only chunks update last_real_chunk_at but NOT last_visible_chunk_at,
        # so reasoning-heavy models (DeepSeek-R1, Claude extended thinking) keep
        # the client's thinking indicator alive even when no user-visible text flows.
        last_visible_chunk_at = last_chunk_state.get(
            "last_visible_chunk_at",
            last_chunk_state.get("last_real_chunk_at", started_at),
        )
        if now - last_visible_chunk_at >= interval:
            await _emit_progress_chunk(
                on_chunk,
                elapsed_seconds=max(1, int(now - started_at)),
            )

def _strip_reasoning_markup(
    text: Any,
    *,
    state: Optional[Dict[str, Any]] = None,
) -> str:
    """Remove provider reasoning blocks from visible assistant text.

    Supports both explicit ADK thought parts and inline `<think>...</think>`
    / `<thinking>...</thinking>` wrappers that some reasoning models emit.
    When ``state`` is provided, unmatched opening tags are tracked across
    streaming chunks so client UIs do not briefly render hidden reasoning.
    """
    if not isinstance(text, str) or not text:
        return ""

    local_state = state if state is not None else {"inside_think_block": False}
    lower_text = text.lower()
    cursor = 0
    visible_segments: list[str] = []
    inside_think_block = bool(local_state.get("inside_think_block"))

    while cursor < len(text):
        if inside_think_block:
            close_candidates: list[tuple[int, int]] = []
            for tag in _THINK_BLOCK_TAGS:
                close_tag = f"</{tag}>"
                close_index = lower_text.find(close_tag, cursor)
                if close_index >= 0:
                    close_candidates.append((close_index, len(close_tag)))
            if not close_candidates:
                local_state["inside_think_block"] = True
                return "".join(visible_segments)
            close_index, close_len = min(close_candidates, key=lambda item: item[0])
            cursor = close_index + close_len
            inside_think_block = False
            continue

        open_candidates: list[int] = []
        for tag in _THINK_BLOCK_TAGS:
            open_index = lower_text.find(f"<{tag}", cursor)
            if open_index >= 0:
                open_candidates.append(open_index)
        if not open_candidates:
            visible_segments.append(text[cursor:])
            break

        open_index = min(open_candidates)
        visible_segments.append(text[cursor:open_index])
        tag_end = text.find(">", open_index)
        if tag_end < 0:
            inside_think_block = True
            cursor = len(text)
            break
        inside_think_block = True
        cursor = tag_end + 1

    local_state["inside_think_block"] = inside_think_block
    cleaned = "".join(visible_segments)
    cleaned = re.sub(r"</(?:think|thinking)\s*>", "", cleaned, flags=re.IGNORECASE)
    return cleaned

def _show_thinking_enabled() -> bool:
    """Return the admin-configured "Show model thinking" preference.

    Same lazy `from server import STATE` pattern used elsewhere in this file
    (see _get_session_manager above); defaults to False (hide reasoning) when
    config isn't reachable.
    """
    try:
        from server import STATE
        cfg = (STATE.config or {}).get("model_behavior", {})
        return bool(cfg.get("show_thinking", False)) if isinstance(cfg, dict) else False
    except Exception:
        return False

def _sanitize_visible_agent_parts(
    parts: Any,
    *,
    state: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return only the user-visible message parts from an ADK payload."""
    sanitized_parts: List[Dict[str, Any]] = []
    show_thinking = _show_thinking_enabled()
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        is_thought_part = bool(part.get("thought"))
        if is_thought_part and not show_thinking:
            continue
        part_copy = dict(part)
        text_value = part_copy.get("text")
        if is_thought_part and show_thinking:
            # Genuine reasoning the admin opted to see - label it clearly and
            # skip the leak-scrubbing pipeline below, which is meant for
            # supposedly-final answer text and would otherwise strip
            # legitimate reasoning as if it had leaked into the reply.
            stripped_text = text_value.strip() if isinstance(text_value, str) else ""
            if not stripped_text:
                continue
            part_copy["text"] = _THINKING_PART_LABEL + stripped_text
            sanitized_parts.append(part_copy)
            continue
        if isinstance(text_value, str):
            visible_text = _strip_reasoning_markup(text_value, state=state)
            if not visible_text.strip():
                continue
            if _looks_like_provider_artifact_response(visible_text):
                continue
            if _looks_like_internal_reasoning_json(visible_text):
                continue
            if _looks_like_malformed_visible_response_stub(visible_text):
                continue
            recovered_text = _recover_malformed_visible_json_answer(visible_text)
            if recovered_text:
                visible_text = recovered_text
            visible_text = _strip_inline_reasoning_leakage(visible_text)
            if not visible_text.strip():
                continue
            if _looks_like_provider_artifact_response(visible_text):
                continue
            if _looks_like_internal_reasoning_json(visible_text):
                continue
            part_copy["text"] = visible_text
        sanitized_parts.append(part_copy)
    thought_answer = _extract_safe_answer_from_thought_parts(parts)
    if thought_answer:
        if not sanitized_parts:
            sanitized_parts.append({"text": thought_answer})
        else:
            for part_copy in sanitized_parts:
                replacement = _prefer_complete_thought_answer_if_visible_is_clipped(
                    part_copy.get("text"),
                    thought_answer,
                )
                if replacement:
                    part_copy["text"] = replacement
                    break
    return sanitized_parts

def _sanitize_agent_event_payload(
    payload: Any,
    *,
    stream_state: Optional[Dict[str, Any]] = None,
) -> Any:
    """Remove hidden reasoning content from ADK event payloads."""
    if not isinstance(payload, dict):
        return payload

    sanitized_payload = dict(payload)
    for container_key in ("message", "content"):
        container = sanitized_payload.get(container_key)
        if not isinstance(container, dict):
            continue
        parts = container.get("parts")
        if not isinstance(parts, list):
            continue
        container_copy = dict(container)
        container_copy["parts"] = _sanitize_visible_agent_parts(parts, state=stream_state)
        sanitized_payload[container_key] = container_copy
    return sanitized_payload

def _extract_visible_agent_response_text(agent_response_data: Dict[str, Any]) -> Optional[str]:
    """Build the final client-visible assistant text from ADK response data."""
    for container_key in ("content", "message"):
        container = agent_response_data.get(container_key)
        if not isinstance(container, dict):
            continue
        parts = _sanitize_visible_agent_parts(container.get("parts"))
        if not parts:
            thought_answer = _extract_safe_answer_from_thought_parts(container.get("parts"))
            if thought_answer:
                return thought_answer
            continue
        response_text = "".join(
            str(part.get("text") or "")
            for part in parts
            if isinstance(part.get("text"), str)
        )
        response_text = response_text.strip()
        if response_text:
            response_text = re.sub(r"\n{3,}", "\n\n", response_text)
            return response_text

    for part in _payload_content_parts(agent_response_data):
        for response_key in ("functionResponse", "function_response"):
            response_payload = part.get(response_key)
            response_text = _coerce_visible_tool_response_text(response_payload)
            if response_text:
                return response_text
    return None

def _coerce_visible_tool_response_text(value: Any) -> Optional[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if not isinstance(value, dict):
        return None

    visible_chunks: List[str] = []
    for key in ("message", "text", "snapshot", "error", "error_message"):
        candidate = value.get(key)
        if isinstance(candidate, str):
            cleaned = candidate.strip()
            if cleaned and cleaned not in visible_chunks:
                visible_chunks.append(cleaned)
    if visible_chunks:
        return "\n\n".join(visible_chunks)

    for key in ("response", "result", "data", "output"):
        nested_text = _coerce_visible_tool_response_text(value.get(key))
        if nested_text:
            return nested_text
    return None

def _payload_visible_agent_response_text(payload: Any) -> Optional[str]:
    """Return visible assistant text for a raw SSE payload, if any."""
    if not isinstance(payload, dict):
        return None
    try:
        sanitized_payload = _sanitize_agent_event_payload(payload)
        return _extract_visible_agent_response_text(sanitized_payload)
    except Exception:
        return None

def _payload_content_parts(payload: Any) -> List[Dict[str, Any]]:
    """Return normalized content parts from an ADK event payload."""
    if not isinstance(payload, dict):
        return []
    for container_key in ("content", "message"):
        container = payload.get(container_key)
        if not isinstance(container, dict):
            continue
        parts = container.get("parts")
        if isinstance(parts, list):
            return [part for part in parts if isinstance(part, dict)]
    return []

def _payload_has_tool_activity(payload: Any) -> bool:
    """Return True when a payload contains tool calls, tool results, or long-running tool markers."""
    for part in _payload_content_parts(payload):
        if part.get("functionCall") or part.get("function_call"):
            return True
        if part.get("functionResponse") or part.get("function_response"):
            return True
    long_running_tool_ids = None
    if isinstance(payload, dict):
        long_running_tool_ids = payload.get("longRunningToolIds") or payload.get("long_running_tool_ids")
    return bool(long_running_tool_ids)

def _should_retry_empty_root_model_response(
    payload: Any,
    visible_response: Any,
) -> bool:
    if isinstance(visible_response, str) and visible_response.strip():
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("errorMessage") or payload.get("error_message"):
        return False
    if _payload_has_tool_activity(payload):
        return False
    author = resolve_runtime_agent_name(str(payload.get("author") or "").strip())
    if author and not is_root_agent_name(author):
        return False
    return True

def _classify_ai_backend_error(error_message: Any) -> Optional[Dict[str, str]]:
    raw_text = str(error_message or "").strip()
    if not raw_text:
        return None

    lowered = raw_text.lower()
    if "apple intelligence" in lowered:
        if "context" in lowered or "too large" in lowered:
            message = "This chat or agent instructions are too long for Apple Intelligence. Start a new chat, shorten the instructions, or choose another chat mode."
        elif "turn on" in lowered or "downloading" in lowered or "unavailable" in lowered or "requires" in lowered or "needs macos" in lowered:
            message = "Apple Intelligence is not ready on this Mac. Check Apple Intelligence in System Settings, or choose another chat mode."
        else:
            message = "Apple Intelligence could not complete this request. Try a shorter request or choose another chat mode."
        return {"provider": "apple_intelligence", "kind": "apple_intelligence_unavailable", "user_message": message}
    looks_like_ollama = any(
        marker in lowered
        for marker in (
            "ollama",
            "localhost:11434",
            "/api/chat",
            "requires more system memory",
            "memory layout cannot be allocated",
        )
    )

    if looks_like_ollama:
        memory_match = _OLLAMA_MEMORY_REQUIREMENT_RE.search(raw_text)
        if memory_match:
            required = memory_match.group("required").strip()
            available = memory_match.group("available").strip()
            return {
                "provider": "ollama",
                "kind": "ollama_insufficient_memory",
                "user_message": (
                    "The selected Ollama model can't run on this machine because it needs "
                    f"{required} of system memory but only {available} is available. "
                    "Choose a smaller Ollama model in the Admin Dashboard under AI Provider, "
                    "or free memory and try again."
                ),
            }
        if "memory layout cannot be allocated" in lowered:
            return {
                "provider": "ollama",
                "kind": "ollama_insufficient_memory",
                "user_message": (
                    "The selected Ollama model could not allocate enough memory on this machine. "
                    "Choose a smaller Ollama model in the Admin Dashboard under AI Provider, "
                    "or free memory and try again."
                ),
            }
        if any(
            fragment in lowered
            for fragment in (
                "connection refused",
                "all connection attempts failed",
                "failed to connect",
                "api connection error",
                "could not connect",
                "is not reachable",
            )
        ):
            return {
                "provider": "ollama",
                "kind": "ollama_unreachable",
                "user_message": (
                    "AutoYou couldn't reach the local Ollama runtime. "
                    "Open the Ollama app or run `ollama serve`, then try again."
                ),
            }
        if "not found" in lowered and "model" in lowered:
            return {
                "provider": "ollama",
                "kind": "ollama_model_missing",
                "user_message": (
                    "The selected Ollama model is not installed yet. "
                    "Pull it in the Admin Dashboard under AI Provider, or run `ollama pull <model>` and try again."
                ),
            }

    return None

def _should_replace_with_backend_error_message(agent_response: Optional[str]) -> bool:
    if not isinstance(agent_response, str):
        return True
    normalized = " ".join(agent_response.strip().lower().split())
    if not normalized:
        return True
    return normalized in _GENERIC_AI_FALLBACK_RESPONSES

def _merge_agent_response_payload(
    preferred_payload: Optional[Dict[str, Any]],
    trailing_payload: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Merge a text/error-bearing payload with trailing metadata from the stream."""
    if not isinstance(preferred_payload, dict):
        return dict(trailing_payload) if isinstance(trailing_payload, dict) else None
    if not isinstance(trailing_payload, dict):
        return dict(preferred_payload)

    merged = dict(preferred_payload)
    trailing_priority_keys = {
        "author",
        "actions",
        "branch",
        "citationMetadata",
        "errorCode",
        "errorMessage",
        "error_code",
        "error_message",
        "id",
        "invocationId",
        "longRunningToolIds",
        "partial",
        "turnComplete",
        "turn_complete",
        "finishReason",
        "finish_reason",
        "timestamp",
        "usageMetadata",
        "usage_metadata",
    }

    for key, value in trailing_payload.items():
        if key in ("content", "message") and key in merged:
            continue
        if key in trailing_priority_keys:
            if value not in (None, "", [], {}):
                merged[key] = value
            continue

        current_value = merged.get(key)
        if current_value in (None, "", [], {}) and value not in (None, "", [], {}):
            merged[key] = value

    return merged

def _select_agent_response_payload(
    *,
    last_payload: Optional[Dict[str, Any]],
    last_text_payload: Optional[Dict[str, Any]],
    last_error_payload: Optional[Dict[str, Any]],
    last_text_index: Optional[int] = None,
    last_error_index: Optional[int] = None,
    last_tool_activity_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Choose the best payload from an SSE stream for downstream response parsing."""
    if isinstance(last_error_payload, dict) and (
        last_text_index is None
        or (last_error_index is not None and last_error_index > last_text_index)
    ):
        return _merge_agent_response_payload(last_error_payload, last_payload)

    stale_text = (
        isinstance(last_text_payload, dict)
        and last_text_index is not None
        and last_tool_activity_index is not None
        and last_tool_activity_index > last_text_index
    )
    if isinstance(last_text_payload, dict):
        if not stale_text:
            return _merge_agent_response_payload(last_text_payload, last_payload)
    if isinstance(last_error_payload, dict):
        return _merge_agent_response_payload(last_error_payload, last_payload)
    if isinstance(last_payload, dict):
        return dict(last_payload)
    return None

# Utility: sanitize arbitrary metadata into JSON-serializable primitives
def _safe_jsonable(value: Any) -> Any:
    """Convert arbitrary Python objects to JSON-serializable primitives.

    - Dict: keys coerced to strings; values recursively sanitized
    - List/Tuple/Set: converted to lists with sanitized elements
    - datetime/date: ISO 8601 strings
    - bytes: UTF-8 decode fallback to base64
    - Pydantic models or objects with ``dict``/``__dict__``: converted to dicts
    - Floats with inf/nan: stringified to avoid JSON encoding issues
    - Unknown objects: ``str(value)``
    """
    try:
        from datetime import datetime as _dt_datetime, date as _dt_date

        if value is None:
            return None
        if isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            # Avoid NaN/Inf serialization issues
            if value != value or value in (float('inf'), float('-inf')):
                return str(value)
            return value
        if isinstance(value, _dt_datetime):
            try:
                return value.isoformat()
            except Exception:
                return str(value)
        if isinstance(value, _dt_date):
            return value.isoformat()
        if isinstance(value, bytes):
            try:
                return value.decode('utf-8', errors='ignore')
            except Exception:
                import base64 as _b64
                return _b64.b64encode(value).decode('ascii')
        if isinstance(value, dict):
            return {str(k): _safe_jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_safe_jsonable(v) for v in list(value)]
        try:
            from pydantic import BaseModel as _BM  # type: ignore
            if isinstance(value, _BM):
                return _safe_jsonable(value.dict())
        except Exception:
            pass
        try:
            if hasattr(value, 'dict'):
                return _safe_jsonable(value.dict())
            if hasattr(value, '__dict__'):
                return _safe_jsonable(vars(value))
        except Exception:
            pass
        return str(value)
    except Exception:
        return str(value)

def _metadata_summary(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Create a structured summary of metadata for logging.

    Returns a dict with:
    - keys: sorted list of top-level keys
    - types: mapping of top-level keys to simple type names
    - size_estimates: selected counts from known nested structures
    """
    try:
        keys = list(metadata.keys())
        types = {k: type(metadata.get(k)).__name__ for k in keys}

        size_estimates: Dict[str, Any] = {}
        # Attachments summary details
        att = metadata.get("attachments_summary")
        if isinstance(att, dict):
            for fld in ("count", "total_size_bytes", "path_saved_count", "path_skipped_count"):
                if fld in att:
                    size_estimates[f"attachments_summary.{fld}"] = att.get(fld)
            mimes = att.get("mimetypes")
            if isinstance(mimes, list):
                size_estimates["attachments_summary.mimetypes_count"] = len(mimes)

        # Usage metadata footprint
        usage = metadata.get("usage_metadata")
        if isinstance(usage, dict):
            size_estimates["usage_metadata_keys_count"] = len(usage.keys())

        return {
            "keys": sorted(keys),
            "types": types,
            "size_estimates": size_estimates,
        }
    except Exception:
        return {"keys": [], "types": {}, "size_estimates": {}}

def _log_metadata_summary(metadata: Dict[str, Any], label: str = "chat_response") -> None:
    """Log a structured summary of metadata safely.

    Ensures the content is JSON-safe and avoids raising during logging.
    """
    try:
        md = metadata if isinstance(metadata, dict) else {}
        # Metadata is expected to be sanitized already; re-sanitize defensively
        md_sanitized = _safe_jsonable(md) if isinstance(md, dict) else {}
        summary = _metadata_summary(md_sanitized if isinstance(md_sanitized, dict) else {})
        logger.info("ChatResponse metadata summary (%s): %s", label, json.dumps(summary))
    except Exception:
        # Never raise from logging helpers
        pass
# AI Agent Server configuration
def get_ai_agent_server_url() -> str:
    """Get the AI Agent Server base URL from environment variables or defaults.

    Respects optional `AI_AGENT_SERVER_HOST` and `AI_AGENT_SERVER_PORT` env vars,
    defaulting to `localhost` and `8081` respectively.
    """
    host = os.environ.get("AI_AGENT_SERVER_HOST", "localhost")
    port = os.environ.get("AI_AGENT_SERVER_PORT", "8081")
    return f"http://{host}:{port}"

def _get_ai_agent_local_recovery_lock() -> asyncio.Lock:
    global _AI_AGENT_LOCAL_RECOVERY_LOCK, _AI_AGENT_LOCAL_RECOVERY_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _AI_AGENT_LOCAL_RECOVERY_LOCK is None or _AI_AGENT_LOCAL_RECOVERY_LOCK_LOOP is not loop:
        _AI_AGENT_LOCAL_RECOVERY_LOCK = asyncio.Lock()
        _AI_AGENT_LOCAL_RECOVERY_LOCK_LOOP = loop
    return _AI_AGENT_LOCAL_RECOVERY_LOCK

def _is_loopback_ai_agent_url(base_url: str) -> tuple[bool, str, Optional[int]]:
    try:
        parsed = urlparse(base_url)
    except Exception:
        return False, "", None

    host = str(parsed.hostname or "").strip().lower()
    if host not in {"127.0.0.1", "::1", "localhost"}:
        return False, host, None

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return True, host, int(port)

async def _recover_local_ai_agent_server_if_possible(
    ai_agent_url: Optional[str],
    *,
    reason: str,
    treat_already_healthy_as_recovered: bool = False,
) -> bool:
    """Restart/wait for the local AI worker after a connection-level failure.

    Returns True only when the local worker was unhealthy and became healthy
    after this recovery attempt. A healthy worker that merely returned an ADK
    or model error is left alone unless the caller explicitly asks to retry a
    transient startup race.
    """
    if str(os.getenv("AUTOYOU_AI_AGENT_WORKER", "")).strip() == "1":
        return False

    base_url = ai_agent_url or get_ai_agent_server_url()
    is_loopback, host, port = _is_loopback_ai_agent_url(base_url)
    if not is_loopback or port is None:
        return False

    async with _get_ai_agent_local_recovery_lock():
        try:
            import server
        except Exception as exc:
            logger.debug("AI Agent recovery skipped; server module unavailable: %s", exc)
            return False

        expected_port = int(getattr(server, "AI_AGENT_SERVER_PORT", port) or port)
        if int(port) != expected_port:
            logger.debug(
                "AI Agent recovery skipped for %s; URL port %s does not match configured port %s",
                base_url,
                port,
                expected_port,
            )
            return False

        normalize_probe_host = getattr(server, "_normalize_probe_host", lambda value: value)
        probe_host = normalize_probe_host(host or getattr(server, "SERVER_BIND_HOST", "127.0.0.1"))
        health_check = getattr(server, "_is_ai_agent_server_healthy", None)
        if callable(health_check):
            try:
                if await asyncio.to_thread(health_check, probe_host, expected_port, 1.0):
                    return bool(treat_already_healthy_as_recovered)
            except Exception:
                pass

        state = getattr(server, "STATE", None)
        config = getattr(state, "config", None) if state is not None else None
        ai_agent_config = (config or {}).get("ai_agent", {}) if isinstance(config, dict) else {}
        if ai_agent_config and not bool(ai_agent_config.get("enabled", True)):
            logger.warning("AI Agent recovery skipped after %s because the AI Agent Server is disabled", reason)
            return False
        if ai_agent_config and not bool(ai_agent_config.get("auto_start", True)):
            logger.warning("AI Agent recovery skipped after %s because AI Agent auto-start is disabled", reason)
            return False

        start_server = getattr(server, "start_ai_agent_server_background", None)
        if not callable(start_server):
            return False

        logger.warning(
            "AI Agent Server on %s:%s is unavailable after %s; attempting automatic recovery",
            probe_host,
            expected_port,
            reason,
        )
        try:
            started = bool(await start_server())
        except Exception as exc:
            logger.error("Automatic AI Agent Server recovery failed after %s: %s", reason, exc, exc_info=True)
            return False

        if not started:
            return False

        if callable(health_check):
            try:
                return bool(await asyncio.to_thread(health_check, probe_host, expected_port, 1.0))
            except Exception:
                return True
        return True

async def _create_ai_agent_session_with_recovery(
    user_id: str,
    ai_agent_url: Optional[str],
    *,
    reason: str,
) -> Optional[str]:
    session_id = await create_ai_agent_session(user_id, ai_agent_url)
    if session_id:
        return session_id

    recovered = await _recover_local_ai_agent_server_if_possible(
        ai_agent_url,
        reason=reason,
        treat_already_healthy_as_recovered=True,
    )
    if not recovered:
        return None

    return await create_ai_agent_session(user_id, ai_agent_url)

# ── OpenClaw provider helpers ─────────────────────────────────────────────────

def _active_ai_provider() -> str:
    """Return the currently configured AI provider name (lower-cased).

    Priority: AI_PROVIDER env var (set by server.py from config.encrypted) >
    'ollama' default.
    """
    return os.environ.get("AI_PROVIDER", "ollama").strip().lower()


def _request_requires_autoyou_agent_graph(request: Any) -> bool:
    """Keep live web requests inside AutoYou's specialist graph.

    Hermes and OpenClaw normally use their direct gateway fast paths. A live
    web request is different: the AutoYou Internet specialist owns the browser
    tools and the fail-closed result handling, so bypassing the graph would
    let the provider answer without verified network evidence.
    """
    metadata = request.metadata if isinstance(getattr(request, "metadata", None), dict) else {}

    for key in ("steering_target", "agent_name", "route_target"):
        target = str(metadata.get(key) or "").strip()
        if target and resolve_runtime_agent_name(target) == resolve_runtime_agent_name("internet_agent"):
            return True

    candidate = str(metadata.get("scheduled_task_instruction") or "").strip()
    if not candidate:
        candidate = str(getattr(request, "message", "") or "").strip()
    if not candidate:
        return False

    try:
        from autoyou_agents.internet_agent.agent import is_internet_request

        return bool(is_internet_request(candidate))
    except Exception as exc:
        logger.debug("Could not classify provider request for AutoYou routing: %s", exc)
        return bool(
            re.search(
                r"\b(?:internet|web|online|browse|scrape|website|current|latest|recent|today|breaking)\b",
                candidate,
                flags=re.IGNORECASE,
            )
        )

def _openclaw_chat_url() -> str:
    """Return the OpenAI-compatible base URL for the OpenClaw gateway."""
    port = os.environ.get("OPENCLAW_PORT", "18789").strip()
    return f"http://127.0.0.1:{port}"

def _openclaw_agent_chat_url() -> str:
    """Return the OpenAI-compatible base URL for the OpenClaw sub-agent gateway."""
    port = os.environ.get("OPENCLAW_AGENT_PORT", os.environ.get("OPENCLAW_PORT", "18789")).strip()
    return f"http://127.0.0.1:{port}"

def _hermes_chat_url() -> str:
    """Return the OpenAI-compatible base URL for the Hermes Agent gateway."""
    port = os.environ.get("HERMES_PORT", "8642").strip()
    return f"http://127.0.0.1:{port}"

def _positive_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None

def _usage_positive_int(usage: Dict[str, Any], *keys: str) -> Optional[int]:
    for key in keys:
        if key in usage:
            parsed = _positive_int(usage.get(key))
            if parsed is not None:
                return parsed
    return None

def _normalize_ollama_model_name(model_name: str) -> str:
    normalized = str(model_name or "").strip()
    if normalized.startswith(("hf.co/", "huggingface.co/")):
        return normalized
    if "/" in normalized:
        provider, remainder = normalized.split("/", 1)
        if provider in {"ollama", "ollama_chat", "ollama_local", "ollama-local"} and remainder:
            return remainder
    return normalized

def _active_model_name_for_provider(provider: str) -> str:
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider == "apple_intelligence":
        return "apple_intelligence/on-device"
    if normalized_provider == "ollama":
        model_name = _normalize_ollama_model_name(os.environ.get("OLLAMA_MODEL", "ministral-3:8b"))
        return f"ollama_chat/{model_name}" if model_name else ""
    if normalized_provider == "google":
        base_model_name = str(os.environ.get("GOOGLE_MODEL", "gemini-2.5-flash") or "").strip()
        if not base_model_name:
            return ""
        if str(os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "")).strip().lower() in {"1", "true", "yes"}:
            return f"vertex_ai/{base_model_name}"
        return f"gemini/{base_model_name}"
    if normalized_provider == "litellm":
        return str(os.environ.get("LITELLM_MODEL", "") or "").strip()
    if normalized_provider == "openclaw":
        model_name = str(os.environ.get("OPENCLAW_MODEL", "") or "").strip()
        return f"openai/{model_name}" if model_name else ""
    if normalized_provider == "hermes":
        model_name = str(os.environ.get("HERMES_MODEL", "hermes-agent") or "hermes-agent").strip()
        return f"openai/{model_name}" if model_name else ""
    return ""

def _configured_ollama_num_ctx() -> Optional[int]:
    try:
        from autoyou_agents.model_config import resolve_ollama_num_ctx

        resolved_num_ctx, _resolved_source = resolve_ollama_num_ctx(
            _normalize_ollama_model_name(os.environ.get("OLLAMA_MODEL", "ministral-3:8b"))
        )
        return _positive_int(resolved_num_ctx)
    except Exception:
        return None

def _configured_ollama_num_ctx_source() -> Optional[str]:
    try:
        from autoyou_agents.model_config import resolve_ollama_num_ctx

        _resolved_num_ctx, resolved_source = resolve_ollama_num_ctx(
            _normalize_ollama_model_name(os.environ.get("OLLAMA_MODEL", "ministral-3:8b"))
        )
        return str(resolved_source or "").strip() or None
    except Exception:
        return None

def _is_ollama_model_context(provider: str, model_name: str) -> bool:
    normalized_provider = str(provider or "").strip().lower()
    normalized_model_name = str(model_name or "").strip().lower()
    if normalized_provider == "ollama":
        return True
    return normalized_model_name.startswith("ollama/") or normalized_model_name.startswith("ollama_chat/")

def _resolve_context_window(provider: str, model_name: str) -> tuple[Optional[int], Optional[str]]:
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider == "apple_intelligence":
        return 4096, "on_device_model"
    normalized_model_name = str(model_name or "").strip()
    is_ollama_model = _is_ollama_model_context(normalized_provider, normalized_model_name)
    ollama_num_ctx = _configured_ollama_num_ctx() if is_ollama_model else None
    cache_key = (normalized_provider, normalized_model_name, is_ollama_model, ollama_num_ctx)
    cached = _CONTEXT_WINDOW_CACHE.get(cache_key)
    if cached is not None:
        return cached[0], cached[1] or None

    if ollama_num_ctx:
        resolved = (ollama_num_ctx, _configured_ollama_num_ctx_source() or "machine_heuristic")
        _CONTEXT_WINDOW_CACHE[cache_key] = resolved
        return resolved

    if is_ollama_model:
        resolved = (None, "")
        _CONTEXT_WINDOW_CACHE[cache_key] = resolved
        return None, None

    if not normalized_model_name or normalized_provider == "openclaw":
        resolved = (None, "")
        _CONTEXT_WINDOW_CACHE[cache_key] = resolved
        return None, None

    try:
        import litellm

        model_info = litellm.get_model_info(normalized_model_name)
        if isinstance(model_info, dict):
            for key in ("max_input_tokens", "max_tokens"):
                limit = _positive_int(model_info.get(key))
                if limit:
                    resolved = (limit, f"litellm.{key}")
                    _CONTEXT_WINDOW_CACHE[cache_key] = resolved
                    return resolved
    except Exception as exc:
        logger.debug(
            "Failed to resolve context window for provider=%s model=%s: %s",
            normalized_provider,
            normalized_model_name,
            exc,
        )

    resolved = (None, "")
    _CONTEXT_WINDOW_CACHE[cache_key] = resolved
    return None, None

def _format_compact_token_count(value: Optional[int]) -> str:
    if value is None:
        return ""
    if value < 1000:
        return str(value)
    if value < 1_000_000:
        compact = value / 1000.0
        return f"{compact:.1f}k" if compact < 100 else f"{compact:.0f}k"
    compact = value / 1_000_000.0
    return f"{compact:.1f}m" if compact < 100 else f"{compact:.0f}m"

def _context_usage_alert_level(prompt_tokens: Optional[int], context_window: Optional[int]) -> str:
    if prompt_tokens is None or not context_window:
        return "normal"
    ratio = prompt_tokens / float(context_window)
    if ratio >= 0.85:
        return "critical"
    if ratio >= 0.70:
        return "warning"
    return "normal"

def _build_context_usage_snapshot(
    *,
    ai_agent_session_id: str,
    user_id: str,
    external_session_id: str,
    usage_metadata: Any,
) -> Optional[Dict[str, Any]]:
    usage = usage_metadata if isinstance(usage_metadata, dict) else {}
    prompt_tokens = _usage_positive_int(
        usage,
        "prompt_token_count",
        "promptTokenCount",
        "prompt_tokens",
    )
    candidates_tokens = _usage_positive_int(
        usage,
        "candidates_token_count",
        "candidatesTokenCount",
        "completion_tokens",
    )
    total_tokens = _usage_positive_int(
        usage,
        "total_token_count",
        "totalTokenCount",
        "total_tokens",
    )
    cached_tokens = _usage_positive_int(
        usage,
        "cached_content_token_count",
        "cachedContentTokenCount",
        "cached_prompt_tokens",
    )
    thoughts_tokens = _usage_positive_int(
        usage,
        "thoughts_token_count",
        "thoughtsTokenCount",
        "reasoning_tokens",
    )
    tool_use_prompt_tokens = _usage_positive_int(
        usage,
        "tool_use_prompt_token_count",
        "toolUsePromptTokenCount",
    )

    if all(
        value is None
        for value in (
            prompt_tokens,
            candidates_tokens,
            total_tokens,
            cached_tokens,
            thoughts_tokens,
            tool_use_prompt_tokens,
        )
    ):
        if usage:
            logger.debug(
                "Usage metadata present but no recognized token counters were found; keys=%s",
                sorted(str(key) for key in usage.keys()),
            )
        return None

    provider = _active_ai_provider()
    model_name = _active_model_name_for_provider(provider)
    context_window, context_window_source = _resolve_context_window(provider, model_name)
    usage_ratio = None
    if prompt_tokens is not None and context_window:
        usage_ratio = round(prompt_tokens / float(context_window), 6)

    summary_text = (
        f"Ctx {_format_compact_token_count(prompt_tokens)}/{_format_compact_token_count(context_window)}"
        if prompt_tokens is not None and context_window
        else (f"Ctx {_format_compact_token_count(prompt_tokens)}" if prompt_tokens is not None else "")
    )

    return {
        "available": True,
        "ai_agent_session_id": str(ai_agent_session_id or "").strip(),
        "conversation_session_id": str(external_session_id or "").strip(),
        "user_id": str(user_id or "").strip(),
        "provider": provider,
        "model": model_name,
        "prompt_tokens": prompt_tokens,
        "candidates_tokens": candidates_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "thoughts_tokens": thoughts_tokens,
        "tool_use_prompt_tokens": tool_use_prompt_tokens,
        "context_window": context_window,
        "context_window_source": context_window_source,
        "usage_ratio": usage_ratio,
        "alert_level": _context_usage_alert_level(prompt_tokens, context_window),
        "summary_text": summary_text,
        "updated_at_ms": int(datetime.now().timestamp() * 1000),
    }

def _match_context_health_command(text: Any) -> Optional[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return None
    command_token = normalized.split(None, 1)[0].lower()
    command_token = re.sub(r"@[A-Za-z0-9_]+$", "", command_token)
    if command_token in _CONTEXT_HEALTH_COMMANDS:
        return command_token
    return None

def _is_ollama_context_snapshot(snapshot: Dict[str, Any]) -> bool:
    provider = str(snapshot.get("provider") or "").strip().lower()
    model_name = str(snapshot.get("model") or "").strip().lower()
    return provider == "ollama" or model_name.startswith("ollama/") or model_name.startswith("ollama_chat/")

def _load_context_health_snapshot_for_request(
    request: "ChatRequest",
) -> Dict[str, Any]:
    session_manager = get_session_manager()
    provider = _active_ai_provider()
    model_name = _active_model_name_for_provider(provider)
    context_window, context_window_source = _resolve_context_window(provider, model_name)

    snapshot: Dict[str, Any] = {
        "available": False,
        "conversation_session_id": str(request.session_id or "").strip(),
        "user_id": str(request.user_id or "").strip(),
        "provider": provider,
        "model": model_name,
        "prompt_tokens": None,
        "candidates_tokens": None,
        "total_tokens": None,
        "cached_tokens": None,
        "thoughts_tokens": None,
        "tool_use_prompt_tokens": None,
        "context_window": context_window,
        "context_window_source": context_window_source,
        "usage_ratio": None,
        "alert_level": "normal",
        "summary_text": "",
        "updated_at_ms": int(datetime.now().timestamp() * 1000),
    }

    if (
        session_manager is None
        or not hasattr(session_manager, "get_session_context_usage_snapshot")
    ):
        return snapshot

    normalized_user_id = str(request.user_id or "").strip()
    normalized_session_id = str(request.session_id or "").strip()
    candidate_session_ids: List[str] = []
    if (
        normalized_session_id
        and hasattr(session_manager, "get_mapped_session_id")
    ):
        try:
            mapped_session_id = session_manager.get_mapped_session_id(
                normalized_session_id,
                normalized_user_id or None,
            )
            if mapped_session_id:
                candidate_session_ids.append(str(mapped_session_id))
        except Exception as exc:
            logger.debug(
                "Failed to resolve mapped session for context command %s/%s: %s",
                normalized_user_id,
                normalized_session_id,
                exc,
            )
    if normalized_session_id:
        candidate_session_ids.append(normalized_session_id)

    seen: set[str] = set()
    for candidate_session_id in candidate_session_ids:
        if not candidate_session_id or candidate_session_id in seen:
            continue
        seen.add(candidate_session_id)
        try:
            persisted = session_manager.get_session_context_usage_snapshot(
                candidate_session_id,
                normalized_user_id or None,
            )
        except Exception as exc:
            logger.debug(
                "Failed to read context snapshot for session %s: %s",
                candidate_session_id,
                exc,
            )
            continue
        if not isinstance(persisted, dict):
            continue
        snapshot.update(persisted)
        snapshot["conversation_session_id"] = normalized_session_id
        snapshot["user_id"] = normalized_user_id
        snapshot.setdefault("provider", provider)
        snapshot.setdefault("model", model_name)
        snapshot.setdefault("context_window", context_window)
        snapshot.setdefault("context_window_source", context_window_source)
        snapshot["available"] = bool(
            snapshot.get("available")
            or snapshot.get("prompt_tokens") is not None
            or snapshot.get("usage_ratio") is not None
        )
        snapshot["ai_agent_session_id"] = candidate_session_id
        return snapshot

    return snapshot

def _format_usage_ratio(ratio: Any) -> str:
    try:
        if ratio is None:
            return "unknown"
        return f"{float(ratio) * 100.0:.1f}%"
    except Exception:
        return "unknown"

def _build_context_health_recommendation(snapshot: Dict[str, Any]) -> str:
    usage_ratio = snapshot.get("usage_ratio")
    alert_level = str(snapshot.get("alert_level") or "normal").strip().lower()
    if usage_ratio is None:
        return "No token telemetry yet. Send one normal message, then run /context again."
    if alert_level == "critical":
        return "Start a fresh conversation with /new now. Compaction is only a fallback on local hardware."
    if alert_level == "warning":
        return "Consider starting a fresh conversation soon with /new."
    return "Conversation health is normal."

def _build_context_health_response_text(
    snapshot: Dict[str, Any],
    *,
    compaction_policy: Optional[Dict[str, Any]] = None,
) -> str:
    prompt_tokens = snapshot.get("prompt_tokens")
    context_window = snapshot.get("context_window")
    has_snapshot_data = bool(
        snapshot.get("available")
        or prompt_tokens is not None
        or snapshot.get("usage_ratio") is not None
    )
    alert_level = (
        str(snapshot.get("alert_level") or "normal").strip().lower()
        if has_snapshot_data
        else "no-data"
    )
    lines = [
        "Context health",
        f"prompt_tokens: {_format_compact_token_count(prompt_tokens) or 'unknown'}",
        f"context_window: {_format_compact_token_count(context_window) or 'unknown'}",
        f"usage_ratio: {_format_usage_ratio(snapshot.get('usage_ratio'))}",
        f"alert_level: {alert_level}",
        f"context_window_source: {snapshot.get('context_window_source') or 'unknown'}",
    ]

    if snapshot.get("provider") or snapshot.get("model"):
        lines.append(
            f"provider/model: {snapshot.get('provider') or 'unknown'} / {snapshot.get('model') or 'unknown'}"
        )

    if compaction_policy and compaction_policy.get("enabled"):
        lines.append(
            "fallback_compaction: enabled near "
            f"{_format_compact_token_count(compaction_policy.get('token_threshold'))} prompt tokens"
        )
    else:
        lines.append("fallback_compaction: disabled")

    lines.append(f"recommendation: {_build_context_health_recommendation(snapshot)}")
    return "\n".join(lines)

async def send_message_to_openclaw(
    message: str,
    session_id: str,
    *,
    context: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    on_chunk: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Call the OpenClaw gateway using the shared attachment-aware adapter.

    The session-level conversation history is maintained by OpenClaw itself via
    a stable session key derived from ``session_id``. Attachments in ``context``
    are forwarded through `/v1/responses` when supported.
    """
    provider = _active_ai_provider()
    if provider == "openclaw_agent":
        base_url = _openclaw_agent_chat_url()
        model = os.environ.get("OPENCLAW_AGENT_MODEL", os.environ.get("OPENCLAW_MODEL", "")).strip()
        token = os.environ.get("OPENCLAW_AGENT_TOKEN", os.environ.get("OPENCLAW_TOKEN", "")).strip()
    else:  # "openclaw"
        base_url = _openclaw_chat_url()
        model = os.environ.get("OPENCLAW_MODEL", "").strip()
        token = os.environ.get("OPENCLAW_TOKEN", "").strip()
    return await call_openclaw_gateway(
        api_base=base_url,
        model=model or "default",
        message=message,
        session_id=session_id,
        token=token,
        context=context,
        metadata=metadata,
        on_chunk=on_chunk,
    )

async def send_message_to_hermes(
    message: str,
    session_id: str,
    *,
    context: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    on_chunk: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Call the Hermes Agent gateway using the shared OpenAI-compatible adapter.

    Hermes exposes an OpenAI-compatible /v1/chat/completions endpoint and
    optionally /v1/responses.  The shared call_openclaw_gateway handles both
    with transparent fallback, so attachments are forwarded when supported.
    The session header sent is x-openclaw-session-key (ignored by Hermes);
    Hermes session continuity is maintained through per-session history in
    the shared gateway module.
    """
    base_url = _hermes_chat_url()
    model = os.environ.get("HERMES_MODEL", "hermes-agent").strip() or "hermes-agent"
    token = os.environ.get("HERMES_TOKEN", "").strip()
    return await call_openclaw_gateway(
        api_base=base_url,
        model=model,
        message=message,
        session_id=session_id,
        token=token,
        context=context,
        metadata=metadata,
        on_chunk=on_chunk,
    )

def _build_structured_steering_message(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    """Translate a structured steering target into a stable natural-language command."""
    payload = metadata if isinstance(metadata, dict) else {}
    target = resolve_runtime_agent_name(payload.get("steering_target"))
    if not target:
        return None

    if target == ROOT_AGENT_NAME:
        return "go back to the root agent"

    if target == resolve_runtime_agent_name("memory_agent"):
        # The root memory callback matches "memory agent" text, while the raw
        # underscore form can miss that path and trigger an internal transfer error.
        return "go to memory agent"

    display_name = format_agent_display_name(target).strip()
    if not display_name or display_name.lower() == "root":
        return "go back to the root agent"

    normalized_label = re.sub(r"\s+", " ", display_name).strip().lower()
    if normalized_label.endswith(" agent"):
        return f"go to {normalized_label}"
    return f"go to {normalized_label} agent"

def _is_autoyou_signtoross_probe(metadata: Optional[Dict[str, Any]]) -> bool:
    payload = metadata if isinstance(metadata, dict) else {}
    return (
        str(payload.get("client") or "").strip().lower() == "mike"
        and str(payload.get("purpose") or "").strip().lower() == "signtoross_probe"
    )

def _ollama_api_base_url() -> str:
    return str(
        os.environ.get("OLLAMA_API_BASE")
        or os.environ.get("OLLAMA_BASE_URL")
        or "http://127.0.0.1:11434"
    ).strip().rstrip("/")


def _native_ollama_settings() -> Dict[str, Any]:
    """Read the Server-owned Ollama configuration without using AutoYou AI."""
    config: Dict[str, Any] = {}
    presets: Dict[str, Any] = {}
    try:
        import server

        config = server.STATE.config or {}
        presets = dict(getattr(server, "_MODEL_BEHAVIOR_MODES", {}) or {})
    except Exception:
        pass
    ollama_cfg = config.get("ollama", {}) if isinstance(config, dict) else {}
    behavior = config.get("model_behavior", {}) if isinstance(config, dict) else {}
    api_base = str(ollama_cfg.get("api_base") or _ollama_api_base_url()).strip().rstrip("/")
    model = str(ollama_cfg.get("model") or os.getenv("OLLAMA_MODEL") or "").strip()
    return {
        "api_base": api_base or _ollama_api_base_url(),
        "model": model,
        "behavior": behavior if isinstance(behavior, dict) else {},
        "presets": presets,
    }


def _native_odysseus_settings() -> Dict[str, str]:
    """Read the encrypted Server config first, then its documented env fallbacks."""
    config: Dict[str, Any] = {}
    try:
        import server

        config = server.STATE.config or {}
    except Exception:
        pass
    provider_cfg = config.get("ai_provider", {}) if isinstance(config, dict) else {}
    return {
        "api_base": str(
            provider_cfg.get("odysseus_api_base") or os.getenv("ODYSSEUS_API_BASE") or "http://127.0.0.1:7000"
        ).strip().rstrip("/"),
        "model": str(provider_cfg.get("odysseus_model") or os.getenv("ODYSSEUS_MODEL") or "").strip(),
        "token": str(provider_cfg.get("odysseus_token") or os.getenv("ODYSSEUS_API_TOKEN") or "").strip(),
    }


async def send_message_to_native_ollama(
    message: str,
    session_id: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    system_prompt: str = "You are AutoYou, a helpful AI assistant.",
) -> Dict[str, Any]:
    """Call native Ollama with the saved model behavior and no agent session."""
    settings = _native_ollama_settings()
    model = await get_ollama_model(settings["api_base"], settings["model"])
    behavior = settings["behavior"]
    capability = await inspect_ollama_capabilities(settings["api_base"], model)
    thinking_enabled = resolve_ollama_thinking_enabled(behavior.get("show_thinking"))
    thinking_level = resolve_ollama_thinking_level(behavior.get("thinking_level"))
    think = (
        resolve_ollama_think_option(
            model,
            thinking_enabled,
            level=thinking_level,
            capabilities=capability,
        )
        if capability.get("available")
        else thinking_enabled
    )
    result = await call_ollama_with_history(
        store=_NATIVE_OLLAMA_HISTORY,
        ollama_api=settings["api_base"],
        model=model,
        message=message,
        session_id=session_id,
        system_prompt=system_prompt,
        options=resolve_ollama_gateway_options(behavior, settings["presets"], model),
        think=think,
        turn_id=str((metadata or {}).get("client_prompt_id") or ""),
    )
    result["thinking_capability"] = capability
    result["think"] = think
    return result


async def send_message_to_odysseus_gateway(
    message: str,
    session_id: str,
) -> Dict[str, str]:
    """Call Odysseus's own authenticated companion/session API directly."""
    settings = _native_odysseus_settings()
    return {
        "response": await call_odysseus(
            api_base=settings["api_base"],
            model=settings["model"],
            message=message,
            session_id=session_id,
            token=settings["token"],
        ),
        "api_base": settings["api_base"],
        "model": settings["model"],
    }


def _sanitize_direct_llm_text(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    payload = {"message": {"parts": [{"text": text}]}}
    cleaned = _extract_visible_agent_response_text(_sanitize_agent_event_payload(payload))
    return re.sub(r"\n{3,}", "\n\n", cleaned or "").strip()

async def _send_autoyou_signtoross_legal_advice_via_ollama(
    message: str,
    metadata: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Answer AutoYou powered OpenSign + MikeOSS legal-advice probe through local Ollama.

    This path is intentionally narrow: it only backs Mike's live integration
    gate and avoids the full ADK session/tool stack when that stack returns an
    empty root response for a simple legal drafting probe.
    """
    model_name = _normalize_ollama_model_name(
        str((metadata or {}).get("ollama_model") or os.environ.get("OLLAMA_MODEL") or "ministral-3:8b")
    )
    if not model_name:
        return None

    prompt = str(message or "").strip()
    if not prompt:
        return None

    system_prompt = (
        "You are AutoYou SignToROSS helping Mike verify a local legal workflow. "
        "Answer with one concise legal drafting note. "
        "Use the configured local Ollama model only. "
        "Mention OpenSign as the electronic signature workflow. "
        "Do not mention cloud providers, internal capabilities, DocuSign, or Notarize."
    )
    payload = {
        "model": model_name,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "options": {
            "temperature": 0.1,
            "top_p": 0.85,
            "num_predict": 160,
        },
    }

    url = f"{_ollama_api_base_url()}/api/chat"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
            async with session.post(url, json=payload) as response:
                response_text = await response.text()
                if response.status != 200:
                    logger.warning(
                        "AutoYou powered OpenSign + MikeOSS legal advice Ollama probe failed status=%s body=%s",
                        response.status,
                        response_text[:300],
                    )
                    return None
                try:
                    data = json.loads(response_text) if response_text else {}
                except Exception:
                    logger.warning("AutoYou powered OpenSign + MikeOSS legal advice Ollama probe returned non-JSON output")
                    return None
    except Exception as exc:
        logger.warning("AutoYou powered OpenSign + MikeOSS legal advice Ollama probe failed: %s", exc)
        return None

    content = ""
    message_payload = data.get("message") if isinstance(data, dict) else None
    if isinstance(message_payload, dict) and isinstance(message_payload.get("content"), str):
        content = message_payload["content"]
    elif isinstance(data, dict) and isinstance(data.get("response"), str):
        content = data["response"]

    answer = _sanitize_direct_llm_text(content)
    if not answer:
        return None

    return {
        "response": answer,
        "model": model_name,
        "ollama_api_base": _ollama_api_base_url(),
        "raw_done_reason": data.get("done_reason") if isinstance(data, dict) else None,
    }

class ChatRequest(BaseModel):
    """Request model for chat API."""
    message: str = Field(..., description="The user's message to send to the agent")
    session_id: Optional[str] = Field(None, description="Session ID for conversation continuity")
    user_id: Optional[str] = Field("AutoYou-client", description="User ID for session management")
    context: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="Previous conversation context")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata for the request")

class ChatResponse(BaseModel):
    """Response model for chat API."""
    response: str = Field(..., description="The agent's response message")
    session_id: str = Field(..., description="Session ID for this conversation")
    message_id: str = Field(..., description="Unique ID for this message exchange")
    timestamp: datetime = Field(default_factory=datetime.now, description="Timestamp of the response")
    agent_name: str = Field(..., description="Name of the agent that responded")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional response metadata")
    voice_reply_audio_path: Optional[str] = Field(default=None, exclude=True, description="Internal local path to a synthesized audio reply when the inbound turn was a recorded voice note")
    voice_reply_transcript: Optional[str] = Field(default=None, description="Transcript of the inbound voice note, when applicable")
    media_reply_attachments: List[Dict[str, Any]] = Field(default_factory=list, exclude=True, description="Internal image/video attachments to deliver as a separate media message")

class SessionInfo(BaseModel):
    """Session information model."""
    session_id: str = Field(..., description="Session ID")
    user_id: str = Field(..., description="User ID")
    created_at: datetime = Field(..., description="Session creation timestamp")
    last_activity: datetime = Field(..., description="Last activity timestamp")
    message_count: int = Field(0, description="Total messages recorded in this session")
    messages: Optional[List[Dict[str, Any]]] = Field(default=None, description="Session messages")

def _server_memory_identity_metadata() -> Dict[str, Any]:
    try:
        import server

        out: Dict[str, Any] = {}
        get_name = getattr(server, "get_configured_server_name", None)
        if callable(get_name):
            out["server_name"] = str(get_name() or "").strip()
        get_key = getattr(server, "_get_server_identity_key", None)
        if callable(get_key):
            out["server_identity_key"] = str(get_key() or "").strip()
        return {k: v for k, v in out.items() if v}
    except Exception:
        return {}

def _build_memory_metadata(
    request: ChatRequest,
    *,
    ai_agent_session_id: str,
    response_metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    request_metadata = dict(request.metadata or {})
    out: Dict[str, Any] = {
        "source_user_id": str(request.user_id or "").strip(),
        "external_session_id": str(request.session_id or "").strip(),
        "adk_session_id": str(ai_agent_session_id or "").strip(),
        **_server_memory_identity_metadata(),
    }
    for key in (
        "client",
        "source",
        "client_prompt_id",
        "conversation_action",
        "edited_message_id",
        "conversation_session_id",
        "conversation_thread_id",
        "canonical_owner_key",
        "owner_key",
        "canonical_user_id",
        "canonical_session_id",
        "destination_session_id",
        "raw_session_id",
        "pairing_mode",
        "pairing_type",
        "pair_mode",
        "client_display_name",
        "server_name",
        "server_id",
        "server_identity_key",
    ):
        value = request_metadata.get(key)
        if value not in ("", None):
            out[key] = value
    if isinstance(response_metadata, dict):
        for key in ("response_author", "agent_name", "message_id", "invocation_id"):
            value = response_metadata.get(key)
            if value not in ("", None):
                out[key] = value
    if out.get("pairing_type") and not out.get("pairing_mode"):
        out["pairing_mode"] = out["pairing_type"]
    if out.get("pair_mode") and not out.get("pairing_mode"):
        out["pairing_mode"] = out["pair_mode"]
    return {k: v for k, v in out.items() if v not in ("", None)}

# Get services through the service manager from server state
def get_session_manager():
    """Get session manager from the server state."""
    from server import STATE
    
    # Ensure service manager is initialized even if config is not available yet
    if STATE.service_manager is None:
        from service_manager import ensure_service_manager_initialized
        STATE.service_manager = ensure_service_manager_initialized()
    
    return STATE.service_manager.get_session_manager()

def get_session_metrics():
    """Get session metrics from the server state."""
    from server import STATE
    
    # Ensure service manager is initialized even if config is not available yet
    if STATE.service_manager is None:
        from service_manager import ensure_service_manager_initialized
        STATE.service_manager = ensure_service_manager_initialized()
    
    return STATE.service_manager.get_session_metrics()

async def _get_ai_session_invocation_agent(
    session_manager: Any,
    *,
    user_id: str,
    session_id: str,
) -> str:
    """Read _autoyou_root_invocation_agent from the ADK session state.

    The agent.py root agent writes this key at the start of each invocation
    (reset to root) and overwrites it when a sub-agent tool actually fires.
    Reading it after the SSE response completes gives the true responding agent.
    """
    if not session_manager or not user_id or not session_id:
        return ""
    try:
        session_data = await session_manager.get_user_session(user_id, session_id)
        if isinstance(session_data, dict):
            return str(session_data.get("_autoyou_root_invocation_agent") or "").strip()
    except Exception as exc:
        logger.debug(
            "Failed to read invocation agent state for %s/%s: %s",
            user_id, session_id, exc,
        )
    return ""

async def _get_ai_session_control_state(
    session_manager: Any,
    *,
    user_id: str,
    session_id: str,
) -> Dict[str, Any]:
    if not session_manager or not user_id or not session_id:
        return normalize_session_control_state(None)
    try:
        session_data = await session_manager.get_user_session(user_id, session_id)
        if isinstance(session_data, dict):
            return normalize_session_control_state(session_data.get(SESSION_CONTROL_STATE_KEY))
    except Exception as exc:
        logger.debug("Failed to read session control state for %s/%s: %s", user_id, session_id, exc)
    return normalize_session_control_state(None)

async def _prime_ai_session_control_state(
    session_manager: Any,
    *,
    user_id: str,
    session_id: str,
    canonical_user_id: str,
    canonical_session_id: str,
    owner_key: str,
    queue_position: int = 0,
) -> Dict[str, Any]:
    existing = await _get_ai_session_control_state(
        session_manager,
        user_id=user_id,
        session_id=session_id,
    )
    updated = prepare_session_control_for_turn(
        existing,
        canonical_user_id=canonical_user_id,
        canonical_session_id=canonical_session_id,
        owner_key=owner_key,
        queue_position=queue_position,
    )
    if session_manager:
        try:
            await session_manager.update_user_session(
                user_id,
                session_id,
                {SESSION_CONTROL_STATE_KEY: updated},
            )
        except Exception as exc:
            logger.debug("Failed to prime session control state for %s/%s: %s", user_id, session_id, exc)
    return updated

def _build_autoyou_state_delta_from_metadata(metadata: Any) -> Dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}

    state_delta: Dict[str, Any] = {}
    conversation_session_id = str(metadata.get("conversation_session_id") or "").strip()
    if conversation_session_id:
        state_delta[AUTOYOU_CONVERSATION_SESSION_STATE_KEY] = conversation_session_id
        state_delta[AUTOYOU_CONVERSATION_SESSION_USER_STATE_KEY] = conversation_session_id

    owner_key = str(metadata.get("canonical_owner_key") or metadata.get("owner_key") or "").strip()
    if owner_key:
        state_delta[AUTOYOU_OWNER_KEY_STATE_KEY] = owner_key
        state_delta[AUTOYOU_OWNER_KEY_USER_STATE_KEY] = owner_key

    reply_target = normalize_reply_target(metadata.get("reply_target"))
    if reply_target:
        state_delta[AUTOYOU_REPLY_TARGET_STATE_KEY] = reply_target
        state_delta[AUTOYOU_REPLY_TARGET_USER_STATE_KEY] = reply_target

    task_id = str(metadata.get("scheduled_task_id") or "").strip()
    if task_id:
        task_state = {"id": task_id}
        instruction = str(metadata.get("scheduled_task_instruction") or "").strip()
        if instruction:
            task_state["instruction"] = instruction
        state_delta[AUTOYOU_SCHEDULED_TASK_STATE_KEY] = task_state

    client_prompt_id = str(metadata.get("client_prompt_id") or "").strip()
    if client_prompt_id:
        state_delta["_autoyou_turn_metadata"] = {
            "client_prompt_id": client_prompt_id,
        }

    return state_delta


_DIRECT_REMINDER_TRIGGER_RE = re.compile(
    r"\b(?:remind me|create\s+(?:a\s+)?reminder|set\s+(?:a\s+)?reminder|schedule\s+(?:a\s+)?reminder|notify me|wake me up|alarm)\b",
    re.IGNORECASE,
)
_DIRECT_REMINDER_RELATIVE_RE = re.compile(
    r"\b(?:in|after)\s+(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?|days?)\b",
    re.IGNORECASE,
)
_DIRECT_REMINDER_AT_RE = re.compile(
    r"\b(?:at|by)\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|am|pm)?\b",
    re.IGNORECASE,
)


def _direct_reminder_now() -> datetime:
    return datetime.now().astimezone()


def _clean_direct_reminder_message(
    message: str,
    *,
    trigger: re.Match[str],
    time_match: re.Match[str],
) -> str:
    after_time = message[time_match.end():]
    trailing = re.search(r"\b(?:for|to|about)\s+(.+)$", after_time, re.IGNORECASE)
    if trailing:
        candidate = trailing.group(1)
    else:
        candidate = message[trigger.end():time_match.start()]
        candidate = re.sub(r"\b(?:today|tomorrow)\b", " ", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"^\W*(?:me\s+)?(?:to|for|about)\b", " ", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\s+", " ", candidate).strip(" \t\r\n,.;:-")
    if candidate:
        return candidate
    lowered = message.casefold()
    if "wake me up" in lowered or re.search(r"\balarm\b", lowered):
        return "Wake up"
    return message.strip()


def _parse_direct_reminder_request(message: str) -> Optional[Dict[str, Any]]:
    text = str(message or "").strip()
    if not text:
        return None
    trigger = _DIRECT_REMINDER_TRIGGER_RE.search(text)
    if not trigger:
        return None

    now = _direct_reminder_now()
    relative = _DIRECT_REMINDER_RELATIVE_RE.search(text)
    if relative:
        amount = float(relative.group(1))
        unit = relative.group(2).casefold()
        if unit.startswith(("hour", "hr")):
            delta = timedelta(hours=amount)
        elif unit.startswith("day"):
            delta = timedelta(days=amount)
        else:
            delta = timedelta(minutes=amount)
        target = now + delta
        reminder_message = _clean_direct_reminder_message(text, trigger=trigger, time_match=relative)
        return {
            "message": reminder_message,
            "target_time": target,
            "target_time_iso": target.isoformat(),
            "relative_minutes_from_now": delta.total_seconds() / 60.0,
        }

    absolute = _DIRECT_REMINDER_AT_RE.search(text)
    if not absolute:
        return None

    hour = int(absolute.group(1))
    minute = int(absolute.group(2) or "0")
    meridiem = re.sub(r"[^apm]", "", absolute.group(3) or "", flags=re.IGNORECASE).casefold()
    if not 0 <= minute <= 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        if meridiem.startswith("a"):
            hour = 0 if hour == 12 else hour
        else:
            hour = hour if hour == 12 else hour + 12
    elif not 0 <= hour <= 23:
        return None

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if re.search(r"\btomorrow\b", text, re.IGNORECASE):
        target += timedelta(days=1)
    elif target <= now or re.search(r"\bnext\b", text, re.IGNORECASE):
        target += timedelta(days=1)

    reminder_message = _clean_direct_reminder_message(text, trigger=trigger, time_match=absolute)
    return {
        "message": reminder_message,
        "target_time": target,
        "target_time_iso": target.isoformat(),
    }


async def _try_process_direct_reminder(
    request: ChatRequest,
    *,
    start_time: datetime,
) -> Optional[ChatResponse]:
    reminder_request = _parse_direct_reminder_request(request.message)
    if not reminder_request:
        return None

    state = _build_autoyou_state_delta_from_metadata(request.metadata)
    session_id = str(request.session_id or "")
    tool_context = SimpleNamespace(
        state=state,
        _invocation_context=SimpleNamespace(
            user_id=str(request.user_id or "AutoYou-client"),
            session_id=session_id,
            session=SimpleNamespace(id=session_id),
        ),
    )
    try:
        from autoyou_agents.notify_agent.agent import create_reminder

        result = await asyncio.to_thread(
            create_reminder,
            reminder_request["message"],
            target_time_iso=reminder_request["target_time_iso"],
            tool_context=tool_context,
        )
    except Exception as exc:
        logger.warning("Direct reminder creation failed before AI fallback: %s", exc)
        return None

    if not isinstance(result, dict) or result.get("status") != "success":
        logger.info("Direct reminder creation declined: %s", result)
        return None

    end_time = datetime.now()
    processing_time_ms = max(0, int((end_time - start_time).total_seconds() * 1000))
    target_time = reminder_request["target_time"]
    display_time = target_time.strftime("%Y-%m-%d %I:%M %p").lstrip("0")
    response_metadata = {
        "processing_time_ms": processing_time_ms,
        "agent_version": "1.0.0",
        "direct_action": "create_reminder",
        "agent_name": "notify_agent",
        "reminder": {
            "id": result.get("id"),
            "message": reminder_request["message"],
            "target_time_iso": reminder_request["target_time_iso"],
        },
    }
    logger.info(
        "Created reminder through direct notify_agent path id=%s session=%s",
        result.get("id"),
        request.session_id,
    )
    return ChatResponse(
        response=f"Reminder set for {display_time}: {reminder_request['message']}",
        message_id=str(uuid.uuid4()),
        session_id=session_id,
        agent_name=format_agent_display_name("notify_agent"),
        timestamp=end_time,
        metadata=response_metadata,
    )

def _build_ai_agent_run_state_delta(
    metadata: Any,
    session_control_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state_delta = _build_autoyou_state_delta_from_metadata(metadata)
    if isinstance(session_control_state, dict):
        state_delta[SESSION_CONTROL_STATE_KEY] = dict(session_control_state)
    return state_delta

async def _persist_autoyou_state_from_metadata(
    session_manager: Any,
    *,
    user_id: str,
    session_id: str,
    metadata: Any,
) -> None:
    state_delta = _build_autoyou_state_delta_from_metadata(metadata)
    if not state_delta or not session_manager or not user_id or not session_id:
        return
    try:
        await session_manager.update_user_session(user_id, session_id, state_delta)
    except Exception as exc:
        logger.warning("Failed to persist AutoYou ADK state metadata: %s", exc)

def _is_missing_ai_agent_session_response(payload: Any) -> bool:
    return isinstance(payload, dict) and int(payload.get("_error") or 0) == 404

async def _bind_local_ai_agent_session(
    session_manager: Any,
    *,
    user_id: str,
    external_session_id: Optional[str],
    ai_agent_session_id: str,
) -> None:
    if not session_manager or not user_id or not ai_agent_session_id:
        return

    try:
        await session_manager.create_user_session(
            user_id=user_id,
            session_id=ai_agent_session_id,
            initial_state={},
            external_session_id=external_session_id,
        )
    except Exception as exc:
        logger.warning(
            "Failed to create local DB row for AI Agent session %s (external=%s): %s",
            ai_agent_session_id,
            external_session_id,
            exc,
        )

    if not external_session_id:
        return

    try:
        session_manager.set_session_mapping(external_session_id, ai_agent_session_id, user_id)
    except Exception as exc:
        logger.warning(
            "Failed to cache AI Agent session mapping %s -> %s: %s",
            external_session_id,
            ai_agent_session_id,
            exc,
        )

async def _resolve_session_lookup(
    session_manager: Any,
    *,
    user_id: str,
    session_id: str,
) -> tuple[str, Optional[Dict[str, Any]]]:
    normalized_session_id = str(session_id or "").strip()
    if not session_manager or not user_id or not normalized_session_id:
        return normalized_session_id, None

    candidate_session_ids: List[str] = [normalized_session_id]
    try:
        mapped_session_id = session_manager.get_mapped_session_id(normalized_session_id, user_id)
    except Exception as exc:
        logger.debug(
            "Failed to resolve mapped AI Agent session for lookup %s/%s: %s",
            user_id,
            normalized_session_id,
            exc,
        )
        mapped_session_id = None
    if mapped_session_id:
        normalized_mapped_session_id = str(mapped_session_id).strip()
        if normalized_mapped_session_id and normalized_mapped_session_id not in candidate_session_ids:
            candidate_session_ids.append(normalized_mapped_session_id)

    for candidate_session_id in candidate_session_ids:
        try:
            session_data = await session_manager.get_user_session(user_id, candidate_session_id)
        except Exception as exc:
            logger.debug(
                "Failed to load AI Agent session data for lookup %s/%s: %s",
                user_id,
                candidate_session_id,
                exc,
            )
            session_data = None
        if session_data:
            return candidate_session_id, session_data

    return normalized_session_id, None

# Service references will be retrieved dynamically to avoid circular imports

# Global NotesTool for media persistence (used by downstream ingestion tools)

def _extract_attachments_from_context(context: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return _shared_extract_attachments_from_context(context)

_ATTACHMENT_UPLOAD_VERB_RE = re.compile(
    r"\b(?:upload|save|add|append|post|share|send|store|ingest|import|attach|publish)\b",
    re.IGNORECASE,
)
_ATTACHMENT_PAGE_TARGET_RE = re.compile(
    r"\b(?:page(?:\s+feed|\s+tool)?|for\s+you|feed)\b",
    re.IGNORECASE,
)
_ATTACHMENT_NOTES_TARGET_RE = re.compile(
    r"\b(?:notes?|notebook)\b",
    re.IGNORECASE,
)

def _explicit_attachment_ingest_target(
    message: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    payload = metadata if isinstance(metadata, dict) else {}
    raw_text = str(payload.get("steering_command_raw") or "").strip()
    text = str(message or "").strip()
    search_text = " ".join(part for part in (raw_text, text) if part).strip()
    if not search_text or not _ATTACHMENT_UPLOAD_VERB_RE.search(search_text):
        return None

    if _ATTACHMENT_PAGE_TARGET_RE.search(search_text):
        return "page"
    if _ATTACHMENT_NOTES_TARGET_RE.search(search_text):
        return "notes"

    steering_target = resolve_runtime_agent_name(payload.get("steering_target"))
    if steering_target == resolve_runtime_agent_name("page_agent"):
        return "page"
    if steering_target == resolve_runtime_agent_name("notes_agent"):
        return "notes"
    return None

def _run_explicit_attachment_ingest(
    attachments: List[Dict[str, Any]],
    *,
    target: str,
    message: str,
    source: Optional[str],
    user_id: Optional[str],
    session_id: Optional[str],
) -> Dict[str, Any]:
    from autoyou_agents.agent import process_media_content

    intent_hint = f"save to {target}. {message or ''}".strip()
    result = process_media_content(
        attachments=attachments,
        intent_hint=intent_hint,
        source=source,
        user_id=user_id,
        session_id=session_id,
    )
    return result if isinstance(result, dict) else {"status": "error", "message": "media ingest returned no result"}

def _summarize_explicit_attachment_ingest(
    result: Optional[Dict[str, Any]],
    *,
    requested_target: str,
) -> tuple[str, Dict[str, Any], str]:
    payload = result if isinstance(result, dict) else {}
    routed_to = str(payload.get("routed_to") or requested_target or "page").strip().lower()
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    saved_notes = payload.get("saved_notes") if isinstance(payload.get("saved_notes"), list) else []
    created_notes = payload.get("created_notes") if isinstance(payload.get("created_notes"), list) else []
    appended_notes = payload.get("appended_notes") if isinstance(payload.get("appended_notes"), list) else []
    saved_page = payload.get("saved_page") if isinstance(payload.get("saved_page"), list) else []
    errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
    skipped = payload.get("skipped") if isinstance(payload.get("skipped"), list) else []

    # Ingest tools often return one row for the saved media plus another row for
    # the note/page record that references it. Count user-uploaded artifacts, not
    # backend rows, or a single voice note becomes "2 attachments".
    success_count = max(len(items), len(saved_notes), len(created_notes), len(appended_notes), len(saved_page))
    destination_label = {
        "page": "AutoYou page feed",
        "notes": "notes",
        "internet": "internet search results",
    }.get(routed_to, routed_to or requested_target)
    item_view_urls = [
        str(item.get("view_url") or item.get("open_url") or "").strip()
        for item in items
        if isinstance(item, dict)
    ]
    item_view_urls = [
        url for url in item_view_urls
        if url.startswith("http://") or url.startswith("https://")
    ]
    tool_message = str(payload.get("message") or "").strip()

    if success_count > 0:
        if tool_message:
            response = tool_message
        elif routed_to == "notes" and appended_notes:
            note_ids = sorted(
                {
                    str(item.get("note_id"))
                    for item in appended_notes
                    if isinstance(item, dict) and item.get("note_id") is not None
                }
            )
            attachment_noun = "attachment" if success_count == 1 else "attachments"
            response = f"Appended {success_count} {attachment_noun} to note {note_ids[0]}." if len(note_ids) == 1 else f"Appended {success_count} {attachment_noun} to notes."
        elif routed_to == "notes" and created_notes:
            note_count = len(created_notes)
            note_noun = "note" if note_count == 1 else "notes"
            attachment_noun = "attachment" if success_count == 1 else "attachments"
            response = f"Saved {note_count} {note_noun} with {success_count} {attachment_noun} to notes."
        else:
            noun = "attachment" if success_count == 1 else "attachments"
            response = f"Uploaded {success_count} {noun} to {destination_label}."
            if errors:
                response += f" {len(errors)} item(s) still failed."
            elif skipped:
                response += f" {len(skipped)} item(s) were skipped."
    else:
        failure_detail = str(errors[0] if errors else payload.get("message") or "The uploaded attachment could not be saved.").strip()
        response = f"I couldn't save the uploaded attachment. {failure_detail}"

    routed_agent_name = {
        "page": resolve_runtime_agent_name("page_agent") or ROOT_AGENT_NAME,
        "notes": resolve_runtime_agent_name("notes_agent") or ROOT_AGENT_NAME,
        "internet": resolve_runtime_agent_name("internet_agent") or ROOT_AGENT_NAME,
    }.get(routed_to, ROOT_AGENT_NAME)

    metadata = {
        "handled": True,
        "requested_target": requested_target,
        "routed_to": routed_to,
        "success_count": success_count,
        "items_count": len(items),
        "saved_notes_count": len(saved_notes),
        "created_notes_count": len(created_notes),
        "appended_notes_count": len(appended_notes),
        "saved_page_count": len(saved_page),
        "skipped_count": len(skipped),
        "errors": [str(err) for err in errors[:5]],
        "view_urls": item_view_urls[:5],
        "result": _safe_jsonable(payload),
    }
    return response, metadata, routed_agent_name

def _get_temp_media_dir() -> str:
    """Return the temp directory for pre-saved media, ensuring it exists."""
    base = os.path.join(tempfile.gettempdir(), "autoyou_media")
    Path(base).mkdir(parents=True, exist_ok=True)
    return base

def _safe_path_component(value: Optional[str], fallback: str) -> str:
    """Return a filesystem-safe single path component."""
    raw = str(value or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    if not cleaned:
        cleaned = fallback
    return cleaned[:80]

def _safe_filename(filename: Optional[str], mimetype_hint: Optional[str]) -> str:
    return _shared_safe_filename(filename, mimetype_hint)

def _write_bytes_to_temp(data_bytes: bytes, *, filename: str, source: Optional[str], user_id: str, session_id: str) -> Dict[str, Any]:
    return _shared_write_bytes_to_temp(
        data_bytes,
        filename=filename,
        source=source,
        user_id=user_id,
        session_id=session_id,
    )

def _attachment_to_path(
    att: Dict[str, Any],
    *,
    source: Optional[str],
    user_id: str,
    session_id: str,
) -> Dict[str, Any]:
    return _shared_attachment_to_path(
        att,
        source=source,
        user_id=user_id,
        session_id=session_id,
    )

async def create_ai_agent_session(user_id: str, ai_agent_url: str = None) -> Optional[str]:
    """
    Create a new session on the AI Agent Server.
    
    Args:
        user_id: The user identifier
        
    Returns:
        The AI Agent Server session ID if successful, None otherwise
    """
    try:
        # Use provided URL or fall back to environment-based URL
        base_url = ai_agent_url or get_ai_agent_server_url()
        async with aiohttp.ClientSession() as session:
            url = f"{base_url}/apps/autoyou_agents/users/{user_id}/sessions"
            async with session.post(url) as response:
                text_body = None
                try:
                    text_body = await response.text()
                except Exception:
                    pass

                if response.status == 200:
                    try:
                        data = json.loads(text_body) if text_body else await response.json()
                    except Exception:
                        data = {}

                    # Be robust to different response shapes
                    session_id = (
                        (data or {}).get("id")
                        or (data or {}).get("session_id")
                        or (data or {}).get("sessionId")
                        or ((data or {}).get("session") or {}).get("id")
                    )

                    if session_id:
                        logger.info(
                            f"Created AI Agent Server session: {session_id} for user: {user_id} via {url}"
                        )
                        return str(session_id)
                    else:
                        logger.error(
                            f"AI Agent session creation returned 200 but no session id. Body={text_body}"
                        )
                        return None
                else:
                    logger.error(
                        f"Failed to create AI Agent Server session: {response.status} url={url} body={text_body}"
                    )
                    return None
    except Exception as e:
        logger.error(f"Error creating AI Agent Server session: {e}")
        return None

async def get_ai_agent_session(user_id: str, session_id: str, ai_agent_url: str = None) -> Optional[Dict[str, Any]]:
    """
    Get session data from the AI Agent Server.
    
    Args:
        user_id: The user identifier
        session_id: The AI Agent Server session ID
        
    Returns:
        Session data if successful, None otherwise
    """
    try:
        # Use provided URL or fall back to environment-based URL
        base_url = ai_agent_url or get_ai_agent_server_url()
        async with aiohttp.ClientSession() as session:
            # Prefer plural path, then fall back to singular if needed
            urls = [
                f"{base_url}/apps/autoyou_agents/users/{user_id}/sessions/{session_id}",
                f"{base_url}/apps/autoyou_agents/users/{user_id}/session/{session_id}",
            ]
            for url in urls:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Retrieved AI Agent Server session: {session_id} via {url}")
                        return data
                    elif response.status == 404:
                        logger.warning(f"AI Agent Server session not found via {url}")
                        continue
                    else:
                        logger.error(f"Failed to get AI Agent Server session: {response.status} via {url}")
                        # Non-404 errors: abort early
                        return None
            return None
    except Exception as e:
        logger.error(f"Error getting AI Agent Server session: {e}")
        return None

async def send_message_to_ai_agent(
    user_id: str,
    session_id: str,
    message: str,
    ai_agent_url: str = None,
    *,
    context: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    state_delta: Optional[Dict[str, Any]] = None,
    on_chunk: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Send a message to the AI Agent Server and get the response.
    
    Args:
        user_id: The user identifier
        session_id: The AI Agent Server session ID
        message: The message text to send
        
    Returns:
        The agent response if successful, None otherwise
    """
    try:
        # Use provided URL or fall back to environment-based URL
        base_url = ai_agent_url or get_ai_agent_server_url()
        async with aiohttp.ClientSession(timeout=_get_run_sse_timeout()) as session:
            url = f"{base_url}/run_sse"
            stream_sanitize_state = {"inside_think_block": False}
            headers = {
                "Content-Type": "application/json",
                # Some servers are strict about SSE Accept header; omit charset
                "Accept": "text/event-stream",
            }
            # Detect voice-transcribed messages and prefix them so the LLM can
            # apply conversational response rules.  The metadata flag is set by
            # the WebRTC voice pipeline in server.py but never appears in the
            # message text the model reads - we bridge that gap here.
            _meta = metadata or {}
            _is_voice = (
                str(_meta.get("source", "")).lower() in ("voice_call", "voice")
                or bool(_meta.get("is_transcription"))
                or bool(_meta.get("is_voice"))
            )
            _message_text = f"[voice transcript] {message}" if _is_voice else message

            # Build message parts, appending a compact attachments summary if available
            parts: List[Dict[str, Any]] = [{"text": _message_text}]
            try:
                from shared.openclaw_gateway import _load_attachment_bytes
                atts = _extract_attachments_from_context(context or [])
                if atts:
                    summary_items = []
                    for a in atts:
                        name = a.get("filename") or "attachment"
                        mt = str(a.get("mimetype") or "unknown/unknown")
                        mt_lower = mt.lower()
                        # Prefer path-only references; avoid base64 in payload
                        path_info = a.get("path")
                        if path_info:
                            # Surface the full path to minimize argument reconstruction errors downstream
                            summary_items.append(f"{name} ({mt}) @ {str(path_info)}")
                        else:
                            summary_items.append(f"{name} ({mt})")

                        # Forward vision/audio bytes to ADK natively via inline_data
                        if mt_lower.startswith("image/") or mt_lower.startswith("audio/"):
                            # Apply sensible limits to prevent payload explosions: 10MB image, 25MB audio
                            max_size = 25 * 1024 * 1024 if mt_lower.startswith("audio/") else 10 * 1024 * 1024
                            try:
                                data_bytes, error = _load_attachment_bytes(a)
                                if data_bytes and len(data_bytes) <= max_size:
                                    encoded = base64.b64encode(data_bytes).decode("ascii")
                                    parts.append({
                                        "inline_data": {
                                            "mime_type": mt,
                                            "data": encoded
                                        }
                                    })
                                elif error:
                                    logger.warning(f"Failed to load inline data for {name}: {error}")
                                else:
                                    logger.warning(f"Attachment {name} exceeds {max_size} bytes limit for inline_data.")
                            except Exception as e:
                                logger.warning(f"Failed to process inline data for {name}: {e}")

                    summary_text = (
                        f"Attachments: {len(atts)} item(s): " + ", ".join(summary_items) + "."
                    )
                    parts.append({"text": summary_text})
            except Exception:
                # Non-critical; continue without summary
                pass

            payload = {
                "app_name": "autoyou_agents",
                "user_id": user_id,
                "session_id": session_id,
                "new_message": {
                    "role": "user",
                    "parts": parts
                },
                # Forward full context and metadata; attachments should be path-based already
                "context": context or [],
                "metadata": metadata or {},
            }
            if state_delta:
                payload["state_delta"] = state_delta

            async def _post_and_parse_once() -> Optional[Dict[str, Any]]:
                async with session.post(url, json=payload, headers=headers) as response:
                    # Prefer streaming parse for SSE; fall back to full-text
                    last_data: Optional[Dict[str, Any]] = None
                    last_text_data: Optional[Dict[str, Any]] = None
                    last_error_data: Optional[Dict[str, Any]] = None
                    event_index = 0
                    last_text_index: Optional[int] = None
                    last_error_index: Optional[int] = None
                    last_tool_activity_index: Optional[int] = None
                    if response.status == 200:
                        stop_event = asyncio.Event()
                        loop = asyncio.get_running_loop()
                        now_t = loop.time()
                        last_chunk_state = {
                            "last_real_chunk_at": now_t,
                            "last_visible_chunk_at": now_t,
                        }
                        progress_task = asyncio.create_task(
                            _progress_heartbeat_loop(stop_event, last_chunk_state, on_chunk)
                        )
                        try:
                            buffer = ""
                            async for chunk in response.content.iter_any():
                                try:
                                    buffer += chunk.decode("utf-8", errors="ignore")
                                except Exception:
                                    continue
                                events, buffer = _extract_sse_events(buffer)
                                for event in events:
                                    try:
                                        data_content = event.get("data") or ""
                                        if data_content.strip() and data_content != "[DONE]":
                                            event_index += 1
                                            candidate = _sanitize_agent_event_payload(
                                                json.loads(data_content),
                                                stream_state=stream_sanitize_state,
                                            )
                                            last_data = candidate
                                            if _payload_visible_agent_response_text(candidate):
                                                last_text_data = candidate
                                                last_text_index = event_index
                                            if candidate.get("errorMessage") or candidate.get("error_message"):
                                                last_error_data = candidate
                                                last_error_index = event_index
                                            elif candidate.get("error"):
                                                # ADK's generic SSE exception handler emits a bare
                                                # {"error": ...} frame (not errorMessage/error_message),
                                                # e.g. from a post-invocation failure like event
                                                # compaction. That previously vanished with no trace -
                                                # log it so it's diagnosable, without changing which
                                                # payload gets selected as the response.
                                                logger.warning(
                                                    f"SSE frame carried a bare 'error' key for session "
                                                    f"{session_id}: {candidate.get('error')!r}"
                                                )
                                            if _payload_has_tool_activity(candidate):
                                                last_tool_activity_index = event_index
                                            last_chunk_state["last_real_chunk_at"] = loop.time()
                                            # Only suppress heartbeat when user-visible content arrived.
                                            if _payload_visible_agent_response_text(candidate):
                                                last_chunk_state["last_visible_chunk_at"] = loop.time()
                                            if on_chunk:
                                                try:
                                                    result = on_chunk(candidate)
                                                    if asyncio.iscoroutine(result):
                                                        await result
                                                except Exception as cb_err:
                                                    logger.debug(f"on_chunk error: {cb_err}")
                                    except Exception as parse_error:
                                        logger.debug(f"Streaming parse error: {parse_error}")
                                        continue
                        except Exception as stream_err:
                            logger.debug(f"SSE streaming read failed, falling back to text(): {stream_err}")
                        finally:
                            stop_event.set()
                            progress_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await progress_task

                        selected_payload = _select_agent_response_payload(
                            last_payload=last_data,
                            last_text_payload=last_text_data,
                            last_error_payload=last_error_data,
                            last_text_index=last_text_index,
                            last_error_index=last_error_index,
                            last_tool_activity_index=last_tool_activity_index,
                        )
                        if selected_payload:
                            logger.info(
                                f"Received SSE response from AI Agent Server for session: {session_id} via {url}"
                            )
                            return selected_payload

                        # Fall back to aggregated body parsing
                        body_text = None
                        try:
                            body_text = await response.text()
                        except Exception:
                            pass
                        last_data = None
                        last_text_data = None
                        last_error_data = None
                        event_index = 0
                        last_text_index = None
                        last_error_index = None
                        last_tool_activity_index = None
                        events, _ = _extract_sse_events(body_text or "")
                        for event in events:
                            try:
                                data_content = event.get("data") or ""
                                if data_content.strip() and data_content != "[DONE]":
                                    event_index += 1
                                    response_data = _sanitize_agent_event_payload(
                                        json.loads(data_content),
                                        stream_state=stream_sanitize_state,
                                    )
                                    last_data = response_data
                                    if _payload_visible_agent_response_text(response_data):
                                        last_text_data = response_data
                                        last_text_index = event_index
                                    if response_data.get("errorMessage") or response_data.get("error_message"):
                                        last_error_data = response_data
                                        last_error_index = event_index
                                    elif response_data.get("error"):
                                        logger.warning(
                                            f"SSE frame carried a bare 'error' key for session "
                                            f"{session_id}: {response_data.get('error')!r}"
                                        )
                                    if _payload_has_tool_activity(response_data):
                                        last_tool_activity_index = event_index
                            except Exception as parse_error:
                                logger.debug(f"Fallback parse error: {parse_error}")
                                continue
                        selected_payload = _select_agent_response_payload(
                            last_payload=last_data,
                            last_text_payload=last_text_data,
                            last_error_payload=last_error_data,
                            last_text_index=last_text_index,
                            last_error_index=last_error_index,
                            last_tool_activity_index=last_tool_activity_index,
                        )
                        if selected_payload:
                            logger.info(
                                f"Received response from AI Agent Server for session: {session_id} via {url}"
                            )
                            return selected_payload
                        logger.warning("No valid response data found in AI Agent Server response")
                        return None
                    elif response.status == 404:
                        body_text = None
                        try:
                            body_text = await response.text()
                        except Exception:
                            pass
                        logger.warning(
                            f"POST {url} returned 404. Likely invalid/expired session. Body={body_text}"
                        )
                        return {"_error": 404, "_body": body_text}
                    else:
                        body_text = None
                        try:
                            body_text = await response.text()
                        except Exception:
                            pass
                        logger.error(
                            f"Failed to send message to AI Agent Server: {response.status} url={url} body={body_text}"
                        )
                        return None

            return await _post_and_parse_once()
    except Exception as e:
        logger.error(f"Error sending message to AI Agent Server: {e}")
        return None

class APIStatus(BaseModel):
    """API status model."""
    status: str = Field(..., description="API status")
    version: str = Field("1.0.0", description="API version")
    agent_name: str = Field(..., description="Agent name")
    active_sessions: int = Field(0, description="Number of active sessions")
    total_messages: int = Field(0, description="Total messages processed")
    uptime_seconds: int = Field(0, description="Service uptime in seconds")
    timestamp: datetime = Field(default_factory=datetime.now, description="Status timestamp")

async def process_chat_message(
    request: ChatRequest, 
    ai_agent_url: str = None,
    on_chunk: Optional[Callable[[Dict[str, Any]], Any]] = None,
    on_media_reply: Optional[Callable[[List[Dict[str, Any]]], Any]] = None,
) -> ChatResponse:
    """
    Process a chat message using the AI Agent Server and return the response.
    
    Args:
        request: The chat request containing message and metadata
        ai_agent_url: Optional URL override
        on_chunk: Optional callback for streaming tokens
        
    Returns:
        ChatResponse with the agent's reply and session information
    """
    voice_note_active = False
    voice_note_transcript = ""
    voice_note_saved_attachment: Optional[Dict[str, Any]] = None
    media_reply_attachments: List[Dict[str, Any]] = []
    media_reply_delivered = False

    def _merge_media_reply_attachments(new_items: Any, *, source: str) -> int:
        nonlocal media_reply_attachments
        try:
            extracted = extract_media_reply_attachments(new_items, source=source)
        except Exception as exc:
            logger.debug("Failed to extract media reply attachments from %s: %s", source, exc)
            return 0
        added = 0
        seen = {
            (
                str(item.get("path") or ""),
                str(item.get("url") or ""),
                str(item.get("data") or "")[:256],
                str(item.get("filename") or ""),
            )
            for item in media_reply_attachments
            if isinstance(item, dict)
        }
        for item in extracted:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("path") or ""),
                str(item.get("url") or ""),
                str(item.get("data") or "")[:256],
                str(item.get("filename") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            media_reply_attachments.append(item)
            added += 1
        return added

    async def _finalize_voice_note_response(response: ChatResponse) -> ChatResponse:
        def _cleanup_inbound_voice_note() -> None:
            inbound_path = str((voice_note_saved_attachment or {}).get("path") or "").strip()
            if not inbound_path:
                return
            try:
                from shared.voice_messaging import cleanup_paths as _cleanup_paths

                _cleanup_paths(inbound_path)
            except Exception as cleanup_exc:
                logger.debug("Could not remove inbound voice-note temp media: %s", cleanup_exc)

        if response and media_reply_attachments and not media_reply_delivered and not response.media_reply_attachments:
            response.media_reply_attachments = list(media_reply_attachments)
            metadata = dict(response.metadata or {})
            metadata["media_reply"] = public_media_reply_metadata(media_reply_attachments)
            response.metadata = metadata
        if (
            not voice_note_active
            or not response
            or not str(response.response or "").strip()
            or response.response == _DEFAULT_EMPTY_RESPONSE_TEXT
            or response.voice_reply_audio_path
        ):
            _cleanup_inbound_voice_note()
            return response
        try:
            from shared import voice_messaging as _voice_messaging

            voice_artifacts = await _voice_messaging.build_voice_reply(response.response)
            if voice_artifacts and voice_artifacts.has_audio:
                response.voice_reply_audio_path = voice_artifacts.audio_path
                response.voice_reply_transcript = voice_note_transcript
                metadata = dict(response.metadata or {})
                metadata["voice_note"] = {
                    **_voice_messaging.public_voice_note_metadata(metadata.get("voice_note")),
                    "mode": "recorded_voice_note",
                    "inbound_transcript": voice_note_transcript,
                    "reply_audio_attached": True,
                    "inbound_audio_saved": bool((voice_note_saved_attachment or {}).get("path")),
                }
                response.metadata = metadata
                logger.info("Synthesized voice-note reply audio at %s", voice_artifacts.audio_path)
            else:
                logger.info("TTS unavailable for voice-note reply; sending text fallback")
        except Exception as voice_syn_exc:
            logger.warning("Voice-note reply synthesis failed; sending text: %s", voice_syn_exc)
        _cleanup_inbound_voice_note()
        return response

    async def _try_emit_media_reply() -> None:
        nonlocal media_reply_delivered
        if media_reply_delivered or not media_reply_attachments or on_media_reply is None:
            return
        try:
            result = on_media_reply(list(media_reply_attachments))
            if inspect.isawaitable(result):
                result = await result
            media_reply_delivered = result is not False
        except Exception as media_reply_exc:
            logger.warning("Immediate media reply delivery failed; deferring to caller fallback: %s", media_reply_exc)

    try:
        steering_override = _build_structured_steering_message(request.metadata)
        if steering_override:
            original_message = str(request.message or "")
            request.message = steering_override
            try:
                request.metadata = dict(request.metadata or {})
                request.metadata.setdefault("steering_command_raw", original_message)
                request.metadata["steering_command_normalized"] = steering_override
                request.metadata["steering_target"] = (
                    resolve_runtime_agent_name(request.metadata.get("steering_target")) or "root"
                )
            except Exception:
                pass
            logger.info(
                "Normalized structured steering request target=%s original=%r normalized=%r",
                (request.metadata or {}).get("steering_target"),
                original_message,
                steering_override,
            )

        logger.info(
            "Processing chat message user=%s session=%s: %s",
            request.user_id,
            request.session_id or "<none>",
            (request.message[:120] + "…") if isinstance(request.message, str) and len(request.message) > 120 else request.message,
        )
        # Start processing timer
        start_time = datetime.now()
        # Ensure metadata dict exists to avoid unbound errors in fallbacks
        response_metadata: Dict[str, Any] = {}

        # Generate session ID if not provided
        if not request.session_id:
            logger.info(f"Processing chat message for user {request.user_id} - generating new session ID since none received")
            request.session_id = str(uuid.uuid4())

        request.metadata = dict(request.metadata or {})
        if _active_ai_provider() == "ollama" and _is_autoyou_signtoross_probe(request.metadata):
            ros_probe = await _send_autoyou_signtoross_legal_advice_via_ollama(
                request.message,
                request.metadata,
            )
            if ros_probe:
                end_time = datetime.now()
                processing_time_ms = max(0, int((end_time - start_time).total_seconds() * 1000))
                response_metadata = {
                    "provider": "ollama",
                    "purpose": "signtoross_probe",
                    "client": "mike",
                    "model": ros_probe.get("model"),
                    "ollama_api_base": ros_probe.get("ollama_api_base"),
                    "raw_done_reason": ros_probe.get("raw_done_reason"),
                    "processing_time_ms": processing_time_ms,
                    "agent_version": "1.0.0",
                }
                _log_metadata_summary(response_metadata, label="autoyou_signtoross_probe")
                return await _finalize_voice_note_response(ChatResponse(
                    response=str(ros_probe["response"]),
                    message_id=str(uuid.uuid4()),
                    session_id=str(request.session_id),
                    agent_name=ROOT_AGENT_NAME,
                    timestamp=end_time,
                    metadata=response_metadata,
                ))

        context_health_command = _match_context_health_command(request.message)
        if context_health_command:
            snapshot = _load_context_health_snapshot_for_request(request)
            compaction_policy = (
                build_context_compaction_policy(context_window=snapshot.get("context_window"))
                if _is_ollama_context_snapshot(snapshot)
                else {"enabled": False}
            )
            response_text = _build_context_health_response_text(
                snapshot,
                compaction_policy=compaction_policy if compaction_policy.get("enabled") else None,
            )
            end_time = datetime.now()
            processing_time_ms = max(0, int((end_time - start_time).total_seconds() * 1000))
            response_metadata = {
                "processing_time_ms": processing_time_ms,
                "agent_version": "1.0.0",
                "local_command": context_health_command,
                "context_usage": snapshot,
            }
            if compaction_policy.get("enabled"):
                response_metadata["context_compaction"] = compaction_policy
            return ChatResponse(
                response=response_text,
                message_id=str(uuid.uuid4()),
                session_id=str(request.session_id),
                agent_name=ROOT_AGENT_NAME,
                timestamp=end_time,
                metadata=response_metadata,
            )

        # --- Recorded voice-note pipeline ---------------------------------
        # A pure voice note (one audio attachment, no text caption) is
        # transcribed locally with the same Whisper backend the live voice call
        # uses, then answered through this same chat turn. The reply is
        # synthesized back to an audio file just before returning so the
        # originating partner can send it as a voice note. If transcription is
        # unavailable we fall through to today's behavior (the audio attachment
        # is routed to notes by process_media_content).
        source_hint = derive_context_source_hint(request.context, request.metadata)
        try:
            from shared import voice_messaging as _voice_messaging

            if _voice_messaging.faster_whisper_available():
                _is_voice_note, _voice_attachment = _voice_messaging.is_voice_note_only(
                    request.message, request.context
                )
                if _is_voice_note and _voice_attachment:
                    voice_note_saved_attachment = _shared_attachment_to_path(
                        _voice_attachment,
                        source=source_hint,
                        user_id=str(request.user_id or "AutoYou-client"),
                        session_id=str(request.session_id or "unknown"),
                    )
                    voice_note_transcript = await _voice_messaging.transcribe_attachment(
                        voice_note_saved_attachment or _voice_attachment,
                        cleanup_materialized=False,
                    )
                    if voice_note_transcript.strip():
                        voice_note_active = True
                        request.message = voice_note_transcript.strip()
                        request.context = []
                        request.metadata["voice_note"] = {
                            "mode": "recorded_voice_note",
                            "inbound_transcript": voice_note_transcript.strip(),
                            "inbound_audio_saved": bool((voice_note_saved_attachment or {}).get("path")),
                            "filename": (
                                voice_note_saved_attachment or _voice_attachment
                            ).get("filename"),
                            "mimetype": (
                                voice_note_saved_attachment or _voice_attachment
                            ).get("mimetype"),
                        }
                        logger.info(
                            "Voice note transcribed (%d chars); answering as a spoken reply",
                            len(voice_note_transcript),
                        )
        except Exception as voice_pre_exc:
            logger.warning(
                "Voice-note pre-processing failed; using default handling: %s", voice_pre_exc
            )

        try:
            forward_context, attachments, path_saved_count, path_skipped_count = rewrite_context_attachments_to_paths(
                request.context,
                source=source_hint,
                user_id=str(request.user_id or "AutoYou-client"),
                session_id=str(request.session_id or "unknown"),
            )
        except Exception as pre_err:
            logger.warning(f"Path rewrite step encountered an error: {pre_err}")
            forward_context = request.context or []
            attachments = _extract_attachments_from_context(request.context)
            path_saved_count = 0
            path_skipped_count = 0

        try:
            msg = request.message or ""
            looks_like_b64 = False
            if msg and len(msg) >= 128:
                looks_like_b64 = bool(re.fullmatch(r"[A-Za-z0-9+/=\n\r]+", msg))
            if looks_like_b64 and attachments:
                request.message = (
                    f"Media received ({len(attachments)} attachment(s)). Please ingest attachments and persist media."
                )
                logger.info(
                    "Sanitized base64-like message body into a concise media instruction (attachments=%s)",
                    len(attachments)
                )
        except Exception:
            pass

        attachments_summary = summarize_attachments(
            attachments,
            path_saved_count=path_saved_count,
            path_skipped_count=path_skipped_count,
        )
        direct_reminder_response = await _try_process_direct_reminder(request, start_time=start_time)
        if direct_reminder_response:
            return await _finalize_voice_note_response(direct_reminder_response)

        explicit_ingest_target = (
            _explicit_attachment_ingest_target(request.message, request.metadata)
            if attachments
            else None
        )
        if explicit_ingest_target:
            logger.info(
                "Processing explicit attachment ingest target=%s user=%s session=%s attachments=%s",
                explicit_ingest_target,
                request.user_id,
                request.session_id,
                len(attachments),
            )
            ingest_result = await asyncio.to_thread(
                _run_explicit_attachment_ingest,
                attachments,
                target=explicit_ingest_target,
                message=request.message,
                source=source_hint,
                user_id=request.user_id,
                session_id=request.session_id,
            )
            ingest_response_text, ingest_metadata, ingest_agent_name = _summarize_explicit_attachment_ingest(
                ingest_result,
                requested_target=explicit_ingest_target,
            )
            explicit_metadata = {
                "provider": "autoyou",
                "attachments_summary": attachments_summary,
                "media_ingest": ingest_metadata,
            }
            _log_metadata_summary(explicit_metadata, label="explicit_attachment_ingest")
            return await _finalize_voice_note_response(ChatResponse(
                response=ingest_response_text,
                message_id=str(uuid.uuid4()),
                session_id=request.session_id,
                agent_name=format_agent_display_name(ingest_agent_name),
                timestamp=datetime.now(),
                metadata=explicit_metadata,
            ))

        # ── Native gateway fast-paths ────────────────────────────────────────
        # These modes are intentionally ahead of every agent/session decision:
        # their selected provider owns the conversation and no ADK worker is
        # required, including for requests that would otherwise ask for web use.
        _provider = _active_ai_provider()
        if _provider == "ollama_gateway":
            try:
                native_result = await send_message_to_native_ollama(
                    request.message,
                    request.session_id,
                    metadata=request.metadata,
                )
                response_text = _sanitize_direct_llm_text(native_result.get("response"))
                if not response_text:
                    raise OllamaGatewayError("Ollama returned an empty response.")
                native_meta = {
                    "provider": "ollama_gateway",
                    "model": native_result.get("model"),
                    "gateway_mode": True,
                    "agent_runtime_bypassed": True,
                    "attachments_summary": attachments_summary,
                    "attachments_forwarded": 0,
                    "attachments_skipped": len(attachments),
                    "think": native_result.get("think"),
                }
                _log_metadata_summary(native_meta, label="ollama_gateway_success")
                return await _finalize_voice_note_response(ChatResponse(
                    response=response_text,
                    message_id=str(uuid.uuid4()),
                    session_id=request.session_id,
                    agent_name="Ollama",
                    timestamp=datetime.now(),
                    metadata=native_meta,
                ))
            except Exception as exc:
                logger.warning("Native Ollama gateway failed for session=%s: %s", request.session_id, exc)
                native_meta = {
                    "error": True,
                    "error_message": "Native Ollama gateway unavailable",
                    "provider": "ollama_gateway",
                    "gateway_mode": True,
                    "agent_runtime_bypassed": True,
                }
                _log_metadata_summary(native_meta, label="ollama_gateway_failed")
                return await _finalize_voice_note_response(ChatResponse(
                    response=(
                        "I couldn't get a complete response from Ollama. "
                        "Check the saved endpoint and selected model, or try a shorter request."
                    ),
                    message_id=str(uuid.uuid4()),
                    session_id=request.session_id,
                    agent_name="Ollama",
                    timestamp=datetime.now(),
                    metadata=native_meta,
                ))

        if _provider == "odysseus":
            odysseus_result = await send_message_to_odysseus_gateway(request.message, request.session_id)
            response_text = _sanitize_direct_llm_text(odysseus_result.get("response"))
            odysseus_meta = {
                "provider": "odysseus",
                "model": odysseus_result.get("model"),
                "gateway_mode": True,
                "agent_runtime_bypassed": True,
                "attachments_summary": attachments_summary,
                "attachments_forwarded": 0,
                "attachments_skipped": len(attachments),
            }
            if not response_text:
                response_text = "Odysseus did not return a usable response. Check its saved gateway settings."
                odysseus_meta["error"] = True
            _log_metadata_summary(odysseus_meta, label="odysseus_gateway_response")
            return await _finalize_voice_note_response(ChatResponse(
                response=response_text,
                message_id=str(uuid.uuid4()),
                session_id=request.session_id,
                agent_name="Odysseus",
                timestamp=datetime.now(),
                metadata=odysseus_meta,
            ))

        # ── OpenClaw / Hermes / OpenAI-compatible provider fast-path ─────────
        # Ordinary OpenClaw/Hermes chat uses the direct attachment-aware gateway
        # adapter. Requests that need live web evidence stay in the AutoYou ADK
        # graph so the Internet specialist can run its tools and guards.
        live_web_request = _request_requires_autoyou_agent_graph(request)
        if live_web_request:
            logger.info(
                "process_chat_message: keeping live web request in AutoYou agent graph provider=%s session=%s",
                _provider,
                request.session_id,
            )
        if _provider == "hermes" and not live_web_request:
            logger.info(
                "process_chat_message: routing via Hermes Agent provider session=%s user=%s",
                request.session_id,
                request.user_id,
            )
            hermes_result = await send_message_to_hermes(
                message=request.message,
                session_id=request.session_id,
                context=forward_context,
                metadata=request.metadata,
                on_chunk=on_chunk,
            )
            if hermes_result:
                media_count = _merge_media_reply_attachments(hermes_result, source="hermes")
                if media_count:
                    await _try_emit_media_reply()
                hm_meta = {
                    "provider": "hermes",
                    "finish_reason": hermes_result.get("finish_reason", "stop"),
                    "usage": hermes_result.get("usage", {}),
                    "attachments_summary": attachments_summary,
                    "hermes_endpoint": hermes_result.get("openclaw_endpoint"),
                    "attachments_forwarded": hermes_result.get("attachments_forwarded", 0),
                    "attachments_skipped": hermes_result.get("attachments_skipped", 0),
                }
                if media_reply_attachments and not media_reply_delivered:
                    hm_meta["media_reply"] = public_media_reply_metadata(media_reply_attachments)
                _log_metadata_summary(hm_meta, label="hermes_success")
                return await _finalize_voice_note_response(ChatResponse(
                    response=hermes_result["response"],
                    message_id=str(uuid.uuid4()),
                    session_id=request.session_id,
                    agent_name=hermes_result.get("agent_name", "Hermes"),
                    timestamp=datetime.now(),
                    metadata=hm_meta,
                ))
            err_meta = {"error": True, "error_message": "Hermes gateway unavailable", "provider": "hermes"}
            _log_metadata_summary(err_meta, label="hermes_failed")
            return await _finalize_voice_note_response(ChatResponse(
                response=(
                    "I'm unable to reach the Hermes Agent gateway right now. "
                    "Please ensure Hermes is running on the configured port "
                    f"({os.environ.get('HERMES_PORT', '8642')}) with `hermes gateway` and try again."
                ),
                message_id=str(uuid.uuid4()),
                session_id=request.session_id,
                agent_name="Hermes",
                timestamp=datetime.now(),
                metadata=err_meta,
            ))

        if _provider in ("openclaw", "openclaw_agent") and not live_web_request:
            logger.info(
                "process_chat_message: routing via OpenClaw provider=%s session=%s user=%s",
                _provider,
                request.session_id,
                request.user_id,
            )
            openclaw_result = await send_message_to_openclaw(
                message=request.message,
                session_id=request.session_id,
                context=forward_context,
                metadata=request.metadata,
                on_chunk=on_chunk,
            )
            if openclaw_result:
                media_count = _merge_media_reply_attachments(openclaw_result, source="openclaw")
                if media_count:
                    await _try_emit_media_reply()
                oc_meta = {
                    "provider": "openclaw",
                    "openclaw_provider": _provider,
                    "finish_reason": openclaw_result.get("finish_reason", "stop"),
                    "usage": openclaw_result.get("usage", {}),
                    "attachments_summary": attachments_summary,
                    "openclaw_endpoint": openclaw_result.get("openclaw_endpoint"),
                    "attachments_forwarded": openclaw_result.get("attachments_forwarded", 0),
                    "attachments_skipped": openclaw_result.get("attachments_skipped", 0),
                }
                if media_reply_attachments and not media_reply_delivered:
                    oc_meta["media_reply"] = public_media_reply_metadata(media_reply_attachments)
                _log_metadata_summary(oc_meta, label="openclaw_success")
                return await _finalize_voice_note_response(ChatResponse(
                    response=openclaw_result["response"],
                    message_id=str(uuid.uuid4()),
                    session_id=request.session_id,
                    agent_name=openclaw_result.get("agent_name", "OpenClaw"),
                    timestamp=datetime.now(),
                    metadata=oc_meta,
                ))
            # If OpenClaw call failed, fall through to error response
            err_meta = {"error": True, "error_message": "OpenClaw gateway unavailable", "provider": "openclaw"}
            _log_metadata_summary(err_meta, label="openclaw_failed")
            return await _finalize_voice_note_response(ChatResponse(
                response=(
                    "I'm unable to reach the OpenClaw gateway right now. "
                    "Please ensure OpenClaw is running on the configured port "
                    f"({os.environ.get('OPENCLAW_PORT', '18789')}) and try again."
                ),
                message_id=str(uuid.uuid4()),
                session_id=request.session_id,
                agent_name="OpenClaw",
                timestamp=datetime.now(),
                metadata=err_meta,
            ))
        # ── End OpenClaw / Hermes fast-path ──────────────────────────────────

        queue_position = 0
        try:
            queue_position = int(((request.metadata or {}).get("session_execution") or {}).get("queue_position") or 0)
        except Exception:
            queue_position = 0
        owner_key = str(
            (request.metadata or {}).get("canonical_owner_key")
            or (request.metadata or {}).get("owner_key")
            or ""
        ).strip()

        # Check if we have a cached AI Agent Server session ID for this external session
        session_manager = get_session_manager()
        ai_agent_session_id = session_manager.get_mapped_session_id(request.session_id, request.user_id) if session_manager else None
        
        if not ai_agent_session_id:
            # Create a new AI Agent Server session
            ai_agent_session_id = await _create_ai_agent_session_with_recovery(
                request.user_id,
                ai_agent_url,
                reason="session creation",
            )
            if not ai_agent_session_id:
                # Fallback if session creation fails
                final_metadata = {"error": True, "error_message": "Failed to create AI Agent Server session"}
                _log_metadata_summary(final_metadata, label="session_create_failed")
                return await _finalize_voice_note_response(ChatResponse(
                    response="I'm experiencing technical difficulties connecting to the AI service. Please try again in a moment.",
                    message_id=str(uuid.uuid4()),
                    session_id=request.session_id,
                    agent_name=ROOT_AGENT_NAME,
                    timestamp=datetime.now(),
                    metadata=final_metadata
                ))
            await _bind_local_ai_agent_session(
                session_manager,
                user_id=request.user_id,
                external_session_id=request.session_id,
                ai_agent_session_id=ai_agent_session_id,
            )
            logger.info(
                f"Mapped external session {request.session_id} to AI Agent Server session {ai_agent_session_id}"
            )
        else:
            # Pre-flight verify the mapped session still exists on the AI Agent Server
            # This avoids a first /run_sse 404 after agent restarts and keeps mapping fresh.
            try:
                existing = await get_ai_agent_session(request.user_id, ai_agent_session_id, ai_agent_url)
            except Exception:
                existing = None
            if not existing:
                old_sid = ai_agent_session_id
                new_sid = await _create_ai_agent_session_with_recovery(
                    request.user_id,
                    ai_agent_url,
                    reason="session verification",
                )
                if new_sid:
                    ai_agent_session_id = new_sid
                    await _bind_local_ai_agent_session(
                        session_manager,
                        user_id=request.user_id,
                        external_session_id=request.session_id,
                        ai_agent_session_id=new_sid,
                    )
                    logger.info(
                        f"Recreated AI Agent session for user {request.user_id}: {new_sid} (old={old_sid})"
                    )
                else:
                    logger.error("Failed to recreate AI Agent Server session during verification step")
                    final_metadata = {"error": True, "error_message": "Failed to recreate AI Agent Server session"}
                    _log_metadata_summary(final_metadata, label="session_recreate_failed")
                    return await _finalize_voice_note_response(ChatResponse(
                        response="I'm experiencing technical difficulties connecting to the AI service. Please try again in a moment.",
                        message_id=str(uuid.uuid4()),
                        session_id=request.session_id,
                        agent_name=ROOT_AGENT_NAME,
                        timestamp=datetime.now(),
                        metadata=final_metadata
                    ))
        is_conversation_replace = str(
            (request.metadata or {}).get("conversation_action") or ""
        ).strip().lower() in {"replace", "edit", "redo"}
        if is_conversation_replace:
            if not session_manager or not hasattr(session_manager, "replace_conversation_turn"):
                replace_result = {
                    "replaced": False,
                    "reason": "conversation_replacement_unavailable",
                }
            else:
                replace_result = await session_manager.replace_conversation_turn(
                    request.user_id,
                    ai_agent_session_id,
                    edited_message_id=str(
                        (request.metadata or {}).get("edited_message_id") or ""
                    ),
                    edited_message_text=str(
                        (request.metadata or {}).get("edited_message_text") or ""
                    ),
                )
            request.metadata["conversation_replace_result"] = dict(replace_result or {})
            if not bool(replace_result.get("replaced")):
                replace_metadata = {
                    "error": True,
                    "error_message": "The selected prompt could not be replaced in this conversation.",
                    "conversation_control": "conversation_replace_failed",
                    "conversation_action": "replace",
                    "control_result": dict(replace_result or {}),
                    "ai_agent_session_id": ai_agent_session_id,
                    **(request.metadata or {}),
                }
                return await _finalize_voice_note_response(ChatResponse(
                    response=(
                        "I could not safely redo that prompt in the current conversation. "
                        "The existing messages were kept; please try again from the live conversation."
                    ),
                    message_id=str(uuid.uuid4()),
                    session_id=request.session_id,
                    agent_name=ROOT_AGENT_NAME,
                    timestamp=datetime.now(),
                    metadata=_safe_jsonable(replace_metadata),
                ))
            request.metadata["conversation_replace_applied"] = True

        await _persist_autoyou_state_from_metadata(
            session_manager,
            user_id=request.user_id,
            session_id=ai_agent_session_id,
            metadata=request.metadata,
        )
        primed_control_state = await _prime_ai_session_control_state(
            session_manager,
            user_id=request.user_id,
            session_id=ai_agent_session_id,
            canonical_user_id=str(request.user_id or ""),
            canonical_session_id=str(request.session_id or ""),
            owner_key=owner_key,
            queue_position=queue_position,
        )

        async def _send_with_session_recovery(
            payload_context: Optional[List[Dict[str, Any]]],
        ) -> Optional[Dict[str, Any]]:
            nonlocal ai_agent_session_id, primed_control_state
            agent_response_payload = await send_message_to_ai_agent(
                request.user_id,
                ai_agent_session_id,
                request.message,
                ai_agent_url,
                context=payload_context,
                metadata=request.metadata,
                state_delta=_build_ai_agent_run_state_delta(request.metadata, primed_control_state),
                on_chunk=on_chunk,
            )
            if agent_response_payload is None and await _recover_local_ai_agent_server_if_possible(
                ai_agent_url,
                reason="run_sse connection",
            ):
                old_sid = ai_agent_session_id
                new_sid = await create_ai_agent_session(request.user_id, ai_agent_url)
                if not new_sid:
                    logger.error("Failed to create AI Agent Server session after /run_sse connection recovery")
                    return None
                ai_agent_session_id = new_sid
                await _bind_local_ai_agent_session(
                    session_manager,
                    user_id=request.user_id,
                    external_session_id=request.session_id,
                    ai_agent_session_id=new_sid,
                )
                await _persist_autoyou_state_from_metadata(
                    session_manager,
                    user_id=request.user_id,
                    session_id=new_sid,
                    metadata=request.metadata,
                )
                primed_control_state = await _prime_ai_session_control_state(
                    session_manager,
                    user_id=request.user_id,
                    session_id=new_sid,
                    canonical_user_id=str(request.user_id or ""),
                    canonical_session_id=str(request.session_id or ""),
                    owner_key=owner_key,
                    queue_position=queue_position,
                )
                logger.info(
                    "Created AI Agent session for user %s after connection recovery: %s (old=%s)",
                    request.user_id,
                    new_sid,
                    old_sid,
                )
                agent_response_payload = await send_message_to_ai_agent(
                    request.user_id,
                    ai_agent_session_id,
                    request.message,
                    ai_agent_url,
                    context=payload_context,
                    metadata=request.metadata,
                    state_delta=_build_ai_agent_run_state_delta(request.metadata, primed_control_state),
                    on_chunk=on_chunk,
                )
            if not _is_missing_ai_agent_session_response(agent_response_payload):
                return agent_response_payload

            try:
                existing_session = await get_ai_agent_session(request.user_id, ai_agent_session_id, ai_agent_url)
            except Exception:
                existing_session = None

            if existing_session:
                logger.info(
                    "Verified session exists for %s; retrying /run_sse once",
                    ai_agent_session_id,
                )
            else:
                old_sid = ai_agent_session_id
                new_sid = await _create_ai_agent_session_with_recovery(
                    request.user_id,
                    ai_agent_url,
                    reason="run_sse session recovery",
                )
                if not new_sid:
                    logger.error("Failed to recreate AI Agent Server session after /run_sse 404")
                    return None
                ai_agent_session_id = new_sid
                await _bind_local_ai_agent_session(
                    session_manager,
                    user_id=request.user_id,
                    external_session_id=request.session_id,
                    ai_agent_session_id=new_sid,
                )
                await _persist_autoyou_state_from_metadata(
                    session_manager,
                    user_id=request.user_id,
                    session_id=new_sid,
                    metadata=request.metadata,
                )
                primed_control_state = await _prime_ai_session_control_state(
                    session_manager,
                    user_id=request.user_id,
                    session_id=new_sid,
                    canonical_user_id=str(request.user_id or ""),
                    canonical_session_id=str(request.session_id or ""),
                    owner_key=owner_key,
                    queue_position=queue_position,
                )
                logger.info(
                    "Recreated AI Agent session for user %s: %s (old=%s)",
                    request.user_id,
                    new_sid,
                    old_sid,
                )

            agent_response_payload = await send_message_to_ai_agent(
                request.user_id,
                ai_agent_session_id,
                request.message,
                ai_agent_url,
                context=payload_context,
                metadata=request.metadata,
                state_delta=_build_ai_agent_run_state_delta(request.metadata, primed_control_state),
                on_chunk=on_chunk,
            )
            if _is_missing_ai_agent_session_response(agent_response_payload):
                logger.error(
                    "AI Agent Server session %s still missing after recovery retry for external session %s",
                    ai_agent_session_id,
                    request.session_id,
                )
                return None
            return agent_response_payload
        
        # Send message to AI Agent Server
        # Prefer forwarding the rewritten context with path-only attachments on first attempt
        agent_response_data = await _send_with_session_recovery(forward_context)
        if agent_response_data is None:
            # Retry once with original context in case upstream expects raw forms
            agent_response_data = await _send_with_session_recovery(request.context)

        if not agent_response_data:
            # Fallback if message sending fails
            final_metadata = {"error": True, "error_message": "Failed to send message to AI Agent Server"}
            _log_metadata_summary(final_metadata, label="agent_send_failed")
            return await _finalize_voice_note_response(ChatResponse(
                response="I apologize, but I'm having trouble processing your message right now. Please try again.",
                message_id=str(uuid.uuid4()),
                session_id=request.session_id,
                agent_name=ROOT_AGENT_NAME,
                timestamp=datetime.now(),
                metadata=final_metadata
            ))

        media_count = _merge_media_reply_attachments(agent_response_data, source="ai_agent")
        if media_count:
            logger.info(
                "Prepared %d image/video attachment(s) from AI agent response payload",
                media_count,
            )
            await _try_emit_media_reply()
        
        # Extract the final user-visible response text from the AI Agent Server response.
        agent_response = _extract_visible_agent_response_text(
            _sanitize_agent_event_payload(agent_response_data)
        )
        if _should_retry_empty_root_model_response(agent_response_data, agent_response):
            for retry_index in range(2):
                logger.info(
                    "Retrying empty root model response index=%s session=%s user=%s",
                    retry_index + 1,
                    ai_agent_session_id,
                    request.user_id,
                )
                retry_payload = await _send_with_session_recovery(forward_context)
                if not retry_payload:
                    continue
                retry_response = _extract_visible_agent_response_text(
                    _sanitize_agent_event_payload(retry_payload)
                )
                if retry_response:
                    agent_response_data = retry_payload
                    agent_response = retry_response
                    break
        
        # Generate response metadata
        end_time = datetime.now()
        processing_time_ms = max(0, int((end_time - start_time).total_seconds() * 1000))

        # Extract usage metadata if available
        usage_metadata = agent_response_data.get("usageMetadata", {})
        response_author = resolve_runtime_agent_name(agent_response_data.get("author", "") or "")
        message_id = agent_response_data.get("id", str(uuid.uuid4()))
        invocation_id = agent_response_data.get("invocationId", "")
        upstream_error_message = (
            agent_response_data.get("errorMessage")
            or agent_response_data.get("error_message")
            or ""
        )
        upstream_error_code = (
            agent_response_data.get("errorCode")
            or agent_response_data.get("error_code")
            or ""
        )

        control_state_after = await _get_ai_session_control_state(
            session_manager,
            user_id=request.user_id,
            session_id=ai_agent_session_id,
        )
        session_execution_metadata = build_session_execution_metadata(control_state_after or primed_control_state)
        if not invocation_id:
            invocation_id = str(session_execution_metadata.get("last_invocation_id") or "")

        # Prefer the per-invocation agent recorded by _root_after_tool_callback in
        # agent.py over the ADK event author (always root) when a non-root sub-agent
        # actually handled this turn.
        invocation_agent_raw = await _get_ai_session_invocation_agent(
            session_manager,
            user_id=request.user_id,
            session_id=ai_agent_session_id,
        )
        invocation_agent_canonical = resolve_runtime_agent_name(invocation_agent_raw)
        if invocation_agent_canonical and not is_root_agent_name(invocation_agent_canonical):
            resolved_agent_name = invocation_agent_canonical
        else:
            resolved_agent_name = resolve_runtime_agent_name(
                response_author
                or session_execution_metadata.get("last_agent_name")
                or ROOT_AGENT_NAME
            )
        context_usage = _build_context_usage_snapshot(
            ai_agent_session_id=ai_agent_session_id,
            user_id=str(request.user_id or ""),
            external_session_id=str(request.session_id or ""),
            usage_metadata=usage_metadata,
        )
        if (
            context_usage
            and session_manager
            and hasattr(session_manager, "upsert_session_context_usage_snapshot")
        ):
            try:
                session_manager.upsert_session_context_usage_snapshot(
                    session_id=ai_agent_session_id,
                    user_id=str(request.user_id or ""),
                    external_session_id=str(request.session_id or ""),
                    snapshot=context_usage,
                )
            except Exception as exc:
                logger.debug(
                    "Failed to persist context-usage snapshot for %s: %s",
                    ai_agent_session_id,
                    exc,
                )

        response_metadata = {
            "processing_time_ms": processing_time_ms,
            "agent_version": "1.0.0",
            "ai_agent_session_id": ai_agent_session_id,
            "usage_metadata": usage_metadata,
            "response_author": response_author or ROOT_AGENT_NAME,
            "message_id": message_id,
            "invocation_id": invocation_id,
            "attachments_summary": attachments_summary,
            "session_execution": session_execution_metadata,
            **(request.metadata or {})
        }
        response_metadata["response_author"] = response_author or ROOT_AGENT_NAME
        response_metadata["session_execution"] = session_execution_metadata
        response_metadata["agent_name"] = resolved_agent_name
        recovery_actions = build_session_recovery_actions(control_state_after or primed_control_state)
        if recovery_actions:
            response_metadata["recovery_actions"] = recovery_actions
        if context_usage:
            response_metadata["context_usage"] = context_usage
        if upstream_error_code:
            response_metadata["upstream_error_code"] = upstream_error_code
        if upstream_error_message:
            response_metadata["upstream_error_message"] = upstream_error_message
        backend_error = _classify_ai_backend_error(upstream_error_message)
        if backend_error:
            response_metadata["ai_backend_error"] = backend_error
            response_metadata["error"] = True
            response_metadata["error_message"] = upstream_error_message
        # Sanitize metadata to ensure JSON-safe content
        response_metadata = _safe_jsonable(response_metadata) if isinstance(response_metadata, dict) else {}
        try:
            logger.info(
                "ChatResponse author=%s ai_session=%s message_id=%s processing_ms=%s",
                response_author or ROOT_AGENT_NAME,
                ai_agent_session_id,
                message_id,
                processing_time_ms,
            )
        except Exception:
            pass
        
        # Update session metrics (keep existing functionality)
        session_metrics = get_session_metrics()
        if session_metrics:
            session_metrics.record_message(request.user_id, request.session_id)
        
        # Add session event for memory integration (keep existing functionality)
        try:
            if session_manager:
                # Sanitize attachments for session memory (avoid storing raw base64 data)
                try:
                    sanitized_attachments = []
                    for att in attachments:
                        sanitized_attachments.append({
                            "filename": att.get("filename"),
                            "mimetype": att.get("mimetype"),
                            "size_bytes": att.get("size_bytes"),
                        })
                except Exception:
                    sanitized_attachments = []

                write_ok = await session_manager.add_session_event(
                    user_id=request.user_id,
                    session_id=ai_agent_session_id,  # Use AI Agent session ID for memory consistency
                    event_type="chat_interaction",
                    event_data={
                        "user_message": request.message,
                        "agent_response": agent_response,
                        "timestamp": datetime.now().isoformat(),
                        "processing_time_ms": processing_time_ms,
                        "usage_metadata": usage_metadata,
                        "response_author": response_author or ROOT_AGENT_NAME,
                        "message_id": message_id,
                        "invocation_id": invocation_id,
                        "context": request.context or [],
                        "attachments": sanitized_attachments,
                        "memory_metadata": _build_memory_metadata(
                            request,
                            ai_agent_session_id=ai_agent_session_id,
                            response_metadata=response_metadata,
                        ),
                    },
                    external_session_id=request.session_id
                )
                if write_ok:
                    logger.info(
                        f"Added session event for memory integration: AI Agent session {ai_agent_session_id} (external: {request.session_id}); "
                        f"attachments path-saved={path_saved_count} skipped={path_skipped_count}"
                    )
                else:
                    logger.warning(
                        f"Session event write failed for AI Agent session {ai_agent_session_id} (external: {request.session_id})"
                    )

        except Exception as e:
            logger.warning(f"Failed to add session event for memory: {e}")
        
        # Sanitize raw JSON-blob responses that models sometimes return instead
        # of natural language (e.g. tool output leaked verbatim, or the model
        # returned {"role":"assistant","content":"..."} without unwrapping).
        if agent_response and isinstance(agent_response, str):
            stripped = agent_response.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict):
                        # Empty dict "{}" - model produced nothing useful
                        if not parsed:
                            agent_response = None  # Falls through to None handling below
                        # If it looks like a datetime tool dump, format it nicely
                        elif "iso" in parsed and "date" in parsed:
                            agent_response = f"The current date and time is {parsed.get('date', '')} {parsed.get('time', '')}."
                        # If it's a wrapped assistant response, extract content
                        elif "content" in parsed and isinstance(parsed["content"], str):
                            agent_response = parsed["content"].strip() or agent_response
                        # Some models wrap in {"response": "..."} instead
                        elif "response" in parsed and isinstance(parsed["response"], str) and len(parsed) <= 2:
                            agent_response = parsed["response"].strip() or agent_response
                        # Otherwise leave it - some tools legitimately return JSON
                except (json.JSONDecodeError, TypeError, KeyError):
                    pass  # Not JSON, leave as-is

        progress_only_response_filtered = False
        if agent_response and isinstance(agent_response, str) and is_progress_only_response(agent_response):
            progress_only_response_filtered = True
            agent_response = None
            if not isinstance(response_metadata, dict):
                response_metadata = {}
            response_metadata["incomplete_agent_response"] = True
            response_metadata["progress_only_response_filtered"] = True
            response_metadata.setdefault(
                "recovery_actions",
                build_agent_steering_actions(include_coding=True),
            )

        if backend_error and _should_replace_with_backend_error_message(agent_response):
            agent_response = str(backend_error.get("user_message") or "").strip() or agent_response

        # Check if we have a valid response from the AI backend
        if agent_response is None:
            execution_status = ((response_metadata or {}).get("session_execution") or {}).get("status")
            if execution_status in (STATUS_PAUSED, STATUS_BREAKER_OPEN):
                agent_response = build_coding_guard_message(control_state_after or primed_control_state)
            elif backend_error:
                agent_response = str(backend_error.get("user_message") or "").strip() or None
            elif progress_only_response_filtered:
                agent_response = (
                    "The agent returned only a progress update, not a completed answer. "
                    "I did not mark that as done. Use the available action to return to the main agent, "
                    "or ask the specialist to continue with a smaller step."
                )
            elif upstream_error_message:
                lowered_upstream_error = str(upstream_error_message).lower()
                if (
                    "not found in the agent tree" in lowered_upstream_error
                    or "not found" in lowered_upstream_error and "tool" in lowered_upstream_error
                ):
                    agent_response = (
                        "I hit an internal routing error while switching specialist agents. "
                        "Please try your request again."
                    )
                else:
                    agent_response = (
                        "I couldn't complete the request because the AI agent server returned an internal error."
                    )
            else:
                # Check if the AI agent server is actually running - if we got a
                # response_data dict back (even without text), the provider IS
                # available; the model just produced an empty/unusable response.
                if agent_response_data and isinstance(agent_response_data, dict):
                    agent_response = (
                        "I wasn't able to generate a useful response for that. "
                        "Please try rephrasing your request."
                    )
                else:
                    agent_response = (
                        "I'm unable to process your message right now because no AI provider is available. "
                        "Please ensure one of the following is configured: "
                        "Ollama is installed and running (default), "
                        "valid Google API keys are set for Gemini, "
                        "an OpenClaw Gateway is running locally (AI_PROVIDER=openclaw), "
                        "or a LiteLLM-compatible model endpoint is configured (AI_PROVIDER=litellm). "
                        "You can configure these settings in the Admin Dashboard under AI Provider."
                    )
            try:
                if not isinstance(response_metadata, dict):
                    response_metadata = {}
                # Update with error flags
                if isinstance(response_metadata, dict):
                    response_metadata.update({
                        "error": True,
                        "error_message": (
                            upstream_error_message
                            or (
                                "Incomplete agent response"
                                if progress_only_response_filtered
                                else ""
                            )
                            or (
                                "Coding workflow paused"
                                if execution_status in (STATUS_PAUSED, STATUS_BREAKER_OPEN)
                                else "No AI backend available"
                            )
                        ),
                    })
                else:
                    response_metadata = {
                        "error": True,
                        "error_message": upstream_error_message or "No AI backend available",
                    }
            except Exception:
                response_metadata = {
                    "error": True,
                    "error_message": upstream_error_message or (
                        "Incomplete agent response"
                        if progress_only_response_filtered
                        else "No AI backend available"
                    ),
                }
        
        # Log structured metadata summary for normal success path
        try:
            _log_metadata_summary(response_metadata if isinstance(response_metadata, dict) else {}, label="success")
        except Exception:
            pass

        return await _finalize_voice_note_response(ChatResponse(
            response=agent_response,
            message_id=str(uuid.uuid4()),
            session_id=request.session_id,
            agent_name=resolved_agent_name,
            timestamp=datetime.now(),
            metadata=response_metadata
        ))
        
    except Exception as e:
        logger.error(f"Error processing chat message: {e}")
        
        # Still try to add error event for memory integration
        try:
            if 'request' in locals() and hasattr(request, 'user_id') and hasattr(request, 'session_id'):
                # Safely get session_manager from service manager
                error_session_manager = get_session_manager()
                if error_session_manager:
                    # Try to get the AI Agent session ID for consistency
                    ai_agent_session_id_for_error = error_session_manager.get_mapped_session_id(request.session_id, request.user_id) if hasattr(request, 'session_id') else None
                    session_id_for_memory = ai_agent_session_id_for_error or request.session_id or str(uuid.uuid4())
                    
                    await error_session_manager.add_session_event(
                        user_id=request.user_id,
                        session_id=session_id_for_memory,  # Use AI Agent session ID if available
                        event_type="error",
                        event_data={
                            "error_message": str(e),
                            "timestamp": datetime.now().isoformat(),
                            "user_message": getattr(request, 'message', 'Unknown'),
                            "memory_metadata": _build_memory_metadata(
                                request,
                                ai_agent_session_id=session_id_for_memory,
                                response_metadata={
                                    "error": True,
                                    "error_message": str(e),
                                },
                            ),
                        },
                        external_session_id=request.session_id
                    )
        except Exception as mem_error:
            logger.warning(f"Failed to add error event for memory: {mem_error}")
        
        # Return a graceful error response instead of raising HTTPException
        # Log metadata summary for error response
        _log_metadata_summary({"error": True, "error_message": str(e)}, label="exception")
        execution_metadata = {}
        try:
            if 'request' in locals() and hasattr(request, 'user_id') and hasattr(request, 'session_id'):
                err_session_manager = get_session_manager()
                ai_session_id = None
                if err_session_manager and getattr(request, "session_id", None):
                    ai_session_id = err_session_manager.get_mapped_session_id(request.session_id, request.user_id)
                if err_session_manager and ai_session_id:
                    control_state = await _get_ai_session_control_state(
                        err_session_manager,
                        user_id=request.user_id,
                        session_id=ai_session_id,
                    )
                    execution_metadata = build_session_execution_metadata(control_state)
        except Exception:
            execution_metadata = {}
        _voice_chat_response = ChatResponse(
            response="I apologize, but I'm experiencing technical difficulties right now. Please try your request again in a moment.",
            message_id=str(uuid.uuid4()),
            session_id=request.session_id or str(uuid.uuid4()),
            agent_name=ROOT_AGENT_NAME,
            timestamp=datetime.now(),
            metadata={"error": True, "error_message": str(e), "session_execution": execution_metadata}
        )
        if voice_note_active and _voice_chat_response.response and _voice_chat_response.response != _DEFAULT_EMPTY_RESPONSE_TEXT:
            try:
                from shared import voice_messaging as _voice_messaging
                _voice_artifacts = await _voice_messaging.build_voice_reply(
                    _voice_chat_response.response
                )
                if _voice_artifacts and _voice_artifacts.has_audio:
                    _voice_chat_response.voice_reply_audio_path = _voice_artifacts.audio_path
                    _voice_chat_response.voice_reply_transcript = voice_note_transcript
                    logger.info(
                        "Synthesized voice-note reply audio at %s",
                        _voice_artifacts.audio_path,
                    )
                else:
                    logger.info("TTS unavailable for voice-note reply; sending text fallback")
            except Exception as _voice_syn_exc:
                logger.warning("Voice-note reply synthesis failed; sending text: %s", _voice_syn_exc)
        return await _finalize_voice_note_response(_voice_chat_response)

async def get_session_info(user_id: str, session_id: str) -> SessionInfo:
    """
    Get information about a specific session.
    
    Args:
        user_id: The user identifier
        session_id: The session identifier
        
    Returns:
        SessionInfo with session details
    """
    try:
        session_manager = get_session_manager()
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        _, session_data = await _resolve_session_lookup(
            session_manager,
            user_id=user_id,
            session_id=session_id,
        )

        if not session_data:
            raise HTTPException(status_code=404, detail="Session not found")

        events = session_data.get("events", []) if isinstance(session_data, dict) else []
        if not isinstance(events, list):
            events = []

        def _coerce_timestamp(value: Any) -> datetime:
            if isinstance(value, datetime):
                return value
            if isinstance(value, str) and value.strip():
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
                except Exception:
                    pass
            return datetime.now()

        first_event = events[0] if events and isinstance(events[0], dict) else {}
        last_event = events[-1] if events and isinstance(events[-1], dict) else {}

        created_at = _coerce_timestamp(first_event.get("timestamp"))
        last_activity = _coerce_timestamp(last_event.get("timestamp")) if events else created_at

        message_count_raw = session_data.get("message_count", 0) if isinstance(session_data, dict) else 0
        try:
            message_count = int(message_count_raw)
        except Exception:
            message_count = 0

        messages = []
        for event in events:
            if not isinstance(event, dict):
                continue
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            timestamp = event.get("timestamp")
            
            user_msg = data.get("user_message")
            agent_resp = data.get("agent_response")
            
            if user_msg:
                messages.append({
                    "role": "user",
                    "content": str(user_msg),
                    "timestamp": timestamp
                })
            if agent_resp:
                messages.append({
                    "role": "assistant",
                    "content": str(agent_resp),
                    "timestamp": timestamp
                })

        return SessionInfo(
            session_id=session_id,
            user_id=user_id,
            created_at=created_at,
            last_activity=last_activity,
            message_count=message_count,
            messages=messages
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session info: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get session info: {str(e)}")

async def get_api_status() -> APIStatus:
    """
    Get the current API status and information.
    
    Returns:
        APIStatus with current system information
    """
    try:
        # Get basic session statistics
        session_metrics = get_session_metrics()
        if not session_metrics:
            return APIStatus(
                status="error",
                agent_name="AutoYou AI Agent",
                version="1.0.0",
                timestamp=datetime.now()
            )
            
        stats = session_metrics.get_basic_stats()
        
        return APIStatus(
            status="healthy",
            agent_name="AutoYou AI Agent",
            version="1.0.0",
            active_sessions=stats.get("total_sessions", 0),
            total_messages=stats.get("total_messages", 0),
            uptime_seconds=0,  # Could be calculated if needed
            timestamp=datetime.now()
        )
        
    except Exception as e:
        logger.error(f"Error getting API status: {e}")
        return APIStatus(
            status="error",
            agent_name="AutoYou AI Agent", 
            version="1.0.0",
            active_sessions=0,
            total_messages=0,
            uptime_seconds=0,
            timestamp=datetime.now()
        )
