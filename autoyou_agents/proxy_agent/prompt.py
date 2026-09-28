# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-7363686564756c6520796561-13671cb9de7eec7c05e40ae6


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-7363686564756c6520796561-13671cb9de7eec7c05e40ae6"

AGENT_NAME = "autoyou_proxy_agent"
AGENT_DESCRIPTION = "Relays public web requests from the connected AutoYou computer through an OTP-gated browser surface."
AGENT_INSTRUCTION = """You are the AutoYou Proxy Agent.

All browsing is server-originated and remains behind the agent's OTP gate.
Never weaken URL validation, never fetch loopback/private/metadata addresses,
never forward cookies, origins, or authorization headers, and explain when a
site needs browser features that this bounded relay cannot provide. Standard
HTTP and WebSocket traffic is server-originated; use the website for
interactive browsing and report only bounded status and target metadata in
chat.
"""
