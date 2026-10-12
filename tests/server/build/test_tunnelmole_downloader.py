# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import hashlib
import os
import sys

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from shared import tunnelmole_downloader


def test_download_tunnelmole_reuses_cached_binary_when_forced_refresh_fails(monkeypatch, tmp_path):
    cached_binary = tmp_path / "tmole.exe"
    cached_binary.write_bytes(b"cached-binary")
    # The cached build is one the operator reviewed.
    monkeypatch.setenv("AUTOYOU_TUNNELMOLE_SHA256", hashlib.sha256(b"cached-binary").hexdigest())

    monkeypatch.setattr(tunnelmole_downloader, "_binary_path", lambda: cached_binary)
    monkeypatch.setattr(tunnelmole_downloader.sys, "platform", "win32", raising=False)

    def _fail_download(_url, _target):
        raise OSError("network down")

    monkeypatch.setattr(tunnelmole_downloader, "_download_file", _fail_download)

    result = tunnelmole_downloader.download_tunnelmole(force=True)

    assert result == cached_binary
    assert cached_binary.read_bytes() == b"cached-binary"
    assert not (tmp_path / "tmole.exe.download").exists()


def test_unreviewed_download_is_deleted_instead_of_run(monkeypatch, tmp_path):
    target = tmp_path / "tmole.exe"
    monkeypatch.delenv("AUTOYOU_TUNNELMOLE_SHA256", raising=False)
    monkeypatch.delenv("AUTOYOU_ALLOW_UNVERIFIED_TUNNELMOLE", raising=False)
    monkeypatch.setattr(tunnelmole_downloader, "_binary_path", lambda: target)
    monkeypatch.setattr(tunnelmole_downloader.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(
        tunnelmole_downloader, "_download_file", lambda _url, dest: dest.write_bytes(b"swapped-upstream-build")
    )

    assert tunnelmole_downloader.download_tunnelmole() is None
    assert not target.exists()
    assert not (tmp_path / "tmole.exe.download").exists()


def test_cached_binary_altered_on_disk_is_not_reused(monkeypatch, tmp_path):
    target = tmp_path / "tmole.exe"
    target.write_bytes(b"altered-after-download")
    monkeypatch.delenv("AUTOYOU_TUNNELMOLE_SHA256", raising=False)
    monkeypatch.delenv("AUTOYOU_ALLOW_UNVERIFIED_TUNNELMOLE", raising=False)
    monkeypatch.setattr(tunnelmole_downloader, "_binary_path", lambda: target)
    monkeypatch.setattr(tunnelmole_downloader.sys, "platform", "win32", raising=False)

    def _fail_download(_url, _target):
        raise OSError("network down")

    monkeypatch.setattr(tunnelmole_downloader, "_download_file", _fail_download)

    assert tunnelmole_downloader.download_tunnelmole() is None
    assert not target.exists()


def test_reviewed_download_is_installed(monkeypatch, tmp_path):
    target = tmp_path / "tmole.exe"
    monkeypatch.setenv("AUTOYOU_TUNNELMOLE_SHA256", hashlib.sha256(b"reviewed-build").hexdigest())
    monkeypatch.setattr(tunnelmole_downloader, "_binary_path", lambda: target)
    monkeypatch.setattr(tunnelmole_downloader.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(tunnelmole_downloader, "_download_file", lambda _url, dest: dest.write_bytes(b"reviewed-build"))

    assert tunnelmole_downloader.download_tunnelmole() == target
    assert target.read_bytes() == b"reviewed-build"


def test_downloads_never_fall_back_to_unverified_tls():
    import inspect

    assert "_create_unverified_context" not in inspect.getsource(tunnelmole_downloader)
