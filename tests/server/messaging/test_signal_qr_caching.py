# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import server
from signal_service import SignalService


@pytest.fixture(autouse=True)
def _disable_keystore(monkeypatch):
    monkeypatch.setattr(server, "_get_server_keystore", lambda: None)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: False)


def _admin_test_client() -> TestClient:
    client = TestClient(server.admin_app)
    server.ADMIN_SESSIONS["test-admin-session"] = True
    client.cookies.set("admin_session", "test-admin-session")
    return client


@pytest.mark.asyncio
async def test_signal_service_get_qr_code_link_caching():
    service = SignalService(port=8082, device_name="test-signal")
    call_count = 0

    class FakeResponse:
        status_code = 200
        content = b"fake-png-bytes-1"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, url, params=None):
            nonlocal call_count
            call_count += 1
            resp = FakeResponse()
            resp.content = f"fake-png-bytes-{call_count}".encode()
            return resp

    with patch("signal_service.httpx.AsyncClient", return_value=FakeClient()):
        # First call: fetches fresh QR code
        qr1 = await service.get_qr_code_link()
        assert qr1 is not None
        assert "data:image/png;base64," in qr1
        assert call_count == 1

        # Second call: should return cached QR code without calling httpx again
        qr2 = await service.get_qr_code_link()
        assert qr2 == qr1
        assert call_count == 1

        # Third call with force_refresh=True: should bypass cache and fetch new QR code
        qr3 = await service.get_qr_code_link(force_refresh=True)
        assert qr3 is not None
        assert qr3 != qr1
        assert call_count == 2

        # Fourth call without force_refresh: should return new cached QR code
        qr4 = await service.get_qr_code_link()
        assert qr4 == qr3
        assert call_count == 2


@pytest.mark.asyncio
async def test_signal_service_qr_cache_expiration():
    service = SignalService(port=8082, device_name="test-signal")
    service._cached_qr_ttl = 1.0  # short TTL for test
    call_count = 0

    class FakeResponse:
        status_code = 200
        content = b"fake-png-bytes"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, url, params=None):
            nonlocal call_count
            call_count += 1
            resp = FakeResponse()
            resp.content = f"fake-png-bytes-{call_count}".encode()
            return resp

    with patch("signal_service.httpx.AsyncClient", return_value=FakeClient()):
        qr1 = await service.get_qr_code_link()
        assert call_count == 1

        # Immediately: cached
        qr2 = await service.get_qr_code_link()
        assert qr2 == qr1
        assert call_count == 1

        # Fast-forward time past TTL
        service._cached_qr_timestamp = time.monotonic() - 2.0
        qr3 = await service.get_qr_code_link()
        assert qr3 != qr1
        assert call_count == 2


@pytest.mark.asyncio
async def test_signal_service_cleanup_clears_qr_cache():
    service = SignalService(port=8082, device_name="test-signal")
    service._cached_qr_link = "data:image/png;base64,existing"
    service._cached_qr_timestamp = time.monotonic()

    with patch.object(service, "stop", new_callable=AsyncMock):
        await service.cleanup()

    assert service._cached_qr_link is None
    assert service._cached_qr_timestamp == 0.0


def test_admin_signal_qr_route_caching_and_force_refresh(monkeypatch):
    original_service = server.STATE.signal_service
    original_config = server.STATE.config

    class FakeSignalService:
        def __init__(self):
            self.calls = []

        async def check_device_pairing_status(self):
            return {"paired": False}

        async def get_qr_code_link(self, force_refresh=False):
            self.calls.append(force_refresh)
            return "data:image/png;base64,test-qr-code"

    fake_service = FakeSignalService()

    try:
        server.STATE.signal_service = fake_service
        server.STATE.config = {
            "signal": {
                "enabled": True,
                "paired": False,
                "device_name": "AutoYou-Signal",
            }
        }

        with _admin_test_client() as client:
            resp1 = client.get("/api/signal/qr")
            assert resp1.status_code == 200
            assert resp1.json() == {
                "success": True,
                "qr_url": "data:image/png;base64,test-qr-code",
                "device_name": "AutoYou-Signal",
            }
            assert fake_service.calls == [False]

            # Second call without refresh param
            resp2 = client.get("/api/signal/qr")
            assert resp2.status_code == 200
            assert fake_service.calls == [False, False]

            # Third call with ?refresh=true
            resp3 = client.get("/api/signal/qr?refresh=true")
            assert resp3.status_code == 200
            assert fake_service.calls == [False, False, True]

    finally:
        server.STATE.signal_service = original_service
        server.STATE.config = original_config


def test_admin_signal_qr_route_rejects_already_paired(monkeypatch):
    original_service = server.STATE.signal_service
    original_config = server.STATE.config

    class FakeSignalService:
        async def check_device_pairing_status(self):
            return {"paired": True, "phone_number": "+11234567890"}

        async def get_qr_code_link(self, force_refresh=False):
            return "data:image/png;base64,should-not-be-called"

    try:
        server.STATE.signal_service = FakeSignalService()
        # Even if config.signal.paired is False, live check prevents pairing attempt
        server.STATE.config = {
            "signal": {
                "enabled": True,
                "paired": False,
                "device_name": "AutoYou-Signal",
            }
        }

        with _admin_test_client() as client:
            resp = client.get("/api/signal/qr")
            assert resp.status_code == 400
            assert "already paired" in resp.json()["error"].lower()

    finally:
        server.STATE.signal_service = original_service
        server.STATE.config = original_config
