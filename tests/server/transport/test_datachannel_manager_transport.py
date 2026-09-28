# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-6675b219f66b89d77b5dacb3


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-6675b219f66b89d77b5dacb3"

import asyncio
import time

import pytest

from shared.datachannel_manager import (
    DataChannelManager,
    DataChannelMessage,
    DataChannelRuntimeSettings,
    ChunkInfo,
    MessageHeader,
    MessageChunker,
    MessageType,
    create_chat_message,
    create_http_ws_data_message,
)


class _AckingDataChannel:
    def __init__(self, manager: DataChannelManager):
        self.manager = manager
        self.readyState = "open"
        self.bufferedAmount = 0
        self.sent_original_message_ids: list[str] = []

    def send(self, raw_message: str) -> None:
        message = DataChannelMessage.from_json(raw_message)
        if message.header.message_type != MessageType.CHUNK:
            return

        original_message_id = str(message.payload.get("original_message_id") or "")
        self.sent_original_message_ids.append(original_message_id)
        ack_message = self.manager._create_chunk_ack(message)
        loop = asyncio.get_running_loop()
        loop.call_soon(loop.create_task, self.manager._handle_chunk_ack(ack_message))


class _DisconnectingDataChannel:
    def __init__(self, manager: DataChannelManager):
        self.manager = manager
        self.readyState = "open"
        self.bufferedAmount = 0
        self.send_calls = 0

    def send(self, raw_message: str) -> None:
        self.send_calls += 1
        if self.send_calls != 1:
            return

        loop = asyncio.get_running_loop()

        def _disconnect() -> None:
            self.readyState = "closed"
            self.manager.disconnect()

        loop.call_soon(_disconnect)


class _DelayedAckingDataChannel:
    def __init__(self, manager: DataChannelManager, *, ack_delay_seconds: float):
        self.manager = manager
        self.readyState = "open"
        self.bufferedAmount = 0
        self.ack_delay_seconds = ack_delay_seconds
        self.send_calls = 0
        self.acks_processed = 0
        self.sends_before_first_ack = 0

    def send(self, raw_message: str) -> None:
        message = DataChannelMessage.from_json(raw_message)
        if message.header.message_type != MessageType.CHUNK:
            return

        self.send_calls += 1
        if self.acks_processed == 0:
            self.sends_before_first_ack += 1
        ack_message = self.manager._create_chunk_ack(message)
        loop = asyncio.get_running_loop()

        def _ack() -> None:
            self.acks_processed += 1
            loop.create_task(self.manager._handle_chunk_ack(ack_message))

        loop.call_later(self.ack_delay_seconds, _ack)


class _SizeLimitedAckingDataChannel:
    def __init__(self, manager: DataChannelManager, *, max_frame_size: int):
        self.manager = manager
        self.readyState = "open"
        self.bufferedAmount = 0
        self.max_frame_size = max_frame_size
        self.frame_sizes: list[int] = []

    def send(self, raw_message: str) -> None:
        message = DataChannelMessage.from_json(raw_message)
        if message.header.message_type != MessageType.CHUNK:
            return
        frame_size = len(raw_message.encode("utf-8"))
        self.frame_sizes.append(frame_size)
        if frame_size > self.max_frame_size:
            return
        ack_message = self.manager._create_chunk_ack(message)
        asyncio.get_running_loop().call_soon(
            asyncio.get_running_loop().create_task,
            self.manager._handle_chunk_ack(ack_message),
        )


class _PongDataChannel:
    def __init__(self, manager: DataChannelManager):
        self.manager = manager
        self.readyState = "open"
        self.bufferedAmount = 0

    def send(self, raw_message: str) -> None:
        message = DataChannelMessage.from_json(raw_message)
        if message.header.message_type != MessageType.PING:
            return
        pong = DataChannelMessage(
            header=MessageHeader(
                message_id="synthetic-pong",
                message_type=MessageType.PONG,
                timestamp=time.time(),
                session_id=message.header.session_id,
                user_id="synthetic-server",
            ),
            payload={"ping_id": message.header.message_id},
        )
        loop = asyncio.get_running_loop()
        loop.call_soon(loop.create_task, self.manager._handle_pong(pong))


@pytest.mark.asyncio
async def test_chunked_sends_are_serialized_per_datachannel():
    settings = DataChannelRuntimeSettings(
        chunk_ack_timeout_seconds=1.0,
        chunk_ack_max_retries=0,
        chunk_send_pacing_seconds=0.0,
    )
    manager = DataChannelManager(role="server", runtime_settings=settings)
    channel = _AckingDataChannel(manager)
    manager.set_datachannel(channel)

    first_message = create_chat_message(
        message="A" * 5000,
        session_id="session-serialize",
        user_id="tester",
    )
    second_message = create_chat_message(
        message="B" * 5000,
        session_id="session-serialize",
        user_id="tester",
    )

    first_task = asyncio.create_task(manager.send_message(first_message))
    await asyncio.sleep(0)
    second_task = asyncio.create_task(manager.send_message(second_message))

    assert await first_task is True
    assert await second_task is True

    original_message_ids = channel.sent_original_message_ids
    assert set(original_message_ids) == {
        first_message.header.message_id,
        second_message.header.message_id,
    }

    transitions = sum(
        1
        for index in range(1, len(original_message_ids))
        if original_message_ids[index] != original_message_ids[index - 1]
    )
    assert transitions == 1


