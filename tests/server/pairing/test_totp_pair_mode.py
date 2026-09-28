# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Tests for secure-professional pairing code and tunnel lifetime modes."""

import hashlib
import json
import time
from unittest.mock import AsyncMock, patch

import pyotp
import pytest

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import server
from pairing_router import PairingRouter


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_FILE_PATH", str(tmp_path / "config.encrypted"))
    monkeypatch.setattr(server, "CONFIG_BAK_PATH", str(tmp_path / "config.encrypted.bak"))
    monkeypatch.setattr(server, "_get_server_keystore", lambda: None)
    monkeypatch.setattr(server, "_keystore_server_password_available", lambda: False)


@pytest.fixture()
def totp_secret():
    return pyotp.random_base32()


@pytest.fixture()
def _authenticator_timed_config(totp_secret):
    original_config = server.STATE.config
    original_password = server.STATE.server_password
    original_cache = dict(server.STATE.otp_cache)
    server.STATE.config = {
        "security": {
            "mode": "secure_professional",
            "totp_secret": totp_secret,
        },
        "tunnelmole": {
            "enabled": True,
            "pair_code_mode": "authenticator",
            "connection_mode": "timed",
        },
    }
    server.STATE.server_password = "testpassword"
    server.STATE.otp_cache = {}
    yield
    server.STATE.config = original_config
    server.STATE.server_password = original_password
    server.STATE.otp_cache = original_cache


class TestTunnelmoleModeHelpers:
    def test_is_totp_pair_mode_requires_secure_professional(self, _authenticator_timed_config):
        server.STATE.config["security"]["mode"] = "normal"
        assert server._is_totp_pair_mode() is False

    def test_is_totp_pair_mode_false_in_secure_mode(self, _authenticator_timed_config):
        server.STATE.config["security"]["mode"] = "secure"
        assert server._is_totp_pair_mode() is False

    def test_is_totp_pair_mode_requires_authenticator_setting(self, _authenticator_timed_config):
        server.STATE.config["tunnelmole"]["pair_code_mode"] = "random_otp"
        assert server._is_totp_pair_mode() is False

    def test_is_totp_pair_mode_true_for_authenticator_secure_professional(self, _authenticator_timed_config):
        assert server._is_totp_pair_mode() is True

    def test_is_tunnelmole_unmanaged_mode_reads_connection_mode(self, _authenticator_timed_config):
        assert server._is_tunnelmole_unmanaged_mode() is False
        server.STATE.config["tunnelmole"]["connection_mode"] = "unmanaged"
        assert server._is_tunnelmole_unmanaged_mode() is True


class TestGenerateTotpPairOtpAndCache:
    def test_returns_current_totp_code(self, _authenticator_timed_config, totp_secret):
        result = server.generate_totp_pair_otp_and_cache()
        valid_codes = {
            pyotp.TOTP(totp_secret).at(int(time.time()) + step * 30)
            for step in server._PAIRING_TOTP_WINDOW_STEPS
        }
        assert result in valid_codes

    def test_does_not_cache_anything_but_auth_still_validates_live(self, _authenticator_timed_config, totp_secret):
        """H-20-adjacent fix: no standing 24h cache of precomputed hashes -
        generate_totp_pair_otp_and_cache() writes nothing, and validate_otp_hash
        recomputes live against the current TOTP window instead."""
        server.generate_totp_pair_otp_and_cache()
        assert server.STATE.otp_cache == {}

        current_code = pyotp.TOTP(totp_secret).now()
        expected_hash = hashlib.sha256(f"{current_code}:testpassword".encode()).hexdigest()
        result = server.validate_otp_hash(expected_hash)
        assert result["valid"] is True
        assert server.STATE.otp_cache == {}  # still never written to

        wrong_hash = hashlib.sha256(b"000000:testpassword").hexdigest()
        assert server.validate_otp_hash(wrong_hash)["valid"] is False

    def test_returns_empty_when_no_pairing_secret(self, _authenticator_timed_config):
        server.STATE.config["security"]["totp_secret"] = ""
        assert server.generate_totp_pair_otp_and_cache() == ""
        assert server.STATE.otp_cache == {}


class TestStartTunnelmoleServiceForCurrentMode:
    @pytest.mark.asyncio
    async def test_running_timed_mode_extends_timer(self, _authenticator_timed_config):
        with patch.object(server, "get_tunnelmole_status", return_value={"status": "running"}), patch.object(
            server,
            "extend_tunnelmole_timer",
            new=AsyncMock(return_value=True),
        ) as extend_timer, patch.object(
            server,
            "start_tunnelmole_service_no_timer",
            new=AsyncMock(return_value=True),
        ) as start_unmanaged, patch.object(
            server,
            "start_tunnelmole_service_with_timer",
            new=AsyncMock(return_value=True),
        ) as start_timed:
            result = await server.start_tunnelmole_service_for_current_mode()

        assert result is True
        extend_timer.assert_awaited_once_with(None)
        start_unmanaged.assert_not_called()
        start_timed.assert_not_called()

    @pytest.mark.asyncio
    async def test_stopped_unmanaged_mode_uses_no_timer_start(self, _authenticator_timed_config):
        server.STATE.config["tunnelmole"]["connection_mode"] = "unmanaged"
        with patch.object(server, "get_tunnelmole_status", return_value={"status": "stopped"}), patch.object(
            server,
            "start_tunnelmole_service_no_timer",
            new=AsyncMock(return_value=True),
        ) as start_unmanaged, patch.object(
            server,
            "start_tunnelmole_service_with_timer",
            new=AsyncMock(return_value=True),
        ) as start_timed:
            result = await server.start_tunnelmole_service_for_current_mode()

        assert result is True
        start_unmanaged.assert_awaited_once()
        start_timed.assert_not_called()


