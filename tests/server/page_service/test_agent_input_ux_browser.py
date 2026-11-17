"""Browser regressions using isolated TestClient routes, never a live server."""
from urllib.parse import urlsplit
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright, expect = playwright_api.sync_playwright, playwright_api.expect

from autoyou_agents.page_agent.website.backend.app import PageFeedService
from autoyou_agents.shared_tools.agent_input_ux import inject_agent_input_ux
import autoyou_agents.files_agent.website.backend.app as files_app
from autoyou_agents.shared_tools import scheduler_mission_control as sites
from .test_page_posts import photo, post_payload


@pytest.fixture(params=["chromium", "webkit"])
def browser(request):
    with sync_playwright() as playwright:
        engine = getattr(playwright, request.param)
        if not Path(engine.executable_path).is_file():
            pytest.skip(f"Install the optional browser test dependency: python -m playwright install {request.param}")
        instance = engine.launch()
        yield instance
        instance.close()


def serve(page, client):
    page.set_default_timeout(8000)
    page.set_default_navigation_timeout(30000)
    def route(request_route):
        req = request_route.request
        path = urlsplit(req.url).path
        query = urlsplit(req.url).query
        response = client.request(req.method, path + ("?" + query if query else ""), content=req.post_data_buffer,
                                  headers={k: v for k, v in req.headers.items() if k not in {"host", "content-length"}})
        headers = {k: v for k, v in response.headers.items() if k not in {"content-length", "content-encoding"}}
        request_route.fulfill(status=response.status_code, headers=headers, body=response.content)
    page.route("http://agent.test/**", route)
    # External embeds are not a deterministic test of our UI. Keep only their URL/layout contract.
    page.route("https://**", lambda route: route.fulfill(body="<html><body>External player fixture</body></html>", content_type="text/html"))


