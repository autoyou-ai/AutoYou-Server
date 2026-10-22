"""The Notes page names why storage is unavailable without echoing the exception."""

from __future__ import annotations

import sqlite3

import pytest

import autoyou_agents.notes_agent.website.backend.app as notes_app
from autoyou_agents.notes_agent.notes_tool import NotesStorageSealedError

SYNTHETIC_PATH = "/Users/example/Library/Application Support/AutoYou/page_notes.db"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (NotesStorageSealedError(f"sealed. Path: {SYNTHETIC_PATH}"), "sealed by Secure Professional Maximus"),
        (PermissionError(13, "Permission denied", SYNTHETIC_PATH), "read-only"),
        (FileNotFoundError(2, "No such file or directory", SYNTHETIC_PATH), "folder is missing"),
        (sqlite3.DatabaseError("file is not a database"), "could not be opened"),
        (RuntimeError(SYNTHETIC_PATH), notes_app.STORAGE_UNAVAILABLE_MESSAGE),
    ],
)
def test_storage_reason_names_the_cause_without_the_path(exc: Exception, expected: str) -> None:
    reason = notes_app._storage_error_reason(exc)
    assert expected in reason
    assert SYNTHETIC_PATH not in reason
    assert "/Users/example" not in reason
