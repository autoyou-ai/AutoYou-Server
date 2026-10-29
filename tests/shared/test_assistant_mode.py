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

import base64
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


def _run_device(hub, event_name, payload=None, *, streaming=False):
    import asyncio

    replies = []

    async def send(body):
        replies.append(body)

    asyncio.run(am.handle_device_control(
        hub, session_id="pi", event_name=event_name, payload=payload or {},
        server_name="AutoYou-Server", streaming=streaming, send=send,
    ))
    return replies


def _b64(pcm):
    import base64

    return base64.b64encode(ww.pcm16_to_wav_bytes(pcm)).decode()


def test_devices_get_the_model_only_when_it_is_ready_and_on():
    rng = np.random.default_rng(30)
    recorder = Recorder()
    hub = make_hub(recorder)
    try:
        assert _run_device(hub, "assistant_model_request")[0]["reason"] == "disabled"
        am.save_settings({"enabled": True, "wake_phrase": "Hey AutoYou"})
        hub.reload()
        first = _run_device(hub, "assistant_model_request")[0]
        assert first["event"] == "assistant_model" and first["available"] is False
        train_voice_model(rng)
        model = _run_device(hub, "assistant_model_request")[0]["model"]
        assert model["format"] == ww.MODEL_FORMAT and len(model["templates"]) == 3
        assert model["wake_phrase"] == "Hey AutoYou" and model["confirm"] == "voice"
        assert ww.WakeWordModel.from_dict(model).distance_threshold > 0
    finally:
        hub.close()


def test_a_device_can_teach_the_word_by_upload_without_streaming():
    rng = np.random.default_rng(31)
    am.save_settings({"enabled": True})
    hub = make_hub(Recorder())
    try:
        recordings = enroll(rng)
        states = []
        for index, pcm in enumerate(recordings):
            replies = _run_device(hub, "assistant_sample", {
                "audio_base64": _b64(pcm), "wake_phrase": "Hey AutoYou", "train": index == len(recordings) - 1,
            })
            states.extend(r["state"] for r in replies)
        assert states == ["captured", "captured", "captured", "done"]
        assert am.load_settings()["wake_phrase"] == "Hey AutoYou"
        assert am.public_status("AutoYou-Server")["ready"] is True
        rejected = _run_device(hub, "assistant_sample", {"audio_base64": _b64(ww.float_to_pcm16(silence(1.0)))})
        assert rejected[0]["state"] == "rejected"
    finally:
        hub.close()


def test_an_uploaded_utterance_after_an_on_device_wake_reaches_the_conversation():
    rng = np.random.default_rng(32)
    am.save_settings({"enabled": True, "wake_phrase": "Hey AutoYou", "confirm": "both"})
    recorder = Recorder(transcripts=["hey auto you dim the lights", "what a nice day"])
    hub = make_hub(recorder)
    try:
        audio = ww.float_to_pcm16(with_noise(synth_word(WAKE + OTHER, rng=rng), rng=rng))
        _run_device(hub, "assistant_utterance", {"audio_base64": _b64(audio), "score": 0.8})
        assert recorder.wait_for(lambda r: len(r.commands) == 1)
        assert recorder.commands[0][1] == "dim the lights"
        assert recorder.commands[0][2]["wake"] == "device"
        assert recorder.commands[0][2]["wake_score"] == 0.8
        # "both" still needs the phrase in the transcript of an uploaded clip.
        _run_device(hub, "assistant_utterance", {"pcm16_base64": __import__("base64").b64encode(audio).decode()})
        assert recorder.wait_for(lambda r: ("idle", "not_confirmed") in r.states("pi"))
        assert len(recorder.commands) == 1
        assert _run_device(hub, "assistant_utterance", {"audio_base64": "not base64!"})[0]["reason"] == "bad_audio"
    finally:
        hub.close()