def test_page_inline_youtube_edit_and_gallery(browser, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    service = PageFeedService()
    app = FastAPI(); service.register_routes(app)
    client = TestClient(app)
    youtube = client.post("/api/feed", json={"url": "https://www.youtube.com/watch?v=abc123DEF45", "title": "Synthetic video"}).json()["item"]
    blobs = [photo(client, "one.png"), photo(client, "two.png")]
    gallery = client.post("/api/feed", json=post_payload(blobs)).json()["item"]
    context = browser.new_context(viewport={"width": 390, "height": 720}, has_touch=True, is_mobile=True)
    page = context.new_page(); serve(page, client)
    errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://agent.test/")
    page.locator(f'.feed-item[data-item-id="{youtube["id"]}"]').click()
    expect(page.locator("#item-sheet")).to_have_class("sheet-shell open")
    expect(page.locator("#reel")).to_be_hidden()
    frame = page.locator("#item-content iframe")
    expect(frame).to_have_attribute("src", __import__("re").compile(r"youtube-nocookie.*playsinline=1.*origin="))
    box = frame.bounding_box(); assert 1.6 < box["width"] / box["height"] < 1.9
    page.get_by_role("button", name="Edit post", exact=True).click()
    # Model a keyboard reducing the visual viewport while layout height stays unchanged.
    page.evaluate("""() => {
      Object.defineProperty(window.visualViewport, 'height', {get: () => 340, configurable: true});
      Object.defineProperty(window.visualViewport, 'offsetTop', {get: () => 40, configurable: true});
      window.visualViewport.dispatchEvent(new Event('resize'));
    }""")
    page.locator("#item-link-input").fill("https://example.test/edited")
    page.locator("#item-text-input").fill("A corrected transcript")
    page.locator("#item-text-input").evaluate("el => el.focus()")
    page.wait_for_timeout(450)
    field = page.locator("#item-text-input").bounding_box()
    assert field["y"] >= 40 and field["y"] + field["height"] <= 380
    page.get_by_role("button", name="Save changes", exact=True).click()
    assert client.get(f'/api/feed/item/{youtube["id"]}').json()["item"]["content"] == "A corrected transcript"
    page.evaluate('''() => {
        Object.defineProperty(visualViewport, 'height', {get: () => innerHeight, configurable: true});
        Object.defineProperty(visualViewport, 'offsetTop', {get: () => 0, configurable: true});
        visualViewport.dispatchEvent(new Event('resize'));
    }''')
    page.locator("#item-sheet .sheet-button[data-close-sheet]").click()
    page.locator(f'.feed-item[data-item-id="{gallery["id"]}"]').click()
    expect(page.locator(".gallery-slide")).to_have_count(2)
    page.get_by_role("button", name="Next attachment", exact=True).click()
    expect(page.locator(".gallery-count")).to_have_text("2 of 2")
    second = page.locator(".gallery-slide").nth(1)
    assert second.locator(".photo-open").bounding_box()["height"] >= 180
    second.get_by_role("button", name="View the full photo", exact=True).click()
    expect(page.locator("#photo-image")).to_have_attribute("src", __import__("re").compile(blobs[1]))
    page.get_by_role("button", name="Close photo", exact=True).click()
    expect(second.get_by_role("link", name="Download", exact=True)).to_have_attribute("href", __import__("re").compile(blobs[1]))
    page.screenshot(path=str(tmp_path / "page-gallery.png"))
    assert not errors, errors
    context.close()


@pytest.mark.parametrize("width", [390, 820, 1440])
def test_files_responsive_navigation_settings_and_preview(browser, tmp_path, monkeypatch, width):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv(files_app.WORKSPACE_ROOT_ENV, str(tmp_path))
    (tmp_path / "Documents").mkdir(); (tmp_path / "hello.txt").write_text("Synthetic preview text")
    auth = {"authenticated": True, "required": False}
    monkeypatch.setattr(sites, "_describe_auth_state", lambda *_: auth)
    monkeypatch.setattr(files_app, "_api_auth_error", lambda *_: None)
    monkeypatch.setattr(files_app, "_locations", lambda: [
        {"label": "Workspace", "path": str(tmp_path), "kind": "workspace"},
        {"label": "Local disk", "path": str(tmp_path.parent), "kind": "drive", "total_bytes": 1000000000, "free_bytes": 500000000},
    ])
    context = browser.new_context(viewport={"width": width, "height": 900}, has_touch=width < 1000)
    page = context.new_page(); serve(page, TestClient(files_app.app))
    errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://agent.test/")
    expect(page.locator(".entry-name", has_text="hello.txt")).to_be_visible()
    assert page.locator(".folder-header").bounding_box()["y"] < 280
    assert page.locator(".location-button").first.bounding_box()["height"] < 160
    if width < 760:
        assert page.locator(".locations-panel").bounding_box()["height"] < 100
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(tmp_path / f"files-{width}.png"))
    page.get_by_role("button", name="Appearance", exact=True).click()
    page.locator('input[name="skin"][value="midnight"]').check()
    expect(page.locator("html")).to_have_attribute("data-skin", "midnight")
    page.locator("#density-select").select_option("compact")
    page.screenshot(path=str(tmp_path / f"appearance-{width}.png"))
    page.get_by_role("button", name="Done", exact=True).click()
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-skin", "midnight")
    expect(page.locator("html")).to_have_attribute("data-density", "compact")
    page.locator(".entry-row", has_text="hello.txt").click()
    expect(page.locator(".text-preview")).to_have_text("Synthetic preview text")
    if width <= 1200:
        expect(page.locator("#preview-dialog")).to_be_visible()
        page.get_by_role("button", name="Close preview", exact=True).click()
    page.locator("#file-search").fill("Documents")
    expect(page.locator(".entry-row")).to_have_count(1)
    page.locator(".entry-row").click()
    expect(page.locator("#folder-title")).to_have_text("Documents")
    page.get_by_role("button", name="Back", exact=True).click()
    expect(page.locator("#folder-title")).not_to_have_text("Documents")
    assert not errors, errors
    context.close()


def test_input_injection_is_idempotent_and_handles_old_templates():
    html = inject_agent_input_ux('<!doctype html><html><head></head><body><input></body></html>')
    assert html.count('id="autoyou-input-ux"') == 1
    assert inject_agent_input_ux(html) == html


def test_shared_focus_reveals_inputs_in_custom_fixed_overlays(browser):
    context = browser.new_context(viewport={"width": 390, "height": 720}, has_touch=True, is_mobile=True)
    page = context.new_page()
    page.set_content(inject_agent_input_ux('''<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
        <style>#overlay {position:fixed;inset:0;overflow:hidden;background:white} form{margin-top:400px} input{font-size:12px}</style></head>
        <body><div id="overlay"><form><label for="field">Synthetic field</label><input id="field"></form></div></body></html>'''))
    page.locator("#field").focus()
    page.evaluate('''() => {
        Object.defineProperty(visualViewport, 'height', {get: () => 300, configurable: true});
        Object.defineProperty(visualViewport, 'offsetTop', {get: () => 20, configurable: true});
        visualViewport.dispatchEvent(new Event('resize'));
    }''')
    expect(page.locator("#overlay")).to_have_attribute("data-autoyou-keyboard-overlay", "true")
    field = page.locator("#field").bounding_box()
    assert field["y"] >= 20 and field["y"] + field["height"] <= 320
    assert page.locator("#field").evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 16
    context.close()
