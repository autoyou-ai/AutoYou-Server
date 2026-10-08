# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Server-rendered Agent Apps store page.

The page is one self-contained document (markup, style, script and icon sprite),
so a phone behind a slow tunnel gets a complete first paint from a single
response and the page keeps working when it is opened from the home network,
through a paired client's local proxy, or straight on this computer.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import html
import json
from typing import Any, Dict, List, Mapping

from shared.agent_apps.catalog import CATEGORY_LABELS, DEFAULT_CATEGORY, TONES
from shared.agent_apps.glyphs import sprite_markup
from shared.agent_apps.page_css import CSS
from shared.agent_apps.page_js import JS

PAGE_TITLE = "Agent Apps"


def app_open_target(frontend: Mapping[str, Any]) -> str:
    """Where an app opens: the same precedence the website directory has always used.

    Only a path on this server or an http(s) address is a launch target, so a
    registry value can never become a ``javascript:`` link.
    """
    target = str(
        frontend.get("open_url") or frontend.get("launch_url") or frontend.get("launch_path") or ""
    ).strip()
    if target.startswith("/") and not target.startswith("//"):
        return target
    return target if target.lower().startswith(("http://", "https://")) else ""


def page_app(frontend: Mapping[str, Any]) -> Dict[str, Any]:
    """One website entry in the shape the page script keeps in memory."""
    name = str(frontend.get("agent_name") or "").strip()
    presentation = frontend.get("app") if isinstance(frontend.get("app"), dict) else {}
    target = app_open_target(frontend)
    colors = presentation.get("colors") or list(TONES[0])
    port = frontend.get("direct_forward_port") or frontend.get("proxy_port") or 0
    category = str(presentation.get("category") or DEFAULT_CATEGORY)
    return {
        "name": name,
        "title": str(frontend.get("title") or frontend.get("display_name") or name or "App").strip(),
        "description": str(frontend.get("description") or "").strip(),
        "open_url": target,
        "ready": bool(frontend.get("frontend_port_registered") and target),
        "route_mode": str(frontend.get("route_mode") or "path_proxy"),
        "port": port if isinstance(port, int) else 0,
        "app": {
            "glyph": str(presentation.get("glyph") or "globe"),
            "category": category,
            "category_label": str(presentation.get("category_label") or CATEGORY_LABELS.get(category, "More")),
            "tone": int(presentation.get("tone") or 0),
            "colors": [str(colors[0]), str(colors[1])],
            "keywords": [str(word) for word in (presentation.get("keywords") or [])][:12],
            "rank": int(presentation.get("rank") or 100),
        },
    }


