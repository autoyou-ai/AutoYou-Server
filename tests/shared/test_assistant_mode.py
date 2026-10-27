# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Assistant mode: settings, wake word samples and training, and the live gate.

The central promise under test: until the wake word is heard, nothing is
transcribed and nothing reaches the model. Storage is redirected to
``tmp_path``; transcription and the agent hand-off are recording fakes.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared import assistant_mode as am  # noqa: E402
from shared import wake_word as ww  # noqa: E402
from test_wake_word import ALSO_OTHER, OTHER, WAKE, enroll, synth_word, with_noise  # noqa: E402

SR = ww.SAMPLE_RATE


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv(am.ASSISTANT_MODE_DIR_ENV, str(tmp_path / "assistant_mode"))
    monkeypatch.setattr(am, "voice_training_clips", lambda limit=200: [])
    yield tmp_path / "assistant_mode"


class Recorder:
    def __init__(self, transcripts=None):
        self.events = []
        self.commands = []
        self.transcribed = []
        self.transcripts = list(transcripts or [])
        self.lock = threading.Lock()

    def emit(self, session_id, payload):
        with self.lock:
            self.events.append((session_id, dict(payload)))

    def submit(self, session_id, text, meta):
        with self.lock:
            self.commands.append((session_id, text, dict(meta)))

    def transcribe(self, pcm):
        with self.lock:
            self.transcribed.append(len(pcm))
            return self.transcripts.pop(0) if self.transcripts else ""

    def wait_for(self, predicate, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if predicate(self):
                    return True
            time.sleep(0.01)
        return False

    def states(self, session_id="phone"):
        return [
            (payload.get("state"), payload.get("reason"))
            for sid, payload in self.events
            if sid == session_id and payload.get("event") == "assistant_state"
        ]


def make_hub(recorder, server_name="AutoYou-Server"):
    return am.AssistantModeHub(
        emit=recorder.emit,
        submit=recorder.submit,
        transcribe=recorder.transcribe,
        server_name=lambda: server_name,
    )


def feed(hub, session_id, samples, chunk=320):
    pcm = ww.float_to_pcm16(samples)
    for start in range(0, len(pcm), chunk * 2):
        hub.feed(session_id, pcm[start : start + chunk * 2])


def silence(seconds):
    return np.zeros(int(seconds * SR))


def train_voice_model(rng, phrase="Hey AutoYou"):
    for recording in enroll(rng):
        assert am.add_sample(recording)["stored"] is True
    return am.train(phrase=phrase)


def test_settings_round_trip_and_phrase_defaults_to_the_server_name(isolated_store):
    assert am.load_settings()["enabled"] is False
    saved = am.save_settings({"enabled": "on", "confirm": "nonsense", "sensitivity": 7, "listen_seconds": 99})
    assert saved["enabled"] is True
    assert saved["confirm"] == "voice"
    assert saved["sensitivity"] == 1.0
    assert saved["listen_seconds"] == 30
    assert am.load_settings()["enabled"] is True
    assert am.effective_phrase(am.load_settings(), "AutoYou-Server") == "AutoYou Server"
    am.save_settings({"wake_phrase": "  Hey\tJarvis\n"})
    assert am.effective_phrase(am.load_settings(), "AutoYou-Server") == "Hey Jarvis"
    assert (isolated_store / am.SETTINGS_FILENAME).is_file()


def test_samples_are_checked_bounded_and_deletable():
    rng = np.random.default_rng(20)
    rejected = am.add_sample(ww.float_to_pcm16(silence(1.0)))
    assert rejected["stored"] is False
    stored = [am.add_sample(recording) for recording in enroll(rng, count=3)]
    assert all(item["stored"] for item in stored)
    assert len(am.list_samples()) == 3
    assert am.read_sample_pcm(stored[0]["id"])
    assert am.delete_sample(stored[0]["id"]) is True
    assert am.delete_sample("../../etc/passwd") is False
    assert len(am.list_samples()) == 2
    for recording in enroll(rng, count=8) + enroll(rng, count=8):
        am.add_sample(recording)
    assert len(am.list_samples()) == am.MAX_STORED_SAMPLES
    assert am.clear_samples() == am.MAX_STORED_SAMPLES
    assert am.list_samples() == []


def test_training_needs_three_recordings_then_reports_ready():
    rng = np.random.default_rng(21)
    with pytest.raises(ValueError):
        am.train(phrase="Hey AutoYou")
    result = train_voice_model(rng)
    assert result["ok"] is True
    am.save_settings({"enabled": True, "wake_phrase": "Hey AutoYou"})
    status = am.public_status("AutoYou-Server")
    assert status["ready"] is True
    assert status["model"]["matches_phrase"] is True
    # A new phrase invalidates the acoustic model until it is retrained.
    am.save_settings({"wake_phrase": "Hey Jarvis"})
    assert am.public_status("AutoYou-Server")["ready"] is False
    capability = am.capability_payload("AutoYou-Server", available=True)
    assert capability["client_wake"] is True and capability["stream_listening"] is True


def test_voice_wake_word_gates_transcription_and_hands_over_one_command():
    rng = np.random.default_rng(22)
    train_voice_model(rng)
    am.save_settings({"enabled": True, "wake_phrase": "Hey AutoYou", "confirm": "voice"})
    recorder = Recorder(transcripts=["what time is it"])
    hub = make_hub(recorder)
    try:
        # Ordinary speech and silence: nothing is transcribed or handed over.
        feed(hub, "phone", with_noise(synth_word(OTHER, rng=rng), rng=rng))
        feed(hub, "phone", silence(1.5))
        feed(hub, "phone", with_noise(synth_word(ALSO_OTHER, rng=rng), rng=rng))
        assert recorder.wait_for(lambda r: hub._queue.empty(), timeout=5)
        time.sleep(0.1)
        assert recorder.transcribed == []
        assert recorder.commands == []

        # The wake word, then a command, then a pause.
        feed(hub, "phone", with_noise(synth_word(WAKE, rng=rng, f0=138), rng=rng, pad_seconds=0.1))
        feed(hub, "phone", synth_word(OTHER + ALSO_OTHER, rng=rng, f0=138))
        feed(hub, "phone", silence(1.6))
        assert recorder.wait_for(lambda r: len(r.commands) == 1)
        assert recorder.commands[0][0] == "phone"
        assert recorder.commands[0][1] == "what time is it"
        assert recorder.commands[0][2]["source"] == "assistant_mode"
        assert recorder.commands[0][2]["wake"] == "voice"
        assert len(recorder.transcribed) == 1
        assert recorder.wait_for(lambda r: ("idle", "submitted") in r.states())
        states = recorder.states()
        assert states[0] == ("listening", "wake_word")
        assert ("thinking", "transcribing") in states
    finally:
        hub.close()


def test_disabled_assistant_mode_never_listens():
    rng = np.random.default_rng(23)
    train_voice_model(rng)
    am.save_settings({"enabled": False, "wake_phrase": "Hey AutoYou"})
    recorder = Recorder(transcripts=["should not happen"])
    hub = make_hub(recorder)
    try:
        assert hub.listens_to(assistant_listening=True, silent_recording=True) is False
        feed(hub, "phone", with_noise(synth_word(WAKE, rng=rng), rng=rng))
        feed(hub, "phone", silence(2.0))
        assert recorder.wait_for(lambda r: hub._queue.empty(), timeout=5)
        time.sleep(0.1)
        assert recorder.events == [] and recorder.transcribed == []
        hub.client_wake("phone")
        time.sleep(0.2)
        assert recorder.events == []
    finally:
        hub.close()


def test_safety_recording_listening_follows_the_owner_choice():
    am.save_settings({"enabled": True, "listen_during_safety_recording": False})
    hub = make_hub(Recorder())
    try:
        assert hub.listens_to(assistant_listening=False, silent_recording=True) is False
        assert hub.listens_to(assistant_listening=True, silent_recording=False) is True
        am.save_settings({"listen_during_safety_recording": True})
        hub.reload()
        assert hub.listens_to(assistant_listening=False, silent_recording=True) is True
        assert hub.listens_to(assistant_listening=False, silent_recording=False) is False
    finally:
        hub.close()


def test_both_mode_drops_a_wake_the_transcript_does_not_confirm():
    rng = np.random.default_rng(24)
    train_voice_model(rng)
    am.save_settings({"enabled": True, "wake_phrase": "Hey AutoYou", "confirm": "both"})
    recorder = Recorder(transcripts=["hey everyone dinner is ready", "hey auto you turn off the lights"])
    hub = make_hub(recorder)
    try:
        for _ in range(2):
            feed(hub, "phone", with_noise(synth_word(WAKE, rng=rng, f0=133), rng=rng, pad_seconds=0.1))
            feed(hub, "phone", synth_word(OTHER, rng=rng))
            feed(hub, "phone", silence(2.5))
            time.sleep(0.3)
        assert recorder.wait_for(lambda r: len(r.commands) == 1)
        assert recorder.commands[0][1] == "turn off the lights"
        assert ("idle", "not_confirmed") in recorder.states()
    finally:
        hub.close()


def test_text_only_wake_word_discards_speech_without_the_phrase():
    rng = np.random.default_rng(25)
    am.save_settings({"enabled": True, "wake_phrase": "Jarvis", "confirm": "text"})
    recorder = Recorder(transcripts=["we should get lunch", "Jarvis, set a timer for ten minutes"])
    hub = make_hub(recorder)
    try:
        for _ in range(2):
            feed(hub, "phone", with_noise(synth_word(OTHER, rng=rng), rng=rng, pad_seconds=0.1))
            feed(hub, "phone", silence(1.0))
            assert recorder.wait_for(lambda r: hub._queue.empty() and len(r.transcribed) >= 1)
            time.sleep(0.2)
        assert recorder.wait_for(lambda r: len(r.commands) == 1)
        assert recorder.commands[0][1] == "set a timer for ten minutes"
        assert recorder.commands[0][2]["wake"] == "text"
        assert len(recorder.transcribed) == 2
    finally:
        hub.close()


def test_client_detected_wake_opens_listening_and_times_out_without_speech():
    am.save_settings({"enabled": True, "wake_phrase": "Jarvis", "confirm": "voice", "listen_seconds": 3})
    recorder = Recorder()
    hub = make_hub(recorder)
    try:
        hub.client_wake("laptop", score=0.8)
        feed(hub, "laptop", silence(3.5))
        assert recorder.wait_for(lambda r: ("idle", "no_speech") in r.states("laptop"))
        assert recorder.states("laptop")[0] == ("listening", "wake_word")
        assert recorder.transcribed == []
    finally:
        hub.close()


def test_only_one_device_answers_a_wake_word_heard_by_two():
    am.save_settings({"enabled": True, "wake_phrase": "Jarvis"})
    recorder = Recorder()
    hub = make_hub(recorder)
    try:
        hub.client_wake("phone")
        hub.client_wake("laptop")
        assert recorder.wait_for(lambda r: len(r.events) >= 2)
        assert recorder.states("phone")[0] == ("listening", "wake_word")
        assert recorder.states("laptop")[0] == ("idle", "answered_elsewhere")
    finally:
        hub.close()


def test_a_device_can_teach_the_wake_word_over_its_stream():
    rng = np.random.default_rng(26)
    am.save_settings({"enabled": True})
    recorder = Recorder()
    hub = make_hub(recorder, server_name="AutoYou-Server")
    try:
        hub.start_enrollment("phone", phrase="Hey AutoYou", count=3)
        feed(hub, "phone", silence(0.5))
        for index in range(3):
            word = synth_word(WAKE, rng=rng, f0=125 + 7 * index, stretch=0.95 + 0.05 * index)
            feed(hub, "phone", with_noise(word, rng=rng, pad_seconds=0.05))
            feed(hub, "phone", silence(0.8))
        assert recorder.wait_for(
            lambda r: any(p.get("event") == "assistant_enroll" and p.get("state") in {"done", "failed"} for _, p in r.events),
            timeout=15,
        )
        enroll_states = [p.get("state") for _, p in recorder.events if p.get("event") == "assistant_enroll"]
        assert enroll_states[0] == "recording"
        assert enroll_states.count("captured") == 3
        assert enroll_states[-1] == "done"
        assert am.load_model() is not None
        assert am.load_settings()["wake_phrase"] == "Hey AutoYou"
        assert am.public_status("AutoYou-Server")["ready"] is True
    finally:
        hub.close()


def test_feed_never_blocks_or_raises_when_the_queue_is_full(monkeypatch):
    monkeypatch.setattr(am, "FEED_QUEUE_MAX_ITEMS", 2)
    hub = make_hub(Recorder())
    hub._queue = am.queue.Queue(maxsize=2)
    hub._worker = threading.Thread(target=lambda: None)  # a worker that is not draining
    hub._worker.is_alive = lambda: True  # type: ignore[method-assign]
    for _ in range(10):
        hub.feed("phone", b"\x00\x00" * 320)
    assert hub.dropped_frames == 8


def test_wake_word_in_calls_is_opt_in_and_needs_assistant_mode():
    hub = make_hub(Recorder())
    try:
        assert hub.gates_calls is False
        am.save_settings({"enabled": False, "wake_word_in_calls": True})
        hub.reload()
        assert hub.gates_calls is False
        am.save_settings({"enabled": True})
        hub.reload()
        assert hub.gates_calls is True
        assert am.capability_payload("AutoYou-Server", available=True)["wake_word_in_calls"] is True
    finally:
        hub.close()
