# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-ec48f7bb5b84667662da6d4a

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-ec48f7bb5b84667662da6d4a"


import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from shared.secure_storage import write_secure_file

MAX_MESSAGE_CHARS = 6000
SYSTEM_PROMPT = (
    "You are a local personal assistant imitating the writing style of the "
    "account owner from private message examples. Keep responses natural, "
    "context-aware, and concise."
)

_EXPORT_LINE_PATTERNS = (
    re.compile(
        r"^\[(?P<date>[^\]]+)\]\s*(?P<sender>[^:]+):\s*(?P<body>.*)$"
    ),
    re.compile(
        r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)\s+-\s+(?P<sender>[^:]+):\s*(?P<body>.*)$"
    ),
    re.compile(
        r"^(?P<date>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)\s+-\s+(?P<sender>[^:]+):\s*(?P<body>.*)$"
    ),
)

_MEDIA_OR_SYSTEM_FRAGMENTS = (
    "messages and calls are end-to-end encrypted",
    "omitted",
    "<media omitted>",
    "image omitted",
    "video omitted",
    "audio omitted",
    "sticker omitted",
    "missed voice call",
    "missed video call",
    "deleted this message",
)

@dataclass(frozen=True)
class ParsedMessage:
    sender: str
    text: str
    timestamp: Optional[str] = None
    from_me: Optional[bool] = None
    chat_name: Optional[str] = None
    source: str = "upload"

@dataclass(frozen=True)
class DatasetBuildResult:
    samples: List[Dict[str, Any]]
    message_count: int
    assistant_message_count: int
    warnings: List[str]

def normalize_text(value: Any) -> str:
    text = str(value or "").replace("\ufeff", "").strip()
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS].rstrip()
    return text

def _is_noise_message(text: str) -> bool:
    lowered = normalize_text(text).lower()
    if not lowered:
        return True
    return any(fragment in lowered for fragment in _MEDIA_OR_SYSTEM_FRAGMENTS)

def _normalize_me_names(me_name: Optional[str | Sequence[str]]) -> set[str]:
    if me_name is None:
        return {"me", "you", "self", "owner", "assistant"}
    if isinstance(me_name, str):
        parts = re.split(r"[,;\n]", me_name)
    else:
        parts = [str(item) for item in me_name]
    normalized = {part.strip().casefold() for part in parts if str(part).strip()}
    normalized.update({"me", "you", "self", "owner", "assistant"})
    return normalized

def _parse_export_line(line: str) -> Optional[ParsedMessage]:
    for pattern in _EXPORT_LINE_PATTERNS:
        match = pattern.match(line)
        if not match:
            continue
        sender = normalize_text(match.group("sender"))
        body = normalize_text(match.group("body"))
        if not sender or _is_noise_message(body):
            return None
        return ParsedMessage(
            sender=sender,
            text=body,
            timestamp=normalize_text(match.group("date")) or None,
            source="whatsapp_export",
        )
    return None

def parse_whatsapp_export_text(text: str) -> List[ParsedMessage]:
    messages: List[ParsedMessage] = []
    current: Optional[ParsedMessage] = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip("\ufeff")
        if not line.strip():
            continue
        parsed = _parse_export_line(line)
        if parsed is not None:
            if current is not None:
                messages.append(current)
            current = parsed
            continue
        if current is None:
            continue
        continuation = normalize_text(line)
        if continuation and not _is_noise_message(continuation):
            current = ParsedMessage(
                sender=current.sender,
                text=normalize_text(f"{current.text}\n{continuation}"),
                timestamp=current.timestamp,
                from_me=current.from_me,
                chat_name=current.chat_name,
                source=current.source,
            )
    if current is not None:
        messages.append(current)
    return messages

