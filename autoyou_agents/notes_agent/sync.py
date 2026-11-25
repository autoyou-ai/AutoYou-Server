"""Revisioned replication of the Notes database. Website, chat and phone writes share one clock."""
import hashlib
import json
import re
import sqlite3
import shutil
import time
from datetime import datetime
from pathlib import Path

from shared.content_files import REFERENCE
from shared.secure_storage import FILE_HEADER, read_secure_file, write_secure_file


CHUNK = 1024 * 1024
MAX_FILE = 64 * 1024 * 1024
KEY = re.compile(r"[0-9a-f]{32}")
DIGEST = re.compile(r"[0-9a-f]{64}")


class Conflict(ValueError):
    pass


def install(db):
    db.execute("""CREATE TABLE IF NOT EXISTS note_sync_clock (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL, library TEXT NOT NULL)""")
    db.execute("INSERT OR IGNORE INTO note_sync_clock VALUES(1,0,lower(hex(randomblob(16))))")
    db.execute("""CREATE TABLE IF NOT EXISTS note_sync (
        id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, revision INTEGER UNIQUE NOT NULL,
        deleted INTEGER DEFAULT 0 NOT NULL, mutation TEXT DEFAULT '' NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS note_sync_uploads (
        key TEXT NOT NULL, digest TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL,
        received INTEGER NOT NULL, touched INTEGER NOT NULL, PRIMARY KEY(key,digest))""")
    # Backfill only on migration. Assign distinct revisions even to notes with identical timestamps.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='note_sync_insert'").fetchone():
        db.execute("""INSERT INTO note_sync(id,key,revision)
            SELECT id,lower(hex(randomblob(16))),id FROM notes""")
        db.execute("UPDATE note_sync_clock SET version=coalesce((SELECT max(revision) FROM note_sync),0)")
    tick = "UPDATE note_sync_clock SET version=version+1 WHERE singleton=1;"
    version = "(SELECT version FROM note_sync_clock WHERE singleton=1)"
    db.execute(f"""CREATE TRIGGER IF NOT EXISTS note_sync_insert AFTER INSERT ON notes BEGIN
        {tick} INSERT INTO note_sync(id,key,revision) VALUES(NEW.id,lower(hex(randomblob(16))),{version}); END""")
    db.execute(f"""CREATE TRIGGER IF NOT EXISTS note_sync_edit AFTER UPDATE OF title,content,tags,category,metadata ON notes
        WHEN OLD.title IS NOT NEW.title OR OLD.content IS NOT NEW.content OR OLD.tags IS NOT NEW.tags
          OR OLD.category IS NOT NEW.category OR OLD.metadata IS NOT NEW.metadata BEGIN
        {tick} UPDATE note_sync SET revision={version},mutation='' WHERE id=NEW.id; END""")
    db.execute(f"""CREATE TRIGGER IF NOT EXISTS note_sync_delete AFTER DELETE ON notes BEGIN
        {tick} UPDATE note_sync SET deleted=1,revision={version},mutation='' WHERE id=OLD.id; END""")


