# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Framework-agnostic logic for the AutoYou Browser Agent.

This module wraps the **auto-browser** controller SDK and exposes a small,
serializable tool surface that the ADK agent (``agent.py``) wraps verbatim.

Architecture note (important):
    The three published packages are *clients* to a controller daemon, not a
    bundled browser:

      * ``auto-browser-client``      -> ``AutoBrowserClient`` REST/HTTP client (+ MCP stdio bridge)
      * ``auto-browser-langchain``   -> ``AutoBrowserTool`` / ``AutoBrowserNode`` adapters
      * ``auto-browser-mcp``         -> metapackage; ``auto-browser-mcp`` console script

    Real browser automation therefore requires the auto-browser **controller**
    running (default ``http://127.0.0.1:8000``). This module never fakes work:
    when the controller is unreachable it returns an actionable
    ``controller_unreachable`` status instead of pretending success.

All tool functions return JSON-serializable dicts with a ``status`` key
(``success`` | ``error`` | ``unavailable`` | ``disabled`` | ``controller_unreachable``),
mirroring the convention used by ``internet_agent``.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

# ── Real SDK import (graceful) ───────────────────────────────────────────────
try:
    from auto_browser_client import AutoBrowserClient  # type: ignore
    from auto_browser_client.client import AutoBrowserError  # type: ignore

    _CLIENT_IMPORT_ERROR: Optional[BaseException] = None
except Exception as exc:  # pragma: no cover - exercised only without the dep
    AutoBrowserClient = None  # type: ignore[assignment]

    class AutoBrowserError(Exception):  # type: ignore[no-redef]
        """Fallback so ``except AutoBrowserError`` is always valid."""

    _CLIENT_IMPORT_ERROR = exc

# LangChain/LangGraph/CrewAI adapters are optional (only needed by callers that
# embed the browser tool into a LangChain/LangGraph graph, not by the ADK path).
try:
    from auto_browser_langchain import AutoBrowserNode, AutoBrowserTool  # type: ignore

    _LANGCHAIN_IMPORT_ERROR: Optional[BaseException] = None
except Exception as exc:  # pragma: no cover - optional integration surface
    AutoBrowserNode = None  # type: ignore[assignment]
    AutoBrowserTool = None  # type: ignore[assignment]
    _LANGCHAIN_IMPORT_ERROR = exc


if AutoBrowserTool is not None and AutoBrowserTool.__init__ is object.__init__:
    def _fallback_init(self, base_url="http://localhost:8000", bearer_token=None, timeout=60.0, **kwargs):
        self.base_url = base_url
        self.bearer_token = bearer_token
        self.timeout = timeout
    AutoBrowserTool.__init__ = _fallback_init



_DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_DEFAULT_TIMEOUT_SECONDS = 60.0
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
_FALSY_ENV_VALUES = {"0", "false", "no", "off"}

# Active-session bookkeeping. Persisted per-conversation in ADK session state
# (preferred) and additionally tracked process-wide as a convenience fallback
# for callers that do not thread a ToolContext.
_BROWSER_SESSION_STATE_KEY = "user:auto_browser_session_id"
_last_session_id: Optional[str] = None


# ── Availability / config ────────────────────────────────────────────────────
def is_auto_browser_available() -> bool:
    """Whether the ``auto-browser-client`` SDK imported successfully."""
    return AutoBrowserClient is not None


def is_langchain_adapter_available() -> bool:
    """Whether the ``auto-browser-langchain`` adapters imported successfully."""
    return AutoBrowserTool is not None


def auto_browser_import_error() -> str:
    return "" if _CLIENT_IMPORT_ERROR is None else str(_CLIENT_IMPORT_ERROR)


def _first_env(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def get_base_url() -> str:
    """Resolve the auto-browser controller base URL (no trailing slash)."""
    value = _first_env("AUTO_BROWSER_BASE_URL", "AUTOYOU_AUTO_BROWSER_BASE_URL")
    return (value or _DEFAULT_BASE_URL).rstrip("/")


def native_viewer_url(session: Any = None) -> Optional[str]:
    """Resolve Auto Browser's human takeover page for the native browser pane."""
    candidates = [
        _first_env("AUTOYOU_AUTO_BROWSER_VIEWER_URL", "AUTO_BROWSER_VNC_URL", "AUTOYOU_AUTO_BROWSER_VNC_URL")
    ]
    if isinstance(session, dict):
        for container in (session, session.get("remote_access")):
            if isinstance(container, dict):
                candidates.extend(container.get(key) for key in ("viewer_url", "novnc_url", "takeover_url", "url"))

    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        try:
            parts = urlsplit(candidate.strip())
            if (parts.scheme == "https" and parts.hostname and not parts.username and not parts.password):
                return candidate.strip()
            if (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
                    and parts.port == 6080 and parts.path == "/vnc.html"
                    and not parts.username and not parts.password):
                return candidate.strip()
        except ValueError:
            continue

    try:
        controller = urlsplit(get_base_url())
        if controller.hostname in {"localhost", "127.0.0.1", "::1"}:
            return "http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale"
    except ValueError:
        pass
    return None


def get_mcp_endpoint() -> str:
    """The HTTP MCP endpoint the stdio bridge proxies to (``<base>/mcp``)."""
    explicit = _first_env("AUTO_BROWSER_MCP_URL", "AUTOYOU_AUTO_BROWSER_MCP_URL")
    if explicit:
        return explicit
    return f"{get_base_url()}/mcp"


def get_bearer_token() -> Optional[str]:
    """Optional bearer token for a controller running with auth enabled."""
    return _first_env("AUTO_BROWSER_BEARER_TOKEN", "AUTOYOU_AUTO_BROWSER_BEARER_TOKEN")


def get_timeout() -> float:
    raw = _first_env("AUTO_BROWSER_HTTP_TIMEOUT_SECONDS", "AUTOYOU_AUTO_BROWSER_TIMEOUT_SECONDS")
    if raw:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return _DEFAULT_TIMEOUT_SECONDS


def get_default_provider() -> Optional[str]:
    """Default LLM provider id the controller uses for autonomous ``agent/run``."""
    return _first_env("AUTO_BROWSER_AGENT_PROVIDER", "AUTOYOU_AUTO_BROWSER_PROVIDER")


def is_browser_agent_enabled() -> bool:
    """Master switch. Defaults on; set ``AUTO_BROWSER_ENABLED=0`` to disable."""
    raw = _first_env("AUTO_BROWSER_ENABLED", "AUTOYOU_AUTO_BROWSER_ENABLED")
    if raw is None:
        return True
    return raw.lower() not in _FALSY_ENV_VALUES


def _controller_guidance() -> str:
    return (
        f"The auto-browser controller is not reachable at {get_base_url()}. "
        "The auto-browser-client / -langchain / -mcp packages are REST clients to a "
        "controller daemon you run separately (they do not bundle a browser). Start the "
        "controller (see https://github.com/LvcidPsyche/auto-browser, e.g. `docker compose up`), "
        "then set AUTO_BROWSER_BASE_URL (and AUTO_BROWSER_BEARER_TOKEN if auth is enabled) and retry."
    )


# ── Result helpers ───────────────────────────────────────────────────────────
def _ok(**fields: Any) -> Dict[str, Any]:
    return {"status": "success", **fields}


def _unavailable() -> Dict[str, Any]:
    return {
        "status": "unavailable",
        "error": (
            "auto-browser SDK is not installed. Install AutoYou's internet component, "
            "e.g. `python -m pip install -r requirements/internet.txt` (provides "
            "auto-browser-client, auto-browser-langchain, auto-browser-mcp)."
        ),
        "import_error": auto_browser_import_error(),
    }


def _disabled() -> Dict[str, Any]:
    return {
        "status": "disabled",
        "enabled": False,
        "message": "Browser automation is disabled (AUTO_BROWSER_ENABLED=0).",
    }


def _error(message: str, **fields: Any) -> Dict[str, Any]:
    return {"status": "error", "error": message, **fields}


def _unreachable(detail: str) -> Dict[str, Any]:
    return {
        "status": "controller_unreachable",
        "reachable": False,
        "base_url": get_base_url(),
        "error": detail,
        "guidance": _controller_guidance(),
    }


def _guard() -> Optional[Dict[str, Any]]:
    """Return a short-circuit result if the agent can't run, else ``None``."""
    if not is_browser_agent_enabled():
        return _disabled()
    if not is_auto_browser_available():
        return _unavailable()
    return None


# ── Session-state bookkeeping ────────────────────────────────────────────────
def _state_get(tool_context: Any, key: str, default: Any = None) -> Any:
    state = getattr(tool_context, "state", None)
    if state is None and isinstance(tool_context, dict):
        state = tool_context
    if state is None:
        return default
    try:
        return state.get(key, default)
    except Exception:
        try:
            return state[key]
        except Exception:
            return default


def _state_set(tool_context: Any, key: str, value: Any) -> None:
    state = getattr(tool_context, "state", None)
    if state is None and isinstance(tool_context, dict):
        state = tool_context
    if state is None:
        return
    try:
        state[key] = value
    except Exception:
        pass


def _set_active_session(tool_context: Any, session_id: Optional[str]) -> None:
    global _last_session_id
    _last_session_id = session_id
    _state_set(tool_context, _BROWSER_SESSION_STATE_KEY, session_id)


def _get_active_session(tool_context: Any, explicit: str = "") -> Optional[str]:
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    stored = _state_get(tool_context, _BROWSER_SESSION_STATE_KEY)
    if stored:
        return str(stored)
    return _last_session_id


def _build_client() -> "AutoBrowserClient":
    return AutoBrowserClient(  # type: ignore[misc]
        base_url=get_base_url(),
        token=get_bearer_token(),
        timeout=get_timeout(),
    )


def _is_connection_error(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError),
    )


def _session_id_from(payload: Any) -> Optional[str]:
    if isinstance(payload, dict):
        for key in ("id", "session_id", "sessionId"):
            value = payload.get(key)
            if value:
                return str(value)
    return None


async def _resolve_or_create_session(
    client: "AutoBrowserClient",
    tool_context: Any,
    explicit: str,
    *,
    start_url: Optional[str] = None,
    auto_create: bool = True,
) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Resolve the active session id, optionally creating a fresh one.

    Returns ``(session_id, created_session_payload | None)``.
    """
    existing = _get_active_session(tool_context, explicit)
    if existing:
        return existing, None
    if not auto_create:
        return None, None
    created = await client.async_create_session(start_url=(start_url or None))
    session_id = _session_id_from(created)
    if session_id:
        _set_active_session(tool_context, session_id)
    return session_id, created


# ── Tools ────────────────────────────────────────────────────────────────────
async def browser_health() -> Dict[str, Any]:
    """Check that the auto-browser controller is installed and reachable.

    Returns controller health plus the resolved base URL. Call this first if any
    other browser action reports the controller is unreachable.
    """
    if not is_browser_agent_enabled():
        return _disabled()
    if not is_auto_browser_available():
        return _unavailable()
    try:
        async with _build_client() as client:
            health = await client.async_health()
        return _ok(
            reachable=True,
            base_url=get_base_url(),
            mcp_endpoint=get_mcp_endpoint(),
            auth_required=bool(get_bearer_token()),
            controller=health,
        )
    except Exception as exc:  # noqa: BLE001 - normalize transport + API errors
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc), base_url=get_base_url())


async def browser_open_session(
    start_url: str = "",
    name: str = "",
    auth_profile: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Open a new browser session on the controller and make it the active session.

    Args:
        start_url: Optional URL to load when the session starts.
        name: Optional friendly session name.
        auth_profile: Optional saved auth-profile name to resume a logged-in session.

    Returns:
        The created session (including its ``session_id``).
    """
    blocked = _guard()
    if blocked:
        return blocked
    try:
        async with _build_client() as client:
            session = await client.async_create_session(
                name=(name or None),
                start_url=(start_url or None),
                auth_profile=(auth_profile or None),
            )
        session_id = _session_id_from(session)
        _set_active_session(tool_context, session_id)
        return _ok(session_id=session_id, active=True, session=session)
    except Exception as exc:  # noqa: BLE001
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc))


