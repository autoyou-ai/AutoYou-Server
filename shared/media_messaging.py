# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import urlparse, unquote
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from shared.secure_storage import FILE_HEADER as SPM_FILE_HEADER, read_secure_file

try:
    from shared.openclaw_gateway import safe_filename
except Exception:  # pragma: no cover - import fallback for packaged runtimes
    def safe_filename(filename: Optional[str], mimetype_hint: Optional[str]) -> str:
        name = os.path.basename(str(filename or "attachment").replace("\\", "/"))
        return name or (mimetypes.guess_extension(str(mimetype_hint or "")) or "attachment")


MEDIA_ATTACHMENT_PREFIXES = ("image/", "video/")
DEFAULT_REMOTE_MEDIA_FETCH_TIMEOUT_SECONDS = 30.0
DEFAULT_MEDIA_ATTACHMENT_MAX_BYTES = 1024 * 1024 * 1024
DEFAULT_MEDIA_REPLY_JSON_STRING_MAX_BYTES = 16 * 1024 * 1024


def is_adts_aac(data: bytes) -> bool:
    """Return whether *data* starts with a complete ADTS AAC frame header."""
    if len(data) < 7 or data[0] != 0xFF or (data[1] & 0xF6) != 0xF0:
        return False
    header_size = 7 if data[1] & 0x01 else 9
    frame_size = ((data[3] & 0x03) << 11) | (data[4] << 3) | ((data[5] & 0xE0) >> 5)
    return header_size <= frame_size <= len(data)


_ATTACHMENT_PLACEHOLDER_RE = re.compile(
    r"^\s*(?:\[attachment(?::[^\]]+)?\]|\(attachment\))\s*$",
    re.IGNORECASE,
)
_CLIENT_ATTACHMENT_FALLBACK_RE = re.compile(
    r"^\s*please\s+review\s+the\s+(?:attached\s+file|\d+\s+attached\s+files|attached\s+files)\.?\s*$",
    re.IGNORECASE,
)


def attachment_mimetype(attachment: Dict[str, Any]) -> str:
    mimetype = str(attachment.get("mimetype") or "").strip().lower()
    if mimetype == "image/jpg":
        return "image/jpeg"
    if mimetype:
        return mimetype
    guessed, _ = mimetypes.guess_type(
        str(attachment.get("filename") or attachment.get("path") or attachment.get("url") or "")
    )
    return (guessed or "application/octet-stream").lower()


def attachment_kind(attachment: Dict[str, Any]) -> str:
    meta = attachment.get("meta") if isinstance(attachment.get("meta"), dict) else {}
    explicit = str(meta.get("kind") or attachment.get("kind") or "").strip().lower()
    if explicit:
        return explicit
    mimetype = attachment_mimetype(attachment)
    if mimetype.startswith("image/"):
        return "image"
    if mimetype.startswith("video/"):
        return "video"
    if mimetype.startswith("audio/"):
        return "voice"
    return "file"


def is_image_or_video_attachment(attachment: Dict[str, Any]) -> bool:
    mimetype = attachment_mimetype(attachment)
    if mimetype.startswith(MEDIA_ATTACHMENT_PREFIXES):
        return True
    return attachment_kind(attachment) in {"image", "camera", "video"}


def _read_env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
        return parsed if parsed >= minimum else default
    except Exception:
        return default


def _media_attachment_max_bytes() -> int:
    return _read_env_int(
        "AUTOYOU_MEDIA_ATTACHMENT_MAX_BYTES",
        DEFAULT_MEDIA_ATTACHMENT_MAX_BYTES,
        minimum=1,
    )


def _media_reply_json_string_max_bytes() -> int:
    return _read_env_int(
        "AUTOYOU_MEDIA_REPLY_JSON_STRING_MAX_BYTES",
        DEFAULT_MEDIA_REPLY_JSON_STRING_MAX_BYTES,
        minimum=0,
    )


def is_media_only_message(message: str, attachments: Iterable[Dict[str, Any]]) -> bool:
    media_attachments = [att for att in attachments if isinstance(att, dict) and is_image_or_video_attachment(att)]
    if not media_attachments:
        return False
    text = str(message or "").strip()
    if not text:
        return True
    if _ATTACHMENT_PLACEHOLDER_RE.match(text):
        return True
    return bool(_CLIENT_ATTACHMENT_FALLBACK_RE.match(text))


