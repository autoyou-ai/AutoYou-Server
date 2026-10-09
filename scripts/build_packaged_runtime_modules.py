# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-daa52a0438c1857ed7270709

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import ast
import hashlib
import importlib.machinery
import json
import os
import py_compile
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Mapping

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-daa52a0438c1857ed7270709"


# Ensure repo root is on sys.path so autoyou_agents is importable when this
# script is run from a venv that doesn't have the project installed (e.g. .venv-build312).
SERVER_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_ROOT))

from autoyou_agents.shared_tools.agent_install_registry import BUILTIN_AGENT_PACKAGE_NAMES


RUNTIME_MODULES_DIRNAME = "runtime_modules"
RUNTIME_INTEGRITY_MANIFEST = "runtime_integrity.json"
SIBLING_AGENT_MANIFEST = "packaged_sibling_agents.json"
#: A JSON file naming modules from another source tree to compile into this
#: bundle (see ``load_extra_sources``). Builds of this repository alone need none.
EXTRA_SOURCES_ENV = "AUTOYOU_EXTRA_SOURCES_MANIFEST"
FORBIDDEN_RUNTIME_PATHS = ("runtime_source",)
TOP_LEVEL_RUNTIME_MODULES = (
    "server.py",
    "rest_api.py",
    "pairing_router.py",
    "ollama_service.py",
    "autoyou_page_service.py",
    "service_manager.py",
    "session_utils.py",
    "page_feed_db.py",
    "signal_service.py",
    "telegram_user_service.py",
    "whatsapp_service.py",
)
PACKAGE_RUNTIME_ROOTS = (
    Path("shared"),
    Path("autoyou_agents"),
    Path("core_server"),
    Path("routers"),
)
STATIC_RUNTIME_FILES = (
    Path("shared") / "tunnelmole_node_launcher.mjs",
    Path("autoyou_agents") / "shared_tools" / "desktop_asset_schema.json",
)
STATIC_RUNTIME_DIRECTORIES = (
    Path("shared") / "native" / "libsodium",
)
EMOTIVOICE_RUNTIME_MODULES = tuple(Path(path) for path in (
    "vendor/emotivoice/config/joint/config.py",
    "vendor/emotivoice/frontend.py",
    "vendor/emotivoice/frontend_cn.py",
    "vendor/emotivoice/frontend_en.py",
    "vendor/emotivoice/models/hifigan/get_random_segments.py",
    "vendor/emotivoice/models/hifigan/models.py",
    "vendor/emotivoice/models/prompt_tts_modified/jets.py",
    "vendor/emotivoice/models/prompt_tts_modified/model_open_source.py",
    "vendor/emotivoice/models/prompt_tts_modified/modules/alignment.py",
    "vendor/emotivoice/models/prompt_tts_modified/modules/encoder.py",
    "vendor/emotivoice/models/prompt_tts_modified/modules/initialize.py",
    "vendor/emotivoice/models/prompt_tts_modified/modules/variance.py",
    "vendor/emotivoice/models/prompt_tts_modified/simbert.py",
))
EMOTIVOICE_RUNTIME_FILES = tuple(Path(path) for path in (
    "vendor/emotivoice/LICENSE",
    "vendor/emotivoice/config/joint/config.yaml",
    "vendor/emotivoice/data/youdao/text/emotion",
    "vendor/emotivoice/data/youdao/text/energy",
    "vendor/emotivoice/data/youdao/text/pitch",
    "vendor/emotivoice/data/youdao/text/speaker2",
    "vendor/emotivoice/data/youdao/text/speed",
    "vendor/emotivoice/data/youdao/text/tokenlist",
    "vendor/emotivoice/lexicon/librispeech-lexicon.txt",
))
AGENT_SIDECAR_FILENAMES = frozenset(
    {
        "whatsapp_history_dump.mjs",
    }
)
# Agent instruction files are not executable Python and are never compiled.
# Preserve them as data only for trusted built-in agents that explicitly ship
# one. Server builds may explicitly add sibling agents to the same bundle.
AGENT_CONTEXT_FILENAMES = frozenset({"AGENT.md", "AGENTS.md"})
ASSET_DIRECTORY_NAMES = frozenset(
    {
        "website",
        "desktop_assets",
        "boilerplate",
        "scheduler_mission_control_frontend",
        "worker",
    }
)
SKIP_DIRECTORY_NAMES = frozenset(
    {
        "__pycache__",
        ".git",
        ".pytest_cache",
        ".adk",
        ".venv",
        "build",
        "chrome_profile",
        "dist",
        "media",
        "node_modules",
        "autoyou_notes_agent",
        "workspace",
        "tests",
        "test",
    }
)
SKIP_ASSET_SUFFIXES = frozenset(
    {
        ".py",
        ".pyc",
        ".pyo",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".db-journal",
        ".log",
        ".old",
        ".sav",
        ".pma",
        ".dat",
        ".pb",
        ".pem",
        ".key",
        ".p12",
        ".pfx",
    }
)
AUTOYOU_AGENTS_BRIDGE_STUB = """from __future__ import annotations
import logging
from typing import Any

LOGGER = logging.getLogger(__name__)
__all__ = [\"root_agent\"]


def _extend_package_path_for_runtime_agents() -> None:
    try:
        from shared.platform_runtime import get_dynamic_agents_root

        dynamic_root = str(get_dynamic_agents_root(\"AutoYou\", anchor=__file__))
        package_path = globals().get(\"__path__\", None)
        if package_path is not None and dynamic_root not in package_path:
            package_path.append(dynamic_root)
    except Exception as exc:
        LOGGER.debug(\"Could not extend autoyou_agents package path: %s\", exc)


_extend_package_path_for_runtime_agents()


def __getattr__(name: str) -> Any:
    if name != \"root_agent\":
        raise AttributeError(f\"module {__name__!r} has no attribute {name!r}\")
    try:
        from autoyou_agents.agent import root_agent as resolved_root_agent
    except Exception as exc:
        LOGGER.warning(\"autoyou_agents root_agent import failed: %s\", exc)
        resolved_root_agent = None
    return resolved_root_agent
"""
EMPTY_PACKAGE_BRIDGE_STUB = '"""Packaged runtime package marker."""\n'
BRIDGE_STUBS = {
    Path("autoyou_agents") / "__init__.py": AUTOYOU_AGENTS_BRIDGE_STUB,
}
TRUSTED_AUTOYOU_AGENT_SUBDIRS = frozenset(BUILTIN_AGENT_PACKAGE_NAMES) | frozenset({"shared_tools"})


