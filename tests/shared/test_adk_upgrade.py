"""Real SDK contracts for ADK 2.11 memory and cancellation using synthetic data."""

import asyncio
from pathlib import Path

import pytest
from google.adk.agents import LlmAgent
from google.adk.cli.utils.service_factory import create_memory_service_from_options
from google.adk.events import Event
from google.adk.memory import SqliteMemoryService
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, Session
from google.genai import types

from autoyou_agents.litellm_ollama_adapter import normalize_ollama_stream_response
from shared.adk_memory import (
    ADK_MEMORY_PERSISTENT_ENV,
    AI_AGENT_MEMORY_SERVICE_URI_ENV,
    resolve_adk_memory_uri,
)


def test_native_memory_uri_isolated_from_inherited_live_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    uri = resolve_adk_memory_uri(
        {AI_AGENT_MEMORY_SERVICE_URI_ENV: "sqlite:///production-memory.db"},
        Path("production-default.db"),
    )
    assert uri == "sqlite:///" + (tmp_path / ".adk" / "memory.db").as_posix()
    assert resolve_adk_memory_uri({}, tmp_path / "unused.db", allow_persistence=False) == "memory://"
    assert resolve_adk_memory_uri({ADK_MEMORY_PERSISTENT_ENV: "0"}, tmp_path / "unused.db") == "memory://"


@pytest.mark.asyncio
async def test_native_memory_factory_accepts_the_isolated_uri_with_special_path_chars(monkeypatch, tmp_path):
    test_root = tmp_path / "synthetic memory #1"
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(test_root))
    uri = resolve_adk_memory_uri({}, Path("production-unused.db"))
    memory = create_memory_service_from_options(base_dir=tmp_path, memory_service_uri=uri)
    try:
        assert isinstance(memory, SqliteMemoryService)
        await memory.add_session_to_memory(Session(
            app_name="synthetic_app", user_id="synthetic_user", id="synthetic_session",
            events=[Event(id="synthetic_event", author="user", content=types.Content(
                role="user", parts=[types.Part(text="Synthetic factory path orchard")],
            ))],
        ))
        assert (test_root / ".adk" / "memory.db").is_file()
    finally:
        await memory.close()


@pytest.mark.asyncio
async def test_native_sqlite_memory_persists_and_keeps_user_and_app_scopes(tmp_path):
    path = tmp_path / "synthetic-memory.db"
    session = Session(
        app_name="synthetic_app", user_id="synthetic_user", id="synthetic_session",
        events=[Event(
            id="synthetic_event", author="user",
            content=types.Content(role="user", parts=[types.Part(text="Synthetic project codename is orchard.")]),
        )],
    )
    first = SqliteMemoryService(db_path=path)
    try:
        await first.add_session_to_memory(session)
        await first.add_session_to_memory(session)
    finally:
        await first.close()
    second = SqliteMemoryService(db_path=path)
    try:
        found = await second.search_memory(app_name="synthetic_app", user_id="synthetic_user", query="orchard")
        assert len(found.memories) == 1
        assert "orchard" in found.memories[0].content.parts[0].text
        other_user = await second.search_memory(app_name="synthetic_app", user_id="another_synthetic_user", query="orchard")
        other_app = await second.search_memory(app_name="another_synthetic_app", user_id="synthetic_user", query="orchard")
        assert other_user.memories == []
        assert other_app.memories == []
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_native_adk_abort_closes_ollama_stream_and_seals_history():
    started = asyncio.Event()
    closed = asyncio.Event()

    class ProviderStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            started.set()
            await asyncio.Future()

        async def aclose(self):
            closed.set()

    class SyntheticLlm(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            async for _chunk in normalize_ollama_stream_response(ProviderStream()):
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Synthetic response")]))

    service = InMemorySessionService()
    await service.create_session(app_name="synthetic_app", user_id="synthetic_user", session_id="synthetic_session")
    runner = Runner(
        app_name="synthetic_app", session_service=service,
        agent=LlmAgent(name="synthetic_agent", model=SyntheticLlm(model="synthetic_model")),
    )
    abort_signal = asyncio.Event()

    async def collect():
        return [event async for event in runner.run_async(
            user_id="synthetic_user", session_id="synthetic_session",
            new_message=types.Content(role="user", parts=[types.Part(text="Synthetic cancellation test")]),
            abort_signal=abort_signal,
        )]

    task = asyncio.create_task(collect())
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        abort_signal.set()
        events = await asyncio.wait_for(task, timeout=3)
        await asyncio.wait_for(closed.wait(), timeout=1)
        assert any(event.error_code == "INVOCATION_ABORTED" for event in events)
        stored = await service.get_session(app_name="synthetic_app", user_id="synthetic_user", session_id="synthetic_session")
        assert any(event.error_code == "INVOCATION_ABORTED" for event in stored.events)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await runner.close()


@pytest.mark.asyncio
async def test_early_ollama_iterator_close_releases_provider_stream():
    class ProviderStream:
        closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            return {"choices": []}

        async def aclose(self):
            self.closed = True

    provider = ProviderStream()
    stream = normalize_ollama_stream_response(provider)
    await anext(stream)
    await stream.aclose()
    assert provider.closed is True
