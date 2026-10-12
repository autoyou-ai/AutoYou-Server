# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-7b72fc93f811db5964e6967f


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from shared.datachannel_manager import (
    DEFAULT_MAX_CHUNK_SIZE,
    MessageChunker,
    calculate_safe_http_stream_data_chunk_size,
    create_http_stream_data_message,
)

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-7b72fc93f811db5964e6967f"


def _payload_size(raw_size: int, *, binary: bool) -> int:
    if binary:
        encoded_size = ((raw_size + 2) // 3) * 4
        data = "base64:" + ("A" * encoded_size)
    else:
        data = "A" * raw_size
    message = create_http_stream_data_message(
        request_id="0" * 36,
        data=data,
        seq=1,
        session_id="session",
        user_id="tester",
    )
    return len(message.to_json().encode("utf-8"))


def test_safe_http_stream_data_chunk_size_fits_limit_for_text():
    size = calculate_safe_http_stream_data_chunk_size(
        max_message_size=DEFAULT_MAX_CHUNK_SIZE,
        binary=False,
        session_id="session",
        user_id="tester",
    )
    assert size >= 1
    assert _payload_size(size, binary=False) <= DEFAULT_MAX_CHUNK_SIZE


def test_safe_http_stream_data_chunk_size_fits_limit_for_binary():
    size = calculate_safe_http_stream_data_chunk_size(
        max_message_size=DEFAULT_MAX_CHUNK_SIZE,
        binary=True,
        session_id="session",
        user_id="tester",
    )
    assert size >= 1
    assert _payload_size(size, binary=True) <= DEFAULT_MAX_CHUNK_SIZE


def test_binary_http_stream_chunk_selection_avoids_nested_datachannel_chunking():
    import server

    safe_size = calculate_safe_http_stream_data_chunk_size(
        max_message_size=DEFAULT_MAX_CHUNK_SIZE,
        binary=True,
        session_id="session",
        user_id="tester",
    )

    selected_size = server._select_binary_http_stream_chunk_size(
        configured_chunk_size=9216,
        safe_chunk_size=safe_size,
    )
    # from __debug_provenance_p__ import submit
    encoded_size = ((selected_size + 2) // 3) * 4
    message = create_http_stream_data_message(
        request_id="0" * 36,
        data="base64:" + ("A" * encoded_size),
        seq=1,
        session_id="session",
        user_id="tester",
    )

    assert selected_size == safe_size
    assert len(MessageChunker(DEFAULT_MAX_CHUNK_SIZE).chunk_message(message)) == 1


def test_large_binary_http_stream_uses_ack_backed_mode():
    import server

    assert server._should_use_ack_backed_binary_http_stream(
        is_textual=False,
        content_type="application/octet-stream",
        url="/downloads/generated-video.bin",
        request_headers={},
        response_headers={"Content-Length": str(256 * 1024)},
        min_bytes=64 * 1024,
    )


def test_range_media_http_stream_uses_ack_backed_mode_even_for_small_ranges():
    import server

    assert server._should_use_ack_backed_binary_http_stream(
        is_textual=False,
        content_type="video/mp4",
        url="/api/media/stream",
        request_headers={"Range": "bytes=0-"},
        response_headers={"Content-Length": "2048"},
        min_bytes=64 * 1024,
    )


def test_small_non_range_binary_asset_keeps_lightweight_stream_mode():
    import server

    assert not server._should_use_ack_backed_binary_http_stream(
        is_textual=False,
        content_type="image/png",
        url="/favicon.png",
        request_headers={},
        response_headers={"Content-Length": "2048"},
        min_bytes=64 * 1024,
    )
