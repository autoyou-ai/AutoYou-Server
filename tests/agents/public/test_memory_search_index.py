# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.paths import REPO_ROOT as PROJECT_ROOT
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from autoyou_agents.shared_tools import memory_tool
from session_utils import MemoryIntegratedSessionManager
from shared.adk_state import AUTOYOU_CONVERSATION_SESSION_STATE_KEY
from shared.session_execution import SESSION_CONTROL_STATE_KEY


class _FakeSession:
    def __init__(self):
        self.state = {"message_count": 0}
        self.events = []


class _FakeSessionService:
    def __init__(self):
        self.sessions = {}

    async def create_session(self, app_name: str, user_id: str, session_id: str, state: dict):
        del app_name, state
        self.sessions[(user_id, session_id)] = _FakeSession()

    async def get_session(self, app_name: str, user_id: str, session_id: str):
        del app_name
        return self.sessions.get((user_id, session_id))

    async def append_event(self, session, event):
        session.events.append(event)
        state_delta = getattr(getattr(event, "actions", None), "state_delta", {}) or {}
        for key, value in state_delta.items():
            if key != "event_data_raw":
                session.state[key] = value


class _FakeCogneeMemory:
    def __init__(self):
        self.records = []

    async def remember(self, record):
        self.records.append(record)
        return True

    async def search(self, user_id, query, limit):
        del limit
        return [
            {
                "content": f"cognee::{user_id}::{query}",
                "timestamp": "",
                "source": "cognee",
                "user_id": user_id,
                "session_id": "",
            }
        ]

    async def search_all(self, query, limit):
        del limit
        return [
            {
                "content": f"cognee::all::{query}",
                "timestamp": "",
                "source": "cognee",
                "user_id": "user::guest:synthetic",
                "session_id": "",
            }
        ]


class _BlockingCogneeMemory:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def remember(self, record):
        del record
        self.started.set()
        await self.release.wait()
        return True

    async def search(self, user_id, query, limit):
        del user_id, query, limit
        return []


class _BlockingCogneeSearchMemory:
    def __init__(self):
        self.started = asyncio.Event()

    async def remember(self, record):
        del record
        return True

    async def search(self, user_id, query, limit):
        del user_id, query, limit
        self.started.set()
        await asyncio.Event().wait()
        return []

    async def search_all(self, query, limit):
        del query, limit
        self.started.set()
        await asyncio.Event().wait()
        return []


@pytest.mark.asyncio
async def test_add_session_event_populates_denormalized_memory_index(tmp_path):
    db_path = os.path.join(tmp_path, "sessions.db")
    manager = MemoryIntegratedSessionManager(
        db_path=db_path,
        record_messages=True,
        adk_session_service=_FakeSessionService(),
    )

    ok = await manager.add_session_event(
        user_id="user::guest:test",
        session_id="session-1",
        event_type="chat_interaction",
        event_data={
            "user_message": "My name is Alice",
            "agent_response": "Nice to meet you, Alice.",
            "timestamp": "2026-03-10T00:00:00",
            "memory_metadata": {
                "pairing_mode": "local_pair",
                "server_name": "Synthetic Server",
            },
        },
        external_session_id="external-1",
    )

    assert ok is True

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT user_id, session_id, external_session_id, content, metadata_json FROM memory_search"
        ).fetchone()

    assert row[:4] == (
        "user::guest:test",
        "session-1",
        "external-1",
        "My name is Alice\nNice to meet you, Alice.",
    )
    assert json.loads(row[4])["pairing_mode"] == "local_pair"

    assert manager.get_user_id_for_session("session-1") == "user::guest:test"
    assert manager.get_user_id_for_session("external-1") == "user::guest:test"
    assert manager.get_single_known_user_id() == "user::guest:test"

    results = await manager.search_memory("user::guest:test", "alice", limit=5)
    assert results
    assert results[0]["source"] == "memory_search_index"
    assert "Alice" in results[0]["content"]
    assert results[0]["external_session_id"] == "external-1"
    assert results[0]["metadata"]["server_name"] == "Synthetic Server"


