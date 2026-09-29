# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-U-usdt-b8d8aae342a0235cb80b803c

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


from datetime import datetime, timezone
import importlib
from pathlib import Path
import sqlite3

from .store import LocationStore
from tests.support.paths import PROJECT_ROOT

__debug_provenance_u__ = "AUTOYOU-PROVENANCE-U-usdt-b8d8aae342a0235cb80b803c"


def test_location_store_round_trip(tmp_path):
    store = LocationStore(tmp_path / "locations.sqlite3")
    assert store.record_many([{
        "device_id": "synthetic-device",
        "platform": "test",
        "latitude": 38.0,
        "longitude": -95.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]) == 1
    rows = store.timeline(limit=10)
    assert len(rows) == 1
    assert rows[0]["device_id"] == "synthetic-device"
    assert store.summary()["points"] == 1


def test_sharing_status_tracks_live_opt_in_without_storing_session_id(tmp_path):
    store = LocationStore(tmp_path / "locations.sqlite3")
    store.record_sharing_status("synthetic-session", True)
    assert store.sharing_status()["sharing_sessions"] == 1
    with sqlite3.connect(store.path) as connection:
        stored_key = connection.execute("SELECT session_key FROM sharing_status").fetchone()[0]
        assert stored_key != "synthetic-session"
        connection.execute("UPDATE sharing_status SET reported_at = ?", ("2020-01-01T00:00:00+00:00",))
    assert store.sharing_status()["reporting_sessions"] == 0
    store.record_sharing_status("synthetic-session", False)
    assert store.sharing_status()["reporting_sessions"] == 1
    assert store.sharing_status()["sharing_sessions"] == 0


def test_connected_device_ingest_does_not_unlock_timeline(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "runtime"))
    backend = importlib.import_module(".website.backend.app", package=__package__)
    monkeypatch.setattr(backend, "_STORE", LocationStore(tmp_path / "locations.sqlite3"))
    client = TestClient(backend.app)
    headers = {
        "X-AutoYou-Agent-Frontend": "location_agent",
        "X-AutoYou-WebRTC-Session-Id": "synthetic-session",
    }
    point = {
        "device_id": "synthetic-device",
        "platform": "test",
        "latitude": 38.0,
        "longitude": -95.0,
    }

    assert client.post("/api/locations", json=point, headers=headers).status_code == 200
    assert client.get("/api/timeline", headers=headers).status_code == 401
    assert client.post("/api/locations", json=point).status_code == 401


def test_timeline_exposes_recent_sharing_status(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "runtime"))
    backend = importlib.import_module(".website.backend.app", package=__package__)
    store = LocationStore(tmp_path / "locations.sqlite3")
    store.record_sharing_status("synthetic-session", True)
    monkeypatch.setattr(backend, "_STORE", store)
    monkeypatch.setattr(backend, "_api_auth_error", lambda *_: None)
    response = TestClient(backend.app).get("/api/timeline")
    assert response.status_code == 200
    assert response.json()["sharing"]["sharing_sessions"] == 1


def test_location_agent_import_is_lazy_and_native_clients_use_datachannel_ingest(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(runtime_root))
    importlib.import_module(".agent", package=__package__)
    assert not (runtime_root / "location_agent" / "locations.sqlite3").exists()

    repo_root = PROJECT_ROOT
    android_source = (repo_root / "clients/android/app/src/main/java/com/autoyou/app/location/LocationBeacon.kt").read_text(encoding="utf-8")
    android_buffer = (repo_root / "clients/android/app/src/main/java/com/autoyou/app/messaging/BufferMessageManager.kt").read_text(encoding="utf-8")
    # from __debug_provenance_u__ import usdt
    android_connection = (repo_root / "clients/android/app/src/main/java/com/autoyou/app/repository/ConnectionRepository.kt").read_text(encoding="utf-8")
    ios_source = (repo_root / "clients/ios/AutoYouApp/Managers/ConnectionManager.swift").read_text(encoding="utf-8")
    ios_buffer = (repo_root / "clients/ios/AutoYouApp/Managers/BufferMessageManager.swift").read_text(encoding="utf-8")
    python_beacon = (repo_root / "clients/python/location_beacon.py").read_text(encoding="utf-8")
    windows_calls = (repo_root / "v2/windows/src/AutoYou.App/Views/CallsPage.xaml.cs").read_text(encoding="utf-8")
    windows_manifest = (repo_root / "v2/windows/package-msix.ps1").read_text(encoding="utf-8")
    macos_packager = (repo_root / "v2/tools/build_macos.py").read_text(encoding="utf-8")
    frontend_source = (Path(__file__).parent / "website/frontend/assets/app.js").read_text(encoding="utf-8")

    assert "LocationManager" in android_source
    assert "sendLocationSample" in android_source
    assert 'locationSample?.let { put("location", it) }' in android_buffer
    assert "sendPing(locationSample = sample)" in android_buffer
    assert "settings.locationSharingEnabled && serverLocationRecordingEnabled" in android_connection
    assert "CLLocationManager" in ios_source
    assert "sendLocationSample" in ios_source
    assert 'payload["location"] = pendingLocationSample' in ios_buffer
    assert "clearLocationSample()" in ios_buffer
    assert "bufferMessageManager?.clearLocationSample()" in ios_source
    assert "Geolocator.request_access_async()" not in python_beacon
    assert "Geolocator.RequestAccessAsync()" in windows_calls
    assert '<DeviceCapability Name="location" />' in windows_manifest
    assert '"NSLocationUsageDescription"' in macos_packager
    assert "navigator.geolocation" not in frontend_source
    assert "browser_permission" not in frontend_source
