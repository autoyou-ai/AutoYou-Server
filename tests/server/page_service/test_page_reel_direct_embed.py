# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Tests for Instagram reel direct-first CDN playback and inline embed toggle (Options 1 & 2).

Verifies:
1. Direct-first CDN playback on client network with automatic computer relay fallback.
2. Per-item 'Play from Instagram' toggle button in the rail and keyboard shortcut 'E'.
3. Stream source badge (Direct, Relay, Embed) in the subtitle overlay.
4. Server-side in-memory resolution cache and mobile proxy caching headers on /api/media/stream.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import re
import time
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[3] / "autoyou_agents" / "page_agent" / "website" / "frontend" / "assets"
CSS = (ASSETS / "page.css").read_text(encoding="utf-8")
JS = (ASSETS / "page.js").read_text(encoding="utf-8")
APP_PY = (ASSETS.parents[1] / "backend" / "app.py").read_text(encoding="utf-8")


def _rule(selector: str, css: str = CSS) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"no rule for {selector}"
    return match.group(1)


def test_instagram_icon_and_stream_badge_css():
    """Verify icon and badges for direct, relay and embed modes in CSS."""
    assert ".ic-instagram" in CSS
    badge = _rule(".reel-stream-badge")
    assert "border-radius: 999px" in badge
    assert "text-transform: uppercase" in badge
    direct = _rule('.reel-stream-badge[data-mode="direct"]')
    relay = _rule('.reel-stream-badge[data-mode="relay"]')
    embed = _rule('.reel-stream-badge[data-mode="embed"]')
    assert "#4ade80" in direct  # green for direct
    assert "#60a5fa" in relay   # blue for relay
    assert "#f472b6" in embed   # pink for embed


def test_instagram_reel_mode_toggle_rail_button_and_shortcut():
    """Verify Option 1: Rail button and keyboard shortcut to toggle between Video and Instagram embed."""
    assert 'railButton(\n        "toggle-embed"' in JS or 'railButton("toggle-embed"' in JS
    assert 'data-reel-action="toggle-embed"' in JS
    assert "function toggleSlideMode(slide, item)" in JS
    assert "function getReelSlideMode(item)" in JS
    assert "function setReelSlideMode(item, mode)" in JS
    # Toggling action
    assert 'name === "toggle-embed" && slide && item) {\n      toggleSlideMode(slide, item);' in JS
    # Keyboard shortcut 'e' or 'E'
    assert 'key === "e" || key === "E"' in JS
    # LocalStorage preference
    assert "autoyou.page.itemMode." in JS
    assert "autoyou.page.reelDefaultMode" in JS


def test_direct_first_video_playback_and_fallbacks():
    """Verify Option 2: Direct-first from CDN on client network, with relay and embed fallbacks."""
    # Direct CDN src and referrer policy
    assert 'referrerpolicy="no-referrer"' in JS
    assert 'data-stream-mode="${isDirect ? "direct" : "relay"}"' in JS
    # Fallback watchdog and handlers
    assert "function setupVideoFallback(video, slide, stage)" in JS
    assert "triggerRelayFallback" in JS
    assert "triggerEmbedFallback" in JS
    # Fallback timeout for stalled direct connections
    assert "3800" in JS
    # Fallback on video error
    assert 'video.addEventListener("error"' in JS


def test_server_side_media_resolve_cache():
    """Verify server caches resolved direct media URLs to avoid redundant yt-dlp processes."""
    from autoyou_agents.page_agent.website.backend.app import PageFeedService

    service = PageFeedService.__new__(PageFeedService)
    service._media_resolve_cache = {}

    test_url = "https://www.instagram.com/reel/C1234567890/"
    cached_cdn = "https://instagram.fcdn.net/v/test.mp4?oe=9999"

    # Pre-populate cache
    service._media_resolve_cache[test_url] = (cached_cdn, time.monotonic())
    # Resolving should return immediately from memory without calling yt-dlp
    result = service._resolve_media_url(test_url)
    assert result == cached_cdn


def test_server_stream_headers_for_proxy_caching():
    """Verify /api/media/stream attaches Cache-Control and X-AutoYou-Media-Cache for phone storage."""
    assert 'resp.headers["X-AutoYou-Media-Cache"] = "private, max-age=604800, immutable"' in APP_PY
    assert 'resp.headers["Cache-Control"] = "private, max-age=86400"' in APP_PY
