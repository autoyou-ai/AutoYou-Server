# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Who may change Assistant mode from the admin page.

Every admin may read the settings and use the wake word (test, transcribe).
Only an admin on this computer may change them: settings, recordings,
training. The decision is the server's ``_request_is_from_this_computer``, the
same rule as the Permissions screen; here it is a switch on the fake server.
Storage goes to ``tmp_path``.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

from routers import assistant_mode as routes  # noqa: E402
from shared import assistant_mode as am  # noqa: E402
from shared import wake_word as ww  # noqa: E402


class FakeServer:
    def __init__(self):
        self.logged_in = True
        self.local = True
        self.WEBRTC = SimpleNamespace(assistant_mode=None)

    def _require_api_login(self, request):
        if self.logged_in:
            return None
        return JSONResponse(status_code=401, content={"success": False, "error": "Authentication required"})

    def _request_is_from_this_computer(self, request):
        return self.local

    def _json_response_no_store(self, payload):
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    def get_configured_server_name(self):
        return "Jarvis"

    def _build_webrtc_capabilities(self):
        return {"assistant_mode": {"available": True}}


@pytest.fixture
def admin(tmp_path, monkeypatch):
    monkeypatch.setenv(am.ASSISTANT_MODE_DIR_ENV, str(tmp_path / "assistant_mode"))
    monkeypatch.setattr(am, "voice_training_clips", lambda limit=200: [])
    monkeypatch.setattr(am, "local_transcriber", lambda *a, **k: (lambda pcm: "hey jarvis what time is it"))
    server = FakeServer()
    admin_app = FastAPI()
    routes.register_routes(admin_app, FastAPI(), server)
    return SimpleNamespace(server=server, client=TestClient(admin_app))


def _wav(seconds=1.0):
    t = np.arange(int(seconds * ww.SAMPLE_RATE)) / ww.SAMPLE_RATE
    pcm = ww.float_to_pcm16(0.3 * np.sin(2 * np.pi * 220 * t))
    return base64.b64encode(ww.pcm16_to_wav_bytes(pcm)).decode()


CHANGES = [
    ("POST", "/api/assistant-mode/settings", {"enabled": True}),
    ("POST", "/api/assistant-mode/samples", {"audio_base64": _wav(0.8)}),
    ("DELETE", "/api/assistant-mode/samples", None),
    ("DELETE", "/api/assistant-mode/samples/abcdef0123456789", None),
    ("POST", "/api/assistant-mode/train", {}),
    ("DELETE", "/api/assistant-mode/model", None),
]


def test_a_local_admin_can_change_assistant_mode_and_is_told_so(admin):
    status = admin.client.get("/api/assistant-mode").json()
    assert status["editable"] is True and status["enabled"] is False
    assert status["allow_call_toggle"] is True and status["allow_device_teaching"] is True
    saved = admin.client.post("/api/assistant-mode/settings", json={
        "enabled": True, "wake_phrase": "Hey Jarvis", "allow_call_toggle": False,
    }).json()
    assert saved["editable"] is True
    assert saved["enabled"] is True and saved["wake_phrase"] == "Hey Jarvis"
    assert am.load_settings()["allow_call_toggle"] is False


@pytest.mark.parametrize("method,path,body", CHANGES)
def test_an_admin_elsewhere_cannot_change_anything(admin, method, path, body):
    admin.server.local = False
    before = am.load_settings()
    response = admin.client.request(method, path, json=body)
    assert response.status_code == 403
    assert response.json()["local_admin_required"] is True
    assert "this computer" in response.json()["error"]
    assert am.load_settings() == before
    assert am.list_samples() == []


def test_an_admin_elsewhere_can_still_read_and_use_the_wake_word(admin):
    am.save_settings({"enabled": True, "wake_phrase": "Hey Jarvis", "confirm": "text"})
    admin.server.local = False
    status = admin.client.get("/api/assistant-mode")
    assert status.status_code == 200 and status.json()["editable"] is False
    used = admin.client.post("/api/assistant-mode/transcribe", json={"audio_base64": _wav(1.0)}).json()
    assert used["matched"] is True and used["command"] == "what time is it"
    tested = admin.client.post("/api/assistant-mode/test", json={"audio_base64": _wav(1.0)}).json()
    assert tested["would_wake"] is True


def test_signed_out_requests_are_refused_before_the_local_check(admin):
    admin.server.logged_in = False
    assert admin.client.get("/api/assistant-mode").status_code == 401
    assert admin.client.post("/api/assistant-mode/settings", json={"enabled": True}).status_code == 401
