# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-a2d164a991f3e8a95320d87e

"""
Agent Builder sub-agent.

Provides tools to scaffold new AutoYou agent drafts, mark them installed when
allowed, and refresh the current app without a full process restart.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import importlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from google.adk.agents import Agent
from google.adk.tools import ToolContext
from google.adk.tools.transfer_to_agent_tool import transfer_to_agent as _transfer_to_agent

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from autoyou_agents.shared_tools.agent_web_proxy import register_agent_web_port
from autoyou_agents.shared_tools.agent_install_registry import (
    is_builtin_agent_name,
    discover_agent_directories,
    runtime_install_block_reason,
    set_agent_installed,
)
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime
from autoyou_agents.shared_tools.coding_handoff import (
    CODING_HANDOFF_STATE_KEY,
    build_coding_handoff_payload,
)
from autoyou_agents.shared_tools.builder_suite import (
    DEFAULT_BUILDER_SUITE_OLLAMA_MODEL,
    builder_suite_status,
    install_builder_suite_agents,
)
from autoyou_agents.shared_tools.website_handoff import (
    WEBSITE_HANDOFF_STATE_KEY,
    build_website_handoff_payload,
)

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-a2d164a991f3e8a95320d87e"


logger = logging.getLogger(__name__)

# ─── paths ───────────────────────────────────────────────────────────────────
_EMBEDDED_AGENTS_ROOT = Path(__file__).resolve().parent.parent  # autoyou_agents/
_BOILERPLATE_DIR = Path(__file__).parent / "boilerplate"
_ROOT_AGENT_PY = _EMBEDDED_AGENTS_ROOT / "agent.py"
_ROOT_PROMPT_PY = _EMBEDDED_AGENTS_ROOT / "prompt.py"


def _get_writable_agents_root() -> Path:
    try:
        from shared.platform_runtime import get_dynamic_agents_root

        return get_dynamic_agents_root("AutoYou", anchor=__file__)
    except Exception:
        return _EMBEDDED_AGENTS_ROOT


_AGENTS_ROOT = _get_writable_agents_root()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _registry_agents_root_for(agent_name: str) -> Path:
    if (_AGENTS_ROOT / agent_name).is_dir():
        return _AGENTS_ROOT
    if (_EMBEDDED_AGENTS_ROOT / agent_name).is_dir():
        return _EMBEDDED_AGENTS_ROOT
    return _AGENTS_ROOT

def _to_snake(name: str) -> str:
    """Normalise to snake_case, strip trailing _agent if present, then re-add."""
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name).lower().strip("_")
    name = re.sub(r"_+", "_", name)
    if name.endswith("_agent"):
        name = name[: -len("_agent")]
    return f"{name}_agent"


# Fields whose values land in Python identifier / symbol positions in the
# boilerplate (e.g., `def {{tool_name}}(...)` or `create_{{name}}(...)`).
# We enforce a strict identifier regex before substitution so an LLM-controlled
# value cannot inject code by slipping parens, quotes, or whitespace in.
_PY_IDENTIFIER_CONTEXT_FIELDS = frozenset({"name", "tool_name"})

# Fields whose values appear verbatim in comments and docstrings but must not
# contain exotic characters. Restrict to a printable-ASCII subset.
_PRINTABLE_CONTEXT_FIELDS = frozenset({"timestamp"})

_PY_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PRINTABLE_ASCII_RE = re.compile(r"^[A-Za-z0-9 _\-:.,/+()]{1,80}$")


def _escape_py_string_body(value: str) -> str:
    """Escape a value for embedding inside a Python ``"..."`` OR ``\"\"\"...\"\"\"`` string.

    Uses ``json.dumps`` to produce a safely-escaped body (JSON string escapes are
    a strict subset of Python escapes), then additionally replaces any stray
    triple-quote sequences that could still terminate a docstring.
    """
    escaped = json.dumps(value, ensure_ascii=False)[1:-1]
    # Defensive: some boilerplate embeds values inside triple-quoted docstrings.
    # json.dumps does not escape `"""`, so neutralise it explicitly.
    escaped = escaped.replace('"""', '\\"\\"\\"')
    return escaped


def _sanitize_render_value(key: str, value: str) -> str:
    """Apply per-field validation/escaping to a template context value."""
    raw = str(value)
    # from __debug_provenance_m__ import of
    if key in _PY_IDENTIFIER_CONTEXT_FIELDS:
        if not _PY_IDENTIFIER_RE.match(raw):
            raise ValueError(
                f"{key!r} must be a valid Python identifier, got: {raw!r}"
            )
        return raw
    if key in _PRINTABLE_CONTEXT_FIELDS:
        if not _PRINTABLE_ASCII_RE.match(raw):
            raise ValueError(
                f"{key!r} must match a printable-ASCII subset, got: {raw!r}"
            )
        return raw
    # Default: any other LLM-supplied value is treated as untrusted text that
    # lands inside a Python string literal in the generated source.
    return _escape_py_string_body(raw)


def _render_template(template_path: Path, context: Dict[str, str]) -> str:
    text = template_path.read_text(encoding="utf-8")
    for key, value in context.items():
        safe = _sanitize_render_value(key, value)
        text = text.replace("{{" + key + "}}", safe)
    return text


def _admin_api_url(path: str) -> str:
    host = os.environ.get("ADMIN_WEB_SERVICE_HOST", "localhost")
    port = int(os.environ.get("ADMIN_WEB_SERVICE_PORT", "8001"))
    return f"http://{host}:{port}{path}"


def _call_admin_api(path: str, method: str = "POST", payload: Optional[Dict] = None) -> Dict[str, Any]:
    """Call the admin web server API; falls back to 127.0.0.1 if localhost fails."""
    url = _admin_api_url(path)
    timeout = int(os.environ.get("AI_AGENT_HTTP_TIMEOUT", "10"))
    try:
        resp = getattr(requests, method.lower())(url, json=payload, timeout=timeout)
    except requests.exceptions.ConnectionError:
        url = url.replace("localhost", "127.0.0.1")
        resp = getattr(requests, method.lower())(url, json=payload, timeout=timeout)
    try:
        data = resp.json()
    except ValueError:
        data = {"text": resp.text}
    return {"status": "success" if resp.ok else "error", "code": resp.status_code, "data": data, "url": url}


# ─── Tool 1: list_existing_agents ────────────────────────────────────────────

def list_existing_agents() -> Dict[str, Any]:
    """Return the names of all currently installed AutoYou sub-agent directories.

    Returns:
        A dict with 'agents' (list of str) and 'count'.
    """
    try:
        # The writable root is intentionally empty in a fresh compiled or
        # AUTOYOU_TEST_ROOT-scoped runtime. It holds drafts, not the built-in
        # product agents, so listing it alone makes the Admin Agents screen
        # report that no agents exist. Merge both roots while keeping the
        # writable location first for dynamic drafts.
        agents: set[str] = set()
        for agents_root in (_AGENTS_ROOT, _EMBEDDED_AGENTS_ROOT):
            agents.update(discover_agent_directories(agents_root))
        discovered = sorted(agents)
        return {"status": "success", "agents": discovered, "count": len(discovered)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ─── Tool 2: scaffold_agent ──────────────────────────────────────────────────

def scaffold_agent(
    name: str,
    description: str,
    tool_name: str,
    tool_description: str,
) -> Dict[str, Any]:
    """Scaffold a new sub-agent directory from the boilerplate templates.

    Generates `autoyou_agents/{name}/__init__.py`, `agent.py`, and `prompt.py`.
    Does NOT modify `agent.py` at the root level - call `patch_root_agent` next.

    Args:
        name: Desired agent name (normalised to snake_case automatically).
        description: One-sentence description of what the agent does.
        tool_name: Name of the first tool function (snake_case verb phrase).
        tool_description: One-sentence docstring for the first tool.

    Returns:
        dict with 'status', 'agent_name', 'agent_dir', and file paths created.
    """
    try:
        agent_name = _to_snake(name)
        existing = list_existing_agents().get("agents", [])
        if agent_name in existing:
            return {
                "status": "error",
                "message": f"An agent named '{agent_name}' already exists. Choose a different name.",
            }

        agent_dir = _AGENTS_ROOT / agent_name
        agent_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        tool_fn = re.sub(r"[^a-zA-Z0-9_]", "_", tool_name).lower().strip("_")
        context = {
            "name": agent_name,
            "description": description,
            "timestamp": ts,
            "tool_name": tool_fn,
            "tool_description": tool_description,
        }

        files_created = []
        for tmpl_name, out_name in [
            ("__init__.py.tmpl", "__init__.py"),
            ("agent.py.tmpl", "agent.py"),
            ("prompt.py.tmpl", "prompt.py"),
        ]:
            rendered = _render_template(_BOILERPLATE_DIR / tmpl_name, context)
            out_path = agent_dir / out_name
            out_path.write_text(rendered, encoding="utf-8")
            files_created.append(str(out_path))
            logger.info("scaffold_agent: wrote %s", out_path)

        return {
            "status": "success",
            "agent_name": agent_name,
            "agent_dir": str(agent_dir),
            "files_created": files_created,
            "message": (
                f"Agent '{agent_name}' scaffolded successfully. "
                "Call patch_root_agent() next, then restart_ai_agent_server()."
            ),
        }
    except FileExistsError:
        return {"status": "error", "message": f"Directory for '{name}' already exists."}
    except Exception as exc:
        logger.exception("scaffold_agent failed")
        return {"status": "error", "message": str(exc)}


# ─── Tool 3: patch_root_agent ────────────────────────────────────────────────

def patch_root_agent(agent_name: str, description: str) -> Dict[str, Any]:
    """Register a sub-agent in the install registry.

    The runtime root agent discovers installed agents dynamically from the
    install registry, then builds routing guidance from each installed agent's
    package prompt or registry description at startup. Installing an agent must
    not rewrite ``prompt.py`` because runtime install state is machine-local.

    Args:
        agent_name: Exact snake_case agent name (e.g. ``'weather_agent'``).
        description: Short human description used for the routing rule.

    Returns:
        dict with ``'status'`` and ``'patched_files'``.
    """
    try:
        agent_name = _to_snake(agent_name)
        compiled_runtime = False
        try:
            from shared.platform_runtime import is_compiled

            compiled_runtime = bool(is_compiled())
        except Exception:
            compiled_runtime = False

        if compiled_runtime and not is_builtin_agent_name(agent_name):
            block_reason = runtime_install_block_reason(agent_name)
            registry = set_agent_installed(
                agent_name,
                False,
                description=description,
                source="agent_builder",
                agents_root=_registry_agents_root_for(agent_name),
            )
            logger.info("patch_root_agent: saved workspace-only draft for %s", agent_name)
            return {
                "status": "success",
                "patched_files": [],
                "installed": False,
                "workspace_only": True,
                "requires_restart": False,
                "installed_agents": registry.get("installed_agents", []),
                "message": f"Saved '{agent_name}' as workspace-only. {block_reason}",
            }

        registry = set_agent_installed(
            agent_name,
            True,
            description=description,
            source="agent_builder",
            agents_root=_registry_agents_root_for(agent_name),
        )
        logger.info("patch_root_agent: registry updated for %s", agent_name)
        return {
            "status": "success",
            "patched_files": [],
            "installed": True,
            "workspace_only": False,
            "requires_restart": True,
            "installed_agents": registry.get("installed_agents", []),
            "message": (
                f"Registered '{agent_name}' in the install registry. "
                "Restart the AI agent runtime to apply the change."
            ),
        }
    except Exception as exc:
        logger.exception("patch_root_agent failed")
        return {"status": "error", "message": str(exc)}


# ─── Tool 4: restart_ai_agent_server ─────────────────────────────────────────

def restart_ai_agent_server() -> Dict[str, Any]:
    """Reload the current app's agent list through the Admin Web API.

    Calls POST /api/ai/restart on the admin server, which re-runs
    `initialize_root_agent()` to pick up newly imported agent modules
    without killing the server process.

    Returns:
        dict with 'status', 'code', and 'data'.
    """
    try:
        result = _call_admin_api("/api/ai/restart", method="POST")
        if result["status"] == "success":
            logger.info("restart_ai_agent_server: agent list reload triggered successfully")
        return result
    except Exception as exc:
        logger.exception("restart_ai_agent_server failed")
        return {"status": "error", "message": str(exc)}


# ─── Tool 5: get_builder_workflow_status ─────────────────────────────────────

def get_builder_workflow_status() -> Dict[str, Any]:
    """Return install and model status for the agent-builder workflow suite."""
    try:
        status = builder_suite_status(agents_root=_EMBEDDED_AGENTS_ROOT)
        return {
            "status": "success",
            "recommended_ollama_model": DEFAULT_BUILDER_SUITE_OLLAMA_MODEL,
            **status,
        }
    except Exception as exc:
        logger.exception("get_builder_workflow_status failed")
        return {"status": "error", "message": str(exc)}


# ─── Tool 6: install_builder_workflow_agents ─────────────────────────────────

def install_builder_workflow_agents(restart_ai: bool = False) -> Dict[str, Any]:
    """Install the builder, coding, and website agents as one workflow suite.

    Args:
        restart_ai: When true, call the Admin API to reload the running agent graph
            after installation succeeds.

    Returns:
        dict with installed/already_installed/failed agents and optional reload result.
    """
    try:
        result = install_builder_suite_agents(
            agents_root=_EMBEDDED_AGENTS_ROOT,
            source="agent_builder_suite",
        )
        result["recommended_ollama_model"] = DEFAULT_BUILDER_SUITE_OLLAMA_MODEL
        if restart_ai and result.get("status") in {"success", "partial"} and not result.get("failed"):
            result["reload"] = restart_ai_agent_server()
        return result
    except Exception as exc:
        logger.exception("install_builder_workflow_agents failed")
        return {"status": "error", "message": str(exc)}


# ─── Tool 7: get_scaffold_status ─────────────────────────────────────────────

def get_scaffold_status(agent_name: str) -> Dict[str, Any]:
    """Check whether a scaffolded agent directory exists and is importable.

    Args:
        agent_name: The snake_case agent name to check (e.g. 'weather_agent').

    Returns:
        dict with 'exists' (bool), 'importable' (bool), and 'files' list.
    """
    try:
        agent_name = _to_snake(agent_name)
        agent_dir = _AGENTS_ROOT / agent_name
        exists = agent_dir.is_dir()
        files = [f.name for f in agent_dir.iterdir()] if exists else []

        importable = False
        import_error = None
        runtime_blocked = False
        runtime_block_reason = None
        compiled_runtime = False
        try:
            from shared.platform_runtime import is_compiled

            compiled_runtime = bool(is_compiled())
        except Exception:
            compiled_runtime = False

        if compiled_runtime and not is_builtin_agent_name(agent_name):
            runtime_blocked = True
            runtime_block_reason = runtime_install_block_reason(agent_name)
        elif exists:
            try:
                mod = importlib.import_module(f"autoyou_agents.{agent_name}.agent")
                importable = hasattr(mod, f"create_{agent_name}")
            except Exception as e:
                import_error = str(e)

        return {
            "status": "success",
            "agent_name": agent_name,
            "exists": exists,
            "importable": importable,
            "runtime_loadable": bool(importable and not runtime_blocked),
            "runtime_blocked": runtime_blocked,
            "runtime_block_reason": runtime_block_reason,
            "files": files,
            "import_error": import_error,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ─── Tool 8: set_agent_web_port ──────────────────────────────────────────────

def set_agent_web_port(agent_name: str, port: int) -> Dict[str, Any]:
    """Register a dynamic HTTP port-forward for an agent's web server.

    After calling this, connected clients can access the agent's web server at
    the path `/agent/{agent_name}/` through AutoYou. The registration persists
    until the server restarts.

    Args:
        agent_name: The snake_case agent name (e.g. 'weather_agent').
        port: The local TCP port the agent's web server is listening on.

    Returns:
        dict with 'status' and 'proxy_path'.
    """
    try:
        return register_agent_web_port(_to_snake(agent_name), int(port))
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ─── Tool 9: prepare_website_handoff ─────────────────────────────────────────

def prepare_website_handoff(
    agent_name: str,
    description: str,
    tool_name: str,
    tool_description: str,
    implementation_brief: str,
    constraints: Optional[str] = None,
    testing_requirements: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Store a structured implementation handoff for autoyou_website_agent."""
    try:
        if tool_context is None:
            return {"status": "error", "message": "tool_context is required"}

        normalized_name = _to_snake(agent_name)
        agent_dir = _AGENTS_ROOT / normalized_name
        payload = build_website_handoff_payload(
            agent_name=normalized_name,
            description=description,
            tool_name=tool_name,
            tool_description=tool_description,
            implementation_brief=implementation_brief,
            constraints=constraints,
            testing_requirements=testing_requirements,
            agent_dir=agent_dir,
        )
        tool_context.state[WEBSITE_HANDOFF_STATE_KEY] = payload
        return {
            "status": "success",
            "target_agent": "autoyou_website_agent",
            "agent_name": normalized_name,
            "agent_dir": payload.get("agent_dir"),
            "recommended_local_port": payload.get("recommended_local_port"),
            "message": f"Website handoff prepared for {normalized_name}.",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ─── Tool 10: handoff_to_website_agent ──────────────────────────────────────

def handoff_to_website_agent(tool_context: Optional[ToolContext] = None) -> Dict[str, Any]:
    """Transfer control to autoyou_website_agent after a handoff has been prepared."""
    try:
        if tool_context is None:
            return {"status": "error", "message": "tool_context is required"}

        payload = tool_context.state.get(WEBSITE_HANDOFF_STATE_KEY)
        if not payload:
            return {
                "status": "error",
                "message": "No prepared website handoff exists in session state.",
            }

        tool_context.actions.skip_summarization = True
        _transfer_to_agent("autoyou_website_agent", tool_context)
        return {
            "status": "success",
            "target_agent": "autoyou_website_agent",
            "agent_name": payload.get("agent_name"),
            "message": "Transferred to autoyou_website_agent.",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ─── Tool 11: prepare_coding_handoff ─────────────────────────────────────────

def prepare_coding_handoff(
    agent_name: str,
    description: str,
    tool_name: str,
    tool_description: str,
    implementation_brief: str,
    constraints: Optional[str] = None,
    testing_requirements: Optional[str] = None,
    frontend_requirement: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Store a structured implementation handoff for coding_agent in session state."""
    try:
        if tool_context is None:
            return {"status": "error", "message": "tool_context is required"}

        normalized_name = _to_snake(agent_name)
        agent_dir = _AGENTS_ROOT / normalized_name
        payload = build_coding_handoff_payload(
            agent_name=normalized_name,
            description=description,
            tool_name=tool_name,
            tool_description=tool_description,
            implementation_brief=implementation_brief,
            constraints=constraints,
            testing_requirements=testing_requirements,
            frontend_requirement=frontend_requirement,
            agent_dir=agent_dir,
        )
        tool_context.state[CODING_HANDOFF_STATE_KEY] = payload
        return {
            "status": "success",
            "target_agent": "autoyou_coding_agent",
            "agent_name": normalized_name,
            "agent_dir": payload.get("agent_dir"),
            "frontend_requirement": payload.get("frontend_requirement"),
            "testing_requirements": payload.get("testing_requirements"),
            "message": f"Implementation handoff prepared for {normalized_name}.",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ─── Tool 12: handoff_to_coding_agent ────────────────────────────────────────

def handoff_to_coding_agent(tool_context: Optional[ToolContext] = None) -> Dict[str, Any]:
    """Transfer control to coding_agent after a handoff brief has been prepared."""
    try:
        if tool_context is None:
            return {"status": "error", "message": "tool_context is required"}

        payload = tool_context.state.get(CODING_HANDOFF_STATE_KEY)
        if not payload:
            return {
                "status": "error",
                "message": "No prepared coding handoff exists in session state.",
            }

        tool_context.actions.skip_summarization = True
        _transfer_to_agent("autoyou_coding_agent", tool_context)
        return {
            "status": "success",
            "target_agent": "autoyou_coding_agent",
            "agent_name": payload.get("agent_name"),
            "message": "Transferred to autoyou_coding_agent.",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ─── Agent factory ────────────────────────────────────────────────────────────

def create_agent_builder_agent(model_config):
    """Create the agent_builder_agent with its full tool suite.

    Args:
        model_config: The model configuration from get_model_config().

    Returns:
        A configured google.adk.agents.Agent instance.
    """
    tools = [
        list_existing_agents,
        scaffold_agent,
        patch_root_agent,
        restart_ai_agent_server,
        get_builder_workflow_status,
        install_builder_workflow_agents,
        get_scaffold_status,
        set_agent_web_port,
        prepare_website_handoff,
        handoff_to_website_agent,
        prepare_coding_handoff,
        handoff_to_coding_agent,
        get_current_datetime,
    ]
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
    )


prepare_agent_website_builder_handoff = prepare_website_handoff
handoff_to_agent_website_builder_agent = handoff_to_website_agent
prepare_frontend_proxy_handoff = prepare_website_handoff
handoff_to_frontend_proxy_agent = handoff_to_website_agent
