"""Resumable, session-owned ingress for arbitrary agent outputs."""
from pathlib import Path
import secrets
import time
from fastapi import HTTPException
from shared import content_files
from shared.content_library import ContentLibrary, SOURCE, infer_kind, output_dir

CHUNK_BYTES = 512 * 1024
MAX_BYTES = 20 * 1024 ** 3


class ContentTransfers:
    def __init__(self):
        self.library = ContentLibrary()
        with self.library.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS content_uploads(
                id TEXT PRIMARY KEY,owner TEXT NOT NULL,source TEXT NOT NULL,source_id TEXT NOT NULL,
                filename TEXT NOT NULL,title TEXT NOT NULL,path TEXT NOT NULL,size INTEGER NOT NULL,
                received INTEGER NOT NULL DEFAULT 0,complete INTEGER NOT NULL DEFAULT 0,expires REAL NOT NULL)""")

    @staticmethod
    def public(row):
        return {key: row[key] for key in ("id", "size", "received")} | {"chunk_bytes": CHUNK_BYTES}

    @staticmethod
    def row(db, identifier, owner):
        row = db.execute("SELECT * FROM content_uploads WHERE id=? AND owner=? AND expires>?", (identifier, owner, time.time())).fetchone()
        if not row:
            raise HTTPException(404, "Transfer expired or belongs to another session")
        return dict(row)

    def begin(self, owner, filename, size, source, source_id, title=""):
        if not SOURCE.fullmatch(source) or not 0 < size <= MAX_BYTES or len(source_id) > 256:
            raise HTTPException(400, "Invalid content source, ID or size")
        identifier = secrets.token_hex(12)
        name = Path(filename.replace("\\", "/")).name[:180]
        suffix = Path(name).suffix.lower()
        if len(suffix) > 16 or not all(c.isalnum() or c == "." for c in suffix):
            suffix = ""
        target = output_dir(source) / (identifier + suffix)
        partial = target.with_name(target.name + ".part")
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT path FROM content_uploads WHERE expires<? AND complete=0", (time.time(),)):
                expired = Path(row[0])
                content_files.discard(expired.with_name(expired.name + ".part"))
            db.execute("DELETE FROM content_uploads WHERE expires<?", (time.time(),))
            if db.execute("SELECT count(*) FROM content_uploads WHERE owner=? AND complete=0", (owner,)).fetchone()[0] >= 4:
                raise HTTPException(429, "Finish or cancel an existing transfer first")
            content_files.create(partial)
            db.execute("INSERT INTO content_uploads(id,owner,source,source_id,filename,title,path,size,expires) VALUES(?,?,?,?,?,?,?,?,?)",
                       (identifier, owner, source, source_id or identifier, name, title[:300] or name, str(target), size, time.time() + 86400))
            return self.public(self.row(db, identifier, owner))

    def status(self, identifier, owner):
        with self.library.connect() as db:
            return self.public(self.row(db, identifier, owner))

    def append(self, identifier, owner, offset, payload):
        if not 0 < len(payload) <= CHUNK_BYTES:
            raise HTTPException(413, "Use non-empty chunks up to 512 KiB")
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.row(db, identifier, owner)
            if row["complete"] or offset < 0 or offset > row["received"] or offset + len(payload) > row["size"]:
                raise HTTPException(409, "Resume from the received offset")
            path = Path(row["path"] + ".part")
            if content_files.size(path) > row["received"]:
                content_files.truncate(path, row["received"])
            try:
                row["received"] = content_files.append(path, offset, payload)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            db.execute("UPDATE content_uploads SET received=? WHERE id=?", (row["received"], identifier))
            return self.public(row)

    def complete(self, identifier, owner):
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.row(db, identifier, owner)
            if row["received"] != row["size"]:
                raise HTTPException(409, "Transfer is incomplete")
            try:
                content_files.finish_upload(Path(row["path"] + ".part"), Path(row["path"]), row["size"])
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            db.execute("UPDATE content_uploads SET complete=1 WHERE id=?", (identifier,))
        item = self.library.publish(row["source"], row["source_id"], row["title"], infer_kind(row["filename"]), path=row["path"])
        return {"item": {**item, "status": "ready"}}

    def cancel(self, identifier, owner):
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.row(db, identifier, owner)
            if row["complete"]:
                raise HTTPException(409, "Content already belongs to the library")
            content_files.discard(Path(row["path"] + ".part"))
            db.execute("DELETE FROM content_uploads WHERE id=?", (identifier,))
