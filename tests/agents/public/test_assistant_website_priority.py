# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The Assistant website under the priority rules: calls, its device, and speaking.

A page opened through a paired app is that app's device (AutoYou stamps the
WebRTC session on the forwarded request). The hub here is a stand-in that says
which session is in a call; the arbiter is the real one.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from autoyou_agents.shared_tools import scheduler_mission_control as smc
from shared import assistant_mode as am
from shared.remote_access_policy import remote_http_request_allowed

ROOT = Path(__file__).resolve().parents[3]
ADMIN_UI_JS = ROOT / "assets" / "admin-ui.js"
ORIGIN = {"Origin": "http://testserver"}
PHONE = "webrtc:synthetic-phone"


def _via(session_id):
    return {**ORIGIN, "X-AutoYou-Remote-Browser": "webrtc", "X-AutoYou-WebRTC-Session-Id": session_id}


@pytest.fixture
def site(tmp_path, monkeypatch):
    config = {"agent_ui_security": {"assistant_agent": {"auth_mode": "open"}}, "agent_websites": {}}
    monkeypatch.setenv(am.ASSISTANT_MODE_DIR_ENV, str(tmp_path / "assistant_mode"))
    monkeypatch.setattr(smc, "_AGENT_UI_SESSIONS_FILE", str(tmp_path / "agent_ui_sessions.json"))
    monkeypatch.setattr(smc, "_AGENT_UI_SESSIONS", {})
    calls = set()
    hub = SimpleNamespace(
        wake_arbiter=am.WakeArbiter(),
        listener_for=lambda sid: am.WakeListener(key=sid, device=PHONE, in_call=sid in calls),
    )
    server = SimpleNamespace(
        STATE=SimpleNamespace(config=config),
        _default_config=lambda: config,
        _is_logged_in=lambda request: False,
        _persist_state_config=lambda cfg: None,
        _describe_totp_capabilities=lambda cfg: {"totp_configured": True},
        _get_pairing_totp_secret=lambda cfg=None: "JBSWY3DPEHPK3PXP",
        _verify_totp_secret=lambda secret, code: False,
        agent_has_assigned_2fa_profile=lambda name: False,
        get_configured_server_name=lambda: "Jarvis",
        _admin_ui_js_file=lambda: ADMIN_UI_JS,
        WEBRTC=SimpleNamespace(assistant_mode=hub),
    )
    monkeypatch.setattr(smc, "_runtime_server", lambda: server)
    from autoyou_agents.assistant_agent.website.backend import app as backend

    monkeypatch.setattr(backend, "_WARM_UP_STARTED", False)
    monkeypatch.setattr(backend, "warm_up_speech", lambda speak: None)
    return SimpleNamespace(client=TestClient(backend.app), hub=hub, calls=calls)


def test_a_page_on_a_device_in_a_call_waits_for_the_call(site):
    """v2 or a phone in a call, with the Assistant page open: the call answers."""
    site.calls.add("synthetic-call")
    wake = site.client.post("/api/assistant/wake", json={"listener_id": "phone-page", "score": 0.9},
                            headers=_via("synthetic-call")).json()
    assert wake["accepted"] is False and wake["reason"] == "in_call" and wake["pause"] == "in_call"
    assert wake["message"] == am.REFUSAL_MESSAGES["in_call"]
    presence = site.client.post("/api/assistant/presence", json={"listener_id": "phone-page", "listening": True},
                                headers=_via("synthetic-call")).json()
    assert presence == {"success": True, "in_call": True, "pause": "in_call"}
    # A paused page does not hold the device's other listeners back.
    assert not site.hub.wake_arbiter.page_listening_on(PHONE)
    # The call ended: the page is told it may listen again.
    site.calls.clear()
    presence = site.client.post("/api/assistant/presence", json={"listener_id": "phone-page", "listening": True},
                                headers=_via("synthetic-call")).json()
    assert presence["in_call"] is False and presence["pause"] == ""


def test_a_listening_page_is_its_devices_one_listener(site):
    """The phone's own wake word stands down while its Assistant page listens."""
    site.client.post("/api/assistant/presence", json={"listener_id": "phone-page", "listening": True},
                     headers=_via("synthetic-session"))
    assert site.hub.wake_arbiter.page_listening_on(PHONE)
    phone_wake = site.hub.wake_arbiter.contend(am.WakeListener(key="synthetic-session", device=PHONE), score=0.95)
    assert not phone_wake.accepted and phone_wake.reason == "assistant_page"
    site.client.post("/api/assistant/presence", json={"listener_id": "phone-page", "listening": False},
                     headers=_via("synthetic-session"))
    assert not site.hub.wake_arbiter.page_listening_on(PHONE)


def test_a_page_speaking_its_answer_keeps_the_home_from_waking(site):
    kiosk = {**ORIGIN}
    site.client.post("/api/assistant/presence", json={"listener_id": "kiosk", "speaking": True, "seconds": 3},
                     headers=kiosk)
    other = site.client.post("/api/assistant/wake", json={"listener_id": "hallway", "score": 0.9}, headers=_via("synthetic-session")).json()
    assert other["accepted"] is False and other["reason"] == "speaking"
    site.client.post("/api/assistant/presence", json={"listener_id": "kiosk", "speaking": False}, headers=kiosk)
    time.sleep(am.SPEAKING_TAIL_SECONDS + 0.05)
    again = site.client.post("/api/assistant/wake", json={"listener_id": "hallway", "score": 0.9}, headers=_via("synthetic-session")).json()
    assert again["accepted"] is True


def test_the_clearer_hearing_wins_after_the_decision_window(site):
    """Two pages in one room: the answer comes after the window, from the better hearing."""
    arbiter = site.hub.wake_arbiter
    leader = am.WakeListener(key="web:other-room", kind=am.LISTENER_PAGE, device="lan:192.168.1.60")
    claim = arbiter.contend(leader, score=0.3)
    started = time.monotonic()
    wake = site.client.post("/api/assistant/wake", json={"listener_id": "nearest", "score": 0.8}, headers=ORIGIN).json()
    assert wake["accepted"] is True
    assert time.monotonic() - started < 2.0
    assert not arbiter.holds(leader, claim.round_id)


def test_presence_needs_a_listener_and_is_open_to_devices_that_may_ask(site):
    assert site.client.post("/api/assistant/presence", json={"listening": True}, headers=ORIGIN).status_code == 400
    assert remote_http_request_allowed("viewer", "POST", "/agent/assistant_agent/api/assistant/presence")
    assert not remote_http_request_allowed("viewer", "POST", "/agent/notes_agent/api/assistant/presence")


def test_the_page_pauses_for_calls_and_reports_what_it_does():
    script = (ROOT / "autoyou_agents" / "assistant_agent" / "website" / "frontend" / "assets" / "app.js").read_text(
        encoding="utf-8")
    # The score of its own detector goes with each wake, for the best-hearing rule.
    assert "hit.score" in script and "claim.score = score" in script
    # Presence while listening, speaking reports per stretch, a pause for calls.
    assert 'request("api/assistant/presence", body)' in script
    assert "reportSpeaking(parts.slice(i).join(\" \"))" in script
    assert 'if (data.pause === "in_call") pauseFor("call", true);' in script
    # The native apps drive the same pause.
    assert "pause: function (reason) { pauseFor(String(reason || \"call\"), false); }" in script
    # Nothing is spoken over a call on this device.
    assert "!voiceOff && !S.pausedFor && prefs.speak" in script
