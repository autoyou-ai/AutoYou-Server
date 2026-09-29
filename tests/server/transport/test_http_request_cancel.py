# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-breach-4c765766b6a3014ec89ea12a


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio
import base64
import gzip
import json
import os
import sys

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-breach-4c765766b6a3014ec89ea12a"


ensure_repo_on_path()

import server
from shared.datachannel_manager import (
    DataChannelMessage,
    MessageHeader,
    MessageType,
    create_http_request_cancel_message,
)


class _FakeHeaders(dict):
    @property
    def raw(self):
        return [
            (str(key).encode("latin-1"), str(value).encode("latin-1"))
            for key, value in self.items()
        ]


class _FakeStreamResponse:
    def __init__(self, *, body: bytes, headers: dict[str, str], status_code: int = 200):
        self.status_code = status_code
        self.headers = _FakeHeaders(headers)
        self._body = body

    async def aiter_bytes(self, chunk_size: int):
        for offset in range(0, len(self._body), max(1, chunk_size)):
            yield self._body[offset:offset + max(1, chunk_size)]


class _FakeStreamContext:
    def __init__(self, response: _FakeStreamResponse):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeHTTPClient:
    def __init__(self, response: _FakeStreamResponse):
        self.response = response
        self.method = None
        self.kwargs = None

    def stream(self, method, **kwargs):
        self.method = method
        self.kwargs = kwargs
        return _FakeStreamContext(self.response)


class _CapturingDataChannelManager:
    def __init__(self):
        self.sent_messages = []

    async def send_message(self, message):
        self.sent_messages.append(message)
        return True


def test_http_request_cancel_message_roundtrip():
    message = create_http_request_cancel_message(
        request_id="req-1",
        reason="Superseded by new browser navigation",
        session_id="session-1",
        user_id="tester",
    )

    decoded = DataChannelMessage.from_json(message.to_json())

    assert decoded.header.message_type == MessageType.HTTP_REQUEST_CANCEL
    assert decoded.payload["request_id"] == "req-1"
    assert decoded.payload["reason"] == "Superseded by new browser navigation"


def test_datachannel_http_body_decode_preserves_binary_bytes():
    body = bytes([0, 1, 10, 13, 127, 128, 255])
    encoded = base64.b64encode(body).decode("ascii")

    assert server._decode_datachannel_http_body(encoded, body_base64=True) == body
    assert server._decode_datachannel_http_body(
        base64.b64encode(gzip.compress(body)).decode("ascii"),
        compressed=True,
        body_base64=True,
    ) == body


def test_remote_forward_target_rejects_private_absolute_url():
    webrtc = server.WebRTCManager()

    with pytest.raises(server.UnsafeURLError):
        webrtc._validate_remote_forward_target("http://169.254.169.254/latest/meta-data")


def test_webrtc_drops_proxy_origin_metadata_for_cross_origin_loopback_target():
    webrtc = server.WebRTCManager()

    sanitized = webrtc._sanitize_forward_headers(
        {
            "Origin": "http://localhost:8076",
            "Referer": "http://localhost:8076/agent/remote_desktop_agent/",
            "Content-Type": "application/json",
        },
        "http://127.0.0.1:8067/agent/remote_desktop_agent/api/remote_desktop/native-keyboard",
    )

    assert "Origin" not in sanitized
    assert "Referer" not in sanitized
    assert sanitized["Content-Type"] == "application/json"

    same_origin = webrtc._sanitize_forward_headers(
        {"Origin": "http://127.0.0.1:8067"},
        "http://127.0.0.1:8067/agent/remote_desktop_agent/",
    )
    assert same_origin["Origin"] == "http://127.0.0.1:8067"


@pytest.mark.asyncio
async def test_webrtc_http_request_cancel_cancels_registered_task():
    webrtc = server.WebRTCManager()
    request_started = asyncio.Event()
    cancellation_observed = asyncio.Event()

    async def _slow_request():
        try:
            request_started.set()
            await asyncio.sleep(3600)
        finally:
            cancellation_observed.set()

    task = webrtc._track_session_task("session-1", _slow_request(), "http_request")
    webrtc._remember_http_proxy_request_task("session-1", "req-1", task)
    await asyncio.wait_for(request_started.wait(), timeout=0.2)

    cancel_message = DataChannelMessage(
        header=MessageHeader(
            message_id="cancel-1",
            message_type=MessageType.HTTP_REQUEST_CANCEL,
            timestamp=0.0,
            session_id="session-1",
            user_id="tester",
        ),
        payload={
            "request_id": "req-1",
            "reason": "Superseded by new browser navigation",
        },
    )

    await webrtc._handle_http_request_cancel(cancel_message)
    await asyncio.wait_for(cancellation_observed.wait(), timeout=0.2)

    assert task.done()
    assert task.cancelled()
    assert "session-1" not in webrtc.http_proxy_request_tasks


