# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Tests for the generic data-extraction filter + timeline model (fine_tuning)."""

import pytest

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

from autoyou_agents.fine_tuning_agent.fine_tuning_tool import (
    _normalize_dump_config,
    start_whatsapp_history_dump_job,
)


def test_defaults_request_full_available_history_with_a_time_budget():
    cfg = _normalize_dump_config({})
    assert cfg["include_personal"] is True
    assert cfg["include_groups"] is False
    assert cfg["chat_scope"] == "personal"
    assert cfg["timeline_mode"] == "all_time"
    assert cfg["earliest"] is None and cfg["latest"] is None
    assert cfg["all_available_history"] is True
    assert cfg["timeout_seconds"] == 14400
    assert "max_chats" not in cfg
    assert "messages_per_chat" not in cfg
    assert "max_total_messages" not in cfg


def test_time_budget_is_bounded_without_limiting_history_depth():
    cfg = _normalize_dump_config({"all_available_history": False, "timeout_seconds": 999999})

    assert cfg["all_available_history"] is True
    assert cfg["timeout_seconds"] == 86400


def test_include_flags_map_to_scope():
    assert _normalize_dump_config({"include_personal": True, "include_groups": True})["chat_scope"] == "all"
    assert _normalize_dump_config({"include_personal": False, "include_groups": True})["chat_scope"] == "groups"
    assert _normalize_dump_config({"include_personal": True, "include_groups": False})["chat_scope"] == "personal"
    # Neither selected => never pulls nothing; falls back to personal.
    assert _normalize_dump_config({"include_personal": False, "include_groups": False})["chat_scope"] == "personal"


def test_legacy_scope_still_honored_and_echoes_flags():
    cfg = _normalize_dump_config({"chat_scope": "all"})
    assert cfg["chat_scope"] == "all"
    assert cfg["include_personal"] is True and cfg["include_groups"] is True


def test_timeline_range_valid():
    cfg = _normalize_dump_config({"timeline_mode": "range", "earliest": "2024-01-01", "latest": "2024-06-30"})
    assert cfg["timeline_mode"] == "range"
    assert cfg["earliest"] == "2024-01-01" and cfg["latest"] == "2024-06-30"


def test_timeline_all_time_clears_bounds():
    cfg = _normalize_dump_config({"timeline_mode": "all_time", "earliest": "2024-01-01", "latest": "2024-06-30"})
    assert cfg["timeline_mode"] == "all_time"
    assert cfg["earliest"] is None and cfg["latest"] is None


def test_inverted_range_rejected():
    with pytest.raises(ValueError):
        _normalize_dump_config({"timeline_mode": "range", "earliest": "2024-12-31", "latest": "2024-01-01"})


def test_open_ended_range_allowed():
    # Only an earliest (everything since) or only a latest (everything until) is fine.
    assert _normalize_dump_config({"timeline_mode": "range", "earliest": "2024-01-01"})["latest"] is None
    assert _normalize_dump_config({"timeline_mode": "range", "latest": "2024-01-01"})["earliest"] is None


def test_invalid_date_rejected():
    with pytest.raises(ValueError):
        _normalize_dump_config({"timeline_mode": "range", "earliest": "not-a-date"})


def test_start_job_surfaces_inverted_range_error():
    # Bad range must come back as a clean error result, not an exception.
    res = start_whatsapp_history_dump_job(
        {"timeline_mode": "range", "earliest": "2025-01-01", "latest": "2024-01-01"}
    )
    assert res["status"] == "error"
    assert "earliest" in res["message"].lower()