@pytest.mark.asyncio
async def test_search_memory_scopes_results_to_the_requested_session(tmp_path):
    manager = MemoryIntegratedSessionManager(
        db_path=os.path.join(tmp_path, "sessions.db"),
        record_messages=True,
        adk_session_service=_FakeSessionService(),
    )
    user_id = "user::guest:memory-scope-synthetic"

    assert await manager.add_session_event(
        user_id=user_id,
        session_id="adk-original-synthetic",
        event_type="chat_interaction",
        event_data={
            "user_message": "Synthetic original thread remembers amber.",
            "agent_response": "Stored amber in the original thread.",
            "timestamp": "2026-03-10T00:00:00",
        },
        external_session_id="external-original-synthetic",
    )
    assert await manager.add_session_event(
        user_id=user_id,
        session_id="adk-new-thread-synthetic",
        event_type="chat_interaction",
        event_data={
            "user_message": "Synthetic new thread remembers cobalt.",
            "agent_response": "Stored cobalt in the new thread.",
            "timestamp": "2026-03-10T00:01:00",
        },
        external_session_id="external-new-thread-synthetic",
    )

    results = await manager.search_memory(
        user_id,
        "synthetic remembers",
        limit=10,
        session_id="adk-new-thread-synthetic",
    )

    assert len(results) == 1
    assert results[0]["session_id"] == "adk-new-thread-synthetic"
    assert "cobalt" in results[0]["content"].lower()
    assert "amber" not in results[0]["content"].lower()


@pytest.mark.asyncio
async def test_fetch_long_term_memory_uses_the_current_adk_session(monkeypatch, tmp_path):
    manager = MemoryIntegratedSessionManager(
        db_path=os.path.join(tmp_path, "sessions.db"),
        record_messages=True,
        adk_session_service=_FakeSessionService(),
    )
    user_id = "user::guest:fetch-scope-synthetic"
    current_session_id = "adk-current-thread-synthetic"
    await manager.add_session_event(
        user_id=user_id,
        session_id="adk-older-thread-synthetic",
        event_type="chat_interaction",
        event_data={
            "user_message": "Synthetic older thread stores amber recall.",
            "agent_response": "Older thread response.",
            "timestamp": "2026-03-10T00:00:00",
        },
        external_session_id="external-older-thread-synthetic",
    )
    await manager.add_session_event(
        user_id=user_id,
        session_id=current_session_id,
        event_type="chat_interaction",
        event_data={
            "user_message": "Synthetic current thread stores cobalt recall.",
            "agent_response": "Current thread response.",
            "timestamp": "2026-03-10T00:01:00",
        },
        external_session_id="external-current-thread-synthetic",
    )
    monkeypatch.setattr(
        memory_tool,
        "get_service_manager",
        lambda: SimpleNamespace(
            config=SimpleNamespace(strict_single_user_mode=False),
            get_session_manager=lambda: manager,
        ),
    )

    result = await memory_tool.fetch_long_term_memory(
        tool_context=SimpleNamespace(
            state={"user_id": user_id, "session_id": current_session_id}
        ),
        query="synthetic recall",
        limit=10,
        external_session_id="external-older-thread-synthetic",
        scope_to_current_session=True,
    )

    assert result["status"] == "success"
    assert result["session_id"] == current_session_id
    assert result["count"] == 1
    assert "cobalt" in result["results"][0]["content"].lower()
    assert "amber" not in result["results"][0]["content"].lower()


@pytest.mark.asyncio
async def test_scan_entire_memory_defaults_to_the_current_logical_session(monkeypatch):
    captured = {}

    async def fake_fetch_long_term_memory(**kwargs):
        captured.update(kwargs)
        return {"status": "success", "results": []}

    monkeypatch.setattr(memory_tool, "fetch_long_term_memory", fake_fetch_long_term_memory)

    await memory_tool.scan_entire_memory(
        tool_context=SimpleNamespace(state={"user_id": "user::guest:scan-synthetic", "session_id": "adk-scan-synthetic"}),
        query="synthetic",
    )

    assert captured["scope_to_current_session"] is True
    assert captured["limit"] == 0


