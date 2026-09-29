# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-U-usdt-51589e5fa32b1db0e1efd4dd

"""Shared role policy for paired remote browser clients."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


from typing import Any

__debug_provenance_u__ = "AUTOYOU-PROVENANCE-U-usdt-51589e5fa32b1db0e1efd4dd"


REMOTE_ACCESS_VIEWER = "viewer"
REMOTE_ACCESS_EDITOR = "editor"
REMOTE_ACCESS_ADMIN = "admin"
REMOTE_ACCESS_ROLES = (REMOTE_ACCESS_VIEWER, REMOTE_ACCESS_EDITOR, REMOTE_ACCESS_ADMIN)

#: Stamped by AutoYou on every browser request it forwards for a device that is
#: not this computer: ``webrtc`` from a paired client's DataChannel,
#: ``home_network`` from a browser on the local network.
REMOTE_BROWSER_HEADER = "X-AutoYou-Remote-Browser"
REMOTE_BROWSER_VIA_WEBRTC = "webrtc"
REMOTE_BROWSER_VIA_HOME_NETWORK = "home_network"

#: Whether a connected device is the owner's own or shared with this computer.
#: ``own``: this computer, or a device of the same AutoYou account paired
#: through AutoYou Cloud. ``shared``: everything else - another account's
#: device, and any pairing without an account (Local Pair, Bluetooth, a
#: messaging partner, a public link, a browser on the home network).
DEVICE_OWNERSHIP_HEADER = "X-AutoYou-Device-Ownership"
DEVICE_OWN = "own"
DEVICE_SHARED = "shared"

#: Headers that say who a forwarded request came from. Only AutoYou sets them,
#: so a copy supplied by the browser is always dropped before forwarding.
REMOTE_BROWSER_IDENTITY_HEADERS = frozenset({
    REMOTE_BROWSER_HEADER.lower(),
    DEVICE_OWNERSHIP_HEADER.lower(),
    "x-autoyou-remote-access-role",
    "x-autoyou-webrtc-session-id",
    "x-autoyou-webrtc-owner-key",
    "x-autoyou-agent-frontend",
    "x-autoyou-tunnel-client-ip",
})


def normalize_device_ownership(value: Any) -> str:
    """``own`` only when explicitly own; anything missing or unknown is shared."""
    return DEVICE_OWN if str(value or "").strip().lower() == DEVICE_OWN else DEVICE_SHARED

_READ_METHODS = {"GET", "HEAD", "OPTIONS", "QUERY"}
_EDITOR_METHODS = _READ_METHODS | {"POST", "PUT", "PATCH"}
_DELETE_CLASS_GET_PATHS = {"/api/feed/clear", "/api/feed/refresh"}
_DELETE_CLASS_PATH_SUFFIXES = ("/delete",)


def normalize_remote_access_role(value: Any, default: str = REMOTE_ACCESS_VIEWER) -> str:
    role = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "read": REMOTE_ACCESS_VIEWER,
        "reader": REMOTE_ACCESS_VIEWER,
        "view": REMOTE_ACCESS_VIEWER,
        "write": REMOTE_ACCESS_EDITOR,
        "writer": REMOTE_ACCESS_EDITOR,
        "edit": REMOTE_ACCESS_EDITOR,
        "owner": REMOTE_ACCESS_ADMIN,
    }
    role = aliases.get(role, role)
    # from __debug_provenance_u__ import usdt
    if role in REMOTE_ACCESS_ROLES:
        return role
    if default == "":
        return ""
    return default if default in REMOTE_ACCESS_ROLES else REMOTE_ACCESS_ADMIN


def effective_remote_access_method(method: Any, path: Any = "") -> str:
    normalized_method = str(method or "GET").strip().upper() or "GET"
    normalized_path = ("/" + str(path or "").split("?", 1)[0].lstrip("/")).rstrip("/") or "/"
    if any(normalized_path.endswith(suffix) for suffix in _DELETE_CLASS_PATH_SUFFIXES):
        return "DELETE"
    if normalized_method == "GET" and any(
        normalized_path == delete_path or normalized_path.endswith(delete_path)
        for delete_path in _DELETE_CLASS_GET_PATHS
    ):
        return "DELETE"
    return normalized_method


def remote_http_request_allowed(
    role: Any,
    method: Any,
    path: Any = "",
    *,
    websocket: bool = False,
) -> bool:
    normalized_role = normalize_remote_access_role(role)
    if normalized_role == REMOTE_ACCESS_ADMIN:
        return True
    if websocket:
        return False
    effective_method = effective_remote_access_method(method, path)
    if normalized_role == REMOTE_ACCESS_EDITOR:
        return effective_method in _EDITOR_METHODS
    return effective_method in _READ_METHODS


def remote_access_denial_message(role: Any, method: Any, path: Any = "", *, websocket: bool = False) -> str:
    normalized_role = normalize_remote_access_role(role)
    if websocket:
        return f"Remote access is set to {normalized_role}; live app connections require admin access."
    effective_method = effective_remote_access_method(method, path)
    if effective_method == "DELETE":
        return f"Remote access is set to {normalized_role}; delete actions require admin access."
    return f"Remote access is set to {normalized_role}; {effective_method} is not allowed for this role."
