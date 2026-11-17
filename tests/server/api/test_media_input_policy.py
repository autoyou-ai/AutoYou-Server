# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Images and audio reach the model inline only when it can use them.

Review 2026-11-05: every image (up to 10 MB, full size) and voice clip (up to
25 MB) went to the model as base64 whatever it supported; LiteLLM's Ollama
path drops audio, and a text-only model cannot read images. No Ollama is
touched: capabilities are stand-ins.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import asyncio
import base64
import io
import wave

import pytest
from PIL import Image

import rest_api
from shared.ollama_media import prepare_ollama_attachments


def _policy():
    return asyncio.run(rest_api._media_input_policy())


def test_cloud_providers_keep_every_attachment(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "google")
    assert _policy() == {"images": True, "audio": True, "max_image_side": None, "model": ""}


def test_local_models_get_images_only_with_vision_and_never_audio(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setattr(
        rest_api, "_native_ollama_settings", lambda: {"api_base": "http://127.0.0.1:11434", "model": "synthetic-text:8b"}
    )

    monkeypatch.setattr(rest_api, "_cached_ollama_capabilities", lambda base, model: {"available": True, "supports_vision": False})
    assert _policy() == {"images": False, "audio": False, "max_image_side": 1536, "model": "synthetic-text:8b"}

    monkeypatch.setattr(rest_api, "_cached_ollama_capabilities", lambda base, model: {"available": True, "supports_vision": True})
    assert _policy()["images"] is True

    # An Ollama that does not report capabilities keeps sending images, as before.
    monkeypatch.setattr(rest_api, "_cached_ollama_capabilities", lambda base, model: {"available": False})
    assert _policy()["images"] is True


def test_capabilities_are_looked_up_once_per_model(monkeypatch):
    calls = []

    def inspect(base, model, timeout_seconds=3.0):
        calls.append(model)
        return {"available": True, "supports_vision": True}

    monkeypatch.setattr(rest_api, "inspect_ollama_model_capabilities", inspect)
    rest_api._media_capability_cache.clear()

    rest_api._cached_ollama_capabilities("http://127.0.0.1:11434", "synthetic-vision:4b")
    rest_api._cached_ollama_capabilities("http://127.0.0.1:11434", "synthetic-vision:4b")

    assert calls == ["synthetic-vision:4b"]
    rest_api._media_capability_cache.clear()


def _png(width, height, mode="RGB"):
    buffer = io.BytesIO()
    Image.new(mode, (width, height)).save(buffer, format="PNG")
    return buffer.getvalue()


def _attachment(filename, mimetype, data):
    return {"filename": filename, "mimetype": mimetype, "data": base64.b64encode(data).decode("ascii")}


def test_large_images_are_shrunk_before_they_go_to_a_local_model():
    attachments = [
        _attachment("large.png", "image/png", _png(3000, 1500)),
        _attachment("transparent.png", "image/png", _png(3000, 1500, "RGBA")),
        _attachment("small.png", "image/png", _png(800, 600)),
        _attachment("invalid.png", "image/png", b"synthetic invalid image"),
    ]
    prepared = prepare_ollama_attachments(attachments, vision=True)

    assert prepared.forwarded == 3
    assert [mime for mime, _ in prepared.media] == ["image/jpeg", "image/png", "image/png"]
    with Image.open(io.BytesIO(base64.b64decode(prepared.media[0][1]))) as image:
        assert image.size == (1536, 768)
    assert prepared.media[2][1] == attachments[2]["data"]


def test_video_is_represented_by_bounded_image_samples():
    av = pytest.importorskip("av")
    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=2)
        stream.width, stream.height = 48, 32
        stream.gop_size = 1
        for index in range(8):
            frame = av.VideoFrame.from_image(Image.new("RGB", (48, 32), color=(index * 25, 0, 0)))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    prepared = prepare_ollama_attachments(
        [_attachment("synthetic.mp4", "video/mp4", buffer.getvalue())],
        vision=True,
    )

    assert prepared.forwarded == 1
    assert 2 <= len(prepared.media) <= 3
    assert all(mime == "image/jpeg" for mime, _ in prepared.media)
    assert "sampled frames" in prepared.notes[0]


def test_capability_and_file_limits_keep_unavailable_media_out(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the AutoYou attachment directories", encoding="utf-8")
    attachments = [
        _attachment("synthetic.png", "image/png", _png(8, 8)),
        _attachment("oversized.txt", "text/plain", b"x" * 9000),
        {"filename": "outside.txt", "mimetype": "text/plain", "path": str(outside)},
        _attachment("synthetic.txt", "text/plain", b"synthetic file value"),
    ]

    prepared = prepare_ollama_attachments(attachments, vision=False, text_files=True)

    assert prepared.forwarded == 1
    assert prepared.media == []
    assert prepared.notes == ["Text attachment synthetic.txt (data):\nsynthetic file value"]


@pytest.mark.asyncio
async def test_native_ollama_sends_supported_media_on_the_current_turn(monkeypatch):
    from shared import ollama_gateway

    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 1600)
    attachments = [
        _attachment("synthetic.png", "image/png", _png(16, 16)),
        _attachment("synthetic.wav", "audio/wav", audio.getvalue()),
        _attachment("synthetic.txt", "text/plain", b"sample file body"),
    ]
    monkeypatch.setattr(rest_api, "_NATIVE_OLLAMA_HISTORY", ollama_gateway.OllamaConversationStore())
    monkeypatch.setattr(rest_api, "_native_ollama_settings", lambda: {
        "api_base": "http://127.0.0.1:9", "model": "gemma4:e4b",
        "behavior": {"num_ctx": 4096}, "presets": {},
    })

    async def capabilities(*args):
        return {"available": True, "supports_vision": True, "supports_audio": True}

    requests = []

    async def generate(**kwargs):
        requests.append(kwargs["messages"])
        return {"model": "gemma4:e4b", "response": "Synthetic reply"}

    monkeypatch.setattr(rest_api, "inspect_ollama_capabilities", capabilities)
    monkeypatch.setattr(ollama_gateway, "call_ollama_gateway", generate)
    first = await rest_api.send_message_to_native_ollama(
        "Describe the attachments.", "synthetic-session", attachments=attachments
    )
    await rest_api.send_message_to_native_ollama("Follow up.", "synthetic-session")

    assert first["attachments_forwarded"] == 3
    assert first["attachments_skipped"] == 0
    assert requests[0][-1]["images"] == [attachments[0]["data"], attachments[1]["data"]]
    assert "sample file body" in requests[0][-1]["content"]
    assert requests[1][1] == {"role": "user", "content": "Describe the attachments."}
