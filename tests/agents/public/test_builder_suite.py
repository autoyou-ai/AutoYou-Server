# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

from pathlib import Path

from tests.support.paths import ensure_repo_on_path


ensure_repo_on_path()

from autoyou_agents.shared_tools.builder_suite import (  # noqa: E402
    BUILDER_SUITE_AGENT_NAMES,
    DEFAULT_BUILDER_SUITE_OLLAMA_MODEL,
    builder_suite_status,
    install_builder_suite_agents,
    resolve_builder_suite_ollama_model,
)
from autoyou_agents.shared_tools.agent_install_registry import load_agent_install_registry  # noqa: E402


def _write_agent_dir(root: Path, name: str) -> None:
    agent_dir = root / name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "agent.py").write_text(
        f"def create_{name}(*args, **kwargs):\n    return None\n",
        encoding="utf-8",
    )


def test_resolve_builder_suite_model_prefers_installed_qwen_27b():
    assert (
        resolve_builder_suite_ollama_model(
            [
                {"name": "ministral-3:8b"},
                {"name": "qwen3.6:latest"},
                {"name": "qwen3.8:27b"},
            ]
        )
        == "qwen3.8:27b"
    )


def test_install_builder_suite_installs_conservative_workflow_agents(tmp_path):
    agents_root = tmp_path / "autoyou_agents"
    registry_path = tmp_path / "agent_install_registry.json"
    for agent_name in BUILDER_SUITE_AGENT_NAMES:
        _write_agent_dir(agents_root, agent_name)
    _write_agent_dir(agents_root, "files_agent")

    result = install_builder_suite_agents(
        agents_root=agents_root,
        registry_path=registry_path,
        source="test-suite",
    )

    assert result["status"] == "success"
    assert result["failed"] == []
    assert sorted(result["installed"]) == sorted(BUILDER_SUITE_AGENT_NAMES)
    assert result["requires_restart"] is True
    assert result["recommended_ollama_model"] == DEFAULT_BUILDER_SUITE_OLLAMA_MODEL

    registry = load_agent_install_registry(
        agents_root=agents_root,
        registry_path=registry_path,
    )
    assert set(BUILDER_SUITE_AGENT_NAMES).issubset(set(registry["installed_agents"]))
    assert "files_agent" not in registry["installed_agents"]

    status = builder_suite_status(
        agents_root=agents_root,
        registry_path=registry_path,
        selected_model="ministral-3:8b",
    )
    assert status["installed"] is True
    assert status["selected_model_matches_recommendation"] is False
