"""Explicit indexing of known agent catalogs. Never recursively scans the host."""
from pathlib import Path
import hashlib
import json
import os
import sqlite3

from shared.content_library import ContentLibrary, managed_roots, infer_kind
from shared.platform_runtime import get_mutable_data_dir, get_service_data_dir


def refresh_existing(cancelled=lambda: False):
    from autoyou_agents.notes_agent.notes_tool import NotesTool
    from autoyou_agents.media_agent.website.backend.store import data_root
    from autoyou_agents.location_agent.store import _database_path
    from autoyou_agents.music_agent.website.backend.uploads import import_root, legacy_import_root

    root = get_mutable_data_dir("AutoYou", anchor=__file__)
    catalog = ContentLibrary()
    specs = [
        ("notes_agent", NotesTool._resolve_default_storage_dir() / "autoyou_notes.db", "SELECT id,title,'note' AS kind,'' AS path FROM notes"),
        ("page_agent", root / "page_feed.db", "SELECT id,title,'page' AS kind,'' AS path FROM feed_items"),
        ("media_agent", data_root() / "library.sqlite3", "SELECT id,title,CASE kind WHEN 'movie' THEN 'video' WHEN 'episode' THEN 'video' WHEN 'photo' THEN 'image' WHEN 'document' THEN 'file' ELSE kind END AS kind,COALESCE(file_path,'') AS path FROM items WHERE status!='deleted'"),
        ("media_generation_agent", get_service_data_dir("media_generation_agent", anchor=__file__) / "media_history.db", "SELECT id,COALESCE(file_name,original_prompt) AS title,media_type AS kind,file_path AS path FROM media_history WHERE status='completed'"),
        ("location_agent", _database_path(), "SELECT substr(timestamp,1,10) AS id,'Locations on ' || substr(timestamp,1,10) AS title,'location' AS kind,'' AS path FROM locations GROUP BY substr(timestamp,1,10)"),
    ]
    result = {"indexed": 0, "sources": [], "errors": []}
    for source, path, query in specs:
        if cancelled():
            break
        if not path.is_file():
            continue
        try:
            # Existing databases only. The secure SQLite adapter transparently
            # handles sealed databases using the same active Maximus key.
            with sqlite3.connect(path) as db:
                db.row_factory = sqlite3.Row
                for row in db.execute(query):
                    href = f"/agent/{source}/"
                    if source == "media_agent":
                        href += "#/item/" + str(row["id"])
                    catalog.publish(source, str(row["id"]), row["title"] or "Untitled", row["kind"], path=row["path"] or "", href=href)
                    result["indexed"] += 1
            result["sources"].append(source)
        except Exception:
            # Do not return file paths or database exception details to clients.
            result["errors"].append(source + ": unable to read its catalog")
    for source, scan in (("attachments", scan_attachments), ("voice_training_agent", scan_voice), ("audio_folders", scan_audio_folders),
                         ("diary_agent", scan_private), ("games_agent", scan_games), ("reminders_agent", scan_reminders)):
        if cancelled():
            break
        try:
            result["indexed"] += scan(catalog, cancelled=cancelled)
            result["sources"].append(source)
        except Exception:
            result["errors"].append(source + ": unable to read its content")
    for folder in {import_root(), legacy_import_root(), *(root / "music_agent" for root in managed_roots())}:
        path = folder / ".pending" / "uploads.sqlite"
        if not path.is_file():
            continue
        try:
            with sqlite3.connect(path) as db:
                for identifier, filename, title in db.execute("SELECT id,filename,title FROM uploads WHERE complete=1"):
                    target = folder / (identifier + Path(filename).suffix.lower())
                    if target.is_file():
                        catalog.publish("music_agent", identifier, title or filename, "audio", path=str(target), href="/agent/music_agent/")
                        result["indexed"] += 1
        except Exception:
            result["errors"].append("music_agent: unable to read its import catalog")
    return result


def _json(value, fallback):
    try:
        return json.loads(value or "")
    except (ValueError, TypeError):
        return fallback


