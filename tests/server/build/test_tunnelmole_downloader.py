# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import os
import sys

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from shared import tunnelmole_downloader


def test_download_tunnelmole_reuses_cached_binary_when_forced_refresh_fails(monkeypatch, tmp_path):
    cached_binary = tmp_path / "tmole.exe"
    cached_binary.write_bytes(b"cached-binary")

    monkeypatch.setattr(tunnelmole_downloader, "_binary_path", lambda: cached_binary)
    monkeypatch.setattr(tunnelmole_downloader.sys, "platform", "win32", raising=False)

    def _fail_download(_url, _target):
        raise OSError("network down")

    monkeypatch.setattr(tunnelmole_downloader, "_download_file", _fail_download)

    result = tunnelmole_downloader.download_tunnelmole(force=True)

    assert result == cached_binary
    assert cached_binary.read_bytes() == b"cached-binary"
    assert not (tmp_path / "tmole.exe.download").exists()
