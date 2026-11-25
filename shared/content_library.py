"""Owner's cross-agent content catalog and destination for new managed outputs.

No scanning or writes at import time. Producers publish references after their
own transaction commits; existing agent stores remain the source of truth.
The catalog deliberately has no network/model calls or file-delete operation.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from shared.platform_runtime import get_mutable_data_dir
from shared.secure_storage import load_secure_json, save_secure_json, secure_storage_enabled

KINDS = {"page", "note", "audio", "voice", "video", "image", "location", "file", "agent"}
SOURCE = re.compile(r"[a-z][a-z0-9_-]{0,79}\Z")
COLLECTION = "COALESCE(NULLIF(collection,''),source_collection)"


def state_root() -> Path:
    return get_mutable_data_dir("AutoYou", anchor=__file__) / "content_library"


def settings() -> dict:
    values = load_secure_json(state_root() / "settings.json", default={})
    # An operator-configured folder must never escape the test sandbox.
    configured = values.get("output_root", "")
    if configured and os.getenv("AUTOYOU_TEST_ROOT") and not Path(configured).resolve().is_relative_to(Path(os.environ["AUTOYOU_TEST_ROOT"]).resolve()):
        configured = ""
    root = Path(configured) if configured else state_root() / "content"
    return {"output_root": str(root.resolve()), "encrypted_at_rest": secure_storage_enabled(),
            "auto_organize": values.get("auto_organize", True) is not False,
            "semantic_labels": values.get("semantic_labels", True) is not False,
            "notes_sync_enabled": values.get("notes_sync_enabled", True) is not False,
            # Self-data agents (the Diary) keep their own sign-in, so they are listed only when the owner opts in.
            "index_private": values.get("index_private", False) is True}


def configure_organization(*, auto_organize: bool, semantic_labels: bool, index_private: bool | None = None, notes_sync_enabled: bool | None = None) -> dict:
    values = load_secure_json(state_root() / "settings.json", default={})
    values.update(auto_organize=auto_organize, semantic_labels=semantic_labels)
    if index_private is not None:
        values["index_private"] = bool(index_private)
    if notes_sync_enabled is not None:
        values["notes_sync_enabled"] = notes_sync_enabled
    save_secure_json(state_root() / "settings.json", values)
    return settings()


def configure_output_root(value: str) -> dict:
    path = Path(value).expanduser()
    if not path.is_absolute() or path == Path(path.anchor):
        raise ValueError("Choose an absolute folder inside a drive, not the drive itself")
    if os.getenv("AUTOYOU_TEST_ROOT") and not path.resolve().is_relative_to(Path(os.environ["AUTOYOU_TEST_ROOT"]).resolve()):
        raise ValueError("Test content must remain inside AUTOYOU_TEST_ROOT")
    path.mkdir(parents=True, exist_ok=True)
    # A small write probe verifies access without modifying existing content.
    probe = path / (".autoyou-write-check-" + uuid.uuid4().hex)
    try:
        with probe.open("xb") as handle:
            handle.write(b"")
    finally:
        probe.unlink(missing_ok=True)
    previous = load_secure_json(state_root() / "settings.json", default={})
    history = list(dict.fromkeys([*previous.get("managed_roots", []), settings()["output_root"], str(path.resolve())]))
    save_secure_json(state_root() / "settings.json", {**previous, "output_root": str(path.resolve()), "managed_roots": history})
    return {**settings(), "restart_required": True,
            "message": "New outputs use this folder after agent restart. Existing content stays linked in the catalog."}


def output_dir(source: str) -> Path:
    if not SOURCE.fullmatch(source):
        raise ValueError("Invalid content source")
    path = Path(settings()["output_root"]) / source
    path.mkdir(parents=True, exist_ok=True)
    return path


def managed_roots():
    """Include prior destinations during Maximus key rotation and unsealing."""
    values = load_secure_json(state_root() / "settings.json", default={})
    roots = [settings()["output_root"], *values.get("managed_roots", [])]
    test = os.getenv("AUTOYOU_TEST_ROOT")
    return [Path(root).resolve() for root in roots if not test or Path(root).resolve().is_relative_to(Path(test).resolve())]


def infer_kind(filename: str, fallback: str = "file") -> str:
    mime = mimetypes.guess_type(filename)[0] or ""
    family = mime.split("/")[0]
    return family if family in {"audio", "video", "image"} else fallback


def labels_for(title: str, kind: str, source: str) -> list[str]:
    # Transparent local rules, not a claim of semantic AI understanding.
    words = re.findall(r"[a-z][a-z0-9-]{2,24}", Path(title).stem.lower())
    source_label = "custom-agent" if source.startswith("custom_") else source.removesuffix("_agent").replace("_", "-")
    return list(dict.fromkeys([kind, source_label] + words[:6]))


# Schema work takes the catalog's write lock. Every API request builds a
# ContentLibrary, so it runs once per catalog file and process, not per request.
_SCHEMA_READY: set[str] = set()
_SCHEMA_LOCK = threading.Lock()

# Index rows are small on purpose: a remote client walks the whole catalog once,
# then only fetches changes. Heavy or private fields never travel in the index.
INDEX_FIELDS = ("id", "source", "source_id", "title", "kind", "href", "labels", "annotation", "collection",
                "source_collection", "source_labels", "parent_id", "revision", "updated", "organization_state", "byte_size")


class ContentLibrary:
    def __init__(self):
        self.root = state_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "catalog.sqlite3"
        key = str(self.db_path)
        if key in _SCHEMA_READY and self.db_path.exists():
            return
        with _SCHEMA_LOCK:
            self._migrate()
            _SCHEMA_READY.add(key)

    def _migrate(self):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS content(
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
                    title TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL DEFAULT '',
                    href TEXT NOT NULL DEFAULT '', labels TEXT NOT NULL DEFAULT '[]',
                    annotation TEXT NOT NULL DEFAULT '', collection TEXT NOT NULL DEFAULT '',
                    suggested_labels TEXT NOT NULL DEFAULT '[]', revision INTEGER NOT NULL,
                    updated REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(source,source_id));
                CREATE TABLE IF NOT EXISTS clock(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO clock VALUES(1,0);
                CREATE INDEX IF NOT EXISTS content_revision ON content(revision);
                CREATE INDEX IF NOT EXISTS content_kind ON content(kind,updated);
            """)
            # Additive migration: older agents can continue publishing during a rolling restart.
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(content)")}
            additions = {
                "parent_id": "TEXT NOT NULL DEFAULT ''", "source_labels": "TEXT NOT NULL DEFAULT '[]'",
                "source_collection": "TEXT NOT NULL DEFAULT ''", "source_text": "TEXT NOT NULL DEFAULT ''",
                "source_version": "INTEGER NOT NULL DEFAULT 1", "processed_version": "INTEGER NOT NULL DEFAULT 0",
                "user_edited": "INTEGER NOT NULL DEFAULT 0", "suggested_annotation": "TEXT NOT NULL DEFAULT ''",
                "suggested_collection": "TEXT NOT NULL DEFAULT ''", "organization_state": "TEXT NOT NULL DEFAULT 'pending'",
                "organization_basis": "TEXT NOT NULL DEFAULT ''", "retry_at": "REAL NOT NULL DEFAULT 0",
                "digest": "TEXT NOT NULL DEFAULT ''", "byte_size": "INTEGER NOT NULL DEFAULT 0",
                "file_stamp": "TEXT NOT NULL DEFAULT ''", "managed_path": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE content ADD COLUMN {name} {declaration}")
            # Existing annotations must never be mistaken for automatic defaults.
            if "user_edited" not in columns:
                db.execute("UPDATE content SET user_edited=1 WHERE annotation!='' OR collection!=''")
                for existing in db.execute("SELECT id,title,kind,source,labels FROM content").fetchall():
                    if json.loads(existing["labels"]) != labels_for(existing["title"], existing["kind"], existing["source"]):
                        db.execute("UPDATE content SET user_edited=1 WHERE id=?", (existing["id"],))
            db.execute("CREATE INDEX IF NOT EXISTS content_digest ON content(digest) WHERE deleted=0")
            db.execute("CREATE INDEX IF NOT EXISTS content_parent ON content(parent_id)")
            db.execute("CREATE TABLE IF NOT EXISTS content_state(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            # Live-row indexes keep the recency, per-agent and per-type views off a table scan.
            db.execute("CREATE INDEX IF NOT EXISTS content_live_updated ON content(deleted,updated DESC,id)")
            db.execute("CREATE INDEX IF NOT EXISTS content_live_source ON content(deleted,source,updated DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS content_live_kind ON content(deleted,kind,updated DESC)")
            # Identity of agents that have no code on this computer (agents created on a phone).
            db.execute("""CREATE TABLE IF NOT EXISTS content_agents(
                source TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', handle TEXT NOT NULL DEFAULT '',
                icon TEXT NOT NULL DEFAULT '', accent TEXT NOT NULL DEFAULT '', base_kind TEXT NOT NULL DEFAULT '',
                updated REAL NOT NULL DEFAULT 0)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def tick(db):
        db.execute("UPDATE clock SET revision=revision+1 WHERE id=1")
        return db.execute("SELECT revision FROM clock WHERE id=1").fetchone()[0]

    def publish(self, source: str, source_id: str, title: str, kind: str = "file", *, path: str = "", href: str = "", force_revision=False, _db=None,
                parent_id=None, source_labels=None, source_collection=None, source_text=None) -> dict:
        if not SOURCE.fullmatch(source) or not str(source_id) or len(str(source_id)) > 256:
            raise ValueError("Invalid content source or identifier")
        if kind not in KINDS:
            raise ValueError("Unsupported content kind")
        if href and (not href.startswith("/agent/") or href.startswith("//") or any(c in href for c in "\r\n\\")):
            raise ValueError("Content links must point to an AutoYou agent")
        title = str(title).strip()[:300] or "Untitled"
        with (self.connect() if _db is None else nullcontext(_db)) as db:
            if _db is None:
                db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM content WHERE source=? AND source_id=?", (source, str(source_id))).fetchone()
            file_changed = False
            if row and path and row["file_stamp"]:
                try:
                    stat = Path(path).stat()
                    file_changed = row["file_stamp"] != f"{stat.st_size}:{stat.st_mtime_ns}"
                except OSError:
                    file_changed = row["organization_state"] != "missing"
            extras = {
                "parent_id": str(parent_id if parent_id is not None else row["parent_id"] if row else "")[:64],
                "source_labels": json.dumps(list(dict.fromkeys(str(x).strip()[:64] for x in source_labels if str(x).strip()))[:24]) if source_labels is not None else row["source_labels"] if row else "[]",
                "source_collection": str(source_collection if source_collection is not None else row["source_collection"] if row else "")[:100],
                "source_text": str(source_text if source_text is not None else row["source_text"] if row else "")[:32768],
            }
            if row and not force_revision and not file_changed and not row["deleted"] and all(row[key] == value for key, value in {"title": title, "kind": kind, "path": str(path), "href": href, **extras}.items()):
                return self.public(row)
            identifier = row["id"] if row else uuid.uuid4().hex
            revision = self.tick(db)
            db.execute("""INSERT INTO content(id,source,source_id,title,kind,path,href,suggested_labels,labels,revision,updated)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,source_id) DO UPDATE SET
                title=excluded.title,kind=excluded.kind,path=excluded.path,href=excluded.href,
                suggested_labels=excluded.suggested_labels,revision=excluded.revision,updated=excluded.updated,deleted=0""",
                (identifier, source, str(source_id), title, kind, str(path), href,
                 json.dumps(labels_for(title, kind, source)), json.dumps(labels_for(title, kind, source)), revision, time.time()))
            db.execute("""UPDATE content SET parent_id=?,source_labels=?,source_collection=?,source_text=?,
                       source_version=?,organization_state='pending',retry_at=0 WHERE id=?""",
                       (*extras.values(), (row["source_version"] + 1) if row else 1, identifier))
            if row and (row["path"] != str(path) or file_changed):
                db.execute("UPDATE content SET managed_path='',digest='',byte_size=0,file_stamp='' WHERE id=?", (identifier,))
            return self.public(db.execute("SELECT * FROM content WHERE id=?", (identifier,)).fetchone())

    @staticmethod
    def public(row, *, include_path=False):
        data = dict(row)
        for key in ("labels", "suggested_labels", "source_labels"):
            data[key] = json.loads(data[key])
        if not include_path:
            data.pop("path", None)
            data.pop("managed_path", None)
            data.pop("file_stamp", None)
            data.pop("source_text", None)
        data["has_file"] = bool(row["path"])
        return data

    @staticmethod
    def compact(item) -> dict:
        """The wire shape of the walkable index: enough to list, group and filter.

        Takes a `public()` row. A removed row travels as a bare tombstone so a
        client can drop it. Paths, digests, note bodies and worker bookkeeping
        never leave the computer in this shape.
        """
        if item["deleted"]:
            return {"id": item["id"], "source": item["source"], "deleted": 1, "revision": item["revision"]}
        out = {key: item[key] for key in INDEX_FIELDS}
        out["has_file"] = item["has_file"]
        out["deleted"] = 0
        return out

    def list(self, *, q="", kind="", source="", collection="", after=None, offset=0, limit=100, sort="updated") -> dict:
        where, args = [], []
        if after is None:
            where.append("deleted=0")
        else:
            where.append("revision>?")
            args.append(max(0, int(after)))
        for key, value in (("kind", kind), ("source", source), ("collection", collection)):
            if value:
                column = COLLECTION if key == "collection" else key
                where.append(column + "=?")
                args.append(value)
        if q:
            where.append("(instr(lower(title),lower(?)) OR instr(lower(labels),lower(?)) OR instr(lower(source_labels),lower(?)) OR instr(lower(annotation),lower(?)) OR instr(lower(source_text),lower(?)))")
            args.extend([q[:300]] * 5)
        clause = " WHERE " + " AND ".join(where) if where else ""
        limit, offset = min(200, max(1, int(limit))), max(0, int(offset))
        with self.connect() as db:
            db.execute("BEGIN")
            revision = db.execute("SELECT revision FROM clock WHERE id=1").fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM content" + clause, args).fetchone()[0]
            order = "revision" if after is not None else {"title": "lower(title),id", "size": "byte_size DESC,id"}.get(sort, "updated DESC,id")
            rows = db.execute("SELECT * FROM content" + clause + " ORDER BY " + order + " LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        items = [self.public(row) for row in rows]
        return {"items": items, "total": total, "offset": offset, "has_more": offset + len(items) < total,
                "cursor": items[-1]["revision"] if after is not None and items else revision, "revision": revision}

    def annotate(self, identifier, *, labels, annotation, collection, expected_revision):
        if not isinstance(labels, list) or len(labels) > 24 or any(not isinstance(x, str) or len(x) > 64 for x in labels):
            raise ValueError("Use up to 24 labels of 64 characters")
        if len(annotation) > 8000 or len(collection) > 100:
            raise ValueError("Annotation or collection is too long")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM content WHERE id=? AND deleted=0", (identifier,)).fetchone()
            if not row:
                raise LookupError("Content not found")
            if row["revision"] != expected_revision:
                raise FileExistsError("This item changed on another device. Refresh before saving.")
            db.execute("UPDATE content SET user_edited=1,labels=?,annotation=?,collection=?,revision=?,updated=? WHERE id=?",
                       (json.dumps(list(dict.fromkeys(x.strip() for x in labels if x.strip()))), annotation, collection.strip(), self.tick(db), time.time(), identifier))
            return self.public(db.execute("SELECT * FROM content WHERE id=?", (identifier,)).fetchone())

    def forget(self, source, source_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM content WHERE source=? AND source_id=? AND deleted=0", (source, str(source_id))).fetchone():
                return
            db.execute("UPDATE content SET deleted=1,revision=?,updated=? WHERE source=? AND source_id=?",
                       (self.tick(db), time.time(), source, str(source_id)))
            parent = db.execute("SELECT id FROM content WHERE source=? AND source_id=?", (source, str(source_id))).fetchone()
            for child in db.execute("SELECT id FROM content WHERE parent_id=? AND deleted=0", (parent[0],)).fetchall():
                db.execute("UPDATE content SET deleted=1,revision=?,updated=? WHERE id=?", (self.tick(db), time.time(), child[0]))


def publish_content(*args, **kwargs):
    """Best-effort indexing must not roll back a producer's committed content."""
    try:
        return ContentLibrary().publish(*args, **kwargs)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Content catalog update failed; source content is unchanged", exc_info=True)
        return None


def forget_content(source, source_id):
    try:
        ContentLibrary().forget(source, source_id)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Content catalog removal failed", exc_info=True)


def save_output(source, filename, chunks, *, title="", kind=None, href=""):
    """Common output contract for built-in and dynamically created Python agents.

    `chunks` is an iterable of byte chunks, not a path or an executable payload.
    The returned catalog ID is stable for subsequent annotation and sync reads.
    """
    from shared import content_files
    identifier = uuid.uuid4().hex
    name = Path(filename.replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
        suffix = ""
    target = output_dir(source) / (identifier + suffix)
    content_files.create(target)
    try:
        offset = 0
        for chunk in chunks:
            offset = content_files.append(target, offset, chunk)
        return ContentLibrary().publish(source, identifier, title or name, kind or infer_kind(name), path=str(target), href=href)
    except BaseException:
        content_files.discard(target)
        raise