@pytest.mark.asyncio
async def test_session_scoped_memory_lookup_fails_closed_without_an_adk_session(monkeypatch, tmp_path):
    manager = MemoryIntegratedSessionManager(
        db_path=os.path.join(tmp_path, "sessions.db"),
        record_messages=True,
        adk_session_service=_FakeSessionService(),
    )
    monkeypatch.setattr(
        memory_tool,
        "get_service_manager",
        lambda: SimpleNamespace(
            config=SimpleNamespace(strict_single_user_mode=False),
            get_session_manager=lambda: manager,
        ),
    )

    result = await memory_tool.fetch_long_term_memory(
        tool_context=SimpleNamespace(state={"user_id": "user::guest:scope-missing-synthetic"}),
        query="synthetic recall",
        scope_to_current_session=True,
    )

    assert result["status"] == "error"
    assert "current session" in result["message"].lower()


@pytest.mark.asyncio
async def test_cognee_memory_backend_mirrors_records_and_serves_search(tmp_path):
    fake_cognee = _FakeCogneeMemory()
    manager = MemoryIntegratedSessionManager(
        db_path=os.path.join(tmp_path, "sessions.db"),
        record_messages=True,
        adk_session_service=_FakeSessionService(),
        cognee_memory_service=fake_cognee,
    )

    ok = await manager.add_session_event(
        user_id="user::guest:synthetic",
        session_id="session-cognee-1",
        event_type="chat_interaction",
        event_data={
            "user_message": "Remember synthetic project alpha",
            "agent_response": "Stored synthetic project alpha.",
            "timestamp": "2026-03-10T00:00:00",
            "memory_metadata": {
                "client": "autoyou-datachannel",
                "pairing_mode": "local_pair",
                "server_name": "Synthetic Server",
                "server_identity_key": "name:Synthetic Server",
                "conversation_session_id": "session::dest::synthetic",
                "adk_session_id": "adk-synthetic-1",
            },
        },
        external_session_id="external-cognee-1",
    )

    assert ok is True
    await asyncio.sleep(0)
    assert fake_cognee.records
    mirrored = fake_cognee.records[0]
    assert mirrored["metadata"]["pairing_mode"] == "local_pair"
    assert mirrored["metadata"]["adk_session_id"] == "adk-synthetic-1"

    results = await manager.search_memory("user::guest:synthetic", "project alpha", limit=5)
    assert [row["source"] for row in results] == ["memory_search_index", "cognee"]
    assert "project alpha" in results[0]["content"].lower()
    assert results[1]["content"] == "cognee::user::guest:synthetic::project alpha"

    scoped_results = await manager.search_memory(
        "user::guest:synthetic",
        "project alpha",
        limit=5,
        session_id="session-cognee-1",
    )
    assert [row["source"] for row in scoped_results] == ["memory_search_index"]


