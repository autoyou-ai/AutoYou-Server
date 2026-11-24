"""What the caller of the content API may do.

The owner at this computer may do everything. A browser forwarded from another
device carries the role its pairing was given (viewer, editor or admin) in a
header only AutoYou stamps; the shared remote policy decides what that role may
do, and storage changes and removals are kept for the admin role. The server
checks every action itself, so a screen that hides a button is never the only
guard.
"""
from __future__ import annotations

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
    headers = request.headers
    if not headers.get(REMOTE_BROWSER_HEADER, "").strip() and not headers.get(ROLE_HEADER, "").strip():
        return OWNER
    return normalize_remote_access_role(headers.get(ROLE_HEADER))


def abilities(request) -> dict:
    role = role_of(request)
    full = role in (OWNER, REMOTE_ACCESS_ADMIN)
    return {
        "role": role,
        "read": True,
        # Organizing and adding go through the same method policy as every other agent app.
        "edit": full or remote_http_request_allowed(role, "PATCH", "/items/0"),
        "upload": full or remote_http_request_allowed(role, "POST", "/upload-sessions"),
        "delete": full,
        "manage": full,
    }


def deny(request, action: str):
    """None when allowed, else a 403 that names the role and what it lacks."""
    allowed = abilities(request)
    if allowed.get(action):
        return None
    wording = {"edit": "organize content", "upload": "add content", "delete": "remove content", "manage": "change storage settings"}
    return JSONResponse({"error": f"Remote access is set to {allowed['role']}; {wording.get(action, action)} requires {'admin' if action in ('delete', 'manage') else 'editor'} access."},
                        status_code=403, headers={"Cache-Control": "no-store"})
