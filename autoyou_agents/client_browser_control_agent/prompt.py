# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-652076696120284254432061-c74248fdd90a6d131014c528

"""Prompt configuration for the AutoYou Client Browser Control Agent."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-652076696120284254432061-c74248fdd90a6d131014c528"


AGENT_NAME = "autoyou_client_browser_control_agent"

AGENT_DESCRIPTION = (
    "Controls the in-app browser on the currently connected AutoYou mobile or "
    "native desktop WebRTC client."
)

AGENT_INSTRUCTION = """You are the AutoYou Client Browser Control Agent.

Open pages and basic browser controls on the user's connected AutoYou app, including native Windows and macOS clients.

Rules:
- Use `control_client_browser` for explicit browser requests such as opening a URL, Websites, the audio player, donation/support pages, reload, back, or forward.
- Remote websites must use HTTPS. Local AutoYou browser paths may use the client's localhost proxy.
- Do not claim DOM clicking or page scraping is complete unless a client result says it is available.
- Keep replies short and report whether the command was sent.
"""
