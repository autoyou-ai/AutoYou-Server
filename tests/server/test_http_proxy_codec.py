# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-304232623937526530302920-f4eb92aadbbf2b043998288d

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-304232623937526530302920-f4eb92aadbbf2b043998288d"


import base64
import gzip

from shared.http_proxy_codec import encode_http_proxy_response


def test_large_json_response_can_be_compressed_for_transport():
    body_text = '{"items":[' + ",".join('"agent"' for _ in range(2000)) + "]}"

    encoded = encode_http_proxy_response(
        body_text.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        text_compression_threshold_chars=1,
    )

    assert encoded.is_textual is True
    assert encoded.compressed is True
    assert gzip.decompress(base64.b64decode(encoded.body)).decode("utf-8") == body_text
    assert encoded.headers["Content-Transfer-Encoding"] == "base64"


def test_large_non_json_text_can_still_be_compressed():
    body_text = "plain text " * 2000

    encoded = encode_http_proxy_response(
        body_text.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
        text_compression_threshold_chars=1,
    )

    assert encoded.is_textual is True
    assert encoded.compressed is True
    assert encoded.body != body_text
    assert encoded.headers["Content-Transfer-Encoding"] == "base64"
