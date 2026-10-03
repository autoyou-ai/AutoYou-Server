# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-849b6aaf9452f2ea0d150ba2

"""Pairing HTTP routes for the full AutoYou server."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from typing import Any, Callable, Dict, Optional
import asyncio
import base64
import binascii

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-849b6aaf9452f2ea0d150ba2"


def register_routes(
    admin_app: FastAPI,
    auth_app: FastAPI,
    server: Any,
) -> Dict[str, Callable[..., Any]]:
    @admin_app.get("/v1/unlock/status")
    @auth_app.get("/v1/unlock/status")
    async def get_unlock_status():
        state = server._get_unlock_state()
        meta = server._load_unlock_metadata()
        agreement_pending_unlock = bool(
            state == "Locked"
            and server.is_license_acknowledgement_pending_unlock(anchor=server.__file__)
        )
        agreement_required = bool(
            state == "SetupPending"
            or (
                not agreement_pending_unlock
                and not server._agreement_metadata_is_current(meta)
            )
        )
        return {
            "state": state,
            "terms_accepted": bool(meta.get("terms_accepted", False)) and not agreement_required,
            "license_type": meta.get("license_type", None),
            "agreement_version": meta.get("agreement_version", None),
            "current_agreement_version": server.CURRENT_AGREEMENT_VERSION,
            "agreement_required": agreement_required,
            "agreement_pending_unlock": agreement_pending_unlock,
        }

    @admin_app.post("/v1/unlock/setup")
    @auth_app.post("/v1/unlock/setup")
    async def post_unlock_setup(request: Request):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON payload"})

        password = str(payload.get("password") or "").strip()
        license_type = str(payload.get("license_type") or "").strip()
        terms_accepted = bool(payload.get("terms_accepted"))
        signtoross_token = str(payload.get("signtoross_token") or "").strip()

        if server._get_unlock_state() != "SetupPending":
            return JSONResponse(status_code=400, content={"success": False, "error": "Setup is already completed. Use /v1/unlock/verify instead."})

        if not password:
            return JSONResponse(status_code=400, content={"success": False, "error": "Password is required"})

        if not terms_accepted:
            return JSONResponse(status_code=400, content={"success": False, "error": f"You must accept the current AutoYou {server._CURRENT_TERMS_ACCEPTANCE_LABEL} to proceed."})

        if license_type not in ["individual", "commercial"]:
            return JSONResponse(status_code=400, content={"success": False, "error": "license_type must be either 'individual' or 'commercial'"})

        salt_hex, hash_hex = server._hash_password(password)

        import datetime
        unlock_data = {
            "salt_hex": salt_hex,
            "hash_hex": hash_hex,
            "terms_accepted": True,
            "license_type": license_type,
            "agreement_version": server.CURRENT_AGREEMENT_VERSION,
            "accepted_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "signtoross_token": signtoross_token
        }

        try:
            path = server._get_unlock_file_path()
            server._safe_write_unlock_json(path, unlock_data)
            server.record_license_acknowledgement(anchor=server.__file__, accepted_by="server_unlock_setup")
        except Exception as e:
            server.LOGGER.error("Failed to write first-run unlock agreement: %s", e)
            return JSONResponse(status_code=500, content={"success": False, "error": f"Failed to save unlock agreement: {str(e)}"})

        try:
            ks = server._get_server_keystore()
            keystore_available = ks is not None and ks.is_available()
            preferred_store = server.CONFIG_STORE_KEYSTORE if keystore_available else server.CONFIG_STORE_ENCRYPTED

            if not server.STATE.config:
                server.STATE.config = server._build_initial_server_config()

            server._persist_state_config(server.STATE.config, server_password=password, preferred_store=preferred_store)
            server._set_config_session(
                config_store=preferred_store,
                server_password=password,
                config_unlock_password=password
            )
        except Exception as e:
            server.LOGGER.error("Failed to re-encrypt config on setup: %s", e)

        server.STATE._unlock_state_mem = "Ready"

        # Initialize background services
        server.asyncio.create_task(server._initialize_services_on_startup())

        return {
            "success": True,
            "state": "Ready",
            "terms_accepted": True,
            "license_type": license_type,
            "agreement_version": server.CURRENT_AGREEMENT_VERSION
        }

    @admin_app.post("/v1/unlock/verify")
    @auth_app.post("/v1/unlock/verify")
    async def post_unlock_verify(request: Request):
        transport_error = server._require_loopback_or_https_request(request)
        if transport_error:
            return transport_error
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON payload"})

        password = str(payload.get("password") or "").strip()

        state = server._get_unlock_state()
        if state == "SetupPending":
            return JSONResponse(status_code=400, content={"success": False, "error": "Setup is pending. Use /v1/unlock/setup instead."})

        meta = server._load_unlock_metadata()
        salt_hex = meta.get("salt_hex")
        hash_hex = meta.get("hash_hex")
        agreement_pending_unlock = server.is_license_acknowledgement_pending_unlock(
            anchor=server.__file__
        )
        agreement_required = bool(
            not agreement_pending_unlock
            and not server._agreement_metadata_is_current(meta)
        )

        # Legacy /login installs may not have unlock hash metadata. Resolve the
        # password against the same keystore-or-file config path used by /login,
        # then self-heal the metadata below.
        legacy_unlock_migration_needed = not salt_hex or not hash_hex
        precomputed_cfg = None
        resolved_store = server.CONFIG_STORE_NONE
        if legacy_unlock_migration_needed:
            precomputed_cfg, resolved_store = server._resolve_config_for_password(password)
            if not precomputed_cfg:
                return JSONResponse(status_code=401, content={"success": False, "error": "Incorrect password"})
        elif not server._verify_password(password, salt_hex, hash_hex):
            return JSONResponse(status_code=401, content={"success": False, "error": "Incorrect password"})

        if agreement_required and not bool(payload.get("terms_accepted")):
            return JSONResponse(
                status_code=428,
                content={
                    "success": False,
                    "error": "You must accept the current AutoYou terms before unlocking.",
                    "agreement_required": True,
                    "current_agreement_version": server.CURRENT_AGREEMENT_VERSION,
                },
            )

        if not legacy_unlock_migration_needed:
            precomputed_cfg, resolved_store = server._resolve_config_for_password(password)
            if precomputed_cfg is None:
                return JSONResponse(
                    status_code=503,
                    content={"success": False, "error": "Saved configuration could not be loaded."},
                )

        try:
            server.STATE.config = precomputed_cfg
            server._configure_secure_storage_for_config(precomputed_cfg, password=password)
        except server.SecureStorageError as exc:
            server.LOGGER.error("Failed to initialize secure storage on verify: %s", exc)
            if server.secure_storage_enabled():
                try:
                    server.disable_secure_storage()
                except Exception as cleanup_exc:  # pragma: no cover - defensive cleanup.
                    server.LOGGER.warning("Failed to reset secure storage after unlock failure: %s", cleanup_exc)
            server.STATE.config = {}
            server._set_config_session(config_store=server.CONFIG_STORE_NONE)
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "error": f"Secure Professional Maximus storage could not be initialized: {exc}",
                },
            )

        if agreement_pending_unlock:
            meta = server._load_unlock_metadata()
            agreement_required = not server._agreement_metadata_is_current(meta)
            if agreement_required and not bool(payload.get("terms_accepted")):
                return JSONResponse(
                    status_code=428,
                    content={
                        "success": False,
                        "error": "You must accept the current AutoYou terms before unlocking.",
                        "agreement_required": True,
                        "current_agreement_version": server.CURRENT_AGREEMENT_VERSION,
                    },
                )

        try:
            meta = server._sync_unlock_metadata(password, accepted_by="server_unlock_verify")
        except Exception as exc:
            server.LOGGER.error("Failed to update unlock metadata: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": "Failed to save unlock configuration."})

        try:
            server._set_config_session(
                config_store=resolved_store,
                server_password=password,
                config_unlock_password=password,
            )
            if server._keystore_server_password_available():
                server._persist_server_password(password)
        except Exception as e:
            server.LOGGER.error("Failed to load config on verify: %s", e)
            if server._get_security_mode_from_cfg(server.STATE.config) == server.SECURE_PROFESSIONAL_MAXIMUS_MODE:
                return JSONResponse(
                    status_code=500,
                    content={
                        "success": False,
                        "error": "Secure Professional Maximus storage could not be initialized.",
                    },
                )

        server.STATE._unlock_state_mem = "Ready"

        # Initialize background services
        server.asyncio.create_task(server._initialize_services_on_startup())

        return {
            "success": True,
            "state": "Ready",
            "terms_accepted": True,
            "license_type": meta.get("license_type"),
            "agreement_version": meta.get("agreement_version"),
            "current_agreement_version": server.CURRENT_AGREEMENT_VERSION,
            "agreement_required": False,
        }

    @auth_app.get("/health")
    async def auth_health():
        return {
            "status": "running",
            "service": "AutoYou Auth Server",
            "runtime": server.build_runtime_environment_status(),
        }

    @auth_app.post("/auth")
    async def auth_endpoint(request: Request):
        """Authenticate OTP hash and issue a session for /signal exchange."""
        # H-2: key the rate limiter on the real tunnelmole-edge client IP
        # when we're behind the local bridge; otherwise on the direct peer.
        client_ip = server._rate_limit_client_key(request)
        if not server.AUTH_GLOBAL_RATE_LIMITER.is_allowed("global"):
            server.LOGGER.warning("Global rate limit exceeded for /auth")
            return JSONResponse(
                status_code=429,
                content={"success": False, "error": "Rate limit exceeded. Try again later."},
            )
        if not server.AUTH_RATE_LIMITER.is_allowed(client_ip):
            server.LOGGER.warning("Rate limit exceeded for /auth from %s", client_ip)
            return JSONResponse(
                status_code=429,
                content={"success": False, "error": "Rate limit exceeded. Try again later."},
            )

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        auth_result = await server.handle_auth_request(payload if isinstance(payload, dict) else {})
        if not auth_result.get("success"):
            return JSONResponse(status_code=401, content=auth_result)

        session_id = auth_result.get("session_id")
        server_id = server._get_stable_server_id()
        server_identity_key = server._get_server_identity_key()
        return {
            "success": True,
            "session_id": session_id,  # Android compatibility
            "sessionId": session_id,   # tunnelmole/python compatibility
            "iceServers": await server._get_pairing_ice_servers_async(),
            "server_name": server.get_configured_server_name(),
            "serverName": server.get_configured_server_name(),
            "server_id": server_id,
            "serverId": server_id,
            "server_identity_key": server_identity_key,
            "serverIdentityKey": server_identity_key,
            "session": auth_result.get("session") or {},
        }

    @auth_app.post("/signal/{session_id}")
    async def signal_endpoint(session_id: str, request: Request):
        """Handle offer/candidate signaling for OTP pairing flow."""
        try:
            signal_data = await request.json()
        except Exception:
            signal_data = {}

        signal_type = (signal_data.get("type") if isinstance(signal_data, dict) else None) or "offer"
        signal_type = str(signal_type).strip().lower() or "offer"
        authenticated_session = server._is_authenticated_pair_session(session_id)
        limiter = server._select_signal_rate_limiter(signal_type, authenticated_session=authenticated_session)
        rate_limit_bucket, client_key = server._signal_rate_limit_bucket(
            request,
            session_id=session_id,
            signal_type=signal_type,
            authenticated_session=authenticated_session,
        )
        if not limiter.is_allowed(rate_limit_bucket):
            server.LOGGER.warning(
                "Rate limit exceeded for signaling type=%s session=%s from %s",
                signal_type,
                session_id,
                client_key,
            )
            return JSONResponse(
                status_code=429,
                content={"success": False, "error": "Rate limit exceeded. Try again later."},
            )

        if not authenticated_session:
            return JSONResponse(status_code=401, content={"success": False, "error": "Session not authenticated"})

        try:
            if signal_type == "offer":
                # Clear stale queued candidates from any previous attempt for this session.
                server.WEBRTC.pop_outgoing_trickle_candidates(session_id)
                answer = await server.WEBRTC.handle_session_offer(session_id, signal_data)
                return answer

            if signal_type == "candidate":
                candidate_payload = signal_data
                if isinstance(signal_data, dict) and signal_data.get("candidate") is None and signal_data.get("sdp"):
                    # Accept {"type":"candidate","sdp":"candidate:..."} style inputs.
                    candidate_payload = {
                        "candidate": signal_data.get("sdp"),
                        "sdpMid": signal_data.get("sdpMid"),
                        "sdpMLineIndex": signal_data.get("sdpMLineIndex"),
                    }
                success = await server.WEBRTC.handle_session_candidate(session_id, candidate_payload)
                if success:
                    return {"success": True, "message": "ICE candidate processed"}
                return JSONResponse(status_code=400, content={"success": False, "error": "Failed to process ICE candidate"})

            return JSONResponse(status_code=400, content={"success": False, "error": f"Unsupported signal type: {signal_type}"})
        except Exception as e:
            server.LOGGER.error(f"Signaling error for session {session_id}: {e}")
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @auth_app.get("/signal/{session_id}")
    async def signal_poll_endpoint(session_id: str):
        """Compatibility polling endpoint for trickle ICE clients."""
        if not server._is_authenticated_pair_session(session_id):
            return JSONResponse(status_code=401, content={"success": False, "error": "Session not authenticated"})
        messages = server.WEBRTC.pop_outgoing_trickle_candidates(session_id)
        return {"success": True, "messages": messages}

    @admin_app.get("/api/local-pair-info")
    async def local_pair_info(request: Request):
        """LAN connect details for the admin Home / Live View Local Pair cards.

        Returns the host's private IPv4 addresses plus the admin port so a phone
        on the same network can use Local Pair without guessing the address. No
        discovery traffic is emitted (see shared/local_network_info.py).
        """
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        try:
            from shared.local_network_info import build_local_pair_info

            info = build_local_pair_info(
                port=int(server.ADMIN_WEB_SERVICE_PORT),
                bind_host=server.SERVER_BIND_HOST,
                server_name=server.get_configured_server_name(),
            )
            info["success"] = True
            return JSONResponse(info)
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @admin_app.get("/api/bluetooth-pairing/status")
    async def bluetooth_pairing_status_endpoint(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        return server._json_response_no_store(server._bluetooth_pairing_runtime_status())

    def live_pair_response(result, raw=False):
        from shared.live_pairing import qr_frames, wire_text
        text = (result.get("message", "") if raw else wire_text(result)) if result else ""
        images = [server._build_local_qr_data_url(frame) for frame in qr_frames(text)] if text else []
        return JSONResponse({"result": result, "text": text, "qr_frames": images}, headers={"Cache-Control": "no-store"})

    @admin_app.get("/api/live-pair/status")
    async def live_pair_status(request: Request):
        if server._require_login(request):
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        return await asyncio.to_thread(live_pair_response, server.WEBRTC.live_pairing.status())

    @admin_app.post("/api/live-pair/scan")
    async def live_pair_scan(request: Request):
        if server._require_login(request):
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        try:
            if len(await request.body()) > 3000000:
                raise ValueError("Camera frame is too large")
            payload = await request.json()
            source = payload.get("image", "") if isinstance(payload, dict) else ""
            if not isinstance(source, str) or not source.startswith("data:image/jpeg;base64,"):
                raise ValueError("Invalid camera frame")
            data = base64.b64decode(source.split(",", 1)[1], validate=True)
            if len(data) > 2000000:
                raise ValueError("Camera frame is too large")
            def decode():
                import cv2
                import numpy as np
                frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None or max(frame.shape[:2]) > 1920:
                    raise ValueError("Invalid camera frame")
                text, _, _ = cv2.QRCodeDetector().detectAndDecode(frame)
                return text
            return JSONResponse({"text": await asyncio.to_thread(decode)}, headers={"Cache-Control": "no-store"})
        except (ValueError, TypeError, binascii.Error):
            return JSONResponse({"error": "Could not read the camera frame. Use Paste."}, status_code=400)
        except ImportError:
            return JSONResponse({"error": "Camera scanning is unavailable. Use Paste."}, status_code=503)

    @admin_app.post("/api/live-pair")
    async def live_pair_api(request: Request):
        if server._require_login(request):
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        try:
            if len(await request.body()) > 70000:
                raise ValueError("Pairing message is too large")
            server._configure_pairing_router_helpers()
            payload = await request.json()
            result = await server.WEBRTC.live_pair_action("admin-liveqr", payload)
            return await asyncio.to_thread(live_pair_response, result, payload.get("raw_response") is True)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400,
                                headers={"Cache-Control": "no-store"})

    @admin_app.post("/api/autopair")
    async def autopair_api(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        rate_limit_bucket, rate_limit_client_key = server._autopair_rate_limit_bucket(request)
        if not server.AUTOPAIR_RATE_LIMITER.is_allowed(rate_limit_bucket):
            server.LOGGER.warning("AutoPair rate limit exceeded from %s", rate_limit_client_key)
            return JSONResponse({"error": "Too many pairing attempts. Please wait a minute before trying again."}, status_code=429)
        import json as _json
        try:
            current_password = server.get_current_password()
            if not current_password:
                return JSONResponse({"error": "Server password not configured"}, status_code=500)
            expected_hash = server._server_generate_hash(current_password)
            mode = server.get_security_mode()
            header_platform = str(request.headers.get("X-AutoYou-Platform") or "").strip().lower()
            if server._is_bluetooth_pair_platform(header_platform) and not server._is_bluetooth_pairing_enabled():
                return server._bluetooth_pairing_disabled_response()
            raw_body = (await request.body()).decode("utf-8", errors="replace")

            if mode == "secure" or server._is_secure_professional_mode(mode):
                # Security-mode aware path. The offer arrives encrypted; decrypt and
                # re-encrypt the answer through the same pairing_router helpers the
                # chat AutoPair path uses, so Local Pair is identical to AutoPair in
                # secure modes. Plaintext offers are rejected by _parse_autopair_payload
                # when the server is configured for a secure mode.
                if server.pairing_router is None:
                    return JSONResponse({"error": "Pairing subsystem not ready"}, status_code=503)
                try:
                    server._configure_pairing_router_helpers()
                except Exception as exc:
                    server.LOGGER.warning("Failed to configure pairing router for local pair: %s", exc)
                parse_sender_id = str(
                    request.query_params.get("session_id")
                    or request.headers.get("X-AutoYou-Session-Id")
                    or "admin-web"
                ).strip() or "admin-web"
                parse_platform = header_platform or ("local" if parse_sender_id != "admin-web" else "admin-web")
                parsed = await server.pairing_router._parse_autopair_payload(
                    raw_body,
                    platform=parse_platform,
                    sender_id=parse_sender_id,
                    identity_sender_id=parse_sender_id,
                )
                if isinstance(parsed, str):
                    return JSONResponse({"error": parsed}, status_code=400)
                payload, session = parsed
                if not isinstance(payload, dict):
                    return JSONResponse({"error": "Invalid payload"}, status_code=400)
                payload_platform = str(payload.get("_autoyou_pairing_platform") or "").strip().lower()
                if server._is_bluetooth_pair_platform(payload_platform) and not server._is_bluetooth_pairing_enabled():
                    return server._bluetooth_pairing_disabled_response()
                if not server.secrets.compare_digest(str(payload.get("hash") or ""), expected_hash):
                    return JSONResponse({"error": "Authentication failed"}, status_code=401)
                client_platform, client_sender_id = server._resolve_local_pair_client_identity(request, payload)
                payload["_autoyou_pairing_platform"] = client_platform
                payload["_autoyou_sender_id"] = client_sender_id
                payload["_autoyou_pairing_mode"] = "totp_pair" if server._is_secure_professional_mode(mode) else "secure_pair"
                if server._is_loopback_client_host(getattr(getattr(request, "client", None), "host", None)):
                    payload["_autoyou_loopback_pairing"] = True
                payload["_autoyou_same_machine_audio"] = server._is_same_machine_audio_client(request)
                payload["_autoyou_device_ownership"] = server._local_pair_device_ownership(request)
                answer = await server.WEBRTC.handle_autopair_offer(client_sender_id, payload)
                answer_text = await server.pairing_router._format_autopair_answer(
                    answer,
                    platform=client_platform,
                    sender_id=client_sender_id,
                    session=session,
                )
                return PlainTextResponse(answer_text, status_code=200)

            # Normal mode (legacy/plaintext): plaintext JSON offer. Unchanged
            # behavior for the admin web UI and existing automation/test callers.
            try:
                payload = _json.loads(raw_body)
            except Exception:
                return JSONResponse({"error": "Invalid payload"}, status_code=400)
            if not isinstance(payload, dict):
                return JSONResponse({"error": "Invalid payload"}, status_code=400)
            payload_platform = str(payload.get("_autoyou_pairing_platform") or "").strip().lower()
            if server._is_bluetooth_pair_platform(payload_platform) and not server._is_bluetooth_pairing_enabled():
                return server._bluetooth_pairing_disabled_response()
            if not server.secrets.compare_digest(str(payload.get("hash") or ""), expected_hash):
                return JSONResponse({"error": "Authentication failed"}, status_code=401)
            client_platform, client_sender_id = server._resolve_local_pair_client_identity(request, payload)
            payload["_autoyou_pairing_platform"] = client_platform
            payload["_autoyou_sender_id"] = client_sender_id
            if server._is_loopback_client_host(getattr(getattr(request, "client", None), "host", None)):
                payload["_autoyou_loopback_pairing"] = True
            payload["_autoyou_same_machine_audio"] = server._is_same_machine_audio_client(request)
            payload["_autoyou_device_ownership"] = server._local_pair_device_ownership(request)
            if server.pairing_router is None:
                return JSONResponse({"error": "Pairing subsystem not ready"}, status_code=503)
            # Delegate to WebRTC manager
            answer = await server.WEBRTC.handle_autopair_offer(client_sender_id, payload)
            wrapped_answer = server.pairing_router._build_autopair_answer_payload(answer, client_sender_id)
            answer_text = "/autopair_answer\n" + _json.dumps(wrapped_answer, separators=(",", ":"))
            return PlainTextResponse(answer_text, status_code=200)
        except Exception as e:
            import traceback
            error_payload = ("\n--- ERROR ---\n" + traceback.format_exc()).encode("utf-8", errors="replace")
            try:
                error_log_path = server.get_logs_dir("AutoYou", anchor=server.__file__) / "error.log"
                if server.secure_storage_enabled():
                    server.append_secure_file(error_log_path, error_payload)
                else:
                    with error_log_path.open("ab") as ef:
                        ef.write(error_payload)
            except server.SecureStorageError as storage_exc:
                server.LOGGER.error("Could not persist protected pairing error log: %s", storage_exc)
            return JSONResponse({"error": str(e)}, status_code=500)

    @admin_app.post("/api/autopair_hello")
    async def autopair_hello_api(request: Request):
        """First leg of the CPace handshake for Local Pair's direct-HTTP transport.

        Mirrors /api/autopair's identity resolution exactly, so the session key
        this establishes is found again when the client's follow-up /api/autopair
        call arrives - see PairingRouter._handle_autopair_hello_command for the
        same flow used by chat-based transports (Telegram/WhatsApp/Signal).
        """
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        rate_limit_bucket, rate_limit_client_key = server._autopair_rate_limit_bucket(request)
        if not server.AUTOPAIR_RATE_LIMITER.is_allowed(rate_limit_bucket):
            server.LOGGER.warning("AutoPair rate limit exceeded from %s", rate_limit_client_key)
            return JSONResponse({"error": "Too many pairing attempts. Please wait a minute before trying again."}, status_code=429)
        if server.pairing_router is None:
            return JSONResponse({"error": "Pairing subsystem not ready"}, status_code=503)
        try:
            server._configure_pairing_router_helpers()
        except Exception as exc:
            server.LOGGER.warning("Failed to configure pairing router for local pair hello: %s", exc)
        header_platform = str(request.headers.get("X-AutoYou-Platform") or "").strip().lower()
        parse_sender_id = str(
            request.query_params.get("session_id")
            or request.headers.get("X-AutoYou-Session-Id")
            or "admin-web"
        ).strip() or "admin-web"
        parse_platform = header_platform or ("local" if parse_sender_id != "admin-web" else "admin-web")
        raw_body = (await request.body()).decode("utf-8", errors="replace")
        hello_text = f"/autopair_hello\n{raw_body}"
        response_text = await server.pairing_router.process_message(
            hello_text,
            platform=parse_platform,
            sender_id=parse_sender_id,
            identity_sender_id=parse_sender_id,
        )
        if not response_text or not response_text.startswith(server.AUTOPAIR_HELLO_ANSWER_PREFIX):
            return JSONResponse({"error": response_text or "Unable to start secure pairing"}, status_code=400)
        return PlainTextResponse(response_text, status_code=200)

    @admin_app.post("/change-password")
    async def change_password_endpoint(request: Request, new_password: str = Form(...), confirm_password: str = Form(...)):
        redir = server._require_login(request)
        if redir:
            return redir
        block_reason = server._config_write_block_reason()
        if block_reason:
            return PlainTextResponse(block_reason, status_code=409)
        if not new_password or new_password != confirm_password:
            return server._html_page("""
            <div class='card'>
              <h2>Error</h2>
              <p class='muted'>New password confirmation does not match.</p>
              <a href='/'>Back</a>
            </div>
            """)
        strength_error = server._server_password_strength_error(new_password)
        if strength_error:
            return server._html_page(f"""
            <div class='card'>
              <h2>Error</h2>
              <p class='muted'>{strength_error}</p>
              <a href='/'>Back</a>
            </div>
            """)
        try:
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
            server.STATE.used_default_password = (new_password == server.DEFAULT_SERVER_PASSWORD)
            return RedirectResponse(url="/?password=updated", status_code=302)
        except Exception as e:
            return server._html_page(f"""
            <div class='card'>
              <h2>Failed to change password</h2>
              <pre class='mono'>{str(e)}</pre>
              <a href='/'>Back</a>
            </div>
            """)

    @admin_app.post("/save-security")
    async def save_security_endpoint(request: Request, security_mode: str = Form(...)):
        """Persist security mode selection from Admin dashboard.

        Allowed values include "secure_professional_maximus", the strongest
        local-storage protection mode.
        Updates `STATE.config['security']['mode']` and saves the encrypted config.
        """
        redir = server._require_login(request)
        if redir:
            return redir
        block_reason = server._config_write_block_reason()
        if block_reason:
            return PlainTextResponse(block_reason, status_code=409)
        mode = server._normalize_security_mode(security_mode or "secure", default="")
        if mode not in (
            "normal",
            "secure",
            "secure_professional",
            server.SECURE_PROFESSIONAL_MAXIMUS_MODE,
        ):
            return server._html_page("""
            <div class='card'>
              <h2>Invalid security mode</h2>
              <a href='/'>Back</a>
            </div>
            """)
        try:
            cfg = server._loaded_config_for_update()
            sec = cfg.setdefault("security", {})
            sec["mode"] = mode
            server.STATE.config = server._save_and_reload_state_config(cfg)
            return RedirectResponse(url=f"/?saved=security_mode", status_code=302)
        except Exception as e:
            return server._html_page(f"""
            <div class='card'>
              <h2>Failed to save security mode</h2>
              <pre class='mono'>{str(e)}</pre>
              <a href='/'>Back</a>
            </div>
            """)

    @admin_app.post("/admin/security/totp/generate")
    async def admin_totp_generate(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)
        if server.pyotp is None:
            return JSONResponse({"success": False, "error": "pyotp not installed"}, status_code=500)
        try:
            payload = await request.json()
            target = server._normalize_totp_target(payload.get("target"))
            cfg = server._loaded_config_for_update()
            server._apply_default_security_config(cfg)
            secret = server.pyotp.random_base32()
            cfg.setdefault("security", {})[server._totp_target_config_key(target)] = secret
            server._persist_state_config(cfg)
            return JSONResponse(server._totp_payload(secret=secret, target=target))
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @admin_app.post("/admin/security/totp/show")
    async def admin_totp_show(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        if not server._has_loaded_config_session():
            return JSONResponse({"success": False, "error": "Not logged in"}, status_code=401)
        try:
            cfg = server.STATE.config or server._default_config()
            server._apply_default_security_config(cfg)
            secret = server._get_pairing_totp_secret(cfg)
            if not secret:
                return JSONResponse({"success": False, "error": "No saved 2FA secret."}, status_code=404)
            return JSONResponse(server._totp_payload(secret=secret, target="pairing"))
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @admin_app.get("/admin/security/totp/current-code")
    async def admin_totp_current_code(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        if not server._has_loaded_config_session():
            return JSONResponse({"success": False, "error": "Not logged in"}, status_code=401)
        try:
            import time as _time
            cfg = server.STATE.config or server._default_config()
            server._apply_default_security_config(cfg)
            secret = server._get_pairing_totp_secret(cfg)
            if not secret or server.pyotp is None:
                return JSONResponse({"success": False, "error": "No 2FA secret configured."}, status_code=404)
            totp = server.pyotp.TOTP(secret)
            code = totp.now()
            seconds_remaining = 30 - (int(_time.time()) % 30)
            return JSONResponse({
                "success": True,
                "code": code,
                "seconds_remaining": seconds_remaining,
                "period": 30,
            })
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @admin_app.post("/admin/security/totp/import")
    async def admin_totp_import(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)
        try:
            payload = await request.json()
            preview = server._preview_totp_payload(value=payload.get("value"), target=payload.get("target"))
            cfg = server._loaded_config_for_update()
            server._apply_default_security_config(cfg)
            cfg.setdefault("security", {})[server._totp_target_config_key(preview["target"])] = preview["secret"]
            server._persist_state_config(cfg)
            return JSONResponse(preview)
        except ValueError as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)
        except RuntimeError as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @admin_app.post("/admin/security/totp/delete")
    async def admin_totp_delete(request: Request):
        redir = server._require_login(request)
        # from __debug_provenance_i__ import or
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)
        try:
            cfg = server._loaded_config_for_update()
            server._apply_default_security_config(cfg)
            cfg.setdefault("security", {})["totp_secret"] = ""
            server._persist_state_config(cfg)
            return JSONResponse({"success": True, "target": "pairing"})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @admin_app.get("/api/settings/export-qr")
    async def admin_export_settings_qr(request: Request):
        """Return a QR code URL encoding all server settings for Automatic Configuration.

        The payload is a compact JSON object with abbreviated keys so the resulting
        QR code stays scannable even on older phones.  Keys:

            v       - payload version (3)
            profiles- enabled computer transports with their security tier and endpoint
            preferred- default transport to select after import
            pw      - server password
            mode    - security mode string (normal | secure | secure_professional)
            totp    - shared TOTP secret (base32); empty clears an old secret
            tg      - Telegram ACL username or null
            wa      - WhatsApp phone number or null
            sg      - Signal phone number or null
            ice     - full iceServers array (all configured connection-helper entries)
            name    - server display name
            tm      - tunnelmole enabled (bool)
            autopair- autopair available: tunnelmole on + ≥1 messaging partner (bool)
            proxy   - frontend/datachannel proxy enabled (bool)
            voice   - STT+TTS voice calls configured and available (bool)
            cloud   - AutoYou Cloud integration enabled (bool)
            defmsg  - default messaging partner: "tg"|"wa"|"sg"|"none"
            vc      - video calls enabled on this server (bool)
            bg      - background mode (background keep-alive audio) enabled (bool)
            rec     - silent safety recording enabled (bool)
            cam     - client should start its own camera automatically on calls
                      (server records/consumes the client camera) (bool)
            caud    - client should start computer audio automatically on calls
                      (server captures its own speaker/loopback audio) (bool)
        """
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        if not server._has_loaded_config_session():
            return JSONResponse({"error": "Not logged in"}, status_code=401)

        try:
            cfg = server.STATE.config or server._default_config()
            server_password = server.get_server_password()
            if not server_password:
                return JSONResponse(
                    {
                        "error": "Server pairing password is unavailable. Sign in once with the real password to refresh keystore-backed pairing credentials.",
                    },
                    status_code=409,
                )

            # Security mode
            sec = cfg.get("security", {})
            mode = str(sec.get("mode", "secure")).strip() or "secure"

            # Pairing TOTP - Secure Professional now uses one shared server-scoped secret.
            totp_secret = server._get_pairing_totp_secret(cfg)

            # Messaging partners - only include if non-empty.
            # Config values may lag behind the live service state (e.g. freshly-paired
            # WhatsApp/Signal whose phone number hasn't been persisted yet), so we
            # always prefer live service data when the config slot is empty.

            # Telegram: the QR field must be the BOT's @username (what the client
            # messages to reach the server), not the ACL list (which restricts who
            # the bot responds to - a server-side security setting irrelevant to clients).
            # Read from config cache first; fall back to a live Telegram API call if
            # the cache is empty but a bot token is configured.
            tg_cfg = cfg.get("telegram", {})
            tg_username: Optional[str] = (tg_cfg.get("bot_username") or "").strip() or None
            if not tg_username and (tg_cfg.get("bot_token") or "").strip():
                try:
                    _tg_status, _tg_name = await server._telegram_status()
                    if _tg_status == "Connected" and _tg_name and _tg_name != "-":
                        tg_username = _tg_name  # e.g. "@YourAutoYouBot"
                        # Cache for next time so we don't hit Telegram API every QR export
                        tg_cfg["bot_username"] = tg_username
                except Exception:
                    pass

            wa_cfg = cfg.get("whatsapp", {})
            wa_number: Optional[str] = (wa_cfg.get("phone_number") or "").strip() or None
            # Live fallback: if config is stale but the service is running and paired
            if not wa_number and server.STATE.whatsapp_service is not None:
                try:
                    _wa_live = await server._check_and_update_whatsapp_pairing_status(
                        settle_delay_seconds=0, persist_pairing=True
                    )
                    wa_number = (_wa_live.get("phone_number") or "").strip() or None
                except Exception:
                    pass

            sg_cfg = cfg.get("signal", {})
            sg_number: Optional[str] = (sg_cfg.get("phone_number") or "").strip() or None
            # Live fallback for Signal (check_device_pairing_status is lightweight)
            if not sg_number and server.STATE.signal_service is not None:
                try:
                    _sg_live = await server.STATE.signal_service.check_device_pairing_status()
                    if isinstance(_sg_live, dict) and _sg_live.get("paired"):
                        sg_number = (_sg_live.get("phone_number") or "").strip() or None
                        if sg_number and server.STATE.config is not None:
                            server.STATE.config.setdefault("signal", {})["phone_number"] = sg_number
                except Exception:
                    pass

            # Normalize phone numbers: ensure they start with '+' for E.164 format.
            # WhatsApp/Signal typically report numbers without the leading '+' (e.g. "919445576879").
            def _normalize_phone(num: Optional[str]) -> Optional[str]:
                if not num:
                    return num
                if not num.startswith("+"):
                    num = "+" + num
                return num

            wa_number = _normalize_phone(wa_number)
            sg_number = _normalize_phone(sg_number)

            # Default messaging partner: prefer telegram → whatsapp → signal
            configured_partners = [
                ("tg", tg_username),
                ("wa", wa_number),
                ("sg", sg_number),
            ]
            active_partners = [(k, v) for k, v in configured_partners if v]
            if len(active_partners) == 1:
                defmsg = active_partners[0][0]
            elif len(active_partners) > 1:
                defmsg = active_partners[0][0]  # tg > wa > sg order already preserved
            else:
                defmsg = "none"

            # ICE servers - include ALL configured entries (no cap); clients handle
            # large lists fine and the extra data aids connectivity on restricted networks.
            ice_servers = server._get_pairing_ice_servers()

            # Server name
            server_name = str(cfg.get("server", {}).get("name") or "AutoYou-Server").strip()

            # ── Feature flags ────────────────────────────────────────────────────

            # Tunnelmole: is it enabled in config?
            tunnelmole_cfg = cfg.get("tunnelmole", {})
            tm_enabled: bool = bool(tunnelmole_cfg.get("enabled", True))

            # AutoPair: only useful when tunnelmole is on AND ≥1 messaging partner configured
            autopair_available: bool = tm_enabled and bool(active_partners)

            # Frontend / localhost proxy: admin.frontend_proxy_enabled
            proxy_enabled: bool = bool(cfg.get("admin", {}).get("frontend_proxy_enabled", True))

            # Voice calls: STT+TTS both configured with a real provider (not "none")
            speech_cfg = server._speech_config(cfg)
            stt_provider = str(speech_cfg.get("stt", {}).get("model") or "").strip()
            tts_provider = str(speech_cfg.get("tts", {}).get("provider") or "none").strip().lower()
            voice_enabled: bool = (tts_provider not in ("", "none")) and bool(stt_provider)

            # AutoYou Cloud: is a server_token configured?
            cloud_cfg = cfg.get("cloud", {})
            cloud_enabled: bool = bool((cloud_cfg.get("server_token") or "").strip())

            # Video call feature flags - clients mirror these into their local
            # call settings so a single scan configures the newer call features.
            video_call_enabled: bool = server._get_video_call_enabled(cfg=cfg)
            video_call_audio_enabled: bool = server._get_video_call_audio_enabled(cfg=cfg)
            background_mode_enabled: bool = server._get_background_mode_enabled(cfg=cfg)
            silent_recording_enabled: bool = server._get_silent_recording_enabled(cfg=cfg)
            # Auto self-video: the server is configured to record/consume the
            # client camera, so the client should start its camera automatically.
            auto_self_video: bool = video_call_enabled and server._get_video_record_my_video_enabled(cfg=cfg)
            # Auto computer-audio: the server captures its own (computer) audio
            # for calls, so the client should start listening automatically.
            video_call_cfg = server._get_video_call_config(cfg=cfg)
            computer_audio_captured: bool = bool(video_call_cfg.get("capture_audio")) and (
                "speaker_loopback" in (video_call_cfg.get("audio_sources") or [])
            )
            auto_computer_audio: bool = (
                video_call_enabled and video_call_audio_enabled and computer_audio_captured
            )

            payload: dict = {
                "v": 3,
                "pw": server_password,
                "mode": mode,
                "totp": totp_secret,
                "tg": tg_username,
                "wa": wa_number,
                "sg": sg_number,
                "ice": ice_servers if ice_servers else None,
                "name": server_name,
                "tm": tm_enabled,
                "autopair": autopair_available,
                "proxy": proxy_enabled,
                "voice": voice_enabled,
                "cloud": cloud_enabled,
                "defmsg": defmsg if defmsg != "none" else None,
                "vc": video_call_enabled,
                "bg": background_mode_enabled,
                "rec": silent_recording_enabled,
                "cam": auto_self_video,
                "caud": auto_computer_audio,
            }
            from shared.local_network_info import build_local_pair_info
            from shared.mobile_provisioning import mobile_pairing_profiles

            payload.update(mobile_pairing_profiles(
                cloud=cloud_enabled, autopair=autopair_available, otp=tm_enabled,
                bluetooth=server._is_bluetooth_pairing_enabled(),
                local=build_local_pair_info(
                    port=int(server.ADMIN_WEB_SERVICE_PORT), bind_host=server.SERVER_BIND_HOST,
                    server_name=server_name,
                ),
                security_tier=sec.get("tier", "B"),
                bluetooth_name=f"{server_name} Bluetooth Pair",
            ))
            # Empty TOTP explicitly clears credentials from a previous computer.
            payload["totp"] = totp_secret or ""
            if mode == "secure_professional_maximus":
                payload["mode"] = "secure_professional"

            # Remove null/false feature flags to keep payload compact; always keep
            # required identity fields.
            always_include = {"v", "pw", "mode", "name"}
            payload = {
                k: v for k, v in payload.items()
                if k in always_include or v is not None
            }

            json_str = server.json.dumps(payload, separators=(",", ":"))
            qr_url = server._build_local_qr_data_url(json_str)
            if not qr_url:
                return JSONResponse(
                    {"error": "Could not create the setup QR locally. Check the QR component and try again."},
                    status_code=503, headers={"Cache-Control": "no-store"},
                )
            return JSONResponse({
                "qr_url": qr_url,
                "payload_size": len(json_str),
                "features": {
                    "tunnelmole": tm_enabled,
                    "autopair": autopair_available,
                    "proxy": proxy_enabled,
                    "voice": voice_enabled,
                    "cloud": cloud_enabled,
                    "totp": totp_secret is not None,
                    "messaging_partners": len(active_partners),
                    "ice_servers": len(ice_servers),
                    "default_partner": defmsg,
                    "video_calls": video_call_enabled,
                    "background_mode": background_mode_enabled,
                    "safety_recording": silent_recording_enabled,
                    "auto_self_video": auto_self_video,
                    "auto_computer_audio": auto_computer_audio,
                    "pairing_modes": list(payload["profiles"]),
                },
            }, headers={"Cache-Control": "no-store"})

        except Exception as e:
            server.LOGGER.exception("Failed to build settings QR payload")
            return JSONResponse({"error": str(e)}, status_code=500)

    @admin_app.post("/api/admin/session/verify")
    async def admin_session_verify(request: Request):
        """Verify a TOTP code for admin session elevation.

        Called by the admin_agent's ``verify_admin_totp`` tool.  Does NOT require
        prior browser login - the TOTP code itself is the credential.

        Body: {"totp_code": str}
        Returns: {"valid": bool, "client_id": str?, "error": str?}
        """
        try:
            payload = await request.json()
            totp_code = str(payload.get("totp_code", "")).strip()
            if not totp_code:
                return JSONResponse({"valid": False, "error": "totp_code required"}, status_code=400)
            cfg = server.STATE.config or server._default_config()
            capabilities = server._describe_totp_capabilities(cfg)
            if not capabilities.get("totp_configured"):
                return JSONResponse(
                    {"valid": False, "error": "No 2FA secret configured on this server. Configure Two-Factor Authentication in Admin UI → Security first."},
                    status_code=400,
                )
            if server.pyotp is None:
                return JSONResponse({"valid": False, "error": "pyotp not installed"}, status_code=500)
            pairing_secret = server._get_pairing_totp_secret(cfg)
            if not server._verify_totp_secret(pairing_secret, totp_code, valid_window=1):
                return JSONResponse({"valid": False, "error": "Invalid TOTP code. Please check your authenticator app."})
            server.LOGGER.info("Admin session TOTP verified")
            token = server.secrets.token_hex(32)
            server.ADMIN_API_TOKENS[token] = server.time.time() + 3600
            return JSONResponse({"valid": True, "client_id": "admin", "token": token})
        except Exception as e:
            server.LOGGER.error("admin_session_verify error: %s", e)
            return JSONResponse({"valid": False, "error": str(e)}, status_code=500)

    @admin_app.get("/api/admin/session/capabilities")
    async def admin_session_capabilities():
        """Report whether the server has a usable shared 2FA secret configured."""
        try:
            cfg = server.STATE.config or server._default_config()
            return JSONResponse(server._describe_totp_capabilities(cfg))
        except Exception as e:
            server.LOGGER.error("admin_session_capabilities error: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    return {
        "get_unlock_status": get_unlock_status,
        "post_unlock_setup": post_unlock_setup,
        "post_unlock_verify": post_unlock_verify,
        "auth_health": auth_health,
        "auth_endpoint": auth_endpoint,
        "signal_endpoint": signal_endpoint,
        "signal_poll_endpoint": signal_poll_endpoint,
        "local_pair_info": local_pair_info,
        "bluetooth_pairing_status_endpoint": bluetooth_pairing_status_endpoint,
        "autopair_api": autopair_api,
        "live_pair_api": live_pair_api,
        "autopair_hello_api": autopair_hello_api,
        "change_password_endpoint": change_password_endpoint,
        "save_security_endpoint": save_security_endpoint,
        "admin_totp_generate": admin_totp_generate,
        "admin_totp_show": admin_totp_show,
        "admin_totp_current_code": admin_totp_current_code,
        "admin_totp_import": admin_totp_import,
        "admin_totp_delete": admin_totp_delete,
        "admin_export_settings_qr": admin_export_settings_qr,
        "admin_session_verify": admin_session_verify,
        "admin_session_capabilities": admin_session_capabilities
    }
