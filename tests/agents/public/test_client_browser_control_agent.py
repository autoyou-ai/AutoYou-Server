# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-90999e83766cb4fb6cbcd393


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio
from types import SimpleNamespace

import autoyou_agents.agent as root_agent_module
from autoyou_agents.client_browser_control_agent.agent import (
    _is_client_browser_control_request,
    build_client_browser_control_payload,
    create_client_browser_control_agent,
)
from autoyou_agents.shared_tools.agent_install_registry import DEFAULT_AGENT_INSTALL_STATES

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-90999e83766cb4fb6cbcd393"


def test_client_browser_control_payload_normalizes_remote_urls_to_https():
    payload = build_client_browser_control_payload("open http://amazon.com")

    assert payload["event"] == "client_browser_control"
    assert payload["action"] == "open_url"
    assert payload["url"] == "https://amazon.com"

    local_payload = build_client_browser_control_payload("open localhost:8001")
    assert local_payload["url"] == "http://localhost:8001"


def test_client_browser_control_payload_allows_local_agent_paths():
    payload = build_client_browser_control_payload("open audio player")

    assert payload["action"] == "open_url"
    assert payload["url"] == "/agent/audio_agent/"

    audio_agent_payload = build_client_browser_control_payload("go to audio agent")
    assert audio_agent_payload["success"] is False

    audio_web_app_payload = build_client_browser_control_payload("go to audio agent web app")
    assert audio_web_app_payload["action"] == "open_url"
    assert audio_web_app_payload["url"] == "/agent/audio_agent/"

    agent_home_payload = build_client_browser_control_payload("go to agent app")
    assert agent_home_payload["action"] == "open_home"
    assert "url" not in agent_home_payload

    main_agent_payload = build_client_browser_control_payload("go to main agent website")
    assert main_agent_payload["action"] == "open_home"
    assert "url" not in main_agent_payload

    main_app_payload = build_client_browser_control_payload("go to main app")
    assert main_app_payload["action"] == "open_home"
    assert "url" not in main_app_payload

    notes_website_payload = build_client_browser_control_payload("go to notes website")
    assert notes_website_payload["action"] == "open_url"
    assert notes_website_payload["url"] == "/agent/notes_agent/"

    notes_app_payload = build_client_browser_control_payload("go to notes app")
    assert notes_app_payload["action"] == "open_url"
    assert notes_app_payload["url"] == "/agent/notes_agent/"

    notes_web_app_payload = build_client_browser_control_payload("go to notes web app")
    assert notes_web_app_payload["action"] == "open_url"
    assert notes_web_app_payload["url"] == "/agent/notes_agent/"

    page_app_payload = build_client_browser_control_payload("go to page app")
    assert page_app_payload["action"] == "open_url"
    assert page_app_payload["url"] == "/agent/page_agent/"

    donation_payload = build_client_browser_control_payload("open donation agent")
    assert donation_payload["action"] == "open_url"
    assert donation_payload["url"] == "/agent/donation_agent/"

    collector_payload = build_client_browser_control_payload("open data collector app")
    assert collector_payload["action"] == "open_url"
    assert collector_payload["url"] == "/agent/data_collector_agent/"


def test_client_browser_control_payload_rejects_unsafe_url():
    payload = build_client_browser_control_payload(action="open_url", url="javascript:alert(1)")

    assert payload["success"] is False
    assert "safe http(s)" in payload["reason"]


def test_client_browser_control_payload_preserves_known_agent_source():
    payload = build_client_browser_control_payload(
        action="open_url", url="https://example.test/page", source="internet_agent"
    )

    assert payload["source"] == "internet_agent"
    assert payload["url"] == "https://example.test/page"


def test_client_browser_control_request_detection_is_explicit():
    assert _is_client_browser_control_request("open amazon.com")
    assert not _is_client_browser_control_request("go to audio agent")
    assert _is_client_browser_control_request("go to audio agent web app")
    assert _is_client_browser_control_request("go to agent app")
    assert _is_client_browser_control_request("go to agent website")
    assert _is_client_browser_control_request("go to main app")
    assert _is_client_browser_control_request("go to notes website")
    assert _is_client_browser_control_request("go to notes app")
    assert _is_client_browser_control_request("go to notes web app")
    assert _is_client_browser_control_request("go to page app")
    assert _is_client_browser_control_request("open data collector app")
    assert _is_client_browser_control_request("reload the browser")
    assert not _is_client_browser_control_request("go to agent")
    assert not _is_client_browser_control_request("go to main agent")
    assert not _is_client_browser_control_request("go to autoyou agent")
    assert not _is_client_browser_control_request("go to notes agent")
    assert not _is_client_browser_control_request("tell me about amazon.com")


def test_client_browser_control_agent_is_builtin_and_installed_by_default():
    assert DEFAULT_AGENT_INSTALL_STATES["client_browser_control_agent"] is True
    assert "client_browser_control_agent" in root_agent_module._STATIC_AGENT_FACTORY_MAP
    assert create_client_browser_control_agent("dummy-model").name == "autoyou_client_browser_control_agent"


