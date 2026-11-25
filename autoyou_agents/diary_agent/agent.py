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
_KEYSTORE_SERVICE = "AutoYou-DiaryData"


def _journal_lock():
    return FileLock(str(_diary_path()) + ".lock", timeout=10)


_ATTRIBUTES = "<!-- autoyou-entry-details "


def _read_body(raw):
    line, _, body = raw.partition("\n")
    if line.startswith(_ATTRIBUTES) and line.endswith(" -->"):
        attributes = json.loads(line[len(_ATTRIBUTES):-4])
        return body, attributes
    return raw, {}


def _details(value):
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
    return {"captured_at": captured, "time_zone": zone, "journal": journal.strip(), "tags": list(dict.fromkeys(t.strip() for t in tags if t.strip())),
            "favorite": favorite, "media": attachments}


def publish_diary_entry(entry_id: str, markdown: str, base_revision: Optional[str] = None, details: Optional[dict] = None) -> Dict[str, Any]:
    """Upsert one explicitly published phone entry without replacing other entries."""
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
        if details is not None:
            attrs = _details(details)
            if match[4].startswith(_ATTRIBUTES):
                raise ValueError("Reserved journal marker.")
            encoded = json.dumps(attrs, ensure_ascii=True, sort_keys=True, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e")
            text = text[:match.start(4)] + _ATTRIBUTES + encoded + " -->\n" + text[match.start(4):]
        elif match[4].startswith(_ATTRIBUTES):
            # Entry metadata travels as structured fields and is validated independently.
            raise ValueError("Reserved journal marker.")
        # Canonical markers make retries from either mobile platform idempotent.
        text = text.replace(f"autoyou-diary:{entry_id}", f"autoyou-diary:{identifier}")
        block_pattern = rf"(?ms)^<!-- autoyou-diary:{re.escape(identifier)} -->.*?^<!-- /autoyou-diary:{re.escape(identifier)} -->$"
        with _journal_lock():
            if details is not None and any(not diary_media_store(identifier, row["id"]).exists() for row in attrs["media"]):
                raise ValueError("An attachment has not finished uploading.")
            store = _store()
            existing = store.read() or ""
            previous = re.search(block_pattern, existing, flags=re.IGNORECASE)
            revision = sha256(text.encode()).hexdigest()
            held = sha256(previous[0].encode()).hexdigest() if previous else ""
            if base_revision is not None and base_revision != held and revision != held:
                return {"status": "conflict", "revision": held}
            if previous:
                updated = re.sub(block_pattern, lambda _: text, existing, count=1, flags=re.IGNORECASE)
            else:
                updated = (existing.rstrip() or "# Diary") + "\n\n" + text + "\n"
            if updated != existing:
                store.write(updated)
        return {"status": "success", "entry_id": identifier, "revision": revision, "message": "Diary entry published."}
    except (ValueError, TypeError) as exc:
        return {"status": "error", "message": str(exc)}
    except Exception:
        LOGGER.exception("publish_diary_entry failed")
        return {"status": "error", "message": "Could not publish the diary entry. Your existing journal was preserved."}


def list_diary_entries(order: str = "desc") -> List[Dict[str, Any]]:
    """Return catalog of diary entries from diary.md without full content."""
    with _journal_lock():
        store = _store()
        text = store.read() or ""
    pattern = re.compile(
        r"(?ms)^<!-- autoyou-diary:([0-9a-fA-F-]{36}) -->\s*\n"
        r"### (\d{4}-\d{2}-\d{2})\s*\n\n"
        r"#### ([^\n]+)\s*\n\n"
        r"##### ([^\n]*)\s*\n\n"
        r"(.*?)\s*\n"
        r"<!-- /autoyou-diary:\1 -->"
    )
    entries = []
    for match in pattern.finditer(text):
        entry_id, date, time_val, title, body = match.groups()
        body, attrs = _read_body(body)
        preview = " ".join(body.strip().split())
        if len(preview) > 120:
            sample = preview[:119].rsplit(" ", 1)[0]
            preview = sample.rstrip() + "…"
        entries.append({
            "id": entry_id,
            "revision": sha256(match[0].encode()).hexdigest(),
            "date": date,
            "time": time_val.strip(),
            "title": title.strip() or "Untitled",
            "preview": preview,
            "has_media": bool(attrs.get("media")) or "Attachments:" in body or "/media/" in body,
            "journal": attrs.get("journal", ""), "tags": attrs.get("tags", []), "favorite": attrs.get("favorite", False),
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
    return {
        "id": identifier,
        "revision": sha256(match[0].encode()).hexdigest(),
        "date": date,
        "time": time_val.strip(),
        "title": title.strip() or "Untitled",
        "body": body.strip(),
        "markdown": match.group(0),
        "journal": attrs.get("journal", ""), "tags": attrs.get("tags", []),
        "favorite": attrs.get("favorite", False), "media": attrs.get("media", []),
        "captured_at": attrs.get("captured_at", ""), "time_zone": attrs.get("time_zone", ""),
    }


def _encryption_enabled() -> bool:
    """Independent-key encryption is OFF by default; opt in via env/config."""
    return str(os.getenv("AUTOYOU_DIARY_ENCRYPT", "")).strip().lower() in {"1", "true", "yes", "on"}


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
        encrypt=_encryption_enabled() or (_diary_path().parent / ".independent-key").is_file(),
        keystore_service=_KEYSTORE_SERVICE,
        keystore_username="diary",
    )


def diary_media_store(entry_id: str, media_id: str):
    """Apply the Diary data protection policy to phone attachments."""
    identifier = str(uuid.UUID(entry_id))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", media_id):
        raise ValueError("Invalid media id.")
    from shared.secure_data_store import SecureDataStore
    return SecureDataStore(
        _diary_path().parent / "media" / f"{identifier}-{media_id}.json",
        encrypt=_encryption_enabled() or (_diary_path().parent / ".independent-key").is_file(), keystore_service=_KEYSTORE_SERVICE,
        keystore_username="diary",
    )


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


def create_diary_agent(model_config: Any) -> Agent:
    return Agent(name=AGENT_NAME, model=model_config, description=AGENT_DESCRIPTION,
                 instruction=AGENT_INSTRUCTION, tools=[list_diary_entries, get_diary_entry, publish_diary_entry])
