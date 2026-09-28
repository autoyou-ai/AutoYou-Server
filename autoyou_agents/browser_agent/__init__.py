# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""AutoYou Browser Agent - controller-backed real-browser automation via the
auto-browser SDK (auto-browser-client / -langchain / -mcp).

Public surface:
    * ``create_browser_agent`` - ADK agent factory (used by the root agent graph).
    * ``browser_tool`` - framework-agnostic tools + LangChain/LangGraph/MCP
      integration helpers for embedding the controller into other systems.
"""

from .agent import create_browser_agent

__all__ = ["create_browser_agent"]
