# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-36b8ce6b01a3150ecb010e17


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import os
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-36b8ce6b01a3150ecb010e17"


ensure_repo_on_path()

import server as server_module
from session_utils import MemoryIntegratedSessionManager


class _SyntheticAdkSessionService:
    def __init__(self):
        self.deleted = []
        # from __debug_provenance_n__ import license

    async def delete_session(self, **kwargs):
        self.deleted.append(dict(kwargs))


class _SyntheticRebuildAdkSessionService:
    def __init__(self, events, *, fail_append=False):
        self.sessions = {
            "synthetic-adk-session": SimpleNamespace(
                id="synthetic-adk-session",
                state={"synthetic": "state"},
                events=list(events),
            )
        }
        self.fail_append = fail_append
        self._append_count = 0

    async def get_session(self, **kwargs):
        return self.sessions.get(kwargs["session_id"])

    async def delete_session(self, **kwargs):
        self.sessions.pop(kwargs["session_id"], None)

    async def create_session(self, **kwargs):
        self.sessions[kwargs["session_id"]] = SimpleNamespace(
            id=kwargs["session_id"],
            state=dict(kwargs.get("state") or {}),
            events=[],
        )

    async def append_event(self, *, session, event):
        self._append_count += 1
        if self.fail_append and self._append_count == 2:
            raise RuntimeError("synthetic append failure")
        session.events.append(event)


def _synthetic_adk_event(event_id, author, text="", prompt_id=""):
    state_delta = {}
    if prompt_id:
        state_delta["_autoyou_turn_metadata"] = {"client_prompt_id": prompt_id}
    return SimpleNamespace(
        id=event_id,
        author=author,
        actions=SimpleNamespace(state_delta=state_delta),
        content=SimpleNamespace(parts=[SimpleNamespace(text=text)] if text else []),
    )


def test_conversation_threads_resume_and_advance(tmp_path):
    db_path = str(tmp_path / "sessions.db")
    manager = MemoryIntegratedSessionManager(db_path=db_path, adk_session_service=object())

    assert manager.get_current_conversation_thread("telegram:5550001001") == 1
    assert (
        manager.get_current_conversation_session_id("telegram:5550001001")
        == "session::telegram:5550001001"
    )

    thread_id, canonical_session_id = manager.advance_conversation_thread("telegram:5550001001")
    assert thread_id == 2
    assert canonical_session_id == "session::telegram:5550001001::2"
    assert manager.get_current_conversation_thread("telegram:5550001001") == 2

    reopened = MemoryIntegratedSessionManager(db_path=db_path, adk_session_service=object())
    assert reopened.get_current_conversation_thread("telegram:5550001001") == 2
    assert (
        reopened.get_current_conversation_session_id("telegram:5550001001")
        == "session::telegram:5550001001::2"
    )


def test_new_thread_uses_fresh_external_session_mapping(tmp_path):
    db_path = str(tmp_path / "sessions.db")
    manager = MemoryIntegratedSessionManager(db_path=db_path, adk_session_service=object())
    owner_key = "cloud:eq347mzfmatcf8u"
    user_id = f"user::{owner_key}"

    initial_session_id = manager.get_current_conversation_session_id(owner_key)
    manager.set_session_mapping(initial_session_id, "ai-session-one", user_id)

    thread_id, next_session_id = manager.advance_conversation_thread(owner_key)

    assert thread_id == 2
    assert next_session_id == "session::cloud:eq347mzfmatcf8u::2"
    assert manager.get_mapped_session_id(initial_session_id, user_id) == "ai-session-one"
    assert manager.get_mapped_session_id(next_session_id, user_id) is None


def test_session_context_usage_snapshot_persists_across_restart(tmp_path):
    db_path = str(tmp_path / "sessions.db")
    manager = MemoryIntegratedSessionManager(db_path=db_path, adk_session_service=object())
    snapshot = {
        "available": True,
        "summary_text": "Ctx 6.1k/8k",
        "alert_level": "warning",
        "prompt_tokens": 6100,
        "context_window": 8192,
        "conversation_session_id": "session::telegram:5550001001::4",
    }

    manager.upsert_session_context_usage_snapshot(
        session_id="ai-session-ctx",
        user_id="user::telegram:5550001001",
        external_session_id="session::telegram:5550001001::4",
        snapshot=snapshot,
    )

    reopened = MemoryIntegratedSessionManager(db_path=db_path, adk_session_service=object())
    persisted = reopened.get_session_context_usage_snapshot(
        "ai-session-ctx",
        "user::telegram:5550001001",
    )

    assert persisted == snapshot


