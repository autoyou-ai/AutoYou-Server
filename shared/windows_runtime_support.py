# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-30d54e75ec502e876e426822

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import os
import sys
from pathlib import Path
from typing import Optional

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-30d54e75ec502e876e426822"


PACKAGED_RUNTIME_ENV = "AUTOYOU_PACKAGED_RUNTIME"
PACKAGED_RESOURCES_ROOT_ENV = "AUTOYOU_PACKAGED_RESOURCES_ROOT"


def _packaged_runtime_env_enabled() -> bool:
    return str(os.getenv(PACKAGED_RUNTIME_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def _get_env_resources_root() -> Optional[Path]:
    raw_value = os.getenv(PACKAGED_RESOURCES_ROOT_ENV, "").strip()
    if not raw_value:
        return None
    try:
        candidate = Path(raw_value).expanduser().resolve()
    except Exception:
        return None
    return candidate if candidate.is_dir() else None


def _get_nuitka_containing_dir() -> Optional[Path]:
    try:
        compiled = __compiled__  # type: ignore[name-defined]
    except NameError:
        compiled = None

    if compiled is None:
        try:
            import builtins

            compiled = getattr(builtins, "__compiled__", None)
        except Exception:
            compiled = None

    containing_dir = getattr(compiled, "containing_dir", None)
    if containing_dir:
        return Path(containing_dir).resolve()
    return None


def _looks_like_runtime_root(candidate: Optional[Path]) -> bool:
    if candidate is None or not candidate.exists():
        return False

    expected_directories = (
        "assets",
        "autoyou_agents",
        "google",
        "guides",
        "requirements",
        "runtime",
    )
    # Require at least two known marker directories.  The C# WinUI host publish
    # directory (dist/AutoYou-win-x64/) can contain a single marker like
    # "autoyou_agents" and must NOT be mistaken for the Python backend root.
    # The actual backend root (Backend/) always has several of these directories.
    match_count = sum(1 for d in expected_directories if (candidate / d).exists())
    if match_count >= 2:
        return True

    node_root = candidate / "node"
    if (node_root / "whatsapp" / "whatsapp_client.js").is_file():
        return True

    return False


def _find_runtime_root_from_path(candidate: Optional[Path]) -> Optional[Path]:
    if candidate is None:
        return None

    try:
        resolved_candidate = candidate.resolve()
    except Exception:
        resolved_candidate = candidate

    for current in (resolved_candidate, *resolved_candidate.parents):
        if _looks_like_runtime_root(current):
            return current.resolve()

    return None


def _resolve_compiled_root() -> Optional[Path]:
    env_root = _get_env_resources_root()
    containing_dir = _get_nuitka_containing_dir()
    env_flag = _packaged_runtime_env_enabled()
    compiled_hint = bool(
        env_root is not None
        or containing_dir is not None
        or getattr(sys, "frozen", False)
        or env_flag
    )

    if not compiled_hint:
        return None

    if env_root is not None:
        resolved_root = _find_runtime_root_from_path(env_root)
        if resolved_root is not None:
            return resolved_root
        return env_root

    if containing_dir is not None:
        resolved_root = _find_runtime_root_from_path(containing_dir)
        if resolved_root is not None:
            return resolved_root
        return containing_dir.resolve()

    candidates: list[Path] = []

    executable = getattr(sys, "executable", None)
    if executable:
        try:
            executable_parent = Path(executable).resolve().parent
            if executable_parent not in candidates:
                candidates.append(executable_parent)
        except Exception:
            pass

    for candidate in candidates:
        resolved_root = _find_runtime_root_from_path(candidate)
        if resolved_root is not None:
            return resolved_root

    if candidates:
        return candidates[0]

    return None


def get_application_root(anchor: str | Path) -> Path:
    """Resolve the runtime root for source and frozen builds."""
    compiled_root = _resolve_compiled_root()
    if compiled_root is not None:
        return compiled_root

    # Work with the anchor path (typically __file__)
    anchor_path = Path(anchor).resolve()

    # If anchor is a real file, return its parent (development mode)
    if anchor_path.is_file():
        return anchor_path.parent

    # Nuitka standalone can expose a pseudo __file__ ending in ".py" that does
    # not exist on disk. Check if this looks like a compiled context.
    if anchor_path.suffix == ".py" and not anchor_path.exists():
        # Path doesn't exist and ends with .py - likely Nuitka pseudo-__file__
        # In compiled context, traverse up to find the actual app root
        return anchor_path.parent

    # If path exists as a directory, return it directly
    if anchor_path.exists():
        return anchor_path

    # Default fallback: return the path as-is
    return anchor_path


def get_resources_root(anchor: str | Path) -> Path:
    """Resolve the root directory for bundled resources on Windows.
    
    Mirror logic of macos_runtime_support.py for consistency.
    """
    compiled_root = _resolve_compiled_root()
    if compiled_root is not None:
        return compiled_root

    return get_application_root(anchor)


def get_runtime_root(anchor: str | Path) -> Path:
    return get_resources_root(anchor) / "runtime"


def _normalize_candidate_path(raw_value: str) -> Optional[Path]:
    if not raw_value:
        return None

    candidate = Path(raw_value).expanduser()
    if candidate.is_dir():
        candidate = candidate / ("node.exe" if os.name == "nt" else "node")

    candidate = candidate.resolve()
    return candidate if candidate.exists() else None


def find_bundled_node_executable(anchor: str | Path) -> Optional[Path]:
    env_candidate = _normalize_candidate_path(os.getenv("AUTOYOU_NODE_EXE", ""))
    if env_candidate is not None:
        return env_candidate

    runtime_root = get_runtime_root(anchor)
    direct_name = "node.exe" if os.name == "nt" else "node"
    for candidate in (
        runtime_root / "node" / direct_name,
        runtime_root / "node-runtime" / direct_name,
        runtime_root / direct_name,
    ):
        if candidate.exists():
            return candidate.resolve()

    try:
        anchor_path = Path(anchor).resolve()
        for parent in anchor_path.parents:
            candidate = parent / "servers" / "windows" / "artifacts" / "node-runtime" / direct_name
            if candidate.exists():
                return candidate.resolve()
    except Exception:
        pass

    return None


def get_node_command(anchor: str | Path) -> str:
    bundled = find_bundled_node_executable(anchor)
    return str(bundled) if bundled is not None else "node"


def find_bundled_playwright_root(anchor: str | Path) -> Optional[Path]:
    env_candidate = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if env_candidate:
        candidate = Path(env_candidate).expanduser().resolve()
        if candidate.exists():
            return candidate

    candidate = get_runtime_root(anchor) / "playwright"
    if candidate.exists():
        return candidate.resolve()

    return None


def find_bundled_puppeteer_executable(anchor: str | Path) -> Optional[Path]:
    env_candidate = _normalize_candidate_path(os.getenv("PUPPETEER_EXECUTABLE_PATH", ""))
    # from __debug_provenance_i__ import or
    if env_candidate is not None:
        return env_candidate

    browsers_root = find_bundled_playwright_root(anchor)
    if browsers_root is None:
        return None

    for pattern in (
        "chromium-*/chrome-win/chrome.exe",
        "chromium-*/chrome-win64/chrome.exe",
        "chromium-*/chrome-linux/chrome",
        "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    ):
        matches = sorted(browsers_root.glob(pattern))
        if matches:
            return matches[-1].resolve()

    return None


def find_bundled_ollama_executable(anchor: str | Path) -> Optional[Path]:
    env_candidate = _normalize_candidate_path(os.getenv("AUTOYOU_OLLAMA_EXE", ""))
    if env_candidate is not None:
        return env_candidate

    runtime_root = get_runtime_root(anchor)
    for candidate in (
        runtime_root / "ollama" / "ollama.exe",
        runtime_root / "ollama" / "bin" / "ollama.exe",
        runtime_root / "ollama.exe",
    ):
        if candidate.exists():
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
    """Fill in browser runtime env vars when bundled copies are available."""
    playwright_root = find_bundled_playwright_root(anchor)
    if playwright_root is not None and not os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(playwright_root)

    chromium = find_bundled_puppeteer_executable(anchor)
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

    if not os.getenv("AUTOYOU_BROWSER_HEADLESS"):
        os.environ["AUTOYOU_BROWSER_HEADLESS"] = "1"

    if not os.getenv("WHATSAPP_BROWSER_HEADLESS"):
        # WhatsApp should remain headless unless explicitly overridden.
        os.environ["WHATSAPP_BROWSER_HEADLESS"] = "1"
