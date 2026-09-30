# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-4a0e2a7bd5db183bfcf7b65e

"""Admin agent - full server management via Admin Web API.

Admin session for high-risk operations
---------------------------------------
Some operations (restarting the AI agent, changing models, installing/uninstalling agents)
require an elevated *admin session*.  To obtain one the user must supply a valid 6-digit
TOTP code from the server's admin authenticator secret.

Workflow:
  1. User asks for a high-risk operation.
  2. Admin agent detects no valid session → asks user for their current TOTP code.
  3. User provides the code → agent calls ``verify_admin_totp(totp_code)``.
    4. Server verifies the code against the configured admin TOTP secret.
  5. On success the session token is stored in ADK session state for 1 hour.
  6. Subsequent high-risk calls within the session window proceed without re-verification.
  7. Session can be revoked early via ``revoke_admin_session()``.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import json as _json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlparse

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-4a0e2a7bd5db183bfcf7b65e"


try:
    from google.adk.tools import ToolContext
except ImportError:
    ToolContext = None  # type: ignore[assignment,misc]

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime, inject_realtime_datetime_into_request
from autoyou_agents.shared_tools.agent_install_registry import load_agent_install_registry
from shared.adk_state import (
    AUTOYOU_REPLY_TARGET_STATE_KEY,
    AUTOYOU_REPLY_TARGET_USER_STATE_KEY,
    normalize_reply_target,
    state_get_first,
)
from shared.session_execution import create_text_llm_response, create_tool_call_llm_response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
_MAX_ADMIN_API_TEXT_CHARS = 4096

# ── Session state keys (stored in ADK session state) ─────────────────────────
_SESSION_KEY = "user:admin_session_valid_until"
_SESSION_CLIENT = "user:admin_session_client_id"
_SESSION_TOKEN = "user:admin_session_auth_token"
_LEGACY_SESSION_KEY = "admin_session_valid_until"
_LEGACY_SESSION_CLIENT = "admin_session_client_id"
_LEGACY_SESSION_TOKEN = "admin_session_auth_token"
_SESSION_DURATION = 3600
_INTERNAL_AI_AGENT_API_TOKEN_ENV = "AUTOYOU_AI_INTERNAL_API_TOKEN"
_ADMIN_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY = "_autoyou_admin_tool_dispatch_invocation_id"
_ADMIN_TOOL_RESULT_INVOCATION_ID_STATE_KEY = "_autoyou_admin_tool_result_invocation_id"
_ADMIN_TOOL_RESULT_MESSAGE_STATE_KEY = "_autoyou_admin_tool_result_message"
_ADMIN_TOTP_PENDING_STATE_KEY = "user:admin_totp_pending"
_DEFAULT_ACCOUNT_FUNDING_PATH = "/dashboard?section=funding"
_SUPPORTED_ACCOUNT_OAUTH_PROVIDERS = {"apple", "github", "google"}
_TOTP_REPLY_TEXT_PATTERN = re.compile(
    r"^\s*(?:(?:my\s+)?(?:totp|otp|authenticator|verification)\s+code|(?:totp|otp|code))?\s*(?:is|:|-)?\s*(\d{6})\s*[.!?]?\s*$",
    re.IGNORECASE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_port(explicit_port: Optional[int] = None) -> int:
    """Resolve the Admin App port (default 8001)."""
    if explicit_port is not None:
        try:
            return int(explicit_port)
        except ValueError:
            pass
    for _env_var in ("ADMIN_WEB_SERVICE_PORT", "AUTOYOU_ADMIN_PORT", "AUTOYOU_ADMIN_SPORT"):
        _env_port = os.environ.get(_env_var)
        if _env_port:
            try:
                return int(_env_port)
            except ValueError:
                pass
    return 8001

def _resolve_host(explicit_host: Optional[str] = None) -> str:
    """Resolve the Admin App host (default localhost)."""
    env_host = os.environ.get("ADMIN_WEB_SERVICE_HOST")
    if explicit_host:
        return explicit_host
    if env_host:
        return env_host
    return "localhost"

def _http(
    method: str,
    path: str,
    payload: Optional[Dict] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
    timeout: int = 15,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Call an Admin API endpoint without accepting login redirects as data.

    Returns a dict with keys: status ("success"|"error"), code (int), data (dict), url (str).
    Automatically retries localhost → 127.0.0.1 on connection failure.
    """
    resolved_port = _resolve_port(port)
    resolved_host = _resolve_host(host)
    url = f"http://{resolved_host}:{resolved_port}{path}"

    def _do(u: str) -> Dict[str, Any]:
        _has_requests = False
        try:
            import requests  # type: ignore[import]
            _has_requests = True
        except ImportError:
            pass

        if _has_requests:
            try:
                sess = requests.Session()
                hdrs = {"Accept": "application/json", "Content-Type": "application/json"}
                if token:
                    hdrs["Authorization"] = f"Bearer {token}"
                if method.upper() == "GET":
                    resp = sess.get(
                        u,
                        timeout=timeout,
                        headers=hdrs,
                        allow_redirects=False,
                    )
                else:
                    resp = sess.post(
                        u,
                        json=payload,
                        timeout=timeout,
                        headers=hdrs,
                        allow_redirects=False,
                    )
                status_code = int(resp.status_code)
                if 300 <= status_code < 400:
                    return {
                        "status": "error",
                        "code": status_code,
                        "data": {
                            "error": "Admin API redirected the request. Check the Admin session and API route."
                        },
                        "url": u,
                    }
                content_type = str(resp.headers.get("Content-Type") or "").lower()
                if urlparse(u).path.startswith("/api/") and "text/html" in content_type:
                    return {
                        "status": "error",
                        "code": status_code,
                        "data": {
                            "error": "Admin API returned an HTML page instead of JSON. "
                            "Check the Admin session and API route."
                        },
                        "url": u,
                    }
                try:
                    data = resp.json()
                except ValueError:
                    body = resp.text
                    data = {
                        "text": body[:_MAX_ADMIN_API_TEXT_CHARS],
                        "truncated": len(body) > _MAX_ADMIN_API_TEXT_CHARS,
                    }
                ok = 200 <= status_code < 300
                return {"status": "success" if ok else "error", "code": status_code, "data": data, "url": u}
            except Exception:
                # Connection refused, timeout, DNS failure, etc. - return an error
                # dict so callers can handle gracefully instead of crashing ADK.
                return {"status": "error", "code": None, "data": {}, "url": u}

        # urllib fallback
        import urllib.request as _ur
        import urllib.error as _ue

        class _NoRedirectHandler(_ur.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        try:
            if method.upper() == "GET":
                req = _ur.Request(u, method="GET")
            else:
                body_bytes = _json.dumps(payload or {}).encode()
                req = _ur.Request(u, data=body_bytes, method="POST")
                req.add_header("Content-Type", "application/json")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            req.add_header("Accept", "application/json")
            opener = _ur.build_opener(_NoRedirectHandler)
            with opener.open(req, timeout=timeout) as r:
                status_code = int(r.getcode())
                if r.geturl() != u or 300 <= status_code < 400:
                    return {
                        "status": "error",
                        "code": status_code,
                        "data": {
                            "error": "Admin API redirected the request. Check the Admin session and API route."
                        },
                        "url": u,
                    }
                content_type = str(r.headers.get("Content-Type") or "").lower()
                if urlparse(u).path.startswith("/api/") and "text/html" in content_type:
                    return {
                        "status": "error",
                        "code": status_code,
                        "data": {
                            "error": "Admin API returned an HTML page instead of JSON. "
                            "Check the Admin session and API route."
                        },
                        "url": u,
                    }
                body = r.read(_MAX_ADMIN_API_TEXT_CHARS + 1).decode("utf-8", errors="replace")
                try:
                    data = _json.loads(body)
                except ValueError:
                    data = {
                        "text": body[:_MAX_ADMIN_API_TEXT_CHARS],
                        "truncated": len(body) > _MAX_ADMIN_API_TEXT_CHARS,
                    }
                ok = 200 <= status_code < 300
                return {"status": "success" if ok else "error", "code": status_code, "data": data, "url": u}
        except _ue.HTTPError as he:
            if 300 <= int(he.code) < 400:
                return {
                    "status": "error",
                    "code": int(he.code),
                    "data": {
                        "error": "Admin API redirected the request. Check the Admin session and API route."
                    },
                    "url": u,
                }
            try:
                body = he.read(_MAX_ADMIN_API_TEXT_CHARS + 1).decode("utf-8", errors="replace")
                data = _json.loads(body)
            except Exception:
                data = {}
            return {"status": "error", "code": he.code, "data": data, "url": u}
        except Exception:
            return {"status": "error", "code": None, "data": {}, "url": u}

    result = _do(url)
    # Connection-level failure → retry with 127.0.0.1 if host is localhost
    if result.get("status") == "error" and resolved_host == "localhost" and result.get("code") is None:
        fallback_url = f"http://127.0.0.1:{resolved_port}{path}"
        result = _do(fallback_url)
    return result

def _is_local_development_host(hostname: str | None) -> bool:
    host = str(hostname or "").strip().lower().strip("[]")
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")

def _safe_base_url(value: str, default: str = "https://app.autoyou.me") -> str:
    text = str(value or default).strip().rstrip("/")
    if not text or any(ch.isspace() for ch in text):
        return default
    try:
        parsed = urlparse(text)
    except ValueError:
        return default
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return default
    try:
        hostname = parsed.hostname
    except ValueError:
        return default
    if parsed.scheme == "http" and not _is_local_development_host(hostname):
        return default
    return text

def _safe_account_next_path(value: str) -> str:
    text = str(value or _DEFAULT_ACCOUNT_FUNDING_PATH).strip()
    if not text.startswith("/") or text.startswith("//") or "\\" in text or any(ch.isspace() for ch in text):
        return _DEFAULT_ACCOUNT_FUNDING_PATH
    try:
        parsed = urlparse(text)
    except ValueError:
        return _DEFAULT_ACCOUNT_FUNDING_PATH
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return _DEFAULT_ACCOUNT_FUNDING_PATH
    return text

def get_account_oauth_sign_in(
    provider: str = "google",
    next_path: str = _DEFAULT_ACCOUNT_FUNDING_PATH,
) -> Dict[str, Any]:
    """Return a direct account sign-in URL without requiring Admin TOTP.

    This is a safe account sign-in handoff for Ads Watching credit
    state. It does not create or elevate an AutoYou admin session.
    """
    normalized_provider = str(provider or "google").strip().lower()
    if normalized_provider not in _SUPPORTED_ACCOUNT_OAUTH_PROVIDERS:
        normalized_provider = "google"
    account_url = _safe_base_url(os.getenv("ACCOUNT_PUBLIC_URL", ""), "https://app.autoyou.me")
    safe_next_path = _safe_account_next_path(next_path)
    dashboard_url = f"{account_url}{safe_next_path}"
    oauth_url = (
        f"{account_url}/v1/auth/oauth/{normalized_provider}/start?"
        f"{urlencode({'next': safe_next_path})}"
    )
    return {
        "status": "success",
        "auth": "account_oauth",
        "provider": normalized_provider,
        "requires_totp": False,
        "starts_admin_session": False,
        "account_base_url": account_url,
        "dashboard_url": dashboard_url,
        "oauth_url": oauth_url,
        "message": (
            "Open oauth_url to sign in to the AutoYou account. "
            "This does not require Admin TOTP and does not start an elevated admin session."
        ),
    }

def _resolve_state_container(tool_context_or_state: Any) -> Any:
    if tool_context_or_state is None:
        return None
    try:
        state = getattr(tool_context_or_state, "state", None)
    except Exception:
        state = None
    return state if state is not None else tool_context_or_state

def _state_set(tool_context_or_state: Any, key: str, value: Any) -> None:
    if tool_context_or_state is None or not key:
        return
    state = _resolve_state_container(tool_context_or_state)
    if state is None:
        return
    try:
        state[key] = value
    except Exception as exc:
        logger.warning("Could not store %s in state: %s", key, exc)

def _state_get(tool_context_or_state: Any, *keys: str) -> Any:
    if tool_context_or_state is None:
        return None
    state = _resolve_state_container(tool_context_or_state)
    if state is None:
        return None
    try:
        value = state_get_first(state, *keys)
        if value not in (None, ""):
            return value
        for key in keys:
            if not key:
                continue
            try:
                value = state[key]
            except Exception:
                continue
            if value not in (None, ""):
                return value
    except Exception:
        pass
    return None

def _check_admin_session(tool_context: Any) -> bool:
    """Return True if a valid admin session is currently active."""
    try:
        expiry = _state_get(tool_context, _SESSION_KEY, _LEGACY_SESSION_KEY)
        if expiry and float(expiry) > time.time():
            return True
    except Exception:
        pass
    return False

def _session_time_left(tool_context: Any) -> int:
    """Return seconds remaining in the current admin session (0 if none)."""
    try:
        expiry = _state_get(tool_context, _SESSION_KEY, _LEGACY_SESSION_KEY)
        if expiry:
            left = float(expiry) - time.time()
            return max(0, int(left))
    except Exception:
        pass
    return 0

def _extract_text_from_llm_request(llm_request: Any) -> str:
    for content in reversed(getattr(llm_request, "contents", []) or []):
        if str(getattr(content, "role", "") or "").strip().lower() not in {"", "user"}:
            continue
        parts: List[str] = []
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            return " ".join(parts).strip()
    return ""

def _get_invocation_id(context: Any) -> str:
    return str(getattr(context, "invocation_id", "") or "").strip()

def _tool_dispatch_already_happened(state: Any, invocation_id: str) -> bool:
    if not invocation_id:
        return False
    return str(_state_get(state, _ADMIN_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY) or "").strip() == invocation_id

def _mark_tool_dispatch(state: Any, invocation_id: str) -> None:
    if not invocation_id:
        return
    _state_set(state, _ADMIN_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY, invocation_id)
    _state_set(state, _ADMIN_TOOL_RESULT_INVOCATION_ID_STATE_KEY, "")
    _state_set(state, _ADMIN_TOOL_RESULT_MESSAGE_STATE_KEY, "")

def _extract_totp_reply_code(user_text: str) -> Optional[str]:
    match = _TOTP_REPLY_TEXT_PATTERN.match(str(user_text or ""))
    if not match:
        return None
    return str(match.group(1) or "").strip() or None

def _extract_audio_playback_toggle(user_text: str) -> Optional[bool]:
    lowered = " ".join(str(user_text or "").lower().split())
    if "playback" not in lowered:
        return None
    if re.search(r"\b(enable|turn on|re-enable|reenable|allow)\b", lowered):
        return True
    if re.search(r"\b(disable|turn off)\b", lowered):
        return False
    return None

def _render_admin_tool_response(tool_name: str, tool_response: Dict[str, Any]) -> str:
    message = str(tool_response.get("message") or "").strip()
    if message:
        return message

    if tool_name == "set_audio_playback_enabled":
        data = tool_response.get("data") if isinstance(tool_response, dict) else None
        if isinstance(data, dict) and "enabled" in data:
            enabled = bool(data.get("enabled"))
            return f"Server-side WebRTC audio playback is now {'enabled' if enabled else 'disabled'}."

    if bool(tool_response.get("active")):
        minutes_remaining = int(tool_response.get("minutes_remaining") or 0)
        seconds_remaining = int(tool_response.get("seconds_remaining") or 0)
        return f"Admin session is active. {minutes_remaining} min {seconds_remaining % 60} sec remaining."

    status = str(tool_response.get("status") or "").strip().lower()
    if status == "success":
        return "Admin action completed successfully."
    return "Admin action failed."

def _record_admin_tool_result(state: Any, invocation_id: str, tool_name: str, tool_response: Dict[str, Any]) -> None:
    if not invocation_id:
        return
    _state_set(state, _ADMIN_TOOL_RESULT_INVOCATION_ID_STATE_KEY, invocation_id)
    _state_set(state, _ADMIN_TOOL_RESULT_MESSAGE_STATE_KEY, _render_admin_tool_response(tool_name, tool_response))

def _get_session_token(tool_context: Any) -> Optional[str]:
    """Retrieve the Bearer token currently stored in the active session."""
    try:
        token = _state_get(tool_context, _SESSION_TOKEN, _LEGACY_SESSION_TOKEN)
        return str(token).strip() or None
    except Exception:
        pass
    return None

def _get_internal_ai_agent_api_token() -> Optional[str]:
    try:
        token = str(os.getenv(_INTERNAL_AI_AGENT_API_TOKEN_ENV, "") or "").strip()
        return token or None
    except Exception:
        return None

def _resolve_admin_api_token(
    tool_context: Any,
    *,
    allow_internal_fallback: bool = False,
) -> Optional[str]:
    if _check_admin_session(tool_context):
        token = _get_session_token(tool_context)
        if token:
            return token
    if allow_internal_fallback:
        return _get_internal_ai_agent_api_token()
    return None

def _get_saved_reply_target(state: Any) -> Optional[Dict[str, Any]]:
    return normalize_reply_target(
        state_get_first(
            state,
            AUTOYOU_REPLY_TARGET_STATE_KEY,
            AUTOYOU_REPLY_TARGET_USER_STATE_KEY,
        )
    )

def _dispatch_reply_target_message(
    *,
    message: str,
    reply_target: Dict[str, Any],
    token: str,
    context: Optional[List[Dict[str, Any]]] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_message = str(message or "").strip()
    if not normalized_message:
        return {"status": "error", "message": "message is required"}

    transport = str(reply_target.get("transport") or "").strip().lower()
    if transport == "telegram":
        payload: Dict[str, Any] = {
            "chat_id": reply_target["chat_id"],
            "message": normalized_message,
        }
        if reply_target.get("reply_to_message_id") is not None:
            payload["reply_to_message_id"] = reply_target["reply_to_message_id"]
        if context:
            payload["context"] = list(context)
        result = _http("POST", "/api/telegram/send", payload, port=port, host=host, timeout=30, token=token)
    elif transport == "telegram_user":
        payload = {"message": normalized_message}
        if context:
            payload["context"] = list(context)
        result = _http(
            "POST",
            "/api/telegram-user/send",
            payload,
            port=port,
            host=host,
            timeout=30,
            token=token,
        )
    elif transport == "whatsapp":
        payload = {"to": reply_target["to"], "message": normalized_message}
        if context:
            payload["context"] = list(context)
        result = _http(
            "POST",
            "/api/whatsapp/send",
            payload,
            port=port,
            host=host,
            timeout=30,
            token=token,
        )
    elif transport == "signal":
        payload = {"to": reply_target["to"], "message": normalized_message}
        if context:
            payload["context"] = list(context)
        result = _http(
            "POST",
            "/api/signal/send",
            payload,
            port=port,
            host=host,
            timeout=30,
            token=token,
        )
    elif transport == "webrtc":
        payload = {"message": normalized_message}
        session_id = str(reply_target.get("session_id") or "").strip()
        owner_key = str(reply_target.get("owner_key") or "").strip()
        if session_id:
            payload["session_id"] = session_id
        if owner_key:
            payload["owner_key"] = owner_key
        if context:
            payload["context"] = list(context)
        result = _http(
            "POST",
            "/api/webrtc/send",
            payload,
            port=port,
            host=host,
            timeout=30,
            token=token,
        )
    else:
        return {"status": "error", "message": f"Unsupported reply target transport: {transport}"}

    if result.get("status") == "success":
        label = "Telegram Saved Messages" if transport == "telegram_user" else transport
        result["message"] = f"Sent message via {label}."
    return result

def dispatch_saved_reply_target_message(
    message: str,
    state: Any,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        expiry = state_get_first(state, _SESSION_KEY, _LEGACY_SESSION_KEY)
        if not expiry or float(expiry) <= time.time():
            return {
                "status": "error",
                "message": "Admin session required. Call verify_admin_totp() first.",
            }
    except Exception:
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }

    token = state_get_first(state, _SESSION_TOKEN, _LEGACY_SESSION_TOKEN)
    token_text = str(token or "").strip()
    if not token_text:
        return {
            "status": "error",
            "message": "Admin session token missing. Call verify_admin_totp() first.",
        }

    reply_target = _get_saved_reply_target(state)
    if not reply_target:
        return {
            "status": "error",
            "message": "No saved reply target is available in ADK state.",
        }

    return _dispatch_reply_target_message(
        message=message,
        reply_target=reply_target,
        token=token_text,
        port=port,
        host=host,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Admin session tools (TOTP 2FA)
# ─────────────────────────────────────────────────────────────────────────────

def verify_admin_totp(totp_code: str, tool_context: Optional[Any] = None) -> Dict[str, Any]:
    """Verify a 6-digit TOTP code and start a 1-hour elevated admin session.

    The server checks the code against the configured admin TOTP secret using pyotp.
    On success, the session is stored in ADK session state so subsequent
    high-risk operations can proceed without re-verification for 1 hour.

    This must be called before any high-risk operation (AI agent restart,
    model changes, agent install/uninstall).

    Args:
        totp_code: The current 6-digit TOTP code from the user's authenticator app.
        tool_context: Injected by ADK. Used to store the session token in state.

    Returns:
        dict with ``valid`` (bool) and a status message.
    """
    totp_code = str(totp_code).strip()
    result = _http("POST", "/api/admin/session/verify", {"totp_code": totp_code})
    data = result.get("data", {})
    if result.get("status") == "success" and data.get("valid"):
        client_id = data.get("client_id", "unknown")
        if tool_context is not None:
            expiry = time.time() + _SESSION_DURATION
            _state_set(tool_context, _SESSION_KEY, expiry)
            _state_set(tool_context, _LEGACY_SESSION_KEY, expiry)
            _state_set(tool_context, _SESSION_CLIENT, client_id)
            _state_set(tool_context, _LEGACY_SESSION_CLIENT, client_id)
            if "token" in data:
                _state_set(tool_context, _SESSION_TOKEN, data["token"])
                _state_set(tool_context, _LEGACY_SESSION_TOKEN, data["token"])
        return {
            "valid": True,
            "message": (
                f"Admin session granted for 1 hour (client: {client_id}). "
                "You can now perform high-risk operations."
            ),
        }
    error_msg = data.get("error") or "Invalid TOTP code. Please check your authenticator app."
    return {"valid": False, "message": error_msg}

def check_admin_session(tool_context: Optional[Any] = None) -> Dict[str, Any]:
    """Check whether a valid admin session is currently active.

    Returns the session status and how many minutes remain.

    Args:
        tool_context: Injected by ADK.

    Returns:
        dict with ``active`` (bool), ``minutes_remaining`` (int), ``client_id`` (str).
    """
    active = _check_admin_session(tool_context)
    left = _session_time_left(tool_context)
    client = str(_state_get(tool_context, _SESSION_CLIENT, _LEGACY_SESSION_CLIENT) or "")
    if active:
        return {
            "active": True,
            "minutes_remaining": left // 60,
            "seconds_remaining": left,
            "client_id": client,
            "message": f"Admin session is active. {left // 60} min {left % 60} sec remaining.",
        }

    capability_result = _http("GET", "/api/admin/session/capabilities")
    capability_data = capability_result.get("data", {}) if isinstance(capability_result, dict) else {}
    totp_configured = bool(capability_data.get("totp_configured")) if isinstance(capability_data, dict) else False
    usable_totp_client_count = int(capability_data.get("usable_totp_client_count") or 0) if isinstance(capability_data, dict) else 0
    security_mode = str(capability_data.get("security_mode") or "").strip() if isinstance(capability_data, dict) else ""

    if isinstance(capability_result, dict) and capability_result.get("status") == "success" and not totp_configured:
        return {
            "active": False,
            "minutes_remaining": 0,
            "totp_configured": False,
            "usable_totp_client_count": usable_totp_client_count,
            "security_mode": security_mode,
            "message": (
                "No active admin session, and this server has no usable admin 2FA secret configured yet. "
                "Configure Admin Login 2FA in Admin UI -> Security before attempting admin-session actions."
            ),
        }

    return {
        "active": False,
        "minutes_remaining": 0,
        "totp_configured": True if not isinstance(capability_result, dict) or capability_result.get("status") != "success" else totp_configured,
        "usable_totp_client_count": usable_totp_client_count,
        "security_mode": security_mode,
        "message": (
            "No active admin session. Call verify_admin_totp() with your current "
            "6-digit authenticator code to start a 1-hour elevated session."
        ),
    }

def revoke_admin_session(tool_context: Optional[Any] = None) -> Dict[str, Any]:
    """Revoke the current admin session immediately.

    After calling this, high-risk operations will require re-verification.

    Args:
        tool_context: Injected by ADK.

    Returns:
        dict confirming revocation.
    """
    _state_set(tool_context, _SESSION_KEY, 0)
    _state_set(tool_context, _LEGACY_SESSION_KEY, 0)
    _state_set(tool_context, _SESSION_CLIENT, "")
    _state_set(tool_context, _LEGACY_SESSION_CLIENT, "")
    _state_set(tool_context, _SESSION_TOKEN, "")
    _state_set(tool_context, _LEGACY_SESSION_TOKEN, "")
    return {"revoked": True, "message": "Admin session revoked. Re-verification will be required for future high-risk operations."}

def get_saved_reply_target(tool_context: Optional[Any] = None) -> Dict[str, Any]:
    """Inspect the reply target saved in ADK state for the current user/session."""
    reply_target = _get_saved_reply_target(getattr(tool_context, "state", None) if tool_context is not None else None)
    if not reply_target:
        return {
            "status": "error",
            "message": "No saved reply target is available in ADK state.",
        }
    return {
        "status": "success",
        "reply_target": reply_target,
        "message": f"Saved reply target is configured for {reply_target['transport']}.",
    }

def send_saved_reply_target_message(
    message: str,
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Send a message to the saved reply target using the current admin session."""
    if tool_context is None:
        return {"status": "error", "message": "tool_context is required"}
    return dispatch_saved_reply_target_message(
        message,
        getattr(tool_context, "state", None),
        port=port,
        host=host,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Service restart tools
# ─────────────────────────────────────────────────────────────────────────────

def restart_whatsapp(port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Restart the WhatsApp service via the Admin Web API.

    Calls ``POST /api/whatsapp/restart``. Falls back GET on 405 and retries
    ``127.0.0.1`` if ``localhost`` is unreachable.

    Args:
        port: Override Admin Web port (default from env or 8001).
        host: Override Admin Web host (default from env or localhost).

    Returns:
        dict with status, code, message, data, url.
    """
    result = _http("POST", "/api/whatsapp/restart", port=port, host=host)
    if result.get("status") == "success":
        result["message"] = "WhatsApp service restart requested."
    return result

def restart_signal(port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Restart the Signal service via the Admin Web API.

    Args:
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with status, code, message, data, url.
    """
    result = _http("POST", "/api/signal/restart", port=port, host=host)
    if result.get("status") == "success":
        result["message"] = "Signal service restart requested."
    return result

def restart_ai_agent_server(tool_context: Optional[Any] = None, port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Hot-reload the root AI agent in-process via the Admin Web API.

    Calls ``POST /api/ai/restart``, which re-runs ``initialize_root_agent()``
    inside the running server process.  This picks up any Prompt Override
    override or updated settings WITHOUT killing either the Admin Web server or
    the AI-Agent server process.

    Works in both DEV and compiled (PROD) mode because:
    - Agent code is compiled into the binary and always importable.
    - Only the ADK runner object is replaced; HTTP listeners are untouched.

    **Requires an active admin session** (call ``verify_admin_totp`` first).

    Args:
        tool_context: Injected by ADK. Required for session check.
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with status and message.
    """
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": (
                "Admin session required. Please provide your current TOTP code via "
                "verify_admin_totp() to start a 1-hour elevated session."
            ),
        }
    token = _get_session_token(tool_context)
    result = _http("POST", "/api/ai/restart", port=port, host=host, timeout=20, token=token)
    if result.get("status") == "success":
        result["message"] = (
            "AI agent hot-reload triggered. The new configuration is now active. "
            "No processes were killed - only the ADK runner was replaced in-process."
        )
    return result

# ─────────────────────────────────────────────────────────────────────────────
# Internet search
# ─────────────────────────────────────────────────────────────────────────────

def get_internet_search_enabled(port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Read whether internet/web search is currently enabled for the AI agent.

    Returns:
        dict with ``enabled`` (bool) and status.
    """
    return _http("GET", "/api/ai/internet/search_enabled", port=port, host=host)

def set_internet_search_enabled(enabled: bool, port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Enable or disable internet/web search for the AI agent.

    Args:
        enabled: True to enable, False to disable.
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with status.
    """
    return _http("POST", "/api/ai/internet/search_enabled", {"enabled": bool(enabled)}, port=port, host=host)

def get_audio_playback_enabled(
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Read whether server-side WebRTC audio-file playback is enabled."""
    token = _resolve_admin_api_token(tool_context, allow_internal_fallback=True)
    return _http("GET", "/api/webrtc/playback/enabled", port=port, host=host, token=token)

def set_audio_playback_enabled(
    enabled: bool,
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Enable or disable server-side WebRTC audio-file playback."""
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }
    token = _get_session_token(tool_context)
    if not token:
        return {
            "status": "error",
            "message": "Admin session token missing. Call verify_admin_totp() again before retrying.",
        }
    return _http(
        "POST",
        "/api/webrtc/playback/enabled",
        {"enabled": bool(enabled)},
        port=port,
        host=host,
        token=token,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Model library & selection
# ─────────────────────────────────────────────────────────────────────────────

def get_model_library(port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """List locally installed models and the available model catalog.

    Returns both ``local`` (installed) and ``catalog`` (downloadable) model lists.

    Args:
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with ``local`` list and ``catalog`` list.
    """
    local = _http("GET", "/api/model-library/local", port=port, host=host)
    catalog = _http("GET", "/api/model-library/catalog", port=port, host=host)
    return {
        "status": "success",
        "local_models": local.get("data", {}),
        "catalog": catalog.get("data", {}),
    }

def get_model_details(model_id: str, port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Get detailed information about a specific model.

    Args:
        model_id: The model identifier (e.g. ``llama3.2``, ``gemma2:9b``).
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with model details including size, capabilities, and requirements.
    """
    from urllib.parse import quote

    return _http("GET", f"/api/model-library/details?id={quote(str(model_id), safe='')}", port=port, host=host)

def select_model(
    model_id: str,
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Select the active AI model for the server.

    Changes which model is used for all AI conversations going forward.
    After selecting, you should call ``restart_ai_agent_server()`` for the
    change to fully take effect.

    **Requires an active admin session.**

    Args:
        model_id: The model identifier to activate (e.g. ``llama3.2``, ``gemma2:9b``).
        tool_context: Injected by ADK. Required for session check.
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with status and confirmation.
    """
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }
    token = _get_session_token(tool_context)
    result = _http("POST", "/api/model-library/select", {"model": model_id}, port=port, host=host, token=token)
    if result.get("status") == "success":
        result["message"] = (
            f"Model '{model_id}' selected. Call restart_ai_agent_server() to apply the change."
        )
    return result

def download_model(
    model_id: str,
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Download a model from Ollama or HuggingFace.

    Initiates a background download. Use ``get_model_download_status()`` to
    monitor progress.

    **Requires an active admin session.**

    Args:
        model_id: The model identifier to download (e.g. ``llama3.2``, ``mistral``).
        tool_context: Injected by ADK. Required for session check.
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with ``job_id`` for tracking download progress.
    """
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }
    token = _get_session_token(tool_context)
    result = _http(
        "POST",
        "/api/model-library/download",
        {"source": "ollama", "reference": model_id, "title": model_id},
        port=port,
        host=host,
        token=token,
    )
    if result.get("status") == "success":
        job_id = ((result.get("data") or {}).get("job") or {}).get("job_id")
        result["message"] = (
            f"Download started for '{model_id}'."
            + (f" Track with job_id: {job_id}" if job_id else "")
        )
    return result

def get_model_download_status(
    job_id: Optional[str] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Check the status of active or completed model downloads.

    Args:
        job_id: Specific download job ID to query (optional; omit for all jobs).
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with download status and progress.
    """
    if job_id:
        return _http("GET", f"/api/model-library/downloads/{job_id}", port=port, host=host)
    return _http("GET", "/api/model-library/downloads", port=port, host=host)

def delete_model(
    model_id: str,
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete a locally installed Ollama model from disk.

    Permanently removes the model files. If the deleted model is the active
    model, select another model before restarting the AI runtime.

    **Requires an active admin session.**

    Args:
        model_id: The installed model name/tag to delete (e.g. ``llama3.2:latest``).
        tool_context: Injected by ADK. Required for session check.
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with status and confirmation.
    """
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }
    token = _get_session_token(tool_context)
    result = _http("POST", "/api/model-library/delete", {"model": model_id}, port=port, host=host, token=token)
    data = result.get("data") or {}
    if result.get("status") == "success":
        result["message"] = data.get("warning") or data.get("message") or f"Deleted local model '{model_id}'."
    else:
        result["message"] = data.get("error") or f"Failed to delete model '{model_id}'."
    return result

def get_model_behavior(port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Get the current AI model behavior mode and LiteLLM parameters.

    Returns:
        dict with ``mode`` (accurate/creative/human/none) and resolved parameters.
    """
    return _http("GET", "/api/model-behavior", port=port, host=host)

def set_model_behavior(
    mode: str,
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Set the AI model behavior mode.

    Behavior modes control temperature and sampling parameters:
    - ``accurate``: Low temperature, factual responses.
    - ``creative``: Higher temperature, more varied responses.
    - ``human``: Balanced, conversational tone.
    - ``none``: No override - use model defaults.

    **Requires an active admin session.**

    Args:
        mode: One of ``accurate``, ``creative``, ``human``, ``none``.
        tool_context: Injected by ADK. Required for session check.
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with updated behavior configuration.
    """
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }
    valid_modes = {"accurate", "creative", "human", "none"}
    if mode not in valid_modes:
        return {"status": "error", "message": f"Invalid mode '{mode}'. Choose from: {sorted(valid_modes)}"}
    token = _get_session_token(tool_context)
    return _http("POST", "/api/model-behavior", {"mode": mode}, port=port, host=host, token=token)

# ─────────────────────────────────────────────────────────────────────────────
# Speech / STT / TTS
# ─────────────────────────────────────────────────────────────────────────────

def get_speech_model_status(port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Get the current speech (STT/TTS) model status and configuration.

    Returns information about which Whisper STT model is active, TTS voice,
    and available speech models.

    Returns:
        dict with STT/TTS configuration and status.
    """
    return _http("GET", "/api/speech-models/status", port=port, host=host)

def download_speech_model(
    model_name: str,
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Download a speech (Whisper STT) model.

    Available Whisper models: ``tiny``, ``base``, ``small``, ``medium``, ``large``.
    Larger models are more accurate but slower and use more memory.

    **Requires an active admin session.**

    Args:
        model_name: Whisper model size (e.g. ``base``, ``small``, ``medium``).
        tool_context: Injected by ADK. Required for session check.
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with download job information.
    """
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }
    token = _get_session_token(tool_context)
    result = _http("POST", "/api/speech-models/download", {"model": model_name}, port=port, host=host, token=token)
    if result.get("status") == "success":
        result["message"] = (
            f"Speech model '{model_name}' download started. "
            "Use get_speech_model_download_status() to monitor progress."
        )
    return result

def delete_speech_model(
    model_name: str,
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete a cached speech (Whisper STT) model from the local cache.

    Permanently removes the cached faster-whisper model files. The selected
    STT model will be re-downloaded on next use if it is deleted.

    **Requires an active admin session.**

    Args:
        model_name: Whisper model name to delete (e.g. ``small.en``, ``medium``).
        tool_context: Injected by ADK. Required for session check.
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with status and confirmation.
    """
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }
    token = _get_session_token(tool_context)
    result = _http("POST", "/api/speech-models/delete", {"model": model_name}, port=port, host=host, token=token)
    data = result.get("data") or {}
    if result.get("status") == "success":
        result["message"] = data.get("warning") or data.get("message") or f"Deleted cached STT model '{model_name}'."
    else:
        result["message"] = data.get("error") or f"Failed to delete STT model '{model_name}'."
    return result

def get_speech_model_download_status(port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Check the status of active speech model downloads.

    Returns:
        dict with download jobs and their progress.
    """
    return _http("GET", "/api/speech-models/downloads", port=port, host=host)

# ─────────────────────────────────────────────────────────────────────────────
# Agent install / uninstall
# ─────────────────────────────────────────────────────────────────────────────

def get_installed_agents(port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """List installed and available AutoYou sub-agents.

    Returns the current install registry: which agents are active vs available.

    Returns:
        dict with ``installed_agents`` list and ``available_agents`` list.
    """
    del port, host
    try:
        registry = load_agent_install_registry()
    except Exception as exc:
        logger.warning("Could not read the local agent install registry: %s", exc)
        return {"status": "error", "message": "Could not read the local agent install registry."}
    return {
        "status": "success",
        "installed_agents": list(registry.get("installed_agents") or []),
        "available_agents": list(registry.get("available_agents") or []),
    }

def install_agent(
    agent_name: str,
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Install (enable) a sub-agent.

    After installing, call ``restart_ai_agent_server()`` for the change to take effect.

    **Requires an active admin session.**

    Args:
        agent_name: Name of the agent to install (e.g. ``coding_agent``, ``notes_agent``).
        tool_context: Injected by ADK. Required for session check.
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with install result.
    """
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }
    token = _get_session_token(tool_context)
    result = _http("POST", "/api/agents/install", {"agent_name": agent_name}, port=port, host=host, token=token)
    if result.get("status") == "success":
        result["message"] = (
            f"Agent '{agent_name}' installed. Call restart_ai_agent_server() to activate it."
        )
    return result

def uninstall_agent(
    agent_name: str,
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Uninstall (disable) a sub-agent.

    After uninstalling, call ``restart_ai_agent_server()`` for the change to take effect.

    **Requires an active admin session.**

    Args:
        agent_name: Name of the agent to uninstall.
        tool_context: Injected by ADK. Required for session check.
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with uninstall result.
    """
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }
    token = _get_session_token(tool_context)
    result = _http("POST", "/api/agents/uninstall", {"agent_name": agent_name}, port=port, host=host, token=token)
    # from __debug_provenance_y__ import legal
    if result.get("status") == "success":
        result["message"] = (
            f"Agent '{agent_name}' uninstalled. Call restart_ai_agent_server() to deactivate it."
        )
    return result

# ─────────────────────────────────────────────────────────────────────────────
# Service status tools
# ─────────────────────────────────────────────────────────────────────────────

def get_ai_agent_status(port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Get the current status of the AI agent server.

    Returns:
        dict with running state, port, and process info.
    """
    return _http("GET", "/api/ai-agent-server/status", port=port, host=host)

def get_signal_status(port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Get Signal messaging service status and integration state.

    Returns:
        dict with Signal paired state, device name, and service health.
    """
    return _http("GET", "/api/signal/detailed-status", port=port, host=host)

def get_whatsapp_status(port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Get WhatsApp messaging service status.

    Returns:
        dict with WhatsApp connection state and QR status.
    """
    return _http("GET", "/api/whatsapp/status", port=port, host=host)

def get_tunnelmole_status(port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Get Tunnelmole (public tunnel) status and current public URL.

    Returns:
        dict with tunnel state and public URL if active.
    """
    return _http("GET", "/api/tunnelmole/status", port=port, host=host)

def get_server_overview(port: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
    """Get a comprehensive overview of server status.

    Combines AI agent status, speech model status, model behavior, and
    internet search state into a single summary.

    Returns:
        dict with combined server state overview.
    """
    ai_status = _http("GET", "/api/ai-agent-server/status", port=port, host=host)
    speech = _http("GET", "/api/speech-models/status", port=port, host=host)
    behavior = _http("GET", "/api/model-behavior", port=port, host=host)
    internet = _http("GET", "/api/ai/internet/search_enabled", port=port, host=host)
    audio_playback = _http("GET", "/api/webrtc/playback/enabled", port=port, host=host)
    return {
        "status": "success",
        "ai_agent": ai_status.get("data", {}),
        "speech_models": speech.get("data", {}),
        "model_behavior": behavior.get("data", {}),
        "internet_search": internet.get("data", {}),
        "audio_playback": audio_playback.get("data", {}),
    }

# ─────────────────────────────────────────────────────────────────────────────
# Generic web API executor
# ─────────────────────────────────────────────────────────────────────────────

def admin_web_api_call(
    method: str,
    endpoint: str,
    payload_json: Optional[str] = None,
    tool_context: Optional[Any] = None,
    port: Optional[int] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a generic web API call to the Admin Server on behalf of the user.

    Can be used to modify settings, execute routines, and query advanced states.
    For example: POST /api/settings/save

    **Requires an active admin session.**

    Args:
        method: HTTP method (e.g. GET, POST, PUT, DELETE).
        endpoint: API path (e.g. /api/ai/restart). MUST start with /.
        payload_json: Optional JSON string body.
        tool_context: Injected by ADK. Required for session authentication.
        port: Override Admin Web port.
        host: Override Admin Web host.

    Returns:
        dict with the API response status and data.
    """
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }
    
    payload = None
    if payload_json:
        try:
            payload = _json.loads(payload_json)
        except _json.JSONDecodeError as exc:
            return {"status": "error", "message": f"Invalid JSON payload: {exc}"}

    token = _get_session_token(tool_context)
    result = _http(method, endpoint, payload, port, host, timeout=30, token=token)
    return result

# ─────────────────────────────────────────────────────────────────────────────
# Software update (authorized-server signed manifest + origin/main sync)
# ─────────────────────────────────────────────────────────────────────────────

def get_update_status(
    tool_context: Optional[Any] = None,
    timeout: int = 15,
) -> Dict[str, Any]:
    """Check whether an AutoYou software update is available.

    Uses the authenticated Admin API, which enforces the saved software-update
    preference and linked AutoYou account before making any update request.

    Returns:
        dict with ``current_version``, ``latest_version``, ``update_available``,
        ``install_kind`` (source/packaged), and the platform artifact, or an
        error message (e.g. when no trusted update key is configured).
    """
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }
    result = _http(
        "GET",
        "/api/software-update/status",
        timeout=timeout,
        token=_get_session_token(tool_context),
    )
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return {"status": "success" if result.get("status") == "success" and data.get("success") else "error", **data}

def apply_software_update(
    tool_context: Optional[Any] = None,
    confirm: bool = False,
    timeout: int = 600,
) -> Dict[str, Any]:
    """Apply an available AutoYou software update.

    Source installs fast-forward the checkout to ``origin/main`` (refused if the
    working tree is dirty or diverged). Packaged installs download and SHA-256
    verify the signed artifact named in the manifest and stage it for the OS
    installer. After a successful source sync, restart AutoYou to load new code.

    **Requires an active admin session**, and ``confirm=True`` to proceed.

    Args:
        tool_context: Injected by ADK. Required for the admin-session check.
        confirm: Must be True to actually apply the update.
        timeout: Network timeout (seconds) for artifact download.
    """
    if not _check_admin_session(tool_context):
        return {
            "status": "error",
            "message": "Admin session required. Call verify_admin_totp() first.",
        }
    if not confirm:
        return {
            "status": "needs_confirmation",
            "message": "Re-call apply_software_update with confirm=true to proceed.",
        }
    result = _http(
        "POST",
        "/api/software-update/apply",
        {},
        timeout=timeout,
        token=_get_session_token(tool_context),
    )
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return {"status": "success" if result.get("status") == "success" and data.get("success") else "error", **data}
