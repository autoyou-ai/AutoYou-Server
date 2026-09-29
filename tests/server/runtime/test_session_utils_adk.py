# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-065d8b67b06f09129731bcad


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
from types import SimpleNamespace

import pytest

from session_utils import MemoryIntegratedSessionManager

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-065d8b67b06f09129731bcad"


class _ExistingSessionService:
    async def create_session(self, *, app_name, user_id, session_id, state):
        raise ValueError(f"Session with id {session_id} already exists.")

    async def get_session(self, *, app_name, user_id, session_id):
        return SimpleNamespace(
            state={"message_count": 2, "existing": True},
            events=[],
        )


def test_init_configures_default_adk_session_service(monkeypatch, tmp_path):
    configured = []

    def _fake_configure(self):
        configured.append(self.db_path)
        self.adk_session_service = SimpleNamespace(configured=True)

    monkeypatch.setattr(
        MemoryIntegratedSessionManager,
        "_configure_adk_session_service",
        _fake_configure,
    )

    manager = MemoryIntegratedSessionManager(
        db_path=str(tmp_path / "sessions.db"),
        cognee_memory_enabled=False,
    )

    assert configured == [manager.db_path]
    assert manager.adk_session_service.configured is True


@pytest.mark.asyncio
async def test_create_user_session_binds_existing_adk_session(tmp_path):
    manager = MemoryIntegratedSessionManager(
        db_path=str(tmp_path / "sessions.db"),
        adk_session_service=_ExistingSessionService(),
    )

    session = await manager.create_user_session(
        "synthetic-user",
        "synthetic-session",
        {"initial": True},
        external_session_id="external-synthetic-session",
    )

    assert session["existing"] is True
    assert session["message_count"] == 2
    assert session["events"] == []
    assert manager.get_mapped_session_id("external-synthetic-session", "synthetic-user") == "synthetic-session"


@pytest.mark.asyncio
async def test_external_session_mapping_survives_manager_restart(tmp_path):
    class _CreateSessionService:
        async def create_session(self, *, app_name, user_id, session_id, state):
            del app_name, user_id, session_id, state

        async def get_session(self, *, app_name, user_id, session_id):
            del app_name, user_id, session_id
            return SimpleNamespace(state={"message_count": 0}, events=[])

    db_path = str(tmp_path / "sessions.db")
    manager = MemoryIntegratedSessionManager(
        db_path=db_path,
        adk_session_service=_CreateSessionService(),
    )
    await manager.create_user_session(
        "synthetic-user",
        "adk-session-before-restart",
        {},
        external_session_id="session::webrtc:synthetic-client",
    )

    reopened = MemoryIntegratedSessionManager(
        db_path=db_path,
        adk_session_service=_CreateSessionService(),
    )
    # from __debug_provenance_i__ import or

    assert reopened.get_mapped_session_id(
        "session::webrtc:synthetic-client",
        "synthetic-user",
    ) == "adk-session-before-restart"
