# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-a7d219e4d69e7fc51e89c187

"""Routes attached to the separate AI agent worker application."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-a7d219e4d69e7fc51e89c187"


import hashlib
import hmac
import os
import time
from types import ModuleType
from typing import Optional

from fastapi import FastAPI, Request

from rest_api import APIStatus, ChatRequest, ChatResponse, SessionInfo

_LAN_OTP_COOKIE_NAME = "autoyou_ai_agent_lan_otp"
_LAN_OTP_HEADER_NAME = "x-autoyou-otp"
_LAN_OTP_QUERY_PARAM = "otp"
_LAN_OTP_TOKEN_MAX_AGE_SECONDS = 3600

_runtime_module: Optional[ModuleType] = None


def bind_runtime(module: ModuleType) -> None:
    global _runtime_module
    _runtime_module = module


def _runtime() -> ModuleType:
    if _runtime_module is None:
        import server

        bind_runtime(server)
    return _runtime_module


def _install_ai_agent_dev_graph_compat_routes(app_instance: FastAPI) -> None:
    server = _runtime()
    original_build_graph_endpoint = server._replace_app_get_route(
        app_instance,
        "/dev/build_graph/{app_name}",
    )
    if original_build_graph_endpoint is None:
        return

    adk_web_server = server._extract_adk_web_server_from_endpoint(original_build_graph_endpoint)

    @app_instance.get("/dev/build_graph/{app_name}")
    async def autoyou_build_graph_info(app_name: str):
        try:
            payload = original_build_graph_endpoint(app_name)
            if server.asyncio.iscoroutine(payload):
                payload = await payload
            return server.JSONResponse(content=server._make_json_safe(payload))
        except Exception as exc:
            server.LOGGER.warning(
                "AI agent build graph metadata route failed for %s: %s",
                app_name,
                exc,
            )
            return server.JSONResponse(
                status_code=500,
                content={"error": str(exc)},
            )

    if not server._has_app_get_route(app_instance, "/dev/build_graph_image/{app_name}"):

        @app_instance.get("/dev/build_graph_image/{app_name}")
        async def autoyou_build_graph_image(
            app_name: str,
            dark_mode: bool = False,
            node: Optional[str] = None,
        ):
            if adk_web_server is None:
                return server.JSONResponse(content={})

            try:
                from google.adk.cli import agent_graph as adk_agent_graph

                agent_or_app = adk_web_server.agent_loader.load_agent(app_name)
                root_agent = adk_web_server._get_root_agent(agent_or_app)
            except Exception as exc:
                server.LOGGER.warning(
                    "AI agent graph load failed for %s: %s",
                    app_name,
                    exc,
                )
                return server.JSONResponse(content={})

            try:
                if node:
                    target_agent = server._resolve_agent_graph_target(root_agent, app_name, node)
                    if target_agent is None:
                        return server.JSONResponse(content={})

                    dot_graph = await adk_agent_graph.get_agent_graph(
                        target_agent,
                        [],
                        dark_mode=dark_mode,
                    )
                    return server.JSONResponse(content=server._build_dot_result_payload(dot_graph))

                graph_payload: server.Dict[str, server.Dict[str, str]] = {}
                for graph_key, target_agent in server._iter_agent_graph_targets(app_name, root_agent):
                    dot_graph = await adk_agent_graph.get_agent_graph(
                        target_agent,
                        [],
                        dark_mode=dark_mode,
                    )
                    dot_payload = server._build_dot_result_payload(dot_graph)
                    if dot_payload:
                        graph_payload[graph_key] = dot_payload

                return server.JSONResponse(content=graph_payload)
            except Exception as exc:
                server.LOGGER.warning(
                    "AI agent graph compatibility route failed for %s: %s",
                    app_name,
                    exc,
                )
                return server.JSONResponse(content={})

    app_instance.openapi_schema = None


def _make_lan_otp_gate_token(secret: str) -> str:
    """Short-lived, stateless session token so a valid OTP code isn't required
    on every single request -- just re-verified against its own signature and
    age. Signed with the same TOTP secret rather than inventing new key
    material; that secret is only ever available inside this process and the
    admin process, never sent to the browser itself (only this derived token
    is)."""
    issued_at = str(int(time.time()))
    signature = hmac.new(secret.encode("utf-8"), issued_at.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{issued_at}.{signature}"


def _verify_lan_otp_gate_token(token: str, secret: str) -> bool:
    try:
        issued_at_str, signature = token.split(".", 1)
        issued_at = int(issued_at_str)
    except (ValueError, AttributeError):
        return False
    expected_signature = hmac.new(secret.encode("utf-8"), issued_at_str.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return False
    return (time.time() - issued_at) <= _LAN_OTP_TOKEN_MAX_AGE_SECONDS


def _install_ai_agent_lan_otp_gate(app_instance: FastAPI) -> None:
    """Require a valid OTP code on the opt-in LAN/HTTPS AI Agent listener.

    The plain loopback listener (8081, today's behavior) is never affected by
    this -- both listeners serve the SAME app instance (same pattern as the
    admin server's 8001/8443 pair), so the gate has to tell them apart itself
    rather than being a property of the app. It does that via
    ``request.scope["server"]``, the ``(host, port)`` uvicorn actually bound
    the connection on: only the configured LAN HTTPS port is challenged.
    """
    server = _runtime()

    @app_instance.middleware("http")
    async def _ai_agent_lan_otp_gate(request, call_next):
        lan_https_port_raw = os.environ.get("AUTOYOU_AI_AGENT_LAN_HTTPS_PORT")
        server_tuple = request.scope.get("server")
        request_port = server_tuple[1] if server_tuple else None
        if not lan_https_port_raw or request_port != int(lan_https_port_raw):
            # Not the LAN listener (or it isn't configured at all) -- this is
            # the plain loopback port behaving exactly as it always has.
            return await call_next(request)

        totp_secret = os.environ.get(server.AI_AGENT_TOTP_SECRET_ENV)
        if not totp_secret:
            return server.JSONResponse(
                status_code=503,
                content={"error": "LAN access is misconfigured: no OTP secret is available to this worker."},
            )

        token = request.cookies.get(_LAN_OTP_COOKIE_NAME)
        if token and _verify_lan_otp_gate_token(token, totp_secret):
            return await call_next(request)

        code = request.headers.get(_LAN_OTP_HEADER_NAME) or request.query_params.get(_LAN_OTP_QUERY_PARAM)
        if code and server._verify_totp_secret(totp_secret, code, valid_window=1):
            response = await call_next(request)
            response.set_cookie(
                _LAN_OTP_COOKIE_NAME,
                _make_lan_otp_gate_token(totp_secret),
                max_age=_LAN_OTP_TOKEN_MAX_AGE_SECONDS,
                httponly=True,
                samesite="lax",
                secure=True,
            )
            return response

        return server.JSONResponse(
            status_code=401,
            content={
                "error": "OTP code required for LAN access.",
                "hint": f"Provide it via '?{_LAN_OTP_QUERY_PARAM}=<code>' or the '{_LAN_OTP_HEADER_NAME}' header.",
            },
        )


def attach_ai_agent_endpoints(app_instance: FastAPI):
    """Attach custom endpoints to the AI Agent FastAPI app."""
    server = _runtime()
    _install_ai_agent_lan_otp_gate(app_instance)
    server._install_ai_agent_dev_graph_compat_routes(app_instance)
    server.install_route_aware_request_logging(
        app_instance,
        logger_name="autoyou.http.ai_agent",
        debug_path_prefixes=("/health",),
    )

    # Add custom health check endpoint
    @app_instance.get("/health")
    async def health_check():
        """Health check endpoint for monitoring."""
        return {
            "status": "healthy",
            "service": "AutoYou AI Agent",
            "runtime": server.build_runtime_environment_status(),
        }

    @app_instance.post("/api/internal/runtime-settings")
    async def update_runtime_settings(request: Request):
        """Apply parent-authorized settings without restarting the AI worker."""
        if not server._request_uses_ai_agent_internal_token(request):
            return server.JSONResponse(
                status_code=403,
                content={"success": False, "error": "Invalid internal AI worker token."},
            )
        try:
            payload = await request.json()
            return server._apply_ai_agent_runtime_settings(payload)
        except ValueError as exc:
            return server.JSONResponse(
                status_code=400,
                content={"success": False, "error": str(exc)},
            )
        except Exception as exc:
            server.LOGGER.warning("AI worker runtime-setting update failed: %s", exc)
            return server.JSONResponse(
                status_code=500,
                content={"success": False, "error": "Runtime-setting update failed."},
            )

    # REST API Endpoints for external chat applications
    @app_instance.post("/api/chat", response_model=ChatResponse)
    async def chat_endpoint(chat_request: ChatRequest):
        """
        Main chat endpoint for external applications.

        Send a message to the AutoYou AI Agent and receive a response.
        Supports session management for conversation continuity.
        """
        # Construct the AI Agent Server URL from the configured port
        ai_agent_url = f"http://localhost:{server.AI_AGENT_SERVER_PORT}"
        return await server.process_chat_message(chat_request, ai_agent_url)

    @app_instance.get("/api/sessions/{user_id}/{session_id}", response_model=SessionInfo)
    async def get_session_endpoint(user_id: str, session_id: str):
        """
        Get information about a specific session.
        """
        return await server.get_session_info(user_id, session_id)

    @app_instance.get("/api/status", response_model=APIStatus)
    async def api_status_endpoint():
        """
        Get the current API status and agent information.
        """
        return await server.get_api_status()

    @app_instance.get("/api/docs")
    async def api_documentation():
        """
        API documentation endpoint with usage examples.
        """
        return {
            "title": "AutoYou AI Agent REST API",
            "version": "1.0.0",
            "description": "REST API for interacting with AutoYou AI Agent",
            "endpoints": {
                "/api/chat": {
                    "method": "POST",
                    "description": "Send a message to the agent and receive a response",
                    "example_request": {
                        "message": "Hello, how can you help me?",
                        "session_id": "optional-session-id",
                        "user_id": "test_user123",
                        "context": [],
                        "metadata": {}
                    },
                    "example_response": {
                        "response": "Hello! I'm AutoYou, your AI assistant...",
                        "session_id": "generated-or-provided-session-id",
                        "message_id": "unique-message-id",
                    }
                }
            }
        }
