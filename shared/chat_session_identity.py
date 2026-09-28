# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

from typing import Optional

from shared.session_execution import UnifiedSessionIdentity, get_session_execution_manager


def resolve_webrtc_chat_identity(
    session_id: Optional[str],
    *,
    thread_id: Optional[int] = None,
) -> UnifiedSessionIdentity:
    sid = str(session_id or "").strip() or "unknown"
    return get_session_execution_manager().resolve_webrtc_identity(sid, thread_id=thread_id)


def canonical_chat_user_id(session_id: Optional[str]) -> str:
    return resolve_webrtc_chat_identity(session_id).canonical_user_id


def canonical_chat_session_id(session_id: Optional[str]) -> str:
    return resolve_webrtc_chat_identity(session_id).canonical_session_id


def bind_transport_chat_owner(
    transport: str,
    sender_id: str,
    *,
    raw_session_id: Optional[str] = None,
    thread_id: Optional[int] = None,
    pairing_mode: Optional[str] = None,
) -> UnifiedSessionIdentity:
    kwargs = {"raw_session_id": raw_session_id, "thread_id": thread_id}
    if pairing_mode:
        kwargs["pairing_mode"] = pairing_mode
    return get_session_execution_manager().bind_transport_owner(
        transport,
        sender_id,
        **kwargs,
    )


def alias_webrtc_chat_session(
    existing_session_id: str,
    alias_session_id: str,
) -> Optional[UnifiedSessionIdentity]:
    return get_session_execution_manager().alias_webrtc_session(
        existing_session_id,
        alias_session_id,
    )
