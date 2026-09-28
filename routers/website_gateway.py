# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-2d2030786345634238313737-49854a9bb40b36858ccfc0c3

"""Website apps through the admin port, behind the admin sign-in.

In the ``path_proxy`` home-network mode only the admin port listens beyond
loopback. A browser on another device signs in there - over HTTPS, since a
password over plain HTTP from the network is refused - and reaches every
website app under ``/agent/<name>/`` on that same origin. Each request is
handed to the page service in-process: no second listener, no extra hop, and
the same routes, shims and media streaming the websites port serves.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-2d2030786345634238313737-49854a9bb40b36858ccfc0c3"


from typing import Any, Callable, Dict

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, RedirectResponse

from shared.remote_access_policy import REMOTE_BROWSER_IDENTITY_HEADERS

_HTTP_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "QUERY"]

#: The page service's own paths, served unchanged on the admin origin.
WEBSITE_GATEWAY_PATHS = (
    "/websites",
    "/agent-websites",
    "/agent-frontends",
    "/api/websites",
    "/api/agent-websites",
    "/api/agent-frontends",
    "/api/agent-directory",
    "/agent/{agent_name}",
    "/agent/{agent_name}/{proxy_path:path}",
)

# Routing state from the admin app; the page service routes the request again.
_ADMIN_ROUTING_KEYS = frozenset({"endpoint", "path_params", "route", "router"})


class WebsiteGateway:
    """ASGI endpoint that serves the page service on the admin origin."""

    def __init__(self, server: Any):
        self.server = server

    def _page_service_app(self):
        server = self.server
        if not getattr(server, "AUTOYOU_PAGE_SERVICE_AVAILABLE", False):
            return None
        service = server.get_autoyou_page_service()
        return getattr(service, "app", None) if service is not None else None

    def forwarded_scope(self, scope: Dict[str, Any], connection: HTTPConnection) -> Dict[str, Any]:
        forwarded = {key: value for key, value in scope.items() if key not in _ADMIN_ROUTING_KEYS}
        if self.server._request_via_remote_browser_proxy(connection):
            # A paired device's browser: AutoYou stamped who it is, and the
            # website apps apply its remote client role from those headers.
            return forwarded
        # The operator, signed in with the admin password: drop any identity a
        # browser claimed and present this computer, which is the owner.
        forwarded["headers"] = [
            (key, value)
            for key, value in scope.get("headers") or []
            if key.decode("latin-1").lower() not in REMOTE_BROWSER_IDENTITY_HEADERS
        ]
        client = scope.get("client") or ("127.0.0.1", 0)
        forwarded["client"] = ("127.0.0.1", client[1] if len(client) > 1 else 0)
        return forwarded

    async def __call__(self, scope, receive, send) -> None:
        connection = HTTPConnection(scope)
        signed_in = self.server._is_logged_in(connection)
        if scope["type"] == "websocket":
            app = self._page_service_app() if signed_in else None
            if app is None:
                await send({"type": "websocket.close", "code": 1008})
                return
            await app(self.forwarded_scope(scope, connection), receive, send)
            return

        method = str(scope.get("method") or "GET").upper()
        path = str(scope.get("path") or "/")
        if not signed_in:
            wants_page = method in {"GET", "HEAD"} and "/api/" not in path
            response = (
                RedirectResponse(url="/login", status_code=302)
                if wants_page
                else JSONResponse({"success": False, "error": "Sign in to open website apps."}, status_code=401)
            )
            await response(scope, receive, send)
            return
        app = self._page_service_app()
        if app is None:
            response = JSONResponse(
                {"success": False, "error": "Website apps are not running on this computer."},
                status_code=503,
            )
            await response(scope, receive, send)
            return
        await app(self.forwarded_scope(scope, connection), receive, send)


def register_routes(admin_app: Any, auth_app: Any, server: Any) -> Dict[str, Callable[..., Any]]:
    del auth_app
    gateway = WebsiteGateway(server)
    for path in WEBSITE_GATEWAY_PATHS:
        admin_app.router.add_route(path, gateway, methods=_HTTP_METHODS, include_in_schema=False)
        if path.startswith("/agent/"):
            admin_app.router.add_websocket_route(path, gateway)
    return {"WEBSITE_GATEWAY": gateway}
