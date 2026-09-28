# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Runtime audio-transceiver direction changes must actually take effect.

aiortc latches `sender._enabled` / `receiver._enabled` inside
`RTCRtpTransceiver._setCurrentDirection`, which only runs while applying a local
or remote description. Assigning `transceiver.direction` after the one-shot
offer/answer therefore only records a *desired* direction for a renegotiation
that never happens: the live sender keeps dropping every frame it pulls off the
track (`RTCRtpSender._next_encoded_frame` returns None when `_enabled` is False)
and the live receiver keeps dropping inbound RTP.

A Background Mode session whose answer narrowed the audio m-line would then stay
silent (no music/TTS) or deaf (no microphone) for the whole call that follows
`call_state(active=true)`, with no renegotiation available to repair it.
"""

from types import SimpleNamespace

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server


class FakeTransceiver:
    """Mimics aiortc's negotiation-latched direction handling."""

    def __init__(self, *, current_direction, offer_direction="sendrecv"):
        self.direction = current_direction
        self.currentDirection = current_direction
        self._offerDirection = offer_direction
        self.sender = SimpleNamespace(_enabled=None, replaceTrack=lambda track: None)
        self.receiver = SimpleNamespace(_enabled=None)
        self.applied = []
        self._apply(current_direction)

    def _apply(self, direction):
        self.currentDirection = direction
        self.sender._enabled = direction in ("sendonly", "sendrecv")
        self.receiver._enabled = direction in ("recvonly", "sendrecv")

    def _setCurrentDirection(self, direction):
        self.applied.append(direction)
        self._apply(direction)


def _webrtc():
    return server.WebRTCManager()


# ── direction intersection ───────────────────────────────────────────────────

def test_direction_intersection_matches_aiortc_bitmap() -> None:
    intersect = server.WebRTCManager._intersect_media_directions
    assert intersect("sendrecv", "sendrecv") == "sendrecv"
    assert intersect("sendrecv", "recvonly") == "recvonly"
    assert intersect("sendrecv", "sendonly") == "sendonly"
    assert intersect("sendrecv", "inactive") == "inactive"
    assert intersect("sendonly", "recvonly") == "inactive"
    assert intersect("bogus", "sendrecv") == "inactive"


# ── the repair itself ────────────────────────────────────────────────────────

def test_restoring_sendrecv_reenables_a_narrowed_sender_and_receiver() -> None:
    webrtc = _webrtc()
    transceiver = FakeTransceiver(current_direction="inactive")
    assert transceiver.sender._enabled is False
    assert transceiver.receiver._enabled is False

    webrtc._set_transceiver_direction(
        transceiver, "sendrecv", session_id="session-1", reason="call-restore"
    )

    assert transceiver.direction == "sendrecv"
    assert transceiver.currentDirection == "sendrecv"
    assert transceiver.sender._enabled is True
    assert transceiver.receiver._enabled is True


def test_recvonly_answer_regains_the_microphone_when_a_call_starts() -> None:
    # A client that offered recvonly for Background Mode gets a sendonly answer,
    # which leaves the server unable to hear the mic once the call begins.
    webrtc = _webrtc()
    transceiver = FakeTransceiver(current_direction="sendonly")
    assert transceiver.receiver._enabled is False

    webrtc._set_transceiver_direction(
        transceiver, "sendrecv", session_id="session-1", reason="call-restore"
    )

    assert transceiver.receiver._enabled is True
    assert transceiver.sender._enabled is True


def test_direction_is_clamped_to_what_the_peer_offered() -> None:
    # The peer offered recvonly, so the server must never promote itself past
    # what was negotiated even though the call wants full duplex.
    webrtc = _webrtc()
    transceiver = FakeTransceiver(current_direction="inactive", offer_direction="recvonly")

    webrtc._set_transceiver_direction(
        transceiver, "sendrecv", session_id="session-1", reason="call-restore"
    )

    assert transceiver.currentDirection == "recvonly"
    assert transceiver.sender._enabled is False
    assert transceiver.receiver._enabled is True


def test_no_op_when_the_effective_direction_is_unchanged() -> None:
    webrtc = _webrtc()
    transceiver = FakeTransceiver(current_direction="sendrecv")
    transceiver.applied.clear()

    webrtc._set_transceiver_direction(
        transceiver, "sendrecv", session_id="session-1", reason="idle"
    )

    assert transceiver.applied == []
    assert transceiver.sender._enabled is True


