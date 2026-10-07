"""Protected Game Studio website and the local engine protocol description."""

import asyncio
import hashlib
from io import BytesIO
from ipaddress import ip_address
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from shared.runtime_module_loader import import_autoyou_shared_tools_module
from shared.remote_access_policy import REMOTE_BROWSER_IDENTITY_HEADERS


_smc = import_autoyou_shared_tools_module(
    "autoyou_agents.shared_tools.scheduler_mission_control", anchor=__file__
)
_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_UNREAL_PLUGIN_DIR = _FRONTEND_DIR / "assets" / "unreal" / "AutoYouGameInput"
_UNREAL_PLUGIN_FILES = (
    "AutoYouGameInput.uplugin",
    "README.md",
    "Source/AutoYouGameInput/AutoYouGameInput.Build.cs",
    "Source/AutoYouGameInput/Public/AutoYouGameInputSubsystem.h",
    "Source/AutoYouGameInput/Private/AutoYouGameInputSubsystem.cpp",
)
_MAX_MOBILE_GAME_BYTES = 511 * 1024
_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
_DOWNLOAD_WORKERS = asyncio.Semaphore(4)


def _download_source(path: Path) -> bytes:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > 256 * 1024:
        raise ValueError("Download source is unavailable")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as source:
        opened = os.fstat(source.fileno())
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("Download source changed")
        data = source.read(256 * 1024 + 1)
    if len(data) > 256 * 1024:
        raise ValueError("Download source exceeds the limit")
    return data


def _game_download(download_id: str) -> bytes:
    if download_id == "python-adapter":
        return _download_source(_FRONTEND_DIR / "assets" / "game_input_client.py")
    if download_id != "unreal-plugin":
        raise ValueError("Unknown game download")
    archive = BytesIO()
    with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
        for relative in _UNREAL_PLUGIN_FILES:
            info = ZipInfo("AutoYouGameInput/" + relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, _download_source(_UNREAL_PLUGIN_DIR / relative))
    data = archive.getvalue()
    if len(data) > _MAX_DOWNLOAD_BYTES:
        raise ValueError("Download exceeds the limit")
    return data


async def _owned_game_download(download_id: str) -> bytes:
    from shared.iroh_media import _join_owned
    async with _DOWNLOAD_WORKERS:
        return await _join_owned(asyncio.create_task(asyncio.to_thread(_game_download, download_id)))


def _local_game_page(websocket: WebSocket) -> bool:
    try:
        local = ip_address(websocket.client.host).is_loopback if websocket.client else False
    except ValueError:
        local = False
    origin = urlsplit(websocket.headers.get("origin", ""))
    return bool(
        local and origin.scheme in {"http", "https"}
        and origin.netloc.lower() == websocket.headers.get("host", "").lower()
        and not any(name in websocket.headers for name in (
            "forwarded", "x-forwarded-for", "x-real-ip", *REMOTE_BROWSER_IDENTITY_HEADERS
        ))
    )