def _source_path(repo_root: Path, relative_path: Path) -> Path:
    return repo_root / relative_path


@dataclass(frozen=True)
class AgentRoot:
    path: Path
    #: An exclusive root may not reuse a package name this repository already has.
    exclusive: bool = False


@dataclass(frozen=True)
class ExtraSources:
    """Modules from another source tree, named by whoever runs the build.

    ``modules`` and every ``*.py`` in ``packages`` are compiled at the same
    relative path inside the bundle; a package's ``__init__.py`` and each
    ``package_markers`` entry become bytecode package markers. ``agent_roots``
    hold extra ``*_agent`` packages to bundle beside this repository's agents.
    """

    root: Path
    modules: tuple[Path, ...] = ()
    packages: tuple[Path, ...] = ()
    package_markers: tuple[Path, ...] = ()
    agent_roots: tuple[AgentRoot, ...] = ()


def _manifest_relative_path(value: object, field_name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Extra sources {field_name} entries must be relative paths")
    path = Path(value.replace("\\", "/"))
    if path.is_absolute() or path.drive or ".." in path.parts or not path.parts:
        raise ValueError(f"Extra sources {field_name} entry must stay inside the source root: {value}")
    return path


def load_extra_sources(manifest_path: Path) -> ExtraSources:
    """Read an extra-sources manifest (version 1)."""
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(f"Unsupported extra sources manifest at {manifest_path}")
    root = Path(str(data.get("root") or ""))
    if not root.is_absolute() or not root.is_dir():
        raise ValueError(f"Extra sources root must be an existing absolute directory: {root}")

    def relative_list(field_name: str) -> tuple[Path, ...]:
        values = data.get(field_name, [])
        if not isinstance(values, list):
            raise ValueError(f"Extra sources {field_name} must be a list")
        return tuple(_manifest_relative_path(value, field_name) for value in values)

    agent_roots = []
    for entry in data.get("agent_roots", []) or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("Extra sources agent_roots entries need a path")
        agent_path = Path(entry["path"])
        agent_roots.append(AgentRoot(agent_path if agent_path.is_absolute() else root / agent_path,
                                     exclusive=entry.get("exclusive") is True))
    return ExtraSources(
        root=root.resolve(),
        modules=relative_list("modules"),
        packages=relative_list("packages"),
        package_markers=relative_list("package_markers"),
        agent_roots=tuple(agent_roots),
    )


@dataclass(frozen=True)
class ModuleBuildSpec:
    source_relative_path: Path

    @property
    def source_stem(self) -> str:
        return self.source_relative_path.stem

    @property
    def destination_relative_dir(self) -> Path:
        return self.source_relative_path.parent


@dataclass(frozen=True)
class RuntimeModulePlan:
    compile_specs: tuple[ModuleBuildSpec, ...]
    asset_files: tuple[Path, ...]
    static_files: tuple[Path, ...]
    bridge_stubs: tuple[Path, ...]
    source_overrides: Mapping[Path, Path] = field(default_factory=dict)
    sibling_agent_names: tuple[str, ...] = ()
    #: Whether agents from another tree were bundled (recorded for the runtime).
    agent_overlay: bool = False


def _is_skipped_path(relative_path: Path) -> bool:
    return any(part in SKIP_DIRECTORY_NAMES for part in relative_path.parts)


def _is_trusted_runtime_source(relative_path: Path) -> bool:
    if not relative_path.parts:
        return False
    if relative_path.parts[0] != "autoyou_agents":
        return True
    if len(relative_path.parts) == 2:
        return True
    return relative_path.parts[1] in TRUSTED_AUTOYOU_AGENT_SUBDIRS


def _iter_python_module_sources(repo_root: Path) -> Iterator[Path]:
    for relative_path_text in TOP_LEVEL_RUNTIME_MODULES:
        relative_path = Path(relative_path_text)
        source_path = _source_path(repo_root, relative_path)
        if not source_path.is_file():
            raise FileNotFoundError(f"Expected runtime module at {source_path}")
        yield relative_path

    for package_root in PACKAGE_RUNTIME_ROOTS:
        source_root = _source_path(repo_root, package_root)
        if not source_root.is_dir():
            raise FileNotFoundError(f"Expected runtime package root at {source_root}")
        for source_path in sorted(source_root.rglob("*.py")):
            relative_path = package_root / source_path.relative_to(source_root)
            if _is_skipped_path(relative_path):
                continue
            if not _is_trusted_runtime_source(relative_path):
                continue
            if source_path.name == "__init__.py":
                continue
            if source_path.name.startswith("test_"):
                continue
            if relative_path in STATIC_RUNTIME_FILES:
                continue
            yield relative_path


def _is_agent_asset(relative_path: Path) -> bool:
    is_asset_directory_file = any(part in ASSET_DIRECTORY_NAMES for part in relative_path.parts)
    is_agent_sidecar = len(relative_path.parts) == 3 and relative_path.name in AGENT_SIDECAR_FILENAMES
    is_agent_context_file = len(relative_path.parts) >= 3 and relative_path.name in AGENT_CONTEXT_FILENAMES
    return (
        (is_asset_directory_file or is_agent_sidecar or is_agent_context_file)
        and relative_path.suffix.lower() not in SKIP_ASSET_SUFFIXES
        and ".test." not in relative_path.name
        and not relative_path.name.startswith(".env")
    )


def _desktop_asset_sources(agent_package_root: Path) -> set[Path] | None:
    """Return only public setup files; personal manifests and imagery stay local."""
    assets_root = agent_package_root / "desktop_assets"
    template_path = assets_root / "manifest.template.json"
    if not template_path.is_file():
        return None
    if assets_root.is_symlink():
        raise ValueError(f"Desktop asset directory must not be a symlink: {assets_root}")
    prompt_path = assets_root / "setup_prompt.md"
    for path in (template_path, prompt_path):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Desktop asset setup file is missing or unsafe: {path}")
    try:
        payload = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Invalid desktop manifest template at {template_path}: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or str(payload.get("agent_name") or "") != agent_package_root.name
        or int(payload.get("schema_version", 0)) != 2
        or not isinstance(payload.get("asset_packs"), list)
    ):
        raise ValueError(f"Desktop manifest template identity or schema is invalid: {template_path}")
    return {template_path.resolve(), prompt_path.resolve()}


