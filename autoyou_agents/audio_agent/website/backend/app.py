# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-534454206164647265737320-12277e461eb28a37d4f618a0

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-534454206164647265737320-12277e461eb28a37d4f618a0"


import asyncio
import hashlib
import mimetypes
import os
import random
import re
import struct
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterator, List, Optional, Tuple

import aiohttp
from fastapi import Body, FastAPI, Query, Request
try:
    from fastapi.middleware.gzip import GZipMiddleware
except ImportError:
    class GZipMiddleware:  # no-op when not available in compiled build
        def __init__(self, app, **kwargs):
            self.app = app
        async def __call__(self, scope, receive, send):
            await self.app(scope, receive, send)
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from shared.audio_agent_library import (
    load_audio_agent_library_settings,
    normalize_audio_agent_library_settings,
    resolve_audio_library_path_details,
    resolve_audio_library_roots,
    save_audio_agent_library_settings,
)
from shared.audio_playback_settings import MUSIC_LIBRARY_DIRS_ENV
from autoyou_agents.shared_tools.scheduler_mission_control import install_agent_website_auth

_AGENT_NAME = "audio_agent"
_SESSION_TTL_DAYS = 7


def _import_auth_helpers():
    """Lazy-import shared auth helpers to avoid startup failures if scheduler_mission_control
    is not available (e.g., during unit testing with mocked paths)."""
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import (  # type: ignore[import]
            _totp_capabilities,
            _create_agent_session,
            _agent_session_is_valid,
            _delete_agent_session,
            _agent_cookie_name,
            _agent_cookie_path,
            _describe_auth_state,
            _runtime_server,
            _set_agent_session_cookie,
        )
        return {
            "totp_capabilities": _totp_capabilities,
            "create_session": _create_agent_session,
            "session_valid": _agent_session_is_valid,
            "delete_session": _delete_agent_session,
            "cookie_name": _agent_cookie_name,
            "cookie_path": _agent_cookie_path,
            "describe_auth_state": _describe_auth_state,
            "runtime_server": _runtime_server,
            "set_session_cookie": _set_agent_session_cookie,
        }
    except Exception:
        return {}


def _check_auth(request: Request) -> bool:
    """Return True if the request carries a valid audio-agent session token."""
    from autoyou_agents.shared_tools.scheduler_mission_control import _global_agent_website_otp_disabled

    if _global_agent_website_otp_disabled():
        return True
    helpers = _import_auth_helpers()
    describe_auth_state = helpers.get("describe_auth_state")
    if describe_auth_state:
        try:
            auth = describe_auth_state(request, _AGENT_NAME)
            # The audio website is open by default, but its Settings drawer
            # remains an explicit OTP boundary. A real audio session or the
            # opt-in shared website session may unlock that drawer; the
            # manifest's public "open" state alone must not do so.
            if str(auth.get("auth_mode") or "").strip().lower() != "open":
                return bool(auth.get("authenticated"))
            if str(auth.get("via") or "").strip().lower() in {"mission_session", "shared_session"}:
                return bool(auth.get("authenticated"))
        except Exception:
            pass
    session_valid = helpers.get("session_valid")
    cookie_name_fn = helpers.get("cookie_name")
    if not session_valid or not cookie_name_fn:
        return False
    try:
        cookie_token = request.cookies.get(cookie_name_fn(_AGENT_NAME), "")
        if cookie_token and session_valid(_AGENT_NAME, cookie_token):
            return True
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            bearer = auth_header[7:].strip()
            if bearer and session_valid(_AGENT_NAME, bearer):
                return True
    except Exception:
        pass
    return False


APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
INDEX_HTML_PATH = FRONTEND_DIR / "index.html"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}
MAX_SCAN_TRACKS = 12000
DEFAULT_STREAM_RANGE_WINDOW_BYTES = 1024 * 1024
DEFAULT_STREAM_READ_CHUNK_BYTES = 64 * 1024
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
_AUDIO_CONTENT_TYPES = {
    ".aac": "audio/aac",
    ".aiff": "audio/aiff",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".wma": "audio/x-ms-wma",
}
_SKIP_DIRECTORY_NAMES = {
    "__pycache__",
    "node_modules",
    "runtime_modules",
    "runtime_site_packages",
    "runtime_stdlib",
    "site-packages",
    ".git",
    ".venv",
    "venv",
}

_artwork_cache_lock = Lock()
_artwork_cache: Dict[str, Optional[Tuple[bytes, str]]] = {}

_library_lock = Lock()
_library_cache: Dict[str, Any] = {
    "roots": [],
    "root_details": [],
    "tracks": [],
    "by_id": {},
    "limit": 0,
    "truncated": False,
    "scanned": False,
}

_library_settings = load_audio_agent_library_settings(anchor=__file__)

_settings_lock = Lock()
_settings: Dict[str, Any] = {
    "search_limit": 12000,
    "repeat_mode": "off",
    "shuffle_enabled": False,
    "auto_relay_on_play": False,
    "reply_target": {
        "session_id": "",
        "owner_key": "",
    },
    **_library_settings,
}


