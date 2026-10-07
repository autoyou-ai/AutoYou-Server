# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-because-c9d5a81993c0ca1570232b50

"""Webrtc HTTP routes for the full AutoYou server."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from pathlib import Path
from typing import Any, Callable, Dict
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from shared.remote_access_policy import REMOTE_BROWSER_IDENTITY_HEADERS

__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-because-c9d5a81993c0ca1570232b50"


def register_routes(
    admin_app: FastAPI,
    auth_app: FastAPI,
    server: Any,
) -> Dict[str, Callable[..., Any]]:
    hosted_game_path = Path(__file__).resolve().parents[1] / "autoyou_agents/game_agent/website/frontend/play.html"

    def local_game_page_allowed(connection: Request | WebSocket) -> bool:
        peer = connection.client.host if connection.client else ""
        host = connection.headers.get("host", "")
        try:
            parsed_host = urlsplit(f"http://{host}").hostname
        except ValueError:
            return False
        return bool(
            server._is_loopback_client_host(peer)
            and server._is_loopback_client_host(parsed_host)
            and not any(connection.headers.get(name) for name in (
                "forwarded", "x-forwarded-for", "x-real-ip", *REMOTE_BROWSER_IDENTITY_HEADERS,
            ))
        )

    @admin_app.get("/api/webrtc/hosted-game/play")
    async def hosted_game_page(request: Request):
        if not local_game_page_allowed(request):
            return JSONResponse(status_code=403, content={"success": False, "error": "Local computer only"})
        if not server._get_game_mode_available(cfg=(server.STATE.config or {})):
            return JSONResponse(status_code=409, content={"success": False, "error": "Game mode is disabled"})
        return FileResponse(hosted_game_path, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})

    @admin_app.get("/api/webrtc/hosted-game/api/game/native-input/status")
    async def hosted_game_input_status(request: Request):
        if not local_game_page_allowed(request):
            return JSONResponse(status_code=403, content={"success": False, "error": "Local computer only"})
        return JSONResponse({
            "available": bool(server._get_game_mode_available(cfg=(server.STATE.config or {}))),
            "engine_connected": server.WEBRTC.game_input_hub.connected,
        }, headers={"Cache-Control": "no-store"})

    async def serve_game_input_stream(websocket: WebSocket, *, owner: str = "engine"):
        hub = server.WEBRTC.game_input_hub
        queue = hub.attach(owner=owner)
        if queue is None:
            await websocket.close(code=1013)
            return
        try:
            await websocket.accept()
            await server.WEBRTC._sync_game_input_engine_state()
            disconnect = server.asyncio.create_task(websocket.receive())
            try:
                while True:
                    queued = server.asyncio.create_task(queue.get())
                    done, _ = await server.asyncio.wait(
                        (queued, disconnect), timeout=3, return_when=server.asyncio.FIRST_COMPLETED,
                    )
                    if disconnect in done:
                        break
                    if queued in done:
                        frame = queued.result()
                    else:
                        queued.cancel()
                        await server.asyncio.gather(queued, return_exceptions=True)
                        frame = {"event": "heartbeat"}
                    await websocket.send_json(frame)
            finally:
                disconnect.cancel()
                queued.cancel()
                await server.asyncio.gather(disconnect, queued, return_exceptions=True)
        except (WebSocketDisconnect, OSError, RuntimeError):
            pass
        finally:
            hub.detach(queue)
            await server.WEBRTC._sync_game_input_engine_state()

    @admin_app.websocket("/api/webrtc/hosted-game/api/game/native-input")
    async def hosted_game_input_stream(websocket: WebSocket):
        origin = urlsplit(websocket.headers.get("origin", ""))
        if (
            not local_game_page_allowed(websocket)
            or origin.scheme not in {"http", "https"}
            or origin.netloc.lower() != websocket.headers.get("host", "").lower()
            or not server._get_game_mode_available(cfg=(server.STATE.config or {}))
        ):
            await websocket.close(code=1008)
            return
        await serve_game_input_stream(websocket, owner="hosted-neon")

    @admin_app.get("/api/webrtc/game-input/connection")
    async def admin_get_game_input_connection(request: Request):
        auth_error = server._require_webrtc_playback_auth_json(request)
        if auth_error:
            return auth_error
        if not server._get_game_mode_available(cfg=(server.STATE.config or {})):
            return JSONResponse(status_code=409, content={"success": False, "error": "Game mode is disabled"})
        return JSONResponse(
            content={
                "success": True,
                "path": "/api/webrtc/game-input/stream",
                "token": server.WEBRTC.game_input_hub.token,
                "engine_connected": server.WEBRTC.game_input_hub.connected,
            },
            headers={"Cache-Control": "no-store"},
        )

    @admin_app.websocket("/api/webrtc/game-input/stream")
    async def local_game_input_stream(websocket: WebSocket):
        peer = websocket.client.host if websocket.client else ""
        authorization = websocket.headers.get("authorization", "")
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        hub = server.WEBRTC.game_input_hub
        if (
            not server._is_loopback_client_host(peer)
            or any(websocket.headers.get(name) for name in ("forwarded", "x-forwarded-for", "x-real-ip"))
            or not server._get_game_mode_available(cfg=(server.STATE.config or {}))
            or not token
            or not server.secrets.compare_digest(token, hub.token)
        ):
            await websocket.close(code=1008)
            return
        await serve_game_input_stream(websocket)

    @admin_app.get("/api/webrtc/microphone-sharing")
    async def admin_get_microphone_sharing(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        manager = server.WEBRTC
        if manager is None:
            return JSONResponse(status_code=503, content={"success": False, "error": "Call service unavailable"})
        return {
            "success": True,
            "enabled": manager.server_microphone_sharing_enabled,
            "owner": manager.host_audio_owner(),
            "connected_devices": manager.connected_device_count(),
            "same_machine_devices": manager.connected_device_count(same_machine_only=True),
            "configured_sources": server._get_video_audio_sources(cfg=(server.STATE.config or {})),
        }

    @admin_app.put("/api/webrtc/microphone-sharing")
    async def admin_set_microphone_sharing(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})
        if not isinstance(payload, dict) or type(payload.get("enabled")) is not bool:
            return JSONResponse(status_code=400, content={"success": False, "error": "Choose whether to share the computer microphone"})
        manager = server.WEBRTC
        if manager is None:
            return JSONResponse(status_code=503, content={"success": False, "error": "Call service unavailable"})
        try:
            await server._apply_server_microphone_sharing(payload["enabled"])
        except server.ConfigWriteBlocked as exc:
            return server._json_config_write_blocked_response(str(exc))
        return {"success": True, "enabled": manager.server_microphone_sharing_enabled,
                "owner": manager.host_audio_owner(), "connected_devices": manager.connected_device_count(),
                "same_machine_devices": manager.connected_device_count(same_machine_only=True),
                "configured_sources": server._get_video_audio_sources(cfg=(server.STATE.config or {}))}

    @admin_app.get("/api/webrtc/playback/enabled")
    async def admin_get_audio_playback_enabled(request: Request):
        auth_error = server._require_webrtc_playback_auth_json(request)
        if auth_error:
            return auth_error
        try:
            enabled = server._get_audio_playback_enabled(cfg=(server.STATE.config or {}))
            music_library_dirs = server._resolve_audio_playback_music_library_dirs(cfg=(server.STATE.config or {}))
            return {
                "success": True,
                "enabled": enabled,
                "installed": server._is_audio_agent_installed(),
                "music_library_dirs": music_library_dirs,
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.get("/api/webrtc/capabilities")
    async def admin_get_webrtc_capabilities(request: Request):
        auth_error = server._require_webrtc_playback_auth_json(request)
        if auth_error:
            return auth_error
        try:
            return {
                "success": True,
                "capabilities": server._build_webrtc_capabilities(cfg=(server.STATE.config or {}), include_admin=True),
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.get("/api/webrtc/audio-devices")
    async def admin_get_webrtc_audio_devices(request: Request, refresh: bool = False):
        auth_error = server._require_webrtc_playback_auth_json(request)
        if auth_error:
            return auth_error
        try:
            return await server.asyncio.to_thread(server._enumerate_webrtc_audio_devices_payload, refresh=refresh)
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.get("/api/webrtc/camera-devices")
    async def admin_get_webrtc_camera_devices(request: Request, refresh: bool = False):
        auth_error = server._require_webrtc_playback_auth_json(request)
        if auth_error:
            return auth_error
        try:
            return await server.asyncio.to_thread(
                server._enumerate_webrtc_camera_devices_payload,
                cfg=(server.STATE.config or {}),
                refresh=refresh,
            )
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.get("/api/webrtc/monitors")
    async def admin_get_webrtc_monitors(request: Request, refresh: bool = False):
        auth_error = server._require_webrtc_playback_auth_json(request)
        if auth_error:
            return auth_error
        try:
            return await server.asyncio.to_thread(
                server._enumerate_webrtc_monitors_payload,
                cfg=(server.STATE.config or {}),
                refresh=refresh,
            )
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.get("/api/webrtc/video-files")
    async def admin_get_webrtc_video_files(request: Request):
        auth_error = server._require_webrtc_playback_auth_json(request)
        if auth_error:
            return auth_error
        try:
            target_dir = server._resolve_video_file_upload_dir()
            files = []
            if target_dir.exists():
                for f in sorted(target_dir.iterdir(), key=lambda p: p.name):
                    if f.is_file() and f.suffix.lower() in {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi"}:
                        files.append({
                            "name": f.name,
                            "path": str(f),
                            "size_bytes": f.stat().st_size,
                            "created_at": f.stat().st_mtime,
                        })
            return {
                "success": True,
                "files": files,
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.post("/api/webrtc/video-input/{source_id}/frame")
    async def admin_publish_realtime_video_input_frame(source_id: str, request: Request):
        auth_error = server._require_webrtc_playback_auth_json(request)
        if auth_error:
            return auth_error
        if server.publish_realtime_video_jpeg is None:
            return JSONResponse(status_code=503, content={"success": False, "error": "Realtime video input is unavailable"})

        try:
            content_type = str(request.headers.get("content-type") or "").lower()
            if "image/jpeg" in content_type or "application/octet-stream" in content_type:
                jpeg_bytes = await request.body()
            else:
                try:
                    payload = await request.json()
                except Exception as exc:
                    raise ValueError("Expected image/jpeg body or JSON with jpeg_base64/image_base64") from exc
                if not isinstance(payload, dict):
                    raise ValueError("Frame payload must be a JSON object")
                encoded = (
                    payload.get("jpeg_base64")
                    or payload.get("image_base64")
                    or payload.get("data")
                    or payload.get("data_url")
                    or ""
                )
                encoded_text = str(encoded or "").strip()
                if "," in encoded_text and encoded_text.lower().startswith("data:"):
                    encoded_text = encoded_text.split(",", 1)[1]
                if not encoded_text:
                    raise ValueError("Missing jpeg_base64/image_base64 frame data")
                jpeg_bytes = server.base64.b64decode(encoded_text, validate=True)

            if not jpeg_bytes:
                raise ValueError("Empty video frame")
            frame = server.publish_realtime_video_jpeg(
                source_id=source_id,
                jpeg_bytes=jpeg_bytes,
                source="admin_api",
            )
            return {
                "success": True,
                "source_id": frame.source_id,
                "sequence": frame.sequence,
                "timestamp_ms": frame.timestamp_ms,
                "width": frame.width,
                "height": frame.height,
            }
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.warning("Failed to publish realtime video input frame for %s: %s", source_id, exc)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/webrtc/video-file/upload")
    async def admin_upload_video_file(request: Request):
        auth_error = server._require_webrtc_playback_auth_json(request)
        if auth_error:
            return auth_error
        try:
            form = await request.form()
            uploaded = form.get("video")
        except Exception as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": f"Invalid upload form: {exc}"})
        if uploaded is None or not hasattr(uploaded, "read"):
            return JSONResponse(status_code=400, content={"success": False, "error": "Missing video file field"})

        try:
            original_name = str(getattr(uploaded, "filename", "") or "video-file").strip() or "video-file"
            suffix = server.Path(original_name).suffix.lower()
            if suffix not in {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi"}:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Unsupported video file type"},
                )
            safe_stem = server.re.sub(r"[^A-Za-z0-9_.-]+", "_", server.Path(original_name).stem).strip("._")[:80] or "video"
            target_dir = server._resolve_video_file_upload_dir()
            target_path = target_dir / f"{int(server.time.time() * 1000)}-{safe_stem}{suffix}"
            size_bytes = 0
            if server.secure_storage_enabled():
                uploaded_bytes = bytearray()
                while True:
                    chunk = await uploaded.read(1024 * 1024)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    uploaded_bytes.extend(chunk)
                if size_bytes:
                    server.write_secure_file(target_path, bytes(uploaded_bytes))
            else:
                with target_path.open("wb") as output_file:
                    while True:
                        chunk = await uploaded.read(1024 * 1024)
                        if not chunk:
                            break
                        size_bytes += len(chunk)
                        output_file.write(chunk)
            if size_bytes <= 0:
                with server.suppress(Exception):
                    target_path.unlink()
                return JSONResponse(status_code=400, content={"success": False, "error": "Empty video file"})
            return {
                "success": True,
                "file_path": str(target_path),
                "file_name": target_path.name,
                "size_bytes": size_bytes,
            }
        except Exception as exc:
            server.LOGGER.warning("Failed to upload video file: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})
        finally:
            close_method = getattr(uploaded, "close", None)
            if callable(close_method):
                with server.suppress(Exception):
                    maybe_close = close_method()
                    if hasattr(maybe_close, "__await__"):
                        await maybe_close

    @admin_app.post("/api/webrtc/video-file/play")
    async def admin_play_video_file(request: Request):
        auth_error = server._require_webrtc_playback_auth_json(request)
        if auth_error:
            return auth_error
        if server.play_video_file_playback is None:
            return JSONResponse(status_code=503, content={"success": False, "error": "Video file playback is unavailable"})
        try:
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            cfg = server.STATE.config or {}
            status = server._configure_video_file_playback_from_config(cfg=cfg, restart=False)
            if not server._get_video_file_available(cfg=cfg):
                return JSONResponse(status_code=409, content={"success": False, "error": "No playable video file is configured", "status": status})
            status = server.play_video_file_playback(
                source_id=server._get_video_file_source_id(cfg=cfg),
                restart=server._coerce_enabled_flag((payload or {}).get("restart")) if isinstance(payload, dict) and "restart" in payload else False,
            )
            return {"success": True, "status": status}
        except Exception as exc:
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/webrtc/video-file/pause")
    async def admin_pause_video_file(request: Request):
        auth_error = server._require_webrtc_playback_auth_json(request)
        if auth_error:
            return auth_error
        if server.pause_video_file_playback is None:
            return JSONResponse(status_code=503, content={"success": False, "error": "Video file playback is unavailable"})
        status = server.pause_video_file_playback(source_id=server._get_video_file_source_id(cfg=(server.STATE.config or {})))
        return {"success": True, "status": status}

    @admin_app.post("/api/webrtc/video-file/status")
    async def admin_video_file_status(request: Request):
        auth_error = server._require_webrtc_playback_auth_json(request)
        # from __debug_provenance_l__ import because
        if auth_error:
            return auth_error
        cfg = server.STATE.config or {}
        status = server._configure_video_file_playback_from_config(cfg=cfg, restart=False)
        return {"success": True, "status": status}

    @admin_app.post("/api/webrtc/playback/enabled")
    async def admin_set_audio_playback_enabled(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            payload = {}
            try:
                payload = await request.json()
            except Exception:
                form = await request.form()
                payload = {k: v for k, v in form.items()}

            enabled = server._coerce_enabled_flag(payload.get("enabled"))
            previously_enabled = server._get_audio_playback_enabled(cfg=(server.STATE.config or {}))
            cfg = server._apply_audio_playback_enabled(bool(enabled))
            if server.WEBRTC is not None and previously_enabled != bool(enabled):
                await server.WEBRTC.apply_video_call_settings(force_audio_rewire=True)
            music_library_dirs = server._resolve_audio_playback_music_library_dirs(cfg=cfg)
            return {
                "success": True,
                "enabled": bool(enabled),
                "installed": server._is_audio_agent_installed(),
                "music_library_dirs": music_library_dirs,
            }
        except Exception as e:
            if isinstance(e, server.ConfigWriteBlocked):
                return server._json_config_write_blocked_response(str(e))
            server.LOGGER.error(f"Error setting audio playback enabled: {e}")
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    routes = {
        "admin_get_microphone_sharing": admin_get_microphone_sharing,
        "admin_set_microphone_sharing": admin_set_microphone_sharing,
        "admin_get_audio_playback_enabled": admin_get_audio_playback_enabled,
        "admin_get_webrtc_capabilities": admin_get_webrtc_capabilities,
        "admin_get_webrtc_audio_devices": admin_get_webrtc_audio_devices,
        "admin_get_webrtc_camera_devices": admin_get_webrtc_camera_devices,
        "admin_get_webrtc_monitors": admin_get_webrtc_monitors,
        "admin_get_webrtc_video_files": admin_get_webrtc_video_files,
        "admin_publish_realtime_video_input_frame": admin_publish_realtime_video_input_frame,
        "admin_upload_video_file": admin_upload_video_file,
        "admin_play_video_file": admin_play_video_file,
        "admin_pause_video_file": admin_pause_video_file,
        "admin_video_file_status": admin_video_file_status,
        "admin_set_audio_playback_enabled": admin_set_audio_playback_enabled
    }

    @admin_app.post("/api/webrtc/send")
    async def admin_send_webrtc_message(request: Request):
      """Send a text message to a connected WebRTC datachannel client."""
      auth_response = server._require_webrtc_playback_auth_json(request)
      if auth_response is not None:
        return auth_response

      try:
        data = await request.json()
        message = str(data.get("message") or "").strip()
        session_id = str(data.get("session_id") or "").strip()
        owner_key = str(data.get("owner_key") or "").strip()
        metadata = data.get("metadata")
        context = data.get("context")
        if metadata is not None and not isinstance(metadata, dict):
          return server.JSONResponse(
            status_code=400,
            content={"success": False, "error": "Field 'metadata' must be a JSON object when provided"}
          )
        if context is not None and not isinstance(context, list):
          return server.JSONResponse(
            status_code=400,
            content={"success": False, "error": "Field 'context' must be a JSON array when provided"}
          )
        if not message and not context:
          return server.JSONResponse(
            status_code=400,
            content={"success": False, "error": "Missing 'message' or 'context' field"}
          )
        if not session_id and not owner_key:
          return server.JSONResponse(
            status_code=400,
            content={"success": False, "error": "Missing 'session_id' or 'owner_key' field"}
          )

        reply_target: server.Dict[str, server.Any] = {"transport": "webrtc"}
        if session_id:
          reply_target["session_id"] = session_id
        if owner_key:
          reply_target["owner_key"] = owner_key

        effective_metadata = dict(metadata or {})
        effective_metadata.setdefault(
          "source",
          "internal_ai_agent" if server._request_uses_ai_agent_internal_token(request) else "admin_api",
        )
        success = await server.WEBRTC.send_chat_to_reply_target(
          reply_target,
          message,
          metadata=effective_metadata,
          context=context if isinstance(context, list) else None,
          user_id=server.get_configured_server_name(),
        )
        if success:
          return {"success": True, "message": "Message sent successfully"}
        return server.JSONResponse(
          status_code=404,
          content={"success": False, "error": "Connected browser client not available"}
        )
      except Exception as e:
        server.LOGGER.error(f"Error sending browser message (admin): {e}")
        return server.JSONResponse(
          status_code=500,
          content={"success": False, "error": str(e)}
        )

    @admin_app.post("/api/webrtc/rewarded-ad")
    async def admin_trigger_webrtc_rewarded_ad(request: Request):
      """Send a native rewarded-ad control message to a connected WebRTC client."""
      auth_response = server._require_webrtc_playback_auth_json(request)
      if auth_response is not None:
        return auth_response

      try:
        data = await request.json()
        if not isinstance(data, dict):
          return server.JSONResponse(
            status_code=400,
            content={"success": False, "error": "Request body must be a JSON object"}
          )

        session_id = str(data.get("session_id") or "").strip()
        owner_key = str(data.get("owner_key") or "").strip()
        allow_single_live_fallback = bool(server._coerce_enabled_flag(data.get("allow_single_live_fallback", False)))
        if not session_id and not owner_key and not allow_single_live_fallback:
          return server.JSONResponse(
            status_code=400,
            content={"success": False, "error": "Missing 'session_id' or 'owner_key' field"}
          )

        raw_control_payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        control_payload = server._lock_native_mobile_rewarded_ad_control_payload(raw_control_payload)
        if not server._rewarded_ad_trigger_enabled():
            return server.JSONResponse(
              status_code=403,
              content={
                "success": False,
              "reason": "Rewarded-ad triggering is disabled on this AutoYou server.",
              "triggered_count": 0,
            },
          )

        reply_target: server.Dict[str, server.Any] = {"transport": "webrtc"}
        if session_id:
          reply_target["session_id"] = session_id
        if owner_key:
          reply_target["owner_key"] = owner_key

        success, status = await server.WEBRTC.send_rewarded_ad_control_to_reply_target(
          reply_target,
          control_payload,
          allow_single_live_fallback=allow_single_live_fallback,
        )
        if success:
          return status

        resolution = str(status.get("resolution") or "").strip()
        status_code = 409 if resolution == "ambiguous" else 404
        if "control messages are unavailable" in str(status.get("reason") or ""):
          status_code = 500
        return server.JSONResponse(status_code=status_code, content=status)
      except Exception as e:
        server.LOGGER.error(f"Error triggering WebRTC rewarded ad (admin): {e}")
        return server.JSONResponse(
          status_code=500,
          content={"success": False, "error": str(e)}
        )

    @admin_app.post("/api/webrtc/client-browser-control")
    async def admin_trigger_webrtc_client_browser_control(request: Request):
      """Send a browser-control message to a connected WebRTC client."""
      auth_response = server._require_webrtc_playback_auth_json(request)
      if auth_response is not None:
        return auth_response

      try:
        data = await request.json()
        if not isinstance(data, dict):
          return server.JSONResponse(
            status_code=400,
            content={"success": False, "error": "Request body must be a JSON object"}
          )

        session_id = str(data.get("session_id") or "").strip()
        owner_key = str(data.get("owner_key") or "").strip()
        allow_single_live_fallback = bool(server._coerce_enabled_flag(data.get("allow_single_live_fallback", False)))
        if not session_id and not owner_key and not allow_single_live_fallback:
          return server.JSONResponse(
            status_code=400,
            content={"success": False, "error": "Missing 'session_id' or 'owner_key' field"}
          )

        raw_control_payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        control_payload = server._lock_client_browser_control_payload(raw_control_payload)
        if not control_payload.get("action"):
          return server.JSONResponse(
            status_code=400,
            content={
              "success": False,
              "reason": "Unsupported client browser control action or URL.",
              "triggered_count": 0,
            },
          )

        reply_target: server.Dict[str, server.Any] = {"transport": "webrtc"}
        if session_id:
          reply_target["session_id"] = session_id
        if owner_key:
          reply_target["owner_key"] = owner_key

        success, status = await server.WEBRTC.send_client_browser_control_to_reply_target(
          reply_target,
          control_payload,
          allow_single_live_fallback=allow_single_live_fallback,
        )
        if success:
          return status

        resolution = str(status.get("resolution") or "").strip()
        status_code = 409 if resolution == "ambiguous" else 404
        if "control messages are unavailable" in str(status.get("reason") or ""):
          status_code = 500
        return server.JSONResponse(status_code=status_code, content=status)
      except Exception as e:
        server.LOGGER.error(f"Error triggering WebRTC client browser control (admin): {e}")
        return server.JSONResponse(
          status_code=500,
          content={"success": False, "error": str(e)}
        )

    @admin_app.post("/api/webrtc/playback/play")
    async def admin_play_webrtc_audio(request: Request):
            auth_response = server._require_webrtc_playback_auth_json(request)
            if auth_response is not None:
                return auth_response

            try:
                data = await request.json()
                file_path = str(data.get("file_path") or "").strip()
                session_id = str(data.get("session_id") or "").strip()
                owner_key = str(data.get("owner_key") or "").strip()
                if not file_path:
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing 'file_path' field"}
                    )
                if not session_id and not owner_key:
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing 'session_id' or 'owner_key' field"}
                    )

                reply_target: server.Dict[str, server.Any] = {"transport": "webrtc"}
                if session_id:
                    reply_target["session_id"] = session_id
                if owner_key:
                    reply_target["owner_key"] = owner_key

                if "native_audio_scope" in data:
                    reply_target["native_audio_scope"] = data["native_audio_scope"]
                success, status = await server.WEBRTC.play_audio_file_to_reply_target(reply_target, file_path)
                if success:
                    return {"success": True, "status": status}
                error_state = str((status or {}).get("state") or "").strip().lower()
                error_detail = str((status or {}).get("detail") or "Playback failed").strip()
                return server.JSONResponse(
                    status_code=409 if error_state == "disabled" else (404 if error_state == "unavailable" else 400),
                    content={"success": False, "error": error_detail, "status": status}
                )
            except Exception as e:
                server.LOGGER.error(f"Error playing WebRTC audio (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

    @admin_app.post("/api/webrtc/playback/pause")
    async def admin_pause_webrtc_audio(request: Request):
            auth_response = server._require_webrtc_playback_auth_json(request)
            if auth_response is not None:
                return auth_response

            try:
                data = await request.json()
                session_id = str(data.get("session_id") or "").strip()
                owner_key = str(data.get("owner_key") or "").strip()
                if not session_id and not owner_key:
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing 'session_id' or 'owner_key' field"}
                    )

                reply_target: server.Dict[str, server.Any] = {"transport": "webrtc"}
                if session_id:
                    reply_target["session_id"] = session_id
                if owner_key:
                    reply_target["owner_key"] = owner_key

                if "native_audio_scope" in data:
                    reply_target["native_audio_scope"] = data["native_audio_scope"]
                success, status = await server.WEBRTC.pause_audio_playback_for_reply_target(reply_target)
                if success:
                    return {"success": True, "status": status}
                error_state = str((status or {}).get("state") or "").strip().lower()
                return server.JSONResponse(
                    status_code=409 if error_state == "disabled" else 404,
                    content={"success": False, "error": str((status or {}).get("detail") or "No active playback session")}
                )
            except Exception as e:
                server.LOGGER.error(f"Error pausing WebRTC audio (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

    @admin_app.post("/api/webrtc/playback/resume")
    async def admin_resume_webrtc_audio(request: Request):
            auth_response = server._require_webrtc_playback_auth_json(request)
            if auth_response is not None:
                return auth_response

            try:
                data = await request.json()
                session_id = str(data.get("session_id") or "").strip()
                owner_key = str(data.get("owner_key") or "").strip()
                if not session_id and not owner_key:
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing 'session_id' or 'owner_key' field"}
                    )

                reply_target: server.Dict[str, server.Any] = {"transport": "webrtc"}
                if session_id:
                    reply_target["session_id"] = session_id
                if owner_key:
                    reply_target["owner_key"] = owner_key

                if "native_audio_scope" in data:
                    reply_target["native_audio_scope"] = data["native_audio_scope"]
                success, status = await server.WEBRTC.resume_audio_playback_for_reply_target(reply_target)
                if success:
                    return {"success": True, "status": status}
                error_state = str((status or {}).get("state") or "").strip().lower()
                return server.JSONResponse(
                    status_code=409 if error_state == "disabled" else 404,
                    content={"success": False, "error": str((status or {}).get("detail") or "No active playback session")}
                )
            except Exception as e:
                server.LOGGER.error(f"Error resuming WebRTC audio (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

    @admin_app.post("/api/webrtc/playback/stop")
    async def admin_stop_webrtc_audio(request: Request):
            auth_response = server._require_webrtc_playback_auth_json(request)
            if auth_response is not None:
                return auth_response

            try:
                data = await request.json()
                session_id = str(data.get("session_id") or "").strip()
                owner_key = str(data.get("owner_key") or "").strip()
                if not session_id and not owner_key:
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing 'session_id' or 'owner_key' field"}
                    )

                reply_target: server.Dict[str, server.Any] = {"transport": "webrtc"}
                if session_id:
                    reply_target["session_id"] = session_id
                if owner_key:
                    reply_target["owner_key"] = owner_key

                if "native_audio_scope" in data:
                    reply_target["native_audio_scope"] = data["native_audio_scope"]
                success, status = await server.WEBRTC.stop_audio_playback_for_reply_target(reply_target)
                if success:
                    return {"success": True, "status": status}
                error_state = str((status or {}).get("state") or "").strip().lower()
                return server.JSONResponse(
                    status_code=409 if error_state == "disabled" else 404,
                    content={"success": False, "error": str((status or {}).get("detail") or "No active playback session")}
                )
            except Exception as e:
                server.LOGGER.error(f"Error stopping WebRTC audio (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

    @admin_app.post("/api/webrtc/playback/status")
    async def admin_get_webrtc_audio_status(request: Request):
            auth_response = server._require_webrtc_playback_auth_json(request)
            if auth_response is not None:
                return auth_response

            try:
                data = await request.json()
                session_id = str(data.get("session_id") or "").strip()
                owner_key = str(data.get("owner_key") or "").strip()
                if not session_id and not owner_key:
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing 'session_id' or 'owner_key' field"}
                    )

                reply_target: server.Dict[str, server.Any] = {"transport": "webrtc"}
                if session_id:
                    reply_target["session_id"] = session_id
                if owner_key:
                    reply_target["owner_key"] = owner_key

                if "native_audio_scope" in data:
                    reply_target["native_audio_scope"] = data["native_audio_scope"]
                status = await server.WEBRTC.get_audio_playback_status_for_reply_target(reply_target)
                error_state = str((status or {}).get("state") or "").strip().lower()
                return {"success": error_state not in {"unavailable", "disabled"}, "status": status}
            except Exception as e:
                server.LOGGER.error(f"Error getting WebRTC audio status (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

    routes.update({
        "admin_send_webrtc_message": admin_send_webrtc_message,
        "admin_trigger_webrtc_rewarded_ad": admin_trigger_webrtc_rewarded_ad,
        "admin_trigger_webrtc_client_browser_control": admin_trigger_webrtc_client_browser_control,
        "admin_play_webrtc_audio": admin_play_webrtc_audio,
        "admin_pause_webrtc_audio": admin_pause_webrtc_audio,
        "admin_resume_webrtc_audio": admin_resume_webrtc_audio,
        "admin_stop_webrtc_audio": admin_stop_webrtc_audio,
        "admin_get_webrtc_audio_status": admin_get_webrtc_audio_status,
    })

    return routes
