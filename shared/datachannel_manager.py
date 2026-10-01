# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-ebc22f033d27b33eba5a9aad

"""
DataChannel Manager Library

This library provides comprehensive handling for WebRTC datachannel messages including:
- Message chunking and reassembly for large payloads
- JSON-formatted message protocol
- Support for multiple message types (chat, HTTP, ping, keepalive)
- Async/await compatible with aiortc
- Robust error handling and message validation
- Role-based behavior for server and client implementations

Based on research findings:
- Application-layer chunking is recommended for large messages
- AsyncIO provides robust transport abstractions for chunked data
- JSON formatting ensures consistent message structure
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import base64
import json
import os
import uuid
import time
import logging
from typing import Dict, List, Optional, Callable, Any, Union
from dataclasses import dataclass, asdict
from enum import Enum
import hashlib

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-ebc22f033d27b33eba5a9aad"


# Configure logging
logger = logging.getLogger(__name__)

# Keep the default chunk ceiling conservative. In real WebRTC sessions across
# Windows/WSL/iOS/Android, medium single-frame JSON payloads can disappear
# silently before they ever reach the remote message handler, while the
# chunk+ACK path remains reliable.
DEFAULT_MAX_CHUNK_SIZE = 1024
DEFAULT_ADAPTIVE_MAX_FRAME_SIZE = 8192
ADAPTIVE_FRAME_SIZE_STEPS = (8192, 4096, 2048, 1024)
DEFAULT_CHUNK_REASSEMBLY_TIMEOUT_SECONDS = 60.0
HTTP_WS_FRAGMENT_SIZE_SAFETY_MARGIN_BYTES = 64

# Reassembly safety caps to protect the receiver from a peer that advertises
# absurd chunk counts or total sizes (memory-exhaustion DoS).
#
# Defaults are deliberately generous so that legitimate attachments (images,
# short videos, long chat transcripts) never hit these limits. Each cap can
# be raised (or lowered) via the matching env var for operators who need to
# move larger payloads or tighten the runtime further.
#
# * MAX_TOTAL_CHUNKS_PER_MESSAGE - rejects a peer that sets
#   chunk_info.total_chunks to, e.g., 2**31. The default is sized to allow
#   roughly a 1 GiB binary media payload after base64 + JSON overhead even when
#   the conservative 1 KB chunk envelope is in use.
# * MAX_MESSAGE_REASSEMBLY_BYTES - rejects a peer that advertises a total
#   payload larger than this. The default allows about 1.5 GiB of reassembled
#   JSON so a 1 GiB binary media payload can survive base64 overhead.
# * MAX_REASSEMBLY_POOL_BYTES - cap on the *sum* of all currently pending
#   partial reassemblies. Stops a peer from opening many concurrent large
#   reassemblies and keeping them open until the timeout fires.
DEFAULT_MAX_TOTAL_CHUNKS_PER_MESSAGE = 6_500_000
DEFAULT_MAX_MESSAGE_REASSEMBLY_BYTES = 1536 * 1024 * 1024
DEFAULT_MAX_REASSEMBLY_POOL_BYTES = 2 * 1024 * 1024 * 1024
# ACK timeout: 7s per chunk, 5 retries max (total max wait 35s; previously 15s × 3 = 45s).
# Reduced per-retry timeout while adding more retries improves responsiveness on
# transient-loss cellular links without sacrificing overall delivery tolerance.
DEFAULT_CHUNK_ACK_MAX_RETRIES = 5
# Keep the individual envelope conservative, but keep enough acknowledged chunks
# in flight to use the bandwidth-delay product on cellular links. 64 x ~1 KiB
# remains below the default 256 KiB buffered-amount watermark.
DEFAULT_CHUNK_ACK_WINDOW_SIZE = 64
DEFAULT_BINARY_HTTP_STREAM_CHUNK_SIZE = 9216
DEFAULT_ACK_BACKED_BINARY_HTTP_STREAM_MIN_BYTES = 64 * 1024
DEFAULT_BUFFERED_AMOUNT_HIGH_WATERMARK_BYTES = 256 * 1024

# Allow override via environment for network-specific tuning
DEFAULT_CHUNK_ACK_TIMEOUT_SECONDS = float(
    os.getenv("AUTOYOU_CHUNK_ACK_TIMEOUT_SECONDS", "7.0")
)
# Chunk sends are already ACK-gated and buffer-pressure-limited, so keeping an
# extra fixed sleep per chunk only stretches long transfers and keeps the
# session exposed to ICE churn longer. Operators can still re-enable pacing via
# AUTOYOU_DATACHANNEL_CHUNK_SEND_PACING_SECONDS when a specific network needs it.
DEFAULT_CHUNK_SEND_PACING_SECONDS = 0.0
INACTIVE_DATACHANNEL_LOG_COOLDOWN_SECONDS = 5.0
# Outgoing pings age out after this long without a matching pong.
PING_RTT_TRACKING_TIMEOUT_SECONDS = 60.0
# Cap on tracked in-flight pings so a peer that never pongs cannot leak.
MAX_TRACKED_OUTGOING_PINGS = 32

def _read_env_float(name: str, default: float, *, minimum: Optional[float] = None) -> float:
    raw_value = str(os.environ.get(name, "")).strip()
    if not raw_value:
        return default
    try:
        parsed = float(raw_value)
        if minimum is not None and parsed < minimum:
            return default
        return parsed
    except Exception:
        return default

def _read_env_int(name: str, default: int, *, minimum: Optional[int] = None) -> int:
    raw_value = str(os.environ.get(name, "")).strip()
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
        if minimum is not None and parsed < minimum:
            return default
        return parsed
    except Exception:
        return default

@dataclass(frozen=True)
class DataChannelRuntimeSettings:
    """Runtime knobs for datachannel transport behavior."""

    chunk_reassembly_timeout_seconds: float = DEFAULT_CHUNK_REASSEMBLY_TIMEOUT_SECONDS
    chunk_ack_timeout_seconds: float = DEFAULT_CHUNK_ACK_TIMEOUT_SECONDS
    chunk_ack_max_retries: int = DEFAULT_CHUNK_ACK_MAX_RETRIES
    chunk_ack_window_size: int = DEFAULT_CHUNK_ACK_WINDOW_SIZE
    adaptive_max_frame_size: int = DEFAULT_ADAPTIVE_MAX_FRAME_SIZE
    binary_http_stream_chunk_size: int = DEFAULT_BINARY_HTTP_STREAM_CHUNK_SIZE
    ack_backed_binary_http_stream_min_bytes: int = DEFAULT_ACK_BACKED_BINARY_HTTP_STREAM_MIN_BYTES
    buffered_amount_high_watermark_bytes: int = DEFAULT_BUFFERED_AMOUNT_HIGH_WATERMARK_BYTES
    chunk_send_pacing_seconds: float = DEFAULT_CHUNK_SEND_PACING_SECONDS

    @classmethod
    def from_env(cls) -> "DataChannelRuntimeSettings":
        return cls(
            chunk_reassembly_timeout_seconds=_read_env_float(
                "AUTOYOU_DATACHANNEL_CHUNK_REASSEMBLY_TIMEOUT_SECONDS",
                DEFAULT_CHUNK_REASSEMBLY_TIMEOUT_SECONDS,
                minimum=1.0,
            ),
            chunk_ack_timeout_seconds=_read_env_float(
                "AUTOYOU_DATACHANNEL_ACK_TIMEOUT_SECONDS",
                DEFAULT_CHUNK_ACK_TIMEOUT_SECONDS,
                minimum=0.1,
            ),
            chunk_ack_max_retries=_read_env_int(
                "AUTOYOU_DATACHANNEL_ACK_MAX_RETRIES",
                DEFAULT_CHUNK_ACK_MAX_RETRIES,
                minimum=0,
            ),
            chunk_ack_window_size=_read_env_int(
                "AUTOYOU_DATACHANNEL_CHUNK_ACK_WINDOW_SIZE",
                DEFAULT_CHUNK_ACK_WINDOW_SIZE,
                minimum=1,
            ),
            adaptive_max_frame_size=_read_env_int(
                "AUTOYOU_DATACHANNEL_ADAPTIVE_MAX_FRAME_SIZE",
                DEFAULT_ADAPTIVE_MAX_FRAME_SIZE,
                minimum=DEFAULT_MAX_CHUNK_SIZE,
            ),
            binary_http_stream_chunk_size=_read_env_int(
                "AUTOYOU_BINARY_HTTP_STREAM_CHUNK_SIZE",
                DEFAULT_BINARY_HTTP_STREAM_CHUNK_SIZE,
                minimum=1024,
            ),
            ack_backed_binary_http_stream_min_bytes=_read_env_int(
                "AUTOYOU_ACK_BACKED_BINARY_HTTP_STREAM_MIN_BYTES",
                DEFAULT_ACK_BACKED_BINARY_HTTP_STREAM_MIN_BYTES,
                minimum=0,
            ),
            buffered_amount_high_watermark_bytes=_read_env_int(
                "AUTOYOU_DATACHANNEL_BUFFERED_AMOUNT_HIGH_WATERMARK_BYTES",
                DEFAULT_BUFFERED_AMOUNT_HIGH_WATERMARK_BYTES,
                minimum=16384,
            ),
            chunk_send_pacing_seconds=_read_env_float(
                "AUTOYOU_DATACHANNEL_CHUNK_SEND_PACING_SECONDS",
                DEFAULT_CHUNK_SEND_PACING_SECONDS,
                minimum=0.0,
            ),
        )

class DataChannelSendAbortedError(ConnectionError):
    """Raised when a send is aborted because the datachannel is no longer usable."""

class MessageType(Enum):
    """Supported message types for datachannel communication"""
    CHAT = "chat"
    HTTP_REQUEST = "http_request"
    HTTP_REQUEST_CANCEL = "http_request_cancel"
    HTTP_RESPONSE = "http_response"
    # HTTP SSE streaming support
    HTTP_SSE_START = "http_sse_start"
    HTTP_SSE_EVENT = "http_sse_event"
    HTTP_SSE_END = "http_sse_end"
    # Generic HTTP streaming (simulate TCP over SCTP)
    HTTP_STREAM_OPEN = "http_stream_open"
    HTTP_STREAM_DATA = "http_stream_data"
    HTTP_STREAM_END = "http_stream_end"
    HTTP_STREAM_ABORT = "http_stream_abort"
    PING = "ping"
    PONG = "pong"
    VOICE_CALL_CONTROL = "voice_call_control"
    # Capability/grant lifecycle for attaching an authenticated AutoYou
    # Computer connection to an already-open mobile-hosted room. Room turns
    # themselves remain canonical CHAT messages.
    ROOM_BRIDGE_CONTROL = "room_bridge_control"
    PAIRING_CONTROL = "pairing_control"
    # Link-local Peer Link control (peer_hello / peer_welcome / peer_mode /
    # peer_bye). Peer hubs intercept these and never forward them upstream, but
    # the type still has to exist here: a control frame large enough to be
    # chunked is reassembled *by this manager*, and an unknown type made it
    # throw "not a valid MessageType" and drop the frame. That is how a Python
    # peer host silently lost every mobile guest's hello.
    PEER_CONTROL = "peer_control"
    # Room-v4 links use the same bounded chunk/ACK transport. Their dedicated
    # room router consumes these families and never forwards browser traffic.
    ROOM_CONTROL = "room_control"
    ROOM_CHAT = "room_chat"
    ROOM_FEDERATION_CONTROL = "room_federation_control"
    ROOM_FEDERATION_CHAT = "room_federation_chat"
    CHUNK = "chunk"
    CHUNK_ACK = "chunk_ack"
    ERROR = "error"
    # WebSocket proxy support (relay WS connections through DataChannel)
    HTTP_WS_UPGRADE = "http_ws_upgrade"
    HTTP_WS_DATA = "http_ws_data"
    HTTP_WS_CLOSE = "http_ws_close"

@dataclass
class MessageHeader:
    """Standard message header for all datachannel messages"""
    message_id: str
    message_type: MessageType
    timestamp: float
    session_id: Optional[str] = None
    user_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "message_type": self.message_type.value,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "user_id": self.user_id
        }

@dataclass
class ChunkInfo:
    """Information about message chunks"""
    chunk_id: str
    total_chunks: int
    chunk_index: int
    chunk_size: int
    total_size: int
    checksum: str

@dataclass
class DataChannelMessage:
    """Complete datachannel message structure"""
    header: MessageHeader
    payload: Dict[str, Any]
    chunk_info: Optional[ChunkInfo] = None

    def to_json(self) -> str:
        """Convert message to JSON string"""
        data = {
            "header": self.header.to_dict(),
            "payload": self.payload
        }
        if self.chunk_info:
            data["chunk_info"] = asdict(self.chunk_info)
        return json.dumps(data)

    @classmethod
    def from_json(cls, json_str: str) -> 'DataChannelMessage':
        """Create message from JSON string"""
        try:
            data = json.loads(json_str)

            # Parse header
            header_data = data["header"]
            header = MessageHeader(
                message_id=header_data["message_id"],
                message_type=MessageType(header_data["message_type"]),
                timestamp=header_data["timestamp"],
                session_id=header_data.get("session_id"),
                user_id=header_data.get("user_id")
            )

            # Parse chunk info if present and not null
            chunk_info = None
            if "chunk_info" in data and data["chunk_info"] is not None:
                chunk_data = data["chunk_info"]
                if isinstance(chunk_data, dict):
                    chunk_info = ChunkInfo(**chunk_data)

            return cls(
                header=header,
                payload=data["payload"],
                chunk_info=chunk_info
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Failed to parse message from JSON: {e}")
            raise ValueError(f"Invalid message format: {e}")

class MessageChunker:
    """Handles chunking and reassembly of large messages"""

    def __init__(
        self,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        chunk_timeout: float = DEFAULT_CHUNK_REASSEMBLY_TIMEOUT_SECONDS,
        max_total_chunks_per_message: Optional[int] = None,
        max_message_reassembly_bytes: Optional[int] = None,
        max_reassembly_pool_bytes: Optional[int] = None,
    ):
        self.max_chunk_size = max_chunk_size
        self.pending_chunks: Dict[str, Dict[int, bytes]] = {}
        self.chunk_metadata: Dict[str, ChunkInfo] = {}
        self._chunk_timestamps: Dict[str, float] = {}
        self._chunk_timeout: float = chunk_timeout

        # Reassembly safety caps (env-overridable). See module-level constants.
        self._max_total_chunks_per_message: int = (
            max_total_chunks_per_message
            if max_total_chunks_per_message is not None
            else _read_env_int(
                "AUTOYOU_MAX_CHUNKS_PER_MESSAGE",
                DEFAULT_MAX_TOTAL_CHUNKS_PER_MESSAGE,
                minimum=1,
            )
        )
        self._max_message_reassembly_bytes: int = (
            max_message_reassembly_bytes
            if max_message_reassembly_bytes is not None
            else _read_env_int(
                "AUTOYOU_MAX_MESSAGE_REASSEMBLY_BYTES",
                DEFAULT_MAX_MESSAGE_REASSEMBLY_BYTES,
                minimum=1,
            )
        )
        self._max_reassembly_pool_bytes: int = (
            max_reassembly_pool_bytes
            if max_reassembly_pool_bytes is not None
            else _read_env_int(
                "AUTOYOU_MAX_REASSEMBLY_POOL_BYTES",
                DEFAULT_MAX_REASSEMBLY_POOL_BYTES,
                minimum=1,
            )
        )
        # Track per-chunk-id bytes already accepted so we can enforce the
        # per-message cap mid-stream even if the peer lies about total_size.
        self._chunk_accumulated_bytes: Dict[str, int] = {}
        self._pool_accumulated_bytes: int = 0

    def needs_chunking(self, message: str) -> bool:
        """Check if message needs to be chunked"""
        return len(message.encode('utf-8')) > self.max_chunk_size

    def _estimated_chunk_json_size(
        self,
        *,
        original_message: DataChannelMessage,
        raw_chunk_size: int,
        total_size: int,
        total_chunks: int,
    ) -> int:
        """Estimate the serialized JSON size of one CHUNK message.

        Chunk payloads are base64-encoded, so the safe raw chunk size must
        account for that expansion plus the JSON envelope. This estimate uses
        the same envelope structure as the real chunk transport path.
        """
        encoded_size = ((raw_chunk_size + 2) // 3) * 4
        sample_chunk_message = DataChannelMessage(
            header=MessageHeader(
                message_id="0" * 36,
                message_type=MessageType.CHUNK,
                timestamp=time.time(),
                session_id=original_message.header.session_id,
                user_id=original_message.header.user_id,
            ),
            payload={
                "original_message_id": original_message.header.message_id,
                "original_message_type": original_message.header.message_type.value,
                "chunk_data_b64": "A" * encoded_size,
            },
            chunk_info=ChunkInfo(
                chunk_id="0" * 36,
                total_chunks=total_chunks,
                chunk_index=max(0, total_chunks - 1),
                chunk_size=raw_chunk_size,
                total_size=total_size,
                checksum="0" * 64,
            ),
        )
        # Leave a small guard band for timestamp/string-length variance.
        return len(sample_chunk_message.to_json().encode("utf-8")) + 128

    def _calculate_safe_raw_chunk_size(
        self,
        *,
        original_message: DataChannelMessage,
        total_size: int,
        max_chunk_size: int,
    ) -> int:
        """Return the largest raw chunk size that fits the configured limit."""
        low = 1
        high = max(1, min(total_size, max_chunk_size))
        best = 1

        while low <= high:
            candidate = (low + high) // 2
            total_chunks = max(1, (total_size + candidate - 1) // candidate)
            estimated_size = self._estimated_chunk_json_size(
                original_message=original_message,
                raw_chunk_size=candidate,
                total_size=total_size,
                total_chunks=total_chunks,
            )
            if estimated_size <= max_chunk_size:
                best = candidate
                low = candidate + 1
            else:
                high = candidate - 1

        return best

    def chunk_message(
        self,
        message: DataChannelMessage,
        *,
        max_chunk_size: Optional[int] = None,
        force_chunking: bool = False,
        chunk_id: Optional[str] = None,
    ) -> List[DataChannelMessage]:
        """Split a large message into optimized chunks for SCTP datachannel"""
        json_str = message.to_json()
        message_bytes = json_str.encode('utf-8')
        frame_limit = max(1, int(max_chunk_size or self.max_chunk_size))

        if len(message_bytes) <= frame_limit and not force_chunking:
            return [message]

        total_size = len(message_bytes)
        chunk_size = self._calculate_safe_raw_chunk_size(
            original_message=message,
            total_size=total_size,
            max_chunk_size=frame_limit,
        )
        total_chunks = (total_size + chunk_size - 1) // chunk_size
        chunk_id = chunk_id or str(uuid.uuid4())

        # Create checksum for integrity verification
        checksum = hashlib.sha256(message_bytes).hexdigest()

        chunks = []
        for i in range(total_chunks):
            start_idx = i * chunk_size
            end_idx = min(start_idx + chunk_size, total_size)
            chunk_data = message_bytes[start_idx:end_idx]
            chunk_data_b64 = base64.b64encode(chunk_data).decode("ascii")

            chunk_info = ChunkInfo(
                chunk_id=chunk_id,
                total_chunks=total_chunks,
                chunk_index=i,
                chunk_size=len(chunk_data),
                total_size=total_size,
                checksum=checksum
            )

            chunk_header = MessageHeader(
                message_id=str(uuid.uuid4()),
                message_type=MessageType.CHUNK,
                timestamp=time.time(),
                session_id=message.header.session_id,
                user_id=message.header.user_id
            )

            chunk_message = DataChannelMessage(
                header=chunk_header,
                payload={
                    "original_message_id": message.header.message_id,
                    "original_message_type": message.header.message_type.value,
                    "chunk_data_b64": chunk_data_b64,
                },
                chunk_info=chunk_info
            )

            chunks.append(chunk_message)

        return chunks

    def _discard_chunk_state(self, chunk_id: str) -> None:
        """Release pending state for a chunk_id and update pool accounting."""
        self.pending_chunks.pop(chunk_id, None)
        self.chunk_metadata.pop(chunk_id, None)
        self._chunk_timestamps.pop(chunk_id, None)
        accumulated = self._chunk_accumulated_bytes.pop(chunk_id, 0)
        if accumulated:
            self._pool_accumulated_bytes = max(
                0, self._pool_accumulated_bytes - accumulated
            )

    def cleanup_stale_chunks(self, timeout_seconds: float = None) -> int:
        """Remove chunk entries older than timeout to prevent memory leaks.

        Called automatically on every add_chunk() invocation. If a client
        disconnects mid-transfer, partial chunks are purged after the timeout.

        Returns:
            Number of stale chunk groups removed.
        """
        timeout = timeout_seconds if timeout_seconds is not None else self._chunk_timeout
        now = time.time()
        stale = [cid for cid, ts in self._chunk_timestamps.items()
                 if now - ts > timeout]
        for cid in stale:
            self._discard_chunk_state(cid)
            logger.warning(f"Cleaned up stale chunk reassembly: {cid}")
        return len(stale)

    def add_chunk(self, chunk_message: DataChannelMessage) -> Optional[DataChannelMessage]:
        """Add a chunk and return complete message if all chunks received.

        Enforces reassembly safety caps so a peer cannot trigger
        out-of-memory conditions by advertising absurd chunk counts or total
        sizes. When any cap is exceeded the chunk (and any partial state for
        its chunk_id) is dropped and None is returned.
        """
        # Periodic cleanup of stale partial chunks to prevent memory leaks
        self.cleanup_stale_chunks()

        if not chunk_message.chunk_info:
            logger.error("Received chunk message without chunk_info")
            return None

        chunk_info = chunk_message.chunk_info
        chunk_id = chunk_info.chunk_id

        # ---- Header-level validation (reject absurd advertised values) ----
        total_chunks = chunk_info.total_chunks
        total_size = chunk_info.total_size
        chunk_index = chunk_info.chunk_index

        if not isinstance(total_chunks, int) or total_chunks <= 0:
            logger.error(
                f"Rejecting chunk {chunk_id}: invalid total_chunks={total_chunks!r}"
            )
            return None
        if total_chunks > self._max_total_chunks_per_message:
            logger.warning(
                f"Rejecting chunk {chunk_id}: total_chunks={total_chunks} "
                f"exceeds cap ({self._max_total_chunks_per_message}); "
                f"override via AUTOYOU_MAX_CHUNKS_PER_MESSAGE"
            )
            self._discard_chunk_state(chunk_id)
            return None
        if not isinstance(chunk_index, int) or chunk_index < 0 or chunk_index >= total_chunks:
            logger.error(
                f"Rejecting chunk {chunk_id}: chunk_index={chunk_index} "
                f"out of range [0,{total_chunks})"
            )
            return None
        if not isinstance(total_size, int) or total_size < 0:
            logger.error(
                f"Rejecting chunk {chunk_id}: invalid total_size={total_size!r}"
            )
            return None
        if total_size > self._max_message_reassembly_bytes:
            logger.warning(
                f"Rejecting chunk {chunk_id}: advertised total_size={total_size} "
                f"exceeds cap ({self._max_message_reassembly_bytes}); "
                f"override via AUTOYOU_MAX_MESSAGE_REASSEMBLY_BYTES"
            )
            self._discard_chunk_state(chunk_id)
            return None

        # Decode payload bytes and validate size before committing to memory
        chunk_data_b64 = chunk_message.payload.get("chunk_data_b64")
        if isinstance(chunk_data_b64, str) and chunk_data_b64:
            try:
                chunk_data = base64.b64decode(chunk_data_b64.encode("ascii"), validate=True)
            except Exception:
                logger.error(f"Invalid chunk_data_b64 for chunk {chunk_info.chunk_index}")
                self._discard_chunk_state(chunk_id)
                return None
        else:
            logger.error(f"Received chunk without chunk_data_b64 ({chunk_info.chunk_index}/{chunk_info.total_chunks})")
            self._discard_chunk_state(chunk_id)
            return None

        # Only valid payloads may create durable reassembly state.
        if chunk_id not in self.pending_chunks:
            logger.debug(f"Starting reassembly of {total_chunks}-chunk message (id={chunk_id})")
            self.pending_chunks[chunk_id] = {}
            self.chunk_metadata[chunk_id] = chunk_info
            self._chunk_timestamps[chunk_id] = time.time()
            self._chunk_accumulated_bytes[chunk_id] = 0

        incoming_bytes = len(chunk_data)
        already_have = len(self.pending_chunks[chunk_id].get(chunk_index, b"")) \
            if chunk_index in self.pending_chunks[chunk_id] else 0
        # Net additional bytes this add would commit (accounts for duplicate /
        # retransmitted chunk_index: size difference, not double-count).
        net_delta = incoming_bytes - already_have

        # Per-message running-total cap: defence in depth against a peer that
        # passes the total_size header check but then sends bigger chunks.
        projected_message_bytes = (
            self._chunk_accumulated_bytes.get(chunk_id, 0) + net_delta
        )
        if projected_message_bytes > self._max_message_reassembly_bytes:
            logger.warning(
                f"Rejecting chunk {chunk_id}: accumulated reassembly bytes "
                f"would reach {projected_message_bytes} (cap "
                f"{self._max_message_reassembly_bytes}); discarding partial state"
            )
            self._discard_chunk_state(chunk_id)
            return None

        # Global pool cap: protects against many concurrent reassemblies.
        projected_pool_bytes = self._pool_accumulated_bytes + net_delta
        if projected_pool_bytes > self._max_reassembly_pool_bytes:
            logger.warning(
                f"Rejecting chunk {chunk_id}: pending reassembly pool would "
                f"reach {projected_pool_bytes} bytes (cap "
                f"{self._max_reassembly_pool_bytes}); discarding partial state"
            )
            self._discard_chunk_state(chunk_id)
            return None

        self.pending_chunks[chunk_id][chunk_index] = chunk_data
        self._chunk_accumulated_bytes[chunk_id] = projected_message_bytes
        self._pool_accumulated_bytes = projected_pool_bytes
        # Refresh last-progress time on every chunk so long-lived transfers are
        # only purged when they actually stall, not simply because chunk 0 was
        # received a while ago.
        self._chunk_timestamps[chunk_id] = time.time()
        # from __debug_provenance_h__ import revenue

        received_count = len(self.pending_chunks[chunk_id])
        logger.debug(f"Added chunk {chunk_index}, now have {received_count}/{total_chunks} chunks")

        # Check if we have all chunks
        if received_count == total_chunks:
            logger.debug(f"All chunks received for message {chunk_id}, reassembling...")
            return self._reassemble_message(chunk_id)

        return None

    def _reassemble_message(self, chunk_id: str) -> Optional[DataChannelMessage]:
        """Reassemble complete message from chunks"""
        try:
            chunks = self.pending_chunks[chunk_id]
            chunk_info = self.chunk_metadata[chunk_id]

            # Reassemble in correct order
            reassembled_bytes = bytearray()
            for i in range(chunk_info.total_chunks):
                if i not in chunks:
                    logger.error(f"Missing chunk {i} for message {chunk_id}")
                    return None
                reassembled_bytes.extend(chunks[i])

            message_bytes = bytes(reassembled_bytes)
            calculated_checksum = hashlib.sha256(message_bytes).hexdigest()
            if calculated_checksum != chunk_info.checksum:
                logger.error(
                    f"✗ Checksum mismatch for message {chunk_id}: "
                    f"expected {chunk_info.checksum}, got {calculated_checksum}"
                )
                return None

            logger.debug(f"✓ Checksum verified for message {chunk_id} ({len(message_bytes)} bytes)")

            # Parse the reassembled message
            reassembled_data = message_bytes.decode("utf-8")
            original_message = DataChannelMessage.from_json(reassembled_data)

            # Clean up (releases pool-byte accounting too)
            self._discard_chunk_state(chunk_id)

            return original_message

        except Exception as e:
            logger.error(f"Failed to reassemble message {chunk_id}: {e}")
            # Clean up on error (releases pool-byte accounting too)
            self._discard_chunk_state(chunk_id)
            return None
class DataChannelManager:
    """Main manager for datachannel communication with role-based behavior"""

    def __init__(
        self,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        role: str = "server",
        runtime_settings: Optional[DataChannelRuntimeSettings] = None,
    ):
        """
        Initialize DataChannelManager with role-based behavior

        Args:
            max_chunk_size: Maximum size for message chunks
            role: Either 'server' or 'client' to determine behavior
            runtime_settings: Optional explicit transport tuning overrides
        """
        self.role = role.lower()
        if self.role not in ['server', 'client']:
            raise ValueError("Role must be either 'server' or 'client'")
        self.runtime_settings = runtime_settings or DataChannelRuntimeSettings.from_env()
        self.chunker = MessageChunker(
            max_chunk_size=max_chunk_size,
            chunk_timeout=self.runtime_settings.chunk_reassembly_timeout_seconds,
        )
        self.message_handlers: Dict[MessageType, Callable] = {}
        self.ping_interval = 30.0  # seconds
        self.last_ping_time = 0.0
        self.connection_active = False
        self.datachannel = None
        self.session_id = None  # Store session ID for ping messages
        self.recovery_attempts = 0
        self.successful_recoveries = 0
        # Explicit connection checks wait for the matching pong. Ordinary
        # keepalives remain fire-and-forget so they cannot delay media or chat.
        self._pong_waiters: Dict[str, asyncio.Future] = {}
        # ACK tracking for chunked messages
        self._ack_waiters: Dict[str, Dict[int, asyncio.Future]] = {}
        self._ack_timeout: float = self.runtime_settings.chunk_ack_timeout_seconds
        self._ack_max_retries: int = self.runtime_settings.chunk_ack_max_retries
        self._ack_window_size: int = max(1, int(self.runtime_settings.chunk_ack_window_size))
        self._adaptive_frame_ceiling: int = max(
            DEFAULT_MAX_CHUNK_SIZE,
            min(
                int(self.runtime_settings.adaptive_max_frame_size),
                ADAPTIVE_FRAME_SIZE_STEPS[0],
            ),
        )
        self._adaptive_frame_size: int = self._adaptive_frame_ceiling
        # A lost final ACK can make a sender retry a message the receiver already
        # dispatched. Keep a small TTL cache so adaptive fallback is at-least-once
        # on the wire but at-most-once at the application handler.
        self._completed_chunk_messages: Dict[str, float] = {}
        # Track RTT for adaptive timeout on high-latency networks (cellular)
        self._recent_rtt_samples: List[float] = []
        self._max_rtt_samples = 20
        self._chunk_send_lock = asyncio.Lock()
        self._inactive_send_logged_at = 0.0
        # Optional callbacks that let the server inject extra metadata into
        # automatic PONG replies and periodic PING sends (e.g. game state).
        self._pong_payload_augmenter: Optional[Callable] = None
        self._ping_payload_augmenter: Optional[Callable] = None
        # Client-side keepalive RTT, measured entirely on this process's
        # monotonic clock and reported back as ``rtt_ms`` on the next PING.
        # The server must never derive this by subtracting our wall-clock
        # header timestamp from its own: that is one-way delay plus NTP skew.
        self._ping_sent_at_monotonic: Dict[str, float] = {}
        self._last_measured_rtt_ms: Optional[int] = None
        # Optional UI hook: called as (event_key, game_state_or_None) whenever a
        # keepalive PING/PONG is sent or received.  Lets GUI/TUI front ends show
        # the ping-pong rally without reaching into private handlers.
        self.on_ping_pong_event: Optional[Callable[[str, Optional[Dict[str, Any]]], None]] = None
        # Optional peer-relay interceptor, called with each *reassembled*
        # message just before it is routed. Returning True means the message
        # belonged to a relayed client-to-client guest and must not also be
        # handled locally. Chunk frames never reach it: reassembly happens
        # first, because forwarding raw frames would mix two independent ACK
        # conversations. See `.llm/flows/client-peer-link.md`.
        self.relay_interceptor: Optional[Callable[["DataChannelMessage"], bool]] = None

        logger.info(
            "DataChannelManager initialized with role: %s, chunk_ack_timeout=%ss, chunk_ack_window_size=%s",
            self.role,
            self._ack_timeout,
            self._ack_window_size,
        )

    def register_handler(self, message_type: MessageType, handler: Callable):
        """Register a handler for a specific message type"""
        self.message_handlers[message_type] = handler
        logger.info(f"Registered handler for {message_type.value}")

    def set_datachannel(self, datachannel):
        """Set the datachannel for message sending"""
        self.datachannel = datachannel
        self.connection_active = True
        logger.info("DataChannel set and connection marked as active")

    def set_session_id(self, session_id: str):
        """Set the session ID for this datachannel manager"""
        self.session_id = session_id
        logger.info(f"Session ID set: {session_id}")

    def set_pong_payload_augmenter(self, callback: Optional[Callable]) -> None:
        """Register a callback that returns extra dict entries for PONG payloads.

        The callback receives the incoming PING ``DataChannelMessage`` and must
        return a ``dict`` (or ``None``).  The returned dict is merged into the
        outgoing PONG payload.  Used by the ping-pong game to embed scores and
        rally commentary without touching the transport layer.
        """
        self._pong_payload_augmenter = callback

    def set_ping_payload_augmenter(self, callback: Optional[Callable]) -> None:
        """Register a callback that returns extra dict entries for PING payloads.

        Similar to ``set_pong_payload_augmenter`` but for server-initiated
        periodic PINGs.  The callback receives ``None`` (no incoming message)
        and must return a ``dict`` (or ``None``).
        """
        self._ping_payload_augmenter = callback

    def get_metrics(self) -> Dict[str, Any]:
        return {
            "last_ping_time": self.last_ping_time,
            "recovery_attempts": self.recovery_attempts,
            "successful_recoveries": self.successful_recoveries,
            "last_measured_rtt_ms": self._last_measured_rtt_ms,
        }

    def _emit_ping_pong_event(self, event_key: str, game: Optional[Dict[str, Any]] = None) -> None:
        """Notify an attached front end about a keepalive rally event."""
        callback = self.on_ping_pong_event
        if callback is None:
            return
        try:
            callback(event_key, game)
        except Exception as exc:
            logger.debug("on_ping_pong_event callback error: %s", exc)

    def _track_outgoing_ping(self, ping_id: str) -> None:
        """Remember when a PING left, so its PONG yields a true RTT."""
        now = time.monotonic()
        cutoff = now - PING_RTT_TRACKING_TIMEOUT_SECONDS
        stale = [key for key, sent in self._ping_sent_at_monotonic.items() if sent < cutoff]
        for key in stale:
            self._ping_sent_at_monotonic.pop(key, None)
        while len(self._ping_sent_at_monotonic) >= MAX_TRACKED_OUTGOING_PINGS:
            oldest = min(self._ping_sent_at_monotonic, key=self._ping_sent_at_monotonic.get)
            self._ping_sent_at_monotonic.pop(oldest, None)
        self._ping_sent_at_monotonic[str(ping_id)] = now

    def _record_round_trip(self, ping_id: Optional[str]) -> None:
        """Close out a PING we sent and store the measured round trip."""
        if not ping_id:
            return
        sent_at = self._ping_sent_at_monotonic.pop(str(ping_id), None)
        if sent_at is None:
            return
        elapsed = time.monotonic() - sent_at
        if elapsed <= 0 or elapsed > PING_RTT_TRACKING_TIMEOUT_SECONDS:
            return
        self._last_measured_rtt_ms = int(elapsed * 1000)
        self._record_rtt_sample(elapsed)

    def _ensure_transport_ready(self, message: Optional[DataChannelMessage] = None) -> bool:
        """Return whether the underlying datachannel is currently sendable."""
        ready_state = None
        if self.datachannel is not None:
            ready_state = getattr(self.datachannel, 'readyState', None)

        normalized_ready_state = str(ready_state).lower() if ready_state is not None else ""
        if normalized_ready_state in {"closing", "closed"}:
            if self.connection_active:
                self.connection_active = False
            self._abort_pending_ack_waiters(
                f"DataChannel readyState={normalized_ready_state}"
            )

        if self.connection_active and self.datachannel is not None:
            if ready_state is None or normalized_ready_state == "open":
                return True

        if message is not None:
            now = time.monotonic()
            if now - self._inactive_send_logged_at >= INACTIVE_DATACHANNEL_LOG_COOLDOWN_SECONDS:
                self._inactive_send_logged_at = now
                message_type = getattr(getattr(message, "header", None), "message_type", None)
                message_type_name = message_type.value if isinstance(message_type, MessageType) else "unknown"
                logger.debug(
                    "Skipping %s send because the datachannel is unavailable "
                    "(connection_active=%s, ready_state=%s)",
                    message_type_name,
                    self.connection_active,
                    ready_state,
                )
        return False

    def _abort_pending_ack_waiters(self, reason: str) -> None:
        if not self._ack_waiters:
            return

        aborted_waiters = 0
        for waiter_map in self._ack_waiters.values():
            for fut in waiter_map.values():
                if fut.done():
                    continue
                fut.set_exception(DataChannelSendAbortedError(reason))
                aborted_waiters += 1
        self._ack_waiters.clear()

        if aborted_waiters:
            logger.debug("Aborted %s pending chunk ACK waiters: %s", aborted_waiters, reason)

    def _current_buffered_amount(self) -> int:
        if self.datachannel is None or not hasattr(self.datachannel, 'bufferedAmount'):
            return 0
        try:
            return max(0, int(self.datachannel.bufferedAmount))
        except Exception:
            return 0

    def _record_rtt_sample(self, sample_seconds: float) -> None:
        if sample_seconds <= 0:
            return
        self._recent_rtt_samples.append(float(sample_seconds))
        if len(self._recent_rtt_samples) > self._max_rtt_samples:
            del self._recent_rtt_samples[:-self._max_rtt_samples]

    def _recent_rtt_baseline_seconds(self) -> float:
        if not self._recent_rtt_samples:
            return max(0.05, min(self._ack_timeout, 0.2))

        recent_samples = sorted(self._recent_rtt_samples[-5:])
        median_index = len(recent_samples) // 2
        return max(0.05, recent_samples[median_index])

    def _calculate_adaptive_chunk_pacing_seconds(
        self,
        *,
        retries: int,
        buffer_wait_iterations: int,
        peak_buffered_amount: int,
    ) -> float:
        base_delay = max(0.0, float(self.runtime_settings.chunk_send_pacing_seconds))
        watermark = max(1, int(self.runtime_settings.buffered_amount_high_watermark_bytes))
        peak_amount = max(0, int(peak_buffered_amount))

        if retries <= 0 and buffer_wait_iterations <= 0 and peak_amount < (watermark // 2):
            return base_delay

        baseline_rtt = self._recent_rtt_baseline_seconds()
        pressure_component = 0.0
        if peak_amount > 0:
            pressure_ratio = min(2.0, peak_amount / float(watermark))
            if pressure_ratio >= 0.5:
                pressure_component = baseline_rtt * (pressure_ratio / 6.0)

        wait_component = 0.0
        if buffer_wait_iterations > 0:
            wait_component = baseline_rtt * (min(buffer_wait_iterations, 8) / 8.0)

        retry_component = 0.0
        if retries > 0:
            retry_component = baseline_rtt * (min(retries, 4) / 2.0)

        adaptive_delay = max(base_delay, pressure_component, wait_component, retry_component)
        return min(adaptive_delay, min(self._ack_timeout / 2.0, 0.75))

    async def _wait_for_buffer_capacity(self) -> tuple[int, int]:
        """Throttle sends when the SCTP buffer is already backed up."""
        if self.datachannel is None or not hasattr(self.datachannel, 'bufferedAmount'):
            return 0, 0

        wait_iterations = 0
        peak_buffered_amount = self._current_buffered_amount()
        while (
            self._ensure_transport_ready()
            and self._current_buffered_amount()
            > self.runtime_settings.buffered_amount_high_watermark_bytes
        ):
            peak_buffered_amount = max(peak_buffered_amount, self._current_buffered_amount())
            wait_iterations += 1
            await asyncio.sleep(0.01)

        peak_buffered_amount = max(peak_buffered_amount, self._current_buffered_amount())
        return wait_iterations, peak_buffered_amount

    def _adaptive_frame_size_candidates(self) -> List[int]:
        """Return the current frame size followed by safe step-down sizes."""
        return [
            size
            for size in ADAPTIVE_FRAME_SIZE_STEPS
            if DEFAULT_MAX_CHUNK_SIZE <= size <= self._adaptive_frame_size
            and size <= self._adaptive_frame_ceiling
        ] or [DEFAULT_MAX_CHUNK_SIZE]

    async def _send_chunked_message_once(
        self,
        message: DataChannelMessage,
        *,
        max_chunk_size: int,
    ) -> bool:
        chunks = self.chunker.chunk_message(
            message,
            max_chunk_size=max_chunk_size,
            force_chunking=True,
        )
        log_fn = logger.info if len(chunks) >= 32 else logger.debug
        log_fn(
            "Sending %s message in %s chunks (message_id=%s)",
            message.header.message_type.value,
            len(chunks),
            message.header.message_id,
        )

        if chunks:
            chunk_id = chunks[0].chunk_info.chunk_id if chunks[0].chunk_info else None
            if chunk_id:
                self._ack_waiters.setdefault(chunk_id, {})

        for window_start in range(0, len(chunks), self._ack_window_size):
            window = chunks[window_start:window_start + self._ack_window_size]
            futures: Dict[int, asyncio.Future] = {}
            retries_by_index: Dict[int, int] = {}
            sent_started_at: Dict[int, float] = {}
            recorded_acks: set[int] = set()
            buffer_wait_iterations = 0
            peak_buffered_amount = self._current_buffered_amount()

            for chunk in window:
                chunk_info = chunk.chunk_info
                if not chunk_info:
                    logger.error("Chunk missing chunk_info")
                    return False

                waiter_map = self._ack_waiters.setdefault(chunk_info.chunk_id, {})
                fut = asyncio.get_event_loop().create_future()
                waiter_map[chunk_info.chunk_index] = fut
                futures[chunk_info.chunk_index] = fut
                retries_by_index[chunk_info.chunk_index] = 0

            while True:
                try:
                    for chunk in window:
                        chunk_info = chunk.chunk_info
                        if not chunk_info:
                            continue
                        fut = futures[chunk_info.chunk_index]
                        if not fut.done() or chunk_info.chunk_index in recorded_acks:
                            continue
                        fut.result()
                        recorded_acks.add(chunk_info.chunk_index)
                        started_at = sent_started_at.get(chunk_info.chunk_index)
                        if started_at is not None:
                            self._record_rtt_sample(time.monotonic() - started_at)
                        logger.debug(
                            f"✓ Received ACK for chunk {chunk_info.chunk_index}/{chunk_info.total_chunks} "
                            f"(chunk_id={chunk_info.chunk_id})"
                        )
                        if retries_by_index.get(chunk_info.chunk_index, 0) > 0:
                            self.successful_recoveries += 1

                    pending_chunks = [
                        chunk for chunk in window
                        if chunk.chunk_info and not futures[chunk.chunk_info.chunk_index].done()
                    ]
                    if not pending_chunks:
                        adaptive_delay = self._calculate_adaptive_chunk_pacing_seconds(
                            retries=max(retries_by_index.values(), default=0),
                            buffer_wait_iterations=buffer_wait_iterations,
                            peak_buffered_amount=max(peak_buffered_amount, self._current_buffered_amount()),
                        )
                        if adaptive_delay > 0:
                            await asyncio.sleep(adaptive_delay)
                        break

                    for chunk in pending_chunks:
                        chunk_info = chunk.chunk_info
                        if not chunk_info:
                            continue
                        retries = retries_by_index.get(chunk_info.chunk_index, 0)
                        if retries > self._ack_max_retries:
                            logger.error(
                                f"❌ Failed to receive ACK for chunk {chunk_info.chunk_index} "
                                f"(chunk_id={chunk_info.chunk_id}) after {self._ack_max_retries} retries "
                                f"with {self._ack_timeout}s timeout per chunk"
                            )
                            self._ack_waiters.pop(chunk_info.chunk_id, None)
                            return False
                        if not self._ensure_transport_ready(message):
                            logger.warning(
                                f"Connection lost during chunk send "
                                f"(chunk {chunk_info.chunk_index}/{chunk_info.total_chunks}, "
                                f"chunk_id={chunk_info.chunk_id}); aborting"
                            )
                            self._ack_waiters.pop(chunk_info.chunk_id, None)
                            return False

                        wait_iterations, buffered_amount = await self._wait_for_buffer_capacity()
                        buffer_wait_iterations = max(buffer_wait_iterations, wait_iterations)
                        peak_buffered_amount = max(peak_buffered_amount, buffered_amount)
                        if not self._ensure_transport_ready(message):
                            self._ack_waiters.pop(chunk_info.chunk_id, None)
                            return False
                        if futures[chunk_info.chunk_index].done():
                            continue
                        sent_started_at[chunk_info.chunk_index] = time.monotonic()
                        logger.debug(
                            f"Sending chunk {chunk_info.chunk_index}/{chunk_info.total_chunks} "
                            f"(chunk_id={chunk_info.chunk_id}, size={chunk_info.chunk_size} bytes)"
                        )
                        self.datachannel.send(chunk.to_json())
                        await asyncio.sleep(0)

                    wait_futures = [
                        asyncio.shield(futures[chunk.chunk_info.chunk_index])
                        for chunk in pending_chunks
                        if chunk.chunk_info and not futures[chunk.chunk_info.chunk_index].done()
                    ]
                    if not wait_futures:
                        continue
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*wait_futures),
                            timeout=self._ack_timeout,
                        )
                    except asyncio.TimeoutError:
                        timed_out_chunks = [
                            chunk for chunk in window
                            if chunk.chunk_info and not futures[chunk.chunk_info.chunk_index].done()
                        ]
                        for chunk in timed_out_chunks:
                            chunk_info = chunk.chunk_info
                            if not chunk_info:
                                continue
                            retries = retries_by_index.get(chunk_info.chunk_index, 0) + 1
                            retries_by_index[chunk_info.chunk_index] = retries
                            logger.warning(
                                f"⏱ ACK timeout for chunk {chunk_info.chunk_index}/{chunk_info.total_chunks} "
                                f"(chunk_id={chunk_info.chunk_id}); retry {retries}/{self._ack_max_retries} "
                                f"(timeout was {self._ack_timeout}s)"
                            )
                            self.recovery_attempts += 1
                            if retries > self._ack_max_retries:
                                logger.error(
                                    f"❌ Failed to receive ACK for chunk {chunk_info.chunk_index} "
                                    f"(chunk_id={chunk_info.chunk_id}) after {self._ack_max_retries} retries "
                                    f"with {self._ack_timeout}s timeout per chunk"
                                )
                                self._ack_waiters.pop(chunk_info.chunk_id, None)
                                return False
                        adaptive_retry_delay = max(
                            0.05,
                            self._calculate_adaptive_chunk_pacing_seconds(
                                retries=max(retries_by_index.values(), default=0),
                                buffer_wait_iterations=buffer_wait_iterations,
                                peak_buffered_amount=max(peak_buffered_amount, self._current_buffered_amount()),
                            ),
                        )
                        await asyncio.sleep(adaptive_retry_delay)
                except DataChannelSendAbortedError as aborted_err:
                    first_chunk_info = window[0].chunk_info if window and window[0].chunk_info else None
                    if first_chunk_info:
                        logger.debug(
                            "Aborted chunk window starting at %s/%s (chunk_id=%s): %s",
                            first_chunk_info.chunk_index,
                            first_chunk_info.total_chunks,
                            first_chunk_info.chunk_id,
                            aborted_err,
                        )
                        self._ack_waiters.pop(first_chunk_info.chunk_id, None)
                    return False
                except asyncio.CancelledError:
                    first_chunk_info = window[0].chunk_info if window and window[0].chunk_info else None
                    if first_chunk_info:
                        logger.debug(
                            "Cancelled chunk window starting at %s/%s (chunk_id=%s)",
                            first_chunk_info.chunk_index,
                            first_chunk_info.total_chunks,
                            first_chunk_info.chunk_id,
                        )
                        self._ack_waiters.pop(first_chunk_info.chunk_id, None)
                    raise
                except Exception as e:
                    first_chunk_info = window[0].chunk_info if window and window[0].chunk_info else None
                    if first_chunk_info:
                        self._ack_waiters.pop(first_chunk_info.chunk_id, None)
                    logger.error(f"Error waiting for ACK window: {e}")
                    return False

        return True

    async def _send_chunked_message(self, message: DataChannelMessage) -> bool:
        """Send oversized messages with ACK-backed adaptive frame sizing."""
        for frame_size in self._adaptive_frame_size_candidates():
            if not self._ensure_transport_ready(message):
                return False
            if await self._send_chunked_message_once(message, max_chunk_size=frame_size):
                self._adaptive_frame_size = frame_size
                return True
            if frame_size > DEFAULT_MAX_CHUNK_SIZE:
                logger.warning(
                    "Chunk ACK delivery failed for %s-byte frames; stepping down",
                    frame_size,
                )
        return False

    def _remember_completed_chunk_message(self, message_id: str) -> bool:
        """Return true when a reassembled message was already dispatched."""
        now = time.monotonic()
        cutoff = now - self.runtime_settings.chunk_reassembly_timeout_seconds
        self._completed_chunk_messages = {
            key: value
            for key, value in self._completed_chunk_messages.items()
            if value >= cutoff
        }
        if message_id in self._completed_chunk_messages:
            return True
        self._completed_chunk_messages[message_id] = now
        if len(self._completed_chunk_messages) > 256:
            oldest = sorted(self._completed_chunk_messages.items(), key=lambda item: item[1])
            for key, _ in oldest[: len(oldest) - 256]:
                self._completed_chunk_messages.pop(key, None)
        return False

    async def send_message(self, message: DataChannelMessage) -> bool:
        """Send a message through the datachannel, handling chunking if needed"""
        if not self._ensure_transport_ready(message):
            return False

        try:
            json_str = message.to_json()
            if self.chunker.needs_chunking(json_str):
                async with self._chunk_send_lock:
                    if not self._ensure_transport_ready(message):
                        return False
                    return await self._send_chunked_message(message)
            else:
                await self._wait_for_buffer_capacity()
                if not self._ensure_transport_ready(message):
                    return False
                self.datachannel.send(json_str)

            return True
        except DataChannelSendAbortedError as aborted_err:
            logger.debug(
                "Aborted %s send because the datachannel became unavailable: %s",
                message.header.message_type.value,
                aborted_err,
            )
            return False
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False

    async def handle_received_message(self, raw_message: str) -> Optional[DataChannelMessage]:
        """Process received message, handling chunking and routing to appropriate handlers"""
        try:
            # Validate input
            if not raw_message or not isinstance(raw_message, str):
                error_msg = f"Invalid message format: expected non-empty string, got {type(raw_message)}" if self.role == "client" else "Received empty or invalid message"
                logger.error(error_msg)
                await self._send_error_response("Invalid message format")
                return None

            message = DataChannelMessage.from_json(raw_message)

            # Validate message structure
            if not message.header or not message.header.message_type:
                logger.error("Message missing required header or message_type")
                await self._send_error_response("Invalid message structure")
                return None

            # Handle chunk messages
            if message.header.message_type == MessageType.CHUNK:
                if message.chunk_info:
                    logger.debug(
                        f"Received chunk [{message.chunk_info.chunk_index}/{message.chunk_info.total_chunks}] "
                        f"(chunk_id={message.chunk_info.chunk_id}, size={message.chunk_info.chunk_size} bytes)"
                    )
                complete_message = self.chunker.add_chunk(message)
                # Send ACK for this chunk immediately
                try:
                    if message.chunk_info:
                        ack_msg = self._create_chunk_ack(message)
                        success = await self.send_message(ack_msg)
                        if success:
                            logger.debug(
                                f"✓ Sent CHUNK_ACK for [{message.chunk_info.chunk_index}/{message.chunk_info.total_chunks}]"
                            )
                        else:
                            logger.warning(
                                f"⚠ Failed to send CHUNK_ACK for [{message.chunk_info.chunk_index}/{message.chunk_info.total_chunks}]"
                            )
                except Exception as ack_err:
                    logger.error(f"Failed to send CHUNK_ACK: {ack_err}")
                if complete_message:
                    if self._remember_completed_chunk_message(complete_message.header.message_id):
                        logger.debug(
                            "Ignoring duplicate reassembled message %s after an ACK retry",
                            complete_message.header.message_id,
                        )
                        return complete_message
                    logger.info(
                        f"✓ Reassembled complete message: {complete_message.header.message_type.value} "
                        f"(id={complete_message.header.message_id})"
                    )
                    return await self._route_message(complete_message)
                else:
                    # Still waiting for more chunks
                    return None

            # Handle chunk ACK messages
            if message.header.message_type == MessageType.CHUNK_ACK:
                await self._handle_chunk_ack(message)
                # Also route to any registered handler for observability
                if MessageType.CHUNK_ACK in self.message_handlers:
                    try:
                        await self.message_handlers[MessageType.CHUNK_ACK](message)
                    except Exception as e:
                        logger.error(f"Handler error for {MessageType.CHUNK_ACK.value}: {e}")
                return message

            # Handle complete messages
            return await self._route_message(message)

        except ValueError as e:
            logger.error(f"JSON parsing error: {e}")
            await self._send_error_response(f"JSON parsing error: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to handle received message: {e}")
            await self._send_error_response(f"Message processing error: {e}")
            return None

    async def _route_message(self, message: DataChannelMessage) -> Optional[DataChannelMessage]:
        """Route message to appropriate handler with role-based behavior"""
        if self.relay_interceptor is not None:
            try:
                if self.relay_interceptor(message):
                    return message
            except Exception as e:
                logger.error("Peer relay interceptor failed; dropping message: %s", e)
                return message

        message_type = message.header.message_type

        # Handle built-in message types
        if message_type == MessageType.PING:
            if self.role == "client":
                logger.info(f"📥 Received ping message from server: {message.header.message_id}")
            await self._handle_ping(message)

            # Client and server observers can read policy carried by keepalive.
            if message_type in self.message_handlers:
                try:
                    await self.message_handlers[message_type](message)
                except Exception as e:
                    logger.error(f"Handler error for {message_type.value}: {e}")
            return message

        elif message_type == MessageType.PONG:
            await self._handle_pong(message)

            if message_type in self.message_handlers:
                try:
                    await self.message_handlers[message_type](message)
                except Exception as e:
                    logger.error(f"Handler error for {message_type.value}: {e}")
            return message

        # Lightweight routing for HTTP SSE streaming lifecycle
        if message_type in (MessageType.HTTP_SSE_START, MessageType.HTTP_SSE_EVENT, MessageType.HTTP_SSE_END):
            # Do not attempt chunk reassembly for SSE; events should arrive as discrete messages
            if message_type in self.message_handlers:
                try:
                    await self.message_handlers[message_type](message)
                    return message
                except Exception as e:
                    logger.error(f"Handler error for {message_type.value}: {e}")
                    await self._send_error_response(f"Handler error: {e}")
            else:
                logger.warning(f"No handler registered for SSE type: {message_type.value}")
                await self._send_error_response(f"Unsupported message type: {message_type.value}")
            return message

        # Lightweight routing for generic HTTP streaming
        if message_type in (
            MessageType.HTTP_STREAM_OPEN,
            MessageType.HTTP_STREAM_DATA,
            MessageType.HTTP_STREAM_END,
            MessageType.HTTP_STREAM_ABORT,
        ):
            # Streams are framed already; do not reassemble at this layer
            if message_type in self.message_handlers:
                try:
                    await self.message_handlers[message_type](message)
                    return message
                except Exception as e:
                    logger.error(f"Handler error for {message_type.value}: {e}")
                    await self._send_error_response(f"Handler error: {e}")
            else:
                logger.warning(f"No handler registered for HTTP stream type: {message_type.value}")
                await self._send_error_response(f"Unsupported message type: {message_type.value}")
            return message

        # Route to registered handlers
        if message_type in self.message_handlers:
            try:
                await self.message_handlers[message_type](message)
                return message
            except Exception as e:
                logger.error(f"Handler error for {message_type.value}: {e}")
                await self._send_error_response(f"Handler error: {e}")
        else:
            logger.warning(f"No handler registered for message type: {message_type.value}")
            await self._send_error_response(f"Unsupported message type: {message_type.value}")

        return message

    def _create_chunk_ack(self, chunk_message: DataChannelMessage) -> DataChannelMessage:
        """Create a CHUNK_ACK message acknowledging one received chunk."""
        ci = chunk_message.chunk_info
        return DataChannelMessage(
            header=MessageHeader(
                message_id=str(uuid.uuid4()),
                message_type=MessageType.CHUNK_ACK,
                timestamp=time.time(),
                session_id=chunk_message.header.session_id,
                user_id=chunk_message.header.user_id,
            ),
            payload={
                "chunk_id": ci.chunk_id if ci else None,
                "chunk_index": ci.chunk_index if ci else None,
                "total_chunks": ci.total_chunks if ci else None,
                "checksum": ci.checksum if ci else None,
                "original_message_id": chunk_message.payload.get("original_message_id"),
            },
        )

    async def _handle_chunk_ack(self, ack_message: DataChannelMessage):
        """Mark the corresponding chunk as acknowledged to let sender continue."""
        try:
            payload = ack_message.payload or {}
            chunk_id = payload.get("chunk_id")
            chunk_index = payload.get("chunk_index")
            total_chunks = payload.get("total_chunks")
            if chunk_index is not None:
                try:
                    chunk_index = int(chunk_index)
                except (ValueError, TypeError):
                    pass
            if not chunk_id or chunk_index is None:
                logger.warning("Received malformed CHUNK_ACK without chunk_id/chunk_index")
                return
            waiter_map = self._ack_waiters.get(chunk_id)
            if not waiter_map:
                # No waiter map; might be a late ack or we didn't expect it
                logger.debug(f"Late/unexpected CHUNK_ACK for chunk_id={chunk_id} index={chunk_index}/{total_chunks}")
                return
            fut = waiter_map.get(chunk_index)
            if not fut:
                logger.debug(
                    f"No pending ack future for chunk_id={chunk_id} index={chunk_index}/{total_chunks} "
                    f"(already acknowledged or already cleaned up)"
                )
                return
            if not fut.done():
                logger.debug(f"✓ CHUNK_ACK processed for [{chunk_index}/{total_chunks}]")
                fut.set_result(True)
            # Cleanup acknowledged future to avoid leaks
            try:
                del waiter_map[chunk_index]
                if not waiter_map:
                    del self._ack_waiters[chunk_id]
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Error handling CHUNK_ACK: {e}")

    async def _handle_ping(self, message: DataChannelMessage):
        """Handle ping message by sending pong response with role-based behavior"""
        if self.role == "client":
            # Client behavior: Enhanced validation and use own session/user ID
            if not self.session_id:
                logger.error("Cannot send pong response: session_id is not set")
                return

            logger.debug(f"Received ping from server, responding with pong using session_id: {self.session_id}")

            # Surface any ping-pong game state the server embedded in the PING.
            self._emit_ping_pong_event("ping_received", (message.payload or {}).get("game"))

            pong_message = DataChannelMessage(
                header=MessageHeader(
                    message_id=str(uuid.uuid4()),
                    message_type=MessageType.PONG,
                    timestamp=time.time(),
                    session_id=self.session_id,  # Use client's own session_id
                    user_id=f"client_{self.session_id}"  # Use client's own user_id
                ),
                payload={"ping_id": message.header.message_id, "fallback_text": "📥 Ping Received / 📤 Pong Sent"}
            )

            success = await self.send_message(pong_message)
            if success:
                logger.info(f"📤 Pong response sent successfully for ping: {message.header.message_id}")
                self._emit_ping_pong_event("pong_sent", None)
            else:
                logger.error(f"❌ Failed to send pong response for ping: {message.header.message_id}")
        else:
            # Server behavior: Standard response using incoming message's session/user ID
            payload = {
                "ping_id": message.header.message_id,
                "fallback_text": "📥 Ping Received / 📤 Pong Sent",
            }

            # Allow server-side code to inject extra metadata (e.g. game state)
            if self._pong_payload_augmenter:
                try:
                    extra = self._pong_payload_augmenter(message)
                    if isinstance(extra, dict):
                        payload.update(extra)
                except Exception as aug_exc:
                    logger.debug("pong_payload_augmenter error: %s", aug_exc)

            pong_message = DataChannelMessage(
                header=MessageHeader(
                    message_id=str(uuid.uuid4()),
                    message_type=MessageType.PONG,
                    timestamp=time.time(),
                    session_id=message.header.session_id,
                    user_id=message.header.user_id
                ),
                payload=payload
            )
            await self.send_message(pong_message)

    async def _handle_pong(self, message: DataChannelMessage):
        """Handle pong message - serves as keepalive confirmation"""
        ping_id = message.payload.get('ping_id')
        logger.debug(f"Received pong for ping: {ping_id} - connection alive")
        # Update last ping time to indicate connection is alive
        self.last_ping_time = time.time()
        # Close out the rally we opened; both ends of this measurement are our
        # own monotonic clock, so the result is a true round trip.
        self._record_round_trip(ping_id)
        self._emit_ping_pong_event("pong_received", (message.payload or {}).get("game"))
        waiter = self._pong_waiters.pop(str(ping_id or ""), None)
        if waiter is not None and not waiter.done():
            waiter.set_result(True)

    async def _send_error_response(self, error_message: str):
        """Send error response"""
        error_msg = DataChannelMessage(
            header=MessageHeader(
                message_id=str(uuid.uuid4()),
                message_type=MessageType.ERROR,
                timestamp=time.time()
            ),
            payload={"error": error_message}
        )
        await self.send_message(error_msg)

    async def send_ping(self, session_id: str = None) -> bool:
        """Send ping message - serves as keepalive"""
        payload: Dict[str, Any] = {"keepalive": True}

        # Allow server-side code to inject extra metadata (e.g. game state)
        if self._ping_payload_augmenter:
            try:
                extra = self._ping_payload_augmenter(None)
                if isinstance(extra, dict):
                    payload.update(extra)
            except Exception as aug_exc:
                logger.debug("ping_payload_augmenter error: %s", aug_exc)

        ping_id = str(uuid.uuid4())
        if self.role == "client":
            payload["ping_id"] = ping_id
            # Report the round trip we measured ourselves last rally so the
            # server can score on a real number instead of a cross-clock delta.
            if self._last_measured_rtt_ms is not None:
                payload["rtt_ms"] = self._last_measured_rtt_ms

        ping_message = DataChannelMessage(
            header=MessageHeader(
                message_id=ping_id,
                message_type=MessageType.PING,
                timestamp=time.time(),
                session_id=session_id or self.session_id,
                user_id="datachannel_manager"
            ),
            payload=payload
        )
        if self.role == "client":
            self._track_outgoing_ping(ping_id)
        sent = await self.send_message(ping_message)
        if self.role == "client":
            if sent:
                self._emit_ping_pong_event("keepalive_ping_sent", None)
            else:
                self._ping_sent_at_monotonic.pop(ping_id, None)
        return sent

    async def send_ping_and_wait(self, session_id: str = None, timeout: float = 4.0) -> bool:
        """Send one user-requested ping and confirm its matching pong arrives.

        This is intentionally separate from ``send_ping``: periodic keepalives
        should never wait on the network, while a UI-labelled connection test
        must only report success after the remote AutoYou server replies.
        """
        ping_id = str(uuid.uuid4())
        payload: Dict[str, Any] = {"keepalive": True, "connection_check": True}
        if self.role == "client":
            payload["ping_id"] = ping_id
            if self._last_measured_rtt_ms is not None:
                payload["rtt_ms"] = self._last_measured_rtt_ms

        ping_message = DataChannelMessage(
            header=MessageHeader(
                message_id=ping_id,
                message_type=MessageType.PING,
                timestamp=time.time(),
                session_id=session_id or self.session_id,
                user_id="datachannel_manager",
            ),
            payload=payload,
        )
        if self.role == "client":
            self._track_outgoing_ping(ping_id)
            self._emit_ping_pong_event("user_ping_sent", None)
        waiter = asyncio.get_running_loop().create_future()
        self._pong_waiters[ping_id] = waiter
        try:
            if not await self.send_message(ping_message):
                return False
            await asyncio.wait_for(asyncio.shield(waiter), timeout=max(0.1, float(timeout)))
            return True
        except (asyncio.TimeoutError, DataChannelSendAbortedError):
            return False
        finally:
            self._pong_waiters.pop(ping_id, None)

    async def start_periodic_tasks(self):
        """Start periodic ping task (serves as keepalive)"""
        asyncio.create_task(self._periodic_ping())

    async def _periodic_ping(self):
        """Send periodic ping messages (serves as keepalive)"""
        while self.connection_active:
            try:
                current_time = time.time()
                if current_time - self.last_ping_time >= self.ping_interval:
                    await self.send_ping(self.session_id)
                    self.last_ping_time = current_time
                await asyncio.sleep(1.0)
            except Exception as e:
                logger.error(f"Error in periodic ping: {e}")
                await asyncio.sleep(5.0)

    def disconnect(self):
        """Mark connection as inactive and cleanup"""
        self.connection_active = False
        self.datachannel = None
        self._abort_pending_ack_waiters("DataChannel disconnected")
        self._ping_sent_at_monotonic.clear()
        for waiter in self._pong_waiters.values():
            if not waiter.done():
                waiter.set_exception(DataChannelSendAbortedError("DataChannel disconnected"))
        self._pong_waiters.clear()
        # Clear any pending chunk reassemblies to prevent memory leaks
        self.chunker.pending_chunks.clear()
        self.chunker.chunk_metadata.clear()
        self.chunker._chunk_timestamps.clear()
        self.chunker._chunk_accumulated_bytes.clear()
        self.chunker._pool_accumulated_bytes = 0
        self._completed_chunk_messages.clear()
        logger.info("DataChannel disconnected and pending chunks cleared")

# Utility functions for creating common message types

def create_chat_message(message: str, session_id: str, user_id: str, context: List[Dict] = None, metadata: Dict = None) -> DataChannelMessage:
    """Create a chat message"""
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.CHAT,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id
        ),
        payload={
            "message": message,
            "context": context or [],
            "metadata": metadata or {}
        }
    )

def create_voice_call_control_message(payload: Dict[str, Any], session_id: str = None, user_id: str = None) -> DataChannelMessage:
    """Create a voice-call control message for client/server coordination."""
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.VOICE_CALL_CONTROL,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system",
        ),
        payload=payload or {},
    )

def create_room_bridge_control_message(payload: Dict[str, Any], session_id: str = None, user_id: str = None) -> DataChannelMessage:
    """Create a live-only room-bridge grant/control message."""
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.ROOM_BRIDGE_CONTROL,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system",
        ),
        payload=payload or {},
    )

def create_http_request_message(method: str, url: str, headers: Dict, body: str = "", session_id: str = None, user_id: str = None, compressed: bool = False) -> DataChannelMessage:
    """Create an HTTP request message with compression support.

    The method is an opaque HTTP token, so safe methods with request content
    such as RFC 10008 QUERY use the same envelope as POST/PUT/PATCH.

    Detects SSE strictly via the `Accept` header or explicit payload hint.
    Path-based inference is removed to align with aggregated POST side-channel
    behavior and header-only detection on both client and server.
    """
    # Compute SSE hint strictly: Accept header only
    try:
        accept_header: str = headers.get("Accept", "")
        sse_hint: bool = ("text/event-stream" in accept_header)
    except Exception:
        sse_hint = False

    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_REQUEST,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system"
        ),
        payload={
            "method": str(method or "GET").strip().upper() or "GET",
            "url": url,
            "headers": headers,
            "body": body,
            "compressed": compressed,
            # Hint for server to treat this request as SSE if client asks for it
            # Be tolerant of Accept variations and explicit SSE path usage
            "sse": sse_hint,
        }
    )

def create_http_request_cancel_message(
    request_id: str,
    reason: str = "Client canceled request",
    session_id: str = None,
    user_id: str = None,
) -> DataChannelMessage:
    """Create an HTTP request cancel message for aborting stale browser loads."""
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_REQUEST_CANCEL,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system",
        ),
        payload={
            "request_id": request_id,
            "reason": reason,
        },
    )

def create_http_response_message(
    status_code: int,
    headers: Dict,
    body: str,
    request_id: str,
    session_id: str = None,
    user_id: str = None,
    compressed: bool = False,
    raw_headers: list = None,
) -> DataChannelMessage:
    """Create an HTTP response message with compression support.

    Args:
        status_code: Upstream HTTP status code
        headers: Single-value header mapping (best-effort)
        body: Response body, possibly compressed or base64
        request_id: Correlation ID for matching the original request
        session_id: Optional session identifier
        user_id: Optional user identifier
        compressed: Whether the body is gzip+base64 compressed by server
        raw_headers: Optional list of raw header pairs preserving duplicates
            (e.g., multiple Set-Cookie headers). Names and values must be
            decoded to text (latin-1 recommended for HTTP header bytes).
    """
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_RESPONSE,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system",
        ),
        payload={
            "status_code": status_code,
            "headers": headers,
            "body": body,
            "request_id": request_id,
            "compressed": compressed,
            # Preserve duplicates like multiple Set-Cookie headers
            "raw_headers": raw_headers or [],
        },
    )

def create_http_sse_start_message(request_id: str, headers: Dict[str, Any], session_id: str = None, user_id: str = None) -> DataChannelMessage:
    """Create an HTTP SSE start message to initiate a streaming response.

    Args:
        request_id: Correlation ID of the originating HTTP request
        headers: Response headers to apply (should include Content-Type: text/event-stream)
        session_id: Optional session identifier
        user_id: Optional user identifier
    """
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_SSE_START,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system"
        ),
        payload={
            "request_id": request_id,
            "headers": headers
        }
    )

def create_http_sse_event_message(request_id: str, data: str, event: Optional[str] = None, session_id: str = None, user_id: str = None) -> DataChannelMessage:
    """Create an HTTP SSE event message for streaming data.

    Args:
        request_id: Correlation ID of the originating HTTP request
        data: Event data payload (string). Will be forwarded as `data: ...` lines
        event: Optional SSE event name
        session_id: Optional session identifier
        user_id: Optional user identifier
    """
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_SSE_EVENT,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system"
        ),
        payload={
            "request_id": request_id,
            "data": data,
            "event": event
        }
    )

def create_http_sse_end_message(request_id: str, session_id: str = None, user_id: str = None) -> DataChannelMessage:
    """Create an HTTP SSE end message to close a streaming response.

    Args:
        request_id: Correlation ID of the originating HTTP request
        session_id: Optional session identifier
        user_id: Optional user identifier
    """
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_SSE_END,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system"
        ),
        payload={
            "request_id": request_id
        }
    )

# ===== Generic HTTP streaming helpers (simulate TCP over SCTP) =====
def create_http_stream_open_message(
    request_id: str,
    status_code: int,
    headers: Dict[str, Any],
    raw_headers: list | None = None,
    session_id: str = None,
    user_id: str = None,
) -> DataChannelMessage:
    """Create an HTTP stream open message carrying response status and headers."""
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_STREAM_OPEN,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system",
        ),
        payload={
            "request_id": request_id,
            "status_code": status_code,
            "headers": headers,
            "raw_headers": raw_headers or [],
        },
    )

def create_http_stream_data_message(
    request_id: str,
    data: str,
    seq: int,
    session_id: str = None,
    user_id: str = None,
) -> DataChannelMessage:
    """Create an HTTP stream data message with monotonic sequence for ordering."""
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_STREAM_DATA,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system",
        ),
        payload={
            "request_id": request_id,
            "seq": seq,
            "data": data,
        },
    )

def calculate_safe_http_stream_data_chunk_size(
    max_message_size: int = DEFAULT_MAX_CHUNK_SIZE,
    *,
    binary: bool = False,
    session_id: str = None,
    user_id: str = None,
) -> int:
    """Return the largest HTTP stream payload that fits one SCTP message.

    Browser HTTP responses are already segmented at the HTTP stream layer. If one
    `HTTP_STREAM_DATA` envelope itself grows beyond the generic datachannel
    message limit, it gets re-chunked into many `CHUNK` messages and becomes
    ACK-bound. This helper sizes the stream payload so the envelope stays under
    the configured message ceiling and avoids that second chunking pass.

    For binary payloads the returned value is the raw byte count before the
    `base64:` wrapper is applied.
    """
    limit = max(1, int(max_message_size))
    low = 1
    high = limit
    best = 1

    while low <= high:
        candidate = (low + high) // 2
        if binary:
            encoded_size = ((candidate + 2) // 3) * 4
            payload_data = "base64:" + ("A" * encoded_size)
        else:
            payload_data = "A" * candidate

        sample_message = create_http_stream_data_message(
            request_id="0" * 36,
            data=payload_data,
            seq=1,
            session_id=session_id,
            user_id=user_id,
        )
        estimated_size = len(sample_message.to_json().encode("utf-8")) + 64
        if estimated_size <= limit:
            best = candidate
            low = candidate + 1
        else:
            high = candidate - 1

    return max(1, best)

def create_http_stream_end_message(
    request_id: str,
    session_id: str = None,
    user_id: str = None,
) -> DataChannelMessage:
    """Create an HTTP stream end message to signal stream completion."""
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_STREAM_END,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system",
        ),
        payload={
            "request_id": request_id,
        },
    )

def create_http_stream_abort_message(
    request_id: str,
    error: str,
    session_id: str = None,
    user_id: str = None,
) -> DataChannelMessage:
    """Create an HTTP stream abort message for error propagation."""
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_STREAM_ABORT,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system",
        ),
        payload={
            "request_id": request_id,
            "error": error,
        },
    )

def create_http_ws_upgrade_message(
    request_id: str,
    status: str,
    url: str = None,
    session_id: str = None,
    user_id: str = None,
    subprotocol: str = None,
) -> DataChannelMessage:
    """Create a WebSocket upgrade status message for the local proxy bridge."""
    payload: Dict[str, Any] = {
        "request_id": request_id,
        "status": status,
    }
    if url is not None:
        payload["url"] = url
    if subprotocol:
        payload["subprotocol"] = subprotocol
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_WS_UPGRADE,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system",
        ),
        payload=payload,
    )

def create_http_ws_data_message(
    request_id: str,
    data: Union[str, bytes, bytearray],
    session_id: str = None,
    user_id: str = None,
    *,
    fragment_id: str = None,
    fragment_index: int = None,
    total_fragments: int = None,
) -> DataChannelMessage:
    """Create a WebSocket data relay message preserving text vs binary framing.

    Binary messages carry `data_b64` plus a `binary` marker. A legacy
    `data: "base64:..."` shadow field is also emitted for older iOS clients that
    still decode binary frames through the text path.
    """
    if isinstance(data, str):
        payload: Dict[str, Any] = {
            "request_id": request_id,
            "opcode": "text",
            "binary": False,
            "data": data,
        }
    else:
        raw = bytes(data)
        payload = {
            "request_id": request_id,
            "opcode": "binary",
            "binary": True,
            "data_b64": base64.b64encode(raw).decode("ascii"),
            # Preserve the historical iOS text fallback until that client path
            # is updated to consume explicit binary relay payloads.
            "data": "base64:" + base64.b64encode(raw).decode("ascii"),
        }
    if fragment_id and int(total_fragments or 0) > 1:
        payload["fragment_id"] = str(fragment_id)
        payload["fragment_index"] = int(fragment_index or 0)
        payload["total_fragments"] = int(total_fragments)
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_WS_DATA,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system",
        ),
        payload=payload,
    )

def _create_http_ws_binary_fragment_message(
    request_id: str,
    data_b64: str,
    session_id: str = None,
    user_id: str = None,
    *,
    fragment_id: str,
    fragment_index: int,
    total_fragments: int,
) -> DataChannelMessage:
    payload: Dict[str, Any] = {
        "request_id": request_id,
        "opcode": "binary",
        "binary": True,
        "data_b64": data_b64,
        "fragment_id": str(fragment_id),
        "fragment_index": int(fragment_index),
        "total_fragments": int(total_fragments),
    }
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_WS_DATA,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system",
        ),
        payload=payload,
    )

def calculate_safe_http_ws_data_chunk_size(
    max_message_size: int = DEFAULT_MAX_CHUNK_SIZE,
    *,
    binary: bool = False,
    session_id: str = None,
    user_id: str = None,
    fragmented: bool = False,
) -> int:
    """Return the largest WS relay payload that fits one SCTP message."""
    limit = max(1, int(max_message_size))
    low = 1
    high = limit
    best = 1

    while low <= high:
        candidate = (low + high) // 2
        payload_data: Union[str, bytes]
        if binary and fragmented:
            sample_message = _create_http_ws_binary_fragment_message(
                request_id="0" * 36,
                data_b64="A" * candidate,
                session_id=session_id,
                user_id=user_id,
                fragment_id="0" * 36,
                fragment_index=999999,
                total_fragments=999999,
            )
        elif binary:
            payload_data = b"\x00" * candidate
            sample_message = create_http_ws_data_message(
                request_id="0" * 36,
                data=payload_data,
                session_id=session_id,
                user_id=user_id,
                fragment_id=("0" * 36) if fragmented else None,
                fragment_index=999999 if fragmented else None,
                total_fragments=999999 if fragmented else None,
            )
        else:
            payload_data = "A" * candidate
            sample_message = create_http_ws_data_message(
                request_id="0" * 36,
                data=payload_data,
                session_id=session_id,
                user_id=user_id,
                fragment_id=("0" * 36) if fragmented else None,
                fragment_index=999999 if fragmented else None,
                total_fragments=999999 if fragmented else None,
            )
        estimated_size = (
            len(sample_message.to_json().encode("utf-8"))
            + HTTP_WS_FRAGMENT_SIZE_SAFETY_MARGIN_BYTES
        )
        if estimated_size <= limit:
            best = candidate
            low = candidate + 1
        else:
            high = candidate - 1

    return max(1, best)

def create_http_ws_data_messages(
    request_id: str,
    data: Union[str, bytes, bytearray],
    session_id: str = None,
    user_id: str = None,
    *,
    max_message_size: int = DEFAULT_MAX_CHUNK_SIZE,
) -> List[DataChannelMessage]:
    """Create one or more WS relay messages sized to avoid CHUNK/ACK fallback."""
    limit = max(1, int(max_message_size))
    single_message = create_http_ws_data_message(
        request_id=request_id,
        data=data,
        session_id=session_id,
        user_id=user_id,
    )
    if (
        len(single_message.to_json().encode("utf-8"))
        + HTTP_WS_FRAGMENT_SIZE_SAFETY_MARGIN_BYTES
        <= limit
    ):
        return [single_message]

    is_binary = not isinstance(data, str)
    fragment_id = str(uuid.uuid4())
    fragment_budget = calculate_safe_http_ws_data_chunk_size(
        max_message_size=limit,
        binary=is_binary,
        session_id=session_id,
        user_id=user_id,
        fragmented=True,
    )

    if is_binary:
        encoded_data = base64.b64encode(bytes(data)).decode("ascii")
        parts: List[str] = []
        cursor = 0
        total_length = len(encoded_data)
        while cursor < total_length:
            remaining = total_length - cursor
            low = 1
            high = min(fragment_budget, remaining)
            best = 1

            while low <= high:
                candidate = (low + high) // 2
                fragment_piece = encoded_data[cursor:cursor + candidate]
                sample_message = _create_http_ws_binary_fragment_message(
                    request_id=request_id,
                    data_b64=fragment_piece,
                    session_id=session_id,
                    user_id=user_id,
                    fragment_id=fragment_id,
                    fragment_index=999999,
                    total_fragments=999999,
                )
                estimated_size = (
                    len(sample_message.to_json().encode("utf-8"))
                    + HTTP_WS_FRAGMENT_SIZE_SAFETY_MARGIN_BYTES
                )
                if estimated_size <= limit:
                    best = candidate
                    low = candidate + 1
                else:
                    high = candidate - 1

            parts.append(encoded_data[cursor:cursor + best])
            cursor += best

        total_fragments = len(parts)
        return [
            _create_http_ws_binary_fragment_message(
                request_id=request_id,
                data_b64=part,
                session_id=session_id,
                user_id=user_id,
                fragment_id=fragment_id,
                fragment_index=index,
                total_fragments=total_fragments,
            )
            for index, part in enumerate(parts)
        ]

    text_data = str(data)
    parts: List[str] = []
    cursor = 0
    total_length = len(text_data)
    while cursor < total_length:
        remaining = total_length - cursor
        low = 1
        high = min(fragment_budget, remaining)
        best = 1

        while low <= high:
            candidate = (low + high) // 2
            fragment_piece = text_data[cursor:cursor + candidate]
            sample_message = create_http_ws_data_message(
                request_id=request_id,
                data=fragment_piece,
                session_id=session_id,
                user_id=user_id,
                fragment_id=fragment_id,
                fragment_index=999999,
                total_fragments=999999,
            )
            estimated_size = (
                len(sample_message.to_json().encode("utf-8"))
                + HTTP_WS_FRAGMENT_SIZE_SAFETY_MARGIN_BYTES
            )
            if estimated_size <= limit:
                best = candidate
                low = candidate + 1
            else:
                high = candidate - 1

        parts.append(text_data[cursor:cursor + best])
        cursor += best

    total_fragments = len(parts)
    return [
        create_http_ws_data_message(
            request_id=request_id,
            data=part,
            session_id=session_id,
            user_id=user_id,
            fragment_id=fragment_id,
            fragment_index=index,
            total_fragments=total_fragments,
        )
        for index, part in enumerate(parts)
    ]

def create_http_ws_close_message(
    request_id: str,
    code: int = 1000,
    reason: str = "Closed",
    session_id: str = None,
    user_id: str = None,
) -> DataChannelMessage:
    """Create a WebSocket close relay message."""
    return DataChannelMessage(
        header=MessageHeader(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HTTP_WS_CLOSE,
            timestamp=time.time(),
            session_id=session_id,
            user_id=user_id or "system",
        ),
        payload={
            "request_id": request_id,
            "code": int(code),
            "reason": reason,
        },
    )

def decode_http_ws_data_payload(payload: Dict[str, Any]) -> tuple[Union[str, bytes], bool]:
    """Decode a relayed WebSocket payload into text or binary data.

    Returns:
        A tuple of `(data, is_binary)`, where data is either `str` or `bytes`.

    Compatibility:
        - Preferred format: `binary=true` with `data_b64`
        - Legacy iOS binary format: `data="base64:..."`
        - Text format: `data="<utf8 text>"`
    """
    opcode = str(payload.get("opcode") or "").lower()
    is_binary = bool(payload.get("binary")) or opcode == "binary" or bool(payload.get("data_b64"))

    if is_binary:
        data_b64 = payload.get("data_b64")
        if isinstance(data_b64, str) and data_b64:
            try:
                return base64.b64decode(data_b64.encode("ascii")), True
            except Exception as exc:
                raise ValueError("Invalid HTTP_WS_DATA base64 payload") from exc

        legacy_data = payload.get("data")
        if isinstance(legacy_data, str) and legacy_data.startswith("base64:"):
            try:
                return base64.b64decode(legacy_data[7:].encode("ascii")), True
            except Exception as exc:
                raise ValueError("Invalid legacy HTTP_WS_DATA base64 payload") from exc

        if isinstance(legacy_data, (bytes, bytearray)):
            return bytes(legacy_data), True
        if isinstance(legacy_data, str):
            return legacy_data.encode("utf-8"), True
        return b"", True

    data = payload.get("data", "")
    if isinstance(data, str):
        return data, False
    if isinstance(data, (bytes, bytearray)):
        return bytes(data).decode("utf-8", errors="replace"), False
    return str(data), False
