# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-534454206164647265737320-b58f7e4f5f8a667584558276


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-534454206164647265737320-b58f7e4f5f8a667584558276"

import json

import shared.platform_runtime as platform_runtime

from autoyou_agents.shared_tools.agent_directory import (
    build_agent_directory_payload,
    build_agent_directory_entry,
    command_name_for_agent,
    concise_agent_description,
    load_agent_prompt_description,
)


def test_command_name_for_agent_strips_runtime_wrappers():
    assert command_name_for_agent("autoyou_notes_agent") == "notes"
    assert command_name_for_agent("autoyou_audio_agent") == "audio"
    assert command_name_for_agent("website_agent") == "website"
    assert command_name_for_agent("autoyou_website_agent") == "website"
    assert command_name_for_agent("agent_website_builder_agent") == "website"
    assert command_name_for_agent("frontend_proxy_agent") == "website"
    assert command_name_for_agent("agent_builder_agent") == "agent builder"


def test_concise_agent_description_uses_first_sentence():
    description = "A concise first sentence. A second sentence that should not show up."
    assert concise_agent_description(description, fallback_display_name="Notes") == "A concise first sentence."


def test_build_agent_directory_entry_shapes_display_fields():
    entry = build_agent_directory_entry(
        "notes_agent",
        frontend_entry={
            "launch_path": "/agent/notes_agent/",
            "proxy_path": "/agent/notes_agent/",
            "entry_path": "/",
            "proxy_port": 8094,
        },
    )

    assert entry["agent_name"] == "autoyou_notes_agent"
    assert entry["package_name"] == "notes_agent"
    assert entry["display_name"] == "Notes"
    assert entry["command_name"] == "notes"
    assert entry["title"] == "Notes"
    assert entry["launch_path"] == "/agent/notes_agent/"


def test_build_agent_directory_payload_uses_single_install_registry_source(tmp_path):
    agents_root = tmp_path / "autoyou_agents"
    for agent_name in ("notes_agent", "audio_agent", "draft_agent"):
        agent_dir = agents_root / agent_name
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.py").write_text("AGENT_NAME = 'test'\n", encoding="utf-8")

    install_registry = {
        "updated_at": "2026-05-24 02:00 UTC",
        "agents": {
            "notes_agent": {"installed": True},
            "audio_agent": {"installed": True},
            "draft_agent": {"installed": False},
        },
        "installed_agents": ["notes_agent", "audio_agent"],
    }
    frontend_registry = {
        "updated_at": "2026-05-24 02:01 UTC",
        "frontends": [
            {
                "agent_name": "audio_agent",
                "launch_path": "/agent/audio_agent/",
                "proxy_path": "/agent/audio_agent/",
                "entry_path": "/",
                "proxy_port": 8097,
            }
        ],
    }

    installed_payload = build_agent_directory_payload(
        agents_root=agents_root,
        install_registry=install_registry,
        frontend_registry=frontend_registry,
        installed_only=True,
    )
    all_payload = build_agent_directory_payload(
        agents_root=agents_root,
        install_registry=install_registry,
        frontend_registry=frontend_registry,
        installed_only=False,
    )

    assert installed_payload["agent_names"] == ["autoyou_agent", "audio_agent", "notes_agent"]
    assert all_payload["agent_names"] == ["autoyou_agent", "audio_agent", "draft_agent", "notes_agent"]
    main = next(item for item in installed_payload["agents"] if item["package_name"] == "autoyou_agent")
    assert main["title"] == "Main"
    assert main["command_name"] == "AutoYou"
    assert main["description"] == "Your main AutoYou assistant for everyday help and switching to specialist agents."
    audio = next(item for item in installed_payload["agents"] if item["package_name"] == "audio_agent")
    assert audio["installed"] is True
    assert audio["has_frontend"] is True
    assert audio["launch_path"] == "/agent/audio_agent/"


def test_load_agent_prompt_description_uses_registry_for_workspace_drafts_in_compiled_mode(
    tmp_path,
    monkeypatch,
):
    agents_root = tmp_path / "autoyou_agents"
    agent_dir = agents_root / "custom_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.py").write_text("def create_custom_agent():\n    return None\n", encoding="utf-8")
    (agent_dir / "prompt.py").write_text("raise RuntimeError('should not import')\n", encoding="utf-8")

    registry_path = tmp_path / "agent_install_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "agents": {
                    "custom_agent": {
                        "installed": False,
                        "description": "Workspace draft description.",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)
    monkeypatch.setenv("AUTOYOU_AGENT_INSTALL_REGISTRY_PATH", str(registry_path))

    description = load_agent_prompt_description("custom_agent", agents_root=agents_root)

    assert description == "Workspace draft description."
