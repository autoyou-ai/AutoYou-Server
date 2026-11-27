"""Date-indexed Markdown journal and its independently protected media."""
from __future__ import annotations

import datetime as _datetime
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import uuid

from filelock import FileLock
from google.adk.agents import Agent

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION

LOGGER = logging.getLogger(__name__)
_DIARY_DIRNAME = "diary_agent"
_DIARY_FILENAME = "diary.md"
_JOURNALS_FILENAME = "journals.json"
_TEMPLATES_FILENAME = "templates.json"
_PREFERENCES_FILENAME = "preferences.json"
_KEYSTORE_SERVICE = "AutoYou-DiaryData"

_ATTRIBUTES = "<!-- autoyou-entry-details "
_RICH_ATTR_KEYS = ("location", "weather", "mood", "activity", "steps", "pinned", "template")
_MEDIA_LINK_RE = re.compile(
    r"\[([^\]\n]{1,160})\]\((?:/agent/diary_agent)?/api/diary/entries/([0-9A-Fa-f-]{36})/media/([A-Za-z0-9_-]{1,64})\)"
)
_AUDIO_EXT_RE = re.compile(r"\.(m4a|aac|mp3|wav|ogg|oga|opus|flac|weba|amr|caf)$", re.IGNORECASE)
_VIDEO_EXT_RE = re.compile(r"\.(mp4|m4v|mov|webm|3gp|mkv)$", re.IGNORECASE)
_IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp|heic|heif|avif)$", re.IGNORECASE)
_CHECKLIST_RE = re.compile(r"(?m)^\s*[-*+]\s+\[[ xX]\]\s+")

_JOURNAL_PALETTE = [
    "#44B6FF",
    "#2FB67C",
    "#F59E0B",
    "#8B5CF6",
    "#EC4899",
    "#0EA5E9",
    "#EF4444",
    "#14B8A6",
    "#6366F1",
    "#F97316",
]

BUILTIN_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "five-minute-am",
        "name": "5 Minute AM",
        "category": "Five Minute Journal",
        "color": "#F59E0B",
        "icon": "sun",
        "builtin": True,
        "title": "Morning Intentions",
        "tags": ["morning", "gratitude"],
        "body": (
            "### I am grateful for...\n"
            "1. \n2. \n3. \n\n"
            "### What would make today great?\n"
            "- [ ] \n- [ ] \n- [ ] \n\n"
            "### Daily affirmation\n"
            "I am "
        ),
    },
    {
        "id": "five-minute-pm",
        "name": "5 Minute PM",
        "category": "Five Minute Journal",
        "color": "#1E3A5F",
        "icon": "moon",
        "builtin": True,
        "title": "Evening Reflection",
        "tags": ["evening", "reflection"],
        "body": (
            "### Highlights of today\n"
            "1. \n2. \n3. \n\n"
            "### What did I learn today?\n"
            "\n\n"
            "### One thing I could have made even better\n"
            ""
        ),
    },
    {
        "id": "gratitude",
        "name": "Gratitude",
        "category": "Mindfulness",
        "color": "#10B981",
        "icon": "heart",
        "builtin": True,
        "title": "Grateful Today",
        "tags": ["gratitude"],
        "body": (
            "### Three things I appreciate right now\n"
            "- \n- \n- \n\n"
            "### Someone who made my day brighter\n"
            "\n\n"
            "### A small moment worth remembering\n"
            ""
        ),
    },
    {
        "id": "daily-reflection",
        "name": "Daily Reflection",
        "category": "Daily",
        "color": "#44B6FF",
        "icon": "book",
        "builtin": True,
        "title": "Daily Reflection",
        "tags": ["daily"],
        "body": (
            "### How I feel right now\n"
            "\n\n"
            "### Key moments\n"
            "- \n\n"
            "### On my mind\n"
            "\n\n"
            "### Looking ahead to tomorrow\n"
            "- [ ] "
        ),
    },
    {
        "id": "travel-log",
        "name": "Travel Log",
        "category": "Lifestyle",
        "color": "#0EA5E9",
        "icon": "compass",
        "builtin": True,
        "title": "Travel Notes",
        "tags": ["travel"],
        "body": (
            "### Where we went\n"
            "\n\n"
            "### Favorite sight, sound, or meal\n"
            "\n\n"
            "### Notes & recommendations for next time\n"
            "- "
        ),
    },
    {
        "id": "decisions",
        "name": "Decisions",
        "category": "Log",
        "color": "#7C849A",
        "icon": "scale",
        "builtin": True,
        "title": "Decision Log",
        "tags": ["decisions"],
        "body": (
            "### Decision\n"
            "\n\n"
            "### Options considered\n"
            "- \n- \n\n"
            "### Reasoning & expected outcome\n"
            "\n\n"
            "### Review date\n"
            ""
        ),
    },
    {
        "id": "meals",
        "name": "Meals",
        "category": "Log",
        "color": "#14B8A6",
        "icon": "utensils",
        "builtin": True,
        "title": "Meal Log",
        "tags": ["meals", "health"],
        "body": (
            "### Breakfast\n- \n\n"
            "### Lunch\n- \n\n"
            "### Dinner\n- \n\n"
            "### Energy & hydration notes\n"
            ""
        ),
    },
    {
        "id": "daily-standup",
        "name": "Daily Standup",
        "category": "Log",
        "color": "#6366F1",
        "icon": "briefcase",
        "builtin": True,
        "title": "Daily Standup",
        "tags": ["work", "standup"],
        "body": (
            "### Completed yesterday\n"
            "- [x] \n\n"
            "### Focus for today\n"
            "- [ ] \n- [ ] \n\n"
            "### Blockers or notes\n"
            ""
        ),
    },
    {
        "id": "workout",
        "name": "Workout",
        "category": "Health",
        "color": "#EF4444",
        "icon": "activity",
        "builtin": True,
        "title": "Workout Session",
        "tags": ["workout", "fitness"],
        "body": (
            "### Activity & duration\n"
            "\n\n"
            "### Sets / Distance / Effort\n"
            "- \n\n"
            "### How my body felt\n"
            ""
        ),
    },
    {
        "id": "weekly-review",
        "name": "Weekly Review",
        "category": "Reflection",
        "color": "#8B5CF6",
        "icon": "calendar",
        "builtin": True,
        "title": "Weekly Review",
        "tags": ["weekly", "review"],
        "body": (
            "### Biggest wins this week\n"
            "- \n- \n\n"
            "### Challenges & lessons\n"
            "- \n\n"
            "### Priorities for next week\n"
            "- [ ] \n- [ ] \n- [ ] "
        ),
    },
]

