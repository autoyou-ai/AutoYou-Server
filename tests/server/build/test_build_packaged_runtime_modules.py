# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from pathlib import Path

import pytest

import scripts.build_packaged_runtime_modules as runtime_builder


def _write_text(path: Path, content: str = "pass\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _populate_required_runtime_sources(repo_root: Path) -> None:
    for relative_path in runtime_builder.TOP_LEVEL_RUNTIME_MODULES:
        _write_text(repo_root / relative_path, "VALUE = 1\n")
    # build_runtime_module_plan() raises FileNotFoundError unless every package
    # root exists. Seed them from the production tuple rather than by name, so
    # adding a root cannot silently break this fixture again - core_server/ and
    # routers/ were added by the route/runtime refactor (326f0c7d) and left
    # these tests failing.
    for package_root in runtime_builder.PACKAGE_RUNTIME_ROOTS:
        _write_text(repo_root / package_root / "_fixture_runtime_module.py", "VALUE = 1\n")
    _write_text(
        repo_root / "shared" / "tunnelmole_node_launcher.mjs",
        "console.log('runtime launcher');\n",
    )
    _write_text(repo_root / "shared" / "log_redaction.py", "VALUE = 1\n")
    # STATIC_RUNTIME_DIRECTORIES entries must exist with at least one file each;
    # mirror the real repo's vendored libsodium platform payloads.
    for relative_directory in runtime_builder.STATIC_RUNTIME_DIRECTORIES:
        if relative_directory == Path("shared") / "native" / "libsodium":
            _write_text(repo_root / relative_directory / "darwin-arm64" / "libsodium.dylib", "sidecar\n")
            _write_text(repo_root / relative_directory / "windows-x86_64" / "libsodium.dll", "sidecar\n")
        else:
            _write_text(repo_root / relative_directory / "sidecar.bin", "sidecar\n")


def test_runtime_module_plan_excludes_autoyou_lite_from_main_bundle(tmp_path):
    _populate_required_runtime_sources(tmp_path)
    _write_text(tmp_path / "shared" / "platform_runtime.py")
    _write_text(tmp_path / "autoyou_agents" / "agent.py")
    _write_text(tmp_path / "openclaw" / "autoyou_lite" / "server.py")

    plan = runtime_builder.build_runtime_module_plan(tmp_path)
    compiled_paths = {spec.source_relative_path.as_posix() for spec in plan.compile_specs}

    assert "shared/platform_runtime.py" in compiled_paths
    assert "autoyou_agents/agent.py" in compiled_paths
    assert "autoyou_lite/server.py" not in compiled_paths


def test_runtime_module_plan_keeps_embedded_agents_with_sibling_checkout(tmp_path):
    server_root = tmp_path / "AutoYou-Server"
    _populate_required_runtime_sources(server_root)
    sibling_root = tmp_path / "autoyou_agents"
    _write_text(sibling_root / "__init__.py", "")
    _write_text(sibling_root / "agent.py")
    _write_text(sibling_root / "notes_agent" / "sibling_only.py")
    _write_text(server_root / "autoyou_agents" / "notes_agent" / "stale_only.py")

    plan = runtime_builder.build_runtime_module_plan(server_root)
    compiled_paths = {spec.source_relative_path.as_posix() for spec in plan.compile_specs}

    assert "autoyou_agents/notes_agent/stale_only.py" in compiled_paths
    assert "autoyou_agents/notes_agent/sibling_only.py" not in compiled_paths


def test_server_plan_includes_sibling_agents_private_agents_and_safe_assets(tmp_path, monkeypatch):
    server_root = tmp_path / "AutoYou-Server"
    _populate_required_runtime_sources(server_root)
    _write_text(server_root / "autoyou_agents" / "notes_agent" / "agent.py")
    sibling_root = tmp_path / "autoyou_agents"
    _write_text(sibling_root / "__init__.py", "")
    _write_text(sibling_root / "notes_agent" / "agent.py", "SERVER_SOURCE_MUST_WIN = False\n")
    _write_text(sibling_root / "notes_agent" / "desktop_assets" / "icon.svg", "<svg/>\n")
    _write_text(sibling_root / "trading_agent" / "agent.py")
    _write_text(sibling_root / "trading_agent" / "website" / "manifest.json", "{}\n")
    _write_text(sibling_root / "trading_agent" / "trading_agent" / "trading_agent.db", "live state\n")
    _write_text(sibling_root / "private" / "mail_agent" / "agent.py")
    _write_text(sibling_root / "private" / "mail_agent" / "prompt.py")
    _write_text(sibling_root / "private" / "mail_agent" / "AGENT.md", "private context\n")
    _write_text(sibling_root / "private" / "mail_agent" / "mail_agent" / "configuration.json", "live config\n")
    _write_text(sibling_root / "private" / "mail_agent" / "worker" / "index.mjs", "export default {};\n")
    _write_text(sibling_root / "private" / "mail_agent" / "worker" / "index.test.mjs", "test code\n")
    _write_text(sibling_root / "private" / "mail_agent" / "worker" / ".env", "secret\n")
    _write_text(sibling_root / "private" / "mail_agent" / "worker" / "node_modules" / "pkg" / "index.js", "package\n")

    plan = runtime_builder.build_runtime_module_plan(server_root, include_sibling_agents=True)
    compiled = {spec.source_relative_path.as_posix() for spec in plan.compile_specs}
    assets = {path.as_posix() for path in plan.asset_files}

    assert "autoyou_agents/notes_agent/agent.py" in compiled
    assert Path("autoyou_agents/notes_agent/agent.py") not in plan.source_overrides
    assert "autoyou_agents/trading_agent/agent.py" in compiled
    assert "autoyou_agents/mail_agent/agent.py" in compiled
    assert "autoyou_agents/mail_agent/prompt.py" in compiled
    assert "autoyou_agents/notes_agent/desktop_assets/icon.svg" in assets
    assert "autoyou_agents/trading_agent/website/manifest.json" in assets
    assert "autoyou_agents/mail_agent/AGENT.md" in assets
    assert "autoyou_agents/mail_agent/worker/index.mjs" in assets
    assert "autoyou_agents/mail_agent/worker/index.test.mjs" not in assets
    assert "autoyou_agents/mail_agent/worker/.env" not in assets
    assert "autoyou_agents/mail_agent/worker/node_modules/pkg/index.js" not in assets
    assert "autoyou_agents/trading_agent/trading_agent/trading_agent.db" not in assets
    assert "autoyou_agents/mail_agent/mail_agent/configuration.json" not in assets
    assert plan.sibling_agent_names == ("mail_agent", "trading_agent")

    def fake_run_nuitka_module_build(*, output_root, spec, source_override=None, **_kwargs):
        if spec.source_relative_path == Path("autoyou_agents/mail_agent/agent.py"):
            assert source_override == sibling_root / "private" / "mail_agent" / "agent.py"
        destination = output_root / spec.destination_relative_dir / f"{spec.source_stem}.cp313-linux_x86_64.so"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"compiled-module")
        return destination

    monkeypatch.setattr(runtime_builder, "_run_nuitka_module_build", fake_run_nuitka_module_build)
    bundle_root = tmp_path / "bundle"
    manifest = runtime_builder.build_packaged_runtime_modules(
        repo_root=server_root,
        bundle_root=bundle_root,
        build_root=tmp_path / "build",
        job_count=1,
        extra_nuitka_args=(),
        include_sibling_agents=True,
    )
    marker = bundle_root / "runtime_modules/autoyou_agents/packaged_sibling_agents.json"
    assert marker.read_text(encoding="utf-8").strip().startswith('[\n  "mail_agent"')
    assert "runtime_modules/autoyou_agents/packaged_sibling_agents.json" in manifest["files"]
    assert not (bundle_root / "runtime_modules/autoyou_agents/mail_agent/mail_agent/configuration.json").exists()


