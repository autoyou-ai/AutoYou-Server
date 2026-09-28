# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-425a59663352447455546d73-3dfc75d7ea49766bd05668d5

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-425a59663352447455546d73-3dfc75d7ea49766bd05668d5"


import base64
import hashlib
import logging
import os
import html
import json
import mimetypes
import re
from email.utils import formatdate
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import FastAPI, Request
try:
    from fastapi.middleware.gzip import GZipMiddleware
except ImportError:
    class GZipMiddleware:  # no-op when not available in compiled build
        def __init__(self, app, **kwargs):
            self.app = app
        async def __call__(self, scope, receive, send):
            await self.app(scope, receive, send)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from autoyou_agents.notes_agent.notes_tool import NotesTool
from autoyou_agents.shared_tools.agent_install_registry import load_agent_install_registry
from autoyou_agents.shared_tools.scheduler_mission_control import install_agent_website_auth
from shared.media_messaging import is_adts_aac
from shared.secure_storage import read_secure_file


LOGGER = logging.getLogger("autoyou.notes_website")

APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
INDEX_HTML_PATH = FRONTEND_DIR / "index.html"
DEFAULT_UI_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100
DEFAULT_MEDIA_RANGE_WINDOW_BYTES = 1024 * 1024
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}
UNINSTALLED_MESSAGE = (
    "notes_agent is currently uninstalled. Install it and restart the AI agent runtime "
    "to browse notes here."
)
STORAGE_UNAVAILABLE_MESSAGE = "Notes storage is unavailable on this server."
MAX_CATEGORY_FILTERS = 12

# Each category keeps one colour and icon everywhere it appears. The accent and
# icon names are CSS classes in frontend/styles.css.
_CATEGORY_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("purple", "doc", ("personal", "life", "self", "me", "family", "home")),
    ("blue", "briefcase", ("work", "job", "career", "business", "office", "meeting", "meetings",
                           "project", "projects", "clients")),
    ("green", "leaf", ("health", "fitness", "wellness", "wellbeing", "exercise", "workout", "workouts",
                       "medical", "sleep", "diet", "nutrition")),
    ("amber", "bulb", ("idea", "ideas", "inspiration", "brainstorm", "brainstorming", "creative")),
    ("teal", "check", ("task", "tasks", "todo", "todos", "to-do", "to do", "checklist", "errands")),
    ("rose", "heart", ("journal", "diary", "reflection", "reflections", "gratitude", "relationships")),
    ("purple", "book", ("learning", "study", "school", "education", "research", "reading", "books")),
    ("rose", "cart", ("shopping", "groceries", "grocery", "wishlist")),
    ("green", "card", ("finance", "finances", "money", "budget", "bills", "expenses")),
    ("teal", "pin", ("travel", "trip", "trips", "vacation", "places")),
    ("purple", "mic", ("voice", "voice note", "voice notes", "audio", "recording", "recordings")),
)
_CATEGORY_APPEARANCE: dict[str, tuple[str, str]] = {
    name: (accent, icon) for accent, icon, names in _CATEGORY_GROUPS for name in names
}
_FALLBACK_ACCENTS = ("blue", "purple", "green", "amber", "teal", "rose")

_notes_tool: Optional[NotesTool] = None
_notes_tool_error: Optional[str] = None


