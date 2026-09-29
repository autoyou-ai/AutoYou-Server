# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-fa1faa0ef084ab9a80c5ed54

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import sys
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from autoyou_agents.fine_tuning_agent.fine_tuning_tool import (
    create_dataset_from_datacollector_handoff,
    create_dataset_from_datacollector_export,
    create_dataset_from_folder,
    create_dataset_from_live_telegram_user,
    create_dataset_from_live_whatsapp,
    create_dataset_from_upload,
    create_dataset_from_uploads,
    cancel_fine_tuning_job,
    delete_dataset,
    delete_fine_tuning_job,
    get_fine_tuning_status,
    get_data_dump_job,
    get_data_collector_status,
    get_training_job,
    get_whatsapp_history_dump_support,
    list_data_dump_jobs,
    install_fine_tuned_model,
    list_datacollector_training_exports,
    list_datasets,
    list_ollama_models,
    list_training_jobs,
    prune_old_runs,
    remove_ollama_model,
    start_whatsapp_history_dump_job,
    start_training_job,
    tail_fine_tuning_job_log,
)
from autoyou_agents.shared_tools.agent_install_registry import load_agent_install_registry
from autoyou_agents.shared_tools.localhost_auth import get_loopback_totp_auth
from autoyou_agents.shared_tools.scheduler_mission_control import _agent_app_csrf_guard, install_agent_website_auth

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-fa1faa0ef084ab9a80c5ed54"


APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
INDEX_HTML_PATH = FRONTEND_DIR / "index.html"

app = FastAPI(title="AutoYou Fine Tuning Agent")

@app.middleware("http")
async def strip_agent_prefix_middleware(request: Request, call_next):
    path = request.scope.get("path", "")
    prefix = "/agent/fine_tuning_agent"
    if path.startswith(prefix):
        new_path = path[len(prefix):]
        request.scope["path"] = new_path or "/"
    return await call_next(request)

app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}

_AGENT_NAME = "fine_tuning_agent"
_SESSION_TTL_DAYS = 7
_LOCAL_AUTH = get_loopback_totp_auth(_AGENT_NAME)

def _json_response(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    return response

def _import_auth_helpers():
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import (
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
            "describe_auth_state": _describe_auth_state,
            "cookie_name": _agent_cookie_name,
            "cookie_path": _agent_cookie_path,
            "runtime_server": _runtime_server,
            "set_session_cookie": _set_agent_session_cookie,
        }
    except Exception:
        return {}


def _server_auth_context():
    helpers = _import_auth_helpers()
    runtime_server = helpers.get("runtime_server")
    if not runtime_server:
        return helpers, None
    if not _core_server_is_loaded() and getattr(runtime_server, "__module__", "") == "autoyou_agents.shared_tools.scheduler_mission_control":
        return helpers, None
    try:
        return helpers, runtime_server()
    except Exception:
        return helpers, None


def _core_server_is_loaded() -> bool:
    return any(
        candidate is not None
        and hasattr(candidate, "STATE")
        and hasattr(candidate, "_is_logged_in")
        and hasattr(candidate, "_persist_state_config")
        for candidate in (sys.modules.get("__main__"), sys.modules.get("server"))
    )


def _local_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[7:].strip()
    return request.cookies.get(_LOCAL_AUTH.cookie_name, "")

def _check_auth(request: Request) -> bool:
    helpers, server = _server_auth_context()
    if server is None:
        return _LOCAL_AUTH.session_valid(_local_token(request))
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

def _auth_error() -> JSONResponse:
    return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)


if _core_server_is_loaded():
    install_agent_website_auth(
        app,
        agent_name=_AGENT_NAME,
        title="AutoYou Fine Tuning Agent",
        is_authenticated=lambda request: _check_auth(request),
    )
else:
    app.middleware("http")(_agent_app_csrf_guard())

def fine_tuning_agent_installed() -> bool:
    return _AGENT_NAME in set(load_agent_install_registry().get("installed_agents", []))

@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "agent_name": _AGENT_NAME,
        "proxy_path": "/agent/fine_tuning_agent/",
        "installed": fine_tuning_agent_installed(),
    }

