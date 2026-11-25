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
from shared.secure_storage import SecureStorageError, load_secure_json, save_secure_json, secure_storage_enabled

KINDS = {"page", "note", "audio", "voice", "video", "image", "location", "file", "agent"}
SOURCE = re.compile(r"[a-z][a-z0-9_-]{0,79}\Z")
COLLECTION = "COALESCE(NULLIF(collection,''),source_collection)"
PRIVACY_OPTIONS = {
    "diary_agent": "index_private",
    "profile_agent": "index_profile",
    "reminders_agent": "index_reminders",
}
PRIVACY_BASE_KINDS = {
    "diary": "index_private",
    "profile": "index_profile",
    "reminders": "index_reminders",
}


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
            "content_sync_enabled": values.get("content_sync_enabled", True) is not False,
            "notes_sync_enabled": values.get("notes_sync_enabled", True) is not False,
            # Self-data agents (the Diary) keep their own sign-in, so they are listed only when the owner opts in.
            "index_private": values.get("index_private", False) is True,
            "index_profile": values.get("index_profile", False) is True,
            "index_reminders": values.get("index_reminders", False) is True}


def configure_organization(*, auto_organize: bool, semantic_labels: bool, index_private: bool | None = None,
                           index_profile: bool | None = None, index_reminders: bool | None = None,
                           notes_sync_enabled: bool | None = None, content_sync_enabled: bool | None = None) -> dict:
    values = load_secure_json(state_root() / "settings.json", default={})
    values.update(auto_organize=auto_organize, semantic_labels=semantic_labels)
    if index_private is not None:
        values["index_private"] = bool(index_private)
    if index_profile is not None:
        values["index_profile"] = bool(index_profile)
    if index_reminders is not None:
        values["index_reminders"] = bool(index_reminders)
    if content_sync_enabled is not None:
        values["content_sync_enabled"] = content_sync_enabled
    if notes_sync_enabled is not None:
        values["notes_sync_enabled"] = notes_sync_enabled
    save_secure_json(state_root() / "settings.json", values)
    if False in (index_private, index_profile, index_reminders) or True in (index_private, index_profile, index_reminders):
        library = ContentLibrary()
        with library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            disabled_bases = {
                base for base, flag in (("diary", index_private), ("profile", index_profile), ("reminders", index_reminders))
                if flag is False
            }
            disabled_sources = {
                source for source, flag in (("diary_agent", index_private), ("profile_agent", index_profile), ("reminders_agent", index_reminders))
                if flag is False
            }
            if disabled_bases:
                for row in db.execute("SELECT source,base_kind FROM content_agents").fetchall():
                    if row["base_kind"] in disabled_bases:
                        disabled_sources.add(row["source"])
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_content'").fetchone():
                    for row in db.execute("SELECT DISTINCT source, json_extract(payload,'$.base_kind') AS base_kind FROM native_content").fetchall():
                        if row["base_kind"] in disabled_bases:
                            disabled_sources.add(row["source"])
            for source in sorted(disabled_sources):
                live_rows = db.execute("SELECT id FROM content WHERE source=? AND deleted=0", (source,)).fetchall()
                for row in live_rows:
                    library._tombstone(db, row["id"], library.tick(db), preserve_native_file=True)
                    for child in db.execute("SELECT id FROM content WHERE parent_id=? AND deleted=0", (row["id"],)).fetchall():
                        library._tombstone(db, child["id"], library.tick(db), preserve_native_file=True)
                stale_deleted = db.execute(
                    """SELECT id,revision FROM content WHERE source=? AND deleted=1 AND (
                       title!='' OR source_text!='' OR annotation!='' OR suggested_annotation!=''
                       OR labels!='[]' OR suggested_labels!='[]' OR source_labels!='[]'
                       OR collection!='' OR source_collection!='' OR suggested_collection!=''
                       OR href!='' OR path!='' OR managed_path!='')""",
                    (source,),
                ).fetchall()
                for row in stale_deleted:
                    library._tombstone(db, row["id"], row["revision"])
            enabled_bases = {
                base for base, flag in (("diary", index_private), ("profile", index_profile), ("reminders", index_reminders))
                if flag is True
            }
            if enabled_bases and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_content'").fetchone():
                for row in db.execute("SELECT source,source_id,payload FROM native_content").fetchall():
                    try:
                        record = json.loads(row["payload"])
                    except Exception:
                        continue
                    base = record.get("base_kind", "")
                    if base in enabled_bases:
                        kind = {"notes": "note", "diary": "note", "page": "page", "music": "audio"}.get(base, "agent")
                        item = library.publish(
                            row["source"],
                            row["source_id"],
                            record.get("title", "Untitled"),
                            kind,
                            _db=db,
                            force_revision=True,
                            source_labels=record.get("tags"),
                            source_collection=record.get("category"),
                            source_text=record.get("body", ""),
                            href="/agent/organizer_agent/api/content/native/" + row["source"] + "/" + row["source_id"],
                        )
                        library.restore_native_files(db, row["source"], record=record, parent_id=item["id"])
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
    from shared.content_agent_upgrade import storage_path
    path = storage_path(Path(settings()["output_root"]), source)
    path.mkdir(parents=True, exist_ok=True)
    return path


