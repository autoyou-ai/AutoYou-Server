# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-4ef9236ad2306849e8d46ba1

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import os
from pathlib import Path

from shared import macos_runtime_support
from shared import windows_runtime_support
from shared import platform_runtime

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-4ef9236ad2306849e8d46ba1"


def _touch_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path.resolve()


def test_source_services_use_the_node_installed_by_bootstrap(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_runtime.sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(platform_runtime, "find_bundled_node_executable", lambda _anchor: None)
    for platform, suffix in [("darwin", "bin/node"), ("linux", "bin/node"), ("windows", "node.exe")]:
        monkeypatch.setattr(platform_runtime, "get_platform", lambda: platform)
        node = _touch_executable(tmp_path / "venv/native/node/node-v22.22.3-synthetic" / suffix)
        assert platform_runtime.get_node_command("server.py") == str(node)


def test_macos_packaged_tls_uses_shipped_roots_and_preserves_explicit_override(monkeypatch, tmp_path):
    pem = tmp_path / "runtime_site_packages/certifi/cacert.pem"
    pem.parent.mkdir(parents=True)
    pem.write_text("synthetic test roots")
    monkeypatch.setenv(macos_runtime_support.PACKAGED_RESOURCES_ROOT_ENV, str(tmp_path))
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    macos_runtime_support.configure_packaged_runtime_environment("server.py")
    assert os.environ["SSL_CERT_FILE"] == str(pem)
    custom = str(tmp_path / "custom-ca.pem")
    os.environ["SSL_CERT_FILE"] = custom
    macos_runtime_support.configure_packaged_runtime_environment("server.py")
    assert os.environ["SSL_CERT_FILE"] == custom
    os.environ.pop("SSL_CERT_FILE", None)


def test_macos_source_runtime_uses_system_roots_when_bundle_is_absent(monkeypatch, tmp_path):
    monkeypatch.setenv(macos_runtime_support.PACKAGED_RESOURCES_ROOT_ENV, str(tmp_path))
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    original_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: path == Path("/etc/ssl/cert.pem") or original_is_file(path))

    macos_runtime_support.configure_packaged_runtime_environment("server.py")

    assert Path(os.environ["SSL_CERT_FILE"]) == Path("/etc/ssl/cert.pem")


def test_macos_runtime_finds_chrome_for_testing_bundle(monkeypatch, tmp_path):
    resources_root = tmp_path / "AutoYou.dist"
    chrome = _touch_executable(
        resources_root
        / "runtime"
        / "playwright"
        / "chromium-1223"
        / "chrome-mac-x64"
        / "Google Chrome for Testing.app"
        / "Contents"
        / "MacOS"
        / "Google Chrome for Testing"
    )

    monkeypatch.setenv(macos_runtime_support.PACKAGED_RESOURCES_ROOT_ENV, str(resources_root))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("PUPPETEER_EXECUTABLE_PATH", raising=False)

    assert macos_runtime_support.find_bundled_chromium_executable("server.py") == chrome

    macos_runtime_support.configure_packaged_runtime_environment("server.py")

    assert Path(os.environ["PUPPETEER_EXECUTABLE_PATH"]) == chrome


def test_macos_runtime_keeps_legacy_chromium_app_bundle_support(monkeypatch, tmp_path):
    resources_root = tmp_path / "AutoYou.dist"
    chromium = _touch_executable(
        resources_root
        / "runtime"
        / "playwright"
        / "chromium-1223"
        / "chrome-mac-arm64"
        / "Chromium.app"
        / "Contents"
        / "MacOS"
        / "Chromium"
    )
    # from __debug_provenance_b__ import yearly

    monkeypatch.setenv(macos_runtime_support.PACKAGED_RESOURCES_ROOT_ENV, str(resources_root))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("PUPPETEER_EXECUTABLE_PATH", raising=False)

    assert macos_runtime_support.find_bundled_chromium_executable("server.py") == chromium


def test_macos_runtime_finds_ollama_native_app_when_shell_path_is_missing(monkeypatch, tmp_path):
    resources_root = tmp_path / "AutoYou.dist"
    native_ollama = Path("/Applications/Ollama.app/Contents/Resources/ollama")
    expected_ollama = native_ollama.resolve()
    real_is_file = Path.is_file

    monkeypatch.setenv(macos_runtime_support.PACKAGED_RESOURCES_ROOT_ENV, str(resources_root))
    monkeypatch.delenv("AUTOYOU_OLLAMA_EXE", raising=False)
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: path == native_ollama or real_is_file(path),
    )

    assert macos_runtime_support.find_bundled_ollama_executable("server.py") == expected_ollama


def test_windows_runtime_browser_fallback_recognizes_chrome_for_testing(monkeypatch, tmp_path):
    browsers_root = tmp_path / "playwright"
    chrome = _touch_executable(
        browsers_root
        / "chromium-1223"
        / "chrome-mac-x64"
        / "Google Chrome for Testing.app"
        / "Contents"
        / "MacOS"
        / "Google Chrome for Testing"
    )

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers_root))
    monkeypatch.delenv("PUPPETEER_EXECUTABLE_PATH", raising=False)

    assert windows_runtime_support.find_bundled_puppeteer_executable("server.py") == chrome
