# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Background Mode sessions must still own a dedicated outbound media lane.

`voice_pipeline_enabled` is False for every offer that carries an active
`background_audio` state, because Background Mode negotiates a receive-only
keepalive rather than a live voice pipeline. Both offer handlers still build an
`AudioManager` for those sessions, and both must register the two outbound lanes
(`tts_track` for spoken replies, `playback_track` for file/media playback).

Without the media lane, `AudioManager._get_playback_track()` falls back to the
speech lane, so an agent-started song lands on the TTS queue - where VAD
barge-in, `stop_tts` and background-audio suppression all cancel it. See
`tests/server/media/test_audio_manager_playback_lane.py` for the other half.
"""

import ast

from tests.support.paths import REPO_ROOT, ensure_repo_on_path

ensure_repo_on_path()

import server

ENGINE = REPO_ROOT / "core_server" / "webrtc_engine.py"
OFFER_HANDLERS = ("handle_session_offer", "handle_autopair_offer")


def _handler_node(name: str) -> ast.AST:
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {ENGINE}")


def _guarded_by_voice_pipeline(node: ast.AST, target: str) -> bool:
    """True when every call to `target` sits under `if voice_pipeline_enabled`."""
    found = False
    for sub in ast.walk(node):
        if not isinstance(sub, ast.If):
            continue
        test = sub.test
        if not (isinstance(test, ast.Name) and test.id == "voice_pipeline_enabled"):
            continue
        for inner in ast.walk(ast.Module(body=sub.body, type_ignores=[])):
            if isinstance(inner, ast.Attribute) and inner.attr == target:
                found = True
    return found


def _calls(node: ast.AST, target: str) -> int:
    return sum(
        1
        for sub in ast.walk(node)
        if isinstance(sub, ast.Attribute) and sub.attr == target
    )


def test_both_offer_handlers_register_outbound_lanes() -> None:
    for name in OFFER_HANDLERS:
        node = _handler_node(name)
        assert _calls(node, "_ensure_audio_manager_outbound_tracks") >= 1, name


def test_lane_registration_is_not_gated_on_voice_pipeline_enabled() -> None:
    # voice_pipeline_enabled is False whenever background_offer_state["active"]
    # is True, so gating on it would leave Background Mode sessions without a
    # media lane for the entire connection.
    for name in OFFER_HANDLERS:
        node = _handler_node(name)
        assert not _guarded_by_voice_pipeline(
            node, "_ensure_audio_manager_outbound_tracks"
        ), name


def test_set_playback_track_is_not_gated_on_voice_pipeline_enabled() -> None:
    for name in OFFER_HANDLERS:
        node = _handler_node(name)
        assert not _guarded_by_voice_pipeline(node, "set_playback_track"), name
        assert not _guarded_by_voice_pipeline(node, "set_tts_track"), name


def test_ensure_tracks_registers_a_distinct_lane_per_role() -> None:
    class FakeTrack:
        readyState = "live"

    class FakeManager:
        def __init__(self):
            self.tts_track = None
            self.playback_track = None

        def set_tts_track(self, track):
            self.tts_track = track

        def set_playback_track(self, track):
            self.playback_track = track

    manager = FakeManager()
    cfg = server._default_config()

    tts_track, playback_track = server._ensure_audio_manager_outbound_tracks(
        audio_manager=manager,
        cfg=cfg,
    )

    assert tts_track is not None
    assert playback_track is not None
    assert tts_track is not playback_track
    assert manager.tts_track is tts_track
    assert manager.playback_track is playback_track

    # A second pass reuses the live lanes instead of orphaning them.
    again = server._ensure_audio_manager_outbound_tracks(audio_manager=manager, cfg=cfg)
    assert again == (tts_track, playback_track)
