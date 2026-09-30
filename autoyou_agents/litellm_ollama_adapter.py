# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-U-usdt-52f73dedea5d09edfe88fdf8

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import copy
import ast
import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

__debug_provenance_u__ = "AUTOYOU-PROVENANCE-U-usdt-52f73dedea5d09edfe88fdf8"


LOGGER = logging.getLogger(__name__)
_TOOL_CALLS_PREFIX_RE = re.compile(r"^\s*\[TOOL_CALLS\]\s*")
_DEFAULT_MISSING_TOOL_RESULT_MESSAGE = (
    "Error: Missing tool result (tool execution may have been interrupted before a response was recorded)."
)
_MISSING_AGENT_REQUEST_PLACEHOLDER = "Please help with the user's most recent request."
_SCHEDULED_EXECUTION_GUIDANCE_RE = re.compile(
    r"\n+\s*Recurring task execution rules:\s*",
    re.IGNORECASE,
)
_JSON_WRAPPED_RESPONSE_RE = re.compile(
    r'^\s*\{\s*"role"\s*:\s*"assistant"\s*,\s*"content"\s*:\s*(".*?")\s*\}\s*$',
    re.DOTALL,
)
_JSON_WRAPPED_CONTENT_FIRST_RE = re.compile(
    r'^\s*\{\s*"content"\s*:\s*(".*?")\s*,\s*"role"\s*:\s*"assistant"\s*\}\s*$',
    re.DOTALL,
)
_CJK_TEXT_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]")
_REASONING_ANSWER_STOP_MARKERS = (
    "<|channel>",
    "<channel|>",
    "[thought]",
    "thought}",
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
_INLINE_REASONING_LEAK_MARKERS = (
    "\u200bo|thought|",
    "o|thought|",
    "<|channel>thought",
    "<channel|>",
    "<|tool_response",
    "|tool_response",
    "|thought|",
    "thought}",
    "思考过程：",
    "思考过程:",
    "思考過程：",
    "思考過程:",
    "outof_thought",
    "thoughtthought_",
    "---PROMPT ANALYSIS---",
    "---**[",
)
_REASONING_FILLER_PREFIX_RE = r"(?:(?:okay|alright|sure|well|hmm|so|right)[,.]?\s+)?"
_REASONING_PREAMBLE_RE = re.compile(
    r"(?is)^\s*" + _REASONING_FILLER_PREFIX_RE + r"(?:"
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
    r"(?is)^\s*" + _REASONING_FILLER_PREFIX_RE + r"(?:"
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
    r"|i\s+(?:need|should|will)\s+(?:to\s+)?(?:answer|respond|reply|use|call|check|route|provide)\b"
    r"|role\s+and\s+instructions?"
    r"|internal\s+reasoning"
    r")"
)
# finish_reason values (case-insensitive) that mean generation was cut off by
# a token budget rather than reaching a natural stop - a hybrid-reasoning
# local model (e.g. qwen3) can exhaust its whole budget without ever leaving
# its own internal reasoning narrative, so this is a structural signal (not
# another reasoning-phrase guess) used to catch content that would otherwise
# be delivered as a finished reply.
_TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens"})
_COMPLETE_SENTENCE_END_RE = re.compile(r'["\')\]]?[.!?]["\')\]]?\s*$')
_DEFAULT_TRUNCATED_RESPONSE_MESSAGE = (
    "[Response was cut off before it finished - try asking again, or ask a shorter question.]"
)
_TRUNCATED_THINKING_LABEL = (
    "[Incomplete - model was still reasoning when it ran out of room]\n\n"
)
_TRUNCATED_THINKING_EXCERPT_MAX_CHARS = 500
# A local model that never emits a stop token can answer correctly in its first
# sentence and then run on until the token budget ends the turn mid-word
# (observed with ministral-3:8b: "hi" produced a clean greeting followed by ~1k
# tokens of the system prompt read back). Those leading complete sentences are a
# real answer and are worth keeping instead of discarding the whole turn. The
# salvage is bounded to a few sentences because the run-on tail is often that
# regurgitation, which is itself well-punctuated and would otherwise qualify.
_SALVAGE_SENTENCE_END_RE = re.compile(r'["\')\]]?[.!?]["\')\]]?(?=\s|$)')
_SALVAGE_MAX_SENTENCES = 3
_SALVAGE_MAX_CHARS = 400
_SALVAGE_MIN_CHARS = 15
# Salvage-local reasoning markers. Kept separate from the shared reasoning
# patterns above because those are consulted by other paths, and widening them
# would change behaviour well beyond truncation salvage. "let me recall ..." in
# particular survives the upstream reasoning strippers, so by the time salvage
# runs it can be the only narrative left in the candidate.
_SALVAGE_REASONING_MARKER_RE = re.compile(
    r"(?is)\b(?:"
    r"let\s+me\s+(?:recall|think|check|see|look|start|begin|first|figure)"
    r"|let'?s\s+(?:think|start|begin|see|recall)"
    r"|first,?\s+i\s+(?:need|should|will|have)"
    r"|i\s+need\s+to\b"
    r"|i\s+should\b"
    r"|the\s+user\s+(?:is\s+asking|asked|wants)"
    r")"
)
# A weak local model can collapse when the prompt advertises a large tool
# payload and start echoing the tool-schema JSON back as its visible answer
# (observed with ministral-3:14b against ~33 advertised agent tools: it emits
# schema vocabulary such as `"description":`/`"required":` interleaved with
# prose, and never emits a real tool call). finish_reason is a normal "stop",
# so the truncation path never sees it, and the text starts with plausible
# words, so the reasoning-leak prefixes never match either.
#
# These are *structural* signals - schema-key echo, quote/colon density,
# unbalanced quoting, and degenerate character runs - rather than another
# reasoning-phrase guess, so they do not depend on predicting the exact
# wording a given model collapses into.
_TOOL_SCHEMA_ECHO_KEY_RE = re.compile(
    r'"\s*(?:description|parameters|properties|required|type|name|function|functions|arguments|enum|items)\s*"\s*:',
    re.IGNORECASE,
)
_QUOTED_KEY_VALUE_RE = re.compile(r'"\s*:\s*"')
_DEGENERATE_CHAR_RUN_RE = re.compile(r"(.)\1{23,}")
_DEGENERATE_TOKEN_RUN_RE = re.compile(r"(?:<unused\d+>|<\|[a-z0-9_]+\|>){2,}", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_TOOL_SCHEMA_ECHO_MIN_CHARS = 40
_TOOL_SCHEMA_ECHO_MIN_DISTINCT_KEYS = 3
_TOOL_SCHEMA_ECHO_MIN_KEY_VALUE_PAIRS = 4
_DEFAULT_DEGENERATE_RESPONSE_MESSAGE = (
    "[The local model returned an unusable response - it echoed its own tool definitions instead of "
    "answering. This model may not handle this many tools; pick a different model in the model library.]"
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
_SINGLE_ENTRY_JSON_MACHINE_KEYS = {
    "content",
    "data",
    "error",
    "message",
    "output",
    "response",
    "result",
    "role",
    "status",
    "text",
}
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
    "agent",
    "agent_name",
    "analysis",
    "arguments",
    "args",
    "input",
    "name",
    "observation",
    "plan",
    "reasoning",
    "scratchpad",
    "target_agent",
    "thought",
    "tool",
    "tool_args",
    "tool_input",
    "tool_name",
}
_INTERNAL_REASONING_REQUIRED_JSON_KEYS = {"analysis", "reasoning", "thought"}

def _ollama_debug_enabled() -> bool:
    return str(os.getenv("AUTOYOU_OLLAMA_DEBUG_RESPONSE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

def _safe_set_function_attr(function: Any, attr: str, value: Any) -> bool:
    """Set an attribute on a LiteLLM function/tool-call object robustly.

    LiteLLM response objects are pydantic models which may resist plain
    ``setattr`` depending on the pydantic version and frozen-model settings.
    This helper tries multiple approaches and verifies the write took effect.
    """
    if isinstance(function, dict):
        function[attr] = value
        return function.get(attr) == value
    # Attempt 1: plain setattr (works on pydantic v1 & most v2 models)
    try:
        setattr(function, attr, value)
        if getattr(function, attr, None) == value:
            return True
    except Exception:
        pass
    # Attempt 2: direct __dict__ write (bypasses __setattr__ overrides)
    try:
        function.__dict__[attr] = value
        if getattr(function, attr, None) == value:
            return True
    except Exception:
        pass
    # Attempt 3: object.__setattr__ (bypasses pydantic v2 frozen guard)
    try:
        object.__setattr__(function, attr, value)
        if getattr(function, attr, None) == value:
            return True
    except Exception:
        pass
    # Attempt 4: pydantic v2 model_fields_set manipulation
    try:
        if hasattr(function, "__pydantic_fields_set__"):
            object.__setattr__(function, attr, value)
            function.__pydantic_fields_set__.add(attr)
            return getattr(function, attr, None) == value
    except Exception:
        pass
    LOGGER.debug("_safe_set_function_attr failed for attr=%s on %s", attr, type(function).__name__)
    return False

def _get_obj_field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    obj_get = getattr(obj, "get", None)
    if callable(obj_get):
        try:
            return obj_get(key, default)
        except TypeError:
            try:
                return obj_get(key)
            except Exception:
                pass
        except Exception:
            pass
    return getattr(obj, key, default)

def is_ollama_chat_model(model: Any) -> bool:
    if not isinstance(model, str):
        return False
    model_name = model.strip()
    if not model_name:
        return False
    if model_name.startswith(("ollama_chat/", "ollama/")):
        return True
    configured = str(os.getenv("OLLAMA_MODEL") or "").strip()
    if not configured:
        return False
    configured_without_provider = re.sub(r"^(?:ollama_chat|ollama)/", "", configured)
    model_without_provider = re.sub(r"^(?:ollama_chat|ollama)/", "", model_name)
    return bool(configured_without_provider and model_without_provider == configured_without_provider)

def _normalize_tool_name(name: Any) -> Any:
    if not isinstance(name, str):
        return name
    cleaned = _TOOL_CALLS_PREFIX_RE.sub("", name).strip()
    return cleaned or name.strip()

def _unwrap_json_wrapped_content(text: Any) -> Any:
    """Unwrap content from models that emit ``{"role":"assistant","content":"..."}``."""
    if not isinstance(text, str):
        return text
    for pattern in (_JSON_WRAPPED_RESPONSE_RE, _JSON_WRAPPED_CONTENT_FIRST_RE):
        match = pattern.match(text)
        if match:
            try:
                inner = json.loads(match.group(1))
                if isinstance(inner, str) and inner.strip():
                    LOGGER.debug("Unwrapped JSON-wrapped assistant content")
                    return inner.strip()
            except Exception:
                pass
    # Full JSON parse - handles multi-line content, escaped quotes, extra keys
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                role = str(parsed.get("role") or "").strip().lower()
                content = parsed.get("content")
                if role == "assistant" and isinstance(content, str) and content.strip():
                    LOGGER.debug("Unwrapped JSON-wrapped assistant content (full parse)")
                    return content.strip()
                # Some models omit role or use different role values
                if not role and isinstance(content, str) and content.strip() and len(parsed) <= 2:
                    LOGGER.debug("Unwrapped JSON-wrapped content (no role key)")
                    return content.strip()
                structured_answer = _extract_structured_json_answer(
                    parsed,
                    require_internal_marker=True,
                )
                if structured_answer:
                    LOGGER.debug("Unwrapped structured JSON assistant answer")
                    return structured_answer
                single_entry_answer = _extract_single_entry_json_answer(parsed)
                if single_entry_answer:
                    LOGGER.debug("Unwrapped single-entry JSON assistant answer")
                    return single_entry_answer
        except Exception:
            pass
    malformed_answer = _extract_malformed_json_answer_text(stripped)
    if malformed_answer:
        LOGGER.debug("Recovered malformed JSON-wrapped assistant answer")
        return malformed_answer
    return text

def _extract_malformed_json_answer_text(text: str) -> Optional[str]:
    """Recover a natural-language answer emitted as a broken JSON object key.

    Gemma 4 can occasionally return visible assistant content shaped like a
    broken JSON object key instead of plain text when the surrounding prompt
    advertises tools. Only recover the first quoted string when the full object
    is not valid JSON and the quoted string looks like a real answer, not a key.
    """
    stripped = text.strip()
    if stripped.startswith('{"'):
        try:
            json.loads(stripped)
            return None
        except Exception:
            pass
    else:
        value_fragment_answer = _extract_malformed_json_value_answer_text(stripped)
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

def _extract_single_entry_json_answer(parsed: Any) -> Optional[str]:
    if not isinstance(parsed, dict) or len(parsed) != 1:
        return None
    key, value = next(iter(parsed.items()))
    key_text = str(key or "").strip()
    key_candidate = _clean_malformed_json_answer_candidate(key_text) if key_text else ""
    if key_text.lower() in _SINGLE_ENTRY_JSON_MACHINE_KEYS:
        return None
    if isinstance(value, str):
        candidate = _clean_malformed_json_answer_candidate(value)
        if _looks_like_natural_language_answer(candidate) and _looks_like_natural_language_answer(key_candidate):
            return candidate
    if _looks_like_natural_language_answer(key_candidate):
        return key_candidate
    return None

def _extract_structured_json_answer(
    parsed: Any,
    *,
    require_internal_marker: bool = False,
) -> Optional[str]:
    if not isinstance(parsed, dict):
        return None
    if require_internal_marker:
        keys = {str(key).strip().lower() for key in parsed.keys()}
        if not keys.intersection(_INTERNAL_REASONING_REQUIRED_JSON_KEYS):
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
    if _extract_structured_json_answer(parsed):
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

def _extract_malformed_json_value_answer_text(text: str) -> Optional[str]:
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

def _sanitize_assistant_content_text(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    # First unwrap JSON-wrapped responses from smaller models
    text = _unwrap_json_wrapped_content(text)
    if not isinstance(text, str):
        return text
    if _looks_like_internal_reasoning_json(text):
        return None
    if _looks_like_provider_artifact_response(text):
        return None
    text = _strip_inline_reasoning_leakage(text)
    if not text:
        return None
    if _looks_like_provider_artifact_response(text):
        return None
    if not _TOOL_CALLS_PREFIX_RE.match(text):
        return text
    return None

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

def _looks_like_malformed_content_stub(text: Any) -> bool:
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
    if _looks_like_malformed_content_stub(prefix):
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

def _strip_code_fences(text: str) -> str:
    """Drop fenced code blocks before scoring degeneration.

    A model legitimately asked to *show* a JSON schema answers inside a fence.
    Scoring only the unfenced remainder keeps that answer from being mistaken
    for a collapse.
    """
    return _CODE_FENCE_RE.sub(" ", text)


def _looks_like_empty_code_block(text: Any) -> bool:
    """True when visible content is just an unclosed or empty markdown code block."""
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    return bool(re.fullmatch(r"```[a-zA-Z]*\s*(?:```)?", stripped))

def looks_like_degenerate_tool_schema_echo(
    text: Any,
    *,
    advertised_tool_names: Optional[set[str]] = None,
) -> bool:
    """True when visible content collapsed into echoing the tool schema.

    Requires two independent structural signals (or one very strong one) so a
    normal reply that merely quotes a key or two is not suppressed.
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if len(stripped) < _TOOL_SCHEMA_ECHO_MIN_CHARS:
        return False

    # Well-formed JSON is handled by the structured-answer paths above; a
    # collapse is by definition malformed.
    try:
        json.loads(stripped)
        return False
    except Exception:
        pass

    scored = _strip_code_fences(stripped)
    if len(scored.strip()) < _TOOL_SCHEMA_ECHO_MIN_CHARS:
        return False

    # A long run of one character (e.g. "+1200000000000...") or reserved control token
    # (e.g. "<unused50><unused50>...") is generation collapse on its own.
    if _DEGENERATE_CHAR_RUN_RE.search(scored) or _DEGENERATE_TOKEN_RUN_RE.search(scored):
        return True

    signals = 0

    distinct_schema_keys = {
        match.group(0).strip().lower()
        for match in _TOOL_SCHEMA_ECHO_KEY_RE.finditer(scored)
    }
    if len(distinct_schema_keys) >= _TOOL_SCHEMA_ECHO_MIN_DISTINCT_KEYS:
        signals += 1

    if len(_QUOTED_KEY_VALUE_RE.findall(scored)) >= _TOOL_SCHEMA_ECHO_MIN_KEY_VALUE_PAIRS:
        signals += 1

    quote_count = scored.count('"')
    if quote_count >= 6 and quote_count % 2 == 1:
        signals += 1

    if advertised_tool_names:
        for tool_name in advertised_tool_names:
            if tool_name and tool_name in scored:
                signals += 1
                break

    return signals >= 2


def response_degenerated_into_tool_schema_echo(
    response: Any,
    *,
    tools: List[Dict[str, Any]] | None = None,
) -> bool:
    """True when a completed response carries degenerate content and no tool call.

    Used by the request wrappers to decide whether re-issuing the completion
    without the tool payload is worth one attempt.
    """
    advertised_tool_names = set(extract_tool_names(tools))
    degenerate = False
    try:
        for choice in _iter_response_choices(response):
            for message_field in ("message", "delta"):
                message = _get_obj_field(choice, message_field)
                if message is None:
                    continue
                if _get_obj_field(message, "tool_calls"):
                    return False
                if looks_like_degenerate_tool_schema_echo(
                    _get_obj_field(message, "content"),
                    advertised_tool_names=advertised_tool_names,
                ):
                    degenerate = True
    except Exception as exc:
        LOGGER.debug("Failed to inspect Ollama response for degeneration: %s", exc)
        return False
    return degenerate


def _extract_message_reasoning_text(message: Any) -> Optional[str]:
    candidates: list[Any] = []
    for key in ("reasoning_content", "reasoning", "thinking", "thinking_blocks"):
        candidates.append(_get_obj_field(message, key))

    for attr in ("model_extra", "additional_kwargs", "provider_specific_fields"):
        container = _get_obj_field(message, attr)
        if isinstance(container, dict):
            for key in ("reasoning_content", "reasoning", "thinking"):
                candidates.append(container.get(key))

    for candidate in candidates:
        text = _coerce_reasoning_candidate_to_text(candidate)
        if text:
            return text
    return None

def _coerce_reasoning_candidate_to_text(candidate: Any) -> Optional[str]:
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    if not isinstance(candidate, list):
        return None
    chunks: list[str] = []
    for item in candidate:
        if isinstance(item, str) and item.strip():
            chunks.append(item.strip())
        elif isinstance(item, dict):
            for key in ("thinking", "text", "content", "reasoning", "reasoning_content"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    chunks.append(value.strip())
                    break
    return "\n".join(chunks).strip() or None

def _extract_final_answer_from_reasoning(reasoning_text: Any) -> Optional[str]:
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
    cleaned = cleaned.strip(" -*_`>|")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

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

def _prefer_complete_reasoning_answer_if_content_is_clipped(
    content: Any,
    reasoning_answer: Any,
) -> Optional[str]:
    if not isinstance(content, str) or not isinstance(reasoning_answer, str):
        return None
    visible = re.sub(r"\s+", " ", content).strip()
    complete = re.sub(r"\s+", " ", reasoning_answer).strip()
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

def _normalize_tool_arguments(arguments: Any) -> Any:
    """Return strict JSON string arguments when normalization is possible.

    Ollama's Go unmarshaler is strict about function argument JSON. Models and
    middleware sometimes emit Python-style dict strings (single quotes) or
    object values. Canonicalize these into compact JSON strings.
    """
    if isinstance(arguments, dict):
        try:
            return json.dumps(arguments, ensure_ascii=False)
        except Exception:
            return arguments

    if not isinstance(arguments, str):
        return arguments

    stripped = arguments.strip()
    if not stripped:
        return "{}"

    try:
        parsed = json.loads(stripped)
        return json.dumps(parsed, ensure_ascii=False)
    except Exception:
        pass

    try:
        parsed = ast.literal_eval(stripped)
        # Tool argument payloads should be JSON-compatible containers.
        if isinstance(parsed, (dict, list, str, int, float, bool)) or parsed is None:
            return json.dumps(parsed, ensure_ascii=False)
    except Exception:
        pass

    return arguments

def _normalize_tool_calls_in_mapping(message: Dict[str, Any]) -> None:
    tool_calls = message.get("tool_calls")
    # from __debug_provenance_u__ import usdt
    if not isinstance(tool_calls, list):
        return

    for tool_call in tool_calls:
        if isinstance(tool_call, dict):
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            original_name = function.get("name")
            normalized_name = _normalize_tool_name(original_name)
            if normalized_name != original_name:
                function["name"] = normalized_name

            # Ensure arguments are valid strict JSON for Ollama's Go unmarshaler.
            # Models sometimes emit single-quoted JSON dicts like `{'query': 'val'}`
            arguments = function.get("arguments")
            normalized_arguments = _normalize_tool_arguments(arguments)
            if normalized_arguments != arguments:
                function["arguments"] = normalized_arguments
        else:
            # ADK 1.26+ passes ChatCompletionMessageToolCall Pydantic objects,
            # not plain dicts. Newer Ollama validates tool call names in history
            # against the registered tools list, so we must strip the
            # [TOOL_CALLS] prefix from object-style entries too.
            function = getattr(tool_call, "function", None)
            if function is None:
                continue
            original_name = _get_obj_field(function, "name")
            normalized_name = _normalize_tool_name(original_name)
            if normalized_name != original_name and normalized_name:
                _safe_set_function_attr(function, "name", normalized_name)
            original_arguments = _get_obj_field(function, "arguments")
            normalized_arguments = _normalize_tool_arguments(original_arguments)
            if normalized_arguments != original_arguments:
                _safe_set_function_attr(function, "arguments", normalized_arguments)

def _extract_tool_call_ids(message: Dict[str, Any]) -> List[str]:
    tool_call_ids: List[str] = []
    for tool_call in message.get("tool_calls") or []:
        if isinstance(tool_call, dict):
            tool_call_id = str(tool_call.get("id") or "").strip()
        else:
            tool_call_id = str(getattr(tool_call, "id", None) or "").strip()
        if tool_call_id:
            tool_call_ids.append(tool_call_id)
    return tool_call_ids

def _missing_tool_result_message(
    tool_call_id: str,
    *,
    content: str,
    role: str = "tool",
) -> Dict[str, Any]:
    return {
        "role": str(role or "tool"),
        "tool_call_id": str(tool_call_id),
        "content": str(content),
    }

def repair_missing_tool_results(
    messages: List[Dict[str, Any]] | None,
    *,
    missing_result_message: str = _DEFAULT_MISSING_TOOL_RESULT_MESSAGE,
    model: str = "",
) -> List[Dict[str, Any]]:
    """Heal assistant tool calls that have no matching tool response message.

    Earlier AutoYou/ADK compatibility bugs could leave persisted session
    histories with a tool call but no subsequent `role="tool"` result. ADK
    heals this on the fly but logs a warning on every future turn for the same
    stale session. Apply the same repair deterministically here so both old and
    new sessions can continue without repeated warning noise.
    """

    expected_tool_role = "tool_responses" if "gemma4" in str(model or "").lower() else "tool"
    healed_messages: List[Dict[str, Any]] = []
    pending_tool_call_ids: List[str] = []

    for raw_message in copy.deepcopy(messages or []):
        if not isinstance(raw_message, dict):
            if pending_tool_call_ids:
                healed_messages.extend(
                    _missing_tool_result_message(
                        tool_call_id,
                        content=missing_result_message,
                        role=expected_tool_role,
                    )
                    for tool_call_id in pending_tool_call_ids
                )
                pending_tool_call_ids = []
            healed_messages.append(raw_message)
            continue

        role = str(raw_message.get("role") or "").strip().lower()
        if pending_tool_call_ids and role != expected_tool_role:
            healed_messages.extend(
                _missing_tool_result_message(
                    tool_call_id,
                    content=missing_result_message,
                    role=expected_tool_role,
                )
                for tool_call_id in pending_tool_call_ids
            )
            pending_tool_call_ids = []

        if role == "assistant":
            pending_tool_call_ids = _extract_tool_call_ids(raw_message)
        elif role == expected_tool_role:
            tool_call_id = str(raw_message.get("tool_call_id") or "").strip()
            if tool_call_id in pending_tool_call_ids:
                pending_tool_call_ids.remove(tool_call_id)

        healed_messages.append(raw_message)

    if pending_tool_call_ids:
        healed_messages.extend(
            _missing_tool_result_message(
                tool_call_id,
                content=missing_result_message,
                role=expected_tool_role,
            )
            for tool_call_id in pending_tool_call_ids
        )

    return healed_messages

def prepare_tools_for_ollama(tools: List[Dict[str, Any]] | None) -> List[Dict[str, Any]] | None:
    """Return a normalized copy of tool declarations for Ollama/LiteLLM."""

    if tools is None:
        return None

    normalized_tools = copy.deepcopy(tools)
    if not isinstance(normalized_tools, list):
        return normalized_tools

    for tool in normalized_tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        original_name = function.get("name")
        normalized_name = _normalize_tool_name(original_name)
        if normalized_name != original_name:
            function["name"] = normalized_name

    return normalized_tools

def _normalize_image_part(part: Any) -> Optional[Dict[str, Any]]:
    """Normalize a content part holding an image into litellm's expected shape.

    Litellm's ``extract_images_from_message`` keys specifically off ``image_url``
    parts. Convert Google ADK-style ``inline_data`` parts to canonical
    ``image_url`` data URLs so LiteLLM extracts image bytes for Ollama's
    ``images`` array.
    """
    if not isinstance(part, dict):
        return None
    if bool(part.get("image_url")):
        return part
    inline = part.get("inline_data")
    if isinstance(inline, dict):
        mime = str(inline.get("mime_type") or "image/jpeg")
        data = str(inline.get("data") or "")
        if data:
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{data}"},
            }
    return None


def prepare_messages_for_ollama(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a normalized copy of messages for Ollama/LiteLLM.

    ADK sometimes sends multipart assistant content. Ollama expects plain text
    content, and malformed assistant text like `[TOOL_CALLS]foo` can poison the
    next request. Normalize on a deep copy so shared session state is not
    modified in place.

    Content carrying an image stays in list form. litellm's ollama_chat
    transformation builds Ollama's `images` array by scanning the content
    *list* for `image_url` parts, so flattening a vision turn to a plain string
    here silently drops the picture and leaves the model asked to describe an
    image it was never shown.
    """

    normalized_messages = repair_missing_tool_results(messages)

    for message in normalized_messages:
        role = str(message.get("role") or "")
        content = message.get("content")

        if isinstance(content, list):
            text_parts: List[str] = []
            image_parts: List[Any] = []
            for part in content:
                norm_image = _normalize_image_part(part)
                if norm_image is not None:
                    image_parts.append(norm_image)
                    continue
                if isinstance(part, dict) and "text" in part:
                    text = str(part["text"])
                    if role == "assistant":
                        text = _sanitize_assistant_content_text(text)
                    if isinstance(text, str) and text:
                        text_parts.append(text)
            joined_text = " ".join(text_parts).strip()
            if image_parts:
                # Keep visual input before text for Gemma 4's multimodal template.
                message["content"] = image_parts + (
                    [{"type": "text", "text": joined_text}] if joined_text else []
                )
            else:
                message["content"] = joined_text or None
        elif role == "assistant":
            message["content"] = _sanitize_assistant_content_text(content)

        _normalize_tool_calls_in_mapping(message)

    return normalized_messages

def normalize_ollama_response(
    response: Any,
    *,
    messages: List[Dict[str, Any]] | None = None,
    tools: List[Dict[str, Any]] | None = None,
    reasoning_fallback: Optional[str] = None,
) -> Any:
    """Normalize malformed Ollama tool-call markers in LiteLLM responses."""
    return _normalize_ollama_response_internal(
        response,
        messages=messages,
        tools=tools,
        reasoning_fallback=reasoning_fallback,
    )

async def normalize_ollama_stream_response(
    response: Any,
    *,
    messages: List[Dict[str, Any]] | None = None,
    tools: List[Dict[str, Any]] | None = None,
) -> Any:
    """Yield normalized LiteLLM stream chunks for Ollama models."""
    reasoning_buffer = ""
    async for chunk in response:
        reasoning_piece = _extract_response_reasoning_text(chunk)
        if reasoning_piece:
            reasoning_buffer = f"{reasoning_buffer}\n{reasoning_piece}".strip()
            if len(reasoning_buffer) > 12000:
                reasoning_buffer = reasoning_buffer[-12000:]
        yield normalize_ollama_response(
            chunk,
            messages=messages,
            tools=tools,
            reasoning_fallback=reasoning_buffer or None,
        )

def _extract_response_reasoning_text(response: Any) -> Optional[str]:
    chunks: list[str] = []
    for choice in _iter_response_choices(response):
        for message_field in ("message", "delta"):
            message = _get_obj_field(choice, message_field)
            if message is None:
                continue
            reasoning_text = _extract_message_reasoning_text(message)
            if reasoning_text:
                chunks.append(reasoning_text)
    return "\n".join(chunks).strip() or None

def _iter_response_choices(response: Any) -> List[Any]:
    choices = _get_obj_field(response, "choices", [])
    if isinstance(choices, list):
        return choices
    return []

def _strip_scheduled_execution_guidance(text: Any) -> str:
    """Keep scheduler instructions out of fallback tool arguments."""
    value = str(text or "").strip()
    if not value:
        return ""
    marker = _SCHEDULED_EXECUTION_GUIDANCE_RE.search(value)
    return value[:marker.start()].strip() if marker else value


def _extract_latest_user_request(messages: List[Dict[str, Any]] | None) -> str:
    for message in reversed(messages or []):
        if str(_get_obj_field(message, "role") or "").strip().lower() != "user":
            continue
        content = _get_obj_field(message, "content")
        if isinstance(content, str) and content.strip():
            return _strip_scheduled_execution_guidance(content)
        # ADK sends multipart content for media-bearing turns. Without this the
        # latest request reads as empty, and every downstream repair that needs
        # the user's text (most importantly the required `request` argument for
        # an agent tool) silently gives up.
        if isinstance(content, list):
            text_parts = [
                str(part["text"]).strip()
                for part in content
                if isinstance(part, dict)
                and isinstance(part.get("text"), str)
                and part["text"].strip()
            ]
            if text_parts:
                return _strip_scheduled_execution_guidance(" ".join(text_parts))
    return ""

def _parse_function_arguments(raw_arguments: Any) -> Dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments)
    if not isinstance(raw_arguments, str):
        return {}
    stripped = raw_arguments.strip()
    if not stripped:
        return {}
    try:
        parsed = json.loads(stripped)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}

def _rewrite_transfer_tool_call(
    function: Any,
    *,
    advertised_tool_names: set[str],
    latest_user_request: str,
) -> bool:
    function_name = _get_obj_field(function, "name")
    if function_name != "transfer_to_agent":
        return False

    raw_arguments = _get_obj_field(function, "arguments")
    parsed_arguments = _parse_function_arguments(raw_arguments)
    target_tool_name = str(
        parsed_arguments.get("agent_name")
        or parsed_arguments.get("target_agent")
        or parsed_arguments.get("name")
        or ""
    ).strip()
    if not target_tool_name or target_tool_name not in advertised_tool_names:
        return False

    request_text = str(
        latest_user_request
        or parsed_arguments.get("request")
        or parsed_arguments.get("query")
        or ""
    ).strip()
    if not request_text:
        return False

    _safe_set_function_attr(function, "name", target_tool_name)
    _safe_set_function_attr(function, "arguments", json.dumps({"request": request_text}, ensure_ascii=False))
    LOGGER.warning(
        "Rewrote unsupported transfer_to_agent tool call to explicit agent tool '%s' for LiteLLM/Ollama compatibility",
        target_tool_name,
    )
    return True

def _extract_explicit_agent_tool_names(tools: List[Dict[str, Any]] | None) -> set[str]:
    explicit_agent_tool_names: set[str] = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            continue

        parameters = function.get("parameters_json_schema") or function.get("parameters")
        if isinstance(parameters, dict):
            properties = parameters.get("properties")
            required = parameters.get("required")
            request_schema = properties.get("request") if isinstance(properties, dict) else None
            if (
                isinstance(request_schema, dict)
                and str(request_schema.get("type") or "").lower() == "string"
                and list(required or []) == ["request"]
                and set((properties or {}).keys()) == {"request"}
            ):
                explicit_agent_tool_names.add(name)
                continue

        if name == "memory_agent" or name.endswith("_agent"):
            explicit_agent_tool_names.add(name)

    return explicit_agent_tool_names

def _build_dynamic_keyword_hint_map(advertised_tool_names: set[str]) -> Dict[str, str]:
    """Build keyword→tool_name mapping dynamically from the advertised tools.

    Works for any agent - built-in, user-scaffolded via agent_builder_agent,
    or future additions - by extracting keywords from the tool name itself.
    E.g. ``autoyou_notes_agent`` → keywords ``note``, ``notes``.
         ``weather_agent`` → keyword ``weather``.
         ``coding_agent``  → keywords ``coding``, ``code``.
    """
    hint_map: Dict[str, str] = {}
    for tool_name in advertised_tool_names:
        # Skip non-agent tools (plain functions like get_current_datetime)
        if not tool_name.endswith("_agent"):
            continue
        # Strip autoyou_ prefix and _agent suffix to get the semantic core
        core = tool_name
        if core.startswith("autoyou_"):
            core = core[len("autoyou_"):]
        if core.endswith("_agent"):
            core = core[:-len("_agent")]
        if not core:
            continue
        # Each underscore-separated word becomes a keyword
        for word in core.split("_"):
            word = word.strip().lower()
            if not word or len(word) < 3:
                continue
            if word not in hint_map:
                hint_map[word] = tool_name
            # Add singular/plural variants for common patterns
            if word.endswith("s") and len(word) > 3:
                singular = word[:-1]
                if singular not in hint_map:
                    hint_map[singular] = tool_name
            elif not word.endswith("s"):
                plural = word + "s"
                if plural not in hint_map:
                    hint_map[plural] = tool_name
    # Add common synonyms that models frequently use
    _synonyms: Dict[str, List[str]] = {
        "search": ["internet", "web", "browse", "lookup", "find", "google"],
        "internet": ["search", "web", "browse", "lookup", "find", "google"],
        "web": ["search", "internet", "browse", "lookup"],
        "note": ["notes", "reminder", "reminders", "todo"],
        "notes": ["note", "reminder", "reminders", "todo"],
        "memory": ["remember", "recall", "memories"],
    }
    for keyword, tool_name in list(hint_map.items()):
        for synonym in _synonyms.get(keyword, []):
            if synonym not in hint_map:
                hint_map[synonym] = tool_name
    return hint_map

def _rewrite_self_referential_tool_call(
    function: Any,
    *,
    root_agent_name: str,
    advertised_tool_names: set[str],
    latest_user_request: str,
) -> bool:
    """Rewrite tool calls where the model calls itself (the root agent name).

    Smaller models sometimes hallucinate a call to the root agent's own name
    (e.g. ``autoyou_agent``) which is not a registered tool.  When possible,
    infer the intended sub-agent from the arguments and rewrite; otherwise
    strip the tool call entirely so ADK does not raise ValueError.

    This function is a **fallback only** - it is never reached when the model
    correctly calls an advertised tool.  Models that understand LiteLLM and
    the normal AutoYou tool path (e.g. qwen3:4b, ministral-3:8b) will never trigger it.
    """
    function_name = _get_obj_field(function, "name")
    if not function_name or function_name != root_agent_name:
        return False

    raw_arguments = _get_obj_field(function, "arguments")
    parsed_arguments = _parse_function_arguments(raw_arguments)

    # Check if the arguments explicitly name a real sub-agent
    target_name = str(
        parsed_arguments.get("agent_name")
        or parsed_arguments.get("target_agent")
        or parsed_arguments.get("name")
        or parsed_arguments.get("tool")
        or ""
    ).strip()
    if target_name and target_name in advertised_tool_names:
        request_text = str(
            parsed_arguments.get("request")
            or parsed_arguments.get("query")
            or latest_user_request
            or ""
        ).strip()
        if request_text:
            _safe_set_function_attr(function, "name", target_name)
            _safe_set_function_attr(function, "arguments", json.dumps({"request": request_text}, ensure_ascii=False))
            LOGGER.warning(
                "Rewrote self-referential '%s' tool call to sub-agent '%s'",
                root_agent_name, target_name,
            )
            return True

    # Dynamically build keyword hints from ALL advertised tools (including
    # user-built agents scaffolded via agent_builder_agent).
    if latest_user_request:
        lower_request = latest_user_request.lower()
        hint_map = _build_dynamic_keyword_hint_map(advertised_tool_names)
        for hint, candidate in hint_map.items():
            if hint in lower_request and candidate in advertised_tool_names:
                _safe_set_function_attr(function, "name", candidate)
                _safe_set_function_attr(function, "arguments", json.dumps({"request": latest_user_request}, ensure_ascii=False))
                LOGGER.warning(
                    "Rewrote self-referential '%s' tool call to '%s' via dynamic keyword hint '%s'",
                    root_agent_name, candidate, hint,
                )
                return True

    # Last resort: strip the self-referential tool call entirely so the model
    # falls back to its text response.  Previously this rewrote to
    # get_current_datetime, but that produced unhelpful datetime JSON for
    # questions like "search for X".  Stripping gives the LLM a second chance
    # to respond naturally or lets the next turn re-route properly.
    _safe_set_function_attr(function, "name", None)
    _safe_set_function_attr(function, "arguments", "{}" if isinstance(raw_arguments, str) else {})
    LOGGER.warning(
        "Stripped unresolvable self-referential '%s' tool call (no advertised tool matched)",
        root_agent_name,
    )
    return True

def _ensure_request_argument_for_agent_tool(
    function: Any,
    *,
    explicit_agent_tool_names: set[str],
    latest_user_request: str,
) -> bool:
    function_name = _get_obj_field(function, "name")
    if function_name not in explicit_agent_tool_names:
        return False

    raw_arguments = _get_obj_field(function, "arguments")
    parsed_arguments = _parse_function_arguments(raw_arguments)
    request_text = str(parsed_arguments.get("request") or "").strip()
    if request_text:
        return False

    # ADK's AgentTool.run_async reads args['request'] unconditionally, so an
    # agent tool call that reaches it without the key raises KeyError and takes
    # down the whole root agent for the turn. Guarantee the key here: prefer the
    # model's own phrasing from an equivalent argument, then the user's latest
    # message, and only then a placeholder - a vague request the sub-agent can
    # answer beats a crash that loses the turn.
    fallback_text = ""
    for alias in ("query", "input", "text", "message", "prompt", "task"):
        candidate = parsed_arguments.get(alias)
        if isinstance(candidate, str) and candidate.strip():
            fallback_text = candidate.strip()
            break

    resolved_request = fallback_text or latest_user_request or _MISSING_AGENT_REQUEST_PLACEHOLDER

    normalized_arguments = dict(parsed_arguments)
    normalized_arguments["request"] = resolved_request
    _safe_set_function_attr(function, "arguments", json.dumps(normalized_arguments, ensure_ascii=False))
    LOGGER.warning(
        "Filled missing request argument for explicit agent tool '%s' (source=%s)",
        function_name,
        "sibling_argument" if fallback_text else ("latest_user_message" if latest_user_request else "placeholder"),
    )
    return True

def _match_bare_explicit_agent_tool_name(
    text: Any,
    *,
    explicit_agent_tool_names: set[str],
) -> Optional[str]:
    if not isinstance(text, str):
        return None

    candidate = text.strip()
    if not candidate:
        return None

    if candidate.startswith("```") and candidate.endswith("```") and len(candidate) >= 6:
        candidate = candidate[3:-3].strip()
    candidate = candidate.strip("` ")
    if not candidate:
        return None

    return candidate if candidate in explicit_agent_tool_names else None

def _extract_agent_tool_name_from_planner_json(
    text: Any,
    *,
    explicit_agent_tool_names: set[str],
) -> Optional[str]:
    if not isinstance(text, str) or not text.strip() or not explicit_agent_tool_names:
        return None
    stripped = text.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        parsed = json.loads(stripped)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None

    candidates: list[str] = []

    def _append_candidate(value: Any) -> None:
        if isinstance(value, str):
            cleaned = value.strip().strip("`'\" ")
            if cleaned and cleaned.lower() not in {"none", "null", "no_action", "no tool", "no_tool"}:
                candidates.append(cleaned)
        elif isinstance(value, dict):
            for nested_key in ("tool_name", "tool", "action", "agent_name", "target_agent", "agent", "name"):
                nested_value = value.get(nested_key)
                if isinstance(nested_value, str):
                    _append_candidate(nested_value)

    for key in ("tool_name", "tool", "action", "agent_name", "target_agent", "agent", "name"):
        _append_candidate(parsed.get(key))

    matched: set[str] = set()
    for candidate in candidates:
        normalized = _normalize_tool_name(candidate)
        if isinstance(normalized, str) and normalized in explicit_agent_tool_names:
            matched.add(normalized)
            continue
        for tool_name in explicit_agent_tool_names:
            if candidate == tool_name:
                matched.add(tool_name)
                continue
            if re.fullmatch(rf"`?{re.escape(tool_name)}`?", candidate):
                matched.add(tool_name)

    return next(iter(matched)) if len(matched) == 1 else None

def _build_tool_name_pattern(tool_names: set[str]) -> str:
    return "|".join(re.escape(name) for name in sorted(tool_names, key=len, reverse=True))

def _tool_context_is_negated(prefix: str) -> bool:
    compact = re.sub(r"\s+", " ", prefix or "").lower()
    return bool(re.search(r"\b(?:do\s+not|don't|never|not|avoid)\b[^.!?]{0,90}$", compact))

def _tool_context_is_disqualified(context: str, *, suffix: str = "") -> bool:
    compact = re.sub(r"\s+", " ", context or "").lower()
    suffix_compact = re.sub(r"\s+", " ", suffix or "").lower()
    if not compact:
        return False
    if suffix_compact and re.match(r"^[^.!?]{0,80}\?", suffix_compact):
        return True
    disqualifiers = (
        r"\bno\s+specific\s+(?:specialized\s+)?(?:task|query|request|tool)\b",
        r"\bno\s+(?:specialized\s+)?tool\s+(?:matches|matched|needed|is\s+needed|applies|is\s+applicable)\b",
        r"\bnot\s+applicable\b",
        r"\bshould\s+not\s+have\s+routed\b",
        r"\bshouldn['']?t\s+have\s+routed\b",
        r"\bshould\s+not\s+(?:route|call|use|invoke)\b",
        r"\bshouldn['']?t\s+(?:route|call|use|invoke)\b",
        r"\bpreviously\s+asked\b[^.!?]{0,160}\bbut\s+now\b",
        r"\bbut\s+now\b[^.!?]{0,120}\b(?:just\s+)?(?:saying|a\s+greeting|casual)\b",
        r"\bjust\s+(?:a\s+)?(?:casual\s+)?greeting\b",
        r"\bcurrent\s+turn\b[^.!?]{0,120}\b(?:just\s+)?(?:a\s+)?(?:casual\s+)?greeting\b",
        r"\bfailed\s+or\s+returned\b[^.!?]{0,120}\b(?:empty|error)\b",
    )
    return any(re.search(pattern, compact) for pattern in disqualifiers)

def _extract_agent_tool_name_from_routing_text(
    text: Any,
    *,
    explicit_agent_tool_names: set[str],
) -> Optional[str]:
    if not isinstance(text, str) or not text.strip() or not explicit_agent_tool_names:
        return None

    compact = re.sub(r"\s+", " ", text).strip()
    name_pattern = _build_tool_name_pattern(explicit_agent_tool_names)
    if not name_pattern:
        return None

    patterns = (
        rf"\b(?:i\s+(?:should|must|need(?:s)?\s+to|will|am\s+going\s+to)\s+)?(?:call|invoke)\s+(?:the\s+)?[`'\"]?(?P<tool>{name_pattern})[`'\"]?(?:\s+tool)?",
        rf"\bi\s+(?:should|must|need(?:s)?\s+to|will|am\s+going\s+to)\s+use\s+(?:the\s+)?[`'\"]?(?P<tool>{name_pattern})[`'\"]?(?:\s+tool)?",
        rf"\b(?:route|routed|routing|forward|delegate|send)\b[^.!?]{{0,120}}\b(?:to|through|via)\s+(?:the\s+)?[`'\"]?(?P<tool>{name_pattern})[`'\"]?",
        rf"\b(?:request|task|message|query)\b[^.!?]{{0,120}}\b(?:should|must|needs?\s+to|will)\s+(?:be\s+)?(?:routed|forwarded|delegated|sent)\s+(?:to|through|via)\s+(?:the\s+)?[`'\"]?(?P<tool>{name_pattern})[`'\"]?",
        rf"[`'\"]?(?P<tool>{name_pattern})[`'\"]?\s+(?:tool|agent)\b[^.!?]{{0,140}}\b(?:should|must|needs?\s+to|will|is\s+the\s+(?:correct|appropriate)|fits|matches)\b",
    )

    matches: list[tuple[int, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, compact, flags=re.IGNORECASE):
            tool_name = match.group("tool")
            if tool_name not in explicit_agent_tool_names:
                continue
            if _tool_context_is_negated(compact[max(0, match.start() - 110):match.start()]):
                continue
            context = compact[max(0, match.start() - 180):min(len(compact), match.end() + 180)]
            suffix = compact[match.end():min(len(compact), match.end() + 120)]
            if _tool_context_is_disqualified(context, suffix=suffix):
                continue
            matches.append((match.start(), tool_name))

    if matches:
        unique = {tool_name for _, tool_name in matches}
        if len(unique) == 1:
            return matches[0][1]
        return None

    mentioned: list[tuple[int, str]] = []
    for tool_name in explicit_agent_tool_names:
        for match in re.finditer(rf"`?{re.escape(tool_name)}`?", compact):
            window_start = max(0, match.start() - 120)
            window_end = min(len(compact), match.end() + 120)
            before = compact[window_start:match.start()]
            suffix = compact[match.end():window_end]
            window = compact[window_start:window_end].lower()
            if _tool_context_is_negated(before):
                continue
            if _tool_context_is_disqualified(window, suffix=suffix):
                continue
            if not re.search(r"\b(?:call|route|routed|routing|delegate|forward|send|use|invoke)\b", window):
                continue
            if not re.search(r"\b(?:should|must|need|needs|will|therefore|correct|appropriate|specialized|tool)\b", window):
                continue
            mentioned.append((match.start(), tool_name))

    unique_mentions = {tool_name for _, tool_name in mentioned}
    if len(unique_mentions) == 1:
        return mentioned[0][1]
    return None

def _extract_agent_tool_name_from_planner_artifact(
    *,
    content: Any,
    reasoning_text: Any,
    explicit_agent_tool_names: set[str],
) -> Optional[str]:
    from_json = _extract_agent_tool_name_from_planner_json(
        content,
        explicit_agent_tool_names=explicit_agent_tool_names,
    )
    if from_json:
        return from_json
    from_reasoning = _extract_agent_tool_name_from_routing_text(
        reasoning_text,
        explicit_agent_tool_names=explicit_agent_tool_names,
    )
    if from_reasoning:
        return from_reasoning
    return _extract_agent_tool_name_from_routing_text(
        content,
        explicit_agent_tool_names=explicit_agent_tool_names,
    )

def _synthesize_explicit_agent_tool_call(
    message: Any,
    *,
    tool_name: str,
    latest_user_request: str,
) -> bool:
    if not tool_name or not latest_user_request:
        return False

    try:
        from litellm.types.utils import ChatCompletionMessageToolCall, Function

        tool_call = ChatCompletionMessageToolCall(
            function=Function(
                arguments=json.dumps({"request": latest_user_request}, ensure_ascii=False),
                name=tool_name,
            ),
            id=f"call_{uuid.uuid4().hex[:12]}",
            type="function",
        )
    except Exception:
        try:
            from types import SimpleNamespace

            tool_call = SimpleNamespace(
                function=SimpleNamespace(
                    arguments=json.dumps({"request": latest_user_request}, ensure_ascii=False),
                    name=tool_name,
                ),
                id=f"call_{uuid.uuid4().hex[:12]}",
                type="function",
            )
        except Exception:
            return False

    _safe_set_function_attr(message, "content", None)
    if not _safe_set_function_attr(message, "tool_calls", [tool_call]):
        try:
            message.tool_calls = [tool_call]
        except Exception:
            return False

    LOGGER.warning(
        "Converted assistant routing artifact '%s' into an explicit agent tool call",
        tool_name,
    )
    return True

def _resolve_root_agent_name() -> str:
    """Return the root agent name from the prompt module without circular imports."""
    try:
        from autoyou_agents.prompt import AGENT_NAME
        return str(AGENT_NAME or "").strip()
    except Exception:
        return "autoyou_agent"

def _resolve_show_thinking_enabled() -> bool:
    """Return whether the admin has opted in to seeing raw model reasoning.

    Mirrors autoyou_agents.model_config._load_model_behavior_config's lazy
    `import server` pattern to avoid a circular import at module load time.
    Defaults to False (hide reasoning) when config isn't reachable.
    """
    try:
        import server as _srv
        cfg = (_srv.STATE.config or {}).get("model_behavior", {})
        return bool(cfg.get("show_thinking", False)) if isinstance(cfg, dict) else False
    except Exception:
        return False

def _is_truncated_finish_reason(finish_reason: Any) -> bool:
    return str(finish_reason or "").strip().lower() in _TRUNCATED_FINISH_REASONS

def _looks_incomplete_after_truncation(text: Optional[str]) -> bool:
    """Return True when text looks cut off mid-thought.

    Only consulted once the provider's own finish_reason already confirms
    truncation - this is a general completeness check (does it end on
    terminal punctuation?), not another reasoning-phrase guess, so it
    doesn't depend on predicting every way a model might phrase reasoning.
    """
    if not isinstance(text, str) or not text.strip():
        return True
    return not bool(_COMPLETE_SENTENCE_END_RE.search(text.strip()))

def _looks_like_reasoning_narrative(text: Any) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    candidate = text.strip()
    return bool(
        _REASONING_PREAMBLE_RE.match(candidate)
        or _REASONING_SENTENCE_FRAGMENT_RE.search(candidate)
        or _SALVAGE_REASONING_MARKER_RE.search(candidate)
    )

def _salvage_complete_prefix_after_truncation(
    text: Optional[str],
    *,
    advertised_tool_names: set[str],
    original_text: Any = None,
) -> Optional[str]:
    """Return the leading complete sentences of a reply cut off by the budget.

    Only consulted once finish_reason has already confirmed truncation. Returns
    None when there is nothing worth keeping, in which case the caller falls
    back to the cut-off notice. Internal reasoning narrative is never salvaged:
    a turn that ran out of room while still thinking has no answer in it yet.

    `original_text` is the raw model content before the reasoning strippers ran.
    It has to be judged too - those strippers can remove a reasoning preamble and
    leave a mid-thought fragment that reads like prose on its own, and salvaging
    that fragment would deliver internal narrative as the answer.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    if _looks_like_reasoning_narrative(original_text) or _looks_like_reasoning_narrative(stripped):
        return None

    end = 0
    for count, match in enumerate(_SALVAGE_SENTENCE_END_RE.finditer(stripped), start=1):
        if match.end() > _SALVAGE_MAX_CHARS:
            break
        end = match.end()
        if count >= _SALVAGE_MAX_SENTENCES:
            break
    if end < _SALVAGE_MIN_CHARS:
        return None

    candidate = stripped[:end].strip()
    if not candidate:
        return None
    if _looks_like_reasoning_narrative(candidate):
        return None
    if looks_like_degenerate_tool_schema_echo(
        candidate,
        advertised_tool_names=advertised_tool_names,
    ):
        return None
    return candidate

def _build_truncated_response_content(reasoning_text: Optional[str]) -> str:
    if not _resolve_show_thinking_enabled():
        return _DEFAULT_TRUNCATED_RESPONSE_MESSAGE
    excerpt = str(reasoning_text or "").strip()
    if not excerpt:
        return _DEFAULT_TRUNCATED_RESPONSE_MESSAGE
    if len(excerpt) > _TRUNCATED_THINKING_EXCERPT_MAX_CHARS:
        excerpt = excerpt[:_TRUNCATED_THINKING_EXCERPT_MAX_CHARS].rstrip() + "…"
    return _TRUNCATED_THINKING_LABEL + excerpt

def _normalize_ollama_response_internal(
    response: Any,
    *,
    messages: List[Dict[str, Any]] | None = None,
    tools: List[Dict[str, Any]] | None = None,
    reasoning_fallback: Optional[str] = None,
) -> Any:
    """Normalize malformed Ollama tool-call markers in LiteLLM responses."""

    advertised_tool_names = set(extract_tool_names(tools))
    explicit_agent_tool_names = _extract_explicit_agent_tool_names(tools)
    latest_user_request = _extract_latest_user_request(messages)
    root_agent_name = _resolve_root_agent_name()

    try:
        for choice in _iter_response_choices(response):
            finish_reason = _get_obj_field(choice, "finish_reason")
            for message_field in ("message", "delta"):
                message = _get_obj_field(choice, message_field)
                if message is None:
                    continue
                _normalize_ollama_message(
                    message,
                    explicit_agent_tool_names=explicit_agent_tool_names,
                    advertised_tool_names=advertised_tool_names,
                    latest_user_request=latest_user_request,
                    root_agent_name=root_agent_name,
                    reasoning_fallback=reasoning_fallback,
                    finish_reason=finish_reason,
                )
    except Exception as exc:
        LOGGER.debug("Failed to normalize Ollama response: %s", exc)

    return response

def _normalize_ollama_message(
    message: Any,
    *,
    explicit_agent_tool_names: set[str],
    advertised_tool_names: set[str],
    latest_user_request: str,
    root_agent_name: str,
    reasoning_fallback: Optional[str],
    finish_reason: Any = None,
) -> None:
    content = _get_obj_field(message, "content")
    internal_reasoning_json = _looks_like_internal_reasoning_json(content)
    normalized_content = _sanitize_assistant_content_text(content)
    tool_calls = _get_obj_field(message, "tool_calls")
    malformed_visible_answer = (
        _extract_malformed_json_answer_text(content)
        if isinstance(content, str) and not tool_calls
        else None
    )
    reasoning_text = _extract_message_reasoning_text(message) or reasoning_fallback
    if not tool_calls and normalized_content is None:
        routing_tool_name = _extract_agent_tool_name_from_planner_artifact(
            content=content,
            reasoning_text=reasoning_text,
            explicit_agent_tool_names=explicit_agent_tool_names,
        )
        if routing_tool_name and _synthesize_explicit_agent_tool_call(
            message,
            tool_name=routing_tool_name,
            latest_user_request=latest_user_request,
        ):
            return
    if (
        not tool_calls
        and (
            _looks_like_malformed_content_stub(normalized_content)
            or malformed_visible_answer
            or internal_reasoning_json
            or (normalized_content is None and isinstance(content, str) and content.strip())
        )
    ):
        routing_tool_name = _extract_agent_tool_name_from_planner_artifact(
            content=content,
            reasoning_text=reasoning_text,
            explicit_agent_tool_names=explicit_agent_tool_names,
        )
        if routing_tool_name and _synthesize_explicit_agent_tool_call(
            message,
            tool_name=routing_tool_name,
            latest_user_request=latest_user_request,
        ):
            return
        reasoning_answer = _extract_final_answer_from_reasoning(
            reasoning_text
        )
        if _ollama_debug_enabled():
            LOGGER.warning(
                "Ollama normalize stub content=%r reasoning_len=%s fallback_len=%s answer_len=%s message_type=%s",
                normalized_content,
                len(reasoning_text or ""),
                len(reasoning_fallback or ""),
                len(reasoning_answer or ""),
                type(message).__name__,
            )
        if reasoning_answer:
            normalized_content = reasoning_answer
            LOGGER.debug("Promoted clean Ollama reasoning answer into visible content")
        elif malformed_visible_answer:
            normalized_content = malformed_visible_answer
    elif not tool_calls and isinstance(normalized_content, str):
        reasoning_answer = _extract_final_answer_from_reasoning(reasoning_text)
        complete_content = _prefer_complete_reasoning_answer_if_content_is_clipped(
            normalized_content,
            reasoning_answer,
        )
        if complete_content:
            normalized_content = complete_content
            LOGGER.debug("Replaced clipped Ollama visible content with clean reasoning answer")
    # The collapse check runs before the truncation branch, not after it. When a
    # collapse also exhausts the token budget - the common case, since a model
    # echoing schema does not stop on its own - the truncation branch used to
    # overwrite normalized_content first, leaving this check inspecting the
    # cut-off notice rather than what the model produced. That made the collapse
    # path unreachable whenever finish_reason was "length", and told the user to
    # "ask a shorter question" when the actionable answer is to pick a model that
    # can handle the advertised tools.
    if not tool_calls and (
        looks_like_degenerate_tool_schema_echo(
            normalized_content,
            advertised_tool_names=advertised_tool_names,
        ) or _looks_like_empty_code_block(normalized_content)
    ):
        LOGGER.warning(
            "Ollama response collapsed into a tool-schema echo or empty code block (%s advertised tools); "
            "replacing it with a clear failure notice instead of delivering it as an answer",
            len(advertised_tool_names),
        )
        normalized_content = _DEFAULT_DEGENERATE_RESPONSE_MESSAGE
    elif (
        not tool_calls
        and _is_truncated_finish_reason(finish_reason)
        and _looks_incomplete_after_truncation(normalized_content)
    ):
        salvaged = _salvage_complete_prefix_after_truncation(
            normalized_content,
            advertised_tool_names=advertised_tool_names,
            original_text=content,
        )
        if salvaged:
            normalized_content = salvaged
            LOGGER.info(
                "Ollama turn hit the token budget mid-sentence; delivering its %d "
                "complete leading characters instead of a cut-off notice",
                len(salvaged),
            )
        else:
            normalized_content = _build_truncated_response_content(reasoning_text)
            LOGGER.debug("Replaced truncated Ollama content with a clear cut-off notice")
    if normalized_content != content:
        _safe_set_function_attr(message, "content", normalized_content)

    bare_tool_name = _match_bare_explicit_agent_tool_name(
        normalized_content,
        explicit_agent_tool_names=explicit_agent_tool_names,
    )
    if bare_tool_name and not _get_obj_field(message, "tool_calls"):
        _synthesize_explicit_agent_tool_call(
            message,
            tool_name=bare_tool_name,
            latest_user_request=latest_user_request,
        )

    tool_calls = _get_obj_field(message, "tool_calls")
    if not isinstance(tool_calls, list):
        return

    for tool_call in tool_calls:
        function = _get_obj_field(tool_call, "function")
        if function is None:
            continue
        original_arguments = _get_obj_field(function, "arguments")
        normalized_arguments = _normalize_tool_arguments(original_arguments)
        if normalized_arguments != original_arguments:
            _safe_set_function_attr(function, "arguments", normalized_arguments)
        original_name = _get_obj_field(function, "name")
        normalized_name = _normalize_tool_name(original_name)
        if normalized_name != original_name and normalized_name:
            _safe_set_function_attr(function, "name", normalized_name)
        _rewrite_self_referential_tool_call(
            function,
            root_agent_name=root_agent_name,
            advertised_tool_names=advertised_tool_names,
            latest_user_request=latest_user_request,
        )
        _rewrite_transfer_tool_call(
            function,
            advertised_tool_names=advertised_tool_names,
            latest_user_request=latest_user_request,
        )
        _fix_unknown_tool_name(
            function,
            advertised_tool_names=advertised_tool_names,
            latest_user_request=latest_user_request,
        )
        # Runs last so it also covers names rewritten by the repairs above:
        # whichever rewrite wins, an agent tool call leaves here with the
        # `request` argument ADK requires.
        _ensure_request_argument_for_agent_tool(
            function,
            explicit_agent_tool_names=explicit_agent_tool_names,
            latest_user_request=latest_user_request,
        )

def _adk_tool_safety_net_installed() -> bool:
    """True when ADK's _get_tool patch can report unknown names to the model."""
    try:
        from autoyou_agents.agent import _ADK_TOOL_SAFETY_NET_INSTALLED

        return bool(_ADK_TOOL_SAFETY_NET_INSTALLED)
    except Exception:
        return False

def _fix_unknown_tool_name(
    function: Any,
    *,
    advertised_tool_names: set[str],
    latest_user_request: str,
) -> bool:
    """Last-resort fix for tool names not in the advertised set."""
    function_name = str(_get_obj_field(function, "name") or "").strip()
    if not function_name or function_name in advertised_tool_names:
        return False

    # Check if this is an unadvertised specialist agent and two-stage routing is active.
    try:
        from autoyou_agents.agent import _resolve_specialist_tool, _ROUTER_TOOL_NAME, _two_stage_routing_active
        is_two_stage_active = _two_stage_routing_active()
    except ImportError:
        _resolve_specialist_tool = None
        _ROUTER_TOOL_NAME = "route_to_specialist"
        is_two_stage_active = False

    if is_two_stage_active and _resolve_specialist_tool:
        resolved_name, specialist_tool = _resolve_specialist_tool(function_name)
        if specialist_tool is not None:
            LOGGER.warning(
                "Model directly called unadvertised specialist '%s'; rewriting to '%s'",
                function_name, _ROUTER_TOOL_NAME,
            )
            _safe_set_function_attr(function, "name", _ROUTER_TOOL_NAME)
            raw_arguments = _get_obj_field(function, "arguments")
            parsed = _parse_function_arguments(raw_arguments)
            # The specialist tool takes a single 'request' argument, or the model
            # might have passed the arguments inside the payload.
            request_text = parsed.get("request") or latest_user_request
            new_args = {
                "agent": resolved_name,
                "request": str(request_text or ""),
            }
            _safe_set_function_attr(function, "arguments", json.dumps(new_args, ensure_ascii=False))
            return True

    # Local models sometimes add a namespace from a different tool protocol
    # (for example ``browser.open``) even though the advertised AutoYou tool
    # names are unqualified. Normalize only unambiguous web aliases and keep
    # their required argument shape valid before ADK sees the call.
    parsed_arguments = _parse_function_arguments(_get_obj_field(function, "arguments"))
    namespace, separator, alias = function_name.lower().rpartition(".")
    alias = alias.strip()
    web_namespace = namespace.strip() in {"web", "browser", "internet", "search"}
    live_web_request = bool(
        re.search(
            # A plain request such as "summarize the news" is not enough to
            # prove that the caller requested live retrieval. Keep the
            # unqualified find/lookup repair scoped to live or web-oriented
            # wording, while requests such as "latest news" still qualify.
            r"\b(current|latest|recent|live|headlines?|web|internet|online|browse|search|article|website)\b",
            str(latest_user_request or ""),
            re.IGNORECASE,
        )
    )
    alias_targets = {
        "search": "internet_search",
        "search_query": "internet_search",
        "query": "internet_search",
        "browse": "scrape_website",
        "fetch": "scrape_website",
        "open": "scrape_website",
        "navigate": "navigate_page",
    }
    if live_web_request and "internet_search" in advertised_tool_names and alias in {"find", "lookup"}:
        alias_targets[alias] = "internet_search"
    if separator and web_namespace and alias == "run":
        alias_targets[alias] = "scrape_website" if parsed_arguments.get("url") else "internet_search"
    alias_target = alias_targets.get(alias)
    if alias_target in advertised_tool_names:
        if alias_target in {"scrape_website", "navigate_page"} and not parsed_arguments.get("url"):
            can_fallback_to_search = bool(parsed_arguments.get("query")) or (
                web_namespace and alias in {"open", "browse", "fetch"}
            )
            alias_target = "internet_search" if can_fallback_to_search else None
        if alias_target in advertised_tool_names:
            if alias_target == "internet_search" and not parsed_arguments.get("query"):
                search_query = parsed_arguments.get("search_query") or parsed_arguments.get("queries")
                if isinstance(search_query, list):
                    search_query = " ".join(
                        str(item.get("q") or item.get("query") or "").strip()
                        if isinstance(item, dict)
                        else str(item or "").strip()
                        for item in search_query
                    ).strip()
                elif isinstance(search_query, dict):
                    search_query = search_query.get("q") or search_query.get("query")
                parsed_arguments["query"] = str(search_query or parsed_arguments.get("q") or latest_user_request)
            elif alias_target == "navigate_page":
                parsed_arguments.setdefault("actions", [])
            LOGGER.warning(
                "Normalized unqualified web-tool alias '%s' to '%s'",
                function_name,
                alias_target,
            )
            _safe_set_function_attr(function, "name", alias_target)
            _safe_set_function_attr(
                function,
                "arguments",
                json.dumps(parsed_arguments, ensure_ascii=False),
            )
            return True

    # Try substring match (e.g. model says "notes_agent" but tool is "autoyou_notes_agent")
    for advertised in advertised_tool_names:
        if function_name in advertised or advertised in function_name:
            LOGGER.warning(
                "Fuzzy-matched hallucinated tool name '%s' to '%s'",
                function_name, advertised,
            )
            _safe_set_function_attr(function, "name", advertised)
            raw_arguments = _get_obj_field(function, "arguments")
            parsed = _parse_function_arguments(raw_arguments)
            if not parsed.get("request") and latest_user_request:
                parsed["request"] = latest_user_request
                _safe_set_function_attr(function, "arguments", json.dumps(parsed, ensure_ascii=False))
            return True

    # No match found. Coercing the call into an unrelated tool answers a
    # question the model never asked - a hallucinated `find` came back as the
    # current time - and burns the turn without telling it anything. ADK's
    # _get_tool safety net instead returns "that tool does not exist, here are
    # the ones that do", which the model can act on, so leave the name for it.
    if _adk_tool_safety_net_installed():
        LOGGER.warning(
            "Unknown tool name '%s' not in advertised tools %s; leaving it for the ADK "
            "safety net to report back to the model",
            function_name, sorted(advertised_tool_names),
        )
        return False

    # Without that safety net an unknown name aborts the run, so fall back to a
    # harmless advertised tool rather than crashing.
    if "get_current_datetime" not in advertised_tool_names:
        LOGGER.warning(
            "Unknown tool name '%s' not in advertised tools %s and no safe fallback tool is advertised",
            function_name, sorted(advertised_tool_names),
        )
        return False

    LOGGER.warning(
        "Unknown tool name '%s' not in advertised tools %s; rewriting to get_current_datetime",
        function_name, sorted(advertised_tool_names),
    )
    raw_arguments = _get_obj_field(function, "arguments")
    _safe_set_function_attr(function, "name", "get_current_datetime")
    _safe_set_function_attr(function, "arguments", "{}" if isinstance(raw_arguments, str) else {})
    return True

def extract_tool_names(tools: List[Dict[str, Any]] | None) -> List[str]:
    """Return the full advertised tool-name list from a LiteLLM tools payload."""

    if not isinstance(tools, list):
        return []

    tool_names: List[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name.strip():
            tool_names.append(name.strip())

    return tool_names

def summarize_tool_names_for_debug(tools: List[Dict[str, Any]] | None, limit: int = 12) -> List[str]:
    """Return a compact summary of advertised tool names for provider error logs."""

    if limit <= 0:
        return []
    return extract_tool_names(tools)[:limit]

def summarize_messages_for_debug(messages: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    """Return a compact summary of recent messages for provider error logs."""

    summaries: List[Dict[str, Any]] = []
    for message in (messages or [])[-limit:]:
        content = message.get("content")
        if isinstance(content, str):
            content_summary: Optional[str] = content[:120]
        elif isinstance(content, list):
            part_types = []
            for part in content:
                if isinstance(part, dict):
                    part_types.append(str(part.get("type", "text")))
                else:
                    part_types.append(type(part).__name__)
            content_summary = f"parts:{','.join(part_types)}"
        else:
            content_summary = None

        tool_names: List[str] = []
        for tool_call in message.get("tool_calls") or []:
            if isinstance(tool_call, dict):
                function = tool_call.get("function")
                if isinstance(function, dict) and function.get("name"):
                    tool_names.append(str(function["name"]))

        summaries.append(
            {
                "role": message.get("role"),
                "content": content_summary,
                "tool_calls": tool_names,
            }
        )

    return summaries
