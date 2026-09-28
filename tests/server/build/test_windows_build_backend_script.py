# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from pathlib import Path

from tests.support.paths import REPO_ROOT


BUILD_SCRIPT = REPO_ROOT / "servers" / "windows" / "build-backend.ps1"


def test_windows_build_backend_verifies_managed_frontend_runtime_imports():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "Assert-PackagedBackendServerImports" in text
    assert "--verify-runtime-import" in text
    assert "autoyou_agents.agent_builder_agent.website.backend.app" in text
    assert "autoyou_agents.notify_agent.website.backend.app" in text
    assert "autoyou_agents.remote_desktop_agent.website.backend.app" in text
    assert "autoyou_agents.skills_agent.website.backend.app" in text
    assert "autoyou_agents.tasks_agent.website.backend.app" in text
    assert "autoyou_agents.voice_training_agent.website.backend.app" in text
    assert "autoyou_agents.website_agent.website.backend.app" in text


def test_windows_release_profiles_support_the_training_stack():
    build_all = (REPO_ROOT / "servers" / "windows" / "build-all.ps1").read_text(encoding="utf-8")
    publish = (REPO_ROOT / "servers" / "windows" / "publish-desktop.ps1").read_text(encoding="utf-8")
    binary_default = (REPO_ROOT / "requirements" / "binary-default.txt").read_text(encoding="utf-8")
    full = (REPO_ROOT / "requirements" / "full.txt").read_text(encoding="utf-8")

    assert '[ValidateSet("binary-default", "connector-full", "training-full")]' in build_all
    assert 'Requirements = "full"' in publish
    assert 'Requirements = "training-full"' in publish
    assert 'Requirements = "binary-default"' in publish
    assert "-r voice.txt" not in binary_default
    assert "-r voice.txt" in full


def test_windows_backend_build_installs_tunnelmole_node_service_dependencies():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert '@("whatsapp", "tunnelmole")' in text
    assert "node_modules present for service '$svc'" in text


def test_windows_backend_build_reconciles_reused_dependency_drift_before_pip_check():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "scripts\\reconcile_python_runtime_env.py" in text
    assert text.index("Invoke-CheckedCommand -FilePath $pythonExe -Arguments $reconcileArguments") < text.index(
        "Assert-PipDependencyConsistency -PythonExe $pythonExe"
    )
