# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-I-75676820766f6c756e746172-897a7ab102c0a694509f20fc

"""Protected Game Studio website and the local engine protocol description."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import asyncio
import json
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

__debug_trace_i__ = "AUTOYOU-PROVENANCE-I-75676820766f6c756e746172-897a7ab102c0a694509f20fc"


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
# from __debug_trace_i__ import autoyou_provenance_i_75676820766f6c756e746172_897a7ab102c0a694509f20fc
from shared.game_catalog import FRAME_CSP_META, MAX_GAME_BYTES

_MAX_MOBILE_GAME_BYTES = MAX_GAME_BYTES


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
    from shared.game_package_catalog import GamePackages
    from shared.game_package_uploads import GamePackageUploads
    uploads = None
    def packages():
        server = _smc._runtime_server()
        return GamePackages(Path(server.__file__).resolve().parent, server.get_mutable_data_dir("AutoYou", anchor=server.__file__))

    @app.get("/api/game/packages")
    async def package_catalog(request: Request):
        if not _smc._describe_chat_auth_state(request, agent_name).get("authenticated"):
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        return JSONResponse(await asyncio.to_thread(packages().listing), headers={"Cache-Control": "no-store"})

    @app.get("/api/game/packages/{game_id}/download")
    async def package_file(request: Request, game_id: str):
        if not _smc._describe_chat_auth_state(request, agent_name).get("authenticated"):
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        try:
            file = await asyncio.to_thread(packages().resolve, game_id)
            return FileResponse(file, media_type="application/vnd.autoyou.game", filename=game_id+".aygame", headers={"Cache-Control": "no-store"})
        except (OSError, ValueError): return JSONResponse({"error": "Game unavailable"}, status_code=404)

    def upload_store():
        nonlocal uploads
        if uploads is None:
            server = _smc._runtime_server()
            uploads = GamePackageUploads(server.get_mutable_data_dir("AutoYou", anchor=server.__file__))
        return uploads

    def publishing_token(request):
        header = request.headers.get("authorization", "")
        return header[7:].strip() if header.startswith("Bearer ") else request.cookies.get(_smc._agent_chat_cookie_name(agent_name), "")

    def publishing_authenticated(request):
        token = publishing_token(request)
        return bool(token and len(token) <= 512 and _smc._agent_chat_session_is_valid(agent_name, token))

    @app.get("/api/game/publish-auth/status")
    async def publish_auth_status(request: Request):
        # Public browsing does not authorize writing a game into the library.
        return JSONResponse({"success": True, "required": True, "authenticated": publishing_authenticated(request)}, headers={"Cache-Control": "no-store"})

    @app.post("/api/game/publish-auth/login")
    async def publish_auth_login(request: Request):
        body = bytearray()
        async for part in request.stream():
            if len(body) + len(part) > 4096:
                return JSONResponse({"success": False, "error": "Authentication request is too large"}, status_code=413)
            body.extend(part)
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict): raise ValueError("Expected an object")
        except (ValueError, UnicodeError):
            return JSONResponse({"success": False, "error": "Invalid JSON body"}, status_code=400)
        code = str(payload.get("totp_code") or payload.get("code") or "").strip()
        if not code:
            return JSONResponse({"success": False, "error": "Authenticator code is required"}, status_code=400)
        server = _smc._runtime_server()
        if server.agent_has_assigned_2fa_profile(agent_name):
            verified = server.verify_agent_assigned_2fa(agent_name, code)
        else:
            if not _smc._totp_capabilities().get("totp_configured"):
                return JSONResponse({"success": False, "error": "Set up the computer's authenticator before publishing games"}, status_code=400)
            verified = server._verify_totp_secret(server._get_pairing_totp_secret(server.STATE.config), code)
        if not verified:
            return JSONResponse({"success": False, "error": "Invalid authenticator code"}, status_code=401)
        settings = _smc._get_agent_security_settings(agent_name)
        token = _smc._create_agent_chat_session(agent_name, settings["session_ttl_days"])
        return JSONResponse({"success": True, "token": token}, headers={"Cache-Control": "no-store"})

    async def bounded_body(request, limit):
        body = bytearray()
        async for part in request.stream():
            if len(body) + len(part) > limit: raise ValueError("Game upload request is too large")
            body.extend(part)
        return bytes(body)

    @app.post("/api/game/packages/uploads")
    async def package_upload_begin(request: Request):
        if not publishing_authenticated(request): return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            result = await asyncio.to_thread(upload_store().begin, publishing_token(request), json.loads(await bounded_body(request, 4096)))
            return JSONResponse({"success": True, **result}, headers={"Cache-Control": "no-store"})
        except (ValueError, UnicodeError) as error: return JSONResponse({"success": False, "error": str(error)}, status_code=400)

    @app.put("/api/game/packages/uploads/{upload_id}")
    async def package_upload_write(request: Request, upload_id: str, offset: int = 0):
        if not publishing_authenticated(request): return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            body = await bounded_body(request, 1024 * 1024)
            result = await asyncio.to_thread(upload_store().write, upload_id, publishing_token(request), offset, body)
            return JSONResponse({"success": True, **result}, headers={"Cache-Control": "no-store"})
        except ValueError as error: return JSONResponse({"success": False, "error": str(error)}, status_code=400)

    @app.post("/api/game/packages/uploads/{upload_id}/complete")
    async def package_upload_complete(request: Request, upload_id: str):
        if not publishing_authenticated(request): return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            result = await asyncio.to_thread(upload_store().finish, upload_id, publishing_token(request))
            return JSONResponse({"success": True, **result}, headers={"Cache-Control": "no-store"})
        except ValueError as error: return JSONResponse({"success": False, "error": str(error)}, status_code=400)

    @app.post("/api/game/publish/{game_id}")
    async def publish_game(request: Request, game_id: str):
        if not publishing_authenticated(request):
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        if re.fullmatch(r"[a-z0-9-]{1,64}", game_id) is None:
            return JSONResponse({"success": False, "error": "Use a short lowercase game name"}, status_code=400)
        html = bytearray()
        async for part in request.stream():
            if len(html) + len(part) > _MAX_MOBILE_GAME_BYTES:
                return JSONResponse({"success": False, "error": "Move large game assets into a complete game package (up to 5 GiB)"}, status_code=413)
            html.extend(part)
        try:
            html.decode("utf-8")
        except UnicodeError:
            return JSONResponse({"success": False, "error": "Game must be UTF-8 HTML"}, status_code=400)
        head = re.search(br"<head(?=[\s>])[^>]*>", html[:4096], re.IGNORECASE)
        if head is None:
            return JSONResponse({"success": False, "error": "Game HTML needs a head element"}, status_code=400)
        # Mobile downloads contain this policy already; do not grow the game on each round trip.
        while html[head.end():].startswith(FRAME_CSP_META):
            del html[head.end():head.end() + len(FRAME_CSP_META)]
        if len(html) > MAX_GAME_BYTES - len(FRAME_CSP_META):
            return JSONResponse({"success": False, "error": "Game exceeds the mobile library limit"}, status_code=413)
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
            "observe": "/api/webrtc/game-input/observe",
            "observe_relay": "./api/game/observe",
            "observed": ("Read-only, any number of readers: game_input_observed messages carry player, session_id, "
                         "input (a readable record: name, kind, device, input_type, control, value, phase, timestamp) "
                         "and frame (the protocol frame without its lease id or typed text). The engine keeps "
                         "control; observing never takes input away from it."),
        }, headers={"Cache-Control": "no-store"})

    @app.websocket("/api/game/observe")
    async def observe_inputs(websocket: WebSocket):
        """Relay the read-only input feed to a signed-in website page, for web app agents."""
        auth = _smc._describe_chat_auth_state(websocket, agent_name)
        if not auth.get("authenticated") or auth.get("auth_mode") == "open":
            await websocket.close(code=1008)
            return
        server = _smc._runtime_server()
        url = f"ws://127.0.0.1:{server.ADMIN_WEB_SERVICE_PORT}/api/webrtc/game-input/observe"
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


app = _smc.create_agent_chat_app(
    agent_name="game_agent",
    title="Game Studio",
    description="Design a local game and connect its input engine to AutoYou game sessions.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
