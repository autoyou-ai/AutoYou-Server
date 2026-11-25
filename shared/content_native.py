"""Owner-authorized import of custom native agent records, preserving their identity."""
import hashlib
import json
import re
import uuid
from shared import content_agents
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
    media = payload.get("media")
    if media is not None:
        if not isinstance(media, list) or len(media) != len(attachments):
            raise ValueError("Invalid custom attachments")
        validated = []
        for key, row in zip(attachments, media):
            if (not isinstance(row,dict) or row.get("item_id") != key or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", str(row.get("id","")))
                    or not isinstance(row.get("name"),str) or not 0 < len(row["name"]) <= 256 or any(c in row["name"] for c in "/\\")
                    or not re.fullmatch(r"[a-zA-Z0-9.+_-]+/[a-zA-Z0-9.+_-]+",str(row.get("mime","")))
                    or not isinstance(row.get("transcription",""),str) or len(row.get("transcription","")) > 50000):
                raise ValueError("Invalid custom attachment")
            validated.append({k:row[k] for k in ("id","item_id","name","mime","transcription") if k in row})
        if len({row["id"] for row in validated}) != len(validated):
            raise ValueError("Repeated custom attachment")
        value["media"] = validated
    tags = payload.get("tags",[])
    if not isinstance(tags,list) or len(tags)>128 or any(not isinstance(tag,str) or len(tag)>128 for tag in tags):
        raise ValueError("Invalid custom tags")
    if "tags" in payload:
        value["tags"] = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
    if "category" in payload:
        value["category"] = str(payload["category"])[:256]
    if "favorite" in payload:
        value["favorite"] = payload["favorite"] is True
    for key in ("created_at", "diary_date", "time_zone", "reminder", "agent_name", "agent_handle", "agent_icon", "agent_accent"):
        if payload.get(key) is not None:
            value[key] = payload[key]
    encoded = json.dumps(value, sort_keys=True)
    library = ContentLibrary()
    with library.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS native_content(source TEXT,source_id TEXT,updated INTEGER,payload TEXT,PRIMARY KEY(source,source_id))")
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT updated,payload FROM native_content WHERE source=? AND source_id=?", (source, identifier)).fetchone()
        base_revision = payload.get("base_revision")
        revision = hashlib.sha256(existing["payload"].encode()).hexdigest() if existing else ""
        if base_revision is not None and base_revision != revision and (not existing or existing["payload"] != encoded):
            raise FileExistsError("This record changed on the computer.")
        if not existing and db.execute("SELECT 1 FROM content WHERE source=? AND source_id=? AND deleted=1", (source,identifier)).fetchone():
            raise FileExistsError("This record was removed.")
        if base_revision is None and existing and (existing["updated"] > updated or (existing["updated"] == updated and existing["payload"] != encoded)):
            raise FileExistsError("A newer or conflicting copy is already on this computer. Refresh before publishing.")
        parent = db.execute("SELECT id FROM content WHERE source=? AND source_id=?", (source, identifier)).fetchone()
        for attachment in attachments:
            row = db.execute("SELECT parent_id FROM content WHERE id=? AND source=? AND path<>'' AND deleted=0",
                             (attachment, source)).fetchone()
            if not row or (row["parent_id"] and (not parent or row["parent_id"] != parent["id"])):
                raise ValueError("An attachment has not finished uploading to this agent")
        db.execute("INSERT INTO native_content VALUES(?,?,?,?) ON CONFLICT(source,source_id) DO UPDATE SET updated=excluded.updated,payload=excluded.payload",
                   (source, identifier, updated, encoded))
        # The phone names its own agent; keep that so My Content shows it by name, icon and accent.
        content_agents.remember(db, source, content_agents.clean_identity(payload))
        kind = {"notes": "note", "diary": "note", "page": "page", "music": "audio"}.get(base, "agent")
        item = library.publish(source, identifier, title, kind, _db=db,
                               force_revision=bool(existing and existing["payload"] != encoded),
                               source_labels=value.get("tags"), source_collection=value.get("category"), source_text=body,
                               href="/agent/organizer_agent/api/content/native/" + source + "/" + identifier)
        # Attachments belong to their record, so removing the record removes them with it.
        for attachment in attachments:
            db.execute("UPDATE content SET parent_id=? WHERE id=? AND source=? AND deleted=0", (item["id"], attachment, source))
    return {"item": item, "revision":hashlib.sha256(encoded.encode()).hexdigest(), "updated_at": updated}


