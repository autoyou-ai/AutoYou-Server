# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Shared application dispatch after transport and session authorization.

Pairing, room bridge admission and legacy keepalive/rebinding happen before
this boundary. Route implementations and business policy remain with their
existing owners. This module imports no WebRTC or Iroh networking code.
"""

from __future__ import annotations

from typing import Any
from shared.datachannel_manager import MessageType


async def dispatch_bound_control_message(
    engine: Any, message: Any, *, trusted_transport_id: str, channel: Any,
) -> bool:
    """Handle transport-scoped operations before any legacy header aliasing."""
    kind = message.header.message_type
    if kind == MessageType.PAIRING_CONTROL:
        engine._track_session_task(trusted_transport_id,
            engine._handle_live_pair_control(message, trusted_transport_id, channel), "live_pairing")
    elif kind == MessageType.ROOM_BRIDGE_CONTROL:
        engine._track_session_task(trusted_transport_id, engine._handle_room_bridge_control(message,
            trusted_transport_id=trusted_transport_id, datachannel_manager=channel), "room_bridge_control")
    elif kind == MessageType.CHAT and isinstance(message.payload, dict) and \
            isinstance(message.payload.get("metadata"), dict) and \
            isinstance(message.payload["metadata"].get("room_bridge"), dict):
        engine._track_session_task(trusted_transport_id, engine._handle_room_bridge_chat(message,
            trusted_transport_id=trusted_transport_id, datachannel_manager=channel), "room_bridge_chat")
    else:
        return False
    return True


def bootstrap_application_session(engine: Any, session_id: str, *, runtime: Any) -> None:
    """Replay application state through an already ready, authorized channel."""
    channel = engine.datachannel_managers.get(session_id)
    from shared.session_transport import session_channel_ready
    if channel is None or not session_channel_ready(channel):
        return
    voice_status = engine.voice_call_status_by_session.get(session_id)
    audio_status = engine._get_audio_manager_readiness_status(session_id)
    stored = str(voice_status.get("state") or "").lower() if isinstance(voice_status, dict) else ""
    live = str(audio_status.get("state") or "").lower() if isinstance(audio_status, dict) else ""
    if isinstance(audio_status, dict) and (not isinstance(voice_status, dict) or
            (stored == "warming" and live != "warming") or
            (stored == "unavailable" and live in {"warming", "ready"})):
        voice_status = audio_status
    if isinstance(voice_status, dict):
        engine._track_session_task(session_id,
            engine._publish_voice_call_status(session_id, dict(voice_status)), "voice_call_status_bootstrap")
    if session_id in engine.voice_call_playback_by_session:
        engine._track_session_task(session_id, engine._publish_voice_call_status(session_id,
            dict(engine.voice_call_playback_by_session[session_id])), "voice_call_playback_bootstrap")
    engine._track_session_task(session_id, engine._publish_webrtc_capabilities(session_id), "session_capabilities_bootstrap")
    engine._track_session_task(session_id, engine.send_server_profile_to_session(session_id), "server_profile_bootstrap")
    engine._track_session_task(session_id, engine._prime_conversation_context_status(session_id,
        runtime._resolve_conversation_identity(engine._resolve_chat_identity(session_id))), "conversation_context_bootstrap")
    engine._track_session_task(session_id, engine._flush_pending_voice_chat_messages(session_id), "voice_chat_flush")
    engine._track_session_task(session_id, flush_pending_scheduler_notifications(engine, session_id), "scheduler_notification_flush")


async def flush_pending_scheduler_notifications(engine: Any, session_id: str) -> None:
    identity = engine._resolve_chat_identity(session_id)
    from shared import scheduler_service
    await scheduler_service.flush_pending_notifications_for_owner(
        owner_key=identity.owner_key, canonical_user_id=identity.canonical_user_id)


async def dispatch_application_message(
    engine: Any,
    message: Any,
    *,
    trusted_transport_id: str,
    session_id: str,
) -> bool:
    kind = message.header.message_type
    if kind == MessageType.CHAT:
        engine._track_session_task(session_id, engine._handle_chat_message(message), "chat", survive_disconnect=True)
    elif kind == MessageType.HTTP_REQUEST_CANCEL:
        engine._track_session_task(session_id, engine._handle_http_request_cancel(message), "http_request_cancel")
    elif kind == MessageType.HTTP_STREAM_ABORT:
        engine._track_session_task(session_id, engine._handle_http_stream_abort(message), "http_stream_abort")
    elif kind == MessageType.HTTP_REQUEST:
        task = engine._track_session_task(session_id, engine._handle_http_request(message), "http_request")
        engine._remember_http_proxy_request_task(
            session_id, str((message.payload or {}).get("request_id") or message.header.message_id or ""), task,
        )
    elif kind == MessageType.VOICE_CALL_CONTROL:
        engine._track_session_task(
            session_id, engine._handle_voice_call_control_message(message, trusted_session_id=trusted_transport_id),
            "voice_call_control",
        )
    elif kind == MessageType.HTTP_WS_DATA:
        engine._track_session_task(session_id, engine._handle_ws_data_from_client(message), "http_ws_data")
    elif kind == MessageType.HTTP_WS_CLOSE:
        engine._track_session_task(session_id, engine._handle_ws_close_from_client(message), "http_ws_close")
    elif kind == MessageType.ERROR:
        await engine._handle_error_message(message)
    else:
        return False
    return True
