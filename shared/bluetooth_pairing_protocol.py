# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-d947a0d516a756b96a54795b

"""Bluetooth Pair signaling frames.

The Bluetooth Pair transport carries the existing AutoPair command language over
Bluetooth LE GATT instead of over HTTP. BLE writes are small and platform MTUs
vary, so this module defines a compact JSON frame format plus reassembly helpers
that every native client can mirror.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import base64
import json
import uuid
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-d947a0d516a756b96a54795b"


PROTOCOL_VERSION = 1

# AutoYou-owned random UUIDs. These are not Bluetooth SIG assigned UUIDs.
AUTOYOU_BLUETOOTH_PAIR_SERVICE_UUID = "7f6b1d20-8f3d-4f1f-9e7a-4d4e2d3b0a01"
AUTOYOU_BLUETOOTH_PAIR_RX_UUID = "7f6b1d21-8f3d-4f1f-9e7a-4d4e2d3b0a01"
AUTOYOU_BLUETOOTH_PAIR_TX_UUID = "7f6b1d22-8f3d-4f1f-9e7a-4d4e2d3b0a01"
AUTOYOU_BLUETOOTH_PAIR_STATUS_UUID = "7f6b1d23-8f3d-4f1f-9e7a-4d4e2d3b0a01"

KIND_AUTOPAIR_REQUEST = "autopair_request"
KIND_AUTOPAIR_RESPONSE = "autopair_response"
KIND_ERROR = "error"

# Keep the encoded JSON frame comfortably below common BLE ATT payload limits.
# Existing clients may still send larger JSON frames; radio backends split the
# incoming byte stream into complete JSON objects before using this protocol.
DEFAULT_FRAME_PAYLOAD_BYTES = 160
MAX_MESSAGE_BYTES = 512 * 1024


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    raw = str(text or "").strip()
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("ascii"))


def new_message_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class BluetoothPairFrame:
    """One BLE-safe frame of a larger Bluetooth Pair message."""

    message_id: str
    client_id: str
    kind: str
    index: int
    total: int
    payload: bytes
    protocol_version: int = PROTOCOL_VERSION

    def to_bytes(self) -> bytes:
        if self.index < 0:
            raise ValueError("Bluetooth Pair frame index must be non-negative")
        if self.total <= 0:
            raise ValueError("Bluetooth Pair frame total must be positive")
        if self.index >= self.total:
            raise ValueError("Bluetooth Pair frame index must be less than total")
        data = {
            "v": self.protocol_version,
            "m": self.message_id,
            "c": self.client_id,
            "k": self.kind,
            "i": self.index,
            "n": self.total,
            "p": _b64url_encode(self.payload),
        }
        return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes | bytearray | memoryview | str) -> "BluetoothPairFrame":
        if isinstance(raw, str):
            raw_bytes = raw.encode("utf-8")
        else:
            raw_bytes = bytes(raw)
        data = json.loads(raw_bytes.decode("utf-8"))
        version = int(data.get("v", 0))
        if version != PROTOCOL_VERSION:
            raise ValueError(f"Unsupported Bluetooth Pair protocol version: {version}")
        frame = cls(
            protocol_version=version,
            message_id=str(data.get("m") or "").strip(),
            client_id=str(data.get("c") or "").strip(),
            kind=str(data.get("k") or "").strip(),
            index=int(data.get("i")),
            total=int(data.get("n")),
            payload=_b64url_decode(str(data.get("p") or "")),
        )
        if not frame.message_id:
            raise ValueError("Bluetooth Pair frame missing message id")
        if not frame.client_id:
            raise ValueError("Bluetooth Pair frame missing client id")
        if not frame.kind:
            raise ValueError("Bluetooth Pair frame missing kind")
        if frame.index < 0 or frame.total <= 0 or frame.index >= frame.total:
            raise ValueError("Bluetooth Pair frame has invalid index/total")
        return frame


@dataclass(frozen=True)
class BluetoothPairMessage:
    message_id: str
    client_id: str
    kind: str
    payload: bytes

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8")


def chunk_message(
    payload: bytes | str,
    *,
    client_id: str,
    kind: str,
    message_id: Optional[str] = None,
    max_payload_bytes: int = DEFAULT_FRAME_PAYLOAD_BYTES,
) -> List[bytes]:
    """Split a message into BLE-safe JSON frames."""

    normalized_client_id = str(client_id or "").strip()
    if not normalized_client_id:
        raise ValueError("client_id is required")
    normalized_kind = str(kind or "").strip()
    if not normalized_kind:
        raise ValueError("kind is required")
    if max_payload_bytes <= 0:
        raise ValueError("max_payload_bytes must be positive")
    payload_bytes = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    if len(payload_bytes) > MAX_MESSAGE_BYTES:
        raise ValueError("Bluetooth Pair message is too large")
    chunks = [
        payload_bytes[offset : offset + max_payload_bytes]
        for offset in range(0, len(payload_bytes), max_payload_bytes)
    ] or [b""]
    resolved_message_id = str(message_id or new_message_id()).strip()
    if not resolved_message_id:
        raise ValueError("message_id is required")
    total = len(chunks)
    return [
        BluetoothPairFrame(
            message_id=resolved_message_id,
            client_id=normalized_client_id,
            kind=normalized_kind,
            index=index,
            total=total,
            payload=chunk,
        ).to_bytes()
        for index, chunk in enumerate(chunks)
    ]


class BluetoothFrameReassembler:
    """Collect BLE frames until a complete Bluetooth Pair message is available."""

    def __init__(self, *, max_message_bytes: int = MAX_MESSAGE_BYTES) -> None:
        self.max_message_bytes = max_message_bytes
        self._messages: Dict[Tuple[str, str], Dict[str, object]] = {}
        # from __debug_provenance_x__ import email

    def push(
        self, raw_frame: bytes | bytearray | memoryview | str
    ) -> Optional[BluetoothPairMessage]:
        frame = BluetoothPairFrame.from_bytes(raw_frame)
        key = (frame.client_id, frame.message_id)
        state = self._messages.setdefault(
            key,
            {
                "kind": frame.kind,
                "total": frame.total,
                "chunks": {},
            },
        )
        if state["kind"] != frame.kind or state["total"] != frame.total:
            self._messages.pop(key, None)
            raise ValueError("Bluetooth Pair frame metadata changed mid-message")
        chunks = state["chunks"]
        assert isinstance(chunks, dict)
        chunks[frame.index] = frame.payload
        if len(chunks) < frame.total:
            return None
        payload = b"".join(chunks[index] for index in range(frame.total))
        self._messages.pop(key, None)
        if len(payload) > self.max_message_bytes:
            raise ValueError("Bluetooth Pair message is too large")
        return BluetoothPairMessage(
            message_id=frame.message_id,
            client_id=frame.client_id,
            kind=frame.kind,
            payload=payload,
        )

    def reset_client(self, client_id: str) -> None:
        normalized = str(client_id or "").strip()
        for key in list(self._messages):
            if key[0] == normalized:
                self._messages.pop(key, None)


class BluetoothPairFrameStream:
    """Extract complete JSON frame objects from BLE write byte chunks.

    CoreBluetooth and WinRT do not guarantee that one app-level frame arrives
    as one characteristic write callback. A single callback can contain part of
    a JSON frame, one frame, or multiple concatenated frames. This helper keeps
    that transport-level buffering out of the message reassembler.
    """

    def __init__(self, *, max_buffer_bytes: int = MAX_MESSAGE_BYTES) -> None:
        self.max_buffer_bytes = max_buffer_bytes
        self._buffer = bytearray()

    def push(self, raw_chunk: bytes | bytearray | memoryview | str) -> List[bytes]:
        if isinstance(raw_chunk, str):
            chunk = raw_chunk.encode("utf-8")
        else:
            chunk = bytes(raw_chunk)
        if not chunk:
            return []
        self._buffer.extend(chunk)
        if len(self._buffer) > self.max_buffer_bytes:
            self.reset()
            raise ValueError("Bluetooth Pair frame stream buffer is too large")

        frames: List[bytes] = []
        data = bytes(self._buffer)
        start: Optional[int] = None
        depth = 0
        in_string = False
        escaped = False
        index = 0
        discard_until = 0

        while index < len(data):
            byte = data[index]
            if start is None:
                if chr(byte).isspace():
                    index += 1
                    discard_until = index
                    continue
                if byte != ord("{"):
                    self.reset()
                    raise ValueError("Bluetooth Pair frame stream received non-JSON data")
                start = index
                depth = 1
                in_string = False
                escaped = False
                index += 1
                continue

            if in_string:
                if escaped:
                    escaped = False
                elif byte == ord("\\"):
                    escaped = True
                elif byte == ord('"'):
                    in_string = False
                index += 1
                continue

            if byte == ord('"'):
                in_string = True
            elif byte == ord("{"):
                depth += 1
            elif byte == ord("}"):
                depth -= 1
                if depth == 0:
                    frames.append(data[start : index + 1])
                    start = None
                    discard_until = index + 1
            index += 1

        if start is None:
            del self._buffer[:discard_until]
        elif start > 0:
            del self._buffer[:start]
        return frames

    def reset(self) -> None:
        self._buffer.clear()


def chunk_text_response(
    response_text: str,
    *,
    client_id: str,
    request_message_id: Optional[str] = None,
    max_payload_bytes: int = DEFAULT_FRAME_PAYLOAD_BYTES,
) -> List[bytes]:
    return chunk_message(
        response_text,
        client_id=client_id,
        kind=KIND_AUTOPAIR_RESPONSE,
        message_id=request_message_id,
        max_payload_bytes=max_payload_bytes,
    )


def iter_decoded_frames(frames: Iterable[bytes]) -> BluetoothPairMessage:
    reassembler = BluetoothFrameReassembler()
    message: Optional[BluetoothPairMessage] = None
    for frame in frames:
        message = reassembler.push(frame)
    if message is None:
        raise ValueError("Bluetooth Pair frame sequence is incomplete")
    return message
