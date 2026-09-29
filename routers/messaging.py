# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-tenpercent-ba2c4e27e05b8217f8cf0d9a

"""Messaging HTTP routes for the full AutoYou server."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from typing import Any, Callable, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-tenpercent-ba2c4e27e05b8217f8cf0d9a"


def register_routes(
    admin_app: FastAPI,
    auth_app: FastAPI,
    server: Any,
) -> Dict[str, Callable[..., Any]]:
    @admin_app.post("/api/admin/telegram/allow-code")
    async def admin_generate_telegram_allow_code_api(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)

        cfg = server._loaded_config_for_update(copy_config=True)
        telegram_cfg = cfg.setdefault("telegram", {})
        if not str(telegram_cfg.get("bot_token") or "").strip():
            return JSONResponse(status_code=400, content={"success": False, "error": "Telegram bot token is required first."})

        try:
            telegram_cfg["access_gate_enabled"] = True
            server.STATE.config = server._save_and_reload_state_config(cfg)
            allow_code = server._issue_telegram_allow_code()
            return server._json_response_no_store(
                {
                    "success": True,
                    "allow_code": allow_code,
                    "bootstrap": await server._build_admin_ui_bootstrap_payload(),
                }
            )
        except Exception as exc:
            server.LOGGER.error("admin_generate_telegram_allow_code_api failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.get("/api/telegram/senders")
    async def admin_telegram_sender_discovery(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        return server._json_response_no_store(server._telegram_sender_discovery_payload())

    @admin_app.post("/api/telegram/senders/approve")
    async def admin_telegram_approve_senders(request: Request):
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

        raw_sender_ids = payload.get("sender_ids") if isinstance(payload, dict) else []
        if isinstance(raw_sender_ids, str):
            raw_sender_ids = server._split_admin_ui_list_value(raw_sender_ids)
        if not isinstance(raw_sender_ids, list):
            raw_sender_ids = []
        sender_ids = [
            sender_id
            for sender_id in (
                server._normalize_telegram_sender_id(value)
                for value in raw_sender_ids
            )
            if sender_id
        ]
        if not sender_ids:
            return JSONResponse(status_code=400, content={"success": False, "error": "Select at least one discovered Telegram sender ID."})

        try:
            cfg = server._loaded_config_for_update(copy_config=True)
            telegram_cfg = cfg.setdefault("telegram", {})
            existing_sender_ids = [
                sender_id
                for sender_id in (
                    server._normalize_telegram_sender_id(value)
                    for value in telegram_cfg.get("acl_sender_ids", []) or []
                )
                if sender_id
            ]
            telegram_cfg["acl_sender_ids"] = sorted(set(existing_sender_ids + sender_ids))
            telegram_cfg["access_gate_enabled"] = True
            server.STATE.config = server._save_and_reload_state_config(cfg)
            return server._json_response_no_store(
                {
                    **server._telegram_sender_discovery_payload(),
                    "message": "Telegram sender approval list updated.",
                    "bootstrap": await server._build_admin_ui_bootstrap_payload(),
                }
            )
        except Exception as exc:
            server.LOGGER.error("admin_telegram_approve_senders failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.get("/api/telegram-user/status")
    async def admin_get_telegram_user_status(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error

        config = server.STATE.config.get("telegram_user", {}) if server.STATE.config else {}
        enabled = bool(config.get("enabled", False))
        prompt_builder_config = config.get("prompt_builder", {}) if isinstance(config, dict) else {}
        if not isinstance(prompt_builder_config, dict):
            prompt_builder_config = {}
        feature_enabled = server._messaging_partner_feature_enabled("telegram_user")
        service = server.STATE.telegram_user_service
        if not feature_enabled and service is None:
            return {
                "enabled": False,
                "feature_enabled": False,
                "status": "disabled",
                "connected": False,
                "authorized": False,
                "ready": False,
                "needs_password": False,
                "owner_scoped": True,
                "saved_messages_only": True,
                "training_export_consent": bool(config.get("training_export_consent", False)),
                "prompt_builder": {
                    "enabled": bool(prompt_builder_config.get("enabled", False)),
                    "application_agent": str(prompt_builder_config.get("application_agent") or "codex_desktop_agent"),
                },
                "error": server._messaging_partner_disabled_message("telegram_user"),
            }
        if service is None:
            return {
                "enabled": enabled,
                "feature_enabled": feature_enabled,
                "status": "stopped" if enabled else "disabled",
                "connected": False,
                "authorized": False,
                "ready": False,
                "needs_password": False,
                "owner_scoped": True,
                "saved_messages_only": True,
                "training_export_consent": bool(config.get("training_export_consent", False)),
                "prompt_builder": {
                    "enabled": bool(prompt_builder_config.get("enabled", False)),
                    "application_agent": str(prompt_builder_config.get("application_agent") or "codex_desktop_agent"),
                },
                "error": "Service not started" if enabled else "Service disabled",
            }

        try:
            cached_status = server._get_cached_admin_status("telegram_user_status")
            if isinstance(cached_status, dict):
                return cached_status
            found, raw_status = await server._call_telegram_user_service_method(
                service,
                ("get_status", "status"),
            )
            if found and isinstance(raw_status, dict):
                status = server._redact_telegram_user_status_payload(server._make_json_safe(raw_status))
            else:
                status = {"status": str(raw_status or "starting")}
            connected = bool(status.get("connected") or status.get("authorized"))
            status["enabled"] = enabled
            status["feature_enabled"] = feature_enabled
            status["connected"] = connected
            status.setdefault("authorized", connected)
            status.setdefault("ready", connected)
            status.setdefault(
                "needs_password",
                bool(status.get("awaiting_2fa") or status.get("two_factor_required")),
            )
            status.setdefault("status", "connected" if connected else "starting")
            status["owner_scoped"] = True
            status["saved_messages_only"] = True
            status["training_export_consent"] = bool(config.get("training_export_consent", False))
            status["prompt_builder"] = {
                "enabled": bool(prompt_builder_config.get("enabled", False)),
                "application_agent": str(prompt_builder_config.get("application_agent") or "codex_desktop_agent"),
            }
            return server._set_cached_admin_status("telegram_user_status", status, ttl_seconds=1.0)
        except Exception as exc:
            server.LOGGER.warning("Telegram User status request failed: %s", exc)
            return {
                "enabled": enabled,
                "feature_enabled": feature_enabled,
                "status": "error",
                "connected": False,
                "authorized": False,
                "ready": False,
                "needs_password": False,
                "owner_scoped": True,
                "saved_messages_only": True,
                "training_export_consent": bool(config.get("training_export_consent", False)),
                "prompt_builder": {
                    "enabled": bool(prompt_builder_config.get("enabled", False)),
                    "application_agent": str(prompt_builder_config.get("application_agent") or "codex_desktop_agent"),
                },
                "error": "Status check failed",
            }

    @admin_app.get("/api/telegram-user/qr")
    async def admin_get_telegram_user_qr_code(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        if not server._messaging_partner_feature_enabled("telegram_user"):
            return server._messaging_partner_disabled_response("telegram_user")
        block_reason = server._config_write_block_reason()
        # from __debug_provenance_f__ import tenpercent
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)

        config = server.STATE.config.get("telegram_user", {}) if server.STATE.config else {}
        if not bool(config.get("enabled", False)):
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Enable Telegram User before signing in."},
            )
        if server.STATE.telegram_user_service is None:
            await server.start_or_restart_telegram_user()
        service = server.STATE.telegram_user_service
        if service is None:
            return JSONResponse(
                status_code=503,
                content={"success": False, "error": "Telegram User is not ready. Check the saved API settings."},
            )

        try:
            _, result = await server._call_telegram_user_service_method(
                service,
                ("get_qr_code_data_url",),
            )
            qr_url = server._telegram_user_qr_url(result)
            login_result: Any = None
            if not qr_url:
                found, login_result = await server._call_telegram_user_service_method(
                    service,
                    ("begin_qr_login", "start_qr_login"),
                )
                if not found:
                    return JSONResponse(
                        status_code=503,
                        content={"success": False, "error": "Telegram User QR sign-in is unavailable."},
                    )
                qr_url = server._telegram_user_qr_url(login_result)
            if not qr_url:
                _, result = await server._call_telegram_user_service_method(
                    service,
                    ("get_qr_code_data_url",),
                )
                qr_url = server._telegram_user_qr_url(result)
            if qr_url:
                return {
                    "success": True,
                    "qr_url": qr_url,
                    "status": "ready",
                    "needs_password": False,
                    "connected": False,
                    "saved_messages_only": True,
                }
            needs_password = bool(
                isinstance(login_result, dict)
                and (login_result.get("needs_password") or login_result.get("awaiting_2fa"))
            )
            return JSONResponse(
                status_code=202,
                content={
                    "success": False,
                    "pending": True,
                    "status": "needs_password" if needs_password else "starting",
                    "needs_password": needs_password,
                    "connected": False,
                    "saved_messages_only": True,
                    "error": "Sign-in is still being prepared.",
                },
            )
        except Exception as exc:
            server.LOGGER.warning("Telegram User QR request failed: %s", exc)
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "Could not prepare a Telegram sign-in code."},
            )

    @admin_app.post("/api/telegram-user/restart")
    async def admin_restart_telegram_user_service(request: Request):
        auth_error = server._require_api_login_json(request)
        if auth_error is not None:
            return auth_error
        if not server._messaging_partner_feature_enabled("telegram_user"):
            return server._messaging_partner_disabled_response("telegram_user")
        if not bool(((server.STATE.config or {}).get("telegram_user", {}) or {}).get("enabled", False)):
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Enable Telegram User and save its API settings first."},
            )
        if await server.start_or_restart_telegram_user():
            return {"success": True, "message": "Telegram User restarted successfully."}
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Telegram User could not be started. Check the saved API settings."},
        )

    @admin_app.post("/api/telegram-user/disconnect")
    async def admin_disconnect_telegram_user(request: Request):
        auth_error = server._require_api_login_json(request)
        if auth_error is not None:
            return auth_error
        if not server._messaging_partner_feature_enabled("telegram_user"):
            return server._messaging_partner_disabled_response("telegram_user")
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)

        service = server.STATE.telegram_user_service
        try:
            if service is not None:
                found, result = await server._call_telegram_user_service_method(
                    service,
                    ("disconnect", "log_out", "cleanup_session"),
                )
                if found and not server._telegram_user_call_succeeded(result):
                    raise RuntimeError("service disconnect was not completed")
            await server.stop_telegram_user()
            if not server._clear_telegram_user_session():
                raise RuntimeError("protected session state could not be cleared")
            return {"success": True, "message": "Telegram User was disconnected."}
        except Exception as exc:
            server.LOGGER.warning("Telegram User disconnect failed: %s", exc)
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "Telegram User could not be disconnected."},
            )

    @admin_app.post("/api/telegram-user/2fa")
    async def admin_complete_telegram_user_2fa(request: Request):
        auth_error = server._require_api_login_json(request)
        if auth_error is not None:
            return auth_error
        if not server._messaging_partner_feature_enabled("telegram_user"):
            return server._messaging_partner_disabled_response("telegram_user")
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)
        service = server.STATE.telegram_user_service
        if service is None:
            return JSONResponse(
                status_code=503,
                content={"success": False, "error": "Start Telegram User before confirming sign-in."},
            )
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})
        password = str((payload or {}).get("password") or "").strip()
        if not password:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Enter the Telegram sign-in password."},
            )
        try:
            found, result = await server._call_telegram_user_service_method(
                service,
                ("complete_2fa", "submit_2fa_password", "complete_two_factor_auth"),
                password,
            )
            if not found:
                return JSONResponse(
                    status_code=503,
                    content={"success": False, "error": "Password confirmation is unavailable."},
                )
            if not server._telegram_user_call_succeeded(result):
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Telegram did not accept that password."},
                )
            server._invalidate_admin_status_cache("telegram_user_status")
            return {
                "success": True,
                "status": "connected",
                "connected": True,
                "authorized": True,
                "ready": True,
                "needs_password": False,
                "saved_messages_only": True,
            }
        except Exception as exc:
            server.LOGGER.warning("Telegram User password confirmation failed: %s", exc)
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Telegram did not accept that password."},
            )

    @admin_app.post("/api/telegram-user/send")
    async def admin_send_telegram_user_message(request: Request):
        auth_error = server._require_api_login_json(request)
        if auth_error is not None:
            return auth_error
        if not server._messaging_partner_feature_enabled("telegram_user"):
            return server._messaging_partner_disabled_response("telegram_user")
        service = server.STATE.telegram_user_service
        if service is None:
            return JSONResponse(
                status_code=503,
                content={"success": False, "error": "Telegram User is not available."},
            )
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

        message = str((payload or {}).get("message") or "").strip()
        context = (payload or {}).get("context")
        attachments_payload = (payload or {}).get("attachments")
        if context is not None and not isinstance(context, list):
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Field 'context' must be a JSON array when provided"},
            )
        if attachments_payload is not None and not isinstance(attachments_payload, list):
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Field 'attachments' must be a JSON array when provided"},
            )
        media_attachments = server._extract_admin_media_attachments(
            context,
            attachments_payload,
            source="admin_telegram_user_send",
        )
        if not message and not media_attachments:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Enter a message or attach media."},
            )
        try:
            if media_attachments:
                found, result = await server._call_telegram_user_service_method(
                    service,
                    ("send_media_attachments",),
                    server._caption_first_media_attachment(media_attachments, message),
                )
                if not found:
                    return JSONResponse(
                        status_code=503,
                        content={"success": False, "error": "Telegram User media sending is unavailable."},
                    )
                if server._telegram_user_call_succeeded(result):
                    return {
                        "success": True,
                        "message": "Media sent to Saved Messages.",
                        "media_attachments": len(media_attachments),
                    }
                if not message:
                    return JSONResponse(
                        status_code=500,
                        content={"success": False, "error": "Telegram could not send the media."},
                    )
            if message:
                found, result = await server._call_telegram_user_service_method(
                    service,
                    ("send_message",),
                    message,
                )
                if found and server._telegram_user_call_succeeded(result):
                    return {"success": True, "message": "Message sent to Saved Messages."}
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "Telegram could not send the message."},
            )
        except Exception as exc:
            server.LOGGER.warning("Telegram User send failed: %s", exc)
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "Telegram could not send the message."},
            )

    @admin_app.get("/api/telegram-user/messages")
    async def admin_get_telegram_user_messages(request: Request, limit: int = 100):
        """Return metadata only; message bodies remain in the consent-gated local path."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        if not server._messaging_partner_feature_enabled("telegram_user"):
            return server._messaging_partner_disabled_response("telegram_user")
        service = server.STATE.telegram_user_service
        if service is None:
            return JSONResponse(
                status_code=503,
                content={"success": False, "error": "Telegram User is not available."},
            )
        safe_limit = max(1, min(int(limit), 1000))
        try:
            try:
                found, entries = await server._call_telegram_user_service_method(
                    service,
                    ("get_message_log",),
                    safe_limit,
                )
            except TypeError:
                found, entries = await server._call_telegram_user_service_method(
                    service,
                    ("get_message_log",),
                )
            if not found:
                return JSONResponse(
                    status_code=503,
                    content={"success": False, "error": "Telegram User history is unavailable."},
                )
            messages = server._telegram_user_message_metadata(entries, limit=safe_limit)
            return {
                "success": True,
                "messages": messages,
                "count": len(messages),
                "content_included": False,
                "saved_messages_only": True,
            }
        except Exception as exc:
            server.LOGGER.warning("Telegram User metadata request failed: %s", exc)
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "Telegram User history could not be read."},
            )

    @admin_app.post("/admin/telegram/allow-code")
    async def admin_generate_telegram_allow_code(request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        block_reason = server._config_write_block_reason()
        if block_reason:
            return PlainTextResponse(block_reason, status_code=409)

        cfg = server._loaded_config_for_update()
        telegram_cfg = cfg.setdefault("telegram", {})
        if not str(telegram_cfg.get("bot_token") or "").strip():
            return RedirectResponse(url="/?telegram_allow_error=missing_token", status_code=302)

        telegram_cfg["access_gate_enabled"] = True
        server._persist_state_config(cfg)
        allow_code = server._issue_telegram_allow_code()
        return RedirectResponse(url=f"/?telegram_allow_code={allow_code}", status_code=302)

    routes = {
        "admin_generate_telegram_allow_code_api": admin_generate_telegram_allow_code_api,
        "admin_telegram_sender_discovery": admin_telegram_sender_discovery,
        "admin_telegram_approve_senders": admin_telegram_approve_senders,
        "admin_get_telegram_user_status": admin_get_telegram_user_status,
        "admin_get_telegram_user_qr_code": admin_get_telegram_user_qr_code,
        "admin_restart_telegram_user_service": admin_restart_telegram_user_service,
        "admin_disconnect_telegram_user": admin_disconnect_telegram_user,
        "admin_complete_telegram_user_2fa": admin_complete_telegram_user_2fa,
        "admin_send_telegram_user_message": admin_send_telegram_user_message,
        "admin_get_telegram_user_messages": admin_get_telegram_user_messages,
        "admin_generate_telegram_allow_code": admin_generate_telegram_allow_code
    }

    if server.SignalService is not None:
        @admin_app.get("/api/signal/qr")
        async def admin_get_signal_qr_code(request: Request):
            """Get QR code for Signal pairing (admin endpoint)."""
            auth_response = server._require_api_login_json(request)
            if auth_response is not None:
                return auth_response
            if not server._messaging_partner_feature_enabled("signal"):
                return server._messaging_partner_disabled_response("signal")
            if server.STATE.signal_service is None:
                return server.JSONResponse(
                    status_code=503,
                    content={"success": False, "error": "Signal service not available"}
                )

            try:
                # Check if device is already paired
                signal_config = server.STATE.config.get("signal", {}) if server.STATE.config else {}
                if signal_config.get("paired", False):
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Device is already paired. Use cleanup to re-pair."}
                    )

                try:
                    live_status = await server.STATE.signal_service.check_device_pairing_status()
                    if live_status.get("paired", False):
                        return server.JSONResponse(
                            status_code=400,
                            content={"success": False, "error": "Device is already paired. Use cleanup to re-pair."}
                        )
                except Exception as check_err:
                    server.LOGGER.debug("Live pairing status check failed during QR request: %s", check_err)

                refresh_raw = str(request.query_params.get("refresh", "") or request.query_params.get("force", "")).strip().lower()
                force_refresh = refresh_raw in ("true", "1", "yes")
                device_name = signal_config.get("device_name", "AutoYou-Signal")

                qr_code = await server.STATE.signal_service.get_qr_code_link(force_refresh=force_refresh)
                if qr_code:
                    # Return format expected by admin UI JavaScript
                    return {"success": True, "qr_url": qr_code, "device_name": device_name}
                else:
                    return server.JSONResponse(
                        status_code=500,
                        content={"success": False, "error": "Failed to generate QR code"}
                    )
            except Exception as e:
                server.LOGGER.error(f"Error getting Signal QR code (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @admin_app.get("/api/signal/status")
        async def admin_get_signal_status(request: Request):
            """Get Signal service status (admin endpoint)."""
            auth_response = server._require_api_login_json(request)
            if auth_response is not None:
                return auth_response
            feature_enabled = server._messaging_partner_feature_enabled("signal")
            if not feature_enabled and server.STATE.signal_service is None:
                return {
                    "enabled": False,
                    "feature_enabled": False,
                    "container_running": False,
                    "paired": False,
                    "phone_number": None,
                    "error": server._messaging_partner_disabled_message("signal"),
                }
            if server.STATE.signal_service is None:
                signal_config = server.STATE.config.get("signal", {}) if server.STATE.config else {}
                enabled = signal_config.get("enabled", False)
                return {
                    "enabled": enabled,
                    "container_running": False,
                    "paired": False,
                    "phone_number": None,
                    "error": "Service not started" if enabled else "Service disabled"
                }

            try:
                cached_status = server._get_cached_admin_status("signal_status")
                if cached_status is not None:
                    if isinstance(cached_status, dict):
                        cached_status["enabled"] = True
                        cached_status.setdefault("feature_enabled", feature_enabled)
                    return cached_status
                status = await server.STATE.signal_service.get_status()
                if isinstance(status, dict):
                    status["enabled"] = True
                    status.setdefault("feature_enabled", feature_enabled)
                server._set_cached_admin_status("signal_status", status, ttl_seconds=1.0)
                return status
            except Exception as e:
                server.LOGGER.error(f"Error getting Signal status (admin): {e}")
                return {
                    "enabled": True,
                    "container_running": False,
                    "paired": False,
                    "phone_number": None,
                    "error": str(e)
                }

        @admin_app.get("/api/signal/messages")
        async def admin_get_signal_messages(request: Request, limit: int = 100):
            """Get recent Signal message log (admin endpoint)."""
            auth_response = server._require_api_login_json(request)
            if auth_response is not None:
                return auth_response
            if server.STATE.signal_service is None:
                return server.JSONResponse(
                    status_code=503,
                    content={"success": False, "error": "Signal service not available"}
                )

            try:
                messages = server.STATE.signal_service.get_message_log(limit)
                return {"success": True, "messages": messages, "count": len(messages)}
            except Exception as e:
                server.LOGGER.error(f"Error getting Signal messages (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @admin_app.get("/api/signal/detailed-status")
        async def admin_get_signal_detailed_status(request: Request):
            """Get detailed Signal service status with integration states (admin endpoint)."""
            auth_response = server._require_api_login_json(request)
            if auth_response is not None:
                return auth_response
            if server.STATE.signal_service is None:
                return server.JSONResponse(
                    status_code=503,
                    content={"success": False, "error": "Signal service not available"}
                )

            try:
                status = await server.STATE.signal_service.get_detailed_status_async()
                return {"success": True, "status": status}
            except Exception as e:
                server.LOGGER.error(f"Error getting detailed Signal status (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @admin_app.get("/api/signal/device-name")
        async def admin_get_signal_device_name(request: Request):
            """Get the actual Signal device name when available without noisy 404s."""
            auth_response = server._require_api_login_json(request)
            if auth_response is not None:
                return auth_response
            if server.STATE.signal_service is None:
                return server.JSONResponse(
                    status_code=503,
                    content={"error": "Signal service not available"}
                )

            try:
                signal_config = server.STATE.config.get("signal", {}) if server.STATE.config else {}
                configured_name = signal_config.get("device_name", "AutoYou-Signal")
                device_name = await server.STATE.signal_service.get_device_name_from_api()
                if device_name:
                    return {"success": True, "paired": True, "device_name": device_name}
                paired_phone = await server.STATE.signal_service.get_paired_phone_number()
                return {
                    "success": False,
                    "paired": bool(paired_phone),
                    "device_name": configured_name,
                    "error": "Device name not available from Signal API yet",
                }
            except Exception as e:
                server.LOGGER.error(f"Error getting Signal device name (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"error": str(e)}
                )

        @admin_app.post("/api/signal/cleanup")
        async def admin_cleanup_signal(request: Request):
            """Cleanup Signal pairing and reset configuration (admin endpoint)."""
            auth_response = server._require_api_login_json(request)
            if auth_response is not None:
                return auth_response
            if not server._messaging_partner_feature_enabled("signal"):
                return server._messaging_partner_disabled_response("signal")
            if server.SignalService is None:
                return server.JSONResponse(
                    status_code=503,
                    content={"success": False, "error": "Signal service module not available"}
                )

            try:
                signal_config = server.STATE.config.setdefault("signal", {}) if server.STATE.config is not None else {}
                # Re-pair always needs a full container recycle so persisted account state is dropped.
                shutdown_docker = True

                cleanup_service = server.STATE.signal_service
                if cleanup_service is None:
                    cleanup_service = server.SignalService(
                        port=signal_config.get("port", 8082),
                        device_name=signal_config.get("device_name", "AutoYou-Signal"),
                    )

                cleanup_success = await cleanup_service.cleanup(shutdown_docker=shutdown_docker)
                if not cleanup_success:
                    raise RuntimeError("Signal cleanup did not complete successfully")

                # Prevent the restart path from stopping the same service twice.
                server.STATE.signal_service = None

                # Update config to reflect unpaired status
                if server.STATE.config:
                    signal_config["paired"] = False
                    signal_config["phone_number"] = ""
                    if server._can_persist_config():
                        server._persist_state_config(server.STATE.config)

                # Restart Signal service for fresh pairing
                await server.start_or_restart_signal()

                return {"success": True, "message": "Signal configuration cleaned up successfully"}
            except Exception as e:
                server.LOGGER.error(f"Error cleaning up Signal (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @admin_app.post("/api/signal/send")
        async def admin_send_signal_message(request: Request):
            """Send Signal message (admin endpoint)."""
            if not server._messaging_partner_feature_enabled("signal"):
                return server._messaging_partner_disabled_response("signal")
            auth_response = server._require_api_login_json(request)
            if auth_response is not None:
                return auth_response
            if server.STATE.signal_service is None:
                return server.JSONResponse(
                    status_code=503,
                    content={"success": False, "error": "Signal service not available"}
                )

            try:
                data = await request.json()
                to = str(data.get("to") or "").strip()
                message = str(data.get("message") or "").strip()
                context = data.get("context")
                attachments_payload = data.get("attachments")
                if context is not None and not isinstance(context, list):
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Field 'context' must be a JSON array when provided"}
                    )
                if attachments_payload is not None and not isinstance(attachments_payload, list):
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Field 'attachments' must be a JSON array when provided"}
                    )

                media_attachments = server._extract_admin_media_attachments(
                    context,
                    attachments_payload,
                    source="admin_signal_send",
                )
                if not to or (not message and not media_attachments):
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing 'to' and message/media attachment field"}
                    )

                if media_attachments:
                    captioned_attachments = server._caption_first_media_attachment(media_attachments, message)
                    send_media = getattr(server.STATE.signal_service, "send_media_attachments", None)
                    if not callable(send_media):
                        return server.JSONResponse(
                            status_code=503,
                            content={"success": False, "error": "Signal media send is not available"}
                        )
                    if await send_media(to, captioned_attachments):
                        return {
                            "success": True,
                            "message": "Media message sent successfully",
                            "media_attachments": len(media_attachments),
                        }
                    if not message:
                        return server.JSONResponse(
                            status_code=500,
                            content={"success": False, "error": "No Signal media attachments could be sent"}
                        )

                success = await server.STATE.signal_service.send_message(to, message)
                if success:
                    return {"success": True, "message": "Message sent successfully"}
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": "Failed to send message"}
                )
            except Exception as e:
                server.LOGGER.error(f"Error sending Signal message (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @admin_app.post("/api/signal/restart")
        async def admin_restart_signal_service(request: Request):
            """Restart Signal service (admin endpoint)."""
            auth_response = server._require_api_login_json(request)
            if auth_response is not None:
                return auth_response
            if not server._messaging_partner_feature_enabled("signal"):
                return server._messaging_partner_disabled_response("signal")
            # If SignalService module not available, we cannot start/restart
            if server.SignalService is None:
                return server.JSONResponse(
                    status_code=503,
                    content={"success": False, "error": "Signal service not available"}
                )

            try:
                server.LOGGER.info("Admin requested Signal service restart")
                # Start or restart regardless of current state
                await server.start_or_restart_signal()

                if server.STATE.signal_service is not None:
                    return {"success": True, "message": "Signal service restarted successfully"}
                else:
                    return server.JSONResponse(
                        status_code=500,
                        content={"success": False, "error": "Failed to restart Signal service"}
                    )
            except Exception as e:
                server.LOGGER.error(f"Error restarting Signal service (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        routes.update({
            "admin_get_signal_qr_code": admin_get_signal_qr_code,
            "admin_get_signal_status": admin_get_signal_status,
            "admin_get_signal_messages": admin_get_signal_messages,
            "admin_get_signal_detailed_status": admin_get_signal_detailed_status,
            "admin_get_signal_device_name": admin_get_signal_device_name,
            "admin_cleanup_signal": admin_cleanup_signal,
            "admin_send_signal_message": admin_send_signal_message,
            "admin_restart_signal_service": admin_restart_signal_service,
        })

    if server.WhatsAppService is not None:
        @admin_app.get("/api/whatsapp/qr")
        async def admin_get_whatsapp_qr_code(request: Request):
            """Get QR code for WhatsApp pairing (admin endpoint)."""
            auth_response = server._require_api_login_json(request)
            if auth_response is not None:
                return auth_response
            if not server._messaging_partner_feature_enabled("whatsapp"):
                return server._messaging_partner_disabled_response("whatsapp")
            if server.STATE.whatsapp_service is None:
                return server.JSONResponse(
                    status_code=503,
                    content={"success": False, "error": "WhatsApp service not available"}
                )

            try:
                svc = server.STATE.whatsapp_service
                if svc is None:
                    return server.JSONResponse(
                        status_code=503,
                        content={"success": False, "error": "WhatsApp service not available"}
                    )
                status = await svc.get_status()
                transport_ready = bool(status.get("connection_healthy")) or (
                    bool(status.get("ready")) and bool(status.get("phone_number"))
                )
                if status.get("paired") or transport_ready:
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Device is already paired. Use cleanup to re-pair."}
                )
                whatsapp_config = server.STATE.config.get("whatsapp", {}) if server.STATE.config else {}
                device_name = whatsapp_config.get("device_name", "AutoYou-WhatsApp")
                if not status.get("last_qr_available") and status.get("node_process_running"):
                    await svc.wait_for_qr_code()
                    status = await svc.get_status()
                    transport_ready = bool(status.get("connection_healthy")) or (
                        bool(status.get("ready")) and bool(status.get("phone_number"))
                    )
                    if status.get("paired") or transport_ready:
                        return server.JSONResponse(
                            status_code=400,
                            content={"success": False, "error": "Device is already paired. Use cleanup to re-pair."}
                        )
                svc = server.STATE.whatsapp_service
                if svc is None:
                    return server.JSONResponse(
                        status_code=503,
                        content={"success": False, "error": "WhatsApp service not available"}
                    )
                qr_url = await svc.get_qr_code_data_url()
                if qr_url:
                    return {"success": True, "qr_url": qr_url, "device_name": device_name}
                elif status.get("node_process_running"):
                    return server.JSONResponse(
                        status_code=202,
                        content={
                            "success": False,
                            "pending": True,
                            "error": "QR code not ready yet. WhatsApp is still initializing.",
                            "status": status.get("status"),
                        }
                    )
                else:
                    return server.JSONResponse(
                        status_code=500,
                        content={"success": False, "error": "Failed to generate QR code"}
                    )
            except Exception as e:
                server.LOGGER.error(f"Error getting WhatsApp QR code (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @admin_app.get("/api/whatsapp/status")
        async def admin_get_whatsapp_status(request: Request):
            """Get WhatsApp service status (admin endpoint)."""
            auth_response = server._require_api_login_json(request)
            if auth_response is not None:
                return auth_response
            feature_enabled = server._messaging_partner_feature_enabled("whatsapp")
            if not feature_enabled and server.STATE.whatsapp_service is None:
                return {
                    "enabled": False,
                    "feature_enabled": False,
                    "status": "disabled",
                    "paired": False,
                    "phone_number": None,
                    "error": server._messaging_partner_disabled_message("whatsapp"),
                }
            if server.STATE.whatsapp_service is None:
                whatsapp_config = server.STATE.config.get("whatsapp", {}) if server.STATE.config else {}
                enabled = whatsapp_config.get("enabled", False)
                return {
                    "enabled": enabled,
                    "status": "stopped",
                    "paired": False,
                    "phone_number": None,
                    "error": "Service not started" if enabled else "Service disabled"
                }

            try:
                cached_status = server._get_cached_admin_status("whatsapp_status")
                if cached_status is not None:
                    if isinstance(cached_status, dict):
                        cached_status["enabled"] = True
                    return cached_status
                status = await server._check_and_update_whatsapp_pairing_status(
                    settle_delay_seconds=0,
                    persist_pairing=False,
                )
                server._set_cached_admin_status("whatsapp_status", status, ttl_seconds=1.0)
                return status
            except Exception as e:
                server.LOGGER.error(f"Error getting WhatsApp status (admin): {e}")
                return {
                    "enabled": True,
                    "status": "error",
                    "paired": False,
                    "phone_number": None,
                    "error": str(e)
                }

        @admin_app.post("/api/whatsapp/reset")
        async def admin_reset_whatsapp_session(request: Request):
            """Reset WhatsApp session (admin endpoint)."""
            auth_response = server._require_api_login_json(request)
            if auth_response is not None:
                return auth_response
            if not server._messaging_partner_feature_enabled("whatsapp"):
                return server._messaging_partner_disabled_response("whatsapp")
            try:
                whatsapp_config = server.STATE.config.get("whatsapp", {}) if server.STATE.config else {}
                service = server.STATE.whatsapp_service
                if service is None:
                    websocket_port = int(whatsapp_config.get("websocket_port", server.os.getenv("WHATSAPP_WS_PORT", "8083")))
                    device_name = whatsapp_config.get("device_name", "AutoYou-WhatsApp")
                    ai_api_url = whatsapp_config.get("ai_api_url", "http://localhost:8081/api/chat")
                    service = server.WhatsAppService(
                        websocket_port=websocket_port,
                        device_name=device_name,
                        ai_api_url=ai_api_url,
                    )

                success = await service.cleanup_session()
                if not success:
                    return server.JSONResponse(
                        status_code=500,
                        content={"success": False, "error": "Failed to fully reset WhatsApp session"}
                    )

                if server.STATE.whatsapp_service is service:
                    server.STATE.whatsapp_service = None

                # Update config to reflect unpaired status
                if server.STATE.config:
                    whatsapp_config = server.STATE.config.setdefault("whatsapp", {})
                    whatsapp_config["paired"] = False
                    whatsapp_config["phone_number"] = ""
                    if server._can_persist_config():
                        server._persist_state_config(server.STATE.config)

                restarted = False
                if whatsapp_config.get("enabled", False):
                    await server.start_or_restart_whatsapp()
                    restarted = server.STATE.whatsapp_service is not None
                    if not restarted:
                        return server.JSONResponse(
                            status_code=500,
                            content={
                                "success": False,
                                "error": "WhatsApp session was reset, but the service failed to restart",
                            },
                        )

                return {
                    "success": True,
                    "message": "WhatsApp session reset successfully",
                    "restarted": restarted,
                }
            except Exception as e:
                server.LOGGER.error(f"Error resetting WhatsApp session (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @admin_app.post("/api/whatsapp/send")
        async def admin_send_whatsapp_message(request: Request):
            """Send WhatsApp message (admin endpoint)."""
            if not server._messaging_partner_feature_enabled("whatsapp"):
                return server._messaging_partner_disabled_response("whatsapp")
            auth_response = server._require_api_login_json(request)
            if auth_response is not None:
                return auth_response
            if server.STATE.whatsapp_service is None:
                return server.JSONResponse(
                    status_code=503,
                    content={"success": False, "error": "WhatsApp service not available"}
                )

            try:
                data = await request.json()
                to = str(data.get("to") or "").strip()
                message = str(data.get("message") or "").strip()
                context = data.get("context")
                attachments_payload = data.get("attachments")
                if context is not None and not isinstance(context, list):
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Field 'context' must be a JSON array when provided"}
                    )
                if attachments_payload is not None and not isinstance(attachments_payload, list):
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Field 'attachments' must be a JSON array when provided"}
                    )

                media_attachments = server._extract_admin_media_attachments(
                    context,
                    attachments_payload,
                    source="admin_whatsapp_send",
                )
                if not to or (not message and not media_attachments):
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing 'to' and message/media attachment field"}
                    )

                if media_attachments:
                    captioned_attachments = server._caption_first_media_attachment(media_attachments, message)
                    send_media = getattr(server.STATE.whatsapp_service, "send_media_attachments", None)
                    if not callable(send_media):
                        return server.JSONResponse(
                            status_code=503,
                            content={"success": False, "error": "WhatsApp media send is not available"}
                        )
                    if await send_media(to, captioned_attachments):
                        return {
                            "success": True,
                            "message": "Media message sent successfully",
                            "media_attachments": len(media_attachments),
                        }
                    if not message:
                        return server.JSONResponse(
                            status_code=500,
                            content={"success": False, "error": "No WhatsApp media attachments could be sent"}
                        )

                success = await server.STATE.whatsapp_service.send_message(to, message)
                if success:
                    return {"success": True, "message": "Message sent successfully"}
                else:
                    return server.JSONResponse(
                        status_code=500,
                        content={"success": False, "error": "Failed to send message"}
                    )
            except Exception as e:
                server.LOGGER.error(f"Error sending WhatsApp message (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @admin_app.post("/api/whatsapp/restart")
        async def admin_restart_whatsapp_service(request: Request):
            """Restart WhatsApp service (admin endpoint)."""
            auth_response = server._require_api_login_json(request)
            if auth_response is not None:
                return auth_response
            if not server._messaging_partner_feature_enabled("whatsapp"):
                return server._messaging_partner_disabled_response("whatsapp")
            if server.STATE.whatsapp_service is None:
                return server.JSONResponse(
                    status_code=503,
                    content={"success": False, "error": "WhatsApp service not available"}
                )

            try:
                server.LOGGER.info("Admin requested WhatsApp service restart")
                success = await server.STATE.whatsapp_service.restart()

                if success:
                    return {"success": True, "message": "WhatsApp service restarted successfully"}
                else:
                    return server.JSONResponse(
                        status_code=500,
                        content={"success": False, "error": "Failed to restart WhatsApp service"}
                    )
            except Exception as e:
                server.LOGGER.error(f"Error restarting WhatsApp service (admin): {e}")
                return server.JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @admin_app.post("/webhook/whatsapp")
        async def whatsapp_webhook(request: Request):
            """Receive webhook events from the avoylenko/wwebjs-api Docker container."""
            # The container is co-located, so network position is the boundary.
            # Without this an internet caller could inject forged inbound
            # messages straight into the WhatsApp event handler - and from
            # there into whatever the agent does with incoming messages.
            auth_response = server._require_local_service_callback_json(
                request, token_env="AUTOYOU_WHATSAPP_WEBHOOK_TOKEN"
            )
            if auth_response is not None:
                return auth_response
            try:
                payload = await request.json()
            except Exception:
                return server.JSONResponse(status_code=400, content={"error": "Invalid JSON"})

            service = server.STATE.whatsapp_service
            if service is not None:
                handle_fn = getattr(service, "handle_webhook_event", None)
                if handle_fn is not None:
                    try:
                        await handle_fn(payload)
                    except Exception as _e:
                        server.LOGGER.warning("WhatsApp webhook handler error: %s", _e)

            return {"status": "ok"}

        routes.update({
            "admin_get_whatsapp_qr_code": admin_get_whatsapp_qr_code,
            "admin_get_whatsapp_status": admin_get_whatsapp_status,
            "admin_reset_whatsapp_session": admin_reset_whatsapp_session,
            "admin_send_whatsapp_message": admin_send_whatsapp_message,
            "admin_restart_whatsapp_service": admin_restart_whatsapp_service,
            "whatsapp_webhook": whatsapp_webhook,
        })

    @admin_app.post("/api/telegram/send")
    async def admin_send_telegram_message(request: Request):
        """Send Telegram message (admin endpoint)."""
        auth_response = server._require_api_login_json(request)
        if auth_response is not None:
            return auth_response

        bot = server._get_active_telegram_bot()
        if bot is None:
            return server.JSONResponse(
                status_code=503,
                content={"success": False, "error": "Telegram bot not available"}
            )

        try:
            data = await request.json()
            message = str(data.get("message") or "").strip()
            context = data.get("context")
            attachments_payload = data.get("attachments")
            if context is not None and not isinstance(context, list):
                return server.JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Field 'context' must be a JSON array when provided"}
                )
            if attachments_payload is not None and not isinstance(attachments_payload, list):
                return server.JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Field 'attachments' must be a JSON array when provided"}
                )

            try:
                chat_id = int(str(data.get("chat_id") or "").strip())
            except Exception:
                return server.JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Missing or invalid 'chat_id' field"}
                )

            reply_to_message_id = data.get("reply_to_message_id")
            normalized_reply_to_message_id = None
            if reply_to_message_id not in (None, ""):
                try:
                    normalized_reply_to_message_id = int(str(reply_to_message_id).strip())
                except Exception:
                    return server.JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Invalid 'reply_to_message_id' field"}
                    )

            media_attachments: server.List[server.Dict[str, server.Any]] = []
            if context is not None or attachments_payload is not None:
                try:
                    from shared.media_messaging import extract_media_reply_attachments

                    if context is not None:
                        media_attachments.extend(
                            extract_media_reply_attachments(context, source="admin_telegram_send")
                        )
                    if attachments_payload is not None:
                        media_attachments.extend(
                            extract_media_reply_attachments(
                                {"attachments": attachments_payload},
                                source="admin_telegram_send",
                            )
                        )
                except Exception as exc:
                    server.LOGGER.warning("Failed to extract Telegram media attachments: %s", exc)

            if not message and not media_attachments:
                return server.JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Missing 'message' or media attachment field"}
                )

            if media_attachments:
                try:
                    from shared.media_messaging import attachment_mimetype, load_attachment_bytes
                    from shared.openclaw_gateway import safe_filename
                except Exception as exc:
                    return server.JSONResponse(
                        status_code=500,
                        content={"success": False, "error": f"Telegram media helpers unavailable: {exc}"}
                    )

                sent_or_queued = False
                skipped: server.List[str] = []
                pending_before = len(server._telegram_pending_media_replies())
                for index, attachment in enumerate(media_attachments):
                    media_bytes, error = load_attachment_bytes(attachment)
                    if media_bytes is None:
                        skipped.append(str(error or "attachment data unavailable"))
                        continue
                    mimetype = attachment_mimetype(attachment)
                    filename = safe_filename(attachment.get("filename") or attachment.get("path"), mimetype)
                    caption = str(attachment.get("caption") or "").strip()
                    if index == 0 and message and not caption:
                        caption = message
                    if await server._send_telegram_media_payload(
                        bot,
                        chat_id=chat_id,
                        media_bytes=media_bytes,
                        filename=filename,
                        mimetype=mimetype,
                        caption=caption,
                        reply_to_message_id=normalized_reply_to_message_id,
                        queue_on_failure=True,
                    ):
                        sent_or_queued = True
                    elif len(server._telegram_pending_media_replies()) > pending_before:
                        sent_or_queued = True
                        pending_before = len(server._telegram_pending_media_replies())

                if sent_or_queued:
                    return {
                        "success": True,
                        "message": "Media message sent successfully",
                        "media_attachments": len(media_attachments),
                        "skipped": skipped,
                    }
                if not message:
                    return server.JSONResponse(
                        status_code=500,
                        content={
                            "success": False,
                            "error": "No Telegram media attachments could be sent",
                            "skipped": skipped,
                        }
                    )

            await server._send_telegram_text_via_bot(
                bot,
                chat_id,
                message,
                split_text=True,
                reply_to_message_id=normalized_reply_to_message_id,
            )
            return {"success": True, "message": "Message sent successfully"}
        except Exception as e:
            server.LOGGER.error(f"Error sending Telegram message (admin): {e}")
            return server.JSONResponse(
                status_code=500,
                content={"success": False, "error": str(e)}
            )

    routes["admin_send_telegram_message"] = admin_send_telegram_message

    return routes
