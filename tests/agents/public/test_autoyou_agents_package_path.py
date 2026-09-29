# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-b23b9e5a7374d39a03fefc70


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import importlib

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


def test_sibling_agents_package_extends_embedded_copy(tmp_path, monkeypatch):
    package_root = tmp_path / "AutoYou-Server" / "autoyou_agents"
    package_root.mkdir(parents=True)
    sibling_root = tmp_path / "autoyou_agents"
    sibling_root.mkdir()
    (sibling_root / "__init__.py").write_text("", encoding="utf-8")
    private_root = sibling_root / "private"
    private_root.mkdir()

    monkeypatch.setattr(autoyou_agents, "__file__", str(package_root / "__init__.py"))
    monkeypatch.setattr(autoyou_agents, "__path__", [str(package_root)])
    monkeypatch.setattr(platform_runtime, "is_compiled", lambda: False)
    monkeypatch.setattr(platform_runtime, "get_dynamic_agents_root", lambda *args, **kwargs: package_root)

    autoyou_agents._extend_package_path_for_runtime_agents()

    assert list(autoyou_agents.__path__) == [str(package_root), str(sibling_root.resolve()), str(private_root.resolve())]
