# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-6f7240bcd2ac6f328866c429

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
SQLite-backed persistence for the AutoYou Page feed.

Provides simple CRUD helpers to initialize the database, load items,
insert new items, delete by id, and clear the feed. Designed to work
closely with autoyou_page_service.py.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import sqlite3
import os
from typing import List, Dict, Any, Optional, TypedDict, Tuple
import uuid
from datetime import datetime, timedelta
from contextlib import suppress
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-pay-6f7240bcd2ac6f328866c429"


_X_HOST_ALIASES = {
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
    "m.twitter.com",
    "x.com",
    "www.x.com",
    "mobile.x.com",
    "m.x.com",
}

def _canonicalize_feed_type(item_type: Optional[str]) -> str:
    normalized = str(item_type or "").strip().lower()
    if normalized == "x":
        return "twitter"
    return normalized or "article"

def _canonicalize_x_url(url: Optional[str]) -> str:
    raw = str(url or "").strip()
    if not raw or raw.lower().startswith("blob://"):
        return raw
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").strip().lower()
        if host not in _X_HOST_ALIASES:
            return raw
        canonical = parsed._replace(
            scheme=parsed.scheme or "https",
            netloc="x.com",
            query="",
            fragment="",
        )
        return urlunparse(canonical)
    except Exception:
        return raw

def _canonicalize_source(source: Optional[str]) -> str:
    raw = str(source or "").strip()
    if not raw:
        return ""
    lower = raw.lower()
    if lower in _X_HOST_ALIASES:
        return "x.com"
    try:
        host = (urlparse(raw).hostname or "").strip().lower()
        if host in _X_HOST_ALIASES:
            return "x.com"
    except Exception:
        pass
    return raw

def _expand_type_aliases(types: Optional[List[str]]) -> List[str]:
    expanded: List[str] = []
    for item_type in (types or []):
        normalized = _canonicalize_feed_type(item_type)
        if not normalized:
            continue
        if normalized not in expanded:
            expanded.append(normalized)
        if normalized == "twitter" and "x" not in expanded:
            expanded.append("x")
    return expanded

def _expand_source_aliases(sources: Optional[List[str]]) -> List[str]:
    expanded: List[str] = []
    for source in (sources or []):
        normalized = _canonicalize_source(source)
        raw = str(source or "").strip()
        for candidate in (normalized, raw):
            candidate_norm = _canonicalize_source(candidate)
            if candidate_norm and candidate_norm not in expanded:
                expanded.append(candidate_norm)
        if raw and raw not in expanded:
            expanded.append(raw)
        if normalized == "x.com":
            for alias in sorted(_X_HOST_ALIASES):
                if alias not in expanded:
                    expanded.append(alias)
    return expanded

def _get_app_data_root() -> Path:
    """Get the application data root, handling both dev and compiled contexts."""
    try:
        from shared.platform_runtime import get_config_dir
        app_root = get_config_dir("AutoYou", anchor=__file__)
        return app_root
    except (ImportError, Exception):
        # Fallback: use script directory
        return Path(__file__).resolve().parent

class FeedItem(TypedDict):
    id: int
    type: str
    url: str
    title: str
    content: str
    source: str
    added_at: str
    favourite: bool

class FeedItemFull(FeedItem):
    tags: List[str]

class BlobMeta(TypedDict):
    id: str
    filename: str
    mimetype: str
    size: int
    path: str
    created_at: str

