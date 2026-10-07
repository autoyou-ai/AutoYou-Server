# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-0dd0810810871b192298a431


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import asyncio
import importlib
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import server
import pytest

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-0dd0810810871b192298a431"


def test_bluetooth_pair_runtime_status_reports_enabled_but_not_listening():
    original_config = server.STATE.config
    original_runtime = getattr(server.STATE, "bluetooth_pairing_server", None)
    try:
        cfg = server._default_config()
        cfg["bluetooth_pairing"]["enabled"] = True
        server.STATE.config = cfg
        server.STATE.bluetooth_pairing_server = None

        status = server._bluetooth_pairing_runtime_status()

        assert status["enabled"] is True
        assert status["running"] is False
        assert status["transport"] == "ble-gatt"
        assert "not running" in status["error"]
    finally:
        server.STATE.config = original_config
        server.STATE.bluetooth_pairing_server = original_runtime


def test_admin_config_patch_persists_bluetooth_pairing_setting():
    cfg, touched, _theme = server._apply_admin_ui_config_patch(
        server._default_config(),
        {"bluetooth_pairing": {"enabled": True}},
    )

    assert "bluetooth_pairing" in touched
    assert cfg["bluetooth_pairing"]["enabled"] is True


def test_admin_config_patch_persists_remote_access_role():
    cfg, touched, _theme = server._apply_admin_ui_config_patch(
        server._default_config(),
        {"autoyou_page": {"remote_access_role": "viewer"}},
    )

    assert "autoyou_page" in touched
    assert cfg["autoyou_page"]["remote_access_role"] == "viewer"


def test_admin_config_patch_rejects_unknown_remote_access_role():
    try:
        server._apply_admin_ui_config_patch(
            server._default_config(),
            {"autoyou_page": {"remote_access_role": "operator"}},
        )
    except ValueError as exc:
        assert "remote_access_role" in str(exc)
    else:
        raise AssertionError("expected invalid remote access role to be rejected")


def test_admin_config_patch_persists_ai_memory_backend():
    cfg, touched, _theme = server._apply_admin_ui_config_patch(
        server._default_config(),
        {"ai_agent": {"memory_backend": "cognee"}},
    )

    assert "ai_agent" in touched
    assert cfg["ai_agent"]["memory_backend"] == "cognee"


def test_admin_config_patch_rejects_unknown_ai_memory_backend():
    try:
        server._apply_admin_ui_config_patch(
            server._default_config(),
            {"ai_agent": {"memory_backend": "remote-production-memory"}},
        )
    except ValueError as exc:
        assert "ai_agent.memory_backend" in str(exc)
    else:
        raise AssertionError("expected invalid memory backend to be rejected")


def test_admin_config_patch_persists_video_call_settings():
    cfg, touched, _theme = server._apply_admin_ui_config_patch(
        server._default_config(),
        {
            "video_call": {
                "enabled": False,
                "audio_enabled": False,
                "background_mode_enabled": True,
                "silent_recording_enabled": True,
                "silent_recording_dir": "C:/AutoYou/test-silent",
                "silent_recording_batch_seconds": 9999,
                "disable_autoyou_agents": True,
                "ai_audio_replies_enabled": False,
                "record_my_video": True,
                "recording_dir": "C:/AutoYou/test-video",
                "recording_mode": "images",
                "image_interval_seconds": 9,
                "outbound_source": "video_file",
                "outbound_sources": ["remote_desktop", "camera"],
                "api_video_source_id": "camera-feed",
                "audio_sources": ["microphone", "speaker_loopback"],
                "video_file": {
                    "path": "C:/AutoYou/test-clip.mp4",
                    "loop": True,
                },
                "remote_desktop": {
                    "enabled": False,
                    "send_screen": False,
                    "monitor_id": 1,
                    "quality": "HIGH",
                    "bitrate_kbps": 9000,
                    "control_enabled": True,
                },
            }
        },
    )

    assert "video_call" in touched
    assert cfg["video_call"]["enabled"] is False
    assert cfg["video_call"]["audio_enabled"] is False
    assert cfg["video_call"]["background_mode_enabled"] is True
    assert cfg["video_call"]["silent_recording_enabled"] is True
    assert cfg["video_call"]["silent_recording_dir"] == "C:/AutoYou/test-silent"
    assert cfg["video_call"]["silent_recording_batch_seconds"] == 3599
    assert cfg["video_call"]["disable_autoyou_agents"] is True
    assert cfg["video_call"]["ai_audio_replies_enabled"] is False
    assert cfg["video_call"]["record_my_video"] is True
    assert cfg["video_call"]["recording_dir"] == "C:/AutoYou/test-video"
    assert cfg["video_call"]["recording_mode"] == "images"
    assert cfg["video_call"]["image_interval_seconds"] == 9
    assert cfg["video_call"]["outbound_source"] == "remote_desktop"
    assert cfg["video_call"]["outbound_sources"] == ["remote_desktop", "camera"]
    assert cfg["video_call"]["api_video_source_id"] == "camera-feed"
    assert cfg["video_call"]["audio_sources"] == ["microphone", "speaker_loopback"]
    assert cfg["video_call"]["capture_audio"] is True
    assert cfg["video_call"]["video_file"]["path"] == "C:/AutoYou/test-clip.mp4"
    assert cfg["video_call"]["video_file"]["loop"] is True
    assert cfg["video_call"]["remote_desktop"]["enabled"] is False
    assert cfg["video_call"]["remote_desktop"]["send_screen"] is False
    assert cfg["video_call"]["remote_desktop"]["monitor_id"] == 1
    assert cfg["video_call"]["remote_desktop"]["quality"] == "high"
    assert cfg["video_call"]["remote_desktop"]["bitrate_kbps"] == 3000
    assert cfg["video_call"]["remote_desktop"]["control_enabled"] is True


def test_admin_config_patch_persists_wuift_setting():
    cfg, touched, _theme = server._apply_admin_ui_config_patch(
        server._default_config(),
        {"video_call": {"wuift_enabled": False}},
    )

    assert "video_call" in touched
    assert cfg["video_call"]["wuift_enabled"] is False


def test_game_button_layout_is_validated_and_persisted():
    cfg, _, _ = server._apply_admin_ui_config_patch(
        server._default_config(),
        {"video_call": {"remote_desktop": {"game_buttons": "Jump:jump, Fire:fire"}}},
    )
    assert cfg["video_call"]["remote_desktop"]["game_buttons"] == [
        {"label": "Jump", "name": "jump"}, {"label": "Fire", "name": "fire"},
    ]
    assert server._normalize_video_call_config(cfg["video_call"])["remote_desktop"]["game_buttons"] == cfg["video_call"]["remote_desktop"]["game_buttons"]
    with pytest.raises(ValueError, match="Game buttons"):
        server._apply_admin_ui_config_patch(server._default_config(), {
            "video_call": {"remote_desktop": {"game_buttons": "Jump:jump, Again:jump"}},
        })
    with pytest.raises(ValueError, match="Game buttons"):
        server._apply_admin_ui_config_patch(server._default_config(), {
            "video_call": {"remote_desktop": {"game_buttons": "Bad:<script>"}},
        })
    with pytest.raises(ValueError, match="Game buttons"):
        server._apply_admin_ui_config_patch(server._default_config(), {
            "video_call": {"remote_desktop": {"game_buttons": [{"label": "Jump, Fire", "name": "jump"}]}},
        })


def test_wuift_enabled_defaults_on_and_follows_audio_master_gate():
    cfg = server._default_config()
    assert server._get_wuift_enabled(cfg=cfg) is True

    cfg["video_call"]["wuift_enabled"] = False
    assert server._get_wuift_enabled(cfg=cfg) is False

    cfg["video_call"]["wuift_enabled"] = True
    cfg["video_call"]["audio_enabled"] = False
    assert server._get_wuift_enabled(cfg=cfg) is False


def test_webrtc_capabilities_advertise_wuift_policy():
    cfg = server._default_config()

    capabilities = server._build_webrtc_capabilities(cfg=cfg)
    assert capabilities["host_platform"] == server.get_platform()
    wuift = capabilities["audio"]["wuift"]
    assert wuift["enabled"] is True
    assert wuift["available"] == bool(
        server._get_video_call_audio_enabled(cfg=cfg)
        and server.AudioManager is not None
        and server._get_video_call_agent_processing_enabled(cfg=cfg)
    )

    cfg["video_call"]["wuift_enabled"] = False
    disabled = server._build_webrtc_capabilities(cfg=cfg)
    assert disabled["audio"]["wuift"]["enabled"] is False

    cfg["video_call"]["wuift_enabled"] = True
    cfg["video_call"]["audio_enabled"] = False
    audio_off = server._build_webrtc_capabilities(cfg=cfg)
    assert audio_off["audio"]["wuift"]["enabled"] is False
    assert audio_off["audio"]["wuift"]["available"] is False


def test_outbound_video_availability_only_probes_selected_sources(monkeypatch):
    def unexpected(*, cfg=None):
        raise AssertionError("unselected video source was probed")

    monkeypatch.setattr(server, "_get_video_outbound_sources", lambda *, cfg=None: ["remote_desktop"])
    monkeypatch.setattr(server, "_get_remote_desktop_video_available", lambda *, cfg=None: True)
    monkeypatch.setattr(server, "_get_api_realtime_video_available", unexpected)
    monkeypatch.setattr(server, "_get_video_file_available", unexpected)
    monkeypatch.setattr(server, "_get_camera_video_available", unexpected)

    assert server._get_available_video_outbound_sources() == ["remote_desktop"]


def test_video_file_loop_defaults_on_but_preserves_explicit_disable():
    default_cfg = server._default_config()
    assert default_cfg["video_call"]["video_file"]["loop"] is True

    normalized = server._normalize_video_call_config(
        {"outbound_source": "video_file", "video_file": {"path": "clip.mp4"}}
    )
    assert normalized["video_file"]["loop"] is True

    explicitly_disabled = server._normalize_video_call_config(
        {"outbound_source": "video_file", "video_file": {"path": "clip.mp4", "loop": False}}
    )
    assert explicitly_disabled["video_file"]["loop"] is False


def test_video_file_audio_uses_media_lane_when_file_is_a_secondary_video_source(monkeypatch, tmp_path):
    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(b"synthetic video fixture")
    cfg = server._default_config()
    cfg["video_call"]["outbound_sources"] = ["api", "video_file"]
    cfg["video_call"]["video_file"] = {"path": str(video_file), "loop": True}

    class FakeTrack:
        def __init__(self):
            self.calls = []

        def play_audio_file(self, path, *, source, loop):
            self.calls.append((path, source, loop))

    class FakeMixedAudioStreamTrack:
        def __init__(self, sources):
            self.sources = list(sources)

        def source_names(self):
            return [name for name, _track in self.sources]

    monkeypatch.setattr(server, "MixedAudioStreamTrack", FakeMixedAudioStreamTrack)
    tts_track = FakeTrack()
    playback_track = FakeTrack()
    mixed_track = server._create_configured_outbound_audio_track(
        cfg=cfg,
        session_id="synthetic-session",
        tts_track=tts_track,
        playback_track=playback_track,
    )

    assert mixed_track.sources == [
        ("ai_replies", tts_track),
        ("playback", playback_track),
    ]
    assert tts_track.calls == []
    assert playback_track.calls == [(str(video_file), "video_file", True)]


def test_remote_desktop_storage_tier_does_not_block_chat_capabilities(monkeypatch):
    def protected_registry(**_kwargs):
        raise server.SecureStorageError("synthetic protected registry")

    monkeypatch.setattr(server, "load_agent_install_registry", protected_registry)

    assert server._is_remote_desktop_agent_installed() is False