def _json_response(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    return response


def _media_type_for_attachment(filename: str, mimetype: Optional[str]) -> str:
    stored_type = str(mimetype or "").split(";", 1)[0].strip().lower()
    if stored_type in {"audio/x-m4a", "audio/m4a", "audio/mp4a-latm"}:
        return "audio/mp4"
    if stored_type:
        return stored_type
    guessed_type, _ = mimetypes.guess_type(filename or "")
    if guessed_type in {"audio/x-m4a", "audio/m4a"}:
        return "audio/mp4"
    return guessed_type or "application/octet-stream"


def _inline_content_disposition(filename: str) -> str:
    display_name = str(filename or "attachment").replace("\r", "_").replace("\n", "_")
    quoted_name = display_name.replace("\\", "\\\\").replace('"', r"\"")
    return f"inline; filename=\"{quoted_name}\"; filename*=UTF-8''{quote(display_name)}"


def _parse_range_header(range_header: Optional[str], file_size: int) -> Optional[tuple[int, int]]:
    header = str(range_header or "").strip()
    if not header:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or file_size <= 0:
        raise ValueError("Invalid range")
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise ValueError("Invalid range")
    if start_text:
        start = int(start_text)
        end = int(end_text) if end_text else file_size - 1
    else:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            raise ValueError("Invalid range")
        start = max(file_size - suffix_length, 0)
        end = file_size - 1
    if start >= file_size or end < start:
        raise ValueError("Unsatisfiable range")
    return (start, min(end, file_size - 1))


def _media_range_window_bytes() -> int:
    raw_value = str(
        os.getenv(
            "AUTOYOU_NOTES_MEDIA_RANGE_WINDOW_BYTES",
            str(DEFAULT_MEDIA_RANGE_WINDOW_BYTES),
        )
        or ""
    ).strip()
    try:
        parsed = int(raw_value)
    except Exception:
        parsed = DEFAULT_MEDIA_RANGE_WINDOW_BYTES
    return max(64 * 1024, parsed)


def _bounded_media_range(start: int, end: int, file_size: int) -> tuple[int, int]:
    if file_size <= 0:
        return (0, -1)
    window = _media_range_window_bytes()
    bounded_end = min(file_size - 1, int(start) + window - 1, int(end))
    return (int(start), max(int(start), bounded_end))


def _iter_file_range(file_path: Path, start: int, end: int, data: Optional[bytes] = None):
    if end < start:
        return
    data = data if data is not None else read_secure_file(file_path)
    position = max(0, int(start))
    end_position = min(len(data) - 1, int(end))
    while position <= end_position:
        next_position = min(end_position + 1, position + 64 * 1024)
        yield data[position:next_position]
        position = next_position


def get_notes_tool() -> Optional[NotesTool]:
    global _notes_tool, _notes_tool_error
    if _notes_tool is not None:
        return _notes_tool
    try:
        _notes_tool = NotesTool()
        _notes_tool_error = None
    except Exception as exc:
        # Keep the reason: a sealed database, a read-only path and a missing
        # directory all reach here, and "unavailable" alone tells the user
        # nothing about which one they are looking at or how to fix it.
        _notes_tool_error = str(exc).strip() or exc.__class__.__name__
        LOGGER.error("Notes storage could not be opened: %s", exc)
        _notes_tool = None
    return _notes_tool


def storage_unavailable_error() -> str:
    """Explain why notes storage could not be opened."""
    return _notes_tool_error or STORAGE_UNAVAILABLE_MESSAGE


def notes_agent_installed() -> bool:
    return "notes_agent" in set(load_agent_install_registry().get("installed_agents", []))


def _build_preview(content: str) -> str:
    compact_preview = re.sub(r"\s+", " ", str(content or "")).strip()
    return compact_preview[:220] + ("..." if len(compact_preview) > 220 else "")


def _format_timestamp_label(value: Any) -> str:
    if not value:
        return "Unknown"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.strftime("%b %d, %I:%M %p").replace(" 0", " ")
    except Exception:
        return str(value)


def _parse_local_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    # Notes store server-local times; convert anything zoned to match them.
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _format_relative_day(value: Any, now: Optional[datetime] = None) -> str:
    """Match app.js formatRelativeDay so the first paint does not change."""
    parsed = _parse_local_timestamp(value)
    if parsed is None:
        return str(value or "")
    today = (now or datetime.now()).date()
    day = parsed.date()
    if day == today:
        return "Today"
    if (today - day).days == 1:
        return "Yesterday"
    label = f"{parsed.strftime('%b')} {day.day}"
    return label if day.year == today.year else f"{label}, {day.year}"


def category_appearance(category: Any) -> tuple[str, str]:
    """Return the (accent, icon) pair the website uses for a category."""
    key = re.sub(r"\s+", " ", str(category or "").strip().lower())
    if not key:
        return ("slate", "doc")
    known = _CATEGORY_APPEARANCE.get(key)
    if known:
        return known
    # A stable digest, unlike hash(), gives a category the same accent in
    # every process and after every restart.
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return (_FALLBACK_ACCENTS[digest[0] % len(_FALLBACK_ACCENTS)], "doc")


def _split_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[,#\n]+", str(value or ""))
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        cleaned = str(item or "").strip()
        if cleaned.startswith("#"):
            cleaned = cleaned[1:].strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        tags.append(cleaned)
    return tags


def _encode_cursor(updated_at: Optional[str], note_id: Optional[int]) -> Optional[str]:
    if not updated_at or note_id is None:
        return None
    raw = f"{updated_at}\n{int(note_id)}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    raw_cursor = str(cursor or "").strip()
    if not raw_cursor:
        return (None, None)
    padding = "=" * (-len(raw_cursor) % 4)
    try:
        decoded = base64.urlsafe_b64decode(raw_cursor + padding).decode("utf-8")
        updated_at, note_id = decoded.split("\n", 1)
        return (updated_at or None, int(note_id))
    except Exception:
        return (None, None)


def normalize_note_payload(
    note: Dict[str, Any],
    *,
    include_content: bool = True,
    include_metadata: bool = True,
) -> Dict[str, Any]:
    note_id = note.get("id")
    try:
        note_id = int(note_id)
    except (TypeError, ValueError):
        note_id = None

    content = str(note.get("content") or "")
    title = str(note.get("title") or "Untitled note").strip() or "Untitled note"
    category = str(note.get("category") or "").strip() or None
    tags = note.get("tags")
    metadata = note.get("metadata")
    accent, icon = category_appearance(category)

    payload = {
        "id": note_id,
        "title": title,
        "preview": _build_preview(content),
        "tags": list(tags) if isinstance(tags, list) else [],
        "category": category,
        "accent": accent,
        "icon": icon,
        "created_at": note.get("created_at"),
        "updated_at": note.get("updated_at"),
    }
    if include_content:
        payload["content"] = content
    if include_metadata:
        payload["metadata"] = metadata if isinstance(metadata, dict) else {}
    return payload


def _render_note_list_markup(notes: list[Dict[str, Any]], error: Optional[str] = None) -> str:
    """First-paint markup; app.js renderList() produces the same structure."""
    if error:
        return f'<div class="error-card">{html.escape(str(error))}</div>'
    if not notes:
        return (
            '<div class="empty-state"><span class="note-icon accent-slate" aria-hidden="true">'
            '<i class="ic ic-doc"></i></span><p class="empty-title">No notes yet</p>'
            '<p class="empty-copy">Tap + to write one, or ask AutoYou to save a note for you.</p></div>'
        )

    now = datetime.now()
    cards: list[str] = []
    for note in notes:
        note_id = note.get("id")
        title = html.escape(str(note.get("title") or "Untitled note"))
        preview = html.escape(str(note.get("preview") or "No content"))
        day = html.escape(_format_relative_day(note.get("updated_at") or note.get("created_at"), now))
        category = str(note.get("category") or "").strip()
        accent, icon = category_appearance(category)
        category_markup = (
            f'<span class="note-dot" aria-hidden="true">•</span><span class="note-category">{html.escape(category)}</span>'
            if category
            else ""
        )
        cards.append(
            f"""
            <article class="note-card accent-{accent}" data-note-id="{note_id}">
              <button class="note-item" type="button" data-action="open" data-note-id="{note_id}">
                <span class="note-icon" aria-hidden="true"><i class="ic ic-{icon}"></i></span>
                <span class="note-body">
                  <span class="note-title">{title}</span>
                  <span class="note-meta"><span class="note-time">{day}</span>{category_markup}</span>
                  <span class="note-preview">{preview}</span>
                </span>
                <i class="ic ic-chevron-right note-chevron" aria-hidden="true"></i>
              </button>
            </article>
            """.strip()
        )
    return "\n".join(cards)


def _render_category_filters_markup(categories: list[Dict[str, Any]], active: str = "") -> str:
    """First-paint filter chips; app.js renderCategoryFilters() matches them."""
    chips = [
        '<button class="filter-chip filter-all{}" type="button" data-category="" aria-pressed="{}">All</button>'.format(
            "" if active else " is-active",
            "false" if active else "true",
        )
    ]
    for item in categories[:MAX_CATEGORY_FILTERS]:
        name = str(item.get("name") or "")
        if not name:
            continue
        selected = name == active
        chips.append(
            '<button class="filter-chip accent-{}{}" type="button" data-category="{}" aria-pressed="{}">{}</button>'.format(
                item.get("accent") or category_appearance(name)[0],
                " is-active" if selected else "",
                html.escape(name),
                "true" if selected else "false",
                html.escape(name),
            )
        )
    return "".join(chips)


def list_category_filters(notes_tool: NotesTool) -> list[Dict[str, Any]]:
    categories = []
    for item in notes_tool.list_categories(limit=MAX_CATEGORY_FILTERS):
        accent, icon = category_appearance(item.get("name"))
        categories.append({**item, "accent": accent, "icon": icon})
    return categories


def load_notes_listing_payload(
    *,
    limit: int = DEFAULT_UI_LIST_LIMIT,
    cursor: Optional[str] = None,
    query: Optional[str] = None,
    category: Optional[str] = None,
    order: Optional[str] = None,
    include_content: bool = False,
    include_metadata: bool = False,
) -> Dict[str, Any]:
    if not notes_agent_installed():
        return {"success": False, "error": UNINSTALLED_MESSAGE, "notes": []}

    notes_tool = get_notes_tool()
    if notes_tool is None:
        return {
            "success": False,
            "error": storage_unavailable_error(),
            "notes": [],
        }

    bounded_limit = max(1, min(int(limit or DEFAULT_UI_LIST_LIMIT), MAX_LIST_LIMIT))
    cursor_updated_at, cursor_note_id = _decode_cursor(cursor)
    selected_category = str(category or "").strip() or None
    normalized_order = "oldest" if str(order or "").strip().lower() == "oldest" else "newest"
    notes = notes_tool.list_notes(
        limit=bounded_limit + 1,
        include_content=include_content,
        include_metadata=include_metadata,
        query=str(query or "").strip() or None,
        category=selected_category,
        cursor_updated_at=cursor_updated_at,
        cursor_note_id=cursor_note_id,
        descending=normalized_order == "newest",
    )

    has_more = len(notes) > bounded_limit
    visible_notes = notes[:bounded_limit]
    items = [
        normalize_note_payload(
            note,
            include_content=include_content,
            include_metadata=include_metadata,
        )
        for note in visible_notes
        if note.get("id") is not None
    ]
    last_item = items[-1] if items else None
    next_cursor = (
        _encode_cursor(
            str(last_item.get("updated_at") or last_item.get("created_at") or ""),
            int(last_item.get("id")) if last_item and last_item.get("id") is not None else None,
        )
        if has_more and last_item
        else None
    )

    payload = {
        "success": True,
        "notes": items,
        "returned_count": len(items),
        "total_count": notes_tool.count_notes(
            query=str(query or "").strip() or None,
            category=selected_category,
        ),
        "limit": bounded_limit,
        "cursor": cursor or None,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "query": str(query or "").strip(),
        "category": selected_category or "",
        "order": normalized_order,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if not cursor:
        # Filter chips cover every note, not just this page or search.
        payload["categories"] = list_category_filters(notes_tool)
    return payload


def load_note_detail_payload(note_id: int) -> Dict[str, Any]:
    if not notes_agent_installed():
        return {"success": False, "error": UNINSTALLED_MESSAGE}

    notes_tool = get_notes_tool()
    if notes_tool is None:
        return {"success": False, "error": storage_unavailable_error()}

    note = notes_tool.get_note(int(note_id))
    if not note:
        return {"success": False, "error": f"Note {note_id} was not found."}
    return {"success": True, "note": normalize_note_payload(note, include_content=True, include_metadata=True)}


def update_note_payload(note_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not notes_agent_installed():
        return {"success": False, "error": UNINSTALLED_MESSAGE}

    notes_tool = get_notes_tool()
    if notes_tool is None:
        return {"success": False, "error": storage_unavailable_error()}

    current_note = notes_tool.get_note(int(note_id))
    if not current_note:
        return {"success": False, "error": f"Note {note_id} was not found."}

    fields_present = any(key in payload for key in ("title", "content", "category", "tags"))
    if not fields_present:
        return {"success": False, "error": "No editable fields were provided."}

    title = payload.get("title")
    content = payload.get("content")
    category = payload.get("category")
    tags = payload.get("tags") if "tags" in payload else None

    success = notes_tool.update_note(
        note_id=int(note_id),
        title=(str(title).strip() or "Untitled note") if title is not None else None,
        content=str(content or "") if content is not None else None,
        category=str(category or "").strip() if category is not None else None,
        tags=_split_tags(tags) if "tags" in payload else None,
    )
    if not success:
        return {"success": False, "error": "No changes were saved."}

    updated_note = notes_tool.get_note(int(note_id))
    return {
        "success": True,
        "note": normalize_note_payload(updated_note or current_note, include_content=True, include_metadata=True),
    }


def create_note_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not notes_agent_installed():
        return {"success": False, "error": UNINSTALLED_MESSAGE}

    notes_tool = get_notes_tool()
    if notes_tool is None:
        return {"success": False, "error": storage_unavailable_error()}

    title = str(payload.get("title") or "").strip() or "Untitled note"
    content = str(payload.get("content") or "")
    category = str(payload.get("category") or "").strip() or None
    tags_raw = payload.get("tags")
    tags = _split_tags(tags_raw) if "tags" in payload else []

    result = notes_tool.create_note(title=title, content=content, tags=tags, category=category)
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "Failed to create note.")}

    note_id = result.get("note_id")
    note = notes_tool.get_note(int(note_id)) if note_id else None
    if not note:
        return {"success": True, "note_id": note_id}
    return {"success": True, "note": normalize_note_payload(note, include_content=True, include_metadata=True)}


def delete_note_payload(note_id: int) -> Dict[str, Any]:
    if not notes_agent_installed():
        return {"success": False, "error": UNINSTALLED_MESSAGE}

    notes_tool = get_notes_tool()
    if notes_tool is None:
        return {"success": False, "error": storage_unavailable_error()}

    deleted = notes_tool.delete_note(int(note_id))
    if not deleted:
        return {"success": False, "error": f"Note {note_id} was not found."}
    return {"success": True, "deleted_note_id": int(note_id)}


app = FastAPI(title="AutoYou Notes")
install_agent_website_auth(
    app,
    agent_name="notes_agent",
    title="AutoYou Notes",
    register_auth_routes=True,
)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")


def _serialize_inline_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload).replace("</", "<\\/")


def render_index_html() -> str:
    template = INDEX_HTML_PATH.read_text(encoding="utf-8")
    initial_payload = load_notes_listing_payload(
        limit=DEFAULT_UI_LIST_LIMIT,
        include_content=False,
        include_metadata=False,
    )
    loaded = bool(initial_payload.get("success"))
    notes = initial_payload.get("notes", []) if loaded else []
    total_count = int(initial_payload.get("total_count") or len(notes) or 0)
    status_text = (
        f"Showing {len(notes)} of {total_count} notes."
        if loaded
        else str(initial_payload.get("error") or "Notes unavailable.")
    )
    sync_title = (
        f"Updated {_format_timestamp_label(initial_payload.get('generated_at'))}"
        if loaded
        else status_text
    )
    html_output = template.replace("__INITIAL_NOTES_BOOTSTRAP_JSON__", _serialize_inline_json(initial_payload))
    html_output = html_output.replace("__INITIAL_NOTES_COUNT__", str(total_count))
    html_output = html_output.replace("__INITIAL_NOTES_STATUS__", html.escape(status_text))
    html_output = html_output.replace("__INITIAL_SYNC_STATE__", "synced" if loaded else "error")
    html_output = html_output.replace("__INITIAL_SYNC_LABEL__", "Synced" if loaded else "Not synced")
    html_output = html_output.replace("__INITIAL_SYNC_TITLE__", html.escape(sync_title))
    html_output = html_output.replace(
        "__INITIAL_CATEGORY_FILTERS_HTML__",
        _render_category_filters_markup(initial_payload.get("categories") or []),
    )
    html_output = html_output.replace(
        "__INITIAL_NOTE_LIST_HTML__",
        _render_note_list_markup(notes, None if loaded else initial_payload.get("error")),
    )
    return html_output


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "agent_name": "notes_agent",
        "proxy_path": "/agent/notes_agent/",
        "installed": notes_agent_installed(),
    }


