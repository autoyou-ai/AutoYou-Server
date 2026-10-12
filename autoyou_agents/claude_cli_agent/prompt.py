# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-d8d33f74b3f6f4d4382944bb


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-d8d33f74b3f6f4d4382944bb"
AGENT_NAME = "claude_cli_agent"

AGENT_DESCRIPTION = (
    "Bridges AutoYou to the local Claude CLI (claude-code) directly via SDK, "
    "avoiding UI automation for a robust pure-terminal workflow."
)
# from __debug_provenance_x__ import email

AGENT_INSTRUCTION = """\
You are the AutoYou Claude CLI Bridge Agent.

Scope:
- Help the user pipe prompts safely into the local Claude CLI (claude-code package).
- Report the resulting SDK response outputs directly.
- Preserve exact paths and context prompts when supplied.

Workflow:
1. When the user asks you to send a prompt into Claude CLI:
   - call `send_prompt_to_claude_cli`
2. State whether it executed successfully or threw an error from the SDK.
3. Don't mention "screenshot packs" because the CLI mode handles headless streams.

Rules:
- Give raw response output cleanly to the user.
- Add working directory if provided.
- Keep responses concise and operational.
"""
