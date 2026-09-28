# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

# ============================================================================
# CRITICAL: multiprocessing.freeze_support() MUST be the very first executed
# statement - before any other import or user code.
#
# Why this matters for Nuitka compiled builds:
#   When Python's multiprocessing (spawn context, default on Windows) forks a
#   worker process it re-runs the executable from the top.  freeze_support()
#   detects the worker-spawn arguments and immediately invokes the worker
#   function, bypassing all GUI / server startup code.  If ANY import runs
#   before freeze_support() (pystray touches the Win32 message queue, PIL
#   loads native DLLs, etc.) the worker process can crash or deadlock.
#
# Rule: nothing may appear above freeze_support() except comments and the
# imports required to call it.
# ============================================================================
import multiprocessing
import sys
import multiprocessing.spawn as _multiprocessing_spawn

if len(sys.argv) >= 2 and sys.argv[1] == "--multiprocessing-fork":
    _multiprocessing_spawn.freeze_support()

multiprocessing.freeze_support()
sys.dont_write_bytecode = True

# Install before anything can call Process.start(). Spawned children inherit a
# snapshot of this process's sys.path, and cv2's loader transiently inserts its
# own package directory there while relinking the native extension; a child that
# captures that snapshot resolves stdlib `typing` to cv2/typing/__init__.py and
# dies during interpreter bootstrap. Stdlib-only import, safe this early.
try:
    from shared.mp_syspath_guard import install_multiprocessing_syspath_guard

    install_multiprocessing_syspath_guard()
except Exception:
    pass

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# ── Now safe to import everything else ──────────────────────────────────────
import builtins
import argparse
import hashlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import secrets
import signal
import shutil
import socket
import subprocess
import threading
import time
import types
import webbrowser
from pathlib import Path
from PIL import Image, ImageDraw

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

PACKAGED_RESOURCES_ROOT_ENV = "AUTOYOU_PACKAGED_RESOURCES_ROOT"
PACKAGED_RUNTIME_ENV = "AUTOYOU_PACKAGED_RUNTIME"
GRACEFUL_SHUTDOWN_WAIT_ENV = "AUTOYOU_GRACEFUL_SHUTDOWN_WAIT_SECONDS"
DEFAULT_GRACEFUL_SHUTDOWN_WAIT_SECONDS = 90.0


def _graceful_shutdown_wait_seconds() -> float:
    try:
        configured = float(os.getenv(GRACEFUL_SHUTDOWN_WAIT_ENV, str(DEFAULT_GRACEFUL_SHUTDOWN_WAIT_SECONDS)))
        return max(10.0, configured)
    except Exception:
        return DEFAULT_GRACEFUL_SHUTDOWN_WAIT_SECONDS


def _load_pystray_module():
    if not IS_WINDOWS:
        return None
    return importlib.import_module("pystray")


def _looks_like_packaged_runtime_dir(candidate: Path | None) -> bool:
    if candidate is None or not candidate.exists():
        return False

    runtime_markers = (
        "runtime_modules",
        "runtime_source",
        "runtime_site_packages",
        "runtime_stdlib",
        "runtime",
    )
    return any((candidate / marker).exists() for marker in runtime_markers)


def _is_nuitka_compiled_executable() -> bool:
    try:
        compiled = getattr(builtins, "__compiled__", None)
        if compiled is not None:
            return True
    except Exception:
        pass
    try:
        _ = __compiled__  # type: ignore[name-defined]  # noqa: F821
        return True
    except Exception:
        return False


def _looks_like_packaged_executable() -> bool:
    if getattr(sys, "frozen", False):
        return True
    if _is_nuitka_compiled_executable():
        return True

    executable = getattr(sys, "executable", "")
    if not executable:
        return False
    try:
        return _looks_like_packaged_runtime_dir(Path(executable).resolve().parent)
    except Exception:
        return False


def _configure_packaged_multiprocessing_spawn() -> None:
    """Make multiprocessing spawn use frozen child argv for standalone builds."""
    if _looks_like_packaged_executable():
        setattr(sys, "frozen", True)


def _get_macos_app_bundle_executable_dir() -> Path | None:
    if not IS_MAC:
        return None

    try:
        executable_dir = Path(sys.executable).resolve().parent
    except Exception:
        return None

    if executable_dir.name != "MacOS" or not executable_dir.is_dir():
        return None

    return executable_dir


def _patch_dotenv_for_packaged_runtime() -> None:
    """Keep python-dotenv from crashing on compiled frame paths."""
    packaged_hint = bool(
        os.getenv(PACKAGED_RUNTIME_ENV, "").strip()
        or os.getenv(PACKAGED_RESOURCES_ROOT_ENV, "").strip()
        or getattr(sys, "frozen", False)
    )
    if not packaged_hint:
        try:
            packaged_hint = getattr(builtins, "__compiled__", None) is not None  # type: ignore[attr-defined]
        except Exception:
            packaged_hint = False
    if not packaged_hint:
        return

    try:
        import dotenv
        import dotenv.main as dotenv_main
    except Exception:
        return

    existing_find = getattr(dotenv_main, "find_dotenv", None)
    if callable(existing_find) and not getattr(existing_find, "_autoyou_packaged_patch", False):
        original_find = existing_find

        def _safe_find_dotenv(*args, **kwargs):
            call_kwargs = dict(kwargs)
            call_kwargs.setdefault("usecwd", True)
            try:
                return original_find(*args, **call_kwargs)
            except (AssertionError, OSError) as exc:
                if isinstance(exc, AssertionError) or "Starting path not found" in str(exc):
                    return ""
                raise

        setattr(_safe_find_dotenv, "_autoyou_packaged_patch", True)
        dotenv_main.find_dotenv = _safe_find_dotenv
        try:
            dotenv.find_dotenv = _safe_find_dotenv
        except Exception:
            pass

    existing_load = getattr(dotenv_main, "load_dotenv", None)
    if callable(existing_load) and not getattr(existing_load, "_autoyou_packaged_patch", False):
        original_load = existing_load

        def _safe_load_dotenv(*args, **kwargs):
            try:
                return original_load(*args, **kwargs)
            except (AssertionError, OSError) as exc:
                if isinstance(exc, AssertionError) or "Starting path not found" in str(exc):
                    return False
                raise

        setattr(_safe_load_dotenv, "_autoyou_packaged_patch", True)
        dotenv_main.load_dotenv = _safe_load_dotenv
        try:
            dotenv.load_dotenv = _safe_load_dotenv
        except Exception:
            pass

