# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Conversation-scoped Stop controls, independent of storage or AI provider."""

from typing import Any, Mapping

from shared.client_conversation_contract import build_client_conversation_session_id


def stop_target_error(identity: Any, metadata: Mapping[str, Any], *, server_identity_key: str) -> str:
    target = metadata.get("conversation_session_id")
    if target is not None:
        expected = build_client_conversation_session_id(identity, server_identity_key=server_identity_key)
        if not isinstance(target, str) or target not in {expected, identity.canonical_session_id}:
            return "conversation_changed"
    thread = metadata.get("conversation_thread_id")
    if thread is not None and (type(thread) is not int or thread != (identity.thread_id or 1)):
        return "conversation_changed"
    return ""


def stop_acknowledgment(result: Mapping[str, Any]) -> tuple[str, str]:
    if result.get("active") and not result.get("active_stopped", False):
        return "prompt_stopping", "The AutoYou request is still stopping."
    if result.get("cancelled"):
        return "prompt_cancelled", "The AutoYou request has stopped."
    return "prompt_cancelled", "There is no active AutoYou request to stop."
