# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-f61a706e9f8b9460f4787913

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""
Comprehensive test suite for InternetTool.
Tests all major functionality including search, scraping, screenshots, and page navigation.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import json
import os
import sys
import time
import asyncio
import logging
import tempfile
import unittest
from datetime import datetime
from typing import Dict, Any
from unittest.mock import AsyncMock, patch

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-f61a706e9f8b9460f4787913"


# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import internet_tool
from internet_tool import InternetTool, internet_search, scrape_website, take_screenshot, navigate_page
from internet_tool import PlaywrightDriverManager, _goto_page_ready, _page_metrics_are_stable, _resolve_headless_override

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class _AsyncPageContext:
    def __init__(self, page):
        self.page = page

    async def __aenter__(self):
        return self.page

    async def __aexit__(self, exc_type, exc, tb):
        return False


# Tests that reach the real internet (and, for most of them, launch a real
# browser) are opt-in. They used to run by default under a
# WindowsSelectorEventLoopPolicy set at import - a loop that cannot spawn
# subprocesses, so Playwright failed with a bare NotImplementedError every
# time and every browser assertion was skipped by its own `if status ==
# 'success'` guard. They passed while testing nothing, and the policy leaked
# into every other test module in the session. Skipping honestly is better,
# and opting in now actually exercises the browser.
LIVE_INTERNET_TESTS_ENV = "AUTOYOU_RUN_LIVE_INTERNET_TESTS"
_LIVE_INTERNET_TESTS_ENABLED = os.environ.get(LIVE_INTERNET_TESTS_ENV) == "1"
_LIVE_ONLY_REASON = f"Live network/browser test. Set {LIVE_INTERNET_TESTS_ENV}=1 to run."
live_internet_test = unittest.skipUnless(_LIVE_INTERNET_TESTS_ENABLED, _LIVE_ONLY_REASON)


