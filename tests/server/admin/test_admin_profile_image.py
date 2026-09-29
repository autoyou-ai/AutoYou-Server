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


PNG_IMAGE = b"\x89PNG\r\n\x1a\nsynthetic avatar"
SAME_ORIGIN = {"Origin": "http://testserver", "Referer": "http://testserver/admin"}


@pytest.fixture
def avatar_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(server, "get_mutable_data_dir", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(server, "_get_stable_server_id", lambda _cfg=None: "synthetic-server")
    monkeypatch.setattr(server, "WEBRTC", None)
    monkeypatch.setattr(
        server,
        "_request_uses_ai_agent_internal_token",
        lambda request: request.headers.get("Authorization") == "Bearer synthetic-internal-token",
    )
    return TestClient(server.admin_app)


def test_admin_origin_profile_avatar_rejects_anonymous_callers(avatar_client):
    photo = server._save_admin_profile_image(PNG_IMAGE)
    image = {"image": ("avatar.png", b"\x89PNG\r\n\x1a\nattacker", "image/png")}

    assert avatar_client.get("/api/profile/avatar").status_code == 401
    for method in ("POST", "PUT", "PATCH"):
        response = avatar_client.request(method, "/api/profile/avatar", headers=SAME_ORIGIN, files=image)
        assert response.status_code == 401, method
    assert avatar_client.delete("/api/profile/avatar", headers=SAME_ORIGIN).status_code == 401

    assert server._get_admin_profile_image_path() == photo
    assert photo.read_bytes() == PNG_IMAGE


@pytest.mark.parametrize(
    "credentials",
    [
        {"cookies": {"admin_session": "synthetic-avatar-session"}},
        {"headers": {"Authorization": "Bearer synthetic-internal-token"}},
    ],
    ids=["admin-session", "internal-agent-token"],
)
def test_admin_origin_profile_avatar_serves_signed_in_callers(avatar_client, monkeypatch, credentials):
    monkeypatch.setitem(server.ADMIN_SESSIONS, "synthetic-avatar-session", True)
    for name, value in credentials.get("cookies", {}).items():
        avatar_client.cookies.set(name, value)
    headers = {**SAME_ORIGIN, **credentials.get("headers", {})}
    image = {"image": ("avatar.png", PNG_IMAGE, "image/png")}

    saved = avatar_client.put("/api/profile/avatar", headers=headers, files=image)
    assert saved.status_code == 200 and saved.json()["has_photo"] is True
    fetched = avatar_client.get("/api/profile/avatar", headers=headers)
    assert (fetched.status_code, fetched.content) == (200, PNG_IMAGE)
    deleted = avatar_client.delete("/api/profile/avatar", headers=headers)
    assert deleted.json() == {"success": True, "has_photo": False}
    assert server._get_admin_profile_image_path() is None
