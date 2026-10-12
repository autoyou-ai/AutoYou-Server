# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

import importlib
import importlib.util
import sys
import types

import shared.runtime_module_loader as runtime_module_loader


def _restore_modules(originals: dict[str, object | None]) -> None:
    for module_name, module in originals.items():
        if module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = module


def test_import_autoyou_shared_tools_module_falls_back_to_source_file(monkeypatch, tmp_path):
    module_name = "autoyou_agents.shared_tools.test_runtime_loader_source_fallback"
    originals = {
        name: sys.modules.get(name)
        for name in (
            "autoyou_agents",
            "autoyou_agents.shared_tools",
            module_name,
        )
    }
    for name in originals:
        sys.modules.pop(name, None)

    shared_tools_root = tmp_path / "autoyou_agents" / "shared_tools"
    shared_tools_root.mkdir(parents=True)
    module_path = shared_tools_root / "test_runtime_loader_source_fallback.py"
    module_path.write_text("VALUE = 7\n", encoding="utf-8")

    monkeypatch.setattr(
        runtime_module_loader.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name=name)),
    )
    monkeypatch.setattr(
        runtime_module_loader,
        "_iter_candidate_shared_tools_dirs",
        lambda anchor: (shared_tools_root,),
    )

    try:
        module = runtime_module_loader.import_autoyou_shared_tools_module(module_name, anchor=module_path)
        assert module.VALUE == 7
        assert module.__file__ == str(module_path)
        assert str(shared_tools_root.parent) in list(sys.modules["autoyou_agents"].__path__)
        assert str(shared_tools_root) in list(sys.modules["autoyou_agents.shared_tools"].__path__)
    finally:
        _restore_modules(originals)


def test_import_autoyou_shared_tools_module_prefers_compiled_suffix_fallback(monkeypatch, tmp_path):
    module_name = "autoyou_agents.shared_tools.test_runtime_loader_compiled_fallback"
    originals = {
        name: sys.modules.get(name)
        for name in (
            "autoyou_agents",
            "autoyou_agents.shared_tools",
            module_name,
        )
    }
    for name in originals:
        sys.modules.pop(name, None)

    shared_tools_root = tmp_path / "autoyou_agents" / "shared_tools"
    shared_tools_root.mkdir(parents=True)
    compiled_path = shared_tools_root / "test_runtime_loader_compiled_fallback.compiled"
    compiled_path.write_bytes(b"compiled")
    (shared_tools_root / "test_runtime_loader_compiled_fallback.py").write_text("VALUE = 'source'\n", encoding="utf-8")

    monkeypatch.setattr(
        runtime_module_loader.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name=name)),
    )
    monkeypatch.setattr(
        runtime_module_loader,
        "_iter_candidate_shared_tools_dirs",
        lambda anchor: (shared_tools_root,),
    )
    monkeypatch.setattr(
        runtime_module_loader.importlib.machinery,
        "EXTENSION_SUFFIXES",
        [".compiled"],
    )

    original_spec_from_file_location = runtime_module_loader.importlib.util.spec_from_file_location

    class _CompiledLoader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.LOADED_FROM = "compiled"
            module.__file__ = str(compiled_path)

    def _fake_spec_from_file_location(name: str, location: str, *args, **kwargs):
        if str(location).endswith(".compiled"):
            return importlib.util.spec_from_loader(name, _CompiledLoader(), origin=location)
        return original_spec_from_file_location(name, location, *args, **kwargs)

    monkeypatch.setattr(
        runtime_module_loader.importlib.util,
        "spec_from_file_location",
        _fake_spec_from_file_location,
    )

    try:
        module = runtime_module_loader.import_autoyou_shared_tools_module(module_name, anchor=compiled_path)
        assert module.LOADED_FROM == "compiled"
        assert module.__file__ == str(compiled_path)
    finally:
        _restore_modules(originals)


def test_import_autoyou_shared_tools_module_prefers_runtime_modules_copy(monkeypatch, tmp_path):
    module_name = "autoyou_agents.shared_tools.test_runtime_loader_packaged_copy"
    originals = {
        name: sys.modules.get(name)
        for name in (
            "autoyou_agents",
            "autoyou_agents.shared_tools",
            module_name,
        )
    }
    for name in originals:
        sys.modules.pop(name, None)

    shared_tools_root = tmp_path / "runtime_modules" / "autoyou_agents" / "shared_tools"
    shared_tools_root.mkdir(parents=True)
    module_path = shared_tools_root / "test_runtime_loader_packaged_copy.py"
    module_path.write_text("VALUE = 'packaged'\n", encoding="utf-8")

    stale_module = types.ModuleType(module_name)
    stale_module.__file__ = str(tmp_path / "autoyou_agents" / "shared_tools" / "test_runtime_loader_packaged_copy.py")
    stale_module.VALUE = "stale"
    sys.modules[module_name] = stale_module

    monkeypatch.setattr(
        runtime_module_loader,
        "_iter_candidate_shared_tools_dirs",
        lambda anchor: (shared_tools_root,),
    )

    try:
        module = runtime_module_loader.import_autoyou_shared_tools_module(module_name, anchor=module_path)
        assert module is not stale_module
        assert module.VALUE == "packaged"
        assert module.__file__ == str(module_path)
    finally:
        _restore_modules(originals)


def test_managed_frontend_backends_import_from_source_tree() -> None:
    for module_name in (
        "autoyou_agents.notify_agent.website.backend.app",
        "autoyou_agents.tasks_agent.website.backend.app",
        "autoyou_agents.website_agent.website.backend.app",
    ):
        module = importlib.import_module(module_name)
        assert getattr(module, "app", None) is not None
