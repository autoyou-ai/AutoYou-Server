# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-72933c63515c82c4e4d35be3


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-72933c63515c82c4e4d35be3"

import server


def test_get_current_password_returns_server_password_in_keystore_mode():
    original_server_password = server.STATE.server_password
    original_unlock_password = server.STATE.config_unlock_password
    original_config_store = server.STATE.config_store

    try:
        server._set_config_session(
            config_store=server.CONFIG_STORE_KEYSTORE,
            server_password="1234",
        )

        assert server.get_current_password() == "1234"
    finally:
        server._set_config_session(
            config_store=original_config_store,
            server_password=original_server_password,
            config_unlock_password=original_unlock_password,
        )


def test_generate_otp_hash_uses_server_password_in_keystore_mode(monkeypatch):
    original_server_password = server.STATE.server_password
    original_unlock_password = server.STATE.config_unlock_password
    original_config_store = server.STATE.config_store
    original_config = server.STATE.config
    original_otp_cache = dict(server.STATE.otp_cache)

    try:
        server._set_config_session(
            config_store=server.CONFIG_STORE_KEYSTORE,
            server_password="1234",
        )
        server.STATE.config = {"server": {"name": "AutoYou-Server"}}
        server.STATE.otp_cache = {}

        monkeypatch.setattr(server, "generate_otp", lambda: "otp-1234")
        monkeypatch.setattr(server, "generate_hash", lambda value: f"hash::{value}")

        otp = server.generate_otp_hash_and_cache(5)

        assert otp == "otp-1234"
        assert "hash::otp-1234:1234" in server.STATE.otp_cache
    finally:
        server._set_config_session(
            config_store=original_config_store,
            server_password=original_server_password,
            config_unlock_password=original_unlock_password,
        )
        server.STATE.config = original_config
        server.STATE.otp_cache = original_otp_cache