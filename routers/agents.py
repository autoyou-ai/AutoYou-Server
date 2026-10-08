# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-a8b9cd0a195d9bffe3078258

"""Agents HTTP routes for the full AutoYou server."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shared.ollama_capabilities import THINKING_LEVELS
from shared.ollama_gateway import probe_ollama, reset_ollama_model_cache
from shared.odysseus_gateway import probe_odysseus, reset_odysseus_cache

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-a8b9cd0a195d9bffe3078258"


def _agent_registry_storage_error(exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "success": False,
            "error": (
                "Agent installation is blocked because the protected install registry cannot be "
                "decrypted. Restore the matching Secure Professional Maximus key or recover the "
                "registry, then retry."
            ),
            "storage_error": str(exc),
        },
    )


def register_routes(
    admin_app: FastAPI,
    auth_app: FastAPI,
    server: Any,
) -> Dict[str, Callable[..., Any]]:
    @admin_app.post("/api/agents/frontend")
    async def admin_set_agent_frontend_state(request: Request):
        """Enable or disable a browser-facing agent control from Agent Studio."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error

        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

        try:
            agent_name = server._normalize_agent_directory_name(payload.get("agent_name"))
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})

        route_mode_provided = False
        enabled = server._get_agent_frontend_enabled(agent_name, cfg=(server.STATE.config or {}))
        try:
            if agent_name == "internet_agent":
                enabled = server._coerce_enabled_flag(payload.get("enabled"))
                if not server._is_internet_agent_installed():
                    return JSONResponse(
                        status_code=409,
                        content={
                            "success": False,
                            "error": "Internet Search is not installed. Install it and restart AutoYou AI before using this toggle.",
                        },
                )
                _, runtime_sync = await server._set_internet_search_enabled_live(enabled)
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
            else:
                enabled_provided = "enabled" in payload
                route_mode_provided = "route_mode" in payload
                cfg_for_update: Optional[Dict[str, Any]] = None
                manifest_entries = server.discover_frontend_manifests(
                    agents_root=server._AUTOYOU_AGENTS_ROOT,
                    agent_names=[agent_name],
                    proxy_ports={
                        name: int(port)
                        for name, port in (server.STATE.dynamic_agent_proxy_ports or {}).items()
                        if port
                    },
                    browser_base_url=f"http://127.0.0.1:{server._get_autoyou_browser_forward_port_from_state()}",
                )
                if agent_name != "admin_agent" and not manifest_entries:
                    return JSONResponse(
                        status_code=404,
                        content={
                            "success": False,
                            "error": f"No frontend-capable UI is registered for '{agent_name}'.",
                        },
                    )
                block_reason = server._config_write_block_reason()
                if block_reason:
                    return server._json_config_write_blocked_response(block_reason)

                cfg_for_update = server._loaded_config_for_update()
                enabled = (
                    server._coerce_enabled_flag(payload.get("enabled"))
                    if enabled_provided
                    else server._get_agent_frontend_enabled(agent_name, cfg=cfg_for_update)
                )
                if route_mode_provided:
                    cfg_for_update = server._set_agent_frontend_route_mode(
                        agent_name,
                        payload.get("route_mode"),
                        cfg=cfg_for_update,
                    )
                if enabled_provided:
                    cfg_for_update = server._set_agent_frontend_enabled(
                        agent_name,
                        enabled,
                        cfg=cfg_for_update,
                    )
                server._persist_state_config(cfg_for_update)

                if agent_name == "admin_agent":
                    server._register_admin_frontend_proxy()
                elif (
                    agent_name in server.MANAGED_FRONTEND_APPS
                    or agent_name in server._managed_frontend_runtime_specs()
                ):
                    await server.sync_managed_frontend_backends()

            result = server._build_agent_builder_listing_payload()
            server._sync_frontend_registry_from_builder_payload(result)
            detail = result.get("agent_details", {}).get(agent_name, {})
            control = detail.get("frontend_control") or {}
            return {
                "success": True,
                "agent_name": agent_name,
                "enabled": bool((control or {}).get("enabled", enabled if "enabled" in locals() else False)),
                "route_mode": (control or {}).get("route_mode"),
                "requires_restart": False,
                "message": (
                    f"{control.get('label') or 'Agent website'} route updated."
                    if route_mode_provided and not enabled_provided
                    else (
                        f"{control.get('label') or 'Agent website'} enabled."
                        if (control or {}).get("enabled", enabled if "enabled" in locals() else False)
                        else f"{control.get('label') or 'Agent website'} disabled."
                    )
                ),
                "detail": detail,
                "payload": result,
            }
        except Exception as exc:
            if isinstance(exc, server.ConfigWriteBlocked):
                return server._json_config_write_blocked_response(str(exc))
            server.LOGGER.error("Failed to update frontend state for %s: %s", agent_name, exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.get("/api/agent-websites/security")
    async def admin_get_agent_websites_security(request: Request):
        """Return the global agent-websites security configuration."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        from autoyou_agents.shared_tools import scheduler_mission_control as _mission_control

        cfg = server.STATE.config or server._default_config()
        website_cfg = cfg.get("agent_websites", {}) if isinstance(cfg, dict) else {}
        totp_caps = server._describe_totp_capabilities(cfg)
        return JSONResponse({
            "success": True,
            "require_otp": bool(website_cfg.get("require_otp", False)),
            "disable_otp": bool(website_cfg.get("disable_otp", False)),
            "shared_session_enabled": bool(website_cfg.get("shared_session_enabled", False)),
            "shared_session_ttl_days": _mission_control._normalize_session_ttl_days(
                website_cfg.get("shared_session_ttl_days")
            ),
            "totp_configured": totp_caps.get("totp_configured", False),
        })

    @admin_app.post("/api/agent-websites/security")
    async def admin_set_agent_websites_security(request: Request):
        """Update the global agent-websites OTP requirement + shared-session toggles."""
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
        from autoyou_agents.shared_tools import scheduler_mission_control as _mission_control

        cfg = server._loaded_config_for_update()
        website_cfg = cfg.setdefault("agent_websites", {})
        previous_disable_otp = bool(website_cfg.get("disable_otp", False))
        previous_shared_session = bool(website_cfg.get("shared_session_enabled", False))
        disable_otp = payload.get("disable_otp", website_cfg.get("disable_otp", False))
        if type(disable_otp) is not bool:
            return JSONResponse(status_code=400, content={"success": False, "error": "disable_otp must be a boolean"})
        require_otp = bool(payload.get("require_otp", False)) and not disable_otp
        shared_session_enabled = bool(payload.get("shared_session_enabled", False))
        shared_session_ttl_days = _mission_control._normalize_session_ttl_days(
            payload.get("shared_session_ttl_days")
        )
        website_cfg["require_otp"] = require_otp
        website_cfg["disable_otp"] = disable_otp
        website_cfg["shared_session_enabled"] = shared_session_enabled
        website_cfg["shared_session_ttl_days"] = shared_session_ttl_days
        server._persist_state_config(cfg)
        server.STATE.config = cfg
        if disable_otp != previous_disable_otp:
            _mission_control.clear_all_agent_sessions()
        elif previous_shared_session and not shared_session_enabled:
            _mission_control.clear_shared_agent_sessions()
        response = JSONResponse({
            "success": True,
            "require_otp": require_otp,
            "disable_otp": disable_otp,
            "shared_session_enabled": shared_session_enabled,
            "shared_session_ttl_days": shared_session_ttl_days,
        })
        if shared_session_enabled and not disable_otp:
            _mission_control._maybe_upgrade_shared_session_cookie(response, request)
        return response

    @admin_app.post("/api/agent-websites/{agent_name}/auth")
    async def admin_set_agent_website_auth(agent_name: str, request: Request):
        """Gate one agent website behind OTP, open it, or reset it to the default policy.

        Websites such as ``notes_agent`` and ``page_agent`` ship open and are
        exempt from the global "require OTP" switch, so this is the one-click way
        to put a single website behind the authenticator without changing the
        others. ``mode`` is ``totp`` (always ask), ``open`` (never ask) or
        ``default`` (drop the override and follow the website's own default and
        the global policy).
        """
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
        mode = str((payload or {}).get("mode") or "").strip().lower() if isinstance(payload, dict) else ""
        if mode not in {"totp", "open", "default"}:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "mode must be 'totp', 'open' or 'default'"},
            )
        try:
            name = server._normalize_agent_directory_name(agent_name)
        except ValueError:
            name = ""
        cfg_now = server.STATE.config or server._default_config()
        known_websites = {
            str(route.get("agent_name") or "").strip()
            for route in server._build_agent_website_routes(cfg_now)
            if route.get("agent_name")
        }
        if not name or name not in known_websites:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": "Unknown agent website"},
            )
        from autoyou_agents.shared_tools import scheduler_mission_control as _mission_control

        if mode == "totp" and _mission_control._global_agent_website_otp_disabled():
            return JSONResponse(
                status_code=409,
                content={
                    "success": False,
                    "error": "OTP is disabled for all agent websites. Enable agent website OTP before requiring it for one website.",
                },
            )
        if mode == "totp" and not server._describe_totp_capabilities(cfg_now).get("totp_configured", False):
            if not server.agent_has_assigned_2fa_profile(name):
                return JSONResponse(
                    status_code=409,
                    content={
                        "success": False,
                        "error": (
                            "Set up the shared authenticator first (Security), otherwise nobody "
                            "could sign in to this website."
                        ),
                    },
                )
        cfg = server._loaded_config_for_update()
        security_cfg = cfg.get(_mission_control.UI_SECURITY_CONFIG_KEY)
        if not isinstance(security_cfg, dict):
            security_cfg = {}
            cfg[_mission_control.UI_SECURITY_CONFIG_KEY] = security_cfg
        entry = dict(security_cfg.get(name) or {}) if isinstance(security_cfg.get(name), dict) else {}
        if mode == "default":
            entry.pop("auth_mode", None)
        else:
            entry["auth_mode"] = mode
        if entry:
            security_cfg[name] = entry
        else:
            security_cfg.pop(name, None)
        server._persist_state_config(cfg)
        server.STATE.config = cfg
        settings = _mission_control._get_agent_security_settings(name)
        return JSONResponse({
            "success": True,
            "agent_name": name,
            "override": mode,
            "auth_mode": settings.get("auth_mode"),
            "global_otp_disabled": _mission_control._global_agent_website_otp_disabled(),
        })

    @admin_app.post("/api/agent-websites/sessions/sign-out-all")
    async def admin_sign_out_all_agent_sessions(request: Request):
        """Kill switch: invalidate every outstanding agent-website session token.

        Covers per-agent, chat, and the opt-in shared session in one call, so a
        lost/stolen device's session stops working immediately rather than
        waiting for cookie expiry.
        """
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        from autoyou_agents.shared_tools import scheduler_mission_control as _mission_control

        return JSONResponse({
            "success": True,
            "sessions_invalidated": _mission_control.clear_all_agent_sessions(),
        })

    @admin_app.get("/api/agent-websites/default")
    async def admin_get_default_agent_website(request: Request):
        """Return the current default/home agent website and selectable options."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        cfg = server.STATE.config or server._default_config()
        options = [{"agent_name": "agent_websites", "title": "Agent Apps (store)", "kind": "directory"}]
        for route in server._build_agent_website_routes(cfg):
            name = str(route.get("agent_name") or "").strip()
            if name:
                options.append({"agent_name": name, "title": route.get("title") or name, "kind": "agent_frontend"})
        return JSONResponse({
            "success": True,
            "default_agent_website": server._resolve_default_agent_website(cfg),
            "default_website": server._build_default_website_payload(cfg),
            "options": options,
        })

    @admin_app.post("/api/agent-websites/default")
    async def admin_set_default_agent_website(request: Request):
        """Set the default/home agent website every WebRTC browser client lands on.

        Accepts an installed agent website ``agent_name`` or the sentinel
        ``agent_websites`` (the directory page). Persists to config and re-stamps the
        frontend registry so the page-service ``/`` redirect and clients pick it up.
        """
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
        requested = str((payload or {}).get("agent_name") or "").strip()
        if not requested:
            return JSONResponse(status_code=400, content={"success": False, "error": "agent_name is required"})

        valid = set(server._AGENT_WEBSITES_DIRECTORY_SENTINELS)
        try:
            for entry in server.load_frontend_registry().get("frontends", []) or []:
                name = str((entry or {}).get("agent_name") or "").strip()
                if name:
                    valid.add(name)
        except Exception:
            pass
        if requested not in valid:
            return JSONResponse(status_code=400, content={"success": False, "error": f"Unknown agent website: {requested}"})

        normalized = "agent_websites" if requested in server._AGENT_WEBSITES_DIRECTORY_SENTINELS else requested
        cfg = server._loaded_config_for_update()
        cfg.setdefault("autoyou_page", {})["default_agent_website"] = normalized
        server._persist_state_config(cfg)
        server.STATE.config = cfg
        # Re-stamp the registry so the page-service `/` redirect + clients update.
        try:
            server._sync_frontend_registry_from_builder_payload(server._build_agent_builder_listing_payload())
        except Exception as exc:
            server.LOGGER.warning("Failed to refresh registry after default-website change: %s", exc)
        return JSONResponse({
            "success": True,
            "default_agent_website": normalized,
            "default_website": server._build_default_website_payload(server.STATE.config or {}),
        })

    @admin_app.get("/api/bookmarks")
    async def admin_list_bookmarks(request: Request):
        """Return server-managed browser bookmarks exposed to paired clients."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        return JSONResponse(server._build_bookmarks_admin_payload())

    @admin_app.post("/api/bookmarks")
    async def admin_create_bookmark(request: Request):
        """Create a server-managed browser bookmark."""
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
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400, content={"success": False, "error": "Bookmark payload must be an object."})
        try:
            current = server._get_autoyou_bookmarks(server._loaded_config_for_update())
            created = server._normalize_bookmark_entries([payload])[0]
            existing_ids = {str(item.get("id") or "") for item in current}
            if created["id"] in existing_ids:
                created["id"] = server._normalize_bookmark_id("", fallback_url=created["url"], seen_ids=existing_ids)
            result = server._persist_autoyou_bookmarks(current + [created])
            result["bookmark"] = result["bookmarks"][-1] if result["bookmarks"] else created
            return JSONResponse(result)
        except Exception as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})

    @admin_app.patch("/api/bookmarks/{bookmark_id}")
    async def admin_update_bookmark(bookmark_id: str, request: Request):
        """Update a server-managed browser bookmark."""
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
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400, content={"success": False, "error": "Bookmark payload must be an object."})
        try:
            cfg = server._loaded_config_for_update()
            current = server._get_autoyou_bookmarks(cfg)
            bookmark_key = str(bookmark_id or "").strip()
            updated: List[Dict[str, Any]] = []
            replaced = False
            for item in current:
                if str(item.get("id") or "") != bookmark_key:
                    updated.append(item)
                    continue
                merged = dict(item)
                for field in ("title", "url", "description", "enabled"):
                    if field in payload:
                        merged[field] = payload[field]
                merged["id"] = item.get("id")
                normalized = server._normalize_bookmark_entries([merged])[0]
                normalized["id"] = str(item.get("id") or normalized.get("id") or "")
                updated.append(normalized)
                replaced = True
            if not replaced:
                return JSONResponse(status_code=404, content={"success": False, "error": "Bookmark not found."})
            result = server._persist_autoyou_bookmarks(updated)
            result["bookmark"] = next((item for item in result["bookmarks"] if item.get("id") == bookmark_key), None)
            return JSONResponse(result)
        except Exception as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})

    @admin_app.delete("/api/bookmarks/{bookmark_id}")
    async def admin_delete_bookmark(bookmark_id: str, request: Request):
        """Delete one server-managed browser bookmark."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        # from __debug_provenance_a__ import schedule
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)
        cfg = server._loaded_config_for_update()
        current = server._get_autoyou_bookmarks(cfg)
        bookmark_key = str(bookmark_id or "").strip()
        updated = [item for item in current if str(item.get("id") or "") != bookmark_key]
        if len(updated) == len(current):
            return JSONResponse(status_code=404, content={"success": False, "error": "Bookmark not found."})
        return JSONResponse(server._persist_autoyou_bookmarks(updated))

    @admin_app.delete("/api/bookmarks")
    @admin_app.post("/api/bookmarks/clear")
    async def admin_clear_bookmarks(request: Request):
        """Clear all server-managed browser bookmarks."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)
        return JSONResponse(server._persist_autoyou_bookmarks([]))

    @admin_app.get("/api/ai/openclaw/status")
    async def openclaw_gateway_status(request: Request):
        """Check whether the OpenClaw Gateway is reachable and return available models."""
        auth_response = server._require_api_login_json(request)
        if auth_response is not None:
            return auth_response
        try:
            import httpx as _httpx
            ai_prov = (server.STATE.config or {}).get("ai_provider", {})
            port  = int(ai_prov.get("openclaw_port", server.os.getenv("OPENCLAW_PORT", "18789")))
            token = ai_prov.get("openclaw_token", server.os.getenv("OPENCLAW_TOKEN", "")) or ""
            headers: dict = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            async with _httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"http://127.0.0.1:{port}/v1/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            models = [m.get("id", "") for m in (data.get("data") or [])]
            return JSONResponse({"running": True, "port": port, "models": models})
        except Exception as exc:
            port_val = (server.STATE.config or {}).get("ai_provider", {}).get("openclaw_port", 18789)
            return JSONResponse({"running": False, "port": port_val, "error": str(exc)})

    @admin_app.get("/api/ai/hermes/status")
    async def hermes_gateway_status(request: Request):
        """Check whether the Hermes Agent gateway is reachable and return available models."""
        auth_response = server._require_api_login_json(request)
        if auth_response is not None:
            return auth_response
        try:
            import httpx as _httpx
            ai_prov = (server.STATE.config or {}).get("ai_provider", {})
            port  = int(ai_prov.get("hermes_port", server.os.getenv("HERMES_PORT", "8642")))
            token = ai_prov.get("hermes_token", server.os.getenv("HERMES_TOKEN", "")) or ""
            headers: dict = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            async with _httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"http://127.0.0.1:{port}/v1/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            models = [m.get("id", "") for m in (data.get("data") or [])]
            return JSONResponse({"running": True, "port": port, "models": models})
        except Exception as exc:
            port_val = (server.STATE.config or {}).get("ai_provider", {}).get("hermes_port", 8642)
            return JSONResponse({"running": False, "port": port_val, "error": str(exc)})

    @admin_app.get("/api/ai/ollama/status")
    async def ollama_gateway_status(request: Request):
        """Return only sanitized native Ollama readiness for an authenticated admin."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        cfg = server.STATE.config or server._default_config()
        ollama_cfg = cfg.get("ollama", {}) if isinstance(cfg, dict) else {}
        result = await probe_ollama(str(ollama_cfg.get("api_base") or "http://127.0.0.1:11434"))
        return JSONResponse({"running": bool(result.get("available")), **result})

    @admin_app.get("/api/ai/odysseus/status")
    async def odysseus_gateway_status(request: Request):
        """Return sanitized Odysseus readiness without disclosing credentials."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        cfg = server.STATE.config or server._default_config()
        provider_cfg = cfg.get("ai_provider", {}) if isinstance(cfg, dict) else {}
        result = await probe_odysseus(
            str(provider_cfg.get("odysseus_api_base") or "http://127.0.0.1:7000"),
            token=str(provider_cfg.get("odysseus_token") or server.os.getenv("ODYSSEUS_API_TOKEN", "")),
        )
        return JSONResponse(result)

    @admin_app.post("/api/ai/restart")
    async def admin_restart_ai_agent(request: Request):
        """Apply the latest agent configuration to AutoYou AI."""
        # H-18: authenticated admin only. Restarts the agent runtime.
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        if server._is_native_gateway_provider():
            return {"success": True, "message": "Native gateway is active; no AutoYou AI worker is required."}
        try:
            server.refresh_agent_install_registry(agents_root=server._AUTOYOU_AGENTS_ROOT)
            if server._is_agent_process_running(server.STATE.agent_process):
                await server.restart_ai_agent_server()
                server.LOGGER.info("admin_restart_ai_agent: AutoYou AI process restarted")
                return {"success": True, "message": "AI agent runtime restarted successfully"}

            import autoyou_agents.agent as _agent_mod
            server.importlib.invalidate_caches()
            new_root = _agent_mod.initialize_root_agent()
            server.LOGGER.info(
                "admin_restart_ai_agent: in-process reload completed. root_agent=%s",
                new_root.name if new_root else None,
            )
            return {"success": True, "message": "AI agent reloaded in-process successfully"}
        except Exception as e:
            server.LOGGER.error("admin_restart_ai_agent failed: %s", e)
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.get("/api/model-behavior")
    async def get_model_behavior(request: Request):
        """Return current model behavior mode and resolved LiteLlm parameters."""
        # H-18: authenticated admin only. Exposes current AutoYou AI parameters.
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        modes = server._get_model_behavior_modes()
        root_cfg = server.STATE.config or server._default_config()
        cfg = root_cfg.get("model_behavior", {})
        mode = cfg.get("mode", "accurate")
        preset = modes.get(mode, modes["none"])
        # Merge advanced overrides (non-None values) on top of preset
        resolved = dict(preset["params"])
        for key in ("temperature", "top_p", "top_k", "repeat_penalty", "num_ctx"):
            val = cfg.get(key)
            if val is not None:
                try:
                    resolved[key] = float(val) if key != "num_ctx" else int(val)
                except (TypeError, ValueError):
                    pass
        effective_num_ctx, effective_num_ctx_source = server._resolve_effective_behavior_num_ctx(cfg)
        if effective_num_ctx is not None:
            resolved["num_ctx"] = effective_num_ctx
        selected_model = str(root_cfg.get("ollama", {}).get("model") or "").strip()
        thinking_capability: Dict[str, Any] = {
            "name": selected_model,
            "available": False,
            "supports_thinking": None,
            "thinking_levels": [],
            "capabilities": [],
            "capability_source": "unavailable",
        }
        if selected_model and str(root_cfg.get("ai_provider", {}).get("provider") or "ollama").lower() in {"ollama", "ollama_gateway"}:
            try:
                api_base = str(root_cfg.get("ollama", {}).get("api_base") or "http://localhost:11434")
                local_models = await server.asyncio.to_thread(
                    server.model_library_service.list_local_models,
                    api_base,
                )
                for local_model in local_models:
                    if str(local_model.get("name") or "").casefold() == selected_model.casefold():
                        thinking_capability = {
                            key: local_model.get(key)
                            for key in (
                                "name",
                                "available",
                                "supports_thinking",
                                "thinking_levels",
                                "capabilities",
                                "family",
                                "parameter_size",
                                "quantization_level",
                                "capability_source",
                                "capability_error",
                            )
                            if key in local_model
                        }
                        thinking_capability.setdefault("available", False)
                        break
            except Exception as exc:
                thinking_capability["capability_error"] = str(exc)
        return {
            "mode": mode,
            "label": preset["label"],
            "description": preset["description"],
            "preset_params": preset["params"],
            "resolved_params": resolved,
            "advanced_overrides": {k: cfg.get(k) for k in ("temperature", "top_p", "top_k", "repeat_penalty", "num_ctx")},
            "show_thinking": bool(cfg.get("show_thinking", False)),
            "thinking_level": cfg.get("thinking_level"),
            "selected_model": selected_model,
            "thinking_capability": thinking_capability,
            "runtime_defaults": {
                "num_ctx": effective_num_ctx,
                "num_ctx_source": effective_num_ctx_source,
            },
            "modes": {k: {"label": v["label"], "description": v["description"], "params": v["params"]}
                      for k, v in modes.items()},
        }

    @admin_app.post("/api/model-behavior")
    async def set_model_behavior(request: Request):
        """Update model behavior mode and/or advanced overrides, then hot-reload the AI agent."""
        # H-18: authenticated admin only. Writes persisted AI config.
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)
        try:
            modes = server._get_model_behavior_modes()
            body = await request.json()
            mode = str(body.get("mode", "none")).lower()
            if mode not in modes:
                return JSONResponse(status_code=400, content={"success": False, "error": f"Unknown mode '{mode}'. Valid: {list(modes)}"})

            cfg = server._loaded_config_for_update()
            mb = cfg.setdefault("model_behavior", {})
            mb["mode"] = mode

            # Advanced numeric overrides - null clears the override
            def _parse_numeric(val, cast):
                if val is None or str(val).strip() == "":
                    return None
                return cast(val)

            for key, cast in [("temperature", float), ("top_p", float), ("top_k", float),
                               ("repeat_penalty", float), ("num_ctx", int)]:
                if key in body:
                    try:
                        mb[key] = _parse_numeric(body[key], cast)
                    except (TypeError, ValueError) as e:
                        return JSONResponse(status_code=400, content={"success": False, "error": f"Invalid {key}: {e}"})

            if "show_thinking" in body:
                mb["show_thinking"] = bool(body["show_thinking"])

            if "thinking_level" in body:
                raw_thinking_level = body.get("thinking_level")
                normalized_thinking_level = str(raw_thinking_level or "").strip().lower()
                if normalized_thinking_level in {"", "auto", "default"}:
                    mb["thinking_level"] = None
                elif normalized_thinking_level not in THINKING_LEVELS:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "success": False,
                            "error": f"Invalid thinking_level. Valid values: auto, {', '.join(THINKING_LEVELS)}",
                        },
                    )
                else:
                    mb["thinking_level"] = normalized_thinking_level

            # Persist to the active config store
            try:
                server._persist_state_config(cfg)
            except Exception as save_err:
                server.LOGGER.warning("model_behavior: config save failed: %s", save_err)
            server.LOGGER.info(
                "model_behavior updated: mode=%s overrides=%s show_thinking=%s thinking_level=%s",
                mode,
                {k: mb.get(k) for k in ("temperature", "top_p", "top_k", "repeat_penalty", "num_ctx")},
                mb.get("show_thinking", False),
                mb.get("thinking_level"),
            )

            # Native routes read this config for each request.  Do not import or
            # warm AutoYou AI merely to apply a direct gateway setting.
            if server._is_native_gateway_provider():
                reset_ollama_model_cache()
                reset_odysseus_cache()
                reload_result = {"success": True, "mode": "native_gateway"}
            else:
                reload_result: Dict[str, Any] = {"success": False, "error": "reload skipped"}
                try:
                    import importlib
                    import autoyou_agents.agent as _agent_mod
                    importlib.invalidate_caches()
                    new_root = _agent_mod.initialize_root_agent()
                    reload_result = {"success": True, "agent": new_root.name if new_root else None}
                    server.LOGGER.info("model_behavior: AI agent hot-reloaded with new params")
                except Exception as reload_err:
                    reload_result = {"success": False, "error": str(reload_err)}
                    server.LOGGER.warning("model_behavior: AI agent reload failed: %s", reload_err)

            return {"success": True, "mode": mode, "reload": reload_result}
        except Exception as e:
            server.LOGGER.error("set_model_behavior failed: %s", e)
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.post("/api/builder/create")
    async def admin_builder_create(request: Request):
        """Scaffold a new workspace draft for the agent studio.

        Expected JSON body::

            {
                "name": "weather_agent",
                "description": "Fetches current weather from OpenWeatherMap.",
                "tool_name": "get_weather",
                "tool_description": "Return the current weather for a city."
            }
        """
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

        name = (payload.get("name") or "").strip()
        description = (payload.get("description") or "").strip()
        tool_name = (payload.get("tool_name") or "handle_request").strip()
        tool_description = (payload.get("tool_description") or description).strip()

        if not name or not description:
            return JSONResponse(status_code=400, content={"success": False, "error": "'name' and 'description' are required"})

        try:
            scaffold_result = server.scaffold_agent_draft(
                name,
                description,
                tool_name,
                tool_description,
                agents_root=server._workspace_agents_root(),
            )
            agent_name = scaffold_result["agent_name"]
            return server._build_agent_workbench_success_response(
                agent_name=agent_name,
                message=(
                    f"Workspace draft '{agent_name}' created. "
                    "Use the Instructions and Frontend workbenches to continue shaping it before publishing."
                ),
                requires_restart=False,
                extra={
                    "draft_dir": scaffold_result.get("draft_dir"),
                    "files_created": scaffold_result.get("created_files"),
                    "installed": False,
                    "workspace_only": False,
                },
            )
        except FileExistsError as exc:
            return JSONResponse(status_code=409, content={"success": False, "error": str(exc)})
        except Exception as e:
            server.LOGGER.error("admin_builder_create failed: %s", e)
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.get("/api/builder/status/{agent_name}")
    async def admin_builder_status(agent_name: str, request: Request):
        """Return detailed studio state for a specific agent."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            detail_payload = server._build_agent_workbench_detail_payload(agent_name)
            if detail_payload.get("status") != "success":
                return JSONResponse(status_code=404, content={"success": False, "error": detail_payload.get("error") or "Agent not found"})
            return {
                "success": True,
                "agent_name": detail_payload.get("agent_name"),
                "detail": detail_payload.get("detail"),
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.get("/api/builder/agents")
    async def admin_builder_list_agents(request: Request):
        """List live agents and workspace drafts for the admin studio."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            result = server._build_agent_builder_listing_payload()
            if result.get("status") == "success":
                server._sync_frontend_registry_from_builder_payload(result)
            return result
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.get("/api/agents/manage")
    async def admin_manage_agents(request: Request):
        """Return the overview payload for the agent studio."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            result = server._build_agent_builder_listing_payload()
            if result.get("status") == "success":
                server._sync_frontend_registry_from_builder_payload(result)
            return result
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @admin_app.post("/api/agents/draft/{agent_name}/discard")
    async def admin_discard_agent_draft(agent_name: str, request: Request):
        """Discard a workspace draft agent (called by Agent Builder UI)."""
        auth_error = server._require_loopback_or_token(request)
        if auth_error:
            return auth_error
        try:
            result = server.delete_agent_draft(
                agent_name,
                agents_root=server._workspace_agents_root(),
            )
            payload = server._build_agent_builder_listing_payload()
            server._sync_frontend_registry_from_builder_payload(payload)
            return JSONResponse({
                "success": True,
                "agent_name": result["agent_name"],
                "message": f"Discarded workspace draft '{result['agent_name']}' .",
                "requires_restart": False,
                "payload": payload,
            })
        except FileNotFoundError as exc:
            return JSONResponse(status_code=404, content={"success": False, "error": str(exc)})
        except Exception as exc:
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/agents/draft/{agent_name}/publish")
    async def admin_publish_agent_draft(agent_name: str, request: Request):
        """Copy workspace draft files into the live agents directory (called by Agent Builder UI)."""
        auth_error = server._require_loopback_or_token(request)
        if auth_error:
            return auth_error
        try:
            result = server.publish_agent_draft(
                agent_name,
                agents_root=server._workspace_agents_root(),
            )
            payload = server._build_agent_builder_listing_payload()
            server._sync_frontend_registry_from_builder_payload(payload)
            return JSONResponse({
                "success": True,
                "agent_name": result["agent_name"],
                "published_files": result.get("published_files", []),
                "message": f"Published workspace draft '{result['agent_name']}' to agents directory.",
                "payload": payload,
            })
        except PermissionError as exc:
            return JSONResponse(status_code=403, content={"success": False, "error": str(exc), "compiled_block": True})
        except FileNotFoundError as exc:
            return JSONResponse(status_code=404, content={"success": False, "error": str(exc)})
        except ValueError as exc:
            return JSONResponse(status_code=422, content={"success": False, "error": str(exc)})
        except Exception as exc:
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/agents/install")
    async def admin_install_agent(request: Request):
        """Mark an agent installed and register its runtime metadata."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

        try:
            agent_name = server._normalize_agent_directory_name(payload.get("agent_name"))
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})

        if not server.can_install_agent_in_runtime(agent_name, compiled=server.is_compiled()):
            result = server._build_agent_builder_listing_payload()
            return JSONResponse(
                status_code=409,
                content={
                    "success": False,
                    "error": server._workspace_agent_runtime_block_reason(agent_name),
                    "detail": result.get("agent_details", {}).get(agent_name, {}),
                    "payload": result,
                },
            )

        try:
            available_agents = set(
                server.refresh_agent_install_registry(agents_root=server._AUTOYOU_AGENTS_ROOT)
                .get("agents", {})
                .keys()
            )
        except server.SecureStorageError as exc:
            return _agent_registry_storage_error(exc)
        if agent_name not in available_agents:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": f"Agent '{agent_name}' was not found on disk."},
            )

        description = str(payload.get("description") or server._load_agent_prompt_description(agent_name)).strip()
        try:
            from autoyou_agents.agent_builder_agent.agent import patch_root_agent

            patch_result = patch_root_agent(agent_name, description)
            if patch_result.get("status") != "success":
                return JSONResponse(status_code=422, content={"success": False, "error": patch_result.get("message"), "detail": patch_result})
        except Exception as exc:
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

        await server.sync_managed_frontend_backends()
        result = server._build_agent_builder_listing_payload()
        server._sync_frontend_registry_from_builder_payload(result)

        try:
            frontends = result.get("frontends", [])
            agent_frontend = next((f for f in frontends if f.get("agent_name") == agent_name), None)
            if agent_frontend:
                cfg = server._loaded_config_for_update()
                current_bookmarks = server._get_autoyou_bookmarks(cfg)
                base_url = server._get_autoyou_page_service_base_url(cfg)
                target_url = f"{base_url}/agent/{agent_name}/"

                exists = any(
                    bm.get("id") == agent_name or bm.get("url") == target_url
                    for bm in current_bookmarks
                )
                if not exists:
                    display_name = server.format_agent_display_name(agent_name)
                    new_bookmark = {
                        "id": agent_name,
                        "title": display_name,
                        "url": target_url,
                        "description": f"{display_name} website",
                        "enabled": True,
                    }
                    server._persist_autoyou_bookmarks(current_bookmarks + [new_bookmark])
                    server.LOGGER.info("Added website bookmark for installed agent '%s'", agent_name)
        except Exception as exc:
            server.LOGGER.warning("Failed to auto-add bookmark for installed agent '%s': %s", agent_name, exc)

        detail = result.get("agent_details", {}).get(agent_name, {})
        if not isinstance(detail, dict):
            detail = {}
        frontend_control = detail.get("frontend_control") if isinstance(detail, dict) else {}
        frontend_control = frontend_control if isinstance(frontend_control, dict) else {}
        frontend_path = str(
            frontend_control.get("open_url")
            or frontend_control.get("browser_path")
            or detail.get("frontend_path", "")
            or ""
        ).strip()
        has_frontend = bool(detail.get("has_frontend")) if isinstance(detail, dict) else False
        frontend_ready = bool(frontend_control.get("active"))
        restart_note = "Restart AutoYou AI to load chat routing, then refresh or reconnect any open Agent Website clients."
        if has_frontend and frontend_ready:
            message = f"Installed '{agent_name}'. Website route is ready"
            if frontend_path:
                message += f" at {frontend_path}"
            message += f". {restart_note}"
        elif has_frontend:
            message = (
                f"Installed '{agent_name}'. Its website files are present, but the website backend is not registered yet. "
                f"{restart_note} If it still does not appear, restart Browser & Page."
            )
        else:
            message = f"Installed '{agent_name}'. {restart_note}"

        return {
            "success": True,
            "agent_name": agent_name,
            "requires_restart": True,
            "frontend_ready": frontend_ready,
            "message": message,
            "detail": detail,
            "payload": result,
        }

    @admin_app.post("/api/agents/install-builder-suite")
    async def admin_install_builder_suite(request: Request):
        """Install the conservative Agent Builder workflow suite in one call."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}

        try:
            install_result = server.install_builder_suite_agents(
                agents_root=server._AUTOYOU_AGENTS_ROOT,
                source="admin_builder_suite",
            )
        except server.SecureStorageError as exc:
            return _agent_registry_storage_error(exc)
        await server.sync_managed_frontend_backends()
        result = server._build_agent_builder_listing_payload()
        server._sync_frontend_registry_from_builder_payload(result)

        reload_result = None
        if bool(payload.get("restart_ai", False)) and not install_result.get("failed"):
            reload_result = await admin_restart_ai_agent(request)
            if isinstance(reload_result, JSONResponse):
                try:
                    import json as _json

                    reload_result = _json.loads(reload_result.body.decode("utf-8"))
                except Exception:
                    reload_result = {"success": False, "error": "AI reload failed."}

        failed = install_result.get("failed") or []
        status_code = 409 if failed else 200
        return JSONResponse(
            status_code=status_code,
            content={
                "success": not bool(failed),
                "suite": "agent_builder",
                "agent_names": list(server.BUILDER_SUITE_AGENT_NAMES),
                "installed": install_result.get("installed", []),
                "already_installed": install_result.get("already_installed", []),
                "failed": failed,
                "requires_restart": bool(install_result.get("requires_restart")),
                "recommended_ollama_model": server.DEFAULT_BUILDER_SUITE_OLLAMA_MODEL,
                "reload": reload_result,
                "payload": result,
            },
        )

    @admin_app.post("/api/agents/uninstall")
    async def admin_uninstall_agent(request: Request):
        """Mark an agent uninstalled without removing its source files."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

        try:
            agent_name = server._normalize_agent_directory_name(payload.get("agent_name"))
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})

        try:
            server.set_agent_installed(
                agent_name,
                False,
                source="admin",
                agents_root=server._AUTOYOU_AGENTS_ROOT,
            )
        except KeyError:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": f"Agent '{agent_name}' was not found on disk."},
            )
        except server.SecureStorageError as exc:
            return _agent_registry_storage_error(exc)
        except Exception as exc:
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

        server.STATE.dynamic_agent_proxy_ports.pop(agent_name, None)
        await server.sync_managed_frontend_backends()
        result = server._build_agent_builder_listing_payload()
        server._sync_frontend_registry_from_builder_payload(result)

        try:
            cfg = server._loaded_config_for_update()
            current_bookmarks = server._get_autoyou_bookmarks(cfg)
            base_url = server._get_autoyou_page_service_base_url(cfg)
            target_url = f"{base_url}/agent/{agent_name}/"

            updated_bookmarks = [
                bm for bm in current_bookmarks
                if bm.get("id") != agent_name and bm.get("url") != target_url
            ]
            if len(updated_bookmarks) < len(current_bookmarks):
                server._persist_autoyou_bookmarks(updated_bookmarks)
                server.LOGGER.info("Removed website bookmark for uninstalled agent '%s'", agent_name)
        except Exception as exc:
            server.LOGGER.warning("Failed to auto-remove bookmark for uninstalled agent '%s': %s", agent_name, exc)

        detail = result.get("agent_details", {}).get(agent_name, {})
        if not isinstance(detail, dict):
            detail = {}
        return {
            "success": True,
            "agent_name": agent_name,
            "requires_restart": True,
            "message": f"Uninstalled '{agent_name}'. Restart AutoYou AI to unload chat routing, then refresh or reconnect any open Agent Website clients.",
            "detail": detail,
            "payload": result,
        }

    @admin_app.post("/api/builder/agent_port")
    async def admin_builder_register_agent_port(request: Request):
        """Register a dynamic HTTP port-forward for an agent's web server.

        Body: {"agent_name": "my_agent", "port": 8200}
        """
        local_error = server._require_loopback_request(request)
        if local_error:
            return local_error
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

        agent_name = (payload.get("agent_name") or "").strip().lower()
        port = payload.get("port")

        if not agent_name or not port:
            return JSONResponse(status_code=400, content={"success": False, "error": "'agent_name' and 'port' are required"})

        try:
            port = int(port)
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content={"success": False, "error": "'port' must be an integer"})

        server.STATE.dynamic_agent_proxy_ports[agent_name] = port
        server._sync_frontend_registry_from_builder_payload(server._build_agent_builder_listing_payload())
        server.LOGGER.info("Registered dynamic proxy port: agent=%s port=%d", agent_name, port)
        return {
            "success": True,
            "agent_name": agent_name,
            "port": port,
            "proxy_path": f"/agent/{agent_name}/",
            "message": f"Requests to /agent/{agent_name}/ will be forwarded to localhost:{port}",
        }

    @admin_app.get("/api/agents/workbench/{agent_name}")
    async def admin_workbench_get_detail(agent_name: str, request: Request):
        """Return workbench detail for a specific agent (new admin UI)."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            detail_payload = server._build_agent_workbench_detail_payload(agent_name)
            if detail_payload.get("status") != "success":
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": detail_payload.get("error") or "Agent not found"},
                )
            return {
                "success": True,
                "agent_name": detail_payload.get("agent_name"),
                "detail": detail_payload.get("detail"),
            }
        except Exception as exc:
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/agents/workbench/{agent_name}/clone")
    async def admin_workbench_clone(agent_name: str, request: Request):
        """Clone a live agent to a workspace draft."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            result = server.clone_live_agent_to_draft(agent_name, agents_root=server._workspace_agents_root())
            return server._build_agent_workbench_success_response(
                agent_name=result.get("agent_name", agent_name),
                message=f"Live agent '{agent_name}' cloned to workspace draft.",
                requires_restart=False,
                extra={"files_created": result.get("files_created", [])},
            )
        except FileExistsError as exc:
            return JSONResponse(status_code=409, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_workbench_clone failed: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/agents/workbench/{agent_name}/instructions")
    async def admin_workbench_save_instructions(agent_name: str, request: Request):
        """Save draft instruction content for an agent."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

        description = str(payload.get("description") or "").strip()
        instructions = str(payload.get("instructions") or "").strip()
        try:
            server.save_draft_instruction(
                agent_name,
                description=description,
                instructions=instructions,
                agents_root=server._workspace_agents_root(),
            )
            return server._build_agent_workbench_success_response(
                agent_name=agent_name,
                message=f"Draft instructions saved for '{agent_name}'.",
            )
        except FileNotFoundError as exc:
            return JSONResponse(status_code=404, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_workbench_save_instructions failed: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/agents/workbench/{agent_name}/test")
    async def admin_workbench_test_draft(agent_name: str, request: Request):
        """Run a structural check on a workspace draft."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            test_result = server.build_draft_runtime_test(agent_name, agents_root=server._workspace_agents_root())
            payload, detail = server._build_agent_workbench_refresh(agent_name)
            return {
                "success": True,
                "agent_name": agent_name,
                "draft_test": test_result,
                "payload": payload,
                "detail": detail,
            }
        except FileNotFoundError as exc:
            return JSONResponse(status_code=404, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_workbench_test failed: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/agents/workbench/{agent_name}/publish")
    async def admin_workbench_publish_draft(agent_name: str, request: Request):
        """Publish a workspace draft to the live agents directory (new admin UI)."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        install_after_publish = bool((payload or {}).get("install_after_publish", False))
        try:
            result = server.publish_agent_draft(
                agent_name,
                install_after_publish=install_after_publish,
                agents_root=server._workspace_agents_root(),
            )
            return server._build_agent_workbench_success_response(
                agent_name=result.get("agent_name", agent_name),
                message=f"Draft '{agent_name}' published. Restart AutoYou AI to load it.",
                requires_restart=True,
                extra={"published_files": result.get("published_files", [])},
            )
        except PermissionError as exc:
            return JSONResponse(status_code=403, content={"success": False, "error": str(exc), "compiled_block": True})
        except FileNotFoundError as exc:
            return JSONResponse(status_code=404, content={"success": False, "error": str(exc)})
        except ValueError as exc:
            return JSONResponse(status_code=422, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_workbench_publish failed: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/agents/workbench/{agent_name}/discard")
    async def admin_workbench_discard_draft(agent_name: str, request: Request):
        """Discard a workspace draft (new admin UI)."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            result = server.delete_agent_draft(agent_name, agents_root=server._workspace_agents_root())
            return server._build_agent_workbench_success_response(
                agent_name=result.get("agent_name", agent_name),
                message=f"Workspace draft '{agent_name}' discarded.",
            )
        except FileNotFoundError as exc:
            return JSONResponse(status_code=404, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_workbench_discard failed: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/agents/workbench/{agent_name}/frontend/scaffold")
    async def admin_workbench_scaffold_frontend(agent_name: str, request: Request):
        """Scaffold frontend assets for an agent draft."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        ui_purpose = str((payload or {}).get("ui_purpose") or "Agent frontend shell").strip()
        app_title = str((payload or {}).get("app_title") or f"{agent_name} UI").strip()
        local_port = int((payload or {}).get("local_port") or 8094)
        frontend_stack = server.normalize_frontend_stack(
            (payload or {}).get("frontend_stack") or server.DEFAULT_FRONTEND_STACK
        )
        backend_stack = server.normalize_backend_stack(
            (payload or {}).get("backend_stack") or server.DEFAULT_BACKEND_STACK
        )
        try:
            result = server.scaffold_frontend_draft(
                agent_name,
                ui_purpose=ui_purpose,
                app_title=app_title,
                local_port=local_port,
                frontend_stack=frontend_stack,
                backend_stack=backend_stack,
                agents_root=server._workspace_agents_root(),
            )
            return server._build_agent_workbench_success_response(
                agent_name=agent_name,
                message=f"Website scaffold created for '{agent_name}'.",
                extra={"files_created": result.get("files_created", [])},
            )
        except FileNotFoundError as exc:
            return JSONResponse(status_code=404, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_workbench_scaffold_frontend failed: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/agents/workbench/{agent_name}/frontend/manifest")
    async def admin_workbench_save_manifest(agent_name: str, request: Request):
        """Save the draft frontend manifest for an agent."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

        try:
            server.save_draft_frontend_manifest(
                agent_name,
                title=str(payload.get("title") or "").strip(),
                description=str(payload.get("description") or "").strip(),
                entry_path=str(payload.get("entry_path") or "/").strip(),
                recommended_port=payload.get("recommended_port"),
                requires_proxy_registration=bool(payload.get("requires_proxy_registration", True)),
                frontend_stack=payload.get("frontend_stack"),
                backend_stack=payload.get("backend_stack"),
                managed_runtime=payload.get("managed_runtime"),
                agents_root=server._workspace_agents_root(),
            )
            return server._build_agent_workbench_success_response(
                agent_name=agent_name,
                message=f"Draft frontend manifest saved for '{agent_name}'.",
            )
        except FileNotFoundError as exc:
            return JSONResponse(status_code=404, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_workbench_save_manifest failed: %s", exc)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.get("/api/agent-instructions")
    async def get_agent_instructions(request: Request):
        """Get the current agent instructions.

        In compiled mode the instructions are read from the compiled module (or the
        Prompt Override file if Prompt Override is active).  The response includes
        ``is_compiled`` and ``read_only`` flags so the admin UI can adapt its UI
        accordingly.
        """
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            content = server._read_agent_prompt_file()
            payload = server._build_agent_instruction_payload(content)
            payload["success"] = True
            _compiled = server.is_compiled()
            # Prompt Override can be active in BOTH compiled and source runs. When active it
            # takes precedence over prompt.py and both the raw and per-section editors
            # round-trip into the override file.
            _jailbreak = server.is_jailbreak_active(anchor=server.__file__)
            payload["is_compiled"] = _compiled
            # Instructions are read-only only when compiled AND Prompt Override is NOT active.
            payload["read_only"] = _compiled and not _jailbreak
            payload["jailbreak_active"] = _jailbreak
            if _compiled and not _jailbreak:
                payload["read_only_notice"] = (
                    "Instructions are locked in this packaged AutoYou runtime. "
                    "Enable Prompt Override to edit them locally."
                )
            elif _jailbreak:
                payload["read_only_notice"] = (
                    "Prompt Override active \u2014 it takes precedence over the base prompt. "
                    "Raw and per-section edits both round-trip into the override and apply "
                    "on the next AI restart. Disable Prompt Override to fall back to the base prompt."
                    if not _compiled else
                    "Prompt Override active \u2014 changes are saved to your local override file "
                    "and applied on the next server restart."
                )
            else:
                payload["read_only_notice"] = (
                    "Editable runtime \u2014 edits save locally and apply on the next AI restart. "
                    "Prompt Override is only needed when direct editing is locked."
                )
            return payload

        except Exception as e:
            server.LOGGER.error(f"Error reading agent instructions: {e}")
            return {"success": False, "error": str(e)}

    @admin_app.post("/api/agent-instructions")
    async def update_agent_instructions(request: Request):
        """Update the agent instructions in prompt.py."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            data = await request.json()
            new_instructions = data.get("instructions", "")
            if not isinstance(new_instructions, str):
                return {"success": False, "error": "Instructions must be a string"}
            new_instructions = new_instructions.strip()

            if not new_instructions:
                return {"success": False, "error": "Instructions cannot be empty"}

            content = server._read_agent_prompt_file()
            new_content = server._replace_string_assignment_value(content, "AGENT_INSTRUCTION", new_instructions)
            server._write_agent_prompt_file(new_content)
            runtime_reload_status = await server._reload_ai_agent_runtime_from_prompt_update()
            payload = server._build_agent_instruction_payload(new_content)

            server.LOGGER.info("Agent instructions updated successfully")
            payload.update({
                "success": True,
                "message": "Agent instructions updated successfully",
                "runtime_reload": runtime_reload_status,
            })
            return payload

        except Exception as e:
            server.LOGGER.error(f"Error updating agent instructions: {e}")
            return {"success": False, "error": str(e)}

    @admin_app.post("/api/agent-instructions/sections")
    async def update_agent_instruction_sections(request: Request):
        """Update the editable prompt section variables and regenerate AGENT_INSTRUCTION."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            data = await request.json()
            updates = server._coerce_prompt_section_updates(data.get("sections"))
            if not updates:
                return {"success": False, "error": "No editable prompt sections were provided"}

            content = server._read_agent_prompt_file()
            prompt_payload = server._build_agent_instruction_payload(content)
            if not prompt_payload.get("section_builder_available"):
                return {"success": False, "error": "Prompt sections are unavailable. Use the raw prompt editor instead."}

            current_section_values = {
                str(section.get("variable") or ""): str(section.get("value") or "")
                for section in prompt_payload.get("sections", [])
                if section.get("variable")
            }
            current_section_values.update(updates)
            composed_instructions = server._compose_prompt_from_section_values(current_section_values)
            if not composed_instructions:
                return {"success": False, "error": "The composed prompt cannot be empty"}

            new_content = content
            for variable_name, value in updates.items():
                new_content = server._replace_string_assignment_value(new_content, variable_name, value)
            new_content = server._replace_string_assignment_value(new_content, "AGENT_INSTRUCTION", composed_instructions)
            server._write_agent_prompt_file(new_content)
            runtime_reload_status = await server._reload_ai_agent_runtime_from_prompt_update()
            payload = server._build_agent_instruction_payload(new_content)

            server.LOGGER.info("Prompt builder sections updated successfully")
            payload.update({
                "success": True,
                "message": "Prompt sections updated successfully",
                "runtime_reload": runtime_reload_status,
            })
            return payload

        except Exception as e:
            server.LOGGER.error(f"Error updating prompt sections: {e}")
            return {"success": False, "error": str(e)}

    @admin_app.post("/api/agent-instructions/revert")
    async def revert_agent_instructions(request: Request):
        """Revert AGENT_INSTRUCTION to DEFAULT_INSTRUCTION in prompt.py (admin endpoint)."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            content = server._read_agent_prompt_file()
            default_text, default_source = server._default_agent_instruction_text(content)
            new_content = server._replace_string_assignment_value(content, "AGENT_INSTRUCTION", default_text)
            server._write_agent_prompt_file(new_content)
            runtime_reload_status = await server._reload_ai_agent_runtime_from_prompt_update()
            payload = server._build_agent_instruction_payload(new_content)

            server.LOGGER.info("Agent instructions reverted to default successfully")
            payload.update({
                "success": True,
                "instructions": default_text,
                "message": (
                    "Agent instructions reverted to the composed section fallback"
                    if default_source == "sections"
                    else "Agent instructions reverted to default"
                ),
                "runtime_reload": runtime_reload_status,
            })
            return payload

        except Exception as e:
            server.LOGGER.error(f"Error reverting agent instructions: {e}")
            return {"success": False, "error": str(e)}

    @admin_app.get("/api/jailbreak/status")
    async def jailbreak_status(request: Request):
        """Return current Prompt Override status."""
        redir = server._require_login(request)
        if redir:
            return redir
        storage_dir = server.get_jailbreak_data_dir(anchor=server.__file__)
        ack_file = storage_dir / server.JAILBREAK_ACKNOWLEDGEMENT_FILENAME
        active = ack_file.exists()
        has_prompt = active and (storage_dir / server.JAILBREAK_ROOT_PROMPT_FILENAME).exists()
        return JSONResponse({
            "active": active,
            "has_custom_prompt": has_prompt,
            "storage_dir": str(storage_dir),
            "user_data_dir": str(storage_dir),
            "is_compiled": server.is_compiled(),
        })

    @admin_app.post("/api/jailbreak/activate")
    async def jailbreak_activate(request: Request):
        """Create the ACKNOWLEDGEMENT_AGREEMENT file to enable Prompt Override."""
        redir = server._require_login(request)
        if redir:
            return redir
        storage_dir = server.get_jailbreak_data_dir(anchor=server.__file__)
        ack_file = storage_dir / server.JAILBREAK_ACKNOWLEDGEMENT_FILENAME
        try:
            ack_text = (
                "I understand that Prompt Override allows editing root agent instructions "
                "and can change the safety behavior built into AutoYou.\n"
                "I accept full responsibility for custom prompts, behavior changes, outputs, "
                "automations, integrations, data, accounts, and legal or platform compliance "
                "while Prompt Override is active.\n"
                "AutoYou is provided as-is; OpenStorey LLC, the AutoYou owner board, and "
                "contributors disclaim warranties and limit liability to the maximum extent "
                "permitted by law.\n\n"
                f"Activated: {__import__('datetime').datetime.now().isoformat()}\n"
            )
            server.write_secure_file(ack_file, ack_text.encode("utf-8"))
            server.LOGGER.info("Prompt Override enabled - created %s", ack_file)
            return JSONResponse({"success": True, "message": "Prompt Override enabled."})
        except Exception as exc:
            server.LOGGER.error("Failed to enable Prompt Override: %s", exc)
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    @admin_app.post("/api/jailbreak/deactivate")
    async def jailbreak_deactivate(request: Request):
        """Remove the ACKNOWLEDGEMENT_AGREEMENT file to deenable Prompt Override."""
        redir = server._require_login(request)
        if redir:
            return redir
        storage_dir = server.get_jailbreak_data_dir(anchor=server.__file__)
        ack_file = storage_dir / server.JAILBREAK_ACKNOWLEDGEMENT_FILENAME
        try:
            if ack_file.exists():
                ack_file.unlink()
            server.LOGGER.info("Prompt Override disabled")
            return JSONResponse({"success": True, "message": "Prompt Override disabled."})
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    @admin_app.get("/api/jailbreak/prompt")
    async def jailbreak_get_prompt(request: Request):
        """Return the current Prompt Override root agent prompt."""
        redir = server._require_login(request)
        if redir:
            return redir
        if not server.is_jailbreak_active(anchor=server.__file__):
            return JSONResponse({"active": False, "prompt": None})
        # The override is stored as a full prompt module; surface only the
        # AGENT_INSTRUCTION so the Security-screen box stays a focused editor.
        override = server.get_jailbreak_root_prompt(anchor=server.__file__)
        instruction = ""
        if override:
            if server._override_defines_full_prompt_module(override):
                try:
                    instruction = server._extract_string_assignment_value(override, "AGENT_INSTRUCTION")
                except Exception:
                    instruction = ""
            else:
                instruction = override
        return JSONResponse({"active": True, "prompt": instruction})

    @admin_app.post("/api/jailbreak/prompt")
    async def jailbreak_save_prompt(request: Request):
        """Save a custom root agent override prompt (Prompt Override must be active)."""
        redir = server._require_login(request)
        if redir:
            return redir
        if not server.is_jailbreak_active(anchor=server.__file__):
            return JSONResponse(
                {"success": False, "error": "Prompt Override is not active."},
                status_code=403,
            )
        try:
            body = await request.json()
            prompt_text = str(body.get("prompt", ""))
            # Overlay the edited AGENT_INSTRUCTION onto the current base prompt and
            # persist the FULL module so the per-section builder stays in sync.
            if server.is_compiled():
                base = server._synthesize_agent_prompt_content()
            else:
                with open(server._agent_prompt_file_path(), "r", encoding="utf-8") as f:
                    base = f.read()
            full_source = server._replace_string_assignment_value(base, "AGENT_INSTRUCTION", prompt_text)
            server._persist_jailbreak_root_prompt_override(full_source)
            return JSONResponse({"success": True})
        except Exception as exc:
            server.LOGGER.error("Failed to save Prompt Override prompt: %s", exc)
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    @admin_app.get("/api/agent-security/profiles")
    async def agent_security_list_profiles(request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        store = server._get_agent_security_store()
        if store is None:
            return JSONResponse({"success": False, "error": "Server is locked; unlock to manage 2FA profiles."}, status_code=409)
        return JSONResponse({"success": True, "profiles": store.list_profiles()})

    @admin_app.get("/api/agent-security/profiles/{profile_id}/current-code")
    async def agent_security_current_code(profile_id: str, request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        store = server._get_agent_security_store()
        if store is None:
            return JSONResponse({"success": False, "error": "Server is locked."}, status_code=409)
        try:
            from shared.agent_security_profiles import SecurityProfileError
            return JSONResponse({"success": True, **store.current_code(profile_id)})
        except SecurityProfileError as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            server.LOGGER.error("agent_security_current_code failed: %s", exc)
            return JSONResponse({"success": False, "error": "Could not generate the authenticator code."}, status_code=500)

    @admin_app.post("/api/agent-security/profiles")
    async def agent_security_create_profile(request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        store = server._get_agent_security_store()
        if store is None:
            return JSONResponse({"success": False, "error": "Server is locked; unlock to create 2FA profiles."}, status_code=409)
        try:
            body = await request.json()
        except Exception:
            body = {}
        label = str((body or {}).get("label") or "").strip()
        issuer = str((body or {}).get("issuer") or "AutoYou").strip() or "AutoYou"
        try:
            from shared.agent_security_profiles import SecurityProfileError
            enrolment = store.create_profile(label, issuer=issuer)
            # provisioning_uri + manual_entry_secret are returned exactly ONCE here.
            # qr_data_url is rendered locally (no third-party service sees the secret)
            # and is likewise never persisted - it only ever exists in this response.
            enrolment["qr_data_url"] = server._build_local_qr_data_url(enrolment.get("provisioning_uri", ""))
            server.LOGGER.info("Created agent 2FA profile %s (%s)", enrolment.get("profile_id"), label)
            return JSONResponse({"success": True, "enrolment": enrolment})
        except SecurityProfileError as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
        except Exception as exc:
            server.LOGGER.error("agent_security_create_profile failed: %s", exc)
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    @admin_app.post("/api/agent-security/profiles/{profile_id}/verify")
    async def agent_security_verify_profile(profile_id: str, request: Request):
        """Verify a TOTP code directly against a profile (enrolment confirmation).

        Used right after creation, before the profile is assigned to any agent, to
        confirm the admin actually captured the QR/secret correctly. Standard TOTP
        tolerance (valid_window=1, i.e. +/-30s) applies, same as the agent-scoped
        verify route.
        """
        redir = server._require_login(request)
        if redir:
            return redir
        client_ip = server._rate_limit_client_key(request)
        if not server.LOGIN_RATE_LIMITER.is_allowed(client_ip):
            return JSONResponse(
                {"success": False, "error": "Too many attempts. Please wait a minute and try again."},
                status_code=429,
            )
        store = server._get_agent_security_store()
        if store is None:
            return JSONResponse({"success": False, "error": "Server is locked."}, status_code=409)
        if not store.profile_exists(profile_id):
            return JSONResponse({"success": False, "error": "Unknown 2FA profile."}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            body = {}
        code = str((body or {}).get("code") or "").strip()
        verified = store.verify(profile_id, code)
        return JSONResponse({"success": True, "verified": bool(verified)})

    @admin_app.post("/api/agent-security/profiles/{profile_id}/assign")
    async def agent_security_assign_profile(profile_id: str, request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        store = server._get_agent_security_store()
        if store is None:
            return JSONResponse({"success": False, "error": "Server is locked."}, status_code=409)
        try:
            body = await request.json()
        except Exception:
            body = {}
        agent_name = str((body or {}).get("agent_name") or "").strip()
        if not agent_name:
            return JSONResponse({"success": False, "error": "Choose an agent website to assign."}, status_code=400)
        try:
            # Cross-check against the real agent registry to catch typos from the admin
            # UI dropdown. Fail open (skip the check) when the overview itself comes
            # back empty - that means the registry couldn't be resolved, not that no
            # agent websites exist, and a bogus assignment here is a UX foot-gun, not
            # a security hole (nothing reads an assignment to a nonexistent agent).
            overview_entries = server._build_agent_builder_listing_payload().get("agent_overview") or []
            if overview_entries:
                installed_website_agents = {
                    entry.get("agent_name")
                    for entry in overview_entries
                    if entry.get("installed") and entry.get("has_frontend")
                }
                if agent_name not in installed_website_agents:
                    return JSONResponse(
                        {"success": False, "error": f"'{agent_name}' is not an installed agent website."},
                        status_code=400,
                    )
            from shared.agent_security_profiles import SecurityProfileError
            store.assign_agent(agent_name, profile_id)
            return JSONResponse({"success": True, "profiles": store.list_profiles()})
        except SecurityProfileError as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
        except Exception as exc:
            server.LOGGER.error("agent_security_assign_profile failed: %s", exc)
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    @admin_app.post("/api/agent-security/profiles/{profile_id}/wipe")
    async def agent_security_wipe_profile(profile_id: str, request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        store = server._get_agent_security_store()
        if store is None:
            return JSONResponse({"success": False, "error": "Server is locked."}, status_code=409)
        removed = store.wipe_profile(profile_id)
        return JSONResponse({"success": True, "removed": removed, "profiles": store.list_profiles()})

    @admin_app.post("/api/agent-security/agents/{agent_name}/wipe")
    async def agent_security_wipe_agent(agent_name: str, request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        store = server._get_agent_security_store()
        if store is None:
            return JSONResponse({"success": False, "error": "Server is locked."}, status_code=409)
        freed = store.wipe_agent(agent_name)
        return JSONResponse({"success": True, "freed_profile_id": freed, "profiles": store.list_profiles()})

    @admin_app.get("/api/agent-security/agents/{agent_name}/status")
    async def agent_security_agent_status(agent_name: str, request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        store = server._get_agent_security_store()
        if store is None:
            return JSONResponse({"success": False, "error": "Server is locked."}, status_code=409)
        return JSONResponse({
            "success": True,
            "agent_name": agent_name,
            "requires_2fa": store.agent_requires_2fa(agent_name),
            "profile_id": store.get_agent_profile_id(agent_name),
        })

    @admin_app.post("/api/agent-security/verify")
    async def agent_security_verify(request: Request):
        """Verify a TOTP code for an agent's assigned profile (the website gate backend)."""
        redir = server._require_login(request)
        if redir:
            return redir
        # Rate-limit verification attempts to blunt TOTP brute-force (shared limiter).
        client_ip = server._rate_limit_client_key(request)
        if not server.LOGIN_RATE_LIMITER.is_allowed(client_ip):
            return JSONResponse(
                {"success": False, "error": "Too many attempts. Please wait a minute and try again."},
                status_code=429,
            )
        store = server._get_agent_security_store()
        if store is None:
            return JSONResponse({"success": False, "error": "Server is locked."}, status_code=409)
        try:
            body = await request.json()
        except Exception:
            body = {}
        agent_name = str((body or {}).get("agent_name") or "").strip()
        code = str((body or {}).get("code") or "").strip()
        # No assigned profile => the agent is not 2FA-gated => access allowed.
        if not store.agent_requires_2fa(agent_name):
            return JSONResponse({"success": True, "required": False, "verified": True})
        verified = store.verify_for_agent(agent_name, code)
        return JSONResponse({"success": True, "required": True, "verified": bool(verified)})

    return {
        "admin_set_agent_frontend_state": admin_set_agent_frontend_state,
        "admin_get_agent_websites_security": admin_get_agent_websites_security,
        "admin_set_agent_websites_security": admin_set_agent_websites_security,
        "admin_set_agent_website_auth": admin_set_agent_website_auth,
        "admin_sign_out_all_agent_sessions": admin_sign_out_all_agent_sessions,
        "admin_get_default_agent_website": admin_get_default_agent_website,
        "admin_set_default_agent_website": admin_set_default_agent_website,
        "admin_list_bookmarks": admin_list_bookmarks,
        "admin_create_bookmark": admin_create_bookmark,
        "admin_update_bookmark": admin_update_bookmark,
        "admin_delete_bookmark": admin_delete_bookmark,
        "admin_clear_bookmarks": admin_clear_bookmarks,
        "openclaw_gateway_status": openclaw_gateway_status,
        "hermes_gateway_status": hermes_gateway_status,
        "ollama_gateway_status": ollama_gateway_status,
        "odysseus_gateway_status": odysseus_gateway_status,
        "admin_restart_ai_agent": admin_restart_ai_agent,
        "get_model_behavior": get_model_behavior,
        "set_model_behavior": set_model_behavior,
        "admin_builder_create": admin_builder_create,
        "admin_builder_status": admin_builder_status,
        "admin_builder_list_agents": admin_builder_list_agents,
        "admin_manage_agents": admin_manage_agents,
        "admin_discard_agent_draft": admin_discard_agent_draft,
        "admin_publish_agent_draft": admin_publish_agent_draft,
        "admin_install_agent": admin_install_agent,
        "admin_install_builder_suite": admin_install_builder_suite,
        "admin_uninstall_agent": admin_uninstall_agent,
        "admin_builder_register_agent_port": admin_builder_register_agent_port,
        "admin_workbench_get_detail": admin_workbench_get_detail,
        "admin_workbench_clone": admin_workbench_clone,
        "admin_workbench_save_instructions": admin_workbench_save_instructions,
        "admin_workbench_test_draft": admin_workbench_test_draft,
        "admin_workbench_publish_draft": admin_workbench_publish_draft,
        "admin_workbench_discard_draft": admin_workbench_discard_draft,
        "admin_workbench_scaffold_frontend": admin_workbench_scaffold_frontend,
        "admin_workbench_save_manifest": admin_workbench_save_manifest,
        "get_agent_instructions": get_agent_instructions,
        "update_agent_instructions": update_agent_instructions,
        "update_agent_instruction_sections": update_agent_instruction_sections,
        "revert_agent_instructions": revert_agent_instructions,
        "jailbreak_status": jailbreak_status,
        "jailbreak_activate": jailbreak_activate,
        "jailbreak_deactivate": jailbreak_deactivate,
        "jailbreak_get_prompt": jailbreak_get_prompt,
        "jailbreak_save_prompt": jailbreak_save_prompt,
        "agent_security_list_profiles": agent_security_list_profiles,
        "agent_security_create_profile": agent_security_create_profile,
        "agent_security_verify_profile": agent_security_verify_profile,
        "agent_security_assign_profile": agent_security_assign_profile,
        "agent_security_wipe_profile": agent_security_wipe_profile,
        "agent_security_wipe_agent": agent_security_wipe_agent,
        "agent_security_agent_status": agent_security_agent_status,
        "agent_security_verify": agent_security_verify
    }
