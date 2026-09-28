# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from pathlib import Path

import pytest

import shared.platform_runtime as platform_runtime

from autoyou_agents.shared_tools.agent_install_registry import (
    BUILTIN_AGENT_PACKAGE_NAMES,
    get_agent_install_registry_path,
    load_agent_install_registry,
    PRIVATE_AGENT_PACKAGE_NAMES,
    DEFAULT_AGENT_INSTALL_STATES,
    refresh_agent_install_registry,
    set_agent_installed,
    can_install_agent_in_runtime,
    discover_agent_directories,
)


def test_private_package_mechanism_blocks_compiled_runtime(monkeypatch):
    import autoyou_agents.shared_tools.agent_install_registry as registry

    monkeypatch.setattr(registry, "PRIVATE_AGENT_PACKAGE_NAMES", frozenset({"notes_agent"}))
    monkeypatch.setattr(
        registry,
        "BUILTIN_AGENT_PACKAGE_NAMES",
        frozenset(registry.DEFAULT_AGENT_INSTALL_STATES) - frozenset({"notes_agent"}),
    )

    assert registry.can_install_agent_in_runtime("notes_agent", compiled=True) is False
    assert registry.can_install_agent_in_runtime("notes_agent", compiled=False) is True
    assert "is a private agent package" in registry.runtime_install_block_reason("notes_agent")
    # An undeclared name still gets the generic workspace-draft explanation.
    assert "Workspace draft only" in registry.runtime_install_block_reason("some_draft_agent")


def _write_agent_dir(agents_root: Path, agent_name: str) -> None:
    agent_dir = agents_root / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "agent.py").write_text("AGENT_NAME = 'test'\n", encoding="utf-8")


def test_source_discovery_includes_ignored_private_agent_folder(tmp_path, monkeypatch):
    agents_root = tmp_path / "autoyou_agents"
    _write_agent_dir(agents_root / "private", "synthetic_private_agent")
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(platform_runtime, "get_dynamic_agents_root", lambda *args, **kwargs: agents_root)

    assert "synthetic_private_agent" in discover_agent_directories(agents_root)


def test_refresh_agent_install_registry_applies_default_install_states(tmp_path):
    agents_root = tmp_path / "autoyou_agents"
    agents_root.mkdir()
    for agent_name in (
        "agent_builder_agent",
        "coding_agent",
        "media_generation_agent",
        "notes_agent",
        "custom_agent",
        "website_agent",
    ):
        _write_agent_dir(agents_root, agent_name)

    registry_path = tmp_path / "agent_install_registry.json"
    payload = refresh_agent_install_registry(
        agents_root=agents_root,
        registry_path=registry_path,
    )

    assert payload["installed_agents"] == ["notes_agent"]
    assert set(payload["available_agents"]) == {
        "agent_builder_agent",
        "coding_agent",
        "custom_agent",
        "media_generation_agent",
        "website_agent",
    }

    reloaded = load_agent_install_registry(
        agents_root=agents_root,
        registry_path=registry_path,
    )
    assert reloaded["installed_agents"] == ["notes_agent"]
    assert set(reloaded["available_agents"]) == set(payload["available_agents"])