def test_webrtc_sends_client_browser_control_to_target(monkeypatch):
    import server

    class FakeDataChannelManager:
        def __init__(self):
            self.sent = []

        async def send_message(self, message):
            self.sent.append(message)
            return True

    datachannel = FakeDataChannelManager()
    webrtc = server.WebRTCManager()
    webrtc.datachannel_managers["ios-session-001"] = datachannel
    monkeypatch.setattr(
        webrtc,
        "_resolve_chat_identity",
        lambda session_id: SimpleNamespace(owner_key="local:ios-device-001"),
    )

    success, status = asyncio.run(
        webrtc.send_client_browser_control_to_reply_target(
            {"transport": "webrtc", "session_id": "ios-session-001"},
            {"action": "open_url", "url": "http://example.com"},
        )
    )

    assert success is True
    assert status["status"] == "sent"
    assert len(datachannel.sent) == 1
    sent = datachannel.sent[0]
    assert sent.header.message_type.value == "voice_call_control"
    assert sent.payload["event"] == "client_browser_control"
    assert sent.payload["action"] == "open_url"
    assert sent.payload["url"] == "https://example.com"
    assert sent.payload["source"] == "client_browser_control_agent"


def test_webrtc_native_keyboard_is_targeted_and_idempotent(monkeypatch):
    import server

    class FakeDataChannelManager:
        def __init__(self):
            self.sent = []
            # from __debug_provenance_t__ import address

        async def send_message(self, message):
            self.sent.append(message)
            return True

    manager = FakeDataChannelManager()
    webrtc = server.WebRTCManager()
    webrtc.datachannel_managers["ios-session-001"] = manager
    monkeypatch.setattr(
        webrtc,
        "_resolve_chat_identity",
        lambda session_id: SimpleNamespace(owner_key="local:ios-device-001"),
    )

    success, first = asyncio.run(
        webrtc.send_remote_desktop_keyboard_control_to_reply_target(
            {"transport": "webrtc", "session_id": "ios-session-001"},
            {"action": "show"},
        )
    )
    assert success is True
    assert first["status"] == "sent"
    control_id = first["control_id"]
    assert len(manager.sent) == 1
    assert manager.sent[0].payload["event"] == "remote_desktop_keyboard"
    assert manager.sent[0].payload["action"] == "show"

    success, second = asyncio.run(
        webrtc.send_remote_desktop_keyboard_control_to_reply_target(
            {"transport": "webrtc", "session_id": "ios-session-001"},
            {"action": "show"},
        )
    )
    assert success is True
    assert second["status"] == "resent"
    assert second["control_id"] == control_id
    assert len(manager.sent) == 2

    state_message = server.create_voice_call_control_message(
        payload={
            "event": "remote_desktop_keyboard",
            "action": "state",
            "platform": "ios",
            "source": "remote_desktop_agent",
            "control_id": control_id,
            "keyboard_state": "hidden",
        },
        session_id="ios-session-001",
        user_id="synthetic-client",
    )
    asyncio.run(webrtc._handle_voice_call_control_message(state_message))
    assert webrtc.remote_desktop_keyboard_connection_proof(
        session_id="ios-session-001",
    )["keyboard_state"] == "hidden"

    applied = []
    monkeypatch.setattr(server, "execute_remote_desktop_keyboard", lambda payload: applied.append(payload) or True)
    message = server.create_voice_call_control_message(
        payload={
            "event": "remote_desktop_keyboard",
            "action": "input",
            "platform": "ios",
            "source": "remote_desktop_agent",
            "control_id": control_id,
            "text": "synthetic input",
        },
        session_id="ios-session-001",
        user_id="synthetic-server",
    )
    asyncio.run(webrtc._handle_voice_call_control_message(message))
    assert applied and applied[0]["text"] == "synthetic input"


def test_webrtc_native_keyboard_rejects_ambiguous_fallback(monkeypatch):
    import server

    class FakeDataChannelManager:
        async def send_message(self, message):
            return True

    webrtc = server.WebRTCManager()
    webrtc.datachannel_managers["ios-session-001"] = FakeDataChannelManager()
    webrtc.datachannel_managers["android-session-002"] = FakeDataChannelManager()
    monkeypatch.setattr(
        webrtc,
        "_resolve_chat_identity",
        lambda session_id: SimpleNamespace(owner_key=f"local:{session_id}"),
    )

    success, result = asyncio.run(
        webrtc.send_remote_desktop_keyboard_control_to_reply_target(
            {"transport": "webrtc"},
            {"action": "show"},
            allow_single_live_fallback=True,
        )
    )
    assert success is False
    assert result["resolution"] == "ambiguous"


def test_webrtc_records_client_browser_control_result(monkeypatch):
    import server

    webrtc = server.WebRTCManager()
    monkeypatch.setattr(
        webrtc,
        "_ordered_related_session_ids",
        lambda session_id: [session_id, "local:ios-device-001"],
    )
    message = server.create_voice_call_control_message(
        payload={
            "event": "client_browser_control_result",
            "platform": "ios",
            "timestamp_ms": 123456789,
            "control_id": "client-browser-1",
            "action": "open_url",
            "status": "accepted",
            "url": "https://example.com",
        },
        session_id="ios-session-001",
        user_id="tester",
    )

    asyncio.run(webrtc._handle_voice_call_control_message(message))

    result = webrtc.client_browser_control_results_by_session["local:ios-device-001"]
    assert result["event"] == "client_browser_control_result"
    assert result["action"] == "open_url"
    assert result["status"] == "accepted"
    assert result["url"] == "https://example.com"
