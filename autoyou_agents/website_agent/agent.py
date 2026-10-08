# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-U-usdt-9acb14b5677de03c611c2925

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import html
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from google.adk.agents import Agent
from google.adk.tools import ToolContext
from google.adk.tools.transfer_to_agent_tool import transfer_to_agent as _transfer_to_agent

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from autoyou_agents.agent_builder_agent.agent import get_scaffold_status, list_existing_agents
from autoyou_agents.shared_tools.agent_web_proxy import (
    normalize_agent_name,
    register_agent_web_port,
)
from autoyou_agents.shared_tools.coding_handoff import (
    CODING_HANDOFF_STATE_KEY,
    build_coding_handoff_payload,
)
from autoyou_agents.shared_tools.builder_suite import (
    DEFAULT_BUILDER_SUITE_OLLAMA_MODEL,
    builder_suite_status,
    install_builder_suite_agents,
)
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime
from autoyou_agents.shared_tools.frontend_manifest import (
    build_frontend_manifest,
    write_frontend_manifest,
)
from autoyou_agents.shared_tools.frontend_registry import (
    load_frontend_registry,
    refresh_frontend_registry,
)
from autoyou_agents.shared_tools.website_handoff import (
    ACTIVE_WEBSITE_CONTEXT_STATE_KEY,
    DEFAULT_WEBSITE_LOCAL_PORT,
    WEBSITE_HANDOFF_STATE_KEY,
)
from autoyou_agents.shared_tools.website_scaffold import (
    DEFAULT_BACKEND_STACK,
    DEFAULT_FRONTEND_STACK,
    backend_stack_label,
    default_managed_runtime,
    build_website_template_context,
    frontend_stack_label,
    iter_template_outputs,
    normalize_backend_stack,
    normalize_frontend_stack,
)

__debug_provenance_u__ = "AUTOYOU-PROVENANCE-U-usdt-9acb14b5677de03c611c2925"


logger = logging.getLogger(__name__)

def _get_agents_root() -> Path:
    """Get autoyou_agents root, handling both dev and compiled contexts."""
    try:
        from shared.platform_runtime import get_dynamic_agents_root

        agents_root = get_dynamic_agents_root("AutoYou", anchor=__file__)
        if agents_root.is_dir():
            return agents_root
    except (ImportError, Exception):
        pass
    # Fallback: traverse up from this file
    return Path(__file__).resolve().parents[1]

_AGENTS_ROOT = _get_agents_root()
_BOILERPLATE_DIR = Path(__file__).resolve().parent / "boilerplate"

_NUMERIC_PLACEHOLDER_FIELDS = frozenset({"local_port"})

def _escape_for_html(value: str) -> str:
    """HTML-escape a value for use in an element body or quoted attribute."""
    return html.escape(value, quote=True)

def _escape_for_python_string(value: str) -> str:
    """Escape a value so it is safe inside a Python double-quoted string literal.

    `json.dumps` produces a valid JSON string literal, and JSON string escapes
    are a strict subset of Python string escapes, so the body (without the
    surrounding quotes) is safe to paste into ``"..."`` in a .py file.
    """
    return json.dumps(value, ensure_ascii=False)[1:-1]

def _escape_for_markdown(value: str) -> str:
    """Neutralize characters that let an LLM-controlled value break out of
    a Markdown text context (backticks, angle-bracket tags, raw HTML)."""
    sanitized = value.replace("\\", "\\\\").replace("`", "\\`")
    sanitized = sanitized.replace("<", "&lt;").replace(">", "&gt;")
    # Collapse CR/LF so a multiline value cannot inject headings or code fences.
    sanitized = sanitized.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return sanitized

def _escape_for_json_or_js(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]

def _escape_for_css(value: str) -> str:
    # Drop characters that could break out of a CSS declaration/selector.
    return re.sub(r"[<>{};*/\"']", "", value)

