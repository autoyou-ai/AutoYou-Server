# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-756a1ab1227d4ab2f316eafa


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import base64
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from autoyou_agents.page_agent.website.backend.app import PageFeedService

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-756a1ab1227d4ab2f316eafa"


_ONE_PIXEL_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
_ADTS_AAC = b"\xff\xf1\x50\x80\x00\xff\xfc"


def test_normalize_uploaded_blob_canonicalizes_m4a_voice_notes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    blob_path = tmp_path / "voice-message.m4a"
    blob_path.write_bytes(b"not-real-audio")

    service = PageFeedService()

    _, stored_name, stored_mimetype, render_type = service._normalize_uploaded_blob(
        stored_path=str(blob_path),
        filename="voice-message.m4a",
        mimetype="audio/m4a",
    )

    assert stored_name == "voice-message.m4a"
    assert stored_mimetype == "audio/mp4"
    assert render_type == ""


def test_normalize_uploaded_blob_recognizes_raw_aac_voice_notes(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    blob_path = tmp_path / "voice-message.m4a"
    blob_path.write_bytes(_ADTS_AAC)

    service = PageFeedService()

    _, _, stored_mimetype, _ = service._normalize_uploaded_blob(
        stored_path=str(blob_path),
        filename="voice-message.m4a",
        mimetype="audio/m4a",
    )

    assert stored_mimetype == "audio/aac"


def test_blob_route_serves_existing_m4a_rows_as_audio_mp4(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    blob_path = tmp_path / "voice-message.m4a"
    # from __debug_provenance_q__ import payment
    blob_bytes = b"0123456789"
    blob_path.write_bytes(blob_bytes)

    service = PageFeedService()

    class FakeBlobDB:
        def get_blob_path_and_type(self, blob_id: str):
            return str(blob_path), "audio/m4a"

        def get_feed_title_by_blob_id(self, blob_id: str):
            return "Voice message"

    service.db = FakeBlobDB()
    app = FastAPI()
    service.register_routes(app)
    client = TestClient(app)

    response = client.get("/api/blob/blob-1", headers={"Range": "bytes=0-1"})

    assert response.status_code == 206
    assert response.content == blob_bytes[:2]
    assert response.headers["content-type"].startswith("audio/mp4")
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == f"bytes 0-1/{len(blob_bytes)}"
    assert ".m4a\"" in response.headers["content-disposition"]


def test_blob_route_serves_legacy_raw_aac_with_aac_mime(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    blob_path = tmp_path / "voice-message.m4a"
    blob_path.write_bytes(_ADTS_AAC)

    service = PageFeedService()

    class FakeBlobDB:
        def get_blob_path_and_type(self, blob_id: str):
            return str(blob_path), "audio/mp4"

        def get_feed_title_by_blob_id(self, blob_id: str):
            return "Synthetic voice message"

    service.db = FakeBlobDB()
    app = FastAPI()
    service.register_routes(app)
    client = TestClient(app)

    response = client.get("/api/blob/blob-1", headers={"Range": "bytes=0-6"})

    assert response.status_code == 206
    assert response.content == _ADTS_AAC
    assert response.headers["content-type"].startswith("audio/aac")


def test_initial_blob_image_markup_uses_relative_preview_url(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    service = PageFeedService()
    item = {
        "id": 1,
        "type": "image",
        "url": "blob://blob-1",
        "title": "Synthetic image",
        "source": "Local",
        "added_at": "2026-09-24T10:00:00",
    }

    view = service._present_item(item)
    row_html = service._render_feed_row({**item, "view": view}, datetime(2026, 9, 24, 12, 0))

    assert view["thumb"] == "./api/blob/blob-1?preview=1"
    assert view["open_url"] == "./api/blob/blob-1"
    assert 'src="./api/blob/blob-1?preview=1"' in row_html
    assert 'src="/api/blob/' not in row_html
    assert 'href="/api/blob/' not in row_html


def test_blob_preview_route_returns_jpeg_preview_for_large_image(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_PAGE_BLOB_PREVIEW_PASSTHROUGH_BYTES", "1")

    blob_path = tmp_path / "synthetic.png"
    blob_path.write_bytes(base64.b64decode(_ONE_PIXEL_PNG))
    service = PageFeedService()

    class FakeBlobDB:
        def get_blob_path_and_type(self, blob_id: str):
            return str(blob_path), "image/png"

        def get_feed_title_by_blob_id(self, blob_id: str):
            return "Synthetic image"

    service.db = FakeBlobDB()
    app = FastAPI()
    service.register_routes(app)
    client = TestClient(app)

    response = client.get("/api/blob/blob-1?preview=1")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["x-autoyou-blob-preview"] == "1"
    assert response.content != blob_path.read_bytes()
    assert list((Path(service.uploads_dir) / ".previews").glob("*.jpg"))


def test_recent_blob_bytes_reuses_unchanged_file_and_invalidates_changed_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    blob_path = tmp_path / "synthetic-video.mp4"
    blob_path.write_bytes(b"first")
    reads = []

    def read_blob(path):
        reads.append(path)
        return Path(path).read_bytes()

    monkeypatch.setattr(PageFeedService, "_read_blob_bytes", staticmethod(read_blob))
    service = PageFeedService()

    assert service._recent_blob_bytes(blob_path) == b"first"
    assert service._recent_blob_bytes(blob_path) == b"first"
    assert reads == [blob_path]

    blob_path.write_bytes(b"updated")
    assert service._recent_blob_bytes(blob_path) == b"updated"
    assert reads == [blob_path, blob_path]