def test_server_side_wakes_still_need_a_stream():
    am.save_settings({"enabled": True})
    hub = make_hub(Recorder())
    try:
        assert _run_device(hub, "assistant_wake")[0]["reason"] == "not_streaming"
        assert _run_device(hub, "assistant_enroll")[0]["state"] == "failed"
        assert _run_device(hub, "assistant_wake", streaming=True) == []
        assert am.capability_payload("AutoYou-Server", available=True)["client_detection"] is True
    finally:
        hub.close()


def test_live_frame_is_the_newest_fresh_camera_picture():
    from shared.assistant_mode import live_frame_attachment

    now = 1_000.0

    class Registry:
        def __init__(self, frames):
            self.frames = frames

        def latest(self, session_id):
            return self.frames.get(session_id)

    fresh = SimpleNamespace(timestamp_ms=int((now - 1) * 1000), jpeg_bytes=b"\xff\xd8synthetic", width=640, height=480)
    stale = SimpleNamespace(timestamp_ms=int((now - 10) * 1000), jpeg_bytes=b"\xff\xd8old", width=640, height=480)
    attachment = live_frame_attachment(["raw", "device"], registry=Registry({"device": fresh}), now=now)
    assert attachment["mimetype"] == "image/jpeg" and attachment["width"] == 640
    assert base64.b64decode(attachment["data"]) == b"\xff\xd8synthetic"
    assert live_frame_attachment(["device"], registry=Registry({"device": stale}), now=now) is None
    assert live_frame_attachment(["device"], registry=Registry({}), now=now) is None


def test_live_video_setting_defaults_on_and_saves(isolated_store):
    assert am.load_settings()["use_live_video"] is True
    am.save_settings({"use_live_video": False})
    assert am.load_settings()["use_live_video"] is False


def test_a_device_on_a_call_turns_assistant_mode_on_for_that_call_only():
    am.save_settings({"enabled": True, "wake_phrase": "Hey AutoYou"})
    hub = make_hub(Recorder())
    try:
        assert hub.gates_call("pi") is False
        reply = _run_device(hub, "assistant_call_mode", {"enabled": True})
        assert reply == [{"event": "assistant_call_mode", "enabled": True, "forced": False, "reason": "",
                          "wake_phrase": "Hey AutoYou"}]
        assert hub.gates_call("pi") is True and hub.gates_call("laptop") is False
        assert hub.gates_calls is False  # other calls still answer everything
        assert hub.snapshot()["call_mode_sessions"] == ["pi"]
        off = _run_device(hub, "assistant_call_mode", {"enabled": False})
        assert off[0]["enabled"] is False and hub.gates_call("pi") is False
        _run_device(hub, "assistant_call_mode", {"enabled": True})
        hub.end_session("pi")
        assert hub.gates_call("pi") is False
    finally:
        hub.close()


def test_call_mode_follows_the_owners_permissions():
    hub = make_hub(Recorder())
    try:
        assert _run_device(hub, "assistant_call_mode", {"enabled": True})[0]["reason"] == "disabled"
        am.save_settings({"enabled": True, "allow_call_toggle": False})
        hub.reload()
        refused = _run_device(hub, "assistant_call_mode", {"enabled": True})[0]
        assert refused["enabled"] is False and refused["reason"] == "not_allowed"
        assert hub.gates_call("pi") is False
        capability = am.capability_payload("AutoYou-Server", available=True)
        assert capability["call_toggle"] is False

        am.save_settings({"allow_call_toggle": True})
        hub.reload()
        assert am.capability_payload("AutoYou-Server", available=True)["call_toggle"] is True
        assert am.capability_payload("AutoYou-Server", available=False)["call_toggle"] is False
        _run_device(hub, "assistant_call_mode", {"enabled": True})
        assert hub.gates_call("pi") is True
        # Turning the permission off later stops the call's gate at once.
        am.save_settings({"allow_call_toggle": False})
        hub.reload()
        assert hub.gates_call("pi") is False

        am.save_settings({"wake_word_in_calls": True})
        hub.reload()
        forced = _run_device(hub, "assistant_call_mode", {"enabled": False})[0]
        assert forced["enabled"] is True and forced["forced"] is True
        assert hub.gates_call("anyone") is True
    finally:
        hub.close()