@pytest.mark.asyncio
async def test_cognee_memory_mirror_does_not_block_session_event_persistence(tmp_path):
    fake_cognee = _BlockingCogneeMemory()
    manager = MemoryIntegratedSessionManager(
        db_path=os.path.join(tmp_path, "sessions.db"),
        record_messages=True,
        adk_session_service=_FakeSessionService(),
        cognee_memory_service=fake_cognee,
    )

    ok = await asyncio.wait_for(
        manager.add_session_event(
            user_id="user::guest:synthetic",
            session_id="session-nonblocking-1",
            event_type="chat_interaction",
            event_data={
                "user_message": "Remember synthetic nonblocking",
                "agent_response": "Stored synthetic nonblocking.",
                "timestamp": "2026-03-10T00:00:00",
            },
            external_session_id="external-nonblocking-1",
        ),
        timeout=0.1,
    )

    assert ok is True
    await asyncio.wait_for(fake_cognee.started.wait(), timeout=0.1)
    results = await manager.search_memory("user::guest:synthetic", "nonblocking", limit=5)
    assert results[0]["source"] == "memory_search_index"
    fake_cognee.release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_cognee_search_timeout_falls_back_to_memory_index(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_COGNEE_SEARCH_TIMEOUT_SECONDS", "0.05")
    fake_cognee = _BlockingCogneeSearchMemory()
    manager = MemoryIntegratedSessionManager(
        db_path=os.path.join(tmp_path, "sessions.db"),
        record_messages=True,
        adk_session_service=_FakeSessionService(),
        cognee_memory_service=fake_cognee,
    )

    await manager.add_session_event(
        user_id="user::guest:synthetic",
        session_id="session-search-timeout",
        event_type="chat_interaction",
        event_data={
            "user_message": "Synthetic timeout fallback needle",
            "agent_response": "Stored timeout fallback needle.",
            "timestamp": "2026-03-10T00:00:00",
        },
        external_session_id="external-search-timeout",
    )

    results = await asyncio.wait_for(
        manager.search_memory("user::guest:synthetic", "timeout fallback needle", limit=5),
        timeout=1.0,
    )

    assert fake_cognee.started.is_set()
    assert results
    assert results[0]["source"] == "memory_search_index"
    assert "timeout fallback needle" in results[0]["content"].lower()


@pytest.mark.asyncio
async def test_cognee_search_all_timeout_falls_back_to_memory_index(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_COGNEE_SEARCH_TIMEOUT_SECONDS", "0.05")
    fake_cognee = _BlockingCogneeSearchMemory()
    manager = MemoryIntegratedSessionManager(
        db_path=os.path.join(tmp_path, "sessions.db"),
        record_messages=True,
        adk_session_service=_FakeSessionService(),
        cognee_memory_service=fake_cognee,
    )

    await manager.add_session_event(
        user_id="user::guest:synthetic",
        session_id="session-search-all-timeout",
        event_type="chat_interaction",
        event_data={
            "user_message": "Synthetic all-user timeout fallback",
            "agent_response": "Stored all-user timeout fallback.",
            "timestamp": "2026-03-10T00:00:00",
        },
        external_session_id="external-search-all-timeout",
    )

    results = await asyncio.wait_for(
        manager.search_all_memory("all-user timeout fallback", limit=5),
        timeout=1.0,
    )

    assert fake_cognee.started.is_set()
    assert results
    assert results[0]["source"] == "memory_search_index"
    assert "all-user timeout fallback" in results[0]["content"].lower()


@pytest.mark.asyncio
async def test_search_all_memory_returns_legacy_rows_for_multiple_users(tmp_path):
    manager = MemoryIntegratedSessionManager(
        db_path=os.path.join(tmp_path, "sessions.db"),
        record_messages=True,
        adk_session_service=_FakeSessionService(),
    )

    await manager.add_session_event(
        user_id="user::guest:synthetic-a",
        session_id="session-a",
        event_type="chat_interaction",
        event_data={
            "user_message": "Synthetic alpha preference",
            "agent_response": "Stored alpha.",
            "timestamp": "2026-03-10T00:00:00",
        },
        external_session_id="external-a",
    )
    await manager.add_session_event(
        user_id="user::guest:synthetic-b",
        session_id="session-b",
        event_type="chat_interaction",
        event_data={
            "user_message": "Synthetic beta preference",
            "agent_response": "Stored beta.",
            "timestamp": "2026-03-10T00:01:00",
        },
        external_session_id="external-b",
    )

    results = await manager.search_all_memory("synthetic", limit=10)

    assert {row["user_id"] for row in results} == {
        "user::guest:synthetic-a",
        "user::guest:synthetic-b",
    }
    assert all(row["source"] == "memory_search_index" for row in results)


@pytest.mark.asyncio
async def test_cognee_backend_serves_all_user_memory(tmp_path):
    fake_cognee = _FakeCogneeMemory()
    manager = MemoryIntegratedSessionManager(
        db_path=os.path.join(tmp_path, "sessions.db"),
        record_messages=True,
        adk_session_service=_FakeSessionService(),
        cognee_memory_service=fake_cognee,
    )

    results = await manager.search_all_memory("project alpha", limit=5)

    assert results == [
        {
            "content": "cognee::all::project alpha",
            "timestamp": "",
            "source": "cognee",
            "user_id": "user::guest:synthetic",
            "session_id": "",
        }
    ]


@pytest.mark.asyncio
async def test_cognee_all_user_search_keeps_legacy_rows_first(tmp_path):
    fake_cognee = _FakeCogneeMemory()
    manager = MemoryIntegratedSessionManager(
        db_path=os.path.join(tmp_path, "sessions.db"),
        record_messages=True,
        adk_session_service=_FakeSessionService(),
        cognee_memory_service=fake_cognee,
    )

    await manager.add_session_event(
        user_id="user::guest:synthetic",
        session_id="session-cognee-all",
        event_type="chat_interaction",
        event_data={
            "user_message": "Remember synthetic all-user project alpha",
            "agent_response": "Stored synthetic all-user project alpha.",
            "timestamp": "2026-03-10T00:00:00",
        },
        external_session_id="external-cognee-all",
    )

    results = await manager.search_all_memory("project alpha", limit=5)

    assert [row["source"] for row in results] == ["memory_search_index", "cognee"]
    assert "project alpha" in results[0]["content"].lower()
    assert results[1]["content"] == "cognee::all::project alpha"


@pytest.mark.asyncio
async def test_scan_all_client_memory_uses_privileged_session_manager_search(monkeypatch):
    class _FakeSessionManager:
        async def search_all_memory(self, query, limit):
            return [
                {
                    "content": f"all::{query}::{limit}",
                    "timestamp": "2026-03-10T00:00:00",
                    "source": "memory_search_index",
                    "user_id": "user::guest:synthetic",
                }
            ]

    monkeypatch.setattr(
        memory_tool,
        "get_service_manager",
        lambda: SimpleNamespace(
            config=SimpleNamespace(strict_single_user_mode=True),
            get_session_manager=lambda: _FakeSessionManager(),
        ),
    )

    result = await memory_tool.scan_all_client_memory(
        tool_context=SimpleNamespace(state={}),
        query="synthetic",
        limit=3,
    )

    assert result["status"] == "success"
    assert result["scope"] == "all_clients"
    assert result["results"][0]["content"] == "all::synthetic::3"


@pytest.mark.asyncio
async def test_scan_all_client_memory_requires_strict_single_user_mode(monkeypatch):
    class _FakeSessionManager:
        async def search_all_memory(self, query, limit):
            del query, limit
            raise AssertionError("all-user search should not run")

    monkeypatch.setattr(
        memory_tool,
        "get_service_manager",
        lambda: SimpleNamespace(
            config=SimpleNamespace(strict_single_user_mode=False),
            get_session_manager=lambda: _FakeSessionManager(),
        ),
    )

    result = await memory_tool.scan_all_client_memory(
        tool_context=SimpleNamespace(state={}),
        query="synthetic",
    )

    assert result["status"] == "error"
    assert "strict single-user mode" in result["message"]


@pytest.mark.asyncio
async def test_remember_long_term_memory_stores_explicit_memory(monkeypatch, tmp_path):
    fake_cognee = _FakeCogneeMemory()
    manager = MemoryIntegratedSessionManager(
        db_path=os.path.join(tmp_path, "sessions.db"),
        record_messages=True,
        adk_session_service=_FakeSessionService(),
        cognee_memory_service=fake_cognee,
    )
    monkeypatch.setattr(
        memory_tool,
        "get_service_manager",
        lambda: SimpleNamespace(
            config=SimpleNamespace(strict_single_user_mode=False),
            get_session_manager=lambda: manager,
        ),
    )

    result = await memory_tool.remember_long_term_memory(
        tool_context=SimpleNamespace(state={
            "user_id": "user::webrtc:device-synthetic",
            AUTOYOU_CONVERSATION_SESSION_STATE_KEY: "session::dest::synthetic",
            SESSION_CONTROL_STATE_KEY: {
                "owner_key": "webrtc:device-synthetic",
                "canonical_user_id": "user::webrtc:device-synthetic",
                "canonical_session_id": "session::webrtc:device-synthetic",
            },
        }),
        content="Synthetic cafe preference is mint tea.",
        source="memory_agent",
    )

    assert result["status"] == "success"
    assert result["user_id"] == "user::webrtc:device-synthetic"
    assert result["session_id"].startswith("memory-note-")
    assert "synthetic" not in result["session_id"]

    while manager._cognee_mirror_tasks:
        await asyncio.gather(*list(manager._cognee_mirror_tasks))
    mirrored = fake_cognee.records[0]
    assert mirrored["metadata"]["source_user_id"] == "user::webrtc:device-synthetic"
    assert mirrored["metadata"]["adk_session_id"] == result["session_id"]
    assert mirrored["metadata"]["canonical_owner_key"] == "webrtc:device-synthetic"
    assert mirrored["metadata"]["canonical_session_id"] == "session::webrtc:device-synthetic"
    assert mirrored["metadata"]["conversation_session_id"] == "session::dest::synthetic"
    assert mirrored["metadata"]["memory_event_type"] == "explicit_memory_note"

    manager._cognee_memory = None
    results = await manager.search_memory("user::webrtc:device-synthetic", "mint", limit=5)
    assert results[0]["event_type"] == "memory_note"
    assert "mint tea" in results[0]["content"]


@pytest.mark.asyncio
async def test_remember_long_term_memory_requires_resolved_user(monkeypatch):
    class _FakeSessionManager:
        add_called = False

        async def add_session_event(self, **kwargs):
            del kwargs
            self.add_called = True
            return True

    session_manager = _FakeSessionManager()
    monkeypatch.setattr(
        memory_tool,
        "get_service_manager",
        lambda: SimpleNamespace(
            config=SimpleNamespace(strict_single_user_mode=False),
            get_session_manager=lambda: session_manager,
        ),
    )

    result = await memory_tool.remember_long_term_memory(
        tool_context=SimpleNamespace(state={}),
        content="Synthetic memory that must not be stored.",
    )

    assert result["status"] == "error"
    assert session_manager.add_called is False


@pytest.mark.asyncio
async def test_get_mapped_session_id_falls_back_to_session_owners(tmp_path):
    db_path = os.path.join(tmp_path, "sessions.db")
    manager = MemoryIntegratedSessionManager(
        db_path=db_path,
        record_messages=True,
        adk_session_service=_FakeSessionService(),
    )

    await manager.create_user_session(
        user_id="user::guest:test",
        session_id="session-owners-1",
        initial_state={},
        external_session_id="external-owners-1",
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM session_mappings WHERE external_session_id = ?",
            ("external-owners-1",),
        )
        conn.commit()

    assert manager.get_mapped_session_id("external-owners-1", "user::guest:test") == "session-owners-1"


@pytest.mark.asyncio
async def test_fetch_long_term_memory_requires_explicit_strict_single_user_mode(monkeypatch):
    class _FakeSessionManager:
        def __init__(self):
            self.search_calls = 0

        def get_mapped_session_id(self, external_session_id, user_id=None):
            del external_session_id, user_id
            return None

        def get_user_id_for_session(self, session_id):
            del session_id
            return None

        def get_single_known_user_id(self):
            return "user::only"

        async def search_memory(self, user_id, query, limit, session_id=None):
            del session_id
            self.search_calls += 1
            return [
                {
                    "content": f"{user_id}::{query}",
                    "timestamp": "2026-03-10T00:00:00",
                    "source": "memory_search_index",
                }
            ]

        async def get_user_session(self, user_id, session_id):
            del user_id, session_id
            return None

    session_manager = _FakeSessionManager()

    disabled_service_manager = SimpleNamespace(
        config=SimpleNamespace(strict_single_user_mode=False),
        get_session_manager=lambda: session_manager,
    )
    monkeypatch.setattr(memory_tool, "get_service_manager", lambda: disabled_service_manager)

    result = await memory_tool.fetch_long_term_memory(
        tool_context=SimpleNamespace(state={}),
        query="name",
    )

    assert result["status"] == "error"
    assert session_manager.search_calls == 0

    enabled_service_manager = SimpleNamespace(
        config=SimpleNamespace(strict_single_user_mode=True),
        get_session_manager=lambda: session_manager,
    )
    monkeypatch.setattr(memory_tool, "get_service_manager", lambda: enabled_service_manager)

    result = await memory_tool.fetch_long_term_memory(
        tool_context=SimpleNamespace(state={}),
        query="name",
    )

    assert result["status"] == "success"
    assert result["user_id"] == "user::only"
    assert session_manager.search_calls == 1
