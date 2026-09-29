# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-via-e9678340342c3c9482dc7803

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import json
import mimetypes
from pathlib import Path
from typing import Any, Dict, Optional
import asyncio
import threading

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from autoyou_agents.media_generation_agent.media_generation_tool import (
    get_wan2gp_config,
    save_wan2gp_config,
    list_history,
    get_history_item,
    delete_history_item,
    save_history_item,
    generate_media_sync,
    ai_enhance_prompt,
    _normalize_media_type,
    _normalize_model_type,
    sanitize_media_history_item_for_api,
    wan2gp_environment_status,
    detect_and_apply_wan2gp_paths,
    start_wan2gp_install,
    get_wan2gp_install_status,
    OUTPUT_DIR
)
from autoyou_agents.shared_tools.agent_install_registry import load_agent_install_registry
from autoyou_agents.shared_tools.scheduler_mission_control import install_agent_website_auth
from shared.secure_storage import read_secure_file

__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-via-e9678340342c3c9482dc7803"


APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
INDEX_HTML_PATH = FRONTEND_DIR / "index.html"

app = FastAPI(title="AutoYou Media Generator")
install_agent_website_auth(
    app,
    agent_name="media_generation_agent",
    title="AutoYou Media Generator",
    is_authenticated=lambda request: _check_auth(request),
)

@app.middleware("http")
async def strip_agent_prefix_middleware(request: Request, call_next):
    path = request.scope.get("path", "")
    prefix = "/agent/media_generation_agent"
    if path.startswith(prefix):
        new_path = path[len(prefix):]
        if not new_path:
            new_path = "/"
        request.scope["path"] = new_path
    return await call_next(request)

# Serve frontend files under /assets
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")

# Serve media files directly from AutoYou output directory
MEDIA_DIR = OUTPUT_DIR
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}

_GENERATION_LOCK = threading.Lock()

def _json_response(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    return response

_AGENT_NAME = "media_generation_agent"
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
    """Return True only for this agent's configured auth policy."""
    helpers = _import_auth_helpers()
    describe_auth_state = helpers.get("describe_auth_state")
    if describe_auth_state:
        try:
            return bool(describe_auth_state(request, _AGENT_NAME).get("authenticated"))
        except Exception:
            pass
    session_valid = helpers.get("session_valid")
    cookie_name_fn = helpers.get("cookie_name")
    if not session_valid or not cookie_name_fn:
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

def media_agent_installed() -> bool:
    return "media_generation_agent" in set(load_agent_install_registry().get("installed_agents", []))

@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "agent_name": "media_generation_agent",
        "proxy_path": "/agent/media_generation_agent/",
        "installed": media_agent_installed(),
    }

# ── Auth Endpoints ─────────────────────────────────────────────────────────

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
    # from __debug_provenance_r__ import via
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

@app.get("/api/history")
def api_history(request: Request, media_type: Optional[str] = None) -> JSONResponse:
    """List generated media history."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        items = list_history(limit=50, media_type=media_type)
        public_items = [sanitize_media_history_item_for_api(item) for item in items]
        return _json_response({"success": True, "history": public_items})
    except Exception as e:
        return _json_response({"success": False, "error": str(e)}, status_code=500)

@app.get("/api/history/{item_id}")
def api_history_item(item_id: int, request: Request) -> JSONResponse:
    """Get single history item details."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    item = get_history_item(item_id)
    if not item:
        return _json_response({"success": False, "error": f"Item {item_id} not found"}, status_code=404)
    return _json_response({"success": True, "item": sanitize_media_history_item_for_api(item)})

@app.delete("/api/history/{item_id}")
def api_delete_item(item_id: int, request: Request) -> JSONResponse:
    """Delete entry and associated file."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    deleted = delete_history_item(item_id)
    if not deleted:
        return _json_response({"success": False, "error": f"Item {item_id} not found or failed to delete"}, status_code=404)
    return _json_response({"success": True, "deleted_item_id": item_id})

@app.get("/api/config")
def api_get_config(request: Request) -> JSONResponse:
    """Load current Settings."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        config = get_wan2gp_config()
        # Add quick local status check
        root_path = Path(config.get("root", ""))
        python_path = Path(config.get("python", ""))
        connected = root_path.exists() and python_path.exists()
        
        return _json_response({
            "success": True, 
            "config": config,
            "connected": connected,
            "diagnostic": {
                "root_exists": root_path.exists(),
                "python_exists": python_path.exists(),
                "app_dir_exists": Path(config.get("app_dir", "")).exists()
            }
        })
    except Exception as e:
        return _json_response({"success": False, "error": str(e)}, status_code=500)

@app.post("/api/config")
async def api_save_config(request: Request) -> JSONResponse:
    """Save Settings overrides."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            return _json_response({"success": False, "error": "Invalid payload format"}, status_code=400)
        
        save_wan2gp_config(payload)
        return _json_response({"success": True, "message": "Settings saved successfully."})
    except Exception as e:
        return _json_response({"success": False, "error": str(e)}, status_code=500)

@app.get("/api/wan2gp/environment")
def api_wan2gp_environment(request: Request) -> JSONResponse:
    """Platform + install status for the Wan2GP environment panel."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        return _json_response({"success": True, "environment": wan2gp_environment_status()})
    except Exception as e:
        return _json_response({"success": False, "error": str(e)}, status_code=500)

