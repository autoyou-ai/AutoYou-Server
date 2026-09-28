# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

import base64
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from fastapi import Request

from autoyou_agents.shared_tools.scheduler_mission_control import (
    _api_auth_error,
    _json_response,
    create_agent_website_app,
)
from shared.platform_runtime import (
    get_mutable_data_dir,
    normalize_local_filesystem_path,
    strip_windows_extended_path_prefix,
)


APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
DIST_ASSETS_DIR = DIST_DIR / "assets"

AGENT_NAME = "files_agent"
APP_TITLE = "Files Agent"
DESCRIPTION = "Authenticated Finder-style local file browser for the AutoYou workspace and this computer."
WORKSPACE_ROOT_ENV = "AUTOYOU_FILES_AGENT_WORKSPACE_ROOT"
AUTOYOU_WORKSPACE_ROOT_ENV = "AUTOYOU_WORKSPACE_ROOT"
MAX_DIRECTORY_ENTRIES = 400
MAX_TEXT_PREVIEW_BYTES = 200_000
MAX_IMAGE_PREVIEW_BYTES = 2_000_000
TEXT_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".conf",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".log",
    ".md",
    ".py",
    ".ps1",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}


def _path_text(path: Path) -> str:
    return strip_windows_extended_path_prefix(path)


def _default_workspace_root() -> Path:
    raw_root = str(
        os.getenv(WORKSPACE_ROOT_ENV) or os.getenv(AUTOYOU_WORKSPACE_ROOT_ENV) or ""
    ).strip()
    if raw_root:
        return normalize_local_filesystem_path(raw_root)
    return normalize_local_filesystem_path(get_mutable_data_dir("AutoYou", anchor=__file__))


def _resolve_path(raw_path: str | None = None) -> Path:
    value = str(raw_path or "").strip()
    return normalize_local_filesystem_path(value) if value else _default_workspace_root()


def _parent_text(path: Path) -> str | None:
    parent = path.parent
    if parent == path:
        return None
    return _path_text(parent)


def _preview_kind(path: Path, size_bytes: int | None) -> str:
    if path.is_dir():
        return "folder"
    suffix = path.suffix.lower()
    media_type, _ = mimetypes.guess_type(str(path))
    if suffix in IMAGE_EXTENSIONS and (size_bytes or 0) <= MAX_IMAGE_PREVIEW_BYTES:
        return "image"
    if suffix in TEXT_EXTENSIONS or (media_type or "").startswith("text/"):
        return "text"
    if media_type in {"application/json", "application/xml"}:
        return "text"
    return "binary"


def _entry_payload(path: Path) -> Dict[str, Any]:
    try:
        stat = path.stat()
        size_bytes = None if path.is_dir() else stat.st_size
        modified = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    except OSError:
        size_bytes = None
        modified = None
    kind = "directory" if path.is_dir() else "file" if path.is_file() else "other"
    return {
        "name": path.name or _path_text(path),
        "path": _path_text(path),
        "parent": _parent_text(path),
        "kind": kind,
        "extension": path.suffix.lower(),
        "hidden": path.name.startswith("."),
        "size_bytes": size_bytes,
        "modified": modified,
        "preview_kind": _preview_kind(path, size_bytes),
    }


def _locations() -> list[Dict[str, str]]:
    seen: set[str] = set()
    rows: list[Dict[str, str]] = []

    def add(label: str, path: Path, kind: str) -> None:
        try:
            resolved = normalize_local_filesystem_path(path)
            if not resolved.exists():
                return
        except OSError:
            return
        key = str(resolved).lower() if os.name == "nt" else str(resolved)
        if key in seen:
            return
        seen.add(key)
        rows.append({"label": label, "path": _path_text(resolved), "kind": kind})

    workspace = _default_workspace_root()
    add("Workspace", workspace, "workspace")
    home = Path.home()
    add("Home", home, "home")
    for label in ("Desktop", "Documents", "Downloads", "Pictures"):
        add(label, home / label, "folder")
    if os.name == "nt":
        listdrives = getattr(os, "listdrives", None)
        drives = listdrives() if callable(listdrives) else sorted({Path.home().anchor, workspace.anchor})
        for drive in drives:
            add(str(drive).rstrip("\\/") or str(drive), Path(drive), "drive")
    else:
        add("Computer", Path("/"), "drive")
    return rows


