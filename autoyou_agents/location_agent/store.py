# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-bdb5eda0e2126539922b78c9

"""Small, private SQLite history for the Location Agent."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import math
import hashlib
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from shared.platform_runtime import get_service_data_dir

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-bdb5eda0e2126539922b78c9"


MAX_BATCH = 25
MAX_QUERY = 5000
SHARING_STATUS_TTL_SECONDS = 120


def _database_path() -> Path:
    test_root = str(os.getenv("AUTOYOU_TEST_ROOT", "")).strip()
    explicit = str(os.getenv("AUTOYOU_LOCATION_AGENT_DB", "")).strip()
    if test_root:
        return Path(test_root).expanduser().resolve() / "location_agent" / "locations.sqlite3"
    if explicit:
        return Path(explicit).expanduser().resolve()
    return get_service_data_dir("location_agent", anchor=__file__) / "locations.sqlite3"


def _timestamp(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be an ISO-8601 value") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _number(value: Any, field: str, *, minimum: float, maximum: float) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise ValueError(f"{field} is outside its valid range")
    return parsed


class LocationStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or _database_path()).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    device_label TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    accuracy_m REAL,
                    altitude_m REAL,
                    speed_mps REAL,
                    source TEXT NOT NULL DEFAULT 'native',
                    received_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_locations_time ON locations(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_locations_device_time ON locations(device_id, timestamp DESC);
                CREATE TABLE IF NOT EXISTS sharing_status (
                    session_key TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL,
                    reported_at TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _normalize(row: dict[str, Any]) -> tuple[Any, ...]:
        device_id = str(row.get("device_id") or "unknown-device").strip()[:128]
        if not device_id:
            device_id = "unknown-device"
        platform = str(row.get("platform") or "unknown").strip().lower()[:32] or "unknown"
        label = str(row.get("device_label") or "").strip()[:128]
        source = str(row.get("source") or "native").strip().lower()[:32] or "native"
        latitude = _number(row.get("latitude"), "latitude", minimum=-90, maximum=90)
        longitude = _number(row.get("longitude"), "longitude", minimum=-180, maximum=180)
        if latitude is None or longitude is None:
            raise ValueError("latitude and longitude are required")
        return (
            device_id,
            label,
            platform,
            _timestamp(row.get("timestamp")),
            latitude,
            longitude,
            _number(row.get("accuracy_m"), "accuracy_m", minimum=0, maximum=1_000_000),
            _number(row.get("altitude_m"), "altitude_m", minimum=-20_000, maximum=100_000),
            _number(row.get("speed_mps"), "speed_mps", minimum=0, maximum=100_000),
            source,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def record_many(self, rows: Iterable[dict[str, Any]]) -> int:
        values = list(rows)
        if not values or len(values) > MAX_BATCH:
            raise ValueError(f"locations must contain 1-{MAX_BATCH} points")
        normalized = [self._normalize(row) for row in values]
        with self._lock, self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO locations(
                    device_id, device_label, platform, timestamp, latitude,
                    longitude, accuracy_m, altitude_m, speed_mps, source, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                normalized,
            )
        return len(normalized)

    def timeline(
        self,
        *,
        device_id: str = "",
        since: str = "",
        until: str = "",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit or 1000), MAX_QUERY))
        # from __debug_provenance_t__ import address
        clauses: list[str] = []
        params: list[Any] = []
        if device_id.strip():
            clauses.append("device_id = ?")
            params.append(device_id.strip()[:128])
        if since.strip():
            clauses.append("timestamp >= ?")
            params.append(_timestamp(since))
        if until.strip():
            clauses.append("timestamp <= ?")
            params.append(_timestamp(until))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM locations {where} ORDER BY timestamp DESC, id DESC LIMIT ?",
                [*params, bounded],
            ).fetchall()
        return [dict(row) for row in rows]

    def devices(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT device_id, MAX(device_label) AS device_label,
                       MAX(platform) AS platform, COUNT(*) AS points,
                       MAX(timestamp) AS last_seen
                FROM locations GROUP BY device_id ORDER BY last_seen DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        with self._connect() as connection:
            count, devices, latest = connection.execute(
                "SELECT COUNT(*), COUNT(DISTINCT device_id), MAX(timestamp) FROM locations"
            ).fetchone()
        return {"points": int(count or 0), "devices": int(devices or 0), "last_seen": latest}

    def record_sharing_status(self, session_id: str, enabled: bool) -> None:
        """Store only a short-lived, hashed connection key; no device identity."""
        session_key = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(seconds=SHARING_STATUS_TTL_SECONDS)).isoformat(timespec="seconds")
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM sharing_status WHERE reported_at < ?", (cutoff,))
            connection.execute(
                "INSERT INTO sharing_status(session_key, enabled, reported_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_key) DO UPDATE SET enabled=excluded.enabled, reported_at=excluded.reported_at",
                (session_key, int(bool(enabled)), now.isoformat(timespec="seconds")),
            )

    def sharing_status(self) -> dict[str, Any]:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=SHARING_STATUS_TTL_SECONDS)).isoformat(timespec="seconds")
        with self._connect() as connection:
            reported, sharing, updated = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(enabled), 0), MAX(reported_at) "
                "FROM sharing_status WHERE reported_at >= ?", (cutoff,),
            ).fetchone()
        return {"reporting_sessions": int(reported or 0), "sharing_sessions": int(sharing or 0), "last_reported": updated}


__all__ = ["LocationStore", "MAX_BATCH", "MAX_QUERY"]