def test_release_agent_defaults_match_public_private_policy():
    # The first-run set is deliberately lean: a small local model has to be able
    # to pick reliably among these, and every omitted agent is one click away in
    # Settings. Anything added here widens the root prompt for every install.
    default_on = {
        "admin_agent",
        "ads_watching_agent",
        "audio_agent",
        "client_browser_control_agent",
        "donation_agent",
        "earnings_agent",
        "internet_agent",
        "memory_agent",
        "notes_agent",
        "notify_agent",
        "page_agent",
        "persona_agent",
        "tasks_agent",
    }
    opt_in = {
        "build_prompt_agent",
        "browser_agent",
        "backup_agent",
        "coding_agent",
        "data_collector_agent",
        "education_agent",
        "files_agent",
        "fine_tuning_agent",
        "hosting_agent",
        "ionos_agent",
        "ionos_cloudflare_agent",
        "location_agent",
        "mail_agent",
        "mac_security_agent",
        "model_picker_agent",
        "skills_agent",
        "voice_training_agent",
        "win_security_agent",
        "agent_builder_agent",
        "claude_cli_agent",
        "claude_desktop_agent",
        "cli_agent",
        "cloudflare_agent",
        "codex_desktop_agent",
        "hermes_agent",
        "media_generation_agent",
        "openclaw_agent",
        "proxy_agent",
        "remote_desktop_agent",
        "robinhood_agent",
        "trading_agent",
        "website_agent",
    }

    for agent_name in default_on:
        assert DEFAULT_AGENT_INSTALL_STATES[agent_name] is True
    for agent_name in opt_in | PRIVATE_AGENT_PACKAGE_NAMES:
        assert DEFAULT_AGENT_INSTALL_STATES[agent_name] is False
    for agent_name in PRIVATE_AGENT_PACKAGE_NAMES:
        assert agent_name not in BUILTIN_AGENT_PACKAGE_NAMES
        assert can_install_agent_in_runtime(agent_name, compiled=True) is False

    assert PRIVATE_AGENT_PACKAGE_NAMES == {
        "cloudflare_agent",
        "ionos_agent",
        "ionos_cloudflare_agent",
        "mail_agent",
        "robinhood_agent",
        "trading_agent",
    }
    assert set(DEFAULT_AGENT_INSTALL_STATES) == default_on | opt_in
    for agent_name in set(DEFAULT_AGENT_INSTALL_STATES) - PRIVATE_AGENT_PACKAGE_NAMES:
        assert agent_name in BUILTIN_AGENT_PACKAGE_NAMES
        assert can_install_agent_in_runtime(agent_name, compiled=True) is True


def test_legacy_streaming_agent_registry_entry_is_migrated_to_education_agent(tmp_path):
    agents_root = tmp_path / "autoyou_agents"
    _write_agent_dir(agents_root, "education_agent")
    registry_path = tmp_path / "agent_install_registry.json"
    registry_path.write_text(
        '{"agents":{"streaming_agent":{"installed":false,"description":"Legacy"}}}',
        encoding="utf-8",
    )

    payload = load_agent_install_registry(agents_root=agents_root, registry_path=registry_path)

    assert payload["installed_agents"] == []
    assert payload["agents"]["education_agent"]["description"] == "Legacy"
    assert "streaming_agent" not in payload["agents"]


def test_load_agent_install_registry_accepts_windows_utf8_bom(tmp_path):
    agents_root = tmp_path / "autoyou_agents"
    _write_agent_dir(agents_root, "notes_agent")
    registry_path = tmp_path / "agent_install_registry.json"
    registry_path.write_bytes(
        b"\xef\xbb\xbf"
        + b'{"agents":{"notes_agent":{"installed":true,"source":"ecosystem-installer"}}}'
    )

    payload = load_agent_install_registry(agents_root=agents_root, registry_path=registry_path)

    assert payload["installed_agents"] == ["notes_agent"]
    assert payload["agents"]["notes_agent"]["source"] == "ecosystem-installer"


def test_default_registry_path_respects_autoyou_test_root(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(runtime_root))

    registry_path = get_agent_install_registry_path()

    assert registry_path == (runtime_root / "AutoYou" / "agent_install_registry.json").resolve()


def test_set_agent_installed_updates_disk_backed_registry(tmp_path):
    agents_root = tmp_path / "autoyou_agents"
    agents_root.mkdir()
    _write_agent_dir(agents_root, "custom_agent")

    registry_path = tmp_path / "agent_install_registry.json"
    refresh_agent_install_registry(agents_root=agents_root, registry_path=registry_path)

    payload = set_agent_installed(
        "custom_agent",
        True,
        description="Custom runtime agent.",
        source="test",
        agents_root=agents_root,
        registry_path=registry_path,
    )

    assert payload["installed_agents"] == ["custom_agent"]
    entry = payload["agents"]["custom_agent"]
    assert entry["installed"] is True
    assert entry["description"] == "Custom runtime agent."
    assert entry["source"] == "test"


