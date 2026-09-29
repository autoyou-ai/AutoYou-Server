# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-da16506f8e4518271716d724


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio
from datetime import datetime

import pytest

import rest_api
from shared.adk_state import (
    AUTOYOU_CONVERSATION_SESSION_USER_STATE_KEY,
    AUTOYOU_OWNER_KEY_USER_STATE_KEY,
    AUTOYOU_REPLY_TARGET_USER_STATE_KEY,
)
from shared.session_execution import SESSION_CONTROL_STATE_KEY

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-da16506f8e4518271716d724"


class _DummySessionManager:
    def __init__(self):
        self.mapping = {"external-session": "old-internal"}
        self.created_sessions = []
        self.updated_sessions = []
        self.event_writes = []
        self.sessions = {
            ("user-1", "old-internal"): {SESSION_CONTROL_STATE_KEY: {}, "events": [], "message_count": 0},
            ("user-1", "internal-session"): {
                "events": [
                    {"timestamp": "2026-04-23T10:00:00"},
                    {"timestamp": "2026-04-23T10:05:00"},
                ],
                "message_count": 2,
            },
        }

    def get_mapped_session_id(self, external_session_id, user_id=None):
        del user_id
        return self.mapping.get(external_session_id)

    def set_session_mapping(self, external_session_id, internal_session_id, user_id=None):
        del user_id
        self.mapping[external_session_id] = internal_session_id

    async def create_user_session(self, user_id, session_id, initial_state, external_session_id=None):
        self.created_sessions.append((user_id, session_id, external_session_id))
        self.sessions[(user_id, session_id)] = {
            **dict(initial_state or {}),
            "events": [],
            "message_count": 0,
        }
        return self.sessions[(user_id, session_id)]

    async def update_user_session(self, user_id, session_id, session_data):
        self.updated_sessions.append((user_id, session_id, dict(session_data or {})))
        current = dict(self.sessions.get((user_id, session_id), {}))
        current.update(dict(session_data or {}))
        self.sessions[(user_id, session_id)] = current
        return current
    async def add_session_event(self, user_id, session_id, event_type, event_data, external_session_id=None):
        self.event_writes.append((user_id, session_id, event_type, external_session_id, dict(event_data or {})))
        current = dict(self.sessions.get((user_id, session_id), {}))
        events = list(current.get("events", []))
        events.append({"timestamp": datetime.now().isoformat(), "type": event_type, "data": dict(event_data or {})})
        current["events"] = events
        current["message_count"] = int(current.get("message_count", 0) or 0) + 1
        self.sessions[(user_id, session_id)] = current
        return True

    async def get_user_session(self, user_id, session_id):
        session_data = self.sessions.get((user_id, session_id))
        return dict(session_data) if isinstance(session_data, dict) else None


def test_ai_run_role_comes_from_server_auth_context_not_chat_metadata():
    client_spoof = rest_api._build_ai_agent_run_state_delta(
        {"autoyou_authenticated_actor_role": "admin"}
    )
    authenticated_admin = rest_api._build_ai_agent_run_state_delta(
        {}, authenticated_actor_role="admin"
    )

    assert client_spoof["autoyou_authenticated_actor_role"] == ""
    assert authenticated_admin["autoyou_authenticated_actor_role"] == "admin"


