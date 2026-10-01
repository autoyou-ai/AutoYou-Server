# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-9628afa4e1065048d4567828

"""
NotesTool: Persistence for notes and media attachments.

Provides a simple SQLite-backed interface for saving media attachments to disk
and recording metadata in a table for downstream use by agents and APIs.

This module includes only the media persistence that is needed for attachment
ingestion from chat contexts. It can be extended with full note CRUD APIs as
required.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import base64
import json
import logging
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from shared.secure_storage import (
    FILE_HEADER as SPM_FILE_HEADER,
    SecureStorageError,
    ensure_secure_storage_for_path,
    read_secure_file,
    sealed_envelope_kind,
    write_secure_file,
)

__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-pay-9628afa4e1065048d4567828"


LOGGER = logging.getLogger(__name__)


class NotesStorageSealedError(RuntimeError):
    """The notes database is a Maximus envelope that this process cannot open.

    Raised instead of SQLite's opaque "file is not a database" so callers - and
    the user - can tell a sealed store apart from a corrupt one.
    """

class NotesTool:
    """Media persistence and lightweight notes tool.

    Parameters
    ----------
    db_path: Optional[str]
        Path to the SQLite database file. If not provided, a default path
        under AutoYou's mutable runtime data directory will be used.
    """

    _MEDIA_SUBDIR = "media"
    DEFAULT_LIST_LIMIT = 25
    DEFAULT_PAGE_LIMIT = 20

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path:
            self._db_path = db_path
            self._storage_dir = Path(db_path).expanduser().resolve().parent
        else:
            self._storage_dir = self._resolve_default_storage_dir()
            os.makedirs(self._storage_dir, exist_ok=True)
            self._db_path = str(self._storage_dir / "autoyou_notes.db")
        self._init_database()
        if not db_path:
            self._migrate_legacy_databases()

    @classmethod
    def _resolve_default_storage_dir(cls) -> Path:
        module_dir = Path(__file__).resolve().parent
        try:
            from shared.platform_runtime import get_mutable_data_dir

            runtime_anchor = module_dir
            for parent in module_dir.parents:
                if (parent / "server.py").is_file() and (parent / "autoyou_agents").is_dir():
                    runtime_anchor = parent
                    break

            runtime_root = get_mutable_data_dir("AutoYou", anchor=runtime_anchor)
            return runtime_root / "autoyou_notes_agent"
        except (ImportError, Exception):
            pass
        return module_dir / "autoyou_notes_agent"

    @classmethod
    def _legacy_db_candidates(cls, primary_db_path: Path) -> list[Path]:
        module_dir = Path(__file__).resolve().parent
        candidates = [
            module_dir / "autoyou_notes.db",
            module_dir / "autoyou_notes_agent" / "autoyou_notes.db",
        ]
        try:
            from shared.platform_runtime import get_user_data_dir

            user_dir = get_user_data_dir("AutoYou")
            candidates.extend(
                [
                    user_dir / "autoyou_notes.db",
                    user_dir / "autoyou_notes_agent" / "autoyou_notes.db",
                ]
            )
        except (ImportError, Exception):
            pass

        resolved_primary = primary_db_path.expanduser().resolve()
        unique_candidates: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            resolved_candidate = candidate.expanduser().resolve()
            key = str(resolved_candidate).lower()
            if key == str(resolved_primary).lower() or key in seen:
                continue
            seen.add(key)
            unique_candidates.append(resolved_candidate)
        return unique_candidates

    @staticmethod
    def _safe_json_loads(value: Any, default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except Exception:
            return default

    @staticmethod
    def _local_timestamp() -> str:
        """Return the current local wall-clock time as an ISO string."""
        return datetime.now().isoformat(timespec="seconds")

    @classmethod
    def _normalize_stored_timestamp(cls, value: Any) -> Any:
        """Normalize legacy UTC timestamps to local wall-clock ISO strings."""
        raw = str(value or "").strip()
        if not raw:
            return value
        # New local format already uses `T` without a timezone suffix.
        if "T" in raw and "+" not in raw and not raw.endswith("Z"):
            return raw
        try:
            if " " in raw and "T" not in raw:
                parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            else:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    return raw
            return parsed.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
        except Exception:
            return raw

    def _database_counts(self, db_path: Path) -> tuple[int, int]:
        if not db_path.is_file():
            return (0, 0)
        try:
            with sqlite3.connect(str(db_path)) as conn:
                notes_count = int(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
                media_count = int(conn.execute("SELECT COUNT(*) FROM media_attachments").fetchone()[0])
            return (notes_count, media_count)
        except Exception:
            return (0, 0)

    @staticmethod
    def _note_signature(row: sqlite3.Row) -> str:
        payload = {
            "title": row["title"],
            "content": row["content"],
            "tags": NotesTool._safe_json_loads(row["tags"], []),
            "category": row["category"],
            "metadata": NotesTool._safe_json_loads(row["metadata"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        return json.dumps(payload, sort_keys=True, default=str)

    @staticmethod
    def _hash_file(path_value: Optional[str]) -> Optional[str]:
        path_str = str(path_value or "").strip()
        if not path_str or not os.path.isfile(path_str):
            return None
        import hashlib

        digest = hashlib.sha256()
        raw = Path(path_str).read_bytes()
        digest.update(read_secure_file(path_str) if raw.startswith(SPM_FILE_HEADER) else raw)
        return digest.hexdigest()

    def _media_signature(self, row: sqlite3.Row) -> str:
        payload = {
            "filename": row["filename"],
            "mimetype": row["mimetype"],
            "size_bytes": row["size_bytes"],
            "source": row["source"],
            "user_id": row["user_id"],
            "session_id": row["session_id"],
            "message_id": row["message_id"],
            "metadata": self._safe_json_loads(row["metadata"], {}),
            "file_hash": self._hash_file(row["path"]),
            "path_basename": os.path.basename(str(row["path"] or "")),
        }
        return json.dumps(payload, sort_keys=True, default=str)

    @staticmethod
    def _files_match(left: str, right: str) -> bool:
        if not (os.path.isfile(left) and os.path.isfile(right)):
            return False
        try:
            return NotesTool._hash_file(left) == NotesTool._hash_file(right)
        except Exception:
            return False

    def _copy_legacy_media_file(
        self,
        *,
        legacy_path: str,
        filename: Optional[str],
        source: Optional[str],
        session_id: Optional[str],
    ) -> str:
        legacy_path = str(legacy_path or "").strip()
        if not legacy_path or not os.path.isfile(legacy_path):
            return legacy_path

        target_dir = self._ensure_media_dir(source, session_id)
        safe_name = self._sanitize_filename(filename or os.path.basename(legacy_path))
        target_path = os.path.join(target_dir, safe_name)
        base, ext = os.path.splitext(safe_name)

        if os.path.abspath(legacy_path) == os.path.abspath(target_path):
            return target_path

        counter = 1
        while os.path.exists(target_path):
            if self._files_match(legacy_path, target_path):
                return target_path
            target_name = f"{base}_{counter}{ext}"
            target_path = os.path.join(target_dir, target_name)
            counter += 1

        raw = Path(legacy_path).read_bytes()
        write_secure_file(
            target_path,
            read_secure_file(legacy_path) if raw.startswith(SPM_FILE_HEADER) else raw,
        )
        return target_path

    def _migrate_legacy_database(self, legacy_db_path: Path) -> tuple[int, int]:
        imported_media = 0
        imported_notes = 0
        primary_db_path = Path(self._db_path)

        with sqlite3.connect(str(primary_db_path)) as dest_conn, sqlite3.connect(str(legacy_db_path)) as source_conn:
            dest_conn.row_factory = sqlite3.Row
            source_conn.row_factory = sqlite3.Row

            existing_media = {
                self._media_signature(row): {
                    "id": int(row["id"]),
                    "filename": row["filename"],
                    "mimetype": row["mimetype"],
                    "path": row["path"],
                }
                for row in dest_conn.execute(
                    """
                    SELECT id, filename, mimetype, path, size_bytes, source, user_id,
                           session_id, message_id, metadata
                    FROM media_attachments
                    """
                )
            }
            media_id_map: dict[int, Dict[str, Any]] = {}

            try:
                media_rows = source_conn.execute(
                    """
                    SELECT id, filename, mimetype, path, size_bytes, source, user_id,
                           session_id, message_id, metadata, created_at
                    FROM media_attachments
                    ORDER BY id ASC
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                media_rows = []

            for row in media_rows:
                signature = self._media_signature(row)
                existing = existing_media.get(signature)
                if existing is not None:
                    media_id_map[int(row["id"])] = dict(existing)
                    continue

                migrated_path = self._copy_legacy_media_file(
                    legacy_path=str(row["path"] or ""),
                    filename=row["filename"],
                    source=row["source"],
                    session_id=row["session_id"],
                )
                cursor = dest_conn.execute(
                    """
                    INSERT INTO media_attachments (
                        filename, mimetype, path, size_bytes, source, user_id,
                        session_id, message_id, metadata, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["filename"],
                        row["mimetype"],
                        migrated_path,
                        row["size_bytes"],
                        row["source"],
                        row["user_id"],
                        row["session_id"],
                        row["message_id"],
                        row["metadata"],
                        row["created_at"],
                    ),
                )
                imported_media += 1
                migrated = {
                    "id": int(cursor.lastrowid),
                    "filename": row["filename"],
                    "mimetype": row["mimetype"],
                    "path": migrated_path,
                }
                media_id_map[int(row["id"])] = dict(migrated)
                existing_media[signature] = dict(migrated)

            existing_notes = {
                self._note_signature(row)
                for row in dest_conn.execute(
                    """
                    SELECT id, title, content, tags, category, metadata, created_at, updated_at
                    FROM notes
                    """
                )
            }

            try:
                note_rows = source_conn.execute(
                    """
                    SELECT id, title, content, tags, category, metadata, created_at, updated_at
                    FROM notes
                    ORDER BY id ASC
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                note_rows = []

            for row in note_rows:
                metadata_obj = self._safe_json_loads(row["metadata"], {})
                attachments = metadata_obj.get("media_attachments")
                if isinstance(attachments, list):
                    rewritten_attachments = []
                    for attachment in attachments:
                        if not isinstance(attachment, dict):
                            rewritten_attachments.append(attachment)
                            continue
                        mapped = None
                        try:
                            mapped = media_id_map.get(int(attachment.get("id")))
                        except Exception:
                            mapped = None
                        if mapped is None:
                            rewritten_attachments.append(attachment)
                            continue
                        rewritten_attachments.append(
                            {
                                **attachment,
                                "id": mapped["id"],
                                "filename": mapped["filename"],
                                "path": mapped["path"],
                                "mimetype": mapped["mimetype"],
                            }
                        )
                    metadata_obj["media_attachments"] = rewritten_attachments

                note_signature = json.dumps(
                    {
                        "title": row["title"],
                        "content": row["content"],
                        "tags": self._safe_json_loads(row["tags"], []),
                        "category": row["category"],
                        "metadata": metadata_obj,
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    },
                    sort_keys=True,
                    default=str,
                )
                if note_signature in existing_notes:
                    continue

                dest_conn.execute(
                    """
                    INSERT INTO notes (
                        title, content, tags, category, metadata, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["title"],
                        row["content"],
                        row["tags"],
                        row["category"],
                        json.dumps(metadata_obj, sort_keys=True),
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
                existing_notes.add(note_signature)
                imported_notes += 1

            dest_conn.commit()

        return (imported_media, imported_notes)

    def _migrate_legacy_databases(self) -> None:
        primary_db_path = Path(self._db_path).expanduser().resolve()
        for legacy_db_path in self._legacy_db_candidates(primary_db_path):
            notes_count, media_count = self._database_counts(legacy_db_path)
            if notes_count == 0 and media_count == 0:
                continue
            try:
                imported_media, imported_notes = self._migrate_legacy_database(legacy_db_path)
                if imported_media or imported_notes:
                    LOGGER.info(
                        "Migrated NotesTool data from %s into %s (media=%d, notes=%d)",
                        legacy_db_path,
                        primary_db_path,
                        imported_media,
                        imported_notes,
                    )
            except Exception as exc:
                LOGGER.warning(
                    "Failed to migrate NotesTool database from %s into %s: %s",
                    legacy_db_path,
                    primary_db_path,
                    exc,
                )

    # ----------------------------
    # Database initialization
    # ----------------------------
    def _ensure_storage_readable(self) -> None:
        """Attach to the Maximus boundary, or fail with a message that explains why.

        A sealed database opened by a plaintext reader raises SQLite's
        "file is not a database", which reads as corruption and sends anyone
        debugging it in the wrong direction. Detect the envelope explicitly:
        attach to the inherited boundary when one is configured, otherwise say
        that the store is sealed and name the file.
        """
        db_path = Path(self._db_path)
        if not db_path.is_file():
            return
        try:
            if ensure_secure_storage_for_path(db_path):
                return
        except SecureStorageError as exc:
            raise NotesStorageSealedError(
                f"The notes database {db_path.name} is sealed by Secure Professional Maximus "
                f"storage and the boundary could not be attached: {exc}"
            ) from exc

        kind = sealed_envelope_kind(db_path)
        if kind is None:
            return
        raise NotesStorageSealedError(
            f"The notes database {db_path.name} is sealed by Secure Professional Maximus "
            "storage, but that storage mode is not active in this process. Re-enable Secure "
            "Professional Maximus to unseal it, or move the file aside to start a new notes "
            f"database. Path: {db_path}"
        )

    def _init_database(self) -> None:
        """Initialize the SQLite database with required tables if not exists.

        Ensures both `media_attachments` and `notes` tables exist. Adds a
        `metadata` column to `notes` if missing, used to store linkage to
        media attachment records.
        """
        self._ensure_storage_readable()
        try:
            with sqlite3.connect(self._db_path) as conn:
                # Media attachments table
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS media_attachments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL,
                        mimetype TEXT,
                        path TEXT NOT NULL,
                        size_bytes INTEGER,
                        source TEXT,
                        user_id TEXT,
                        session_id TEXT,
                        message_id TEXT,
                        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
                        metadata TEXT
                    )
                    """
                )

                # Notes table for CRUD operations (now includes metadata TEXT)
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        content TEXT,
                        tags TEXT,           -- JSON array of strings
                        category TEXT,
                        metadata TEXT,       -- JSON object for extended info (e.g., media links)
                        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
                        updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))
                    )
                    """
                )

                # Migration: ensure `metadata` column exists on `notes`
                try:
                    cur = conn.execute("PRAGMA table_info(notes)")
                    cols = [row[1] for row in cur.fetchall()]
                    if "metadata" not in cols:
                        conn.execute("ALTER TABLE notes ADD COLUMN metadata TEXT")
                except Exception:
                    # If PRAGMA or ALTER fails silently, continue; creation path covers new DBs
                    pass

                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_notes_updated_at ON notes(updated_at DESC, id DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at DESC, id DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_notes_category ON notes(category)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_media_attachments_created_at ON media_attachments(created_at DESC, id DESC)"
                )

                # Ensure all note timestamps are populated and normalized to local time.
                conn.execute(
                    """
                    UPDATE notes
                    SET updated_at = COALESCE(NULLIF(updated_at, ''), created_at, ?)
                    WHERE updated_at IS NULL OR TRIM(updated_at) = ''
                    """,
                    (self._local_timestamp(),),
                )
                conn.execute(
                    """
                    UPDATE notes
                    SET created_at = COALESCE(NULLIF(created_at, ''), updated_at, ?)
                    WHERE created_at IS NULL OR TRIM(created_at) = ''
                    """,
                    (self._local_timestamp(),),
                )

                self._normalize_timestamp_column(conn, "notes", "created_at")
                self._normalize_timestamp_column(conn, "notes", "updated_at")
                self._normalize_timestamp_column(conn, "media_attachments", "created_at")
        except Exception as exc:
            raise RuntimeError(f"Failed to initialize notes database: {exc}")

    def _normalize_timestamp_column(self, conn: sqlite3.Connection, table_name: str, column_name: str) -> None:
        rows = conn.execute(
            f"""
            SELECT id, {column_name}
            FROM {table_name}
            WHERE {column_name} IS NOT NULL
              AND TRIM({column_name}) != ''
            """
        ).fetchall()
        updates: list[tuple[str, int]] = []
        for row_id, raw_value in rows:
            normalized = self._normalize_stored_timestamp(raw_value)
            if normalized and normalized != str(raw_value):
                updates.append((str(normalized), int(row_id)))
        if updates:
            conn.executemany(
                f"UPDATE {table_name} SET {column_name} = ? WHERE id = ?",
                updates,
            )

    # ----------------------------
    # Media helpers
    # ----------------------------
    @staticmethod
    def _sanitize_path_component(value: Optional[str], fallback: str) -> str:
        raw = str(value or "").replace("\x00", "").strip()
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", raw).strip(" ._")
        return (cleaned or fallback)[:120]

    def _ensure_media_dir(self, source: Optional[str], session_id: Optional[str]) -> str:
        """Ensure media directory exists and return the target directory path.

        Directory structure: <module_dir>/media/<source>/<session_id>
        Missing parts are skipped when not provided.
        """
        media_root = self._storage_dir / self._MEDIA_SUBDIR
        current_path = media_root
        if source:
            current_path = current_path / self._sanitize_path_component(source, "unknown_source")
        if session_id:
            current_path = current_path / self._sanitize_path_component(session_id, "unknown_session")

        target_dir = str(current_path)
        os.makedirs(target_dir, exist_ok=True)
        return target_dir

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Sanitize filename to avoid path traversal and illegal characters."""
        raw = str(filename or "").replace("\x00", "")
        name = os.path.basename(raw.replace("\\", "/"))
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
        if not name:
            return "attachment"
        stem = os.path.splitext(name)[0].upper()
        if stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", stem):
            name = f"_{name}"
        return name

    @staticmethod
    def _decode_base64_data(data_base64: str) -> bytes:
        """Decode base64 data robustly, supporting optional data URI prefix.

        - Strips whitespace and newlines
        - Auto-pads to a multiple of 4 characters
        - Tries strict decode first, then relaxed when needed
        """
        if not isinstance(data_base64, str) or not data_base64:
            raise ValueError("data_base64 must be a non-empty string")
        # Extract payload from data URL if present
        s = data_base64.strip()
        if s.lower().startswith("data:"):
            try:
                _, s = s.split(",", 1)
            except Exception:
                # If parsing fails, treat the whole string as payload
                s = s
        # Normalize: remove whitespace/newlines
        b64 = "".join(s.split())
        if not b64:
            raise ValueError("empty base64 payload")
        # Auto-pad to multiple of 4
        rem = len(b64) % 4
        if rem:
            b64 += "=" * (4 - rem)
        # Decode: try strict, then relaxed
        try:
            return base64.b64decode(b64, validate=True)
        except Exception:
            try:
                return base64.b64decode(b64, validate=False)
            except Exception as exc:
                raise ValueError(f"Invalid base64 data: {exc}")

    # ----------------------------
    # Public API
    # ----------------------------
    def _build_notes_filters(
        self,
        *,
        category: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        created_on: Optional[str] = None,
        query: Optional[str] = None,
        cursor_updated_at: Optional[str] = None,
        cursor_note_id: Optional[int] = None,
        descending: bool = True,
    ) -> tuple[list[str], list[Any]]:
        where: list[str] = []
        params: list[Any] = []
        if category:
            where.append("category = ?")
            params.append(category)
        if created_on:
            where.append("date(created_at) = date(?)")
            params.append(created_on)
        else:
            if created_after:
                where.append("date(created_at) >= date(?)")
                params.append(created_after)
            if created_before:
                where.append("date(created_at) <= date(?)")
                params.append(created_before)
        if query:
            search_value = f"%{str(query).strip()}%"
            where.append(
                """
                (
                    title LIKE ?
                    OR content LIKE ?
                    OR COALESCE(tags, '') LIKE ?
                    OR COALESCE(category, '') LIKE ?
                )
                """.strip()
            )
            params.extend([search_value, search_value, search_value, search_value])
        if cursor_updated_at and cursor_note_id is not None:
            # The cursor is the last row already returned, so continue past it
            # in whichever direction the listing is ordered.
            comparison = "<" if descending else ">"
            where.append(f"(updated_at {comparison} ? OR (updated_at = ? AND id {comparison} ?))")
            params.extend([cursor_updated_at, cursor_updated_at, int(cursor_note_id)])
        return where, params

    def count_notes(
        self,
        *,
        category: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        created_on: Optional[str] = None,
        query: Optional[str] = None,
    ) -> int:
        where, params = self._build_notes_filters(
            category=category,
            created_after=created_after,
            created_before=created_before,
            created_on=created_on,
            query=query,
        )
        sql = "SELECT COUNT(*) FROM notes" + (" WHERE " + " AND ".join(where) if where else "")
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(sql, params).fetchone()
            return int(row[0] or 0) if row else 0

    def list_categories(self, limit: int = 50) -> list[Dict[str, Any]]:
        """Return non-empty categories with their note counts, most used first."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT category, COUNT(*) AS note_count
                FROM notes
                WHERE category IS NOT NULL AND TRIM(category) != ''
                GROUP BY category
                ORDER BY note_count DESC, category COLLATE NOCASE ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [{"name": str(row[0]), "count": int(row[1] or 0)} for row in rows]

    def create_note(
        self,
        title: str,
        content: str,
        tags: Optional[list[str]] = None,
        category: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new note and return its identifier.

        Args:
            title: Note title.
            content: Note content/body.
            tags: Optional list of tags.
            category: Optional category name.
            metadata: Optional JSON-serializable object with extra info.

        Returns:
            Dict with `success`, and `note_id` when successful.
        """
        if not title:
            return {"success": False, "error": "title is required"}
        try:
            tags_json = json.dumps(tags or [])
            meta_json = json.dumps(metadata or {})
            now = self._local_timestamp()
            with sqlite3.connect(self._db_path) as conn:
                cur = conn.execute(
                    """
                    INSERT INTO notes (
                        title, content, tags, category, metadata, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (title, content, tags_json, category, meta_json, now, now),
                )
                note_id = cur.lastrowid
            return {"success": True, "note_id": int(note_id)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def search_notes(self, query: str, limit: Optional[int] = None) -> list[Dict[str, Any]]:
        """Search notes by title/content substring match."""
        if not query:
            return []
        lim = int(limit or self.DEFAULT_LIST_LIMIT)
        return self.list_notes(limit=lim, query=query)

    def list_notes(
        self,
        *,
        category: Optional[str] = None,
        limit: Optional[int] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        created_on: Optional[str] = None,
        include_content: bool = True,
        include_metadata: bool = True,
        query: Optional[str] = None,
        cursor_updated_at: Optional[str] = None,
        cursor_note_id: Optional[int] = None,
        descending: bool = True,
    ) -> list[Dict[str, Any]]:
        """List notes with optional category and date filters.

        Date filters accept `YYYY-MM-DD` and operate on `created_at` timestamp.
        Results are ordered by last update, newest first unless `descending`
        is False.
        """
        lim = int(limit or self.DEFAULT_LIST_LIMIT)
        where, params = self._build_notes_filters(
            category=category,
            created_after=created_after,
            created_before=created_before,
            created_on=created_on,
            query=query,
            cursor_updated_at=cursor_updated_at,
            cursor_note_id=cursor_note_id,
            descending=descending,
        )

        select_columns = ["id", "title"]
        if include_content:
            select_columns.append("content")
        else:
            select_columns.append("substr(content, 1, 512) AS content")
        select_columns.extend(["tags", "category", "created_at", "updated_at"])
        if include_metadata:
            select_columns.append("metadata")

        direction = "DESC" if descending else "ASC"
        sql = (
            f"SELECT {', '.join(select_columns)} FROM notes"
            + (" WHERE " + " AND ".join(where) if where else "")
            + f" ORDER BY updated_at {direction}, id {direction} LIMIT ?"
        )
        params.append(lim)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
        return [self._row_to_note(r) for r in rows]

    def get_note(self, note_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a note by its identifier."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT id, title, content, tags, category, metadata, created_at, updated_at FROM notes WHERE id = ?",
                (int(note_id),),
            )
            row = cur.fetchone()
        return self._row_to_note(row) if row else None

    def update_note(
        self,
        *,
        note_id: int,
        title: Optional[str] = None,
        content: Optional[str] = None,
        tags: Optional[list[str]] = None,
        category: Optional[str] = None,
    ) -> bool:
        """Update a note. Returns True if a row was modified."""
        sets = []
        params: list[Any] = []
        if title is not None:
            sets.append("title = ?")
            params.append(title)
        if content is not None:
            sets.append("content = ?")
            params.append(content)
        if tags is not None:
            sets.append("tags = ?")
            params.append(json.dumps(tags))
        if category is not None:
            sets.append("category = ?")
            params.append(category)
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.append(self._local_timestamp())
        params.append(int(note_id))
        sql = f"UPDATE notes SET {', '.join(sets)} WHERE id = ?"
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(sql, params)
            return cur.rowcount > 0

    def append_media_attachment_to_note(
        self,
        *,
        note_id: int,
        attachment: Dict[str, Any],
        content: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Attach a saved media row to an existing note's metadata."""
        note = self.get_note(note_id)
        if not note:
            return {"success": False, "error": f"note {note_id} not found"}

        metadata = note.get("metadata") if isinstance(note.get("metadata"), dict) else {}
        metadata = dict(metadata or {})
        attachments = metadata.get("media_attachments")
        if not isinstance(attachments, list):
            attachments = []
        attachments.append(
            {
                "id": attachment.get("id"),
                **({"client_media_id": attachment["client_media_id"]} if attachment.get("client_media_id") else {}),
                "filename": attachment.get("filename"),
                "path": attachment.get("path"),
                "mimetype": attachment.get("mimetype"),
                "size_bytes": attachment.get("size_bytes"),
            }
        )
        metadata["media_attachments"] = attachments

        clean_content = str(content or "").strip()
        note_content = str(note.get("content") or "")
        updated_content = None
        if clean_content:
            updated_content = f"{note_content.rstrip()}\n\n{clean_content}".strip()

        sets = ["metadata = ?", "updated_at = ?"]
        params: list[Any] = [json.dumps(metadata), self._local_timestamp()]
        if updated_content is not None:
            sets.insert(0, "content = ?")
            params.insert(0, updated_content)
        params.append(int(note_id))

        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(f"UPDATE notes SET {', '.join(sets)} WHERE id = ?", params)
            if cur.rowcount <= 0:
                return {"success": False, "error": f"note {note_id} not found"}

        return {"success": True, "note_id": int(note_id), "media_id": attachment.get("id")}

    def delete_note(self, note_id: int) -> bool:
        """Delete a note by its identifier."""
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute("DELETE FROM notes WHERE id = ?", (int(note_id),))
            return cur.rowcount > 0

    # ----------------------------
    # Internal helpers
    # ----------------------------
    @staticmethod
    def _row_to_note(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        # Safely access optional columns for robustness across SELECT variants
        if isinstance(row, sqlite3.Row):
            keys = set(row.keys())
            content_val = row["content"] if "content" in keys else ""
            category_val = row["category"] if "category" in keys else None
            created_at_val = row["created_at"] if "created_at" in keys else None
            updated_at_val = row["updated_at"] if "updated_at" in keys else None
            tags_val = row["tags"] if "tags" in keys else None
            meta_val = row["metadata"] if "metadata" in keys else None
        else:
            content_val = row and row.get("content", "")
            category_val = row and row.get("category")
            created_at_val = row and row.get("created_at")
            updated_at_val = row and row.get("updated_at")
            tags_val = row and row.get("tags")
            meta_val = row and row.get("metadata")
        try:
            tags_list = json.loads(tags_val) if tags_val else []
        except Exception:
            tags_list = []
        try:
            meta_obj = json.loads(meta_val) if meta_val else {}
        except Exception:
            meta_obj = {}
        return {
            "id": int(row["id"]),
            "title": row["title"],
            "content": content_val or "",
            "tags": tags_list,
            "category": category_val,
            "metadata": meta_obj,
            "created_at": created_at_val,
            "updated_at": updated_at_val,
        }

    def save_media_attachment(
        self,
        filename: str,
        data_base64: str,
        mimetype: Optional[str] = None,
        *,
        source: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist an attachment to disk and record metadata in the database.

        Returns a dictionary with saved file information, including database row id.
        """
        if not filename:
            raise ValueError("filename must be provided")
        if not data_base64:
            raise ValueError("data_base64 must be provided")

        content = self._decode_base64_data(data_base64)

        target_dir = self._ensure_media_dir(source, session_id)
        safe_name = self._sanitize_filename(filename)
        # If no extension present, prefer a sensible one based on mimetype
        base_current, ext_current = os.path.splitext(safe_name)
        # from __debug_provenance_e__ import pay
        if not ext_current and mimetype:
            mt = (mimetype or "").lower()
            mapping = {
                "image/jpeg": ".jpg",
                "image/jpg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
                "image/gif": ".gif",
                "video/mp4": ".mp4",
                "audio/mpeg": ".mp3",
                "application/pdf": ".pdf",
                "text/plain": ".txt",
            }
            ext_pref = mapping.get(mt, "")
            if ext_pref:
                safe_name = f"{safe_name}{ext_pref}"
        file_path = os.path.join(target_dir, safe_name)

        base, ext = os.path.splitext(safe_name)
        counter = 1
        while os.path.exists(file_path):
            safe_name = f"{base}_{counter}{ext}"
            file_path = os.path.join(target_dir, safe_name)
            counter += 1

        write_secure_file(file_path, content)

        meta_json = json.dumps(metadata or {})
        created_at = self._local_timestamp()

        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO media_attachments (
                    filename, mimetype, path, size_bytes, source, user_id,
                    session_id, message_id, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    safe_name,
                    mimetype,
                    file_path,
                    len(content),
                    source,
                    user_id,
                    session_id,
                    message_id,
                    meta_json,
                    created_at,
                ),
            )
            row_id = cur.lastrowid

        return {
            "id": row_id,
            "filename": safe_name,
            "mimetype": mimetype,
            "path": file_path,
            "size_bytes": len(content),
            "source": source,
            "user_id": user_id,
            "session_id": session_id,
            "message_id": message_id,
        }

    def save_media_attachment_from_path(
        self,
        path: str,
        filename: Optional[str] = None,
        mimetype: Optional[str] = None,
        *,
        source: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist an attachment from a local filesystem path and record metadata.

        Copies bytes from the provided `path` into the NotesTool media directory,
        ensuring a unique, sanitized filename. Records the final persisted path
        and returns metadata including the database row id.
        """
        if not path or not os.path.isfile(path):
            raise ValueError("path must point to an existing file")

        # Read bytes
        raw = Path(path).read_bytes()
        content = read_secure_file(path) if raw.startswith(SPM_FILE_HEADER) else raw

        # Decide filename: provided override or basename of source path
        src_name = os.path.basename(path)
        safe_name = self._sanitize_filename(filename or src_name)
        # If an override was provided without extension, borrow from source or mimetype
        base_current, ext_current = os.path.splitext(safe_name)
        if not ext_current:
            src_ext = os.path.splitext(src_name)[1]
            if src_ext:
                safe_name = f"{safe_name}{src_ext}"
            elif mimetype:
                mt = (mimetype or "").lower()
                mapping = {
                    "image/jpeg": ".jpg",
                    "image/jpg": ".jpg",
                    "image/png": ".png",
                    "image/webp": ".webp",
                    "image/gif": ".gif",
                    "video/mp4": ".mp4",
                    "audio/mpeg": ".mp3",
                    "application/pdf": ".pdf",
                    "text/plain": ".txt",
                }
                ext_pref = mapping.get(mt, "")
                if ext_pref:
                    safe_name = f"{safe_name}{ext_pref}"

        target_dir = self._ensure_media_dir(source, session_id)
        file_path = os.path.join(target_dir, safe_name)

        base, ext = os.path.splitext(safe_name)
        counter = 1
        while os.path.exists(file_path):
            safe_name = f"{base}_{counter}{ext}"
            file_path = os.path.join(target_dir, safe_name)
            counter += 1

        write_secure_file(file_path, content)

        meta_json = json.dumps(metadata or {})
        created_at = self._local_timestamp()

        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO media_attachments (
                    filename, mimetype, path, size_bytes, source, user_id,
                    session_id, message_id, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    safe_name,
                    mimetype,
                    file_path,
                    len(content),
                    source,
                    user_id,
                    session_id,
                    message_id,
                    meta_json,
                    created_at,
                ),
            )
            row_id = cur.lastrowid

        return {
            "id": row_id,
            "filename": safe_name,
            "mimetype": mimetype,
            "path": file_path,
            "size_bytes": len(content),
            "source": source,
            "user_id": user_id,
            "session_id": session_id,
            "message_id": message_id,
        }

    def ingest_attachments(
        self,
        attachments: list[dict[str, Any]],
        *,
        route_to: Optional[str] = None,
        source: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        default_title: Optional[str] = None,
        default_content: Optional[str] = None,
        append_to_note_id: Optional[int] = None,
        append_to_recent: bool = False,
    ) -> dict[str, Any]:
        """Ingest a list of attachments and persist them either as notes media or
        route them to the Page feed as blobs.

        Parameters
        ----------
        attachments: list[dict[str, Any]]
            Each attachment may contain keys like `filename`, `data` (base64), `mimetype`,
            and arbitrary metadata such as `size_bytes`, `id`, etc.
        route_to: Optional[str]
            Routing hint: "notes" to persist in notes media, "page" to add blobs
            into the page feed, or None/"auto" to decide based on mimetype.
        source: Optional[str]
            Source label (e.g., "telegram", "signal", "whatsapp").
        user_id: Optional[str]
            User identifier for attribution.
        session_id: Optional[str]
            External session identifier for directory scoping.
        message_id: Optional[str]
            Original upstream message id for traceability.

        Returns
        -------
        dict[str, Any]
            Summary with lists of saved items and any errors.
        """
        saved_notes: list[dict[str, Any]] = []
        created_notes: list[dict[str, Any]] = []
        appended_notes: list[dict[str, Any]] = []
        saved_page: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[str] = []

        route_norm = (route_to or "auto").strip().lower()
        append_target_note: Optional[Dict[str, Any]] = None
        if route_norm == "notes" and (append_to_note_id is not None or append_to_recent):
            if append_to_note_id is not None:
                append_target_note = self.get_note(int(append_to_note_id))
                if append_target_note is None:
                    errors.append(f"Note {append_to_note_id} was not found; creating a new note instead.")
            else:
                recent_notes = self.list_notes(limit=1)
                append_target_note = recent_notes[0] if recent_notes else None
                if append_target_note is None:
                    skipped.append({"reason": "no_recent_note_to_append"})

        def _append_to_target_if_requested(saved: Dict[str, Any], note_content: Optional[str]) -> bool:
            if append_target_note is None:
                return False
            result = self.append_media_attachment_to_note(
                note_id=int(append_target_note["id"]),
                attachment=saved,
                content=note_content,
            )
            if result.get("success"):
                appended_notes.append({"note_id": result.get("note_id"), "media_id": result.get("media_id")})
                return True
            errors.append(
                f"Append to note {append_target_note.get('id')} failed for "
                f"'{saved.get('filename')}': {result.get('error')}"
            )
            return False

        def _kind_from_mime(mime: Optional[str]) -> str:
            m = (mime or "").lower()
            if m.startswith("image/"):
                return "image"
            if m.startswith("video/"):
                return "video"
            if m.startswith("audio/"):
                return "audio"
            return "blob"

        # Lazy import to avoid hard dependency when not routing to page
        page_tool = None
        if route_norm == "page":
            try:
                from autoyou_agents.page_agent.page_tool import PageTool  # type: ignore
                page_tool = PageTool()
            except Exception as e:
                errors.append(f"Failed to initialize PageTool: {e}")

        for att in attachments or []:
            try:
                if not isinstance(att, dict):
                    skipped.append({"reason": "non_dict", "value": str(att)})
                    continue
                filename = str(att.get("filename") or "attachment")
                data_b64 = att.get("data")  # base64 bytes expected
                path = att.get("path")
                mimetype = att.get("mimetype")
                meta = {k: v for k, v in att.items() if k not in {"filename", "mimetype", "data"}}
                caption = str(att.get("caption") or "").strip()

                # Prefer path-backed attachments when available
                if path and not data_b64:
                    # Decide routing for path-backed attachments
                    kind = _kind_from_mime(mimetype)
                    go_page = (route_norm == "page") or (route_norm == "auto" and kind in {"video", "audio"})

                    if go_page:
                        # Initialize PageTool on-demand if not already
                        if page_tool is None:
                            try:
                                from autoyou_agents.page_agent.page_tool import PageTool  # type: ignore
                                page_tool = PageTool()
                            except Exception as e:
                                errors.append(f"Failed to initialize PageTool: {e}")
                                skipped.append({"reason": "no_page_tool", "filename": filename, "path": path})
                                continue
                        # Use add_blob_from_path for proper blob registration
                        try:
                            res = page_tool.add_blob_from_path(
                                path=str(path),
                                filename=filename,
                                mimetype=mimetype,
                                source=source,
                                user_id=user_id,
                                session_id=session_id,
                                message_id=message_id,
                                metadata=meta,
                                title=filename,
                            )
                            if res.get("success"):
                                saved_page.append({"item": res.get("item"), "filename": filename, "path": str(path)})
                            else:
                                errors.append(f"Page add_blob_from_path failed for '{filename}' @ {path}: {res.get('error')}")
                        except Exception as e:
                            errors.append(f"Page add_blob_from_path exception for '{filename}' @ {path}: {e}")
                        # Path-backed attachment has been handled; proceed to next
                        continue
                    else:
                        # Persist into notes storage from path
                        try:
                            saved = self.save_media_attachment_from_path(
                                path=str(path),
                                filename=filename,
                                mimetype=mimetype,
                                source=source,
                                user_id=user_id,
                                session_id=session_id,
                                message_id=message_id,
                                metadata=meta,
                            )
                            saved_notes.append(saved)
                            # Auto-create a note linked to this media attachment
                            # Prefer a provided note title if present in attachment payload, else use caption or defaults
                            title = str(att.get("note_title") or att.get("title") or "").strip()
                            if not title:
                                # Derive from caption if present: use first segment before comma/pipe
                                if caption:
                                    import re as _re
                                    parts = _re.split(r"[,|]\\s*", caption, maxsplit=1)
                                    title = (parts[0].strip() if parts and parts[0] else caption[:80].strip())
                                else:
                                    title = (default_title or os.path.splitext(filename)[0] or "attachment")
                            # Choose content: prefer explicit note_content, else caption, else default
                            note_content = str(att.get("note_content") or "").strip()
                            if not note_content:
                                if caption:
                                    import re as _re
                                    parts = _re.split(r"[,|]\\s*", caption, maxsplit=1)
                                    if len(parts) == 2 and parts[1].strip():
                                        note_content = parts[1].strip()
                                    else:
                                        note_content = caption
                                else:
                                    note_content = (
                                        default_content
                                        or (
                                            f"Saved attachment '{filename}' from {source or 'unknown source'} "
                                            f"(session={session_id or 'n/a'}) at path {saved.get('path')}"
                                        )
                                    )
                            explicit_note_content = str(att.get("note_content") or "").strip() or default_content
                            if _append_to_target_if_requested(saved, explicit_note_content):
                                continue
                            note_meta = {
                                "media_attachments": [
                                    {
                                        "id": saved.get("id"),
                                        "filename": saved.get("filename"),
                                        "path": saved.get("path"),
                                        "mimetype": saved.get("mimetype"),
                                    }
                                ],
                                "source": source,
                                "user_id": user_id,
                                "session_id": session_id,
                                "message_id": message_id,
                            }
                            cr = self.create_note(title=title, content=note_content, metadata=note_meta)
                            if cr.get("success"):
                                created_notes.append({"note_id": cr.get("note_id"), "media_id": saved.get("id")})
                            else:
                                errors.append(f"Auto note creation failed for '{filename}': {cr.get('error')}")
                        except Exception as e:
                            errors.append(f"Auto note creation exception for '{filename}': {e}")
                        # Path-backed attachment has been handled; proceed to next
                        continue

                # If no inline data present and no path, attempt to route URL-only items to page feed
                if not data_b64:
                    url = str(att.get("url") or "").strip()
                    if url and route_norm in ("page", "auto"):
                        # Initialize PageTool on-demand if not already
                        if page_tool is None:
                            try:
                                from autoyou_agents.page_agent.page_tool import PageTool  # type: ignore
                                page_tool = PageTool()
                            except Exception as e:
                                errors.append(f"Failed to initialize PageTool: {e}")
                                skipped.append({"reason": "no_page_tool", "filename": filename, "url": url})
                                continue
                        item_type = _kind_from_mime(mimetype) if mimetype else None
                        r = page_tool.add_link(url=url, title=filename, source=(source or meta.get("source")), item_type=item_type)
                        if r.get("success"):
                            saved_page.append({"item": r.get("item"), "filename": filename, "url": url})
                        else:
                            errors.append(f"Page add_link failed for '{filename}': {r.get('error')}")
                    else:
                        skipped.append({"reason": "no_data", "filename": filename})
                    continue

                # Decide routing for data-backed attachments
                kind = _kind_from_mime(mimetype)
                go_page = (route_norm == "page") or (route_norm == "auto" and kind in {"video", "audio"})

                if go_page:
                    # Save bytes to disk first via notes storage (for a canonical path), then register in page feed
                    saved = self.save_media_attachment(
                        filename=filename,
                        data_base64=str(data_b64),
                        mimetype=mimetype,
                        source=source,
                        user_id=user_id,
                        session_id=session_id,
                        message_id=message_id,
                        metadata=meta,
                    )
                    saved_notes.append(saved)
                    if page_tool is None:
                        try:
                            from autoyou_agents.page_agent.page_tool import PageTool  # type: ignore
                            page_tool = PageTool()
                        except Exception as e:
                            errors.append(f"Failed to initialize PageTool: {e}")
                            continue
                    # Register blob from persisted path
                    try:
                        r = page_tool.add_blob_from_path(
                            path=str(saved.get("path")),
                            filename=filename,
                            mimetype=mimetype,
                            source=source,
                            user_id=user_id,
                            session_id=session_id,
                            message_id=message_id,
                            metadata=meta,
                            title=filename,
                        )
                        if r.get("success"):
                            saved_page.append({"item": r.get("item"), "filename": filename, "path": saved.get("path")})
                        else:
                            errors.append(f"Page add_blob_from_path failed for '{filename}': {r.get('error')}")
                    except Exception as e:
                        errors.append(f"Page add_blob_from_path exception for '{filename}': {e}")
                else:
                    saved = self.save_media_attachment(
                        filename=filename,
                        data_base64=str(data_b64),
                        mimetype=mimetype,
                        source=source,
                        user_id=user_id,
                        session_id=session_id,
                        message_id=message_id,
                        metadata=meta,
                    )
                    saved_notes.append(saved)
                    # Auto-create a note linked to this media attachment
                    try:
                        # Prefer a provided note title if present in attachment payload, else use caption or defaults
                        title = str(att.get("note_title") or att.get("title") or "").strip()
                        if not title:
                            if caption:
                                import re as _re
                                parts = _re.split(r"[,|]\\s*", caption, maxsplit=1)
                                title = (parts[0].strip() if parts and parts[0] else caption[:80].strip())
                            else:
                                title = (default_title or os.path.splitext(filename)[0] or "attachment")
                        note_meta = {
                            "media_attachments": [
                                {
                                    "id": saved.get("id"),
                                    "filename": saved.get("filename"),
                                    "path": saved.get("path"),
                                    "mimetype": saved.get("mimetype"),
                                }
                            ],
                            "source": source,
                            "user_id": user_id,
                            "session_id": session_id,
                            "message_id": message_id,
                        }
                        # Choose content: prefer explicit note_content, else caption, else default
                        note_content = str(att.get("note_content") or "").strip()
                        if not note_content:
                            if caption:
                                import re as _re
                                parts = _re.split(r"[,|]\\s*", caption, maxsplit=1)
                                if len(parts) == 2 and parts[1].strip():
                                    note_content = parts[1].strip()
                                else:
                                    note_content = caption
                            else:
                                note_content = (
                                    default_content
                                    or (
                                        f"Saved attachment '{filename}' from {source or 'unknown source'} "
                                        f"(session={session_id or 'n/a'}) at path {saved.get('path')}"
                                    )
                                )
                        explicit_note_content = str(att.get("note_content") or "").strip() or default_content
                        if _append_to_target_if_requested(saved, explicit_note_content):
                            continue
                        cr = self.create_note(title=title, content=note_content, metadata=note_meta)
                        if cr.get("success"):
                            created_notes.append({"note_id": cr.get("note_id"), "media_id": saved.get("id")})
                        else:
                            errors.append(f"Auto note creation failed for '{filename}': {cr.get('error')}")
                    except Exception as e:
                        errors.append(f"Auto note creation exception for '{filename}': {e}")
            except Exception as e:
                label = str(att.get("filename") or "attachment") if isinstance(att, dict) else "attachment"
                errors.append(f"Attachment ingest error for '{self._sanitize_filename(label)}': {e}")

        return {
            "success": True,
            "saved_notes": saved_notes,
            "created_notes": created_notes,
            "appended_notes": appended_notes,
            "saved_page": saved_page,
            "skipped": skipped,
            "errors": errors,
        }