@app.get("/api/notes")
def api_notes(
    limit: int = DEFAULT_UI_LIST_LIMIT,
    cursor: Optional[str] = None,
    query: Optional[str] = None,
    category: Optional[str] = None,
    order: Optional[str] = None,
    include_content: bool = False,
    include_metadata: bool = False,
) -> JSONResponse:
    payload = load_notes_listing_payload(
        limit=limit,
        cursor=cursor,
        query=query,
        category=category,
        order=order,
        include_content=include_content,
        include_metadata=include_metadata,
    )
    status_code = 200 if payload.get("success") else 409
    return _json_response(payload, status_code=status_code)


@app.get("/api/notes/{note_id}")
def api_note_detail(note_id: int) -> JSONResponse:
    payload = load_note_detail_payload(note_id)
    error_text = str(payload.get("error") or "").lower()
    if payload.get("success"):
        status_code = 200
    elif "uninstalled" in error_text:
        status_code = 409
    else:
        status_code = 404
    return _json_response(payload, status_code=status_code)


@app.post("/api/notes")
async def api_create_note(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    response_payload = create_note_payload(payload if isinstance(payload, dict) else {})
    status_code = 201 if response_payload.get("success") else 400
    error_text = str(response_payload.get("error") or "").lower()
    if "uninstalled" in error_text:
        status_code = 409
    return _json_response(response_payload, status_code=status_code)


@app.get("/api/media/{attachment_id}")
def api_serve_media(attachment_id: int, request: Request) -> Any:
    notes_tool = get_notes_tool()
    if notes_tool is None:
        return _json_response({"success": False, "error": "Notes storage unavailable."}, status_code=503)
    try:
        import sqlite3 as _sqlite3
        with _sqlite3.connect(notes_tool._db_path) as conn:
            conn.row_factory = _sqlite3.Row
            row = conn.execute(
                "SELECT filename, mimetype, path, size_bytes FROM media_attachments WHERE id = ?",
                (int(attachment_id),),
            ).fetchone()
    except Exception as exc:
        return _json_response({"success": False, "error": str(exc)}, status_code=500)
    if not row:
        return _json_response({"success": False, "error": "Attachment not found."}, status_code=404)
    file_path = Path(str(row["path"] or ""))
    if not file_path.is_file():
        return _json_response({"success": False, "error": "File not found on disk."}, status_code=404)
    stat_result = file_path.stat()
    file_data = read_secure_file(file_path)
    file_size = len(file_data)
    filename = str(row["filename"] or file_path.name)
    media_type = "audio/aac" if is_adts_aac(file_data) else _media_type_for_attachment(filename, row["mimetype"])
    etag = f'W/"{file_size:x}-{int(stat_result.st_mtime_ns):x}"'
    base_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
        "Content-Disposition": _inline_content_disposition(filename),
        "Content-Encoding": "identity",
        "ETag": etag,
        "Last-Modified": formatdate(stat_result.st_mtime, usegmt=True),
    }
    try:
        requested_range = _parse_range_header(request.headers.get("range"), file_size)
    except ValueError:
        return Response(
            status_code=416,
            headers={
                **base_headers,
                "Content-Range": f"bytes */{file_size}",
                "Content-Length": "0",
            },
        )

    if requested_range is not None:
        start, end = _bounded_media_range(*requested_range, file_size)
        content_length = end - start + 1
        return StreamingResponse(
            _iter_file_range(file_path, start, end, file_data),
            status_code=206,
            media_type=media_type,
            headers={
                **base_headers,
                "Content-Length": str(content_length),
                "Content-Range": f"bytes {start}-{end}/{file_size}",
            },
        )

    return StreamingResponse(
        _iter_file_range(file_path, 0, file_size - 1, file_data),
        media_type=media_type,
        headers={
            **base_headers,
            "Content-Length": str(file_size),
        },
    )


