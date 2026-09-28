# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path
from typing import Iterable, Optional


_AUTOYOU_AGENTS_PACKAGE = "autoyou_agents"
_AUTOYOU_SHARED_TOOLS_PACKAGE = "autoyou_agents.shared_tools"


def _iter_package_paths(package_name: str) -> Iterable[Path]:
    package = sys.modules.get(package_name)
    for raw_path in tuple(getattr(package, "__path__", ()) or ()):
        try:
            yield Path(raw_path).resolve()
        except Exception:
            continue


def _iter_candidate_shared_tools_dirs(anchor: str | Path) -> Iterable[Path]:
    seen: set[Path] = set()

    def _add(candidate: Path) -> Iterable[Path]:
        resolved = candidate.resolve()
        if resolved in seen or not resolved.is_dir():
            return ()
        seen.add(resolved)
        return (resolved,)

    for package_path in _iter_package_paths(_AUTOYOU_SHARED_TOOLS_PACKAGE):
        yield from _add(package_path)

    for agents_root in _iter_package_paths(_AUTOYOU_AGENTS_PACKAGE):
        yield from _add(agents_root / "shared_tools")

    try:
        from shared.platform_runtime import get_embedded_agents_root

        yield from _add(get_embedded_agents_root(anchor) / "shared_tools")
    except Exception:
        pass

    for raw_path in tuple(sys.path):
        if not raw_path:
            continue
        try:
            yield from _add(Path(raw_path).resolve() / "autoyou_agents" / "shared_tools")
        except Exception:
            continue


def _find_shared_tools_module_path(module_name: str, *, anchor: str | Path) -> Optional[Path]:
    stem = module_name.rsplit(".", 1)[-1]
    for shared_tools_dir in _iter_candidate_shared_tools_dirs(anchor):
        for suffix in importlib.machinery.EXTENSION_SUFFIXES:
            candidate = shared_tools_dir / f"{stem}{suffix}"
            if candidate.is_file():
                return candidate.resolve()
        source_candidate = shared_tools_dir / f"{stem}.py"
        if source_candidate.is_file():
            return source_candidate.resolve()
    return None


def _should_prefer_direct_module_load(module_path: Path) -> bool:
    if any(module_path.name.endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES):
        return True
    return any(part in {"runtime_modules", "runtime_source"} for part in module_path.parts)


def _module_loaded_from_path(module: object, module_path: Path) -> bool:
    try:
        loaded_path = Path(str(getattr(module, "__file__", ""))).resolve()
    except Exception:
        return False
    return loaded_path == module_path


def _ensure_package(package_name: str, package_dir: Path) -> None:
    package = sys.modules.get(package_name)
    if package is None:
        package = types.ModuleType(package_name)
        package.__file__ = str(package_dir)
        package.__package__ = package_name
        package.__path__ = [str(package_dir)]
        sys.modules[package_name] = package
        return

    package_paths = list(getattr(package, "__path__", ()) or ())
    package_path_text = str(package_dir)
    if package_path_text not in package_paths:
        package_paths.append(package_path_text)
        package.__path__ = package_paths


def _should_retry_shared_tools_import(module_name: str, error: ImportError) -> bool:
    if not isinstance(error, ModuleNotFoundError):
        return True

    missing_name = str(getattr(error, "name", "") or "").strip()
    if not missing_name:
        return True
    if missing_name in {_AUTOYOU_AGENTS_PACKAGE, _AUTOYOU_SHARED_TOOLS_PACKAGE, module_name}:
        return True
    return missing_name.startswith(f"{_AUTOYOU_SHARED_TOOLS_PACKAGE}.")


def _load_shared_tools_module_from_path(module_name: str, module_path: Path) -> object:
    agents_root = module_path.parents[1]
    shared_tools_root = module_path.parent
    _ensure_package(_AUTOYOU_AGENTS_PACKAGE, agents_root)
    _ensure_package(_AUTOYOU_SHARED_TOOLS_PACKAGE, shared_tools_root)

    module_spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"Could not create module spec for {module_name!r} at {module_path}")

    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    try:
        module_spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def import_autoyou_shared_tools_module(module_name: str, *, anchor: str | Path) -> object:
    """Import a shared_tools module from source or packaged runtime modules."""
    if not module_name.startswith(f"{_AUTOYOU_SHARED_TOOLS_PACKAGE}."):
        raise ValueError(f"Expected an autoyou shared_tools module, got {module_name!r}")

    module_path = _find_shared_tools_module_path(module_name, anchor=anchor)
    prefer_direct_load = module_path is not None and _should_prefer_direct_module_load(module_path)

    existing = sys.modules.get(module_name)
    if existing is not None:
        if prefer_direct_load and module_path is not None and not _module_loaded_from_path(existing, module_path):
            sys.modules.pop(module_name, None)
            return _load_shared_tools_module_from_path(module_name, module_path)
        return existing

    if prefer_direct_load and module_path is not None:
        return _load_shared_tools_module_from_path(module_name, module_path)

    import_error: Optional[ImportError] = None
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        if not _should_retry_shared_tools_import(module_name, exc):
            raise
        import_error = exc

    if module_path is None:
        if import_error is not None:
            raise import_error
        raise ModuleNotFoundError(f"Could not resolve {module_name!r}")

    return _load_shared_tools_module_from_path(module_name, module_path)
