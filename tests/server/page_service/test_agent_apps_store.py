# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Agent Apps store: presentation catalog, payload, first paint and script contract."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import autoyou_page_service
from autoyou_agents.shared_tools.frontend_manifest import (
    _normalize_frontend_manifest_payload,
    build_frontend_manifest,
)
from shared.agent_apps import catalog
from shared.agent_apps.glyphs import APP_GLYPHS, GLYPH_NAMES, UI_GLYPHS, sprite_markup
from shared.agent_apps.page import render_agent_apps_page
from shared.agent_apps.page_js import JS


def _site(name, title="", description="", **extra):
    entry = {
        "agent_name": name,
        "title": title or name,
        "description": description,
        "frontend_port_registered": True,
        "launch_path": f"/agent/{name}/",
        "open_url": f"/agent/{name}/",
    }
    entry.update(extra)
    return entry


def _service(monkeypatch, frontends, **registry):
    monkeypatch.setattr(
        autoyou_page_service,
        "load_frontend_registry",
        lambda: {"frontends": frontends, **registry},
    )
    return autoyou_page_service.AutoYouPageService()


# ---------- catalog ----------


def test_every_known_app_uses_a_real_glyph_and_section():
    for name, (glyph, category, rank, _keywords) in catalog._KNOWN.items():
        assert glyph in GLYPH_NAMES, name
        assert category in catalog.CATEGORY_LABELS, name
        assert rank < catalog.UNRANKED
    assert len({rank for _g, _c, rank, _k in catalog._KNOWN.values()}) == len(catalog._KNOWN)


def test_glyph_sets_are_separate_and_drawn_into_one_sprite():
    assert not set(APP_GLYPHS) & set(UI_GLYPHS)
    sprite = sprite_markup()
    for name in [*APP_GLYPHS, *UI_GLYPHS]:
        assert f'id="g-{name}"' in sprite


def test_manifest_hints_win_and_bad_hints_fall_back():
    declared = catalog.describe_agent_app(
        _site("garden_agent", "Garden", "Plant care", icon="heart", accent="#3366ff", category="media", keywords=["plants", "soil"])
    )
    assert (declared["glyph"], declared["category"]) == ("heart", "media")
    assert declared["colors"][0] != declared["colors"][1]
    assert declared["keywords"][:2] == ["plants", "soil"]

    broken = catalog.describe_agent_app(_site("garden_agent", "Garden", "Plant care", icon="not-a-glyph", accent="blue", category="nope"))
    assert broken["glyph"] in GLYPH_NAMES
    assert broken["category"] in catalog.CATEGORY_LABELS
    assert broken["colors"] == list(catalog.TONES[broken["tone"]])


def test_unknown_agents_get_an_inferred_icon_and_stable_tone():
    journal = catalog.describe_agent_app(_site("diary_agent", "Daily Diary", "Write a private journal"))
    assert journal["glyph"] == "notes"
    plain = catalog.describe_agent_app(_site("xyzzy_agent", "Xyzzy", "Does a thing"))
    assert plain["glyph"] == catalog.DEFAULT_GLYPH
    assert plain["rank"] == catalog.UNRANKED
    assert catalog.describe_agent_app(_site("xyzzy_agent"))["tone"] == plain["tone"]


def test_category_summary_lists_only_sections_in_use_in_store_order():
    apps = [
        {"app": {"category": "media"}},
        {"app": {"category": "everyday"}},
        {"app": {"category": "everyday"}},
    ]
    assert catalog.category_summary(apps) == [
        {"id": "everyday", "label": "Everyday", "count": 2},
        {"id": "media", "label": "Media", "count": 1},
    ]


def test_manifest_presentation_hints_pass_through_only_when_set():
    plain = _normalize_frontend_manifest_payload({"agent_name": "a"}, agent_name="a", manifest_path="m", website_root="w")
    assert not {"icon", "accent", "category", "keywords"} & set(plain)
    hinted = _normalize_frontend_manifest_payload(
        {"agent_name": "a", "icon": "bell", "accent": "#112233", "keywords": "one, two;three"},
        agent_name="a", manifest_path="m", website_root="w",
    )
    assert hinted["icon"] == "bell" and hinted["accent"] == "#112233"
    assert hinted["keywords"] == ["one", "two", "three"]
    built = build_frontend_manifest(agent_name="a", title="A", description="d", icon="bell", keywords=["x"])
    assert built["icon"] == "bell" and built["keywords"] == ["x"]
    assert "icon" not in build_frontend_manifest(agent_name="a", title="A", description="d")


# ---------- payload and search ----------


def test_api_payload_keeps_its_shape_and_adds_presentation(monkeypatch):
    service = _service(
        monkeypatch,
        [
            _site("notes_agent", "Notes Library", "Read notes", route_mode="direct_forward", direct_forward_port=8094, open_url="http://127.0.0.1:8094/"),
            _site("audio_agent", "Audio Player", "Play your library"),
        ],
        browser_base_url="http://127.0.0.1:8067",
    )
    monkeypatch.setattr(service, "_configured_server_name", lambda: "Studio Mac")
    body = TestClient(service.app).get("/api/websites").json()

    assert body["success"] is True
    assert body["autoyou_browser_base_url"] == "http://127.0.0.1:8067"
    assert body["server_name"] == "Studio Mac"
    assert [c["id"] for c in body["categories"]] == ["everyday", "media"]
    notes = next(item for item in body["frontends"] if item["agent_name"] == "notes_agent")
    # Native clients mirror direct ports from exactly these fields.
    assert notes["direct_forward_port"] == 8094
    assert notes["app"]["glyph"] == "notes"
    assert notes["display_name"] == "Notes"