app = FastAPI(title="AutoYou Audio Player")
install_agent_website_auth(
    app,
    agent_name=_AGENT_NAME,
    title="AutoYou Audio Player",
    is_authenticated=lambda request: _check_auth(request),
)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")


def _json_response(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    return response


def _settings_auth_error(request: Request) -> Optional[JSONResponse]:
    if _check_auth(request):
        return None
    return _json_response(
        {
            "success": False,
            "error": "Audio settings require OTP authentication.",
        },
        status_code=401,
    )


def _sanitize_settings(payload: Dict[str, Any], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    current = dict(base or _settings)

    search_limit = payload.get("search_limit", current.get("search_limit", 12000))
    try:
        search_limit = int(search_limit)
    except Exception:
        search_limit = 12000
    search_limit = max(100, min(search_limit, MAX_SCAN_TRACKS))

    repeat_mode = str(payload.get("repeat_mode", current.get("repeat_mode", "off")) or "off").strip().lower()
    if repeat_mode not in {"off", "one", "all"}:
        repeat_mode = "off"

    reply_target_input = payload.get("reply_target") if isinstance(payload.get("reply_target"), dict) else {}
    existing_reply_target = current.get("reply_target") if isinstance(current.get("reply_target"), dict) else {}
    session_id = str(
        reply_target_input.get("session_id", existing_reply_target.get("session_id", "")) or ""
    ).strip()
    owner_key = str(
        reply_target_input.get("owner_key", existing_reply_target.get("owner_key", "")) or ""
    ).strip()

    library_settings = normalize_audio_agent_library_settings(
        payload if any(key in payload for key in ("audio_sources", "ad_hoc_paths")) else {},
        base=current,
    )

    return {
        "search_limit": search_limit,
        "repeat_mode": repeat_mode,
        "shuffle_enabled": bool(payload.get("shuffle_enabled", current.get("shuffle_enabled", False))),
        "auto_relay_on_play": bool(payload.get("auto_relay_on_play", current.get("auto_relay_on_play", False))),
        "reply_target": {
            "session_id": session_id,
            "owner_key": owner_key,
        },
        **library_settings,
    }


def _current_settings() -> Dict[str, Any]:
    with _settings_lock:
        return _sanitize_settings({}, base=_settings)


def _update_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    with _settings_lock:
        incoming = payload or {}
        library_settings_changed = any(key in incoming for key in ("audio_sources", "ad_hoc_paths"))
        merged = dict(_settings)
        merged.update(incoming)
        sanitized = _sanitize_settings(merged, base=_settings)
        if library_settings_changed:
            # Persist before publishing the in-memory change so a failed
            # write cannot leave this worker claiming settings that the next
            # worker would not load.
            save_audio_agent_library_settings(sanitized, anchor=__file__)
        _settings.clear()
        _settings.update(sanitized)
        if library_settings_changed:
            with _library_lock:
                _library_cache["scanned"] = False
                _library_cache["roots"] = []
                _library_cache["root_details"] = []
        return dict(sanitized)


def _find_track_index(tracks: List[Dict[str, Any]], track_id: str) -> int:
    normalized_id = str(track_id or "").strip().lower()
    if not normalized_id:
        return -1
    for index, track in enumerate(tracks):
        if str(track.get("id") or "").strip().lower() == normalized_id:
            return index
    return -1


def _next_repeat_mode(current_mode: str) -> str:
    order = ["off", "one", "all"]
    normalized = str(current_mode or "off").strip().lower()
    if normalized not in order:
        return "off"
    return order[(order.index(normalized) + 1) % len(order)]


def _reply_target_from_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    settings = _current_settings()
    fallback = settings.get("reply_target") if isinstance(settings.get("reply_target"), dict) else {}
    reply_target_input = payload.get("reply_target") if isinstance(payload.get("reply_target"), dict) else {}

    session_id = str(
        payload.get("session_id")
        or reply_target_input.get("session_id")
        or fallback.get("session_id")
        or ""
    ).strip()
    owner_key = str(
        payload.get("owner_key")
        or reply_target_input.get("owner_key")
        or fallback.get("owner_key")
        or ""
    ).strip()
    return {
        "session_id": session_id,
        "owner_key": owner_key,
    }


async def _relay_webrtc_playback(endpoint: str, request_payload: Dict[str, Any]) -> Dict[str, Any]:
    admin_host = str(os.getenv("ADMIN_WEB_SERVICE_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
    admin_port = str(os.getenv("ADMIN_WEB_SERVICE_PORT", "8001") or "8001").strip() or "8001"
    token = str(os.getenv("AUTOYOU_AI_INTERNAL_API_TOKEN", "") or "").strip()
    relay_url = f"http://{admin_host}:{admin_port}{endpoint}"

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(relay_url, json=request_payload, headers=headers) as response:
                try:
                    data = await response.json(content_type=None)
                except Exception:
                    raw_text = await response.text()
                    data = {"success": False, "error": raw_text}

                return {
                    "success": bool(response.status < 400 and bool(data.get("success", True))),
                    "status_code": int(response.status),
                    "relay_url": relay_url,
                    "data": data,
                }
    except Exception as exc:
        return {
            "success": False,
            "status_code": 502,
            "relay_url": relay_url,
            "data": {"success": False, "error": str(exc)},
        }


def _normalize_roots(explicit_roots: Optional[str] = None) -> List[str]:
    if explicit_roots not in (None, ""):
        configured_values = str(explicit_roots).replace("\r", "\n").split("\n")
        roots = [value.strip().strip('"').strip("'") for value in configured_values if value.strip()]
    else:
        roots = resolve_audio_library_roots(_current_settings(), anchor=__file__)
    normalized: List[str] = []
    seen: set[str] = set()
    for root in roots:
        try:
            resolved = Path(str(root)).expanduser().resolve(strict=False)
        except Exception:
            continue
        if not resolved.is_dir():
            continue
        dedupe_key = os.path.normcase(os.path.normpath(str(resolved)))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(str(resolved))
    return normalized


def _should_skip_dir(dir_name: str) -> bool:
    normalized = str(dir_name or "").strip().lower()
    if not normalized:
        return True
    if normalized.startswith("."):
        return True
    return normalized in _SKIP_DIRECTORY_NAMES


def _parse_display_metadata(file_path: Path) -> Dict[str, str]:
    stem = file_path.stem.strip()
    compact = re.sub(r"[_\-]+", " ", stem).strip() or file_path.name
    artist = "Local Library"
    title = compact
    if " - " in compact:
        parts = [part.strip() for part in compact.split(" - ") if part.strip()]
        if len(parts) >= 2:
            artist = parts[0]
            title = " - ".join(parts[1:])
    return {
        "artist": artist,
        "title": title,
    }


def _audio_content_type(file_path: Path) -> str:
    return _AUDIO_CONTENT_TYPES.get(file_path.suffix.lower()) or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"


def _build_track_payload(path_value: Path, root_value: Path) -> Dict[str, Any]:
    absolute_path = str(path_value.resolve())
    metadata = _parse_display_metadata(path_value)
    track_id = hashlib.sha1(os.path.normcase(absolute_path).encode("utf-8")).hexdigest()[:16]
    try:
        relative_path = str(path_value.resolve().relative_to(root_value.resolve())).replace("\\", "/")
    except Exception:
        relative_path = path_value.name

    return {
        "id": track_id,
        "title": metadata["title"],
        "artist": metadata["artist"],
        "file_name": path_value.name,
        "relative_path": relative_path,
        "directory": str(path_value.parent),
        "stream_url": f"api/stream/{track_id}",
        "content_type": _audio_content_type(path_value),
    }


def _track_matches_query(payload: Dict[str, Any], query: str) -> bool:
    normalized_query = " ".join(str(query or "").strip().lower().split())
    if not normalized_query:
        return True
    haystack = " ".join(
        str(payload.get(key) or "")
        for key in ("title", "artist", "file_name", "relative_path", "directory")
    ).lower()
    return all(part in haystack for part in normalized_query.split())


def _scan_tracks(roots: List[str], limit: int, query: str = "") -> Dict[str, Any]:
    tracks: List[Dict[str, Any]] = []
    by_id: Dict[str, str] = {}
    max_tracks = max(1, int(limit))
    normalized_query = " ".join(str(query or "").strip().lower().split())

    for root_text in roots:
        root_path = Path(root_text)
        for base, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [name for name in sorted(dirnames) if not _should_skip_dir(name)]
            for filename in sorted(filenames):
                extension = Path(filename).suffix.lower()
                if extension not in _MUSIC_EXTENSIONS:
                    continue
                if len(tracks) >= max_tracks:
                    return {
                        "tracks": tracks,
                        "by_id": by_id,
                        "truncated": True,
                    }
                path_value = Path(base) / filename
                payload = _build_track_payload(path_value, root_path)
                if normalized_query and not _track_matches_query(payload, normalized_query):
                    continue
                tracks.append(payload)
                by_id[payload["id"]] = str(path_value)

    return {
        "tracks": tracks,
        "by_id": by_id,
        "truncated": False,
    }


def _parse_apic_frame(data: bytes) -> Optional[Tuple[bytes, str]]:
    """Parse an ID3v2 APIC frame payload into (image_bytes, mime_type)."""
    try:
        if len(data) < 4:
            return None
        encoding = data[0]
        rest = data[1:]
        null_pos = rest.find(b"\x00")
        if null_pos < 0:
            return None
        mime = rest[:null_pos].decode("ascii", errors="ignore").strip().lower()
        mime = mime if mime.startswith("image/") else "image/jpeg"
        rest = rest[null_pos + 1:]
        if not rest:
            return None
        rest = rest[1:]  # skip picture-type byte
        if encoding in (1, 2):
            i = 0
            while i + 1 < len(rest):
                if rest[i] == 0 and rest[i + 1] == 0:
                    rest = rest[i + 2:]
                    break
                i += 2
            else:
                return None
        else:
            np = rest.find(b"\x00")
            rest = rest[np + 1:] if np >= 0 else rest
        return (rest, mime) if len(rest) >= 4 else None
    except Exception:
        return None


def _extract_id3v2_apic(file_path: Path) -> Optional[Tuple[bytes, str]]:
    """Pure-Python ID3v2 APIC extractor for MP3 files."""
    try:
        with open(file_path, "rb") as fh:
            hdr = fh.read(10)
            if len(hdr) < 10 or hdr[:3] != b"ID3":
                return None
            version_major = hdr[3]
            flags = hdr[5]
            raw = hdr[6:10]
            tag_size = (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]
            if flags & 0x40:
                ext_raw = fh.read(4)
                if len(ext_raw) < 4:
                    return None
                if version_major == 4:
                    ext_size = (ext_raw[0] << 21) | (ext_raw[1] << 14) | (ext_raw[2] << 7) | ext_raw[3]
                else:
                    ext_size = struct.unpack(">I", ext_raw)[0]
                fh.seek(ext_size - 4, 1)
            end_offset = 10 + tag_size
            while fh.tell() < end_offset - 10:
                frame_hdr = fh.read(10)
                if len(frame_hdr) < 10 or frame_hdr[0:1] == b"\x00":
                    break
                frame_id = frame_hdr[:4].decode("ascii", errors="replace")
                if version_major == 4:
                    sz = frame_hdr[4:8]
                    frame_size = (sz[0] << 21) | (sz[1] << 14) | (sz[2] << 7) | sz[3]
                else:
                    frame_size = struct.unpack(">I", frame_hdr[4:8])[0]
                if frame_size <= 0 or frame_size > 50 * 1024 * 1024:
                    break
                if frame_id == "APIC":
                    frame_data = fh.read(frame_size)
                    result = _parse_apic_frame(frame_data)
                    if result:
                        return result
                else:
                    fh.seek(frame_size, 1)
        return None
    except Exception:
        return None


def _extract_artwork_pyav(file_path: Path) -> Optional[Tuple[bytes, str]]:
    """FFmpeg/PyAV fallback: extracts attached-picture streams (M4A, FLAC, OGG, MP3)."""
    try:
        import av  # type: ignore[import]
        with av.open(str(file_path)) as container:
            for stream in container.streams:
                if stream.type != "video":
                    continue
                disposition = getattr(stream, "disposition", None)
                if isinstance(disposition, int):
                    is_pic = bool(disposition & 0x400)
                elif isinstance(disposition, dict):
                    is_pic = bool(disposition.get("attached_pic", False))
                else:
                    is_pic = False
                if not is_pic:
                    continue
                for packet in container.demux(stream):
                    if getattr(packet, "size", 0) <= 0:
                        continue
                    data = bytes(packet)
                    if data[:3] == b"\xff\xd8\xff":
                        return data, "image/jpeg"
                    if data[:8] == b"\x89PNG\r\n\x1a\n":
                        return data, "image/png"
                    if data[:3] == b"GIF":
                        return data, "image/gif"
                    return data, "image/jpeg"
        return None
    except Exception:
        return None


def _extract_artwork(file_path: Path) -> Optional[Tuple[bytes, str]]:
    ext = file_path.suffix.lower()
    if ext == ".mp3":
        result = _extract_id3v2_apic(file_path)
        if result:
            return result
    return _extract_artwork_pyav(file_path)


def _extract_artwork_cached(track_id: str, file_path: Path) -> Optional[Tuple[bytes, str]]:
    with _artwork_cache_lock:
        if track_id in _artwork_cache:
            return _artwork_cache[track_id]
    result = _extract_artwork(file_path)
    with _artwork_cache_lock:
        _artwork_cache[track_id] = result
    return result


def _stream_range_window_bytes() -> int:
    raw_value = str(os.getenv("AUTOYOU_AUDIO_STREAM_RANGE_WINDOW_BYTES", str(DEFAULT_STREAM_RANGE_WINDOW_BYTES)) or "").strip()
    try:
        parsed = int(raw_value)
    except Exception:
        parsed = DEFAULT_STREAM_RANGE_WINDOW_BYTES
    return max(64 * 1024, parsed)


def _parse_http_range(range_header: str, file_size: int) -> Optional[Tuple[int, int]]:
    """Parse single-range HTTP header and return inclusive (start, end)."""
    if file_size <= 0:
        return None

    header = str(range_header or "").strip().lower()
    if not header.startswith("bytes="):
        return None

    range_spec = header[6:].strip()
    if "," in range_spec:
        return None
    if "-" not in range_spec:
        return None

    start_text, end_text = [part.strip() for part in range_spec.split("-", 1)]

    try:
        if start_text:
            start = int(start_text)
            if start < 0 or start >= file_size:
                return None
            if end_text:
                end = int(end_text)
                if end < start:
                    return None
                end = min(end, file_size - 1)
            else:
                end = file_size - 1
            return (start, end)

        suffix_length = int(end_text)
        if suffix_length <= 0:
            return None
        suffix_length = min(suffix_length, file_size)
        start = file_size - suffix_length
        end = file_size - 1
        return (start, end)
    except Exception:
        return None


def _iter_file_slice(file_path: Path, start: int, end: int) -> Iterator[bytes]:
    remaining = (end - start) + 1
    with file_path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            piece = handle.read(min(DEFAULT_STREAM_READ_CHUNK_BYTES, remaining))
            if not piece:
                break
            remaining -= len(piece)
            yield piece


def _library_page(force: bool, offset: int, limit: int, query: str = "") -> Dict[str, Any]:
    normalized_offset = max(0, int(offset))
    normalized_limit = max(1, min(int(limit), MAX_SCAN_TRACKS))
    scan_limit = min(MAX_SCAN_TRACKS, normalized_offset + normalized_limit + 1)
    normalized_query = " ".join(str(query or "").strip().lower().split())
    settings = _current_settings()
    roots = _normalize_roots()
    root_details = resolve_audio_library_path_details(settings, anchor=__file__)

    if normalized_query:
        scanned = _scan_tracks(roots, limit=scan_limit, query=normalized_query)
        cached_tracks = list(scanned["tracks"])
        page_tracks = cached_tracks[normalized_offset:normalized_offset + normalized_limit]
        has_more = bool(scanned["truncated"]) or len(cached_tracks) > (normalized_offset + normalized_limit)
        next_offset = normalized_offset + len(page_tracks) if has_more else None
        return {
            "roots": list(roots),
            "root_details": list(root_details),
            "tracks": page_tracks,
            "by_id": dict(scanned["by_id"]),
            "truncated": has_more,
            "cached": False,
            "offset": normalized_offset,
            "limit": normalized_limit,
            "next_offset": next_offset,
            "has_more": has_more,
            "cached_limit": scan_limit,
            "cached_track_count": len(cached_tracks),
            "query": normalized_query,
        }

    with _library_lock:
        cached_limit = int(_library_cache.get("limit") or 0)
        cached_truncated = bool(_library_cache.get("truncated"))
        cache_scanned = bool(_library_cache.get("scanned"))
        can_reuse_cache = (
            not force
            and cache_scanned
            and _library_cache.get("roots") == roots
            and (not cached_truncated or cached_limit >= scan_limit)
        )

        if not can_reuse_cache:
            scanned = _scan_tracks(roots, limit=scan_limit)
            _library_cache["roots"] = list(roots)
            _library_cache["root_details"] = list(root_details)
            _library_cache["tracks"] = list(scanned["tracks"])
            _library_cache["by_id"] = dict(scanned["by_id"])
            _library_cache["truncated"] = bool(scanned["truncated"])
            _library_cache["limit"] = int(scan_limit)
            _library_cache["scanned"] = True
            cached_limit = int(scan_limit)
            cached_truncated = bool(scanned["truncated"])

        cached_tracks = list(_library_cache["tracks"])
        page_tracks = cached_tracks[normalized_offset:normalized_offset + normalized_limit]
        has_more = bool(cached_truncated) or len(cached_tracks) > (normalized_offset + normalized_limit)
        next_offset = normalized_offset + len(page_tracks) if has_more else None

        return {
            "roots": list(_library_cache["roots"]),
            "root_details": list(_library_cache.get("root_details") or root_details),
            "tracks": page_tracks,
            "by_id": dict(_library_cache["by_id"]),
            "truncated": has_more,
            "cached": can_reuse_cache,
            "offset": normalized_offset,
            "limit": normalized_limit,
            "next_offset": next_offset,
            "has_more": has_more,
            "cached_limit": cached_limit,
            "cached_track_count": len(cached_tracks),
            "query": "",
        }


def _library_state(force: bool, limit: int) -> Dict[str, Any]:
    return _library_page(force=force, offset=0, limit=limit)


@app.get("/")
async def index() -> FileResponse:
    response = FileResponse(INDEX_HTML_PATH)
    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    return response


@app.get("/api/library")
async def api_library(
    force: bool = Query(default=False),
    offset: int = Query(default=0, ge=0, le=MAX_SCAN_TRACKS),
    limit: Optional[int] = Query(default=None, ge=1, le=MAX_SCAN_TRACKS),
    query: str = Query(default=""),
) -> JSONResponse:
    settings = _current_settings()
    effective_limit = int(limit or settings.get("search_limit") or MAX_SCAN_TRACKS)
    state = await asyncio.to_thread(
        _library_page,
        bool(force),
        int(offset or 0),
        max(1, min(effective_limit, MAX_SCAN_TRACKS)),
        str(query or ""),
    )
    return _json_response(
        {
            "success": True,
            "music_library_dirs_env": os.getenv(MUSIC_LIBRARY_DIRS_ENV, ""),
            "roots": state["roots"],
            "track_count": len(state["tracks"]),
            "tracks": state["tracks"],
            "truncated": bool(state["truncated"]),
            "cached": bool(state["cached"]),
            "offset": int(state["offset"]),
            "limit": int(state["limit"]),
            "next_offset": state["next_offset"],
            "has_more": bool(state["has_more"]),
            "cached_track_count": int(state["cached_track_count"]),
            "query": state.get("query", ""),
            "audio_paths": state.get("root_details", []),
            "settings": settings,
        }
    )


@app.get("/api/settings")
async def api_get_settings(request: Request) -> JSONResponse:
    auth_error = _settings_auth_error(request)
    if auth_error:
        return auth_error
    settings = _current_settings()
    return _json_response(
        {
            "success": True,
            "settings": settings,
            "audio_paths": resolve_audio_library_path_details(settings, anchor=__file__),
        }
    )


@app.post("/api/settings")
async def api_update_settings(request: Request, payload: Dict[str, Any] = Body(default={})) -> JSONResponse:
    auth_error = _settings_auth_error(request)
    if auth_error:
        return auth_error
    settings = _update_settings(payload if isinstance(payload, dict) else {})
    return _json_response(
        {
            "success": True,
            "settings": settings,
            "audio_paths": resolve_audio_library_path_details(settings, anchor=__file__),
        }
    )


@app.post("/api/relay/{action}")
async def api_relay_action(action: str, request: Request, payload: Dict[str, Any] = Body(default={})) -> JSONResponse:
    auth_error = _settings_auth_error(request)
    if auth_error:
        return auth_error
    normalized_action = str(action or "").strip().lower()
    action_to_endpoint = {
        "play": "/api/webrtc/playback/play",
        "pause": "/api/webrtc/playback/pause",
        "resume": "/api/webrtc/playback/resume",
        "stop": "/api/webrtc/playback/stop",
        "status": "/api/webrtc/playback/status",
    }
    custom_actions = {"next", "previous", "shuffle", "repeat"}
    endpoint = action_to_endpoint.get(normalized_action)
    if not endpoint and normalized_action not in custom_actions:
        return _json_response(
            {
                "success": False,
                "error": f"Unsupported relay action '{normalized_action}'.",
                "supported_actions": sorted(set(action_to_endpoint.keys()) | custom_actions),
            },
            status_code=400,
        )

    payload = payload if isinstance(payload, dict) else {}
    settings = _current_settings()

    if normalized_action == "repeat":
        requested_mode = str(payload.get("mode") or "").strip().lower()
        if requested_mode not in {"off", "one", "all"}:
            requested_mode = _next_repeat_mode(str(settings.get("repeat_mode") or "off"))
        updated_settings = _update_settings({"repeat_mode": requested_mode})
        return _json_response(
            {
                "success": True,
                "action": normalized_action,
                "message": f"Repeat mode set to {requested_mode}.",
                "settings": updated_settings,
            }
        )

    reply_target = _reply_target_from_payload(payload)
    if not reply_target.get("session_id") and not reply_target.get("owner_key"):
        return _json_response(
            {
                "success": False,
                "error": "Set reply_target.session_id or reply_target.owner_key in audio app settings before relaying playback.",
            },
            status_code=400,
        )

    request_payload: Dict[str, Any] = {
        key: value
        for key, value in reply_target.items()
        if str(value or "").strip()
    }

    selected_track: Optional[Dict[str, Any]] = None

    if normalized_action in {"next", "previous", "shuffle"}:
        state = _library_state(force=False, limit=MAX_SCAN_TRACKS)
        tracks = list(state.get("tracks") or [])
        if not tracks:
            return _json_response(
                {
                    "success": False,
                    "error": "No tracks are available in the scanned library.",
                },
                status_code=400,
            )

        current_track_id = str(payload.get("track_id") or "").strip().lower()
        current_index = _find_track_index(tracks, current_track_id)

        if normalized_action == "shuffle":
            if len(tracks) == 1:
                target_track = tracks[0]
            else:
                candidate_indexes = list(range(len(tracks)))
                if current_index >= 0:
                    candidate_indexes = [index for index in candidate_indexes if index != current_index]
                target_track = tracks[random.choice(candidate_indexes)]
        elif normalized_action == "next":
            if current_index < 0:
                target_track = tracks[0]
            elif str(settings.get("repeat_mode") or "off") == "one":
                target_track = tracks[current_index]
            else:
                target_track = tracks[(current_index + 1) % len(tracks)]
        else:
            if current_index < 0:
                target_track = tracks[-1]
            elif str(settings.get("repeat_mode") or "off") == "one":
                target_track = tracks[current_index]
            else:
                target_track = tracks[(current_index - 1) % len(tracks)]

        request_payload["file_path"] = str(target_track.get("directory") or "").strip()
        file_name = str(target_track.get("file_name") or "").strip()
        request_payload["file_path"] = str(Path(request_payload["file_path"]) / file_name)
        selected_track = dict(target_track)
        endpoint = "/api/webrtc/playback/play"

    if normalized_action == "play":
        file_path = str(payload.get("file_path") or "").strip()
        track_id = str(payload.get("track_id") or "").strip().lower()
        if not file_path and track_id:
            state = _library_state(force=False, limit=MAX_SCAN_TRACKS)
            file_path = str(state.get("by_id", {}).get(track_id) or "").strip()
        if not file_path:
            return _json_response(
                {
                    "success": False,
                    "error": "play relay requires file_path or track_id",
                },
                status_code=400,
            )
        request_payload["file_path"] = file_path

    relay_result = await _relay_webrtc_playback(endpoint, request_payload)
    response_status = int(relay_result.get("status_code") or 200)
    response_payload = {
        "success": bool(relay_result.get("success")),
        "action": normalized_action,
        "request_payload": request_payload,
        "relay_url": relay_result.get("relay_url"),
        "relay": relay_result.get("data") if isinstance(relay_result.get("data"), dict) else {},
    }
    if selected_track is not None:
        response_payload["selected_track"] = selected_track
    return _json_response(response_payload, status_code=response_status)


# ── Auth Endpoints (used by settings panel OTP gate) ───────────────────────

@app.post("/api/auth/login")
async def api_auth_login(request: Request) -> JSONResponse:
    """Verify TOTP code and issue a per-agent session token."""
    helpers = _import_auth_helpers()
    totp_capabilities = helpers.get("totp_capabilities")
    create_session = helpers.get("create_session")
    cookie_name_fn = helpers.get("cookie_name")
    cookie_path_fn = helpers.get("cookie_path")
    runtime_server = helpers.get("runtime_server")

    if not all([create_session, cookie_name_fn, runtime_server]):
        return _json_response({"success": False, "error": "Auth helpers not available."}, status_code=503)

    try:
        payload = await request.json()
    except Exception:
        return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)

    code = str(payload.get("totp_code") or payload.get("code") or "").strip()
    if not code:
        return _json_response({"success": False, "error": "totp_code is required."}, status_code=400)

    server = runtime_server()
    assigned_profile = False
    try:
        assigned_profile = server.agent_has_assigned_2fa_profile(_AGENT_NAME) is True
    except Exception:
        pass

    if assigned_profile:
        try:
            verified = bool(server.verify_agent_assigned_2fa(_AGENT_NAME, code))
        except Exception:
            verified = False
    else:
        totp_caps = totp_capabilities() if totp_capabilities else {}
        if not totp_caps.get("totp_configured"):
            return _json_response({"success": False, "error": "2FA is not configured on this server."}, status_code=400)
        cfg = server.STATE.config or server._default_config()
        try:
            secret = server._get_pairing_totp_secret(cfg)
            verified = server._verify_totp_secret(secret, code)
        except Exception:
            verified = False

    if not verified:
        return _json_response({"success": False, "error": "Invalid authentication code."}, status_code=401)

    ttl_days = _SESSION_TTL_DAYS
    settings: Dict[str, Any] = {"session_ttl_days": ttl_days}
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import _get_agent_security_settings

        settings = _get_agent_security_settings(_AGENT_NAME)
        ttl_days = settings.get("session_ttl_days", ttl_days)
    except Exception:
        pass

    token = create_session(_AGENT_NAME, ttl_days)
    response = _json_response({"success": True, "token": token, "authenticated": True})
    set_session_cookie = helpers.get("set_session_cookie")
    if set_session_cookie:
        set_session_cookie(response, request, _AGENT_NAME, token, settings)
    else:
        response.set_cookie(
            cookie_name_fn(_AGENT_NAME), token, httponly=True, samesite="lax", path="/",
            max_age=int(ttl_days * 86400),
        )
    return response


@app.get("/api/auth/status")
async def api_auth_status(request: Request) -> JSONResponse:
    authenticated = _check_auth(request)
    return _json_response({"success": True, "authenticated": authenticated})


@app.post("/api/auth/logout")
async def api_auth_logout(request: Request) -> JSONResponse:
    helpers = _import_auth_helpers()
    delete_session = helpers.get("delete_session")
    cookie_name_fn = helpers.get("cookie_name")
    cookie_path_fn = helpers.get("cookie_path")

    if delete_session and cookie_name_fn:
        cookie_token = request.cookies.get(cookie_name_fn(_AGENT_NAME), "")
        if cookie_token:
            try:
                delete_session(_AGENT_NAME, cookie_token)
            except Exception:
                pass
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            bearer = auth_header[7:].strip()
            if bearer:
                try:
                    delete_session(_AGENT_NAME, bearer)
                except Exception:
                    pass

    response = _json_response({"success": True})
    if cookie_name_fn:
        if cookie_path_fn:
            response.delete_cookie(cookie_name_fn(_AGENT_NAME), path=cookie_path_fn(_AGENT_NAME))
        response.delete_cookie(cookie_name_fn(_AGENT_NAME), path="/")
    return response


# ── WebRTC Prefetch Endpoint ───────────────────────────────────────────────

@app.get("/api/webrtc/prefetch")
async def api_webrtc_prefetch(request: Request) -> JSONResponse:
    """Return currently active WebRTC session IDs so the settings form can
    pre-populate the relay target fields. Requires settings-panel auth."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)

    helpers = _import_auth_helpers()
    runtime_server = helpers.get("runtime_server")

    sessions: List[Dict[str, str]] = []

    if runtime_server:
        try:
            server = runtime_server()
            # Access the main server's WEBRTC manager to enumerate live sessions
            webrtc = getattr(server, "WEBRTC", None) or getattr(server, "_webrtc", None)
            if webrtc is None:
                import server as _main_server  # type: ignore[import]
                webrtc = getattr(_main_server, "WEBRTC", None)
            if webrtc is not None:
                for sid in list(getattr(webrtc, "session_peers", {}).keys()):
                    sessions.append({"session_id": str(sid), "owner_key": ""})
                # Try to enrich with owner_key from datachannel managers
                dc_managers = getattr(webrtc, "datachannel_managers", {})
                for sid, mgr in list(dc_managers.items()):
                    owner_key = str(getattr(mgr, "owner_key", "") or "").strip()
                    for entry in sessions:
                        if entry.get("session_id") == str(sid):
                            entry["owner_key"] = owner_key
        except Exception:
            pass

    return _json_response({
        "success": True,
        "sessions": sessions,
        "count": len(sessions),
    })


@app.get("/api/artwork/{track_id}")
async def api_artwork(track_id: str, request: Request) -> Response:
    normalized_id = str(track_id or "").strip().lower()
    if not normalized_id:
        return Response(status_code=404)

    with _artwork_cache_lock:
        if normalized_id in _artwork_cache and _artwork_cache[normalized_id] is None:
            return Response(status_code=404)

    state = _library_state(force=False, limit=MAX_SCAN_TRACKS)
    source_path = state["by_id"].get(normalized_id)
    if not source_path:
        with _artwork_cache_lock:
            _artwork_cache[normalized_id] = None
        return Response(status_code=404)

    file_path = Path(source_path)
    if not file_path.is_file():
        with _artwork_cache_lock:
            _artwork_cache[normalized_id] = None
        return Response(status_code=404)

    result = await asyncio.to_thread(_extract_artwork_cached, normalized_id, file_path)
    if result is None:
        return Response(status_code=404)

    image_bytes, mime_type = result
    return Response(
        content=image_bytes,
        media_type=mime_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "Accept-Ranges": "none",
        },
    )


@app.get("/api/stream/{track_id}")
async def api_stream(track_id: str, request: Request):
    normalized_track_id = str(track_id or "").strip().lower()
    if not normalized_track_id:
        return _json_response({"success": False, "error": "track_id is required"}, status_code=400)

    state = _library_state(force=False, limit=MAX_SCAN_TRACKS)
    source_path = state["by_id"].get(normalized_track_id)
    if not source_path:
        state = _library_state(force=True, limit=MAX_SCAN_TRACKS)
        source_path = state["by_id"].get(normalized_track_id)

    if not source_path:
        return _json_response({"success": False, "error": "Track not found"}, status_code=404)

    file_path = Path(source_path)
    if not file_path.is_file():
        return _json_response({"success": False, "error": "File is no longer available"}, status_code=410)

    content_type = _audio_content_type(file_path)
    file_size = int(file_path.stat().st_size)
    range_header = request.headers.get("range", "")
    parsed_range = _parse_http_range(range_header, file_size)

    if range_header and parsed_range is None:
        response = Response(status_code=416)
        response.headers["Content-Range"] = f"bytes */{file_size}"
        response.headers["Accept-Ranges"] = "bytes"
        for header_name, header_value in NO_CACHE_HEADERS.items():
            response.headers[header_name] = header_value
        return response

    if parsed_range is None:
        response = FileResponse(path=file_path, media_type=content_type)
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Content-Length"] = str(file_size)
        for header_name, header_value in NO_CACHE_HEADERS.items():
            response.headers[header_name] = header_value
        return response

    start, end = parsed_range
    range_spec = str(range_header or "").strip().lower().removeprefix("bytes=").strip()
    if range_spec.endswith("-") or request.headers.get("x-autoyou-webrtc-session-id"):
        window = _stream_range_window_bytes()
        if (end - start + 1) > window:
            end = min(file_size - 1, start + window - 1)

    content_length = (end - start) + 1
    response = StreamingResponse(
        _iter_file_slice(file_path, start, end),
        status_code=206,
        media_type=content_type,
    )
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    response.headers["Content-Length"] = str(content_length)
    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    return response