def test_device_teaching_can_be_left_to_this_computer():
    rng = np.random.default_rng(32)
    am.save_settings({"enabled": True, "allow_device_teaching": False})
    hub = make_hub(Recorder())
    try:
        pcm = enroll(rng)[0]
        refused = _run_device(hub, "assistant_sample", {"audio_base64": _b64(pcm), "train": True})
        assert refused == [{"event": "assistant_enroll", "state": "failed", "reason": "not_allowed"}]
        assert am.list_samples() == []
        streamed = _run_device(hub, "assistant_enroll", {"action": "start"}, streaming=True)
        assert streamed[0]["reason"] == "not_allowed"
        capability = am.capability_payload("AutoYou-Server", available=True)
        assert capability["device_samples"] is False and capability["enrollment"] is False
        assert capability["device_teaching"] is False
        # The model a device detects with is unaffected.
        assert _run_device(hub, "assistant_model_request")[0]["event"] == "assistant_model"
    finally:
        hub.close()


def test_permission_settings_default_to_letting_devices_help():
    settings = am.load_settings()
    assert settings["allow_call_toggle"] is True and settings["allow_device_teaching"] is True
    saved = am.save_settings({"allow_call_toggle": "off", "allow_device_teaching": 0})
    assert saved["allow_call_toggle"] is False and saved["allow_device_teaching"] is False
    status = am.public_status("AutoYou-Server")
    assert status["allow_call_toggle"] is False and status["allow_device_teaching"] is False
    assert set(am.PERMISSION_KEYS) <= set(am.DEFAULT_SETTINGS)


def _blocking_hub(recorder, release):
    def slow(pcm):
        release.wait(5)
        return recorder.transcribe(pcm)

    return am.AssistantModeHub(
        emit=recorder.emit, submit=recorder.submit, transcribe=slow, server_name=lambda: "AutoYou-Server"
    )


def _drain(hub, recorder):
    assert recorder.wait_for(lambda r: hub._queue.empty(), timeout=5)
    time.sleep(0.2)


def test_a_command_is_not_submitted_after_the_listener_was_cancelled():
    am.save_settings({"enabled": True, "wake_phrase": "Hey AutoYou"})
    words = ww.float_to_pcm16(silence(1.0))
    # Control: without a cancel the same command is handed over.
    release = threading.Event()
    recorder = Recorder(transcripts=["hey autoyou what time is it"])
    hub = _blocking_hub(recorder, release)
    try:
        hub.device_utterance("pi", words, score=0.9)
        release.set()
        assert recorder.wait_for(lambda r: r.commands, timeout=5)
        assert recorder.commands[0][1] == "what time is it"
    finally:
        hub.close()
    # Cancelled while it was being transcribed: nothing reaches the conversation.
    release = threading.Event()
    recorder = Recorder(transcripts=["hey autoyou what time is it"])
    hub = _blocking_hub(recorder, release)
    try:
        hub.device_utterance("pi", words, score=0.9)
        assert recorder.wait_for(lambda r: ("thinking", "transcribing") in r.states("pi"), timeout=5)
        hub.cancel("pi", "cancelled")
        _drain(hub, recorder)
        release.set()
        assert recorder.wait_for(lambda r: r.transcribed, timeout=5)
        _drain(hub, recorder)
        assert recorder.commands == []
    finally:
        hub.close()


