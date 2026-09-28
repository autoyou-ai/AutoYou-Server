# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-68747470733a2f2f6275792e-05e5f9d7353c820dd23ff721

"""Workspace editing tools for repo-aware coding agents.

These tools are intentionally scoped to the AutoYou repository root so an ADK
sub-agent can inspect, edit, and verify code without escaping the project.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-68747470733a2f2f6275792e-05e5f9d7353c820dd23ff721"


import base64
import collections
import difflib
import io
import os
import platform
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, Deque, Iterator, List, Optional

from ._subprocess_env import (
    _SENSITIVE_ENV_EXACT,
    _SENSITIVE_ENV_SUBSTRINGS,
    scrubbed_subprocess_env as _scrubbed_subprocess_env,
)


_DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_IGNORED_DIRS = {
    ".git",
    ".idea",
    ".next",
    ".venv",
    ".vscode",
    "__pycache__",
    "DerivedData",
    "build",
    "dist",
    "node_modules",
    "venv",
}
_MAX_TEXT_FILE_BYTES = 1_000_000
_MAX_OUTPUT_CHARS = 12_000
_MAX_RUN_OUTPUT_LINES = 500
_RUN_COMMAND_OPT_IN_ENV = "AUTOYOU_ENABLE_AGENT_RUN_COMMAND"
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
_BINARY_EXTENSIONS = {".pyc", ".pyo", ".so", ".dll", ".exe", ".whl", ".zip", ".tar", ".gz"}
_FILE_TYPE_GLOBS: Dict[str, List[str]] = {
    "py": ["*.py"],
    "js": ["*.js", "*.mjs", "*.cjs"],
    "ts": ["*.ts", "*.tsx", "*.mts"],
    "html": ["*.html", "*.htm"],
    "css": ["*.css", "*.scss", "*.sass", "*.less"],
    "json": ["*.json", "*.jsonc"],
    "md": ["*.md", "*.markdown"],
    "yaml": ["*.yaml", "*.yml"],
    "sh": ["*.sh", "*.bash", "*.zsh"],
    "sql": ["*.sql"],
    "go": ["*.go"],
    "rs": ["*.rs"],
    "java": ["*.java"],
    "cpp": ["*.cpp", "*.cc", "*.cxx", "*.h", "*.hpp"],
}
_DANGEROUS_COMMAND_PATTERNS = (
    # --- Git destructive operations ---
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+checkout\s+--\b",
    r"\bgit\s+clean\b.*\b-f\b",
    r"\bgit\s+push\s+.*--force\b",
    r"\bgit\s+push\s+-f\b",
    # --- Filesystem destruction ---
    r"\brm\s+-rf\b",
    r"\brm\s+-r\s+/\b",
    r"\bdd\s+.*\bof=/dev/\b",
    r"\bwipefs\b",
    r"\bshred\b",
    r"\bmkfs\b",
    r"\bdiskutil\s+erase",
    r"\bformat\s+[a-z]:",
    # --- Privilege escalation ---
    r"\bsudo\b",
    r"\bsu\s+-\b",
    r"\bsu\s+root\b",
    r"\bdoas\b",
    r"\bpkexec\b",
    # --- System operations ---
    r"\bshutdown\b",
    r"\breboot\b",
    r"\binit\s+[0-6]\b",
    r"\bsystemctl\s+(?:poweroff|reboot|halt)\b",
    r"\bsysctl\s+-w\b",
    r"\bmodprobe\b",
    r"\binsmod\b",
    r"\brmmod\b",
    # --- Remote-exec pipelines (curl|sh, wget|bash, base64 -d | sh, etc.) ---
    r"\bcurl\b[^|]*\|\s*(?:sh|bash|zsh|ksh|dash|python|python3|node|perl|ruby)\b",
    r"\bwget\b[^|]*\|\s*(?:sh|bash|zsh|ksh|dash|python|python3|node|perl|ruby)\b",
    r"\bbase64\b[^|]*-d[^|]*\|\s*(?:sh|bash|zsh|ksh|dash|python|python3|node|perl|ruby)\b",
    r"\bbase64\b[^|]*--decode[^|]*\|\s*(?:sh|bash|zsh|ksh|dash|python|python3|node|perl|ruby)\b",
    # --- Netcat / data exfiltration ---
    r"\|\s*nc\s+",
    r"\|\s*ncat\s+",
    r"\|\s*socat\b",
    r"\|\s*telnet\b",
    # --- Reverse shell patterns ---
    r"\bbash\s+-i\s+>&\s*/dev/tcp/",
    r"\bpython3?\s+-c\s+.*\bsocket\b.*\bconnect\b",
    r"\bperl\s+-e\s+.*\bsocket\b",
    r"\bnc\s+.*-e\s+(?:/bin/)?(?:sh|bash)\b",
    r"\bncat\s+.*-e\s+(?:/bin/)?(?:sh|bash)\b",
    r"\bsocat\b.*\bexec\b.*(?:sh|bash)\b",
    # --- Writing to autorun / system config ---
    r">\s*~/\.(?:bashrc|zshrc|profile|bash_profile|config/fish/config\.fish)\b",
    r">\s*/etc/(?:cron|profile|sudoers|passwd|shadow|hosts)",
    # --- Cron/scheduled task manipulation ---
    r"\bcrontab\s+-[re]\b",
    r"\bat\s+.*<<<",
    # --- Permission / ownership abuse ---
    r"\bchmod\s+(?:777|666|a\+[rwx])\b",
    r"\bchown\s+root\b",
    r"\bsetuid\b",
    # --- Container / VM escape ---
    r"\bdocker\s+run\s+.*--privileged\b",
    r"\bnsenter\b",
    r"\bunshare\b",
    # --- Process killing ---
    r"\bkill\s+-9\s+-1\b",
    r"\bkillall\b",
    r"\bpkill\s+-9\b",
    # --- Environment / secrets exfiltration ---
    r"\benv\b\s*\|\s*(?:curl|wget|nc|ncat|base64)\b",
    r"\bprintenv\b\s*\|\s*(?:curl|wget|nc|ncat|base64)\b",
    r"\bcat\s+.*\.(?:env|pem|key)\b\s*\|\s*(?:curl|wget|nc|ncat)\b",
    # --- History / log manipulation ---
    r"\bhistory\s+-[cdw]\b",
    r">\s*/var/log/",
    # --- Package manager from arbitrary URLs ---
    r"\bpip\s+install\s+.*https?://",
    r"\bnpm\s+install\s+.*https?://",
    # --- Fork bomb ---
    r":\(\)\{\s*:\|:&\s*\};\s*:",
    r"\bfork\s*\(\s*\)\s*&&",
    # --- Indirect subshells & execution wrappers (bypass evasion) ---
    r"\$\(",
    r"`",
    r"\bpython3?\s+-c\b",
    r"\b(?:bash|sh|zsh|ksh|dash)\s+-c\b",
)

def _workspace_root() -> Path:
    configured = os.environ.get("AUTOYOU_WORKSPACE_ROOT")
    root = Path(configured).expanduser() if configured else _DEFAULT_WORKSPACE_ROOT
    return root.resolve()


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    extra = len(text) - limit
    return text[:limit] + f"\n... [truncated {extra} chars]"


# Files an agent must never write, even though they sit inside the workspace.
#
# In a source run the mutable-data directory, the config directory and this
# workspace root are all the same directory, so path confinement alone does not
# separate "project files the agent may edit" from "state that authorizes the
# agent". Writing ``ACKNOWLEDGEMENT_AGREEMENT`` plus ``jailbreak_root_prompt.txt``
# activates the Prompt Override, whose contents are exec'd at agent start - that
# turns any prompt injection into persistent code execution. The keystore and
# encrypted config are protected for the same reason.
#
# Reads are unaffected; only mutation is denied.
_PROTECTED_BASENAMES = frozenset({
    "ACKNOWLEDGEMENT_AGREEMENT",
    "jailbreak_root_prompt.txt",
    "server_unlock.json",
    "ai_agent_internal_api_token.txt",
})
_PROTECTED_SUFFIXES = (".keystore.enc", ".key-mode")
_PROTECTED_NAME_PREFIXES = ("config.keystore.enc",)


def _is_protected_runtime_path(resolved: Path) -> bool:
    name = resolved.name
    if name in _PROTECTED_BASENAMES:
        return True
    if any(name.endswith(suffix) for suffix in _PROTECTED_SUFFIXES):
        return True
    if any(name.startswith(prefix) for prefix in _PROTECTED_NAME_PREFIXES):
        return True
    # The private per-user runtime directory, when it happens to fall inside
    # the workspace (source runs, AUTOYOU_TEST_ROOT-style relocations).
    return ".autoyou" in resolved.parts


def _assert_workspace_path_writable(resolved: Path) -> None:
    if _is_protected_runtime_path(resolved):
        raise ValueError(
            f"path is protected AutoYou runtime state and cannot be modified by an agent: "
            f"{resolved.name}"
        )


def _resolve_workspace_path(
    path: str, *, must_exist: bool = False, for_write: bool = False
) -> Path:
    if not path or not str(path).strip():
        raise ValueError("path is required")

    root = _workspace_root()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate

    resolved = candidate.resolve(strict=must_exist)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path escapes workspace root: {path}")
    if for_write:
        _assert_workspace_path_writable(resolved)
    return resolved


def _relative_path(path: Path) -> str:
    return str(path.relative_to(_workspace_root()))


def _is_probably_text_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        if path.stat().st_size > _MAX_TEXT_FILE_BYTES:
            return False
        with path.open("rb") as handle:
            sample = handle.read(8192)
        return b"\x00" not in sample
    except OSError:
        return False


def _iter_workspace_files(base: Path) -> Iterator[Path]:
    for current_root, dirnames, filenames in os.walk(base):
        current = Path(current_root)
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS)
        for filename in sorted(filenames):
            candidate = current / filename
            if _is_probably_text_file(candidate):
                yield candidate


def _render_diff(before: str, after: str, path_label: str) -> str:
    diff_lines = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"{path_label} (before)",
        tofile=f"{path_label} (after)",
        lineterm="",
    )
    return _truncate("\n".join(diff_lines))


def get_workspace_root() -> Dict[str, Any]:
    """Return the repository root visible to coding tools."""
    root = _workspace_root()
    return {
        "status": "success",
        "workspace_root": str(root),
    }


def list_workspace(path: str = ".", max_depth: int = 2, max_entries: int = 200) -> Dict[str, Any]:
    """List files and directories under a workspace path."""
    try:
        base = _resolve_workspace_path(path, must_exist=True)
        if not base.is_dir():
            return {"status": "error", "message": f"Not a directory: {path}"}

        max_depth = max(0, int(max_depth))
        max_entries = max(1, int(max_entries))
        entries: List[Dict[str, Any]] = []
        rendered: List[str] = []

        for current_root, dirnames, filenames in os.walk(base):
            current = Path(current_root)
            depth = len(current.relative_to(base).parts)
            if depth >= max_depth:
                dirnames[:] = []
            else:
                dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS)

            for dirname in dirnames:
                item = current / dirname
                rel = _relative_path(item)
                entries.append({"path": rel, "type": "directory"})
                rendered.append(f"{rel}/")
                if len(entries) >= max_entries:
                    return {
                        "status": "success",
                        "base_path": _relative_path(base),
                        "entries": entries,
                        "rendered": _truncate("\n".join(rendered)),
                        "truncated": True,
                    }

            for filename in sorted(filenames):
                item = current / filename
                rel = _relative_path(item)
                entries.append({"path": rel, "type": "file"})
                rendered.append(rel)
                if len(entries) >= max_entries:
                    return {
                        "status": "success",
                        "base_path": _relative_path(base),
                        "entries": entries,
                        "rendered": _truncate("\n".join(rendered)),
                        "truncated": True,
                    }

        return {
            "status": "success",
            "base_path": _relative_path(base),
            "entries": entries,
            "rendered": "\n".join(rendered),
            "truncated": False,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def search_workspace(
    pattern: str,
    path: str = ".",
    max_results: int = 100,
    use_regex: bool = False,
    case_sensitive: bool = False,
    context_lines: int = 0,
    file_glob: str = "",
    file_type: str = "",
) -> Dict[str, Any]:
    """Search text files in the workspace for a literal or regex pattern.

    Args:
        pattern: Literal string or regex to find.
        path: Directory to search under (relative or absolute within workspace).
        max_results: Maximum number of matching lines to return.
        use_regex: Treat pattern as a regular expression.
        case_sensitive: Case-sensitive matching (default: case-insensitive).
        context_lines: Number of surrounding lines to include before/after each match.
        file_glob: Glob pattern to restrict files (e.g. ``*.py``).
        file_type: Shortcut type filter (``py``, ``js``, ``ts``, ``html``, etc.).
    """
    try:
        if not pattern:
            return {"status": "error", "message": "pattern is required"}

        base = _resolve_workspace_path(path, must_exist=True)
        max_results = max(1, int(max_results))
        context_lines = max(0, int(context_lines))
        flags = 0 if case_sensitive else re.IGNORECASE
        compiled = re.compile(pattern if use_regex else re.escape(pattern), flags)

        glob_patterns: List[str] = []
        if file_type and file_type in _FILE_TYPE_GLOBS:
            glob_patterns = _FILE_TYPE_GLOBS[file_type]
        elif file_glob:
            glob_patterns = [file_glob]

        def _matches_glob(file_path: Path) -> bool:
            if not glob_patterns:
                return True
            name = file_path.name
            return any(file_path.match(g) or _match_glob_simple(name, g) for g in glob_patterns)

        results: List[Dict[str, Any]] = []
        rendered: List[str] = []

        for file_path in _iter_workspace_files(base):
            if not _matches_glob(file_path):
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            all_lines = text.splitlines()
            for line_number, line in enumerate(all_lines, start=1):
                if not compiled.search(line):
                    continue

                rel = _relative_path(file_path)
                entry: Dict[str, Any] = {
                    "path": rel,
                    "line": line_number,
                    "text": line.strip(),
                }
                if context_lines > 0:
                    before_start = max(0, line_number - 1 - context_lines)
                    after_end = min(len(all_lines), line_number + context_lines)
                    entry["before"] = all_lines[before_start : line_number - 1]
                    entry["after"] = all_lines[line_number:after_end]
                    ctx_block = "\n".join([
                        *(f"{before_start + i + 1}: {l}" for i, l in enumerate(entry["before"])),
                        f"{line_number}: {line.rstrip()} <<<",
                        *(f"{line_number + i + 1}: {l}" for i, l in enumerate(entry["after"])),
                    ])
                    rendered.append(f"-- {rel}:{line_number} --\n{ctx_block}")
                else:
                    rendered.append(f"{rel}:{line_number}: {line.strip()}")

                results.append(entry)
                if len(results) >= max_results:
                    return {
                        "status": "success",
                        "pattern": pattern,
                        "results": results,
                        "rendered": _truncate("\n".join(rendered)),
                        "truncated": True,
                    }

        return {
            "status": "success",
            "pattern": pattern,
            "results": results,
            "rendered": "\n".join(rendered),
            "truncated": False,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _match_glob_simple(name: str, pattern: str) -> bool:
    """Minimal fnmatch-like match without importing fnmatch (already in stdlib)."""
    import fnmatch
    return fnmatch.fnmatch(name, pattern)


def read_file(
    path: str,
    start_line: int = 1,
    end_line: int = 250,
    offset: Optional[int] = None,
) -> Dict[str, Any]:
    """Read a text file from the workspace with numbered lines.

    Args:
        path: File path (relative to workspace root or absolute within it).
        start_line: First line to read (1-indexed). Ignored when ``offset`` is set.
        end_line: Last line to read (inclusive). Window size is ``end_line - start_line``.
        offset: When set, overrides ``start_line`` - useful for continuation reads
                (pass the ``next_start_line`` returned from a prior call).
    """
    try:
        file_path = _resolve_workspace_path(path, must_exist=True)
        if not file_path.is_file():
            return {"status": "error", "message": f"Not a file: {path}"}

        suffix = file_path.suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            raw = file_path.read_bytes()
            mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
                ".svg": "image/svg+xml",
                ".ico": "image/x-icon",
            }.get(suffix, "image/octet-stream")
            b64 = base64.b64encode(raw).decode()
            return {
                "status": "success",
                "path": _relative_path(file_path),
                "image": True,
                "mime_type": mime,
                "data_uri": f"data:{mime};base64,{b64}",
                "size_bytes": len(raw),
            }

        if suffix in _BINARY_EXTENSIONS:
            return {
                "status": "error",
                "message": f"Binary file not readable as text: {path}",
                "binary": True,
            }

        text = file_path.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        total = len(all_lines)

        if offset is not None:
            start_line = max(1, int(offset))
        else:
            start_line = max(1, int(start_line))

        window = max(1, int(end_line) - int(start_line) + 1) if offset is None else max(1, int(end_line) - start_line + 1)
        end_line_actual = min(total, start_line - 1 + window)
        selected = all_lines[start_line - 1 : end_line_actual]
        numbered = "\n".join(
            f"{line_no:>6}\t{line}"
            for line_no, line in enumerate(selected, start=start_line)
        )

        has_more = end_line_actual < total
        result: Dict[str, Any] = {
            "status": "success",
            "path": _relative_path(file_path),
            "start_line": start_line,
            "end_line": end_line_actual,
            "total_lines": total,
            "has_more": has_more,
            "content": "\n".join(selected),
            "numbered_content": numbered,
        }
        if has_more:
            result["next_start_line"] = end_line_actual + 1
            result["continuation_hint"] = (
                f"File has {total - end_line_actual} more lines. "
                f"Call read_file with offset={end_line_actual + 1} to continue."
            )
        return result
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def write_file(path: str, content: str, create_dirs: bool = False) -> Dict[str, Any]:
    """Write a full text file under the workspace root."""
    try:
        target = _resolve_workspace_path(path, must_exist=False, for_write=True)
        if target.exists() and target.is_dir():
            return {"status": "error", "message": f"Path is a directory: {path}"}

        parent = target.parent
        if not parent.exists():
            if not create_dirs:
                return {
                    "status": "error",
                    "message": f"Parent directory does not exist: {parent}",
                }
            parent.mkdir(parents=True, exist_ok=True)

        created = not target.exists()
        target.write_text(content, encoding="utf-8")
        return {
            "status": "success",
            "path": _relative_path(target),
            "created": created,
            "line_count": len(content.splitlines()),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def replace_text(
    path: str,
    old_text: str,
    new_text: str,
    expected_replacements: int = 1,
) -> Dict[str, Any]:
    """Replace exact text in a workspace file."""
    try:
        if old_text == "":
            return {"status": "error", "message": "old_text must not be empty"}

        file_path = _resolve_workspace_path(path, must_exist=True, for_write=True)
        before = file_path.read_text(encoding="utf-8", errors="replace")
        actual = before.count(old_text)
        expected = int(expected_replacements)
        if actual == 0:
            return {
                "status": "error",
                "message": f"old_text was not found in {path}",
            }
        if expected > 0 and actual != expected:
            return {
                "status": "error",
                "message": (
                    f"Expected {expected} replacement(s) in {path}, found {actual}. "
                    "Refine the target text before editing."
                ),
            }

        after = before.replace(old_text, new_text)
        file_path.write_text(after, encoding="utf-8")
        return {
            "status": "success",
            "path": _relative_path(file_path),
            "replacements": actual,
            "diff": _render_diff(before, after, _relative_path(file_path)),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def insert_before(path: str, marker: str, text_to_insert: str, occurrence: int = 1) -> Dict[str, Any]:
    """Insert text immediately before a marker in a workspace file."""
    try:
        file_path = _resolve_workspace_path(path, must_exist=True, for_write=True)
        before = file_path.read_text(encoding="utf-8", errors="replace")
        matches = list(re.finditer(re.escape(marker), before))
        target_occurrence = int(occurrence)
        if target_occurrence < 1 or target_occurrence > len(matches):
            return {
                "status": "error",
                "message": f"Marker occurrence {target_occurrence} not found in {path}",
            }

        insert_at = matches[target_occurrence - 1].start()
        after = before[:insert_at] + text_to_insert + before[insert_at:]
        file_path.write_text(after, encoding="utf-8")
        return {
            "status": "success",
            "path": _relative_path(file_path),
            "occurrence": target_occurrence,
            "diff": _render_diff(before, after, _relative_path(file_path)),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def insert_after(path: str, marker: str, text_to_insert: str, occurrence: int = 1) -> Dict[str, Any]:
    """Insert text immediately after a marker in a workspace file."""
    try:
        file_path = _resolve_workspace_path(path, must_exist=True, for_write=True)
        before = file_path.read_text(encoding="utf-8", errors="replace")
        matches = list(re.finditer(re.escape(marker), before))
        target_occurrence = int(occurrence)
        if target_occurrence < 1 or target_occurrence > len(matches):
            return {
                "status": "error",
                "message": f"Marker occurrence {target_occurrence} not found in {path}",
            }

        insert_at = matches[target_occurrence - 1].end()
        after = before[:insert_at] + text_to_insert + before[insert_at:]
        file_path.write_text(after, encoding="utf-8")
        return {
            "status": "success",
            "path": _relative_path(file_path),
            "occurrence": target_occurrence,
            "diff": _render_diff(before, after, _relative_path(file_path)),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def create_directory(path: str) -> Dict[str, Any]:
    """Create a directory under the workspace root."""
    try:
        target = _resolve_workspace_path(path, must_exist=False, for_write=True)
        target.mkdir(parents=True, exist_ok=True)
        return {
            "status": "success",
            "path": _relative_path(target),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def move_path(source_path: str, destination_path: str, overwrite: bool = False) -> Dict[str, Any]:
    """Move or rename a file or directory inside the workspace."""
    try:
        source = _resolve_workspace_path(source_path, must_exist=True, for_write=True)
        destination = _resolve_workspace_path(destination_path, must_exist=False, for_write=True)
        if destination.exists():
            if not overwrite:
                return {
                    "status": "error",
                    "message": f"Destination already exists: {destination_path}",
                }
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return {
            "status": "success",
            "source_path": _relative_path(source),
            "destination_path": _relative_path(destination),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def delete_path(path: str, recursive: bool = False, confirm: bool = False) -> Dict[str, Any]:
    """Delete a file or directory under the workspace root."""
    try:
        if not confirm:
            return {
                "status": "error",
                "message": "Deletion requires confirm=true after explicit user approval.",
            }

        target = _resolve_workspace_path(path, must_exist=True, for_write=True)
        root = _workspace_root()
        if target == root:
            return {"status": "error", "message": "Refusing to delete workspace root"}

        rel = _relative_path(target)
        if target.is_dir():
            if not recursive:
                return {
                    "status": "error",
                    "message": "Directory deletion requires recursive=true.",
                }
            shutil.rmtree(target)
        else:
            target.unlink()

        return {"status": "success", "path": rel}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def run_command(command: str, cwd: str = ".", timeout_seconds: int = 120) -> Dict[str, Any]:
    """Run a non-destructive shell command inside the workspace.

    Uses a rolling line buffer so large outputs are captured without OOM risk.
    On timeout, returns whatever partial output was collected.
    """
    try:
        if not command or not command.strip():
            return {"status": "error", "message": "command is required"}

        lowered = command.lower()
        for pattern in _DANGEROUS_COMMAND_PATTERNS:
            if re.search(pattern, lowered):
                return {
                    "status": "error",
                    "message": "Command blocked by safety policy. Use dedicated file tools instead.",
                }

        if os.environ.get(_RUN_COMMAND_OPT_IN_ENV, "").strip().lower() not in {"1", "true", "yes", "on"}:
            return {
                "status": "error",
                "message": (
                    "run_command is disabled by default for release safety. "
                    f"Set {_RUN_COMMAND_OPT_IN_ENV}=1 to explicitly allow agent shell commands."
                ),
            }

        cwd_path = _resolve_workspace_path(cwd, must_exist=True)
        timeout = max(1, int(timeout_seconds))
        env = _scrubbed_subprocess_env()

        stdout_buf: Deque[str] = collections.deque(maxlen=_MAX_RUN_OUTPUT_LINES)
        stderr_buf: Deque[str] = collections.deque(maxlen=_MAX_RUN_OUTPUT_LINES)
        stdout_overflow = [0]
        stderr_overflow = [0]

        if platform.system() == "Windows":
            argv = ["cmd", "/c", command]
        else:
            argv = ["/bin/sh", "-c", command]

        proc = subprocess.Popen(
            argv,
            cwd=str(cwd_path),
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        def _drain(stream: io.TextIOWrapper, buf: Deque[str], overflow: List[int]) -> None:
            for raw_line in stream:
                line = raw_line.rstrip("\n")
                if len(buf) >= buf.maxlen:  # type: ignore[arg-type]
                    overflow[0] += 1
                buf.append(line)

        t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_buf, stdout_overflow), daemon=True)
        t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_buf, stderr_overflow), daemon=True)
        t_out.start()
        t_err.start()

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                proc.kill()
            except OSError:
                pass

        t_out.join(timeout=5)
        t_err.join(timeout=5)

        def _buf_to_str(buf: Deque[str], overflow: int) -> str:
            lines = list(buf)
            text = "\n".join(lines)
            if overflow:
                text = f"[{overflow} earlier lines omitted]\n" + text
            return _truncate(text)

        exit_code = proc.returncode
        stdout_text = _buf_to_str(stdout_buf, stdout_overflow[0])
        stderr_text = _buf_to_str(stderr_buf, stderr_overflow[0])

        if timed_out:
            return {
                "status": "error",
                "message": f"Command timed out after {timeout} seconds (partial output captured)",
                "command": command,
                "cwd": _relative_path(cwd_path),
                "exit_code": None,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "timed_out": True,
            }

        return {
            "status": "success" if exit_code == 0 else "error",
            "command": command,
            "cwd": _relative_path(cwd_path),
            "exit_code": exit_code,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "timed_out": False,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def git_status() -> Dict[str, Any]:
    """Return `git status --short --branch` for the workspace."""
    try:
        completed = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=str(_workspace_root()),
            capture_output=True,
            text=True,
            timeout=120,
            env=_scrubbed_subprocess_env(),
        )
        return {
            "status": "success" if completed.returncode == 0 else "error",
            "stdout": _truncate(completed.stdout),
            "stderr": _truncate(completed.stderr),
            "exit_code": completed.returncode,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def git_diff(path: str = "", staged: bool = False) -> Dict[str, Any]:
    """Return a git diff for the workspace or a specific file."""
    try:
        base_cmd = ["git", "diff"]
        if staged:
            base_cmd.append("--staged")
        if path:
            target = _resolve_workspace_path(path, must_exist=False)
            rel = _relative_path(target)
            base_cmd.extend(["--", rel])
        completed = subprocess.run(
            base_cmd,
            cwd=str(_workspace_root()),
            capture_output=True,
            text=True,
            timeout=120,
            env=_scrubbed_subprocess_env(),
        )
        return {
            "status": "success" if completed.returncode == 0 else "error",
            "path": path or ".",
            "staged": staged,
            "stdout": _truncate(completed.stdout),
            "stderr": _truncate(completed.stderr),
            "exit_code": completed.returncode,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