def _agent_uses_shared_desktop_controls(source_path: Path) -> bool:
    """Detect desktop agents by their shared control import, not by package name."""
    try:
        module = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    except (OSError, UnicodeError, SyntaxError):
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.ImportFrom):
            if str(node.module or "").rsplit(".", 1)[-1] == "desktop_app_control":
                return True
        elif isinstance(node, ast.Import):
            if any(alias.name.rsplit(".", 1)[-1] == "desktop_app_control" for alias in node.names):
                return True
    return False


def _validate_desktop_agent_manifests(
    repo_root: Path,
    compile_specs: Iterable[ModuleBuildSpec],
    source_overrides: Mapping[Path, Path],
) -> None:
    agent_sources = {
        spec.source_relative_path
        for spec in compile_specs
        if len(spec.source_relative_path.parts) == 3
        and spec.source_relative_path.parts[0] == "autoyou_agents"
        and spec.source_relative_path.name == "agent.py"
    }
    for agent_source in sorted(agent_sources):
        source_path = source_overrides.get(agent_source, _source_path(repo_root, agent_source))
        if not _agent_uses_shared_desktop_controls(source_path):
            continue
        manifest_relative_path = agent_source.parent / "desktop_assets" / "manifest.template.json"
        manifest_source = source_overrides.get(
            manifest_relative_path,
            _source_path(repo_root, manifest_relative_path),
        )
        if not manifest_source.is_file():
            raise FileNotFoundError(
                f"Desktop agent {agent_source.parent.name} uses shared desktop controls but is missing "
                f"{manifest_relative_path.as_posix()}. Include a generic desktop_assets/manifest.template.json in the source tree."
            )


