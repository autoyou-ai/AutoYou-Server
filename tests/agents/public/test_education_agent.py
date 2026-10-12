# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-to-11b8cc7fc6f2295d67caf4e4

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import importlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-to-11b8cc7fc6f2295d67caf4e4"


education_backend = importlib.import_module("autoyou_agents.education_agent.website.backend.app")
EDUCATION_FRONTEND = (
    Path(__file__).resolve().parents[3]
    / "autoyou_agents"
    / "education_agent"
    / "website"
    / "frontend"
)


def _auth(authenticated: bool = True):
    return lambda _request, agent_name: {
        "authenticated": authenticated,
        "agent_name": agent_name,
        "via": "test",
    }


def test_education_status_requires_auth(monkeypatch):
    monkeypatch.setattr(education_backend, "_describe_chat_auth_state", _auth(False))

    client = TestClient(education_backend.app)
    response = client.get("/api/education/status")

    assert response.status_code == 401
    assert response.json()["success"] is False


def test_education_status_returns_synthetic_webrtc_snapshot(monkeypatch, tmp_path):
    video_dir = tmp_path / "video-recordings"
    silent_dir = tmp_path / "silent-recordings"
    video_dir.mkdir()
    silent_dir.mkdir()
    (video_dir / "synthetic-session.mp4").write_bytes(b"synthetic-video")
    (silent_dir / "synthetic-session.wav").write_bytes(b"synthetic-audio")

    class FakeRecorder:
        def status(self):
            return {
                "session_id": "session-alpha",
                "output_dir": str(silent_dir),
                "current_path": str(silent_dir / "synthetic-session.wav"),
                "bytes_written": 32,
            }

    fake_video_sink = SimpleNamespace(
        recording_path=str(video_dir / "synthetic-video-session"),
        recording_format="mp4_video",
        recording_enabled=True,
    )
    fake_webrtc = SimpleNamespace(
        session_peers={"session-alpha": object()},
        audio_sinks={"session-alpha": object()},
        video_sinks={"session-alpha": fake_video_sink},
        desktop_video_tracks={},
        datachannel_managers={"session-alpha": object()},
        voice_call_status_by_session={"session-alpha": {"platform": "ios", "timestamp_ms": 123000}},
        voice_call_playback_by_session={},
        voice_call_client_active_by_session={"session-alpha": True},
        background_audio_state_by_session={"session-alpha": {"active": True, "silent_recording": True}},
        silent_recorders={"session-alpha": FakeRecorder()},
        pending_voice_chat_messages={},
        client_display_name_snapshot=lambda _session_id: {
            "owner_key": "local:synthetic-education-client",
            "client_display_name": "  Synthetic Study Circle  ",
            "reported_client_display_name": "  Synthetic Study Circle  ",
            "server_name_override": "",
        },
        streaming_event_snapshot=lambda limit=100: [
            {
                "id": "event-1",
                "timestamp_ms": 123000,
                "session_id": "session-alpha",
                "direction": "inbound",
                "channel": "voice",
                "text": "Synthetic hello",
            }
        ],
    )

    fake_config = {
        "video_call": {
            "recording_dir": str(video_dir),
            "silent_recording_dir": str(silent_dir),
        }
    }
    fake_server = SimpleNamespace(
        STATE=SimpleNamespace(config=fake_config, service_manager=None),
        WEBRTC=fake_webrtc,
        _default_config=lambda: fake_config,
        _build_webrtc_capabilities=lambda cfg=None, include_admin=False: {
            "audio": {"enabled": True, "agent_processing_enabled": True},
            "video": {"receive_enabled": True, "record_my_video": True},
            "outbound_video": {"source": "remote_desktop"},
        },
    )

    class FakeFrameRegistry:
        def status(self):
            return {
                "active": True,
                "active_sessions": 1,
                "latest_session_id": "session-alpha",
                "latest_sequence": 7,
                "latest_timestamp_ms": 123000,
                "latest_width": 640,
                "latest_height": 360,
            }

        def latest(self, session_id=None):
            return SimpleNamespace(
                session_id=session_id or "session-alpha",
                sequence=7,
                timestamp_ms=123000,
                width=640,
                height=360,
                source="ios",
                jpeg_bytes=b"\xff\xd8\xff\xd9",
            )

    monkeypatch.setattr(education_backend, "_describe_chat_auth_state", _auth(True))
    monkeypatch.setattr(education_backend, "_runtime_server", lambda: fake_server)
    monkeypatch.setattr(education_backend, "VIDEO_FRAME_REGISTRY", FakeFrameRegistry())

    client = TestClient(education_backend.app)
    response = client.get("/api/education/status")
    payload = response.json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["webrtc"]["session_count"] == 1
    session = payload["webrtc"]["sessions"][0]
    assert session["session_id"] == "session-alpha"
    assert session["owner_key"] == "local:synthetic-education-client"
    assert session["client_display_name"] == "  Synthetic Study Circle  "
    assert session["datachannel_connected"] is True
    assert session["voice_call_active"] is True
    assert session["video_recording"]["enabled"] is True
    assert payload["recent_events"][0]["text"] == "Synthetic hello"
    assert payload["recordings"]["active_silent_recorders"][0]["bytes_written"] == 32
    assert payload["recordings"]["video_recordings"][0]["name"] == "synthetic-session.mp4"