def test_webrtc_capabilities_reflect_video_and_remote_desktop_policy(monkeypatch, tmp_path):
    cfg = server._default_config()
    cfg["video_call"]["record_my_video"] = True
    cfg["video_call"]["recording_dir"] = str(tmp_path)
    cfg["video_call"]["recording_mode"] = "images"
    cfg["video_call"]["image_interval_seconds"] = 11
    cfg["video_call"]["background_mode_enabled"] = True
    cfg["video_call"]["silent_recording_enabled"] = True
    cfg["video_call"]["silent_recording_dir"] = str(tmp_path / "silent-recordings")
    cfg["video_call"]["silent_recording_batch_seconds"] = 900
    cfg["video_call"]["remote_desktop"]["monitor_id"] = 1
    cfg["video_call"]["remote_desktop"]["quality"] = "high"
    cfg["video_call"]["remote_desktop"]["bitrate_kbps"] = 2800
    # from __debug_provenance_n__ import license
    cfg["video_call"]["remote_desktop"]["control_enabled"] = True
    cfg["video_call"]["remote_desktop"]["game_buttons"] = [{"label": "Jump", "name": "jump"}]

    monkeypatch.setattr(server, "_is_remote_desktop_agent_installed", lambda: False)

    monkeypatch.setattr(server, "_get_agent_frontend_enabled", lambda _agent_name, *, cfg=None: False)
    monkeypatch.setattr(server, "IncomingVideoTrackSink", object())
    monkeypatch.setattr(server, "RemoteDesktopVideoStreamTrack", object())
    monkeypatch.setattr(server, "RealtimeVideoInputStreamTrack", object())
    monkeypatch.setattr(server, "VideoFileStreamTrack", object())
    monkeypatch.setattr(server, "AudioTrackSink", object())
    monkeypatch.setattr(server, "create_outbound_video_track", lambda *args, **kwargs: object())
    monkeypatch.setattr(server, "remote_desktop_input_backend_probed", lambda: True)

    capabilities = server._build_webrtc_capabilities(cfg=cfg, include_admin=True)

    assert capabilities["video"]["enabled"] is True
    assert capabilities["video"]["receive_enabled"] is True
    assert capabilities["video"]["record_my_video"] is True
    assert capabilities["video"]["recording_dir"] == str(tmp_path)
    assert capabilities["video"]["recording_mode"] == "images"
    assert capabilities["video"]["recording_format"] == "jpeg_images"
    assert capabilities["video"]["image_interval_seconds"] == 11
    assert capabilities["video"]["disable_autoyou_agents"] is False
    assert capabilities["audio"]["enabled"] is True
    assert capabilities["audio"]["agents_disabled"] is False
    assert capabilities["audio"]["agent_processing_enabled"] is True
    assert capabilities["audio"]["background_mode"]["enabled"] is True
    assert capabilities["audio"]["background_mode"]["configured"] is True
    assert capabilities["audio"]["background_mode"]["available"] is True
    assert capabilities["audio"]["background_mode"]["client_audio_direction"] == "inactive"
    assert capabilities["audio"]["background_mode"]["ios_client_audio_direction"] == "recvonly"
    assert capabilities["audio"]["background_mode"]["android_client_audio_direction"] == "inactive"
    assert capabilities["audio"]["background_mode"]["server_audio_direction"] == "inactive"
    assert capabilities["audio"]["background_mode"]["ios_server_audio_direction"] == "sendonly"
    assert capabilities["audio"]["background_mode"]["android_server_audio_direction"] == "inactive"
    assert capabilities["audio"]["background_mode"]["desktop_server_audio_direction"] == "inactive"
    assert capabilities["audio"]["background_mode"]["server_audio_output"] == "none"
    assert capabilities["audio"]["background_mode"]["ios_server_audio_output"] == "background_audio_heartbeat"
    assert capabilities["audio"]["background_mode"]["android_server_audio_output"] == "none"
    assert capabilities["audio"]["background_mode"]["desktop_server_audio_output"] == "none"
    assert capabilities["audio"]["silent_recording"]["enabled"] is True
    assert capabilities["audio"]["silent_recording"]["configured"] is True
    assert capabilities["audio"]["silent_recording"]["available"] is True
    assert capabilities["audio"]["silent_recording"]["client_audio_direction"] == "sendonly"
    assert capabilities["audio"]["silent_recording"]["server_audio_direction"] == "recvonly"
    assert capabilities["audio"]["silent_recording"]["batch_seconds"] == 900
    assert capabilities["audio"]["silent_recording"]["recording_dir"] == str(tmp_path / "silent-recordings")
    assert capabilities["remote_desktop"]["enabled"] is True
    assert capabilities["remote_desktop"]["agent_installed"] is False
    assert capabilities["remote_desktop"]["agent_enabled"] is False
    assert capabilities["remote_desktop"]["monitor_id"] == 1
    assert capabilities["remote_desktop"]["quality"] == "high"
    assert capabilities["remote_desktop"]["max_width"] == 1920
    assert capabilities["remote_desktop"]["fps"] == 20
    assert capabilities["remote_desktop"]["bitrate_kbps"] == 2800
    assert capabilities["remote_desktop"]["frame_aspect_ratio"] > 0
    assert capabilities["remote_desktop"]["control_enabled"] is True
    assert capabilities["remote_desktop"]["control_available"] is True
    assert capabilities["remote_desktop"]["control_protocol"] == "autoyou_remote_desktop_v1"
    assert capabilities["remote_desktop"]["game_buttons"] == [{"label": "Jump", "name": "jump"}]
    assert capabilities["outbound_video"]["available_sources"]["remote_desktop"]["game_buttons"] == [{"label": "Jump", "name": "jump"}]
    assert capabilities["outbound_video"]["source"] == "remote_desktop"
    assert capabilities["outbound_video"]["enabled"] is True
    assert capabilities["outbound_video"]["available_sources"]["remote_desktop"]["enabled"] is True
    assert capabilities["outbound_video"]["runtime_status"] == "ready"

    cfg["video_call"]["audio_sources"] = ["microphone", "speaker_loopback"]
    cfg["video_call"]["ai_audio_replies_enabled"] = False
    cfg["video_call"]["outbound_sources"] = ["remote_desktop", "camera"]
    capabilities = server._build_webrtc_capabilities(cfg=cfg, include_admin=True)
    assert capabilities["audio"]["audio_sources"] == ["microphone", "speaker_loopback"]
    assert capabilities["audio"]["capture_audio"] is True
    assert capabilities["audio"]["ai_audio_replies_enabled"] is False
    assert capabilities["audio"]["output_mode"] == "mixed"
    assert [item["id"] for item in capabilities["audio"]["mixer"]["sources"]] == [
        "microphone",
        "speaker_loopback",
    ]
    assert capabilities["outbound_video"]["source"] == "stitched"
    assert capabilities["outbound_video"]["sources"] == ["remote_desktop", "camera"]

    cfg["video_call"]["disable_autoyou_agents"] = True
    capabilities = server._build_webrtc_capabilities(cfg=cfg, include_admin=True)
    assert capabilities["video"]["disable_autoyou_agents"] is True
    assert capabilities["audio"]["enabled"] is True
    assert capabilities["audio"]["agents_disabled"] is True
    assert capabilities["audio"]["agent_processing_enabled"] is False
    assert capabilities["audio"]["voice_pipeline_available"] is False
    assert capabilities["audio"]["tts_available"] is False
    assert capabilities["outbound_video"]["enabled"] is True
    cfg["video_call"]["disable_autoyou_agents"] = False
    cfg["video_call"].pop("outbound_sources", None)

    cfg["video_call"]["outbound_source"] = "api"
    cfg["video_call"]["api_video_source_id"] = "camera-feed"
    capabilities = server._build_webrtc_capabilities(cfg=cfg)
    assert capabilities["outbound_video"]["source"] == "api"
    assert capabilities["outbound_video"]["api_source_id"] == "camera-feed"
    assert capabilities["outbound_video"]["enabled"] is True

    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(b"synthetic video fixture")
    cfg["video_call"]["outbound_source"] = "video_file"
    cfg["video_call"]["video_file"] = {"path": str(video_file), "loop": True}
    capabilities = server._build_webrtc_capabilities(cfg=cfg, include_admin=True)
    assert capabilities["outbound_video"]["source"] == "video_file"
    assert capabilities["outbound_video"]["enabled"] is True
    video_file_caps = capabilities["outbound_video"]["available_sources"]["video_file"]
    assert video_file_caps["enabled"] is True
    assert video_file_caps["file_path"] == str(video_file)
    assert video_file_caps["file_exists"] is True
    assert video_file_caps["loop"] is True

    cfg["video_call"]["remote_desktop"]["enabled"] = False
    cfg["video_call"]["outbound_source"] = "remote_desktop"
    capabilities = server._build_webrtc_capabilities(cfg=cfg)
    assert capabilities["remote_desktop"]["enabled"] is False
    assert capabilities["remote_desktop"]["configured_enabled"] is False


def test_webrtc_capabilities_minimize_outbound_video_telemetry_for_peers(monkeypatch):
    telemetry = {
        "frame_count": 3,
        "error_count": 1,
        "latest_frame": {
            "source": "remote_desktop",
            "width": 1280,
            "height": 720,
            "timestamp_ms": 12345,
            "frame_count": 3,
        },
        "latest_error": {
            "source": "negotiated",
            "stage": "rtp_encode",
            "detail": "avcodec_open2(libx264)",
            "timestamp_ms": 12346,
            "error_count": 1,
        },
    }
    monkeypatch.setattr(server, "outbound_video_telemetry_status", lambda: telemetry)

    peer_capabilities = server._build_webrtc_capabilities(cfg=server._default_config())
    peer_telemetry = peer_capabilities["outbound_video"]["telemetry"]
    assert peer_telemetry == {
        "frame_count": 3,
        "error_count": 1,
        "latest_error": telemetry["latest_error"],
    }

    admin_capabilities = server._build_webrtc_capabilities(
        cfg=server._default_config(),
        include_admin=True,
    )
    assert admin_capabilities["outbound_video"]["telemetry"]["latest_frame"] == telemetry["latest_frame"]


def test_webrtc_capabilities_distinguish_ready_from_actively_streaming(monkeypatch):
    cfg = server._default_config()
    track = SimpleNamespace(is_enabled=True)
    monkeypatch.setattr(server, "WEBRTC", SimpleNamespace(desktop_video_tracks={"synthetic-session": track}))
    monkeypatch.setattr(server, "_is_remote_desktop_agent_installed", lambda: True)
    monkeypatch.setattr(server, "_get_agent_frontend_enabled", lambda _name, *, cfg=None: True)
    monkeypatch.setattr(server, "RemoteDesktopVideoStreamTrack", object())
    monkeypatch.setattr(
        server,
        "outbound_video_telemetry_status",
        lambda: {
            "frame_count": 1,
            "error_count": 0,
            "latest_frame": {
                "source": "remote_desktop",
                "width": 1280,
                "height": 720,
                "timestamp_ms": int(server.time.time() * 1000),
                "frame_count": 1,
            },
            "latest_error": None,
        },
    )

    capabilities = server._build_webrtc_capabilities(cfg=cfg)

    assert capabilities["outbound_video"]["ready"] is True
    assert capabilities["outbound_video"]["stream_active"] is True
    assert capabilities["outbound_video"]["recent_frame"] is True
    assert capabilities["outbound_video"]["runtime_status"] == "streaming"


def test_webrtc_capabilities_report_live_audio_capture_health(monkeypatch):
    cfg = server._default_config()
    cfg["video_call"]["audio_sources"] = ["microphone", "speaker_loopback"]
    cfg["video_call"]["ai_audio_replies_enabled"] = False
    microphone = SimpleNamespace(
        capture_status=lambda: {
            "running": True,
            "device_open": True,
            "loopback": False,
            "device_name": "Synthetic USB Microphone",
            "capture_rate": 48000,
            "channels": 1,
            "last_error": "",
        }
    )
    loopback = SimpleNamespace(
        capture_status=lambda: {
            "running": True,
            "device_open": False,
            "loopback": True,
            "device_name": "",
            "capture_rate": 0,
            "channels": 0,
            "last_error": "Synthetic loopback temporarily unavailable",
        }
    )
    monkeypatch.setattr(
        server.STATE,
        "local_audio_tracks",
        {"synthetic-session": [microphone, loopback]},
        raising=False,
    )

    capabilities = server._build_webrtc_capabilities(cfg=cfg, include_admin=True)
    sources = {item["id"]: item for item in capabilities["audio"]["mixer"]["sources"]}

    assert sources["microphone"]["state"] == "active"
    assert sources["microphone"]["active"] is True
    assert sources["microphone"]["capture"]["device_name"] == "Synthetic USB Microphone"
    assert sources["speaker_loopback"]["state"] == "retrying"
    assert sources["speaker_loopback"]["active"] is False
    assert "temporarily unavailable" in sources["speaker_loopback"]["capture"]["last_error"]


def test_webrtc_capabilities_report_missing_computer_sound_loopback(monkeypatch):
    cfg = server._default_config()
    cfg["video_call"]["audio_sources"] = ["speaker_loopback"]
    cfg["video_call"]["ai_audio_replies_enabled"] = False
    monkeypatch.setattr(
        server,
        "_enumerate_webrtc_audio_devices_payload",
        lambda **_kwargs: {
            "loopback_available": False,
            "loopback_reason": "No compatible computer-sound loopback input was found.",
        },
    )
    monkeypatch.setattr(server.STATE, "local_audio_tracks", {}, raising=False)

    capabilities = server._build_webrtc_capabilities(cfg=cfg, include_admin=True)
    source = capabilities["audio"]["mixer"]["sources"][0]

    assert source["id"] == "speaker_loopback"
    assert source["label"] == "Computer sound unavailable."
    assert source["state"] == "unavailable"
    assert source["active"] is False
    assert source["reason"] == "No compatible computer-sound loopback input was found."
    assert capabilities["audio"]["loopback_available"] is False
    assert capabilities["audio"]["loopback_reason"] == "No compatible computer-sound loopback input was found."


