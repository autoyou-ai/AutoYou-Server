"""Removing content from My Content without touching anything AutoYou does not own.

The catalog is an index. For most agents the agent's own store is the truth, so
removing from My Content must go through that agent (or send the person there),
never edit the index alone: an index-only removal comes back on the next sync and
leaves the agent showing something My Content hides. Three outcomes, decided per item:

``catalog``  My Content owns the item (files added here, records from agents made on
             a phone). It is removed, and a stored file goes with it only when no other
             item still uses it and it lives in a managed folder.
``agent``    An agent owns it and My Content can ask that agent to remove it. The agent
             does the removal and tombstones its own catalog row.
``open``     An agent owns it and has no safe remote removal. The screen sends the
             person to that agent's app.

A file outside AutoYou's managed folders is never deleted, by any path through here.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from shared import content_agents, content_files
from shared.content_library import ContentLibrary

LOGGER = logging.getLogger(__name__)


class RemovalError(Exception):
    def __init__(self, message: str, status: int = 409, **extra):
        super().__init__(message)
        self.status, self.extra = status, extra


# --- adapters: ask an agent to remove one of its own items --------------------------------------
def _number(row) -> int:
    try:
        return int(row["source_id"])
    except (TypeError, ValueError):
        raise RemovalError("Open the agent to remove this item.", status=409)


def _notes(row):
    from autoyou_agents.notes_agent.notes_tool import NotesTool
    if not NotesTool().delete_note(_number(row)):
        raise LookupError("Note not found")


def _pages(row):
    from page_feed_db import PageFeedDB
    from shared.platform_runtime import get_config_dir
    config_dir = get_config_dir("AutoYou", anchor=Path(__file__).resolve().parents[1] / "server.py")
    db = PageFeedDB(db_path=os.path.join(str(config_dir), "page_feed.db"))
    result = db.delete_item_and_cleanup(_number(row), uploads_dir=os.path.join(str(config_dir), "uploads"))
    if not result.get("deleted"):
        raise LookupError("Page not found")


def _media(row):
    from autoyou_agents.media_generation_agent.media_generation_tool import delete_history_item
    if not delete_history_item(_number(row)):
        raise LookupError("Media not found")


def _voice(row):
    from autoyou_agents.voice_training_agent.agent import delete_voice_transcript
    source_id = str(row["source_id"])
    if ":" not in source_id:
        raise RemovalError("Open the agent to remove this item.", status=409)
    result = delete_voice_transcript(source_id.split(":", 1)[1])
    if result.get("status") != "success":
        raise LookupError("Recording not found")


def _tv(row):
    from autoyou_agents.media_agent.website.backend.store import Store
    # The TV library marks a title deleted; it never deletes the media folder behind it.
    Store().update_item(str(row["source_id"]), {"status": "deleted"})


ADAPTERS = {"notes_agent": _notes, "page_agent": _pages, "media_generation_agent": _media,
            "voice_training_agent": _voice, "media_agent": _tv}


def plan(row) -> dict:
    """How removing this item would work. `row` is a catalog row or a public item."""
    source = row["source"]
    if not content_agents.owns_data(source):
        return {"mode": "catalog", "owner": source}
    if source in ADAPTERS and not _is_attachment(row):
        return {"mode": "agent", "owner": source}
    return {"mode": "open", "owner": source}


def _is_attachment(row) -> bool:
    return str(row["source_id"]).startswith("attachment:")


# --- catalog-owned removal ----------------------------------------------------------------------
def _owned(path) -> bool:
    from shared.content_storage import owned
    try:
        return bool(path) and Path(path).exists() and owned(path)
    except OSError:
        return False


def _still_used(db, column: str, path: str) -> bool:
    return bool(db.execute(f"SELECT 1 FROM content WHERE deleted=0 AND {column}=? LIMIT 1", (path,)).fetchone())


def _remove_files(library: ContentLibrary, candidates: list[tuple[str, str]]) -> tuple[int, list[str]]:
    """Delete stored files nothing else references. Returns (files removed, warnings)."""
    removed, warnings = 0, []
    with library.connect() as db:
        doomed = []
        for path, managed in candidates:
            for target, columns in ((path, ("path", "managed_path")), (managed, ("managed_path", "path"))):
                if target and _owned(target) and not any(_still_used(db, column, target) for column in columns) and target not in doomed:
                    doomed.append(target)
    for target in doomed:
        try:
            content_files.discard(target)
            removed += 1
        except Exception:
            LOGGER.warning("Could not remove a stored content file", exc_info=True)
            warnings.append("A stored file could not be removed and was left in place.")
    return removed, warnings


def _remove_catalog_item(library: ContentLibrary, row, expected_revision: int) -> dict:
    with library.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT * FROM content WHERE id=? AND deleted=0", (row["id"],)).fetchone()
        if not current:
            raise LookupError("Content not found")
        if current["revision"] != expected_revision:
            raise FileExistsError("This item changed on another device. Refresh before removing it.")
        family = [current, *db.execute("SELECT * FROM content WHERE parent_id=? AND deleted=0", (current["id"],)).fetchall()]
        for member in family:
            db.execute("UPDATE content SET deleted=1,revision=?,updated=? WHERE id=?",
                       (library.tick(db), time.time(), member["id"]))
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_content'").fetchone():
            db.execute("DELETE FROM native_content WHERE source=? AND source_id=?", (current["source"], current["source_id"]))
        candidates = [(member["path"], member["managed_path"]) for member in family]
    files, warnings = _remove_files(library, candidates)
    return {"removed": [member["id"] for member in family], "files": files, "warnings": warnings}


def _remove_through_agent(library: ContentLibrary, row, expected_revision: int) -> dict:
    if row["revision"] != expected_revision:
        raise FileExistsError("This item changed on another device. Refresh before removing it.")
    try:
        ADAPTERS[row["source"]](row)
    except RemovalError:
        raise
    except LookupError:
        pass  # The agent no longer has it; the catalog entry is stale, so drop it below.
    except Exception:
        LOGGER.warning("An agent could not remove %s content", row["source"], exc_info=True)
        info = content_agents.describe(row["source"])
        raise RemovalError(f"{info['name']} could not remove this item. Open {info['name']} to remove it there.", status=409,
                           owner=row["source"], app=info["app"])
    # The agent tombstones its own row; this makes the outcome certain and is a no-op after that.
    library.forget(row["source"], row["source_id"])
    return {"removed": [row["id"]], "files": 0, "warnings": []}


def remove(library: ContentLibrary, item_id: str, expected_revision: int) -> dict:
    """Remove one item by the rules in the module docstring. Raises LookupError, FileExistsError or RemovalError."""
    with library.connect() as db:
        row = db.execute("SELECT * FROM content WHERE id=? AND deleted=0", (item_id,)).fetchone()
    if not row:
        raise LookupError("Content not found")
    decision = plan(row)
    if decision["mode"] == "open":
        info = content_agents.describe(row["source"])
        raise RemovalError(f"Remove this in {info['name']}. My Content only lists what {info['name']} keeps.", status=409,
                           owner=row["source"], app=info["app"], mode="open")
    result = _remove_catalog_item(library, row, expected_revision) if decision["mode"] == "catalog" else _remove_through_agent(library, row, expected_revision)
    return {"id": item_id, "mode": decision["mode"], **result}


def remove_many(library: ContentLibrary, requests: list[dict]) -> list[dict]:
    """Per-item outcomes, so one stale or protected item never blocks the rest."""
    results = []
    for entry in requests:
        identifier = str(entry.get("id", ""))
        try:
            results.append({"ok": True, **remove(library, identifier, int(entry.get("revision", 0)))})
        except RemovalError as error:
            results.append({"ok": False, "id": identifier, "status": error.status, "error": str(error), **error.extra})
        except FileExistsError as error:
            results.append({"ok": False, "id": identifier, "status": 409, "error": str(error), "stale": True})
        except LookupError as error:
            results.append({"ok": False, "id": identifier, "status": 404, "error": str(error), "missing": True})
        except (TypeError, ValueError):
            results.append({"ok": False, "id": identifier, "status": 400, "error": "Invalid removal request"})
    return results
