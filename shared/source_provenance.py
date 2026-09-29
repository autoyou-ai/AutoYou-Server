# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-8a2e09a2e180d9e91fae881e

"""Small provenance helpers used by the AutoYou watermark CLI."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-8a2e09a2e180d9e91fae881e"


_COMMENT_STYLES = {
    ".py": ("# ", ""),
    ".sh": ("# ", ""),
    ".bash": ("# ", ""),
    ".zsh": ("# ", ""),
    ".bat": ("REM ", ""),
    ".cmd": ("REM ", ""),
    ".js": ("// ", ""),
    ".ts": ("// ", ""),
    ".tsx": ("// ", ""),
    ".jsx": ("// ", ""),
    ".cjs": ("// ", ""),
    ".mjs": ("// ", ""),
    ".css": ("/* ", " */"),
    ".scss": ("/* ", " */"),
    ".html": ("<!-- ", " -->"),
    ".htm": ("<!-- ", " -->"),
    ".toml": ("# ", ""),
}

_EXCLUDED_DIRECTORIES = {
    ".git",
    ".venv",
    ".venv-build312",
    "venv",
    "env",
    "node_modules",
    "artifacts",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".vs",
    "bin",
    "obj",
    "assets",
    ".github",
    "AutoYouWindowsHost",
    ".agents",
    ".llm",
    ".run",
    "training",
    "support_training",
    "vendor",
    "tmp",
    "output",
    "scratch",
}

_EXCLUDED_FILES = {
    "LICENSE",
    "NOTICE.txt",
    "THIRD-PARTY-NOTICES.md",
    "manifest.json",
    ".gitleaks.toml",
    "Dockerfile",
    ".gitignore",
    "PROVENANCE_MANIFEST.json",
    "provenance_watermark.py",
}

_CANARY_LINE_RE = re.compile(
    r"^[ \t]*(?:#|//|<!--|/\*|REM)[ \t]*(?:AY|AUTOYOU)-PROVENANCE-[^\r\n]*\r?\n?",
    re.MULTILINE,
)
_COPYRIGHT_LICENSE_RE = re.compile(
    r"^[ \t]*__(?:copyright|license)__[ \t]*=[ \t]*[\"'][^\"']*[\"'][ \t]*\r?\n?",
    re.MULTILINE,
)
_PROVENANCE_VAR_RE = re.compile(
    r"^[ \t]*__\w+__[ \t]*=[ \t]*[\"'][^\"']*AUTOYOU-PROVENANCE-[^\"']*[\"'][ \t]*\r?\n?",
    re.MULTILINE,
)
_IMPORT_COMMENT_RE = re.compile(
    r"^[ \t]*# from __\w+__ import \w+[ \t]*\r?\n?",
    re.MULTILINE,
)
_PROVENANCE_CONTACT_RE = re.compile(
    r"^[ \t]*(?:#|//|<!--|/\*|REM)[ \t]*Provenance violations:[^\r\n]*\r?\n?",
    re.MULTILINE,
)
# from __debug_provenance_m__ import of


def _normalize_path(path: str | Path) -> str:
    return Path(path).as_posix().lstrip("./")


def _git_ignored_paths(repo_root: Path, rel_paths: list[str]) -> set[str] | None:
    if not rel_paths:
        return set()
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "check-ignore", "--stdin"],
        input="\n".join(rel_paths) + "\n",
        text=True,
        capture_output=True,
    )
    if proc.returncode not in (0, 1):
        return None
    return {_normalize_path(line) for line in proc.stdout.splitlines() if line}


def _strip_watermarks(content: str) -> str:
    content = _CANARY_LINE_RE.sub("", content)
    content = _COPYRIGHT_LICENSE_RE.sub("", content)
    content = _PROVENANCE_VAR_RE.sub("", content)
    content = _IMPORT_COMMENT_RE.sub("", content)
    content = _PROVENANCE_CONTACT_RE.sub("", content)
    content = re.sub(
        r"^[ \t]*(?:AY|AUTOYOU)-PROVENANCE-[^\r\n]*\r?\n",
        "",
        content,
        flags=re.MULTILINE,
    )
    content = re.sub(
        r"^[ \t]*[A-Z]-[0-9a-fA-F]{24}-[0-9a-fA-F]{24}[ \t]*\r?\n",
        "",
        content,
        flags=re.MULTILINE,
    )
    content = re.sub(
        r"^[ \t]*[0-9a-fA-F]{24}[ \t]*\r?\n",
        "",
        content,
        flags=re.MULTILINE,
    )
    return re.sub(r"\n{4,}", "\n\n\n", content)


def compute_file_canary(rel_path: str, content_sha256: str) -> str:
    seed = f"{_normalize_path(rel_path)}\0{content_sha256}\0AUTOYOU-PROVENANCE"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_comment_style(path: str | Path) -> tuple[str, str]:
    return _COMMENT_STYLES.get(Path(path).suffix.lower(), ("# ", ""))


def _eligible_files(repo_root: Path) -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    for dirpath_raw, dirnames, filenames in os.walk(repo_root):
        dirpath = Path(dirpath_raw)
        dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDED_DIRECTORIES)
        for name in sorted(filenames):
            fpath = dirpath / name
            if name in _EXCLUDED_FILES or fpath.suffix.lower() not in _COMMENT_STYLES:
                continue
            candidates.append((fpath, fpath.relative_to(repo_root).as_posix()))

    ignored = _git_ignored_paths(repo_root, [rel for _, rel in candidates])
    if ignored is None:
        return candidates
    return [(path, rel) for path, rel in candidates if _normalize_path(rel) not in ignored]


def _git_text(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        capture_output=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def build_manifest(repo_root: Path) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    files: dict[str, dict[str, str]] = {}
    for path, rel in _eligible_files(repo_root):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        clean = _strip_watermarks(content)
        clean_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()
        files[rel] = {
            "canary": compute_file_canary(rel, clean_hash),
            "content_sha256": hash_file(path),
            "canonical_content_sha256": clean_hash,
        }

    return {
        "schema": "autoyou-provenance-manifest-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_head": _git_text(repo_root, "rev-parse", "HEAD"),
        "working_tree_status": _git_text(repo_root, "status", "--short").splitlines(),
        "file_count": len(files),
        "files": files,
    }


def write_manifest(repo_root: Path, manifest: dict[str, Any]) -> Path:
    path = Path(repo_root) / "PROVENANCE_MANIFEST.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_manifest(repo_root: Path) -> dict[str, Any] | None:
    path = Path(repo_root) / "PROVENANCE_MANIFEST.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
