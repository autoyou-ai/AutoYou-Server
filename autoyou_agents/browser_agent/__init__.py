# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-7363686564756c6520796561-8df516cfcfe05a267ad84807

"""AutoYou Browser Agent - controller-backed real-browser automation via the
auto-browser SDK (auto-browser-client / -langchain / -mcp).

Public surface:
    * ``create_browser_agent`` - ADK agent factory (used by the root agent graph).
    * ``browser_tool`` - framework-agnostic tools + LangChain/LangGraph/MCP
      integration helpers for embedding the controller into other systems.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-7363686564756c6520796561-8df516cfcfe05a267ad84807"


from .agent import create_browser_agent

__all__ = ["create_browser_agent"]
