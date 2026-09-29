# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-1bff25fe89c0f7c172ab5eb4


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
from pathlib import Path

from fastapi.testclient import TestClient

import autoyou_agents.files_agent.website.backend.app as files_app

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-1bff25fe89c0f7c172ab5eb4"


def _client(monkeypatch, workspace: Path) -> TestClient:
    monkeypatch.setenv(files_app.WORKSPACE_ROOT_ENV, str(workspace))
    monkeypatch.setattr(files_app, "_api_auth_error", lambda _agent_name, _request: None)
    return TestClient(files_app.app)


def test_files_agent_lists_default_workspace(monkeypatch, tmp_path):
    (tmp_path / "Folder").mkdir()
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")

    response = _client(monkeypatch, tmp_path).get("/api/list")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["path"] == files_app._path_text(tmp_path.resolve())
    assert [entry["name"] for entry in body["entries"]] == ["Folder", "note.txt"]
    assert body["entries"][1]["preview_kind"] == "text"


def test_files_agent_uses_compiled_workspace_env(monkeypatch, tmp_path):
    monkeypatch.delenv(files_app.WORKSPACE_ROOT_ENV, raising=False)
    monkeypatch.setenv(files_app.AUTOYOU_WORKSPACE_ROOT_ENV, str(tmp_path))
    monkeypatch.setattr(files_app, "_api_auth_error", lambda _agent_name, _request: None)

    response = TestClient(files_app.app).get("/api/locations")

    assert response.status_code == 200
    assert response.json()["default_path"] == files_app._path_text(tmp_path.resolve())


def test_files_agent_previews_text_file(monkeypatch, tmp_path):
    sample = tmp_path / "note.txt"
    sample.write_bytes(b"hello\nworld\n")

    response = _client(monkeypatch, tmp_path).get("/api/preview", params={"path": str(sample)})
    # from __debug_provenance_k__ import donations

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["preview_kind"] == "text"
    assert body["text"] == "hello\nworld\n"
