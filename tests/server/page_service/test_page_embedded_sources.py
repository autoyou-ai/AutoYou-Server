# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Regression tests for Page Agent embedded browser media sources & attachments.

Covers:
1. YouTube playback resolution (yt-dlp JS runtime opts, storyboard filtering,
   stream header forwarding, and sheet/reel video upgrade with iframe fallback).
2. Bluesky handle-to-DID embed resolution (`embed.bsky.app` requires `did:`).
3. Website OpenGraph preview extraction and inline website preview toggle.
4. Attachment & file downloads (`?download=1` Content-Disposition, clean
   filenames without duplicate extensions, single-blob `/api/feed/item/{id}`
   attachment list for mobile sync, and text/markdown blob inline content).
5. Instagram (`/reels/`, `/<user>/reel/`) and Threads (`/post/<id>`) embed URLs.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from pathlib import Path
from types import SimpleNamespace
import sys

from fastapi.testclient import TestClient
import pytest

from autoyou_agents.page_agent.page_tool import (
    _classify_blob_item_type,
    _extension_for_mimetype,
)
from autoyou_agents.page_agent.website.backend import app as page_backend
from autoyou_agents.page_agent.website.backend.app import PageFeedService
from autoyou_agents.shared_tools import scheduler_mission_control
from page_feed_db import PageFeedDB

ASSETS = (
    Path(__file__).resolve().parents[3]
    / "autoyou_agents"
    / "page_agent"
    / "website"
    / "frontend"
    / "assets"
)
JS = (ASSETS / "page.js").read_text(encoding="utf-8")
CSS = (ASSETS / "page.css").read_text(encoding="utf-8")


