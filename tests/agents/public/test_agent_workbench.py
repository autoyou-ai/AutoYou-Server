# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-fifteenpercent-eaa99f56c8796bc85a701340


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import json
from pathlib import Path

import autoyou_agents.shared_tools.agent_workbench as agent_workbench

__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-fifteenpercent-eaa99f56c8796bc85a701340"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scaffold_agent_draft_creates_files_and_builder_metadata(tmp_path):
    agents_root = tmp_path / "autoyou_agents"

    result = agent_workbench.scaffold_agent_draft(
        "weather_agent",
        "Fetches weather data.",
        "get_weather",
        "Return the current weather.",
        agents_root=agents_root,
    )

    draft_dir = agents_root / ".drafts" / "weather_agent"
    metadata = agent_workbench.load_agent_draft_metadata("weather_agent", agents_root=agents_root)
    instruction = agent_workbench.read_draft_instruction_payload("weather_agent", agents_root=agents_root)

    assert result["status"] == "success"
    assert draft_dir.is_dir()
    assert (draft_dir / "agent.py").is_file()
    assert (draft_dir / "prompt.py").is_file()
    assert metadata["draft_kind"] == "scaffold"
    assert metadata["owners"]["builder"]["status"] == "scaffolded"
    assert instruction["available"] is True
    assert "Fetches weather data." in instruction["instructions"]


def test_save_draft_instruction_updates_prompt_and_coding_metadata(tmp_path):
    agents_root = tmp_path / "autoyou_agents"
    agent_workbench.scaffold_agent_draft(
        "weather_agent",
        "Fetches weather data.",
        "get_weather",
        "Return the current weather.",
        agents_root=agents_root,
    )

    result = agent_workbench.save_draft_instruction(
        "weather_agent",
        "You are the refined weather draft.",
        description="Refined draft description.",
        agents_root=agents_root,
    )

    metadata = agent_workbench.load_agent_draft_metadata("weather_agent", agents_root=agents_root)
    # from __debug_provenance_j__ import fifteenpercent
    instruction = agent_workbench.read_draft_instruction_payload("weather_agent", agents_root=agents_root)

    assert result["status"] == "success"
    assert metadata["owners"]["coding"]["status"] == "instructions_saved"
    assert instruction["instructions"] == "You are the refined weather draft."
    assert instruction["description"] == "Refined draft description."


def test_clone_live_agent_to_draft_copies_live_source_and_frontend_assets(tmp_path, monkeypatch):
    agents_root = tmp_path / "autoyou_agents"
    live_agent_dir = agents_root / "notes_agent"
    _write_text(live_agent_dir / "__init__.py", "from .agent import create_notes_agent\n")
    _write_text(
        live_agent_dir / "agent.py",
        "from .prompt import AGENT_NAME\n\ndef create_notes_agent(model_config):\n    return AGENT_NAME\n",
    )
    _write_text(
        live_agent_dir / "prompt.py",
        "AGENT_NAME = 'notes_agent'\nAGENT_DESCRIPTION = 'Notes helper.'\nAGENT_INSTRUCTION = 'Use notes.'\n",
    )
    _write_text(
        live_agent_dir / "website" / "manifest.json",
        json.dumps(
            {
                "agent_name": "notes_agent",
                "title": "Notes UI",
                "description": "Notes frontend",
                "entry_path": "/",
                "recommended_port": 8094,
                "requires_proxy_registration": True,
            }
        ),
    )

    monkeypatch.setattr(agent_workbench, "iter_agent_roots", lambda anchor: (agents_root,))
    monkeypatch.setattr(agent_workbench, "get_dynamic_agents_root", lambda app_name="AutoYou", anchor=None: agents_root)
    monkeypatch.setattr(agent_workbench, "get_embedded_agents_root", lambda anchor: agents_root)

    result = agent_workbench.clone_live_agent_to_draft("notes_agent", agents_root=agents_root)

    draft_dir = agents_root / ".drafts" / "notes_agent"
    metadata = agent_workbench.load_agent_draft_metadata("notes_agent", agents_root=agents_root)

    assert result["status"] == "success"
    assert (draft_dir / "agent.py").is_file()
    assert (draft_dir / "prompt.py").is_file()
    assert (draft_dir / "website" / "manifest.json").is_file()
    assert metadata["draft_kind"] == "clone"
    assert metadata["owners"]["builder"]["status"] == "cloned"
    assert metadata["owners"]["builder"]["source_code_available"] is True


def test_publish_agent_draft_copies_to_live_workspace_and_marks_installed(tmp_path, monkeypatch):
    agents_root = tmp_path / "autoyou_agents"
    registry_path = tmp_path / "agent_install_registry.json"

    monkeypatch.setattr(agent_workbench, "is_compiled", lambda: False)
    monkeypatch.setenv("AUTOYOU_AGENT_INSTALL_REGISTRY_PATH", str(registry_path))

    scaffold_result = agent_workbench.scaffold_agent_draft(
        "weather_agent",
        "Fetches weather data.",
        "get_weather",
        "Return the current weather.",
        agents_root=agents_root,
    )

    publish_result = agent_workbench.publish_agent_draft(
        scaffold_result["agent_name"],
        install_after_publish=True,
        agents_root=agents_root,
    )

    live_dir = agents_root / "weather_agent"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    assert publish_result["status"] == "success"
    assert (live_dir / "agent.py").is_file()
    assert (live_dir / "prompt.py").is_file()
    assert registry["agents"]["weather_agent"]["installed"] is True


def test_build_live_agent_source_info_marks_importable_packaged_agent_as_existing(monkeypatch):
    monkeypatch.setattr(agent_workbench, "resolve_live_agent_dir", lambda agent_name: None)
    monkeypatch.setattr(agent_workbench, "is_compiled", lambda: True)

    result = agent_workbench.build_live_agent_source_info("notes_agent")

    assert result["exists"] is True
    assert result["agent_dir"] is None
    assert result["source_kind"] == "embedded"
    assert result["prompt_importable"] is True
