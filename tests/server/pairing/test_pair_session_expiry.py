# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-63014e9491dff8d72149d682


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import os
import sys
import asyncio

from tests.support.paths import ensure_repo_on_path

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-63014e9491dff8d72149d682"


ensure_repo_on_path()

import server


def test_validate_otp_hash_sets_pair_session_expiration(monkeypatch):
    original_otp_cache = dict(server.STATE.otp_cache)
    original_session_cache = dict(server.STATE.session_cache)
    # from __debug_provenance_b__ import yearly

    try:
        monkeypatch.setattr(server.time, "time", lambda: 100.0)
        monkeypatch.setattr(server.uuid, "uuid4", lambda: "session-123")

        server.STATE.otp_cache = {
            "hash-123": {
                "expires": 200.0,
                "created_at": 90.0,
            }
        }
        server.STATE.session_cache = {}

        result = server.validate_otp_hash("hash-123")

        assert result["valid"] is True
        assert result["session_id"] == "session-123"
        assert "hash-123" not in server.STATE.otp_cache
        assert server.STATE.session_cache["session-123"]["expires_at"] == (
            100.0 + (server.PAIR_SESSION_EXPIRATION_MINUTES * 60)
        )
    finally:
        server.STATE.otp_cache = original_otp_cache
        server.STATE.session_cache = original_session_cache


def test_authenticated_pair_session_rejects_and_prunes_expired_legacy_session(monkeypatch):
    original_session_cache = dict(server.STATE.session_cache)

    try:
        created_at = 100.0
        server.STATE.session_cache = {
            "session-legacy": {
                "id": "session-legacy",
                "created_at": created_at,
                "authenticated": True,
            }
        }
        monkeypatch.setattr(
            server.time,
            "time",
            lambda: created_at + (server.PAIR_SESSION_EXPIRATION_MINUTES * 60) + 1.0,
        )

        assert server._is_authenticated_pair_session("session-legacy") is False
        assert "session-legacy" not in server.STATE.session_cache
    finally:
        server.STATE.session_cache = original_session_cache


def test_handle_auth_request_binds_stable_direct_client_id(monkeypatch):
    original_session_cache = dict(server.STATE.session_cache)
    original_client_names = dict(server.WEBRTC.client_display_names_by_owner)

    try:
        monkeypatch.setattr(
            server,
            "validate_otp_hash",
            lambda _: {
                "valid": True,
                "session_id": "direct-session-123",
                "session": {
                    "id": "direct-session-123",
                    "created_at": 100.0,
                    "expires_at": 200.0,
                    "authenticated": True,
                },
            },
        )

        result = asyncio.run(
            server.handle_auth_request(
                {
                    "hash": "hash-123",
                    "client_id": "ios-direct-auth-test-123",
                    "client_display_name": "  Synthetic Study Circle  ",
                }
            )
        )

        assert result["success"] is True
        assert result["session"]["owner_key"] == "direct:ios-direct-auth-test-123"
        assert result["session"]["canonical_session_id"] == "session::direct:ios-direct-auth-test-123"
        assert result["session"]["client_display_name"] == "  Synthetic Study Circle  "
        resolved = server.resolve_webrtc_chat_identity("direct-session-123")
        assert resolved.owner_key == "direct:ios-direct-auth-test-123"
        assert resolved.canonical_session_id == "session::direct:ios-direct-auth-test-123"
    finally:
        server.STATE.session_cache = original_session_cache
        server.WEBRTC.client_display_names_by_owner.clear()
        server.WEBRTC.client_display_names_by_owner.update(original_client_names)


def test_handle_auth_request_drops_invalid_display_name_without_failing_auth(monkeypatch):
    original_session_cache = dict(server.STATE.session_cache)
    original_client_names = dict(server.WEBRTC.client_display_names_by_owner)

    try:
        monkeypatch.setattr(
            server,
            "validate_otp_hash",
            lambda _: {
                "valid": True,
                "session_id": "direct-session-overlong-name",
                "session": {
                    "id": "direct-session-overlong-name",
                    "created_at": 100.0,
                    "expires_at": 200.0,
                    "authenticated": True,
                },
            },
        )

        result = asyncio.run(
            server.handle_auth_request(
                {
                    "hash": "hash-overlong",
                    "client_id": "ios-direct-auth-overlong-name",
                    "client_display_name": "A" * (server._CLIENT_DISPLAY_NAME_MAX_LENGTH + 1),
                }
            )
        )

        assert result["success"] is True
        assert result["session"]["owner_key"] == "direct:ios-direct-auth-overlong-name"
        assert result["session"]["client_display_name"] == ""
    finally:
        server.STATE.session_cache = original_session_cache
        server.WEBRTC.client_display_names_by_owner.clear()
        server.WEBRTC.client_display_names_by_owner.update(original_client_names)


def test_handle_auth_request_ignores_display_name_without_stable_direct_client_id(monkeypatch):
    original_session_cache = dict(server.STATE.session_cache)
    original_client_names = dict(server.WEBRTC.client_display_names_by_owner)

    try:
        monkeypatch.setattr(
            server,
            "validate_otp_hash",
            lambda _: {
                "valid": True,
                "session_id": "guest-direct-session",
                "session": {
                    "id": "guest-direct-session",
                    "created_at": 100.0,
                    "expires_at": 200.0,
                    "authenticated": True,
                },
            },
        )

        result = asyncio.run(
            server.handle_auth_request(
                {
                    "hash": "hash-guest",
                    "client_display_name": "Synthetic Guest Study Circle",
                }
            )
        )

        assert result["success"] is True
        assert result["session"]["client_display_name"] == ""
        assert "guest:guest-direct-session" not in server.WEBRTC.client_display_names_by_owner
    finally:
        server.STATE.session_cache = original_session_cache
        server.WEBRTC.client_display_names_by_owner.clear()
        server.WEBRTC.client_display_names_by_owner.update(original_client_names)
