# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-d9924bae2f2d591431c30e52


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-6c406175746f796f752e6d65-d9924bae2f2d591431c30e52"

from shared.datachannel_manager import (
    create_http_ws_data_message,
    decode_http_ws_data_payload,
)


def test_ws_text_payload_round_trip():
    message = create_http_ws_data_message("req-1", "hello world", session_id="s1")

    assert message.payload["request_id"] == "req-1"
    assert message.payload["binary"] is False
    assert message.payload["opcode"] == "text"

    decoded, is_binary = decode_http_ws_data_payload(message.payload)
    assert is_binary is False
    assert decoded == "hello world"


def test_ws_binary_payload_round_trip():
    raw = b"\x00\x01\x02binary\xff"
    message = create_http_ws_data_message("req-2", raw, session_id="s2")

    assert message.payload["request_id"] == "req-2"
    assert message.payload["binary"] is True
    assert message.payload["opcode"] == "binary"
    assert "data_b64" in message.payload

    decoded, is_binary = decode_http_ws_data_payload(message.payload)
    assert is_binary is True
    assert decoded == raw


def test_ws_legacy_ios_binary_payload_decodes():
    decoded, is_binary = decode_http_ws_data_payload(
        {
            "request_id": "req-legacy",
            "data": "base64:AAECYmluYXJ5/w==",
        }
    )

    assert is_binary is False
    assert decoded == "base64:AAECYmluYXJ5/w=="

    decoded, is_binary = decode_http_ws_data_payload(
        {
            "request_id": "req-legacy-binary",
            "opcode": "binary",
            "data": "base64:AAECYmluYXJ5/w==",
        }
    )

    assert is_binary is True
    assert decoded == b"\x00\x01\x02binary\xff"