def scan_attachments(catalog=None, cancelled=lambda: False):
    """Known databases only; carry native tags and parent links into the catalog."""
    from autoyou_agents.notes_agent.notes_tool import NotesTool
    catalog = catalog or ContentLibrary()
    count = 0
    databases = [
        ("page_agent", get_mutable_data_dir("AutoYou", anchor=__file__) / "page_feed.db"),
        ("notes_agent", NotesTool._resolve_default_storage_dir() / "autoyou_notes.db"),
    ]
    for source, path in databases:
        if not path.is_file():
            continue
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            table = "notes" if source == "notes_agent" else "feed_items"
            if table not in tables:
                continue
            parents, media_parents = {}, {}
            for raw in db.execute("SELECT * FROM " + table):
                if cancelled():
                    return count
                row = dict(raw)
                tags = _json(row.get("tags"), []) if source == "notes_agent" else [r[0] for r in db.execute("SELECT tag FROM item_tags WHERE item_id=?", (row["id"],))] if "item_tags" in tables else []
                parent = catalog.publish(source, str(row["id"]), row.get("title") or "Untitled", "note" if source == "notes_agent" else "page",
                    href=f"/agent/{source}/", source_labels=tags if isinstance(tags, list) else [],
                    source_collection=row.get("category") or "", source_text=row.get("content") or "")
                parents[str(row["id"])] = parent
                for entry in _json(row.get("metadata"), {}).get("media_attachments", []):
                    if isinstance(entry, dict) and entry.get("id") is not None:
                        media_parents[str(entry["id"])] = parent
            media_table = "media_attachments" if source == "notes_agent" else "blobs"
            if media_table not in tables:
                continue
            seen = set()
            for raw in db.execute("SELECT * FROM " + media_table):
                if cancelled():
                    return count
                row = dict(raw)
                path = Path(row.get("path") or "")
                if not row.get("path") or not path.is_file():
                    continue
                related = [media_parents.get(str(row["id"]))]
                if source == "page_agent" and "feed_attachments" in tables:
                    related = [parents.get(str(r[0])) for r in db.execute("SELECT item_id FROM feed_attachments WHERE blob_id=?", (row["id"],))] or [None]
                for parent in related:
                    sid = "attachment:" + str(row["id"]) + (":" + parent["source_id"] if parent and source == "page_agent" else "")
                    catalog.publish(source, sid, row.get("filename") or path.name, infer_kind(path.name), path=str(path),
                        parent_id=parent["id"] if parent else "", source_labels=parent["source_labels"] if parent else [],
                        source_collection=parent["source_collection"] if parent else "Attachments",
                        source_text=str(row.get("transcription") or ""))
                    seen.add(sid)
                    count += 1
            _reconcile(catalog, source, "attachment:", seen)
    return count


def _reconcile(catalog, source, prefix, seen):
    # Only an entirely completed source scan can remove stale index entries.
    with catalog.connect() as db:
        rows = db.execute("SELECT source_id FROM content WHERE source=? AND deleted=0 AND source_id LIKE ?", (source, prefix + "%")).fetchall()
    for row in rows:
        if row[0] not in seen:
            catalog.forget(source, row[0])