def test_native_v2_plan_rejects_sibling_overlay(tmp_path):
    with pytest.raises(ValueError, match="cannot include sibling agents"):
        runtime_builder.build_runtime_module_plan(tmp_path, desktop=True, include_sibling_agents=True)


def test_desktop_runtime_plan_reads_private_native_sources_beside_server(tmp_path):
    server_root = tmp_path / "AutoYou-Server"
    _populate_required_runtime_sources(server_root)
    for name in (
        "autoyou_client.py", "desktop_client.py", "cloud_pair.py", "audio_streams.py",
        "attachments_helper.py", "bluetooth_pairing_client.py", "http_proxy_client.py",
        "legal_acceptance.py", "location_beacon.py", "conversation_history.py",
    ):
        _write_text(tmp_path / "clients" / "python" / name)
    _write_text(tmp_path / "clients" / "python" / "peer_link" / "__init__.py")
    _write_text(tmp_path / "clients" / "python" / "peer_link" / "manager.py")
    _write_text(tmp_path / "v2" / "runtime" / "__init__.py")
    _write_text(tmp_path / "v2" / "runtime" / "worker.py")

    plan = runtime_builder.build_runtime_module_plan(server_root, desktop=True)
    compiled_paths = {spec.source_relative_path.as_posix() for spec in plan.compile_specs}

    assert "clients/python/autoyou_client.py" in compiled_paths
    assert "clients/python/peer_link/manager.py" in compiled_paths
    assert "v2/runtime/worker.py" in compiled_paths
    assert runtime_builder._source_path(server_root, Path("v2/runtime/worker.py")) == tmp_path / "v2/runtime/worker.py"