def _extra_routes(app, agent_name):
    @app.post("/api/game/publish/{game_id}")
    async def publish_game(request: Request, game_id: str):
        auth = _smc._describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        server = _smc._runtime_server()
        if auth.get("auth_mode") == "open" and not server._is_logged_in(request):
            return JSONResponse({"success": False, "error": "Sign in to publish a game"}, status_code=401)
        if re.fullmatch(r"[a-z0-9-]{1,64}", game_id) is None:
            return JSONResponse({"success": False, "error": "Use a short lowercase game name"}, status_code=400)
        html = bytearray()
        async for part in request.stream():
            if len(part) > _MAX_MOBILE_GAME_BYTES - len(html):
                return JSONResponse({"success": False, "error": "Game exceeds 511 KB"}, status_code=413)
            html.extend(part)
        try:
            html.decode("utf-8")
        except UnicodeError:
            return JSONResponse({"success": False, "error": "Game must be UTF-8 HTML"}, status_code=400)
        if re.search(br"<head(?=[\s>])[^>]*>", html[:4096], re.IGNORECASE) is None:
            return JSONResponse({"success": False, "error": "Game HTML needs a head element"}, status_code=400)
        directory = server.get_mutable_data_dir("AutoYou", anchor=server.__file__) / "mobile_games"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{game_id}.html"
            if target.is_symlink() or (target.exists() and not target.is_file()):
                return JSONResponse({"success": False, "error": "Game name is unavailable"}, status_code=409)
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(html)
            try:
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            return JSONResponse({"success": False, "error": "Could not save game"}, status_code=500)
        return JSONResponse({"success": True, "id": game_id}, headers={"Cache-Control": "no-store"})

    @app.get("/play")
    async def play_game(request: Request):
        if not _smc._describe_chat_auth_state(request, agent_name).get("authenticated"):
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        return FileResponse(_FRONTEND_DIR / "play.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/game/native-input/status")
    async def native_input_status(request: Request):
        if not _smc._describe_chat_auth_state(request, agent_name).get("authenticated"):
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        server = _smc._runtime_server()
        return JSONResponse({
            "available": bool(server._get_game_mode_available(cfg=(server.STATE.config or {}))),
            "engine_connected": server.WEBRTC.game_input_hub.connected,
        }, headers={"Cache-Control": "no-store"})

    @app.websocket("/api/game/native-input")
    async def native_input(websocket: WebSocket):
        if not _local_game_page(websocket) or not _smc._describe_chat_auth_state(websocket, agent_name).get("authenticated"):
            await websocket.close(code=1008)
            return
        server = _smc._runtime_server()
        if not server._get_game_mode_available(cfg=(server.STATE.config or {})):
            await websocket.close(code=1008)
            return
        url = f"ws://127.0.0.1:{server.ADMIN_WEB_SERVICE_PORT}/api/webrtc/game-input/stream"
        try:
            async with connect(url, additional_headers={
                "Authorization": f"Bearer {server.WEBRTC.game_input_hub.token}"
            }, open_timeout=5) as upstream:
                await websocket.accept()
                disconnect = asyncio.create_task(websocket.receive())
                incoming = None
                try:
                    while True:
                        incoming = asyncio.create_task(upstream.recv())
                        done, _ = await asyncio.wait((incoming, disconnect), return_when=asyncio.FIRST_COMPLETED)
                        if disconnect in done:
                            break
                        await websocket.send_text(incoming.result())
                finally:
                    disconnect.cancel()
                    tasks = [disconnect]
                    if incoming is not None:
                        incoming.cancel()
                        tasks.append(incoming)
                    await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, ConnectionClosed, InvalidStatus, WebSocketDisconnect, RuntimeError):
            pass
        finally:
            try:
                await websocket.close()
            except RuntimeError:
                pass

    @app.get("/api/game/downloads")
    async def game_downloads(request: Request):
        if not _smc._describe_chat_auth_state(request, agent_name).get("authenticated"):
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            downloads = []
            for download_id, filename in (("unreal-plugin", "AutoYouGameInput.zip"), ("python-adapter", "game_input_client.py")):
                data = await _owned_game_download(download_id)
                downloads.append({"id": download_id, "filename": filename, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            return JSONResponse({"downloads": downloads}, headers={"Cache-Control": "no-store"})
        except (OSError, ValueError):
            return JSONResponse({"success": False, "error": "Game download is unavailable"}, status_code=503)

    async def game_download_response(request: Request, download_id: str, filename: str, media_type: str):
        if not _smc._describe_chat_auth_state(request, agent_name).get("authenticated"):
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            data = await _owned_game_download(download_id)
        except (OSError, ValueError):
            return JSONResponse({"success": False, "error": "Game download is unavailable"}, status_code=503)
        digest = hashlib.sha256(data).hexdigest()
        if request.headers.get("x-autoyou-game-sha256", digest) != digest:
            return JSONResponse({"success": False, "error": "Game download changed. Try again."}, status_code=409)
        return Response(data, media_type=media_type, headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-AutoYou-Game-SHA256": digest,
        })

    @app.get("/api/game/unreal-plugin")
    async def unreal_plugin(request: Request):
        return await game_download_response(request, "unreal-plugin", "AutoYouGameInput.zip", "application/zip")

    @app.get("/api/game/python-adapter")
    async def python_adapter(request: Request):
        return await game_download_response(request, "python-adapter", "game_input_client.py", "text/x-python")

    @app.get("/api/game/protocol")
    async def game_protocol(request: Request):
        if not _smc._describe_chat_auth_state(request, agent_name).get("authenticated"):
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        return JSONResponse({
            "version": "autoyou_game_v1",
            "stream": "/api/webrtc/game-input/stream",
            "connection": "/api/webrtc/game-input/connection",
            "events": ["touch", "sensor", "axis", "button", "session_start", "state_reset", "session_end",
                       "remote_desktop_input", "remote_desktop_keyboard"],
            "touch": "Full active pointer set, normalized x and y in [0,1], at most ten points",
            "sensor": "accelerometer or gyroscope, x/y/z in SI units",
            "axis": "Named analog control in [-1,1]",
            "button": "Named digital control with down or up phase",
            "heartbeat": "Native clients renew an active game control lease; this frame is not forwarded to the engine",
            "mouse": "remote_desktop_input carries coordinate_mode for movement and optional x/y on button frames",
            "keyboard": "remote_desktop_keyboard carries action, key, phase, text, or keyboard_state",
            "button_layout": "Configure up to four label:name buttons in Admin > Video & Calls; clients receive game_buttons in WebRTC capabilities",
            "state_reset": "all_sessions=true clears all players after engine backlog loss",
            "session_start": "A new active player lease; the engine should initialize that session's held state",
        }, headers={"Cache-Control": "no-store"})


app = _smc.create_agent_chat_app(
    agent_name="game_agent",
    title="Game Studio",
    description="Design a local game and connect its input engine to AutoYou game sessions.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
