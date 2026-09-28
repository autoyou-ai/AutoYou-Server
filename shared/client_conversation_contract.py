# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote


def conversation_thread_id_for_metadata(thread_id: Any) -> int:
    try:
        normalized_thread_id = int(thread_id or 1)
    except Exception:
        normalized_thread_id = 1
    if normalized_thread_id <= 0:
        normalized_thread_id = 1
    return normalized_thread_id


def encode_conversation_identity_component(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return "_"
    return quote(normalized, safe="")


def get_client_destination_session_id(identity: Any) -> str:
    transport = str(getattr(identity, "transport", "") or "").strip().lower()
    sender_id = str(getattr(identity, "sender_id", "") or "").strip()
    raw_session_id = str(getattr(identity, "raw_session_id", "") or "").strip()
    owner_key = str(getattr(identity, "owner_key", "") or "").strip()

    if transport == "cloud" and sender_id:
        return sender_id
    return raw_session_id or sender_id or owner_key


def infer_pairing_mode(identity: Any) -> str:
    explicit = str(getattr(identity, "pairing_mode", "") or "").strip()
    if explicit:
        return explicit
    transport = str(getattr(identity, "transport", "") or "").strip().lower()
    if transport in {"cloud"}:
        return "cloud_pair"
    if transport in {"bluetooth", "bluetooth-local"}:
        return "bluetooth_pair"
    if transport in {"local", "windows-local", "admin-web", "webrtc", "webrtc-datachannel", "datachannel"}:
        return "local_pair"
    return ""


def build_client_conversation_session_id(
    identity: Any,
    *,
    server_identity_key: str,
) -> str:
    canonical_session_id = str(getattr(identity, "canonical_session_id", "") or "").strip()
    owner_key = str(getattr(identity, "owner_key", "") or "").strip()
    if not owner_key:
        return canonical_session_id

    destination_session_id = get_client_destination_session_id(identity)
    parts = [
        "session",
        "dest",
        "server",
        encode_conversation_identity_component(server_identity_key),
        "owner",
        encode_conversation_identity_component(owner_key),
        "target",
        encode_conversation_identity_component(destination_session_id),
    ]
    normalized_thread_id = conversation_thread_id_for_metadata(
        getattr(identity, "thread_id", None)
    )
    if normalized_thread_id > 1:
        parts.extend(["thread", str(normalized_thread_id)])
    return "::".join(parts)


def build_client_session_identity_payload(
    identity: Any,
    *,
    server_id: str,
    server_identity_key: str,
    pairing_mode: Optional[str] = None,
) -> Dict[str, Any]:
    raw_session_id = str(getattr(identity, "raw_session_id", "") or "").strip()
    destination_session_id = get_client_destination_session_id(identity)
    payload = {
        "owner_key": str(getattr(identity, "owner_key", "") or "").strip(),
        "canonical_user_id": str(getattr(identity, "canonical_user_id", "") or "").strip(),
        "canonical_session_id": str(getattr(identity, "canonical_session_id", "") or "").strip(),
        "conversation_session_id": build_client_conversation_session_id(
            identity,
            server_identity_key=server_identity_key,
        ),
        "destination_session_id": destination_session_id,
        "raw_session_id": raw_session_id,
        "server_id": str(server_id or "").strip(),
        "server_identity_key": str(server_identity_key or "").strip(),
    }
    resolved_pairing_mode = str(pairing_mode or "").strip() or infer_pairing_mode(identity)
    if resolved_pairing_mode:
        payload["pairing_mode"] = resolved_pairing_mode
    return {
        key: value
        for key, value in payload.items()
        if value not in ("", None)
    }


def build_conversation_metadata(
    identity: Any,
    *,
    server_identity_key: str,
    server_id: str = "",
    pairing_mode: Optional[str] = None,
    reset: bool = False,
) -> Dict[str, Any]:
    payload = {
        "conversation_session_id": build_client_conversation_session_id(
            identity,
            server_identity_key=server_identity_key,
        ),
        "conversation_thread_id": conversation_thread_id_for_metadata(
            getattr(identity, "thread_id", None)
        ),
        "conversation_reset": bool(reset),
        "canonical_owner_key": str(getattr(identity, "owner_key", "") or "").strip(),
        "canonical_user_id": str(getattr(identity, "canonical_user_id", "") or "").strip(),
        "canonical_session_id": str(getattr(identity, "canonical_session_id", "") or "").strip(),
        "destination_session_id": get_client_destination_session_id(identity),
        "raw_session_id": str(getattr(identity, "raw_session_id", "") or "").strip(),
        "server_id": str(server_id or "").strip(),
        "server_identity_key": str(server_identity_key or "").strip(),
    }
    resolved_pairing_mode = str(pairing_mode or "").strip() or infer_pairing_mode(identity)
    if resolved_pairing_mode:
        payload["pairing_mode"] = resolved_pairing_mode
    return {
        key: value
        for key, value in payload.items()
        if value not in ("", None)
    }


def build_autoyou_conversation_metadata(
    identity: Any,
    *,
    reset: bool = False,
) -> Dict[str, Any]:
    try:
        import server

        get_server_id = getattr(server, "_get_stable_server_id", None)
        get_server_key = getattr(server, "_get_server_identity_key", None)
        server_id = str(get_server_id() if callable(get_server_id) else "").strip()
        server_identity_key = str(get_server_key() if callable(get_server_key) else "").strip()
    except Exception:
        server_id = "AutoYou-Server"
        server_identity_key = "name:AutoYou-Server"

    return build_conversation_metadata(
        identity,
        server_id=server_id or "AutoYou-Server",
        server_identity_key=server_identity_key or "name:AutoYou-Server",
        reset=reset,
    )