def _iter_agent_asset_files(repo_root: Path) -> Iterator[Path]:
    agent_root = _source_path(repo_root, Path("autoyou_agents"))
    desktop_asset_sources: dict[str, set[Path]] = {}
    for package_root in sorted(agent_root.iterdir()) if agent_root.is_dir() else ():
        if not package_root.is_dir() or package_root.is_symlink():
            continue
        package_agent_path = Path("autoyou_agents") / package_root.name / "agent.py"
        if not _is_trusted_runtime_source(package_agent_path):
            continue
        sources = _desktop_asset_sources(package_root)
        if sources is not None:
            desktop_asset_sources[package_root.name] = sources

    for source_path in sorted(agent_root.rglob("*")):
        if not source_path.is_file() or source_path.is_symlink():
            continue
        relative_path = Path("autoyou_agents") / source_path.relative_to(agent_root)
        if len(relative_path.parts) >= 3 and "desktop_assets" in relative_path.parts:
            package_name = relative_path.parts[1]
            allowed_sources = desktop_asset_sources.get(package_name)
            if allowed_sources is not None and source_path.resolve() not in allowed_sources:
                continue
        if not _is_skipped_path(relative_path) and _is_trusted_runtime_source(relative_path) and _is_agent_asset(relative_path):
            yield relative_path