def _load_remote_attachment_bytes(url: str, *, max_bytes: int) -> Tuple[Optional[bytes], Optional[str]]:
    try:
        from shared.url_safety import safe_follow_redirects
    except Exception as exc:
        return None, f"cannot validate remote media URL: {exc}"

    try:
        import requests

        headers = {"User-Agent": "AutoYou-media-fetch/1.0"}
        with requests.Session() as session:
            response = safe_follow_redirects(
                session,
                url,
                method="GET",
                timeout=DEFAULT_REMOTE_MEDIA_FETCH_TIMEOUT_SECONDS,
                headers=headers,
            )
            if response.status_code >= 400:
                return None, f"remote media returned HTTP {response.status_code}"
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        return None, f"remote media exceeds {max_bytes} byte limit"
                except Exception:
                    pass
            buffer = io.BytesIO()
            total = 0
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    return None, f"remote media exceeds {max_bytes} byte limit"
                buffer.write(chunk)
            return buffer.getvalue(), None
    except Exception as exc:
        return None, f"failed to fetch remote media: {exc}"


def load_attachment_bytes(attachment: Dict[str, Any]) -> Tuple[Optional[bytes], Optional[str]]:
    max_bytes = _media_attachment_max_bytes()
    path_value = str(attachment.get("path") or "").strip()
    if path_value and os.path.exists(path_value):
        try:
            path = Path(path_value)
            raw = path.read_bytes()
            data = read_secure_file(path) if raw.startswith(SPM_FILE_HEADER) else raw
            if len(data) > max_bytes:
                return None, f"{os.path.basename(path_value)} exceeds {max_bytes} byte limit"
            return data, None
        except OSError as exc:
            return None, f"failed to read {os.path.basename(path_value)}: {exc}"

    data_value = attachment.get("data")
    if isinstance(data_value, str) and data_value.strip():
        payload = data_value.strip()
        if payload.startswith("data:") and "," in payload:
            payload = payload.split(",", 1)[1]
        try:
            data = base64.b64decode(payload, validate=False)
            if len(data) > max_bytes:
                return None, f"inline attachment exceeds {max_bytes} byte limit"
            return data, None
        except Exception as exc:
            return None, f"failed to decode base64 attachment: {exc}"

    url_value = attachment.get("url")
    if isinstance(url_value, str) and url_value.startswith("data:") and "," in url_value:
        try:
            data = base64.b64decode(url_value.split(",", 1)[1], validate=False)
            if len(data) > max_bytes:
                return None, f"data URL attachment exceeds {max_bytes} byte limit"
            return data, None
        except Exception as exc:
            return None, f"failed to decode data URL attachment: {exc}"

    if isinstance(url_value, str) and url_value.startswith(("http://", "https://")):
        return _load_remote_attachment_bytes(url_value, max_bytes=max_bytes)

    return None, "no local or inline attachment data"


def _filename_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        name = os.path.basename(unquote(parsed.path or ""))
        return name or "attachment"
    except Exception:
        return "attachment"


def _string_payload_looks_like_media(value: str) -> bool:
    raw = str(value or "").strip()
    if raw.startswith("data:image/") or raw.startswith("data:video/"):
        return True
    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        guessed, _ = mimetypes.guess_type(parsed.path or "")
        return bool((guessed or "").lower().startswith(MEDIA_ATTACHMENT_PREFIXES))
    return False


