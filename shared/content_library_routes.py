"""My Content screen routes: cacheable summary, change feed, groups, previews, removal.

Registered by `register_content_routes`, so the admin console, the Organizer site
and a phone's agent route all get the same endpoints with the same checks. Every
route runs the caller's `authorize` first, then the role check for what it does.
"""
from __future__ import annotations

import asyncio
import gzip
import io
import json
import mimetypes
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from shared import content_access, content_agents, content_files, content_index, content_removal, content_thumbs
from shared.content_library import ContentLibrary, settings

CACHE_HEADERS = {"Cache-Control": "no-store", "Vary": "Accept-Encoding"}
PREVIEW_CHARS = 4000


def document_excerpt(path: Path, suffix: str) -> str:
    """Read a small excerpt without extracting archive members or rendering their markup."""
    if suffix not in {".epub", ".docx", ".pptx", ".xlsx", ".zip"} or path.stat().st_size > 8 * 1024 * 1024:
        return ""
    limit = 8 * 1024 * 1024
    data = b"".join(content_files.read_range(path, 0, limit))
    if len(data) > limit:
        return ""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.filelist) > 2048:
                return ""
            if suffix == ".zip":
                return "\n".join(info.filename for info in archive.filelist[:80])
            pattern = {".docx": r"word/document\.xml", ".xlsx": r"xl/(?:sharedStrings|worksheets/sheet1)\.xml",
                       ".pptx": r"ppt/slides/slide\d+\.xml", ".epub": r".+\.x?html?"}[suffix]
            excerpts = []
            for info in [item for item in archive.filelist if re.fullmatch(pattern, item.filename, re.IGNORECASE)][:6]:
                if info.file_size > 512 * 1024 or info.flag_bits & 1:
                    continue
                xml = archive.read(info)
                if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper() or b"\x00" in xml:
                    continue
                root = ElementTree.fromstring(xml)
                body = root.find(".//{*}body")
                if body is not None:
                    root = body
                for tag in ("style", "script"):
                    for node in root.iterfind(".//{*}" + tag):
                        node.clear()
                excerpts.append(" ".join(part.strip() for part in root.itertext() if part.strip()))
                if sum(map(len, excerpts)) > PREVIEW_CHARS:
                    break
            return "\n".join(excerpts)
    except (ValueError, OSError, zipfile.BadZipFile, ElementTree.ParseError, NotImplementedError):
        return ""


class Target(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)


class RemovalBatch(BaseModel):
    items: list[Target] = Field(min_length=1, max_length=100)


class OrganizeBatch(BaseModel):
    items: list[Target] = Field(min_length=1, max_length=100)
    collection: str | None = Field(default=None, max_length=100)
    add_labels: list[str] = Field(default_factory=list, max_length=24)
    remove_labels: list[str] = Field(default_factory=list, max_length=24)


def _matches(header: str, tag: str) -> bool:
    return any(part.strip() in ("*", tag, tag.removeprefix("W/")) for part in header.split(","))


def answer(request: Request, payload, tag: str, status: int = 200):
    """JSON with a validator: 304 when the caller already holds this answer, gzip when it can take it."""
    headers = {**CACHE_HEADERS, "ETag": tag}
    if _matches(request.headers.get("if-none-match", ""), tag):
        return Response(status_code=304, headers=headers)
    body = json.dumps(payload, separators=(",", ":")).encode()
    if len(body) > 1400 and "gzip" in request.headers.get("accept-encoding", ""):
        body, headers["Content-Encoding"] = gzip.compress(body, 5, mtime=0), "gzip"
    return Response(body, status_code=status, media_type="application/json", headers=headers)


