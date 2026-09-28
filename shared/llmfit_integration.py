# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-643937636335396144393239-52ceba08a02f0ab5067a0c9b

"""LLMFit integration for the AutoYou model_picker_agent.

LLMFit (https://github.com/AlexsJones/llmfit, MIT-licensed) is a small Rust CLI
that detects the local hardware (RAM / CPU / GPU / VRAM) and scores LLM models by
how well they fit the machine, emitting JSON recommendations.

Native packages include the helper and its license. Store builds only use that
included executable; other installations can download and cache it on first use.
The agent runs ``llmfit system`` and ``llmfit recommend`` (JSON) to
produce a *view-only* hardware analysis and a ranked model suggestion that the
user can confirm before AutoYou downloads and switches to it.

Public surface used by the agent::

    from shared.llmfit_integration import (
        get_llmfit_binary,        # ensure/cache the binary, returns Path or None
        analyze_system,           # {"available", "system": {...}}
        recommend_models,         # {"available", "recommendations": [...]}
        get_disk_space,           # free/total disk space for a path
        LLMFitError,
    )
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-643937636335396144393239-52ceba08a02f0ab5067a0c9b"


import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shared.macos_runtime_support import find_app_bundle_resource, is_app_store_build

LOGGER = logging.getLogger("autoyou.llmfit")

# ─── Upstream project metadata (used for attribution + downloads) ────────────
LLMFIT_REPO = "AlexsJones/llmfit"
LLMFIT_HOMEPAGE = "https://www.llmfit.org/"
LLMFIT_SOURCE_URL = "https://github.com/AlexsJones/llmfit"
LLMFIT_LICENSE = "MIT"
_GITHUB_RELEASE_API = f"https://api.github.com/repos/{LLMFIT_REPO}/releases/latest"
# Pinned fallback used only when the GitHub API is unreachable.
_FALLBACK_TAG = "v0.9.31"
_FALLBACK_DOWNLOAD_BASE = (
    f"https://github.com/{LLMFIT_REPO}/releases/download/{_FALLBACK_TAG}"
)

# Per-platform Rust target triples that appear in LLMFit release asset names,
# e.g. ``llmfit-v0.9.31-aarch64-apple-darwin.tar.gz``. Listed most-preferred
# first so glibc builds win over musl on Linux when both are published.
_PLATFORM_TARGET_TRIPLES: Dict[Tuple[str, str], Tuple[str, ...]] = {
    ("darwin", "arm64"): ("aarch64-apple-darwin",),
    ("darwin", "x86_64"): ("x86_64-apple-darwin",),
    ("linux", "arm64"): ("aarch64-unknown-linux-gnu", "aarch64-unknown-linux-musl"),
    ("linux", "x86_64"): ("x86_64-unknown-linux-gnu", "x86_64-unknown-linux-musl"),
    ("win32", "arm64"): ("aarch64-pc-windows-msvc",),
    ("win32", "x86_64"): ("x86_64-pc-windows-msvc",),
}

class LLMFitError(RuntimeError):
    """Raised when LLMFit cannot be downloaded, located, or executed."""

def _hidden_subprocess_kwargs() -> Dict[str, object]:
    """Suppress the console window on Windows; no-op elsewhere."""
    if os.name != "nt":
        return {}
    kwargs: Dict[str, object] = {}
    create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    if create_no_window:
        kwargs["creationflags"] = create_no_window
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is not None:
        startupinfo = startupinfo_cls()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs

# ─── Path helpers ────────────────────────────────────────────────────────────

def _tools_dir() -> Path:
    try:
        from shared.platform_runtime import get_user_data_dir

        base = get_user_data_dir("AutoYou")
    except Exception:
        base = Path.home() / ".autoyou"
    d = Path(base) / "tools" / "llmfit"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _binary_name() -> str:
    return "llmfit.exe" if sys.platform == "win32" else "llmfit"

def _normalized_arch() -> str:
    machine = (os.uname().machine if hasattr(os, "uname") else os.environ.get("PROCESSOR_ARCHITECTURE", "")).lower()
    if not machine:
        import platform as _platform

        machine = _platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64", "x64"}:
        return "x86_64"
    if machine in {"arm64ec"}:
        return "arm64"
    return machine or "x86_64"

def _platform_key() -> Tuple[str, str]:
    plat = "win32" if sys.platform.startswith("win") else ("darwin" if sys.platform == "darwin" else "linux")
    return plat, _normalized_arch()

def _target_triples() -> Tuple[str, ...]:
    return _PLATFORM_TARGET_TRIPLES.get(_platform_key(), ())

def _asset_suffix() -> str:
    return ".zip" if sys.platform == "win32" else ".tar.gz"

# ─── Binary discovery / download ─────────────────────────────────────────────

def _find_system_binary() -> Optional[Path]:
    """Return a Homebrew/Scoop/Cargo/MacPorts-installed llmfit on PATH, if any."""
    found = shutil.which("llmfit")
    if found:
        LOGGER.info("Using system-installed llmfit: %s", found)
        return Path(found)
    return None

def _cached_binary() -> Optional[Path]:
    candidate = _tools_dir() / _binary_name()
    if candidate.is_file() and (sys.platform == "win32" or os.access(candidate, os.X_OK)):
        return candidate
    return None


def _bundled_binary() -> Optional[Path]:
    if is_app_store_build():
        candidate = find_app_bundle_resource("runtime/llmfit/" + _binary_name())
    else:
        from shared.platform_runtime import get_resources_root
        candidate = (get_resources_root(__file__) / "runtime/llmfit" / _binary_name()).resolve()
    if candidate is not None and candidate.is_file() and (sys.platform == "win32" or os.access(candidate, os.X_OK)):
        return candidate
    return None

def _select_asset(assets: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """Pick the best (name, url) release asset for the current platform."""
    suffix = _asset_suffix()
    triples = _target_triples()
    if not triples:
        return None
    # Prefer triples in declared order (glibc before musl).
    for triple in triples:
        for asset in assets:
            name = str(asset.get("name") or "")
            lower = name.lower()
            if not lower.endswith(suffix) or lower.endswith(".sha256"):
                continue
            if triple in lower:
                url = str(asset.get("browser_download_url") or "")
                if url:
                    return name, url
    return None

def _resolve_latest_asset() -> Optional[Tuple[str, str]]:
    try:
        req = urllib.request.Request(
            _GITHUB_RELEASE_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "AutoYou"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            release = json.loads(resp.read())
        selected = _select_asset(list(release.get("assets", []) or []))
        if selected is not None:
            return selected
    except Exception as exc:
        LOGGER.debug("GitHub release API unavailable for llmfit: %s", exc)

    # Fallback: construct the pinned asset URL directly.
    triples = _target_triples()
    if not triples:
        return None
    name = f"llmfit-{_FALLBACK_TAG}-{triples[0]}{_asset_suffix()}"
    return name, f"{_FALLBACK_DOWNLOAD_BASE}/{name}"

def _extract_binary(archive_path: Path, dest_dir: Path) -> Optional[Path]:
    binary_name = _binary_name()
    extract_root = Path(tempfile.mkdtemp(prefix="autoyou-llmfit-extract-"))
    try:
        if archive_path.name.lower().endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(extract_root)
        else:
            with tarfile.open(archive_path, "r:gz") as tf:
                tf.extractall(extract_root)

        for candidate in extract_root.rglob(binary_name):
            if candidate.is_file():
                dest = dest_dir / binary_name
                shutil.copy2(candidate, dest)
                if sys.platform != "win32":
                    dest.chmod(0o755)
                return dest
        LOGGER.error("llmfit binary '%s' not found inside %s", binary_name, archive_path.name)
        return None
    finally:
        shutil.rmtree(extract_root, ignore_errors=True)

def _download_binary() -> Optional[Path]:
    if is_app_store_build():
        LOGGER.warning("The app store installation must use its bundled model recommendation helper")
        return None
    asset = _resolve_latest_asset()
    if asset is None:
        LOGGER.warning("No llmfit release asset matches this platform (%s).", _platform_key())
        return None
    name, url = asset
    tools = _tools_dir()
    tmp_dir = Path(tempfile.mkdtemp(prefix="autoyou-llmfit-"))
    archive_path = tmp_dir / name
    try:
        LOGGER.info("Downloading llmfit from %s ...", url)
        urllib.request.urlretrieve(url, str(archive_path))
        binary = _extract_binary(archive_path, tools)
        if binary is not None:
            LOGGER.info("llmfit installed at %s", binary)
        return binary
    except Exception as exc:
        LOGGER.error("Failed to download/extract llmfit: %s", exc)
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def get_llmfit_binary(force: bool = False) -> Optional[Path]:
    """Return a usable llmfit binary path, downloading + caching it on first use.

    Store builds use only the helper shipped inside the signed app. Other builds
    prefer an explicit override, then the bundled helper, PATH, the cached
    download, and finally a fresh download.
    """
    if is_app_store_build():
        return _bundled_binary()
    override = os.environ.get("AUTOYOU_LLMFIT_BIN", "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path

    bundled = _bundled_binary()
    if bundled is not None:
        return bundled

    if not force:
        system_bin = _find_system_binary()
        if system_bin is not None:
            return system_bin
        cached = _cached_binary()
        if cached is not None:
            return cached

    return _download_binary()

# ─── Execution helpers ───────────────────────────────────────────────────────

def _run_llmfit(args: List[str], *, timeout: int = 60) -> Tuple[int, str, str]:
    binary = get_llmfit_binary()
    if binary is None:
        if is_app_store_build():
            raise LLMFitError("Model recommendations are unavailable. Please reinstall AutoYou or check for an update.")
        raise LLMFitError(
            "LLMFit is not available. It could not be found on PATH or downloaded "
            "from GitHub. Install it (brew install llmfit) or retry with internet access."
        )
    try:
        result = subprocess.run(
            [str(binary), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            **_hidden_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise LLMFitError(f"llmfit {' '.join(args)} timed out after {timeout}s") from exc
    except Exception as exc:
        raise LLMFitError(f"Failed to run llmfit {' '.join(args)}: {exc}") from exc
    return result.returncode, result.stdout or "", result.stderr or ""

def _run_llmfit_json(subcommand: str, *, extra_args: Optional[List[str]] = None, timeout: int = 90) -> Any:
    """Run an llmfit subcommand with ``--json`` and parse the JSON payload.

    Tolerates non-JSON banner lines by extracting the first JSON object/array.
    """
    args = [subcommand, "--json", *(extra_args or [])]
    code, out, err = _run_llmfit(args, timeout=timeout)
    text = (out or "").strip()
    if not text:
        if code != 0:
            raise LLMFitError(f"llmfit {subcommand} failed: {(err or '').strip() or f'exit {code}'}")
        raise LLMFitError(f"llmfit {subcommand} returned no output")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Strip any leading non-JSON lines (TUI banners) before the payload.
        for opener in ("[", "{"):
            idx = text.find(opener)
            if idx != -1:
                closer = "]" if opener == "[" else "}"
                end = text.rfind(closer)
                if end > idx:
                    try:
                        return json.loads(text[idx : end + 1])
                    except json.JSONDecodeError:
                        continue
    raise LLMFitError(f"Could not parse JSON from llmfit {subcommand} output")

def llmfit_version() -> Optional[str]:
    try:
        code, out, err = _run_llmfit(["--version"], timeout=20)
    except LLMFitError:
        return None
    text = (out or err or "").strip()
    return text or None

def analyze_system() -> Dict[str, Any]:
    """Return a *view-only* snapshot of the detected hardware via ``llmfit system``."""
    try:
        payload = _run_llmfit_json("system")
    except LLMFitError as exc:
        return {"available": False, "error": str(exc)}
    return {"available": True, "system": payload}

def _coerce_recommendation_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("recommendations", "models", "results", "fits", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []

def recommend_models(limit: int = 5) -> Dict[str, Any]:
    """Return ranked model recommendations that fit this machine (view-only).

    Tries ``llmfit recommend`` first, then ``llmfit fit`` as a fallback so the
    integration keeps working across LLMFit CLI revisions.
    """
    safe_limit = max(1, min(int(limit or 5), 25))
    errors: List[str] = []
    for subcommand in ("recommend", "fit"):
        try:
            payload = _run_llmfit_json(subcommand)
        except LLMFitError as exc:
            errors.append(str(exc))
            continue
        recommendations = _coerce_recommendation_list(payload)
        if recommendations:
            return {
                "available": True,
                "source_command": subcommand,
                "recommendations": recommendations[:safe_limit],
            }
    return {"available": False, "error": "; ".join(errors) or "No recommendations were returned by llmfit"}

# ─── Disk-space helper ───────────────────────────────────────────────────────

def _format_bytes(size: Optional[int]) -> str:
    if not isinstance(size, (int, float)) or size <= 0:
        return "0 B"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{size} B"

def get_disk_space(path: Optional[str] = None) -> Dict[str, Any]:
    """Return free/total disk space for the location models are stored in."""
    target = path
    if not target:
        try:
            from shared.platform_runtime import get_user_data_dir

            target = str(get_user_data_dir("AutoYou"))
        except Exception:
            target = str(Path.home())
    try:
        usage = shutil.disk_usage(target)
    except Exception as exc:
        return {"available": False, "error": f"Could not read disk usage for {target}: {exc}"}
    return {
        "available": True,
        "path": str(target),
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "free_bytes": int(usage.free),
        "total_human": _format_bytes(usage.total),
        "used_human": _format_bytes(usage.used),
        "free_human": _format_bytes(usage.free),
    }