def get_application_root() -> Path:
    """Return the application root directory."""
    try:
        # Nuitka onefile/standalone support
        compiled_containing_dir: Path | None = None
        try:
            compiled = builtins.__compiled__  # type: ignore
            containing_dir = getattr(compiled, "containing_dir", None)
            if containing_dir:
                compiled_containing_dir = Path(containing_dir).resolve()
        except Exception:
            compiled_containing_dir = None

        if IS_MAC:
            executable_dir = _get_macos_app_bundle_executable_dir()
            if executable_dir is not None and (
                compiled_containing_dir is None
                or not _looks_like_packaged_runtime_dir(compiled_containing_dir)
            ):
                return executable_dir.resolve()

        if compiled_containing_dir is not None:
            return compiled_containing_dir

        anchor_path = Path(__file__).resolve()

        if anchor_path.suffix == ".py" and not anchor_path.exists():
            # Pseudo-__file__ in Nuitka compiled context
            return anchor_path.parent

        if anchor_path.is_file():
            return anchor_path.parent
    except Exception:
        return Path.cwd()

    return anchor_path

APP_ROOT = get_application_root()
RUNTIME_MODULES_DIRNAME = "runtime_modules"
RUNTIME_SOURCE_DIRNAME = "runtime_source"
RUNTIME_INTEGRITY_MANIFEST = "runtime_integrity.json"
_RUNTIME_INTEGRITY_VERIFIED = False


class _RuntimeStdlibFinder(importlib.abc.MetaPathFinder):
    """Resolve stdlib modules from the bundled runtime_stdlib tree first."""

    def __init__(self, stdlib_root: Path):
        self.stdlib_root = stdlib_root.resolve()

    def find_spec(self, fullname: str, path=None, target=None):
        module_parts = fullname.split(".")
        package_init = self.stdlib_root.joinpath(*module_parts, "__init__.py")
        if package_init.is_file():
            return importlib.util.spec_from_file_location(
                fullname,
                package_init,
                submodule_search_locations=[str(package_init.parent)],
            )

        module_path = self.stdlib_root.joinpath(*module_parts).with_suffix(".py")
        if module_path.is_file():
            return importlib.util.spec_from_file_location(fullname, module_path)

        return None


def _is_path_within_root(candidate_path: str, root: Path) -> bool:
    try:
        Path(candidate_path).resolve().relative_to(root)
        return True
    except Exception:
        return False


class _RuntimeSitePackagesFinder(importlib.abc.MetaPathFinder):
    """Resolve bundled third-party packages before Nuitka embedded copies."""

    def __init__(self, site_packages_root: Path):
        self.site_packages_root = site_packages_root.resolve()

    def find_spec(self, fullname: str, path=None, target=None):
        if path is None:
            search_path = [str(self.site_packages_root)]
        else:
            search_path = [
                candidate_path
                for candidate_path in path
                if _is_path_within_root(candidate_path, self.site_packages_root)
            ]
            if not search_path:
                return None

        spec = importlib.machinery.PathFinder.find_spec(fullname, search_path)
        if spec is None:
            return None

        origin = getattr(spec, "origin", None)
        if origin not in {None, "namespace"}:
            return spec if _is_path_within_root(origin, self.site_packages_root) else None

        locations = getattr(spec, "submodule_search_locations", None)
        if locations:
            for location in locations:
                if _is_path_within_root(location, self.site_packages_root):
                    return spec

        return None


def _install_runtime_stdlib_finder(stdlib_root: Path) -> None:
    if not stdlib_root.is_dir():
        return

    resolved_root = stdlib_root.resolve()
    for finder in sys.meta_path:
        if isinstance(finder, _RuntimeStdlibFinder) and finder.stdlib_root == resolved_root:
            return

    sys.meta_path.insert(0, _RuntimeStdlibFinder(resolved_root))


def _install_runtime_site_packages_finder(site_packages_root: Path) -> None:
    if not site_packages_root.is_dir():
        return

    resolved_root = site_packages_root.resolve()
    for finder in sys.meta_path:
        if isinstance(finder, _RuntimeSitePackagesFinder) and finder.site_packages_root == resolved_root:
            return

    insert_index = 0
    for index, finder in enumerate(sys.meta_path):
        if isinstance(finder, _RuntimeStdlibFinder):
            insert_index = index + 1
            break

    sys.meta_path.insert(insert_index, _RuntimeSitePackagesFinder(resolved_root))


def _reload_runtime_stdlib_modules(app_root: Path, module_names: tuple[str, ...]) -> None:
    runtime_stdlib_root = app_root / "runtime_stdlib"
    if not runtime_stdlib_root.is_dir():
        return

    _install_runtime_stdlib_finder(runtime_stdlib_root)

    cleared_roots: set[str] = set()
    for module_name in module_names:
        root_name = module_name.split(".", 1)[0]
        if root_name not in cleared_roots:
            loaded_module_names = [
                loaded_name
                for loaded_name in tuple(sys.modules)
                if loaded_name == root_name or loaded_name.startswith(f"{root_name}.")
            ]
            for loaded_name in loaded_module_names:
                sys.modules.pop(loaded_name, None)
            cleared_roots.add(root_name)

        importlib.import_module(module_name)


def _prepend_runtime_path(path: Path, *, add_to_process_path: bool = False) -> None:
    if not path.is_dir():
        return

    resolved_path = str(path)
    if resolved_path not in sys.path:
        sys.path.insert(0, resolved_path)

    if add_to_process_path:
        existing_process_path = os.environ.get("PATH", "")
        process_path_entries = [entry for entry in existing_process_path.split(os.pathsep) if entry]
        if resolved_path not in process_path_entries:
            os.environ["PATH"] = os.pathsep.join([resolved_path, *process_path_entries])

    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath_entries = [entry for entry in existing_pythonpath.split(os.pathsep) if entry]
    if resolved_path not in pythonpath_entries:
        os.environ["PYTHONPATH"] = os.pathsep.join([resolved_path, *pythonpath_entries])