def register(app, authorize, prefix: str, worker=None):
    def error(message, status, **extra):
        return JSONResponse({"error": message, **extra}, status_code=status, headers={"Cache-Control": "no-store"})

    def gate(request, action="read"):
        denied = authorize(request)
        if denied is not None:
            return denied
        return content_access.deny(request, action) if action != "read" else None

    def live(item_id):
        library = ContentLibrary()
        with library.connect() as db:
            row = db.execute("SELECT * FROM content WHERE id=? AND deleted=0", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Content not found")
        return library, row

    @app.get(prefix + "/summary")
    async def summary(request: Request):
        denied = gate(request)
        if denied is not None:
            return denied

        def build():
            library = ContentLibrary()
            data = content_index.summary(library)
            sources = data.pop("sources")
            names = content_agents.describe_all(library, [row["source"] for row in sources])
            agents = [{**names[row["source"]], "count": row["count"], "bytes": row["bytes"], "latest": row["latest"],
                       "removal": content_removal.plan({"source": row["source"], "source_id": ""})["mode"]} for row in sources]
            current = settings()
            return {**data, "agents": agents, "abilities": content_access.abilities(request),
                    "settings": {key: current[key] for key in ("output_root", "encrypted_at_rest", "auto_organize", "semantic_labels", "index_private")}}
        payload = await asyncio.to_thread(build)
        tag = content_index.etag("summary", payload["revision"], payload["worker"], payload["settings"], payload["abilities"]["role"])
        return answer(request, payload, tag)

    @app.get(prefix + "/changes")
    async def changes(request: Request, after: int = 0, limit: int = 500):
        denied = gate(request)
        if denied is not None:
            return denied
        result = await asyncio.to_thread(lambda: content_index.changes(ContentLibrary(), after, limit))
        return answer(request, result, content_index.etag("changes", after, limit, result["revision"], result["cursor"]))

    @app.get(prefix + "/groups")
    async def groups(request: Request, per: int = 6, q: str = "", kind: str = "", collection: str = ""):
        denied = gate(request)
        if denied is not None:
            return denied
        result = await asyncio.to_thread(lambda: content_index.groups(ContentLibrary(), per=per, q=q, kind=kind, collection=collection))
        return answer(request, result, content_index.etag("groups", per, q, kind, collection, result["revision"]))

    @app.get(prefix + "/index")
    async def index(request: Request, q: str = "", kind: str = "", source: str = "", collection: str = "",
                    offset: int = 0, limit: int = 60, sort: str = "updated"):
        denied = gate(request)
        if denied is not None:
            return denied
        result = await asyncio.to_thread(lambda: content_index.listing(
            ContentLibrary(), q=q, kind=kind, source=source, collection=collection, offset=offset, limit=limit, sort=sort))
        return answer(request, result, content_index.etag("index", q, kind, source, collection, offset, limit, sort, result["revision"]))

    @app.get(prefix + "/items/{item_id}/preview")
    async def preview(item_id: str, request: Request):
        denied = gate(request)
        if denied is not None:
            return denied

        def build():
            library, row = live(item_id)
            item = library.public(row)
            text = row["source_text"] or ""
            if row["kind"] in ("note", "page", "agent"):
                if not text and content_agents.is_custom(row["source"]):
                    try:
                        from shared.content_native import read_record
                        text = read_record(row["source"], row["source_id"]).get("body", "")
                    except LookupError:
                        text = ""
            media = None
            if row["path"]:
                media = {"type": mimetypes.guess_type(row["path"])[0] or "application/octet-stream",
                         "thumbnail": row["kind"] == "image"}
                path = Path(row["managed_path"] or row["path"])
                if not text and path.is_file() and Path(row["path"]).suffix.lower() in {
                    ".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".html", ".srt", ".vtt", ".log", ".py", ".js", ".css"
                }:
                    # Bound legacy whole-envelope reads as well as the preview returned to the screen.
                    if path.stat().st_size <= 2 * 1024 * 1024:
                        sample = b"".join(content_files.read_range(path, 0, PREVIEW_CHARS * 4 + 3))
                        if b"\x00" not in sample:
                            text = sample.decode("utf-8-sig", errors="replace")
                elif not text and path.is_file():
                    text = document_excerpt(path, Path(row["path"]).suffix.lower())
            identity = content_agents.describe_all(library, [row["source"]])[row["source"]]
            return {"item": item, "text": text[:PREVIEW_CHARS], "truncated": len(text) > PREVIEW_CHARS, "media": media,
                    "agent": identity, "removal": content_removal.plan(row), "abilities": content_access.abilities(request)}
        payload = await asyncio.to_thread(build)
        return answer(request, payload, content_index.etag("preview", item_id, payload["item"]["revision"], payload["abilities"]["role"]))

    @app.get(prefix + "/items/{item_id}/thumbnail")
    async def thumbnail(item_id: str, request: Request, size: int = 320):
        denied = gate(request)
        if denied is not None:
            return denied
        _, row = await asyncio.to_thread(live, item_id)
        path = Path(row["managed_path"] or row["path"] or "")
        if row["kind"] != "image" or not row["path"] or not path.is_file():
            raise HTTPException(404, "No preview")
        pixels = content_thumbs.nearest_size(size)
        made = await asyncio.to_thread(content_thumbs.thumbnail, path, (row["id"], row["revision"]), pixels)
        if made is None:
            raise HTTPException(404, "No preview")
        tag = f'"t{row["revision"]}-{pixels}"'
        headers = {"ETag": tag, "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}
        if _matches(request.headers.get("if-none-match", ""), tag):
            return Response(status_code=304, headers=headers)
        return Response(made[0], media_type=made[1], headers=headers)

    @app.delete(prefix + "/items/{item_id}")
    async def remove_item(item_id: str, request: Request, expected_revision: int):
        denied = gate(request, "delete")
        if denied is not None:
            return denied
        results = await asyncio.to_thread(lambda: content_removal.remove_many(ContentLibrary(), [{"id": item_id, "revision": expected_revision}]))
        result = results[0]
        if not result["ok"]:
            return error(result["error"], result["status"], **{k: v for k, v in result.items() if k in ("owner", "app", "mode", "stale", "missing")})
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.post(prefix + "/items/delete")
    async def remove_items(request: Request, payload: RemovalBatch):
        # The path ends in /delete so the shared remote policy treats it as a delete action too.
        denied = gate(request, "delete")
        if denied is not None:
            return denied
        results = await asyncio.to_thread(lambda: content_removal.remove_many(
            ContentLibrary(), [{"id": item.id, "revision": item.revision} for item in payload.items]))
        return JSONResponse({"results": results, "removed": sum(1 for r in results if r["ok"])}, headers={"Cache-Control": "no-store"})

    @app.post(prefix + "/items/organize")
    async def organize_items(request: Request, payload: OrganizeBatch):
        denied = gate(request, "edit")
        if denied is not None:
            return denied
        if payload.collection is None and not payload.add_labels and not payload.remove_labels:
            return error("Choose a collection or labels to apply.", 400)

        def run():
            library, results = ContentLibrary(), []
            drop = {label.strip().lower() for label in payload.remove_labels}
            for target in payload.items:
                try:
                    with library.connect() as db:
                        row = db.execute("SELECT * FROM content WHERE id=? AND deleted=0", (target.id,)).fetchone()
                    if not row:
                        raise LookupError("Content not found")
                    labels = [label for label in json.loads(row["labels"]) if label.strip().lower() not in drop]
                    labels += [label.strip() for label in payload.add_labels if label.strip()]
                    item = library.annotate(target.id, labels=list(dict.fromkeys(labels)), annotation=row["annotation"],
                                            collection=row["collection"] if payload.collection is None else payload.collection,
                                            expected_revision=target.revision)
                    results.append({"ok": True, "id": target.id, "item": library.compact(item)})
                except FileExistsError as exc:
                    results.append({"ok": False, "id": target.id, "status": 409, "error": str(exc), "stale": True})
                except LookupError as exc:
                    results.append({"ok": False, "id": target.id, "status": 404, "error": str(exc), "missing": True})
                except ValueError as exc:
                    results.append({"ok": False, "id": target.id, "status": 400, "error": str(exc)})
            return results
        results = await asyncio.to_thread(run)
        return JSONResponse({"results": results, "updated": sum(1 for r in results if r["ok"])}, headers={"Cache-Control": "no-store"})