def _coerce_numeric_field(value: Any, field: str) -> str:
    raw = str(value).strip()
    if not re.fullmatch(r"-?\d+", raw):
        raise ValueError(f"{field} must be an integer literal, got: {value!r}")
    return str(int(raw))

def _select_escaper(template_name: str) -> Callable[[str], str]:
    lower = template_name.lower()
    # Strip trailing .tmpl so we detect the real target file type.
    if lower.endswith(".tmpl"):
        lower = lower[: -len(".tmpl")]
    if lower.endswith(".html") or lower.endswith(".htm"):
        return _escape_for_html
    if lower.endswith(".py"):
        return _escape_for_python_string
    if lower.endswith(".md") or lower.endswith(".markdown"):
        return _escape_for_markdown
    if (
        lower.endswith(".json")
        or lower.endswith(".go")
        or lower.endswith(".js")
        or lower.endswith(".jsx")
        or lower.endswith(".mjs")
        or lower.endswith(".rs")
        or lower.endswith(".toml")
        or lower.endswith(".ts")
        or lower.endswith(".tsx")
    ):
        return _escape_for_json_or_js
    if lower.endswith(".css"):
        return _escape_for_css
    # Default to HTML-safe escaping - never return raw LLM-controlled text.
    return _escape_for_html

def _render_template(template_name: str, context: Dict[str, Any]) -> str:
    text = (_BOILERPLATE_DIR / template_name).read_text(encoding="utf-8")
    escape = _select_escaper(template_name)
    for key, value in context.items():
        placeholder = "{{" + key + "}}"
        if key in _NUMERIC_PLACEHOLDER_FIELDS:
            # Bare numeric literals (e.g., `"recommended_local_port": {{local_port}}`)
            # must not be quoted/escaped but must be validated as integers.
            safe = _coerce_numeric_field(value, key)
        else:
            safe = escape(str(value))
        text = text.replace(placeholder, safe)
    return text

def _safe_title(agent_name: str) -> str:
    return agent_name.replace("_", " ").strip().title()

def _resolve_agent_dir(agent_name: str) -> Path:
    candidate = _AGENTS_ROOT / agent_name
    if candidate.is_dir():
        return candidate
    try:
        from shared.platform_runtime import is_compiled, iter_agent_roots

        if is_compiled():
            for root in iter_agent_roots(__file__):
                resolved_candidate = root / agent_name
                if resolved_candidate.is_dir():
                    return resolved_candidate
    except Exception:
        pass
    return candidate

def get_pending_website_handoff(tool_context: ToolContext) -> Dict[str, Any]:
    """Return and activate any prepared builder -> website handoff."""
    payload = tool_context.state.get(WEBSITE_HANDOFF_STATE_KEY)
    if not payload:
        return {"status": "success", "handoff_present": False}

    tool_context.state[WEBSITE_HANDOFF_STATE_KEY] = None
    tool_context.state[ACTIVE_WEBSITE_CONTEXT_STATE_KEY] = payload
    return {
        "status": "success",
        "handoff_present": True,
        "handoff": payload,
    }

def get_builder_workflow_status() -> Dict[str, Any]:
    """Return install and model status for the agent-builder workflow suite."""
    try:
        status = builder_suite_status(agents_root=_AGENTS_ROOT)
        return {
            "status": "success",
            "recommended_ollama_model": DEFAULT_BUILDER_SUITE_OLLAMA_MODEL,
            **status,
        }
    except Exception as exc:
        logger.exception("get_builder_workflow_status failed")
        return {"status": "error", "message": str(exc)}

def install_builder_workflow_agents(restart_ai: bool = False) -> Dict[str, Any]:
    """Install the builder/coding/website workflow suite from the website agent."""
    try:
        result = install_builder_suite_agents(
            agents_root=_AGENTS_ROOT,
            source="website_agent_suite",
        )
        result["recommended_ollama_model"] = DEFAULT_BUILDER_SUITE_OLLAMA_MODEL
        if restart_ai and result.get("status") in {"success", "partial"} and not result.get("failed"):
            from autoyou_agents.agent_builder_agent.agent import restart_ai_agent_server

            result["reload"] = restart_ai_agent_server()
        return result
    except Exception as exc:
        logger.exception("install_builder_workflow_agents failed")
        return {"status": "error", "message": str(exc)}

