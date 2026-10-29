# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Background refreshes must not move the reader, and Interact video must stay light.

The admin page rebuilds itself when polled data changes. That used to drop
keyboard focus to the page body, send inner scroll boxes back to their top, and
interrupt text selection. Interact polls several times a second, so it showed
the problem most. Interact also shows live camera video now, which must stay off
until asked for, download a picture only when a device sent a new one, and stop
when the person leaves the screen.

The real bundle runs in headless Chromium against stubbed APIs; no live server,
config, or credential store is touched.
"""
from __future__ import annotations

import asyncio
import io
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.support.paths import REPO_ROOT, ensure_repo_on_path

ensure_repo_on_path()

sync_api = pytest.importorskip("playwright.sync_api")

ASSETS = REPO_ROOT / "assets"
EXPOSE_MARKER = "    boot();\n}());"
SHELL = (
    '<!DOCTYPE html><html lang="en" data-theme="light"><head><meta charset="utf-8">'
    '<link rel="stylesheet" href="/assets/admin-ui.css"></head><body>'
    '<div id="autoyou-admin-root"><p>Loading</p></div>'
    '<script src="/assets/admin-ui.js"></script></body></html>'
)


def _bootstrap_json(monkeypatch) -> str:
    import server

    monkeypatch.setattr(server.STATE, "config", server._default_config())
    payload = asyncio.run(server._build_admin_ui_bootstrap_payload())
    payload.setdefault("config", {}).setdefault("onboarding", {})["wizard_completed"] = True
    return json.dumps(payload, default=str)


def _jpeg() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 6), (40, 120, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


class _Stub:
    def __init__(self, bootstrap: str):
        self.bootstrap = bootstrap
        self.script = (ASSETS / "admin-ui.js").read_text(encoding="utf-8")
        assert EXPOSE_MARKER in self.script, "The bundle no longer ends with boot(); update this test's hook"
        self.script = self.script.replace(
            EXPOSE_MARKER,
            "    window.__admin = {state, renderApp, agentCategoryOf, AGENT_CATEGORIES};\n" + EXPOSE_MARKER,
        )
        self.picture = _jpeg()
        self.frame_requests: list[dict] = []
        self.feeds = [{"id": "synthetic-cam", "name": "Synthetic Phone", "sequence": 7, "width": 8, "height": 6, "timestamp_ms": 1}]

    def handle(self, route):
        url = urlsplit(route.request.url)
        path = url.path
        if path == "/":
            return route.fulfill(status=200, content_type="text/html", body=SHELL)
        if path == "/assets/admin-ui.js":
            return route.fulfill(status=200, content_type="application/javascript", body=self.script)
        if path == "/assets/admin-ui.css":
            return route.fulfill(status=200, content_type="text/css",
                                 body=(ASSETS / "admin-ui.css").read_text(encoding="utf-8"))
        if path.startswith("/assets/"):
            return route.fulfill(status=200, content_type="image/png", body=b"")
        if path == "/api/admin/bootstrap":
            return route.fulfill(status=200, content_type="application/json", body=self.bootstrap)
        if path == "/api/screen-listen":
            body = {"mode": "off", "participants": [], "inputs": [], "selected": [], "video_feeds": self.feeds}
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        if path == "/api/screen-listen/frame.jpg":
            query = parse_qs(url.query)
            self.frame_requests.append({key: values[0] for key, values in query.items()})
            sequence = self.feeds[0]["sequence"]
            headers = {"X-AutoYou-Frame-Sequence": str(sequence), "Cache-Control": "no-store"}
            if int(query.get("after", ["0"])[0]) >= sequence:
                return route.fulfill(status=204, headers=headers, body=b"")
            return route.fulfill(status=200, content_type="image/jpeg", headers=headers, body=self.picture)
        return route.fulfill(status=200, content_type="application/json", body="{}")


def _launch(playwright):
    try:
        return playwright.chromium.launch()
    except Exception as exc:  # browser binaries are an optional dev dependency
        pytest.skip(f"Chromium is not installed for Playwright: {exc}")


def _open(browser, stub, failures, height=700):
    page = browser.new_page(viewport={"width": 1280, "height": height})
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.route("http://admin.test/**", stub.handle)
    page.goto("http://admin.test/")
    page.wait_for_selector('[data-action="nav:agents"]')
    return page


def _change_markup_and_poll(page, name):
    """A background poll whose data really changed, so the page is rebuilt."""
    page.evaluate(
        "name => { window.__marker = document.querySelector('.ayu-shell');"
        " window.__admin.state.bootstrap.admin.server_name = name;"
        " window.__admin.renderApp({passive: true}); }",
        name,
    )


def test_background_refresh_keeps_scroll_focus_and_selection(monkeypatch):
    stub = _Stub(_bootstrap_json(monkeypatch))
    failures: list[str] = []
    with sync_api.sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _open(browser, stub, failures, height=520)
            page.locator('[data-action="nav:agents"]').first.click()
            page.evaluate("window.scrollTo(0, 900)")
            before = page.evaluate("window.scrollY")
            assert before > 400, "the Agents page should be long enough to scroll"
            nav_scroll = page.evaluate("(() => { const nav = document.querySelector('.ayu-nav'); nav.scrollTop = 120; return nav.scrollTop; })()")
            target = page.evaluate(
                "(() => { const button = [...document.querySelectorAll('.ayu-panel [data-action^=\"select-agent:\"]')]"
                ".find(el => { const box = el.getBoundingClientRect(); return box.top > 0 && box.bottom < innerHeight; });"
                " button.focus({preventScroll: true}); return button.dataset.action; })()"
            )
            _change_markup_and_poll(page, "Synthetic Renamed")
            assert page.evaluate("window.__marker !== document.querySelector('.ayu-shell')"), "the poll did not rebuild the page"
            assert abs(page.evaluate("window.scrollY") - before) <= 1, "a background refresh moved the page"
            assert page.evaluate("document.querySelector('.ayu-nav').scrollTop") == nav_scroll, "the menu scrolled back to its top"
            assert page.evaluate("document.activeElement && document.activeElement.dataset.action") == target, (
                "keyboard focus left the button the person was on")

            # Selected text holds the refresh until the selection is cleared.
            page.evaluate(
                "(() => { const text = document.querySelector('.ayu-panel p');"
                " const range = document.createRange(); range.selectNodeContents(text);"
                " getSelection().removeAllRanges(); getSelection().addRange(range); })()"
            )
            _change_markup_and_poll(page, "Synthetic Again")
            assert page.evaluate("window.__marker === document.querySelector('.ayu-shell')"), "the refresh cleared selected text"
            page.evaluate("getSelection().removeAllRanges()")
            page.wait_for_function("window.__marker !== document.querySelector('.ayu-shell')")
            assert "Synthetic Again" in page.inner_text(".ayu-contextbar")
        finally:
            browser.close()
    assert not failures, failures


def test_interact_live_video_is_opt_in_light_and_survives_refreshes(monkeypatch):
    stub = _Stub(_bootstrap_json(monkeypatch))
    failures: list[str] = []
    with sync_api.sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _open(browser, stub, failures)
            page.evaluate("localStorage.removeItem('autoyou.interact.video')")
            page.locator('[data-action="nav:interact"]').first.click()
            page.wait_for_selector('[data-action="interact-video-toggle"]')
            page.wait_for_timeout(1200)
            assert stub.frame_requests == [], "video downloaded before it was turned on"
            assert "1 device is sending camera video" in page.inner_text('[data-panel-title="Live video"]')

            page.locator('[data-action="interact-video-toggle"]').click()
            page.wait_for_selector(".ayu-interact-video-tile", state="attached")
            page.wait_for_timeout(1200)
            assert stub.frame_requests == [], "a picture scrolled out of view was downloaded"
            page.locator(".ayu-interact-video-tile").scroll_into_view_if_needed()
            page.wait_for_selector(".ayu-interact-video-tile.has-frame img[src^='blob:']")
            assert "Synthetic Phone" in page.inner_text(".ayu-interact-video-tile figcaption")
            page.evaluate("window.__grid = document.querySelector('.ayu-interact-video-grid'); window.__src = document.querySelector('.ayu-interact-video-tile img').src")

            # An unchanged camera is asked with its last frame number and sends nothing.
            page.wait_for_timeout(2600)
            assert stub.frame_requests[0] == {"id": "synthetic-cam", "after": "0"}
            assert all(request["after"] == "7" for request in stub.frame_requests[1:])
            assert 2 <= len(stub.frame_requests) <= 5, f"low rate asks about once a second: {stub.frame_requests}"
            assert page.evaluate("document.querySelector('.ayu-interact-video-tile img').src === window.__src")

            # Other parts of the page refreshing keep the same video grid and picture.
            page.evaluate("window.__admin.state.interact.error = 'synthetic'; window.__admin.renderApp({passive: true})")
            assert page.evaluate("window.__grid === document.querySelector('.ayu-interact-video-grid')"), "a refresh reloaded the video"

            # A new frame is fetched; enlarging a picture speeds only that one up.
            stub.feeds[0]["sequence"] = 8
            page.locator(".ayu-interact-video-tile").click()
            page.wait_for_function("document.querySelector('.ayu-interact-video-tile img').src !== window.__src")
            assert page.evaluate("document.querySelector('.ayu-interact-video-tile').classList.contains('focused')")

            # Leaving Interact stops the video and releases the pictures.
            page.locator('[data-action="nav:overview"]').first.click()
            page.wait_for_timeout(300)
            count = len(stub.frame_requests)
            page.wait_for_timeout(1500)
            assert len(stub.frame_requests) == count, "video kept downloading after leaving Interact"
            assert page.evaluate("document.querySelectorAll('.ayu-interact-video-tile').length") == 0

            # The choice to show video is remembered for the next visit.
            assert json.loads(page.evaluate("localStorage.getItem('autoyou.interact.video')"))["on"] is True
        finally:
            browser.close()
    assert not failures, failures


def test_every_built_in_agent_is_listed_in_one_category_and_boot_lists_startup_items(monkeypatch):
    from autoyou_agents.shared_tools.agent_install_registry import DEFAULT_AGENT_INSTALL_STATES

    stub = _Stub(_bootstrap_json(monkeypatch))
    failures: list[str] = []
    with sync_api.sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _open(browser, stub, failures)
            listed = page.evaluate("window.__admin.AGENT_CATEGORIES.flatMap(category => category[2])")
            assert len(listed) == len(set(listed)), "an agent is listed in two categories"
            assert sorted(set(DEFAULT_AGENT_INSTALL_STATES) - set(listed)) == []
            assert page.evaluate("window.__admin.agentCategoryOf({agent_name: 'assistant_agent'})") == "assistant"
            assert page.evaluate("window.__admin.agentCategoryOf({agent_name: 'game_agent'})") == "media"
            assert page.evaluate("window.__admin.agentCategoryOf({agent_name: 'client_browser_control_agent'})") == "assistant"
            # A new agent from outside the list is grouped by what it does.
            assert page.evaluate("window.__admin.agentCategoryOf({agent_name: 'my_flashcards_agent', description: 'Study lessons with flashcards'})") == "learning"

            page.locator('[data-action="nav:agents"]').first.click()
            chips = page.eval_on_selector_all(".ayu-filter-chip", "els => els.map(el => el.dataset.action)")
            assert "agent-category:connections" not in chips
            assert "agent-category:assistant" in chips

            page.locator('[data-action="nav:setup"]').first.click()
            page.evaluate("window.__admin.state.setup.mode = 'boot'; window.__admin.renderApp()")
            page.wait_for_selector('[data-panel-title="Also starts with AutoYou"]')
            assert page.locator('[data-bind="videoCall.remote_desktop.keep_awake"]').count() == 1
        finally:
            browser.close()
    assert not failures, failures
