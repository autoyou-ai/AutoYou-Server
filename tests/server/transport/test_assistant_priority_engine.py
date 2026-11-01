# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""How the WebRTC engine describes each connected device to the wake arbiter.

Which device a session is (this computer, or a paired device), where it is
(home, or away when a cloud pairing runs over the internet or a public link
carries it), and whether a call owns its microphone. Sessions and addresses
are synthetic.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server  # noqa: E402
from shared import assistant_mode as am  # noqa: E402


@pytest.fixture
def webrtc(tmp_path, monkeypatch):
    monkeypatch.setenv(am.ASSISTANT_MODE_DIR_ENV, str(tmp_path / "assistant_mode"))
    cfg = server._default_config()
    original = server.STATE.config
    server.STATE.config = cfg
    manager = server.WebRTCManager()
    try:
        yield manager
    finally:
        manager.assistant_mode.close()
        server.STATE.config = original


def _peer(remote_host):
    pair = SimpleNamespace(remote_addr=(remote_host, 50000))
    ice = SimpleNamespace(_connection=SimpleNamespace(_nominated={1: pair}))
    return SimpleNamespace(sctp=SimpleNamespace(transport=SimpleNamespace(transport=ice)), getTransceivers=lambda: [])


def test_the_hub_asks_the_engine_who_each_session_is(webrtc):
    assert webrtc.assistant_mode._describe_listener == webrtc._assistant_listener_for_session


def test_a_paired_device_at_home_is_its_own_device(webrtc):
    listener = webrtc.assistant_mode.listener_for("synthetic-phone-session")
    assert listener.key == "synthetic-phone-session"
    assert listener.device.startswith("webrtc:")
    assert listener.place == am.HOME
    assert listener.kind == am.LISTENER_DEVICE and listener.in_call is False


def test_a_device_paired_from_this_computer_is_this_computer(webrtc):
    webrtc.same_machine_audio_sessions.add("synthetic-local-session")
    assert webrtc.assistant_mode.listener_for("synthetic-local-session").device == "this-computer"


def test_a_call_owns_the_session_and_a_wake_word_call_is_its_listener(webrtc, monkeypatch):
    monkeypatch.setattr(webrtc, "_voice_call_client_active_for_session", lambda session_id: True)
    ordinary = webrtc.assistant_mode.listener_for("synthetic-call")
    assert ordinary.in_call is True and ordinary.kind == am.LISTENER_DEVICE
    monkeypatch.setattr(webrtc, "_assistant_gates_calls", lambda session_id="": True)
    gated = webrtc.assistant_mode.listener_for("synthetic-call")
    assert gated.in_call is True and gated.kind == am.LISTENER_CALL


@pytest.mark.parametrize(
    "transport, pairing, remote, away",
    [
        ("local", "local_pair", "", False),
        ("bluetooth", "bluetooth_pair", "", False),
        ("tunnelmole", "", "", True),
        ("public", "", "", True),
        # Cloud Pair says how the device found this computer, not where it is.
        ("cloud", "cloud_pair", "192.168.1.20", False),
        ("cloud", "cloud_pair", "100.64.0.9", True),
        ("cloud", "cloud_pair", "", False),
    ],
)
def test_only_a_device_that_is_really_elsewhere_decides_its_own_wakes(webrtc, transport, pairing, remote, away):
    if remote:
        webrtc.session_peers["synthetic-cloud"] = _peer(remote)
    identity = SimpleNamespace(transport=transport, pairing_mode=pairing)
    assert webrtc._assistant_session_is_away("synthetic-cloud", identity) is away


def test_the_connections_own_address_is_read_from_its_nominated_pair(webrtc):
    webrtc.session_peers["synthetic-cloud"] = _peer("192.168.1.20")
    assert webrtc._nominated_remote_host("synthetic-cloud") == "192.168.1.20"
    assert webrtc._nominated_remote_host("synthetic-unknown") == ""
    webrtc.session_peers["synthetic-broken"] = SimpleNamespace()
    assert webrtc._nominated_remote_host("synthetic-broken") == ""


def test_a_call_speaking_its_answer_keeps_the_home_from_waking(webrtc):
    webrtc._assistant_note_speaking("synthetic-call", "Here is the weather for today and tomorrow.")
    other = am.WakeListener(key="web:kiosk", kind=am.LISTENER_PAGE, device="lan:192.168.1.40")
    refused = webrtc.assistant_mode.wake_arbiter.contend(other, score=0.9)
    assert not refused.accepted and refused.reason == "speaking"
    # The call itself is not held back by its own voice.
    own = webrtc.assistant_mode.wake_arbiter.contend(webrtc.assistant_mode.listener_for("synthetic-call"), score=0.9)
    assert own.accepted