def _append_loaded_package_path(package_name: str, package_dir: Path) -> None:
    if not package_dir.is_dir():
        return

    package_module = sys.modules.get(package_name)
    if package_module is None:
        return

    package_path = getattr(package_module, "__path__", None)
    if package_path is None:
        return

    resolved_path = str(package_dir.resolve())
    existing_entries = list(package_path)
    if resolved_path in existing_entries:
        return

    try:
        package_path.append(resolved_path)
    except Exception:
        package_module.__path__ = [*existing_entries, resolved_path]


def _extend_loaded_runtime_package_paths(runtime_modules_root: Path) -> None:
    """Let already-loaded compiled packages find bundled runtime subpackages."""
    if not runtime_modules_root.is_dir():
        return

    runtime_package_roots = ("autoyou_agents", "shared")
    for module_name in tuple(sys.modules):
        if not any(
            module_name == root_name or module_name.startswith(f"{root_name}.")
            for root_name in runtime_package_roots
        ):
            continue
        package_dir = runtime_modules_root.joinpath(*module_name.split("."))
        _append_loaded_package_path(module_name, package_dir)


def _ensure_runtime_package_chain(module_name: str, runtime_modules_root: Path) -> None:
    """Create runtime package parents before importing nested compiled modules."""
    if not runtime_modules_root.is_dir():
        return

    package_parts = str(module_name or "").split(".")[:-1]
    for depth in range(1, len(package_parts) + 1):
        package_name = ".".join(package_parts[:depth])
        package_dir = runtime_modules_root.joinpath(*package_parts[:depth])
        if not package_dir.is_dir():
            continue

        existing_module = sys.modules.get(package_name)
        if existing_module is not None:
            _append_loaded_package_path(package_name, package_dir)
            continue

        package_module = types.ModuleType(package_name)
        package_module.__file__ = str(package_dir / "__init__.py")
        package_module.__package__ = package_name
        package_module.__path__ = [str(package_dir)]
        sys.modules[package_name] = package_module


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_packaged_runtime_integrity(app_root: Path) -> None:
    global _RUNTIME_INTEGRITY_VERIFIED

    if _RUNTIME_INTEGRITY_VERIFIED:
        return

    manifest_path = app_root / RUNTIME_INTEGRITY_MANIFEST
    if not manifest_path.is_file():
        return

    runtime_modules_root = app_root / RUNTIME_MODULES_DIRNAME
    if not runtime_modules_root.is_dir():
        raise RuntimeError(f"Packaged runtime integrity manifest found but '{runtime_modules_root}' is missing")

    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    if not isinstance(manifest, dict):
        raise RuntimeError("Packaged runtime integrity manifest must be a JSON object")

    algorithm = str(manifest.get("algorithm", "")).strip().lower()
    if algorithm != "sha256":
        raise RuntimeError(f"Unsupported packaged runtime integrity algorithm: {algorithm or 'missing'}")

    tracked_files = manifest.get("files")
    if not isinstance(tracked_files, dict):
        raise RuntimeError("Packaged runtime integrity manifest is missing its file hash table")

    tracked_roots = manifest.get("tracked_roots") or [RUNTIME_MODULES_DIRNAME]
    tracked_root_paths = [
        app_root / str(relative_root).replace("\\", "/").strip("/")
        for relative_root in tracked_roots
        if str(relative_root).strip()
    ]
    allowed_python_files = {
        str(relative_path).replace("\\", "/").strip("/")
        for relative_path in (manifest.get("allowed_python_files") or [])
        if str(relative_path).strip()
    }

    forbidden_paths = [
        str(relative_path).replace("\\", "/").strip("/")
        for relative_path in (manifest.get("forbidden_paths") or [])
        if str(relative_path).strip()
    ]
    for forbidden_path in forbidden_paths:
        if (app_root / forbidden_path).exists():
            raise RuntimeError(f"Forbidden packaged runtime path present: {forbidden_path}")

    missing_files: list[str] = []
    mismatched_files: list[str] = []
    unexpected_files: list[str] = []
    unexpected_python_files: list[str] = []

    normalized_hashes = {
        str(relative_path).replace("\\", "/").strip("/"): str(expected_hash).strip().lower()
        for relative_path, expected_hash in tracked_files.items()
    }

    for relative_path, expected_hash in normalized_hashes.items():
        candidate_path = app_root / relative_path
        if not candidate_path.is_file():
            missing_files.append(relative_path)
            continue
        actual_hash = _hash_file(candidate_path)
        if actual_hash != expected_hash:
            mismatched_files.append(relative_path)

    for tracked_root in tracked_root_paths:
        if not tracked_root.exists():
            continue
        for candidate_path in tracked_root.rglob("*"):
            if not candidate_path.is_file():
                continue
            relative_path = candidate_path.relative_to(app_root).as_posix()
            if relative_path not in normalized_hashes:
                unexpected_files.append(relative_path)
            if candidate_path.suffix == ".py" and relative_path not in allowed_python_files:
                unexpected_python_files.append(relative_path)

    problems: list[str] = []
    if missing_files:
        problems.append("missing=" + ", ".join(sorted(missing_files)[:10]))
    if mismatched_files:
        problems.append("mismatched=" + ", ".join(sorted(mismatched_files)[:10]))
    if unexpected_files:
        problems.append("unexpected=" + ", ".join(sorted(unexpected_files)[:10]))
    if unexpected_python_files:
        problems.append("unexpected-python=" + ", ".join(sorted(unexpected_python_files)[:10]))

    if problems:
        raise RuntimeError("Packaged runtime integrity verification failed: " + "; ".join(problems))

    _RUNTIME_INTEGRITY_VERIFIED = True


def _configure_runtime_modules_path(app_root: Path) -> None:
    """Prepend bundled compiled runtime modules when present."""
    try:
        runtime_modules_root = app_root / RUNTIME_MODULES_DIRNAME
        _prepend_runtime_path(runtime_modules_root)
        _extend_loaded_runtime_package_paths(runtime_modules_root)
    except Exception:
        pass


def _configure_runtime_source_path(app_root: Path) -> None:
    """Prepend bundled runtime-only source modules when present."""
    try:
        runtime_source_root = app_root / RUNTIME_SOURCE_DIRNAME
        _prepend_runtime_path(runtime_source_root)
    except Exception:
        pass


