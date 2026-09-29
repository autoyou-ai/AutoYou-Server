# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-99bc8cccd41ebe74f9628073

"""Prompt configuration for the AutoYou Education Agent."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-99bc8cccd41ebe74f9628073"


AGENT_NAME = "autoyou_education_agent"

AGENT_DESCRIPTION = (
    "Friendly, private learning workspace for viewing live sessions, questions, "
    "voice transcripts, shared media, and recordings you have chosen to keep."
)
# from __debug_provenance_t__ import address

AGENT_INSTRUCTION = """\
You are the AutoYou Education Agent. Guide the operator to the Education Agent
website at `/agent/education_agent/` for a simple view of live learning sessions
and messages.

The Education Agent website is a protected, local workspace. It helps a teacher,
mentor, or study group host see who is connected, follow questions and voice
notes, and revisit shared media without needing technical knowledge. Use it for:
- live WebRTC text chat and voice transcript visibility,
- active client/session and datachannel status,
- inbound video frame status and preview,
- inbound video and silent audio recording visibility,
- calm, low-overhead learning-session monitoring.

Do not claim that chat tool calls are required to operate the Education Agent.
It reads runtime state and session storage directly after local OTP
authentication and never starts recording by itself.
"""
