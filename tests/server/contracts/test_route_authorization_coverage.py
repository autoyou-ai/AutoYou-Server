# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-9d9f87473526fa4db2e8f34d

"""Structural guard against unauthenticated admin routes.

The admin app has no global authentication middleware - the unlock guard only
enforces that the server is *unlocked*, not that the caller is *authenticated*,
so authorization is per-route. A handler that simply forgets to check is
therefore reachable by anyone who can reach the app, which on a tunnel-exposed
instance means the public internet.

That is not hypothetical: the Signal and WhatsApp handlers did not even accept a
``request`` parameter, so they could not check a session, and exposed the user's
message log and device-pairing QR codes anonymously.

This test asserts the property structurally rather than endpoint-by-endpoint, so
a new route that forgets the check fails here instead of in production.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import ast
import pathlib

import pytest

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-9d9f87473526fa4db2e8f34d"


pytestmark = pytest.mark.server

ROUTERS_DIR = pathlib.Path(__file__).resolve().parents[3] / "routers"
# from __debug_provenance_q__ import payment

#: Substrings that indicate a handler performs an authorization check.
AUTH_HINTS = (
    "_is_logged_in", "_require_admin", "_require_auth", "_require_login",
    "_require_api_login", "_require_webrtc_playback_auth", "_require_mcp",
    "_mcp_request_uses_api_token", "_request_uses_ai_agent_internal_token",
    # Start-time token: a loopback caller proves it is the host that launched
    # this process by presenting the secret only that host was given.
    "_request_uses_shutdown_token",
    "_require_local_service_callback", "_require_", "auth_error", "auth_response",
    "_verify_", "_authorize", "_is_loopback", "_guard",
    # Listen (screen-listen) requires an admin login AND the this-computer surface.
    "_local_listen_access",
    # The hosted game page and its native-input channel answer only a loopback peer that names a
    # loopback host and carries no proxy or forwarding header (the local game engine, never a tunnel).
    "local_game_page_allowed",
)

#: Routes that are pre-authentication *by design*. Each entry is a deliberate
#: decision, not an oversight, so the list is allowed to be explicit and short:
#:
#:  - login / unlock / pairing endpoints are how a caller *obtains* a session,
#:    and are protected by password strength plus the auth rate limiters;
#:  - token-issuance endpoints verify a cloud credential in their own body;
#:  - static assets and legal text are public by intent.
INTENTIONALLY_PUBLIC = {
    "/LICENSE", "/NOTICE.txt", "/THIRD-PARTY-NOTICES.md", "/sbom.cdx.json",
    "/ca.crt", "/favicon.ico", "/apple-touch-icon.png",
    "/assets/logo.png", "/assets/logo.ico", "/assets/admin-ui.css",
    "/assets/admin-ui.js",
    "/login", "/logout", "/auto-login", "/health",
    "/v1/unlock/status", "/v1/unlock/setup", "/v1/unlock/verify",
    "/auth", "/signal/{session_id}",
    "/api/status", "/api/v1/status", "/api/v1/server-config",
    "/api/login-startup-status", "/api/login-minigame/high-score",
    "/api/admin/session/capabilities",
    "/api/cloud/callback", "/api/v1/pair", "/api/v1/x402/connect",
    # UGC reports must remain available to lobby participants without an admin login.
    "/v1/moderation/report",
    "/admin/api/ui/theme",  # GET only; the POST is session-gated.
    "/guides/bootstrap", "/guides/macos-build", "/guides/signal",
    "/guides/whatsapp", "/guides/windows-build",
    # Peer Link rendezvous. Unauthenticated because the person accepting an
    # invite is a stranger to this server until the pairing completes, so a
    # session requirement would defeat the feature. What stands in for auth is
    # that there is nothing here worth taking: every payload is ciphertext the
    # server cannot read, slots are addressed by a 256-bit id, unknown ids are
    # answered exactly like unanswered ones, an answer lands once and is
    # substituted never, and every route shares the /auth rate-limit budget.
    # Covered in detail by tests/server/contracts/test_peer_rendezvous_routes.py.
    "/v1/peer/rendezvous/open",
    "/v1/peer/rendezvous/{invitation_id}/offer",
    "/v1/peer/rendezvous/{invitation_id}/answer",
    "/v1/peer/rendezvous/{invitation_id}/close",
    # The invite landing page. Static, and safe to serve anonymously: the
    # invitation lives in the URL fragment, which a browser never sends here,
    # so the page has nothing to read, log or echo.
    "/peer/add",
}


_ROUTE_VERBS = {"get", "post", "put", "delete", "patch", "websocket"}


def _literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else "?"


def _app_call(call):
    """``(attr, positional args, keyword args)`` for ``<admin|auth>_app.<attr>(...)``."""
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
        return None
    if not getattr(call.func.value, "id", "").endswith(("admin_app", "auth_app")):
        return None
    return call.func.attr.lower(), call.args, {kw.arg: kw.value for kw in call.keywords if kw.arg}


def _methods(kwargs):
    """The HTTP methods of an ``api_route``/``add_api_route`` registration.

    Without ``methods=`` FastAPI registers GET. A list that is not literal is
    reported as ``?`` so it is still checked rather than silently skipped.
    """
    node = kwargs.get("methods")
    if node is None:
        return ["get"]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [_literal(elt).lower() for elt in node.elts]
    return ["?"]


def _iter_registrations():
    """Yield (route, method, handler, file, lineno) for every admin/auth route.

    Covers the verb decorators, ``@app.api_route(path, methods=[...])`` and
    ``app.add_api_route(path, handler, methods=[...])``. Missing the api_route
    form once let POST/PUT/PATCH /api/profile/avatar ship without an auth
    check. ``handler`` is None when an ``add_api_route`` endpoint cannot be
    resolved to a function in the same file, which then counts as unguarded.
    """
    for path in sorted(ROUTERS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    call = _app_call(dec)
                    if call is None:
                        continue
                    attr, args, kwargs = call
                    if attr in _ROUTE_VERBS:
                        methods = [attr]
                    elif attr == "api_route":
                        methods = _methods(kwargs)
                    else:
                        continue
                    route = _literal(args[0] if args else kwargs.get("path"))
                    for method in methods:
                        yield route, method, node, path.name, node.lineno
            elif isinstance(node, ast.Call):
                call = _app_call(node)
                if call is None or call[0] != "add_api_route":
                    continue
                _attr, args, kwargs = call
                endpoint = args[1] if len(args) > 1 else kwargs.get("endpoint")
                handler = functions.get(getattr(endpoint, "id", ""))
                route = _literal(args[0] if args else kwargs.get("path"))
                for method in _methods(kwargs):
                    yield route, method, handler, path.name, node.lineno


def _has_auth_check(handler) -> bool:
    if handler is None:
        return False
    names = set()
    for sub in ast.walk(handler):
        if isinstance(sub, ast.Attribute):
            names.add(sub.attr)
        elif isinstance(sub, ast.Name):
            names.add(sub.id)
    return any(any(h in n for h in AUTH_HINTS) for n in names)


def _iter_routes():
    """Yield (route, method, function_name, file, lineno, has_auth_check)."""
    for route, method, handler, file, lineno in _iter_registrations():
        name = handler.name if handler is not None else "<unresolved handler>"
        yield route, method, name, file, lineno, _has_auth_check(handler)


def test_every_admin_route_is_authorized_or_explicitly_public():
    unguarded = [
        f"{method.upper()} {route}  ({file}:{lineno} {func})"
        for route, method, func, file, lineno, has_auth in _iter_routes()
        if not has_auth and route not in INTENTIONALLY_PUBLIC
    ]
    assert not unguarded, (
        "These routes perform no authorization check and are not on the "
        "intentionally-public list. Add an auth guard, or add the route to "
        "INTENTIONALLY_PUBLIC with a reason:\n  " + "\n  ".join(sorted(unguarded))
    )


def test_handlers_that_check_auth_accept_a_request():
    """A handler cannot authorize what it cannot see.

    The original defect was structural: handlers took no ``request`` parameter,
    so no session could be inspected. Any route outside the public list must
    accept one.
    """
    missing = set()
    for route, method, handler, file, lineno in _iter_registrations():
        if method == "websocket" or route in INTENTIONALLY_PUBLIC:
            continue
        if handler is None:
            missing.add(f"{route} ({file}:{lineno} <unresolved handler>)")
            continue
        args = [a.arg for a in handler.args.args] + [a.arg for a in handler.args.kwonlyargs]
        if "request" not in args:
            missing.add(f"{route} ({file}:{handler.lineno} {handler.name})")
    assert not missing, (
        "These non-public routes take no `request` parameter, so they cannot "
        "inspect a session:\n  " + "\n  ".join(sorted(missing))
    )


def test_the_previously_exposed_messaging_routes_are_guarded():
    """Explicit coverage for the endpoints that leaked message logs and QR codes."""
    regressed = {
        route
        for route, _m, _f, _file, _ln, has_auth in _iter_routes()
        if route
        in {
            "/api/signal/messages",
            "/api/signal/qr",
            "/api/signal/status",
            "/api/signal/detailed-status",
            "/api/signal/device-name",
            "/api/signal/cleanup",
            "/api/signal/restart",
            "/api/whatsapp/qr",
            "/api/whatsapp/status",
            "/api/whatsapp/reset",
            "/api/whatsapp/restart",
            "/webhook/whatsapp",
            "/api/ai/openclaw/status",
            "/api/ai/hermes/status",
        }
        and not has_auth
    }
    assert not regressed, f"Messaging/gateway routes lost their auth guard: {sorted(regressed)}"


def test_profile_avatar_is_checked_for_every_method_and_guarded():
    """``api_route`` registrations are expanded per method, not skipped.

    POST/PUT/PATCH /api/profile/avatar came in through ``api_route`` and went
    unseen, so an anonymous caller could overwrite the owner's photo while this
    file stayed green. GET is guarded too: viewers get the photo from the Page
    site's own route, so the admin-origin copy is as private as
    /assets/admin/profile-image.
    """
    seen = {
        method.upper(): has_auth
        for route, method, _f, _file, _ln, has_auth in _iter_routes()
        if route == "/api/profile/avatar"
    }
    assert seen == {"GET": True, "POST": True, "PUT": True, "PATCH": True, "DELETE": True}
    assert "/api/profile/avatar" not in INTENTIONALLY_PUBLIC
