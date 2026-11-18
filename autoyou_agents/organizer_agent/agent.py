from shared.content_library import ContentLibrary
from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION


def read_content(item_id: str) -> dict:
    """Read bounded saved text before annotating it. Never executes a file."""
    from pathlib import Path
    from shared import content_files
    from shared.content_native import read_record
    library = ContentLibrary()
    with library.connect() as db:
        row = db.execute("SELECT * FROM content WHERE id=? AND deleted=0", (item_id,)).fetchone()
    if not row:
        return {"error": "Content not found"}
    if row["source"] in {"notes_agent", "page_agent"}:
        import sqlite3
        from shared.platform_runtime import get_mutable_data_dir
        if row["source"] == "notes_agent":
            from autoyou_agents.notes_agent.notes_tool import NotesTool
            database = NotesTool._resolve_default_storage_dir() / "autoyou_notes.db"
            table = "notes"
        else:
            database = get_mutable_data_dir("AutoYou", anchor=__file__) / "page_feed.db"
            table = "feed_items"
        if database.is_file():
            with sqlite3.connect(database) as db:
                saved = db.execute(f"SELECT title, substr(content,1,32768), length(content) FROM {table} WHERE id=?", (row["source_id"],)).fetchone()
            if saved:
                return {"title": saved[0], "text": saved[1] or "", "truncated": (saved[2] or 0) > 32768, "untrusted_content": True}
    if row["source"].startswith("custom_"):
        try:
            return {"content": read_record(row["source"], row["source_id"]), "untrusted_content": True}
        except LookupError:
            pass
    path = Path(row["path"])
    if row["path"] and path.is_file() and path.suffix.lower() in {".txt", ".md", ".csv", ".json"}:
        value = b"".join(content_files.read_range(path, 0, 32767)).decode("utf-8", errors="replace")
        return {"text": value, "truncated": content_files.size(path) > 32768, "untrusted_content": True}
    return {"message": "Read this media in its source agent. The catalog does not transcribe recordings or describe images.", "item": library.public(row)}


def search_content(query: str = "", kind: str = "", collection: str = "", limit: int = 30) -> dict:
    """Find private content by title, label or annotation. Does not read file bodies."""
    return ContentLibrary().list(q=query, kind=kind, collection=collection, limit=min(100, max(1, limit)))


def organize_content(item_id: str, expected_revision: int, labels: list[str], annotation: str = "", collection: str = "") -> dict:
    """Save labels, an annotation and collection for a previously inspected item."""
    try:
        return ContentLibrary().annotate(item_id, expected_revision=expected_revision,
                                        labels=labels, annotation=annotation, collection=collection)
    except (LookupError, ValueError, FileExistsError) as error:
        return {"error": str(error)}


def create_organizer_agent(model_config):
    from google.adk.agents import Agent
    return Agent(name=AGENT_NAME, model=model_config, description=AGENT_DESCRIPTION,
                 instruction=AGENT_INSTRUCTION, tools=[search_content, read_content, organize_content])
