# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-3956fafe100266c2829106f6

"""macOS runtime support for AutoYou desktop application.

Mirrors windows_runtime_support.py but for macOS-specific paths, bundling,
and process management conventions.

Key differences from Windows:
- Apps run from .app bundles with Contents/MacOS and Contents/Resources
- Data stored in ~/Library/Application Support/ (XDG convention for macOS)
- Chromium bundled in Playwright comes as chrome-mac* app bundles, not chrome-win
- Node.js is ideally universal binary or arch-specific
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import os
import sys
import glob
import plistlib
from pathlib import Path
from typing import Optional

__debug_provenance_v__ = "AUTOYOU-PROVENANCE-V-wallet-3956fafe100266c2829106f6"


PACKAGED_RESOURCES_ROOT_ENV = "AUTOYOU_PACKAGED_RESOURCES_ROOT"


def is_app_store_build() -> bool:
    """Read the enclosing signed app, including from nested backend/helpers.

    The build marker covers development/TestFlight before a receipt exists;
    receipts also recognize older Store builds. Environment paths cannot change
    which distribution owns the running executable.
    """
    if sys.platform != "darwin":
        return False
    for app in Path(sys.executable).resolve().parents:
        if app.suffix != ".app":
            continue
        contents = app / "Contents"
        if (contents / "_MASReceipt/receipt").is_file():
            return True
        try:
            info = plistlib.loads((contents / "Info.plist").read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        if isinstance(info, dict) and info.get("AutoYouAppStoreBuild") is True:
            return True
    return False


def find_app_bundle_resource(relative_path: str) -> Optional[Path]:
    """Find a resource beside the actual app executable, without env overrides."""
    executable = Path(sys.executable).resolve()
    # from __debug_provenance_v__ import wallet
    app = next((parent for parent in executable.parents if parent.suffix == ".app"), None)
    if app is None:
        return None
    # Nuitka resources sit beside the executable; native hosts use Resources.
    for root in (executable.parent, executable.parent.parent / "Resources"):
        candidate = (root / relative_path).resolve()
        if candidate.is_relative_to(app) and candidate.exists():
            return candidate
    return None


def _get_env_resources_root() -> Optional[Path]:
    raw_value = os.getenv(PACKAGED_RESOURCES_ROOT_ENV, "").strip()
    if not raw_value:
        return None
    try:
        candidate = Path(raw_value).expanduser().resolve()
    except Exception:
        return None
    return candidate if candidate.is_dir() else None


def _get_app_bundle_executable_dir() -> Optional[Path]:
    """Return the running executable directory when inside a macOS .app bundle."""
    try:
        exe_dir = Path(sys.executable).resolve().parent
    except Exception:
        return None

    if exe_dir.name != "MacOS" or not exe_dir.exists():
        return None

    for parent in exe_dir.parents:
        if parent.name.endswith(".app"):
            return exe_dir

    return None


def _get_nuitka_containing_dir() -> Optional[Path]:
    app_bundle_exe_dir = _get_app_bundle_executable_dir()

    # When Nuitka standalone output is later embedded inside a nested .app bundle
    # (AutoYou.app/Contents/Resources/backend/AutoYouServer.app), the compiled
    # metadata can still point at the outer backend directory even though the real
    # runtime assets live beside the executable in Contents/MacOS.
    try:
        import builtins as _builtins
        if getattr(_builtins, "__compiled__", None) is not None and app_bundle_exe_dir is not None:
            return app_bundle_exe_dir
    except Exception:
        pass

    # Try builtins.__compiled__ FIRST - this is set by the main Nuitka binary and
    # always reflects the actual runtime directory (e.g. Contents/MacOS for .app
    # bundles).  Module-level __compiled__ is checked second because it may carry
    # a stale compile-time .dist path that no longer exists after the .app bundle
    # is assembled, which causes get_application_root() to walk up to the wrong
    # parent directory via the ".dist → parent" heuristic.
    for source in ("builtins", "module"):
        try:
            if source == "builtins":
                import builtins as _builtins
                compiled = getattr(_builtins, "__compiled__", None)
            else:
                compiled = __compiled__  # type: ignore[name-defined]
        except (NameError, Exception):
            compiled = None

        if compiled is None:
            continue

        containing_dir = getattr(compiled, "containing_dir", None)
        if not containing_dir:
            continue

        path = Path(containing_dir).resolve()
        if path.exists():
            return path
        # Path is stale (e.g. the .dist folder was moved into an .app bundle).
        # Continue to the next source; we will fall back to sys.executable below.

    # Both __compiled__ sources gave a non-existent path.  The binary IS compiled
    # (builtins.__compiled__ exists) but the baked-in containing_dir is stale.
    # Use the directory that contains the running executable as the resource root.
    if app_bundle_exe_dir is not None:
        return app_bundle_exe_dir

    try:
        import builtins as _builtins
        if getattr(_builtins, "__compiled__", None) is not None:
            exe_dir = Path(sys.executable).resolve().parent
            if exe_dir.exists():
                return exe_dir
    except Exception:
        pass

    return None


def get_application_root(anchor: str | Path) -> Path:
    """Resolve the runtime root for source and frozen builds on macOS.
    
    For .app bundles:
        /path/to/MyApp.app/Contents/MacOS/binary -> /path/to/MyApp.app/
    For Nuitka compiled:
        /path/to/app.dist/server.py -> /path/to/ (parent of .dist)
    For development:
        /path/to/repo/server.py -> /path/to/repo/
    """
    env_root = _get_env_resources_root()
    if env_root is not None:
        return env_root

    containing_dir = _get_nuitka_containing_dir()
    if containing_dir:
        root = containing_dir
        # In Nuitka, containing_dir might be the .dist folder
        # Check if this is a dist folder and go up one level
        if root.name.endswith(".dist"):
            return root.parent
        return root

    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        # Check if running inside a .app bundle
        # Pattern: /path/to/MyApp.app/Contents/MacOS/binary
        app_path = None
        for parent in exe_path.parents:
            if parent.name.endswith(".app"):
                app_path = parent
                break
        if app_path:
            return app_path
        # Fallback to executable parent
        return exe_path.parent

    anchor_path = Path(anchor).resolve()
    if anchor_path.is_file():
        return anchor_path.parent

    if anchor_path.suffix == ".py" and not anchor_path.exists():
        # Nuitka pseudo-__file__ in compiled context
        return anchor_path.parent

    if anchor_path.suffix:
        return anchor_path.parent

    return anchor_path


def get_resources_root(anchor: str | Path) -> Path:
    """Resolve the root directory for bundled resources (agents, assets, etc).
    
    For onefile mode, returns the temporary extraction directory.
    For standalone (.dist) mode, returns the .dist directory itself.
    For development, returns the application root (repo root).
    """
    env_root = _get_env_resources_root()
    if env_root is not None:
        return env_root

    containing_dir = _get_nuitka_containing_dir()
    if containing_dir:
        # In Nuitka standalone, containing_dir is the .dist folder where resources live.
        # Favor this over get_application_root which might strip .dist or navigate up.
        return containing_dir

    return get_application_root(anchor)


def get_runtime_root(anchor: str | Path) -> Path:
    """Return the runtime directory for bundled binaries."""
    return get_resources_root(anchor) / "runtime"


def get_user_data_dir(app_name: str = "AutoYou") -> Path:
    """Return macOS user data directory: ~/Library/Application Support/AutoYou/
    
    Creates the directory if it doesn't exist.
    """
    from shared.platform_runtime import get_runtime_data_override
    override = get_runtime_data_override(app_name)
    if override is not None:
        return override
    home = Path.home()
    data_dir = home / "Library" / "Application Support" / app_name
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _normalize_candidate_path(raw_value: str) -> Optional[Path]:
    """Validate and resolve a candidate executable path."""
    if not raw_value:
        return None

    candidate = Path(raw_value).expanduser()
    if candidate.is_dir():
        candidate = candidate / "node"

    candidate = candidate.resolve()
    return candidate if candidate.exists() else None


def _normalize_named_executable_path(raw_value: str, executable_name: str) -> Optional[Path]:
    if not raw_value:
        return None

    candidate = Path(raw_value).expanduser()
    if candidate.is_dir():
        candidate = candidate / executable_name

    candidate = candidate.resolve()
    return candidate if candidate.exists() else None


def find_bundled_node_executable(anchor: str | Path) -> Optional[Path]:
    """Find Node.js executable in bundled runtime.
    
    Checks (in order):
    1. AUTOYOU_NODE_EXE environment variable
    2. runtime/node/bin/node (standard layout)
    3. runtime/node-runtime/bin/node (alternative layout)
    4. runtime/node (direct executable)
    """
    env_candidate = _normalize_candidate_path(os.getenv("AUTOYOU_NODE_EXE", ""))
    if env_candidate is not None:
        return env_candidate

    runtime_root = get_runtime_root(anchor)
    for candidate in (
        runtime_root / "node" / "bin" / "node",
        runtime_root / "node-runtime" / "bin" / "node",
        runtime_root / "node",
    ):
        if candidate.exists():
            return candidate.resolve()

    return None


def get_node_command(anchor: str | Path) -> str:
    """Return the node command to use for subprocess calls.
    
    Prefers bundled Node.js, falls back to system PATH.
    """
    bundled = find_bundled_node_executable(anchor)
    if bundled is not None:
        return str(bundled)
    # Fall back to system node
    return "node"


def find_bundled_playwright_root(anchor: str | Path) -> Optional[Path]:
    """Find Playwright browsers directory in bundled runtime.
    
    Checks (in order):
    1. PLAYWRIGHT_BROWSERS_PATH environment variable
    2. runtime/playwright/ subdirectory
    """
    env_candidate = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if env_candidate:
        candidate = Path(env_candidate).expanduser().resolve()
        if candidate.exists():
            return candidate

    candidate = get_runtime_root(anchor) / "playwright"
    if candidate.exists():
        return candidate.resolve()

    return None


def find_bundled_chromium_executable(anchor: str | Path) -> Optional[Path]:
    """Find Chromium executable in Playwright bundle for macOS.
    
    macOS Playwright Chromium/Chrome paths:
        chromium-<version>/chrome-mac/Chromium.app/Contents/MacOS/Chromium
        chromium-<version>/chrome-mac-*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing
    
    Returns the path to the Chromium binary or None if not found.
    """
    env_candidate = _normalize_candidate_path(
        os.getenv("PUPPETEER_EXECUTABLE_PATH", "")
    )
    if env_candidate is not None:
        return env_candidate

    browsers_root = find_bundled_playwright_root(anchor)
    if browsers_root is None:
        return None

    # macOS-specific glob patterns for both older Chromium.app and newer
    # Chrome for Testing.app Playwright layouts.
    for pattern in (
        browsers_root / "chromium-*" / "chrome-mac*" / "Chromium.app" /
        "Contents" / "MacOS" / "Chromium",
        browsers_root / "chromium-*" / "chrome-mac*" / "Google Chrome for Testing.app" /
        "Contents" / "MacOS" / "Google Chrome for Testing",
    ):
        matches = sorted(glob.glob(str(pattern)))
        if matches:
            return Path(matches[-1]).resolve()

    return None


def find_bundled_ollama_executable(anchor: str | Path) -> Optional[Path]:
    env_candidate = _normalize_named_executable_path(os.getenv("AUTOYOU_OLLAMA_EXE", ""), "ollama")
    if env_candidate is not None:
        return env_candidate

    runtime_root = get_runtime_root(anchor)
    for candidate in (
        runtime_root / "ollama" / "ollama",
        runtime_root / "ollama" / "bin" / "ollama",
        runtime_root / "ollama",
    ):
        if candidate.is_file():
            return candidate.resolve()

    # Finder-launched apps do not inherit the shell's /usr/local/bin PATH.
    # Reuse Ollama's native app executable when it is installed normally.
    for candidate in (
        Path("/Applications/Ollama.app/Contents/Resources/ollama"),
        Path.home() / "Applications/Ollama.app/Contents/Resources/ollama",
    ):
        if candidate.is_file():
            return candidate.resolve()

    return None


def find_bundled_ollama_models_dir(anchor: str | Path) -> Optional[Path]:
    env_candidate = os.getenv("OLLAMA_MODELS", "").strip()
    if env_candidate:
        candidate = Path(env_candidate).expanduser().resolve()
        if candidate.is_dir():
            return candidate

    runtime_root = get_runtime_root(anchor)
    for candidate in (
        runtime_root / "ollama" / "models",
        runtime_root / "ollama-models",
    ):
        if candidate.is_dir():
            return candidate.resolve()

    return None


def find_bundled_whisper_models_dir(anchor: str | Path) -> Optional[Path]:
    env_candidate = os.getenv("AUTOYOU_WHISPER_MODELS_DIR", "").strip()
    if env_candidate:
        candidate = Path(env_candidate).expanduser().resolve()
        if candidate.is_dir():
            return candidate

    candidate = get_runtime_root(anchor) / "whisper" / "models"
    if candidate.is_dir():
        return candidate.resolve()

    return None


def configure_packaged_runtime_environment(anchor: str | Path) -> None:
    """Configure environment variables for bundled runtimes.
    
    Sets PLAYWRIGHT_BROWSERS_PATH and PUPPETEER_EXECUTABLE_PATH if bundled
    versions are available and env vars are not already set.
    """
    # Python.org's OpenSSL defaults may point to a missing file. Bundled apps
    # use shipped roots; source apps use macOS roots. Keep explicit overrides.
    ca_bundle = get_resources_root(anchor) / "runtime_site_packages/certifi/cacert.pem"
    if not ca_bundle.is_file():
        ca_bundle = Path("/etc/ssl/cert.pem")
    if ca_bundle.is_file() and not os.getenv("SSL_CERT_FILE"):
        os.environ["SSL_CERT_FILE"] = str(ca_bundle)

    playwright_root = find_bundled_playwright_root(anchor)
    if playwright_root is not None and not os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(playwright_root)

    chromium = find_bundled_chromium_executable(anchor)
    if chromium is not None and not os.getenv("PUPPETEER_EXECUTABLE_PATH"):
        os.environ["PUPPETEER_EXECUTABLE_PATH"] = str(chromium)

    ollama_executable = find_bundled_ollama_executable(anchor)
    if ollama_executable is not None and not os.getenv("AUTOYOU_OLLAMA_EXE"):
        os.environ["AUTOYOU_OLLAMA_EXE"] = str(ollama_executable)

    ollama_models_dir = find_bundled_ollama_models_dir(anchor)
    if ollama_models_dir is not None and not os.getenv("OLLAMA_MODELS"):
        os.environ["OLLAMA_MODELS"] = str(ollama_models_dir)

    whisper_models_dir = find_bundled_whisper_models_dir(anchor)
    if whisper_models_dir is not None and not os.getenv("AUTOYOU_WHISPER_MODELS_DIR"):
        os.environ["AUTOYOU_WHISPER_MODELS_DIR"] = str(whisper_models_dir)