def changes(tool, after, limit):
    with sqlite3.connect(tool._db_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        clock = db.execute("SELECT library,version FROM note_sync_clock").fetchone()
        rows = db.execute("SELECT * FROM note_sync WHERE revision>? ORDER BY revision LIMIT ?", (after, limit + 1)).fetchall()
        return {**dict(clock), "changes": [dict(row) for row in rows[:limit]], "has_more": len(rows) > limit}


def _record(db, key):
    state = db.execute("SELECT * FROM note_sync WHERE key=?", (key,)).fetchone()
    if not state:
        raise LookupError("Note not found")
    result = dict(state)
    if not state["deleted"]:
        note = db.execute("SELECT title,content,tags,category,metadata,created_at,updated_at FROM notes WHERE id=?", (state["id"],)).fetchone()
        meta = json.loads(note["metadata"] or "{}") or {}
        media = meta.get("media_attachments", [])
        result.update(title=note["title"], content=note["content"] or "", category=note["category"] or "",
                      tags=json.loads(note["tags"] or "[]"), url=meta.get("native_url", ""),
                      media=[{k: item[k] for k in ("id", "filename", "mimetype", "size_bytes", "client_media_id", "digest", "transcription") if k in item}
                             for item in media if isinstance(item, dict) and item.get("id")])
        for field in ("created", "updated"):
            stamp = note[field + "_at"]
            result[field + "_at"] = stamp
            if stamp:
                try:
                    result[field + "_ms"] = round(datetime.fromisoformat(stamp).timestamp() * 1000)
                except (ValueError, OSError, OverflowError):
                    pass
    return result


def record(tool, key):
    with sqlite3.connect(tool._db_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        return _record(db, key)


def stage(tool, key, digest, data, filename, offset, total):
    if not KEY.fullmatch(key) or not DIGEST.fullmatch(digest):
        raise ValueError("Invalid attachment identity")
    if not data or len(data) != min(CHUNK, total - offset) or offset % CHUNK or not (0 <= offset < offset + len(data) <= total <= MAX_FILE):
        raise ValueError("Invalid attachment range")
    if data.startswith(FILE_HEADER) or (offset == 0 and data.startswith(REFERENCE)):
        raise ValueError("Reserved storage envelope")
    now = int(time.time())
    with sqlite3.connect(tool._db_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        # Only uncommitted uploads expire. Source attachments retain their own lifetime.
        for row in db.execute("SELECT path FROM note_sync_uploads WHERE touched<?", (now - 86400,)):
            path = Path(row["path"])
            shutil.rmtree(path.parent / (path.stem + ".parts"), ignore_errors=True)
            if not db.execute("SELECT 1 FROM media_attachments WHERE path=?", (str(path),)).fetchone():
                path.unlink(missing_ok=True)
        db.execute("DELETE FROM note_sync_uploads WHERE touched<?", (now - 86400,))
        row = db.execute("SELECT * FROM note_sync_uploads WHERE key=? AND digest=?", (key, digest)).fetchone()
        if row:
            path, received = Path(row["path"]), row["received"]
            if row["size"] != total:
                raise ValueError("Attachment length changed")
            if received == total and path.is_file():
                return {"offset": total}
        else:
            used, count = db.execute("SELECT coalesce(sum(size),0),count(*) FROM note_sync_uploads").fetchone()
            if used + total > 2 * 1024**3 or count >= 2048:
                raise ValueError("Pending Notes uploads are full")
            suffix = Path(tool._sanitize_filename(filename)).suffix.lower()
            suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,12}", suffix) and suffix != ".partial" else ".bin"
            path = Path(tool._ensure_media_dir("notes-sync", key)) / (digest + suffix)
            received = 0
            db.execute("INSERT INTO note_sync_uploads VALUES(?,?,?,?,?,?)", (key, digest, str(path), total, received, now))
        pending = path.parent / (path.stem + ".parts")
        if offset != received:
            return {"offset": received}
        pending.mkdir(exist_ok=True)
        write_secure_file(pending / str(offset), data)
        received += len(data)
        if received == total:
            # The agent serves one protected file. Never interpret uploaded bytes as catalog references.
            payload = b"".join(read_secure_file(pending / str(n)) for n in range(0, total, CHUNK))
            if hashlib.sha256(payload).hexdigest() != digest:
                shutil.rmtree(pending)
                db.execute("DELETE FROM note_sync_uploads WHERE key=? AND digest=?", (key, digest))
                db.commit()
                raise ValueError("Attachment digest does not match")
            write_secure_file(path, payload)
        db.execute("UPDATE note_sync_uploads SET received=?,touched=? WHERE key=? AND digest=?", (received, now, key, digest))
    if received == total:
        shutil.rmtree(pending, ignore_errors=True)
    return {"offset": received}


def save(tool, key, payload):
    with sqlite3.connect(tool._db_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        state = db.execute("SELECT * FROM note_sync WHERE key=?", (key,)).fetchone()
        if state and not state["deleted"] and state["mutation"] == payload.mutation:
            return _record(db, key)
        if (state and state["deleted"]) or payload.base_revision != (state["revision"] if state else 0):
            raise Conflict("This note changed on the computer")
        meta = {}
        if state:
            raw = db.execute("SELECT metadata FROM notes WHERE id=?", (state["id"],)).fetchone()[0]
            meta = json.loads(raw or "{}") or {}
        existing = {int(item["id"]): item for item in meta.get("media_attachments", [])}
        media = []
        timestamp = tool._local_timestamp()
        for ref in payload.media:
            if ref.id is not None:
                if ref.id not in existing:
                    raise ValueError("Attachment does not belong to this note")
                media.append({**existing[ref.id], "transcription": ref.transcription})
            else:
                row = db.execute("SELECT path,size FROM note_sync_uploads WHERE key=? AND digest=? AND received=size", (key, ref.digest)).fetchone()
                if not row or not Path(row["path"]).is_file():
                    raise ValueError("Upload this attachment again")
                filename = tool._sanitize_filename(ref.filename)[:180]
                attachment_id = db.execute("""INSERT INTO media_attachments(filename,mimetype,path,size_bytes,source,created_at)
                    VALUES(?,?,?,?,'phone',?)""", (filename, ref.mimetype, row["path"], row["size"], timestamp)).lastrowid
                media.append(dict(id=attachment_id, filename=filename, mimetype=ref.mimetype, path=row["path"],
                                  size_bytes=row["size"], client_media_id=ref.client_media_id, digest=ref.digest, transcription=ref.transcription))
        db.executemany("DELETE FROM note_sync_uploads WHERE key=? AND digest=?", [(key, ref.digest) for ref in payload.media if ref.digest])
        meta["media_attachments"] = media
        meta["native_url"] = payload.url
        fields = (payload.title, payload.content, json.dumps(payload.tags), payload.category, json.dumps(meta))
        updated = datetime.fromtimestamp(payload.updated_ms / 1000).isoformat(timespec="milliseconds") if payload.updated_ms is not None else timestamp
        if state:
            db.execute("UPDATE notes SET title=?,content=?,tags=?,category=?,metadata=?,updated_at=? WHERE id=?",
                       (*fields, updated, state["id"]))
            note_id = state["id"]
        else:
            note_id = db.execute("INSERT INTO notes(title,content,tags,category,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                                 (*fields, datetime.fromtimestamp(payload.created_ms / 1000).isoformat(timespec="milliseconds") if payload.created_ms is not None else updated, updated)).lastrowid
        db.execute("UPDATE note_sync SET key=?,mutation=? WHERE id=?", (key, payload.mutation, note_id))
        result = _record(db, key)
    from shared.content_library import infer_kind, publish_content
    parent = publish_content("notes_agent", str(note_id), payload.title, "note", href="/agent/notes_agent/",
                             source_labels=payload.tags, source_collection=payload.category, source_text=payload.content)
    if parent:
        attached = {item["id"] for item in media}
        for item in [*media, *(item for item_id, item in existing.items() if item_id not in attached)]:
            if item.get("path"):
                linked = item["id"] in attached
                publish_content("notes_agent", "attachment:" + str(item["id"]), item["filename"], infer_kind(item["filename"]),
                                path=item["path"], parent_id=parent["id"] if linked else "", source_labels=payload.tags if linked else [],
                                source_collection=payload.category if linked else "Attachments")
    return result


def remove(tool, key, revision, pending_mutation=""):
    with sqlite3.connect(tool._db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT id,revision,deleted,mutation FROM note_sync WHERE key=?", (key,)).fetchone()
        if not row:
            # A local deletion may arrive before the phone receives its first create receipt.
            # Reserve the key so that delayed creates and retries cannot resurrect the note.
            if revision == 0 and pending_mutation:
                note_id = db.execute("INSERT INTO notes(title) VALUES('')").lastrowid
                db.execute("UPDATE note_sync SET key=? WHERE id=?", (key, note_id))
                db.execute("DELETE FROM notes WHERE id=?", (note_id,))
            return
        if row[2]:
            return
        if revision != row[1] and not (pending_mutation and pending_mutation == row[3]):
            raise Conflict("This note changed before deletion")
        db.execute("DELETE FROM notes WHERE id=?", (row[0],))
    from shared.content_library import forget_content
    forget_content("notes_agent", str(row[0]))
