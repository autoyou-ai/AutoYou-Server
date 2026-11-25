"""Authorize a replication request forwarded by the paired WebRTC session."""
from fastapi import HTTPException

from shared import content_access
from shared.content_library import settings
from shared.remote_access_policy import REMOTE_BROWSER_HEADER, REMOTE_BROWSER_VIA_WEBRTC


def require_pair(request, agent):
    if (not request.client or request.client.host not in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
            or request.headers.get(REMOTE_BROWSER_HEADER) != REMOTE_BROWSER_VIA_WEBRTC
            or request.headers.get("x-autoyou-agent-frontend") != agent
            or not request.headers.get("x-autoyou-webrtc-session-id")
            or request.headers.get(content_access.ROLE_HEADER) not in {"viewer", "editor", "admin"}):
        raise HTTPException(403, "Sync requires a paired computer connection")
    option = "notes_sync_enabled" if agent == "notes_agent" else "content_sync_enabled"
    if not settings()[option]:
        raise HTTPException(403, "Sync is disabled in My Content on this computer")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.url.path.endswith("/auth/login") and not request.url.path.endswith("/publish-auth/login"):
        action = "delete" if request.method == "DELETE" else "edit"
        if not content_access.abilities(request)[action]:
            raise HTTPException(403, "The paired device does not have permission for this change")


def install_collection_sync(app, agent):
    """Require the paired session and the computer's permission on every sync transfer."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    if agent not in {"diary_agent", "page_agent", "music_agent", "media_agent", "games_agent",
                     "reminders_agent", "profile_agent", "organizer_agent"}:
        return

    @app.middleware("http")
    async def sync_access(request: Request, call_next):
        requested = request.headers.get("x-autoyou-content-sync")
        policy = request.method == "GET" and request.scope["path"].removeprefix("/agent/" + agent) == "/api/sync/policy"
        if requested is not None or policy:
            if requested is not None and requested != agent:
                return JSONResponse({"error": "Wrong collection for this transfer"}, status_code=403)
            try:
                require_pair(request, agent)
            except HTTPException as error:
                return JSONResponse({"error": error.detail}, status_code=error.status_code)
            if policy:
                return JSONResponse({"version": 1, "agent": agent, "role": content_access.role_of(request)}, headers={"Cache-Control": "no-store"})
        return await call_next(request)
