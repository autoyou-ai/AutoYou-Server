# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Prompt <-> tool sync guards for the builder/website sub-agents.

These agents enumerate their tools by name inside AGENT_INSTRUCTION. If the
factory tool list and the prompt drift apart the model will hallucinate or
fail tool calls. This test keeps them in lockstep without importing the heavy
google.adk runtime (pure AST + prompt module load).
"""

import ast
import importlib.util
import re
from pathlib import Path

import pytest

from tests.support.paths import REPO_ROOT as ROOT

AGENTS = [
    "autoyou_agents/agent_builder_agent",
    "autoyou_agents/website_agent",
]


def _load_prompt(path: Path):
    spec = importlib.util.spec_from_file_location(f"{path.parent.name}_prompt", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _factory_tool_names(agent_py: Path) -> list[str]:
    """Return the Name identifiers from the `tools = [...]` list in agent.py."""
    tree = ast.parse(agent_py.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "tools" and isinstance(node.value, ast.List):
                return [elt.id for elt in node.value.elts if isinstance(elt, ast.Name)]
    raise AssertionError(f"No `tools = [...]` list found in {agent_py}")


@pytest.mark.parametrize("agent_dir", AGENTS)
def test_prompt_documents_every_factory_tool(agent_dir: str):
    base = ROOT / agent_dir
    prompt = _load_prompt(base / "prompt.py")
    instruction = getattr(prompt, "AGENT_INSTRUCTION", "")
    assert instruction, f"{agent_dir} prompt.py missing AGENT_INSTRUCTION"

    tool_names = _factory_tool_names(base / "agent.py")
    assert tool_names, f"{agent_dir} has no factory tools"

    missing = [
        name
        for name in tool_names
        if not re.search(r"\b" + re.escape(name) + r"\b", instruction)
    ]
    assert not missing, (
        f"{agent_dir} prompt.py does not document factory tools: {missing}. "
        "Prompt and tool list have drifted -> hallucination risk."
    )


@pytest.mark.parametrize("agent_dir", AGENTS)
def test_prompt_has_no_adk_placeholder_and_required_exports(agent_dir: str):
    base = ROOT / agent_dir
    prompt = _load_prompt(base / "prompt.py")
    for attr in ("AGENT_NAME", "AGENT_DESCRIPTION", "AGENT_INSTRUCTION"):
        assert getattr(prompt, attr, None), f"{agent_dir} prompt.py missing {attr}"
    instruction = prompt.AGENT_INSTRUCTION
    assert "{" not in instruction and "}" not in instruction, (
        f"{agent_dir} AGENT_INSTRUCTION contains a curly brace; ADK will treat "
        "it as a session-state template and raise KeyError at runtime."
    )


@pytest.mark.parametrize("agent_dir", AGENTS)
def test_prompt_is_compiled_mode_aware(agent_dir: str):
    """Both agents must tell the model how packaged/compiled builds differ so
    they stop claiming scaffolds are 'live' in a binary."""
    prompt = _load_prompt(ROOT / agent_dir / "prompt.py")
    instruction = prompt.AGENT_INSTRUCTION.lower()
    assert "packaged" in instruction or "compiled" in instruction, (
        f"{agent_dir} prompt.py must be compiled/packaged-mode aware"
    )
    assert "get_scaffold_status" in prompt.AGENT_INSTRUCTION, (
        f"{agent_dir} prompt.py must instruct using get_scaffold_status to "
        "detect runtime_blocked before claiming an agent is live"
    )
