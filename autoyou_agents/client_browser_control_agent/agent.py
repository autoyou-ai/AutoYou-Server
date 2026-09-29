# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-8eb0a7e41da323469a5d99cc

"""Client Browser Control Agent implementation."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import re
import sys
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

from google.adk.agents import Agent

from autoyou_agents.admin_agent.agent import (
    _check_admin_session,
    _extract_text_from_llm_request,
    _get_internal_ai_agent_api_token,
    _get_session_token,
    _http,
)
from autoyou_agents.client_browser_control_agent.prompt import (
    AGENT_DESCRIPTION,
    AGENT_INSTRUCTION,
    AGENT_NAME,
)
from autoyou_agents.donation_agent.agent import get_donation_agent_status
from autoyou_agents.shared_tools.datetime_tool import (
    get_current_datetime,
    inject_realtime_datetime_into_request,
)
from shared.adk_state import (
    AUTOYOU_OWNER_KEY_STATE_KEY,
    AUTOYOU_OWNER_KEY_USER_STATE_KEY,
    AUTOYOU_REPLY_TARGET_STATE_KEY,
    AUTOYOU_REPLY_TARGET_USER_STATE_KEY,
    normalize_reply_target,
    state_get_first,
)
from shared.session_execution import (
    SESSION_CONTROL_STATE_KEY,
    create_tool_call_llm_response,
    normalize_session_control_state,
)

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-8eb0a7e41da323469a5d99cc"


_CLIENT_BROWSER_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY = (
    "autoyou_client_browser_control_dispatch_invocation_id"
)

_OPEN_URL_PATTERN = re.compile(
    r"\b(?:open|load|go\s+to|visit|browse\s+to|show)\b\s+(?P<target>\S+)",
    re.IGNORECASE,
)
_URL_CANDIDATE_PATTERN = re.compile(
    r"\b(?P<target>(?:https?://|www\.)[^\s<>()]+|[a-z0-9][a-z0-9.-]+\.[a-z]{2,}(?:/[^\s<>()]*)?)",
    re.IGNORECASE,
)
_BROWSER_ACTION_PATTERN = re.compile(
    r"\b(?:browser|webview|web\s+view|web\s+browser|site|website|url|page|tab)\b",
    re.IGNORECASE,
)
_OPEN_ACTION_PATTERN = re.compile(r"\b(?:open|load|go\s+to|visit|browse\s+to|show)\b", re.IGNORECASE)
_AUDIO_BROWSER_TARGET_PATTERN = re.compile(
    r"\b(?:audio\s+player|music\s+player|player\s+ui)\b",
    re.IGNORECASE,
)
_AGENT_HOME_TARGET_PATTERN = re.compile(
    r"^(?:open|load|go\s+to|visit|browse\s+to|show)(?:\s+the)?\s+(?:agents(?:\s+(?:web\s+app|app|website|web\s+site|site|page|home|directory|frontend|front\s+end))?|agent\s+(?:web\s+app|app|website|web\s+site|site|page|home|directory|frontend|front\s+end))$",
    re.IGNORECASE,
)
_MAIN_AGENT_TARGET_PATTERN = re.compile(
    r"^(?:open|load|go\s+to|visit|browse\s+to|show)(?:\s+the)?\s+(?:main|root)(?:[_\s]+agent)?\s+(?:web\s+app|app|website|web\s+site|site|page|home|directory|frontend|front\s+end)$",
    re.IGNORECASE,
)
_AGENT_FRONTEND_TARGET_PATTERN = re.compile(
    r"^(?:open|load|go\s+to|visit|browse\s+to|show)(?:\s+the)?\s+(?P<target>.+?)\s+(?:web\s+app|app|website|web\s+site|site|page|frontend|front\s+end)$",
    re.IGNORECASE,
)
_AGENT_FRONTEND_ALIASES = {
    "ad": "ads_watching_agent",
    "ads": "ads_watching_agent",
    "ad watching": "ads_watching_agent",
    "ads watching": "ads_watching_agent",
    "audio": "audio_agent",
    "music": "audio_agent",
    "player": "audio_agent",
    "data collector": "data_collector_agent",
    "fine tuning": "fine_tuning_agent",
    "finetuning": "fine_tuning_agent",
    "hosting": "hosting_agent",
    "note": "notes_agent",
    "notes": "notes_agent",
    "notification": "notify_agent",
    "notifications": "notify_agent",
    "notify": "notify_agent",
    "page": "page_agent",
    "pages": "page_agent",
    "skill": "skills_agent",
    "skills": "skills_agent",
    "task": "tasks_agent",
    "tasks": "tasks_agent",
    "voice training": "voice_training_agent",
}
_KNOWN_AGENT_FRONTEND_NAMES = {
    "ads_watching_agent",
    "audio_agent",
    "data_collector_agent",
    "donation_agent",
    "fine_tuning_agent",
    "hosting_agent",
    "notes_agent",
    "notify_agent",
    "page_agent",
    "skills_agent",
    "tasks_agent",
    "voice_training_agent",
}

def _clean_target_text(value: Any) -> str:
    return str(value or "").strip().strip("\"'<>").rstrip(".,;!?")


def _is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower().strip("[]")
    return normalized in {"localhost", "127.0.0.1", "::1"}


def _normalize_browser_url(raw_url: Any) -> str:
    target = _clean_target_text(raw_url)
    if not target or len(target) > 2048 or any(ch.isspace() for ch in target):
        return ""
    if target.startswith("/") and not target.startswith("//"):
        return target
    if "://" not in target:
        authority = target.split("/", 1)[0]
        if ":" in authority and not authority.rsplit(":", 1)[1].isdigit():
            return ""
        host_hint = authority.rsplit(":", 1)[0].strip("[]")
        if "." not in host_hint and not _is_loopback_host(host_hint):
            return ""
        scheme = "http" if _is_loopback_host(host_hint) else "https"
        target = f"{scheme}://{target}"
    parsed = urlsplit(target)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").strip()
    if scheme not in {"http", "https"} or not host:
        return ""
    if scheme == "http" and not _is_loopback_host(host):
        parsed = parsed._replace(scheme="https")
    return urlunsplit(parsed)


def _extract_requested_url(text: str) -> str:
    explicit = _OPEN_URL_PATTERN.search(text)
    if explicit:
        normalized = _normalize_browser_url(explicit.group("target"))
        if normalized:
            return normalized
    fallback = _URL_CANDIDATE_PATTERN.search(text)
    return _normalize_browser_url(fallback.group("target")) if fallback else ""


def _normalize_agent_frontend_target(value: Any) -> str:
    target = re.sub(r"[_-]+", " ", str(value or "").strip().lower())
    target = re.sub(r"\s+", " ", target).strip(" .,!?:;\"'")
    target = re.sub(r"^(?:the|autoyou)\s+", "", target).strip()
    target = re.sub(r"\s+agent$", "", target).strip()
    return target


def _agent_frontend_path_from_request(text: str) -> str:
    match = _AGENT_FRONTEND_TARGET_PATTERN.search(str(text or "").strip())
    if not match:
        return ""

    target = _normalize_agent_frontend_target(match.group("target"))
    if not target:
        return ""
    if target in {"agent", "agents", "main", "root"}:
        return "__home__"

    agent_name = _AGENT_FRONTEND_ALIASES.get(target)
    # from __debug_provenance_h__ import revenue
    if not agent_name:
        snake = re.sub(r"[^a-z0-9]+", "_", target).strip("_")
        if snake and not snake.endswith("_agent"):
            snake = f"{snake}_agent"
        if snake in _KNOWN_AGENT_FRONTEND_NAMES:
            agent_name = snake
    if not agent_name:
        return ""
    return f"/agent/{agent_name}/"


def _official_autoyou_url(kind: str) -> str:
    if kind == "support":
        return "https://www.autoyou.me/support/"
    try:
        status = get_donation_agent_status()
        url = str(status.get("public_donate_page") or "").strip()
    except Exception:
        url = ""
    return _normalize_browser_url(url) or "https://www.autoyou.me/donate/"


def build_client_browser_control_payload(
    request: str = "",
    *,
    action: str = "",
    url: str = "",
    source: str = "client_browser_control_agent",
) -> Dict[str, Any]:
    """Build a safe browser-control payload for a connected AutoYou app."""
    normalized_request = " ".join(str(request or "").split()).strip()
    lowered = normalized_request.lower()
    requested_action = str(action or "").strip().lower()
    source = str(source or "").strip().lower()
    if source not in {"client_browser_control_agent", "internet_agent", "browser_agent"}:
        source = "client_browser_control_agent"
    requested_url = _normalize_browser_url(url)
    agent_frontend_path = _agent_frontend_path_from_request(normalized_request)

    if not requested_action:
        if re.search(r"\b(reload|refresh)\b", lowered):
            requested_action = "reload"
        elif re.search(r"\b(go\s+)?back\b", lowered) and _BROWSER_ACTION_PATTERN.search(lowered):
            requested_action = "back"
        elif re.search(r"\b(go\s+)?forward\b", lowered) and _BROWSER_ACTION_PATTERN.search(lowered):
            requested_action = "forward"
        elif _AUDIO_BROWSER_TARGET_PATTERN.search(lowered):
            requested_action = "open_url"
            requested_url = "/agent/audio_agent/"
        elif agent_frontend_path == "__home__":
            requested_action = "open_home"
        elif agent_frontend_path:
            requested_action = "open_url"
            requested_url = agent_frontend_path
        elif (
            re.search(r"\b(websites?|agent\s+websites?|browser\s+home)\b", lowered)
            or _AGENT_HOME_TARGET_PATTERN.search(lowered)
            or _MAIN_AGENT_TARGET_PATTERN.search(lowered)
        ):
            requested_action = "open_home"
        elif re.search(r"\b(donation_agent|donation\s+agent)\b", lowered):
            requested_action = "open_url"
            requested_url = "/agent/donation_agent/"
        elif re.search(r"\b(donate|donation|support\s+autoyou)\b", lowered):
            requested_action = "open_url"
            requested_url = _official_autoyou_url("donate")
        elif re.search(r"\bsupport\s+(page|site|website)\b", lowered):
            requested_action = "open_url"
            requested_url = _official_autoyou_url("support")
        else:
            requested_url = requested_url or _extract_requested_url(normalized_request)
            if requested_url and _OPEN_ACTION_PATTERN.search(lowered):
                requested_action = "open_url"

    allowed_actions = {"open_url", "open_home", "reload", "back", "forward"}
    if requested_action not in allowed_actions:
        return {
            "success": False,
            "reason": "Supported client browser controls are open URL, home, reload, back, and forward.",
        }
    if requested_action == "open_url" and not requested_url:
        return {"success": False, "reason": "A safe http(s) URL or local browser path is required."}

    payload: Dict[str, Any] = {
        "event": "client_browser_control",
        "action": requested_action,
        "source": source,
        "platform": "server",
    }
    if requested_url:
        payload["url"] = requested_url
    if normalized_request:
        payload["requested_text"] = normalized_request[:512]
    return payload


def _is_client_browser_control_request(user_text: str) -> bool:
    normalized = " ".join(str(user_text or "").split()).strip()
    if normalized.lower().startswith("[voice transcript]"):
        normalized = normalized[len("[voice transcript]") :].strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    if re.search(r"\b(reload|refresh|back|forward)\b", lowered):
        return bool(_BROWSER_ACTION_PATTERN.search(lowered))
    if _AUDIO_BROWSER_TARGET_PATTERN.search(lowered):
        return bool(_OPEN_ACTION_PATTERN.search(lowered))
    if _agent_frontend_path_from_request(normalized):
        return bool(_OPEN_ACTION_PATTERN.search(lowered))
    if (
        re.search(r"\b(agent\s+websites?|browser\s+home)\b", lowered)
        or _AGENT_HOME_TARGET_PATTERN.search(lowered)
        or _MAIN_AGENT_TARGET_PATTERN.search(lowered)
    ):
        return bool(_OPEN_ACTION_PATTERN.search(lowered))
    if re.search(r"\b(donation_agent|donation\s+agent|donate|donation|support\s+autoyou|support\s+(?:page|site|website))\b", lowered):
        return bool(_OPEN_ACTION_PATTERN.search(lowered))
    return bool(_OPEN_ACTION_PATTERN.search(lowered) and _extract_requested_url(normalized))


def _state_get(state: Any, key: str, default: Any = None) -> Any:
    try:
        return state.get(key, default)
    except Exception:
        try:
            return state[key]
        except Exception:
            return default


def _state_set(state: Any, key: str, value: Any) -> None:
    try:
        state[key] = value
    except Exception:
        pass


def _get_invocation_id(context: Any) -> str:
    return str(getattr(context, "invocation_id", "") or "").strip()


def _tool_dispatch_already_happened(state: Any, invocation_id: str) -> bool:
    if not invocation_id:
        return False
    return (
        str(_state_get(state, _CLIENT_BROWSER_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY, "") or "").strip()
        == invocation_id
    )


def _mark_tool_dispatch(state: Any, invocation_id: str) -> None:
    if invocation_id:
        _state_set(state, _CLIENT_BROWSER_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY, invocation_id)


def _normalize_target_value(value: Any, *, max_length: int = 256) -> str:
    return str(value or "").strip()[:max_length]


def _extract_browser_reply_target(tool_context: Optional[Any]) -> Optional[Dict[str, Any]]:
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    owner_key = str(
        state_get_first(state, AUTOYOU_OWNER_KEY_STATE_KEY, AUTOYOU_OWNER_KEY_USER_STATE_KEY) or ""
    ).strip()
    reply_target = normalize_reply_target(
        state_get_first(state, AUTOYOU_REPLY_TARGET_STATE_KEY, AUTOYOU_REPLY_TARGET_USER_STATE_KEY)
    )
    if reply_target:
        transport = str(reply_target.get("transport") or "").strip().lower()
        if transport == "webrtc" and owner_key and not str(reply_target.get("owner_key") or "").strip():
            reply_target = dict(reply_target)
            reply_target["owner_key"] = owner_key
        if transport == "webrtc":
            return reply_target
        if owner_key:
            return {
                "transport": "webrtc",
                "owner_key": owner_key,
                "_allow_single_live_fallback": True,
            }

    control_state = normalize_session_control_state(state_get_first(state, SESSION_CONTROL_STATE_KEY))
    canonical_session_id = str(control_state.get("canonical_session_id") or "").strip()
    owner_key = str(control_state.get("owner_key") or owner_key or "").strip()
    if canonical_session_id or owner_key:
        target: Dict[str, Any] = {"transport": "webrtc"}
        if canonical_session_id and canonical_session_id.startswith("webrtc:"):
            target["session_id"] = canonical_session_id
        if owner_key:
            target["owner_key"] = owner_key
        target["_allow_single_live_fallback"] = True
        return target
    return None


def _route_missing_404(result: Dict[str, Any], data: Any) -> bool:
    if not isinstance(result, dict) or result.get("code") != 404:
        return False
    if isinstance(data, dict):
        if str(data.get("detail") or "").strip().lower() == "not found":
            return True
        if data.get("success") is False and (data.get("reason") or data.get("error")):
            return False
    return True


def _failure_kind_for_result(result: Dict[str, Any], data: Any, reason: str) -> str:
    code = result.get("code") if isinstance(result, dict) else None
    if code is None:
        return "admin_server_unreachable"
    if code == 409:
        return "multiple_webrtc_clients"
    if _route_missing_404(result, data):
        return "server_route_missing"
    lowered_reason = str(reason or "").lower()
    if code == 404 and ("webrtc" in lowered_reason or "datachannel" in lowered_reason or "client" in lowered_reason):
        return "webrtc_client_unavailable"
    if code and int(code) >= 500:
        return "server_error"
    return "control_failed"


async def _try_in_process_client_browser_control(
    reply_target: Dict[str, Any],
    request_payload: Dict[str, Any],
    *,
    allow_single_live_fallback: bool,
) -> Optional[Dict[str, Any]]:
    for module_name in ("server", "__main__"):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        webrtc = getattr(module, "WEBRTC", None)
        sender = getattr(webrtc, "send_client_browser_control_to_reply_target", None)
        if not callable(sender):
            continue
        try:
            success, status = await sender(
                dict(reply_target),
                dict(request_payload.get("payload") or {}),
                allow_single_live_fallback=allow_single_live_fallback,
            )
        except Exception as exc:
            return {
                "success": False,
                "reason": f"In-process WebRTC client-browser sender failed: {exc}",
                "triggered_count": 0,
                "failure_kind": "in_process_sender_error",
                "fallback": "in_process_webrtc_sender",
            }
        response = dict(status) if isinstance(status, dict) else {"success": bool(success), "reason": str(status or "")}
        response.setdefault("success", bool(success))
        response.setdefault("triggered_count", 1 if success else 0)
        response.setdefault("fallback", "in_process_webrtc_sender")
        if not response.get("success") and "failure_kind" not in response:
            reason = str(response.get("reason") or "")
            response["failure_kind"] = _failure_kind_for_result(
                {"code": 404},
                {"success": False, "reason": reason},
                reason,
            )
        return response
    return None


def _normalize_control_result(result: Dict[str, Any]) -> Dict[str, Any]:
    data = result.get("data") if isinstance(result, dict) else None
    if isinstance(data, dict) and data.get("success") is True:
        return data

    reason = ""
    if isinstance(data, dict):
        reason = str(data.get("reason") or data.get("error") or "").strip()
    if not reason:
        reason = "The client browser command could not be sent to the live AutoYou app."
    if isinstance(result, dict) and _route_missing_404(result, data):
        reason = (
            "The running AutoYou admin server does not expose /api/webrtc/client-browser-control. "
            "Restart AutoYou so it loads the latest browser-control route."
        )
    if isinstance(result, dict) and result.get("code") == 409:
        reason = reason or "Multiple WebRTC clients are connected; specify the target session or owner."

    response: Dict[str, Any] = {
        "success": False,
        "reason": reason,
        "triggered_count": 0,
    }
    if isinstance(result, dict):
        response["http_status"] = result.get("code")
        response["failure_kind"] = _failure_kind_for_result(result, data, reason)
    if isinstance(data, dict):
        for key in ("resolution", "owner_key", "target_session_id"):
            if key in data:
                response[key] = data.get(key)
    return response


async def _send_client_browser_control(
    *,
    payload: Dict[str, Any],
    session_id: str = "",
    owner_key: str = "",
    allow_single_live_fallback: bool = False,
    tool_context: Optional[Any] = None,
) -> Dict[str, Any]:
    token: Optional[str] = None
    if _check_admin_session(tool_context):
        token = _get_session_token(tool_context)
    if not token:
        token = _get_internal_ai_agent_api_token()
    if not token:
        return {
            "success": False,
            "reason": "Internal WebRTC browser-control authorization is unavailable. Restart AutoYou and try again.",
            "triggered_count": 0,
        }

    request_payload: Dict[str, Any] = {
        "payload": dict(payload or {}),
        "allow_single_live_fallback": bool(allow_single_live_fallback),
    }
    normalized_session_id = _normalize_target_value(session_id)
    normalized_owner_key = _normalize_target_value(owner_key)
    if normalized_session_id:
        request_payload["session_id"] = normalized_session_id
    if normalized_owner_key:
        request_payload["owner_key"] = normalized_owner_key

    result = _http(
        "POST",
        "/api/webrtc/client-browser-control",
        request_payload,
        timeout=30,
        token=token,
    )
    normalized_result = result if isinstance(result, dict) else {}
    data = normalized_result.get("data") if isinstance(normalized_result, dict) else None
    if _route_missing_404(normalized_result, data):
        reply_target: Dict[str, Any] = {"transport": "webrtc"}
        if normalized_session_id:
            reply_target["session_id"] = normalized_session_id
        if normalized_owner_key:
            reply_target["owner_key"] = normalized_owner_key
        fallback_result = await _try_in_process_client_browser_control(
            reply_target,
            request_payload,
            allow_single_live_fallback=bool(allow_single_live_fallback),
        )
        if fallback_result is not None:
            fallback_result.setdefault("http_status", normalized_result.get("code"))
            fallback_result.setdefault("http_failure_kind", "server_route_missing")
            return fallback_result

    return _normalize_control_result(normalized_result)


async def _dispatch_client_browser_control_payload(
    payload: Dict[str, Any],
    tool_context: Optional[Any],
) -> Dict[str, Any]:
    reply_target = _extract_browser_reply_target(tool_context)
    if not reply_target:
        return {
            "success": False,
            "reason": "Client browser control requires a live AutoYou app conversation.",
            "triggered_count": 0,
        }

    allow_single_live_fallback = bool(reply_target.pop("_allow_single_live_fallback", False))
    result = await _send_client_browser_control(
        payload=payload,
        session_id=str(reply_target.get("session_id") or "").strip(),
        owner_key=str(reply_target.get("owner_key") or "").strip(),
        allow_single_live_fallback=allow_single_live_fallback,
        tool_context=tool_context,
    )
    if result.get("success"):
        result.setdefault("status", "sent")
        result["action"] = payload.get("action")
        if "url" in payload:
            result["url"] = payload["url"]
    return result


def get_client_browser_control_status() -> Dict[str, Any]:
    return {
        "status": "available",
        "transport": "voice_call_control",
        "commands": ["open_url", "open_home", "reload", "back", "forward"],
        "remote_url_policy": "https_required_except_loopback",
        "local_paths": ["/websites", "/agent/audio_agent/", "/agent/notes_agent/", "/agent/page_agent/"],
    }


async def control_client_browser(
    request: str = "",
    action: str = "",
    url: str = "",
    tool_context: Optional[Any] = None,
) -> Dict[str, Any]:
    """Send a browser-control command to the current WebRTC client."""
    payload = build_client_browser_control_payload(request, action=action, url=url)
    if not payload.get("success", True):
        return payload
    return await _dispatch_client_browser_control_payload(payload, tool_context)


async def _client_browser_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    inject_realtime_datetime_into_request(llm_request)
    user_text = _extract_text_from_llm_request(llm_request)
    if not _is_client_browser_control_request(user_text):
        return None

    invocation_id = _get_invocation_id(callback_context)
    state = getattr(callback_context, "state", None)
    if _tool_dispatch_already_happened(state, invocation_id):
        return None

    _mark_tool_dispatch(state, invocation_id)
    return create_tool_call_llm_response(
        "control_client_browser",
        {"request": user_text},
        custom_metadata={
            "response_author": AGENT_NAME,
            "browser_action": "control_client_browser",
            "browser_deterministic_trigger": True,
        },
    )


def create_client_browser_control_agent(model_config: Any) -> Agent:
    """Create the Client Browser Control Agent."""
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=[
            get_current_datetime,
            get_client_browser_control_status,
            control_client_browser,
        ],
        before_model_callback=_client_browser_before_model_callback,
    )