@pytest.mark.asyncio
async def test_explicit_connection_check_requires_matching_pong():
    manager = DataChannelManager(role="client")
    manager.set_session_id("session-ping-check")
    manager.set_datachannel(_PongDataChannel(manager))

    assert await manager.send_ping_and_wait(timeout=0.25) is True
    assert manager._pong_waiters == {}


@pytest.mark.asyncio
async def test_disconnect_aborts_pending_chunk_waiters_immediately():
    settings = DataChannelRuntimeSettings(
        chunk_ack_timeout_seconds=5.0,
        chunk_ack_max_retries=5,
        chunk_send_pacing_seconds=0.0,
    )
    manager = DataChannelManager(role="server", runtime_settings=settings)
    channel = _DisconnectingDataChannel(manager)
    manager.set_datachannel(channel)

    message = create_chat_message(
        message="C" * 5000,
        session_id="session-disconnect",
        user_id="tester",
    )

    started_at = time.monotonic()
    sent = await manager.send_message(message)
    elapsed = time.monotonic() - started_at

    assert sent is False
    assert channel.send_calls == 1
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_chunk_retry_waiter_survives_timeout_until_delayed_ack_arrives():
    settings = DataChannelRuntimeSettings(
        chunk_ack_timeout_seconds=0.05,
        chunk_ack_max_retries=2,
        chunk_send_pacing_seconds=0.0,
        adaptive_max_frame_size=1024,
    )
    manager = DataChannelManager(role="server", runtime_settings=settings)
    channel = _DelayedAckingDataChannel(manager, ack_delay_seconds=0.06)
    manager.set_datachannel(channel)

    message = create_chat_message(
        message="D" * 2500,
        session_id="session-delayed-ack",
        user_id="tester",
    )

    sent = await manager.send_message(message)

    assert sent is True
    assert channel.send_calls > 1


@pytest.mark.asyncio
async def test_chunked_send_pipelines_ack_window_before_waiting():
    settings = DataChannelRuntimeSettings(
        chunk_ack_timeout_seconds=1.0,
        chunk_ack_max_retries=0,
        chunk_ack_window_size=4,
        chunk_send_pacing_seconds=0.0,
        adaptive_max_frame_size=1024,
    )
    manager = DataChannelManager(role="server", runtime_settings=settings)
    channel = _DelayedAckingDataChannel(manager, ack_delay_seconds=0.05)
    manager.set_datachannel(channel)

    message = create_chat_message(
        message="E" * 6000,
        session_id="session-windowed-ack",
        user_id="tester",
    )
    total_chunks = len(manager.chunker.chunk_message(message))

    assert total_chunks > settings.chunk_ack_window_size
    assert await manager.send_message(message) is True
    assert channel.sends_before_first_ack == settings.chunk_ack_window_size


@pytest.mark.asyncio
async def test_adaptive_frames_step_down_to_a_working_envelope_ceiling():
    settings = DataChannelRuntimeSettings(
        chunk_ack_timeout_seconds=0.01,
        chunk_ack_max_retries=0,
        adaptive_max_frame_size=8192,
    )
    manager = DataChannelManager(role="server", runtime_settings=settings)
    channel = _SizeLimitedAckingDataChannel(manager, max_frame_size=2100)
    manager.set_datachannel(channel)

    message = create_chat_message(
        message="F" * 5000,
        session_id="session-adaptive-fallback",
        user_id="tester",
    )

    assert await manager.send_message(message) is True
    assert manager._adaptive_frame_size == 2048
    assert max(channel.frame_sizes) > 2100
    assert any(size <= 2100 for size in channel.frame_sizes)


@pytest.mark.asyncio
async def test_oversized_websocket_data_uses_adaptive_ack_backed_frames():
    settings = DataChannelRuntimeSettings(
        chunk_ack_timeout_seconds=0.01,
        chunk_ack_max_retries=0,
        adaptive_max_frame_size=8192,
    )
    manager = DataChannelManager(role="server", runtime_settings=settings)
    channel = _SizeLimitedAckingDataChannel(manager, max_frame_size=2100)
    manager.set_datachannel(channel)
    manager.set_session_id("session-ws-adaptive")

    message = create_http_ws_data_message(
        request_id="request-ws-adaptive",
        data="W" * 5000,
        session_id="session-ws-adaptive",
    )

    assert await manager.send_message(message) is True
    assert manager._adaptive_frame_size == 2048
    assert max(channel.frame_sizes) <= 8192
    assert any(size <= 2100 for size in channel.frame_sizes)


def test_chunk_envelopes_fit_each_adaptive_frame_step():
    message = create_chat_message(
        message="G" * 5000,
        session_id="session-frame-ceiling",
        user_id="tester",
    )
    chunker = MessageChunker()

    for frame_size in (8192, 4096, 2048, 1024):
        chunks = chunker.chunk_message(
            message,
            max_chunk_size=frame_size,
            force_chunking=True,
        )
        assert chunks
        assert all(len(chunk.to_json().encode("utf-8")) <= frame_size for chunk in chunks)


