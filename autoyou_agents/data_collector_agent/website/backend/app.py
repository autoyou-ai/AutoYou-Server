# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-548701397bbdf0798b252ce4

"""Loopback-only Data Collector Agent website."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from autoyou_agents.data_collector_agent.collector import (
    AGENT_NAME,
    CollectionRunBusy,
    application_capabilities,
    begin_collection_run,
    collect,
    collection_cancellation_requested,
    collection_run_status,
    create_training_export,
    export_zip,
    finish_collection_run,
    list_training_exports,
    list_timeline,
    load_job,
    public_status,
    request_collection_cancellation,
    rebuild,
    update_job,
)
from autoyou_agents.shared_tools.localhost_auth import get_loopback_totp_auth
from autoyou_agents.shared_tools.scheduler_mission_control import _agent_app_csrf_guard, install_agent_website_auth

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-548701397bbdf0798b252ce4"


APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
ASSETS_DIR = FRONTEND_DIR / "assets"
INDEX_HTML_PATH = FRONTEND_DIR / "index.html"
_AGENT_NAME = AGENT_NAME
_SESSION_TTL_DAYS = 7
_LOCAL_AUTH = get_loopback_totp_auth(_AGENT_NAME)
_RUN_LOCK = threading.Lock()
_RUN_STATE: Dict[str, Any] = {"state": "idle"}
_RUN_CANCEL_EVENT: Optional[threading.Event] = None
_HANDOFF_LOCK = threading.Lock()
_HANDOFFS: Dict[str, Dict[str, Any]] = {}
_NO_CACHE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}

def _json(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    for name, value in _NO_CACHE.items():
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


def _core_server_is_loaded() -> bool:
    return any(
        candidate is not None
        and hasattr(candidate, "STATE")
        and hasattr(candidate, "_is_logged_in")
        and hasattr(candidate, "_persist_state_config")
        for candidate in (sys.modules.get("__main__"), sys.modules.get("server"))
    )


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


def _local_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[7:].strip()
    return request.cookies.get(_LOCAL_AUTH.cookie_name, "")


def _is_authenticated(request: Request) -> bool:
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

        if _get_agent_security_settings(_AGENT_NAME).get("auth_mode") == "open":
            return True
    except Exception:
        pass
    try:
        cookie_token = request.cookies.get(cookie_name_fn(_AGENT_NAME), "")
        if cookie_token and session_valid(_AGENT_NAME, cookie_token):
            return True
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            bearer = authorization[7:].strip()
            return bool(bearer and session_valid(_AGENT_NAME, bearer))
    except Exception:
        pass
    return False


def _auth_error() -> JSONResponse:
    return _json({"success": False, "error": "Not authenticated"}, 401)


app = FastAPI(title="AutoYou Data Collector Agent")


@app.middleware("http")
async def strip_agent_prefix_middleware(request: Request, call_next):
    prefix = f"/agent/{_AGENT_NAME}"
    path = request.scope.get("path", "")
    if path.startswith(prefix):
        request.scope["path"] = path[len(prefix):] or "/"
    return await call_next(request)


if _core_server_is_loaded():
    install_agent_website_auth(
        app,
        agent_name=_AGENT_NAME,
        title="AutoYou Data Collector Agent",
        is_authenticated=lambda request: _is_authenticated(request),
    )
else:
    app.middleware("http")(_agent_app_csrf_guard())


app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else "127.0.0.1"
    # from __debug_provenance_a__ import schedule
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


async def _payload(request: Request) -> Dict[str, Any]:
    try:
        value = await request.json()
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _run(action: str, job: Dict[str, Any], cancel_event: threading.Event, run_id: str) -> None:
    global _RUN_CANCEL_EVENT, _RUN_STATE
    try:
        result = (
            collect(job, cancel_check=lambda: cancel_event.is_set() or collection_cancellation_requested(run_id))
            if action == "collect"
            else rebuild(job, cancel_check=lambda: cancel_event.is_set() or collection_cancellation_requested(run_id))
        )
        state = {
            "state": "cancelled" if result.get("status") == "cancelled" else "complete",
            "action": action,
            "finished_at": time.time(),
            "result": result,
        }
    except Exception as exc:
        state = {
            "state": "cancelled" if cancel_event.is_set() else "error",
            "action": action,
            "finished_at": time.time(),
            "error": str(exc),
        }
    finish_collection_run(run_id)
    with _RUN_LOCK:
        if _RUN_CANCEL_EVENT is cancel_event:
            _RUN_CANCEL_EVENT = None
        _RUN_STATE = state


def _start_run(action: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    global _RUN_CANCEL_EVENT, _RUN_STATE
    with _RUN_LOCK:
        if _RUN_STATE.get("state") in {"running", "cancelling"}:
            return {"status": "error", "message": "A collection operation is already running."}
        allowed = {
            "apps",
            "source_roots",
            "project_filters",
            "worktrees",
            "mode",
            "direction",
            "copy_raw",
            "output_root",
            "machine_id",
            "whatsapp",
            "telegram",
        }
        job = update_job({key: value for key, value in changes.items() if key in allowed}) if changes else load_job()
        try:
            run_id = begin_collection_run()
        except CollectionRunBusy as exc:
            return {"status": "error", "message": str(exc)}
        started_at = time.time()
        cancel_event = threading.Event()
        _RUN_CANCEL_EVENT = cancel_event
        _RUN_STATE = {"state": "running", "action": action, "started_at": started_at}
        thread = threading.Thread(
            target=_run,
            args=(action, job, cancel_event, run_id),
            daemon=True,
            name=f"data-collector-{action}",
        )
        thread.start()
    return {"status": "started", "run": dict(_RUN_STATE)}


def _cancel_run() -> Dict[str, Any]:
    global _RUN_STATE
    with _RUN_LOCK:
        if _RUN_STATE.get("state") in {"running", "cancelling"} and _RUN_CANCEL_EVENT is not None:
            _RUN_CANCEL_EVENT.set()
            request_collection_cancellation()
            _RUN_STATE = {
                **_RUN_STATE,
                "state": "cancelling",
                "cancel_requested_at": time.time(),
            }
            return {"status": "cancelling", "run": dict(_RUN_STATE)}
        result = request_collection_cancellation()
        if result.get("status") == "cancelling":
            return {**result, "run": collection_run_status()}
        return result


def _prune_handoffs_locked() -> None:
    now = time.time()
    for code in [code for code, item in _HANDOFFS.items() if item["expires_at"] <= now]:
        _HANDOFFS.pop(code, None)


@app.get("/health")
def health() -> Dict[str, Any]:
    return public_status()


@app.get("/api/auth/status")
def auth_status(request: Request) -> JSONResponse:
    helpers, server = _server_auth_context()
    if server is None:
        return _json({"success": True, "authenticated": _is_authenticated(request), **_LOCAL_AUTH.status()})
    totp_configured = False
    totp_capabilities = helpers.get("totp_capabilities")
    if totp_capabilities:
        try:
            totp_configured = bool(totp_capabilities().get("totp_configured"))
        except Exception:
            pass
    auth_mode = "totp"
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import _get_agent_security_settings

        auth_mode = _get_agent_security_settings(_AGENT_NAME).get("auth_mode", "totp")
    except Exception:
        pass
    return _json(
        {
            "success": True,
            "authenticated": _is_authenticated(request),
            "auth_mode": auth_mode,
            "totp_configured": totp_configured,
        }
    )


@app.post("/api/auth/login")
async def auth_login(request: Request) -> JSONResponse:
    payload = await _payload(request)
    code = str(payload.get("totp_code") or payload.get("code") or "").strip()
    if not code:
        return _json({"success": False, "error": "totp_code is required."}, 400)
    helpers, server = _server_auth_context()
    if server is None:
        token = _LOCAL_AUTH.create_session(code)
        if not token:
            return _json({"success": False, "error": "Invalid authentication code or standalone TOTP is not configured."}, 401)
        response = _json({"success": True, "authenticated": True, "token": token})
        response.set_cookie(_LOCAL_AUTH.cookie_name, token, httponly=True, samesite="lax", max_age=_LOCAL_AUTH.session_ttl_seconds, path="/")
        return response

    create_session = helpers.get("create_session")
    cookie_name_fn = helpers.get("cookie_name")
    if not create_session or not cookie_name_fn:
        return _json({"success": False, "error": "Server authentication helpers are unavailable."}, 503)
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
        totp_capabilities = helpers.get("totp_capabilities")
        if not (totp_capabilities() if totp_capabilities else {}).get("totp_configured"):
            return _json({"success": False, "error": "2FA is not configured on this server."}, 400)
        try:
            cfg = server.STATE.config or server._default_config()
            verified = server._verify_totp_secret(server._get_pairing_totp_secret(cfg), code)
        except Exception:
            verified = False
    if not verified:
        return _json({"success": False, "error": "Invalid authentication code."}, 401)
    settings: Dict[str, Any] = {"session_ttl_days": _SESSION_TTL_DAYS}
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import _get_agent_security_settings

        settings = _get_agent_security_settings(_AGENT_NAME)
    except Exception:
        pass
    token = create_session(_AGENT_NAME, settings.get("session_ttl_days", _SESSION_TTL_DAYS))
    response = _json({"success": True, "authenticated": True, "token": token})
    set_session_cookie = helpers.get("set_session_cookie")
    if set_session_cookie:
        set_session_cookie(response, request, _AGENT_NAME, token, settings)
    else:
        response.set_cookie(cookie_name_fn(_AGENT_NAME), token, httponly=True, samesite="lax", path="/", max_age=int(settings.get("session_ttl_days", _SESSION_TTL_DAYS) * 86400))
    return response


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> JSONResponse:
    helpers, server = _server_auth_context()
    if server is None:
        _LOCAL_AUTH.delete_session(_local_token(request))
        response = _json({"success": True})
        response.delete_cookie(_LOCAL_AUTH.cookie_name, path="/")
        return response
    delete_session = helpers.get("delete_session")
    cookie_name_fn = helpers.get("cookie_name")
    if delete_session and cookie_name_fn:
        for token in (request.cookies.get(cookie_name_fn(_AGENT_NAME), ""), request.headers.get("Authorization", "").removeprefix("Bearer ").strip()):
            if token:
                try:
                    delete_session(_AGENT_NAME, token)
                except Exception:
                    pass
    response = _json({"success": True})
    if cookie_name_fn:
        cookie_path_fn = helpers.get("cookie_path")
        response.delete_cookie(cookie_name_fn(_AGENT_NAME), path=cookie_path_fn(_AGENT_NAME) if cookie_path_fn else f"/agent/{_AGENT_NAME}")
        response.delete_cookie(cookie_name_fn(_AGENT_NAME), path="/")
    return response


@app.get("/api/status")
def status(request: Request) -> JSONResponse:
    if not _is_authenticated(request):
        return _auth_error()
    with _RUN_LOCK:
        run_state = dict(_RUN_STATE)
    if run_state.get("state") not in {"running", "cancelling"}:
        external_run = collection_run_status()
        if external_run.get("state") in {"running", "cancelling"}:
            run_state = external_run
    return _json({"success": True, **public_status(), "run": run_state, "job": load_job()})


@app.get("/api/job")
def get_job(request: Request) -> JSONResponse:
    if not _is_authenticated(request):
        return _auth_error()
    return _json({"success": True, "job": load_job()})


@app.get("/api/capabilities")
def capabilities(request: Request) -> JSONResponse:
    if not _is_authenticated(request):
        return _auth_error()
    return _json({"success": True, **application_capabilities()})


@app.get("/api/timeline")
def timeline(
    request: Request,
    app_id: str = "",
    machine_id: str = "",
    project: str = "",
    target: str = "",
    direction: str = "both",
    offset: int = 0,
    limit: int = 100,
) -> JSONResponse:
    if not _is_authenticated(request):
        return _auth_error()
    try:
        result = list_timeline(
            app_id=app_id,
            machine_id=machine_id,
            project=project,
            target=target,
            direction=direction,
            offset=offset,
            limit=limit,
        )
    except ValueError as exc:
        return _json({"success": False, "error": str(exc)}, 400)
    return _json({"success": True, **result})


@app.put("/api/job")
async def put_job(request: Request) -> JSONResponse:
    if not _is_authenticated(request):
        return _auth_error()
    try:
        job = update_job(await _payload(request))
    except ValueError as exc:
        return _json({"success": False, "error": str(exc)}, 400)
    return _json({"success": True, "job": job})


@app.post("/api/collect")
async def start_collect(request: Request) -> JSONResponse:
    if not _is_authenticated(request):
        return _auth_error()
    result = _start_run("collect", await _payload(request))
    return _json({"success": result.get("status") == "started", **result}, 202 if result.get("status") == "started" else 409)


@app.post("/api/rebuild")
async def start_rebuild(request: Request) -> JSONResponse:
    if not _is_authenticated(request):
        return _auth_error()
    result = _start_run("rebuild", await _payload(request))
    return _json({"success": result.get("status") == "started", **result}, 202 if result.get("status") == "started" else 409)


@app.post("/api/run/cancel")
def cancel_run(request: Request) -> JSONResponse:
    if not _is_authenticated(request):
        return _auth_error()
    result = _cancel_run()
    status_code = 202 if result.get("status") == "cancelling" else 409
    return _json({"success": result.get("status") == "cancelling", **result}, status_code)


@app.get("/api/exports")
def exports(request: Request, limit: int = 30) -> JSONResponse:
    if not _is_authenticated(request):
        return _auth_error()
    return _json({"success": True, **list_training_exports(limit=limit)})


@app.post("/api/exports")
async def create_export(request: Request) -> JSONResponse:
    if not _is_authenticated(request):
        return _auth_error()
    payload = await _payload(request)
    result = create_training_export(title=str(payload.get("title") or "").strip() or None)
    return _json({"success": result.get("status") == "success", **result}, 200 if result.get("status") == "success" else 400)


@app.post("/api/handoffs")
async def create_handoff(request: Request) -> JSONResponse:
    if not _is_authenticated(request):
        return _auth_error()
    payload = await _payload(request)
    export_id = str(payload.get("export_id") or "").strip()
    if not export_id:
        return _json({"success": False, "error": "export_id is required."}, 400)
    exports = {item.get("id") for item in list_training_exports(limit=100).get("exports", [])}
    if export_id not in exports:
        return _json({"success": False, "error": "Training export was not found."}, 404)
    code = secrets.token_urlsafe(24)
    expires_at = time.time() + 5 * 60
    with _HANDOFF_LOCK:
        _prune_handoffs_locked()
        _HANDOFFS[code] = {"export_id": export_id, "expires_at": expires_at}
    return _json({"success": True, "code": code, "export_id": export_id, "expires_in_seconds": 300})


@app.post("/api/handoffs/consume")
async def consume_handoff(request: Request) -> Response:
    if not _is_loopback(request):
        return _json({"success": False, "error": "Loopback access is required."}, 403)
    payload = await _payload(request)
    code = str(payload.get("code") or "").strip()
    with _HANDOFF_LOCK:
        _prune_handoffs_locked()
        handoff = _HANDOFFS.pop(code, None)
    if not handoff:
        return _json({"success": False, "error": "The handoff code is invalid or expired."}, 401)
    try:
        archive = export_zip(str(handoff["export_id"]))
    except FileNotFoundError:
        return _json({"success": False, "error": "The selected export no longer exists."}, 404)
    return Response(content=archive, media_type="application/zip", headers={"Cache-Control": "no-store", "Content-Disposition": 'attachment; filename="data-collector-training-export.zip"'})


@app.get("/{full_path:path}")
def serve_frontend(full_path: str) -> Response:
    candidate = FRONTEND_DIR / full_path
    if full_path and candidate.is_file():
        return Response(content=candidate.read_bytes(), headers=_NO_CACHE)
    response = HTMLResponse(INDEX_HTML_PATH.read_text(encoding="utf-8"))
    for name, value in _NO_CACHE.items():
        response.headers[name] = value
    return response


if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("AUTOYOU_DATA_COLLECTOR_PORT", "18067")))