def test_education_client_name_update_requires_auth(monkeypatch):
    monkeypatch.setattr(education_backend, "_describe_chat_auth_state", _auth(False))

    response = TestClient(education_backend.app).put(
        "/api/education/client-name",
        json={"owner_key": "local:synthetic-education-client", "client_display_name": "Synthetic Learner"},
    )

    assert response.status_code == 401
    assert response.json()["success"] is False


def test_education_client_name_update_uses_authenticated_server_control(monkeypatch):
    captured = {}

    def set_name(owner_key, client_display_name):
        captured["owner_key"] = owner_key
        captured["client_display_name"] = client_display_name
        return {
            "owner_key": owner_key,
            "client_display_name": client_display_name,
            "history_stored": False,
        }

    monkeypatch.setattr(education_backend, "_describe_chat_auth_state", _auth(True))
    monkeypatch.setattr(
        education_backend,
        "_runtime_server",
        lambda: SimpleNamespace(_set_client_name_override=set_name),
    )

    response = TestClient(education_backend.app).put(
        "/api/education/client-name",
        json={
            "owner_key": "local:synthetic-education-client",
            "client_display_name": "  Synthetic Learner  ",
        },
    )

    assert response.status_code == 200
    assert captured == {
        "owner_key": "local:synthetic-education-client",
        "client_display_name": "  Synthetic Learner  ",
    }
    assert response.json()["client_identity"]["history_stored"] is False


def test_education_status_dedupes_audio_video_aliases_without_losing_frames(monkeypatch):
    fake_video_sink = SimpleNamespace(
        recording_path="",
        recording_format="mp4_video",
        recording_enabled=False,
    )
    fake_webrtc = SimpleNamespace(
        session_peers={},
        audio_sinks={"audio-relay-123": object()},
        video_sinks={"video-relay-123": fake_video_sink},
        desktop_video_tracks={},
        datachannel_managers={"audio-relay-123": object(), "video-relay-123": object()},
        voice_call_status_by_session={"audio-relay-123": {"platform": "ios", "timestamp_ms": 123000}},
        voice_call_playback_by_session={},
        voice_call_client_active_by_session={"audio-relay-123": True},
        background_audio_state_by_session={},
        silent_recorders={},
        pending_voice_chat_messages={},
        _datachannel_connection_key=lambda session_id: "owner:ios-client-123",
        streaming_event_snapshot=lambda limit=100: [
            {
                "id": "event-1",
                "timestamp_ms": 123000,
                "session_id": "video-relay-123",
                "direction": "inbound",
                "channel": "video",
                "text": "Synthetic frame event",
            }
        ],
    )
    fake_server = SimpleNamespace(
        STATE=SimpleNamespace(config={"video_call": {}}, service_manager=None),
        WEBRTC=fake_webrtc,
        _default_config=lambda: {"video_call": {}},
        _build_webrtc_capabilities=lambda cfg=None, include_admin=False: {},
    )

    class FakeFrameRegistry:
        def latest(self, session_id=None):
            if session_id != "video-relay-123":
                return None
            return SimpleNamespace(
                session_id="video-relay-123",
                sequence=7,
                timestamp_ms=123000,
                width=640,
                height=360,
                source="ios",
                jpeg_bytes=b"\xff\xd8\xff\xd9",
            )

    monkeypatch.setattr(education_backend, "_describe_chat_auth_state", _auth(True))
    monkeypatch.setattr(education_backend, "_runtime_server", lambda: fake_server)
    monkeypatch.setattr(education_backend, "VIDEO_FRAME_REGISTRY", FakeFrameRegistry())

    client = TestClient(education_backend.app)
    payload = client.get("/api/education/status").json()

    assert payload["webrtc"]["session_count"] == 1
    session = payload["webrtc"]["sessions"][0]
    assert session["session_id"] == "audio-relay-123"
    assert session["session_aliases"] == ["audio-relay-123", "video-relay-123"]
    assert session["audio_active"] is True
    assert session["video_active"] is True
    assert session["latest_video_frame"]["session_id"] == "video-relay-123"
    assert payload["recent_events"][0]["text"] == "Synthetic frame event"

    response = client.get("/api/education/frame.jpg?session_id=audio-relay-123")
    assert response.status_code == 200
    assert response.headers["x-autoyou-frame-session"] == "video-relay-123"
    assert response.content == b"\xff\xd8\xff\xd9"


