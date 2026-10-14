# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License. See LICENSE.
import asyncio
import base64
import json

import pytest

from shared.binary_http import (BodyInbox, MAX_BODY_BYTES, REQUEST_BODY, RESPONSE_BODY,
                                decode_frame, encode_frame, payload_capacity)
from shared.datachannel_manager import DataChannelManager, DataChannelMessage, MessageHeader, MessageType


def test_binary_http_wire_contract_and_malformed_frames():
    golden = bytes.fromhex("4159423101030102030461626300ff80")
    assert decode_frame(golden) == (RESPONSE_BODY, "abc", 0x01020304, b"\x00\xff\x80")
    assert encode_frame(RESPONSE_BODY, "abc", 0x01020304, b"\x00\xff\x80") == golden
    for frame in (b"", golden[:10], b"BAD!" + golden[4:], golden[:4] + b"\x03" + golden[5:],
                  golden[:6] + b"\x00" * 4 + golden[10:], golden + bytes(16384)):
        with pytest.raises(ValueError):
            decode_frame(frame)
    with pytest.raises(ValueError):
        encode_frame(RESPONSE_BODY, "../file", 1, b"unsafe")


@pytest.mark.asyncio
async def test_body_inbox_is_bounded_and_rejects_gaps_and_overruns():
    inbox = BodyInbox()
    assert not inbox.feed("unopened", 1, b"untrusted")
    assert not inbox.pending
    future = inbox.open("synthetic", 3)
    assert inbox.feed("synthetic", 1, b"a")
    assert not inbox.feed("synthetic", 3, b"bc")
    with pytest.raises(ValueError, match="sequence"):
        await future
    inbox.discard("synthetic")
    future = inbox.open("synthetic", 2)
    assert not inbox.feed("synthetic", 1, b"abc")
    with pytest.raises(ValueError, match="length"):
        await future
    inbox.discard("synthetic")
    for index in range(4):
        inbox.open(f"synthetic-{index}", MAX_BODY_BYTES)
    with pytest.raises(ValueError, match="slots"):
        inbox.open("fifth", 1)
    inbox.clear()
    with pytest.raises(ValueError, match="limit"):
        inbox.open("too-large", MAX_BODY_BYTES + 1)


class LoopbackChannel:
    readyState = "open"
    ordered = True
    maxRetransmits = None
    maxPacketLifeTime = None
    bufferedAmount = 0

    def __init__(self, receiver):
        self.receiver = receiver
        self.frames = []
        self.tasks = []

    def send(self, frame):
        self.frames.append(frame)
        self.tasks.append(asyncio.create_task(self.receiver.handle_received_message(frame)))


@pytest.mark.asyncio
async def test_negotiated_upload_and_download_use_raw_bodies_with_legacy_fallback():
    client = DataChannelManager(role="client")
    server = DataChannelManager(role="server")
    outgoing = LoopbackChannel(server)
    incoming = LoopbackChannel(client)
    client.set_datachannel(outgoing)
    server.set_datachannel(incoming)
    client.binary_http_supported = True
    body = bytes(range(256)) * 8192
    received = asyncio.get_running_loop().create_future()

    async def on_request(message):
        if "binary_body_length" not in message.payload:
            return
        async def read():
            received.set_result(await server.receive_binary_http_body(
                message.payload["request_id"], message.payload["binary_body_length"], message.header))
        asyncio.create_task(read())

    server.register_handler(MessageType.HTTP_REQUEST, on_request)
    request = DataChannelMessage(MessageHeader("synthetic-message", MessageType.HTTP_REQUEST, 0), {
        "request_id": "synthetic-upload", "method": "PUT", "url": "/synthetic", "headers": {},
        "body": base64.b64encode(body).decode("ascii"), "body_base64": True, "binary_http": 1,
    })
    assert await client.send_message(request)
    assert await asyncio.wait_for(received, 2) == body
    raw = [frame for frame in outgoing.frames if isinstance(frame, bytes)]
    assert raw and all(len(frame) <= 16384 and decode_frame(frame)[0] == REQUEST_BODY for frame in raw)
    control = json.loads(outgoing.frames[0])["payload"]
    assert control["body"] == "" and control["binary_body_length"] == len(body)
    assert sum(map(len, raw)) < len(body) * 1.01

    delivered = []
    async def on_data(message):
        delivered.append(message.payload["data"])
    client.register_handler(MessageType.HTTP_STREAM_DATA, on_data)
    client.relay_interceptor = lambda message: (message.to_json() and False)
    async def on_open(message):
        pass
    client.register_handler(MessageType.HTTP_STREAM_OPEN, on_open)
    await client.handle_received_message(DataChannelMessage(
        MessageHeader("synthetic-open", MessageType.HTTP_STREAM_OPEN, 0),
        {"request_id": "synthetic-playback", "headers": {"X-AutoYou-Binary-HTTP": "1"}}).to_json())
    capacity = payload_capacity("synthetic-playback")
    for sequence, offset in enumerate(range(0, len(body), capacity), 1):
        assert await server.send_binary_http("synthetic-playback", sequence, body[offset:offset + capacity])
    await asyncio.gather(*incoming.tasks, *outgoing.tasks)
    assert b"".join(delivered) == body
    assert not server._binary_body_inbox.pending

    # Old servers never advertise support: ordinary small requests remain JSON.
    client.binary_http_supported = False
    outgoing.frames.clear()
    tiny = DataChannelMessage(request.header, dict(request.payload, body="dGVzdA=="))
    await client.send_message(tiny)
    assert isinstance(outgoing.frames[0], str)
    assert json.loads(outgoing.frames[0])["payload"]["body"] == "dGVzdA=="
    await asyncio.gather(*outgoing.tasks)