def test_runtime_module_plan_includes_agent_directory_shared_logic(tmp_path):
    _populate_required_runtime_sources(tmp_path)
    _write_text(tmp_path / "shared" / "platform_runtime.py")
    _write_text(tmp_path / "autoyou_agents" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "shared_tools" / "agent_directory.py")
    _write_text(tmp_path / "autoyou_agents" / "shared_tools" / "agent_install_registry.py")
    _write_text(tmp_path / "autoyou_agents" / "shared_tools" / "frontend_registry.py")
    _write_text(tmp_path / "autoyou_agents" / "shared_tools" / "desktop_app_registry.py")

    plan = runtime_builder.build_runtime_module_plan(tmp_path)
    compiled_paths = {spec.source_relative_path.as_posix() for spec in plan.compile_specs}

    assert "autoyou_agents/shared_tools/agent_directory.py" in compiled_paths
    assert "autoyou_agents/shared_tools/agent_install_registry.py" in compiled_paths
    assert "autoyou_agents/shared_tools/frontend_registry.py" in compiled_paths
    assert "autoyou_agents/shared_tools/desktop_app_registry.py" in compiled_paths


def test_build_packaged_runtime_modules_copies_static_runtime_sidecars(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    bundle_root = tmp_path / "bundle"
    build_root = tmp_path / "build"

    _populate_required_runtime_sources(repo_root)
    _write_text(repo_root / "shared" / "platform_runtime.py")
    _write_text(repo_root / "autoyou_agents" / "agent.py")

    def fake_run_nuitka_module_build(*, output_root, spec, **_kwargs):
        destination = output_root / spec.destination_relative_dir / f"{spec.source_stem}.cp313-linux_x86_64.so"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"compiled-module")
        return destination

    monkeypatch.setattr(runtime_builder, "_run_nuitka_module_build", fake_run_nuitka_module_build)

    manifest = runtime_builder.build_packaged_runtime_modules(
        repo_root=repo_root,
        bundle_root=bundle_root,
        build_root=build_root,
        job_count=1,
        extra_nuitka_args=(),
    )

    sidecar_path = bundle_root / runtime_builder.RUNTIME_MODULES_DIRNAME / "shared" / "tunnelmole_node_launcher.mjs"
    assert sidecar_path.read_text(encoding="utf-8") == "console.log('runtime launcher');\n"
    assert "runtime_modules/shared/tunnelmole_node_launcher.mjs" in manifest["files"]

    log_redaction_path = (
        bundle_root
        / runtime_builder.RUNTIME_MODULES_DIRNAME
        / "shared"
        / "log_redaction.cp313-linux_x86_64.so"
    )
    assert log_redaction_path.read_bytes() == b"compiled-module"
    assert "runtime_modules/shared/log_redaction.cp313-linux_x86_64.so" in manifest["files"]
    assert "runtime_modules/shared/log_redaction.py" not in manifest["allowed_python_files"]

    libsodium_path = (
        bundle_root
        / runtime_builder.RUNTIME_MODULES_DIRNAME
        / "shared" / "native" / "libsodium" / "darwin-arm64" / "libsodium.dylib"
    )
    assert libsodium_path.is_file()
    assert "runtime_modules/shared/native/libsodium/darwin-arm64/libsodium.dylib" in manifest["files"]
    assert "runtime_modules/shared/native/libsodium/windows-x86_64/libsodium.dll" in manifest["files"]