def _candidate_from_media_string(value: str, *, source: str, kind_hint: str = "") -> Optional[Dict[str, Any]]:
    raw = str(value or "").strip()
    if not raw:
        return None
    mimetype = ""
    filename = "attachment"
    key = "data"
    stored_value = raw
    if raw.startswith("data:") and "," in raw:
        header = raw.split(",", 1)[0]
        mimetype = header[5:].split(";", 1)[0].strip().lower()
        filename_match = re.search(r";filename=([^;]+)", header, flags=re.IGNORECASE)
        filename = filename_match.group(1) if filename_match else (
            "image.png" if mimetype.startswith("image/") else "video.mp4"
        )
        key = "url"
    elif raw.startswith(("http://", "https://")):
        key = "url"
        filename = _filename_from_url(raw)
        mimetype = attachment_mimetype({"url": raw, "filename": filename})
    else:
        mimetype = "video/mp4" if kind_hint == "video" else "image/png"
        filename = "video.mp4" if kind_hint == "video" else "image.png"

    if kind_hint and not mimetype.startswith((f"{kind_hint}/",)):
        if kind_hint == "image" and not mimetype.startswith("image/"):
            mimetype = "image/png"
        elif kind_hint == "video" and not mimetype.startswith("video/"):
            mimetype = "video/mp4"

    candidate = {
        "filename": safe_filename(filename, mimetype),
        "mimetype": mimetype,
        key: stored_value,
        "size_bytes": 0,
        "meta": {
            "kind": "video" if mimetype.startswith("video/") else "image",
            "role": "media_reply",
            "source": source,
        },
    }
    return candidate if is_image_or_video_attachment(candidate) else None