@pytest.mark.asyncio
async def test_duplicate_reassembled_message_is_not_dispatched_twice():
    manager = DataChannelManager(role="server")
    manager.set_datachannel(_AckingDataChannel(manager))
    dispatched: list[str] = []

    async def on_chat(message: DataChannelMessage) -> None:
        dispatched.append(message.header.message_id)

    manager.register_handler(MessageType.CHAT, on_chat)
    message = create_chat_message(
        message="H" * 2500,
        session_id="session-duplicate-reassembly",
        user_id="tester",
    )
    chunks = manager.chunker.chunk_message(message, max_chunk_size=1024, force_chunking=True)

    for chunk in chunks + chunks:
        await manager.handle_received_message(chunk.to_json())

    assert dispatched == [message.header.message_id]


def test_adaptive_chunk_pacing_only_activates_under_pressure():
    settings = DataChannelRuntimeSettings(
        chunk_ack_timeout_seconds=1.0,
        chunk_ack_max_retries=2,
        chunk_send_pacing_seconds=0.0,
        buffered_amount_high_watermark_bytes=256 * 1024,
    )
    manager = DataChannelManager(role="server", runtime_settings=settings)

    assert manager._calculate_adaptive_chunk_pacing_seconds(
        retries=0,
        buffer_wait_iterations=0,
        peak_buffered_amount=settings.buffered_amount_high_watermark_bytes // 4,
    ) == 0.0

    manager._record_rtt_sample(0.2)
    delay = manager._calculate_adaptive_chunk_pacing_seconds(
        retries=1,
        buffer_wait_iterations=2,
        peak_buffered_amount=settings.buffered_amount_high_watermark_bytes,
    )

    assert delay > 0.0
    assert delay <= min(manager._ack_timeout / 2.0, 0.75)


def test_chunk_reassembly_timeout_refreshes_on_each_received_chunk(monkeypatch):
    fake_now = {"value": 1000.0}

    def _fake_time() -> float:
        return fake_now["value"]

    monkeypatch.setattr("shared.datachannel_manager.time.time", _fake_time)

    chunker = MessageChunker(max_chunk_size=256, chunk_timeout=1.0)
    message = create_chat_message(
        message="upload-payload-" * 64,
        session_id="session-upload-timeout",
        user_id="tester",
    )
    chunks = chunker.chunk_message(message)

    assert len(chunks) >= 3
    chunk_id = chunks[0].chunk_info.chunk_id

    assert chunker.add_chunk(chunks[0]) is None
    assert chunker._chunk_timestamps[chunk_id] == pytest.approx(1000.0)

    fake_now["value"] = 1000.8
    assert chunker.add_chunk(chunks[1]) is None
    assert chunker._chunk_timestamps[chunk_id] == pytest.approx(1000.8)

    fake_now["value"] = 1001.6
    assert chunker.cleanup_stale_chunks() == 0
    assert chunk_id in chunker.pending_chunks


def test_malformed_chunk_does_not_create_pending_state():
    chunker = MessageChunker(max_chunk_size=256)
    chunk_id = "malformed-synthetic"
    chunk = DataChannelMessage(
        header=MessageHeader(
            message_id="chunk-malformed",
            message_type=MessageType.CHUNK,
            timestamp=0.0,
            session_id="session-malformed",
            user_id="tester",
        ),
        payload={
            "original_message_id": "original-malformed",
            "original_message_type": MessageType.CHAT.value,
            "chunk_data_b64": "====",
        },
        chunk_info=ChunkInfo(
            chunk_id=chunk_id,
            total_chunks=1,
            chunk_index=0,
            chunk_size=1,
            total_size=1,
            checksum="0" * 64,
        ),
    )

    assert chunker.add_chunk(chunk) is None
    assert chunk_id not in chunker.pending_chunks
    assert chunk_id not in chunker.chunk_metadata


def test_chunk_reassembly_accepts_1gb_media_chunk_counts_without_old_cap():
    chunker = MessageChunker(max_chunk_size=1024)
    chunk_id = "synthetic-large-media"
    large_json_size = 1536 * 1024 * 1024
    total_chunks = 5_900_000
    chunk = DataChannelMessage(
        header=MessageHeader(
            message_id="chunk-1",
            message_type=MessageType.CHUNK,
            timestamp=0.0,
            session_id="session-large-media",
            user_id="tester",
        ),
        payload={
            "original_message_id": "original-large-media",
            "original_message_type": MessageType.CHAT.value,
            "chunk_data_b64": "QQ==",
        },
        chunk_info=ChunkInfo(
            chunk_id=chunk_id,
            total_chunks=total_chunks,
            chunk_index=0,
            chunk_size=1,
            total_size=large_json_size,
            checksum="0" * 64,
        ),
    )

    assert chunker.add_chunk(chunk) is None
    assert chunk_id in chunker.pending_chunks
    assert chunker.chunk_metadata[chunk_id].total_chunks == total_chunks