def _configure_runtime_site_packages_path(app_root: Path) -> None:
    """Prepend bundled third-party runtime packages when present."""
    try:
        runtime_site_packages_root = app_root / "runtime_site_packages"
        _install_runtime_site_packages_finder(runtime_site_packages_root)
        _prepend_runtime_path(runtime_site_packages_root)
        _prepend_runtime_path(runtime_site_packages_root / "win32")
        _prepend_runtime_path(runtime_site_packages_root / "win32" / "lib")
        _prepend_runtime_path(runtime_site_packages_root / "Pythonwin")
        _prepend_runtime_path(runtime_site_packages_root / "pywin32_system32", add_to_process_path=True)
    except Exception:
        pass


def _configure_runtime_stdlib_path(app_root: Path) -> None:
    """Prepend bundled Python stdlib/modules when present."""
    try:
        runtime_stdlib_root = app_root / "runtime_stdlib"
        _install_runtime_stdlib_finder(runtime_stdlib_root)
        _prepend_runtime_path(runtime_stdlib_root)

        runtime_stdlib_dlls_root = runtime_stdlib_root / "DLLs"
        _prepend_runtime_path(runtime_stdlib_dlls_root, add_to_process_path=True)
        runtime_stdlib_lib_dynload_root = runtime_stdlib_root / "lib-dynload"
        _prepend_runtime_path(runtime_stdlib_lib_dynload_root, add_to_process_path=True)
    except Exception:
        pass


def _configure_readable_mime_database() -> None:
    """Load only the system mime files this process may actually open.

    `mimetypes` checks that a known file exists and then opens it. Inside a
    sandbox the check succeeds while the open is refused, and the resulting
    PermissionError reaches whatever first guessed a content type. Deciding it
    once here keeps every later guess deterministic.
    """
    try:
        import mimetypes
    except Exception:
        return
    readable = []
    for candidate in mimetypes.knownfiles:
        try:
            with open(candidate, "rb"):
                readable.append(candidate)
        except OSError:
            continue
    mimetypes.knownfiles = readable
    try:
        mimetypes.init()
    except Exception:
        pass


def _configure_runtime_import_paths(app_root: Path) -> None:
    _verify_packaged_runtime_integrity(app_root)
    _configure_readable_mime_database()
    try:
        _configure_runtime_stdlib_path(app_root)
        _configure_runtime_site_packages_path(app_root)
        _configure_runtime_source_path(app_root)
        _configure_runtime_modules_path(app_root)
    except Exception:
        pass


_configure_runtime_import_paths(APP_ROOT)

SHUTDOWN_TOKEN_ENV = "AUTOYOU_SHUTDOWN_TOKEN"
SHUTDOWN_TOKEN_HEADER = "X-AutoYou-Shutdown-Token"
RUNTIME_SITE_PACKAGE_OVERRIDES = (
    "websockets",
    "websockets.asyncio",
    "websockets.asyncio.client",
    "websockets.client",
)


def _reload_runtime_site_packages_modules(app_root: Path, module_names: tuple[str, ...]) -> None:
    runtime_site_packages_root = app_root / "runtime_site_packages"
    if not runtime_site_packages_root.is_dir():
        return

    _install_runtime_site_packages_finder(runtime_site_packages_root)

    cleared_roots: set[str] = set()
    for module_name in module_names:
        root_name = module_name.split(".", 1)[0]
        if root_name in cleared_roots:
            continue

        loaded_module_names = [
            loaded_name
            for loaded_name in tuple(sys.modules)
            if loaded_name == root_name or loaded_name.startswith(f"{root_name}.")
        ]
        for loaded_name in loaded_module_names:
            sys.modules.pop(loaded_name, None)
        cleared_roots.add(root_name)

    for module_name in module_names:
        importlib.import_module(module_name)


def _prepare_runtime_server_imports(app_root: Path) -> None:
    _configure_runtime_import_paths(app_root)
    _reload_runtime_stdlib_modules(app_root, ("http", "http.cookies"))
    _reload_runtime_site_packages_modules(app_root, RUNTIME_SITE_PACKAGE_OVERRIDES)


def _load_runtime_module_from_path(
    module_name: str,
    relative_path_parts: tuple[str, ...],
    package_paths: tuple[tuple[str, tuple[str, ...]], ...] = (),
):
    existing_module = sys.modules.get(module_name)
    if existing_module is not None:
        return existing_module

    allowed_missing_names = {module_name, module_name.split(".", 1)[0]}
    allowed_missing_names.update(package_name for package_name, _ in package_paths)
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as import_error:
        missing_name = str(getattr(import_error, "name", "") or "").strip()
        if missing_name and missing_name not in allowed_missing_names:
            raise

    runtime_source_root = APP_ROOT / RUNTIME_SOURCE_DIRNAME
    module_path = runtime_source_root.joinpath(*relative_path_parts)
    if not module_path.is_file():
        raise ModuleNotFoundError(f"Runtime module '{module_name}' not found at '{module_path}'") from import_error

    for package_name, package_path_parts in package_paths:
        if package_name in sys.modules:
            continue

        package_path = runtime_source_root.joinpath(*package_path_parts)
        package_module = types.ModuleType(package_name)
        package_module.__file__ = str(package_path)
        package_module.__package__ = package_name
        package_module.__path__ = [str(package_path)]
        sys.modules[package_name] = package_module

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create runtime loader for '{module_name}' at '{module_path}'")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _get_shared_platform_runtime_module():
    return _load_runtime_module_from_path(
        "shared.platform_runtime",
        ("shared", "platform_runtime.py"),
        (("shared", ("shared",)),),
    )


def _append_launcher_log(message: str) -> None:
    """Write launcher diagnostics to a persistent log for packaged builds."""
    try:
        platform_runtime_module = _get_shared_platform_runtime_module()
        log_path = platform_runtime_module.get_logs_dir("AutoYou", anchor=__file__) / "launcher.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log_file:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            log_file.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def _get_admin_port_from_env(env: dict | None = None) -> int:
    variables = env if env is not None else os.environ
    for key in ("AUTOYOU_ADMIN_PORT", "AUTOYOU_ADMIN_SPORT"):
        value = str(variables.get(key, "")).strip()
        if value.isdigit():
            return int(value)
    return 8001


def _get_port_from_env(keys: tuple[str, ...], default: int, env: dict | None = None) -> int:
    variables = env if env is not None else os.environ
    for key in keys:
        value = str(variables.get(key, "")).strip()
        if value.isdigit():
            return int(value)
    return int(default)


