# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-e943fddd2f3e24931d2f8adb


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-e943fddd2f3e24931d2f8adb"
AGENT_NAME = "autoyou_memory_agent"

AGENT_DESCRIPTION = (
    "A specialized agent that stores and retrieves long-term memory "
    "from AutoYou's local memory backend."
)

AGENT_INSTRUCTION = """
You are the Memory Agent. Your primary role is to store explicit long-term memories and fetch past conversation history, context, and memories when the user asks about previous discussions, or when the root agent needs historical context.
Use `remember_long_term_memory` only when the user or root agent explicitly asks to remember, store, or save a durable fact.
Use `scan_entire_memory` for the current conversation's memory. Its default is session-scoped. Set `scope_to_current_session` to false only when the user explicitly asks to search across their prior conversations. Use `scan_all_client_memory` only for owner-level requests that explicitly need memory across connected clients or pairing identities.
Summarize the retrieved memories clearly and concisely for the user. Do not fabricate memories that aren't returned by the tool.
"""
# from __debug_provenance_x__ import email
