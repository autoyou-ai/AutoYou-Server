# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-U-usdt-6261bb800e2f51f73d17dd68

"""Education Agent website backend.

The backend is intentionally read-mostly: it exposes the current WebRTC/session
state to an authenticated operator without starting media capture by itself.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from fastapi import Request
from fastapi.responses import Response

from shared.runtime_module_loader import import_autoyou_shared_tools_module as _import_autoyou_shared_tools_module
from shared.video_call_manager import VIDEO_FRAME_REGISTRY

__debug_provenance_u__ = "AUTOYOU-PROVENANCE-U-usdt-6261bb800e2f51f73d17dd68"


_SMC_MOD = "autoyou_agents.shared_tools.scheduler_mission_control"
_smc = _import_autoyou_shared_tools_module(_SMC_MOD, anchor=__file__)
create_agent_chat_app = _smc.create_agent_chat_app
_describe_chat_auth_state = _smc._describe_chat_auth_state
_json_response = _smc._json_response
_runtime_server = _smc._runtime_server

_AGENT_NAME = "education_agent"
_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_MAX_RECENT_LIMIT = 250
_LIVE_SESSION_MAPS = (
    "session_peers",
    "audio_sinks",
    "video_sinks",
    "desktop_video_tracks",
    "datachannel_managers",
    "silent_recorders",
)
_CLOSED_STATES = {"closed", "closing", "failed", "disconnected"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _jsonable(value: Any, *, max_string: int = 1000) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:max_string]
    if isinstance(value, dict):
        return {str(k): _jsonable(v, max_string=max_string) for k, v in list(value.items())[:60]}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item, max_string=max_string) for item in list(value)[:80]]
    return str(value)[:max_string]


def _limit_text(value: Any, limit: int = 1800) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def _limit_display_name(value: Any, limit: int = 120) -> str:
    text = value if isinstance(value, str) else str(value or "")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def _safe_limit(raw_limit: Any, default: int = 100) -> int:
    try:
        parsed = int(raw_limit)
    except Exception:
        parsed = default
    return max(1, min(_MAX_RECENT_LIMIT, parsed))


def _path_from_sqlite_uri(raw_uri: str) -> Optional[Path]:
    uri = str(raw_uri or "").strip()
    if not uri:
        return None
    if uri.startswith("sqlite+aiosqlite:///"):
        path = uri[len("sqlite+aiosqlite:///") :]
    elif uri.startswith("sqlite:///"):
        path = uri[len("sqlite:///") :]
    elif "://" not in uri:
        path = uri
    else:
        parsed = urlparse(uri)
        if not parsed.scheme.startswith("sqlite"):
            return None
        path = parsed.path or ""
    path = unquote(path)
    if os.name == "nt" and re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    if not path:
        return None
    return Path(path).expanduser()


def _resolve_session_db_path(server: Any) -> Optional[Path]:
    resolver = getattr(server, "_resolve_ai_agent_storage_uris", None)
    if callable(resolver):
        try:
            uris = resolver()
            if isinstance(uris, dict):
                return _path_from_sqlite_uri(str(uris.get("session_service_uri") or ""))
        except Exception:
            pass
    service_manager = getattr(getattr(server, "STATE", None), "service_manager", None)
    config = getattr(service_manager, "config", None)
    db_path = getattr(config, "adk_db_path", None) or getattr(config, "db_path", None)
    return _path_from_sqlite_uri(str(db_path or ""))


def _parse_timestamp_ms(value: Any) -> int:
    if isinstance(value, (int, float)):
        numeric = float(value)
        return int(numeric if numeric > 10_000_000_000 else numeric * 1000)
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        numeric = float(text)
        return int(numeric if numeric > 10_000_000_000 else numeric * 1000)
    except Exception:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except Exception:
        return 0


def _event_data_messages(row: sqlite3.Row) -> List[Dict[str, Any]]:
    raw_event = row["event_data"] if "event_data" in row.keys() else ""
    try:
        event_data = json.loads(raw_event) if isinstance(raw_event, str) else raw_event
    except Exception:
        event_data = {}
    if not isinstance(event_data, dict):
        event_data = {}

    state_delta = (
        event_data.get("actions", {})
        if isinstance(event_data.get("actions"), dict)
        else {}
    ).get("state_delta", {})
    if not isinstance(state_delta, dict):
        state_delta = {}
    event_raw = state_delta.get("event_data_raw")
    if not isinstance(event_raw, dict):
        event_raw = {}
    memory_metadata = event_raw.get("memory_metadata")
    if not isinstance(memory_metadata, dict):
        memory_metadata = {}

    timestamp_ms = _parse_timestamp_ms(
        event_raw.get("timestamp")
        or event_data.get("timestamp")
        or (row["timestamp"] if "timestamp" in row.keys() else None)
    )
    base = {
        "feed": "session_db",
        "event_id": str(row["id"] if "id" in row.keys() else ""),
        "session_id": str(row["session_id"] if "session_id" in row.keys() else ""),
        "user_id": str(row["user_id"] if "user_id" in row.keys() else ""),
        "timestamp_ms": timestamp_ms,
        "source": "session_db",
    }
    client_display_name = _limit_display_name(memory_metadata.get("client_display_name"))
    if client_display_name:
        base["client_display_name"] = client_display_name

    messages: List[Dict[str, Any]] = []
    user_message = _limit_text(event_raw.get("user_message"))
    if user_message:
        messages.append({**base, "id": f"{base['event_id']}:user", "direction": "inbound", "channel": "chat", "text": user_message})
    agent_response = _limit_text(event_raw.get("agent_response"))
    if agent_response:
        messages.append({**base, "id": f"{base['event_id']}:agent", "direction": "outbound", "channel": "chat", "text": agent_response})
    return messages


def _read_recent_session_db_messages(server: Any, limit: int) -> Dict[str, Any]:
    db_path = _resolve_session_db_path(server)
    if db_path is None:
        return {"available": False, "path": "", "recent_messages": [], "error": "session_db_path_unavailable"}
    resolved = db_path.resolve()
    if not resolved.exists():
        return {"available": False, "path": str(resolved), "recent_messages": [], "error": "session_db_missing"}

    messages: List[Dict[str, Any]] = []
    try:
        conn = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "events" not in tables:
                return {"available": True, "path": str(resolved), "recent_messages": [], "error": "events_table_missing"}
            rows = conn.execute(
                """
                SELECT id, app_name, user_id, session_id, invocation_id, timestamp, event_data
                FROM events
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            for row in reversed(rows):
                messages.extend(_event_data_messages(row))
        finally:
            conn.close()
    except Exception as exc:
        return {"available": False, "path": str(resolved), "recent_messages": [], "error": str(exc)[:240]}

    return {
        "available": True,
        "path": str(resolved),
        "recent_messages": messages[-limit:],
        "error": "",
    }


