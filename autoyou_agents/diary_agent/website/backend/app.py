"""Private Diary website, catalog, Day One workspace APIs and range-aware attachment endpoints."""
from hashlib import sha256
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional
import asyncio
import base64
import json
import re
import threading
import time

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from autoyou_agents.diary_agent.agent import (
    _journal_lock,
    compute_diary_insights,
    delete_diary_entry,
    delete_diary_media,
    delete_journal,
    delete_template,
    diary_media_store,
    export_diary,
    get_diary_entry,
    get_diary_sync_state,
    get_preferences,
    list_diary_entries,
    list_journals,
    list_templates,
    publish_diary_entry,
    save_preferences,
    save_template,
    upsert_journal,
)
from autoyou_agents.shared_tools.agent_install_registry import load_agent_install_registry
from autoyou_agents.shared_tools.scheduler_mission_control import _describe_auth_state, install_agent_website_auth
from shared import content_access
from shared.media_messaging import is_adts_aac

_AGENT_NAME = "diary_agent"
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
NO_CACHE_HEADERS = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
app = FastAPI(title="AutoYou Diary", docs_url=None, redoc_url=None)


def _check_auth(request):
    return bool(_describe_auth_state(request, _AGENT_NAME).get("authenticated"))


def _json_response(payload, status_code=200):
    return JSONResponse(payload, status_code=status_code, headers=NO_CACHE_HEADERS)


def _auth_error():
    return _json_response({"success": False, "error": "Authentication required"}, 401)


install_agent_website_auth(app, agent_name=_AGENT_NAME, title="Diary", register_auth_routes=True,
                           is_authenticated=lambda request: _check_auth(request))
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")


@app.middleware("http")
async def agent_prefix(request: Request, call_next):
    prefix = "/agent/diary_agent"
    path = request.scope["path"]
    if path == prefix or path.startswith(prefix + "/"):
        request.scope["path"] = path[len(prefix):] or "/"
    response = await call_next(request)
    if request.scope["path"].startswith("/assets/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.get("/health")
def health():
    return {"status": "ok", "agent_name": _AGENT_NAME, "installed": _AGENT_NAME in load_agent_install_registry()["installed_agents"]}


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/api/diary/workspace")
def api_diary_workspace(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    entries = list_diary_entries(order="desc")
    journals = list_journals(entries=entries)
    templates = list_templates()
    preferences = get_preferences()
    insights = compute_diary_insights(entries, journals=journals)
    sync_state = get_diary_sync_state()
    perms = content_access.abilities(request)
    return _json_response({
        "success": True,
        "journals": journals,
        "templates": templates,
        "preferences": preferences,
        "insights": insights,
        "sync": sync_state,
        "permissions": {
            "role": perms.get("role", "owner"),
            "can_edit": bool(perms.get("edit", True)),
            "can_delete": bool(perms.get("delete", True)),
        },
    })


@app.get("/api/diary/sync/state")
def api_diary_sync_state(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, **get_diary_sync_state()})


@app.get("/api/diary/journals")
def api_list_journals(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, "journals": list_journals()})


@app.post("/api/diary/journals")
async def api_upsert_journal(request: Request) -> JSONResponse:
    denied = content_access.deny(request, "edit")
    if denied is not None:
        return denied
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected a journal object.")
        record = upsert_journal(
            payload.get("name", ""),
            color=payload.get("color", ""),
            icon=payload.get("icon", "book"),
            description=payload.get("description", ""),
            streak_included=bool(payload.get("streak_included", True)),
            rename_from=payload.get("rename_from"),
        )
        return _json_response({"success": True, "journal": record, "journals": list_journals()})
    except (ValueError, TypeError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)


@app.delete("/api/diary/journals/{journal_name}")
def api_delete_journal(journal_name: str, request: Request, move_to: str = "") -> JSONResponse:
    denied = content_access.deny(request, "delete")
    if denied is not None:
        return denied
    if not _check_auth(request):
        return _auth_error()
    try:
        result = delete_journal(journal_name, move_entries_to=move_to)
        return _json_response({"success": True, **result, "journals": list_journals()})
    except (ValueError, TypeError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)


@app.get("/api/diary/templates")
def api_list_templates(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, "templates": list_templates()})


@app.post("/api/diary/templates")
async def api_save_template(request: Request) -> JSONResponse:
    denied = content_access.deny(request, "edit")
    if denied is not None:
        return denied
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
        record = save_template(payload)
        return _json_response({"success": True, "template": record, "templates": list_templates()})
    except (ValueError, TypeError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)