class PageFeedDB:
    """Thin wrapper around a SQLite database for feed item persistence.

    - Creates table `feed_items` on init if absent.
    - Exposes helpers to load, insert, delete and clear items.
    - Uses AUTOINCREMENT to ensure monotonically increasing ids.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path:
            self.db_path = db_path
        else:
            app_root = _get_app_data_root()
            self.db_path = str(app_root / "page_feed.db")
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """Create a SQLite connection."""
        con = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        with suppress(Exception):
            con.execute("PRAGMA busy_timeout = 30000")
        # Ensure ON DELETE CASCADE works for foreign keys
        with suppress(Exception):
            con.execute("PRAGMA foreign_keys = ON")
        return con

    def _init_db(self) -> None:
        """Initialize database and ensure required tables exist."""
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS feed_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT,
                    content TEXT NOT NULL DEFAULT '',
                    source TEXT,
                    added_at TEXT NOT NULL,
                    favourite INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # Add indexes for efficient queries
            con.execute("CREATE INDEX IF NOT EXISTS idx_feed_items_added_at ON feed_items(added_at)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_feed_items_type ON feed_items(type)")
            # Keyset pagination orders by (added_at, id) in either direction.
            con.execute("CREATE INDEX IF NOT EXISTS idx_feed_items_added_at_id ON feed_items(added_at, id)")

            # Perform lightweight migration to add 'favourite' if missing (older DBs)
            try:
                cur = con.execute("PRAGMA table_info(feed_items)")
                cols = [row[1] for row in cur.fetchall()]
                if 'favourite' not in cols:
                    con.execute("ALTER TABLE feed_items ADD COLUMN favourite INTEGER NOT NULL DEFAULT 0")
                if 'content' not in cols:
                    con.execute("ALTER TABLE feed_items ADD COLUMN content TEXT NOT NULL DEFAULT ''")
            except Exception:
                # If PRAGMA or ALTER fails, continue; fresh DBs already have the column
                pass

            # Tags mapping table
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS item_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL,
                    tag TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(item_id) REFERENCES feed_items(id) ON DELETE CASCADE
                )
                """
            )
            # Indexes and uniqueness for tags
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_item_tags_unique ON item_tags(item_id, tag)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_item_tags_item_id ON item_tags(item_id)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag)")

            # Blobs table: UUID primary keys for stronger security
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS blobs (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    mimetype TEXT,
                    size INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_blobs_created_at ON blobs(created_at)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_blobs_mimetype ON blobs(mimetype)")

            # Lightweight migration: if an older table exists with INTEGER id, migrate to TEXT UUID
            try:
                cur = con.execute("PRAGMA table_info(blobs)")
                cols = cur.fetchall()
                # cols: cid, name, type, notnull, dflt_value, pk
                id_type = next((row[2] for row in cols if row[1] == 'id'), '').upper()
                # If id is INTEGER (legacy), perform table migration
                if id_type.startswith('INTEGER'):
                    # Create a temporary table with desired schema
                    con.execute(
                        """
                        CREATE TABLE IF NOT EXISTS blobs_new (
                            id TEXT PRIMARY KEY,
                            filename TEXT NOT NULL,
                            mimetype TEXT,
                            size INTEGER NOT NULL,
                            path TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        )
                        """
                    )
                    # Copy rows with new UUID ids; update feed_items URLs accordingly
                    old_rows = con.execute("SELECT id, filename, mimetype, size, path, created_at FROM blobs").fetchall()
                    for r in old_rows:
                        new_id = str(uuid.uuid4())
                        con.execute(
                            "INSERT INTO blobs_new (id, filename, mimetype, size, path, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                            (new_id, str(r[1]), str(r[2] or ''), int(r[3] or 0), str(r[4]), str(r[5]))
                        )
                        # Update feed_items URL from blob://<old_int> to blob://<new_uuid>
                        try:
                            old_url = f"blob://{int(r[0])}"
                            new_url = f"blob://{new_id}"
                            con.execute("UPDATE feed_items SET url = ? WHERE url = ?", (new_url, old_url))
                        except Exception:
                            pass
                    # Replace old table
                    con.execute("DROP TABLE blobs")
                    con.execute("ALTER TABLE blobs_new RENAME TO blobs")
                    con.execute("CREATE INDEX IF NOT EXISTS idx_blobs_created_at ON blobs(created_at)")
                    con.execute("CREATE INDEX IF NOT EXISTS idx_blobs_mimetype ON blobs(mimetype)")
            except Exception:
                # If PRAGMA or migration fails, continue with existing schema
                pass

    def load_all(self) -> List[FeedItem]:
        """Load all feed items ordered by added_at DESC (latest first)."""
        with self._connect() as con:
            cur = con.execute(
                "SELECT id, type, url, title, content, source, added_at, favourite FROM feed_items ORDER BY added_at DESC"
            )
            rows = cur.fetchall()
        items: List[FeedItem] = []
        # from __debug_provenance_e__ import pay
        for r in rows:
            item_type = _canonicalize_feed_type(r[1])
            item_url = _canonicalize_x_url(r[2]) if item_type == "twitter" else str(r[2])
            item_source = _canonicalize_source(r[5]) if item_type == "twitter" else str(r[5] or "")
            items.append(
                FeedItem(
                    id=int(r[0]),
                    type=item_type,
                    url=item_url,
                    title=str(r[3] or ""),
                    content=str(r[4] or ""),
                    source=item_source,
                    added_at=str(r[6]),
                    favourite=bool(int(r[7] or 0)),
                )
            )
        return items

    def insert(self, item_type: str, url: str, title: Optional[str] = None, source: Optional[str] = None, content: str = "") -> FeedItem:
        """Insert a new item and return the created record with id."""
        item_type = _canonicalize_feed_type(item_type)
        url = _canonicalize_x_url(url) if item_type == "twitter" else str(url or "").strip()
        source = _canonicalize_source(source or self._source_from_url(url))
        added_at = datetime.now().isoformat()
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO feed_items (type, url, title, content, source, added_at, favourite) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (item_type, url, title or "", str(content or "")[:50000], source or "", added_at),
            )
            item_id = int(cur.lastrowid)
        return FeedItem(
            id=item_id,
            type=item_type,
            url=url,
            title=title or "",
            content=str(content or "")[:50000],
            source=source or "",
            added_at=added_at,
            favourite=False,
        )

    @staticmethod
    def _source_from_url(url: str) -> str:
        try:
            return urlparse(str(url or "").strip()).hostname or ""
        except Exception:
            return ""

    def delete(self, item_id: int) -> bool:
        """Delete an item by id. Returns True if a row was deleted."""
        with self._connect() as con:
            cur = con.execute("DELETE FROM feed_items WHERE id = ?", (item_id,))
            return cur.rowcount > 0

    def _extract_blob_id(self, url: str) -> Optional[str]:
        """Extract blob id from a feed URL like 'blob://<uuid>'."""
        try:
            u = (url or "").strip()
            if u.lower().startswith("blob://"):
                return u.split("://", 1)[1]
            return None
        except Exception:
            return None

    def get_item_url(self, item_id: int) -> Optional[str]:
        with self._connect() as con:
            cur = con.execute("SELECT url FROM feed_items WHERE id = ?", (item_id,))
            row = cur.fetchone()
            if not row:
                return None
            return str(row[0] or "")

    def get_item_title(self, item_id: int) -> Optional[str]:
        with self._connect() as con:
            cur = con.execute("SELECT title FROM feed_items WHERE id = ?", (item_id,))
            row = cur.fetchone()
            if not row:
                return None
            t = str(row[0] or "").strip()
            return t or None

    def update_blob_path_and_filename(self, blob_id: str, new_path: str, new_filename: Optional[str] = None) -> bool:
        with self._connect() as con:
            cur = con.execute(
                "UPDATE blobs SET path = ?, filename = COALESCE(?, filename) WHERE id = ?",
                (str(new_path), (str(new_filename) if new_filename else None), str(blob_id)),
            )
            return cur.rowcount > 0

    def delete_item_and_cleanup(self, item_id: int, uploads_dir: Optional[str] = None) -> Dict[str, Any]:
        """Delete an item and clean up related data.

        - Deletes the item from `feed_items`.
        - Ensures tags are removed (via FK cascade or explicit).
        - If the item references a local blob (blob://<id>) and no other items
          reference it, deletes the blob metadata and removes the file from disk.

        Returns a summary dict with counts and flags.
        """
        result: Dict[str, Any] = {
            "deleted": 0,
            "tags_deleted": 0,
            "blob_id": None,
            "blob_deleted": 0,
            "file_deleted": 0,
        }

        with self._connect() as con:
            # Fetch the item's URL before deletion to discover blob reference
            cur = con.execute("SELECT url FROM feed_items WHERE id = ?", (item_id,))
            row = cur.fetchone()
            if not row:
                return result
            url = str(row[0] or "")
            blob_id = self._extract_blob_id(url)
            result["blob_id"] = blob_id

            # Delete the item
            dcur = con.execute("DELETE FROM feed_items WHERE id = ?", (item_id,))
            result["deleted"] = int(dcur.rowcount or 0)

            # Explicit tag cleanup for compatibility if FK cascades are off
            with suppress(Exception):
                tcur = con.execute("DELETE FROM item_tags WHERE item_id = ?", (item_id,))
                result["tags_deleted"] = int(tcur.rowcount or 0)

            # If a blob was referenced, check if any remaining items reference it
            if blob_id:
                blob_url = f"blob://{blob_id}"
                cur2 = con.execute("SELECT COUNT(1) FROM feed_items WHERE url = ?", (blob_url,))
                (ref_count,) = cur2.fetchone() or (0,)
                if int(ref_count or 0) == 0:
                    # Retrieve blob path to delete file
                    cur3 = con.execute("SELECT path FROM blobs WHERE id = ?", (blob_id,))
                    row3 = cur3.fetchone()
                    path = str(row3[0]) if row3 and row3[0] else None
                    # Delete blob metadata
                    bcur = con.execute("DELETE FROM blobs WHERE id = ?", (blob_id,))
                    result["blob_deleted"] = int(bcur.rowcount or 0)
                    # Delete file on disk if exists
                    if path and os.path.exists(path):
                        with suppress(Exception):
                            os.remove(path)
                            result["file_deleted"] = 1
                    # Also consider uploads_dir cleanup in case of moved files
                    if not path and uploads_dir:
                        # Attempt to find likely file by prefix of blob id
                        with suppress(Exception):
                            for name in os.listdir(uploads_dir):
                                p = os.path.join(uploads_dir, name)
                                if os.path.isfile(p) and blob_id in name:
                                    os.remove(p)
                                    result["file_deleted"] = 1
                                    break

        return result

    def clear(self) -> int:
        """Delete all items. Returns number of rows deleted."""
        with self._connect() as con:
            cur = con.execute("DELETE FROM feed_items")
            return cur.rowcount or 0

    def clear_all_and_cleanup(self, uploads_dir: Optional[str] = None) -> Dict[str, Any]:
        """Clear the feed and perform comprehensive cleanup.

        - Deletes all rows in `feed_items` and `item_tags`.
        - Deletes all rows in `blobs` and removes files from `uploads_dir`.
        - Removes orphan files in `uploads_dir` that have no DB entries.

        Returns a summary dict with counts.
        """
        result: Dict[str, Any] = {
            "items_deleted": 0,
            "tags_deleted": 0,
            "blobs_deleted": 0,
            "files_deleted_count": 0,
        }

        with self._connect() as con:
            # Delete items and tags
            cur_items = con.execute("DELETE FROM feed_items")
            result["items_deleted"] = int(cur_items.rowcount or 0)
            with suppress(Exception):
                cur_tags = con.execute("DELETE FROM item_tags")
                result["tags_deleted"] = int(cur_tags.rowcount or 0)

            # Collect blob paths, then delete blob metadata
            blob_paths: List[str] = []
            with suppress(Exception):
                for (p,) in con.execute("SELECT path FROM blobs").fetchall():
                    if p:
                        blob_paths.append(str(p))
            cur_blobs = con.execute("DELETE FROM blobs")
            result["blobs_deleted"] = int(cur_blobs.rowcount or 0)

        # Delete blob files on disk
        for p in blob_paths:
            with suppress(Exception):
                if p and os.path.exists(p) and os.path.isfile(p):
                    os.remove(p)
                    result["files_deleted_count"] += 1

        # Clean up any orphan files left in uploads_dir
        if uploads_dir and os.path.isdir(uploads_dir):
            with suppress(Exception):
                for name in os.listdir(uploads_dir):
                    fp = os.path.join(uploads_dir, name)
                    if os.path.isfile(fp):
                        # If this file wasn't already deleted above, remove it now
                        if os.path.exists(fp):
                            try:
                                os.remove(fp)
                                result["files_deleted_count"] += 1
                            except Exception:
                                pass

        return result

    def reset_sequence(self) -> None:
        """Reset AUTOINCREMENT sequence so ids start again from 1 after clear.

        This relies on the presence of the `sqlite_sequence` table, which
        exists only when a table uses AUTOINCREMENT.
        """
        try:
            with self._connect() as con:
                con.execute("DELETE FROM sqlite_sequence WHERE name = ?", ("feed_items",))
        except Exception:
            # Silently ignore if sqlite_sequence doesn't exist
            pass

    def max_id(self) -> int:
        """Return the maximum id currently present, or 0 if none."""
        with self._connect() as con:
            cur = con.execute("SELECT COALESCE(MAX(id), 0) FROM feed_items")
            (max_id,) = cur.fetchone()
            return int(max_id or 0)

    # --- Blobs API ---

    def insert_blob(self, filename: str, mimetype: Optional[str], size: int, path: str) -> BlobMeta:
        """Insert a blob metadata record and return it with id.

        The file bytes are expected to already be stored on disk at `path`.
        """
        created_at = datetime.now().isoformat()
        new_id = str(uuid.uuid4())
        with self._connect() as con:
            con.execute(
                "INSERT INTO blobs (id, filename, mimetype, size, path, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (new_id, filename, mimetype or "", int(size or 0), path, created_at),
            )
        return BlobMeta(
            id=new_id,
            filename=str(filename),
            mimetype=str(mimetype or ""),
            size=int(size or 0),
            path=str(path),
            created_at=created_at,
        )

    def get_blob_meta(self, blob_id: str) -> Optional[BlobMeta]:
        """Fetch blob metadata by id."""
        with self._connect() as con:
            cur = con.execute("SELECT id, filename, mimetype, size, path, created_at FROM blobs WHERE id = ?", (str(blob_id),))
            row = cur.fetchone()
            if not row:
                return None
        return BlobMeta(
            id=str(row[0]),
            filename=str(row[1]),
            mimetype=str(row[2] or ""),
            size=int(row[3] or 0),
            path=str(row[4]),
            created_at=str(row[5]),
        )

    def get_blob_path_and_type(self, blob_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Return (path, mimetype) for a blob id, or (None, None) if missing."""
        with self._connect() as con:
            cur = con.execute("SELECT path, mimetype FROM blobs WHERE id = ?", (str(blob_id),))
            row = cur.fetchone()
            if not row:
                return (None, None)
            return (str(row[0]), str(row[1] or ""))

    def get_feed_title_by_blob_id(self, blob_id: str) -> Optional[str]:
        """Return the most recent feed item title referencing this blob id.

        Looks up `feed_items.url = 'blob://{blob_id}'` and returns the title, if present.
        """
        blob_url = f"blob://{blob_id}"
        with self._connect() as con:
            cur = con.execute(
                "SELECT title FROM feed_items WHERE url = ? ORDER BY added_at DESC LIMIT 1",
                (blob_url,),
            )
            row = cur.fetchone()
            if not row:
                return None
            title = str(row[0] or '').strip()
            return title or None

    # --- Favourites and Tags APIs ---

    def set_favourite(self, item_id: int, favourite: bool) -> bool:
        """Set or unset favourite flag for an item."""
        with self._connect() as con:
            cur = con.execute(
                "UPDATE feed_items SET favourite = ? WHERE id = ?",
                (1 if favourite else 0, item_id),
            )
            return cur.rowcount > 0

    def set_title(self, item_id: int, title: str) -> bool:
        """Update the title of a feed item.

        Returns True if a row was updated. Title is stored as-is, trimmed to
        avoid leading/trailing whitespace. Empty titles are allowed to support
        clearing a previously set title.
        """
        title_norm = (title or "").strip()
        with self._connect() as con:
            cur = con.execute(
                "UPDATE feed_items SET title = ? WHERE id = ?",
                (title_norm, item_id),
            )
            return cur.rowcount > 0

    def add_tag(self, item_id: int, tag: str, uploads_dir: Optional[str] = None) -> bool:
        tag_norm = (tag or "").strip()
        if not tag_norm:
            return False
        before_tags = self.list_tags(item_id)
        now = datetime.now().isoformat()
        with self._connect() as con:
            try:
                con.execute(
                    "INSERT OR IGNORE INTO item_tags (item_id, tag, created_at) VALUES (?, ?, ?)",
                    (item_id, tag_norm, now),
                )
            except Exception:
                return False
        if len(before_tags) == 0:
            url = self.get_item_url(item_id) or ""
            blob_id = self._extract_blob_id(url or "")
            if blob_id:
                path, mime = self.get_blob_path_and_type(blob_id)
                if path:
                    p = os.path.abspath(path)
                    root = None
                    if uploads_dir and os.path.isdir(uploads_dir):
                        root = uploads_dir
                    else:
                        d = os.path.dirname(p)
                        while True:
                            if os.path.basename(d) == "uploads":
                                root = d
                                break
                            nd = os.path.dirname(d)
                            if nd == d:
                                root = os.path.dirname(p)
                                break
                            d = nd
                    safe_tag = "".join([c if c.isalnum() or c in ".-_" else "_" for c in tag_norm])[:120] or "untagged"
                    target_dir = os.path.join(root, safe_tag)
                    try:
                        os.makedirs(target_dir, exist_ok=True)
                    except Exception:
                        target_dir = root
                    base = os.path.basename(p)
                    ext = os.path.splitext(base)[1]
                    title = self.get_item_title(item_id) or os.path.splitext(base)[0]
                    safe_title = "".join([c if c.isalnum() or c in ".-_" else "_" for c in title])[:120] or os.path.splitext(base)[0]
                    new_name = f"{safe_title}{ext}" if ext else safe_title
                    new_path = os.path.join(target_dir, new_name)
                    if os.path.abspath(new_path) != p:
                        if os.path.exists(new_path):
                            ts = str(int(datetime.now().timestamp() * 1000))
                            root_name, root_ext = os.path.splitext(new_name)
                            new_name = f"{root_name}_{ts}{root_ext}"
                            new_path = os.path.join(target_dir, new_name)
                        try:
                            os.replace(p, new_path)
                            self.update_blob_path_and_filename(blob_id, new_path, new_name)
                        except Exception:
                            pass
        return True

    def delete_tag(self, item_id: int, tag: str, uploads_dir: Optional[str] = None) -> bool:
        tag_norm = (tag or "").strip()
        if not tag_norm:
            return False
        deleted = False
        with self._connect() as con:
            cur = con.execute(
                "DELETE FROM item_tags WHERE item_id = ? AND tag = ?",
                (item_id, tag_norm),
            )
            deleted = cur.rowcount > 0
        if not deleted:
            return False
        remaining = self.list_tags(item_id)
        if len(remaining) == 0:
            url = self.get_item_url(item_id) or ""
            blob_id = self._extract_blob_id(url or "")
            if blob_id:
                path, _ = self.get_blob_path_and_type(blob_id)
                if path:
                    p = os.path.abspath(path)
                    base = os.path.basename(p)
                    root = None
                    if uploads_dir and os.path.isdir(uploads_dir):
                        root = uploads_dir
                    else:
                        d = os.path.dirname(p)
                        while True:
                            if os.path.basename(d) == "uploads":
                                root = d
                                break
                            nd = os.path.dirname(d)
                            if nd == d:
                                root = os.path.dirname(p)
                                break
                            d = nd
                    new_path = os.path.join(root, base)
                    if os.path.abspath(new_path) != p:
                        if os.path.exists(new_path):
                            ts = str(int(datetime.now().timestamp() * 1000))
                            stem, ext = os.path.splitext(base)
                            new_path = os.path.join(root, f"{stem}_{ts}{ext}")
                        try:
                            os.replace(p, new_path)
                            self.update_blob_path_and_filename(blob_id, new_path, base if os.path.basename(new_path) == base else os.path.basename(new_path))
                        except Exception:
                            pass
        return True

    def list_tags(self, item_id: int) -> List[str]:
        """List all tags for an item."""
        with self._connect() as con:
            cur = con.execute(
                "SELECT tag FROM item_tags WHERE item_id = ? ORDER BY tag ASC",
                (item_id,),
            )
            rows = cur.fetchall()
        return [str(r[0]) for r in rows]

    # --- Query API ---

    def query_items(
        self,
        order: str = "desc",
        types: Optional[List[str]] = None,
        source: Optional[str] = None,
        sources: Optional[List[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        favourites_only: bool = False,
        tag_search: Optional[str] = None,
        limit: Optional[int] = None,
        ignore_date_default: bool = False,
        cursor_added_at: Optional[str] = None,
        cursor_id: Optional[int] = None,
        stored_only: bool = False,
    ) -> List[FeedItemFull]:
        """Efficient query for feed items with optional filters and tags aggregation.

        - order: 'desc' or 'asc' by added_at
        - types: list of item types to include (twitter, instagram, article, video, youtube, tiktok)
        - sources: list of sources (OR semantics; e.g., ["Local", "x.com"]) to include
        - date_from/date_to: ISO-8601 strings; default is last 7 days if none provided
        - favourites_only: restrict to favourite items
        - tag_search: substring match on tags; uses LIKE for simplicity
        - limit: optional max number of results
        - cursor_added_at/cursor_id: the last row of the previous page; results
          continue after it in the requested order
        - stored_only: restrict to files kept on this computer (blob:// items)
        """
        where: List[str] = []
        params: List[Any] = []

        # Default date window: last 7 days (unless explicitly disabled or searching)
        if not ignore_date_default and not date_from and not date_to and not tag_search:
            now = datetime.now()
            date_from = (now - timedelta(days=7)).isoformat()
            date_to = now.isoformat()

        if date_from:
            where.append("added_at >= ?")
            params.append(date_from)
        if date_to:
            where.append("added_at <= ?")
            params.append(date_to)

        if types:
            types = _expand_type_aliases(types)
            placeholders = ",".join(["?"] * len(types))
            where.append(f"type IN ({placeholders})")
            params.extend(types)

        # Multi-source OR filter using IN; fallback to single source for compatibility
        clean_sources = _expand_source_aliases([s.strip() for s in (sources or []) if s and s.strip()])
        if clean_sources:
            placeholders = ",".join(["?"] * len(clean_sources))
            where.append(f"source IN ({placeholders})")
            params.extend(clean_sources)
        elif source and source.strip():
            where.append("source = ?")
            params.append(_canonicalize_source(source.strip()))

        if favourites_only:
            where.append("favourite = 1")

        if stored_only:
            where.append("feed_items.url LIKE 'blob://%'")

        # Tag/Title search via semi-join or title substring match
        join_clause = ""
        if tag_search and tag_search.strip():
            term = f"%{tag_search.strip()}%"
            where.append("(feed_items.title LIKE ? OR feed_items.id IN (SELECT DISTINCT item_id FROM item_tags WHERE tag LIKE ?))")
            params.append(term)
            params.append(term)

        order_sql = "DESC" if (order or "desc").lower() == "desc" else "ASC"
        if cursor_added_at and cursor_id is not None:
            comparison = "<" if order_sql == "DESC" else ">"
            where.append(
                f"(feed_items.added_at {comparison} ? OR (feed_items.added_at = ? AND feed_items.id {comparison} ?))"
            )
            params.extend([str(cursor_added_at), str(cursor_added_at), int(cursor_id)])

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        limit_sql = f" LIMIT {int(limit)}" if (limit and limit > 0) else ""

        sql = (
            "SELECT feed_items.id, feed_items.type, feed_items.url, feed_items.title, feed_items.content, feed_items.source, "
            "feed_items.added_at, feed_items.favourite "
            "FROM feed_items "
            f"{join_clause}"
            f"{where_sql} "
            f"ORDER BY feed_items.added_at {order_sql}, feed_items.id {order_sql}"
            f"{limit_sql}"
        )

        with self._connect() as con:
            cur = con.execute(sql, params)
            rows = cur.fetchall()
            item_ids = [int(r[0]) for r in rows]
            tags_map: Dict[int, List[str]] = {}
            if item_ids:
                placeholders = ",".join(["?"] * len(item_ids))
                tcur = con.execute(
                    f"SELECT item_id, tag FROM item_tags WHERE item_id IN ({placeholders}) ORDER BY tag ASC",
                    item_ids,
                )
                for item_id, tag in tcur.fetchall():
                    tags_map.setdefault(int(item_id), []).append(str(tag))

        items: List[FeedItemFull] = []
        for r in rows:
            item_id = int(r[0])
            item_type = _canonicalize_feed_type(r[1])
            item_url = _canonicalize_x_url(r[2]) if item_type == "twitter" else str(r[2])
            item_source = _canonicalize_source(r[5]) if item_type == "twitter" else str(r[5] or "")
            items.append(
                FeedItemFull(
                    id=item_id,
                    type=item_type,
                    url=item_url,
                    title=str(r[3] or ""),
                    content=str(r[4] or ""),
                    source=item_source,
                    added_at=str(r[6]),
                    favourite=bool(int(r[7] or 0)),
                    tags=tags_map.get(item_id, []),
                )
            )
        return items

    def summary(self, tag_limit: int = 8) -> Dict[str, Any]:
        """Whole-feed totals and the most used tags, for the page header."""
        with self._connect() as con:
            total = int(con.execute("SELECT COUNT(*) FROM feed_items").fetchone()[0] or 0)
            favourites = int(con.execute("SELECT COUNT(*) FROM feed_items WHERE favourite = 1").fetchone()[0] or 0)
            sources = int(
                con.execute(
                    "SELECT COUNT(DISTINCT source) FROM feed_items WHERE source IS NOT NULL AND TRIM(source) != ''"
                ).fetchone()[0]
                or 0
            )
            latest = con.execute("SELECT id, added_at FROM feed_items ORDER BY added_at DESC, id DESC LIMIT 1").fetchone()
            tag_rows = con.execute(
                "SELECT item_tags.tag, COUNT(DISTINCT item_tags.item_id) AS uses "
                "FROM item_tags JOIN feed_items ON feed_items.id = item_tags.item_id "
                "GROUP BY item_tags.tag ORDER BY uses DESC, item_tags.tag COLLATE NOCASE ASC LIMIT ?",
                (max(1, int(tag_limit)),),
            ).fetchall()
        return {
            "total": total,
            "favourites": favourites,
            "sources": sources,
            "latest_id": int(latest[0]) if latest else None,
            "latest_added_at": str(latest[1]) if latest else None,
            "top_tags": [{"tag": str(row[0]), "count": int(row[1] or 0)} for row in tag_rows],
        }

    def get_blob_metas(self, blob_ids: List[str]) -> Dict[str, BlobMeta]:
        """Fetch metadata for several blobs in one query, keyed by blob id."""
        wanted = [str(blob_id) for blob_id in dict.fromkeys(blob_ids or []) if blob_id]
        if not wanted:
            return {}
        placeholders = ",".join(["?"] * len(wanted))
        with self._connect() as con:
            rows = con.execute(
                f"SELECT id, filename, mimetype, size, path, created_at FROM blobs WHERE id IN ({placeholders})",
                wanted,
            ).fetchall()
        return {
            str(row[0]): BlobMeta(
                id=str(row[0]),
                filename=str(row[1]),
                mimetype=str(row[2] or ""),
                size=int(row[3] or 0),
                path=str(row[4]),
                created_at=str(row[5]),
            )
            for row in rows
        }

    def load_recent_desc(self, days: int = 7, limit: Optional[int] = None) -> List[FeedItemFull]:
        """Convenience: last N days ordered by added_at DESC with tags aggregated."""
        now = datetime.now()
        date_from = (now - timedelta(days=days)).isoformat()
        return self.query_items(order="desc", date_from=date_from, date_to=now.isoformat(), limit=limit)