def _sibling_agent_sources(
    repo_root: Path, existing_paths: set[Path], agent_roots: Iterable[AgentRoot],
) -> tuple[dict[Path, Path], set[str]]:
    """Overlay agent packages from the roots a manifest names, without copying live agent state."""
    overrides: dict[Path, Path] = {}
    extra_names: set[str] = set()
    server_agent_root = repo_root / "autoyou_agents"
    for agent_root in agent_roots:
        root_path = agent_root.path
        if not root_path.is_dir() or root_path.is_symlink():
            continue
        for package in sorted(root_path.iterdir()):
            if not package.is_dir() or package.is_symlink() or not package.name.endswith("_agent"):
                continue
            if _is_skipped_path(Path(package.name)):
                continue
            if not (package / "agent.py").is_file() and not (server_agent_root / package.name / "agent.py").is_file():
                continue
            if agent_root.exclusive and (server_agent_root / package.name).exists():
                raise ValueError(f"Agent package collides with Server source: {package.name}")
            included = False
            desktop_asset_sources = _desktop_asset_sources(package)
            for source_path in sorted(package.rglob("*")):
                if not source_path.is_file() or source_path.is_symlink():
                    continue
                relative_path = Path("autoyou_agents") / package.name / source_path.relative_to(package)
                if "desktop_assets" in relative_path.parts and desktop_asset_sources is not None:
                    if source_path.resolve() not in desktop_asset_sources:
                        continue
                if _is_skipped_path(relative_path) or relative_path in existing_paths or relative_path in overrides:
                    continue
                if source_path.suffix == ".py":
                    if source_path.name == "__init__.py" or source_path.name.startswith("test_"):
                        continue
                elif not _is_agent_asset(relative_path):
                    continue
                overrides[relative_path] = source_path
                included = True
            if included and package.name not in BUILTIN_AGENT_PACKAGE_NAMES:
                extra_names.add(package.name)
    return overrides, extra_names


def _iter_static_runtime_files(repo_root: Path) -> Iterator[Path]:
    for relative_path in STATIC_RUNTIME_FILES:
        source_path = repo_root / relative_path
        if not source_path.is_file():
            raise FileNotFoundError(f"Expected runtime sidecar at {source_path}")
        yield relative_path
    for relative_directory in STATIC_RUNTIME_DIRECTORIES:
        source_directory = repo_root / relative_directory
        if not source_directory.is_dir():
            raise FileNotFoundError(f"Expected runtime sidecar directory at {source_directory}")
        for source_path in sorted(source_directory.rglob("*")):
            if source_path.is_file():
                yield source_path.relative_to(repo_root)


def _extra_source_modules(
    extra: ExtraSources,
) -> tuple[list[Path], set[Path], dict[Path, Path]]:
    """Modules to compile, package markers, and where each one is read from."""
    modules: list[Path] = []
    markers: set[Path] = set(extra.package_markers)
    overrides: dict[Path, Path] = {}
    for relative in extra.modules:
        source = extra.root / relative
        if not source.is_file():
            raise FileNotFoundError(f"Missing extra source module: {relative.as_posix()}")
        modules.append(relative)
        overrides[relative] = source
    for package in extra.packages:
        package_dir = extra.root / package
        if not package_dir.is_dir():
            raise FileNotFoundError(f"Missing extra source package: {package.as_posix()}")
        for source in sorted(package_dir.glob("*.py")):
            relative = package / source.name
            overrides[relative] = source
            if source.name == "__init__.py":
                markers.add(relative)
            else:
                modules.append(relative)
    for marker in extra.package_markers:
        if (extra.root / marker).is_file():
            overrides[marker] = extra.root / marker
    return modules, markers, overrides


