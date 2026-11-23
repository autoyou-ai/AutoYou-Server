# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-via-e9678340342c3c9482dc7803

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import base64
import json
import mimetypes
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from autoyou_agents.media_generation_agent import engines as media_engines
from autoyou_agents.media_generation_agent import factory as media_factory
from autoyou_agents.media_generation_agent import media_generation_tool as mgt
from autoyou_agents.media_generation_agent.media_generation_tool import (
    MEDIA_DATA_DIR,
    OUTPUT_DIR,
    _external_python_subprocess_env,
    _normalize_media_type,
    _normalize_model_type,
    ai_enhance_prompt,
    delete_history_item,
    detect_and_apply_wan2gp_paths,
    draw_things_environment_status,
    generate_media_sync,
    get_active_llm_info,
    get_history_item,
    get_wan2gp_config,
    get_wan2gp_install_status,
    list_available_llm_options,
    list_history,
    list_installed_wan_models,
    sanitize_media_history_item_for_api,
    save_history_item,
    save_wan2gp_config,
    start_wan2gp_install,
    update_history_status,
    wan2gp_environment_status,
)
from autoyou_agents.self_improvement import goal_loop
from autoyou_agents.shared_tools.agent_input_ux import AgentHTMLResponse as HTMLResponse
from autoyou_agents.shared_tools.agent_install_registry import load_agent_install_registry
from autoyou_agents.shared_tools.scheduler_mission_control import install_agent_website_auth
from shared.http_errors import internal_error_message
from shared.media_generation_runtime import probe_media_file, probe_wan_runtime
from shared.path_safety import contained_path

__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-via-e9678340342c3c9482dc7803"


APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
INDEX_HTML_PATH = FRONTEND_DIR / "index.html"

app = FastAPI(title="AutoYou Media Factory Studio")
install_agent_website_auth(
    app,
    agent_name="media_generation_agent",
    title="AutoYou Media Factory Studio",
    is_authenticated=lambda request: _check_auth(request),
)


@app.middleware("http")
async def strip_agent_prefix_middleware(request: Request, call_next):
    path = request.scope.get("path", "")
    prefix = "/agent/media_generation_agent"
    if path.startswith(prefix):
        new_path = path[len(prefix) :]
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

_ALLOWED_UPLOAD_EXTENSIONS = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".bmp": "image",
    ".mp4": "video",
    ".mov": "video",
    ".webm": "video",
    ".mkv": "video",
    ".avi": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".ogg": "audio",
    ".flac": "audio",
    ".aac": "audio",
}
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
_ALLOWED_PROJECT_ASSET_SUBDIRS = {"assets", "keyframes", "clips", "chain_frames", "final"}