@pytest.mark.asyncio
async def test_process_chat_message_uses_direct_ollama_for_autoyou_signtoross_probe(monkeypatch):
    async def fake_ros_probe(message, metadata):
        assert "OpenSign" in message
        assert metadata["client"] == "mike"
        assert metadata["purpose"] == "signtoross_probe"
        return {
            "response": "The parties may consent to electronic execution through OpenSign and retain the signed copy with the final NDA.",
            "model": "ministral-3:8b",
            "ollama_api_base": "http://127.0.0.1:11434",
            "raw_done_reason": "stop",
        }

    monkeypatch.setattr(rest_api, "_active_ai_provider", lambda: "ollama")
    monkeypatch.setattr(rest_api, "_send_autoyou_signtoross_legal_advice_via_ollama", fake_ros_probe)
    monkeypatch.setattr(
        rest_api,
        "get_session_manager",
        lambda: (_ for _ in ()).throw(AssertionError("ADK session path should not run")),
    )

    request = rest_api.ChatRequest(
        message="Draft one OpenSign execution note.",
        user_id="mike-integration-doctor",
        session_id="autoyou-signtoross-legal-advice-probe",
        context=[],
        metadata={
            "client": "mike",
            "purpose": "signtoross_probe",
            "ai_provider": "ollama",
            "ollama_model": "ministral-3:8b",
        },
    )

    response = await rest_api.process_chat_message(request, ai_agent_url="http://127.0.0.1:8081")

    assert "OpenSign" in response.response
    assert response.session_id == "autoyou-signtoross-legal-advice-probe"
    assert response.agent_name == rest_api.ROOT_AGENT_NAME
    assert response.metadata["provider"] == "ollama"
    assert response.metadata["purpose"] == "signtoross_probe"
    assert response.metadata["model"] == "ministral-3:8b"


