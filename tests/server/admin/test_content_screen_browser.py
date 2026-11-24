"""My Content in a real browser: the screen, its API, light and dark, phone width, keyboard.

The browser is the machine's own Chrome or Edge, headless, with a throwaway profile
and no network beyond 127.0.0.1. The server is the real content API over a sandboxed
AUTOYOU_TEST_ROOT holding synthetic content. Tests skip where no browser exists.
"""
import asyncio
import io
import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from shared import secure_storage
from shared.content_library import ContentLibrary, output_dir, save_output
from tests.support.headless_browser import Browser, LocalServer, find_browser

ASSETS = Path(__file__).resolve().parents[3] / "assets"
PREFIX = "/api/admin/content"

pytestmark = pytest.mark.skipif(find_browser() is None, reason="needs Chrome or Edge")

HOST = """<!doctype html><html lang="en" data-theme="__THEME__"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Host</title>
<link rel="stylesheet" href="/assets/admin-ui.css"><link rel="stylesheet" href="__PREFIX__/assets/content-screen.css"></head>
<body><div class="ayu-shell"><div class="ayu-main-wrap"><main class="ayu-main"><div class="ayu-screen"><div id="host"></div></div></main></div></div>
<script src="__PREFIX__/assets/content-store.js"></script><script src="__PREFIX__/assets/content-screen.js"></script>
<script>
  var role = new URLSearchParams(location.search).get('role');
  document.cookie = 'autoyou_test_session=synthetic; path=/';
  window.__events = [];
  ['mousedown', 'mouseup', 'click'].forEach(function (t) { document.addEventListener(t, function (e) { window.__events.push(t + ':' + (e.target.className && e.target.className.baseVal === undefined ? e.target.className : e.target.tagName) + '@' + Math.round(e.clientX) + ',' + Math.round(e.clientY)); }, true); });
  window.notices = [];
  window.navigated = '';
  window.ctx = {
    assetBase: '__PREFIX__/assets/', url: function (p) { return '__PREFIX__' + p; },
    request: function (p, o) {
      o = Object.assign({credentials: 'same-origin'}, o || {});
      if (role) { o.headers = Object.assign({'X-AutoYou-Remote-Browser': 'webrtc', 'X-AutoYou-Remote-Access-Role': role, 'X-AutoYou-WebRTC-Session-Id': 'synthetic'}, o.headers || {}); }
      return fetch('__PREFIX__' + p, o);
    },
    notice: function (k, m) { window.notices.push([k, m]); },
    navigate: function (s) { window.navigated = s; }
  };
  AutoYouContent.mount(document.getElementById('host'), window.ctx);
</script></body></html>"""


class Knobs:
    def __init__(self):
        self.delay = 0.0
        self.fail = False
        self.skew = False
        self.log = []

    def calls(self, name, method="GET"):
        return [entry for entry in self.log if entry[1].split("?")[0] == name and entry[0] == method]


def build_app(knobs):
    from shared.content_library_api import register_content_routes
    app = FastAPI()

    @app.middleware("http")
    async def watch(request, call_next):
        path = request.url.path
        screen_data = path.startswith(PREFIX) and "/assets/" not in path
        if screen_data and knobs.delay:
            await asyncio.sleep(knobs.delay)
        if screen_data and knobs.fail:
            return JSONResponse({"error": "The computer is offline."}, status_code=503)
        if screen_data and knobs.skew and path.endswith("/summary"):
            return JSONResponse({"success": True})  # a server from before this screen existed
        response = await call_next(request)
        if path.startswith(PREFIX):
            knobs.log.append((request.method, path[len(PREFIX):] + ("?" + request.url.query if request.url.query else ""), response.status_code))
        return response

    register_content_routes(app, lambda request: None)

    @app.get("/host")
    async def host(theme: str = "light"):
        return HTMLResponse(HOST.replace("__THEME__", theme).replace("__PREFIX__", PREFIX), headers={"Cache-Control": "no-store"})

    @app.get("/favicon.ico")
    async def favicon():
        return Response(status_code=204)

    @app.get("/assets/admin-ui.css")
    async def admin_css():
        return FileResponse(ASSETS / "admin-ui.css", media_type="text/css")
    return app


def png(width=96, height=64):
    from PIL import Image
    data = io.BytesIO()
    Image.new("RGB", (width, height), (70, 90, 150)).save(data, "PNG")
    return data.getvalue()