async def browser_navigate(
    url: str,
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Navigate the active (or specified) browser session to a URL.

    Creates a session automatically if none is active.
    """
    blocked = _guard()
    if blocked:
        return blocked
    if not (url or "").strip():
        return _error("A URL is required to navigate.")
    try:
        async with _build_client() as client:
            resolved, created = await _resolve_or_create_session(
                client, tool_context, session_id, start_url=url.strip()
            )
            if not resolved:
                return _error("Could not establish a browser session.")
            if created is not None:
                # The session already opened on start_url; no extra navigate needed.
                return _ok(session_id=resolved, url=url.strip(), created_session=True)
            result = await client.async_navigate(resolved, url=url.strip())
        return _ok(session_id=resolved, url=url.strip(), result=result)
    except Exception as exc:  # noqa: BLE001
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc))


async def browser_observe(
    preset: str = "normal",
    limit: int = 40,
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Observe the current page: URL plus interactable elements with ``element_id``s.

    Use the returned ``element_id`` values with ``browser_click`` / ``browser_type``.

    Args:
        preset: "fast" (screenshot only), "normal" (default), or "rich" (extended).
        limit: Max number of elements to return.
    """
    blocked = _guard()
    if blocked:
        return blocked
    resolved = _get_active_session(tool_context, session_id)
    if not resolved:
        return _error("No active browser session. Open one with browser_open_session or browser_navigate first.")
    try:
        async with _build_client() as client:
            observation = await client.async_observe(resolved, preset=preset, limit=limit)
        return _ok(session_id=resolved, observation=observation)
    except Exception as exc:  # noqa: BLE001
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc))


