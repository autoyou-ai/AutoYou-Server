# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Framework-agnostic logic for the Assistant agent.

A wake-word Assistant mode turn arrives as ordinary chat text plus a marker in
session state (``AUTOYOU_ASSISTANT_TURN_STATE_KEY``). The root agent claims the
marker for the matching message and asks :func:`plan_request` what kind of
request it is:

* ``browser`` - open a web page and read its title or text. Simple reads run
  here through the Browser agent's own tool layer (the auto-browser controller,
  normally the Docker desktop); anything more goes to the Browser agent itself.
* ``desktop`` - list or focus windows through the Remote Desktop agent's tools.
* ``code`` - "write a script to automate ..." goes to Pi (or the Coding agent).
* ``answer`` - everything else is answered by the model from the recent chat,
  shared files and, during a video call, the latest camera picture.

Nothing here pretends: an agent that is not installed, a controller that is not
running, or a page without a title is reported as such.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional
from urllib.parse import urlsplit

from shared.adk_state import AUTOYOU_ASSISTANT_TURN_STATE_KEY, normalize_assistant_turn

TURN_MAX_AGE_SECONDS = 300.0
READ_TEXT_LIMIT = 600

ROUTE_BROWSER = "browser"
ROUTE_DESKTOP = "desktop"
ROUTE_CODE = "code"
ROUTE_ANSWER = "answer"

_VOICE_PREFIX_RE = re.compile(r"^\s*\[voice transcript\]\s*", re.IGNORECASE)
# Speech-to-text often spells a domain out ("example dot com").
_SPOKEN_DOT_RE = re.compile(r"(?<=[a-z0-9])\s+dot\s+(?=[a-z0-9])", re.IGNORECASE)
_URL_RE = re.compile(
    r"(?P<url>(?:https?://)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}(?::\d{2,5})?(?:/[^\s,;!?\"')]*)?)",
    re.IGNORECASE,
)
_WEB_VERB_RE = re.compile(
    r"\b(?:open|go\s+to|goto|visit|browse(?:\s+to)?|navigate\s+to|load|pull\s+up|bring\s+up|show\s+me|check|look\s+at|read)\b",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"\b(?:title|heading|headline|called|name\s+of\s+the\s+page)\b", re.IGNORECASE)
_READ_TEXT_RE = re.compile(
    r"\b(?:read\s+(?:me\s+)?(?:the\s+)?(?:page|text|content|it)|what\s+(?:does\s+it|it)\s+says?|summari[sz]e)\b",
    re.IGNORECASE,
)
# Beyond reading: these need the Browser agent's own multi-step loop.
_WEB_ACTION_RE = re.compile(
    r"\b(?:click|press|type|fill|log\s*in|sign\s*in|search\s+for|submit|buy|order|book|download|scroll)\b",
    re.IGNORECASE,
)
_CODE_RE = re.compile(
    r"\b(?:write|make|create|build|generate)\b.{0,40}\b(?:script|program|code|automation|macro)\b"
    r"|\bcode\s+(?:to|that|which)\b|\bautomate\b.{0,60}\b(?:with|using|in)\s+(?:a\s+)?(?:script|code|python|node|powershell|bash)\b",
    re.IGNORECASE,
)
_LIST_WINDOWS_RE = re.compile(
    r"\b(?:what|which|list|show)\b.{0,20}\b(?:windows|apps|applications|programs)\b.{0,20}\b(?:open|running)\b"
    r"|\blist\s+(?:my\s+|the\s+)?windows\b",
    re.IGNORECASE,
)
_FOCUS_WINDOW_RE = re.compile(
    r"\b(?:switch\s+to|focus(?:\s+on)?|bring\s+(?:up|forward)|go\s+to)\s+(?:the\s+|my\s+)?(?P<target>.+?)(?:\s+(?:window|app|application))?\s*[.!?]*$",
    re.IGNORECASE,
)
_DESKTOP_HINT_RE = re.compile(r"\b(?:window|windows|desktop|on\s+my\s+(?:computer|screen|pc|mac))\b", re.IGNORECASE)


@dataclass(frozen=True)
class AssistantPlan:
    route: str
    request: str
    url: str = ""
    read_title: bool = False
    read_text: bool = False
    needs_browser_agent: bool = False
    window: str = ""
    list_windows: bool = False


def clean_request_text(text: Any) -> str:
    """The spoken request without the voice prefix or extra spaces."""
    cleaned = _VOICE_PREFIX_RE.sub("", str(text or ""))
    return " ".join(cleaned.split()).strip()


def _comparable(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean_request_text(text).casefold()).strip()


