# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-to-fb9a8ed9039f9eef39271bc9


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import logging
import re
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

from google.adk.agents import Agent

# Memory is handled by root AutoYou memory tools.

from .notes_tool import NotesTool
from .prompt import (
    AGENT_NAME,
    AGENT_DESCRIPTION,
    AGENT_INSTRUCTION,
    EXPANDED_AGENT_INSTRUCTION,
)
from autoyou_agents.model_config import model_uses_expanded_harness
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime, inject_realtime_datetime_into_request
from shared.session_execution import create_text_llm_response, create_tool_call_llm_response

__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-to-fb9a8ed9039f9eef39271bc9"


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_notes_tool_instance: Optional[NotesTool] = None


def _get_notes_tool() -> NotesTool:
    """Open notes storage on first use rather than at import time.

    Constructing NotesTool during import meant any storage fault - a sealed
    database, an unwritable path - propagated out of the module import, which
    dropped the entire notes agent from the runtime registry. The root agent
    then had no notes capability at all yet still fielded notes requests, so it
    answered from the model alone and reported work it had never done.

    Deferring construction keeps the agent loadable and turns a storage fault
    into a per-tool error the caller actually sees. The instance is cached only
    on success, so fixing the underlying storage recovers without a restart.
    """
    global _notes_tool_instance
    if _notes_tool_instance is None:
        _notes_tool_instance = NotesTool()
    return _notes_tool_instance


def notes_storage_error() -> Optional[str]:
    """Return why notes storage cannot be opened, or None when it is healthy."""
    try:
        _get_notes_tool()
    except Exception as exc:
        return str(exc)
    return None


