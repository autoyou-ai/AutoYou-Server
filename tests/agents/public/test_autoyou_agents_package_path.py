# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-b23b9e5a7374d39a03fefc70


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import importlib
from pathlib import Path
import subprocess
import sys

import pytest

import autoyou_agents
import shared.platform_runtime as platform_runtime

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-b23b9e5a7374d39a03fefc70"


def test_runtime_plugin_root_is_added_to_autoyou_agents_package_path(tmp_path, monkeypatch):
    dynamic_root = tmp_path / "dynamic_agents"
    dynamic_root.mkdir()

    plugin_dir = dynamic_root / "custom_agent"
    # from __debug_provenance_n__ import license
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "prompt.py").write_text(
        "AGENT_DESCRIPTION = 'Runtime-loaded custom agent.'\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(platform_runtime, "get_dynamic_agents_root", lambda *args, **kwargs: dynamic_root)

    importlib.reload(autoyou_agents)
    module = importlib.import_module("autoyou_agents.custom_agent.prompt")

    assert module.AGENT_DESCRIPTION == "Runtime-loaded custom agent."
    assert str(dynamic_root) in list(autoyou_agents.__path__)


def test_private_folder_extends_the_package_and_nothing_beside_the_checkout_does(tmp_path, monkeypatch):
    package_root = tmp_path / "AutoYou-Server" / "autoyou_agents"
    package_root.mkdir(parents=True)
    # An agents package beside the checkout is somebody else's tree, not ours.
    beside = tmp_path / "autoyou_agents"
    (beside / "private").mkdir(parents=True)
    (beside / "__init__.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(autoyou_agents, "__file__", str(package_root / "__init__.py"))
    monkeypatch.setattr(autoyou_agents, "__path__", [str(package_root)])
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(platform_runtime, "get_dynamic_agents_root", lambda *args, **kwargs: package_root)

    autoyou_agents._extend_package_path_for_runtime_agents()

    # private/ is listed before it exists, so an agent copied in later still imports.
    assert list(autoyou_agents.__path__) == [str(package_root), str((package_root / "private").resolve())]


def test_an_agent_copied_into_private_imports_like_a_builtin(tmp_path, monkeypatch):
    package_root = tmp_path / "AutoYou-Server" / "autoyou_agents"
    agent_dir = package_root / "private" / "lantern_dropin_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "__init__.py").write_text("", encoding="utf-8")
    (agent_dir / "prompt.py").write_text("AGENT_DESCRIPTION = 'Copied in.'\n", encoding="utf-8")

    monkeypatch.setattr(autoyou_agents, "__file__", str(package_root / "__init__.py"))
    monkeypatch.setattr(autoyou_agents, "__path__", [*autoyou_agents.__path__])
    monkeypatch.setattr(platform_runtime, "get_dynamic_agents_root", lambda *args, **kwargs: tmp_path / "dynamic")
    autoyou_agents._extend_package_path_for_runtime_agents()
    try:
        importlib.invalidate_caches()
        module = importlib.import_module("autoyou_agents.lantern_dropin_agent.prompt")
        assert module.AGENT_DESCRIPTION == "Copied in."
    finally:
        for name in [name for name in sys.modules if name.startswith("autoyou_agents.lantern_dropin_agent")]:
            sys.modules.pop(name, None)


def test_private_agents_folder_is_never_committed():
    repo_root = Path(autoyou_agents.__file__).resolve().parents[1]
    if not (repo_root / ".git").exists():
        pytest.skip("not a git checkout")
    for path in ("autoyou_agents/private", "autoyou_agents/private/lantern_agent/agent.py"):
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", path], cwd=repo_root, check=False
        )
        assert result.returncode == 0, f"{path} is not git-ignored"


def test_a_website_copied_into_private_is_listed_with_the_others(tmp_path):
    from autoyou_agents.shared_tools.frontend_manifest import discover_frontend_manifests

    agents_root = tmp_path / "autoyou_agents"
    website = agents_root / "private" / "lantern_site_agent" / "website"
    website.mkdir(parents=True)
    (website / "manifest.json").write_text(
        '{"agent_name": "lantern_site_agent", "title": "Lantern Site"}', encoding="utf-8"
    )

    entries = discover_frontend_manifests(agents_root=agents_root)

    assert [entry["agent_name"] for entry in entries] == ["lantern_site_agent"]
