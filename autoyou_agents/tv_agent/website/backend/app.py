"""AutoYou TV: open viewing, role-based Studio and OTP-only Settings."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from autoyou_agents.shared_tools.scheduler_mission_control import install_agent_website_auth

from .auth import Auth, COOKIE_NAME, SESSION_SECONDS
from .jobs import Jobs, executable
from . import playback
from .media import (VIDEO_EXTENSIONS, CONTENT_TYPES, file_stream, parse_name, permitted_path, public_url,
                    read_ticket, remote_stream, subtitle_content, subtitles)
from .store import Store, uid
from .uploads import MAX_UPLOAD_BYTES, Uploads, upload_owner

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "web"


class Login(BaseModel):
    code: str = Field(default="", max_length=16)
    totp_code: str = Field(default="", max_length=16)


class InvitationLogin(BaseModel):
    token: str = Field(min_length=10, max_length=512)


class FolderInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None


class SourceInput(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    name: str = Field(default="", max_length=200)


class ImportInput(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    title: str = Field(default="", max_length=300)
    kind: Literal["movie", "episode", "video"] = "movie"
    mode: Literal["stream", "download"] = "stream"
    folder_id: str | None = None
    year: int | None = Field(default=None, ge=1880, le=2200)


class ItemEdit(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    kind: Literal["movie", "episode", "video"] | None = None
    folder_id: str | None = None
    year: int | None = Field(default=None, ge=1880, le=2200)
    series: str | None = Field(default=None, max_length=300)
    season: int | None = Field(default=None, ge=0, le=999)
    episode: int | None = Field(default=None, ge=0, le=9999)


class MatchInput(BaseModel):
    imdb_id: str = Field(default="", pattern=r"^(?:tt\d{7,10})?$", max_length=12)


class SettingsInput(BaseModel):
    library_name: str = Field(default="AutoYou TV", min_length=1, max_length=100)
    autoplay_next: bool = True
    adaptive_streaming: bool = True
    omdb_api_key: str | None = Field(default=None, max_length=512)


class InvitationInput(BaseModel):
    label: str = Field(default="Editor", max_length=100)
    role: Literal["editor", "admin"] = "editor"
    days: int = Field(default=7, ge=1, le=30)


class UploadInput(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    size: int = Field(gt=0, le=MAX_UPLOAD_BYTES)
    title: str = Field(default="", max_length=300)
    kind: Literal["movie", "episode", "video"] = "movie"
    folder_id: str | None = None


def public_folder(folder: dict) -> dict:
    return {k: v for k, v in folder.items() if k not in {"root_path", "relative_path"}} | {"source": bool(folder.get("root_path"))}


def public_item(item: dict) -> dict:
    result = {k: v for k, v in item.items() if k not in {"file_path", "source_url", "source_key"}}
    result["technical"] = {k: v for k, v in item["technical"].items() if k not in {"browser_path", "hls_source"}}
    version = item["technical"].get("hls_version", "")
    result["stream_url"] = (f"api/playback/{item['id']}/{version}/master.m3u8" if playback.ID.fullmatch(version)
                            else f"api/stream/{item['id']}")
    result["artwork_url"] = f"api/artwork/{item['id']}" if item["technical"].get("artwork") else ""
    result["is_remote"] = bool(item.get("source_url"))
    result["is_hls"] = bool(version or (item.get("source_url") and urlsplit(item["source_url"]).path.lower().endswith(".m3u8")))
    return result


def create_app(data_dir: Path | None = None, *, otp_verifier=None) -> FastAPI:
    def services():
        if application.state.store is None:
            application.state.store = Store(data_dir)
            application.state.auth = Auth(application.state.store, otp_verifier)
            application.state.jobs = Jobs(application.state.store)
            application.state.uploads = Uploads(application.state.store)
        return application.state.store, application.state.auth, application.state.jobs

    @asynccontextmanager
    async def lifespan(_app):
        _, _, jobs = services()
        await jobs.start()
        try:
            yield
        finally:
            await jobs.stop()

    application = FastAPI(title="AutoYou TV", lifespan=lifespan)
    application.state.store = None
    # Reuse AutoYou's CSRF/session adapter, while explicitly making the viewing
    # surface public. Every mutation below applies its own editor/OTP boundary.
    install_agent_website_auth(application, agent_name="tv_agent", title="AutoYou TV",
                               is_authenticated=lambda request: True, shared_session_upgrade=False)

    @application.exception_handler(LookupError)
    async def missing(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @application.exception_handler(ValueError)
    async def invalid(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @application.middleware("http")
    async def response_headers(request: Request, call_next):
        media_request = request.url.path.startswith(("/api/stream/", "/api/playback/", "/api/subtitles/"))
        response = Response(status_code=204) if media_request and request.method == "OPTIONS" else await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if media_request:
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Range, If-Range, If-None-Match"
            response.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, ETag"
        if request.url.path.startswith("/api/") and not media_request:
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/health")
    async def health():
        return {"success": True, "agent": "tv_agent"}

    @application.get("/")
    async def index():
        if not (FRONTEND / "index.html").is_file():
            raise HTTPException(503, "Build the TV frontend with npm ci and npm run build")
        return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})

    if (FRONTEND / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    @application.get("/api/config")
    async def config(request: Request):
        store, auth, _ = services()
        return {"library_name": store.setting("library_name", "AutoYou TV"),
                "autoplay_next": store.setting("autoplay_next", True), "auth": auth.session(request),
                "capabilities": {"download": bool(executable("yt-dlp")), "transcode": bool(executable("ffmpeg")),
                                 "probe": bool(executable("ffprobe"))}}

    @application.get("/api/auth/status")
    async def auth_status(request: Request):
        _, auth, _ = services()
        return auth.session(request)

    def session_response(request: Request, auth: Auth, token: str):
        response = JSONResponse({"success": True, "token": token})
        # A scoped cookie also works through AutoYou's path proxy. Bearer tokens
        # support clients whose data-channel HTTP transport cannot carry cookies.
        response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", secure=request.url.scheme == "https",
                            max_age=SESSION_SECONDS, path="/")
        return response

    @application.post("/api/auth/login")
    async def login(request: Request, payload: Login):
        _, auth, _ = services()
        token = await asyncio.to_thread(auth.login_otp, request, (payload.code or payload.totp_code).strip())
        return session_response(request, auth, token)

    @application.post("/api/auth/invite")
    async def accept_invite(request: Request, payload: InvitationLogin):
        _, auth, _ = services()
        return session_response(request, auth, auth.login_invitation(payload.token))

    @application.post("/api/auth/logout")
    async def logout(request: Request):
        _, auth, _ = services()
        auth.logout(request)
        response = JSONResponse({"success": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @application.get("/api/library")
    async def library(q: str = Query("", max_length=200), kind: str = "", folder_id: str | None = None,
                      genre: str = Query("", max_length=80), sort: str = "recent",
                      ids: str | None = Query(None, max_length=8000),
                      offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        store, _, _ = services()
        selected_ids = ids.split(",")[:200] if ids else None
        page = store.library(query=q, kind=kind, folder_id=folder_id, genre=genre, sort=sort,
                             offset=offset, limit=limit, ids=selected_ids)
        page["items"] = [public_item(item) for item in page["items"]]
        with store.connect() as db:
            genres = db.execute("SELECT DISTINCT json_extract(metadata,'$.genre') FROM items WHERE status!='deleted'").fetchall()
        page["genres"] = sorted({genre.strip() for row in genres if row[0] for genre in row[0].split(",") if genre.strip()})
        return page

    @application.get("/api/items/{item_id}")
    async def detail(item_id: str):
        store, _, _ = services()
        item = store.item(item_id)
        if item["status"] == "deleted":
            raise HTTPException(404, "This title was removed from the library")
        result = public_item(item)
        try:
            result["subtitles"] = subtitles(store, item)
        except HTTPException:
            result["subtitles"] = []
        result["episodes"], result["next_episode"] = [], None
        if item.get("series"):
            with store.connect() as db:
                ids = db.execute("SELECT id FROM items WHERE series=? AND kind='episode' AND status='ready' ORDER BY season,episode,title",
                                 (item["series"],)).fetchall()
            episodes = [public_item(store.item(row[0])) for row in ids]
            result["episodes"] = episodes
            index = next((i for i, row in enumerate(episodes) if row["id"] == item_id), -1)
            if index >= 0 and index + 1 < len(episodes):
                result["next_episode"] = episodes[index + 1]
        return result

    @application.get("/api/folders")
    async def folder_list(parent_id: str | None = None, all: bool = False):
        store, _, _ = services()
        breadcrumbs = []
        current = parent_id
        for _ in range(50):
            if not current:
                break
            folder = store.folder(current)
            breadcrumbs.insert(0, public_folder(folder))
            current = folder["parent_id"]
        return {"folders": [public_folder(f) for f in store.folders(parent_id, all_folders=all)], "breadcrumbs": breadcrumbs}

    @application.post("/api/folders")
    async def add_folder(request: Request, payload: FolderInput):
        store, auth, _ = services()
        auth.require_editor(request)
        return public_folder(store.add_folder(payload.name, payload.parent_id))

    @application.patch("/api/folders/{folder_id}")
    async def rename_folder(request: Request, folder_id: str, payload: FolderInput):
        store, auth, _ = services()
        auth.require_editor(request)
        store.folder(folder_id)
        if not payload.name.strip():
            raise ValueError("Enter a folder name")
        with store.connect() as db:
            db.execute("UPDATE folders SET name=? WHERE id=?", (payload.name.strip(), folder_id))
        return public_folder(store.folder(folder_id))

    @application.delete("/api/folders/{folder_id}")
    async def delete_folder(request: Request, folder_id: str):
        store, auth, _ = services()
        auth.require_editor(request)
        folder = store.folder(folder_id)
        if folder["root_path"]:
            raise ValueError("Manage source folders in OTP-protected Settings")
        with store.connect() as db:
            db.execute("UPDATE folders SET parent_id=? WHERE parent_id=?", (folder["parent_id"], folder_id))
            db.execute("DELETE FROM folders WHERE id=?", (folder_id,))
        return {"success": True}

    @application.post("/api/folders/{folder_id}/scan")
    async def rescan(request: Request, folder_id: str):
        _, auth, jobs = services()
        auth.require_editor(request)
        return await jobs.enqueue("scan", {"folder_id": folder_id})

    @application.post("/api/items")
    async def add_link(request: Request, payload: ImportInput):
        store, auth, jobs = services()
        auth.require_editor(request)
        url = await asyncio.to_thread(public_url, payload.url)
        if payload.folder_id:
            store.folder(payload.folder_id)
        if payload.mode == "download":
            return {"job": await jobs.enqueue("download", payload.model_dump())}
        title = payload.title.strip() or parse_name(unquote(Path(urlsplit(url).path).name) or "Untitled stream")["title"]
        item, _ = store.add_item(source_key="url:" + url, source_url=url, title=title, kind=payload.kind,
                                 year=payload.year, folder_id=payload.folder_id)
        job = await jobs.enqueue("metadata", {"item_id": item["id"]})
        return {"item": public_item(item), "job": job}

    @application.post("/api/uploads")
    async def upload(request: Request, folder_id: str | None = None,
                     title: str = Query("", max_length=300), kind: Literal["movie", "episode", "video"] = "movie"):
        store, auth, jobs = services()
        auth.require_editor(request)
        if folder_id:
            store.folder(folder_id)
        filename = unquote(request.headers.get("X-File-Name", "video.mp4"))
        suffix = Path(filename).suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            raise HTTPException(415, "Upload a video file such as MP4, WebM, MOV or MKV")
        destination = store.media_dir / (uid() + suffix)
        temporary = destination.with_suffix(suffix + ".uploading")
        written = 0
        try:
            with temporary.open("xb") as handle:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "Video exceeds the 20 GB upload limit")
                    await asyncio.to_thread(handle.write, chunk)
            if written == 0:
                raise HTTPException(400, "The uploaded file is empty")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        info = parse_name(filename)
        info["title"] = title.strip() or info["title"]
        info["kind"] = "episode" if info["episode"] is not None else kind
        item, _ = store.add_item(source_key="file:" + str(destination), file_path=str(destination), folder_id=folder_id,
                                 technical={"size": written, "extension": suffix}, **info)
        await jobs.enqueue("prepare", {"item_id": item["id"]})
        return {"item": public_item(item)}

    @application.post("/api/upload-sessions")
    async def begin_upload(request: Request, payload: UploadInput):
        _, auth, _ = services()
        auth.require_editor(request)
        return await asyncio.to_thread(application.state.uploads.begin, upload_owner(request), **payload.model_dump())

    @application.get("/api/upload-sessions/{upload_id}")
    async def upload_status(request: Request, upload_id: str):
        _, auth, _ = services()
        auth.require_editor(request)
        return application.state.uploads.status(upload_id, upload_owner(request))

    @application.put("/api/upload-sessions/{upload_id}")
    async def upload_chunk(request: Request, upload_id: str, offset: int = Query(0, ge=0)):
        _, auth, _ = services()
        auth.require_editor(request)
        return await application.state.uploads.append(request, upload_id, upload_owner(request), offset)

    @application.post("/api/upload-sessions/{upload_id}/complete")
    async def complete_upload(request: Request, upload_id: str):
        store, auth, jobs = services()
        auth.require_editor(request)
        item = await asyncio.to_thread(application.state.uploads.complete, upload_id, upload_owner(request))
        if not item["technical"]:
            await jobs.enqueue("prepare", {"item_id": item["id"]})
        return {"item": public_item(item)}

    @application.delete("/api/upload-sessions/{upload_id}")
    async def cancel_upload(request: Request, upload_id: str):
        _, auth, _ = services()
        auth.require_editor(request)
        await asyncio.to_thread(application.state.uploads.cancel, upload_id, upload_owner(request))
        return {"success": True}

    @application.patch("/api/items/{item_id}")
    async def edit_item(request: Request, item_id: str, payload: ItemEdit):
        store, auth, _ = services()
        auth.require_editor(request)
        changes = payload.model_dump(exclude_unset=True)
        if "title" in changes and (changes["title"] is None or not changes["title"].strip()):
            raise ValueError("Enter a title")
        return public_item(store.update_item(item_id, changes))

    @application.delete("/api/items/{item_id}")
    async def remove_item(request: Request, item_id: str):
        store, auth, _ = services()
        auth.require_editor(request)
        store.update_item(item_id, {"status": "deleted"})
        return {"success": True}

    @application.post("/api/items/{item_id}/metadata")
    async def rematch(request: Request, item_id: str, payload: MatchInput):
        store, auth, jobs = services()
        auth.require_editor(request)
        store.item(item_id)
        return await jobs.enqueue("metadata", {"item_id": item_id, "imdb_id": payload.imdb_id})

    @application.post("/api/items/{item_id}/transcode")
    async def transcode(request: Request, item_id: str):
        store, auth, jobs = services()
        auth.require_editor(request)
        store.item(item_id)
        if any(j["kind"] == "transcode" and j["payload"].get("item_id") == item_id and j["status"] in {"queued", "running"} for j in store.jobs()):
            raise HTTPException(409, "This title already has a rendition in progress")
        return await jobs.enqueue("transcode", {"item_id": item_id})

    @application.post("/api/items/{item_id}/streaming")
    async def optimize_streaming(request: Request, item_id: str):
        store, auth, jobs = services()
        auth.require_editor(request)
        item = store.item(item_id)
        if not item.get("file_path") or item["status"] == "deleted":
            raise ValueError("Download this title before optimizing streaming")
        if any(j["kind"] == "streaming" and j["payload"].get("item_id") == item_id and j["status"] in {"queued", "running"} for j in store.jobs()):
            raise HTTPException(409, "This title is already being optimized")
        return await jobs.enqueue("streaming", {"item_id": item_id})

    @application.get("/api/items/{item_id}/playback")
    async def playback_info(item_id: str):
        store, _, _ = services()
        item = store.item(item_id)
        if item["status"] == "deleted":
            raise HTTPException(404, "This title was removed")
        optimized = playback.ready(store, item)
        version = item["technical"].get("hls_version", "")
        is_hls = optimized or bool(item.get("source_url") and urlsplit(item["source_url"]).path.lower().endswith(".m3u8"))
        return {"version": 1, "item_id": item_id, "title": item["title"],
                "url": f"api/playback/{item_id}/{version}/master.m3u8" if optimized else f"api/stream/{item_id}",
                "fallback_url": f"api/stream/{item_id}", "is_hls": is_hls,
                "mime_type": "application/vnd.apple.mpegurl" if is_hls else CONTENT_TYPES.get(
                    Path(item["technical"].get("browser_path") or item.get("file_path") or urlsplit(item.get("source_url") or "").path).suffix.lower(), "video/mp4"),
                "optimized": optimized, "qualities": item["technical"].get("streaming_qualities", []) if optimized else [],
                "subtitles": subtitles(store, item) if item.get("file_path") else []}

    @application.api_route("/api/playback/{item_id}/{version}/{resource:path}", methods=["GET", "HEAD"])
    async def playback_resource(item_id: str, version: str, resource: str, request: Request):
        store, _, _ = services()
        path = playback.resource_path(store, store.item(item_id), version, resource)
        response = file_stream(path, request)
        response.headers["Cache-Control"] = "private, max-age=86400, immutable"
        return response

    @application.get("/api/jobs")
    async def job_list(request: Request):
        store, auth, _ = services()
        auth.require_editor(request)
        return {"jobs": store.jobs()}

    @application.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(request: Request, job_id: str):
        _, auth, jobs = services()
        auth.require_editor(request)
        await jobs.cancel(job_id)
        return {"success": True}

    @application.post("/api/jobs/{job_id}/retry")
    async def retry_job(request: Request, job_id: str):
        store, auth, jobs = services()
        auth.require_editor(request)
        old = store.job(job_id)
        if old["status"] not in {"failed", "cancelled"}:
            raise ValueError("Only failed or cancelled imports can be retried")
        return await jobs.enqueue(old["kind"], old["payload"])

    @application.get("/api/stream/jobs/events")
    async def job_events(request: Request):
        store, auth, _ = services()
        auth.require_editor(request)
        async def events():
            previous = ""
            while not await request.is_disconnected():
                if auth.session(request)["role"] not in {"editor", "admin"}:
                    break
                current = json.dumps(store.jobs())
                if current != previous:
                    yield "event: jobs\ndata: " + current + "\n\n"
                    previous = current
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(2)
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @application.api_route("/api/stream/{item_id}", methods=["GET", "HEAD"])
    async def stream(item_id: str, request: Request, ticket: str = Query("", max_length=16000)):
        store, _, _ = services()
        item = store.item(item_id)
        if item["status"] == "deleted":
            raise HTTPException(404, "This title was removed")
        if ticket:
            return await remote_stream(store, item_id, read_ticket(store, item_id, ticket), request)
        if item.get("file_path"):
            path = item["technical"].get("browser_path") or item["file_path"]
            return file_stream(permitted_path(store, path), request)
        return await remote_stream(store, item_id, item["source_url"], request)

    @application.get("/api/subtitles/{item_id}/{track_id}")
    async def subtitle(item_id: str, track_id: str):
        store, _, _ = services()
        return Response(subtitle_content(store, store.item(item_id), track_id), media_type="text/vtt")

    @application.get("/api/artwork/{item_id}")
    async def artwork(item_id: str):
        store, _, _ = services()
        item = store.item(item_id)
        path = store.artwork_dir / (item_id + ".jpg")
        if item["status"] == "deleted" or not path.is_file() or not path.resolve().is_relative_to(store.artwork_dir.resolve()):
            raise HTTPException(404, "Artwork not available")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @application.get("/api/settings/browse")
    async def browse_source(request: Request, path: str = Query("", max_length=4096)):
        store, auth, _ = services()
        auth.require_settings(request)
        selected = Path(path).expanduser().resolve() if path else store.media_dir
        if not selected.is_dir():
            raise ValueError("Choose an existing folder on the server")
        directories = []
        try:
            for child in selected.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    directories.append({"name": child.name, "path": str(child)})
                    if len(directories) > 400:
                        break
        except OSError:
            raise ValueError("This folder could not be opened") from None
        locations = [{"label": "TV uploads", "path": str(store.media_dir)}]
        # During tests/QA, browse only synthetic locations by default.
        import os
        home = Path(os.environ["AUTOYOU_TEST_ROOT"]) if os.getenv("AUTOYOU_TEST_ROOT") else Path.home()
        if home.is_dir():
            locations.append({"label": "Home", "path": str(home)})
        if os.name == "nt" and not os.getenv("AUTOYOU_TEST_ROOT") and hasattr(os, "listdrives"):
            locations.extend({"label": drive, "path": drive} for drive in os.listdrives())
        return {"path": str(selected), "parent": str(selected.parent) if selected.parent != selected else None,
                "directories": sorted(directories[:400], key=lambda row: row["name"].lower()),
                "locations": locations, "truncated": len(directories) > 400}

    @application.get("/api/settings")
    async def settings(request: Request):
        store, auth, _ = services()
        auth.require_settings(request)
        return {"library_name": store.setting("library_name", "AutoYou TV"),
                "adaptive_streaming": store.setting("adaptive_streaming", True),
                "autoplay_next": store.setting("autoplay_next", True), "omdb_configured": bool(store.key()),
                "sources": [f for f in store.folders(all_folders=True) if f["root_path"] and not f["relative_path"]],
                "invitations": auth.invitations()}

    @application.post("/api/settings")
    async def save_settings(request: Request, payload: SettingsInput):
        store, auth, _ = services()
        auth.require_settings(request)
        if not payload.library_name.strip():
            raise ValueError("Enter a library name")
        if payload.omdb_api_key is not None:
            store.set_key(payload.omdb_api_key)
        store.set_setting("library_name", payload.library_name.strip())
        store.set_setting("autoplay_next", payload.autoplay_next)
        store.set_setting("adaptive_streaming", payload.adaptive_streaming)
        return {"success": True, "omdb_configured": bool(store.key())}

    @application.post("/api/settings/sources")
    async def add_source(request: Request, payload: SourceInput):
        store, auth, jobs = services()
        auth.require_settings(request)
        path = Path(payload.path).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("Choose an existing folder on the server")
        folder = store.add_folder(payload.name.strip() or path.name or "Media", root_path=str(path))
        return {"folder": public_folder(folder), "job": await jobs.enqueue("scan", {"folder_id": folder["id"]})}

    @application.delete("/api/settings/sources/{folder_id}")
    async def remove_source(request: Request, folder_id: str):
        store, auth, _ = services()
        auth.require_settings(request)
        folder = store.folder(folder_id)
        if not folder["root_path"] or folder["relative_path"]:
            raise ValueError("Choose a source root")
        with store.connect() as db:
            db.execute("DELETE FROM folders WHERE root_path=?", (folder["root_path"],))
            # Source files remain untouched, but removed source entries no longer stream.
            rows = db.execute("SELECT id,file_path FROM items WHERE file_path IS NOT NULL").fetchall()
            for row in rows:
                if Path(row["file_path"]).is_relative_to(Path(folder["root_path"])):
                    db.execute("UPDATE items SET status='deleted' WHERE id=?", (row["id"],))
        return {"success": True}

    @application.post("/api/settings/invitations")
    async def invitation(request: Request, payload: InvitationInput):
        _, auth, _ = services()
        auth.require_settings(request)
        return auth.create_invitation(payload.label, payload.role, payload.days)

    @application.delete("/api/settings/invitations/{invitation_id}")
    async def revoke(request: Request, invitation_id: str):
        _, auth, _ = services()
        auth.require_settings(request)
        auth.revoke(invitation_id)
        return {"success": True}

    @application.post("/api/settings/enrich")
    async def enrich_library(request: Request):
        store, auth, jobs = services()
        auth.require_settings(request)
        if not store.key():
            raise ValueError("Configure the OMDb key first")
        with store.connect() as db:
            ids = db.execute("SELECT id FROM items WHERE status!='deleted'").fetchall()
        for row in ids:
            await jobs.enqueue("metadata", {"item_id": row[0]})
        return {"queued": len(ids)}

    return application


app = create_app()
