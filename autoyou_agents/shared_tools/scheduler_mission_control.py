# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-2d2030786345634238313737-4abb6a2220e374f7a7303c06

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-2d2030786345634238313737-4abb6a2220e374f7a7303c06"


import copy
import html
import json
import os
import re
import secrets
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
try:
    from fastapi.middleware.gzip import GZipMiddleware
except ImportError:
    class GZipMiddleware:  # no-op when not available in compiled build
        def __init__(self, app, **kwargs):
            self.app = app
        async def __call__(self, scope, receive, send):
            await self.app(scope, receive, send)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from autoyou_agents.tasks_agent.agent import _extract_send_message_action
from shared.adk_state import derive_reply_target_from_owner_key, normalize_reply_target
from shared.scheduler_service import (
    OUTBOUND_NOTIFICATION_QUEUE_FILE,
    REMINDERS_FILE,
    TASKS_FILE,
    append_scheduler_activity_log,
    cancel_active_task_execution,
    get_runtime_messaging_partner_reply_targets,
    get_pending_notification_queue_snapshot,
    load_json,
    read_scheduler_activity_log,
    save_json,
)
from shared.session_execution import (
    build_canonical_user_id,
    build_owner_key,
    get_session_execution_manager,
)


MODULE_ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = MODULE_ROOT / "scheduler_mission_control_frontend"
INDEX_HTML_PATH = FRONTEND_DIR / "index.html"
ASSETS_DIR = FRONTEND_DIR / "assets"
DEFAULT_SESSION_TTL_DAYS = 30
MIN_SESSION_TTL_DAYS = 1
MAX_SESSION_TTL_DAYS = 365
UI_SECURITY_CONFIG_KEY = "agent_ui_security"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "base-uri 'self'; "
        "connect-src 'self' ws: wss:; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data: blob:; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:"
    ),
}
AGENT_META: Dict[str, Dict[str, str]] = {
    "tasks": {
        "agent_name": "tasks_agent",
        "title": "Tasks",
        "eyebrow": "Scheduled Jobs",
        "subtitle": "AI workflows that run on a schedule - recurring or one-time.",
        "item_label": "task",
        "items_title": "Scheduled Tasks",
        "composer_title": "New Task",
        "delete_all_label": "Stop All Tasks",
        "topbar_icon": "⏱",
        "peer_agent": "notify_agent",
        "peer_label": "Notify",
        "role_title": "AI-Driven Scheduler",
        "role_copy": "Tasks run AutoYou work on a schedule, use the right tools, and deliver a fresh result to your chosen target.",
        "role_points": "Use for recurring research, automation, or any job that needs the LLM to think before sending.",
    },
    "notify": {
        "agent_name": "notify_agent",
        "title": "Notify",
        "eyebrow": "Timed Notifications",
        "subtitle": "Send a saved message to your device at a specific time.",
        "item_label": "notification",
        "items_title": "Scheduled Notifications",
        "composer_title": "New Notification",
        "delete_all_label": "Delete All Notifications",
        "topbar_icon": "🔔",
        "peer_agent": "tasks_agent",
        "peer_label": "Tasks",
        "role_title": "Timed Dispatch",
        "role_copy": "Notifications store a fixed message and a target, then send that exact message when the time arrives - no LLM invoked at delivery time.",
        "role_points": "Use for reminders, nudges, and timed outbound messages that must be deterministic.",
    },
}
# Internal reminder-domain alias so reminder board metadata resolves to notify.
AGENT_META["reminders"] = AGENT_META["notify"]
_AGENT_UI_SESSIONS: Dict[str, Dict[str, float]] = {}


def _module_root_is_packaged_runtime() -> bool:
    return any(parent.name in {"runtime_modules", "runtime_source"} for parent in (MODULE_ROOT, *MODULE_ROOT.parents))


def _fallback_agent_ui_sessions_file(app_name: str = "AutoYou") -> Path:
    test_root = os.environ.get("AUTOYOU_TEST_ROOT", "").strip()
    if test_root:
        root = Path(test_root).expanduser()
        if root.name.lower() != app_name.lower():
            root = root / app_name
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / app_name
    elif sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / app_name
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / app_name

    root.mkdir(parents=True, exist_ok=True)
    return root / "agent_ui_sessions.json"


def _resolve_agent_ui_sessions_file() -> str:
    env_path = os.environ.get("AUTOYOU_AGENT_UI_SESSIONS_PATH", "").strip()
    if env_path:
        return str(Path(env_path).expanduser().resolve())

    repo_default = MODULE_ROOT.parent.parent / "agent_ui_sessions.json"
    packaged_runtime_root = _module_root_is_packaged_runtime()
    try:
        from shared.platform_runtime import get_config_dir, is_compiled

        if (
            os.environ.get("AUTOYOU_TEST_ROOT", "").strip()
            or bool(is_compiled())
            or packaged_runtime_root
        ):
            return str((get_config_dir("AutoYou", anchor=__file__) / "agent_ui_sessions.json").resolve())
    except Exception:
        if os.environ.get("AUTOYOU_TEST_ROOT", "").strip() or packaged_runtime_root:
            return str(_fallback_agent_ui_sessions_file().resolve())

    return str(repo_default.resolve())


_AGENT_UI_SESSIONS_FILE = _resolve_agent_ui_sessions_file()


def _load_agent_sessions() -> None:
    """Load persisted agent UI sessions from disk into the in-memory store."""
    global _AGENT_UI_SESSIONS
    try:
        data = load_json(_AGENT_UI_SESSIONS_FILE)
        if isinstance(data, dict):
            _AGENT_UI_SESSIONS = {
                k: {t: float(exp) for t, exp in v.items()}
                for k, v in data.items()
                if isinstance(v, dict)
            }
    except Exception:
        pass


def _save_agent_sessions() -> None:
    """Persist the in-memory agent UI sessions to disk."""
    try:
        save_json(_AGENT_UI_SESSIONS_FILE, _AGENT_UI_SESSIONS)
    except Exception:
        pass


_load_agent_sessions()


def _runtime_server() -> Any:
    for module_name in ("__main__", "server"):
        candidate = sys.modules.get(module_name)
        if (
            candidate is not None
            and hasattr(candidate, "STATE")
            and hasattr(candidate, "_is_logged_in")
            and hasattr(candidate, "_persist_state_config")
        ):
            return candidate
    import server as server_module

    return server_module


def _copy_runtime_config() -> Dict[str, Any]:
    server = _runtime_server()
    current = server.STATE.config or server._default_config()
    return copy.deepcopy(current)


