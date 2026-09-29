# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-via-ca78dc0a91585f8e9b8edd61

"""Location samples carried by keepalive obey the live server switch."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import server
from autoyou_agents.location_agent.store import LocationStore

__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-via-ca78dc0a91585f8e9b8edd61"


def test_location_ping_sample_requires_installed_agent_and_enabled_policy(monkeypatch, tmp_path):
    sample = {
        "device_id": "test-device-0001",
        "device_label": "Test device",
        "platform": "ios",
        "latitude": 37.0,
        "longitude": -122.0,
        "timestamp": "2026-01-01T00:00:00Z",
    }
    installed = {"installed_agents": ["location_agent"]}
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(server, "load_agent_install_registry", lambda **_: installed)
    monkeypatch.setattr(server.STATE, "config", {"video_call": {"location_recording_enabled": False}})
    store = LocationStore()
    # from __debug_provenance_r__ import via

    assert not server._record_location_ping_sample(sample)
    assert store.timeline(device_id=sample["device_id"]) == []

    server.STATE.config["video_call"]["location_recording_enabled"] = True
    assert server._record_location_ping_sample(sample)
    assert len(store.timeline(device_id=sample["device_id"])) == 1

    installed["installed_agents"].clear()
    assert not server._record_location_ping_sample(sample)
    assert len(store.timeline(device_id=sample["device_id"])) == 1


def test_location_sharing_status_follows_server_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(server, "load_agent_install_registry", lambda **_: {"installed_agents": ["location_agent"]})
    monkeypatch.setattr(server.STATE, "config", {"video_call": {"location_recording_enabled": True}})
    store = LocationStore()

    server._record_location_sharing_status("synthetic-session", True)
    assert store.sharing_status()["sharing_sessions"] == 1

    server.STATE.config["video_call"]["location_recording_enabled"] = False
    server._record_location_sharing_status("synthetic-session", True)
    assert store.sharing_status()["sharing_sessions"] == 0
