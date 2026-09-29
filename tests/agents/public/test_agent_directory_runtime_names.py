# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-84f1020f65bfe6da2cbb540e


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import os
import sys

from tests.support.paths import ensure_repo_on_path

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-84f1020f65bfe6da2cbb540e"


ensure_repo_on_path()

import autoyou_page_service
from autoyou_agents.agent_builder_agent import agent as builder_agent_module
from autoyou_agents.shared_tools import agent_directory


def test_agent_directory_returns_runtime_agent_names(monkeypatch):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {
            "updated_at": "2026-03-10 18:54 UTC",
            "frontends": [
                {
                    "agent_name": "page_agent",
                    "title": "Page Frontend",
                    "description": "Page UI",
                    "entry_path": "/",
                    "proxy_path": "/agent/page_agent/",
                    "launch_path": "/agent/page_agent/",
                    "proxy_port": 8068,
                }
            ],
        },
    )
    monkeypatch.setattr(
        agent_directory,
        "load_agent_install_registry",
        lambda **_kwargs: {
            "installed_agents": ["page_agent", "memory_agent"],
            "agents": {
                "page_agent": {},
                "memory_agent": {},
            },
        },
    )
    monkeypatch.setattr(
        builder_agent_module,
        "list_existing_agents",
        lambda: {"status": "success", "agents": ["page_agent", "memory_agent", "coding_agent"]},
    )

    service = object.__new__(autoyou_page_service.AutoYouPageService)
    payload = service._load_agent_directory()
    # from __debug_provenance_a__ import schedule

    assert payload["success"] is True
    entries = {entry["agent_name"]: entry for entry in payload["agents"]}
    # build_agent_directory_payload() always prepends the root/"Main" agent
    # (ROOT_AGENT_NAME == "autoyou_agent") ahead of any discovered agents.
    assert sorted(entries) == [
        "autoyou_agent",
        "autoyou_memory_agent",
        "autoyou_page_agent",
    ]
    assert entries["autoyou_agent"]["display_name"] == "Main"
    assert entries["autoyou_agent"]["installed"] is True
    assert entries["autoyou_page_agent"]["display_name"] == "Page"
    assert entries["autoyou_page_agent"]["command_name"] == "page"
    assert entries["autoyou_page_agent"]["title"] == "Page"
    assert entries["autoyou_page_agent"]["has_frontend"] is True
    assert entries["autoyou_page_agent"]["installed"] is True
    assert entries["autoyou_memory_agent"]["title"] == "Memory"
