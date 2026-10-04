"""Connected desktop settings follow the paired browser role."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import server
from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType


@pytest.mark.asyncio
async def test_remote_desktop_settings_viewer_editor_and_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    cfg = server._default_config()
    cfg["video_call"]["audio_sources"] = ["microphone", "speaker_loopback"]
    cfg["autoyou_page"]["remote_access_role"] = "viewer"
    monkeypatch.setattr(server.STATE, "config", cfg, raising=False)

    async def apply_patch(patch):
        updated, _, _ = server._apply_admin_ui_config_patch(server.STATE.config, patch)
        server.STATE.config = updated

    monkeypatch.setattr(server, "_apply_admin_ui_config_update", apply_patch)
    webrtc = server.WebRTCManager()
    channel = SimpleNamespace(send_message=AsyncMock(return_value=True))
    webrtc.datachannel_managers["synthetic-session"] = channel

    async def request(method="GET", changes=None):
        body = json.dumps(changes).encode() if changes is not None else b""
        message = DataChannelMessage(
            header=MessageHeader("synthetic-message", MessageType.HTTP_REQUEST, 0.0,
                                 "synthetic-session", "synthetic-user"),
            payload={"request_id": "synthetic-request", "method": method,
                     "url": "/api/v1/remote-desktop-settings", "headers": {},
                     "body": base64.b64encode(body).decode(), "body_base64": True},
        )
        await webrtc._handle_http_request(message)
        return channel.send_message.call_args.args[0].payload

    read = await request()
    assert read["status_code"] == 200
    values = json.loads(read["body"])
    assert values["computer_sound"] is True
    assert values["role"] == "viewer"
    assert "paths" not in values
    assert "admin_frontend_enabled" not in values
    assert "remote_access_role" not in values
    assert set(values) == {
        "computer_sound", "control_enabled", "game_enabled", "location_recording_enabled",
        "voice_call_recording_enabled", "video_call_recording_enabled", "role",
    }
    assert (await request("POST", {"computer_sound": False}))["status_code"] == 403

    cfg["autoyou_page"]["remote_access_role"] = "editor"
    assert (await request("POST", {"unknown": True}))["status_code"] == 400
    updated = await request("POST", {
        "computer_sound": False, "control_enabled": True, "game_enabled": True,
        "location_recording_enabled": True, "voice_call_recording_enabled": True,
        "video_call_recording_enabled": True,
    })
    assert updated["status_code"] == 200
    values = json.loads(updated["body"])
    assert all(values[key] is True for key in (
        "control_enabled", "game_enabled", "location_recording_enabled",
        "voice_call_recording_enabled", "video_call_recording_enabled",
    ))
    assert values["computer_sound"] is False
    assert server.STATE.config["video_call"]["audio_sources"] == ["microphone"]
    disabled = await request("POST", {"control_enabled": False})
    assert json.loads(disabled["body"])["game_enabled"] is False
    assert (await request("DELETE"))["status_code"] == 403

    server.STATE.config["autoyou_page"]["remote_access_role"] = "admin"
    assert (await request("DELETE"))["status_code"] == 405