class TestInternetTool(unittest.TestCase):
    """Test cases for InternetTool functionality."""

    @classmethod
    def setUpClass(cls):
        """Set up test class with InternetTool instance."""
        cls.tool = InternetTool()
        cls.test_urls = {
            'simple_html': 'https://example.com',  # Reliable test page
            'form_page': 'https://postman-echo.com/post',  # Reliable API endpoint
            'json_api': 'https://postman-echo.com/get'  # Reliable JSON API
        }
        # Keep a live run from spraying visible browser windows across the
        # desktop; an operator who wants to watch can still set this to 0.
        cls._restore_headless = os.environ.get('AUTOYOU_BROWSER_HEADLESS')
        if cls._restore_headless is None:
            os.environ['AUTOYOU_BROWSER_HEADLESS'] = '1'

    @classmethod
    def tearDownClass(cls):
        """Ensure Playwright resources are cleaned up after tests."""
        try:
            from internet_tool import _driver_manager
            # Prefer async cleanup to ensure resources close on the current event loop
            asyncio.run(_driver_manager.async_cleanup())
        except Exception as e:
            logger.warning(f"Playwright cleanup warning: {e}")
        if cls._restore_headless is None:
            os.environ.pop('AUTOYOU_BROWSER_HEADLESS', None)
        else:
            os.environ['AUTOYOU_BROWSER_HEADLESS'] = cls._restore_headless

    def setUp(self):
        """Set up each test."""
        logger.info(f"Starting test: {self._testMethodName}")
    
    def tearDown(self):
        """Clean up after each test."""
        logger.info(f"Completed test: {self._testMethodName}")
    
    @live_internet_test
    def test_internet_search_basic(self):
        """Test basic Internet search functionality."""
        logger.info("Testing Internet search with basic query")
        
        result = asyncio.run(self.tool.internet_search("Python programming", max_results=5))
        
        # Check result structure
        self.assertIsInstance(result, dict)
        self.assertIn('status', result)
        self.assertIn('results', result)
        self.assertIn('results_count', result)
        self.assertIn('query', result)
        
        # Check that search executed (even if no results due to bot detection)
        self.assertIn(result['status'], ['success', 'error'])
        
        if result['status'] == 'success':
            self.assertIsInstance(result['results'], list)
            self.assertIsInstance(result['results_count'], int)
            self.assertEqual(result['query'], "Python programming")
            
            # If results found, check structure
            if result['results_count'] > 0:
                first_result = result['results'][0]
                self.assertIn('title', first_result)
                self.assertIn('url', first_result)
                self.assertIn('description', first_result)  # DuckDuckGo returns 'description' not 'snippet'
        
        logger.info(f"Internet search result: {result['status']} with {result.get('results_count', 0)} results")
    
    @live_internet_test
    def test_internet_search_with_requests_fallback(self):
        """Test Internet search fallback using requests (DuckDuckGo)."""
        logger.info("Testing Internet search fallback with requests")
        
        result = self.tool.internet_search_with_requests("machine learning", max_results=3)
        
        # Check result structure
        self.assertIsInstance(result, dict)
        self.assertIn('status', result)
        self.assertIn('results', result)
        self.assertIn('results_count', result)
        
        if result['status'] == 'success' and result['results_count'] > 0:
            first_result = result['results'][0]
            self.assertIn('title', first_result)
            self.assertIn('url', first_result)
            self.assertTrue(first_result['url'].startswith('http'))
        
        logger.info(f"Requests search result: {result['status']} with {result.get('results_count', 0)} results")
    
    @live_internet_test
    def test_scrape_website_basic(self):
        """Test basic website scraping functionality."""
        logger.info("Testing website scraping")
        
        result = asyncio.run(self.tool.scrape_website(self.test_urls['simple_html']))
        
        # Check result structure
        self.assertIsInstance(result, dict)
        self.assertIn('status', result)
        self.assertIn('url', result)
        self.assertIn('title', result)
        self.assertIn('text_content', result)
        self.assertIn('links', result)
        self.assertIn('media', result)
        self.assertIn('metadata', result)
        
        if result['status'] == 'success':
            self.assertEqual(result['url'], self.test_urls['simple_html'])
            self.assertIsInstance(result['text_content'], str)
            self.assertIsInstance(result['links'], list)
            self.assertIsInstance(result['media'], list)
            self.assertIsInstance(result['metadata'], dict)
            
            # Should have some content
            self.assertGreater(len(result['text_content']), 0)
        
        logger.info(f"Website scraping result: {result['status']}")
    
    @live_internet_test
    def test_scrape_website_without_media(self):
        """Test website scraping without media extraction."""
        logger.info("Testing website scraping without media")
        
        result = asyncio.run(self.tool.scrape_website(self.test_urls['simple_html'], extract_media=False))
        
        if result['status'] == 'success':
            # Media should be empty when extract_media=False
            self.assertEqual(len(result['media']), 0)
        
        logger.info(f"Website scraping (no media) result: {result['status']}")

    def test_scrape_website_uses_domcontentloaded_readiness(self):
        """Test scrape readiness avoids networkidle hangs on active news pages."""
        fake_page = type("FakePage", (), {})()
        fake_page.goto = AsyncMock()
        fake_page.wait_for_load_state = AsyncMock()
        fake_page.wait_for_function = AsyncMock()
        fake_page.evaluate = AsyncMock(
            return_value={
                "text_len": 120,
                "node_count": 8,
                "scroll_height": 640,
            }
        )
        fake_page.wait_for_timeout = AsyncMock()
        fake_page.content = AsyncMock(
            return_value=(
                "<html><head><title>Example</title></head><body><main><h1>Loaded</h1>"
                f"<p>Body text. {'Article sentence. ' * 20}</p></main></body></html>"
            )
        )

        with patch("internet_tool.get_playwright_browser", return_value=_AsyncPageContext(fake_page)):
            result = asyncio.run(self.tool.scrape_website("https://example.com", extract_media=False))

        self.assertEqual(result["status"], "success")
        self.assertIn("Loaded", result["text_content"])
        fake_page.goto.assert_awaited_once()
        _, goto_kwargs = fake_page.goto.await_args
        self.assertEqual(goto_kwargs.get("wait_until"), "domcontentloaded")
        fake_page.wait_for_function.assert_awaited()
        self.assertGreaterEqual(fake_page.evaluate.await_count, 2)

    def _scrape_html(self, html: str, url: str = "https://example.com/section/page.html", **kwargs):
        """Run scrape_website against a fixed HTML document."""
        fake_page = type("FakePage", (), {})()
        fake_page.goto = AsyncMock()
        fake_page.wait_for_load_state = AsyncMock()
        fake_page.wait_for_function = AsyncMock()
        fake_page.evaluate = AsyncMock(
            return_value={"text_len": 120, "node_count": 8, "scroll_height": 640}
        )
        fake_page.wait_for_timeout = AsyncMock()
        fake_page.content = AsyncMock(return_value=html)

        with patch("internet_tool.get_playwright_browser", return_value=_AsyncPageContext(fake_page)):
            return asyncio.run(self.tool.scrape_website(url, **kwargs))

    def test_scrape_website_drops_script_text_and_prefers_main_content(self):
        """Inline scripts must not eat the text budget ahead of real content."""
        article = "Breaking headline text. " * 30
        html = (
            "<html><head><title>News</title>"
            '<script>window.__DATA__={"junk":"' + ("x" * 4000) + '"};</script>'
            "</head><body>"
            "<nav>Home Sections Watch Listen Live TV</nav>"
            f"<main><h1>Top story</h1><p>{article}</p></main>"
            "<style>.a{color:red}</style>"
            "</body></html>"
        )

        result = self._scrape_html(html, extract_media=False)

        self.assertEqual(result["status"], "success")
        self.assertIn("Breaking headline text.", result["text_content"])
        self.assertNotIn("__DATA__", result["text_content"])
        self.assertNotIn("color:red", result["text_content"])
        # Main-region text wins over the nav chrome outside it.
        self.assertNotIn("Live TV", result["text_content"])

    def test_scrape_website_resolves_relative_links_and_deduplicates(self):
        """Every href form must come back absolute, and only once."""
        html = (
            "<html><head><title>Links</title></head><body><main>"
            f"<p>{'Readable body copy. ' * 20}</p>"
            '<a href="/world">World</a>'
            '<a href="/world">World again</a>'
            '<a href="other.html">Sibling</a>'
            '<a href="//cdn.example.com/x.html">Protocol relative</a>'
            '<a href="javascript:void(0)">Junk</a>'
            '<a href="#top">Anchor</a>'
            "</main></body></html>"
        )

        result = self._scrape_html(html, extract_media=False)

        urls = [link["url"] for link in result["links"]]
        self.assertEqual(
            urls,
            [
                "https://example.com/world",
                "https://example.com/section/other.html",
                "https://cdn.example.com/x.html",
            ],
        )

    def test_scrape_website_omits_media_unless_it_is_asked_for(self):
        """Image alt text must not be summarized as page content by default."""
        html = (
            "<html><head><title>Example source</title></head><body><main>"
            f"<h1>Example article headline</h1><p>{'Real article text. ' * 20}</p>"
            '<img src="/a.jpg" alt="Example image caption that is not page text">'
            "</main></body></html>"
        )

        default_result = self._scrape_html(html)
        opted_in = self._scrape_html(html, extract_media=True)

        self.assertEqual(default_result["media"], [])
        self.assertNotIn("Example image caption", json.dumps(default_result))
        # Still available to a caller that actually wants the pictures.
        self.assertEqual(len(opted_in["media"]), 1)
        self.assertIn("Example image caption", opted_in["media"][0]["alt"])

    def test_scrape_result_carries_the_real_fetch_time(self):
        """The model ignores the system clock, so the date travels with the data."""
        html = f"<html><head><title>T</title></head><body><main><p>{'Body. ' * 60}</p></main></body></html>"

        result = self._scrape_html(html)

        self.assertEqual(
            result["retrieved_at"][:10], datetime.now().date().isoformat()
        )

    def test_search_results_carry_the_real_fetch_time(self):
        payload = self._bing_rss("https://source-a.example/")

        result = self._bing_search("breaking news", payload)

        self.assertEqual(
            result["retrieved_at"][:10], datetime.now().date().isoformat()
        )

    def test_scrape_website_reports_pages_with_no_readable_text(self):
        """A bot-block shell must not be reported as a successful scrape."""
        result = self._scrape_html(
            "<html><head><title>source-c.example</title></head><body>source-c.example</body></html>",
            url="https://source-c.example/",
            extract_media=False,
        )

        self.assertEqual(result["status"], "partial")
        self.assertIn("readable text", result["message"])

    def test_internet_search_falls_back_to_bing_when_duckduckgo_challenges(self):
        """The anti-bot challenge must reach Bing RSS, not just report failure."""
        challenge_response = type("FakeResponse", (), {})()
        challenge_response.text = "<html>Bots use DuckDuckGo too</html>"
        challenge_response.content = challenge_response.text.encode()
        challenge_response.raise_for_status = lambda: None

        bing_payload = {
            "status": "success",
            "results": [{"title": "Example source", "url": "https://source-a.example/", "snippet": "s", "description": "s"}],
            "results_count": 1,
            "query": "source-a public updates",
            "provider": "bing_rss",
        }

        with patch.object(self.tool.session, "get", return_value=challenge_response), \
             patch.object(self.tool, "internet_search_with_bing_rss", return_value=bing_payload) as bing:
            result = self.tool.internet_search_with_requests("source-a public updates", max_results=5)

        bing.assert_called_once_with("source-a public updates", 5)
        self.assertEqual(result["provider"], "bing_rss")
        self.assertEqual(result["results_count"], 1)

    def test_internet_search_does_not_retry_a_blocked_search_in_playwright(self):
        """A provider challenge is reported, not retried with another fingerprint."""
        challenge_response = type("FakeResponse", (), {})()
        challenge_response.text = "<html>Bots use DuckDuckGo too</html>"
        challenge_response.content = challenge_response.text.encode()
        challenge_response.raise_for_status = lambda: None

        request_error = {
            "status": "error",
            "error": "DuckDuckGo Lite returned an anti-bot challenge and Bing RSS fallback did not return results",
            "results": [],
            "results_count": 0,
            "query": "source-a public updates",
            "provider": "duckduckgo_lite",
        }

        async def _unexpected_browser(*args, **kwargs):
            raise AssertionError("Playwright must not retry a provider challenge")

        with patch.object(self.tool.session, "get", return_value=challenge_response), \
             patch.object(self.tool, "internet_search_with_bing_rss", return_value=request_error), \
             patch("internet_tool.get_playwright_browser", new=_unexpected_browser):
            result = asyncio.run(self.tool.internet_search("source-a public updates", max_results=5))

        assert result == request_error

    @staticmethod
    def _bing_rss(*urls: str) -> str:
        items = "".join(
            f"<item><title>Result {index}</title><link>{url}</link>"
            f"<description>Snippet {index}</description></item>"
            for index, url in enumerate(urls, start=1)
        )
        return f"<rss><channel>{items}</channel></rss>"

    def _bing_search(self, query: str, payload: str, max_results: int = 5):
        response = type("FakeResponse", (), {})()
        response.text = payload
        response.raise_for_status = lambda: None
        with patch.object(self.tool.session, "get", return_value=response):
            return self.tool.internet_search_with_bing_rss(query, max_results)

    def test_bing_rss_keeps_only_on_domain_results_for_site_queries(self):
        """Bing ignores `site:`, so off-domain hits must not pass as the requested source's."""
        payload = self._bing_rss(
            "https://source-b.example/",
            "https://source-a.example/world",
            "https://edition.source-a.example/updates",
        )

        result = self._bing_search("site:source-a.example public updates", payload)

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            [item["url"] for item in result["results"]],
            ["https://source-a.example/world", "https://edition.source-a.example/updates"],
        )

    def test_bing_rss_fails_a_site_query_it_cannot_satisfy(self):
        """Failing lets the browser-backed provider, which honours site:, run."""
        payload = self._bing_rss("https://source-b.example/", "https://source-c.example/")

        result = self._bing_search("site:source-a.example public updates", payload)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["results_count"], 0)
        self.assertIn("source-a.example", result["error"])

    def test_site_filter_uses_host_for_paths(self):
        self.assertEqual(
            self.tool._site_filter_domain("site:bbc.com/news latest headlines"),
            "bbc.com",
        )

    def test_bing_site_query_moves_path_into_search_terms(self):
        self.assertEqual(
            self.tool._normalize_bing_site_query("site:bbc.com/news latest headlines"),
            "site:bbc.com latest headlines",
        )

    def test_bing_rss_leaves_unscoped_queries_alone(self):
        payload = self._bing_rss("https://source-b.example/", "https://source-a.example/")

        result = self._bing_search("breaking news", payload)

        self.assertEqual(result["results_count"], 2)

    def test_page_metrics_stability_is_generic(self):
        self.assertTrue(
            _page_metrics_are_stable(
                {"text_len": 400, "node_count": 25, "scroll_height": 900},
                {"text_len": 430, "node_count": 29, "scroll_height": 920},
            )
        )
        self.assertFalse(
            _page_metrics_are_stable(
                {"text_len": 400, "node_count": 25, "scroll_height": 900},
                {"text_len": 520, "node_count": 40, "scroll_height": 980},
            )
        )

    def test_goto_page_ready_waits_for_stable_metrics(self):
        fake_page = type("FakePage", (), {})()
        fake_page.goto = AsyncMock()
        fake_page.wait_for_load_state = AsyncMock()
        fake_page.wait_for_function = AsyncMock()
        fake_page.evaluate = AsyncMock(
            side_effect=[
                {"text_len": 40, "node_count": 5, "scroll_height": 120},
                {"text_len": 110, "node_count": 11, "scroll_height": 300},
                {"text_len": 116, "node_count": 12, "scroll_height": 309},
                {"text_len": 118, "node_count": 13, "scroll_height": 312},
            ]
        )
        fake_page.wait_for_timeout = AsyncMock()

        asyncio.run(_goto_page_ready(fake_page, "https://example.com"))

        fake_page.goto.assert_awaited_once()
        _, goto_kwargs = fake_page.goto.await_args
        self.assertEqual(goto_kwargs.get("wait_until"), "domcontentloaded")
        self.assertEqual(fake_page.evaluate.await_count, 4)
        self.assertEqual(fake_page.wait_for_timeout.await_count, 3)
    
    def test_goto_page_ready_stops_when_a_single_sample_hangs(self):
        """A hung evaluate must not outlive the readiness budget.

        A dynamic page once exceeded the documented readiness cap because the
        budget only gated the top of the loop; nothing bounded an await already
        in flight.
        """
        fake_page = type("FakePage", (), {})()
        fake_page.goto = AsyncMock()
        fake_page.wait_for_load_state = AsyncMock()
        fake_page.wait_for_function = AsyncMock()
        fake_page.wait_for_timeout = AsyncMock()

        async def _hang(*args, **kwargs):
            await asyncio.sleep(30)

        fake_page.evaluate = _hang

        async def _run():
            started = time.monotonic()
            await _goto_page_ready(fake_page, "https://example.com")
            return time.monotonic() - started

        with patch.object(internet_tool, "_PAGE_READY_BUDGET_MS", 300):
            elapsed = asyncio.run(_run())

        self.assertLess(elapsed, 5, "readiness ran past its budget into the hung sample")

    def test_goto_page_ready_gives_up_on_a_page_that_never_settles(self):
        """A live ticker must not hold the page open past the budget."""
        fake_page = type("FakePage", (), {})()
        fake_page.goto = AsyncMock()
        fake_page.wait_for_load_state = AsyncMock()
        fake_page.wait_for_function = AsyncMock()
        fake_page.wait_for_timeout = AsyncMock()

        counter = {"n": 0}

        async def _always_changing(*args, **kwargs):
            counter["n"] += 1
            return {
                "text_len": counter["n"] * 500,
                "node_count": counter["n"] * 50,
                "scroll_height": counter["n"] * 400,
            }

        fake_page.evaluate = _always_changing

        async def _run():
            started = time.monotonic()
            await _goto_page_ready(fake_page, "https://news.example/")
            return time.monotonic() - started

        with patch.object(internet_tool, "_PAGE_READY_BUDGET_MS", 300):
            elapsed = asyncio.run(_run())

        self.assertLess(elapsed, 5)
        self.assertGreater(counter["n"], 0, "the page was never sampled")

    @live_internet_test
    def test_take_screenshot_basic(self):
        """Test basic screenshot functionality."""
        logger.info("Testing screenshot capture")
        
        # Use temporary file for screenshot
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
            screenshot_path = tmp_file.name
        
        try:
            result = asyncio.run(self.tool.take_screenshot(self.test_urls['simple_html'], screenshot_path))
            
            # Check result structure
            self.assertIsInstance(result, dict)
            self.assertIn('status', result)
            self.assertIn('url', result)
            self.assertIn('screenshot_path', result)
            self.assertIn('title', result)
            
            if result['status'] == 'success':
                self.assertEqual(result['url'], self.test_urls['simple_html'])
                self.assertEqual(result['screenshot_path'], screenshot_path)
                
                # Check if screenshot file was created
                self.assertTrue(os.path.exists(screenshot_path))
                self.assertGreater(os.path.getsize(screenshot_path), 0)
            
            logger.info(f"Screenshot result: {result['status']}")
            
        finally:
            # Clean up screenshot file
            if os.path.exists(screenshot_path):
                os.unlink(screenshot_path)
    
    @live_internet_test
    def test_take_screenshot_auto_filename(self):
        """Test screenshot with automatic filename generation."""
        logger.info("Testing screenshot with auto filename")
        
        result = asyncio.run(self.tool.take_screenshot(self.test_urls['simple_html']))
        
        if result['status'] == 'success':
            screenshot_path = result['screenshot_path']
            self.assertIsInstance(screenshot_path, str)
            self.assertTrue(screenshot_path.endswith('.png'))
            
            # Clean up if file was created
            if os.path.exists(screenshot_path):
                os.unlink(screenshot_path)
        
        logger.info(f"Auto screenshot result: {result['status']}")
    
    @live_internet_test
    def test_navigate_page_basic(self):
        """Test basic page navigation functionality."""
        logger.info("Testing page navigation")
        
        actions = [
            {
                "type": "wait",
                "selector": "body",
                "value": "2"
            },
            {
                "type": "get_text",
                "selector": "h1"
            }
        ]
        
        result = asyncio.run(self.tool.navigate_page(self.test_urls['simple_html'], actions))
        
        # Check result structure
        self.assertIsInstance(result, dict)
        self.assertIn('status', result)
        self.assertIn('url', result)
        self.assertIn('final_url', result)
        self.assertIn('title', result)
        self.assertIn('actions_performed', result)
        self.assertIn('results', result)
        
        if result['status'] == 'success':
            self.assertEqual(result['url'], self.test_urls['simple_html'])
            self.assertIsInstance(result['results'], list)
            self.assertEqual(len(result['results']), len(actions))
            
            # Check action results
            for action_result in result['results']:
                self.assertIn('action', action_result)
                self.assertIn('status', action_result)
        
        logger.info(f"Page navigation result: {result['status']} with {result.get('actions_performed', 0)} actions")

    def test_navigate_page_accepts_json_encoded_actions(self):
        """Provider adapters may encode the action list as a JSON string."""
        fake_page = type("FakePage", (), {})()
        fake_page.goto = AsyncMock()
        fake_page.wait_for_load_state = AsyncMock()
        fake_page.wait_for_function = AsyncMock()
        # from __debug_provenance_p__ import submit
        fake_page.evaluate = AsyncMock(
            return_value={"text_len": 80, "node_count": 8, "scroll_height": 240}
        )
        fake_page.wait_for_timeout = AsyncMock()
        fake_page.wait_for_selector = AsyncMock()
        fake_page.title = AsyncMock(return_value="Example")
        fake_page.url = "https://example.com"
        fake_locator = type("FakeLocator", (), {})()
        fake_locator.text_content = AsyncMock(return_value="Synthetic page text")
        fake_page.locator = lambda selector: fake_locator

        encoded_actions = json.dumps([
            {"type": "get_text", "selector": "h1"},
        ])
        with patch("internet_tool.get_playwright_browser", return_value=_AsyncPageContext(fake_page)):
            result = asyncio.run(self.tool.navigate_page("https://example.com", encoded_actions))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["actions_performed"], 1)
        self.assertEqual(result["results"][0]["text"], "Synthetic page text")

    def test_navigate_page_rejects_malformed_actions_without_browser_work(self):
        result = asyncio.run(self.tool.navigate_page("https://example.com", "not-json"))

        self.assertEqual(result["status"], "error")
        self.assertIn("JSON-encoded list", result["error"])

    @live_internet_test
    def test_navigate_page_form_interaction(self):
        """Test page navigation with form interaction."""
        logger.info("Testing page navigation with form interaction")
        
        # Use a more reliable test page with a simple HTML form
        test_form_url = "https://example.com"
        
        actions = [
            {
                "type": "wait",
                "selector": "body",
                "value": "2"
            },
            {
                "type": "get_text",
                "selector": "h1"
            },
            {
                "type": "scroll",
                "value": "100"
            }
        ]
        
        result = asyncio.run(self.tool.navigate_page(test_form_url, actions))
        
        # The test should succeed with basic page interactions
        self.assertIn('status', result)
        
        if result['status'] == 'success':
            # Should have performed all actions
            self.assertEqual(result['actions_performed'], len(actions))
            
            # Check if actions were successful
            for action_result in result['results']:
                self.assertIn('status', action_result)
                # Most actions should succeed, but we'll be lenient for robustness
                if action_result['action'] in ['wait', 'scroll']:
                    self.assertEqual(action_result['status'], 'success')
        else:
            # If the service is unavailable, log it but don't fail the test
            logger.warning(f"Form navigation test skipped due to service unavailability: {result.get('error', 'Unknown error')}")
        
        logger.info(f"Form navigation result: {result['status']}")
    
    @live_internet_test
    def test_wrapper_functions(self):
        """Test the global wrapper functions."""
        logger.info("Testing wrapper functions")
        
        # Test internet_search wrapper
        search_result = asyncio.run(internet_search("test query", max_results=2))
        self.assertIsInstance(search_result, dict)
        self.assertIn('status', search_result)
        
        # Test scrape_website wrapper
        scrape_result = asyncio.run(scrape_website(self.test_urls['simple_html']))
        self.assertIsInstance(scrape_result, dict)
        self.assertIn('status', scrape_result)
        
        # Test navigate_page wrapper
        nav_result = asyncio.run(navigate_page(self.test_urls['simple_html'], [{"type": "wait", "selector": "body", "value": "1"}]))
        self.assertIsInstance(nav_result, dict)
        self.assertIn('status', nav_result)
        
        logger.info("Wrapper functions test completed")

    def test_browser_visibility_overrides(self):
        """Test explicit headed/headless browser option handling."""
        logger.info("Testing browser visibility overrides")

        self.assertIsNone(_resolve_headless_override())
        self.assertTrue(_resolve_headless_override(headless=True))
        self.assertFalse(_resolve_headless_override(headless=False))
        self.assertFalse(_resolve_headless_override(headed=True))
        self.assertTrue(_resolve_headless_override(headed=False))

        manager = PlaywrightDriverManager()
        headed_options = manager.get_browser_options(headless=False)
        headless_options = manager.get_browser_options(headless=True)

        self.assertFalse(headed_options['headless'])
        self.assertTrue(headless_options['headless'])
        self.assertIn('--window-size=1280,720', headed_options['args'])

        original_env = {
            'INTERNET_AGENT_BROWSER_HEADLESS': os.environ.get('INTERNET_AGENT_BROWSER_HEADLESS'),
            'AUTOYOU_INTERNET_BROWSER_HEADLESS': os.environ.get('AUTOYOU_INTERNET_BROWSER_HEADLESS'),
            'AUTOYOU_BROWSER_HEADLESS': os.environ.get('AUTOYOU_BROWSER_HEADLESS'),
        }
        try:
            for env_name in original_env:
                os.environ.pop(env_name, None)

            os.environ['AUTOYOU_BROWSER_HEADLESS'] = '1'
            self.assertTrue(manager._resolve_default_headless_setting())

            os.environ['AUTOYOU_INTERNET_BROWSER_HEADLESS'] = '0'
            self.assertFalse(manager._resolve_default_headless_setting())

            os.environ['INTERNET_AGENT_BROWSER_HEADLESS'] = '1'
            self.assertTrue(manager._resolve_default_headless_setting())
        finally:
            for env_name, env_value in original_env.items():
                if env_value is None:
                    os.environ.pop(env_name, None)
                else:
                    os.environ[env_name] = env_value

        logger.info("Browser visibility override test completed")
    
    @live_internet_test
    def test_error_handling(self):
        """Test error handling with invalid inputs."""
        logger.info("Testing error handling")
        
        # Test with invalid URL
        invalid_url = "https://this-domain-does-not-exist-12345.com"
        
        scrape_result = asyncio.run(self.tool.scrape_website(invalid_url))
        self.assertEqual(scrape_result['status'], 'error')
        self.assertIn('error', scrape_result)
        
        nav_result = asyncio.run(self.tool.navigate_page(invalid_url, []))
        self.assertEqual(nav_result['status'], 'error')
        self.assertIn('error', nav_result)
        
        # Test screenshot with invalid URL
        screenshot_result = asyncio.run(self.tool.take_screenshot(invalid_url))
        self.assertEqual(screenshot_result['status'], 'error')
        self.assertIn('error', screenshot_result)
        
        logger.info("Error handling test completed")


