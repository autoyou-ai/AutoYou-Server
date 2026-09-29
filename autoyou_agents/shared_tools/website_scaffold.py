# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-7ac0015fc68d948b967e408e

"""Shared stack metadata for AutoYou agent website scaffolds."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import re
from typing import Any, Dict, Iterable, Tuple

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-7ac0015fc68d948b967e408e"


DEFAULT_BACKEND_STACK = "python_fastapi"
NODE_TYPESCRIPT_BACKEND_STACK = "node_typescript"
GO_BACKEND_STACK = "go_http"
RUST_BACKEND_STACK = "rust_axum"

DEFAULT_FRONTEND_STACK = "fastapi_static"
REACT_TYPESCRIPT_STACK = "react_typescript"
ANGULAR_TYPESCRIPT_STACK = "angular_typescript"

_BACKEND_STACK_ALIASES = {
    "": DEFAULT_BACKEND_STACK,
    "default": DEFAULT_BACKEND_STACK,
    "fastapi": DEFAULT_BACKEND_STACK,
    "fastapi_static": DEFAULT_BACKEND_STACK,
    "python": DEFAULT_BACKEND_STACK,
    "py": DEFAULT_BACKEND_STACK,
    "python_fastapi": DEFAULT_BACKEND_STACK,
    "python-fastapi": DEFAULT_BACKEND_STACK,
    "node": NODE_TYPESCRIPT_BACKEND_STACK,
    "nodejs": NODE_TYPESCRIPT_BACKEND_STACK,
    "node_js": NODE_TYPESCRIPT_BACKEND_STACK,
    "node_typescript": NODE_TYPESCRIPT_BACKEND_STACK,
    "node-typescript": NODE_TYPESCRIPT_BACKEND_STACK,
    "typescript": NODE_TYPESCRIPT_BACKEND_STACK,
    "ts": NODE_TYPESCRIPT_BACKEND_STACK,
    "go": GO_BACKEND_STACK,
    "golang": GO_BACKEND_STACK,
    "go_http": GO_BACKEND_STACK,
    "go-http": GO_BACKEND_STACK,
    "rust": RUST_BACKEND_STACK,
    "rust_axum": RUST_BACKEND_STACK,
    "rust-axum": RUST_BACKEND_STACK,
    "axum": RUST_BACKEND_STACK,
}

_FRONTEND_STACK_ALIASES = {
    "": DEFAULT_FRONTEND_STACK,
    "default": DEFAULT_FRONTEND_STACK,
    "fastapi": DEFAULT_FRONTEND_STACK,
    "fastapi_static": DEFAULT_FRONTEND_STACK,
    "python": DEFAULT_FRONTEND_STACK,
    "static": DEFAULT_FRONTEND_STACK,
    "html": DEFAULT_FRONTEND_STACK,
    "html_js": DEFAULT_FRONTEND_STACK,
    "html-js": DEFAULT_FRONTEND_STACK,
    "javascript": DEFAULT_FRONTEND_STACK,
    "js": DEFAULT_FRONTEND_STACK,
    "vanilla": DEFAULT_FRONTEND_STACK,
    "vanilla_js": DEFAULT_FRONTEND_STACK,
    "react": REACT_TYPESCRIPT_STACK,
    "react_ts": REACT_TYPESCRIPT_STACK,
    "react_typescript": REACT_TYPESCRIPT_STACK,
    "react-typescript": REACT_TYPESCRIPT_STACK,
    "react_vite": REACT_TYPESCRIPT_STACK,
    "vite_react": REACT_TYPESCRIPT_STACK,
    "typescript": REACT_TYPESCRIPT_STACK,
    "ts": REACT_TYPESCRIPT_STACK,
    "npm": REACT_TYPESCRIPT_STACK,
    "node": REACT_TYPESCRIPT_STACK,
    "angular": ANGULAR_TYPESCRIPT_STACK,
    "angular_ts": ANGULAR_TYPESCRIPT_STACK,
    "angular_typescript": ANGULAR_TYPESCRIPT_STACK,
    "angular-typescript": ANGULAR_TYPESCRIPT_STACK,
}

BACKEND_STACK_CHOICES: Tuple[Dict[str, str], ...] = (
    {
        "id": DEFAULT_BACKEND_STACK,
        "label": "Python + FastAPI",
        "short_label": "Python",
        "description": "AutoYou-native backend with the shared per-agent session boundary.",
    },
    {
        "id": NODE_TYPESCRIPT_BACKEND_STACK,
        "label": "Node + TypeScript",
        "short_label": "Node + TS",
        "description": "Small Node HTTP server compiled from TypeScript.",
    },
    {
        "id": GO_BACKEND_STACK,
        "label": "Go HTTP",
        "short_label": "Go",
        "description": "Small net/http backend for lightweight compiled deployments.",
    },
    {
        "id": RUST_BACKEND_STACK,
        "label": "Rust + Axum",
        "short_label": "Rust",
        "description": "Axum/Tokio backend for Rust service deployments.",
    },
)

FRONTEND_STACK_CHOICES: Tuple[Dict[str, str], ...] = (
    {
        "id": DEFAULT_FRONTEND_STACK,
        "label": "HTML + JavaScript",
        "short_label": "HTML + JS",
        "description": "Small static frontend with no package install or build step.",
    },
    {
        "id": REACT_TYPESCRIPT_STACK,
        "label": "React + TypeScript",
        "short_label": "React + TS",
        "description": "Vite starter for richer browser apps, served after the build step.",
    },
    {
        "id": ANGULAR_TYPESCRIPT_STACK,
        "label": "Angular + TypeScript",
        "short_label": "Angular",
        "description": "Angular CLI starter, served after the build step.",
    },
)


def _normalize_stack_key(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return re.sub(r"[\s/]+", "_", raw)


def normalize_backend_stack(value: Any) -> str:
    return _BACKEND_STACK_ALIASES.get(_normalize_stack_key(value), DEFAULT_BACKEND_STACK)


def normalize_frontend_stack(value: Any) -> str:
    return _FRONTEND_STACK_ALIASES.get(_normalize_stack_key(value), DEFAULT_FRONTEND_STACK)


def backend_stack_label(value: Any) -> str:
    normalized = normalize_backend_stack(value)
    for choice in BACKEND_STACK_CHOICES:
        if choice["id"] == normalized:
            return choice["label"]
    return "Python + FastAPI"


def frontend_stack_label(value: Any) -> str:
    normalized = normalize_frontend_stack(value)
    for choice in FRONTEND_STACK_CHOICES:
        if choice["id"] == normalized:
            return choice["label"]
    return "HTML + JavaScript"


def backend_stack_choices_payload() -> list[Dict[str, str]]:
    return [dict(choice) for choice in BACKEND_STACK_CHOICES]


def frontend_stack_choices_payload() -> list[Dict[str, str]]:
    return [dict(choice) for choice in FRONTEND_STACK_CHOICES]


def backend_template_outputs(backend_stack: Any) -> Tuple[Tuple[str, str], ...]:
    normalized = normalize_backend_stack(backend_stack)
    if normalized == NODE_TYPESCRIPT_BACKEND_STACK:
        return (
            ("website/__init__.py.tmpl", "__init__.py"),
            ("node_typescript/backend/package.json.tmpl", "backend/package.json"),
            ("node_typescript/backend/tsconfig.json.tmpl", "backend/tsconfig.json"),
            ("node_typescript/backend/src/server.ts.tmpl", "backend/src/server.ts"),
        )
    if normalized == GO_BACKEND_STACK:
        return (
            ("website/__init__.py.tmpl", "__init__.py"),
            ("go_http/backend/go.mod.tmpl", "backend/go.mod"),
            ("go_http/backend/main.go.tmpl", "backend/main.go"),
        )
    if normalized == RUST_BACKEND_STACK:
        return (
            ("website/__init__.py.tmpl", "__init__.py"),
            ("rust_axum/backend/Cargo.toml.tmpl", "backend/Cargo.toml"),
            ("rust_axum/backend/src/main.rs.tmpl", "backend/src/main.rs"),
        )
    return (
        ("website/__init__.py.tmpl", "__init__.py"),
        ("backend/__init__.py.tmpl", "backend/__init__.py"),
        ("python_fastapi/backend/app.py.tmpl", "backend/app.py"),
    )


def frontend_template_outputs(frontend_stack: Any) -> Tuple[Tuple[str, str], ...]:
    normalized = normalize_frontend_stack(frontend_stack)
    if normalized == REACT_TYPESCRIPT_STACK:
        return (
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
        )
    if normalized == ANGULAR_TYPESCRIPT_STACK:
        return (
            ("angular_typescript/frontend/package.json.tmpl", "frontend/package.json"),
            ("angular_typescript/frontend/angular.json.tmpl", "frontend/angular.json"),
            ("angular_typescript/frontend/tsconfig.json.tmpl", "frontend/tsconfig.json"),
            ("angular_typescript/frontend/tsconfig.app.json.tmpl", "frontend/tsconfig.app.json"),
            ("angular_typescript/frontend/src/index.html.tmpl", "frontend/src/index.html"),
            ("angular_typescript/frontend/src/main.ts.tmpl", "frontend/src/main.ts"),
            ("angular_typescript/frontend/src/styles.css.tmpl", "frontend/src/styles.css"),
            ("angular_typescript/frontend/dist/browser/index.html.tmpl", "frontend/dist/browser/index.html"),
            ("angular_typescript/frontend/dist/browser/assets/app.js.tmpl", "frontend/dist/browser/assets/app.js"),
            ("angular_typescript/frontend/dist/browser/assets/styles.css.tmpl", "frontend/dist/browser/assets/styles.css"),
        )
    return (
        ("frontend/index.html.tmpl", "frontend/index.html"),
        ("frontend/assets/app.js.tmpl", "frontend/assets/app.js"),
        ("frontend/assets/styles.css.tmpl", "frontend/assets/styles.css"),
    )


def package_slug_for_agent(agent_name: str) -> str:
    slug = str(agent_name or "agent").replace("_", "-").lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-") or "agent"
    # from __debug_provenance_y__ import legal
    return f"autoyou-{slug}-website"


def rust_crate_name_for_agent(agent_name: str) -> str:
    return package_slug_for_agent(agent_name).replace("-", "_")


def _frontend_dist_dir(frontend_stack: str) -> str:
    if frontend_stack == ANGULAR_TYPESCRIPT_STACK:
        return "dist/browser"
    if frontend_stack == REACT_TYPESCRIPT_STACK:
        return "dist"
    return ""


def _frontend_root_relative(frontend_stack: str) -> str:
    dist = _frontend_dist_dir(frontend_stack)
    return f"frontend/{dist}" if dist else "frontend"


def _frontend_commands(frontend_stack: str) -> tuple[str, str, str]:
    if frontend_stack in {REACT_TYPESCRIPT_STACK, ANGULAR_TYPESCRIPT_STACK}:
        return ("npm install", "npm run build", "cd frontend && npm install && npm run build && cd ..")
    return ("No frontend install needed.", "No frontend build needed.", "")


def _backend_commands(backend_stack: str, package_name: str, local_port: int) -> tuple[str, str, str]:
    if backend_stack == NODE_TYPESCRIPT_BACKEND_STACK:
        return (
            "cd backend && npm install",
            "cd backend && npm run build",
            f"cd backend && npm install && npm run build && node dist/server.js --port {int(local_port)}",
        )
    if backend_stack == GO_BACKEND_STACK:
        return (
            "No backend install needed beyond Go.",
            "cd backend && go build .",
            f"cd backend && go run . --port {int(local_port)}",
        )
    if backend_stack == RUST_BACKEND_STACK:
        return (
            "No backend install needed beyond Rust/Cargo.",
            "cd backend && cargo build",
            f"cd backend && cargo run -- --port {int(local_port)}",
        )
    return (
        "Use the AutoYou Python environment with FastAPI and uvicorn.",
        "No backend build needed.",
        f"uvicorn {package_name} --host 127.0.0.1 --port {int(local_port)}",
    )


def _compose_local_run_command(frontend_step: str, backend_step: str) -> str:
    if frontend_step:
        return f"{frontend_step} && {backend_step}"
    return backend_step


def build_website_template_context(
    *,
    agent_name: str,
    app_title: str,
    ui_purpose: str,
    local_port: int,
    proxy_path: str,
    package_name: str,
    frontend_stack: Any = DEFAULT_FRONTEND_STACK,
    backend_stack: Any = DEFAULT_BACKEND_STACK,
) -> Dict[str, Any]:
    front_stack = normalize_frontend_stack(frontend_stack)
    back_stack = normalize_backend_stack(backend_stack)
    front_label = frontend_stack_label(front_stack)
    back_label = backend_stack_label(back_stack)
    frontend_dist_dir = _frontend_dist_dir(front_stack)
    frontend_root_relative = _frontend_root_relative(front_stack)
    frontend_install, frontend_build, frontend_step = _frontend_commands(front_stack)
    backend_install, backend_build, backend_step = _backend_commands(back_stack, package_name, int(local_port))
    python_native = back_stack == DEFAULT_BACKEND_STACK
    return {
        "agent_name": agent_name,
        "app_title": app_title,
        "ui_purpose": ui_purpose,
        "local_port": int(local_port),
        "proxy_path": proxy_path,
        "package_name": package_name,
        "frontend_stack": front_stack,
        "frontend_stack_label": front_label,
        "backend_stack": back_stack,
        "backend_stack_label": back_label,
        "package_slug": package_slug_for_agent(agent_name),
        "rust_crate_name": rust_crate_name_for_agent(agent_name),
        "frontend_dist_dir": frontend_dist_dir,
        "frontend_root_relative": frontend_root_relative,
        "npm_install_command": frontend_install,
        "npm_build_command": frontend_build,
        "frontend_install_command": frontend_install,
        "frontend_build_command": frontend_build,
        "backend_install_command": backend_install,
        "backend_build_command": backend_build,
        "backend_run_command": backend_step,
        "local_run_command": _compose_local_run_command(frontend_step, backend_step),
        "runtime_note": (
            f"{front_label} source is served by the {back_label} backend. "
            "Build the frontend before running the backend."
            if frontend_dist_dir
            else f"{front_label} files are served directly by the {back_label} backend."
        ),
        "backend_security_note": (
            "This Python backend uses AutoYou's shared per-agent session boundary."
            if python_native
            else (
                "This non-Python backend is a generic local HTTP starter. Keep private "
                "agent data behind explicit auth before exposing custom routes."
            )
        ),
    }


def iter_template_outputs(
    frontend_stack: Any,
    backend_stack: Any = DEFAULT_BACKEND_STACK,
) -> Iterable[Tuple[str, str]]:
    return (
        *backend_template_outputs(backend_stack),
        *frontend_template_outputs(frontend_stack),
        ("README.md.tmpl", "README.md"),
    )