def scan_voice(catalog=None, cancelled=lambda: False):
    from shared.voice_training_storage import get_voice_training_dir
    from shared.secure_storage import load_secure_json
    from shared.custom_voice_tts import voice_clone_outputs_dir
    catalog = catalog or ContentLibrary()
    root = get_voice_training_dir()
    seen, count = set(), 0
    records = load_secure_json(root / "transcripts.json", default=[])
    if not isinstance(records, list):
        raise ValueError("Voice transcript catalog is unavailable")
    for row in records:
        if cancelled():
            return count
        if not isinstance(row, dict) or not row.get("id"):
            continue
        sid = "transcript:" + str(row["id"])
        text = str(row.get("transcript") or row.get("text") or "")
        parent = catalog.publish("voice_training_agent", sid, text[:90] or "Voice transcript", "note",
            href="/agent/voice_training_agent/", source_text=text, source_labels=["transcript"], source_collection="Voice / Transcripts")
        seen.add(sid)
        filename = str(row.get("filename") or "")
        path = root / "recordings" / filename
        if filename and path.resolve().is_relative_to(root.resolve()) and path.is_file():
            sid = "recording:" + str(row["id"])
            catalog.publish("voice_training_agent", sid, filename, "voice", path=str(path), parent_id=parent["id"],
                source_text=text, source_labels=["recording"], source_collection="Voice / Recordings")
            seen.add(sid)
        count += 1
    # Index completed voice output artifacts, never consent files or credentials.
    outputs = voice_clone_outputs_dir()
    if outputs.is_dir():
        for model_dir in outputs.iterdir():
            if not model_dir.is_dir() or model_dir.is_symlink():
                continue
            for path in model_dir.iterdir():
                if cancelled():
                    return count
                if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {".wav", ".json", ".safetensors", ".onnx", ".pt"}:
                    continue
                sid = "model:" + model_dir.name + ":" + path.name
                catalog.publish("voice_training_agent", sid, model_dir.name + " / " + path.name,
                    "voice" if path.suffix.lower() == ".wav" else "file", path=str(path),
                    source_labels=["voice-model", "preview" if "preview" in path.name else "artifact"], source_collection="Voice / Models")
                seen.add(sid)
                count += 1
    for prefix in ("transcript:", "recording:", "model:"):
        _reconcile(catalog, "voice_training_agent", prefix, seen)
    return count


def _scan_custom_base(catalog, base_kind: str, enabled: bool, cancelled=lambda: False) -> int:
    count = 0
    seen_by_source: dict[str, set[str]] = {}
    with catalog.connect() as db:
        custom_sources = {
            row[0] for row in db.execute("SELECT source FROM content_agents WHERE base_kind=?", (base_kind,)).fetchall()
        }
        has_native = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_content'").fetchone())
        native_rows = (
            db.execute(
                "SELECT source,source_id,payload FROM native_content WHERE json_extract(payload,'$.base_kind')=?",
                (base_kind,),
            ).fetchall()
            if has_native
            else []
        )
    for row in native_rows:
        custom_sources.add(row["source"])
    if enabled:
        kind = {"notes": "note", "diary": "note", "page": "page", "music": "audio"}.get(base_kind, "agent")
        for row in native_rows:
            if cancelled():
                return count
            record = _json(row["payload"], {})
            if not isinstance(record, dict):
                continue
            item = catalog.publish(
                row["source"],
                row["source_id"],
                record.get("title") or "Untitled",
                kind,
                base_kind=base_kind,
                source_labels=record.get("tags"),
                source_collection=record.get("category"),
                source_text=record.get("body") or "",
                href="/agent/organizer_agent/api/content/native/" + row["source"] + "/" + row["source_id"],
            )
            with catalog.connect() as db:
                catalog.restore_native_files(db, row["source"], record=record, parent_id=item["id"])
            seen_by_source.setdefault(row["source"], set()).add(row["source_id"])
            count += 1
    for source in sorted(custom_sources):
        with catalog.connect() as db:
            if enabled:
                # Completed and not-yet-attached uploads belong to the native
                # source store too; a parent-only scan must not remove them.
                files = {row[0] for row in db.execute("SELECT source_id FROM content WHERE source=? AND deleted=0 AND path!=''", (source,))}
                seen_by_source.setdefault(source, set()).update(files)
            else:
                for row in db.execute("SELECT id FROM content WHERE source=? AND deleted=0", (source,)).fetchall():
                    catalog._tombstone(db, row["id"], catalog.tick(db), preserve_native_file=True)
        if enabled:
            _reconcile(catalog, source, "", seen_by_source.get(source, set()))
    return count


