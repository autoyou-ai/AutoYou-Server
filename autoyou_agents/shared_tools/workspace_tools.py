# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-5b3d5342f11e89e1eb3c6842

"""Workspace editing tools for repo-aware coding agents.

These tools are intentionally scoped to the AutoYou repository root so an ADK
sub-agent can inspect, edit, and verify code without escaping the project.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import ast
import base64
import collections
import difflib
import fnmatch
from functools import wraps
import io
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Deque, Iterator, List, Optional

from ._subprocess_env import (
    _SENSITIVE_ENV_EXACT,
    _SENSITIVE_ENV_SUBSTRINGS,
    scrubbed_subprocess_env as _scrubbed_subprocess_env,
)

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-5b3d5342f11e89e1eb3c6842"


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
    "coding_workspace_rag",
    "dist",
    "game_package_cache",
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

_WORKSPACE_MUTATION_LOCK = threading.RLock()


def _serialized_workspace_write(function):
    @wraps(function)
    def serialized(*args, **kwargs):
        with _WORKSPACE_MUTATION_LOCK:
            return function(*args, **kwargs)
    return serialized


def _workspace_root() -> Path:
    configured = os.environ.get("AUTOYOU_WORKSPACE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    from shared.platform_runtime import get_mutable_data_dir, is_compiled

    if is_compiled():
        ws = (get_mutable_data_dir("AutoYou", anchor=__file__) / "workspace").resolve()
        ws.mkdir(parents=True, exist_ok=True)
        return ws
    return _DEFAULT_WORKSPACE_ROOT.resolve()


def _extra_compiled_workspace_roots() -> tuple[Path, ...]:
    """Writable AutoYou-owned subtrees reachable when no explicit AUTOYOU_WORKSPACE_ROOT is pinned."""
    if os.environ.get("AUTOYOU_WORKSPACE_ROOT"):
        return ()
    try:
        from shared.platform_runtime import (
            get_dynamic_agents_root,
            get_mutable_data_dir,
            get_service_data_dir,
            is_compiled,
        )

        anchor = _DEFAULT_WORKSPACE_ROOT / "server.py"
        mutable = get_mutable_data_dir("AutoYou", anchor=anchor)
        candidates: list[Path] = [
            get_service_data_dir("game_projects", anchor=anchor),
            mutable / "game_projects",
            mutable / "mobile_games",
        ]
        if is_compiled():
            candidates.insert(0, get_dynamic_agents_root("AutoYou", anchor=anchor))
        roots: list[Path] = []
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in roots:
                roots.append(resolved)
        return tuple(roots)
    except Exception:
        return ()


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS, *, keep_tail: bool = False) -> str:
    if len(text) <= limit:
        return text
    extra = len(text) - limit
    if keep_tail and limit >= 400:
        head_len = int(limit * 0.45)
        tail_len = limit - head_len
        return (
            text[:head_len]
            + f"\n... [omitted {extra} middle chars] ...\n"
            + text[-tail_len:]
        )
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
    "jailbreak_root_prompt.sig",
    "jailbreak_signing.key",
    "server_unlock.json",
    "ai_agent_internal_api_token.txt",
    "config.encrypted",
    "sessions.db",
})
_PROTECTED_SUFFIXES = (".keystore.enc", ".key-mode")
# from __debug_provenance_a__ import schedule
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


def _is_within_allowed_roots(resolved: Path, root: Path) -> bool:
    if resolved == root or root in resolved.parents:
        return True
    for extra in _extra_compiled_workspace_roots():
        if resolved == extra or extra in resolved.parents:
            return True
    return False


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
    if not _is_within_allowed_roots(resolved, root):
        raise ValueError(f"path escapes workspace root: {path}")
    if for_write:
        _assert_workspace_path_writable(resolved)
    return resolved


def _relative_path(path: Path) -> str:
    root = _workspace_root()
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


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
    if base.is_file():
        if _is_probably_text_file(base):
            yield base
        return
    for current_root, dirnames, filenames in os.walk(base):
        current = Path(current_root)
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS)
        for filename in sorted(filenames):
            candidate = current / filename
            if not _is_within_allowed_roots(candidate.resolve(), _workspace_root()):
                continue
            if _is_probably_text_file(candidate):
                yield candidate


def _literal_search_candidates(base: Path, pattern: str, case_sensitive: bool) -> Optional[List[Path]]:
    """Use ripgrep to shortlist matching files; retain Python regex semantics."""
    executable = shutil.which("rg")
    if not executable or "\n" in pattern or "\r" in pattern:
        return None
    if not case_sensitive and not pattern.isascii():
        return None  # Python and Rust have different Unicode case-fold rules.
    from shared.process_lifecycle import process_spawn_kwargs

    folder = base if base.is_dir() else base.parent
    command = [executable, "--no-config", "--files-with-matches", "--null",
               "--hidden", "--no-ignore", "--text", "--max-filesize", str(_MAX_TEXT_FILE_BYTES)]
    rg_pattern = pattern
    if not case_sensitive:
        command.append("--ignore-case")
        # Python IGNORECASE additionally matches these four Unicode characters
        # for ASCII I/K/S. Keep the shortlist a superset of Python's matches.
        equivalents = {"i": "[iI\u0130\u0131]", "k": "[kK\u212a]", "s": "[sS\u017f]"}
        rg_pattern = "".join(equivalents.get(char.lower(), re.escape(char)) for char in pattern)
    else:
        command.append("--fixed-strings")
    for ignored in sorted(_IGNORED_DIRS):
        command.extend(["--glob", f"!**/{ignored}/**"])
    command.extend(["--", rg_pattern, "." if base.is_dir() else base.name])
    try:
        result = subprocess.run(command, cwd=folder, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=10,
                                env=_scrubbed_subprocess_env(),
                                **process_spawn_kwargs(hide_window=True))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode not in (0, 1):
        return None
    root = _workspace_root()
    candidates = []
    for name in result.stdout.decode("utf-8", errors="replace").split("\0"):
        if not name:
            continue
        candidate = (folder / name).resolve()
        if _is_within_allowed_roots(candidate, root) and _is_probably_text_file(candidate):
            candidates.append(candidate)
    return sorted(set(candidates))


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
        path: File or directory to search (relative or absolute within workspace).
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

        # Regex searches keep Python's existing syntax, including lookarounds.
        # Missing or incompatible ripgrep falls back to the portable scanner.
        candidates = None if use_regex else _literal_search_candidates(base, pattern, case_sensitive)
        for file_path in candidates if candidates is not None else _iter_workspace_files(base):
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
        end_line: Last line to read (inclusive). Window size is ``end_line - start_line + 1``.
        offset: When set, overrides ``start_line`` - useful for continuation reads
                (pass the ``next_start_line`` returned from a prior call).
                The requested window size is preserved at the new offset.
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

        # An offset moves the requested window, rather than shrinking it to a
        # single line when the next page begins beyond the original end_line.
        window = max(1, int(end_line) - max(1, int(start_line)) + 1)
        if offset is not None:
            start_line = max(1, int(offset))
        else:
            start_line = max(1, int(start_line))

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


@_serialized_workspace_write
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
        _atomic_write_bytes(target, content.encode("utf-8"))
        return {
            "status": "success",
            "path": _relative_path(target),
            "created": created,
            "line_count": len(content.splitlines()),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _nearest_match_hints(content: str, needle: str, max_hints: int = 3) -> List[Dict[str, Any]]:
    """Return closest line windows in `content` to help recover from a missed edit."""
    needle_lines = [line.strip() for line in str(needle or "").splitlines() if line.strip()]
    if not needle_lines:
        return []
    anchor = needle_lines[0]
    all_lines = content.splitlines()
    scored: List[tuple[float, int]] = []
    for idx, line in enumerate(all_lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        ratio = difflib.SequenceMatcher(None, anchor[:120], stripped[:120]).ratio()
        if anchor in stripped or stripped in anchor:
            ratio = max(ratio, 0.85)
        if ratio >= 0.55:
            scored.append((ratio, idx))
    scored.sort(key=lambda item: (-item[0], item[1]))
    hints: List[Dict[str, Any]] = []
    used: set[int] = set()
    for ratio, line_no in scored:
        if any(abs(line_no - prev) <= 3 for prev in used):
            continue
        used.add(line_no)
        start = max(1, line_no - 2)
        end = min(len(all_lines), line_no + max(2, len(needle_lines) + 1))
        snippet = "\n".join(f"{n}: {all_lines[n - 1]}" for n in range(start, end + 1))
        hints.append({"line": line_no, "similarity": round(ratio, 2), "snippet": snippet[:500]})
        if len(hints) >= max_hints:
            break
    return hints


def _replace_with_whitespace_tolerance(
    content: str,
    old_text: str,
    new_text: str,
) -> Optional[tuple[str, int]]:
    """Unique line-trimmed match fallback when exact whitespace/CRLF differs slightly."""
    old_lines = old_text.splitlines()
    if not old_lines:
        return None
    normalized_old = [line.strip() for line in old_lines]
    file_lines = content.splitlines(keepends=True)
    file_stripped = [line.strip() for line in file_lines]
    window = len(normalized_old)
    hits: List[int] = []
    for start in range(0, len(file_stripped) - window + 1):
        if file_stripped[start : start + window] == normalized_old:
            hits.append(start)
    if len(hits) != 1:
        return None
    pos = hits[0]
    line_ending = "\r\n" if "\r\n" in content else "\n"
    trailing_nl = file_lines[pos + window - 1].endswith(("\n", "\r\n"))
    replacement_text = new_text.replace("\r\n", "\n").replace("\n", line_ending)
    if trailing_nl and not replacement_text.endswith(("\n", "\r\n")):
        replacement_text += line_ending
    updated = "".join(file_lines[:pos]) + replacement_text + "".join(file_lines[pos + window :])
    return updated, 1


@_serialized_workspace_write
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
        before = file_path.read_bytes().decode("utf-8")
        actual = before.count(old_text)
        expected = int(expected_replacements)
        if actual == 0:
            hints = _nearest_match_hints(before, old_text)
            payload: Dict[str, Any] = {
                "status": "error",
                "message": f"old_text was not found in {path}",
            }
            if hints:
                payload["closest_matches"] = hints
                payload["hint"] = (
                    "Inspect the closest_matches line numbers with read_file or retry with exact text "
                    "(or use multi_edit_file / apply_patch)."
                )
            return payload
        if expected > 0 and actual != expected:
            return {
                "status": "error",
                "message": (
                    f"Expected {expected} replacement(s) in {path}, found {actual}. "
                    "Refine the target text before editing."
                ),
            }

        after = before.replace(old_text, new_text)
        _atomic_write_bytes(file_path, after.encode("utf-8"))
        return {
            "status": "success",
            "path": _relative_path(file_path),
            "replacements": actual,
            "diff": _render_diff(before, after, _relative_path(file_path)),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@_serialized_workspace_write
def insert_before(path: str, marker: str, text_to_insert: str, occurrence: int = 1) -> Dict[str, Any]:
    """Insert text immediately before a marker in a workspace file."""
    try:
        file_path = _resolve_workspace_path(path, must_exist=True, for_write=True)
        before = file_path.read_bytes().decode("utf-8")
        matches = list(re.finditer(re.escape(marker), before))
        target_occurrence = int(occurrence)
        if target_occurrence < 1 or target_occurrence > len(matches):
            return {
                "status": "error",
                "message": f"Marker occurrence {target_occurrence} not found in {path}",
            }

        insert_at = matches[target_occurrence - 1].start()
        after = before[:insert_at] + text_to_insert + before[insert_at:]
        _atomic_write_bytes(file_path, after.encode("utf-8"))
        return {
            "status": "success",
            "path": _relative_path(file_path),
            "occurrence": target_occurrence,
            "diff": _render_diff(before, after, _relative_path(file_path)),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@_serialized_workspace_write
def insert_after(path: str, marker: str, text_to_insert: str, occurrence: int = 1) -> Dict[str, Any]:
    """Insert text immediately after a marker in a workspace file."""
    try:
        file_path = _resolve_workspace_path(path, must_exist=True, for_write=True)
        before = file_path.read_bytes().decode("utf-8")
        matches = list(re.finditer(re.escape(marker), before))
        target_occurrence = int(occurrence)
        if target_occurrence < 1 or target_occurrence > len(matches):
            return {
                "status": "error",
                "message": f"Marker occurrence {target_occurrence} not found in {path}",
            }

        insert_at = matches[target_occurrence - 1].end()
        after = before[:insert_at] + text_to_insert + before[insert_at:]
        _atomic_write_bytes(file_path, after.encode("utf-8"))
        return {
            "status": "success",
            "path": _relative_path(file_path),
            "occurrence": target_occurrence,
            "diff": _render_diff(before, after, _relative_path(file_path)),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@_serialized_workspace_write
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


@_serialized_workspace_write
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


@_serialized_workspace_write
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
    return _run_command_with_cancel(command, cwd, timeout_seconds)


def _run_command_with_cancel(
    command: str, cwd: str = ".", timeout_seconds: int = 120,
    cancel_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    """Same command policy, with cancellation for an owning coding subprocess."""
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
        timeout = max(0, int(timeout_seconds))
        env = _scrubbed_subprocess_env()

        stdout_buf: Deque[str] = collections.deque(maxlen=_MAX_RUN_OUTPUT_LINES)
        stderr_buf: Deque[str] = collections.deque(maxlen=_MAX_RUN_OUTPUT_LINES)
        stdout_overflow = [0]
        stderr_overflow = [0]

        if platform.system() == "Windows":
            # One command line, passed verbatim: a list would go through
            # list2cmdline, whose backslash-escaped quotes cmd.exe does not
            # understand, so `node "file.mjs"` reached node with the quotes.
            # /s keeps the command's own quotes; /d skips AutoRun commands.
            argv = f'cmd /d /s /c "{command}"'
        else:
            argv = ["/bin/sh", "-c", command]

        from shared.process_lifecycle import process_spawn_kwargs, force_kill_process_tree
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd_path),
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **process_spawn_kwargs(hide_window=True),
        )

        def _drain(stream: io.TextIOWrapper, buf: Deque[str], overflow: List[int]) -> None:
            while raw_line := stream.readline(_MAX_OUTPUT_CHARS):
                line = raw_line.rstrip("\n")
                if len(buf) >= buf.maxlen:  # type: ignore[arg-type]
                    overflow[0] += 1
                buf.append(line)

        t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_buf, stdout_overflow), daemon=True)
        t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_buf, stderr_overflow), daemon=True)
        t_out.start()
        t_err.start()

        timed_out = False
        cancelled = False
        try:
            if cancel_event is None:
                proc.wait(timeout=timeout if timeout > 0 else None)
            else:
                deadline = time.monotonic() + timeout if timeout > 0 else None
                while proc.poll() is None:
                    if cancel_event.wait(timeout=min(0.2, max(0.0, deadline - time.monotonic())) if deadline else 0.2):
                        cancelled = True
                        force_kill_process_tree(proc.pid, process_group=True)
                        proc.wait(timeout=3)
                        break
                    if deadline is not None and time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(argv, timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                force_kill_process_tree(proc.pid, process_group=True)
                proc.wait(timeout=3)
            except OSError:
                pass

        t_out.join(timeout=5)
        t_err.join(timeout=5)
        if t_out.is_alive() or t_err.is_alive():
            force_kill_process_tree(proc.pid, process_group=True)
            t_out.join(timeout=3)
            t_err.join(timeout=3)

        def _buf_to_str(buf: Deque[str], overflow: int) -> str:
            lines = list(buf)
            text = "\n".join(lines)
            if overflow:
                text = f"[{overflow} earlier lines omitted]\n" + text
            return _truncate(text, keep_tail=True)

        exit_code = proc.returncode
        stdout_text = _buf_to_str(stdout_buf, stdout_overflow[0])
        stderr_text = _buf_to_str(stderr_buf, stderr_overflow[0])

        if timed_out or cancelled:
            return {
                "status": "error",
                "message": "Command cancelled when the coding turn ended (partial output captured)" if cancelled else f"Command timed out after {timeout} seconds (partial output captured)",
                "command": command,
                "cwd": _relative_path(cwd_path),
                "exit_code": None,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "timed_out": timed_out,
                "cancelled": cancelled,
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


def _parse_json_or_list(value: Any, *, field_name: str) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            if field_name == "paths_json" and not raw.startswith(("[", "{", '"')):
                return [raw]
            raise ValueError(f"{field_name} must be a JSON array or list: {exc}") from exc
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
        return [parsed]
    if isinstance(value, dict):
        return [value]
    raise ValueError(f"{field_name} must be a list or JSON-encoded list")


def find_files(
    pattern: str = "*",
    path: str = ".",
    max_results: int = 100,
) -> Dict[str, Any]:
    """Find workspace files matching a glob pattern (e.g. '*.py', '*game*.html')."""
    try:
        base = _resolve_workspace_path(path, must_exist=True)
        if not base.is_dir():
            return {"status": "error", "message": f"Not a directory: {path}"}
        glob_pattern = str(pattern or "*").strip() or "*"
        max_results = max(1, min(300, int(max_results)))
        root = _workspace_root()
        matches: List[str] = []
        truncated = False

        for current_root, dirnames, filenames in os.walk(base):
            current = Path(current_root)
            dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS)
            for filename in sorted(filenames):
                candidate = current / filename
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if not _is_within_allowed_roots(resolved, root):
                    continue
                rel = _relative_path(resolved).replace("\\", "/")
                if fnmatch.fnmatch(filename, glob_pattern) or fnmatch.fnmatch(rel, glob_pattern):
                    matches.append(rel)
                    if len(matches) >= max_results:
                        truncated = True
                        break
            if truncated:
                break

        return {
            "status": "success",
            "pattern": glob_pattern,
            "base_path": _relative_path(base),
            "count": len(matches),
            "files": matches,
            "rendered": _truncate("\n".join(matches)),
            "truncated": truncated,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def read_files_batch(requests_json: str) -> Dict[str, Any]:
    """Read targeted file slices in a single tool call, reporting every requested file.

    Args:
        requests_json: JSON array of objects, each with ``path`` and optional
            ``start_line`` (default 1) and ``end_line`` (default 120).
            Example: ``[{"path": "app.py", "start_line": 1, "end_line": 80}]``.
    """
    try:
        items = _parse_json_or_list(requests_json, field_name="requests_json")
        if not items:
            return {"status": "error", "message": "At least one file read request is required"}
        results: List[Dict[str, Any]] = []
        rendered_parts: List[str] = []
        for entry in items:
            if isinstance(entry, str):
                entry = {"path": entry}
            if not isinstance(entry, dict) or not entry.get("path"):
                results.append({"status": "error", "message": "Each entry must have a 'path'"})
                continue
            try:
                start = int(entry.get("start_line") or 1)
                end = int(entry.get("end_line") or (start + 119))
                offset = entry.get("offset")
                res = read_file(
                    str(entry["path"]), start_line=start, end_line=end,
                    offset=int(offset) if offset is not None else None,
                )
            except (TypeError, ValueError) as exc:
                res = {"status": "error", "path": str(entry["path"]), "message": str(exc)}
            results.append(res)
            if res.get("status") == "success":
                header = f"=== {res.get('path')} (L{res.get('start_line')}-L{res.get('end_line')}/{res.get('total_lines')}) ==="
                rendered_parts.append(f"{header}\n{res.get('numbered_content') or ''}")
            else:
                rendered_parts.append(f"=== {entry.get('path')} ERROR: {res.get('message')} ===")
        ok_count = sum(1 for r in results if r.get("status") == "success")
        return {
            "status": "success" if ok_count > 0 else "error",
            "read_count": ok_count,
            "total_requested": len(items),
            "results": results,
            "rendered": _truncate("\n\n".join(rendered_parts), limit=18_000),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@_serialized_workspace_write
def append_file(path: str, content: str, create_dirs: bool = False) -> Dict[str, Any]:
    """Append text to an existing workspace file (or create it if missing).

    Ideal for building large files (such as HTML5 games or multi-section apps)
    in clean sequential chunks without hitting model output token caps.
    """
    try:
        target = _resolve_workspace_path(path, must_exist=False, for_write=True)
        if target.exists() and target.is_dir():
            return {"status": "error", "message": f"Path is a directory: {path}"}
        parent = target.parent
        if not parent.exists():
            if not create_dirs:
                return {"status": "error", "message": f"Parent directory does not exist: {parent}"}
            parent.mkdir(parents=True, exist_ok=True)
        created = not target.exists()
        existing = target.read_bytes().decode("utf-8") if target.exists() else ""
        updated = existing + content
        _atomic_write_bytes(target, updated.encode("utf-8"))
        return {
            "status": "success",
            "path": _relative_path(target),
            "created": created,
            "appended_lines": len(content.splitlines()),
            "total_lines": len(updated.splitlines()),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@_serialized_workspace_write
def multi_edit_file(path: str, edits_json: str, allow_whitespace_fallback: bool = True) -> Dict[str, Any]:
    """Apply multiple sequential edits to one file atomically in a single tool call.

    Args:
        path: Workspace file path.
        edits_json: JSON array of ``{"old_text": "...", "new_text": "..."}`` objects.
        allow_whitespace_fallback: When exact match misses, retry with unique
            line-trimmed matching before failing.
    """
    try:
        edits = _parse_json_or_list(edits_json, field_name="edits_json")
        if not edits:
            return {"status": "error", "message": "edits_json must contain at least one edit"}
        file_path = _resolve_workspace_path(path, must_exist=True, for_write=True)
        before = file_path.read_bytes().decode("utf-8")
        current = before
        applied = 0
        fuzzy_used = 0

        for idx, edit in enumerate(edits, start=1):
            if not isinstance(edit, dict):
                return {"status": "error", "message": f"Edit #{idx} must be an object with old_text and new_text"}
            old_text = str(edit.get("old_text") or "")
            new_text = str(edit.get("new_text") if edit.get("new_text") is not None else "")
            if not old_text:
                return {"status": "error", "message": f"Edit #{idx} has empty old_text"}
            count = current.count(old_text)
            expected = int(edit.get("expected_replacements", 1))
            if expected < 0:
                return {"status": "error", "message": f"Edit #{idx}: expected_replacements cannot be negative"}
            if count == 0 and allow_whitespace_fallback:
                fallback = _replace_with_whitespace_tolerance(current, old_text, new_text)
                if fallback is not None:
                    current, replaced = fallback
                    if expected > 0 and replaced != expected:
                        return {"status": "error", "message": f"Edit #{idx}: whitespace match count differs from expected_replacements (no changes written)"}
                    applied += replaced
                    fuzzy_used += 1
                    continue
            if count == 0:
                hints = _nearest_match_hints(current, old_text)
                return {
                    "status": "error",
                    "message": f"Edit #{idx} failed: old_text not found in {path} (no changes written).",
                    "failed_edit_index": idx,
                    "closest_matches": hints,
                }
            if expected > 0 and count != expected:
                return {
                    "status": "error",
                    "message": (
                        f"Edit #{idx} failed: expected {expected} match(es) in {path}, found {count} "
                        "(no changes written)."
                    ),
                    "failed_edit_index": idx,
                }
            current = current.replace(old_text, new_text)
            applied += count

        _atomic_write_bytes(file_path, current.encode("utf-8"))
        return {
            "status": "success",
            "path": _relative_path(file_path),
            "edits_applied": len(edits),
            "replacements": applied,
            "whitespace_fallback_used": fuzzy_used,
            "diff": _render_diff(before, current, _relative_path(file_path)),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Replace a file only after the complete contents have reached disk."""
    mode = target.stat().st_mode if target.exists() else None
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as handle:
            temp_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp_name, mode)
        os.replace(temp_name, target)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _apply_hunk_lines(
    file_lines: List[str], hunk_lines: List[str], *, position: Optional[int] = None,
    minimum: int = 0, at_eof: bool = False,
) -> Optional[tuple[List[str], int]]:
    old = [line[1:] for line in hunk_lines if line.startswith((" ", "-"))]
    new = [line[1:] for line in hunk_lines if line.startswith((" ", "+"))]
    if not old:
        pos = position if position is not None else len(file_lines)
        if not minimum <= pos <= len(file_lines):
            return None
    else:
        width = len(old)
        if position is not None and minimum <= position <= len(file_lines) - width and file_lines[position:position + width] == old:
            pos = position
        else:
            hits = [i for i in range(minimum, len(file_lines) - width + 1)
                    if file_lines[i:i + width] == old and (not at_eof or i + width == len(file_lines))]
            if not hits:
                hits = [i for i in range(minimum, len(file_lines) - width + 1)
                        if [line.strip() for line in file_lines[i:i + width]] == [line.strip() for line in old]
                        and (not at_eof or i + width == len(file_lines))]
            if len(hits) != 1:
                return None
            pos = hits[0]
        if at_eof and pos + width != len(file_lines):
            return None
    return file_lines[:pos] + new + file_lines[pos + len(old):], pos + len(new)


