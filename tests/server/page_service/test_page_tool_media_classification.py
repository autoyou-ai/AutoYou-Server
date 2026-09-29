# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-5b828c9efe16f7344a288660


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import base64
from urllib.parse import parse_qs, urlparse

from autoyou_agents.page_agent.page_tool import (
    PageTool,
    _classify_blob_item_type,
    _normalize_attachment_mimetype,
)

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-5b828c9efe16f7344a288660"


def test_page_tool_uses_the_owned_server_page_port(monkeypatch, tmp_path) -> None:
    import server

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_PAGE_PORT", "18067")
    monkeypatch.setattr(server.STATE, "config", {"autoyou_page": {"port": 8067}})
    tool = PageTool()
    assert tool.base_url == "http://127.0.0.1:18067/agent/page_agent"
    assert str(tmp_path) in tool._db_path
    monkeypatch.delenv("AUTOYOU_PAGE_PORT")
    assert tool.base_url == "http://127.0.0.1:8067/agent/page_agent"
    assert PageTool(base_url="http://127.0.0.1:28067").base_url == "http://127.0.0.1:28067"


def test_normalize_attachment_mimetype_upgrades_generic_mp4_uploads() -> None:
    normalized = _normalize_attachment_mimetype(
        mimetype_hint="application/octet-stream",
        filename="clip.mp4",
        path="/tmp/clip.mp4",
        metadata={"kind": "video"},
    )
    assert normalized == "video/mp4"


def test_normalize_attachment_mimetype_fixes_application_mp4() -> None:
    normalized = _normalize_attachment_mimetype(
        mimetype_hint="application/mp4",
        filename="clip.mp4",
    )
    assert normalized == "video/mp4"


def test_classify_blob_item_type_marks_videos_as_video() -> None:
    assert _classify_blob_item_type("video/mp4") == "video"


def test_page_tool_query_feed_http_forwards_limit_sources_and_timeline_all(monkeypatch) -> None:
    captured = {}

    def fake_http_json(method, path, body=None):
        captured["method"] = method
        # from __debug_provenance_t__ import address
        captured["path"] = path
        captured["body"] = body
        return {"items": []}

    tool = PageTool(base_url="http://127.0.0.1:8067")
    monkeypatch.setattr(tool, "_http_json", fake_http_json)

    result = tool.query_feed(
        order="asc",
        types=["article", "video"],
        sources=["Telegram", "Local"],
        favourites_only=True,
        tag_search="saved",
        limit=5,
        timeline_all=True,
    )

    query = parse_qs(urlparse(captured["path"]).query)
    assert result == {"success": True, "items": []}
    assert captured["method"] == "GET"
    assert urlparse(captured["path"]).path == "/api/feed/query"
    assert query["order"] == ["asc"]
    assert query["types"] == ["article,video"]
    assert query["sources"] == ["Telegram,Local"]
    assert query["favourites_only"] == ["1"]
    assert query["tag_search"] == ["saved"]
    assert query["limit"] == ["5"]
    assert query["timeline_all"] == ["1"]


def test_page_tool_http_blob_upload_links_to_page_feed_for_user_display(monkeypatch) -> None:
    captured = {}

    def fake_upload_blob(filename, data_bytes, mimetype=None):
        captured["upload"] = {
            "filename": filename,
            "data_bytes": data_bytes,
            "mimetype": mimetype,
        }
        return {"id": "blob-1", "filename": filename, "mimetype": mimetype, "size": len(data_bytes)}

    def fake_http_json(method, path, body=None):
        captured["feed"] = {
            "method": method,
            "path": path,
            "body": body,
        }
        return {
            "item": {
                "id": 7,
                "type": "image",
                "url": "blob://blob-1",
                "title": "synthetic.png",
                "source": "Local",
            }
        }

    tool = PageTool(base_url="http://127.0.0.1:8067")
    monkeypatch.setattr(tool, "_http_upload_blob", fake_upload_blob)
    monkeypatch.setattr(tool, "_http_json", fake_http_json)

    payload = base64.b64encode(b"synthetic image bytes").decode("ascii")
    result = tool.add_blob(
        filename="synthetic.png",
        data_base64=payload,
        mimetype="image/png",
    )

    assert result["success"] is True
    assert result["item"]["url"] == "blob://blob-1"
    assert result["item"]["open_url"] == "http://127.0.0.1:8067/api/blob/blob-1"
    assert result["item"]["view_url"] == "http://127.0.0.1:8067/api/blob/blob-1"
    assert result["item"]["storage_location"] == "AutoYou page feed on this computer"
    assert result["item"]["local_only"] is True
    assert "[Page Feed](http://127.0.0.1:8067/)" in result["message"]
    assert "/api/blob/" not in result["message"]
    assert "blob://blob-1" not in result["message"]
    assert captured["feed"]["body"]["url"] == "blob://blob-1"


def test_page_tool_updates_server_photo_through_avatar_api(monkeypatch, tmp_path) -> None:
    captured = {}
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"synthetic-avatar"
    image_path = tmp_path / "synthetic-avatar.png"
    image_path.write_bytes(image_bytes)
    tool = PageTool(base_url="http://127.0.0.1:8067")

    def fake_upload(path, field_name, filename, data_bytes, mimetype=None):
        captured.update(path=path, field_name=field_name, filename=filename, data=data_bytes)
        return {"success": True, "has_photo": True}

    monkeypatch.setattr(tool, "_http_upload_multipart", fake_upload)
    result = tool.update_server_display_photo_from_path(str(image_path))

    assert result["success"] is True
    assert captured == {
        "path": "/api/profile/avatar",
        "field_name": "image",
        "filename": "profile-photo",
        "data": image_bytes,
    }


def test_page_tool_delete_tag_http_escapes_path_segment(monkeypatch) -> None:
    captured = {}

    def fake_http_json(method, path, body=None):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"ok": True}

    tool = PageTool(base_url="http://127.0.0.1:8067")
    monkeypatch.setattr(tool, "_http_json", fake_http_json)

    result = tool.delete_tag(42, "focus/alpha & beta")

    assert result == {"success": True, "ok": True}
    assert captured["method"] == "DELETE"
    assert captured["path"] == "/api/item/42/tags/focus%2Falpha%20%26%20beta"
