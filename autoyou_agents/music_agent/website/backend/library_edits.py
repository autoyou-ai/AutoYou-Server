"""Owner changes to library labels and visibility, including tracks on read-only volumes."""
import hashlib
import json
import sqlite3
from pathlib import Path

from fastapi import HTTPException
from shared.platform_runtime import get_service_data_dir


def _path():
    return get_service_data_dir("music_agent", anchor=__file__) / "library-edits.sqlite3"


def revision(path, title, artist):
    stat = path.stat()
    value = [title, artist, stat.st_size, stat.st_mtime_ns]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def load():
    file = _path()
    if not file.is_file():
        return {}
    with sqlite3.connect(file) as db:
        return {row[0]: (row[1], bool(row[2])) for row in db.execute("SELECT id,title,hidden FROM track_edits")}


def change(track, path, base_revision, title=None, hide=False):
    file = _path()
    file.parent.mkdir(exist_ok=True, parents=True)
    with sqlite3.connect(file) as db:
        db.execute("CREATE TABLE IF NOT EXISTS track_edits(id TEXT PRIMARY KEY,title TEXT,hidden INTEGER NOT NULL DEFAULT 0)")
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT title,hidden FROM track_edits WHERE id=?", (track["id"],)).fetchone()
        if old and old[1]:
            raise HTTPException(404, "Track was removed from the library")
        current = old[0] if old and old[0] is not None else track["title"]
        if revision(Path(path), current, track["artist"]) != base_revision:
            raise HTTPException(409, "The track changed. Refresh before editing.")
        db.execute("INSERT OR REPLACE INTO track_edits VALUES(?,?,?)", (track["id"], title if title is not None else current, int(hide)))
