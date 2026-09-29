# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-breach-a3270dab3e3c25935d8ab591


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import json
import re
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autoyou_agents.page_agent.website.backend.app import PageFeedService
from page_feed_db import PageFeedDB
from shared.remote_access_policy import REMOTE_BROWSER_HEADER

__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-breach-a3270dab3e3c25935d8ab591"


VIEWER = {"X-AutoYou-WebRTC-Session-Id": "synthetic-session", "X-AutoYou-Remote-Access-Role": "viewer"}
EDITOR = {"X-AutoYou-WebRTC-Session-Id": "synthetic-session", "X-AutoYou-Remote-Access-Role": "editor"}


def _backdate(db, item_id, days):
    stamp = (datetime.now() - timedelta(days=days)).isoformat()
    with db._connect() as con:
        con.execute("UPDATE feed_items SET added_at = ? WHERE id = ?", (stamp, item_id))


@pytest.fixture()
def feed(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    # No live server configuration: the website falls back to its own default.
    monkeypatch.delitem(sys.modules, "server", raising=False)
    service = PageFeedService()
    service.db = PageFeedDB(str(tmp_path / "feed.db"))
    app = FastAPI()
    service.register_routes(app)
    return service, TestClient(app)


def test_feed_pages_through_the_whole_timeline_by_default(feed):
    service, client = feed
    ids = [service.db.insert("article", f"https://example.test/{n}", title=f"Item {n}")["id"] for n in range(5)]
    for offset, item_id in enumerate(ids):
        _backdate(service.db, item_id, 400 - offset * 90)

    first = client.get("/api/feed/page?limit=2").json()
    second = client.get(f"/api/feed/page?limit=2&cursor={first['next_cursor']}").json()
    third = client.get(f"/api/feed/page?limit=2&cursor={second['next_cursor']}").json()

    assert first["window_days"] == 0
    assert [item["id"] for item in first["items"]] == ids[::-1][:2]
    assert [item["id"] for item in second["items"]] == ids[::-1][2:4]
    assert [item["id"] for item in third["items"]] == ids[::-1][4:]
    assert (first["has_more"], second["has_more"], third["has_more"]) == (True, True, False)
    assert third["next_cursor"] is None
    oldest_first = client.get("/api/feed/page?limit=10&order=asc").json()
    assert [item["id"] for item in oldest_first["items"]] == ids


def test_configured_window_applies_live_and_search_spans_everything(feed, monkeypatch):
    service, client = feed
    recent = service.db.insert("article", "https://example.test/recent", title="Recent note")["id"]
    old = service.db.insert("article", "https://example.test/old", title="Old recipe")["id"]
    _backdate(service.db, old, 40)
    fake_server = SimpleNamespace(STATE=SimpleNamespace(config={"autoyou_page": {"feed_window_days": 14}}))
    monkeypatch.setitem(sys.modules, "server", fake_server)

    windowed = client.get("/api/feed/page").json()
    everything = client.get("/api/feed/page?all=1").json()
    searched = client.get("/api/feed/page?q=recipe").json()

    assert windowed["window_days"] == 14
    assert [item["id"] for item in windowed["items"]] == [recent]
    assert [item["id"] for item in everything["items"]] == [recent, old]
    assert [item["id"] for item in searched["items"]] == [old]

    fake_server.STATE.config["autoyou_page"]["feed_window_days"] = 0
    assert [item["id"] for item in client.get("/api/feed/page").json()["items"]] == [recent, old]


def test_views_and_type_tabs_filter_the_feed(feed):
    service, client = feed
    article = service.db.insert("article", "https://example.test/a", title="Article")["id"]
    video = service.db.insert("youtube", "https://www.youtube.com/watch?v=abc123DEF45", title="Video")["id"]
    upload = service.db.insert("image", "blob://synthetic-blob", title="Photo", source="Local")["id"]
    service.db.set_favourite(video, True)

    def ids(query):
        return [item["id"] for item in client.get(f"/api/feed/page?{query}").json()["items"]]

    assert ids("view=favourites") == [video]
    assert ids("view=uploads") == [upload]
    assert ids("tab=videos") == [video]
    assert ids("tab=articles") == [article]
    assert ids("tab=photos") == [upload]


def test_page_feed_accepts_native_text_items(feed):
    service, client = feed
    response = client.post(
        "/api/feed",
        headers=EDITOR,
        json={"type": "text", "title": "Trip note", "content": "Pack the camera and charger.", "source": "This Device"},
    )
    # from __debug_provenance_o__ import breach

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["type"] == "text"
    assert item["content"] == "Pack the camera and charger."
    stored = client.get("/api/feed/page").json()["items"][0]
    assert stored["content"] == item["content"]
    assert "Pack the camera and charger." in client.get("/").text


def test_summary_reports_genuine_totals_tags_favourite_and_cover(feed):
    service, client = feed
    first = service.db.insert("article", "https://news.example.test/a", title="A")["id"]
    second = service.db.insert("image", "blob://cover-blob", title="Sunset", source="Local")["id"]
    service.db.insert("article", "https://news.example.test/b", title="B")
    service.db.add_tag(first, "reading")
    service.db.add_tag(second, "reading")
    service.db.add_tag(second, "travel")
    service.db.set_favourite(first, True)

    summary = client.get("/api/feed/summary").json()

    assert (summary["total"], summary["favourites"], summary["sources"]) == (3, 1, 2)
    assert summary["top_tags"] == [{"tag": "reading", "count": 2}, {"tag": "travel", "count": 1}]
    assert summary["latest_favourite"]["id"] == first
    assert summary["cover"] == {"url": "./api/blob/cover-blob?preview=1", "item_id": second}


def test_home_page_renders_real_items_and_owner_controls(feed):
    service, client = feed
    service.db.insert("article", "https://aeon.example.test/habits", title="The quiet power of small habits")

    html = client.get("/").text
    bootstrap = json.loads(re.search(r"window.__PAGE_BOOTSTRAP__ = (\{.*?\});\n", html).group(1))

    assert 'data-can-add="1" data-can-edit="1" data-can-delete="1" data-can-manage="1" data-role="owner"' in html
    assert "The quiet power of small habits" in html
    assert '<dd id="stat-total">1</dd>' in html
    assert bootstrap["access"]["role"] == "owner"
    assert bootstrap["items"][0]["view"]["kind"] == "Article"
    assert not re.search(r"__(?!PAGE_BOOTSTRAP__)[A-Z_]+__", html)


def test_home_page_limits_trusted_contacts_to_their_role(feed):
    service, client = feed
    service.db.insert("article", "https://example.test/shared", title="Shared link")

    viewer_html = client.get("/", headers=VIEWER).text
    editor_html = client.get("/", headers=EDITOR).text

    assert 'data-can-add="0" data-can-edit="0" data-can-delete="0" data-can-manage="0" data-role="viewer"' in viewer_html
    assert '<span class="role-pill">View only</span>' in viewer_html
    assert "Nothing has been saved" not in viewer_html
    assert 'data-can-add="1" data-can-edit="1" data-can-delete="0" data-can-manage="0" data-role="editor"' in editor_html


def test_viewers_cannot_change_the_owner_theme(feed):
    _, client = feed

    assert client.post("/api/ui/theme", json={"theme": "light"}, headers=VIEWER).status_code == 403
    assert client.post("/api/ui/theme", json={"theme": "light"}).status_code == 200


def test_saved_text_cannot_break_out_of_markup(feed):
    service, client = feed
    service.db.insert("article", "javascript:alert(1)", title="</script><script>alert(1)</script>")

    html = client.get("/").text
    item = client.get("/api/feed/page").json()["items"][0]

    assert "<script>alert(1)</script>" not in html
    assert "\\u003c/script\\u003e" in html
    assert item["view"]["open_url"] == ""
    assert "javascript:" not in item["view"]["favicon"]


def test_presenter_describes_each_kind_of_item(feed):
    service, _ = feed
    present = service._present_item

    youtube = present({"type": "youtube", "url": "https://www.youtube.com/watch?v=abc123DEF45", "title": "Talk"})
    tweet = present({"type": "x", "url": "https://twitter.com/someone/status/12345?s=20", "title": ""})
    document = present({"type": "document", "url": "blob://doc-1", "title": "Plan", "source": "Local"}, {"filename": "Plan.pdf", "size": 2048})
    article = present({"type": "article", "url": "https://www.aeon.co/essays/habits", "title": ""})

    assert youtube["thumb"] == "https://i.ytimg.com/vi/abc123DEF45/hqdefault.jpg"
    assert youtube["byline"] == ["YouTube"]
    assert (tweet["kind"], tweet["label"], tweet["open_url"]) == ("X post", "X", "https://x.com/someone/status/12345")
    assert tweet["title"] == "Post by @someone"
    assert article["byline"] == ["Article", "aeon.co"]
    assert (document["label"], document["size"], document["open_url"]) == ("PDF", 2048, "./api/blob/doc-1")
    assert (article["title"], article["label"], article["favicon"]) == ("aeon.co", "Ae", "https://www.aeon.co/favicon.ico")


def test_legacy_query_endpoint_treats_a_zero_window_as_the_whole_feed(feed):
    service, client = feed
    old = service.db.insert("article", "https://example.test/old", title="Old")["id"]
    _backdate(service.db, old, 90)

    items = client.get("/api/feed/query").json()["items"]

    assert [item["id"] for item in items] == [old]


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def test_page_profile_uses_the_admin_photo_and_server_name(feed, tmp_path, monkeypatch):
    _, client = feed
    photo = tmp_path / "profile-avatar.png"
    photo.write_bytes(PNG_BYTES)
    fake_server = SimpleNamespace(
        STATE=SimpleNamespace(config={}),
        get_configured_server_name=lambda: "AutoYouRocks",
        _get_admin_profile_image_path=lambda: photo,
    )
    monkeypatch.setitem(sys.modules, "server", fake_server)

    html = client.get("/").text
    avatar = client.get(re.search(r'class="avatar-img" src="\./(api/profile/avatar\?v=\d+)"', html).group(1))
    viewer_avatar = client.get("/api/profile/avatar", headers=VIEWER)

    assert "<title>AutoYouRocks · AutoYou Page</title>" in html
    assert '<h2 id="hero-title">AutoYouRocks</h2>' in html
    assert '<button id="profile-photo-select" class="brand-tile profile-photo-button has-photo"' in html
    assert (avatar.status_code, avatar.content, avatar.headers["content-type"]) == (200, PNG_BYTES, "image/png")
    assert avatar.headers["cache-control"] == "private, max-age=86400"
    assert viewer_avatar.status_code == 200


def test_page_profile_falls_back_to_the_autoyou_mark(feed, monkeypatch):
    _, client = feed
    fake_server = SimpleNamespace(
        STATE=SimpleNamespace(config={}),
        get_configured_server_name=lambda: "AutoYou-Server",
        _get_admin_profile_image_path=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "server", fake_server)

    html = client.get("/").text
    mark_url = re.search(r'class="avatar-img" src="\./(assets/autoyou-mark\.svg\?v=[0-9a-f]{12})"', html).group(1)
    mark = client.get(mark_url)

    assert '<button id="profile-photo-select" class="brand-tile profile-photo-button is-mark"' in html
    assert '<h2 id="hero-title">AutoYou Page</h2>' in html
    assert f'<link rel="icon" type="image/svg+xml" href="./{mark_url}">' in html
    assert mark.status_code == 200 and mark.headers["content-type"].startswith("image/svg+xml")
    assert client.get("/api/profile/avatar").status_code == 404


def test_page_profile_photo_write_uses_remote_role_policy(feed, tmp_path, monkeypatch):
    _, client = feed
    photo = tmp_path / "server-photo.png"

    def save_profile_image(payload):
        if not payload.startswith(PNG_BYTES[:8]):
            raise ValueError("Profile image must be a PNG, JPEG, or WebP file.")
        photo.write_bytes(payload)
        return photo

    def delete_profile_image():
        photo.unlink(missing_ok=True)

    monkeypatch.setitem(
        sys.modules,
        "server",
        SimpleNamespace(
            _get_admin_profile_image_path=lambda: photo if photo.is_file() else None,
            _save_admin_profile_image=save_profile_image,
            _delete_admin_profile_image_files=delete_profile_image,
            WEBRTC=None,
        ),
    )
    admin = {**EDITOR, "X-AutoYou-Remote-Access-Role": "admin"}
    image = ("server-photo.png", PNG_BYTES, "image/png")
    home_network_viewer = {
        REMOTE_BROWSER_HEADER: "home_network",
        "X-AutoYou-Remote-Access-Role": "viewer",
    }

    assert client.get("/api/profile/avatar", headers=VIEWER).status_code == 404
    assert client.post("/api/profile/avatar", headers=VIEWER, files={"image": image}).status_code == 403
    assert client.put("/api/profile/avatar", headers=VIEWER, files={"image": image}).status_code == 403
    assert client.patch("/api/profile/avatar", headers=VIEWER, files={"image": image}).status_code == 403
    assert client.post("/api/profile/avatar", headers=home_network_viewer, files={"image": image}).status_code == 403
    assert client.post("/api/profile/avatar", headers=EDITOR, files={"image": image}).status_code == 200
    assert client.put("/api/profile/avatar", headers=EDITOR, files={"image": image}).status_code == 200
    assert client.patch("/api/profile/avatar", headers=EDITOR, files={"image": image}).status_code == 200
    assert client.get("/api/profile/avatar", headers=VIEWER).content == PNG_BYTES
    assert client.delete("/api/profile/avatar", headers=EDITOR).status_code == 403
    assert client.delete("/api/profile/avatar", headers=admin).json() == {"success": True, "has_photo": False}
    assert client.get("/api/profile/avatar", headers=VIEWER).status_code == 404


def test_page_profile_photo_controls_follow_browser_role(feed):
    _, client = feed

    editor = client.get("/", headers=EDITOR).text
    viewer = client.get("/", headers=VIEWER).text
    admin = client.get("/", headers={**EDITOR, "X-AutoYou-Remote-Access-Role": "admin"}).text
    script_path = re.search(r'src="\./(assets/page\.js\?v=[0-9a-f]{12})"', editor).group(1)
    script = client.get(f"/{script_path}").text

    assert 'id="profile-photo-select"' in editor
    assert 'accept="image/png,image/jpeg,image/webp"' in editor
    assert 'id="profile-photo-remove"' not in editor
    assert 'id="profile-photo-select"' not in viewer
    assert 'id="profile-photo-select"' in admin
    assert 'id="profile-photo-remove" class="profile-photo-remove" type="button" hidden' in admin
    assert '$("profile-photo-select").addEventListener("click"' in script
    assert '$("profile-photo-input").addEventListener("change"' in script


def test_page_ships_the_full_screen_feed_and_photo_viewer(feed):
    service, client = feed
    service.db.insert("image", "blob://synthetic-photo", title="Sunset", source="Local")

    html = client.get("/").text
    script = client.get(re.search(r'src="\./(assets/page\.js\?v=[0-9a-f]{12})"', html).group(1)).text

    assert 'id="reel"' in html and 'id="reel-track"' in html
    assert 'id="photo-viewer"' in html and 'id="play-feed"' in html
    assert "function openReel(" in script and "function openPhoto(" in script
    # Opened photos load the original file; the list keeps the light preview.
    assert "./api/blob/synthetic-photo?preview=1" in html
    assert "const full = safeUrl(view.open_url);" in script