@pytest.mark.asyncio
async def test_delete_conversation_history_removes_autoyou_records_and_mapping_aliases(tmp_path):
    db_path = str(tmp_path / "sessions.db")
    adk_service = _SyntheticAdkSessionService()
    manager = MemoryIntegratedSessionManager(db_path=db_path, adk_session_service=adk_service)
    user_id = "user::local:synthetic-delete-device"
    session_id = "session::local:synthetic-delete-device"
    external_id = "synthetic-webrtc-session"
    alias_id = "synthetic-reconnect-session"

    manager.set_session_mapping(external_id, "synthetic-ai-session", user_id)
    manager.set_session_mapping(alias_id, "synthetic-ai-session", user_id)
    manager.upsert_session_context_usage_snapshot(
        session_id="synthetic-ai-session",
        user_id=user_id,
        external_session_id=session_id,
        snapshot={"available": True, "conversation_session_id": session_id},
    )

    result = await manager.delete_conversation_history(
        user_id,
        session_id,
        external_session_id=external_id,
    )

    assert result["deleted"] is True
    assert "adk_session" in result["components"]
    assert manager.get_mapped_session_id(external_id, user_id) is None
    assert manager.get_mapped_session_id(alias_id, user_id) is None
    assert adk_service.deleted[0]["session_id"] == session_id
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM session_context_usage WHERE session_id = ?",
            ("synthetic-ai-session",),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM session_mappings WHERE session_id = ?",
            ("synthetic-ai-session",),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_server_delete_does_not_claim_success_when_history_store_fails(monkeypatch):
    class _ExecutionManager:
        async def clear_session(self, _session_id):
            return {"cleared": True}

    class _HistoryManager:
        async def delete_conversation_history(self, *_args, **_kwargs):
            return {
                "deleted": False,
                "components": [],
                "reason": "synthetic_history_store_failure",
            }

    monkeypatch.setattr(server_module, "get_session_execution_manager", lambda: _ExecutionManager())
    monkeypatch.setattr(server_module, "_get_conversation_session_manager", lambda: _HistoryManager())

    result = await server_module._delete_server_conversation_history(
        SimpleNamespace(
            canonical_session_id="session::local:synthetic-delete-failure",
            canonical_user_id="user::local:synthetic-delete-failure",
            raw_session_id="synthetic-webrtc-delete-failure",
        )
    )

    assert result["deleted"] is False
    assert result["active_execution_cleared"] is True
    assert result["reason"] == "synthetic_history_store_failure"


@pytest.mark.asyncio
async def test_replace_conversation_turn_keeps_adk_session_id_and_removes_tail(tmp_path):
    events = [
        _synthetic_adk_event("synthetic-system", "system"),
        _synthetic_adk_event("synthetic-user-one", "user", "first", "synthetic-prompt-one"),
        _synthetic_adk_event("synthetic-assistant-one", "autoyou", "reply one"),
        _synthetic_adk_event("synthetic-user-two", "user", "second", "synthetic-prompt-two"),
        _synthetic_adk_event("synthetic-assistant-two", "autoyou", "reply two"),
    ]
    service = _SyntheticRebuildAdkSessionService(events)
    manager = MemoryIntegratedSessionManager(
        db_path=str(tmp_path / "sessions.db"),
        adk_session_service=service,
    )

    result = await manager.replace_conversation_turn(
        "synthetic-user",
        "synthetic-adk-session",
        edited_message_id="synthetic-prompt-two",
        edited_message_text="second edited",
    )

    assert result["replaced"] is True
    rebuilt = service.sessions["synthetic-adk-session"]
    assert rebuilt.id == "synthetic-adk-session"
    assert [event.id for event in rebuilt.events] == [
        "synthetic-system",
        "synthetic-user-one",
        "synthetic-assistant-one",
    ]
    assert rebuilt.state == {"synthetic": "state"}


@pytest.mark.asyncio
async def test_replace_conversation_turn_restores_original_adk_events_on_rebuild_failure(tmp_path):
    events = [
        _synthetic_adk_event("synthetic-system", "system"),
        _synthetic_adk_event("synthetic-user-one", "user", "first", "synthetic-prompt-one"),
        _synthetic_adk_event("synthetic-assistant-one", "autoyou", "reply one"),
        _synthetic_adk_event("synthetic-user-two", "user", "second", "synthetic-prompt-two"),
        _synthetic_adk_event("synthetic-assistant-two", "autoyou", "reply two"),
    ]
    service = _SyntheticRebuildAdkSessionService(events, fail_append=True)
    manager = MemoryIntegratedSessionManager(
        db_path=str(tmp_path / "sessions.db"),
        adk_session_service=service,
    )

    result = await manager.replace_conversation_turn(
        "synthetic-user",
        "synthetic-adk-session",
        edited_message_id="synthetic-prompt-two",
    )

    assert result == {"replaced": False, "reason": "adk_session_rebuild_failed"}
    assert [event.id for event in service.sessions["synthetic-adk-session"].events] == [
        "synthetic-system",
        "synthetic-user-one",
        "synthetic-assistant-one",
        "synthetic-user-two",
        "synthetic-assistant-two",
    ]
