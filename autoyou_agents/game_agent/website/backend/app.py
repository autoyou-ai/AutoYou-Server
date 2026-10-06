"""Protected Game Studio website and the local engine protocol description."""

import asyncio
from io import BytesIO
from ipaddress import ip_address
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

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
        # Open mode has no password, so "authenticated" proves nothing there, and an agent website never
        # takes the admin UI session as its own auth: publishing needs a password-protected server.
        if not auth.get("authenticated") or auth.get("auth_mode") == "open":
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        if re.fullmatch(r"[a-z0-9-]{1,64}", game_id) is None:
            return JSONResponse({"success": False, "error": "Use a short lowercase game name"}, status_code=400)
        html = bytearray()
        async for part in request.stream():
            html.extend(part)
            if len(html) > _MAX_MOBILE_GAME_BYTES:
                return JSONResponse({"success": False, "error": "Game exceeds 511 KB"}, status_code=413)
        try:
            html.decode("utf-8")
        except UnicodeError:
            return JSONResponse({"success": False, "error": "Game must be UTF-8 HTML"}, status_code=400)
        if re.search(br"<head(?=[\s>])[^>]*>", html[:4096], re.IGNORECASE) is None:
            return JSONResponse({"success": False, "error": "Game HTML needs a head element"}, status_code=400)
        server = _smc._runtime_server()
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

    @app.get("/api/game/unreal-plugin")
    async def unreal_plugin(request: Request):
        if not _smc._describe_chat_auth_state(request, agent_name).get("authenticated"):
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        archive = BytesIO()
        with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
            for relative in _UNREAL_PLUGIN_FILES:
                source = _UNREAL_PLUGIN_DIR / relative
                if source.is_symlink():
                    return JSONResponse({"success": False, "error": "Plugin source is unavailable"}, status_code=500)
                bundle.write(source, Path("AutoYouGameInput") / relative)
        return Response(archive.getvalue(), media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="AutoYouGameInput.zip"',
            "Cache-Control": "no-store",
        })

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