def _normalize_session_ttl_days(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_SESSION_TTL_DAYS
    return max(MIN_SESSION_TTL_DAYS, min(MAX_SESSION_TTL_DAYS, parsed))


def _normalize_auth_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "open":
        return "open"
    return "totp"


AGENT_WEBSITES_CONFIG_KEY = "agent_websites"


def _global_agent_website_otp_disabled() -> bool:
    try:
        cfg = _runtime_server().STATE.config or _runtime_server()._default_config()
        if not isinstance(cfg, dict):
            return False
        websites = cfg.get(AGENT_WEBSITES_CONFIG_KEY)
        if not isinstance(websites, dict):
            return False
        return bool(websites.get("disable_otp", False))
    except Exception:
        return False


def _resolve_manifest_auth_settings(agent_name: str) -> Dict[str, Any]:
    try:
        from .frontend_manifest import resolve_agent_auth_manifest

        return resolve_agent_auth_manifest(agent_name)
    except Exception:
        return {"auth_default": "inherit", "shared_session_eligible": True, "bypass_global_otp": False}


def _agent_manifest_auth_default_mode(manifest_auth_default: str) -> str:
    return "open" if manifest_auth_default == "open" else "totp"


def _get_agent_security_settings(agent_name: str) -> Dict[str, Any]:
    cfg = _runtime_server().STATE.config or _runtime_server()._default_config()
    root = cfg.get(UI_SECURITY_CONFIG_KEY, {})
    entry = root.get(agent_name, {}) if isinstance(root, dict) else {}

    manifest_settings = _resolve_manifest_auth_settings(agent_name)
    manifest_auth_default = manifest_settings.get("auth_default", "inherit")
    website_cfg = cfg.get(AGENT_WEBSITES_CONFIG_KEY, {}) if isinstance(cfg, dict) else {}
    if _global_agent_website_otp_disabled():
        return {
            "auth_mode": "open",
            "session_ttl_days": _normalize_session_ttl_days(entry.get("session_ttl_days")),
            "bypass_global_otp": True,
            "per_agent_profile": False,
            "shared_session_eligible": bool(manifest_settings.get("shared_session_eligible", True)),
        }

    # Precedence (most to least specific):
    #   1. Explicit per-agent admin config auth_mode - an admin's decision for
    #      THIS exact agent always wins outright, over both the global toggle
    #      and the agent's own manifest default.
    #   2. The global "require OTP on all agent websites" policy - if on and
    #      TOTP is configured, gates every agent that hasn't been explicitly
    #      exempted (bypass_global_otp, from either per-agent config or the
    #      agent's own manifest default).
    #   3. The agent's own manifest-declared default ("open"/"gated"/"inherit").
    # This is the manifest-driven replacement for what used to be a single
    # hardcoded `if agent_name == "ads_watching_agent"` branch here - any
    # agent can now declare itself open by default via its own
    # website/manifest.json, and both a specific admin edit and the global
    # policy can still override that default in either direction.
    raw_configured_mode = str(entry.get("auth_mode") or "").strip().lower()
    if "bypass_global_otp" in entry:
        bypass_global = bool(entry.get("bypass_global_otp"))
    else:
        bypass_global = bool(manifest_settings.get("bypass_global_otp", False))

    if raw_configured_mode in {"open", "totp"}:
        per_agent_mode = raw_configured_mode
    else:
        per_agent_mode = _agent_manifest_auth_default_mode(manifest_auth_default)
        if not bypass_global:
            if bool(website_cfg.get("require_otp", False)):
                totp_caps = _totp_capabilities()
                if totp_caps.get("totp_configured"):
                    per_agent_mode = "totp"

    # Per-agent 2FA profile (multi-issuance): a dedicated profile assigned to this
    # agent forces TOTP and is verified against THAT profile (derive-from-password,
    # no shared secret), independent of the global require_otp toggle.
    per_agent_profile = False
    if per_agent_mode != "open":
        try:
            per_agent_profile = bool(
                _runtime_server().agent_has_assigned_2fa_profile(agent_name)
            )
        except Exception:
            per_agent_profile = False
        if per_agent_profile:
            per_agent_mode = "totp"

    return {
        "auth_mode": per_agent_mode,
        "session_ttl_days": _normalize_session_ttl_days(entry.get("session_ttl_days")),
        "bypass_global_otp": bypass_global,
        "per_agent_profile": per_agent_profile,
        "shared_session_eligible": bool(manifest_settings.get("shared_session_eligible", True)),
    }


def _save_agent_security_settings(
    agent_name: str,
    *,
    auth_mode: str,
    session_ttl_days: int,
) -> Dict[str, Any]:
    server = _runtime_server()
    cfg = _copy_runtime_config()
    security_cfg = cfg.get(UI_SECURITY_CONFIG_KEY)
    if not isinstance(security_cfg, dict):
        security_cfg = {}
        cfg[UI_SECURITY_CONFIG_KEY] = security_cfg
    security_cfg[agent_name] = {
        "auth_mode": _normalize_auth_mode(auth_mode),
        "session_ttl_days": _normalize_session_ttl_days(session_ttl_days),
    }
    server._persist_state_config(cfg)
    server.STATE.config = cfg
    return _get_agent_security_settings(agent_name)


def _agent_cookie_name(agent_name: str) -> str:
    return f"autoyou_{agent_name}_mission_session"


def _agent_cookie_path(agent_name: str) -> str:
    return f"/agent/{agent_name}"


def _prune_agent_sessions(agent_name: str) -> None:
    now = time.time()
    bucket = _AGENT_UI_SESSIONS.get(agent_name, {})
    expired_tokens = [token for token, expires_at in bucket.items() if float(expires_at) <= now]
    for token in expired_tokens:
        bucket.pop(token, None)
    if not bucket:
        _AGENT_UI_SESSIONS.pop(agent_name, None)


def _create_agent_session(agent_name: str, ttl_days: int) -> str:
    _prune_agent_sessions(agent_name)
    token = secrets.token_urlsafe(32)
    bucket = _AGENT_UI_SESSIONS.setdefault(agent_name, {})
    bucket[token] = time.time() + (_normalize_session_ttl_days(ttl_days) * 86400)
    _save_agent_sessions()
    return token


def _delete_agent_session(agent_name: str, token: Optional[str]) -> None:
    normalized = str(token or "").strip()
    if not normalized:
        return
    bucket = _AGENT_UI_SESSIONS.get(agent_name)
    if not bucket:
        return
    bucket.pop(normalized, None)
    if not bucket:
        _AGENT_UI_SESSIONS.pop(agent_name, None)
    _save_agent_sessions()


def _agent_session_is_valid(agent_name: str, token: Optional[str]) -> bool:
    normalized = str(token or "").strip()
    if not normalized:
        return False
    _prune_agent_sessions(agent_name)
    return float(_AGENT_UI_SESSIONS.get(agent_name, {}).get(normalized, 0.0)) > time.time()


def clear_all_agent_sessions() -> int:
    """Admin 'sign out everywhere' kill switch.

    Invalidates every outstanding token (per-agent, chat, and shared) so
    already-issued cookies on every device stop working immediately, not
    just on next expiry. Returns the number of sessions invalidated.
    """
    count = sum(len(bucket) for bucket in _AGENT_UI_SESSIONS.values())
    _AGENT_UI_SESSIONS.clear()
    _save_agent_sessions()
    return count


# ─── Opt-in shared cross-agent OTP session ────────────────────────────────────
# Off by default (agent_websites.shared_session_enabled). When an admin turns
# it on, completing OTP for any one eligible agent website also unlocks every
# other eligible agent website in the same browser/cookie jar - useful for
# mobile WebViews that already reuse one cookie jar across agent navigations.
# Reuses the same _AGENT_UI_SESSIONS store/file as per-agent and chat sessions
# via one more reserved bucket key, so it inherits the existing save/prune/load
# logic with no new persistence code.
_SHARED_SESSION_BUCKET_KEY = "__shared__"
SHARED_SESSION_COOKIE_NAME = "autoyou_shared_agent_session"


def clear_shared_agent_sessions() -> None:
    if _AGENT_UI_SESSIONS.pop(_SHARED_SESSION_BUCKET_KEY, None):
        _save_agent_sessions()


def _create_shared_agent_session(ttl_days: int) -> str:
    return _create_agent_session(_SHARED_SESSION_BUCKET_KEY, ttl_days)


def _shared_agent_session_is_valid(token: Optional[str]) -> bool:
    return _agent_session_is_valid(_SHARED_SESSION_BUCKET_KEY, token)


def _shared_session_settings() -> Dict[str, Any]:
    cfg = _runtime_server().STATE.config or _runtime_server()._default_config()
    website_cfg = cfg.get(AGENT_WEBSITES_CONFIG_KEY, {}) if isinstance(cfg, dict) else {}
    return {
        "enabled": bool(website_cfg.get("shared_session_enabled", False)),
        "ttl_days": _normalize_session_ttl_days(website_cfg.get("shared_session_ttl_days")),
    }


def _shared_session_authenticates(request: Request, settings: Dict[str, Any]) -> bool:
    shared = _shared_session_settings()
    if not shared["enabled"] or not settings.get("shared_session_eligible", True):
        return False
    return _shared_agent_session_is_valid(request.cookies.get(SHARED_SESSION_COOKIE_NAME))


def _cookie_should_be_secure(request: Request) -> bool:
    """True when the browser's view of this connection is HTTPS.

    Either directly, or via a reverse-proxy/tunnel hop that sets the standard
    forwarded-proto header (Tunnelmole, cloud-pair bridge). Direct loopback
    (127.0.0.1) traffic is plain HTTP by design, so this correctly stays False
    there - marking a loopback cookie Secure would make the browser silently
    refuse to send it back.
    """
    forwarded_proto = str(request.headers.get("x-forwarded-proto", "")).split(",", 1)[0].strip().lower()
    return forwarded_proto == "https" or request.url.scheme == "https"


def _maybe_set_shared_session_cookie(response: Response, request: Request, settings: Dict[str, Any]) -> None:
    """Called right after a per-agent login succeeds.

    Populates the shared cookie too, iff the global toggle is on AND this
    agent is eligible per its manifest.
    """
    shared = _shared_session_settings()
    if _global_agent_website_otp_disabled() or not shared["enabled"] or not settings.get("shared_session_eligible", True):
        return
    token = _create_shared_agent_session(shared["ttl_days"])
    response.set_cookie(
        SHARED_SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=_cookie_should_be_secure(request),
        path="/",
        max_age=int(shared["ttl_days"] * 86400),
    )


def _maybe_upgrade_shared_session_cookie(response: Response, request: Request) -> None:
    """Let an existing OTP session join sharing when the toggle is enabled later."""
    if _global_agent_website_otp_disabled() or not _shared_session_settings()["enabled"] or _shared_agent_session_is_valid(
        request.cookies.get(SHARED_SESSION_COOKIE_NAME)
    ):
        return
    for cookie_name, token in request.cookies.items():
        match = re.fullmatch(r"autoyou_([a-z0-9_]+)_(mission|chat)_session", cookie_name)
        if not match:
            continue
        agent_name, kind = match.groups()
        bucket = f"__chat__{agent_name}" if kind == "chat" else agent_name
        if not _agent_session_is_valid(bucket, token):
            continue
        settings = _get_agent_security_settings(agent_name)
        if settings["shared_session_eligible"]:
            _maybe_set_shared_session_cookie(response, request, settings)
            return


def _shared_session_upgrade_middleware():
    async def _upgrade(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path.rstrip("/") or "/"
        if request.method == "GET" and response.status_code < 400 and (
            path == "/"
            or re.fullmatch(r"/agent/[a-z0-9_]+", path)
            or path.endswith("/api/auth/status")
            or path.endswith("/api/bootstrap")
        ):
            _maybe_upgrade_shared_session_cookie(response, request)
        return response

    return _upgrade


# ─── Origin/Referer CSRF guard for agent-website apps ─────────────────────────
# These FastAPI sub-apps had zero CSRF defense before this change. This is a
# small, self-contained guard (deliberately not shared with server.py's
# already-hardened admin_app CSRF middleware, to avoid touching that
# previously-reviewed code as a side effect of this change).
_CSRF_PROTECTED_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


def _normalized_header_host(value: str) -> str:
    return str(value or "").split(",", 1)[0].strip().lower()


def _request_csrf_hosts(request: Request) -> set[str]:
    hosts = {_normalized_header_host(request.headers.get("host", ""))}
    client_host = request.client.host if request.client else ""
    if client_host in {"127.0.0.1", "::1", "localhost", "testclient"}:
        hosts.add(_normalized_header_host(request.headers.get("x-forwarded-host", "")))
    return {host for host in hosts if host}


def _agent_app_csrf_guard(exempt_paths: frozenset = frozenset({"/api/auth/login"})):
    async def _guard(request: Request, call_next):
        if (request.method or "").upper() in _CSRF_PROTECTED_METHODS:
            raw_path = (request.url.path or "").rstrip("/") or "/"
            if raw_path not in exempt_paths:
                origin = request.headers.get("origin") or request.headers.get("referer") or ""
                if origin:
                    from urllib.parse import urlsplit

                    if (urlsplit(origin).netloc or "").lower() not in _request_csrf_hosts(request):
                        return _json_response(
                            {"success": False, "error": "Cross-origin request blocked."}, status_code=403
                        )
                # No Origin/Referer at all -> only allow a loopback peer (CLI/local
                # tooling). "testclient" is Starlette TestClient's fixed peer
                # sentinel (server.py's own _is_loopback_client_host recognizes
                # the same value) - it can never appear as a real TCP peer.
                elif not (
                    request.client and request.client.host in {"127.0.0.1", "::1", "localhost", "testclient"}
                ):
                    return _json_response(
                        {"success": False, "error": "Cross-origin request blocked."}, status_code=403
                    )
        return await call_next(request)

    return _guard


def _totp_capabilities() -> Dict[str, Any]:
    server = _runtime_server()
    return server._describe_totp_capabilities(server.STATE.config or server._default_config())


def _describe_auth_state(request: Request, agent_name: str) -> Dict[str, Any]:
    settings = _get_agent_security_settings(agent_name)
    totp_caps = _totp_capabilities()
    totp_configured = bool(totp_caps.get("totp_configured"))

    if settings["auth_mode"] == "open":
        return {
            **settings,
            "required": False,
            "authenticated": True,
            "via": "open",
            "totp_configured": totp_configured,
        }

    # Check per-agent mission session cookie
    cookie_token = request.cookies.get(_agent_cookie_name(agent_name))
    if _agent_session_is_valid(agent_name, cookie_token):
        return {
            **settings,
            "required": True,
            "authenticated": True,
            "via": "mission_session",
            "totp_configured": totp_configured,
        }

    # Check Authorization: Bearer <token> header (for WebRTC tunnel clients
    # that cannot send cookies across the SCTP/HTTP proxy boundary)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:].strip()
        if _agent_session_is_valid(agent_name, bearer_token):
            return {
                **settings,
                "required": True,
                "authenticated": True,
                "via": "mission_session",
                "totp_configured": totp_configured,
            }

    # Opt-in shared cross-agent session (off by default; see
    # agent_websites.shared_session_enabled).
    if _shared_session_authenticates(request, settings):
        return {
            **settings,
            "required": True,
            "authenticated": True,
            "via": "shared_session",
            "totp_configured": totp_configured,
        }

    return {
        **settings,
        "required": True,
        "authenticated": False,
        "via": "none",
        "totp_configured": totp_configured,
    }


def _apply_headers(response: Response) -> Response:
    for header_name, header_value in NO_CACHE_HEADERS.items():
        response.headers[header_name] = header_value
    for header_name, header_value in SECURITY_HEADERS.items():
        response.headers[header_name] = header_value
    return response


def _json_response(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    return _apply_headers(JSONResponse(payload, status_code=status_code))


def _html_response(text: str, status_code: int = 200) -> HTMLResponse:
    return _apply_headers(HTMLResponse(text, status_code=status_code))


def _api_auth_error(agent_name: str, request: Request) -> Optional[JSONResponse]:
    auth = _describe_auth_state(request, agent_name)
    if auth.get("authenticated"):
        return None
    return _json_response(
        {
            "success": False,
            "error": "Not authenticated",
            "auth": auth,
        },
        status_code=401,
    )


def _inject_frontend_auth_gate(
    html_content: str,
    *,
    title: str,
    auth: Dict[str, Any],
    otp_field: str,
) -> str:
    """Fail closed before page-specific JavaScript can render a locked UI.

    Factory frontends have different layouts, but the authentication boundary is
    the same. Keep the page body inert and hidden until the scoped session cookie
    exists, then reload so the page-specific bootstrap sees authenticated state.
    """
    locked = bool(auth.get("required")) and not bool(auth.get("authenticated"))
    auth_json = json.dumps(auth, ensure_ascii=True).replace("</", "<\\/")
    escaped_title = html.escape(title, quote=True)
    locked_attr = "" if locked else " hidden"
    viewport_injection = ""
    if not re.search(
        r'<meta\b[^>]*\bname\s*=\s*["\']viewport["\']',
        html_content,
        re.IGNORECASE,
    ):
        viewport_injection = '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
    head_injection = f"""
{viewport_injection}<style id="autoyou-auth-gate-styles">
  html[data-autoyou-auth-locked="true"] body > *:not(#autoyou-auth-gate):not(script) {{
    display: none !important;
    pointer-events: none !important;
    user-select: none !important;
  }}
  #autoyou-auth-gate[hidden] {{ display: none !important; }}
  #autoyou-auth-gate {{
    position: fixed; inset: 0; z-index: 2147483647; display: grid;
    place-items: center; padding: 24px; color-scheme: light dark;
    background: Canvas; color: CanvasText;
    font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  #autoyou-auth-gate section {{
    width: min(100%, 380px); padding: 24px; border: 1px solid color-mix(in srgb, CanvasText 18%, transparent);
    border-radius: 14px; background: color-mix(in srgb, Canvas 94%, CanvasText 6%);
    box-shadow: 0 16px 48px color-mix(in srgb, CanvasText 20%, transparent);
  }}
  #autoyou-auth-gate h2 {{ margin: 0 0 8px; font-size: 1.25rem; }}
  #autoyou-auth-gate p {{ margin: 0 0 16px; opacity: .78; }}
  #autoyou-auth-gate form {{ display: grid; gap: 10px; }}
  #autoyou-auth-gate input, #autoyou-auth-gate button {{
    min-height: 44px; box-sizing: border-box; width: 100%; padding: 10px 12px;
    border: 1px solid color-mix(in srgb, CanvasText 24%, transparent); border-radius: 9px;
    font: inherit;
  }}
  #autoyou-auth-gate input {{ background: Canvas; color: CanvasText; text-align: center; letter-spacing: .24em; }}
  #autoyou-auth-gate button {{ border-color: transparent; background: Highlight; color: HighlightText; cursor: pointer; font-weight: 600; }}
  #autoyou-auth-gate button:disabled {{ opacity: .6; cursor: wait; }}
  #autoyou-auth-gate [role="alert"] {{ min-height: 1.5em; margin: 4px 0 0; color: #b42318; }}
</style>
<script id="autoyou-auth-gate-bootstrap">
  window.__AUTOYOU_AUTH__ = {auth_json};
  if (window.__AUTOYOU_AUTH__.required && !window.__AUTOYOU_AUTH__.authenticated) {{
    document.documentElement.dataset.autoyouAuthLocked = "true";
  }}
</script>
"""
    gate = f"""
<div id="autoyou-auth-gate" role="dialog" aria-modal="true" aria-labelledby="autoyou-auth-title"{locked_attr}>
  <section>
    <h2 id="autoyou-auth-title">Unlock {escaped_title}</h2>
    <p>Enter the 6-digit authenticator code to access this private agent.</p>
    <form id="autoyou-auth-form">
      <input id="autoyou-auth-code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]*" placeholder="6-digit code" aria-label="Authenticator code">
      <button id="autoyou-auth-submit" type="submit">Unlock</button>
      <p id="autoyou-auth-error" role="alert" aria-live="polite"></p>
    </form>
  </section>
</div>
"""
    gate_script = f"""
<script id="autoyou-auth-gate-script">
(function () {{
  var auth = window.__AUTOYOU_AUTH__ || {{}};
  if (!auth.required || auth.authenticated) return;
  var root = document.documentElement;
  var gate = document.getElementById('autoyou-auth-gate');
  var form = document.getElementById('autoyou-auth-form');
  var input = document.getElementById('autoyou-auth-code');
  var button = document.getElementById('autoyou-auth-submit');
  var error = document.getElementById('autoyou-auth-error');
  document.querySelectorAll('body > *:not(#autoyou-auth-gate):not(script)').forEach(function (element) {{
    element.inert = true;
  }});
  if (input) window.setTimeout(function () {{ input.focus(); }}, 0);
  if (!form) return;
  form.addEventListener('submit', function (event) {{
    event.preventDefault();
    var code = String((input && input.value) || '').trim();
    if (!/^\\d{{6}}$/.test(code)) {{
      if (error) error.textContent = 'Enter the 6-digit authenticator code.';
      return;
    }}
    if (button) button.disabled = true;
    if (error) error.textContent = '';
    var payload = {{}};
    payload[{json.dumps(otp_field)}] = code;
    fetch('./api/auth/login', {{
      method: 'POST',
      credentials: 'same-origin',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(payload)
    }}).then(function (response) {{
      return response.json().catch(function () {{ return {{}}; }}).then(function (data) {{
        if (!response.ok || !data.success) throw new Error(data.error || 'Authentication failed.');
        return data;
      }});
    }}).then(function () {{
      delete root.dataset.autoyouAuthLocked;
      window.location.reload();
    }}).catch(function (reason) {{
      if (error) error.textContent = reason.message || 'Authentication failed.';
      if (button) button.disabled = false;
      if (input) {{ input.value = ''; input.focus(); }}
    }});
  }});
}})();
</script>
"""

    head_close = html_content.lower().find("</head>")
    if head_close >= 0:
        html_content = html_content[:head_close] + head_injection + html_content[head_close:]
    else:
        body_start = html_content.lower().find("<body")
        body_open = html_content.find(">", body_start) if body_start >= 0 else -1
        if body_open >= 0:
            html_content = html_content[:body_open + 1] + head_injection + html_content[body_open + 1:]
    body_start = html_content.lower().find("<body")
    body_open = html_content.find(">", body_start) if body_start >= 0 else -1
    if body_open >= 0:
        html_content = html_content[:body_open + 1] + gate + html_content[body_open + 1:]
    body_close = html_content.lower().rfind("</body>")
    if body_close >= 0:
        html_content = html_content[:body_close] + gate_script + html_content[body_close:]
    return html_content


def _set_agent_session_cookie(
    response: Response,
    request: Request,
    agent_name: str,
    token: str,
    settings: Dict[str, Any],
) -> None:
    """Set one canonical, direct-port-safe agent session cookie."""
    ttl_days = _normalize_session_ttl_days(settings.get("session_ttl_days"))
    response.set_cookie(
        _agent_cookie_name(agent_name),
        token,
        httponly=True,
        samesite="lax",
        secure=_cookie_should_be_secure(request),
        path="/",
        max_age=ttl_days * 86400,
    )
    _maybe_set_shared_session_cookie(response, request, settings)


async def _agent_website_login_response(request: Request, agent_name: str) -> JSONResponse:
    settings = _get_agent_security_settings(agent_name)
    server = _runtime_server()
    if settings["auth_mode"] == "open":
        return _json_response({"success": True, "auth": _describe_auth_state(request, agent_name)})
    try:
        payload = await request.json()
    except Exception:
        return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)

    code = str(payload.get("totp_code") or payload.get("code") or "").strip()
    if not code:
        return _json_response({"success": False, "error": "OTP code is required."}, status_code=400)

    try:
        assigned_profile = server.agent_has_assigned_2fa_profile(agent_name) is True
    except Exception:
        assigned_profile = False
    if assigned_profile:
        try:
            verified = bool(server.verify_agent_assigned_2fa(agent_name, code))
        except Exception:
            verified = False
    else:
        if not _totp_capabilities().get("totp_configured"):
            return _json_response(
                {"success": False, "error": "2FA is not configured on this server."},
                status_code=400,
            )
        cfg = server.STATE.config or server._default_config()
        try:
            verified = bool(server._verify_totp_secret(server._get_pairing_totp_secret(cfg), code))
        except Exception:
            verified = False
    if not verified:
        return _json_response({"success": False, "error": "Invalid OTP code."}, status_code=401)

    token = _create_agent_session(agent_name, settings["session_ttl_days"])
    response = _json_response({"success": True, "token": token})
    _set_agent_session_cookie(response, request, agent_name, token, settings)
    return response


def _agent_website_auth_status_response(request: Request, agent_name: str) -> JSONResponse:
    return _json_response({"success": True, "auth": _describe_auth_state(request, agent_name)})


async def _agent_website_logout_response(request: Request, agent_name: str) -> JSONResponse:
    cookie_name = _agent_cookie_name(agent_name)
    token = request.cookies.get(cookie_name)
    if token:
        _delete_agent_session(agent_name, token)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        _delete_agent_session(agent_name, auth_header[7:].strip())
    response = _json_response({"success": True})
    response.delete_cookie(cookie_name, path="/")
    response.delete_cookie(cookie_name, path=_agent_cookie_path(agent_name))
    return response


def install_agent_website_auth(
    app: FastAPI,
    *,
    agent_name: str,
    title: str,
    register_auth_routes: bool = False,
    is_authenticated: Optional[Any] = None,
) -> None:
    """Apply the shared OTP boundary to a bespoke agent website.

    Factory-built sites already get this behavior from their factory. This
    adapter keeps custom FastAPI sites from drifting into a second auth style.
    """
    app.middleware("http")(_agent_app_csrf_guard())
    app.middleware("http")(_shared_session_upgrade_middleware())
    prefix = f"/agent/{agent_name}"

    @app.middleware("http")
    async def _shared_agent_auth_boundary(request: Request, call_next):
        path = request.url.path or "/"
        if path == prefix or path.startswith(prefix + "/"):
            path = path[len(prefix):] or "/"
        if (
            request.method == "OPTIONS"
            or path == "/health"
            or path.startswith("/assets/")
            or path == "/favicon.ico"
            or path == "/api/auth"
            or path.startswith("/api/auth/")
        ):
            return await call_next(request)

        if is_authenticated is not None:
            try:
                if is_authenticated(request):
                    return await call_next(request)
            except Exception:
                pass
        auth = _describe_auth_state(request, agent_name)
        if auth.get("authenticated"):
            return await call_next(request)
        if path.startswith("/api/") or path.startswith("/media/") or path.startswith("/outputs/"):
            return _json_response(
                {"success": False, "error": "Not authenticated", "auth": auth},
                status_code=401,
            )

        shell = "<!doctype html><html><head><meta charset=\"utf-8\"><title></title></head><body></body></html>"
        return _html_response(
            _inject_frontend_auth_gate(shell, title=title, auth=auth, otp_field="totp_code"),
        )

    if not register_auth_routes:
        return

    async def _login(request: Request):
        return await _agent_website_login_response(request, agent_name)

    async def _logout(request: Request):
        return await _agent_website_logout_response(request, agent_name)

    async def _status(request: Request):
        return _agent_website_auth_status_response(request, agent_name)

    app.add_api_route("/api/auth/login", _login, methods=["POST"])
    app.add_api_route("/api/auth/logout", _logout, methods=["POST"])
    app.add_api_route("/api/auth/status", _status, methods=["GET"])


def _reply_target_label(reply_target: Optional[Dict[str, Any]]) -> str:
    normalized = normalize_reply_target(reply_target) or {}
    if not normalized:
        return "No target"

    transport = str(normalized.get("transport") or "").strip().lower()
    if transport == "webrtc":
        session_id = str(normalized.get("session_id") or "").strip()
        owner_key = str(normalized.get("owner_key") or "").strip()
        if session_id and owner_key:
            return f"AutoYou App session {session_id} ({owner_key})"
        if session_id:
            return f"AutoYou App session {session_id}"
        if owner_key:
            return f"AutoYou App ({owner_key})"
        return "AutoYou App"
    if transport == "telegram":
        chat_id = str(normalized.get("chat_id") or "").strip()
        return f"Telegram {chat_id}" if chat_id else "Telegram"
    if transport == "telegram_user":
        return "Telegram User Saved Messages"
    if transport == "whatsapp":
        recipient = str(normalized.get("to") or "").strip()
        return f"WhatsApp {recipient}" if recipient else "WhatsApp"
    if transport == "signal":
        recipient = str(normalized.get("to") or "").strip()
        return f"Signal {recipient}" if recipient else "Signal"
    return transport or "Saved target"


def _owner_key_from_reply_target(reply_target: Optional[Dict[str, Any]]) -> Optional[str]:
    normalized = normalize_reply_target(reply_target) or {}
    transport = str(normalized.get("transport") or "").strip().lower()
    if transport == "webrtc":
        owner_key = str(normalized.get("owner_key") or "").strip()
        return owner_key or None
    if transport == "telegram":
        chat_id = str(normalized.get("chat_id") or "").strip()
        return build_owner_key("telegram", chat_id) if chat_id else None
    if transport == "telegram_user":
        return None
    if transport == "whatsapp":
        recipient = str(normalized.get("to") or "").strip()
        return build_owner_key("whatsapp", recipient) if recipient else None
    if transport == "signal":
        recipient = str(normalized.get("to") or "").strip()
        return build_owner_key("signal", recipient) if recipient else None
    return None


def _transport_title(transport: str) -> str:
    return {
        "webrtc": "AutoYou App",
        "telegram": "Telegram Bot",
        "telegram_user": "Telegram User",
        "whatsapp": "WhatsApp",
        "signal": "Signal",
    }.get(transport, transport.title())


def _transport_available(transport: str) -> bool:
    server = _runtime_server()
    normalized = str(transport or "").strip().lower()
    if normalized == "telegram":
        try:
            return server._get_active_telegram_bot() is not None
        except Exception:
            return False
    if normalized == "telegram_user":
        return getattr(getattr(server, "STATE", None), "telegram_user_service", None) is not None
    if normalized == "whatsapp":
        return getattr(getattr(server, "STATE", None), "whatsapp_service", None) is not None
    if normalized == "signal":
        return getattr(getattr(server, "STATE", None), "signal_service", None) is not None
    if normalized == "webrtc":
        return bool(_collect_live_webrtc_targets())
    return False


def _collect_live_webrtc_targets() -> List[Dict[str, Any]]:
    server = _runtime_server()
    manager = getattr(server, "WEBRTC", None)
    if manager is None:
        return []

    grouped_options: Dict[str, Dict[str, Any]] = {}
    datachannel_managers = dict(getattr(manager, "datachannel_managers", {}) or {})
    if hasattr(manager, "_unique_datachannel_manager_entries"):
        live_entries = list(manager._unique_datachannel_manager_entries(require_send_message=True))
    else:
        live_entries = []
        seen_manager_ids: set[int] = set()
        for session_id, datachannel_manager in sorted(
            list(datachannel_managers.items()),
            key=lambda item: str(item[0]),
        ):
            if datachannel_manager is None or not hasattr(datachannel_manager, "send_message"):
                continue
            manager_id = id(datachannel_manager)
            if manager_id in seen_manager_ids:
                continue
            seen_manager_ids.add(manager_id)
            live_entries.append((str(session_id), datachannel_manager))

    for session_id, datachannel_manager in live_entries:
        if datachannel_manager is None or not hasattr(datachannel_manager, "send_message"):
            continue
        try:
            identity = manager._resolve_chat_identity(str(session_id))
        except Exception:
            identity = None
        owner_key = str(getattr(identity, "owner_key", "") or "").strip()
        canonical_user_id = str(getattr(identity, "canonical_user_id", "") or "").strip()
        stable_identity = owner_key or canonical_user_id or str(session_id)
        preferred_session_id = str(getattr(datachannel_manager, "session_id", "") or "").strip()
        if not preferred_session_id or datachannel_managers.get(preferred_session_id) is not datachannel_manager:
            preferred_session_id = str(session_id)
        reply_target: Dict[str, Any] = {
            "transport": "webrtc",
            "session_id": preferred_session_id,
        }
        if owner_key:
            reply_target["owner_key"] = owner_key
        option_id = f"webrtc:{stable_identity}"
        existing = grouped_options.get(option_id)
        if existing is None:
            grouped_options[option_id] = {
                "id": option_id,
                "transport": "webrtc",
                "label": "AutoYou App",
                "detail": owner_key or canonical_user_id or f"Session {session_id}",
                "connected": True,
                "owner_key": owner_key,
                "canonical_user_id": canonical_user_id,
                "reply_target": reply_target,
                "session_ids": [str(session_id)],
            }
            continue
        existing["session_ids"].append(str(session_id))
        if not existing.get("owner_key") and owner_key:
            existing["owner_key"] = owner_key
        if not existing.get("canonical_user_id") and canonical_user_id:
            existing["canonical_user_id"] = canonical_user_id

    options: List[Dict[str, Any]] = []
    for option in grouped_options.values():
        session_ids = list(option.get("session_ids") or [])
        detail = str(option.get("detail") or "AutoYou App")
        if len(session_ids) > 1:
            detail = f"{detail} · {len(session_ids)} live sessions"
        option["detail"] = detail
        options.append(option)
    options.sort(key=lambda item: (str(item.get("owner_key") or ""), str(item.get("detail") or "")))
    return options


def _known_owner_keys() -> List[str]:
    owner_keys: set[str] = set()
    for record in load_json(TASKS_FILE) + load_json(REMINDERS_FILE):
        owner_key = str(record.get("creator_owner_key") or "").strip()
        if owner_key:
            owner_keys.add(owner_key)

    for reply_target in get_runtime_messaging_partner_reply_targets():
        owner_key = str(_owner_key_from_reply_target(reply_target) or "").strip()
        if owner_key:
            owner_keys.add(owner_key)

    try:
        manager = get_session_execution_manager()
        owner_keys.update(
            str(owner_key).strip()
            for owner_key in getattr(manager, "_owner_aliases", {}).values()
            if str(owner_key).strip()
        )
    except Exception:
        pass

    return sorted(owner_keys)


def _build_target_catalog() -> Dict[str, Any]:
    options: List[Dict[str, Any]] = []
    seen: set[str] = set()
    owner_keys: set[str] = set(_known_owner_keys())

    live_webrtc_options = _collect_live_webrtc_targets()
    for option in live_webrtc_options:
        if option["id"] in seen:
            continue
        seen.add(option["id"])
        options.append(option)
        owner_key = str(option.get("owner_key") or "").strip()
        if owner_key:
            owner_keys.add(owner_key)

    for owner_key in sorted(owner_keys):
        transport, _, sender_id = owner_key.partition(":")
        if transport not in {"telegram", "telegram_user", "whatsapp", "signal"}:
            continue
        if not sender_id or not _transport_available(transport):
            continue
        reply_target = derive_reply_target_from_owner_key(owner_key)
        normalized_target = normalize_reply_target(reply_target)
        if not normalized_target:
            continue
        option_id = f"{transport}:{sender_id}"
        if option_id in seen:
            continue
        seen.add(option_id)
        options.append(
            {
                "id": option_id,
                "transport": transport,
                "label": _transport_title(transport),
                "detail": sender_id,
                "connected": True,
                "owner_key": owner_key,
                "canonical_user_id": build_canonical_user_id(owner_key),
                "reply_target": normalized_target,
            }
        )

    return {
        "count": len(options),
        "options": options,
    }


def _resolve_target_context(payload: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    reply_target = normalize_reply_target(payload.get("delivery_target"))
    owner_key = str(payload.get("owner_key") or "").strip() or str(_owner_key_from_reply_target(reply_target) or "")
    if not owner_key:
        raise ValueError("Choose a connected message partner before saving.")

    if not reply_target:
        reply_target = normalize_reply_target(derive_reply_target_from_owner_key(owner_key))
    if not reply_target:
        raise ValueError("The selected partner does not expose a supported delivery target.")

    return owner_key, build_canonical_user_id(owner_key), reply_target


def _task_interval_minutes(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError("Interval must be numeric.")
    if parsed < 1:
        raise ValueError("Interval must be at least 1 minute.")
    return parsed


def _normalize_iso_datetime(value: Any) -> Tuple[float, str]:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("A reminder time is required.")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed.timestamp(), parsed.astimezone(timezone.utc).isoformat()


def _normalize_task_record(task: Dict[str, Any]) -> Dict[str, Any]:
    last_run_s = float(task.get("last_run_s") or 0.0)
    interval_minutes = float(task.get("interval_minutes") or 0.0)
    last_result_at_s = float(task.get("last_result_at_s") or 0.0)
    enabled = bool(task.get("enabled", True))
    run_once = bool(task.get("run_once")) or interval_minutes == 0
    next_run_at_s = None
    if not run_once and enabled and interval_minutes > 0:
        next_run_at_s = last_run_s + (interval_minutes * 60.0)
    return {
        "id": str(task.get("id") or ""),
        "instruction": str(task.get("instruction") or ""),
        "interval_minutes": interval_minutes,
        "run_once": run_once,
        "enabled": enabled,
        "created_at_s": float(task.get("created_at_s") or 0.0),
        "updated_at_s": float(task.get("updated_at_s") or 0.0),
        "last_run_s": last_run_s,
        "next_run_at_s": next_run_at_s,
        "owner_key": str(task.get("creator_owner_key") or ""),
        "delivery_target": normalize_reply_target(task.get("delivery_target")) or {},
        "delivery_target_label": _reply_target_label(task.get("delivery_target")),
        "run_counter": int(task.get("run_counter") or 0),
        "recent_outputs": list(task.get("recent_outputs") or []),
        "last_result_preview": str(task.get("last_result_preview") or ""),
        "last_result_at_s": last_result_at_s,
        "lock_reply_target": bool(task.get("lock_reply_target")),
        "direct_action_type": str((task.get("action") or {}).get("type") or ""),
    }


def _normalize_reminder_record(reminder: Dict[str, Any]) -> Dict[str, Any]:
    timestamp_s = float(reminder.get("timestamp_s") or 0.0)
    return {
        "id": str(reminder.get("id") or ""),
        "message": str(reminder.get("message") or ""),
        "timestamp_s": timestamp_s,
        "iso": str(reminder.get("iso") or ""),
        "created_at_s": float(reminder.get("created_at_s") or 0.0),
        "updated_at_s": float(reminder.get("updated_at_s") or 0.0),
        "owner_key": str(reminder.get("creator_owner_key") or ""),
        "delivery_target": normalize_reply_target(reminder.get("delivery_target")) or {},
        "delivery_target_label": _reply_target_label(reminder.get("delivery_target")),
        "lock_reply_target": bool(reminder.get("lock_reply_target")),
    }


def _build_task_stats(items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    now = time.time()
    enabled_items = [item for item in items if item.get("enabled")]
    paused_items = [item for item in items if not item.get("enabled")]
    due_soon_items = [
        item
        for item in enabled_items
        if item.get("next_run_at_s") is not None and float(item["next_run_at_s"]) <= now + 3600
    ]
    pinned_items = [item for item in items if item.get("lock_reply_target")]
    return [
        {"label": "Enabled", "value": str(len(enabled_items)), "detail": "Recurring tasks currently active"},
        {"label": "Paused", "value": str(len(paused_items)), "detail": "Tasks held without deleting history"},
        {"label": "Due Soon", "value": str(len(due_soon_items)), "detail": "Scheduled to run within the next hour"},
        {"label": "Pinned", "value": str(len(pinned_items)), "detail": "Tasks locked to one explicit delivery partner"},
    ]


def build_tasks_live_summary(
    *,
    queue_limit: int = 6,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    current_time = float(now if now is not None else time.time())
    items = [_normalize_task_record(task) for task in load_json(TASKS_FILE)]
    enabled_items = [item for item in items if item.get("enabled")]
    paused_items = [item for item in items if not item.get("enabled")]
    due_soon_items = [
        item
        for item in enabled_items
        if item.get("next_run_at_s") is not None and float(item["next_run_at_s"]) <= current_time + 3600
    ]
    pinned_items = [item for item in items if item.get("lock_reply_target")]
    next_run_item = min(
        (item for item in enabled_items if item.get("next_run_at_s") is not None),
        key=lambda item: float(item.get("next_run_at_s") or 0.0),
        default=None,
    )
    last_result_item = max(
        (item for item in items if float(item.get("last_result_at_s") or 0.0) > 0.0),
        key=lambda item: float(item.get("last_result_at_s") or 0.0),
        default=None,
    )
    queue = get_pending_notification_queue_snapshot(limit=queue_limit, now=current_time)
    return {
        "success": True,
        "generated_at_s": current_time,
        "tasks": {
            "total_count": len(items),
            "enabled_count": len(enabled_items),
            "paused_count": len(paused_items),
            "due_soon_count": len(due_soon_items),
            "pinned_count": len(pinned_items),
            "next_run_at_s": float(next_run_item.get("next_run_at_s")) if next_run_item else None,
            "next_run_task_id": str(next_run_item.get("id") or "") if next_run_item else "",
            "next_run_preview": str(next_run_item.get("instruction") or "") if next_run_item else "",
            "last_result_at_s": float(last_result_item.get("last_result_at_s")) if last_result_item else None,
            "last_result_task_id": str(last_result_item.get("id") or "") if last_result_item else "",
            "last_result_preview": str(last_result_item.get("last_result_preview") or "") if last_result_item else "",
        },
        "queue": queue,
    }


def _build_reminder_stats(items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    now = time.time()
    next_hour_items = [item for item in items if float(item.get("timestamp_s") or 0.0) <= now + 3600]
    pinned_items = [item for item in items if item.get("lock_reply_target")]
    return [
        {"label": "Pending", "value": str(len(items)), "detail": "Reminders waiting to fire"},
        {"label": "Next Hour", "value": str(len(next_hour_items)), "detail": "Reminders due within 60 minutes"},
        {"label": "Pinned", "value": str(len(pinned_items)), "detail": "Reminders locked to one explicit delivery partner"},
        {"label": "Queue File", "value": str(len(load_json(OUTBOUND_NOTIFICATION_QUEUE_FILE))), "detail": "Shared scheduler backlog entries"},
    ]


def _serialize_activity_records(agent_kind: str, *, limit: int = 60) -> List[Dict[str, Any]]:
    return [
        {
            "timestamp_s": float(record.get("timestamp_s") or 0.0),
            "event_type": str(record.get("event_type") or ""),
            "status": str(record.get("status") or ""),
            "message": str(record.get("message") or ""),
            "details": record.get("details") if isinstance(record.get("details"), dict) else {},
            "item_id": str(record.get("item_id") or ""),
        }
        for record in read_scheduler_activity_log(agent_kind=agent_kind, limit=limit)
    ]


def _build_dashboard_payload(agent_kind: str, request: Request) -> Dict[str, Any]:
    meta = AGENT_META[agent_kind]
    agent_name = meta["agent_name"]
    auth = _describe_auth_state(request, agent_name)
    payload: Dict[str, Any] = {
        "success": True,
        "agent_kind": agent_kind,
        "agent_name": agent_name,
        "meta": meta,
        "auth": auth,
        "generated_at_s": time.time(),
        "links": {
            "frontends": "../../websites",
        },
    }
    if not auth.get("authenticated"):
        return payload

    if agent_kind == "tasks":
        items = [_normalize_task_record(task) for task in load_json(TASKS_FILE)]
        items.sort(key=lambda item: (not bool(item.get("enabled")), float(item.get("next_run_at_s") or 0.0), item["id"]))
        stats = _build_task_stats(items)
    else:
        items = [_normalize_reminder_record(reminder) for reminder in load_json(REMINDERS_FILE)]
        items.sort(key=lambda item: (float(item.get("timestamp_s") or 0.0), item["id"]))
        stats = _build_reminder_stats(items)

    payload.update(
        {
            "items": items,
            "stats": stats,
            "targets": _build_target_catalog(),
            "queue": get_pending_notification_queue_snapshot(limit=10),
            "activity": _serialize_activity_records(agent_kind),
        }
    )
    return payload


def _create_task_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    instruction = str(payload.get("instruction") or "").strip()
    if not instruction:
        raise ValueError("Task instruction is required.")

    is_run_once = bool(payload.get("run_once")) or payload.get("interval_minutes") == 0
    if is_run_once:
        interval_minutes = 0.0
    else:
        interval_minutes = _task_interval_minutes(payload.get("interval_minutes"))

    owner_key, canonical_user_id, reply_target = _resolve_target_context(payload)
    now = time.time()
    task_id = uuid.uuid4().hex[:8]

    # For run-once with a future run-at time, schedule last_run_s so scheduler fires then.
    if is_run_once:
        run_at_iso = str(payload.get("run_once_at_iso") or "").strip()
        if run_at_iso:
            try:
                _dt = datetime.fromisoformat(run_at_iso.replace("Z", "+00:00"))
                last_run_s = _dt.timestamp()
            except Exception:
                last_run_s = 0.0
        else:
            last_run_s = 0.0
    else:
        last_run_s = now

    task: Dict[str, Any] = {
        "id": task_id,
        "instruction": instruction,
        "interval_minutes": interval_minutes,
        "run_once": is_run_once,
        "last_run_s": last_run_s,
        "scheduler_session_id": f"scheduled-task::{task_id}",
        "created_at_s": now,
        "updated_at_s": now,
        "enabled": bool(payload.get("enabled", True)),
        "creator_owner_key": owner_key,
        "creator_user_id": canonical_user_id,
        "delivery_target": reply_target,
        "lock_reply_target": True,
    }
    action = _extract_send_message_action(instruction)
    if action:
        task["action"] = action
    tasks = load_json(TASKS_FILE)
    tasks.append(task)
    save_json(TASKS_FILE, tasks)
    append_scheduler_activity_log(
        "task_created",
        agent_kind="tasks",
        item_id=task_id,
        status="created",
        message=instruction,
        details={
            "interval_minutes": interval_minutes,
            "enabled": bool(task.get("enabled")),
            "owner_key": owner_key,
            "lock_reply_target": True,
        },
    )
    return _normalize_task_record(task)


def _update_task_from_payload(item_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    tasks = load_json(TASKS_FILE)
    updated_task: Optional[Dict[str, Any]] = None
    now = time.time()
    for task in tasks:
        if str(task.get("id") or "") != item_id:
            continue
        if "instruction" in payload:
            instruction = str(payload.get("instruction") or "").strip()
            if not instruction:
                raise ValueError("Task instruction cannot be empty.")
            task["instruction"] = instruction
            task.pop("recent_outputs", None)
            task.pop("last_result_preview", None)
            action = _extract_send_message_action(instruction)
            if action:
                task["action"] = action
            else:
                task.pop("action", None)
        if "interval_minutes" in payload:
            task["interval_minutes"] = _task_interval_minutes(payload.get("interval_minutes"))
        if "enabled" in payload:
            next_enabled = bool(payload.get("enabled"))
            previous_enabled = bool(task.get("enabled", True))
            task["enabled"] = next_enabled
            if previous_enabled != next_enabled:
                task["last_run_s"] = now
                if not next_enabled:
                    cancel_active_task_execution(item_id)
        if "delivery_target" in payload or "owner_key" in payload:
            owner_key, canonical_user_id, reply_target = _resolve_target_context(payload)
            task["creator_owner_key"] = owner_key
            task["creator_user_id"] = canonical_user_id
            task["delivery_target"] = reply_target
            task["lock_reply_target"] = True
        task["updated_at_s"] = now
        updated_task = task
        break

    if updated_task is None:
        raise KeyError(f"Task {item_id} not found.")

    save_json(TASKS_FILE, tasks)
    append_scheduler_activity_log(
        "task_updated",
        agent_kind="tasks",
        item_id=item_id,
        status="updated",
        message=str(updated_task.get("instruction") or ""),
        details={
            "interval_minutes": float(updated_task.get("interval_minutes") or 0.0),
            "enabled": bool(updated_task.get("enabled", True)),
            "owner_key": str(updated_task.get("creator_owner_key") or ""),
            "lock_reply_target": bool(updated_task.get("lock_reply_target")),
        },
    )
    return _normalize_task_record(updated_task)


def _delete_task(item_id: str) -> Dict[str, Any]:
    tasks = load_json(TASKS_FILE)
    retained = [task for task in tasks if str(task.get("id") or "") != item_id]
    if len(retained) == len(tasks):
        raise KeyError(f"Task {item_id} not found.")
    save_json(TASKS_FILE, retained)
    cancel_active_task_execution(item_id)
    append_scheduler_activity_log(
        "task_deleted",
        agent_kind="tasks",
        item_id=item_id,
        status="deleted",
        message=f"Deleted scheduled task {item_id}.",
    )
    return {"deleted": True, "item_id": item_id}


def _delete_all_tasks() -> Dict[str, Any]:
    tasks = load_json(TASKS_FILE)
    removed_ids = [str(task.get("id") or "") for task in tasks if str(task.get("id") or "").strip()]
    save_json(TASKS_FILE, [])
    cancelled_count = sum(1 for task_id in removed_ids if cancel_active_task_execution(task_id))
    append_scheduler_activity_log(
        "task_bulk_deleted",
        agent_kind="tasks",
        status="deleted",
        message=f"Deleted {len(removed_ids)} scheduled task(s).",
        details={"removed_ids": removed_ids[:50], "cancelled_count": cancelled_count},
    )
    return {"removed_count": len(removed_ids), "cancelled_count": cancelled_count}


def _create_reminder_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    message = str(payload.get("message") or "").strip()
    if not message:
        raise ValueError("Reminder message is required.")
    timestamp_s, iso_value = _normalize_iso_datetime(payload.get("target_time_iso"))
    owner_key, canonical_user_id, reply_target = _resolve_target_context(payload)
    now = time.time()
    reminder_id = uuid.uuid4().hex[:8]
    reminder: Dict[str, Any] = {
        "id": reminder_id,
        "message": message,
        "timestamp_s": timestamp_s,
        "iso": iso_value,
        "created_at_s": now,
        "updated_at_s": now,
        "creator_owner_key": owner_key,
        "creator_user_id": canonical_user_id,
        "delivery_target": reply_target,
        "lock_reply_target": True,
    }
    reminders = load_json(REMINDERS_FILE)
    reminders.append(reminder)
    save_json(REMINDERS_FILE, reminders)
    append_scheduler_activity_log(
        "reminder_created",
        agent_kind="reminders",
        item_id=reminder_id,
        status="created",
        message=message,
        details={
            "timestamp_s": timestamp_s,
            "owner_key": owner_key,
            "lock_reply_target": True,
        },
    )
    return _normalize_reminder_record(reminder)


def _update_reminder_from_payload(item_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    reminders = load_json(REMINDERS_FILE)
    updated_reminder: Optional[Dict[str, Any]] = None
    now = time.time()
    for reminder in reminders:
        if str(reminder.get("id") or "") != item_id:
            continue
        if "message" in payload:
            message = str(payload.get("message") or "").strip()
            if not message:
                raise ValueError("Reminder message cannot be empty.")
            reminder["message"] = message
        if "target_time_iso" in payload:
            timestamp_s, iso_value = _normalize_iso_datetime(payload.get("target_time_iso"))
            reminder["timestamp_s"] = timestamp_s
            reminder["iso"] = iso_value
        if "delivery_target" in payload or "owner_key" in payload:
            owner_key, canonical_user_id, reply_target = _resolve_target_context(payload)
            reminder["creator_owner_key"] = owner_key
            reminder["creator_user_id"] = canonical_user_id
            reminder["delivery_target"] = reply_target
            reminder["lock_reply_target"] = True
        reminder["updated_at_s"] = now
        updated_reminder = reminder
        break

    if updated_reminder is None:
        raise KeyError(f"Reminder {item_id} not found.")

    save_json(REMINDERS_FILE, reminders)
    append_scheduler_activity_log(
        "reminder_updated",
        agent_kind="reminders",
        item_id=item_id,
        status="updated",
        message=str(updated_reminder.get("message") or ""),
        details={
            "timestamp_s": float(updated_reminder.get("timestamp_s") or 0.0),
            "owner_key": str(updated_reminder.get("creator_owner_key") or ""),
            "lock_reply_target": bool(updated_reminder.get("lock_reply_target")),
        },
    )
    return _normalize_reminder_record(updated_reminder)


def _delete_reminder(item_id: str) -> Dict[str, Any]:
    reminders = load_json(REMINDERS_FILE)
    retained = [reminder for reminder in reminders if str(reminder.get("id") or "") != item_id]
    if len(retained) == len(reminders):
        raise KeyError(f"Reminder {item_id} not found.")
    save_json(REMINDERS_FILE, retained)
    append_scheduler_activity_log(
        "reminder_deleted",
        agent_kind="reminders",
        item_id=item_id,
        status="deleted",
        message=f"Deleted reminder {item_id}.",
    )
    return {"deleted": True, "item_id": item_id}


def _delete_all_reminders() -> Dict[str, Any]:
    reminders = load_json(REMINDERS_FILE)
    removed_ids = [str(reminder.get("id") or "") for reminder in reminders if str(reminder.get("id") or "").strip()]
    save_json(REMINDERS_FILE, [])
    append_scheduler_activity_log(
        "reminder_bulk_deleted",
        agent_kind="reminders",
        status="deleted",
        message=f"Deleted {len(removed_ids)} reminder(s).",
        details={"removed_ids": removed_ids[:50]},
    )
    return {"removed_count": len(removed_ids)}


def _bootstrap_payload(agent_kind: str, request: Request) -> Dict[str, Any]:
    payload = _build_dashboard_payload(agent_kind, request)
    payload["frontend_assets"] = {
        "app_js": "./assets/app.js",
        "styles_css": "./assets/styles.css",
    }
    return payload


def create_scheduler_mission_control_app(agent_kind: str) -> FastAPI:
    normalized_kind = str(agent_kind or "").strip().lower()
    if normalized_kind not in AGENT_META:
        raise ValueError(f"Unsupported scheduler mission-control app kind: {agent_kind!r}")

    meta = AGENT_META[normalized_kind]
    agent_name = meta["agent_name"]

    app = FastAPI(title=meta["title"])
    app.add_middleware(GZipMiddleware, minimum_size=512)
    app.middleware("http")(_agent_app_csrf_guard())
    app.middleware("http")(_shared_session_upgrade_middleware())

    @app.middleware("http")
    async def add_common_headers(request: Request, call_next):
        response = await call_next(request)
        return _apply_headers(response)

    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

    @app.get("/")
    async def mission_control_index(request: Request):
        bootstrap = _bootstrap_payload(normalized_kind, request)
        template = INDEX_HTML_PATH.read_text(encoding="utf-8")
        rendered = (
            template.replace("__MISSION_CONTROL_TITLE__", html.escape(meta["title"], quote=True))
            .replace("__MISSION_CONTROL_BOOTSTRAP_JSON__", json.dumps(bootstrap, ensure_ascii=False))
        )
        return _html_response(
            _inject_frontend_auth_gate(
                rendered,
                title=meta["title"],
                auth=bootstrap.get("auth", {}),
                otp_field="totp_code",
            )
        )

    @app.get("/api/bootstrap")
    async def mission_control_bootstrap(request: Request):
        return _json_response(_bootstrap_payload(normalized_kind, request))

    @app.get("/api/dashboard")
    async def mission_control_dashboard(request: Request):
        auth_error = _api_auth_error(agent_name, request)
        if auth_error:
            return auth_error
        return _json_response(_build_dashboard_payload(normalized_kind, request))

    @app.post("/api/items")
    async def mission_control_create_item(request: Request):
        auth_error = _api_auth_error(agent_name, request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        try:
            if normalized_kind == "tasks":
                item = _create_task_from_payload(payload)
            else:
                item = _create_reminder_from_payload(payload)
        except ValueError as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=400)
        return _json_response({"success": True, "item": item})

    @app.patch("/api/items/{item_id}")
    async def mission_control_update_item(item_id: str, request: Request):
        auth_error = _api_auth_error(agent_name, request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        try:
            if normalized_kind == "tasks":
                item = _update_task_from_payload(item_id, payload)
            else:
                item = _update_reminder_from_payload(item_id, payload)
        except KeyError as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=404)
        except ValueError as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=400)
        return _json_response({"success": True, "item": item})

    @app.delete("/api/items/{item_id}")
    async def mission_control_delete_item(item_id: str, request: Request):
        auth_error = _api_auth_error(agent_name, request)
        if auth_error:
            return auth_error
        try:
            if normalized_kind == "tasks":
                result = _delete_task(item_id)
            else:
                result = _delete_reminder(item_id)
        except KeyError as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=404)
        return _json_response({"success": True, **result})

    @app.post("/api/items/delete-all")
    async def mission_control_delete_all(request: Request):
        auth_error = _api_auth_error(agent_name, request)
        if auth_error:
            return auth_error
        result = _delete_all_tasks() if normalized_kind == "tasks" else _delete_all_reminders()
        return _json_response({"success": True, **result})

    @app.get("/api/fallback-settings")
    async def get_fallback_settings(request: Request):
        auth_error = _api_auth_error(agent_name, request)
        if auth_error:
            return auth_error
        server = _runtime_server()
        cfg = server.STATE.config or server._default_config()
        scheduler_cfg = cfg.get("scheduler", {})
        return _json_response({
            "success": True,
            "fallback_mode": scheduler_cfg.get("fallback_mode", "default"),
            "fallback_max_age_seconds": float(scheduler_cfg.get("fallback_max_age_seconds", 30 * 60)),
        })

    @app.post("/api/fallback-settings")
    async def post_fallback_settings(request: Request):
        auth_error = _api_auth_error(agent_name, request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        fallback_mode = str(payload.get("fallback_mode", "default")).strip().lower()
        if fallback_mode not in {"default", "broadcast", "telegram", "telegram_user", "whatsapp", "signal"}:
            return _json_response({"success": False, "error": f"Invalid fallback mode: {fallback_mode}"}, status_code=400)
        try:
            fallback_max_age_seconds = float(payload.get("fallback_max_age_seconds", 30 * 60))
            if fallback_max_age_seconds <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            return _json_response({"success": False, "error": "Timeout must be a positive number."}, status_code=400)
        server = _runtime_server()
        cfg = _copy_runtime_config()
        scheduler_cfg = cfg.get("scheduler")
        if not isinstance(scheduler_cfg, dict):
            scheduler_cfg = {}
            cfg["scheduler"] = scheduler_cfg
        scheduler_cfg["fallback_mode"] = fallback_mode
        scheduler_cfg["fallback_max_age_seconds"] = fallback_max_age_seconds
        server._persist_state_config(cfg)
        server.STATE.config = cfg
        return _json_response({
            "success": True,
            "fallback_mode": fallback_mode,
            "fallback_max_age_seconds": fallback_max_age_seconds,
        })

    @app.post("/api/auth/login")
    async def mission_control_login(request: Request):
        settings = _get_agent_security_settings(agent_name)
        server = _runtime_server()

        # Already authenticated - skip OTP
        if settings["auth_mode"] == "open":
            auth = _describe_auth_state(request, agent_name)
            return _json_response({"success": True, "auth": auth})

        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)

        totp_code = str(payload.get("totp_code") or "").strip()
        if not totp_code:
            return _json_response({"success": False, "error": "totp_code is required."}, status_code=400)

        # Verify against the agent's OWN per-agent 2FA profile when assigned;
        # otherwise fall back to the single shared pairing TOTP secret.
        if settings.get("per_agent_profile"):
            if not server.verify_agent_assigned_2fa(agent_name, totp_code):
                return _json_response({"success": False, "error": "Invalid authentication code."}, status_code=401)
        else:
            totp_caps = _totp_capabilities()
            if not totp_caps.get("totp_configured"):
                return _json_response({"success": False, "error": "TOTP is not configured on this server."}, status_code=400)

            cfg = server.STATE.config or server._default_config()
            pairing_secret = server._get_pairing_totp_secret(cfg)
            if not server._verify_totp_secret(pairing_secret, totp_code):
                return _json_response({"success": False, "error": "Invalid authentication code."}, status_code=401)

        # Create per-agent mission session
        ttl_days = settings.get("session_ttl_days", DEFAULT_SESSION_TTL_DAYS)
        token = _create_agent_session(agent_name, ttl_days)
        auth = {
            **settings,
            "required": True,
            "authenticated": True,
            "via": "mission_session",
        }
        response = _json_response({"success": True, "token": token, "auth": auth})
        response.set_cookie(
            _agent_cookie_name(agent_name),
            token,
            httponly=True,
            samesite="lax",
            secure=_cookie_should_be_secure(request),
            path="/",
            max_age=int(ttl_days * 86400),
        )
        _maybe_set_shared_session_cookie(response, request, settings)
        return response

    @app.post("/api/auth/logout")
    async def mission_control_logout(request: Request):
        # Remove per-agent session cookie token if present
        cookie_token = request.cookies.get(_agent_cookie_name(agent_name))
        if cookie_token:
            _delete_agent_session(agent_name, cookie_token)
        # Also check Bearer header token
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            bearer_token = auth_header[7:].strip()
            if bearer_token:
                _delete_agent_session(agent_name, bearer_token)
        response = _json_response({"success": True})
        response.delete_cookie(_agent_cookie_name(agent_name), path=_agent_cookie_path(agent_name))
        response.delete_cookie(_agent_cookie_name(agent_name), path="/")
        return response

    @app.get("/api/auth/status")
    async def mission_control_auth_status(request: Request):
        auth = _describe_auth_state(request, agent_name)
        return _json_response({"success": True, "auth": auth})

    return app


# ─── Agent Chat App Factory ───────────────────────────────────────────────────

def create_agent_website_app(
    agent_name: str,
    title: str,
    description: str = "",
    index_path: Optional[Path] = None,
    assets_dir: Optional[Path] = None,
    extra_routes_fn: Optional[Any] = None,
) -> FastAPI:
    """Create a protected static website app for an agent.

    This is the small-app counterpart to ``create_agent_chat_app``. It owns
    the TOTP/session routes and injects the fail-closed frontend gate so a
    custom website cannot accidentally render an interactive locked shell.
    """
    app = FastAPI(title=title, docs_url=None, redoc_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=512)
    app.middleware("http")(_agent_app_csrf_guard())
    app.middleware("http")(_shared_session_upgrade_middleware())

    if assets_dir and assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir), html=False), name="assets")

    @app.get("/")
    async def website_index(request: Request):
        auth = _describe_auth_state(request, agent_name)
        if index_path and index_path.is_file():
            html_content = index_path.read_text(encoding="utf-8")
            bootstrap_json = json.dumps({
                "agent_name": agent_name,
                "title": title,
                "description": description,
                "auth": auth,
            })
            html_content = html_content.replace("__BOOTSTRAP_JSON__", bootstrap_json, 1)
            return _html_response(
                _inject_frontend_auth_gate(
                    html_content,
                    title=title,
                    auth=auth,
                    otp_field="totp_code",
                )
            )
        return _json_response({"agent_name": agent_name, "title": title, "auth": auth})

    @app.get("/api/bootstrap")
    async def website_bootstrap(request: Request):
        auth = _describe_auth_state(request, agent_name)
        return _json_response({
            "success": True,
            "agent_name": agent_name,
            "title": title,
            "description": description,
            "auth": auth,
            "totp": _totp_capabilities(),
        })

    @app.post("/api/auth/login")
    async def website_login(request: Request):
        settings = _get_agent_security_settings(agent_name)
        server = _runtime_server()
        if settings["auth_mode"] == "open":
            return _json_response({"success": True, "auth": _describe_auth_state(request, agent_name)})
        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        code = str(payload.get("totp_code") or payload.get("code") or "").strip()
        if not code:
            return _json_response({"success": False, "error": "OTP code is required."}, status_code=400)

        assigned_profile = False
        try:
            assigned_profile = server.agent_has_assigned_2fa_profile(agent_name) is True
        except Exception:
            pass
        if assigned_profile:
            try:
                verified = bool(server.verify_agent_assigned_2fa(agent_name, code))
            except Exception:
                verified = False
        else:
            totp_caps = _totp_capabilities()
            if not totp_caps.get("totp_configured"):
                return _json_response({"success": False, "error": "2FA is not configured on this server."}, status_code=400)
            cfg = server.STATE.config or server._default_config()
            try:
                verified = bool(server._verify_totp_secret(server._get_pairing_totp_secret(cfg), code))
            except Exception:
                verified = False
        if not verified:
            return _json_response({"success": False, "error": "Invalid OTP code."}, status_code=401)

        token = _create_agent_session(agent_name, settings["session_ttl_days"])
        response = _json_response({"success": True, "token": token})
        cookie_name = _agent_cookie_name(agent_name)
        response.set_cookie(
            cookie_name,
            token,
            httponly=True,
            samesite="lax",
            secure=_cookie_should_be_secure(request),
            max_age=settings["session_ttl_days"] * 86400,
            path="/",
        )
        _maybe_set_shared_session_cookie(response, request, settings)
        return response

    @app.get("/api/auth/status")
    async def website_auth_status(request: Request):
        return _json_response({"success": True, "auth": _describe_auth_state(request, agent_name)})

    @app.post("/api/auth/logout")
    async def website_logout(request: Request):
        cookie_name = _agent_cookie_name(agent_name)
        token = request.cookies.get(cookie_name)
        if token:
            _delete_agent_session(agent_name, token)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            _delete_agent_session(agent_name, auth_header[7:].strip())
        response = _json_response({"success": True})
        response.delete_cookie(cookie_name, path="/")
        response.delete_cookie(cookie_name, path=_agent_cookie_path(agent_name))
        return response

    if extra_routes_fn is not None:
        extra_routes_fn(app, agent_name)

    if index_path:
        frontend_root = index_path.parent.resolve()
        fallback_index = index_path.resolve()

        @app.get("/{full_path:path}")
        async def website_frontend(full_path: str):
            candidate = (frontend_root / full_path).resolve()
            if candidate.is_file() and (candidate == frontend_root or frontend_root in candidate.parents):
                return FileResponse(candidate)
            if fallback_index.is_file():
                return FileResponse(fallback_index)
            return _json_response({"success": False, "error": "Frontend is unavailable."}, status_code=404)

    return app


def _get_ai_agent_base_url() -> str:
    host = os.environ.get("AI_AGENT_HOST", "127.0.0.1")
    port = os.environ.get("AI_AGENT_SERVER_PORT", os.environ.get("AUTOYOU_AI_PORT", "8081"))
    return f"http://{host}:{port}"


def _get_internal_api_token() -> str:
    return str(os.environ.get("AUTOYOU_AI_INTERNAL_API_TOKEN", "") or "").strip()


def _agent_chat_cookie_name(agent_name: str) -> str:
    return f"autoyou_{agent_name}_chat_session"


def _agent_chat_session_is_valid(agent_name: str, token: Optional[str]) -> bool:
    cookie_name = _agent_chat_cookie_name(agent_name)
    bucket_key = f"__chat__{agent_name}"
    return _agent_session_is_valid(bucket_key, token)


def _create_agent_chat_session(agent_name: str, ttl_days: int) -> str:
    bucket_key = f"__chat__{agent_name}"
    return _create_agent_session(bucket_key, ttl_days)


def _delete_agent_chat_session(agent_name: str, token: Optional[str]) -> None:
    bucket_key = f"__chat__{agent_name}"
    _delete_agent_session(bucket_key, token)


def _describe_chat_auth_state(request: Request, agent_name: str) -> Dict[str, Any]:
    settings = _get_agent_security_settings(agent_name)

    if settings["auth_mode"] == "open":
        return {**settings, "required": False, "authenticated": True, "via": "open"}

    cookie_name = _agent_chat_cookie_name(agent_name)
    cookie_token = request.cookies.get(cookie_name)
    if _agent_chat_session_is_valid(agent_name, cookie_token):
        return {**settings, "required": True, "authenticated": True, "via": "chat_session"}

    # Check Authorization: Bearer <token> header (for WebRTC tunnel clients
    # that cannot send cookies across the SCTP/HTTP proxy boundary)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:].strip()
        if _agent_chat_session_is_valid(agent_name, bearer_token):
            return {**settings, "required": True, "authenticated": True, "via": "chat_session"}

    # Opt-in shared cross-agent session (off by default; see
    # agent_websites.shared_session_enabled).
    if _shared_session_authenticates(request, settings):
        return {**settings, "required": True, "authenticated": True, "via": "shared_session"}

    return {**settings, "required": True, "authenticated": False, "via": "none"}


def create_agent_chat_app(
    agent_name: str,
    title: str,
    description: str = "",
    frontend_dir: Optional[Path] = None,
    extra_routes_fn: Optional[Any] = None,
) -> FastAPI:
    """Create a FastAPI chat UI app for an agent with OTP auth and ADK proxy.

    Args:
        agent_name: The agent's snake_case name (e.g. ``'agent_builder_agent'``).
        title: Human-readable title for the UI.
        description: Short description for the UI.
        frontend_dir: Path to the static frontend directory to mount at ``/assets``.
        extra_routes_fn: Optional callable ``fn(app, agent_name)`` to attach extra routes.

    Returns:
        A configured FastAPI application ready for ASGI serving.
    """
    import aiohttp as _aiohttp

    app = FastAPI(title=title, docs_url=None, redoc_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=512)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://127.0.0.1"],
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.middleware("http")(_agent_app_csrf_guard())
    app.middleware("http")(_shared_session_upgrade_middleware())

    if frontend_dir and (frontend_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(frontend_dir / "assets"), html=False), name="assets")

    @app.get("/")
    async def chat_index(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if frontend_dir:
            html_path = frontend_dir / "index.html"
            if html_path.is_file():
                html_content = html_path.read_text(encoding="utf-8")
                bootstrap_json = json.dumps({
                    "agent_name": agent_name,
                    "title": title,
                    "description": description,
                    "auth": auth,
                })
                html_content = html_content.replace("__BOOTSTRAP_JSON__", bootstrap_json, 1)
                return _html_response(
                    _inject_frontend_auth_gate(
                        html_content,
                        title=title,
                        auth=auth,
                        otp_field="code",
                    )
                )
        return _json_response({"agent_name": agent_name, "title": title, "auth": auth})

    @app.get("/api/bootstrap")
    async def chat_bootstrap(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        totp_caps = _totp_capabilities()
        return _json_response({
            "success": True,
            "agent_name": agent_name,
            "title": title,
            "description": description,
            "auth": auth,
            "totp": totp_caps,
        })

    @app.post("/api/auth/login")
    async def chat_login(request: Request):
        settings = _get_agent_security_settings(agent_name)
        server = _runtime_server()
        if settings["auth_mode"] == "open":
            return _json_response({"success": True, "auth": _describe_chat_auth_state(request, agent_name)})
        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        code = str(payload.get("code") or "").strip()
        if not code:
            return _json_response({"success": False, "error": "OTP code is required."}, status_code=400)

        assigned_profile = False
        try:
            assigned_profile = server.agent_has_assigned_2fa_profile(agent_name) is True
        except Exception:
            assigned_profile = False
        if assigned_profile:
            try:
                verified = bool(server.verify_agent_assigned_2fa(agent_name, code))
            except Exception:
                verified = False
        else:
            totp_caps = _totp_capabilities()
            if not totp_caps.get("totp_configured"):
                return _json_response({"success": False, "error": "2FA is not configured on this server."}, status_code=400)
            cfg = server.STATE.config or server._default_config()
            secret = server._get_pairing_totp_secret(cfg)
            try:
                verified = server._verify_totp_secret(secret, code)
            except Exception:
                verified = False
        if not verified:
            return _json_response({"success": False, "error": "Invalid OTP code."}, status_code=401)
        token = _create_agent_chat_session(agent_name, settings["session_ttl_days"])
        cookie_name = _agent_chat_cookie_name(agent_name)
        # Return token in body so WebRTC tunnel clients (which cannot receive
        # SameSite cookies cross-origin) can store it and use Authorization: Bearer
        response = _json_response({"success": True, "token": token})
        response.set_cookie(
            cookie_name,
            token,
            httponly=True,
            samesite="lax",
            secure=_cookie_should_be_secure(request),
            max_age=settings["session_ttl_days"] * 86400,
            path="/",
        )
        _maybe_set_shared_session_cookie(response, request, settings)
        return response

    @app.post("/api/auth/logout")
    async def chat_logout(request: Request):
        cookie_name = _agent_chat_cookie_name(agent_name)
        token = request.cookies.get(cookie_name)
        _delete_agent_chat_session(agent_name, token)
        response = _json_response({"success": True})
        response.delete_cookie(cookie_name, path="/")
        response.delete_cookie(cookie_name, path=f"/agent/{agent_name}")
        return response

    @app.get("/api/auth/status")
    async def chat_auth_status(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        totp_caps = _totp_capabilities()
        return _json_response({"success": True, "auth": auth, "totp": totp_caps})

    @app.post("/api/chat")
    async def chat_message(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        ai_base = _get_ai_agent_base_url()
        token = _get_internal_api_token()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with _aiohttp.ClientSession() as session:
                async with session.post(
                    f"{ai_base}/api/chat",
                    json=payload,
                    headers=headers,
                    timeout=_aiohttp.ClientTimeout(total=120),
                ) as resp:
                    data = await resp.json(content_type=None)
                    return _json_response(data, status_code=resp.status)
        except Exception as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=502)

    @app.post("/api/chat/stream")
    async def chat_stream(request: Request):
        """SSE streaming proxy - streams ADK events from /api/chat/stream."""
        from fastapi.responses import StreamingResponse
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            async def _unauth():
                yield "data: " + json.dumps({"error": "Not authenticated"}) + "\n\n"
            return StreamingResponse(_unauth(), media_type="text/event-stream", status_code=401)
        try:
            payload = await request.json()
        except Exception:
            async def _bad():
                yield "data: " + json.dumps({"error": "Invalid JSON body"}) + "\n\n"
            return StreamingResponse(_bad(), media_type="text/event-stream", status_code=400)
        ai_base = _get_ai_agent_base_url()
        token = _get_internal_api_token()
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async def _stream_generator():
            try:
                async with _aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{ai_base}/api/chat",
                        json={**payload, "stream": True},
                        headers=headers,
                        timeout=_aiohttp.ClientTimeout(total=300, sock_read=None),
                    ) as resp:
                        if resp.content_type == "text/event-stream":
                            async for chunk in resp.content:
                                yield chunk
                        else:
                            data = await resp.json(content_type=None)
                            yield ("data: " + json.dumps(data) + "\n\n").encode()
            except Exception as exc:
                yield ("data: " + json.dumps({"error": str(exc)}) + "\n\n").encode()

        from fastapi.responses import StreamingResponse
        return StreamingResponse(_stream_generator(), media_type="text/event-stream")

    @app.post("/api/security")
    async def chat_save_security(request: Request):
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        # An "open" agent (manifest-declared or admin-configured) reports
        # "authenticated" for every visitor by design - that must not be
        # enough to let an anonymous visitor rewrite THIS agent's own
        # security settings (e.g. silently re-gating a public support page
        # as a griefing/self-lockout DoS). Require the real admin-dashboard
        # session in that case; a normally-gated agent already proved a
        # real OTP code to reach "authenticated", so it keeps working as-is.
        if auth.get("auth_mode") == "open":
            try:
                is_admin = bool(_runtime_server()._is_logged_in(request))
            except Exception:
                is_admin = False
            if not is_admin:
                return _json_response({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        settings = _save_agent_security_settings(
            agent_name,
            auth_mode=str(payload.get("auth_mode", "totp")),
            session_ttl_days=int(payload.get("session_ttl_days", DEFAULT_SESSION_TTL_DAYS)),
        )
        return _json_response({"success": True, "settings": settings})

    if extra_routes_fn is not None:
        extra_routes_fn(app, agent_name)

    return app
