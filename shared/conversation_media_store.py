# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Durable server-side copies of conversation media.

Voice notes, spoken replies, and AI-generated files used to live only in the
temporary media directory and were removed after delivery, so the admin Chat &
History view could show a transcript but never the audio or file behind it.
This module keeps a server-owned copy (encrypted when Secure Professional
Maximus storage is on). The index rows live in ``sessions.db`` through
``MemoryIntegratedSessionManager``; this module only owns the files.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import base64
import logging
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CONVERSATION_MEDIA_DIR_ENV = "AUTOYOU_CONVERSATION_MEDIA_DIR"
MAX_ARCHIVED_MEDIA_BYTES = 64 * 1024 * 1024

# Types a browser may render inline on the admin origin. Everything else is
# served as a download so an uploaded HTML/SVG file can never run script there.
INLINE_SAFE_MIMETYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/heic",
        "image/heif",
        "application/pdf",
        "text/plain",
    }
)


def _repo_anchor() -> Path:
    return Path(__file__).resolve().parents[1] / "server.py"


def conversation_media_dir() -> Path:
    override = str(os.getenv(CONVERSATION_MEDIA_DIR_ENV, "") or "").strip()
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    from shared.platform_runtime import get_service_data_dir

    return get_service_data_dir("conversation_media", anchor=_repo_anchor())


def media_archive_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """``chat_history.keep_media`` (default on) controls new copies only."""
    effective = cfg
    if effective is None:
        try:
            from server import STATE  # type: ignore

            effective = STATE.config or {}
        except Exception:
            effective = {}
    section = effective.get("chat_history") if isinstance(effective, dict) else None
    if not isinstance(section, dict) or "keep_media" not in section:
        return True
    value = section.get("keep_media")
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def media_kind(mimetype: Any, filename: Any = "") -> str:
    value = str(mimetype or "").split(";", 1)[0].strip().lower()
    if not value:
        guessed, _ = mimetypes.guess_type(str(filename or ""))
        value = (guessed or "").lower()
    if value.startswith("image/"):
        return "image"
    if value.startswith("video/"):
        return "video"
    if value.startswith("audio/"):
        return "audio"
    return "file"


def is_inline_safe(mimetype: Any) -> bool:
    value = str(mimetype or "").split(";", 1)[0].strip().lower()
    return value in INLINE_SAFE_MIMETYPES or value.startswith(("audio/", "video/"))