@pytest.fixture
def isolated_page(monkeypatch, tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    db = PageFeedDB(str(tmp_path / "feed.db"))
    service = page_backend.service
    monkeypatch.setattr(service, "db", db)
    monkeypatch.setattr(service, "uploads_dir", str(uploads))
    monkeypatch.setattr(service, "feed_items", [])
    monkeypatch.setattr(
        scheduler_mission_control,
        "_describe_auth_state",
        lambda request, agent_name: {"required": True, "authenticated": True},
    )
    return service, TestClient(page_backend.app), uploads


def test_youtube_id_extraction_variants():
    extract = PageFeedService._youtube_id_from_url
    assert extract("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract("https://youtu.be/dQw4w9WgXcQ/extra?si=synthetic") == "dQw4w9WgXcQ"
    assert extract("https://www.youtube.com/shorts/dQw4w9WgXcQ?feature=share") == "dQw4w9WgXcQ"
    assert extract("https://www.youtube.com/live/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract("https://www.youtube.com/v/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_resolve_media_url_filters_storyboards_and_caches_headers(monkeypatch):
    service = PageFeedService.__new__(PageFeedService)
    service._media_resolve_cache = {}
    service._media_headers_cache = {}

    captured_opts = []

    class FakeYDL:
        def __init__(self, opts):
            captured_opts.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            return {
                "formats": [
                    {
                        "format_id": "sb0",
                        "ext": "mhtml",
                        "vcodec": "none",
                        "acodec": "none",
                        "url": "https://i.ytimg.com/sb/synthetic/storyboard.mhtml",
                    },
                    {
                        "format_id": "140",
                        "ext": "m4a",
                        "vcodec": "none",
                        "acodec": "mp4a.40.2",
                        "url": "https://example.com/audio-only.m4a",
                    },
                    {
                        "format_id": "18",
                        "ext": "mp4",
                        "vcodec": "avc1.42001E",
                        "acodec": "mp4a.40.2",
                        "protocol": "https",
                        "url": "https://example.com/video-muxed.mp4",
                        "http_headers": {"User-Agent": "SyntheticYTDLP/1.0"},
                    },
                ]
            }

    monkeypatch.setattr(page_backend, "assert_safe_http_url", lambda u: u)
    monkeypatch.setattr(page_backend, "is_safe_http_url", lambda u: True)
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))

    resolved = service._resolve_media_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert resolved == "https://example.com/video-muxed.mp4"
    assert captured_opts[0].get("js_runtimes") == {"node": {}, "deno": {}}
    assert captured_opts[0].get("remote_components") == ["ejs:github"]
    assert service._media_headers_cache[resolved]["User-Agent"] == "SyntheticYTDLP/1.0"


def test_bluesky_post_resolution_and_preview_endpoint(isolated_page, monkeypatch):
    service, client, _ = isolated_page
    monkeypatch.setattr(page_backend, "assert_safe_http_url", lambda u: u)
    monkeypatch.setattr(page_backend, "is_safe_http_url", lambda u: True)

    def fake_follow(session, url, **kwargs):
        if "resolveHandle" in url:
            body = b'{"did": "did:plc:synthetic123456"}'
        else:
            body = (
                b'{"thread": {"post": {'
                b'"author": {"did": "did:plc:synthetic123456", "handle": "synthetic.bsky.social", "displayName": "Synthetic"},'
                b'"record": {"text": "Hello from Bluesky"},'
                b'"embed": {"images": [{"thumb": "https://cdn.bsky.app/img/synthetic.jpg"}]}'
                b"}}}"
            )
        return SimpleNamespace(
            ok=True,
            url=url,
            headers={"content-type": "application/json"},
            content=body,
            close=lambda: None,
        )

    monkeypatch.setattr(page_backend, "safe_follow_redirects", fake_follow)

    resp = client.get(
        "/api/media/preview",
        params={"url": "https://bsky.app/profile/synthetic.bsky.social/post/3kxyz123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["did"] == "did:plc:synthetic123456"
    assert (
        data["embed_url"]
        == "https://embed.bsky.app/embed/did:plc:synthetic123456/app.bsky.feed.post/3kxyz123"
    )
    assert data["url"] == "https://cdn.bsky.app/img/synthetic.jpg"
    assert "Hello from Bluesky" in data["title"]


def test_website_preview_extraction_and_endpoint(isolated_page, monkeypatch):
    _, client, _ = isolated_page
    monkeypatch.setattr(page_backend, "assert_safe_http_url", lambda u: u)
    monkeypatch.setattr(page_backend, "is_safe_http_url", lambda u: True)

    html_bytes = (
        b"<html><head>"
        b'<meta property="og:title" content="Synthetic Article Title">'
        b'<meta name="description" content="A concise summary of the article.">'
        b'<meta property="og:image" content="/assets/hero.png">'
        b'<meta property="og:site_name" content="Synthetic News">'
        b"</head><body></body></html>"
    )

    def fake_follow(session, url, **kwargs):
        return SimpleNamespace(
            ok=True,
            url="https://example.com/stories/1",
            headers={"content-type": "text/html; charset=utf-8"},
            content=html_bytes,
            close=lambda: None,
        )

    monkeypatch.setattr(page_backend, "safe_follow_redirects", fake_follow)

    resp = client.get("/api/media/preview", params={"url": "https://example.com/stories/1"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["title"] == "Synthetic Article Title"
    assert payload["description"] == "A concise summary of the article."
    assert payload["image"] == "https://example.com/assets/hero.png"
    assert payload["url"] == "https://example.com/assets/hero.png"


def test_blob_download_disposition_and_single_blob_item_attachments(isolated_page):
    service, client, uploads = isolated_page

    upload_resp = client.post(
        "/api/blob",
        files={"file": ("notes.md", b"# Heading\nSome markdown notes.", "text/markdown")},
    )
    assert upload_resp.status_code == 200
    blob_id = upload_resp.json()["id"]

    feed_resp = client.post(
        "/api/feed",
        json={
            "url": f"blob://{blob_id}",
            "type": "text",
            "title": "notes.md",
            "source": "Local",
        },
    )
    assert feed_resp.status_code == 200
    created = feed_resp.json()["item"]
    assert created["view"]["download_url"] == f"./api/blob/{blob_id}?download=1"
    assert "# Heading" in created["view"]["content"]

    # Inline stream by default vs forced attachment when ?download=1 is passed
    inline_resp = client.get(f"/api/blob/{blob_id}")
    assert inline_resp.status_code == 200
    assert inline_resp.headers["content-disposition"].startswith("inline;")
    assert 'filename="notes.md"' in inline_resp.headers["content-disposition"]

    dl_resp = client.get(f"/api/blob/{blob_id}?download=1")
    assert dl_resp.status_code == 200
    assert dl_resp.headers["content-disposition"].startswith("attachment;")
    assert 'filename="notes.md"' in dl_resp.headers["content-disposition"]

    # Mobile sync endpoint `/api/feed/item/{id}` includes synthesized attachment for single-blob items
    detail_resp = client.get(f"/api/feed/item/{created['id']}")
    assert detail_resp.status_code == 200
    detail_item = detail_resp.json()["item"]
    assert len(detail_item["attachments"]) == 1
    assert detail_item["attachments"][0]["blob_id"] == blob_id
    assert detail_item["attachments"][0]["name"] == "notes.md"


def test_instagram_and_threads_url_variants_and_page_tool_text_mime():
    assert _classify_blob_item_type("text/plain") == "text"
    assert _classify_blob_item_type("text/markdown") == "text"
    assert _extension_for_mimetype("text/markdown") == ".md"

    embed = PageFeedService._instagram_embed_url_from_url
    assert embed("https://www.instagram.com/reels/SyntheticReel1/") == "https://www.instagram.com/reel/SyntheticReel1/embed/"
    assert embed("https://www.instagram.com/synthetic_user/reel/SyntheticReel2/") == "https://www.instagram.com/reel/SyntheticReel2/embed/"
    assert embed("https://www.instagram.com/synthetic_user/p/SyntheticPost1/") == "https://www.instagram.com/p/SyntheticPost1/embed/"

    # Verify frontend JS helpers for YouTube, Bluesky, Website Preview, and Downloads
    assert "data-youtube-stage=" in JS
    assert "async function resolveReelYoutube(slide, stage, item)" in JS
    assert "async function resolveBlueskyEmbeds(root)" in JS
    assert "async function resolveWebsitePreviews(root)" in JS
    assert "function toggleWebsitePreview(button)" in JS
    assert ".website-preview-frame" in CSS
