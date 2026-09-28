# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-a2fb621ac120621faf543aee

"""Synthetic contracts for optional per-pair client display names."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-a2fb621ac120621faf543aee"


from copy import deepcopy
import inspect
from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import rest_api
import server
from shared.datachannel_manager import DataChannelMessage, MessageHeader, MessageType


def test_normalize_client_display_name_preserves_nonblank_text_and_keeps_blank_legacy_behavior():
    assert server._normalize_client_display_name(None) == ""
    assert server._normalize_client_display_name("   ") == ""
    assert server._normalize_client_display_name("  Ada's Study Circle  ") == "  Ada's Study Circle  "


@pytest.mark.parametrize(
    "value",
    (
        42,
        "Ada\nStudy Circle",
        "A" * (server._CLIENT_DISPLAY_NAME_MAX_LENGTH + 1),
    ),
)
def test_normalize_client_display_name_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        server._normalize_client_display_name(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("  Ada's Study Circle  ", "  Ada's Study Circle  "),
        (None, ""),
        ("   ", ""),
        (42, ""),
        ("Ada\nStudy Circle", ""),
        ("A" * (server._CLIENT_DISPLAY_NAME_MAX_LENGTH + 1), ""),
    ),
)
def test_transport_client_display_name_degrades_to_legacy_behavior_instead_of_failing(value, expected):
    assert server._client_display_name_from_transport(value) == expected


def test_client_display_names_require_a_stable_owner_key():
    with pytest.raises(ValueError, match="stable client"):
        server._normalize_client_identity_owner_key("guest:synthetic-session")


def test_client_identity_defaults_are_live_only_until_history_is_explicitly_enabled():
    cfg = {"ai_agent": {"record_messages_in_database": True}}

    assert server._apply_default_client_identity_config(cfg) is True
    assert cfg["client_identity"] == {
        "store_client_names_in_history": False,
        "name_overrides": {},
    }
    assert server._client_name_history_enabled(cfg) is False


def test_incognito_recording_mode_removes_client_name_overrides_from_config():
    cfg = {
        "ai_agent": {"record_messages_in_database": False},
        "client_identity": {
            "store_client_names_in_history": True,
            "name_overrides": {"local:synthetic-study-device": "Instructor Ada"},
        },
    }

    assert server._apply_default_client_identity_config(cfg) is True
    assert cfg["client_identity"]["store_client_names_in_history"] is True
    assert cfg["client_identity"]["name_overrides"] == {}
    assert server._client_name_history_enabled(cfg) is False
    assert server._configured_client_name_override("local:synthetic-study-device", cfg=cfg) == ""


def test_configured_client_name_override_requires_opt_in_and_is_scoped_to_pair_owner():
    cfg = {
        "ai_agent": {"record_messages_in_database": True},
        "client_identity": {
            "store_client_names_in_history": True,
            "name_overrides": {"local:synthetic-study-device": "Instructor Ada"},
        },
    }

    assert server._configured_client_name_override("local:synthetic-study-device", cfg=cfg) == "Instructor Ada"
    assert server._configured_client_name_override("cloud:synthetic-study-device", cfg=cfg) == ""

    cfg["client_identity"]["store_client_names_in_history"] = False
    assert server._configured_client_name_override("local:synthetic-study-device", cfg=cfg) == ""


def test_admin_config_patch_exposes_explicit_client_name_history_setting():
    cfg, touched, _theme = server._apply_admin_ui_config_patch(
        server._default_config(),
        {
            "client_identity": {
                "store_client_names_in_history": True,
            }
        },
    )

    assert cfg["client_identity"]["store_client_names_in_history"] is True
    assert cfg["client_identity"]["name_overrides"] == {}
    assert "client_identity" in touched
    assert "ai_agent" not in touched


def test_webrtc_client_display_name_snapshot_uses_server_override_and_clear_preserves_owner_scope(monkeypatch):
    local_identity = SimpleNamespace(owner_key="local:synthetic-study-device")
    cloud_identity = SimpleNamespace(owner_key="cloud:synthetic-study-device")
    manager = server.WebRTCManager()
    identities = {
        "synthetic-local-session": local_identity,
        "synthetic-cloud-session": cloud_identity,
    }
    cfg = {
        "ai_agent": {"record_messages_in_database": True},
        "client_identity": {
            "store_client_names_in_history": True,
            "name_overrides": {},
        },
    }
    persisted_cfg = deepcopy(cfg)

    def load_config_for_update(*, copy_config):
        assert copy_config is True
        return deepcopy(persisted_cfg)

    def save_and_reload_config(updated_cfg):
        persisted_cfg.clear()
        persisted_cfg.update(deepcopy(updated_cfg))
        return deepcopy(persisted_cfg)

    monkeypatch.setattr(server.STATE, "config", cfg)
    monkeypatch.setattr(server, "WEBRTC", manager)
    monkeypatch.setattr(server, "_loaded_config_for_update", load_config_for_update)
    monkeypatch.setattr(server, "_save_and_reload_state_config", save_and_reload_config)
    monkeypatch.setattr(manager, "_resolve_chat_identity", lambda session_id: identities.get(session_id))

    assert manager.remember_client_display_name(local_identity, "Local Study Circle") == "Local Study Circle"
    assert manager.remember_client_display_name(cloud_identity, "Cloud Study Circle") == "Cloud Study Circle"

    local_snapshot = manager.client_display_name_snapshot("synthetic-local-session")
    assert local_snapshot["reported_client_display_name"] == "Local Study Circle"
    assert local_snapshot["server_name_override"] == ""
    assert local_snapshot["client_display_name"] == "Local Study Circle"
    assert local_snapshot["history_enabled"] is True
    assert manager.client_name_history_metadata(local_identity) == {
        "client_display_name": "Local Study Circle"
    }

    updated = server._set_client_name_override("local:synthetic-study-device", "Instructor Ada")
    assert updated["client_display_name"] == "Instructor Ada"
    assert manager.client_display_name_snapshot("synthetic-local-session")["server_name_override"] == "Instructor Ada"
    assert manager.client_display_name_snapshot("synthetic-local-session")["client_display_name"] == "Instructor Ada"
    assert manager.client_display_name_snapshot("synthetic-cloud-session")["client_display_name"] == "Cloud Study Circle"

    cleared = server._set_client_name_override("local:synthetic-study-device", "")
    assert cleared["client_display_name"] == "Local Study Circle"
    assert persisted_cfg["client_identity"]["name_overrides"] == {}
    assert manager.client_display_name_snapshot("synthetic-local-session")["client_display_name"] == "Local Study Circle"

    assert manager.remember_client_display_name(local_identity, "") == ""
    assert manager.client_display_name_snapshot("synthetic-local-session")["client_display_name"] == ""

    server.STATE.config = {
        "ai_agent": {"record_messages_in_database": False},
        "client_identity": {"store_client_names_in_history": True, "name_overrides": {}},
    }
    assert manager.client_name_history_metadata(cloud_identity) == {}


@pytest.mark.asyncio
async def test_webrtc_chat_strips_untrusted_client_name_metadata_when_history_is_off(monkeypatch):
    webrtc = server.WebRTCManager()
    captured = {}
    identity = SimpleNamespace(
        canonical_session_id="session::cloud:synthetic-client",
        canonical_user_id="user::cloud:synthetic-client",
        owner_key="cloud:synthetic-client",
        raw_session_id="synthetic-relay",
    )

    class DummyDataChannelManager:
        async def send_message(self, message):
            return True

    class ExecutingSessionManager:
        async def submit_turn(self, identity_arg, factory, **_kwargs):
            return await factory()

    async def fake_process_chat_message(chat_request, **_kwargs):
        captured["metadata"] = dict(chat_request.metadata or {})
        return SimpleNamespace(response="synthetic reply", metadata={})

    monkeypatch.setattr(
        server.STATE,
        "config",
        {
            "ai_agent": {"record_messages_in_database": True},
            "client_identity": {"store_client_names_in_history": False, "name_overrides": {}},
        },
    )
    monkeypatch.setattr(webrtc, "_resolve_chat_identity", lambda _session_id: identity)
    monkeypatch.setattr(server, "get_session_execution_manager", lambda: ExecutingSessionManager())
    monkeypatch.setattr(rest_api, "process_chat_message", fake_process_chat_message)
    webrtc.datachannel_managers["synthetic-relay"] = DummyDataChannelManager()

    await webrtc._handle_chat_message(
        DataChannelMessage(
            header=MessageHeader(
                message_id="synthetic-message",
                message_type=MessageType.CHAT,
                timestamp=0.0,
                session_id="synthetic-relay",
                user_id="synthetic-client",
            ),
            payload={
                "message": "hello",
                "context": [],
                "metadata": {"client_display_name": "untrusted synthetic name"},
            },
        )
    )

    assert "client_display_name" not in captured["metadata"]


@pytest.mark.asyncio
async def test_legacy_ai_settings_clear_live_name_overrides_when_history_is_off(monkeypatch):
    cfg = {
        "ai_agent": {"record_messages_in_database": True},
        "client_identity": {
            "store_client_names_in_history": True,
            "name_overrides": {"local:synthetic-study-device": "Synthetic Instructor"},
        },
    }

    class FakeWebRTC:
        clear_calls = 0

        def clear_client_name_overrides(self):
            self.clear_calls += 1

    async def no_op():
        return None

    fake_webrtc = FakeWebRTC()
    optional_form_values = {
        name: None
        for name in inspect.signature(server.save_config_endpoint).parameters
        if name not in {"request", "kind"}
    }
    monkeypatch.setattr(server, "_require_login", lambda _request: None)
    monkeypatch.setattr(server, "_config_write_block_reason", lambda: None)
    monkeypatch.setattr(server, "_loaded_config_for_update", lambda: cfg)
    monkeypatch.setattr(server, "_persist_state_config", lambda _cfg: "test")
    monkeypatch.setattr(server, "stop_ai_agent_server", no_op)
    monkeypatch.setattr(server, "WEBRTC", fake_webrtc)
    monkeypatch.setattr(server.STATE, "service_manager", None)

    await server.save_config_endpoint(
        request=SimpleNamespace(),
        kind="ai_agent",
        **optional_form_values,
    )

    assert fake_webrtc.clear_calls == 1
