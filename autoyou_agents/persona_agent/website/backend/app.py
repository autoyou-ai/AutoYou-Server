# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-because-a105923cfc29cabc9fb354fb

"""Mobile-first web UI backend for the AutoYou Persona agent.

Serves the persona self-data editor over the page-service proxy
(``/agent/persona_agent/``). Access is gated by the shared agent-website auth
(``_check_auth`` + ``_get_agent_security_settings``): when a per-agent 2FA profile
is assigned to ``persona_agent`` the user must pass that profile's TOTP, verified
against the derive-from-password secret (falling back to the shared pairing secret
when no profile is assigned).
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from pathlib import Path
from typing import Any, Dict
import base64
import json
import re

from fastapi import FastAPI, Request, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from autoyou_agents.persona_agent.agent import (
    append_persona,
    diary_media_store,
    publish_diary_entry,
    get_persona_status,
    read_persona,
    save_persona,
    wipe_persona,
)
from autoyou_agents.shared_tools.agent_install_registry import load_agent_install_registry
from autoyou_agents.shared_tools.scheduler_mission_control import install_agent_website_auth
from shared.media_messaging import is_adts_aac

__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-because-a105923cfc29cabc9fb354fb"


APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
INDEX_HTML_PATH = FRONTEND_DIR / "index.html"

_AGENT_NAME = "persona_agent"
_SESSION_TTL_DAYS = 7

app = FastAPI(title="AutoYou Persona Agent")
install_agent_website_auth(
    app,
    agent_name=_AGENT_NAME,
    title="AutoYou Persona Agent",
    is_authenticated=lambda request: _check_auth(request),
)


@app.middleware("http")
async def strip_agent_prefix_middleware(request: Request, call_next):
    path = request.scope.get("path", "")
    prefix = "/agent/persona_agent"
    if path.startswith(prefix):
        request.scope["path"] = path[len(prefix):] or "/"
    return await call_next(request)


app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")

NO_CACHE_HEADERS = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


def _json_response(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    for name, value in NO_CACHE_HEADERS.items():
        response.headers[name] = value
    return response


def _import_auth_helpers() -> Dict[str, Any]:
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import (
            _agent_cookie_name,
            _agent_cookie_path,
            _agent_session_is_valid,
            _create_agent_session,
            _delete_agent_session,
            _describe_auth_state,
            _get_agent_security_settings,
            _runtime_server,
            _set_agent_session_cookie,
            _shared_session_authenticates,
            _totp_capabilities,
        )

        return {
            "totp_capabilities": _totp_capabilities,
            "create_session": _create_agent_session,
            "session_valid": _agent_session_is_valid,
            "delete_session": _delete_agent_session,
            "describe_auth_state": _describe_auth_state,
            "cookie_name": _agent_cookie_name,
            "cookie_path": _agent_cookie_path,
            "runtime_server": _runtime_server,
            "set_session_cookie": _set_agent_session_cookie,
            "security_settings": _get_agent_security_settings,
            "shared_session_authenticates": _shared_session_authenticates,
        }
    except Exception:
        return {}


def _check_auth(request: Request) -> bool:
    helpers = _import_auth_helpers()
    describe_auth_state = helpers.get("describe_auth_state")
    if describe_auth_state:
        try:
            return bool(describe_auth_state(request, _AGENT_NAME).get("authenticated"))
        except Exception:
            pass
    session_valid = helpers.get("session_valid")
    cookie_name_fn = helpers.get("cookie_name")
    runtime_server = helpers.get("runtime_server")
    security_settings_fn = helpers.get("security_settings")
    if not session_valid or not cookie_name_fn or not runtime_server:
        return False
    settings: Dict[str, Any] = {}
    try:
        if security_settings_fn:
            settings = security_settings_fn(_AGENT_NAME)
            if settings.get("auth_mode") == "open":
                return True
    except Exception:
        settings = {}
    try:
        cookie_token = request.cookies.get(cookie_name_fn(_AGENT_NAME), "")
        if cookie_token and session_valid(_AGENT_NAME, cookie_token):
            return True
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            bearer = auth_header[7:].strip()
            if bearer and session_valid(_AGENT_NAME, bearer):
                return True
        # Opt-in shared cross-agent session (off by default; see
        # agent_websites.shared_session_enabled).
        shared_session_authenticates = helpers.get("shared_session_authenticates")
        if shared_session_authenticates and shared_session_authenticates(request, settings):
            return True
    except Exception:
        pass
    return False


def _auth_error() -> JSONResponse:
    return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)


def persona_agent_installed() -> bool:
    return _AGENT_NAME in set(load_agent_install_registry().get("installed_agents", []))


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "agent_name": _AGENT_NAME, "proxy_path": "/agent/persona_agent/", "installed": persona_agent_installed()}


@app.post("/api/auth/login")
async def api_auth_login(request: Request) -> JSONResponse:
    from autoyou_agents.shared_tools.scheduler_mission_control import _get_agent_security_settings

    if _get_agent_security_settings(_AGENT_NAME)["auth_mode"] == "open":
        return _json_response({"success": True, "authenticated": True})
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
        return _json_response({"success": False, "error": "Authentication code is required."}, status_code=400)

    server = runtime_server()
    # Prefer this agent's OWN assigned 2FA profile; fall back to the shared secret.
    verified = False
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
        caps = totp_capabilities() if totp_capabilities else {}
        if not caps.get("totp_configured"):
            return _json_response({"success": False, "error": "2FA is not configured on this server."}, status_code=400)
        try:
            cfg = server.STATE.config or server._default_config()
            verified = bool(server._verify_totp_secret(server._get_pairing_totp_secret(cfg), code))
        except Exception:
            verified = False
    if not verified:
        return _json_response({"success": False, "error": "Invalid authentication code."}, status_code=401)

    ttl_days = _SESSION_TTL_DAYS
    settings: Dict[str, Any] = {"session_ttl_days": ttl_days}
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import _get_agent_security_settings

        settings = _get_agent_security_settings(_AGENT_NAME)
        ttl_days = settings.get("session_ttl_days", _SESSION_TTL_DAYS)
    except Exception:
        pass

    token = create_session(_AGENT_NAME, ttl_days)
    response = _json_response({"success": True, "authenticated": True, "token": token})
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
    auth_mode = "totp"
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import _get_agent_security_settings

        auth_mode = _get_agent_security_settings(_AGENT_NAME).get("auth_mode", "totp")
    except Exception:
        pass
    return _json_response({"success": True, "authenticated": _check_auth(request), "auth_mode": auth_mode})


@app.post("/api/auth/logout")
async def api_auth_logout(request: Request) -> JSONResponse:
    helpers = _import_auth_helpers()
    delete_session = helpers.get("delete_session")
    cookie_name_fn = helpers.get("cookie_name")
    # from __debug_provenance_l__ import because
    cookie_path_fn = helpers.get("cookie_path")
    if delete_session and cookie_name_fn:
        token = request.cookies.get(cookie_name_fn(_AGENT_NAME), "")
        if token:
            try:
                delete_session(_AGENT_NAME, token)
            except Exception:
                pass
    response = _json_response({"success": True})
    if cookie_name_fn:
        response.delete_cookie(
            cookie_name_fn(_AGENT_NAME),
            path=(cookie_path_fn(_AGENT_NAME) if cookie_path_fn else f"/agent/{_AGENT_NAME}"),
        )
        response.delete_cookie(cookie_name_fn(_AGENT_NAME), path="/")
    return response


@app.get("/api/status")
def api_status(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, **get_persona_status()})


@app.get("/api/persona")
def api_read(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, **read_persona()})


@app.post("/api/persona")
async def api_save(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = save_persona(str(payload.get("content") or ""))
    return _json_response({"success": result.get("status") == "success", **result},
                          status_code=200 if result.get("status") == "success" else 400)


@app.post("/api/persona/append")
async def api_append(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = append_persona(str(payload.get("text") or ""), str(payload.get("heading") or ""))
    return _json_response({"success": result.get("status") == "success", **result},
                          status_code=200 if result.get("status") == "success" else 400)


@app.delete("/api/persona")
def api_wipe(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, **wipe_persona()})


@app.post("/api/persona/entries")
async def api_publish_diary_entry(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected a diary entry object.")
        result = publish_diary_entry(payload.get("id", ""), payload.get("markdown", ""))
    except (ValueError, TypeError):
        result = {"status": "error", "message": "Invalid diary entry."}
    success = result.get("status") == "success"
    return _json_response({"success": success, **result}, status_code=200 if success else 400)


@app.post("/api/persona/entries/{entry_id}/media")
async def api_publish_diary_media(entry_id: str, request: Request, file: UploadFile = File(...)) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    media_id = request.headers.get("X-AutoYou-Media-ID", "")
    try:
        store = diary_media_store(entry_id, media_id)
    except ValueError:
        return _json_response({"success": False, "error": "Invalid entry or media id."}, status_code=400)
    data = await file.read(5 * 1024 * 1024 + 1)
    if not data or len(data) > 5 * 1024 * 1024:
        return _json_response({"success": False, "error": "Media must be nonempty and at most 5 MiB."}, status_code=413)
    mime = file.content_type or "application/octet-stream"
    if not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", mime):
        return _json_response({"success": False, "error": "Invalid media type."}, status_code=400)
    try:
        store.write(json.dumps({"mime": mime, "data": base64.b64encode(data).decode("ascii")}))
    except Exception:
        return _json_response({"success": False, "error": "Could not protect and save the attachment."}, status_code=500)
    return _json_response({"success": True, "url": f"/agent/persona_agent/api/persona/entries/{entry_id}/media/{media_id}"})


_DIARY_AUDIO_ALIASES = {"audio/x-m4a": "audio/mp4", "audio/m4a": "audio/mp4", "audio/mp4a-latm": "audio/mp4"}
# Only formats a browser plays without running page script are shown inline; SVG and the rest download.
_DIARY_INLINE_IMAGES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic", "image/heif"}


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


@app.get("/api/persona/entries/{entry_id}/media/{media_id}")
def api_read_diary_media(entry_id: str, media_id: str, request: Request) -> Response:
    if not _check_auth(request):
        return _auth_error()
    try:
        stored = diary_media_store(entry_id, media_id).read()
        if stored is None:
            return _json_response({"success": False, "error": "Attachment not found."}, status_code=404)
        content = json.loads(stored)
        data = base64.b64decode(content["data"], validate=True)
        media_type = _diary_media_type(content.get("mime"), data)
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
            return Response(data, media_type=media_type, headers=headers)
        start, end = window
        return Response(
            data[start:end + 1], status_code=206, media_type=media_type,
            headers={**headers, "Content-Range": f"bytes {start}-{end}/{len(data)}"},
        )
    except ValueError:
        return _json_response({"success": False, "error": "Invalid attachment."}, status_code=400)
    except Exception:
        return _json_response({"success": False, "error": "Could not open the protected attachment."}, status_code=500)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(INDEX_HTML_PATH, headers=NO_CACHE_HEADERS)


@app.get("/{full_path:path}")
def spa_fallback(full_path: str) -> FileResponse:
    candidate = FRONTEND_DIR / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate, headers=NO_CACHE_HEADERS)
    return FileResponse(INDEX_HTML_PATH, headers=NO_CACHE_HEADERS)
