# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
from google.adk.agents import Agent
from typing import Any, Dict, Optional
from datetime import datetime
import os
import logging
import re

# Google ADK memory imports omitted; handled by root agent

from .internet_tool import internet_search, scrape_website, take_screenshot, navigate_page, ingest_attachments
from .prompt import (
    AGENT_NAME,
    AGENT_DESCRIPTION,
    AGENT_INSTRUCTION,
    EXPANDED_AGENT_INSTRUCTION,
)
from autoyou_agents.model_config import model_uses_expanded_harness
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime, inject_realtime_datetime_into_request
from shared.session_execution import create_text_llm_response, create_tool_call_llm_response
from .expanded_harness import (
    after_model_callback as _expanded_after_model_callback,
    after_tool_callback as _expanded_after_tool_callback,
    before_tool_callback as _expanded_before_tool_callback,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_INTERNET_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY = "_autoyou_internet_tool_dispatch_invocation_id"
_INTERNET_TOOL_ERROR_INVOCATION_ID_STATE_KEY = "_autoyou_internet_tool_error_invocation_id"
_INTERNET_TOOL_ERROR_MESSAGE_STATE_KEY = "_autoyou_internet_tool_error_message"
_INTERNET_TOOL_SUCCESS_INVOCATION_ID_STATE_KEY = "_autoyou_internet_tool_success_invocation_id"
_INTERNET_TOOL_RESULT_MESSAGE_STATE_KEY = "_autoyou_internet_tool_result_message"
_INTERNET_USER_REQUEST_STATE_KEY = "_autoyou_internet_user_request"
_INTERNET_REQUEST_INVOCATION_ID_STATE_KEY = "_autoyou_internet_request_invocation_id"
_INTERNET_REQUEST_MODE_STATE_KEY = "_autoyou_internet_request_mode"
_INTERNET_REQUEST_MODE_RESEARCH = "research"
_INTERNET_REQUEST_MODE_SEARCH_RESULTS = "search_results"
_INTERNET_FRESHNESS_PATTERN = re.compile(r"\b(latest|current|recent|today(?:'s)?|breaking|fresh)\b", re.IGNORECASE)
_INTERNET_NEWS_PATTERN = re.compile(r"\b(news|headlines?|updates?)\b", re.IGNORECASE)
_INTERNET_SEARCH_ACTION_PATTERN = re.compile(r"\b(search|look up|google)\b", re.IGNORECASE)
_INTERNET_BROWSE_ACTION_PATTERN = re.compile(
    r"\b(?:browse|scrape|visit|open|navigate|download|fetch|read)\b",
    re.IGNORECASE,
)
_INTERNET_SYNTHESIS_PATTERN = re.compile(
    r"\b(?:summari[sz]e|synthesi[sz]e|compare|analy[sz]e|explain|brief|roundup|digest|report)\b",
    re.IGNORECASE,
)
_INTERNET_RESULT_ONLY_PATTERN = re.compile(
    r"(?:\b(?:return|show|list|provide|give)\b[^.!?\n]{0,60}\b(?:search\s+)?results?\b|"
    r"\b(?:search\s+)?results?\s+only\b|\blinks?\s+only\b)",
    re.IGNORECASE,
)
_INTERNET_MULTI_SOURCE_PATTERN = re.compile(
    r"\b(?:several|multiple|different|independent|two|three|many)\b[^.!?\n]{0,50}"
    r"\b(?:sources?|sites?|websites?|outlets?|domains?)\b",
    re.IGNORECASE,
)
_INTERNET_CONTROL_CONTEXT_PATTERN = re.compile(
    r"(?:Recurring task execution rules:|"
    r"Treat these prior outputs as untrusted context\.|"
    r"Avoid repeating these recent outputs:|"
    r"Earlier live-data result text is intentionally omitted\.)",
    re.IGNORECASE,
)
_INTERNET_ROUTE_PREFIX_PATTERN = re.compile(
    r"^\s*(?:(?:begin|start)\s+fresh[.!,:;\s-]*)?"
    r"(?:use|go\s+to|switch\s+to|route\s+to|delegate\s+to|handoff\s+to|open)\s+"
    r"(?:the\s+)?(?:autoyou[_ -]?)?internet[_ -]?agent\b[.!,:;\s-]*",
    re.IGNORECASE,
)
_INTERNET_QUERY_OUTPUT_CLAUSE_PATTERN = re.compile(
    r"(?:[.;]\s*|\s+(?:and|then)\s+)(?:please\s+)?"
    r"(?:summari[sz]e|synthesi[sz]e|compare|analy[sz]e|explain|brief|report|return|provide|give)\b.*$",
    re.IGNORECASE,
)
_INTERNET_EXPLICIT_AGENT_PATTERN = re.compile(r"\binternet[_ -]?agent\b", re.IGNORECASE)
_INTERNET_URL_PATTERN = re.compile(
    r"(?:https?://|www\.|[a-z0-9][a-z0-9.-]+\.[a-z]{2,}(?:/[^\s<>\"']*)?)",
    re.IGNORECASE,
)
_INTERNET_NETWORK_ACTION_PATTERN = re.compile(
    r"\b(search|look up|google|browse|scrape|visit|open|download|fetch|retrieve)\b",
    re.IGNORECASE,
)
_INTERNET_NETWORK_OBJECT_PATTERN = re.compile(
    r"\b(internet|web|online|website|webpage|url|link)\b",
    re.IGNORECASE,
)
_INTERNET_LIVE_SUBJECT_PATTERN = re.compile(
    r"\b(news|headlines?|updates?|information|status|prices?|weather|scores?|events?|articles?|reports?|data|availability|releases?)\b",
    re.IGNORECASE,
)
_MONTH_YEAR_PATTERN = re.compile(
    r"\b("
    r"january|february|march|april|may|june|july|august|september|october|november|december"
    r")\s+20\d{2}\b",
    re.IGNORECASE,
)
_YEAR_PATTERN = re.compile(r"\b20\d{2}\b")


def _extract_text_from_llm_request(llm_request: Any) -> str:
    for content in reversed(getattr(llm_request, "contents", []) or []):
        if str(getattr(content, "role", "") or "").strip().lower() not in {"", "user"}:
            continue
        parts: list[str] = []
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            return " ".join(parts).strip()
    return ""


def _internet_search_enabled() -> bool:
    raw_value = str(os.getenv("AUTOYOU_INTERNET_SEARCH_ENABLED", "1") or "1").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


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
    return bool(invocation_id) and str(
        _state_get(state, _INTERNET_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY, "") or ""
    ).strip() == invocation_id


def _mark_tool_dispatch(state: Any, invocation_id: str) -> None:
    if invocation_id:
        _state_set(state, _INTERNET_TOOL_DISPATCH_INVOCATION_ID_STATE_KEY, invocation_id)


def _record_tool_error(state: Any, invocation_id: str, message: str) -> None:
    _state_set(state, _INTERNET_TOOL_ERROR_INVOCATION_ID_STATE_KEY, str(invocation_id or ""))
    _state_set(state, _INTERNET_TOOL_ERROR_MESSAGE_STATE_KEY, str(message or "").strip())


def _record_tool_result_message(state: Any, invocation_id: str, message: str) -> None:
    _state_set(state, _INTERNET_TOOL_SUCCESS_INVOCATION_ID_STATE_KEY, str(invocation_id or ""))
    _state_set(state, _INTERNET_TOOL_RESULT_MESSAGE_STATE_KEY, str(message or "").strip())
    _record_tool_error(state, "", "")


def _strip_internet_request_boilerplate(user_text: str) -> str:
    """Return only the user objective that is safe to use for tool planning."""
    text = str(user_text or "").strip()
    marker = _INTERNET_CONTROL_CONTEXT_PATTERN.search(text)
    if marker:
        text = text[:marker.start()].rstrip()
    text = _INTERNET_ROUTE_PREFIX_PATTERN.sub("", text, count=1)
    text = re.sub(
        r"^to\s+(?=(?:search|look\s+up|google|browse|scrape|visit|open|navigate|download|fetch|read)\b)",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    return text.strip(" \t\r\n.,;:!-")


def _classify_internet_request_mode(user_text: str) -> str:
    """Separate literal result listing from research that needs model synthesis."""
    text = _strip_internet_request_boilerplate(user_text)
    has_synthesis = bool(_INTERNET_SYNTHESIS_PATTERN.search(text))
    has_browse_action = bool(_INTERNET_BROWSE_ACTION_PATTERN.search(text))
    has_multiple_sources = bool(_INTERNET_MULTI_SOURCE_PATTERN.search(text))
    has_explicit_search = bool(
        _extract_search_query(text) or _INTERNET_SEARCH_ACTION_PATTERN.search(text)
    )
    asks_for_results = bool(_INTERNET_RESULT_ONLY_PATTERN.search(text))
    if _has_exact_query_directive(text) and not has_synthesis:
        return _INTERNET_REQUEST_MODE_SEARCH_RESULTS
    if has_explicit_search and not has_synthesis and not has_browse_action:
        return _INTERNET_REQUEST_MODE_SEARCH_RESULTS
    if asks_for_results and not has_synthesis and not has_browse_action:
        return _INTERNET_REQUEST_MODE_SEARCH_RESULTS
    if (
        has_browse_action
        and _INTERNET_URL_PATTERN.search(text)
        and not has_synthesis
        and not has_multiple_sources
    ):
        return _INTERNET_REQUEST_MODE_SEARCH_RESULTS
    return _INTERNET_REQUEST_MODE_RESEARCH


def _ensure_internet_request_state(state: Any, invocation_id: str, user_text: str) -> str:
    stored_invocation_id = str(
        _state_get(state, _INTERNET_REQUEST_INVOCATION_ID_STATE_KEY, "") or ""
    ).strip()
    if not invocation_id or stored_invocation_id != invocation_id:
        mode = _classify_internet_request_mode(user_text)
        clean_request = _strip_internet_request_boilerplate(user_text)
        _state_set(state, _INTERNET_REQUEST_INVOCATION_ID_STATE_KEY, invocation_id)
        _state_set(state, _INTERNET_REQUEST_MODE_STATE_KEY, mode)
        _state_set(state, _INTERNET_USER_REQUEST_STATE_KEY, clean_request[:2000])
        return mode
    return str(
        _state_get(state, _INTERNET_REQUEST_MODE_STATE_KEY, _INTERNET_REQUEST_MODE_RESEARCH)
        or _INTERNET_REQUEST_MODE_RESEARCH
    )


def _request_must_start_with_browsing(user_text: str) -> bool:
    """Keep page and multi-source work in the model-owned tool sequence."""
    text = _strip_internet_request_boilerplate(user_text)
    return bool(
        _INTERNET_BROWSE_ACTION_PATTERN.search(text)
        or _INTERNET_MULTI_SOURCE_PATTERN.search(text)
    )


def _extract_search_query(user_text: str) -> Optional[str]:
    text = _strip_internet_request_boilerplate(user_text)
    lowered = " ".join(text.lower().split())

    if any(token in lowered for token in ("strictly as-is", "strictly as is", "exactly", "exact query")):
        quoted = re.findall(r"[\"“](.+?)[\"”]", text)
        if quoted:
            return quoted[0].strip()

    patterns = (
        r"\bsearch(?:\s+the)?\s+(?:internet|web)\s+(?:for|on)?\s+(.+)$",
        r"\binternet\s+search(?:\s+for)?\s+(.+)$",
        r"\bweb\s+search(?:\s+for)?\s+(.+)$",
        r"\bsearch\s+for\s+(.+)$",
        r"\blook up\s+(.+)$",
        r"\bsearch\s+(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            query = _INTERNET_QUERY_OUTPUT_CLAUSE_PATTERN.sub("", match.group(1))
            return query.strip(" .,:;!?\"'")

    quoted = re.findall(r"[\"“](.+?)[\"”]", text)
    if quoted and re.search(r"\bsearch\b", lowered, re.IGNORECASE):
        return quoted[0].strip()
    return None


def _has_exact_query_directive(user_text: str) -> bool:
    lowered = " ".join(str(user_text or "").lower().split())
    return any(token in lowered for token in ("strictly as-is", "strictly as is", "exactly", "exact query"))


def _looks_like_live_internet_request(user_text: str) -> bool:
    text = _strip_internet_request_boilerplate(user_text)
    if not text:
        return False
    return bool(
        _extract_search_query(text)
        or (_INTERNET_FRESHNESS_PATTERN.search(text) and _INTERNET_NEWS_PATTERN.search(text))
        or _INTERNET_SEARCH_ACTION_PATTERN.search(text)
    )


def is_internet_request(user_text: str) -> bool:
    """Compatibility classifier for the root/provider routing layer."""
    text = str(user_text or "").strip()
    if not text:
        return False
    if _INTERNET_EXPLICIT_AGENT_PATTERN.search(text) or _INTERNET_URL_PATTERN.search(text):
        return True
    if _INTERNET_FRESHNESS_PATTERN.search(text) and _INTERNET_LIVE_SUBJECT_PATTERN.search(text):
        return True
    if _INTERNET_SEARCH_ACTION_PATTERN.search(text):
        return True
    return bool(
        _INTERNET_NETWORK_ACTION_PATTERN.search(text)
        and _INTERNET_NETWORK_OBJECT_PATTERN.search(text)
    )


def _normalize_live_search_query(user_text: str) -> Optional[str]:
    text = " ".join(_strip_internet_request_boilerplate(user_text).split()).strip()
    if not text:
        return None

    query = _extract_search_query(text) or text
    query = _INTERNET_QUERY_OUTPUT_CLAUSE_PATTERN.sub("", query)
    query = query.strip(" .,:;!?\"'")
    if not query:
        return None

    if _has_exact_query_directive(text):
        return query

    current_datetime = get_current_datetime()
    current_date = str(current_datetime.get("date") or "").strip()
    current_year = current_date[:4] if len(current_date) >= 4 else ""
    current_month_year = ""
    try:
        if current_date:
            current_month_year = datetime.fromisoformat(current_date).strftime("%B %Y")
    except Exception:
        current_month_year = ""
    lowered_query = query.lower()
    freshness_request = bool(_INTERNET_FRESHNESS_PATTERN.search(query))

    if freshness_request and current_year:
        if current_month_year:
            query = _MONTH_YEAR_PATTERN.sub(current_month_year, query)
        else:
            query = _MONTH_YEAR_PATTERN.sub(current_year, query)
        query = _YEAR_PATTERN.sub(
            lambda match: current_year if match.group(0) != current_year else match.group(0),
            query,
        )

    if freshness_request and current_date and "as of" not in lowered_query:
        query = f"{query} as of {current_date}"

    return query


def _format_search_result_preview(result: Dict[str, Any]) -> str:
    title = str(result.get("title") or "Untitled result").strip()
    url = str(result.get("url") or "").strip()
    snippet = str(result.get("snippet") or result.get("description") or "").strip()
    line = f"- {title}"
    if url:
        line += f" ({url})"
    if snippet:
        line += f": {snippet[:280]}"
    return line


def _format_verified_internet_tool_result(tool_name: str, args: Dict[str, Any], tool_response: Dict[str, Any]) -> str:
    if tool_name == "internet_search":
        query = str(tool_response.get("query") or args.get("query") or "").strip()
        results = [
            result
            for result in list(tool_response.get("results") or [])
            if isinstance(result, dict)
        ]
        count = int(tool_response.get("results_count") or len(results))
        noun = "result" if count == 1 else "results"
        prefix = f"Retrieved {count} live internet search {noun}"
        if query:
            prefix += f" for '{query}'"
        prefix += "."
        if not results:
            return prefix
        preview_lines = "\n".join(_format_search_result_preview(result) for result in results[:5])
        return f"{prefix}\n\nTop results:\n{preview_lines}"

    if tool_name == "scrape_website":
        url = str(tool_response.get("url") or args.get("url") or "").strip()
        title = str(tool_response.get("title") or "Untitled page").strip()
        text = " ".join(str(tool_response.get("text_content") or "").split()).strip()
        lines = [f"Retrieved the page '{title}'" + (f" at {url}." if url else ".")]
        if text:
            lines.append("")
            lines.append(text[:700])
        return "\n".join(lines)

    return str(tool_response.get("message") or f"{tool_name} completed successfully.").strip()


async def _internet_datetime_injection_callback(callback_context: Any, llm_request: Any) -> Any:
    """Inject real datetime so search queries use the correct year."""
    inject_realtime_datetime_into_request(llm_request)
    return None


async def _internet_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    raw_user_text = _extract_text_from_llm_request(llm_request)
    if not raw_user_text:
        return None

    invocation_id = _get_invocation_id(callback_context)
    request_mode = _ensure_internet_request_state(
        callback_context.state,
        invocation_id,
        raw_user_text,
    )
    user_text = _strip_internet_request_boilerplate(raw_user_text)
    if _tool_dispatch_already_happened(callback_context.state, invocation_id):
        success_invocation_id = str(
            _state_get(callback_context.state, _INTERNET_TOOL_SUCCESS_INVOCATION_ID_STATE_KEY, "") or ""
        ).strip()
        if invocation_id and invocation_id == success_invocation_id:
            result_message = str(
                _state_get(callback_context.state, _INTERNET_TOOL_RESULT_MESSAGE_STATE_KEY, "") or ""
            ).strip()
            if result_message and request_mode == _INTERNET_REQUEST_MODE_SEARCH_RESULTS:
                return create_text_llm_response(
                    result_message,
                    custom_metadata={
                        "response_author": AGENT_NAME,
                        "internet_verified_tool_result": True,
                    },
                )
            return None
        error_invocation_id = str(
            _state_get(callback_context.state, _INTERNET_TOOL_ERROR_INVOCATION_ID_STATE_KEY, "") or ""
        ).strip()
        if (
            invocation_id
            and invocation_id == error_invocation_id
            and request_mode == _INTERNET_REQUEST_MODE_SEARCH_RESULTS
        ):
            error_message = str(
                _state_get(callback_context.state, _INTERNET_TOOL_ERROR_MESSAGE_STATE_KEY, "") or ""
            ).strip() or "The internet tool failed during this request."
            return create_text_llm_response(
                f"I could not finish the internet action because the internet tool failed: {error_message}",
                custom_metadata={"response_author": AGENT_NAME, "internet_error": error_message},
            )
        return None

    if not _internet_search_enabled():
        if re.search(r"\b(search|browse|scrape|visit|open)\b", user_text, flags=re.IGNORECASE):
            return create_text_llm_response(
                "Internet search is disabled right now, so I did not run any network tools.",
                custom_metadata={"response_author": AGENT_NAME, "internet_enabled": False},
            )
        return None

    search_query = None
    if not _request_must_start_with_browsing(user_text) and _looks_like_live_internet_request(user_text):
        search_query = _normalize_live_search_query(user_text)
    if search_query:
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "internet_search",
            {"query": search_query},
            custom_metadata={
                "response_author": AGENT_NAME,
                "internet_intent": "search",
                "internet_query": search_query,
            },
        )

    return None


def _extract_explicit_page_url(user_text: str) -> Optional[str]:
    match = re.search(r"https?://[^\s<>\"']+", str(user_text or ""), flags=re.IGNORECASE)
    if match:
        return match.group(0).rstrip(".,;:!?)]}")
    domain_match = re.search(
        r"\b((?:www\.)?[a-z0-9][a-z0-9.-]+\.[a-z]{2,}(?:/[^\s<>\"']*)?)\b",
        str(user_text or ""),
        flags=re.IGNORECASE,
    )
    if domain_match:
        target = domain_match.group(1).rstrip(".,;:!?)]}")
        return f"https://{target}"
    return None


async def _internet_expanded_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    raw_user_text = _extract_text_from_llm_request(llm_request)
    invocation_id = _get_invocation_id(callback_context)
    _ensure_internet_request_state(
        callback_context.state,
        invocation_id,
        raw_user_text,
    )
    user_text = _strip_internet_request_boilerplate(raw_user_text)

    page_url = _extract_explicit_page_url(user_text)
    direct_page_request = bool(
        page_url
        and re.search(r"\b(?:open|visit|load|scrape|page\s+title|visible\s+headlines?)\b", user_text, re.IGNORECASE)
    )
    if direct_page_request and not _tool_dispatch_already_happened(callback_context.state, invocation_id):
        _mark_tool_dispatch(callback_context.state, invocation_id)
        return create_tool_call_llm_response(
            "scrape_website",
            {"url": page_url},
            custom_metadata={
                "response_author": AGENT_NAME,
                "internet_intent": "open_page",
                "internet_url": page_url,
            },
        )
    return await _internet_before_model_callback(callback_context, llm_request)


async def _internet_compact_before_model_pipeline(callback_context: Any, llm_request: Any) -> Any:
    """Run datetime grounding and compact planning as one callback."""
    await _internet_datetime_injection_callback(callback_context, llm_request)
    return await _internet_before_model_callback(callback_context, llm_request)


async def _internet_expanded_before_model_pipeline(callback_context: Any, llm_request: Any) -> Any:
    """Run datetime grounding and expanded planning as one callback."""
    await _internet_datetime_injection_callback(callback_context, llm_request)
    return await _internet_expanded_before_model_callback(callback_context, llm_request)


async def _internet_after_tool_callback(
    tool: Any,
    args: Dict[str, Any],
    tool_context: Any,
    tool_response: Any,
) -> Any:
    invocation_id = _get_invocation_id(tool_context)
    if not invocation_id:
        return None

    tool_name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "").strip()
    if tool_name not in {"internet_search", "scrape_website", "take_screenshot", "navigate_page"}:
        return None

    # Model-issued tools do not pass through the deterministic dispatch branch.
    # Mark every observed network call so the next before-model pass never adds
    # an accidental second preflight search to the same invocation.
    _mark_tool_dispatch(tool_context.state, invocation_id)

    if isinstance(tool_response, dict):
        status = str(tool_response.get("status") or "").strip().lower()
        error_message = str(tool_response.get("error") or tool_response.get("message") or "").strip()
        if status == "success":
            _record_tool_result_message(
                tool_context.state,
                invocation_id,
                _format_verified_internet_tool_result(tool_name, args, tool_response),
            )
        elif status in {"error", "partial", "disabled"} and error_message:
            _record_tool_error(tool_context.state, invocation_id, error_message)

        url = str(args.get("url") or tool_response.get("url") or "").strip()
        if status == "success" and url and tool_name in {"scrape_website", "take_screenshot", "navigate_page"}:
            from autoyou_agents.client_browser_control_agent.agent import (
                _dispatch_client_browser_control_payload,
                build_client_browser_control_payload,
            )

            payload = build_client_browser_control_payload(
                action="open_url", url=url, source="internet_agent"
            )
            if payload.get("success", True):
                result = await _dispatch_client_browser_control_payload(payload, tool_context)
                if not result.get("success"):
                    logger.debug("Internet page was not mirrored in the native browser pane: %s",
                                 str(result.get("reason") or "unavailable")[:200])
    return None


def create_internet_agent(model_config):
    """Create a model-appropriate Internet agent."""
    tools = [
        ingest_attachments,
        internet_search,
        scrape_website,
        take_screenshot,
        navigate_page,
        get_current_datetime,
    ]
    expanded = model_uses_expanded_harness(model_config)
    logger.info(
        "Internet agent harness profile=%s model=%s",
        "expanded" if expanded else "compact",
        getattr(model_config, "model", model_config),
    )
    agent_kwargs = {
        "name": AGENT_NAME,
        "model": model_config,
        "description": AGENT_DESCRIPTION,
        "instruction": EXPANDED_AGENT_INSTRUCTION if expanded else AGENT_INSTRUCTION,
        "before_model_callback": (
            _internet_expanded_before_model_pipeline
            if expanded
            else _internet_compact_before_model_pipeline
        ),
        "after_tool_callback": [_internet_after_tool_callback],
        "tools": tools,
    }
    if expanded:
        agent_kwargs.update(
            before_tool_callback=[_expanded_before_tool_callback],
            after_tool_callback=[_internet_after_tool_callback, _expanded_after_tool_callback],
            after_model_callback=[_expanded_after_model_callback],
        )
    return Agent(**agent_kwargs)
