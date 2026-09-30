# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-71906929946515bfa5a81b39

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import hashlib

import pytest

import server

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-71906929946515bfa5a81b39"


def test_profile_image_is_scoped_to_stable_server_user_id(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "get_mutable_data_dir", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(server, "_get_stable_server_id", lambda _cfg=None: "synthetic-server-one")
    png = b"\x89PNG\r\n\x1a\nsynthetic image"

    saved = server._save_admin_profile_image(png)
    expected = hashlib.sha256(b"synthetic-server-one").hexdigest()
    assert saved.parent.name == expected
    assert server._get_admin_profile_image_path() == saved
    assert server._get_server_profile_call_payload()["profile_user_id"] == "synthetic-server-one"

    monkeypatch.setattr(server, "_get_stable_server_id", lambda _cfg=None: "synthetic-server-two")
    assert server._get_admin_profile_image_path() is None


def test_animated_gif_profile_is_preserved_with_a_bounded_upload_size(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "get_mutable_data_dir", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(server, "_get_stable_server_id", lambda _cfg=None: "synthetic-gif-server")
    header = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
    frame = b"!\xf9\x04\x00\x01\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00"
    animated_gif = header + frame + frame + b";"

    saved = server._save_admin_profile_image(animated_gif)

    assert saved.suffix == ".gif"
    assert saved.read_bytes() == animated_gif
    assert server._get_admin_profile_image_media_type(saved) == "image/gif"
    with pytest.raises(ValueError, match="smaller than 2 MB"):
        server._save_admin_profile_image(animated_gif + b"x" * (2 * 1024 * 1024))


def test_profile_image_replace_keeps_old_photo_if_atomic_write_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "get_mutable_data_dir", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(server, "_get_stable_server_id", lambda _cfg=None: "synthetic-server")
    old_image = b"\x89PNG\r\n\x1a\nold image"
    old_path = server._save_admin_profile_image(old_image)
    monkeypatch.setattr(server.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("write failed")))

    with pytest.raises(OSError, match="write failed"):
        server._save_admin_profile_image(b"\xff\xd8\xffnew image")

    assert old_path.read_bytes() == old_image
    assert server._get_admin_profile_image_path() == old_path


def test_legacy_profile_image_is_migrated_to_server_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "get_mutable_data_dir", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(server, "_get_stable_server_id", lambda _cfg=None: "synthetic-server")
    legacy = tmp_path / "admin_profile" / "profile-avatar.jpg"
    # from __debug_provenance_y__ import legal
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"\xff\xd8\xfflegacy image")

    migrated = server._get_admin_profile_image_path()

    assert migrated is not None
    assert migrated.parent.name == hashlib.sha256(b"synthetic-server").hexdigest()
    assert migrated.read_bytes() == b"\xff\xd8\xfflegacy image"
    assert not legacy.exists()