@app.delete("/api/diary/templates/{template_id}")
def api_delete_template(template_id: str, request: Request) -> JSONResponse:
    denied = content_access.deny(request, "delete")
    if denied is not None:
        return denied
    if not _check_auth(request):
        return _auth_error()
    try:
        result = delete_template(template_id)
        return _json_response({"success": True, **result, "templates": list_templates()})
    except (ValueError, TypeError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)


@app.get("/api/diary/preferences")
def api_get_preferences(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, "preferences": get_preferences()})


@app.put("/api/diary/preferences")
async def api_save_preferences(request: Request) -> JSONResponse:
    denied = content_access.deny(request, "edit")
    if denied is not None:
        return denied
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
        prefs = save_preferences(payload)
        return _json_response({"success": True, "preferences": prefs})
    except (ValueError, TypeError) as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)


@app.get("/api/diary/export")
def api_export_diary(request: Request, format: str = "dayone_json", journal: str = "") -> Response:
    if not _check_auth(request):
        return _auth_error()
    try:
        content, media_type, filename = export_diary(fmt=format, journal=journal)
    except ValueError as exc:
        return _json_response({"success": False, "error": str(exc)}, 400)
    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={
            **NO_CACHE_HEADERS,
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.get("/api/diary/entries")
def api_list_diary_entries(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    order = str(request.query_params.get("order") or "desc")
    entries = list_diary_entries(order=order)
    total = len(entries)
    limit_param = request.query_params.get("limit")
    offset_param = request.query_params.get("offset")
    if limit_param is not None or offset_param is not None:
        try:
            offset_val = max(0, int(offset_param)) if offset_param is not None else 0
        except ValueError:
            offset_val = 0
        try:
            limit_val = max(1, min(int(limit_param), 500)) if limit_param is not None else 100
        except ValueError:
            limit_val = 100
        page_entries = entries[offset_val:offset_val + limit_val]
        next_off = offset_val + len(page_entries)
        has_more = next_off < total
        return _json_response(
            {
                "success": True,
                "entries": page_entries,
                "total": total,
                "offset": offset_val,
                "limit": limit_val,
                "has_more": has_more,
                "next_offset": next_off if has_more else None,
            }
        )
    return _json_response(
        {
            "success": True,
            "entries": entries,
            "total": total,
            "offset": 0,
            "has_more": False,
            "next_offset": None,
        }
    )


@app.get("/api/diary/entries/{entry_id}")
def api_get_diary_entry(entry_id: str, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    try:
        entry = get_diary_entry(entry_id)
    except Exception:
        entry = None
    if not entry:
        return _json_response({"success": False, "error": "Diary entry not found."}, status_code=404)
    return _json_response({"success": True, "entry": entry})


@app.post("/api/diary/entries")
async def api_publish_diary_entry(request: Request) -> JSONResponse:
    denied = content_access.deny(request, "edit")
    if denied is not None:
        return denied
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected a diary entry object.")
        result = publish_diary_entry(payload.get("id", ""), payload.get("markdown", ""), payload.get("base_revision"), payload.get("details"))
    except (ValueError, TypeError):
        result = {"status": "error", "message": "Invalid diary entry."}
    if result.get("status") == "conflict":
        return _json_response({"success": False, "error": "This entry changed. Reload to see the latest version."}, 409)
    success = result.get("status") == "success"
    return _json_response({"success": success, **result}, status_code=200 if success else 400)


@app.delete("/api/diary/entries/{entry_id}")
def remove_entry(entry_id: str, base_revision: str, request: Request):
    denied = content_access.deny(request, "delete")
    if denied is not None:
        return denied
    if not _check_auth(request):
        return _auth_error()
    try:
        result = delete_diary_entry(entry_id, base_revision)
    except ValueError:
        return _json_response({"error": "Invalid diary entry ID"}, 400)
    if result["status"] == "conflict":
        return _json_response({"error": "This entry changed. Reload to see the latest version."}, 409)
    return _json_response({"success": True, **result})


@app.delete("/api/diary/entries/{entry_id}/media/{media_id}")
def api_delete_diary_media(entry_id: str, media_id: str, request: Request) -> JSONResponse:
    denied = content_access.deny(request, "edit")
    if denied is not None:
        return denied
    if not _check_auth(request):
        return _auth_error()
    try:
        result = delete_diary_media(entry_id, media_id)
        _evict_diary_media_cache(entry_id, media_id)
        return _json_response({"success": True, **result})
    except ValueError:
        return _json_response({"success": False, "error": "Invalid entry or media id."}, 400)


@app.post("/api/diary/entries/{entry_id}/media")
async def api_publish_diary_media(entry_id: str, request: Request, file: UploadFile = File(...)) -> JSONResponse:
    denied = content_access.deny(request, "edit")
    if denied is not None:
        return denied
    if not _check_auth(request):
        return _auth_error()
    media_id = request.headers.get("X-AutoYou-Media-ID", "")
    try:
        store = diary_media_store(entry_id, media_id)
    except ValueError:
        return _json_response({"success": False, "error": "Invalid entry or media id."}, status_code=400)
    data = await file.read(64 * 1024 * 1024 + 1)
    if not data:
        return _json_response({"success": False, "error": "The attachment is empty. Choose a non-empty file."}, status_code=400)
    if len(data) > 64 * 1024 * 1024:
        return _json_response({"success": False, "error": "Attachments can be up to 64 MiB. Use Media for larger photos or videos."}, status_code=413)
    mime = file.content_type or "application/octet-stream"
    if not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", mime):
        return _json_response({"success": False, "error": "Invalid media type."}, status_code=400)
    try:
        checksum = sha256(data).hexdigest()

        def save():
            with _journal_lock():
                if store.exists():
                    previous = json.loads(store.read())
                    held = previous.get("sha256") or sha256(base64.b64decode(previous["data"], validate=True)).hexdigest()
                    return checksum == held and mime == previous["mime"]
                store.write(json.dumps({"mime": mime, "sha256": checksum, "data": base64.b64encode(data).decode("ascii")}))
                return True

        if not await asyncio.to_thread(save):
            return _json_response({"error": "That attachment ID already holds a different file."}, 409)
        _evict_diary_media_cache(entry_id, media_id)
    except Exception:
        return _json_response({"success": False, "error": "Could not protect and save the attachment."}, status_code=500)
    return _json_response({"success": True, "sha256": checksum, "size": len(data), "url": f"/agent/diary_agent/api/diary/entries/{entry_id}/media/{media_id}"})


_DIARY_AUDIO_ALIASES = {"audio/x-m4a": "audio/mp4", "audio/m4a": "audio/mp4", "audio/mp4a-latm": "audio/mp4"}
# Only formats a browser plays without running page script are shown inline; SVG and the rest download.
_DIARY_INLINE_IMAGES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic", "image/heif", "image/avif"}


def _diary_media_type(stored_mime: Any, data: bytes) -> str:
    """The type a browser should play: raw ADTS AAC from a phone is audio/aac whatever the app labelled it."""
    if is_adts_aac(data[:64]):
        return "audio/aac"
    mime = str(stored_mime or "").split(";", 1)[0].strip().lower() or "application/octet-stream"
    return _DIARY_AUDIO_ALIASES.get(mime, mime)


def _diary_media_inline(media_type: str) -> bool:
    return media_type.startswith(("audio/", "video/")) or media_type in _DIARY_INLINE_IMAGES


def _diary_byte_range(header: Any, size: int) -> tuple[int, int] | None:
    """The satisfiable single range asked for, None without a Range header; ValueError when it cannot be met."""
    text = str(header or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", text)
    if not match or size <= 0 or not (match.group(1) or match.group(2)):
        raise ValueError("Invalid range")
    first, last = match.groups()
    if first:
        start, end = int(first), int(last) if last else size - 1
    else:
        count = int(last)
        if count <= 0:
            raise ValueError("Invalid range")
        start, end = max(size - count, 0), size - 1
    if start >= size or end < start:
        raise ValueError("Unsatisfiable range")
    return start, min(end, size - 1)


_DIARY_MEDIA_CACHE_SECONDS = 60.0
_DIARY_MEDIA_CACHE_MAX_BYTES = 64 * 1024 * 1024
_DIARY_MEDIA_CACHE_MAX_ITEMS = 8
_DIARY_MEDIA_CACHE: "OrderedDict[tuple[str, str], tuple[Optional[tuple[int, int]], float, str, bytes]]" = OrderedDict()
_DIARY_MEDIA_CACHE_LOCK = threading.Lock()


def _evict_diary_media_cache(entry_id: str, media_id: str) -> None:
    with _DIARY_MEDIA_CACHE_LOCK:
        _DIARY_MEDIA_CACHE.pop((entry_id, media_id), None)
        _DIARY_MEDIA_CACHE.pop((entry_id.lower(), media_id), None)


def _diary_store_signature(store: Any) -> Optional[tuple[int, int]]:
    store_path = getattr(store, "path", None)
    if store_path is None:
        return None
    try:
        st = Path(store_path).stat()
        return int(st.st_mtime_ns), int(st.st_size)
    except OSError:
        return None


def _load_decoded_diary_media(entry_id: str, media_id: str) -> Optional[tuple[str, bytes]]:
    store = diary_media_store(entry_id, media_id)
    cache_key = (entry_id.lower(), media_id)
    sig = _diary_store_signature(store)
    now = time.monotonic()
    if sig is not None:
        with _DIARY_MEDIA_CACHE_LOCK:
            expired = [k for k, entry in _DIARY_MEDIA_CACHE.items() if now - entry[1] > _DIARY_MEDIA_CACHE_SECONDS]
            for k in expired:
                _DIARY_MEDIA_CACHE.pop(k, None)
            hit = _DIARY_MEDIA_CACHE.get(cache_key)
            if hit is not None and hit[0] == sig:
                _, _, cached_mime, cached_data = hit
                _DIARY_MEDIA_CACHE[cache_key] = (sig, now, cached_mime, cached_data)
                _DIARY_MEDIA_CACHE.move_to_end(cache_key)
                return cached_mime, cached_data

    stored = store.read()
    if stored is None:
        return None
    content = json.loads(stored)
    data = base64.b64decode(content["data"], validate=True)
    media_type = _diary_media_type(content.get("mime"), data)
    sig_after = _diary_store_signature(store)
    if sig_after is not None and len(data) <= _DIARY_MEDIA_CACHE_MAX_BYTES:
        now_after = time.monotonic()
        with _DIARY_MEDIA_CACHE_LOCK:
            _DIARY_MEDIA_CACHE[cache_key] = (sig_after, now_after, media_type, data)
            _DIARY_MEDIA_CACHE.move_to_end(cache_key)
            while (
                len(_DIARY_MEDIA_CACHE) > _DIARY_MEDIA_CACHE_MAX_ITEMS
                or sum(len(entry[3]) for entry in _DIARY_MEDIA_CACHE.values()) > _DIARY_MEDIA_CACHE_MAX_BYTES
            ):
                _DIARY_MEDIA_CACHE.popitem(last=False)
    return media_type, data


def _iter_memory_slice(data: bytes, start: int, end: int, chunk: int = 65536):
    position = max(0, int(start))
    end_position = min(len(data) - 1, int(end))
    while position <= end_position:
        next_position = min(end_position + 1, position + chunk)
        yield data[position:next_position]
        position = next_position


@app.get("/api/diary/entries/{entry_id}/media/{media_id}")
async def api_read_diary_media(entry_id: str, media_id: str, request: Request) -> Response:
    if not _check_auth(request):
        return _auth_error()
    try:
        loaded = await asyncio.to_thread(_load_decoded_diary_media, entry_id, media_id)
        if loaded is None:
            return _json_response({"success": False, "error": "Attachment not found."}, status_code=404)
        media_type, data = loaded
        headers = {
            **NO_CACHE_HEADERS,
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Content-Disposition": "inline" if _diary_media_inline(media_type) else "attachment",
            "Accept-Ranges": "bytes",
        }
        try:
            window = _diary_byte_range(request.headers.get("range"), len(data))
        except ValueError:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{len(data)}"})
        if window is None:
            return StreamingResponse(
                _iter_memory_slice(data, 0, len(data) - 1),
                media_type=media_type,
                headers={**headers, "Content-Length": str(len(data))},
            )
        start, end = window
        return StreamingResponse(
            _iter_memory_slice(data, start, end),
            status_code=206,
            media_type=media_type,
            headers={
                **headers,
                "Content-Range": f"bytes {start}-{end}/{len(data)}",
                "Content-Length": str(end - start + 1),
            },
        )
    except ValueError:
        return _json_response({"success": False, "error": "Invalid attachment."}, status_code=400)
    except Exception:
        return _json_response({"success": False, "error": "Could not open the protected attachment."}, status_code=500)


from shared.paired_content import install_collection_sync
install_collection_sync(app, "diary_agent")
