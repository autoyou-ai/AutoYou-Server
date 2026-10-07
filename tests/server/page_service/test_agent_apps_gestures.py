# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Agent Apps in a real browser: touch, mouse, keyboard, search and saved layout.

Runs the page service on a loopback port with a synthetic registry and drives
Chromium with real touch input. Skipped when Playwright or its browser is not
installed. It never touches the operator's registry or configuration.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

import autoyou_page_service

APPS = [
    ("page_agent", "AutoYou Page", "Saved links and media."),
    ("notes_agent", "Notes Library", "Read your notes."),
    ("tasks_agent", "Tasks Mission Control", "Recurring tasks."),
    ("notify_agent", "Notify", "Timed notifications."),
    ("files_agent", "Files Agent", "Browse files."),
    ("audio_agent", "Audio Player", "Stream your music library."),
    ("earnings_agent", "Earnings", "Pending credits."),
]
STORE_KEY = "autoyou.agentApps.v2"


def _registry():
    return {
        "browser_base_url": None,
        "frontends": [
            {
                "agent_name": name,
                "title": title,
                "description": description,
                "frontend_port_registered": name != "earnings_agent",
                "launch_path": f"/agent/{name}/",
                "open_url": f"/agent/{name}/",
            }
            for name, title, description in APPS
        ],
    }