def seed():
    """A library like a real one: several built-in agents, two agents made on a phone, one agent with no safe remote removal."""
    from autoyou_agents.notes_agent.notes_tool import NotesTool
    from shared.content_native import import_record
    notes = NotesTool()
    for title, body in (("Studio microphone checklist", "Gain staging, pop filter, room tone."),
                        ("Weekly review: launch tasks", "Ship the pairing guide."), ("Ideas for the garden shed", "Shelving and a potting bench.")):
        notes.create_note(title=title, content=body, category="Studio" if "Studio" in title else "")
    library = ContentLibrary()
    library.publish("page_agent", "1", "How local-first sync works", "page", href="/agent/page_agent/", source_collection="References", source_text="Saved article body")
    library.publish("page_agent", "2", "Recipe: lemon olive-oil cake", "page", href="/agent/page_agent/")
    save_output("media_generation_agent", "aurora.png", [png()], title="Aurora over a quiet harbor", kind="image")
    save_output("media_generation_agent", "teaser.mp4", [b"synthetic video bytes" * 50], title="Product teaser", kind="video")
    save_output("files_agent", "budget.csv", [b"item,cost\nsynthetic,1\n"], title="Budget 2027", kind="file")
    save_output("files_agent", "lease.pdf", [b"%PDF-1.4 synthetic"], title="Lease agreement (draft)", kind="file")
    library.publish("location_agent", "2026-11-20", "Locations on 2026-11-20", "location", href="/agent/location_agent/")
    for index, (source, title, identity) in enumerate((
            ("custom_garden_planner_a1b2c3", "Tomato bed layout", dict(agent_name="Garden Planner", agent_handle="garden", agent_icon="leaf", agent_accent="#2E9E5B")),
            ("custom_garden_planner_a1b2c3", "Watering schedule", {}),
            ("custom_reading_list_d4e5f6", "Essay: the slow web", dict(agent_name="Reading List", agent_handle="reading", agent_icon="book", agent_accent="#C2410C")))):
        import_record({"id": f"00000000-0000-4000-8000-0000000000{index + 1:02d}", "kind": source, "base_kind": "notes", "title": title,
                       "body": "Synthetic body for " + title, "updated_at": 100 + index, "attachments": [], **identity})


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("websockets.sync.client")
    instance = Browser(find_browser())
    yield instance
    instance.close()


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(secure_storage, "keyring_available", lambda: False)
    secure_storage.disable_secure_storage()
    yield tmp_path
    secure_storage.disable_secure_storage()


class Screen:
    def __init__(self, browser, populated=True):
        if populated:
            seed()
        self.knobs = Knobs()
        self.server = LocalServer(build_app(self.knobs))
        self.page = browser.new_page()

    def open(self, theme="light", role="", width=1400, height=900, mobile=False, wait=True):
        self.page.viewport(width, height, mobile=mobile)
        query = f"?theme={theme}" + (f"&role={role}" if role else "")
        self.page.goto(self.server.url + "/host" + query)
        if wait:
            self.page.wait("!!document.querySelector('.ayc-shelf, .ayc-state, .ayc-list')", 15, "the screen to draw")
        return self

    def js(self, expression):
        return self.page.eval(expression)

    def titles(self):
        return self.js("Array.from(document.querySelectorAll('.ayc-row .ayc-rowtitle, .ayc-card .ayc-card-title')).map(e => e.textContent)")

    def close(self):
        self.page.close()
        self.server.stop()


@pytest.fixture
def screen(browser):
    made = []

    def make(**options):
        made.append(Screen(browser, **options))
        return made[-1]
    yield make
    for item in made:
        item.close()


def settle(shot):
    shot.page.wait("AutoYouContent._state().store.walkComplete === true && AutoYouContent._state().load === 'ready' && !AutoYouContent._state().scheduled", 15, "the index to be complete")


def to_list(shot):
    shot.page.click('button[data-act="view"][data-id="list"]')
    shot.page.wait("!!document.querySelector('.ayc-list')", 10, "the list view")


# ---------------------------------------------------------------------------------------------------------
def test_overview_groups_every_agent_by_name_with_the_identity_phones_sent(screen):
    shot = screen().open()
    shot.page.wait("document.querySelectorAll('.ayc-shelf').length >= 7", 15, "a shelf per agent")
    names = shot.js("Array.from(document.querySelectorAll('.ayc-shelf-title h3')).map(e => e.textContent)")
    assert {"Notes", "Pages", "Media Generation", "Files", "Locations", "Garden Planner", "Reading List"} <= set(names)
    accent = shot.js("(() => { const h = Array.from(document.querySelectorAll('.ayc-shelf')).find(s => s.querySelector('h3').textContent === 'Garden Planner'); return getComputedStyle(h.querySelector('.ayc-avatar')).getPropertyValue('--ayc-accent').trim(); })()")
    assert accent == "#2E9E5B"
    assert "items from" in shot.js("document.querySelector('.ayc-sub').textContent")
    assert shot.js("document.querySelector('.ayc-hero h1').textContent") == "My Content"
    assert not shot.page.errors()


