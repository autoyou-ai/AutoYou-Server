# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-8f60b8eb0ad9b086220d2bf9

"""Expanded Internet-agent controls for models that can sustain tool loops."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import json
import logging
import os
import re
from typing import Any, Dict, List
from urllib.parse import urlsplit

from autoyou_agents.shared_tools.datetime_tool import get_current_datetime
from shared.session_execution import create_text_llm_response, create_tool_call_llm_response

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-8f60b8eb0ad9b086220d2bf9"


logger = logging.getLogger(__name__)


_NETWORK_TOOL_NAMES = frozenset(
    {"internet_search", "scrape_website", "take_screenshot", "navigate_page"}
)
_URL_TOOL_NAMES = frozenset({"scrape_website", "take_screenshot", "navigate_page"})
_GUARD_STATE_KEY = "_autoyou_internet_tool_call_guard"
_USER_REQUEST_STATE_KEY = "_autoyou_internet_user_request"
_REQUEST_MODE_STATE_KEY = "_autoyou_internet_request_mode"
_EVIDENCE_STATE_KEY = "_autoyou_internet_evidence"
_TERMINAL_STATE_KEY = "_autoyou_internet_terminal"
# from __debug_provenance_k__ import donations
_TERMINAL_REASON_STATE_KEY = "_autoyou_internet_terminal_reason"
_DEFAULT_TOOL_CALL_BUDGET = 8
_DEFAULT_DOMAIN_CALL_LIMIT = 2
_CONSECUTIVE_REFUSAL_LIMIT = 3
_TRACKED_SIGNATURE_LIMIT = 40
_SITE_OPERATOR_PATTERN = re.compile(r"\bsite:(\S+)", re.IGNORECASE)
_NO_SEARCH_PATTERN = re.compile(
    r"\b(?:do not|don't|never)\s+(?:use\s+)?(?:a\s+)?(?:search engine|search engines|internet search|search)\b",
    re.IGNORECASE,
)
_PAGE_EVIDENCE_REQUEST_PATTERN = re.compile(
    r"\b(?:open|visit|browse|read|scrape|summari[sz]e|synthesi[sz]e|compare|analy[sz]e|"
    r"brief|roundup|digest|report|headlines?)\b",
    re.IGNORECASE,
)
_MULTI_SOURCE_REQUEST_PATTERN = re.compile(
    r"(?:\b(?:several|multiple|different|independent|two|three|many)\b[^.!?\n]{0,60}"
    r"\b(?:sources?|sites?|websites?|outlets?|domains?)\b|"
    r"\b(?:sources|sites|websites|outlets)\b[^.!?\n]{0,80}[,&])",
    re.IGNORECASE,
)
_NAMED_SOURCE_LIST_PATTERN = re.compile(
    r"\b(?:sites?|websites?|outlets?)\s*(?:-|:)\s*([^.!?\n]+)",
    re.IGNORECASE,
)
_SOURCE_MATCH_STOP_WORDS = frozenset(
    {"a", "an", "and", "latest", "news", "outlet", "site", "the", "website"}
)
_SECONDARY_CONTENT_DOMAINS = frozenset(
    {
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "reddit.com",
        "tiktok.com",
        "wikipedia.org",
        "x.com",
        "youtube.com",
    }
)
_YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")
_NON_PAGE_URL_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff",
    ".mp4", ".webm", ".mov", ".avi", ".mkv", ".mp3", ".wav", ".ogg", ".flac",
    ".zip", ".tar", ".gz", ".rar", ".7z", ".exe", ".dmg", ".msi", ".pkg",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".css", ".js", ".pdf",
)


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


def _invocation_id(context: Any) -> str:
    return str(getattr(context, "invocation_id", "") or "").strip()


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "") or "").strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _tool_signature(tool_name: str, args: Dict[str, Any]) -> str:
    if tool_name == "internet_search":
        # max_results and provider-specific options do not make a search a
        # new retrieval. Compare the normalized query only so a model cannot
        # evade the duplicate guard by changing an irrelevant argument.
        normalized = {"query": " ".join(str((args or {}).get("query") or "").split()).strip().lower()}
    elif tool_name in _URL_TOOL_NAMES:
        normalized = {"url": " ".join(str((args or {}).get("url") or "").split()).strip().lower()}
    else:
        normalized = {
            key: " ".join(value.split()).strip().lower() if isinstance(value, str) else value
            for key, value in sorted((args or {}).items())
        }
    try:
        payload = json.dumps(normalized, sort_keys=True, ensure_ascii=True, default=str)
    except Exception:
        payload = str(normalized)
    return f"{tool_name}:{payload}"


def _normalize_domain(host: str) -> str:
    labels = [part for part in str(host or "").strip().lower().split(".") if part]
    if len(labels) <= 2:
        return ".".join(labels)
    if labels[-2] in {"co", "com", "org", "net", "gov", "edu", "ac", "or", "ne", "go"} and len(labels[-1]) <= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _call_domain(tool_name: str, args: Dict[str, Any]) -> str:
    if tool_name in _URL_TOOL_NAMES:
        try:
            return _normalize_domain(urlsplit(str((args or {}).get("url") or "")).netloc)
        except Exception:
            return ""
    if tool_name == "internet_search":
        match = _SITE_OPERATOR_PATTERN.search(str((args or {}).get("query") or ""))
        if match:
            return _normalize_domain(match.group(1).strip().strip(".,;/\"'"))
    return ""


def _load_guard(state: Any, invocation_id: str) -> Dict[str, Any]:
    guard = _state_get(state, _GUARD_STATE_KEY, None)
    if not isinstance(guard, dict) or str(guard.get("invocation_id") or "") != invocation_id:
        return {"invocation_id": invocation_id, "count": 0, "refusals": 0, "signatures": [], "domains": {}}
    return {
        "invocation_id": invocation_id,
        "count": int(guard.get("count") or 0),
        "refusals": int(guard.get("refusals") or 0),
        "signatures": list(guard.get("signatures") or []),
        "domains": dict(guard.get("domains") or {}),
    }


def _correct_stale_years(query: str, user_request: str) -> str:
    current_year = str((get_current_datetime() or {}).get("date") or "")[:4]
    if not current_year:
        return query
    requested_years = set(_YEAR_PATTERN.findall(str(user_request or "")))

    def replace(match: re.Match[str]) -> str:
        year = match.group(0)
        if year == current_year or year in requested_years or year > current_year:
            return year
        return current_year

    return _YEAR_PATTERN.sub(replace, str(query or ""))


async def before_tool_callback(tool: Any, args: Dict[str, Any], tool_context: Any) -> Any:
    """Stop repeated, runaway, and non-page calls before they reach a browser."""
    tool_name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "").strip()
    if tool_name not in _NETWORK_TOOL_NAMES:
        return None

    invocation_id = _invocation_id(tool_context)
    if not invocation_id:
        return None

    logger.info("Expanded Internet guard inspecting tool=%s invocation=%s", tool_name, invocation_id)

    guard = _load_guard(tool_context.state, invocation_id)
    budget = _positive_int("AUTOYOU_INTERNET_MAX_TOOL_CALLS", _DEFAULT_TOOL_CALL_BUDGET)
    domain_limit = _positive_int("AUTOYOU_INTERNET_MAX_CALLS_PER_DOMAIN", _DEFAULT_DOMAIN_CALL_LIMIT)

    if bool(_state_get(tool_context.state, _TERMINAL_STATE_KEY, False)):
        return {
            "status": "tool_budget_exhausted",
            "message": "The Internet agent has already stopped repeated network calls. Write the final answer from verified evidence.",
        }

    def refuse(status: str, message: str) -> Dict[str, Any]:
        guard["count"] += 1
        guard["refusals"] += 1
        if status in {"skipped_duplicate", "search_disallowed"}:
            _state_set(tool_context.state, _TERMINAL_STATE_KEY, True)
        if status == "search_disallowed":
            _state_set(
                tool_context.state,
                _TERMINAL_REASON_STATE_KEY,
                "The request explicitly forbids search-engine use, so only the directly opened page is shown.",
            )
        if guard["refusals"] >= _CONSECUTIVE_REFUSAL_LIMIT:
            status = "tool_budget_exhausted"
            message = "Several network calls were refused. Stop calling tools and write the final answer from retrieved evidence."
            _state_set(tool_context.state, _TERMINAL_STATE_KEY, True)
        _state_set(tool_context.state, _GUARD_STATE_KEY, guard)
        logger.warning("Expanded Internet guard refused tool=%s status=%s", tool_name, status)
        return {"status": status, "message": message}

    if guard["count"] >= budget:
        _state_set(tool_context.state, _TERMINAL_STATE_KEY, True)
        return {
            "status": "tool_budget_exhausted",
            "message": f"The {budget}-call network budget is exhausted. Stop calling tools and write the final answer from retrieved evidence.",
        }

    user_request = str(_state_get(tool_context.state, _USER_REQUEST_STATE_KEY, "") or "")
    if tool_name == "internet_search" and _NO_SEARCH_PATTERN.search(user_request):
        return refuse(
            "search_disallowed",
            "The request forbids search-engine use. Use the directly opened page or write the final answer.",
        )

    if tool_name == "internet_search" and isinstance(args, dict):
        args["query"] = _correct_stale_years(
            str(args.get("query") or ""),
            str(_state_get(tool_context.state, _USER_REQUEST_STATE_KEY, "") or ""),
        )

    if tool_name in _URL_TOOL_NAMES:
        url = str((args or {}).get("url") or "")
        if urlsplit(url).path.lower().endswith(_NON_PAGE_URL_SUFFIXES):
            return refuse(
                "unsupported_url",
                f"{url} is not a readable web page. Open the article page that contains it instead.",
            )

    signature = _tool_signature(tool_name, args)
    if signature in guard["signatures"]:
        return refuse(
            "skipped_duplicate",
            f"This exact {tool_name} call already ran. Use its result or write the final answer.",
        )

    domain = _call_domain(tool_name, args)
    if domain and int(guard["domains"].get(domain, 0)) >= domain_limit:
        return refuse(
            "domain_call_limit_reached",
            f"The per-request call limit for {domain} is reached. Use another source or write the final answer.",
        )

    guard["count"] += 1
    guard["refusals"] = 0
    guard["signatures"] = (guard["signatures"] + [signature])[-_TRACKED_SIGNATURE_LIMIT:]
    if domain:
        guard["domains"][domain] = int(guard["domains"].get(domain, 0)) + 1
    _state_set(tool_context.state, _GUARD_STATE_KEY, guard)
    logger.info("Expanded Internet guard allowed tool=%s call_count=%s", tool_name, guard["count"])
    return None


def _record_evidence(state: Any, tool_name: str, args: Dict[str, Any], response: Dict[str, Any]) -> None:
    if str(response.get("status") or "").strip().lower() != "success":
        return
    existing = _state_get(state, _EVIDENCE_STATE_KEY, [])
    evidence = list(existing) if isinstance(existing, list) else []
    if tool_name == "internet_search":
        results = response.get("results") if isinstance(response.get("results"), list) else []
        item = {
            "kind": "search",
            "query": str(response.get("query") or args.get("query") or "").strip(),
            "results": [
                {
                    "title": str(result.get("title") or "").strip()[:240],
                    "url": str(result.get("url") or "").strip()[:500],
                    "snippet": str(result.get("snippet") or result.get("description") or "").strip()[:500],
                }
                for result in results[:6]
                if isinstance(result, dict)
            ],
        }
    else:
        raw_headlines = response.get("headlines") if isinstance(response.get("headlines"), list) else []
        item = {
            "kind": "page",
            "url": str(response.get("url") or args.get("url") or "").strip()[:500],
            "title": str(response.get("title") or "").strip()[:240],
            "text": " ".join(str(response.get("text_content") or "").split())[:1200],
            "headlines": [
                {
                    "title": str(headline.get("title") or "").strip()[:240],
                    "url": str(headline.get("url") or "").strip()[:500],
                }
                for headline in raw_headlines[:12]
                if isinstance(headline, dict) and str(headline.get("title") or "").strip()
            ],
        }
    if item not in evidence:
        evidence.append(item)
    _state_set(state, _EVIDENCE_STATE_KEY, evidence[-12:])


def after_tool_callback(tool: Any, args: Dict[str, Any], tool_context: Any, tool_response: Any) -> Any:
    tool_name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "").strip()
    if tool_name not in _NETWORK_TOOL_NAMES or not isinstance(tool_response, dict):
        return None
    status = str(tool_response.get("status") or "").strip().lower()
    if status == "success":
        _record_evidence(tool_context.state, tool_name, args, tool_response)
    elif status == "tool_budget_exhausted":
        _state_set(tool_context.state, _TERMINAL_STATE_KEY, True)
    return None


def _response_text(response: Any) -> str:
    content = getattr(response, "content", None)
    parts = getattr(content, "parts", []) or []
    text_parts: List[str] = []
    for part in parts:
        if getattr(part, "function_call", None) is not None or getattr(part, "function_response", None) is not None:
            return ""
        text = getattr(part, "text", None)
        if isinstance(text, str) and text.strip():
            text_parts.append(text.strip())
    return " ".join(text_parts).strip()


def _cites_evidence(text: str, evidence: Any) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered or not isinstance(evidence, list):
        return False
    for item in evidence:
        if not isinstance(item, dict):
            continue
        urls = [item.get("url")]
        urls.extend(
            result.get("url")
            for result in item.get("results") or []
            if isinstance(result, dict)
        )
        if any(str(url or "").strip().lower().rstrip(".,;:!?)]}") in lowered for url in urls):
            return True
    return False


def _cites_page_evidence(text: str, evidence: Any) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered or not isinstance(evidence, list):
        return False
    for item in evidence:
        if not isinstance(item, dict) or item.get("kind") != "page":
            continue
        page_url = str(item.get("url") or "").strip().lower().rstrip(".,;:!?)]}")
        if page_url and page_url in lowered:
            return True
        for headline in item.get("headlines") or []:
            if not isinstance(headline, dict):
                continue
            headline_url = str(headline.get("url") or "").strip().lower().rstrip(".,;:!?)]}")
            if headline_url and headline_url in lowered:
                return True
    return False


def _page_evidence_domains(evidence: Any) -> set[str]:
    domains: set[str] = set()
    if not isinstance(evidence, list):
        return domains
    for item in evidence:
        if not isinstance(item, dict) or item.get("kind") != "page":
            continue
        try:
            domain = _normalize_domain(urlsplit(str(item.get("url") or "")).netloc)
        except Exception:
            domain = ""
        if domain:
            domains.add(domain)
    return domains


def _candidate_page_urls(evidence: Any) -> List[str]:
    candidates: List[str] = []
    if not isinstance(evidence, list):
        return candidates
    for item in evidence:
        if not isinstance(item, dict) or item.get("kind") != "search":
            continue
        for result in item.get("results") or []:
            if not isinstance(result, dict):
                continue
            url = str(result.get("url") or "").strip()
            try:
                parsed = urlsplit(url)
            except Exception:
                continue
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            if parsed.path.lower().endswith(_NON_PAGE_URL_SUFFIXES):
                continue
            if url not in candidates:
                candidates.append(url)
    return candidates


def _attempted_page_urls(state: Any) -> set[str]:
    guard = _state_get(state, _GUARD_STATE_KEY, {})
    signatures = guard.get("signatures") if isinstance(guard, dict) else []
    attempted: set[str] = set()
    for signature in signatures or []:
        if not str(signature).startswith("scrape_website:"):
            continue
        try:
            payload = json.loads(str(signature).split(":", 1)[1])
        except Exception:
            continue
        url = str(payload.get("url") or "").strip()
        if url:
            attempted.add(url)
    return attempted


def _attempted_search_queries(state: Any) -> set[str]:
    guard = _state_get(state, _GUARD_STATE_KEY, {})
    signatures = guard.get("signatures") if isinstance(guard, dict) else []
    attempted: set[str] = set()
    for signature in signatures or []:
        if not str(signature).startswith("internet_search:"):
            continue
        try:
            payload = json.loads(str(signature).split(":", 1)[1])
        except Exception:
            continue
        query = " ".join(str(payload.get("query") or "").lower().split()).strip()
        if query:
            attempted.add(query)
    return attempted


def _named_sources(user_request: str) -> List[str]:
    match = _NAMED_SOURCE_LIST_PATTERN.search(str(user_request or ""))
    if not match:
        return []
    raw_sources = re.split(r"\s*(?:,|&|\band\b)\s*", match.group(1), flags=re.IGNORECASE)
    sources: List[str] = []
    for raw_source in raw_sources:
        source = re.sub(r"^(?:the\s+)", "", str(raw_source or "").strip(), flags=re.IGNORECASE)
        source = source.strip(" \t\r\n,;:-")
        if 2 <= len(source) <= 60 and source not in sources:
            sources.append(source)
        if len(sources) >= 4:
            break
    return sources


def _source_terms(source: str) -> List[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", str(source or "").lower())
        if token not in _SOURCE_MATCH_STOP_WORDS
    ]


def _source_matches_text(source: str, text: str) -> bool:
    terms = _source_terms(source)
    if not terms:
        return False
    normalized = " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))
    compact = normalized.replace(" ", "")
    return all(term in normalized.split() or term in compact for term in terms)


def _search_item_matches_source(item: Dict[str, Any], source: str) -> bool:
    if _source_matches_text(source, str(item.get("query") or "")):
        return True
    return any(
        isinstance(result, dict)
        and _source_matches_text(
            source,
            f"{result.get('title') or ''} {result.get('url') or ''}",
        )
        for result in item.get("results") or []
    )


def _search_evidence_has_source(evidence: Any, source: str) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("kind") == "search"
        and _search_item_matches_source(item, source)
        for item in (evidence or [])
    )


def _page_evidence_has_source(evidence: Any, source: str) -> bool:
    for item in evidence or []:
        if not isinstance(item, dict) or item.get("kind") != "page":
            continue
        try:
            domain = _normalize_domain(urlsplit(str(item.get("url") or "")).netloc)
        except Exception:
            domain = ""
        if not domain or domain in _SECONDARY_CONTENT_DOMAINS:
            continue
        if _source_matches_text(
            source,
            f"{item.get('title') or ''} {item.get('url') or ''}",
        ):
            return True
    return False


def _next_named_source_search(state: Any, evidence: Any, sources: List[str]) -> str:
    attempted_queries = _attempted_search_queries(state)
    for source in sources:
        if _search_evidence_has_source(evidence, source):
            continue
        query = f"{source} latest headlines"
        if " ".join(query.lower().split()) not in attempted_queries:
            return query
    return ""


def _source_result_score(source: str, result: Dict[str, Any]) -> int:
    title = str(result.get("title") or "")
    url = str(result.get("url") or "")
    try:
        domain = _normalize_domain(urlsplit(url).netloc)
    except Exception:
        domain = ""
    if domain in _SECONDARY_CONTENT_DOMAINS:
        return -100
    score = 0
    if _source_matches_text(source, domain):
        score += 6
    if _source_matches_text(source, title):
        score += 3
    return score


def _next_named_source_page_url(state: Any, evidence: Any, sources: List[str]) -> str:
    attempted = _attempted_page_urls(state)
    for source in sources:
        if _page_evidence_has_source(evidence, source):
            continue
        candidates: List[Dict[str, Any]] = []
        for item in evidence or []:
            if not isinstance(item, dict) or item.get("kind") != "search":
                continue
            if not _search_item_matches_source(item, source):
                continue
            candidates.extend(
                result
                for result in item.get("results") or []
                if isinstance(result, dict)
            )
        ranked = sorted(
            enumerate(candidates),
            key=lambda entry: (-_source_result_score(source, entry[1]), entry[0]),
        )
        for _, result in ranked:
            if _source_result_score(source, result) <= 0:
                continue
            url = str(result.get("url") or "").strip()
            try:
                parsed = urlsplit(url)
            except Exception:
                continue
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            if parsed.path.lower().endswith(_NON_PAGE_URL_SUFFIXES):
                continue
            if url.lower() not in attempted:
                return url
    return ""


def _required_page_domain_count(user_request: str, candidates: List[str]) -> int:
    if not _PAGE_EVIDENCE_REQUEST_PATTERN.search(str(user_request or "")):
        return 0
    candidate_domains = {
        _normalize_domain(urlsplit(url).netloc)
        for url in candidates
        if urlsplit(url).netloc
    }
    if _MULTI_SOURCE_REQUEST_PATTERN.search(str(user_request or "")) and len(candidate_domains) >= 2:
        return 2
    return 1


def _next_required_page_url(state: Any, evidence: Any, user_request: str) -> str:
    candidates = _candidate_page_urls(evidence)
    required_domains = _required_page_domain_count(user_request, candidates)
    page_domains = _page_evidence_domains(evidence)
    if required_domains <= len(page_domains):
        return ""

    attempted = _attempted_page_urls(state)
    for url in candidates:
        domain = _normalize_domain(urlsplit(url).netloc)
        if not domain or domain in page_domains or url.lower() in attempted:
            continue
        return url
    return ""


def _format_evidence(state: Any, reason: str) -> str:
    lines = [
        "Latest visible items verified from the requested pages:",
        str(reason or "The Internet agent could not complete the request.").strip(),
    ]
    evidence = _state_get(state, _EVIDENCE_STATE_KEY, [])
    if isinstance(evidence, list) and evidence:
        page_items = [
            item
            for item in evidence
            if isinstance(item, dict) and item.get("kind") == "page"
        ]
        items_to_render = page_items or evidence
        for item in items_to_render:
            if not isinstance(item, dict):
                continue
            if item.get("kind") == "page":
                title = str(item.get("title") or "Untitled page").strip()
                url = str(item.get("url") or "").strip()
                lines.append(f"\n{title}" + (f" ({url})" if url else ""))
                headlines = item.get("headlines") or []
                if headlines:
                    for headline in headlines[:6]:
                        if not isinstance(headline, dict):
                            continue
                        headline_title = str(headline.get("title") or "").strip()
                        headline_url = str(headline.get("url") or "").strip()
                        entry = f"- {headline_title}"
                        if headline_url:
                            entry += f" ({headline_url})"
                        lines.append(entry)
                else:
                    excerpt = str(item.get("text") or "").strip()
                    if excerpt:
                        lines.append(f"- {excerpt[:600]}")
                continue
            for result in item.get("results") or []:
                if not isinstance(result, dict):
                    continue
                title = str(result.get("title") or "Untitled").strip()
                url = str(result.get("url") or "").strip()
                snippet = str(result.get("snippet") or "").strip()
                entry = f"- {title}"
                if url:
                    entry += f" ({url})"
                if snippet:
                    entry += f": {snippet}"
                lines.append(entry)
    lines.append("\nThis result uses only content retrieved during this run.")
    return "\n".join(lines)


async def after_model_callback(callback_context: Any, llm_response: Any) -> Any:
    evidence = _state_get(callback_context.state, _EVIDENCE_STATE_KEY, [])
    terminal = bool(_state_get(callback_context.state, _TERMINAL_STATE_KEY, False))
    if (not isinstance(evidence, list) or not evidence) and not terminal:
        return None
    user_request = str(_state_get(callback_context.state, _USER_REQUEST_STATE_KEY, "") or "")
    page_evidence_required = bool(
        _PAGE_EVIDENCE_REQUEST_PATTERN.search(user_request)
        and str(_state_get(callback_context.state, _REQUEST_MODE_STATE_KEY, "research") or "research")
        != "search_results"
    )
    if not terminal:
        sources = _named_sources(user_request) if page_evidence_required else []
        required_search_query = _next_named_source_search(
            callback_context.state,
            evidence,
            sources,
        ) if sources else ""
        if required_search_query:
            logger.info(
                "Expanded Internet harness requires named-source discovery before finalizing: %s",
                required_search_query,
            )
            return create_tool_call_llm_response(
                "internet_search",
                {"query": required_search_query},
                custom_metadata={"internet_named_source_required": True},
            )
        required_page_url = (
            _next_named_source_page_url(callback_context.state, evidence, sources)
            if sources
            else _next_required_page_url(
                callback_context.state,
                evidence,
                user_request,
            )
        ) if page_evidence_required else ""
        if required_page_url:
            logger.info(
                "Expanded Internet harness requires page evidence before finalizing: %s",
                required_page_url,
            )
            return create_tool_call_llm_response(
                "scrape_website",
                {"url": required_page_url},
                custom_metadata={"internet_page_evidence_required": True},
            )
    response_text = _response_text(llm_response)
    if terminal:
        reason = str(
            _state_get(callback_context.state, _TERMINAL_REASON_STATE_KEY, "") or ""
        ).strip() or "The model kept requesting repeated network calls, so only verified evidence is shown."
    elif not response_text or response_text.startswith("Live network retrieval stopped before"):
        return None
    else:
        refusal_markers = (
            "cannot browse",
            "can't browse",
            "unable to browse",
            "do not have browsing",
            "don't have browsing",
            "browsing capability",
            "training only includes",
        )
        page_required = page_evidence_required
        page_evidence = any(
            isinstance(item, dict) and item.get("kind") == "page"
            for item in evidence
        )
        snippet_only_markers = (
            "based on the search results",
            "search results provide",
            "search results provided",
            "based on the snippets",
            "snippets retrieved",
            "need to click",
            "recommend visiting",
            "can scrape",
            "scrape the top headlines",
        )
        if any(marker in response_text.lower() for marker in refusal_markers):
            reason = "The model did not finish the live retrieval, so only verified evidence is shown."
        elif page_required and page_evidence and any(
            marker in response_text.lower() for marker in snippet_only_markers
        ):
            reason = "The model ignored retrieved page content, so the verified visible items are shown directly."
        elif page_required and page_evidence and _cites_page_evidence(response_text, evidence):
            return None
        elif not page_required and _cites_evidence(response_text, evidence):
            return None
        else:
            reason = "The model returned text that was not traceable to a source retrieved during this run."
    return create_text_llm_response(
        _format_evidence(callback_context.state, reason),
        custom_metadata={"internet_fallback": True},
    )


__all__ = ["after_model_callback", "after_tool_callback", "before_tool_callback"]