def test_runtime_module_plan_keeps_agent_ui_assets_only(tmp_path):
    _populate_required_runtime_sources(tmp_path)
    _write_text(tmp_path / "shared" / "platform_runtime.py")
    _write_text(tmp_path / "autoyou_agents" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "notes_agent" / "website" / "manifest.json", "{}\n")
    _write_text(tmp_path / "autoyou_agents" / "notes_agent" / "website" / "frontend" / "index.html", "<html></html>\n")
    _write_text(
        tmp_path / "autoyou_agents" / "shared_tools" / "scheduler_mission_control_frontend" / "assets" / "app.js",
        "console.log('mission-control');\n",
    )
    _write_text(tmp_path / "autoyou_agents" / "notes_agent" / "media" / "attachment.jpg", "ignored\n")
    _write_text(tmp_path / "autoyou_agents" / "internet_agent" / "chrome_profile" / "Default" / "Preferences", "ignored\n")

    plan = runtime_builder.build_runtime_module_plan(tmp_path)
    asset_paths = {path.as_posix() for path in plan.asset_files}

    assert "autoyou_agents/notes_agent/website/manifest.json" in asset_paths
    assert "autoyou_agents/notes_agent/website/frontend/index.html" in asset_paths
    assert (
        "autoyou_agents/shared_tools/scheduler_mission_control_frontend/assets/app.js"
        in asset_paths
    )
    assert "autoyou_agents/notes_agent/media/attachment.jpg" not in asset_paths
    assert "autoyou_agents/internet_agent/chrome_profile/Default/Preferences" not in asset_paths


def test_runtime_module_plan_uses_one_bytecode_package_bridge(tmp_path):
    _populate_required_runtime_sources(tmp_path)
    _write_text(tmp_path / "shared" / "platform_runtime.py")
    _write_text(tmp_path / "autoyou_agents" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "notes_agent" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "notes_agent" / "website" / "backend" / "app.py")

    plan = runtime_builder.build_runtime_module_plan(tmp_path)
    bridge_paths = {path.as_posix() for path in plan.bridge_stubs}

    assert bridge_paths == {"autoyou_agents/__init__.py"}


def test_build_packaged_runtime_modules_emits_bytecode_package_bridge(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    bundle_root = tmp_path / "bundle"
    build_root = tmp_path / "build"

    _populate_required_runtime_sources(repo_root)
    _write_text(repo_root / "shared" / "platform_runtime.py")
    _write_text(repo_root / "autoyou_agents" / "agent.py")
    _write_text(repo_root / "autoyou_agents" / "notes_agent" / "agent.py")
    _write_text(repo_root / "autoyou_agents" / "notes_agent" / "website" / "backend" / "app.py")

    def fake_run_nuitka_module_build(*, output_root, spec, **_kwargs):
        destination = output_root / spec.destination_relative_dir / f"{spec.source_stem}.cp313-linux_x86_64.so"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"compiled-module")
        return destination

    monkeypatch.setattr(runtime_builder, "_run_nuitka_module_build", fake_run_nuitka_module_build)

    manifest = runtime_builder.build_packaged_runtime_modules(
        repo_root=repo_root,
        bundle_root=bundle_root,
        build_root=build_root,
        job_count=1,
        extra_nuitka_args=(),
    )

    bridge_path = (
        bundle_root
        / runtime_builder.RUNTIME_MODULES_DIRNAME
        / "autoyou_agents"
        / "__init__.pyc"
    )
    assert bridge_path.is_file()
    assert "runtime_modules/autoyou_agents/__init__.pyc" in manifest["files"]
    assert "runtime_modules/autoyou_agents/__init__.py" not in manifest["allowed_python_files"]
    assert not bridge_path.with_suffix(".py").exists()