def test_titles_and_labels_from_agents_are_shown_as_text_never_as_markup(screen):
    shot = screen(populated=False)
    ContentLibrary().publish("notes_agent", "9", '<img src=x onerror="window.pwned=1">Hostile title', "note", source_labels=["<b>bold</b>"])
    shot.open()
    shot.page.wait("document.querySelectorAll('.ayc-card').length === 1", 10, "the card")
    assert shot.js("document.querySelector('.ayc-card-title').textContent").startswith("<img src=x")
    assert shot.js("document.querySelectorAll('.ayc-card img, .ayc-card b').length") == 0
    assert shot.js("window.pwned === undefined")


def test_a_hostile_link_in_the_catalog_is_never_followed(screen):
    shot = screen(populated=False)
    row = ContentLibrary().publish("notes_agent", "77", "Linked note", "note", href="/agent/notes_agent/")
    with ContentLibrary().connect() as db:  # a row an old or compromised server could hold
        db.execute("UPDATE content SET href=? WHERE id=?", ("javascript:window.pwned=1", row["id"]))
    shot.open()
    shot.page.wait("document.querySelectorAll('.ayc-card').length === 1", 10, "the card")
    shot.page.click(".ayc-card")
    shot.page.wait("!!document.querySelector('.ayc-facts')", 10, "the details")
    assert shot.js("document.querySelectorAll('a[href^=\"javascript\"], [src^=\"javascript\"]').length") == 0
    assert shot.js("document.querySelector('.ayc-preview-actions a.ayu-btn-primary[href]') === null"), "no Open link without a safe address"


def test_search_answers_from_the_cache_and_clearing_it_returns_to_the_overview(screen):
    shot = screen().open()
    settle(shot)
    before = len(shot.knobs.log)
    shot.page.press("/")
    shot.page.wait("document.activeElement.classList.contains('ayc-search-input')", 10, "the slash key to jump to search")
    shot.page.type("garden")
    shot.page.wait("!!document.querySelector('.ayc-list')", 10, "a result list")
    assert set(shot.titles()) == {"Ideas for the garden shed", "Tomato bed layout", "Watering schedule"}
    # Three or more characters also ask the server, because note bodies are not in the cached index.
    time.sleep(0.8)
    time_boxed = [e for e in shot.knobs.log[before:] if not e[1].startswith("/summary") and "/thumbnail" not in e[1]]
    assert all(e[1].startswith("/index?q=garden") for e in time_boxed), time_boxed
    shot.page.press("Escape")
    shot.page.wait("!!document.querySelector('.ayc-shelf') && !document.querySelector('.ayc-list')", 10, "the overview again")
    assert shot.js("document.querySelector('.ayc-search-input').value") == ""


def test_type_chips_agent_select_and_collection_select_narrow_the_list(screen):
    shot = screen().open()
    settle(shot)
    shot.page.click('.ayc-chip[data-group="kind"][data-id="note"]')
    shot.page.wait("!!document.querySelector('.ayc-list')", 10, "the list")
    assert all(title in {"Studio microphone checklist", "Weekly review: launch tasks", "Ideas for the garden shed", "Tomato bed layout", "Watering schedule", "Essay: the slow web"}
               for title in shot.titles())
    assert shot.js("document.querySelector('.ayc-chip[data-id=\"note\"]').getAttribute('aria-pressed')") == "true"
    shot.js("(() => { const s = document.querySelector('select[data-ayc=\"source\"]'); s.value = 'custom_garden_planner_a1b2c3'; s.dispatchEvent(new Event('change', {bubbles: true})); })()")
    shot.page.wait("document.querySelectorAll('.ayc-row').length === 2", 10, "two garden notes")
    shot.page.click('button[data-act="clear-filters"], .ayc-view button[data-id="agents"], .ayc-seg button[data-id="agents"]')
    shot.page.wait("!!document.querySelector('.ayc-shelf')", 10, "the overview")
    shot.js("(() => { const s = document.querySelector('select[data-ayc=\"collection\"]'); s.value = 'Studio'; s.dispatchEvent(new Event('change', {bubbles: true})); })()")
    shot.page.wait("document.querySelectorAll('.ayc-row').length === 1", 10, "one Studio item")


def test_a_filter_with_no_matches_explains_itself_and_offers_a_way_out(screen):
    shot = screen().open()
    settle(shot)
    shot.page.click(".ayc-search-input")
    shot.page.type("zzzzqq")
    shot.page.wait("!!document.querySelector('.ayc-state')", 10, "the no-match state")
    assert "Nothing matches" in shot.js("document.querySelector('.ayc-state h3').textContent")
    shot.page.click('.ayc-state [data-act="clear-filters"]')
    shot.page.wait("!!document.querySelector('.ayc-shelf')", 10, "the overview")