def _media_reference_value(raw: Any) -> Any:
    if isinstance(raw, dict):
        for key in ("url", "uri", "file_uri", "fileUri", "data", "base64", "b64_json"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return raw


def _inline_data_mapping(value: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for key in ("inlineData", "inline_data"):
        inline = value.get(key)
        if isinstance(inline, dict):
            return inline
    return None


def _file_data_mapping(value: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for key in ("fileData", "file_data"):
        file_data = value.get(key)
        if isinstance(file_data, dict):
            return file_data
    return None


def _candidate_attachment_from_mapping(value: Dict[str, Any], *, source: str) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None

    source_obj = value.get("source") if isinstance(value.get("source"), dict) else None
    inline_data = _inline_data_mapping(value)
    file_data = _file_data_mapping(value)
    path_value = (
        value.get("path")
        or value.get("file_path")
        or value.get("filepath")
        or value.get("local_path")
        or value.get("localFilePath")
    )
    url_value = _media_reference_value(
        value.get("url")
        or value.get("uri")
        or value.get("file_uri")
        or value.get("fileUri")
        or value.get("media_url")
        or value.get("image_url")
        or value.get("video_url")
        or (file_data.get("fileUri") if file_data else None)
        or (file_data.get("file_uri") if file_data else None)
        or (file_data.get("uri") if file_data else None)
    )
    data_value = (
        value.get("data")
        or value.get("data_base64")
        or value.get("base64")
        or value.get("b64_json")
        or value.get("content_base64")
        or (inline_data.get("data") if inline_data else None)
    )
    if source_obj:
        source_type = str(source_obj.get("type") or "").strip().lower()
        if source_type == "url":
            url_value = url_value or source_obj.get("url")
        elif source_type == "base64":
            data_value = data_value or source_obj.get("data")

    type_hint = str(
        value.get("mimetype")
        or value.get("mime_type")
        or value.get("mimeType")
        or value.get("media_type")
        or (inline_data.get("mimeType") if inline_data else None)
        or (inline_data.get("mime_type") if inline_data else None)
        or (file_data.get("mimeType") if file_data else None)
        or (file_data.get("mime_type") if file_data else None)
        or value.get("type")
        or ""
    ).strip().lower()
    kind_hint = str(value.get("kind") or value.get("media_kind") or value.get("media_type") or "").strip().lower()

    mimetype = ""
    if "/" in type_hint:
        mimetype = "image/jpeg" if type_hint == "image/jpg" else type_hint
    elif kind_hint == "image" or type_hint in {"output_image", "image", "input_image", "image_url"}:
        mimetype = str(
            value.get("mimetype") or value.get("mime_type") or value.get("mimeType") or "image/png"
        ).strip().lower()
    elif kind_hint == "video" or type_hint in {"video", "output_video", "video_url"}:
        mimetype = str(
            value.get("mimetype") or value.get("mime_type") or value.get("mimeType") or "video/mp4"
        ).strip().lower()

    filename = (
        value.get("filename")
        or value.get("file_name")
        or value.get("name")
        or (os.path.basename(str(path_value)) if path_value else None)
        or (_filename_from_url(str(url_value)) if url_value else None)
        or ("image.png" if mimetype.startswith("image/") else "video.mp4" if mimetype.startswith("video/") else "attachment")
    )
    if not mimetype:
        mimetype = attachment_mimetype({"filename": filename, "path": path_value, "url": url_value})

    candidate: Dict[str, Any] = {
        "filename": safe_filename(str(filename), mimetype),
        "mimetype": mimetype,
        "meta": {
            "kind": "video" if mimetype.startswith("video/") else "image" if mimetype.startswith("image/") else kind_hint,
            "role": "media_reply",
            "source": source,
        },
    }
    for key, raw in (("path", path_value), ("url", url_value), ("data", data_value)):
        if isinstance(raw, str) and raw.strip():
            candidate[key] = raw.strip()
            break
    if not any(candidate.get(key) for key in ("path", "url", "data")):
        return None
    try:
        candidate["size_bytes"] = int(value.get("size_bytes") or value.get("bytes") or value.get("size") or 0)
    except Exception:
        candidate["size_bytes"] = 0
    if candidate.get("path") and not candidate["size_bytes"]:
        try:
            candidate["size_bytes"] = os.path.getsize(str(candidate["path"]))
        except OSError:
            pass
    if value.get("caption"):
        candidate["caption"] = str(value.get("caption"))
    return candidate if is_image_or_video_attachment(candidate) else None


def extract_media_reply_attachments(
    payload: Any,
    *,
    source: str = "agent_response",
    max_items: int = 8,
) -> List[Dict[str, Any]]:
    """Extract image/video attachments from common agent/backend response shapes."""
    attachments: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    visited: Set[int] = set()

    def _fingerprint(attachment: Dict[str, Any]) -> str:
        for key in ("path", "url", "data"):
            value = attachment.get(key)
            if isinstance(value, str) and value:
                if key == "data":
                    return f"data:{hashlib.sha256(value[:4096].encode('utf-8', errors='ignore')).hexdigest()}"
                return f"{key}:{value}"
        return f"{attachment.get('filename')}:{attachment.get('mimetype')}:{attachment.get('size_bytes')}"

    def _add(candidate: Optional[Dict[str, Any]]) -> None:
        if not candidate or len(attachments) >= max_items:
            return
        fp = _fingerprint(candidate)
        if fp in seen:
            return
        seen.add(fp)
        attachments.append(candidate)

    def _walk_string(value: str, *, key_hint: str = "", depth: int = 0) -> None:
        raw = str(value or "").strip()
        if not raw:
            return
        lowered_key = key_hint.lower()
        kind_hint = "video" if "video" in lowered_key else "image" if "image" in lowered_key else ""
        if _string_payload_looks_like_media(raw) or kind_hint:
            _add(_candidate_from_media_string(raw, source=source, kind_hint=kind_hint))
        if len(attachments) >= max_items:
            return
        if not (raw.startswith("{") or raw.startswith("[")):
            return
        max_json_bytes = _media_reply_json_string_max_bytes()
        if max_json_bytes <= 0 or len(raw.encode("utf-8", errors="ignore")) > max_json_bytes:
            return
        try:
            parsed = json.loads(raw)
        except Exception:
            return
        _walk(parsed, depth + 1)

    def _walk(value: Any, depth: int = 0) -> None:
        if len(attachments) >= max_items or depth > 8:
            return
        if isinstance(value, str):
            _walk_string(value, depth=depth)
            return
        if isinstance(value, (int, float, bool)) or value is None:
            return
        object_id = id(value)
        if object_id in visited:
            return
        visited.add(object_id)

        if isinstance(value, list):
            for item in value:
                _walk(item, depth + 1)
            return

        if not isinstance(value, dict):
            return

        _add(_candidate_attachment_from_mapping(value, source=source))

        for key in ("image", "images", "image_url", "image_urls"):
            raw_value = value.get(key)
            if isinstance(raw_value, str):
                _walk_string(raw_value, key_hint=key, depth=depth + 1)
            elif isinstance(raw_value, list):
                for item in raw_value:
                    if isinstance(item, str):
                        _walk_string(item, key_hint=key, depth=depth + 1)
                    else:
                        _walk(item, depth + 1)

        for key in ("video", "videos", "video_url", "video_urls"):
            raw_value = value.get(key)
            if isinstance(raw_value, str):
                _walk_string(raw_value, key_hint=key, depth=depth + 1)
            elif isinstance(raw_value, list):
                for item in raw_value:
                    if isinstance(item, str):
                        _walk_string(item, key_hint=key, depth=depth + 1)
                    else:
                        _walk(item, depth + 1)

        for response_key in ("functionResponse", "function_response"):
            response_payload = value.get(response_key)
            if isinstance(response_payload, dict):
                _walk(response_payload.get("response", response_payload), depth + 1)

        for key in (
            "media_reply_attachments",
            "autoyou_media_reply",
            "attachments",
            "media_attachments",
            "generated_media",
            "media",
            "files",
            "output",
            "outputs",
            "content",
            "parts",
            "text",
            "message",
            "messages",
            "response",
            "result",
            "data",
            "item",
            "items",
        ):
            if key in value:
                child = value.get(key)
                if isinstance(child, str):
                    _walk_string(child, key_hint=key, depth=depth + 1)
                else:
                    _walk(child, depth + 1)

    _walk(payload)
    return attachments


def media_reply_attachments_from_saved(
    attachments: Iterable[Dict[str, Any]],
    *,
    source: str = "media_echo",
) -> List[Dict[str, Any]]:
    replies: List[Dict[str, Any]] = []
    for attachment in attachments:
        if not isinstance(attachment, dict) or not is_image_or_video_attachment(attachment):
            continue
        mimetype = attachment_mimetype(attachment)
        filename = safe_filename(attachment.get("filename") or attachment.get("path"), mimetype)
        item: Dict[str, Any] = {
            "filename": filename,
            "mimetype": mimetype,
            "size_bytes": int(attachment.get("size_bytes") or 0),
            "meta": {
                **(attachment.get("meta") if isinstance(attachment.get("meta"), dict) else {}),
                "kind": "video" if mimetype.startswith("video/") else "image",
                "role": "media_echo",
                "source": source,
            },
        }
        for key in ("path", "data", "url"):
            value = attachment.get(key)
            if isinstance(value, str) and value:
                item[key] = value
                break
        if item.get("path") and not item["size_bytes"]:
            try:
                item["size_bytes"] = os.path.getsize(str(item["path"]))
            except OSError:
                pass
        replies.append(item)
    return replies


def inline_attachment_for_client(attachment: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(attachment, dict):
        return None
    data_bytes, error = load_attachment_bytes(attachment)
    if data_bytes is None:
        return None
    mimetype = attachment_mimetype(attachment)
    filename = safe_filename(attachment.get("filename") or attachment.get("path"), mimetype)
    meta = dict(attachment.get("meta") or {}) if isinstance(attachment.get("meta"), dict) else {}
    meta.setdefault("kind", "video" if mimetype.startswith("video/") else "image")
    meta.setdefault("role", "media_reply")
    return {
        "filename": filename,
        "mimetype": mimetype,
        "data": base64.b64encode(data_bytes).decode("ascii"),
        "size_bytes": int(attachment.get("size_bytes") or len(data_bytes)),
        "meta": meta,
    }


def inline_context_for_client(
    attachments: Iterable[Dict[str, Any]],
    *,
    source: str = "media_reply",
) -> List[Dict[str, Any]]:
    inline = [
        converted
        for converted in (inline_attachment_for_client(attachment) for attachment in attachments)
        if converted
    ]
    return [{"source": source, "attachments": inline}] if inline else []


def data_url_attachment(attachment: Dict[str, Any]) -> Optional[str]:
    converted = inline_attachment_for_client(attachment)
    if not converted:
        return None
    return f"data:{converted['mimetype']};filename={converted['filename']};base64,{converted['data']}"


def public_media_reply_metadata(attachments: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    items = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        mimetype = attachment_mimetype(attachment)
        items.append(
            {
                "filename": safe_filename(attachment.get("filename") or attachment.get("path"), mimetype),
                "mimetype": mimetype,
                "size_bytes": int(attachment.get("size_bytes") or 0),
                "kind": "video" if mimetype.startswith("video/") else "image",
            }
        )
    return {
        "count": len(items),
        "items": items,
    }
