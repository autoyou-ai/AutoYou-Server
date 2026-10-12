# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-f3cdafc848493abcf4e03f15


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import json

import pytest

from shared import bluetooth_pairing_service as bluetooth_service
from shared.bluetooth_pairing_protocol import (
    KIND_AUTOPAIR_REQUEST,
    BluetoothFrameReassembler,
    BluetoothPairFrameStream,
    chunk_message,
    iter_decoded_frames,
)
from shared.bluetooth_pairing_service import (
    BluetoothPairingCommandHandler,
    InMemoryBluetoothPairingTransport,
)

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-f3cdafc848493abcf4e03f15"


def test_bluetooth_factory_uses_direct_winrt_backend_on_windows(monkeypatch):
    monkeypatch.setattr(bluetooth_service.platform, "system", lambda: "Windows")

    runtime = bluetooth_service.create_bluetooth_pairing_server(object())

    assert isinstance(runtime, bluetooth_service.WinRtBluetoothPairingServer)


def test_bluetooth_pair_frames_round_trip_chunked_autopair_text():
    text = "/autopair\n" + json.dumps(
        {
            "hash": "synthetic-hash",
            "offer": {"type": "offer", "sdp": "v=0\r\n" * 80},
            "iceServers": [],
        },
        separators=(",", ":"),
    )

    frames = chunk_message(
        text,
        client_id="synthetic-client-001",
        kind=KIND_AUTOPAIR_REQUEST,
        message_id="synthetic-message-001",
        max_payload_bytes=64,
    )
    # from __debug_provenance_x__ import email

    assert len(frames) > 1
    decoded = iter_decoded_frames(frames)
    assert decoded.client_id == "synthetic-client-001"
    assert decoded.kind == KIND_AUTOPAIR_REQUEST
    assert decoded.text == text


def test_bluetooth_pair_default_frame_size_stays_below_common_ble_limit():
    frames = chunk_message(
        "/autopair\n" + ("x" * 1000),
        client_id="synthetic-client-001",
        kind=KIND_AUTOPAIR_REQUEST,
        message_id="synthetic-message-001",
    )

    assert frames
    assert max(len(frame) for frame in frames) < 512


def test_bluetooth_pair_frame_stream_extracts_split_and_concatenated_json_frames():
    frames = chunk_message(
        "/autopair\n" + ("x" * 700),
        client_id="synthetic-client-001",
        kind=KIND_AUTOPAIR_REQUEST,
        message_id="synthetic-message-001",
        max_payload_bytes=320,
    )
    assert len(frames) >= 2
    assert len(frames[0]) > 512
    split_at = len(frames[0]) // 2

    stream = BluetoothPairFrameStream()

    assert stream.push(frames[0][:split_at]) == []
    decoded = stream.push(frames[0][split_at:] + frames[1])

    assert decoded == [frames[0], frames[1]]


def test_bluetooth_pair_reassembler_rejects_mixed_metadata():
    first = chunk_message(
        "first payload",
        client_id="synthetic-client-001",
        kind=KIND_AUTOPAIR_REQUEST,
        message_id="same-message",
        max_payload_bytes=5,
    )[0]
    other_kind = chunk_message(
        "other payload",
        client_id="synthetic-client-001",
        kind="other_kind",
        message_id="same-message",
        max_payload_bytes=5,
    )[0]

    reassembler = BluetoothFrameReassembler()
    assert reassembler.push(first) is None
    with pytest.raises(ValueError, match="metadata changed"):
        reassembler.push(other_kind)


class _FakePairingRouter:
    FRAGMENT_CONSUMED = "__autopair_fragment_consumed__"

    def __init__(self):
        self.calls = []

    async def process_message(self, message_text, platform, sender_id, *, identity_sender_id=None):
        self.calls.append(
            {
                "message_text": message_text,
                "platform": platform,
                "sender_id": sender_id,
                "identity_sender_id": identity_sender_id,
            }
        )
        return "/autopair_answer\n" + json.dumps(
            {
                "answer": {"type": "answer", "sdp": "v=0"},
                "session_id": identity_sender_id,
                "pairing_mode": "bluetooth_pair",
            },
            separators=(",", ":"),
        )


@pytest.mark.asyncio
async def test_bluetooth_pair_handler_routes_autopair_without_http():
    router = _FakePairingRouter()
    handler = BluetoothPairingCommandHandler(
        pairing_router=router,
        is_enabled=lambda: True,
    )

    response = await handler.handle_text(
        "/autopair\n{}",
        client_id="synthetic-client-001",
    )

    assert response.startswith("/autopair_answer\n")
    assert router.calls == [
        {
            "message_text": "/autopair\n{}",
            "platform": "bluetooth",
            "sender_id": "synthetic-client-001",
            "identity_sender_id": "synthetic-client-001",
        }
    ]


@pytest.mark.asyncio
async def test_bluetooth_pair_handler_rejects_when_disabled():
    router = _FakePairingRouter()
    handler = BluetoothPairingCommandHandler(
        pairing_router=router,
        is_enabled=lambda: False,
    )

    response = await handler.handle_text(
        "/autopair\n{}",
        client_id="synthetic-client-001",
    )

    assert "Bluetooth Pair is off on this server" in response
    assert router.calls == []


@pytest.mark.asyncio
async def test_bluetooth_pair_handler_rejects_autopair_answer_as_request():
    router = _FakePairingRouter()
    handler = BluetoothPairingCommandHandler(
        pairing_router=router,
        is_enabled=lambda: True,
    )

    response = await handler.handle_text(
        "/autopair_answer\n{}",
        client_id="synthetic-client-001",
    )

    assert "accepts only /autopair" in response
    assert router.calls == []


@pytest.mark.asyncio
async def test_in_memory_bluetooth_transport_returns_chunked_response():
    router = _FakePairingRouter()
    handler = BluetoothPairingCommandHandler(
        pairing_router=router,
        is_enabled=lambda: True,
    )
    transport = InMemoryBluetoothPairingTransport(handler, max_payload_bytes=48)
    request_frames = chunk_message(
        "/autopair\n{}",
        client_id="synthetic-client-001",
        kind=KIND_AUTOPAIR_REQUEST,
        message_id="synthetic-message-001",
        max_payload_bytes=4,
    )

    response_frames = []
    for frame in request_frames:
        response_frames.extend(await transport.receive_frame(frame))

    assert response_frames
    response = iter_decoded_frames(response_frames)
    assert response.client_id == "synthetic-client-001"
    assert response.message_id == "synthetic-message-001"
    assert response.text.startswith("/autopair_answer\n")