def _json_response(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    return response


def _active_output_root() -> Path:
    root = Path(MEDIA_DIR) if MEDIA_DIR != OUTPUT_DIR else Path(mgt.OUTPUT_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _uploads_dir() -> Path:
    up = _active_output_root() / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    return up


def _resolve_client_media_ref(ref: Optional[str]) -> Optional[str]:
    """Resolve an opaque UI media reference into a verified, contained local file path.

    Rejects arbitrary host paths so HTTP clients can only reference uploaded media,
    recorded gallery items, or contained project assets.
    """
    raw = str(ref or "").strip()
    if not raw:
        return None
    if raw.startswith("upload:"):
        fname = raw[len("upload:") :].strip()
        candidate = contained_path(_uploads_dir(), fname)
        return str(candidate) if (candidate and candidate.is_file()) else None
    if raw.startswith("history:"):
        raw_id = raw[len("history:") :].strip()
        if not raw_id.isdigit():
            return None
        item = get_history_item(int(raw_id))
        if item and item.get("file_path") and Path(item["file_path"]).is_file():
            return str(Path(item["file_path"]))
        return None
    if raw.startswith("project:"):
        parts = raw[len("project:") :].strip().split("/")
        if len(parts) == 3 and parts[0].isdigit() and parts[1] in _ALLOWED_PROJECT_ASSET_SUBDIRS:
            pid = int(parts[0])
            proj_root = _active_output_root() / "projects" / f"project_{pid}"
            base = proj_root if parts[1] == "final" else (proj_root / parts[1])
            candidate = contained_path(base, parts[2])
            return str(candidate) if (candidate and candidate.is_file()) else None
        return None
    # Allow bare filename inside uploads/images/videos/output root only.
    if "/" not in raw and "\\" not in raw and ".." not in raw:
        for base in (
            _uploads_dir(),
            _active_output_root() / "images",
            _active_output_root() / "videos",
            _active_output_root(),
        ):
            candidate = contained_path(base, raw)
            if candidate and candidate.is_file():
                return str(candidate)
    return None


def _attach_project_urls(project: Dict[str, Any]) -> Dict[str, Any]:
    """Augment a sanitized project dict with safe browser URLs for keyframes, clips, and master output."""
    out = media_factory.sanitize_project_for_api(project)
    pid = out.get("id")
    if out.get("has_final_output") and out.get("final_file_name"):
        out["final_video_url"] = f"/agent/media_generation_agent/media/video/{out['final_file_name']}"
    else:
        out["final_video_url"] = ""
    if isinstance(out.get("scenes"), list):
        for scene in out["scenes"]:
            kf_name = str(scene.get("keyframe_file_name") or "").strip()
            clip_name = str(scene.get("clip_file_name") or "").strip()
            scene["keyframe_url"] = (
                f"/agent/media_generation_agent/api/projects/{pid}/assets/keyframes/{kf_name}"
                if kf_name
                else ""
            )
            scene["clip_url"] = (
                f"/agent/media_generation_agent/api/projects/{pid}/assets/clips/{clip_name}"
                if clip_name
                else ""
            )
    return out


_AGENT_NAME = "media_generation_agent"
_SESSION_TTL_DAYS = 7


def _import_auth_helpers():
    """Lazy-import shared auth helpers to avoid startup failures if scheduler_mission_control
    is not available (e.g., during unit testing with mocked paths)."""
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import (  # type: ignore[import]
            _agent_cookie_name,
            _agent_cookie_path,
            _agent_session_is_valid,
            _create_agent_session,
            _delete_agent_session,
            _describe_auth_state,
            _runtime_server,
            _set_agent_session_cookie,
            _totp_capabilities,
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
        "features": ["media_factory", "goal_loop", "image_edit", "multimodal_inputs", "multi_engine"],
    }


# ── Auth Endpoints ─────────────────────────────────────────────────────────


@app.post("/api/auth/login")
async def api_auth_login(request: Request) -> JSONResponse:
    """Verify TOTP code and issue a per-agent session token."""
    helpers = _import_auth_helpers()
    totp_capabilities = helpers.get("totp_capabilities")
    create_session = helpers.get("create_session")
    cookie_name_fn = helpers.get("cookie_name")
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
            cookie_name_fn(_AGENT_NAME),
            token,
            httponly=True,
            samesite="lax",
            path="/",
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
    describe_auth_state = helpers.get("describe_auth_state")
    if describe_auth_state:
        try:
            described = describe_auth_state(request, _AGENT_NAME)
            if described.get("via") == "loopback_no_totp":
                auth_mode = "open"
        except Exception:
            pass
    return _json_response(
        {
            "success": True,
            "authenticated": authenticated,
            "auth_mode": auth_mode,
            "totp_configured": totp_configured,
        }
    )

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


# ── History Endpoints ─────────────────────────────────────────────────────


@app.get("/api/history")
def api_history(request: Request, media_type: Optional[str] = None) -> JSONResponse:
    """List generated media history."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        items = list_history(limit=80, media_type=media_type)
        public_items = [sanitize_media_history_item_for_api(item) for item in items]
        return _json_response({"success": True, "history": public_items})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


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


# ── Config, Hardware, Multi-Engine, Models & LLM Options ──────────────────


@app.get("/api/config")
def api_get_config(request: Request) -> JSONResponse:
    """Load current settings and connection status for the active local engine."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        config = get_wan2gp_config()
        engine = str(config.get("engine") or "wan2gp").strip().lower()
        if engine == "draw_things":
            draw_status = draw_things_environment_status(config)
            connected = draw_status["ready"]
            diagnostic: Dict[str, Any] = {
                "draw_things_cli_found": draw_status["cli_found"],
                "models_dir_exists": draw_status["models_dir_exists"],
            }
        elif engine == "comfyui":
            comfy = media_engines.discover_comfyui_installation(config)
            connected = bool(comfy.get("ready"))
            diagnostic = {"comfyui": comfy}
        elif engine == "mlx":
            mlx = media_engines.discover_mlx_installation(config)
            connected = bool(mlx.get("ready"))
            diagnostic = {"mlx": mlx}
        elif engine == "sdcpp":
            sdcpp = media_engines.discover_sdcpp_installation(config)
            connected = bool(sdcpp.get("ready"))
            diagnostic = {"sdcpp": sdcpp}
        else:
            root_path = Path(config.get("root", ""))
            python_path = Path(config.get("python", ""))
            runtime = probe_wan_runtime(config, _external_python_subprocess_env())
            connected = bool(runtime.get("ready"))
            diagnostic = {
                "root_exists": root_path.exists(),
                "python_exists": python_path.exists(),
                "app_dir_exists": Path(config.get("app_dir", "")).exists(),
                "runtime": runtime,
            }

        return _json_response(
            {
                "success": True,
                "config": config,
                "connected": connected,
                "diagnostic": diagnostic,
                "active_llm": get_active_llm_info(),
            }
        )
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


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
    except ValueError as e:
        return _json_response({"success": False, "error": str(e)}, status_code=400)
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.get("/api/wan2gp/environment")
def api_wan2gp_environment(request: Request) -> JSONResponse:
    """Platform + install status for the Wan2GP environment panel."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        return _json_response({"success": True, "environment": wan2gp_environment_status()})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.post("/api/wan2gp/detect")
async def api_wan2gp_detect(request: Request) -> JSONResponse:
    """Discover an existing Wan2GP install on this machine and save its paths."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        result = detect_and_apply_wan2gp_paths()
        return _json_response({"success": result.get("status") == "applied", **result})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.post("/api/wan2gp/install")
async def api_wan2gp_install(request: Request) -> JSONResponse:
    """Start the machine-appropriate managed Wan2GP install in the background."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        result = start_wan2gp_install()
        return _json_response({"success": result.get("status") == "started", **result})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.get("/api/wan2gp/install/status")
def api_wan2gp_install_status(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        return _json_response({"success": True, "install": get_wan2gp_install_status()})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.get("/api/hardware")
def api_hardware(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        cfg = get_wan2gp_config()
        wan_runtime = probe_wan_runtime(cfg, _external_python_subprocess_env())
        hw = media_engines.detect_hardware_capabilities(wan_runtime)
        return _json_response({"success": True, "hardware": hw, "wan_runtime": wan_runtime})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.get("/api/engines")
def api_engines(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        cfg = get_wan2gp_config()
        wan_env = wan2gp_environment_status()
        dt = draw_things_environment_status(cfg)
        snapshot = media_engines.all_engines_status(cfg, wan_env=wan_env, draw_things_status=dt)
        snapshot["install_states"] = {
            "wan2gp": get_wan2gp_install_status(),
            "comfyui": media_engines.get_engine_install_status("comfyui"),
            "mlx": media_engines.get_engine_install_status("mlx"),
        }
        return _json_response({"success": True, **snapshot})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.post("/api/engines/verify")
def api_engines_verify(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        cfg = get_wan2gp_config()
        wan_env = wan2gp_environment_status()
        dt = draw_things_environment_status(cfg)
        report = media_engines.verify_media_factory_installation(cfg, wan_env=wan_env, draw_things_status=dt)
        return _json_response({"success": True, "verification": report})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.post("/api/engines/detect")
def api_engines_detect(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        wan_res = detect_and_apply_wan2gp_paths()
        cfg = get_wan2gp_config()
        wan_env = wan2gp_environment_status()
        dt = draw_things_environment_status(cfg)
        snapshot = media_engines.all_engines_status(cfg, wan_env=wan_env, draw_things_status=dt)
        return _json_response({"success": True, "wan2gp_detect": wan_res, **snapshot})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.post("/api/engines/install")
async def api_engines_install(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
        engine = str((payload or {}).get("engine") or "wan2gp").strip().lower()
        if engine == "wan2gp":
            result = start_wan2gp_install()
        elif engine in {"comfyui", "mlx"}:
            result = media_engines.start_managed_engine_install(engine, on_config_update=save_wan2gp_config)
        else:
            return _json_response(
                {"success": False, "error": f"Automated install is not supported for '{engine}'."},
                status_code=400,
            )
        return _json_response({"success": result.get("status") in {"started", "already_running"}, "engine": engine, **result})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.get("/api/engines/install/status")
def api_engines_install_status(request: Request, engine: str = "wan2gp") -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        norm = str(engine or "wan2gp").strip().lower()
        state = get_wan2gp_install_status() if norm == "wan2gp" else media_engines.get_engine_install_status(norm)
        return _json_response({"success": True, "engine": norm, "install": state})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.get("/api/models")
def api_models(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        cfg = get_wan2gp_config()
        catalog = list_installed_wan_models(cfg)
        flat_installed: List[Dict[str, Any]] = []
        seen_ids = set()
        for kind in ("video", "image", "edit"):
            for entry in catalog.get(kind) or []:
                if entry.get("installed") and entry.get("id") not in seen_ids:
                    seen_ids.add(entry["id"])
                    flat_installed.append({**entry, "kind": kind})
        return _json_response({"success": True, "models": catalog, "installed_models": flat_installed})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.get("/api/llm-options")
def api_llm_options(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        opts = list_available_llm_options()
        return _json_response({"success": True, **opts})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


# ── Multimodal Uploads ────────────────────────────────────────────────────


@app.post("/api/uploads")
async def api_upload_media(request: Request) -> JSONResponse:
    """Upload an image, video, or audio file (JSON base64 or raw body) into contained storage."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        content_type = (request.headers.get("content-type") or "").lower()
        raw_bytes: bytes = b""
        orig_name = "upload.bin"
        mimetype = ""

        if "application/json" in content_type:
            payload = await request.json()
            if not isinstance(payload, dict):
                return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
            orig_name = str(payload.get("filename") or payload.get("name") or "upload.bin").strip()
            mimetype = str(payload.get("mimetype") or payload.get("mime_type") or "").strip()
            b64 = str(payload.get("data_base64") or payload.get("data") or "").strip()
            if "," in b64 and b64.lower().startswith("data:"):
                header, b64 = b64.split(",", 1)
                if not mimetype and ";" in header:
                    mimetype = header[5:].split(";", 1)[0].strip()
            if not b64:
                return _json_response({"success": False, "error": "data_base64 is required."}, status_code=400)
            try:
                raw_bytes = base64.b64decode(b64, validate=True)
            except Exception:
                return _json_response({"success": False, "error": "Invalid base64 payload."}, status_code=400)
        else:
            raw_bytes = await request.body()
            orig_name = str(request.headers.get("x-filename") or "upload.bin").strip()
            mimetype = content_type.split(";", 1)[0].strip()

        if not raw_bytes:
            return _json_response({"success": False, "error": "Uploaded file is empty."}, status_code=400)
        if len(raw_bytes) > _MAX_UPLOAD_BYTES:
            return _json_response({"success": False, "error": "File exceeds 100 MB upload limit."}, status_code=413)

        ext = Path(orig_name).suffix.lower()
        if not ext and mimetype:
            ext = mimetypes.guess_extension(mimetype) or ""
            if ext == ".jpe":
                ext = ".jpg"
        if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
            return _json_response(
                {"success": False, "error": f"Unsupported media type '{ext or mimetype}'. Allowed: image, video, audio."},
                status_code=400,
            )
        kind = _ALLOWED_UPLOAD_EXTENSIONS[ext]
        safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(orig_name).stem).strip("_")[:48] or kind
        saved_name = f"{safe_stem}_{uuid.uuid4().hex[:10]}{ext}"
        dest = contained_path(_uploads_dir(), saved_name)
        if dest is None:
            return _json_response({"success": False, "error": "Invalid upload filename."}, status_code=400)
        dest.write_bytes(raw_bytes)

        probe: Dict[str, Any] = {}
        if kind in {"video", "audio"}:
            try:
                probe = probe_media_file(dest)
            except Exception:
                probe = {}

        return _json_response(
            {
                "success": True,
                "upload": {
                    "filename": saved_name,
                    "ref": f"upload:{saved_name}",
                    "kind": kind,
                    "mimetype": mimetype or (mimetypes.guess_type(saved_name)[0] or "application/octet-stream"),
                    "size_bytes": len(raw_bytes),
                    "url": f"/agent/media_generation_agent/api/uploads/{saved_name}",
                    "probe": probe,
                },
            }
        )
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.get("/api/uploads/{filename}")
def api_serve_upload(filename: str, request: Request):
    if not _check_auth(request):
        return HTMLResponse("Not authenticated", status_code=401)
    file_path = contained_path(_uploads_dir(), filename)
    if file_path is None or not file_path.is_file():
        return HTMLResponse("Upload not found", status_code=404)
    mime, _ = mimetypes.guess_type(file_path.name)
    from shared import content_files

    return content_files.response(file_path, request, mime or "application/octet-stream")


# ── Prompt Enhancement, Quick Generation & Image Modification ─────────────


@app.post("/api/enhance-prompt")
async def api_enhance(request: Request) -> JSONResponse:
    """Enhance raw visual prompt using AutoYou AI model config (or per-request LLM override)."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
        prompt = str(payload.get("prompt") or "").strip()
        media_type = _normalize_media_type(payload.get("media_type", "video"))
        context_notes = str(payload.get("context_notes") or "").strip() or None
        edit_mode = bool(payload.get("edit_mode", False))
        llm_provider = str(payload.get("llm_provider_override") or "").strip() or None
        llm_model = str(payload.get("llm_model_override") or "").strip() or None

        if not prompt:
            return _json_response({"success": False, "error": "Prompt is required"}, status_code=400)

        enhanced = ai_enhance_prompt(
            prompt,
            media_type,
            context_notes=context_notes,
            edit_mode=edit_mode,
            llm_provider_override=llm_provider,
            llm_model_override=llm_model,
        )
        return _json_response(
            {
                "success": True,
                "enhanced_prompt": enhanced,
                "llm": get_active_llm_info(provider_override=llm_provider, model_override=llm_model),
            }
        )
    except ValueError as e:
        return _json_response({"success": False, "error": str(e)}, status_code=400)
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


def run_background_generation(
    prompt: str,
    media_type: str,
    model_type: Optional[str],
    resolution: Optional[str],
    steps: Optional[int],
    frames: Optional[int],
    seed: Optional[int],
    optimized_prompt: Optional[str],
    item_id: int,
    *,
    duration_seconds: Optional[float] = None,
    fps: Optional[int] = None,
    negative_prompt: Optional[str] = None,
    reference_image_path: Optional[str] = None,
    end_image_path: Optional[str] = None,
    input_video_path: Optional[str] = None,
    input_audio_path: Optional[str] = None,
    edit_mode: bool = False,
    denoise_strength: Optional[float] = None,
    engine_override: Optional[str] = None,
    llm_provider_override: Optional[str] = None,
    llm_model_override: Optional[str] = None,
    context_notes: Optional[str] = None,
):
    """Run generation in background thread."""
    try:
        kwargs: Dict[str, Any] = {
            "prompt": prompt,
            "media_type": media_type,
            "model_type": model_type,
            "resolution": resolution,
            "steps": steps,
            "frames": frames,
            "seed": seed,
            "history_item_id": item_id,
            "optimized_prompt": optimized_prompt,
        }
        # Only forward extended kwargs when non-default so legacy test mocks stay compatible!
        optional_extras = {
            "duration_seconds": duration_seconds,
            "fps": fps,
            "negative_prompt": negative_prompt,
            "reference_image_path": reference_image_path,
            "end_image_path": end_image_path,
            "input_video_path": input_video_path,
            "input_audio_path": input_audio_path,
            "denoise_strength": denoise_strength,
            "engine_override": engine_override,
            "llm_provider_override": llm_provider_override,
            "llm_model_override": llm_model_override,
            "context_notes": context_notes,
        }
        for key, value in optional_extras.items():
            if value is not None:
                kwargs[key] = value
        if edit_mode:
            kwargs["edit_mode"] = True
        result = generate_media_sync(**kwargs)
        if result.get("status") != "success":
            update_history_status(item_id, "failed", result.get("message") or "Generation failed.")
    except Exception as e:
        update_history_status(item_id, "failed", f"Background thread crash: {str(e)}")


@app.post("/api/generate")
async def api_generate(request: Request) -> JSONResponse:
    """Launch asynchronous image/video generation job (supports multimodal inputs)."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    item_id = None
    try:
        payload = await request.json()
        prompt = str(payload.get("prompt") or "").strip()
        media_type = _normalize_media_type(payload.get("media_type", "video"))
        model_type = _normalize_model_type(payload.get("model_type"))
        resolution = payload.get("resolution")
        steps = payload.get("steps")
        frames = payload.get("frames")
        duration_seconds = payload.get("duration_seconds")
        fps = payload.get("fps")
        seed = payload.get("seed")
        optimized_prompt = str(payload.get("optimized_prompt") or "").strip() or None
        negative_prompt = str(payload.get("negative_prompt") or "").strip() or None
        edit_mode = bool(payload.get("edit_mode", False))
        denoise_strength = payload.get("denoise_strength")
        engine_override = str(payload.get("engine_override") or "").strip() or None
        llm_provider = str(payload.get("llm_provider_override") or "").strip() or None
        llm_model = str(payload.get("llm_model_override") or "").strip() or None
        context_notes = str(payload.get("context_notes") or "").strip() or None

        ref_img = _resolve_client_media_ref(payload.get("reference_image_ref"))
        end_img = _resolve_client_media_ref(payload.get("end_image_ref"))
        in_vid = _resolve_client_media_ref(payload.get("input_video_ref"))
        in_aud = _resolve_client_media_ref(payload.get("input_audio_ref"))

        if not prompt:
            return _json_response({"success": False, "error": "Prompt description is required"}, status_code=400)

        settings = {
            "model_type": model_type,
            "resolution": resolution,
            "num_inference_steps": steps,
            "video_length": frames,
            "seed": seed,
        }
        item_id = save_history_item(
            media_type=media_type,
            original_prompt=prompt,
            optimized_prompt=optimized_prompt or "Queued for generation.",
            file_path="",
            file_name="",
            settings=settings,
            status="queued",
        )

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
                "duration_seconds": float(duration_seconds) if duration_seconds else None,
                "fps": int(fps) if fps else None,
                "negative_prompt": negative_prompt,
                "reference_image_path": ref_img,
                "end_image_path": end_img,
                "input_video_path": in_vid,
                "input_audio_path": in_aud,
                "edit_mode": edit_mode,
                "denoise_strength": float(denoise_strength) if denoise_strength is not None else None,
                "engine_override": engine_override,
                "llm_provider_override": llm_provider,
                "llm_model_override": llm_model,
                "context_notes": context_notes,
            },
            daemon=True,
            name=f"media-generation-{item_id}",
        )
        thread.start()

        return _json_response(
            {
                "success": True,
                "item_id": item_id,
                "status": "queued",
                "message": "Generation job launched successfully in background.",
            }
        )
    except ValueError as e:
        return _json_response({"success": False, "error": str(e)}, status_code=400)
    except Exception as e:
        if item_id is not None:
            update_history_status(item_id, "failed", f"Could not start generation: {e}")
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.post("/api/edit-image")
async def api_edit_image(request: Request) -> JSONResponse:
    """Modify an uploaded or gallery image using instruction-guided editing."""
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    item_id = None
    try:
        payload = await request.json()
        instruction = str(payload.get("instruction") or payload.get("prompt") or "").strip()
        source_ref = str(payload.get("source_ref") or payload.get("reference_image_ref") or "").strip()
        if not instruction:
            return _json_response({"success": False, "error": "Edit instruction is required."}, status_code=400)
        source_path = _resolve_client_media_ref(source_ref)
        if not source_path:
            return _json_response({"success": False, "error": "Source image reference not found."}, status_code=404)

        model_type = _normalize_model_type(payload.get("model_type"))
        resolution = payload.get("resolution")
        steps = int(payload["steps"]) if payload.get("steps") else None
        seed = int(payload["seed"]) if payload.get("seed") is not None else -1
        denoise_strength = float(payload.get("denoise_strength") or 0.65)
        engine_override = str(payload.get("engine_override") or "").strip() or None
        context_notes = str(payload.get("context_notes") or "").strip() or None

        item_id = save_history_item(
            media_type="image",
            original_prompt=f"[Image Edit] {instruction}",
            optimized_prompt="Queued for image modification.",
            file_path="",
            file_name="",
            settings={"model_type": model_type, "resolution": resolution, "num_inference_steps": steps, "seed": seed},
            status="queued",
        )

        def _worker() -> None:
            try:
                res = media_factory.modify_image_sync(
                    instruction=instruction,
                    source_image_path=source_path,
                    model_type=model_type,
                    resolution=resolution,
                    steps=steps,
                    seed=seed,
                    denoise_strength=denoise_strength,
                    engine_override=engine_override,
                    context_notes=context_notes,
                    history_item_id=item_id,
                )
                if res.get("status") != "success":
                    update_history_status(item_id, "failed", res.get("message") or "Image edit failed.")
            except Exception as exc:
                update_history_status(item_id, "failed", f"Image edit crash: {exc}")

        threading.Thread(target=_worker, daemon=True, name=f"media-edit-{item_id}").start()
        return _json_response(
            {
                "success": True,
                "item_id": item_id,
                "status": "queued",
                "message": "Image modification job started.",
            }
        )
    except ValueError as e:
        return _json_response({"success": False, "error": str(e)}, status_code=400)
    except Exception as e:
        if item_id is not None:
            update_history_status(item_id, "failed", str(e))
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


