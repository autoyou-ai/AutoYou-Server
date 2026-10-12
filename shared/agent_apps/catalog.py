# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""How each website app looks and where it sits in the Agent Apps store.

Every registered agent website gets a small presentation record: a glyph from
the built-in web-icon set, a gradient, a category and a default position. A
website manifest may set ``icon``, ``accent``, ``category`` and ``keywords`` to
choose its own; anything it leaves out is inferred from the agent's name and
description, so a freshly scaffolded agent still lands with a sensible icon.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import re
import zlib
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from shared.agent_apps.glyphs import GLYPH_NAMES

DEFAULT_GLYPH = "globe"
DEFAULT_CATEGORY = "other"
UNRANKED = 100

#: Store sections, in the order the category chips show them.
CATEGORIES: Tuple[Tuple[str, str], ...] = (
    ("everyday", "Everyday"),
    ("create", "Create"),
    ("media", "Media"),
    ("computer", "Computer"),
    ("community", "Community"),
    (DEFAULT_CATEGORY, "More"),
)
CATEGORY_LABELS: Dict[str, str] = dict(CATEGORIES)

#: Gradient pairs (light stop, deep stop). The icon tone is an index into this.
TONES: Tuple[Tuple[str, str], ...] = (
    ("#ff8a5c", "#f0366b"),
    ("#ffc23d", "#ff7a2f"),
    ("#3ddfb0", "#0f9aa6"),
    ("#4cc3ff", "#2f5bea"),
    ("#8e9bff", "#6a3df0"),
    ("#f58ad0", "#b72ad6"),
    ("#b4e84f", "#1f9d55"),
    ("#2fd8f0", "#5b5ff0"),
    ("#ff8196", "#d6204f"),
    ("#ffd93d", "#f2730f"),
    ("#69efd2", "#3a7bf0"),
    ("#c79bff", "#5457ee"),
)

# name -> (glyph, category, default position, search keywords)
_KNOWN: Dict[str, Tuple[str, str, int, Tuple[str, ...]]] = {
    "page_agent": ("page", "everyday", 1, ("feed", "links", "photos", "saved", "home", "videos")),
    "notes_agent": ("notes", "everyday", 2, ("journal", "writing", "memo", "notebook")),
    "tasks_agent": ("tasks", "everyday", 3, ("schedule", "recurring", "missions", "todo", "scheduler")),
    "notify_agent": ("bell", "everyday", 4, ("reminders", "alerts", "notifications")),
    "files_agent": ("folder", "computer", 5, ("finder", "documents", "browse")),
    "audio_agent": ("music", "media", 6, ("songs", "player", "playlist", "listen")),
    "media_generation_agent": ("film", "media", 7, ("video", "image", "generate", "art")),
    "skills_agent": ("bolt", "everyday", 8, ("automation", "custom", "workflows")),
    "persona_agent": ("user", "everyday", 9, ("profile", "facts", "about me")),
    "agent_builder_agent": ("sparkles", "create", 10, ("scaffold", "build agents", "draft")),
    "website_agent": ("layout", "create", 11, ("frontend", "design", "builder", "publish")),
    "build_prompt_agent": ("terminal", "create", 12, ("codex", "claude", "prompt", "code")),
    "game_agent": ("gamepad", "create", 13, ("games", "engine", "play")),
    "voice_training_agent": ("mic", "create", 14, ("speech", "clone", "tts", "voice")),
    "fine_tuning_agent": ("sliders", "create", 15, ("train", "model", "dataset", "personal model")),
    "remote_desktop_agent": ("monitor", "computer", 16, ("screen", "cast", "mouse", "keyboard", "control")),
    "backup_agent": ("archive", "computer", 17, ("restore", "sync", "copy")),
    "location_agent": ("pin", "computer", 18, ("map", "places", "timeline", "gps")),
    "hosting_agent": ("broadcast", "computer", 19, ("public", "link", "tunnel", "share")),
    "proxy_agent": ("route", "computer", 20, ("relay", "internet", "browse")),
    "admin_agent": ("settings", "computer", 21, ("admin", "server", "configuration")),
    "mac_security_agent": ("shield", "computer", 22, ("connections", "network", "security")),
    "win_security_agent": ("shield", "computer", 23, ("connections", "network", "security")),
    "education_agent": ("book", "community", 24, ("learn", "classes", "courses", "live")),
    "donation_agent": ("heart", "community", 25, ("support", "donate", "crypto")),
    "earnings_agent": ("coins", "community", 26, ("credits", "rewards", "money")),
    "ads_watching_agent": ("tv", "community", 27, ("ads", "watch", "rewards")),
    "data_collector_agent": ("database", "computer", 28, ("export", "dataset", "training", "privacy")),
}

