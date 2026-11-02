# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The Assistant website's answers, its sign-in, and what it hears.

A slow answer must survive proxies that give up after about 30 s; near-silence
must never be transcribed into invented words; the sign-in screen must not
reload in a loop; and a second start may never open a second microphone.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import asyncio
import base64
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from autoyou_agents.shared_tools import scheduler_mission_control as smc
from shared import assistant_mode as am
from shared import wake_word as ww
from shared.remote_access_policy import remote_http_request_allowed

ROOT = Path(__file__).resolve().parents[3]
ADMIN_UI_JS = ROOT / "assets" / "admin-ui.js"
FRONTEND = ROOT / "autoyou_agents" / "assistant_agent" / "website" / "frontend"
ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def site(tmp_path, monkeypatch):
    config = {"agent_ui_security": {"assistant_agent": {"auth_mode": "open"}}, "agent_websites": {}}
    monkeypatch.setenv(am.ASSISTANT_MODE_DIR_ENV, str(tmp_path / "assistant_mode"))
    monkeypatch.setattr(smc, "_AGENT_UI_SESSIONS_FILE", str(tmp_path / "agent_ui_sessions.json"))
    monkeypatch.setattr(smc, "_AGENT_UI_SESSIONS", {})
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
        WEBRTC=None,
    )
    monkeypatch.setattr(smc, "_runtime_server", lambda: server)
    from autoyou_agents.assistant_agent.website.backend import app as backend

    monkeypatch.setattr(backend, "_WARM_UP_STARTED", False)
    monkeypatch.setattr(backend, "warm_up_speech", lambda speak: None)
    monkeypatch.setattr(backend, "_TURNS", {})
    return SimpleNamespace(backend=backend, config=config)


def _wav(samples):
    return base64.b64encode(ww.pcm16_to_wav_bytes(ww.float_to_pcm16(samples))).decode()


def _speech(seconds=1.2):
    t = np.arange(int(seconds * ww.SAMPLE_RATE)) / ww.SAMPLE_RATE
    envelope = np.clip(np.sin(t * np.pi * 3.0), 0, None) ** 2
    return 0.3 * envelope * (np.sin(2 * np.pi * 150 * t) + 0.5 * np.sin(2 * np.pi * 300 * t))


# -- Slow answers -----------------------------------------------------------------