@pytest.fixture(scope="module")
def base_url():
    original = autoyou_page_service.load_frontend_registry
    autoyou_page_service.load_frontend_registry = _registry
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    service = autoyou_page_service.AutoYouPageService(port=port, host="127.0.0.1")
    server = uvicorn.Server(uvicorn.Config(service.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "page service did not start"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        autoyou_page_service.load_frontend_registry = original


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except Exception as exc:  # browser binaries not installed
            pytest.skip(f"Chromium is not available: {exc}")
        yield launched
        launched.close()


class Phone:
    """A touch phone: a page plus raw touch input over the DevTools protocol."""

    def __init__(self, browser, base_url, **context_args):
        self.context = browser.new_context(
            viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, **context_args
        )
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.cdp = self.context.new_cdp_session(self.page)
        self.url = f"{base_url}/websites"
        self.page.goto(self.url)
        self.page.wait_for_selector("#grid .cell")
        self.page.wait_for_timeout(500)

    def touch(self, kind, *points):
        self.cdp.send(
            "Input.dispatchTouchEvent",
            {"type": kind, "touchPoints": [{"x": x, "y": y, "id": i, "force": 0.5} for i, (x, y) in enumerate(points)]},
        )

    def names(self):
        return self.page.evaluate(
            "Array.from(document.querySelectorAll('#grid .cell:not([hidden])')).map(c => c.dataset.name)"
        )

    def center(self, name):
        return self.page.evaluate(
            "n => { const r = document.querySelector('.cell[data-name=\"' + n + '\"]').getBoundingClientRect();"
            " return [r.left + r.width / 2, r.top + r.height / 2]; }",
            name,
        )

    def stored(self):
        return self.page.evaluate("k => JSON.parse(localStorage.getItem(k))", STORE_KEY)

    def close(self):
        self.context.close()


@pytest.fixture()
def phone(browser, base_url):
    device = Phone(browser, base_url)
    yield device
    device.close()


def test_sliding_a_finger_previews_apps_without_opening_any(phone):
    first, _, third = phone.names()[:3]
    (x0, y0), (x2, _) = phone.center(first), phone.center(third)
    phone.touch("touchStart", (x0, y0))
    for step in range(1, 11):
        phone.touch("touchMove", (x0 + (x2 - x0) * step / 10, y0))
        phone.page.wait_for_timeout(25)
    phone.page.wait_for_timeout(80)
    assert phone.page.inner_text("#dock-name") == "Tasks Mission Control"
    phone.touch("touchEnd")
    phone.page.wait_for_timeout(400)
    assert phone.page.url == phone.url
    assert phone.page.evaluate("document.querySelector('.cell.is-focus').dataset.name") == third


def test_holding_lifts_an_app_and_the_new_order_is_saved(phone):
    names = phone.names()
    (xs, ys), (xd, yd) = phone.center(names[3]), phone.center(names[0])
    phone.touch("touchStart", (xs, ys))
    phone.page.wait_for_timeout(650)
    assert phone.page.evaluate("document.documentElement.classList.contains('is-dragging')")
    for step in range(1, 13):
        phone.touch("touchMove", (xs + (xd - xs) * step / 12, ys + (yd - ys) * step / 12))
        phone.page.wait_for_timeout(30)
    phone.page.wait_for_timeout(250)
    phone.touch("touchEnd")
    phone.page.wait_for_timeout(450)

    assert phone.names()[:2] == [names[3], names[0]]
    assert phone.page.url == phone.url
    assert phone.stored()["order"][:2] == [names[3], names[0]]
    phone.page.reload()
    phone.page.wait_for_selector("#grid .cell")
    phone.page.wait_for_timeout(400)
    assert phone.names()[:2] == [names[3], names[0]]


def test_a_tap_opens_and_a_vertical_swipe_scrolls(phone):
    phone.page.set_viewport_size({"width": 390, "height": 420})
    phone.page.wait_for_timeout(200)
    before = phone.page.evaluate("window.scrollY")
    phone.touch("touchStart", (195, 380))
    for step in range(1, 12):
        phone.touch("touchMove", (195, 380 - step * 28))
        phone.page.wait_for_timeout(16)
    phone.touch("touchEnd")
    phone.page.wait_for_timeout(500)
    assert phone.page.evaluate("window.scrollY") > before + 30
    assert phone.page.url == phone.url
    assert not phone.page.evaluate("document.documentElement.classList.contains('is-dragging')")

    phone.page.set_viewport_size({"width": 390, "height": 844})
    phone.page.evaluate("window.scrollTo(0, 0)")
    phone.page.wait_for_timeout(200)
    x, y = phone.center("notes_agent")
    phone.touch("touchStart", (x, y))
    phone.page.wait_for_timeout(60)
    phone.touch("touchEnd")
    phone.page.wait_for_url("**/agent/notes_agent/**", timeout=5000)


def test_two_fingers_resize_and_the_size_is_remembered(phone):
    before = phone.page.evaluate("parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--s'))")
    phone.touch("touchStart", (155, 420), (235, 420))
    phone.page.wait_for_timeout(60)
    for step in range(1, 9):
        phone.touch("touchMove", (155 - step * 8, 420), (235 + step * 8, 420))
        phone.page.wait_for_timeout(30)
    phone.touch("touchEnd")
    phone.page.wait_for_timeout(450)
    after = phone.page.evaluate("parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--s'))")
    assert after > before + 0.2
    assert abs(phone.stored()["scale"] - after) < 0.01


def test_search_uses_http_query_and_blocks_rearranging(phone):
    requests = []
    phone.page.on("request", lambda request: requests.append((request.method, request.url)))
    phone.page.fill("#q", "music")
    phone.page.wait_for_timeout(900)
    assert "audio_agent" in phone.names() and len(phone.names()) < len(APPS)
    assert any(method == "QUERY" and url.endswith("/api/websites") for method, url in requests)

    x, y = phone.center(phone.names()[0])
    phone.touch("touchStart", (x, y))
    phone.page.wait_for_timeout(600)
    assert not phone.page.evaluate("document.documentElement.classList.contains('is-dragging')")
    assert "Clear" in phone.page.inner_text("#toast")
    phone.touch("touchEnd")


def test_an_app_that_is_not_ready_explains_itself(phone):
    phone.page.evaluate("document.querySelector('.cell[data-name=earnings_agent] a').click()")
    phone.page.wait_for_timeout(300)
    assert phone.page.url == phone.url
    assert "not ready" in phone.page.inner_text("#toast").lower()


def test_native_shell_keeps_the_layout_when_web_storage_does_not(browser, base_url):
    """The iOS in-app browser has no persistent web storage; the page mirrors to the shell."""
    shell_script = """
      (function () {
        var saved = window.name.indexOf('LAYOUT:') === 0 ? window.name.slice(7) : null;
        window.webkit = { messageHandlers: { autoyouAppsLayout: { postMessage: function (m) {
          if (m.op === 'set') { window.name = 'LAYOUT:' + m.value; return Promise.resolve(true); }
          return Promise.resolve(saved);
        } } } };
        Object.defineProperty(window, 'localStorage', { get: function () { throw new Error('no storage'); } });
      })();
    """
    context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    context.add_init_script(shell_script)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{base_url}/websites")
    page.wait_for_timeout(800)
    page.click("#btn-view")
    page.click("#sorts button[data-sort=name]")
    page.wait_for_timeout(500)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(300)
    assert '"sort":"name"' in page.evaluate("window.name")
    page.reload()
    page.wait_for_timeout(1000)
    assert page.get_attribute("#sorts button[data-sort=name]", "aria-checked") == "true"
    assert not errors
    context.close()


def test_desktop_mouse_and_keyboard(browser, base_url):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    page.goto(f"{base_url}/websites")
    page.wait_for_selector("#grid .cell")
    page.wait_for_timeout(500)

    def names():
        return page.evaluate("Array.from(document.querySelectorAll('#grid .cell:not([hidden])')).map(c => c.dataset.name)")

    def center(name):
        return page.evaluate(
            "n => { const r = document.querySelector('.cell[data-name=\"' + n + '\"]').getBoundingClientRect();"
            " return [r.left + r.width / 2, r.top + r.height / 2]; }",
            name,
        )

    order = names()
    x, y = center(order[4])
    page.mouse.move(x, y)
    page.wait_for_timeout(150)
    assert page.inner_text("#dock-name") == "Files Agent"
    xa, ya = center(order[0])
    page.mouse.down()
    page.mouse.move(x + 10, y + 6, steps=3)
    assert page.evaluate("document.documentElement.classList.contains('is-dragging')")
    page.mouse.move(xa, ya, steps=12)
    page.wait_for_timeout(150)
    page.mouse.up()
    page.wait_for_timeout(450)
    assert names()[0] == order[4]
    assert page.url.endswith("/websites")

    page.focus(f".cell[data-name='{names()[2]}'] a")
    moved = names()[2]
    page.keyboard.press("Shift+ArrowLeft")
    page.wait_for_timeout(350)
    assert names()[1] == moved
    context.close()
