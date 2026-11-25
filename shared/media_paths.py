"""App-owned temporary attachments shared by chat, voice and content ingestion."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


def temp_media_root(*, create: bool = False) -> Path:
    # Test runs must neither accept nor write the operator's temporary media.
    base = Path(os.environ.get("AUTOYOU_TEST_ROOT") or tempfile.gettempdir())
    root = base / "autoyou_media"
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise ValueError("The temporary media directory must be app-owned")
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root.resolve()


def page_database_path() -> Path:
    from shared.platform_runtime import get_config_dir

    return get_config_dir("AutoYou", anchor=Path(__file__).resolve().parents[1] / "server.py") / "page_feed.db"


def page_upload_dir() -> Path:
    from shared.content_library import output_dir

    return output_dir("page_agent")