def test_a_slow_answer_is_collected_after_the_question(site, monkeypatch):
    release = threading.Event()

    async def slow_ask(text, *, session_id, wake):
        while not release.is_set():
            await asyncio.sleep(0.01)
        return {"response": "Paris, for a long time now.", "agent_name": "assistant_agent"}

    monkeypatch.setattr(site.backend, "ask_assistant", slow_ask)
    monkeypatch.setattr(site.backend, "TURN_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(site.backend, "TURN_POLL_WAIT_SECONDS", 0.05)
    with TestClient(site.backend.app) as client:
        first = client.post("/api/assistant/turn", json={"text": "capital of France", "session_id": "synthetic-1234"},
                            headers=ORIGIN).json()
        assert first["success"] is True and first["pending"] is True
        turn_id = first["turn_id"]
        still = client.get("/api/assistant/turn/" + turn_id).json()
        assert still["pending"] is True
        release.set()
        done = client.get("/api/assistant/turn/" + turn_id).json()
        for _ in range(50):
            if not done.get("pending"):
                break
            done = client.get("/api/assistant/turn/" + turn_id).json()
        assert done["reply"] == "Paris, for a long time now." and "pending" not in done


def test_a_quick_answer_comes_back_with_the_question(site, monkeypatch):
    async def quick_ask(text, *, session_id, wake):
        return {"response": "Paris.", "agent_name": "assistant_agent"}

    monkeypatch.setattr(site.backend, "ask_assistant", quick_ask)
    with TestClient(site.backend.app) as client:
        data = client.post("/api/assistant/turn", json={"text": "capital of France", "session_id": "synthetic-1234"},
                           headers=ORIGIN).json()
    assert data["reply"] == "Paris." and "pending" not in data and data["turn_id"]


def test_a_failed_slow_answer_reports_why(site, monkeypatch):
    async def broken_ask(text, *, session_id, wake):
        await asyncio.sleep(0.1)
        raise RuntimeError("synthetic outage")

    monkeypatch.setattr(site.backend, "ask_assistant", broken_ask)
    monkeypatch.setattr(site.backend, "TURN_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(site.backend, "TURN_POLL_WAIT_SECONDS", 1.0)
    with TestClient(site.backend.app) as client:
        first = client.post("/api/assistant/turn", json={"text": "hello", "session_id": "synthetic-1234"}, headers=ORIGIN)
        assert first.json()["pending"] is True
        failed = client.get("/api/assistant/turn/" + first.json()["turn_id"])
    assert failed.status_code == 502 and "could not answer" in failed.json()["error"]


def test_unknown_or_malformed_answers_are_not_found(site):
    with TestClient(site.backend.app) as client:
        assert client.get("/api/assistant/turn/" + "x" * 24).status_code == 404
        assert client.get("/api/assistant/turn/..%2Fsecret").status_code == 404


def test_devices_that_may_ask_may_collect_answers():
    assert remote_http_request_allowed("viewer", "GET", "/agent/assistant_agent/api/assistant/turn/abcdefghijklmnopqrstuvwx")


# -- What it hears ----------------------------------------------------------------


def test_near_silence_is_never_transcribed(site, monkeypatch):
    heard = []
    monkeypatch.setattr(am, "local_transcriber", lambda *a, **k: (lambda pcm: heard.append(len(pcm)) or "Thank you."))
    with TestClient(site.backend.app) as client:
        quiet = client.post("/api/assistant/transcribe", json={"audio_base64": _wav(np.zeros(16000)), "listener_id": "abc"},
                            headers=ORIGIN).json()
        assert quiet["transcript"] == "" and quiet["reason"] == "no_speech" and heard == []
        # Whisper's stock phrase for a breath is dropped when hardly anything was said.
        short = _speech(0.5)
        breath = client.post("/api/assistant/transcribe", json={"audio_base64": _wav(short), "listener_id": "abc"},
                             headers=ORIGIN).json()
        assert heard and breath["transcript"] == "" and breath["command"] == ""


def test_real_speech_is_transcribed(site, monkeypatch):
    monkeypatch.setattr(am, "local_transcriber", lambda *a, **k: (lambda pcm: "what is the capital of France"))
    with TestClient(site.backend.app) as client:
        data = client.post("/api/assistant/transcribe", json={"audio_base64": _wav(_speech(1.6)), "listener_id": "abc"},
                           headers=ORIGIN).json()
    assert data["command"] == "what is the capital of France"


def test_voiced_seconds_measures_speech_not_length(site):
    assert site.backend.voiced_seconds(ww.float_to_pcm16(np.zeros(32000))) == 0.0
    assert site.backend.voiced_seconds(ww.float_to_pcm16(_speech(1.6))) > 0.4


# -- Cold-start and slow transcription --------------------------------------------


def test_a_slow_transcription_is_collected_via_result_endpoint(site, monkeypatch):
    release = threading.Event()

    def slow_transcribe(*a, **k):
        def _transcribe(pcm):
            release.wait(timeout=5.0)
            return "what is the capital of France"
        return _transcribe

    monkeypatch.setattr(am, "local_transcriber", slow_transcribe)
    monkeypatch.setattr(site.backend, "TRANSCRIBE_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(site.backend, "TURN_POLL_WAIT_SECONDS", 0.05)
    with TestClient(site.backend.app) as client:
        first = client.post(
            "/api/assistant/transcribe",
            json={"audio_base64": _wav(_speech(1.6)), "listener_id": "abc"},
            headers=ORIGIN,
        ).json()
        assert first["success"] is True and first["pending"] is True
        job_id = first["job_id"]
        assert first["turn_id"] == job_id
        still = client.get("/api/assistant/result/" + job_id).json()
        assert still["pending"] is True
        release.set()
        done = client.get("/api/assistant/result/" + job_id).json()
        for _ in range(50):
            if not done.get("pending"):
                break
            done = client.get("/api/assistant/result/" + job_id).json()
        assert done["command"] == "what is the capital of France" and "pending" not in done
        # The legacy turn endpoint alias also returns the result.
        legacy = client.get("/api/assistant/turn/" + job_id).json()
        assert legacy["command"] == "what is the capital of France"


def test_speech_recognition_warm_up_status_and_waiting(site):
    backend = site.backend
    backend._SPEECH_WARMING.clear()
    assert backend.assistant_status()["speech_ready"] is True

    backend._SPEECH_WARMING.set()
    assert backend.assistant_status()["speech_ready"] is False

    backend._SPEECH_WARMING.clear()
    assert asyncio.run(backend.wait_for_speech_warm_up(limit=0.5)) is True


def test_a_failed_slow_transcription_reports_via_result(site, monkeypatch):
    release = threading.Event()

    def failing_transcribe(*a, **k):
        def _transcribe(pcm):
            release.wait(timeout=5.0)
            raise RuntimeError("synthetic transcription crash")
        return _transcribe

    monkeypatch.setattr(am, "local_transcriber", failing_transcribe)
    monkeypatch.setattr(site.backend, "TRANSCRIBE_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(site.backend, "TURN_POLL_WAIT_SECONDS", 0.05)
    with TestClient(site.backend.app) as client:
        first = client.post(
            "/api/assistant/transcribe",
            json={"audio_base64": _wav(_speech(1.6)), "listener_id": "abc"},
            headers=ORIGIN,
        ).json()
        assert first["pending"] is True
        release.set()
        failed = client.get("/api/assistant/result/" + first["job_id"])
        for _ in range(50):
            if not failed.json().get("pending"):
                break
            failed = client.get("/api/assistant/result/" + first["job_id"])
        assert failed.status_code == 503 and "not available" in failed.json()["error"]


def test_the_robot_sleeping_eyes_are_curved_eyelids_not_squashed_rings():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    script = _script()
    assert 'class="eye-sleep"' in page
    assert 'eye.querySelector(".eye-sleep")' in script
    assert "eyeOpen = Math.max(0.1, anim.blink)" in script


# -- The page ---------------------------------------------------------------------


def _script():
    return (FRONTEND / "assets" / "app.js").read_text(encoding="utf-8")


def test_the_sign_in_screen_never_reloads_in_a_loop():
    script = _script()
    # Signed out: the page does nothing until the sign-in screen reloads it.
    assert "var signedOut = Boolean(AUTH.required && !AUTH.authenticated);" in script
    start = script.index("// ---- Start ----")
    assert script.index("if (signedOut) return;", start) < script.index("refreshStatus(true)", start)
    # A sign-in that ends later reloads at most once a minute.
    assert 'sessionStorage.getItem(STORE + "authReload")' in script and "Date.now() - last > 60000" in script
    request = script[script.index("function request("):script.index("function signedOutNow(")]
    assert "location.reload" not in request


def test_one_microphone_at_a_time():
    script = _script()
    assert "if (S.capture !== capture) return;" in script
    assert "if (S.starting) return S.starting;" in script
    assert "if (!S.wantListening || S.pausedFor || S.capture) { stopCapture(capture); return; }" in script


def test_the_conversation_scrolls_without_a_scrollbar_and_can_be_stopped():
    script = _script()
    styles = (FRONTEND / "assets" / "styles.css").read_text(encoding="utf-8")
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="turns"' in page and 'id="ask-send"' in page and 'class="icon-stop"' in page
    assert "scrollbar-width: none" in styles and ".conversation::-webkit-scrollbar { display: none" in styles
    assert "if (!text) { if (busy()) stopTurn(); return; }" in script
    assert 'save("history", history.slice(-HISTORY_LIMIT))' in script
    # A follow-up that hears nothing goes back to waiting without an error.
    assert "if (!followUp) hint(\"Sorry, I didn't catch that.\", \"\", 4);" in script


def test_the_robot_turns_each_joint_on_its_ball_and_never_through_itself():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    script = _script()
    # Shoulder balls at 116/284 and elbow balls under the same centres.
    assert '<circle cx="116" cy="322" r="17"' in page and '<circle cx="284" cy="322" r="17"' in page
    assert '<circle cx="116" cy="388" r="11"' in page and '<circle cx="284" cy="388" r="11"' in page
    for pivot in ("116 322", "284 322", "116 388", "284 388"):
        assert pivot + ')")' in script
    # The forearm is drawn under the upper arm and its elbow ball.
    left = page[page.index('<g id="arm-left">'):page.index('<g id="arm-right">')]
    assert left.index('id="forearm-left"') < left.index('<rect x="101" y="318"') < left.index('cy="388" r="11"')
    # No logo or new parts on the robot: the same shapes, joints only.
    assert "logo" not in page.lower()
    assert re.search(r"target\.armR = clamp\(target\.armR, -140, 8\)", script)
    assert "function drive(j, target, dt)" in script


def test_robot_features_glowing_backpack_emblem_and_expressive_eyebrows():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    script = _script()
    # Eyebrows exist in SVG face group above eyes
    assert 'id="brow-l"' in page and 'id="brow-r"' in page
    # Forehead blemish (ellipse rx="58" and sheen path M112 128) is removed
    assert 'rx="58"' not in page and 'M112 128' not in page
    # Backpack-inspired core emblem is on torso with cloud and network nodes
    assert 'id="core"' in page
    assert '<polygon points="0,-25.5 22.5,-12.5 22.5,12.5 0,25.5 -22.5,12.5 -22.5,-12.5"' in page
    # Eyebrow spring joints and speech/emotion reactions wired in script
    assert "browLY: joint(0, 110, 0.6)" in script
    assert "browLTilt: joint(0, 85, 0.6)" in script
    assert "bot.browL.setAttribute" in script and "bot.browR.setAttribute" in script