def test_pre_negotiation_transceiver_is_left_to_the_offer_answer_flow() -> None:
    webrtc = _webrtc()
    transceiver = FakeTransceiver(current_direction="sendrecv")
    transceiver.currentDirection = None
    transceiver.applied.clear()

    webrtc._set_transceiver_direction(
        transceiver, "recvonly", session_id="session-1", reason="background-audio"
    )

    # Desired direction recorded for the answer; enable flags untouched.
    assert transceiver.direction == "recvonly"
    assert transceiver.applied == []


def test_background_suppression_narrows_the_live_direction() -> None:
    webrtc = _webrtc()
    transceiver = FakeTransceiver(current_direction="sendrecv")
    webrtc.audio_transceivers["session-1"] = transceiver

    webrtc._suppress_outbound_audio_for_background(
        "session-1",
        silent_recording=True,
        server_audio_direction="recvonly",
    )

    assert transceiver.currentDirection == "recvonly"
    assert transceiver.sender._enabled is False
    assert transceiver.receiver._enabled is True


# ── Safety Recording must never lose the receiver ────────────────────────────

def test_safety_recording_keeps_the_receiver_enabled() -> None:
    # Safety Recording is send-only *from the phone*: the server has to keep
    # receiving that microphone RTP to write the rotating WAV batches. Making
    # runtime direction changes effective must never take that away.
    webrtc = _webrtc()
    transceiver = FakeTransceiver(current_direction="sendrecv")
    webrtc.audio_transceivers["session-1"] = transceiver

    webrtc._suppress_outbound_audio_for_background(
        "session-1",
        silent_recording=True,
        # The value the background_audio_state handler computes for recording.
        server_audio_direction="sendrecv",
    )

    assert transceiver.receiver._enabled is True
    # Nothing is sent down to the phone while it is only recording.
    assert getattr(transceiver.sender, "track", None) is None


def test_safety_recording_fallback_direction_still_receives() -> None:
    # Defensive: even without an explicit direction the recording mode resolves
    # to recvonly, which keeps the server's receiver alive.
    webrtc = _webrtc()
    transceiver = FakeTransceiver(current_direction="sendrecv")
    webrtc.audio_transceivers["session-1"] = transceiver

    webrtc._suppress_outbound_audio_for_background(
        "session-1",
        silent_recording=True,
        server_audio_direction=None,
    )

    assert transceiver.currentDirection == "recvonly"
    assert transceiver.receiver._enabled is True


def test_plain_background_keepalive_keeps_sending_the_heartbeat() -> None:
    # Background Mode without recording: server sends the keepalive heartbeat and
    # the phone's microphone stays closed. The receiver may be enabled; the
    # inbound chunk handler drops those frames while background state is active.
    webrtc = _webrtc()
    transceiver = FakeTransceiver(current_direction="sendrecv")
    webrtc.audio_transceivers["session-1"] = transceiver

    webrtc._suppress_outbound_audio_for_background(
        "session-1",
        silent_recording=False,
        server_audio_direction="sendrecv",
    )

    assert transceiver.currentDirection == "sendrecv"
    assert transceiver.sender._enabled is True


def test_clearing_idle_outbound_audio_keeps_the_m_line_usable() -> None:
    webrtc = _webrtc()
    transceiver = FakeTransceiver(current_direction="inactive")
    webrtc.audio_transceivers["session-1"] = transceiver

    webrtc._clear_outbound_audio_track_for_idle("session-1")

    assert transceiver.currentDirection == "sendrecv"
    assert transceiver.sender._enabled is True
    assert transceiver.receiver._enabled is True


def test_invalid_direction_falls_back_to_inactive() -> None:
    webrtc = _webrtc()
    transceiver = FakeTransceiver(current_direction="sendrecv")

    webrtc._set_transceiver_direction(
        transceiver, "garbage", session_id="session-1", reason="test"
    )

    assert transceiver.direction == "inactive"
    assert transceiver.currentDirection == "inactive"


def test_transceiver_without_the_private_hook_still_gets_its_direction() -> None:
    webrtc = _webrtc()
    transceiver = SimpleNamespace(direction="inactive", currentDirection="inactive")

    webrtc._set_transceiver_direction(
        transceiver, "sendrecv", session_id="session-1", reason="call-restore"
    )

    assert transceiver.direction == "sendrecv"
