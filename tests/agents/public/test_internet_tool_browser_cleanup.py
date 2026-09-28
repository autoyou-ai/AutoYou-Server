# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


from tests.support.paths import REPO_ROOT as PROJECT_ROOT
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from autoyou_agents.internet_agent import internet_tool


class _FakePage:
    def __init__(self):
        self.close_count = 0
        self.default_timeout = None
        self.default_navigation_timeout = None

    def set_default_timeout(self, value):
        self.default_timeout = value

    def set_default_navigation_timeout(self, value):
        self.default_navigation_timeout = value

    async def close(self):
        self.close_count += 1


class _FakeContext:
    def __init__(self):
        self.page = _FakePage()
        self.close_count = 0
        self.routes = []

    async def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    async def new_page(self):
        return self.page

    async def close(self):
        self.close_count += 1


class _FakeBrowserForContextManager:
    def __init__(self):
        self.context = _FakeContext()

    async def new_context(self, **kwargs):
        del kwargs
        return self.context


class _FakeDriverManager:
    def __init__(self, browser):
        self.browser = browser
        self.release_count = 0
        self.requested_headless = []

    async def get_browser(self, headless=None):
        self.requested_headless.append(headless)
        return self.browser

    async def release_browser(self):
        self.release_count += 1


class _FakeBrowser:
    def __init__(self, name):
        self.name = name
        self.close_count = 0

    @property
    def contexts(self):
        return []

    async def close(self):
        self.close_count += 1


class _FakeLauncher:
    def __init__(self):
        self.launch_count = 0
        self.browsers = []

    async def launch(self, **kwargs):
        del kwargs
        self.launch_count += 1
        launch_number = self.launch_count
        if launch_number == 1:
            await asyncio.sleep(0.01)
        browser = _FakeBrowser(f"browser-{launch_number}")
        self.browsers.append(browser)
        return browser


class _FakePlaywright:
    def __init__(self, launcher):
        self.chromium = launcher
        self.firefox = launcher
        self.webkit = launcher
        self.stop_count = 0

    async def stop(self):
        self.stop_count += 1


class _FakePlaywrightStarter:
    def __init__(self, playwright):
        self.playwright = playwright
        self.start_count = 0

    async def start(self):
        self.start_count += 1
        return self.playwright


@pytest.mark.asyncio
async def test_get_playwright_browser_releases_browser_lease_after_context_exit(monkeypatch):
    fake_browser = _FakeBrowserForContextManager()
    fake_manager = _FakeDriverManager(fake_browser)

    monkeypatch.setattr(internet_tool, "_driver_manager", fake_manager)

    async with internet_tool.get_playwright_browser(headless=True) as page:
        assert page is fake_browser.context.page

    assert fake_manager.requested_headless == [True]
    assert fake_manager.release_count == 1
    assert fake_browser.context.page.close_count == 1
    assert fake_browser.context.close_count == 1


@pytest.mark.asyncio
async def test_driver_manager_serializes_concurrent_launch_and_cleans_up_last_release(monkeypatch):
    internet_tool.PlaywrightDriverManager._instance = None
    manager = internet_tool.PlaywrightDriverManager()
    manager._browser = None
    manager._playwright = None
    manager._browser_headless = None
    manager._active_contexts = 0
    manager._browser_pids = set()
    manager._browser_condition = None
    manager._browser_condition_loop = None

    launcher = _FakeLauncher()
    playwright = _FakePlaywright(launcher)
    starter = _FakePlaywrightStarter(playwright)

    monkeypatch.setattr(internet_tool, "async_playwright", lambda: starter)
    monkeypatch.setattr(manager, "_cleanup_tracked_processes", lambda log_context: None)

    try:
        browser_one, browser_two = await asyncio.gather(
            manager.get_browser(headless=True),
            manager.get_browser(headless=True),
        )

        assert browser_one is browser_two
        assert starter.start_count == 1
        assert launcher.launch_count == 1
        assert manager._active_contexts == 2

        shared_browser = launcher.browsers[0]

        await manager.release_browser()
        assert manager._active_contexts == 1
        assert shared_browser.close_count == 0
        assert playwright.stop_count == 0

        await manager.release_browser()
        assert manager._active_contexts == 0
        assert shared_browser.close_count == 1
        assert playwright.stop_count == 1
        assert manager._browser is None
        assert manager._playwright is None
    finally:
        await manager.async_cleanup()
        internet_tool.PlaywrightDriverManager._instance = None


class _FakeRoute:
    def __init__(self):
        self.continued = 0
        self.aborted = []

    async def continue_(self):
        self.continued += 1

    async def abort(self, reason=None):
        self.aborted.append(reason)