def test_runtime_module_plan_excludes_workspace_only_custom_agents(tmp_path):
    _populate_required_runtime_sources(tmp_path)
    _write_text(tmp_path / "shared" / "platform_runtime.py")
    _write_text(tmp_path / "autoyou_agents" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "notes_agent" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "notes_agent" / "website" / "manifest.json", "{}\n")
    _write_text(tmp_path / "autoyou_agents" / "simple_agent" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "simple_agent" / "prompt.py")
    _write_text(tmp_path / "autoyou_agents" / "simple_agent" / "website" / "manifest.json", "{}\n")

    plan = runtime_builder.build_runtime_module_plan(tmp_path)
    compiled_paths = {spec.source_relative_path.as_posix() for spec in plan.compile_specs}
    asset_paths = {path.as_posix() for path in plan.asset_files}

    assert "autoyou_agents/notes_agent/agent.py" in compiled_paths
    assert "autoyou_agents/notes_agent/website/manifest.json" in asset_paths
    assert "autoyou_agents/simple_agent/agent.py" not in compiled_paths
    assert "autoyou_agents/simple_agent/prompt.py" not in compiled_paths
    assert "autoyou_agents/simple_agent/website/manifest.json" not in asset_paths


def test_runtime_module_plan_includes_mac_security_agent(tmp_path):
    """mac_security_agent is no longer a private package, so it must ship.

    The shipped-agent allowlist is derived from BUILTIN_AGENT_PACKAGE_NAMES,
    which is DEFAULT_AGENT_INSTALL_STATES minus PRIVATE_AGENT_PACKAGE_NAMES.
    With no private agents, a declared agent that is installable at runtime
    must also have its sources and assets in the packaged build -- otherwise
    the runtime would advertise an agent whose files were never compiled in.
    """
    _populate_required_runtime_sources(tmp_path)
    _write_text(tmp_path / "shared" / "platform_runtime.py")
    _write_text(tmp_path / "autoyou_agents" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "mac_security_agent" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "mac_security_agent" / "website" / "manifest.json", "{}\n")
    _write_text(tmp_path / "autoyou_agents" / "mac_security_agent" / "website" / "frontend" / "index.html", "<html></html>\n")

    plan = runtime_builder.build_runtime_module_plan(tmp_path)
    compiled_paths = {spec.source_relative_path.as_posix() for spec in plan.compile_specs}
    asset_paths = {path.as_posix() for path in plan.asset_files}

    assert "autoyou_agents/mac_security_agent/agent.py" in compiled_paths
    assert "autoyou_agents/mac_security_agent/website/manifest.json" in asset_paths
    assert "autoyou_agents/mac_security_agent/website/frontend/index.html" in asset_paths


def test_runtime_module_plan_includes_claude_cli_agent(tmp_path):
    """Regression: claude_cli_agent was absent from DEFAULT_AGENT_INSTALL_STATES,
    so it fell out of BUILTIN_AGENT_PACKAGE_NAMES and its sources were pruned
    from the packaged build -- while autoyou_agents/agent.py still imported it
    statically. Declaring it fixes both halves."""
    _populate_required_runtime_sources(tmp_path)
    _write_text(tmp_path / "shared" / "platform_runtime.py")
    _write_text(tmp_path / "autoyou_agents" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "claude_cli_agent" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "claude_cli_agent" / "prompt.py")

    plan = runtime_builder.build_runtime_module_plan(tmp_path)
    compiled_paths = {spec.source_relative_path.as_posix() for spec in plan.compile_specs}

    assert "autoyou_agents/claude_cli_agent/agent.py" in compiled_paths
    assert "autoyou_agents/claude_cli_agent/prompt.py" in compiled_paths


