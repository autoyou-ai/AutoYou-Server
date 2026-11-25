"""Restart-safe uploads with bounded requests for HTTP and data-channel proxies."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import time

from fastapi import HTTPException, Request
from shared import content_files

from .auth import COOKIE_NAME
from .media import MEDIA_EXTENSIONS, media_kind, parse_name
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
        self.directory = store.media_dir / ".uploads"
        self.directory.mkdir(exist_ok=True)

    def _row(self, db, upload_id: str, owner: str) -> dict:
        row = db.execute("SELECT * FROM uploads WHERE id=? AND owner=? AND expires>?",
                         (upload_id, owner, time.time())).fetchone()
        if not row:
            raise HTTPException(404, "Upload has expired or belongs to another session")
        return dict(row)

    def partial(self, upload_id):
        current = self.directory / (upload_id + ".part")
        legacy = self.store.root / "uploads" / (upload_id + ".part")
        return legacy if not current.exists() and legacy.is_file() else current

    @staticmethod
    def public(row: dict) -> dict:
        return {key: row[key] for key in ("id", "size", "received", "item_id", "expires")} | {"chunk_bytes": CHUNK_BYTES}

    def begin(self, owner: str, filename: str, size: int, title: str, kind: str, folder_id: str | None):
        suffix = Path(filename).suffix.lower()
        if suffix not in MEDIA_EXTENSIONS:
            raise HTTPException(415, "Choose a supported photo, video, audio file, PDF or EPUB")
        kind = media_kind(suffix, kind)
        if not 0 < size <= MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Choose a non-empty media file up to 20 GB")
        if folder_id:
            self.store.folder(folder_id)
        upload_id, now = uid(), time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            expired = db.execute("SELECT id FROM uploads WHERE expires<?", (now,)).fetchall()
            for row in expired:
                content_files.discard(self.partial(row[0]))
            db.execute("DELETE FROM uploads WHERE expires<?", (now,))
            active = db.execute("SELECT COUNT(*) FROM uploads WHERE owner=? AND item_id IS NULL", (owner,)).fetchone()[0]
            if active >= 4:
                raise HTTPException(429, "Finish or cancel an existing upload before adding another")
            db.execute("INSERT INTO uploads(id,owner,filename,title,kind,folder_id,size,expires) VALUES (?,?,?,?,?,?,?,?)",
                       (upload_id, owner, filename, title.strip(), kind, folder_id, size, now + 86400))
            content_files.create(self.directory / (upload_id + ".part"))
            return self.public(self._row(db, upload_id, owner))

    def status(self, upload_id: str, owner: str):
        with self.store.connect() as db:
            return self.public(self._row(db, upload_id, owner))

    def _append(self, upload_id: str, owner: str, offset: int, chunk_path):
        payload = chunk_path.read_bytes() if isinstance(chunk_path, Path) else bytes(chunk_path)
        length = len(payload)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, upload_id, owner)
            if row["item_id"] or offset > row["received"] or offset + length > row["size"]:
                raise HTTPException(409, "Upload offset does not match the server. Resume from its received offset.")
            partial = self.partial(upload_id)
            if not partial.is_file() or content_files.size(partial) < row["received"]:
                raise HTTPException(409, "Upload data is unavailable. Cancel this upload and start again.")
            try:
                if content_files.size(partial) > row["received"]:
                    content_files.truncate(partial, row["received"])
                row["received"] = content_files.append(partial, offset, payload)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            db.execute("UPDATE uploads SET received=? WHERE id=?", (row["received"], upload_id))
            return self.public(row)

    async def append(self, request: Request, upload_id: str, owner: str, offset: int):
        self.status(upload_id, owner)
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > CHUNK_BYTES:
                raise HTTPException(413, "Upload requests must contain at most 2 MB")
            payload.extend(chunk)
        if not payload:
            raise HTTPException(400, "The upload chunk is empty")
        return await asyncio.to_thread(self._append, upload_id, owner, offset, payload)

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
                partial = self.partial(upload_id)
                try:
                    content_files.finish_upload(partial, destination, row["size"])
                except ValueError as error:
                    raise HTTPException(409, str(error)) from error
                info = parse_name(row["filename"])
                info["title"] = row["title"] or info["title"]
                info["kind"] = media_kind(destination.suffix.lower(), "episode" if info["episode"] is not None else row["kind"])
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
            content_files.discard(self.partial(upload_id))
            db.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
