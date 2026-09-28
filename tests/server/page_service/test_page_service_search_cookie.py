# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import os
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.testclient import TestClient

from page_feed_db import PageFeedDB
from autoyou_agents.page_agent.website.backend.app import PageFeedService


def test_page_feed_db_search_by_title_and_tag(tmp_path):
    db_file = tmp_path / "test_feed.db"
    db = PageFeedDB(str(db_file))

    # Insert items
    item1 = db.insert("article", "https://example.com/1", title="Secret Assignment Agreement")
    item2 = db.insert("article", "https://example.com/2", title="Something Else")
    db.add_tag(item2["id"], "agreement")
    item3 = db.insert("article", "https://example.com/3", title="Other Document")

    # Search for "agreement"
    results = db.query_items(tag_search="agreement")
    result_ids = {item["id"] for item in results}

    assert item1["id"] in result_ids  # Matches title
    assert item2["id"] in result_ids  # Matches tag
    assert item3["id"] not in result_ids  # No match


def test_page_feed_db_search_bypasses_seven_days(tmp_path):
    db_file = tmp_path / "test_feed.db"
    db = PageFeedDB(str(db_file))

    # Insert item
    item = db.insert("article", "https://example.com/old", title="Old Agreement")

    # Manually backdate added_at to 15 days ago
    fifteen_days_ago = (datetime.now() - timedelta(days=15)).isoformat()
    with db._connect() as con:
        con.execute("UPDATE feed_items SET added_at = ? WHERE id = ?", (fifteen_days_ago, item["id"]))

    # Query with tag_search="Agreement" (should bypass 7-day default limit and return it)
    results_search = db.query_items(tag_search="Agreement")
    assert len(results_search) == 1
    assert results_search[0]["id"] == item["id"]

    # Query without tag_search (should apply 7-day default limit and return nothing)
    results_no_search = db.query_items()
    assert len(results_no_search) == 0


def test_backend_api_query_bypasses_seven_days(monkeypatch):
    service = PageFeedService()
    
    class FakeDb:
        def __init__(self):
            self.last_query_items_kwargs = {}

        def query_items(self, **kwargs):
            self.last_query_items_kwargs = kwargs
            return [{"id": 1, "type": "article", "url": "", "title": "Old", "source": "", "added_at": "", "favourite": False, "tags": []}]

    fake_db = FakeDb()
    service.db = fake_db
    app = FastAPI()
    service.register_routes(app)
    client = TestClient(app)

    # Calling API with tag_search
    resp = client.get("/api/feed/query?tag_search=Agreement")
    assert resp.status_code == 200
    assert fake_db.last_query_items_kwargs.get("ignore_date_default") is True
    assert fake_db.last_query_items_kwargs.get("tag_search") == "Agreement"
