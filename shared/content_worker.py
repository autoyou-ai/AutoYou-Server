"""One leased, restartable library worker for the unlocked owner server."""
import json
import os
from pathlib import Path
import threading
import time
import uuid

from shared.content_library import COLLECTION, ContentLibrary, configure_output_root, settings
from shared.content_library_scan import refresh_existing
from shared.content_organization import suggest
from shared.content_storage import index_file, migration_plan


def initialize(library):
    with library.connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS content_operations(
                id TEXT PRIMARY KEY, destination TEXT NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, cancel INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS content_operation_items(
                operation_id TEXT NOT NULL,item_id TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
                error TEXT NOT NULL DEFAULT '',PRIMARY KEY(operation_id,item_id));
        """)


def start_migration(destination):
    plan = migration_plan(destination)
    library = ContentLibrary()
    initialize(library)
    with library.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM content_operations WHERE status IN ('queued','running')").fetchone():
            raise FileExistsError("A storage operation is already running")
        # The user has reviewed a copy-only operation. New outputs also use it.
        configure_output_root(plan["destination"])
        identifier = uuid.uuid4().hex
        db.execute("INSERT INTO content_operations VALUES(?,?,'queued',?,?,0)", (identifier, plan["destination"], time.time(), time.time()))
        db.execute("INSERT INTO content_operation_items(operation_id,item_id) SELECT ?,id FROM content WHERE deleted=0 AND path!=''", (identifier,))
    return {"id": identifier, **plan}


def cancel_migration(identifier):
    library = ContentLibrary()
    initialize(library)
    with library.connect() as db:
        db.execute("UPDATE content_operations SET cancel=1 WHERE id=? AND status IN ('queued','running')", (identifier,))


def activity():
    library = ContentLibrary()
    initialize(library)
    with library.connect() as db:
        summary = dict(db.execute("""SELECT COUNT(*) AS total,
            COALESCE(SUM(organization_state='pending'),0) AS pending,
            COALESCE(SUM(organization_state='waiting_model'),0) AS waiting_model,
            COALESCE(SUM(organization_state IN ('failed','missing')),0) AS needs_attention,
            COALESCE(SUM(byte_size),0) AS logical_bytes FROM content WHERE deleted=0""").fetchone())
        state = db.execute("SELECT value FROM content_state WHERE key='worker'").fetchone()
        operations = []
        for op in db.execute("SELECT * FROM content_operations ORDER BY created DESC LIMIT 8"):
            counts = {r[0]: r[1] for r in db.execute("SELECT status,COUNT(*) FROM content_operation_items WHERE operation_id=? GROUP BY status", (op["id"],))}
            operations.append({**dict(op), "counts": counts, "total": sum(counts.values())})
        sources = [{"source": r[0], "count": r[1]} for r in db.execute("SELECT source,COUNT(*) FROM content WHERE deleted=0 GROUP BY source ORDER BY source")]
        collections = [r[0] for r in db.execute(
            f"SELECT DISTINCT {COLLECTION} AS name FROM content WHERE deleted=0 AND {COLLECTION}!='' ORDER BY name LIMIT 200"
        )]
    return {"summary": summary, "worker": json.loads(state[0]) if state else {"state": "waiting", "message": "Organization starts after the server is unlocked"}, "operations": operations, "sources": sources, "collections": collections}


class ContentWorker:
    def __init__(self, config=lambda: {}, ready=lambda: True):
        self.config, self.ready = config, ready
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.owner = uuid.uuid4().hex
        self.last_scan = 0
        self.last_scan_errors = []
        self.heartbeat = 0
        self.thread = None

    def start(self):
        if os.getenv("AUTOYOU_TEST_ROOT") and not os.getenv("AUTOYOU_TEST_CONTENT_WORKER"):
            return
        self.thread = threading.Thread(target=self.run, name="autoyou-content-library", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)

    def cancelled(self):
        if self.stop_event.is_set() or not self.ready():
            return True
        now = time.time()
        if now - self.heartbeat > 20:
            with ContentLibrary().connect() as db:
                db.execute("UPDATE content_state SET value=? WHERE key='lease' AND json_extract(value,'$.owner')=?",
                           (json.dumps({"owner": self.owner, "expires": now + 120}), self.owner))
            self.heartbeat = now
        return False

    def lease(self, library):
        with library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            value = db.execute("SELECT value FROM content_state WHERE key='lease'").fetchone()
            lease = json.loads(value[0]) if value else {}
            if lease.get("expires", 0) > time.time() and lease.get("owner") != self.owner:
                return False
            db.execute("INSERT OR REPLACE INTO content_state VALUES('lease',?)", (json.dumps({"owner": self.owner, "expires": time.time() + 120}),))
        self.heartbeat = time.time()
        return True

    def state(self, library, **value):
        with library.connect() as db:
            db.execute("INSERT OR REPLACE INTO content_state VALUES('worker',?)", (json.dumps({**value, "updated": time.time()}),))

    def run(self):
        while not self.stop_event.is_set():
            try:
                if self.ready():
                    self.run_once()
            except Exception:
                # Locked Maximus stores cannot be read or written until unlock.
                import logging
                logging.getLogger(__name__).warning("Library organization paused; it will retry", exc_info=True)
            self.wake.wait(15)
            self.wake.clear()

    def run_once(self, *, scan=True, limit=4):
        if not self.ready():
            return
        library = ContentLibrary()
        initialize(library)
        if not self.lease(library):
            return
        try:
            preferences = settings()
            if scan and preferences["auto_organize"] and time.time() - self.last_scan >= 300:
                self.state(library, state="indexing", message="Finding content from your agents")
                scan_result = refresh_existing(cancelled=self.cancelled)
                self.last_scan = time.time()
                self.last_scan_errors = scan_result["errors"]
            self.process_migration(library)
            if preferences["auto_organize"]:
                with library.connect() as db:
                    rows = db.execute("""SELECT * FROM content WHERE deleted=0 AND
                        (processed_version<source_version OR organization_state IN ('waiting_model','failed')) AND retry_at<=?
                        ORDER BY retry_at,updated LIMIT ?""", (time.time(), limit)).fetchall()
                for row in rows:
                    if self.cancelled():
                        break
                    self.process(library, dict(row), preferences)
            self.state(library, state="idle" if preferences["auto_organize"] else "paused",
                       message="Your library stays organized automatically" if preferences["auto_organize"] else "Automatic organization is paused",
                       last_scan=self.last_scan, scan_errors=self.last_scan_errors)
        finally:
            with library.connect() as db:
                db.execute("DELETE FROM content_state WHERE key='lease' AND json_extract(value,'$.owner')=?", (self.owner,))

    def process(self, library, row, preferences):
        try:
            fields = index_file(row, cancelled=self.cancelled)
            defaults = suggest(row, {}, semantic=False)
            retry = 0
            if preferences["semantic_labels"]:
                try:
                    defaults = suggest(row, self.config())
                except Exception:
                    defaults.update(organization_state="waiting_model", organization_basis="Source metadata; local AI is waiting")
                    retry = time.time() + 1800
            fields = {**defaults, **fields, "processed_version": row["source_version"], "retry_at": retry}
            with library.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT * FROM content WHERE id=? AND deleted=0", (row["id"],)).fetchone()
                if not current or current["source_version"] != row["source_version"]:
                    return
                if not current["user_edited"]:
                    fields.update(labels=fields["suggested_labels"], annotation=fields["suggested_annotation"], collection=fields["suggested_collection"])
                fields.update(revision=library.tick(db), updated=time.time())
                db.execute("UPDATE content SET " + ','.join(key + '=?' for key in fields) + " WHERE id=?", (*fields.values(), row["id"]))
        except InterruptedError:
            return
        except Exception:
            with library.connect() as db:
                db.execute("UPDATE content SET organization_state='failed',retry_at=? WHERE id=? AND source_version=?", (time.time() + 300, row["id"], row["source_version"]))

    def process_migration(self, library):
        with library.connect() as db:
            op = db.execute("SELECT * FROM content_operations WHERE status IN ('queued','running') ORDER BY created LIMIT 1").fetchone()
        if not op:
            return
        if op["cancel"]:
            with library.connect() as db:
                db.execute("UPDATE content_operations SET status='cancelled',updated=? WHERE id=?", (time.time(), op["id"]))
            return
        with library.connect() as db:
            db.execute("UPDATE content_operations SET status='running',updated=? WHERE id=?", (time.time(), op["id"]))
            pending = db.execute("SELECT item_id FROM content_operation_items WHERE operation_id=? AND status='pending' LIMIT 1", (op["id"],)).fetchone()
            row = db.execute("SELECT * FROM content WHERE id=? AND deleted=0", (pending[0],)).fetchone() if pending else None
        if not pending:
            with library.connect() as db:
                failed = db.execute("SELECT 1 FROM content_operation_items WHERE operation_id=? AND status='failed'", (op["id"],)).fetchone()
                db.execute("UPDATE content_operations SET status=?,updated=? WHERE id=?", ("needs_review" if failed else "completed", time.time(), op["id"]))
            return
        try:
            if not row:
                status, error = "skipped", "Item was removed from its source"
            else:
                fields = index_file(dict(row), copy_external=True, destination=op["destination"], cancelled=self.cancelled)
                if fields.get("organization_state") == "missing":
                    raise FileNotFoundError()
                with library.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    current = db.execute("SELECT source_version,deleted FROM content WHERE id=?", (row["id"],)).fetchone()
                    if not current or current[1] or current[0] != row["source_version"]:
                        raise ValueError("Source changed")
                    fields.update(revision=library.tick(db), updated=time.time())
                    db.execute("UPDATE content SET " + ','.join(key + '=?' for key in fields) + " WHERE id=?", (*fields.values(), row["id"]))
                status, error = "verified", ""
        except InterruptedError:
            return
        except Exception:
            status, error = "failed", "Source unavailable, changed, or failed verification. Original kept."
        with library.connect() as db:
            db.execute("UPDATE content_operation_items SET status=?,error=? WHERE operation_id=? AND item_id=?", (status, error, op["id"], pending[0]))
            db.execute("UPDATE content_operations SET updated=? WHERE id=?", (time.time(), op["id"]))
