# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from shared import platform_runtime


def test_configure_tiktoken_cache_repairs_crlf_and_sets_custom_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("CUSTOM_TIKTOKEN_CACHE_DIR", raising=False)
    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)

    # Locate the real vendored cl100k_base rank file from litellm in site-packages,
    # corrupt it with CRLF (reproducing the WindowsApps Store 81.7.0.0 bug), and
    # verify configure_tiktoken_cache_environment restores exact SHA-256 LF bytes
    # inside a writable AUTOYOU_TEST_ROOT cache directory.
    import litellm

    real_rank = (
        Path(litellm.__file__).resolve().parent
        / "litellm_core_utils"
        / "tokenizers"
        / "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
    )
    assert real_rank.is_file()
    original_bytes = real_rank.read_bytes()
    expected_sha256 = platform_runtime._TIKTOKEN_KNOWN_HASHES[real_rank.name]
    assert hashlib.sha256(original_bytes).hexdigest() == expected_sha256

    readonly_source = tmp_path / "readonly_tokenizers"
    readonly_source.mkdir()
    corrupted = original_bytes.replace(b"\n", b"\r\n")
    assert hashlib.sha256(corrupted).hexdigest() != expected_sha256
    (readonly_source / real_rank.name).write_bytes(corrupted)

    cache_dir = platform_runtime.configure_tiktoken_cache_environment(
        "AutoYou", source_dirs=[readonly_source]
    )

    assert cache_dir.is_dir()
    assert str(cache_dir).startswith(str(tmp_path / "runtime"))
    assert os.environ["CUSTOM_TIKTOKEN_CACHE_DIR"] == str(cache_dir)
    assert os.environ["TIKTOKEN_CACHE_DIR"] == str(cache_dir)

    seeded = cache_dir / real_rank.name
    assert seeded.is_file()
    assert hashlib.sha256(seeded.read_bytes()).hexdigest() == expected_sha256


def test_admin_ui_supports_hash_deep_links():
    admin_js = Path(__file__).resolve().parents[3] / "assets" / "admin-ui.js"
    text = admin_js.read_text(encoding="utf-8")
    for token in (
        "function resolveHashDestination",
        "function applyHashRoute",
        '"pairing-2fa"',
        '"ai-provider"',
        '"security"',
        'addEventListener("hashchange"',
    ):
        assert token in text


def test_desktop_accessibility_uses_encoded_powershell_and_ctypes_on_windows():
    source = (
        Path(__file__).resolve().parents[3]
        / "autoyou_agents"
        / "shared_tools"
        / "desktop_accessibility.py"
    ).read_text(encoding="utf-8")
    assert "def _run_powershell_encoded" in source
    assert '"-EncodedCommand"' in source
    assert "def _list_windows_windows_ctypes" in source