def _request_local_shutdown(admin_port: int, shutdown_token: str, timeout: float = 3.0) -> bool:
    if not shutdown_token:
        return False

    from urllib.request import Request, urlopen

    request = Request(
        f"http://127.0.0.1:{admin_port}/shutdown",
        data=b"",
        method="POST",
        headers={SHUTDOWN_TOKEN_HEADER: shutdown_token},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return 200 <= getattr(response, "status", 0) < 300
    except Exception as exc:
        _append_launcher_log(
            f"local shutdown request failed port={admin_port} exc={exc.__class__.__name__}:{exc}"
        )
        return False


def _is_loopback_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, int(port))) == 0
    except Exception:
        return False


def _get_admin_browser_url(env: dict | None = None, argv: list[str] | None = None) -> str:
    variables = env if env is not None else os.environ
    bind_host = str(
        variables.get("AUTOYOU_BIND_HOST")
        or variables.get("AUTOYOU_HOST_BIND")
        or "127.0.0.1"
    ).strip()
    launch_args = list(sys.argv[1:] if argv is None else argv)
    for index, argument in enumerate(launch_args):
        if argument in {"--host", "--bind-host"} and index + 1 < len(launch_args):
            bind_host = str(launch_args[index + 1]).strip() or bind_host
        elif argument.startswith("--host="):
            bind_host = argument.split("=", 1)[1].strip() or bind_host

    port = _get_admin_port_from_env(variables)
    for index, argument in enumerate(launch_args):
        if argument in {"--admin", "--admin-port"} and index + 1 < len(launch_args):
            candidate = launch_args[index + 1]
        elif argument.startswith("--admin="):
            candidate = argument.split("=", 1)[1]
        else:
            continue
        try:
            parsed = int(candidate)
        except (TypeError, ValueError):
            continue
        if 1 <= parsed <= 65535:
            port = parsed

    if bind_host in {"0.0.0.0", "::", "*"}:
        bind_host = "127.0.0.1"
    if ":" in bind_host and not bind_host.startswith("["):
        bind_host = f"[{bind_host}]"
    return f"http://{bind_host}:{port}/"


def _open_default_browser(url: str) -> bool:
    try:
        if webbrowser.open(url, new=2, autoraise=True):
            return True
    except Exception:
        pass

    # WSLg may expose either wslview, xdg-open, or the Windows browser bridge.
    for command in ("wslview", "xdg-open", "gio", "explorer.exe"):
        executable = shutil.which(command)
        if not executable:
            continue
        try:
            args = [executable, "open", url] if command == "gio" else [executable, url]
            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception:
            continue
    return False


def _open_admin_browser_when_ready() -> None:
    url = _get_admin_browser_url()
    deadline = time.monotonic() + 120
    from urllib.request import Request, urlopen

    while time.monotonic() < deadline:
        try:
            request = Request(url, headers={"Cache-Control": "no-cache"})
            with urlopen(request, timeout=2) as response:
                if 200 <= int(getattr(response, "status", 0)) < 500:
                    if not _open_default_browser(url):
                        _append_launcher_log(f"Admin UI is ready but no default browser opener was found: {url}")
                    return
        except Exception:
            pass
        time.sleep(1)

    _append_launcher_log(f"Admin UI did not become reachable before browser-open timeout: {url}")


def _start_packaged_admin_browser() -> None:
    if not IS_LINUX or not _looks_like_packaged_executable():
        return
    threading.Thread(target=_open_admin_browser_when_ready, name="autoyou-admin-browser", daemon=True).start()


