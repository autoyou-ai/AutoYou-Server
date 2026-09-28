# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-652076696120284254432061-f0d27e2df63ed6ca4acc6fcb

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-652076696120284254432061-f0d27e2df63ed6ca4acc6fcb"


import asyncio
import inspect
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from autoyou_agents.shared_tools.scheduler_mission_control import (
    _api_auth_error,
    _json_response,
    create_agent_website_app,
)
from shared.url_safety import resolve_safe_http_ip

from ...proxy import MAX_REQUEST_BYTES, fetch_target, normalize_websocket_target, rewrite_html

AGENT_NAME = "proxy_agent"
APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
ASSETS_DIR = FRONTEND_DIR / "assets"
INDEX_PATH = FRONTEND_DIR / "index.html"
DESCRIPTION = "OTP-gated server-side web relay using the connected computer's internet."
MAX_WS_MESSAGE_BYTES = 1_048_576


def _error(message: str, status_code: int = 400):
    return _json_response({"success": False, "error": message}, status_code=status_code)


def _endpoint(request: Request) -> str:
    # The WebRTC server adds this header for path-proxied agent requests. A
    # direct same-port browser uses the short local route instead.
    return "/agent/proxy_agent/proxy" if request.headers.get("X-AutoYou-Agent-Frontend") else "/proxy"


async def _serve(request: Request, target: str):
    if error := _api_auth_error(AGENT_NAME, request):
        return error
    try:
        if int(request.headers.get("content-length") or 0) > MAX_REQUEST_BYTES:
            return _error("Request body is too large.", 413)
    except ValueError:
        pass
    try:
        body = await request.body()
        upstream = fetch_target(
            target,
            method=request.method,
            body=body,
            headers={
                "Accept": request.headers.get("Accept", ""),
                "Accept-Language": request.headers.get("Accept-Language", ""),
                "Content-Type": request.headers.get("Content-Type", ""),
            },
        )
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception:
        return _error("The server could not reach that public website.", 502)

    content = upstream.body
    if upstream.content_type == "text/html" and content:
        content = rewrite_html(content, base_url=upstream.final_url, endpoint=_endpoint(request))
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if upstream.content_type:
        headers["Content-Type"] = f"{upstream.content_type}; charset=utf-8" if upstream.content_type.startswith("text/") else upstream.content_type
    if upstream.content_type == "text/html":
        headers["Content-Security-Policy"] = "default-src 'self' data: blob:; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; connect-src 'self' ws: wss:; frame-src 'self' data: blob:"
    return Response(content=content, status_code=upstream.status_code, headers=headers)


async def _serve_websocket(websocket: WebSocket) -> None:
    if _api_auth_error(AGENT_NAME, websocket) is not None:
        await websocket.close(code=1008, reason="OTP authentication required")
        return
    try:
        target = normalize_websocket_target(websocket.query_params.get("url", ""))
    except ValueError:
        await websocket.close(code=1008, reason="Public WebSocket target required")
        return

    try:
        import websockets
    except ImportError:
        await websocket.close(code=1011, reason="WebSocket relay unavailable")
        return

    parsed = urlparse(target)
    try:
        safe_ip = resolve_safe_http_ip(parsed.hostname or "")
    except Exception:
        await websocket.close(code=1008, reason="WebSocket target rejected")
        return

    requested_protocols = [
        item.strip()
        for item in str(websocket.headers.get("sec-websocket-protocol") or "").split(",")
        if item.strip()
    ]
    connect_kwargs = {
        "max_size": MAX_WS_MESSAGE_BYTES,
        "max_queue": 16,
        "compression": None,
        "open_timeout": 10,
        "close_timeout": 5,
        "subprotocols": requested_protocols or None,
    }
    header_param = (
        "additional_headers"
        if "additional_headers" in inspect.signature(websockets.connect).parameters
        else "extra_headers"
    )
    connect_kwargs[header_param] = {
        "User-Agent": "AutoYou Proxy Agent/1.0 (self-hosted)",
        "Accept-Language": str(websocket.headers.get("accept-language") or "en-US,en;q=0.8")[:128],
    }
    if "proxy" in inspect.signature(websockets.connect).parameters:
        connect_kwargs["proxy"] = None
    connect_kwargs["host"] = safe_ip
    if parsed.scheme == "wss":
        import ssl

        try:
            import certifi

            connect_kwargs["ssl"] = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            pass
        connect_kwargs["server_hostname"] = parsed.hostname

    try:
        async with websockets.connect(target, **connect_kwargs) as upstream:
            # The upstream handshake is complete; mirror only its negotiated
            # subprotocol. Cookies, Origin, and Authorization never leave here.
            await websocket.accept(subprotocol=getattr(upstream, "subprotocol", None))

            async def client_to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        await upstream.close()
                        return
                    if message.get("text") is not None:
                        if len(message["text"].encode("utf-8")) > MAX_WS_MESSAGE_BYTES:
                            await upstream.close(code=1009, reason="WebSocket message is too large")
                            await websocket.close(code=1009, reason="WebSocket message is too large")
                            return
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        if len(message["bytes"]) > MAX_WS_MESSAGE_BYTES:
                            await upstream.close(code=1009, reason="WebSocket message is too large")
                            await websocket.close(code=1009, reason="WebSocket message is too large")
                            return
                        await upstream.send(message["bytes"])

            async def upstream_to_client() -> None:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(str(message))

            client_task = asyncio.create_task(client_to_upstream())
            upstream_task = asyncio.create_task(upstream_to_client())
            done, pending = await asyncio.wait(
                {client_task, upstream_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                with suppress(BaseException):
                    task.result()
            if upstream_task in done:
                with suppress(Exception):
                    await websocket.close(code=1000, reason="Upstream closed")
    except WebSocketDisconnect:
        return
    except Exception:
        with suppress(Exception):
            await websocket.close(code=1011, reason="Upstream WebSocket relay failed")


def _extra_routes(app, agent_name: str) -> None:
    @app.get("/health")
    def health():
        return {"status": "ok", "agent_name": agent_name, "server_originated": True}

    @app.get("/api/status")
    def status(request: Request):
        if error := _api_auth_error(agent_name, request):
            return error
        return _json_response({"success": True, "agent_name": agent_name, "direct_port": 7077, "server_originated": True, "websocket_relay": True})

    @app.api_route("/proxy", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy(request: Request, url: str = ""):
        return await _serve(request, url)

    @app.websocket("/proxy")
    async def proxy_websocket(websocket: WebSocket):
        await _serve_websocket(websocket)

    @app.websocket("/ws")
    async def ws_websocket(websocket: WebSocket):
        await _serve_websocket(websocket)

    @app.api_route("/{target_path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def direct_path(request: Request, target_path: str):
        if target_path.startswith("api/") or target_path in {"api", "health", "proxy"}:
            return _error("Unknown proxy route.", 404)
        return await _serve(request, target_path)


app = create_agent_website_app(
    agent_name=AGENT_NAME,
    title="Internet Relay",
    description=DESCRIPTION,
    index_path=INDEX_PATH,
    assets_dir=ASSETS_DIR,
    extra_routes_fn=_extra_routes,
)
