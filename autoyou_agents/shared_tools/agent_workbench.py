# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Shared workspace-draft helpers for the AutoYou agent studio."""

from __future__ import annotations

import ast
import html
import importlib
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from shared.platform_runtime import (
    get_dynamic_agents_root,
    get_embedded_agents_root,
    is_compiled,
    iter_agent_roots,
)
from shared.secure_storage import load_secure_json, save_secure_json

from .agent_identity import format_agent_display_name, resolve_runtime_agent_name
from .agent_install_registry import (
    can_install_agent_in_runtime,
    is_builtin_agent_name,
    normalize_agent_package_name,
    set_agent_installed,
)
from .frontend_manifest import (
    FRONTEND_MANIFEST_RELATIVE_PATH,
    build_frontend_manifest,
    load_frontend_manifest,
    write_frontend_manifest,
)
from .website_scaffold import (
    DEFAULT_FRONTEND_STACK,
    build_website_template_context,
    frontend_stack_label,
    iter_template_outputs,
    normalize_frontend_stack,
)


LOGGER = logging.getLogger(__name__)

AGENT_DRAFTS_DIRNAME = ".drafts"
AGENT_DRAFT_METADATA_FILENAME = ".autoyou-draft.json"
SOURCE_UNAVAILABLE_NOTE_FILENAME = "LIVE_SOURCE_UNAVAILABLE.md"
_COPY_IGNORE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    AGENT_DRAFTS_DIRNAME,
}
_COPY_IGNORE_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dylib",
    ".dll",
}
_BUILDER_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "agent_builder_agent" / "boilerplate"
_FRONTEND_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "website_agent" / "boilerplate"
_PY_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PRINTABLE_ASCII_RE = re.compile(r"^[A-Za-z0-9 _\-:.,/+()]{1,80}$")
_FRONTEND_NUMERIC_PLACEHOLDER_FIELDS = frozenset({"local_port"})


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _normalize_agent_name(raw_name: Optional[str]) -> str:
    normalized = normalize_agent_package_name(raw_name)
    if not normalized:
        raise ValueError("agent_name is required")
    return normalized


def _draft_metadata_base(agent_name: str, draft_dir: Path) -> Dict[str, Any]:
    timestamp = _utc_timestamp()
    return {
        "schema_version": 1,
        "agent_name": agent_name,
        "display_name": format_agent_display_name(resolve_runtime_agent_name(agent_name)),
        "draft_dir": str(draft_dir.resolve()),
        "draft_kind": None,
        "source_agent_name": agent_name,
        "source_kind": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "owners": {
            "builder": {"status": "idle", "updated_at": None},
            "coding": {"status": "idle", "updated_at": None},
            "frontend": {"status": "idle", "updated_at": None},
        },
    }