def _list_loopback_listener_pids(port: int) -> list[int]:
    lsof_candidates = ("/usr/sbin/lsof", "/usr/bin/lsof")
    for lsof_path in lsof_candidates:
        if not Path(lsof_path).is_file():
            continue
        try:
            result = subprocess.run(
                [lsof_path, "-nP", "-t", f"-iTCP:{int(port)}", "-sTCP:LISTEN"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except Exception:
            continue

        pids: list[int] = []
        for raw_line in (result.stdout or "").splitlines():
            value = raw_line.strip()
            if value.isdigit():
                pids.append(int(value))
        if pids:
            return sorted(set(pids))

    try:
        import psutil
    except Exception:
        return []

    results: set[int] = set()
    loopback_hosts = {"127.0.0.1", "localhost", "0.0.0.0", "::1", "::"}
    try:
        connections = psutil.net_connections(kind="tcp")
    except Exception:
        return []

    for connection in connections:
        try:
            if getattr(connection, "status", "") != psutil.CONN_LISTEN:
                continue
            local_address = getattr(connection, "laddr", None)
            if not local_address or len(local_address) < 2:
                continue
            local_host = str(local_address[0])
            local_port = int(local_address[1])
            listener_pid = int(connection.pid) if connection.pid is not None else None
        except Exception:
            continue
        if local_port != int(port) or listener_pid is None:
            continue
        if local_host not in loopback_hosts:
            continue
        results.add(listener_pid)
    return sorted(results)


def _force_kill_pid_tree(pid: int | None) -> None:
    try:
        normalized_pid = int(pid) if pid is not None else None
    except Exception:
        normalized_pid = None
    if normalized_pid is None or normalized_pid <= 0:
        return

    if os.name != "nt":
        try:
            os.killpg(normalized_pid, signal.SIGKILL)
        except Exception:
            pass

    try:
        import psutil
    except Exception:
        try:
            os.kill(normalized_pid, signal.SIGKILL)
        except Exception:
            pass
        return

    try:
        root_process = psutil.Process(normalized_pid)
    except Exception:
        return

    processes = []
    try:
        processes.extend(root_process.children(recursive=True))
    except Exception:
        pass
    processes.append(root_process)

    for proc in reversed(processes):
        try:
            proc.kill()
        except Exception:
            pass


def _strip_compiled_server_script_arg(args: list[str]) -> tuple[list[str], bool]:
    """Remove ``server.py`` re-entry markers produced by some compiled launches."""
    cleaned_args: list[str] = []
    saw_server_script = False

    for raw_arg in args:
        try:
            arg_name = str(raw_arg).replace("\\", "/").rsplit("/", 1)[-1].lower()
        except Exception:
            arg_name = str(raw_arg).lower()

        if arg_name == "server.py":
            saw_server_script = True
            continue
        cleaned_args.append(raw_arg)

    return cleaned_args, saw_server_script

# Determine server executable or script
if IS_WINDOWS:
    # In a built environment, server might be a separate exe or we import it.
    # For now, let's assume we run server.py via the same python executable or we spawn it.
    # If compiled, sys.executable is the tray app itself.
    # If we want to ship 1 exe, we could actually import server and run it in a thread.
    pass

class TrayApp:
    def __init__(self):
        self.server_process = None
        self.server_shutdown_token = None
        self.server_admin_port = 8001
        self.server_ai_port = 8081
        self.server_auth_port = 8002
        self.icon = None
        self.running = True

    def _create_default_icon(self):
        """Create a fallback icon if we don't have a packaged tray/app icon."""
        image = Image.new('RGB', (64, 64), color=(73, 109, 137))
        d = ImageDraw.Draw(image)
        d.text((10, 25), "AutoYou", fill=(255, 255, 0))
        return image

    def _get_icon_image(self):
        """Load a packaged tray/app icon from assets or fall back to logo.png."""
        icon_candidates = [
            APP_ROOT / "assets" / "AppIcon.png",
            APP_ROOT / "assets" / "TrayLogo.png",
            APP_ROOT / "assets" / "AppLogo@1x.png",
            APP_ROOT / "assets" / "logo.png",
        ]
        for icon_path in icon_candidates:
            if not icon_path.exists():
                continue
            try:
                return Image.open(icon_path)
            except Exception as e:
                print(f"Failed to load icon {icon_path.name}: {e}")
        return self._create_default_icon()

    def start_server(self):
        """Spawn the AutoYou server as a subprocess."""
        if self.server_process and self.server_process.poll() is None:
            return  # Already running

        print("Starting AutoYou server...")

        # Determine how to run the server
        cwd = str(APP_ROOT)
        env = os.environ.copy()
        self.server_shutdown_token = secrets.token_urlsafe(32)
        env[SHUTDOWN_TOKEN_ENV] = self.server_shutdown_token
        env[PACKAGED_RUNTIME_ENV] = "1"
        env[PACKAGED_RESOURCES_ROOT_ENV] = str(APP_ROOT)
        self.server_admin_port = _get_admin_port_from_env(env)
        self.server_ai_port = _get_port_from_env(
            ("AUTOYOU_AI_PORT", "AUTOYOU_AI_AGENT_SERVER_PORT", "AI_AGENT_SERVER_PORT"),
            8081,
            env,
        )
        self.server_auth_port = _get_port_from_env(("AUTOYOU_AUTH_PORT", "AUTH_SERVER_PORT"), 8002, env)

        # On Windows, we need to hide the console window of the subprocess
        lifecycle_module = _load_runtime_module_from_path(
            "shared.process_lifecycle",
            ("shared", "process_lifecycle.py"),
            (("shared", ("shared",)),),
        )
        env = lifecycle_module.add_parent_pid_environment(env)
        process_options = lifecycle_module.process_spawn_kwargs(hide_window=True)

        # ── stdout/stderr handling ────────────────────────────────────────────
        # Do NOT pipe stdout/stderr to PIPE without a consumer thread.
        # If the log buffer fills (64 KB on Windows), the subprocess BLOCKS
        # indefinitely on write() - which stalls the whole server.
        # Strategy: write directly to a log file so there is no pipe buffer limit.
        platform_runtime_module = _get_shared_platform_runtime_module()

        log_dir = platform_runtime_module.get_logs_dir("AutoYou", anchor=__file__)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        log_file_path = log_dir / "server.log"

        # Use the shared packaged-runtime detector so tray/server launch stays
        # aligned with the rest of the compiled path handling.
        is_packaged_runtime = platform_runtime_module.is_compiled()
        if is_packaged_runtime:
            cmd = [sys.executable, "--run-server"]
        else:
            server_script = APP_ROOT / "server.py"
            cmd = [sys.executable, str(server_script)]

        _append_launcher_log(
            f"start_server packaged={is_packaged_runtime} exe={sys.executable!r} cwd={cwd!r} cmd={cmd!r}"
        )

        try:
            try:
                log_handle = open(log_file_path, "a", encoding="utf-8", buffering=1)
            except Exception:
                log_handle = subprocess.DEVNULL  # fallback: discard output

            self.server_process = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=log_handle,
                stderr=log_handle,
                **process_options,
            )
        except Exception as e:
            print(f"Failed to start server: {e}")

    def stop_server(self):
        """Gracefully stop the server process."""
        if self.server_process and self.server_process.poll() is None:
            print("Stopping AutoYou server...")
            process_pid = getattr(self.server_process, "pid", None)
            graceful_shutdown_requested = _request_local_shutdown(
                self.server_admin_port,
                self.server_shutdown_token or "",
            )
            if graceful_shutdown_requested:
                _append_launcher_log(
                    f"graceful shutdown requested port={self.server_admin_port} pid={getattr(self.server_process, 'pid', None)}"
                )
            try:
                self.server_process.wait(timeout=_graceful_shutdown_wait_seconds() if graceful_shutdown_requested else 5)
            except subprocess.TimeoutExpired:
                print("Server did not gracefully stop, terminating...")
                self.server_process.terminate()
                try:
                    self.server_process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    print("Server did not terminate cleanly, killing...")
                    self.server_process.kill()
            lingering_ports = [
                port
                for port in (self.server_admin_port, self.server_ai_port, self.server_auth_port, 8067, 8083)
                if _is_loopback_port_open(port)
            ]
            if lingering_ports:
                _append_launcher_log(
                    f"ports still open after tray shutdown {lingering_ports}; forcing PID cleanup"
                )
                for port in lingering_ports:
                    for listener_pid in _list_loopback_listener_pids(port):
                        _force_kill_pid_tree(listener_pid)
                _force_kill_pid_tree(process_pid)
            self.server_process = None
            self.server_shutdown_token = None

    def restart_server(self, icon, item):
        """Menu action to restart server."""
        self.stop_server()
        time.sleep(1)
        self.start_server()

    def open_chat(self, icon, item):
        """Menu action to open the chat UI."""
        webbrowser.open(f"http://localhost:{self.server_ai_port}/")

    def open_admin(self, icon, item):
        """Menu action to open the admin UI."""
        webbrowser.open(f"http://localhost:{self.server_admin_port}/")

    def quit_app(self, icon, item):
        """Menu action to quit the tray app and stop the server."""
        self.running = False
        self.stop_server()
        icon.stop()

    def run(self):
        """Run the tray app."""
        pystray = _load_pystray_module()
        if pystray is None:
            raise RuntimeError(
                "Tray mode is only supported on Windows for this launcher. "
                "Use the native macOS AutoYou host app."
            )

        self.start_server()

        menu = pystray.Menu(
            pystray.MenuItem("Admin UI (Settings)", self.open_admin, default=True),
            pystray.MenuItem("Open Chat", self.open_chat),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart Server", self.restart_server),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self.quit_app)
        )

        image = self._get_icon_image()
        self.icon = pystray.Icon("AutoYou", image, "AutoYou", menu)

        # icon.run() blocks until icon.stop() is called
        self.icon.run()

def run_server_mode():
    """Run the actual server logic (invoked with --run-server flag)."""
    print("Running in server mode...")
    _start_packaged_admin_browser()
    os.environ[PACKAGED_RUNTIME_ENV] = "1"
    os.environ[PACKAGED_RESOURCES_ROOT_ENV] = str(APP_ROOT)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    _patch_dotenv_for_packaged_runtime()
    _prepare_runtime_server_imports(APP_ROOT)

    # Point Whisper/Hugging Face caches at a writable user directory so
    # compiled workers never try to download models into the app bundle.
    try:
        platform_runtime_module = _get_shared_platform_runtime_module()
        platform_runtime_module.configure_whisper_cache_environment("AutoYou")
    except Exception as exc:
        print(f"[autoyou_app] Warning: could not set Whisper cache dir: {exc}")

    try:
        import asyncio

        server_module = _load_runtime_module_from_path("server", ("server.py",))
        asyncio.run(server_module.main())
    except KeyboardInterrupt:
        print("Server stopped.")
    except Exception as exc:
        _append_launcher_log(f"run_server_mode failed: {exc.__class__.__name__}: {exc}")
        raise


def run_desktop_stdio_mode():
    """Private native-host IPC; the same executable owns backend subprocesses."""
    packaged = _looks_like_packaged_executable()
    if packaged:
        os.environ["AUTOYOU_V2_SERVER_EXECUTABLE"] = sys.executable
    os.environ["AUTOYOU_V2_BUNDLED"] = "1" if packaged else "0"
    # Dynamic import keeps legacy server distributions free of desktop modules.
    worker = importlib.import_module("v2.runtime.worker")
    worker.main()


def run_ai_agent_server_mode(host: str, port: int, agent_dir: str):
    """Run only the AI agent FastAPI worker inside the compiled binary."""
    print(f"Running AI Agent server mode on {host}:{port}...")
    os.environ[PACKAGED_RUNTIME_ENV] = "1"
    os.environ[PACKAGED_RESOURCES_ROOT_ENV] = str(APP_ROOT)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    _patch_dotenv_for_packaged_runtime()
    _prepare_runtime_server_imports(APP_ROOT)
    try:
        platform_runtime_module = _get_shared_platform_runtime_module()
        platform_runtime_module.configure_whisper_cache_environment("AutoYou")
        server_module = _load_runtime_module_from_path("server", ("server.py",))

        server_module.run_agent_server(host, int(port), agent_dir, os.environ.copy())
    except KeyboardInterrupt:
        print("AI Agent server stopped.")
    except Exception as exc:
        _append_launcher_log(f"run_ai_agent_server_mode failed: {exc.__class__.__name__}: {exc}")
        raise


def run_fine_tuning_runner_mode(argv: list[str]) -> int:
    """Run the compiled Fine Tuning worker without shipping agent Python source."""
    os.environ[PACKAGED_RUNTIME_ENV] = "1"
    os.environ[PACKAGED_RESOURCES_ROOT_ENV] = str(APP_ROOT)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    _patch_dotenv_for_packaged_runtime()
    _prepare_runtime_server_imports(APP_ROOT)
    module_name = "autoyou_agents.fine_tuning_agent.training_runner"
    _ensure_runtime_package_chain(module_name, APP_ROOT / RUNTIME_MODULES_DIRNAME)
    runner_module = importlib.import_module(module_name)
    result = runner_module.main(argv)
    return int(result or 0)


def run_autoyou_lite_server_mode(host: str, port: int, auth_port: int):
    """Run the dedicated autoyou_lite FastAPI server inside the compiled binary."""
    print(f"Running autoyou_lite server mode on {host}:{port}...")
    os.environ[PACKAGED_RUNTIME_ENV] = "1"
    os.environ[PACKAGED_RESOURCES_ROOT_ENV] = str(APP_ROOT)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    _patch_dotenv_for_packaged_runtime()
    _prepare_runtime_server_imports(APP_ROOT)
    try:
        platform_runtime_module = _get_shared_platform_runtime_module()
        platform_runtime_module.configure_whisper_cache_environment("AutoYouLib")
        import uvicorn

        try:
            lib_server_module = _load_runtime_module_from_path(
                "autoyou_lite.server",
                ("autoyou_lite", "server.py"),
                (
                    ("autoyou_lite", ("autoyou_lite",)),
                ),
            )
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "autoyou_lite is not bundled with the main AutoYou.exe backend. "
                "Use the dedicated autoyou_lite package/build instead."
            ) from exc
        AutoYouLib = lib_server_module.AutoYouLib
        create_app = lib_server_module.create_app

        config = {"port": int(port), "auth_port": int(auth_port)}
        if host:
            config["host"] = host
        lib = AutoYouLib(config)
        host = lib._bind_host
        app = create_app(lib)
        uvicorn.run(
            app,
            host=host,
            port=int(port),
            log_level=os.getenv("AUTOYOU_LITE_LOG_LEVEL", "info"),
        )
    except KeyboardInterrupt:
        print("autoyou_lite server stopped.")
    except Exception as exc:
        _append_launcher_log(f"run_autoyou_lite_server_mode failed: {exc.__class__.__name__}: {exc}")
        raise


