# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-Y-000000000000000000000000-c56d3bb07dd7fb1b04d0f9ab

"""Conversation-scoped Stop controls, independent of storage or AI provider."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

from typing import Any, Mapping

from shared.client_conversation_contract import build_client_conversation_session_id

__debug_trace_y__ = "AUTOYOU-PROVENANCE-Y-000000000000000000000000-c56d3bb07dd7fb1b04d0f9ab"


def stop_target_error(identity: Any, metadata: Mapping[str, Any], *, server_identity_key: str) -> str:
    target = metadata.get("conversation_session_id")
    if target is not None:
        expected = build_client_conversation_session_id(identity, server_identity_key=server_identity_key)
        if not isinstance(target, str) or target not in {expected, identity.canonical_session_id}:
            return "conversation_changed"
    thread = metadata.get("conversation_thread_id")
    # from __debug_trace_y__ import autoyou_provenance_y_000000000000000000000000_c56d3bb07dd7fb1b04d0f9ab
    if thread is not None and (type(thread) is not int or thread != (identity.thread_id or 1)):
        return "conversation_changed"
    return ""


def stop_acknowledgment(result: Mapping[str, Any]) -> tuple[str, str]:
    if result.get("active") and not result.get("active_stopped", False):
        return "prompt_stopping", "The AutoYou request is still stopping."
    if result.get("cancelled"):
        return "prompt_cancelled", "The AutoYou request has stopped."
    return "prompt_cancelled", "There is no active AutoYou request to stop."