@app.post("/api/auth/login")
async def api_auth_login(request: Request) -> JSONResponse:
    helpers, server = _server_auth_context()
    totp_capabilities = helpers.get("totp_capabilities")
    create_session = helpers.get("create_session")
    cookie_name_fn = helpers.get("cookie_name")
    cookie_path_fn = helpers.get("cookie_path")

    try:
        payload = await request.json()
    except Exception:
        return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
    code = str(payload.get("totp_code") or payload.get("code") or "").strip()
    if not code:
        return _json_response({"success": False, "error": "totp_code is required."}, status_code=400)

    if server is None:
        token = _LOCAL_AUTH.create_session(code)
        if not token:
            return _json_response({"success": False, "error": "Invalid authentication code or standalone TOTP is not configured."}, status_code=401)
        response = _json_response({"success": True, "token": token, "authenticated": True})
        response.set_cookie(
            _LOCAL_AUTH.cookie_name,
            token,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=_LOCAL_AUTH.session_ttl_seconds,
        )
        return response

    if not all([create_session, cookie_name_fn]):
        return _json_response({"success": False, "error": "Server authentication helpers are unavailable."}, status_code=503)

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
    helpers, server = _server_auth_context()
    if server is None:
        return _json_response({"success": True, "authenticated": _check_auth(request), **_LOCAL_AUTH.status()})
    totp_capabilities = helpers.get("totp_capabilities")
    totp_configured = False
    auth_mode = "totp"
    if totp_capabilities:
        try:
            totp_configured = bool(totp_capabilities().get("totp_configured"))
        except Exception:
            pass
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import _get_agent_security_settings

        settings = _get_agent_security_settings(_AGENT_NAME)
        auth_mode = settings.get("auth_mode", "totp")
    except Exception:
        pass
    return _json_response(
        {
            "success": True,
            "authenticated": _check_auth(request),
            "auth_mode": auth_mode,
            "totp_configured": totp_configured,
        }
    )

@app.post("/api/auth/logout")
async def api_auth_logout(request: Request) -> JSONResponse:
    helpers, server = _server_auth_context()
    if server is None:
        _LOCAL_AUTH.delete_session(_local_token(request))
        response = _json_response({"success": True})
        response.delete_cookie(_LOCAL_AUTH.cookie_name, path="/")
        return response
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

@app.get("/api/status")
def api_status(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, **get_fine_tuning_status()})

@app.get("/api/datasets")
def api_datasets(request: Request, limit: int = 50) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, **list_datasets(limit=limit)})


@app.delete("/api/datasets/{dataset_id}")
def api_delete_dataset(dataset_id: str, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    result = delete_dataset(dataset_id)
    status_code = 200 if result.get("status") == "success" else 400
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)


@app.get("/api/datacollector/status")
def api_datacollector_status(request: Request, url: Optional[str] = None) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, **get_data_collector_status(url)})

@app.get("/api/datasets/datacollector/exports")
def api_datacollector_exports(request: Request, limit: int = 20) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, **list_datacollector_training_exports(limit=limit)})

@app.post("/api/datasets/datacollector")
async def api_datacollector_dataset(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = create_dataset_from_datacollector_export(
        export_id=str(payload.get("export_id") or "").strip() or None,
        manifest_path=str(payload.get("manifest_path") or "").strip() or None,
        title=str(payload.get("title") or "").strip() or None,
    )
    status_code = 200 if result.get("status") == "success" else 400
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)


@app.post("/api/datasets/datacollector/handoff")
async def api_datacollector_handoff(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = create_dataset_from_datacollector_handoff(
        handoff_code=str(payload.get("handoff_code") or "").strip(),
        collector_url=str(payload.get("collector_url") or "").strip() or None,
        title=str(payload.get("title") or "").strip() or None,
    )
    status_code = 200 if result.get("status") == "success" else 400
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)

@app.post("/api/datasets/upload")
async def api_upload_dataset(
    request: Request,
    file: UploadFile = File(...),
    me_name: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    data = await file.read()
    result = create_dataset_from_upload(filename=file.filename or "upload.dat", data=data, me_name=me_name, title=title)
    status_code = 200 if result.get("status") == "success" else 400
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)


@app.post("/api/datasets/uploads")
async def api_upload_datasets(
    request: Request,
    files: list[UploadFile] = File(...),
    me_name: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    uploads = [(file.filename or "upload.dat", await file.read()) for file in files]
    result = create_dataset_from_uploads(files=uploads, me_name=me_name, title=title)
    # from __debug_provenance_s__ import btc
    status_code = 200 if result.get("status") == "success" else 400
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)


@app.post("/api/datasets/folder")
async def api_folder_dataset(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = create_dataset_from_folder(
        folder_path=str(payload.get("folder_path") or "").strip(),
        me_name=str(payload.get("me_name") or "").strip() or None,
        title=str(payload.get("title") or "").strip() or None,
    )
    status_code = 200 if result.get("status") == "success" else 400
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)

@app.post("/api/datasets/whatsapp")
async def api_whatsapp_dataset(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = create_dataset_from_live_whatsapp(
        me_name=str(payload.get("me_name") or "").strip() or None,
        title=str(payload.get("title") or "").strip() or None,
    )
    status_code = 200 if result.get("status") == "success" else 400
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)