def test_query_matches_keywords_and_section_names(monkeypatch):
    service = _service(
        monkeypatch,
        [_site("audio_agent", "Audio Player", "Plays files"), _site("notes_agent", "Notes Library", "Reads text")],
    )
    client = TestClient(service.app)

    def names(query):
        response = client.request("QUERY", "/api/websites", json={"query": query, "limit": 10})
        assert response.status_code == 200
        return [item["agent_name"] for item in response.json()["frontends"]]

    assert names("songs") == ["audio_agent"]
    assert names("media") == ["audio_agent"]
    assert names("journal") == ["notes_agent"]


# ---------- first paint ----------


def _boot(html_text):
    match = re.search(r'<script type="application/json" id="boot">(.*?)</script>', html_text, re.S)
    assert match, "boot data missing"
    return json.loads(match.group(1))


def test_first_paint_is_complete_ordered_and_self_contained(monkeypatch):
    service = _service(
        monkeypatch,
        [
            _site("zeta_agent", "Zeta", "Last by rank"),
            _site("notes_agent", "Notes Library", "Read notes"),
            _site("page_agent", "AutoYou Page", "Home"),
            _site("tasks_agent", "Tasks", "Missions", frontend_port_registered=False),
        ],
    )
    page = TestClient(service.app).get("/websites").text

    order = re.findall(r'<li class="cell[^"]*" data-name="([^"]+)"', page)
    assert order == ["page_agent", "notes_agent", "tasks_agent", "zeta_agent"]
    assert '<li class="cell is-wait" data-name="tasks_agent"' in page
    assert 'data-autoyou-scroll-managed="1"' in page.split("<body", 1)[0]
    assert "viewport-fit=cover" in page
    assert [app["name"] for app in _boot(page)["apps"]] == order
    # Everything the page needs is inline: no outside request on load.
    assert not re.search(r'(?:src|href)="https?://', page)
    assert "<link " not in page
    assert "Agent Apps" in page


def test_page_escapes_registry_text_and_search_prefill(monkeypatch):
    evil = '</script><img src=x onerror="alert(1)">'
    service = _service(monkeypatch, [_site("evil_agent", evil, evil)])
    page = TestClient(service.app).get("/websites", params={"q": evil}).text

    assert "<img src=x" not in page
    assert page.count("</script>") == 2  # boot data and the page script only
    assert _boot(page)["apps"][0]["title"] == evil
    assert _boot(page)["query"] == evil
    assert 'value="&lt;/script&gt;' in page


def test_only_paths_and_http_addresses_can_launch(monkeypatch):
    service = _service(
        monkeypatch,
        [
            _site("sneaky_agent", "Sneaky", "x", open_url="javascript:alert(1)", launch_path="javascript:alert(2)"),
            _site("proto_agent", "Proto", "x", open_url="//evil.example/x", launch_path="//evil.example/x"),
            _site("fine_agent", "Fine", "x", open_url="http://127.0.0.1:8001/", route_mode="direct_forward", direct_forward_port=8001),
        ],
    )
    page = TestClient(service.app).get("/websites").text
    assert 'href="javascript:' not in page
    apps = {app["name"]: app for app in _boot(page)["apps"]}
    # The directory already turns a bare launch path into a path on this server.
    assert apps["sneaky_agent"]["open_url"].startswith("/")
    assert apps["proto_agent"]["open_url"] == ""
    assert apps["fine_agent"]["open_url"] == "http://127.0.0.1:8001/"


def test_empty_registry_still_renders_a_helpful_page(monkeypatch):
    page = TestClient(_service(monkeypatch, []).app).get("/websites").text
    assert "No apps yet" in page
    assert _boot(page)["apps"] == []


def test_renderer_tolerates_sparse_entries():
    page = render_agent_apps_page({"frontends": [{"agent_name": "bare_agent"}, "junk", {"title": "nameless"}]})
    assert [a["name"] for a in _boot(page)["apps"]] == ["bare_agent"]


# ---------- script contract ----------


def test_script_keeps_the_native_client_contract():
    assert "fetch('/api/websites'" in JS  # native proxies learn direct ports from this exact request
    assert "method: 'QUERY'" in JS
    assert "autoyouAppsLayout" in JS and "autoyouHaptic" in JS
    assert "navigator.vibrate" in JS
    assert "touchmove" in JS and "gesturestart" in JS
    # Class names the shells' scroll scripts match on must not be used.
    assert not re.search(r'class="[^"]*(scroll|overflow)', render_agent_apps_page({"frontends": []}))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_script_parses(tmp_path):
    source = tmp_path / "apps.js"
    source.write_text(JS, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
