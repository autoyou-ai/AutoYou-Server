# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
from __future__ import annotations

import os
import uuid
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from autoyou_agents.voice_training_agent.agent import (
    _get_paths,
    install_custom_voice_tts_provider,
    list_voice_transcripts,
    delete_voice_transcript,
    test_synthesize_voice,
)
from autoyou_agents.voice_training_agent import train_model
from autoyou_agents.shared_tools.agent_install_registry import load_agent_install_registry
from autoyou_agents.shared_tools.scheduler_mission_control import install_agent_website_auth
from shared.voice_training_storage import (
    copy_voice_training_data,
    get_voice_training_dir,
    get_voice_training_storage_info,
    reset_voice_training_dir,
    set_voice_training_dir,
)
from shared.secure_storage import SecureStorageError, load_secure_json, read_secure_file, save_secure_json, write_secure_file

APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT
INDEX_HTML_PATH = FRONTEND_DIR / "index.html"

app = FastAPI(title="Voice Training Agent")
install_agent_website_auth(
    app,
    agent_name="voice_training_agent",
    title="Voice Training Agent",
    is_authenticated=lambda request: _check_auth(request),
)

@app.middleware("http")
async def strip_agent_prefix_middleware(request: Request, call_next):
    path = request.scope.get("path", "")
    prefix = "/agent/voice_training_agent"
    if path.startswith(prefix):
        new_path = path[len(prefix):]
        if not new_path:
            new_path = "/"
        request.scope["path"] = new_path
    return await call_next(request)

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}