def _parse_patch_operations(raw: str) -> List[Dict[str, Any]]:
    lines = raw.replace("\r\n", "\n").splitlines()
    ops: List[Dict[str, Any]] = []
    if lines and lines[0] == "*** Begin Patch":
        if lines[-1] != "*** End Patch":
            raise ValueError("Incomplete patch: missing *** End Patch")
        idx = 1
        while idx < len(lines) - 1:
            match = re.fullmatch(r"\*\*\* (Add|Update|Delete) File: (.+)", lines[idx])
            if not match:
                raise ValueError(f"Invalid patch operation: {lines[idx]}")
            kind, path = match.groups()
            op: Dict[str, Any] = {"kind": kind.lower(), "path": path.strip(), "hunks": [], "lines": []}
            idx += 1
            hunk = None
            while idx < len(lines) - 1 and not re.match(r"\*\*\* (?:Add|Update|Delete) File:", lines[idx]):
                line = lines[idx]
                if kind == "Add" and line.startswith("+"):
                    op["lines"].append(line[1:])
                elif kind == "Update" and line.startswith("@@"):
                    hunk = {"header": line, "lines": [], "eof": False}
                    op["hunks"].append(hunk)
                elif kind == "Update" and line == "*** End of File" and hunk is not None:
                    hunk["eof"] = True
                elif kind == "Update" and hunk is not None and (not line or line[0] in " +-" or line.startswith("\\ No newline")):
                    hunk["lines"].append(line or " ")
                else:
                    raise ValueError(f"Invalid {kind.lower()} patch line: {line}")
                idx += 1
            if kind == "Update" and not op["hunks"]:
                raise ValueError(f"No update hunks for {path}")
            ops.append(op)
    else:
        idx = 0
        while idx < len(lines):
            if not lines[idx].startswith("--- "):
                if lines[idx].startswith(("diff --git ", "index ", "new file mode ", "deleted file mode ")) or not lines[idx].strip():
                    idx += 1
                    continue
                raise ValueError(f"Unsupported unified diff line: {lines[idx]}")
            if idx + 1 >= len(lines) or not lines[idx + 1].startswith("+++ "):
                raise ValueError("Unified diff requires --- and +++ headers")
            old, new = [line[4:].split("\t", 1)[0].strip() for line in lines[idx:idx + 2]]
            def clean_path(path: str) -> str:
                if path.startswith('"'):
                    path = json.loads(path)
                return path[2:] if path.startswith(("a/", "b/")) else path
            old, new = clean_path(old), clean_path(new)
            if old != new and "/dev/null" not in {old, new}:
                raise ValueError("Rename diffs require move_path before editing")
            op = {"kind": "add" if old == "/dev/null" else "delete" if new == "/dev/null" else "update",
                  "path": new if new != "/dev/null" else old, "hunks": [], "lines": [], "unified": True}
            idx += 2
            while idx < len(lines) and lines[idx].startswith("@@"):
                match = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*", lines[idx])
                if not match:
                    raise ValueError(f"Invalid unified hunk: {lines[idx]}")
                old_start, old_count, new_start, new_count = [int(x) if x is not None else 1 for x in match.groups()]
                hunk = {"position": old_start if old_count == 0 else old_start - 1, "lines": [], "eof": False}
                idx += 1
                read_old = read_new = 0
                while read_old < old_count or read_new < new_count:
                    if idx >= len(lines) or not lines[idx] or lines[idx][0] not in " +-":
                        raise ValueError("Incomplete unified diff hunk")
                    line = lines[idx]
                    read_old += line[0] in " -"
                    read_new += line[0] in " +"
                    if read_old > old_count or read_new > new_count:
                        raise ValueError("Unified diff hunk length mismatch")
                    hunk["lines"].append(line)
                    idx += 1
                    if idx < len(lines) and lines[idx].startswith("\\ No newline"):
                        hunk["lines"].append(lines[idx])
                        idx += 1
                op["hunks"].append(hunk)
            if not op["hunks"]:
                raise ValueError("Unified diff contains no hunks")
            if op["kind"] == "add":
                if any(line.startswith((" ", "-")) for h in op["hunks"] for line in h["lines"]):
                    raise ValueError("Unified file addition cannot contain old lines")
                op["lines"] = [line[1:] for h in op["hunks"] for line in h["lines"] if line.startswith("+")]
            ops.append(op)
    if not ops:
        raise ValueError("Could not parse any file operations from patch_text")
    return ops