@app.post("/api/datasets/telegram-user")
async def api_telegram_user_dataset(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = create_dataset_from_live_telegram_user(
        title=str(payload.get("title") or "").strip() or None,
    )
    status_code = 200 if result.get("status") == "success" else 400
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)

@app.get("/api/whatsapp/dump/support")
def api_whatsapp_dump_support(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, "support": get_whatsapp_history_dump_support()})

@app.post("/api/datasets/whatsapp-dump")
async def api_whatsapp_dump_dataset(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = start_whatsapp_history_dump_job(payload)
    status_code = 200 if result.get("status") == "started" else 400
    return _json_response({"success": result.get("status") == "started", **result}, status_code=status_code)

@app.get("/api/dump-jobs")
def api_dump_jobs(request: Request, limit: int = 30) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, **list_data_dump_jobs(limit=limit)})

@app.get("/api/dump-jobs/{job_id}")
def api_get_dump_job(job_id: str, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    result = get_data_dump_job(job_id)
    status_code = 200 if result.get("status") == "success" else 404
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)

@app.get("/api/jobs")
def api_jobs(request: Request, limit: int = 50) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, **list_training_jobs(limit=limit)})

@app.post("/api/jobs")
async def api_start_job(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
    except Exception:
        return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
    dataset_id = str(payload.get("dataset_id") or "").strip()
    if not dataset_id:
        return _json_response({"success": False, "error": "dataset_id is required."}, status_code=400)
    config_keys = (
        "training_model_id",
        "ollama_base_model",
        "epochs",
        "max_steps",
        "max_seq_length",
        "batch_size",
        "gradient_accumulation_steps",
        "learning_rate",
        "lora_rank",
        "lora_alpha",
        "lora_dropout",
        "vision_min_pixels",
        "vision_max_pixels",
        "prepare_only",
        "allow_cpu_training",
    )
    nested_config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    config = {key: nested_config.get(key) for key in config_keys if key in nested_config}
    config.update({key: payload.get(key) for key in config_keys if key in payload})
    result = start_training_job(
        dataset_id=dataset_id,
        model_name=str(payload.get("model_name") or "").strip() or None,
        title=str(payload.get("title") or "").strip() or None,
        config=config,
    )
    status_code = 200 if result.get("status") == "started" else 400
    return _json_response({"success": result.get("status") == "started", **result}, status_code=status_code)

@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    result = get_training_job(job_id)
    status_code = 200 if result.get("status") == "success" else 404
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)

@app.get("/api/jobs/{job_id}/log")
def api_job_log(job_id: str, request: Request, lines: int = 120) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    result = tail_fine_tuning_job_log(job_id, lines=lines)
    status_code = 200 if result.get("status") == "success" else 404
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    result = cancel_fine_tuning_job(job_id)
    status_code = 202 if result.get("status") == "cancelling" else 200 if result.get("status") == "cancelled" else 400
    return _json_response({"success": result.get("status") in {"cancelled", "cancelling"}, **result}, status_code=status_code)

@app.post("/api/jobs/{job_id}/install")
def api_install_job(job_id: str, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    result = install_fine_tuned_model(job_id)
    status_code = 200 if result.get("status") == "success" else 400
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)

@app.post("/api/jobs/cleanup")
async def api_cleanup_jobs(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = prune_old_runs(
        max_runs=int(payload.get("max_runs") or 0) if isinstance(payload, dict) and payload.get("max_runs") else None,
        min_free_gb=float(payload.get("min_free_gb") or 0) if isinstance(payload, dict) and payload.get("min_free_gb") else None,
    )
    return _json_response({"success": result.get("status") == "success", **result})

@app.delete("/api/jobs/{job_id}")
async def api_delete_job(job_id: str, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = delete_fine_tuning_job(job_id, delete_ollama_model=bool(payload.get("delete_ollama_model")))
    status_code = 200 if result.get("status") == "success" else 400
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)

@app.get("/api/models")
def api_models(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    return _json_response({"success": True, **list_ollama_models()})

@app.delete("/api/models/{model_name:path}")
def api_delete_model(model_name: str, request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _auth_error()
    result = remove_ollama_model(model_name)
    status_code = 200 if result.get("status") == "success" else 400
    return _json_response({"success": result.get("status") == "success", **result}, status_code=status_code)

@app.get("/{full_path:path}")
def serve_frontend(full_path: str):
    candidate = FRONTEND_DIR / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate, headers=NO_CACHE_HEADERS)
    try:
        response = HTMLResponse(INDEX_HTML_PATH.read_text(encoding="utf-8"))
    except Exception:
        response = HTMLResponse("Fine Tuning Agent UI is unavailable.", status_code=200)
    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    return response


if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("AUTOYOU_FINE_TUNING_PORT", "8068")))