async def browser_click(
    selector: str = "",
    element_id: str = "",
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Click an element by CSS ``selector`` or by ``element_id`` (from browser_observe)."""
    blocked = _guard()
    if blocked:
        return blocked
    if not (selector or "").strip() and not (element_id or "").strip():
        return _error("Provide a CSS selector or an element_id (from browser_observe) to click.")
    resolved = _get_active_session(tool_context, session_id)
    if not resolved:
        return _error("No active browser session. Open one first.")
    try:
        async with _build_client() as client:
            result = await client.async_click(
                resolved,
                selector=(selector or None),
                element_id=(element_id or None),
            )
        return _ok(session_id=resolved, result=result)
    except Exception as exc:  # noqa: BLE001
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc))


async def browser_type(
    text: str,
    selector: str = "",
    element_id: str = "",
    clear_first: bool = True,
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Type ``text`` into a field by CSS ``selector`` or ``element_id``.

    For credentials, prefer the saved auth-profile flow (browser_save_auth_profile)
    over typing secrets through the model.
    """
    blocked = _guard()
    if blocked:
        return blocked
    if not (selector or "").strip() and not (element_id or "").strip():
        return _error("Provide a CSS selector or an element_id (from browser_observe) to type into.")
    resolved = _get_active_session(tool_context, session_id)
    if not resolved:
        return _error("No active browser session. Open one first.")
    try:
        async with _build_client() as client:
            result = await client.async_type_text(
                resolved,
                text,
                selector=(selector or None),
                element_id=(element_id or None),
                clear_first=clear_first,
            )
        return _ok(session_id=resolved, result=result)
    except Exception as exc:  # noqa: BLE001
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc))


