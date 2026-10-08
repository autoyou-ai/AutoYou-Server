# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-9199bb88fee39eb4b5dfd402

"""Services HTTP routes for the full AutoYou server."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import hashlib
import hmac
import re
from typing import Any, Callable, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-9199bb88fee39eb4b5dfd402"


def register_routes(
    admin_app: FastAPI,
    auth_app: FastAPI,
    server: Any,
) -> Dict[str, Callable[..., Any]]:
    @admin_app.post("/ai-agent-server/start")
    async def start_ai_agent_server_endpoint(request: Request):
        """Start AI Agent Server."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            if await server.start_ai_agent_server_background():
                return {"status": "AI Agent Server started successfully"}
            return {"status": "Error: AI Agent Server did not become healthy on startup."}
        except Exception as e:
            server.LOGGER.error(f"Error starting AI Agent Server: {e}")
            return {"status": f"Error: {str(e)}"}

    @admin_app.post("/ai-agent-server/stop")
    async def stop_ai_agent_server_endpoint(request: Request):
        """Stop AI Agent Server."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            await server.stop_ai_agent_server()
            return {"status": "AI Agent Server stopped successfully"}
        except Exception as e:
            server.LOGGER.error(f"Error stopping AI Agent Server: {e}")
            return {"status": f"Error: {str(e)}"}

    @admin_app.post("/ai-agent-server/restart")
    async def restart_ai_agent_server_endpoint(request: Request):
        """Restart AI Agent Server."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            if not await server.restart_ai_agent_server():
                return JSONResponse(status_code=503, content={"status": "Error: AI Agent Server did not become healthy after restart."})
            return {"status": "AI Agent Server restarted successfully"}
        except Exception as e:
            server.LOGGER.error(f"Error restarting AI Agent Server: {e}")
            return {"status": f"Error: {str(e)}"}

    @admin_app.get("/api/ai-agent-server/status")
    async def get_ai_agent_server_status_endpoint(request: Request):
        """Get AI Agent Server status."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            cached_status = server._get_cached_admin_status("ai_agent_status")
            if cached_status is not None:
                return {"status": cached_status}
            status = await server._ai_agent_server_status()
            server._set_cached_admin_status("ai_agent_status", status)
            return {"status": status}
        except Exception as e:
            server.LOGGER.error(f"Error getting AI Agent Server status: {e}")
            return {"status": "Error"}

    @admin_app.get("/api/v1/status")
    async def browser_status_endpoint(request: Request):
        auth_error = server._require_session_for_network_peer(request)
        if auth_error:
            return auth_error
        return server._json_response_no_store(server._build_browser_status_payload())

    @admin_app.get("/api/v1/server-config")
    async def browser_server_config_endpoint(request: Request):
        auth_error = server._require_session_for_network_peer(request)
        if auth_error:
            return auth_error
        return server._json_response_no_store(server._build_browser_server_config_payload())

    @admin_app.post("/autoyou-page-service/start")
    async def start_autoyou_page_service_endpoint(request: Request):
        """Start For AutoYou Page Service."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            await server.start_autoyou_page_service_background()
            return {"status": "For AutoYou Page Service started successfully"}
        except Exception as e:
            server.LOGGER.error(f"Error starting For AutoYou Page Service: {e}")
            return {"status": f"Error: {str(e)}"}

    @admin_app.post("/autoyou-page-service/stop")
    async def stop_autoyou_page_service_endpoint(request: Request):
        """Stop For AutoYou Page Service."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            await server.stop_autoyou_page_service_background()
            return {"status": "For AutoYou Page Service stopped successfully"}
        except Exception as e:
            server.LOGGER.error(f"Error stopping For AutoYou Page Service: {e}")
            return {"status": f"Error: {str(e)}"}

    @admin_app.post("/autoyou-page-service/restart")
    async def restart_autoyou_page_service_endpoint(request: Request):
        """Restart For AutoYou Page Service."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            await server.restart_autoyou_page_service()
            return {"status": "For AutoYou Page Service restarted successfully"}
        except Exception as e:
            server.LOGGER.error(f"Error restarting For AutoYou Page Service: {e}")
            return {"status": f"Error: {str(e)}"}

    @admin_app.get("/api/autoyou-page-service/status")
    async def get_autoyou_page_service_status_endpoint(request: Request):
        """Get For AutoYou Page Service status."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            cached_status = server._get_cached_admin_status("autoyou_page_status")
            if cached_status is not None:
                return {"status": cached_status}
            status = await server._autoyou_page_service_status()
            server._set_cached_admin_status("autoyou_page_status", status)
            return {"status": status}
        except Exception as e:
            server.LOGGER.error(f"Error getting For AutoYou Page Service status: {e}")
            return {"status": "Error"}

    @admin_app.get("/api/tunnelmole/status")
    async def get_tunnelmole_status_endpoint(request: Request):
        """Get Tunnelmole service status."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            status_info = server.get_tunnelmole_status()
            return status_info
        except Exception as e:
            server.LOGGER.error(f"Error getting Tunnelmole status: {e}")
            return {"status": "Error", "public_url": None}

    @admin_app.get("/api/tunnelmole/website-hosting")
    async def admin_get_tunnelmole_website_hosting(request: Request):
        """Return selectable websites and the current public website choice."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        return JSONResponse(server._build_tunnelmole_website_hosting_payload())

    @admin_app.post("/api/tunnelmole/website-hosting")
    async def admin_set_tunnelmole_website_hosting(request: Request):
        """Select which installed website opens from the public proxy URL."""
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)
        try:
            payload = await request.json()
            result = await server._set_tunnelmole_website_hosting_from_payload(payload)
            status_code = 200 if result.get("success") else 503
            return JSONResponse(result, status_code=status_code)
        except server.ConfigWriteBlocked as exc:
            return server._json_config_write_blocked_response(str(exc))
        except PermissionError as exc:
            return JSONResponse(status_code=402, content={"success": False, "error": str(exc), "plan_required": True})
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("Failed to update public website hosting: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/tunnelmole/start")
    async def start_tunnelmole_endpoint(request: Request):
        """Start Tunnelmole service using the configured lifetime mode."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            started = await server.start_tunnelmole_service_for_current_mode(force=True)
            if not started:
                status = server.get_tunnelmole_status()
                message = (status.get("upgrade") or {}).get("message") or "Public reverse proxy service could not be started."
                return JSONResponse(
                    status_code=503,
                    content={"status": "Error", "message": message},
                )
            return server.get_tunnelmole_status()
        except Exception as e:
            server.LOGGER.error(f"Error starting Tunnelmole service: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "Error", "message": str(e)},
            )

    @admin_app.post("/tunnelmole/stop")
    async def stop_tunnelmole_endpoint(request: Request):
        """Stop Tunnelmole service."""
        redir = server._require_login(request)
        # from __debug_provenance_k__ import donations
        if redir:
            return redir

        try:
            await server.stop_tunnelmole_service()
            await server.stop_auth_server()
            return {"status": "Public reverse proxy service stopped successfully"}
        except Exception as e:
            server.LOGGER.error(f"Error stopping Tunnelmole service: {e}")
            return {"status": f"Error: {str(e)}"}

    @admin_app.post("/tunnelmole/refresh")
    async def refresh_tunnelmole_endpoint(request: Request):
        """Refresh Tunnelmole service status."""
        redir = server._require_login(request)
        if redir:
            return redir

        try:
            status_info = server.get_tunnelmole_status()
            return status_info
        except Exception as e:
            server.LOGGER.error(f"Error refreshing Tunnelmole status: {e}")
            return {"status": "Error", "public_url": None}

    @admin_app.get("/runtime/owner")
    async def runtime_owner_endpoint(request: Request):
        """Prove to the host that started this process that this listener is it.

        A host cannot always inspect a child's sockets — inside a sandbox that
        is refused — so ownership is proven with the start-time token instead of
        an OS lookup. Anyone without the token is told nothing.
        """
        challenge = request.headers.get("x-autoyou-owner-challenge", "")
        if challenge:
            # Never transmit the start token to an unverified local listener.
            # Separate request/response domains prevent reflecting the request
            # proof back as apparent evidence that a foreign port is ours.
            token = str(server.os.getenv(server.SHUTDOWN_TOKEN_ENV, "")).strip()
            provided = request.headers.get("x-autoyou-owner-proof", "")
            if (not token or not re.fullmatch(r"[0-9a-f]{64}", challenge) or not re.fullmatch(r"[0-9a-f]{64}", provided) or
                    not server._is_loopback_client_host(getattr(getattr(request, "client", None), "host", None))):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            expected = hmac.new(token.encode(), ("autoyou-owner-request:" + challenge).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(provided, expected):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            pid = server.os.getpid()
            proof = hmac.new(token.encode(), f"autoyou-owner-response:{challenge}:{pid}".encode(), hashlib.sha256).hexdigest()
            return JSONResponse({"pid": pid, "instance": server.SERVER_INSTANCE_NAME,
                                 "ports": server.build_instance_runtime_status()["ports"], "proof": proof})
        if not server._request_uses_shutdown_token(request):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return JSONResponse({"pid": server.os.getpid(), "instance": server.SERVER_INSTANCE_NAME})

    @admin_app.post("/shutdown")
    async def shutdown_server_endpoint(request: Request):
        """Gracefully shutdown the AutoYou server."""
        if server._request_uses_shutdown_token(request):
            server.LOGGER.info("Server shutdown requested via local shutdown token")
        else:
            redir = server._require_login(request)
            if redir:
                return redir
            server.LOGGER.info("Server shutdown requested via admin UI")

        # Schedule shutdown in a background task to allow response to be sent
        async def deferred_shutdown():
            try:
                # Give a moment for the response to be sent
                await server.asyncio.sleep(1)
                await server.graceful_shutdown()
            except Exception as e:
                server.LOGGER.error(f"Error during graceful shutdown: {e}")

        # Start the shutdown process in the background
        server.track_background_task(deferred_shutdown())

        return JSONResponse({"success": True, "message": "Server shutdown initiated"})

    return {
        "runtime_owner_endpoint": runtime_owner_endpoint,
        "start_ai_agent_server_endpoint": start_ai_agent_server_endpoint,
        "stop_ai_agent_server_endpoint": stop_ai_agent_server_endpoint,
        "restart_ai_agent_server_endpoint": restart_ai_agent_server_endpoint,
        "get_ai_agent_server_status_endpoint": get_ai_agent_server_status_endpoint,
        "browser_status_endpoint": browser_status_endpoint,
        "browser_server_config_endpoint": browser_server_config_endpoint,
        "start_autoyou_page_service_endpoint": start_autoyou_page_service_endpoint,
        "stop_autoyou_page_service_endpoint": stop_autoyou_page_service_endpoint,
        "restart_autoyou_page_service_endpoint": restart_autoyou_page_service_endpoint,
        "get_autoyou_page_service_status_endpoint": get_autoyou_page_service_status_endpoint,
        "get_tunnelmole_status_endpoint": get_tunnelmole_status_endpoint,
        "admin_get_tunnelmole_website_hosting": admin_get_tunnelmole_website_hosting,
        "admin_set_tunnelmole_website_hosting": admin_set_tunnelmole_website_hosting,
        "start_tunnelmole_endpoint": start_tunnelmole_endpoint,
        "stop_tunnelmole_endpoint": stop_tunnelmole_endpoint,
        "refresh_tunnelmole_endpoint": refresh_tunnelmole_endpoint,
        "shutdown_server_endpoint": shutdown_server_endpoint
    }