class _RouteCapturingContext:
    def __init__(self):
        self.handler = None

    async def route(self, pattern, handler):
        del pattern
        self.handler = handler


async def _install_guard_with(monkeypatch, checker):
    """Install the SSRF guard with a stubbed safety check and return its handler."""
    import shared.url_safety as url_safety

    internet_tool._ssrf_host_decisions.clear()
    monkeypatch.delenv("AUTOYOU_INTERNET_ALLOW_PRIVATE_IPS", raising=False)
    monkeypatch.setattr(url_safety, "is_safe_http_url", checker)

    context = _RouteCapturingContext()
    await internet_tool._install_ssrf_route_guard(context)
    assert context.handler is not None
    return context.handler


@pytest.mark.asyncio
async def test_ssrf_guard_checks_each_host_once(monkeypatch):
    """A page's hundreds of subresources must not each pay a DNS resolution."""
    checked = []

    def _fake_is_safe(url, **kwargs):
        checked.append(url)
        return True

    guard = await _install_guard_with(monkeypatch, _fake_is_safe)

    for path in ("/", "/a.js", "/b.css", "/c.png"):
        route = _FakeRoute()
        await guard(route, SimpleNamespace(url=f"https://cdn.example.com{path}"))
        assert route.continued == 1

    assert len(checked) == 1

    # A different host is its own decision.
    await guard(_FakeRoute(), SimpleNamespace(url="https://other.example.com/x"))
    assert len(checked) == 2


@pytest.mark.asyncio
async def test_ssrf_guard_resolves_a_cold_host_once_under_concurrency(monkeypatch):
    """Parallel subresources to one CDN must not each occupy a worker thread."""
    in_flight = threading.Event()
    checked = []

    def _slow_is_safe(url, **kwargs):
        checked.append(url)
        in_flight.wait(timeout=5)
        return True

    guard = await _install_guard_with(monkeypatch, _slow_is_safe)

    routes = [_FakeRoute() for _ in range(8)]
    tasks = [
        asyncio.create_task(guard(route, SimpleNamespace(url=f"https://cdn.example.com/asset-{index}.js")))
        for index, route in enumerate(routes)
    ]
    await asyncio.sleep(0.05)
    in_flight.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)

    assert len(checked) == 1, "the cold host was resolved more than once"
    assert all(route.continued == 1 for route in routes)


@pytest.mark.asyncio
async def test_ssrf_guard_blocks_disallowed_hosts(monkeypatch):
    guard = await _install_guard_with(monkeypatch, lambda url, **kwargs: False)

    route = _FakeRoute()
    await guard(route, SimpleNamespace(url="http://169.254.169.254/latest/meta-data/"))

    assert route.continued == 0
    assert route.aborted == ["blockedbyclient"]


@pytest.mark.asyncio
async def test_ssrf_guard_blocks_when_the_safety_check_raises(monkeypatch):
    def _raise(url, **kwargs):
        raise RuntimeError("resolution exploded")

    guard = await _install_guard_with(monkeypatch, _raise)

    route = _FakeRoute()
    await guard(route, SimpleNamespace(url="https://unresolvable.example/"))

    assert route.continued == 0
    assert route.aborted == ["blockedbyclient"]


@pytest.mark.asyncio
async def test_ssrf_guard_does_not_block_the_event_loop(monkeypatch):
    """The check runs off-loop, so a slow DNS lookup cannot stall the server."""
    released = threading.Event()

    def _slow_is_safe(url, **kwargs):
        # A genuinely blocking wait: this returns only because it runs in a
        # worker thread while the event loop keeps turning.
        released.wait(timeout=5)
        return True

    guard = await _install_guard_with(monkeypatch, _slow_is_safe)

    guard_task = asyncio.create_task(guard(_FakeRoute(), SimpleNamespace(url="https://slow.example/")))
    # The loop stays responsive while the check is in flight.
    await asyncio.sleep(0.05)
    assert not guard_task.done()
    released.set()
    await asyncio.wait_for(guard_task, timeout=5)


@pytest.mark.asyncio
async def test_driver_manager_reports_missing_playwright(monkeypatch):
    internet_tool.PlaywrightDriverManager._instance = None
    manager = internet_tool.PlaywrightDriverManager()
    manager._browser = None
    manager._playwright = None
    manager._browser_headless = None
    manager._active_contexts = 0
    manager._browser_condition = None
    manager._browser_condition_loop = None

    monkeypatch.setattr(internet_tool, "async_playwright", None)

    try:
        with pytest.raises(RuntimeError, match="Playwright is not installed"):
            await manager.get_browser(headless=True)
    finally:
        await manager.async_cleanup()
        internet_tool.PlaywrightDriverManager._instance = None
