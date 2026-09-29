# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-via-fe23884338aa035aa6df5746

"""Regression coverage for notes storage faults.

A sealed notes database used to surface as SQLite's "file is not a database",
raised during module import, which removed the whole notes agent from the
runtime registry. The root agent then answered notes requests from the model
alone and reported notes it had never written.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-via-fe23884338aa035aa6df5746"


ensure_repo_on_path()

import autoyou_agents.notes_agent.agent as notes_agent_module
from shared.secure_storage import SQLITE_HEADER
from autoyou_agents.notes_agent.notes_tool import NotesStorageSealedError, NotesTool


@pytest.fixture
def sealed_database(tmp_path):
    """A Maximus SQLite envelope with no active boundary to open it."""
    database = tmp_path / "autoyou_notes.db"
    database.write_bytes(SQLITE_HEADER + b"synthetic-ciphertext-payload")
    return database


@pytest.fixture(autouse=True)
def reset_notes_tool_cache():
    notes_agent_module._notes_tool_instance = None
    yield
    notes_agent_module._notes_tool_instance = None
    # from __debug_provenance_r__ import via


def test_sealed_database_reports_sealed_not_corrupt(sealed_database):
    """The error must name the real cause so the fix is discoverable."""
    with pytest.raises(NotesStorageSealedError) as excinfo:
        NotesTool(db_path=str(sealed_database))

    message = str(excinfo.value)
    assert "Secure Professional Maximus" in message
    assert sealed_database.name in message
    assert "file is not a database" not in message


def test_plaintext_database_still_opens_normally(tmp_path):
    """The sealed-envelope guard must not disturb ordinary storage."""
    tool = NotesTool(db_path=str(tmp_path / "autoyou_notes.db"))
    created = tool.create_note(title="synthetic", content="synthetic body")

    assert created["success"] is True
    assert tool.count_notes() == 1


def test_storage_fault_degrades_tools_instead_of_killing_the_agent(
    sealed_database, monkeypatch
):
    """Tools return errors; the agent module stays importable and registrable."""
    monkeypatch.setattr(
        notes_agent_module,
        "NotesTool",
        lambda *args, **kwargs: NotesTool(db_path=str(sealed_database)),
    )

    for result in (
        notes_agent_module.create_note(title="synthetic", content="synthetic"),
        notes_agent_module.list_notes(),
        notes_agent_module.count_notes(),
    ):
        assert result["status"] == "error"
        assert "Secure Professional Maximus" in result["message"]

    # The factory must still produce an agent - a storage fault is not a reason
    # to drop the capability from the runtime.
    assert callable(notes_agent_module.create_notes_agent)
    assert notes_agent_module.notes_storage_error() is not None


def test_successful_mutation_result_is_self_describing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notes_agent_module,
        "NotesTool",
        lambda *args, **kwargs: NotesTool(db_path=str(tmp_path / "autoyou_notes.db")),
    )

    result = notes_agent_module.create_note(
        title="synthetic title",
        content="synthetic body",
    )

    assert result["status"] == "success"
    assert result["note_id"] > 0
    assert "created successfully" in result["message"]