def test_refresh_agent_install_registry_discovers_runtime_plugins_in_compiled_mode(tmp_path, monkeypatch):
    embedded_root = tmp_path / "embedded_agents"
    embedded_root.mkdir()

    dynamic_root = tmp_path / "dynamic_agents"
    dynamic_root.mkdir()
    _write_agent_dir(dynamic_root, "custom_agent")

    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)
    monkeypatch.setattr(
        platform_runtime,
        "iter_agent_roots",
        lambda anchor, app_name="AutoYou": (dynamic_root, embedded_root),
    )

    registry_path = tmp_path / "compiled_registry.json"
    payload = refresh_agent_install_registry(
        agents_root=embedded_root,
        registry_path=registry_path,
    )

    assert "custom_agent" in payload["available_agents"]
    assert "notes_agent" in payload["installed_agents"]


def test_compiled_registry_omits_private_agent_names_by_default(tmp_path, monkeypatch):
    embedded_root = tmp_path / "embedded_agents"
    embedded_root.mkdir()

    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)
    monkeypatch.setattr(platform_runtime, "iter_agent_roots", lambda anchor, app_name="AutoYou": (embedded_root,))

    payload = refresh_agent_install_registry(
        agents_root=embedded_root,
        registry_path=tmp_path / "compiled_registry.json",
    )

    for agent_name in PRIVATE_AGENT_PACKAGE_NAMES:
        assert agent_name not in payload["agents"]
        assert agent_name not in payload["available_agents"]


def test_compiled_registry_accepts_only_agents_named_in_server_bundle(tmp_path, monkeypatch):
    import autoyou_agents.shared_tools.agent_install_registry as registry

    embedded_root = tmp_path / "runtime_modules" / "autoyou_agents"
    (embedded_root / "shared_tools").mkdir(parents=True)
    (embedded_root / "packaged_sibling_agents.json").write_text(
        '["trading_agent", "../unexpected_agent", 42]', encoding="utf-8",
    )
    monkeypatch.setattr(registry, "__file__", str(embedded_root / "shared_tools/agent_install_registry.py"))
    monkeypatch.setattr(registry, "PACKAGED_SIBLING_AGENT_NAMES", registry._load_packaged_sibling_agent_names())
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)
    monkeypatch.setattr(platform_runtime, "iter_agent_roots", lambda anchor, app_name="AutoYou": (embedded_root,))

    assert registry.can_install_agent_in_runtime("trading_agent", compiled=True)
    assert registry.is_builtin_agent_name("trading_agent")
    assert not registry.can_install_agent_in_runtime("mail_agent", compiled=True)
    assert not registry.can_install_agent_in_runtime("unexpected_agent", compiled=True)
    assert "trading_agent" in registry.discover_agent_directories(embedded_root)
    assert "mail_agent" not in registry.discover_agent_directories(embedded_root)


def test_compiled_registry_forces_workspace_agents_to_stay_uninstalled(tmp_path, monkeypatch):
    agents_root = tmp_path / "autoyou_agents"
    agents_root.mkdir()
    _write_agent_dir(agents_root, "notes_agent")
    _write_agent_dir(agents_root, "custom_agent")

    registry_path = tmp_path / "compiled_registry.json"
    registry_path.write_text(
        (
            '{"agents":{"notes_agent":{"installed":true},'
            '"custom_agent":{"installed":true,"description":"Draft"}}}'
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)
    monkeypatch.setattr(
        platform_runtime,
        "iter_agent_roots",
        lambda anchor, app_name="AutoYou": (agents_root,),
    )

    payload = load_agent_install_registry(
        agents_root=agents_root,
        registry_path=registry_path,
    )

    assert "notes_agent" in payload["installed_agents"]
    assert "custom_agent" not in payload["installed_agents"]
    assert "custom_agent" in payload["available_agents"]


def test_set_agent_installed_rejects_workspace_agent_install_in_compiled_mode(tmp_path, monkeypatch):
    agents_root = tmp_path / "autoyou_agents"
    agents_root.mkdir()
    _write_agent_dir(agents_root, "custom_agent")

    registry_path = tmp_path / "agent_install_registry.json"
    refresh_agent_install_registry(agents_root=agents_root, registry_path=registry_path)

    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: True)

    with pytest.raises(PermissionError):
        set_agent_installed(
            "custom_agent",
            True,
            agents_root=agents_root,
            registry_path=registry_path,
        )