def build_runtime_module_plan(
    repo_root: Path, *, extra_sources: ExtraSources | None = None, include_emotivoice: bool = False,
) -> RuntimeModulePlan:
    compile_specs = tuple(ModuleBuildSpec(relative_path) for relative_path in _iter_python_module_sources(repo_root))
    asset_files = tuple(_iter_agent_asset_files(repo_root))
    static_files = tuple(_iter_static_runtime_files(repo_root))
    if include_emotivoice:
        required_paths = (*EMOTIVOICE_RUNTIME_MODULES, *EMOTIVOICE_RUNTIME_FILES)
        missing = [path for path in required_paths if not (repo_root / path).is_file()]
        if missing:
            raise FileNotFoundError(f"Missing EmotiVoice runtime files: {', '.join(path.as_posix() for path in missing)}")
        compile_specs += tuple(ModuleBuildSpec(path) for path in EMOTIVOICE_RUNTIME_MODULES)
        static_files += EMOTIVOICE_RUNTIME_FILES
    bridge_stubs = set(BRIDGE_STUBS)
    source_overrides: dict[Path, Path] = {}
    sibling_agent_names: set[str] = set()
    agent_overlay = bool(extra_sources and extra_sources.agent_roots)
    if agent_overlay:
        source_overrides, sibling_agent_names = _sibling_agent_sources(
            repo_root, {spec.source_relative_path for spec in compile_specs} | set(asset_files),
            extra_sources.agent_roots,
        )
        compile_specs += tuple(
            ModuleBuildSpec(path) for path in source_overrides if path.suffix == ".py"
        )
        asset_files += tuple(path for path in source_overrides if path.suffix != ".py")
    _validate_desktop_agent_manifests(repo_root, compile_specs, source_overrides)
    if extra_sources is not None:
        existing = {spec.source_relative_path for spec in compile_specs}
        modules, markers, module_sources = _extra_source_modules(extra_sources)
        clashes = sorted(path.as_posix() for path in modules if path in existing)
        if clashes:
            raise ValueError(f"Extra source modules collide with this repository: {', '.join(clashes)}")
        compile_specs += tuple(ModuleBuildSpec(path) for path in modules)
        bridge_stubs |= markers
        source_overrides.update(module_sources)
    extra_tops = {path.parts[0] for path in (
        *(extra_sources.modules if extra_sources else ()),
        *(extra_sources.packages if extra_sources else ()),
        *(extra_sources.package_markers if extra_sources else ()),
    )}
    for spec in compile_specs:
        for parent in spec.source_relative_path.parents:
            if parent == Path("."):
                break
            initializer = parent / "__init__.py"
            source = source_overrides.get(initializer) or _source_path(repo_root, initializer)
            if not source.is_file() and extra_sources is not None and initializer.parts[0] in extra_tops:
                source = extra_sources.root / initializer
                if source.is_file():
                    source_overrides[initializer] = source
            if source.is_file():
                bridge_stubs.add(initializer)
    return RuntimeModulePlan(
        compile_specs=tuple(sorted(compile_specs, key=lambda spec: spec.source_relative_path.as_posix())),
        asset_files=tuple(sorted(asset_files, key=lambda path: path.as_posix())),
        static_files=tuple(sorted(static_files, key=lambda path: path.as_posix())),
        bridge_stubs=tuple(sorted(bridge_stubs)),
        source_overrides=source_overrides,
        sibling_agent_names=tuple(sorted(sibling_agent_names)),
        agent_overlay=agent_overlay,
    )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_compiled_module_filename(path: Path, source_stem: str) -> bool:
    filename = path.name
    return any(filename == f"{source_stem}{suffix}" for suffix in importlib.machinery.EXTENSION_SUFFIXES)


def _looks_like_nuitka_memory_failure(output_text: str) -> bool:
    lowered = str(output_text or "").lower()
    # from __debug_provenance_s__ import btc
    return (
        "memoryerror" in lowered
        or "out of memory" in lowered
        or "allocation failed" in lowered
    )


