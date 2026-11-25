"""Authenticated content API shared by Admin and Organizer Agent."""
import asyncio
import hashlib
import html
import json
from pathlib import Path
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from shared import content_access, content_files
from shared.content_library import ContentLibrary, SOURCE, configure_output_root, configure_organization, infer_kind, output_dir, settings
from shared.platform_runtime import get_resources_root


class Annotation(BaseModel):
    labels: list[str] = Field(default_factory=list, max_length=24)
    annotation: str = Field(default="", max_length=8000)
    collection: str = Field(default="", max_length=100)
    expected_revision: int = Field(ge=1)


class Destination(BaseModel):
    output_root: str = Field(min_length=1, max_length=2048)


class Organization(BaseModel):
    auto_organize: bool = True
    semantic_labels: bool = True
    index_profile: bool | None = None
    index_reminders: bool | None = None
    index_private: bool | None = None  # omitted by older screens, which must not switch it off
    content_sync_enabled: bool | None = None
    notes_sync_enabled: bool | None = None


class TransferInput(BaseModel):
    filename: str = Field(min_length=1, max_length=180)
    size: int = Field(gt=0, le=20 * 1024 ** 3)
    title: str = Field(default="", max_length=300)
    source: str = Field(default="files_agent", max_length=80)
    source_id: str = Field(default="", max_length=256)


async def bounded_body(request, limit):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, "Request exceeds the transfer limit")
        body.extend(chunk)
    return bytes(body)