def get_agent_drafts_root(
    *,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Path:
    root = Path(agents_root).resolve() if agents_root is not None else get_dynamic_agents_root(app_name, anchor=anchor)
    drafts_root = root / AGENT_DRAFTS_DIRNAME
    drafts_root.mkdir(parents=True, exist_ok=True)
    return drafts_root.resolve()


def get_agent_draft_dir(
    agent_name: str,
    *,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Path:
    return get_agent_drafts_root(agents_root=agents_root, app_name=app_name, anchor=anchor) / _normalize_agent_name(agent_name)


def _draft_metadata_path(
    agent_name: str,
    *,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Path:
    return get_agent_draft_dir(
        agent_name,
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    ) / AGENT_DRAFT_METADATA_FILENAME


def list_agent_draft_names(
    *,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> list[str]:
    drafts_root = get_agent_drafts_root(agents_root=agents_root, app_name=app_name, anchor=anchor)
    names: list[str] = []
    for child in sorted(drafts_root.iterdir(), key=lambda entry: entry.name):
        if not child.is_dir():
            continue
        if child.name.startswith("_"):
            continue
        if (
            (child / AGENT_DRAFT_METADATA_FILENAME).is_file()
            or (child / "prompt.py").is_file()
            or (child / "agent.py").is_file()
        ):
            names.append(child.name)
    return names


def load_agent_draft_metadata(
    agent_name: str,
    *,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Dict[str, Any]:
    normalized = _normalize_agent_name(agent_name)
    draft_dir = get_agent_draft_dir(normalized, agents_root=agents_root, app_name=app_name, anchor=anchor)
    metadata = _draft_metadata_base(normalized, draft_dir)
    metadata_path = _draft_metadata_path(normalized, agents_root=agents_root, app_name=app_name, anchor=anchor)
    if metadata_path.is_file():
        try:
            raw = load_secure_json(metadata_path, default={})
        except (OSError, ValueError, TypeError):
            raw = {}
        if isinstance(raw, dict):
            for key in (
                "schema_version",
                "draft_kind",
                "source_agent_name",
                "source_kind",
                "created_at",
                "updated_at",
            ):
                if raw.get(key) not in (None, ""):
                    metadata[key] = raw.get(key)
            owners = raw.get("owners")
            if isinstance(owners, dict):
                for owner_name in ("builder", "coding", "frontend"):
                    owner_state = owners.get(owner_name)
                    if isinstance(owner_state, dict):
                        merged_state = dict(metadata["owners"].get(owner_name) or {})
                        merged_state.update(owner_state)
                        metadata["owners"][owner_name] = merged_state
    metadata["draft_dir"] = str(draft_dir.resolve())
    metadata["draft_exists"] = draft_dir.is_dir()
    metadata["has_prompt"] = (draft_dir / "prompt.py").is_file()
    metadata["has_agent_source"] = (draft_dir / "agent.py").is_file()
    metadata["has_frontend"] = (draft_dir / FRONTEND_MANIFEST_RELATIVE_PATH).is_file()
    return metadata


def update_agent_draft_metadata(
    agent_name: str,
    *,
    metadata_updates: Optional[Dict[str, Any]] = None,
    owner: Optional[str] = None,
    owner_updates: Optional[Dict[str, Any]] = None,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Dict[str, Any]:
    normalized = _normalize_agent_name(agent_name)
    draft_dir = get_agent_draft_dir(normalized, agents_root=agents_root, app_name=app_name, anchor=anchor)
    draft_dir.mkdir(parents=True, exist_ok=True)
    metadata = load_agent_draft_metadata(
        normalized,
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    )
    for key, value in (metadata_updates or {}).items():
        if key in {"owners", "draft_exists", "has_prompt", "has_agent_source", "has_frontend"}:
            continue
        metadata[key] = value

    if owner:
        owner_key = str(owner).strip().lower()
        if owner_key not in {"builder", "coding", "frontend"}:
            raise ValueError(f"Unknown draft owner: {owner}")
        state = dict(metadata.setdefault("owners", {}).get(owner_key) or {})
        state.update(owner_updates or {})
        state["updated_at"] = _utc_timestamp()
        metadata["owners"][owner_key] = state

    metadata["updated_at"] = _utc_timestamp()
    metadata_path = _draft_metadata_path(
        normalized,
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    )
    save_secure_json(metadata_path, metadata)
    return load_agent_draft_metadata(
        normalized,
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    )


def delete_agent_draft(
    agent_name: str,
    *,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Dict[str, Any]:
    normalized = _normalize_agent_name(agent_name)
    draft_dir = get_agent_draft_dir(normalized, agents_root=agents_root, app_name=app_name, anchor=anchor)
    if not draft_dir.exists():
        raise FileNotFoundError(f"No workspace draft exists for {normalized}")
    shutil.rmtree(draft_dir)
    return {"status": "success", "agent_name": normalized, "draft_dir": str(draft_dir)}


def _serialize_python_string_literal(value: str) -> str:
    if "'''" not in value:
        return "'''" + value + "'''"
    return repr(value)


def _find_string_assignment_node(module_ast: ast.Module, variable_name: str) -> Optional[ast.AST]:
    for node in module_ast.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == variable_name:
                    return node
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == variable_name:
                return node
    return None


def _resolve_string_assignment_value(
    module_ast: ast.Module,
    variable_name: str,
    seen: Optional[set[str]] = None,
) -> str:
    seen = seen or set()
    if variable_name in seen:
        raise ValueError(f"Cyclic string assignment reference for {variable_name}")
    seen.add(variable_name)

    assign_node = _find_string_assignment_node(module_ast, variable_name)
    if not assign_node:
        raise ValueError(f"Could not find string assignment for {variable_name}")
    value_node = assign_node.value
    if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
        return value_node.value
    if isinstance(value_node, ast.Name):
        return _resolve_string_assignment_value(module_ast, value_node.id, seen)
    raise ValueError(f"{variable_name} is not assigned a string literal")


def extract_prompt_string_value(content: str, variable_name: str) -> str:
    return _resolve_string_assignment_value(ast.parse(content), variable_name)


def replace_prompt_string_value(content: str, variable_name: str, new_value: str) -> str:
    module_ast = ast.parse(content)
    assign_node = _find_string_assignment_node(module_ast, variable_name)
    if not assign_node:
        raise ValueError(f"Could not find string assignment for {variable_name}")
    if assign_node.lineno is None or assign_node.end_lineno is None:
        raise ValueError(f"Missing source location metadata for {variable_name}")

    lhs = variable_name
    source_segment = ast.get_source_segment(content, assign_node)
    if source_segment and "=" in source_segment:
        lhs = source_segment.split("=", 1)[0].strip() or variable_name

    lines = content.splitlines(keepends=True)
    replacement = f"{lhs} = {_serialize_python_string_literal(new_value)}\n"
    return "".join(lines[: assign_node.lineno - 1]) + replacement + "".join(lines[assign_node.end_lineno :])


def _render_prompt_source_from_module(module: Any) -> str:
    lines = [
        f"# synthesized from {module.__name__}",
        "",
    ]
    for key, value in module.__dict__.items():
        if key.startswith("_"):
            continue
        if not key.isupper():
            continue
        if not isinstance(value, str):
            continue
        lines.append(f"{key} = {_serialize_python_string_literal(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _read_live_prompt_source(agent_name: str) -> str:
    normalized = _normalize_agent_name(agent_name)
    live_dir = resolve_live_agent_dir(normalized)
    prompt_path = live_dir / "prompt.py" if live_dir is not None else None
    if prompt_path is not None and prompt_path.is_file():
        return prompt_path.read_text(encoding="utf-8")
    module = importlib.import_module(f"autoyou_agents.{normalized}.prompt")
    return _render_prompt_source_from_module(module)


def _live_roots() -> tuple[Path, ...]:
    return iter_agent_roots(__file__)


def resolve_live_agent_dir(agent_name: str) -> Optional[Path]:
    normalized = _normalize_agent_name(agent_name)
    for root in _live_roots():
        candidate = root / normalized
        if candidate.is_dir():
            return candidate.resolve()
    return None


def build_live_agent_source_info(agent_name: str) -> Dict[str, Any]:
    normalized = _normalize_agent_name(agent_name)
    live_dir = resolve_live_agent_dir(normalized)
    dynamic_root = get_dynamic_agents_root("AutoYou", anchor=__file__).resolve()
    embedded_root = get_embedded_agents_root(__file__).resolve()
    if live_dir is None:
        prompt_importable = False
        agent_importable = False
        try:
            importlib.import_module(f"autoyou_agents.{normalized}.prompt")
            prompt_importable = True
        except Exception:
            prompt_importable = False
        try:
            importlib.import_module(f"autoyou_agents.{normalized}.agent")
            agent_importable = True
        except Exception:
            agent_importable = False
        if not (prompt_importable or agent_importable):
            return {
                "exists": False,
                "agent_name": normalized,
                "agent_dir": None,
                "source_kind": None,
                "has_prompt_source": False,
                "has_agent_source": False,
                "has_frontend": False,
                "prompt_path": None,
                "agent_path": None,
                "prompt_importable": False,
                "agent_importable": False,
            }

        return {
            "exists": True,
            "agent_name": normalized,
            "agent_dir": None,
            "source_kind": "embedded" if is_builtin_agent_name(normalized) else ("workspace" if not is_compiled() else "embedded"),
            "has_prompt_source": False,
            "has_agent_source": False,
            "has_frontend": False,
            "prompt_path": None,
            "agent_path": None,
            "prompt_importable": prompt_importable,
            "agent_importable": agent_importable,
        }

    source_kind = "unknown"
    if live_dir == dynamic_root or dynamic_root in live_dir.parents:
        source_kind = "workspace"
    elif live_dir == embedded_root or embedded_root in live_dir.parents:
        source_kind = "embedded"

    return {
        "exists": True,
        "agent_name": normalized,
        "agent_dir": str(live_dir),
        "source_kind": source_kind,
        "has_prompt_source": (live_dir / "prompt.py").is_file(),
        "has_agent_source": (live_dir / "agent.py").is_file(),
        "has_frontend": (live_dir / FRONTEND_MANIFEST_RELATIVE_PATH).is_file(),
        "prompt_path": str((live_dir / "prompt.py").resolve()) if (live_dir / "prompt.py").is_file() else None,
        "agent_path": str((live_dir / "agent.py").resolve()) if (live_dir / "agent.py").is_file() else None,
        "prompt_importable": (live_dir / "prompt.py").is_file(),
        "agent_importable": (live_dir / "agent.py").is_file(),
    }


def _escape_py_string_body(value: str) -> str:
    escaped = json.dumps(value, ensure_ascii=False)[1:-1]
    return escaped.replace('"""', '\\"\\"\\"')


def _sanitize_template_value(key: str, value: Any) -> str:
    raw = str(value)
    if key in {"name", "tool_name"}:
        if not _PY_IDENTIFIER_RE.match(raw):
            raise ValueError(f"{key!r} must be a valid Python identifier, got: {raw!r}")
        return raw
    if key in {"timestamp"}:
        if not _PRINTABLE_ASCII_RE.match(raw):
            raise ValueError(f"{key!r} must match a printable-ASCII subset, got: {raw!r}")
        return raw
    return _escape_py_string_body(raw)


def _render_template(template_path: Path, context: Dict[str, Any]) -> str:
    text = template_path.read_text(encoding="utf-8")
    for key, value in context.items():
        text = text.replace("{{" + key + "}}", _sanitize_template_value(key, value))
    return text


def _copy_editable_tree(source_dir: Path, destination_dir: Path) -> list[str]:
    copied: list[str] = []
    for source_path in sorted(source_dir.rglob("*")):
        relative_path = source_path.relative_to(source_dir)
        if any(part in _COPY_IGNORE_DIR_NAMES for part in relative_path.parts):
            continue
        if source_path.is_dir():
            continue
        if source_path.name in {AGENT_DRAFT_METADATA_FILENAME, SOURCE_UNAVAILABLE_NOTE_FILENAME}:
            continue
        if source_path.suffix.lower() in _COPY_IGNORE_FILE_SUFFIXES:
            continue
        target_path = destination_dir / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        copied.append(str(target_path))
    return copied


def _write_source_unavailable_note(draft_dir: Path, *, agent_name: str) -> str:
    note_path = draft_dir / SOURCE_UNAVAILABLE_NOTE_FILENAME
    note_path.write_text(
        (
            f"# Editable draft for {agent_name}\n\n"
            "This draft was copied from an installed AutoYou app. Prompt text and "
            "frontend assets can still be edited here, but app code must be edited "
            "from an editable development workspace.\n"
        ),
        encoding="utf-8",
    )
    return str(note_path)


def read_live_instruction_payload(agent_name: str) -> Dict[str, Any]:
    normalized = _normalize_agent_name(agent_name)
    try:
        prompt_source = _read_live_prompt_source(normalized)
        instructions = extract_prompt_string_value(prompt_source, "AGENT_INSTRUCTION")
    except Exception as exc:
        return {
            "available": False,
            "agent_name": normalized,
            "error": str(exc),
        }

    description = ""
    try:
        description = extract_prompt_string_value(prompt_source, "AGENT_DESCRIPTION")
    except Exception:
        description = ""

    source_info = build_live_agent_source_info(normalized)
    compiled_runtime = bool(is_compiled())
    immutable_reason = (
        "Installed app files are read-only here. Clone to a workspace draft to edit safely."
        if compiled_runtime and is_builtin_agent_name(normalized)
        else "Live agent is shown read-only here. Clone to a workspace draft before editing."
    )

    return {
        "available": True,
        "agent_name": normalized,
        "instructions": instructions,
        "description": description,
        "read_only": True,
        "immutable_reason": immutable_reason,
        "prompt_path": source_info.get("prompt_path"),
        "source_kind": source_info.get("source_kind"),
        "source_code_available": bool(source_info.get("has_agent_source")),
    }


def read_draft_instruction_payload(
    agent_name: str,
    *,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Dict[str, Any]:
    normalized = _normalize_agent_name(agent_name)
    draft_dir = get_agent_draft_dir(normalized, agents_root=agents_root, app_name=app_name, anchor=anchor)
    prompt_path = draft_dir / "prompt.py"
    if not prompt_path.is_file():
        return {
            "available": False,
            "agent_name": normalized,
            "draft_dir": str(draft_dir),
            "error": "No draft prompt.py exists yet.",
        }

    prompt_source = prompt_path.read_text(encoding="utf-8")
    instructions = extract_prompt_string_value(prompt_source, "AGENT_INSTRUCTION")
    description = ""
    try:
        description = extract_prompt_string_value(prompt_source, "AGENT_DESCRIPTION")
    except Exception:
        description = ""
    return {
        "available": True,
        "agent_name": normalized,
        "instructions": instructions,
        "description": description,
        "read_only": False,
        "prompt_path": str(prompt_path.resolve()),
        "agent_path": str((draft_dir / "agent.py").resolve()) if (draft_dir / "agent.py").is_file() else None,
        "source_code_available": (draft_dir / "agent.py").is_file(),
    }


def scaffold_agent_draft(
    name: str,
    description: str,
    tool_name: str,
    tool_description: str,
    *,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Dict[str, Any]:
    agent_name = _normalize_agent_name(name)
    if resolve_live_agent_dir(agent_name) is not None:
        raise FileExistsError(f"A live agent named '{agent_name}' already exists.")

    draft_dir = get_agent_draft_dir(agent_name, agents_root=agents_root, app_name=app_name, anchor=anchor)
    if draft_dir.exists():
        raise FileExistsError(f"A workspace draft named '{agent_name}' already exists.")

    draft_dir.mkdir(parents=True, exist_ok=True)
    context = {
        "name": agent_name,
        "description": str(description or "").strip(),
        "timestamp": _utc_timestamp(),
        "tool_name": re.sub(r"[^a-zA-Z0-9_]", "_", str(tool_name or "")).lower().strip("_") or "handle_request",
        "tool_description": str(tool_description or description or "").strip(),
    }

    created_files: list[str] = []
    for template_name, output_name in (
        ("__init__.py.tmpl", "__init__.py"),
        ("agent.py.tmpl", "agent.py"),
        ("prompt.py.tmpl", "prompt.py"),
    ):
        rendered = _render_template(_BUILDER_TEMPLATE_DIR / template_name, context)
        output_path = draft_dir / output_name
        output_path.write_text(rendered, encoding="utf-8")
        created_files.append(str(output_path))

    metadata = update_agent_draft_metadata(
        agent_name,
        metadata_updates={
            "draft_kind": "scaffold",
            "source_agent_name": agent_name,
            "source_kind": "workspace_draft",
        },
        owner="builder",
        owner_updates={
            "status": "scaffolded",
            "description": str(description or "").strip(),
            "tool_name": context["tool_name"],
            "tool_description": context["tool_description"],
            "created_files": created_files,
            "message": "New workspace draft scaffold created.",
        },
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    )
    return {
        "status": "success",
        "agent_name": agent_name,
        "draft_dir": str(draft_dir.resolve()),
        "created_files": created_files,
        "metadata": metadata,
    }


def clone_live_agent_to_draft(
    agent_name: str,
    *,
    overwrite: bool = False,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Dict[str, Any]:
    normalized = _normalize_agent_name(agent_name)
    live_dir = resolve_live_agent_dir(normalized)
    if live_dir is None:
        raise FileNotFoundError(f"Live agent '{normalized}' was not found.")

    draft_dir = get_agent_draft_dir(normalized, agents_root=agents_root, app_name=app_name, anchor=anchor)
    if draft_dir.exists():
        if not overwrite:
            raise FileExistsError(f"Workspace draft for '{normalized}' already exists.")
        shutil.rmtree(draft_dir)
    draft_dir.mkdir(parents=True, exist_ok=True)

    copied_files = _copy_editable_tree(live_dir, draft_dir)
    if not (draft_dir / "prompt.py").is_file():
        prompt_source = _read_live_prompt_source(normalized)
        (draft_dir / "prompt.py").write_text(prompt_source, encoding="utf-8")
        copied_files.append(str((draft_dir / "prompt.py").resolve()))
    if not (draft_dir / "__init__.py").is_file():
        (draft_dir / "__init__.py").write_text("", encoding="utf-8")
        copied_files.append(str((draft_dir / "__init__.py").resolve()))

    source_info = build_live_agent_source_info(normalized)
    source_unavailable_note = None
    if not (draft_dir / "agent.py").is_file():
        source_unavailable_note = _write_source_unavailable_note(draft_dir, agent_name=normalized)
        copied_files.append(source_unavailable_note)

    metadata = update_agent_draft_metadata(
        normalized,
        metadata_updates={
            "draft_kind": "clone",
            "source_agent_name": normalized,
            "source_kind": source_info.get("source_kind"),
        },
        owner="builder",
        owner_updates={
            "status": "cloned",
            "source_dir": str(live_dir),
            "copied_files": copied_files,
            "source_code_available": bool(source_info.get("has_agent_source")),
            "message": (
                "Cloned the live agent into a workspace draft."
                if source_info.get("has_agent_source")
                else "Cloned prompt and web assets into a workspace draft. App code must be edited from an editable development workspace."
            ),
        },
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    )
    return {
        "status": "success",
        "agent_name": normalized,
        "draft_dir": str(draft_dir.resolve()),
        "copied_files": copied_files,
        "source_code_available": bool(source_info.get("has_agent_source")),
        "metadata": metadata,
    }


def save_draft_instruction(
    agent_name: str,
    instructions: str,
    *,
    description: Optional[str] = None,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Dict[str, Any]:
    normalized = _normalize_agent_name(agent_name)
    prompt_path = get_agent_draft_dir(
        normalized,
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    ) / "prompt.py"
    if not prompt_path.is_file():
        raise FileNotFoundError(f"No draft prompt.py exists for '{normalized}'. Clone or scaffold a draft first.")

    content = prompt_path.read_text(encoding="utf-8")
    updated = replace_prompt_string_value(content, "AGENT_INSTRUCTION", str(instructions or "").strip())
    if description is not None:
        updated = replace_prompt_string_value(updated, "AGENT_DESCRIPTION", str(description).strip())
    prompt_path.write_text(updated, encoding="utf-8")

    metadata = update_agent_draft_metadata(
        normalized,
        owner="coding",
        owner_updates={
            "status": "instructions_saved",
            "instruction_path": str(prompt_path.resolve()),
            "instruction_length": len(str(instructions or "").strip()),
            "description": str(description).strip() if description is not None else None,
        },
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    )
    return {
        "status": "success",
        "agent_name": normalized,
        "prompt_path": str(prompt_path.resolve()),
        "metadata": metadata,
    }


def _escape_frontend_html(value: str) -> str:
    return html.escape(value, quote=True)


def _escape_frontend_python_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _escape_frontend_markdown(value: str) -> str:
    sanitized = value.replace("\\", "\\\\").replace("`", "\\`")
    sanitized = sanitized.replace("<", "&lt;").replace(">", "&gt;")
    return sanitized.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _escape_frontend_json_or_js(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _escape_frontend_css(value: str) -> str:
    return re.sub(r"[<>{};*/\"']", "", value)


def _coerce_frontend_numeric(value: Any, field: str) -> str:
    raw = str(value).strip()
    if not re.fullmatch(r"-?\d+", raw):
        raise ValueError(f"{field} must be an integer literal, got: {value!r}")
    return str(int(raw))


def _select_frontend_escaper(template_name: str):
    lower = template_name.lower()
    if lower.endswith(".tmpl"):
        lower = lower[: -len(".tmpl")]
    if lower.endswith(".html") or lower.endswith(".htm"):
        return _escape_frontend_html
    if lower.endswith(".py"):
        return _escape_frontend_python_string
    if lower.endswith(".md") or lower.endswith(".markdown"):
        return _escape_frontend_markdown
    if (
        lower.endswith(".json")
        or lower.endswith(".js")
        or lower.endswith(".jsx")
        or lower.endswith(".mjs")
        or lower.endswith(".ts")
        or lower.endswith(".tsx")
    ):
        return _escape_frontend_json_or_js
    if lower.endswith(".css"):
        return _escape_frontend_css
    return _escape_frontend_html


def _render_frontend_template(template_name: str, context: Dict[str, Any]) -> str:
    text = (_FRONTEND_TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    escape = _select_frontend_escaper(template_name)
    for key, value in context.items():
        placeholder = "{{" + key + "}}"
        if key in _FRONTEND_NUMERIC_PLACEHOLDER_FIELDS:
            safe = _coerce_frontend_numeric(value, key)
        else:
            safe = escape(str(value))
        text = text.replace(placeholder, safe)
    return text


def scaffold_frontend_draft(
    agent_name: str,
    *,
    ui_purpose: str,
    local_port: int,
    app_title: Optional[str] = None,
    frontend_stack: str = DEFAULT_FRONTEND_STACK,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Dict[str, Any]:
    normalized = _normalize_agent_name(agent_name)
    stack = normalize_frontend_stack(frontend_stack)
    draft_dir = get_agent_draft_dir(normalized, agents_root=agents_root, app_name=app_name, anchor=anchor)
    if not draft_dir.is_dir():
        raise FileNotFoundError(f"No workspace draft exists for '{normalized}'.")

    website_root = draft_dir / "website"
    backend_dir = website_root / "backend"
    frontend_dir = website_root / "frontend"
    backend_dir.mkdir(parents=True, exist_ok=True)
    frontend_dir.mkdir(parents=True, exist_ok=True)

    title = (app_title or "").strip() or f"{format_agent_display_name(resolve_runtime_agent_name(normalized))} Website"
    purpose = str(ui_purpose or "").strip() or "Agent website shell"
    local_port = int(local_port)
    package_name = f"autoyou_agents.{normalized}.website.backend.app:app"
    context = build_website_template_context(
        agent_name=normalized,
        app_title=title,
        ui_purpose=purpose,
        local_port=local_port,
        proxy_path=f"/agent/{normalized}/",
        package_name=package_name,
        frontend_stack=stack,
    )

    created_files: list[str] = []
    skipped_files: list[str] = []
    for template_name, output_relative_path in iter_template_outputs(stack):
        output_path = website_root / output_relative_path
        if output_path.exists():
            skipped_files.append(str(output_path.resolve()))
            continue
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(_render_frontend_template(template_name, context), encoding="utf-8")
        created_files.append(str(output_path.resolve()))

    manifest = build_frontend_manifest(
        agent_name=normalized,
        title=title,
        description=purpose,
        recommended_port=local_port,
        requires_proxy_registration=True,
        frontend_stack=stack,
    )
    manifest_path = write_frontend_manifest(draft_dir, manifest)
    created_files.append(str(manifest_path.resolve()))

    metadata = update_agent_draft_metadata(
        normalized,
        owner="frontend",
        owner_updates={
            "status": "frontend_scaffolded",
            "website_root": str(website_root.resolve()),
            "manifest_path": str(manifest_path.resolve()),
            "recommended_port": local_port,
            "title": title,
            "description": purpose,
            "frontend_stack": stack,
            "frontend_stack_label": frontend_stack_label(stack),
            "created_files": created_files,
            "skipped_files": skipped_files,
        },
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    )
    return {
        "status": "success",
        "agent_name": normalized,
        "website_root": str(website_root.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "frontend_stack": stack,
        "frontend_stack_label": frontend_stack_label(stack),
        "created_files": created_files,
        "skipped_files": skipped_files,
        "metadata": metadata,
    }


def save_draft_frontend_manifest(
    agent_name: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    entry_path: str = "/",
    recommended_port: Optional[int] = None,
    requires_proxy_registration: bool = True,
    frontend_stack: Optional[str] = None,
    manifest: Optional[Dict[str, Any]] = None,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Dict[str, Any]:
    normalized = _normalize_agent_name(agent_name)
    draft_dir = get_agent_draft_dir(normalized, agents_root=agents_root, app_name=app_name, anchor=anchor)
    if not draft_dir.is_dir():
        raise FileNotFoundError(f"No workspace draft exists for '{normalized}'.")

    existing_manifest = load_frontend_manifest(draft_dir) or {}
    if isinstance(manifest, dict):
        title = manifest.get("title", title)
        description = manifest.get("description", description)
        entry_path = manifest.get("entry_path", entry_path)
        recommended_port = manifest.get("recommended_port", recommended_port)
        requires_proxy_registration = manifest.get(
            "requires_proxy_registration",
            requires_proxy_registration,
        )
        frontend_stack = manifest.get("frontend_stack", frontend_stack)

    stack = normalize_frontend_stack(
        frontend_stack
        if frontend_stack not in (None, "")
        else existing_manifest.get("frontend_stack", DEFAULT_FRONTEND_STACK)
    )
    manifest_title = (
        str(title)
        if title is not None
        else str(existing_manifest.get("title") or normalized)
    )
    manifest_description = (
        str(description)
        if description is not None
        else str(existing_manifest.get("description") or "")
    )
    manifest = build_frontend_manifest(
        agent_name=normalized,
        title=manifest_title,
        description=manifest_description,
        entry_path=entry_path,
        recommended_port=recommended_port,
        requires_proxy_registration=requires_proxy_registration,
        frontend_stack=stack,
    )
    if existing_manifest.get("direct_forward_port") not in (None, ""):
        manifest["direct_forward_port"] = existing_manifest.get("direct_forward_port")
    manifest_path = write_frontend_manifest(draft_dir, manifest)

    metadata = update_agent_draft_metadata(
        normalized,
        owner="frontend",
        owner_updates={
            "status": "manifest_saved",
            "manifest_path": str(manifest_path.resolve()),
            "recommended_port": int(recommended_port) if recommended_port not in (None, "") else None,
            "title": manifest.get("title"),
            "description": manifest.get("description"),
            "entry_path": manifest.get("entry_path"),
            "requires_proxy_registration": bool(requires_proxy_registration),
            "frontend_stack": stack,
            "frontend_stack_label": frontend_stack_label(stack),
        },
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    )
    return {
        "status": "success",
        "agent_name": normalized,
        "manifest_path": str(manifest_path.resolve()),
        "manifest": manifest,
        "frontend_stack": stack,
        "metadata": metadata,
    }


def _list_website_files(base_dir: Path) -> list[str]:
    website_root = base_dir / "website"
    if not website_root.is_dir():
        return []
    return sorted(
        str(path.relative_to(base_dir))
        for path in website_root.rglob("*")
        if path.is_file()
    )


def build_draft_runtime_test(
    agent_name: str,
    *,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Dict[str, Any]:
    normalized = _normalize_agent_name(agent_name)
    draft_dir = get_agent_draft_dir(
        normalized,
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    )
    prompt_path = draft_dir / "prompt.py"
    agent_path = draft_dir / "agent.py"
    manifest_path = draft_dir / FRONTEND_MANIFEST_RELATIVE_PATH

    result = {
        "status": "success",
        "agent_name": normalized,
        "draft_dir": str(draft_dir.resolve()),
        "prompt_valid": False,
        "agent_valid": False,
        "factory_found": False,
        "frontend_manifest_valid": False,
        "runtime_loadable_estimate": False,
        "errors": [],
    }

    if prompt_path.is_file():
        try:
            ast.parse(prompt_path.read_text(encoding="utf-8"))
            result["prompt_valid"] = True
        except Exception as exc:
            result["errors"].append(f"prompt.py: {exc}")
    else:
        result["errors"].append("prompt.py is missing")

    if agent_path.is_file():
        try:
            module_ast = ast.parse(agent_path.read_text(encoding="utf-8"))
            result["agent_valid"] = True
            factory_name = f"create_{normalized}"
            result["factory_found"] = any(
                isinstance(node, ast.FunctionDef) and node.name == factory_name
                for node in module_ast.body
            )
            if not result["factory_found"]:
                result["errors"].append(f"agent.py is missing {factory_name}()")
        except Exception as exc:
            result["errors"].append(f"agent.py: {exc}")
    else:
        result["errors"].append("agent.py is missing")

    if manifest_path.is_file():
        try:
            json.loads(manifest_path.read_text(encoding="utf-8"))
            result["frontend_manifest_valid"] = True
        except Exception as exc:
            result["errors"].append(f"website/manifest.json: {exc}")

    result["runtime_loadable_estimate"] = bool(
        result["prompt_valid"] and result["agent_valid"] and result["factory_found"]
    )
    return result


def publish_agent_draft(
    agent_name: str,
    *,
    install_after_publish: bool = False,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Dict[str, Any]:
    if is_compiled():
        raise PermissionError(
            "Workspace drafts cannot be published directly into an installed AutoYou app. "
            "Move this draft into an editable development workspace to install and test it."
        )

    normalized = _normalize_agent_name(agent_name)
    draft_dir = get_agent_draft_dir(normalized, agents_root=agents_root, app_name=app_name, anchor=anchor)
    if not draft_dir.is_dir():
        raise FileNotFoundError(f"No workspace draft exists for '{normalized}'.")

    test_result = build_draft_runtime_test(
        normalized,
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    )
    if not test_result.get("runtime_loadable_estimate"):
        raise ValueError("Draft is not publish-ready. Fix the draft and test it before publishing.")

    live_root = Path(agents_root).resolve() if agents_root is not None else get_dynamic_agents_root(app_name, anchor=anchor).resolve()
    live_dir = live_root / normalized
    backups_root = get_agent_drafts_root(agents_root=agents_root, app_name=app_name, anchor=anchor) / "_published_backups"
    backups_root.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if live_dir.exists():
        backup_path = backups_root / f"{normalized}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        shutil.copytree(live_dir, backup_path)
        shutil.rmtree(live_dir)
    live_dir.mkdir(parents=True, exist_ok=True)

    published_files = _copy_editable_tree(draft_dir, live_dir)
    if install_after_publish:
        description = ""
        try:
            description = read_draft_instruction_payload(
                normalized,
                agents_root=agents_root,
                app_name=app_name,
                anchor=anchor,
            ).get("description") or ""
        except Exception:
            description = ""
        set_agent_installed(
            normalized,
            True,
            description=description or None,
            source="draft_publish",
            agents_root=live_root,
        )

    metadata = update_agent_draft_metadata(
        normalized,
        owner="coding",
        owner_updates={
            "status": "published",
            "live_path": str(live_dir.resolve()),
            "backup_path": str(backup_path.resolve()) if backup_path is not None else None,
            "published_files": published_files,
            "install_after_publish": bool(install_after_publish),
        },
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    )
    return {
        "status": "success",
        "agent_name": normalized,
        "live_path": str(live_dir.resolve()),
        "backup_path": str(backup_path.resolve()) if backup_path is not None else None,
        "published_files": published_files,
        "install_after_publish": bool(install_after_publish),
        "metadata": metadata,
    }


def build_agent_draft_summary(
    agent_name: str,
    *,
    agents_root: Optional[Path] = None,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Dict[str, Any]:
    normalized = _normalize_agent_name(agent_name)
    draft_dir = get_agent_draft_dir(normalized, agents_root=agents_root, app_name=app_name, anchor=anchor)
    metadata = load_agent_draft_metadata(normalized, agents_root=agents_root, app_name=app_name, anchor=anchor)
    prompt_state = read_draft_instruction_payload(
        normalized,
        agents_root=agents_root,
        app_name=app_name,
        anchor=anchor,
    )
    manifest = load_frontend_manifest(draft_dir) if draft_dir.is_dir() else None
    test_allowed = not is_compiled()
    return {
        "exists": draft_dir.is_dir(),
        "agent_name": normalized,
        "draft_dir": str(draft_dir.resolve()),
        "metadata": metadata,
        "instruction": prompt_state,
        "frontend_manifest": manifest,
        "website_files": _list_website_files(draft_dir) if draft_dir.is_dir() else [],
        "has_source_code": (draft_dir / "agent.py").is_file(),
        "source_unavailable_note": (
            str((draft_dir / SOURCE_UNAVAILABLE_NOTE_FILENAME).resolve())
            if (draft_dir / SOURCE_UNAVAILABLE_NOTE_FILENAME).is_file()
            else None
        ),
        "can_publish": bool(draft_dir.is_dir() and not is_compiled()),
        "can_test": bool(draft_dir.is_dir() and test_allowed),
        "can_install_runtime": can_install_agent_in_runtime(normalized, compiled=is_compiled()),
    }