def _attr(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _glyph(name: str) -> str:
    return f'<svg class="glyph" viewBox="0 0 24 24" aria-hidden="true"><use href="#g-{_attr(name)}"/></svg>'


def _cell(app: Mapping[str, Any]) -> str:
    colors = app["app"]["colors"]
    name = _attr(app["name"])
    title = html.escape(app["title"])
    label = _attr(app["title"] + ("" if app["ready"] else ", not ready"))
    classes = "cell" if app["ready"] else "cell is-wait"
    return (
        f'<li class="{classes}" data-name="{name}">'
        f'<a class="app" href="{_attr(app["open_url"] or "#")}" draggable="false" aria-label="{label}" '
        f'aria-describedby="d-{name}">'
        f'<span class="icon" style="--a:{_attr(colors[0])};--b:{_attr(colors[1])}">{_glyph(app["app"]["glyph"])}</span>'
        f'<span class="label">{title}</span></a></li>'
    )


def _chips(apps: List[Mapping[str, Any]], categories: List[Mapping[str, Any]]) -> str:
    buttons = [f'<button type="button" class="chip" data-cat="all" aria-pressed="true">All <b>{len(apps)}</b></button>']
    for category in categories:
        buttons.append(
            f'<button type="button" class="chip" data-cat="{_attr(category["id"])}" aria-pressed="false">'
            f'{html.escape(str(category["label"]))} <b>{int(category["count"])}</b></button>'
        )
    return "".join(buttons)


def inline_json(value: Any) -> str:
    """JSON that is safe inside a script element."""
    return (
        json.dumps(value, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


_SIZE_BUTTONS = "".join(
    f'<button type="button" role="radio" aria-checked="false" aria-label="Size {index + 1} of 5" '
    f'style="font-size:{size}rem">A</button>'
    for index, size in enumerate((0.78, 0.95, 1.15, 1.4, 1.7))
)

_PAGE = """<!DOCTYPE html>
<html lang="en" data-autoyou-scroll-managed="1">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<meta name="theme-color" content="#090b14" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#eef1fc" media="(prefers-color-scheme: light)">
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body data-autoyou-scroll-managed="1">
__SPRITE__
<div class="shell" id="shell">
  <header class="hero">
    <span class="mark">__MARK__</span>
    <div class="hero-text">
      <h1>__TITLE__</h1>
      <p class="sub" id="sub">__SUBTITLE__</p>
    </div>
  </header>
  <div class="bar" id="bar">
    <form class="search" id="search-form" role="search" action="#">
      __SEARCH_ICON__
      <label class="sr" for="q">Search apps by name or by what they do</label>
      <input id="q" type="search" maxlength="200" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="search" placeholder="Search apps" value="__QUERY__">
      <button type="button" class="ibtn" id="q-clear" aria-label="Clear search" __CLEAR_HIDDEN__>__CLOSE_ICON__</button>
    </form>
    <button type="button" class="ibtn" id="btn-refresh" aria-label="Refresh apps">__REFRESH_ICON__</button>
    <button type="button" class="ibtn" id="btn-view" aria-label="Text size and layout" aria-haspopup="true" aria-expanded="false" aria-controls="sheet">__TEXT_ICON__</button>
  </div>
  <div class="layout">
    <main>
      <nav class="chips" id="chips" aria-label="Sections">__CHIPS__</nav>
      <p class="note" id="note" hidden>__OFFLINE_ICON__<span>This computer is not answering right now. Showing the apps seen last.</span></p>
      <ul class="grid" id="grid" role="list" aria-label="Apps">__CELLS__</ul>
      <div class="empty" id="empty" hidden role="status">
        <strong>Nothing matches</strong><span>Try another word.</span>
        <p><button type="button" class="link" id="empty-clear">Clear search</button></p>
      </div>
    </main>
    <aside class="dock" id="dock" data-expanded="0" data-collapsed="0" aria-label="About the selected app">
      <div class="dock-content" id="dock-content">
        <div class="dock-head">
          <span class="icon" id="dock-icon" style="--a:#8e9bff;--b:#6a3df0"></span>
          <div class="dock-text">
            <div class="dock-title"><h2 id="dock-name">__FIRST_TITLE__</h2><span class="pill" id="dock-cat"></span></div>
          </div>
        </div>
        <div class="dock-body" id="dock-body">
          <p class="dock-desc" id="dock-desc">__FIRST_DESCRIPTION__</p>
          <div class="dock-meta" id="dock-meta"></div>
        </div>
        <div class="dock-actions" id="dock-actions">
          <a class="btn primary" id="dock-open" href="#">__OPEN_ICON__Open</a>
          <button type="button" class="btn" id="dock-copy">__COPY_ICON__Copy link</button>
        </div>
      </div>
      <button type="button" class="dock-grab" aria-label="Hide selected app details" aria-expanded="true" aria-controls="dock-content"><i></i></button>
      <p class="dock-hint" id="dock-hint">Slide to preview. Hold to move. Pinch to resize.</p>
    </aside>
  </div>
</div>
<div class="sheet" id="sheet" hidden role="group" aria-label="Text size and layout">
  <section>
    <h3>Text and icon size</h3>
    <div class="seg sizes" id="sizes" role="radiogroup" aria-label="Text and icon size">__SIZE_BUTTONS__</div>
    <small>Pinch with two fingers on the apps to fine-tune.</small>
  </section>
  <section>
    <h3>Arrange</h3>
    <div class="seg" id="sorts" role="radiogroup" aria-label="Arrange apps">
      <button type="button" role="radio" aria-checked="true" data-sort="custom">My order</button>
      <button type="button" role="radio" aria-checked="false" data-sort="usage">Most used</button>
      <button type="button" role="radio" aria-checked="false" data-sort="name">A to Z</button>
    </div>
  </section>
  <section class="row">
    <small>Saved in this browser, on this device.</small>
    <button type="button" class="link" id="btn-reset">Reset layout</button>
  </section>
</div>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<div class="sr" id="live" aria-live="polite"></div>
<script type="application/json" id="boot">__BOOT__</script>
<script>__JS__</script>
</body>
</html>
"""


def render_agent_apps_page(
    payload: Mapping[str, Any],
    *,
    server_name: str = "",
    query: str = "",
) -> str:
    """The full store page for the registry payload ``autoyou_page_service`` serves."""
    frontends = payload.get("frontends") if isinstance(payload.get("frontends"), list) else []
    apps = [page_app(item) for item in frontends if isinstance(item, dict) and item.get("agent_name")]
    apps.sort(key=lambda app: (app["app"]["rank"], app["title"].casefold()))
    categories = payload.get("categories") if isinstance(payload.get("categories"), list) else []
    query = str(query or "").strip()[:200]
    server_name = str(server_name or "").strip()[:64]
    boot = {
        "apps": apps,
        "base": str(payload.get("autoyou_browser_base_url") or ""),
        "server_name": server_name,
        "categories": categories,
        "query": query,
    }
    total = len(apps)
    subtitle = f"{total} app{'' if total == 1 else 's'}" + (f" on {server_name}" if server_name else "")
    first = apps[0] if apps else {"title": "No apps yet", "description": "Turn a website on in the admin page and it will appear here."}
    replacements = {
        "__TITLE__": PAGE_TITLE,
        "__CSS__": CSS,
        "__SPRITE__": sprite_markup(),
        "__MARK__": _glyph("ui-mark"),
        "__SUBTITLE__": html.escape(subtitle),
        "__SEARCH_ICON__": _glyph("ui-search"),
        "__CLOSE_ICON__": _glyph("ui-close"),
        "__REFRESH_ICON__": _glyph("ui-refresh"),
        "__TEXT_ICON__": _glyph("ui-text"),
        "__OPEN_ICON__": _glyph("ui-open"),
        "__COPY_ICON__": _glyph("ui-copy"),
        "__OFFLINE_ICON__": _glyph("ui-wifi-off"),
        "__QUERY__": _attr(query),
        "__CLEAR_HIDDEN__": "" if query else "hidden",
        "__CHIPS__": _chips(apps, categories),
        "__CELLS__": "".join(_cell(app) for app in apps),
        "__FIRST_TITLE__": html.escape(first["title"]),
        "__FIRST_DESCRIPTION__": html.escape(first["description"] or ""),
        "__SIZE_BUTTONS__": _SIZE_BUTTONS,
        "__BOOT__": inline_json(boot),
        "__JS__": JS,
    }
    # The script and style are substituted last so their text is never scanned
    # for the page's other placeholders.
    document = _PAGE
    for token, value in replacements.items():
        if token in ("__JS__", "__CSS__"):
            continue
        document = document.replace(token, value)
    return document.replace("__CSS__", replacements["__CSS__"]).replace("__JS__", replacements["__JS__"])
