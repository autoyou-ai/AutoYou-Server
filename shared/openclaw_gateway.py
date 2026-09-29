# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-00c4a401c91482d3bf22e95d

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import base64
import logging
import mimetypes
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from shared.secure_storage import (
    FILE_HEADER as SPM_FILE_HEADER,
    read_secure_file,
    secure_storage_enabled,
    write_secure_file,
)

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-00c4a401c91482d3bf22e95d"


logger = logging.getLogger("shared.openclaw_gateway")

_RESPONSE_IMAGE_MIME_TYPES = frozenset(
    {
        "image/gif",
        "image/heic",
        "image/heif",
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
    }
)
_RESPONSE_FILE_MIME_TYPES = frozenset(
    {
        "application/json",
        "application/pdf",
        "text/csv",
        "text/html",
        "text/markdown",
        "text/plain",
    }
)
_TEXT_FALLBACK_MIME_TYPES = frozenset(
    {
        "application/json",
        "text/csv",
        "text/html",
        "text/markdown",
        "text/plain",
    }
)
_RESPONSE_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_RESPONSE_FILE_MAX_BYTES = 5 * 1024 * 1024
_TEXT_ATTACHMENT_MAX_CHARS = 20000
_VOICE_REPLY_STYLE_HINT = (
    "The user's message was transcribed from speech. Reply for spoken playback: "
    "keep it brief and natural, use plain text only, and avoid markdown, bullet points, emoji, or code fences."
)


def _header_session_key(session_id: str) -> str:
    return str(session_id or "autoyou-session").strip() or "autoyou-session"


