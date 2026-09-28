# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-5174e5392840899c2ad01429

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-5174e5392840899c2ad01429"


from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from autoyou_agents.audio_agent.website.backend import app as audio_backend
from autoyou_agents.fine_tuning_agent.website.backend import app as fine_tuning_backend
from autoyou_agents.media_generation_agent.website.backend import app as media_backend
from autoyou_agents.voice_training_agent.website.backend import app as voice_backend


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_global_otp_off_opens_audio_settings_without_an_agent_cookie(monkeypatch) -> None:
    from autoyou_agents.shared_tools import scheduler_mission_control as security

    monkeypatch.setattr(security, "_global_agent_website_otp_disabled", lambda: True)
    assert audio_backend._check_auth(None) is True


@pytest.mark.parametrize(
    ("backend", "agent_name"),
    [
        (audio_backend, "audio_agent"),
        (fine_tuning_backend, "fine_tuning_agent"),
        (media_backend, "media_generation_agent"),
        (voice_backend, "voice_training_agent"),
    ],
)
def test_standalone_agent_login_prefers_assigned_totp_profile(monkeypatch, backend, agent_name) -> None:
    calls = []
    server = SimpleNamespace(
        STATE=SimpleNamespace(config={}),
        agent_has_assigned_2fa_profile=lambda name: name == agent_name,
        verify_agent_assigned_2fa=lambda name, code: calls.append((name, code)) or code == "654321",
    )
    helpers = {
        "totp_capabilities": lambda: {"totp_configured": False},
        "create_session": lambda agent, days: f"{agent}-session",
        "cookie_name": lambda agent: f"{agent}-cookie",
        "cookie_path": lambda agent: "/",
        "runtime_server": lambda: server,
    }
    monkeypatch.setattr(backend, "_import_auth_helpers", lambda: helpers)

    client = TestClient(backend.app)
    response = client.post("/api/auth/login", json={"totp_code": "654321"})

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert calls == [(agent_name, "654321")]


@pytest.mark.parametrize(
    "relative_path",
    [
        "autoyou_agents/fine_tuning_agent/website/frontend/index.html",
        "autoyou_agents/media_generation_agent/website/frontend/index.html",
        "autoyou_agents/voice_training_agent/website/index.html",
    ],
)
def test_standalone_agent_frontends_hide_and_inert_protected_shells(relative_path: str) -> None:
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert "inert" in source
    assert "hidden" in source
