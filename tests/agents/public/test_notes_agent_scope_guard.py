# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-643937636335396144393239-b172dceba118daf14dc345c0


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-643937636335396144393239-b172dceba118daf14dc345c0"

import asyncio
from types import SimpleNamespace

import pytest
from google.genai import types

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import autoyou_agents.notes_agent.agent as notes_agent_module


def _llm_request(text: str):
    return SimpleNamespace(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part(text=text)],
            )
        ]
    )


def test_notes_agent_scope_guard_redirects_local_file_operations():
    response = asyncio.run(
        notes_agent_module._notes_before_model_callback(
            SimpleNamespace(state={}, invocation_id="notes-file-scope"),
            _llm_request("Rename my local music file to weekend-final.mp3"),
        )
    )

    assert response is not None
    assert "autoyou_files_agent" in response.content.parts[0].text


@pytest.mark.parametrize(
    "request_text",
    [
        "Add to notes, synthetic title. Content asis: Synthetic body.",
        "Create a note titled synthetic title. Content as is: Synthetic body.",
        'Title "synthetic title", content: "Synthetic body."',
        "Create a note",
    ],
)
def test_notes_agent_leaves_mutations_to_the_model(request_text):
    """The June callback shape must not parse, mutate, or clarify writes."""
    response = asyncio.run(
        notes_agent_module._notes_before_model_callback(
            SimpleNamespace(state={}, invocation_id="notes-model-owned-mutation"),
            _llm_request(request_text),
        )
    )

    assert response is None


def test_notes_agent_uses_only_the_june_callbacks(monkeypatch):
    monkeypatch.setattr(notes_agent_module, "Agent", lambda **kwargs: kwargs)

    config = notes_agent_module.create_notes_agent("synthetic-model")

    assert config["before_model_callback"] == [notes_agent_module._notes_before_model_callback]
    assert "after_tool_callback" not in config
    assert "after_model_callback" not in config