# First rule that matches a word in "name title description" picks the glyph.
_INFERENCE: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("notes", ("note", "journal", "memo", "diary")),
    ("tasks", ("task", "todo", "checklist", "mission", "scheduler")),
    ("calendar", ("calendar", "agenda", "event")),
    ("bell", ("notify", "notification", "reminder", "alert")),
    ("music", ("music", "audio", "song", "podcast", "playlist")),
    ("film", ("video", "movie", "film", "image", "photo", "media", "generator")),
    ("camera", ("camera", "scan")),
    ("folder", ("file", "folder", "document", "finder")),
    ("archive", ("backup", "archive", "restore")),
    ("gamepad", ("game", "arcade", "puzzle")),
    ("mic", ("voice", "speech", "microphone", "tts", "dictation")),
    ("chat", ("chat", "message", "conversation", "assistant")),
    ("mail", ("mail", "inbox")),
    ("pin", ("location", "map", "gps", "places")),
    ("shield", ("security", "secure", "privacy", "firewall", "protect")),
    ("key", ("password", "otp", "credential", "authenticator", "vault")),
    ("settings", ("admin", "setting", "configure", "configuration", "preferences")),
    ("terminal", ("terminal", "shell", "command", "prompt", "developer")),
    ("code", ("code", "coding", "script", "program")),
    ("chart", ("trading", "market", "analytics", "statistics", "chart", "finance")),
    ("database", ("database", "dataset", "collector", "records")),
    ("book", ("learn", "education", "course", "lesson", "study", "book")),
    ("heart", ("donate", "donation", "support", "charity")),
    ("coins", ("earn", "credit", "payment", "wallet", "reward", "price")),
    ("tv", ("ads", "advert", "watch", "stream")),
    ("monitor", ("desktop", "screen", "monitor", "display")),
    ("sliders", ("tune", "training", "model", "tuning")),
    ("sparkles", ("builder", "create", "generate", "magic")),
    ("broadcast", ("host", "publish", "public", "tunnel")),
    ("route", ("relay", "proxy", "route", "vpn")),
    ("cloud", ("cloud", "sync", "upload")),
    ("search", ("search", "find", "lookup")),
    ("user", ("profile", "persona", "account", "contact")),
    ("bolt", ("skill", "automation", "workflow", "power")),
)

_GLYPH_CATEGORY = {
    "notes": "everyday", "tasks": "everyday", "calendar": "everyday", "bell": "everyday",
    "mail": "everyday", "chat": "everyday", "user": "everyday", "bolt": "everyday",
    "page": "everyday", "search": "everyday", "home": "everyday",
    "music": "media", "film": "media", "camera": "media", "tv": "media",
    "sparkles": "create", "layout": "create", "terminal": "create", "code": "create",
    "gamepad": "create", "mic": "create", "sliders": "create", "chart": "create",
    "folder": "computer", "archive": "computer", "pin": "computer", "shield": "computer",
    "key": "computer", "settings": "computer", "monitor": "computer", "route": "computer",
    "broadcast": "computer", "database": "computer", "cloud": "computer", "lock": "computer",
    "book": "community", "heart": "community", "coins": "community",
}

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_WORD = re.compile(r"[a-z0-9]+")


