# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Who answers a wake word, in every situation the owner listed.

The rules, in order:

1. A call owns its device's microphone.
2. One listener per device, and the Assistant page wins.
3. One answer per home: the device that heard the word best within the
   decision window answers, a device away from home answers for itself, and
   no wake counts there while an answer is being spoken.

Each test names the situation it covers. Identifiers are synthetic; public
addresses come from the documentation ranges.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared import assistant_mode as am  # noqa: E402
from shared import wake_word as ww  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv(am.ASSISTANT_MODE_DIR_ENV, str(tmp_path / "assistant_mode"))
    monkeypatch.setattr(am, "voice_training_clips", lambda limit=200: [])
    yield


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(am, "time", SimpleNamespace(monotonic=lambda: now["t"], time=time.time))
    return now


def page(key, device, *, place=am.HOME, in_call=False):
    return am.WakeListener(key=key, kind=am.LISTENER_PAGE, device=device, place=place, in_call=in_call)


def device(key, name, *, place=am.HOME, in_call=False, kind=am.LISTENER_DEVICE):
    return am.WakeListener(key=key, kind=kind, device=name, place=place, in_call=in_call)


def admin(key):
    return am.WakeListener(key=key, kind=am.LISTENER_ADMIN, device="this-computer")


class Recorder:
    def __init__(self):
        self.events = []
        self.commands = []
        self.lock = threading.Lock()

    def emit(self, session_id, payload):
        with self.lock:
            self.events.append((session_id, dict(payload)))

    def submit(self, session_id, text, meta):
        with self.lock:
            self.commands.append((session_id, text, dict(meta)))

    def transcribe(self, pcm):
        return "what time is it"

    def wait_for(self, predicate, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if predicate(self):
                    return True
            time.sleep(0.01)
        return False

    def states(self, session_id):
        return [
            (payload.get("state"), payload.get("reason"), payload.get("cause"))
            for sid, payload in self.events
            if sid == session_id and payload.get("event") == "assistant_state"
        ]


def make_hub(recorder, describe=None):
    return am.AssistantModeHub(
        emit=recorder.emit,
        submit=recorder.submit,
        transcribe=recorder.transcribe,
        server_name=lambda: "Jarvis",
        describe_listener=describe,
    )


# -- One thin or headless client at home -------------------------------------


def test_one_thin_client_at_home_answers(clock):
    arbiter = am.WakeArbiter()
    kiosk = page("web:kiosk", "lan:192.168.1.40")
    claim = arbiter.contend(kiosk, score=0.7)
    assert claim.accepted and claim.reason == "wake_word"
    clock["t"] += am.WAKE_DECISION_SECONDS + 0.01
    assert arbiter.holds(kiosk, claim.round_id)


# -- Several devices in one room -----------------------------------------------


def test_the_device_that_heard_the_word_best_answers(clock):
    arbiter = am.WakeArbiter()
    revoked = []
    kitchen = page("web:kitchen", "lan:192.168.1.41")
    phone = device("phone-session", "webrtc:synthetic-phone")
    first = arbiter.contend(kitchen, score=0.62, on_revoke=revoked.append)
    assert first.accepted
    clock["t"] += 0.1
    # The phone was nearer: it heard the word more clearly and takes the wake.
    better = arbiter.contend(phone, score=0.81)
    assert better.accepted and better.round_id == first.round_id
    assert revoked == ["answered_elsewhere"]
    assert not arbiter.holds(kitchen, first.round_id)
    assert arbiter.holds(phone, first.round_id)
    # A weaker hearing inside the window is turned away at once.
    weaker = arbiter.contend(device("tv-session", "webrtc:synthetic-tv"), score=0.4)
    assert not weaker.accepted and weaker.reason == "answered_elsewhere"
    # After the decision, even a clearer late copy of the same word loses.
    clock["t"] += am.WAKE_DECISION_SECONDS
    late = arbiter.contend(device("hall-session", "webrtc:synthetic-hall"), score=0.99)
    assert not late.accepted


def test_without_both_scores_the_first_claim_keeps_the_wake(clock):
    arbiter = am.WakeArbiter()
    first = arbiter.contend(device("stream-a", "webrtc:a"))
    second = arbiter.contend(device("stream-b", "webrtc:b"), score=0.99)
    assert first.accepted and not second.accepted


def test_no_wake_counts_in_the_home_while_an_answer_is_spoken(clock):
    """A spoken answer that contains the wake word must not wake another device."""
    arbiter = am.WakeArbiter()
    kiosk = page("web:kiosk", "lan:192.168.1.40")
    phone = device("phone-session", "webrtc:synthetic-phone")
    arbiter.speaking(kiosk, 4.0)
    assert arbiter.contend(kiosk, score=1.0).reason == "speaking"
    refused = arbiter.contend(phone, score=0.9)
    assert not refused.accepted and refused.reason == "speaking"
    # Someone away from home is not held up by the robot speaking here.
    away = device("travel-session", "webrtc:synthetic-travel", place="away:webrtc:synthetic-travel")
    assert arbiter.contend(away, score=0.9).accepted
    # The answer ended: only a short tail remains, then the home listens again.
    arbiter.speaking(kiosk, 0)
    assert arbiter.contend(phone, score=0.9).reason == "speaking"
    clock["t"] += am.SPEAKING_TAIL_SECONDS + 0.01
    assert arbiter.snapshot()["speaking"] == []
    assert arbiter.contend(kiosk, score=1.0, spoken_at=clock["t"]-am.SPEAKING_TAIL_SECONDS).reason == "speaking"
    assert arbiter.contend(phone, score=0.9).accepted


def test_a_speaking_report_cannot_hold_the_home_for_ever(clock):
    arbiter = am.WakeArbiter()
    arbiter.speaking(page("web:kiosk", "lan:k"), 10_000)
    clock["t"] += am.MAX_SPEAKING_SECONDS + am.SPEAKING_TAIL_SECONDS + 0.01
    assert arbiter.contend(device("phone-session", "webrtc:p"), score=0.9).accepted


# -- v2 with no call: the Assistant section and the Calls wake word -------------


def test_the_assistant_page_takes_over_its_devices_wake_word(clock):
    arbiter = am.WakeArbiter()
    section = page("web:v2-section", "webrtc:synthetic-desktop")
    calls_stream = device("desktop-session", "webrtc:synthetic-desktop")
    arbiter.page_listening(section, True)
    refused = arbiter.contend(calls_stream, score=0.95)
    assert not refused.accepted and refused.reason == "assistant_page"
    # Another device in the home is not held back by this one's page.
    assert arbiter.contend(device("phone-session", "webrtc:synthetic-phone"), score=0.5).accepted
    clock["t"] += 5
    # The page stopped listening: the Calls wake word answers again.
    arbiter.page_listening(section, False)
    assert arbiter.contend(calls_stream, score=0.95).accepted


def test_on_one_device_the_page_wins_the_same_wake_even_with_a_weaker_hearing(clock):
    arbiter = am.WakeArbiter()
    revoked = []
    stream = arbiter.contend(device("desktop-session", "webrtc:synthetic-desktop"), score=0.95, on_revoke=revoked.append)
    section = arbiter.contend(page("web:v2-section", "webrtc:synthetic-desktop"), score=0.3)
    assert stream.accepted and section.accepted and revoked == ["answered_elsewhere"]
    # And the stream cannot take it back.
    again = arbiter.contend(device("desktop-session-2", "webrtc:synthetic-desktop"), score=0.99)
    assert not again.accepted


def test_a_page_presence_report_expires_when_the_page_goes_quiet(clock):
    arbiter = am.WakeArbiter()
    arbiter.page_listening(page("web:gone", "webrtc:synthetic-desktop"), True)
    assert arbiter.page_listening_on("webrtc:synthetic-desktop")
    clock["t"] += am.PAGE_PRESENCE_SECONDS + 0.01
    assert not arbiter.page_listening_on("webrtc:synthetic-desktop")
    assert arbiter.contend(device("desktop-session", "webrtc:synthetic-desktop"), score=0.9).accepted


# -- Calls: ordinary, wake-word, and a phone in a call ---------------------------


def test_a_call_owns_its_devices_microphone(clock):
    arbiter = am.WakeArbiter()
    # v2 in an ordinary call with the section listening: the call answers.
    section = arbiter.contend(page("web:v2-section", "webrtc:synthetic-desktop", in_call=True), score=0.9)
    assert not section.accepted and section.reason == "in_call"
    # The phone's own wake word during its call is refused the same way.
    phone_wake = arbiter.contend(device("phone-session", "webrtc:synthetic-phone", in_call=True), score=0.9)
    assert phone_wake.reason == "in_call"
    # A wake-word call is the call's own listener and answers inside the call.
    call = arbiter.contend(device("call-session", "webrtc:synthetic-phone", in_call=True, kind=am.LISTENER_CALL), score=0.9)
    assert call.accepted


# -- The server computer: the admin listener and an Assistant page ------------------


def test_on_this_computer_the_assistant_page_beats_the_admin_listener(clock):
    arbiter = am.WakeArbiter()
    arbiter.page_listening(page("web:local-page", "this-computer"), True)
    refused = arbiter.contend(admin("browser:chat-history"), score=0.9)
    assert not refused.accepted and refused.reason == "assistant_page"
    assert am.REFUSAL_MESSAGES["assistant_page"]


# -- A cloud-paired device away from home ------------------------------------------


def test_a_device_away_from_home_answers_for_itself(clock):
    arbiter = am.WakeArbiter()
    home = arbiter.contend(page("web:kiosk", "lan:192.168.1.40"), score=0.6)
    away = arbiter.contend(
        device("travel-session", "webrtc:synthetic-travel", place="away:webrtc:synthetic-travel"), score=0.5
    )
    assert home.accepted and away.accepted and home.round_id != away.round_id


# -- The hub: streamed and on-device wakes are told when they lose ---------------------


def test_a_streamed_wake_is_handed_back_when_a_page_heard_it_better(clock):
    am.save_settings({"enabled": True, "wake_phrase": "Jarvis", "confirm": "voice"})
    recorder = Recorder()
    describe = lambda sid: am.WakeListener(key=sid, device="webrtc:synthetic-" + sid)  # noqa: E731
    hub = make_hub(recorder, describe)
    try:
        hub.client_wake("laptop", score=0.5)
        assert recorder.wait_for(lambda r: ("listening", "wake_word", None) in r.states("laptop"))
        better = hub.wake_arbiter.contend(page("web:kitchen", "lan:192.168.1.41"), score=0.9)
        assert better.accepted
        assert recorder.wait_for(lambda r: ("idle", "answered_elsewhere", None) in r.states("laptop"))
        assert hub.session_state("laptop") == "idle"
        assert recorder.commands == []
    finally:
        hub.close()


def test_an_announced_phone_wake_is_refused_after_it_was_handed_back(clock):
    rng = np.random.default_rng(7)
    from test_wake_word import enroll

    am.save_settings({"enabled": True, "wake_phrase": "Hey AutoYou"})
    for recording in enroll(rng):
        assert am.add_sample(recording)["stored"] is True
    am.train(phrase="Hey AutoYou")
    recorder = Recorder()
    hub = make_hub(recorder)
    try:
        config = hub.config()
        item = hub._session("phone-session")
        item.reserve_device_wake("synthetic-wake", config, 0.4)
        assert item.device_wake[1] is True
        assert hub.wake_arbiter.contend(page("web:kitchen", "lan:192.168.1.41"), score=0.9).accepted
        assert recorder.wait_for(lambda r: item.device_wake is not None and item.device_wake[1] is False)
        item.device_utterance(ww.float_to_pcm16(np.zeros(16000)), config, 0.4, "synthetic-wake")
        assert recorder.wait_for(lambda r: r.states("phone-session").count(("idle", "answered_elsewhere", None)) >= 2)
        assert recorder.commands == []
    finally:
        hub.close()


def test_a_refused_device_is_told_why(clock):
    am.save_settings({"enabled": True, "wake_phrase": "Jarvis", "confirm": "voice"})
    recorder = Recorder()
    describe = lambda sid: am.WakeListener(key=sid, device="webrtc:synthetic-phone", in_call=True)  # noqa: E731
    hub = make_hub(recorder, describe)
    try:
        hub.client_wake("phone-session", score=0.9)
        # Old clients read "answered_elsewhere"; newer ones see which rule decided it.
        assert recorder.wait_for(lambda r: ("idle", "answered_elsewhere", "in_call") in r.states("phone-session"))
    finally:
        hub.close()


def test_without_a_description_every_session_is_its_own_device_at_home():
    hub = make_hub(Recorder())
    try:
        assert hub.listener_for("synthetic") == am.WakeListener(key="synthetic")
        assert hub.listener_for("synthetic").device_key == "synthetic"
    finally:
        hub.close()


# -- Where a browser listener is, from AutoYou's own stamps only ------------------------


def _request(headers=None, host="127.0.0.1"):
    return SimpleNamespace(headers=dict(headers or {}), client=SimpleNamespace(host=host))


def test_browser_listeners_are_placed_from_autoyous_own_stamps():
    via_phone = {"X-AutoYou-Remote-Browser": "webrtc", "X-AutoYou-WebRTC-Session-Id": "synthetic-session"}
    hub = SimpleNamespace(listener_for=lambda sid: am.WakeListener(
        key=sid, device="webrtc:synthetic-owner", place="away:webrtc:synthetic-owner", in_call=True))
    # Opened through a paired app: that device, its place and its call.
    proxied = am.browser_listener(_request(via_phone), "web:a", kind=am.LISTENER_PAGE, hub=hub)
    assert proxied == am.WakeListener(key="web:a", kind=am.LISTENER_PAGE, device="webrtc:synthetic-owner",
                                      place="away:webrtc:synthetic-owner", in_call=True)
    no_hub = am.browser_listener(_request(via_phone), "web:a", kind=am.LISTENER_PAGE)
    assert no_hub.device == "webrtc:synthetic-session" and no_hub.place == am.HOME
    # Through a public link: nobody at home hears it.
    tunnel = am.browser_listener(_request({"X-AutoYou-Tunnel-Client-IP": "203.0.113.9"}), "web:b", kind=am.LISTENER_PAGE)
    assert tunnel.place == "away:web:b"
    # An address outside the home network (here the carrier/VPN shared range).
    outside = am.browser_listener(_request(host="100.64.0.9"), "web:c", kind=am.LISTENER_PAGE)
    assert outside.place == "away:web:c"
    # A browser on the home network is its own device at home.
    lan = am.browser_listener(_request(host="192.168.1.50"), "web:d", kind=am.LISTENER_PAGE)
    assert lan.device == "lan:192.168.1.50" and lan.place == am.HOME
    relayed = am.browser_listener(_request({"X-AutoYou-Remote-Browser": "home_network"}), "web:e", kind=am.LISTENER_PAGE)
    assert relayed.device == "lan:web:e" and relayed.place == am.HOME
    # Signed in on another device through this computer's address (https://<lan-ip>:8443):
    # the admin port hands the page on as this computer, but the address the browser
    # used shows it is another device at home.
    other_device = am.browser_listener(_request({"X-Forwarded-Host": "192.168.1.208:8443"}), "web:g", kind=am.LISTENER_PAGE)
    assert other_device.device == "lan:web:g" and other_device.place == am.HOME
    for host in ("127.0.0.1:8443", "localhost:8001", "[::1]:8443"):
        here = am.browser_listener(_request({"X-Forwarded-Host": host}), "web:h", kind=am.LISTENER_PAGE)
        assert here.device == "this-computer", host
    # Anything else is this computer.
    local = am.browser_listener(_request(), "browser:f", kind=am.LISTENER_ADMIN)
    assert local.device == "this-computer" and local.kind == am.LISTENER_ADMIN


def test_home_addresses_are_loopback_private_and_link_local():
    for value in ("127.0.0.1", "::1", "192.168.1.208", "10.0.0.5", "172.16.4.4", "fe80::1", "::ffff:192.168.1.9"):
        assert am._is_home_address(value), value
    # The carrier/VPN shared range is not this home's network.
    for value in ("100.64.0.9", "100.127.255.1"):
        assert not am._is_home_address(value), value