def _run_nuitka_module_build(
    *,
    repo_root: Path,
    build_root: Path,
    output_root: Path,
    spec: ModuleBuildSpec,
    job_count: int,
    extra_nuitka_args: Iterable[str],
    source_override: Path | None = None,
) -> Path:
    per_module_build_root = build_root / spec.source_relative_path.parent / spec.source_stem
    if per_module_build_root.exists():
        shutil.rmtree(per_module_build_root)
    per_module_build_root.mkdir(parents=True, exist_ok=True)

    attempt_job_count = max(job_count, 1)
    while True:
        command = [
            sys.executable,
            "-m",
            "nuitka",
            "--module",
            "--nofollow-imports",
            f"--jobs={attempt_job_count}",
            "--lto=no",
            "--low-memory",
            "--assume-yes-for-downloads",
            "--file-reference-choice=runtime",
            f"--output-dir={per_module_build_root}",
            *extra_nuitka_args,
            str(source_override or _source_path(repo_root, spec.source_relative_path)),
        ]
        completed = subprocess.run(
            command,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            errors="replace",
        )
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        if completed.returncode == 0:
            break

        combined_output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        if attempt_job_count > 1 and _looks_like_nuitka_memory_failure(combined_output):
            next_job_count = max(attempt_job_count // 2, 1)
            if next_job_count < attempt_job_count:
                print(
                    (
                        f"[runtime-modules] Nuitka ran out of memory while compiling "
                        f"{spec.source_relative_path.as_posix()} with --jobs={attempt_job_count}; "
                        f"retrying with --jobs={next_job_count}"
                    ),
                    file=sys.stderr,
                )
                shutil.rmtree(per_module_build_root, ignore_errors=True)
                per_module_build_root.mkdir(parents=True, exist_ok=True)
                attempt_job_count = next_job_count
                continue

        raise subprocess.CalledProcessError(completed.returncode, command)

    candidates = sorted(
        candidate
        for candidate in per_module_build_root.rglob("*")
        if candidate.is_file() and _matches_compiled_module_filename(candidate, spec.source_stem)
    )
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one compiled module for {spec.source_relative_path}, found {len(candidates)}"
        )

    destination_dir = output_root / spec.destination_relative_dir
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination_path = destination_dir / candidates[0].name
    shutil.copy2(candidates[0], destination_path)
    shutil.rmtree(per_module_build_root, ignore_errors=True)
    return destination_path


def _write_bridge_stubs(
    *,
    output_root: Path,
    build_root: Path,
    plan: RuntimeModulePlan,
    repo_root: Path | None = None,
) -> tuple[Path, ...]:
    written_paths: list[Path] = []
    for relative_path in plan.bridge_stubs:
        source_path = build_root / "bridge-stubs" / relative_path
        destination_path = output_root / relative_path.with_suffix(".pyc")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        source_text = BRIDGE_STUBS.get(relative_path)
        stub_source = plan.source_overrides.get(relative_path) or (
            _source_path(repo_root, relative_path) if repo_root is not None else None
        )
        if source_text is None and stub_source is not None and stub_source.is_file():
            source_text = stub_source.read_text(encoding="utf-8")
        source_path.write_text(source_text or EMPTY_PACKAGE_BRIDGE_STUB, encoding="utf-8")
        py_compile.compile(
            str(source_path),
            cfile=str(destination_path),
            # Keep the bridge bytecode filename deterministic and independent
            # of the checkout path. Absolute build-host paths are rejected by
            # the Windows Store MSIX verifier.
            dfile=relative_path.as_posix(),
            doraise=True,
            optimize=2,
        )
        source_path.unlink()
        written_paths.append(destination_path)
    return tuple(written_paths)


def _copy_asset_files(*, repo_root: Path, output_root: Path, plan: RuntimeModulePlan) -> tuple[Path, ...]:
    copied_paths: list[Path] = []
    for relative_path in plan.asset_files:
        source_path = plan.source_overrides.get(relative_path, _source_path(repo_root, relative_path))
        destination_path = output_root / relative_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)
        copied_paths.append(destination_path)
    return tuple(copied_paths)


def _copy_static_runtime_files(*, repo_root: Path, output_root: Path, plan: RuntimeModulePlan) -> tuple[Path, ...]:
    copied_paths: list[Path] = []
    for relative_path in plan.static_files:
        source_path = repo_root / relative_path
        destination_path = output_root / relative_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)
        copied_paths.append(destination_path)
    return tuple(copied_paths)


