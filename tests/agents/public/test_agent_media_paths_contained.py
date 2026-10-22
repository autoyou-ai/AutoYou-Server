"""Media and export routes that take a client file name stay inside their folder."""

from __future__ import annotations

import pytest
from fastapi.responses import HTMLResponse

import autoyou_agents.media_generation_agent.website.backend.app as media_app
import autoyou_agents.voice_training_agent.website.backend.app as voice_app
from autoyou_agents.fine_tuning_agent import fine_tuning_tool

ESCAPES = ["../outside.wav", "../../outside.wav", "..", "/etc/hosts"]


@pytest.fixture
def voice_dirs(tmp_path, monkeypatch):
    vt_dir = tmp_path / "voice_training"
    recordings = vt_dir / "recordings"
    (vt_dir / "test_syntheses").mkdir(parents=True)
    recordings.mkdir()
    (tmp_path / "outside.wav").write_bytes(b"RIFF synthetic")
    (vt_dir / "outside.wav").write_bytes(b"RIFF synthetic")
    monkeypatch.setattr(voice_app, "_check_auth", lambda request: True)
    monkeypatch.setattr(voice_app, "_get_paths", lambda: (vt_dir, vt_dir / "transcripts.json", recordings))
    return vt_dir


@pytest.mark.parametrize("name", ESCAPES)
def test_voice_recordings_route_refuses_names_outside_its_folder(voice_dirs, name: str) -> None:
    for route in (voice_app.serve_recording, voice_app.serve_synthesized_file):
        response = route(name, request=None)
        assert isinstance(response, HTMLResponse) and response.status_code == 404


@pytest.fixture
def media_dir(tmp_path, monkeypatch):
    root = tmp_path / "media"
    (root / "images").mkdir(parents=True)
    (root / "images" / "made.png").write_bytes(b"synthetic")
    (root / "older.png").write_bytes(b"synthetic")
    (tmp_path / "outside.wav").write_bytes(b"synthetic")
    monkeypatch.setattr(media_app, "_check_auth", lambda request: True)
    monkeypatch.setattr(media_app, "MEDIA_DIR", root)
    return root


def test_media_route_serves_its_own_files(media_dir) -> None:
    assert media_app.serve_media_file("image", "made.png", request=None).status_code == 200
    assert media_app.serve_media_file("image", "older.png", request=None).status_code == 200


@pytest.mark.parametrize("name", ESCAPES)
def test_media_route_refuses_names_outside_its_folder(media_dir, name: str) -> None:
    assert media_app.serve_media_file("image", name, request=None).status_code == 404


def test_datacollector_export_id_cannot_leave_the_exports_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTOYOU_DATA_COLLECTOR_ROOT", str(tmp_path / "collector"))
    outside = tmp_path / "manifest.json"
    outside.write_text("{}", encoding="utf-8")
    result = fine_tuning_tool.create_dataset_from_datacollector_export(export_id="../../../../../..")
    assert result == {"status": "error", "message": "export_id is not a valid DataCollector export id."}
