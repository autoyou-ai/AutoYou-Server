# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-96d10de91e8e50dfd223c4de

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoyou_agents.audio_agent.website.backend import app as audio_ui_backend

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-96d10de91e8e50dfd223c4de"


@pytest.fixture(autouse=True)
def _authenticated_audio_website(monkeypatch):
    """Functional endpoint tests run behind the website's shared auth boundary."""
    monkeypatch.setattr(audio_ui_backend, "_check_auth", lambda request: True)


def test_audio_ui_backend_import_and_settings_endpoint() -> None:
    client = TestClient(audio_ui_backend.app)

    response = client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "settings" in payload
    assert "reply_target" in payload["settings"]


def test_repeat_relay_action_updates_mode_without_reply_target() -> None:
    client = TestClient(audio_ui_backend.app)

    response = client.post("/api/relay/repeat", json={"mode": "all"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["settings"]["repeat_mode"] == "all"


def test_unsupported_relay_action_includes_new_supported_actions() -> None:
    client = TestClient(audio_ui_backend.app)

    response = client.post("/api/relay/not-a-real-action", json={})
    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    supported = set(payload["supported_actions"])
    assert {"next", "previous", "repeat", "shuffle"}.issubset(supported)


def test_next_relay_action_selects_next_track_and_calls_play(monkeypatch) -> None:
    async def fake_relay(endpoint: str, request_payload: dict) -> dict:
        return {
            "success": True,
            "status_code": 200,
            "relay_url": "http://127.0.0.1:8001/api/webrtc/playback/play",
            "data": {
                "success": True,
                "status": "playing",
                "endpoint": endpoint,
                "request_payload": request_payload,
            },
        }

    tracks = [
        {
            "id": "track-a",
            "title": "Song A",
            "artist": "Local Library",
            "file_name": "song_a.mp3",
            "relative_path": "song_a.mp3",
            "directory": "C:/music",
            "stream_url": "api/stream/track-a",
        },
        {
            "id": "track-b",
            "title": "Song B",
            "artist": "Local Library",
            "file_name": "song_b.mp3",
            "relative_path": "song_b.mp3",
            "directory": "C:/music",
            "stream_url": "api/stream/track-b",
        },
    ]

    monkeypatch.setattr(audio_ui_backend, "_relay_webrtc_playback", fake_relay)
    monkeypatch.setattr(
        audio_ui_backend,
        "_library_state",
        lambda force, limit: {
            "roots": ["C:/music"],
            "tracks": tracks,
            "by_id": {"track-a": "C:/music/song_a.mp3", "track-b": "C:/music/song_b.mp3"},
            "truncated": False,
            "cached": True,
        },
    )

    client = TestClient(audio_ui_backend.app)
    response = client.post(
        "/api/relay/next",
        json={
            "track_id": "track-a",
            "reply_target": {"owner_key": "owner-123"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["action"] == "next"
    assert payload["selected_track"]["id"] == "track-b"
    assert payload["request_payload"]["file_path"].replace("\\", "/").endswith("/song_b.mp3")


def test_auth_status_unauthenticated_by_default(monkeypatch) -> None:
    """auth/status returns authenticated=False when no session is set."""
    monkeypatch.setattr(audio_ui_backend, "_check_auth", lambda request: False)
    client = TestClient(audio_ui_backend.app)
    response = client.get("/api/auth/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    # Without any session cookie or token, should not be authenticated
    assert payload["authenticated"] is False


def test_audio_ui_frontend_includes_visible_playback_spinner_guard() -> None:
    client = TestClient(audio_ui_backend.app)

    response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert "function hasPlaybackProgressAdvanced(snapshot)" in response.text
    assert "function syncBufferingIndicatorToPlayback()" in response.text
    assert "bindPlaybackVisibilityWatchers();" in response.text
    assert "function loadLibraryPage(loadId, offset, overallLimit)" in response.text
    assert "window.Amplitude.addSong" in response.text
    assert "window.Amplitude.getAudio" in response.text
    assert "function syncTransportPlaybackStateFromAudio()" in response.text
    assert "function browserCanPlayTrack(track)" in response.text
    assert "function handleCurrentTrackLoadFailure(message)" in response.text
    assert "function toggleListExpanded()" in response.text
    assert "function wireTransportModeButtons()" in response.text
    assert "function searchLibrary(query)" in response.text


def test_audio_ui_play_toggle_has_webview_accessibility_fallback() -> None:
    client = TestClient(audio_ui_backend.app)

    html_response = client.get("/")
    css_response = client.get("/assets/styles.css")

    assert html_response.status_code == 200
    assert css_response.status_code == 200
    assert '<span class="sr-only">Play or Pause</span>' in html_response.text
    assert ".sr-only" in css_response.text


def test_audio_ui_frontend_includes_library_sources_and_page_feed_upload() -> None:
    client = TestClient(audio_ui_backend.app)

    html_response = client.get("/")
    js_response = client.get("/assets/app.js")

    assert html_response.status_code == 200
    assert js_response.status_code == 200
    assert 'id="setting-source-voice"' in html_response.text
    assert 'id="setting-source-page"' in html_response.text
    assert 'id="setting-source-notes"' in html_response.text
    assert 'id="audio-upload-input"' in html_response.text
    assert "resolveAgentApiPath('page_agent', 'api/blob')" in js_response.text
    assert "resolveAgentApiPath('page_agent', 'api/feed')" in js_response.text
    assert "function renderAudioPaths(paths)" in js_response.text


def test_audio_ui_frontend_paginates_library_on_mobile_scroll() -> None:
    """Regression (2026-07-14): mobile WebViews scroll the document, so the
    element-scroll trigger alone was not reliable; appended batches also
    restarted their row numbering at "1" (e.g. "47 of 127")."""
    client = TestClient(audio_ui_backend.app)

    response = client.get("/assets/app.js")

    assert response.status_code == 200
    # Document-level capture-phase scroll fallback for mobile WebViews.
    assert "document.addEventListener('scroll', maybeLoadMoreLibrary" in response.text
    assert "function playlistDistanceFromBottom()" in response.text
    # Fill only a short viewport; each later page still requires near-bottom.
    assert "if (loaded) maybeLoadMoreLibrary();" in response.text
    assert "return false;" in response.text
    assert "function scheduleLibraryAutoload()" not in response.text
    # Library rows show their absolute position, not the per-batch index.
    assert "var displayIndex = (type === 'library') ? index : relativeIndex;" in response.text
    assert "String(displayIndex + 1) + '. ' + trackLabel" in response.text


def test_audio_ui_styles_respect_hidden_attribute() -> None:
    client = TestClient(audio_ui_backend.app)

    response = client.get("/assets/styles.css")

    assert response.status_code == 200
    assert "[hidden] { display: none !important; }" in response.text
    assert ".track-search" in response.text
    assert ".buffering-indicator" in response.text
    assert "position: absolute;" in response.text
    assert ".player.list-expanded" in response.text


def test_auth_login_fails_when_helpers_unavailable(monkeypatch) -> None:
    """login returns 503 when the scheduler_mission_control helpers can't be loaded."""
    monkeypatch.setattr(audio_ui_backend, "_import_auth_helpers", lambda: {})
    client = TestClient(audio_ui_backend.app)
    response = client.post("/api/auth/login", json={"totp_code": "123456"})
    assert response.status_code == 503


def test_auth_login_fails_when_totp_not_configured(monkeypatch) -> None:
    """login returns 400 when TOTP is not configured on the server."""
    mock_helpers = {
        "totp_capabilities": lambda: {"totp_configured": False},
        "create_session": MagicMock(),
        "cookie_name": lambda a: "audio_session",
        "cookie_path": lambda a: "/",
        "runtime_server": MagicMock(),
    }
    monkeypatch.setattr(audio_ui_backend, "_import_auth_helpers", lambda: mock_helpers)
    client = TestClient(audio_ui_backend.app)
    # from __debug_provenance_m__ import of
    response = client.post("/api/auth/login", json={"totp_code": "123456"})
    assert response.status_code == 400
    assert "not configured" in response.json()["error"].lower()


def test_auth_login_issues_token_on_valid_totp(monkeypatch) -> None:
    """login returns token when TOTP verification succeeds."""
    mock_server = MagicMock()
    mock_server.STATE.config = {}
    mock_server._default_config.return_value = {}
    mock_server._get_pairing_totp_secret.return_value = "TESTSECRET"
    mock_server._verify_totp_secret.return_value = True

    mock_helpers = {
        "totp_capabilities": lambda: {"totp_configured": True},
        "create_session": lambda agent, days: "test-session-token-xyz",
        "session_valid": lambda agent, token: token == "test-session-token-xyz",
        "delete_session": MagicMock(),
        "cookie_name": lambda a: "audio_agent_session",
        "cookie_path": lambda a: "/",
        "runtime_server": lambda: mock_server,
    }
    monkeypatch.setattr(audio_ui_backend, "_import_auth_helpers", lambda: mock_helpers)

    client = TestClient(audio_ui_backend.app, raise_server_exceptions=True)
    response = client.post("/api/auth/login", json={"totp_code": "123456"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["token"] == "test-session-token-xyz"


def test_webrtc_prefetch_requires_auth(monkeypatch) -> None:
    """prefetch endpoint returns 401 when not authenticated."""
    monkeypatch.setattr(audio_ui_backend, "_check_auth", lambda request: False)
    client = TestClient(audio_ui_backend.app)
    response = client.get("/api/webrtc/prefetch")
    assert response.status_code == 401
    assert response.json()["success"] is False


def test_audio_library_is_public_by_default(monkeypatch) -> None:
    monkeypatch.setattr(audio_ui_backend, "_check_auth", lambda request: False)
    client = TestClient(audio_ui_backend.app)

    assert client.get("/api/library").status_code == 200


def test_audio_page_is_public_while_settings_keep_their_otp_gate(monkeypatch) -> None:
    monkeypatch.setattr(audio_ui_backend, "_check_auth", lambda request: False)
    client = TestClient(audio_ui_backend.app)

    page = client.get("/")
    assert page.status_code == 200
    assert 'id="autoyou-auth-gate"' not in page.text
    assert 'id="otp-gate"' in page.text
    assert client.get("/api/auth/status").json()["authenticated"] is False


def test_audio_settings_and_relay_still_require_otp(monkeypatch) -> None:
    monkeypatch.setattr(audio_ui_backend, "_check_auth", lambda request: False)
    client = TestClient(audio_ui_backend.app)

    assert client.get("/api/settings").status_code == 401
    assert client.post("/api/settings", json={}).status_code == 401
    assert client.post("/api/relay/repeat", json={"mode": "all"}).status_code == 401


def test_webrtc_prefetch_returns_sessions_when_authed(monkeypatch) -> None:
    """prefetch returns active WebRTC sessions after auth helpers confirm validity."""
    mock_webrtc = MagicMock()
    mock_webrtc.session_peers = {"sess-1": MagicMock(), "sess-2": MagicMock()}
    mock_webrtc.datachannel_managers = {}

    mock_server = MagicMock()
    mock_server.WEBRTC = mock_webrtc

    mock_helpers = {
        "totp_capabilities": lambda: {"totp_configured": True},
        "create_session": MagicMock(),
        "session_valid": lambda agent, token: token == "valid-token",
        "delete_session": MagicMock(),
        "cookie_name": lambda a: "audio_agent_session",
        "cookie_path": lambda a: "/",
        "runtime_server": lambda: mock_server,
    }
    monkeypatch.setattr(audio_ui_backend, "_import_auth_helpers", lambda: mock_helpers)

    client = TestClient(audio_ui_backend.app)
    response = client.get(
        "/api/webrtc/prefetch",
        headers={"Authorization": "Bearer valid-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["count"] == 2
    session_ids = {s["session_id"] for s in payload["sessions"]}
    assert session_ids == {"sess-1", "sess-2"}


def test_library_cache_rescans_when_request_limit_increases(monkeypatch) -> None:
    """If cached scan was truncated at a low limit, a larger limit should trigger a fresh scan."""

    calls = []

    def fake_scan(roots, limit):
        calls.append(limit)
        # Simulate a library with 150 files.
        track_count = min(limit, 150)
        tracks = [
            {
                "id": f"track-{i}",
                "title": f"Track {i}",
                "artist": "Local Library",
                "file_name": f"track_{i}.mp3",
                "relative_path": f"track_{i}.mp3",
                "directory": "C:/music",
                "stream_url": f"api/stream/track-{i}",
            }
            for i in range(track_count)
        ]
        return {
            "tracks": tracks,
            "by_id": {t["id"]: f"C:/music/{t['file_name']}" for t in tracks},
            "truncated": limit < 150,
        }

    monkeypatch.setattr(audio_ui_backend, "_normalize_roots", lambda: ["C:/music"])
    monkeypatch.setattr(audio_ui_backend, "_scan_tracks", fake_scan)

    # Reset cache so this test is deterministic.
    audio_ui_backend._library_cache["roots"] = []
    audio_ui_backend._library_cache["tracks"] = []
    audio_ui_backend._library_cache["by_id"] = {}
    audio_ui_backend._library_cache["limit"] = 0
    audio_ui_backend._library_cache["truncated"] = False
    audio_ui_backend._library_cache["scanned"] = False

    low = audio_ui_backend._library_state(force=False, limit=10)
    high = audio_ui_backend._library_state(force=False, limit=120)

    assert low["cached"] is False
    assert low["truncated"] is True
    assert high["cached"] is False
    assert len(high["tracks"]) == 120
    # Each scan now reads one sentinel track past the requested page so the API
    # can report whether another page exists without forcing a full-library scan.
    assert calls == [11, 121]


def test_scan_tracks_exact_limit_is_not_marked_truncated(tmp_path: Path) -> None:
    for idx in range(3):
        (tmp_path / f"track_{idx}.mp3").write_bytes(b"test-audio")

    scanned = audio_ui_backend._scan_tracks([str(tmp_path)], limit=3)

    assert scanned["truncated"] is False
    assert len(scanned["tracks"]) == 3


def test_api_library_supports_paged_batches(monkeypatch) -> None:
    total_tracks = 250

    def fake_scan(roots, limit):
        track_count = min(limit, total_tracks)
        tracks = [
            {
                "id": f"track-{i}",
                "title": f"Track {i}",
                "artist": "Local Library",
                "file_name": f"track_{i}.mp3",
                "relative_path": f"track_{i}.mp3",
                "directory": "C:/music",
                "stream_url": f"api/stream/track-{i}",
            }
            for i in range(track_count)
        ]
        return {
            "tracks": tracks,
            "by_id": {track["id"]: f"C:/music/{track['file_name']}" for track in tracks},
            "truncated": total_tracks > limit,
        }

    monkeypatch.setattr(audio_ui_backend, "_normalize_roots", lambda: ["C:/music"])
    monkeypatch.setattr(audio_ui_backend, "_scan_tracks", fake_scan)

    audio_ui_backend._library_cache["roots"] = []
    audio_ui_backend._library_cache["tracks"] = []
    audio_ui_backend._library_cache["by_id"] = {}
    audio_ui_backend._library_cache["limit"] = 0
    audio_ui_backend._library_cache["truncated"] = False
    audio_ui_backend._library_cache["scanned"] = False

    client = TestClient(audio_ui_backend.app)

    first = client.get("/api/library?offset=0&limit=100")
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["track_count"] == 100
    assert first_payload["has_more"] is True
    assert first_payload["next_offset"] == 100
    assert first_payload["tracks"][0]["id"] == "track-0"
    assert first_payload["tracks"][-1]["id"] == "track-99"

    second = client.get("/api/library?offset=100&limit=100")
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["track_count"] == 100
    assert second_payload["has_more"] is True
    assert second_payload["next_offset"] == 200
    assert second_payload["tracks"][0]["id"] == "track-100"
    assert second_payload["tracks"][-1]["id"] == "track-199"

    third = client.get("/api/library?offset=200&limit=100")
    assert third.status_code == 200
    third_payload = third.json()
    assert third_payload["track_count"] == 50
    assert third_payload["has_more"] is False
    assert third_payload["next_offset"] is None
    assert third_payload["tracks"][0]["id"] == "track-200"
    assert third_payload["tracks"][-1]["id"] == "track-249"


def test_api_library_query_returns_small_matching_page(monkeypatch, tmp_path: Path) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Summit.mp3").write_bytes(b"audio")
    (music / "Sunset Drive.mp3").write_bytes(b"audio")
    (music / "Podcast.wav").write_bytes(b"audio")

    monkeypatch.setattr(audio_ui_backend, "_normalize_roots", lambda: [str(music)])

    audio_ui_backend._library_cache["roots"] = []
    audio_ui_backend._library_cache["tracks"] = []
    audio_ui_backend._library_cache["by_id"] = {}
    audio_ui_backend._library_cache["limit"] = 0
    audio_ui_backend._library_cache["truncated"] = False
    audio_ui_backend._library_cache["scanned"] = False

    client = TestClient(audio_ui_backend.app)
    response = client.get("/api/library?query=sum&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "sum"
    assert payload["track_count"] == 1
    assert payload["tracks"][0]["file_name"] == "Summit.mp3"
    assert payload["has_more"] is False


def test_api_stream_open_ended_range_is_windowed(monkeypatch, tmp_path: Path) -> None:
    file_bytes = b"A" * (2 * 1024 * 1024)
    audio_file = tmp_path / "sample.mp3"
    audio_file.write_bytes(file_bytes)

    monkeypatch.setattr(
        audio_ui_backend,
        "_library_state",
        lambda force, limit: {
            "roots": [str(tmp_path)],
            "tracks": [],
            "by_id": {"track-1": str(audio_file)},
            "truncated": False,
            "cached": True,
        },
    )

    client = TestClient(audio_ui_backend.app)
    response = client.get("/api/stream/track-1", headers={"Range": "bytes=0-"})

    assert response.status_code == 206
    window = audio_ui_backend._stream_range_window_bytes()
    assert len(response.content) == window
    assert response.headers.get("accept-ranges") == "bytes"
    assert response.headers.get("content-range") == f"bytes 0-{window - 1}/{len(file_bytes)}"


def test_api_stream_finite_range_is_honored_exactly(monkeypatch, tmp_path: Path) -> None:
    file_bytes = b"A" * (2 * 1024 * 1024)
    audio_file = tmp_path / "finite-range.mp3"
    audio_file.write_bytes(file_bytes)

    monkeypatch.setattr(
        audio_ui_backend,
        "_library_state",
        lambda force, limit: {
            "roots": [str(tmp_path)],
            "tracks": [],
            "by_id": {"track-finite": str(audio_file)},
            "truncated": False,
            "cached": True,
        },
    )

    client = TestClient(audio_ui_backend.app)
    end = len(file_bytes) - 1
    response = client.get(
        "/api/stream/track-finite",
        headers={
            "Range": f"bytes=0-{end}",
            "Accept-Encoding": "identity",
        },
    )

    assert response.status_code == 206
    assert len(response.content) == len(file_bytes)
    assert response.headers.get("content-length") == str(len(file_bytes))
    assert response.headers.get("content-range") == f"bytes 0-{end}/{len(file_bytes)}"


def test_api_stream_webrtc_finite_range_is_windowed(monkeypatch, tmp_path: Path) -> None:
    file_bytes = b"A" * (2 * 1024 * 1024)
    audio_file = tmp_path / "webrtc-range.mp3"
    audio_file.write_bytes(file_bytes)

    monkeypatch.setattr(
        audio_ui_backend,
        "_library_state",
        lambda force, limit: {
            "roots": [str(tmp_path)],
            "tracks": [],
            "by_id": {"track-webrtc": str(audio_file)},
            "truncated": False,
            "cached": True,
        },
    )

    response = TestClient(audio_ui_backend.app).get(
        "/api/stream/track-webrtc",
        headers={
            "Range": f"bytes=0-{len(file_bytes) - 1}",
            "Accept-Encoding": "identity",
            "X-AutoYou-WebRTC-Session-Id": "synthetic-session",
        },
    )

    window = audio_ui_backend._stream_range_window_bytes()
    assert response.status_code == 206
    assert len(response.content) == window
    assert response.headers.get("content-length") == str(window)
    assert response.headers.get("content-range") == f"bytes 0-{window - 1}/{len(file_bytes)}"


def test_audio_buffering_indicator_stays_out_of_seek_layout() -> None:
    frontend_dir = Path(audio_ui_backend.__file__).resolve().parents[1] / "frontend"
    css = (frontend_dir / "styles.css").read_text(encoding="utf-8")
    block = css.split(".buffering-indicator", 1)[1].split("}", 1)[0]

    assert "position: relative" in block
    assert "position: absolute" not in block


def test_api_stream_invalid_range_returns_416(monkeypatch, tmp_path: Path) -> None:
    file_bytes = b"A" * 4096
    audio_file = tmp_path / "sample.mp3"
    audio_file.write_bytes(file_bytes)

    monkeypatch.setattr(
        audio_ui_backend,
        "_library_state",
        lambda force, limit: {
            "roots": [str(tmp_path)],
            "tracks": [],
            "by_id": {"track-2": str(audio_file)},
            "truncated": False,
            "cached": True,
        },
    )

    client = TestClient(audio_ui_backend.app)
    response = client.get("/api/stream/track-2", headers={"Range": "bytes=999999-"})

    assert response.status_code == 416
    assert response.headers.get("content-range") == f"bytes */{len(file_bytes)}"


def test_track_payload_includes_browser_audio_content_type(tmp_path: Path) -> None:
    audio_file = tmp_path / "song.m4a"
    audio_file.write_bytes(b"synthetic-audio")

    payload = audio_ui_backend._build_track_payload(audio_file, tmp_path)

    assert payload["content_type"] == "audio/mp4"


def test_api_stream_uses_browser_audio_content_type(monkeypatch, tmp_path: Path) -> None:
    audio_file = tmp_path / "sample.flac"
    audio_file.write_bytes(b"synthetic-audio")

    monkeypatch.setattr(
        audio_ui_backend,
        "_library_state",
        lambda force, limit: {
            "roots": [str(tmp_path)],
            "tracks": [],
            "by_id": {"track-flac": str(audio_file)},
            "truncated": False,
            "cached": True,
        },
    )

    client = TestClient(audio_ui_backend.app)
    response = client.get("/api/stream/track-flac")

    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("audio/flac")


def test_audio_ui_backend_import_and_settings_endpoint() -> None:
    client = TestClient(audio_ui_backend.app)

    response = client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "settings" in payload
    assert "reply_target" in payload["settings"]


def test_repeat_relay_action_updates_mode_without_reply_target() -> None:
    client = TestClient(audio_ui_backend.app)

    response = client.post("/api/relay/repeat", json={"mode": "all"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["settings"]["repeat_mode"] == "all"


def test_unsupported_relay_action_includes_new_supported_actions() -> None:
    client = TestClient(audio_ui_backend.app)

    response = client.post("/api/relay/not-a-real-action", json={})
    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    supported = set(payload["supported_actions"])
    assert {"next", "previous", "repeat", "shuffle"}.issubset(supported)


def test_next_relay_action_selects_next_track_and_calls_play(monkeypatch) -> None:
    async def fake_relay(endpoint: str, request_payload: dict) -> dict:
        return {
            "success": True,
            "status_code": 200,
            "relay_url": "http://127.0.0.1:8001/api/webrtc/playback/play",
            "data": {
                "success": True,
                "status": "playing",
                "endpoint": endpoint,
                "request_payload": request_payload,
            },
        }

    tracks = [
        {
            "id": "track-a",
            "title": "Song A",
            "artist": "Local Library",
            "file_name": "song_a.mp3",
            "relative_path": "song_a.mp3",
            "directory": "C:/music",
            "stream_url": "api/stream/track-a",
        },
        {
            "id": "track-b",
            "title": "Song B",
            "artist": "Local Library",
            "file_name": "song_b.mp3",
            "relative_path": "song_b.mp3",
            "directory": "C:/music",
            "stream_url": "api/stream/track-b",
        },
    ]

    monkeypatch.setattr(audio_ui_backend, "_relay_webrtc_playback", fake_relay)
    monkeypatch.setattr(
        audio_ui_backend,
        "_library_state",
        lambda force, limit: {
            "roots": ["C:/music"],
            "tracks": tracks,
            "by_id": {"track-a": "C:/music/song_a.mp3", "track-b": "C:/music/song_b.mp3"},
            "truncated": False,
            "cached": True,
        },
    )

    client = TestClient(audio_ui_backend.app)
    response = client.post(
        "/api/relay/next",
        json={
            "track_id": "track-a",
            "reply_target": {"owner_key": "owner-123"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["action"] == "next"
    assert payload["selected_track"]["id"] == "track-b"
    assert payload["request_payload"]["file_path"].replace("\\", "/").endswith("/song_b.mp3")