_NON_NOTES_FILE_OPERATION_PATTERN = re.compile(
    r"\b(?:inspect|list|show|rename|move|copy|delete|remove|create)\b.*\b(?:file|folder|directory|path|filesystem|local file|local folder|song|track|music)\b",
    re.IGNORECASE,
)
_NOTES_LAST_MUTATION_STATE_KEY = "_autoyou_notes_last_mutation"
_NOTES_DISPATCH_INVOCATION_STATE_KEY = "_autoyou_notes_dispatch_invocation"
_NOTES_DISPATCH_RESULT_STATE_KEY = "_autoyou_notes_dispatch_result"
_NOTES_DISPATCH_SUCCESS_STATE_KEY = "_autoyou_notes_dispatch_success"
_NOTES_MUTATION_PATTERN = re.compile(
    r"\b(?:create|make|add|append|update|edit|change|modify|save|store|write|put|delete|remove)\b.*\b(?:note|notes|entry|record)\b"
    r"|\b(?:note|notes|entry|record)\b.*\b(?:create|make|add|append|update|edit|change|modify|save|store|write|put|delete|remove)\b",
    re.IGNORECASE | re.DOTALL,
)
_NOTES_TITLE_PATTERN = re.compile(
    r"\b(?:title(?:d)?|named|called|heading|that\s+says|saying)\s*(?:is|:|=)?\s*"
    r"(?:\"([^\"]+)\"|'([^']+)'|([^,;\n]+?))"
    r"(?=\s*(?:[,;]|\band\s+(?:content|body|text)\b|\bcontent\b|\bbody\b|\btext\b|\btag\b|$))",
    re.IGNORECASE | re.DOTALL,
)
_NOTES_CONTENT_FIELD_PATTERN = re.compile(
    r"(?:(?:^|[,;\n])\s*(?:the\s+)?|\b(?:with|and)\s+)"
    r"(?:content|body|text)\s*(?:is|should be|:|=|,)?\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_NOTES_ID_PATTERN = re.compile(r"(?:\bnotes?\s*#?\s*(\d+)\b|#(\d+)\b)", re.IGNORECASE)


def _notes_state(callback_context: Any) -> Any:
    return getattr(callback_context, "state", None)


def _content_text(content: Any) -> str:
    parts: List[str] = []
    for part in getattr(content, "parts", []) or []:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text.strip() and not getattr(part, "thought", False):
            parts.append(text.strip())
    return "\n".join(parts).strip()


def _request_history_texts(llm_request: Any, role: str) -> List[str]:
    return [
        text
        for content in getattr(llm_request, "contents", []) or []
        if str(getattr(content, "role", "") or "").strip().lower() == role
        for text in [_content_text(content)]
        if text
    ]


def _strip_wrapping_quotes(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"\"", "'", "`"}:
        text = text[1:-1].strip()
    return text.strip()


def _extract_note_title(user_text: str) -> Optional[str]:
    match = _NOTES_TITLE_PATTERN.search(str(user_text or ""))
    if not match:
        return None
    title = next((group for group in match.groups() if group), "")
    title = _strip_wrapping_quotes(title).strip(" .,:;!?\"")
    return title or None


def _extract_note_id(user_text: str) -> Optional[int]:
    match = _NOTES_ID_PATTERN.search(str(user_text or ""))
    if not match:
        return None
    try:
        return int(next(group for group in match.groups() if group))
    except (StopIteration, TypeError, ValueError):
        return None


def _infer_note_title(previous_user_text: str) -> Optional[str]:
    """Infer a bounded title from a recognizable preceding question."""
    text = " ".join(str(previous_user_text or "").split()).strip(" .!?\"")
    if not text:
        return None
    match = re.match(
        r"(?:what|who)\s+(?:is|are|was|were)\s+(?:the\s+concept\s+of\s+)?(.+)$"
        r"|(?:explain|define|describe|tell\s+me\s+about)\s+(?:the\s+concept\s+of\s+)?(.+)$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    subject = next((group for group in match.groups() if group), "").strip(" .!?\"")
    return subject[:120] or None


def _extract_embedded_note_context(text: str, marker: str, end_marker: Optional[str] = None) -> str:
    suffix = rf"(?=\n\s*{re.escape(end_marker)}|$)" if end_marker else r"$"
    match = re.search(
        rf"{re.escape(marker)}\s*\n?(.*?){suffix}",
        str(text or ""),
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _extract_note_tags(user_text: str) -> Optional[List[str]]:
    match = re.search(
        r"\b(?:with\s+)?tags?\s*(?:is|:|=)?\s*(?:\"([^\"]+)\"|'([^']+)'|([^,;\n.]+))",
        str(user_text or ""),
        re.IGNORECASE,
    )
    if not match:
        return None
    raw_tag = next((group for group in match.groups() if group), "")
    tag = _strip_wrapping_quotes(raw_tag).strip(" .,:;!?\"")
    tags = [item.strip() for item in re.split(r"[,;\s]+", tag) if item.strip()]
    return tags or None


def _extract_note_content(user_text: str, previous_model_text: str) -> Optional[str]:
    text = str(user_text or "").strip()
    field_text = text.split("[AutoYou previous user request]", 1)[0].rstrip()
    match = _NOTES_CONTENT_FIELD_PATTERN.search(field_text)
    if match:
        content = match.group(1).strip()
        content = re.sub(
            r"(?:\s*,\s*|\s+)(?:with\s+)?tags?\s+.*$",
            "",
            content,
            flags=re.IGNORECASE,
        ).strip()
        content = _strip_wrapping_quotes(content).strip(" ,;")
        if content and not re.fullmatch(
            r"(?:the\s+)?(?:previous|earlier|above|this|that|it|the answer|the response|the reply)"
            r"(?:\s+(?:answer|response|reply|content))?[.!?]?",
            content,
            flags=re.IGNORECASE,
        ):
            return content
    if re.search(
        r"\b(?:all\s+this|previous|earlier|above|this\s+answer|this\s+response|this\s+reply)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return previous_model_text.strip() or _extract_embedded_note_context(
            text,
            "[AutoYou previous assistant answer; save this as note content only]",
        ) or None
    return None


def _resolve_note_id_by_title(title: Optional[str]) -> Optional[int]:
    if not title:
        return None
    try:
        matches = _get_notes_tool().search_notes(title, limit=10)
    except Exception:
        return None
    exact = [
        note for note in matches
        if str(note.get("title") or "").strip().casefold() == title.casefold()
        and note.get("id") is not None
    ]
    return int(exact[0]["id"]) if len(exact) == 1 else None


def _build_deterministic_note_mutation(
    user_text: str,
    llm_request: Any,
    state: Any,
) -> Optional[Dict[str, Any]]:
    text = str(user_text or "").strip()
    if not text or not _NOTES_MUTATION_PATTERN.search(text):
        if not (_extract_note_title(text) and _NOTES_CONTENT_FIELD_PATTERN.search(text)):
            return None

    previous_users = _request_history_texts(llm_request, "user")[:-1]
    previous_models = _request_history_texts(llm_request, "model")
    previous_user_text = previous_users[-1] if previous_users else _extract_embedded_note_context(
        text,
        "[AutoYou previous user request]",
        "[AutoYou previous assistant answer; save this as note content only]",
    )
    previous_model_text = previous_models[-1] if previous_models else _extract_embedded_note_context(
        text,
        "[AutoYou previous assistant answer; save this as note content only]",
    )
    title = _extract_note_title(text) or _infer_note_title(previous_user_text)
    content = _extract_note_content(text, previous_model_text)
    note_id = _extract_note_id(text)
    last_mutation = state.get(_NOTES_LAST_MUTATION_STATE_KEY, {}) if state is not None else {}
    if note_id is None and isinstance(last_mutation, dict) and last_mutation.get("note_id") is not None:
        if re.search(r"\b(?:that|the|this|it|existing|current|same)\s+note\b", text, re.IGNORECASE):
            note_id = int(last_mutation["note_id"])
    if note_id is None and re.search(r"\b(?:update|edit|change|modify)\b", text, re.IGNORECASE):
        note_id = _resolve_note_id_by_title(title)

    if re.search(r"\b(?:delete|remove)\b", text, re.IGNORECASE):
        if note_id is None:
            return {"clarify": "I can delete a note after you provide its note id."}
        return {"tool": "delete_note", "args": {"note_id": note_id}}

    is_update = bool(
        re.search(r"\b(?:update|edit|change|modify|append|add)\b", text, re.IGNORECASE)
        and note_id is not None
    )
    if is_update:
        if not content:
            return {"clarify": "I can update that note after you provide the new content."}
        return {"tool": "update_note", "args": {"note_id": note_id, "content": content}}

    is_create = bool(
        re.search(r"\b(?:create|make|new|save|store)\b", text, re.IGNORECASE)
        or (title and content and not note_id)
        or re.search(r"\badd\b.*\b(?:to|in)\s+(?:my\s+)?notes?\b", text, re.IGNORECASE)
    )
    if not is_create:
        return None
    if not title or not content:
        missing = "the title and content" if not title and not content else (
            "the title" if not title else "the content"
        )
        return {"clarify": f"I can create that note - what should {missing} be?"}

    args: Dict[str, Any] = {"title": title, "content": content}
    tags = _extract_note_tags(text)
    if tags:
        args["tags"] = tags
    return {"tool": "create_note", "args": args}


def _notes_after_tool_callback(
    tool: Any,
    args: Dict[str, Any],
    tool_context: Any,
    tool_response: Any,
) -> Any:
    state = _notes_state(tool_context)
    if state is None or not isinstance(tool_response, dict):
        return None
    tool_name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "").strip()
    if tool_name not in {"create_note", "update_note", "delete_note"}:
        return None
    succeeded = str(tool_response.get("status") or "").strip().lower() == "success"
    invocation_id = str(getattr(tool_context, "invocation_id", "") or "") or "__no_invocation__"
    if state.get(_NOTES_DISPATCH_INVOCATION_STATE_KEY) == invocation_id:
        fallback = (
            "The note operation completed without a result message."
            if succeeded
            else "The note operation failed and nothing was saved."
        )
        state[_NOTES_DISPATCH_RESULT_STATE_KEY] = str(
            tool_response.get("message") or tool_response.get("error") or fallback
        )
        state[_NOTES_DISPATCH_SUCCESS_STATE_KEY] = succeeded
    if not succeeded:
        return None
    if tool_name == "create_note":
        note_id = tool_response.get("note_id")
        if note_id is not None:
            state[_NOTES_LAST_MUTATION_STATE_KEY] = {
                "operation": tool_name,
                "note_id": note_id,
                "title": args.get("title"),
            }
    elif args.get("note_id") is not None:
        previous = state.get(_NOTES_LAST_MUTATION_STATE_KEY, {})
        state[_NOTES_LAST_MUTATION_STATE_KEY] = {
            **(previous if isinstance(previous, dict) else {}),
            "operation": tool_name,
            "note_id": args.get("note_id"),
        }
    return None


def _extract_text_from_llm_request(llm_request: Any) -> str:
    for content in reversed(getattr(llm_request, "contents", []) or []):
        if str(getattr(content, "role", "") or "").strip().lower() not in {"", "user"}:
            continue
        parts: list[str] = []
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            return " ".join(parts).strip()
    return ""

def _extract_list_filters(user_text: str) -> Dict[str, Optional[str]]:
    lowered = " ".join(str(user_text or "").lower().split())
    filters: Dict[str, Optional[str]] = {
        "created_on": None,
        "created_after": None,
        "created_before": None,
    }
    if "today" in lowered:
        filters["created_on"] = "today"
    elif "yesterday" in lowered:
        filters["created_on"] = "yesterday"
    elif "last week" in lowered or "past week" in lowered:
        filters["created_after"] = "last week"
    return filters

def _normalize_note_date_value(value: Optional[str], *, allow_last_week: bool = False) -> Optional[str]:
    if not value:
        return None
    normalized = str(value).strip().lower()
    today = datetime.now().date()
    # from __debug_provenance_d__ import to
    if normalized in {"today", "now"}:
        return today.strftime("%Y-%m-%d")
    if normalized == "yesterday":
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")
    if allow_last_week and normalized in {"last week", "past week"}:
        return (today - timedelta(days=7)).strftime("%Y-%m-%d")
    return str(value)

def _normalize_note_filters(
    *,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    created_on: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    return {
        "created_after": _normalize_note_date_value(created_after, allow_last_week=True),
        "created_before": _normalize_note_date_value(created_before),
        "created_on": _normalize_note_date_value(created_on),
    }

def _build_note_filter_text(
    *,
    category: Optional[str] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    created_on: Optional[str] = None,
    query: Optional[str] = None,
) -> str:
    filter_desc: List[str] = []
    if category:
        filter_desc.append(f"category '{category}'")
    if created_on:
        filter_desc.append(f"created on {created_on}")
    elif created_after or created_before:
        if created_after and created_before:
            filter_desc.append(f"created between {created_after} and {created_before}")
        elif created_after:
            filter_desc.append(f"created after {created_after}")
        elif created_before:
            filter_desc.append(f"created before {created_before}")
    if query:
        filter_desc.append(f"matching '{query}'")
    return " with " + " and ".join(filter_desc) if filter_desc else ""

def _format_note_preview(note: Dict[str, Any]) -> str:
    title = str(note.get("title") or "(untitled)").strip()
    created_at = str(note.get("created_at") or "").strip()
    note_id = note.get("id")
    detail = f"#{note_id}: {title}" if note_id is not None else title
    return detail + (f" ({created_at})" if created_at else "")

def _render_note_listing(notes: List[Dict[str, Any]], *, count: int, prefix: str) -> str:
    if not notes:
        return prefix
    preview_lines = "\n".join(f"- {_format_note_preview(note)}" for note in notes[:5])
    return f"{prefix}\n\nTop results:\n{preview_lines}"

def _extract_search_query(user_text: str) -> Optional[str]:
    text = str(user_text or "").strip()
    patterns = (
        r"\bsearch\s+notes?\s+(?:for|about)\s+(.+)$",
        r"\bfind\s+notes?\s+(?:for|about)\s+(.+)$",
        r"\bsearch\s+for\s+(.+?)\s+in\s+notes?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            return match.group(1).strip(" .,:;!?\"'")
    return None

def _is_count_request(user_text: str) -> bool:
    lowered = " ".join(str(user_text or "").lower().split())
    return bool(
        lowered
        and (
            "how many notes" in lowered
            or "do i have notes" in lowered
            or "do i have any notes" in lowered
            or "notes count" in lowered
            or "count notes" in lowered
        )
    )

def _is_list_request(user_text: str) -> bool:
    lowered = " ".join(str(user_text or "").lower().split())
    if not lowered:
        return False
    if re.search(r"\b(?:add|create|make|save|store|new|update|edit|change|delete|remove)\b", lowered):
        return False
    return any(
        phrase in lowered
        for phrase in (
            "show notes",
            "list notes",
            "what notes do i have",
            "which notes do i have",
            "show my notes",
            "list my notes",
            "go to notes agent",
        )
    )

def _is_non_notes_file_operation_request(user_text: str) -> bool:
    lowered = " ".join(str(user_text or "").lower().split())
    if not lowered:
        return False
    if re.search(r"\b(?:rename|move|copy|delete|remove)\b", lowered) and re.search(r"\bnotes?\b", lowered):
        return False
    if "attachment" in lowered and re.search(r"\b(?:save|attach|ingest|upload)\b", lowered):
        return False
    return bool(_NON_NOTES_FILE_OPERATION_PATTERN.search(lowered))

async def _notes_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    del callback_context
    inject_realtime_datetime_into_request(llm_request)
    user_text = _extract_text_from_llm_request(llm_request)
    if not user_text:
        return None

    if _is_non_notes_file_operation_request(user_text):
        return create_text_llm_response(
            "I can save files into notes, but I cannot rename, move, copy, delete, or inspect arbitrary local files. Use autoyou_files_agent for authenticated local filesystem operations.",
            custom_metadata={"response_author": AGENT_NAME, "notes_query_kind": "scope_guard"},
        )

    filters = _extract_list_filters(user_text)

    if _is_count_request(user_text):
        count_result = count_notes(**filters)
        if count_result.get("status") != "success":
            return create_text_llm_response(str(count_result.get("message") or "Failed to read notes."))
        preview_result = list_notes(limit=5, **filters)
        preview_notes = list(preview_result.get("notes") or []) if preview_result.get("status") == "success" else []
        count = int(count_result.get("count") or 0)
        noun = "note" if count == 1 else "notes"
        filter_text = _build_note_filter_text(**filters)
        prefix = f"You have **{count} {noun}**{filter_text}." if filter_text else f"You have **{count} {noun}**."
        message = _render_note_listing(
            preview_notes,
            count=count,
            prefix=prefix,
        )
        return create_text_llm_response(
            message,
            custom_metadata={"response_author": AGENT_NAME, "notes_query_kind": "count"},
        )

    search_query = _extract_search_query(user_text)
    if search_query:
        result = search_notes(search_query, limit=10)
        if result.get("status") != "success":
            return create_text_llm_response(str(result.get("message") or "Failed to search notes."))
        count = int(result.get("count") or 0)
        prefix = (
            f"Found **{count} notes** matching \u201c{search_query}\u201d."
            if count
            else f"No notes found matching \u201c{search_query}\u201d."
        )
        return create_text_llm_response(
            _render_note_listing(list(result.get("results") or []), count=count, prefix=prefix),
            custom_metadata={"response_author": AGENT_NAME, "notes_query_kind": "search"},
        )

    if _is_list_request(user_text):
        result = list_notes(limit=50, **filters)
        if result.get("status") != "success":
            return create_text_llm_response(str(result.get("message") or "Failed to list notes."))
        count = int(result.get("count") or 0)
        prefix = f"Found **{count} notes**." if count else "No notes found with these filters."
        return create_text_llm_response(
            _render_note_listing(list(result.get("notes") or []), count=count, prefix=prefix),
            custom_metadata={"response_author": AGENT_NAME, "notes_query_kind": "list"},
        )

    return None


async def _notes_expanded_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    """Provide deterministic mutation dispatch for larger local models.

    The compact profile deliberately leaves writes to the model. Larger
    models get the former guarded path: explicit fields are parsed before
    generation, the selected tool is dispatched once, and the next callback
    turn narrates only the verified tool result.
    """
    inject_realtime_datetime_into_request(llm_request)
    user_text = _extract_text_from_llm_request(llm_request)
    if not user_text:
        return None

    state = _notes_state(callback_context)
    invocation_id = str(getattr(callback_context, "invocation_id", "") or "") or "__no_invocation__"

    if _is_non_notes_file_operation_request(user_text):
        return create_text_llm_response(
            "I can save files into notes, but I cannot rename, move, copy, delete, or inspect arbitrary local files. Use autoyou_files_agent for authenticated local filesystem operations.",
            custom_metadata={"response_author": AGENT_NAME, "notes_query_kind": "scope_guard"},
        )

    if state is not None and state.get(_NOTES_DISPATCH_INVOCATION_STATE_KEY) == invocation_id:
        result_message = str(state.get(_NOTES_DISPATCH_RESULT_STATE_KEY) or "").strip()
        if result_message:
            return create_text_llm_response(
                result_message,
                custom_metadata={
                    "response_author": AGENT_NAME,
                    "notes_mutation_verified": bool(state.get(_NOTES_DISPATCH_SUCCESS_STATE_KEY)),
                },
            )

    mutation = _build_deterministic_note_mutation(user_text, llm_request, state)
    if mutation and mutation.get("clarify"):
        return create_text_llm_response(
            str(mutation["clarify"]),
            custom_metadata={
                "response_author": AGENT_NAME,
                "notes_query_kind": "clarify_missing_field",
            },
        )
    if mutation and state is not None:
        state[_NOTES_DISPATCH_INVOCATION_STATE_KEY] = invocation_id
        state[_NOTES_DISPATCH_RESULT_STATE_KEY] = ""
        state[_NOTES_DISPATCH_SUCCESS_STATE_KEY] = False
        return create_tool_call_llm_response(
            mutation["tool"],
            mutation["args"],
            custom_metadata={
                "response_author": AGENT_NAME,
                "notes_mutation_dispatch": "before_model",
            },
        )

    return await _notes_before_model_callback(callback_context, llm_request)

def ingest_attachments(
    attachments: List[Dict[str, Any]],
    route_to: Optional[str] = None,
    source: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    default_title: Optional[str] = None,
    default_content: Optional[str] = None,
    append_to_note_id: Optional[int] = None,
    append_to_recent: bool = False,
) -> dict:
    """Ingest attachments and persist them as notes media or route to page feed.

    Args:
        attachments: List of attachment dicts containing keys like `path` (preferred),
            `filename`, `mimetype`, and optional `data` (base64) or `url`.
        route_to: "notes", "page", or None/"auto" to let the tool decide based on mimetype.
        source: Optional source label (e.g., "telegram", "signal", "whatsapp").
        user_id: Optional user identifier.
        session_id: Optional session identifier.
        message_id: Optional upstream message id.

    Returns:
        dict: Summary of saved items and any errors.
    """
    try:
        res = _get_notes_tool().ingest_attachments(
            attachments=attachments,
            route_to=route_to,
            source=source,
            user_id=user_id,
            session_id=session_id,
            message_id=message_id,
            default_title=default_title,
            default_content=default_content,
            append_to_note_id=append_to_note_id,
            append_to_recent=append_to_recent,
        )
        return {"status": "success", **res}
    except Exception as e:
        return {"status": "error", "message": f"Failed to ingest attachments: {e}"}

def save_attachment_from_path(
    path: str,
    filename: Optional[str] = None,
    mimetype: Optional[str] = None,
    *,
    source: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> dict:
    """Persist an attachment into notes storage from a local filesystem path.

    Wraps NotesTool.save_media_attachment_from_path.
    """
    try:
        res = _get_notes_tool().save_media_attachment_from_path(
            path=path,
            filename=filename,
            mimetype=mimetype,
            source=source,
            user_id=user_id,
            session_id=session_id,
            message_id=message_id,
            metadata=metadata,
        )
        return {"status": "success", "attachment": res}
    except Exception as e:
        return {"status": "error", "message": f"Failed to save attachment from path: {e}"}

def create_note(title: str, content: str, tags: Optional[List[str]] = None, category: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> dict:
    """Create a new note with the given title, content, tags, category, and optional metadata.
    
    Args:
        title: The title of the note
        content: The content/body of the note
        tags: Optional list of tags for the note
        category: Optional category for the note
        metadata: Optional JSON-serializable object containing extra info (e.g., media links)
        
    Returns:
        dict: Result with note_id if successful, or error message
    """
    try:
        result = _get_notes_tool().create_note(
            title=title,
            content=content,
            tags=tags or [],
            category=category,
            metadata=metadata,
        )
        if result.get('success'):
            return {
                "status": "success",
                "note_id": result['note_id'],
                "message": f"Note '{title}' created successfully with ID {result['note_id']}"
            }
        else:
            return {
                "status": "error",
                "message": result.get('error', 'Unknown error occurred')
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to create note: {str(e)}"
        }

def search_notes(query: str, limit: Optional[int] = 10) -> dict:
    """Search for notes containing the given query.
    
    Args:
        query: The search query to find in notes
        limit: Maximum number of results to return (default: 10)
        
    Returns:
        dict: Search results with matching notes
    """
    try:
        bounded_limit = limit or 10
        results = _get_notes_tool().search_notes(query=query, limit=bounded_limit)
        total_count = _get_notes_tool().count_notes(query=query)
        return {
            "status": "success",
            "results": results,
            "count": total_count,
            "returned_count": len(results),
            "message": f"Found {total_count} notes matching '{query}'"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to search notes: {str(e)}"
        }

def count_notes(
    category: Optional[str] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    created_on: Optional[str] = None,
    query: Optional[str] = None,
) -> dict:
    """Count notes using the same NotesTool SQL path as the notes website."""
    try:
        normalized_filters = _normalize_note_filters(
            created_after=created_after,
            created_before=created_before,
            created_on=created_on,
        )
        total_count = _get_notes_tool().count_notes(
            category=category,
            created_after=normalized_filters["created_after"],
            created_before=normalized_filters["created_before"],
            created_on=normalized_filters["created_on"],
            query=query,
        )
        filter_text = _build_note_filter_text(
            category=category,
            created_after=created_after,
            created_before=created_before,
            created_on=created_on,
            query=query,
        )
        noun = "note" if total_count == 1 else "notes"
        return {
            "status": "success",
            "count": total_count,
            "message": f"Found {total_count} {noun}{filter_text}",
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to count notes: {str(e)}",
        }

def list_notes(category: Optional[str] = None, limit: Optional[int] = NotesTool.DEFAULT_LIST_LIMIT, 
               created_after: Optional[str] = None, created_before: Optional[str] = None,
               created_on: Optional[str] = None) -> dict:
    """List all notes, optionally filtered by category and/or date.
    
    Args:
        category: Optional category to filter by
        limit: Maximum number of notes to return (default: NotesTool.DEFAULT_LIST_LIMIT)
        created_after: Show notes created after this date (YYYY-MM-DD or keywords like 'today')
        created_before: Show notes created before this date (YYYY-MM-DD or keywords like 'today')
        created_on: Show notes created on this specific date (YYYY-MM-DD or keywords like 'today')
        
    Returns:
        dict: List of notes
    """
    try:
        normalized_filters = _normalize_note_filters(
            created_after=created_after,
            created_before=created_before,
            created_on=created_on,
        )

        results = _get_notes_tool().list_notes(
            category=category, 
            limit=limit,
            created_after=normalized_filters["created_after"],
            created_before=normalized_filters["created_before"],
            created_on=normalized_filters["created_on"],
        )
        total_count = _get_notes_tool().count_notes(
            category=category,
            created_after=normalized_filters["created_after"],
            created_before=normalized_filters["created_before"],
            created_on=normalized_filters["created_on"],
        )
        returned_count = len(results)
        filter_text = _build_note_filter_text(
            category=category,
            created_after=created_after,
            created_before=created_before,
            created_on=created_on,
        )

        if total_count <= 0:
            message = f"No notes found{filter_text}."
        elif returned_count < total_count:
            message = f"Retrieved {returned_count} of {total_count} notes{filter_text}"
        else:
            message = f"Retrieved {returned_count} notes{filter_text}"

        return {
            "status": "success",
            "notes": results,
            "count": total_count,
            "returned_count": returned_count,
            "message": message,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to list notes: {str(e)}"
        }

def get_note(note_id: int) -> dict:
    """Retrieve a specific note by its ID.
    
    Args:
        note_id: The ID of the note to retrieve
        
    Returns:
        dict: The note data if found, or error message
    """
    try:
        note = _get_notes_tool().get_note(note_id)
        if note:
            return {
                "status": "success",
                "note": note,
                "message": f"Retrieved note {note_id}"
            }
        else:
            return {
                "status": "error",
                "message": f"Note {note_id} not found"
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to get note: {str(e)}"
        }

def update_note(note_id: int, title: Optional[str] = None, content: Optional[str] = None, 
                tags: Optional[List[str]] = None, category: Optional[str] = None) -> dict:
    """Update an existing note.
    
    Args:
        note_id: The ID of the note to update
        title: New title for the note (optional)
        content: New content for the note (optional)
        tags: New tags for the note (optional)
        category: New category for the note (optional)
        
    Returns:
        dict: Success or error message
    """
    try:
        success = _get_notes_tool().update_note(
            note_id=note_id,
            title=title,
            content=content,
            tags=tags,
            category=category
        )
        if success:
            return {
                "status": "success",
                "message": f"Note {note_id} updated successfully"
            }
        else:
            return {
                "status": "error",
                "message": f"Failed to update note {note_id} - note may not exist"
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to update note: {str(e)}"
        }

def delete_note(note_id: int) -> dict:
    """Delete a note by its ID.
    
    Args:
        note_id: The ID of the note to delete
        
    Returns:
        dict: Success or error message
    """
    try:
        success = _get_notes_tool().delete_note(note_id)
        if success:
            return {
                "status": "success",
                "message": f"Note {note_id} deleted successfully"
            }
        else:
            return {
                "status": "error",
                "message": f"Failed to delete note {note_id} - note may not exist"
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to delete note: {str(e)}"
        }

def create_notes_agent(model_config):
    """Create a notes agent with the provided model configuration.
    
    Args:
        model_config: The model configuration to use for the agent
        
    Returns:
        Agent: Configured notes agent
    """
    # Prepare tools list with memory tools if available
    tools = [
        create_note,
        count_notes,
        search_notes,
        list_notes,
        get_note,
        update_note,
        delete_note,
        ingest_attachments,
        save_attachment_from_path,
        get_current_datetime,
    ]
    
    # Note: Memory tools omitted. Root Memory Agent now provides access.
    
    expanded = model_uses_expanded_harness(model_config)
    logger.info(
        "Notes agent harness profile=%s model=%s",
        "expanded" if expanded else "compact",
        getattr(model_config, "model", model_config),
    )
    agent_kwargs = {
        "name": AGENT_NAME,
        "model": model_config,
        "description": AGENT_DESCRIPTION,
        "instruction": EXPANDED_AGENT_INSTRUCTION if expanded else AGENT_INSTRUCTION,
        "before_model_callback": [
            _notes_expanded_before_model_callback if expanded else _notes_before_model_callback,
        ],
        "tools": tools,
    }
    if expanded:
        agent_kwargs["after_tool_callback"] = [_notes_after_tool_callback]

    return Agent(
        **agent_kwargs
    )
