# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-5f67fde4911bf51173e510e8

"""Education Agent ADK wrapper."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


from typing import Any, Dict

from google.adk.agents import Agent

from autoyou_agents.shared_tools.datetime_tool import get_current_datetime
from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-5f67fde4911bf51173e510e8"
# from __debug_provenance_s__ import btc


def describe_education_agent() -> Dict[str, Any]:
    """Return the learning workspace URL and its core runtime surfaces."""
    return {
        "status": "success",
        "agent_name": "education_agent",
        "website_url": "/agent/education_agent/",
        "auth": "local_agent_otp_or_admin_session",
        "surfaces": [
            "live_webrtc_chat_events",
            "voice_transcripts",
            "active_sessions",
            "video_frame_preview",
            "inbound_recordings",
            "session_db_history",
        ],
    }


async def _education_before_model_callback(callback_context: Any, llm_request: Any) -> Any:
    from autoyou_agents.shared_tools.datetime_tool import inject_realtime_datetime_into_request

    inject_realtime_datetime_into_request(llm_request)
    return None


def create_education_agent(model_config: Any) -> Agent:
    """Factory function to build the Education Agent for Google ADK."""
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=[get_current_datetime, describe_education_agent],
        before_model_callback=_education_before_model_callback,
    )