def test_education_frame_endpoint_returns_latest_jpeg(monkeypatch):
    fake_webrtc = SimpleNamespace(
        session_peers={"session-alpha": object()},
        audio_sinks={},
        video_sinks={"session-alpha": object()},
        desktop_video_tracks={},
        datachannel_managers={},
        voice_call_status_by_session={},
        voice_call_playback_by_session={},
        voice_call_client_active_by_session={},
        background_audio_state_by_session={},
        silent_recorders={},
        pending_voice_chat_messages={},
    )
    fake_server = SimpleNamespace(WEBRTC=fake_webrtc)

    class FakeFrameRegistry:
        def latest(self, session_id=None):
            return SimpleNamespace(
                session_id=session_id or "session-alpha",
                sequence=11,
                timestamp_ms=456000,
                width=2,
                height=2,
                source="ios",
                jpeg_bytes=b"\xff\xd8\xff\xd9",
            )

    monkeypatch.setattr(education_backend, "_describe_chat_auth_state", _auth(True))
    monkeypatch.setattr(education_backend, "_runtime_server", lambda: fake_server)
    monkeypatch.setattr(education_backend, "VIDEO_FRAME_REGISTRY", FakeFrameRegistry())

    client = TestClient(education_backend.app)
    response = client.get("/api/education/frame.jpg?session_id=session-alpha")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-autoyou-frame-sequence"] == "11"
    assert response.content == b"\xff\xd8\xff\xd9"


def test_computer_video_status_and_preview_use_one_live_outbound_track(monkeypatch):
    class FakeImage:
        width = 4
        height = 2

        def convert(self, mode):
            assert mode == "RGB"
            return self

        def save(self, buffer, *, format, quality):
            assert format == "JPEG"
            assert quality == 82
            buffer.write(b"\xff\xd8synthetic-computer-video\xff\xd9")

    class FakeTrack:
        def __init__(self, sources):
            self.is_enabled = True
            self.readyState = "live"
            self.sources = sources
            self.capture_calls = 0

        def source_names(self):
            return list(self.sources)

        def capture_preview_image(self, *, max_width):
            self.capture_calls += 1
            assert max_width == 960
            return FakeImage()

    track = FakeTrack(["remote_desktop", "camera"])
    fake_webrtc = SimpleNamespace(
        desktop_video_tracks={"synthetic-session": track, "synthetic-alias": track},
    )
    capabilities = {
        "audio": {},
        "video": {},
        "outbound_video": {
            "enabled": True,
            "source": "stitched",
            "configured_source": "stitched",
            "sources": ["remote_desktop", "camera"],
            "active_sources": ["remote_desktop", "camera"],
        },
    }
    fake_server = SimpleNamespace(
        STATE=SimpleNamespace(config={"video_call": {}}, service_manager=None),
        WEBRTC=fake_webrtc,
        _default_config=lambda: {"video_call": {}},
        _build_webrtc_capabilities=lambda cfg=None, include_admin=False: capabilities,
    )
    monkeypatch.setattr(education_backend, "_describe_chat_auth_state", _auth(True))
    monkeypatch.setattr(education_backend, "_runtime_server", lambda: fake_server)

    client = TestClient(education_backend.app)
    payload = client.get("/api/education/status").json()
    computer_video = payload["computer_video"]

    assert computer_video["label"] == "Computer video"
    assert computer_video["configured_sources"] == ["remote_desktop", "camera"]
    assert computer_video["active_sources"] == ["remote_desktop", "camera"]
    assert computer_video["active"] is True
    assert computer_video["preview_available"] is True
    assert computer_video["enabled_track_count"] == 1
    assert payload["self_video"] == computer_video
    assert track.capture_calls == 0

    response = client.get("/api/education/computer-frame.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-autoyou-frame-source"] == "stitched"
    assert response.headers["x-autoyou-frame-width"] == "4"
    assert response.headers["x-autoyou-frame-height"] == "2"
    assert response.content == b"\xff\xd8synthetic-computer-video\xff\xd9"
    assert track.capture_calls == 1

    track.is_enabled = False
    assert client.get("/api/education/computer-frame.jpg").status_code == 404
    assert track.capture_calls == 1

    track.is_enabled = True
    second_track = FakeTrack(["remote_desktop"])
    fake_webrtc.desktop_video_tracks["second-session"] = second_track
    ambiguous = client.get("/api/education/status").json()["computer_video"]
    assert ambiguous["active"] is False
    assert ambiguous["preview_available"] is False
    assert ambiguous["enabled_track_count"] == 2
    assert client.get("/api/education/computer-frame.jpg").status_code == 404
    assert track.capture_calls == 1
    assert second_track.capture_calls == 0