def _build_integrity_manifest(bundle_root: Path, tracked_root: Path, plan: RuntimeModulePlan) -> dict[str, object]:
    tracked_files: dict[str, str] = {}
    for file_path in sorted(tracked_root.rglob("*")):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(bundle_root).as_posix()
        tracked_files[relative_path] = _hash_file(file_path)

    allowed_python_files_set = {
        relative_path.as_posix()
        for relative_path in plan.static_files
        if relative_path.suffix == ".py"
    }
    allowed_python_files = sorted(allowed_python_files_set)
    allowed_python_files = [f"{RUNTIME_MODULES_DIRNAME}/{path}" for path in allowed_python_files]

    return {
        "version": 1,
        "algorithm": "sha256",
        "tracked_roots": [RUNTIME_MODULES_DIRNAME],
        "forbidden_paths": list(FORBIDDEN_RUNTIME_PATHS),
        "allowed_python_files": allowed_python_files,
        "files": tracked_files,
    }


def build_packaged_runtime_modules(
    *,
    repo_root: Path,
    bundle_root: Path,
    build_root: Path,
    job_count: int,
    extra_nuitka_args: Iterable[str],
    extra_sources: ExtraSources | None = None,
    include_emotivoice: bool = False,
) -> dict[str, object]:
    plan = build_runtime_module_plan(
        repo_root, extra_sources=extra_sources, include_emotivoice=include_emotivoice,
    )
    output_root = bundle_root / RUNTIME_MODULES_DIRNAME
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    build_root.mkdir(parents=True, exist_ok=True)

    for spec in plan.compile_specs:
        print(f"[runtime-modules] compiling {spec.source_relative_path.as_posix()}")
        _run_nuitka_module_build(
            repo_root=repo_root,
            build_root=build_root,
            output_root=output_root,
            spec=spec,
            job_count=job_count,
            extra_nuitka_args=extra_nuitka_args,
            source_override=plan.source_overrides.get(spec.source_relative_path),
        )

    _copy_asset_files(repo_root=repo_root, output_root=output_root, plan=plan)
    _copy_static_runtime_files(repo_root=repo_root, output_root=output_root, plan=plan)
    _write_bridge_stubs(output_root=output_root, build_root=build_root, plan=plan, repo_root=repo_root)
    if plan.agent_overlay:
        (output_root / "autoyou_agents" / SIBLING_AGENT_MANIFEST).write_text(
            json.dumps(list(plan.sibling_agent_names), indent=2) + "\n", encoding="utf-8",
        )

    manifest = _build_integrity_manifest(bundle_root, output_root, plan)
    from runpy import run_path
    run_path(str(SERVER_ROOT / "scripts/stage_iroh_runtime.py"))["stage_from_environment"](bundle_root, manifest)
    manifest_path = bundle_root / RUNTIME_INTEGRITY_MANIFEST
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _normalize_cli_tokens(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        return None

    normalized: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--nuitka-arg" and index + 1 < len(argv):
            normalized.append(f"--nuitka-arg={argv[index + 1]}")
            index += 2
            continue
        normalized.append(token)
        index += 1

    return normalized


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = _normalize_cli_tokens(argv)
    parser = argparse.ArgumentParser(description="Build compiled runtime modules for the packaged backend.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--bundle-root", required=True)
    parser.add_argument("--build-root", required=True)
    parser.add_argument("--jobs", type=int, default=max(os.cpu_count() or 1, 1))
    parser.add_argument("--nuitka-arg", action="append", default=[])
    parser.add_argument(
        "--extra-sources",
        default=os.environ.get(EXTRA_SOURCES_ENV, ""),
        help=f"JSON manifest of modules from another source tree to compile in (default: ${EXTRA_SOURCES_ENV})",
    )
    parser.add_argument("--include-emotivoice", action="store_true", help="Compile the optional EmotiVoice inference runtime and copy its non-model data")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    bundle_root = Path(args.bundle_root).resolve()
    build_root = Path(args.build_root).resolve()

    build_packaged_runtime_modules(
        repo_root=repo_root,
        bundle_root=bundle_root,
        build_root=build_root,
        job_count=max(int(args.jobs), 1),
        extra_nuitka_args=tuple(args.nuitka_arg),
        extra_sources=load_extra_sources(Path(args.extra_sources)) if args.extra_sources else None,
        include_emotivoice=args.include_emotivoice,
    )
    print(f"Packaged runtime modules ready under {bundle_root / RUNTIME_MODULES_DIRNAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
