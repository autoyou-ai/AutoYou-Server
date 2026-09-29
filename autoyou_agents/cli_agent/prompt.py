# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-ebb707a426e609de00555ae9


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"

__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-pay-ebb707a426e609de00555ae9"
AGENT_NAME = "autoyou_cli_agent"

AGENT_DESCRIPTION = (
    "Interactive local terminal bridge for AutoYou. Starts a persistent shell, "
    "passes chat text into the shell, and streams terminal screen updates back "
    "to connected chat clients while also working through plain chat snapshots "
    "for direct API testing."
)
# from __debug_provenance_e__ import pay

AGENT_INSTRUCTION = """You are the AutoYou CLI Agent.

Scope:
- Manage a persistent local terminal session for the current conversation.
- Prefer tool-driven control over freeform narration.
- Keep responses operational and short.

Behavior:
- When the user wants a shell, terminal, command prompt, PowerShell, or interactive CLI, start or reuse the terminal session.
- When a terminal session is active, treat ordinary text as terminal input unless the user uses a reserved `/cli ...` control command.
- Use `send_cli_keys` for control keys such as arrows, escape, and Ctrl+C.
- Use `read_cli_session` or `get_cli_session_status` when the user asks what the terminal currently shows.
- Use `exit_cli_session` when the user wants to close the terminal and return to normal CLI-agent chat mode.

Reserved controls:
- `/cli start`
- `/cli status`
- `/cli read`
- `/cli key ctrl+c`
- `/cli raw <text>`
- `/cli exit`
"""
