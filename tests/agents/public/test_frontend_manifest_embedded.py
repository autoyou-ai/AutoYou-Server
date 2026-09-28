# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-e658e086944cbc0d4b60292d


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-e658e086944cbc0d4b60292d"

from pathlib import Path

import shared.platform_runtime as platform_runtime
import autoyou_agents.shared_tools.frontend_manifest as frontend_manifest

from autoyou_agents.shared_tools.frontend_manifest import (
    build_frontend_discovery_entry,
    build_frontend_manifest,
    discover_frontend_manifests,
    load_frontend_manifest,
    write_frontend_manifest,
)


def test_proxy_frontend_manifest_builds_launchable_notes_entry():
    manifest = build_frontend_manifest(
        agent_name="notes_agent",
        title="Notes Library",
        description="Read-only notes UI",
        recommended_port=8094,
        requires_proxy_registration=True,
    )

    entry = build_frontend_discovery_entry(
        manifest=manifest,
        proxy_port=8094,
        browser_base_url="http://127.0.0.1:8067",
    )

    assert entry["frontend_port_registered"] is True
    assert entry["proxy_port"] == 8094
    assert entry["proxy_path"] == "/agent/notes_agent/"
    assert entry["launch_url"] == "http://127.0.0.1:8067/agent/notes_agent/"
    assert entry["local_url"] == "http://127.0.0.1:8094/"


def test_direct_forward_frontend_prefers_same_port_local_url():
    manifest = build_frontend_manifest(
        agent_name="admin_agent",
        title="Admin UI",
        description="Admin dashboard",
        recommended_port=8001,
        requires_proxy_registration=True,
    )
    manifest["direct_forward_port"] = 8001

    entry = build_frontend_discovery_entry(
        manifest=manifest,
        proxy_port=8001,
        browser_base_url="http://127.0.0.1:8067",
    )

    assert entry["launch_url"] == "http://127.0.0.1:8067/agent/admin_agent/"
    assert entry["local_url"] == "http://127.0.0.1:8001/"


def test_discover_frontend_manifests_reads_runtime_plugin_root_in_compiled_mode(tmp_path, monkeypatch):
    embedded_root = tmp_path / "embedded_agents"
    embedded_root.mkdir()

    dynamic_root = tmp_path / "dynamic_agents"
    dynamic_agent_dir = dynamic_root / "custom_agent"
    dynamic_agent_dir.mkdir(parents=True)

    write_frontend_manifest(
        dynamic_agent_dir,
        build_frontend_manifest(
            agent_name="custom_agent",
            title="Custom Agent UI",
            description="Runtime plugin UI",
            recommended_port=9100,
        ),
    )

    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)
    monkeypatch.setattr(
        platform_runtime,
        "iter_agent_roots",
        lambda anchor, app_name="AutoYou": (dynamic_root, embedded_root),
    )

    entries = discover_frontend_manifests(
        agents_root=embedded_root,
        agent_names=["custom_agent"],
        proxy_ports={"custom_agent": 9100},
        browser_base_url="http://127.0.0.1:8067",
    )

    assert len(entries) == 1
    assert entries[0]["agent_name"] == "custom_agent"
    assert entries[0]["launch_url"] == "http://127.0.0.1:8067/agent/custom_agent/"


def test_load_frontend_manifest_reads_embedded_path_without_importing_package(tmp_path, monkeypatch):
    embedded_root = tmp_path / "runtime_modules" / "autoyou_agents"
    agent_dir = embedded_root / "media_generation_agent"
    write_frontend_manifest(
        agent_dir,
        build_frontend_manifest(
            agent_name="media_generation_agent",
            title="Media Generator",
            description="Generate media",
            recommended_port=8067,
        ),
    )

    monkeypatch.setattr(
        platform_runtime,
        "iter_agent_roots",
        lambda anchor, app_name="AutoYou": (embedded_root,),
    )
    monkeypatch.setattr(
        frontend_manifest.importlib_resources,
        "files",
        lambda package_name: (_ for _ in ()).throw(AssertionError("package import fallback should not run")),
    )

    manifest = load_frontend_manifest(tmp_path / "dynamic_agents" / "media_generation_agent")

    assert manifest is not None
    assert manifest["agent_name"] == "media_generation_agent"
    assert manifest["website_root"] == str(agent_dir / "website")


def test_load_frontend_manifest_ignores_invalid_json(tmp_path):
    agent_dir = tmp_path / "broken_agent"
    manifest_dir = agent_dir / "website"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text("{not valid json", encoding="utf-8")

    manifest = load_frontend_manifest(agent_dir)

    assert manifest is None


def test_fine_tuning_frontend_manifest_is_path_proxy_on_8068():
    repo_root = Path(__file__).resolve().parents[3]
    manifest = load_frontend_manifest(repo_root / "autoyou_agents" / "fine_tuning_agent")

    assert manifest is not None
    assert manifest["agent_name"] == "fine_tuning_agent"
    assert manifest["recommended_port"] == 8068
    assert manifest["requires_proxy_registration"] is True

    entry = build_frontend_discovery_entry(
        manifest=manifest,
        proxy_port=8068,
        browser_base_url="http://127.0.0.1:8067",
    )

    assert entry["proxy_path"] == "/agent/fine_tuning_agent/"
    assert entry["launch_url"] == "http://127.0.0.1:8067/agent/fine_tuning_agent/"
    assert entry["local_url"] == "http://127.0.0.1:8068/"