def _json_response(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    return response

_AGENT_NAME = "voice_training_agent"
_SESSION_TTL_DAYS = 7

def _import_auth_helpers():
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import (
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
    if not session_valid or not cookie_name_fn or not runtime_server:
        return False
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import _get_agent_security_settings
        settings = _get_agent_security_settings(_AGENT_NAME)
        if settings.get("auth_mode") == "open":
            return True
    except Exception:
        pass

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

def voice_agent_installed() -> bool:
    return "voice_training_agent" in set(load_agent_install_registry().get("installed_agents", []))

@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "agent_name": "voice_training_agent",
        "proxy_path": "/agent/voice_training_agent/",
        "installed": voice_agent_installed(),
    }

# ── Auth Endpoints ─────────────────────────────────────────────────────────

@app.post("/api/auth/login")
async def api_auth_login(request: Request) -> JSONResponse:
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
        ttl_days = settings.get("session_ttl_days", _SESSION_TTL_DAYS)
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
    helpers = _import_auth_helpers()
    totp_capabilities = helpers.get("totp_capabilities")
    
    totp_configured = False
    auth_mode = "totp"
    if totp_capabilities:
        try:
            totp_caps = totp_capabilities()
            totp_configured = bool(totp_caps.get("totp_configured"))
        except Exception:
            pass
            
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import _get_agent_security_settings
        settings = _get_agent_security_settings(_AGENT_NAME)
        auth_mode = settings.get("auth_mode", "totp")
    except Exception:
        pass

    authenticated = _check_auth(request)
    return _json_response({
        "success": True, 
        "authenticated": authenticated,
        "auth_mode": auth_mode,
        "totp_configured": totp_configured
    })

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
        cookie_path = cookie_path_fn(_AGENT_NAME) if cookie_path_fn else f"/agent/{_AGENT_NAME}"
        response.delete_cookie(cookie_name_fn(_AGENT_NAME), path=cookie_path)
        response.delete_cookie(cookie_name_fn(_AGENT_NAME), path="/")
    return response

# ── Protected API Endpoints ───────────────────────────────────────────────

@app.get("/api/transcripts")
def api_get_transcripts(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    res = list_voice_transcripts(limit=100)
    return _json_response(res)

@app.delete("/api/transcripts/{transcript_id}")
def api_delete_transcript(transcript_id: str, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    res = delete_voice_transcript(transcript_id)
    return _json_response(res)

@app.post("/api/transcripts/upload")
async def api_upload_transcript(
    request: Request,
    transcript: str = Form(...),
    file: UploadFile = File(...)
) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        vt_dir, transcripts_file, recordings_dir = _get_paths()
        recordings_dir.mkdir(parents=True, exist_ok=True)

        rec_id = str(uuid.uuid4())[:8]
        filename = f"upload_{rec_id}.wav"
        file_path = recordings_dir / filename

        # Read upload content and save it
        content = await file.read()
        write_secure_file(file_path, content)

        # Update metadata json
        data = []
        if transcripts_file.exists():
            try:
                loaded = load_secure_json(transcripts_file, default=[])
                data = loaded if isinstance(loaded, list) else []
            except SecureStorageError:
                raise
            except Exception:
                pass

        new_entry = {
            "id": rec_id,
            "filename": filename,
            "transcript": transcript,
            "timestamp": time.time(),
            "source": "upload",
        }
        data.append(new_entry)

        save_secure_json(transcripts_file, data)

        return _json_response({"success": True, "entry": new_entry})
    except Exception as e:
        return _json_response({"success": False, "error": str(e)}, status_code=500)

@app.post("/api/train")
async def api_trigger_train(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    epochs = int(payload.get("epochs") or train_model.DEFAULT_TRAINING_EPOCHS)
    success = train_model.start_training_async(epochs=epochs)
    if success:
        return _json_response({"success": True, "message": "Voice training started in the background."})
    else:
        return _json_response({"success": False, "error": "Training is already in progress."}, status_code=409)

@app.get("/api/train/status")
def api_train_status(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    status_data = train_model.get_status()
    return _json_response({"success": True, "training_status": status_data})

@app.get("/api/storage")
def api_storage_status(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        return _json_response({"success": True, "storage": get_voice_training_storage_info()})
    except Exception as e:
        return _json_response({"success": False, "error": str(e)}, status_code=500)

@app.post("/api/storage")
async def api_set_storage(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    current_status = train_model.get_status()
    if current_status.get("status") == "training":
        return _json_response(
            {
                "success": False,
                "error": "Voice training is running. Stop it or wait for completion before changing storage.",
                "training_status": current_status,
            },
            status_code=409,
        )
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    path = str(payload.get("path") or "").strip()
    migrate_existing = bool(payload.get("migrate_existing", True))
    if not path:
        return _json_response({"success": False, "error": "path is required"}, status_code=400)
    try:
        source_dir = get_voice_training_dir()
        migration = copy_voice_training_data(source_dir, path) if migrate_existing else None
        storage = set_voice_training_dir(path)
        return _json_response({"success": True, "storage": storage, "migration": migration})
    except Exception as e:
        return _json_response({"success": False, "error": str(e)}, status_code=400)

@app.post("/api/storage/reset")
async def api_reset_storage(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    current_status = train_model.get_status()
    if current_status.get("status") == "training":
        return _json_response(
            {
                "success": False,
                "error": "Voice training is running. Stop it or wait for completion before changing storage.",
                "training_status": current_status,
            },
            status_code=409,
        )
    try:
        storage = reset_voice_training_dir()
        return _json_response({"success": True, "storage": storage})
    except Exception as e:
        return _json_response({"success": False, "error": str(e)}, status_code=400)

@app.post("/api/synthesize")
async def api_trigger_synthesis(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    text = str(payload.get("text") or "").strip()
    if not text:
        return _json_response({"success": False, "error": "Text is required"}, status_code=400)

    res = test_synthesize_voice(text)
    return _json_response(res)

@app.post("/api/install-provider")
async def api_install_provider(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    res = install_custom_voice_tts_provider()
    if res.get("status") == "success":
        return _json_response({"success": True, **res})
    return _json_response({"success": False, **res}, status_code=400)

@app.get("/api/recordings/{filename}")
def serve_recording(filename: str, request: Request):
    if not _check_auth(request):
        return HTMLResponse("Not authenticated", status_code=401)
    _, _, recordings_dir = _get_paths()
    file_path = recordings_dir / filename
    if not file_path.exists() or not file_path.is_file():
        return HTMLResponse("File not found", status_code=404)
    return Response(content=read_secure_file(file_path), media_type="audio/wav", headers={"Cache-Control": "no-store, max-age=0"})

@app.get("/api/synthesize/file/{filename}")
def serve_synthesized_file(filename: str, request: Request):
    if not _check_auth(request):
        return HTMLResponse("Not authenticated", status_code=401)
    vt_dir, _, _ = _get_paths()
    file_path = vt_dir / "test_syntheses" / filename
    if not file_path.exists() or not file_path.is_file():
        return HTMLResponse("File not found", status_code=404)
    return Response(content=read_secure_file(file_path), media_type="audio/wav", headers={"Cache-Control": "no-store, max-age=0"})

@app.get("/{full_path:path}")
def serve_frontend(full_path: str):
    candidate = FRONTEND_DIR / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate)
    
    try:
        html_content = INDEX_HTML_PATH.read_text(encoding="utf-8")
        response = HTMLResponse(html_content)
    except Exception:
        response = HTMLResponse("Voice Training Web UI Placeholder", status_code=200)
        
    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    return response
