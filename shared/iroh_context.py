# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Native attachment references at the existing application context boundary."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from shared.session_transport import SessionDenied

MAX_ATTACHMENTS = 64


def _items(context):
    if not isinstance(context, list) or len(context) > 256:
        raise ValueError("invalid attachment context")
    total = 0
    for item in context:
        if not isinstance(item, dict):
            raise ValueError("invalid attachment context item")
        attachments = item.get("attachments", [])
        if not isinstance(attachments, list):
            raise ValueError("invalid attachment list")
        total += len(attachments)
        if total > MAX_ATTACHMENTS or any(not isinstance(attachment, dict) for attachment in attachments):
            raise ValueError("attachment context exceeds its bound")
        yield item, attachments


def base64_blocks(encoded: str):
    if not isinstance(encoded, str) or len(encoded) > (1024 * 1024 * 1024 + 2) // 3 * 4:
        raise ValueError("encoded attachment exceeds its bound")
    for offset in range(0, len(encoded), 65536):
        part = encoded[offset:offset + 65536]
        if "=" in part and offset + len(part) != len(encoded):
            raise ValueError("attachment has trailing encoded data")
        data = base64.b64decode(part, validate=True)
        if data:
            yield data


def needs_file_capability(context: list) -> bool:
    return any(any(attachment.get(key) is not None for key in ("file_ref", "path", "data"))
        for _item, attachments in _items(context) for attachment in attachments)


async def outgoing_context(files, context: list) -> list:
    result = []
    for item, attachments in _items(context):
        rewritten = []
        for attachment in attachments:
            converted = dict(attachment)
            descriptor = converted.get("file_ref")
            if descriptor is None and (converted.get("data") is not None or converted.get("path")):
                filename = converted.get("filename") or "attachment.bin"
                mime = converted.get("mimetype") or "application/octet-stream"
                if converted.get("path"):
                    from shared.secure_storage import iter_secure_file
                    source = Path(converted["path"])
                    descriptor = await files.stage_reader(lambda: iter_secure_file(source, maximum_bytes=1024 * 1024 * 1024),
                        filename=filename, mime_type=mime)
                else:
                    encoded = converted["data"]
                    descriptor = await files.stage_reader(lambda: base64_blocks(encoded), filename=filename, mime_type=mime)
            if descriptor is not None:
                status = await files.send(descriptor)
                if status["phase"] != "committed":
                    raise ConnectionError("attachment did not receive a durable receipt")
                for key in ("data", "path", "url", "content"):
                    converted.pop(key, None)
                converted.update(file_ref=descriptor, filename=descriptor["filename"],
                    mimetype=descriptor["mime_type"], size_bytes=descriptor["total"])
            rewritten.append(converted)
        result.append(dict(item, attachments=rewritten) if "attachments" in item else dict(item))
    return result


async def incoming_context(files, context: list) -> list:
    from shared.openclaw_gateway import _get_temp_media_dir
    authority = [files.binding.endpoint_id, files.binding.device_id, files.binding.owner_key,
        files.binding.canonical_user_id, files.binding.conversation_key, files.binding.authorization_epoch]
    import json
    scope = hashlib.sha256(json.dumps(authority, separators=(",", ":")).encode()).hexdigest()
    directory = Path(os.environ["AUTOYOU_TEST_ROOT"]) / "autoyou_media" if os.environ.get("AUTOYOU_TEST_ROOT") else Path(_get_temp_media_dir())
    directory = directory / "iroh" / scope
    result = []
    for item, attachments in _items(context):
        rewritten = []
        for attachment in attachments:
            if attachment.get("path"):
                raise SessionDenied("a peer attachment cannot name a local server path")
            converted = dict(attachment)
            descriptor = converted.pop("file_ref", None)
            if descriptor is not None:
                if any(converted.get(key) for key in ("data", "url", "content")):
                    raise SessionDenied("attachment reference contains conflicting content")
                promoted = await files.promote_attachment(descriptor, directory=directory)
                converted.update(promoted)
            rewritten.append(converted)
        result.append(dict(item, attachments=rewritten) if "attachments" in item else dict(item))
    return result