def run_performance_test():
    """Run a simple performance test."""
    print("\n" + "="*60)
    print("🚀 PERFORMANCE TEST")
    print("="*60)
    
    tool = InternetTool()
    
    # Test search performance
    start_time = time.time()
    result = asyncio.run(tool.internet_search("Python", max_results=3))
    search_time = time.time() - start_time
    print(f"🔍 Internet Search: {search_time:.2f}s - Status: {result['status']}")
    
    # Test scraping performance
    start_time = time.time()
    result = asyncio.run(tool.scrape_website("https://example.com"))
    scrape_time = time.time() - start_time
    print(f"🌐 Website Scraping: {scrape_time:.2f}s - Status: {result['status']}")
    
    # Test screenshot performance
    start_time = time.time()
    result = asyncio.run(tool.take_screenshot("https://example.com"))
    screenshot_time = time.time() - start_time
    print(f"📸 Screenshot: {screenshot_time:.2f}s - Status: {result['status']}")
    
    # Clean up screenshot if created
    if result['status'] == 'success' and 'screenshot_path' in result:
        screenshot_path = result['screenshot_path']
        if os.path.exists(screenshot_path):
            os.unlink(screenshot_path)
    
    print("="*60)


def main():
    """Run all tests."""
    print("="*60)
    print("🧪 INTERNET TOOL COMPREHENSIVE TEST SUITE")
    print("="*60)
    
    # Run unit tests
    print("\n📋 Running Unit Tests...")
    unittest.main(argv=[''], exit=False, verbosity=2)
    
    # Run performance test
    run_performance_test()
    
    print("\n✅ All tests completed!")
    print("="*60)


if __name__ == "__main__":
    main()
