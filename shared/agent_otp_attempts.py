"""A shared, bounded guessing budget for every agent website OTP entrypoint."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
import secrets
import sqlite3
import time
from pathlib import Path

WINDOW_SECONDS = 300
CLIENT_ATTEMPTS = 5
GLOBAL_ATTEMPTS = 20
_clock = time.time


def _database_path() -> Path:
    # get_user_data_dir applies AUTOYOU_TEST_ROOT before any production path.
    from shared.platform_runtime import get_user_data_dir

    return Path(get_user_data_dir()) / "agent_otp_attempts.sqlite3"


@contextmanager
def _connect():
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS attempts "
            "(id TEXT PRIMARY KEY, caller TEXT NOT NULL, created REAL NOT NULL)"
        )
        with connection:
            yield connection
    finally:
        connection.close()


def reserve_attempt(caller: str) -> str | None:
    """Reserve before verification; failures and in-flight attempts share a cap.

    One database covers factory, bespoke and publishing logins, even when
    agents run in separate processes or share an authenticator profile.
    """
    now = _clock()
    caller_hash = hashlib.sha256(caller.encode("utf-8")).hexdigest()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM attempts WHERE created <= ?", (now - WINDOW_SECONDS,))
        total, own = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(caller = ?), 0) FROM attempts", (caller_hash,)
        ).fetchone()
        if total >= GLOBAL_ATTEMPTS or own >= CLIENT_ATTEMPTS:
            return None
        attempt = secrets.token_hex(16)
        connection.execute("INSERT INTO attempts VALUES (?, ?, ?)", (attempt, caller_hash, now))
        return attempt


def accept_attempt(attempt: str) -> None:
    """A successful login releases only its own reservation, never failures."""
    with _connect() as connection:
        connection.execute("DELETE FROM attempts WHERE id = ?", (attempt,))
