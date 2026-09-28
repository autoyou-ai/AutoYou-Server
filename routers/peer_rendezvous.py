# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-e9f3ce600868fb1a1aa978dc

"""Peer Link rendezvous routes - the answer leg, without a human in it.

These endpoints are deliberately unauthenticated. The whole point of an invite
is that the person accepting it is a stranger to this server until the pairing
completes, so requiring a session here would defeat the feature.

What replaces authentication is that there is nothing here worth taking:

* every payload is ciphertext the server cannot read (see
  :mod:`shared.peer_rendezvous`);
* a slot is addressed by a 256-bit invitation id that only the invite holder
  knows, and unknown ids are answered exactly like unanswered ones, so the
  surface cannot be enumerated;
* an answer can be posted once and collected once, so neither leg can be
  replayed or substituted;
* every route is rate limited per client and globally, and slots self-expire.

Registered on ``auth_app`` beside ``/auth`` and ``/signal`` because that is the
existing public pairing surface. Nothing here touches admin state.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-e9f3ce600868fb1a1aa978dc"


from typing import Any, Callable, Dict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from shared.peer_invite_page import render_invite_landing_page

from shared.peer_rendezvous import (
    MAX_ENVELOPE_BYTES,
    RENDEZVOUS,
    RendezvousError,
    SlotState,
)

#: One generic failure for every rejected request. Distinguishing "no such
#: invitation" from "already answered" would turn these routes into an oracle.
_GENERIC_REJECTION = "That invite is no longer available."

#: Bounded read so a request body cannot be used to exhaust memory before the
#: envelope size check runs.
_MAX_BODY_BYTES = MAX_ENVELOPE_BYTES + 4096


def register_routes(
    admin_app: FastAPI,
    auth_app: FastAPI,
    server: Any,
) -> Dict[str, Callable[..., Any]]:
    def _rejected(status: int = 404) -> JSONResponse:
        return JSONResponse(
            status_code=status, content={"success": False, "error": _GENERIC_REJECTION}
        )

    def _rate_limited(request: Request, bucket: str) -> JSONResponse | None:
        """Apply the shared public-surface limiters.

        Reuses the same limiters as ``/auth`` so a burst against the rendezvous
        cannot be used to sidestep the pairing budget, and keys on the real
        client IP behind the tunnel bridge.
        """
        if not server.AUTH_GLOBAL_RATE_LIMITER.is_allowed("global"):
            server.LOGGER.warning("Global rate limit exceeded for %s", bucket)
            return JSONResponse(
                status_code=429,
                content={"success": False, "error": "Too many requests. Try again shortly."},
            )
        client_ip = server._rate_limit_client_key(request)
        if not server.AUTH_RATE_LIMITER.is_allowed(f"{bucket}:{client_ip}"):
            server.LOGGER.warning("Rate limit exceeded for %s from %s", bucket, client_ip)
            return JSONResponse(
                status_code=429,
                content={"success": False, "error": "Too many requests. Try again shortly."},
            )
        return None

    async def _json_body(request: Request) -> Dict[str, Any]:
        raw = await request.body()
        if len(raw) > _MAX_BODY_BYTES:
            raise RendezvousError("request body is too large")
        try:
            import json

            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception as exc:
            raise RendezvousError("request body is not JSON") from exc
        if not isinstance(payload, dict):
            raise RendezvousError("request body is not an object")
        return payload

    # ------------------------------------------------------------------
    # Inviter: publish an invitation
    # ------------------------------------------------------------------
    @auth_app.post("/v1/peer/rendezvous/open")
    async def peer_rendezvous_open(request: Request):
        """Publish an encrypted offer under a freshly minted invitation id.

        Called by the inviting device, which already holds an admin session for
        its own server; the route stays open because the same code path serves
        a client relaying through a tunnel where that session is not presented.
        The invitation id is unguessable, so publishing is not a useful action
        for anyone who does not already have one.
        """
        limited = _rate_limited(request, "peer-rendezvous-open")
        if limited is not None:
            return limited
        try:
            payload = await _json_body(request)
            invitation_id = payload.get("invitation_id") or payload.get("invitationId")
            offer = payload.get("offer") or ""
            RENDEZVOUS.open(invitation_id, offer)
        except RendezvousError as exc:
            server.LOGGER.info("Rejected rendezvous open: %s", exc)
            return _rejected(400)
        return {"success": True}

    # ------------------------------------------------------------------
    # Invitee: read the offer, then answer it
    # ------------------------------------------------------------------
    @auth_app.get("/v1/peer/rendezvous/{invitation_id}/offer")
    async def peer_rendezvous_offer(invitation_id: str, request: Request):
        """Hand the invitee the inviter's encrypted offer.

        Returns 404 for unknown, expired and exhausted invitations alike.
        """
        limited = _rate_limited(request, "peer-rendezvous-offer")
        if limited is not None:
            return limited
        try:
            offer = RENDEZVOUS.fetch_offer(invitation_id)
        except RendezvousError:
            return _rejected()
        if not offer:
            return _rejected()
        return {"success": True, "offer": offer}

    @auth_app.post("/v1/peer/rendezvous/{invitation_id}/answer")
    async def peer_rendezvous_answer(invitation_id: str, request: Request):
        """Store the invitee's encrypted answer, or its decline."""
        limited = _rate_limited(request, "peer-rendezvous-answer")
        if limited is not None:
            return limited
        try:
            payload = await _json_body(request)
            if bool(payload.get("declined")):
                RENDEZVOUS.post_decline(invitation_id)
            else:
                RENDEZVOUS.post_answer(invitation_id, payload.get("answer") or "")
        except RendezvousError as exc:
            server.LOGGER.info("Rejected rendezvous answer: %s", exc)
            return _rejected()
        return {"success": True}

    # ------------------------------------------------------------------
    # Inviter: collect the answer
    # ------------------------------------------------------------------
    @auth_app.get("/v1/peer/rendezvous/{invitation_id}/answer")
    async def peer_rendezvous_collect(invitation_id: str, request: Request):
        """Collect the answer once.

        Polled by the inviting device while the invite is outstanding. A
        ``pending`` result covers "not answered yet", "never existed" and
        "expired", which is what keeps this from being an enumeration oracle.
        """
        limited = _rate_limited(request, "peer-rendezvous-collect")
        if limited is not None:
            return limited
        try:
            state, envelope = RENDEZVOUS.collect(invitation_id)
        except RendezvousError:
            return {"success": True, "state": SlotState.PENDING.value}
        if state is SlotState.READY:
            return {"success": True, "state": state.value, "answer": envelope}
        return {"success": True, "state": state.value}

    @auth_app.post("/v1/peer/rendezvous/{invitation_id}/close")
    async def peer_rendezvous_close(invitation_id: str, request: Request):
        """Withdraw an invitation. Always reports success, never confirms existence."""
        limited = _rate_limited(request, "peer-rendezvous-close")
        if limited is not None:
            return limited
        RENDEZVOUS.close(invitation_id)
        return {"success": True}

    # ------------------------------------------------------------------
    # The invite landing page
    # ------------------------------------------------------------------
    @admin_app.get("/peer/add", response_class=HTMLResponse)
    @auth_app.get("/peer/add", response_class=HTMLResponse)
    async def peer_invite_landing(request: Request):
        """Render the page someone lands on after tapping an invite link.

        Public by necessity: the invitee has no relationship with this server
        yet. It is also entirely safe to serve anonymously, because the page is
        static and the invitation lives in the URL fragment - which the browser
        never sends here. Nothing about the invite is read, logged or echoed
        server-side; the page resolves it on the device and hands it to the app.
        """
        response = HTMLResponse(render_invite_landing_page())
        # An invite is one-time and personal; never let it sit in a shared cache.
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # ------------------------------------------------------------------
    # Operator diagnostics (counts only, admin-gated)
    # ------------------------------------------------------------------
    @admin_app.get("/api/peer/rendezvous/status")
    async def peer_rendezvous_status(request: Request):
        auth_response = server._require_api_login_json(request)
        if auth_response is not None:
            return auth_response
        return {"success": True, **RENDEZVOUS.stats()}

    return {
        "peer_rendezvous_open": peer_rendezvous_open,
        "peer_rendezvous_offer": peer_rendezvous_offer,
        "peer_rendezvous_answer": peer_rendezvous_answer,
        "peer_rendezvous_collect": peer_rendezvous_collect,
        "peer_rendezvous_close": peer_rendezvous_close,
        "peer_invite_landing": peer_invite_landing,
        "peer_rendezvous_status": peer_rendezvous_status,
    }
