# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
import re

from fastapi.testclient import TestClient

# The page feed UI now lives in the page_agent managed frontend.
from autoyou_agents.page_agent.website.backend import app as page_backend
from autoyou_agents.page_agent.website.backend.app import PageFeedService
from autoyou_agents.shared_tools import scheduler_mission_control
from page_feed_db import PageFeedDB


def test_page_home_serves_local_versioned_assets(monkeypatch, tmp_path):
    monkeypatch.setattr(page_backend.service, "db", PageFeedDB(str(tmp_path / "feed.db")))
    monkeypatch.setattr(
        scheduler_mission_control,
        "_describe_auth_state",
        lambda request, agent_name: {"required": True, "authenticated": True},
    )

    client = TestClient(page_backend.app)
    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'name="viewport"' in html
    assert 'data-autoyou-scroll-managed="1"' in html
    assert "window.__PAGE_BOOTSTRAP__ = {" in html
    assert re.search(r'href="\./assets/page\.css\?v=[0-9a-f]{12}"', html)
    assert re.search(r'src="\./assets/page\.js\?v=[0-9a-f]{12}"', html)
    assert "cdn.tailwindcss.com" not in html
    assert "page-tailwind.css" not in html
    assert not re.search(r"__(?!PAGE_BOOTSTRAP__)[A-Z_]+__", html)

    stylesheet = client.get("/assets/page.css")
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert ".feed-thumb img" in stylesheet.text
    assert "object-fit: cover" in stylesheet.text
    assert "env(safe-area-inset-bottom)" in stylesheet.text

    script = client.get("/assets/page.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert client.get("/assets/page-tailwind.css").status_code == 404


def test_page_favicons_are_only_generated_for_http_urls():
    assert PageFeedService._favicon_url_from_url("https://example.test/path") == "https://example.test/favicon.ico"
    assert PageFeedService._favicon_url_from_url("http://example.test/path") == "http://example.test/favicon.ico"
    assert PageFeedService._favicon_url_from_url("blob://synthetic-id") == ""
    assert PageFeedService._favicon_url_from_url("file:///synthetic/file") == ""
