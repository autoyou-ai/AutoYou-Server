# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Shared stack metadata for AutoYou agent website scaffolds."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Tuple


DEFAULT_FRONTEND_STACK = "fastapi_static"
REACT_TYPESCRIPT_STACK = "react_typescript"

_FRONTEND_STACK_ALIASES = {
    "": DEFAULT_FRONTEND_STACK,
    "default": DEFAULT_FRONTEND_STACK,
    "fastapi": DEFAULT_FRONTEND_STACK,
    "fastapi_static": DEFAULT_FRONTEND_STACK,
    "python": DEFAULT_FRONTEND_STACK,
    "static": DEFAULT_FRONTEND_STACK,
    "html": DEFAULT_FRONTEND_STACK,
    "vanilla": DEFAULT_FRONTEND_STACK,
    "react": REACT_TYPESCRIPT_STACK,
    "react_ts": REACT_TYPESCRIPT_STACK,
    "react_typescript": REACT_TYPESCRIPT_STACK,
    "react-typescript": REACT_TYPESCRIPT_STACK,
    "typescript": REACT_TYPESCRIPT_STACK,
    "ts": REACT_TYPESCRIPT_STACK,
    "npm": REACT_TYPESCRIPT_STACK,
    "node": REACT_TYPESCRIPT_STACK,
}

FRONTEND_STACK_CHOICES: Tuple[Dict[str, str], ...] = (
    {
        "id": DEFAULT_FRONTEND_STACK,
        "label": "Simple website starter",
        "short_label": "Simple",
        "description": "Small local website that works without extra frontend setup or an AI model.",
    },
    {
        "id": REACT_TYPESCRIPT_STACK,
        "label": "React + TypeScript",
        "short_label": "React + TS",
        "description": "Starter for richer browser apps, served by AutoYou after the build step.",
    },
)


def normalize_frontend_stack(value: Any) -> str:
    raw = str(value or "").strip().lower()
    normalized = re.sub(r"[\s/]+", "_", raw)
    return _FRONTEND_STACK_ALIASES.get(normalized, DEFAULT_FRONTEND_STACK)


def frontend_stack_label(value: Any) -> str:
    normalized = normalize_frontend_stack(value)
    for choice in FRONTEND_STACK_CHOICES:
        if choice["id"] == normalized:
            return choice["label"]
    return "Simple website starter"


def frontend_stack_choices_payload() -> list[Dict[str, str]]:
    return [dict(choice) for choice in FRONTEND_STACK_CHOICES]


def frontend_template_outputs(frontend_stack: Any) -> Tuple[Tuple[str, str], ...]:
    normalized = normalize_frontend_stack(frontend_stack)
    if normalized == REACT_TYPESCRIPT_STACK:
        return (
            ("website/__init__.py.tmpl", "__init__.py"),
            ("backend/__init__.py.tmpl", "backend/__init__.py"),
            ("react_typescript/backend/app.py.tmpl", "backend/app.py"),
            ("react_typescript/frontend/package.json.tmpl", "frontend/package.json"),
            ("react_typescript/frontend/tsconfig.json.tmpl", "frontend/tsconfig.json"),
            ("react_typescript/frontend/vite.config.ts.tmpl", "frontend/vite.config.ts"),
            ("react_typescript/frontend/index.html.tmpl", "frontend/index.html"),
            ("react_typescript/frontend/src/main.tsx.tmpl", "frontend/src/main.tsx"),
            ("react_typescript/frontend/src/App.tsx.tmpl", "frontend/src/App.tsx"),
            ("react_typescript/frontend/src/styles.css.tmpl", "frontend/src/styles.css"),
            ("react_typescript/frontend/dist/index.html.tmpl", "frontend/dist/index.html"),
            ("react_typescript/frontend/dist/assets/app.js.tmpl", "frontend/dist/assets/app.js"),
            ("react_typescript/frontend/dist/assets/styles.css.tmpl", "frontend/dist/assets/styles.css"),
            ("README.md.tmpl", "README.md"),
        )

    return (
        ("website/__init__.py.tmpl", "__init__.py"),
        ("backend/__init__.py.tmpl", "backend/__init__.py"),
        ("backend/app.py.tmpl", "backend/app.py"),
        ("frontend/index.html.tmpl", "frontend/index.html"),
        ("frontend/app.js.tmpl", "frontend/app.js"),
        ("frontend/styles.css.tmpl", "frontend/styles.css"),
        ("README.md.tmpl", "README.md"),
    )


def package_slug_for_agent(agent_name: str) -> str:
    slug = str(agent_name or "agent").replace("_", "-").lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-") or "agent"
    return f"autoyou-{slug}-website"


def build_website_template_context(
    *,
    agent_name: str,
    app_title: str,
    ui_purpose: str,
    local_port: int,
    proxy_path: str,
    package_name: str,
    frontend_stack: Any = DEFAULT_FRONTEND_STACK,
) -> Dict[str, Any]:
    stack = normalize_frontend_stack(frontend_stack)
    stack_label = frontend_stack_label(stack)
    is_react = stack == REACT_TYPESCRIPT_STACK
    return {
        "agent_name": agent_name,
        "app_title": app_title,
        "ui_purpose": ui_purpose,
        "local_port": int(local_port),
        "proxy_path": proxy_path,
        "package_name": package_name,
        "frontend_stack": stack,
        "frontend_stack_label": stack_label,
        "package_slug": package_slug_for_agent(agent_name),
        "npm_install_command": "npm install" if is_react else "No frontend install needed.",
        "npm_build_command": "npm run build" if is_react else "No frontend build needed.",
        "local_run_command": (
            f"cd frontend && npm install && npm run build && cd .. && "
            f"uvicorn {package_name} --host 127.0.0.1 --port {int(local_port)}"
            if is_react
            else f"uvicorn {package_name} --host 127.0.0.1 --port {int(local_port)}"
        ),
        "runtime_note": (
            "React source lives in frontend/src and builds into frontend/dist. "
            "AutoYou serves the built files after the build step."
            if is_react
            else "This starter uses a small local website server and has no frontend build step."
        ),
    }


def iter_template_outputs(frontend_stack: Any) -> Iterable[Tuple[str, str]]:
    return frontend_template_outputs(frontend_stack)
