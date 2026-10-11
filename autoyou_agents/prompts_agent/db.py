# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Database engine and persistence layer for prompts_agent."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from shared.secure_storage import enable_secure_storage_from_environment

from .config import (
    DATA_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_SETTINGS,
    SOFTWARE_COLORS,
    SOFTWARE_LABELS,
    get_prompts_db_path,
)

logger = logging.getLogger(__name__)

_DB_LOCK = threading.RLock()


def utc_now() -> str:
    """Return current ISO-formatted UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def hash_pin(pin: str) -> str:
    """Hash a numeric OTP or password using SHA-256 with salt."""
    salt = "autoyou-prompts-salt-2026"
    return hashlib.sha256(f"{salt}:{pin.strip()}".encode("utf-8")).hexdigest()


class PromptsDatabase:
    """Thread-safe SQLite storage for harvested prompts, sessions, projects, and hooks."""

    def __init__(self, db_path: Optional[Path] = None):
        enable_secure_storage_from_environment()
        self.db_path = Path(db_path or get_prompts_db_path()).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with _DB_LOCK:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_schema(self) -> None:
        """Create tables, indexes, and full-text search virtual tables."""
        with self._connection() as conn:
            cursor = conn.cursor()

            # 1. Settings
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # 2. Projects / Worktrees
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    root_path TEXT NOT NULL,
                    software TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    prompt_count INTEGER DEFAULT 0
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_software ON projects(software)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name)")

            # 3. Sessions / Conversations
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    software TEXT NOT NULL,
                    project_id TEXT,
                    project_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_file TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    turn_count INTEGER DEFAULT 0,
                    model TEXT,
                    tags TEXT DEFAULT '[]',
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_software ON sessions(software)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC)")

            # 4. Prompts / Q&A turns
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prompts (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    output_text TEXT,
                    attachments_json TEXT DEFAULT '[]',
                    model TEXT,
                    tokens INTEGER DEFAULT 0,
                    timestamp TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    is_final_output INTEGER DEFAULT 0,
                    is_favorite INTEGER DEFAULT 0,
                    annotation TEXT DEFAULT '',
                    metadata_json TEXT DEFAULT '{}',
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompts_session ON prompts(session_id, turn_index)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompts_timestamp ON prompts(timestamp DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompts_fav ON prompts(is_favorite)")

            # In-place migrations for existing tables
            try:
                cursor.execute("ALTER TABLE prompts ADD COLUMN is_favorite INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            try:
                cursor.execute("ALTER TABLE prompts ADD COLUMN annotation TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass

            # 5. Attachments
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY,
                    prompt_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    file_size INTEGER DEFAULT 0,
                    file_path TEXT,
                    thumbnail_b64 TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_attachments_prompt ON attachments(prompt_id)")

            # 6. File cache for fast incremental sync
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS file_cache (
                    file_path TEXT PRIMARY KEY,
                    software TEXT NOT NULL,
                    mtime REAL NOT NULL,
                    file_size INTEGER NOT NULL,
                    last_synced_at TEXT NOT NULL,
                    session_count INTEGER DEFAULT 0
                )
            """)

            # 7. Sync run history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_history (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    sources_scanned INTEGER DEFAULT 0,
                    new_prompts INTEGER DEFAULT 0,
                    updated_sessions INTEGER DEFAULT 0,
                    error_message TEXT
                )
            """)

            # 8. Webhook / Push tokens
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS hooks (
                    token_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL,
                    token_prefix TEXT NOT NULL,
                    client_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    is_active INTEGER DEFAULT 1
                )
            """)

            # 9. Optional FTS5 virtual table for lightning-fast search
            try:
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS prompts_fts USING fts5(
                        prompt_id UNINDEXED,
                        session_id UNINDEXED,
                        prompt_text,
                        output_text,
                        title,
                        project_name,
                        software
                    )
                """)
            except sqlite3.OperationalError:
                logger.info("FTS5 extension not available in SQLite; using indexed LIKE queries.")

            # Seed default settings if empty
            for key, val in DEFAULT_SETTINGS.items():
                cursor.execute(
                    "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(val) if not isinstance(val, str) else val, utc_now()),
                )

    # ─── Settings ─────────────────────────────────────────────────────────────

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._connection() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if not row:
                return default
            val = row["value"]
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return val

    def set_setting(self, key: str, value: Any) -> None:
        raw = json.dumps(value) if not isinstance(value, str) else value
        with self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, raw, utc_now()),
            )

    def get_all_settings(self) -> Dict[str, Any]:
        with self._connection() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            out: Dict[str, Any] = {}
            for r in rows:
                val = r["value"]
                try:
                    out[r["key"]] = json.loads(val)
                except Exception:
                    out[r["key"]] = val
            return out

    def verify_otp_pin(self, pin: str) -> bool:
        """Verify entered PIN against stored hashed PIN or default."""
        stored_hash = self.get_setting("otp_pin_hash")
        default_pin = str(self.get_setting("default_otp_pin", "123456"))
        if not stored_hash:
            return pin.strip() == default_pin.strip()
        return hash_pin(pin) == stored_hash

    def update_otp_pin(self, new_pin: str) -> None:
        self.set_setting("otp_pin_hash", hash_pin(new_pin))

    # ─── Projects & Sessions ──────────────────────────────────────────────────

    def upsert_project(self, project_id: str, name: str, root_path: str, software: str) -> str:
        with self._connection() as conn:
            now = utc_now()
            conn.execute("""
                INSERT INTO projects (id, name, root_path, software, first_seen, last_seen, prompt_count)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    root_path = excluded.root_path,
                    last_seen = excluded.last_seen
            """, (project_id, name, root_path, software, now, now))
            return project_id

    def upsert_session(
        self,
        session_id: str,
        software: str,
        project_name: str,
        title: str,
        source_file: Optional[str] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        turn_count: int = 0,
        model: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project_id: Optional[str] = None,
    ) -> None:
        with self._connection() as conn:
            now = utc_now()
            c_at = created_at or now
            u_at = updated_at or now
            tags_json = json.dumps(tags or [])
            conn.execute("""
                INSERT INTO sessions (id, software, project_id, project_name, title, source_file, created_at, updated_at, turn_count, model, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_name = excluded.project_name,
                    title = excluded.title,
                    source_file = excluded.source_file,
                    updated_at = excluded.updated_at,
                    turn_count = excluded.turn_count,
                    model = COALESCE(excluded.model, sessions.model),
                    tags = excluded.tags
            """, (session_id, software, project_id, project_name, title, source_file, c_at, u_at, turn_count, model, tags_json))

    def insert_session_bundle(
        self,
        session_id: str,
        software: str,
        project_name: str,
        title: str,
        source_file: Optional[str] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        model: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project_id: Optional[str] = None,
        turns: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Insert session and all its turns within a single transaction for maximum throughput."""
        now = utc_now()
        c_at = created_at or now
        u_at = updated_at or now
        turns_list = turns or []
        turn_count = len(turns_list)
        tags_json = json.dumps(tags or [])

        with self._connection() as conn:
            # 1. Upsert session
            conn.execute("""
                INSERT INTO sessions (id, software, project_id, project_name, title, source_file, created_at, updated_at, turn_count, model, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_name = excluded.project_name,
                    title = excluded.title,
                    source_file = excluded.source_file,
                    updated_at = excluded.updated_at,
                    turn_count = excluded.turn_count,
                    model = COALESCE(excluded.model, sessions.model),
                    tags = excluded.tags
            """, (session_id, software, project_id, project_name, title, source_file, c_at, u_at, turn_count, model, tags_json))

            # 2. Insert prompt turns
            new_prompts = 0
            for t_idx, turn in enumerate(turns_list):
                pid = f"{session_id}_t{t_idx}"
                prompt_text = turn.get("prompt_text", "")
                output_text = turn.get("output_text")
                att_json = json.dumps(turn.get("attachments") or [])
                t_model = turn.get("model") or model
                tokens = turn.get("tokens", 0)
                ts = turn.get("timestamp") or u_at
                is_final = 1 if turn.get("is_final_output") else 0
                meta_json = json.dumps(turn.get("metadata") or {})

                conn.execute("""
                    INSERT INTO prompts (
                        id, session_id, turn_index, role, prompt_text, output_text,
                        attachments_json, model, tokens, timestamp, created_at,
                        is_final_output, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        prompt_text = excluded.prompt_text,
                        output_text = excluded.output_text,
                        attachments_json = excluded.attachments_json,
                        model = excluded.model,
                        tokens = excluded.tokens,
                        is_final_output = excluded.is_final_output,
                        is_favorite = prompts.is_favorite,
                        annotation = prompts.annotation,
                        metadata_json = excluded.metadata_json
                """, (
                    pid, session_id, t_idx, turn.get("role", "user"), prompt_text, output_text,
                    att_json, t_model, tokens, ts, now, is_final, meta_json
                ))
                new_prompts += 1

                # Update FTS5
                try:
                    conn.execute("DELETE FROM prompts_fts WHERE prompt_id = ?", (pid,))
                    conn.execute("""
                        INSERT INTO prompts_fts (prompt_id, session_id, prompt_text, output_text, title, project_name, software)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (pid, session_id, prompt_text, output_text or "", title, project_name, software))
                except sqlite3.OperationalError:
                    pass

            return new_prompts

    # ─── Prompts ──────────────────────────────────────────────────────────────

    def insert_prompt(
        self,
        prompt_id: str,
        session_id: str,
        turn_index: int,
        role: str,
        prompt_text: str,
        output_text: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        tokens: int = 0,
        timestamp: Optional[str] = None,
        is_final_output: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Insert or replace prompt turn and update search index."""
        now = utc_now()
        ts = timestamp or now
        att_json = json.dumps(attachments or [])
        meta_json = json.dumps(metadata or {})
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO prompts (
                    id, session_id, turn_index, role, prompt_text, output_text,
                    attachments_json, model, tokens, timestamp, created_at,
                    is_final_output, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    prompt_text = excluded.prompt_text,
                    output_text = excluded.output_text,
                    attachments_json = excluded.attachments_json,
                    model = excluded.model,
                    tokens = excluded.tokens,
                    is_final_output = excluded.is_final_output,
                    is_favorite = prompts.is_favorite,
                    annotation = prompts.annotation,
                    metadata_json = excluded.metadata_json
            """, (
                prompt_id, session_id, turn_index, role, prompt_text, output_text,
                att_json, model, tokens, ts, now, 1 if is_final_output else 0, meta_json
            ))

            # Update session turn count and project prompt count
            conn.execute("""
                UPDATE sessions SET turn_count = (
                    SELECT COUNT(*) FROM prompts WHERE session_id = ?
                ), updated_at = ? WHERE id = ?
            """, (session_id, ts, session_id))

            # Update FTS5 if table exists
            try:
                s_row = conn.execute("SELECT software, project_name, title FROM sessions WHERE id = ?", (session_id,)).fetchone()
                if s_row:
                    conn.execute("DELETE FROM prompts_fts WHERE prompt_id = ?", (prompt_id,))
                    conn.execute("""
                        INSERT INTO prompts_fts (prompt_id, session_id, prompt_text, output_text, title, project_name, software)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (prompt_id, session_id, prompt_text, output_text or "", s_row["title"], s_row["project_name"], s_row["software"]))
            except sqlite3.OperationalError:
                pass

            return True

    def toggle_favorite(self, prompt_id: str) -> bool:
        """Toggle favorite state of a prompt."""
        with self._connection() as conn:
            row = conn.execute("SELECT is_favorite FROM prompts WHERE id = ?", (prompt_id,)).fetchone()
            if not row:
                return False
            new_val = 0 if row["is_favorite"] else 1
            conn.execute("UPDATE prompts SET is_favorite = ? WHERE id = ?", (new_val, prompt_id))
            return bool(new_val)

    def update_annotation(self, prompt_id: str, note: str) -> bool:
        """Add or update operator notes/annotations on a prompt turn."""
        with self._connection() as conn:
            conn.execute("UPDATE prompts SET annotation = ? WHERE id = ?", (note, prompt_id))
            return True

    def get_prompts(
        self,
        query: Optional[str] = None,
        software: Optional[str] = None,
        project: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        has_attachment: Optional[bool] = None,
        has_output: Optional[bool] = None,
        is_favorite: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "newest",
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Query prompts with filtering, full-text search, and pagination."""
        with self._connection() as conn:
            where_clauses: List[str] = []
            params: List[Any] = []

            if software and software != "all":
                where_clauses.append("s.software = ?")
                params.append(software)

            if project:
                where_clauses.append("s.project_name LIKE ?")
                params.append(f"%{project}%")

            if date_from:
                where_clauses.append("p.timestamp >= ?")
                params.append(date_from)

            if date_to:
                where_clauses.append("p.timestamp <= ?")
                params.append(date_to)

            if has_attachment is True:
                where_clauses.append("p.attachments_json != '[]'")

            if has_output is True:
                where_clauses.append("p.output_text IS NOT NULL AND p.output_text != ''")

            if is_favorite is True:
                where_clauses.append("p.is_favorite = 1")

            fts_joined = False
            if query and query.strip():
                clean_q = query.strip()
                try:
                    # Test if FTS works
                    test_match = clean_q.replace('"', '""')
                    conn.execute("SELECT 1 FROM prompts_fts WHERE prompts_fts MATCH ? LIMIT 1", (f'"{test_match}"',))
                    where_clauses.append("p.id IN (SELECT prompt_id FROM prompts_fts WHERE prompts_fts MATCH ?)")
                    params.append(f'"{test_match}"')
                    fts_joined = True
                except Exception:
                    where_clauses.append("(p.prompt_text LIKE ? OR p.output_text LIKE ? OR s.title LIKE ?)")
                    param_like = f"%{clean_q}%"
                    params.extend([param_like, param_like, param_like])

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            # Count total matching
            count_sql = f"""
                SELECT COUNT(*) as total
                FROM prompts p
                JOIN sessions s ON p.session_id = s.id
                {where_sql}
            """
            total = conn.execute(count_sql, params).fetchone()["total"]

            order_by = "p.timestamp DESC" if sort != "oldest" else "p.timestamp ASC"

            select_sql = f"""
                SELECT
                    p.id, p.session_id, p.turn_index, p.role, p.prompt_text,
                    p.output_text, p.attachments_json, p.model, p.tokens,
                    p.timestamp, p.is_final_output, p.is_favorite, p.annotation,
                    p.metadata_json, s.software, s.project_name, s.title as session_title
                FROM prompts p
                JOIN sessions s ON p.session_id = s.id
                {where_sql}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
            """
            rows = conn.execute(select_sql, [*params, limit, offset]).fetchall()

            out: List[Dict[str, Any]] = []
            for r in rows:
                item = dict(r)
                try:
                    item["attachments"] = json.loads(item.get("attachments_json") or "[]")
                except Exception:
                    item["attachments"] = []
                try:
                    item["metadata"] = json.loads(item.get("metadata_json") or "{}")
                except Exception:
                    item["metadata"] = {}
                out.append(item)

            return out, total

    def get_prompt_by_id(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("""
                SELECT
                    p.*, s.software, s.project_name, s.title as session_title
                FROM prompts p
                JOIN sessions s ON p.session_id = s.id
                WHERE p.id = ?
            """, (prompt_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            try:
                item["attachments"] = json.loads(item.get("attachments_json") or "[]")
            except Exception:
                item["attachments"] = []
            try:
                item["metadata"] = json.loads(item.get("metadata_json") or "{}")
            except Exception:
                item["metadata"] = {}
            return item

    def get_session_by_id(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return session info and its full ordered turn chain."""
        with self._connection() as conn:
            s_row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not s_row:
                return None
            session = dict(s_row)
            p_rows = conn.execute("""
                SELECT * FROM prompts
                WHERE session_id = ?
                ORDER BY turn_index ASC, timestamp ASC
            """, (session_id,)).fetchall()

            turns = []
            for r in p_rows:
                t = dict(r)
                try:
                    t["attachments"] = json.loads(t.get("attachments_json") or "[]")
                except Exception:
                    t["attachments"] = []
                try:
                    t["metadata"] = json.loads(t.get("metadata_json") or "{}")
                except Exception:
                    t["metadata"] = {}
                turns.append(t)
            session["turns"] = turns
            return session

    def get_projects(self) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("""
                SELECT
                    s.project_name,
                    s.software,
                    COUNT(DISTINCT s.id) as session_count,
                    COUNT(p.id) as prompt_count,
                    MAX(p.timestamp) as last_activity
                FROM sessions s
                LEFT JOIN prompts p ON s.id = p.session_id
                GROUP BY s.project_name, s.software
                ORDER BY last_activity DESC
            """).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self) -> Dict[str, Any]:
        with self._connection() as conn:
            total_prompts = conn.execute("SELECT COUNT(*) as c FROM prompts").fetchone()["c"]
            total_sessions = conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
            total_projects = conn.execute("SELECT COUNT(DISTINCT project_name) as c FROM sessions").fetchone()["c"]
            last_sync_row = conn.execute("SELECT * FROM sync_history ORDER BY started_at DESC LIMIT 1").fetchone()

            by_sw_rows = conn.execute("""
                SELECT s.software, COUNT(p.id) as count
                FROM sessions s
                JOIN prompts p ON s.id = p.session_id
                GROUP BY s.software
            """).fetchall()
            by_software = {r["software"]: r["count"] for r in by_sw_rows}

            # Database file size
            db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

            return {
                "total_prompts": total_prompts,
                "total_sessions": total_sessions,
                "total_projects": total_projects,
                "by_software": by_software,
                "db_size_bytes": db_size,
                "last_sync": dict(last_sync_row) if last_sync_row else None,
            }

    # ─── Mind Map / Obsidian Visual Graph Data ────────────────────────────────

    def get_mind_map_graph(self, software: Optional[str] = None, max_nodes: int = 150) -> Dict[str, Any]:
        """Generate interactive force/mind-map graph nodes and edges.

        Hierarchy:
        Root -> Software Nodes -> Project Nodes -> Session Nodes -> Prompt Leaf Nodes.
        """
        nodes: List[Dict[str, Any]] = []
        links: List[Dict[str, Any]] = []
        node_ids: set[str] = set()

        def add_node(node_id: str, label: str, group: str, val: int, color: str, meta: Optional[Dict] = None):
            if node_id not in node_ids:
                node_ids.add(node_id)
                nodes.append({
                    "id": node_id,
                    "label": label,
                    "group": group,
                    "val": val,
                    "color": color,
                    "meta": meta or {},
                })

        def add_link(source: str, target: str, link_type: str):
            if source in node_ids and target in node_ids:
                links.append({
                    "source": source,
                    "target": target,
                    "type": link_type,
                })

        # 1. Root Central Hub
        root_id = "root"
        add_node(root_id, "My Prompts", "root", 30, "#6366f1")

        with self._connection() as conn:
            sw_filter = "WHERE s.software = ?" if (software and software != "all") else ""
            params = [software] if (software and software != "all") else []

            # 2. Software platforms
            sw_rows = conn.execute(f"""
                SELECT s.software, COUNT(p.id) as p_count
                FROM sessions s
                JOIN prompts p ON s.id = p.session_id
                {sw_filter}
                GROUP BY s.software
            """, params).fetchall()

            for sw_r in sw_rows:
                sw_name = sw_r["software"]
                sw_id = f"sw_{sw_name}"
                sw_color = SOFTWARE_COLORS.get(sw_name, "#94a3b8")
                sw_label = SOFTWARE_LABELS.get(sw_name, sw_name.title())
                add_node(sw_id, sw_label, "software", max(15, min(25, sw_r["p_count"])), sw_color)
                add_link(root_id, sw_id, "root-software")

            # 3. Top Projects per software
            proj_rows = conn.execute(f"""
                SELECT s.software, s.project_name, COUNT(p.id) as p_count, MAX(p.timestamp) as last_seen
                FROM sessions s
                JOIN prompts p ON s.id = p.session_id
                {sw_filter}
                GROUP BY s.software, s.project_name
                ORDER BY p_count DESC
                LIMIT 40
            """, params).fetchall()

            for pr in proj_rows:
                sw_name = pr["software"]
                sw_id = f"sw_{sw_name}"
                p_name = pr["project_name"] or "General"
                proj_id = f"proj_{sw_name}_{hashlib.md5(p_name.encode()).hexdigest()[:8]}"
                sw_color = SOFTWARE_COLORS.get(sw_name, "#94a3b8")
                add_node(proj_id, p_name, "project", max(10, min(20, pr["p_count"])), sw_color, {
                    "software": sw_name,
                    "prompt_count": pr["p_count"],
                    "last_seen": pr["last_seen"]
                })
                add_link(sw_id, proj_id, "software-project")

            # 4. Recent Sessions and Prompts (up to node budget)
            sess_rows = conn.execute(f"""
                SELECT s.id, s.software, s.project_name, s.title, COUNT(p.id) as p_count, MAX(p.timestamp) as updated_at
                FROM sessions s
                JOIN prompts p ON s.id = p.session_id
                {sw_filter}
                GROUP BY s.id
                ORDER BY updated_at DESC
                LIMIT 50
            """, params).fetchall()

            for sr in sess_rows:
                sw_name = sr["software"]
                p_name = sr["project_name"] or "General"
                proj_id = f"proj_{sw_name}_{hashlib.md5(p_name.encode()).hexdigest()[:8]}"
                sess_id = f"sess_{sr['id']}"
                title = (sr["title"] or "Session")[:30]
                sw_color = SOFTWARE_COLORS.get(sw_name, "#94a3b8")
                add_node(sess_id, title, "session", 8, sw_color, {
                    "raw_id": sr["id"],
                    "software": sw_name,
                    "project": p_name,
                    "full_title": sr["title"],
                })
                if proj_id in node_ids:
                    add_link(proj_id, sess_id, "project-session")

                # Sample prompt turns under this session
                pr_turns = conn.execute("""
                    SELECT id, prompt_text, turn_index, timestamp
                    FROM prompts
                    WHERE session_id = ?
                    ORDER BY turn_index ASC
                    LIMIT 3
                """, (sr["id"],)).fetchall()

                for pt in pr_turns:
                    pt_id = f"prompt_{pt['id']}"
                    snippet = (pt["prompt_text"] or "").strip().splitlines()[0][:35]
                    add_node(pt_id, f"Turn {pt['turn_index']}: {snippet}...", "prompt", 5, "#a855f7", {
                        "raw_id": pt["id"],
                        "session_id": sr["id"],
                        "timestamp": pt["timestamp"],
                    })
                    add_link(sess_id, pt_id, "session-prompt")

        return {"nodes": nodes, "links": links}

    # ─── File Cache & Sync Tracking ───────────────────────────────────────────

    def get_cached_file(self, file_path: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM file_cache WHERE file_path = ?", (file_path,)).fetchone()
            return dict(row) if row else None

    def update_cached_file(self, file_path: str, software: str, mtime: float, file_size: int, session_count: int) -> None:
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO file_cache (file_path, software, mtime, file_size, last_synced_at, session_count)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    mtime = excluded.mtime,
                    file_size = excluded.file_size,
                    last_synced_at = excluded.last_synced_at,
                    session_count = excluded.session_count
            """, (file_path, software, mtime, file_size, utc_now(), session_count))

    def record_sync_start(self, run_id: str) -> None:
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO sync_history (run_id, started_at, status)
                VALUES (?, ?, 'running')
            """, (run_id, utc_now()))

    def record_sync_finish(
        self,
        run_id: str,
        status: str,
        sources_scanned: int = 0,
        new_prompts: int = 0,
        updated_sessions: int = 0,
        error_message: Optional[str] = None,
    ) -> None:
        with self._connection() as conn:
            conn.execute("""
                UPDATE sync_history SET
                    finished_at = ?,
                    status = ?,
                    sources_scanned = ?,
                    new_prompts = ?,
                    updated_sessions = ?,
                    error_message = ?
                WHERE run_id = ?
            """, (utc_now(), status, sources_scanned, new_prompts, updated_sessions, error_message, run_id))

    # ─── Hooks / Push Tokens ──────────────────────────────────────────────────

    def create_hook_token(self, client_name: str) -> Tuple[str, str]:
        """Generate a secure hook token for external CLI or software integration."""
        raw_token = "ay_hook_" + secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        token_id = secrets.token_hex(8)
        prefix = raw_token[:14]
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO hooks (token_id, token_hash, token_prefix, client_name, created_at, is_active)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (token_id, token_hash, prefix, client_name, utc_now()))
        return token_id, raw_token

    def validate_hook_token(self, raw_token: str) -> Optional[Dict[str, Any]]:
        if not raw_token:
            return None
        token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
        with self._connection() as conn:
            row = conn.execute("""
                SELECT * FROM hooks
                WHERE token_hash = ? AND is_active = 1
            """, (token_hash,)).fetchone()
            if not row:
                return None
            conn.execute("UPDATE hooks SET last_used_at = ? WHERE token_id = ?", (utc_now(), row["token_id"]))
            return dict(row)

    def list_hooks(self) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("SELECT token_id, token_prefix, client_name, created_at, last_used_at, is_active FROM hooks ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def revoke_hook(self, token_id: str) -> bool:
        with self._connection() as conn:
            conn.execute("UPDATE hooks SET is_active = 0 WHERE token_id = ?", (token_id,))
            return True