def scaffold_website_split(
    agent_name: str,
    ui_purpose: str,
    local_port: int = DEFAULT_WEBSITE_LOCAL_PORT,
    app_title: Optional[str] = None,
    frontend_stack: str = DEFAULT_FRONTEND_STACK,
    backend_stack: str = DEFAULT_BACKEND_STACK,
) -> Dict[str, Any]:
    """Create a lightweight website split for an agent website.

    Python/FastAPI plus HTML/JS is the default. Other backend and frontend
    stacks create local HTTP starters that can be registered through AutoYou.
    """
    try:
        normalized_name = normalize_agent_name(agent_name)
        stack = normalize_frontend_stack(frontend_stack)
        backend = normalize_backend_stack(backend_stack)
        agent_dir = _resolve_agent_dir(normalized_name)
        if not agent_dir.is_dir():
            return {
                "status": "error",
                "message": f"Agent directory does not exist: {normalized_name}",
            }

        local_port = int(local_port)
        website_root = agent_dir / "website"
        backend_dir = website_root / "backend"
        frontend_dir = website_root / "frontend"
        backend_dir.mkdir(parents=True, exist_ok=True)
        frontend_dir.mkdir(parents=True, exist_ok=True)

        title = (app_title or "").strip() or f"{_safe_title(normalized_name)} Website"
        purpose = (ui_purpose or "").strip() or "Agent website shell"
        proxy_path = f"/agent/{normalized_name}/"
        package_name = f"autoyou_agents.{normalized_name}.website.backend.app:app"
        context = build_website_template_context(
            agent_name=normalized_name,
            app_title=title,
            ui_purpose=purpose,
            local_port=local_port,
            proxy_path=proxy_path,
            package_name=package_name,
            frontend_stack=stack,
            backend_stack=backend,
        )

        created_files: List[str] = []
        skipped_files: List[str] = []
        for template_name, output_relative_path in iter_template_outputs(stack, backend):
            output_path = website_root / output_relative_path
            if output_path.exists():
                skipped_files.append(str(output_path))
                continue
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                _render_template(template_name, context),
                encoding="utf-8",
            )
            created_files.append(str(output_path))

        manifest_path = website_root / "manifest.json"
        if manifest_path.exists():
            skipped_files.append(str(manifest_path))
        else:
            manifest = build_frontend_manifest(
                agent_name=normalized_name,
                title=title,
                description=purpose,
                entry_path="/",
                recommended_port=local_port,
                requires_proxy_registration=True,
                frontend_stack=stack,
                backend_stack=backend,
                managed_runtime=default_managed_runtime(backend),
            )
            created_manifest_path = write_frontend_manifest(agent_dir, manifest)
            created_files.append(str(created_manifest_path))
            current_registry = load_frontend_registry()
            refresh_frontend_registry(
                agents_root=_AGENTS_ROOT,
                proxy_ports={
                    item.get("agent_name"): item.get("proxy_port")
                    for item in current_registry.get("frontends", [])
                    if item.get("agent_name")
                },
                browser_base_url=current_registry.get("browser_base_url"),
            )

        return {
            "status": "success",
            "agent_name": normalized_name,
            "website_root": str(website_root),
            "local_port": local_port,
            "proxy_path": proxy_path,
            "frontend_stack": stack,
            "frontend_stack_label": frontend_stack_label(stack),
            "backend_stack": backend,
            "backend_stack_label": backend_stack_label(backend),
            "created_files": created_files,
            "skipped_files": skipped_files,
            "message": (
                f"Scaffolded {backend_stack_label(backend)} + {frontend_stack_label(stack)} "
                f"website/backend split for {normalized_name}. "
                f"Recommended local port: {local_port}."
            ),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

def register_website_port(agent_name: str, port: int) -> Dict[str, Any]:
    """Register the agent website backend port as an AutoYou website route."""
    try:
        return register_agent_web_port(agent_name, int(port))
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

def prepare_coding_handoff_from_website(
    agent_name: str,
    implementation_brief: str,
    constraints: Optional[str] = None,
    testing_requirements: Optional[str] = None,
    local_port: int = DEFAULT_WEBSITE_LOCAL_PORT,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Prepare a coding-agent handoff using the active website builder context."""
    try:
        if tool_context is None:
            return {"status": "error", "message": "tool_context is required"}

        normalized_name = normalize_agent_name(agent_name)
        active_context = tool_context.state.get(ACTIVE_WEBSITE_CONTEXT_STATE_KEY) or {}
        if active_context and active_context.get("agent_name") != normalized_name:
            active_context = {}

        agent_dir = _resolve_agent_dir(normalized_name)
        website_root = agent_dir / "website"
        proxy_path = f"/agent/{normalized_name}/"
        website_files = (
            sorted(str(path) for path in website_root.rglob("*") if path.is_file())
            if website_root.exists()
            else []
        )

        base_constraints = (active_context.get("constraints") or "").strip()
        workflow_constraints = (
            f"Agent website scaffold exists at {website_root}. "
            f"Keep browser asset paths relative so the existing prefix-stripping proxy remains valid. "
            f"Recommended local website port: {int(local_port)}. Proxy path: {proxy_path}"
        )
        merged_constraints = "\n".join(part for part in [base_constraints, (constraints or "").strip(), workflow_constraints] if part)

        payload = build_coding_handoff_payload(
            agent_name=normalized_name,
            description=active_context.get("description") or f"Agent website workflow for {normalized_name}",
            tool_name=active_context.get("tool_name") or "implement_website_workflow",
            tool_description=active_context.get("tool_description") or "Implements the generated backend/website scaffold",
            implementation_brief=implementation_brief,
            constraints=merged_constraints,
            testing_requirements=testing_requirements or active_context.get("testing_requirements"),
            frontend_requirement="yes",
            agent_dir=agent_dir,
        )
        payload["website_scaffold_files"] = website_files
        payload["frontend_scaffold_files"] = website_files
        payload["recommended_local_port"] = int(local_port)
        payload["proxy_path"] = proxy_path
        payload["dynamic_proxy_phase"] = "Phase 3"

        tool_context.state[CODING_HANDOFF_STATE_KEY] = payload
        return {
            "status": "success",
            "target_agent": "autoyou_coding_agent",
            "agent_name": normalized_name,
            "website_scaffold_files": website_files,
            "frontend_scaffold_files": website_files,
            "proxy_path": proxy_path,
            "recommended_local_port": int(local_port),
            "message": f"Coding handoff prepared for {normalized_name}.",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

def handoff_to_coding_agent(tool_context: Optional[ToolContext] = None) -> Dict[str, Any]:
    """Transfer control from the website builder workflow to coding_agent."""
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

def create_website_agent(model_config):
    """Create the website agent."""
    tools = [
        get_pending_website_handoff,
        get_builder_workflow_status,
        install_builder_workflow_agents,
        list_existing_agents,
        get_scaffold_status,
        scaffold_website_split,
        register_website_port,
        prepare_coding_handoff_from_website,
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

get_pending_agent_website_builder_handoff = get_pending_website_handoff
get_pending_frontend_workflow_handoff = get_pending_website_handoff
scaffold_agent_website_split = scaffold_website_split
scaffold_frontend_split = scaffold_website_split
register_agent_website_port = register_website_port
# from __debug_provenance_u__ import usdt
register_frontend_web_port = register_website_port
prepare_coding_handoff_from_agent_website = prepare_coding_handoff_from_website
prepare_coding_handoff_from_frontend = prepare_coding_handoff_from_website
create_agent_website_builder_agent = create_website_agent
create_frontend_proxy_agent = create_website_agent
