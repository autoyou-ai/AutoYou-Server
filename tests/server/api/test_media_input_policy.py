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
import io

from PIL import Image

import rest_api


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


def test_large_images_are_shrunk_before_they_go_to_a_local_model():
    data, mime = rest_api._shrink_image_for_model(_png(3000, 1500), "image/png", 1536)
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (1536, 768)
    assert mime == "image/jpeg"

    # Transparency is kept as PNG.
    _data, mime = rest_api._shrink_image_for_model(_png(3000, 1500, "RGBA"), "image/png", 1536)
    assert mime == "image/png"

    small = _png(800, 600)
    assert rest_api._shrink_image_for_model(small, "image/png", 1536) == (small, "image/png")
    assert rest_api._shrink_image_for_model(b"synthetic bytes", "image/png", 1536) == (b"synthetic bytes", "image/png")
    assert rest_api._shrink_image_for_model(small, "image/png", None) == (small, "image/png")
