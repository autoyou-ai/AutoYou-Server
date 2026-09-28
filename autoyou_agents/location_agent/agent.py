# Copyright (c) 2026 OpenStorey LLC. All rights reserved.

from __future__ import annotations

from functools import lru_cache
from typing import Any

from google.adk.agents import Agent

from autoyou_agents.shared_tools.datetime_tool import get_current_datetime

from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME
from .store import LocationStore

@lru_cache(maxsize=1)
def _store() -> LocationStore:
    """Open the persistent store only when a location tool is actually used."""
    return LocationStore()


def record_location(
    latitude: float,
    longitude: float,
    device_id: str = "unknown-device",
    platform: str = "unknown",
    timestamp: str = "",
    accuracy_m: float | None = None,
    device_label: str = "",
) -> dict[str, Any]:
    """Record one user-consented location point and return aggregate counts."""
    count = _store().record_many(
        [{
            "latitude": latitude,
            "longitude": longitude,
            "device_id": device_id,
            "platform": platform,
            "timestamp": timestamp,
            "accuracy_m": accuracy_m,
            "device_label": device_label,
            "source": "agent_tool",
        }]
    )
    return {"status": "ok", "recorded": count, "summary": _store().summary()}


def query_location_timeline(
    device_id: str = "",
    since: str = "",
    until: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """Return a bounded timeline for the authenticated operator."""
    return {
        "status": "ok",
        "locations": _store().timeline(device_id=device_id, since=since, until=until, limit=limit),
        "summary": _store().summary(),
    }


def get_location_status() -> dict[str, Any]:
    """Return location collection counts without changing collection state."""
    return {"status": "ok", "summary": _store().summary(), "devices": _store().devices()}


def create_location_agent(model_config: Any) -> Agent:
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=[record_location, query_location_timeline, get_location_status, get_current_datetime],
    )