def parse_live_whatsapp_log(items: Iterable[Dict[str, Any]]) -> List[ParsedMessage]:
    messages: List[ParsedMessage] = []
    for item in items or []:
        text = normalize_text(item.get("message"))
        if _is_noise_message(text):
            continue
        timestamp = item.get("timestamp")
        timestamp_text = None
        if isinstance(timestamp, (int, float)):
            try:
                timestamp_text = datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat().replace("+00:00", "Z")
            except Exception:
                timestamp_text = None
        elif timestamp:
            timestamp_text = normalize_text(timestamp)
        messages.append(
            ParsedMessage(
                sender=normalize_text(item.get("contact_name") or item.get("chat_name") or "me") or "me",
                text=text,
                timestamp=timestamp_text,
                from_me=True,
                chat_name=normalize_text(item.get("chat_name")) or None,
                source="whatsapp_live",
            )
        )
    return messages

def _timestamp_from_unix(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return normalize_text(value) or None


def parse_live_telegram_user_log(items: Iterable[Dict[str, Any]]) -> List[ParsedMessage]:
    """Parse the in-memory, owner-only Saved Messages log exposed by the runtime."""
    messages: List[ParsedMessage] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        # Defense in depth: the runtime rejects these before logging, and tuning
        # must never turn an explicitly non-owner or forwarded entry into data.
        if item.get("saved_messages") is not True and item.get("is_saved_messages") is not True:
            continue
        if item.get("owner_scoped") is not True:
            continue
        if any(item.get(key) for key in ("forwarded", "is_forwarded", "forwarded_from", "forward_from")):
            continue
        text = normalize_text(item.get("message") or item.get("text") or item.get("body"))
        if _is_noise_message(text):
            continue
        if text.lower().startswith(("/pair", "/otp_pair", "/autopair")):
            continue
        direction = str(item.get("direction") or "").strip().lower()
        from_me = direction in {"assistant", "outbound", "sent", "server"}
        messages.append(
            ParsedMessage(
                sender="assistant" if from_me else "me",
                text=text,
                timestamp=_timestamp_from_unix(item.get("timestamp") or item.get("date")),
                from_me=from_me,
                chat_name="Telegram Saved Messages",
                source="telegram_user_live",
            )
        )
    return messages


def parse_whatsapp_history_dump(payload: Dict[str, Any]) -> List[ParsedMessage]:
    messages: List[ParsedMessage] = []
    rows = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return messages

    for item in rows:
        if not isinstance(item, dict):
            continue
        text = normalize_text(item.get("body") or item.get("message") or item.get("text"))
        if _is_noise_message(text):
            continue
        chat = item.get("chat") if isinstance(item.get("chat"), dict) else {}
        chat_name = normalize_text(chat.get("name")) or normalize_text(chat.get("id")) or None
        is_group = bool(chat.get("isGroup") or chat.get("is_group"))
        from_me = bool(item.get("fromMe") or item.get("from_me"))
        if from_me:
            sender = "me"
        elif is_group:
            sender = normalize_text(item.get("senderName")) or "Group participant"
            text = f"{sender}: {text}" if sender and sender != "Group participant" else text
        else:
            sender = normalize_text(item.get("senderName")) or chat_name or "contact"

        messages.append(
            ParsedMessage(
                sender=sender,
                text=text,
                timestamp=_timestamp_from_unix(item.get("timestamp")),
                from_me=from_me,
                chat_name=chat_name,
                source="whatsapp_history_dump",
            )
        )
    return messages

def parse_jsonl_samples(text: str) -> DatasetBuildResult:
    samples: List[Dict[str, Any]] = []
    warnings: List[str] = []
    message_count = 0
    assistant_count = 0
    for line_no, raw_line in enumerate(str(text or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(f"Line {line_no}: invalid JSONL row: {exc.msg}")
            continue
        messages = payload.get("messages")
        if not isinstance(messages, list):
            warnings.append(f"Line {line_no}: missing messages array")
            continue
        normalized_messages: List[Dict[str, str]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip().lower()
            content = normalize_text(message.get("content"))
            if role not in {"system", "user", "assistant"} or not content:
                continue
            normalized_messages.append({"role": role, "content": content})
            message_count += 1
            if role == "assistant":
                assistant_count += 1
        if any(msg["role"] == "assistant" for msg in normalized_messages):
            sample: Dict[str, Any] = {"messages": normalized_messages}
            raw_images = payload.get("images")
            if raw_images is not None:
                if not isinstance(raw_images, list):
                    warnings.append(f"Line {line_no}: images must be an array of bundled file names")
                else:
                    image_names: List[str] = []
                    for raw_name in raw_images:
                        name = str(raw_name or "").strip().replace("\\", "/")
                        # Image assets are resolved only from this dataset's private
                        # bundle. A bare file name makes traversal impossible and
                        # keeps a portable JSONL manifest.
                        if not name or "/" in name or ":" in name:
                            warnings.append(f"Line {line_no}: image names must be bundled file names")
                            continue
                        image_names.append(name)
                    if image_names:
                        sample["images"] = image_names
            samples.append(sample)
    return DatasetBuildResult(samples=samples, message_count=message_count, assistant_message_count=assistant_count, warnings=warnings)


def _json_content(value: Any) -> str:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, dict):
        return _json_content(value.get("text") or value.get("content") or value.get("body") or value.get("parts"))
    if isinstance(value, list):
        parts = [_json_content(item) for item in value]
        return normalize_text("\n".join(part for part in parts if part))
    return ""


def parse_json_dataset(text: str) -> DatasetBuildResult:
    """Accept common ChatGPT-style and generic role/content JSON exports."""
    try:
        payload = json.loads(str(text or ""))
    except json.JSONDecodeError as exc:
        return DatasetBuildResult(samples=[], message_count=0, assistant_message_count=0, warnings=[f"Invalid JSON: {exc.msg}"])

    records = payload if isinstance(payload, list) else payload.get("conversations") if isinstance(payload, dict) else []
    if isinstance(payload, dict) and not isinstance(records, list):
        records = [payload]
    messages: List[Dict[str, str]] = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        direct = record.get("messages")
        if isinstance(direct, list):
            for message in direct:
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role") or message.get("author", {}).get("role") or "").strip().lower()
                content = _json_content(message.get("content") or message.get("text") or message.get("message"))
                if role in {"system", "user", "assistant"} and content:
                    messages.append({"role": role, "content": content})
        mapping = record.get("mapping")
        if isinstance(mapping, dict):
            ordered = []
            for node in mapping.values():
                if not isinstance(node, dict) or not isinstance(node.get("message"), dict):
                    continue
                message = node["message"]
                try:
                    created_at = float(message.get("create_time") or 0)
                except (TypeError, ValueError):
                    created_at = 0.0
                ordered.append((created_at, message))
            for _created, message in sorted(ordered, key=lambda item: item[0]):
                author = message.get("author") if isinstance(message.get("author"), dict) else {}
                role = str(author.get("role") or "").strip().lower()
                content = _json_content(message.get("content"))
                if role in {"system", "user", "assistant"} and content:
                    messages.append({"role": role, "content": content})
    if not messages:
        return DatasetBuildResult(samples=[], message_count=0, assistant_message_count=0, warnings=["JSON did not contain supported role/content conversation rows."])
    samples = _samples_from_role_messages(messages)
    return DatasetBuildResult(
        samples=samples,
        message_count=len(messages),
        assistant_message_count=sum(1 for message in messages if message["role"] == "assistant"),
        warnings=[] if samples else ["No assistant replies were found in the JSON export."],
    )


def parse_plain_text_dataset(text: str) -> DatasetBuildResult:
    """Turn a text corpus into bounded continuation samples for local training."""
    normalized = normalize_text(text)
    if not normalized:
        return DatasetBuildResult(samples=[], message_count=0, assistant_message_count=0, warnings=["Text file is empty."])
    chunks = [normalize_text(chunk) for chunk in re.split(r"\n\s*\n", normalized) if normalize_text(chunk)]
    samples = []
    for chunk in chunks:
        for start in range(0, len(chunk), MAX_MESSAGE_CHARS):
            content = chunk[start : start + MAX_MESSAGE_CHARS].strip()
            if content:
                samples.append({"messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "Continue in the supplied source's style."}, {"role": "assistant", "content": content}]})
    return DatasetBuildResult(
        samples=samples,
        message_count=len(samples),
        assistant_message_count=len(samples),
        warnings=["Plain text was converted to continuation samples; use conversation exports when speaker roles matter."],
    )

def parse_csv_messages(text: str, *, me_name: Optional[str] = None) -> DatasetBuildResult:
    stream = io.StringIO(str(text or ""))
    reader = csv.DictReader(stream)
    if not reader.fieldnames:
        return DatasetBuildResult(samples=[], message_count=0, assistant_message_count=0, warnings=["CSV header row is required."])

    fields = {field.strip().lower(): field for field in reader.fieldnames if field}
    rows = list(reader)
    warnings: List[str] = []

    if "role" in fields and "content" in fields:
        messages: List[Dict[str, str]] = []
        for row_no, row in enumerate(rows, start=2):
            role = str(row.get(fields["role"]) or "").strip().lower()
            content = normalize_text(row.get(fields["content"]))
            if role not in {"system", "user", "assistant"}:
                warnings.append(f"Row {row_no}: role must be system, user, or assistant.")
                continue
            if not content:
                warnings.append(f"Row {row_no}: content is empty.")
                continue
            messages.append({"role": role, "content": content})
        samples = _samples_from_role_messages(messages)
        assistant_count = sum(1 for msg in messages if msg.get("role") == "assistant")
        return DatasetBuildResult(samples=samples, message_count=len(messages), assistant_message_count=assistant_count, warnings=warnings)

    sender_key = fields.get("sender") or fields.get("name") or fields.get("author")
    message_key = fields.get("message") or fields.get("content") or fields.get("text") or fields.get("body")
    timestamp_key = fields.get("timestamp") or fields.get("time") or fields.get("date")
    from_me_key = fields.get("from_me") or fields.get("fromme") or fields.get("is_me")
    if not sender_key or not message_key:
        return DatasetBuildResult(
            samples=[],
            message_count=0,
            assistant_message_count=0,
            warnings=["CSV must contain either role/content or sender/message columns."],
        )

    me_names = _normalize_me_names(me_name)
    parsed_messages: List[ParsedMessage] = []
    for row_no, row in enumerate(rows, start=2):
        sender = normalize_text(row.get(sender_key))
        message = normalize_text(row.get(message_key))
        if not sender or _is_noise_message(message):
            continue
        raw_from_me = str(row.get(from_me_key) or "").strip().lower() if from_me_key else ""
        from_me = raw_from_me in {"1", "true", "yes", "y", "me", "self"}
        if not raw_from_me:
            from_me = sender.casefold() in me_names
        parsed_messages.append(
            ParsedMessage(
                sender=sender,
                text=message,
                timestamp=normalize_text(row.get(timestamp_key)) if timestamp_key else None,
                from_me=from_me,
                source="csv",
            )
        )
    return build_samples_from_messages(parsed_messages, me_name=me_name)

def _samples_from_role_messages(messages: Sequence[Dict[str, str]], *, context_window: int = 8) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    context: List[Dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = normalize_text(message.get("content"))
        if not role or not content:
            continue
        if role == "assistant":
            sample_messages = [msg for msg in context[-context_window:] if msg.get("content")]
            if not any(msg.get("role") == "system" for msg in sample_messages):
                sample_messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
            sample_messages.append({"role": "assistant", "content": content})
            samples.append({"messages": sample_messages})
        context.append({"role": role, "content": content})
    return samples

def build_samples_from_messages(
    messages: Sequence[ParsedMessage],
    *,
    me_name: Optional[str] = None,
    context_window: int = 6,
) -> DatasetBuildResult:
    me_names = _normalize_me_names(me_name)
    samples: List[Dict[str, Any]] = []
    warnings: List[str] = []
    role_history: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    assistant_count = 0
    current_chat_name: Optional[str] = None

    for message in messages:
        chat_name = normalize_text(message.chat_name)
        if chat_name and current_chat_name and chat_name != current_chat_name:
            role_history = [{"role": "system", "content": SYSTEM_PROMPT}]
        if chat_name:
            current_chat_name = chat_name

        text = normalize_text(message.text)
        if _is_noise_message(text):
            continue
        is_me = bool(message.from_me)
        if message.from_me is None:
            is_me = message.sender.casefold() in me_names
        role = "assistant" if is_me else "user"
        if role == "assistant":
            assistant_count += 1
            context = [role_history[0]]
            context.extend(role_history[1:][-context_window:])
            context.append({"role": "assistant", "content": text})
            if any(msg["role"] == "user" for msg in context):
                samples.append({"messages": context})
            else:
                samples.append(
                    {
                        "messages": [
                            role_history[0],
                            {
                                "role": "user",
                                "content": "Continue the conversation in my usual style.",
                            },
                            {"role": "assistant", "content": text},
                        ]
                    }
                )
        role_history.append({"role": role, "content": text})

    if not samples:
        warnings.append("No assistant/owner replies were found after validation.")
    return DatasetBuildResult(
        samples=samples,
        message_count=len(messages),
        assistant_message_count=assistant_count,
        warnings=warnings,
    )

def build_dataset_from_bytes(
    data: bytes,
    *,
    filename: str,
    me_name: Optional[str] = None,
) -> DatasetBuildResult:
    text = data.decode("utf-8-sig", errors="replace")
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".jsonl":
        return parse_jsonl_samples(text)
    if suffix == ".csv":
        return parse_csv_messages(text, me_name=me_name)
    if suffix == ".json":
        return parse_json_dataset(text)
    if suffix in {".txt", ".log"}:
        messages = parse_whatsapp_export_text(text)
        parsed = build_samples_from_messages(messages, me_name=me_name)
        return parsed if parsed.samples else parse_plain_text_dataset(text)
    if suffix in {".md", ".markdown"}:
        return parse_plain_text_dataset(text)
    return DatasetBuildResult(samples=[], message_count=0, assistant_message_count=0, warnings=["Unsupported file type. Use .txt, .csv, .json, .jsonl, .log, or .md."])


def build_dataset_from_folder(
    folder: str | Path,
    *,
    me_name: Optional[str] = None,
    max_total_bytes: int = 0,
) -> DatasetBuildResult:
    """Build one dataset from a local data dump without copying its raw files."""
    # ponytail: each file is read into memory; stream JSONL when multi-GB files need lower RAM.
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        return DatasetBuildResult(samples=[], message_count=0, assistant_message_count=0, warnings=["Folder was not found."])
    if root == Path(root.anchor) or root == Path.home().resolve():
        return DatasetBuildResult(samples=[], message_count=0, assistant_message_count=0, warnings=["Choose a data dump folder, not a drive or home directory."])

    supported = {".csv", ".json", ".jsonl", ".log", ".markdown", ".md", ".txt"}
    samples: List[Dict[str, Any]] = []
    warnings: List[str] = []
    message_count = 0
    assistant_message_count = 0
    bytes_read = 0
    file_count = 0
    byte_limit = max(0, int(max_total_bytes or 0))
    try:
        paths = (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in supported)
        for path in paths:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if byte_limit and bytes_read + size > byte_limit:
                warnings.append("Folder import reached its configured byte limit.")
                break
            try:
                result = build_dataset_from_bytes(path.read_bytes(), filename=path.name, me_name=me_name)
            except OSError:
                warnings.append(f"{path.name}: could not be read.")
                continue
            bytes_read += size
            file_count += 1
            samples.extend(result.samples)
            message_count += result.message_count
            assistant_message_count += result.assistant_message_count
            warnings.extend(f"{path.name}: {warning}" for warning in result.warnings)
    except OSError:
        warnings.append("Folder could not be scanned.")
    if not file_count:
        warnings.append("No supported data files were found in the folder.")
    return DatasetBuildResult(
        samples=samples,
        message_count=message_count,
        assistant_message_count=assistant_message_count,
        warnings=warnings,
    )

def split_samples(samples: Sequence[Dict[str, Any]], *, eval_fraction: float = 0.08) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    normalized = list(samples)
    if len(normalized) < 10:
        return normalized, []
    eval_count = max(1, int(len(normalized) * eval_fraction))
    return normalized[:-eval_count], normalized[-eval_count:]

def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    serialized: List[str] = []
    for row in rows:
        serialized.append(json.dumps(row, ensure_ascii=False) + "\n")
        count += 1
    write_secure_file(path, "".join(serialized).encode("utf-8"))
    return count