def test_a_command_is_not_submitted_after_assistant_mode_was_turned_off():
    am.save_settings({"enabled": True, "wake_phrase": "Hey AutoYou"})
    release = threading.Event()
    recorder = Recorder(transcripts=["hey autoyou what time is it"])
    hub = _blocking_hub(recorder, release)
    try:
        hub.device_utterance("pi", ww.float_to_pcm16(silence(1.0)), score=0.9)
        assert recorder.wait_for(lambda r: ("thinking", "transcribing") in r.states("pi"), timeout=5)
        am.save_settings({"enabled": False})
        hub.reload()
        release.set()
        assert recorder.wait_for(lambda r: r.transcribed, timeout=5)
        _drain(hub, recorder)
        assert recorder.commands == []
    finally:
        hub.close()


@pytest.mark.parametrize("confirm", ["voice", "both"])
def test_voice_choices_transcribe_nothing_without_a_trained_word(confirm):
    rng = np.random.default_rng(41)
    am.save_settings({"enabled": True, "wake_phrase": "Hey AutoYou", "confirm": confirm})
    recorder = Recorder(transcripts=["hey autoyou what time is it"] * 4)
    hub = make_hub(recorder)
    try:
        assert hub.config().needs_training is True
        for formants in (OTHER, WAKE, ALSO_OTHER):
            feed(hub, "phone", with_noise(synth_word(formants, rng=rng), rng=rng))
            feed(hub, "phone", silence(1.5))
        _drain(hub, recorder)
        assert recorder.transcribed == []  # never transcribed, so never sent
        assert recorder.commands == []
        assert hub.session_state("phone") == "idle"
    finally:
        hub.close()
    # A model trained for another word does not count either.
    train_voice_model(rng, phrase="Hey Jarvis")
    hub = make_hub(Recorder())
    try:
        assert hub.config().needs_training is True
    finally:
        hub.close()


def test_devices_refetch_the_model_when_its_settings_change():
    rng = np.random.default_rng(42)
    am.save_settings({"enabled": True, "wake_phrase": "Hey AutoYou"})
    assert am.capability_payload("AutoYou-Server", available=True)["model_revision"] == ""
    train_voice_model(rng)
    first = am.capability_payload("AutoYou-Server", available=True)["model_revision"]
    assert first
    am.save_settings({"allow_call_toggle": False})
    assert am.capability_payload("AutoYou-Server", available=True)["model_revision"] == first
    am.save_settings({"sensitivity": 0.9})
    second = am.capability_payload("AutoYou-Server", available=True)["model_revision"]
    assert second != first
    am.save_settings({"listen_seconds": 12})
    third = am.capability_payload("AutoYou-Server", available=True)["model_revision"]
    assert third not in {first, second}
    assert am.device_model_payload("AutoYou-Server")["sensitivity"] == 0.9
    assert am.device_model_payload("AutoYou-Server")["listen_seconds"] == 12


def test_one_device_answers_even_when_another_uploads_seconds_later(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(am, "time", SimpleNamespace(monotonic=lambda: clock["now"], time=time.time))
    am.save_settings({"enabled": True, "wake_phrase": "Hey AutoYou"})
    recorder = Recorder(transcripts=["hey autoyou what time is it"] * 4)
    hub = make_hub(recorder)
    try:
        # The phone streams: the server heard the word just now.
        hub.client_wake("phone", score=0.9)
        assert recorder.wait_for(lambda r: ("listening", "wake_word") in r.states("phone"), timeout=5)
        # The Pi heard the same words itself and uploads 4.5 s of them 4 s later.
        clock["now"] = 104.0
        hub.device_utterance("pi", ww.float_to_pcm16(silence(4.5)), score=0.9)
        assert recorder.wait_for(lambda r: ("idle", "answered_elsewhere") in r.states("pi"), timeout=5)
        # A separate request a while later is answered.
        clock["now"] = 130.0
        hub.device_utterance("pi", ww.float_to_pcm16(silence(2.0)), score=0.9)
        assert recorder.wait_for(lambda r: any(sid == "pi" for sid, _, _ in r.commands), timeout=5)
    finally:
        hub.close()
