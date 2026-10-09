#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Generate the reference pages that are derived from source code.

Two pages are produced, so they cannot drift from the code:

* ``docs/api-reference/route-catalog.mdx``: every route declared in ``routers/``.
* ``docs/agent-framework/shared-tools-and-subsystems.mdx``: the shared helper
  modules in ``autoyou_agents/shared_tools/`` with their public functions.
* ``docs/agents/*.mdx``: the nine agent pages, one per domain, with each
  agent's description, declared tools, and website. An agent that is not
  assigned to a domain makes the script fail, so a new agent forces a docs update.

Everything is read with the ``ast`` module. Nothing is imported or started, so
the script is safe to run anywhere and never touches runtime state.

Usage:
    python scripts/generate_reference_docs.py          # rewrite the pages
    python scripts/generate_reference_docs.py --check  # exit 1 if either is stale
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTE_OUTPUT = REPO_ROOT / "docs" / "api-reference" / "route-catalog.mdx"
AGENT_DOCS_DIR = REPO_ROOT / "docs" / "agents"
AGENTS_DIR = REPO_ROOT / "autoyou_agents"
SHARED_TOOLS_DIR = AGENTS_DIR / "shared_tools"
SHARED_TOOLS_OUTPUT = REPO_ROOT / "docs" / "agent-framework" / "shared-tools-and-subsystems.mdx"
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "websocket"}

#: (router file, title, one-line purpose). Order is the order on the page.
ROUTER_FILES = (
    ("routers/admin.py", "Admin and configuration", "Configuration, password and security settings, authenticator setup, the chat and conversation API, status, scheduler summaries, and setup recipes."),
    ("routers/admin_ui.py", "Admin console and sign-in", "The console pages and assets, sign-in, native unlock, factory reset, software updates, guides, and legal documents."),
    ("routers/agents.py", "Agents and Agent Builder", "Agent catalog, install state, the Agent Builder workbench, agent website frontends, bookmarks, provider status, and per-agent 2FA profiles."),
    ("routers/pairing.py", "Pairing, unlock, and signaling", "First-run setup, unlock, device pairing, and WebRTC signaling."),
    ("routers/peer_rendezvous.py", "Peer Link rendezvous", "Invitation slots used to link one AutoYou app to another."),
    ("routers/webrtc.py", "WebRTC and media", "Live sessions, calls, microphone and screen sharing, and DataChannel status."),
    ("routers/messaging.py", "Messaging connectors", "Telegram, Signal, and WhatsApp connector control."),
    ("routers/models.py", "Models and speech", "The model library, speech models, and WebRTC configuration parsing."),
    ("routers/services.py", "Companion services", "Start, stop, and inspect the AI Agent worker, the page service, and the Tunnelmole public link, plus status and shutdown."),
    ("routers/cloud.py", "AutoYou Cloud", "Optional Cloud Pair registration and device approval."),
    ("routers/mcp.py", "MCP bridge", "Authenticated routes used by MCP clients."),
    ("routers/moderation.py", "Moderation", "Abuse reports and moderation state."),
    ("routers/ai_opt_out.py", "AI opt-out", "The `ai.txt` and `robots.txt` crawler-policy files, registered on both the admin and auth apps. A middleware on those apps also adds `X-Robots-Tag: noai, noimageai` and `TDM-Reservation: 1` headers to every response."),
    ("routers/ai_agent.py", "AI Agent worker", "Routes served by the separate AI Agent worker process."),
)

#: (page file, title, description, intro, agent directories). The union must be every agent.
AGENT_DOMAINS = (
    ("system-and-admin.mdx", "System & Administration Agents",
     "Administration, backup, hosting, and host network observability agents.",
     "Agents that administer the server, move files for backup, explain public hosting, and observe network activity on the host.",
     ("admin_agent", "backup_agent", "hosting_agent", "win_security_agent", "mac_security_agent")),
    ("coding-and-building.mdx", "Coding & Building Agents",
     "Agents for skills, files, code, and building new agents and websites.",
     "Agents that manage skills and files, edit code, build new agents and websites, and hand work to command-line and desktop coding tools.",
     ("skills_agent", "files_agent", "coding_agent", "agent_builder_agent", "website_agent", "cli_agent",
      "claude_cli_agent", "claude_desktop_agent", "codex_desktop_agent", "build_prompt_agent")),
    ("intelligence-and-models.mdx", "Intelligence & Model Agents",
     "Memory, persona, model selection, and bridged agent harnesses.",
     "Agents that keep memory and a persona, pick models, and bridge to other agent harnesses.",
     ("memory_agent", "persona_agent", "model_picker_agent", "openclaw_agent", "hermes_agent")),
    ("media-and-creative.mdx", "Media & Creative Agents",
     "Audio, media generation, notes, and the page feed.",
     "Agents for audio playback, image and video generation, notes, and the page feed.",
     ("audio_agent", "media_generation_agent", "notes_agent", "page_agent")),
    ("monetization.mdx", "Monetization Agents",
     "Support ads, earnings, and donation agents.",
     "Agents that relate to supporting AutoYou: support ads, credits and requests, and donations.",
     ("ads_watching_agent", "earnings_agent", "donation_agent")),
    ("utility.mdx", "Utility Agents",
     "Remote desktop, education, games, location, and proxy agents.",
     "Agents for remote desktop viewing, learning, game design, location history, and relaying web requests.",
     ("remote_desktop_agent", "education_agent", "game_agent", "location_agent", "proxy_agent")),
    ("web-and-automation.mdx", "Web & Automation Agents",
     "Browser automation, client browser control, and internet research.",
     "Agents that drive a real browser, control the browser on a connected client, and search and read the web.",
     ("browser_agent", "client_browser_control_agent", "internet_agent")),
    ("communication-and-tasks.mdx", "Communication & Task Agents",
     "Reminders and scheduled tasks.",
     "Agents that send timed notifications and schedule recurring jobs.",
     ("notify_agent", "tasks_agent")),
    ("ai-and-training.mdx", "AI & Training Agents",
     "Dataset collection, fine-tuning, and voice training.",
     "Agents that prepare datasets, run local fine-tuning, and train custom voices.",
     ("data_collector_agent", "fine_tuning_agent", "voice_training_agent")),
)

APP_LABELS = {
    "admin_app": "Admin app",
    "auth_app": "Auth app",
    "app_instance": "AI Agent worker",
    "app": "Admin and auth apps",
}


class Route(NamedTuple):
    app: str
    method: str
    path: str
    handler: str
    summary: str


def _humanize(name: str) -> str:
    text = name.strip("_").replace("_", " ")
    return text[:1].upper() + text[1:] if text else name


def _escape(text: str) -> str:
    """Make text safe for an MDX table cell or paragraph."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
        .replace("|", "\\|")
        .replace("\n", " ")
        .strip()
    )


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- routes


def collect_routes(relative: str) -> List[Route]:
    routes: List[Route] = []
    for node in ast.walk(_parse(REPO_ROOT / relative)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
                continue
            if decorator.func.attr not in HTTP_METHODS or not isinstance(decorator.func.value, ast.Name):
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue
            path = decorator.args[0].value
            if not isinstance(path, str):
                continue
            doc = (ast.get_docstring(node) or "").strip().splitlines()
            summary = doc[0].strip() if doc else _humanize(node.name)
            routes.append(Route(decorator.func.value.id, decorator.func.attr.upper(), path, node.name, summary))
    return sorted(routes, key=lambda r: (r.app, r.path, r.method))


def collect_gateway() -> List[Route]:
    """Routes that ``routers/website_gateway.py`` adds to the admin app with ``add_route``."""
    values: Dict[str, List[str]] = {}
    for node in _parse(REPO_ROOT / "routers" / "website_gateway.py").body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, (ast.Tuple, ast.List)):
                values[node.targets[0].id] = [
                    e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
    methods = ", ".join(f"`{m}`" for m in values.get("_HTTP_METHODS", []))
    routes: List[Route] = []
    for path in values.get("WEBSITE_GATEWAY_PATHS", []):
        routes.append(Route("admin_app", methods, path, "WebsiteGateway", "Served by the page service on the admin origin; requires an admin sign-in."))
        if path.startswith("/agent/"):
            routes.append(Route("admin_app", "`WEBSOCKET`", path, "WebsiteGateway", "WebSocket proxy to the agent website; requires an admin sign-in."))
    return routes


def _method_cell(method: str) -> str:
    return method if method.startswith("`") else f"`{method}`"


def _check_router_modules_listed() -> None:
    listed = {relative for relative, _, _ in ROUTER_FILES} | {"routers/website_gateway.py"}
    present = {f"routers/{p.name}" for p in (REPO_ROOT / "routers").glob("*.py") if p.name != "__init__.py"}
    unlisted = sorted(present - listed)
    if unlisted:
        raise SystemExit(
            "scripts/generate_reference_docs.py: add these router modules to ROUTER_FILES so their routes "
            f"appear in the route catalog: {unlisted}"
        )


def render_routes() -> str:
    _check_router_modules_listed()
    sections: List[str] = []
    totals: Dict[str, int] = {}
    for relative, title, purpose in ROUTER_FILES:
        routes = collect_routes(relative)
        if not routes:
            continue
        for route in routes:
            totals[route.app] = totals.get(route.app, 0) + 1
        rows = "\n".join(
            f"| {APP_LABELS.get(r.app, r.app)} | `{r.method}` | `{_escape(r.path)}` | {_escape(r.summary)} |"
            for r in routes
        )
        sections.append(
            f"## {title}\n\n{purpose} Source: `{relative}`.\n\n"
            "| App | Method | Path | Summary |\n| :--- | :--- | :--- | :--- |\n"
            f"{rows}\n"
        )
    gateway = collect_gateway()
    if gateway:
        totals["admin_app"] = totals.get("admin_app", 0) + len(gateway)
        rows = "\n".join(
            f"| {APP_LABELS['admin_app']} | {_method_cell(r.method)} | `{_escape(r.path)}` | {_escape(r.summary)} |"
            for r in gateway
        )
        sections.append(
            "## Website gateway\n\n"
            "Agent website routes mounted on the admin origin. Source: `routers/website_gateway.py`.\n\n"
            "| App | Method | Path | Summary |\n| :--- | :--- | :--- | :--- |\n"
            f"{rows}\n"
        )
    summary = ", ".join(f"{APP_LABELS.get(app, app)}: {count}" for app, count in sorted(totals.items()))
    header = (
        "---\n"
        'title: "Route Catalog"\n'
        'description: "Every HTTP and WebSocket route declared in routers/, generated from the source."\n'
        "---\n\n"
        "{/* Generated by scripts/generate_reference_docs.py. Do not edit by hand. */}\n\n"
        "# Route Catalog\n\n"
        "This page lists every route declared in `routers/`: the FastAPI decorator routes, and the website gateway paths that are added with `add_route`. "
        "It is generated from the source with `python scripts/generate_reference_docs.py`, "
        "and a test fails when it is out of date.\n\n"
        "A route registered on both the admin app and the auth app appears once for each. "
        "Summaries come from each handler's docstring, or its name when it has none. "
        "Paths in `{braces}` are path parameters. Request and response shapes are in the "
        "handlers; this catalog records which routes exist, not their payloads.\n\n"
        f"Routes counted ({summary}).\n\n"
    )
    return header + "\n".join(sections)


