# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""FastAPI backend application for Prompts Agent ("My Prompt")."""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from shared.secure_storage import secure_storage_enabled

from autoyou_agents.prompts_agent.collector import get_global_collector
from autoyou_agents.prompts_agent.config import (
    AGENT_NAME,
    APP_DISPLAY_NAME,
    DATA_DIR,
    SOFTWARE_COLORS,
    SOFTWARE_LABELS,
    SUPPORTED_SOFTWARES,
)
from autoyou_agents.prompts_agent.db import PromptsDatabase, utc_now

logger = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
INDEX_HTML = FRONTEND_DIR / "index.html"

# Active OTP session tokens: {token: expire_timestamp}
_OTP_SESSIONS: Dict[str, float] = {}
_OTP_LOCK = threading.Lock()
_OTP_TTL_SECONDS = 3600  # 1 hour validity


def _create_otp_session() -> str:
    token = secrets.token_hex(24)
    with _OTP_LOCK:
        _OTP_SESSIONS[token] = time.time() + _OTP_TTL_SECONDS
    return token


def _is_otp_authenticated(token: Optional[str]) -> bool:
    if not token:
        return False
    with _OTP_LOCK:
        exp = _OTP_SESSIONS.get(token)
        if exp and exp > time.time():
            return True
        elif exp:
            del _OTP_SESSIONS[token]
    return False


def _extract_auth_token(request: Request) -> Optional[str]:
    # Check X-OTP-Token header
    h = request.headers.get("X-OTP-Token")
    if h:
        return h.strip()
    # Check Bearer token
    auth_h = request.headers.get("Authorization", "")
    if auth_h.startswith("Bearer "):
        return auth_h[7:].strip()
    # Check cookie
    cookie = request.cookies.get("autoyou_prompts_otp")
    if cookie:
        return cookie.strip()
    return None


def get_db() -> PromptsDatabase:
    return get_global_collector().db


app = FastAPI(
    title=APP_DISPLAY_NAME,
    description="Universal prompt harvester and Obsidian-style mind map visualizer",
    version="1.0.0",
)

# The frontend uses relative same-origin requests, including through the
# server's registered proxy. Private prompt history must not be readable by
# arbitrary websites through a credentialed wildcard CORS policy.


# ─── Public Endpoints ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    if INDEX_HTML.is_file():
        return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>My Prompt</h1><p>Frontend assets not found.</p>")


@app.get("/api/status")
async def get_status():
    collector = get_global_collector()
    return {
        "status": "online",
        "app_name": APP_DISPLAY_NAME,
        "agent_name": AGENT_NAME,
        "is_syncing": collector.is_syncing,
        "utc_time": utc_now(),
    }


@app.get("/api/stats")
async def get_stats():
    db = get_db()
    stats = db.get_stats()
    return {
        "status": "success",
        **stats,
        "software_labels": SOFTWARE_LABELS,
        "software_colors": SOFTWARE_COLORS,
    }