@pytest.mark.asyncio
async def test_process_chat_message_rebinds_mapping_after_run_sse_404(monkeypatch):
    session_manager = _DummySessionManager()
    send_attempts = []
    session_lookup_calls = []

    async def fake_send_message_to_ai_agent(
        user_id,
        session_id,
        message,
        ai_agent_url=None,
        *,
        context=None,
        metadata=None,
        state_delta=None,
        on_chunk=None,
    ):
        del user_id, message, ai_agent_url, context, metadata, state_delta, on_chunk
        send_attempts.append(session_id)
        if len(send_attempts) == 1:
            return {"_error": 404, "_body": "missing session"}
        return {
            "message": {"parts": [{"text": "Recovered reply"}]},
            "author": "autoyou",
            "id": "msg-1",
            "invocationId": "invoke-1",
            "usageMetadata": {},
        }

    async def fake_get_ai_agent_session(user_id, session_id, ai_agent_url=None):
        del user_id, ai_agent_url
        session_lookup_calls.append(session_id)
        if len(session_lookup_calls) == 1:
            return {"id": session_id}
        return None

    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setattr(rest_api, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(rest_api, "get_session_metrics", lambda: None)
    monkeypatch.setattr(rest_api, "_active_ai_provider", lambda: "ollama")
    monkeypatch.setattr(rest_api, "send_message_to_ai_agent", fake_send_message_to_ai_agent)
    monkeypatch.setattr(rest_api, "get_ai_agent_session", fake_get_ai_agent_session)
    monkeypatch.setattr(rest_api, "create_ai_agent_session", lambda user_id, ai_agent_url=None: asyncio.sleep(0, result="new-internal"))

    request = rest_api.ChatRequest(
        message="hello",
        user_id="user-1",
        session_id="external-session",
        context=[],
        metadata={"canonical_owner_key": "direct:device-1"},
    )

    response = await rest_api.process_chat_message(request, ai_agent_url="http://127.0.0.1:8081")

    assert send_attempts == ["old-internal", "new-internal"]
    assert response.response == "Recovered reply"
    assert response.metadata["ai_agent_session_id"] == "new-internal"
    assert session_manager.mapping["external-session"] == "new-internal"
    assert ("user-1", "new-internal", "external-session") in session_manager.created_sessions
    assert any(session_id == "new-internal" for _, session_id, _ in session_manager.updated_sessions)
    assert session_manager.event_writes[-1][1] == "new-internal"
    assert session_manager.event_writes[-1][3] == "external-session"


@pytest.mark.asyncio
async def test_process_chat_message_recovers_local_ai_worker_after_session_create_failure(monkeypatch):
    session_manager = _DummySessionManager()
    session_create_calls = []
    # from __debug_provenance_t__ import address
    recovery_reasons = []

    async def fake_create_ai_agent_session(user_id, ai_agent_url=None):
        del user_id, ai_agent_url
        session_create_calls.append(True)
        if len(session_create_calls) == 1:
            return None
        return "recovered-internal"

    async def fake_recover(ai_agent_url, *, reason, treat_already_healthy_as_recovered=False):
        recovery_reasons.append((ai_agent_url, reason, treat_already_healthy_as_recovered))
        return True

    async def fake_send_message_to_ai_agent(
        user_id,
        session_id,
        message,
        ai_agent_url=None,
        *,
        context=None,
        metadata=None,
        state_delta=None,
        on_chunk=None,
    ):
        del user_id, message, ai_agent_url, context, metadata, state_delta, on_chunk
        assert session_id == "recovered-internal"
        return {
            "message": {"parts": [{"text": "Recovered after restart"}]},
            "author": "autoyou",
            "id": "msg-recovered",
            "invocationId": "invoke-recovered",
            "usageMetadata": {},
        }

    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setattr(rest_api, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(rest_api, "get_session_metrics", lambda: None)
    monkeypatch.setattr(rest_api, "create_ai_agent_session", fake_create_ai_agent_session)
    monkeypatch.setattr(rest_api, "_recover_local_ai_agent_server_if_possible", fake_recover)
    monkeypatch.setattr(rest_api, "send_message_to_ai_agent", fake_send_message_to_ai_agent)

    request = rest_api.ChatRequest(
        message="hello",
        user_id="user-1",
        session_id="new-external-session",
        context=[],
        metadata={"canonical_owner_key": "direct:device-1"},
    )

    response = await rest_api.process_chat_message(request, ai_agent_url="http://127.0.0.1:8081")

    assert response.response == "Recovered after restart"
    assert response.metadata["ai_agent_session_id"] == "recovered-internal"
    assert len(session_create_calls) == 2
    assert recovery_reasons == [("http://127.0.0.1:8081", "session creation", True)]
    assert session_manager.mapping["new-external-session"] == "recovered-internal"


@pytest.mark.asyncio
async def test_get_session_info_accepts_external_session_id(monkeypatch):
    session_manager = _DummySessionManager()
    session_manager.mapping["external-session"] = "internal-session"

    monkeypatch.setattr(rest_api, "get_session_manager", lambda: session_manager)

    info = await rest_api.get_session_info("user-1", "external-session")

    assert info.session_id == "external-session"
    assert info.user_id == "user-1"
    assert info.message_count == 2
    assert info.created_at.isoformat().startswith("2026-04-23T10:00:00")
    assert info.last_activity.isoformat().startswith("2026-04-23T10:05:00")


def test_build_context_usage_snapshot_accepts_camel_case_usage_metadata(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "ministral-3:8b")
    monkeypatch.setattr(rest_api, "_resolve_context_window", lambda provider, model_name: (8192, "test"))

    snapshot = rest_api._build_context_usage_snapshot(
        ai_agent_session_id="ai-session-1",
        user_id="user::guest:test",
        external_session_id="session::guest:test",
        usage_metadata={
            "promptTokenCount": 145,
            "candidatesTokenCount": 12,
            "totalTokenCount": 157,
            "cachedContentTokenCount": 3,
        },
    )

    assert snapshot is not None
    assert snapshot["prompt_tokens"] == 145
    assert snapshot["candidates_tokens"] == 12
    assert snapshot["total_tokens"] == 157
    assert snapshot["cached_tokens"] == 3
    assert snapshot["summary_text"] == "Ctx 145/8.2k"


def test_resolve_context_window_does_not_trust_litellm_fallback_for_ollama_without_num_ctx(monkeypatch):
    class _DummyLiteLlm:
        @staticmethod
        def get_model_info(model_name):
            return {"max_input_tokens": 262144, "model_name": model_name}

    monkeypatch.setattr(rest_api, "_configured_ollama_num_ctx", lambda: None)
    monkeypatch.setitem(rest_api.__dict__, "_CONTEXT_WINDOW_CACHE", {})
    monkeypatch.setitem(__import__("sys").modules, "litellm", _DummyLiteLlm())

    context_window, source = rest_api._resolve_context_window("ollama", "ollama_chat/ministral-3:8b")

    assert context_window is None
    assert source is None


def test_resolve_context_window_still_uses_explicit_ollama_num_ctx(monkeypatch):
    monkeypatch.setattr(rest_api, "_configured_ollama_num_ctx", lambda: 8192)
    monkeypatch.setattr(rest_api, "_configured_ollama_num_ctx_source", lambda: "model_behavior.num_ctx")
    monkeypatch.setitem(rest_api.__dict__, "_CONTEXT_WINDOW_CACHE", {})

    context_window, source = rest_api._resolve_context_window("ollama", "ollama_chat/ministral-3:8b")

    assert context_window == 8192
    assert source == "model_behavior.num_ctx"


@pytest.mark.asyncio
async def test_process_chat_message_surfaces_ollama_memory_error(monkeypatch):
    session_manager = _DummySessionManager()

    async def fake_send_message_to_ai_agent(
        user_id,
        session_id,
        message,
        ai_agent_url=None,
        *,
        context=None,
        metadata=None,
        state_delta=None,
        on_chunk=None,
    ):
        del user_id, session_id, message, ai_agent_url, context, metadata, state_delta, on_chunk
        return {
            "author": "autoyou",
            "id": "msg-ollama-1",
            "invocationId": "invoke-ollama-1",
            "usageMetadata": {},
            "errorMessage": (
                'litellm.APIConnectionError: Ollama_chatException - '
                '{"error":"model requires more system memory (6.4 GiB) than is available (5.1 GiB)"}'
            ),
        }

    monkeypatch.setattr(rest_api, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(rest_api, "get_session_metrics", lambda: None)
    monkeypatch.setattr(rest_api, "_active_ai_provider", lambda: "ollama")
    monkeypatch.setattr(rest_api, "send_message_to_ai_agent", fake_send_message_to_ai_agent)
    monkeypatch.setattr(
        rest_api,
        "create_ai_agent_session",
        lambda user_id, ai_agent_url=None: asyncio.sleep(0, result="ollama-session"),
    )

    request = rest_api.ChatRequest(
        message="hello",
        user_id="user-1",
        session_id="external-session",
        context=[],
        metadata={"canonical_owner_key": "direct:device-1"},
    )

    response = await rest_api.process_chat_message(request, ai_agent_url="http://127.0.0.1:8081")

    assert "needs 6.4 GiB" in response.response
    assert "5.1 GiB is available" in response.response
    assert "smaller Ollama model" in response.response
    assert response.metadata["error"] is True
    assert response.metadata["ai_backend_error"]["kind"] == "ollama_insufficient_memory"
    assert "model requires more system memory" in response.metadata["error_message"]


@pytest.mark.asyncio
async def test_process_chat_message_passes_webrtc_reply_target_as_run_state_delta(monkeypatch):
    session_manager = _DummySessionManager()
    session_manager.mapping = {"session::telegram:5550001001": "internal-session"}
    captured = {}

    async def fake_send_message_to_ai_agent(
        user_id,
        session_id,
        message,
        ai_agent_url=None,
        *,
        context=None,
        metadata=None,
        state_delta=None,
        on_chunk=None,
    ):
        del user_id, session_id, message, ai_agent_url, context, metadata, on_chunk
        captured["state_delta"] = dict(state_delta or {})
        return {
            "message": {"parts": [{"text": "ok"}]},
            "author": "autoyou",
            "id": "msg-state-delta",
            "invocationId": "invoke-state-delta",
            "usageMetadata": {},
        }

    async def fake_get_ai_agent_session(user_id, session_id, ai_agent_url=None):
        del user_id, ai_agent_url
        return {"id": session_id}

    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setattr(rest_api, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(rest_api, "get_session_metrics", lambda: None)
    monkeypatch.setattr(rest_api, "_active_ai_provider", lambda: "ollama")
    monkeypatch.setattr(rest_api, "send_message_to_ai_agent", fake_send_message_to_ai_agent)
    monkeypatch.setattr(rest_api, "get_ai_agent_session", fake_get_ai_agent_session)
    monkeypatch.setattr(
        rest_api,
        "create_ai_agent_session",
        lambda user_id, ai_agent_url=None: (_ for _ in ()).throw(
            AssertionError("session mapping should avoid live AI Agent session creation")
        ),
    )

    request = rest_api.ChatRequest(
        message="play aura",
        user_id="user::telegram:5550001001",
        session_id="session::telegram:5550001001",
        context=[],
        metadata={
            "canonical_owner_key": "telegram:5550001001",
            "reply_target": {
                "transport": "webrtc",
                "owner_key": "telegram:5550001001",
                "session_id": "voice-session",
            },
            "conversation_session_id": (
                "session::dest::server::local%3Adesk-server::owner::telegram%3A5550001001"
                "::target::5550001001::thread::7"
            ),
            "conversation_thread_id": 7,
            "conversation_force_target": True,
            "session_execution": {"queue_position": 3},
        },
    )

    response = await rest_api.process_chat_message(request, ai_agent_url="http://127.0.0.1:8081")

    assert response.response == "ok"
    state_delta = captured["state_delta"]
    assert state_delta[AUTOYOU_REPLY_TARGET_USER_STATE_KEY] == {
        "transport": "webrtc",
        "owner_key": "telegram:5550001001",
        "session_id": "voice-session",
    }
    assert state_delta[AUTOYOU_OWNER_KEY_USER_STATE_KEY] == "telegram:5550001001"
    assert state_delta[AUTOYOU_CONVERSATION_SESSION_USER_STATE_KEY] == (
        "session::dest::server::local%3Adesk-server::owner::telegram%3A5550001001"
        "::target::5550001001::thread::7"
    )
    assert state_delta[SESSION_CONTROL_STATE_KEY]["owner_key"] == "telegram:5550001001"
    assert state_delta[SESSION_CONTROL_STATE_KEY]["queue_position"] == 3


@pytest.mark.asyncio
async def test_process_chat_message_error_event_preserves_memory_metadata(monkeypatch):
    session_manager = _DummySessionManager()
    session_manager.mapping["session::webrtc:device-synthetic"] = "internal-webrtc-session"

    async def fake_send_message_to_ai_agent(*args, **kwargs):
        raise RuntimeError("synthetic upstream failure")

    async def fake_get_ai_agent_session(user_id, session_id, ai_agent_url=None):
        del user_id, ai_agent_url
        return {"id": session_id}

    monkeypatch.setattr(rest_api, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(rest_api, "get_session_metrics", lambda: None)
    monkeypatch.setattr(rest_api, "_active_ai_provider", lambda: "google")
    monkeypatch.setattr(rest_api, "get_ai_agent_session", fake_get_ai_agent_session)
    monkeypatch.setattr(rest_api, "send_message_to_ai_agent", fake_send_message_to_ai_agent)

    request = rest_api.ChatRequest(
        message="remember this synthetic failure",
        user_id="user::webrtc:device-synthetic",
        session_id="session::webrtc:device-synthetic",
        context=[],
        metadata={
            "client": "autoyou-datachannel",
            "pairing_mode": "local_pair",
            "canonical_owner_key": "webrtc:device-synthetic",
            "conversation_session_id": (
                "session::dest::server::local%3Aserver::owner::webrtc%3Adevice-synthetic"
                "::target::device-synthetic"
            ),
            "conversation_thread_id": 1,
            "destination_session_id": "device-synthetic",
            "raw_session_id": "device-synthetic",
        },
    )

    response = await rest_api.process_chat_message(request, ai_agent_url="http://127.0.0.1:8081")

    assert response.metadata["error"] is True
    user_id, session_id, event_type, external_session_id, event_data = session_manager.event_writes[-1]
    assert user_id == "user::webrtc:device-synthetic"
    assert session_id == "internal-webrtc-session"
    assert event_type == "error"
    assert external_session_id == "session::webrtc:device-synthetic"
    memory_metadata = event_data["memory_metadata"]
    assert memory_metadata["client"] == "autoyou-datachannel"
    assert memory_metadata["pairing_mode"] == "local_pair"
    assert memory_metadata["canonical_owner_key"] == "webrtc:device-synthetic"
    assert memory_metadata["adk_session_id"] == "internal-webrtc-session"