def read_record(source, identifier):
    library = ContentLibrary()
    with library.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_content'").fetchone():
            raise LookupError("Record not found")
        row = db.execute("SELECT payload FROM native_content WHERE source=? AND source_id=?", (source, identifier)).fetchone()
    if not row:
        raise LookupError("Record not found")
    return json.loads(row[0])


def register_collection(app, authorize):
    from fastapi import HTTPException, Request
    from shared import content_access, content_removal
    from shared.paired_content import require_pair
    from shared.content_library_api import bounded_body

    def gate(request, action="read"):
        require_pair(request,"organizer_agent")
        return authorize(request) or content_access.deny(request,action)

    def source_name(value):
        if not content_agents.is_custom(value):
            raise HTTPException(400,"Invalid custom agent")
        return value

    @app.get("/api/sync/custom")
    def definitions(request: Request):
        denied = gate(request)
        if denied is not None:
            return denied
        with ContentLibrary().connect() as db:
            return {"agents":[dict(row) for row in db.execute("SELECT source,name,handle,icon,accent,base_kind FROM content_agents ORDER BY source") if content_agents.is_custom(row["source"])]}

    @app.put("/api/sync/custom/{source}/definition")
    async def define(source: str, request: Request):
        denied = gate(request,"edit")
        if denied is not None:
            return denied
        source_name(source)
        try:
            data = json.loads(await bounded_body(request,4096))
            if not isinstance(data,dict):
                raise ValueError("Invalid custom identity")
            identity = content_agents.clean_identity(data)
            if not identity["name"] or identity["base_kind"] not in {"notes","page","diary","music","media","reminders","assistant"}:
                raise ValueError("Invalid custom identity")
        except (ValueError, TypeError) as e:
            raise HTTPException(400,"Invalid custom identity") from e
        with ContentLibrary().connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM content_agents WHERE source=?",(source,)).fetchone()
            if old is None:
                content_agents.remember(db,source,identity)
            elif old["base_kind"] != identity["base_kind"]:
                raise HTTPException(409,"This custom agent has a different base on the computer")
        return {"source":source}

    @app.get("/api/sync/custom/{source}")
    def catalog(source: str, request: Request, after: str = ""):
        denied = gate(request)
        if denied is not None:
            return denied
        source_name(source)
        with ContentLibrary().connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='native_content' AND type='table'").fetchone():
                return {"items":[],"next":None}
            rows = db.execute("""SELECT n.source_id,n.payload FROM native_content n JOIN content c ON n.source=c.source AND n.source_id=c.source_id
                                 WHERE n.source=? AND c.deleted=0 AND n.source_id>? ORDER BY n.source_id LIMIT 101""",(source,after)).fetchall()
        return {"items":[{"id":row[0],"revision":hashlib.sha256(row[1].encode()).hexdigest()} for row in rows[:100]],
                "next":rows[99][0] if len(rows)>100 else None}

    @app.api_route("/api/sync/custom/{source}/{identifier}", methods=["GET","DELETE"])
    def record(source: str, identifier: str, request: Request):
        denied = gate(request,"delete" if request.method == "DELETE" else "read")
        if denied is not None:
            return denied
        import mimetypes
        from pathlib import Path
        library = ContentLibrary()
        with library.connect() as db:
            db.execute("BEGIN")
            item = db.execute("SELECT id,revision FROM content WHERE source=? AND source_id=? AND deleted=0",(source_name(source),identifier)).fetchone()
            if not item:
                raise HTTPException(404,"Record not found")
            row = db.execute("SELECT payload FROM native_content WHERE source=? AND source_id=?",(source,identifier)).fetchone()
            if not row:
                raise HTTPException(404,"Record not found")
            data = json.loads(row["payload"])
            revision = hashlib.sha256(row["payload"].encode()).hexdigest()
            if request.method == "GET":
                if "media" not in data:
                    data["media"] = []
                    for attachment in data.get("attachments",[]):
                        held = db.execute("SELECT title,path FROM content WHERE id=? AND source=? AND deleted=0",(attachment,source)).fetchone()
                        if not held:
                            raise HTTPException(409,"A custom record attachment is missing")
                        name = Path(held["title"] or held["path"]).name or "attachment"
                        data["media"].append({"id":attachment,"item_id":attachment,"name":name,"mime":mimetypes.guess_type(name)[0] or "application/octet-stream"})
                return {"record":data,"revision":revision,"item_id":item["id"]}
        if request.query_params.get("base_revision") != revision:
            raise HTTPException(409,"Record changed on the computer")
        try:
            content_removal.remove(library,item["id"],item["revision"])
        except FileExistsError as e:
            raise HTTPException(409,"Record changed on the computer") from e
        return {"deleted":True}