def verify_runtime_imports(module_names: tuple[str, ...]) -> None:
    _prepare_runtime_server_imports(APP_ROOT)
    runtime_modules_root = APP_ROOT / RUNTIME_MODULES_DIRNAME
    for module_name in module_names:
        _ensure_runtime_package_chain(module_name, runtime_modules_root)
        importlib.import_module(module_name)
        print(f"Runtime import verified: {module_name}")


def verify_server_imports() -> None:
    _prepare_runtime_server_imports(APP_ROOT)
    _load_runtime_module_from_path("server", ("server.py",))
    print("Runtime server import verified: server")


def _should_default_to_server_mode(program_name: str, app_root: Path) -> bool:
    try:
        executable_name = Path(program_name).name.lower()
    except Exception:
        executable_name = ""

    if executable_name in {"autoyouserver", "autoyoudevserver", "autoyouserver.exe", "autoyoudevserver.exe"}:
        return True
    if IS_LINUX and executable_name == "autoyou":
        return True

    try:
        return app_root.name.lower() == "backend"
    except Exception:
        return False


def _default_adk_agent_dir() -> str:
    """Resolve the ADK agents_dir root for source and packaged launches."""
    try:
        platform_runtime_module = _get_shared_platform_runtime_module()
        return str(platform_runtime_module.get_adk_agents_base_dir(__file__))
    except Exception as exc:
        _append_launcher_log(f"falling back to APP_ROOT for agent-dir default: {exc}")
        return str(APP_ROOT)


