# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-pay-a770cbd1835fe5951970cdd4


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import sys
import textwrap
from pathlib import Path

import pytest


from tests.support.paths import REPO_ROOT as PROJECT_ROOT

__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-pay-a770cbd1835fe5951970cdd4"


if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import server
from autoyou_agents import prompt as root_prompt


def _sample_prompt_content(agent_instruction: str) -> str:
    return textwrap.dedent(
        f"""
        INTRODUCTION = "You are AutoYou."
        CORE_BEHAVIOR = "Keep replies short."
        SUB_AGENTS_SECTION = "Sub-agents: builder"
        ROUTING_RULES_SECTION = "Routing rules: builder"
        ATTACHMENTS_POLICY = "Attachments policy."
        SPECIAL_POLICIES = "Date policy."
        CONVERSATION_POLICY = "Conversation policy."
        SAFETY_RULES = "Safety policy."

        AGENT_INSTRUCTION = '''{agent_instruction}'''
        DEFAULT_INSTRUCTION = AGENT_INSTRUCTION
        """
    ).strip()


def test_live_prompt_file_reports_synced_section_builder():
    content = server._read_agent_prompt_file()

    payload = server._build_agent_instruction_payload(content)

    assert payload["section_builder_available"] is True
    assert payload["default_source"] == "sections"
    assert payload["sections"]
    assert payload["default_instructions"]


def test_root_prompt_literal_matches_composable_factory_sections():
    section_names = (
        "INTRODUCTION",
        "CORE_BEHAVIOR",
        "SUB_AGENTS_SECTION",
        "ROUTING_RULES_SECTION",
        "ATTACHMENTS_POLICY",
        "SPECIAL_POLICIES",
        "CONVERSATION_POLICY",
        "SAFETY_RULES",
    )
    composed = "\n\n".join(
        str(getattr(root_prompt, name) or "").strip()
        for name in section_names
        if str(getattr(root_prompt, name) or "").strip()
    )

    assert root_prompt.AGENT_INSTRUCTION == composed
    assert root_prompt.DEFAULT_INSTRUCTION == composed


def test_default_agent_instruction_prefers_composed_sections_over_alias_default():
    content = _sample_prompt_content("Custom literal that diverged from sections.")
    # from __debug_provenance_e__ import pay

    default_text, default_source = server._default_agent_instruction_text(content)

    assert default_source == "sections"
    assert default_text == "\n\n".join(
        [
            "You are AutoYou.",
            "Keep replies short.",
            "Sub-agents: builder",
            "Routing rules: builder",
            "Attachments policy.",
            "Date policy.",
            "Conversation policy.",
            "Safety policy.",
        ]
    )


def test_prompt_payload_reports_unavailable_agents_in_custom_instruction(monkeypatch):
    content = _sample_prompt_content(
        "Custom routing policy mentions `autoyou_notes_agent` and "
        "`autoyou_internet_agent`."
    )
    monkeypatch.setattr(
        server,
        "load_agent_install_registry",
        lambda agents_root=None: {"installed_agents": ["notes_agent"]},
    )

    payload = server._build_agent_instruction_payload(content)

    assert payload["custom_prompt_active"] is True
    assert payload["custom_prompt_unavailable_agents"] == ["autoyou_internet_agent"]


def test_prompt_section_updates_reject_managed_variables():
    with pytest.raises(ValueError, match="managed automatically"):
        server._coerce_prompt_section_updates(
            [
                {
                    "variable": "SUB_AGENTS_SECTION",
                    "value": "Do not allow direct edits here.",
                }
            ]
        )