def test_runtime_module_plan_includes_voice_training_agent_runtime(tmp_path):
    _populate_required_runtime_sources(tmp_path)
    _write_text(tmp_path / "shared" / "platform_runtime.py")
    _write_text(tmp_path / "shared" / "custom_voice_tts.py")
    _write_text(tmp_path / "autoyou_agents" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "voice_training_agent" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "voice_training_agent" / "train_model.py")
    _write_text(tmp_path / "autoyou_agents" / "voice_training_agent" / "website" / "backend" / "app.py")
    _write_text(tmp_path / "autoyou_agents" / "voice_training_agent" / "website" / "index.html", "<html></html>\n")
    _write_text(tmp_path / "autoyou_agents" / "voice_training_agent" / "website" / "manifest.json", "{}\n")

    plan = runtime_builder.build_runtime_module_plan(tmp_path)
    compiled_paths = {spec.source_relative_path.as_posix() for spec in plan.compile_specs}
    asset_paths = {path.as_posix() for path in plan.asset_files}

    assert "shared/custom_voice_tts.py" in compiled_paths
    assert "autoyou_agents/voice_training_agent/agent.py" in compiled_paths
    assert "autoyou_agents/voice_training_agent/train_model.py" in compiled_paths
    assert "autoyou_agents/voice_training_agent/website/backend/app.py" in compiled_paths
    assert "autoyou_agents/voice_training_agent/website/index.html" in asset_paths
    assert "autoyou_agents/voice_training_agent/website/manifest.json" in asset_paths


def test_runtime_module_plan_includes_fine_tuning_agent_runtime_and_dump_worker(tmp_path):
    _populate_required_runtime_sources(tmp_path)
    _write_text(tmp_path / "shared" / "platform_runtime.py")
    _write_text(tmp_path / "autoyou_agents" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "fine_tuning_agent" / "agent.py")
    _write_text(tmp_path / "autoyou_agents" / "fine_tuning_agent" / "fine_tuning_tool.py")
    _write_text(tmp_path / "autoyou_agents" / "fine_tuning_agent" / "training_data.py")
    _write_text(tmp_path / "autoyou_agents" / "fine_tuning_agent" / "training_runner.py")
    _write_text(
        tmp_path / "autoyou_agents" / "fine_tuning_agent" / "whatsapp_history_dump.mjs",
        "console.log('synthetic dump worker');\n",
    )
    _write_text(tmp_path / "autoyou_agents" / "fine_tuning_agent" / "website" / "backend" / "app.py")
    _write_text(tmp_path / "autoyou_agents" / "fine_tuning_agent" / "website" / "manifest.json", "{}\n")
    _write_text(
        tmp_path / "autoyou_agents" / "fine_tuning_agent" / "website" / "frontend" / "index.html",
        "<html></html>\n",
    )
    _write_text(
        tmp_path / "autoyou_agents" / "fine_tuning_agent" / "workspace" / "runtime.log",
        "ignored\n",
    )
    _write_text(
        tmp_path / "autoyou_agents" / "fine_tuning_agent" / "workspace" / "tools" / "convert_lora_to_gguf.py",
        "raise RuntimeError('generated runtime tool must not be compiled')\n",
    )

    plan = runtime_builder.build_runtime_module_plan(tmp_path)
    compiled_paths = {spec.source_relative_path.as_posix() for spec in plan.compile_specs}
    asset_paths = {path.as_posix() for path in plan.asset_files}

    assert "autoyou_agents/fine_tuning_agent/agent.py" in compiled_paths
    assert "autoyou_agents/fine_tuning_agent/fine_tuning_tool.py" in compiled_paths
    assert "autoyou_agents/fine_tuning_agent/training_data.py" in compiled_paths
    assert "autoyou_agents/fine_tuning_agent/training_runner.py" in compiled_paths
    assert "autoyou_agents/fine_tuning_agent/website/backend/app.py" in compiled_paths
    assert "autoyou_agents/fine_tuning_agent/whatsapp_history_dump.mjs" in asset_paths
    assert "autoyou_agents/fine_tuning_agent/website/manifest.json" in asset_paths
    assert "autoyou_agents/fine_tuning_agent/website/frontend/index.html" in asset_paths
    assert "autoyou_agents/fine_tuning_agent/workspace/tools/convert_lora_to_gguf.py" not in compiled_paths
    assert "autoyou_agents/fine_tuning_agent/workspace/runtime.log" not in asset_paths


