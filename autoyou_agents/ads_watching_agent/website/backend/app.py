# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-363546313441613439306132-579710536367900fda3098fd

"""Ads Watching Agent UI backend."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-363546313441613439306132-579710536367900fda3098fd"


import time
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from fastapi import Request, Response
from shared.runtime_module_loader import import_autoyou_shared_tools_module as _import_autoyou_shared_tools_module

from autoyou_agents.ads_watching_agent.agent import (
    get_web_ad_fallback,
    get_ads_watching_status,
    trigger_agent_website_rewarded_ad,
)

_SMC_MOD = "autoyou_agents.shared_tools.scheduler_mission_control"
_smc = _import_autoyou_shared_tools_module(_SMC_MOD, anchor=__file__)
create_agent_chat_app = _smc.create_agent_chat_app
_json_response = _smc._json_response
_runtime_server = _smc._runtime_server

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_AGENT_NAME = "ads_watching_agent"
_WEB_FALLBACK_CONTROL_TTL_SECONDS = 30 * 60
_WEB_FALLBACK_CONTROLS: dict[str, float] = {}


def _as_nonnegative_int(value: Any) -> int:
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _request_header(request: Request, name: str) -> str:
    return str(request.headers.get(name) or "").strip()


def _request_query_param(request: Request, name: str) -> str:
    try:
        return str(request.query_params.get(name) or "").strip()
    except Exception:
        return ""


def _coalesce_session_identity(request: Request) -> tuple[str, str]:
    session_id = _request_query_param(request, "session_id")
    owner_key = _request_query_param(request, "owner_key")
    if not session_id:
        session_id = _request_header(request, "X-AutoYou-WebRTC-Session-Id")
    if not owner_key:
        owner_key = _request_header(request, "X-AutoYou-WebRTC-Owner-Key")
    return session_id, owner_key


def _coalesce_control_id(request: Request, payload: Mapping[str, Any]) -> str:
    control_id = _request_query_param(request, "control_id")
    if control_id:
        return control_id
    if isinstance(payload, Mapping):
        return str(payload.get("control_id") or "").strip()
    return ""


def _coalesce_web_rewarded_ad_unit(request: Request, payload: Mapping[str, Any] = None) -> str:
    return ""


def _latest_rewarded_ad_completion(session_id: str = "", owner_key: str = "") -> dict[str, Any] | None:
    try:
        runtime = _runtime_server()
        webrtc = getattr(runtime, "WEBRTC", None)
        reader = getattr(webrtc, "latest_rewarded_ad_completion", None)
        if reader is None:
            return None
        completion = reader(session_id=session_id, owner_key=owner_key)
    except Exception:
        return None
    return completion if isinstance(completion, dict) else None


def _rewarded_ad_connection_proof(session_id: str = "", owner_key: str = "") -> dict[str, Any]:
    fallback = {
        "connected": False,
        "resolution": "unavailable",
        "heartbeat_recent": False,
        "heartbeat_age_seconds": None,
    }
    try:
        runtime = _runtime_server()
        webrtc = getattr(runtime, "WEBRTC", None)
        reader = getattr(webrtc, "rewarded_ad_connection_proof", None)
        if reader is None:
            return fallback
        proof = reader(
            session_id=session_id,
            owner_key=owner_key,
            allow_single_live_fallback=not bool(session_id or owner_key),
        )
    except Exception:
        return fallback
    if not isinstance(proof, Mapping):
        return fallback
    connected = bool(proof.get("connected"))
    raw_age = proof.get("heartbeat_age_seconds")
    try:
        heartbeat_age_seconds = max(0.0, round(float(raw_age), 1)) if raw_age is not None else None
    except (TypeError, ValueError):
        heartbeat_age_seconds = None
    resolution = str(proof.get("resolution") or "unavailable").strip()[:48] or "unavailable"
    if resolution not in {"reply_target", "single_live_datachannel", "unavailable", "ambiguous", "owner_mismatch"}:
        resolution = "unavailable"
    return {
        "connected": connected,
        "resolution": resolution,
        "heartbeat_recent": bool(proof.get("heartbeat_recent")),
        "heartbeat_age_seconds": heartbeat_age_seconds,
    }


def _public_ad_status(
    status: Mapping[str, Any],
    connection_proof: Mapping[str, Any],
    web_rewarded_ad_hint: str = "",
) -> dict[str, Any]:
    units = status.get("live_ad_units")
    if not isinstance(units, Mapping):
        units = {}
    ios_rewarded = bool(units.get("ios_rewarded"))
    android_rewarded = bool(units.get("android_rewarded"))
    web_rewarded = bool(units.get("web_rewarded"))
    web_rewarded_ad_unit = str(status.get("web_rewarded_ad_unit") or "").strip()
    if len(web_rewarded_ad_unit) > 768:
        web_rewarded_ad_unit = web_rewarded_ad_unit[:768]
    web_fallback = get_web_ad_fallback()
    web_page = bool(web_fallback.get("available"))
    return {
        "status": "ready" if ios_rewarded or android_rewarded or web_rewarded or web_page else "unavailable",
        "live_ad_units": {
            "ios_rewarded": ios_rewarded,
            "android_rewarded": android_rewarded,
            "web_rewarded": web_rewarded,
            "web_page": web_page,
        },
        "web_rewarded_ad_unit": web_rewarded_ad_unit,
        "web_fallback": {
            "available": web_page,
            "mode": str(web_fallback.get("mode") or "web_page"),
            "url": str(web_fallback.get("url") or "") if web_page else "",
        },
        "connection_proof": dict(connection_proof),
    }


def _public_watch_ad_result(result: Mapping[str, Any]) -> dict[str, Any]:
    if result.get("success") is not True:
        return {
            "triggered_count": 0,
            "status": "unavailable",
        }
    status = str(result.get("status") or "sent").strip().lower()
    if status not in {"sent", "already_active"}:
        status = "sent"
    return {
        "triggered_count": _as_nonnegative_int(result.get("triggered_count")),
        "status": status,
    }


def _remember_web_fallback_control(control_id: str) -> None:
    now = time.monotonic()
    for key, expires_at in list(_WEB_FALLBACK_CONTROLS.items()):
        if expires_at <= now:
            _WEB_FALLBACK_CONTROLS.pop(key, None)
    if control_id:
        _WEB_FALLBACK_CONTROLS[control_id] = now + _WEB_FALLBACK_CONTROL_TTL_SECONDS


def _has_web_fallback_control(control_id: str) -> bool:
    now = time.monotonic()
    expires_at = _WEB_FALLBACK_CONTROLS.get(control_id)
    if expires_at is None:
        return False
    if expires_at <= now:
        _WEB_FALLBACK_CONTROLS.pop(control_id, None)
        return False
    return True


def _public_web_fallback_result(control_id: str = "") -> dict[str, Any] | None:
    fallback = get_web_ad_fallback(include_provider_config=True)
    if not fallback.get("available"):
        return None
    watch_url = str(fallback.get("url") or "")
    if control_id and watch_url:
        _remember_web_fallback_control(control_id[:128])
        parsed = urlparse(watch_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["control_id"] = [control_id[:128]]
        watch_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    return {
        "triggered_count": 0,
        "status": "web_fallback",
        "mode": str(fallback.get("mode") or "web_page"),
        "watch_url": watch_url,
    }


def _watch_ad_status_code(result: dict) -> int:
    if result.get("success") is True:
        return 200
    reason = str(result.get("reason") or result.get("error") or "").lower()
    resolution = str(result.get("resolution") or "").lower()
    http_status = result.get("http_status")
    try:
        parsed_status = int(http_status)
    except (TypeError, ValueError):
        parsed_status = 0
    if parsed_status in {403, 404, 409, 500}:
        return parsed_status
    if "disabled" in reason:
        return 403
    if resolution == "ambiguous" or "multiple" in reason:
        return 409
    if "not reachable" in reason or "authorization is unavailable" in reason:
        return 502
    return 404


def _extra_routes(app, agent_name: str) -> None:
    @app.get("/favicon.ico")
    async def favicon():
        return Response(status_code=204)

    @app.get("/api/status")
    async def api_status(request: Request):
        session_id, owner_key = _coalesce_session_identity(request)
        connection_proof = _rewarded_ad_connection_proof(session_id=session_id, owner_key=owner_key)
        status = _public_ad_status(
            get_ads_watching_status(),
            connection_proof,
            web_rewarded_ad_hint=_coalesce_web_rewarded_ad_unit(request),
        )
        return _json_response(
            {
                "success": True,
                "status": status,
                "rewarded_ad_completion": _latest_rewarded_ad_completion(
                    session_id=session_id,
                    owner_key=owner_key,
                ),
            }
        )

    @app.post("/api/watch-ad")
    async def api_watch_ad(request: Request):
        session_id, owner_key = _coalesce_session_identity(request)
        payload = {}
        try:
            parsed = await request.json()
            if isinstance(parsed, Mapping):
                payload = parsed
        except Exception:
            payload = {}
        web_rewarded_ad_unit = _coalesce_web_rewarded_ad_unit(request, payload)
        result = await trigger_agent_website_rewarded_ad(
            session_id=session_id,
            owner_key=owner_key,
            control_id=_coalesce_control_id(request, payload),
            web_rewarded_ad_unit=web_rewarded_ad_unit,
        )
        public_result = _public_watch_ad_result(result)
        if result.get("success") is not True:
            fallback_control_id = f"web-{uuid.uuid4().hex}"
            fallback_result = _public_web_fallback_result(fallback_control_id)
            if fallback_result is not None:
                return _json_response(
                    {
                        "success": True,
                        "result": fallback_result,
                    }
                )
        payload = {
            "success": bool(result.get("success")),
            "result": public_result,
        }
        return _json_response(payload, status_code=_watch_ad_status_code(result))

    @app.post("/api/web-rewarded-completed")
    async def api_web_rewarded_completed(request: Request):
        """Record an approved GPT web reward as local pending readback only."""
        payload = {}
        try:
            parsed = await request.json()
            if isinstance(parsed, Mapping):
                payload = parsed
        except Exception:
            payload = {}
        session_id, _owner_key = _coalesce_session_identity(request)
        watched_seconds = payload.get("watched_seconds")
        control_id = _coalesce_control_id(request, payload)
        if not _has_web_fallback_control(control_id):
            return _json_response(
                {"success": False, "error": "The rewarded web session is missing or expired."},
                status_code=409,
            )
        try:
            timestamp_ms = int(float(payload.get("timestamp_ms") or time.time() * 1000))
        except (TypeError, ValueError, OverflowError):
            timestamp_ms = int(time.time() * 1000)
        try:
            from shared.pending_ad_credits import (
                normalize_watched_seconds,
                pending_credit_amount,
                record_rewarded_ad_completion,
            )

            normalized_seconds = normalize_watched_seconds(watched_seconds)
            amount = pending_credit_amount(normalized_seconds)
            if amount <= 0:
                return _json_response(
                    {"success": False, "error": "A completed rewarded ad must include watched seconds."},
                    status_code=400,
                )
            record_rewarded_ad_completion(
                {
                    "session_id": session_id,
                    "platform": "web",
                    "timestamp_ms": timestamp_ms,
                    "control_id": control_id,
                    "source": "ads_watching_agent",
                    "watched_seconds": normalized_seconds,
                }
            )
            return _json_response(
                {
                    "success": True,
                    "reward": {
                        "pending_credits": amount,
                        "watched_seconds": normalized_seconds,
                        "ledger_effect": "none",
                        "cloud_hot_path_write": False,
                    },
                }
            )
        except Exception:
            return _json_response(
                {"success": False, "error": "The local pending-credit tally is unavailable."},
                status_code=503,
            )


app = create_agent_chat_app(
    agent_name=_AGENT_NAME,
    title="Ads Watching",
    description="Open AutoYou ads on connected clients.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
