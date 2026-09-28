# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import os
import sys

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from autoyou_agents.admin_agent.prompt import AGENT_NAME as ADMIN_AGENT_NAME
from autoyou_agents.audio_agent.prompt import AGENT_NAME as AUDIO_AGENT_NAME
from autoyou_agents.internet_agent.prompt import AGENT_NAME as INTERNET_AGENT_NAME
from autoyou_agents.notes_agent.prompt import AGENT_NAME as NOTES_AGENT_NAME
from autoyou_agents.page_agent.prompt import AGENT_NAME as PAGE_AGENT_NAME
from autoyou_agents.prompt import (
    AGENT_INSTRUCTION as ROOT_AGENT_INSTRUCTION,
    ATTACHMENTS_POLICY as ROOT_ATTACHMENTS_POLICY,
)


def test_root_prompt_uses_only_active_root_subagent_names():
    for agent_name in (
        NOTES_AGENT_NAME,
        INTERNET_AGENT_NAME,
        PAGE_AGENT_NAME,
        ADMIN_AGENT_NAME,
        AUDIO_AGENT_NAME,
        "autoyou_files_agent",
        "autoyou_backup_agent",
        "autoyou_memory_agent",
        "autoyou_agent_builder_agent",
        "autoyou_cloudflare_agent",
        "autoyou_ionos_agent",
        "autoyou_ionos_cloudflare_agent",
        "autoyou_mail_agent",
        "autoyou_notify_agent",
        "autoyou_tasks_agent",
        "autoyou_website_agent",
        "autoyou_coding_agent",
        "autoyou_openclaw_agent",
        "autoyou_skills_agent",
        "autoyou_remote_desktop_agent",
        "autoyou_build_prompt_agent",
        "claude_desktop_agent",
        "codex_desktop_agent",
        # Ship enabled by DEFAULT_AGENT_INSTALL_STATES, so the root prompt has to
        # be able to route to them. education_agent sat in the inactive list
        # below as a leftover from when it was streaming_agent (404ec770 renamed
        # it); it was the only "inactive" entry that actually installs by
        # default, which left a shipped agent unreachable from chat.
        "autoyou_education_agent",
        "autoyou_hosting_agent",
        "autoyou_voice_training_agent",
        "autoyou_ads_watching_agent",
        "autoyou_persona_agent",
    ):
        assert f"`{agent_name}`" in ROOT_AGENT_INSTRUCTION

    for inactive_agent_name in (
        "claude_cli_agent",
    ):
        assert f"`{inactive_agent_name}`" not in ROOT_AGENT_INSTRUCTION

    for legacy_alias in (
        "`notes_agent`",
        "`internet_agent`",
        "`admin_agent`",
        "`audio_agent`",
        "`files_agent`",
        "`memory_agent`",
        "`agent_builder_agent`",
        "`coding_agent`",
        "`notify_agent`",
        "`openclaw_agent`",
        "`remote_desktop_agent`",
        "`education_agent`",
        "`skills_agent`",
        "`tasks_agent`",
        "`website_agent`",
    ):
        assert legacy_alias not in ROOT_AGENT_INSTRUCTION
        assert legacy_alias not in ROOT_ATTACHMENTS_POLICY

    assert "`reminder_agent`" not in ROOT_AGENT_INSTRUCTION
    assert "`reminder_agent`" not in ROOT_ATTACHMENTS_POLICY


def test_root_attachment_policy_defaults_media_to_notes_and_search_to_internet():
    assert "`autoyou_notes_agent`" in ROOT_ATTACHMENTS_POLICY
    assert "`autoyou_internet_agent`" in ROOT_ATTACHMENTS_POLICY
    assert "`autoyou_page_agent`" in ROOT_ATTACHMENTS_POLICY
    assert "`autoyou_audio_agent`" in ROOT_AGENT_INSTRUCTION
