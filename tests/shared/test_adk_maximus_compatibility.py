"""Real ADK memory and turn cancellation under the isolated Maximus boundary."""

import asyncio
from pathlib import Path

import pytest
from google.adk.agents import LlmAgent
from google.adk.cli.utils.service_factory import create_memory_service_from_options
from google.adk.memory import InMemoryMemoryService
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types

import server
from autoyou_agents.litellm_ollama_adapter import normalize_ollama_stream_response
from shared import secure_storage
from shared.adk_memory import ADK_MEMORY_PERSISTENT_ENV, AI_AGENT_MEMORY_SERVICE_URI_ENV
from shared.session_execution import SessionExecutionManager, SessionTurnCancelledError


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch, tmp_path):
    secure_storage.disable_secure_storage()
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(secure_storage, "keyring_available", lambda: False)
    yield
    secure_storage.disable_secure_storage()


def enable_maximus(root):
    secure_storage.enable_secure_storage(
        app_name="AutoYou-test", root=root, password="synthetic-maximus-password",
    )


@pytest.mark.parametrize("mode", ["normal", "secure", "secure_professional", "secure_professional_maximus"])
@pytest.mark.parametrize("record", [True, False])
@pytest.mark.parametrize("compiled", [False, True])
def test_worker_memory_policy_matches_security_and_recording(mode, record, compiled, monkeypatch, tmp_path):
    monkeypatch.setattr(server.STATE, "config", {
        "security": {"mode": mode}, "ai_agent": {"record_messages_in_database": record},
    })
    monkeypatch.setattr(server, "is_compiled", lambda: compiled)
    monkeypatch.setattr(server, "get_config_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(server, "get_user_data_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(server, "_load_or_create_ai_agent_internal_api_token", lambda: "synthetic-internal-token")
    monkeypatch.setattr(server, "_ai_agent_lan_access_enabled", lambda: False)
    if mode == "secure_professional_maximus":
        enable_maximus(tmp_path)
    env = server._prepare_ai_agent_process_env({
        "AUTOYOU_TEST_ROOT": str(tmp_path),
        AI_AGENT_MEMORY_SERVICE_URI_ENV: "sqlite:///synthetic-inherited-unused.db",
    })
    ephemeral = not record or mode == "secure_professional_maximus"
    assert (env[AI_AGENT_MEMORY_SERVICE_URI_ENV] == "memory://") is ephemeral
    if not ephemeral:
        assert env[AI_AGENT_MEMORY_SERVICE_URI_ENV] == "sqlite:///" + (tmp_path / ".adk/memory.db").as_posix()
    if mode == "secure_professional_maximus":
        assert env[server.AI_AGENT_ARTIFACT_SERVICE_URI_ENV].startswith("autoyou-secure-file://")
    assert env[ADK_MEMORY_PERSISTENT_ENV] == ("1" if record else "0")


def test_explicit_native_memory_opt_out_survives_worker_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(server.STATE, "config", {"ai_agent": {"record_messages_in_database": True}})
    monkeypatch.setattr(server, "is_compiled", lambda: False)
    monkeypatch.setattr(server, "get_config_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(server, "_load_or_create_ai_agent_internal_api_token", lambda: "synthetic-internal-token")
    monkeypatch.setattr(server, "_ai_agent_lan_access_enabled", lambda: False)
    env = server._prepare_ai_agent_process_env({ADK_MEMORY_PERSISTENT_ENV: "0"})
    assert env[ADK_MEMORY_PERSISTENT_ENV] == "0"
    assert env[AI_AGENT_MEMORY_SERVICE_URI_ENV] == "memory://"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellation", ["native_abort", "turn_cancel", "runtime_shutdown"])
async def test_mid_turn_cancellation_keeps_maximus_history_encrypted_and_memory_usable(cancellation, tmp_path):
    enable_maximus(tmp_path)
    started = asyncio.Event()
    closed = asyncio.Event()

    class ProviderStream:
        sent_partial = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.sent_partial:
                self.sent_partial = True
                return {"choices": [{"delta": {"content": "Synthetic partial reply"}}]}
            started.set()
            await asyncio.Future()

        async def aclose(self):
            closed.set()

    class SyntheticLlm(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            async for _chunk in normalize_ollama_stream_response(ProviderStream()):
                yield LlmResponse(
                    partial=True,
                    content=types.Content(role="model", parts=[types.Part(text="Synthetic partial reply")]),
                )

    path = tmp_path / "synthetic-sessions.db"
    service = DatabaseSessionService(db_url="sqlite+aiosqlite:///" + path.as_posix())
    await service.create_session(
        app_name="synthetic_app", user_id="synthetic_user", session_id="synthetic_session",
        state={"synthetic_existing_state": "preserved"},
    )
    memory = create_memory_service_from_options(base_dir=tmp_path, memory_service_uri="memory://")
    assert isinstance(memory, InMemoryMemoryService)
    runner = Runner(
        app_name="synthetic_app", session_service=service, memory_service=memory,
        agent=LlmAgent(name="synthetic_agent", model=SyntheticLlm(model="synthetic_model")),
    )
    manager = SessionExecutionManager(default_turn_timeout_seconds=10)
    identity = manager.bind_transport_owner("local", "synthetic-device", raw_session_id="synthetic-thread")
    abort_signal = asyncio.Event()

    async def collect():
        return [event async for event in runner.run_async(
            user_id="synthetic_user", session_id="synthetic_session",
            new_message=types.Content(role="user", parts=[types.Part(text="Synthetic orchard cancellation")]),
            abort_signal=abort_signal,
        )]

    turn = asyncio.create_task(manager.submit_turn(identity, collect))
    try:
        await asyncio.wait_for(started.wait(), 5)
        if cancellation == "native_abort":
            abort_signal.set()
            events = await asyncio.wait_for(turn, 5)
            assert any(event.error_code == "INVOCATION_ABORTED" for event in events)
        elif cancellation == "turn_cancel":
            result = await manager.cancel_turn(identity)
            assert result["active_stopped"] is True
            with pytest.raises(SessionTurnCancelledError):
                await asyncio.wait_for(turn, 5)
        else:
            await asyncio.wait_for(manager.shutdown(), 5)
            with pytest.raises(asyncio.CancelledError):
                await turn
        await asyncio.wait_for(closed.wait(), 2)
        stored = await service.get_session(app_name="synthetic_app", user_id="synthetic_user", session_id="synthetic_session")
        assert stored.state["synthetic_existing_state"] == "preserved"
        assert any(event.author == "user" for event in stored.events)
        if cancellation == "native_abort":
            assert any(event.error_code == "INVOCATION_ABORTED" for event in stored.events)
        assert not any(
            event.is_final_response() and event.error_code is None
            and "Synthetic partial reply" in event.model_dump_json()
            for event in stored.events
        )
        await memory.add_session_to_memory(stored)
        found = await memory.search_memory(app_name="synthetic_app", user_id="synthetic_user", query="orchard")
        assert found.memories
        assert (await memory.search_memory(app_name="synthetic_app", user_id="another_synthetic_user", query="orchard")).memories == []
    finally:
        await manager.shutdown()
        await asyncio.gather(turn, return_exceptions=True)
        await runner.close()
    assert path.read_bytes().startswith(secure_storage.SQLITE_HEADER)
    assert b"orchard" not in path.read_bytes()
    assert not (tmp_path / ".adk/memory.db").exists()
    assert all(not Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))

    # Reattach the existing boundary as a new worker would, without enrolling a key.
    secure_storage.disable_secure_storage()
    secure_storage.enable_secure_storage(
        app_name="AutoYou-test", root=tmp_path, password="synthetic-maximus-password",
        allow_key_creation=False,
    )
    reopened = DatabaseSessionService(db_url="sqlite+aiosqlite:///" + path.as_posix())
    try:
        restored = await reopened.get_session(app_name="synthetic_app", user_id="synthetic_user", session_id="synthetic_session")
        assert restored.state["synthetic_existing_state"] == "preserved"
        assert any(event.author == "user" for event in restored.events)
    finally:
        await reopened.close()
