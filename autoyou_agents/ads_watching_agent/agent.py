# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-11e0baf6e7f768b276866bfa

"""Ads Watching Agent implementation."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-11e0baf6e7f768b276866bfa"


import os
import re
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from google.adk.agents import Agent

from autoyou_agents.admin_agent.agent import (
    _check_admin_session,
    _get_internal_ai_agent_api_token,
    _get_session_token,
    _extract_text_from_llm_request,
    _http,
)
from autoyou_agents.ads_watching_agent.prompt import (
    AGENT_DESCRIPTION,
    AGENT_INSTRUCTION,
    AGENT_NAME,
)
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
from shared.session_execution import SESSION_CONTROL_STATE_KEY, normalize_session_control_state
from shared.session_execution import create_tool_call_llm_response


_ADS_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY = "autoyou_ads_watching_tool_dispatch_invocation_id"

# The verb and the ad noun must sit next to each other, separated only by
# articles and ad qualifiers. The previous pattern used an unbounded `.*` over
# text that `_normalized_ad_trigger_text` has already collapsed to a single
# line, so a dictated note containing "...I want to start like building..." and
# a later "...ad mob..." matched across ~3000 characters and fired a rewarded
# ad on a note-taking request. Bare `add` is gone from the loose pattern for
# the same reason - it is an ordinary verb, not an ad noun - and survives only
# in the tight two-word phrases below, where it is unambiguously a typo.
_REWARDED_AD_TRIGGER_PATTERNS = (
    re.compile(
        r"\b(?:start|trigger|show|watch|play|open|run)\b"
        r"(?:\s+(?:the|a|an|me|my|another|one|some|next))*"
        r"(?:\s+(?:native|rewarded|video|full|short))*"
        r"\s+ads?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:native\s+(?:ad|add)|rewarded\s+(?:ad|add)|watch\s+(?:ad|add)|trigger\s+(?:ad|add))\b",
        re.IGNORECASE,
    ),
)

# A dictated paragraph is content, not a command. Beyond this length an
# incidental "watch ad" inside prose should not start a rewarded ad.
_REWARDED_AD_TRIGGER_MAX_CHARS = 240

_REWARDED_AD_SHORT_TRIGGER_TEXTS = {
    "start",
    "start it",
    "start now",
    "trigger",
    "trigger it",
    "trigger now",
    "show it",
    "watch",
    "watch now",
    "yes",
    "do it",
    "run it",
}

_REWARDED_AD_CANCEL_TEXTS = {
    "skip",
    "cancel",
    "stop",
    "never mind",
    "nevermind",
}


def _env_flag_enabled_default_true(name: str) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _web_rewarded_ad_trigger_enabled() -> bool:
    return _env_flag_enabled_default_true("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED")


def _default_web_rewarded_ad_unit() -> str:
    # Keep the server override aligned with the desktop release contract while
    # accepting the shorter local-runtime name used by source deployments.
    for env_name in (
        "AUTOYOU_AD_MANAGER_WEB_REWARDED_AD_UNIT_PATH",
        "AUTOYOU_DESKTOP_REWARDED_AD_WEB_REWARDED_AD_UNIT_PATH",
    ):
        web_unit = _coerce_web_rewarded_ad_unit_hint(os.getenv(env_name, ""))
        if web_unit:
            return web_unit
    return ""


def _web_ad_page_fallback_enabled() -> bool:
    return _env_flag_enabled_default_true("AUTOYOU_WEB_AD_PAGE_FALLBACK_ENABLED")


def _web_ad_page_url() -> str:
    raw_url = str(
        os.getenv("AUTOYOU_DESKTOP_REWARDED_AD_URL", "https://autoyou.me/rewards/watch")
        or "https://autoyou.me/rewards/watch"
    ).strip()
    if any(ch.isspace() for ch in raw_url):
        return "https://autoyou.me/rewards/watch"
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return "https://autoyou.me/rewards/watch"
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return "https://autoyou.me/rewards/watch"
    return urlunparse(parsed._replace(query=""))


def _web_ad_page_target(*, include_provider_config: bool = False) -> str:
    """Build the page target without putting provider config in status readback."""
    target = _web_ad_page_url()
    if not include_provider_config:
        return target
    parsed = urlparse(target)
    query = parse_qs(parsed.query, keep_blank_values=True)
    web_unit = _resolve_web_rewarded_ad_unit("")
    if web_unit and _web_rewarded_ad_trigger_enabled():
        query["web_rewarded_ad_unit"] = [web_unit]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def get_web_ad_fallback(*, include_provider_config: bool = False) -> Dict[str, Any]:
    """Return a safe browser-page fallback for the Ads Watching UI."""
    web_unit = _resolve_web_rewarded_ad_unit("")
    available = bool(web_unit) and _web_ad_page_fallback_enabled() and _web_rewarded_ad_trigger_enabled()
    return {
        "available": available,
        "url": _web_ad_page_target(include_provider_config=include_provider_config) if available else "",
        "mode": "gpt_rewarded" if available else "web_page",
    }


def _native_mobile_ad_trigger_enabled() -> bool:
    return _env_flag_enabled_default_true("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED")


def _resolve_web_rewarded_ad_unit(raw_value: str = "") -> str:
    web_rewarded_ad_unit = _coerce_web_rewarded_ad_unit_hint(
        raw_value if raw_value is not None else ""
    )
    if web_rewarded_ad_unit:
        return web_rewarded_ad_unit
    return _default_web_rewarded_ad_unit()


def _rewarded_ad_trigger_enabled(web_rewarded_ad_unit: str = "") -> bool:
    if _native_mobile_ad_trigger_enabled():
        return True
    return _web_rewarded_ad_trigger_enabled() and bool(_resolve_web_rewarded_ad_unit(web_rewarded_ad_unit))


def _reward_full_watch_seconds() -> int:
    try:
        parsed = int(float(os.getenv("AUTOYOU_AD_REWARD_FULL_WATCH_SECONDS", "30") or "30"))
    except (TypeError, ValueError):
        parsed = 30
    return max(1, min(3600, parsed))


def _normalized_ad_trigger_text(user_text: str) -> str:
    return " ".join(str(user_text or "").strip().lower().split())


def _is_rewarded_ad_trigger_request(user_text: str) -> bool:
    normalized = _normalized_ad_trigger_text(user_text)
    if not normalized or normalized in _REWARDED_AD_CANCEL_TEXTS:
        return False
    if normalized in _REWARDED_AD_SHORT_TRIGGER_TEXTS:
        return True
    if len(normalized) > _REWARDED_AD_TRIGGER_MAX_CHARS:
        return False
    return any(pattern.search(normalized) for pattern in _REWARDED_AD_TRIGGER_PATTERNS)


def _get_invocation_id(context: Any) -> str:
    return str(getattr(context, "invocation_id", "") or "").strip()


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


def _ads_tool_dispatch_already_happened(state: Any, invocation_id: str) -> bool:
    if not invocation_id:
        return False
    return str(_state_get(state, _ADS_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY, "") or "").strip() == invocation_id


def _mark_ads_tool_dispatch(state: Any, invocation_id: str) -> None:
    if invocation_id:
        _state_set(state, _ADS_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY, invocation_id)


def get_ads_watching_status() -> Dict[str, Any]:
    """Return the local native-ad orchestration status."""
    web_rewarded_ad_unit = _resolve_web_rewarded_ad_unit("")
    native_mobile_trigger_enabled = _native_mobile_ad_trigger_enabled()
    web_rewarded_ad_enabled = (
        bool(web_rewarded_ad_unit) and _web_rewarded_ad_trigger_enabled()
    )
    web_page_fallback_enabled = bool(get_web_ad_fallback().get("available"))
    return {
        "status": "configured" if native_mobile_trigger_enabled or web_rewarded_ad_enabled or web_page_fallback_enabled else "disabled",
        "live_ad_units": {
            "web_rewarded": web_rewarded_ad_enabled,
            "web_page": web_page_fallback_enabled,
            "android_rewarded": bool(native_mobile_trigger_enabled),
            "ios_rewarded": bool(native_mobile_trigger_enabled),
        },
        "desktop_connect_web_policy": {
            "configured_by": "signed_client_private_release_env",
            "server_web_ad_unit_override": "approved_gpt_web_fallback_only",
            "completion_proof": "not_emitted",
            "cash_crypto_or_transferable_value": "not_available",
        },
        "runtime_flags": {
            "native_mobile_ad_trigger": (
                "enabled" if native_mobile_trigger_enabled else "disabled"
            ),
            "web_rewarded_ad_unit": "enabled" if web_rewarded_ad_enabled else "disabled",
            "web_page_fallback": "enabled" if web_page_fallback_enabled else "disabled",
        },
        "web_rewarded_ad_unit": web_rewarded_ad_unit,
        "support_ad_boundary": {
            "native_agent_trigger": "client_native_ad_trigger_no_account_required",

            "agent_website_targeting": "webrtc_headers_or_single_live_client_when_available",
            "native_settings_trigger": "direct_ios_android_no_webrtc_required",
            "webrtc_liveness": "advisory_for_remote_browser_readback_not_native_ad_availability",
            "native_ad_unit_source": "baked_into_client_builds",
            "server_ad_unit_dependency": "none_for_native_mobile_trigger",
            "web_page_fallback": "approved_gpt_rewarded_only",
        },
        "safety_limits": {
            "can_fake_ad_views": False,
            "can_trigger_native_mobile_ad": bool(native_mobile_trigger_enabled),
            "can_trigger_web_rewarded_ad": web_rewarded_ad_enabled,
            "can_open_web_ad_page": web_page_fallback_enabled,
            "account_service_required": False,
        },
    }


def describe_ads_monetization_path() -> Dict[str, Any]:
    """Describe the current AutoYou support-ad path."""
    return {
        "support_ads": [
            "Use the baked AutoYou-owned AdMob app and rewarded unit IDs in iOS and Android store clients.",
            "The Ads Watching website can target the current mobile client through trusted WebRTC browser headers, while native iOS/Android Settings can start the same support ad directly without account sign-in or WebRTC.",
            "Desktop clients open the public Watch Ad page in the user's browser. A web reward is available only when a Google Ad Manager rewarded slot is configured in the private release environment.",
        ],
        "user_value_boundary": [
            "iOS and Android apps can keep pending reward credits for watched ads.",
            "Pending reward credits are for future AutoYou Cloud time and subscription gifts.",
            "Do not promise cash, crypto, gift cards, transfers, or account value.",
            "The server passes only the approved Google Ad Manager rewarded slot path to the Watch Ad page; it does not expose customer ids, mobile ad unit ids, or account credentials.",

            "Do not fake ad views, refresh ads, automate views, or route traffic through proxies.",
        ],
        "desktop_web_route": [
            "Use https://autoyou.me/rewards/watch for desktop web ads.",
            "Do not use app.autoyou.me or account-service for desktop ads.",
            "The web page loads Google ad scripts only after the user chooses Watch Ad.",

        ],
    }


def _extract_ads_reply_target(tool_context: Optional[Any]) -> Optional[Dict[str, Any]]:
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    owner_key = str(
        state_get_first(state, AUTOYOU_OWNER_KEY_STATE_KEY, AUTOYOU_OWNER_KEY_USER_STATE_KEY)
        or ""
    ).strip()
    reply_target = normalize_reply_target(
        state_get_first(
            state,
            AUTOYOU_REPLY_TARGET_STATE_KEY,
            AUTOYOU_REPLY_TARGET_USER_STATE_KEY,
        )
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


def _rewarded_ad_control_payload() -> Dict[str, Any]:
    control_payload = {
        "event": "show_rewarded_ad",
        "platform": "server",
        "source": "ads_watching_agent",
        "native_ad_unit_source": "client_baked_autoyou_build",
        "desktop_web_ad_config_source": "signed_client_baked_release_env",
        "ad_unit_ids_in_payload": False,
        "expected_watch_seconds": _reward_full_watch_seconds(),
    }
    return control_payload


def _normalize_target_value(value: Any, *, max_length: int = 256) -> str:
    return str(value or "").strip()[:max_length]


def _coerce_web_rewarded_ad_unit_hint(raw_value: Any, *, max_length: int = 768) -> str:
    text = str(raw_value or "").strip()
    if not text or len(text) > max_length:
        return ""
    if any(ch.isspace() for ch in text):
        return ""
    return text


def _route_missing_404(result: Dict[str, Any], data: Any) -> bool:
    if not isinstance(result, dict) or result.get("code") != 404:
        return False
    if isinstance(data, dict):
        if str(data.get("detail") or "").strip().lower() == "not found":
            return True
        if data.get("success") is False and (data.get("reason") or data.get("error")):
            return False
    return True


def _failure_kind_for_rewarded_ad_result(result: Dict[str, Any], data: Any, reason: str) -> str:
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
    if code == 403:
        return "native_mobile_trigger_disabled"
    if code and int(code) >= 500:
        return "server_error"
    return "trigger_failed"


async def _try_in_process_rewarded_ad(
    reply_target: Dict[str, Any],
    request_payload: Dict[str, Any],
    *,
    allow_single_live_fallback: bool,
) -> Optional[Dict[str, Any]]:
    """Fallback for same-process AI/admin runtimes whose HTTP route is stale.

    Do not import ``server`` here. Source runs often execute ``server.py`` as
    ``__main__``; importing it by name would create a second module with empty
    WebRTC state. Only reuse modules that are already loaded in this process.
    """

    for module_name in ("server", "__main__"):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        webrtc = getattr(module, "WEBRTC", None)
        sender = getattr(webrtc, "send_rewarded_ad_control_to_reply_target", None)
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
                "reason": f"In-process WebRTC rewarded-ad sender failed: {exc}",
                "triggered_count": 0,
                "failure_kind": "in_process_sender_error",
                "fallback": "in_process_webrtc_sender",
            }
        if isinstance(status, dict):
            response = dict(status)
        else:
            response = {
                "success": bool(success),
                "reason": str(status or ""),
            }
        response.setdefault("success", bool(success))
        response.setdefault("triggered_count", 1 if success else 0)
        response.setdefault("fallback", "in_process_webrtc_sender")
        if not response.get("success") and "failure_kind" not in response:
            reason = str(response.get("reason") or "")
            response["failure_kind"] = _failure_kind_for_rewarded_ad_result(
                {"code": 404},
                {"success": False, "reason": reason},
                reason,
            )
        return response
    return None


def _rewarded_ad_disabled_response() -> Dict[str, Any]:
    return {
        "success": False,
        "reason": "Rewarded-ad triggering is disabled on this AutoYou server.",
        "triggered_count": 0,
    }


def _rewarded_ad_unavailable_auth_response() -> Dict[str, Any]:
    return {
        "success": False,
        "reason": "Internal WebRTC rewarded-ad authorization is unavailable. Restart the AI agent or AutoYou and try again.",
        "triggered_count": 0,
    }


def _normalize_rewarded_ad_result(result: Dict[str, Any]) -> Dict[str, Any]:
    data = result.get("data") if isinstance(result, dict) else None
    if isinstance(data, dict) and data.get("success") is True:
        return data

    reason = ""
    if isinstance(data, dict):
        reason = str(data.get("reason") or data.get("error") or "").strip()
    if not reason:
        reason = "The native rewarded ad session could not be started on the live AutoYou server."
    if isinstance(result, dict) and _route_missing_404(result, data):
        reason = (
            "The running AutoYou admin server does not expose /api/webrtc/rewarded-ad. "
            "Restart the AutoYou server/admin runtime so it loads the latest native-ad route; "
            "no ad control was sent to the phone."
        )
    if isinstance(result, dict) and result.get("code") == 409:
        reason = reason or "Multiple WebRTC clients are connected; specify the target session or owner."
    if isinstance(result, dict) and result.get("status") == "error" and result.get("code") is None:
        reason = "The live AutoYou admin server is not reachable from the AI agent process."

    response: Dict[str, Any] = {
        "success": False,
        "reason": reason,
        "triggered_count": 0,
    }
    if isinstance(result, dict):
        response["http_status"] = result.get("code")
        response["failure_kind"] = _failure_kind_for_rewarded_ad_result(result, data, reason)
    if isinstance(data, dict):
        for key in ("resolution", "owner_key", "target_session_id"):
            if key in data:
                response[key] = data.get(key)
    return response


async def _send_rewarded_ad_control(
    *,
    session_id: str = "",
    owner_key: str = "",
    allow_single_live_fallback: bool = False,
    control_id: str = "",
    web_rewarded_ad_unit: str = "",
    tool_context: Optional[Any] = None,
) -> Dict[str, Any]:
    token: Optional[str] = None
    if _check_admin_session(tool_context):
        token = _get_session_token(tool_context)
    if not token:
        token = _get_internal_ai_agent_api_token()
    if not token:
        return _rewarded_ad_unavailable_auth_response()

    request_payload: Dict[str, Any] = {
        "payload": _rewarded_ad_control_payload(),
        "allow_single_live_fallback": bool(allow_single_live_fallback),
    }
    cleaned_control_id = str(control_id or "").strip()
    if cleaned_control_id:
        request_payload["payload"]["control_id"] = cleaned_control_id
    normalized_session_id = _normalize_target_value(session_id)
    normalized_owner_key = _normalize_target_value(owner_key)
    if normalized_session_id:
        request_payload["session_id"] = normalized_session_id
    if normalized_owner_key:
        request_payload["owner_key"] = normalized_owner_key

    result = _http(
        "POST",
        "/api/webrtc/rewarded-ad",
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
        fallback_result = await _try_in_process_rewarded_ad(
            reply_target,
            request_payload,
            allow_single_live_fallback=bool(allow_single_live_fallback),
        )
        if fallback_result is not None:
            fallback_result.setdefault("http_status", normalized_result.get("code"))
            fallback_result.setdefault("http_failure_kind", "server_route_missing")
            return fallback_result

    return _normalize_rewarded_ad_result(normalized_result)


async def trigger_agent_website_rewarded_ad(
    *,
    session_id: str = "",
    owner_key: str = "",
    control_id: str = "",
    web_rewarded_ad_unit: str = "",
) -> Dict[str, Any]:
    """Start Ads Watching from the website without exposing account- or mobile-ad unit IDs.

    When the request arrives through a WebRTC browser proxy, the server injects
    the requesting client session headers and this targets that exact client. If
    those headers are absent, the route only falls back when there is one live
    WebRTC client, avoiding a broadcast-style ad trigger.
    """
    normalized_session_id = _normalize_target_value(session_id)
    normalized_owner_key = _normalize_target_value(owner_key)
    normalized_control_id = str(control_id or "").strip()
    if not _native_mobile_ad_trigger_enabled():
        return _rewarded_ad_disabled_response()

    return await _send_rewarded_ad_control(
        session_id=normalized_session_id,
        owner_key=normalized_owner_key,
        allow_single_live_fallback=not bool(normalized_session_id or normalized_owner_key),
        control_id=normalized_control_id,
        web_rewarded_ad_unit="",
    )


async def trigger_client_rewarded_ad(tool_context: Optional[Any] = None) -> Dict[str, Any]:
    """Ask the current WebRTC client to start a native support ad.

    This intentionally does not expose an HTTP trigger and does not broadcast to
    every connected client. The native iOS/Android app presents the configured
    AutoYou AdMob rewarded ad directly. Any user-facing value message belongs to
    the signed-in native app, not this control message.
    """
    if not _native_mobile_ad_trigger_enabled():
        return _rewarded_ad_disabled_response()

    reply_target = _extract_ads_reply_target(tool_context)
    if not reply_target:
        return {
            "success": False,
            "reason": "Rewarded ads must be started from a conversation with a live WebRTC-capable client.",
            "triggered_count": 0,
        }

    allow_single_live_fallback = bool(reply_target.pop("_allow_single_live_fallback", False))
    session_id = str(reply_target.get("session_id") or "").strip()
    owner_key = str(reply_target.get("owner_key") or "").strip()
    return await _send_rewarded_ad_control(
        session_id=session_id,
        owner_key=owner_key,
        allow_single_live_fallback=allow_single_live_fallback,
        tool_context=tool_context,
    )


async def _ads_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    inject_realtime_datetime_into_request(llm_request)
    user_text = _extract_text_from_llm_request(llm_request)
    if not _is_rewarded_ad_trigger_request(user_text):
        return None

    invocation_id = _get_invocation_id(callback_context)
    state = getattr(callback_context, "state", None)
    if _ads_tool_dispatch_already_happened(state, invocation_id):
        return None

    _mark_ads_tool_dispatch(state, invocation_id)
    return create_tool_call_llm_response(
        "trigger_client_rewarded_ad",
        {},
        custom_metadata={
            "response_author": AGENT_NAME,
            "ads_action": "trigger_client_rewarded_ad",
            "ads_deterministic_trigger": True,
        },
    )


def create_ads_watching_agent(model_config: Any) -> Agent:
    """Create the Ads Watching Agent."""
    tools: List[Any] = [
        get_current_datetime,
        get_ads_watching_status,
        describe_ads_monetization_path,
        trigger_client_rewarded_ad,
    ]
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
        before_model_callback=_ads_before_model_callback,
    )