# ── Media Factory Projects, Scenes & Goal Loop Endpoints ──────────────────


@app.get("/api/projects")
def api_list_projects(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        projects = []
        for proj in media_factory.list_projects(limit=50):
            safe = _attach_project_urls(proj)
            safe["goal_status"] = goal_loop.get_media_factory_goal_status(int(proj["id"]))
            projects.append(safe)
        return _json_response({"success": True, "projects": projects})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.post("/api/projects")
async def api_create_project(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        original_request = str(payload.get("original_request") or payload.get("prompt") or "").strip()
        title = str(payload.get("title") or "").strip()
        if not original_request and not title:
            return _json_response({"success": False, "error": "A title or creative brief is required."}, status_code=400)

        attachments: List[Dict[str, Any]] = []
        for raw_ref in payload.get("attachment_refs") or []:
            resolved = _resolve_client_media_ref(str(raw_ref))
            if resolved:
                mime, _ = mimetypes.guess_type(resolved)
                attachments.append({"path": resolved, "mimetype": mime or ""})

        project = media_factory.create_project(
            title=title or original_request[:64],
            original_request=original_request or title,
            kind=str(payload.get("kind") or "long_video"),
            target_duration_seconds=float(payload.get("target_duration_seconds") or 30.0),
            scene_duration_seconds=float(payload["scene_duration_seconds"]) if payload.get("scene_duration_seconds") else None,
            fps=int(payload["fps"]) if payload.get("fps") else None,
            aspect_ratio=str(payload.get("aspect_ratio") or "16:9"),
            resolution=str(payload.get("resolution") or "").strip() or None,
            engine=str(payload.get("engine") or "").strip() or None,
            video_model=str(payload.get("video_model") or "").strip() or None,
            image_model=str(payload.get("image_model") or "").strip() or None,
            edit_model=str(payload.get("edit_model") or "").strip() or None,
            memory_profile=str(payload.get("memory_profile") or "").strip() or None,
            llm_provider_override=str(payload.get("llm_provider_override") or "").strip() or None,
            llm_model_override=str(payload.get("llm_model_override") or "").strip() or None,
            style_bible=str(payload.get("style_bible") or ""),
            embedded_context=payload.get("embedded_context") if isinstance(payload.get("embedded_context"), dict) else None,
            attachments=attachments,
            settings=payload.get("settings") if isinstance(payload.get("settings"), dict) else None,
            plan_now=bool(payload.get("plan_now", False)),
        )
        safe = _attach_project_urls(project)
        safe["goal_status"] = goal_loop.get_media_factory_goal_status(int(project["id"]))
        return _json_response({"success": True, "project": safe})
    except ValueError as e:
        return _json_response({"success": False, "error": str(e)}, status_code=400)
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.get("/api/projects/{project_id}")
def api_get_project(project_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    project = media_factory.get_project(project_id, include_scenes=True, include_logs=True)
    if not project:
        return _json_response({"success": False, "error": f"Project #{project_id} not found"}, status_code=404)
    safe = _attach_project_urls(project)
    safe["goal_status"] = goal_loop.get_media_factory_goal_status(project_id)
    return _json_response({"success": True, "project": safe})


@app.patch("/api/projects/{project_id}")
async def api_update_project(project_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        # Disallow direct overwrite of internal host path columns over HTTP.
        blocked = {"final_output_path", "owner_pid", "owner_started_at"}
        clean_updates = {k: v for k, v in payload.items() if k not in blocked}
        updated = media_factory.update_project(project_id, clean_updates)
        if not updated:
            return _json_response({"success": False, "error": f"Project #{project_id} not found"}, status_code=404)
        safe = _attach_project_urls(updated)
        safe["goal_status"] = goal_loop.get_media_factory_goal_status(project_id)
        return _json_response({"success": True, "project": safe})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.delete("/api/projects/{project_id}")
def api_delete_project(project_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        goal_loop.cancel_media_factory_goal(project_id)
    except Exception:
        pass
    deleted = media_factory.delete_project(project_id)
    if not deleted:
        return _json_response({"success": False, "error": f"Project #{project_id} not found"}, status_code=404)
    return _json_response({"success": True, "deleted_project_id": project_id})


@app.post("/api/projects/{project_id}/plan")
async def api_plan_project(project_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
        project = media_factory.plan_project_script(
            project_id,
            instructions=str(payload.get("instructions") or ""),
            scene_count=int(payload["scene_count"]) if payload.get("scene_count") else None,
            replace_existing=bool(payload.get("replace_existing", True)),
        )
        safe = _attach_project_urls(project)
        safe["goal_status"] = goal_loop.get_media_factory_goal_status(project_id)
        return _json_response({"success": True, "project": safe})
    except ValueError as e:
        return _json_response({"success": False, "error": str(e)}, status_code=404)
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.post("/api/projects/{project_id}/scenes")
async def api_add_scene(project_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        scene_data = {
            k: v
            for k, v in payload.items()
            if k not in {"reference_image_path", "input_video_path", "input_audio_path", "keyframe_image_path", "rendered_clip_path", "refined_clip_path"}
        }
        if payload.get("reference_image_ref"):
            resolved = _resolve_client_media_ref(payload.get("reference_image_ref"))
            if resolved:
                scene_data["reference_image_path"] = resolved
        if payload.get("input_video_ref"):
            resolved = _resolve_client_media_ref(payload.get("input_video_ref"))
            if resolved:
                scene_data["input_video_path"] = resolved
        if payload.get("input_audio_ref"):
            resolved = _resolve_client_media_ref(payload.get("input_audio_ref"))
            if resolved:
                scene_data["input_audio_path"] = resolved
        scene = media_factory.add_scene(project_id, scene_data)
        project = media_factory.get_project(project_id, include_scenes=True, include_logs=True)
        return _json_response({"success": True, "scene_id": scene["id"], "project": _attach_project_urls(project or {})})
    except ValueError as e:
        return _json_response({"success": False, "error": str(e)}, status_code=404)
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.patch("/api/projects/{project_id}/scenes/{scene_id}")
async def api_update_scene(project_id: int, scene_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        blocked = {"reference_image_path", "input_video_path", "input_audio_path", "keyframe_image_path", "rendered_clip_path", "refined_clip_path"}
        updates = {k: v for k, v in payload.items() if k not in blocked}
        if payload.get("reference_image_ref"):
            resolved = _resolve_client_media_ref(payload.get("reference_image_ref"))
            if resolved:
                updates["reference_image_path"] = resolved
        if payload.get("input_video_ref"):
            resolved = _resolve_client_media_ref(payload.get("input_video_ref"))
            if resolved:
                updates["input_video_path"] = resolved
        if payload.get("input_audio_ref"):
            resolved = _resolve_client_media_ref(payload.get("input_audio_ref"))
            if resolved:
                updates["input_audio_path"] = resolved
        scene = media_factory.update_scene(project_id, scene_id, updates)
        if not scene:
            return _json_response({"success": False, "error": f"Scene #{scene_id} not found"}, status_code=404)
        project = media_factory.get_project(project_id, include_scenes=True, include_logs=True)
        return _json_response({"success": True, "project": _attach_project_urls(project or {})})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.delete("/api/projects/{project_id}/scenes/{scene_id}")
def api_delete_scene(project_id: int, scene_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    deleted = media_factory.delete_scene(project_id, scene_id)
    if not deleted:
        return _json_response({"success": False, "error": f"Scene #{scene_id} not found"}, status_code=404)
    project = media_factory.get_project(project_id, include_scenes=True, include_logs=True)
    return _json_response({"success": True, "project": _attach_project_urls(project or {})})


@app.post("/api/projects/{project_id}/scenes/{scene_id}/enhance")
async def api_enhance_scene(project_id: int, scene_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        direction = str((payload or {}).get("direction") or "").strip()
        media_factory.enhance_scene_prompt(project_id, scene_id, direction=direction)
        project = media_factory.get_project(project_id, include_scenes=True, include_logs=True)
        return _json_response({"success": True, "project": _attach_project_urls(project or {})})
    except ValueError as e:
        return _json_response({"success": False, "error": str(e)}, status_code=404)
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/render")
def api_render_single_scene(project_id: int, scene_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    project = media_factory.get_project(project_id, include_scenes=True)
    if not project:
        return _json_response({"success": False, "error": f"Project #{project_id} not found"}, status_code=404)
    scene = next((s for s in project.get("scenes") or [] if int(s["id"]) == int(scene_id)), None)
    if not scene:
        return _json_response({"success": False, "error": f"Scene #{scene_id} not found"}, status_code=404)
    res = goal_loop.start_media_factory_goal(
        goal_loop.MediaGoalConfig(
            project_id=project_id,
            stages=("render",),
            scene_indices=(int(scene["scene_index"]),),
            force_rerender=True,
        )
    )
    return _json_response({"success": res.get("status") in {"started", "already_running"}, **res})


@app.post("/api/projects/{project_id}/goal/start")
async def api_project_goal_start(project_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        project = media_factory.get_project(project_id, include_scenes=False)
        if not project:
            return _json_response({"success": False, "error": f"Project #{project_id} not found"}, status_code=404)

        raw_stages = payload.get("stages") or ["plan", "render", "refine", "assemble"]
        stages = tuple(str(s).strip().lower() for s in raw_stages if str(s).strip())
        scene_indices = (
            tuple(int(i) for i in payload["scene_indices"])
            if isinstance(payload.get("scene_indices"), list) and payload["scene_indices"]
            else None
        )
        audio_path = _resolve_client_media_ref(payload.get("audio_ref")) or ""
        cfg = goal_loop.MediaGoalConfig(
            project_id=project_id,
            stages=stages or ("plan", "render", "refine", "assemble"),
            use_keyframes=bool(payload.get("use_keyframes", False)),
            scene_indices=scene_indices,
            max_scene_repair_attempts=max(1, min(5, int(payload.get("max_scene_repair_attempts") or 2))),
            refinement_direction=str(payload.get("refinement_direction") or ""),
            audio_path=audio_path,
            force_rerender=bool(payload.get("force_rerender", False)),
        )
        result = goal_loop.start_media_factory_goal(cfg)
        return _json_response({"success": result.get("status") in {"started", "already_running"}, **result})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.post("/api/projects/{project_id}/goal/stage")
async def api_project_goal_stage(project_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
        stage = str((payload or {}).get("stage") or "").strip().lower()
        if stage not in media_factory.VALID_STAGES:
            return _json_response({"success": False, "error": f"Invalid stage '{stage}'."}, status_code=400)
        scene_indices = (
            tuple(int(i) for i in payload["scene_indices"])
            if isinstance(payload.get("scene_indices"), list) and payload["scene_indices"]
            else None
        )
        cfg = goal_loop.MediaGoalConfig(
            project_id=project_id,
            stages=(stage,),
            use_keyframes=(stage == "keyframes"),
            scene_indices=scene_indices,
            refinement_direction=str(payload.get("refinement_direction") or ""),
            force_rerender=bool(payload.get("force_rerender", False)),
        )
        result = goal_loop.start_media_factory_goal(cfg)
        return _json_response({"success": result.get("status") in {"started", "already_running"}, **result})
    except Exception as e:
        return _json_response({"success": False, "error": internal_error_message(e)}, status_code=500)


@app.post("/api/projects/{project_id}/goal/pause")
def api_project_goal_pause(project_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    res = goal_loop.pause_media_factory_goal(project_id)
    return _json_response({"success": True, **res})


@app.post("/api/projects/{project_id}/goal/resume")
async def api_project_goal_resume(project_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    res = goal_loop.resume_media_factory_goal(project_id, use_keyframes=bool((payload or {}).get("use_keyframes", False)))
    return _json_response({"success": res.get("status") in {"started", "already_running"}, **res})


@app.post("/api/projects/{project_id}/goal/cancel")
def api_project_goal_cancel(project_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    res = goal_loop.cancel_media_factory_goal(project_id)
    return _json_response({"success": True, **res})


@app.get("/api/projects/{project_id}/goal/status")
def api_project_goal_status(project_id: int, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
    project = media_factory.get_project(project_id, include_scenes=True, include_logs=True)
    status = goal_loop.get_media_factory_goal_status(project_id)
    return _json_response(
        {
            "success": True,
            "goal": status,
            "project": _attach_project_urls(project) if project else None,
        }
    )


@app.get("/api/projects/{project_id}/assets/{subdir}/{filename}")
def api_serve_project_asset(project_id: int, subdir: str, filename: str, request: Request):
    if not _check_auth(request):
        return HTMLResponse("Not authenticated", status_code=401)
    if subdir not in _ALLOWED_PROJECT_ASSET_SUBDIRS:
        return HTMLResponse("Invalid asset directory", status_code=400)
    proj_root = _active_output_root() / "projects" / f"project_{int(project_id)}"
    target_dir = proj_root if subdir == "final" else (proj_root / subdir)
    file_path = contained_path(target_dir, filename)
    if file_path is None or not file_path.is_file():
        return HTMLResponse("Project asset not found", status_code=404)
    mime, _ = mimetypes.guess_type(file_path.name)
    from shared import content_files

    return content_files.response(file_path, request, mime or "application/octet-stream")


# ── Generated Media & Frontend Serving ────────────────────────────────────


@app.get("/media/{media_type}/{filename}")
def serve_media_file(media_type: str, filename: str, request: Request):
    """Serve generated video or image file from absolute output storage."""
    if not _check_auth(request):
        return HTMLResponse("Not authenticated", status_code=401)
    folder = "images" if media_type == "image" else "videos"
    file_path = contained_path(MEDIA_DIR / folder, filename)

    if file_path is None or not file_path.is_file():
        file_path = contained_path(MEDIA_DIR, filename)

    if file_path is None or not file_path.is_file():
        file_path = contained_path(MEDIA_DATA_DIR / "output" / folder, filename)
    if file_path is None or not file_path.is_file():
        from shared.content_library import managed_roots

        for root in managed_roots():
            candidate = contained_path(root / "media_generation_agent" / folder, filename)
            if candidate is not None and candidate.is_file():
                file_path = candidate
                break
    if file_path is None or not file_path.is_file():
        return HTMLResponse("Media file not found", status_code=404)

    try:
        mime, _ = mimetypes.guess_type(file_path.name)
        from shared import content_files

        return content_files.response(file_path, request, mime or "application/octet-stream")
    except Exception as exc:
        return HTMLResponse(f"Media file could not be opened. {internal_error_message(exc)}", status_code=500)


@app.get("/{full_path:path}")
def serve_frontend(full_path: str):
    """Serve index.html or other static files in frontend."""
    candidate = contained_path(FRONTEND_DIR, full_path) if full_path else None
    if candidate is not None and candidate.is_file():
        return FileResponse(candidate)

    try:
        html_content = INDEX_HTML_PATH.read_text(encoding="utf-8")
        response = HTMLResponse(html_content)
    except Exception:
        response = HTMLResponse("Media Factory Studio Web UI placeholder", status_code=200)

    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    return response
