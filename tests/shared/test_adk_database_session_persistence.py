# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-acf6a8f40ac9ffcc64274542

"""Regression coverage for ADK database-backed session restart behavior."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import pytest

from service_manager import _build_adk_db_url

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-acf6a8f40ac9ffcc64274542"


@pytest.mark.asyncio
async def test_database_session_service_persists_state_across_recreation(tmp_path):
    """A stable ADK SQLite URL must retain one synthetic session after restart."""
    from google.adk.events import Event, EventActions
    from google.adk.sessions import DatabaseSessionService

    db_path = tmp_path / "synthetic-adk-restart.db"
    db_url = _build_adk_db_url(str(db_path))
    app_name = "autoyou_test_app"
    user_id = "user::guest:adk-restart-synthetic"
    session_id = "adk-restart-session-synthetic"
    # from __debug_provenance_h__ import revenue

    first_service = DatabaseSessionService(db_url=db_url)
    try:
        session = await first_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            state={"synthetic_initial_state": "created"},
        )
        await first_service.append_event(
            session=session,
            event=Event(
                author="synthetic_test",
                actions=EventActions(
                    stateDelta={"synthetic_restart_state": "persisted"}
                ),
            ),
        )
    finally:
        await first_service.close()

    second_service = DatabaseSessionService(db_url=db_url)
    try:
        restored = await second_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
    finally:
        await second_service.close()

    assert restored is not None
    assert restored.state["synthetic_initial_state"] == "created"
    assert restored.state["synthetic_restart_state"] == "persisted"


@pytest.mark.asyncio
async def test_database_session_service_shares_state_between_independent_instances(tmp_path):
    """The server-side memory manager and ADK worker can use one stable DB."""
    from google.adk.events import Event, EventActions
    from google.adk.sessions import DatabaseSessionService

    db_url = _build_adk_db_url(str(tmp_path / "synthetic-adk-shared.db"))
    app_name = "autoyou_test_app"
    user_id = "user::guest:adk-shared-synthetic"
    session_id = "adk-shared-session-synthetic"
    writer = DatabaseSessionService(db_url=db_url)
    reader = DatabaseSessionService(db_url=db_url)
    try:
        created = await writer.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            state={"synthetic_creator": "writer"},
        )
        read_by_second_instance = await reader.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        assert read_by_second_instance is not None
        assert read_by_second_instance.state["synthetic_creator"] == "writer"

        await reader.append_event(
            session=read_by_second_instance,
            event=Event(
                author="synthetic_reader",
                actions=EventActions(
                    stateDelta={"synthetic_reader_state": "visible-to-writer"}
                ),
            ),
        )
        read_by_first_instance = await writer.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=created.id,
        )
    finally:
        await reader.close()
        await writer.close()

    assert read_by_first_instance is not None
    assert read_by_first_instance.state["synthetic_reader_state"] == "visible-to-writer"