def managed_roots():
    """Include the catalog state root and prior destinations during Maximus key rotation and unsealing."""
    base = state_root()
    default_content = base / "content"
    try:
        values = load_secure_json(base / "settings.json", default={})
        current_output = settings()["output_root"]
    except SecureStorageError:
        values = {}
        current_output = str(default_content)
    roots = list(dict.fromkeys([str(base), str(default_content), current_output, *values.get("managed_roots", [])]))
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
            from shared.content_agent_upgrade import PREVIOUS_NAMES
            for source, old in PREVIOUS_NAMES.items():
                if source == "profile_agent":
                    continue  # The old private catalog indexed only dated entries.
                for row in db.execute("SELECT id,source_id,href FROM content WHERE source=? AND deleted=0", (old,)).fetchall():
                    existing = db.execute("SELECT id FROM content WHERE source=? AND source_id=?", (source, row["source_id"])).fetchone()
                    if existing:
                        db.execute("UPDATE content SET parent_id=? WHERE parent_id=?", (existing["id"], row["id"]))
                        db.execute("UPDATE content SET deleted=1,title='',source_text='',annotation='',revision=? WHERE id=? AND deleted=0",
                                   (self.tick(db), row["id"]))
                    else:
                        href = row["href"].replace("/agent/" + old + "/", "/agent/" + source + "/")
                        db.execute("UPDATE content SET source=?,href=?,revision=? WHERE id=?", (source, href, self.tick(db), row["id"]))
            # Identity of agents that have no code on this computer (agents created on a phone).
            db.execute("""CREATE TABLE IF NOT EXISTS content_agents(
                source TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', handle TEXT NOT NULL DEFAULT '',
                icon TEXT NOT NULL DEFAULT '', accent TEXT NOT NULL DEFAULT '', base_kind TEXT NOT NULL DEFAULT '',
                updated REAL NOT NULL DEFAULT 0)""")
            # Original native attachment records are retained while their index
            # is hidden. This source store is never returned by library feeds.
            db.execute("""CREATE TABLE IF NOT EXISTS native_content_files(
                id TEXT PRIMARY KEY, source TEXT NOT NULL, metadata TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS content_references(
                identity TEXT NOT NULL, nonce TEXT NOT NULL, digest TEXT NOT NULL,
                PRIMARY KEY(identity,nonce))""")

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

    @staticmethod
    def _tombstone(db, row_id: str, revision: int, *, preserve_native_file=False) -> None:
        if preserve_native_file:
            row = db.execute("SELECT * FROM content WHERE id=?", (row_id,)).fetchone()
            if row and row["source"].startswith("custom_") and row["path"]:
                db.execute("INSERT OR REPLACE INTO native_content_files VALUES(?,?,?)",
                           (row_id, row["source"], json.dumps(dict(row))))
        else:
            db.execute("DELETE FROM native_content_files WHERE id=?", (row_id,))
        db.execute(
            """UPDATE content SET deleted=1,title='',path='',href='',labels='[]',annotation='',
               collection='',suggested_labels='[]',parent_id='',source_labels='[]',
               source_collection='',source_text='',user_edited=0,suggested_annotation='',
               suggested_collection='',organization_state='pending',organization_basis='',
               digest='',byte_size=0,file_stamp='',managed_path='',revision=?,updated=?
               WHERE id=?""",
            (revision, time.time(), row_id),
        )

    def restore_native_files(self, db, source, *, record=None, parent_id=""):
        if not self.source_enabled(source, db=db):
            return
        columns = {row[1] for row in db.execute("PRAGMA table_info(content)")} - {"id", "source", "source_id"}
        for saved in db.execute("SELECT id,metadata FROM native_content_files WHERE source=?", (source,)).fetchall():
            original = json.loads(saved["metadata"])
            values = {key: value for key, value in original.items() if key in columns}
            values.update(deleted=0, revision=self.tick(db), updated=time.time(), organization_state="pending",
                          source_version=original["source_version"] + 1, retry_at=0)
            db.execute("UPDATE content SET " + ','.join(key + '=?' for key in values) + " WHERE id=? AND source=? AND deleted=1",
                       (*values.values(), saved["id"], source))
            db.execute("DELETE FROM native_content_files WHERE id=?", (saved["id"],))
        if record is not None:
            media_by_item = {value["item_id"]: value for value in record.get("media", []) if "item_id" in value}
            for attachment in record.get("attachments", []):
                media = media_by_item.get(attachment, {})
                db.execute("UPDATE content SET parent_id=?,source_text=? WHERE id=? AND source=? AND deleted=0",
                           (parent_id, str(media.get("transcription", ""))[:32768], attachment, source))

    @classmethod
    def privacy_option_for(cls, source: str, db=None, *, base_kind: str = "") -> str | None:
        if source in PRIVACY_OPTIONS:
            return PRIVACY_OPTIONS[source]
        if base_kind in PRIVACY_BASE_KINDS:
            return PRIVACY_BASE_KINDS[base_kind]
        if not str(source).startswith("custom_"):
            return None

        def _lookup(conn) -> str:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_agents'").fetchone():
                agent = conn.execute("SELECT base_kind FROM content_agents WHERE source=?", (source,)).fetchone()
                if agent and agent[0]:
                    return str(agent[0])
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_content'").fetchone():
                native = conn.execute(
                    "SELECT json_extract(payload,'$.base_kind') FROM native_content WHERE source=? LIMIT 1",
                    (source,),
                ).fetchone()
                if native and native[0]:
                    return str(native[0])
            return ""

        resolved = ""
        if db is not None:
            resolved = _lookup(db)
        elif (state_root() / "catalog.sqlite3").exists():
            with cls().connect() as conn:
                resolved = _lookup(conn)
        return PRIVACY_BASE_KINDS.get(resolved)

    @classmethod
    def source_enabled(cls, source: str, db=None, *, base_kind: str = "", current_settings: dict | None = None) -> bool:
        option = cls.privacy_option_for(source, db, base_kind=base_kind)
        if not option:
            return True
        prefs = current_settings or settings()
        return bool(prefs.get(option))

    @classmethod
    def disabled_sources(cls, db=None) -> list[str]:
        if db is None:
            with cls().connect() as connection:
                return cls.disabled_sources(connection)
        prefs = settings()
        hidden = {source for source, option in PRIVACY_OPTIONS.items() if not prefs[option]}
        for row in db.execute("SELECT source,base_kind FROM content_agents"):
            if row["base_kind"] in PRIVACY_BASE_KINDS and not prefs[PRIVACY_BASE_KINDS[row["base_kind"]]]:
                hidden.add(row["source"])
        return sorted(hidden)

    def publish(self, source: str, source_id: str, title: str, kind: str = "file", *, path: str = "", href: str = "", force_revision=False, _db=None,
                parent_id=None, source_labels=None, source_collection=None, source_text=None, base_kind: str = "") -> dict:
        if not SOURCE.fullmatch(source) or not str(source_id) or len(str(source_id)) > 256:
            raise ValueError("Invalid content source or identifier")
        if kind not in KINDS:
            raise ValueError("Unsupported content kind")
        if href and (not href.startswith("/agent/") or href.startswith("//") or any(c in href for c in "\r\n\\")):
            raise ValueError("Content links must point to an AutoYou agent")
        title = str(title).strip()[:300] or "Untitled"
        with (self.connect() if _db is None else nullcontext(_db)) as db:
            privacy_option = self.privacy_option_for(source, db=db, base_kind=base_kind)
            if privacy_option and not settings()[privacy_option]:
                existing_row = db.execute("SELECT id,deleted,revision FROM content WHERE source=? AND source_id=?", (source, str(source_id))).fetchone()
                if existing_row and not existing_row["deleted"]:
                    if _db is None:
                        db.execute("BEGIN IMMEDIATE")
                    rev = self.tick(db)
                    self._tombstone(db, existing_row["id"], rev, preserve_native_file=True)
                    for child in db.execute("SELECT id FROM content WHERE parent_id=? AND deleted=0", (existing_row["id"],)).fetchall():
                        self._tombstone(db, child["id"], self.tick(db), preserve_native_file=True)
                    return {"id": existing_row["id"], "deleted": 1, "source": source, "revision": rev}
                return {
                    "id": existing_row["id"] if existing_row else uuid.uuid4().hex,
                    "deleted": 1,
                    "source": source,
                    "revision": existing_row["revision"] if existing_row else 0,
                }
            row = db.execute("SELECT * FROM content WHERE source=? AND source_id=?", (source, str(source_id))).fetchone()

            def _extras_and_changed(current):
                changed = False
                if current and path and current["file_stamp"]:
                    try:
                        stat = Path(path).stat()
                        changed = current["file_stamp"] != f"{stat.st_size}:{stat.st_mtime_ns}"
                    except OSError:
                        changed = current["organization_state"] != "missing"
                ext = {
                    "parent_id": str(parent_id if parent_id is not None else current["parent_id"] if current else "")[:64],
                    "source_labels": json.dumps(list(dict.fromkeys(str(x).strip()[:64] for x in source_labels if str(x).strip()))[:24]) if source_labels is not None else current["source_labels"] if current else "[]",
                    "source_collection": str(source_collection if source_collection is not None else current["source_collection"] if current else "")[:100],
                    "source_text": str(source_text if source_text is not None else current["source_text"] if current else "")[:32768],
                }
                return ext, changed

            extras, file_changed = _extras_and_changed(row)
            if row and not force_revision and not file_changed and not row["deleted"] and all(row[key] == value for key, value in {"title": title, "kind": kind, "path": str(path), "href": href, **extras}.items()):
                return self.public(row)
            if _db is None:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM content WHERE source=? AND source_id=?", (source, str(source_id))).fetchone()
                extras, file_changed = _extras_and_changed(row)
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
        if row["deleted"]:
            return {"id": row["id"], "source": row["source"], "deleted": 1, "revision": row["revision"]}
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

    def list(self, *, q="", kind="", source="", collection="", after=None, offset=0, limit=100, sort="updated", allowed_sources=None) -> dict:
        where, args = [], []
        if after is None:
            where.append("deleted=0")
        else:
            where.append("revision>?")
            args.append(max(0, int(after)))
        if allowed_sources is not None:
            allowed_list = sorted(set(allowed_sources))
            if not allowed_list:
                with self.connect() as db:
                    revision = db.execute("SELECT revision FROM clock WHERE id=1").fetchone()[0]
                return {"items": [], "total": 0, "offset": max(0, int(offset)), "has_more": False, "cursor": revision, "revision": revision}
            where.append("source IN (" + ",".join("?" for _ in allowed_list) + ")")
            args.extend(allowed_list)
        for key, value in (("kind", kind), ("source", source), ("collection", collection)):
            if value:
                column = COLLECTION if key == "collection" else key
                where.append(column + "=?")
                args.append(value)
        if q:
            where.append("(instr(lower(title),lower(?)) OR instr(lower(labels),lower(?)) OR instr(lower(source_labels),lower(?)) OR instr(lower(annotation),lower(?)) OR instr(lower(source_text),lower(?)))")
            args.extend([q[:300]] * 5)
        limit, offset = min(200, max(1, int(limit))), max(0, int(offset))
        with self.connect() as db:
            db.execute("BEGIN")
            hidden = self.disabled_sources(db)
            if hidden:
                where.append("(deleted=1 OR source NOT IN (" + ','.join('?' for _ in hidden) + "))")
                args.extend(hidden)
            clause = " WHERE " + " AND ".join(where) if where else ""
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
            if not row or not self.source_enabled(row["source"], db=db):
                raise LookupError("Content not found")
            if row["revision"] != expected_revision:
                raise FileExistsError("This item changed on another device. Refresh before saving.")
            db.execute("UPDATE content SET user_edited=1,labels=?,annotation=?,collection=?,revision=?,updated=? WHERE id=?",
                       (json.dumps(list(dict.fromkeys(x.strip() for x in labels if x.strip()))), annotation, collection.strip(), self.tick(db), time.time(), identifier))
            return self.public(db.execute("SELECT * FROM content WHERE id=?", (identifier,)).fetchone())

    def forget(self, source, source_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            parent = db.execute("SELECT id FROM content WHERE source=? AND source_id=? AND deleted=0", (source, str(source_id))).fetchone()
            if not parent:
                return
            self._tombstone(db, parent["id"], self.tick(db))
            for child in db.execute("SELECT id FROM content WHERE parent_id=? AND deleted=0", (parent["id"],)).fetchall():
                self._tombstone(db, child["id"], self.tick(db))


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
