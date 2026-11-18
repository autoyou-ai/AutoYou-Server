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

from shared import content_files
from shared.content_library import ContentLibrary, configure_output_root, infer_kind, output_dir, settings


class Annotation(BaseModel):
    labels: list[str] = Field(default_factory=list, max_length=24)
    annotation: str = Field(default="", max_length=8000)
    collection: str = Field(default="", max_length=100)
    expected_revision: int = Field(ge=1)


class Destination(BaseModel):
    output_root: str = Field(min_length=1, max_length=2048)


class TransferInput(BaseModel):
    filename: str = Field(min_length=1, max_length=180)
    size: int = Field(gt=0, le=20 * 1024 ** 3)
    title: str = Field(default="", max_length=300)
    source: str = Field(default="files_agent", max_length=80)
    source_id: str = Field(default="", max_length=256)


def register_content_routes(app, authorize, prefix="/api/admin/content"):
    def json_response(data, status=200):
        return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})

    def owner(request):
        # Authorize first. The digest scopes resumable receipts without storing credentials.
        value = request.headers.get("authorization") or request.headers.get("cookie") or request.headers.get("x-autoyou-webrtc-session-id")
        if not value:
            raise HTTPException(401, "A signed-in session is required for resumable transfers")
        return hashlib.sha256(value.encode()).hexdigest()

    async def bounded_body(request, limit):
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > limit:
                raise HTTPException(413, "Request exceeds the transfer limit")
            body.extend(chunk)
        return bytes(body)

    @app.post(prefix + "/upload-sessions")
    async def begin_transfer(request: Request, payload: TransferInput):
        denied = authorize(request)
        if denied is not None:
            return denied
        from shared.content_transfers import ContentTransfers
        return json_response(await asyncio.to_thread(lambda: ContentTransfers().begin(owner(request), **payload.model_dump())))

    @app.api_route(prefix + "/upload-sessions/{identifier}", methods=["GET", "PUT", "DELETE"])
    async def transfer(identifier: str, request: Request, offset: int = 0):
        denied = authorize(request)
        if denied is not None:
            return denied
        from shared.content_transfers import ContentTransfers, CHUNK_BYTES
        identity = owner(request)
        transfers = ContentTransfers()
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
        from shared.content_transfers import ContentTransfers
        return json_response(await asyncio.to_thread(lambda: ContentTransfers().complete(identifier, owner(request))))

    @app.get(prefix + "/items/{item_id}")
    async def metadata(item_id: str, request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        library = ContentLibrary()
        with library.connect() as db:
            row = db.execute("SELECT * FROM content WHERE id=? AND deleted=0", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Content not found")
        return json_response({**library.public(row), "status": "ready"})

    @app.post(prefix + "/native")
    async def native_import(request: Request):
        denied = authorize(request)
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
    async def view(request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        from fastapi.responses import HTMLResponse
        template = (Path(__file__).resolve().parents[1] / "assets" / "content-library.html").read_text(encoding="utf-8")
        relative = "../" + prefix.rstrip("/").rsplit("/", 1)[-1]
        return HTMLResponse(template.replace("__CONTENT_API__", relative), headers={"Cache-Control": "no-store"})

    @app.post(prefix + "/refresh-existing")
    async def refresh_existing(request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        from shared.content_library_scan import refresh_existing as scan
        return json_response(await asyncio.to_thread(scan))

    @app.get(prefix + "/assets/{name}")
    async def asset(name: str, request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        if name not in {"content-library.js", "content-drop.js", "content-library.css", "content-motion.css", "open-motion-LICENSE.txt"}:
            raise HTTPException(404)
        from fastapi.responses import FileResponse
        return FileResponse(Path(__file__).resolve().parents[1] / "assets" / name)

    @app.get(prefix)
    async def listing(request: Request, q: str = "", kind: str = "", source: str = "", collection: str = "", after: int | None = None, offset: int = 0, limit: int = 100):
        denied = authorize(request)
        if denied is not None:
            return denied
        result = await asyncio.to_thread(lambda: ContentLibrary().list(q=q, kind=kind, source=source, collection=collection, after=after, offset=offset, limit=limit))
        return json_response({**result, "settings": settings()})

    @app.put(prefix + "/settings")
    async def destination(request: Request, payload: Destination):
        denied = authorize(request)
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
        try:
            return json_response(await asyncio.to_thread(lambda: ContentLibrary().annotate(item_id, **payload.model_dump())))
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
    async def content(item_id: str, request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        library = ContentLibrary()
        with library.connect() as db:
            row = db.execute("SELECT path,title FROM content WHERE id=? AND deleted=0", (item_id,)).fetchone()
        if not row or not row["path"] or not Path(row["path"]).is_file():
            raise HTTPException(404, "Open this item in its source agent")
        response = content_files.response(Path(row["path"]), request)
        # User-supplied HTML/SVG must not execute with the owner's origin.
        if Path(row["path"]).suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".mp4", ".webm", ".mov", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac"}:
            from urllib.parse import quote
            response.headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(row["title"], safe="")
        response.headers["Content-Security-Policy"] = "sandbox"
        return response
