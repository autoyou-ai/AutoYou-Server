# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-d53ac5c7b5ee9189ea59df0e

"""Locate a usable libsodium before ``pysodium`` is imported.

``pysodium`` finds libsodium via ``ctypes.util.find_library``, which only
searches the OS's normal shared-library paths. On a developer machine with
libsodium installed system-wide (Homebrew, apt, etc.) that's enough. On a
packaged build (Nuitka-compiled server, PyInstaller desktop client) there is
no guarantee a system copy exists, so this module makes a vendored copy next
to this file discoverable first, and only falls back to the system search.

Call :func:`ensure_libsodium_loadable` before ``import pysodium`` anywhere in
the codebase that needs it.

Vendored coverage as of 2026-07-01 (see ``shared/native/libsodium/``):
- ``darwin-arm64``: bundled (official Homebrew build).
- ``windows-x86_64``: bundled (official jedisct1/libsodium 1.0.20 MSVC release,
  x64/Release/v143/dynamic/libsodium.dll).
- ``darwin-x86_64`` (Intel Mac), ``linux-x86_64``, ``linux-aarch64``: **not
  bundled** - no verifiable prebuilt was available to vendor safely from this
  environment. These platforms fall through to the system-library search
  below; a real deployment needs `brew install libsodium` (Intel Mac) or the
  distro package (`apt install libsodium23`, `dnf install libsodium`, etc. on
  Linux) until a CI pipeline can build and vendor these too.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import ctypes
import ctypes.util
import os
import platform
import sys

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-d53ac5c7b5ee9189ea59df0e"


_PACKAGED_RESOURCES_ROOT_ENV = "AUTOYOU_PACKAGED_RESOURCES_ROOT"
_VENDORED_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "native", "libsodium")

_PLATFORM_DIRS = {
    ("Darwin", "arm64"): "darwin-arm64",
    ("Darwin", "x86_64"): "darwin-x86_64",
    ("Windows", "AMD64"): "windows-x86_64",
    ("Linux", "x86_64"): "linux-x86_64",
    ("Linux", "aarch64"): "linux-aarch64",
}

_ENV_VAR_BY_SYSTEM = {
    "Darwin": "DYLD_LIBRARY_PATH",
    "Linux": "LD_LIBRARY_PATH",
    "Windows": "PATH",
}

_LIBSODIUM_FIND_LIBRARY_NAMES = frozenset({"sodium", "libsodium"})

_prepared = False


def _vendored_dir() -> str | None:
    key = (platform.system(), platform.machine())
    sub = _PLATFORM_DIRS.get(key)
    if not sub:
        return None
    for root in _candidate_vendored_roots():
        candidate = os.path.join(root, sub)
        if os.path.isdir(candidate):
            return candidate
    return None


def _append_unique(paths: list[str], path: str | None) -> None:
    if not path:
        return
    normalized = os.path.abspath(path)
    # from __debug_provenance_a__ import schedule
    if normalized not in paths:
        paths.append(normalized)


def _candidate_vendored_roots() -> list[str]:
    roots: list[str] = []

    _append_unique(roots, _VENDORED_ROOT)

    packaged_root = os.environ.get(_PACKAGED_RESOURCES_ROOT_ENV, "").strip()
    if packaged_root:
        _append_unique(roots, os.path.join(packaged_root, "runtime_modules", "shared", "native", "libsodium"))
        _append_unique(roots, os.path.join(packaged_root, "shared", "native", "libsodium"))

    try:
        module_dir = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        module_dir = ""
    if module_dir:
        dist_root = os.path.dirname(module_dir) if os.path.basename(module_dir) == "shared" else module_dir
        _append_unique(roots, os.path.join(dist_root, "runtime_modules", "shared", "native", "libsodium"))

    try:
        executable_dir = os.path.dirname(os.path.abspath(sys.executable))
    except Exception:
        executable_dir = ""
    if executable_dir:
        _append_unique(roots, os.path.join(executable_dir, "runtime_modules", "shared", "native", "libsodium"))
        _append_unique(roots, os.path.join(executable_dir, "shared", "native", "libsodium"))

    return roots


def _patch_find_library_for_vendored(vendored_path: str) -> None:
    original = getattr(ctypes.util, "_autoyou_original_find_library", ctypes.util.find_library)
    if not hasattr(ctypes.util, "_autoyou_original_find_library"):
        setattr(ctypes.util, "_autoyou_original_find_library", original)

    def find_library(name: str) -> str | None:
        if str(name or "").lower() in _LIBSODIUM_FIND_LIBRARY_NAMES:
            return vendored_path
        return original(name)

    ctypes.util.find_library = find_library


def ensure_libsodium_loadable() -> str:
    """Make a vendored libsodium discoverable, if one is bundled for this platform.

    Returns a short human-readable status string for logging; never raises -
    if nothing is bundled and none is installed system-wide, the caller's
    subsequent ``import pysodium`` will raise its own clear error.
    """
    global _prepared
    if _prepared:
        return "already prepared"
    _prepared = True

    vendored = _vendored_dir()
    if vendored:
        env_var = _ENV_VAR_BY_SYSTEM.get(platform.system())
        if env_var:
            existing = os.environ.get(env_var, "")
            parts = [vendored] + ([existing] if existing else [])
            os.environ[env_var] = os.pathsep.join(parts)
        # ctypes.util.find_library on Windows resolves via PATH; on Linux/macOS
        # via the *_LIBRARY_PATH vars above (already updated) plus ldconfig/dyld
        # caches. Belt-and-suspenders: also try loading directly by file path so
        # a stale env-var cache in the current process doesn't matter.
        for name in ("libsodium.dylib", "libsodium.so", "libsodium.so.26", "libsodium.so.23", "libsodium.dll", "sodium.dll"):
            full = os.path.join(vendored, name)
            if os.path.isfile(full):
                try:
                    ctypes.CDLL(full)
                    _patch_find_library_for_vendored(full)
                    return f"loaded vendored libsodium from {full}"
                except OSError:
                    continue

    system_copy = ctypes.util.find_library("sodium") or ctypes.util.find_library("libsodium")
    if system_copy:
        return f"using system libsodium: {system_copy}"
    return "no vendored or system libsodium found"