def test_configured_remote_desktop_outbound_track_uses_capture_profile(monkeypatch):
    class FakeDesktopTrack:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    cfg = server._default_config()
    cfg["video_call"]["remote_desktop"]["monitor_id"] = 2
    cfg["video_call"]["remote_desktop"]["quality"] = "high"
    monkeypatch.setattr(server, "RemoteDesktopVideoStreamTrack", FakeDesktopTrack)
    monkeypatch.setattr(server, "_get_remote_desktop_video_available", lambda **_kwargs: True)

    track = server._create_configured_outbound_video_track(cfg=cfg)

    assert track.kwargs == {"monitor_id": 2, "fps": 20, "max_width": 1920}


def test_probed_missing_camera_is_not_an_active_outbound_source(monkeypatch):
    cfg = server._default_config()
    cfg["video_call"]["outbound_sources"] = ["remote_desktop", "camera"]
    monkeypatch.setattr(server, "create_outbound_video_track", object())
    monkeypatch.setattr(server, "_get_remote_desktop_video_available", lambda **_kwargs: True)
    monkeypatch.setattr(server, "_is_remote_desktop_agent_installed", lambda: True)
    monkeypatch.setattr(server, "_get_agent_frontend_enabled", lambda _name, *, cfg=None: True)
    monkeypatch.setattr(server, "RemoteDesktopVideoStreamTrack", object())
    monkeypatch.setattr(
        server,
        "_get_cached_webrtc_device_payload",
        lambda key: {"devices": [{"id": 0, "available": False}]} if key == "camera:0:True" else None,
    )

    assert server._get_camera_video_available(cfg=cfg) is False
    assert server._get_available_video_outbound_sources(cfg=cfg) == ["remote_desktop"]


def test_apply_video_call_settings_stops_agent_processing_when_disabled():
    class DummyAudioSink:
        def __init__(self):
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1

    class DummyAudioManager:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    async def run() -> None:
        webrtc = server.WebRTCManager()
        audio_sink = DummyAudioSink()
        audio_manager = DummyAudioManager()
        worker = None
        original_config = server.STATE.config
        original_audio_managers = dict(server.STATE.audio_managers)
        try:
            cfg = server._default_config()
            cfg["video_call"]["disable_autoyou_agents"] = True
            server.STATE.config = cfg
            server.STATE.audio_managers.clear()
            server.STATE.audio_managers.update(
                {
                    "session-1": audio_manager,
                    "session-alias": audio_manager,
                }
            )
            webrtc.audio_sinks["session-1"] = audio_sink
            webrtc.voice_command_queues["session-1"] = asyncio.Queue(maxsize=2)
            worker = asyncio.create_task(webrtc._voice_command_worker("session-1"))
            webrtc.voice_command_workers["session-1"] = worker
            webrtc.pending_voice_chat_messages["session-1"] = ["queued transcript"]
            chat_message = SimpleNamespace(payload={"metadata": {"source": "manual_chat"}})
            webrtc._offline_pending_messages["session-1"] = [
                {
                    "enqueued_at": 1.0,
                    "label": "voice reply",
                    "message": SimpleNamespace(payload={"metadata": {"source": "voice_call"}}),
                },
                {
                    "enqueued_at": 1.0,
                    "label": "chat message",
                    "message": chat_message,
                },
            ]

            await webrtc.apply_video_call_settings()

            assert audio_sink.stop_calls == 1
            assert audio_manager.close_calls == 1
            assert webrtc.audio_sinks == {}
            assert webrtc.voice_command_queues == {}
            assert webrtc.voice_command_workers == {}
            assert webrtc.pending_voice_chat_messages == {}
            assert webrtc._offline_pending_messages == {
                "session-1": [
                    {
                        "enqueued_at": 1.0,
                        "label": "chat message",
                        "message": chat_message,
                    }
                ]
            }
            assert server.STATE.audio_managers == {}
            assert worker.done()
        finally:
            if worker is not None and not worker.done():
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
            server.STATE.config = original_config
            server.STATE.audio_managers.clear()
            server.STATE.audio_managers.update(original_audio_managers)

    asyncio.run(run())


def test_apply_video_call_settings_updates_active_remote_desktop_profile(monkeypatch):
    class FakeDesktopTrack:
        def __init__(self):
            self.monitor_id = 0
            self.disabled = False
            self.profile = None

        def disable(self):
            self.disabled = True

        def apply_remote_desktop_profile(self, **profile):
            self.profile = profile
            self.monitor_id = profile["monitor_id"]

    async def run() -> None:
        webrtc = server.WebRTCManager()
        track = FakeDesktopTrack()
        cfg = server._default_config()
        cfg["video_call"]["remote_desktop"]["monitor_id"] = 3
        cfg["video_call"]["remote_desktop"]["quality"] = "ultra"

        original_config = server.STATE.config
        try:
            server.STATE.config = cfg
            monkeypatch.setattr(server, "_is_remote_desktop_agent_installed", lambda: True)
            monkeypatch.setattr(server, "_get_agent_frontend_enabled", lambda agent_name, *, cfg=None: True)
            monkeypatch.setattr(server, "RemoteDesktopVideoStreamTrack", object())
            webrtc.desktop_video_tracks["session-1"] = track
            webrtc.desktop_video_tracks["session-alias"] = track

            await webrtc.apply_video_call_settings()

            assert track.monitor_id == 3
            assert track.profile == {"monitor_id": 3, "fps": 24, "max_width": 2560}
            assert track.disabled is False
        finally:
            server.STATE.config = original_config

    asyncio.run(run())


def test_apply_video_call_settings_disables_only_remote_child_when_camera_remains(monkeypatch):
    class FakeCompositeTrack:
        is_enabled = True

        def __init__(self):
            self.policy = []
            self.disable_calls = 0

        def set_remote_desktop_enabled(self, enabled):
            self.policy.append(bool(enabled))

        def disable(self):
            self.disable_calls += 1

    async def run() -> None:
        session_id = "synthetic-composite-policy"
        webrtc = server.WebRTCManager()
        track = FakeCompositeTrack()
        webrtc.desktop_video_tracks[session_id] = track
        lease = webrtc._store_remote_desktop_control_lease(
            session_id,
            control_id="synthetic-policy-control",
            touch_mode="direct",
            track=track,
        )
        lease["held_buttons"].add("left")
        released = []
        cfg = server._default_config()
        cfg["video_call"]["outbound_sources"] = ["remote_desktop", "camera"]
        cfg["video_call"]["remote_desktop"]["send_screen"] = False
        original_config = server.STATE.config
        try:
            server.STATE.config = cfg
            monkeypatch.setattr(server, "_get_outbound_video_available", lambda **_kwargs: True)
            monkeypatch.setattr(
                server,
                "_get_available_video_outbound_sources",
                lambda **_kwargs: ["camera"],
            )
            monkeypatch.setattr(
                server,
                "_get_remote_desktop_control_available",
                lambda **_kwargs: False,
            )
            monkeypatch.setattr(
                server,
                "release_remote_desktop_inputs",
                lambda buttons=(), **kwargs: released.append(set(buttons)),
            )

            await webrtc.apply_video_call_settings()

            assert track.policy == [False]
            assert track.disable_calls == 0
            assert released == [{"left"}]
            assert not webrtc.remote_desktop_control_leases_by_session
        finally:
            server.STATE.config = original_config

    asyncio.run(run())


