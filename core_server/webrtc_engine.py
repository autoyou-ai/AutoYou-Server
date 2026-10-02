# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-c309a67c34c2f465b86a7b30

"""Full AutoYou WebRTC, DataChannel, media, and remote-control engine."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import hashlib
import inspect
import json
import re
import webbrowser
from pathlib import Path
from types import ModuleType
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Set, Tuple

# Imported at module scope, not lazily inside the keepalive handlers.  The old
# per-rally `import game_ping_pong` resolved a bare root-level module, so any
# entry point that did not happen to have the repo root on sys.path (packaged
# build, different cwd) silently disabled the game inside a blanket except.
# `shared` is a real package and carries no project-internal imports.
from shared import game_ping_pong
from shared.game_input import GameInputHub, normalize_game_input
from shared.datachannel_manager import (
    DataChannelMessage as SharedDataChannelMessage,
    MessageHeader as SharedMessageHeader,
    MessageType as SharedMessageType,
    create_room_bridge_control_message,
)
from shared.room_bridge import (
    ROOM_BRIDGE_PROTOCOL,
    ROOM_BRIDGE_READ_ONLY_SYSTEM_PROMPT,
    RoomBridgeAdmission,
    RoomBridgeError,
    RoomBridgeGrantStore,
    bound_room_bridge_reply,
    cancel_revoked_tasks,
    chat_ack_control_payload,
    error_control_payload,
    final_reply_room_metadata,
    granted_control_payload,
    normalize_message_id,
    note_control_payload,
    presence_control_payload,
    require_bound_owner_key,
    require_room_bridge_read_only_backend,
    revoked_control_payload,
)
from shared.call_listener_coordinator import CallListenerCoordinator
from shared.screen_listen import ScreenListenMixer
from shared.remote_access_policy import (
    DEVICE_OWN,
    DEVICE_OWNERSHIP_HEADER,
    DEVICE_SHARED,
    REMOTE_BROWSER_HEADER,
    REMOTE_BROWSER_IDENTITY_HEADERS,
    REMOTE_BROWSER_VIA_WEBRTC,
    normalize_device_ownership,
)
from shared.room_call_listener import computer_presence
from shared.chat_history_identity import sanitize_peer_relay
from shared.webrtc_transport import configure_sctp_fragment_size
from shared.aiortc_turn import order_ice_servers_for_aiortc, prime_turn_udp_probe
from shared.live_pairing import LivePairing

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-c309a67c34c2f465b86a7b30"


_runtime: ModuleType
_GAME_INPUT_UNAVAILABLE = "Game input is unavailable. Connect a local game engine or enable host input."
_MOBILE_GAME_CSP = (b'<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
                    b'script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data:; '
                    b'media-src data:; connect-src \'none\'; form-action \'none\'">')
_MOBILE_GAME_MAX_BYTES = 512 * 1024
_HOSTED_GAME_LOOP = Path(__file__).resolve().parents[1] / "autoyou_agents/game_agent/website/frontend/assets/audio/stream-loop.wav"


def _speak_audio_manager(audio_manager: Any, message: str, *, context: str = "") -> None:
    speak = getattr(audio_manager, "speak")
    if context:
        try:
            parameters = inspect.signature(speak).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "context" in parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        ):
            speak(message, context=context)
            return
    speak(message)


def bind_runtime(module: ModuleType) -> None:
    """Bind the live server dependency surface used by WebRTC sessions."""
    global _runtime
    _runtime = module


class WebRTCManager:
    def __init__(self):
        self.session_peers: Dict[str, RTCPeerConnection] = {}  # For both autopair and tunnelmole sessions
        self.audio_sinks: Dict[str, Any] = {}  # Track inbound audio sinks per session for explicit cleanup
        self.video_sinks: Dict[str, Any] = {}  # Track inbound video sinks per session for explicit cleanup
        self.desktop_video_tracks: Dict[str, Any] = {}  # Outbound desktop video tracks per session (gated off until the client starts a video call)
        self.audio_transceivers: Dict[str, Any] = {}  # Negotiated audio m-lines for call/background direction control
        self.pending_candidates: Dict[str, list] = {}  # Store ICE candidates per session
        self.outgoing_trickle_candidates: Dict[str, list] = {}  # Server-side ICE candidates queued per session
        self.seen_remote_ice_candidates: Dict[str, Set[str]] = {}  # Deduplicate bursty trickle ICE submissions
        self.cleanup_tasks: Dict[str, asyncio.Task] = {}  # Track cleanup tasks to prevent pending tasks during shutdown
        self.datachannel_managers: Dict[str, 'DataChannelManager'] = {}  # Track datachannel managers per session
        self._ws_fragment_buffers: Dict[str, Dict[str, Any]] = {}
        self.session_establishment_tasks: Dict[str, asyncio.Task] = {}  # Tear down stale pre-open sessions
        self.session_disconnect_grace_tasks: Dict[str, asyncio.Task] = {}  # Allow transient disconnect recovery
        self._cleanup_locks: Dict[str, asyncio.Lock] = {}  # Per-session locks to prevent race conditions
        # session_id -> queued STT tuples: (text, transcript_already_mirrored)
        self.voice_command_queues: Dict[str, asyncio.Queue] = {}
        self.voice_command_workers: Dict[str, asyncio.Task] = {}  # session_id -> queue worker task
        self.session_message_tasks: Dict[str, Set[asyncio.Task]] = {}  # per-session long-running datachannel tasks cancelled on disconnect
        self.session_reconnect_survivable_tasks: Dict[str, Set[asyncio.Task]] = {}  # per-session chat turns that must survive transport reconnects
        # Room grants are bearer capabilities bound to a server-resolved owner,
        # mobile-generated room epoch, and the currently live transport. They
        # are intentionally in-memory and never enter the generic offline queue.
        self.room_bridge_grants = RoomBridgeGrantStore()
        self.live_pairing = LivePairing(getattr(_runtime, "pairing_router", None))
        # Which rooms the Computer is following. Empty until a call carries
        # audio for a room that holds a grant.
        self.call_listeners = CallListenerCoordinator()
        self.room_bridge_expiry_tasks: Dict[str, asyncio.Task] = {}
        self.http_proxy_request_tasks: Dict[str, Dict[str, asyncio.Task]] = {}  # session_id -> request_id -> proxy task
        self.voice_call_status_by_session: Dict[str, Dict[str, Any]] = {}
        self.voice_call_playback_by_session: Dict[str, Dict[str, Any]] = {}
        self.voice_call_client_active_by_session: Dict[str, bool] = {}
        self.screen_sessions: Dict[str, Dict[str, Any]] = {}
        self.screen_inputs = _runtime.deque(maxlen=100)
        self.screen_listen_mixer = ScreenListenMixer()
        # Derived from the pairing HTTP peer. This controls audio routing only.
        self.same_machine_audio_sessions: Set[str] = set()
        self.host_media_owner_by_session: Dict[str, str] = {}
        self.server_microphone_sharing_enabled = True
        # WUIFT segmentation hold requested by the client; re-applied to the
        # per-session AudioManager whenever it is (re)created.
        self.wuift_hold_by_session: Dict[str, bool] = {}
        # Last-applied outbound call audio config; None until the first
        # apply_video_call_settings establishes the baseline.
        self._outbound_call_audio_signature: Optional[Tuple[Any, ...]] = None
        self.background_audio_state_by_session: Dict[str, Dict[str, Any]] = {}
        self.silent_recorders: Dict[str, Any] = {}
        self.rewarded_ad_completion_by_session: Dict[str, Dict[str, Any]] = {}
        # Prevent a repeated browser/tool request from presenting a second
        # native ad while the first rewarded-ad control is still active.
        self.rewarded_ad_control_leases_by_session: Dict[str, Dict[str, Any]] = {}
        self.client_browser_control_results_by_session: Dict[str, Dict[str, Any]] = {}
        # Short-lived, target-specific leases for native remote-desktop keyboard
        # input.  Each alias points to the same lease so transport rekeys do not
        # broaden the target, and no keyboard event is ever broadcast.
        self.remote_desktop_keyboard_leases_by_session: Dict[str, Dict[str, Any]] = {}
        # Native full-screen call control is separate from the optional Remote
        # Desktop agent website keyboard lease. Aliases share one mutable lease
        # so expiry, held buttons, and failure counts cannot drift after rekeying.
        self.remote_desktop_control_leases_by_session: Dict[str, Dict[str, Any]] = {}
        self.remote_desktop_control_lock = _runtime.asyncio.Lock()
        self.game_input_hub = GameInputHub()
        self.streaming_events: deque[Dict[str, Any]] = _runtime.deque(maxlen=500)
        self.streaming_events_lock = _runtime.threading.Lock()
        # Optional names are display metadata only. They are keyed by the
        # canonical transport owner, never the disposable WebRTC session id.
        self.client_display_names_by_owner: Dict[str, str] = {}
        self.client_name_overrides_by_owner: Dict[str, str] = {}
        # Own or shared, per canonical transport owner (see remember_device_ownership).
        self.device_ownership_by_owner: Dict[str, str] = {}
        self.pending_voice_chat_messages: Dict[str, List[Any]] = {}  # session_id -> buffered voice chat payloads
        self._offline_pending_messages: Dict[str, List[Dict[str, Any]]] = {}  # session_id -> [{enqueued_at, message}] - persists across disconnects
        self._voice_chat_flush_locks: Dict[str, asyncio.Lock] = {}
        self._offline_queue_flush_locks: Dict[str, asyncio.Lock] = {}
        self._offline_nudge_last_sent_at: Dict[str, float] = {}  # session_id -> unix ts of last "replies waiting" cloud nudge
        self._voice_dc_session_id: Dict[str, str] = {}  # raw WebRTC session id -> latest client-facing session_id (iOS/Android UUID)
        try:
            queue_size = int(_runtime.os.getenv("VOICE_COMMAND_QUEUE_MAXSIZE", "20"))
            self.voice_command_queue_maxsize = max(1, queue_size)
        except Exception:
            self.voice_command_queue_maxsize = 20
        try:
            establish_timeout = float(_runtime.os.getenv("WEBRTC_ESTABLISH_TIMEOUT_SECONDS", "120"))
            self.session_establish_timeout_seconds = max(5.0, establish_timeout)
        except Exception:
            self.session_establish_timeout_seconds = 90.0
        try:
            disconnect_grace = float(_runtime.os.getenv("WEBRTC_DISCONNECT_GRACE_SECONDS", "10"))
            self.session_disconnect_grace_seconds = max(0.0, disconnect_grace)
        except Exception:
            self.session_disconnect_grace_seconds = 10.0

    def _record_streaming_event(
        self,
        *,
        session_id: Any,
        direction: str,
        channel: str,
        text: Any = "",
        source: str = "",
        owner_key: Any = "",
        canonical_session_id: Any = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        normalized_text = str(text or "").strip()
        event = {
            "id": _runtime.uuid.uuid4().hex,
            "timestamp_ms": int(_runtime.time.time() * 1000),
            "session_id": str(session_id or "").strip(),
            "direction": str(direction or "event").strip() or "event",
            "channel": str(channel or "chat").strip() or "chat",
            "source": str(source or "").strip(),
            "text": normalized_text[:4000],
            "owner_key": str(owner_key or "").strip(),
            "canonical_session_id": str(canonical_session_id or "").strip(),
        }
        if isinstance(metadata, dict) and metadata:
            compact_metadata: Dict[str, Any] = {}
            for key in (
                "is_transcription",
                "is_status",
                "is_progress",
                "is_streaming",
                "response_author",
                "agent_name",
                "display_agent_name",
            ):
                if key in metadata:
                    compact_metadata[key] = metadata.get(key)
            if compact_metadata:
                event["metadata"] = compact_metadata
        with self.streaming_events_lock:
            self.streaming_events.append(event)

    def streaming_event_snapshot(self, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            normalized_limit = max(1, min(500, int(limit)))
        except Exception:
            normalized_limit = 100
        with self.streaming_events_lock:
            return [dict(item) for item in list(self.streaming_events)[-normalized_limit:]]

    @staticmethod
    def _owner_key_from_identity(identity: Any) -> str:
        try:
            return _runtime._normalize_client_identity_owner_key(getattr(identity, "owner_key", ""))
        except ValueError:
            return ""

    @staticmethod
    def _remember_runtime_client_name(
        values: Dict[str, str],
        *,
        owner_key: str,
        client_name: str,
    ) -> None:
        if not client_name:
            values.pop(owner_key, None)
            return
        if owner_key not in values and len(values) >= _runtime._CLIENT_IDENTITY_MAX_OVERRIDES:
            values.pop(next(iter(values)), None)
        values[owner_key] = client_name

    def remember_client_display_name(self, identity: Any, value: Any) -> str:
        """Remember the latest optional name for a live canonical client."""
        owner_key = self._owner_key_from_identity(identity)
        if not owner_key:
            return ""
        client_name = _runtime._normalize_client_display_name(value)
        self._remember_runtime_client_name(
            self.client_display_names_by_owner,
            owner_key=owner_key,
            client_name=client_name,
        )
        return client_name

    #: Bounds the per-device ownership memory; the oldest device is forgotten
    #: first and simply reads as shared until it pairs again.
    _DEVICE_OWNERSHIP_MEMORY = 1024

    def remember_device_ownership(self, identity: Any, ownership: Any) -> str:
        """Remember whether a paired device is the owner's own or shared, by device."""
        owner_key = self._owner_key_from_identity(identity)
        normalized = normalize_device_ownership(ownership)
        if not owner_key:
            return normalized
        memory = self.device_ownership_by_owner
        memory.pop(owner_key, None)
        while len(memory) >= self._DEVICE_OWNERSHIP_MEMORY:
            memory.pop(next(iter(memory)))
        memory[owner_key] = normalized
        return normalized

    def device_ownership_for_session(self, session_id: Any) -> str:
        """Own or shared for a live session; unknown devices are shared."""
        try:
            identity = self._resolve_chat_identity(str(session_id or "").strip())
        except Exception:
            return DEVICE_SHARED
        owner_key = self._owner_key_from_identity(identity)
        return normalize_device_ownership(self.device_ownership_by_owner.get(owner_key)) if owner_key else DEVICE_SHARED

    _MESSAGING_PARTNER_LABELS = {
        "telegram": "Telegram",
        "telegram_user": "Telegram",
        "signal": "Signal",
        "whatsapp": "WhatsApp",
    }

    def describe_connected_device(self, session_id: Any) -> Dict[str, str]:
        """How a live session's device reached this computer, for the admin view."""
        try:
            identity = self._resolve_chat_identity(str(session_id or "").strip())
        except Exception:
            identity = None
        transport = str(getattr(identity, "transport", "") or "").strip().lower()
        pairing_mode = str(getattr(identity, "pairing_mode", "") or "").strip().lower()
        ownership = self.device_ownership_for_session(session_id)
        if transport == "cloud" or pairing_mode == "cloud_pair":
            connected_via = "AutoYou Cloud"
        elif transport in self._MESSAGING_PARTNER_LABELS:
            connected_via = self._MESSAGING_PARTNER_LABELS[transport]
        elif transport in {"bluetooth", "bluetooth-local"} or pairing_mode == "bluetooth_pair":
            connected_via = "Bluetooth"
        elif transport in {"local", "windows-local", "admin-web"} or pairing_mode in {"local_pair", "secure_pair", "totp_pair"}:
            connected_via = "This computer" if ownership == DEVICE_OWN else "Local network"
        elif transport in {"tunnelmole", "public", "pair"}:
            connected_via = "Public link"
        else:
            connected_via = "Direct pairing"
        return {
            "device_ownership": ownership,
            "connected_via": connected_via,
            "pairing_mode": pairing_mode or "auto_pair",
        }

    def _same_machine_audio_session(self, session_id: str) -> bool:
        return any(alias in self.same_machine_audio_sessions
                   for alias in self._ordered_related_session_ids(str(session_id or "")))

    def host_audio_owner(self) -> str:
        for session_id in self.same_machine_audio_sessions:
            if self._voice_call_client_active_for_session(session_id):
                return "connected_call"
            owner = self.host_media_owner_by_session.get(session_id, "")
            if owner in {"connected_call", "lobby", "peer", "recording"}:
                return owner
        return ""

    def server_capture_allowed_for_session(self, session_id: str) -> bool:
        return bool(self.server_microphone_sharing_enabled
                    and not self._same_machine_audio_session(session_id)
                    and not self.host_audio_owner())

    def connected_device_count(self, *, same_machine_only: bool = False) -> int:
        connections: Set[int] = set()
        for session_id, manager in self.datachannel_managers.items():
            if same_machine_only and not self._same_machine_audio_session(session_id):
                continue
            channel = getattr(manager, "datachannel", None)
            if str(getattr(channel, "readyState", "") or "").lower() != "open":
                continue
            connections.add(id(manager))
        return len(connections)

    async def set_server_microphone_sharing(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("Choose whether to share the computer microphone")
        if self.server_microphone_sharing_enabled == enabled:
            return
        self.server_microphone_sharing_enabled = enabled
        await self._rewire_outbound_audio_for_host()

    async def _rewire_outbound_audio_for_host(self) -> None:
        """Rebuild live outbound tracks after the host capture owner changes."""
        seen: Set[int] = set()
        for session_id in list(self.audio_transceivers):
            _, transceiver = self._audio_transceiver_for_session(session_id)
            if transceiver is None or id(transceiver) in seen:
                continue
            seen.add(id(transceiver))
            if self._background_audio_state_for_session(session_id).get("active"):
                continue
            sender = getattr(transceiver, "sender", None)
            if getattr(sender, "track", None) is None and not self._voice_call_client_active_for_session(session_id):
                continue
            self._restore_outbound_audio_for_call(session_id)

    def set_client_name_override(self, owner_key: Any, value: Any) -> str:
        normalized_owner_key = _runtime._normalize_client_identity_owner_key(owner_key)
        client_name = _runtime._normalize_client_display_name(value)
        self._remember_runtime_client_name(
            self.client_name_overrides_by_owner,
            owner_key=normalized_owner_key,
            client_name=client_name,
        )
        return client_name

    def clear_client_name_overrides(self) -> None:
        self.client_name_overrides_by_owner.clear()

    def _client_display_name_for_identity(self, identity: Any) -> Dict[str, Any]:
        owner_key = self._owner_key_from_identity(identity)
        if not owner_key:
            return {
                "owner_key": "",
                "client_display_name": "",
                "reported_client_display_name": "",
                "server_name_override": "",
                "history_enabled": _runtime._client_name_history_enabled(),
            }
        reported_name = self.client_display_names_by_owner.get(owner_key, "")
        runtime_override = self.client_name_overrides_by_owner.get(owner_key, "")
        configured_override = _runtime._configured_client_name_override(owner_key)
        history_enabled = _runtime._client_name_history_enabled()
        server_override = configured_override if history_enabled else runtime_override
        return {
            "owner_key": owner_key,
            "client_display_name": server_override or reported_name,
            "reported_client_display_name": reported_name,
            "server_name_override": server_override,
            "history_enabled": history_enabled,
        }

    def client_display_name_snapshot(self, session_id: Any) -> Dict[str, Any]:
        try:
            identity = self._resolve_chat_identity(str(session_id or ""))
        except Exception:
            identity = None
        return self._client_display_name_for_identity(identity)

    def client_name_history_metadata(self, identity: Any) -> Dict[str, str]:
        snapshot = self._client_display_name_for_identity(identity)
        client_name = str(snapshot.get("client_display_name") or "")
        if snapshot.get("history_enabled") and client_name:
            return {"client_display_name": client_name}
        return {}

    def _drop_video_call_agent_offline_messages(self, *, reason: str) -> None:
        dropped_count = 0
        voice_labels = {"voice reply", "voice-note reply", "media reply"}
        for session_id, queue_ref in list(self._offline_pending_messages.items()):
            kept_entries = []
            for entry in list(queue_ref or []):
                label = str((entry or {}).get("label") or "").strip().lower()
                message = (entry or {}).get("message")
                payload = getattr(message, "payload", None)
                metadata = payload.get("metadata") if isinstance(payload, dict) else {}
                source = str((metadata or {}).get("source") or "").strip().lower() if isinstance(metadata, dict) else ""
                if label in voice_labels or source == "voice_call":
                    dropped_count += 1
                    continue
                kept_entries.append(entry)
            if kept_entries:
                self._offline_pending_messages[session_id] = kept_entries
            else:
                self._offline_pending_messages.pop(session_id, None)
        if dropped_count:
            _runtime.LOGGER.info("Dropped %d offline voice-call agent message(s): %s", dropped_count, reason)

    async def _stop_video_call_agent_processing(
        self,
        *,
        reason: str,
        stop_audio_sinks: bool = True,
        preserve_audio_runtime: bool = False,
    ) -> None:
        if stop_audio_sinks and not preserve_audio_runtime:
            stopped_audio_sinks: List[Any] = []
            for session_id, audio_sink in list(self.audio_sinks.items()):
                if any(audio_sink is current for current in stopped_audio_sinks):
                    self.audio_sinks.pop(session_id, None)
                    continue
                self.audio_sinks.pop(session_id, None)
                stopped_audio_sinks.append(audio_sink)
                try:
                    if await self._await_cleanup_awaitable(
                        audio_sink.stop(),
                        session_id=session_id,
                        label=reason,
                    ):
                        _runtime.LOGGER.info("Stopped voice audio sink for %s: %s", session_id, reason)
                except Exception as exc:
                    _runtime.LOGGER.warning("Failed to stop voice audio sink for %s after %s: %s", session_id, reason, exc)

        for session_id in sorted(set(self.voice_command_queues.keys()) | set(self.voice_command_workers.keys())):
            try:
                await self._stop_voice_command_worker(session_id)
            except Exception as exc:
                _runtime.LOGGER.warning("Failed to stop voice command worker for %s after %s: %s", session_id, reason, exc)

        if self.pending_voice_chat_messages:
            pending_count = sum(len(messages or []) for messages in self.pending_voice_chat_messages.values())
            self.pending_voice_chat_messages.clear()
            _runtime.LOGGER.info("Cleared %d pending voice transcript message(s): %s", pending_count, reason)
        self._drop_video_call_agent_offline_messages(reason=reason)

        if not preserve_audio_runtime:
            closed_audio_managers: List[Any] = []
            for session_id, audio_manager in list(getattr(_runtime.STATE, "audio_managers", {}).items()):
                if audio_manager is None or any(audio_manager is current for current in closed_audio_managers):
                    _runtime.STATE.audio_managers.pop(session_id, None)
                    continue
                alias_ids = self._purge_audio_manager_aliases(audio_manager, close_manager=True)
                closed_audio_managers.append(audio_manager)
                _runtime.LOGGER.info(
                    "Closed voice AudioManager for %s across aliases %s: %s",
                    session_id,
                    sorted(alias_ids),
                    reason,
                )

    async def apply_video_call_settings(self, *, force_audio_rewire: bool = False) -> None:
        cfg = _runtime.STATE.config or {}
        self.server_microphone_sharing_enabled = bool(
            _runtime._get_video_call_config(cfg=cfg).get("server_microphone_sharing_enabled", True))
        video_enabled = _runtime._get_video_call_enabled(cfg=cfg)
        audio_enabled = _runtime._get_video_call_audio_enabled(cfg=cfg)
        agent_processing_enabled = _runtime._get_video_call_agent_processing_enabled(cfg=cfg)
        recording_enabled = bool(video_enabled and _runtime._get_video_record_my_video_enabled(cfg=cfg))
        recording_dir = _runtime._resolve_video_recording_dir(cfg=cfg)
        recording_mode = _runtime._get_video_recording_mode(cfg=cfg)
        image_interval_seconds = _runtime._get_video_image_interval_seconds(cfg=cfg)
        if "video_file" in _runtime._get_video_outbound_sources(cfg=cfg):
            _runtime._configure_video_file_playback_from_config(cfg=cfg, restart=False)

        if not _runtime._get_silent_recording_enabled(cfg=cfg):
            self._close_silent_recorders_for_ids(list(self.silent_recorders.keys()))
        if not _runtime._get_wuift_enabled(cfg=cfg):
            for session_id, hold_active in list(self.wuift_hold_by_session.items()):
                if not hold_active:
                    continue
                self.wuift_hold_by_session[session_id] = False
                audio_manager = _runtime.STATE.audio_managers.get(session_id)
                if audio_manager and hasattr(audio_manager, "set_segmentation_hold"):
                    audio_manager.set_segmentation_hold(False, source=f"admin:{session_id}:wuift_disabled")
        for session_id, state in list(self.background_audio_state_by_session.items()):
            if not isinstance(state, dict) or not state.get("active"):
                continue
            if self._background_audio_mode_allowed(
                silent_recording=bool(state.get("silent_recording")),
                cfg=cfg,
            ):
                continue
            if not self._voice_call_client_active_for_session(session_id):
                self._clear_outbound_audio_track_for_idle(session_id)
            self._set_background_audio_state(
                session_id,
                active=False,
                silent_recording=False,
                muted=True,
                platform=str(state.get("platform") or "unknown"),
                timestamp_ms=int(_runtime.time.time() * 1000),
            )
        if not self._background_audio_consumer_enabled(cfg=cfg):
            self.background_audio_state_by_session.clear()

        if not agent_processing_enabled:
            reason = (
                "AutoYou Agents are disabled for video calls"
                if _runtime._get_video_call_agents_disabled(cfg=cfg)
                else "server audio is disabled for video calls"
            )
            await self._stop_video_call_agent_processing(
                reason=reason,
                stop_audio_sinks=not self._background_audio_consumer_enabled(cfg=cfg),
                # The live sink callback already reads the current config before
                # feeding STT. Keep the negotiated receiver and AudioManager
                # available across a master-audio off/on toggle so the active
                # call can resume without renegotiation.
                preserve_audio_runtime=not audio_enabled,
            )

        outbound_video_available = _runtime._get_outbound_video_available(cfg=cfg)
        remote_desktop_allowed = "remote_desktop" in _runtime._get_available_video_outbound_sources(cfg=cfg)
        desktop_profile = _runtime._get_remote_desktop_capture_profile(cfg=cfg)
        updated_tracks: set[int] = set()
        for session_id, desktop_track in list(self.desktop_video_tracks.items()):
            if desktop_track is None or id(desktop_track) in updated_tracks:
                continue
            updated_tracks.add(id(desktop_track))
            try:
                apply_policy = getattr(desktop_track, "set_remote_desktop_enabled", None)
                if callable(apply_policy):
                    apply_policy(remote_desktop_allowed)
                if not outbound_video_available:
                    desktop_track.disable()
                    _runtime.LOGGER.info("Disabled outbound video track for %s after video settings update", session_id)
                    continue
                if remote_desktop_allowed:
                    apply_profile = getattr(desktop_track, "apply_remote_desktop_profile", None)
                    if callable(apply_profile):
                        game_active = any(
                            lease.get("mode") == "game" and lease.get("track") is desktop_track
                            for lease in self.remote_desktop_control_leases_by_session.values()
                        )
                        apply_profile(
                            monitor_id=desktop_profile["monitor_id"],
                            fps=30 if game_active else desktop_profile["fps"],
                            max_width=desktop_profile["max_width"],
                        )
                    elif hasattr(desktop_track, "monitor_id"):
                        desktop_track.monitor_id = desktop_profile["monitor_id"]
                    _runtime.LOGGER.info(
                        "Updated outbound remote desktop profile for %s: %s",
                        session_id,
                        desktop_profile,
                    )
            except Exception as exc:
                _runtime.LOGGER.warning("Failed to update outbound video policy for %s: %s", session_id, exc)

        desktop_control_available = _runtime._get_remote_desktop_control_available(cfg=cfg)
        game_control_available = _runtime._get_game_mode_available(cfg=cfg)
        if not desktop_control_available or not game_control_available:
            released_leases: set[int] = set()
            for session_id, lease in list(self.remote_desktop_control_leases_by_session.items()):
                lease_available = game_control_available if lease.get("mode") == "game" else desktop_control_available
                if lease_available:
                    continue
                if id(lease) in released_leases:
                    continue
                released_leases.add(id(lease))
                await self._release_remote_desktop_control(
                    session_id,
                    str(lease.get("control_id") or ""),
                )

        # Re-apply outbound call audio sources (AI replies / computer microphone /
        # speaker loopback) to live voice calls so enabling "Add this computer's
        # audio" or changing the microphone mid-call takes effect immediately
        # instead of waiting for the next call.
        audio_signature = (
            bool(_runtime._get_video_call_audio_enabled(cfg=cfg)),
            self.server_microphone_sharing_enabled,
            tuple(_runtime._get_video_audio_sources(cfg=cfg)),
            _runtime._get_video_input_audio_source(cfg=cfg),
            bool(_runtime._get_ai_audio_replies_enabled(cfg=cfg)),
            bool(_runtime._get_audio_playback_enabled(cfg=cfg)),
        )
        previous_audio_signature = self._outbound_call_audio_signature
        self._outbound_call_audio_signature = audio_signature
        if force_audio_rewire or previous_audio_signature != audio_signature:
            restored_transceiver_ids: set[int] = set()
            for candidate_id in list(self.audio_transceivers.keys()):
                if not self._voice_call_client_active_for_session(candidate_id):
                    continue
                _, transceiver = self._audio_transceiver_for_session(candidate_id)
                if transceiver is None or id(transceiver) in restored_transceiver_ids:
                    continue
                restored_transceiver_ids.add(id(transceiver))
                try:
                    self._restore_outbound_audio_for_call(candidate_id)
                    _runtime.LOGGER.info(
                        "Re-applied outbound call audio sources for %s after video call settings update",
                        _runtime.redact_identifier(candidate_id),
                    )
                except Exception as exc:
                    _runtime.LOGGER.warning("Failed to re-apply outbound call audio for %s: %s", candidate_id, exc)

        if not video_enabled:
            for session_id, video_sink in list(self.video_sinks.items()):
                self.video_sinks.pop(session_id, None)
                try:
                    await self._await_cleanup_awaitable(
                        video_sink.stop(),
                        session_id=session_id,
                        label="video sink disabled by config",
                    )
                except Exception as exc:
                    _runtime.LOGGER.warning("Failed to stop video sink for %s after video disable: %s", session_id, exc)
            return

        for sink in list(self.video_sinks.values()):
            if hasattr(sink, "recording_enabled"):
                try:
                    sink.recording_enabled = recording_enabled
                    sink.recording_dir = recording_dir
                    sink.recording_mode = recording_mode
                    sink.image_interval_seconds = image_interval_seconds
                except Exception:
                    pass

    def _cleanup_stale_ws_fragments(
        self,
        *,
        now_monotonic: Optional[float] = None,
        request_id: Optional[str] = None,
    ) -> None:
        if not self._ws_fragment_buffers:
            return
        now_value = now_monotonic if now_monotonic is not None else _runtime.time.monotonic()
        cutoff = now_value - 60.0
        stale_ids = []
        for fragment_id, entry in list(self._ws_fragment_buffers.items()):
            if request_id and entry.get("request_id") != request_id:
                continue
            updated_at = float(entry.get("updated_at") or 0.0)
            if request_id or updated_at < cutoff:
                stale_ids.append(fragment_id)
        for fragment_id in stale_ids:
            self._ws_fragment_buffers.pop(fragment_id, None)

    def _consume_ws_fragment_payload(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        fragment_id = str(payload.get("fragment_id") or "").strip()
        total_fragments_raw = payload.get("total_fragments")
        if not fragment_id or total_fragments_raw in (None, ""):
            return payload

        try:
            total_fragments = int(total_fragments_raw)
            fragment_index = int(payload.get("fragment_index") or 0)
        except (TypeError, ValueError):
            _runtime.LOGGER.warning("Ignoring malformed WS fragment metadata for request_id=%s", payload.get("request_id") or "")
            return None

        if total_fragments <= 1:
            return payload
        if fragment_index < 0 or fragment_index >= total_fragments:
            _runtime.LOGGER.warning(
                "Ignoring WS fragment with out-of-range index request_id=%s fragment_id=%s index=%s total=%s",
                payload.get("request_id") or "",
                fragment_id,
                fragment_index,
                total_fragments,
            )
            return None

        self._cleanup_stale_ws_fragments()

        opcode = str(payload.get("opcode") or "").lower()
        is_binary = bool(payload.get("binary")) or opcode == "binary" or bool(payload.get("data_b64"))
        part_data = payload.get("data_b64") if is_binary else payload.get("data")
        if not isinstance(part_data, str):
            _runtime.LOGGER.warning(
                "Ignoring WS fragment missing payload request_id=%s fragment_id=%s index=%s",
                payload.get("request_id") or "",
                fragment_id,
                fragment_index,
            )
            return None

        entry = self._ws_fragment_buffers.get(fragment_id)
        if entry is None:
            entry = {
                "request_id": payload.get("request_id") or "",
                "opcode": "binary" if is_binary else "text",
                "binary": is_binary,
                "total_fragments": total_fragments,
                "parts": {},
                "updated_at": _runtime.time.monotonic(),
            }
            self._ws_fragment_buffers[fragment_id] = entry

        if entry.get("request_id") != (payload.get("request_id") or "") or int(entry.get("total_fragments") or 0) != total_fragments:
            _runtime.LOGGER.warning(
                "Resetting inconsistent WS fragment buffer request_id=%s fragment_id=%s",
                payload.get("request_id") or "",
                fragment_id,
            )
            self._ws_fragment_buffers.pop(fragment_id, None)
            return None

        parts = entry.get("parts") or {}
        parts[fragment_index] = part_data
        entry["parts"] = parts
        entry["updated_at"] = _runtime.time.monotonic()

        if len(parts) < total_fragments:
            return None

        try:
            combined = "".join(str(parts[index]) for index in range(total_fragments))
        except KeyError:
            return None
        finally:
            self._ws_fragment_buffers.pop(fragment_id, None)

        reassembled = dict(payload)
        reassembled.pop("fragment_id", None)
        reassembled.pop("fragment_index", None)
        reassembled.pop("total_fragments", None)
        if is_binary:
            reassembled["opcode"] = "binary"
            reassembled["binary"] = True
            reassembled["data_b64"] = combined
            reassembled["data"] = "base64:" + combined
        else:
            reassembled["opcode"] = "text"
            reassembled["binary"] = False
            reassembled["data"] = combined
            reassembled.pop("data_b64", None)
        return reassembled

    async def _publish_voice_call_status(self, session_id: str, payload: Dict[str, Any]) -> None:
        raw_payload = dict(payload or {}) if isinstance(payload, dict) else {}
        event_name = str(raw_payload.get("event") or "readiness").strip().lower() or "readiness"
        if event_name == "playback":
            existing = self.voice_call_playback_by_session.get(session_id)
        else:
            existing = self.voice_call_status_by_session.get(session_id)

        normalized = dict(existing) if isinstance(existing, dict) else {}
        normalized.update(raw_payload)
        normalized["event"] = event_name
        normalized.setdefault("timestamp_ms", int(_runtime.time.time() * 1000))
        normalized.setdefault("platform", "server")
        if event_name == "playback":
            self.voice_call_playback_by_session[session_id] = normalized
        else:
            self.voice_call_status_by_session[session_id] = normalized

        datachannel_manager = self._datachannel_manager_for_session(session_id, require_send_message=True)
        if not datachannel_manager:
            _runtime.LOGGER.debug("Voice call status buffered for %s until the datachannel is ready", session_id)
            return

        try:
            message = _runtime.create_voice_call_control_message(
                payload=normalized,
                session_id=session_id,
                user_id=_runtime.get_configured_server_name(),
            )
            await datachannel_manager.send_message(message)
        except Exception as exc:
            _runtime.LOGGER.warning("Failed to send voice call status for %s: %s", session_id, exc)

    def _get_audio_manager_readiness_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return None
        manager = _runtime.STATE.audio_managers.get(normalized_session_id)
        if manager is None:
            return None
        getter = getattr(manager, "get_readiness_status", None)
        if callable(getter):
            try:
                status = getter()
                if isinstance(status, dict):
                    return dict(status)
            except Exception as exc:
                _runtime.LOGGER.debug(
                    "Failed to read audio manager readiness for %s: %s",
                    _runtime._redact_session_id(normalized_session_id),
                    exc,
                )
        if getattr(manager, "recorder", None) is not None:
            return {
                "event": "readiness",
                "state": "ready",
                "detail": "Voice pipeline ready.",
                "timestamp_ms": int(_runtime.time.time() * 1000),
                "platform": "server",
            }
        return None

    async def _publish_webrtc_capabilities(self, session_id: str) -> None:
        datachannel_manager = self._datachannel_manager_for_session(session_id, require_send_message=True)
        if not datachannel_manager:
            return
        try:
            message = _runtime.create_voice_call_control_message(
                payload={
                    "event": "server_capabilities",
                    "capabilities": _runtime._build_webrtc_capabilities(cfg=(_runtime.STATE.config or {})),
                    "timestamp_ms": int(_runtime.time.time() * 1000),
                    "platform": "server",
                },
                session_id=session_id,
                user_id=_runtime.get_configured_server_name(),
            )
            await datachannel_manager.send_message(message)
        except Exception as exc:
            _runtime.LOGGER.warning("Failed to send WebRTC capabilities for %s: %s", session_id, exc)

    async def _prime_conversation_context_status(self, session_id: str, identity: Any) -> None:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return
        try:
            snapshot = await _runtime._conversation_context_usage_snapshot(identity)
            await self._publish_voice_call_status(
                normalized_session_id,
                {"context_usage": snapshot},
            )
        except Exception as exc:
            _runtime.LOGGER.debug(
                "Failed to prime conversation context status for %s: %s",
                normalized_session_id,
                exc,
            )

    def _buffer_voice_chat_message(self, session_id: str, message: Any, *, label: str) -> str:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return "failed"

        # Voice replies are stored in the offline queue (persists across reconnects).
        # All other labels (transcript, status, …) use the short-term in-memory buffer.
        if label in {"voice reply", "voice-note reply", "media reply"}:
            return self._enqueue_to_offline_queue(normalized_session_id, message)

        queue_ref = self.pending_voice_chat_messages.setdefault(normalized_session_id, [])
        queue_ref.append(message)
        if len(queue_ref) > self.voice_command_queue_maxsize:
            queue_ref.pop(0)
            _runtime.LOGGER.warning(
                "Dropped oldest buffered %s for %s because the voice chat buffer exceeded %d entries",
                label,
                normalized_session_id,
                self.voice_command_queue_maxsize,
            )
        _runtime.LOGGER.info("Buffered %s for %s until the datachannel is ready", label, normalized_session_id)
        return "buffered"

    async def _deliver_voice_chat_message(self, session_id: str, message: Any, *, label: str) -> str:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return "failed"

        datachannel_manager = self._datachannel_manager_for_session(
            normalized_session_id,
            require_send_message=True,
        )
        if datachannel_manager:
            try:
                if await datachannel_manager.send_message(message):
                    return "sent"
            except Exception as exc:
                _runtime.LOGGER.warning(
                    "Failed to send %s to %s; buffering for retry: %s",
                    label,
                    normalized_session_id,
                    exc,
                )

        return self._buffer_voice_chat_message(normalized_session_id, message, label=label)

    async def _flush_pending_voice_chat_messages(self, session_id: str) -> None:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return

        lock = self._voice_chat_flush_locks.setdefault(normalized_session_id, _runtime.asyncio.Lock())
        async with lock:
            queue_ref = self.pending_voice_chat_messages.get(normalized_session_id)
            if not queue_ref:
                # Short-term buffer is empty; still try to deliver any offline-queued replies.
                await self._flush_offline_queue(normalized_session_id)
                return

            datachannel_manager = self._datachannel_manager_for_session(
                normalized_session_id,
                require_send_message=True,
            )
            if not datachannel_manager:
                return

            flushed = 0
            while queue_ref:
                message = queue_ref[0]
                try:
                    sent = bool(await datachannel_manager.send_message(message))
                except Exception as exc:
                    _runtime.LOGGER.warning(
                        "Failed to flush buffered voice chat message for %s: %s",
                        normalized_session_id,
                        exc,
                    )
                    sent = False

                if not sent:
                    break

                if not queue_ref:
                    break
                if queue_ref[0] is message:
                    queue_ref.pop(0)
                else:
                    try:
                        queue_ref.remove(message)
                    except ValueError:
                        pass
                flushed += 1

            if not queue_ref:
                self.pending_voice_chat_messages.pop(normalized_session_id, None)

            if flushed:
                _runtime.LOGGER.info(
                    "Flushed %d buffered voice chat message(s) for %s",
                    flushed,
                    normalized_session_id,
                )

            await self._flush_offline_queue(normalized_session_id)

    def _annotate_client_message_delivery_metadata(
        self,
        message: Any,
        *,
        delivery_mode: str,
        label: str,
        enqueued_at: Optional[float] = None,
    ) -> None:
        try:
            payload = getattr(message, "payload", None)
            if not isinstance(payload, dict):
                return
            metadata = payload.get("metadata")
            normalized_metadata = dict(metadata) if isinstance(metadata, dict) else {}
            normalized_metadata.setdefault(
                "notification_delivery_mode",
                str(delivery_mode or "").strip() or "offline_queue",
            )
            normalized_metadata["notification_transport_delivery_mode"] = (
                str(delivery_mode or "").strip() or "offline_queue"
            )
            normalized_metadata["notification_delivery_label"] = str(label or "").strip()
            if enqueued_at is not None:
                normalized_metadata["notification_enqueued_at_s"] = float(enqueued_at)
                normalized_metadata["notification_enqueued_at_ms"] = int(float(enqueued_at) * 1000)
            payload["metadata"] = normalized_metadata
        except Exception as exc:
            _runtime.LOGGER.debug(
                "Failed to annotate client message delivery metadata for %s: %s",
                label,
                exc,
            )

    def _enqueue_to_offline_queue(self, session_id: str, message: Any, *, label: str = "voice reply") -> str:
        """Store a message in the offline queue so it survives WebRTC disconnects.

        The queue is keyed by the stable per-device session identifier (cloud relay_id
        or phone UUID). Entries expire after _WEBRTC_OFFLINE_QUEUE_TTL_SECONDS and the
        queue is capped at _WEBRTC_OFFLINE_QUEUE_MAX_SIZE to prevent OOM.
        """
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return "failed"

        now = _runtime.time.time()
        cutoff = now - _runtime._WEBRTC_OFFLINE_QUEUE_TTL_SECONDS
        queue_ref = self._offline_pending_messages.setdefault(normalized_session_id, [])

        # Evict expired entries first.
        while queue_ref and queue_ref[0]["enqueued_at"] < cutoff:
            queue_ref.pop(0)

        # Enforce size cap - drop the oldest if the queue is full.
        if len(queue_ref) >= _runtime._WEBRTC_OFFLINE_QUEUE_MAX_SIZE:
            queue_ref.pop(0)
            _runtime.LOGGER.warning(
                "Offline queue cap (%d) reached for %s; dropped oldest entry",
                _runtime._WEBRTC_OFFLINE_QUEUE_MAX_SIZE,
                normalized_session_id,
            )

        self._annotate_client_message_delivery_metadata(
            message,
            delivery_mode="offline_queue",
            label=label,
            enqueued_at=now,
        )
        queue_ref.append({"enqueued_at": now, "message": message, "label": label})
        _runtime.LOGGER.info(
            "Queued %s for %s in offline queue (%d pending, TTL=%dd)",
            label,
            normalized_session_id,
            len(queue_ref),
            _runtime._WEBRTC_OFFLINE_QUEUE_TTL_SECONDS // 86400,
        )
        self._schedule_offline_replies_nudge(normalized_session_id)
        return "buffered"

    def _schedule_offline_replies_nudge(self, session_id: str) -> None:
        """Fire-and-forget the cloud nudge; never blocks or fails the enqueue."""
        try:
            loop = _runtime.asyncio.get_running_loop()
        except RuntimeError:
            return
        try:
            loop.create_task(self._maybe_send_offline_replies_nudge(session_id))
        except Exception as exc:
            _runtime.LOGGER.debug("Could not schedule offline replies nudge for %s: %s", session_id, exc)

    async def _maybe_send_offline_replies_nudge(self, session_id: str) -> None:
        """Send a content-free 'replies waiting' push when a Cloud Pair client
        misses replies while its device is unreachable.

        Privacy contract: the message text never leaves this server - the push
        carries only a generic title/body, so nothing user-generated transits
        APNs/FCM. Only cloud-owned sessions are nudged (Local Pair identities
        have no cloud account linkage), rate-limited per session so a burst of
        queued replies produces at most one push per cooldown window. The
        account-service applies its own paid/offline-tier gating on top.
        """
        if str(_runtime.os.getenv("AUTOYOU_OFFLINE_REPLIES_NUDGE", "1") or "").strip().lower() not in {"1", "true", "yes", "on"}:
            return
        cloud_cfg = (_runtime.STATE.config or {}).get("cloud") or {}
        if not str(cloud_cfg.get("server_token") or "").strip():
            return

        try:
            identity = self._resolve_chat_identity(session_id)
        except Exception as exc:
            _runtime.LOGGER.debug("Offline nudge skipped for %s; identity resolution failed: %s", session_id, exc)
            return
        owner_key = str(getattr(identity, "owner_key", "") or "").strip()
        canonical_user_id = str(getattr(identity, "canonical_user_id", "") or "").strip()
        if not (owner_key.startswith("cloud:") or canonical_user_id.startswith("user::cloud:")):
            return

        try:
            cooldown = float(_runtime.os.getenv("AUTOYOU_OFFLINE_NUDGE_COOLDOWN_SECONDS", "1800"))
        except Exception:
            cooldown = 1800.0
        now = _runtime.time.time()
        last_sent = self._offline_nudge_last_sent_at.get(session_id, 0.0)
        if (now - last_sent) < max(60.0, cooldown):
            return
        self._offline_nudge_last_sent_at[session_id] = now

        try:
            result = await _runtime._notify_cloud_client(
                title="AutoYou",
                body=f"New replies are waiting on {_runtime.get_configured_server_name()}.",
                category="offline_replies",
                data={"source": "offline_queue", "delivery": "offline_replies_nudge"},
            )
            if not result.get("sent"):
                # Let the next enqueue retry instead of waiting out the cooldown.
                self._offline_nudge_last_sent_at.pop(session_id, None)
                _runtime.LOGGER.debug(
                    "Offline replies nudge not delivered for %s: %s",
                    session_id,
                    result.get("error") or result.get("detail") or result,
                )
        except Exception as exc:
            self._offline_nudge_last_sent_at.pop(session_id, None)
            _runtime.LOGGER.debug("Offline replies nudge failed for %s: %s", session_id, exc)

    async def _flush_offline_queue(self, session_id: str) -> None:
        """Deliver offline-queued voice replies to the reconnected datachannel.

        Called from _flush_pending_voice_chat_messages after the short-term buffer
        is drained, ensuring the client receives all AI replies that accumulated
        while the connection was down. Privacy is maintained by keying per session_id;
        no messages are delivered to a different client's session.
        """
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return

        lock = self._offline_queue_flush_locks.setdefault(normalized_session_id, _runtime.asyncio.Lock())
        async with lock:
            queue_ref = self._offline_pending_messages.get(normalized_session_id)
            if not queue_ref:
                return

            datachannel_manager = self._datachannel_manager_for_session(
                normalized_session_id,
                require_send_message=True,
            )
            if not datachannel_manager:
                return

            now = _runtime.time.time()
            cutoff = now - _runtime._WEBRTC_OFFLINE_QUEUE_TTL_SECONDS
            flushed = 0

            while queue_ref:
                entry = queue_ref[0]
                if entry["enqueued_at"] < cutoff:
                    queue_ref.pop(0)
                    continue

                try:
                    sent = bool(await datachannel_manager.send_message(entry["message"]))
                except Exception as exc:
                    _runtime.LOGGER.warning(
                        "Failed to flush offline queued message for %s: %s",
                        normalized_session_id,
                        exc,
                    )
                    sent = False

                if not sent:
                    break

                if not queue_ref:
                    break
                if queue_ref[0] is entry:
                    queue_ref.pop(0)
                else:
                    try:
                        queue_ref.remove(entry)
                    except ValueError:
                        pass
                flushed += 1

            if not queue_ref:
                self._offline_pending_messages.pop(normalized_session_id, None)
                # The client caught up; the next offline period may nudge again
                # without waiting out the cooldown window.
                self._offline_nudge_last_sent_at.pop(normalized_session_id, None)

            if flushed:
                _runtime.LOGGER.info(
                    "Delivered %d offline queued message(s) for %s",
                    flushed,
                    normalized_session_id,
                )

    def _make_voice_call_status_callback(
        self,
        session_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> Callable[[Dict[str, Any]], None]:
        def _callback(payload: Dict[str, Any]) -> None:
            payload_snapshot = dict(payload or {})

            def _schedule() -> None:
                self._track_session_task(
                    session_id,
                    self._publish_voice_call_status(session_id, payload_snapshot),
                    "voice_call_status",
                )

            try:
                loop.call_soon_threadsafe(_schedule)
            except RuntimeError:
                _runtime.LOGGER.debug("Skipping voice call status update for %s because the loop is closed", session_id)

        return _callback

    def _cancel_session_establishment_timeout(
        self,
        session_id: str,
        expected_pc: Optional[RTCPeerConnection] = None,
        reason: Optional[str] = None,
    ) -> None:
        current_pc = self.session_peers.get(session_id)
        if expected_pc is not None and current_pc is not None and current_pc is not expected_pc:
            return
        task = self.session_establishment_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
            if reason:
                _runtime.LOGGER.debug(
                    "Cleared WebRTC establish timeout for %s (%s)",
                    session_id,
                    reason,
                )

    def _arm_session_establishment_timeout(
        self,
        session_id: str,
        pc: RTCPeerConnection,
        *,
        label: str,
    ) -> None:
        self._cancel_session_establishment_timeout(session_id, expected_pc=pc)

        async def _watch_establishment() -> None:
            try:
                await _runtime.asyncio.sleep(self.session_establish_timeout_seconds)
                active_pc = self.session_peers.get(session_id)
                if active_pc is not pc:
                    return
                if session_id in self.datachannel_managers:
                    return
                if session_id in getattr(_runtime.STATE, "audio_managers", {}):
                    return
                if session_id in self.video_sinks:
                    return
                connection_state = getattr(pc, "connectionState", None)
                ice_state = getattr(pc, "iceConnectionState", None)
                _runtime.LOGGER.warning(
                    "WebRTC %s session %s did not establish a datachannel or media track within %.1fs "
                    "(connection_state=%s, ice_state=%s); cleaning up stale peer",
                    label,
                    session_id,
                    self.session_establish_timeout_seconds,
                    connection_state,
                    ice_state,
                )
                self.cleanup_session(session_id, expected_pc=pc)
            except _runtime.asyncio.CancelledError:
                return
            except Exception as exc:
                _runtime.LOGGER.warning(
                    "WebRTC establish-timeout watcher failed for %s: %s",
                    session_id,
                    exc,
                )

        task = _runtime.track_background_task(_watch_establishment())
        self.session_establishment_tasks[session_id] = task

        def _done(done_task: asyncio.Task) -> None:
            current = self.session_establishment_tasks.get(session_id)
            if current is done_task:
                self.session_establishment_tasks.pop(session_id, None)

        task.add_done_callback(_done)

    def _cancel_session_disconnect_grace(
        self,
        session_id: str,
        expected_pc: Optional[RTCPeerConnection] = None,
        reason: Optional[str] = None,
    ) -> None:
        current_pc = self.session_peers.get(session_id)
        if expected_pc is not None and current_pc is not None and current_pc is not expected_pc:
            return
        task = self.session_disconnect_grace_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
            if reason:
                _runtime.LOGGER.debug(
                    "Cleared WebRTC disconnect grace for %s (%s)",
                    session_id,
                    reason,
                )

    def _arm_session_disconnect_grace(
        self,
        session_id: str,
        pc: RTCPeerConnection,
        *,
        source: str,
    ) -> None:
        self._cancel_session_disconnect_grace(session_id, expected_pc=pc)

        if self.session_disconnect_grace_seconds <= 0:
            _runtime.LOGGER.warning(
                "WebRTC %s session %s entered disconnected state with zero grace configured; cleaning up immediately",
                source,
                session_id,
            )
            self.cleanup_session(session_id, expected_pc=pc)
            return

        async def _watch_disconnect() -> None:
            try:
                await _runtime.asyncio.sleep(self.session_disconnect_grace_seconds)
                active_pc = self.session_peers.get(session_id)
                if active_pc is not pc:
                    return
                connection_state = str(getattr(pc, "connectionState", "") or "").lower()
                ice_state = str(getattr(pc, "iceConnectionState", "") or "").lower()
                if connection_state not in {"disconnected", "failed", "closed"} and ice_state not in {
                    "disconnected",
                    "failed",
                    "closed",
                }:
                    return
                _runtime.LOGGER.warning(
                    "WebRTC %s session %s remained disconnected for %.1fs "
                    "(connection_state=%s, ice_state=%s); cleaning up",
                    source,
                    session_id,
                    self.session_disconnect_grace_seconds,
                    connection_state or "unknown",
                    ice_state or "unknown",
                )
                self.cleanup_session(session_id, expected_pc=pc)
            except _runtime.asyncio.CancelledError:
                return
            except Exception as exc:
                _runtime.LOGGER.warning(
                    "WebRTC disconnect grace watcher failed for %s: %s",
                    session_id,
                    exc,
                )

        task = _runtime.track_background_task(_watch_disconnect())
        self.session_disconnect_grace_tasks[session_id] = task

        def _done(done_task: asyncio.Task) -> None:
            current = self.session_disconnect_grace_tasks.get(session_id)
            if current is done_task:
                self.session_disconnect_grace_tasks.pop(session_id, None)

        task.add_done_callback(_done)
        _runtime.LOGGER.info(
            "WebRTC %s session %s disconnected; allowing %.1fs for recovery before cleanup",
            source,
            session_id,
            self.session_disconnect_grace_seconds,
        )

    def _handle_transport_disconnect_state(
        self,
        session_id: str,
        pc: RTCPeerConnection,
        *,
        source: str,
        state: Optional[str],
    ) -> None:
        normalized_state = str(state or "").lower()
        if not normalized_state:
            return

        if normalized_state == "disconnected":
            self._arm_session_disconnect_grace(session_id, pc, source=source)
            return

        self._cancel_session_disconnect_grace(
            session_id,
            expected_pc=pc,
            reason=f"{source}:{normalized_state}",
        )

        if normalized_state in {"failed", "closed"}:
          self.cleanup_session(session_id, expected_pc=pc)

    def _track_session_task(
        self,
        session_id: str,
        coro: Awaitable[Any],
        label: str,
        *,
        survive_disconnect: bool = False,
    ) -> asyncio.Task:
        """Run a per-session task without blocking datachannel message dispatch."""
        task = _runtime.asyncio.create_task(coro)
        task_map = (
            self.session_reconnect_survivable_tasks
            if survive_disconnect
            else self.session_message_tasks
        )
        task_set = task_map.setdefault(session_id, set())
        task_set.add(task)
        _runtime.background_tasks.add(task)

        def _done(done_task: asyncio.Task) -> None:
            _runtime.background_tasks.discard(done_task)
            active = task_map.get(session_id)
            if active is not None:
                active.discard(done_task)
                if not active:
                    task_map.pop(session_id, None)
            try:
                exc = done_task.exception()
            except _runtime.asyncio.CancelledError:
                return
            except Exception as exc:
                _runtime.LOGGER.debug(f"Failed to inspect {label} task for {session_id}: {exc}")
                return
            if exc is not None:
                _runtime.LOGGER.warning(f"{label} task failed for {session_id}: {exc}")

        task.add_done_callback(_done)
        return task

    def _remember_http_proxy_request_task(
        self,
        session_id: str,
        request_id: str,
        task: asyncio.Task,
    ) -> None:
        normalized_session_id = str(session_id or "").strip()
        normalized_request_id = str(request_id or "").strip()
        if not normalized_session_id or not normalized_request_id:
            return

        session_tasks = self.http_proxy_request_tasks.setdefault(normalized_session_id, {})
        session_tasks[normalized_request_id] = task

        def _done(done_task: asyncio.Task) -> None:
            active = self.http_proxy_request_tasks.get(normalized_session_id)
            if active is None:
                return
            if active.get(normalized_request_id) is done_task:
                active.pop(normalized_request_id, None)
                if not active:
                    self.http_proxy_request_tasks.pop(normalized_session_id, None)

        task.add_done_callback(_done)

    async def _handle_http_request_cancel(self, message: 'DataChannelMessage') -> None:
        session_id = str(message.header.session_id or "").strip()
        payload = message.payload or {}
        request_id = str(payload.get("request_id") or "").strip()
        if not request_id:
            return

        reason = str(payload.get("reason") or "Client canceled request")
        task = None
        matched_session_id = session_id

        candidate_session_ids = [session_id]
        for related_id in sorted(self._related_voice_session_ids(session_id)):
            if related_id and related_id not in candidate_session_ids:
                candidate_session_ids.append(related_id)

        for candidate_session_id in candidate_session_ids:
            session_tasks = self.http_proxy_request_tasks.get(candidate_session_id)
            if not session_tasks:
                continue
            task = session_tasks.pop(request_id, None)
            if not session_tasks:
                self.http_proxy_request_tasks.pop(candidate_session_id, None)
            if task is not None:
                matched_session_id = candidate_session_id
                break

        if task is None:
            _runtime.LOGGER.debug(
                "HTTP proxy cancel arrived after completion or before registration session=%s request_id=%s (%s)",
                session_id,
                request_id,
                reason,
            )
            return

        if not task.done():
            task.cancel()
            with _runtime.suppress(_runtime.asyncio.CancelledError):
                await task

        _runtime.LOGGER.info(
            "HTTP proxy request canceled session=%s request_id=%s (%s)",
            matched_session_id,
            request_id,
            reason,
        )

    async def _handle_http_stream_abort(self, message: 'DataChannelMessage') -> None:
        session_id = str(message.header.session_id or "").strip()
        payload = message.payload or {}
        request_id = str(payload.get("request_id") or "").strip()
        if not request_id:
            return

        reason = str(payload.get("error") or payload.get("reason") or "Client aborted stream")
        task = None
        matched_session_id = session_id

        candidate_session_ids = [session_id]
        for related_id in sorted(self._related_voice_session_ids(session_id)):
            if related_id and related_id not in candidate_session_ids:
                candidate_session_ids.append(related_id)

        for candidate_session_id in candidate_session_ids:
            session_tasks = self.http_proxy_request_tasks.get(candidate_session_id)
            if not session_tasks:
                continue
            task = session_tasks.pop(request_id, None)
            if not session_tasks:
                self.http_proxy_request_tasks.pop(candidate_session_id, None)
            if task is not None:
                matched_session_id = candidate_session_id
                break

        if task is None:
            _runtime.LOGGER.debug(
                "HTTP stream abort arrived after completion or before registration session=%s request_id=%s (%s)",
                session_id,
                request_id,
                reason,
            )
            return

        if not task.done():
            task.cancel()
            with _runtime.suppress(_runtime.asyncio.CancelledError):
                await task

        _runtime.LOGGER.info(
            "HTTP stream aborted by client session=%s request_id=%s (%s)",
            matched_session_id,
            request_id,
            reason,
        )

    @staticmethod
    def _get_streamed_http_response_fallback_max_bytes() -> int:
        try:
            configured_limit = int(
                _runtime.os.environ.get("AUTOYOU_STREAMED_HTTP_RESPONSE_FALLBACK_MAX_BYTES", "0")
            )
        except Exception:
            configured_limit = 0
        return max(0, configured_limit)

    @staticmethod
    def _get_buffered_http_response_max_bytes() -> int:
        try:
            configured_limit = int(
                _runtime.os.environ.get("AUTOYOU_BUFFERED_HTTP_RESPONSE_MAX_BYTES", str(512 * 1024))
            )
        except Exception:
            configured_limit = 512 * 1024
        return max(0, configured_limit)

    @staticmethod
    def _consume_cleanup_task_result(task: asyncio.Task, label: str) -> None:
        try:
            exc = task.exception()
        except _runtime.asyncio.CancelledError:
            return
        except Exception as inspect_err:
            _runtime.LOGGER.debug("Failed to inspect %s after cleanup timeout: %s", label, inspect_err)
            return
        if exc is not None:
            _runtime.LOGGER.debug("%s finished after timeout with: %s", label, exc)

    async def _await_cleanup_awaitable(
        self,
        awaitable: Awaitable[Any],
        *,
        session_id: str,
        label: str,
        timeout_seconds: Optional[float] = None,
    ) -> bool:
        """Await one cleanup operation without letting it pin session teardown."""
        timeout = (
            _runtime.WEBRTC_RESOURCE_CLEANUP_TIMEOUT_SECONDS
            if timeout_seconds is None
            else max(0.05, float(timeout_seconds))
        )
        task = _runtime.asyncio.create_task(awaitable)
        task_label = f"{label} cleanup for {session_id}"
        try:
            done, _ = await _runtime.asyncio.wait({task}, timeout=timeout)
        except _runtime.asyncio.CancelledError:
            if not task.done():
                task.cancel()
                task.add_done_callback(
                    lambda done_task, task_label=task_label: self._consume_cleanup_task_result(done_task, task_label)
                )
            raise

        if task in done:
            try:
                await task
                return True
            except _runtime.asyncio.CancelledError:
                _runtime.LOGGER.debug("%s was cancelled", task_label)
                return False
            except Exception as exc:
                _runtime.LOGGER.warning("Error during %s: %s", task_label, exc)
                return False

        task.cancel()
        task.add_done_callback(
            lambda done_task, task_label=task_label: self._consume_cleanup_task_result(done_task, task_label)
        )
        _runtime.LOGGER.warning(
            "Timed out after %.1fs during %s",
            timeout,
            task_label,
        )
        return False

    async def _cancel_task_collection_with_timeout(
        self,
        tasks: Iterable[asyncio.Task],
        *,
        session_id: str,
        label: str,
        timeout_seconds: Optional[float] = None,
    ) -> bool:
        active_tasks = [task for task in list(tasks) if task is not None and not task.done()]
        if not active_tasks:
            return True

        timeout = (
            _runtime.WEBRTC_TASK_CANCEL_TIMEOUT_SECONDS
            if timeout_seconds is None
            else max(0.05, float(timeout_seconds))
        )
        for task in active_tasks:
            task.cancel()

        try:
            done, pending = await _runtime.asyncio.wait(active_tasks, timeout=timeout)
        except _runtime.asyncio.CancelledError:
            for task in active_tasks:
                if not task.done():
                    task.cancel()
                    task.add_done_callback(
                        lambda done_task, task_label=f"{label} for {session_id}": self._consume_cleanup_task_result(done_task, task_label)
                    )
            raise

        for task in done:
            self._consume_cleanup_task_result(task, f"{label} for {session_id}")

        if pending:
            for task in pending:
                task.add_done_callback(
                    lambda done_task, task_label=f"{label} for {session_id}": self._consume_cleanup_task_result(done_task, task_label)
                )
            _runtime.LOGGER.warning(
                "Timed out after %.1fs cancelling %d %s task(s) for WebRTC session %s",
                timeout,
                len(pending),
                label,
                session_id,
            )
            return False
        return True

    async def _cancel_session_message_tasks(
        self,
        session_id: str,
        expected_tasks: Optional[Set[asyncio.Task]] = None,
    ) -> None:
        """Cancel any transport-bound per-session datachannel tasks."""
        await self._cancel_tracked_session_tasks(
            self.session_message_tasks,
            session_id,
            expected_tasks=expected_tasks,
        )

    async def _cancel_session_reconnect_survivable_tasks(
        self,
        session_id: str,
        expected_tasks: Optional[Set[asyncio.Task]] = None,
    ) -> None:
        """Cancel reconnect-survivable tasks, used during process shutdown."""
        await self._cancel_tracked_session_tasks(
            self.session_reconnect_survivable_tasks,
            session_id,
            expected_tasks=expected_tasks,
        )

    async def _cancel_tracked_session_tasks(
        self,
        task_map: Dict[str, Set[asyncio.Task]],
        session_id: str,
        *,
        expected_tasks: Optional[Set[asyncio.Task]] = None,
    ) -> None:
        """Cancel tracked per-session tasks from the provided task map."""
        if expected_tasks is not None:
            current_tasks = task_map.get(session_id)
            if current_tasks is not expected_tasks:
                return

        tasks = list(task_map.pop(session_id, set()))
        if not tasks:
            return
        await self._cancel_task_collection_with_timeout(
            tasks,
            session_id=session_id,
            label="session",
        )

    def _related_voice_session_ids(self, session_id: str) -> Set[str]:
        """Return all session ids that share the same WebRTC voice state."""
        related_ids: Set[str] = {str(session_id)}

        related_datachannel_managers = [
            manager
            for sid, manager in self.datachannel_managers.items()
            if str(sid) in related_ids
        ]
        for sid, manager in list(self.datachannel_managers.items()):
            if any(manager is current for current in related_datachannel_managers):
                related_ids.add(str(sid))

        related_audio_managers = [
            manager
            for sid, manager in getattr(_runtime.STATE, "audio_managers", {}).items()
            if str(sid) in related_ids
        ]
        for sid, manager in list(getattr(_runtime.STATE, "audio_managers", {}).items()):
            if any(manager is current for current in related_audio_managers):
                related_ids.add(str(sid))

        return related_ids

    def _ordered_related_session_ids(self, session_id: str) -> List[str]:
        normalized_session_id = str(session_id or "").strip()
        ordered_ids: List[str] = []
        if normalized_session_id:
            ordered_ids.append(normalized_session_id)

        related_ids = self._related_voice_session_ids(normalized_session_id)
        mapped_client_id = str(self._voice_dc_session_id.get(normalized_session_id) or "").strip()
        mapped_targets = {normalized_session_id}
        if mapped_client_id:
            related_ids.add(mapped_client_id)
            mapped_targets.add(mapped_client_id)
        for raw_id, client_id in list(self._voice_dc_session_id.items()):
            if str(client_id or "").strip() in mapped_targets:
                related_ids.add(str(raw_id or "").strip())

        for related_id in sorted(related_ids):
            related_id = str(related_id or "").strip()
            if related_id and related_id not in ordered_ids:
                ordered_ids.append(related_id)
        return ordered_ids

    def _register_video_sink_aliases(self, primary_session_id: str, video_sink: Any) -> None:
        primary_id = str(primary_session_id or "").strip()
        if not primary_id or video_sink is None:
            return
        for alias_id in self._ordered_related_session_ids(primary_id):
            existing_sink = self.video_sinks.get(alias_id)
            if existing_sink is not None and existing_sink is not video_sink:
                _runtime.LOGGER.debug(
                    "Skipping inbound video sink alias %s -> %s because another sink is registered",
                    _runtime._redact_session_id(primary_id),
                    _runtime._redact_session_id(alias_id),
                )
                continue
            self.video_sinks[alias_id] = video_sink
            if alias_id != primary_id:
                try:
                    video_sink.add_alias(alias_id)
                except Exception:
                    pass
                try:
                    if _runtime.VIDEO_FRAME_REGISTRY is not None:
                        _runtime.VIDEO_FRAME_REGISTRY.alias_session(primary_id, alias_id)
                except Exception:
                    pass

    def _register_desktop_video_track_aliases(self, primary_session_id: str, desktop_track: Any) -> None:
        primary_id = str(primary_session_id or "").strip()
        if not primary_id or desktop_track is None:
            return
        for alias_id in self._ordered_related_session_ids(primary_id):
            existing_track = self.desktop_video_tracks.get(alias_id)
            if existing_track is not None and existing_track is not desktop_track:
                _runtime.LOGGER.debug(
                    "Skipping desktop video track alias %s -> %s because another track is registered",
                    _runtime._redact_session_id(primary_id),
                    _runtime._redact_session_id(alias_id),
                )
                continue
            self.desktop_video_tracks[alias_id] = desktop_track

    def _prefer_remote_desktop_video_codec(self, peer_connection: Any, offer_sdp: str) -> None:
        cfg = _runtime.STATE.config or {}
        if (not self._sdp_has_media_section(offer_sdp, "video")
                or "remote_desktop" not in _runtime._get_available_video_outbound_sources(cfg=cfg)
                or _runtime._get_remote_desktop_capture_profile(cfg=cfg)["bitrate_kbps"] <= 1500):
            return
        try:
            from aiortc import RTCRtpSender
            from aiortc.sdp import SessionDescription

            codecs = list(RTCRtpSender.getCapabilities("video").codecs)
            h264 = [
                codec
                for codec in codecs
                if str(getattr(codec, "mimeType", "")).lower() == "video/h264"
            ]
            if h264:
                # aiortc chooses the common codec during setRemoteDescription,
                # before deferred capture starts on video_state(active). Preserve
                # offer order: the first bundled transport supplies ICE credentials.
                pending = list(peer_connection.getTransceivers())
                for media in SessionDescription.parse(offer_sdp).media:
                    if media.kind not in {"audio", "video"}:
                        continue
                    transceiver = next((t for t in pending if t.kind == media.kind
                                        and t.mid in (None, media.rtp.muxId)), None)
                    if transceiver is None:
                        transceiver = peer_connection.addTransceiver(media.kind, direction="recvonly")
                    else:
                        pending.remove(transceiver)
                    if media.kind == "video":
                        transceiver.setCodecPreferences(h264 + [codec for codec in codecs if codec not in h264])
        except Exception as exc:
            _runtime.LOGGER.debug("Could not prefer H.264 for remote desktop video: %s", exc)

    async def _enforce_remote_desktop_video_bitrate(self, track: Any, sender: Any) -> None:
        try:
            while getattr(track, "_autoyou_desktop_video_sender", None) is sender:
                encoder = getattr(sender, "_RTCRtpSender__encoder", None)
                if encoder is not None and hasattr(encoder, "target_bitrate"):
                    target_bps = (
                        _runtime._get_remote_desktop_capture_profile(cfg=(_runtime.STATE.config or {}))["bitrate_kbps"]
                        * 1000
                    )
                    state = (id(encoder), target_bps)
                    current = int(getattr(encoder, "target_bitrate", 0) or 0)
                    if getattr(track, "_autoyou_desktop_video_bitrate_state", None) != state or current > target_bps:
                        try:
                            encoder.target_bitrate = target_bps
                            track._autoyou_desktop_video_bitrate_state = state
                        except Exception as exc:
                            _runtime.LOGGER.debug("Could not apply remote desktop bitrate: %s", exc)
                await _runtime.asyncio.sleep(0.5)
        except _runtime.asyncio.CancelledError:
            return

    def _configure_remote_desktop_video_sender(
        self,
        session_id: str,
        transceiver: Any,
        track: Any,
    ) -> None:
        self._register_desktop_video_track_aliases(session_id, track)
        sender = transceiver.sender
        sender.replaceTrack(track)
        transceiver.direction = "sendrecv"
        if "remote_desktop" not in _runtime._get_available_video_outbound_sources(cfg=(_runtime.STATE.config or {})):
            return
        previous_task = getattr(track, "_autoyou_desktop_video_bitrate_task", None)
        if isinstance(previous_task, _runtime.asyncio.Task):
            previous_task.cancel()
        track._autoyou_desktop_video_sender = sender
        track._autoyou_desktop_video_bitrate_state = None
        try:
            track._autoyou_desktop_video_bitrate_task = _runtime.asyncio.get_running_loop().create_task(
                self._enforce_remote_desktop_video_bitrate(track, sender)
            )
        except RuntimeError:
            track._autoyou_desktop_video_bitrate_task = None

    def _register_audio_transceiver_aliases(self, primary_session_id: str, audio_transceiver: Any) -> None:
        primary_id = str(primary_session_id or "").strip()
        if not primary_id or audio_transceiver is None:
            return
        for alias_id in self._ordered_related_session_ids(primary_id):
            existing_transceiver = self.audio_transceivers.get(alias_id)
            if existing_transceiver is not None and existing_transceiver is not audio_transceiver:
                continue
            self.audio_transceivers[alias_id] = audio_transceiver

    def _audio_transceiver_for_session(self, session_id: str) -> Tuple[Optional[str], Optional[Any]]:
        for candidate_id in self._ordered_related_session_ids(session_id):
            transceiver = self.audio_transceivers.get(candidate_id)
            if transceiver is not None:
                return candidate_id, transceiver
        return None, None

    def _drop_local_capture_audio_tracks_for_ids(self, session_ids: Iterable[str]) -> None:
        local_tracks = getattr(_runtime.STATE, "local_audio_tracks", None)
        if not isinstance(local_tracks, dict):
            return
        for session_id in list(session_ids or []):
            tracks = local_tracks.pop(str(session_id or "").strip(), None)
            if tracks is None:
                continue
            if not isinstance(tracks, (list, tuple, set)):
                tracks = [tracks]
            for track in tracks:
                # Call both: disable() stops the PyAudio capture thread and stop()
                # flips readyState to "ended" so any in-flight recv() raises instead
                # of blocking a mixer that still references the old track.
                for method_name in ("disable", "stop"):
                    method = getattr(track, method_name, None)
                    if callable(method):
                        try:
                            method()
                        except Exception:
                            pass

    def _attach_background_audio_heartbeat_track(
        self,
        session_id: str,
        transceiver: Optional[Any] = None,
    ) -> bool:
        if _runtime.BackgroundAudioHeartbeatTrack is None:
            _runtime.LOGGER.warning("Cannot attach iOS background audio heartbeat for %s; audio track runtime unavailable", session_id)
            return False
        if transceiver is None:
            _, transceiver = self._audio_transceiver_for_session(session_id)
        if transceiver is None:
            return False
        sender = getattr(transceiver, "sender", None)
        if sender is None or not hasattr(sender, "replaceTrack"):
            return False
        try:
            track = _runtime.BackgroundAudioHeartbeatTrack()
            sender.replaceTrack(track)
            _runtime.LOGGER.info(
                "Attached iOS background audio heartbeat for %s "
                "(amplitude=%s frequency_hz=%s sample_rate=%s frame_size=%s)",
                _runtime.redact_identifier(session_id),
                getattr(track, "amplitude", "unknown"),
                getattr(track, "frequency_hz", "unknown"),
                getattr(track, "sample_rate", "unknown"),
                getattr(track, "frame_size", "unknown"),
            )
            return True
        except Exception as exc:
            _runtime.LOGGER.warning("Failed to attach iOS background audio heartbeat for %s: %s", session_id, exc)
            return False

    def _suppress_outbound_audio_for_background(
        self,
        session_id: str,
        *,
        silent_recording: bool,
        server_audio_direction: Optional[str] = None,
    ) -> None:
        alias_ids = self._ordered_related_session_ids(session_id)
        for alias_id in alias_ids:
            audio_manager = _runtime.STATE.audio_managers.get(alias_id)
            if audio_manager is None:
                continue
            stop_speaking = getattr(audio_manager, "stop_speaking", None)
            if callable(stop_speaking):
                try:
                    stop_speaking(source="background_audio_mode")
                except Exception:
                    pass
            try:
                _runtime._stop_audio_manager_playback(
                    audio_manager,
                    source="background_audio_mode",
                )
            except Exception:
                pass
        self._drop_local_capture_audio_tracks_for_ids(alias_ids)
        _, transceiver = self._audio_transceiver_for_session(session_id)
        if transceiver is None:
            return
        desired_direction = str(
            server_audio_direction
            or ("recvonly" if silent_recording else "inactive")
        ).strip().lower()
        if desired_direction not in {"sendonly", "recvonly", "sendrecv", "inactive"}:
            desired_direction = "recvonly" if silent_recording else "inactive"
        try:
            sender = getattr(transceiver, "sender", None)
            if sender is not None and hasattr(sender, "replaceTrack"):
                if desired_direction in {"sendonly", "sendrecv"} and not silent_recording:
                    attached = self._attach_background_audio_heartbeat_track(session_id, transceiver)
                else:
                    sender.replaceTrack(None)
        except Exception as exc:
            _runtime.LOGGER.debug("Failed to update outbound audio track for %s: %s", session_id, exc)
        self._set_transceiver_direction(
            transceiver,
            desired_direction,
            session_id=session_id,
            reason="background-audio",
        )

    # aiortc's direction bitmap: index & index is the intersection of two directions.
    _MEDIA_DIRECTIONS = ("inactive", "sendonly", "recvonly", "sendrecv")

    @classmethod
    def _intersect_media_directions(cls, first: str, second: str) -> str:
        try:
            return cls._MEDIA_DIRECTIONS[
                cls._MEDIA_DIRECTIONS.index(first) & cls._MEDIA_DIRECTIONS.index(second)
            ]
        except ValueError:
            return "inactive"

    def _set_transceiver_direction(
        self,
        transceiver: Any,
        direction: str,
        *,
        session_id: str = "",
        reason: str = "",
    ) -> None:
        """Change a transceiver's direction so it actually takes effect.

        aiortc latches ``sender._enabled`` / ``receiver._enabled`` inside
        ``_setCurrentDirection``, which only runs while applying a local or
        remote description. Assigning ``transceiver.direction`` after the
        one-shot offer/answer therefore updates the *desired* direction for a
        renegotiation that never happens, while the live sender keeps dropping
        every frame it pulls off the track and the live receiver keeps dropping
        inbound RTP. A session negotiated ``recvonly``/``sendonly``/``inactive``
        for Background Mode would then stay deaf or silent for the whole call
        that follows, no matter what track is attached.

        The current direction is clamped to what the remote peer offered, so
        this never sends or accepts media the peer did not agree to.
        """
        normalized = str(direction or "").strip().lower()
        if normalized not in self._MEDIA_DIRECTIONS:
            normalized = "inactive"
        try:
            transceiver.direction = normalized
        except Exception as exc:
            _runtime.LOGGER.debug(
                "Failed to set audio transceiver direction for %s (%s): %s",
                session_id,
                reason,
                exc,
            )
            return

        apply_current = getattr(transceiver, "_setCurrentDirection", None)
        if not callable(apply_current):
            return
        # Only repair an already-negotiated transceiver; before negotiation the
        # normal offer/answer flow assigns currentDirection itself.
        if getattr(transceiver, "currentDirection", None) is None:
            return
        offer_direction = str(getattr(transceiver, "_offerDirection", "") or "sendrecv").strip().lower()
        if offer_direction not in self._MEDIA_DIRECTIONS:
            offer_direction = "sendrecv"
        effective = self._intersect_media_directions(normalized, offer_direction)
        if effective == str(getattr(transceiver, "currentDirection", "") or ""):
            return
        try:
            apply_current(effective)
            _runtime.LOGGER.info(
                "Re-applied negotiated audio direction %s for %s (%s, requested=%s, peer_offer=%s)",
                effective,
                _runtime.redact_identifier(str(session_id)),
                reason or "direction-change",
                normalized,
                offer_direction,
            )
        except Exception as exc:
            _runtime.LOGGER.debug(
                "Failed to re-apply negotiated audio direction for %s (%s): %s",
                session_id,
                reason,
                exc,
            )

    def _clear_outbound_audio_track_for_idle(self, session_id: str) -> None:
        """Detach server audio without changing the negotiated audio m-line."""
        _, transceiver = self._audio_transceiver_for_session(session_id)
        if transceiver is None:
            return
        try:
            sender = getattr(transceiver, "sender", None)
            if sender is not None and hasattr(sender, "replaceTrack"):
                sender.replaceTrack(None)
            # The one-shot answer must remain usable if the client starts a
            # call later without renegotiation.
            self._set_transceiver_direction(
                transceiver,
                "sendrecv",
                session_id=session_id,
                reason="idle",
            )
        except Exception as exc:
            _runtime.LOGGER.debug("Failed to clear idle outbound audio for %s: %s", session_id, exc)

    def _restore_outbound_audio_for_call(self, session_id: str) -> None:
        _, transceiver = self._audio_transceiver_for_session(session_id)
        if transceiver is None:
            return
        alias_ids = self._ordered_related_session_ids(session_id)
        audio_manager = None
        for alias_id in alias_ids:
            audio_manager = _runtime.STATE.audio_managers.get(alias_id)
            if audio_manager is not None:
                break
        # Stop stale local capture tracks before rebuilding the outbound set so
        # detached PyAudio streams do not keep capturing in the background.
        self._drop_local_capture_audio_tracks_for_ids(alias_ids)
        current_cfg = _runtime.STATE.config or {}
        if not _runtime._get_video_call_audio_enabled(cfg=current_cfg):
            if audio_manager is not None:
                stop_speaking = getattr(audio_manager, "stop_speaking", None)
                if callable(stop_speaking):
                    try:
                        stop_speaking(source="video_call_audio_disabled")
                    except Exception:
                        pass
                try:
                    _runtime._stop_audio_manager_playback(
                        audio_manager,
                        source="video_call_audio_disabled",
                    )
                except Exception:
                    pass
            self._clear_outbound_audio_track_for_idle(session_id)
            return
        if audio_manager is not None and not _runtime._get_ai_audio_replies_enabled(cfg=current_cfg):
            stop_speaking = getattr(audio_manager, "stop_speaking", None)
            if callable(stop_speaking):
                try:
                    stop_speaking(source="ai_audio_replies_disabled")
                except Exception:
                    pass
        if audio_manager is not None and not _runtime._get_audio_playback_enabled(cfg=current_cfg):
            try:
                _runtime._stop_audio_manager_playback(
                    audio_manager,
                    source="audio_playback_disabled",
                )
            except Exception:
                pass
        try:
            tts_track, playback_track = _runtime._ensure_audio_manager_outbound_tracks(
                audio_manager=audio_manager,
                cfg=current_cfg,
            )
        except Exception as exc:
            _runtime.LOGGER.debug("Failed to prepare outbound audio tracks while restoring %s: %s", session_id, exc)
            tts_track, playback_track = None, None
        effective_audio_track = _runtime._create_configured_outbound_audio_track(
            cfg=current_cfg,
            session_id=str(session_id),
            tts_track=tts_track,
            playback_track=playback_track,
            include_loopback=self._screen_audio_active_for_session(session_id),
        )
        if effective_audio_track is None:
            self._clear_outbound_audio_track_for_idle(session_id)
            return
        try:
            transceiver.sender.replaceTrack(effective_audio_track)
        except Exception as exc:
            self._drop_local_capture_audio_tracks_for_ids(alias_ids)
            _runtime.LOGGER.warning("Failed to restore outbound call audio for %s: %s", session_id, exc)
            return
        # Restore full duplex for the call. A session negotiated for Background
        # Mode may have been narrowed at answer time, and aiortc only latches the
        # send/receive enable flags during negotiation - without this the call
        # would keep dropping outbound music/TTS frames and inbound microphone RTP.
        self._set_transceiver_direction(
            transceiver,
            "sendrecv",
            session_id=session_id,
            reason="call-restore",
        )

    def _desktop_video_track_for_session(self, session_id: str) -> Tuple[Optional[str], Optional[Any]]:
        for candidate_id in self._ordered_related_session_ids(session_id):
            track = self.desktop_video_tracks.get(candidate_id)
            if track is not None:
                return candidate_id, track
        return None, None

    def _screen_audio_active_for_session(self, session_id: str) -> bool:
        if "remote_desktop" not in _runtime._get_video_outbound_sources(cfg=(_runtime.STATE.config or {})):
            return False
        _, track = self._desktop_video_track_for_session(session_id)
        is_enabled = getattr(track, "is_enabled", None)
        return bool(is_enabled()) if callable(is_enabled) else False

    def latest_rewarded_ad_completion(
        self,
        session_id: str = "",
        owner_key: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Return the latest sanitized local rewarded-ad completion for a session.

        This is an in-memory edge proof for the active WebRTC browser path only.
        It never exposes provider transaction IDs or creates account value.
        """
        def _safe_float(value: Any, default: float = 0.0) -> float:
            try:
                parsed = float(value)
            except Exception:
                return default
            if not _runtime.math.isfinite(parsed):
                return default
            return parsed

        def _safe_int(value: Any, default: int = 0) -> int:
            try:
                return int(float(value))
            except Exception:
                return default

        requested_owner_key = str(owner_key or "").strip()
        for candidate_id in self._ordered_related_session_ids(str(session_id or "")):
            completion = self.rewarded_ad_completion_by_session.get(candidate_id)
            if not isinstance(completion, dict):
                continue
            if requested_owner_key:
                resolved_owner_key = self._resolve_webrtc_owner_key(candidate_id)
                if resolved_owner_key and resolved_owner_key != requested_owner_key:
                    continue
            return {
                "event": "rewarded_ad_completed",
                "session_id": str(completion.get("session_id") or candidate_id),
                "platform": str(completion.get("platform") or "unknown")[:32],
                "timestamp_ms": _safe_int(completion.get("timestamp_ms"), 0),
                "control_id": str(completion.get("control_id") or "")[:128],
                "source": str(completion.get("source") or "ads_watching_agent")[:64],
                "watched_seconds": max(0.0, min(120.0, _safe_float(completion.get("watched_seconds"), 0.0))),
            }
        return None

    def _purge_audio_manager_aliases(self, audio_manager: Any, *, close_manager: bool = False) -> Set[str]:
        alias_ids: Set[str] = set()
        if audio_manager is None:
            return alias_ids

        for sid, manager in list(getattr(_runtime.STATE, "audio_managers", {}).items()):
            if manager is not audio_manager:
                continue
            alias_id = str(sid)
            alias_ids.add(alias_id)
            _runtime.STATE.audio_managers.pop(sid, None)
            self.voice_call_status_by_session.pop(alias_id, None)
            self.voice_call_playback_by_session.pop(alias_id, None)
            self.voice_call_client_active_by_session.pop(alias_id, None)
            self.wuift_hold_by_session.pop(alias_id, None)
            self.rewarded_ad_completion_by_session.pop(alias_id, None)

        if close_manager and hasattr(audio_manager, "close"):
            try:
                audio_manager.close()
            except Exception as exc:
                _runtime.LOGGER.warning("Error closing audio manager aliases %s: %s", sorted(alias_ids), exc)

        return alias_ids

    def _audio_manager_alias_ids(self, audio_manager: Any) -> Set[str]:
        alias_ids: Set[str] = set()
        if audio_manager is None:
            return alias_ids

        for sid, manager in list(getattr(_runtime.STATE, "audio_managers", {}).items()):
            if manager is audio_manager:
                alias_ids.add(str(sid))

        return alias_ids

    def _set_voice_call_client_active(self, session_id: str, active: bool) -> Set[str]:
        normalized_session_id = str(session_id or "").strip()
        alias_ids: Set[str] = {normalized_session_id} if normalized_session_id else set()
        audio_manager = _runtime.STATE.audio_managers.get(normalized_session_id)
        if audio_manager is not None:
            alias_ids.update(self._audio_manager_alias_ids(audio_manager))
        for alias_id in alias_ids:
            self.voice_call_client_active_by_session[alias_id] = bool(active)
        return alias_ids

    def _voice_call_client_active_for_session(self, session_id: str) -> bool:
        return any(
            bool(self.voice_call_client_active_by_session.get(candidate_id, False))
            for candidate_id in self._ordered_related_session_ids(session_id)
        )

    def _screen_session_for_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        for candidate_id in self._ordered_related_session_ids(session_id):
            session = self.screen_sessions.get(candidate_id)
            if session is not None:
                return session
        return None

    def _set_screen_session(self, session_id: str, mode: str) -> None:
        current = self._screen_session_for_session(session_id)
        if mode == "ended":
            if current is not None:
                current["mode"] = "ended"
                current["muted"] = True
                self.screen_listen_mixer.forget(str(current["id"]))
            return
        if current is not None and current["id"] == session_id and current["mode"] == mode:
            return
        related = self._ordered_related_session_ids(session_id)
        for candidate_id in related:
            previous = self.screen_sessions.pop(candidate_id, None)
            if previous is not None:
                self.screen_listen_mixer.forget(str(previous["id"]))
        if mode not in {"watch", "interactive"}:
            return
        name = self.client_display_name_snapshot(session_id).get("client_display_name") or "Connected device"
        self.screen_sessions[session_id] = {
            "id": session_id, "name": str(name)[:64], "mode": mode,
            "muted": True, "layout": "off", "last_input": "", "last_input_at": 0.0,
        }

    def screen_listen_snapshot(self) -> Dict[str, Any]:
        participants = [dict(session) for session in self.screen_sessions.values()
                        if session["mode"] in {"watch", "interactive"} and
                        self._voice_call_client_active_for_session(str(session["id"]))]
        return {
            **self.screen_listen_mixer.snapshot(),
            "participants": participants,
            "inputs": list(self.screen_inputs)[-50:],
        }

    def configure_screen_listen(self, mode: str, selected: Iterable[str] = ()) -> Dict[str, Any]:
        allowed = {str(session["id"]) for session in self.screen_sessions.values()
                   if session["mode"] == "interactive" and
                   self._voice_call_client_active_for_session(str(session["id"]))}
        choices = {str(value) for value in selected}
        if mode == "selected" and not choices <= allowed:
            raise ValueError("Choose a connected screen participant")
        self.screen_listen_mixer.configure(mode, choices if mode == "selected" else ())
        return self.screen_listen_snapshot()

    def _should_accept_voice_call_audio(self, session_id: str) -> bool:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return True
        return self.voice_call_client_active_by_session.get(normalized_session_id, True)

    @staticmethod
    def _coerce_control_bool(raw_value: Any) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            return raw_value.strip().lower() in {"1", "true", "yes", "on", "active", "enabled"}
        return bool(raw_value)

    @staticmethod
    def _extract_audio_media_direction(sdp: Any) -> str:
        current_media = ""
        for raw_line in str(sdp or "").replace("\r\n", "\n").split("\n"):
            line = raw_line.strip().lower()
            if not line:
                continue
            if line.startswith("m="):
                current_media = line[2:].split(" ", 1)[0]
                continue
            if current_media == "audio" and line in {"a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive"}:
                return line[2:]
        return ""

    @staticmethod
    def _sdp_has_media_section(sdp: Any, media_kind: str) -> bool:
        prefix = f"m={str(media_kind or '').strip().lower()} "
        if prefix == "m= ":
            return False
        return any(
            raw_line.strip().lower().startswith(prefix)
            for raw_line in str(sdp or "").replace("\r\n", "\n").split("\n")
        )

    def _background_audio_mode_allowed(
        self,
        *,
        silent_recording: bool,
        cfg: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if silent_recording:
            return _runtime._get_silent_recording_enabled(cfg=cfg)
        return _runtime._get_background_mode_enabled(cfg=cfg)

    def _background_audio_offer_state(
        self,
        offer_data: Mapping[str, Any],
        sdp: Any = "",
        *,
        cfg: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = offer_data if isinstance(offer_data, _runtime.Mapping) else {}
        hint_raw = payload.get("background_audio") or payload.get("background_audio_state")
        hint = hint_raw if isinstance(hint_raw, _runtime.Mapping) else {}
        platform = str(hint.get("platform") or payload.get("platform") or "unknown").strip() or "unknown"
        client_direction = str(
            hint.get("client_audio_direction")
            or payload.get("client_audio_direction")
            or self._extract_audio_media_direction(sdp)
            or ""
        ).strip().lower()
        silent_recording = self._coerce_control_bool(
            hint.get(
                "silent_recording",
                hint.get("recording", payload.get("silent_recording", payload.get("recording"))),
            )
        )
        muted_raw = hint.get("muted", payload.get("muted"))
        muted_provided = "muted" in hint or "muted" in payload
        muted = self._coerce_control_bool(muted_raw) if muted_provided else not silent_recording
        active = self._coerce_control_bool(
            hint.get(
                "active",
                payload.get("background_audio_active", payload.get("background_mode_active", False)),
            )
        )
        if active and not self._background_audio_mode_allowed(
            silent_recording=silent_recording,
            cfg=cfg,
        ):
            active = False
            silent_recording = False
            muted = True
            server_direction = "inactive"
        if active and silent_recording:
            client_direction = "sendonly"
            muted = False
            offer_direction = self._extract_audio_media_direction(sdp)
            server_direction = "sendrecv" if offer_direction == "sendrecv" else "recvonly"
        elif active:
            silent_recording = False
            muted = True
            offer_direction = self._extract_audio_media_direction(sdp)
            if platform.lower() == "ios" and client_direction in {"sendrecv", "recvonly"}:
                client_direction = "recvonly"
            # Keep a sendrecv answer whenever the offer reserved that m-line
            # for a later call. Track attachment, not post-answer direction
            # changes, controls whether background audio is actually sent.
            if offer_direction == "sendrecv":
                server_direction = "sendrecv"
            elif offer_direction == "sendonly":
                server_direction = "recvonly"
            elif offer_direction == "recvonly":
                server_direction = "sendonly"
            else:
                client_direction = "inactive"
                server_direction = "inactive"
        else:
            muted = True
            server_direction = "inactive"
        return {
            "active": bool(active),
            "silent_recording": bool(active and silent_recording),
            "muted": bool(muted),
            "platform": platform,
            "client_audio_direction": client_direction,
            "server_audio_direction": server_direction,
        }

    def _background_audio_state_for_session(self, session_id: str) -> Dict[str, Any]:
        for candidate_id in self._ordered_related_session_ids(session_id):
            state = self.background_audio_state_by_session.get(candidate_id)
            if isinstance(state, dict):
                return state
        return {}

    def _set_background_audio_state(
        self,
        session_id: str,
        *,
        active: bool,
        silent_recording: bool,
        muted: bool,
        platform: str,
        timestamp_ms: Any = None,
    ) -> Set[str]:
        normalized_session_id = str(session_id or "").strip()
        alias_ids = set(self._ordered_related_session_ids(normalized_session_id))
        if normalized_session_id:
            alias_ids.add(normalized_session_id)
        if not alias_ids:
            return set()

        state = {
            "active": bool(active),
            "silent_recording": bool(active and silent_recording),
            "muted": bool(muted),
            "platform": str(platform or "unknown").strip() or "unknown",
            "timestamp_ms": timestamp_ms,
            "updated_at": _runtime.time.time(),
        }
        for alias_id in alias_ids:
            self.background_audio_state_by_session[alias_id] = state

        if not state["active"] or not state["silent_recording"]:
            self._close_silent_recorders_for_ids(alias_ids)
        return alias_ids

    def _close_silent_recorders_for_ids(self, session_ids: Iterable[str]) -> None:
        closed_recorders: List[Any] = []
        for session_id in list(session_ids or []):
            normalized_session_id = str(session_id or "").strip()
            if not normalized_session_id:
                continue
            recorder = self.silent_recorders.pop(normalized_session_id, None)
            if recorder is None or any(recorder is current for current in closed_recorders):
                continue
            closed_recorders.append(recorder)
            try:
                recorder.close()
            except Exception as exc:
                _runtime.LOGGER.warning("Failed to close silent recording writer for %s: %s", normalized_session_id, exc)

    def _get_or_create_silent_recorder(self, session_id: str) -> Optional[Any]:
        if _runtime.StreamingWavBatchRecorder is None:
            return None
        alias_ids = self._ordered_related_session_ids(session_id)
        for alias_id in alias_ids:
            recorder = self.silent_recorders.get(alias_id)
            if recorder is not None:
                for register_alias in alias_ids:
                    self.silent_recorders[register_alias] = recorder
                return recorder

        recorder_session_id = str(alias_ids[0] if alias_ids else session_id or "session").strip() or "session"
        recorder = _runtime.StreamingWavBatchRecorder(
            session_id=recorder_session_id,
            output_dir=_runtime._resolve_silent_recording_dir(cfg=(_runtime.STATE.config or {})),
            max_batch_seconds=_runtime._get_silent_recording_batch_seconds(cfg=(_runtime.STATE.config or {})),
        )
        for alias_id in alias_ids:
            self.silent_recorders[alias_id] = recorder
        return recorder

    def _write_silent_recording_chunk(self, session_id: str, chunk: bytes) -> bool:
        state = self._background_audio_state_for_session(session_id)
        if not state.get("active"):
            return False
        if not state.get("silent_recording"):
            return False
        if not _runtime._get_silent_recording_enabled(cfg=(_runtime.STATE.config or {})):
            self._close_silent_recorders_for_ids(self._ordered_related_session_ids(session_id))
            return True
        recorder = self._get_or_create_silent_recorder(session_id)
        if recorder is None:
            return True
        try:
            recorder.write(chunk)
        except Exception as exc:
            _runtime.LOGGER.warning("Failed to write silent recording chunk for %s: %s", session_id, exc)
        return True

    def _background_audio_consumer_enabled(self, *, cfg: Optional[Dict[str, Any]] = None) -> bool:
        effective_cfg = cfg if cfg is not None else (_runtime.STATE.config or {})
        return bool(
            _runtime.AudioTrackSink is not None
            and _runtime._get_video_call_audio_enabled(cfg=effective_cfg)
            and (
                _runtime._get_background_mode_enabled(cfg=effective_cfg)
                or _runtime._get_silent_recording_enabled(cfg=effective_cfg)
            )
        )

    def _start_inbound_audio_track_sink(
        self,
        track: Any,
        *,
        session_id: str,
        audio_manager: Any,
        audio_call_enabled: bool,
        cfg_snapshot: Dict[str, Any],
    ) -> bool:
        if _runtime.AudioTrackSink is None or not audio_call_enabled:
            return False
        # Screen Listen needs the inbound track even when AI voice agents are disabled.

        primary_session_id = str(session_id)

        sink = _runtime.AudioTrackSink(
            track,
            lambda chunk, sid=primary_session_id, mgr=audio_manager: self._handle_inbound_voice_audio_chunk(
                sid,
                mgr,
                chunk,
            ),
        )
        for alias_id in self._ordered_related_session_ids(primary_session_id):
            existing_sink = self.audio_sinks.get(alias_id)
            if existing_sink is not None and existing_sink is not sink:
                continue
            self.audio_sinks[alias_id] = sink
        _runtime.track_background_task(sink.start())
        return True

    def _session_has_webrtc_state(self, session_id: str) -> bool:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return False

        cleanup_task = self.cleanup_tasks.get(normalized_session_id)
        if cleanup_task is not None and not cleanup_task.done():
            return True

        state_maps = (
            self.session_peers,
            self.audio_sinks,
            self.video_sinks,
            self.desktop_video_tracks,
            self.audio_transceivers,
            self.pending_candidates,
            self.outgoing_trickle_candidates,
            self.seen_remote_ice_candidates,
            self.datachannel_managers,
            self.session_establishment_tasks,
            self.session_disconnect_grace_tasks,
            self.voice_command_queues,
            self.voice_command_workers,
            self.session_message_tasks,
            self.session_reconnect_survivable_tasks,
            self.http_proxy_request_tasks,
            self.pending_voice_chat_messages,
            self.background_audio_state_by_session,
            self.screen_sessions,
            self.silent_recorders,
        )
        if any(normalized_session_id in mapping for mapping in state_maps):
            return True
        if normalized_session_id in getattr(_runtime.STATE, "audio_managers", {}):
            return True
        return False

    def _replacement_session_ids(
        self,
        session_id: str,
        client_session_id_hint: Optional[str],
        *,
        stable_client_id_hint: Optional[str] = None,
    ) -> List[str]:
        """Return existing raw/client ids that should be torn down before a new WebRTC offer."""
        ordered_ids: List[str] = []

        def add(value: Any) -> None:
            normalized = str(value or "").strip()
            if normalized and normalized not in ordered_ids:
                ordered_ids.append(normalized)

        primary_id = str(session_id or "").strip()
        hinted_id = str(client_session_id_hint or "").strip()
        stable_client_id = str(stable_client_id_hint or "").strip()

        for base_id in (primary_id, hinted_id):
            if not base_id:
                continue
            for related_id in self._ordered_related_session_ids(base_id):
                add(related_id)

        for raw_id, client_id in list(self._voice_dc_session_id.items()):
            raw_id = str(raw_id or "").strip()
            client_id = str(client_id or "").strip()
            if hinted_id and client_id == hinted_id:
                add(client_id)
                add(raw_id)
            if primary_id and (raw_id == primary_id or client_id == primary_id):
                add(raw_id)
                add(client_id)

        if stable_client_id:
            for cached_session_id, session_payload in list(getattr(_runtime.STATE, "session_cache", {}).items()):
                if not isinstance(session_payload, dict):
                    continue
                cached_stable_client_id = str(session_payload.get("stable_client_id") or "").strip()
                if cached_stable_client_id == stable_client_id:
                    add(cached_session_id)

        return [
            candidate_id
            for candidate_id in ordered_ids
            if self._session_has_webrtc_state(candidate_id)
        ]

    async def _cleanup_replacement_sessions(
        self,
        session_id: str,
        *,
        client_session_id_hint: Optional[str] = None,
        stable_client_id_hint: Optional[str] = None,
        label: str,
        clear_client_status_hint: bool = False,
    ) -> bool:
        replacement_session_ids = self._replacement_session_ids(
            str(session_id),
            client_session_id_hint,
            stable_client_id_hint=stable_client_id_hint,
        )
        replacing_session = bool(replacement_session_ids)
        redacted_session_id = _runtime.redact_identifier(session_id)
        for replacement_session_id in replacement_session_ids:
            if not self._session_has_webrtc_state(replacement_session_id):
                continue
            existing_pc = self.session_peers.get(replacement_session_id)
            _runtime.LOGGER.info(
                "Replacing existing %s WebRTC session for %s via %s",
                label,
                redacted_session_id,
                _runtime.redact_identifier(replacement_session_id),
            )
            try:
                await _runtime.asyncio.wait_for(
                    self.async_cleanup_session(replacement_session_id, expected_pc=existing_pc),
                    timeout=_runtime.AUTOPAIR_REPLACEMENT_CLEANUP_TIMEOUT_SECONDS,
                )
            except _runtime.asyncio.TimeoutError:
                _runtime.LOGGER.warning(
                    "Timed out waiting %.1fs for stale %s cleanup for %s via %s; proceeding with replacement",
                    _runtime.AUTOPAIR_REPLACEMENT_CLEANUP_TIMEOUT_SECONDS,
                    label,
                    redacted_session_id,
                    _runtime.redact_identifier(replacement_session_id),
                )

        normalized_client_session_id = str(client_session_id_hint or "").strip()
        if clear_client_status_hint and normalized_client_session_id and replacing_session:
            self.voice_call_status_by_session.pop(normalized_client_session_id, None)
        return replacing_session

    def _handle_inbound_voice_audio_chunk(self, session_id: str, audio_manager: Any, chunk: bytes) -> None:
        screen_session = self._screen_session_for_session(session_id)
        if screen_session is not None:
            if screen_session["mode"] == "interactive" and not screen_session["muted"]:
                self.screen_listen_mixer.feed(str(screen_session["id"]), chunk)
            return  # Screen sessions never enter STT, AI agents, or call listeners.
        background_state = self._background_audio_state_for_session(session_id)
        if background_state.get("active"):
            if background_state.get("silent_recording"):
                self._write_silent_recording_chunk(session_id, chunk)
            return
        if audio_manager is None:
            return
        if not _runtime._get_video_call_agent_processing_enabled(cfg=(_runtime.STATE.config or {})):
            return
        if not self._should_accept_voice_call_audio(session_id):
            return
        self._feed_call_listener(session_id, chunk)
        audio_manager.process_audio_chunk(chunk)

    def _feed_call_listener(self, session_id: str, chunk: bytes) -> None:
        """Hand one frame to the Computer's listener, if it is in this call.

        Off the callback, always. Transcription runs at utterance boundaries and
        blocks; doing that here would stall the audio this same callback is
        delivering for playback. The coordinator takes its own lock, so frames
        arriving out of order across workers cannot interleave a flush.

        Nothing here may raise: this sits on the call's audio path.
        """
        try:
            grant = self.room_bridge_grants.grant_for_transport(session_id)
            if grant is None or not self.call_listeners.is_listening(grant.room_id):
                return
            loop = _runtime.asyncio.get_running_loop()
            loop.run_in_executor(
                None,
                lambda: self.call_listeners.on_audio(
                    grant.room_id,
                    device_id=str(session_id),
                    display_name=_runtime.get_configured_server_name(),
                    pcm=chunk,
                ),
            )
        except Exception as exc:  # pragma: no cover - defensive
            _runtime.LOGGER.debug("Call listener did not take a frame: %s", exc)

    def _serialize_ice_candidate_for_signal(self, candidate: Any) -> Optional[Dict[str, Any]]:
        """Serialize an aiortc ICE candidate into signaling payload format."""
        if candidate is None:
            return None
        try:
            cand_sdp = None
            if _runtime.candidate_to_sdp is not None:
                cand_sdp = _runtime.candidate_to_sdp(candidate)
            elif hasattr(candidate, "to_sdp"):
                cand_sdp = candidate.to_sdp()
            else:
                cand_sdp = str(candidate)
            if cand_sdp and not str(cand_sdp).startswith("candidate:"):
                cand_sdp = f"candidate:{cand_sdp}"
            return {
                "type": "candidate",
                "candidate": cand_sdp,
                "sdpMid": getattr(candidate, "sdpMid", None),
                "sdpMLineIndex": getattr(candidate, "sdpMLineIndex", None),
            }
        except Exception as e:
            _runtime.LOGGER.warning(f"Failed to serialize ICE candidate: {e}")
            return None

    def queue_outgoing_trickle_candidate(self, session_id: str, candidate: Any) -> None:
        """Queue a server ICE candidate for polling-based trickle retrieval."""
        payload = self._serialize_ice_candidate_for_signal(candidate)
        if not payload:
            return
        self.outgoing_trickle_candidates.setdefault(session_id, []).append(payload)

    def pop_outgoing_trickle_candidates(self, session_id: str) -> list:
        """Pop and clear queued server ICE candidates for a session."""
        queued = self.outgoing_trickle_candidates.get(session_id, [])
        if queued:
            self.outgoing_trickle_candidates[session_id] = []
        return list(queued)

    def _remote_candidate_fingerprint(self, candidate: Any) -> Optional[str]:
        """Build a stable fingerprint for remote ICE candidates."""
        cand_line: Optional[str] = None
        cand_mid: Any = None
        cand_mline: Any = None

        if isinstance(candidate, dict):
            cand_value = candidate.get("candidate")
            cand_mid = candidate.get("sdpMid")
            cand_mline = candidate.get("sdpMLineIndex")
            if isinstance(cand_value, dict):
                cand_mid = cand_value.get("sdpMid", cand_mid)
                cand_mline = cand_value.get("sdpMLineIndex", cand_mline)
                cand_value = cand_value.get("candidate") or cand_value.get("sdp")
            if cand_value:
                cand_line = str(cand_value)
        elif candidate is not None:
            try:
                if _runtime.candidate_to_sdp is not None:
                    cand_line = _runtime.candidate_to_sdp(candidate)
            except Exception:
                cand_line = None
            cand_mid = getattr(candidate, "sdpMid", None)
            cand_mline = getattr(candidate, "sdpMLineIndex", None)
            if not cand_line:
                cand_line = str(candidate)

        if not cand_line:
            return None
        if not cand_line.startswith("candidate:"):
            cand_line = f"candidate:{cand_line}"
        return f"{cand_line}|{cand_mid}|{cand_mline}"

    def _register_remote_ice_candidate(self, session_id: str, candidate: Any) -> bool:
        """Return True when the remote ICE candidate was already seen for the session."""
        fingerprint = self._remote_candidate_fingerprint(candidate)
        if not fingerprint:
            return False
        seen = self.seen_remote_ice_candidates.setdefault(session_id, set())
        if fingerprint in seen:
            return True
        seen.add(fingerprint)
        return False

    def _normalize_remote_ice_candidate(self, candidate: Any) -> Any:
        """Convert signaling payloads into aiortc ICE candidates when possible."""
        if not isinstance(candidate, dict) or _runtime.candidate_from_sdp is None:
            return candidate

        cand_str = candidate.get("candidate")
        cand_mid = candidate.get("sdpMid")
        cand_mline = candidate.get("sdpMLineIndex")

        # Accept legacy nested candidate payload shape:
        # {"candidate":{"candidate":"...","sdpMid":"0","sdpMLineIndex":0}, ...}
        if isinstance(cand_str, dict):
            cand_mid = cand_str.get("sdpMid", cand_mid)
            cand_mline = cand_str.get("sdpMLineIndex", cand_mline)
            cand_str = cand_str.get("candidate") or cand_str.get("sdp")

        if not cand_str:
            return candidate

        cand_line = cand_str if str(cand_str).startswith("candidate:") else f"candidate:{cand_str}"
        candidate_obj = _runtime.candidate_from_sdp(cand_line)
        candidate_obj.sdpMid = cand_mid
        candidate_obj.sdpMLineIndex = cand_mline
        return candidate_obj

    @staticmethod
    def _resolve_chat_identity(session_id: Optional[str]):
        return _runtime.resolve_webrtc_chat_identity(session_id)

    def _voice_training_conversation_provider(self, session_id: Optional[str]):
        """Resolve which conversation a saved voice-training clip was spoken in."""

        def _provider() -> Dict[str, str]:
            identity = _runtime._resolve_conversation_identity(self._resolve_chat_identity(session_id))
            return {
                "user_id": str(getattr(identity, "canonical_user_id", "") or ""),
                "session_id": str(getattr(identity, "canonical_session_id", "") or ""),
            }

        return _provider

    @staticmethod
    def _relay_route(message: 'DataChannelMessage') -> List[Dict[str, Any]]:
        """Read a bounded, contiguous client relay route from a chat envelope."""
        payload = getattr(message, "payload", None)
        if not isinstance(payload, dict):
            return []
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return []
        raw_path = metadata.get("relay_path")
        if not isinstance(raw_path, list) or len(raw_path) != 1:
            return []
        route: List[Dict[str, Any]] = []
        for expected_hop, raw in enumerate(raw_path, start=1):
            if not isinstance(raw, dict):
                return []
            peer_id = str(raw.get("peer_id") or "").strip()
            name = str(raw.get("name") or "").strip()
            platform = str(raw.get("platform") or "").strip()
            try:
                hop = int(raw.get("hop") or 0)
            except (TypeError, ValueError):
                return []
            if (
                not peer_id
                or len(peer_id) > 256
                or len(name) > 128
                or len(platform) > 32
                or hop != expected_hop
            ):
                return []
            route.append({"peer_id": peer_id, "hop": hop})
        return route

    @classmethod
    def _relay_origin_sender_id(cls, message: 'DataChannelMessage') -> str:
        route = cls._relay_route(message)
        return str(route[-1]["peer_id"]) if route else ""

    @classmethod
    def _relay_identity_key(
        cls,
        message: 'DataChannelMessage',
        session_id: Optional[str],
    ) -> str:
        """Scope a descendant route to the authenticated root WebRTC session."""
        route = cls._relay_route(message)
        root_session = str(session_id or "").strip()
        if not route or not root_session:
            return ""
        canonical = json.dumps(route, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(
            f"{root_session}\0{canonical}".encode("utf-8")
        ).hexdigest()

    def _resolve_chat_identity_for_message(self, message: 'DataChannelMessage', session_id: Optional[str]):
        """Identity for an inbound chat turn, honouring a relayed guest.

        Deliberately uses `resolve_transport_identity` rather than
        `bind_transport_chat_owner`: binding would alias the *transport* session
        to the guest's owner, and that session belongs to the relaying client,
        whose own turns must keep landing in its own conversation.
        """
        relay_identity = self._relay_identity_key(message, session_id)
        if not relay_identity:
            return self._resolve_chat_identity(session_id)
        return _runtime.get_session_execution_manager().resolve_transport_identity(
            "peer",
            relay_identity,
        )

    def _relay_presentation(self, message: 'DataChannelMessage', session_id: Optional[str]) -> Dict[str, Any]:
        """How Chat & History shows a relayed guest: who, on what, carried by whom.

        The relaying device attests all of it, so it is presentation only; the
        guest's owner comes from `_resolve_chat_identity_for_message`. The
        guest's name follows the owner's "store client names in chat history"
        choice, the same rule a directly paired device's name follows.
        """
        route = self._relay_route(message)
        root_session = str(session_id or "").strip()
        if not route or not root_session:
            return {}
        entry = message.payload["metadata"]["relay_path"][-1]
        presentation: Dict[str, Any] = {"hop": route[-1]["hop"], "platform": entry.get("platform")}
        try:
            relay_owner = self._resolve_chat_identity(root_session)
        except Exception:
            relay_owner = None
        presentation["via_user_id"] = str(getattr(relay_owner, "canonical_user_id", "") or "")
        if _runtime._client_name_history_enabled():
            presentation["name"] = entry.get("name")
            if relay_owner is not None:
                presentation["via_name"] = self.client_name_history_metadata(relay_owner).get("client_display_name")
        return sanitize_peer_relay(presentation)

    @staticmethod
    def _canonical_chat_user_id(session_id: Optional[str]) -> str:
        """Return canonical user_id for WebRTC chat/voice from same phone session."""
        return _runtime.WebRTCManager._resolve_chat_identity(session_id).canonical_user_id

    @staticmethod
    def _canonical_chat_session_id(session_id: Optional[str]) -> str:
        """Return canonical session_id for WebRTC chat/voice from same phone session."""
        return _runtime.WebRTCManager._resolve_chat_identity(session_id).canonical_session_id

    def _resolve_voice_chat_session_id(self, session_id: Optional[str]) -> str:
        """Return the client-facing datachannel session id for voice UI/chat continuity."""
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return ""
        return str(self._voice_dc_session_id.get(normalized_session_id) or normalized_session_id)

    def _datachannel_manager_for_session(
        self,
        session_id: Optional[str],
        *,
        require_send_message: bool = False,
    ) -> Optional[Any]:
        for candidate_id in self._ordered_related_session_ids(str(session_id or "")):
            datachannel_manager = self.datachannel_managers.get(candidate_id)
            if not datachannel_manager:
                continue
            if require_send_message and not hasattr(datachannel_manager, "send_message"):
                continue
            return datachannel_manager
        return None

    @staticmethod
    def _datachannel_manager_is_live(datachannel_manager: Optional[Any]) -> bool:
        """Keep live session IDs exclusive while allowing closed channels to be replaced."""
        if datachannel_manager is None:
            return False
        if getattr(datachannel_manager, "connection_active", True) is False:
            return False
        channel = getattr(datachannel_manager, "datachannel", None)
        ready_state = str(getattr(channel, "readyState", "") or "").strip().lower()
        return ready_state not in {"closing", "closed"}

    def _retire_inactive_datachannel_manager(self, datachannel_manager: Any) -> bool:
        """Remove a closed manager and cancel proxy work that still targets it."""
        if self._datachannel_manager_is_live(datachannel_manager):
            return False

        stale_aliases = [
            str(session_id)
            for session_id, manager in list(self.datachannel_managers.items())
            if manager is datachannel_manager
        ]
        try:
            datachannel_manager.disconnect()
        except Exception:
            pass

        for stale_session_id in stale_aliases:
            if self.datachannel_managers.get(stale_session_id) is datachannel_manager:
                self.datachannel_managers.pop(stale_session_id, None)
            self._voice_dc_session_id.pop(stale_session_id, None)
            stale_http_tasks = self.http_proxy_request_tasks.pop(stale_session_id, {})
            for stale_task in stale_http_tasks.values():
                if not stale_task.done():
                    stale_task.cancel()
        return True

    @staticmethod
    def _is_lingering_webrtc_teardown_task(task: asyncio.Task) -> bool:
        try:
            coro = task.get_coro()
        except Exception:
            return False

        qualname = str(getattr(coro, "__qualname__", "") or "")
        if not qualname and hasattr(coro, "cr_code"):
            qualname = str(getattr(coro.cr_code, "co_qualname", "") or getattr(coro.cr_code, "co_name", "") or "")

        if "TurnClientMixin.send_data" in qualname:
            return True
        if "_data_channel_flush" in qualname:
            return True
        return False

    async def _cancel_lingering_webrtc_teardown_tasks(self, *, timeout_seconds: float = 0.25) -> int:
        try:
            current_task = _runtime.asyncio.current_task()
        except RuntimeError:
            return 0

        await _runtime.asyncio.sleep(0)

        lingering_tasks = [
            task
            for task in _runtime.asyncio.all_tasks()
            if task is not current_task and not task.done() and self._is_lingering_webrtc_teardown_task(task)
        ]
        if not lingering_tasks:
            return 0

        for task in lingering_tasks:
            task.cancel()

        for task in lingering_tasks:
            with _runtime.suppress(_runtime.asyncio.CancelledError, Exception):
                await _runtime.asyncio.wait_for(task, timeout=timeout_seconds)

        _runtime.LOGGER.debug("Cancelled %d lingering aiortc/aioice teardown task(s)", len(lingering_tasks))
        return len(lingering_tasks)

    def _build_webrtc_reply_target(self, session_id: Optional[str], identity: Any) -> Optional[Dict[str, Any]]:
      resolved_session_id = str(session_id or getattr(identity, "raw_session_id", "") or "").strip()
      owner_key = str(getattr(identity, "owner_key", "") or "").strip()

      reply_target: Dict[str, Any] = {"transport": "webrtc"}
      if owner_key:
        reply_target["owner_key"] = owner_key
      if resolved_session_id:
        reply_target["session_id"] = resolved_session_id
      if len(reply_target) == 1:
        return None
      return reply_target

    def _preferred_datachannel_session_id(
      self,
      fallback_session_id: Any,
      datachannel_manager: Any,
      snapshot: Optional[List[Tuple[str, Any]]] = None,
    ) -> str:
      fallback = str(fallback_session_id or "").strip()
      if not datachannel_manager:
        return fallback

      manager_entries = snapshot if snapshot is not None else list(self.datachannel_managers.items())
      aliases = [
        str(alias_id or "").strip()
        for alias_id, alias_manager in manager_entries
        if alias_manager is datachannel_manager and str(alias_id or "").strip()
      ]

      preferred_session_id = str(getattr(datachannel_manager, "session_id", "") or "").strip()
      if preferred_session_id in aliases:
        return preferred_session_id

      for alias_id in aliases:
        mapped_client_id = str(self._voice_dc_session_id.get(alias_id) or "").strip()
        if mapped_client_id in aliases:
          return mapped_client_id

      return fallback or (aliases[0] if aliases else "")

    def _resolve_webrtc_reply_target_session_id(self, reply_target: Dict[str, Any]) -> Optional[str]:
      target_session_id = str(reply_target.get("session_id") or "").strip()
      if target_session_id:
        datachannel_manager = self._datachannel_manager_for_session(
          target_session_id,
          require_send_message=True,
        )
        if datachannel_manager:
          return self._preferred_datachannel_session_id(target_session_id, datachannel_manager)

      target_owner_key = str(reply_target.get("owner_key") or "").strip()
      if not target_owner_key:
        return None

      for candidate_session_id, datachannel_manager in list(self.datachannel_managers.items()):
        if not datachannel_manager or not hasattr(datachannel_manager, "send_message"):
          continue
        try:
          identity = self._resolve_chat_identity(str(candidate_session_id))
        except Exception:
          continue
        if str(getattr(identity, "owner_key", "") or "").strip() == target_owner_key:
          return self._preferred_datachannel_session_id(candidate_session_id, datachannel_manager)
      return None

    def _datachannel_connection_key(self, session_id: Any) -> str:
      normalized_session_id = str(session_id or "").strip()
      if not normalized_session_id:
        return ""
      try:
        identity = self._resolve_chat_identity(normalized_session_id)
      except Exception:
        identity = None
      for field in ("owner_key", "canonical_session_id"):
        value = str(getattr(identity, field, "") or "").strip()
        if value:
          return f"{field}:{value}"
      return f"session:{normalized_session_id}"

    def _unique_datachannel_manager_entries(
      self,
      *,
      require_send_message: bool = False,
    ) -> List[Tuple[str, Any]]:
      entries: List[Tuple[str, Any]] = []
      seen_managers: List[Any] = []
      seen_connection_keys: Set[str] = set()
      snapshot = list(self.datachannel_managers.items())
      for candidate_session_id, datachannel_manager in snapshot:
        if not candidate_session_id or not datachannel_manager:
          continue
        if require_send_message and not hasattr(datachannel_manager, "send_message"):
          continue
        if any(datachannel_manager is seen_manager for seen_manager in seen_managers):
          continue
        preferred_session_id = self._preferred_datachannel_session_id(
          candidate_session_id,
          datachannel_manager,
          snapshot,
        )
        connection_key = self._datachannel_connection_key(preferred_session_id or candidate_session_id)
        if connection_key and connection_key in seen_connection_keys:
          continue
        entries.append((preferred_session_id or str(candidate_session_id), datachannel_manager))
        seen_managers.append(datachannel_manager)
        if connection_key:
          seen_connection_keys.add(connection_key)
      return entries

    def _live_control_datachannel_sessions(self) -> List[Tuple[str, Any]]:
      return self._unique_datachannel_manager_entries(require_send_message=True)

    def rewarded_ad_connection_proof(
      self,
      *,
      session_id: str = "",
      owner_key: str = "",
      allow_single_live_fallback: bool = False,
    ) -> Dict[str, Any]:
      """Return a privacy-minimal liveness proof for Ads Watching.

      The ads website only needs to know whether a native-capable WebRTC
      datachannel is currently targetable. Do not expose session ids, owner
      keys, raw timestamps, ad units, or account identifiers here.
      """
      reply_target: Dict[str, Any] = {"transport": "webrtc"}
      normalized_session_id = str(session_id or "").strip()
      normalized_owner_key = str(owner_key or "").strip()
      if normalized_session_id:
        reply_target["session_id"] = normalized_session_id
      if normalized_owner_key:
        reply_target["owner_key"] = normalized_owner_key

      try:
        target_session_id, manager, _owner, resolution, error = self._resolve_rewarded_ad_control_target(
          reply_target,
          allow_single_live_fallback=bool(allow_single_live_fallback),
        )
      except Exception:
        return {
          "connected": False,
          "resolution": "unavailable",
          "heartbeat_recent": False,
          "heartbeat_age_seconds": None,
        }

      if error or not target_session_id or manager is None:
        return {
          "connected": False,
          "resolution": str(resolution or "unavailable")[:48],
          "heartbeat_recent": False,
          "heartbeat_age_seconds": None,
        }

      heartbeat_age_seconds: Optional[float] = None
      try:
        last_ping_time = float(getattr(manager, "last_ping_time", 0.0) or 0.0)
      except Exception:
        last_ping_time = 0.0
      if last_ping_time > 0:
        heartbeat_age_seconds = round(max(0.0, _runtime.time.time() - last_ping_time), 1)

      return {
        "connected": True,
        "resolution": str(resolution or "reply_target")[:48],
        "heartbeat_recent": bool(
          heartbeat_age_seconds is not None and heartbeat_age_seconds <= 90.0
        ),
        "heartbeat_age_seconds": heartbeat_age_seconds,
      }

    def remote_desktop_keyboard_connection_proof(
      self,
      *,
      session_id: str = "",
      owner_key: str = "",
      allow_single_live_fallback: bool = False,
    ) -> Dict[str, Any]:
      """Return the same privacy-minimal liveness proof for keyboard control.

      The resolver is shared with native rewarded-ad control so exact session,
      owner, and single-client fallback rules remain identical across agents.
      """
      proof = self.rewarded_ad_connection_proof(
        session_id=session_id,
        owner_key=owner_key,
        allow_single_live_fallback=allow_single_live_fallback,
      )
      keyboard_state = "inactive"
      if proof.get("connected"):
        reply_target: Dict[str, Any] = {"transport": "webrtc"}
        if str(session_id or "").strip():
          reply_target["session_id"] = str(session_id).strip()
        if str(owner_key or "").strip():
          reply_target["owner_key"] = str(owner_key).strip()
        try:
          target_session_id, manager, _owner, _resolution, error = self._resolve_rewarded_ad_control_target(
            reply_target,
            allow_single_live_fallback=allow_single_live_fallback,
          )
          if not error and target_session_id and manager is not None:
            lease = self._remote_desktop_keyboard_lease_for_session(str(target_session_id), "")
            candidate = str((lease or {}).get("keyboard_state") or "").strip().lower()
            if candidate in {"visible", "hidden"}:
              keyboard_state = candidate
        except Exception:
          pass
      proof["keyboard_state"] = keyboard_state
      return proof

    def _resolve_webrtc_owner_key(self, session_id: str) -> str:
      try:
        return str(getattr(self._resolve_chat_identity(str(session_id)), "owner_key", "") or "").strip()
      except Exception:
        return ""

    def _resolve_rewarded_ad_control_target(
      self,
      reply_target: Dict[str, Any],
      *,
      allow_single_live_fallback: bool = False,
    ) -> Tuple[Optional[str], Optional[Any], str, str, Optional[str]]:
      normalized_reply_target = dict(reply_target or {})
      requested_owner_key = str(normalized_reply_target.get("owner_key") or "").strip()
      resolved_session_id = self._resolve_webrtc_reply_target_session_id(normalized_reply_target)
      if resolved_session_id:
        manager = self._datachannel_manager_for_session(resolved_session_id, require_send_message=True)
        if manager is None:
          return None, None, "", "unavailable", "The requesting WebRTC client is not ready for control messages."
        owner_key = self._resolve_webrtc_owner_key(str(resolved_session_id))
        if requested_owner_key and owner_key and owner_key != requested_owner_key:
          return None, None, owner_key, "owner_mismatch", "The resolved WebRTC client does not match the requesting account."
        return str(resolved_session_id), manager, owner_key, "reply_target", None

      live_sessions = self._live_control_datachannel_sessions()
      if allow_single_live_fallback and len(live_sessions) == 1:
        fallback_session_id, manager = live_sessions[0]
        owner_key = self._resolve_webrtc_owner_key(fallback_session_id)
        if requested_owner_key and owner_key and owner_key != requested_owner_key:
          return None, None, owner_key, "owner_mismatch", "The single live WebRTC client does not match the requesting account."
        return fallback_session_id, manager, owner_key, "single_live_datachannel", None
      if not live_sessions:
        return None, None, "", "unavailable", "No active client datachannel sessions"
      if allow_single_live_fallback:
        return None, None, "", "ambiguous", "Multiple WebRTC clients are connected; specify the target session or owner."
      return None, None, "", "unavailable", "The requesting WebRTC client is not currently connected."

    def _resolve_client_control_target(
      self,
      reply_target: Dict[str, Any],
      *,
      allow_single_live_fallback: bool = False,
    ) -> Tuple[Optional[str], Optional[Any], str, str, Optional[str]]:
      return self._resolve_rewarded_ad_control_target(
        reply_target,
        allow_single_live_fallback=allow_single_live_fallback,
      )

    async def send_client_browser_control_to_reply_target(
      self,
      reply_target: Dict[str, Any],
      payload: Dict[str, Any],
      *,
      allow_single_live_fallback: bool = False,
    ) -> Tuple[bool, Dict[str, Any]]:
      normalized_reply_target = dict(reply_target or {})
      target_session_id, manager, owner_key, resolution, error = self._resolve_client_control_target(
        normalized_reply_target,
        allow_single_live_fallback=allow_single_live_fallback,
      )
      if error:
        return False, {
          "success": False,
          "reason": error,
          "triggered_count": 0,
          "resolution": resolution,
          "owner_key": owner_key,
        }
      if not target_session_id or manager is None:
        return False, {
          "success": False,
          "reason": "The requesting WebRTC client is not currently connected.",
          "triggered_count": 0,
          "resolution": resolution,
        }

      if _runtime.__dict__.get("create_voice_call_control_message") is None:
        return False, {
          "success": False,
          "reason": "WebRTC control messages are unavailable in this server process.",
          "triggered_count": 0,
          "resolution": resolution,
        }

      control_payload = _runtime._lock_client_browser_control_payload(payload)
      if not control_payload.get("action"):
        return False, {
          "success": False,
          "reason": "Unsupported client browser control action or URL.",
          "triggered_count": 0,
          "target_session_id": str(target_session_id),
          "owner_key": owner_key,
          "resolution": resolution,
        }
      control_payload.setdefault("control_id", f"client-browser-{_runtime.uuid.uuid4().hex}")
      control_payload.setdefault("timestamp_ms", int(_runtime.time.time() * 1000))
      control_payload["session_id"] = str(target_session_id)

      try:
        message = _runtime.create_voice_call_control_message(
          payload=control_payload,
          session_id=str(target_session_id),
          user_id=_runtime.get_configured_server_name(),
        )
        sent = bool(await manager.send_message(message))
      except Exception as exc:
        return False, {
          "success": False,
          "reason": f"Failed to send client browser control message: {exc}",
          "triggered_count": 0,
          "target_session_id": str(target_session_id),
          "owner_key": owner_key,
          "resolution": resolution,
        }

      if not sent:
        return False, {
          "success": False,
          "reason": "The client browser control message was not accepted by the data channel.",
          "triggered_count": 0,
          "target_session_id": str(target_session_id),
          "owner_key": owner_key,
          "resolution": resolution,
        }

      return True, {
        "success": True,
        "triggered_count": 1,
        "target_session_id": str(target_session_id),
        "owner_key": owner_key,
        "resolution": resolution,
        "status": "sent",
      }

    @staticmethod
    def _is_native_call_remote_desktop_payload(payload: Dict[str, Any]) -> bool:
      source = str(payload.get("source") or "").strip().lower()
      platform = str(payload.get("platform") or "").strip().lower()
      return bool(
        (source == "autoyou_lite" and platform in {"ios", "android", "chrome"})
        or (source == "autoyou_desktop" and platform in {"macos", "windows", "linux"})
      )

    async def _probe_remote_desktop_input_backend(self, *, refresh: bool = False) -> bool:
      try:
        return bool(
          await _runtime.asyncio.to_thread(_runtime.remote_desktop_input_backend_available, refresh=refresh)
        )
      except Exception as exc:
        _runtime.LOGGER.debug("Remote desktop input backend probe failed: %s", exc)
        return False

    def _remote_desktop_control_lease_for_session(
      self,
      session_id: str,
      control_id: str = "",
    ) -> Optional[Dict[str, Any]]:
      normalized_control_id = str(control_id or "").strip()
      for candidate_id in self._ordered_related_session_ids(str(session_id or "")):
        lease = self.remote_desktop_control_leases_by_session.get(candidate_id)
        if isinstance(lease, dict) and (
          not normalized_control_id
          or str(lease.get("control_id") or "") == normalized_control_id
        ):
          return lease
      return None

    def _store_remote_desktop_control_lease(
      self,
      session_id: str,
      *,
      control_id: str,
      touch_mode: str,
      track: Any,
      mode: str = "desktop",
    ) -> Dict[str, Any]:
      lease: Dict[str, Any] = {
        "control_id": str(control_id),
        "target_session_id": str(session_id),
        "touch_mode": str(touch_mode or "direct"),
        "mode": mode,
        "track": track,
        "held_buttons": set(),
        "held_keys": set(),
        "input_failures": 0,
        "expires_at": _runtime.time.time() + _runtime._REMOTE_DESKTOP_CONTROL_LEASE_SECONDS,
      }
      mapping_reader = getattr(track, "remote_desktop_mapping", None)
      lease["mapping"] = json.dumps(mapping_reader(), sort_keys=True) if callable(mapping_reader) else None
      alias_ids = self._ordered_related_session_ids(str(session_id)) or [str(session_id)]
      for alias_id in alias_ids:
        self.remote_desktop_control_leases_by_session[alias_id] = lease
      lease["expiry_task"] = _runtime.asyncio.create_task(
        self._expire_remote_desktop_control_lease(str(session_id), lease)
      )
      return lease

    def _apply_game_capture_rate(self, track: Any, *, game_active: bool) -> None:
      apply_profile = getattr(track, "apply_remote_desktop_profile", None)
      if not callable(apply_profile):
        return
      profile = _runtime._get_remote_desktop_capture_profile(cfg=(_runtime.STATE.config or {}))
      try:
        set_game_mode = getattr(track, "set_game_mode", None)
        if callable(set_game_mode):
          set_game_mode(game_active)
        apply_profile(monitor_id=profile["monitor_id"],
                      fps=30 if game_active else profile["fps"],
                      max_width=profile["max_width"])
      except Exception as exc:
        _runtime.LOGGER.debug("Could not change game capture rate: %s", exc)

    def _publish_game_session_start(self, session_id: str, lease: Dict[str, Any]) -> None:
      self.game_input_hub.publish(str(lease.get("target_session_id") or session_id), {
        "event": "game_input", "input_type": "session_start", "control_id": str(lease["control_id"]),
      })
      if (self.game_input_hub.owner == "hosted-neon"
          and _runtime._get_audio_playback_enabled(cfg=(_runtime.STATE.config or {}))
          and not lease.get("hosted_game_audio_manager")):
        _resolved, audio_manager = self._resolve_audio_manager_for_reply_target({
          "transport": "webrtc", "session_id": session_id,
        })
        if audio_manager is not None:
          try:
            audio_manager.play_audio_file(str(_HOSTED_GAME_LOOP), source="hosted_game", loop=True)
            lease["hosted_game_audio_manager"] = audio_manager
          except Exception as exc:
            _runtime.LOGGER.warning("Could not start hosted game audio: %s", exc)

    def _stop_hosted_game_audio(self, lease: Dict[str, Any]) -> None:
      audio_manager = lease.pop("hosted_game_audio_manager", None)
      if audio_manager is None:
        return
      if any(candidate is not lease and candidate.get("hosted_game_audio_manager") is audio_manager
             for candidate in self.remote_desktop_control_leases_by_session.values()):
        return
      try:
        audio_manager.stop_playback(source="hosted_game")
      except Exception as exc:
        _runtime.LOGGER.warning("Could not stop hosted game audio: %s", exc)

    async def _expire_remote_desktop_control_lease(
      self,
      session_id: str,
      lease: Dict[str, Any],
    ) -> None:
      control_id = str(lease.get("control_id") or "")
      while True:
        await _runtime.asyncio.sleep(max(0.0, float(lease.get("expires_at") or 0.0) - _runtime.time.time()))
        async with self.remote_desktop_control_lock:
          if self._remote_desktop_control_lease_for_session(session_id, control_id) is not lease:
            return
          if float(lease.get("expires_at") or 0.0) > _runtime.time.time():
            continue
          if await self._release_remote_desktop_control_locked(
            session_id,
            control_id,
            expected_lease=lease,
          ):
            await self._send_remote_desktop_control_status(
              session_id,
              control_id=control_id,
              active=False,
              reason="Remote desktop control expired.",
            )
          return

    async def _release_remote_desktop_control_locked(
      self,
      session_id: str,
      control_id: str = "",
      *,
      expected_lease: Optional[Dict[str, Any]] = None,
    ) -> bool:
      lease = self._remote_desktop_control_lease_for_session(session_id, control_id)
      if lease is None or (expected_lease is not None and lease is not expected_lease):
        return False
      for alias_id, candidate in list(self.remote_desktop_control_leases_by_session.items()):
        if candidate is lease:
          self.remote_desktop_control_leases_by_session.pop(alias_id, None)
      expiry_task = lease.pop("expiry_task", None)
      if isinstance(expiry_task, _runtime.asyncio.Task) and expiry_task is not _runtime.asyncio.current_task():
        expiry_task.cancel()
        await _runtime.asyncio.gather(expiry_task, return_exceptions=True)
      held_buttons = set(lease.get("held_buttons") or ())
      held_keys = set(lease.get("held_keys") or ())
      lease["held_buttons"] = set()
      lease["held_keys"] = set()
      lease["expires_at"] = 0.0
      if lease.get("mode") == "game":
        self._stop_hosted_game_audio(lease)
        self.game_input_hub.publish(str(lease.get("target_session_id") or session_id), {
          "event": "game_input", "input_type": "session_end", "control_id": str(lease["control_id"]),
        })
        track = lease.get("track")
        still_active = any(candidate.get("mode") == "game" and candidate.get("track") is track
                           for candidate in self.remote_desktop_control_leases_by_session.values())
        self._apply_game_capture_rate(track, game_active=still_active)
      await _runtime.asyncio.to_thread(_runtime.release_remote_desktop_inputs, held_buttons,
                                     **({"held_keys": held_keys} if held_keys else {}))
      return True

    async def _release_remote_desktop_control(
      self,
      session_id: str,
      control_id: str = "",
      *,
      expected_lease: Optional[Dict[str, Any]] = None,
    ) -> bool:
      async with self.remote_desktop_control_lock:
        return await self._release_remote_desktop_control_locked(
          session_id,
          control_id,
          expected_lease=expected_lease,
        )

    async def _send_remote_desktop_control_status(
      self,
      session_id: str,
      *,
      control_id: str = "",
      active: bool,
      reason: str = "",
    ) -> bool:
      manager = self._datachannel_manager_for_session(session_id, require_send_message=True)
      if manager is None or _runtime.__dict__.get("create_voice_call_control_message") is None:
        return False
      payload: Dict[str, Any] = {
        "event": "remote_desktop_control_status",
        # Existing iOS and Android releases use this as the protocol source.
        "source": "autoyou_lite",
        "control_id": str(control_id or ""),
        "active": bool(active),
        "available": _runtime._get_remote_desktop_control_available(cfg=(_runtime.STATE.config or {})),
        "timestamp_ms": int(_runtime.time.time() * 1000),
      }
      lease = self._remote_desktop_control_lease_for_session(session_id, control_id)
      if active and lease is not None and lease.get("mode") == "game":
        payload["engine_input"] = bool(lease.get("engine_input"))
      if reason:
        payload["reason"] = str(reason)[:256]
        if reason in {
          "Start the video call before controlling the desktop.",
          "Start the full-screen video call before controlling the desktop.",
          "The desktop pane is not present in the outbound video.",
          "The video call is not active.",
          "The desktop video source is not attached.",
          "The desktop video source is paused.",
          "The screen layout changed. Resume screen control to continue.",
          "The shared screen is unavailable for control.",
          "Remote desktop control expired.",
          "Remote desktop control is not active for this request.",
          _GAME_INPUT_UNAVAILABLE,
        }:
          payload["retryable"] = True
      _matched_id, track = self._desktop_video_track_for_session(session_id)
      mapping_reader = getattr(track, "remote_desktop_mapping", None)
      if callable(mapping_reader):
        try:
          mapping = mapping_reader()
          if isinstance(mapping, dict):
            payload["mapping"] = mapping
        except Exception:
          pass
      try:
        message = _runtime.create_voice_call_control_message(
          payload=payload,
          session_id=str(session_id),
          user_id=_runtime.get_configured_server_name(),
        )
        return bool(await manager.send_message(message))
      except Exception as exc:
        _runtime.LOGGER.debug("Failed to send remote desktop control status to %s: %s", session_id, exc)
        return False

    def _remote_desktop_control_state(
      self,
      session_id: str,
      control_id: str,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
      lease = self._remote_desktop_control_lease_for_session(session_id, control_id)
      if lease is None:
        return None, "Remote desktop control is not active for this request."
      if float(lease.get("expires_at") or 0.0) <= _runtime.time.time():
        return None, "Remote desktop control expired."
      if lease.get("mode") == "game":
        if not _runtime._get_game_mode_available(cfg=(_runtime.STATE.config or {})):
          return None, "Game mode is disabled or unavailable."
      elif not _runtime._get_remote_desktop_control_available(cfg=(_runtime.STATE.config or {})):
        return None, "Remote desktop control is disabled or unavailable."
      if not self._voice_call_client_active_for_session(session_id):
        return None, "The video call is not active."
      _matched_id, track = self._desktop_video_track_for_session(session_id)
      if track is None or track is not lease.get("track"):
        return None, "The desktop video source is not attached."
      if not callable(getattr(track, "map_output_point_to_desktop", None)):
        return None, "The desktop video source cannot map remote input."
      if getattr(track, "is_enabled", True) is False:
        return None, "The desktop video source is paused."
      try:
        if json.dumps(track.remote_desktop_mapping(), sort_keys=True) != lease.get("mapping"):
          return None, "The screen layout changed. Resume screen control to continue."
      except Exception:
        return None, "The shared screen is unavailable for control."
      return lease, ""

    async def _handle_remote_desktop_control(
      self,
      session_id: str,
      payload: Dict[str, Any],
    ) -> None:
      normalized = _runtime.normalize_remote_desktop_control_payload(payload)
      control_id = str((normalized or {}).get("control_id") or payload.get("control_id") or "")
      async with self.remote_desktop_control_lock:
        if normalized is None or not self._is_native_call_remote_desktop_payload(normalized):
          await self._send_remote_desktop_control_status(
            session_id,
            control_id=control_id,
            active=False,
            reason="Invalid native remote desktop control request.",
          )
          return

        if normalized["action"] == "stop":
          released = await self._release_remote_desktop_control_locked(session_id, control_id)
          await self._send_remote_desktop_control_status(
            session_id,
            control_id=control_id,
            active=False,
            reason=(
              "Remote desktop control stopped."
              if released
              else "Remote desktop control is not active for this request."
            ),
          )
          return

        game_mode = normalized.get("mode") == "game"
        if game_mode and not _runtime._get_game_mode_available(cfg=(_runtime.STATE.config or {})):
          reason = "Game mode is disabled or unavailable."
        elif not game_mode and not _runtime._get_remote_desktop_control_available(cfg=(_runtime.STATE.config or {})):
          reason = "Remote desktop control is disabled or unavailable."
        elif not self._voice_call_client_active_for_session(session_id):
          reason = "Start the video call before controlling the desktop."
        else:
          reason = ""

        host_input_available = False
        if not reason and (not game_mode or not self.game_input_hub.connected):
          host_input_available = await self._probe_remote_desktop_input_backend(refresh=game_mode)
          if not host_input_available and not game_mode:
            reason = "Native input is unavailable on the server host."
          elif not host_input_available and game_mode:
            reason = _GAME_INPUT_UNAVAILABLE

        _matched_id, track = self._desktop_video_track_for_session(session_id)
        mapping_reader = getattr(track, "remote_desktop_mapping", None)
        if not reason and (
          track is None
          or getattr(track, "is_enabled", True) is False
          or not callable(mapping_reader)
        ):
          reason = "Start the full-screen video call before controlling the desktop."
        if not reason:
          try:
            mapping = mapping_reader()
          except Exception:
            mapping = {}
          if not isinstance(mapping, dict) or not mapping.get("content_rect"):
            reason = "The desktop pane is not present in the outbound video."
        if reason:
          await self._send_remote_desktop_control_status(
            session_id,
            control_id=control_id,
            active=False,
            reason=reason,
          )
          return

        existing = self._remote_desktop_control_lease_for_session(session_id)
        if existing is not None:
          await self._release_remote_desktop_control_locked(
            session_id,
            str(existing.get("control_id") or ""),
          )
        lease = self._store_remote_desktop_control_lease(
          session_id,
          control_id=control_id,
          touch_mode=str(normalized.get("touch_mode") or "direct"),
          track=track,
          mode=str(normalized.get("mode") or "desktop"),
        )
        if lease["mode"] == "game":
          lease["engine_input"] = self.game_input_hub.connected
          lease["host_input_available"] = host_input_available
          lease["mouse_fallback"] = host_input_available and not lease["engine_input"]
          self._apply_game_capture_rate(track, game_active=True)
          lease["mapping"] = json.dumps(track.remote_desktop_mapping(), sort_keys=True)
          if lease["engine_input"]:
            self._publish_game_session_start(session_id, lease)
        await self._send_remote_desktop_control_status(
          session_id,
          control_id=control_id,
          active=True,
        )

    async def _forward_game_engine_input_locked(
      self, session_id: str, lease: Dict[str, Any], frame: Optional[Dict[str, Any]],
    ) -> bool:
      if lease.get("mode") != "game":
        return False
      if lease.get("engine_input") and not self.game_input_hub.connected:
        self._stop_hosted_game_audio(lease)
        lease["engine_input"] = False
        lease["host_input_available"] = await self._probe_remote_desktop_input_backend(refresh=True)
        lease["mouse_fallback"] = lease["host_input_available"]
        if not lease["host_input_available"]:
          control_id = str(lease["control_id"])
          await self._release_remote_desktop_control_locked(session_id, control_id)
          await self._send_remote_desktop_control_status(
            session_id, control_id=control_id, active=False, reason=_GAME_INPUT_UNAVAILABLE,
          )
          return True
        await self._send_remote_desktop_control_status(
          session_id, control_id=str(lease["control_id"]), active=True,
        )
        if frame is None or frame.get("input_type") != "touch":
          latest_touch = lease.get("latest_touch_frame")
          if latest_touch and latest_touch["points"]:
            lease["mouse_blocked"] = False
            if not await self._apply_game_touch_fallback_locked(session_id, lease, latest_touch):
              return True
      if self.game_input_hub.connected and not lease.get("engine_input"):
        held_buttons = set(lease.get("held_buttons") or ())
        held_keys = set(lease.get("held_keys") or ())
        if held_buttons or held_keys:
          await _runtime.asyncio.to_thread(_runtime.release_remote_desktop_inputs,
                                           held_buttons, held_keys=held_keys)
        lease["held_buttons"] = set()
        lease["held_keys"] = set()
        lease["mouse_touch_id"] = None
        lease["mouse_point"] = None
        lease["engine_input"] = True
        lease["mouse_fallback"] = False
        self._publish_game_session_start(session_id, lease)
        if frame is None or frame.get("input_type") != "touch":
          latest_touch = lease.get("latest_touch_frame")
          if latest_touch and latest_touch["points"]:
            self.game_input_hub.publish(str(lease.get("target_session_id") or session_id), latest_touch)
        await self._send_remote_desktop_control_status(
          session_id, control_id=str(lease["control_id"]), active=True,
        )
      if frame is None:
        return True
      if lease.get("engine_input"):
        self.game_input_hub.publish(str(lease.get("target_session_id") or session_id), frame)
      elif lease.get("host_input_available"):
        return False
      lease["expires_at"] = _runtime.time.time() + _runtime._REMOTE_DESKTOP_CONTROL_LEASE_SECONDS
      return True

    async def _sync_game_input_engine_state(self) -> None:
      async with self.remote_desktop_control_lock:
        seen: set[int] = set()
        for session_id, lease in list(self.remote_desktop_control_leases_by_session.items()):
          if lease.get("mode") != "game" or id(lease) in seen:
            continue
          seen.add(id(lease))
          await self._forward_game_engine_input_locked(session_id, lease, None)

    async def _report_remote_desktop_input_failure_locked(
      self,
      session_id: str,
      lease: Dict[str, Any],
    ) -> None:
      lease["input_failures"] = int(lease.get("input_failures") or 0) + 1
      if lease["input_failures"] < _runtime._REMOTE_DESKTOP_INPUT_FAILURE_LIMIT:
        return
      control_id = str(lease.get("control_id") or "")
      await self._probe_remote_desktop_input_backend(refresh=True)
      await self._release_remote_desktop_control_locked(session_id, control_id)
      await self._send_remote_desktop_control_status(
        session_id,
        control_id=control_id,
        active=False,
        reason="The server host stopped accepting remote input.",
      )

    async def _handle_remote_desktop_input(
      self,
      session_id: str,
      payload: Dict[str, Any],
    ) -> None:
      normalized = _runtime.normalize_remote_desktop_input_payload(payload)
      if normalized is None or not self._is_native_call_remote_desktop_payload(normalized):
        return
      control_id = str(normalized.get("control_id") or "")
      async with self.remote_desktop_control_lock:
        lease, reason = self._remote_desktop_control_state(session_id, control_id)
        if lease is None:
          await self._release_remote_desktop_control_locked(session_id, control_id)
          await self._send_remote_desktop_control_status(
            session_id,
            control_id=control_id,
            active=False,
            reason=reason,
          )
          return
        if await self._forward_game_engine_input_locked(session_id, lease, normalized):
          return
        applied = await _runtime.asyncio.to_thread(
          _runtime.execute_remote_desktop_input,
          normalized,
          track=lease.get("track"),
        )
        if not applied:
          await self._report_remote_desktop_input_failure_locked(session_id, lease)
          return
        lease["input_failures"] = 0
        if normalized["input_type"] == "button":
          button = str(normalized.get("button") or "left")
          if normalized.get("phase") == "down":
            lease["held_buttons"].add(button)
          elif normalized.get("phase") == "up":
            lease["held_buttons"].discard(button)
        lease["expires_at"] = _runtime.time.time() + _runtime._REMOTE_DESKTOP_CONTROL_LEASE_SECONDS

    async def _apply_game_touch_fallback_locked(
      self, session_id: str, lease: Dict[str, Any], frame: Dict[str, Any],
    ) -> bool:
      points = frame["points"]
      if len(points) > 1:
        lease["mouse_blocked"] = True
      elif not points:
        lease["mouse_blocked"] = False
      touch = points[0] if len(points) == 1 and not lease.get("mouse_blocked") else None
      old_id = lease.get("mouse_touch_id")
      if old_id is not None and (touch is None or touch["id"] != old_id):
        released = await _runtime.asyncio.to_thread(
          _runtime.execute_remote_desktop_input,
          {"control_id": frame["control_id"], "input_type": "button", "button": "left", "phase": "up"},
          track=lease["track"],
        )
        if not released:
          await self._report_remote_desktop_input_failure_locked(session_id, lease)
          return False
        lease["input_failures"] = 0
        lease["held_buttons"].discard("left")
        lease["mouse_touch_id"] = None
        lease["mouse_point"] = None
      if touch is not None:
        point = (touch["x"], touch["y"])
        if lease.get("mouse_touch_id") is None:
          command = {"control_id": frame["control_id"], "input_type": "button", "button": "left",
                     "phase": "down", "x": point[0], "y": point[1]}
        elif point != lease.get("mouse_point"):
          command = {"control_id": frame["control_id"], "input_type": "move",
                     "coordinate_mode": "absolute", "x": point[0], "y": point[1]}
        else:
          command = None
        if command is not None:
          applied = await _runtime.asyncio.to_thread(
            _runtime.execute_remote_desktop_input, command, track=lease["track"],
          )
          if not applied:
            await self._report_remote_desktop_input_failure_locked(session_id, lease)
            return False
          lease["input_failures"] = 0
          lease["mouse_touch_id"] = touch["id"]
          lease["mouse_point"] = point
          lease["held_buttons"].add("left")
      return True

    async def _handle_game_input(self, session_id: str, payload: Dict[str, Any]) -> None:
      frame = normalize_game_input(payload)
      if frame is None or not self._is_native_call_remote_desktop_payload(payload):
        return
      async with self.remote_desktop_control_lock:
        lease, reason = self._remote_desktop_control_state(session_id, frame["control_id"])
        if lease is None:
          await self._release_remote_desktop_control_locked(session_id, frame["control_id"])
          await self._send_remote_desktop_control_status(
            session_id, control_id=frame["control_id"], active=False, reason=reason,
          )
          return
        if lease.get("mode") != "game" or not _runtime._get_game_mode_available(cfg=(_runtime.STATE.config or {})):
          return
        if frame["input_type"] == "heartbeat":
          lease["expires_at"] = _runtime.time.time() + _runtime._REMOTE_DESKTOP_CONTROL_LEASE_SECONDS
          return
        if frame["input_type"] == "touch":
          lease["latest_touch_frame"] = frame
        if await self._forward_game_engine_input_locked(session_id, lease, frame):
          return
        if lease.get("mouse_fallback") and frame["input_type"] == "touch":
          if not await self._apply_game_touch_fallback_locked(session_id, lease, frame):
            return
        elif lease.get("host_input_available") and frame["input_type"] in {"axis", "button"}:
          key = None
          if frame["input_type"] == "axis" and frame["axis"] in {"left_x", "left_y"}:
            direction = -1 if frame["value"] < -0.55 else 1 if frame["value"] > 0.55 else 0
            if direction != lease.get(frame["axis"], 0):
              lease[frame["axis"]] = direction
              if direction:
                keys = ("left", "right") if frame["axis"] == "left_x" else ("up", "down")
                key = keys[0 if direction < 0 else 1]
          elif frame["input_type"] == "button" and frame["phase"] == "down":
            key = {"action_a": "space", "jump": "space", "action_b": "right",
                   "left": "left", "right": "right"}.get(frame["button"])
          if key:
            applied = await _runtime.asyncio.to_thread(_runtime.execute_remote_desktop_keyboard, {
              "action": "key", "key": key, "phase": "press", "control_id": frame["control_id"],
            })
            if not applied:
              await self._report_remote_desktop_input_failure_locked(session_id, lease)
              return
            lease["input_failures"] = 0
        lease["expires_at"] = _runtime.time.time() + _runtime._REMOTE_DESKTOP_CONTROL_LEASE_SECONDS

    async def _handle_call_remote_desktop_keyboard(
      self,
      session_id: str,
      payload: Dict[str, Any],
    ) -> None:
      normalized = _runtime.normalize_remote_desktop_keyboard_payload(payload)
      if normalized is None or not self._is_native_call_remote_desktop_payload(normalized):
        return
      action = str(normalized.get("action") or "")
      if action not in {"input", "key", "hide", "state"}:
        return
      control_id = str(normalized.get("control_id") or "")
      async with self.remote_desktop_control_lock:
        lease, reason = self._remote_desktop_control_state(session_id, control_id)
        if lease is None:
          await self._release_remote_desktop_control_locked(session_id, control_id)
          await self._send_remote_desktop_control_status(
            session_id,
            control_id=control_id,
            active=False,
            reason=reason,
          )
          return
        if action in {"hide", "input", "key"} and await self._forward_game_engine_input_locked(
          session_id, lease, normalized,
        ):
          return
        if action == "hide":
          keys = set(lease.get("held_keys") or ())
          lease["held_keys"] = set()
          if keys:
            await _runtime.asyncio.to_thread(_runtime.release_remote_desktop_inputs, held_keys=keys)
          else:
            await _runtime.asyncio.to_thread(_runtime.release_stuck_modifiers)
        elif action in {"input", "key"}:
          held_keys = lease.setdefault("held_keys", set())
          if action == "key" and normalized["phase"] == "down" and len(held_keys) >= 128:
            await self._release_remote_desktop_control_locked(session_id, control_id)
            await self._send_remote_desktop_control_status(session_id, control_id=control_id, active=False,
                                                          reason="Screen control stopped after too many held keys.")
            return
          applied = await _runtime.asyncio.to_thread(_runtime.execute_remote_desktop_keyboard, normalized)
          if not applied:
            await self._report_remote_desktop_input_failure_locked(session_id, lease)
            return
          lease["input_failures"] = 0
          if action == "key":
            if normalized["phase"] == "down":
              held_keys.add(normalized["key"])
            else:
              held_keys.discard(normalized["key"])
        lease["expires_at"] = _runtime.time.time() + _runtime._REMOTE_DESKTOP_CONTROL_LEASE_SECONDS

    def _prune_remote_desktop_keyboard_leases(self) -> None:
      now = _runtime.time.time()
      for session_id, lease in list(self.remote_desktop_keyboard_leases_by_session.items()):
        try:
          expires_at = float(lease.get("expires_at") or 0.0)
        except (TypeError, ValueError):
          expires_at = 0.0
        if expires_at <= now:
          self.remote_desktop_keyboard_leases_by_session.pop(session_id, None)

    def _remote_desktop_keyboard_lease_for_session(
      self,
      session_id: str,
      control_id: str,
    ) -> Optional[Dict[str, Any]]:
      self._prune_remote_desktop_keyboard_leases()
      normalized_control_id = str(control_id or "").strip()
      for candidate_id in self._ordered_related_session_ids(str(session_id or "")):
        lease = self.remote_desktop_keyboard_leases_by_session.get(candidate_id)
        if isinstance(lease, dict) and (
          not normalized_control_id or str(lease.get("control_id") or "") == normalized_control_id
        ):
          return lease
      return None

    def _store_remote_desktop_keyboard_lease(
      self,
      target_session_id: str,
      *,
      control_id: str,
      owner_key: str,
      keyboard_state: str = "visible",
    ) -> Dict[str, Any]:
      normalized_keyboard_state = str(keyboard_state or "visible").strip().lower()
      if normalized_keyboard_state not in {"visible", "hidden"}:
        normalized_keyboard_state = "visible"
      lease = {
        "control_id": str(control_id),
        "target_session_id": str(target_session_id),
        "owner_key": str(owner_key or ""),
        "keyboard_state": normalized_keyboard_state,
        "expires_at": _runtime.time.time() + 10 * 60,
      }
      alias_ids = self._ordered_related_session_ids(str(target_session_id))
      if not alias_ids:
        alias_ids = [str(target_session_id)]
      for alias_id in alias_ids:
        self.remote_desktop_keyboard_leases_by_session[alias_id] = dict(lease)
      return lease

    def _clear_remote_desktop_keyboard_lease(self, target_session_id: str, control_id: str = "") -> None:
      normalized_control_id = str(control_id or "").strip()
      for alias_id in self._ordered_related_session_ids(str(target_session_id)):
        lease = self.remote_desktop_keyboard_leases_by_session.get(alias_id)
        if not normalized_control_id or (
          isinstance(lease, dict) and str(lease.get("control_id") or "") == normalized_control_id
        ):
          self.remote_desktop_keyboard_leases_by_session.pop(alias_id, None)

    async def send_remote_desktop_keyboard_control_to_reply_target(
      self,
      reply_target: Dict[str, Any],
      payload: Dict[str, Any],
      *,
      allow_single_live_fallback: bool = False,
    ) -> Tuple[bool, Dict[str, Any]]:
      """Send one idempotent native-keyboard command to one WebRTC client."""
      normalized_reply_target = dict(reply_target or {})
      target_session_id, manager, owner_key, resolution, error = self._resolve_client_control_target(
        normalized_reply_target,
        allow_single_live_fallback=allow_single_live_fallback,
      )
      if error or not target_session_id or manager is None:
        return False, {
          "success": False,
          "reason": error or "The requesting WebRTC client is not currently connected.",
          "triggered_count": 0,
          "resolution": resolution,
          "owner_key": owner_key,
        }
      if _runtime.__dict__.get("create_voice_call_control_message") is None:
        return False, {
          "success": False,
          "reason": "WebRTC control messages are unavailable in this server process.",
          "triggered_count": 0,
          "resolution": resolution,
        }

      control_payload = _runtime._lock_remote_desktop_keyboard_control_payload(payload)
      action = str(control_payload.get("action") or "")
      if not action:
        return False, {
          "success": False,
          "reason": "Unsupported remote desktop keyboard action.",
          "target_session_id": str(target_session_id),
          "owner_key": owner_key,
          "resolution": resolution,
        }

      self._prune_remote_desktop_keyboard_leases()
      existing_lease = self._remote_desktop_keyboard_lease_for_session(str(target_session_id), "")
      created_lease = False
      if action == "show":
        if existing_lease is None:
          control_id = f"remote-keyboard-{_runtime.uuid.uuid4().hex}"
          created_lease = True
        else:
          control_id = str(existing_lease.get("control_id") or "")
        self._store_remote_desktop_keyboard_lease(
          str(target_session_id),
          control_id=control_id,
          owner_key=owner_key,
          keyboard_state="visible",
        )
      else:
        control_id = str(control_payload.get("control_id") or "").strip()
        lease = self._remote_desktop_keyboard_lease_for_session(str(target_session_id), control_id)
        if lease is None:
          return False, {
            "success": False,
            "reason": "The native keyboard control lease is missing or expired.",
            "target_session_id": str(target_session_id),
            "owner_key": owner_key,
            "resolution": resolution,
          }

      control_payload["control_id"] = control_id
      control_payload["timestamp_ms"] = int(_runtime.time.time() * 1000)
      control_payload["session_id"] = str(target_session_id)

      try:
        message = _runtime.create_voice_call_control_message(
          payload=control_payload,
          session_id=str(target_session_id),
          user_id=_runtime.get_configured_server_name(),
        )
        sent = bool(await manager.send_message(message))
      except Exception as exc:
        sent = False
        send_error = str(exc)
      else:
        send_error = ""

      if not sent:
        if action == "show" and created_lease:
          self._clear_remote_desktop_keyboard_lease(str(target_session_id), control_id)
        return False, {
          "success": False,
          "reason": send_error or "The native keyboard control message was not accepted by the data channel.",
          "target_session_id": str(target_session_id),
          "owner_key": owner_key,
          "resolution": resolution,
        }

      if action == "hide":
        self._clear_remote_desktop_keyboard_lease(str(target_session_id), control_id)
        # See the matching comment in _handle_voice_call_control_message: this
        # push can race the client's own modifier keyUp cleanup, so release on
        # the host unconditionally rather than trusting the client to land its
        # cleanup message before the lease disappears.
        await _runtime.asyncio.to_thread(_runtime.release_stuck_modifiers)
      return True, {
        "success": True,
        "triggered_count": 1,
        "status": "resent" if action == "show" and existing_lease is not None else "sent",
        "control_id": control_id,
        "target_session_id": str(target_session_id),
        "owner_key": owner_key,
        "resolution": resolution,
      }

    def _prune_rewarded_ad_control_leases(self) -> None:
      now = _runtime.time.time()
      for session_id, lease in list(self.rewarded_ad_control_leases_by_session.items()):
        try:
          expires_at = float(lease.get("expires_at") or 0.0)
        except (TypeError, ValueError):
          expires_at = 0.0
        if expires_at <= now:
          self.rewarded_ad_control_leases_by_session.pop(session_id, None)

    def _rewarded_ad_control_lease_for_session(self, session_id: str) -> Optional[Dict[str, Any]]:
      self._prune_rewarded_ad_control_leases()
      for candidate_id in self._ordered_related_session_ids(str(session_id or "")):
        lease = self.rewarded_ad_control_leases_by_session.get(candidate_id)
        if isinstance(lease, dict):
          return lease
      return None

    def _store_rewarded_ad_control_lease(self, target_session_id: str, control_id: str) -> None:
      lease = {
        "control_id": str(control_id),
        "target_session_id": str(target_session_id),
        "expires_at": _runtime.time.time() + _runtime._REWARDED_AD_CONTROL_LEASE_SECONDS,
      }
      alias_ids = self._ordered_related_session_ids(str(target_session_id))
      if not alias_ids:
        alias_ids = [str(target_session_id)]
      for alias_id in alias_ids:
        self.rewarded_ad_control_leases_by_session[alias_id] = dict(lease)

    def _clear_rewarded_ad_control_lease(self, target_session_id: str, control_id: str = "") -> None:
      normalized_control_id = str(control_id or "").strip()
      for alias_id in self._ordered_related_session_ids(str(target_session_id)):
        lease = self.rewarded_ad_control_leases_by_session.get(alias_id)
        if not normalized_control_id or (
          isinstance(lease, dict) and str(lease.get("control_id") or "") == normalized_control_id
        ):
          self.rewarded_ad_control_leases_by_session.pop(alias_id, None)

    async def send_rewarded_ad_control_to_reply_target(
      self,
      reply_target: Dict[str, Any],
      payload: Dict[str, Any],
      *,
      allow_single_live_fallback: bool = False,
    ) -> Tuple[bool, Dict[str, Any]]:
      normalized_reply_target = dict(reply_target or {})
      target_session_id, manager, owner_key, resolution, error = self._resolve_rewarded_ad_control_target(
        normalized_reply_target,
        allow_single_live_fallback=allow_single_live_fallback,
      )
      if error:
        return False, {
          "success": False,
          "reason": error,
          "triggered_count": 0,
          "resolution": resolution,
          "owner_key": owner_key,
        }
      if not target_session_id or manager is None:
        return False, {
          "success": False,
          "reason": "The requesting WebRTC client is not currently connected.",
          "triggered_count": 0,
          "resolution": resolution,
        }

      if _runtime.__dict__.get("create_voice_call_control_message") is None:
        return False, {
          "success": False,
          "reason": "WebRTC control messages are unavailable in this server process.",
          "triggered_count": 0,
          "resolution": resolution,
        }

      control_payload = _runtime._lock_native_mobile_rewarded_ad_control_payload(payload)
      existing_lease = self._rewarded_ad_control_lease_for_session(str(target_session_id))
      if existing_lease is not None:
        return True, {
          "success": True,
          "triggered_count": 0,
          "status": "already_active",
          "control_id": str(existing_lease.get("control_id") or ""),
          "target_session_id": str(target_session_id),
          "owner_key": owner_key,
          "resolution": resolution,
        }

      control_id = str(control_payload.get("control_id") or "").strip()
      if not control_id:
        control_id = f"rewarded-ad-{_runtime.uuid.uuid4().hex}"
      control_payload["control_id"] = control_id[:128]
      control_payload.setdefault("timestamp_ms", int(_runtime.time.time() * 1000))
      control_payload["session_id"] = str(target_session_id)
      self._store_rewarded_ad_control_lease(str(target_session_id), control_payload["control_id"])

      try:
        message = _runtime.create_voice_call_control_message(
          payload=control_payload,
          session_id=str(target_session_id),
          user_id=_runtime.get_configured_server_name(),
        )
        sent = bool(await manager.send_message(message))
      except Exception as exc:
        self._clear_rewarded_ad_control_lease(str(target_session_id), control_payload["control_id"])
        return False, {
          "success": False,
          "reason": f"Failed to send rewarded ad control message: {exc}",
          "triggered_count": 0,
          "target_session_id": str(target_session_id),
          "owner_key": owner_key,
          "resolution": resolution,
        }

      if not sent:
        self._clear_rewarded_ad_control_lease(str(target_session_id), control_payload["control_id"])
        return False, {
          "success": False,
          "reason": "The rewarded ad control message was not accepted by the data channel.",
          "triggered_count": 0,
          "target_session_id": str(target_session_id),
          "owner_key": owner_key,
          "resolution": resolution,
        }

      return True, {
        "success": True,
        "triggered_count": 1,
        "status": "sent",
        "control_id": control_payload["control_id"],
        "target_session_id": str(target_session_id),
        "owner_key": owner_key,
        "resolution": resolution,
      }

    async def send_chat_to_session(
      self,
      session_id: str,
      message: str,
      *,
      metadata: Optional[Dict[str, Any]] = None,
      context: Optional[List[Dict[str, Any]]] = None,
      user_id: Optional[str] = None,
      queue_if_undelivered: bool = True,
    ) -> bool:
      # A message that cannot be sent now is kept for the device's next
      # connection unless the caller reports the failure to a person instead.
      normalized_session_id = str(session_id or "").strip()
      normalized_message = str(message or "").strip()
      context_payload = [dict(item) for item in (context or []) if isinstance(item, dict)]
      if not normalized_session_id or (not normalized_message and not context_payload):
        return False

      effective_metadata = dict(metadata or {})
      try:
        identity = self._resolve_chat_identity(normalized_session_id)
      except Exception as exc:
        _runtime.LOGGER.debug(
          "Failed to resolve chat identity for outbound WebRTC chat to %s: %s",
          normalized_session_id,
          exc,
        )
        identity = None

      if identity is not None:
        owner_key = str(getattr(identity, "owner_key", "") or "").strip()
        canonical_user_id = str(getattr(identity, "canonical_user_id", "") or "").strip()
        canonical_session_id = str(getattr(identity, "canonical_session_id", "") or "").strip()

        if owner_key:
          effective_metadata.setdefault("canonical_owner_key", owner_key)
        if canonical_user_id:
          effective_metadata.setdefault("canonical_user_id", canonical_user_id)

        if not _runtime.normalize_reply_target(effective_metadata.get("reply_target")):
          reply_target = self._build_webrtc_reply_target(normalized_session_id, identity)
          if reply_target:
            effective_metadata["reply_target"] = reply_target

        requested_conversation_session_id = str(
          effective_metadata.get("conversation_session_id") or ""
        ).strip()
        if not requested_conversation_session_id:
          # No conversation was named, so this goes to the one the device is
          # in now, as an AI reply does. The transport identity carries no
          # thread: on its own it names the device's first conversation, and
          # a client that has since started another ignores a message for it.
          identity = _runtime._resolve_conversation_identity(identity)
        if not requested_conversation_session_id or requested_conversation_session_id == canonical_session_id:
          conversation_metadata = _runtime._build_conversation_metadata(identity)
          effective_metadata["conversation_session_id"] = conversation_metadata.get(
            "conversation_session_id"
          )
          effective_metadata["conversation_thread_id"] = conversation_metadata.get(
            "conversation_thread_id"
          )
          effective_metadata.setdefault(
            "conversation_reset",
            conversation_metadata.get("conversation_reset"),
          )

      response_message = _runtime.create_chat_message(
        message=normalized_message,
        session_id=normalized_session_id,
        user_id=user_id or _runtime.get_configured_server_name(),
        context=context_payload,
        metadata=effective_metadata,
      )
      stable_session_id = self._resolve_voice_chat_session_id(normalized_session_id)

      datachannel_manager = self._datachannel_manager_for_session(
        normalized_session_id,
        require_send_message=True,
      )
      if not datachannel_manager:
        _runtime.LOGGER.warning(
          "No live datachannel manager for outbound WebRTC chat to %s; %s",
          normalized_session_id,
          "queuing offline" if queue_if_undelivered else "not sent",
        )
        if queue_if_undelivered:
          self._enqueue_to_offline_queue(stable_session_id, response_message, label="chat message")
        return False

      try:
        sent = bool(await datachannel_manager.send_message(response_message))
      except Exception as exc:
        _runtime.LOGGER.warning(
          "Failed to send outbound WebRTC chat to %s: %s",
          normalized_session_id,
          exc,
        )
        sent = False

      if not sent and queue_if_undelivered:
        self._enqueue_to_offline_queue(stable_session_id, response_message, label="chat message")

      return sent

    async def send_chat_to_reply_target(
      self,
      reply_target: Dict[str, Any],
      message: str,
      *,
      metadata: Optional[Dict[str, Any]] = None,
      context: Optional[List[Dict[str, Any]]] = None,
      user_id: Optional[str] = None,
      queue_if_undelivered: bool = True,
    ) -> bool:
      normalized_transport = str((reply_target or {}).get("transport") or "").strip().lower()
      if normalized_transport not in {"webrtc", "webrtc-datachannel", "datachannel"}:
        return False

      resolved_session_id = self._resolve_webrtc_reply_target_session_id(dict(reply_target or {}))
      if not resolved_session_id:
        _runtime.LOGGER.warning("No connected WebRTC session matches reply target %s", reply_target)
        return False

      return await self.send_chat_to_session(
        resolved_session_id,
        message,
        metadata=metadata,
        context=context,
        user_id=user_id,
        queue_if_undelivered=queue_if_undelivered,
      )

    def _resolve_audio_manager_for_reply_target(
            self,
            reply_target: Dict[str, Any],
        ) -> Tuple[Optional[str], Optional[Any]]:
            normalized_reply_target = dict(reply_target or {})
            target_session_id = str(normalized_reply_target.get("session_id") or "").strip()
            if target_session_id:
                audio_manager = _runtime.STATE.audio_managers.get(target_session_id)
                if audio_manager is not None:
                    return target_session_id, audio_manager

            resolved_session_id = self._resolve_webrtc_reply_target_session_id(normalized_reply_target)
            if resolved_session_id:
                audio_manager = _runtime.STATE.audio_managers.get(resolved_session_id)
                if audio_manager is not None:
                    return resolved_session_id, audio_manager

            target_owner_key = str(normalized_reply_target.get("owner_key") or "").strip()
            if not target_owner_key and resolved_session_id:
                try:
                    target_owner_key = str(
                        getattr(self._resolve_chat_identity(str(resolved_session_id)), "owner_key", "") or ""
                    ).strip()
                except Exception:
                    target_owner_key = ""

            if target_owner_key:
                for candidate_session_id, candidate_audio_manager in list(_runtime.STATE.audio_managers.items()):
                    if candidate_audio_manager is None:
                        continue
                    try:
                        identity = self._resolve_chat_identity(str(candidate_session_id))
                    except Exception:
                        continue
                    if str(getattr(identity, "owner_key", "") or "").strip() != target_owner_key:
                        continue
                    if resolved_session_id and str(candidate_session_id) != str(resolved_session_id):
                        _runtime.LOGGER.info(
                            "Resolved WebRTC playback audio manager via owner_key fallback: reply_target=%s datachannel_session=%s audio_session=%s",
                            normalized_reply_target,
                            resolved_session_id,
                            candidate_session_id,
                        )
                    return str(candidate_session_id), candidate_audio_manager

            if resolved_session_id:
                _runtime.LOGGER.warning(
                    "No active audio manager matches WebRTC playback target %s (resolved_session_id=%s, owner_key=%s, audio_manager_keys=%s)",
                    normalized_reply_target,
                    resolved_session_id,
                    target_owner_key,
                    sorted(str(key) for key in _runtime.STATE.audio_managers.keys()),
                )
                return resolved_session_id, None

            return None, None

    async def play_audio_file_to_reply_target(
            self,
            reply_target: Dict[str, Any],
            file_path: str,
        ) -> Tuple[bool, Dict[str, Any]]:
            if not _runtime._get_audio_playback_enabled(cfg=(_runtime.STATE.config or {})):
                return False, _runtime._audio_playback_disabled_status()
            resolved_session_id, audio_manager = self._resolve_audio_manager_for_reply_target(reply_target)
            if not resolved_session_id or audio_manager is None or not hasattr(audio_manager, "play_audio_file"):
                return False, {
                    "event": "playback",
                    "state": "unavailable",
                    "detail": "No active voice playback session is available.",
                }
            try:
                return True, dict(audio_manager.play_audio_file(file_path, source="audio_file"))
            except FileNotFoundError:
                return False, {
                    "event": "playback",
                    "state": "error",
                    "detail": "Audio file not found.",
                }
            except Exception as exc:
                _runtime.LOGGER.warning("Failed to play outbound audio for %s: %s", resolved_session_id, exc)
                return False, {
                    "event": "playback",
                    "state": "error",
                    "detail": str(exc),
                }

    async def pause_audio_playback_for_reply_target(
            self,
            reply_target: Dict[str, Any],
        ) -> Tuple[bool, Dict[str, Any]]:
            if not _runtime._get_audio_playback_enabled(cfg=(_runtime.STATE.config or {})):
                return False, _runtime._audio_playback_disabled_status()
            resolved_session_id, audio_manager = self._resolve_audio_manager_for_reply_target(reply_target)
            if not resolved_session_id or audio_manager is None or not hasattr(audio_manager, "pause_playback"):
                return False, {
                    "event": "playback",
                    "state": "unavailable",
                    "detail": "No active voice playback session is available.",
                }
            ok = bool(audio_manager.pause_playback())
            return ok, dict(audio_manager.get_playback_status())

    async def resume_audio_playback_for_reply_target(
            self,
            reply_target: Dict[str, Any],
        ) -> Tuple[bool, Dict[str, Any]]:
            if not _runtime._get_audio_playback_enabled(cfg=(_runtime.STATE.config or {})):
                return False, _runtime._audio_playback_disabled_status()
            resolved_session_id, audio_manager = self._resolve_audio_manager_for_reply_target(reply_target)
            if not resolved_session_id or audio_manager is None or not hasattr(audio_manager, "resume_playback"):
                return False, {
                    "event": "playback",
                    "state": "unavailable",
                    "detail": "No active voice playback session is available.",
                }
            ok = bool(audio_manager.resume_playback())
            return ok, dict(audio_manager.get_playback_status())

    async def stop_audio_playback_for_reply_target(
            self,
            reply_target: Dict[str, Any],
        ) -> Tuple[bool, Dict[str, Any]]:
            if not _runtime._get_audio_playback_enabled(cfg=(_runtime.STATE.config or {})):
                return False, _runtime._audio_playback_disabled_status()
            resolved_session_id, audio_manager = self._resolve_audio_manager_for_reply_target(reply_target)
            if (
                not resolved_session_id
                or audio_manager is None
                or not (
                    hasattr(audio_manager, "stop_playback")
                    or hasattr(audio_manager, "stop_speaking")
                )
            ):
                return False, {
                    "event": "playback",
                    "state": "unavailable",
                    "detail": "No active voice playback session is available.",
                }
            _runtime._stop_audio_manager_playback(
                audio_manager,
                source=f"admin_api:{resolved_session_id}:stop",
            )
            return True, dict(audio_manager.get_playback_status())

    async def get_audio_playback_status_for_reply_target(
            self,
            reply_target: Dict[str, Any],
        ) -> Dict[str, Any]:
            if not _runtime._get_audio_playback_enabled(cfg=(_runtime.STATE.config or {})):
                return _runtime._audio_playback_disabled_status()
            resolved_session_id, audio_manager = self._resolve_audio_manager_for_reply_target(reply_target)
            if not resolved_session_id or audio_manager is None or not hasattr(audio_manager, "get_playback_status"):
                return {
                    "event": "playback",
                    "state": "unavailable",
                    "detail": "No active voice playback session is available.",
                }
            return dict(audio_manager.get_playback_status())

    async def _voice_command_worker(self, session_id: str):
        """Process queued voice commands sequentially per session."""
        queue_ref = self.voice_command_queues.get(session_id)
        if not queue_ref:
            return
        _runtime.LOGGER.info(f"Started voice command worker for {session_id}")
        try:
            while True:
                item = await queue_ref.get()
                try:
                    if item is None:
                        return

                    text = item
                    transcript_already_mirrored = False
                    queued_identity = None
                    if isinstance(item, tuple):
                        if item:
                            text = item[0]
                        if len(item) > 1:
                            transcript_already_mirrored = bool(item[1])
                        if len(item) > 2:
                            queued_identity = item[2]

                    await self._process_voice_command(
                        session_id,
                        text,
                        transcript_already_mirrored=bool(transcript_already_mirrored),
                        identity=queued_identity,
                    )
                except Exception as e:
                    _runtime.LOGGER.error(f"Voice command worker error for {session_id}: {e}")
                finally:
                    queue_ref.task_done()
        finally:
            _runtime.LOGGER.info(f"Stopped voice command worker for {session_id}")

    async def _enqueue_voice_command(self, session_id: str, text: str):
        """Enqueue STT transcript for sequential processing."""
        normalized = (text or "").strip()
        if not normalized:
            return
        if not _runtime._get_video_call_agent_processing_enabled(cfg=(_runtime.STATE.config or {})):
            _runtime.LOGGER.info(
                "Dropping voice transcript for %s because video-call AutoYou Agent processing is disabled",
                session_id,
            )
            return

        dc_session_id = self._resolve_voice_chat_session_id(session_id)
        identity = _runtime._resolve_conversation_identity(self._resolve_chat_identity(dc_session_id))

        # Use the latest client-facing session_id (iOS/Android UUID) for both UI
        # delivery and canonical chat identity. Normal OTP pair starts on a raw
        # /signal session UUID and later re-registers under the client UUID. If
        # voice keeps using the raw UUID, transcripts/replies fragment into a
        # second guest conversation and the client drops those bubbles.

        queue_ref = self.voice_command_queues.get(session_id)
        if queue_ref is None:
            queue_ref = _runtime.asyncio.Queue(maxsize=self.voice_command_queue_maxsize)
            self.voice_command_queues[session_id] = queue_ref
        worker = self.voice_command_workers.get(session_id)
        if worker is None or worker.done():
            if worker is not None:
                try:
                    worker_exc = worker.exception()
                except _runtime.asyncio.CancelledError:
                    worker_exc = None
                except Exception as inspect_err:
                    worker_exc = inspect_err
                if worker_exc is not None:
                    _runtime.LOGGER.warning(
                        "Restarting dead voice command worker for %s after error: %r",
                        session_id,
                        worker_exc,
                    )
                else:
                    _runtime.LOGGER.info("Restarting completed voice command worker for %s", session_id)
            self.voice_command_workers[session_id] = _runtime.asyncio.create_task(
                self._voice_command_worker(session_id)
            )

        # Mirror transcript immediately for UX when possible. If the chat
        # datachannel is not ready yet, buffer the transcript and flush it as
        # soon as the channel becomes usable.
        transcript_already_mirrored = False
        try:
            user_transcript = _runtime.create_chat_message(
                message=normalized,
                session_id=dc_session_id,
                user_id=dc_session_id,  # Must match session_id so client renders user bubble
                context=[],
                metadata={
                    "source": "voice_call",
                    "is_transcription": True,
                    **_runtime._build_conversation_metadata(identity),
                },
            )
            delivery_state = await self._deliver_voice_chat_message(
                dc_session_id,
                user_transcript,
                label="voice transcript",
            )
            transcript_already_mirrored = delivery_state in {"sent", "buffered"}
            if delivery_state == "sent":
                _runtime.LOGGER.info(f"Mirrored user STT to DataChannel: {normalized}")
        except Exception as dc_err:
            _runtime.LOGGER.warning(f"Failed to mirror user STT to datachannel for {session_id}: {dc_err}")

        if queue_ref.full():
            try:
                dropped = queue_ref.get_nowait()
                queue_ref.task_done()
                dropped_text = dropped[0] if isinstance(dropped, tuple) and dropped else dropped
                _runtime.LOGGER.warning(f"Voice command queue full for {session_id}; dropping oldest transcript: {dropped_text!r}")
            except Exception:
                _runtime.LOGGER.warning(f"Voice command queue full for {session_id}; failed to drop oldest item")

        queue_ref.put_nowait((normalized, transcript_already_mirrored, identity))
        _runtime.LOGGER.info(f"Queued voice transcript for {session_id}; pending={queue_ref.qsize()}")

    def _enqueue_voice_command_threadsafe(self, session_id: str, text: str, loop: asyncio.AbstractEventLoop):
        """Thread-safe enqueue for STT callbacks running outside the main event loop."""
        try:
            fut = _runtime.asyncio.run_coroutine_threadsafe(self._enqueue_voice_command(session_id, text), loop)
            def _done(f):
                try:
                    f.result()
                except Exception as e:
                    _runtime.LOGGER.error(f"Failed to enqueue voice transcript for {session_id}: {e}")
            fut.add_done_callback(_done)
        except Exception as e:
            _runtime.LOGGER.error(f"Failed to schedule voice transcript enqueue for {session_id}: {e}")

    async def _stop_voice_command_worker(
        self,
        session_id: str,
        expected_queue: Optional[asyncio.Queue] = None,
        expected_worker: Optional[asyncio.Task] = None,
    ):
        """Stop and cleanup per-session voice queue worker."""
        queue_ref = self.voice_command_queues.get(session_id)
        worker = self.voice_command_workers.get(session_id)

        if expected_queue is not None and queue_ref is not expected_queue:
            queue_ref = None
        elif queue_ref is not None:
            self.voice_command_queues.pop(session_id, None)

        if expected_worker is not None and worker is not expected_worker:
            worker = None
        elif worker is not None:
            self.voice_command_workers.pop(session_id, None)

        if queue_ref is not None:
            try:
                if queue_ref.full():
                    dropped = queue_ref.get_nowait()
                    queue_ref.task_done()
                    _runtime.LOGGER.warning(f"Dropping queued transcript while stopping worker for {session_id}: {dropped!r}")
            except Exception:
                pass
            try:
                queue_ref.put_nowait(None)
            except Exception:
                pass

        if worker is not None and not worker.done():
            try:
                await _runtime.asyncio.wait_for(worker, timeout=2.0)
            except _runtime.asyncio.CancelledError:
                pass
            except Exception:
                worker.cancel()
                try:
                    await worker
                except _runtime.asyncio.CancelledError:
                    pass
                except Exception:
                    pass

    def _rtc_configuration(self) -> Optional[RTCConfiguration]:
        if _runtime.RTCConfiguration is None:
            return None
        try:
            ice_servers_cfg = order_ice_servers_for_aiortc(_runtime._get_pairing_ice_servers())
            servers = []
            for s in ice_servers_cfg:
                urls = s.get('urls') if isinstance(s, dict) else None
                if not urls:
                    continue
                username = s.get('username') if isinstance(s, dict) else None
                credential = s.get('credential') if isinstance(s, dict) else None
                servers.append(_runtime.RTCIceServer(urls=urls, username=username, credential=credential))
            return _runtime.RTCConfiguration(iceServers=servers)
        except Exception as e:
            _runtime.LOGGER.warning(f"Invalid RTC config: {e}")
            return _runtime.RTCConfiguration(iceServers=[])

    def _create_rtc_configuration_with_custom_ice(self, custom_ice_servers: Optional[list] = None) -> Optional[RTCConfiguration]:
        """Create RTC configuration with custom ICE servers if provided, otherwise use default config."""
        if _runtime.RTCConfiguration is None:
            return None
        try:
            # Use custom ICE servers if provided, otherwise fall back to config
            if custom_ice_servers:
                ice_servers_cfg = custom_ice_servers
            else:
                ice_servers_cfg = _runtime._get_pairing_ice_servers()
            # aiortc keeps only the first TURN URI; order them so that pick is reachable.
            ice_servers_cfg = order_ice_servers_for_aiortc(ice_servers_cfg)

            servers = []
            for s in ice_servers_cfg:
                # Allow either dict objects or plain strings
                if isinstance(s, dict):
                    urls = s.get('urls') or s.get('url')
                    if not urls:
                        continue
                    username = s.get('username')
                    credential = s.get('credential')
                    servers.append(_runtime.RTCIceServer(urls=urls, username=username, credential=credential))
                elif isinstance(s, str):
                    ss = s.strip()
                    if not ss:
                        continue
                    # Accept single URL string
                    servers.append(_runtime.RTCIceServer(urls=[ss]))
                else:
                    # Unknown format; skip
                    continue
            return _runtime.RTCConfiguration(iceServers=servers)
        except Exception as e:
            _runtime.LOGGER.warning(f"Invalid RTC config: {e}")
            return _runtime.RTCConfiguration(iceServers=[])

    def _create_datachannel_handler(
        self,
        identifier: str,
        user_prefix: str,
        enable_keepalive: bool = True,
        enable_tunnelmole_cleanup: bool = False,
        expected_pc: Optional[RTCPeerConnection] = None,
        client_session_id_hint: Optional[str] = None,
    ):
        """Create a centralized datachannel handler with optional keepalive and cleanup functionality."""
        def _redact_session_id(value: Any) -> Any:
            return _runtime.redact_identifier(value)

        redacted_identifier = _redact_session_id(identifier)

        def on_datachannel(channel):
            _runtime.LOGGER.info("Data channel opened from %s %s: %s", user_prefix, redacted_identifier, channel.label)
            self._cancel_session_establishment_timeout(
                str(identifier),
                expected_pc=expected_pc,
                reason=f"datachannel:{channel.label}",
            )

            # Keepalive mechanism variables
            keepalive_task = None
            last_pong_time = _runtime.time.time() if enable_keepalive else None
            # Any inbound datachannel traffic (chat, HTTP proxy chunks/ACKs,
            # voice-call control, ...) is equally valid proof the connection
            # is alive. A dedicated PONG can queue for a long time behind a
            # large bulk HTTP stream transfer sharing the same ordered/
            # reliable SCTP channel (e.g. a multi-MB audio file proxied in
            # ~500-byte chunks) even though the peer is actively, correctly
            # ACKing every one of those chunks. Gating liveness on PONG alone
            # produced false "connection may be dead" cleanups on a perfectly
            # healthy local-network connection mid-transfer.
            last_activity_time = _runtime.time.time() if enable_keepalive else None
            channel_open_initialized = False
            # Ping-pong game state embedded in keepalive framing
            _ping_pong_game_metadata: dict = {}
            _last_ping_sent_at: float = 0.0

            def _game_session_key() -> str:
                """Single scorer key for both halves of a rally.

                Client PINGs used to key on ``ping_msg.header.session_id`` while
                PONGs keyed on the bound ``client_session_id``.  Those differ
                during the pre-bind window, so one connection kept two scorers
                and each half of the rally landed in a different game.
                """
                return str(identifier)

            # Keepalive ping function
            async def keepalive_ping():
                # _last_ping_sent_at has to be nonlocal: assigning it without
                # this made it a local of keepalive_ping, so the enclosing copy
                # the PONG handler reads stayed 0.0 and every server-initiated
                # rally was silently dropped from the game.
                nonlocal last_pong_time, last_activity_time, _last_ping_sent_at
                current_session_id = identifier  # Start with the initial identifier

                while channel.readyState == "open":
                    try:
                        current_session_id = self._preferred_datachannel_session_id(
                            current_session_id,
                            datachannel_manager,
                        )

                        # Send ping message using proper DataChannelMessage format
                        ping_header = _runtime.MessageHeader(
                            message_id=str(_runtime.uuid.uuid4()),
                            message_type=_runtime.MessageType.PING,
                            timestamp=_runtime.time.time(),
                            session_id=str(current_session_id),
                            user_id=f"{user_prefix}_{current_session_id}"
                        )
                        payload_data = {
                            "timestamp": _runtime.time.time(),
                            "fallback_text": "📤 Ping Sent / 📥 Pong Received",
                            "location_recording_enabled": _runtime._location_recording_available(cfg=(_runtime.STATE.config or {})),
                        }
                        # Embed ping-pong game metadata if available
                        if _ping_pong_game_metadata:
                            payload_data["game"] = dict(_ping_pong_game_metadata)

                        _last_ping_sent_at = _runtime.time.time()
                        ping_message = _runtime.DataChannelMessage(
                            header=ping_header,
                            payload=payload_data
                        )
                        if datachannel_manager is not None:
                            await datachannel_manager.send_message(ping_message)
                        else:
                            channel.send(ping_message.to_json())
                        _runtime.LOGGER.info(
                            "Sent keepalive ping to %s %s (ping_id: %s)",
                            user_prefix,
                            _redact_session_id(current_session_id),
                            ping_header.message_id,
                        )

                        # Declare dead only when NEITHER a pong NOR any other
                        # inbound message (chat, HTTP proxy data/ACKs, voice
                        # call control, ...) has arrived recently. A pure PONG
                        # check alone false-positives while a legitimate bulk
                        # transfer (e.g. a large HTTP-proxied audio file) is
                        # actively occupying the same ordered channel.
                        idle_timeout = _runtime._datachannel_idle_timeout_seconds()
                        if _runtime.time.time() - last_activity_time > idle_timeout:
                            _runtime.LOGGER.warning(
                                "No pong or other activity received from %s %s for %.0f seconds, connection may be dead. Triggering cleanup.",
                                user_prefix,
                                _redact_session_id(current_session_id),
                                idle_timeout,
                            )
                            self.cleanup_session(str(identifier), expected_pc=expected_pc)
                            break

                        # Wait 30 seconds before next ping
                        await _runtime.asyncio.sleep(30)
                    except Exception as e:
                        _runtime.LOGGER.error(
                            "Keepalive ping failed for %s %s: %s",
                            user_prefix,
                            _redact_session_id(current_session_id),
                            e,
                        )
                        self.cleanup_session(str(identifier), expected_pc=expected_pc)
                        break

            # Schedule tunnelmole service cleanup if enabled
            cleanup_tunnelmole = None
            if enable_tunnelmole_cleanup:
                async def cleanup_tunnelmole():
                    await _runtime.asyncio.sleep(1)  # Brief delay to ensure the channel is stable

                    if _runtime._is_tunnelmole_unmanaged_mode():
                        _runtime.LOGGER.info(
                            "DataChannel open for session %s; keeping tunnelmole alive (unmanaged mode)",
                            redacted_identifier,
                        )
                        return

                    if _runtime._is_otp_multiuse():
                        # Multi-use: keep tunnelmole alive so reconnects and additional
                        # clients can still reach /auth.  The tunnelmole timer manages
                        # the eventual shutdown within the configured timeout window.
                        _runtime.LOGGER.info(
                            "DataChannel open for session %s; keeping tunnelmole alive (multi-use mode)",
                            redacted_identifier,
                        )
                        return

                    _runtime.LOGGER.info(
                        "WebRTC connection established for session %s, initiating tunnelmole cleanup (single-use)",
                        redacted_identifier,
                    )

                    # Stop the tunnelmole timeout timer first to override any existing timeout
                    timer_stopped = await _runtime.stop_tunnelmole_timer()
                    if timer_stopped:
                        _runtime.LOGGER.info("Successfully stopped tunnelmole timer for session %s", redacted_identifier)
                    else:
                        _runtime.LOGGER.warning("Failed to stop tunnelmole timer for session %s", redacted_identifier)

                    # Stop the tunnelmole service; signaling is complete, public URL no longer needed
                    await _runtime.stop_tunnelmole_service(
                        reason=f"WebRTC DataChannel open for session {redacted_identifier}; ending public exposure"
                    )
                    _runtime.LOGGER.info(
                        "Tunnelmole stopped after successful WebRTC connection %s",
                        redacted_identifier,
                    )

                    # NOTE: Do NOT stop the auth server here. The auth/signaling server must
                    # remain running so the iOS app can re-pair after reconnects. Stopping it
                    # causes the client to flood /signal with retries (getting 429s) and can
                    # trigger a datachannel reconnect loop. Auth server lifetime is managed by
                    # the tunnelmole timeout handler and explicit /tunnelmole/stop calls only.

            # Initialize DataChannel Manager if available
            datachannel_manager = None
            if _runtime.DataChannelManager is not None:
                datachannel_manager = _runtime.DataChannelManager(role='server')
                datachannel_manager.set_datachannel(channel)
                try:
                    datachannel_manager.set_session_id(str(identifier))
                except Exception:
                    pass

                def _pong_augmenter(ping_msg: Any) -> dict:
                    """Score a client-initiated ping and return game metadata.

                    The client times its own PING->PONG on a single clock and
                    reports the previous rally's result as ``rtt_ms``.  We use
                    that rather than ``now() - ping.header.timestamp``: that
                    subtracted the phone's wall clock from the server's, so it
                    measured one-way delay plus unbounded NTP skew, and any
                    offset over 5s silently fell back to a fabricated constant.
                    """
                    payload = (getattr(ping_msg, "payload", None) or {})
                    location_enabled = _runtime._location_recording_available(cfg=(_runtime.STATE.config or {}))
                    if type(payload.get("location_sharing_enabled")) is bool:
                        _runtime._record_location_sharing_status(
                            str(identifier), payload["location_sharing_enabled"] and location_enabled
                        )
                    if location_enabled and isinstance(payload.get("location"), dict):
                        _runtime._record_location_ping_sample(payload["location"])
                    reply = {"location_recording_enabled": location_enabled}
                    try:
                        reported_ms = payload.get("rtt_ms")
                        client_rtt = None
                        if reported_ms is not None:
                            try:
                                candidate = float(reported_ms) / 1000.0
                                # Sanity-bound a peer-supplied number before it
                                # reaches the scorer.
                                if 0.0 < candidate <= 30.0:
                                    client_rtt = candidate
                            except (TypeError, ValueError):
                                client_rtt = None
                        if client_rtt is None:
                            # First rally of a session, or an older client that
                            # does not report RTT yet.
                            client_rtt = datachannel_manager._recent_rtt_baseline_seconds()
                        snapshot = self.client_display_name_snapshot(_game_session_key())
                        client_name = snapshot.get("client_display_name")
                        server_name = _runtime.get_configured_server_name()
                        result = game_ping_pong.ping_pong_scorer_manager.process_ping_pong(
                            _game_session_key(), client_rtt, "client",
                            client_name=client_name,
                            server_name=server_name,
                        )
                        _ping_pong_game_metadata.clear()
                        _ping_pong_game_metadata.update(result)
                        reply["game"] = dict(result)
                        return reply
                    except Exception as game_exc:
                        _runtime.LOGGER.warning("PingPong client-ping scoring error: %s", game_exc)
                        return reply

                datachannel_manager.set_pong_payload_augmenter(_pong_augmenter)

                # Initially store with the identifier (chat_id for autopair, session_id for tunnelmole)
                # but we'll re-register with the actual client session_id when we receive the first message
                self.datachannel_managers[identifier] = datachannel_manager
                bound_client_session_ids: Set[str] = set()

                async def _flush_pending_scheduler_notifications(session_id: str) -> None:
                    normalized_session_id = str(session_id or "").strip()
                    if not normalized_session_id:
                        return
                    try:
                        identity = self._resolve_chat_identity(normalized_session_id)
                    except Exception as exc:
                        _runtime.LOGGER.debug(
                            "Skipping queued scheduler notification flush for %s because identity resolution failed: %s",
                            _redact_session_id(normalized_session_id),
                            exc,
                        )
                        return

                    owner_key = str(getattr(identity, "owner_key", "") or "").strip()
                    canonical_user_id = str(getattr(identity, "canonical_user_id", "") or "").strip()
                    if not owner_key and not canonical_user_id:
                        return

                    try:
                        from shared import scheduler_service as _scheduler_svc

                        results = await _scheduler_svc.flush_pending_notifications_for_owner(
                            owner_key=owner_key,
                            canonical_user_id=canonical_user_id,
                        )
                        delivered_count = sum(
                            1
                            for result in results.values()
                            if str(result.get("status") or "").strip() == "success"
                        )
                        if delivered_count:
                            _runtime.LOGGER.info(
                                "Flushed %d queued scheduler notification(s) for session %s (owner=%s)",
                                delivered_count,
                                _redact_session_id(normalized_session_id),
                                _redact_session_id(owner_key or canonical_user_id),
                            )
                    except Exception as exc:
                        _runtime.LOGGER.warning(
                            "Failed to flush queued scheduler notifications for %s: %s",
                            _redact_session_id(normalized_session_id),
                            exc,
                        )

                def _channel_is_sendable() -> bool:
                    ready_state = getattr(channel, "readyState", None)
                    normalized_ready_state = str(ready_state).lower() if ready_state is not None else ""
                    return ready_state is None or normalized_ready_state == "open"

                def _bootstrap_datachannel_session(session_id: Optional[str]) -> None:
                    normalized_session_id = str(session_id or "").strip()
                    if not normalized_session_id or not _channel_is_sendable():
                        return

                    voice_call_status = self.voice_call_status_by_session.get(normalized_session_id)
                    audio_manager_status = self._get_audio_manager_readiness_status(normalized_session_id)
                    stored_voice_state = (
                        str(voice_call_status.get("state") or "").lower()
                        if isinstance(voice_call_status, dict)
                        else ""
                    )
                    live_voice_state = (
                        str(audio_manager_status.get("state") or "").lower()
                        if isinstance(audio_manager_status, dict)
                        else ""
                    )
                    if isinstance(audio_manager_status, dict) and (
                        not isinstance(voice_call_status, dict)
                        or (
                            stored_voice_state == "warming"
                            and live_voice_state != "warming"
                        )
                        or (
                            stored_voice_state == "unavailable"
                            and live_voice_state in {"warming", "ready"}
                        )
                    ):
                        voice_call_status = audio_manager_status
                    if isinstance(voice_call_status, dict):
                        self._track_session_task(
                            normalized_session_id,
                            self._publish_voice_call_status(
                                normalized_session_id,
                                dict(voice_call_status),
                            ),
                            "voice_call_status_bootstrap",
                        )
                    if normalized_session_id in self.voice_call_playback_by_session:
                        self._track_session_task(
                            normalized_session_id,
                            self._publish_voice_call_status(
                                normalized_session_id,
                                dict(self.voice_call_playback_by_session[normalized_session_id]),
                            ),
                            "voice_call_playback_bootstrap",
                        )
                    self._track_session_task(
                        normalized_session_id,
                        self._publish_webrtc_capabilities(normalized_session_id),
                        "webrtc_capabilities_bootstrap",
                    )
                    self._track_session_task(
                        normalized_session_id,
                        self._prime_conversation_context_status(
                            normalized_session_id,
                            _runtime._resolve_conversation_identity(
                                self._resolve_chat_identity(normalized_session_id)
                            ),
                        ),
                        "conversation_context_bootstrap",
                    )
                    self._track_session_task(
                        normalized_session_id,
                        self._flush_pending_voice_chat_messages(normalized_session_id),
                        "voice_chat_flush",
                    )
                    self._track_session_task(
                        normalized_session_id,
                        _flush_pending_scheduler_notifications(normalized_session_id),
                        "scheduler_notification_flush",
                    )

                def _bootstrap_all_bound_datachannel_sessions() -> None:
                    session_ids = {str(identifier)}
                    session_ids.update(bound_client_session_ids)
                    for pending_session_id in session_ids:
                        _bootstrap_datachannel_session(pending_session_id)

                def _initialize_datachannel_open_state() -> None:
                    nonlocal channel_open_initialized, keepalive_task
                    if channel_open_initialized or not _channel_is_sendable():
                        return

                    channel_open_initialized = True
                    _runtime.LOGGER.info(
                        "Data channel fully opened for %s %s; bootstrapping keepalive and state replay",
                        user_prefix,
                        redacted_identifier,
                    )
                    if enable_keepalive:
                        keepalive_task = _runtime.track_background_task(keepalive_ping())
                    if enable_tunnelmole_cleanup and cleanup_tunnelmole is not None:
                        _runtime.asyncio.create_task(cleanup_tunnelmole())
                    _bootstrap_all_bound_datachannel_sessions()

                def bind_client_session_id(client_session_id: Optional[str]) -> bool:
                    normalized_client_session_id = str(client_session_id or "").strip()
                    if not normalized_client_session_id:
                        return False
                    if (
                        normalized_client_session_id == str(identifier)
                        or normalized_client_session_id in bound_client_session_ids
                    ):
                        return True
                    if bound_client_session_ids:
                        return False

                    existing_mgr = self.datachannel_managers.get(normalized_client_session_id)
                    if existing_mgr is not datachannel_manager:
                        if existing_mgr:
                            if not self._retire_inactive_datachannel_manager(existing_mgr):
                                _runtime.LOGGER.warning(
                                    "Rejected datachannel session rebind %s -> %s: session is owned by another channel",
                                    redacted_identifier,
                                    _redact_session_id(normalized_client_session_id),
                                )
                                return False
                            _runtime.LOGGER.info(
                                "Replacing inactive datachannel manager for session %s",
                                _redact_session_id(normalized_client_session_id),
                            )
                        _runtime.LOGGER.info(
                            "Re-registering datachannel manager: %s -> %s (%s)",
                            redacted_identifier,
                            _redact_session_id(normalized_client_session_id),
                            user_prefix,
                        )
                        self.datachannel_managers[normalized_client_session_id] = datachannel_manager

                    bound_client_session_ids.add(normalized_client_session_id)

                    if identifier in self.voice_call_status_by_session:
                        self.voice_call_status_by_session[normalized_client_session_id] = dict(
                            self.voice_call_status_by_session[identifier]
                        )
                    if identifier in self.voice_call_playback_by_session:
                        self.voice_call_playback_by_session[normalized_client_session_id] = dict(
                            self.voice_call_playback_by_session[identifier]
                        )
                    if identifier in self.voice_call_client_active_by_session:
                        self.voice_call_client_active_by_session[normalized_client_session_id] = bool(
                            self.voice_call_client_active_by_session[identifier]
                        )
                    elif normalized_client_session_id in self.voice_call_client_active_by_session:
                        self.voice_call_client_active_by_session[identifier] = bool(
                            self.voice_call_client_active_by_session[normalized_client_session_id]
                        )
                    try:
                        datachannel_manager.set_session_id(str(normalized_client_session_id))
                    except Exception:
                        pass
                    _old_id = str(identifier)
                    _new_id = str(normalized_client_session_id)
                    _audio_mgr = _runtime.STATE.audio_managers.get(_old_id)
                    if _audio_mgr is not None:
                        stale_audio_manager = _runtime.STATE.audio_managers.get(_new_id)
                        if stale_audio_manager is not None and stale_audio_manager is not _audio_mgr:
                            stale_aliases = self._purge_audio_manager_aliases(
                                stale_audio_manager,
                                close_manager=True,
                            )
                            if stale_aliases:
                                _runtime.LOGGER.info(
                                    "Removed stale audio manager aliases before rebinding %s -> %s: %s",
                                    _redact_session_id(_old_id),
                                    _redact_session_id(_new_id),
                                    _redact_session_id(sorted(stale_aliases)),
                                )
                        _runtime.STATE.audio_managers[_new_id] = _audio_mgr
                        if hasattr(_audio_mgr, "status_callback") and _audio_mgr.status_callback is not None:
                            try:
                                _audio_mgr.status_callback = self._make_voice_call_status_callback(
                                    _new_id, _runtime.asyncio.get_running_loop()
                                )
                            except Exception:
                                pass
                    _video_sink = self.video_sinks.get(_old_id)
                    if _video_sink is not None:
                        self._register_video_sink_aliases(_old_id, _video_sink)
                    _desktop_track = self.desktop_video_tracks.get(_old_id)
                    if _desktop_track is not None:
                        self._register_desktop_video_track_aliases(_old_id, _desktop_track)
                    try:
                        _runtime.alias_webrtc_chat_session(
                            str(identifier),
                            str(normalized_client_session_id),
                        )
                    except Exception as alias_err:
                        _runtime.LOGGER.debug(
                            "Failed to alias WebRTC session identity %s -> %s: %s",
                            redacted_identifier,
                            _redact_session_id(normalized_client_session_id),
                            alias_err,
                        )
                    self._voice_dc_session_id[str(identifier)] = str(normalized_client_session_id)
                    for pending_session_id in {str(identifier), str(normalized_client_session_id)}:
                        _bootstrap_datachannel_session(pending_session_id)
                    return True

                hinted_client_session_id = str(client_session_id_hint or "").strip()
                if hinted_client_session_id:
                    bind_client_session_id(hinted_client_session_id)

                @channel.on("open")
                def on_channel_open():
                    _initialize_datachannel_open_state()

                # Create a unified message handler that re-registers the datachannel manager
                # with the client's session_id on first message
                async def unified_message_handler(message):
                    nonlocal last_pong_time
                    client_session_id = message.header.session_id

                    _runtime.LOGGER.debug(
                        "Unified handler received message type: %s from %s",
                        message.header.message_type.value,
                        _redact_session_id(client_session_id),
                    )

                    # Room bridge authority is derived from this handler's
                    # captured, authenticated transport. Never bind or trust a
                    # client-supplied header session_id before authorizing it.
                    if message.header.message_type == _runtime.MessageType.PAIRING_CONTROL:
                        self._track_session_task(str(identifier),
                            self._handle_live_pair_control(message, str(identifier), datachannel_manager),
                            "live_pairing")
                        return
                    if message.header.message_type == _runtime.MessageType.ROOM_BRIDGE_CONTROL:
                        self._track_session_task(
                            str(identifier),
                            self._handle_room_bridge_control(
                                message,
                                trusted_transport_id=str(identifier),
                                datachannel_manager=datachannel_manager,
                            ),
                            "room_bridge_control",
                        )
                        return
                    if message.header.message_type == _runtime.MessageType.CHAT:
                        candidate_payload = message.payload if isinstance(message.payload, dict) else {}
                        candidate_metadata = candidate_payload.get("metadata")
                        if isinstance(candidate_metadata, dict) and isinstance(
                            candidate_metadata.get("room_bridge"),
                            dict,
                        ):
                            self._track_session_task(
                                str(identifier),
                                self._handle_room_bridge_chat(
                                    message,
                                    trusted_transport_id=str(identifier),
                                    datachannel_manager=datachannel_manager,
                                ),
                                "room_bridge_chat",
                            )
                            return

                    if client_session_id and client_session_id != identifier:
                        if not bind_client_session_id(client_session_id):
                            _runtime.LOGGER.warning(
                                "Ignoring message with unbound or conflicting session ID %s from %s",
                                _redact_session_id(client_session_id),
                                redacted_identifier,
                            )
                            return

                    # Handle PONG messages to update keepalive tracking
                    if message.header.message_type == _runtime.MessageType.PONG:
                        if enable_keepalive:
                            last_pong_time = _runtime.time.time()
                            ping_id = message.payload.get('ping_id', 'unknown')
                            _runtime.LOGGER.info(
                                "Received pong from %s %s for ping: %s",
                                user_prefix,
                                _redact_session_id(client_session_id),
                                ping_id,
                            )
                            # Score the server-initiated rally.  This RTT is
                            # genuine: both endpoints of the measurement are
                            # this process's own clock.
                            if _last_ping_sent_at > 0:
                                rtt = _runtime.time.time() - _last_ping_sent_at
                                try:
                                    snapshot = self.client_display_name_snapshot(_game_session_key())
                                    client_name = snapshot.get("client_display_name")
                                    server_name = _runtime.get_configured_server_name()
                                    _ping_pong_game_metadata.clear()
                                    _ping_pong_game_metadata.update(
                                        game_ping_pong.ping_pong_scorer_manager.process_ping_pong(
                                            _game_session_key(), rtt, "server",
                                            client_name=client_name,
                                            server_name=server_name,
                                        )
                                    )
                                    # Also record this sample in datachannel_manager
                                    if datachannel_manager is not None:
                                        datachannel_manager._record_rtt_sample(rtt)
                                except Exception as game_exc:
                                    _runtime.LOGGER.warning("PingPong game scoring error: %s", game_exc)
                        return
                    if message.header.message_type == _runtime.MessageType.PING:
                        _runtime.LOGGER.info(
                            "Received ping from %s %s: %s",
                            user_prefix,
                            _redact_session_id(client_session_id),
                            message.header.message_id,
                        )
                        return

                    # Route to appropriate handler based on message type
                    if message.header.message_type == _runtime.MessageType.CHAT:
                        self._track_session_task(
                            str(client_session_id or identifier),
                            self._handle_chat_message(message),
                            "chat",
                            survive_disconnect=True,
                        )
                    elif message.header.message_type == _runtime.MessageType.HTTP_REQUEST_CANCEL:
                      self._track_session_task(
                        str(client_session_id or identifier),
                        self._handle_http_request_cancel(message),
                        "http_request_cancel",
                      )
                    elif message.header.message_type == _runtime.MessageType.HTTP_STREAM_ABORT:
                      self._track_session_task(
                        str(client_session_id or identifier),
                        self._handle_http_stream_abort(message),
                        "http_stream_abort",
                      )

                    elif message.header.message_type == _runtime.MessageType.HTTP_REQUEST:
                      request_task = self._track_session_task(
                        str(client_session_id or identifier),
                        self._handle_http_request(message),
                        "http_request",
                      )
                      self._remember_http_proxy_request_task(
                        str(client_session_id or identifier),
                        str((message.payload or {}).get("request_id") or message.header.message_id or ""),
                        request_task,
                      )
                    elif message.header.message_type == _runtime.MessageType.VOICE_CALL_CONTROL:
                        self._track_session_task(
                            str(client_session_id or identifier),
                            self._handle_voice_call_control_message(
                                message,
                                trusted_session_id=str(identifier),
                            ),
                            "voice_call_control",
                        )
                    elif message.header.message_type == _runtime.MessageType.HTTP_WS_DATA:
                        self._track_session_task(str(client_session_id or identifier), self._handle_ws_data_from_client(message), "http_ws_data")
                    elif message.header.message_type == _runtime.MessageType.HTTP_WS_CLOSE:
                        self._track_session_task(str(client_session_id or identifier), self._handle_ws_close_from_client(message), "http_ws_close")
                    elif message.header.message_type == _runtime.MessageType.ERROR:
                        await self._handle_error_message(message)

                # Register the unified message handler for all message types including PONG
                datachannel_manager.register_handler(_runtime.MessageType.CHAT, unified_message_handler)
                datachannel_manager.register_handler(_runtime.MessageType.HTTP_REQUEST, unified_message_handler)
                datachannel_manager.register_handler(_runtime.MessageType.HTTP_REQUEST_CANCEL, unified_message_handler)
                datachannel_manager.register_handler(_runtime.MessageType.HTTP_STREAM_ABORT, unified_message_handler)
                datachannel_manager.register_handler(_runtime.MessageType.ERROR, unified_message_handler)
                datachannel_manager.register_handler(_runtime.MessageType.PONG, unified_message_handler)
                datachannel_manager.register_handler(_runtime.MessageType.PING, unified_message_handler)
                datachannel_manager.register_handler(_runtime.MessageType.VOICE_CALL_CONTROL, unified_message_handler)
                datachannel_manager.register_handler(_runtime.MessageType.ROOM_BRIDGE_CONTROL, unified_message_handler)
                datachannel_manager.register_handler(_runtime.MessageType.PAIRING_CONTROL, unified_message_handler)
                # Observe CHUNK_ACK for reliability diagnostics
                datachannel_manager.register_handler(_runtime.MessageType.CHUNK_ACK, unified_message_handler)
                # Register WebSocket proxy handlers
                datachannel_manager.register_handler(_runtime.MessageType.HTTP_WS_DATA, unified_message_handler)
                datachannel_manager.register_handler(_runtime.MessageType.HTTP_WS_CLOSE, unified_message_handler)

                # Start periodic tasks (ping/keepalive)
                # Note: Redundant start_periodic_tasks() ping loop is skipped here to avoid duplicate keepalives.
                # The keepalive_ping() loop handles the periodic pings and connection dead checks.

                _initialize_datachannel_open_state()

            @channel.on("close")
            def on_channel_close():
                nonlocal keepalive_task, datachannel_manager
                _runtime.LOGGER.info("Data channel closed for %s %s", user_prefix, redacted_identifier)
                if keepalive_task:
                    keepalive_task.cancel()
                self._suspend_room_bridge_transport(str(identifier))
                self.live_pairing.cancel(str(identifier))
                if datachannel_manager:
                    datachannel_manager.disconnect()
                for sid, mgr in list(self.datachannel_managers.items()):
                    if mgr is datachannel_manager:
                        del self.datachannel_managers[sid]
                self.cleanup_session(str(identifier), expected_pc=expected_pc)

            @channel.on("message")
            async def on_message(message: str):
                nonlocal last_pong_time, last_activity_time, datachannel_manager
                if enable_keepalive:
                    # Any successfully-received frame (chat, HTTP proxy
                    # chunk/ACK, voice-call control, chunk fragment, ...) is
                    # unambiguous proof this connection round-trips fine right
                    # now. Counting only PONG here previously let a legitimate,
                    # heavy bulk transfer (which itself proves liveness via a
                    # continuous stream of CHUNK_ACKs) starve the dedicated
                    # keepalive PONG behind it on the same ordered channel and
                    # trigger a false "connection may be dead" cleanup.
                    last_activity_time = _runtime.time.time()
                try:
                    # Use DataChannelManager if available
                    if datachannel_manager is not None:
                        processed_message = await datachannel_manager.handle_received_message(message)
                        if processed_message:
                            _runtime.LOGGER.debug(f"Processed message via DataChannelManager: {processed_message.header.message_type.value}")
                        return

                    # Fallback to legacy message handling if DataChannelManager not available
                    _runtime.LOGGER.warning("DataChannelManager not available, using legacy message handling")

                    # Check if this is a keepalive message (only if keepalive is enabled)
                    try:
                        msg_data = _runtime.json.loads(message)
                        if isinstance(msg_data, dict):
                            if msg_data.get("type") == "pong":
                                last_pong_time = _runtime.time.time()
                                _runtime.LOGGER.debug("Received keepalive pong from %s %s", user_prefix, redacted_identifier)
                                return
                            elif msg_data.get("type") == "ping":
                                # Respond to client ping with pong using proper DataChannelMessage format
                                pong_header = _runtime.MessageHeader(
                                    message_id=str(_runtime.uuid.uuid4()),
                                    message_type=_runtime.MessageType.PONG,
                                    timestamp=_runtime.time.time(),
                                    session_id=str(identifier),
                                    user_id=f"{user_prefix}_{identifier}"
                                )
                                payload_data = {
                                    "timestamp": _runtime.time.time(),
                                    "fallback_text": "📥 Ping Received / 📤 Pong Sent",
                                    "location_recording_enabled": _runtime._location_recording_available(cfg=(_runtime.STATE.config or {})),
                                }

                                pong_message = _runtime.DataChannelMessage(
                                    header=pong_header,
                                    payload=payload_data
                                )
                                pong_msg = pong_message.to_json()
                                channel.send(pong_msg)
                                _runtime.LOGGER.debug("Sent keepalive pong to %s %s", user_prefix, redacted_identifier)
                                return
                    except (_runtime.json.JSONDecodeError, TypeError):
                        # Not a JSON message, treat as regular chat message
                        pass

                    # Forward regular messages to our public chat API
                    user_id = f"{user_prefix}_{identifier}"
                    payload = {
                        "message": str(message),
                        "user_id": user_id,
                        "session_id": str(identifier),
                        "metadata": {"client": "autoyou-chat"},
                    }
                    url = f"http://127.0.0.1:{_runtime.STATE.main_server_port}/api/chat"
                    async with _runtime.httpx.AsyncClient(timeout=60.0) as client:
                        resp = await client.post(url, json=payload)
                        data = resp.json()
                    reply = data.get("response", "(no response)")

                    # Send chat response using proper DataChannelMessage format
                    chat_header = _runtime.MessageHeader(
                        message_id=str(_runtime.uuid.uuid4()),
                        message_type=_runtime.MessageType.CHAT,
                        timestamp=_runtime.time.time(),
                        session_id=str(identifier),
                        user_id=f"{user_prefix}_{identifier}"
                    )
                    chat_message = _runtime.DataChannelMessage(
                        header=chat_header,
                        payload={"message": str(reply)}
                    )
                    chat_msg = chat_message.to_json()
                    channel.send(chat_msg)
                except Exception as e:
                    _runtime.LOGGER.error(
                        "Failed to process datachannel message for %s %s: %s",
                        user_prefix,
                        redacted_identifier,
                        e,
                    )

        return on_datachannel

    async def handle_autopair_offer(self, chat_id: str, offer_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle AutoPair offers; Cloud Pair returns early and trickles ICE separately."""
        redacted_chat_id = _runtime.redact_identifier(chat_id)
        _runtime.LOGGER.info(
            "handle_autopair_offer received for %s. Payload keys: %s",
            redacted_chat_id,
            list(offer_data.keys()),
        )
        if _runtime.RTCPeerConnection is None:
            raise RuntimeError("aiortc not available")

        client_session_id_hint: Optional[str] = None
        identity = None
        source_platform = str(offer_data.get("_autoyou_pairing_platform") or "").strip().lower()
        source_pairing_mode = _runtime._resolve_pairing_mode_for_transport(
            source_platform,
            offer_data.get("_autoyou_pairing_mode"),
        )
        client_display_name = _runtime._client_display_name_from_transport(
            offer_data.get("client_display_name")
        )
        # Set by the pairing entry point from how this device paired; a value a
        # client put in its own offer is always overwritten there.
        device_ownership = normalize_device_ownership(offer_data.get("_autoyou_device_ownership"))
        try:
            source_sender_id = str(offer_data.get("_autoyou_sender_id") or chat_id).strip()
            client_session_id_hint = source_sender_id if source_sender_id and source_sender_id != str(chat_id) else None
            if source_platform and source_sender_id:
                identity = _runtime.bind_transport_chat_owner(
                    source_platform,
                    source_sender_id,
                    raw_session_id=str(chat_id),
                    pairing_mode=source_pairing_mode,
                )
                _runtime.LOGGER.info(
                    "Bound AutoPair session %s to transport owner %s",
                    redacted_chat_id,
                    _runtime.redact_identifier(identity.owner_key),
                )
                self.remember_client_display_name(identity, client_display_name)
                self.remember_device_ownership(identity, device_ownership)
        except Exception as bind_err:
            _runtime.LOGGER.warning("Failed to bind AutoPair session identity for %s: %s", redacted_chat_id, bind_err)

        replacing_session = await self._cleanup_replacement_sessions(
            str(chat_id),
            client_session_id_hint=client_session_id_hint,
            label="autopair",
            clear_client_status_hint=True,
        )
        if offer_data.get("_autoyou_same_machine_audio") is True:
            self.same_machine_audio_sessions.add(str(chat_id))

        cfg_snapshot = _runtime.STATE.config or {}
        audio_call_enabled = _runtime._get_video_call_audio_enabled(cfg=cfg_snapshot)
        agents_disabled = _runtime._get_video_call_agents_disabled(cfg=cfg_snapshot)
        video_call_enabled = _runtime._get_video_call_enabled(cfg=cfg_snapshot)
        offer_for_background = offer_data.get("offer")
        offer_sdp_for_background = (
            str(offer_for_background.get("sdp") or "")
            if isinstance(offer_for_background, dict)
            else str(offer_data.get("sdp") or "")
        )
        background_offer_state = self._background_audio_offer_state(
            offer_data,
            offer_sdp_for_background,
            cfg=cfg_snapshot,
        )
        offer_has_audio = self._sdp_has_media_section(offer_sdp_for_background, "audio")
        if background_offer_state.get("active"):
            self._set_background_audio_state(
                str(chat_id),
                active=True,
                silent_recording=bool(background_offer_state.get("silent_recording")),
                muted=bool(background_offer_state.get("muted", True)),
                platform=str(background_offer_state.get("platform") or "unknown"),
                timestamp_ms=int(_runtime.time.time() * 1000),
            )
        voice_pipeline_enabled = bool(
            _runtime.AudioManager
            and audio_call_enabled
            and not agents_disabled
            and offer_has_audio
            and not background_offer_state.get("active")
        )

        # Initialize variables for closure capture
        tts_track = None
        playback_track = None
        audio_manager = None

        # Extract custom ICE servers if provided
        custom_ice_servers = offer_data.get("iceServers")
        if offer_data.get("_autoyou_loopback_pairing") or _runtime._env_flag_enabled("AUTOYOU_ENABLE_LOOPBACK_ICE"):
            _runtime._install_loopback_ice_candidates()
        configure_sctp_fragment_size()
        _runtime._apply_ice_consent_tolerance()
        await prime_turn_udp_probe(custom_ice_servers or _runtime._get_pairing_ice_servers())
        pc = _runtime.RTCPeerConnection(self._create_rtc_configuration_with_custom_ice(custom_ice_servers))

        if source_platform == "cloud":
            self.outgoing_trickle_candidates[str(chat_id)] = []

            @pc.on("icecandidate")
            def _on_autopair_local_icecandidate(candidate):
                if candidate is None:
                    return
                try:
                    self.queue_outgoing_trickle_candidate(str(chat_id), candidate)
                except Exception as exc:
                    _runtime.LOGGER.warning("Failed to queue cloud AutoPair ICE candidate for %s: %s", redacted_chat_id, exc)

        # Use centralized datachannel handler
        pc.on(
            "datachannel",
            self._create_datachannel_handler(
                chat_id,
                "autopair",
                enable_keepalive=True,
                enable_tunnelmole_cleanup=False,
                expected_pc=pc,
                client_session_id_hint=client_session_id_hint,
            ),
        )

        # MUST capture main native event loop because STT runs in a background thread
        main_loop = _runtime.asyncio.get_running_loop()
        voice_status_callback = self._make_voice_call_status_callback(str(chat_id), main_loop)

        if voice_pipeline_enabled or (
            background_offer_state.get("active") and audio_call_enabled and offer_has_audio
        ):
            def on_text(text):
                _runtime.LOGGER.info("STT Text from %s: %s", redacted_chat_id, text)
                self._enqueue_voice_command_threadsafe(chat_id, text, main_loop)

            def ensure_audio_manager(
                preferred_manager: Optional[Any] = None,
                *,
                force_new: bool = False,
            ) -> Any:
                manager = preferred_manager
                if manager is None:
                    manager = _runtime.STATE.audio_managers.get(chat_id)

                if not force_new and manager is not None and not getattr(manager, "_closed", False):
                    if hasattr(manager, "status_callback"):
                        manager.status_callback = voice_status_callback
                    _runtime.STATE.audio_managers[chat_id] = manager
                    return manager

                if manager is not None and getattr(manager, "_closed", False):
                    stale_aliases = self._purge_audio_manager_aliases(manager)
                    if stale_aliases:
                        _runtime.LOGGER.info(
                            "Discarded closed audio manager aliases for %s before reinitializing voice: %s",
                            redacted_chat_id,
                            _runtime.redact_identifier(sorted(stale_aliases)),
                        )
                elif force_new and manager is not None:
                    _runtime.LOGGER.info("Allocating a fresh audio manager for replacement session %s", redacted_chat_id)

                manager = _runtime.AudioManager(
                    on_text,
                    settings_provider=_runtime._speech_config,
                    status_callback=voice_status_callback,
                )
                manager.conversation_context_provider = self._voice_training_conversation_provider(chat_id)
                _runtime.STATE.audio_managers[chat_id] = manager
                if (
                    self.wuift_hold_by_session.get(str(chat_id))
                    and _runtime._get_wuift_enabled()
                    and hasattr(manager, "set_segmentation_hold")
                ):
                    manager.set_segmentation_hold(True, source=f"reconnect:{chat_id}")
                return manager

            existing_audio_manager = _runtime.STATE.audio_managers.get(chat_id)
            audio_manager = ensure_audio_manager(
                existing_audio_manager,
                force_new=replacing_session and existing_audio_manager is not None,
            )
            # Capture audio_manager reference into closure NOW, before registering
            # on_track and before any await.  If a concurrent stale cleanup evicts
            # the manager from STATE between here and setRemoteDescription, on_track
            # can still fall back to this captured reference instead of crashing.
            audio_manager = _runtime.STATE.audio_managers.get(chat_id) or audio_manager
            tts_track, playback_track = _runtime._ensure_audio_manager_outbound_tracks(
                audio_manager=audio_manager,
                cfg=cfg_snapshot,
            )
        else:
            if background_offer_state.get("active"):
                self._track_session_task(
                    str(chat_id),
                    self._publish_voice_call_status(
                        str(chat_id),
                        {
                            "event": "readiness",
                            "state": "ready",
                            "detail": "Phone background connection is active.",
                            "timestamp_ms": int(_runtime.time.time() * 1000),
                        },
                    ),
                    "voice_call_status_ready_background_audio",
                )
            elif audio_call_enabled and agents_disabled:
                self._track_session_task(
                    str(chat_id),
                    self._publish_voice_call_status(
                        str(chat_id),
                        {
                            "event": "readiness",
                            "state": "ready",
                            "detail": "AutoYou Agents are disabled for video calls.",
                            "timestamp_ms": int(_runtime.time.time() * 1000),
                        },
                    ),
                    "voice_call_status_ready_agents_disabled",
                )
            else:
                unavailable_detail = (
                    "Voice audio is disabled in server video-call settings."
                    if not _runtime._get_video_call_audio_enabled(cfg=cfg_snapshot)
                    else "This client connected without voice-call audio."
                    if not offer_has_audio
                    else "Voice pipeline unavailable on server."
                )
                self._track_session_task(
                    str(chat_id),
                    self._publish_voice_call_status(
                        str(chat_id),
                        {
                            "state": "unavailable",
                            "detail": unavailable_detail,
                        },
                    ),
                    "voice_call_status_unavailable",
                )

        @pc.on("track")
        def on_track(track):
            if track.kind == "audio":
                _runtime.LOGGER.info("Audio track received for %s", redacted_chat_id)
                self._cancel_session_establishment_timeout(
                    str(chat_id),
                    expected_pc=pc,
                    reason="audio_track",
                )
                if audio_call_enabled:
                    nonlocal audio_manager
                    if voice_pipeline_enabled:
                        # Prefer the live STATE entry, fall back to the pre-captured
                        # reference in case a concurrent cleanup evicted it from STATE.
                        mgr = _runtime.STATE.audio_managers.get(chat_id) or audio_manager
                        if mgr is None or getattr(mgr, "_closed", False):
                            _runtime.LOGGER.warning(
                                "Audio manager for %s was unavailable during on_track; reinitializing voice pipeline.",
                                redacted_chat_id,
                            )
                            mgr = ensure_audio_manager(mgr, force_new=True)
                        audio_manager = mgr
                    # Re-register both lanes on whichever manager is live now. A
                    # Background Mode offer leaves voice_pipeline_enabled False,
                    # but the manager still needs its own media lane so agent
                    # playback never lands on the speech lane (where VAD barge-in
                    # would cancel it).
                    if audio_manager is not None:
                        if tts_track:
                            audio_manager.set_tts_track(tts_track)
                        if playback_track and hasattr(audio_manager, "set_playback_track"):
                            audio_manager.set_playback_track(playback_track)

                    if not self._start_inbound_audio_track_sink(
                        track,
                        session_id=str(chat_id),
                        audio_manager=audio_manager,
                        audio_call_enabled=audio_call_enabled,
                        cfg_snapshot=cfg_snapshot,
                    ):
                        _runtime.LOGGER.info("Audio track received for %s but no audio consumer is configured", redacted_chat_id)
                else:
                    _runtime.LOGGER.info("Audio disabled, agents disabled, or AudioManager not available; ignoring audio track processing")
            elif track.kind == "video":
                _runtime.LOGGER.info("Video track received for %s", redacted_chat_id)
                self._cancel_session_establishment_timeout(
                    str(chat_id),
                    expected_pc=pc,
                    reason="video_track",
                )
                if not video_call_enabled:
                    _runtime.LOGGER.info("Video calls disabled in server settings; ignoring inbound video for %s", redacted_chat_id)
                    return
                if _runtime.IncomingVideoTrackSink is not None:
                    sink = _runtime.IncomingVideoTrackSink(
                        track,
                        session_id=str(chat_id),
                        recording_enabled=_runtime._get_video_record_my_video_enabled(cfg=(_runtime.STATE.config or {})),
                        recording_dir=_runtime._resolve_video_recording_dir(cfg=(_runtime.STATE.config or {})),
                        recording_mode=_runtime._get_video_recording_mode(cfg=(_runtime.STATE.config or {})),
                        image_interval_seconds=_runtime._get_video_image_interval_seconds(cfg=(_runtime.STATE.config or {})),
                    )
                    self._register_video_sink_aliases(str(chat_id), sink)
                    _runtime.track_background_task(sink.start())
                else:
                    _runtime.LOGGER.warning("Video sink unavailable; ignoring video track for %s", redacted_chat_id)

        @pc.on("iceconnectionstatechange")
        def _on_ice_state_change():
            state = pc.iceConnectionState
            _runtime.LOGGER.info("ICE state for %s: %s", redacted_chat_id, state)
            try:
                self._handle_transport_disconnect_state(
                    chat_id,
                    pc,
                    source="ice",
                    state=state,
                )
            except Exception as e:
                _runtime.LOGGER.error("Cleanup error for %s on ICE state '%s': %s", redacted_chat_id, state, e)

        @pc.on("connectionstatechange")
        def _on_connection_state_change():
            state = getattr(pc, "connectionState", None)
            _runtime.LOGGER.info("Peer connection state for %s: %s", redacted_chat_id, state)
            try:
                self._handle_transport_disconnect_state(
                    chat_id,
                    pc,
                    source="peer_connection",
                    state=state,
                )
            except Exception as e:
                _runtime.LOGGER.error("Cleanup error for %s on connection state '%s': %s", redacted_chat_id, state, e)

        # Extract SDP from offer data (nested in "offer" object)
        offer = offer_data.get("offer")
        if isinstance(offer, dict) and "sdp" in offer:
            sdp_string = offer["sdp"]
            offer_type = offer.get("type", "offer")
        else:
            raise ValueError(f"Invalid autopair offer format: missing 'offer.sdp' field")

        self._prefer_remote_desktop_video_codec(pc, sdp_string)
        await pc.setRemoteDescription(_runtime.RTCSessionDescription(sdp=sdp_string, type=offer_type))

        # Log transceivers for debugging
        transceivers = pc.getTransceivers()
        _runtime.LOGGER.info("Negotiated transceivers for %s: %s", redacted_chat_id, [t.kind for t in transceivers])

        # Attach configured outbound audio track (TTS or local capture) to audio transceiver if available
        audio_transceiver = next((t for t in transceivers if t.kind == "audio"), None)
        if audio_transceiver:
            self._register_audio_transceiver_aliases(str(chat_id), audio_transceiver)
            if background_offer_state.get("active"):
                audio_transceiver.direction = str(background_offer_state.get("server_audio_direction") or "inactive")
                if (
                    audio_transceiver.direction in {"sendonly", "sendrecv"}
                    and not background_offer_state.get("silent_recording")
                ):
                    self._attach_background_audio_heartbeat_track(str(chat_id), audio_transceiver)
                _runtime.LOGGER.info(
                    "Suppressed outbound voice/mixer audio for background session %s; server audio direction=%s silent_recording=%s heartbeat_track=%s",
                    redacted_chat_id,
                    audio_transceiver.direction,
                    background_offer_state.get("silent_recording"),
                    getattr(getattr(audio_transceiver, "sender", None), "track", None) is not None,
                )
            else:
                effective_audio_track = _runtime._create_configured_outbound_audio_track(
                    cfg=cfg_snapshot,
                    session_id=str(chat_id),
                    tts_track=tts_track,
                    playback_track=playback_track,
                    include_loopback=False,
                )
                if effective_audio_track:
                    audio_transceiver.sender.replaceTrack(effective_audio_track)
                    audio_transceiver.direction = "sendrecv"
                    _runtime.LOGGER.info(
                        "Attached outbound audio track (%s) and promoted transceiver to sendrecv for %s",
                        type(effective_audio_track).__name__,
                        redacted_chat_id,
                    )
                else:
                    _runtime.LOGGER.info("No outbound audio track attached for %s", redacted_chat_id)
        else:
            _runtime.LOGGER.warning("No audio transceiver found in offer for %s; voice support disabled", redacted_chat_id)

        video_transceiver = next((t for t in transceivers if t.kind == "video"), None)
        if video_transceiver:
            if _runtime._get_outbound_video_available(cfg=(_runtime.STATE.config or {})):
                # Keep pairing independent of optional desktop-capture setup.  The
                # negotiated sender is populated on video_state(active), before any
                # frames can be sent.
                video_transceiver.direction = "sendrecv"
                _runtime.LOGGER.info(
                    "Deferred outbound video source %s for %s until video_state(active)",
                    _runtime._get_video_outbound_source(cfg=(_runtime.STATE.config or {})),
                    redacted_chat_id,
                )
            else:
                _runtime.LOGGER.info(
                    "Outbound video not attached for %s; capabilities=%s",
                    redacted_chat_id,
                    _runtime._build_webrtc_capabilities(cfg=(_runtime.STATE.config or {})).get("outbound_video", {}),
                )
        else:
            _runtime.LOGGER.info("No video transceiver found in offer for %s; video call feed disabled", redacted_chat_id)

        # Create and set local answer
        try:
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
        except ValueError as ve:
            if "None is not in list" in str(ve):
                _runtime.LOGGER.error("aiortc transceiver bug hit during setLocalDescription for %s: %s", redacted_chat_id, ve)
                # Fallback: close this PC and suggest a DataChannel-only retry if possible?
                # For now, just re-raise so pairing_router sees it, but with more info.
                raise ValueError(f"WebRTC negotiation failure (transceiver mismatch): {ve}")
            raise

        if source_platform == "cloud":
            deadline = _runtime.asyncio.get_event_loop().time() + 0.5
            while pc.iceGatheringState != 'complete' and _runtime.asyncio.get_event_loop().time() < deadline:
                await _runtime.asyncio.sleep(0.05)
        else:
            # Wait for ICE gathering to complete (ONE-SHOT gathering)
            async def _gather_complete():
                while pc.iceGatheringState != 'complete':
                    await _runtime.asyncio.sleep(0.1)
            await _gather_complete()

        # Store in session_peers (unified with sessions)
        self.session_peers[chat_id] = pc

        # Apply any candidates provided in the autopair offering
        candidates = offer_data.get("candidates", [])
        marked_remote_candidates_complete = False
        if candidates and _runtime.RTCIceCandidate:
            _runtime.LOGGER.info("Applying %d candidates from autopair payload for %s", len(candidates), redacted_chat_id)
            for c in candidates:
                try:
                    # Robust parsing using candidate_from_sdp
                    cand_str = c.get("candidate")
                    if cand_str and _runtime.candidate_from_sdp:
                        # Some clients send "candidate:..." some just the part
                        if cand_str.startswith("candidate:"):
                            cand_line = cand_str
                        else:
                            cand_line = f"candidate:{cand_str}"

                        candidate_obj = _runtime.candidate_from_sdp(cand_line)
                        candidate_obj.sdpMid = c.get("sdpMid")
                        candidate_obj.sdpMLineIndex = c.get("sdpMLineIndex")
                        await pc.addIceCandidate(candidate_obj)
                except Exception as ce:
                    _runtime.LOGGER.warning(f"Failed to add ICE candidate from payload: {ce}")
            if source_platform != "cloud":
                try:
                    await pc.addIceCandidate(None)
                    marked_remote_candidates_complete = True
                    _runtime.LOGGER.info("Marked remote one-shot ICE candidates complete for %s", redacted_chat_id)
                except Exception as ce:
                    _runtime.LOGGER.warning("Failed to mark remote ICE candidates complete for %s: %s", redacted_chat_id, ce)
        if source_platform != "cloud" and not marked_remote_candidates_complete:
            try:
                await pc.addIceCandidate(None)
                _runtime.LOGGER.info("Marked remote one-shot ICE candidates complete for %s", redacted_chat_id)
            except Exception as ce:
                _runtime.LOGGER.warning("Failed to mark remote ICE candidates complete for %s: %s", redacted_chat_id, ce)

        self._arm_session_establishment_timeout(str(chat_id), pc, label="autopair")

        resolved_identity = identity
        try:
            resolved_identity = _runtime._resolve_conversation_identity(
                identity or self._resolve_chat_identity(chat_id)
            )
        except Exception as exc:
            _runtime.LOGGER.warning("Failed to resolve AutoPair conversation identity for %s: %s", redacted_chat_id, exc)

        response_sdp = pc.localDescription.sdp
        if source_platform != "cloud":
            response_sdp = _runtime.mark_sdp_ice_gathering_complete(response_sdp)

        response_payload: Dict[str, Any] = {
            "type": pc.localDescription.type,
            "sdp": response_sdp,
            "server_name": _runtime.get_configured_server_name(),
            "capabilities": _runtime._build_webrtc_capabilities(cfg=(_runtime.STATE.config or {})),
            "server_capabilities": _runtime._build_webrtc_capabilities(cfg=(_runtime.STATE.config or {})),
            # How this computer classifies the device, so the client can show
            # the same Own/Shared boundary in Devices and Saved Connections.
            "device_ownership": device_ownership,
        }
        if resolved_identity is not None:
            local_pair_platforms = {"local", "windows-local", "admin-web", "webrtc", "webrtc-datachannel", "datachannel"}
            bluetooth_pair_platforms = {"bluetooth", "bluetooth-local"}
            offer_platform = source_platform
            identity_transport = str(
                getattr(resolved_identity, "transport", "") or ""
            ).strip().lower()
            explicit_pairing_mode = str(getattr(resolved_identity, "pairing_mode", "") or "").strip()
            if explicit_pairing_mode:
                resolved_pairing_mode = explicit_pairing_mode
            elif offer_platform in bluetooth_pair_platforms or identity_transport in bluetooth_pair_platforms:
                resolved_pairing_mode = "bluetooth_pair"
            elif offer_platform in local_pair_platforms or identity_transport in local_pair_platforms:
                resolved_pairing_mode = "local_pair"
            elif offer_platform == "cloud" or identity_transport == "cloud":
                resolved_pairing_mode = "cloud_pair"
            else:
                resolved_pairing_mode = "auto_pair"
            response_payload.update(
                _runtime._build_client_session_identity_payload(
                    resolved_identity,
                    pairing_mode=resolved_pairing_mode,
                )
            )
        return response_payload

    async def handle_session_offer(self, session_id: str, offer: Dict[str, Any]) -> Dict[str, Any]:
        """Handle WebRTC offer for tunnelmole session"""
        _runtime.LOGGER.info(f"Handling session offer for {session_id}")
        if _runtime.RTCPeerConnection is None:
            raise RuntimeError("aiortc not available")

        try:
            cfg_snapshot = _runtime.STATE.config or {}
            audio_call_enabled = _runtime._get_video_call_audio_enabled(cfg=cfg_snapshot)
            agents_disabled = _runtime._get_video_call_agents_disabled(cfg=cfg_snapshot)
            video_call_enabled = _runtime._get_video_call_enabled(cfg=cfg_snapshot)
            offer_sdp_for_background = str(offer.get("sdp") or "") if isinstance(offer, dict) else ""
            background_offer_state = self._background_audio_offer_state(
                offer,
                offer_sdp_for_background,
                cfg=cfg_snapshot,
            )
            offer_has_audio = self._sdp_has_media_section(offer_sdp_for_background, "audio")
            if background_offer_state.get("active"):
                self._set_background_audio_state(
                    str(session_id),
                    active=True,
                    silent_recording=bool(background_offer_state.get("silent_recording")),
                    muted=bool(background_offer_state.get("muted", True)),
                    platform=str(background_offer_state.get("platform") or "unknown"),
                    timestamp_ms=int(_runtime.time.time() * 1000),
                )
            voice_pipeline_enabled = bool(
                _runtime.AudioManager
                and audio_call_enabled
                and not agents_disabled
                and offer_has_audio
                and not background_offer_state.get("active")
            )

            session_payload = _runtime.STATE.session_cache.get(str(session_id), {})
            stable_client_id = (
                str(session_payload.get("stable_client_id") or "").strip()
                if isinstance(session_payload, dict)
                else ""
            )
            name_from_offer = (
                offer.get("client_display_name")
                if isinstance(offer, dict) and "client_display_name" in offer
                else (session_payload.get("client_display_name") if isinstance(session_payload, dict) else None)
            )
            client_display_name = _runtime._client_display_name_from_transport(name_from_offer)
            try:
                effective_client_display_name = self.remember_client_display_name(
                    self._resolve_chat_identity(str(session_id)),
                    client_display_name,
                )
            except Exception as exc:
                _runtime.LOGGER.debug("Could not attach direct-pair client name to %s: %s", session_id, exc)
                effective_client_display_name = ""
            if isinstance(session_payload, dict):
                session_payload["client_display_name"] = effective_client_display_name
                _runtime.STATE.session_cache[str(session_id)] = session_payload
            await self._cleanup_replacement_sessions(
                str(session_id),
                stable_client_id_hint=stable_client_id,
                label="pair",
            )
            # Self-replacement: if _cleanup_replacement_sessions cleared the auth
            # session cache entry for this very session (because early trickle-ICE
            # candidates created pending WebRTC state before the offer arrived),
            # restore it so subsequent candidate POSTs remain authenticated.
            if session_payload and str(session_id) not in _runtime.STATE.session_cache:
                _runtime.STATE.session_cache[str(session_id)] = session_payload

            custom_ice_servers = None
            if isinstance(offer, dict):
                try:
                    custom_ice_servers = offer.get("iceServers")
                except Exception:
                    custom_ice_servers = None

            _runtime.LOGGER.info(f"Creating RTCPeerConnection for {session_id}")
            configure_sctp_fragment_size()
            _runtime._apply_ice_consent_tolerance()
            await prime_turn_udp_probe(custom_ice_servers or _runtime._get_pairing_ice_servers())
            pc = _runtime.RTCPeerConnection(self._create_rtc_configuration_with_custom_ice(custom_ice_servers))

            @pc.on("icecandidate")
            def _on_local_icecandidate(candidate):
                if candidate is None:
                    return
                try:
                    self.queue_outgoing_trickle_candidate(session_id, candidate)
                except Exception as e:
                    _runtime.LOGGER.warning(f"Failed to queue local ICE candidate for {session_id}: {e}")

            # MUST capture main native event loop because STT runs in a background thread
            main_loop = _runtime.asyncio.get_running_loop()
            voice_status_callback = self._make_voice_call_status_callback(str(session_id), main_loop)
            audio_manager = None
            tts_track = None
            playback_track = None

            # Set up AudioManager for this session
            if voice_pipeline_enabled or (
                background_offer_state.get("active") and audio_call_enabled and offer_has_audio
            ):
                _runtime.LOGGER.info("Setting up AudioManager")
                def on_text_received(text):
                    # Safely schedule execution onto main native event loop
                    self._enqueue_voice_command_threadsafe(session_id, text, main_loop)

                audio_manager = _runtime.AudioManager(
                    on_text_received,
                    settings_provider=_runtime._speech_config,
                    status_callback=voice_status_callback,
                )
                audio_manager.conversation_context_provider = self._voice_training_conversation_provider(session_id)

                # Store manager
                _runtime.STATE.audio_managers[session_id] = audio_manager
                # Register both outbound lanes even for a Background Mode offer
                # (voice_pipeline_enabled is False for those). The lanes are not
                # attached to the sender yet - the heartbeat track owns it until
                # call_state(active=true) - but the manager must already own a
                # dedicated media lane, otherwise play_audio_file() falls back to
                # the speech lane and VAD barge-in cancels the caller's music.
                # This matches handle_autopair_offer.
                tts_track, playback_track = _runtime._ensure_audio_manager_outbound_tracks(
                    audio_manager=audio_manager,
                    cfg=cfg_snapshot,
                )
            else:
                if background_offer_state.get("active"):
                    self._track_session_task(
                        str(session_id),
                        self._publish_voice_call_status(
                            str(session_id),
                            {
                                "event": "readiness",
                                "state": "ready",
                                "detail": "Phone background connection is active.",
                                "timestamp_ms": int(_runtime.time.time() * 1000),
                            },
                        ),
                        "voice_call_status_ready_background_audio",
                    )
                elif audio_call_enabled and agents_disabled:
                    self._track_session_task(
                        str(session_id),
                        self._publish_voice_call_status(
                            str(session_id),
                            {
                                "event": "readiness",
                                "state": "ready",
                                "detail": "AutoYou Agents are disabled for video calls.",
                                "timestamp_ms": int(_runtime.time.time() * 1000),
                            },
                        ),
                        "voice_call_status_ready_agents_disabled",
                    )
                else:
                    unavailable_detail = (
                        "Voice audio is disabled in server video-call settings."
                        if not _runtime._get_video_call_audio_enabled(cfg=cfg_snapshot)
                        else "This client connected without voice-call audio."
                        if not offer_has_audio
                        else "Voice pipeline unavailable on server."
                    )
                    self._track_session_task(
                        str(session_id),
                        self._publish_voice_call_status(
                            str(session_id),
                            {
                                "state": "unavailable",
                                "detail": unavailable_detail,
                            },
                        ),
                        "voice_call_status_unavailable",
                    )

            @pc.on("track")
            def on_track(track):
                if track.kind == "audio":
                    _runtime.LOGGER.info(f"Received audio track from {session_id}")
                    self._cancel_session_establishment_timeout(
                        str(session_id),
                        expected_pc=pc,
                        reason="audio_track",
                    )
                    if not self._start_inbound_audio_track_sink(
                        track,
                        session_id=str(session_id),
                        audio_manager=audio_manager,
                        audio_call_enabled=audio_call_enabled,
                        cfg_snapshot=cfg_snapshot,
                    ):
                        _runtime.LOGGER.info("Audio track received for %s but no audio consumer is configured", session_id)
                elif track.kind == "video":
                    _runtime.LOGGER.info(f"Received video track from {session_id}")
                    self._cancel_session_establishment_timeout(
                        str(session_id),
                        expected_pc=pc,
                        reason="video_track",
                    )
                    if not video_call_enabled:
                        _runtime.LOGGER.info("Video calls disabled in server settings; ignoring inbound video for %s", session_id)
                        return
                    if _runtime.IncomingVideoTrackSink is not None:
                        sink = _runtime.IncomingVideoTrackSink(
                            track,
                            session_id=str(session_id),
                            recording_enabled=_runtime._get_video_record_my_video_enabled(cfg=(_runtime.STATE.config or {})),
                            recording_dir=_runtime._resolve_video_recording_dir(cfg=(_runtime.STATE.config or {})),
                            recording_mode=_runtime._get_video_recording_mode(cfg=(_runtime.STATE.config or {})),
                            image_interval_seconds=_runtime._get_video_image_interval_seconds(cfg=(_runtime.STATE.config or {})),
                        )
                        self._register_video_sink_aliases(str(session_id), sink)
                        _runtime.track_background_task(sink.start())
                    else:
                        _runtime.LOGGER.warning("Video sink unavailable; ignoring video track for %s", session_id)

            # Use centralized datachannel handler with keepalive and tunnelmole cleanup enabled
            _runtime.LOGGER.info("Setting up datachannel handler")
            pc.on(
                "datachannel",
                self._create_datachannel_handler(
                    session_id,
                    "pair",
                    enable_keepalive=True,
                    enable_tunnelmole_cleanup=True,
                    expected_pc=pc,
                ),
            )

            @pc.on("iceconnectionstatechange")
            def _on_ice_state_change_pair():
                state = pc.iceConnectionState
                _runtime.LOGGER.info(f"ICE state for {session_id}: {state}")
                try:
                    self._handle_transport_disconnect_state(
                        session_id,
                        pc,
                        source="ice",
                        state=state,
                    )
                except Exception as e:
                    _runtime.LOGGER.error(f"Cleanup error for {session_id} on ICE state '{state}': {e}")

            @pc.on("connectionstatechange")
            def _on_connection_state_change_pair():
                state = getattr(pc, "connectionState", None)
                _runtime.LOGGER.info(f"Peer connection state for {session_id}: {state}")
                try:
                    self._handle_transport_disconnect_state(
                        session_id,
                        pc,
                        source="peer_connection",
                        state=state,
                    )
                except Exception as e:
                    _runtime.LOGGER.error(f"Cleanup error for {session_id} on connection state '{state}': {e}")

            # Apply remote offer - handle both string and dictionary formats
            _runtime.LOGGER.info("Setting remote description")
            if isinstance(offer, str):
                sdp_string = offer
                offer_type = "offer"
            elif isinstance(offer, dict):
                sdp_string = offer["sdp"]
                offer_type = offer.get("type", "offer")
            else:
                raise ValueError(f"Invalid offer format: {type(offer)}")

            self._prefer_remote_desktop_video_codec(pc, sdp_string)
            await pc.setRemoteDescription(_runtime.RTCSessionDescription(sdp=sdp_string, type=offer_type))

            # Log transceivers for debugging
            transceivers = pc.getTransceivers()
            _runtime.LOGGER.info(f"Negotiated transceivers for {session_id}: {[t.kind for t in transceivers]}")

            # Attach configured outbound audio track (TTS or local capture) to audio transceiver if available
            audio_transceiver = next((t for t in transceivers if t.kind == "audio"), None)
            if audio_transceiver:
                self._register_audio_transceiver_aliases(str(session_id), audio_transceiver)
                if background_offer_state.get("active"):
                    audio_transceiver.direction = str(background_offer_state.get("server_audio_direction") or "inactive")
                    if (
                        audio_transceiver.direction in {"sendonly", "sendrecv"}
                        and not background_offer_state.get("silent_recording")
                    ):
                        self._attach_background_audio_heartbeat_track(str(session_id), audio_transceiver)
                    _runtime.LOGGER.info(
                        "Suppressed outbound voice/mixer audio for background session %s; server audio direction=%s silent_recording=%s heartbeat_track=%s",
                        session_id,
                        audio_transceiver.direction,
                        background_offer_state.get("silent_recording"),
                        getattr(getattr(audio_transceiver, "sender", None), "track", None) is not None,
                    )
                else:
                    effective_audio_track = _runtime._create_configured_outbound_audio_track(
                        cfg=cfg_snapshot,
                        session_id=str(session_id),
                        tts_track=tts_track,
                        playback_track=playback_track,
                        include_loopback=False,
                    )
                    if effective_audio_track:
                        audio_transceiver.sender.replaceTrack(effective_audio_track)
                        audio_transceiver.direction = "sendrecv"
                        _runtime.LOGGER.info(
                            "Attached outbound audio track (%s) and promoted transceiver to sendrecv for %s",
                            type(effective_audio_track).__name__,
                            session_id,
                        )
                    else:
                        _runtime.LOGGER.info("No outbound audio track attached for %s", session_id)
            else:
                _runtime.LOGGER.warning(f"No audio transceiver found in session offer for {session_id}")

            video_transceiver = next((t for t in transceivers if t.kind == "video"), None)
            if video_transceiver:
                if _runtime._get_outbound_video_available(cfg=(_runtime.STATE.config or {})):
                    _runtime.LOGGER.info(
                        "Deferring outbound video source %s for %s until video_state(active)",
                        _runtime._get_video_outbound_source(cfg=(_runtime.STATE.config or {})),
                        session_id,
                    )
                    video_transceiver.direction = "sendrecv"
                else:
                    _runtime.LOGGER.info(
                        "Outbound video not attached for %s; capabilities=%s",
                        session_id,
                        _runtime._build_webrtc_capabilities(cfg=(_runtime.STATE.config or {})).get("outbound_video", {}),
                    )
            else:
                _runtime.LOGGER.info(f"No video transceiver found in session offer for {session_id}")

            # Create and set local answer
            _runtime.LOGGER.info("Creating answer")
            try:
                answer = await pc.createAnswer()
                _runtime.LOGGER.info("Setting local description")
                await pc.setLocalDescription(answer)
            except ValueError as ve:
                if "None is not in list" in str(ve):
                    _runtime.LOGGER.error(f"aiortc transceiver bug hit during setLocalDescription for session {session_id}: {ve}")
                    raise ValueError(f"WebRTC negotiation failure (transceiver mismatch): {ve}")
                raise

            async def _gather_complete():
                try:
                    start_wait = _runtime.time.time()
                    while pc.iceGatheringState != 'complete':
                        if _runtime.time.time() - start_wait > 10:
                            _runtime.LOGGER.warning("ICE gathering timed out after 10s, proceeding with collected candidates")
                            break
                        await _runtime.asyncio.sleep(0.1)
                except Exception as e:
                    _runtime.LOGGER.error(f"Error waiting for ICE gathering: {e}")

            await _gather_complete()

            # Keep reference
            self.session_peers[session_id] = pc

            # Apply any pending ICE candidates
            if session_id in self.pending_candidates:
                for candidate_data in self.pending_candidates[session_id]:
                    try:
                        await pc.addIceCandidate(self._normalize_remote_ice_candidate(candidate_data))
                        _runtime.LOGGER.info(f"Applied pending ICE candidate for session {session_id}")
                    except Exception as e:
                        _runtime.LOGGER.error(f"Failed to apply pending ICE candidate for session {session_id}: {e}")
                del self.pending_candidates[session_id]

            if isinstance(offer, dict):
                candidates = offer.get("candidates") or []
                if isinstance(candidates, list):
                    for c in candidates:
                        try:
                            cand_str = c.get("candidate") if isinstance(c, dict) else None
                            if cand_str and _runtime.candidate_from_sdp:
                                cand_line = cand_str if cand_str.startswith("candidate:") else f"candidate:{cand_str}"
                                candidate_obj = _runtime.candidate_from_sdp(cand_line)
                                candidate_obj.sdpMid = c.get("sdpMid") if isinstance(c, dict) else None
                                candidate_obj.sdpMLineIndex = c.get("sdpMLineIndex") if isinstance(c, dict) else None
                                await pc.addIceCandidate(candidate_obj)
                        except Exception as ce:
                            _runtime.LOGGER.warning(f"Failed to add inline ICE candidate from offer payload: {ce}")

            self._arm_session_establishment_timeout(str(session_id), pc, label="pair")
            resolved_identity = None
            try:
                resolved_identity = _runtime._resolve_conversation_identity(
                    self._resolve_chat_identity(str(session_id))
                )
            except Exception as exc:
                _runtime.LOGGER.warning(
                    "Failed to resolve pair conversation identity for %s: %s",
                    session_id,
                    exc,
                )
            server_id = _runtime._get_stable_server_id()
            server_identity_key = _runtime._get_server_identity_key()
            answer_payload = {
                "type": pc.localDescription.type,
                "sdp": pc.localDescription.sdp,
                "server_name": _runtime.get_configured_server_name(),
                "server_id": server_id,
                "server_identity_key": server_identity_key,
                "capabilities": _runtime._build_webrtc_capabilities(cfg=(_runtime.STATE.config or {})),
                "server_capabilities": _runtime._build_webrtc_capabilities(cfg=(_runtime.STATE.config or {})),
            }
            if resolved_identity is not None:
                answer_payload.update(_runtime._build_client_session_identity_payload(resolved_identity))
            return answer_payload

        except Exception as e:
            _runtime.LOGGER.error(f"Error in handle_session_offer: {e}")
            import traceback
            _runtime.LOGGER.error(traceback.format_exc())
            raise

    async def handle_session_candidate(self, session_id: str, candidate: Dict[str, Any]) -> bool:
        """Handle ICE candidate for tunnelmole session"""
        try:
            if self._register_remote_ice_candidate(session_id, candidate):
                _runtime.LOGGER.debug("Ignoring duplicate ICE candidate for session %s", session_id)
                return True
            candidate = self._normalize_remote_ice_candidate(candidate)
            if session_id in self.session_peers:
                pc = self.session_peers[session_id]
                await pc.addIceCandidate(candidate)
                _runtime.LOGGER.info(f"Applied ICE candidate for session {session_id}")
                return True
            else:
                # Store candidate for later if peer connection not ready yet
                if session_id not in self.pending_candidates:
                    self.pending_candidates[session_id] = []
                self.pending_candidates[session_id].append(candidate)
                _runtime.LOGGER.info(f"Stored pending ICE candidate for session {session_id}")
                return True
        except Exception as e:
            _runtime.LOGGER.error(f"Failed to handle ICE candidate for session {session_id}: {e}")
            return False

    def cleanup_session(self, session_id: str, expected_pc: Optional[RTCPeerConnection] = None):
        """Clean up WebRTC session resources (sync version).

        Uses a per-session asyncio.Lock to prevent concurrent cleanup from
        multiple callers (ICE state change, channel close, keepalive timeout).
        All resource teardown is consolidated in _async_cleanup_session.
        """
        current_pc = self.session_peers.get(session_id)
        if expected_pc is not None and current_pc is not None and current_pc is not expected_pc:
             _runtime.LOGGER.info(f"Ignoring stale cleanup for {session_id}; active peer connection has already been replaced")
             return

        # Check if cleanup is already in progress
        if session_id in self.cleanup_tasks and not self.cleanup_tasks[session_id].done():
             _runtime.LOGGER.info(f"Cleanup already in progress for {session_id}, skipping duplicate request")
             return

        # Get or create a per-session lock
        if session_id not in self._cleanup_locks:
            self._cleanup_locks[session_id] = _runtime.asyncio.Lock()
        lock = self._cleanup_locks[session_id]

        async def _locked_cleanup():
            try:
                async with lock:
                    await self._async_cleanup_session(session_id, expected_pc=expected_pc)
            finally:
                # Remove the lock after cleanup completes or is cancelled.
                if self._cleanup_locks.get(session_id) is lock:
                    self._cleanup_locks.pop(session_id, None)

        try:
            cleanup_task = _runtime.asyncio.create_task(_locked_cleanup())
            self.cleanup_tasks[session_id] = cleanup_task

            def cleanup_done(task):
                if session_id in self.cleanup_tasks and self.cleanup_tasks[session_id] == task:
                    del self.cleanup_tasks[session_id]

            cleanup_task.add_done_callback(cleanup_done)

        except Exception as e:
            _runtime.LOGGER.error(f"Error scheduling cleanup for WebRTC session {session_id}: {e}")

    async def _async_cleanup_session(self, session_id: str, expected_pc: Optional[RTCPeerConnection] = None):
        """Async helper - performs all resource cleanup for a WebRTC session.

        Must be called under _cleanup_locks[session_id] to prevent races.
        """
        current_pc = self.session_peers.get(session_id)
        if expected_pc is not None and current_pc is not None and current_pc is not expected_pc:
            _runtime.LOGGER.info(f"Skipping stale async cleanup for {session_id}; session has already been replaced")
            return

        cleanup_ids = set(self._ordered_related_session_ids(session_id))
        previous_host_owner = self.host_audio_owner()
        for cleanup_id in cleanup_ids:
            self._suspend_room_bridge_transport(str(cleanup_id))

        # Clean up ping-pong game state for all related sessions
        try:
            game_ping_pong.ping_pong_scorer_manager.cleanup_session(session_id)
            game_ping_pong.ping_pong_scorer_manager.cleanup_related_sessions(cleanup_ids)
        except Exception as game_exc:
            _runtime.LOGGER.warning("PingPong cleanup error: %s", game_exc)

        missing = object()
        expected_peer_connections = {
            cleanup_id: self.session_peers.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_datachannel_managers = {
            cleanup_id: self.datachannel_managers.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_pending_candidates = {
            cleanup_id: self.pending_candidates.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_seen_remote_ice_candidates = {
            cleanup_id: self.seen_remote_ice_candidates.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_outgoing_trickle_candidates = {
            cleanup_id: self.outgoing_trickle_candidates.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_voice_status = {
            cleanup_id: self.voice_call_status_by_session.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_voice_playback = {
            cleanup_id: self.voice_call_playback_by_session.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_voice_call_client_active = {
            cleanup_id: self.voice_call_client_active_by_session.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_screen_sessions = {
            cleanup_id: self.screen_sessions.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_background_audio_state = {
            cleanup_id: self.background_audio_state_by_session.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_silent_recorders = {
            cleanup_id: self.silent_recorders.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_rewarded_ad_completion = {
            cleanup_id: self.rewarded_ad_completion_by_session.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_rewarded_ad_control_lease = {
            cleanup_id: self.rewarded_ad_control_leases_by_session.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_remote_desktop_keyboard_lease = {
            cleanup_id: self.remote_desktop_keyboard_leases_by_session.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_remote_desktop_control_lease = {
            cleanup_id: self.remote_desktop_control_leases_by_session.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_session_cache = {
            str(cleanup_id): _runtime.STATE.session_cache.get(str(cleanup_id), missing)
            for cleanup_id in cleanup_ids
        }
        expected_voice_command_queues = {
            cleanup_id: self.voice_command_queues.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_voice_command_workers = {
            cleanup_id: self.voice_command_workers.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_pending_voice_chat_messages = {
            cleanup_id: self.pending_voice_chat_messages.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        # from __debug_provenance_x__ import email
        expected_http_proxy_request_tasks = {
          cleanup_id: self.http_proxy_request_tasks.get(cleanup_id, missing)
          for cleanup_id in cleanup_ids
        }
        expected_session_message_tasks = {
            cleanup_id: self.session_message_tasks.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_audio_sinks = {
            cleanup_id: self.audio_sinks.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_audio_transceivers = {
            cleanup_id: self.audio_transceivers.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_video_sinks = {
            cleanup_id: self.video_sinks.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }
        expected_audio_managers = {
            cleanup_id: _runtime.STATE.audio_managers.get(cleanup_id, missing)
            for cleanup_id in cleanup_ids
        }

        def pop_if_current(mapping: Dict[Any, Any], key: Any, expected: Any) -> Any:
            if expected is missing:
                return missing
            current = mapping.get(key, missing)
            if current is not expected:
                return missing
            return mapping.pop(key, missing)

        for cleanup_id in cleanup_ids:
            expected_cleanup_pc = expected_peer_connections.get(cleanup_id, missing)
            self._cancel_session_disconnect_grace(
                cleanup_id,
                expected_pc=None if expected_cleanup_pc is missing else expected_cleanup_pc,
                reason="cleanup",
            )
            self._cancel_session_establishment_timeout(
                cleanup_id,
                expected_pc=None if expected_cleanup_pc is missing else expected_cleanup_pc,
            )

        if len(cleanup_ids) > 1:
            _runtime.LOGGER.info("Cleaning WebRTC voice aliases for %s: %s", session_id, sorted(cleanup_ids))

        stopped_audio_sinks: List[Any] = []
        for cleanup_id in cleanup_ids:
            audio_sink = expected_audio_sinks.get(cleanup_id, missing)
            if audio_sink is missing:
                continue
            pop_if_current(self.audio_sinks, cleanup_id, audio_sink)
            if any(audio_sink is current for current in stopped_audio_sinks):
                continue
            stopped_audio_sinks.append(audio_sink)
            try:
                if await self._await_cleanup_awaitable(
                    audio_sink.stop(),
                    session_id=cleanup_id,
                    label="audio sink",
                ):
                    _runtime.LOGGER.info(f"Stopped audio sink for {cleanup_id}")
            except Exception as e:
                _runtime.LOGGER.warning(f"Error stopping audio sink for {cleanup_id}: {e}")

        for cleanup_id in cleanup_ids:
            audio_transceiver = expected_audio_transceivers.get(cleanup_id, missing)
            if audio_transceiver is not missing:
                pop_if_current(self.audio_transceivers, cleanup_id, audio_transceiver)

        closed_silent_recorders: List[Any] = []
        for cleanup_id in cleanup_ids:
            background_state = expected_background_audio_state.get(cleanup_id, missing)
            if background_state is not missing:
                pop_if_current(self.background_audio_state_by_session, cleanup_id, background_state)
            recorder = expected_silent_recorders.get(cleanup_id, missing)
            if recorder is missing:
                continue
            pop_if_current(self.silent_recorders, cleanup_id, recorder)
            if any(recorder is current for current in closed_silent_recorders):
                continue
            closed_silent_recorders.append(recorder)
            try:
                recorder.close()
                _runtime.LOGGER.info("Closed silent recording writer for %s", cleanup_id)
            except Exception as e:
                _runtime.LOGGER.warning("Error closing silent recording writer for %s: %s", cleanup_id, e)

        stopped_video_sinks: List[Any] = []
        for cleanup_id in cleanup_ids:
            video_sink = expected_video_sinks.get(cleanup_id, missing)
            if video_sink is missing:
                continue
            pop_if_current(self.video_sinks, cleanup_id, video_sink)
            if any(video_sink is current for current in stopped_video_sinks):
                continue
            stopped_video_sinks.append(video_sink)
            try:
                if await self._await_cleanup_awaitable(
                    video_sink.stop(),
                    session_id=cleanup_id,
                    label="video sink",
                ):
                    _runtime.LOGGER.info(f"Stopped video sink for {cleanup_id}")
            except Exception as e:
                _runtime.LOGGER.warning(f"Error stopping video sink for {cleanup_id}: {e}")

        # Drop the gated outbound desktop video track (aiortc stops the track when
        # the peer connection closes; we just disable + forget the lookup entry).
        disabled_desktop_tracks: List[Any] = []
        disabled_local_audio_tracks: List[Any] = []
        for cleanup_id in cleanup_ids:
            desktop_track = self.desktop_video_tracks.pop(cleanup_id, None)
            if desktop_track is not None and not any(desktop_track is current for current in disabled_desktop_tracks):
                disabled_desktop_tracks.append(desktop_track)
                bitrate_task = getattr(desktop_track, "_autoyou_desktop_video_bitrate_task", None)
                if isinstance(bitrate_task, _runtime.asyncio.Task):
                    bitrate_task.cancel()
                desktop_track._autoyou_desktop_video_sender = None
                try:
                    desktop_track.disable()
                except Exception:
                    pass

            # Clean up local outbound audio tracks (mic/loopback)
            if hasattr(_runtime.STATE, "local_audio_tracks"):
                local_audio_tracks = _runtime.STATE.local_audio_tracks.pop(cleanup_id, None)
                if local_audio_tracks is not None:
                    if not isinstance(local_audio_tracks, (list, tuple, set)):
                        local_audio_tracks = [local_audio_tracks]
                    for local_audio in local_audio_tracks:
                        if any(local_audio is current for current in disabled_local_audio_tracks):
                            continue
                        disabled_local_audio_tracks.append(local_audio)
                        try:
                            local_audio.disable()
                        except Exception:
                            pass

        # Close peer connection
        closed_peer_connections: List[Any] = []
        released_remote_desktop_control_leases: set[int] = set()
        for cleanup_id in cleanup_ids:
            pc = expected_peer_connections.get(cleanup_id, missing)
            if pc is missing:
                continue
            pop_if_current(self.session_peers, cleanup_id, pc)
            if any(pc is current for current in closed_peer_connections):
                continue
            closed_peer_connections.append(pc)
            try:
                if await self._await_cleanup_awaitable(
                    pc.close(),
                    session_id=cleanup_id,
                    label="peer connection",
                    timeout_seconds=_runtime.WEBRTC_PEER_CLOSE_TIMEOUT_SECONDS,
                ):
                    _runtime.LOGGER.info(f"Closed WebRTC peer connection for {cleanup_id}")
            except Exception as e:
                _runtime.LOGGER.error(f"Error closing WebRTC peer connection for {cleanup_id}: {e}")

        # Clean up datachannel manager
        disconnected_datachannel_managers: List[Any] = []
        for cleanup_id in cleanup_ids:
            dm = expected_datachannel_managers.get(cleanup_id, missing)
            if dm is missing:
                continue
            pop_if_current(self.datachannel_managers, cleanup_id, dm)
            if any(dm is current for current in disconnected_datachannel_managers):
                continue
            try:
                dm.disconnect()
                disconnected_datachannel_managers.append(dm)
                _runtime.LOGGER.info(f"Cleaned up datachannel manager for session {cleanup_id}")
            except Exception as e:
                _runtime.LOGGER.error(f"Error cleaning up datachannel manager for {cleanup_id}: {e}")

        for cleanup_id in cleanup_ids:
            pop_if_current(self.pending_candidates, cleanup_id, expected_pending_candidates.get(cleanup_id, missing))
            pop_if_current(
                self.seen_remote_ice_candidates,
                cleanup_id,
                expected_seen_remote_ice_candidates.get(cleanup_id, missing),
            )
            pop_if_current(
                self.outgoing_trickle_candidates,
                cleanup_id,
                expected_outgoing_trickle_candidates.get(cleanup_id, missing),
            )
            pop_if_current(
                self.voice_call_status_by_session,
                cleanup_id,
                expected_voice_status.get(cleanup_id, missing),
            )
            pop_if_current(
                self.voice_call_playback_by_session,
                cleanup_id,
                expected_voice_playback.get(cleanup_id, missing),
            )
            pop_if_current(
                self.voice_call_client_active_by_session,
                cleanup_id,
                expected_voice_call_client_active.get(cleanup_id, missing),
            )
            removed_screen = pop_if_current(
                self.screen_sessions,
                cleanup_id,
                expected_screen_sessions.get(cleanup_id, missing),
            )
            if removed_screen is not missing and isinstance(removed_screen, dict):
                self.screen_listen_mixer.forget(str(removed_screen.get("id") or ""))
            pop_if_current(
                self.rewarded_ad_completion_by_session,
                cleanup_id,
                expected_rewarded_ad_completion.get(cleanup_id, missing),
            )
            pop_if_current(
                self.rewarded_ad_control_leases_by_session,
                cleanup_id,
                expected_rewarded_ad_control_lease.get(cleanup_id, missing),
            )
            popped_remote_desktop_keyboard_lease = pop_if_current(
                self.remote_desktop_keyboard_leases_by_session,
                cleanup_id,
                expected_remote_desktop_keyboard_lease.get(cleanup_id, missing),
            )
            if popped_remote_desktop_keyboard_lease is not missing:
                # Mirrors the "hide" cleanup in send_remote_desktop_keyboard_control_to_reply_target /
                # _handle_voice_call_control_message: a lease can be cleared here (app crash, dropped
                # connection) before the client's own modifier keyUp cleanup arrives, so release
                # Ctrl/Alt/Shift on the host unconditionally instead of trusting a keyUp that may never come.
                await _runtime.asyncio.to_thread(_runtime.release_stuck_modifiers)
            expected_remote_desktop_control = expected_remote_desktop_control_lease.get(
                cleanup_id,
                missing,
            )
            if (
                expected_remote_desktop_control is not missing
                and id(expected_remote_desktop_control) not in released_remote_desktop_control_leases
            ):
                released = await self._release_remote_desktop_control(
                    cleanup_id,
                    str(expected_remote_desktop_control.get("control_id") or ""),
                    expected_lease=expected_remote_desktop_control,
                )
                if released:
                    released_remote_desktop_control_leases.add(id(expected_remote_desktop_control))
            pop_if_current(
                self.pending_voice_chat_messages,
                cleanup_id,
                expected_pending_voice_chat_messages.get(cleanup_id, missing),
            )
            if cleanup_id not in self.pending_voice_chat_messages:
                flush_lock = self._voice_chat_flush_locks.get(cleanup_id)
                if flush_lock is None or not flush_lock.locked():
                    self._voice_chat_flush_locks.pop(cleanup_id, None)
            offline_flush_lock = self._offline_queue_flush_locks.get(cleanup_id)
            if cleanup_id not in self._offline_pending_messages and (
                offline_flush_lock is None or not offline_flush_lock.locked()
            ):
                self._offline_queue_flush_locks.pop(cleanup_id, None)
            # Remove voice DC session_id mapping so no stale rekeyed-UUID is
            # used after the connection is torn down.
            self._voice_dc_session_id.pop(cleanup_id, None)
            pop_if_current(
                _runtime.STATE.session_cache,
                str(cleanup_id),
                expected_session_cache.get(str(cleanup_id), missing),
            )

            # Cancel any long-running chat/browser tasks tied to this session.
            expected_tasks = expected_session_message_tasks.get(cleanup_id, missing)
            if expected_tasks is not missing:
              await self._cancel_session_message_tasks(
                cleanup_id,
                expected_tasks=expected_tasks,
              )

            expected_http_tasks = expected_http_proxy_request_tasks.get(cleanup_id, missing)
            if expected_http_tasks is not missing:
                http_proxy_tasks = pop_if_current(
                    self.http_proxy_request_tasks,
                    cleanup_id,
                    expected_http_tasks,
                )
                if http_proxy_tasks is not missing:
                    await self._cancel_task_collection_with_timeout(
                        list(http_proxy_tasks.values()),
                        session_id=cleanup_id,
                        label="HTTP proxy",
                    )

            # Stop and cleanup sequential voice-command worker/queue
            expected_queue = expected_voice_command_queues.get(cleanup_id, missing)
            expected_worker = expected_voice_command_workers.get(cleanup_id, missing)
            current_queue = self.voice_command_queues.get(cleanup_id, missing)
            current_worker = self.voice_command_workers.get(cleanup_id, missing)
            queue_ref = None
            worker = None

            if expected_queue is not missing and current_queue is expected_queue:
              queue_ref = self.voice_command_queues.pop(cleanup_id, None)
            if expected_worker is not missing and current_worker is expected_worker:
              worker = self.voice_command_workers.pop(cleanup_id, None)

            if queue_ref is not None:
              try:
                if queue_ref.full():
                  dropped = queue_ref.get_nowait()
                  queue_ref.task_done()
                  _runtime.LOGGER.warning(
                    f"Dropping queued transcript while stopping worker for {cleanup_id}: {dropped!r}"
                  )
              except Exception:
                pass
              try:
                queue_ref.put_nowait(None)
              except Exception:
                pass

            if worker is not None and not worker.done():
              done, _ = await _runtime.asyncio.wait({worker}, timeout=2.0)
              if worker not in done:
                await self._cancel_task_collection_with_timeout(
                    [worker],
                    session_id=cleanup_id,
                    label="voice command worker",
                    timeout_seconds=_runtime.WEBRTC_TASK_CANCEL_TIMEOUT_SECONDS,
                )

        closed_audio_managers: List[Any] = []
        for cleanup_id in cleanup_ids:
            audio_manager = expected_audio_managers.get(cleanup_id, missing)
            if audio_manager is missing or any(audio_manager is current for current in closed_audio_managers):
                continue
            pop_if_current(_runtime.STATE.audio_managers, cleanup_id, audio_manager)
            try:
                alias_ids = {str(cleanup_id)} | self._purge_audio_manager_aliases(audio_manager)
                audio_manager.close()
                closed_audio_managers.append(audio_manager)
                _runtime.LOGGER.info(
                    "Cleaned up audio manager for %s across aliases: %s",
                    cleanup_id,
                    sorted(alias_ids),
                )
            except Exception as e:
                _runtime.LOGGER.warning(f"Error closing audio manager for {cleanup_id}: {e}")

        try:
            await self._cancel_lingering_webrtc_teardown_tasks()
        except Exception as exc:
            _runtime.LOGGER.debug("Failed to cancel lingering WebRTC teardown tasks for %s: %s", session_id, exc)

        # Clean up any proxied WebSocket connections tied to this session
        if hasattr(self, '_ws_session_requests') and session_id in self._ws_session_requests:
            req_ids = list(self._ws_session_requests.pop(session_id, set()))
            for req_id in req_ids:
                if hasattr(self, '_ws_connections') and req_id in self._ws_connections:
                    ws_conn = self._ws_connections.pop(req_id, None)
                    if ws_conn:
                        try:
                            _runtime.asyncio.create_task(ws_conn.close(code=1001, reason="WebRTC session terminated"))
                            _runtime.LOGGER.info(f"Closed lingering upstream WebSocket {req_id} due to session cleanup")
                        except Exception as close_err:
                            _runtime.LOGGER.warning(f"Error closing lingering WebSocket {req_id}: {close_err}")

        for cleanup_id in cleanup_ids:
            if cleanup_id not in self.session_peers:
                self.same_machine_audio_sessions.discard(cleanup_id)
                self.host_media_owner_by_session.pop(cleanup_id, None)
        if previous_host_owner != self.host_audio_owner():
            await self._rewire_outbound_audio_for_host()
        _runtime.LOGGER.info(f"Fully cleaned up WebRTC session {session_id}")

    async def async_cleanup_session(self, session_id: str, expected_pc: Optional[RTCPeerConnection] = None):
        """Clean up WebRTC session resources (async version, uses same lock)."""
        if session_id not in self._cleanup_locks:
            self._cleanup_locks[session_id] = _runtime.asyncio.Lock()
        lock = self._cleanup_locks[session_id]
        try:
            async with lock:
                await self._async_cleanup_session(session_id, expected_pc=expected_pc)
        finally:
            if self._cleanup_locks.get(session_id) is lock:
                self._cleanup_locks.pop(session_id, None)

    async def live_pair_action(self, owner: str, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("Invalid live pairing control")
        self.live_pairing.router = _runtime.pairing_router
        action = payload.get("action")
        def next_code():
            entries = _runtime.pairing_router._ordered_totp_secret_entries("liveqr", owner)
            if not entries:
                return ""
            import pyotp
            return pyotp.TOTP(entries[0][1]).at(_runtime.time.time() + 30)
        if action == "start":
            return self.live_pairing.start(owner, mode=_runtime.get_security_mode(),
                tier=_runtime.pairing_router._get_security_tier(),
                ice=await _runtime._get_pairing_ice_servers_async(),
                name=_runtime.get_configured_server_name(), password=_runtime.get_current_password(),
                totp_code=next_code())
        if action == "refresh":
            return self.live_pairing.invite(owner, str(payload.get("invite_id") or ""), totp_code=next_code())
        if action == "exchange":
            return await self.live_pairing.exchange(owner, payload.get("request") or {})
        if action == "cancel":
            self.live_pairing.cancel(owner)
            return {"kind": "cancelled"}
        raise ValueError("Unsupported live pairing action")

    async def _handle_live_pair_control(self, message, transport_id, manager):
        payload = message.payload if isinstance(message.payload, dict) else {}
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or len(request_id) > 64:
            return
        try:
            identity = self._room_bridge_trusted_owner(transport_id)
            owner_key = str(getattr(identity, "owner_key", ""))
            if owner_key.startswith("liveqr:") or getattr(identity, "transport", "") == "liveqr":
                raise ValueError("Ask the computer owner to help connect another device")
            if getattr(identity, "transport", "") == "cloud" and self.device_ownership_for_session(transport_id) != DEVICE_OWN:
                raise ValueError("Ask the computer owner to help connect another device")
            result = await self.live_pair_action(transport_id, payload)
            response = {"request_id": request_id, "result": result}
        except Exception as exc:
            response = {"request_id": request_id, "error": str(exc)}
        await manager.send_message(SharedDataChannelMessage(
            header=SharedMessageHeader(message_id=_runtime.uuid.uuid4().hex,
                message_type=SharedMessageType.PAIRING_CONTROL, timestamp=_runtime.time.time(),
                session_id=transport_id), payload=response))

    @staticmethod
    def _room_bridge_trusted_owner(trusted_transport_id: str) -> Any:
        """Resolve authority exclusively from the established transport."""
        identity = _runtime.resolve_webrtc_chat_identity(str(trusted_transport_id or ""))
        require_bound_owner_key(getattr(identity, "owner_key", ""))
        return identity

    @staticmethod
    def _require_room_bridge_backend() -> str:
        """Select only a direct model backend with no agent/tool action surface."""
        config = _runtime.STATE.config or {}
        provider_config = config.get("ai_provider", {}) if isinstance(config, dict) else {}
        configured = (
            provider_config.get("provider")
            if isinstance(provider_config, dict)
            else None
        )
        return require_room_bridge_read_only_backend(
            configured or _runtime.os.getenv("AI_PROVIDER", "ollama")
        )

    async def _send_room_bridge_control(
        self,
        datachannel_manager: Any,
        *,
        trusted_transport_id: str,
        payload: Dict[str, Any],
    ) -> bool:
        if datachannel_manager is None or not hasattr(datachannel_manager, "send_message"):
            return False
        control = create_room_bridge_control_message(
            payload,
            session_id=str(trusted_transport_id or ""),
            user_id=_runtime.get_configured_server_name(),
        )
        try:
            return bool(await datachannel_manager.send_message(control))
        except Exception as exc:
            _runtime.LOGGER.debug("Room bridge control delivery failed: %s", exc)
            return False

    async def _announce_room_bridge_presence(
        self,
        datachannel_manager: Any,
        *,
        grant: Any,
        trusted_transport_id: str,
        listening: bool = False,
    ) -> bool:
        """Publish the Computer's participant row to the room that holds a grant.

        The row is built from the grant's own member record and the single
        contract in :mod:`shared.room_bridge`, never assembled here, so what the
        room displays is what this process actually declared.

        Best effort by design. A room that has moved on, or a transport that has
        gone, must not turn a presence update into a failed grant - the caller
        has already admitted the Computer, and a missing row is a worse screen
        rather than a broken session.
        """
        try:
            payload = presence_control_payload(
                grant,
                computer_presence(dict(grant.computer_member), listening=listening),
            )
        except Exception as exc:
            _runtime.LOGGER.warning("Refused to publish a Computer presence row: %s", exc)
            return False
        return await self._send_room_bridge_control(
            datachannel_manager,
            trusted_transport_id=trusted_transport_id,
            payload=payload,
        )

    def _begin_call_listening(self, grant: Any, *, trusted_transport_id: str) -> None:
        """Let the Computer follow the call in the room this grant covers.

        Attaching here rather than when audio first arrives keeps one rule:
        the grant is what admits the Computer, so the grant is what starts and
        stops its listening. No audio flows until a call does, so an attached
        room with no call simply never produces a turn.

        Refusals are silent by design - an ineligible grant or a provider that
        can act means the call carries on without the Computer, which is the
        correct outcome rather than an error to surface mid-call.
        """
        loop = _runtime.asyncio.get_running_loop()

        def publish(text: str) -> None:
            # Notes cross back onto the loop: this is called from the worker
            # that runs transcription, and the datachannel is not thread-safe.
            _runtime.asyncio.run_coroutine_threadsafe(
                self._send_room_bridge_note(
                    grant, text, trusted_transport_id=trusted_transport_id
                ),
                loop,
            )

        def respond(prompt: str, turns: Any) -> str:
            future = _runtime.asyncio.run_coroutine_threadsafe(
                self._room_listener_reply(prompt), loop
            )
            return str(future.result(timeout=90) or "")

        def announce(row: Dict[str, Any]) -> None:
            manager = self.datachannel_managers.get(trusted_transport_id)
            if manager is None:
                return
            _runtime.asyncio.run_coroutine_threadsafe(
                self._send_room_bridge_control(
                    manager,
                    trusted_transport_id=trusted_transport_id,
                    payload=presence_control_payload(grant, row),
                ),
                loop,
            )

        try:
            backend = self._require_room_bridge_backend()
        except RoomBridgeError:
            return
        self.call_listeners.attach(
            room_id=grant.room_id,
            grant=grant,
            backend=backend,
            responder=respond,
            publish=publish,
            announce=announce,
            server_identity_key=_runtime._get_server_identity_key(),
            server_name=_runtime.get_configured_server_name(),
        )

    async def _room_listener_reply(self, prompt: str) -> str:
        """One model turn for a call listener, on the read-only path only."""
        from rest_api import _sanitize_direct_llm_text, send_message_to_native_ollama

        # Re-checked at use time: an operator can change providers after a grant
        # was issued, and a listener must never fall through to the graph.
        self._require_room_bridge_backend()
        result = await send_message_to_native_ollama(
            prompt,
            f"session::room-listener:{_runtime.hashlib.sha256(prompt.encode()).hexdigest()[:16]}",
            metadata={},
            system_prompt=ROOM_BRIDGE_READ_ONLY_SYSTEM_PROMPT,
        )
        return _sanitize_direct_llm_text(result.get("response")) or ""

    async def _send_room_bridge_note(
        self, grant: Any, text: str, *, trusted_transport_id: str
    ) -> bool:
        """Deliver something the Computer said during a call."""
        manager = self.datachannel_managers.get(trusted_transport_id)
        if manager is None:
            return False
        try:
            payload = note_control_payload(grant, text)
        except RoomBridgeError as exc:
            _runtime.LOGGER.warning("Refused to publish a Computer note: %s", exc)
            return False
        return await self._send_room_bridge_control(
            manager,
            trusted_transport_id=trusted_transport_id,
            payload=payload,
        )

    def _end_call_listening(self, room_id: Any) -> None:
        """The grant went, so the listening goes with it.

        Called on every revocation path - explicit revoke, supersede, expiry and
        transport loss - because a Computer that keeps following a call after
        its admission ended is the one failure this design cannot have.
        """
        try:
            self.call_listeners.detach(str(room_id or ""), summarize=False)
        except Exception as exc:  # pragma: no cover - defensive
            _runtime.LOGGER.debug("Could not stop the call listener: %s", exc)

    def _cancel_room_bridge_expiry(self, grant_id: str) -> None:
        task = self.room_bridge_expiry_tasks.pop(str(grant_id or ""), None)
        current = _runtime.asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()

    def _cancel_room_bridge_tasks(self, tasks: Iterable[Any]) -> int:
        current = _runtime.asyncio.current_task()
        cancelled = 0
        for task in tasks:
            if task is current or getattr(task, "done", lambda: False)():
                continue
            task.cancel()
            cancelled += 1
        return cancelled

    def _suspend_room_bridge_transport(self, trusted_transport_id: str) -> None:
        suspensions = self.room_bridge_grants.suspend_transport(trusted_transport_id)
        for suspended in suspensions:
            self._end_call_listening(suspended.grant.room_id)
        cancelled = cancel_revoked_tasks(
            suspensions,
            current_task=_runtime.asyncio.current_task(),
        )
        if suspensions:
            _runtime.LOGGER.info(
                "Suspended %d room bridge grant(s) and cancelled %d turn(s) for disconnected transport",
                len(suspensions),
                cancelled,
            )

    def _schedule_room_bridge_expiry(self, grant: Any) -> None:
        self._cancel_room_bridge_expiry(grant.grant_id)

        async def _expire() -> None:
            try:
                while True:
                    delay = self.room_bridge_grants.seconds_until_expiry(
                        grant.grant_id,
                        grant.grant_revision,
                    )
                    if delay is None:
                        return
                    if delay > 0:
                        await _runtime.asyncio.sleep(delay)
                        continue
                    break
                revocation = self.room_bridge_grants.expire(
                    grant.grant_id,
                    grant.grant_revision,
                )
                if revocation is None:
                    return
                self._end_call_listening(revocation.grant.room_id)
                cancel_revoked_tasks(
                    (revocation,),
                    current_task=_runtime.asyncio.current_task(),
                )
                manager = self._datachannel_manager_for_session(
                    revocation.grant.current_transport_id,
                    require_send_message=True,
                )
                if manager is not None:
                    await self._send_room_bridge_control(
                        manager,
                        trusted_transport_id=revocation.grant.current_transport_id,
                        payload=revoked_control_payload(revocation),
                    )
            except _runtime.asyncio.CancelledError:
                return
            finally:
                current = _runtime.asyncio.current_task()
                if self.room_bridge_expiry_tasks.get(grant.grant_id) is current:
                    self.room_bridge_expiry_tasks.pop(grant.grant_id, None)

        self.room_bridge_expiry_tasks[grant.grant_id] = _runtime.asyncio.create_task(_expire())

    async def _handle_room_bridge_control(
        self,
        message: Any,
        *,
        trusted_transport_id: str,
        datachannel_manager: Any,
    ) -> None:
        payload = getattr(message, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        request_message_id = ""
        trusted_owner_key = ""
        try:
            trusted_owner = self._room_bridge_trusted_owner(trusted_transport_id)
            trusted_owner_key = str(getattr(trusted_owner, "owner_key", "") or "").strip()
            request_message_id = normalize_message_id(
                getattr(getattr(message, "header", None), "message_id", "")
            )
            if str(payload.get("protocol") or "") != ROOM_BRIDGE_PROTOCOL:
                raise RoomBridgeError("unsupported_protocol", "unsupported room bridge protocol")
            event = str(payload.get("event") or "").strip().lower()
            if event == "grant_request":
                self._require_room_bridge_backend()
                issue = self.room_bridge_grants.issue(
                    trusted_transport_id=trusted_transport_id,
                    host_owner_key=trusted_owner_key,
                    server_identity_key=_runtime._get_server_identity_key(),
                    server_name=_runtime.get_configured_server_name(),
                    room_id=payload.get("room_id"),
                    room_epoch=payload.get("room_epoch"),
                    conversation_epoch=payload.get("conversation_epoch"),
                    permissions=payload.get("permissions"),
                )
                for superseded in issue.superseded_grants:
                    self._cancel_room_bridge_expiry(superseded.grant.grant_id)
                    self._end_call_listening(superseded.grant.room_id)
                cancel_revoked_tasks(
                    issue.superseded_grants,
                    current_task=_runtime.asyncio.current_task(),
                )
                for superseded in issue.superseded_grants:
                    prior_manager = self._datachannel_manager_for_session(
                        superseded.grant.current_transport_id,
                        require_send_message=True,
                    )
                    if prior_manager is not None:
                        await self._send_room_bridge_control(
                            prior_manager,
                            trusted_transport_id=superseded.grant.current_transport_id,
                            payload=revoked_control_payload(superseded),
                        )
                self._schedule_room_bridge_expiry(issue.grant)
                await self._send_room_bridge_control(
                    datachannel_manager,
                    trusted_transport_id=trusted_transport_id,
                    payload=granted_control_payload(
                        issue,
                        request_message_id=request_message_id,
                    ),
                )
                # The participant row follows the grant immediately: the moment
                # the Computer is admitted, everyone in the room can see that it
                # is there and what it may do. Sent as a separate control event
                # rather than folded into `granted` because the row changes
                # again when a call starts, and clients apply both the same way.
                await self._announce_room_bridge_presence(
                    datachannel_manager,
                    grant=issue.grant,
                    trusted_transport_id=trusted_transport_id,
                )
                self._begin_call_listening(
                    issue.grant, trusted_transport_id=trusted_transport_id
                )
                return
            if event == "revoke":
                revocation = self.room_bridge_grants.revoke_authorized(
                    grant_id=payload.get("grant_id"),
                    grant_token=payload.get("grant_token"),
                    trusted_transport_id=trusted_transport_id,
                    trusted_owner_key=trusted_owner_key,
                    room_id=payload.get("room_id"),
                    room_epoch=payload.get("room_epoch"),
                    conversation_epoch=payload.get("conversation_epoch"),
                    grant_revision=payload.get("grant_revision"),
                )
                self._cancel_room_bridge_expiry(revocation.grant.grant_id)
                self._end_call_listening(revocation.grant.room_id)
                cancel_revoked_tasks(
                    (revocation,),
                    current_task=_runtime.asyncio.current_task(),
                )
                await self._send_room_bridge_control(
                    datachannel_manager,
                    trusted_transport_id=trusted_transport_id,
                    payload=revoked_control_payload(revocation),
                )
                return
            raise RoomBridgeError("unsupported_event", "unsupported room bridge control event")
        except RoomBridgeError as exc:
            grant_context = self.room_bridge_grants.resolve_error_grant_context(
                error=exc,
                bridge_metadata=payload,
                trusted_transport_id=trusted_transport_id,
                trusted_owner_key=trusted_owner_key,
            )
            await self._send_room_bridge_control(
                datachannel_manager,
                trusted_transport_id=trusted_transport_id,
                payload=error_control_payload(
                    exc,
                    request_message_id=request_message_id,
                    grant_id=payload.get("grant_id"),
                    grant_context=grant_context,
                ),
            )

    @staticmethod
    def _room_bridge_message_from_cache(
        cached: Dict[str, Any],
        *,
        trusted_transport_id: str,
        server_name: str,
    ) -> SharedDataChannelMessage:
        return SharedDataChannelMessage(
            header=SharedMessageHeader(
                message_id=str(cached.get("message_id") or _runtime.uuid.uuid4()),
                message_type=SharedMessageType.CHAT,
                timestamp=_runtime.time.time(),
                session_id=str(trusted_transport_id or ""),
                user_id=str(server_name or "AutoYou Computer"),
            ),
            payload={
                "message": str(cached.get("message") or ""),
                "context": list(cached.get("context") or []),
                "metadata": dict(cached.get("metadata") or {}),
            },
        )

    async def _handle_room_bridge_chat(
        self,
        message: Any,
        *,
        trusted_transport_id: str,
        datachannel_manager: Any,
    ) -> None:
        payload = getattr(message, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        request_message_id = str(getattr(getattr(message, "header", None), "message_id", "") or "")
        raw_metadata = payload.get("metadata")
        raw_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        bridge_metadata = raw_metadata.get("room_bridge")
        admission: Optional[RoomBridgeAdmission] = None
        current_task = _runtime.asyncio.current_task()
        trusted_owner_key = ""
        try:
            trusted_owner = self._room_bridge_trusted_owner(trusted_transport_id)
            trusted_owner_key = str(getattr(trusted_owner, "owner_key", "") or "")
            if payload.get("context"):
                raise RoomBridgeError(
                    "unsupported_context",
                    "room bridge chat currently accepts payload.message only",
                )
            admission = self.room_bridge_grants.admit_chat(
                bridge_metadata=bridge_metadata,
                trusted_transport_id=trusted_transport_id,
                trusted_owner_key=trusted_owner_key,
                request_message_id=request_message_id,
                message=payload.get("message"),
                task=current_task,
            )
            self._cancel_room_bridge_tasks(admission.displaced_tasks)
            ack_sent = await self._send_room_bridge_control(
                datachannel_manager,
                trusted_transport_id=trusted_transport_id,
                payload=chat_ack_control_payload(admission),
            )
            if not ack_sent:
                self._suspend_room_bridge_transport(trusted_transport_id)
                return
            if admission.duplicate:
                if not self.room_bridge_grants.admission_is_current(admission):
                    return
                if admission.outcome_status == "completed" and admission.cached_final:
                    replay = self._room_bridge_message_from_cache(
                        admission.cached_final,
                        trusted_transport_id=trusted_transport_id,
                        server_name=_runtime.get_configured_server_name(),
                    )
                    await datachannel_manager.send_message(replay)
                elif admission.outcome_status == "cancelled":
                    await self._send_room_bridge_control(
                        datachannel_manager,
                        trusted_transport_id=trusted_transport_id,
                        payload=error_control_payload(
                            RoomBridgeError(
                                "turn_cancelled",
                                "the admitted turn was cancelled; send a new room_event_id",
                            ),
                            request_message_id=request_message_id,
                            grant_id=admission.grant.grant_id,
                            grant_context=admission.grant,
                        ),
                    )
                return

            execution_manager = _runtime.get_session_execution_manager()
            identity = execution_manager.resolve_transport_identity(
                "room",
                admission.grant.room_principal_id,
            )
            identity = _runtime._resolve_conversation_identity(identity)
            request_metadata = {
                "client": "autoyou-room-bridge",
                "source": "room_bridge",
                "client_prompt_id": admission.room_event_id,
                **_runtime._build_conversation_metadata(identity),
            }

            from rest_api import _sanitize_direct_llm_text, send_message_to_native_ollama

            async def _execute_room_turn() -> str:
                provider_task = _runtime.asyncio.current_task()
                if not self.room_bridge_grants.track_provider_task(admission, provider_task):
                    raise _runtime.asyncio.CancelledError
                try:
                    # Re-check at use time in case the operator changed providers
                    # after granting. Never fall through to the action-capable graph.
                    self._require_room_bridge_backend()
                    native_result = await send_message_to_native_ollama(
                        str(payload.get("message") or ""),
                        identity.canonical_session_id,
                        metadata=request_metadata,
                        system_prompt=ROOM_BRIDGE_READ_ONLY_SYSTEM_PROMPT,
                    )
                    reply = _sanitize_direct_llm_text(native_result.get("response"))
                    if not reply:
                        raise RuntimeError("read-only Ollama backend returned an empty response")
                    return reply
                finally:
                    self.room_bridge_grants.untrack_provider_task(admission, provider_task)

            try:
                room_reply = await execution_manager.submit_turn(
                    identity,
                    _execute_room_turn,
                    label="room-bridge-chat",
                )
                reply = bound_room_bridge_reply(room_reply)
                reply_source = "ai_agent"
            except RoomBridgeError:
                raise
            except _runtime.SessionTurnCancelledError:
                return
            except _runtime.SessionQueueFullError as exc:
                reply = bound_room_bridge_reply(exc.status.message)
                reply_source = "session_execution"
            except _runtime.SessionTurnTimeoutError as exc:
                reply = bound_room_bridge_reply(exc.status.message)
                reply_source = "session_execution"
            except _runtime.asyncio.CancelledError:
                return
            except Exception as exc:
                _runtime.LOGGER.warning("Room bridge AI turn failed: %s", exc)
                reply = "AutoYou Computer could not process that room message."
                reply_source = "error"

            response_metadata = {
                "source": reply_source,
                "original_message_id": request_message_id,
                "is_streaming": False,
                **_runtime._build_conversation_metadata(identity),
                "room_bridge": final_reply_room_metadata(admission),
            }
            response = _runtime.create_chat_message(
                message=reply,
                session_id=trusted_transport_id,
                user_id=_runtime.get_configured_server_name(),
                context=[],
                metadata=response_metadata,
            )
            response.header.message_id = str(response_metadata["room_bridge"]["room_event_id"])
            cached_final = {
                "message_id": response.header.message_id,
                "message": reply,
                "context": [],
                "metadata": response_metadata,
            }
            if not self.room_bridge_grants.cache_final(
                admission,
                cached_final,
                task=current_task,
            ):
                return
            if not self.room_bridge_grants.admission_is_current(admission):
                return
            await datachannel_manager.send_message(response)
        except RoomBridgeError as exc:
            grant_context = self.room_bridge_grants.resolve_error_grant_context(
                error=exc,
                bridge_metadata=bridge_metadata,
                trusted_transport_id=trusted_transport_id,
                trusted_owner_key=trusted_owner_key,
            )
            await self._send_room_bridge_control(
                datachannel_manager,
                trusted_transport_id=trusted_transport_id,
                payload=error_control_payload(
                    exc,
                    request_message_id=request_message_id,
                    grant_id=(bridge_metadata or {}).get("grant_id") if isinstance(bridge_metadata, dict) else "",
                    grant_context=grant_context,
                ),
            )
        finally:
            if admission is not None and not admission.duplicate:
                self.room_bridge_grants.abandon_chat(admission, task=current_task)

    async def _handle_chat_message(self, message: 'DataChannelMessage') -> None:
        """Handle chat messages received via datachannel."""
        session_id = getattr(getattr(message, "header", None), "session_id", None)
        message_id = getattr(getattr(message, "header", None), "message_id", None)
        try:
            content = message.payload.get("message", "")
            base_identity = self._resolve_chat_identity_for_message(message, session_id)
            metadata = message.payload.get("metadata", {"client": "autoyou-datachannel"})
            if not isinstance(metadata, dict):
                metadata = {"client": "autoyou-datachannel"}
            else:
                metadata = dict(metadata)
            metadata.setdefault("client", "autoyou-datachannel")
            conversation_action = str(metadata.get("conversation_action") or "").strip().lower()
            if conversation_action in {"cancel", "stop", "interrupt", "delete_server_history"}:
                identity = _runtime._resolve_conversation_identity(base_identity)
                execution_manager = _runtime.get_session_execution_manager()
                if conversation_action in {"cancel", "stop", "interrupt"}:
                    control_result = await execution_manager.cancel_turn(identity)
                    control_name = "prompt_cancelled"
                    response_text = (
                        "Stop requested. The active AutoYou request has been cancelled."
                        if control_result.get("cancelled")
                        else "There is no active AutoYou request to stop."
                    )
                else:
                    control_result = await _runtime._delete_server_conversation_history(identity)
                    control_name = "server_history_deleted" if control_result.get("deleted") else "server_history_delete_failed"
                    response_text = (
                        "AutoYou server conversation history was deleted. Configured AI providers may retain their own history."
                        if control_result.get("deleted")
                        else "AutoYou could not delete the server conversation history."
                    )

                current_task = _runtime.asyncio.current_task()
                for task_set in (self.session_reconnect_survivable_tasks, self.session_message_tasks):
                    for tracked_task in list(task_set.get(str(session_id or ""), set())):
                        if tracked_task is not current_task and not tracked_task.done():
                            tracked_task.cancel()

                response_message = _runtime.create_chat_message(
                    message=response_text,
                    session_id=session_id,
                    user_id=_runtime.get_configured_server_name(),
                    context=[],
                    metadata={
                        "source": "session_control",
                        "conversation_control": control_name,
                        "conversation_action": conversation_action,
                        "control_result": dict(control_result or {}),
                        "original_message_id": message_id,
                        **_runtime._build_conversation_metadata(identity),
                    },
                )
                datachannel_manager = self._datachannel_manager_for_session(
                    session_id,
                    require_send_message=True,
                )
                if datachannel_manager:
                    await datachannel_manager.send_message(response_message)
                return
            if conversation_action in {"rename", "rename_conversation"}:
                # Client -> server only. The name is kept for Chat & History and
                # is never pushed to this or any other client.
                identity = _runtime._resolve_conversation_identity(base_identity)
                control_result = _runtime._rename_server_conversation(identity, metadata)
                response_message = _runtime.create_chat_message(
                    message="",
                    session_id=session_id,
                    user_id=_runtime.get_configured_server_name(),
                    context=[],
                    metadata={
                        "source": "session_control",
                        "conversation_control": (
                            "conversation_renamed"
                            if control_result.get("renamed")
                            else "conversation_rename_failed"
                        ),
                        "conversation_action": "rename",
                        "control_result": dict(control_result or {}),
                        "original_message_id": message_id,
                    },
                )
                datachannel_manager = self._datachannel_manager_for_session(
                    session_id,
                    require_send_message=True,
                )
                if datachannel_manager:
                    await datachannel_manager.send_message(response_message)
                return
            if conversation_action in {"replace", "edit", "redo"}:
                # Edit/redo reuses the current conversation. Stop any still
                # running turn first and wait for its handler/SSE stream to
                # unwind before the replacement is submitted to the same
                # per-session execution queue.
                identity = _runtime._resolve_conversation_identity(base_identity)
                replace_cancel_result = await _runtime.get_session_execution_manager().cancel_turn(identity)
                if (
                    replace_cancel_result.get("active")
                    and not replace_cancel_result.get("active_stopped")
                ):
                    response_message = _runtime.create_chat_message(
                        message=(
                            "The active AutoYou request is still stopping. "
                            "Please wait a moment and send the edit again."
                        ),
                        session_id=session_id,
                        user_id=_runtime.get_configured_server_name(),
                        context=[],
                        metadata={
                            "source": "session_control",
                            "conversation_control": "conversation_replace_failed",
                            "conversation_action": "replace",
                            "control_result": dict(replace_cancel_result),
                            "original_message_id": message_id,
                            **_runtime._build_conversation_metadata(identity),
                        },
                    )
                    datachannel_manager = self._datachannel_manager_for_session(
                        session_id,
                        require_send_message=True,
                    )
                    if datachannel_manager:
                        await datachannel_manager.send_message(response_message)
                    return
                metadata["conversation_replace_cancel_result"] = dict(replace_cancel_result)
            start_new_thread, content, control_only, metadata = _runtime._extract_conversation_request(
                content,
                metadata,
            )
            # A display name is server-controlled history metadata, never arbitrary
            # client chat metadata.  Re-add the effective normalized name below only
            # when its explicit history setting permits it.
            metadata.pop("client_display_name", None)
            # Likewise who-is-who for Chat & History: only the admin chat route
            # marks the owner's own turns, and a relayed guest is described from
            # the validated relay route, never from a claim in the metadata.
            metadata.pop("admin_surface", None)
            metadata.pop("peer_relay", None)
            relay_presentation = self._relay_presentation(message, session_id)
            if relay_presentation:
                metadata["peer_relay"] = relay_presentation
            identity = _runtime._resolve_conversation_identity(
                base_identity,
                start_new_thread=start_new_thread,
            )
            metadata["canonical_owner_key"] = identity.owner_key
            reply_target = self._build_webrtc_reply_target(session_id, identity)
            if reply_target:
              metadata["reply_target"] = reply_target
            metadata.update(_runtime._build_conversation_metadata(identity, reset=start_new_thread))
            metadata.update(self.client_name_history_metadata(identity))
            context = message.payload.get("context", [])

            _runtime.LOGGER.info(
                "Received chat message from session %s (canonical=%s): %s",
                session_id,
                identity.canonical_session_id,
                content,
            )
            self._record_streaming_event(
                session_id=session_id,
                direction="inbound",
                channel="chat",
                text=content,
                source=str(metadata.get("source") or metadata.get("client") or "webrtc"),
                owner_key=identity.owner_key,
                canonical_session_id=identity.canonical_session_id,
                metadata=metadata,
            )
            if start_new_thread:
                await self._prime_conversation_context_status(session_id, identity)

            if start_new_thread and control_only:
                response_message = _runtime.create_chat_message(
                    message="Started a new conversation.",
                    session_id=session_id,
                    user_id=_runtime.get_configured_server_name(),
                    context=[],
                    metadata={
                        "source": "session_execution",
                        "original_message_id": message_id,
                        **_runtime._build_conversation_metadata(identity, reset=True),
                    },
                )
                datachannel_manager = self._datachannel_manager_for_session(
                    session_id,
                    require_send_message=True,
                )
                if datachannel_manager:
                    await datachannel_manager.send_message(response_message)
                return

            progress_state = {"last_sent_at": _runtime.time.monotonic()}
            execution_state = {"queue_position": 0}
            current_bubble = []

            async def _on_execution_status(status) -> None:
                execution_state["queue_position"] = int(getattr(status, "queue_position", 0) or 0)

            def _chat_delivery_session_ids() -> List[str]:
                candidates: List[str] = []
                for candidate in (
                    str(session_id or "").strip(),
                    self._resolve_voice_chat_session_id(session_id),
                ):
                    normalized_candidate = str(candidate or "").strip()
                    if normalized_candidate and normalized_candidate not in candidates:
                        candidates.append(normalized_candidate)
                return candidates

            async def _send_live_chat_message(chat_message: Any) -> bool:
                for delivery_session_id in _chat_delivery_session_ids():
                    datachannel_manager = self._datachannel_manager_for_session(
                        delivery_session_id,
                        require_send_message=True,
                    )
                    if not datachannel_manager:
                        continue
                    try:
                        if await datachannel_manager.send_message(chat_message):
                            return True
                    except Exception as exc:
                        _runtime.LOGGER.debug(
                            "Failed to send live WebRTC chat message to %s: %s",
                            delivery_session_id,
                            exc,
                        )
                return False

            async def _send_or_queue_terminal_chat_message(
                response_message: Any,
                *,
                label: str,
            ) -> bool:
                if await _send_live_chat_message(response_message):
                    return True

                stable_session_id = self._resolve_voice_chat_session_id(session_id)
                if stable_session_id:
                    self._enqueue_to_offline_queue(
                        stable_session_id,
                        response_message,
                        label=label,
                    )

            from rest_api import process_chat_message, ChatRequest
            execution_manager = _runtime.get_session_execution_manager()

            async def stream_chunk(chunk: dict):
                if chunk.get("_autoyou_progress"):
                    now = _runtime.time.monotonic()
                    if now - progress_state["last_sent_at"] < 12.0:
                        return
                    progress_state["last_sent_at"] = now
                    progress_text = str(chunk.get("progress_text") or "Still working...")
                    progress_response = _runtime.create_chat_message(
                        message=progress_text,
                        session_id=session_id,
                        user_id=_runtime.get_configured_server_name(),
                        context=[],
                        metadata={
                            "source": "ai_agent",
                            "original_message_id": message_id,
                            "is_streaming": True,
                            "is_progress": True,
                            **_runtime._build_conversation_metadata(identity, reset=start_new_thread),
                        }
                    )
                    await _send_live_chat_message(progress_response)
                    return

                parts = chunk.get("message", {}).get("parts", [])
                for p in parts:
                    if "text" in p:
                        current_bubble.append(p["text"])
                        partial_msg = "".join(current_bubble)

                        live_response = _runtime.create_chat_message(
                            message=partial_msg,
                            session_id=session_id,
                            user_id=_runtime.get_configured_server_name(),
                            context=[],
                            metadata={
                                "source": "ai_agent",
                                "original_message_id": message_id,
                                "is_streaming": True,
                                **_runtime._build_conversation_metadata(identity, reset=start_new_thread),
                            }
                        )
                        await _send_live_chat_message(live_response)

            async def _send_immediate_media_reply(attachments: List[Dict[str, Any]]) -> bool:
                try:
                    from shared.media_messaging import inline_context_for_client, public_media_reply_metadata

                    media_context = inline_context_for_client(attachments, source="media_reply")
                    if not media_context:
                        return False
                    media_metadata = {
                        "source": "media_reply",
                        "original_message_id": message_id,
                        "is_streaming": False,
                        "media_reply": public_media_reply_metadata(attachments),
                        **_runtime._build_conversation_metadata(identity, reset=start_new_thread),
                    }
                    media_message = _runtime.create_chat_message(
                        message="",
                        session_id=session_id,
                        user_id=_runtime.get_configured_server_name(),
                        context=media_context,
                        metadata=media_metadata,
                    )
                    return bool(
                        await _send_or_queue_terminal_chat_message(
                            media_message,
                            label="media reply",
                        )
                    )
                except Exception as media_reply_exc:
                    _runtime.LOGGER.warning("Failed to send immediate WebRTC media reply attachment: %s", media_reply_exc)
                    return False

            async def _execute_chat_request():
                request_metadata = dict(metadata)
                session_execution = dict(request_metadata.get("session_execution") or {})
                session_execution["queue_position"] = execution_state["queue_position"]
                request_metadata["session_execution"] = session_execution
                chat_req = ChatRequest(
                    message=content,
                    session_id=identity.canonical_session_id,
                    user_id=identity.canonical_user_id,
                    context=context,
                    metadata=request_metadata,
                )
                url = f"http://127.0.0.1:{_runtime.STATE.main_server_port}"
                return await process_chat_message(
                    chat_req,
                    ai_agent_url=url,
                    on_chunk=stream_chunk,
                    on_media_reply=_send_immediate_media_reply,
                )

            chat_resp = await execution_manager.submit_turn(
                identity,
                _execute_chat_request,
                on_status=_on_execution_status,
                label="webrtc-chat",
            )
            reply = chat_resp.response or "(no response)"
            response_metadata = dict(getattr(chat_resp, "metadata", {}) or {})
            if not isinstance(response_metadata, dict):
                response_metadata = {}
            try:
                from shared.voice_messaging import strip_private_voice_note_metadata

                response_metadata = strip_private_voice_note_metadata(response_metadata)
            except Exception:
                pass
            response_metadata.update(_runtime._build_chat_response_agent_metadata(chat_resp, response_metadata))
            response_metadata.update(
                {
                    "source": "ai_agent",
                    "original_message_id": message_id,
                    "is_streaming": False,
                    **_runtime._build_conversation_metadata(identity, reset=start_new_thread),
                }
            )
            response_context: List[Dict[str, Any]] = []
            media_reply_attachments = list(getattr(chat_resp, "media_reply_attachments", []) or [])
            if media_reply_attachments:
                try:
                    from shared.media_messaging import inline_context_for_client, public_media_reply_metadata

                    media_context = inline_context_for_client(media_reply_attachments, source="media_reply")
                    if media_context:
                        media_metadata = {
                            "source": "media_reply",
                            "original_message_id": message_id,
                            "is_streaming": False,
                            "media_reply": public_media_reply_metadata(media_reply_attachments),
                            **_runtime._build_conversation_metadata(identity, reset=start_new_thread),
                        }
                        media_message = _runtime.create_chat_message(
                            message="",
                            session_id=session_id,
                            user_id=_runtime.get_configured_server_name(),
                            context=media_context,
                            metadata=media_metadata,
                        )
                        await _send_or_queue_terminal_chat_message(
                            media_message,
                            label="media reply",
                        )
                except Exception as media_reply_exc:
                    _runtime.LOGGER.warning("Failed to send WebRTC media reply attachment: %s", media_reply_exc)
            voice_reply_audio_path = getattr(chat_resp, "voice_reply_audio_path", None)
            if voice_reply_audio_path:
                try:
                    from shared.voice_messaging import attachment_from_audio_file

                    voice_attachment = attachment_from_audio_file(
                        voice_reply_audio_path,
                        platform="webrtc",
                    )
                    if voice_attachment:
                        response_context = [{"attachments": [voice_attachment], "source": "voice_reply"}]
                        response_metadata["voice_note"] = {
                            **dict(response_metadata.get("voice_note") or {}),
                            "reply_audio_attached": True,
                            "reply_audio_filename": voice_attachment.get("filename"),
                            "reply_audio_mimetype": voice_attachment.get("mimetype"),
                        }
                except Exception as voice_attach_exc:
                    _runtime.LOGGER.warning("Failed to attach WebRTC voice-note reply audio: %s", voice_attach_exc)

            response_message = _runtime.create_chat_message(
                message=reply,
                session_id=session_id,
                user_id=_runtime.get_configured_server_name(),
                context=response_context,
                metadata=response_metadata,
            )
            self._record_streaming_event(
                session_id=session_id,
                direction="outbound",
                channel="chat",
                text=reply,
                source=str(response_metadata.get("source") or "ai_agent"),
                owner_key=identity.owner_key,
                canonical_session_id=identity.canonical_session_id,
                metadata=response_metadata,
            )

            try:
                chat_sent = bool(
                    await _send_or_queue_terminal_chat_message(
                        response_message,
                        label="voice-note reply" if response_context else "chat reply",
                    )
                )
            finally:
                if voice_reply_audio_path:
                    try:
                        from shared.voice_messaging import cleanup_paths as _vm_cleanup

                        _vm_cleanup(voice_reply_audio_path)
                    except Exception:
                        pass
            if chat_sent:
                context_usage = response_metadata.get("context_usage")
                if isinstance(context_usage, dict):
                    await self._publish_voice_call_status(
                        session_id,
                        {"context_usage": context_usage},
                    )
                _runtime.LOGGER.info("Sent chat response to session %s: %s", session_id, reply)
        except _runtime.SessionTurnCancelledError as e:
            _runtime.LOGGER.info("WebRTC chat cancelled for %s", session_id)
            return
        except _runtime.SessionQueueFullError as e:
            _runtime.LOGGER.info("WebRTC chat rejected for %s because the session queue is full", session_id)
            try:

                response_message = _runtime.create_chat_message(
                    message=e.status.message,
                    session_id=session_id,
                    user_id=_runtime.get_configured_server_name(),
                    context=[],
                    metadata={
                        "source": "session_execution",
                        "original_message_id": message_id,
                        **_runtime._build_conversation_metadata(identity, reset=start_new_thread),
                    },
                )
                await _send_or_queue_terminal_chat_message(
                    response_message,
                    label="chat reply",
                )
            except Exception as send_error:
                _runtime.LOGGER.error(f"Failed to send queue-full response: {send_error}")
        except _runtime.SessionTurnTimeoutError as e:
            _runtime.LOGGER.info("WebRTC chat timed out for %s", session_id)
            try:

                response_message = _runtime.create_chat_message(
                    message=e.status.message,
                    session_id=session_id,
                    user_id=_runtime.get_configured_server_name(),
                    context=[],
                    metadata={
                        "source": "session_execution",
                        "original_message_id": message_id,
                        **_runtime._build_conversation_metadata(identity, reset=start_new_thread),
                    },
                )
                await _send_or_queue_terminal_chat_message(
                    response_message,
                    label="chat reply",
                )
            except Exception as send_error:
                _runtime.LOGGER.error(f"Failed to send timeout response: {send_error}")
        except Exception as e:
            _runtime.LOGGER.error(f"Failed to handle chat message: {e}")
            try:
                error_response = _runtime.create_chat_message(
                    message=f"Error processing your message: {str(e)}",
                    session_id=session_id,
                    user_id=_runtime.get_configured_server_name(),
                    context=[],
                    metadata={
                        "source": "error",
                        "original_message_id": message_id,
                        **_runtime._build_conversation_metadata(identity, reset=start_new_thread),
                    }
                )

                await _send_or_queue_terminal_chat_message(
                    error_response,
                    label="chat reply",
                )
            except Exception as send_error:
                _runtime.LOGGER.error(f"Failed to send error response: {send_error}")

    async def _process_voice_command(
        self,
        session_id: str,
        text: str,
        transcript_already_mirrored: bool = False,
        identity: Any = None,
    ):
        """Process voice command text via chat API, speak, and mirror to chat UI."""
        voice_reply_message_id = str(_runtime.uuid.uuid4())

        # Resolve the client-facing session_id so that voice transcript/reply DC
        # messages and the downstream ADK request both stay on the same logical
        # client conversation after normal OTP pair rekeys the datachannel.
        raw_session_id = str(session_id or "").strip()
        dc_session_id = self._resolve_voice_chat_session_id(session_id)
        resolved_identity = _runtime._resolve_conversation_identity(self._resolve_chat_identity(dc_session_id))
        if identity is None:
            identity = resolved_identity
        else:
            current_owner_key = str(getattr(identity, "owner_key", "") or "").strip()
            resolved_owner_key = str(getattr(resolved_identity, "owner_key", "") or "").strip()
            if (
                dc_session_id
                and dc_session_id != raw_session_id
                and resolved_owner_key
                and current_owner_key != resolved_owner_key
            ):
                identity = resolved_identity

        async def _mirror_voice_feedback(message_text: str, *, is_progress: bool = False) -> None:
            try:
                feedback_message = _runtime.create_chat_message(
                    message=message_text,
                    session_id=dc_session_id,
                    user_id=_runtime.get_configured_server_name(),
                    context=[],
                    metadata={
                        "source": "voice_call",
                        "is_status": True,
                        "original_message_id": voice_reply_message_id,
                        "is_streaming": bool(is_progress),
                        "is_progress": bool(is_progress),
                        **_runtime._build_conversation_metadata(identity),
                    },
                )
                await self._deliver_voice_chat_message(
                    dc_session_id,
                    feedback_message,
                    label="voice status",
                )
            except Exception as dc_err:
                _runtime.LOGGER.warning(f"Failed to mirror voice status to datachannel for {session_id}: {dc_err}")

        try:
            _runtime.LOGGER.info(f"🎤 Processing voice command from {session_id}: {text}")
            self._record_streaming_event(
                session_id=dc_session_id,
                direction="inbound",
                channel="voice",
                text=text,
                source="voice_call",
                owner_key=identity.owner_key,
                canonical_session_id=identity.canonical_session_id,
                metadata={"is_transcription": True},
            )
            playback_status_before_turn = _runtime._get_voice_audio_playback_status(session_id)

            if not transcript_already_mirrored:
                try:
                    user_transcript = _runtime.create_chat_message(
                        message=text,
                        session_id=dc_session_id,
                        user_id=dc_session_id,
                        context=[],
                        metadata={
                            "source": "voice_call",
                            "is_transcription": True,
                            **_runtime._build_conversation_metadata(identity),
                        },
                    )
                    delivery_state = await self._deliver_voice_chat_message(
                        dc_session_id,
                        user_transcript,
                        label="voice transcript",
                    )
                    if delivery_state == "sent":
                        _runtime.LOGGER.info(f"Mirrored user STT to DataChannel: {text}")
                except Exception as dc_err:
                    _runtime.LOGGER.warning(f"Failed to mirror user STT to datachannel for {session_id}: {dc_err}")

            from rest_api import ChatRequest, process_chat_message

            execution_manager = _runtime.get_session_execution_manager()
            progress_state = {"last_sent_at": _runtime.time.monotonic()}
            execution_state = {"queue_position": 0}

            async def _on_execution_status(status) -> None:
                execution_state["queue_position"] = int(getattr(status, "queue_position", 0) or 0)

            async def _on_chunk(chunk: Dict[str, Any]) -> None:
                if not chunk.get("_autoyou_progress"):
                    return
                now = _runtime.time.monotonic()
                if now - progress_state["last_sent_at"] < 12.0:
                    return
                progress_state["last_sent_at"] = now
                progress_text = str(chunk.get("progress_text") or "Still working...")
                await _mirror_voice_feedback(progress_text, is_progress=True)

            async def _execute_chat_request():
                request_metadata: Dict[str, Any] = {
                    "source": "voice_call",
                    "client": "autoyou-datachannel",
                    "canonical_owner_key": identity.owner_key,
                    **_runtime._build_conversation_metadata(identity),
                    "session_execution": {
                        "queue_position": execution_state["queue_position"],
                    },
                }
                request_metadata.update(self.client_name_history_metadata(identity))
                reply_target = self._build_webrtc_reply_target(dc_session_id, identity)
                if reply_target:
                    request_metadata["reply_target"] = reply_target

                chat_req = ChatRequest(
                    message=text,
                    user_id=identity.canonical_user_id,
                    session_id=identity.canonical_session_id,
                    metadata=request_metadata,
                    context=[],
                )
                url = f"http://127.0.0.1:{_runtime.STATE.main_server_port}"
                return await process_chat_message(chat_req, ai_agent_url=url, on_chunk=_on_chunk)

            chat_resp = await execution_manager.submit_turn(
                identity,
                _execute_chat_request,
                on_status=_on_execution_status,
                label="webrtc-voice",
            )
            reply = chat_resp.response or "(no response)"
            _runtime.LOGGER.info(f"🤖 AI Response: {reply}")

            playback_status_after_turn = _runtime._get_voice_audio_playback_status(session_id)
            audio_playback_started = _runtime._voice_turn_started_audio_playback(
                playback_status_before_turn,
                playback_status_after_turn,
            )
            ai_audio_replies_enabled = _runtime._get_ai_audio_replies_enabled(cfg=(_runtime.STATE.config or {}))
            suppress_reply_tts = audio_playback_started or not ai_audio_replies_enabled

            if suppress_reply_tts:
                if audio_playback_started:
                    _runtime.LOGGER.info(
                        "Suppressing spoken voice reply for %s because outbound audio playback started.",
                        session_id,
                    )
                else:
                    _runtime.LOGGER.debug(
                        "Skipping spoken voice reply for %s because spoken AI replies are disabled.",
                        session_id,
                    )
            elif session_id in _runtime.STATE.audio_managers:
                _speak_audio_manager(_runtime.STATE.audio_managers[session_id], reply, context=text)
            else:
                _runtime.LOGGER.warning(f"AudioManager missing for {session_id}, cannot speak response")

            response_metadata = dict(getattr(chat_resp, "metadata", {}) or {})
            if not isinstance(response_metadata, dict):
                response_metadata = {}
            try:
                from shared.voice_messaging import strip_private_voice_note_metadata

                response_metadata = strip_private_voice_note_metadata(response_metadata)
            except Exception:
                pass
            response_metadata.update(_runtime._build_chat_response_agent_metadata(chat_resp, response_metadata))
            response_metadata.update(
                {
                    "source": "voice_call",
                    "original_message_id": voice_reply_message_id,
                    "is_streaming": False,
                    "voice_reply_tts_suppressed": bool(suppress_reply_tts),
                    **_runtime._build_conversation_metadata(identity),
                }
            )
            if suppress_reply_tts:
                response_metadata["voice_reply_tts_suppression_reason"] = (
                    "audio_playback_started" if audio_playback_started else "ai_audio_replies_disabled"
                )
            response_message = _runtime.create_chat_message(
                message=reply,
                session_id=dc_session_id,
                user_id=_runtime.get_configured_server_name(),
                context=[],
                metadata=response_metadata,
            )
            self._record_streaming_event(
                session_id=dc_session_id,
                direction="outbound",
                channel="voice",
                text=reply,
                source="voice_call",
                owner_key=identity.owner_key,
                canonical_session_id=identity.canonical_session_id,
                metadata=response_metadata,
            )
            delivery_state = await self._deliver_voice_chat_message(
                dc_session_id,
                response_message,
                label="voice reply",
            )
            context_usage = response_metadata.get("context_usage")
            if isinstance(context_usage, dict):
                await self._publish_voice_call_status(
                    dc_session_id,
                    {"context_usage": context_usage},
                )
            if delivery_state == "failed":
                _runtime.LOGGER.warning("No datachannel_manager for %s - voice reply could not be queued for chat UI", session_id)
        except _runtime.SessionQueueFullError as e:
            _runtime.LOGGER.info("Voice command rejected for %s because the session queue is full", session_id)
            if session_id in _runtime.STATE.audio_managers and _runtime._get_ai_audio_replies_enabled(cfg=(_runtime.STATE.config or {})):
                _runtime.STATE.audio_managers[session_id].speak(e.status.message)
            await _mirror_voice_feedback(e.status.message)
        except _runtime.SessionTurnTimeoutError as e:
            _runtime.LOGGER.info("Voice command timed out for %s", session_id)
            if session_id in _runtime.STATE.audio_managers and _runtime._get_ai_audio_replies_enabled(cfg=(_runtime.STATE.config or {})):
                _runtime.STATE.audio_managers[session_id].speak(e.status.message)
            await _mirror_voice_feedback(e.status.message)
        except Exception as e:
            _runtime.LOGGER.error(f"Voice command processing error for {session_id}: {e!r}")
            import traceback

            _runtime.LOGGER.error(traceback.format_exc())
            if session_id in _runtime.STATE.audio_managers and _runtime._get_ai_audio_replies_enabled(cfg=(_runtime.STATE.config or {})):
                _runtime.STATE.audio_managers[session_id].speak("Sorry, I encountered an error.")

    def _get_autoyou_forward_target_port(self) -> int:
        """Resolve the current AutoYou browser forward target from admin config."""
        return _runtime._get_autoyou_browser_forward_port(_runtime.STATE.config or {})

    def _get_autoyou_page_service_port(self) -> int:
        """Resolve the built-in AutoYou page service port regardless of custom forwarding."""
        return _runtime._get_autoyou_page_service_port(_runtime.STATE.config or {})

    def _resolve_autoyou_forward_url(self, raw_url: str, websocket: bool = False) -> str:
        """Resolve inbound browser URLs against the configured local forward target.

        Path-prefix routing (highest priority):
        - ``/agent/{name}/...`` → ``STATE.dynamic_agent_proxy_ports[name]`` with the prefix stripped.
          This allows scaffolded agents that run their own web servers to be reached through
          the WebRTC DataChannel browser from any connected iOS/Android/Python client.

        Reserved AutoYou UX routes:
        - ``/agent-frontends``, ``/api/agent-frontends``, and ``/api/agent-directory`` always
          resolve to the built-in page service, even when a custom forward target is configured.

        Fallback: route to the AutoYou Page port (``_get_autoyou_forward_target_port()``).
        """
        from urllib.parse import urlparse

        parsed = urlparse(raw_url or "/")
        if parsed.scheme in ("http", "https", "ws", "wss"):
            absolute_agent_name = _runtime._agent_name_from_browser_proxy_path(parsed.path or "/")
            if (
                absolute_agent_name
                and self._is_loopback_host(parsed.hostname)
                and _runtime._get_agent_frontend_enabled(absolute_agent_name, cfg=_runtime.STATE.config or {})
            ):
                path_with_query = parsed.path or "/"
                if parsed.query:
                    path_with_query = f"{path_with_query}?{parsed.query}"
                return self._resolve_autoyou_forward_url(path_with_query, websocket=websocket)
            normalized_absolute_url = _runtime._normalize_client_loopback_target(
                raw_url,
                proxy_target=f"http://127.0.0.1:{self._get_autoyou_forward_target_port()}",
                websocket=websocket,
                advertised_websites=_runtime._get_autoyou_advertised_websites(_runtime.STATE.config or {}),
            )
            if normalized_absolute_url:
                return normalized_absolute_url
            if websocket:
                # An empty result is the route policy's explicit denial. Do
                # not fall back to the original loopback URL and bypass the
                # WebSocket allowlist.
                return ""
            return raw_url

        path_only = parsed.path or raw_url or "/"
        if not path_only.startswith("/"):
            path_only = f"/{path_only}"
        query_suffix = f"?{parsed.query}" if parsed.query else ""
        path = f"{path_only}{query_suffix}"
        scheme = "ws" if websocket else "http"

        if (
            path_only == "/agent-frontends"
            or path_only.startswith("/agent-frontends/")
            or path_only == "/agent-websites"
            or path_only.startswith("/agent-websites/")
            or path_only == "/api/agent-frontends"
            or path_only == "/api/agent-websites"
            or path_only == "/api/agent-directory"
        ):
            if websocket:
                return ""
            target_port = self._get_autoyou_page_service_port()
            return f"{scheme}://127.0.0.1:{target_port}{path}"

        if _runtime._is_ai_agent_rest_api_path(path_only):
            if websocket:
                return ""
            target_port = _runtime._get_ai_agent_api_port()
            return f"{scheme}://127.0.0.1:{target_port}{path}"

        # ── /agent/{name}/ dynamic port routing ─────────────────────────────
        # Client browsers reach scaffolded agent UIs at /agent/<agent_name>/<rest>
        # The prefix is stripped so the upstream server sees only /<rest>.
        import re as _re
        agent_prefix_match = _re.match(r'^/agent/([^/]+)(/.*)?$', path_only)
        if agent_prefix_match:
            agent_name = _runtime.package_agent_name(agent_prefix_match.group(1))
            sub_path = agent_prefix_match.group(2) or "/"
            sub_path_with_query = f"{sub_path}{query_suffix}"
            proxy_ports: dict = getattr(_runtime.STATE, "dynamic_agent_proxy_ports", {}) or {}
            cfg = _runtime.STATE.config or {}
            if not _runtime._get_agent_frontend_enabled(agent_name, cfg=cfg):
                _runtime.LOGGER.info(
                    "_resolve_autoyou_forward_url: /agent/%s is disabled by frontend controls; falling through.",
                    agent_name,
                )
            else:
                if websocket and not _runtime._get_agent_frontend_websocket_enabled(
                    agent_name,
                    cfg=cfg,
                ):
                    _runtime.LOGGER.info(
                        "Refusing WS upgrade to agent %s: websocket_enabled is false on its website manifest.",
                        agent_name,
                    )
                    return ""
                route_mode = _runtime._get_agent_frontend_route_mode(agent_name, cfg=cfg)
                if route_mode == _runtime.AGENT_WEBSITE_ROUTE_MODE_PATH_PROXY:
                    target_port = self._get_autoyou_page_service_port()
                    _runtime.LOGGER.debug(
                        "_resolve_autoyou_forward_url: /agent/%s path-proxy -> %s://127.0.0.1:%d%s",
                        agent_name, scheme, target_port, path,
                    )
                    return f"{scheme}://127.0.0.1:{target_port}{path}"
                if agent_name in proxy_ports and proxy_ports[agent_name]:
                    target_port = int(proxy_ports[agent_name])
                    _runtime.LOGGER.debug(
                        "_resolve_autoyou_forward_url: /agent/%s → http://127.0.0.1:%d%s",
                        agent_name, target_port, sub_path_with_query,
                    )
                    return f"{scheme}://127.0.0.1:{target_port}{sub_path_with_query}"
        # ─────────────────────────────────────────────────────────────────────

        target_port = self._get_autoyou_forward_target_port()
        if websocket:
            # Path-only requests have no port for _normalize_client_loopback_target
            # to inspect, so normalize the synthesized target before opening a
            # tunnel. This keeps /ws consistent with absolute loopback URLs.
            normalized_path_target = _runtime._normalize_client_loopback_target(
                f"http://127.0.0.1:{target_port}{path}",
                proxy_target=f"http://127.0.0.1:{target_port}",
                websocket=True,
                advertised_websites=_runtime._get_autoyou_advertised_websites(
                    _runtime.STATE.config or {}
                ),
            )
            return normalized_path_target if normalized_path_target else ""
        return f"{scheme}://127.0.0.1:{target_port}{path}"

    def _validate_remote_forward_target(self, target_url: str, *, websocket: bool = False) -> None:
        from urllib.parse import urlsplit, urlunsplit

        parsed = urlsplit(target_url or "")
        allowed_schemes = {"ws", "wss"} if websocket else {"http", "https"}
        if parsed.scheme not in allowed_schemes:
            raise _runtime.UnsafeURLError("unsupported remote proxy scheme")
        if self._is_loopback_host(parsed.hostname):
            return
        validation_scheme = {
            "ws": "http",
            "wss": "https",
        }.get(parsed.scheme, parsed.scheme)
        _runtime.assert_safe_http_url(
            urlunsplit((validation_scheme, parsed.netloc, parsed.path, parsed.query, ""))
        )

    @staticmethod
    def _is_loopback_host(host: Optional[str]) -> bool:
        if not host:
            return False
        normalized = str(host).strip().strip("[]").lower()
        return normalized in {"localhost", "127.0.0.1", "::1"}

    @staticmethod
    def _normalized_origin_port(parsed_url: Any) -> Optional[int]:
        if getattr(parsed_url, "port", None) is not None:
            return parsed_url.port
        scheme = (getattr(parsed_url, "scheme", "") or "").lower()
        if scheme in ("http", "ws"):
            return 80
        if scheme in ("https", "wss"):
            return 443
        return None

    def _header_origin_matches_target(self, header_value: str, target_url: str) -> bool:
        from urllib.parse import urlparse

        parsed_header = urlparse(header_value or "")
        parsed_target = urlparse(target_url or "")
        if not (
            parsed_header.scheme
            and parsed_header.hostname
            and parsed_target.scheme
            and parsed_target.hostname
        ):
            return False

        header_scheme = parsed_header.scheme.lower()
        target_scheme = parsed_target.scheme.lower()
        allowed_schemes = {target_scheme}
        if target_scheme == "ws":
            allowed_schemes.add("http")
        elif target_scheme == "wss":
            allowed_schemes.add("https")
        if header_scheme not in allowed_schemes:
            return False

        header_host = parsed_header.hostname.lower()
        target_host = parsed_target.hostname.lower()

        # A WebRTC browser origin can be another loopback port or hostname
        # alias (for example localhost:8076 -> 127.0.0.1:8067). Forwarding
        # that origin to the page service makes its CSRF guard compare the
        # proxy authority with the target Host and reject an otherwise
        # authenticated request. Preserve browser origin metadata only when
        # its authority is exactly the authority of the local target; the
        # authenticated DataChannel and remote-access policy remain the actual
        # authorization boundary for tunneled requests.
        if self._normalized_origin_port(parsed_header) != self._normalized_origin_port(parsed_target):
            return False
        return header_host == target_host

    def _sanitize_forward_headers(
        self,
        headers: Dict[str, Any],
        target_url: str,
        websocket: bool = False,
    ) -> Dict[str, str]:
        sanitized: Dict[str, str] = {}
        hop_by_hop = {
            "connection",
            "proxy-connection",
            "keep-alive",
            "te",
            "trailer",
            "transfer-encoding",
        }
        if not websocket:
            hop_by_hop.add("upgrade")

        internal = {
            "content-length",
            "content-transfer-encoding",
            "host",
            "x-original-url",
            "x-target-url",
            "x-autoyou-ads-account-summary",
            *REMOTE_BROWSER_IDENTITY_HEADERS,
        }
        browser_metadata = {"upgrade-insecure-requests"}

        for raw_key, raw_value in (headers or {}).items():
            if raw_value is None:
                continue
            key = str(raw_key)
            value = str(raw_value)
            lower_key = key.lower()

            if lower_key in hop_by_hop or lower_key in internal or lower_key in browser_metadata:
                continue
            if lower_key.startswith("sec-fetch-") or lower_key.startswith("sec-ch-"):
                continue
            if lower_key in ("origin", "referer") and not self._header_origin_matches_target(value, target_url):
                continue

            sanitized[key] = value

        # A local target sees every forwarded request arrive from 127.0.0.1, so
        # this is how it tells a paired device from this computer.
        try:
            from urllib.parse import urlsplit as _urlsplit

            local_target = self._is_loopback_host(_urlsplit(str(target_url or "")).hostname)
        except Exception:
            local_target = False
        if local_target:
            sanitized[REMOTE_BROWSER_HEADER] = REMOTE_BROWSER_VIA_WEBRTC
        return sanitized

    def _agent_frontend_context_headers(
        self,
        request_path: str,
        target_url: str,
        session_id: str,
        source_headers: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        agent_name = _runtime._agent_name_from_browser_proxy_path(request_path)
        if not agent_name:
            try:
                from urllib.parse import urlsplit as _urlsplit

                parsed = _urlsplit(str(target_url or ""))
                target_port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
                for candidate_name, candidate_port in (_runtime.STATE.dynamic_agent_proxy_ports or {}).items():
                    if int(candidate_port or 0) == target_port:
                        agent_name = _runtime.package_agent_name(candidate_name)
                        break
            except Exception:
                agent_name = ""
        if not agent_name:
            return {}

        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return {}

        headers = {
            "X-AutoYou-Agent-Frontend": agent_name,
            "X-AutoYou-WebRTC-Session-Id": normalized_session_id,
            "X-AutoYou-Remote-Access-Role": _runtime._get_remote_browser_access_role(_runtime.STATE.config or {}),
            DEVICE_OWNERSHIP_HEADER: self.device_ownership_for_session(normalized_session_id),
        }
        try:
            identity = self._resolve_chat_identity(normalized_session_id)
            owner_key = str(getattr(identity, "owner_key", "") or "").strip()
            if owner_key:
                headers["X-AutoYou-WebRTC-Owner-Key"] = owner_key
        except Exception:
            pass
        return headers

    @staticmethod
    def _inject_agent_proxy_shim(html_bytes: bytes, prefix: str) -> bytes:
        """Inject <base> + JS shim into an HTML response so that all absolute-path
        requests fired by the page's JavaScript are automatically prefixed with
        the agent proxy path (e.g. /agent/admin_agent), making the admin UI work
        correctly when served through the WebRTC DataChannel."""
        import re as _re
        prefix_b = prefix.encode("utf-8")
        page_service_paths = (
            b"/agent-websites",
            b"/agent-frontends",
            b"/api/agent-websites",
            b"/api/agent-frontends",
            b"/api/agent-directory",
        )

        def _is_page_service_path(path: bytes) -> bool:
            return any(
                path == candidate or path.startswith(candidate + separator)
                for candidate in page_service_paths
                for separator in (b"/", b"?", b"#")
            )

        # Rewrite absolute-path HTML attributes (src=, href=, action=)
        def _make_attr_rewriter(quote: bytes) -> "_re.Pattern[bytes]":
            q = _re.escape(quote)
            return _re.compile(
                b"((?:src|href|action)\\s*=\\s*" + q + b")(/[^" + quote + b"]*)" + q,
                _re.IGNORECASE,
            )
        def _rewrite_attr(quote: bytes):
            def _sub(m: "_re.Match[bytes]") -> bytes:
                path = m.group(2)
                if (
                    path.startswith(b"//")
                    or path.startswith(prefix_b)
                    or path.startswith(b"/agent/")
                    or _is_page_service_path(path)
                ):
                    return m.group(0)
                return m.group(1) + prefix_b + path + quote
            return _sub
        for _q in (b'"', b"'"):
            html_bytes = _make_attr_rewriter(_q).sub(_rewrite_attr(_q), html_bytes)

        # Build and inject <base> + JS shim immediately after <head>
        viewport_injection = ""
        if not _re.search(
            rb'<meta\b[^>]*\bname\s*=\s*["\']viewport["\']',
            html_bytes[:8192],
            _re.IGNORECASE,
        ):
            viewport_injection = '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        shim = (
            viewport_injection
            + f'<base href="{prefix}/">'
            f'<script>(function(){{'
            f'var _B="{prefix}";'
            f'function _g(u){{return /^(?:\\/agent-websites|\\/agent-frontends|\\/api\\/agent-websites|\\/api\\/agent-frontends|\\/api\\/agent-directory)(?:[\\/?#]|$)/.test(u);}}'
            f'function _r(u){{'
            f'if(typeof u==="string"&&u.startsWith("/")&&!u.startsWith(_B)&&!u.startsWith("//")&&!u.startsWith("/agent/")&&!_g(u))'
            f'{{return _B+u;}}return u;}}'
            f'var _f=window.fetch;'
            f'window.fetch=function(u,o){{return _f.call(this,_r(u),o);}};'
            f'var _x=XMLHttpRequest.prototype.open;'
            f'XMLHttpRequest.prototype.open=function(){{'
            f'var a=[].slice.call(arguments);a[1]=_r(a[1]);return _x.apply(this,a);}};'
            f'try{{var _la=location.assign.bind(location);'
            f'location.assign=function(u){{return _la(_r(u));}};}}catch(_){{}}'
            f'try{{var _lrp=location.replace.bind(location);'
            f'location.replace=function(u){{return _lrp(_r(u));}};}}catch(_){{}}'
            f'var _sa=Element.prototype.setAttribute;'
            f'Element.prototype.setAttribute=function(n,v){{'
            f'if((n==="src"||n==="href"||n==="action")&&typeof v==="string")v=_r(v);'
            f'return _sa.call(this,n,v);}};'
            f'function _pd(C,n){{try{{if(!C||!C.prototype)return;'
            f'var p=C.prototype,d=null,c=p;while(c&&!d){{d=Object.getOwnPropertyDescriptor(c,n);c=Object.getPrototypeOf(c);}}'
            f'if(!d||!d.configurable||!d.set)return;'
            f'Object.defineProperty(p,n,{{configurable:true,enumerable:d.enumerable,'
            f'get:function(){{return d.get?d.get.call(this):this.getAttribute(n);}},'
            f'set:function(v){{return d.set.call(this,_r(v));}}}});}}catch(_){{}}}}'
            f'_pd(typeof HTMLMediaElement==="undefined"?null:HTMLMediaElement,"src");'
            f'_pd(typeof HTMLSourceElement==="undefined"?null:HTMLSourceElement,"src");'
            f'_pd(typeof HTMLImageElement==="undefined"?null:HTMLImageElement,"src");'
            f'_pd(typeof HTMLScriptElement==="undefined"?null:HTMLScriptElement,"src");'
            f'_pd(typeof HTMLIFrameElement==="undefined"?null:HTMLIFrameElement,"src");'
            f'_pd(typeof HTMLAnchorElement==="undefined"?null:HTMLAnchorElement,"href");'
            f'_pd(typeof HTMLLinkElement==="undefined"?null:HTMLLinkElement,"href");'
            f'_pd(typeof HTMLFormElement==="undefined"?null:HTMLFormElement,"action");'
            f'}})();</script>'
        )
        shim_bytes = shim.encode("utf-8")
        lower = html_bytes[:4096].lower()
        head_open = lower.find(b"<head>")
        if head_open != -1:
            insert_at = head_open + len(b"<head>")
            return html_bytes[:insert_at] + shim_bytes + html_bytes[insert_at:]
        head_close = lower.find(b"</head>")
        if head_close != -1:
            return html_bytes[:head_close] + shim_bytes + html_bytes[head_close:]
        return shim_bytes + html_bytes

    async def _send_remote_access_denied(
        self,
        message: 'DataChannelMessage',
        request_id: str,
        method: str,
        url: str,
        *,
        websocket: bool = False,
    ) -> None:
        session_id = message.header.session_id
        datachannel_manager = self._datachannel_manager_for_session(session_id, require_send_message=True)
        if not datachannel_manager:
            return
        role = _runtime._get_remote_browser_access_role(_runtime.STATE.config or {})
        try:
            from urllib.parse import urlparse as _urlparse
            request_path = _urlparse(url).path or "/"
        except Exception:
            request_path = str(url or "/")
        reason = _runtime.remote_access_denial_message(role, method, request_path, websocket=websocket)
        if websocket:
            await datachannel_manager.send_message(
                _runtime.create_http_ws_upgrade_message(
                    request_id=request_id,
                    status="failed",
                    url=url,
                    session_id=session_id,
                    user_id=message.header.user_id,
                )
            )
            return
        await datachannel_manager.send_message(
            _runtime.create_http_response_message(
                status_code=403,
                headers={"Content-Type": "application/json"},
                body=_runtime.json.dumps({"success": False, "error": reason, "remote_access_role": role}),
                request_id=request_id,
                session_id=session_id,
                user_id=message.header.user_id,
                compressed=False,
            )
        )

    async def _handle_http_request(self, message: 'DataChannelMessage') -> None:
        """Handle HTTP requests received via datachannel with compression optimization."""
        try:
            # Extract HTTP request details from payload
            method = str(message.payload.get("method") or "GET").strip().upper() or "GET"
            url = message.payload.get("url", "/")
            headers = message.payload.get("headers", {})
            body = message.payload.get("body")
            request_id = message.payload.get("request_id") or message.header.message_id
            is_compressed = message.payload.get("compressed", False)
            body_base64 = bool(message.payload.get("body_base64", False))
            # Detect SSE requests strictly via payload hint or Accept header only
            # Path-based detection (e.g., "/run_sse") is removed to align with
            # aggregated POST side-channel and header-only semantics.
            accept_header = headers.get("Accept", headers.get("accept", ""))
            sse_requested = (
                bool(message.payload.get("sse"))
                or ("text/event-stream" in accept_header)
            )

            try:
                from urllib.parse import urlparse as _urlparse
                request_path = _urlparse(url).path or "/"
            except Exception:
                request_path = str(url or "/")

            upgrade_header = headers.get("Upgrade", headers.get("upgrade", "")).lower()
            websocket_upgrade = upgrade_header == "websocket"
            access_role = _runtime._get_remote_browser_access_role(_runtime.STATE.config or {})
            if not _runtime.remote_http_request_allowed(access_role, method, request_path, websocket=websocket_upgrade):
                await self._send_remote_access_denied(
                    message,
                    request_id,
                    method,
                    url,
                    websocket=websocket_upgrade,
                )
                return

            if request_path == "/api/v1/remote-desktop-settings":
                status_code = 200
                settings = {}
                if method not in {"GET", "POST"}:
                    status_code = 405
                elif method == "POST":
                    try:
                        raw = _runtime._decode_datachannel_http_body(
                            body, compressed=is_compressed, body_base64=body_base64,
                        )
                        if raw is None or len(raw) > 4096:
                            raise ValueError("Invalid settings body")
                        changes = _runtime.json.loads(raw)
                        allowed = {
                            "computer_sound", "control_enabled", "game_enabled",
                            "location_recording_enabled", "voice_call_recording_enabled",
                            "video_call_recording_enabled",
                        }
                        if (not isinstance(changes, dict) or not changes
                                or set(changes) - allowed
                                or any(type(value) is not bool for value in changes.values())):
                            raise ValueError("Invalid settings fields")
                        current = _runtime.STATE.config or {}
                        video_cfg = _runtime._get_video_call_config(cfg=current)
                        remote_cfg = video_cfg.get("remote_desktop") or {}
                        if (changes.get("game_enabled") is True
                                and not changes.get("control_enabled", remote_cfg.get("control_enabled", False))):
                            raise ValueError("Game mode requires screen control")
                        video_patch = {}
                        remote_patch = {key: changes[key] for key in ("control_enabled", "game_enabled") if key in changes}
                        if changes.get("control_enabled") is False:
                            remote_patch["game_enabled"] = False
                        if remote_patch:
                            video_patch["remote_desktop"] = remote_patch
                        for field, config_field in (
                            ("location_recording_enabled", "location_recording_enabled"),
                            ("video_call_recording_enabled", "record_my_video"),
                        ):
                            if field in changes:
                                video_patch[config_field] = changes[field]
                        if "computer_sound" in changes:
                            sources = [source for source in _runtime._get_video_audio_sources(cfg=current)
                                       if source != "speaker_loopback"]
                            if changes["computer_sound"]:
                                sources.append("speaker_loopback")
                            video_patch["audio_sources"] = sources
                        patch = {"video_call": video_patch} if video_patch else {}
                        if "voice_call_recording_enabled" in changes:
                            speech = dict(current.get("speech") or {})
                            training = dict(speech.get("voice_training") or {})
                            training["capture_enabled"] = changes["voice_call_recording_enabled"]
                            speech["voice_training"] = training
                            patch["speech"] = speech
                        await _runtime._apply_admin_ui_config_update(patch)
                    except (ValueError, TypeError, UnicodeError):
                        status_code = 400
                if status_code == 200:
                    current = _runtime.STATE.config or {}
                    video_cfg = _runtime._get_video_call_config(cfg=current)
                    remote_cfg = video_cfg.get("remote_desktop") or {}
                    settings = {
                        "computer_sound": "speaker_loopback" in _runtime._get_video_audio_sources(cfg=current),
                        "control_enabled": bool(remote_cfg.get("control_enabled", False)),
                        "game_enabled": bool(remote_cfg.get("game_enabled", False)),
                        "location_recording_enabled": bool(video_cfg.get("location_recording_enabled", False)),
                        "voice_call_recording_enabled": bool((current.get("speech") or {}).get("voice_training", {}).get("capture_enabled", False)),
                        "video_call_recording_enabled": bool(video_cfg.get("record_my_video", False)),
                        "role": access_role,
                    }
                datachannel_manager = self._datachannel_manager_for_session(
                    message.header.session_id, require_send_message=True,
                )
                if datachannel_manager:
                    await datachannel_manager.send_message(_runtime.create_http_response_message(
                        status_code=status_code,
                        headers={"Content-Type": "application/json", "Cache-Control": "no-store"},
                        body=_runtime.json.dumps(settings, separators=(",", ":")),
                        request_id=request_id,
                        session_id=message.header.session_id,
                        user_id=message.header.user_id,
                        compressed=False,
                    ))
                return

            if request_path == "/api/v1/games/hosted/start":
                status_code = 200
                result = {"success": True}
                if method != "POST":
                    status_code, result = 405, {"success": False, "error": "Use POST to start a hosted game."}
                elif not _runtime._get_game_mode_available(cfg=(_runtime.STATE.config or {})):
                    status_code, result = 409, {"success": False, "error": "Enable Game mode in computer settings first."}
                elif not self.game_input_hub.connected:
                    game_url = f"http://127.0.0.1:{_runtime.ADMIN_WEB_SERVICE_PORT}/api/webrtc/hosted-game/play"
                    try:
                        opened = await _runtime.asyncio.to_thread(webbrowser.open_new, game_url)
                    except Exception:
                        opened = False
                    if opened:
                        for _ in range(40):
                            if self.game_input_hub.connected:
                                break
                            await _runtime.asyncio.sleep(0.2)
                    if not opened or not self.game_input_hub.connected:
                        status_code, result = 503, {
                            "success": False,
                            "error": "Could not open the hosted game on the computer. Open Neon Horizon there and try again.",
                        }
                datachannel_manager = self._datachannel_manager_for_session(
                    message.header.session_id, require_send_message=True,
                )
                if datachannel_manager:
                    await datachannel_manager.send_message(_runtime.create_http_response_message(
                        status_code=status_code,
                        headers={"Content-Type": "application/json", "Cache-Control": "no-store"},
                        body=_runtime.json.dumps(result, separators=(",", ":")),
                        request_id=request_id,
                        session_id=message.header.session_id,
                        user_id=message.header.user_id,
                        compressed=False,
                    ))
                return

            if request_path == "/api/v1/games" or request_path.startswith("/api/v1/games/"):
                status_code = 200 if method == "GET" else 405
                response_headers = {"Content-Type": "application/json", "Cache-Control": "no-store"}
                response_body = ""
                compressed = False
                game_dirs = (
                    _runtime.APP_ROOT / "assets" / "game",
                    _runtime.get_mutable_data_dir("AutoYou", anchor=_runtime.__file__) / "mobile_games",
                )

                def load_game(path):
                    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MOBILE_GAME_MAX_BYTES - len(_MOBILE_GAME_CSP):
                        raise ValueError("Game asset is unavailable")
                    html = path.read_bytes()
                    if len(html) > _MOBILE_GAME_MAX_BYTES - len(_MOBILE_GAME_CSP):
                        raise ValueError("Game asset exceeds the mobile download limit")
                    html.decode("utf-8")
                    head = re.search(br"<head(?=[\s>])[^>]*>", html[:4096], re.IGNORECASE)
                    if head is None:
                        raise ValueError("Game HTML needs a head element")
                    return html[:head.end()] + _MOBILE_GAME_CSP + html[head.end():]

                if status_code == 200:
                    if request_path == "/api/v1/games":
                        available_games = {}
                        for game_dir in game_dirs:
                            for game_file in sorted(game_dir.glob("*.html"))[:64]:
                                game_id = game_file.stem
                                if (not game_id or len(game_id) > 64
                                        or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in game_id)):
                                    continue
                                try:
                                    load_game(game_file)
                                except (OSError, ValueError, UnicodeError):
                                    continue
                                available_games[game_id] = game_file
                        games = [{"id": game_id, "title": game_id.replace("-", " ").title()}
                                 for game_id in sorted(available_games)[:64]]
                        response_body = _runtime.json.dumps({"games": games}, separators=(",", ":"))
                    else:
                        game_id = request_path[len("/api/v1/games/"):]
                        if (not game_id or len(game_id) > 64
                                or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in game_id)):
                            status_code = 404
                        else:
                            for game_dir in reversed(game_dirs):
                                try:
                                    game_html = load_game(game_dir / f"{game_id}.html")
                                except (OSError, ValueError, UnicodeError):
                                    continue
                                response_headers["Content-Type"] = "text/html; charset=utf-8"
                                encoded = _runtime.encode_http_proxy_response(game_html, headers=response_headers)
                                response_headers, response_body, compressed = encoded.headers, encoded.body, encoded.compressed
                                break
                            else:
                                status_code = 404
                datachannel_manager = self._datachannel_manager_for_session(
                    message.header.session_id, require_send_message=True,
                )
                if datachannel_manager:
                    await datachannel_manager.send_message(_runtime.create_http_response_message(
                        status_code=status_code,
                        headers=response_headers,
                        body=response_body,
                        request_id=request_id,
                        session_id=message.header.session_id,
                        user_id=message.header.user_id,
                        compressed=compressed,
                    ))
                return

            if method.upper() == "GET" and request_path in {"/api/v1/status", "/api/v1/server-config"}:
                local_payload = (
                    _runtime._build_browser_status_payload()
                    if request_path == "/api/v1/status"
                    else _runtime._build_browser_server_config_payload()
                )
                browser_status_body = _runtime.json.dumps(local_payload, separators=(",", ":"))
                response_message = _runtime.create_http_response_message(
                    status_code=200,
                    headers={
                        "Content-Type": "application/json",
                        "Cache-Control": "no-store",
                        "Pragma": "no-cache",
                        "Expires": "0",
                    },
                    body=browser_status_body,
                    request_id=request_id,
                    session_id=message.header.session_id,
                    user_id=message.header.user_id,
                    compressed=False,
                )
                datachannel_manager = self._datachannel_manager_for_session(
                    message.header.session_id,
                    require_send_message=True,
                )
                if datachannel_manager:
                    await datachannel_manager.send_message(response_message)
                return

            if method == "POST" and request_path.rstrip("/") == "/agent/location_agent/api/locations" and not _runtime._location_recording_available(cfg=(_runtime.STATE.config or {})):
                datachannel_manager = self._datachannel_manager_for_session(
                    message.header.session_id,
                    require_send_message=True,
                )
                if datachannel_manager:
                    await datachannel_manager.send_message(_runtime.create_http_response_message(
                        status_code=403,
                        headers={"Content-Type": "application/json"},
                        body='{"success":false,"error":"Location recording is off."}',
                        request_id=request_id,
                        session_id=message.header.session_id,
                        user_id=message.header.user_id,
                        compressed=False,
                    ))
                return

            # Detect WebSocket upgrade requests
            if websocket_upgrade:
                _runtime.LOGGER.info(f"WebSocket upgrade detected for {url} (ID: {request_id})")
                await self._handle_ws_upgrade(message, url, headers, request_id)
                return

            try:
                content_bytes = _runtime._decode_datachannel_http_body(
                    body,
                    compressed=is_compressed,
                    body_base64=body_base64,
                )
                if is_compressed and content_bytes is not None:
                    _runtime.LOGGER.info(f"Decompressed HTTP request body for {method} {url}")
            except Exception as e:
                _runtime.LOGGER.error(f"Failed to decode HTTP request body: {e}")
                raise ValueError("Invalid HTTP request body encoding") from e
            try:
                for k in list(headers.keys()):
                    if k.lower() == "content-transfer-encoding":
                        headers.pop(k, None)
            except Exception:
                pass

            # Resolve absolute vs. relative URLs against the configured local forward target.
            target_url = self._resolve_autoyou_forward_url(url, websocket=False)
            self._validate_remote_forward_target(target_url)
            headers = self._sanitize_forward_headers(headers, target_url, websocket=False)
            headers.update(
                self._agent_frontend_context_headers(
                    request_path,
                    target_url,
                    str(message.header.session_id or ""),
                    source_headers=message.payload.get("headers", {}),
                )
            )

            _runtime.LOGGER.info(f"Forwarding HTTP {method} {url} to {target_url} (ID: {request_id}, compressed: {is_compressed})")

            # Use persistent HTTP client with connection pooling for better performance
            if not hasattr(self, '_http_client'):
                # Create persistent HTTP client with optimized settings
                self._http_client = _runtime.httpx.AsyncClient(
                    transport=_runtime.build_safe_httpx_transport(
                        http2=True,
                        limits=_runtime.httpx.Limits(max_keepalive_connections=20, max_connections=100),
                        allow_loopback=True,
                    ),
                    timeout=_runtime.httpx.Timeout(30.0, connect=5.0),
                    limits=_runtime.httpx.Limits(max_keepalive_connections=20, max_connections=100),
                    http2=True,
                    trust_env=False,
                )

            # Prepare request parameters
            request_kwargs = {
                "url": target_url,
                "headers": headers,
                "timeout": _runtime._get_datachannel_http_request_timeout_seconds(request_path),
            }

            # Add body for methods that support it
            if method in ["POST", "PUT", "PATCH", "QUERY"] and (content_bytes is not None):
                request_kwargs["content"] = content_bytes

            # If SSE is requested, stream the response as SSE events over datachannel
            if sse_requested:

                # Initiate streaming request
                stream_ended = False
                aborted_by_client = False
                datachannel_manager = self._datachannel_manager_for_session(
                    message.header.session_id,
                    require_send_message=True,
                )
                try:
                    async with self._http_client.stream(method, **request_kwargs) as resp:
                        try:
                            if datachannel_manager and hasattr(datachannel_manager, 'send_message'):
                                # Sanitize upstream headers to avoid invalid chunked encoding on client
                                raw_h = dict(resp.headers)
                                safe_h: dict[str, Any] = {}
                                drop = {"content-length", "transfer-encoding", "content-encoding"}
                                for k, v in raw_h.items():
                                    lk = k.lower()
                                    if lk in drop:
                                        continue
                                    if lk == "content-type":
                                        safe_h["Content-Type"] = v
                                    else:
                                        safe_h[k] = v
                                start_msg = _runtime.create_http_sse_start_message(
                                    request_id=request_id,
                                    headers=safe_h,
                                    session_id=message.header.session_id,
                                    user_id=message.header.user_id,
                                )
                                await datachannel_manager.send_message(start_msg)
                            else:
                                aborted_by_client = True
                                _runtime.LOGGER.error(
                                    f"DataChannel manager not available for session {message.header.session_id} - available: {list(self.datachannel_managers.keys())}"
                                )
                        except Exception as send_err:
                            aborted_by_client = True
                            _runtime.LOGGER.warning(f"SSE start send failed; aborting stream {request_id}: {send_err}")
                            try:
                                await resp.aclose()
                            except Exception:
                                pass
                            # Continue to finally block to signal END

                        # Stream upstream response content as SSE events using line-based parsing
                        MAX_EVENT_BYTES = 16384  # keep payload comfortably below common SCTP message limits

                        async def _send_event_payload(text: str) -> None:
                            """Send payload text as one or more SSE_EVENT messages respecting size limits."""
                            nonlocal aborted_by_client
                            if not (datachannel_manager and hasattr(datachannel_manager, 'send_message')):
                                aborted_by_client = True
                                return
                            raw = text.encode("utf-8")
                            cursor = 0
                            total = len(raw)
                            while cursor < total:
                                end = min(cursor + MAX_EVENT_BYTES, total)
                                # try to cut at a newline boundary for readability
                                nl = raw.rfind(b"\n", cursor, end)
                                if nl != -1 and nl - cursor > MAX_EVENT_BYTES // 2:
                                    end = nl + 1
                                piece = raw[cursor:end].decode("utf-8", errors="ignore")
                                event_msg = _runtime.create_http_sse_event_message(
                                    request_id=request_id,
                                    data=piece,
                                    event=None,
                                    session_id=message.header.session_id,
                                    user_id=message.header.user_id,
                                )
                                try:
                                    await datachannel_manager.send_message(event_msg)
                                except Exception as send_err:
                                    aborted_by_client = True
                                    _runtime.LOGGER.debug(f"SSE event send failed; aborting stream {request_id}: {send_err}")
                                    return
                                cursor = end

                        # Support both proper SSE framing and newline-delimited JSON/token streams.
                        # If upstream provides SSE (data:/event:/id:/retry: with blank line separators),
                        # buffer until empty line. Otherwise, flush each non-empty line as an event
                        # to ensure timely token streaming in dev-ui.
                        event_lines: list[str] = []
                        async for line in resp.aiter_lines():
                            if aborted_by_client:
                                try:
                                    await resp.aclose()
                                except Exception:
                                    pass
                                break
                            # Empty line indicates end-of-event for SSE-framed upstream
                            if line == "":
                                if event_lines:
                                    await _send_event_payload("\n".join(event_lines))
                                    event_lines.clear()
                                # Ignore keepalive blank lines
                                continue
                            # If the upstream looks like SSE-framed, accumulate until separator
                            if (
                                line.startswith("data:")
                                or line.startswith(":")
                                or line.startswith("event:")
                                or line.startswith("id:")
                                or line.startswith("retry:")
                            ):
                                event_lines.append(line)
                                continue
                            # Otherwise treat as newline-delimited payload (e.g., model tokens)
                            # Flush immediately to deliver incremental updates to the browser
                            await _send_event_payload(line)

                        # Flush any trailing SSE-framed event
                        if event_lines and not aborted_by_client:
                            await _send_event_payload("\n".join(event_lines))
                except Exception as stream_err:
                    _runtime.LOGGER.warning(f"Upstream SSE streaming error for {method} {url} (ID: {request_id}): {stream_err}")
                finally:
                    # Signal end of stream exactly once
                    if not stream_ended and not aborted_by_client:
                        end_msg = _runtime.create_http_sse_end_message(
                            request_id=request_id,
                            session_id=message.header.session_id,
                            user_id=message.header.user_id,
                        )
                        if datachannel_manager and hasattr(datachannel_manager, 'send_message'):
                            try:
                                await datachannel_manager.send_message(end_msg)
                            except Exception as end_err:
                                _runtime.LOGGER.debug(f"SSE end signaling skipped/failed for {request_id}: {end_err}")
                        stream_ended = True

                _runtime.LOGGER.info(f"HTTP SSE {method} {url} streamed (ID: {request_id})")
                return

            # Make the HTTP request and stream via datachannel (non-SSE)
            # Acquire datachannel manager once
            session_id = message.header.session_id
            user_id = message.header.user_id
            datachannel_manager = self._datachannel_manager_for_session(session_id, require_send_message=True)

            # Open upstream stream and forward chunks over datachannel
            # Accumulators for upstream response
            response_text_accumulator: list[str] = []
            response_bytes_accumulator = bytearray()
            compressed_response = False
            raw_headers: list[tuple[str, str]] = []
            response_headers: dict[str, str] = {}
            seq_no: int = 0

            async def _send_stream_frame_or_raise(frame_message, frame_name: str) -> None:
                if not datachannel_manager or not hasattr(datachannel_manager, 'send_message'):
                    raise RuntimeError(f"HTTP stream transport unavailable during {frame_name}")
                send_ok = await datachannel_manager.send_message(frame_message)
                if not send_ok:
                    raise RuntimeError(f"HTTP stream stalled during {frame_name}")

            try:
                async with self._http_client.stream(method, **request_kwargs) as resp:
                    # Prepare and send stream-open with status and headers
                    try:
                        response_headers = dict(resp.headers)
                        raw = getattr(resp.headers, "raw", None)
                        if raw:
                            raw_headers = [
                                (k.decode("latin-1"), v.decode("latin-1")) for k, v in raw
                            ]
                    except Exception:
                        response_headers = dict(resp.headers)
                        raw_headers = []

                    upstream_response_headers = dict(response_headers)
                    stream_transport_headers = {
                        "connection",
                        "keep-alive",
                        "proxy-authenticate",
                        "proxy-authorization",
                        "proxy-connection",
                        "te",
                        "trailer",
                        "transfer-encoding",
                        "upgrade",
                        "content-transfer-encoding",
                    }
                    stream_open_excluded_headers = stream_transport_headers | {
                        "content-length",
                        "content-encoding",
                    }
                    stream_open_headers: dict[str, str] = {}
                    for key, value in response_headers.items():
                        lower_key = str(key).lower()
                        if lower_key in stream_open_excluded_headers:
                            continue
                        if lower_key == "content-type":
                            stream_open_headers["Content-Type"] = str(value)
                        else:
                            stream_open_headers[str(key)] = str(value)
                    stream_open_raw_headers = [
                        (key, value)
                        for key, value in raw_headers
                        if str(key).lower() not in stream_transport_headers
                    ]

                    # Decide content-type handling (text vs. binary)
                    ct = (upstream_response_headers.get("Content-Type") or upstream_response_headers.get("content-type") or "").lower()
                    is_textual = (
                        ct.startswith("text/")
                        or "application/json" in ct
                        or "application/javascript" in ct
                        or "application/xml" in ct
                        or "image/svg+xml" in ct
                        or "charset=" in ct
                    )

                    # Detect agent HTML responses: shim must be injected so that
                    # the admin UI JS routes absolute-path API calls through
                    # /agent/<name>/ instead of directly to the page service.
                    import re as _re_ah
                    _agent_html_m = _re_ah.match(r'^/agent/([^/?#]+)', url or '/')
                    _is_agent_html = bool(
                        _agent_html_m and 'text/html' in ct and method.upper() == 'GET'
                    )
                    _agent_html_prefix = f"/agent/{_agent_html_m.group(1)}" if _agent_html_m else ""
                    _agent_html_shim_injected: str = ""

                    runtime_settings = (
                        _runtime.DataChannelRuntimeSettings.from_env()
                        if _runtime.DataChannelRuntimeSettings is not None
                        else None
                    )
                    configured_binary_stream_chunk_size = (
                        runtime_settings.binary_http_stream_chunk_size
                        if runtime_settings is not None
                        else 9216
                    )
                    ack_backed_binary_min_bytes = (
                        runtime_settings.ack_backed_binary_http_stream_min_bytes
                        if runtime_settings is not None
                        else 64 * 1024
                    )
                    max_dc_message_size = getattr(
                        getattr(datachannel_manager, "chunker", None),
                        "max_chunk_size",
                        1024,
                    )
                    safe_text_stream_chunk_size = _runtime.calculate_safe_http_stream_data_chunk_size(
                        max_message_size=max_dc_message_size,
                        binary=False,
                        session_id=session_id,
                        user_id=user_id,
                    )
                    safe_binary_stream_chunk_size = _runtime.calculate_safe_http_stream_data_chunk_size(
                        max_message_size=max_dc_message_size,
                        binary=True,
                        session_id=session_id,
                        user_id=user_id,
                    )
                    use_ack_backed_binary_stream = _runtime._should_use_ack_backed_binary_http_stream(
                        is_textual=is_textual,
                        content_type=ct,
                        url=url,
                        request_headers=headers,
                        response_headers=upstream_response_headers,
                        min_bytes=ack_backed_binary_min_bytes,
                    )
                    binary_stream_chunk_size = _runtime._select_binary_http_stream_chunk_size(
                        configured_chunk_size=configured_binary_stream_chunk_size,
                        safe_chunk_size=safe_binary_stream_chunk_size,
                    )
                    text_stream_chunk_size = max(1, safe_text_stream_chunk_size)
                    stream_read_chunk_size = text_stream_chunk_size if is_textual else binary_stream_chunk_size
                    text_stream_decoder = None
                    text_stream_encoding = "utf-8"
                    if is_textual:
                        text_stream_decoder, text_stream_encoding = _runtime._build_incremental_http_text_decoder(ct)

                    if is_textual and text_stream_chunk_size < 65536:
                        _runtime.LOGGER.debug(
                            "Reduced textual HTTP stream chunk size for %s to %s bytes to avoid SCTP re-chunking",
                            request_id,
                            text_stream_chunk_size,
                        )
                    if not is_textual and binary_stream_chunk_size < configured_binary_stream_chunk_size:
                        _runtime.LOGGER.debug(
                            "Reduced binary HTTP stream chunk size for %s to %s bytes to avoid SCTP re-chunking",
                            request_id,
                            binary_stream_chunk_size,
                        )
                    if use_ack_backed_binary_stream:
                        _runtime.LOGGER.info(
                            "Using bounded binary HTTP stream frames for %s (chunk_size=%s, configured_chunk_size=%s, content_length=%s, content_type=%s)",
                            request_id,
                            binary_stream_chunk_size,
                            configured_binary_stream_chunk_size,
                            _runtime._http_content_length_bytes(upstream_response_headers),
                            ct or "unknown",
                        )

                    streamed_fallback_limit = self._get_streamed_http_response_fallback_max_bytes()
                    try:
                        binary_fallback_limit = int(
                            _runtime.os.environ.get("AUTOYOU_BINARY_HTTP_FALLBACK_MAX_BYTES", "262144")
                        )
                    except Exception:
                        binary_fallback_limit = 262144
                    if binary_fallback_limit < 0:
                        binary_fallback_limit = 0

                    if is_textual:
                        response_body_capture_limit: int | None = None
                    elif streamed_fallback_limit > 0 and binary_fallback_limit > 0:
                        response_body_capture_limit = min(
                            streamed_fallback_limit,
                            binary_fallback_limit,
                        )
                    else:
                        response_body_capture_limit = 0
                    response_body_capture_truncated = False
                    response_size_bytes = 0

                    buffered_response_limit = self._get_buffered_http_response_max_bytes()
                    should_try_buffered_response = (
                        buffered_response_limit > 0
                        and is_textual
                        and "text/event-stream" not in ct
                        and method.upper() != "HEAD"
                    )
                    body_fetch_chunk_size = (
                        min(65536, max(1, buffered_response_limit + 1))
                        if should_try_buffered_response
                        else stream_read_chunk_size
                    )
                    body_stream = resp.aiter_bytes(chunk_size=body_fetch_chunk_size)
                    prefetched_stream_chunks: list[bytes] = []

                    if should_try_buffered_response:
                        buffered_body = bytearray()
                        buffer_exceeded = False
                        async for buffered_chunk in body_stream:
                            if not buffered_chunk:
                                continue
                            if len(buffered_body) + len(buffered_chunk) > buffered_response_limit:
                                buffer_exceeded = True
                                if buffered_body:
                                    prefetched_stream_chunks.append(bytes(buffered_body))
                                    buffered_body.clear()
                                prefetched_stream_chunks.append(bytes(buffered_chunk))
                                break
                            buffered_body.extend(buffered_chunk)

                        if not buffer_exceeded:
                            response_body_bytes = bytes(buffered_body)
                            text_body_override: str | None = None
                            if _is_agent_html:
                                response_text = response_body_bytes.decode(
                                    _runtime._http_stream_text_charset(ct),
                                    errors="replace",
                                )
                                shimmed_html_bytes = self._inject_agent_proxy_shim(
                                    response_text.encode("utf-8", errors="ignore"),
                                    _agent_html_prefix,
                                )
                                response_body_bytes = shimmed_html_bytes
                                text_body_override = shimmed_html_bytes.decode(
                                    "utf-8",
                                    errors="ignore",
                                )

                            encoded_response = _runtime.encode_http_proxy_response(
                                response_body_bytes,
                                headers=upstream_response_headers,
                                raw_headers=raw_headers,
                                text_body=text_body_override,
                            )
                            response_message = _runtime.create_http_response_message(
                                status_code=resp.status_code,
                                headers=encoded_response.headers,
                                body=encoded_response.body,
                                request_id=request_id,
                                session_id=session_id,
                                user_id=user_id,
                                compressed=encoded_response.compressed,
                                raw_headers=encoded_response.raw_headers,
                            )
                            if not datachannel_manager or not hasattr(datachannel_manager, 'send_message'):
                                raise RuntimeError("HTTP response transport unavailable during HTTP_RESPONSE")
                            send_ok = await datachannel_manager.send_message(response_message)
                            if not send_ok:
                                raise RuntimeError("HTTP response stalled during HTTP_RESPONSE")

                            _runtime.LOGGER.info(
                                "HTTP %s %s -> %s (buffered textual response, source_size=%s bytes, encoded_size=%s bytes, compressed: %s) (ID: %s)",
                                method,
                                url,
                                resp.status_code,
                                len(response_body_bytes),
                                len(encoded_response.body.encode("utf-8", errors="ignore")),
                                encoded_response.compressed,
                                request_id,
                            )
                            return

                        _runtime.LOGGER.info(
                            "Buffered textual HTTP response exceeded %s bytes for %s; falling back to streamed response",
                            buffered_response_limit,
                            request_id,
                        )

                    if datachannel_manager and hasattr(datachannel_manager, 'send_message'):
                      open_msg = _runtime.create_http_stream_open_message(
                        request_id=request_id,
                        status_code=resp.status_code,
                        headers=stream_open_headers,
                        raw_headers=stream_open_raw_headers,
                        session_id=session_id,
                        user_id=user_id,
                      )
                      await _send_stream_frame_or_raise(open_msg, "HTTP_STREAM_OPEN")

                    async def _iter_response_chunks():
                        for prefetched_chunk in prefetched_stream_chunks:
                            if not prefetched_chunk:
                                continue
                            for offset in range(0, len(prefetched_chunk), stream_read_chunk_size):
                                yield prefetched_chunk[offset:offset + stream_read_chunk_size]
                        async for stream_chunk in body_stream:
                            if not stream_chunk:
                                continue
                            for offset in range(0, len(stream_chunk), stream_read_chunk_size):
                                yield stream_chunk[offset:offset + stream_read_chunk_size]

                    # Stream bytes and forward as HTTP_STREAM_DATA; accumulate appropriately
                    async for chunk in _iter_response_chunks():
                        if not chunk:
                            continue
                        # Always accumulate raw bytes for final assembly
                        try:
                            response_size_bytes += len(chunk)
                            if response_body_capture_limit is None:
                                response_bytes_accumulator.extend(chunk)
                            elif len(response_bytes_accumulator) + len(chunk) <= response_body_capture_limit:
                                response_bytes_accumulator.extend(chunk)
                            else:
                                response_body_capture_truncated = True
                                response_bytes_accumulator.clear()
                        except Exception:
                            # Fallback to list append if bytearray fails (shouldn't happen)
                            response_text_accumulator.append("")
                        # Try text decode for accumulation; fall back to base64-tagged text
                        if is_textual:
                            piece_text = _runtime._decode_incremental_http_text_chunk(
                                text_stream_decoder,
                                chunk,
                                encoding=text_stream_encoding,
                            )
                            if piece_text:
                                response_text_accumulator.append(piece_text)
                        else:
                            import base64
                            piece_text = "base64:" + base64.b64encode(chunk).decode('ascii')

                        # For agent HTML responses, suppress per-chunk sends;
                        # we will inject the shim and send the full body as a
                        # single DATA chunk right before STREAM_END below.
                        if datachannel_manager and hasattr(datachannel_manager, 'send_message') and not _is_agent_html:
                          seq_no += 1
                          data_msg = _runtime.create_http_stream_data_message(
                            request_id=request_id,
                            data=piece_text,
                            seq=seq_no,
                            session_id=session_id,
                            user_id=user_id,
                          )
                          await _send_stream_frame_or_raise(
                            data_msg,
                            f"HTTP_STREAM_DATA seq={seq_no}",
                          )

                    if is_textual and text_stream_decoder is not None:
                        trailing_text = _runtime._flush_incremental_http_text_decoder(
                            text_stream_decoder,
                            encoding=text_stream_encoding,
                        )
                        if trailing_text:
                            response_text_accumulator.append(trailing_text)
                            if datachannel_manager and hasattr(datachannel_manager, 'send_message') and not _is_agent_html:
                              seq_no += 1
                              data_msg = _runtime.create_http_stream_data_message(
                                request_id=request_id,
                                data=trailing_text,
                                seq=seq_no,
                                session_id=session_id,
                                user_id=user_id,
                              )
                              await _send_stream_frame_or_raise(
                                data_msg,
                                f"HTTP_STREAM_DATA seq={seq_no}",
                              )

                    # For agent HTML responses: inject the proxy shim into the
                    # fully-accumulated HTML and send it as a single DATA chunk
                    # so the JS shim is present before the browser executes any scripts.
                    if _is_agent_html and is_textual:
                        try:
                            _raw_html = "".join(response_text_accumulator)
                            _html_bytes = _raw_html.encode("utf-8", errors="ignore")
                            _html_bytes = self._inject_agent_proxy_shim(_html_bytes, _agent_html_prefix)
                            _agent_html_shim_injected = _html_bytes.decode("utf-8", errors="ignore")
                            if datachannel_manager and hasattr(datachannel_manager, 'send_message'):
                                text_piece_size = max(1, text_stream_chunk_size)
                                for offset in range(0, len(_agent_html_shim_injected), text_piece_size):
                                    seq_no += 1
                                    html_piece = _agent_html_shim_injected[offset:offset + text_piece_size]
                                    data_msg = _runtime.create_http_stream_data_message(
                                        request_id=request_id,
                                        data=html_piece,
                                        seq=seq_no,
                                        session_id=session_id,
                                        user_id=user_id,
                                    )
                                    await _send_stream_frame_or_raise(
                                        data_msg,
                                        f"HTTP_STREAM_DATA seq={seq_no}",
                                    )
                        except RuntimeError:
                            raise
                        except Exception as shim_err:
                            _runtime.LOGGER.warning("Agent proxy shim injection failed for %s: %s", url, shim_err)

                    # Signal end-of-stream
                    if datachannel_manager and hasattr(datachannel_manager, 'send_message'):
                      end_msg = _runtime.create_http_stream_end_message(
                        request_id=request_id,
                        session_id=session_id,
                        user_id=user_id,
                      )
                      await _send_stream_frame_or_raise(end_msg, "HTTP_STREAM_END")

                    # After streaming completes, assemble a legacy fallback only
                    # when the body was intentionally captured. Large binary
                    # streams are already fulfilled by HTTP_STREAM_* frames; do
                    # not retain and re-encode the whole video just to skip it.
                    response_body_available_for_fallback = not response_body_capture_truncated
                    if response_body_available_for_fallback:
                        encoded_response = _runtime.encode_http_proxy_response(
                            bytes(response_bytes_accumulator),
                            headers=upstream_response_headers,
                            raw_headers=raw_headers,
                            text_body=_agent_html_shim_injected if _agent_html_shim_injected else None,
                        )
                        response_headers_out = encoded_response.headers
                        response_body = encoded_response.body
                        raw_headers = encoded_response.raw_headers
                        compressed_response = encoded_response.compressed
                        if compressed_response:
                            _runtime.LOGGER.info(
                                "Compressed HTTP response: %s chunks -> %s bytes",
                                seq_no,
                                len(response_body),
                            )
                        response_fallback_size_bytes = len(response_body.encode("utf-8", errors="ignore"))
                    else:
                        response_headers_out = dict(upstream_response_headers)
                        response_body = ""
                        compressed_response = False
                        response_fallback_size_bytes = response_size_bytes

                    # Modern AutoYou clients resolve regular proxy requests from the
                    # HTTP_STREAM_OPEN/DATA/END sequence directly. Sending a duplicate
                    # HTTP_RESPONSE fallback body after the stream finishes can stall the
                    # ordered datachannel under concurrent browser loads, so keep it
                    # disabled by default unless explicitly re-enabled.
                    should_send_response_fallback = (
                        response_body_available_for_fallback
                        and response_fallback_size_bytes <= streamed_fallback_limit
                    )
                    if not is_textual and response_fallback_size_bytes > binary_fallback_limit:
                        should_send_response_fallback = False

                    # Send the final HTTP_RESPONSE message only when it is still a
                    # reasonable safety net. Large streamed binaries already have
                    # a full stream path and duplicating them as one base64
                    # payload can stall the browser datachannel.
                    if should_send_response_fallback:
                        response_message = _runtime.create_http_response_message(
                            status_code=resp.status_code,
                            headers=response_headers_out,
                            body=response_body,
                            request_id=request_id,
                            session_id=session_id,
                            user_id=user_id,
                            compressed=compressed_response,
                            raw_headers=raw_headers,
                        )
                        if datachannel_manager and hasattr(datachannel_manager, 'send_message'):
                            try:
                                fallback_ok = await datachannel_manager.send_message(response_message)
                                if not fallback_ok:
                                    _runtime.LOGGER.debug(
                                        "HTTP_RESPONSE fallback send stalled for %s after stream completion",
                                        request_id,
                                    )
                            except Exception as send_err:
                                _runtime.LOGGER.debug(f"Failed to send HTTP_RESPONSE fallback for {request_id}: {send_err}")
                    else:
                        _runtime.LOGGER.info(
                            "Skipped HTTP_RESPONSE fallback for streamed %s response %s (source_size=%s bytes, fallback_size=%s bytes, chunks=%s, limit=%s)",
                            "textual" if is_textual else "binary",
                            request_id,
                            response_size_bytes,
                            response_fallback_size_bytes,
                            seq_no,
                          streamed_fallback_limit,
                        )

                    _runtime.LOGGER.info(
                        f"HTTP {method} {url} -> {resp.status_code} (streamed {seq_no} chunks, compressed: {compressed_response}) (ID: {request_id})"
                    )
                    return
            except Exception as stream_err:
                _runtime.LOGGER.warning(f"Upstream non-SSE streaming error for {method} {url} (ID: {request_id}): {stream_err}")
                # Attempt to notify client of abort
                if datachannel_manager and hasattr(datachannel_manager, 'send_message'):
                    try:
                        abort_msg = _runtime.create_http_stream_abort_message(
                            request_id=request_id,
                            error=str(stream_err),
                            session_id=session_id,
                            user_id=user_id,
                        )
                        await datachannel_manager.send_message(abort_msg)
                    except Exception:
                        pass

        except Exception as e:
            _runtime.LOGGER.error(f"Failed to handle HTTP request: {e}")
            # Send error response
            try:
                error_status = 403 if isinstance(e, _runtime.UnsafeURLError) else 500
                error_response = _runtime.create_http_response_message(
                    status_code=error_status,
                    headers={"Content-Type": "text/plain"},
                    body=(
                        f"Remote target rejected: {e}"
                        if error_status == 403
                        else f"Internal server error: {str(e)}"
                    ),
                    request_id=message.payload.get("request_id") or message.header.message_id,
                    session_id=message.header.session_id,
                    user_id=message.header.user_id,
                    compressed=False
                )

                datachannel_manager = self._datachannel_manager_for_session(
                    message.header.session_id,
                    require_send_message=True,
                )
                if datachannel_manager:
                    await datachannel_manager.send_message(error_response)
            except Exception as send_error:
                _runtime.LOGGER.error(f"Failed to send error response: {send_error}")

    async def _handle_ws_upgrade(self, message: 'DataChannelMessage', url: str, headers: dict, request_id: str) -> None:
        """Relay a WebSocket connection through the DataChannel.

        Flow:
          1. Client sends http_request with Upgrade: websocket
          2. Server opens a real websockets connection to the target URL
          3. Server relays data bidirectionally via HTTP_WS_DATA messages
          4. Either side can close via HTTP_WS_CLOSE

        Requires client-side support for the HTTP_WS_DATA/CLOSE protocol.
        """
        session_id = message.header.session_id
        datachannel_manager = self._datachannel_manager_for_session(session_id, require_send_message=True)
        if not datachannel_manager:
            _runtime.LOGGER.error(f"No datachannel manager for WS upgrade, session {session_id}")
            return
        if not _runtime.remote_http_request_allowed(_runtime._get_remote_browser_access_role(_runtime.STATE.config or {}), "GET", url, websocket=True):
            await self._send_remote_access_denied(message, request_id, "GET", url, websocket=True)
            return

        try:
            import websockets
        except ImportError:
            _runtime.LOGGER.error("websockets library not available for WS proxy")
            await datachannel_manager.send_message(
                _runtime.create_http_ws_close_message(
                    request_id=request_id,
                    code=1011,
                    reason="WebSocket proxy not available",
                    session_id=session_id,
                )
            )
            return

        ws_url = self._resolve_autoyou_forward_url(url, websocket=True)
        if not ws_url:
            _runtime.LOGGER.info(
                "WebSocket upgrade refused by route policy for %s (ID: %s)",
                url,
                request_id,
            )
            await datachannel_manager.send_message(
                _runtime.create_http_ws_close_message(
                    request_id=request_id,
                    code=1008,
                    reason="WebSocket is not enabled for this route",
                    session_id=session_id,
                )
            )
            return
        try:
            self._validate_remote_forward_target(ws_url, websocket=True)
        except _runtime.UnsafeURLError as exc:
            await datachannel_manager.send_message(
                _runtime.create_http_ws_close_message(
                    request_id=request_id,
                    code=1008,
                    reason=f"Remote target rejected: {exc}",
                    session_id=session_id,
                )
            )
            return

        ws_conn = None
        try:
            sanitized_headers = self._sanitize_forward_headers(headers, ws_url, websocket=True)
            ws_headers = {
                k: v for k, v in sanitized_headers.items()
                if k.lower() not in (
                    "upgrade",
                    "connection",
                    "sec-websocket-key",
                    "sec-websocket-version",
                    "sec-websocket-extensions",
                    "sec-websocket-protocol",
                    "host",
                )
            }
            protocol_header = next(
                (v for k, v in headers.items() if k.lower() == "sec-websocket-protocol"),
                ""
            )
            subprotocols = [item.strip() for item in str(protocol_header).split(",") if item.strip()]

            import inspect
            if "additional_headers" in inspect.signature(websockets.connect).parameters:
                connect_kwargs: Dict[str, Any] = {"additional_headers": ws_headers}
            else:
                connect_kwargs: Dict[str, Any] = {"extra_headers": ws_headers}
            if subprotocols:
                connect_kwargs["subprotocols"] = subprotocols
            from urllib.parse import urlsplit
            parsed_ws_url = urlsplit(ws_url)
            if not self._is_loopback_host(parsed_ws_url.hostname):
                connect_kwargs["host"] = _runtime.resolve_safe_http_ip(parsed_ws_url.hostname or "")
                connect_kwargs["proxy"] = None
                if parsed_ws_url.scheme == "wss":
                    connect_kwargs["server_hostname"] = parsed_ws_url.hostname

            ws_conn = await websockets.connect(ws_url, **connect_kwargs)
            _runtime.LOGGER.info(f"WebSocket proxy connected to {ws_url} (ID: {request_id})")

            await datachannel_manager.send_message(
                _runtime.create_http_ws_upgrade_message(
                    request_id=request_id,
                    status="connected",
                    url=ws_url,
                    session_id=session_id,
                    subprotocol=getattr(ws_conn, "subprotocol", None),
                )
            )

            if not hasattr(self, '_ws_connections'):
                self._ws_connections: Dict[str, Any] = {}
            self._ws_connections[request_id] = ws_conn

            if not hasattr(self, '_ws_session_requests'):
                self._ws_session_requests: Dict[str, set] = {}
            if session_id not in self._ws_session_requests:
                self._ws_session_requests[session_id] = set()
            self._ws_session_requests[session_id].add(request_id)

            try:
                async for ws_message in ws_conn:
                    # Keep one logical WS frame intact. The shared manager owns
                    # adaptive CHUNK/ACK framing; pre-fragmenting here forced
                    # every server-to-client relay through the old 1 KiB path.
                    relay_message = _runtime.create_http_ws_data_message(
                        request_id=request_id,
                        data=ws_message,
                        session_id=session_id,
                    )
                    await datachannel_manager.send_message(relay_message)
            except websockets.exceptions.ConnectionClosed as cc:
                _runtime.LOGGER.info(f"WebSocket closed for {ws_url}: {cc.code} {cc.reason}")
            finally:
                self._cleanup_stale_ws_fragments(request_id=request_id)
                try:
                    await datachannel_manager.send_message(
                        _runtime.create_http_ws_close_message(
                            request_id=request_id,
                            code=getattr(ws_conn, "close_code", None) or 1000,
                            reason=getattr(ws_conn, "close_reason", None) or "Connection closed",
                            session_id=session_id,
                        )
                    )
                except Exception:
                    pass
                self._ws_connections.pop(request_id, None)
                if hasattr(self, '_ws_session_requests') and session_id in self._ws_session_requests:
                    self._ws_session_requests[session_id].discard(request_id)

        except Exception as e:
            _runtime.LOGGER.error(f"WebSocket proxy error for {url} (ID: {request_id}): {e}")
            if ws_conn:
                try:
                    await ws_conn.close()
                except Exception:
                    pass
            if hasattr(self, '_ws_connections'):
                self._ws_connections.pop(request_id, None)
            if hasattr(self, '_ws_session_requests') and session_id in self._ws_session_requests:
                self._ws_session_requests[session_id].discard(request_id)
            try:
                await datachannel_manager.send_message(
                    _runtime.create_http_ws_close_message(
                        request_id=request_id,
                        code=1011,
                        reason=str(e),
                        session_id=session_id,
                    )
                )
            except Exception:
                pass

    async def _handle_ws_data_from_client(self, message: 'DataChannelMessage') -> None:
        """Handle WebSocket data sent by client to relay through to the upstream WS."""
        request_id = message.payload.get("request_id")
        if hasattr(self, '_ws_connections') and request_id in self._ws_connections:
            try:
                payload = self._consume_ws_fragment_payload(message.payload) or {}
                if not payload:
                    return
                data, _ = _runtime.decode_http_ws_data_payload(payload)
                await self._ws_connections[request_id].send(data)
            except Exception as e:
                _runtime.LOGGER.error(f"Error sending WS data to upstream for {request_id}: {e}")

    async def _handle_ws_close_from_client(self, message: 'DataChannelMessage') -> None:
        """Handle WebSocket close request from client."""
        request_id = message.payload.get("request_id")
        self._cleanup_stale_ws_fragments(request_id=request_id)
        if hasattr(self, '_ws_connections') and request_id in self._ws_connections:
            try:
                code = int(message.payload.get("code") or 1000)
                reason = str(message.payload.get("reason") or "")
                await self._ws_connections[request_id].close(code=code, reason=reason)
                self._ws_connections.pop(request_id, None)
                _runtime.LOGGER.info(f"Closed upstream WebSocket for {request_id}")
            except Exception as e:
                _runtime.LOGGER.error(f"Error closing WS for {request_id}: {e}")

    async def _handle_error_message(self, message: 'DataChannelMessage') -> None:
        """Handle error messages received via datachannel."""
        try:
            # Handle both client and server error formats
            if "error_type" in message.payload:
                # Server format: {"error_type": "...", "message": "...", "code": "..."}
                error_type = message.payload.get("error_type", "unknown")
                error_message = message.payload.get("message", "No error message provided")
                error_code = message.payload.get("code", "UNKNOWN")
            else:
                # Client format: {"error": "..."}
                error_type = "client"
                error_message = message.payload.get("error", "No error message provided")
                error_code = "CLIENT_ERROR"

            session_id = message.header.session_id or "unknown"

            # Log the error with appropriate level
            if error_type in ["connection", "network"]:
                _runtime.LOGGER.warning(f"Client error [{session_id}] {error_type}: {error_message} (code: {error_code})")
            else:
                _runtime.LOGGER.error(f"Client error [{session_id}] {error_type}: {error_message} (code: {error_code})")

            # Optionally send acknowledgment back to client
            # (This is optional - some errors might not need responses)

        except Exception as e:
            _runtime.LOGGER.error(f"Failed to handle error message: {e}")

    async def send_server_profile_to_session(self, session_id: str) -> bool:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return False
        manager = self._datachannel_manager_for_session(normalized_session_id, require_send_message=True)
        if manager is None:
            return False
        profile = _runtime._get_server_profile_call_payload()
        message = _runtime.create_voice_call_control_message(
            profile,
            session_id=self._resolve_voice_chat_session_id(normalized_session_id) or normalized_session_id,
            user_id=str(profile.get("profile_user_id") or "system"),
        )
        try:
            return bool(await manager.send_message(message))
        except Exception as exc:
            _runtime.LOGGER.debug("Could not send server profile to active call %s: %s", normalized_session_id, exc)
            return False

    async def broadcast_server_profile(self) -> None:
        sent_managers: Set[int] = set()
        for session_id, active in list(self.voice_call_client_active_by_session.items()):
            if not active:
                continue
            manager = self._datachannel_manager_for_session(session_id, require_send_message=True)
            if manager is None or id(manager) in sent_managers:
                continue
            sent_managers.add(id(manager))
            await self.send_server_profile_to_session(session_id)

    async def _handle_voice_call_control_message(
        self,
        message: 'DataChannelMessage',
        *,
        trusted_session_id: Optional[str] = None,
    ) -> None:
        session_id = str(trusted_session_id or "").strip() or getattr(
            getattr(message, "header", None),
            "session_id",
            None,
        )
        payload = getattr(message, "payload", None) or {}
        if not isinstance(payload, dict):
            _runtime.LOGGER.warning("Ignoring invalid voice call control payload for %s: %r", session_id, payload)
            return
        event_name = str(payload.get("event") or "").strip().lower()
        if event_name == "connected_devices_request":
            manager = self._datachannel_manager_for_session(str(session_id or ""), require_send_message=True)
            if manager is not None:
                response = _runtime.create_voice_call_control_message(
                    {"event": "connected_devices", "count": self.connected_device_count()},
                    session_id=self._resolve_voice_chat_session_id(str(session_id or "")) or str(session_id or ""),
                    user_id="AutoYou",
                )
                await manager.send_message(response)
            return
        if event_name == "screen_input":
            session = self._screen_session_for_session(str(session_id or ""))
            if session is None or session["mode"] != "interactive" or not self._voice_call_client_active_for_session(str(session_id or "")):
                return
            kind = str(payload.get("kind") or "")
            value = str(payload.get("value") or "")
            phase = str(payload.get("phase") or "")
            if kind == "layout" and phase == "set" and value in {"off", "choices", "gamepad"}:
                session["layout"] = value
                session["last_input"] = ""
                return
            if phase != "press" or not (
                kind == "choice" and session["layout"] == "choices" and value in {"A", "B", "C", "D"}
                or kind == "controller" and session["layout"] == "gamepad" and
                value in {"A", "B", "C", "D", "←", "↑", "↓", "→", "Select"}
            ):
                return
            now = _runtime.time.time()
            if now - float(session["last_input_at"]) < 0.04:
                return
            session["last_input"] = value
            session["last_input_at"] = now
            self.screen_inputs.append({
                "session_id": str(session["id"]), "name": str(session["name"]),
                "kind": kind, "value": value, "timestamp": now,
            })
            return
        if self._screen_session_for_session(str(session_id or "")) is not None and event_name in {
            "remote_desktop_control", "remote_desktop_input", "remote_desktop_keyboard", "game_input",
        }:
            return
        if event_name == "remote_desktop_control":
            await self._handle_remote_desktop_control(str(session_id or ""), payload)
            return
        if event_name == "remote_desktop_input":
            await self._handle_remote_desktop_input(str(session_id or ""), payload)
            return
        if event_name == "game_input":
            await self._handle_game_input(str(session_id or ""), payload)
            return
        if (
            event_name == "remote_desktop_keyboard"
            and self._is_native_call_remote_desktop_payload(payload)
        ):
            await self._handle_call_remote_desktop_keyboard(str(session_id or ""), payload)
            return
        if event_name == "remote_desktop_keyboard":
            normalized_keyboard = _runtime.normalize_remote_desktop_keyboard_payload(payload)
            keyboard_source = str(payload.get("source") or "").strip().lower()
            keyboard_platform = str(payload.get("platform") or "").strip().lower()
            action = str((normalized_keyboard or {}).get("action") or "")
            if (
                normalized_keyboard is None
                or keyboard_source != "remote_desktop_agent"
                or keyboard_platform not in {"ios", "android"}
                or action not in {"input", "key", "hide", "state"}
            ):
                _runtime.LOGGER.warning("Ignoring invalid native remote-desktop keyboard control for %s", session_id)
                return

            control_id = str(normalized_keyboard.get("control_id") or "").strip()
            lease = self._remote_desktop_keyboard_lease_for_session(str(session_id or ""), control_id)
            if lease is None:
                _runtime.LOGGER.warning("Ignoring native remote-desktop keyboard event without a valid lease for %s", session_id)
                return
            if action == "hide":
                self._clear_remote_desktop_keyboard_lease(str(session_id or ""), control_id)
                # A "hide" can race the client's own modifier keyUp cleanup
                # (e.g. this hide was itself triggered by the server/website,
                # so the client's teardown keyUp arrives just after the lease
                # is already gone and gets rejected below). Release on the
                # host unconditionally so Ctrl/Alt/Shift never stays stuck.
                await _runtime.asyncio.to_thread(_runtime.release_stuck_modifiers)
                return

            if action == "state":
                self._store_remote_desktop_keyboard_lease(
                    str(session_id or ""),
                    control_id=str(lease.get("control_id") or control_id),
                    owner_key=str(lease.get("owner_key") or ""),
                    keyboard_state=str(normalized_keyboard.get("keyboard_state") or ""),
                )
                return

            if action in ("input", "key"):
                # Sliding-window renewal: a continuously-typing session should
                # not have its lease expire out from under it after the fixed
                # 10-minute window used at "show" time.
                self._store_remote_desktop_keyboard_lease(
                    str(session_id or ""),
                    control_id=str(lease.get("control_id") or control_id),
                    owner_key=str(lease.get("owner_key") or ""),
                    keyboard_state=str(lease.get("keyboard_state") or "visible"),
                )

            applied = await _runtime.asyncio.to_thread(_runtime.execute_remote_desktop_keyboard, normalized_keyboard)
            if not applied:
                _runtime.LOGGER.warning(
                    "Native remote-desktop keyboard event could not be applied for %s (action=%s)",
                    session_id,
                    action,
                )
            return
        if event_name == "call_state":
            platform = str(payload.get("platform") or "unknown").strip() or "unknown"
            timestamp_ms = payload.get("timestamp_ms")
            active = self._coerce_control_bool(payload.get("active"))
            local_call = self._same_machine_audio_session(str(session_id or ""))
            previous_host_owner = self.host_audio_owner() if local_call else ""
            was_active = self._voice_call_client_active_for_session(str(session_id or ""))
            alias_ids = self._set_voice_call_client_active(str(session_id or ""), active)
            screen_mode = str(payload.get("screen_mode") or "") if active else "ended"
            self._set_screen_session(str(session_id or ""), screen_mode)
            if active:
                self._set_background_audio_state(
                    str(session_id or ""),
                    active=False,
                    silent_recording=False,
                    muted=True,
                    platform=platform,
                    timestamp_ms=timestamp_ms,
                )
                self._restore_outbound_audio_for_call(str(session_id or ""))
                if not was_active:
                    await self.send_server_profile_to_session(str(session_id or ""))
            _runtime.LOGGER.info(
                "Received voice call call_state for %s: active=%s platform=%s timestamp_ms=%s aliases=%s",
                session_id,
                active,
                platform,
                timestamp_ms,
                sorted(alias_ids),
            )
            if not active:
                await self._release_remote_desktop_control(str(session_id or ""))
                self._drop_local_capture_audio_tracks_for_ids(alias_ids)
                self._clear_outbound_audio_track_for_idle(str(session_id or ""))
            if local_call and previous_host_owner != self.host_audio_owner():
                await self._rewire_outbound_audio_for_host()
            return
        if event_name == "host_audio_priority":
            if not self._same_machine_audio_session(str(session_id or "")):
                return
            owner = str(payload.get("owner") or "").strip().lower()
            if owner not in {"", "connected_call", "lobby", "peer", "recording"}:
                return
            previous = self.host_audio_owner()
            for alias in self._ordered_related_session_ids(str(session_id or "")):
                if alias in self.same_machine_audio_sessions:
                    if owner:
                        self.host_media_owner_by_session[alias] = owner
                    else:
                        self.host_media_owner_by_session.pop(alias, None)
            if previous != self.host_audio_owner():
                await self._rewire_outbound_audio_for_host()
            return
        # ── video_state: client started/stopped a video call. Gate the outbound
        #    desktop video track so the desktop is only captured/streamed while the
        #    client is actively watching, not on every connection. ──
        if event_name == "background_audio_state":
            normalized_session_id = str(session_id or "").strip()
            if not normalized_session_id:
                _runtime.LOGGER.debug("Ignoring background_audio_state without a session id")
                return
            platform = str(payload.get("platform") or "unknown").strip() or "unknown"
            timestamp_ms = payload.get("timestamp_ms")
            active = self._coerce_control_bool(payload.get("active"))
            silent_recording = self._coerce_control_bool(
                payload.get("silent_recording", payload.get("recording"))
            )
            muted = self._coerce_control_bool(payload.get("muted")) if "muted" in payload else not silent_recording
            client_direction = str(payload.get("client_audio_direction") or "").strip().lower()
            if active and self._voice_call_client_active_for_session(normalized_session_id):
                active = False
                silent_recording = False
                muted = True
                server_audio_direction = "inactive"
                client_direction = "inactive"
            elif active and not self._background_audio_mode_allowed(
                silent_recording=silent_recording,
                cfg=(_runtime.STATE.config or {}),
            ):
                active = False
                silent_recording = False
                muted = True
                server_audio_direction = "inactive"
                client_direction = "inactive"
            elif not active:
                silent_recording = False
                muted = True
                server_audio_direction = "inactive"
            elif silent_recording:
                muted = False
                server_audio_direction = "sendrecv"
                client_direction = "sendonly"
            elif platform.lower() == "ios" and client_direction in {"sendrecv", "recvonly"}:
                silent_recording = False
                muted = True
                client_direction = "recvonly"
                server_audio_direction = "sendrecv"
            else:
                silent_recording = False
                muted = True
                server_audio_direction = "sendrecv"
                client_direction = "sendrecv"
            alias_ids = self._set_background_audio_state(
                normalized_session_id,
                active=active,
                silent_recording=silent_recording,
                muted=muted,
                platform=platform,
                timestamp_ms=timestamp_ms,
            )
            if active:
                self._suppress_outbound_audio_for_background(
                    normalized_session_id,
                    silent_recording=silent_recording,
                    server_audio_direction=server_audio_direction,
                )
            elif not self._voice_call_client_active_for_session(normalized_session_id):
                self._clear_outbound_audio_track_for_idle(normalized_session_id)
            _runtime.LOGGER.info(
                "Received background_audio_state for %s: active=%s silent_recording=%s muted=%s platform=%s aliases=%s",
                session_id,
                active,
                silent_recording,
                muted,
                platform,
                sorted(alias_ids),
            )
            return
        if event_name == "video_state":
            platform = str(payload.get("platform") or "unknown").strip() or "unknown"
            active_raw = payload.get("active")
            if isinstance(active_raw, bool):
                active = active_raw
            elif isinstance(active_raw, str):
                active = active_raw.strip().lower() in {"1", "true", "yes", "on"}
            else:
                active = bool(active_raw)
            camera_active_raw = payload.get("camera_active")
            camera_active: Optional[bool] = None
            if isinstance(camera_active_raw, bool):
                camera_active = camera_active_raw
            elif isinstance(camera_active_raw, str):
                camera_active = camera_active_raw.strip().lower() in {"1", "true", "yes", "on"}
            elif camera_active_raw is not None:
                camera_active = bool(camera_active_raw)
            # WebRTC renderers retain their last frame after a sender removes its
            # camera track. Clear every alias immediately when a modern client
            # reports camera-off (or the whole video call stops); older clients
            # are still covered by the registry's stale-frame expiry.
            if _runtime.VIDEO_FRAME_REGISTRY is not None and (not active or camera_active is False):
                for related_session_id in self._ordered_related_session_ids(str(session_id or "")):
                    try:
                        _runtime.VIDEO_FRAME_REGISTRY.clear_session(related_session_id)
                    except Exception:
                        pass
            matched_track_session_id, track = self._desktop_video_track_for_session(str(session_id or ""))
            server_allows_outbound_video = _runtime._get_outbound_video_available(cfg=(_runtime.STATE.config or {}))
            if track is None and active and server_allows_outbound_video:
                for related_session_id in self._ordered_related_session_ids(str(session_id or "")):
                    peer_connection = self.session_peers.get(related_session_id)
                    if peer_connection is None:
                        continue
                    video_transceiver = next(
                        (transceiver for transceiver in peer_connection.getTransceivers() if transceiver.kind == "video"),
                        None,
                    )
                    if video_transceiver is None:
                        continue
                    try:
                        track = _runtime._create_configured_outbound_video_track(cfg=(_runtime.STATE.config or {}))
                        self._configure_remote_desktop_video_sender(related_session_id, video_transceiver, track)
                        matched_track_session_id = related_session_id
                    except Exception as exc:
                        track = None
                        _runtime.LOGGER.warning("Failed to prepare outbound video track for %s: %s", session_id, exc)
                    break
            if track is not None:
                try:
                    if active and server_allows_outbound_video:
                        track.enable()
                    else:
                        track.disable()
                    _runtime.LOGGER.info(
                        "Outbound video stream %s for %s on client video_state (platform=%s camera_active=%s matched_session=%s source=%s)",
                        "enabled" if (active and server_allows_outbound_video) else "disabled",
                        session_id,
                        platform,
                        camera_active,
                        matched_track_session_id,
                        _runtime._get_video_outbound_source(cfg=(_runtime.STATE.config or {})),
                    )
                except Exception as exc:
                    _runtime.LOGGER.warning("Failed to toggle outbound video track for %s: %s", session_id, exc)
            elif active and not server_allows_outbound_video:
                _runtime.LOGGER.info(
                    "video_state(active=True) received for %s but outbound video is disabled by server capabilities=%s",
                    session_id,
                    _runtime._build_webrtc_capabilities(cfg=(_runtime.STATE.config or {})).get("outbound_video", {}),
                )
            else:
                _runtime.LOGGER.info(
                    "video_state(active=%s) received for %s but no outbound video track is attached; related_sessions=%s",
                    active,
                    session_id,
                    self._ordered_related_session_ids(str(session_id or "")),
                )
            if self._voice_call_client_active_for_session(str(session_id or "")):
                self._restore_outbound_audio_for_call(str(session_id or ""))
            if not active:
                await self._release_remote_desktop_control(str(session_id or ""))
            return
        # ── rewarded_ad_completed: native iOS/Android SDK reward callback has
        #    fired and the AdMob full-screen UI has been dismissed. This is a
        #    local edge proof only.
        if event_name == "rewarded_ad_completed":
            def _bounded_text(value: Any, default: str = "", limit: int = 128) -> str:
                text = str(value if value is not None else default).strip()
                return text[:limit]

            def _safe_float(value: Any, default: float = 0.0) -> float:
                try:
                    parsed = float(value)
                except Exception:
                    return default
                if not _runtime.math.isfinite(parsed):
                    return default
                return parsed

            def _safe_int(value: Any, default: int = 0) -> int:
                try:
                    parsed = int(float(value))
                except Exception:
                    return default
                return parsed

            platform = _bounded_text(payload.get("platform"), "unknown", 32) or "unknown"
            timestamp_ms = _safe_int(payload.get("timestamp_ms"), int(_runtime.time.time() * 1000))
            watched_seconds = max(0.0, min(120.0, _safe_float(payload.get("watched_seconds"), 0.0)))
            completion = {
                "event": "rewarded_ad_completed",
                "session_id": str(session_id or ""),
                "platform": platform,
                "timestamp_ms": timestamp_ms,
                "control_id": _bounded_text(payload.get("control_id"), "", 128),
                "source": _bounded_text(payload.get("source"), "ads_watching_agent", 64) or "ads_watching_agent",
                "watched_seconds": round(watched_seconds, 3),
            }
            alias_ids = self._ordered_related_session_ids(str(session_id or "")) if session_id else []
            if not alias_ids and session_id:
                alias_ids = [str(session_id)]
            control_id = completion["control_id"]
            if control_id and any(
                str(self.rewarded_ad_completion_by_session.get(alias_id, {}).get("control_id") or "") == control_id
                for alias_id in alias_ids
            ):
                self._clear_rewarded_ad_control_lease(str(session_id or ""), control_id)
                _runtime.LOGGER.info(
                    "Ignoring duplicate rewarded ad completion for %s: control_id=%s",
                    session_id,
                    control_id,
                )
                return
            for alias_id in alias_ids:
                self.rewarded_ad_completion_by_session[alias_id] = dict(completion, session_id=alias_id)
            self._clear_rewarded_ad_control_lease(str(session_id or ""), control_id)
            # Durable local pending-credit tally for the Earnings Agent site.
            # Local readback only: no ledger effect and no cloud write.
            try:
                from shared.pending_ad_credits import record_rewarded_ad_completion
                record_rewarded_ad_completion(completion)
            except Exception as exc:
                _runtime.LOGGER.debug("Pending ad-credit tally update failed: %s", exc)
            _runtime.LOGGER.info(
                "Received rewarded ad completion for %s: platform=%s control_id=%s watched_seconds=%.3f aliases=%s",
                session_id,
                platform,
                completion["control_id"],
                completion["watched_seconds"],
                sorted(alias_ids),
            )
            return
        if event_name == "client_browser_control_result":
            def _bounded_text(value: Any, default: str = "", limit: int = 256) -> str:
                text = str(value if value is not None else default).strip()
                return text[:limit]

            result = {
                "event": "client_browser_control_result",
                "session_id": str(session_id or ""),
                "platform": _bounded_text(payload.get("platform"), "unknown", 32) or "unknown",
                "timestamp_ms": payload.get("timestamp_ms"),
                "control_id": _bounded_text(payload.get("control_id"), "", 128),
                "action": _bounded_text(payload.get("action"), "", 32),
                "status": _bounded_text(payload.get("status"), "unknown", 32) or "unknown",
                "url": _bounded_text(payload.get("url"), "", 2048),
                "reason": _bounded_text(payload.get("reason"), "", 256),
            }
            alias_ids = self._ordered_related_session_ids(str(session_id or "")) if session_id else []
            if not alias_ids and session_id:
                alias_ids = [str(session_id)]
            for alias_id in alias_ids:
                self.client_browser_control_results_by_session[alias_id] = dict(result, session_id=alias_id)
            _runtime.LOGGER.info(
                "Received client browser control result for %s: action=%s status=%s control_id=%s aliases=%s",
                session_id,
                result["action"],
                result["status"],
                result["control_id"],
                sorted(alias_ids),
            )
            return
        if self._screen_session_for_session(str(session_id or "")) is not None and event_name in {
            "stop_tts", "wuift_state", "wuift_trigger",
        }:
            return
        # ── stop_tts: client wants to interrupt the current TTS response immediately ──
        if event_name == "stop_tts":
            platform = str(payload.get("platform") or "unknown").strip() or "unknown"
            audio_manager = _runtime.STATE.audio_managers.get(session_id)
            if audio_manager and hasattr(audio_manager, "stop_speaking"):
                audio_manager.stop_speaking(source=f"{platform}:{session_id}:client_request")
                _runtime.LOGGER.info("TTS stopped on client request for session %s (platform=%s)", session_id, platform)
            else:
                _runtime.LOGGER.info("Ignoring stop_tts for %s: no active audio manager", session_id)
            return
        # ── wuift_state: client engages/releases the WUIFT segmentation hold ──
        if event_name == "wuift_state":
            if not session_id:
                return
            if not _runtime._get_wuift_enabled():
                _runtime.LOGGER.info("Ignoring wuift_state for %s: WUIFT is disabled in Video & Calls settings", session_id)
                return
            platform = str(payload.get("platform") or "unknown").strip() or "unknown"
            active = self._coerce_control_bool(payload.get("active"))
            self.wuift_hold_by_session[str(session_id)] = active
            audio_manager = _runtime.STATE.audio_managers.get(session_id)
            if audio_manager and hasattr(audio_manager, "set_segmentation_hold"):
                audio_manager.set_segmentation_hold(active, source=f"{platform}:{session_id}")
            _runtime.LOGGER.info(
                "WUIFT hold %s for session %s (platform=%s, audio_manager=%s)",
                "engaged" if active else "released",
                session_id,
                platform,
                "ready" if audio_manager else "pending",
            )
            return
        # ── wuift_trigger: segment boundary press - flush the held utterance now ──
        if event_name == "wuift_trigger":
            if not session_id:
                return
            if not _runtime._get_wuift_enabled():
                _runtime.LOGGER.info("Ignoring wuift_trigger for %s: WUIFT is disabled in Video & Calls settings", session_id)
                return
            platform = str(payload.get("platform") or "unknown").strip() or "unknown"
            timestamp_ms = payload.get("timestamp_ms")
            normalized_timestamp_ms: Optional[int] = None
            if isinstance(timestamp_ms, (int, float)):
                normalized_timestamp_ms = int(timestamp_ms)
            elif isinstance(timestamp_ms, str):
                try:
                    normalized_timestamp_ms = int(timestamp_ms.strip())
                except Exception:
                    normalized_timestamp_ms = None
            audio_manager = _runtime.STATE.audio_managers.get(session_id)
            if not audio_manager or not hasattr(audio_manager, "flush_utterance"):
                _runtime.LOGGER.info("Ignoring wuift_trigger for %s: no active audio manager", session_id)
                return
            flush_queued = bool(
                audio_manager.flush_utterance(
                    source=f"wuift:{platform}:{session_id}",
                    timestamp_ms=normalized_timestamp_ms,
                )
            )
            _runtime.LOGGER.info(
                "WUIFT trigger flush %s for session %s (platform=%s)",
                "queued" if flush_queued else "skipped",
                session_id,
                platform,
            )
            return

        muted_raw = payload.get("muted")
        platform = str(payload.get("platform") or "unknown").strip() or "unknown"
        timestamp_ms = payload.get("timestamp_ms")

        if isinstance(muted_raw, bool):
            muted = muted_raw
        elif isinstance(muted_raw, str):
            muted = muted_raw.strip().lower() in {"1", "true", "yes", "on"}
        else:
            muted = bool(muted_raw)

        _runtime.LOGGER.info(
            "Received voice call control for %s: muted=%s platform=%s timestamp_ms=%s payload_keys=%s",
            session_id,
            muted,
            platform,
            timestamp_ms,
            sorted(payload.keys()),
        )

        screen_session = self._screen_session_for_session(str(session_id or ""))
        if screen_session is not None:
            if "muted" in payload:
                screen_session["muted"] = muted
                if muted:
                    self.screen_listen_mixer.drop(str(screen_session["id"]))
            return

        if not session_id or not muted:
            return

        audio_manager = _runtime.STATE.audio_managers.get(session_id)
        if not audio_manager or not hasattr(audio_manager, "flush_utterance"):
            _runtime.LOGGER.info("Skipping STT mute flush for %s because no audio manager is active", session_id)
            return

        if _runtime.VOICE_CALL_MUTE_FLUSH_GRACE_SECONDS > 0:
            await _runtime.asyncio.sleep(_runtime.VOICE_CALL_MUTE_FLUSH_GRACE_SECONDS)

        current_audio_manager = _runtime.STATE.audio_managers.get(session_id)
        if current_audio_manager is not audio_manager:
            _runtime.LOGGER.info("Skipping stale STT mute flush for %s because the audio manager changed", session_id)
            return

        normalized_timestamp_ms = None
        if isinstance(timestamp_ms, (int, float)):
            normalized_timestamp_ms = int(timestamp_ms)
        elif isinstance(timestamp_ms, str):
            try:
                normalized_timestamp_ms = int(timestamp_ms.strip())
            except Exception:
                normalized_timestamp_ms = None

        flush_queued = bool(
            audio_manager.flush_utterance(
                source=f"{platform}:{session_id}",
                timestamp_ms=normalized_timestamp_ms,
            )
        )
        _runtime.LOGGER.info(
            "Voice call mute flush %s for %s after %.0fms grace",
            "queued" if flush_queued else "skipped",
            session_id,
            _runtime.VOICE_CALL_MUTE_FLUSH_GRACE_SECONDS * 1000.0,
        )

    async def _force_clear_shutdown_state(self) -> None:
        """Drop WebRTC-owned in-memory state during process shutdown."""
        released_control_leases: set[int] = set()
        async with self.remote_desktop_control_lock:
            for session_id, lease in list(self.remote_desktop_control_leases_by_session.items()):
                if id(lease) in released_control_leases:
                    continue
                if await self._release_remote_desktop_control_locked(
                    session_id,
                    str(lease.get("control_id") or ""),
                    expected_lease=lease,
                ):
                    released_control_leases.add(id(lease))

        pending_tasks: List[asyncio.Task] = []
        pending_tasks.extend(task for task in self.cleanup_tasks.values() if task is not None)
        pending_tasks.extend(task for task in self.session_establishment_tasks.values() if task is not None)
        pending_tasks.extend(task for task in self.session_disconnect_grace_tasks.values() if task is not None)
        pending_tasks.extend(task for task in self.voice_command_workers.values() if task is not None)
        for task_set in self.session_message_tasks.values():
            pending_tasks.extend(task for task in list(task_set or set()) if task is not None)
        for task_set in self.session_reconnect_survivable_tasks.values():
            pending_tasks.extend(task for task in list(task_set or set()) if task is not None)
        pending_tasks.extend(task for task in self.room_bridge_expiry_tasks.values() if task is not None)
        for request_tasks in self.http_proxy_request_tasks.values():
            pending_tasks.extend(task for task in list((request_tasks or {}).values()) if task is not None)

        for task in pending_tasks:
            if task.done():
                continue
            task.cancel()
            task.add_done_callback(
                lambda done_task: self._consume_cleanup_task_result(done_task, "forced WebRTC shutdown task")
            )

        self._close_silent_recorders_for_ids(list(self.silent_recorders.keys()))

        seen_desktop_tracks: set[int] = set()
        for desktop_track in self.desktop_video_tracks.values():
            if desktop_track is None or id(desktop_track) in seen_desktop_tracks:
                continue
            seen_desktop_tracks.add(id(desktop_track))
            bitrate_task = getattr(desktop_track, "_autoyou_desktop_video_bitrate_task", None)
            if isinstance(bitrate_task, _runtime.asyncio.Task) and not bitrate_task.done():
                bitrate_task.cancel()
            try:
                desktop_track._autoyou_desktop_video_sender = None
            except Exception:
                pass

        self.cleanup_tasks.clear()
        self.session_establishment_tasks.clear()
        self.session_disconnect_grace_tasks.clear()
        self._cleanup_locks.clear()
        self.session_peers.clear()
        self.audio_sinks.clear()
        self.video_sinks.clear()
        self.desktop_video_tracks.clear()
        self.remote_desktop_control_leases_by_session.clear()
        self.audio_transceivers.clear()
        self.datachannel_managers.clear()
        self.voice_call_status_by_session.clear()
        self.voice_call_playback_by_session.clear()
        self.voice_call_client_active_by_session.clear()
        self.screen_sessions.clear()
        self.screen_inputs.clear()
        await _runtime.asyncio.to_thread(self.screen_listen_mixer.close)
        self.background_audio_state_by_session.clear()
        self.silent_recorders.clear()
        self.voice_command_queues.clear()
        self.voice_command_workers.clear()
        self.pending_voice_chat_messages.clear()
        self._offline_pending_messages.clear()
        self._voice_chat_flush_locks.clear()
        self._offline_queue_flush_locks.clear()
        self._voice_dc_session_id.clear()
        self.session_message_tasks.clear()
        self.session_reconnect_survivable_tasks.clear()
        self.room_bridge_expiry_tasks.clear()
        self.http_proxy_request_tasks.clear()
        self.pending_candidates.clear()
        self.seen_remote_ice_candidates.clear()
        self.outgoing_trickle_candidates.clear()
        self._ws_fragment_buffers.clear()
        _runtime.STATE.audio_managers.clear()
        _runtime.STATE.session_cache.clear()
        if hasattr(self, "_ws_connections"):
            self._ws_connections.clear()
        if hasattr(self, "_ws_session_requests"):
            self._ws_session_requests.clear()

    async def shutdown(self):
        """Gracefully shutdown all WebRTC sessions and cleanup tasks"""
        try:
            _runtime.LOGGER.info("Initiating WebRTC manager shutdown...")

            session_ids = (
                set(self.session_peers.keys())
                | set(self.datachannel_managers.keys())
                | set(self.session_establishment_tasks.keys())
                | set(self.session_disconnect_grace_tasks.keys())
                | set(self.pending_candidates.keys())
                | set(self.outgoing_trickle_candidates.keys())
                | set(self.voice_command_queues.keys())
                | set(self.voice_command_workers.keys())
                | set(self.pending_voice_chat_messages.keys())
                | set(self.session_message_tasks.keys())
                | set(self.session_reconnect_survivable_tasks.keys())
                | set(self.cleanup_tasks.keys())
                | set(self.audio_sinks.keys())
                | set(self.video_sinks.keys())
                | set(self.audio_transceivers.keys())
                | set(self.background_audio_state_by_session.keys())
                | set(self.silent_recorders.keys())
                | set(getattr(_runtime.STATE, "audio_managers", {}).keys())
            )

            for session_id in sorted(session_ids):
                try:
                    await _runtime.asyncio.wait_for(self.async_cleanup_session(session_id), timeout=5.0)
                except _runtime.asyncio.TimeoutError:
                    _runtime.LOGGER.warning(f"Timed out cleaning WebRTC session {session_id} during shutdown")
                except Exception as e:
                    _runtime.LOGGER.warning(f"Error cleaning WebRTC session {session_id} during shutdown: {e}")

            for session_id, task in list(self.cleanup_tasks.items()):
                try:
                    await self._cancel_task_collection_with_timeout(
                        [task],
                        session_id=session_id,
                        label="cleanup",
                    )
                except Exception as e:
                    _runtime.LOGGER.warning(f"Error cancelling cleanup task for session {session_id}: {e}")

            for session_id, task in list(self.session_establishment_tasks.items()):
                try:
                    await self._cancel_task_collection_with_timeout(
                        [task],
                        session_id=session_id,
                        label="establish-timeout",
                    )
                except Exception as e:
                    _runtime.LOGGER.warning(f"Error cancelling establish-timeout task for session {session_id}: {e}")

            for session_id, task in list(self.session_disconnect_grace_tasks.items()):
                try:
                    await self._cancel_task_collection_with_timeout(
                        [task],
                        session_id=session_id,
                        label="disconnect-grace",
                    )
                except Exception as e:
                    _runtime.LOGGER.warning(f"Error cancelling disconnect-grace task for session {session_id}: {e}")

            for session_id in list(self.session_reconnect_survivable_tasks.keys()):
                try:
                    await self._cancel_session_reconnect_survivable_tasks(session_id)
                except Exception as e:
                    _runtime.LOGGER.warning(
                        f"Error cancelling reconnect-survivable task for session {session_id}: {e}"
                    )

            for session_id, audio_manager in list(getattr(_runtime.STATE, "audio_managers", {}).items()):
                try:
                    audio_manager.close()
                    _runtime.LOGGER.info(f"Closed orphan audio manager during shutdown: {session_id}")
                except Exception as e:
                    _runtime.LOGGER.warning(f"Error closing orphan audio manager {session_id}: {e}")
            _runtime.STATE.audio_managers.clear()

            for session_id, audio_sink in list(self.audio_sinks.items()):
                try:
                    if await self._await_cleanup_awaitable(
                        audio_sink.stop(),
                        session_id=session_id,
                        label="orphan audio sink",
                    ):
                        _runtime.LOGGER.info(f"Stopped orphan audio sink during shutdown: {session_id}")
                except Exception as e:
                    _runtime.LOGGER.warning(f"Error stopping orphan audio sink {session_id}: {e}")
            self.audio_sinks.clear()
            self.audio_transceivers.clear()

            for session_id, recorder in list(self.silent_recorders.items()):
                try:
                    recorder.close()
                    _runtime.LOGGER.info(f"Closed orphan silent recording writer during shutdown: {session_id}")
                except Exception as e:
                    _runtime.LOGGER.warning(f"Error closing orphan silent recording writer {session_id}: {e}")
            self.silent_recorders.clear()
            self.background_audio_state_by_session.clear()
            self._voice_chat_flush_locks.clear()
            self._offline_queue_flush_locks.clear()

            for session_id, video_sink in list(self.video_sinks.items()):
                try:
                    if await self._await_cleanup_awaitable(
                        video_sink.stop(),
                        session_id=session_id,
                        label="orphan video sink",
                    ):
                        _runtime.LOGGER.info(f"Stopped orphan video sink during shutdown: {session_id}")
                except Exception as e:
                    _runtime.LOGGER.warning(f"Error stopping orphan video sink {session_id}: {e}")
            self.video_sinks.clear()

            released_control_leases: set[int] = set()
            for session_id, lease in list(self.remote_desktop_control_leases_by_session.items()):
                if id(lease) in released_control_leases:
                    continue
                if await self._release_remote_desktop_control(
                    session_id,
                    str(lease.get("control_id") or ""),
                    expected_lease=lease,
                ):
                    released_control_leases.add(id(lease))
            self.remote_desktop_control_leases_by_session.clear()

            for _sid, desktop_track in list(self.desktop_video_tracks.items()):
                bitrate_task = getattr(desktop_track, "_autoyou_desktop_video_bitrate_task", None)
                if isinstance(bitrate_task, _runtime.asyncio.Task):
                    bitrate_task.cancel()
                desktop_track._autoyou_desktop_video_sender = None
                try:
                    desktop_track.disable()
                except Exception:
                    pass
            self.desktop_video_tracks.clear()
            self.audio_transceivers.clear()

            self.cleanup_tasks.clear()
            self.session_establishment_tasks.clear()
            self.session_disconnect_grace_tasks.clear()
            self._cleanup_locks.clear()
            self.session_peers.clear()
            self.datachannel_managers.clear()
            self.voice_call_status_by_session.clear()
            self.voice_call_playback_by_session.clear()
            self.voice_call_client_active_by_session.clear()
            self.screen_sessions.clear()
            self.screen_inputs.clear()
            self.voice_command_queues.clear()
            self.voice_command_workers.clear()
            self.pending_voice_chat_messages.clear()
            self._offline_pending_messages.clear()
            self._voice_dc_session_id.clear()
            self.session_message_tasks.clear()
            self.session_reconnect_survivable_tasks.clear()
            self.http_proxy_request_tasks.clear()
            self.pending_candidates.clear()
            self.seen_remote_ice_candidates.clear()
            self.outgoing_trickle_candidates.clear()
            self._ws_fragment_buffers.clear()
            if hasattr(self, "_ws_connections"):
                self._ws_connections.clear()
            if hasattr(self, "_ws_session_requests"):
                self._ws_session_requests.clear()

            # Close the shared httpx client so its connection pool is drained cleanly.
            if hasattr(self, '_http_client'):
                try:
                    await self._http_client.aclose()
                except Exception:
                    pass
                del self._http_client

            try:
                await self._cancel_lingering_webrtc_teardown_tasks(timeout_seconds=0.5)
            except Exception as exc:
                _runtime.LOGGER.debug("Failed to cancel lingering WebRTC teardown tasks during shutdown: %s", exc)

            _runtime.LOGGER.info("WebRTC manager shutdown completed")

        except _runtime.asyncio.CancelledError:
            _runtime.LOGGER.warning("WebRTC manager shutdown was cancelled; forcing WebRTC state cleanup")
            raise
        except Exception as e:
            _runtime.LOGGER.error(f"Error during WebRTC manager shutdown: {e}")
        finally:
            await self._force_clear_shutdown_state()
