"""Restart-safe uploads with bounded requests for HTTP and data-channel proxies."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import time

from fastapi import HTTPException, Request

from .auth import COOKIE_NAME
from .media import VIDEO_EXTENSIONS, parse_name
from .store import Store, digest, uid

MAX_UPLOAD_BYTES = 20 * 1024 ** 3
CHUNK_BYTES = 2 * 1024 ** 2


def upload_owner(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.startswith("Bearer ") else request.cookies.get(COOKIE_NAME, "")
    return digest(token)


class Uploads:
    def __init__(self, store: Store):
        self.store = store
        self.directory = store.root / "uploads"
        self.directory.mkdir(exist_ok=True)

    def _row(self, db, upload_id: str, owner: str) -> dict:
        row = db.execute("SELECT * FROM uploads WHERE id=? AND owner=? AND expires>?",
                         (upload_id, owner, time.time())).fetchone()
        if not row:
            raise HTTPException(404, "Upload has expired or belongs to another session")
        return dict(row)

    @staticmethod
    def public(row: dict) -> dict:
        return {key: row[key] for key in ("id", "size", "received", "item_id", "expires")} | {"chunk_bytes": CHUNK_BYTES}

    def begin(self, owner: str, filename: str, size: int, title: str, kind: str, folder_id: str | None):
        suffix = Path(filename).suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            raise HTTPException(415, "Upload a video file such as MP4, WebM, MOV or MKV")
        if not 0 < size <= MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Choose a non-empty video up to 20 GB")
        if folder_id:
            self.store.folder(folder_id)
        upload_id, now = uid(), time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            expired = db.execute("SELECT id FROM uploads WHERE expires<?", (now,)).fetchall()
            for row in expired:
                (self.directory / (row[0] + ".part")).unlink(missing_ok=True)
            db.execute("DELETE FROM uploads WHERE expires<?", (now,))
            active = db.execute("SELECT COUNT(*) FROM uploads WHERE owner=? AND item_id IS NULL", (owner,)).fetchone()[0]
            if active >= 4:
                raise HTTPException(429, "Finish or cancel an existing upload before adding another")
            db.execute("INSERT INTO uploads(id,owner,filename,title,kind,folder_id,size,expires) VALUES (?,?,?,?,?,?,?,?)",
                       (upload_id, owner, filename, title.strip(), kind, folder_id, size, now + 86400))
            (self.directory / (upload_id + ".part")).touch(exist_ok=False)
            return self.public(self._row(db, upload_id, owner))

    def status(self, upload_id: str, owner: str):
        with self.store.connect() as db:
            return self.public(self._row(db, upload_id, owner))

    def _append(self, upload_id: str, owner: str, offset: int, chunk_path: Path):
        length = chunk_path.stat().st_size
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, upload_id, owner)
            if row["item_id"] or offset > row["received"] or offset + length > row["size"]:
                raise HTTPException(409, "Upload offset does not match the server. Resume from its received offset.")
            partial = self.directory / (upload_id + ".part")
            if not partial.is_file() or partial.stat().st_size < row["received"]:
                raise HTTPException(409, "Upload data is unavailable. Cancel this upload and start again.")
            with partial.open("r+b") as handle:
                if offset < row["received"]:
                    handle.seek(offset)
                    if offset + length > row["received"] or hashlib.sha256(handle.read(length)).digest() != hashlib.sha256(chunk_path.read_bytes()).digest():
                        raise HTTPException(409, "This upload chunk conflicts with data already received")
                    return self.public(row)
                # Recover a process interruption between disk append and SQL commit.
                handle.truncate(row["received"])
                handle.seek(row["received"])
                handle.write(chunk_path.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            row["received"] += length
            db.execute("UPDATE uploads SET received=? WHERE id=?", (row["received"], upload_id))
            return self.public(row)

    async def append(self, request: Request, upload_id: str, owner: str, offset: int):
        self.status(upload_id, owner)
        chunk_path = self.directory / ("chunk-" + uid())
        written = 0
        try:
            with chunk_path.open("xb") as handle:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > CHUNK_BYTES:
                        raise HTTPException(413, "Upload requests must contain at most 2 MB")
                    await asyncio.to_thread(handle.write, chunk)
            if not written:
                raise HTTPException(400, "The upload chunk is empty")
            return await asyncio.to_thread(self._append, upload_id, owner, offset, chunk_path)
        finally:
            chunk_path.unlink(missing_ok=True)

    def complete(self, upload_id: str, owner: str) -> dict:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, upload_id, owner)
            if row["item_id"]:
                item_id = row["item_id"]
            else:
                if row["received"] != row["size"]:
                    raise HTTPException(409, "Upload is incomplete. Resume before adding it to the library.")
                if row["folder_id"]:
                    self.store.folder(row["folder_id"])
                destination = self.store.media_dir / (upload_id + Path(row["filename"]).suffix.lower())
                partial = self.directory / (upload_id + ".part")
                if partial.is_file() and partial.stat().st_size == row["size"]:
                    partial.replace(destination)
                elif not destination.is_file() or destination.stat().st_size != row["size"]:
                    raise HTTPException(409, "Upload data is unavailable. Cancel this upload and start again.")
                info = parse_name(row["filename"])
                info["title"] = row["title"] or info["title"]
                info["kind"] = "episode" if info["episode"] is not None else row["kind"]
                item_id, now = uid(), time.time()
                db.execute("""INSERT INTO items(id,source_key,folder_id,title,kind,year,season,episode,series,
                           file_path,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (item_id, "upload:" + upload_id, row["folder_id"], info["title"], info["kind"],
                            info["year"], info["season"], info["episode"], info["series"], str(destination), now, now))
                db.execute("UPDATE uploads SET item_id=? WHERE id=?", (item_id, upload_id))
        return self.store.item(item_id)

    def cancel(self, upload_id: str, owner: str):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, upload_id, owner)
            if row["item_id"]:
                raise HTTPException(409, "This upload is already in the library")
            (self.directory / (upload_id + ".part")).unlink(missing_ok=True)
            db.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