def test_preview_shows_text_closes_with_escape_and_returns_focus(screen):
    shot = screen().open()
    settle(shot)
    shot.page.click(".ayc-card")
    shot.page.wait("document.querySelector('.ayc-preview') && !document.querySelector('.ayc-preview').hidden && document.querySelector('.ayc-text') && document.querySelector('.ayc-card.is-active')", 10, "the preview")
    assert shot.js("document.querySelector('.ayc-preview').getAttribute('role')") == "complementary"
    assert "Agent" in shot.js("document.querySelector('.ayc-facts').textContent")
    shot.js("document.querySelector('.ayc-card.is-active').focus()")
    shot.page.press("Escape")
    shot.page.wait("document.querySelector('.ayc-preview').hidden", 10, "the preview to close")


def test_organizing_one_item_saves_through_the_api_and_arrives_as_a_small_change(screen):
    shot = screen().open()
    settle(shot)
    before = list(shot.knobs.log)
    shot.page.click(".ayc-card")
    shot.page.wait("!!document.querySelector('[data-act=\"toggle-edit\"]')", 10, "the Organize button")
    shot.page.click('[data-act="toggle-edit"]')
    shot.page.wait("!!document.querySelector('#ayc-edit-collection')", 10, "the editor")
    shot.js("(() => { const c = document.querySelector('#ayc-edit-collection'); c.value = 'Launch'; const l = document.querySelector('#ayc-edit-labels'); l.value = 'urgent, review'; })()")
    shot.page.click('.ayc-edit button[type="submit"]')
    shot.page.wait("window.notices.length > 0", 10, "the saved notice")
    assert shot.js("window.notices[0][0]") == "success"
    shot.page.wait("Array.from(document.querySelectorAll('.ayc-facts dd')).some(e => e.textContent === 'Launch')", 10, "the new collection in the details")
    after = shot.knobs.log[len(before):]
    assert not [e for e in after if e[1].startswith("/index") or e[1].startswith("/groups") or e[1].startswith("/changes?after=0")], after
    assert any(e[0] == "PATCH" for e in after)


def test_a_conflicting_edit_is_explained_and_nothing_is_overwritten(screen):
    shot = screen().open()
    settle(shot)
    shot.page.click(".ayc-card")
    shot.page.wait("!!document.querySelector('[data-act=\"toggle-edit\"]')", 10, "the Organize button")
    shot.page.click('[data-act="toggle-edit"]')
    shot.page.wait("!!document.querySelector('#ayc-edit-collection')", 10, "the editor")
    identifier = shot.js("AutoYouContent._state().active")
    library = ContentLibrary()
    row = next(item for item in library.list()["items"] if item["id"] == identifier)
    library.annotate(identifier, labels=["elsewhere"], annotation="Edited on another device", collection="Elsewhere", expected_revision=row["revision"])
    shot.js("document.querySelector('#ayc-edit-collection').value = 'Mine'")
    shot.page.click('.ayc-edit button[type="submit"]')
    shot.page.wait("document.querySelector('.ayc-edit-status').textContent.includes('changed on another device')", 10, "the conflict message")
    assert library.list()["items"][0] is not None
    assert next(item for item in library.list()["items"] if item["id"] == identifier)["collection"] == "Elsewhere"


def test_deleting_a_file_asks_first_defaults_to_cancel_and_removes_the_stored_file(screen):
    shot = screen().open()
    settle(shot)
    to_list(shot)
    stored = list(output_dir("files_agent").glob("*.csv"))
    assert len(stored) == 1
    target = [r for r in ContentLibrary().list(source="files_agent")["items"] if r["title"] == "Budget 2027"][0]
    shot.page.click(f'[data-act="delete-one"][data-id="{target["id"]}"]')
    shot.page.wait("document.querySelector('dialog.ayc-dialog[open]')", 10, "the confirmation")
    text = shot.js("document.querySelector('dialog.ayc-dialog[open]').textContent")
    assert "Budget 2027" in text and "can't be undone" in text and "stored file" in text
    assert shot.js("document.activeElement.getAttribute('data-act')") == "cancel-dialog", "Cancel has focus, so Enter never deletes by accident"
    shot.page.press("Escape")
    shot.page.wait("!document.querySelector('dialog.ayc-dialog[open]')", 10, "the dialog to close")
    assert stored[0].exists() and ContentLibrary().list(source="files_agent")["total"] == 2
    shot.page.click(f'[data-act="delete-one"][data-id="{target["id"]}"]')
    shot.page.wait("document.querySelector('dialog.ayc-dialog[open]')", 10, "the confirmation again")
    shot.page.click('[data-act="confirm-delete"]')
    shot.page.wait("!document.querySelector('dialog.ayc-dialog[open]')", 10, "the dialog to close after deleting")
    shot.page.wait(f"!document.querySelector('[data-row=\"{target['id']}\"]')", 10, "the row to disappear")
    assert not stored[0].exists()
    assert ContentLibrary().list(source="files_agent")["total"] == 1
    assert shot.js("window.notices.some(n => n[0] === 'success' && n[1].includes('Deleted'))")