def _text(value: Any, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _words(*values: Any) -> List[str]:
    return _WORD.findall(" ".join(str(value or "") for value in values).lower())


def _expand_hex(value: str) -> str:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return value.lower()


def _shade(color: str, factor: float) -> str:
    """Lighten (factor > 0) or darken (factor < 0) a ``#rrggbb`` colour."""
    digits = _expand_hex(color)
    channels = [int(digits[index:index + 2], 16) for index in (0, 2, 4)]
    if factor >= 0:
        channels = [round(channel + (255 - channel) * factor) for channel in channels]
    else:
        channels = [round(channel * (1 + factor)) for channel in channels]
    return "#" + "".join(f"{max(0, min(255, channel)):02x}" for channel in channels)


def accent_colors(accent: Any) -> Optional[List[str]]:
    """Gradient stops for a manifest ``accent`` colour, or None when it is not a hex colour."""
    candidate = _text(accent, 16)
    if not _HEX.match(candidate):
        return None
    base = "#" + _expand_hex(candidate)
    return [_shade(base, 0.22), _shade(base, -0.28)]


def _tone_for(name: str) -> int:
    return zlib.crc32(name.encode("utf-8")) % len(TONES)


def _infer_glyph(name: str, title: str, description: str) -> str:
    # Every registered name ends in "_agent"; that word says nothing about the app.
    words = [word for word in _words(name, title, description) if word != "agent"]
    for glyph, stems in _INFERENCE:
        for word in words:
            if any(word == stem or word.startswith(stem) for stem in stems):
                return glyph
    return DEFAULT_GLYPH


def _keyword_list(values: Any) -> List[str]:
    if isinstance(values, str):
        values = re.split(r"[,;]", values)
    if not isinstance(values, (list, tuple)):
        return []
    cleaned: List[str] = []
    for value in values:
        text = _text(value, 40)
        if text and text.lower() not in (item.lower() for item in cleaned):
            cleaned.append(text)
        if len(cleaned) >= 12:
            break
    return cleaned


def describe_agent_app(frontend: Mapping[str, Any]) -> Dict[str, Any]:
    """The presentation record for one registered agent website."""
    name = _text(frontend.get("agent_name"), 100)
    title = _text(frontend.get("title"), 120)
    description = _text(frontend.get("description"), 600)
    known = _KNOWN.get(name)

    declared_glyph = _text(frontend.get("icon"), 32).lower()
    if declared_glyph in GLYPH_NAMES:
        glyph = declared_glyph
    elif known:
        glyph = known[0]
    else:
        glyph = _infer_glyph(name, title, description)

    declared_category = _text(frontend.get("category"), 32).lower()
    if declared_category in CATEGORY_LABELS:
        category = declared_category
    elif known:
        category = known[1]
    else:
        category = _GLYPH_CATEGORY.get(glyph, DEFAULT_CATEGORY)

    keywords = _keyword_list(frontend.get("keywords"))
    if known:
        keywords = keywords + [word for word in known[3] if word not in keywords]

    colors = accent_colors(frontend.get("accent"))
    tone = _tone_for(name)
    if colors is None:
        colors = list(TONES[tone])

    return {
        "glyph": glyph,
        "category": category,
        "category_label": CATEGORY_LABELS[category],
        "tone": tone,
        "colors": colors,
        "keywords": keywords,
        "rank": known[2] if known else UNRANKED,
    }


def category_summary(frontends: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Categories that have at least one app, in store order, with their counts."""
    counts: Dict[str, int] = {}
    for frontend in frontends:
        app = frontend.get("app") if isinstance(frontend, Mapping) else None
        category = str((app or {}).get("category") or DEFAULT_CATEGORY)
        counts[category] = counts.get(category, 0) + 1
    return [
        {"id": identifier, "label": label, "count": counts[identifier]}
        for identifier, label in CATEGORIES
        if counts.get(identifier)
    ]
