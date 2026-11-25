"""Publish finalized recordings without changing capture policy or moving files."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import sys

LOGGER = logging.getLogger(__name__)


def _path_id(path: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()


def publish_recording(path_value, *, kind: str, category: str = "Calls"):
    """Only called after the recording has been closed and sealed successfully."""
    try:
        candidate = Path(path_value)
        if candidate.is_symlink():
            return None
        path = candidate.resolve()
        if not path.is_file() or path.stat().st_size == 0:
            return None
        from shared.content_library import publish_content

        if kind == "audio":
            # Same identity as the Music folder scanner, so Sync is idempotent.
            return publish_content("music_agent", "folder:" + _path_id(path), path.stem, "audio", path=str(path),
                href="/agent/music_agent/", source_labels=["recording", category.lower()],
                source_collection="Recordings / " + category)
        if kind not in {"video", "frames"}:
            raise ValueError("Unsupported recording kind")
        from autoyou_agents.media_agent.website.backend.store import Store

        store = Store()
        folder = store.add_folder("Recordings", root_path="autoyou:recordings")
        item, _ = store.add_item(source_key="recording:" + _path_id(path),
            title=path.parent.name + (" - Frames" if kind == "frames" else " - Video"),
            kind="document" if kind == "frames" else "video", folder_id=folder["id"], file_path=str(path),
            technical={"recording": True, "recording_category": category})
        if item["status"] == "deleted":
            return None
        return publish_content("media_agent", item["id"], item["title"], "file" if kind == "frames" else "video",
            path=str(path), href="/agent/media_agent/#/item/" + item["id"],
            source_labels=["recording", category.lower()], source_collection="Recordings / " + category)
    except Exception:
        # Recording bytes are more important than indexing. Sync can recover it.
        LOGGER.warning("Could not index a completed recording", exc_info=True)
        return None


def is_registered_recording(path: Path) -> bool:
    """A broad configured recording folder does not authorize its other files."""
    from shared.content_library import ContentLibrary, state_root

    if not (state_root() / "catalog.sqlite3").is_file():
        return False
    with ContentLibrary().connect() as db:
        rows = db.execute("SELECT source,source_id,source_labels FROM content WHERE deleted=0 AND path=?",
                          (str(path.resolve()),)).fetchall()
    for row in rows:
        if row["source"] not in {"media_agent", "music_agent"} or "recording" not in json.loads(row["source_labels"] or "[]"):
            continue
        if row["source"] == "media_agent" or row["source_id"] == "folder:" + _path_id(path):
            return True
    return False


def recording_roots() -> list[tuple[Path, str, str]]:
    """Known app recording destinations, including pre-consolidation locations."""
    from shared.content_library import output_roots
    from shared.platform_runtime import get_mutable_data_dir

    anchor = Path(__file__).resolve().parents[1] / "server.py"
    legacy = get_mutable_data_dir("AutoYou", anchor=anchor) / "output"
    roots = [(legacy / "video-recordings", "video", "Calls"),
             (legacy / "safety-recordings", "audio", "Safety")]
    roots.extend((root / "recordings" / "video", "video", "Calls") for root in output_roots("media_agent"))
    roots.extend((root / "recordings" / "safety", "audio", "Safety") for root in output_roots("music_agent"))
    server = sys.modules.get("server")
    if server and hasattr(server, "_get_video_call_config"):
        cfg = server._get_video_call_config()
        for key, kind, category in (("recording_dir", "video", "Calls"), ("silent_recording_dir", "audio", "Safety")):
            if cfg.get(key):
                configured = Path(cfg[key]).expanduser().resolve()
                test_root = os.getenv("AUTOYOU_TEST_ROOT")
                if not test_root or configured.is_relative_to(Path(test_root).resolve()):
                    roots.append((configured, kind, category))
    return list(dict.fromkeys(roots))


def scan_recordings(catalog=None, cancelled=lambda: False) -> int:
    """Recover known finalized formats; never index in-progress .partial files."""
    count = 0
    seen = set()
    for root, kind, category in recording_roots():
        if not root.is_dir() or root.is_symlink() or root == Path(root.anchor):
            continue
        for directory, folders, names in os.walk(root, followlinks=False):
            folders[:] = [name for name in folders if not name.startswith(".") and not (Path(directory) / name).is_symlink()]
            for name in names:
                if cancelled():
                    return count
                path = Path(directory) / name
                if path.is_symlink() or path.resolve() in seen:
                    continue
                recording_kind = None
                if name.startswith("autoyou-") and path.suffix.lower() == ".wav":
                    recording_kind = "audio"
                elif kind == "video" and name == "video.mp4" and (path.parent / "manifest.jsonl").is_file():
                    recording_kind = "video"
                elif kind == "video" and name == "manifest.jsonl" and not (path.parent / "video.mp4").exists() and any(path.parent.glob("image-*.jpg")):
                    recording_kind = "frames"
                if recording_kind:
                    seen.add(path.resolve())
                    if publish_recording(path, kind=recording_kind, category=category):
                        count += 1
    return count