def main(argv: list[str] | None = None) -> str:
    _configure_runtime_import_paths(APP_ROOT)
    os.environ.setdefault(PACKAGED_RUNTIME_ENV, "1")
    os.environ.setdefault(PACKAGED_RESOURCES_ROOT_ENV, str(APP_ROOT))
    _configure_packaged_multiprocessing_spawn()
    _patch_dotenv_for_packaged_runtime()

    effective_argv = list(sys.argv[1:] if argv is None else argv)
    program_name = sys.argv[0] if sys.argv else "AutoYou"

    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--run-server", action="store_true")
    parser.add_argument("--desktop-stdio", action="store_true")
    parser.add_argument("--run-ai-agent-server", action="store_true")
    parser.add_argument("--run-fine-tuning-runner", action="store_true")
    parser.add_argument("--run-autoyou-lite", action="store_true")
    parser.add_argument("--host", default=os.getenv("AUTOYOU_BIND_HOST"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AUTOYOU_AI_PORT", "8081")))
    parser.add_argument("--auth-port", type=int, default=int(os.getenv("AUTOYOU_LITE_AUTH_PORT", "8098")))
    parser.add_argument("--agent-dir", default=_default_adk_agent_dir())
    parser.add_argument("--verify-server-imports", action="store_true")
    parser.add_argument("--verify-runtime-import", dest="verify_runtime_imports", action="append", default=[])
    parser.add_argument("--read-keyring-password", action="store_true")
    parser.add_argument("--keyring-service", default="")
    parser.add_argument("--keyring-username", default="")
    parsed_args, remaining_args = parser.parse_known_args(effective_argv)
    remaining_args, saw_server_script = _strip_compiled_server_script_arg(remaining_args)

    if argv is None:
        sys.argv = [program_name, *remaining_args]

    _append_launcher_log(
        "entrypoint argv="
        f"{effective_argv!r} remaining={remaining_args!r} "
        f"run_server={parsed_args.run_server} run_ai_agent_server={parsed_args.run_ai_agent_server} "
        f"run_fine_tuning_runner={parsed_args.run_fine_tuning_runner} "
        f"run_autoyou_lite={parsed_args.run_autoyou_lite} "
        f"saw_server_script={saw_server_script}"
    )
    if parsed_args.host:
        os.environ["AUTOYOU_BIND_HOST"] = str(parsed_args.host)

    if parsed_args.read_keyring_password:
        service_name = str(parsed_args.keyring_service or "")
        username = str(parsed_args.keyring_username or "")
        if not service_name or not username:
            raise SystemExit("--read-keyring-password requires --keyring-service and --keyring-username")
        try:
            import keyring

            value = keyring.get_password(service_name, username)
            print(json.dumps({"value": value}))
        except BaseException:
            # The parent treats a non-zero helper exit as a locked credential.
            # Do not emit traceback text that could expose backend details.
            raise SystemExit(1)
        return "keyring-password"

    if parsed_args.verify_server_imports or parsed_args.verify_runtime_imports:
        if parsed_args.verify_server_imports:
            verify_server_imports()
        if parsed_args.verify_runtime_imports:
            verify_runtime_imports(tuple(parsed_args.verify_runtime_imports))
        if parsed_args.verify_server_imports and parsed_args.verify_runtime_imports:
            return "verify-imports"
        if parsed_args.verify_server_imports:
            return "verify-server-imports"
        return "verify-runtime-imports"
    if parsed_args.desktop_stdio:
        run_desktop_stdio_mode()
        return "desktop-stdio"
    if parsed_args.run_ai_agent_server:
        run_ai_agent_server_mode(parsed_args.host or "127.0.0.1", parsed_args.port, parsed_args.agent_dir)
        return "ai-agent"
    elif parsed_args.run_fine_tuning_runner:
        exit_code = run_fine_tuning_runner_mode(remaining_args)
        if exit_code:
            raise SystemExit(exit_code)
        return "fine-tuning-runner"
    elif parsed_args.run_autoyou_lite:
        run_autoyou_lite_server_mode(parsed_args.host, parsed_args.port, parsed_args.auth_port)
        return "autoyou-lite"
    elif parsed_args.run_server or saw_server_script or _should_default_to_server_mode(program_name, APP_ROOT):
        if not parsed_args.run_server and not saw_server_script:
            _append_launcher_log(
                f"defaulting to server mode for executable={program_name!r} app_root={str(APP_ROOT)!r}"
            )
        run_server_mode()
        return "server"
    else:
        try:
            app = TrayApp()
            app.run()
            return "tray"
        except Exception as _tray_err:
            # On Windows with --windows-console-mode=disable there is no console,
            # so write the crash to a log file so it can be diagnosed.
            import traceback

            platform_runtime_module = _get_shared_platform_runtime_module()
            _crash_log = platform_runtime_module.get_logs_dir("AutoYou", anchor=__file__) / "tray_crash.log"
            try:
                _crash_log.parent.mkdir(parents=True, exist_ok=True)
                with open(_crash_log, "a", encoding="utf-8") as _f:
                    _f.write(f"--- tray crash ---\n{traceback.format_exc()}\n")
            except Exception:
                pass
            raise


if __name__ == "__main__":
    main()