async def browser_scroll(
    delta_y: float = 600,
    delta_x: float = 0,
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Scroll the page by ``delta_y`` (and optional ``delta_x``) pixels."""
    blocked = _guard()
    if blocked:
        return blocked
    resolved = _get_active_session(tool_context, session_id)
    if not resolved:
        return _error("No active browser session. Open one first.")
    try:
        async with _build_client() as client:
            result = await client.async_scroll(resolved, delta_x=delta_x, delta_y=delta_y)
        return _ok(session_id=resolved, result=result)
    except Exception as exc:  # noqa: BLE001
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc))


async def browser_screenshot(
    label: str = "manual",
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Capture a screenshot of the current page (returned/stored by the controller)."""
    blocked = _guard()
    if blocked:
        return blocked
    resolved = _get_active_session(tool_context, session_id)
    if not resolved:
        return _error("No active browser session. Open one first.")
    try:
        async with _build_client() as client:
            result = await client.async_screenshot(resolved, label=label)
        return _ok(session_id=resolved, screenshot=result)
    except Exception as exc:  # noqa: BLE001
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc))


async def browser_run_goal(
    goal: str,
    max_steps: int = 6,
    provider: str = "",
    start_url: str = "",
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Hand a natural-language ``goal`` to the controller's own autonomous agent loop.

    The controller plans + executes up to ``max_steps`` browser actions to achieve the
    goal and returns its trace. Creates a session automatically (optionally on
    ``start_url``) if none is active.

    Args:
        goal: What to accomplish, e.g. "find the cheapest direct flight LON->NYC next Friday".
        max_steps: Step budget for the controller's loop.
        provider: LLM provider id the controller should use. Falls back to
            AUTO_BROWSER_AGENT_PROVIDER if omitted.
    """
    blocked = _guard()
    if blocked:
        return blocked
    if not (goal or "").strip():
        return _error("A goal is required.")
    resolved_provider = (provider or "").strip() or (get_default_provider() or "")
    if not resolved_provider:
        return _error(
            "No provider configured for the controller's autonomous agent. "
            "Pass provider=... or set AUTO_BROWSER_AGENT_PROVIDER."
        )
    try:
        async with _build_client() as client:
            resolved, _ = await _resolve_or_create_session(
                client, tool_context, session_id, start_url=(start_url or None)
            )
            if not resolved:
                return _error("Could not establish a browser session.")
            result = await client.async_agent_run(
                resolved,
                provider=resolved_provider,
                goal=goal.strip(),
                max_steps=max_steps,
            )
        run_status = str(result.get("status") or "").strip().lower() if isinstance(result, dict) else ""
        if run_status in {"takeover", "approval_required", "error", "max_steps_reached"}:
            return {
                "status": run_status,
                "message": f"Controller agent run ended with status '{run_status}'.",
                "session_id": resolved,
                "provider": resolved_provider,
                "result": result,
            }
        return _ok(session_id=resolved, provider=resolved_provider, result=result)
    except Exception as exc:  # noqa: BLE001
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc))


async def browser_list_sessions() -> Dict[str, Any]:
    """List open browser sessions on the controller."""
    blocked = _guard()
    if blocked:
        return blocked
    try:
        async with _build_client() as client:
            sessions = await client.async_list_sessions()
        return _ok(sessions=sessions, count=len(sessions or []))
    except Exception as exc:  # noqa: BLE001
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc))


async def browser_close_session(
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Close the active (or specified) browser session and clear it from state."""
    blocked = _guard()
    if blocked:
        return blocked
    resolved = _get_active_session(tool_context, session_id)
    if not resolved:
        return _ok(message="No active browser session to close.", closed=False)
    try:
        async with _build_client() as client:
            result = await client.async_close_session(resolved)
        if _get_active_session(tool_context) == resolved:
            _set_active_session(tool_context, None)
        return _ok(session_id=resolved, closed=True, result=result)
    except Exception as exc:  # noqa: BLE001
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc))