def test_education_status_ignores_stale_non_live_session_keys(monkeypatch):
    fake_webrtc = SimpleNamespace(
        session_peers={},
        audio_sinks={},
        video_sinks={},
        desktop_video_tracks={},
        datachannel_managers={},
        voice_call_status_by_session={"stale-session": {"timestamp_ms": 123000}},
        voice_call_playback_by_session={"stale-session": {"state": "done"}},
        voice_call_client_active_by_session={"stale-session": False},
        background_audio_state_by_session={"stale-session": {"active": False}},
        silent_recorders={},
        pending_voice_chat_messages={"stale-session": ["queued"]},
        streaming_event_snapshot=lambda limit=100: [
            {
                "id": "old-event",
                "timestamp_ms": 123000,
                "session_id": "stale-session",
                "direction": "inbound",
                "channel": "chat",
                "text": "Old message",
            }
        ],
    )
    fake_server = SimpleNamespace(
        STATE=SimpleNamespace(config={"video_call": {}}, service_manager=None),
        WEBRTC=fake_webrtc,
        _default_config=lambda: {"video_call": {}},
        _build_webrtc_capabilities=lambda cfg=None, include_admin=False: {},
    )

    class StaleFrameRegistry:
        def latest(self, session_id=None):
            return SimpleNamespace(
                session_id=session_id or "stale-session",
                sequence=9,
                timestamp_ms=123000,
                width=640,
                height=360,
                jpeg_bytes=b"\xff\xd8\xff\xd9",
            )

    monkeypatch.setattr(education_backend, "_describe_chat_auth_state", _auth(True))
    monkeypatch.setattr(education_backend, "_runtime_server", lambda: fake_server)
    monkeypatch.setattr(education_backend, "VIDEO_FRAME_REGISTRY", StaleFrameRegistry())

    client = TestClient(education_backend.app)
    payload = client.get("/api/education/status").json()

    assert payload["webrtc"]["session_count"] == 0
    assert payload["webrtc"]["sessions"] == []
    assert payload["recent_events"] == []
    assert payload["video_frames"]["active"] is False
    assert client.get("/api/education/frame.jpg?session_id=stale-session").status_code == 404