DEFAULT_PREFERENCES: Dict[str, Any] = {
    "temp_unit": "F",
    "default_journal": "",
    "font_family": "sans",
    "font_size": "md",
    "reminder_enabled": False,
    "reminder_time": "20:00",
    "daily_word_goal": 250,
    "theme": "system",
}


def _journal_lock():
    return FileLock(str(_diary_path()) + ".lock", timeout=10)


def _read_body(raw):
    line, _, body = raw.partition("\n")
    if line.startswith(_ATTRIBUTES) and line.endswith(" -->"):
        attributes = json.loads(line[len(_ATTRIBUTES):-4])
        return body, attributes
    return raw, {}


def _media_kind(name: str) -> str:
    if _AUDIO_EXT_RE.search(name):
        return "audio"
    if _VIDEO_EXT_RE.search(name):
        return "video"
    if _IMAGE_EXT_RE.search(name):
        return "image"
    return "file"


def _is_sketch_name(name: str) -> bool:
    lower = name.strip().lower()
    return _IMAGE_EXT_RE.search(lower) is not None and (lower.startswith("sketch") or lower.startswith("drawing"))


def _clean_location(value: Any) -> Optional[Dict[str, Any]]:
    if value is None or value == "" or value == {}:
        return None
    if not isinstance(value, dict):
        raise ValueError("Invalid diary location.")
    cleaned: Dict[str, Any] = {}
    for key, limit in (("name", 200), ("city", 120), ("region", 120), ("country", 120)):
        raw = value.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str) or len(raw) > limit:
            raise ValueError(f"Invalid location {key}.")
        text = raw.strip()
        if text:
            cleaned[key] = text
    lat = value.get("lat")
    lng = value.get("lng")
    if lat is not None or lng is not None:
        if isinstance(lat, bool) or isinstance(lng, bool) or not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            raise ValueError("Invalid location coordinates.")
        lat_f, lng_f = float(lat), float(lng)
        if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lng_f <= 180.0):
            raise ValueError("Location coordinates out of range.")
        cleaned["lat"] = round(lat_f, 6)
        cleaned["lng"] = round(lng_f, 6)
    return cleaned or None


def _clean_weather(value: Any) -> Optional[Dict[str, Any]]:
    if value is None or value == "" or value == {}:
        return None
    if not isinstance(value, dict):
        raise ValueError("Invalid diary weather.")
    cleaned: Dict[str, Any] = {}
    for key, limit in (("condition", 80), ("icon", 32)):
        raw = value.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str) or len(raw) > limit:
            raise ValueError(f"Invalid weather {key}.")
        text = raw.strip()
        if text:
            cleaned[key] = text
    temp_c = value.get("temp_c")
    if temp_c is not None and temp_c != "":
        if isinstance(temp_c, bool) or not isinstance(temp_c, (int, float)):
            raise ValueError("Invalid weather temperature.")
        t = float(temp_c)
        if not (-90.0 <= t <= 70.0):
            raise ValueError("Weather temperature out of range.")
        cleaned["temp_c"] = round(t, 1)
    humidity = value.get("humidity")
    if humidity is not None and humidity != "":
        if isinstance(humidity, bool) or not isinstance(humidity, (int, float)):
            raise ValueError("Invalid weather humidity.")
        h = int(round(float(humidity)))
        if not (0 <= h <= 100):
            raise ValueError("Weather humidity out of range.")
        cleaned["humidity"] = h
    return cleaned or None


