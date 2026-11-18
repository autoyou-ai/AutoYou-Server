"""Owner-authorized import of custom native agent records, preserving their identity."""
import json
import re
import uuid
from shared.content_library import ContentLibrary


def import_record(payload):
    source = str(payload.get("kind", ""))
    if not re.fullmatch(r"custom_[a-z0-9_-]{1,56}", source):
        raise ValueError("Choose a custom agent source")
    identifier = str(uuid.UUID(str(payload.get("id", "")))).lower()
    base = payload.get("base_kind", "notes")
    if base not in {"notes", "page", "diary", "media", "music", "reminders", "assistant"}:
        raise ValueError("Unsupported custom agent base type")
    if len(json.dumps(payload).encode()) > 512 * 1024:
        raise ValueError("Record exceeds 512 KiB")
    updated = payload.get("updated_at")
    if type(updated) is not int or updated < 0:
        raise ValueError("Invalid record timestamp")
    title = str(payload.get("title", "Untitled"))[:300]
    body = str(payload.get("body", ""))
    attachments = payload.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > 4096 or any(not isinstance(row, str) for row in attachments):
        raise ValueError("Invalid attachment references")
    value = {"id": identifier, "kind": source, "base_kind": base, "title": title, "body": body,
             "url": str(payload.get("url", ""))[:4096], "updated_at": updated, "attachments": attachments}
    for key in ("created_at", "diary_date", "time_zone", "reminder", "agent_name", "agent_handle", "agent_icon", "agent_accent"):
        if payload.get(key) is not None:
            value[key] = payload[key]
    encoded = json.dumps(value, sort_keys=True)
    library = ContentLibrary()
    with library.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS native_content(source TEXT,source_id TEXT,updated INTEGER,payload TEXT,PRIMARY KEY(source,source_id))")
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT updated,payload FROM native_content WHERE source=? AND source_id=?", (source, identifier)).fetchone()
        if existing and (existing["updated"] > updated or (existing["updated"] == updated and existing["payload"] != encoded)):
            raise FileExistsError("A newer or conflicting copy is already on this computer. Refresh before publishing.")
        for attachment in attachments:
            if not db.execute("SELECT 1 FROM content WHERE id=? AND source=? AND deleted=0", (attachment, source)).fetchone():
                raise ValueError("An attachment has not finished uploading to this agent")
        db.execute("INSERT INTO native_content VALUES(?,?,?,?) ON CONFLICT(source,source_id) DO UPDATE SET updated=excluded.updated,payload=excluded.payload",
                   (source, identifier, updated, encoded))
        kind = {"notes": "note", "diary": "note", "page": "page", "music": "audio"}.get(base, "agent")
        item = library.publish(source, identifier, title, kind, _db=db,
                               force_revision=bool(existing and existing["payload"] != encoded),
                               href="/agent/organizer_agent/api/content/native/" + source + "/" + identifier)
    return {"item": item, "updated_at": updated}


def read_record(source, identifier):
    library = ContentLibrary()
    with library.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_content'").fetchone():
            raise LookupError("Record not found")
        row = db.execute("SELECT payload FROM native_content WHERE source=? AND source_id=?", (source, identifier)).fetchone()
    if not row:
        raise LookupError("Record not found")
    return json.loads(row[0])
