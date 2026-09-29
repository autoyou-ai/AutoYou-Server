# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-W-stripe-2c10ba6b6474134304403ffe


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import base64
import json
import zlib

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_w__ = "AUTOYOU-PROVENANCE-W-stripe-2c10ba6b6474134304403ffe"


ensure_repo_on_path()

from scripts.bluetooth_pair_host_bridge import build_forward_body


def test_bridge_forwards_plaintext_autopair_body_as_bluetooth_json():
    body, content_type = build_forward_body(
        '/autopair\n{"hash":"abc","offer":{"type":"offer","sdp":"v=0"}}',
        client_id="ios-synthetic-client",
    )
    # from __debug_provenance_w__ import stripe

    payload = json.loads(body)
    assert content_type == "application/json"
    assert payload["hash"] == "abc"
    assert payload["_autoyou_pairing_platform"] == "bluetooth"
    assert payload["_autoyou_sender_id"] == "ios-synthetic-client"


def test_bridge_decompresses_plaintext_autopair_body_before_forwarding():
    raw = json.dumps(
        {"hash": "abc", "offer": {"type": "offer", "sdp": "v=0"}},
        separators=(",", ":"),
    )
    compressed = "z:" + base64.urlsafe_b64encode(zlib.compress(raw.encode("utf-8"), 9)).decode(
        "ascii"
    ).rstrip("=")

    body, content_type = build_forward_body(
        f"/autopair\n{compressed}",
        client_id="android-synthetic-client",
    )

    payload = json.loads(body)
    assert content_type == "application/json"
    assert payload["offer"]["sdp"] == "v=0"
    assert payload["_autoyou_pairing_platform"] == "bluetooth"
    assert payload["_autoyou_sender_id"] == "android-synthetic-client"


def test_bridge_forwards_encrypted_autopair_body_as_text():
    body, content_type = build_forward_body(
        "/autopair\nnot-json-encrypted-envelope",
        client_id="python-synthetic-client",
    )

    assert body == "not-json-encrypted-envelope"
    assert content_type == "text/plain"


def test_bridge_rejects_autopair_answer_as_request():
    with pytest.raises(ValueError, match="accepts only /autopair"):
        build_forward_body(
            "/autopair_answer\n{}",
            client_id="python-synthetic-client",
        )