def _allow_forward_port(monkeypatch, webrtc, port: int) -> None:
    """Register ``port`` as this manager's forward target.

    The loopback proxy enforces a port allowlist, so a test target has to be a
    registered destination rather than an arbitrary port. This previously
    "worked" only because AUTOYOU_TEST_ROOT disabled the allowlist outright.
    """
    monkeypatch.setattr(webrtc, "_get_autoyou_forward_target_port", lambda: port)


@pytest.mark.asyncio
async def test_small_textual_http_response_uses_buffered_response(monkeypatch):
    monkeypatch.setenv("AUTOYOU_BUFFERED_HTTP_RESPONSE_MAX_BYTES", str(64 * 1024))
    webrtc = server.WebRTCManager()
    _allow_forward_port(monkeypatch, webrtc, 8095)
    datachannel_manager = _CapturingDataChannelManager()
    webrtc.datachannel_managers["session-1"] = datachannel_manager
    webrtc._http_client = _FakeHTTPClient(
        _FakeStreamResponse(
            body=b"<html><body>" + (b"tasks " * 2048) + b"</body></html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
    )

    request_message = DataChannelMessage(
        header=MessageHeader(
            message_id="request-message-1",
            message_type=MessageType.HTTP_REQUEST,
            timestamp=0.0,
            session_id="session-1",
            user_id="tester",
        ),
        payload={
            "request_id": "request-1",
            "method": "GET",
            "url": "http://127.0.0.1:8095/",
            "headers": {},
            "body": "",
        },
    )

    await webrtc._handle_http_request(request_message)

    assert [message.header.message_type for message in datachannel_manager.sent_messages] == [
        MessageType.HTTP_RESPONSE
    ]
    response_payload = datachannel_manager.sent_messages[0].payload
    assert response_payload["request_id"] == "request-1"
    assert response_payload["compressed"] is True


@pytest.mark.asyncio
async def test_binary_audio_stream_uses_incremental_datachannel_frames(monkeypatch):
    monkeypatch.setattr(server.STATE, "config", server._default_config(), raising=False)
    webrtc = server.WebRTCManager()
    datachannel_manager = _CapturingDataChannelManager()
    webrtc.datachannel_managers["session-audio"] = datachannel_manager
    webrtc._http_client = _FakeHTTPClient(
        _FakeStreamResponse(
            body=b"audio" * 800,
            status_code=206,
            headers={
                "Content-Type": "audio/mpeg",
                "Content-Range": "bytes 0-3999/4000",
                "Accept-Ranges": "bytes",
                "Content-Length": "4000",
                "Connection": "keep-alive",
                "Transfer-Encoding": "chunked",
            },
        )
    )

    request_message = DataChannelMessage(
        header=MessageHeader(
            message_id="audio-message",
            message_type=MessageType.HTTP_REQUEST,
            timestamp=0.0,
            session_id="session-audio",
            user_id="tester",
        ),
        payload={
            "request_id": "audio-request",
            "method": "GET",
            "url": "/agent/audio_agent/api/stream/synthetic-track",
            "headers": {"Accept": "*/*", "Range": "bytes=0-"},
            "body": "",
        },
    )

    await webrtc._handle_http_request(request_message)

    message_types = [message.header.message_type for message in datachannel_manager.sent_messages]
    assert message_types[0] == MessageType.HTTP_STREAM_OPEN
    assert message_types[-1] == MessageType.HTTP_STREAM_END
    assert MessageType.HTTP_STREAM_DATA in message_types
    assert MessageType.HTTP_RESPONSE not in message_types
    open_headers = datachannel_manager.sent_messages[0].payload["raw_headers"]
    assert ("Content-Length", "4000") in open_headers
    assert not any(name.lower() in {"connection", "transfer-encoding"} for name, _ in open_headers)
    assert all(
        message.payload["data"].startswith("base64:")
        for message in datachannel_manager.sent_messages
        if message.header.message_type == MessageType.HTTP_STREAM_DATA
    )


@pytest.mark.asyncio
async def test_query_request_forwards_content_over_datachannel(monkeypatch):
    cfg = server._default_config()
    cfg["autoyou_page"]["remote_access_role"] = "viewer"
    monkeypatch.setattr(server.STATE, "config", cfg, raising=False)
    webrtc = server.WebRTCManager()
    _allow_forward_port(monkeypatch, webrtc, 8095)
    datachannel_manager = _CapturingDataChannelManager()
    webrtc.datachannel_managers["session-query"] = datachannel_manager
    http_client = _FakeHTTPClient(
        _FakeStreamResponse(
            body=b'{"success":true,"frontends":[]}',
            headers={"Content-Type": "application/json"},
        )
    )
    webrtc._http_client = http_client
    query_body = b'{"query":"notes","limit":24}'
    request_message = DataChannelMessage(
        header=MessageHeader(
            message_id="request-query-message",
            message_type=MessageType.HTTP_REQUEST,
            timestamp=0.0,
            session_id="session-query",
            user_id="tester",
        ),
        payload={
            "request_id": "request-query",
            "method": "query",
            "url": "http://127.0.0.1:8095/api/agent-websites",
            "headers": {"Content-Type": "application/json"},
            "body": base64.b64encode(query_body).decode("ascii"),
            "body_base64": True,
        },
    )

    await webrtc._handle_http_request(request_message)

    assert http_client.method == "QUERY"
    assert http_client.kwargs["content"] == query_body
    assert [message.header.message_type for message in datachannel_manager.sent_messages] == [
        MessageType.HTTP_RESPONSE
    ]


@pytest.mark.asyncio
async def test_webrtc_http_proxy_blocks_editor_delete(monkeypatch):
    cfg = server._default_config()
    cfg["autoyou_page"]["remote_access_role"] = "editor"
    monkeypatch.setattr(server.STATE, "config", cfg, raising=False)

    webrtc = server.WebRTCManager()
    datachannel_manager = _CapturingDataChannelManager()
    webrtc.datachannel_managers["session-1"] = datachannel_manager

    request_message = DataChannelMessage(
        header=MessageHeader(
            message_id="request-message-1",
            message_type=MessageType.HTTP_REQUEST,
            timestamp=0.0,
            session_id="session-1",
            user_id="tester",
        ),
        payload={
            "request_id": "request-1",
            "method": "DELETE",
            "url": "/api/feed/1",
            "headers": {},
            "body": "",
        },
    )

    await webrtc._handle_http_request(request_message)

    assert len(datachannel_manager.sent_messages) == 1
    response_payload = datachannel_manager.sent_messages[0].payload
    assert response_payload["status_code"] == 403
    body = json.loads(response_payload["body"])
    assert body["remote_access_role"] == "editor"
    assert "delete actions require admin access" in body["error"]


def test_streamed_http_response_fallback_default_disabled(monkeypatch):
    monkeypatch.delenv("AUTOYOU_STREAMED_HTTP_RESPONSE_FALLBACK_MAX_BYTES", raising=False)

    assert server.WebRTCManager._get_streamed_http_response_fallback_max_bytes() == 0


def test_streamed_http_response_fallback_respects_env(monkeypatch):
    monkeypatch.setenv("AUTOYOU_STREAMED_HTTP_RESPONSE_FALLBACK_MAX_BYTES", "4096")
    assert server.WebRTCManager._get_streamed_http_response_fallback_max_bytes() == 4096

    monkeypatch.setenv("AUTOYOU_STREAMED_HTTP_RESPONSE_FALLBACK_MAX_BYTES", "-7")
    assert server.WebRTCManager._get_streamed_http_response_fallback_max_bytes() == 0

    monkeypatch.setenv("AUTOYOU_STREAMED_HTTP_RESPONSE_FALLBACK_MAX_BYTES", "not-a-number")
    assert server.WebRTCManager._get_streamed_http_response_fallback_max_bytes() == 0


def test_buffered_http_response_default_and_env(monkeypatch):
    monkeypatch.delenv("AUTOYOU_BUFFERED_HTTP_RESPONSE_MAX_BYTES", raising=False)
    assert server.WebRTCManager._get_buffered_http_response_max_bytes() == 512 * 1024

    monkeypatch.setenv("AUTOYOU_BUFFERED_HTTP_RESPONSE_MAX_BYTES", "4096")
    assert server.WebRTCManager._get_buffered_http_response_max_bytes() == 4096

    monkeypatch.setenv("AUTOYOU_BUFFERED_HTTP_RESPONSE_MAX_BYTES", "-7")
    assert server.WebRTCManager._get_buffered_http_response_max_bytes() == 0

    monkeypatch.setenv("AUTOYOU_BUFFERED_HTTP_RESPONSE_MAX_BYTES", "not-a-number")
    assert server.WebRTCManager._get_buffered_http_response_max_bytes() == 512 * 1024


def test_incremental_http_text_decoder_preserves_split_utf8_sequences():
    decoder, encoding = server._build_incremental_http_text_decoder("text/html; charset=utf-8")

    first = server._decode_incremental_http_text_chunk(
        decoder,
        b"brilliant \xe2\x99",
        encoding=encoding,
    )
    # from __debug_provenance_o__ import breach
    second = server._decode_incremental_http_text_chunk(
        decoder,
        b"\xaa playlist",
        encoding=encoding,
    )
    trailing = server._flush_incremental_http_text_decoder(decoder, encoding=encoding)

    assert first == "brilliant "
    assert second == "\u266a playlist"
    assert trailing == ""
