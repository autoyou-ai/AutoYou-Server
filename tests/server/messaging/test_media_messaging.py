# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import base64
import json

from shared import media_messaging


def test_media_only_accepts_attachment_placeholders():
    context_attachment = {
        "filename": "synthetic-photo.jpg",
        "mimetype": "image/jpeg",
        "data": base64.b64encode(b"synthetic-image").decode("ascii"),
    }

    assert media_messaging.is_media_only_message("", [context_attachment]) is True
    assert media_messaging.is_media_only_message("(attachment)", [context_attachment]) is True
    assert media_messaging.is_media_only_message("[attachment:synthetic-photo.jpg]", [context_attachment]) is True
    assert media_messaging.is_media_only_message("Please review the attached file.", [context_attachment]) is True
    assert media_messaging.is_media_only_message("caption this image", [context_attachment]) is False


def test_inline_context_for_client_preserves_video_bytes(tmp_path):
    video_path = tmp_path / "synthetic-video.mp4"
    video_bytes = b"\x00\x00\x00\x18ftypmp42synthetic-video"
    video_path.write_bytes(video_bytes)

    context = media_messaging.inline_context_for_client(
        [
            {
                "filename": "synthetic-video.mp4",
                "mimetype": "video/mp4",
                "path": str(video_path),
                "size_bytes": len(video_bytes),
            }
        ]
    )

    attachment = context[0]["attachments"][0]
    assert context[0]["source"] == "media_reply"
    assert attachment["filename"] == "synthetic-video.mp4"
    assert attachment["mimetype"] == "video/mp4"
    assert attachment["meta"]["kind"] == "video"
    assert base64.b64decode(attachment["data"]) == video_bytes


def test_extract_media_reply_attachments_from_wrapped_agent_json_text():
    payload = {
        "message": {
            "parts": [
                {
                    "text": json.dumps(
                        {
                            "response": "Rendered the image.",
                            "media_reply_attachments": [
                                {
                                    "filename": "synthetic-render.png",
                                    "mimetype": "image/png",
                                    "url": "https://cdn.example.com/synthetic-render.png",
                                }
                            ],
                        }
                    )
                }
            ]
        }
    }

    attachments = media_messaging.extract_media_reply_attachments(payload, source="ai_agent")

    assert len(attachments) == 1
    assert attachments[0]["filename"] == "synthetic-render.png"
    assert attachments[0]["mimetype"] == "image/png"
    assert attachments[0]["url"] == "https://cdn.example.com/synthetic-render.png"
    assert attachments[0]["meta"]["source"] == "ai_agent"


def test_extract_media_reply_attachments_from_openai_and_ollama_shapes():
    payload = {
        "output": [
            {
                "type": "output_image",
                "image_url": {"url": "https://cdn.example.com/synthetic-openai.png"},
            }
        ],
        "message": {
            "images": [
                base64.b64encode(b"synthetic-ollama-image").decode("ascii"),
            ]
        },
    }

    attachments = media_messaging.extract_media_reply_attachments(payload, source="developer_backend")

    assert len(attachments) == 2
    assert attachments[0]["url"] == "https://cdn.example.com/synthetic-openai.png"
    assert attachments[0]["mimetype"] == "image/png"
    assert base64.b64decode(attachments[1]["data"]) == b"synthetic-ollama-image"
    assert attachments[1]["mimetype"] == "image/png"


def test_load_attachment_bytes_fetches_safe_remote_url(monkeypatch):
    from shared import url_safety

    media_bytes = b"\x89PNG\r\n\x1a\nsynthetic-remote"
    captured = []

    class FakeResponse:
        status_code = 200
        headers = {"Content-Length": str(len(media_bytes))}

        def iter_content(self, chunk_size):
            captured.append(chunk_size)
            yield media_bytes

    def fake_safe_follow_redirects(session, url, **kwargs):
        captured.append(url)
        return FakeResponse()

    monkeypatch.setattr(url_safety, "safe_follow_redirects", fake_safe_follow_redirects)

    data, error = media_messaging.load_attachment_bytes(
        {
            "filename": "synthetic-remote.png",
            "mimetype": "image/png",
            "url": "https://cdn.example.com/synthetic-remote.png",
        }
    )

    assert error is None
    assert data == media_bytes
    assert captured[0] == "https://cdn.example.com/synthetic-remote.png"