class TestPairingRouterModeSplit:
    def _make_router(
        self,
        *,
        pair_code_mode: str,
        connection_mode: str,
        totp_secret: str,
        status: dict | None = None,
    ) -> PairingRouter:
        router = PairingRouter()
        current_status = dict(status or {"status": "running", "public_url": "https://test.tunnelmole.net"})
        start_no_timer = AsyncMock(return_value=True)
        start_with_timer = AsyncMock(return_value=True)
        extend_timer = AsyncMock()

        async def _ensure_auth() -> bool:
            return True

        def _generate_totp() -> str:
            return pyotp.TOTP(totp_secret).now()

        router.configure(
            generate_hash=lambda s: hashlib.sha256(s.encode()).hexdigest(),
            aead_encrypt=lambda pt, password: pt,
            aead_decrypt=lambda ct, password: ct,
            get_current_password=lambda: "pw",
            get_security_mode=lambda: "secure_professional",
            get_totp_secret_for_sender=lambda _platform, _sender_id: totp_secret,
            get_all_totp_secrets=lambda: {"default": totp_secret},
            get_tunnelmole_status=lambda: dict(current_status),
            extend_tunnelmole_timer=extend_timer,
            start_tunnelmole_service_with_timer=start_with_timer,
            ensure_auth_server_running=_ensure_auth,
            generate_otp_hash_and_cache=lambda _expiration: "random-otp",
            handle_autopair_offer=AsyncMock(),
            is_totp_pair_mode=lambda: pair_code_mode == "authenticator",
            is_tunnelmole_unmanaged_mode=lambda: connection_mode == "unmanaged",
            generate_totp_pair_otp_and_cache=_generate_totp,
            start_tunnelmole_service_no_timer=start_no_timer,
            verify_totp_code=lambda secret, code: pyotp.TOTP(secret).verify(code, valid_window=1),
            totp_hello_rate_limit_allowed=lambda _key: True,
        )

        router._test_status = current_status
        router._test_start_no_timer = start_no_timer
        router._test_start_with_timer = start_with_timer
        router._test_extend_timer = extend_timer
        return router

    @pytest.mark.asyncio
    async def test_authenticator_pair_code_can_still_use_timed_connection_mode(self, totp_secret):
        router = self._make_router(
            pair_code_mode="authenticator",
            connection_mode="timed",
            totp_secret=totp_secret,
        )

        response = await router._handle_pair_command(
            platform="telegram", sender_id="user1", totp_code=pyotp.TOTP(totp_secret).now()
        )
        payload = json.loads(response.split("\n", 1)[1])

        assert payload["otp"] == pyotp.TOTP(totp_secret).now()
        router._test_extend_timer.assert_awaited_once_with(None)
        router._test_start_no_timer.assert_not_called()

    @pytest.mark.asyncio
    async def test_random_otp_can_use_unmanaged_connection_mode(self, totp_secret):
        router = self._make_router(
            pair_code_mode="random_otp",
            connection_mode="unmanaged",
            totp_secret=totp_secret,
        )

        response = await router._handle_pair_command(
            platform="telegram", sender_id="user1", totp_code=pyotp.TOTP(totp_secret).now()
        )
        payload = json.loads(response.split("\n", 1)[1])

        assert payload["otp"] == "random-otp"
        router._test_extend_timer.assert_not_called()
        router._test_start_no_timer.assert_not_called()

    @pytest.mark.asyncio
    async def test_unmanaged_mode_uses_no_timer_start_when_service_is_stopped(self, totp_secret):
        router = self._make_router(
            pair_code_mode="random_otp",
            connection_mode="unmanaged",
            totp_secret=totp_secret,
            status={"status": "stopped", "public_url": None},
        )

        async def _start_no_timer() -> bool:
            router._test_status["status"] = "running"
            router._test_status["public_url"] = "https://test.tunnelmole.net"
            return True

        router._start_tunnelmole_service_no_timer = AsyncMock(side_effect=_start_no_timer)
        response = await router._handle_pair_command(
            platform="telegram", sender_id="user1", totp_code=pyotp.TOTP(totp_secret).now()
        )
        payload = json.loads(response.split("\n", 1)[1])

        assert payload["otp"] == "random-otp"
        router._start_tunnelmole_service_no_timer.assert_awaited_once()
        router._test_start_with_timer.assert_not_called()


class TestAuthRateLimiting:
    def test_rate_limiter_allows_under_limit(self):
        limiter = server.AUTH_RATE_LIMITER
        test_ip = "10.0.0.99"
        limiter._requests.pop(test_ip, None)
        assert limiter.is_allowed(test_ip)

    def test_rate_limiter_blocks_exhausted_key(self):
        limiter = server.AUTH_RATE_LIMITER
        test_ip = "10.0.0.1"
        limiter._requests[test_ip] = [time.time()] * (limiter.max_requests + 1)
        assert not limiter.is_allowed(test_ip)
        limiter._requests.pop(test_ip, None)