@app.get("/api/prompts")
async def list_prompts(
    q: Optional[str] = Query(None, description="Search query"),
    software: Optional[str] = Query(None, description="Software filter"),
    project: Optional[str] = Query(None, description="Project filter"),
    date_from: Optional[str] = Query(None, description="Start date ISO"),
    date_to: Optional[str] = Query(None, description="End date ISO"),
    has_attachment: Optional[bool] = Query(None, description="Has attachment"),
    has_output: Optional[bool] = Query(None, description="Has assistant output"),
    is_favorite: Optional[bool] = Query(None, description="Filter favorites only"),
    sort: str = Query("newest", description="Sort order: newest or oldest"),
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    db = get_db()
    prompts, total = db.get_prompts(
        query=q,
        software=software,
        project=project,
        date_from=date_from,
        date_to=date_to,
        has_attachment=has_attachment,
        has_output=has_output,
        is_favorite=is_favorite,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return {
        "status": "success",
        "total": total,
        "count": len(prompts),
        "limit": limit,
        "offset": offset,
        "prompts": prompts,
    }


@app.get("/api/prompts/{prompt_id}")
async def get_prompt_detail(prompt_id: str):
    db = get_db()
    prompt = db.get_prompt_by_id(prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"status": "success", "prompt": prompt}


@app.post("/api/prompts/{prompt_id}/favorite")
async def toggle_prompt_favorite(prompt_id: str):
    db = get_db()
    is_fav = db.toggle_favorite(prompt_id)
    return {"status": "success", "prompt_id": prompt_id, "is_favorite": is_fav}


@app.post("/api/prompts/{prompt_id}/annotate")
async def annotate_prompt(prompt_id: str, payload: Dict[str, Any]):
    note = str(payload.get("annotation") or payload.get("note") or "").strip()
    db = get_db()
    db.update_annotation(prompt_id, note)
    return {"status": "success", "prompt_id": prompt_id, "annotation": note}


@app.get("/api/sessions")
async def list_sessions(
    software: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    db = get_db()
    prompts, total = db.get_prompts(
        software=software,
        limit=limit,
        offset=offset,
    )
    return {"status": "success", "sessions": prompts, "total": total}


@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str):
    db = get_db()
    session = db.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "success", "session": session}


@app.get("/api/projects")
async def list_projects():
    db = get_db()
    projects = db.get_projects()
    return {"status": "success", "projects": projects}


@app.get("/api/graph")
async def get_mind_map_graph(
    software: Optional[str] = None,
    max_nodes: int = 150,
):
    db = get_db()
    graph_data = db.get_mind_map_graph(software=software, max_nodes=max_nodes)
    return {
        "status": "success",
        "graph": graph_data,
        "software_colors": SOFTWARE_COLORS,
    }


@app.post("/api/sync")
async def trigger_collection_sync(
    full_resync: bool = Query(False, description="Force full re-scan"),
):
    collector = get_global_collector()
    if collector.is_syncing:
        return JSONResponse(
            {"status": "busy", "message": "Collection sync is already running."},
            status_code=409,
        )

    # Launch sync in background worker thread
    def _worker():
        collector.scan_and_collect(full_resync=full_resync)

    threading.Thread(target=_worker, daemon=True, name="PromptsManualSyncWorker").start()
    return {"status": "started", "message": "Harvesting process started in background."}


@app.get("/api/sync/status")
async def get_sync_status():
    collector = get_global_collector()
    stats = collector.db.get_stats()
    return {
        "status": "success",
        "is_syncing": collector.is_syncing,
        "last_sync": stats.get("last_sync"),
    }


# ─── External Push / Software Hook Ingestion ────────────────────────────────

@app.post("/api/push")
async def ingest_hook_prompt(
    request: Request,
    payload: Dict[str, Any],
):
    """Receive a prompt pushed from an external tool, IDE hook, or CLI script."""
    db = get_db()

    # Authenticate token if hooks are registered
    hooks = db.list_hooks()
    if hooks:
        raw_token = request.headers.get("X-Prompt-Token") or request.headers.get("Authorization", "").replace("Bearer ", "").strip()
        validated = db.validate_hook_token(raw_token)
        if not validated:
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing X-Prompt-Token header for software hook.",
            )

    collector = get_global_collector()
    try:
        result = collector.ingest_push_prompt(payload)
        return {"status": "success", **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Error ingesting push prompt: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ─── OTP-Gated Settings & Management Endpoints ──────────────────────────────

@app.get("/api/settings")
async def get_public_settings(request: Request):
    """Return settings state and whether the current session is unlocked via OTP."""
    db = get_db()
    token = _extract_auth_token(request)
    is_unlocked = _is_otp_authenticated(token)

    settings = db.get_all_settings()
    # Mask private hash
    settings.pop("otp_pin_hash", None)
    settings.pop("default_otp_pin", None)
    settings["encrypted_storage"] = secure_storage_enabled()

    return {
        "status": "success",
        "is_unlocked": is_unlocked,
        "settings": settings,
        "supported_softwares": SUPPORTED_SOFTWARES,
    }


@app.post("/api/settings/verify-otp")
async def verify_otp(payload: Dict[str, Any]):
    """Verify numeric OTP / Passcode and issue session token."""
    pin = str(payload.get("pin") or "").strip()
    if not pin:
        raise HTTPException(status_code=400, detail="PIN is required")

    db = get_db()
    if not db.verify_otp_pin(pin):
        raise HTTPException(status_code=401, detail="Invalid OTP PIN / passcode")

    session_token = _create_otp_session()
    response = JSONResponse({
        "status": "success",
        "message": "OTP verified successfully. Settings unlocked.",
        "token": session_token,
        "expires_in_seconds": _OTP_TTL_SECONDS,
    })
    response.set_cookie(
        key="autoyou_prompts_otp",
        value=session_token,
        max_age=_OTP_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/api/settings")
async def update_settings(
    request: Request,
    payload: Dict[str, Any],
):
    """Save configuration changes (Requires verified OTP session)."""
    token = _extract_auth_token(request)
    if not _is_otp_authenticated(token):
        raise HTTPException(
            status_code=401,
            detail="OTP verification required to modify settings.",
        )

    db = get_db()
    allowed_keys = {
        "sync_interval",
        "capture_attachments",
        "capture_outputs",
    }

    if payload.get("encrypted_storage"):
        raise HTTPException(status_code=422, detail="Encrypted prompt storage is not implemented.")

    updated = {}
    if "sync_interval" in payload:
        if payload["sync_interval"] not in {"manual", "10m", "hourly", "daily", "weekly"}:
            raise HTTPException(status_code=422, detail="Unsupported sync interval.")
        db.set_setting("collection_enabled", payload["sync_interval"] != "manual")
    for k, v in payload.items():
        if k in allowed_keys:
            db.set_setting(k, v)
            updated[k] = v

    if "new_pin" in payload and str(payload["new_pin"]).strip():
        db.update_otp_pin(str(payload["new_pin"]).strip())
        updated["pin_updated"] = True

    return {
        "status": "success",
        "message": "Settings updated successfully.",
        "updated": updated,
    }


@app.get("/api/hooks")
async def list_hooks(request: Request):
    """List registered hook tokens (Requires verified OTP session)."""
    token = _extract_auth_token(request)
    if not _is_otp_authenticated(token):
        raise HTTPException(
            status_code=401,
            detail="OTP verification required to manage hooks.",
        )

    db = get_db()
    hooks = db.list_hooks()
    return {"status": "success", "hooks": hooks}


@app.post("/api/hooks/generate")
async def generate_hook(
    request: Request,
    payload: Dict[str, Any],
):
    """Generate a new hook token for a software installation (Requires OTP)."""
    token = _extract_auth_token(request)
    if not _is_otp_authenticated(token):
        raise HTTPException(
            status_code=401,
            detail="OTP verification required to generate hook tokens.",
        )

    client_name = str(payload.get("client_name") or "New Client Hook").strip()
    db = get_db()
    token_id, raw_token = db.create_hook_token(client_name)

    return {
        "status": "success",
        "token_id": token_id,
        "token": raw_token,
        "client_name": client_name,
        "instructions": (
            f"Include this token in external scripts using header 'X-Prompt-Token: {raw_token}' "
            "when pushing to /api/push."
        ),
    }


@app.post("/api/hooks/{token_id}/revoke")
async def revoke_hook(
    token_id: str,
    request: Request,
):
    """Revoke a hook token (Requires OTP)."""
    token = _extract_auth_token(request)
    if not _is_otp_authenticated(token):
        raise HTTPException(status_code=401, detail="OTP verification required.")

    db = get_db()
    db.revoke_hook(token_id)
    return {"status": "success", "message": f"Hook token '{token_id}' revoked."}


@app.get("/api/export")
async def export_prompts(
    format: str = Query("json", description="Export format: json or markdown"),
    software: Optional[str] = None,
):
    """Export collected prompts to JSON or Markdown."""
    db = get_db()
    prompts, total = db.get_prompts(software=software, limit=1000)

    if format.lower() == "markdown":
        md_lines = [f"# My Prompt Export ({utc_now()})\n\nTotal prompts: {total}\n\n---\n"]
        for p in prompts:
            md_lines.append(f"### [{p['software'].upper()}] {p.get('session_title') or 'Untitled'}")
            md_lines.append(f"*Project*: `{p.get('project_name')}` | *Date*: `{p.get('timestamp')}`\n")
            md_lines.append("#### Prompt:")
            md_lines.append(f"```\n{p.get('prompt_text', '').strip()}\n```\n")
            if p.get("output_text"):
                md_lines.append("#### Output:")
                md_lines.append(f"```\n{p.get('output_text', '').strip()}\n```\n")
            md_lines.append("---\n")
        return PlainTextResponse("\n".join(md_lines), media_type="text/markdown")

    return JSONResponse({
        "status": "success",
        "export_date": utc_now(),
        "total": total,
        "prompts": prompts,
    })


# ─── Mount Static Frontend Assets ───────────────────────────────────────────

if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
