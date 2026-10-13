"""A lazy, test-isolated SQLite catalog. Never write state at module import."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Any


def data_root() -> Path:
    # Test isolation must win over even an explicitly configured live TV root.
    if os.getenv("AUTOYOU_TEST_ROOT"):
        return Path(os.environ["AUTOYOU_TEST_ROOT"]).resolve() / "tv_agent"
    if os.getenv("AUTOYOU_TV_DATA_DIR"):
        return Path(os.environ["AUTOYOU_TV_DATA_DIR"]).expanduser().resolve()
    from shared.platform_runtime import get_mutable_data_dir
    return get_mutable_data_dir("AutoYou", anchor=__file__) / "tv_agent_data"


def uid() -> str:
    return secrets.token_hex(12)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or data_root()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.media_dir = self.root / "media"
        self.media_dir.mkdir(exist_ok=True)
        self.artwork_dir = self.root / "artwork"
        self.artwork_dir.mkdir(exist_ok=True)
        self.db_path = self.root / "library.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS folders(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, parent_id TEXT,
                    root_path TEXT, relative_path TEXT NOT NULL DEFAULT '',
                    created REAL NOT NULL, UNIQUE(root_path, relative_path));
                CREATE TABLE IF NOT EXISTS items(
                    id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL,
                    folder_id TEXT REFERENCES folders(id) ON DELETE SET NULL,
                    title TEXT NOT NULL, kind TEXT NOT NULL, year INTEGER,
                    season INTEGER, episode INTEGER, series TEXT,
                    file_path TEXT, source_url TEXT,
                    status TEXT NOT NULL DEFAULT 'ready', metadata TEXT NOT NULL DEFAULT '{}',
                    technical TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS items_folder ON items(folder_id);
                CREATE INDEX IF NOT EXISTS items_kind ON items(kind);
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}',
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS invitations(
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, label TEXT NOT NULL,
                    role TEXT NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions(
                    token_hash TEXT PRIMARY KEY, role TEXT NOT NULL, invitation_id TEXT,
                    expires REAL NOT NULL, otp_until REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS auth_attempts(key TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata_cache(
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS uploads(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, filename TEXT NOT NULL,
                    title TEXT NOT NULL, kind TEXT NOT NULL, folder_id TEXT, size INTEGER NOT NULL,
                    received INTEGER NOT NULL DEFAULT 0, item_id TEXT, expires REAL NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO settings VALUES ('stream_secret', ?)",
                       (json.dumps(secrets.token_urlsafe(32)),))
        if os.name != "nt":
            self.root.chmod(0o700)
            self.db_path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value: Any):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def folder(self, folder_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM folders WHERE id=?", (folder_id,)).fetchone()
        if not row:
            raise LookupError("Folder not found")
        return dict(row)

    def folders(self, parent_id: str | None = None, *, all_folders: bool = False) -> list[dict]:
        sql = """SELECT f.*, (SELECT COUNT(*) FROM items i WHERE i.folder_id=f.id AND i.status!='deleted') AS item_count,
                 (SELECT COUNT(*) FROM folders children WHERE children.parent_id=f.id) AS folder_count FROM folders f"""
        params = () if all_folders else (parent_id,)
        if not all_folders:
            sql += " WHERE f.parent_id IS ?"
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql + " ORDER BY f.name COLLATE NOCASE", params)]

    def add_folder(self, name: str, parent_id: str | None = None, *, root_path: str | None = None,
                   relative_path: str = "") -> dict:
        name = name.strip()
        if not name or len(name) > 200:
            raise ValueError("Use a folder name between 1 and 200 characters")
        if parent_id:
            self.folder(parent_id)
        folder_id = uid()
        with self.connect() as db:
            if root_path:
                existing = db.execute("SELECT id FROM folders WHERE root_path=? AND relative_path=?",
                                      (root_path, relative_path)).fetchone()
                if existing:
                    return self.folder(existing[0])
            db.execute("INSERT INTO folders VALUES (?,?,?,?,?,?)",
                       (folder_id, name, parent_id, root_path, relative_path, time.time()))
        return self.folder(folder_id)

    def item(self, item_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise LookupError("Title not found")
        result = dict(row)
        for key in ("metadata", "technical"):
            result[key] = json.loads(result[key])
        return result

    def add_item(self, *, source_key: str, title: str, kind: str = "video", folder_id: str | None = None,
                 file_path: str | None = None, source_url: str | None = None,
                 year: int | None = None, season: int | None = None, episode: int | None = None,
                 series: str | None = None, technical: dict | None = None) -> tuple[dict, bool]:
        if folder_id:
            self.folder(folder_id)
        with self.connect() as db:
            existing = db.execute("SELECT id FROM items WHERE source_key=?", (source_key,)).fetchone()
            if existing:
                item = self.item(existing[0])
                item["technical"] = {**item["technical"], **(technical or {})}
                if item["status"] != "deleted":
                    item["status"] = "ready"
                db.execute("UPDATE items SET status=?, technical=?, updated=? WHERE id=?",
                           (item["status"], json.dumps(item["technical"]), time.time(), existing[0]))
                return item, False
            item_id = uid()
            now = time.time()
            db.execute("""INSERT INTO items(id,source_key,folder_id,title,kind,year,season,episode,series,
                       file_path,source_url,technical,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (item_id, source_key, folder_id, title.strip()[:300], kind, year, season, episode,
                        series, file_path, source_url, json.dumps(technical or {}), now, now))
        return self.item(item_id), True

    def update_item(self, item_id: str, changes: dict) -> dict:
        self.item(item_id)
        allowed = {"title", "kind", "year", "season", "episode", "series", "folder_id", "status", "metadata", "technical"}
        changes = {k: v for k, v in changes.items() if k in allowed}
        if changes.get("folder_id"):
            self.folder(changes["folder_id"])
        changes["updated"] = time.time()
        params = [json.dumps(v) if k in {"metadata", "technical"} else v for k, v in changes.items()]
        with self.connect() as db:
            db.execute("UPDATE items SET " + ",".join(k + "=?" for k in changes) + " WHERE id=?", (*params, item_id))
        return self.item(item_id)

    def library(self, *, query: str = "", kind: str = "", folder_id: str | None = None,
                genre: str = "", sort: str = "recent", offset: int = 0, limit: int = 100,
                ids: list[str] | None = None) -> dict:
        conditions, params = ["status != 'deleted'"], []
        if query:
            conditions.append("(title LIKE ? ESCAPE '\\' OR series LIKE ? ESCAPE '\\' OR metadata LIKE ? ESCAPE '\\')")
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.extend(["%" + escaped + "%"] * 3)
        if kind:
            conditions.append("kind=?")
            params.append(kind)
        if folder_id:
            conditions.append("folder_id=?")
            params.append(folder_id)
        if genre:
            conditions.append("json_extract(metadata,'$.genre') LIKE ?")
            params.append("%" + genre + "%")
        if ids is not None:
            conditions.append("id IN (" + ",".join("?" for _ in ids) + ")" if ids else "0")
            params.extend(ids)
        where = " WHERE " + " AND ".join(conditions)
        orders = {"recent": "created DESC", "title": "title COLLATE NOCASE, season, episode",
                  "year": "year DESC, title", "rating": "CAST(json_extract(metadata,'$.imdb_rating') AS REAL) DESC"}
        with self.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM items" + where, params).fetchone()[0]
            rows = db.execute("SELECT id FROM items" + where + " ORDER BY " + orders.get(sort, orders["recent"])
                              + " LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
            total = db.execute("SELECT COUNT(*) FROM items WHERE status!='deleted'").fetchone()[0]
        return {"items": [self.item(row[0]) for row in rows], "count": count, "total": total,
                "offset": offset, "has_more": offset + len(rows) < count}

    def job(self, job_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise LookupError("Import not found")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["result"] = json.loads(result["result"])
        return result

    def add_job(self, kind: str, payload: dict) -> dict:
        job_id, now = uid(), time.time()
        with self.connect() as db:
            db.execute("INSERT INTO jobs(id,kind,status,payload,created,updated) VALUES (?,?,'queued',?,?,?)",
                       (job_id, kind, json.dumps(payload), now, now))
        return self.job(job_id)

    def update_job(self, job_id: str, *, status: str, progress: float = 0, message: str = "", result: dict | None = None):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=?,progress=?,message=?,result=?,updated=? WHERE id=?",
                       (status, progress, message[:500], json.dumps(result or {}), time.time(), job_id))

    def jobs(self) -> list[dict]:
        with self.connect() as db:
            ids = db.execute("SELECT id FROM jobs ORDER BY created DESC LIMIT 60").fetchall()
        return [self.job(row[0]) for row in ids]

    def key(self) -> str:
        env_key = os.getenv("AUTOYOU_TV_OMDB_API_KEY")
        if env_key:
            return env_key
        from shared.keystore import keyring_available, read_keyring_password_strict
        if not keyring_available():
            return ""
        try:
            return read_keyring_password_strict(self.key_service(), "omdb-api-key") or ""
        except Exception:
            raise ValueError("The OS credential store could not read the OMDb key") from None

    def key_service(self) -> str:
        # Scope every credential to this library, including AUTOYOU_TEST_ROOT.
        return "AutoYou.TV.Metadata." + digest(str(self.root))[:24]

    def set_key(self, value: str):
        from shared.keystore import delete_keyring_password, keyring_available, write_keyring_password
        if not keyring_available():
            raise ValueError("Secure credential storage is unavailable. Configure AUTOYOU_TV_OMDB_API_KEY on the host.")
        try:
            if value.strip():
                write_keyring_password(self.key_service(), "omdb-api-key", value.strip())
            else:
                delete_keyring_password(self.key_service(), "omdb-api-key")
        except Exception:
            raise ValueError("The OS credential store could not save the OMDb key") from None
