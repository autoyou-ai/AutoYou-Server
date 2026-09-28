# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from types import SimpleNamespace

from autoyou_agents.page_agent import agent as page_agent


def _llm_request(text: str):
    return SimpleNamespace(
        contents=[
            SimpleNamespace(parts=[SimpleNamespace(text=text)]),
        ]
    )


class _FakePageTool:
    def __init__(self):
        self.urls = []

    def add_link(self, *, url, title=None, source=None, item_type=None):
        self.urls.append(url)
        return {
            "success": True,
            "item": {"id": 42, "url": url},
            "message": f"Added {url} to your AutoYou page feed.",
        }

    def query_feed(
        self,
        order="desc",
        types=None,
        source=None,
        sources=None,
        date_from=None,
        date_to=None,
        favourites_only=False,
        tag_search=None,
        limit=None,
        timeline_all=False,
    ):
        return {
            "success": True,
            "items": [
                {
                    "id": 42,
                    "title": "Synthetic page item",
                    "url": "https://example.com/synthetic",
                    "item_type": "article",
                    "source": "Local",
                }
            ],
        }


async def test_page_agent_deterministically_adds_page_feed_url(monkeypatch):
    fake_tool = _FakePageTool()
    monkeypatch.setattr(page_agent, "page_tool", fake_tool)

    response = await page_agent._page_agent_before_model_callback(
        None,
        _llm_request("add www.example.com/synthetic to page feed"),
    )

    assert fake_tool.urls == ["http://www.example.com/synthetic"]
    assert response.content.parts[0].text == (
        "Added http://www.example.com/synthetic to your AutoYou page feed."
    )


async def test_page_agent_deterministic_add_ignores_non_page_requests(monkeypatch):
    fake_tool = _FakePageTool()
    monkeypatch.setattr(page_agent, "page_tool", fake_tool)

    response = await page_agent._page_agent_before_model_callback(
        None,
        _llm_request("summarize www.example.com/synthetic"),
    )

    assert response is None
    assert fake_tool.urls == []


async def test_page_agent_deterministically_queries_page_feed(monkeypatch):
    fake_tool = _FakePageTool()
    monkeypatch.setattr(page_agent, "page_tool", fake_tool)

    response = await page_agent._page_agent_before_model_callback(
        None,
        _llm_request("How many items are in my AutoYou Page feed?"),
    )

    text = response.content.parts[0].text
    assert "The AutoYou Page feed has 1 item." in text
    assert "Synthetic page item" in text
    assert response.custom_metadata["route_reason"] == "deterministic_page_feed_query"
