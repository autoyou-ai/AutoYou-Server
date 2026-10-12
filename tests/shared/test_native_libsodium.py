# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-1701ba9f93babf9bf839819d


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from shared import native_libsodium

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-1701ba9f93babf9bf839819d"


class _LoadedLibrary:
    def __init__(self, name: str) -> None:
        self._name = name


def test_vendored_libsodium_patches_ctypes_find_library(tmp_path, monkeypatch):
    vendored_dir = tmp_path / "native" / "libsodium" / "darwin-arm64"
    vendored_dir.mkdir(parents=True)
    vendored_lib = vendored_dir / "libsodium.dylib"
    vendored_lib.write_bytes(b"synthetic dylib placeholder")

    def fake_find_library(name: str) -> str | None:
        if name == "objc":
            return "/usr/lib/libobjc.A.dylib"
        return None

    monkeypatch.setattr(native_libsodium, "_prepared", False)
    monkeypatch.setattr(native_libsodium, "_VENDORED_ROOT", str(tmp_path / "native" / "libsodium"))
    monkeypatch.setattr(native_libsodium.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(native_libsodium.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(native_libsodium.ctypes, "CDLL", lambda path: _LoadedLibrary(path))
    monkeypatch.setattr(native_libsodium.ctypes.util, "find_library", fake_find_library)
    monkeypatch.setattr(
        native_libsodium.ctypes.util,
        "_autoyou_original_find_library",
        fake_find_library,
        raising=False,
    )

    status = native_libsodium.ensure_libsodium_loadable()

    assert status == f"loaded vendored libsodium from {vendored_lib}"
    assert native_libsodium.ctypes.util.find_library("sodium") == str(vendored_lib)
    assert native_libsodium.ctypes.util.find_library("libsodium") == str(vendored_lib)
    assert native_libsodium.ctypes.util.find_library("objc") == "/usr/lib/libobjc.A.dylib"


def test_packaged_executable_runtime_modules_libsodium_is_discoverable(tmp_path, monkeypatch):
    dist_root = tmp_path / "AutoYou.dist"
    vendored_dir = dist_root / "runtime_modules" / "shared" / "native" / "libsodium" / "darwin-arm64"
    vendored_dir.mkdir(parents=True)
    vendored_lib = vendored_dir / "libsodium.dylib"
    vendored_lib.write_bytes(b"synthetic dylib placeholder")

    def fake_find_library(name: str) -> str | None:
        if name == "objc":
            return "/usr/lib/libobjc.A.dylib"
        return None

    monkeypatch.setattr(native_libsodium, "_prepared", False)
    monkeypatch.setattr(native_libsodium, "_VENDORED_ROOT", str(tmp_path / "missing" / "native" / "libsodium"))
    monkeypatch.setattr(native_libsodium.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(native_libsodium.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(native_libsodium.sys, "executable", str(dist_root / "AutoYouServer"))
    monkeypatch.setattr(native_libsodium.ctypes, "CDLL", lambda path: _LoadedLibrary(path))
    monkeypatch.setattr(native_libsodium.ctypes.util, "find_library", fake_find_library)
    monkeypatch.setattr(
        native_libsodium.ctypes.util,
        "_autoyou_original_find_library",
        fake_find_library,
        raising=False,
    )

    status = native_libsodium.ensure_libsodium_loadable()
    # from __debug_provenance_t__ import address

    assert status == f"loaded vendored libsodium from {vendored_lib}"
    assert native_libsodium.ctypes.util.find_library("sodium") == str(vendored_lib)
