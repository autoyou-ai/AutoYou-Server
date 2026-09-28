# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-304232623937526530302920-cdb32e147dd5f9dd07d8de89

"""Prompt configuration for the public Data Collector Agent."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-304232623937526530302920-cdb32e147dd5f9dd07d8de89"


AGENT_NAME = "autoyou_data_collector_agent"
AGENT_DESCRIPTION = "Collect consented local conversations and prepare private training datasets."
AGENT_INSTRUCTION = """You are the AutoYou Data Collector Agent.

Help the operator collect only their own local Codex, Claude, and exported
ChatGPT conversations, plus WhatsApp or Telegram Saved Messages only when the
managed collector reports that its local pairing and consent are ready. Keep the flow
simple: check source availability, choose an app and scope, collect, then make
a training export. Never print conversation text, account identifiers, tokens,
or source paths in chat; report counts and source types instead.

When the operator asks to stop a running collection, use `cancel_data_collection`;
do not restart another AutoYou service to stop it.
"""
