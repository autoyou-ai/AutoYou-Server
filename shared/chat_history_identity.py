# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Who a Chat & History conversation is with.

The admin Chat & History view lists conversations from every surface: this
admin page, paired apps, messaging partners, the MCP connector, guests relayed
through a paired device (Peer Relay), and Lobbies bridged to this computer.
Each one is attributed to a kind decided by the canonical owner id the server
assigned (``user::<transport>:<sender>``), never by a label a client chose, so
a paired device cannot make its conversation read as the owner's own.

Names are presentation only. A relayed guest's name is attested by the paired
device that relayed it and is kept only when the owner stores client names in
chat history, the same rule a directly paired device's name follows.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import re
from typing import Any, Dict, Mapping, Optional

#: The owner id this admin page chats as. Older builds showed it as the
#: "Active UserID"; it is an internal key, not a person.
ADMIN_CHAT_USER_ID = "admin-web-user"

KIND_SELF = "self"
KIND_DEVICE = "device"
KIND_MESSAGING = "messaging"
KIND_CONNECTOR = "connector"
KIND_PEER = "peer"
KIND_ROOM = "room"
KIND_GUEST = "guest"
KIND_EXTERNAL = "external"

#: Where the owner had this admin page open, stamped by the admin chat route
#: from the request itself.
ADMIN_SURFACE_THIS_COMPUTER = "this_computer"
ADMIN_SURFACE_SECURE_REMOTE_WEB = "secure_remote_web"
ADMIN_SURFACE_HOME_NETWORK = "home_network"
ADMIN_SURFACE_NETWORK = "network"
ADMIN_SURFACE_LABELS = {
    ADMIN_SURFACE_THIS_COMPUTER: "This computer",
    ADMIN_SURFACE_SECURE_REMOTE_WEB: "Secure Remote Web",
    ADMIN_SURFACE_HOME_NETWORK: "Home network browser",
    ADMIN_SURFACE_NETWORK: "Another device",
}

CLIENT_LABELS = {
    "ios": "iPhone / iPad",
    "android": "Android",
    "autoyou-chrome": "Chrome",
    "autoyou-v2-native": "AutoYou desktop",
    "admin-web": "Admin page",
    "autoyou-python": "Desktop client",
    "autoyou-datachannel": "Paired device",
    "autoyou-room-bridge": "Lobby",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "telegram_user": "Telegram",
    "signal": "Signal",
    "chatgpt": "ChatGPT",
    "scheduler": "Scheduler",
}
CHANNEL_LABELS = (
    ("user::telegram", "Telegram"),
    ("user::whatsapp", "WhatsApp"),
    ("user::signal", "Signal"),
    ("user::cloud", "Cloud Pair"),
    ("user::bluetooth", "Bluetooth"),
    ("user::local", "Local pair"),
)
PLATFORM_LABELS = {
    "ios": "iPhone / iPad",
    "ipados": "iPhone / iPad",
    "android": "Android",
    "macos": "Mac",
    "windows": "Windows",
    "linux": "Linux",
    "chrome": "Chrome",
    "python": "Desktop client",
}

_MESSAGING_TRANSPORTS = {"telegram", "telegram_user", "whatsapp", "signal"}
_MESSAGING_CLIENTS = {"telegram", "telegram_user", "whatsapp", "signal"}
_CONNECTOR_SOURCES = {"autoyou-mcp"}
_CONNECTOR_USER_IDS = {"autoyou-mcp"}
_PLATFORM_RE = re.compile(r"[^a-z0-9._-]+")

PEER_NAME_MAX_LENGTH = 120
PEER_PLATFORM_MAX_LENGTH = 32
PEER_MAX_HOP = 32


def _text(value: Any, limit: int) -> str:
    """One printable line, bounded; anything else becomes empty."""
    if not isinstance(value, str):
        return ""
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in value).split())
    return text[:limit]


def owner_transport(user_id: Any) -> str:
    """``local`` for ``user::local:Phone-1``; empty for a raw, non-canonical id."""
    value = str(user_id or "").strip()
    if not value.startswith("user::"):
        return ""
    transport, separator, _sender = value[len("user::"):].partition(":")
    return transport.strip().lower() if separator else ""


def normalize_admin_surface(value: Any) -> str:
    surface = str(value or "").strip().lower()
    return surface if surface in ADMIN_SURFACE_LABELS else ""


def admin_surface_label(value: Any) -> str:
    return ADMIN_SURFACE_LABELS.get(normalize_admin_surface(value), "Admin page")


def sanitize_peer_relay(value: Any) -> Dict[str, Any]:
    """The bounded presentation stored with a relayed guest's turn."""
    if not isinstance(value, Mapping):
        return {}
    out: Dict[str, Any] = {}
    hop = value.get("hop")
    if isinstance(hop, int) and not isinstance(hop, bool) and 1 <= hop <= PEER_MAX_HOP:
        out["hop"] = hop
    platform = _PLATFORM_RE.sub("", _text(value.get("platform"), PEER_PLATFORM_MAX_LENGTH).lower())
    if platform:
        out["platform"] = platform
    for key in ("name", "via_name"):
        name = _text(value.get(key), PEER_NAME_MAX_LENGTH)
        if name:
            out[key] = name
    via_user_id = _text(value.get("via_user_id"), 256)
    if via_user_id:
        out["via_user_id"] = via_user_id
    return out