@_serialized_workspace_write
def apply_patch(patch_text: str, allow_delete: bool = False) -> Dict[str, Any]:
    """Apply a complete Codex or unified patch, rolling back files on a write failure.

    Adds never overwrite existing files. All paths and hunks are validated before
    mutation; repeated operations on one path must be combined into one update.
    """
    try:
        ops = _parse_patch_operations(str(patch_text or "").strip("\n\r"))
        staged = []
        seen = set()
        for op in ops:
            kind = op["kind"]
            target = _resolve_workspace_path(op["path"], must_exist=kind != "add", for_write=True)
            if target in seen:
                raise ValueError(f"Repeated patch operation for {op['path']}")
            seen.add(target)
            if target.exists() and not target.is_file():
                raise ValueError(f"Not a file: {op['path']}")
            if kind == "add" and target.exists():
                raise ValueError(f"File already exists: {op['path']}; use Update File")
            if kind == "delete" and not allow_delete:
                raise ValueError(f"Deleting {op['path']} requires allow_delete=true")
            original = target.read_bytes() if target.exists() else None
            before = original.decode("utf-8") if original is not None else ""
            newline = "\r\n" if "\r\n" in before else "\n"
            trailing = before.endswith("\n")
            after = None
            if kind == "add":
                trailing = bool(op["lines"])
                if op.get("unified") and op["hunks"][-1]["lines"][-1].startswith("\\ No newline"):
                    trailing = False
                after = ("\n".join(op["lines"]) + ("\n" if trailing else "")).encode("utf-8")
            elif kind == "update" or (kind == "delete" and op.get("unified")):
                current = before.splitlines()
                minimum = delta = 0
                for hunk in op["hunks"]:
                    old_len = sum(line.startswith((" ", "-")) for line in hunk["lines"])
                    new_len = sum(line.startswith((" ", "+")) for line in hunk["lines"])
                    position = hunk.get("position")
                    if position is not None:
                        position += delta
                    result = _apply_hunk_lines(current, hunk["lines"], position=position, minimum=minimum, at_eof=hunk["eof"])
                    if result is None:
                        raise ValueError(f"Patch hunk failed or was ambiguous in {op['path']} (no files written)")
                    previous_len = len(current)
                    current, minimum = result
                    if op.get("unified") and minimum - new_len + old_len == previous_len:
                        trailing = True
                    for index, line in enumerate(hunk["lines"]):
                        if line.startswith("\\ No newline") and index > 0 and hunk["lines"][index - 1].startswith((" ", "+")):
                            trailing = False
                    delta += new_len - old_len
                after = (newline.join(current) + (newline if trailing and current else "")).encode("utf-8")
                if kind == "delete":
                    if current:
                        raise ValueError(f"Unified deletion does not remove all content in {op['path']}")
                    after = None
            staged.append((target, original, after, kind))

        committed = []
        created_dirs = []
        try:
            for target, original, after, kind in staged:
                if (target.read_bytes() if target.exists() else None) != original:
                    raise ValueError(f"File changed while patch was being prepared: {target}")
                parent = target.parent
                missing = []
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                for directory in reversed(missing):
                    directory.mkdir()
                    created_dirs.append(directory)
                mode = target.stat().st_mode if original is not None else None
                if after is None:
                    target.unlink()
                else:
                    _atomic_write_bytes(target, after)
                committed.append((target, original, after, mode))
        except Exception as exc:
            rollback_errors = []
            for target, original, after, mode in reversed(committed):
                try:
                    if (target.read_bytes() if target.exists() else None) != after:
                        raise OSError("File changed after patch commit; refusing to overwrite another edit during rollback")
                    if original is None:
                        target.unlink()
                    else:
                        _atomic_write_bytes(target, original)
                        os.chmod(target, mode)
                except OSError as rollback_error:
                    rollback_errors.append(f"{target}: {rollback_error}")
            for directory in reversed(created_dirs):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            if rollback_errors:
                return {"status": "error", "message": str(exc), "rollback_errors": rollback_errors,
                        "message_detail": "Some original files could not be restored; inspect rollback_errors before retrying."}
            raise
        changes = [{"path": _relative_path(target), "action": {"add": "added", "update": "updated", "delete": "deleted"}[kind]}
                   for target, _, _, kind in staged]
        diffs = [_render_diff((before or b"").decode("utf-8"), (after or b"").decode("utf-8"), _relative_path(target))
                 for target, before, after, _ in staged]
        return {"status": "success", "files_changed": len(changes), "changes": changes, "diff": _truncate("\n".join(diffs))}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def verify_workspace_files(paths_json: str) -> Dict[str, Any]:
    """Verify syntax and AutoYou contracts for one or more workspace files WITHOUT needing run_command.

    Checks:
    - Python (.py): AST parse + compile syntax validation; for AutoYou agents checks
      AGENT_NAME / AGENT_INSTRUCTION / create_<name> export warnings.
    - JSON (.json): strict JSON parse; for `game.json` validates offline game structure;
      for `website/manifest.json` validates required keys.
    - HTML (.html/.htm): `<head>` tag presence; for agent websites checks `<base href="./">`;
      for offline games checks `AutoYouGame.quit()` contract and warns on external CDN links.
    - JavaScript (.js/.mjs/.cjs): read-only `node --check` syntax verification when Node is present.

    Args:
        paths_json: JSON array of relative workspace file paths (or a single path string).
    """
    try:
        raw_paths = _parse_json_or_list(paths_json, field_name="paths_json")
        if not raw_paths:
            return {"status": "error", "message": "Provide at least one file path to verify"}
        reports: List[Dict[str, Any]] = []
        all_ok = True
        node_exe = shutil.which("node")

        for raw_path in raw_paths:
            p_str = str(raw_path or "").strip()
            if not p_str:
                all_ok = False
                reports.append({"path": p_str, "ok": False, "errors": ["File path is empty"], "warnings": []})
                continue
            try:
                target = _resolve_workspace_path(p_str, must_exist=True, for_write=False)
            except Exception as exc:
                all_ok = False
                reports.append({"path": p_str, "ok": False, "errors": [str(exc)], "warnings": []})
                continue
            if not target.is_file():
                all_ok = False
                reports.append({"path": p_str, "ok": False, "errors": ["Path is not a file"], "warnings": []})
                continue

            rel = _relative_path(target).replace("\\", "/")
            suffix = target.suffix.lower()
            errors: List[str] = []
            warnings: List[str] = []
            text = target.read_text(encoding="utf-8", errors="replace")

            if suffix == ".py":
                try:
                    tree = ast.parse(text, filename=rel)
                    compile(tree, rel, "exec")
                except SyntaxError as exc:
                    errors.append(f"SyntaxError at line {exc.lineno}: {exc.msg}")
                else:
                    if target.name == "prompt.py" and "autoyou_agents/" in rel:
                        defined = {
                            t.id for node in tree.body if isinstance(node, ast.Assign)
                            for t in node.targets if isinstance(t, ast.Name)
                        }
                        for required_var in ("AGENT_NAME", "AGENT_DESCRIPTION", "AGENT_INSTRUCTION"):
                            if required_var not in defined:
                                warnings.append(f"Missing standard prompt export: {required_var}")
                    if target.name == "agent.py" and "autoyou_agents/" in rel and target.parent.name.endswith("_agent"):
                        expected_factory = f"create_{target.parent.name}"
                        fn_names = {
                            node.name for node in tree.body
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        }
                        if expected_factory not in fn_names:
                            warnings.append(f"Expected factory function '{expected_factory}' not found at module level")
            elif suffix == ".json":
                try:
                    data = json.loads(text)
                except ValueError as exc:
                    errors.append(f"Invalid JSON: {exc}")
                else:
                    if target.name == "game.json" and not isinstance(data, dict):
                        errors.append("game.json must contain an object")
                    elif target.name == "game.json":
                        for key in ("id", "title", "version", "files"):
                            if not data.get(key):
                                errors.append(f"game.json missing required field '{key}'")
                        try:
                            from shared import game_package

                            entry = str(data.get("entrypoint") or "index.html")
                            if data.get("id") and not game_package.GAME_ID.fullmatch(str(data["id"])):
                                errors.append("game.json 'id' must match [a-z0-9-]{1,64}")
                            files_list = data.get("files") if isinstance(data.get("files"), list) else []
                            if entry not in files_list:
                                errors.append(f"game.json entrypoint '{entry}' must be listed in 'files'")
                            for item in files_list:
                                game_package.safe_path(item)
                                asset = (target.parent / item).resolve()
                                if not asset.is_relative_to(target.parent) or (target.parent / item).is_symlink():
                                    errors.append(f"Declared game asset is linked or outside the project: {item}")
                                    continue
                                if not asset.is_file():
                                    errors.append(f"Declared game asset missing: {item}")
                            if not errors and files_list and all((target.parent / str(f)).is_file() for f in files_list):
                                file_records = [{"path": str(f), "size": (target.parent / str(f)).stat().st_size} for f in files_list]
                                if not game_package.folder_supports_quit(target.parent, file_records):
                                    errors.append(game_package.QUIT_REQUIREMENT)
                        except Exception as exc:
                            errors.append(f"Invalid game manifest or assets: {exc}")
                    elif target.name == "manifest.json" and "website" in target.parts and isinstance(data, dict):
                        for key in ("agent_name", "title"):
                            if not data.get(key):
                                warnings.append(f"website manifest.json missing '{key}'")
            elif suffix in {".html", ".htm"}:
                if not re.search(r"<head(?=[\s>])[^>]*>", text, re.IGNORECASE):
                    errors.append("HTML document is missing a <head> element")
                if "website" in target.parts and "frontend" in target.parts:
                    if '<base href="./">' not in text and "<base href='./'>" not in text:
                        warnings.append('Agent website HTML should include <base href="./"> for /agent/<name>/ proxy routing')
                is_game_html = (
                    "games" in target.parts
                    or "mobile_games" in target.parts
                    or (target.parent / "game.json").is_file()
                    or "AutoYouGame" in text
                )
                if is_game_html:
                    try:
                        from shared import game_package

                        if not game_package.supports_quit(text):
                            errors.append(game_package.QUIT_REQUIREMENT)
                    except Exception:
                        pass
                    if re.search(r"(?:src|href)\s*=\s*[\"']https?://", text, re.IGNORECASE):
                        warnings.append("Offline game HTML references an external http(s) URL; offline games must bundle all assets locally")
            elif suffix in {".js", ".mjs", ".cjs"}:
                if node_exe:
                    try:
                        from shared.process_lifecycle import process_spawn_kwargs

                        proc = subprocess.run(
                            [node_exe, "--check", str(target)],
                            capture_output=True,
                            text=True,
                            timeout=10,
                            env=_scrubbed_subprocess_env(),
                            **process_spawn_kwargs(hide_window=True),
                        )
                        if proc.returncode != 0:
                            errors.append(f"Node syntax check failed: {(proc.stderr or proc.stdout).strip()[:400]}")
                    except Exception as exc:
                        warnings.append(f"Node syntax check unavailable: {exc}")

            file_ok = len(errors) == 0
            if not file_ok:
                all_ok = False
            reports.append({"path": rel, "ok": file_ok, "errors": errors, "warnings": warnings})

        return {
            "status": "success" if all_ok else "error",
            "all_passed": all_ok and bool(reports),
            "checked_count": len(reports),
            "reports": reports,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
