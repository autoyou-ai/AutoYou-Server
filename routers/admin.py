# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-0c681cfb79557970d9459e4e

"""Admin HTTP routes for the full AutoYou server."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import time
import uuid
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, Form, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-0c681cfb79557970d9459e4e"


def register_routes(
    admin_app: FastAPI,
    auth_app: FastAPI,
    server: Any,
) -> Dict[str, Callable[..., Any]]:
    def _chat_text(value: Any, fallback: str = "", limit: int = 256) -> str:
        text = " ".join(str(value or "").strip().split())
        return (text or fallback)[:limit]

    def _chat_model_dict(value: Any) -> Dict[str, Any]:
        if hasattr(value, "model_dump"):
            result = value.model_dump()
        elif hasattr(value, "dict"):
            result = value.dict()
        elif isinstance(value, dict):
            result = dict(value)
        else:
            result = {}
        return jsonable_encoder(result) if isinstance(result, dict) else {}

    def _public_attachment(value: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(value, dict):
            return None
        meta = value.get("meta") if isinstance(value.get("meta"), dict) else {}
        filename = _chat_text(value.get("filename") or value.get("path"), "attachment", 180)
        mimetype = _chat_text(value.get("mimetype"), "application/octet-stream", 120)
        try:
            size_bytes = max(0, int(value.get("size_bytes") or 0))
        except (TypeError, ValueError):
            size_bytes = 0
        return {
            "filename": filename,
            "mimetype": mimetype,
            "size_bytes": size_bytes,
            "kind": _chat_text(meta.get("kind"), "file", 32),
        }

    def _chat_response_payload(response: Any) -> Dict[str, Any]:
        payload = _chat_model_dict(response)
        payload.pop("voice_reply_audio_path", None)
        voice_path = str(getattr(response, "voice_reply_audio_path", "") or "").strip()
        if voice_path:
            try:
                from shared.voice_messaging import attachment_from_audio_file, cleanup_paths

                voice_attachment = attachment_from_audio_file(voice_path, platform="admin-web")
                if voice_attachment:
                    payload["voice_reply_audio"] = voice_attachment
                cleanup_paths(voice_path)
            except Exception as exc:
                server.LOGGER.warning("Could not inline admin voice reply: %s", exc)
        media_attachments = getattr(response, "media_reply_attachments", []) or []
        if media_attachments:
            try:
                from shared.media_messaging import inline_attachment_for_client

                payload["media_reply_attachments"] = [
                    item
                    for item in (inline_attachment_for_client(value) for value in media_attachments)
                    if item
                ]
            except Exception as exc:
                server.LOGGER.warning("Could not inline admin media reply: %s", exc)
        return payload

    async def _chat_session_manager():
        from rest_api import get_session_manager

        return get_session_manager()

    def _chat_call_session(call_id: str, user_id: str) -> bool:
        entry = getattr(server.STATE, "session_cache", {}).get(call_id)
        return bool(
            isinstance(entry, dict)
            and entry.get("admin_chat_call") is True
            and str(entry.get("admin_chat_user_id") or "") == user_id
        )

    @admin_app.post("/api/chat")
    async def admin_chat_message(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"success": False, "error": "JSON object required"})
        user_id = _chat_text(body.get("user_id"), "admin-web-user", 256)
        session_id = _chat_text(body.get("session_id"), "", 256)
        context = body.get("context") if isinstance(body.get("context"), list) else []
        if len(context) > 32:
            return JSONResponse(status_code=413, content={"success": False, "error": "Too many attachments"})
        encoded_bytes = sum(
            len(str(item.get("data") or ""))
            for group in context
            if isinstance(group, dict)
            for item in (group.get("attachments") or [])
            if isinstance(item, dict)
        )
        if encoded_bytes > 48 * 1024 * 1024:
            return JSONResponse(status_code=413, content={"success": False, "error": "Attachments are too large"})
        payload = dict(body)
        payload["user_id"] = user_id
        payload["session_id"] = session_id or f"admin-chat-{uuid.uuid4().hex}"
        payload["context"] = context
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        payload["metadata"] = {
            "client": "admin-web",
            "source": "admin_web",
            **metadata,
        }
        try:
            chat_request = server.ChatRequest(**payload)
            response = await server.process_chat_message(
                chat_request,
                f"http://127.0.0.1:{server.AI_AGENT_SERVER_PORT}",
                authenticated_actor_role="admin",
            )
            return server._json_response_no_store(_chat_response_payload(response))
        except Exception as exc:
            server.LOGGER.error("admin_chat_message failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": "Chat request failed"})

    @admin_app.get("/api/chat/sessions")
    async def admin_chat_sessions(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            limit = max(1, min(100, int(request.query_params.get("limit") or 60)))
        except (TypeError, ValueError):
            limit = 60
        query = _chat_text(request.query_params.get("q"), "", 240)
        requested_user = _chat_text(request.query_params.get("user_id"), "", 256)
        try:
            manager = await _chat_session_manager()
            hits = await manager.search_all_memory(query or "*", limit=max(limit * 20, 200))
        except Exception as exc:
            server.LOGGER.warning("Could not read admin chat history: %s", exc)
            return server._json_response_no_store({"success": True, "sessions": [], "error": "History unavailable"})

        grouped: Dict[tuple[str, str], Dict[str, Any]] = {}
        for hit in hits or []:
            if not isinstance(hit, dict):
                continue
            user_id = _chat_text(hit.get("user_id"), "", 256)
            internal_id = _chat_text(hit.get("session_id"), "", 256)
            external_id = _chat_text(hit.get("external_session_id"), "", 256) or internal_id
            if not user_id or not external_id or (requested_user and user_id != requested_user):
                continue
            try:
                sort_time = float(hit.get("sort_timestamp") or 0)
            except (TypeError, ValueError):
                sort_time = 0.0
            key = (user_id, external_id)
            group = grouped.setdefault(key, {
                "user_id": user_id,
                "session_id": external_id,
                "adk_session_id": internal_id,
                "title": "Conversation",
                "preview": "",
                "message_count": 0,
                "has_files": False,
                "has_voice": False,
                "created_at": hit.get("timestamp") or "",
                "last_activity": hit.get("timestamp") or "",
                "_first": sort_time or float("inf"),
                "_last": sort_time,
            })
            group["message_count"] += 1
            metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
            group["has_files"] = bool(group["has_files"] or metadata.get("has_files"))
            group["has_voice"] = bool(group["has_voice"] or metadata.get("has_voice"))
            if sort_time and sort_time < group["_first"]:
                group["_first"] = sort_time
                group["title"] = _chat_text(hit.get("user_message"), "Conversation", 96)
                group["created_at"] = hit.get("timestamp") or group["created_at"]
            if sort_time >= group["_last"]:
                group["_last"] = sort_time
                group["preview"] = _chat_text(hit.get("agent_response") or hit.get("user_message"), "", 180)
                group["last_activity"] = hit.get("timestamp") or group["last_activity"]
            elif not group["preview"]:
                group["preview"] = _chat_text(hit.get("agent_response") or hit.get("user_message"), "", 180)
        sessions = sorted(grouped.values(), key=lambda item: item.get("_last", 0), reverse=True)[:limit]
        for item in sessions:
            item.pop("_first", None)
            item.pop("_last", None)
        return server._json_response_no_store({"success": True, "sessions": sessions, "count": len(sessions)})

    @admin_app.get("/api/chat/session")
    async def admin_chat_session(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        user_id = _chat_text(request.query_params.get("user_id"), "", 256)
        session_id = _chat_text(request.query_params.get("session_id"), "", 256)
        if not user_id or not session_id:
            return JSONResponse(status_code=400, content={"success": False, "error": "user_id and session_id are required"})
        try:
            info = await server.get_session_info(user_id, session_id)
            payload = _chat_model_dict(info)
            manager = await _chat_session_manager()
            internal_id = manager.get_mapped_session_id(session_id, user_id) or session_id
            raw = await manager.get_user_session(user_id, internal_id)
            messages = []
            for event in (raw or {}).get("events", []) if isinstance(raw, dict) else []:
                data = event.get("data") if isinstance(event, dict) else {}
                if not isinstance(data, dict):
                    continue
                timestamp = event.get("timestamp")
                attachments = [item for item in (_public_attachment(value) for value in (data.get("attachments") or [])) if item]
                if data.get("user_message"):
                    messages.append({"role": "user", "content": str(data["user_message"]), "timestamp": timestamp, "attachments": attachments})
                if data.get("agent_response"):
                    messages.append({"role": "assistant", "content": str(data["agent_response"]), "timestamp": timestamp, "attachments": []})
            if messages:
                payload["messages"] = messages
            payload["session_id"] = session_id
            payload["user_id"] = user_id
            return server._json_response_no_store(payload)
        except Exception as exc:
            status = getattr(exc, "status_code", 500)
            detail = getattr(exc, "detail", "Session unavailable")
            return JSONResponse(status_code=status, content={"success": False, "error": str(detail)})

    @admin_app.post("/api/chat/call/offer")
    async def admin_chat_call_offer(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        if server.WEBRTC is None or getattr(server, "RTCPeerConnection", None) is None:
            return JSONResponse(status_code=503, content={"success": False, "error": "Voice calls are unavailable: aiortc is not installed."})
        try:
            body = await request.json()
        except Exception:
            body = {}
        offer = body.get("offer") if isinstance(body, dict) else None
        if not isinstance(offer, dict) or not str(offer.get("sdp") or "").strip():
            return JSONResponse(status_code=400, content={"success": False, "error": "A WebRTC offer is required"})
        user_id = _chat_text(body.get("user_id"), "admin-web-user", 256)
        call_id = _chat_text(body.get("session_id"), "", 256) or f"admin-call-{uuid.uuid4().hex}"
        if len(call_id) > 256:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid call session"})
        try:
            identity = server.bind_transport_chat_owner("admin-web", user_id, raw_session_id=call_id, pairing_mode="local_pair")
            now = time.time()
            server.prune_session_cache()
            server.STATE.session_cache[call_id] = {
                "id": call_id,
                "created_at": now,
                "expires_at": now + (server.PAIR_SESSION_EXPIRATION_MINUTES * 60),
                "authenticated": True,
                "admin_chat_call": True,
                "admin_chat_user_id": user_id,
                "stable_client_id": user_id,
                "client_display_name": "Admin Chat",
                "canonical_user_id": identity.canonical_user_id,
                "canonical_session_id": identity.canonical_session_id,
            }
            offer_payload = dict(offer)
            offer_payload["client_display_name"] = "Admin Chat"
            answer = await server.WEBRTC.handle_session_offer(call_id, offer_payload)
            return server._json_response_no_store({"success": True, "session_id": call_id, "answer": answer})
        except Exception as exc:
            try:
                server.WEBRTC.cleanup_session(call_id)
            except Exception:
                pass
            server.STATE.session_cache.pop(call_id, None)
            server.LOGGER.error("admin_chat_call_offer failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": "Voice call negotiation failed"})

    @admin_app.post("/api/chat/call/candidate")
    async def admin_chat_call_candidate(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            body = await request.json()
        except Exception:
            body = {}
        call_id = _chat_text(body.get("session_id"), "", 256) if isinstance(body, dict) else ""
        user_id = _chat_text(body.get("user_id"), "admin-web-user", 256) if isinstance(body, dict) else "admin-web-user"
        candidate = body.get("candidate") if isinstance(body, dict) else None
        if not call_id or not candidate or not _chat_call_session(call_id, user_id):
            return JSONResponse(status_code=404, content={"success": False, "error": "Call session not found"})
        applied = await server.WEBRTC.handle_session_candidate(call_id, candidate)
        return server._json_response_no_store({"success": bool(applied)})

    @admin_app.post("/api/chat/call/close")
    async def admin_chat_call_close(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            body = await request.json()
        except Exception:
            body = {}
        call_id = _chat_text(body.get("session_id"), "", 256) if isinstance(body, dict) else ""
        user_id = _chat_text(body.get("user_id"), "admin-web-user", 256) if isinstance(body, dict) else "admin-web-user"
        if not call_id or not _chat_call_session(call_id, user_id):
            return server._json_response_no_store({"success": True})
        try:
            server.WEBRTC.cleanup_session(call_id)
        finally:
            server.STATE.session_cache.pop(call_id, None)
        return server._json_response_no_store({"success": True})

    @admin_app.post("/api/test/simulate_pair")
    async def simulate_pair_command(request: Request):
        # H-18 gate: this endpoint triggers OTP generation and tunnelmole
        # start. It must not be reachable from the public tunnel or from an
        # unauthenticated local browser. Allow it only when (a) called by an
        # authenticated admin session, or (b) explicitly enabled via env flag
        # AND originating from loopback (the test harness).
        is_logged_in = server._is_logged_in(request)
        loopback_test = server._test_endpoints_enabled() and server._is_loopback_client_host(
            request.client.host if request.client else None
        )
        # from __debug_provenance_x__ import email
        if not (is_logged_in or loopback_test):
            return JSONResponse(status_code=404, content={"error": "Not found"})

        if not server.pairing_router:
            return JSONResponse(status_code=500, content={"error": "pairing_router not available"})

        test_pair_url = server.os.environ.get("AUTOYOU_TEST_PAIR_PUBLIC_URL", "").strip()
        if loopback_test and test_pair_url:
            otp = ""
            try:
                otp = server.pairing_router._generate_otp_hash_and_cache(5)
            except Exception:
                otp = ""
            response_text = f"/otp\n{server.json.dumps({'otp': otp or '12345678', 'url': test_pair_url})}"
        else:
            # Trigger the pair command processing (platform="test")
            # This will generate OTP and start/extend Tunnelmole
            response_text = await server.pairing_router._handle_pair_command(platform="test", sender_id="test")

        payload = server.parse_otp_response_payload(response_text)
        if payload is None:
            return JSONResponse(status_code=500, content={"error": f"Pair command failed: {response_text}"})

        return payload

    @admin_app.get("/api/status")
    async def admin_api_status():
        """Simple status check for Admin Server."""
        return {
            "status": "running",
            "service": "AutoYou Admin Server",
            "instance": server.build_instance_runtime_status(),
            "runtime": server.build_runtime_environment_status(),
        }

    @admin_app.get("/api/admin/bootstrap")
    async def admin_ui_bootstrap(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        return server._json_response_no_store(await server._build_admin_ui_bootstrap_payload())

    @admin_app.post("/api/setup/recipe/preview")
    async def admin_setup_recipe_preview(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        answers = payload.get("answers") if isinstance(payload, dict) else {}
        agents_payload = await server.asyncio.to_thread(server._safe_agent_builder_listing_payload)
        return server._json_response_no_store(
            server.compile_setup_recipe(
                answers if isinstance(answers, dict) else {},
                config=server.STATE.config or server._default_config(),
                agents_payload=agents_payload,
                api_route_count=server._admin_setup_api_route_count(),
            )
        )

    @admin_app.get("/api/connection/status")
    async def admin_connection_status(request: Request):
        """Connection-helper snapshot for the admin UI.

        All reads happen server-side; no secrets are returned to the browser.
        """
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        client = server._ensure_cloud_entitlements()
        if client is None:
            return server._json_response_no_store({"available": False, "signed_in": False})
        try:
            status = await client.get_connection_status()
            status["available"] = True
            return server._json_response_no_store(status)
        except Exception as exc:
            server.LOGGER.warning("connection status fetch failed: %s", exc)
            return server._json_response_no_store({"available": False, "signed_in": False, "error": "fetch_failed"})

    @admin_app.post("/api/admin/config")
    async def admin_ui_update_config(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)

        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})
        remote_error = server._remote_browser_config_change_error(request, payload)
        if remote_error:
            return remote_error

        try:
            return server._json_response_no_store(await server._apply_admin_ui_config_update(payload))
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_ui_update_config failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/setup/recipe/apply")
    async def admin_setup_recipe_apply(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)

        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
        include_guarded = bool(payload.get("include_guarded", False))
        include_security = bool(payload.get("include_security", False))
        if (include_guarded or include_security) and server._request_via_remote_browser_proxy(request):
            return server._remote_browser_credential_denied()

        try:
            agents_payload = await server.asyncio.to_thread(server._safe_agent_builder_listing_payload)
            recipe = server.compile_setup_recipe(
                answers,
                config=server.STATE.config or server._default_config(),
                agents_payload=agents_payload,
                api_route_count=server._admin_setup_api_route_count(),
            )
            config_payload = server.copy.deepcopy(recipe.get("safe_config_patch") or {})
            if include_guarded:
                for section, value in (recipe.get("guarded_config_patch") or {}).items():
                    config_payload[section] = value
            security_mode = None
            if include_security:
                security_mode = str((recipe.get("security_patch") or {}).get("mode") or "").strip().lower() or None
            bootstrap = await server._apply_admin_ui_config_update(config_payload, security_mode=security_mode)
            refreshed_recipe = server.compile_setup_recipe(
                answers,
                config=bootstrap.get("config", {}),
                agents_payload=bootstrap.get("agents", {}),
                api_route_count=server._admin_setup_api_route_count(),
            )
            return server._json_response_no_store(
                {
                    "success": True,
                    "applied": True,
                    "include_guarded": include_guarded,
                    "include_security": include_security,
                    "recipe": refreshed_recipe,
                    "bootstrap": bootstrap,
                }
            )
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_setup_recipe_apply failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/admin/security/mode")
    async def admin_ui_update_security_mode(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        mode = server._normalize_security_mode((payload or {}).get("mode") or "secure", default="")
        if mode not in {
            "normal",
            "secure",
            "secure_professional",
            server.SECURE_PROFESSIONAL_MAXIMUS_MODE,
        }:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "mode must be normal, secure, secure_professional, or secure_professional_maximus",
                },
            )

        try:
            cfg = server._loaded_config_for_update(copy_config=True)
            cfg.setdefault("security", {})["mode"] = mode
            worker_was_running = bool(
                server.STATE.agent_process is not None and server._is_agent_process_running(server.STATE.agent_process)
            )
            # Downgrade from Maximus: decrypt every owned envelope back to plaintext
            # BEFORE dropping the boundary, otherwise the sealed files stay on disk
            # and read closed in Normal (SQLite "file is not a database", JSON raises).
            # unseal_secure_storage stages+swaps atomically and only then tears the
            # in-memory adapter down, so a failure here leaves Maximus fully intact.
            storage_unsealed = None
            if mode != server.SECURE_PROFESSIONAL_MAXIMUS_MODE and server.secure_storage_enabled():
                storage_unsealed = server.unseal_secure_storage(scan_roots=server._secure_storage_scan_roots())
            server.STATE.config = server._save_and_reload_state_config(cfg)
            # _save_and_reload_state_config runs the stranded-envelope sweep for
            # the case this branch cannot see: Maximus sealed stores in an earlier
            # process, so the boundary was never live here and the unseal above was
            # skipped. Report whatever that sweep found alongside this transition.
            storage_recovery = server._secure_storage_recovery_status()
            worker_restarted = False
            # Restart the worker on either transition so the child re-attaches to (or
            # detaches from) the storage boundary its parent just changed.
            if worker_was_running and (
                mode == server.SECURE_PROFESSIONAL_MAXIMUS_MODE
                or storage_unsealed is not None
                or int(storage_recovery.get("recovered") or 0) > 0
            ):
                worker_restarted = bool(await server.restart_ai_agent_server())
            return server._json_response_no_store(
                {
                    "success": True,
                    "mode": mode,
                    "ai_agent_restarted": worker_restarted,
                    "storage_unsealed": storage_unsealed,
                    "storage_recovery": storage_recovery,
                    "bootstrap": await server._build_admin_ui_bootstrap_payload(),
                }
            )
        except Exception as exc:
            server.LOGGER.error("admin_ui_update_security_mode failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/native/security/totp/setup")
    async def native_security_totp_setup(request: Request):
        """Create an uncommitted authenticator QR for native desktop settings."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)
        if server.pyotp is None:
            return JSONResponse(status_code=503, content={"success": False, "error": "Authenticator setup is unavailable."})
        try:
            secret = server.pyotp.random_base32()
            issuer, account_name = server._totp_account_name("pairing")
            _, otpauth = server._build_totp_otpauth(account_name, secret, issuer)
            qr_data_url = server._build_local_qr_data_url(otpauth)
            if not qr_data_url:
                return JSONResponse(status_code=503, content={"success": False, "error": "Could not create the authenticator QR on this computer."})
            return server._json_response_no_store({
                "success": True,
                "secret": secret,
                "qr_data_url": qr_data_url,
            })
        except Exception as exc:
            server.LOGGER.warning("Native authenticator setup could not be created: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": "Authenticator setup could not be created."})

    @admin_app.post("/api/native/security/totp/confirm")
    async def native_security_totp_confirm(request: Request):
        """Save a new shared authenticator only after the scanned code is verified."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)
        try:
            payload = await request.json()
            secret = server._normalize_totp_secret(payload.get("secret"))
            code = str(payload.get("code") or "").strip()
            if not secret or len(secret) > 128 or len(code) != 6 or not code.isdigit():
                return JSONResponse(status_code=400, content={"success": False, "error": "Enter the six-digit code from your authenticator."})
            if not server._verify_totp_secret(secret, code, valid_window=1):
                return JSONResponse(status_code=400, content={"success": False, "error": "That code did not match. Check the QR and try the current code again."})
            cfg = server._loaded_config_for_update(copy_config=True)
            cfg.setdefault("security", {})["totp_secret"] = secret
            server.STATE.config = server._save_and_reload_state_config(cfg)
            return server._json_response_no_store({"success": True, "totp_configured": True})
        except Exception as exc:
            server.LOGGER.error("Native authenticator setup could not be saved: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": "Authenticator setup could not be saved."})

    @admin_app.post("/api/native/security/totp/show")
    async def native_security_totp_show(request: Request):
        """Return the saved shared authenticator with a locally-rendered QR."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        if not server._has_loaded_config_session():
            return JSONResponse(status_code=401, content={"success": False, "error": "Not logged in"})
        try:
            cfg = server.STATE.config or server._default_config()
            server._apply_default_security_config(cfg)
            secret = server._get_pairing_totp_secret(cfg)
            if not secret:
                return JSONResponse(status_code=404, content={"success": False, "error": "No saved 2FA secret."})
            payload = server._totp_payload(secret=secret, target="pairing")
            qr_data_url = server._build_local_qr_data_url(payload["otpauth"])
            if not qr_data_url:
                return JSONResponse(status_code=503, content={"success": False, "error": "Could not create the authenticator QR on this computer."})
            payload["qr_data_url"] = qr_data_url
            payload.pop("qr_url", None)
            return server._json_response_no_store(payload)
        except Exception as exc:
            server.LOGGER.warning("Native authenticator secret could not be shown: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": "Authenticator secret could not be shown."})

    @admin_app.get("/api/native/security/totp/current-code")
    async def native_security_totp_current_code(request: Request):
        """Return the current code for the saved shared authenticator."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        if not server._has_loaded_config_session():
            return JSONResponse(status_code=401, content={"success": False, "error": "Not logged in"})
        try:
            import time as _time
            cfg = server.STATE.config or server._default_config()
            server._apply_default_security_config(cfg)
            secret = server._get_pairing_totp_secret(cfg)
            if not secret or server.pyotp is None:
                return JSONResponse(status_code=404, content={"success": False, "error": "No 2FA secret configured."})
            code = server.pyotp.TOTP(secret).now()
            return server._json_response_no_store({
                "success": True,
                "code": code,
                "seconds_remaining": 30 - (int(_time.time()) % 30),
                "period": 30,
            })
        except Exception as exc:
            server.LOGGER.warning("Native authenticator code could not be shown: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": "Authenticator code could not be shown."})

    @admin_app.post("/api/native/security/totp/import")
    async def native_security_totp_import(request: Request):
        """Import an authenticator setup link or Base32 secret for native settings."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)
        try:
            payload = await request.json()
            preview = server._preview_totp_payload(value=payload.get("value"), target="pairing")
            cfg = server._loaded_config_for_update(copy_config=True)
            server._apply_default_security_config(cfg)
            cfg.setdefault("security", {})[server._totp_target_config_key(preview["target"])] = preview["secret"]
            server.STATE.config = server._save_and_reload_state_config(cfg)
            qr_data_url = server._build_local_qr_data_url(preview["otpauth"])
            if not qr_data_url:
                return JSONResponse(status_code=503, content={"success": False, "error": "Could not create the authenticator QR on this computer."})
            preview["qr_data_url"] = qr_data_url
            preview.pop("qr_url", None)
            return server._json_response_no_store(preview)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
        except RuntimeError as exc:
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.warning("Native authenticator secret could not be imported: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": "Authenticator secret could not be imported."})

    @admin_app.post("/api/native/security/totp/delete")
    async def native_security_totp_delete(request: Request):
        """Remove the saved shared authenticator used for AutoYou pairing."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)
        try:
            cfg = server._loaded_config_for_update(copy_config=True)
            server._apply_default_security_config(cfg)
            cfg.setdefault("security", {})["totp_secret"] = ""
            server.STATE.config = server._save_and_reload_state_config(cfg)
            return server._json_response_no_store({"success": True, "target": "pairing"})
        except Exception as exc:
            server.LOGGER.warning("Native authenticator secret could not be removed: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": "Authenticator secret could not be removed."})

    @admin_app.post("/api/admin/security/tier")
    async def admin_ui_update_security_tier(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        tier = str((payload or {}).get("tier") or "B").strip().upper()
        if tier not in {"A", "B"}:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "tier must be A (Enhanced Pairing) or B (Quick Pairing)"},
            )

        try:
            cfg = server._loaded_config_for_update(copy_config=True)
            cfg.setdefault("security", {})["tier"] = tier
            server.STATE.config = server._save_and_reload_state_config(cfg)
            return server._json_response_no_store(
                {
                    "success": True,
                    "tier": tier,
                    "bootstrap": await server._build_admin_ui_bootstrap_payload(),
                }
            )
        except Exception as exc:
            server.LOGGER.error("admin_ui_update_security_tier failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/admin/security/storage/rotate")
    async def admin_ui_rotate_storage_key(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        if not server.secure_storage_enabled():
            return JSONResponse(
                status_code=409,
                content={"success": False, "error": "Secure Professional Maximus storage is not active."},
            )
        try:
            worker_was_running = bool(
                server.STATE.agent_process is not None and server._is_agent_process_running(server.STATE.agent_process)
            )
            status = server.rotate_secure_storage(scan_roots=server._secure_storage_scan_roots())
            worker_restarted = False
            if worker_was_running:
                worker_restarted = bool(await server.restart_ai_agent_server())
            return server._json_response_no_store(
                {
                    "success": True,
                    "message": "Maximus storage key rotated and protected data re-encrypted.",
                    "secure_storage": status,
                    "ai_agent_restarted": worker_restarted,
                    "bootstrap": await server._build_admin_ui_bootstrap_payload(),
                }
            )
        except Exception as exc:
            server.LOGGER.error("admin_ui_rotate_storage_key failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/admin/password")
    async def admin_ui_change_password(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        new_password = str((payload or {}).get("new_password") or "")
        confirm_password = str((payload or {}).get("confirm_password") or "")
        if not new_password or new_password != confirm_password:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "New password confirmation does not match."},
            )
        strength_error = server._server_password_strength_error(new_password)
        if strength_error:
            return JSONResponse(status_code=400, content={"success": False, "error": strength_error})

        try:
            worker_was_running = bool(
                server.STATE.agent_process is not None and server._is_agent_process_running(server.STATE.agent_process)
            )
            if server.os.path.exists(server.CONFIG_FILE_PATH):
                try:
                    if server.os.path.exists(server.CONFIG_BAK_PATH):
                        server.os.remove(server.CONFIG_BAK_PATH)
                except Exception:
                    pass
                server.os.replace(server.CONFIG_FILE_PATH, server.CONFIG_BAK_PATH)

            cfg = server._loaded_config_for_update()
            server._persist_state_config(
                cfg,
                server_password=new_password,
                preferred_store=server.STATE.config_store,
            )
            server.STATE.used_default_password = new_password == server.DEFAULT_SERVER_PASSWORD
            if worker_was_running and server.secure_storage_enabled():
                await server.restart_ai_agent_server()
            return server._json_response_no_store(
                {
                    "success": True,
                    "message": "Password updated",
                    "bootstrap": await server._build_admin_ui_bootstrap_payload(),
                }
            )
        except Exception as exc:
            server.LOGGER.error("admin_ui_change_password failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/admin/password/generate")
    async def admin_ui_generate_password(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        return server._json_response_no_store(
            {
                "success": True,
                "password": server.generate_random_server_password(),
                "length": server.SERVER_PASSWORD_GENERATOR_LENGTH,
            }
        )

    @admin_app.get("/api/login-startup-status")
    async def admin_login_startup_status():
        """Expose startup progress for the async login screen."""
        return server._json_response_no_store(server._startup_status_payload())

    @admin_app.get("/api/login-minigame/high-score")
    async def admin_login_minigame_high_score():
        """Return the persisted login-screen mini-game score summary."""
        try:
            score = server._get_login_minigame_high_score()
            response = JSONResponse(
                {
                    "high_score": score,
                    "high_scores": server._list_login_minigame_high_scores(limit=10),
                    "game_key": server.LOGIN_MINIGAME_KEY,
                }
            )
            response.headers["Cache-Control"] = "no-store"
            return response
        except Exception as e:
            server.LOGGER.warning("Unable to load login minigame high score: %s", e)
            return JSONResponse({"high_score": 0, "high_scores": [], "game_key": server.LOGIN_MINIGAME_KEY})

    @admin_app.post("/api/login-minigame/high-score")
    async def admin_login_minigame_save_high_score(request: Request):
        """Persist a completed boot-sweep run and return the latest top scores."""
        try:
            payload = await request.json()
            score = int(payload.get("score") or 0)
            clicks = int(payload.get("clicks") or 0)
            seconds = float(payload.get("seconds") or 0.0)
            result = server._record_login_minigame_run(score, clicks=clicks, seconds=seconds)
            high_score = int(result.get("high_score") or 0)
            if high_score == max(0, score):
                server.LOGGER.info(
                    "Updated login minigame high score to %d (%d clicks in %.3fs)",
                    high_score,
                    clicks,
                    seconds,
                )
            return JSONResponse(
                {
                    "success": True,
                    "high_score": high_score,
                    "high_scores": result.get("high_scores") or [],
                    "recorded_run": result.get("recorded_run"),
                    "game_key": server.LOGIN_MINIGAME_KEY,
                }
            )
        except Exception as e:
            server.LOGGER.warning("Unable to save login minigame high score: %s", e)
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    @admin_app.delete("/api/login-minigame/high-score")
    async def admin_login_minigame_reset_high_score(request: Request):
        """Delete the persisted login-screen mini-game scores."""
        # H-18: destructive endpoint. GET + POST remain public because the
        # login page needs them pre-auth, but wiping scores is admin-only.
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            server._ensure_login_ui_db()
            with server.LOGIN_UI_DB_LOCK:
                conn = server.sqlite3.connect(server.LOGIN_UI_DB_PATH)
                try:
                    conn.execute(
                        "DELETE FROM login_minigame_scores WHERE game_key = ?",
                        (str(server.LOGIN_MINIGAME_KEY),),
                    )
                    conn.execute(
                        "DELETE FROM login_minigame_runs WHERE game_key = ?",
                        (str(server.LOGIN_MINIGAME_KEY),),
                    )
                    conn.commit()
                finally:
                    conn.close()
            return JSONResponse({"success": True, "high_score": 0, "high_scores": [], "game_key": server.LOGIN_MINIGAME_KEY})
        except Exception as e:
            server.LOGGER.warning("Unable to reset login minigame high score: %s", e)
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    @admin_app.get("/api/ai/internet/search_enabled")
    async def admin_get_internet_search_enabled(request: Request):
        """Return whether internet searches are enabled globally."""
        # H-18: authenticated admin only. Reveals agent-installation state.
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            enabled = server._get_agent_frontend_enabled("internet_agent", cfg=(server.STATE.config or {}))
            installed = server._is_internet_agent_installed()
            return {"success": True, "enabled": enabled, "installed": installed}
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.post("/api/ai/internet/search_enabled")
    async def admin_set_internet_search_enabled(request: Request):
        """Enable or disable internet searches globally and persist to encrypted config."""
        # H-18: authenticated admin only. Sensitive config write.
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            if not server._is_internet_agent_installed():
                return JSONResponse(
                    status_code=409,
                    content={
                        "success": False,
                        "error": "Internet Search is not installed. Install it and restart AutoYou AI before using this toggle.",
                    },
                )

            payload = {}
            try:
                payload = await request.json()
            except Exception:
                # Fallback to form
                form = await request.form()
                payload = {k: v for k, v in form.items()}

            enabled = server._coerce_enabled_flag(payload.get("enabled"))
            _, runtime_sync = await server._set_internet_search_enabled_live(bool(enabled))
            if not runtime_sync.get("success"):
                return JSONResponse(
                    status_code=503,
                    content={
                        "success": False,
                        "enabled": bool(enabled),
                        "persisted": True,
                        "error": "The setting was saved, but the running AutoYou AI worker could not be updated.",
                    },
                )
            return {
                "success": True,
                "enabled": bool(enabled),
                "installed": server._is_internet_agent_installed(),
                "runtime_sync": runtime_sync.get("status"),
            }
        except Exception as e:
            if isinstance(e, server.ConfigWriteBlocked):
                return server._json_config_write_blocked_response(str(e))
            server.LOGGER.error(f"Error setting internet search enabled: {e}")
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.get("/api/admin/profile-image")
    async def admin_ui_get_profile_image(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        image_path = server._get_admin_profile_image_path()
        image_bytes = image_path.read_bytes() if image_path is not None else b""
        if len(image_bytes) > server._ADMIN_PROFILE_IMAGE_MAX_BYTES:
            image_bytes = b""
        import base64
        return server._json_response_no_store({
            "profile_user_id": server._get_stable_server_id(),
            "mime_type": server._get_admin_profile_image_media_type(image_path) if image_path and image_bytes else "",
            "data_base64": base64.b64encode(image_bytes).decode("ascii") if image_bytes else "",
        })

    @admin_app.post("/api/admin/profile-image")
    async def admin_ui_upload_profile_image(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error

        uploaded = None
        try:
            form = await request.form()
            uploaded = form.get("image")
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid upload form."})

        if uploaded is None or not hasattr(uploaded, "read"):
            return JSONResponse(status_code=400, content={"success": False, "error": "Profile image file is required."})

        try:
            image_payload = await uploaded.read(server._ADMIN_PROFILE_IMAGE_MAX_BYTES + 1)
            server._save_admin_profile_image(image_payload)
            if server.WEBRTC is not None:
                await server.WEBRTC.broadcast_server_profile()
            return server._json_response_no_store(await server._build_admin_ui_bootstrap_payload())
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_ui_upload_profile_image failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})
        finally:
            close_method = getattr(uploaded, "close", None)
            if callable(close_method):
                close_result = close_method()
                if server.asyncio.iscoroutine(close_result):
                    await close_result

    @admin_app.delete("/api/admin/profile-image")
    async def admin_ui_delete_profile_image(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error

        try:
            server._delete_admin_profile_image_files()
            if server.WEBRTC is not None:
                await server.WEBRTC.broadcast_server_profile()
            return server._json_response_no_store(await server._build_admin_ui_bootstrap_payload())
        except Exception as exc:
            server.LOGGER.error("admin_ui_delete_profile_image failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/save-config")
    async def save_config_endpoint(
        request: Request,
        kind: str = Form(...),
        bot_token: Optional[str] = Form(None),
        rtc_json: Optional[str] = Form(None),
        server_name: Optional[str] = Form(None),
        acl_usernames: Optional[str] = Form(None),
        acl_sender_ids: Optional[str] = Form(None),
        telegram_access_gate_enabled: Optional[str] = Form(None),
        telegram_silent_unapproved_messages: Optional[str] = Form(None),
        signal_enabled: Optional[str] = Form(None),
        signal_port: Optional[str] = Form(None),
        signal_device_name: Optional[str] = Form(None),
        signal_shutdown_docker: Optional[str] = Form(None),
        whatsapp_enabled: Optional[str] = Form(None),
        whatsapp_port: Optional[str] = Form(None),
        whatsapp_device_name: Optional[str] = Form(None),
        ollama_api_base: Optional[str] = Form(None),
        ollama_model: Optional[str] = Form(None),
        use_google_api: Optional[str] = Form(None),
        google_model: Optional[str] = Form(None),
        google_api_key: Optional[str] = Form(None),
        ai_provider: Optional[str] = Form(None),
        openclaw_port: Optional[str] = Form(None),
        openclaw_token: Optional[str] = Form(None),
        openclaw_model: Optional[str] = Form(None),
        openclaw_agent_port: Optional[str] = Form(None),
        openclaw_agent_token: Optional[str] = Form(None),
        openclaw_agent_model: Optional[str] = Form(None),
        hermes_port: Optional[str] = Form(None),
        hermes_token: Optional[str] = Form(None),
        hermes_model: Optional[str] = Form(None),
        litellm_model: Optional[str] = Form(None),
        litellm_api_key: Optional[str] = Form(None),
        litellm_api_base: Optional[str] = Form(None),
        odysseus_api_base: Optional[str] = Form(None),
        odysseus_model: Optional[str] = Form(None),
        odysseus_token: Optional[str] = Form(None),
        speech_tts_provider: Optional[str] = Form(None),
        speech_tts_rate: Optional[str] = Form(None),
        speech_tts_system_voice: Optional[str] = Form(None),
        speech_openai_tts_model: Optional[str] = Form(None),
        speech_openai_tts_voice: Optional[str] = Form(None),
        speech_openai_tts_instructions: Optional[str] = Form(None),
        speech_openai_base_url: Optional[str] = Form(None),
        speech_openai_api_key: Optional[str] = Form(None),
        speech_azure_key: Optional[str] = Form(None),
        speech_azure_region: Optional[str] = Form(None),
        speech_azure_voice: Optional[str] = Form(None),
        speech_azure_endpoint_id: Optional[str] = Form(None),
        speech_stt_model: Optional[str] = Form(None),
        speech_stt_language: Optional[str] = Form(None),
        speech_stt_device: Optional[str] = Form(None),
        speech_stt_compute_type: Optional[str] = Form(None),
        speech_stt_silero_sensitivity: Optional[str] = Form(None),
        speech_stt_post_speech_silence_duration: Optional[str] = Form(None),
        ai_agent_enabled: Optional[str] = Form(None),
        ai_agent_auto_start: Optional[str] = Form(None),
        ai_agent_record_messages: Optional[str] = Form(None),
        ai_agent_memory_backend: Optional[str] = Form(None),
        ai_agent_port: Optional[str] = Form(None),
        tunnelmole_enabled: Optional[str] = Form(None),
        tunnelmole_timeout: Optional[str] = Form(None),
        tunnelmole_otp_timeout: Optional[str] = Form(None),
        tunnelmole_otp_multiuse: Optional[str] = Form(None),
        tunnelmole_pair_code_mode: Optional[str] = Form(None),
        tunnelmole_connection_mode: Optional[str] = Form(None),
        # New AutoYou Page settings
        autoyou_page_port: Optional[str] = Form(None),
        autoyou_page_auto_start: Optional[str] = Form(None),
        autoyou_page_timeline_days: Optional[str] = Form(None),
        autoyou_page_theme: Optional[str] = Form(None),
        autoyou_custom_forward_enabled: Optional[str] = Form(None),
        autoyou_custom_forward_port: Optional[str] = Form(None),
        autoyou_advertised_websites: Optional[str] = Form(None),
        autoyou_bookmarks: Optional[str] = Form(None),
        admin_frontend_proxy_enabled: Optional[str] = Form(None),
    ):
        redir = server._require_login(request)
        if redir:
            return redir
        block_reason = server._config_write_block_reason()
        if block_reason:
            return PlainTextResponse(block_reason, status_code=409)
        cfg = server._loaded_config_for_update()
        server._apply_default_security_config(cfg)
        server._apply_default_tunnelmole_config(cfg)
        server._apply_default_client_identity_config(cfg)
        server._apply_default_agent_frontends_config(cfg)
        server._apply_default_bluetooth_pairing_config(cfg)
        server._apply_default_speech_config(cfg)
        server._apply_default_autoyou_page_config(cfg)
        server._apply_default_messaging_partner_config(cfg)
        server._apply_default_video_call_config(cfg)
        try:
            if kind == 'telegram':
                # Persist bot token if provided
                if bot_token is not None:
                    cfg.setdefault('telegram', {})['bot_token'] = bot_token.strip()
                # Parse and persist ACL usernames if provided
                if acl_usernames is not None:
                    raw = acl_usernames.strip()
                    if not raw:
                        cfg.setdefault('telegram', {})['acl_usernames'] = []
                    else:
                        parts = server.re.split(r'[\,\s]+', raw)
                        names = []
                        for p in parts:
                            h = p.strip()
                            if not h:
                                continue
                            h = h.lstrip('@')
                            if h:
                                names.append(h.lower())
                        cfg.setdefault('telegram', {})['acl_usernames'] = sorted(set(names))
                if acl_sender_ids is not None:
                    raw_sender_ids = acl_sender_ids.strip()
                    if not raw_sender_ids:
                        cfg.setdefault('telegram', {})['acl_sender_ids'] = []
                    else:
                        parts = server.re.split(r'[\,\s]+', raw_sender_ids)
                        sender_ids: List[str] = []
                        invalid_sender_ids: List[str] = []
                        for part in parts:
                            candidate = server._normalize_telegram_sender_id(part)
                            if candidate:
                                sender_ids.append(candidate)
                            elif part.strip():
                                invalid_sender_ids.append(part.strip())
                        if invalid_sender_ids:
                            raise ValueError(
                                'Invalid Telegram user IDs: ' + ', '.join(sorted(set(invalid_sender_ids)))
                            )
                        cfg.setdefault('telegram', {})['acl_sender_ids'] = sorted(set(sender_ids))
                cfg.setdefault('telegram', {})['access_gate_enabled'] = telegram_access_gate_enabled is not None and telegram_access_gate_enabled.lower() in ['true', 'on', '1']
                cfg.setdefault('telegram', {})['silent_unapproved_messages'] = telegram_silent_unapproved_messages is not None and telegram_silent_unapproved_messages.lower() in ['true', 'on', '1']
                # Stop current bot so the new settings apply on restart
                await server.stop_telegram()
            elif kind == 'signal':
                # Handle Signal configuration
                cfg.setdefault('signal', {})

                # Handle checkbox: if signal_enabled is None, it means checkbox was unchecked (disabled)
                # If it's "on" or "true", it means checkbox was checked (enabled)
                if signal_enabled is not None:
                    cfg['signal']['enabled'] = signal_enabled.lower() in ['true', 'on', '1']
                else:
                    # Checkbox was unchecked, explicitly disable Signal
                    cfg['signal']['enabled'] = False
                    server.LOGGER.info("Signal service disabled via Admin Dashboard")

                if signal_port is not None:
                    try:
                        port = int(signal_port.strip())
                        if port < 1 or port > 65535:
                            raise ValueError('Port must be between 1 and 65535')
                        cfg['signal']['port'] = port
                    except ValueError as e:
                        raise ValueError(f'Invalid port number: {e}')
                if signal_device_name is not None:
                    device_name = signal_device_name.strip()
                    if not device_name:
                        raise ValueError('Device name cannot be empty')
                    cfg['signal']['device_name'] = device_name
                if signal_shutdown_docker is not None:
                    cfg['signal']['shutdown_docker_on_exit'] = signal_shutdown_docker.lower() == 'true'

                # Always stop current Signal service first to ensure clean state
                await server.stop_signal()
            elif kind == 'whatsapp':
                # Handle WhatsApp configuration
                cfg.setdefault('whatsapp', {})

                # Handle checkbox: if whatsapp_enabled is None, it means checkbox was unchecked (disabled)
                # If it's "on" or "true", it means checkbox was checked (enabled)
                if whatsapp_enabled is not None:
                    cfg['whatsapp']['enabled'] = whatsapp_enabled.lower() in ['true', 'on', '1']
                else:
                    # Checkbox was unchecked, explicitly disable WhatsApp
                    cfg['whatsapp']['enabled'] = False
                    server.LOGGER.info("WhatsApp service disabled via Admin Dashboard")

                if whatsapp_port is not None:
                    try:
                        port = int(whatsapp_port.strip())
                        if port < 1 or port > 65535:
                            raise ValueError('Port must be between 1 and 65535')
                        cfg['whatsapp']['port'] = port
                        cfg['whatsapp']['websocket_port'] = port
                    except ValueError as e:
                        raise ValueError(f'Invalid port number: {e}')
                if whatsapp_device_name is not None:
                    device_name = whatsapp_device_name.strip()
                    if not device_name:
                        raise ValueError('Device name cannot be empty')
                    cfg['whatsapp']['device_name'] = device_name

                # Always stop current WhatsApp service first to ensure clean state
                await server.stop_whatsapp()
            elif kind == 'ollama':
                # Handle Ollama configuration
                cfg.setdefault('ollama', {})

                if ollama_api_base is not None:
                    api_base = ollama_api_base.strip()
                    if api_base:
                        cfg['ollama']['api_base'] = api_base
                        # Update environment variable for immediate effect
                        server.os.environ['OLLAMA_API_BASE'] = api_base

                if ollama_model is not None:
                    model = ollama_model.strip()
                    if model:
                        cfg['ollama']['model'] = model
                        # Update environment variable for immediate effect
                        server.os.environ['OLLAMA_MODEL'] = model

                # ── Active provider selection ─────────────────────────────────────
                _valid_providers = {"ollama", "ollama_gateway", "odysseus", "openclaw", "hermes", "litellm", "google", "apple_intelligence"}
                ap = cfg.setdefault("ai_provider", {})

                if ai_provider is not None:
                    prov = ai_provider.strip().lower()
                    if prov == "apple_intelligence":
                        from shared.apple_intelligence import status as apple_intelligence_status
                        apple = await apple_intelligence_status()
                        if not apple["available"]:
                            raise ValueError(apple["detail"])
                    if prov in _valid_providers:
                        ap["provider"] = prov
                        # Keep legacy flag in sync for backward compat
                        cfg["ollama"]["use_google_api"] = (prov == "google")
                        server.os.environ["AI_PROVIDER"]   = prov
                        server.os.environ["USE_GOOGLE_API"] = "true" if prov == "google" else "false"
                else:
                    # Radio button - if not sent, keep existing; but if google checkbox
                    # path is used (legacy), sync from it below.
                    pass

                # ── Legacy Google checkbox (kept for backward compat) ─────────────
                if use_google_api is not None:
                    google_flag = use_google_api.lower() in ["true", "on", "1"]
                    cfg["ollama"]["use_google_api"] = google_flag
                    if ai_provider is None:
                        # Only change provider from the checkbox when no radio was sent
                        resolved_prov = "google" if google_flag else "ollama"
                        ap["provider"] = resolved_prov
                        server.os.environ["AI_PROVIDER"]   = resolved_prov
                        server.os.environ["USE_GOOGLE_API"] = "true" if google_flag else "false"

                if google_model is not None:
                    model = google_model.strip()
                    if model:
                        cfg["ollama"]["google_model"] = model
                        server.os.environ["GOOGLE_MODEL"] = model

                if google_api_key is not None:
                    api_key = google_api_key.strip()
                    if api_key and api_key != server.MASKED_SECRET_PLACEHOLDER:
                        cfg["ollama"]["google_api_key"] = api_key
                        server.os.environ["GOOGLE_API_KEY"] = api_key

                # ── OpenClaw provider settings ────────────────────────────────────
                if openclaw_port is not None:
                    try:
                        ap["openclaw_port"] = int(openclaw_port.strip())
                        server.os.environ["OPENCLAW_PORT"] = openclaw_port.strip()
                    except ValueError:
                        pass
                if openclaw_token is not None:
                    tok = openclaw_token.strip()
                    if tok != server.MASKED_SECRET_PLACEHOLDER:
                        ap["openclaw_token"] = tok
                        server.os.environ["OPENCLAW_TOKEN"] = tok
                if openclaw_model is not None:
                    m = openclaw_model.strip()
                    if m:
                        ap["openclaw_model"] = m
                        server.os.environ["OPENCLAW_MODEL"] = m

                # ── OpenClaw sub-agent settings ───────────────────────────────────
                if openclaw_agent_port is not None:
                    try:
                        ap["openclaw_agent_port"] = int(openclaw_agent_port.strip())
                        server.os.environ["OPENCLAW_AGENT_PORT"] = openclaw_agent_port.strip()
                    except ValueError:
                        pass
                if openclaw_agent_token is not None:
                    tok = openclaw_agent_token.strip()
                    if tok != server.MASKED_SECRET_PLACEHOLDER:
                        ap["openclaw_agent_token"] = tok
                        server.os.environ["OPENCLAW_AGENT_TOKEN"] = tok
                if openclaw_agent_model is not None:
                    m = openclaw_agent_model.strip()
                    if m:
                        ap["openclaw_agent_model"] = m
                        server.os.environ["OPENCLAW_AGENT_MODEL"] = m

                # ── Hermes Agent gateway settings ─────────────────────────────────
                if hermes_port is not None:
                    try:
                        ap["hermes_port"] = int(hermes_port.strip())
                        server.os.environ["HERMES_PORT"] = hermes_port.strip()
                    except ValueError:
                        pass
                if hermes_token is not None:
                    tok = hermes_token.strip()
                    if tok != server.MASKED_SECRET_PLACEHOLDER:
                        ap["hermes_token"] = tok
                        server.os.environ["HERMES_TOKEN"] = tok
                if hermes_model is not None:
                    m = hermes_model.strip()
                    if m:
                        ap["hermes_model"] = m
                        server.os.environ["HERMES_MODEL"] = m

                # ── LiteLLM cloud provider settings ──────────────────────────────
                if litellm_model is not None:
                    m = litellm_model.strip()
                    if m:
                        ap["litellm_model"] = m
                        server.os.environ["LITELLM_MODEL"] = m
                if litellm_api_key is not None:
                    k = litellm_api_key.strip()
                    if k and k != server.MASKED_SECRET_PLACEHOLDER:
                        ap["litellm_api_key"] = k
                        server.os.environ["LITELLM_API_KEY"] = k
                if litellm_api_base is not None:
                    b = litellm_api_base.strip()
                    ap["litellm_api_base"] = b
                    server.os.environ["LITELLM_API_BASE"] = b

                # ── Native Odysseus companion-service settings ─────────────────
                if odysseus_api_base is not None:
                    b = odysseus_api_base.strip()
                    if b:
                        ap["odysseus_api_base"] = b
                        server.os.environ["ODYSSEUS_API_BASE"] = b
                if odysseus_model is not None:
                    m = odysseus_model.strip()
                    ap["odysseus_model"] = m
                    server.os.environ["ODYSSEUS_MODEL"] = m
                if odysseus_token is not None:
                    tok = odysseus_token.strip()
                    if tok and tok != server.MASKED_SECRET_PLACEHOLDER:
                        ap["odysseus_token"] = tok
                        server.os.environ["ODYSSEUS_API_TOKEN"] = tok
            elif kind == 'speech':
                server._update_speech_config(
                    cfg,
                    speech_tts_provider=speech_tts_provider,
                    speech_tts_rate=speech_tts_rate,
                    speech_tts_system_voice=speech_tts_system_voice,
                    speech_openai_tts_model=speech_openai_tts_model,
                    speech_openai_tts_voice=speech_openai_tts_voice,
                    speech_openai_tts_instructions=speech_openai_tts_instructions,
                    speech_openai_base_url=speech_openai_base_url,
                    speech_openai_api_key=speech_openai_api_key,
                    speech_azure_key=speech_azure_key,
                    speech_azure_region=speech_azure_region,
                    speech_azure_voice=speech_azure_voice,
                    speech_azure_endpoint_id=speech_azure_endpoint_id,
                    speech_stt_model=speech_stt_model,
                    speech_stt_language=speech_stt_language,
                    speech_stt_device=speech_stt_device,
                    speech_stt_compute_type=speech_stt_compute_type,
                    speech_stt_silero_sensitivity=speech_stt_silero_sensitivity,
                    speech_stt_post_speech_silence_duration=speech_stt_post_speech_silence_duration,
                )
            elif kind == 'rtc' and rtc_json is not None:
                data = server.json.loads(rtc_json)
                if not isinstance(data, dict) or 'iceServers' not in data:
                    raise ValueError('Connection helper JSON must be an object with an iceServers list')
                cfg['rtc'] = data
            elif kind == 'server' and server_name is not None:
                name = server_name.strip()
                if not name:
                    raise ValueError('Server name cannot be empty')
                cfg.setdefault('server', {})['name'] = name
            elif kind == 'ai_agent':
                # Handle AI Agent Server configuration
                cfg.setdefault('ai_agent', {})

                # Handle enabled checkbox
                if ai_agent_enabled is not None:
                    cfg['ai_agent']['enabled'] = ai_agent_enabled.lower() in ['true', 'on', '1']
                else:
                    # Checkbox was unchecked, explicitly disable AI Agent Server
                    cfg['ai_agent']['enabled'] = False
                    server.LOGGER.info("AI Agent Server disabled via Admin Dashboard")

                # Handle auto-start checkbox
                if ai_agent_auto_start is not None:
                    cfg['ai_agent']['auto_start'] = ai_agent_auto_start.lower() in ['true', 'on', '1']
                else:
                    cfg['ai_agent']['auto_start'] = False

                # Handle record messages checkbox
                if ai_agent_record_messages is not None:
                    cfg['ai_agent']['record_messages_in_database'] = ai_agent_record_messages.lower() in ['true', 'on', '1']
                else:
                    cfg['ai_agent']['record_messages_in_database'] = False

                if ai_agent_memory_backend is not None:
                    memory_backend = str(ai_agent_memory_backend or "").strip().lower()
                    if memory_backend not in {"legacy", "cognee"}:
                        raise ValueError("Invalid memory backend")
                    cfg['ai_agent']['memory_backend'] = memory_backend

                # Handle port configuration
                if ai_agent_port is not None:
                    try:
                        port = int(ai_agent_port.strip())
                        if port < 1024 or port > 65535:
                            raise ValueError('Port must be between 1024 and 65535')
                        cfg['ai_agent']['port'] = port
                        # Update global variable for immediate effect
                        global AI_AGENT_SERVER_PORT
                        AI_AGENT_SERVER_PORT = port
                    except ValueError as e:
                        raise ValueError(f'Invalid AI Agent port number: {e}')

                # Always stop current AI Agent Server first to ensure clean state
                await server.stop_ai_agent_server()
            elif kind == 'tunnelmole':
                # Handle Tunnelmole configuration
                cfg.setdefault('tunnelmole', {})
                cfg['tunnelmole'].pop('port', None)
                cfg['tunnelmole'].pop('local_websocket_port', None)
                cfg['tunnelmole'].pop('device_name', None)
                cfg['tunnelmole'].pop('totp_pair_mode', None)

                # Handle enabled checkbox
                if tunnelmole_enabled is not None:
                    cfg['tunnelmole']['enabled'] = tunnelmole_enabled.lower() in ['true', 'on', '1']
                    server.LOGGER.info(f"Tunnelmole enabled set to: {cfg['tunnelmole']['enabled']} (from form value: {tunnelmole_enabled})")
                else:
                    # Checkbox was unchecked, explicitly disable Tunnelmole
                    cfg['tunnelmole']['enabled'] = False
                    server.LOGGER.info("Tunnelmole service disabled via Admin Dashboard (checkbox unchecked)")

                # Handle proxy timeout configuration
                if tunnelmole_timeout is not None:
                    try:
                        timeout = int(tunnelmole_timeout.strip())
                        if timeout < server._TUNNELMOLE_TIMEOUT_MINUTES_MIN or timeout > server._TUNNELMOLE_TIMEOUT_MINUTES_MAX:
                            raise ValueError(
                                f'Timeout must be between {server._TUNNELMOLE_TIMEOUT_MINUTES_MIN} and {server._TUNNELMOLE_TIMEOUT_MINUTES_MAX} minutes'
                            )
                        cfg['tunnelmole']['timeout_minutes'] = timeout
                    except ValueError as e:
                        raise ValueError(f'Invalid Tunnelmole proxy timeout: {e}')

                # Handle OTP timeout configuration
                if tunnelmole_otp_timeout is not None:
                    try:
                        otp_timeout = int(tunnelmole_otp_timeout.strip())
                        if otp_timeout < server._TUNNELMOLE_TIMEOUT_MINUTES_MIN or otp_timeout > server._TUNNELMOLE_TIMEOUT_MINUTES_MAX:
                            raise ValueError(
                                f'OTP timeout must be between {server._TUNNELMOLE_TIMEOUT_MINUTES_MIN} and {server._TUNNELMOLE_TIMEOUT_MINUTES_MAX} minutes'
                            )
                        cfg['tunnelmole']['otp_timeout_minutes'] = otp_timeout
                    except ValueError as e:
                        raise ValueError(f'Invalid Tunnelmole OTP timeout: {e}')

                # Handle OTP multi-use toggle (checkbox - absent when unchecked)
                cfg['tunnelmole']['otp_multiuse'] = tunnelmole_otp_multiuse is not None and \
                    tunnelmole_otp_multiuse.lower() in ('true', 'on', '1')

                cfg['tunnelmole']['pair_code_mode'] = server._normalize_tunnelmole_pair_code_mode(
                    tunnelmole_pair_code_mode
                )
                cfg['tunnelmole']['connection_mode'] = server._normalize_tunnelmole_connection_mode(
                    tunnelmole_connection_mode
                )

                # Always stop current Tunnelmole service first to ensure clean state
                await server.stop_tunnelmole_service()
            elif kind == 'autoyou_page':
                # Handle AutoYou Page configuration
                cfg.setdefault('autoyou_page', {})

                # Port configuration
                if autoyou_page_port is not None:
                    try:
                        ap_port = int(autoyou_page_port.strip())
                        if ap_port < 1024 or ap_port > 65535:
                            raise ValueError('Port must be between 1024 and 65535')
                        cfg['autoyou_page']['port'] = ap_port
                    except ValueError as e:
                        raise ValueError(f'Invalid AutoYou Page port: {e}')

                # Auto-start checkbox
                if autoyou_page_auto_start is not None:
                    cfg['autoyou_page']['auto_start'] = autoyou_page_auto_start.lower() in ['true', 'on', '1']
                else:
                    cfg['autoyou_page']['auto_start'] = False

                # Timeline days setting
                if autoyou_page_timeline_days is not None:
                    try:
                        tl_days = int(autoyou_page_timeline_days.strip())
                        if tl_days < 0 or tl_days > 3650:
                            raise ValueError('Timeline days must be between 0 and 3650 (0 = entire timeline)')
                        cfg['autoyou_page']['timeline_days'] = tl_days
                    except ValueError as e:
                        raise ValueError(f'Invalid timeline days: {e}')

                if autoyou_page_theme is not None:
                    cfg['autoyou_page']['theme'] = server.normalize_ui_theme(autoyou_page_theme, default='dark')

                # Custom forwarding enable
                if autoyou_custom_forward_enabled is not None:
                    cfg['autoyou_page']['custom_forward_enabled'] = autoyou_custom_forward_enabled.lower() in ['true', 'on', '1']
                else:
                    cfg['autoyou_page']['custom_forward_enabled'] = False

                # Custom forwarding port
                if autoyou_custom_forward_port is not None:
                    try:
                        cf_port = int(autoyou_custom_forward_port.strip())
                        if cf_port < 1024 or cf_port > 65535:
                            raise ValueError('Port must be between 1024 and 65535')
                        cfg['autoyou_page']['custom_forward_port'] = cf_port
                    except ValueError as e:
                        raise ValueError(f'Invalid Custom Forward port: {e}')

                if autoyou_advertised_websites is not None:
                    cfg['autoyou_page']['advertised_websites'] = server._normalize_advertised_website_entries(
                        autoyou_advertised_websites,
                        reserved_ports=server._get_reserved_browser_port_routes(cfg),
                    )

                if autoyou_bookmarks is not None:
                    cfg['autoyou_page']['bookmarks'] = server._normalize_bookmark_entries(autoyou_bookmarks)
                # Graceful handling: if custom forward enabled, we'll stop after save;
                # if changing port to a busy one, skip restart and keep prior port.
                try:
                    autoyou_forward_enabled_flag = cfg.get('autoyou_page', {}).get('custom_forward_enabled', False)
                    desired_forward_port = cfg.get('autoyou_page', {}).get('custom_forward_port', 8067)
                    autoyou_page_port = cfg.get("autoyou_page", {}).get('port', 8067)

                    # If forwarding is disabled and desired port is busy by someone else, flag conflict if unused
                    if autoyou_forward_enabled_flag and desired_forward_port != autoyou_page_port:
                        if await server.asyncio.to_thread(server.is_port_in_use, desired_forward_port, "127.0.0.1"):
                            server.LOGGER.warning(f"AutoYou Page config save detected external port forward to {desired_forward_port}")
                        else:
                            server.LOGGER.warning(f"AutoYou Page config save detected external port forward to {desired_forward_port}; but it was not in use, so keeping previous port {autoyou_page_port}")
                            cfg['autoyou_page']['custom_forward_port'] = autoyou_page_port

                except Exception:
                    # If any error during conflict detection, proceed without aggressive stop
                    pass
                try:
                    server._persist_autoyou_ui_theme(cfg.get('autoyou_page', {}).get('theme', 'dark'), cfg=cfg, persist_config=False)
                except Exception as exc:
                    server.LOGGER.warning("Failed to persist shared AutoYou theme preference: %s", exc)
            elif kind == 'admin_frontend':
                # Handle Admin Frontend Proxy configuration
                cfg = server._set_agent_frontend_enabled(
                    "admin_agent",
                    admin_frontend_proxy_enabled is not None,
                    cfg=cfg,
                )
            else:
                raise ValueError('Invalid form submission')
            # Persist to the active config store
            server._persist_state_config(cfg)
            if kind == 'ai_agent' and not server._client_name_history_enabled(cfg):
                server.WEBRTC.clear_client_name_overrides()

            # Update service manager configuration if it exists and AI agent config changed
            if kind == 'ai_agent' and server.STATE.service_manager is not None:
                from service_manager import update_service_config, ServiceConfig
                ai_agent_config = cfg.get("ai_agent", {})
                ai_agent_record_messages = ai_agent_config.get("record_messages_in_database", True)
                ai_agent_port = ai_agent_config.get("port", 8081)

                service_config = ServiceConfig(
                    db_path=str(server._resolve_ai_agent_memory_db_path()),
                    ai_agent_server_port=ai_agent_port,
                    record_messages=ai_agent_record_messages,
                    internet_search_enabled=bool(ai_agent_config.get("internet_search_enabled", True)),
                    audio_playback_enabled=server._get_audio_playback_enabled(cfg=cfg),
                    memory_backend=ai_agent_config.get("memory_backend", "legacy"),
                    adk_db_path=server._resolve_ai_agent_storage_uris().get("session_service_uri"),
                )
                update_service_config(service_config)
                server.LOGGER.info(f"Service manager configuration updated with memory enabled: {ai_agent_record_messages}")

            # Attempt to (re)start services with new settings
            if kind == 'telegram':
                await server.start_or_restart_telegram()
            elif kind == 'signal':
                await server.start_or_restart_signal()
            elif kind == 'whatsapp':
                await server.start_or_restart_whatsapp()
            elif kind == 'ollama':
                # Apply Google API configuration to environment variables
                # (Environment variables are already set above, but ensure consistency)
                server._apply_google_api_config_to_env()
                server.LOGGER.info("Ollama/Google API configuration updated and applied to environment variables")

                # Native gateways own their sessions and must not wake the ADK worker.
                if server._is_native_gateway_provider():
                    await server.stop_ai_agent_server()
                    if str(cfg.get("ai_provider", {}).get("provider") or "").lower() == "ollama_gateway":
                        await server.asyncio.to_thread(server._ensure_local_ollama_runtime_ready)
                elif cfg.get("ai_agent", {}).get("enabled", True):
                    server.LOGGER.info("Restarting AI Agent Server to apply new Ollama/Google API configuration")
                    await server.restart_ai_agent_server()
            elif kind == 'speech':
                server._apply_speech_config_to_active_audio_managers()
            elif kind == 'ai_agent':
                # Start AI Agent Server if enabled and auto-start is on
                if cfg.get("ai_agent", {}).get("enabled", True) and cfg.get("ai_agent", {}).get("auto_start", True):
                    await server.start_ai_agent_server_background()
            elif kind == 'autoyou_page':
                # Apply AutoYou Page config changes via unified helper (decoupled from forwarding)
                try:
                    await server.start_or_restart_autoyou_page_service()
                except Exception:
                    pass
            elif kind == 'admin_frontend':
                server._register_admin_frontend_proxy()
            # Redirect with success indicator banner (use mapping for clarity and correctness)
            suffix_map = {
                'telegram': 'telegram',
                'signal': 'signal',
                'whatsapp': 'whatsapp',
                'ollama': 'ollama',
                'rtc': 'rtc',
                'server': 'server',
                'ai_agent': 'ai_agent',
                'tunnelmole': 'tunnelmole',
                'autoyou_page': 'autoyou_page',
                'speech': 'speech',
                'admin_frontend': 'admin_frontend',
            }
            suffix = suffix_map.get(kind, '')
            return RedirectResponse(url=f"/?saved={suffix}", status_code=302)
        except Exception as e:
            return server._html_page(f"""
            <div class='card'>
              <h2>Save failed</h2>
              <pre class='mono'>{str(e)}</pre>
              <a href='/'>Back</a>
            </div>
            """)

    @admin_app.get("/admin/api/ui/theme")
    async def admin_ui_theme_get_endpoint() -> JSONResponse:
        theme = server._read_autoyou_ui_theme()
        return JSONResponse({"success": True, "theme": theme, "themes": ["dark", "light"]})

    @admin_app.post("/admin/api/ui/theme")
    async def admin_ui_theme_post_endpoint(request: Request) -> JSONResponse:
        # The GET stays public so the pre-session login page can render in the
        # operator's theme, but this writes persisted configuration and must
        # not be reachable without a session - every other config write on this
        # app requires one.
        auth_response = server._require_api_login_json(request)
        if auth_response is not None:
            return auth_response
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        requested_theme = server.normalize_ui_theme((payload or {}).get("theme"), default="dark")
        try:
            persisted_theme = server._persist_autoyou_ui_theme(
                requested_theme,
                cfg=(server.STATE.config if server._can_persist_config() else None),
                persist_config=server._can_persist_config(),
            )
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
        return JSONResponse({"success": True, "theme": persisted_theme})

    @admin_app.get("/ollama-status")
    async def ollama_status_endpoint(request: Request):
        """Endpoint to get current Ollama and Google API status for live updates."""
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"error": "Not authenticated"}, status_code=401)

        try:
            # Get current status
            ollama_status, ollama_status_color, ollama_api_base, ollama_models_count, ollama_selected_model = await server._ollama_status()
            google_status, google_status_color, use_google_api, google_model, google_api_key_status = await server._google_api_status()
            ai_provider_summary = await server._ai_provider_summary()

            return JSONResponse({
                "ollama": {
                    "status": ollama_status,
                    "status_color": ollama_status_color,
                    "api_base": ollama_api_base,
                    "models_count": ollama_models_count,
                    "selected_model": ollama_selected_model
                },
                "google": {
                    "status": google_status,
                    "status_color": google_status_color,
                    "use_google_api": use_google_api,
                    "model": google_model,
                    "api_key_status": google_api_key_status
                },
                "ai_provider_summary": ai_provider_summary
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @admin_app.get("/api/datachannel-status")
    async def datachannel_status(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        try:
            managers = getattr(server.WEBRTC, "datachannel_managers", {})
            if hasattr(server.WEBRTC, "_unique_datachannel_manager_entries"):
                manager_entries = server.WEBRTC._unique_datachannel_manager_entries()
            else:
                manager_entries = list(managers.items())
            last_pings: Dict[str, float] = {}
            connections: List[Dict[str, Any]] = []
            for index, (sid, mgr) in enumerate(manager_entries, start=1):
                try:
                    last_ping = float(getattr(mgr, "last_ping_time", 0.0))
                except Exception:
                    last_ping = 0.0
                last_pings[str(sid)] = last_ping
                connection = {
                    "label": f"Client {index}",
                    "session_id": str(sid),
                    "last_ping_timestamp": last_ping,
                    "connected": True,
                }
                describe = getattr(server.WEBRTC, "describe_connected_device", None)
                if callable(describe):
                    try:
                        connection.update(describe(sid))
                    except Exception as exc:
                        server.LOGGER.debug("Could not describe connected client %s: %s", index, exc)
                connections.append(connection)
            recovery_attempts = sum(int(getattr(mgr, "recovery_attempts", 0)) for _, mgr in manager_entries)
            successful_recoveries = sum(int(getattr(mgr, "successful_recoveries", 0)) for _, mgr in manager_entries)
            return JSONResponse({
                "active_sessions": len(manager_entries),
                "connected_clients": len(manager_entries),
                "connections": connections,
                "last_ping_timestamps": last_pings,
                "recovery_attempts": recovery_attempts,
                "successful_recoveries": successful_recoveries,
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @admin_app.get("/api/scheduler/notification-queue")
    async def scheduler_notification_queue_status(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            from shared import scheduler_service as _scheduler_svc

            queue_snapshot = _scheduler_svc.get_pending_notification_queue_snapshot(limit=6)
            return JSONResponse({"success": True, "queue": queue_snapshot})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @admin_app.get("/api/scheduler/live-summary")
    async def scheduler_live_summary_status(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            from autoyou_agents.shared_tools import scheduler_mission_control as _mission_control

            return JSONResponse(_mission_control.build_tasks_live_summary(queue_limit=6))
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    return {
        "simulate_pair_command": simulate_pair_command,
        "admin_api_status": admin_api_status,
        "admin_ui_bootstrap": admin_ui_bootstrap,
        "admin_setup_recipe_preview": admin_setup_recipe_preview,
        "admin_connection_status": admin_connection_status,
        "admin_ui_update_config": admin_ui_update_config,
        "admin_setup_recipe_apply": admin_setup_recipe_apply,
        "admin_ui_update_security_mode": admin_ui_update_security_mode,
        "admin_ui_update_security_tier": admin_ui_update_security_tier,
        "native_security_totp_show": native_security_totp_show,
        "native_security_totp_current_code": native_security_totp_current_code,
        "native_security_totp_import": native_security_totp_import,
        "native_security_totp_delete": native_security_totp_delete,
        "admin_ui_rotate_storage_key": admin_ui_rotate_storage_key,
        "admin_ui_change_password": admin_ui_change_password,
        "admin_ui_generate_password": admin_ui_generate_password,
        "admin_login_startup_status": admin_login_startup_status,
        "admin_login_minigame_high_score": admin_login_minigame_high_score,
        "admin_login_minigame_save_high_score": admin_login_minigame_save_high_score,
        "admin_login_minigame_reset_high_score": admin_login_minigame_reset_high_score,
        "admin_get_internet_search_enabled": admin_get_internet_search_enabled,
        "admin_set_internet_search_enabled": admin_set_internet_search_enabled,
        "admin_ui_get_profile_image": admin_ui_get_profile_image,
        "admin_ui_upload_profile_image": admin_ui_upload_profile_image,
        "admin_ui_delete_profile_image": admin_ui_delete_profile_image,
        "save_config_endpoint": save_config_endpoint,
        "admin_ui_theme_get_endpoint": admin_ui_theme_get_endpoint,
        "admin_ui_theme_post_endpoint": admin_ui_theme_post_endpoint,
        "ollama_status_endpoint": ollama_status_endpoint,
        "datachannel_status": datachannel_status,
        "scheduler_notification_queue_status": scheduler_notification_queue_status,
        "scheduler_live_summary_status": scheduler_live_summary_status
    }