@app.post("/api/wan2gp/detect")
async def api_wan2gp_detect(request: Request) -> JSONResponse:
    """Discover an existing Wan2GP install on this machine and save its paths."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        result = detect_and_apply_wan2gp_paths()
        return _json_response({"success": result.get("status") == "applied", **result})
    except Exception as e:
        return _json_response({"success": False, "error": str(e)}, status_code=500)

@app.post("/api/wan2gp/install")
async def api_wan2gp_install(request: Request) -> JSONResponse:
    """Start the machine-appropriate managed Wan2GP install in the background."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        result = start_wan2gp_install()
        return _json_response({"success": result.get("status") == "started", **result})
    except Exception as e:
        return _json_response({"success": False, "error": str(e)}, status_code=500)

@app.get("/api/wan2gp/install/status")
def api_wan2gp_install_status(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        return _json_response({"success": True, "install": get_wan2gp_install_status()})
    except Exception as e:
        return _json_response({"success": False, "error": str(e)}, status_code=500)

@app.post("/api/enhance-prompt")
async def api_enhance(request: Request) -> JSONResponse:
    """Enhance raw visual prompt using AutoYou AI model config."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
        prompt = payload.get("prompt", "").strip()
        media_type = _normalize_media_type(payload.get("media_type", "video"))
        
        if not prompt:
            return _json_response({"success": False, "error": "Prompt is required"}, status_code=400)
            
        enhanced = ai_enhance_prompt(prompt, media_type)
        return _json_response({"success": True, "enhanced_prompt": enhanced})
    except ValueError as e:
        return _json_response({"success": False, "error": str(e)}, status_code=400)
    except Exception as e:
        return _json_response({"success": False, "error": str(e)}, status_code=500)

def run_background_generation(
    prompt: str,
    media_type: str,
    model_type: Optional[str],
    resolution: Optional[str],
    steps: Optional[int],
    frames: Optional[int],
    seed: Optional[int],
    optimized_prompt: Optional[str],
    item_id: int
):
    """Run generation in background thread."""
    try:
        with _GENERATION_LOCK:
            generate_media_sync(
                prompt=prompt,
                media_type=media_type,
                model_type=model_type,
                resolution=resolution,
                steps=steps,
                frames=frames,
                seed=seed,
                history_item_id=item_id,
                optimized_prompt=optimized_prompt,
            )
    except Exception as e:
        from autoyou_agents.media_generation_agent.media_generation_tool import update_history_status
        update_history_status(item_id, "failed", f"Background thread crash: {str(e)}")

@app.post("/api/generate")
async def api_generate(request: Request) -> JSONResponse:
    """Launch asynchronous generation job."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
        prompt = payload.get("prompt", "").strip()
        media_type = _normalize_media_type(payload.get("media_type", "video"))
        model_type = _normalize_model_type(payload.get("model_type"))
        resolution = payload.get("resolution")
        steps = payload.get("steps")
        frames = payload.get("frames")
        seed = payload.get("seed")
        optimized_prompt = str(payload.get("optimized_prompt") or "").strip() or None
        
        if not prompt:
            return _json_response({"success": False, "error": "Prompt description is required"}, status_code=400)
            
        # 1. Create database record
        settings = {
            "model_type": model_type,
            "resolution": resolution,
            "num_inference_steps": steps,
            "video_length": frames,
            "seed": seed
        }
        item_id = save_history_item(
            media_type=media_type,
            original_prompt=prompt,
            optimized_prompt="Enhancing prompt...",
            file_path="",
            file_name="",
            settings=settings,
            status="generating"
        )
        
        # 2. Detach long GPU/CPU work from the request lifecycle.
        thread = threading.Thread(
            target=run_background_generation,
            kwargs={
                "prompt": prompt,
                "media_type": media_type,
                "model_type": model_type,
                "resolution": resolution,
                "steps": steps,
                "frames": frames,
                "seed": seed,
                "optimized_prompt": optimized_prompt,
                "item_id": item_id,
            },
            daemon=True,
            name=f"media-generation-{item_id}",
        )
        thread.start()
        
        return _json_response({
            "success": True,
            "item_id": item_id,
            "status": "generating",
            "message": "Generation job launched successfully in background."
        })
    except ValueError as e:
        return _json_response({"success": False, "error": str(e)}, status_code=400)
    except Exception as e:
        return _json_response({"success": False, "error": str(e)}, status_code=500)

@app.get("/media/{media_type}/{filename}")
def serve_media_file(media_type: str, filename: str, request: Request):
    """Serve generated video or image file from absolute output storage."""
    if not _check_auth(request):
        return HTMLResponse("Not authenticated", status_code=401)
    folder = "images" if media_type == "image" else "videos"
    file_path = MEDIA_DIR / folder / filename
    
    if not file_path.exists() or not file_path.is_file():
        # Check parent folder as fallback
        file_path = MEDIA_DIR / filename
        
    if not file_path.exists() or not file_path.is_file():
        return HTMLResponse("Media file not found", status_code=404)
        
    try:
        media_type, _ = mimetypes.guess_type(file_path.name)
        return Response(
            content=read_secure_file(file_path),
            media_type=media_type or "application/octet-stream",
            headers=NO_CACHE_HEADERS,
        )
    except Exception as exc:
        return HTMLResponse(f"Media file could not be opened: {exc}", status_code=500)

@app.get("/{full_path:path}")
def serve_frontend(full_path: str):
    """Serve index.html or other static files in frontend."""
    candidate = FRONTEND_DIR / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate)
    
    # Fallback to serving template
    try:
        html_content = INDEX_HTML_PATH.read_text(encoding="utf-8")
        response = HTMLResponse(html_content)
    except Exception:
        response = HTMLResponse("Media Generator Web UI placeholder", status_code=200)
        
    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    return response
