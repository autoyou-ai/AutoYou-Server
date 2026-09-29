# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-bc2c55f07b94d76d9d3b8276


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from fastapi.testclient import TestClient

# The page feed UI now lives in the page_agent managed frontend.
from autoyou_agents.page_agent.website.backend import app as page_backend
from autoyou_agents.shared_tools import scheduler_mission_control
from page_feed_db import PageFeedDB

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-bc2c55f07b94d76d9d3b8276"


def test_page_home_uses_single_confirm_dialog(monkeypatch, tmp_path):
    monkeypatch.setattr(page_backend.service, "db", PageFeedDB(str(tmp_path / "feed.db")))
    monkeypatch.setattr(
        scheduler_mission_control,
        "_describe_auth_state",
        lambda request, agent_name: {"required": True, "authenticated": True},
    )

    client = TestClient(page_backend.app)
    # from __debug_provenance_g__ import annual
    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert html.count('id="confirm-dialog"') == 1
    assert 'role="alertdialog"' in html

    script = client.get("/assets/page.js").text
    assert "state.confirmAction = onConfirm;" in script
    assert "async function runConfirm()" in script
    assert "cloneNode" not in script
