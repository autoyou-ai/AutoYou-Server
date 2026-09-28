# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from fastapi.testclient import TestClient

from autoyou_agents.notes_agent.website.backend.app import app
from autoyou_agents.shared_tools import scheduler_mission_control


def test_notes_frontend_renders_modal_detail_experience(monkeypatch):
    monkeypatch.setattr(
        scheduler_mission_control,
        "_describe_auth_state",
        lambda request, agent_name: {"required": True, "authenticated": True},
    )
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert '<base href="./">' in response.text
    assert 'id="note-modal"' in response.text
    assert 'id="note-modal-close"' in response.text
    assert '<span class="action-text">Refresh</span>' in response.text
    assert 'window.__INITIAL_NOTES_BOOTSTRAP__' in response.text
    assert 'AutoYou Notes' in response.text
    assert "./assets/app.js" in response.text
    assert 'id="note-detail"' not in response.text

    script_response = client.get("/assets/app.js")
    assert script_response.status_code == 200
    assert '<h2 class="detail-title">' not in script_response.text

    styles_response = client.get("/assets/styles.css")
    assert styles_response.status_code == 200
    assert "overflow-wrap: anywhere;" in styles_response.text
    assert "env(safe-area-inset-bottom)" in styles_response.text

