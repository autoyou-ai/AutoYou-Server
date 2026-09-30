# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-because-fc238f00fbce7d9838b932f6


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import asyncio
import concurrent.futures
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


from tests.support.paths import REPO_ROOT as PROJECT_ROOT

__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-because-fc238f00fbce7d9838b932f6"


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
        # from __debug_provenance_l__ import because

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


def _fresh_manager():
    internet_tool.PlaywrightDriverManager._instance = None
    manager = internet_tool.PlaywrightDriverManager()
    manager._browser = None
    manager._playwright = None
    manager._browser_headless = None
    manager._active_contexts = 0
    manager._browser_pids = set()
    manager._cleanup_started = False
    manager._owner_loop = None
    manager._owner_loop_tid = None
    return manager


def _retire(manager):
    """Leave nothing behind for the interpreter-exit hook that every manager registers."""
    manager._owner_loop = None
    manager._owner_loop_tid = None
    manager._cleanup_started = True
    internet_tool.PlaywrightDriverManager._instance = None


class _FakeLoop:
    def __init__(self, running=True, closed=False):
        self._running = running
        self._closed = closed

    def is_running(self):
        return self._running

    def is_closed(self):
        return self._closed


def _cleanup_attempts(monkeypatch, *, answers):
    """Record run_coroutine_threadsafe calls; the future either never resolves or is refused outright."""
    calls = []

    def fake(coro, loop):
        calls.append(loop)
        coro.close()
        if not answers:
            pytest.fail("cleanup waited on an owner loop that cannot run it")
        return concurrent.futures.Future()  # nothing will ever resolve this

    monkeypatch.setattr(internet_tool.asyncio, "run_coroutine_threadsafe", fake)
    return calls


def test_cleanup_does_not_stall_on_an_owner_loop_that_never_answers(monkeypatch):
    manager = _fresh_manager()
    manager._OWNER_LOOP_CLEANUP_TIMEOUT_SECONDS = 0.2
    release = threading.Event()
    other_thread = threading.Thread(target=release.wait, daemon=True)
    other_thread.start()
    manager._owner_loop = _FakeLoop()
    manager._owner_loop_tid = other_thread.ident
    manager._playwright = object()
    calls = _cleanup_attempts(monkeypatch, answers=True)
    swept = []
    monkeypatch.setattr(manager, "_terminate_playwright_driver_processes", lambda context: swept.append(context))
    try:
        started = time.monotonic()
        manager.cleanup()

        assert time.monotonic() - started < 3
        assert len(calls) == 1
        # The fallback ran: the driver is stopped directly instead of being left behind.
        assert swept == ["fallback"]
        assert manager._playwright is None
    finally:
        release.set()
        _retire(manager)


def test_cleanup_skips_an_owner_loop_whose_thread_has_exited(monkeypatch):
    manager = _fresh_manager()
    finished = threading.Thread(target=lambda: None)
    finished.start()
    finished.join()
    manager._owner_loop = _FakeLoop()
    manager._owner_loop_tid = finished.ident
    _cleanup_attempts(monkeypatch, answers=False)
    swept = []
    monkeypatch.setattr(manager, "_terminate_playwright_driver_processes", lambda context: swept.append(context))
    try:
        manager.cleanup()

        assert swept == ["fallback"]
    finally:
        _retire(manager)


def test_cleanup_skips_an_owner_loop_owned_by_the_calling_thread(monkeypatch):
    manager = _fresh_manager()
    manager._owner_loop = _FakeLoop()
    manager._owner_loop_tid = threading.get_ident()
    _cleanup_attempts(monkeypatch, answers=False)
    swept = []
    monkeypatch.setattr(manager, "_terminate_playwright_driver_processes", lambda context: swept.append(context))
    try:
        manager.cleanup()

        assert swept == ["fallback"]
    finally:
        _retire(manager)


