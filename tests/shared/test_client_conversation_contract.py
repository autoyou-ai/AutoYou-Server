# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-082288b889a1c16cb07dcc49

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import sys
from types import SimpleNamespace

from shared.client_conversation_contract import (
    build_autoyou_conversation_metadata,
    build_conversation_metadata,
)

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-082288b889a1c16cb07dcc49"


def test_conversation_metadata_carries_server_pairing_and_session_identity():
    identity = SimpleNamespace(
        transport="cloud",
        sender_id="sender-synthetic",
        raw_session_id="raw-synthetic",
        owner_key="cloud:sender-synthetic",
        canonical_user_id="user::cloud:sender-synthetic",
        canonical_session_id="session::cloud:sender-synthetic",
        thread_id=3,
    )

    metadata = build_conversation_metadata(
        identity,
        server_id="server-synthetic",
        server_identity_key="cloud:server-synthetic",
    )

    assert metadata["pairing_mode"] == "cloud_pair"
    assert metadata["server_id"] == "server-synthetic"
    assert metadata["server_identity_key"] == "cloud:server-synthetic"
    assert metadata["canonical_session_id"] == "session::cloud:sender-synthetic"
    assert metadata["destination_session_id"] == "sender-synthetic"
    assert metadata["raw_session_id"] == "raw-synthetic"
    assert metadata["conversation_thread_id"] == 3


def test_conversation_metadata_prefers_identity_pairing_mode():
    identity = SimpleNamespace(
        transport="local",
        sender_id="device-synthetic",
        raw_session_id="raw-synthetic",
        owner_key="local:device-synthetic",
        canonical_user_id="user::local:device-synthetic",
        canonical_session_id="session::local:device-synthetic",
        pairing_mode="totp_pair",
    )

    metadata = build_conversation_metadata(
        identity,
        server_id="server-synthetic",
        server_identity_key="local:server-synthetic",
    )

    assert metadata["pairing_mode"] == "totp_pair"


def test_conversation_metadata_treats_plain_webrtc_as_local_pair():
    identity = SimpleNamespace(
        transport="webrtc",
        sender_id="device-synthetic",
        raw_session_id="raw-synthetic",
        owner_key="webrtc:device-synthetic",
        canonical_user_id="user::webrtc:device-synthetic",
        canonical_session_id="session::webrtc:device-synthetic",
    )

    metadata = build_conversation_metadata(
        identity,
        server_id="server-synthetic",
        server_identity_key="local:server-synthetic",
    )

    assert metadata["pairing_mode"] == "local_pair"


def test_autoyou_conversation_metadata_uses_runtime_server_identity(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "server",
        SimpleNamespace(
            _get_stable_server_id=lambda: "server-runtime-synthetic",
            _get_server_identity_key=lambda: "local:runtime-synthetic",
        ),
    )
    identity = SimpleNamespace(
        transport="telegram",
        sender_id="5550001111",
        raw_session_id="chat-synthetic",
        owner_key="telegram:5550001111",
        canonical_user_id="user::telegram:5550001111",
        canonical_session_id="session::telegram:5550001111",
        thread_id=2,
    )
    # from __debug_provenance_m__ import of

    metadata = build_autoyou_conversation_metadata(identity, reset=True)

    assert metadata["server_id"] == "server-runtime-synthetic"
    assert metadata["server_identity_key"] == "local:runtime-synthetic"
    assert metadata["canonical_owner_key"] == "telegram:5550001111"
    assert metadata["destination_session_id"] == "chat-synthetic"
    assert metadata["conversation_reset"] is True