def test_an_agent_owned_note_is_deleted_by_its_agent_and_a_location_points_to_its_app(screen):
    shot = screen().open()
    settle(shot)
    to_list(shot)
    from autoyou_agents.notes_agent.notes_tool import NotesTool
    note = next(r for r in ContentLibrary().list(source="notes_agent")["items"] if r["title"] == "Weekly review: launch tasks")
    shot.page.click(f'[data-act="delete-one"][data-id="{note["id"]}"]')
    shot.page.wait("document.querySelector('dialog.ayc-dialog[open]')", 10, "the confirmation")
    assert "Notes will delete" in shot.js("document.querySelector('dialog.ayc-dialog[open]').textContent")
    shot.page.click('[data-act="confirm-delete"]')
    shot.page.wait(f"!document.querySelector('[data-row=\"{note['id']}\"]')", 10, "the note to disappear")
    assert NotesTool().delete_note(int(note["source_id"])) is False, "the Notes agent really removed it"
    place = next(r for r in ContentLibrary().list(source="location_agent")["items"])
    shot.page.click(f'[data-row="{place["id"]}"] .ayc-rowmain')
    shot.page.wait("!!document.querySelector('.ayc-preview-actions a.ayu-btn-danger')", 10, "the Delete in Locations link")
    link = shot.js("document.querySelector('.ayc-preview-actions a.ayu-btn-danger')")
    assert shot.js("document.querySelector('.ayc-preview-actions a.ayu-btn-danger').getAttribute('href')") == "/agent/location_agent/"
    assert ContentLibrary().list(source="location_agent")["total"] == 1


def test_selecting_several_items_organizes_or_deletes_them_in_one_step(screen):
    shot = screen().open()
    settle(shot)
    to_list(shot)
    ids = [r["id"] for r in ContentLibrary().list(source="custom_garden_planner_a1b2c3")["items"]]
    for number, identifier in enumerate(ids, start=1):
        shot.page.click(f'input[data-act="pick"][data-id="{identifier}"]')
        shot.page.wait(f"document.querySelector('.ayc-bulk').textContent.includes('{number} item')", 10, f"{number} selected")
    assert "2 items selected" in shot.js("document.querySelector('.ayc-bulk').textContent")
    shot.page.click('[data-act="bulk-collect"]')
    shot.page.wait("!!document.querySelector('#ayc-bulk-value')", 10, "the collection dialog")
    shot.js("document.querySelector('#ayc-bulk-value').value = 'Garden'")
    shot.page.click('dialog.ayc-dialog[open] button[type="submit"]')
    shot.page.wait("!document.querySelector('dialog.ayc-dialog[open]')", 10, "the dialog to close")
    shot.page.wait("window.notices.some(n => n[1].includes('Updated 2 items'))", 10, "the update notice")
    assert {r["collection"] for r in ContentLibrary().list(source="custom_garden_planner_a1b2c3")["items"]} == {"Garden"}
    shot.page.click('[data-act="bulk-delete"]')
    shot.page.wait("document.querySelector('dialog.ayc-dialog[open]')", 10, "the delete confirmation")
    assert "Delete 2 items" in shot.js("document.querySelector('dialog.ayc-dialog[open]').textContent")
    rows_before = len(shot.titles())
    shot.page.click('[data-act="confirm-delete"]')
    shot.page.wait("!document.querySelector('dialog.ayc-dialog[open]')", 10, "the dialog to close")
    shot.page.wait("document.querySelectorAll('.ayc-row').length === " + str(rows_before - 2), 10, "two fewer rows")
    assert ContentLibrary().list(source="custom_garden_planner_a1b2c3")["total"] == 0


@pytest.mark.parametrize("role,can_delete,can_edit,can_add", [("viewer", False, False, False), ("editor", False, True, True), ("admin", True, True, True)])
def test_remote_roles_change_what_the_screen_offers_and_the_server_agrees(screen, role, can_delete, can_edit, can_add):
    shot = screen().open(role=role)
    settle(shot)
    to_list(shot)
    present = lambda selector: shot.js(f"!!document.querySelector({json.dumps(selector)})")
    assert present('[data-act="delete-one"]') is can_delete
    assert present('input[data-act="pick"]') is can_edit
    assert present('[data-act="toggle-add"]') is can_add
    assert present('[data-act="storage"]') is can_delete
    if role != "admin":
        assert "Remote access is set to " + role in shot.js("document.querySelector('.ayc-banners').textContent")