def test_remote_desktop_sender_applies_bitrate(monkeypatch):
    class FakeEncoder:
        target_bitrate = 9_000_000

    class FakeSender:
        def __init__(self):
            self._RTCRtpSender__encoder = FakeEncoder()
            self.track = None

        def replaceTrack(self, track):
            self.track = track

    async def run() -> None:
        webrtc = server.WebRTCManager()
        sender = FakeSender()
        transceiver = SimpleNamespace(sender=sender, direction="inactive")
        track = SimpleNamespace()
        cfg = server._default_config()
        cfg["video_call"]["remote_desktop"]["bitrate_kbps"] = 2800
        original_config = server.STATE.config
        try:
            server.STATE.config = cfg
            monkeypatch.setattr(
                server,
                "_get_available_video_outbound_sources",
                lambda **_kwargs: ["remote_desktop"],
            )
            webrtc._configure_remote_desktop_video_sender(
                "synthetic-bitrate-session",
                transceiver,
                track,
            )
            await asyncio.sleep(0)

            assert sender.track is track
            assert transceiver.direction == "sendrecv"
            assert sender._RTCRtpSender__encoder.target_bitrate == 2_800_000
            task = track._autoyou_desktop_video_bitrate_task
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert task.done()
        finally:
            server.STATE.config = original_config

    asyncio.run(run())


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["handle_session_offer", "handle_autopair_offer"])
@pytest.mark.parametrize("bitrate,codec", [(1500, "video/VP8"), (3000, "video/H264")])
@pytest.mark.parametrize("media_order", [("audio", "video"), ("video", "audio"), ("video",)])
async def test_desktop_bitrate_selects_codec_before_offer_is_negotiated(monkeypatch, handler, bitrate, codec, media_order):
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
    from aiortc.sdp import SessionDescription

    cfg = server._default_config()
    cfg["video_call"]["remote_desktop"]["bitrate_kbps"] = bitrate
    monkeypatch.setattr(server.STATE, "config", cfg)
    monkeypatch.setattr(server.STATE, "session_cache", {})
    monkeypatch.setattr(server, "AudioManager", None)
    monkeypatch.setattr(server, "TTSAudioStreamTrack", None)
    monkeypatch.setattr("aioice.ice.get_host_addresses", lambda **kw: ["127.0.0.1"])
    monkeypatch.setattr(server, "_get_available_video_outbound_sources", lambda **kw: ["remote_desktop"])
    monkeypatch.setattr(server, "_get_outbound_video_available", lambda **kw: True)
    peers = []
    def make_peer(*args, **kwargs):
        peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        peers.append(peer)
        return peer
    monkeypatch.setattr(server, "RTCPeerConnection", make_peer)
    manager = server.WebRTCManager()
    monkeypatch.setattr(manager, "_arm_session_establishment_timeout", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_publish_voice_call_status", AsyncMock())
    monkeypatch.setattr(manager, "_handle_transport_disconnect_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_create_datachannel_handler",
                        lambda *args, **kwargs: lambda channel: channel.on("message", channel.send))
    client = make_peer()
    # Native desktop offers audio first. Its initial BUNDLE sections have
    # distinct ICE credentials, so selecting the wrong section breaks chat.
    for kind in media_order:
        client.addTransceiver(kind, direction="recvonly")
    channel = client.createDataChannel("chat")
    received = asyncio.Event()
    channel.on("open", lambda: channel.send("synthetic-connectivity-check"))
    channel.on("message", lambda message: received.set() if message == "synthetic-connectivity-check" else None)
    try:
        offer = await client.createOffer()
        await client.setLocalDescription(offer)
        payload = {"sdp": client.localDescription.sdp, "type": offer.type, "iceServers": []}
        if handler == "handle_autopair_offer":
            payload = {"offer": payload, "iceServers": []}
        answer = await getattr(manager, handler)("synthetic-video-codec", payload)
        media = SessionDescription.parse(answer["sdp"]).media
        assert next(m for m in media if m.kind == "video").rtp.codecs[0].mimeType == codec
        assert manager.desktop_video_tracks == {}, "Negotiation must not open screen capture"
        await client.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
        await asyncio.wait_for(received.wait(), timeout=5)
        assert all(peer.connectionState == "connected" for peer in peers)
    finally:
        for peer in peers:
            await peer.close()


@pytest.mark.parametrize(("source", "platform"), [
    ("autoyou_lite", "ios"), ("autoyou_lite", "android"),
    ("autoyou_lite", "chrome"),
    ("autoyou_desktop", "macos"), ("autoyou_desktop", "windows"),
])
def test_native_call_remote_desktop_lease_routes_mouse_and_keyboard_to_trusted_session(monkeypatch, source, platform):
    class FakeDesktopTrack:
        is_enabled = True
        pane_width = 1.0

        def remote_desktop_mapping(self):
            return {
                "content_rect": {"x": 0.0, "y": 0.0, "width": self.pane_width, "height": 1.0},
                "monitor_bounds": {"left": 0, "top": 0, "width": 1920, "height": 1080},
            }

        def map_output_point_to_desktop(self, x, y):
            return round(float(x) * 1919), round(float(y) * 1079)

    async def run() -> None:
        session_id = "synthetic-native-control"
        webrtc = server.WebRTCManager()
        track = FakeDesktopTrack()
        manager = SimpleNamespace(send_message=AsyncMock(return_value=True))
        webrtc.desktop_video_tracks[session_id] = track
        webrtc.datachannel_managers[session_id] = manager
        applied_mouse = []
        applied_keyboard = []
        released = []
        released_keys = []

        cfg = server._default_config()
        cfg["video_call"]["remote_desktop"]["control_enabled"] = True
        original_config = server.STATE.config
        try:
            server.STATE.config = cfg
            monkeypatch.setattr(server, "remote_desktop_input_backend_probed", lambda: True)
            monkeypatch.setattr(server, "remote_desktop_input_backend_available", lambda refresh=False: True)
            monkeypatch.setattr(
                server,
                "execute_remote_desktop_input",
                lambda payload, *, track: applied_mouse.append((payload, track)) or True,
            )
            monkeypatch.setattr(
                server,
                "execute_remote_desktop_keyboard",
                lambda payload, **kwargs: applied_keyboard.append(payload) or True,
            )
            monkeypatch.setattr(
                server,
                "release_remote_desktop_inputs",
                lambda buttons=(), *, held_keys=(), **kwargs: (released.append(set(buttons)), released_keys.append(set(held_keys))),
            )

            def message(payload):
                # The transport passes its bound session separately; host input
                # must never trust a conflicting payload header.
                return SimpleNamespace(
                    header=SimpleNamespace(session_id="untrusted-header"),
                    payload=payload,
                )

            start_payload = {
                    "event": "remote_desktop_control",
                    "action": "start",
                    "control_id": "native-control-1",
                    "source": source,
                    "platform": platform,
                    "fullscreen": True,
                    "touch_mode": "relative",
                }
            await webrtc._handle_voice_call_control_message(
                message(start_payload),
                trusted_session_id=session_id,
            )
            assert session_id not in webrtc.remote_desktop_control_leases_by_session
            assert "video call" in manager.send_message.await_args.args[0].payload["reason"].lower()

            webrtc._set_voice_call_client_active(session_id, True)
            await webrtc._handle_voice_call_control_message(
                message(start_payload),
                trusted_session_id=session_id,
            )
            lease = webrtc.remote_desktop_control_leases_by_session[session_id]
            assert lease["control_id"] == "native-control-1"
            assert lease["touch_mode"] == "relative"

            await webrtc._handle_voice_call_control_message(
                message({
                    "event": "remote_desktop_input",
                    "control_id": "native-control-1",
                    "source": source,
                    "platform": platform,
                    "input_type": "button",
                    "button": "left",
                    "phase": "down",
                    "x": 0.5,
                    "y": 0.5,
                }),
                trusted_session_id=session_id,
            )
            await webrtc._handle_voice_call_control_message(
                message({
                    "event": "remote_desktop_keyboard",
                    "control_id": "native-control-1",
                    "source": source,
                    "platform": platform,
                    "action": "input",
                    "text": "synthetic input",
                }),
                trusted_session_id=session_id,
            )

            await webrtc._handle_voice_call_control_message(
                message({"event": "remote_desktop_keyboard", "control_id": "native-control-1",
                         "source": source, "platform": platform, "action": "key", "key": "w", "phase": "down"}),
                trusted_session_id=session_id,
            )
            assert lease["held_keys"] == {"w"}
            assert applied_mouse[0][1] is track
            assert applied_keyboard[0]["text"] == "synthetic input"
            assert lease["held_buttons"] == {"left"}

            await webrtc._handle_voice_call_control_message(
                message({
                    "event": "remote_desktop_control",
                    "action": "stop",
                    "control_id": "native-control-1",
                    "source": source,
                    "platform": platform,
                }),
                trusted_session_id=session_id,
            )

            assert session_id not in webrtc.remote_desktop_control_leases_by_session
            assert released == [{"left"}]
            assert released_keys == [{"w"}]
            assert any(call.args[0].payload["active"] is True for call in manager.send_message.await_args_list)
            assert manager.send_message.await_args_list[-1].args[0].payload["active"] is False
            # A changed video composition must not redirect an old cursor point
            # to a different part of the host screen, or leave held keys down.
            await webrtc._handle_voice_call_control_message(message(start_payload), trusted_session_id=session_id)
            await webrtc._handle_voice_call_control_message(message({
                "event": "remote_desktop_keyboard", "source": source, "platform": platform,
                "control_id": "native-control-1", "action": "key", "key": "w", "phase": "down",
            }), trusted_session_id=session_id)
            track.pane_width = 0.5
            before_inputs = len(applied_mouse)
            await webrtc._handle_voice_call_control_message(message({
                "event": "remote_desktop_input", "source": source, "platform": platform,
                "control_id": "native-control-1", "input_type": "move", "x": 0.5, "y": 0.5,
            }), trusted_session_id=session_id)
            assert len(applied_mouse) == before_inputs
            assert session_id not in webrtc.remote_desktop_control_leases_by_session
            assert released_keys[-1] == {"w"}
            assert "layout changed" in manager.send_message.await_args.args[0].payload["reason"]
        finally:
            server.STATE.config = original_config

    asyncio.run(run())


def test_native_call_remote_desktop_lease_expiry_releases_held_buttons(monkeypatch):
    async def run() -> None:
        webrtc = server.WebRTCManager()
        released = []
        released_event = threading.Event()

        def release(buttons=(), **kwargs):
            released.append(set(buttons))
            released_event.set()

        monkeypatch.setattr(server, "_REMOTE_DESKTOP_CONTROL_LEASE_SECONDS", 0.01)
        monkeypatch.setattr(server, "release_remote_desktop_inputs", release)
        lease = webrtc._store_remote_desktop_control_lease(
            "synthetic-expiring-control",
            control_id="expiring-control",
            touch_mode="direct",
            track=object(),
        )
        lease["held_buttons"].add("left")

        assert await asyncio.to_thread(released_event.wait, 1.0)
        assert released == [{"left"}]
        assert not webrtc.remote_desktop_control_leases_by_session

    asyncio.run(run())


def test_native_call_remote_desktop_stop_releases_inflight_mouse_down(monkeypatch):
    class FakeDesktopTrack:
        is_enabled = True

        def remote_desktop_mapping(self):
            return {
                "content_rect": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                "monitor_bounds": {"left": 0, "top": 0, "width": 1920, "height": 1080},
            }

        def map_output_point_to_desktop(self, x, y):
            return round(float(x) * 1919), round(float(y) * 1079)

    async def run() -> None:
        session_id = "synthetic-native-race"
        webrtc = server.WebRTCManager()
        track = FakeDesktopTrack()
        webrtc.desktop_video_tracks[session_id] = track
        webrtc.datachannel_managers[session_id] = SimpleNamespace(
            send_message=AsyncMock(return_value=True)
        )
        webrtc._set_voice_call_client_active(session_id, True)
        webrtc._store_remote_desktop_control_lease(
            session_id,
            control_id="race-control",
            touch_mode="direct",
            track=track,
        )

        input_started = threading.Event()
        allow_input_to_finish = threading.Event()
        released = []

        def blocking_mouse_down(payload, *, track):
            input_started.set()
            assert allow_input_to_finish.wait(2.0)
            return True

        cfg = server._default_config()
        cfg["video_call"]["remote_desktop"]["control_enabled"] = True
        original_config = server.STATE.config
        try:
            server.STATE.config = cfg
            monkeypatch.setattr(server, "remote_desktop_input_backend_probed", lambda: True)
            monkeypatch.setattr(server, "execute_remote_desktop_input", blocking_mouse_down)
            monkeypatch.setattr(
                server,
                "release_remote_desktop_inputs",
                lambda buttons=(), **kwargs: released.append(set(buttons)),
            )

            input_task = asyncio.create_task(
                webrtc._handle_remote_desktop_input(
                    session_id,
                    {
                        "event": "remote_desktop_input",
                        "control_id": "race-control",
                        "source": "autoyou_lite",
                        "platform": "android",
                        "input_type": "button",
                        "button": "left",
                        "phase": "down",
                        "x": 0.5,
                        "y": 0.5,
                    },
                )
            )
            assert await asyncio.to_thread(input_started.wait, 1.0)
            stop_task = asyncio.create_task(
                webrtc._handle_remote_desktop_control(
                    session_id,
                    {
                        "event": "remote_desktop_control",
                        "action": "stop",
                        "control_id": "race-control",
                        "source": "autoyou_lite",
                        "platform": "android",
                    },
                )
            )
            await asyncio.sleep(0)
            assert not stop_task.done()

            allow_input_to_finish.set()
            await asyncio.gather(input_task, stop_task)

            assert released == [{"left"}]
            assert session_id not in webrtc.remote_desktop_control_leases_by_session
        finally:
            allow_input_to_finish.set()
            server.STATE.config = original_config

    asyncio.run(run())


def test_video_state_resolves_cloud_pair_session_alias(monkeypatch):
    class FakeDesktopTrack:
        def __init__(self):
            self.enabled = False
            self.disabled = False

        def enable(self):
            self.enabled = True

        def disable(self):
            self.disabled = True

    async def run() -> None:
        webrtc = server.WebRTCManager()
        datachannel_manager = object()
        webrtc.datachannel_managers["cloud-relay-session"] = datachannel_manager
        webrtc.datachannel_managers["stable-client-device"] = datachannel_manager
        webrtc._voice_dc_session_id["cloud-relay-session"] = "stable-client-device"

        desktop_track = FakeDesktopTrack()
        webrtc.desktop_video_tracks["cloud-relay-session"] = desktop_track

        monkeypatch.setattr(server, "_get_outbound_video_available", lambda *, cfg=None: True)
        message = SimpleNamespace(
            header=SimpleNamespace(session_id="stable-client-device"),
            payload={
                "event": "video_state",
                "active": True,
                "platform": "ios",
            },
        )

        await webrtc._handle_voice_call_control_message(message)

        assert desktop_track.enabled is True
        assert desktop_track.disabled is False

    asyncio.run(run())


def test_video_state_lazily_attaches_outbound_track(monkeypatch):
    class FakeDesktopTrack:
        def __init__(self):
            self.enabled = False

        def enable(self):
            self.enabled = True

    class FakeTransceiver:
        kind = "video"

    class FakePeerConnection:
        def getTransceivers(self):
            return [FakeTransceiver()]

    async def run() -> None:
        webrtc = server.WebRTCManager()
        webrtc.session_peers["synthetic-session"] = FakePeerConnection()
        track = FakeDesktopTrack()
        attached = []
        monkeypatch.setattr(server, "_get_outbound_video_available", lambda *, cfg=None: True)
        monkeypatch.setattr(server, "_create_configured_outbound_video_track", lambda *, cfg=None: track)

        def configure(session_id, transceiver, configured_track):
            attached.append((session_id, transceiver, configured_track))
            webrtc.desktop_video_tracks[session_id] = configured_track

        monkeypatch.setattr(webrtc, "_configure_remote_desktop_video_sender", configure)
        message = SimpleNamespace(
            header=SimpleNamespace(session_id="synthetic-session"),
            payload={"event": "video_state", "active": True, "platform": "windows"},
        )

        await webrtc._handle_voice_call_control_message(message)

        assert len(attached) == 1
        assert attached[0][0] == "synthetic-session"
        assert attached[0][2] is track
        assert track.enabled is True

    asyncio.run(run())


def test_computer_sound_follows_screen_sharing(monkeypatch):
    class FakeDesktopTrack:
        enabled = False

        def enable(self):
            self.enabled = True

        def disable(self):
            self.enabled = False

        def is_enabled(self):
            return self.enabled

    class FakeSender:
        track = None

        def replaceTrack(self, track):
            self.track = track

    async def run() -> None:
        webrtc = server.WebRTCManager()
        sender = FakeSender()
        webrtc.audio_transceivers["synthetic-session"] = SimpleNamespace(sender=sender, direction="sendrecv")
        webrtc.desktop_video_tracks["synthetic-session"] = FakeDesktopTrack()
        webrtc.voice_call_client_active_by_session["synthetic-session"] = True
        cfg = server._default_config()
        original_config = server.STATE.config
        loopback_requests = []

        def create_audio_track(**kwargs):
            loopback_requests.append(kwargs["include_loopback"])
            return object()

        monkeypatch.setattr(server, "_create_configured_outbound_audio_track", create_audio_track)
        monkeypatch.setattr(server, "_get_outbound_video_available", lambda *, cfg=None: True)
        try:
            server.STATE.config = cfg
            webrtc._restore_outbound_audio_for_call("synthetic-session")
            assert loopback_requests == [False]
            for active in (True, False):
                await webrtc._handle_voice_call_control_message(SimpleNamespace(
                    header=SimpleNamespace(session_id="synthetic-session"),
                    payload={"event": "video_state", "active": active, "platform": "ios"},
                ))
            assert loopback_requests == [False, True, False]
        finally:
            server.STATE.config = original_config

    asyncio.run(run())


def test_video_state_camera_off_clears_retained_frames_for_session_aliases(monkeypatch):
    class FakeRegistry:
        def __init__(self):
            self.cleared = []

        def clear_session(self, session_id):
            self.cleared.append(session_id)

    async def run() -> None:
        webrtc = server.WebRTCManager()
        datachannel_manager = object()
        webrtc.datachannel_managers["cloud-relay-session"] = datachannel_manager
        webrtc.datachannel_managers["stable-client-device"] = datachannel_manager
        webrtc._voice_dc_session_id["cloud-relay-session"] = "stable-client-device"
        registry = FakeRegistry()
        monkeypatch.setattr(server, "VIDEO_FRAME_REGISTRY", registry)

        message = SimpleNamespace(
            header=SimpleNamespace(session_id="stable-client-device"),
            payload={
                "event": "video_state",
                "active": True,
                "camera_active": False,
                "platform": "ios",
            },
        )

        await webrtc._handle_voice_call_control_message(message)

        assert set(registry.cleared) == {"cloud-relay-session", "stable-client-device"}

    asyncio.run(run())


def test_background_audio_offer_state_maps_ios_modes():
    webrtc = server.WebRTCManager()
    cfg = server._default_config()
    cfg["video_call"]["background_mode_enabled"] = True
    cfg["video_call"]["silent_recording_enabled"] = True

    keepalive = webrtc._background_audio_offer_state(
        {
            "background_audio": {
                "active": True,
                "silent_recording": False,
                "client_audio_direction": "sendrecv",
                "platform": "ios",
            }
        },
        "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n",
        cfg=cfg,
    )
    recording = webrtc._background_audio_offer_state(
        {
            "background_audio": {
                "active": True,
                "silent_recording": True,
                "client_audio_direction": "sendonly",
                "platform": "ios",
            }
        },
        "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendonly\r\n",
        cfg=cfg,
    )
    android_keepalive = webrtc._background_audio_offer_state(
        {
            "background_audio": {
                "active": True,
                "muted": True,
                "silent_recording": False,
                "client_audio_direction": "inactive",
                "platform": "android",
            }
        },
        "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=inactive\r\n",
        cfg=cfg,
    )
    legacy_recvonly = webrtc._background_audio_offer_state(
        {},
        "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=recvonly\r\n",
    )

    assert keepalive["active"] is True
    assert keepalive["silent_recording"] is False
    assert keepalive["client_audio_direction"] == "recvonly"
    assert keepalive["server_audio_direction"] == "sendrecv"
    assert keepalive["muted"] is True
    assert recording["active"] is True
    assert recording["silent_recording"] is True
    assert recording["server_audio_direction"] == "recvonly"
    assert android_keepalive["active"] is True
    assert android_keepalive["silent_recording"] is False
    assert android_keepalive["muted"] is True
    assert android_keepalive["client_audio_direction"] == "inactive"
    assert android_keepalive["server_audio_direction"] == "inactive"
    assert legacy_recvonly["active"] is False


def test_sdp_audio_media_gate_rejects_datachannel_only_offer():
    datachannel_only_sdp = (
        "v=0\r\n"
        "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
        "a=sctp-port:5000\r\n"
    )
    voice_ready_sdp = (
        "v=0\r\n"
        "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
        "a=sendrecv\r\n"
        "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
    )

    assert server.WebRTCManager._sdp_has_media_section(datachannel_only_sdp, "audio") is False
    assert server.WebRTCManager._sdp_has_media_section(voice_ready_sdp, "audio") is True


def test_background_audio_offer_state_obeys_video_call_settings():
    webrtc = server.WebRTCManager()
    cfg = server._default_config()
    offer = {
        "background_audio": {
            "active": True,
            "silent_recording": False,
            "client_audio_direction": "recvonly",
            "platform": "ios",
        }
    }
    sdp = "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n"

    disabled = webrtc._background_audio_offer_state(offer, sdp, cfg=cfg)
    assert disabled["active"] is False
    assert disabled["server_audio_direction"] == "inactive"

    cfg["video_call"]["background_mode_enabled"] = True
    enabled = webrtc._background_audio_offer_state(offer, sdp, cfg=cfg)
    assert enabled["active"] is True
    assert enabled["server_audio_direction"] == "sendrecv"

    cfg["video_call"]["background_mode_enabled"] = False
    cfg["video_call"]["silent_recording_enabled"] = True
    safety_offer = {
        "background_audio": {
            "active": True,
            "silent_recording": True,
            "client_audio_direction": "sendonly",
            "platform": "ios",
        }
    }
    safety = webrtc._background_audio_offer_state(
        safety_offer,
        "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n",
        cfg=cfg,
    )
    assert safety["active"] is True
    assert safety["silent_recording"] is True
    assert safety["server_audio_direction"] == "sendrecv"


def test_background_audio_offer_state_fail_closed_when_both_settings_disabled():
    """Matrix case: client enables Background mode + Safety Recording while the
    server has BOTH Video & Calls toggles off. The server must refuse the
    request entirely (no background channel, no recording, muted answer)."""
    webrtc = server.WebRTCManager()
    cfg = server._default_config()
    assert cfg["video_call"]["background_mode_enabled"] is False
    assert cfg["video_call"]["silent_recording_enabled"] is False

    refused = webrtc._background_audio_offer_state(
        {
            "background_audio": {
                "active": True,
                "silent_recording": True,
                "client_audio_direction": "sendonly",
                "platform": "ios",
            }
        },
        "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n",
        cfg=cfg,
    )

    assert refused["active"] is False
    assert refused["silent_recording"] is False
    assert refused["muted"] is True
    assert refused["server_audio_direction"] == "inactive"


def test_call_audio_master_gate_overrides_background_settings():
    webrtc = server.WebRTCManager()
    cfg = server._default_config()
    cfg["video_call"].update(
        {
            "audio_enabled": False,
            "background_mode_enabled": True,
            "silent_recording_enabled": True,
        }
    )
    sdp = "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n"

    for silent_recording in (False, True):
        refused = webrtc._background_audio_offer_state(
            {
                "background_audio": {
                    "active": True,
                    "silent_recording": silent_recording,
                    "client_audio_direction": "sendonly" if silent_recording else "recvonly",
                    "platform": "ios",
                }
            },
            sdp,
            cfg=cfg,
        )
        assert refused["active"] is False
        assert refused["silent_recording"] is False
        assert refused["server_audio_direction"] == "inactive"

    capabilities = server._build_webrtc_capabilities(cfg=cfg)
    assert capabilities["audio"]["enabled"] is False
    assert capabilities["audio"]["background_mode"]["enabled"] is False
    assert capabilities["audio"]["silent_recording"]["enabled"] is False


def test_webrtc_capabilities_advertise_background_policy_for_clients():
    """Clients clamp their microphone capture against these two flags; they
    must always be present in the public (non-admin) capabilities payload."""
    original_config = server.STATE.config
    try:
        cfg = server._default_config()
        server.STATE.config = cfg
        capabilities = server._build_webrtc_capabilities(cfg=cfg)
        assert capabilities["audio"]["background_mode"]["enabled"] is False
        assert capabilities["audio"]["silent_recording"]["enabled"] is False

        cfg["video_call"]["background_mode_enabled"] = True
        cfg["video_call"]["silent_recording_enabled"] = True
        capabilities = server._build_webrtc_capabilities(cfg=cfg)
        assert capabilities["audio"]["background_mode"]["enabled"] is True
        assert capabilities["audio"]["silent_recording"]["enabled"] is True
    finally:
        server.STATE.config = original_config


def test_background_audio_control_suppresses_and_restores_outbound_audio(monkeypatch):
    class FakeSender:
        def __init__(self):
            self.track = object()

        def replaceTrack(self, track):
            self.track = track

    class FakeTransceiver:
        def __init__(self):
            self.sender = FakeSender()
            self.direction = "sendrecv"

    class FakeAudioManager:
        def __init__(self):
            self.stop_calls = []
            self.playback_stop_calls = []
            self.tts_track = None

        def stop_speaking(self, *, source):
            self.stop_calls.append(source)

        def set_tts_track(self, track):
            self.tts_track = track

        def stop_playback(self, *, source):
            self.playback_stop_calls.append(source)

    class FakeLocalAudioTrack:
        def __init__(self):
            self.disabled = False

        def disable(self):
            self.disabled = True

    async def run() -> None:
        webrtc = server.WebRTCManager()
        transceiver = FakeTransceiver()
        audio_manager = FakeAudioManager()
        local_audio_track = FakeLocalAudioTrack()
        heartbeat_track = object()
        restored_track = object()
        original_audio_managers = dict(server.STATE.audio_managers)
        original_local_audio_tracks = dict(getattr(server.STATE, "local_audio_tracks", {}))
        original_config = server.STATE.config
        try:
            server.STATE.audio_managers.clear()
            cfg = server._default_config()
            cfg["video_call"]["background_mode_enabled"] = True
            server.STATE.config = cfg
            server.STATE.audio_managers["session-1"] = audio_manager
            monkeypatch.setattr(server.STATE, "local_audio_tracks", {"session-1": local_audio_track}, raising=False)
            monkeypatch.setattr(server, "BackgroundAudioHeartbeatTrack", lambda: heartbeat_track)
            monkeypatch.setattr(server, "TTSAudioStreamTrack", lambda: object())
            monkeypatch.setattr(server, "_create_configured_outbound_audio_track", lambda **_kwargs: restored_track)
            webrtc._register_audio_transceiver_aliases("session-1", transceiver)

            await webrtc._handle_voice_call_control_message(
                SimpleNamespace(
                    header=SimpleNamespace(session_id="session-1"),
                    payload={
                        "event": "background_audio_state",
                        "active": True,
                        "silent_recording": False,
                        "client_audio_direction": "recvonly",
                        "platform": "ios",
                    },
                )
            )

            ios_background_track = transceiver.sender.track
            assert ios_background_track is heartbeat_track
            assert ios_background_track is not restored_track
            assert transceiver.direction == "sendrecv"
            assert local_audio_track.disabled is True
            assert "background_audio_mode" in audio_manager.stop_calls
            assert "background_audio_mode" in audio_manager.playback_stop_calls

            await webrtc._handle_voice_call_control_message(
                SimpleNamespace(
                    header=SimpleNamespace(session_id="session-1"),
                    payload={"event": "call_state", "active": True, "platform": "ios"},
                )
            )

            assert transceiver.sender.track is restored_track
            assert transceiver.direction == "sendrecv"

            await webrtc._handle_voice_call_control_message(
                SimpleNamespace(
                    header=SimpleNamespace(session_id="session-1"),
                    payload={
                        "event": "background_audio_state",
                        "active": True,
                        "silent_recording": False,
                        "client_audio_direction": "recvonly",
                        "platform": "ios",
                    },
                )
            )

            assert transceiver.sender.track is restored_track
            assert transceiver.direction == "sendrecv"

            await webrtc._handle_voice_call_control_message(
                SimpleNamespace(
                    header=SimpleNamespace(session_id="session-1"),
                    payload={"event": "call_state", "active": False, "platform": "ios"},
                )
            )

            await webrtc._handle_voice_call_control_message(
                SimpleNamespace(
                    header=SimpleNamespace(session_id="session-1"),
                    payload={
                        "event": "background_audio_state",
                        "active": True,
                        "muted": True,
                        "silent_recording": False,
                        "client_audio_direction": "inactive",
                        "server_audio_direction": "inactive",
                        "platform": "android",
                    },
                )
            )

            assert transceiver.sender.track is heartbeat_track
            assert transceiver.direction == "sendrecv"
        finally:
            server.STATE.audio_managers.clear()
            server.STATE.audio_managers.update(original_audio_managers)
            server.STATE.local_audio_tracks = original_local_audio_tracks
            server.STATE.config = original_config

    asyncio.run(run())


def test_background_audio_control_ignores_missing_session_id(monkeypatch):
    async def run() -> None:
        webrtc = server.WebRTCManager()
        suppressed_sessions: list[str] = []
        monkeypatch.setattr(
            webrtc,
            "_suppress_outbound_audio_for_background",
            lambda session_id, **_kwargs: suppressed_sessions.append(str(session_id)),
        )

        await webrtc._handle_voice_call_control_message(
            SimpleNamespace(
                header=SimpleNamespace(session_id=""),
                payload={
                    "event": "background_audio_state",
                    "active": True,
                    "silent_recording": False,
                    "client_audio_direction": "recvonly",
                    "platform": "ios",
                },
            )
        )

        assert suppressed_sessions == []
        assert webrtc.background_audio_state_by_session == {}

    asyncio.run(run())


def test_admin_config_patch_persists_custom_audio_and_camera_settings():
    cfg, touched, _theme = server._apply_admin_ui_config_patch(
        server._default_config(),
        {
            "video_call": {
                "capture_audio": True,
                "input_audio_source": "desktop_loopback",
                "camera_device_id": 2,
            }
        },
    )
    assert "video_call" in touched
    assert cfg["video_call"]["capture_audio"] is True
    assert cfg["video_call"]["input_audio_source"] == "desktop_loopback"
    assert cfg["video_call"]["camera_device_id"] == 2


def test_admin_config_patch_persists_call_audio_recording_with_video_settings():
    cfg, touched, _theme = server._apply_admin_ui_config_patch(
        server._default_config(),
        {
            "video_call": {"enabled": True},
            "speech": {"voice_training": {"capture_enabled": True}},
        },
    )

    assert "video_call" in touched
    assert "speech" in touched
    assert cfg["speech"]["voice_training"]["capture_enabled"] is True


def test_safety_recording_default_dir_honors_test_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    cfg = server._default_config()
    cfg["video_call"]["silent_recording_dir"] = ""

    assert server._resolve_silent_recording_dir(cfg=cfg) == str(tmp_path / "AutoYou" / "output" / "safety-recordings")


def test_recording_default_paths_use_runtime_data_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    cfg = server._default_config()
    cfg["video_call"]["recording_dir"] = ""
    cfg["video_call"]["silent_recording_dir"] = ""

    paths = server._build_recording_paths_payload(cfg)

    assert server._resolve_video_recording_dir(cfg=cfg) == str(tmp_path / "AutoYou" / "output" / "video-recordings")
    assert paths["video_recording"]["default_dir"] == str(tmp_path / "AutoYou" / "output" / "video-recordings")
    assert paths["safety_recording"]["default_dir"] == str(tmp_path / "AutoYou" / "output" / "safety-recordings")
    assert paths["voice_training"]["default_dir"] == str(tmp_path / "AutoYou" / "voice_training")
    assert paths["location_recording"]["database_path"] == str(tmp_path / "location_agent" / "locations.sqlite3")
    assert not (tmp_path / "location_agent" / "locations.sqlite3").exists()


def test_saving_speech_settings_preserves_the_permissions_capture_toggle():
    config = server._default_config()
    config["speech"]["voice_training"]["capture_enabled"] = True

    updated, touched, _theme = server._apply_admin_ui_config_patch(config, {
        "speech": {"stt": {"model": "tiny.en"}},
    })

    assert "speech" in touched
    assert updated["speech"]["voice_training"]["capture_enabled"] is True


def test_admin_ui_groups_capture_toggles_under_local_permissions():
    from pathlib import Path

    script = (Path(server.__file__).resolve().parent / "assets" / "admin-ui.js").read_text(encoding="utf-8")
    permissions = script.split("function renderPermissionsScreen()", 1)[1].split("function renderVideoScreen()", 1)[0]
    video_settings = script.split("function renderVideoScreen()", 1)[1].split("function renderSpeechRecognitionBody()", 1)[0]

    assert 'id: "permissions", label: "Permissions"' in script
    for control in (
        'checkbox("videoCall.enabled"', 'checkbox("videoCall.audio_enabled"',
        'checkbox("videoCall.audio_microphone"', 'checkbox("videoCall.record_my_video"',
        'checkbox("videoCall.remote_desktop.send_screen"', 'checkbox("videoCall.outbound_remote_desktop"',
        'checkbox("speech.voice_training_capture_enabled"',
        'checkbox("aiAgent.record_messages_in_database"',
        'checkbox("page.admin_frontend_enabled"',
    ):
        assert control in permissions
        assert control not in video_settings
    assert '"/api/admin/permissions"' in script
    assert "permissions_editable" in script
    assert "location_recording.database_path" in permissions
    assert 'checkbox("audioPlayback.enabled", "Allow audio file playback"' in permissions
    assert 'audio_playback_enabled: "audioPlayback.enabled"' in script
    messaging = script.split("function renderMessagingScreen()", 1)[1].split("function renderPermissionsScreen()", 1)[0]
    assert "toggle-playback-enabled" not in messaging


def test_webrtc_capabilities_reflect_custom_audio_and_camera_settings(monkeypatch):
    cfg = server._default_config()
    cfg["video_call"]["capture_audio"] = True
    cfg["video_call"]["input_audio_source"] = "desktop_loopback"
    cfg["video_call"]["outbound_source"] = "camera"
    cfg["video_call"]["enabled"] = True

    monkeypatch.setattr(server, "_get_camera_video_available", lambda *, cfg=None: True)

    capabilities = server._build_webrtc_capabilities(cfg=cfg)
    assert capabilities["audio"]["capture_audio"] is True
    assert capabilities["audio"]["input_audio_source"] == "desktop_loopback"
    assert capabilities["audio"]["output_mode"] == "mixed"
    assert capabilities["audio"]["mixer"]["enabled"] is capabilities["audio"]["mixer"]["available"]
    assert capabilities["audio"]["mixer"]["sources"][-1]["id"] == "speaker_loopback"
    assert capabilities["audio"]["mixer"]["sources"][-1]["kind"] == "capture"
    assert capabilities["outbound_video"]["available_sources"]["camera"]["enabled"] is True


def test_camera_outbound_source_requires_opencv(monkeypatch):
    cfg = server._default_config()
    cfg["video_call"]["enabled"] = True
    cfg["video_call"]["outbound_source"] = "camera"
    cfg["video_call"]["outbound_sources"] = ["camera"]

    original_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "cv2":
            raise ModuleNotFoundError(name)
        return original_import_module(name)

    monkeypatch.setattr(server, "create_outbound_video_track", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(server.importlib, "import_module", fake_import_module)

    capabilities = server._build_webrtc_capabilities(cfg=cfg)
    assert server._get_outbound_video_available(cfg=cfg) is False
    assert capabilities["outbound_video"]["enabled"] is False
    assert capabilities["outbound_video"]["available_sources"]["camera"]["enabled"] is False


def test_unavailable_webcam_degrades_to_configured_screen(monkeypatch):
    cfg = server._default_config()
    cfg["video_call"]["enabled"] = True
    cfg["video_call"]["outbound_source"] = "remote_desktop"
    cfg["video_call"]["outbound_sources"] = ["remote_desktop", "camera"]

    screen_track = object()
    monkeypatch.setattr(server, "_get_remote_desktop_video_available", lambda *, cfg=None: True)
    monkeypatch.setattr(server, "_get_camera_video_available", lambda *, cfg=None: False)
    monkeypatch.setattr(server, "RemoteDesktopVideoStreamTrack", lambda **_kwargs: screen_track)

    capabilities = server._build_webrtc_capabilities(cfg=cfg)

    assert server._get_outbound_video_available(cfg=cfg) is True
    assert server._create_configured_outbound_video_track(cfg=cfg) is screen_track
    assert capabilities["outbound_video"]["enabled"] is True
    assert capabilities["outbound_video"]["source"] == "remote_desktop"
    assert capabilities["outbound_video"]["configured_source"] == "stitched"
    assert capabilities["outbound_video"]["active_sources"] == ["remote_desktop"]
    assert capabilities["outbound_video"]["unavailable_sources"] == ["camera"]
    assert capabilities["outbound_video"]["degraded"] is True


def test_configured_outbound_audio_capture_mixes_tts_and_playback_lanes(monkeypatch):
    from shared import video_call_manager

    class FakeLocalAudioInputTrack:
        def __init__(self, *, device_index_or_name, capture_loopback):
            self.device_index_or_name = device_index_or_name
            self.capture_loopback = capture_loopback
            self.enabled = False

        def enable(self):
            self.enabled = True

    class FakeMixedAudioStreamTrack:
        def __init__(self, sources):
            self.sources = list(sources)

        def source_names(self):
            return [name for name, _track in self.sources]

    cfg = server._default_config()
    cfg["video_call"]["capture_audio"] = True
    cfg["video_call"]["input_audio_source"] = "desktop_loopback"
    cfg["video_call"]["outbound_source"] = "remote_desktop"

    monkeypatch.setattr(video_call_manager, "LocalAudioInputTrack", FakeLocalAudioInputTrack)
    monkeypatch.setattr(server, "MixedAudioStreamTrack", FakeMixedAudioStreamTrack)
    monkeypatch.setattr(server.STATE, "local_audio_tracks", {}, raising=False)

    tts_track = object()
    playback_track = object()
    track = server._create_configured_outbound_audio_track(
        cfg=cfg,
        session_id="synthetic-session",
        tts_track=tts_track,
        playback_track=playback_track,
    )

    assert isinstance(track, FakeMixedAudioStreamTrack)
    assert track.sources[0] == ("ai_replies", tts_track)
    assert track.sources[1] == ("playback", playback_track)
    capture_name, capture_track = track.sources[2]
    assert capture_name == "speaker_loopback"
    assert capture_track.device_index_or_name == "desktop_loopback"
    assert capture_track.capture_loopback is True
    assert capture_track.enabled is True
    assert server.STATE.local_audio_tracks["synthetic-session"] == [capture_track]


def test_outbound_audio_tracks_are_independent_and_reused(monkeypatch):
    class FakeTrack:
        readyState = "live"

    class FakeAudioManager:
        tts_track = None
        playback_track = None

        def set_tts_track(self, track):
            self.tts_track = track

        def set_playback_track(self, track):
            self.playback_track = track

    created_tracks = []

    def create_track():
        track = FakeTrack()
        created_tracks.append(track)
        return track

    monkeypatch.setattr(server, "TTSAudioStreamTrack", create_track)
    manager = FakeAudioManager()
    cfg = server._default_config()

    first = server._ensure_audio_manager_outbound_tracks(
        audio_manager=manager,
        cfg=cfg,
    )
    second = server._ensure_audio_manager_outbound_tracks(
        audio_manager=manager,
        cfg=cfg,
    )

    assert len(created_tracks) == 2
    assert first == second
    assert first[0] is manager.tts_track
    assert first[1] is manager.playback_track
    assert first[0] is not first[1]


def test_configured_outbound_audio_keeps_capture_when_stdio_is_redirected(monkeypatch):
    from shared import video_call_manager

    class FakeLocalAudioInputTrack:
        def __init__(self, *, device_index_or_name, capture_loopback):
            self.device_index_or_name = device_index_or_name
            self.capture_loopback = capture_loopback
            self.enabled = False

        def enable(self):
            self.enabled = True

    cfg = server._default_config()
    cfg["video_call"]["capture_audio"] = True
    cfg["video_call"]["audio_sources"] = ["microphone", "speaker_loopback"]
    cfg["video_call"]["ai_audio_replies_enabled"] = True

    monkeypatch.setattr(video_call_manager, "LocalAudioInputTrack", FakeLocalAudioInputTrack)
    monkeypatch.setattr(server.STATE, "local_audio_tracks", {}, raising=False)

    tts_track = object()
    track = server._create_configured_outbound_audio_track(
        cfg=cfg,
        session_id="synthetic-session",
        tts_track=tts_track,
    )

    assert track is not tts_track
    assert len(server.STATE.local_audio_tracks["synthetic-session"]) == 2


def test_host_microphone_never_returns_to_same_machine_and_yields_to_local_call(monkeypatch):
    from shared import video_call_manager

    class FakeLocalAudioInputTrack:
        def __init__(self, *, device_index_or_name, capture_loopback):
            self.enabled = False

        def enable(self):
            self.enabled = True

    manager = server.WebRTCManager()
    manager.same_machine_audio_sessions.add("synthetic-local-session")
    cfg = server._default_config()
    cfg["video_call"]["audio_sources"] = ["microphone"]
    cfg["video_call"]["capture_audio"] = True
    monkeypatch.setattr(server, "WEBRTC", manager)
    monkeypatch.setattr(video_call_manager, "LocalAudioInputTrack", FakeLocalAudioInputTrack)
    monkeypatch.setattr(server.STATE, "local_audio_tracks", {}, raising=False)

    local_reply = object()
    assert server._create_configured_outbound_audio_track(
        cfg=cfg, session_id="synthetic-local-session", tts_track=local_reply,
    ) is local_reply
    assert server.STATE.local_audio_tracks == {}
    server._create_configured_outbound_audio_track(
        cfg=cfg, session_id="synthetic-remote-session", tts_track=object(),
    )
    assert len(server.STATE.local_audio_tracks["synthetic-remote-session"]) == 1

    server.STATE.local_audio_tracks.clear()
    manager.host_media_owner_by_session["synthetic-local-session"] = "connected_call"
    remote_reply = object()
    assert server._create_configured_outbound_audio_track(
        cfg=cfg, session_id="synthetic-remote-session", tts_track=remote_reply,
    ) is remote_reply
    assert server.STATE.local_audio_tracks == {}

    manager.host_media_owner_by_session.clear()
    manager.server_microphone_sharing_enabled = False
    assert server._create_configured_outbound_audio_track(
        cfg=cfg, session_id="synthetic-remote-session", tts_track=remote_reply,
    ) is remote_reply
    assert server.STATE.local_audio_tracks == {}


def test_connected_device_count_deduplicates_session_aliases():
    manager = server.WebRTCManager()
    local_channel = SimpleNamespace(datachannel=SimpleNamespace(readyState="open"))
    remote_channel = SimpleNamespace(datachannel=SimpleNamespace(readyState="open"))
    manager.datachannel_managers.update({
        "synthetic-local": local_channel,
        "synthetic-local-alias": local_channel,
        "synthetic-remote": remote_channel,
    })
    manager.same_machine_audio_sessions.add("synthetic-local")

    assert manager.connected_device_count() == 2
    assert manager.connected_device_count(same_machine_only=True) == 1


@pytest.mark.asyncio
async def test_host_audio_priority_accepts_only_trusted_same_machine_session(monkeypatch):
    manager = server.WebRTCManager()
    manager.same_machine_audio_sessions.add("synthetic-local")
    rewire = AsyncMock()
    monkeypatch.setattr(manager, "_rewire_outbound_audio_for_host", rewire)
    message = SimpleNamespace(payload={"event": "host_audio_priority", "owner": "lobby"})

    await manager._handle_voice_call_control_message(message, trusted_session_id="synthetic-remote")
    assert manager.host_audio_owner() == ""
    rewire.assert_not_awaited()

    await manager._handle_voice_call_control_message(message, trusted_session_id="synthetic-local")
    assert manager.host_audio_owner() == "lobby"
    rewire.assert_awaited_once()


@pytest.mark.asyncio
async def test_microphone_sharing_setting_persists_before_live_rewire(monkeypatch):
    cfg = server._default_config()
    manager = server.WebRTCManager()
    saved = []
    monkeypatch.setattr(server, "WEBRTC", manager)
    monkeypatch.setattr(server, "_loaded_config_for_update", lambda **_kwargs: cfg)
    monkeypatch.setattr(server, "_persist_state_config", lambda value: saved.append(value.copy()))

    await server._apply_server_microphone_sharing(False)

    assert saved[0]["video_call"]["server_microphone_sharing_enabled"] is False
    assert manager.server_microphone_sharing_enabled is False


@pytest.mark.asyncio
async def test_microphone_sharing_rewires_an_active_remote_sender(monkeypatch):
    manager = server.WebRTCManager()
    restored = []
    manager.audio_transceivers["synthetic-remote"] = SimpleNamespace(
        sender=SimpleNamespace(track=object()))
    monkeypatch.setattr(manager, "_restore_outbound_audio_for_call", restored.append)

    await manager.set_server_microphone_sharing(False)

    assert restored == ["synthetic-remote"]


@pytest.mark.asyncio
async def test_microphone_sharing_write_requires_admin_login(monkeypatch):
    class Request:
        cookies = {}
        headers = {}

        async def json(self):
            return {"enabled": False}

    apply = AsyncMock()
    monkeypatch.setattr(server, "_apply_server_microphone_sharing", apply)

    response = await server.admin_set_microphone_sharing(Request())

    assert response.status_code == 401
    apply.assert_not_awaited()


def test_admin_get_webrtc_audio_devices(monkeypatch):
    server._WEBRTC_DEVICE_ENUM_CACHE.clear()
    monkeypatch.setenv("AUTOYOU_WEBRTC_DEVICE_ENUM_CACHE_TTL_SECONDS", "0")
    monkeypatch.setattr(
        server,
        "enumerate_pyaudio_input_devices",
        lambda **_kwargs: [
            {
                "id": 0,
                "selector": "Synthetic Microphone",
                "name": "Synthetic Microphone",
                "host_api": "Windows WASAPI",
                "channels": 2,
                "sample_rate": 48000,
                "is_default": True,
            },
            {
                "id": 1,
                "selector": "Synthetic Speakers [Loopback]",
                "name": "Synthetic Speakers [Loopback]",
                "host_api": "Windows WASAPI",
                "channels": 2,
                "sample_rate": 48000,
                "is_default": False,
            },
        ],
    )

    # We mock _require_webrtc_playback_auth_json to pass
    monkeypatch.setattr(server, "_require_webrtc_playback_auth_json", lambda request: None)
    
    class MockRequest:
        pass

    try:
        import asyncio
        async def run_test():
            resp = await server.admin_get_webrtc_audio_devices(MockRequest())
            assert resp["success"] is True
            assert len(resp["devices"]) == 1
            assert resp["devices"][0]["id"] == 0
            assert resp["devices"][0]["name"] == "Synthetic Microphone"
            assert resp["devices"][0]["selector"] == "Synthetic Microphone"
            assert resp["devices"][0]["host_api"] == "Windows WASAPI"
            assert resp["loopback_available"] is True
        asyncio.run(run_test())
    finally:
        server._WEBRTC_DEVICE_ENUM_CACHE.clear()


def test_admin_get_webrtc_video_files(monkeypatch, tmp_path):
    mock_upload_dir = tmp_path / "video-files"
    mock_upload_dir.mkdir(parents=True, exist_ok=True)
    
    (mock_upload_dir / "test1.mp4").write_bytes(b"dummy")
    (mock_upload_dir / "test2.avi").write_bytes(b"dummy2")
    (mock_upload_dir / "not_video.txt").write_bytes(b"ignore")
    
    monkeypatch.setattr(server, "_resolve_video_file_upload_dir", lambda: mock_upload_dir)
    monkeypatch.setattr(server, "_require_webrtc_playback_auth_json", lambda request: None)
    
    class MockRequest:
        pass
        
    async def run_test():
        resp = await server.admin_get_webrtc_video_files(MockRequest())
        assert resp["success"] is True
        assert len(resp["files"]) == 2
        assert resp["files"][0]["name"] == "test1.mp4"
        assert resp["files"][1]["name"] == "test2.avi"
        assert resp["files"][0]["size_bytes"] == 5
        assert resp["files"][1]["size_bytes"] == 6
        
    import asyncio
    asyncio.run(run_test())


def test_admin_get_webrtc_camera_devices(monkeypatch):
    server._WEBRTC_DEVICE_ENUM_CACHE.clear()
    monkeypatch.delenv("AUTOYOU_WEBRTC_CAMERA_PROBE_ENABLED", raising=False)
    monkeypatch.delenv("AUTOYOU_WEBRTC_DEVICE_PROBE_ENABLED", raising=False)
    monkeypatch.setattr(server, "_require_webrtc_playback_auth_json", lambda request: None)
    monkeypatch.setattr(
        server,
        "_open_cv_video_capture",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("camera probe should be disabled")),
    )

    class MockRequest:
        pass

    async def run_test():
        resp = await server.admin_get_webrtc_camera_devices(MockRequest())
        assert resp["success"] is True
        assert resp["source"] == "configured"
        assert resp["probe_enabled"] is False
        assert len(resp["devices"]) == 1
        assert resp["devices"][0]["id"] == 0
        assert resp["devices"][0]["name"] == "Configured camera - capture index 0"
        assert resp["devices"][0]["available"] is None
        assert resp["devices"][0]["probe_status"] == "not_probed"

    import asyncio
    asyncio.run(run_test())


def test_admin_get_webrtc_camera_devices_can_probe_when_opted_in(monkeypatch):
    server._WEBRTC_DEVICE_ENUM_CACHE.clear()

    class FakeVideoCapture:
        def __init__(self, index):
            self.index = index
        def isOpened(self):
            return self.index in (0, 2)
        def release(self):
            pass

    import sys
    from types import ModuleType

    try:
        import cv2  # noqa: F401
    except Exception:
        fake_cv2 = ModuleType("cv2")
        fake_cv2.VideoCapture = FakeVideoCapture
        monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    monkeypatch.setenv("AUTOYOU_WEBRTC_CAMERA_PROBE_ENABLED", "true")
    monkeypatch.setenv("AUTOYOU_WEBRTC_DEVICE_ENUM_CACHE_TTL_SECONDS", "0")
    monkeypatch.setattr(server, "_open_cv_video_capture", lambda _cv2, index: FakeVideoCapture(index))
    monkeypatch.setattr(server, "_require_webrtc_playback_auth_json", lambda request: None)

    class MockRequest:
        pass

    async def run_test():
        resp = await server.admin_get_webrtc_camera_devices(MockRequest())
        assert resp["success"] is True
        assert resp["source"] == "opencv"
        assert resp["probe_enabled"] is True
        assert len(resp["devices"]) == 2
        assert resp["devices"][0]["id"] == 0
        assert resp["devices"][0]["name"] == "Camera 1 - capture index 0"
        assert resp["devices"][0]["available"] is True
        assert resp["devices"][1]["id"] == 2
        assert resp["devices"][1]["name"] == "Camera 3 - capture index 2"

    import asyncio
    asyncio.run(run_test())


def test_webrtc_monitor_devices_use_friendly_dimensions(monkeypatch):
    server._WEBRTC_DEVICE_ENUM_CACHE.clear()

    class FakeCapture:
        monitors = [
            {"left": 0, "top": 0, "width": 3200, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 180, "width": 1280, "height": 720},
        ]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    try:
        import mss as mss_module
    except ImportError:
        from types import ModuleType
        import sys

        mss_module = ModuleType("mss")
        monkeypatch.setitem(sys.modules, "mss", mss_module)
    monkeypatch.setattr(mss_module, "mss", lambda: FakeCapture(), raising=False)

    cfg = server._default_config()
    cfg["video_call"]["remote_desktop"]["monitor_id"] = 2
    payload = server._enumerate_webrtc_monitors_payload(cfg=cfg, refresh=True)

    assert payload["source"] == "mss"
    assert [device["id"] for device in payload["devices"]] == [0, 1, 2]
    assert payload["devices"][0]["name"] == "All displays - 3200x1080"
    assert payload["devices"][2]["name"] == "Display 2 - 1280x720 at +1920,+180"
    assert payload["devices"][2]["configured"] is True


def test_apply_video_call_settings_rewires_live_call_audio(monkeypatch):
    """Changing live outbound audio settings re-applies the call track."""

    async def run() -> None:
        webrtc = server.WebRTCManager()
        restored_sessions: list[str] = []
        monkeypatch.setattr(
            webrtc,
            "_restore_outbound_audio_for_call",
            lambda session_id: restored_sessions.append(str(session_id)),
        )

        class FakeSender:
            def replaceTrack(self, track):
                pass

        transceiver = SimpleNamespace(sender=FakeSender(), direction="sendrecv")
        webrtc.audio_transceivers["session-1"] = transceiver
        webrtc.voice_call_client_active_by_session["session-1"] = True

        cfg = server._default_config()
        original_config = server.STATE.config
        try:
            server.STATE.config = cfg

            # The first live apply must not assume a startup baseline: an
            # already-active call may be the reason settings are being saved.
            await webrtc.apply_video_call_settings()
            assert restored_sessions == ["session-1"]

            # Same signature again: no duplicate rewire.
            restored_sessions.clear()
            await webrtc.apply_video_call_settings()
            assert restored_sessions == []

            cfg["audio_playback"]["enabled"] = False
            await webrtc.apply_video_call_settings()
            assert restored_sessions == ["session-1"]

            restored_sessions.clear()
            cfg["video_call"]["audio_enabled"] = False
            await webrtc.apply_video_call_settings()
            assert restored_sessions == ["session-1"]

            restored_sessions.clear()
            cfg["video_call"]["audio_enabled"] = True
            await webrtc.apply_video_call_settings()
            assert restored_sessions == ["session-1"]

            restored_sessions.clear()
            cfg["video_call"]["audio_sources"] = ["microphone"]
            cfg["video_call"]["capture_audio"] = True
            cfg["video_call"]["input_audio_source"] = "MacBook Pro Microphone"
            await webrtc.apply_video_call_settings()
            assert restored_sessions == ["session-1"]

            # Inactive calls are not rewired.
            restored_sessions.clear()
            webrtc.voice_call_client_active_by_session["session-1"] = False
            cfg["video_call"]["audio_sources"] = []
            cfg["video_call"]["capture_audio"] = False
            await webrtc.apply_video_call_settings()
            assert restored_sessions == []
        finally:
            server.STATE.config = original_config

    asyncio.run(run())


def test_master_audio_off_on_preserves_and_restores_live_voice_runtime(monkeypatch):
    class FakeSender:
        def __init__(self):
            self.track = object()

        def replaceTrack(self, track):
            self.track = track

    class FakeSink:
        def __init__(self):
            self.stop_calls = 0

        async def stop(self):
            self.stop_calls += 1

    class FakeAudioManager:
        def __init__(self):
            self.tts_track = SimpleNamespace(readyState="live")
            self.playback_track = SimpleNamespace(readyState="live")
            self.close_calls = 0
            self.speech_stops = []
            self.media_stops = []

        def close(self):
            self.close_calls += 1

        def stop_speaking(self, *, source):
            self.speech_stops.append(source)

        def stop_playback(self, *, source):
            self.media_stops.append(source)
            return True

    class FakeCaptureTrack:
        def __init__(self):
            self.disabled = False
            self.stopped = False

        def disable(self):
            self.disabled = True

        def stop(self):
            self.stopped = True

    async def run() -> None:
        webrtc = server.WebRTCManager()
        sender = FakeSender()
        transceiver = SimpleNamespace(sender=sender, direction="sendrecv")
        sink = FakeSink()
        manager = FakeAudioManager()
        capture = FakeCaptureTrack()
        restored_track = object()
        cfg = server._default_config()
        original_config = server.STATE.config
        original_audio_managers = dict(server.STATE.audio_managers)
        original_local_tracks = dict(getattr(server.STATE, "local_audio_tracks", {}))
        try:
            server.STATE.config = cfg
            server.STATE.audio_managers.clear()
            server.STATE.audio_managers["session-1"] = manager
            server.STATE.local_audio_tracks = {"session-1": [capture]}
            webrtc.audio_transceivers["session-1"] = transceiver
            webrtc.audio_sinks["session-1"] = sink
            webrtc.voice_call_client_active_by_session["session-1"] = True
            monkeypatch.setattr(
                server,
                "_create_configured_outbound_audio_track",
                lambda **_kwargs: restored_track,
            )

            # Establish the live signature, then turn the master off.
            await webrtc.apply_video_call_settings()
            cfg["video_call"]["audio_enabled"] = False
            await webrtc.apply_video_call_settings()

            assert sender.track is None
            assert capture.disabled is True
            assert capture.stopped is True
            assert "session-1" not in server.STATE.local_audio_tracks
            assert server.STATE.audio_managers["session-1"] is manager
            assert webrtc.audio_sinks["session-1"] is sink
            assert webrtc.voice_call_client_active_by_session["session-1"] is True
            assert manager.close_calls == 0
            assert sink.stop_calls == 0
            assert manager.speech_stops == ["video_call_audio_disabled"]
            assert manager.media_stops == ["video_call_audio_disabled"]

            # The same negotiated sender, sink, and manager resume in place.
            cfg["video_call"]["audio_enabled"] = True
            await webrtc.apply_video_call_settings()

            assert sender.track is restored_track
            assert server.STATE.audio_managers["session-1"] is manager
            assert webrtc.audio_sinks["session-1"] is sink
            assert manager.close_calls == 0
            assert sink.stop_calls == 0
        finally:
            server.STATE.config = original_config
            server.STATE.audio_managers.clear()
            server.STATE.audio_managers.update(original_audio_managers)
            server.STATE.local_audio_tracks = original_local_tracks

    asyncio.run(run())


def test_admin_audio_playback_toggle_forces_live_call_audio_rewire(monkeypatch):
    class FakeWebRTC:
        def __init__(self):
            self.force_audio_rewire = None

        async def apply_video_call_settings(self, *, force_audio_rewire=False):
            self.force_audio_rewire = force_audio_rewire

    class MockRequest:
        client = SimpleNamespace(host="127.0.0.1")
        headers = {}

        async def json(self):
            return {"enabled": True}

    fake_webrtc = FakeWebRTC()
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
    monkeypatch.setattr(server, "_require_api_login", lambda request: None)
    monkeypatch.setattr(server, "_get_audio_playback_enabled", lambda cfg=None: False)
    monkeypatch.setattr(
        server,
        "_apply_audio_playback_enabled",
        lambda enabled: {"audio_playback": {"enabled": enabled}},
    )
    monkeypatch.setattr(server, "_resolve_audio_playback_music_library_dirs", lambda cfg=None: [])
    monkeypatch.setattr(server, "_is_audio_agent_installed", lambda: True)

    response = asyncio.run(server.admin_set_audio_playback_enabled(MockRequest()))

    assert response["success"] is True
    assert fake_webrtc.force_audio_rewire is True


def test_disabling_audio_playback_stops_media_without_interrupting_tts(monkeypatch):
    import service_manager

    class FakeAudioManager:
        def __init__(self):
            self.media_stops = []
            self.tts_stops = []

        def stop_playback(self, *, source):
            self.media_stops.append(source)
            return True

        def stop_speaking(self, *, source):
            self.tts_stops.append(source)

    manager = FakeAudioManager()
    monkeypatch.setattr(server.STATE, "audio_managers", {"session-1": manager})
    monkeypatch.setattr(server, "_loaded_config_for_update", server._default_config)
    monkeypatch.setattr(
        server,
        "_set_audio_playback_enabled",
        lambda enabled, cfg=None: {**server._default_config(), "audio_playback": {"enabled": enabled}},
    )
    monkeypatch.setattr(server, "_persist_state_config", lambda _cfg: None)
    monkeypatch.setattr(server, "_resolve_audio_playback_music_library_dirs", lambda cfg=None: [])
    monkeypatch.setattr(server, "set_audio_playback_enabled_env", lambda _enabled: None)
    monkeypatch.setattr(service_manager, "update_service_config", lambda _config: None)

    server._apply_audio_playback_enabled(False)

    assert manager.media_stops == ["audio_playback_disabled:session-1"]
    assert manager.tts_stops == []


def test_admin_playback_stop_targets_media_lane_only(monkeypatch):
    class FakeAudioManager:
        def __init__(self):
            self.media_stops = []
            self.tts_stops = []

        def stop_playback(self, *, source):
            self.media_stops.append(source)
            return True

        def stop_speaking(self, *, source):
            self.tts_stops.append(source)

        def get_playback_status(self):
            return {"event": "playback", "state": "stopped"}

    async def run() -> None:
        webrtc = server.WebRTCManager()
        manager = FakeAudioManager()
        monkeypatch.setattr(server.STATE, "audio_managers", {"session-1": manager})
        monkeypatch.setattr(server, "_get_audio_playback_enabled", lambda cfg=None: True)

        ok, status = await webrtc.stop_audio_playback_for_reply_target(
            {"transport": "webrtc", "session_id": "session-1"}
        )

        assert ok is True
        assert status["state"] == "stopped"
        assert manager.media_stops == ["admin_api:session-1:stop"]
        assert manager.tts_stops == []

    asyncio.run(run())


def test_restore_outbound_audio_reuses_both_live_lanes_and_drops_stale_capture(monkeypatch):
    """Mid-call restore keeps both producer tracks and stops old capture tracks."""

    class FakeSender:
        def __init__(self):
            self.replaced: list = []

        def replaceTrack(self, track):
            self.replaced.append(track)

    class FakeCaptureTrack:
        def __init__(self):
            self.disabled = False
            self.stopped = False

        def disable(self):
            self.disabled = True

        def stop(self):
            self.stopped = True

    class FakeMixedAudioStreamTrack:
        def __init__(self, sources):
            self.sources = list(sources)

        def source_names(self):
            return [name for name, _track in self.sources]

    webrtc = server.WebRTCManager()
    sender = FakeSender()
    transceiver = SimpleNamespace(sender=sender, direction="inactive")
    webrtc.audio_transceivers["session-1"] = transceiver

    fake_tts = SimpleNamespace(readyState="live")
    fake_playback = SimpleNamespace(readyState="live")
    audio_manager = SimpleNamespace(
        tts_track=fake_tts,
        playback_track=fake_playback,
    )
    stale_capture = FakeCaptureTrack()

    cfg = server._default_config()
    original_config = server.STATE.config
    original_manager = server.STATE.audio_managers.get("session-1")
    try:
        server.STATE.config = cfg
        server.STATE.audio_managers["session-1"] = audio_manager
        monkeypatch.setattr(server, "MixedAudioStreamTrack", FakeMixedAudioStreamTrack)
        if not hasattr(server.STATE, "local_audio_tracks"):
            server.STATE.local_audio_tracks = {}
        server.STATE.local_audio_tracks["session-1"] = [stale_capture]

        webrtc._restore_outbound_audio_for_call("session-1")

        # The stale capture track is fully stopped and forgotten.
        assert stale_capture.disabled is True
        assert stale_capture.stopped is True
        assert "session-1" not in server.STATE.local_audio_tracks

        # Both live producer tracks are reused and reattached through one mixer.
        assert len(sender.replaced) == 1
        assert sender.replaced[0].sources == [
            ("ai_replies", fake_tts),
            ("playback", fake_playback),
        ]
        assert audio_manager.tts_track is fake_tts
        assert audio_manager.playback_track is fake_playback
        assert transceiver.direction == "sendrecv"
    finally:
        server.STATE.config = original_config
        server.STATE.audio_managers.pop("session-1", None)
        if original_manager is not None:
            server.STATE.audio_managers["session-1"] = original_manager
        getattr(server.STATE, "local_audio_tracks", {}).pop("session-1", None)


def test_restore_outbound_audio_master_disable_detaches_and_stops_capture(monkeypatch):
    class FakeSender:
        def __init__(self):
            self.replaced = []

        def replaceTrack(self, track):
            self.replaced.append(track)

    class FakeCaptureTrack:
        def __init__(self):
            self.disabled = False
            self.stopped = False

        def disable(self):
            self.disabled = True

        def stop(self):
            self.stopped = True

    webrtc = server.WebRTCManager()
    sender = FakeSender()
    webrtc.audio_transceivers["session-1"] = SimpleNamespace(
        sender=sender,
        direction="sendrecv",
    )
    capture = FakeCaptureTrack()
    cfg = server._default_config()
    cfg["video_call"]["audio_enabled"] = False
    original_config = server.STATE.config
    original_tracks = dict(getattr(server.STATE, "local_audio_tracks", {}))
    try:
        server.STATE.config = cfg
        monkeypatch.setattr(
            server.STATE,
            "local_audio_tracks",
            {"session-1": [capture]},
            raising=False,
        )

        webrtc._restore_outbound_audio_for_call("session-1")

        assert capture.disabled is True
        assert capture.stopped is True
        assert "session-1" not in server.STATE.local_audio_tracks
        assert sender.replaced == [None]
    finally:
        server.STATE.config = original_config
        server.STATE.local_audio_tracks = original_tracks


def test_restore_outbound_audio_replace_failure_stops_new_capture(monkeypatch):
    class FailingSender:
        def replaceTrack(self, _track):
            raise RuntimeError("synthetic replacement failure")

    class FakeCaptureTrack:
        def __init__(self):
            self.disabled = False
            self.stopped = False

        def disable(self):
            self.disabled = True

        def stop(self):
            self.stopped = True

    webrtc = server.WebRTCManager()
    webrtc.audio_transceivers["session-1"] = SimpleNamespace(
        sender=FailingSender(),
        direction="inactive",
    )
    capture = FakeCaptureTrack()
    cfg = server._default_config()
    original_config = server.STATE.config
    original_tracks = dict(getattr(server.STATE, "local_audio_tracks", {}))

    def create_track(**_kwargs):
        server.STATE.local_audio_tracks["session-1"] = [capture]
        return object()

    try:
        server.STATE.config = cfg
        monkeypatch.setattr(server.STATE, "local_audio_tracks", {}, raising=False)
        monkeypatch.setattr(
            server,
            "_create_configured_outbound_audio_track",
            create_track,
        )

        webrtc._restore_outbound_audio_for_call("session-1")

        assert capture.disabled is True
        assert capture.stopped is True
        assert "session-1" not in server.STATE.local_audio_tracks
    finally:
        server.STATE.config = original_config
        server.STATE.local_audio_tracks = original_tracks


def test_admin_ui_greys_out_webcam_option_when_no_webcam_is_detected():
    from pathlib import Path

    repo_root = Path(server.__file__).resolve().parent
    script = (repo_root / "assets" / "admin-ui.js").read_text(encoding="utf-8")

    # A probe that finds no webcam disables the option with plain guidance
    # instead of leaving a dead pane configurable.
    assert "cameraDevices.probe_enabled" in script
    assert "noWebcamDetected" in script
    assert "Connect a webcam and select Refresh devices, or turn this off." in script
    assert "No webcam found." in script
    assert "the webcam pane stays hidden until a camera is connected" in script
    assert "Not checked - select Refresh devices" in script

    stylesheet = (repo_root / "assets" / "admin-ui.css").read_text(encoding="utf-8")
    assert ".ayu-checkbox:has(input:disabled)" in stylesheet
