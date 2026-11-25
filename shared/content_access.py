"""What the caller of the content API may do.

The owner at this computer may do everything. A browser forwarded from another
device carries the role its pairing was given (viewer, editor or admin) in a
header only AutoYou stamps; the shared remote policy decides what that role may
do, and storage changes and removals are kept for the admin role. The server
checks every action itself, so a screen that hides a button is never the only
guard.
"""
from __future__ import annotations

from typing import Iterable

from fastapi.responses import JSONResponse

from shared.remote_access_policy import (
    REMOTE_ACCESS_ADMIN,
    REMOTE_BROWSER_HEADER,
    normalize_remote_access_role,
    remote_http_request_allowed,
)

ROLE_HEADER = "X-AutoYou-Remote-Access-Role"
OWNER = "owner"


def role_of(request) -> str:
    """`owner` for the person at this computer, otherwise the pairing's role."""
    headers = getattr(request, "headers", None) or {}
    if not headers.get(REMOTE_BROWSER_HEADER, "").strip() and not headers.get(ROLE_HEADER, "").strip():
        return OWNER
    return normalize_remote_access_role(headers.get(ROLE_HEADER))


def is_admin_request(request) -> bool:
    if request is None:
        return False
    try:
        from autoyou_agents.shared_tools.scheduler_mission_control import _runtime_server
        return bool(_runtime_server()._is_logged_in(request))
    except Exception:
        return False


def is_agent_scoped_request(request) -> bool:
    """True when the caller is authenticated via an agent session (such as Organizer) rather than Admin."""
    if request is None or is_admin_request(request):
        return False
    state = getattr(request, "state", None)
    if getattr(state, "content_auth_agent", ""):
        return True
    url = getattr(request, "url", None)
    path = str(getattr(url, "path", "") or (getattr(request, "scope", None) or {}).get("path", "") or "")
    if path.startswith(("/api/content", "/agent/organizer_agent")):
        return True
    headers = getattr(request, "headers", None) or {}
    if str(headers.get("x-autoyou-agent-frontend", "")).strip() == "organizer_agent":
        return True
    return False


def source_allowed(request, source: str, *, ignore_privacy: bool = False) -> bool:
    """True when `request` may access content belonging to `source`."""
    if not source:
        return True
    from shared.content_library import ContentLibrary
    if not ignore_privacy and not ContentLibrary.source_enabled(source):
        return False
    from shared import content_agents
    if source in {"organizer_agent", "files_agent"} or content_agents.is_custom(source):
        return True
    if not is_agent_scoped_request(request):
        return True
    try:
        from autoyou_agents.shared_tools import scheduler_mission_control as smc
        state = smc._describe_auth_state(request, source)
        if isinstance(state, dict) and state.get("authenticated"):
            return True
        chat_state = smc._describe_chat_auth_state(request, source)
        if isinstance(chat_state, dict) and chat_state.get("authenticated"):
            return True
    except Exception:
        return False
    return False


def allowed_sources_filter(request, sources: Iterable[str] | None = None, *, include_disabled=False) -> set[str] | None:
    """Return the subset of catalog sources `request` may read, or None when unrestricted for admin."""
    from shared.content_library import ContentLibrary
    if sources is None:
        if not is_agent_scoped_request(request):
            return None
        library = ContentLibrary()
        with library.connect() as db:
            known = {row[0] for row in db.execute("SELECT DISTINCT source FROM content")}
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_agents'").fetchone():
                known.update(row[0] for row in db.execute("SELECT source FROM content_agents"))
        sources = known
    return {source for source in sources if source_allowed(request, source, ignore_privacy=include_disabled)}


def deny_source(request, source: str, *, ignore_privacy: bool = False):
    """None when `request` may access `source`, else a 404 (if privacy-disabled) or 403 JSONResponse."""
    if not source:
        return None
    from shared.content_library import ContentLibrary
    if not ignore_privacy and not ContentLibrary.source_enabled(source):
        return JSONResponse({"error": "Content not found"}, status_code=404, headers={"Cache-Control": "no-store"})
    if source_allowed(request, source, ignore_privacy=True):
        return None
    from shared import content_agents
    info = content_agents.describe(source)
    return JSONResponse(
        {"error": f"Sign in to {info['name']} to access its content.", "source": source},
        status_code=403,
        headers={"Cache-Control": "no-store"},
    )


def abilities(request, source: str | None = None) -> dict:
    role = role_of(request)
    full = role in (OWNER, REMOTE_ACCESS_ADMIN)
    can_read = True if source is None else source_allowed(request, source)
    return {
        "role": role,
        "read": can_read,
        # Organizing and adding go through the same method policy as every other agent app.
        "edit": can_read and (full or remote_http_request_allowed(role, "PATCH", "/items/0")),
        "upload": can_read and (full or remote_http_request_allowed(role, "POST", "/upload-sessions")),
        "delete": can_read and full,
        # An agent credential is not an Admin credential. Migration and global
        # indexing settings affect sources outside the agent's granted scope.
        "manage": full and not is_agent_scoped_request(request),
    }


def public_settings(request) -> dict:
    from shared.content_library import settings
    values = settings()
    if not abilities(request)["manage"]:
        values["output_root"] = ""
    return values


def deny(request, action: str, source: str | None = None):
    """None when allowed, else a 403/404 that names the role or source permission it lacks."""
    if source is not None:
        source_denied = deny_source(request, source)
        if source_denied is not None:
            return source_denied
    allowed = abilities(request, source=source)
    if allowed.get(action):
        return None
    if action == "manage" and is_agent_scoped_request(request):
        return JSONResponse({"error": "Sign in to Admin to change storage and library-wide settings."},
                            status_code=403, headers={"Cache-Control": "no-store"})
    wording = {"read": "read content", "edit": "organize content", "upload": "add content", "delete": "remove content", "manage": "change storage settings"}
    return JSONResponse({"error": f"Remote access is set to {allowed['role']}; {wording.get(action, action)} requires {'admin' if action in ('delete', 'manage') else 'editor'} access."},
                        status_code=403, headers={"Cache-Control": "no-store"})