def client_label(metadata: Mapping[str, Any]) -> str:
    display = _text(metadata.get("client_display_name"), 64)
    client = str(metadata.get("client") or "").strip().lower()
    return display or CLIENT_LABELS.get(client, "")


def channel_label(user_id: Any) -> str:
    value = str(user_id or "")
    return next((name for prefix, name in CHANNEL_LABELS if value.startswith(prefix)), "")


def conversation_origin(user_id: Any, metadata: Mapping[str, Any]) -> str:
    """"Kitchen iPhone · Local pair": which app, and how it reached this server."""
    label = client_label(metadata)
    channel = channel_label(user_id)
    if label and channel and channel.lower() not in label.lower():
        return f"{label} · {channel}"
    return label or channel


def conversation_kind(user_id: Any, metadata: Optional[Mapping[str, Any]] = None) -> str:
    meta = metadata if isinstance(metadata, Mapping) else {}
    transport = owner_transport(user_id)
    if transport == "admin-web":
        return KIND_SELF
    if transport == "peer":
        return KIND_PEER
    if transport == "room":
        return KIND_ROOM
    if transport == "guest":
        return KIND_GUEST
    if transport in _MESSAGING_TRANSPORTS:
        return KIND_MESSAGING
    if transport:
        return KIND_DEVICE
    # A raw id only reaches history through the server's own HTTP chat routes.
    # Whoever else wrote in it decides first: the owner adding a turn to the
    # connector's conversation does not make it the owner's.
    raw_id = str(user_id or "").strip()
    source = str(meta.get("source") or "").strip().lower()
    client = str(meta.get("client") or "").strip().lower()
    if raw_id in _CONNECTOR_USER_IDS or source in _CONNECTOR_SOURCES:
        return KIND_CONNECTOR
    if client in _MESSAGING_CLIENTS:
        return KIND_MESSAGING
    owner_only = source in {"", "admin_web"} and client in {"", "admin-web"}
    if raw_id == ADMIN_CHAT_USER_ID or (owner_only and (source == "admin_web" or normalize_admin_surface(meta.get("admin_surface")))):
        return KIND_SELF
    return KIND_EXTERNAL


def _via_label(relay: Mapping[str, Any], known_names: Mapping[str, str]) -> str:
    via_name = _text(relay.get("via_name"), PEER_NAME_MAX_LENGTH)
    if via_name:
        return via_name
    via_user_id = str(relay.get("via_user_id") or "")
    if known_names.get(via_user_id):
        return known_names[via_user_id]
    channel = channel_label(via_user_id)
    return f"a {channel} device" if channel else ("a paired device" if via_user_id else "")


def describe_conversation(
    user_id: Any,
    metadata: Optional[Mapping[str, Any]] = None,
    *,
    self_name: str = "",
    known_names: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """``{"kind", "name", "detail", "is_self"}`` for one conversation.

    ``metadata`` is the stored turn metadata (newest values win); ``self_name``
    is this server's name, which the admin page presents as the owner; and
    ``known_names`` maps other conversations' owner ids to their names so a
    relayed guest can say which paired device carried it.
    """
    meta = metadata if isinstance(metadata, Mapping) else {}
    names = known_names if isinstance(known_names, Mapping) else {}
    kind = conversation_kind(user_id, meta)
    if kind == KIND_SELF:
        detail = f"You · {admin_surface_label(meta.get('admin_surface'))}"
        return {"kind": kind, "name": _text(self_name, 120) or "You", "detail": detail, "is_self": True}
    if kind == KIND_PEER:
        relay = sanitize_peer_relay(meta.get("peer_relay"))
        platform = relay.get("platform", "")
        if relay.get("name"):
            name, parts = relay["name"], ["Peer Relay"]
        else:
            name, parts = "Peer Relay guest", [PLATFORM_LABELS.get(platform, platform)] if platform else []
        via = _via_label(relay, names)
        if via:
            parts.append(f"via {via}")
        return {"kind": kind, "name": name, "detail": " · ".join(parts), "is_self": False}
    if kind == KIND_ROOM:
        return {"kind": kind, "name": "Lobby", "detail": "Lobby bridge", "is_self": False}
    if kind == KIND_GUEST:
        return {"kind": kind, "name": "Unpaired session", "detail": "Guest", "is_self": False}
    if kind == KIND_CONNECTOR:
        name = client_label(meta) or "AI connector"
        return {"kind": kind, "name": name, "detail": "MCP connector", "is_self": False}
    label = client_label(meta)
    channel = channel_label(user_id)
    fallback = {KIND_MESSAGING: "Messaging", KIND_DEVICE: "Paired device"}.get(kind, "Chat API")
    name = label or channel or ("External app" if kind == KIND_EXTERNAL else fallback)
    detail = channel if channel and channel.lower() not in name.lower() else fallback
    return {"kind": kind, "name": name, "detail": detail, "is_self": False}