def test_an_idle_screen_costs_one_conditional_request_and_changes_arrive_as_deltas(screen):
    shot = screen().open()
    settle(shot)
    # Poll quickly for the test: unmount/mount reuses the cache and schedules the next poll.
    shot.js("(() => { const s = AutoYouContent._state(); s.poll.floor = 150; s.poll.ceiling = 300; s.poll.wait = 150; AutoYouContent.unmount(); AutoYouContent.mount(document.getElementById('host'), window.ctx); })()")
    base = len(shot.knobs.log)
    time.sleep(1.8)
    idle = shot.knobs.log[base:]
    assert len(idle) >= 3 and all(e[1] == "/summary" and e[2] == 304 for e in idle), idle
    cursor = shot.js("AutoYouContent._state().store.cursor")
    ContentLibrary().publish("notes_agent", "9001", "Arrived while the screen was open", "note", href="/agent/notes_agent/")
    shot.page.wait("AutoYouContent._state().store.all().some(r => r.title === 'Arrived while the screen was open')", 10, "the new item")
    arrived = shot.knobs.log[base + len(idle):]
    paths = [e[1].split("&")[0] for e in arrived if not e[1].startswith("/summary")]
    assert paths == [f"/changes?after={cursor}"], arrived
    assert not [e for e in arrived if e[1].startswith("/groups") or e[1].startswith("/index")]


def test_the_shells_rerenders_do_not_rebuild_the_screen_or_lose_the_users_place(screen):
    shot = screen().open()
    settle(shot)
    shot.page.click(".ayc-search-input")
    shot.page.type("note")
    shot.page.wait("!!document.querySelector('.ayc-list') && AutoYouContent._state().server !== null && !AutoYouContent._state().scheduled", 10, "results with the server's answer")
    time.sleep(0.3)
    shot.js("window.__root = AutoYouContent._state().root; window.__row = document.querySelector('.ayc-row')")
    base = len(shot.knobs.log)
    # What admin-ui.js does on every render: park the screen, replace its host, mount again.
    shot.js("(() => { AutoYouContent.detach(); const host = document.getElementById('host'); host.innerHTML = ''; AutoYouContent.mount(host, window.ctx); })()")
    assert shot.js("document.querySelector('.ayc-root') === window.__root")
    assert shot.js("document.querySelector('.ayc-row') === window.__row"), "rows are not rebuilt"
    assert shot.js("document.querySelector('.ayc-search-input').value") == "note"
    assert shot.js("document.activeElement.classList.contains('ayc-search-input')"), "typing is not interrupted"
    shot.page.wait("true", 1)
    assert [e for e in shot.knobs.log[base:] if not e[1].startswith("/summary")] == []


def test_going_offline_keeps_what_was_loaded_and_recovers_by_itself(screen):
    shot = screen().open()
    settle(shot)
    shot.js("(() => { const s = AutoYouContent._state(); s.poll.floor = 150; s.poll.ceiling = 300; s.poll.wait = 150; AutoYouContent.unmount(); AutoYouContent.mount(document.getElementById('host'), window.ctx); })()")
    count = len(shot.titles())
    shot.knobs.fail = True
    ContentLibrary().publish("notes_agent", "9002", "Added while offline", "note")
    shot.page.wait("document.querySelector('.ayc-banners').textContent.length > 0", 10, "a notice")
    assert len(shot.titles()) == count, "nothing was thrown away"
    shot.knobs.fail = False
    shot.page.wait("AutoYouContent._state().store.all().some(r => r.title === 'Added while offline')", 15, "catching up after the connection returned")
    shot.page.wait("document.querySelector('.ayc-banners').textContent.length === 0", 10, "the notice to clear")


def test_empty_loading_and_error_states(screen, browser):
    shot = screen(populated=False)
    shot.open()
    shot.page.wait("!!document.querySelector('.ayc-state')", 10, "the empty state")
    assert "Nothing here yet" in shot.js("document.querySelector('.ayc-state h3').textContent")
    assert shot.js("Array.from(document.querySelectorAll('.ayc-state button')).map(b => b.textContent.trim())") == ["Add files", "Sync from agents", "Manage agents"]
    shot.page.click('.ayc-state [data-act="go-agents"]')
    assert shot.js("window.navigated") == "agents"
    loading = screen(populated=False)
    loading.knobs.delay = 3
    loading.open(wait=False)
    loading.page.wait("!!document.querySelector('.ayc-skeleton')", 10, "the loading skeleton")
    assert loading.js("document.querySelector('.ayc-results').parentElement.getAttribute('aria-busy')") == "true"
    failing = screen(populated=False)
    failing.knobs.fail = True
    failing.open(wait=False)
    failing.page.wait("!!document.querySelector('.ayc-state[role=alert]')", 10, "the error state")
    assert "could not be loaded" in failing.js("document.querySelector('.ayc-state h3').textContent")
    failing.knobs.fail = False
    failing.page.click('.ayc-state [data-act="retry"]')
    failing.page.wait("!!document.querySelector('.ayc-state h3') && document.querySelector('.ayc-state h3').textContent.includes('Nothing here yet')", 10, "recovery")