def _safe_component(value: Any, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._")
    return (text or fallback)[:80]


def _safe_filename(value: Any, mimetype: Any) -> str:
    name = os.path.basename(str(value or "").replace("\\", "/")).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    if not name:
        extension = mimetypes.guess_extension(str(mimetype or "").split(";", 1)[0].strip()) or ".bin"
        name = f"attachment{extension}"
    return name[:160]


def _attachment_bytes(attachment: Dict[str, Any], *, trusted_path: bool) -> Optional[bytes]:
    """Inline bytes first; a ``path`` only when AutoYou produced it.

    ``trusted_path`` is for server-side producers (TTS replies, agent tools).
    Anything a client sent may only point into AutoYou's own media folders.
    """
    from shared.secure_storage import FILE_HEADER, read_secure_file

    data_value = attachment.get("data")
    if isinstance(data_value, str) and data_value:
        return base64.b64decode(data_value, validate=False)
    url_value = attachment.get("url")
    if isinstance(url_value, str) and url_value.startswith("data:") and "," in url_value:
        return base64.b64decode(url_value.split(",", 1)[1], validate=False)
    path_value = str(attachment.get("path") or "").strip()
    if path_value and os.path.isfile(path_value) and (trusted_path or is_servable_media_path(path_value)):
        raw = Path(path_value).read_bytes()
        return read_secure_file(path_value) if raw.startswith(FILE_HEADER) else raw
    return None


def store_media_bytes(
    data: bytes,
    *,
    user_id: str,
    session_id: str,
    filename: str,
    mimetype: str,
) -> Dict[str, Any]:
    """Write one durable copy and return its index fields (no database write)."""
    from shared.secure_storage import write_secure_file

    media_id = uuid.uuid4().hex
    safe_name = _safe_filename(filename, mimetype)
    target_dir = conversation_media_dir() / _safe_component(user_id, "user") / _safe_component(session_id, "session")
    target_dir.mkdir(parents=True, exist_ok=True)
    extension = os.path.splitext(safe_name)[1][:16]
    target = target_dir / f"{media_id}{extension}"
    write_secure_file(target, data)
    return {
        "media_id": media_id,
        "filename": safe_name,
        "mimetype": str(mimetype or "").strip() or (mimetypes.guess_type(safe_name)[0] or "application/octet-stream"),
        "size_bytes": len(data),
        "storage_path": str(target),
        "created_at": time.time(),
    }


def archive_attachment(
    session_manager: Any,
    attachment: Optional[Dict[str, Any]],
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    role: str,
    source: str,
    transcript: str = "",
    cfg: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Copy one attachment into the durable store and index it. Never raises."""
    if not isinstance(attachment, dict) or session_manager is None:
        return None
    if not hasattr(session_manager, "record_conversation_media"):
        return None
    if not user_id or not session_id or not media_archive_enabled(cfg):
        return None
    try:
        data = _attachment_bytes(attachment, trusted_path=role == "assistant")
        if not data or len(data) > MAX_ARCHIVED_MEDIA_BYTES:
            return None
        mimetype = str(attachment.get("mimetype") or "").strip()
        stored = store_media_bytes(
            data,
            user_id=user_id,
            session_id=session_id,
            filename=str(attachment.get("filename") or attachment.get("path") or ""),
            mimetype=mimetype,
        )
        record = {
            **stored,
            "user_id": str(user_id),
            "session_id": str(session_id),
            "turn_id": str(turn_id or ""),
            "role": "assistant" if role == "assistant" else "user",
            "kind": media_kind(stored["mimetype"], stored["filename"]),
            "source": str(source or "attachment")[:48],
            "transcript": str(transcript or "")[:4000],
        }
        if not session_manager.record_conversation_media(record):
            delete_media_file(record["storage_path"])
            return None
        return record
    except Exception as exc:
        logger.warning("Could not keep a server copy of conversation media: %s", exc)
        return None


def _temp_media_root() -> Path:
    import tempfile

    return (Path(tempfile.gettempdir()) / "autoyou_media").resolve()


def is_servable_media_path(path_value: Any) -> bool:
    """Only files AutoYou itself wrote may be served back from a stored turn.

    A stored chat context can carry a client-supplied ``path``; serving that
    verbatim would turn history into a way to read arbitrary server files.
    """
    text = str(path_value or "").strip()
    if not text:
        return False
    try:
        resolved = Path(text).resolve()
    except Exception:
        return False
    if not resolved.is_file():
        return False
    roots = [_temp_media_root()]
    try:
        roots.append(conversation_media_dir().resolve())
    except Exception:
        pass
    return any(root in resolved.parents for root in roots)


def read_attachment_bytes(attachment: Optional[Dict[str, Any]]) -> Optional[bytes]:
    """Bytes for a stored-turn attachment: inline data, or an AutoYou-owned file."""
    if not isinstance(attachment, dict):
        return None
    return _attachment_bytes(attachment, trusted_path=False)


def read_media_bytes(storage_path: str) -> bytes:
    from shared.secure_storage import read_secure_file

    path = Path(str(storage_path or ""))
    root = conversation_media_dir().resolve()
    resolved = path.resolve()
    if root not in resolved.parents:
        raise FileNotFoundError("Media is outside the conversation media store")
    return read_secure_file(resolved, migrate_plaintext=False)


def delete_media_file(storage_path: Any) -> None:
    try:
        path = Path(str(storage_path or "")).resolve()
        root = conversation_media_dir().resolve()
        if root in path.parents and path.is_file():
            path.unlink()
    except Exception as exc:
        logger.debug("Could not remove conversation media file: %s", exc)