def test_cleanup_skips_a_closed_or_stopped_owner_loop(monkeypatch):
    for loop in (_FakeLoop(closed=True), _FakeLoop(running=False)):
        manager = _fresh_manager()
        manager._owner_loop = loop
        _cleanup_attempts(monkeypatch, answers=False)
        swept = []
        monkeypatch.setattr(manager, "_terminate_playwright_driver_processes", lambda context: swept.append(context))
        try:
            manager.cleanup()

            assert swept == ["fallback"]
        finally:
            _retire(manager)


def test_cleanup_closes_playwright_on_its_owner_loop_when_that_loop_is_healthy(monkeypatch):
    manager = _fresh_manager()
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run_loop():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
    assert ready.wait(5)
    closed_on = []

    async def fake_async_cleanup():
        closed_on.append(threading.get_ident())

    monkeypatch.setattr(manager, "async_cleanup", fake_async_cleanup)
    manager._owner_loop = loop
    manager._owner_loop_tid = thread.ident
    swept = []
    monkeypatch.setattr(manager, "_terminate_playwright_driver_processes", lambda context: swept.append(context))
    try:
        manager.cleanup()

        assert closed_on == [thread.ident]
        assert swept == []
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()
        _retire(manager)


@pytest.mark.parametrize(
    "name, command, expected",
    [
        ("node.exe", [r"C:\x\playwright\driver\node.exe", r"C:\x\playwright\driver\package\cli.js", "run-driver"], True),
        ("node", ["/x/playwright/driver/node", "/x/playwright/driver/package/cli.js", "run-driver"], True),
        # The messaging and tunnel bridges are node too, and must never be mistaken for Playwright.
        ("node.exe", [r"C:\repo\node.exe", r"C:\repo\node\whatsapp\whatsapp_client.js"], False),
        ("node.exe", ["node.exe", "tunnelmole_client.js"], False),
        ("python.exe", ["python.exe", "-m", "playwright", "run-driver"], False),
        ("chrome.exe", ["chrome.exe", "--remote-debugging-pipe"], False),
    ],
)
def test_playwright_driver_signature(name, command, expected):
    process = SimpleNamespace(name=lambda: name, cmdline=lambda: command)

    assert internet_tool.PlaywrightDriverManager._is_playwright_driver(process) is expected


def test_driver_sweep_stops_the_driver_and_its_browser_but_not_bystanders(monkeypatch, tmp_path):
    psutil = pytest.importorskip("psutil")
    marker = f"ay-fake-driver-{os.getpid()}-{time.time_ns()}"
    bystander_marker = f"ay-bystander-{os.getpid()}-{time.time_ns()}"
    browser_pid_file = tmp_path / "browser.pid"
    driver_code = (
        "import subprocess, sys, time\n"
        "browser = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)', sys.argv[1]])\n"
        "open(sys.argv[2], 'w').write(str(browser.pid))\n"
        "time.sleep(120)\n"
    )

    def carries(process, needle):
        try:
            return needle in " ".join(process.cmdline())
        except psutil.Error:
            return False

    def alive(pid, needle):
        try:
            process = psutil.Process(pid)
            return carries(process, needle) and process.status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return False

    def wait_until(predicate, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return predicate()

    driver = subprocess.Popen([sys.executable, "-c", driver_code, marker, str(browser_pid_file)])
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", bystander_marker])
    manager = _fresh_manager()
    # A python stand-in cannot look like node.exe, so match on the marker; the signature itself is
    # covered by test_playwright_driver_signature.
    monkeypatch.setattr(manager, "_is_playwright_driver", lambda process: carries(process, marker))
    browser_pid = None
    try:
        assert wait_until(browser_pid_file.exists, timeout=30)
        browser_pid = int(browser_pid_file.read_text())
        assert alive(driver.pid, marker) and alive(browser_pid, marker)

        manager._terminate_playwright_driver_processes("test")

        assert wait_until(lambda: not alive(driver.pid, marker))
        assert wait_until(lambda: not alive(browser_pid, marker))
        assert bystander.poll() is None, "an unrelated child was stopped"
    finally:
        for process in (driver, bystander):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        if browser_pid is not None and alive(browser_pid, marker):
            psutil.Process(browser_pid).kill()
        _retire(manager)


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