async def browser_list_auth_profiles() -> Dict[str, Any]:
    """List saved auth profiles (named, reusable logged-in browser states)."""
    blocked = _guard()
    if blocked:
        return blocked
    try:
        async with _build_client() as client:
            profiles = await client.async_list_auth_profiles()
        return _ok(auth_profiles=profiles, count=len(profiles or []))
    except Exception as exc:  # noqa: BLE001
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc))


async def browser_save_auth_profile(
    profile_name: str,
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Save the active session's logged-in state as a reusable named auth profile.

    Workflow: open a session, let the human log in (browser_request handoff), then
    save the profile here. Future sessions can resume it via
    ``browser_open_session(auth_profile=...)``.
    """
    blocked = _guard()
    if blocked:
        return blocked
    if not (profile_name or "").strip():
        return _error("A profile_name is required.")
    resolved = _get_active_session(tool_context, session_id)
    if not resolved:
        return _error("No active browser session. Open one and sign in first.")
    try:
        async with _build_client() as client:
            result = await client.async_save_auth_profile(resolved, profile_name.strip())
        return _ok(session_id=resolved, profile_name=profile_name.strip(), result=result)
    except Exception as exc:  # noqa: BLE001
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc))


# ── Integration surfaces for other systems (LangChain / LangGraph / MCP) ──────
def build_langchain_browser_tool(
    *,
    base_url: Optional[str] = None,
    bearer_token: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Any:
    """Return a configured ``AutoBrowserTool`` (LangChain ``BaseTool``) or ``None``.

    For embedding AutoYou's browser controller into LangChain/CrewAI agents. The
    tool proxies the controller's MCP tool surface (``browser.navigate`` etc.).
    """
    if AutoBrowserTool is None:
        logger.warning("auto-browser-langchain not available: %s", _LANGCHAIN_IMPORT_ERROR)
        return None
    return AutoBrowserTool(
        base_url=base_url or get_base_url(),
        bearer_token=bearer_token if bearer_token is not None else get_bearer_token(),
        timeout=timeout if timeout is not None else get_timeout(),
    )


def build_langchain_browser_node(
    *,
    base_url: Optional[str] = None,
    bearer_token: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Any:
    """Return a configured ``AutoBrowserNode`` for LangGraph state graphs, or ``None``."""
    if AutoBrowserNode is None:
        logger.warning("auto-browser-langchain not available: %s", _LANGCHAIN_IMPORT_ERROR)
        return None
    return AutoBrowserNode(
        base_url=base_url or get_base_url(),
        bearer_token=bearer_token if bearer_token is not None else get_bearer_token(),
        timeout=timeout if timeout is not None else get_timeout(),
    )


def get_mcp_bridge_command() -> Dict[str, Any]:
    """Registration descriptor for the ``auto-browser-mcp`` stdio bridge.

    Lets AutoYou expose the controller to any stdio MCP client (Claude Desktop,
    Cursor, the AutoYou MCP layer). The bridge proxies stdio JSON-RPC to the
    controller's HTTP MCP endpoint.
    """
    env: Dict[str, str] = {"AUTO_BROWSER_BASE_URL": get_mcp_endpoint()}
    token = get_bearer_token()
    if token:
        env["AUTO_BROWSER_BEARER_TOKEN"] = token
    return {
        "command": "auto-browser-mcp",
        "args": [],
        # Fallback that works even if the console script is not on PATH:
        "module_command": [sys.executable, "-m", "auto_browser_client.mcp_bridge"],
        "env": env,
    }


def list_controller_mcp_tools() -> Dict[str, Any]:
    """Enumerate the controller's exposed MCP tools (discovery/health helper)."""
    blocked = _guard()
    if blocked:
        return blocked
    if AutoBrowserTool is None:
        return _error("auto-browser-langchain not installed.", import_error=str(_LANGCHAIN_IMPORT_ERROR))
    try:
        tools = AutoBrowserTool.list_tools(base_url=get_base_url(), bearer_token=get_bearer_token())
        return _ok(tools=tools, count=len(tools or []))
    except Exception as exc:  # noqa: BLE001
        if _is_connection_error(exc):
            return _unreachable(str(exc))
        return _error(str(exc))