def test_a_server_that_predates_the_screen_gets_a_clear_message_not_a_broken_page(screen):
    shot = screen(populated=False)
    shot.knobs.skew = True
    shot.open(wait=False)
    shot.page.wait("!!document.querySelector('.ayc-state[role=alert]')", 15, "the update message")
    assert "older than this screen expects" in shot.js("document.querySelector('.ayc-state').textContent")
    assert not shot.page.errors()


def test_adding_files_uploads_them_in_chunks_and_lists_them(screen, sandbox):
    shot = screen().open()
    settle(shot)
    first = sandbox / "synthetic upload.txt"
    first.write_bytes(b"synthetic upload " * 1000)
    document = shot.page.call("DOM.getDocument", depth=1)
    node = shot.page.call("DOM.querySelector", nodeId=document["root"]["nodeId"], selector="input.ayc-file")["nodeId"]
    shot.page.call("DOM.setFileInputFiles", files=[str(first)], nodeId=node)
    shot.page.wait("document.querySelector('.ayc-uploads') && !document.querySelector('.ayc-uploads').hidden && document.querySelector('.ayc-uploads').textContent.includes('Finished')", 15, "the upload to finish")
    shot.page.wait("AutoYouContent._state().store.all().some(r => r.title === 'synthetic upload.txt')", 15, "the new item in the index")
    assert any(path.read_bytes().startswith(b"synthetic upload") for path in output_dir("files_agent").glob("*.txt"))
    assert not shot.page.errors()


def test_keyboard_users_can_reach_everything_and_always_see_where_they_are(screen):
    shot = screen().open()
    settle(shot)
    shot.page.press("Tab")  # keyboard use is what makes focus rings appear
    shot.js("document.querySelector('.ayc-card').focus()")
    ring = shot.js("(() => { const s = getComputedStyle(document.activeElement); return [s.outlineStyle, parseFloat(s.outlineWidth)]; })()")
    first = shot.js("document.activeElement.textContent")
    shot.page.press("ArrowRight")
    assert shot.js("document.activeElement.textContent") != first, "arrow keys move between items"
    shot.page.press("Enter")
    shot.page.wait("!document.querySelector('.ayc-preview').hidden", 10, "Enter opens the preview")
    shot.page.press("Escape")
    shot.page.wait("document.querySelector('.ayc-preview').hidden", 10, "Escape closes it")
    assert shot.js("document.activeElement.classList.contains('ayc-card')"), "focus comes back to the item"
    assert ring[0] != "none" and ring[1] >= 2
    names = shot.js("Array.from(document.querySelectorAll('.ayc-root button, .ayc-root a[href], .ayc-root input:not([type=file]), .ayc-root select')).filter(e => !e.closest('[hidden]')).filter(e => !(e.getAttribute('aria-label') || e.textContent.trim() || e.getAttribute('placeholder'))).length")
    assert names == 0, "every control has an accessible name"


def contrast_report(shot):
    return shot.js("""(() => {
      const parse = c => { const m = c.match(/rgba?\\(([^)]+)\\)/); const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
      const over = (top, bottom) => { const a = top[3]; return [0, 1, 2].map(i => top[i] * a + bottom[i] * (1 - a)).concat([1]); };
      const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
      const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };
      const probe = document.createElement('i'); document.body.appendChild(probe);
      const token = name => { probe.style.color = getComputedStyle(document.documentElement).getPropertyValue(name); return parse(getComputedStyle(probe).color); };
      const pageBackground = token('--ayu-bg');
      const gradient = el => { for (let e = el; e && e !== document.body; e = e.parentElement) { if (/gradient/.test(getComputedStyle(e).backgroundImage)) return true; } return false; };
      const background = el => { const chain = []; for (let e = el; e && e !== document.body; e = e.parentElement) chain.push(e); let base = pageBackground;
        for (let i = chain.length - 1; i >= 0; i--) { const bg = parse(getComputedStyle(chain[i]).backgroundColor); if (bg[3] > 0) base = over(bg, base); } return base; };
      const bad = []; const seen = new Set();
      document.querySelectorAll('.ayc-root *, dialog.ayc-dialog[open] *, .ayc-preview *').forEach(el => {
        if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) return;
        const style = getComputedStyle(el); if (style.visibility === 'hidden' || style.display === 'none' || el.closest('[hidden]') || el.closest('.ayc-thumb') || gradient(el)) return;
        const size = parseFloat(style.fontSize), bold = parseInt(style.fontWeight) >= 700; const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
        const fg = over(parse(style.color), background(el)); const r = ratio(fg, background(el));
        const key = el.className + '|' + style.color; if (seen.has(key)) return; seen.add(key);
        if (r < need) bad.push([el.className || el.tagName, el.textContent.trim().slice(0, 30), Math.round(r * 100) / 100, need]);
      });
      probe.remove(); return bad;
    })()""")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_is_readable_in_both_themes_in_every_view(screen, theme):
    shot = screen()
    shot.page.media(**{"prefers-reduced-motion": "reduce"})  # a colour caught mid-transition is not a measurement
    shot.open(theme=theme)
    settle(shot)
    assert contrast_report(shot) == []
    shot.page.click(".ayc-card")
    shot.page.wait("!document.querySelector('.ayc-preview').hidden && !!document.querySelector('.ayc-facts')", 10, "the preview")
    assert contrast_report(shot) == []
    to_list(shot)
    assert contrast_report(shot) == []
    first = shot.js("document.querySelector('[data-act=\"delete-one\"]').getAttribute('data-id')")
    shot.page.click(f'[data-act="delete-one"][data-id="{first}"]')
    shot.page.wait("document.querySelector('dialog.ayc-dialog[open]')", 10, "the dialog")
    assert contrast_report(shot) == []