def _details(value: Any, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Invalid diary attributes.")
    journal = value.get("journal", "")
    tags = value.get("tags", [])
    favorite = value.get("favorite", False)
    media = value.get("media", [])
    if (not isinstance(journal, str) or len(journal) > 256 or not isinstance(favorite, bool)
            or not isinstance(tags, list) or len(tags) > 128
            or any(not isinstance(tag, str) or len(tag) > 128 for tag in tags)
            or not isinstance(media, list) or len(media) > 128):
        raise ValueError("Invalid journal, tags or attachments.")
    captured = value.get("captured_at", "")
    zone = value.get("time_zone", "")
    if not isinstance(captured, str) or len(captured) > 40 or not isinstance(zone, str) or len(zone) > 120:
        raise ValueError("Invalid capture time.")
    if captured:
        _datetime.datetime.fromisoformat(captured.replace("Z", "+00:00"))
    attachments = []
    for row in media:
        if not isinstance(row, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(row.get("id", ""))):
            raise ValueError("Invalid diary attachment ID.")
        name = row.get("name", "")
        if not isinstance(name, str) or not name or len(name) > 256 or any(ord(c) < 32 or c in "/\\" for c in name):
            raise ValueError("Invalid attachment name.")
        transcript = row.get("transcription", "")
        if not isinstance(transcript, str) or len(transcript) > 50000:
            raise ValueError("Invalid attachment transcription.")
        attachments.append({"id": row["id"], "name": name, "transcription": transcript})
    if len({row["id"] for row in attachments}) != len(attachments):
        raise ValueError("Repeated diary attachment.")
    attrs: Dict[str, Any] = {
        "captured_at": captured,
        "time_zone": zone,
        "journal": journal.strip(),
        "tags": list(dict.fromkeys(t.strip() for t in tags if t.strip())),
        "favorite": favorite,
        "media": attachments,
    }
    prev = existing if isinstance(existing, dict) else {}

    # Preserve rich metadata if an older mobile client omits keys during sync.
    if "location" in value:
        loc = _clean_location(value.get("location"))
        if loc:
            attrs["location"] = loc
    elif prev.get("location"):
        loc = _clean_location(prev.get("location"))
        if loc:
            attrs["location"] = loc

    if "weather" in value:
        weather = _clean_weather(value.get("weather"))
        if weather:
            attrs["weather"] = weather
    elif prev.get("weather"):
        weather = _clean_weather(prev.get("weather"))
        if weather:
            attrs["weather"] = weather

    for text_key, limit in (("mood", 64), ("activity", 64), ("template", 128)):
        if text_key in value:
            raw = value.get(text_key)
            if raw is None:
                continue
            if not isinstance(raw, str) or len(raw) > limit:
                raise ValueError(f"Invalid diary {text_key}.")
            cleaned = raw.strip()
            if cleaned:
                attrs[text_key] = cleaned
        elif prev.get(text_key):
            cleaned = str(prev[text_key]).strip()[:limit]
            if cleaned:
                attrs[text_key] = cleaned

    if "steps" in value:
        raw_steps = value.get("steps")
        if raw_steps is not None and raw_steps != "":
            if isinstance(raw_steps, bool) or not isinstance(raw_steps, int) or not (0 <= raw_steps <= 500000):
                raise ValueError("Invalid diary step count.")
            attrs["steps"] = raw_steps
    elif isinstance(prev.get("steps"), int) and not isinstance(prev.get("steps"), bool):
        attrs["steps"] = prev["steps"]

    if "pinned" in value:
        pinned = value.get("pinned")
        if not isinstance(pinned, bool):
            raise ValueError("Invalid pinned flag.")
        if pinned:
            attrs["pinned"] = True
    elif prev.get("pinned") is True:
        attrs["pinned"] = True

    return attrs


def _encode_entry_block(identifier: str, date: str, time_val: str, title: str, body: str, attrs: Optional[Dict[str, Any]]) -> str:
    clean_title = title.replace("\n", " ")
    if attrs is not None:
        encoded = json.dumps(attrs, ensure_ascii=True, sort_keys=True, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e")
        body_part = f"{_ATTRIBUTES}{encoded} -->\n{body}"
    else:
        body_part = body
    return (
        f"<!-- autoyou-diary:{identifier} -->\n"
        f"### {date}\n\n"
        f"#### {time_val}\n\n"
        f"##### {clean_title}\n\n"
        f"{body_part}\n"
        f"<!-- /autoyou-diary:{identifier} -->"
    )


def publish_diary_entry(entry_id: str, markdown: str, base_revision: Optional[str] = None, details: Optional[dict] = None) -> Dict[str, Any]:
    """Upsert one explicitly published phone or web entry without replacing other entries."""
    try:
        identifier = str(uuid.UUID(str(entry_id)))
        text = str(markdown or "").strip()
        if len(text) > 50000:
            raise ValueError("Diary entry exceeds the 50000-character limit.")
        marker = re.escape(str(entry_id))
        pattern = rf"(?s)^<!-- autoyou-diary:{marker} -->\n### (\d{{4}}-\d{{2}}-\d{{2}})\n\n#### ([^\n]+)\n\n##### ([^\n]*)\n\n(.*?)\n<!-- /autoyou-diary:{marker} -->$"
        match = re.fullmatch(pattern, text)
        if not match or text.count("<!-- autoyou-diary:") != 1 or text.count("<!-- /autoyou-diary:") != 1:
            raise ValueError("Invalid diary Markdown entry.")
        _datetime.date.fromisoformat(match[1])
        if len(match[2]) > 200 or len(match[3]) > 200:
            raise ValueError("Invalid diary heading.")
        if match[4].startswith(_ATTRIBUTES):
            # Entry metadata travels as structured fields and is validated independently.
            raise ValueError("Reserved journal marker.")
        # Validate incoming details early before acquiring file lock.
        if details is not None:
            _details(details)
        block_pattern = rf"(?ms)^<!-- autoyou-diary:{re.escape(identifier)} -->.*?^<!-- /autoyou-diary:{re.escape(identifier)} -->$"
        with _journal_lock():
            store = _store()
            existing = store.read() or ""
            previous = re.search(block_pattern, existing, flags=re.IGNORECASE)
            prev_attrs: Dict[str, Any] = {}
            if previous:
                prev_match = re.fullmatch(
                    rf"(?s)^<!-- autoyou-diary:{re.escape(identifier)} -->\n### (\d{{4}}-\d{{2}}-\d{{2}})\n\n#### ([^\n]+)\n\n##### ([^\n]*)\n\n(.*?)\n<!-- /autoyou-diary:{re.escape(identifier)} -->$",
                    previous[0],
                    flags=re.IGNORECASE,
                )
                if prev_match:
                    _, prev_attrs = _read_body(prev_match[4])
            if details is not None:
                attrs = _details(details, existing=prev_attrs)
                if any(not diary_media_store(identifier, row["id"]).exists() for row in attrs["media"]):
                    raise ValueError("An attachment has not finished uploading.")
                encoded = json.dumps(attrs, ensure_ascii=True, sort_keys=True, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e")
                final_text = text[:match.start(4)] + _ATTRIBUTES + encoded + " -->\n" + text[match.start(4):]
            else:
                final_text = text
            # Canonical markers make retries from either mobile platform idempotent.
            final_text = final_text.replace(f"autoyou-diary:{entry_id}", f"autoyou-diary:{identifier}")
            revision = sha256(final_text.encode()).hexdigest()
            held = sha256(previous[0].encode()).hexdigest() if previous else ""
            if base_revision is not None and base_revision != held and revision != held:
                return {"status": "conflict", "revision": held}
            if previous:
                updated = re.sub(block_pattern, lambda _: final_text, existing, count=1, flags=re.IGNORECASE)
            else:
                updated = (existing.rstrip() or "# Diary") + "\n\n" + final_text + "\n"
            if updated != existing:
                store.write(updated)
        return {"status": "success", "entry_id": identifier, "revision": revision, "message": "Diary entry published."}
    except (ValueError, TypeError) as exc:
        return {"status": "error", "message": str(exc)}
    except Exception:
        LOGGER.exception("publish_diary_entry failed")
        return {"status": "error", "message": "Could not publish the diary entry. Your existing journal was preserved."}


def _collect_entry_media_summary(entry_id: str, body: str, attrs: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    seen = set()
    items: List[Dict[str, Any]] = []
    counts = {"total": 0, "photo": 0, "video": 0, "audio": 0, "sketch": 0, "file": 0}
    for row in attrs.get("media") or []:
        mid = str(row.get("id") or "")
        name = str(row.get("name") or "")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        kind = _media_kind(name)
        is_sketch = _is_sketch_name(name)
        items.append({
            "id": mid,
            "name": name,
            "kind": kind,
            "sketch": is_sketch,
            "has_transcription": bool(str(row.get("transcription") or "").strip()),
        })
        counts["total"] += 1
        if is_sketch:
            counts["sketch"] += 1
            counts["photo"] += 1
        elif kind == "image":
            counts["photo"] += 1
        elif kind in counts:
            counts[kind] += 1
    for m in _MEDIA_LINK_RE.finditer(body):
        label, link_entry_id, mid = m.groups()
        if link_entry_id.lower() != entry_id.lower() or mid in seen:
            continue
        seen.add(mid)
        kind = _media_kind(label)
        is_sketch = _is_sketch_name(label)
        items.append({
            "id": mid,
            "name": label,
            "kind": kind,
            "sketch": is_sketch,
            "has_transcription": False,
        })
        counts["total"] += 1
        if is_sketch:
            counts["sketch"] += 1
            counts["photo"] += 1
        elif kind == "image":
            counts["photo"] += 1
        elif kind in counts:
            counts[kind] += 1
    return items, counts


def _word_count(title: str, body: str) -> int:
    combined = f"{title} {body}".strip()
    return len(re.findall(r"\S+", combined)) if combined else 0


_ENTRY_BLOCK_RE = re.compile(
    r"(?ms)^<!-- autoyou-diary:([0-9a-fA-F-]{36}) -->\s*\n"
    r"### (\d{4}-\d{2}-\d{2})\s*\n\n"
    r"#### ([^\n]+)\s*\n\n"
    r"##### ([^\n]*)\s*\n\n"
    r"(.*?)\s*\n"
    r"<!-- /autoyou-diary:\1 -->"
)


def list_diary_entries(order: str = "desc") -> List[Dict[str, Any]]:
    """Return catalog of diary entries from diary.md without full content."""
    with _journal_lock():
        store = _store()
        text = store.read() or ""
    entries = []
    for match in _ENTRY_BLOCK_RE.finditer(text):
        entry_id, date, time_val, title, body = match.groups()
        body, attrs = _read_body(body)
        preview = " ".join(body.strip().split())
        if len(preview) > 120:
            sample = preview[:119].rsplit(" ", 1)[0]
            preview = sample.rstrip() + "…"
        media_summary, media_counts = _collect_entry_media_summary(entry_id, body, attrs)
        clean_title = title.strip() or "Untitled"
        entries.append({
            "id": entry_id,
            "revision": sha256(match[0].encode()).hexdigest(),
            "date": date,
            "time": time_val.strip(),
            "title": clean_title,
            "preview": preview,
            "has_media": bool(media_summary) or "Attachments:" in body or "/media/" in body,
            "journal": attrs.get("journal", ""),
            "tags": attrs.get("tags", []),
            "favorite": attrs.get("favorite", False),
            "pinned": bool(attrs.get("pinned", False)),
            "captured_at": attrs.get("captured_at", ""),
            "time_zone": attrs.get("time_zone", ""),
            "location": attrs.get("location"),
            "weather": attrs.get("weather"),
            "mood": attrs.get("mood", ""),
            "activity": attrs.get("activity", ""),
            "steps": attrs.get("steps"),
            "template": attrs.get("template", ""),
            "word_count": _word_count(clean_title if clean_title != "Untitled" else "", body),
            "has_checklist": bool(_CHECKLIST_RE.search(body)),
            "media": media_summary,
            "media_counts": media_counts,
        })
    reverse = (str(order or "desc").strip().lower() != "asc")
    entries.sort(key=lambda e: (e["date"], e["time"], e["id"]), reverse=reverse)
    return entries


def get_diary_entry(entry_id: str) -> Optional[Dict[str, Any]]:
    """Return full diary entry by id."""
    try:
        identifier = str(uuid.UUID(str(entry_id)))
    except (ValueError, TypeError):
        return None
    with _journal_lock():
        store = _store()
        text = store.read() or ""
    marker = re.escape(identifier)
    pattern = re.compile(
        rf"(?ms)^<!-- autoyou-diary:{marker} -->\s*\n"
        r"### (\d{4}-\d{2}-\d{2})\s*\n\n"
        r"#### ([^\n]+)\s*\n\n"
        r"##### ([^\n]*)\s*\n\n"
        r"(.*?)\s*\n"
        rf"<!-- /autoyou-diary:{marker} -->"
    )
    match = pattern.search(text)
    if not match:
        return None
    date, time_val, title, body = match.groups()
    body, attrs = _read_body(body)
    clean_title = title.strip() or "Untitled"
    clean_body = body.strip()
    return {
        "id": identifier,
        "revision": sha256(match[0].encode()).hexdigest(),
        "date": date,
        "time": time_val.strip(),
        "title": clean_title,
        "body": clean_body,
        "markdown": match.group(0),
        "journal": attrs.get("journal", ""),
        "tags": attrs.get("tags", []),
        "favorite": attrs.get("favorite", False),
        "media": attrs.get("media", []),
        "captured_at": attrs.get("captured_at", ""),
        "time_zone": attrs.get("time_zone", ""),
        "pinned": bool(attrs.get("pinned", False)),
        "location": attrs.get("location"),
        "weather": attrs.get("weather"),
        "mood": attrs.get("mood", ""),
        "activity": attrs.get("activity", ""),
        "steps": attrs.get("steps"),
        "template": attrs.get("template", ""),
        "word_count": _word_count(clean_title if clean_title != "Untitled" else "", clean_body),
        "has_checklist": bool(_CHECKLIST_RE.search(clean_body)),
    }


def _encryption_enabled() -> bool:
    """Independent-key encryption is OFF by default; opt in via env/config."""
    return str(os.getenv("AUTOYOU_DIARY_ENCRYPT", "")).strip().lower() in {"1", "true", "yes", "on"}


def _is_encrypted() -> bool:
    return _encryption_enabled() or (_diary_path().parent / ".independent-key").is_file()


def _diary_path() -> Path:
    from shared.platform_runtime import get_mutable_data_dir

    from shared.content_agent_upgrade import upgrade_private_documents

    root = get_mutable_data_dir("AutoYou", anchor=__file__)
    upgrade_private_documents(root)
    base = root / _DIARY_DIRNAME
    base.mkdir(parents=True, exist_ok=True)
    return base / _DIARY_FILENAME


def _store():
    from shared.secure_data_store import SecureDataStore

    return SecureDataStore(
        _diary_path(),
        encrypt=_is_encrypted(),
        keystore_service=_KEYSTORE_SERVICE,
        keystore_username="diary",
    )


def _json_store(filename: str):
    from shared.secure_data_store import SecureDataStore

    return SecureDataStore(
        _diary_path().parent / filename,
        encrypt=_is_encrypted(),
        keystore_service=_KEYSTORE_SERVICE,
        keystore_username="diary",
    )


def _read_json_store(filename: str, default: Any) -> Any:
    raw = _json_store(filename).read()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def _write_json_store(filename: str, payload: Any) -> None:
    _json_store(filename).write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))


def diary_media_store(entry_id: str, media_id: str):
    """Apply the Diary data protection policy to phone attachments."""
    identifier = str(uuid.UUID(entry_id))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", media_id):
        raise ValueError("Invalid media id.")
    from shared.secure_data_store import SecureDataStore
    return SecureDataStore(
        _diary_path().parent / "media" / f"{identifier}-{media_id}.json",
        encrypt=_is_encrypted(),
        keystore_service=_KEYSTORE_SERVICE,
        keystore_username="diary",
    )


def delete_diary_media(entry_id: str, media_id: str) -> Dict[str, Any]:
    """Delete one attachment file belonging to an entry."""
    identifier = str(uuid.UUID(entry_id))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", media_id):
        raise ValueError("Invalid media id.")
    with _journal_lock():
        path = _diary_path().parent / "media" / f"{identifier}-{media_id}.json"
        path.unlink(missing_ok=True)
    return {"status": "success", "entry_id": identifier, "media_id": media_id}


def delete_diary_entry(entry_id: str, base_revision: str) -> Dict[str, Any]:
    """Delete exactly the revision the owner reviewed."""
    identifier = str(uuid.UUID(entry_id))
    marker = re.escape(identifier)
    pattern = rf"(?ms)^<!-- autoyou-diary:{marker} -->.*?^<!-- /autoyou-diary:{marker} -->$"
    with _journal_lock():
        store = _store()
        current = store.read() or ""
        found = re.search(pattern, current)
        if not found:
            return {"status": "success", "entry_id": identifier}
        if sha256(found[0].encode()).hexdigest() != base_revision:
            return {"status": "conflict"}
        store.write(current[:found.start()] + current[found.end():])
        for media in (_diary_path().parent / "media").glob(identifier + "-*.json"):
            media.unlink(missing_ok=True)
    return {"status": "success", "entry_id": identifier}


def _color_for_journal(name: str) -> str:
    if not name or name.lower() == "daily journal":
        return "#44B6FF"
    idx = int(sha256(name.lower().encode("utf-8")).hexdigest()[:8], 16) % len(_JOURNAL_PALETTE)
    return _JOURNAL_PALETTE[idx]


def list_journals(entries: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Return configured journals merged with any journal names synced from mobile entries."""
    with _journal_lock():
        saved = _read_json_store(_JOURNALS_FILENAME, [])
    if entries is None:
        entries = list_diary_entries()

    configured: Dict[str, Dict[str, Any]] = {}
    if isinstance(saved, list):
        for row in saved:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            configured[name.lower()] = {
                "name": name,
                "color": row.get("color") if isinstance(row.get("color"), str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", row["color"]) else _color_for_journal(name),
                "icon": str(row.get("icon") or "book")[:32],
                "description": str(row.get("description") or "")[:300],
                "streak_included": bool(row.get("streak_included", True)),
            }

    counts: Dict[str, int] = {}
    latest_dates: Dict[str, str] = {}
    discovered_names: Dict[str, str] = {}
    uncategorized_count = 0
    uncategorized_latest = ""
    for row in entries:
        jname = str(row.get("journal") or "").strip()
        if not jname:
            uncategorized_count += 1
            if row["date"] > uncategorized_latest:
                uncategorized_latest = row["date"]
            continue
        key = jname.lower()
        discovered_names.setdefault(key, jname)
        counts[key] = counts.get(key, 0) + 1
        if row["date"] > latest_dates.get(key, ""):
            latest_dates[key] = row["date"]

    result: List[Dict[str, Any]] = []
    # Always include "Daily Journal" as the default primary journal if none configured.
    if "daily journal" not in configured:
        configured["daily journal"] = {
            "name": "Daily Journal",
            "color": "#44B6FF",
            "icon": "book",
            "description": "Everyday memories, thoughts, and moments.",
            "streak_included": True,
        }

    for key, name in discovered_names.items():
        if key not in configured:
            configured[key] = {
                "name": name,
                "color": _color_for_journal(name),
                "icon": "book",
                "description": "",
                "streak_included": True,
            }

    for key, meta in configured.items():
        count = counts.get(key, 0)
        latest = latest_dates.get(key, "")
        if key == "daily journal":
            count += uncategorized_count
            if uncategorized_latest > latest:
                latest = uncategorized_latest
        result.append({
            **meta,
            "count": count,
            "latest_date": latest,
            "is_default": key == "daily journal",
        })

    result.sort(key=lambda j: (0 if j["is_default"] else 1, -j["count"], j["name"].lower()))
    return result


def upsert_journal(
    name: str,
    *,
    color: str = "",
    icon: str = "book",
    description: str = "",
    streak_included: bool = True,
    rename_from: Optional[str] = None,
) -> Dict[str, Any]:
    clean_name = str(name or "").strip()
    if not clean_name or len(clean_name) > 256:
        raise ValueError("Journal name must be 1 to 256 characters.")
    clean_color = str(color or "").strip() or _color_for_journal(clean_name)
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", clean_color):
        raise ValueError("Invalid journal color.")
    clean_icon = str(icon or "book").strip()[:32] or "book"
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", clean_icon):
        raise ValueError("Invalid journal icon.")
    clean_desc = str(description or "").strip()[:300]
    if not isinstance(streak_included, bool):
        raise ValueError("Invalid streak setting.")

    old_name = str(rename_from or "").strip()
    with _journal_lock():
        saved = _read_json_store(_JOURNALS_FILENAME, [])
        if not isinstance(saved, list):
            saved = []
        filtered = [
            row for row in saved
            if isinstance(row, dict)
            and str(row.get("name") or "").strip().lower() not in {clean_name.lower(), old_name.lower()}
        ]
        record = {
            "name": clean_name,
            "color": clean_color,
            "icon": clean_icon,
            "description": clean_desc,
            "streak_included": streak_included,
        }
        filtered.append(record)
        _write_json_store(_JOURNALS_FILENAME, filtered)

        if old_name and old_name.lower() != clean_name.lower():
            _rewrite_entry_journals_locked(old_name, clean_name)

    return record


def delete_journal(name: str, move_entries_to: str = "") -> Dict[str, Any]:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Journal name is required.")
    target = str(move_entries_to or "").strip()[:256]
    with _journal_lock():
        saved = _read_json_store(_JOURNALS_FILENAME, [])
        if isinstance(saved, list):
            filtered = [
                row for row in saved
                if isinstance(row, dict) and str(row.get("name") or "").strip().lower() != clean_name.lower()
            ]
            _write_json_store(_JOURNALS_FILENAME, filtered)
        _rewrite_entry_journals_locked(clean_name, target)
    return {"status": "success", "deleted": clean_name, "moved_to": target}


def _rewrite_entry_journals_locked(from_journal: str, to_journal: str) -> None:
    store = _store()
    text = store.read() or ""
    if not text:
        return
    changed = False

    def _replace(match: re.Match) -> str:
        nonlocal changed
        entry_id, date, time_val, title, raw_body = match.groups()
        body, attrs = _read_body(raw_body)
        current = str(attrs.get("journal") or "").strip()
        if current.lower() != from_journal.lower():
            return match.group(0)
        new_attrs = dict(attrs)
        new_attrs["journal"] = to_journal
        changed = True
        return _encode_entry_block(entry_id, date, time_val.strip(), title.strip(), body, new_attrs)

    updated = _ENTRY_BLOCK_RE.sub(_replace, text)
    if changed and updated != text:
        store.write(updated)


def list_templates() -> List[Dict[str, Any]]:
    with _journal_lock():
        custom = _read_json_store(_TEMPLATES_FILENAME, [])
    items = [dict(t) for t in BUILTIN_TEMPLATES]
    if isinstance(custom, list):
        for row in custom:
            if isinstance(row, dict) and row.get("id") and row.get("name"):
                items.append({**row, "builtin": False})
    return items


def save_template(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Invalid template.")
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ValueError("Template name must be 1 to 80 characters.")
    category = str(payload.get("category") or "Custom").strip()[:48] or "Custom"
    color = str(payload.get("color") or "#44B6FF").strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise ValueError("Invalid template color.")
    icon = str(payload.get("icon") or "file-text").strip()[:32] or "file-text"
    title = str(payload.get("title") or name).strip()[:200]
    body = str(payload.get("body") or "")
    if len(body) > 20000:
        raise ValueError("Template body is too long.")
    raw_tags = payload.get("tags") or []
    if not isinstance(raw_tags, list) or len(raw_tags) > 32:
        raise ValueError("Invalid template tags.")
    tags = list(dict.fromkeys(str(t).strip()[:64] for t in raw_tags if isinstance(t, str) and t.strip()))
    tid = str(payload.get("id") or "").strip()
    if not tid or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tid):
        tid = "custom-" + uuid.uuid4().hex[:12]
    if any(t["id"] == tid for t in BUILTIN_TEMPLATES):
        tid = "custom-" + uuid.uuid4().hex[:12]
    record = {
        "id": tid,
        "name": name,
        "category": category,
        "color": color,
        "icon": icon,
        "title": title,
        "body": body,
        "tags": tags,
        "builtin": False,
    }
    with _journal_lock():
        custom = _read_json_store(_TEMPLATES_FILENAME, [])
        if not isinstance(custom, list):
            custom = []
        custom = [row for row in custom if isinstance(row, dict) and row.get("id") != tid]
        custom.append(record)
        _write_json_store(_TEMPLATES_FILENAME, custom)
    return record


def delete_template(template_id: str) -> Dict[str, Any]:
    tid = str(template_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tid):
        raise ValueError("Invalid template ID.")
    if any(t["id"] == tid for t in BUILTIN_TEMPLATES):
        raise ValueError("Built-in templates cannot be deleted.")
    with _journal_lock():
        custom = _read_json_store(_TEMPLATES_FILENAME, [])
        if isinstance(custom, list):
            custom = [row for row in custom if isinstance(row, dict) and row.get("id") != tid]
            _write_json_store(_TEMPLATES_FILENAME, custom)
    return {"status": "success", "deleted": tid}


def get_preferences() -> Dict[str, Any]:
    with _journal_lock():
        saved = _read_json_store(_PREFERENCES_FILENAME, {})
    prefs = dict(DEFAULT_PREFERENCES)
    if isinstance(saved, dict):
        for key in DEFAULT_PREFERENCES:
            if key in saved:
                prefs[key] = saved[key]
    return prefs


def save_preferences(updates: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(updates, dict):
        raise ValueError("Invalid preferences.")
    prefs = get_preferences()
    if "temp_unit" in updates:
        unit = str(updates["temp_unit"]).upper()
        if unit not in {"F", "C"}:
            raise ValueError("Temperature unit must be F or C.")
        prefs["temp_unit"] = unit
    if "default_journal" in updates:
        dj = str(updates["default_journal"] or "").strip()
        if len(dj) > 256:
            raise ValueError("Invalid default journal.")
        prefs["default_journal"] = dj
    if "font_family" in updates:
        ff = str(updates["font_family"])
        if ff not in {"sans", "serif", "mono"}:
            raise ValueError("Invalid font family.")
        prefs["font_family"] = ff
    if "font_size" in updates:
        fs = str(updates["font_size"])
        if fs not in {"sm", "md", "lg"}:
            raise ValueError("Invalid font size.")
        prefs["font_size"] = fs
    if "reminder_enabled" in updates:
        if not isinstance(updates["reminder_enabled"], bool):
            raise ValueError("Invalid reminder flag.")
        prefs["reminder_enabled"] = updates["reminder_enabled"]
    if "reminder_time" in updates:
        rt = str(updates["reminder_time"] or "").strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", rt):
            raise ValueError("Invalid reminder time.")
        prefs["reminder_time"] = rt
    if "daily_word_goal" in updates:
        goal = updates["daily_word_goal"]
        if isinstance(goal, bool) or not isinstance(goal, int) or not (0 <= goal <= 50000):
            raise ValueError("Invalid daily word goal.")
        prefs["daily_word_goal"] = goal
    if "theme" in updates:
        theme = str(updates["theme"])
        if theme not in {"system", "light", "dark"}:
            raise ValueError("Invalid theme.")
        prefs["theme"] = theme
    with _journal_lock():
        _write_json_store(_PREFERENCES_FILENAME, prefs)
    return prefs


def compute_diary_insights(
    entries: List[Dict[str, Any]],
    journals: Optional[List[Dict[str, Any]]] = None,
    today: Optional[_datetime.date] = None,
) -> Dict[str, Any]:
    """Compute Day One-style streaks, heatmap counts, On This Day, tags, places and moods."""
    now_day = today or _datetime.date.today()
    excluded_journals = {
        str(j.get("name") or "").strip().lower()
        for j in (journals or [])
        if j.get("streak_included") is False
    }
    daily_counts: Dict[str, int] = {}
    streak_days = set()
    tag_counts: Dict[str, int] = {}
    place_counts: Dict[str, int] = {}
    mood_counts: Dict[str, int] = {}
    media_totals = {"total": 0, "photo": 0, "video": 0, "audio": 0, "sketch": 0, "file": 0}
    total_words = 0
    this_week = 0
    this_month = 0
    this_year = 0
    week_start = now_day - _datetime.timedelta(days=6)
    on_this_day: List[Dict[str, Any]] = []

    for row in entries:
        date_str = str(row.get("date") or "")
        try:
            d = _datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        daily_counts[date_str] = daily_counts.get(date_str, 0) + 1
        jname = str(row.get("journal") or "").strip().lower() or "daily journal"
        if jname not in excluded_journals:
            streak_days.add(d)
        total_words += int(row.get("word_count") or 0)
        mc = row.get("media_counts") or {}
        for k in media_totals:
            media_totals[k] += int(mc.get(k) or 0)
        if week_start <= d <= now_day:
            this_week += 1
        if d.year == now_day.year and d.month == now_day.month:
            this_month += 1
        if d.year == now_day.year:
            this_year += 1
        for tag in row.get("tags") or []:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        loc = row.get("location")
        if isinstance(loc, dict):
            place = loc.get("name") or loc.get("city") or loc.get("country")
            if place:
                place_counts[place] = place_counts.get(place, 0) + 1
        mood = str(row.get("mood") or "").strip()
        if mood:
            mood_counts[mood] = mood_counts.get(mood, 0) + 1
        if d.month == now_day.month and d.day == now_day.day and d.year < now_day.year:
            on_this_day.append({
                "id": row["id"],
                "date": date_str,
                "years_ago": now_day.year - d.year,
                "title": row["title"],
                "preview": row["preview"],
                "journal": row.get("journal", ""),
            })

    sorted_days = sorted(streak_days)
    longest_streak = 0
    run = 0
    prev_day: Optional[_datetime.date] = None
    for d in sorted_days:
        if prev_day is not None and (d - prev_day).days == 1:
            run += 1
        else:
            run = 1
        if run > longest_streak:
            longest_streak = run
        prev_day = d

    journaled_today = now_day in streak_days
    current_streak = 0
    cursor = now_day if journaled_today else (now_day - _datetime.timedelta(days=1))
    while cursor in streak_days:
        current_streak += 1
        cursor -= _datetime.timedelta(days=1)

    on_this_day.sort(key=lambda item: item["years_ago"])
    return {
        "today": now_day.isoformat(),
        "journaled_today": journaled_today,
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "active_days": len(daily_counts),
        "total_entries": len(entries),
        "total_words": total_words,
        "total_media": media_totals["total"],
        "media_counts": media_totals,
        "entries_this_week": this_week,
        "entries_this_month": this_month,
        "entries_this_year": this_year,
        "daily_counts": daily_counts,
        "tag_counts": dict(sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))),
        "place_counts": dict(sorted(place_counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))),
        "mood_counts": dict(sorted(mood_counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))),
        "on_this_day_count": len(on_this_day),
        "on_this_day": on_this_day[:20],
    }


def get_diary_sync_state() -> Dict[str, Any]:
    """Return a compact fingerprint so web UI refreshes when iOS/Android syncs changes."""
    with _journal_lock():
        diary_text = _store().read() or ""
        journals_text = _json_store(_JOURNALS_FILENAME).read() or ""
        templates_text = _json_store(_TEMPLATES_FILENAME).read() or ""
    digest = sha256((diary_text + "\n---\n" + journals_text + "\n---\n" + templates_text).encode("utf-8")).hexdigest()
    count = len(_ENTRY_BLOCK_RE.findall(diary_text))
    return {
        "catalog_hash": digest,
        "total": count,
        "encrypted": _is_encrypted(),
    }


def export_diary(fmt: str = "dayone_json", journal: str = "") -> tuple[str, str, str]:
    """Export journal entries as Day One JSON, Markdown, or Plain Text."""
    filter_journal = str(journal or "").strip().lower()
    catalog = list_diary_entries(order="desc")
    full_entries: List[Dict[str, Any]] = []
    for row in catalog:
        entry_j = str(row.get("journal") or "").strip().lower()
        if filter_journal:
            if filter_journal == "daily journal":
                if entry_j not in {"", "daily journal"}:
                    continue
            elif entry_j != filter_journal:
                continue
        full = get_diary_entry(row["id"])
        if full:
            full_entries.append(full)

    stamp = _datetime.date.today().isoformat()
    slug = re.sub(r"[^a-z0-9]+", "-", filter_journal).strip("-") if filter_journal else "all-journals"
    slug = slug or "diary"

    kind = str(fmt or "dayone_json").strip().lower()
    if kind in {"dayone_json", "json"}:
        dayone_entries = []
        for entry in full_entries:
            text_block = f"# {entry['title']}\n\n{entry['body']}" if entry["title"] and entry["title"] != "Untitled" else entry["body"]
            item: Dict[str, Any] = {
                "uuid": entry["id"].replace("-", "").upper(),
                "id": entry["id"],
                "creationDate": entry.get("captured_at") or f"{entry['date']}T12:00:00Z",
                "timeZone": entry.get("time_zone") or "UTC",
                "date": entry["date"],
                "time": entry["time"],
                "title": entry["title"],
                "text": text_block,
                "journal": entry.get("journal") or "Daily Journal",
                "tags": entry.get("tags") or [],
                "starred": bool(entry.get("favorite")),
                "isPinned": bool(entry.get("pinned")),
            }
            if entry.get("location"):
                item["location"] = entry["location"]
            if entry.get("weather"):
                item["weather"] = entry["weather"]
            if entry.get("mood"):
                item["mood"] = entry["mood"]
            if entry.get("activity"):
                item["activity"] = entry["activity"]
            if entry.get("steps") is not None:
                item["steps"] = entry["steps"]
            if entry.get("media"):
                item["media"] = entry["media"]
            dayone_entries.append(item)
        payload = {"metadata": {"version": "1.0", "generator": "AutoYou Diary"}, "entries": dayone_entries}
        return json.dumps(payload, indent=2, ensure_ascii=False), "application/json; charset=utf-8", f"autoyou-diary-{slug}-{stamp}.json"

    if kind == "markdown":
        lines = [f"# AutoYou Diary ({journal.strip() if journal.strip() else 'All Journals'})", ""]
        for entry in full_entries:
            lines.append(f"## {entry['date']} — {entry['title']}")
            meta_bits = [entry["time"]]
            if entry.get("journal"):
                meta_bits.append(f"Journal: {entry['journal']}")
            if entry.get("location") and entry["location"].get("name"):
                meta_bits.append(f"Location: {entry['location']['name']}")
            if entry.get("mood"):
                meta_bits.append(f"Mood: {entry['mood']}")
            if entry.get("tags"):
                meta_bits.append("Tags: " + ", ".join(f"#{t}" for t in entry["tags"]))
            lines.append("*" + " · ".join(bit for bit in meta_bits if bit) + "*")
            lines.append("")
            lines.append(entry["body"])
            if entry.get("media"):
                lines.append("")
                lines.append("Attachments: " + ", ".join(m["name"] for m in entry["media"]))
            lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines), "text/markdown; charset=utf-8", f"autoyou-diary-{slug}-{stamp}.md"

    if kind in {"text", "txt"}:
        lines = [f"AutoYou Diary — {journal.strip() if journal.strip() else 'All Journals'}", "=" * 60, ""]
        for entry in full_entries:
            lines.append(f"{entry['date']} | {entry['time']}")
            lines.append(entry["title"])
            if entry.get("journal"):
                lines.append(f"Journal: {entry['journal']}")
            if entry.get("tags"):
                lines.append("Tags: " + ", ".join(f"#{t}" for t in entry["tags"]))
            lines.append("")
            lines.append(entry["body"])
            lines.append("")
            lines.append("-" * 60)
            lines.append("")
        return "\n".join(lines), "text/plain; charset=utf-8", f"autoyou-diary-{slug}-{stamp}.txt"

    raise ValueError("Unsupported export format.")


def create_diary_agent(model_config: Any) -> Agent:
    return Agent(name=AGENT_NAME, model=model_config, description=AGENT_DESCRIPTION,
                 instruction=AGENT_INSTRUCTION, tools=[list_diary_entries, get_diary_entry, publish_diary_entry])
