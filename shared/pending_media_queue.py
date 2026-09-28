# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

import base64
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from shared.secure_storage import SecureStorageError, read_secure_file, secure_storage_enabled, write_secure_file


DEFAULT_PENDING_MEDIA_TTL_SECONDS = 24 * 60 * 60
DEFAULT_PENDING_MEDIA_MAX_TOTAL_BYTES = 1024 * 1024 * 1024

_DATA_URL_RE = re.compile(
    r"^data:(?P<mimetype>[^;,]+)?(?:;filename=(?P<filename>[^;]+))?;base64,(?P<data>.*)$",
    re.IGNORECASE | re.DOTALL,
)


def read_env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
        return parsed if parsed >= minimum else default
    except Exception:
        return default


def pending_media_ttl_seconds() -> int:
    return read_env_int(
        "AUTOYOU_PENDING_MEDIA_QUEUE_TTL_SECONDS",
        DEFAULT_PENDING_MEDIA_TTL_SECONDS,
        minimum=0,
    )


def pending_media_max_total_bytes() -> int:
    return read_env_int(
        "AUTOYOU_PENDING_MEDIA_QUEUE_MAX_TOTAL_BYTES",
        DEFAULT_PENDING_MEDIA_MAX_TOTAL_BYTES,
        minimum=0,
    )


def safe_payload_filename(filename: Any, mimetype: str = "") -> str:
    raw = str(filename or "attachment").replace("\\", "/")
    cleaned = os.path.basename(raw).strip()
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", cleaned).strip(" .")
    if not cleaned:
        cleaned = f"attachment-{uuid.uuid4().hex[:8]}"
    if "." not in cleaned:
        ext = mimetypes.guess_extension(str(mimetype or "").split(";", 1)[0]) or ""
        if ext:
            cleaned += ext
    return cleaned[:160]


def media_payload_directory(base_dir: Any) -> Path:
    directory = Path(base_dir) / "pending_media_payloads"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def store_media_payload(
    base_dir: Any,
    media_bytes: bytes,
    *,
    filename: Any = "attachment",
    mimetype: str = "application/octet-stream",
    prefix: str = "media",
) -> Dict[str, Any]:
    payload = bytes(media_bytes or b"")
    if not payload:
        return {}
    directory = media_payload_directory(base_dir)
    safe_name = safe_payload_filename(filename, mimetype)
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(prefix or "media")).strip(".-") or "media"
    target = directory / f"{safe_prefix}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:10]}-{safe_name}"
    write_secure_file(target, payload)
    return {
        "media_path": str(target),
        "media_size_bytes": len(payload),
    }


def decode_data_url(value: str) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    match = _DATA_URL_RE.match(str(value or "").strip())
    if not match:
        return None, None, None
    try:
        data = base64.b64decode(match.group("data") or "", validate=False)
    except Exception:
        return None, match.group("mimetype"), match.group("filename")
    return data, match.group("mimetype"), match.group("filename")


def read_media_payload(entry: Dict[str, Any], *, legacy_key: str = "data") -> Optional[bytes]:
    for key in ("media_path", "data_path", "payload_path"):
        path_value = str(entry.get(key) or "").strip()
        if path_value:
            try:
                path = Path(path_value)
                if path.exists() and path.is_file():
                    return read_secure_file(path)
            except SecureStorageError:
                raise
            except Exception:
                return None

    legacy_value = entry.get(legacy_key)
    if isinstance(legacy_value, str) and legacy_value.strip():
        payload = legacy_value.strip()
        if payload.startswith("data:"):
            data, _, _ = decode_data_url(payload)
            return data
        try:
            return base64.b64decode(payload, validate=False)
        except Exception:
            return None
    return None


def delete_media_payload(entry: Dict[str, Any]) -> None:
    for key in ("media_path", "data_path", "payload_path"):
        path_value = str(entry.get(key) or "").strip()
        if not path_value:
            continue
        try:
            Path(path_value).unlink(missing_ok=True)
        except Exception:
            pass


def entry_is_expired(entry: Dict[str, Any], *, now: Optional[float] = None, ttl_seconds: Optional[int] = None) -> bool:
    ttl = pending_media_ttl_seconds() if ttl_seconds is None else int(ttl_seconds)
    if ttl <= 0:
        return False
    try:
        queued_at = float(entry.get("queued_at") or 0)
    except Exception:
        queued_at = 0.0
    if queued_at <= 0:
        return False
    return ((time.time() if now is None else now) - queued_at) > ttl


def media_payload_size(entry: Dict[str, Any]) -> int:
    for key in ("media_size_bytes", "data_size_bytes", "payload_size_bytes"):
        try:
            value = int(entry.get(key) or 0)
            if value > 0:
                return value
        except Exception:
            pass
    for key in ("media_path", "data_path", "payload_path"):
        path_value = str(entry.get(key) or "").strip()
        if path_value:
            try:
                path = Path(path_value)
                if secure_storage_enabled():
                    return len(read_secure_file(path)) if path.is_file() else 0
                return max(0, int(path.stat().st_size))
            except SecureStorageError:
                raise
            except Exception:
                return 0
    return 0


def prune_and_trim_media_entries(
    entries: Iterable[Dict[str, Any]],
    *,
    limit_count: int,
    ttl_seconds: Optional[int] = None,
    max_total_bytes: Optional[int] = None,
) -> List[Dict[str, Any]]:
    now = time.time()
    kept: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry_is_expired(entry, now=now, ttl_seconds=ttl_seconds):
            delete_media_payload(entry)
            continue
        kept.append(entry)

    if limit_count > 0 and len(kept) > limit_count:
        for entry in kept[: len(kept) - limit_count]:
            delete_media_payload(entry)
        kept = kept[-limit_count:]

    byte_limit = pending_media_max_total_bytes() if max_total_bytes is None else int(max_total_bytes)
    if byte_limit > 0:
        while kept and sum(media_payload_size(entry) for entry in kept) > byte_limit:
            removed = kept.pop(0)
            delete_media_payload(removed)
    return kept


def data_url_from_payload(media_bytes: bytes, *, mimetype: str, filename: str) -> str:
    encoded = base64.b64encode(bytes(media_bytes or b"")).decode("ascii")
    return f"data:{mimetype};filename={filename};base64,{encoded}"