def _dict_copy(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _mapping(webrtc: Any, name: str) -> Dict[Any, Any]:
    mapping = getattr(webrtc, name, {}) if webrtc is not None else {}
    return mapping if isinstance(mapping, dict) else {}


def _peer_connected(webrtc: Any, session_id: str) -> bool:
    peer = _mapping(webrtc, "session_peers").get(session_id)
    if peer is None:
        return False
    state = str(getattr(peer, "connectionState", "") or getattr(peer, "iceConnectionState", "") or "").lower()
    return state not in _CLOSED_STATES


def _datachannel_connected(webrtc: Any, session_id: str) -> bool:
    manager = _mapping(webrtc, "datachannel_managers").get(session_id)
    if manager is None:
        return False
    channel = getattr(manager, "datachannel", None)
    state = str(getattr(channel, "readyState", "") or "").lower()
    return state not in _CLOSED_STATES


def _connection_key(webrtc: Any, session_id: str) -> str:
    session_id = str(session_id or "").strip()
    if not session_id:
        return ""
    key_fn = getattr(webrtc, "_datachannel_connection_key", None)
    if callable(key_fn):
        try:
            key = str(key_fn(session_id) or "").strip()
            if key:
                return key
        except Exception:
            pass
    identity_fn = getattr(webrtc, "_resolve_chat_identity", None)
    if callable(identity_fn):
        try:
            identity = identity_fn(session_id)
        except Exception:
            identity = None
        for field in ("owner_key", "canonical_session_id"):
            value = str(getattr(identity, field, "") or "").strip()
            if value:
                return f"{field}:{value}"
    return f"session:{session_id}"


def _raw_live_session_ids(webrtc: Any) -> List[str]:
    if webrtc is None:
        return []
    session_ids: List[str] = []
    for name in _LIVE_SESSION_MAPS:
        for key in _mapping(webrtc, name).keys():
            session_id = str(key or "").strip()
            if not session_id or session_id in session_ids:
                continue
            if name == "session_peers" and not _peer_connected(webrtc, session_id):
                continue
            if name == "datachannel_managers" and not _datachannel_connected(webrtc, session_id):
                continue
            session_ids.append(session_id)
    return sorted(session_ids)


def _live_session_groups(webrtc: Any) -> List[List[str]]:
    grouped: Dict[str, List[str]] = {}
    for session_id in _raw_live_session_ids(webrtc):
        key = _connection_key(webrtc, session_id)
        grouped.setdefault(key, []).append(session_id)
    return [group for _, group in sorted(grouped.items(), key=lambda item: item[0])]


def _live_session_ids(webrtc: Any) -> List[str]:
    return [group[0] for group in _live_session_groups(webrtc) if group]


def _video_frame_status_for_session(session_id: str) -> Dict[str, Any]:
    try:
        frame = VIDEO_FRAME_REGISTRY.latest(session_id=session_id)
    except Exception:
        frame = None
    if frame is None:
        return {"active": False}
    return {
        "active": True,
        "sequence": int(getattr(frame, "sequence", 0) or 0),
        "timestamp_ms": int(getattr(frame, "timestamp_ms", 0) or 0),
        "width": int(getattr(frame, "width", 0) or 0),
        "height": int(getattr(frame, "height", 0) or 0),
        "source": str(getattr(frame, "source", "") or ""),
    }


def _video_frame_status_for_sessions(session_ids: Iterable[str]) -> Dict[str, Any]:
    frame = _latest_frame_for_sessions(session_ids)
    if frame is None:
        return {"active": False}
    return {
        "active": True,
        "sequence": int(getattr(frame, "sequence", 0) or 0),
        "timestamp_ms": int(getattr(frame, "timestamp_ms", 0) or 0),
        "width": int(getattr(frame, "width", 0) or 0),
        "height": int(getattr(frame, "height", 0) or 0),
        "source": str(getattr(frame, "source", "") or ""),
        "session_id": str(getattr(frame, "session_id", "") or ""),
    }


def _latest_frame_for_sessions(session_ids: Iterable[str]) -> Any:
    latest = None
    for session_id in session_ids:
        try:
            frame = VIDEO_FRAME_REGISTRY.latest(session_id=session_id)
        except Exception:
            frame = None
        if frame is None:
            continue
        if latest is None or int(getattr(frame, "sequence", 0) or 0) > int(getattr(latest, "sequence", 0) or 0):
            latest = frame
    return latest


def _first_mapping_value(webrtc: Any, name: str, session_ids: Iterable[str]) -> Any:
    values = _mapping(webrtc, name)
    for session_id in session_ids:
        if session_id in values:
            return values.get(session_id)
    return None


def _live_frame_status(session_ids: Iterable[str]) -> Dict[str, Any]:
    frame_sessions = [str(session_id or "").strip() for session_id in session_ids if str(session_id or "").strip()]
    frames = []
    for session_id in frame_sessions:
        try:
            frame = VIDEO_FRAME_REGISTRY.latest(session_id=session_id)
        except Exception:
            frame = None
        if frame is not None:
            frames.append(frame)
    latest = max(frames, key=lambda item: int(getattr(item, "sequence", 0) or 0), default=None)
    return {
        "active": latest is not None,
        "active_sessions": len(frames),
        "latest_session_id": str(getattr(latest, "session_id", "") or "") if latest else None,
        "latest_sequence": int(getattr(latest, "sequence", 0) or 0) if latest else 0,
        "latest_timestamp_ms": int(getattr(latest, "timestamp_ms", 0) or 0) if latest else None,
        "latest_width": int(getattr(latest, "width", 0) or 0) if latest else None,
        "latest_height": int(getattr(latest, "height", 0) or 0) if latest else None,
    }


def _session_rows(webrtc: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for session_aliases in _live_session_groups(webrtc):
        if not session_aliases:
            continue
        session_id = session_aliases[0]
        video_sink = _first_mapping_value(webrtc, "video_sinks", session_aliases)
        video_session_ids = [alias for alias in session_aliases if alias in _mapping(webrtc, "video_sinks")]
        recording_path = ""
        recording_format = ""
        recording_enabled = False
        if video_sink is not None:
            try:
                recording_path = str(getattr(video_sink, "recording_path", "") or "")
            except Exception:
                recording_path = ""
            try:
                recording_format = str(getattr(video_sink, "recording_format", "") or "")
            except Exception:
                recording_format = ""
            recording_enabled = bool(getattr(video_sink, "recording_enabled", False))
        client_identity: Dict[str, Any] = {}
        client_identity_snapshot = getattr(webrtc, "client_display_name_snapshot", None)
        if callable(client_identity_snapshot):
            for alias in session_aliases:
                try:
                    candidate = client_identity_snapshot(alias)
                except Exception:
                    candidate = {}
                if isinstance(candidate, dict) and str(candidate.get("owner_key") or ""):
                    client_identity = candidate
                    break
        rows.append(
            {
                "session_id": session_id,
                "session_aliases": session_aliases,
                "owner_key": str(client_identity.get("owner_key") or ""),
                "client_display_name": str(client_identity.get("client_display_name") or ""),
                "reported_client_display_name": str(client_identity.get("reported_client_display_name") or ""),
                "server_name_override": str(client_identity.get("server_name_override") or ""),
                "peer_connected": any(_peer_connected(webrtc, alias) for alias in session_aliases),
                "datachannel_connected": any(_datachannel_connected(webrtc, alias) for alias in session_aliases),
                "audio_active": any(alias in _mapping(webrtc, "audio_sinks") for alias in session_aliases),
                "video_active": bool(video_session_ids),
                "desktop_video_active": any(alias in _mapping(webrtc, "desktop_video_tracks") for alias in session_aliases),
                "voice_call_active": any(bool(_mapping(webrtc, "voice_call_client_active_by_session").get(alias)) for alias in session_aliases),
                "voice_status": _jsonable(_dict_copy(_first_mapping_value(webrtc, "voice_call_status_by_session", session_aliases))),
                "voice_playback": _jsonable(_dict_copy(_first_mapping_value(webrtc, "voice_call_playback_by_session", session_aliases))),
                "background_audio": _jsonable(_dict_copy(_first_mapping_value(webrtc, "background_audio_state_by_session", session_aliases))),
                "silent_recording_active": any(alias in _mapping(webrtc, "silent_recorders") for alias in session_aliases),
                "pending_voice_messages": sum(
                    len(_mapping(webrtc, "pending_voice_chat_messages").get(alias, []) or [])
                    for alias in session_aliases
                ),
                "video_recording": {
                    "enabled": recording_enabled,
                    "path": recording_path,
                    "format": recording_format,
                },
                "latest_video_frame": _jsonable(_video_frame_status_for_sessions(video_session_ids)),
            }
        )
    return rows


def _active_silent_recorders(webrtc: Any) -> List[Dict[str, Any]]:
    recorders = getattr(webrtc, "silent_recorders", {}) if webrtc is not None else {}
    if not isinstance(recorders, dict):
        return []
    seen: set[int] = set()
    output: List[Dict[str, Any]] = []
    for session_id, recorder in recorders.items():
        marker = id(recorder)
        if marker in seen:
            continue
        seen.add(marker)
        status_fn = getattr(recorder, "status", None)
        status = status_fn() if callable(status_fn) else {}
        if not isinstance(status, dict):
            status = {}
        status.setdefault("session_id", str(session_id or ""))
        output.append(_jsonable(status))
    return output


def _recent_files(root: Any, *, suffixes: Iterable[str], limit: int = 8) -> List[Dict[str, Any]]:
    root_text = str(root or "").strip()
    if not root_text:
        return []
    path = Path(root_text).expanduser()
    if not path.exists() or not path.is_dir():
        return []
    suffix_set = {suffix.lower() for suffix in suffixes}
    candidates: List[Path] = []
    for child in path.iterdir():
        if child.is_dir():
            candidates.append(child)
        elif child.suffix.lower() in suffix_set:
            candidates.append(child)
    candidates.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0.0, reverse=True)
    output: List[Dict[str, Any]] = []
    for item in candidates[:limit]:
        try:
            stat = item.stat()
        except Exception:
            continue
        output.append(
            {
                "name": item.name,
                "path": str(item),
                "kind": "directory" if item.is_dir() else "file",
                "size_bytes": int(stat.st_size),
                "modified_at_ms": int(stat.st_mtime * 1000),
            }
        )
    return output


def _recording_snapshot(cfg: Dict[str, Any], webrtc: Any) -> Dict[str, Any]:
    video_cfg = cfg.get("video_call", {}) if isinstance(cfg, dict) else {}
    if not isinstance(video_cfg, dict):
        video_cfg = {}
    return {
        "video_recordings": _recent_files(video_cfg.get("recording_dir"), suffixes=(".mp4", ".jpg", ".jpeg", ".jsonl")),
        "silent_audio_recordings": _recent_files(video_cfg.get("silent_recording_dir"), suffixes=(".wav",)),
        "active_silent_recorders": _active_silent_recorders(webrtc),
    }


def _capabilities_snapshot(server: Any, cfg: Dict[str, Any]) -> Dict[str, Any]:
    builder = getattr(server, "_build_webrtc_capabilities", None)
    if not callable(builder):
        return {}
    try:
        caps = builder(cfg=cfg, include_admin=True)
        return _jsonable(caps) if isinstance(caps, dict) else {}
    except Exception as exc:
        return {"error": str(exc)[:240]}


def _outbound_track_enabled(track: Any) -> bool:
    if track is None:
        return False
    enabled = getattr(track, "is_enabled", False)
    try:
        enabled = enabled() if callable(enabled) else enabled
    except Exception:
        return False
    ready_state = str(getattr(track, "readyState", "live") or "live").strip().lower()
    return bool(enabled) and ready_state not in _CLOSED_STATES and ready_state != "ended"


def _enabled_outbound_video_track(webrtc: Any) -> Tuple[str, Any, int]:
    unique: Dict[int, Tuple[str, Any]] = {}
    for session_id, track in _mapping(webrtc, "desktop_video_tracks").items():
        if not _outbound_track_enabled(track):
            continue
        unique.setdefault(id(track), (str(session_id or ""), track))
    if len(unique) != 1:
        return "", None, len(unique)
    session_id, track = next(iter(unique.values()))
    return session_id, track, 1


def _source_list(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _computer_video_snapshot(capabilities: Dict[str, Any], webrtc: Any) -> Dict[str, Any]:
    outbound = capabilities.get("outbound_video", {}) if isinstance(capabilities, dict) else {}
    if not isinstance(outbound, dict):
        outbound = {}
    session_id, track, enabled_track_count = _enabled_outbound_video_track(webrtc)
    configured_sources = _source_list(outbound.get("sources"))
    active_sources: List[str] = []
    if track is not None:
        source_names = getattr(track, "source_names", None)
        if callable(source_names):
            try:
                active_sources = _source_list(source_names())
            except Exception:
                active_sources = []
        if not active_sources:
            active_sources = _source_list(outbound.get("active_sources"))
    preview_available = bool(track is not None and callable(getattr(track, "capture_preview_image", None)))
    return {
        "label": "Computer video",
        "enabled": bool(outbound.get("enabled")),
        "configured": bool(configured_sources),
        "source": str(outbound.get("source") or "none"),
        "configured_source": str(outbound.get("configured_source") or "none"),
        "configured_sources": configured_sources,
        "active_sources": active_sources,
        "active": track is not None,
        "preview_available": preview_available,
        "track_session_id": session_id,
        "enabled_track_count": enabled_track_count,
    }


def _capture_outbound_preview_jpeg(track: Any, *, max_width: int = 960) -> Tuple[bytes, int, int]:
    if not _outbound_track_enabled(track):
        raise RuntimeError("Outbound computer video is not active")
    capture = getattr(track, "capture_preview_image", None)
    if not callable(capture):
        raise RuntimeError("Outbound computer video preview is unavailable")
    image = capture(max_width=max_width)
    if image is None or not _outbound_track_enabled(track):
        raise RuntimeError("Outbound computer video is not active")
    image = image.convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=82)
    return buffer.getvalue(), int(getattr(image, "width", 0) or 0), int(getattr(image, "height", 0) or 0)


def _live_event(event: Dict[str, Any], live_session_ids: set[str]) -> bool:
    if not live_session_ids:
        return False
    candidates = {
        str(event.get("session_id") or "").strip(),
        str(event.get("canonical_session_id") or "").strip(),
    }
    return bool(candidates & live_session_ids)


def _build_snapshot(limit: int = 100) -> Dict[str, Any]:
    server = _runtime_server()
    state = getattr(server, "STATE", None)
    cfg = getattr(state, "config", None)
    if not isinstance(cfg, dict):
        default_config = getattr(server, "_default_config", None)
        cfg = default_config() if callable(default_config) else {}
    webrtc = getattr(server, "WEBRTC", None)
    sessions = _session_rows(webrtc)
    live_session_ids = {
        str(alias or "")
        for session in sessions
        for alias in (session.get("session_aliases") or [session.get("session_id")])
    }
    live_session_ids.update(str(session.get("session_id") or "") for session in sessions)

    live_events: List[Dict[str, Any]] = []
    # from __debug_provenance_u__ import usdt
    event_snapshot = getattr(webrtc, "streaming_event_snapshot", None)
    if callable(event_snapshot):
        try:
            live_events = event_snapshot(limit=limit)
        except Exception:
            live_events = []
    live_events = [dict(item) for item in live_events if isinstance(item, dict) and _live_event(item, live_session_ids)]
    for item in live_events:
        item.setdefault("feed", "live")

    session_db = _read_recent_session_db_messages(server, limit)
    recent_events = list(live_events)
    recent_events.sort(key=lambda item: int(item.get("timestamp_ms") or 0))
    recent_events = recent_events[-limit:]

    live_video_session_ids = [
        str(alias or "")
        for session in sessions
        if session.get("video_active")
        for alias in (session.get("session_aliases") or [session.get("session_id")])
        if str(alias or "") in _mapping(webrtc, "video_sinks")
    ]
    frame_status = _live_frame_status(live_video_session_ids)
    capabilities = _capabilities_snapshot(server, cfg)
    computer_video = _computer_video_snapshot(capabilities, webrtc)
    client_name_history_enabled = False
    history_enabled_fn = getattr(server, "_client_name_history_enabled", None)
    if callable(history_enabled_fn):
        try:
            client_name_history_enabled = bool(history_enabled_fn(cfg))
        except Exception:
            client_name_history_enabled = False

    return {
        "timestamp_ms": _now_ms(),
        "capabilities": capabilities,
        "webrtc": {
            "session_count": len(sessions),
            "sessions": sessions,
        },
        "video_frames": _jsonable(frame_status),
        "computer_video": _jsonable(computer_video),
        "self_video": _jsonable(computer_video),
        "recordings": _recording_snapshot(cfg, webrtc),
        "client_identity": {
            "history_enabled": client_name_history_enabled,
            "history_note": (
                "Names can be saved with chat history."
                if client_name_history_enabled
                else "Names are live-only. Incognito and message-storage-off modes never save them."
            ),
        },
        "recent_events": _jsonable(recent_events),
        "session_db": _jsonable(session_db),
    }


def _extra_routes(app: Any, agent_name: str) -> None:
    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "agent_name": agent_name,
            "proxy_path": "/agent/education_agent/",
        }

    @app.get("/api/education/status")
    async def education_status(request: Request, limit: int = 100) -> Any:
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated", "auth": auth}, status_code=401)
        snapshot = _build_snapshot(limit=_safe_limit(limit))
        return _json_response({"success": True, "auth": auth, **snapshot})

    @app.put("/api/education/client-name")
    async def education_client_name(request: Request) -> Any:
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated", "auth": auth}, status_code=401)
        try:
            payload = await request.json()
        except Exception:
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        if not isinstance(payload, dict):
            return _json_response({"success": False, "error": "Invalid JSON body."}, status_code=400)
        server = _runtime_server()
        set_name = getattr(server, "_set_client_name_override", None)
        if not callable(set_name):
            return _json_response({"success": False, "error": "Client name controls are unavailable."}, status_code=503)
        try:
            result = set_name(
                payload.get("owner_key"),
                payload.get("client_display_name"),
            )
        except ValueError as exc:
            return _json_response({"success": False, "error": str(exc)}, status_code=400)
        except Exception as exc:
            return _json_response({"success": False, "error": str(exc)[:240]}, status_code=409)
        return _json_response({"success": True, "client_identity": _jsonable(result)})

    @app.get("/api/education/frame.jpg")
    async def education_latest_frame(request: Request, session_id: str = "") -> Any:
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated", "auth": auth}, status_code=401)
        server = _runtime_server()
        webrtc = getattr(server, "WEBRTC", None)
        groups = _live_session_groups(webrtc)
        video_sinks = _mapping(webrtc, "video_sinks")
        live_video_session_ids = [sid for group in groups for sid in group if sid in video_sinks]
        requested_session_id = str(session_id or "").strip()
        if requested_session_id:
            requested_aliases = next((group for group in groups if requested_session_id in group), [requested_session_id])
            frame = _latest_frame_for_sessions(alias for alias in requested_aliases if alias in video_sinks)
        else:
            frame = _latest_frame_for_sessions(live_video_session_ids)
        if frame is None:
            return _json_response({"success": False, "error": "No video frame available"}, status_code=404)
        response = Response(content=frame.jpeg_bytes, media_type="image/jpeg")
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-AutoYou-Frame-Sequence"] = str(getattr(frame, "sequence", 0) or 0)
        response.headers["X-AutoYou-Frame-Session"] = str(getattr(frame, "session_id", "") or "")
        return response

    @app.get("/api/education/self-frame.jpg")
    @app.get("/api/education/computer-frame.jpg")
    async def education_computer_frame(request: Request) -> Any:
        auth = _describe_chat_auth_state(request, agent_name)
        if not auth.get("authenticated"):
            return _json_response({"success": False, "error": "Not authenticated", "auth": auth}, status_code=401)
        server = _runtime_server()
        webrtc = getattr(server, "WEBRTC", None)
        state = getattr(server, "STATE", None)
        cfg = getattr(state, "config", None)
        if not isinstance(cfg, dict):
            default_config = getattr(server, "_default_config", None)
            cfg = default_config() if callable(default_config) else {}
        capabilities = _capabilities_snapshot(server, cfg)
        computer_video = _computer_video_snapshot(capabilities, webrtc)
        if not computer_video.get("preview_available"):
            return _json_response({"success": False, "error": "No computer video frame available"}, status_code=404)
        session_id, track, _count = _enabled_outbound_video_track(webrtc)
        if track is None:
            return _json_response({"success": False, "error": "No computer video frame available"}, status_code=404)
        try:
            jpeg_bytes, width, height = await asyncio.to_thread(_capture_outbound_preview_jpeg, track)
        except Exception:
            return _json_response({"success": False, "error": "No computer video frame available"}, status_code=404)
        response = Response(content=jpeg_bytes, media_type="image/jpeg")
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-AutoYou-Frame-Source"] = str(computer_video.get("source") or "")
        response.headers["X-AutoYou-Frame-Session"] = session_id
        response.headers["X-AutoYou-Frame-Width"] = str(width)
        response.headers["X-AutoYou-Frame-Height"] = str(height)
        return response


app = create_agent_chat_app(
    agent_name=_AGENT_NAME,
    title="Education Agent",
    description="A simple, private view of live learning sessions, questions, media, and recordings.",
    frontend_dir=_FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