def test_the_two_themes_really_differ_and_follow_the_shell(screen):
    shot = screen().open(theme="light")
    settle(shot)
    light = shot.js("getComputedStyle(document.querySelector('.ayc-card')).backgroundColor")
    shot.js("document.documentElement.setAttribute('data-theme', 'dark')")
    dark = shot.js("getComputedStyle(document.querySelector('.ayc-card')).backgroundColor")
    assert light != dark and light.startswith("rgb(255") and not dark.startswith("rgb(255")


def test_phone_width_has_no_sideways_scroll_and_the_preview_becomes_a_sheet(screen):
    shot = screen().open(width=390, height=844, mobile=True)
    settle(shot)
    assert shot.js("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert shot.js("getComputedStyle(document.querySelector('.ayc-cards')).overflowX") == "auto"
    to_list(shot)
    assert shot.js("document.documentElement.scrollWidth <= window.innerWidth + 1")
    shot.page.click(".ayc-rowmain")
    shot.page.wait("!!document.querySelector('.ayc-preview.is-sheet') && !document.querySelector('.ayc-preview').hidden", 10, "the sheet")
    assert shot.js("getComputedStyle(document.querySelector('.ayc-preview')).position") == "fixed"
    assert shot.js("document.querySelector('.ayc-preview').getAttribute('role')") == "dialog"
    shot.page.wait("document.querySelector('.ayc-preview').contains(document.activeElement)", 5, "focus inside the sheet")
    for _ in range(25):
        shot.page.press("Tab")
    assert shot.js("document.querySelector('.ayc-preview').contains(document.activeElement)"), "focus stays inside the open sheet"
    shot.page.press("Escape")
    shot.page.wait("document.querySelector('.ayc-preview').hidden", 10, "the sheet to close")
    assert shot.js("document.documentElement.scrollWidth <= window.innerWidth + 1")
    big = shot.js("Array.from(document.querySelectorAll('.ayc-iconbtn, .ayc-chip')).filter(e => e.offsetParent).every(e => e.getBoundingClientRect().height >= 36)")
    assert big


def test_reduced_motion_turns_off_the_loading_shimmer_and_transitions(screen):
    shot = screen(populated=False)
    shot.knobs.delay = 4
    shot.page.media(**{"prefers-reduced-motion": "no-preference"})
    shot.open(wait=False)
    shot.page.wait("!!document.querySelector('.ayc-line')", 10, "the skeleton")
    assert shot.js("getComputedStyle(document.querySelector('.ayc-line')).animationName") == "ayc-shimmer"
    shot.page.media(**{"prefers-reduced-motion": "reduce"})
    assert shot.js("getComputedStyle(document.querySelector('.ayc-line')).animationName") == "none"
    assert shot.js("getComputedStyle(document.querySelector('.ayc-hero')).transitionDuration") in ("0s", "")


def test_the_screen_reports_what_it_is_doing_to_assistive_technology(screen):
    shot = screen().open()
    settle(shot)
    assert shot.js("document.querySelector('.ayc-root [aria-live=polite]') !== null")
    to_list(shot)
    shot.page.wait("document.querySelector('.ayc-root [aria-live=polite]').textContent.includes('results')", 10, "the result count to be announced")
    assert shot.js("document.querySelector('.ayc-list').getAttribute('role')") == "list"
    assert shot.js("Array.from(document.querySelectorAll('.ayc-row')).every(r => r.getAttribute('role') === 'listitem')")
    assert shot.js("document.querySelector('.ayc-seg').getAttribute('role')") == "group"
