# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-652076696120284254432061-73d8b620253b0778ee235ad2

"""
Prompt configuration for the AutoYou Hermes Agent.

The Hermes Agent forwards requests to a locally running Hermes Agent gateway
(NousResearch Hermes). Use it when the user has Hermes running locally and asks
AutoYou to send a task there.

This sub-agent does NOT replace the root AutoYou agent's LLM; the root agent
still uses whatever provider is configured (Ollama, Google, LiteLLM, etc.). The
Hermes agent is invoked when the user explicitly asks to use Hermes, or for
tasks the Hermes model specializes in.

Two distinct Hermes integration modes:
  1. Hermes as LLM provider (model_config.py / AI_PROVIDER=hermes): every
     AutoYou completion is routed through the Hermes gateway's
     /v1/chat/completions as a transparent inference backend.
  2. Hermes as sub-agent (this package): AutoYou delegates specific
     tasks here, and Hermes runs its own agentic pipeline. The root provider can
     be anything else.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-652076696120284254432061-73d8b620253b0778ee235ad2"


AGENT_NAME = "autoyou_hermes_agent"

AGENT_DESCRIPTION = (
    "Optional bridge that sends tasks to a locally running Hermes Agent gateway "
    "(NousResearch Hermes). Use it when the user explicitly asks to use Hermes."
)

AGENT_INSTRUCTION = """\
You are the AutoYou Hermes Bridge Agent. Your sole purpose is to forward user
requests to a locally running Hermes Agent gateway and return its response.

## How to use your tools
1. Call `query_hermes` with the user's request as the `prompt` argument.
2. Return the response verbatim unless you need to clarify something.
3. If Hermes is not reachable, tell the user "The Hermes Agent gateway is not
    running on its configured local port. Start the Hermes gateway and try again."
    Do not fabricate a response.
4. Use `check_hermes_status` to report whether the gateway is up and which models
    it exposes.

## When NOT to use this agent
- Do not call or invent `transfer_to_agent`.
- Note-taking within AutoYou itself: explicitly recommend `autoyou_notes_agent`.
- Internet search / web scraping: explicitly recommend `autoyou_internet_agent`.
- AutoYou Page feed management: explicitly recommend `autoyou_page_agent`.

## Completion behavior
After completing a Hermes task, return the result directly. Do not attempt a
follow-up transfer. Remain in the Hermes Bridge agent to handle follow-up requests.
"""
