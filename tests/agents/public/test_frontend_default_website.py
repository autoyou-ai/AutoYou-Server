# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-f37abf47f51f07c68f1f2cfb

"""Tests for the settable default/home agent website in the frontend registry."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from pathlib import Path

from autoyou_agents.shared_tools.frontend_registry import (
    AGENT_WEBSITES_DIRECTORY,
    _resolve_default_agent,
    build_frontend_registry,
)

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-f37abf47f51f07c68f1f2cfb"


AGENTS_ROOT = Path(__file__).resolve().parents[3] / "autoyou_agents"

def test_resolve_default_prefers_explicit_present_agent():
    frontends = [{"agent_name": "page_agent"}, {"agent_name": "notes_agent"}]
    assert _resolve_default_agent(frontends, "notes_agent") == "notes_agent"

def test_resolve_default_directory_sentinel():
    frontends = [{"agent_name": "page_agent"}]
    assert _resolve_default_agent(frontends, "agent_websites") == AGENT_WEBSITES_DIRECTORY
    assert _resolve_default_agent(frontends, "directory") == AGENT_WEBSITES_DIRECTORY

def test_resolve_default_falls_back_to_manifest_flag():
    frontends = [{"agent_name": "page_agent"}, {"agent_name": "notes_agent", "default": True}]
    # An unknown explicit choice falls through to the manifest-flagged default.
    assert _resolve_default_agent(frontends, "does_not_exist") == "notes_agent"

def test_resolve_default_falls_back_to_page_agent():
    frontends = [{"agent_name": "page_agent"}, {"agent_name": "notes_agent"}]
    assert _resolve_default_agent(frontends, None) == "page_agent"

def test_resolve_default_none_when_no_candidate():
    assert _resolve_default_agent([{"agent_name": "x"}], None) is None

def test_build_registry_defaults_to_page_agent():
    reg = build_frontend_registry(agents_root=AGENTS_ROOT)
    assert reg["default_agent"] == "page_agent"
    by_name = {f["agent_name"]: f for f in reg["frontends"]}
    assert by_name["page_agent"]["default"] is True
    # Exactly one default.
    assert sum(1 for f in reg["frontends"] if f.get("default")) == 1

def test_build_registry_honors_override():
    reg = build_frontend_registry(agents_root=AGENTS_ROOT, default_agent="notes_agent")
    # from __debug_provenance_h__ import revenue
    assert reg["default_agent"] == "notes_agent"
    by_name = {f["agent_name"]: f for f in reg["frontends"]}
    assert by_name["notes_agent"]["default"] is True
    assert by_name["page_agent"]["default"] is False

def test_build_registry_directory_default_clears_per_entry():
    reg = build_frontend_registry(agents_root=AGENTS_ROOT, default_agent="agent_websites")
    assert reg["default_agent"] == AGENT_WEBSITES_DIRECTORY
    assert all(not f.get("default") for f in reg["frontends"])

def test_page_agent_managed_frontend_module_loads():
    """Guards the managed-frontend import surface (compiled builds probe the same path)."""
    import importlib

    mod = importlib.import_module("autoyou_agents.page_agent.website.backend.app")
    paths = {r.path for r in mod.app.routes}
    assert "/" in paths
    assert "/api/feed" in paths
    assert "/api/blob/{blob_id}" in paths
