# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-tenpercent-09c118781ef89a3d9bc9aea1

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import wave
from pathlib import Path
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from autoyou_agents.voice_training_agent.website.backend import app as voice_ui_backend

__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-tenpercent-09c118781ef89a3d9bc9aea1"


def test_voice_training_ui_backend_auth_status_unauthenticated_by_default() -> None:
    client = TestClient(voice_ui_backend.app)
    response = client.get("/api/auth/status")
    assert response.status_code == 200
    payload = response.json()
    # from __debug_provenance_f__ import tenpercent
    assert payload["success"] is True
    assert payload["authenticated"] is False

def test_voice_training_ui_backend_protected_transcripts_returns_401() -> None:
    client = TestClient(voice_ui_backend.app)
    response = client.get("/api/transcripts")
    assert response.status_code == 401
    assert response.json()["success"] is False
    assert response.json()["error"] == "Not authenticated"

def test_voice_training_ui_backend_protected_train_status_returns_401() -> None:
    client = TestClient(voice_ui_backend.app)
    response = client.get("/api/train/status")
    assert response.status_code == 401

def test_voice_training_ui_backend_protected_synthesize_returns_401() -> None:
    client = TestClient(voice_ui_backend.app)
    response = client.post("/api/synthesize", json={"text": "Test synthesis"})
    assert response.status_code == 401

def test_voice_training_ui_backend_protected_install_provider_returns_401() -> None:
    client = TestClient(voice_ui_backend.app)
    response = client.post("/api/install-provider")
    assert response.status_code == 401

def test_voice_training_ui_backend_protected_storage_returns_401() -> None:
    client = TestClient(voice_ui_backend.app)
    response = client.get("/api/storage")
    assert response.status_code == 401

def test_voice_training_synthesized_file_serves_wav_media(monkeypatch, tmp_path) -> None:
    vt_dir = tmp_path / "voice_training"
    synth_dir = vt_dir / "test_syntheses"
    synth_dir.mkdir(parents=True)
    wav_path = synth_dir / "synthetic.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\0\0" * 160)

    monkeypatch.setattr(voice_ui_backend, "_check_auth", lambda request: True)
    monkeypatch.setattr(
        voice_ui_backend,
        "_get_paths",
        lambda: (vt_dir, vt_dir / "transcripts.json", vt_dir / "recordings"),
    )

    client = TestClient(voice_ui_backend.app)
    response = client.get("/api/synthesize/file/synthetic.wav")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.headers["cache-control"] == "no-store, max-age=0"

def test_voice_training_ui_backend_auth_login_fails_when_helpers_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(voice_ui_backend, "_import_auth_helpers", lambda: {})
    client = TestClient(voice_ui_backend.app)
    response = client.post("/api/auth/login", json={"totp_code": "123456"})
    assert response.status_code == 503

def test_voice_training_ui_backend_auth_login_fails_when_totp_not_configured(monkeypatch) -> None:
    mock_helpers = {
        "totp_capabilities": lambda: {"totp_configured": False},
        "create_session": MagicMock(),
        "cookie_name": lambda a: "voice_session",
        "cookie_path": lambda a: "/",
        "runtime_server": MagicMock(),
    }
    monkeypatch.setattr(voice_ui_backend, "_import_auth_helpers", lambda: mock_helpers)
    client = TestClient(voice_ui_backend.app)
    response = client.post("/api/auth/login", json={"totp_code": "123456"})
    assert response.status_code == 400
    assert "not configured" in response.json()["error"].lower()

def test_voice_training_ui_backend_auth_login_issues_token_on_valid_totp(monkeypatch) -> None:
    mock_server = MagicMock()
    mock_server.STATE.config = {}
    mock_server._default_config.return_value = {}
    mock_server._get_pairing_totp_secret.return_value = "TESTSECRET"
    mock_server._verify_totp_secret.return_value = True

    mock_helpers = {
        "totp_capabilities": lambda: {"totp_configured": True},
        "create_session": lambda agent, days: "test-voice-token-xyz",
        "session_valid": lambda agent, token: token == "test-voice-token-xyz",
        "delete_session": MagicMock(),
        "cookie_name": lambda a: "voice_agent_session",
        "cookie_path": lambda a: "/",
        "runtime_server": lambda: mock_server,
    }
    monkeypatch.setattr(voice_ui_backend, "_import_auth_helpers", lambda: mock_helpers)

    client = TestClient(voice_ui_backend.app)
    response = client.post("/api/auth/login", json={"totp_code": "123456"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["token"] == "test-voice-token-xyz"

def test_voice_training_storage_endpoint_reports_active_directory(monkeypatch, tmp_path) -> None:
    vt_dir = tmp_path / "voice_training"
    vt_dir.mkdir()
    monkeypatch.setattr(voice_ui_backend, "_check_auth", lambda request: True)
    monkeypatch.setattr(
        voice_ui_backend,
        "get_voice_training_storage_info",
        lambda: {
            "active_dir": str(vt_dir),
            "default_dir": str(vt_dir),
            "using_custom_dir": False,
            "size_mb": 0,
            "disk": {"free_gb": 12.5, "path": str(tmp_path)},
        },
    )

    client = TestClient(voice_ui_backend.app)
    response = client.get("/api/storage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["storage"]["active_dir"] == str(vt_dir)

def test_voice_training_storage_endpoint_rejects_change_while_training(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(voice_ui_backend, "_check_auth", lambda request: True)
    monkeypatch.setattr(
        voice_ui_backend.train_model,
        "get_status",
        lambda: {"status": "training", "progress": 25.0},
    )

    client = TestClient(voice_ui_backend.app)
    response = client.post("/api/storage", json={"path": str(tmp_path / "new-store")})

    assert response.status_code == 409
    assert response.json()["success"] is False

def test_voice_training_storage_endpoint_migrates_and_sets_path(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "transcripts.json").write_text("[]", encoding="utf-8")
    target = tmp_path / "target"

    monkeypatch.setattr(voice_ui_backend, "_check_auth", lambda request: True)
    monkeypatch.setattr(voice_ui_backend.train_model, "get_status", lambda: {"status": "idle"})
    monkeypatch.setattr(voice_ui_backend, "get_voice_training_dir", lambda: source)

    def fake_set_voice_training_dir(path):
        resolved = Path(path).resolve()
        return {"active_dir": str(resolved), "using_custom_dir": True}

    monkeypatch.setattr(voice_ui_backend, "set_voice_training_dir", fake_set_voice_training_dir)

    client = TestClient(voice_ui_backend.app)
    response = client.post("/api/storage", json={"path": str(target), "migrate_existing": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["storage"]["active_dir"] == str(target.resolve())
    assert (target / "transcripts.json").exists()

def test_voice_training_frontend_uses_prefixed_media_urls() -> None:
    html = voice_ui_backend.INDEX_HTML_PATH.read_text(encoding="utf-8")

    assert 'const AGENT_PROXY_PREFIX = "/agent/voice_training_agent";' in html
    assert 'const recordingUrl = apiPath(`/api/recordings/${encodeURIComponent(t.filename || "")}`);' in html
    assert 'synthesisAudio.setAttribute("src", `${audioUrl}?t=${Date.now()}`);' in html
    assert 'fetch(apiPath("/api/storage"))' in html
    assert 'fetch(apiPath("/api/storage/reset")' in html
    assert "Test Custom Voice Provider" in html