@app.patch("/api/notes/{note_id}")
async def api_update_note(note_id: int, request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    response_payload = update_note_payload(note_id, payload if isinstance(payload, dict) else {})
    status_code = 200 if response_payload.get("success") else 400
    error_text = str(response_payload.get("error") or "").lower()
    if "not found" in error_text:
        status_code = 404
    elif "uninstalled" in error_text:
        status_code = 409
    return _json_response(response_payload, status_code=status_code)


@app.post("/api/notes/{note_id}/update")
async def api_update_note_post(note_id: int, request: Request) -> JSONResponse:
    return await api_update_note(note_id, request)


@app.delete("/api/notes/{note_id}")
def api_delete_note(note_id: int) -> JSONResponse:
    payload = delete_note_payload(note_id)
    status_code = 200 if payload.get("success") else 404
    error_text = str(payload.get("error") or "").lower()
    if "uninstalled" in error_text:
        status_code = 409
    return _json_response(payload, status_code=status_code)


@app.post("/api/notes/{note_id}/delete")
def api_delete_note_post(note_id: int) -> JSONResponse:
    return api_delete_note(note_id)


@app.get("/{full_path:path}")
def serve_frontend(full_path: str):
    candidate = FRONTEND_DIR / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate)
    response = HTMLResponse(render_index_html())
    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    return response
