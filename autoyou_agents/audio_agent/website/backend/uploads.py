"""Bounded, restart-safe imports from the native Music library."""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException, Request
from shared.platform_runtime import get_mutable_data_dir

CHUNK_BYTES = 1024 * 1024
MAX_BYTES = 1024 ** 3
EXTENSIONS = {".aac", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"}


def import_root() -> Path:
    return get_mutable_data_dir("AutoYou", anchor=Path(__file__).resolve().parents[4]) / "audio_agent" / "imports"


class Uploads:
    def __init__(self):
        self.root = import_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.pending = self.root / ".pending"
        self.pending.mkdir(exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, filename TEXT NOT NULL, title TEXT NOT NULL,
                size INTEGER NOT NULL, received INTEGER NOT NULL DEFAULT 0, complete INTEGER NOT NULL DEFAULT 0,
                expires REAL NOT NULL)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.pending / "uploads.sqlite", timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def row(self, db, upload_id, owner):
        row = db.execute("SELECT * FROM uploads WHERE id=? AND owner=? AND expires>?", (upload_id, owner, time.time())).fetchone()
        if not row:
            raise HTTPException(404, "Upload expired or belongs to another session")
        return dict(row)

    @staticmethod
    def public(row):
        return {key: row[key] for key in ("id", "size", "received")} | {"chunk_bytes": CHUNK_BYTES}

    def begin(self, owner, filename, size, title):
        if Path(filename).suffix.lower() not in EXTENSIONS:
            raise HTTPException(415, "Choose a supported audio file")
        if not 0 < size <= MAX_BYTES:
            raise HTTPException(413, "Choose a non-empty audio file up to 1 GiB")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT id FROM uploads WHERE expires<? AND complete=0", (time.time(),)):
                (self.pending / (row[0] + ".part")).unlink(missing_ok=True)
            db.execute("DELETE FROM uploads WHERE expires<? AND complete=0", (time.time(),))
            if db.execute("SELECT COUNT(*) FROM uploads WHERE owner=? AND complete=0", (owner,)).fetchone()[0] >= 4:
                raise HTTPException(429, "Finish or cancel an existing upload first")
            upload_id = secrets.token_hex(12)
            db.execute("INSERT INTO uploads(id,owner,filename,title,size,expires) VALUES (?,?,?,?,?,?)",
                       (upload_id, owner, Path(filename.replace("\\", "/")).name[:128], title.strip()[:200], size, time.time() + 86400))
            (self.pending / (upload_id + ".part")).touch(exist_ok=False)
            return self.public(self.row(db, upload_id, owner))

    def status(self, upload_id, owner):
        with self.connect() as db:
            return self.public(self.row(db, upload_id, owner))

    def append_bytes(self, upload_id, owner, offset, data):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.row(db, upload_id, owner)
            if row["complete"] or offset > row["received"] or offset + len(data) > row["size"]:
                raise HTTPException(409, "Resume from the computer's received offset")
            with (self.pending / (upload_id + ".part")).open("r+b") as handle:
                if offset < row["received"]:
                    handle.seek(offset)
                    if offset + len(data) > row["received"] or handle.read(len(data)) != data:
                        raise HTTPException(409, "Upload chunk conflicts with received data")
                else:
                    handle.truncate(offset)
                    handle.seek(offset)
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                    row["received"] += len(data)
                    db.execute("UPDATE uploads SET received=? WHERE id=?", (row["received"], upload_id))
            return self.public(row)

    async def append(self, request, upload_id, owner, offset):
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > CHUNK_BYTES:
                raise HTTPException(413, "Upload chunks must be at most 1 MiB")
            data.extend(chunk)
        if not data:
            raise HTTPException(400, "Empty upload chunk")
        return await asyncio.to_thread(self.append_bytes, upload_id, owner, offset, data)

    def complete(self, upload_id, owner):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.row(db, upload_id, owner)
            if row["received"] != row["size"]:
                raise HTTPException(409, "Upload is incomplete")
            target = self.root / (upload_id + Path(row["filename"]).suffix.lower())
            partial = self.pending / (upload_id + ".part")
            if partial.is_file() and partial.stat().st_size == row["size"]:
                partial.replace(target)
            elif not target.is_file() or target.stat().st_size != row["size"]:
                raise HTTPException(409, "Upload data is unavailable")
            db.execute("UPDATE uploads SET complete=1 WHERE id=?", (upload_id,))
            return target

    def cancel(self, upload_id, owner):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.row(db, upload_id, owner)
            if row["complete"]:
                raise HTTPException(409, "This song is already in the library")
            (self.pending / (upload_id + ".part")).unlink(missing_ok=True)
            db.execute("DELETE FROM uploads WHERE id=?", (upload_id,))

    def metadata(self, path):
        with self.connect() as db:
            row = db.execute("SELECT filename,title FROM uploads WHERE id=? AND complete=1", (path.stem,)).fetchone()
            return dict(row) if row else {}


def upload_owner(request: Request) -> str:
    token = request.headers.get("authorization") or request.headers.get("x-autoyou-webrtc-session-id") or request.headers.get("cookie") or (request.client.host if request.client else "local")
    return hashlib.sha256(token.encode()).hexdigest()