# --------------------------------------------------------------------------- agents


def _string_constants(tree: ast.Module) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for node in tree.body:
        target: Optional[ast.expr] = None
        value: Optional[ast.expr] = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and value is not None:
            try:
                literal = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                continue
            if isinstance(literal, str):
                out[target.id] = literal
    return out


def _tool_names(node: Optional[ast.AST]) -> List[str]:
    names: List[str] = []
    if isinstance(node, (ast.List, ast.Tuple)):
        for element in node.elts:
            if isinstance(element, ast.Name):
                names.append(element.id)
            elif isinstance(element, ast.Attribute):
                names.append(element.attr)
    return names


def _declared_tools(tree: ast.Module) -> List[str]:
    found: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "tools":
                    found += _tool_names(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "tools":
                found += _tool_names(node.value)
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "tools":
                    found += _tool_names(keyword.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "tools":
                    found += _tool_names(value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "get_tools":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return):
                    found += _tool_names(sub.value)
    return list(dict.fromkeys(found))


def _function_summaries(agent_dir: Path) -> Dict[str, str]:
    summaries: Dict[str, str] = {}
    for path in sorted(agent_dir.rglob("*.py")):
        relative = path.relative_to(agent_dir).parts
        if "website" in relative or path.name.startswith("test_"):
            continue
        try:
            tree = _parse(path)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = (ast.get_docstring(node) or "").strip().splitlines()
                summaries.setdefault(node.name, doc[0].strip() if doc else "")
    return summaries


def _render_agent(agent_dir: Path) -> str:
    agent_file = agent_dir / "agent.py"
    consts: Dict[str, str] = {}
    prompt_file = agent_dir / "prompt.py"
    if prompt_file.exists():
        consts.update(_string_constants(_parse(prompt_file)))
    agent_tree = _parse(agent_file)
    consts.update(_string_constants(agent_tree))
    description = " ".join(consts.get("AGENT_DESCRIPTION", "").split())
    if not description:
        module_doc = (ast.get_docstring(agent_tree) or "").strip().splitlines()
        description = module_doc[0].strip() if module_doc else ""
    tools = _declared_tools(agent_tree)
    summaries = _function_summaries(agent_dir)
    lines = [f"## `{agent_dir.name}`\n"]
    lines.append(f"{_escape(description) if description else 'No description is declared.'}\n")
    lines.append(f"- **Location:** `autoyou_agents/{agent_dir.name}/`")
    if (agent_dir / "website" / "frontend").is_dir():
        lines.append(f"- **Website:** `/agent/{agent_dir.name}/`")
    lines.append("")
    if tools:
        rows = "\n".join(f"| `{name}` | {_escape(summaries.get(name, '')) or '&nbsp;'} |" for name in tools)
        lines.append("| Declared tool | Summary |\n| :--- | :--- |\n" + rows + "\n")
    else:
        lines.append("No tools are declared statically in `agent.py`; they may be added at runtime.\n")
    return "\n".join(lines)


def render_agent_pages() -> Dict[Path, str]:
    available = {
        p.name
        for p in AGENTS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "agent.py").exists()
    }
    assigned = [name for *_, names in AGENT_DOMAINS for name in names]
    missing = sorted(available - set(assigned))
    unknown = sorted(set(assigned) - available)
    duplicate = sorted({n for n in assigned if assigned.count(n) > 1})
    if missing or unknown or duplicate:
        raise SystemExit(
            "AGENT_DOMAINS in scripts/generate_reference_docs.py is out of step with autoyou_agents/: "
            f"unassigned={missing} unknown={unknown} duplicate={duplicate}"
        )
    pages: Dict[Path, str] = {}
    for filename, title, description, intro, names in AGENT_DOMAINS:
        body = "\n".join(_render_agent(AGENTS_DIR / name) for name in names)
        pages[AGENT_DOCS_DIR / filename] = (
            "---\n"
            f'title: "{title}"\n'
            f'description: "{description}"\n'
            "---\n\n"
            "{/* Generated by scripts/generate_reference_docs.py. Do not edit by hand. */}\n\n"
            f"# {title}\n\n"
            f"{intro}\n\n"
            "This page is generated from the source with `python scripts/generate_reference_docs.py`, and a test fails "
            "when it is out of date. Descriptions come from each agent's `AGENT_DESCRIPTION`. Tools are the functions "
            "the agent's factory lists statically; an agent can also receive tools at runtime, for example from a "
            "toolset, and those are not listed. Tool summaries come from each function's docstring. An agent with a "
            "website is served at the path shown by the [page service](/api-reference/website-gateway).\n\n"
            f"{body}"
        )
    return pages


def render_shared_tools() -> str:
    sections: List[str] = []
    for path in sorted(SHARED_TOOLS_DIR.glob("*.py")):
        if path.name.startswith("_") and path.name != "_subprocess_env.py":
            continue
        tree = _parse(path)
        doc = (ast.get_docstring(tree) or "").strip()
        summary = " ".join(doc.split("\n\n")[0].split()) if doc else ""
        rows: List[str] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                first = (ast.get_docstring(node) or "").strip().splitlines()
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                rows.append(f"| `{node.name}` | {kind} | {_escape(first[0].strip()) if first else '&nbsp;'} |")
        lines = [f"## `{path.name}`\n", f"{_escape(summary) if summary else 'No module docstring.'}\n"]
        if rows:
            lines.append("| Public name | Kind | Summary |\n| :--- | :--- | :--- |\n" + "\n".join(rows) + "\n")
        else:
            lines.append("No public functions or classes.\n")
        sections.append("\n".join(lines))
    header = (
        "---\n"
        'title: "Shared Tools & Subsystems"\n'
        'description: "The helper modules in autoyou_agents/shared_tools/, generated from the source."\n'
        "---\n\n"
        "{/* Generated by scripts/generate_reference_docs.py. Do not edit by hand. */}\n\n"
        "# Shared Tools & Subsystems\n\n"
        "`autoyou_agents/shared_tools/` holds helpers that more than one agent uses: the agent install registry, "
        "agent draft handling, website scaffolding and registration, desktop-app bridges, workspace tools, and "
        "small utilities such as the date and time tool. This page is generated from the source with "
        "`python scripts/generate_reference_docs.py`, and a test fails when it is out of date. Each entry shows the "
        "module's own description and its public functions and classes with the first line of their docstrings.\n\n"
        "These are plain Python modules, not a framework: an agent's tools are ordinary functions, as described in "
        "[Building Custom Agents](building-agents.mdx).\n\n"
    )
    return header + "\n".join(sections)


# --------------------------------------------------------------------------- main


def outputs() -> Dict[Path, str]:
    pages = {ROUTE_OUTPUT: render_routes(), SHARED_TOOLS_OUTPUT: render_shared_tools()}
    pages.update(render_agent_pages())
    return pages


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if a generated page is stale")
    args = parser.parse_args(argv)
    expected = outputs()
    if args.check:
        stale = [
            path
            for path, text in expected.items()
            if (path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else "") != text
        ]
        for path in stale:
            print(f"{path.relative_to(REPO_ROOT)} is out of date; run scripts/generate_reference_docs.py", file=sys.stderr)
        if stale:
            return 1
        print("reference pages are current")
        return 0
    for path, text in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