def _safe_path_component(value: Optional[str], fallback: str) -> str:
    raw = str(value or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    if not cleaned:
        cleaned = fallback
    return cleaned[:80]


def _get_temp_media_dir() -> str:
    base = os.path.join(tempfile.gettempdir(), "autoyou_media")
    Path(base).mkdir(parents=True, exist_ok=True)
    return base


def safe_filename(filename: Optional[str], mimetype_hint: Optional[str]) -> str:
    name = (filename or "attachment").strip() or "attachment"
    base = os.path.basename(name.replace("\\", "/"))
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", base).strip(" .")
    if not base:
        base = "attachment"
    stem = os.path.splitext(base)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", stem):
        base = f"_{base}"
    cur_ext = os.path.splitext(base)[1]

    def _preferred_extension(mt: Optional[str]) -> str:
        mapping = {
            "application/json": ".json",
            "application/pdf": ".pdf",
            "audio/mpeg": ".mp3",
            "image/gif": ".gif",
            "image/heic": ".heic",
            "image/heif": ".heif",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "text/csv": ".csv",
            "text/html": ".html",
            "text/markdown": ".md",
            "text/plain": ".txt",
            "video/mp4": ".mp4",
        }
        if not mt:
            return ""
        return mapping.get(mt.lower(), mimetypes.guess_extension(mt) or "")

    if not cur_ext and mimetype_hint:
        ext = _preferred_extension(mimetype_hint)
        if ext:
            base = f"{base}{ext}"
    return base


def write_bytes_to_temp(
    data_bytes: bytes,
    *,
    filename: str,
    source: Optional[str],
    user_id: str,
    session_id: str,
) -> Dict[str, Any]:
    base_dir = _get_temp_media_dir()
    base_dir_abs = os.path.abspath(base_dir)
    sub_dir_parts = [base_dir_abs]
    if source:
        sub_dir_parts.append(_safe_path_component(source, "unknown_source"))
    sub_dir_parts.append(_safe_path_component(user_id, "unknown_user"))
    sub_dir_parts.append(_safe_path_component(session_id, "unknown_session"))
    target_dir = os.path.abspath(os.path.join(*sub_dir_parts))
    if os.path.commonpath([base_dir_abs, target_dir]) != base_dir_abs:
        raise ValueError("Resolved target directory escapes base temp media directory")
    Path(target_dir).mkdir(parents=True, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(filename))[0]
    ext = os.path.splitext(os.path.basename(filename))[1]
    target_path = os.path.join(target_dir, os.path.basename(filename))
    suffix = 1
    while os.path.exists(target_path):
        target_path = os.path.join(target_dir, f"{base_name}_{suffix}{ext}")
        suffix += 1

    if secure_storage_enabled():
        write_secure_file(target_path, data_bytes)
    else:
        with open(target_path, "wb") as handle:
            handle.write(data_bytes)

    size_bytes = len(data_bytes)
    logger.info("Pre-saved attachment to temp: %s (%s bytes)", target_path, size_bytes)
    return {
        "path": target_path,
        "filename": os.path.basename(target_path),
        "size_bytes": size_bytes,
    }


def extract_attachments_from_context(context: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not context:
        return items
    for context_item in context:
        try:
            for att in context_item.get("attachments") or []:
                if not isinstance(att, dict):
                    continue
                filename = safe_filename(att.get("filename"), att.get("mimetype"))
                if att.get("path") or att.get("data") or att.get("url") or att.get("content"):
                    normalized = dict(att)
                    normalized["filename"] = filename
                    items.append(normalized)
        except Exception:
            continue
    return items


def derive_context_source_hint(
    context: Optional[List[Dict[str, Any]]],
    metadata: Optional[Dict[str, Any]],
) -> Optional[str]:
    try:
        for context_item in context or []:
            if isinstance(context_item, dict) and context_item.get("source"):
                return str(context_item.get("source"))
    except Exception:
        pass
    try:
        payload = metadata or {}
        return str(payload.get("client") or payload.get("source") or "") or None
    except Exception:
        return None


def attachment_to_path(
    att: Dict[str, Any],
    *,
    source: Optional[str],
    user_id: str,
    session_id: str,
) -> Dict[str, Any]:
    try:
        if not isinstance(att, dict):
            return {}
        mimetype_hint = att.get("mimetype")
        existing_path = att.get("path")
        filename_hint = att.get("filename") or existing_path
        filename = safe_filename(filename_hint, mimetype_hint)
        if existing_path and os.path.exists(existing_path):
            normalized_mimetype = _normalize_attachment_mimetype(
                {
                    "mimetype": mimetype_hint,
                    "path": existing_path,
                    "filename": filename,
                }
            ) or None
            cleaned: Dict[str, Any] = {
                "path": existing_path,
                "filename": filename,
                "mimetype": normalized_mimetype,
            }
            try:
                cleaned["size_bytes"] = os.path.getsize(existing_path)
            except Exception:
                pass
            for key in ("caption", "title", "note_title", "note_content", "content", "tags", "category", "meta"):
                if key in att:
                    cleaned[key] = att[key]
            return cleaned

        b64 = att.get("data")
        if isinstance(b64, str) and b64:
            try:
                data_bytes = base64.b64decode(b64, validate=False)
                written = write_bytes_to_temp(
                    data_bytes,
                    filename=filename,
                    source=source,
                    user_id=user_id,
                    session_id=session_id,
                )
                extras = {
                    key: att[key]
                    for key in ("caption", "title", "note_title", "note_content", "content", "tags", "category", "meta")
                    if key in att
                }
                normalized_mimetype = _normalize_attachment_mimetype(
                    {
                        "mimetype": mimetype_hint,
                        "path": written.get("path"),
                        "filename": filename,
                    }
                ) or None
                return {**written, "filename": filename, "mimetype": normalized_mimetype, **extras}
            except Exception:
                pass

        url = att.get("url")
        if isinstance(url, str) and url.startswith("data:"):
            try:
                comma_idx = url.find(",")
                if comma_idx != -1:
                    header = url[5:comma_idx]
                    data_bytes = base64.b64decode(url[comma_idx + 1 :], validate=False)
                    written = write_bytes_to_temp(
                        data_bytes,
                        filename=filename,
                        source=source,
                        user_id=user_id,
                        session_id=session_id,
                    )
                    if not mimetype_hint:
                        mimetype_hint = header.split(";")[0] or None
                    extras = {
                        key: att[key]
                        for key in ("caption", "title", "note_title", "note_content", "content", "tags", "category", "meta")
                        if key in att
                    }
                    normalized_mimetype = _normalize_attachment_mimetype(
                        {
                            "mimetype": mimetype_hint,
                            "path": written.get("path"),
                            "filename": filename,
                        }
                    ) or None
                    return {**written, "filename": filename, "mimetype": normalized_mimetype, **extras}
            except Exception:
                pass

        normalized_mimetype = _normalize_attachment_mimetype(
            {
                "mimetype": mimetype_hint,
                "filename": filename,
                "url": att.get("url"),
            }
        ) or None
        cleaned = {
            "filename": filename,
            "mimetype": normalized_mimetype,
        }
        if url:
            cleaned["url"] = url
        for key in ("caption", "title", "note_title", "note_content", "content", "tags", "category", "meta"):
            if key in att:
                cleaned[key] = att[key]
        return cleaned
    except Exception:
        return {}


def rewrite_context_attachments_to_paths(
    context: Optional[List[Dict[str, Any]]],
    *,
    source: Optional[str],
    user_id: str,
    session_id: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, int]:
    forward_context: List[Dict[str, Any]] = []
    rewritten_flat_attachments: List[Dict[str, Any]] = []
    path_saved_count = 0
    path_skipped_count = 0
    for context_item in context or []:
        if not isinstance(context_item, dict):
            continue
        rewritten = dict(context_item)
        rewritten_attachments: List[Dict[str, Any]] = []
        for att in context_item.get("attachments") or []:
            converted = attachment_to_path(
                att,
                source=source,
                user_id=user_id,
                session_id=session_id,
            )
            if converted:
                converted.pop("data", None)
                rewritten_attachments.append(converted)
                rewritten_flat_attachments.append(converted)
                if converted.get("path"):
                    path_saved_count += 1
                else:
                    path_skipped_count += 1
            else:
                path_skipped_count += 1
        rewritten["attachments"] = rewritten_attachments
        forward_context.append(rewritten)
    return forward_context, rewritten_flat_attachments, path_saved_count, path_skipped_count


def summarize_attachments(
    attachments: List[Dict[str, Any]],
    *,
    path_saved_count: int = 0,
    path_skipped_count: int = 0,
) -> Dict[str, Any]:
    try:
        mime_set = set()
        total_bytes = 0
        for att in attachments:
            mime_set.add((att.get("mimetype") or "unknown").lower())
            try:
                total_bytes += int(att.get("size_bytes", 0) or 0)
            except Exception:
                pass
        return {
            "count": len(attachments),
            "mimetypes": sorted(mime_set),
            "total_size_bytes": total_bytes,
            "path_saved_count": path_saved_count,
            "path_skipped_count": path_skipped_count,
        }
    except Exception:
        return {
            "count": 0,
            "mimetypes": [],
            "total_size_bytes": 0,
            "path_saved_count": path_saved_count,
            "path_skipped_count": path_skipped_count,
        }


def _normalize_attachment_mimetype(att: Dict[str, Any]) -> str:
    hint = str(att.get("mimetype") or "").strip().lower()
    if hint:
        if hint == "image/jpg":
            return "image/jpeg"
        return hint
    filename = str(att.get("filename") or att.get("path") or att.get("url") or "").strip()
    guessed, _ = mimetypes.guess_type(filename)
    # from __debug_provenance_h__ import revenue
    return (guessed or "").lower()


def _load_attachment_bytes(att: Dict[str, Any]) -> Tuple[Optional[bytes], Optional[str]]:
    path_value = att.get("path")
    if isinstance(path_value, str) and path_value and os.path.exists(path_value):
        try:
            path = Path(path_value)
            raw = path.read_bytes()
            return (read_secure_file(path) if raw.startswith(SPM_FILE_HEADER) else raw), None
        except Exception as exc:
            return None, f"failed to read {os.path.basename(path_value)}: {exc}"

    data_value = att.get("data")
    if isinstance(data_value, str) and data_value:
        try:
            return base64.b64decode(data_value, validate=False), None
        except Exception as exc:
            return None, f"failed to decode base64 attachment: {exc}"

    url_value = att.get("url")
    if isinstance(url_value, str) and url_value.startswith("data:"):
        try:
            comma_idx = url_value.find(",")
            if comma_idx == -1:
                return None, "invalid data URL attachment"
            return base64.b64decode(url_value[comma_idx + 1 :], validate=False), None
        except Exception as exc:
            return None, f"failed to decode data URL attachment: {exc}"

    return None, "no inline or local attachment data"


def _build_attachment_part(att: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    filename = safe_filename(att.get("filename"), att.get("mimetype"))
    mimetype = _normalize_attachment_mimetype(att)
    if not mimetype:
        return None, f"{filename} (missing MIME type)"

    remote_url = att.get("url")
    if (
        isinstance(remote_url, str)
        and remote_url.startswith(("http://", "https://"))
        and "path" not in att
        and "data" not in att
    ):
        if mimetype in _RESPONSE_IMAGE_MIME_TYPES:
            return {
                "type": "input_image",
                "source": {"type": "url", "url": remote_url},
            }, None
        if mimetype in _RESPONSE_FILE_MIME_TYPES:
            return {
                "type": "input_file",
                "source": {"type": "url", "url": remote_url},
            }, None
        return None, f"{filename} ({mimetype}; unsupported by OpenClaw responses)"

    data_bytes, error = _load_attachment_bytes(att)
    if data_bytes is None:
        return None, f"{filename} ({error})"

    encoded = base64.b64encode(data_bytes).decode("ascii")
    if mimetype in _RESPONSE_IMAGE_MIME_TYPES:
        if len(data_bytes) > _RESPONSE_IMAGE_MAX_BYTES:
            return None, f"{filename} ({mimetype}; exceeds 10MB image limit)"
        return {
            "type": "input_image",
            "source": {
                "type": "base64",
                "media_type": mimetype,
                "data": encoded,
            },
        }, None
    if mimetype in _RESPONSE_FILE_MIME_TYPES:
        if len(data_bytes) > _RESPONSE_FILE_MAX_BYTES:
            return None, f"{filename} ({mimetype}; exceeds 5MB file limit)"
        return {
            "type": "input_file",
            "source": {
                "type": "base64",
                "media_type": mimetype,
                "data": encoded,
                "filename": filename,
            },
        }, None
    return None, f"{filename} ({mimetype}; unsupported by OpenClaw responses)"


def _attachment_chat_fallback_segment(att: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    filename = safe_filename(att.get("filename"), att.get("mimetype"))
    mimetype = _normalize_attachment_mimetype(att)
    if mimetype not in _TEXT_FALLBACK_MIME_TYPES:
        return None, f"{filename} ({mimetype or 'unknown'}; cannot inline into chat completions)"

    data_bytes, error = _load_attachment_bytes(att)
    if data_bytes is None:
        return None, f"{filename} ({error})"

    text = data_bytes.decode("utf-8", errors="replace")
    if len(text) > _TEXT_ATTACHMENT_MAX_CHARS:
        text = text[:_TEXT_ATTACHMENT_MAX_CHARS] + "\n...[truncated]"
    return (
        f'<attachment name="{filename}" mime="{mimetype}">\n{text}\n</attachment>',
        None,
    )


def _collect_text_fragments(value: Any, fragments: List[str]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            fragments.append(text)
        return
    if isinstance(value, list):
        for item in value:
            _collect_text_fragments(item, fragments)
        return
    if not isinstance(value, dict):
        return

    for key in ("message", "content", "text", "output_text", "value"):
        if key in value:
            _collect_text_fragments(value.get(key), fragments)
            return


def _normalize_text_content(value: Any) -> str:
    fragments: List[str] = []
    _collect_text_fragments(value, fragments)
    if not fragments:
        return ""
    ordered: List[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        normalized = fragment.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return "\n".join(ordered).strip()


def _is_voice_message(metadata: Optional[Dict[str, Any]]) -> bool:
    payload = metadata or {}
    return (
        str(payload.get("source", "")).lower() in ("voice_call", "voice")
        or bool(payload.get("is_transcription"))
        or bool(payload.get("is_voice"))
    )


def build_openclaw_user_text(
    message: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    skipped_attachments: Optional[List[str]] = None,
) -> str:
    raw_message = str(message or "").strip()
    if _is_voice_message(metadata):
        message_text = (
            f"[voice transcript] {raw_message or ' '}\n\n{_VOICE_REPLY_STYLE_HINT}"
        ).strip()
    else:
        message_text = raw_message
    skipped = [item for item in (skipped_attachments or []) if item]
    if skipped:
        message_text = (
            f"{message_text}\n\n"
            "These attachments were provided but could not be forwarded directly to OpenClaw: "
            + "; ".join(skipped)
        ).strip()
    return (
        "This is a direct one-to-one user message delivered by AutoYou. "
        "Reply to the user normally; do not output NO_REPLY unless the user explicitly asks you not to respond."
        f"\n\n{message_text}"
    ).strip()


def _extract_responses_text(data: Dict[str, Any]) -> str:
    top_level = _normalize_text_content(data.get("output_text"))
    if top_level:
        return top_level

    output = data.get("output") or []
    for item in output:
        if not isinstance(item, dict):
            continue
        text = _normalize_text_content(item.get("content") or item)
        if text:
            return text

    return _normalize_text_content(data.get("content"))


def _extract_chat_completions_text(data: Dict[str, Any]) -> str:
    for choice in data.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        text = _normalize_text_content(
            choice.get("message")
            or choice.get("delta")
            or choice.get("content")
            or choice.get("text")
        )
        if text:
            return text

    return _normalize_text_content(
        data.get("message")
        or data.get("content")
        or data.get("text")
    )


def normalize_openclaw_model(model: Optional[str]) -> str:
    normalized = str(model or "").strip()
    if not normalized or normalized in {"default", "openclaw/default"}:
        return "openclaw"
    if normalized.startswith("openclaw:"):
        agent_id = normalized.split(":", 1)[1].strip()
        return "openclaw" if not agent_id or agent_id == "default" else f"openclaw/{agent_id}"
    return normalized


def normalize_openai_compatible_base(api_base: str) -> str:
    """Return an OpenAI-compatible API base without a terminal ``/v1``.

    Configuration accepts either a server root (``https://host``) or the
    conventional OpenAI base (``https://host/v1``).  Keeping one normalized
    representation prevents callers from accidentally repeating the version
    segment while preserving any path prefix such as ``/openai``.
    """
    base = str(api_base or "").strip()
    if not base:
        return ""

    try:
        parsed = urlsplit(base)
    except ValueError:
        # Preserve the old string behavior for malformed values.  The HTTP
        # client will report the configuration error at call time.
        return base.rstrip("/")

    path = parsed.path.rstrip("/")
    if path.casefold().endswith("/v1"):
        path = path[:-3].rstrip("/")

    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def build_openai_compatible_url(api_base: str, endpoint: str) -> str:
    """Build an OpenAI-compatible endpoint URL from a configured base.

    ``api_base`` may be either a server root or a URL ending in ``/v1``.
    ``endpoint`` is a relative endpoint such as ``responses`` or
    ``chat/completions``.
    """
    endpoint_path = str(endpoint or "").strip().lstrip("/")
    if not endpoint_path:
        raise ValueError("OpenAI-compatible endpoint is required")

    normalized_base = normalize_openai_compatible_base(api_base)
    try:
        parsed = urlsplit(normalized_base)
    except ValueError:
        return f"{normalized_base.rstrip('/')}/v1/{endpoint_path}"

    base_path = parsed.path.rstrip("/")
    endpoint_path = f"/v1/{endpoint_path}"
    final_path = f"{base_path}{endpoint_path}" if base_path else endpoint_path
    return urlunsplit((parsed.scheme, parsed.netloc, final_path, parsed.query, ""))


async def call_openclaw_gateway(
    *,
    api_base: str,
    model: str,
    message: str,
    session_id: str,
    token: str = "",
    context: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    timeout: float = 120.0,
    on_chunk: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> Optional[Dict[str, Any]]:
    session_key = _header_session_key(session_id)
    attachments = extract_attachments_from_context(context)
    response_parts: List[Dict[str, Any]] = []
    skipped_attachments: List[str] = []
    for att in attachments:
        part, skipped_reason = _build_attachment_part(att)
        if part:
            response_parts.append(part)
        elif skipped_reason:
            skipped_attachments.append(skipped_reason)

    message_text = build_openclaw_user_text(
        message,
        metadata=metadata,
        skipped_attachments=skipped_attachments,
    )

    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "x-openclaw-session-key": session_key,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    model_name = normalize_openclaw_model(model)
    use_responses = True
    if response_parts:
        url = build_openai_compatible_url(api_base, "responses")
        payload: Dict[str, Any] = {
            "model": model_name,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": message_text}, *response_parts],
                }
            ],
            "stream": False,
            "user": session_key,
        }
    else:
        url = build_openai_compatible_url(api_base, "responses")
        payload = {
            "model": model_name,
            "input": message_text,
            "stream": False,
            "user": session_key,
        }

    async def _post_json(request_url: str, request_payload: Dict[str, Any]) -> Tuple[int, Any]:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as sess:
            async with sess.post(request_url, json=request_payload, headers=headers) as resp:
                if resp.status >= 400:
                    return resp.status, await resp.text()
                return resp.status, await resp.json()

    try:
        status, payload_or_error = await _post_json(url, payload)
    except asyncio.TimeoutError:
        logger.error("OpenClaw request timed out [%s] session=%s", url, session_key)
        return None
    except Exception as exc:
        logger.error("OpenClaw request failed [%s] session=%s: %s", url, session_key, exc)
        return None

    if use_responses and status in (404, 405):
        fallback_segments: List[str] = []
        fallback_notices = list(skipped_attachments)
        for att in attachments:
            segment, notice = _attachment_chat_fallback_segment(att)
            if segment:
                fallback_segments.append(segment)
            elif notice:
                fallback_notices.append(notice)

        if attachments and not fallback_segments:
            logger.warning(
                "OpenClaw /v1/responses unavailable for session=%s; no safe chat-completions fallback exists for %s attachment(s)",
                session_key,
                len(attachments),
            )
            warning_text = (
                "This OpenClaw gateway does not expose /v1/responses, so image and file attachments "
                "cannot be forwarded from AutoYou in this mode.\n\n"
                "Enable `gateway.http.endpoints.responses.enabled=true` in OpenClaw to use direct "
                "attachment forwarding."
            )
            if fallback_notices:
                warning_text = (
                    f"{warning_text}\n\nDetails: "
                    + "; ".join(fallback_notices)
                )
            return {
                "response": warning_text,
                "agent_name": model_name,
                "finish_reason": "unsupported_attachments",
                "usage": {},
                "provider": "openclaw",
                "openclaw_endpoint": "responses-unavailable",
                "attachments_forwarded": len(response_parts),
                "attachments_skipped": len(fallback_notices),
            }

        fallback_message = message_text
        if fallback_segments:
            fallback_message = (
                f"{fallback_message}\n\nAttached text content:\n"
                + "\n\n".join(fallback_segments)
            ).strip()
        if fallback_notices:
            fallback_message = (
                f"{fallback_message}\n\n"
                "Additional attachments could not be forwarded losslessly because "
                "this OpenClaw gateway does not expose /v1/responses: "
                + "; ".join(fallback_notices)
            ).strip()

        if attachments or fallback_notices:
            logger.warning(
                "OpenClaw /v1/responses unavailable for session=%s; falling back to /v1/chat/completions with attachment degradation",
                session_key,
            )
        else:
            logger.info(
                "OpenClaw /v1/responses not enabled for session=%s; using /v1/chat/completions instead",
                session_key,
            )
        fallback_payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": fallback_message or " "}],
            "stream": False,
            "user": session_key,
        }
        fallback_url = build_openai_compatible_url(api_base, "chat/completions")
        try:
            status, payload_or_error = await _post_json(fallback_url, fallback_payload)
            url = fallback_url
            use_responses = False
        except asyncio.TimeoutError:
            logger.error("OpenClaw request timed out [%s] session=%s", fallback_url, session_key)
            return None
        except Exception as exc:
            logger.error("OpenClaw fallback request failed [%s] session=%s: %s", fallback_url, session_key, exc)
            return None

    if status >= 400:
        if status in (404, 405):
            logger.error(
                "OpenClaw gateway surface unavailable [%s] session=%s. "
                "Enable gateway.http.endpoints.responses.enabled=true or "
                "gateway.http.endpoints.chatCompletions.enabled=true",
                url,
                session_key,
            )
            return {
                "response": (
                    "OpenClaw is reachable, but its OpenAI-compatible HTTP surface is disabled. "
                    "Enable `gateway.http.endpoints.responses.enabled=true` or "
                    "`gateway.http.endpoints.chatCompletions.enabled=true` in OpenClaw."
                ),
                "agent_name": model_name,
                "finish_reason": "http_surface_disabled",
                "usage": {},
                "provider": "openclaw",
                "openclaw_endpoint": "http-surface-unavailable",
                "attachments_forwarded": len(response_parts),
                "attachments_skipped": len(skipped_attachments),
            }
        logger.error(
            "OpenClaw request failed [%s] session=%s status=%s body=%s",
            url,
            session_key,
            status,
            str(payload_or_error)[:500],
        )
        return None

    data = payload_or_error

    try:
        if use_responses:
            reply_text = _extract_responses_text(data)
            if reply_text:
                finish_reason = str(data.get("status") or "completed")
            else:
                reply_text = _extract_chat_completions_text(data)
                choice = (data.get("choices") or [{}])[0]
                finish_reason = str(
                    (choice.get("finish_reason") if isinstance(choice, dict) else None)
                    or data.get("status")
                    or "stop"
                )
        else:
            choice = data["choices"][0]
            reply_text = _extract_chat_completions_text(data)
            finish_reason = str(choice.get("finish_reason") or "stop")
        usage = data.get("usage", {})
        agent_name = str(data.get("model") or model_name)
    except (KeyError, IndexError, TypeError) as exc:
        logger.error("Unexpected OpenClaw response format [%s]: %s raw=%s", url, exc, str(data)[:500])
        return None

    if on_chunk and reply_text:
        try:
            result = on_chunk({"content": reply_text, "type": "content"})
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass

    media_reply_attachments: List[Dict[str, Any]] = []
    try:
        from shared.media_messaging import extract_media_reply_attachments

        media_reply_attachments = extract_media_reply_attachments(data, source="openclaw")
    except Exception:
        media_reply_attachments = []

    return {
        "response": reply_text,
        "agent_name": agent_name,
        "finish_reason": finish_reason,
        "usage": usage,
        "provider": "openclaw",
        "openclaw_endpoint": "responses" if use_responses else "chat.completions",
        "attachments_forwarded": len(response_parts),
        "attachments_skipped": len(skipped_attachments),
        "media_reply_attachments": media_reply_attachments,
    }