def test_matches_compiled_module_filename_supports_platform_extension_suffixes(monkeypatch):
    monkeypatch.setattr(
        runtime_builder.importlib.machinery,
        "EXTENSION_SUFFIXES",
        [".cpython-312-darwin.so", ".cp312-win_amd64.pyd"],
    )

    assert runtime_builder._matches_compiled_module_filename(
        Path("server.cpython-312-darwin.so"),
        "server",
    )
    assert runtime_builder._matches_compiled_module_filename(
        Path("server.cp312-win_amd64.pyd"),
        "server",
    )
    assert runtime_builder._matches_compiled_module_filename(
        Path("shared/platform_runtime.cpython-312-darwin.so"),
        "platform_runtime",
    )
    assert not runtime_builder._matches_compiled_module_filename(
        Path("server.py"),
        "server",
    )


def test_looks_like_nuitka_memory_failure_matches_memoryerror_output():
    assert runtime_builder._looks_like_nuitka_memory_failure("Traceback...\nMemoryError\n")
    assert runtime_builder._looks_like_nuitka_memory_failure("clang-cl: error: out of memory")
    assert not runtime_builder._looks_like_nuitka_memory_failure("normal failure")


def test_run_nuitka_module_build_retries_with_lower_job_count_after_memory_error(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    build_root = tmp_path / "build"
    output_root = tmp_path / "output"
    spec = runtime_builder.ModuleBuildSpec(Path("autoyou_agents/notes_agent/agent.py"))
    _write_text(repo_root / spec.source_relative_path, "VALUE = 1\n")

    per_module_build_root = build_root / spec.source_relative_path.parent / spec.source_stem
    commands = []

    class _CompletedProcess:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, cwd=None, capture_output=False, text=False, errors=None):
        commands.append(
            {
                "command": command,
                "cwd": cwd,
                "capture_output": capture_output,
                "text": text,
                "errors": errors,
            }
        )
        if len(commands) == 1:
            return _CompletedProcess(1, stderr="Traceback...\nMemoryError\n")
        compiled_path = per_module_build_root / "agent.cp312-win_amd64.pyd"
        compiled_path.parent.mkdir(parents=True, exist_ok=True)
        compiled_path.write_bytes(b"compiled-module")
        return _CompletedProcess(0)

    monkeypatch.setattr(runtime_builder.subprocess, "run", fake_run)
    monkeypatch.setattr(
        runtime_builder,
        "_matches_compiled_module_filename",
        lambda candidate, stem: candidate.name.endswith(".pyd") and candidate.stem.startswith(stem),
    )

    built_path = runtime_builder._run_nuitka_module_build(
        repo_root=repo_root,
        build_root=build_root,
        output_root=output_root,
        spec=spec,
        job_count=8,
        extra_nuitka_args=("--clang",),
    )

    assert [arg for entry in commands for arg in entry["command"] if str(arg).startswith("--jobs=")] == [
        "--jobs=8",
        "--jobs=4",
    ]
    assert all(entry["capture_output"] is True for entry in commands)
    assert all(entry["text"] is True for entry in commands)
    assert built_path == output_root / spec.destination_relative_dir / "agent.cp312-win_amd64.pyd"
    assert built_path.read_bytes() == b"compiled-module"


def test_parse_args_accepts_split_nuitka_arg_tokens():
    args = runtime_builder.parse_args(
        [
            "--repo-root",
            "repo",
            "--bundle-root",
            "bundle",
            "--build-root",
            "build",
            "--nuitka-arg",
            "--clang",
            "--nuitka-arg",
            "--lto=no",
        ]
    )

    assert args.repo_root == "repo"
    assert args.bundle_root == "bundle"
    assert args.build_root == "build"
    assert args.nuitka_arg == ["--clang", "--lto=no"]


def test_parse_args_accepts_equals_style_nuitka_arg_tokens():
    args = runtime_builder.parse_args(
        [
            "--repo-root=repo",
            "--bundle-root=bundle",
            "--build-root=build",
            "--nuitka-arg=--clang",
            "--nuitka-arg=--show-progress",
        ]
    )

    assert args.nuitka_arg == ["--clang", "--show-progress"]
