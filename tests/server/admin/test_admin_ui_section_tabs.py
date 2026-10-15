# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""A section tab must switch the moment it is clicked.

The admin pages with a horizontal tab strip (Websites & Browser, Messaging,
Video & Calls, Permissions, Speech, Connectivity, Security, ...) build that strip
and hide the other tabs' panels after the page markup is written, so the selected
tab never appears in the markup. A render cache that skipped any render whose
markup was unchanged therefore dropped every tab click: the highlight and the
panels stayed put until the person left the page and came back.

The real bundle runs in headless Chromium against stubbed APIs; no live server,
config, or credential store is touched.
"""
from __future__ import annotations

import asyncio
import json

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
# Every page the owner reported, so a regression in one of them cannot hide
# behind pages that happen to keep working.
REPORTED_SCREENS = {"page", "messaging", "video", "permissions", "speech", "connectivity", "security"}

PANEL_TITLES = """() => [...document.querySelectorAll('.ayu-screen .ayu-panel[data-panel-title]')]
    .filter(panel => !panel.parentElement.closest('.ayu-panel, .ayu-disclosure'))
    .filter(panel => !panel.closest('[hidden]'))
    .map(panel => panel.dataset.panelTitle)"""
ACTIVE_ACTION = "() => document.activeElement && document.activeElement.getAttribute('data-action')"
TAB_STATE = """() => [...document.querySelectorAll('.ayu-section-nav .ayu-tab')].map(tab => ({
    action: tab.dataset.action, active: tab.classList.contains('active'),
    current: tab.getAttribute('aria-current')}))"""


def _bootstrap_json(monkeypatch) -> str:
    import server

    monkeypatch.setattr(server.STATE, "config", server._default_config())
    payload = asyncio.run(server._build_admin_ui_bootstrap_payload())
    payload.setdefault("config", {}).setdefault("onboarding", {})["wizard_completed"] = True
    return json.dumps(payload, default=str)


def _route_handler(bootstrap: str, script: str):
    def handle(route):
        path = route.request.url.split("admin.test", 1)[1].split("?", 1)[0]
        if path == "/":
            return route.fulfill(status=200, content_type="text/html", body=SHELL)
        if path == "/assets/admin-ui.js":
            return route.fulfill(status=200, content_type="application/javascript", body=script)
        if path == "/assets/admin-ui.css":
            return route.fulfill(status=200, content_type="text/css",
                                 body=(ASSETS / "admin-ui.css").read_text(encoding="utf-8"))
        if path.startswith("/assets/"):
            return route.fulfill(status=200, content_type="image/png", body=b"")
        if path == "/api/admin/bootstrap":
            return route.fulfill(status=200, content_type="application/json", body=bootstrap)
        return route.fulfill(status=200, content_type="application/json", body="{}")

    return handle


def _open_admin(browser, bootstrap, script, failures):
    page = browser.new_page(viewport={"width": 1280, "height": 1600})
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.route("http://admin.test/**", _route_handler(bootstrap, script))
    page.goto("http://admin.test/")
    page.wait_for_selector('[data-action="nav:page"]')
    return page


def _exposed_script() -> str:
    script = (ASSETS / "admin-ui.js").read_text(encoding="utf-8")
    assert EXPOSE_MARKER in script, "The bundle no longer ends with boot(); update this test's hook"
    return script.replace(EXPOSE_MARKER, "    window.__admin = {state, renderApp};\n" + EXPOSE_MARKER)


def test_every_section_tab_switches_on_click_and_survives_polling(monkeypatch):
    bootstrap = _bootstrap_json(monkeypatch)
    script = _exposed_script()
    failures: list[str] = []
    exercised: dict[str, list[str]] = {}

    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:  # browser binaries are an optional dev dependency
            pytest.skip(f"Chromium is not installed for Playwright: {exc}")
        try:
            page = _open_admin(browser, bootstrap, script, failures)
            screens = page.eval_on_selector_all(
                '.ayu-sidebar [data-action^="nav:"], [data-action^="nav:"]',
                "els => [...new Set(els.map(el => el.dataset.action.slice(4)))]",
            )
            for screen in screens:
                page.evaluate(f"window.__admin.state.navOpen = false")
                page.locator(f'[data-action="nav:{screen}"]').first.click()
                ids = [
                    tab["action"].split(":", 1)[1]
                    for tab in page.evaluate(TAB_STATE)
                ]
                if len(ids) < 2:
                    continue
                exercised[screen] = ids
                panels_by_tab: dict[str, list[str]] = {}
                # Walk every tab and come back to the first: a later tab must not
                # leave the first one stuck, and the first must not be skipped.
                for tab_id in ids + [ids[0]]:
                    page.locator(f'[data-action="settings-section:{tab_id}"]').click()
                    label = f"{screen}/{tab_id}"
                    tabs = page.evaluate(TAB_STATE)
                    assert [t["action"] for t in tabs if t["active"]] == [f"settings-section:{tab_id}"], (
                        f"{label}: the clicked tab did not become the only active tab")
                    assert [t["action"] for t in tabs if t["current"] == "page"] == [f"settings-section:{tab_id}"], (
                        f"{label}: aria-current did not follow the clicked tab")
                    assert page.evaluate(ACTIVE_ACTION) == f"settings-section:{tab_id}", (
                        f"{label}: keyboard focus did not stay on the clicked tab")
                    assert page.evaluate("window.__admin.state.screen") == screen, f"{label}: left the page"
                    assert page.evaluate(f"window.__admin.state.sections['{screen}']") == tab_id
                    titles = page.evaluate(PANEL_TITLES)
                    assert titles, f"{label}: no panel is visible for this tab"
                    if tab_id in panels_by_tab:
                        assert titles == panels_by_tab[tab_id], f"{label}: revisiting a tab changed its panels"
                    panels_by_tab[tab_id] = titles

                    # A background poll must neither undo the selection nor remount the strip.
                    page.evaluate("window.__strip = document.querySelector('.ayu-section-nav')")
                    page.evaluate("window.__admin.renderApp({passive: true})")
                    assert page.evaluate("window.__strip === document.querySelector('.ayu-section-nav')"), (
                        f"{label}: an unchanged poll render remounted the tab strip")
                    assert [t["action"] for t in page.evaluate(TAB_STATE) if t["active"]] == [
                        f"settings-section:{tab_id}"], f"{label}: a poll render moved the selected tab"
                    assert page.evaluate(PANEL_TITLES) == titles, f"{label}: a poll render changed the panels"

                seen = [title for tab_id in ids for title in panels_by_tab[tab_id]]
                assert len(seen) == len(set(seen)), f"{screen}: one panel is shown under two tabs: {panels_by_tab}"
        finally:
            browser.close()

    assert REPORTED_SCREENS <= set(exercised), (
        f"The reported pages were not all exercised: missing {sorted(REPORTED_SCREENS - set(exercised))}")
    assert not failures, failures


def test_user_initiated_render_is_never_skipped_for_unchanged_markup(monkeypatch):
    """Only background renders may be coalesced; a click-driven render always applies."""
    bootstrap = _bootstrap_json(monkeypatch)
    script = _exposed_script()
    failures: list[str] = []

    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Chromium is not installed for Playwright: {exc}")
        try:
            page = _open_admin(browser, bootstrap, script, failures)
            page.locator('[data-action="nav:page"]').first.click()
            first_tab = page.evaluate(TAB_STATE)[0]["action"].split(":", 1)[1]
            second_tab = page.evaluate(TAB_STATE)[1]["action"].split(":", 1)[1]
            page.evaluate("window.__strip = document.querySelector('.ayu-section-nav')")
            # Same markup as the cached render, different post-render selection.
            page.evaluate(f"window.__admin.state.sections.page = '{second_tab}'; window.__admin.renderApp()")
            assert page.evaluate(TAB_STATE)[1]["active"] is True
            assert page.evaluate("window.__strip !== document.querySelector('.ayu-section-nav')"), (
                "A non-passive render must rebuild the page even when its markup is unchanged")
            page.evaluate(f"window.__admin.state.sections.page = '{first_tab}'; window.__admin.renderApp()")
            assert page.evaluate(TAB_STATE)[0]["active"] is True
        finally:
            browser.close()

    assert not failures, failures
