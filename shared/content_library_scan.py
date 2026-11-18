"""Explicit indexing of known agent catalogs. Never recursively scans the host."""
from pathlib import Path
import sqlite3

from shared.content_library import ContentLibrary, managed_roots
from shared.platform_runtime import get_mutable_data_dir, get_service_data_dir


def refresh_existing():
    from autoyou_agents.notes_agent.notes_tool import NotesTool
    from autoyou_agents.tv_agent.website.backend.store import data_root
    from autoyou_agents.location_agent.store import _database_path
    from autoyou_agents.audio_agent.website.backend.uploads import import_root, legacy_import_root

    root = get_mutable_data_dir("AutoYou", anchor=__file__)
    catalog = ContentLibrary()
    specs = [
        ("notes_agent", NotesTool._resolve_default_storage_dir() / "autoyou_notes.db", "SELECT id,title,'note' AS kind,'' AS path FROM notes"),
        ("page_agent", root / "page_feed.db", "SELECT id,title,'page' AS kind,'' AS path FROM feed_items"),
        ("tv_agent", data_root() / "library.sqlite3", "SELECT id,title,CASE kind WHEN 'movie' THEN 'video' WHEN 'episode' THEN 'video' WHEN 'photo' THEN 'image' WHEN 'document' THEN 'file' ELSE kind END AS kind,COALESCE(file_path,'') AS path FROM items WHERE status!='deleted'"),
        ("media_generation_agent", get_service_data_dir("media_generation_agent", anchor=__file__) / "media_history.db", "SELECT id,COALESCE(file_name,original_prompt) AS title,media_type AS kind,file_path AS path FROM media_history WHERE status='completed'"),
        ("location_agent", _database_path(), "SELECT substr(timestamp,1,10) AS id,'Locations on ' || substr(timestamp,1,10) AS title,'location' AS kind,'' AS path FROM locations GROUP BY substr(timestamp,1,10)"),
    ]
    result = {"indexed": 0, "sources": [], "errors": []}
    for source, path, query in specs:
        if not path.is_file():
            continue
        try:
            # Existing databases only. The secure SQLite adapter transparently
            # handles sealed databases using the same active Maximus key.
            with sqlite3.connect(path) as db:
                db.row_factory = sqlite3.Row
                for row in db.execute(query):
                    href = f"/agent/{source}/"
                    if source == "tv_agent":
                        href += "#/item/" + str(row["id"])
                    catalog.publish(source, str(row["id"]), row["title"] or "Untitled", row["kind"], path=row["path"] or "", href=href)
                    result["indexed"] += 1
            result["sources"].append(source)
        except Exception:
            # Do not return file paths or database exception details to clients.
            result["errors"].append(source + ": unable to read its catalog")
    for folder in {import_root(), legacy_import_root(), *(root / "audio_agent" for root in managed_roots())}:
        path = folder / ".pending" / "uploads.sqlite"
        if not path.is_file():
            continue
        try:
            with sqlite3.connect(path) as db:
                for identifier, filename, title in db.execute("SELECT id,filename,title FROM uploads WHERE complete=1"):
                    target = folder / (identifier + Path(filename).suffix.lower())
                    if target.is_file():
                        catalog.publish("audio_agent", identifier, title or filename, "audio", path=str(target), href="/agent/audio_agent/")
                        result["indexed"] += 1
        except Exception:
            result["errors"].append("audio_agent: unable to read its import catalog")
    return result