def claim_turn(
    state: Any,
    user_text: str,
    invocation_id: str = "",
    *,
    now: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Return this message's Assistant mode turn once, or None.

    The marker must be recent and its text must match the message being
    answered, so a typed follow-up never inherits an old voice turn. A claimed
    marker records the invocation, so a second model call in the same turn
    (after a tool) sees it as already handled.
    """
    if state is None:
        return None
    try:
        turn = normalize_assistant_turn(state.get(AUTOYOU_ASSISTANT_TURN_STATE_KEY))
        raw = state.get(AUTOYOU_ASSISTANT_TURN_STATE_KEY)
    except Exception:
        return None
    if not turn or not isinstance(raw, Mapping):
        return None
    if raw.get("claimed"):
        return None
    current = time.time() if now is None else float(now)
    if turn["at"] and current - turn["at"] > TURN_MAX_AGE_SECONDS:
        return None
    if _comparable(turn["text"]) != _comparable(user_text):
        return None
    try:
        state[AUTOYOU_ASSISTANT_TURN_STATE_KEY] = {**dict(raw), "claimed": str(invocation_id or "1")}
    except Exception:
        return None
    return turn


def _normalize_url(raw: str) -> str:
    candidate = raw.strip().rstrip(".,")
    if not re.match(r"^https?://", candidate, re.IGNORECASE):
        candidate = "https://" + candidate
    parts = urlsplit(candidate)
    if not parts.hostname or "." not in parts.hostname:
        return ""
    return candidate


def find_url(text: str) -> str:
    spoken = _SPOKEN_DOT_RE.sub(".", text)
    for match in _URL_RE.finditer(spoken):
        url = _normalize_url(match.group("url"))
        if url:
            return url
    return ""


def plan_request(text: Any) -> AssistantPlan:
    """Decide what a spoken request needs. Pure; no I/O."""
    request = clean_request_text(text)
    url = find_url(request)
    if url and (_WEB_VERB_RE.search(request) or _TITLE_RE.search(request)):
        return AssistantPlan(
            route=ROUTE_BROWSER,
            request=request,
            url=url,
            read_title=bool(_TITLE_RE.search(request)) or not _READ_TEXT_RE.search(request),
            read_text=bool(_READ_TEXT_RE.search(request)),
            needs_browser_agent=bool(_WEB_ACTION_RE.search(request)),
        )
    if _CODE_RE.search(request):
        return AssistantPlan(route=ROUTE_CODE, request=request)
    if _LIST_WINDOWS_RE.search(request):
        return AssistantPlan(route=ROUTE_DESKTOP, request=request, list_windows=True)
    focus = _FOCUS_WINDOW_RE.search(request)
    if focus and not url and (_DESKTOP_HINT_RE.search(request) or re.search(r"\bswitch\s+to\b", request, re.IGNORECASE)):
        target = re.sub(r"\b(?:window|app|application|on\s+my\s+(?:computer|screen|pc|mac))\b", "", focus.group("target"), flags=re.IGNORECASE)
        target = " ".join(target.split()).strip(" .")
        if target:
            return AssistantPlan(route=ROUTE_DESKTOP, request=request, window=target)
    return AssistantPlan(route=ROUTE_ANSWER, request=request)


# ── Browser (via the Browser agent's tool layer) ─────────────────────────────

BROWSER_AGENT_MISSING_REPLY = (
    "Opening web pages needs the Browser agent. Install it under Agents on the Admin Page, "
    "and start its browser desktop."
)


def browser_agent_installed() -> bool:
    """The Assistant agent only drives a browser the owner gave the Browser agent."""
    try:
        from autoyou_agents.shared_tools.agent_install_registry import is_agent_installed

        return bool(is_agent_installed("browser_agent"))
    except Exception:
        return False


def _first_text(container: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(container, Mapping):
        return ""
    for key in keys:
        value = container.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def page_facts(*payloads: Any) -> Dict[str, str]:
    """Title, URL and visible text from controller session/observe payloads."""
    title = url = text = ""
    queue: List[Any] = list(payloads)
    seen = 0
    while queue and seen < 40:
        item = queue.pop(0)
        seen += 1
        if not isinstance(item, Mapping):
            continue
        title = title or _first_text(item, ("title", "page_title", "document_title"))
        url = url or _first_text(item, ("current_url", "url", "page_url"))
        text = text or _first_text(item, ("text", "visible_text", "text_excerpt", "content", "body_text", "ocr_text"))
        for key in ("observation", "session", "page", "result", "data", "ocr", "snapshot"):
            nested = item.get(key)
            if isinstance(nested, Mapping):
                queue.append(nested)
    return {"title": title, "url": url, "text": text}


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or url
    except Exception:
        return url


def _browser_problem(result: Mapping[str, Any]) -> str:
    status = str(result.get("status") or "")
    if status == "controller_unreachable":
        return (
            "the browser desktop isn't running. Start it with "
            "`docker compose -f servers/browser-desktop/docker-compose.yml up -d`"
        )
    if status == "unavailable":
        return "the browser client isn't installed (requirements/internet.txt)"
    if status == "disabled":
        return "browser automation is turned off (AUTO_BROWSER_ENABLED=0)"
    return str(result.get("error") or result.get("message") or "the browser reported an error")[:200]


async def read_web_page(
    url: str,
    *,
    read_text: bool = False,
    tools: Any = None,
) -> Dict[str, Any]:
    """Open ``url`` in a fresh controller session, read it, close the session."""
    if tools is None:
        from autoyou_agents.browser_agent import browser_tool as tools
    target = _normalize_url(url) if url else ""
    if not target:
        return {"status": "error", "reply": "I couldn't tell which web page to open."}
    opened = await tools.browser_open_session(start_url=target, name="assistant")
    if opened.get("status") != "success":
        return {"status": opened.get("status") or "error", "reply": f"I couldn't open {_host(target)}: {_browser_problem(opened)}."}
    session_id = str(opened.get("session_id") or "")
    try:
        observed = await tools.browser_observe(preset="rich" if read_text else "normal", limit=20, session_id=session_id)
        facts = page_facts(observed, opened)
    finally:
        try:
            await tools.browser_close_session(session_id=session_id)
        except Exception:
            pass
    if observed.get("status") != "success" and not facts["title"]:
        return {"status": observed.get("status") or "error", "reply": f"I opened {_host(target)} but couldn't read it: {_browser_problem(observed)}."}
    where = _host(facts["url"] or target)
    title = facts["title"]
    parts: List[str] = []
    if title:
        parts.append(f'The page title of {where} is "{title}".')
    else:
        parts.append(f"I opened {where}, but the page has no title.")
    if read_text and facts["text"]:
        excerpt = facts["text"][:READ_TEXT_LIMIT].rstrip()
        parts.append(f"It says: {excerpt}")
    return {"status": "success", "reply": " ".join(parts), "title": title, "url": facts["url"] or target}


# ── Desktop (via the Remote Desktop agent's tools) ───────────────────────────

def _desktop_tools() -> Any:
    from autoyou_agents.remote_desktop_agent import agent as desktop
    return desktop


def list_windows(*, desktop: Any = None) -> Dict[str, Any]:
    desktop = desktop or _desktop_tools()
    listed = desktop.list_running_windows()
    windows = [str(w.get("title") or "").strip() for w in listed.get("windows") or [] if isinstance(w, Mapping)]
    windows = [w for w in windows if w]
    if listed.get("status") != "success":
        return {"status": "error", "reply": "I couldn't list the windows on this computer."}
    if not windows:
        return {"status": "success", "reply": "I don't see any open windows.", "windows": []}
    shown = ", ".join(windows[:6])
    more = f", and {len(windows) - 6} more" if len(windows) > 6 else ""
    return {"status": "success", "reply": f"Open windows: {shown}{more}.", "windows": windows}


def focus_window(title: str, *, desktop: Any = None) -> Dict[str, Any]:
    desktop = desktop or _desktop_tools()
    wanted = " ".join(str(title or "").casefold().split())
    if not wanted:
        return {"status": "error", "reply": "Which window should I switch to?"}
    listed = desktop.list_running_windows()
    candidates = [w for w in listed.get("windows") or [] if isinstance(w, Mapping) and str(w.get("title") or "").strip()]
    best = None
    for window in candidates:
        name = " ".join(str(window.get("title") or "").casefold().split())
        process = str(window.get("process_name") or "").casefold()
        if name == wanted:
            best = window
            break
        if best is None and (wanted in name or (process and wanted in process)):
            best = window
    if best is None:
        return {"status": "not_found", "reply": f"I couldn't find a window called {title}."}
    if not desktop.activate_window(best.get("id")):
        return {"status": "error", "reply": f"I found {best.get('title')}, but couldn't bring it forward."}
    return {"status": "success", "reply": f"Switched to {best.get('title')}."}


async def run_plan(
    plan: AssistantPlan,
    *,
    browser_tools: Any = None,
    desktop: Any = None,
) -> Optional[Dict[str, Any]]:
    """Carry out a browser read or desktop step; None for other routes."""
    if plan.route == ROUTE_BROWSER and not plan.needs_browser_agent:
        return await read_web_page(plan.url, read_text=plan.read_text, tools=browser_tools)
    if plan.route == ROUTE_DESKTOP:
        if plan.list_windows:
            return list_windows(desktop=desktop)
        return focus_window(plan.window, desktop=desktop)
    return None


__all__ = [
    "AssistantPlan",
    "BROWSER_AGENT_MISSING_REPLY",
    "browser_agent_installed",
    "ROUTE_ANSWER",
    "ROUTE_BROWSER",
    "ROUTE_CODE",
    "ROUTE_DESKTOP",
    "claim_turn",
    "clean_request_text",
    "find_url",
    "focus_window",
    "list_windows",
    "page_facts",
    "plan_request",
    "read_web_page",
    "run_plan",
]