def register_content_routes(app, authorize, prefix="/api/admin/content", worker=None):
    assets = get_resources_root(Path(__file__).resolve().parents[1] / "server.py") / "assets"

    def json_response(data, status=200):
        return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})

    def owner(request):
        # Authorize first. The digest scopes resumable receipts without storing credentials.
        value = request.headers.get("authorization") or request.headers.get("cookie") or request.headers.get("x-autoyou-webrtc-session-id")
        if not value:
            raise HTTPException(401, "A signed-in session is required for resumable transfers")
        return hashlib.sha256(value.encode()).hexdigest()

    from shared.content_library_routes import register as register_screen_routes
    register_screen_routes(app, authorize, prefix, worker)

    @app.get(prefix + "/activity")
    async def library_activity(request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        from shared.content_worker import activity
        return json_response(await asyncio.to_thread(lambda: activity(allowed_sources=content_access.allowed_sources_filter(request))))

    @app.get(prefix + "/duplicates")
    async def library_duplicates(request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        from shared.content_storage import duplicates
        return json_response({"groups": await asyncio.to_thread(lambda: duplicates(allowed_sources=content_access.allowed_sources_filter(request)))})

    @app.put(prefix + "/organization")
    async def organization(request: Request, payload: Organization):
        denied = authorize(request)
        if denied is not None:
            return denied
        denied = content_access.deny(request, "manage")
        if denied is not None:
            return denied
        for source, flag in (("diary_agent", payload.index_private), ("profile_agent", payload.index_profile), ("reminders_agent", payload.index_reminders)):
            if flag is True:
                source_denied = content_access.deny_source(request, source, ignore_privacy=True)
                if source_denied is not None:
                    return source_denied
        result = await asyncio.to_thread(configure_organization, **payload.model_dump())
        if worker:
            worker.wake.set()
        return json_response(result)

    @app.post(prefix + "/migration/preview")
    async def preview_migration(request: Request, payload: Destination):
        denied = authorize(request)
        if denied is not None:
            return denied
        denied = content_access.deny(request, "manage")
        if denied is not None:
            return denied
        from shared.content_storage import migration_plan
        try:
            return json_response(await asyncio.to_thread(migration_plan, payload.output_root))
        except ValueError as error:
            return json_response({"error": str(error)}, 400)

    @app.post(prefix + "/migration")
    async def migrate(request: Request, payload: Destination):
        denied = authorize(request)
        if denied is not None:
            return denied
        denied = content_access.deny(request, "manage")
        if denied is not None:
            return denied
        from shared.content_worker import start_migration
        try:
            result = await asyncio.to_thread(start_migration, payload.output_root)
            if worker:
                worker.wake.set()
            return json_response(result, 202)
        except FileExistsError as error:
            return json_response({"error": str(error)}, 409)
        except (ValueError, OSError) as error:
            return json_response({"error": str(error)}, 400)

    @app.post(prefix + "/migration/{identifier}/cancel")
    async def cancel_migration(identifier: str, request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        denied = content_access.deny(request, "manage")
        if denied is not None:
            return denied
        from shared.content_worker import cancel_migration as cancel
        await asyncio.to_thread(cancel, identifier)
        return json_response({"success": True})

    @app.post(prefix + "/retry")
    async def retry_organization(request: Request, item_id: str = ""):
        denied = authorize(request)
        if denied is not None:
            return denied
        denied = content_access.deny(request, "edit")
        if denied is not None:
            return denied
        if not (await asyncio.to_thread(settings))["auto_organize"]:
            return json_response({"error": "Automatic organization is paused. Enable it in My Content's library settings before retrying."}, 409)
        library = ContentLibrary()
        if item_id:
            with library.connect() as db:
                row = db.execute("SELECT source FROM content WHERE id=? AND deleted=0", (item_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Content not found")
            denied = content_access.deny_source(request, row["source"])
            if denied is not None:
                return denied
        allowed = content_access.allowed_sources_filter(request)

        def retry():
            with library.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                rows = db.execute("SELECT id,source FROM content WHERE deleted=0 AND organization_state IN ('waiting_model','failed','missing')" +
                                  (" AND id=?" if item_id else ""), (item_id,) if item_id else ()).fetchall()
                queued = 0
                for row in rows:
                    if not library.source_enabled(row["source"], db=db) or (allowed is not None and row["source"] not in allowed):
                        continue
                    db.execute("UPDATE content SET retry_at=0,processed_version=0,organization_state='pending',revision=? WHERE id=?",
                               (library.tick(db), row["id"]))
                    queued += 1
                return queued
        queued = await asyncio.to_thread(retry)
        if worker:
            worker.wake.set()
        return json_response({"success": True, "queued": queued})

    @app.post(prefix + "/upload-sessions")
    async def begin_transfer(request: Request, payload: TransferInput):
        denied = authorize(request)
        if denied is not None:
            return denied
        denied = content_access.deny(request, "upload")
        if denied is not None:
            return denied
        source_denied = content_access.deny_source(request, payload.source)
        if source_denied is not None:
            return source_denied
        from shared.content_transfers import ContentTransfers
        return json_response(await asyncio.to_thread(lambda: ContentTransfers().begin(owner(request), **payload.model_dump())))

    @app.api_route(prefix + "/upload-sessions/{identifier}", methods=["GET", "PUT", "DELETE"])
    async def transfer(identifier: str, request: Request, offset: int = 0):
        denied = authorize(request)
        if denied is not None:
            return denied
        if request.method != "GET":
            denied = content_access.deny(request, "upload")
            if denied is not None:
                return denied
        from shared.content_transfers import ContentTransfers, CHUNK_BYTES
        identity = owner(request)
        transfers = ContentTransfers()
        with transfers.library.connect() as db:
            receipt = transfers.row(db, identifier, identity)
        source_denied = content_access.deny_source(request, receipt["source"])
        if source_denied is not None:
            return source_denied
        if request.method == "GET":
            return json_response(await asyncio.to_thread(transfers.status, identifier, identity))
        if request.method == "DELETE":
            await asyncio.to_thread(transfers.cancel, identifier, identity)
            return json_response({"success": True})
        transfers.status(identifier, identity)
        payload = await bounded_body(request, CHUNK_BYTES)
        return json_response(await asyncio.to_thread(transfers.append, identifier, identity, offset, payload))

    @app.post(prefix + "/upload-sessions/{identifier}/complete")
    async def complete_transfer(identifier: str, request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        denied = content_access.deny(request, "upload")
        if denied is not None:
            return denied
        from shared.content_transfers import ContentTransfers
        transfers, identity = ContentTransfers(), owner(request)
        with transfers.library.connect() as db:
            receipt = transfers.row(db, identifier, identity)
        source_denied = content_access.deny_source(request, receipt["source"])
        if source_denied is not None:
            return source_denied
        return json_response(await asyncio.to_thread(transfers.complete, identifier, identity))

    @app.get(prefix + "/items/{item_id}")
    async def metadata(item_id: str, request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        library = ContentLibrary()
        with library.connect() as db:
            row = db.execute("SELECT * FROM content WHERE id=? AND deleted=0", (item_id,)).fetchone()
            if not row or not library.source_enabled(row["source"], db=db):
                raise HTTPException(404, "Content not found")
        source_denied = content_access.deny_source(request, row["source"])
        if source_denied is not None:
            return source_denied
        return json_response({**library.public(row), "status": "ready"})

    @app.post(prefix + "/native")
    async def native_import(request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        denied = content_access.deny(request, "upload")
        if denied is not None:
            return denied
        from shared.content_native import import_record
        try:
            payload = json.loads(await bounded_body(request, 512 * 1024))
            if not isinstance(payload, dict):
                raise ValueError("Expected an agent record")
            return json_response(await asyncio.to_thread(import_record, payload))
        except FileExistsError as error:
            return json_response({"error": str(error)}, 409)
        except (ValueError, TypeError) as error:
            return json_response({"error": str(error)}, 400)

    @app.get(prefix + "/native/{source}/{identifier}")
    async def native_view(source: str, identifier: str, request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        if not ContentLibrary.source_enabled(source):
            raise HTTPException(404, "Record not found")
        source_denied = content_access.deny_source(request, source)
        if source_denied is not None:
            return source_denied
        from fastapi.responses import HTMLResponse
        from shared.content_native import read_record
        try:
            record = await asyncio.to_thread(read_record, source, identifier)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        links = ''.join('<li><a href="../../items/' + html.escape(item, quote=True) + '/content">Attachment ' + str(index + 1) + '</a></li>' for index, item in enumerate(record["attachments"]))
        body = '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + html.escape(record["title"]) + '</title><style>body{max-width:760px;margin:40px auto;padding:0 20px;background:#14111c;color:#f5efff;font:17px/1.7 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}a{color:#d7b8ff}</style><h1>' + html.escape(record["title"]) + '</h1><pre>' + html.escape(record["body"] + ('\n\n' + record["url"] if record["url"] else '')) + '</pre><ul>' + links + '</ul></html>'
        return HTMLResponse(body, headers={"Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"})

    @app.get(prefix + "/view")
    async def view(request: Request, preview: bool = False):
        denied = authorize(request)
        if denied is not None:
            return denied
        from fastapi.responses import HTMLResponse
        # Keep old /view and ?preview=true bookmarks on the same screen.
        template = (assets / "content-workspace.html").read_text(encoding="utf-8")
        return HTMLResponse(template.replace("__CONTENT_API__", prefix), headers={"Cache-Control": "no-store"})

    @app.post(prefix + "/refresh-existing")
    async def refresh_existing(request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        denied = content_access.deny(request, "edit")
        if denied is not None:
            return denied
        from shared.content_library_scan import refresh_existing as scan
        if worker and (await asyncio.to_thread(settings))["auto_organize"]:
            worker.last_scan = 0
            worker.wake.set()
            return json_response({"queued": True, "indexed": 0, "errors": []}, 202)
        return json_response(await asyncio.to_thread(scan))

    @app.get(prefix + "/assets/{name}")
    async def asset(name: str, request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        if name not in {"content-library.js", "content-drop.js", "content-library.css", "content-motion.css", "open-motion-LICENSE.txt",
                        "content-store.js", "content-screen.js", "content-screen.css", "content-workspace.js", "admin-ui.css"}:
            raise HTTPException(404)
        from fastapi.responses import FileResponse
        return FileResponse(assets / name, headers={"Cache-Control": "no-cache"})

    @app.get(prefix)
    async def listing(request: Request, q: str = "", kind: str = "", source: str = "", collection: str = "", after: int | None = None, offset: int = 0, limit: int = 100):
        denied = authorize(request)
        if denied is not None:
            return denied
        if source and ContentLibrary.source_enabled(source):
            source_denied = content_access.deny_source(request, source, ignore_privacy=True)
            if source_denied is not None:
                return source_denied
        result = await asyncio.to_thread(
            lambda: ContentLibrary().list(
                q=q, kind=kind, source=source, collection=collection, after=after,
                offset=offset, limit=limit, allowed_sources=content_access.allowed_sources_filter(request, include_disabled=after is not None),
            )
        )
        return json_response({**result, "settings": content_access.public_settings(request)})

    @app.put(prefix + "/settings")
    async def destination(request: Request, payload: Destination):
        denied = authorize(request)
        if denied is not None:
            return denied
        denied = content_access.deny(request, "manage")
        if denied is not None:
            return denied
        try:
            return json_response(await asyncio.to_thread(configure_output_root, payload.output_root))
        except (ValueError, OSError) as error:
            return json_response({"error": str(error)}, 400)

    @app.patch(prefix + "/items/{item_id}")
    async def annotate(item_id: str, request: Request, payload: Annotation):
        denied = authorize(request)
        if denied is not None:
            return denied
        denied = content_access.deny(request, "edit")
        if denied is not None:
            return denied
        library = ContentLibrary()
        with library.connect() as db:
            row = db.execute("SELECT source FROM content WHERE id=? AND deleted=0", (item_id,)).fetchone()
            if not row or not library.source_enabled(row["source"], db=db):
                return json_response({"error": "Content not found"}, 404)
        source_denied = content_access.deny_source(request, row["source"])
        if source_denied is not None:
            return source_denied
        try:
            return json_response(await asyncio.to_thread(lambda: library.annotate(item_id, **payload.model_dump())))
        except FileExistsError as error:
            return json_response({"error": str(error)}, 409)
        except LookupError as error:
            return json_response({"error": str(error)}, 404)
        except ValueError as error:
            return json_response({"error": str(error)}, 400)

    @app.post(prefix + "/import")
    async def import_file(request: Request, filename: str = "file", source: str = "files_agent"):
        denied = authorize(request)
        if denied is not None:
            return denied
        denied = content_access.deny(request, "upload")
        if denied is not None:
            return denied
        if SOURCE.fullmatch(source):
            source_denied = content_access.deny_source(request, source)
            if source_denied is not None:
                return source_denied
        name = Path(filename.replace("\\", "/")).name[:180] or "file"
        suffix = Path(name).suffix.lower()
        if len(suffix) > 16 or not all(char.isalnum() or char == "." for char in suffix):
            suffix = ""
        try:
            directory = output_dir(source)
        except ValueError as error:
            return json_response({"error": str(error)}, 400)
        identifier = uuid.uuid4().hex
        target = directory / (identifier + suffix)
        content_files.create(target)
        offset = 0
        try:
            async for part in request.stream():
                if offset + len(part) > 2 * 1024 ** 3:
                    raise HTTPException(413, "Use the TV or Audio resumable importer for files above 2 GiB")
                for start in range(0, len(part), content_files.CHUNK_SIZE):
                    chunk = part[start:start + content_files.CHUNK_SIZE]
                    offset = await asyncio.to_thread(content_files.append, target, offset, chunk)
            if not offset:
                raise HTTPException(400, "Choose a non-empty file")
            row = await asyncio.to_thread(lambda: ContentLibrary().publish(source, identifier, name, infer_kind(name), path=str(target)))
            return json_response(row, 201)
        except BaseException:
            content_files.discard(target)
            raise

    @app.api_route(prefix + "/stream/{item_id}", methods=["GET", "HEAD"])
    @app.api_route(prefix + "/items/{item_id}/content", methods=["GET", "HEAD"])
    async def content(item_id: str, request: Request, download: int = 0, preview: bool = False):
        denied = authorize(request)
        if denied is not None:
            return denied
        library = ContentLibrary()
        with library.connect() as db:
            row = db.execute("SELECT source,path,managed_path,title,digest FROM content WHERE id=? AND deleted=0", (item_id,)).fetchone()
            if not row or not row["path"] or not library.source_enabled(row["source"], db=db):
                raise HTTPException(404, "Open this item in its source agent")
        source_denied = content_access.deny_source(request, row["source"])
        if source_denied is not None:
            return source_denied
        from shared.content_storage import resolve_path
        path = resolve_path(row)
        if not path.is_file():
            raise HTTPException(404, "This file is unavailable; check its source folder")
        import mimetypes
        response = content_files.response(path, request, mimetypes.guess_type(row["path"])[0])
        # User-supplied HTML/SVG must not execute with the owner's origin.
        suffix = Path(row["path"]).suffix.lower()
        inline_pdf = preview and suffix == ".pdf" and b"".join(content_files.read_range(path, 0, 4)) == b"%PDF-"
        if download or (not inline_pdf and suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".mp4", ".webm", ".mov", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac"}):
            from urllib.parse import quote
            response.headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(row["title"], safe="")
        response.headers["Content-Security-Policy"] = "sandbox"
        return response
