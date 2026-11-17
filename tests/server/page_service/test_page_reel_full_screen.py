# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The Page's full-screen feed gives the item the screen (owner request 2026-11-03).

Posts and players fill the screen, the buttons float over them, the text
shows like subtitles when asked for and goes away, and an Instagram reel
plays as the vertical video it is instead of a card Instagram crops to 4:5.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[3] / "autoyou_agents" / "page_agent" / "website" / "frontend" / "assets"
CSS = (ASSETS / "page.css").read_text(encoding="utf-8")
JS = (ASSETS / "page.js").read_text(encoding="utf-8")


def _rule(selector: str, css: str = CSS) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"no rule for {selector}"
    return match.group(1)


def _wide_screen_css() -> str:
    start = CSS.index("@media (min-width: 760px)")
    return CSS[start:CSS.index("@media (max-width: 380px)", start)]


def test_posts_and_players_fill_the_screen():
    post = _rule(".reel-post")
    embed = _rule(".reel-embed")
    assert "width: 100%" in post and "height: min(100%, var(--frame-height, 100%))" in post
    assert "width: 100%" in embed and "height: 100%" in embed and "aspect-ratio" not in embed
    # Nothing reserves room around a post for the caption or the buttons any more.
    assert "min(100%, 440px)" not in post
    assert "calc(150px + env(safe-area-inset-bottom))" not in CSS
    assert "padding-right: 80px" not in CSS


def test_the_text_shows_like_subtitles_when_asked_and_goes_away():
    info = _rule(".reel-info")
    shown = _rule(".reel-slide.captions-on .reel-info")
    line = _rule(".reel-line")
    assert "opacity: 0" in info and "visibility: hidden" in info
    assert "opacity: 1" in shown and "visibility: visible" in shown
    # Each line on its own dark box: legible over a white post or a black video.
    assert "background: rgba(8, 10, 14, 0.8)" in line and "box-decoration-break: clone" in line
    assert 'railButton("caption", "captions", "Text", "Show the text"' in JS
    assert "const CAPTION_MS = 6000;" in JS
    assert re.search(r'name === "caption" && slide\) \{\s*toggleCaptions\(slide\);', JS)
    assert re.search(r'key === "c" \|\| key === "C"\) \{\s*toggleCaptions\(currentSlide\(\)\);', JS)
    # A tap that does nothing else (a card, the space around a post) toggles it.
    assert re.search(r"\} else \{\s*toggleCaptions\(slide\);\s*\}\s*\}, access\.can_edit", JS)


def test_buttons_float_over_the_item_and_name_themselves_beside_it_on_wide_screens():
    assert "display: none" in _rule(".rail-label")
    assert "background: rgba(8, 10, 14, 0.55)" in _rule(".rail-icon")
    assert "display: block" in _rule(".rail-label", _wide_screen_css())
    # The heading fades after a moment so it does not sit on the item's own top.
    assert "opacity: 0" in _rule(".reel.is-quiet .reel-heading")
    assert "const HEADING_MS = 2600;" in JS


def test_an_instagram_reel_plays_as_its_vertical_video_with_the_post_as_fallback():
    assert re.search(
        r'if \(type === "instagram" && open && instagramReel\(item\)\) return "video";', JS
    )
    assert '["reel", "reels", "tv"].includes(' in JS
    assert '(kind === "video" && instagramReel(item))) return "portrait";' in JS
    fallback = JS[JS.index("async function resolveReelVideo"):JS.index("// Text files show their opening lines")]
    assert 'instagramReel(item) ? postEmbed(item, "dark")' in fallback
    assert '<div class="reel-post">${reelFrame(embed' in fallback


def test_a_link_saved_without_a_title_reads_as_its_address():
    helper = JS[JS.index("function captionTitle(item)"):JS.index("function reelInfoMarkup(item)")]
    assert 'url.hostname.replace(/^www\\./, "")' in helper
    assert 'url.pathname.replace(/\\/+$/, "")' in helper
    assert "escapeHtml(captionTitle(item))" in JS
