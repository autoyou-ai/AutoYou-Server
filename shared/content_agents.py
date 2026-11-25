"""Who made a piece of content: one name, icon and accent per agent.

My Content groups everything by the agent that produced it. Built-in agents are
named here once, so the admin console, the Organizer site and the phones agree.
Agents created on a phone have no code on this computer; the phone sends the
agent's identity with each record and it is kept in the catalog, so a custom
agent is shown by its own name, icon and accent instead of an internal id.
"""
from __future__ import annotations

import re
import time

from shared.content_library import ContentLibrary

# source -> (display name, icon key, owns its data). Icon keys name a small shared
# vocabulary the screen draws itself; they are not file names or markup.
BUILTIN = {
    "notes_agent": ("Notes", "note", True),
    "page_agent": ("Pages", "page", True),
    "media_generation_agent": ("Generate Media", "image", True),
    "music_agent": ("Music", "audio", True),
    "media_agent": ("Media", "video", True),
    "files_agent": ("Files", "file", False),
    "voice_training_agent": ("Voice Training", "voice", True),
    "location_agent": ("Locations", "pin", True),
    "diary_agent": ("Diary", "book", True),
    "profile_agent": ("Profile", "agent", True),
    "games_agent": ("Games", "game", True),
    "reminders_agent": ("Reminders", "bell", True),
    "organizer_agent": ("Organizer", "folder", False),
}
BASE_ICONS = {"notes": "note", "diary": "book", "page": "page", "media": "image", "music": "audio",
              "reminders": "bell", "assistant": "spark"}
ICON_KEYS = {"note", "page", "image", "audio", "video", "file", "voice", "pin", "book", "game", "bell", "folder", "spark", "agent"}
CUSTOM = re.compile(r"custom_[a-z0-9_-]{1,56}\Z")
_HANDLE = re.compile(r"[a-z0-9_]{1,32}\Z")
_ICON = re.compile(r"[a-z0-9_.-]{1,32}\Z")
_ACCENT = re.compile(r"#[0-9a-fA-F]{6}\Z")
_SUFFIX = re.compile(r"_[0-9a-f]{6}\Z")


def is_custom(source: str) -> bool:
    return bool(CUSTOM.fullmatch(str(source)))


def owns_data(source: str) -> bool:
    """True when the agent keeps its own store and My Content is only an index of it."""
    return BUILTIN.get(source, ("", "", False))[2]


def fallback_name(source: str) -> str:
    text = _SUFFIX.sub("", re.sub(r"^custom_", "", str(source)))
    text = re.sub(r"_agent\Z", "", text).replace("_", " ").replace("-", " ").strip()
    return text.title() or "Agent"


def clean_identity(payload: dict) -> dict:
    """Bound and validate what a phone says about its agent; anything odd is dropped."""
    name = " ".join(str(payload.get("agent_name") or "").split())[:48]
    handle = str(payload.get("agent_handle") or "").strip().lower().lstrip("@")
    icon = str(payload.get("agent_icon") or "").strip().lower()
    accent = str(payload.get("agent_accent") or "").strip()
    return {
        "name": name,
        "handle": handle if _HANDLE.fullmatch(handle) else "",
        "icon": icon if _ICON.fullmatch(icon) else "",
        "accent": accent if _ACCENT.fullmatch(accent) else "",
        "base_kind": str(payload.get("base_kind") or "")[:24],
    }


def remember(db, source: str, identity: dict) -> None:
    """Keep the newest identity a device sent. Called inside the importer's transaction."""
    if not is_custom(source) or not any(identity.values()):
        return
    db.execute("""INSERT INTO content_agents(source,name,handle,icon,accent,base_kind,updated) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(source) DO UPDATE SET name=CASE WHEN excluded.name!='' THEN excluded.name ELSE name END,
        handle=CASE WHEN excluded.handle!='' THEN excluded.handle ELSE handle END,
        icon=CASE WHEN excluded.icon!='' THEN excluded.icon ELSE icon END,
        accent=CASE WHEN excluded.accent!='' THEN excluded.accent ELSE accent END,
        base_kind=CASE WHEN excluded.base_kind!='' THEN excluded.base_kind ELSE base_kind END,
        updated=excluded.updated""",
               (source, identity["name"], identity["handle"], identity["icon"], identity["accent"], identity["base_kind"], time.time()))


def describe(source: str, stored: dict | None = None) -> dict:
    """The public identity of one agent. Never raises for an unknown source."""
    stored = stored or {}
    if source in BUILTIN:
        name, icon, owns = BUILTIN[source]
        return {"source": source, "name": name, "icon": icon, "accent": "", "handle": "", "native": False,
                "builtin": True, "owns_data": owns, "app": "/agent/" + source + "/"}
    base = stored.get("base_kind", "")
    icon = BASE_ICONS.get(base, "agent")
    return {"source": source, "name": stored.get("name") or fallback_name(source), "icon": icon,
            "accent": stored.get("accent", ""), "handle": stored.get("handle", ""),
            "native": is_custom(source), "builtin": False, "owns_data": False,
            "app": "/agent/organizer_agent/?content_source=" + source if is_custom(source) else ""}


def describe_all(library: ContentLibrary, sources) -> dict:
    """Identity for every source in `sources`, read with one query."""
    with library.connect() as db:
        stored = {row["source"]: dict(row) for row in db.execute("SELECT * FROM content_agents")}
    return {source: describe(source, stored.get(source)) for source in sources}