def _error(message: str, status_code: int = 400):
    return _json_response({"success": False, "error": message}, status_code=status_code)


def _extra_routes(app, agent_name: str) -> None:
    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "agent_name": agent_name,
            "proxy_path": "/agent/files_agent/",
            "frontend_stack": "typescript_modules",
        }

    @app.get("/api/status")
    def status(request: Request):
        auth_error = _api_auth_error(agent_name, request)
        if auth_error:
            return auth_error
        return _json_response(
            {
                "success": True,
                "agent_name": agent_name,
                "title": APP_TITLE,
                "description": DESCRIPTION,
                "frontend_stack": "typescript_modules",
                "recommended_local_port": 8070,
                "proxy_path": "/agent/files_agent/",
                "mode": "local_filesystem_browser",
            }
        )

    @app.get("/api/locations")
    def locations(request: Request):
        auth_error = _api_auth_error(agent_name, request)
        if auth_error:
            return auth_error
        workspace = _default_workspace_root()
        return _json_response(
            {
                "success": True,
                "default_path": _path_text(workspace),
                "locations": _locations(),
            }
        )

    @app.get("/api/list")
    def list_directory(request: Request, path: str | None = None):
        auth_error = _api_auth_error(agent_name, request)
        if auth_error:
            return auth_error
        target = _resolve_path(path)
        try:
            if target.is_file():
                target = target.parent
            if not target.exists():
                return _error("Path does not exist.", 404)
            if not target.is_dir():
                return _error("Path is not a directory.", 400)
            entries: list[Dict[str, Any]] = []
            truncated = False
            for index, child in enumerate(target.iterdir()):
                if index >= MAX_DIRECTORY_ENTRIES:
                    truncated = True
                    break
                entries.append(_entry_payload(child))
        except PermissionError:
            return _error("Permission denied.", 403)
        except OSError as exc:
            return _error(str(exc), 400)

        entries.sort(key=lambda row: (row["kind"] != "directory", str(row["name"]).lower()))
        payload = _entry_payload(target)
        payload.update(
            {
                "success": True,
                "entries": entries,
                "count": len(entries),
                "truncated": truncated,
                "default_path": _path_text(_default_workspace_root()),
            }
        )
        return _json_response(payload)

    @app.get("/api/preview")
    def preview(request: Request, path: str):
        auth_error = _api_auth_error(agent_name, request)
        if auth_error:
            return auth_error
        target = _resolve_path(path)
        try:
            if not target.exists():
                return _error("Path does not exist.", 404)
            payload = _entry_payload(target)
            payload["success"] = True
            if target.is_dir():
                payload["children_count"] = sum(
                    1 for _, _child in zip(range(MAX_DIRECTORY_ENTRIES), target.iterdir())
                )
                return _json_response(payload)

            kind = str(payload["preview_kind"])
            size = int(payload.get("size_bytes") or 0)
            media_type, _ = mimetypes.guess_type(str(target))
            payload["media_type"] = media_type or "application/octet-stream"
            if kind == "image" and size <= MAX_IMAGE_PREVIEW_BYTES:
                image_data = base64.b64encode(target.read_bytes()).decode("ascii")
                payload["data_uri"] = f"data:{payload['media_type']};base64,{image_data}"
            elif kind == "text":
                with target.open("rb") as handle:
                    data = handle.read(MAX_TEXT_PREVIEW_BYTES + 1)
                if b"\x00" in data[:8192]:
                    payload["preview_kind"] = "binary"
                else:
                    payload["text"] = data[:MAX_TEXT_PREVIEW_BYTES].decode("utf-8", errors="replace")
                    payload["truncated"] = len(data) > MAX_TEXT_PREVIEW_BYTES
            return _json_response(payload)
        except PermissionError:
            return _error("Permission denied.", 403)
        except OSError as exc:
            return _error(str(exc), 400)


app = create_agent_website_app(
    agent_name=AGENT_NAME,
    title=APP_TITLE,
    description=DESCRIPTION,
    index_path=DIST_DIR / "index.html",
    assets_dir=DIST_ASSETS_DIR,
    extra_routes_fn=_extra_routes,
)