def scan_private(catalog=None, cancelled=lambda: False):
    """List Diary entries by title and a short preview, only when the owner has opted in.

    The Diary guards its own pages with a separate sign-in, so the library never lists it on
    its own. Switching the option off removes the entries from the library again.
    """
    from shared.content_library import settings
    catalog = catalog or ContentLibrary()
    prefs = settings()
    seen, count = set(), 0
    if prefs["index_private"]:
        from autoyou_agents.diary_agent.agent import list_diary_entries
        for entry in list_diary_entries():
            if cancelled():
                return count
            catalog.publish("diary_agent", entry["id"], entry["title"] or entry["date"], "note", href="/agent/diary_agent/",
                            source_collection=entry.get("journal") or "Diary", source_labels=["diary", *entry.get("tags", [])], source_text=(entry["date"] + " " + entry["preview"]).strip())
            seen.add(entry["id"])
            count += 1
    _reconcile(catalog, "diary_agent", "", seen)
    count += _scan_custom_base(catalog, "diary", prefs["index_private"], cancelled=cancelled)
    profile_ids = set()
    if prefs["index_profile"]:
        from autoyou_agents.profile_agent.agent import read_profile
        result = read_profile()
        if result.get("status") != "success":
            raise ValueError("Profile is unavailable")
        if result.get("content"):
            catalog.publish("profile_agent", "profile", "Profile", "note", href="/agent/profile_agent/",
                            source_collection="Profile", source_text=result["content"])
            profile_ids.add("profile")
            count += 1
    _reconcile(catalog, "profile_agent", "", profile_ids)
    count += _scan_custom_base(catalog, "profile", prefs["index_profile"], cancelled=cancelled)
    return count


def scan_reminders(catalog, cancelled=lambda: False):
    from shared.content_library import settings
    from shared import scheduler_service as scheduler
    prefs = settings()
    seen = set()
    if prefs["index_reminders"]:
        for row in scheduler.load_json(scheduler.REMINDERS_FILE):
            if cancelled():
                return len(seen)
            title, _, body = str(row.get("message") or "").partition("\n")
            identifier = str(row["id"])
            catalog.publish("reminders_agent", identifier, title or "Reminder", "note", href="/agent/reminders_agent/",
                            source_collection="Reminders", source_labels=["reminder"],
                            source_text="\n".join(filter(None, (row.get("iso"), body))))
            seen.add(identifier)
    _reconcile(catalog, "reminders_agent", "", seen)
    custom_count = _scan_custom_base(catalog, "reminders", prefs["index_reminders"], cancelled=cancelled)
    return len(seen) + custom_count


def scan_games(catalog, cancelled=lambda: False):
    from shared.game_package_catalog import GamePackages
    root = Path(__file__).resolve().parents[1]
    packages = GamePackages(root, get_mutable_data_dir("AutoYou", anchor=__file__))
    seen = set()
    after = ""
    while True:
        page = packages.listing(after)
        for game in page["games"]:
            if cancelled():
                return len(seen)
            catalog.publish("games_agent", game["id"], game["title"], "agent", href="/agent/games_agent/",
                            source_collection="Games", source_labels=["game"],
                            source_text=game["title"] + (" · " + str(game["version"]) if game.get("version") else ""))
            seen.add(game["id"])
        after = page["next_after"]
        if not after:
            break
    _reconcile(catalog, "games_agent", "", seen)
    return len(seen)


def scan_audio_folders(catalog=None, cancelled=lambda: False):
    from shared.music_agent_library import load_music_agent_library_settings, resolve_audio_library_path_details
    catalog = catalog or ContentLibrary()
    details = resolve_audio_library_path_details(load_music_agent_library_settings(), anchor=__file__)
    count = 0
    for entry in details:
        # Pages, Notes, and Voice have their own provenance adapters above.
        if entry["source"] in {"voice_transcriptions", "page_feed_audio", "notes_media_audio"} or not entry["exists"]:
            continue
        root = Path(entry["path"])
        for directory, folders, names in os.walk(root, followlinks=False):
            folders[:] = [name for name in folders if not name.startswith(".") and not name.endswith(".parts") and not (Path(directory) / name).is_symlink()]
            for name in names:
                if cancelled():
                    return count
                path = Path(directory) / name
                if path.is_symlink() or infer_kind(name) != "audio":
                    continue
                relative = path.relative_to(root)
                sid = "folder:" + hashlib.sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()
                catalog.publish("music_agent", sid, path.stem, "audio", path=str(path),
                    source_collection=(root.name + (" / " + str(relative.parent) if relative.parent != Path('.') else ""))[:100])
                count += 1
    return count
