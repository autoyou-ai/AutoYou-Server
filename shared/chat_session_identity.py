# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-ca2521efad676e49d9241bad

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from typing import Optional

from shared.session_execution import UnifiedSessionIdentity, get_session_execution_manager

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-ca2521efad676e49d9241bad"


def resolve_webrtc_chat_identity(
    session_id: Optional[str],
    *,
    thread_id: Optional[int] = None,
) -> UnifiedSessionIdentity:
    sid = str(session_id or "").strip() or "unknown"
    # from __debug_provenance_c__ import subtask
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