def test_education_status_reads_synthetic_session_db(monkeypatch, tmp_path):
    db_path = tmp_path / "session.db"
    event_data = {
        "actions": {
            "state_delta": {
                "event_data_raw": {
                    "user_message": "Synthetic inbound message",
                    "agent_response": "Synthetic outbound response",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "memory_metadata": {
                        "client_display_name": "  Synthetic History Learner  ",
                    },
                }
            }
        },
        "timestamp": 1767225600,
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE events (
                id TEXT,
                app_name TEXT,
                user_id TEXT,
                session_id TEXT,
                invocation_id TEXT,
                timestamp TEXT,
                event_data TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO events (id, app_name, user_id, session_id, invocation_id, timestamp, event_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt-1",
                "autoyou_agents",
                "synthetic-user",
                "synthetic-session",
                "inv-1",
                "2026-01-01 00:00:00.000000",
                json.dumps(event_data),
            ),
        )

    fake_webrtc = SimpleNamespace(
        session_peers={},
        audio_sinks={},
        video_sinks={},
        desktop_video_tracks={},
        datachannel_managers={},
        voice_call_status_by_session={},
        voice_call_playback_by_session={},
        voice_call_client_active_by_session={},
        background_audio_state_by_session={},
        silent_recorders={},
        pending_voice_chat_messages={},
        streaming_event_snapshot=lambda limit=100: [],
    )
    fake_config = {"video_call": {}}
    fake_server = SimpleNamespace(
        STATE=SimpleNamespace(config=fake_config, service_manager=None),
        WEBRTC=fake_webrtc,
        _default_config=lambda: fake_config,
        _build_webrtc_capabilities=lambda cfg=None, include_admin=False: {},
        _resolve_ai_agent_storage_uris=lambda: {
            "session_service_uri": f"sqlite+aiosqlite:///{db_path.as_posix()}"
        },
    )

    class EmptyFrameRegistry:
        def status(self):
            return {"active": False, "active_sessions": 0}

        def latest(self, session_id=None):
            return None

    monkeypatch.setattr(education_backend, "_describe_chat_auth_state", _auth(True))
    monkeypatch.setattr(education_backend, "_runtime_server", lambda: fake_server)
    monkeypatch.setattr(education_backend, "VIDEO_FRAME_REGISTRY", EmptyFrameRegistry())

    client = TestClient(education_backend.app)
    payload = client.get("/api/education/status").json()
    texts = [item["text"] for item in payload["session_db"]["recent_messages"]]
    client_names = [item.get("client_display_name") for item in payload["session_db"]["recent_messages"]]

    assert payload["session_db"]["available"] is True
    assert "Synthetic inbound message" in texts
    assert "Synthetic outbound response" in texts
    assert "  Synthetic History Learner  " in client_names


def test_education_frontend_has_accessible_focused_video_controls():
    html = (EDUCATION_FRONTEND / "index.html").read_text(encoding="utf-8")
    javascript = (EDUCATION_FRONTEND / "assets" / "index.js").read_text(encoding="utf-8")
    stylesheet = (EDUCATION_FRONTEND / "assets" / "index.css").read_text(encoding="utf-8")

    assert 'id="video-viewer"' in html
    assert 'role="group" aria-label="Video view controls"' in html
    assert 'role="toolbar"' not in html
    assert 'role="region" aria-label="Focused video frame"' in html
    assert 'aria-labelledby="video-viewer-title" aria-modal="true"' in html
    assert 'aria-label="Zoom level" aria-live="polite"' in html
    assert 'aria-label="Full screen" aria-pressed="false"' in html
    assert 'aria-label="Fill frame" aria-pressed="false"' in html
    assert 'data-viewer-key="self" role="button" tabindex="0"' in html
    assert 'id="client-name-note"' in html
    for control_id in (
        "viewer-fit-button",
        "viewer-zoom-out",
        "viewer-zoom-in",
        "viewer-reset",
        "viewer-fullscreen",
        "viewer-close",
    ):
        assert f'id="{control_id}"' in html

    assert 'object-fit: contain;' in stylesheet
    assert 'height: 320px;' in stylesheet
    assert '.feed-card.expanded .feed-media' in stylesheet
    assert '@media (min-width: 681px) and (max-width: 1180px)' in stylesheet
    assert 'aspect-ratio: 16 / 9;' in stylesheet
    assert 'min-height: 44px;' in stylesheet
    assert 'prefers-reduced-motion: reduce' in stylesheet
    assert '.feed-media[tabindex]:focus-visible' in stylesheet
    assert 'outline-offset: -3px;' in stylesheet
    assert 'addEventListener("pointerdown", viewerPointerDown)' in javascript
    assert 'addEventListener("keydown", viewerKeyDown)' in javascript
    assert "findFeedTrigger(focusedFeedKey)" in javascript
    assert "findFeedTrigger(closedFeedKey)" in javascript
    assert "focus({ preventScroll: true })" in javascript
    assert "clearLiveVideo(\"Session locked\")" in javascript
    assert "Date.now() - state.lastSnapshotAt >= 5000" in javascript
    assert 'clearLiveVideo("Signed out")' in javascript
    assert 'event.key === "Escape" && state.viewer.key && dialog.hasAttribute("open")' in javascript
    assert 'typeof dialog.showModal === "function"' in javascript
    assert 'dialog.setAttribute("open", "")' in javascript
    assert 'dialog.removeAttribute("open")' in javascript
    assert 'document.body.classList.remove("viewer-open")' in javascript
    assert 'fullscreenButton.setAttribute("aria-pressed", String(isFullscreen))' in javascript
    assert "requestFullscreen" in javascript
    assert '"./api/education/client-name"' in javascript
    assert "save-client-name" in javascript
    assert "client_display_name" in javascript
    assert "client-name-note" in javascript


def test_education_frontend_clears_disabled_self_video_frame():
    html = (EDUCATION_FRONTEND / "index.html").read_text(encoding="utf-8")
    javascript = (EDUCATION_FRONTEND / "assets" / "index.js").read_text(encoding="utf-8")
    # from __debug_provenance_d__ import to

    assert "selfVideo.enabled && selfVideo.active && selfVideo.preview_available" in javascript
    assert '"./api/education/computer-frame.jpg?ts="' in javascript
    assert "Computer video" in html
    assert "Phone Camera" in html
    assert "clearImage(selfImg);" in javascript
    assert 'image.hidden = true;' in javascript
    assert 'image.removeAttribute("src");' in javascript
