from __future__ import annotations

import pytest

from autoyou_agents.page_agent.page_tool import PageTool
from shared.url_hosts import host_matches, social_platform, url_hostname


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("youtu.be/abc", "youtube"),
        ("https://m.tiktok.com/@user/video/1", "tiktok"),
        ("https://www.threads.net/@user/post/1", "threads"),
        ("https://instagram.com/p/abc/", "instagram"),
        ("https://x.com/user/status/1", "twitter"),
        ("https://mobile.twitter.com/user/status/1", "twitter"),
    ],
)
def test_social_platform_matches_the_platform_host(url: str, platform: str) -> None:
    assert social_platform(url) == platform


@pytest.mark.parametrize(
    "url",
    [
        "https://netflix.com/title/1",
        "https://example.test/?next=youtube.com",
        "https://youtube.com.example.test/watch?v=abc",
        "https://notinstagram.com/p/abc/",
        "https://example.test/x.com",
        "",
    ],
)
def test_social_platform_ignores_lookalike_hosts(url: str) -> None:
    assert social_platform(url) == ""


def test_host_matches_exact_and_parent_domains_only() -> None:
    assert host_matches("youtube.com", "youtube.com")
    assert host_matches("WWW.YouTube.com.", "youtube.com")
    assert not host_matches("evilyoutube.com", "youtube.com")
    assert not host_matches("", "youtube.com")


def test_url_hostname_accepts_scheme_less_links() -> None:
    assert url_hostname("www.example.test/path") == "www.example.test"
    assert url_hostname("//cdn.example.test/a.mp4") == "cdn.example.test"


def test_page_tool_classifies_lookalikes_as_articles() -> None:
    # classify_url reads no instance state; skip __init__ so no feed paths are resolved.
    classify_url = object.__new__(PageTool).classify_url
    assert classify_url("https://netflix.com/title/1") == "article"
    assert classify_url("https://example.test/watch?ref=youtube.com") == "article"
    assert classify_url("https://www.youtube.com/watch?v=abc") == "youtube"
    assert classify_url("https://cdn.example.test/clip.mp4") == "video"
