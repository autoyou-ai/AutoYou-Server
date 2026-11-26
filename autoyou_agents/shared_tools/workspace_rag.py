# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Pure-Python + SQLite FTS5 workspace RAG and symbol intelligence engine.

Zero external dependencies so it compiles cleanly with Nuitka into v2 macOS,
v2 Windows, Linux, WSL, and native AutoYou-Server bundles.

Indexes workspace files incrementally (by mtime + size) into an isolated cache
directory governed by `AUTOYOU_TEST_ROOT` / `get_service_data_dir`. Source
runs follow the development data-root policy; packaged runs use user data.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import ast
import fnmatch
import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from autoyou_agents.shared_tools import workspace_tools as wt

logger = logging.getLogger(__name__)

_INDEX_LOCK = threading.RLock()
_INDEX_SCHEMA_VERSION = 3
_MAX_INDEXED_FILES = 6000
_MAX_INDEX_FILE_BYTES = 512_000
_CHUNK_LINES = 60
_CHUNK_OVERLAP = 10
_MAX_CHUNKS_PER_FILE = 40

_TOKEN_SPLIT_RE = re.compile(r"[A-Za-z0-9_]+")
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# Lightweight multi-language symbol patterns for non-Python files.
_JS_TS_SYMBOL_PATTERNS = (
    ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)")),
    ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][A-Za-z0-9_$]*)")),
    ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")),
    ("const", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>")),
)
_RUST_SYMBOL_PATTERNS = (
    ("struct", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("enum", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?enum\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("trait", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?trait\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("function", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)")),
)
_GO_SYMBOL_PATTERNS = (
    ("type", re.compile(r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:struct|interface)\b")),
    ("function", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
)
_SWIFT_SYMBOL_PATTERNS = (
    ("class", re.compile(r"^\s*(?:public|private|internal|open|fileprivate|final\s+)*(?:class|struct|actor|enum|protocol)\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("function", re.compile(r"^\s*(?:public|private|internal|open|fileprivate|override|static|class|mutating\s+)*func\s+([A-Za-z_][A-Za-z0-9_]*)")),
)
_CSHARP_SYMBOL_PATTERNS = (
    ("class", re.compile(r"^\s*(?:public|private|internal|protected|static|sealed|abstract|partial\s+)*(?:class|struct|interface|record|enum)\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ("method", re.compile(r"^\s*(?:public|private|internal|protected|static|async|virtual|override|sealed\s+)+[A-Za-z0-9_<>,?\[\]]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
)
_HTML_HEADING_OR_ID_RE = re.compile(r"<(?:h[1-3]|section|main|canvas|form|button)\b[^>]*\bid=[\"']([^\"']+)[\"']", re.IGNORECASE)
_MD_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_ROUTE_DECORATOR_RE = re.compile(r"@\w+\.(get|post|put|patch|delete|websocket|route)\(\s*[\"']([^\"']+)[\"']")


def _code_tokenize(text: str) -> str:
    """Expand camelCase and snake_case identifiers for better BM25 recall."""
    tokens: List[str] = []
    for raw in _TOKEN_SPLIT_RE.findall(str(text or "")):
        low = raw.lower()
        tokens.append(low)
        if "_" in raw:
            for part in raw.split("_"):
                if part and part.lower() != low:
                    tokens.append(part.lower())
        camel_parts = _CAMEL_SPLIT_RE.split(raw)
        if len(camel_parts) > 1:
            for part in camel_parts:
                part_low = part.lower()
                if part_low and part_low != low:
                    tokens.append(part_low)
    return " ".join(tokens)


def _index_db_path(workspace_root: Path) -> Path:
    digest = hashlib.sha256(str(workspace_root.resolve()).encode("utf-8", errors="ignore")).hexdigest()[:16]
    try:
        from shared.platform_runtime import get_service_data_dir

        server_anchor = Path(__file__).resolve().parents[2] / "server.py"
        base_dir = get_service_data_dir("coding_workspace_rag", anchor=server_anchor)
    except Exception:
        test_root = os.getenv("AUTOYOU_TEST_ROOT")
        base_dir = Path(test_root) / "coding_workspace_rag" if test_root else Path.home() / ".cache" / "autoyou" / "coding_workspace_rag"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"ws-{digest}.sqlite3"


def _connect_index(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if not row or str(row["value"]) != str(_INDEX_SCHEMA_VERSION):
        conn.executescript(
            """
            DROP TABLE IF EXISTS files;
            DROP TABLE IF EXISTS symbols;
            DROP TABLE IF EXISTS chunks;
            DROP TABLE IF EXISTS chunks_fts;
            CREATE TABLE files (
                path TEXT PRIMARY KEY,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                line_count INTEGER NOT NULL,
                language TEXT NOT NULL
            );
            CREATE TABLE symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                signature TEXT NOT NULL
            );
            CREATE INDEX idx_symbols_name ON symbols(name COLLATE NOCASE);
            CREATE INDEX idx_symbols_path ON symbols(path);
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                symbol_names TEXT NOT NULL,
                preview TEXT NOT NULL
            );
            CREATE INDEX idx_chunks_path ON chunks(path);
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                path,
                symbols,
                tokens,
                tokenize='unicode61'
            );
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(_INDEX_SCHEMA_VERSION),),
        )
        conn.commit()
    return conn


def _detect_language(path: Path) -> str:
    suffix = path.suffix.lower()
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".html": "html",
        ".htm": "html",
        ".css": "css",
        ".scss": "css",
        ".json": "json",
        ".md": "markdown",
        ".rs": "rust",
        ".go": "go",
        ".swift": "swift",
        ".cs": "csharp",
        ".sql": "sql",
        ".sh": "shell",
        ".ps1": "powershell",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
    }
    return mapping.get(suffix, "text")


def extract_file_symbols(path: Path, text: str) -> List[Dict[str, Any]]:
    """Extract structural symbols (classes, functions, routes, headings) from a text file."""
    lang = _detect_language(path)
    symbols: List[Dict[str, Any]] = []
    lines = text.splitlines()

    if lang == "python":
        try:
            tree = ast.parse(text, filename=str(path))
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    bases = [ast.unparse(b) for b in node.bases] if hasattr(ast, "unparse") else []
                    sig = f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
                    symbols.append(
                        {
                            "name": node.name,
                            "qualified_name": node.name,
                            "kind": "class",
                            "line": int(node.lineno),
                            "end_line": int(getattr(node, "end_lineno", node.lineno) or node.lineno),
                            "signature": sig[:200],
                        }
                    )
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            prefix = "async def" if isinstance(item, ast.AsyncFunctionDef) else "def"
                            args_list = [a.arg for a in item.args.args]
                            sig = f"{prefix} {item.name}({', '.join(args_list[:8])})"
                            symbols.append(
                                {
                                    "name": item.name,
                                    "qualified_name": f"{node.name}.{item.name}",
                                    "kind": "method",
                                    "line": int(item.lineno),
                                    "end_line": int(getattr(item, "end_lineno", item.lineno) or item.lineno),
                                    "signature": sig[:200],
                                }
                            )
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                    args_list = [a.arg for a in node.args.args]
                    sig = f"{prefix} {node.name}({', '.join(args_list[:8])})"
                    symbols.append(
                        {
                            "name": node.name,
                            "qualified_name": node.name,
                            "kind": "function",
                            "line": int(node.lineno),
                            "end_line": int(getattr(node, "end_lineno", node.lineno) or node.lineno),
                            "signature": sig[:200],
                        }
                    )
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.isupper() and len(target.id) >= 3:
                            symbols.append(
                                {
                                    "name": target.id,
                                    "qualified_name": target.id,
                                    "kind": "constant",
                                    "line": int(node.lineno),
                                    "end_line": int(getattr(node, "end_lineno", node.lineno) or node.lineno),
                                    "signature": (lines[node.lineno - 1].strip() if node.lineno <= len(lines) else target.id)[:160],
                                }
                            )
            # Also extract FastAPI/Flask route decorators anywhere in the file.
            for idx, line in enumerate(lines, start=1):
                match = _ROUTE_DECORATOR_RE.search(line)
                if match:
                    method, route = match.group(1).upper(), match.group(2)
                    symbols.append(
                        {
                            "name": route,
                            "qualified_name": f"{method} {route}",
                            "kind": "route",
                            "line": idx,
                            "end_line": idx,
                            "signature": line.strip()[:160],
                        }
                    )
            return symbols[:250]
        except SyntaxError:
            pass  # Fall back to regex extraction below when a file has syntax errors mid-edit.

    pattern_map = {
        "javascript": _JS_TS_SYMBOL_PATTERNS,
        "typescript": _JS_TS_SYMBOL_PATTERNS,
        "rust": _RUST_SYMBOL_PATTERNS,
        "go": _GO_SYMBOL_PATTERNS,
        "swift": _SWIFT_SYMBOL_PATTERNS,
        "csharp": _CSHARP_SYMBOL_PATTERNS,
    }
    patterns = pattern_map.get(lang)
    if patterns:
        for idx, line in enumerate(lines, start=1):
            for kind, regex in patterns:
                m = regex.match(line)
                if m:
                    name = m.group(1)
                    symbols.append(
                        {
                            "name": name,
                            "qualified_name": name,
                            "kind": kind,
                            "line": idx,
                            "end_line": idx,
                            "signature": line.strip()[:180],
                        }
                    )
                    break
            if len(symbols) >= 200:
                break
        return symbols

    if lang == "markdown":
        for idx, line in enumerate(lines, start=1):
            m = _MD_HEADING_RE.match(line)
            if m:
                title = m.group(2).strip()
                symbols.append(
                    {
                        "name": title[:80],
                        "qualified_name": title[:80],
                        "kind": f"h{len(m.group(1))}",
                        "line": idx,
                        "end_line": idx,
                        "signature": line.strip()[:160],
                    }
                )
            if len(symbols) >= 120:
                break
        return symbols

    if lang == "html":
        for idx, line in enumerate(lines, start=1):
            for m in _HTML_HEADING_OR_ID_RE.finditer(line):
                el_id = m.group(1)
                symbols.append(
                    {
                        "name": el_id,
                        "qualified_name": f"#{el_id}",
                        "kind": "element",
                        "line": idx,
                        "end_line": idx,
                        "signature": line.strip()[:160],
                    }
                )
            if len(symbols) >= 120:
                break
        return symbols

    if lang == "python":
        # Regex fallback for Python files with temporary syntax errors.
        py_re = re.compile(r"^\s*(?:async\s+)?(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
        for idx, line in enumerate(lines, start=1):
            m = py_re.match(line)
            if m:
                kind = "class" if m.group(1) == "class" else "function"
                symbols.append(
                    {
                        "name": m.group(2),
                        "qualified_name": m.group(2),
                        "kind": kind,
                        "line": idx,
                        "end_line": idx,
                        "signature": line.strip()[:180],
                    }
                )
    return symbols[:200]


def _chunk_file_lines(lines: List[str], symbols: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    total = len(lines)
    if total == 0:
        return []
    chunks: List[Dict[str, Any]] = []
    step = max(1, _CHUNK_LINES - _CHUNK_OVERLAP)
    start = 0
    while start < total and len(chunks) < _MAX_CHUNKS_PER_FILE:
        end = min(total, start + _CHUNK_LINES)
        start_line = start + 1
        end_line = end
        chunk_text = "\n".join(lines[start:end])
        chunk_syms = [
            s["qualified_name"]
            for s in symbols
            if start_line <= int(s["line"]) <= end_line
        ]
        preview_lines = [
            f"{ln}: {line}"
            for ln, line in enumerate(lines[start : min(end, start + 18)], start=start_line)
        ]
        chunks.append(
            {
                "start_line": start_line,
                "end_line": end_line,
                "symbol_names": ", ".join(chunk_syms[:12]),
                "text": chunk_text,
                "preview": "\n".join(preview_lines)[:900],
            }
        )
        if end >= total:
            break
        start += step
    return chunks


def _sync_workspace_index(workspace_root: Path, base_dir: Path) -> Tuple[sqlite3.Connection, Dict[str, int]]:
    db_path = _index_db_path(workspace_root)
    conn = _connect_index(db_path)
    existing: Dict[str, Tuple[int, int]] = {
        str(row["path"]): (int(row["mtime_ns"]), int(row["size"]))
        for row in conn.execute("SELECT path, mtime_ns, size FROM files")
    }
    seen: set[str] = set()
    updated = 0
    indexed_count = 0
    scan_limited = False
    scope_prefix = ""
    try:
        rel_base = base_dir.relative_to(workspace_root)
        scope_prefix = "" if str(rel_base) == "." else rel_base.as_posix().rstrip("/") + "/"
    except ValueError:
        scope_prefix = ""

    for file_path in wt._iter_workspace_files(base_dir):
        if indexed_count >= _MAX_INDEXED_FILES:
            scan_limited = True
            break
        try:
            st = file_path.stat()
            if st.st_size > _MAX_INDEX_FILE_BYTES:
                continue
            rel = file_path.relative_to(workspace_root).as_posix()
        except Exception:
            continue
        seen.add(rel)
        indexed_count += 1
        prev = existing.get(rel)
        if prev == (st.st_mtime_ns, st.st_size):
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        lang = _detect_language(file_path)
        symbols = extract_file_symbols(file_path, text)
        chunks = _chunk_file_lines(lines, symbols)

        conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE path = ?)", (rel,))
        conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))
        conn.execute("DELETE FROM symbols WHERE path = ?", (rel,))
        conn.execute(
            "INSERT OR REPLACE INTO files(path, mtime_ns, size, line_count, language) VALUES(?, ?, ?, ?, ?)",
            (rel, st.st_mtime_ns, st.st_size, len(lines), lang),
        )
        if symbols:
            conn.executemany(
                "INSERT INTO symbols(path, name, qualified_name, kind, line, end_line, signature) VALUES(?, ?, ?, ?, ?, ?, ?)",
                [
                    (rel, s["name"], s["qualified_name"], s["kind"], s["line"], s["end_line"], s["signature"])
                    for s in symbols
                ],
            )
        for chunk in chunks:
            cur = conn.execute(
                "INSERT INTO chunks(path, start_line, end_line, symbol_names, preview) VALUES(?, ?, ?, ?, ?)",
                (rel, chunk["start_line"], chunk["end_line"], chunk["symbol_names"], chunk["preview"]),
            )
            rowid = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO chunks_fts(rowid, path, symbols, tokens) VALUES(?, ?, ?, ?)",
                (
                    rowid,
                    _code_tokenize(rel),
                    _code_tokenize(chunk["symbol_names"]),
                    _code_tokenize(chunk["text"]),
                ),
            )
        updated += 1

    # Prune deleted files within the scanned scope.
    removed = 0
    for old_path in list(existing.keys()):
        if scan_limited or (base_dir.is_file() and workspace_root / old_path != base_dir):
            continue
        if scope_prefix and not (old_path == scope_prefix.rstrip("/") or old_path.startswith(scope_prefix)):
            continue
        if old_path not in seen:
            conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE path = ?)", (old_path,))
            conn.execute("DELETE FROM chunks WHERE path = ?", (old_path,))
            conn.execute("DELETE FROM symbols WHERE path = ?", (old_path,))
            conn.execute("DELETE FROM files WHERE path = ?", (old_path,))
            removed += 1

    if updated or removed:
        conn.commit()
    return conn, {"indexed_files": indexed_count, "updated": updated, "removed": removed, "truncated": scan_limited}


def _index_workspace_root(base: Path) -> Path:
    root = wt._workspace_root()
    if base.is_relative_to(root):
        return root
    for extra in wt._extra_compiled_workspace_roots():
        if base.is_relative_to(extra):
            return extra
    raise ValueError(f"Path escapes workspace roots: {base}")


def _sql_scope(base: Path, root: Path, column: str) -> tuple[str, list]:
    if base == root:
        return "", []
    rel = base.relative_to(root).as_posix()
    if base.is_file():
        return f" AND {column} = ?", [rel]
    prefix = rel.rstrip("/") + "/"
    return f" AND substr({column}, 1, ?) = ?", [len(prefix), prefix]


def _fts_query_terms(query: str) -> List[str]:
    seen: List[str] = []
    for tok in _code_tokenize(query).split():
        cleaned = re.sub(r"[^a-z0-9_]", "", tok)
        if len(cleaned) >= 2 and cleaned not in seen:
            seen.append(cleaned)
    return seen[:12]


def _matches_filters(rel_path: str, file_type: str, file_glob: str) -> bool:
    if not file_type and not file_glob:
        return True
    name = Path(rel_path).name
    globs: List[str] = []
    if file_type and file_type in wt._FILE_TYPE_GLOBS:
        globs = wt._FILE_TYPE_GLOBS[file_type]
    elif file_glob:
        globs = [file_glob]
    if not globs:
        return True
    return any(fnmatch.fnmatch(name, g) or fnmatch.fnmatch(rel_path, g) for g in globs)


def rag_search_workspace(
    query: str,
    path: str = ".",
    max_results: int = 10,
    file_type: str = "",
    file_glob: str = "",
) -> Dict[str, Any]:
    """Hybrid BM25 + symbol + path semantic-lexical RAG search over the workspace.

    Use this first when exploring unfamiliar code or locating where a concept,
    feature, route, or symbol lives. Returns ranked file/line ranges and previews
    so you can jump straight to targeted edits without burning tool calls.
    """
    started = time.monotonic()
    try:
        q = str(query or "").strip()
        if not q:
            return {"status": "error", "message": "query is required"}
        base = wt._resolve_workspace_path(path, must_exist=True)
        root = _index_workspace_root(base)
        max_results = max(1, min(30, int(max_results)))
        terms = _fts_query_terms(q)
        if not terms:
            return {"status": "error", "message": "query must contain searchable words or identifiers"}

        scope_prefix = ""
        if base != root:
            rel_base = base.relative_to(root).as_posix()
            scope_prefix = rel_base if base.is_file() else rel_base.rstrip("/") + "/"

        with _INDEX_LOCK:
            conn, stats = _sync_workspace_index(root, base)
            with closing(conn):
                scope_sql, scope_params = _sql_scope(base, root, "c.path")
                conn.create_function("autoyou_path_matches", 1, lambda p: int(_matches_filters(p, file_type, file_glob)))
                # Build an OR-backed FTS5 query with prefix matching for high recall,
                # then boost multi-term AND matches and symbol/path hits.
                fts_expr = " OR ".join(f'"{t}"*' for t in terms)
                rows = conn.execute(
                    f"""
                    SELECT
                        c.id,
                        c.path,
                        c.start_line,
                        c.end_line,
                        c.symbol_names,
                        c.preview,
                        bm25(chunks_fts, 4.0, 3.0, 1.0) AS bm25_score
                    FROM chunks_fts
                    JOIN chunks c ON c.id = chunks_fts.rowid
                    WHERE chunks_fts MATCH ? {scope_sql} AND autoyou_path_matches(c.path)
                    ORDER BY bm25_score ASC
                    LIMIT 120
                    """,
                    (fts_expr, *scope_params),
                ).fetchall()

                scored: List[Tuple[float, Dict[str, Any]]] = []
                seen_spans: set[Tuple[str, int]] = set()
                for row in rows:
                    rel_path = str(row["path"])
                    if scope_prefix:
                        if base.is_file() and rel_path != scope_prefix:
                            continue
                        if base.is_dir() and not rel_path.startswith(scope_prefix):
                            continue
                    if not _matches_filters(rel_path, file_type, file_glob):
                        continue
                    span_key = (rel_path, int(row["start_line"]))
                    if span_key in seen_spans:
                        continue
                    seen_spans.add(span_key)
                    haystack = f"{rel_path} {row['symbol_names']} {row['preview']}".lower()
                    term_hits = sum(1 for t in terms if t in haystack)
                    path_hits = sum(1 for t in terms if t in rel_path.lower())
                    sym_hits = sum(1 for t in terms if t in str(row["symbol_names"]).lower())
                    # Lower BM25 is better in SQLite FTS5; convert to positive score.
                    score = (-float(row["bm25_score"])) + (term_hits * 2.5) + (sym_hits * 3.0) + (path_hits * 2.0)
                    scored.append(
                        (
                            score,
                            {
                                "path": wt._relative_path(root / rel_path),
                                "start_line": int(row["start_line"]),
                                "end_line": int(row["end_line"]),
                                "symbols": [s.strip() for s in str(row["symbol_names"]).split(",") if s.strip()],
                                "score": round(score, 2),
                                "preview": str(row["preview"]),
                            },
                        )
                    )

                # Also query direct symbol matches in case a chunk was truncated on huge files.
                for term in terms[:4]:
                    scope_sql, scope_params = _sql_scope(base, root, "path")
                    sym_rows = conn.execute(
                        f"""
                        SELECT path, name, qualified_name, kind, line, end_line, signature
                        FROM symbols
                        WHERE (instr(lower(name), ?) > 0 OR instr(lower(qualified_name), ?) > 0)
                        {scope_sql} AND autoyou_path_matches(path)
                        LIMIT 20
                        """,
                        (term.lower(), term.lower(), *scope_params),
                    ).fetchall()
                    for srow in sym_rows:
                        rel_path = str(srow["path"])
                        if scope_prefix:
                            if base.is_file() and rel_path != scope_prefix:
                                continue
                            if base.is_dir() and not rel_path.startswith(scope_prefix):
                                continue
                        if not _matches_filters(rel_path, file_type, file_glob):
                            continue
                        span_key = (rel_path, int(srow["line"]))
                        if span_key in seen_spans:
                            continue
                        seen_spans.add(span_key)
                        exact_boost = 8.0 if str(srow["name"]).lower() == q.lower() else 4.0
                        scored.append(
                            (
                                exact_boost,
                                {
                                    "path": wt._relative_path(root / rel_path),
                                    "start_line": int(srow["line"]),
                                    "end_line": int(srow["end_line"]),
                                    "symbols": [str(srow["qualified_name"])],
                                    "score": round(exact_boost, 2),
                                    "preview": f"{srow['line']}: {srow['signature']}",
                                },
                            )
                        )

        scored.sort(key=lambda pair: (-pair[0], pair[1]["path"], pair[1]["start_line"]))
        # Diversify: at most 3 chunks per file so one giant file doesn't crowd out other files.
        selected: List[Dict[str, Any]] = []
        per_file: Dict[str, int] = {}
        for _, item in scored:
            p = item["path"]
            if per_file.get(p, 0) >= 3:
                continue
            per_file[p] = per_file.get(p, 0) + 1
            selected.append(item)
            if len(selected) >= max_results:
                break

        rendered_blocks = []
        for item in selected:
            sym_note = f" [{', '.join(item['symbols'][:4])}]" if item["symbols"] else ""
            rendered_blocks.append(
                f"=== {item['path']}:{item['start_line']}-{item['end_line']}{sym_note} ===\n{item['preview']}"
            )

        return {
            "status": "success",
            "query": q,
            "results_count": len(selected),
            "results": selected,
            "rendered": wt._truncate("\n\n".join(rendered_blocks)),
            "index_stats": stats,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def find_symbol(
    symbol_name: str,
    path: str = ".",
    kind: str = "",
    max_results: int = 25,
) -> Dict[str, Any]:
    """Find class, function, method, route, or constant definitions by name."""
    try:
        needle = str(symbol_name or "").strip()
        if not needle:
            return {"status": "error", "message": "symbol_name is required"}
        base = wt._resolve_workspace_path(path, must_exist=True)
        root = _index_workspace_root(base)
        max_results = max(1, min(60, int(max_results)))
        kind_filter = str(kind or "").strip().lower()

        scope_prefix = ""
        if base != root:
            rel_base = base.relative_to(root).as_posix()
            scope_prefix = rel_base if base.is_file() else rel_base.rstrip("/") + "/"

        with _INDEX_LOCK:
            conn, _ = _sync_workspace_index(root, base)
            with closing(conn):
                scope_sql, scope_params = _sql_scope(base, root, "path")
                rows = conn.execute(
                    f"""
                    SELECT path, name, qualified_name, kind, line, end_line, signature
                    FROM symbols
                    WHERE (instr(lower(name), lower(?)) > 0 OR instr(lower(qualified_name), lower(?)) > 0)
                    {scope_sql} AND (? = '' OR lower(kind) = ?)
                    ORDER BY
                        CASE WHEN lower(name) = lower(?) THEN 0
                             WHEN lower(name) LIKE lower(?) THEN 1
                             ELSE 2 END,
                        path ASC, line ASC
                    LIMIT 120
                    """,
                    (needle, needle, *scope_params, kind_filter, kind_filter, needle, f"{needle}%"),
                ).fetchall()

        matches: List[Dict[str, Any]] = []
        for row in rows:
            rel_path = str(row["path"])
            if scope_prefix:
                if base.is_file() and rel_path != scope_prefix:
                    continue
                if base.is_dir() and not rel_path.startswith(scope_prefix):
                    continue
            if kind_filter and str(row["kind"]).lower() != kind_filter:
                continue
            matches.append(
                {
                    "path": wt._relative_path(root / rel_path),
                    "name": str(row["name"]),
                    "qualified_name": str(row["qualified_name"]),
                    "kind": str(row["kind"]),
                    "line": int(row["line"]),
                    "end_line": int(row["end_line"]),
                    "signature": str(row["signature"]),
                }
            )
            if len(matches) >= max_results:
                break

        rendered = "\n".join(
            f"{m['path']}:{m['line']}-{m['end_line']} [{m['kind']}] {m['signature']}"
            for m in matches
        )
        return {
            "status": "success",
            "symbol_name": needle,
            "count": len(matches),
            "matches": matches,
            "rendered": wt._truncate(rendered),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def get_file_outline(path: str) -> Dict[str, Any]:
    """Return a compact structural outline (imports, classes, functions, routes, line spans) for a file."""
    try:
        file_path = wt._resolve_workspace_path(path, must_exist=True)
        if not file_path.is_file():
            return {"status": "error", "message": f"Not a file: {path}"}
        text = file_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        symbols = extract_file_symbols(file_path, text)
        lang = _detect_language(file_path)

        imports: List[str] = []
        if lang == "python":
            for line in lines[:120]:
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")) and len(imports) < 25:
                    imports.append(stripped[:140])
        elif lang in {"javascript", "typescript"}:
            for line in lines[:120]:
                stripped = line.strip()
                if stripped.startswith(("import ", "export ")) and len(imports) < 25:
                    imports.append(stripped[:140])

        rendered_lines = [
            f"File: {wt._relative_path(file_path)} ({lang}, {len(lines)} lines)",
        ]
        if imports:
            rendered_lines.append("Imports/Exports: " + "; ".join(imports[:10]))
        if symbols:
            rendered_lines.append("Symbols:")
            for sym in symbols[:100]:
                span = f"L{sym['line']}-L{sym['end_line']}" if sym["end_line"] != sym["line"] else f"L{sym['line']}"
                rendered_lines.append(f"  {span} [{sym['kind']}] {sym['signature']}")
        else:
            rendered_lines.append("No structured symbols detected; use read_file for line contents.")

        return {
            "status": "success",
            "path": wt._relative_path(file_path),
            "language": lang,
            "total_lines": len(lines),
            "imports": imports,
            "symbols": symbols,
            "rendered": wt._truncate("\n".join(rendered_lines)),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
