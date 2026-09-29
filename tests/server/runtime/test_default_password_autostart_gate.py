# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-e3a4e4c88b725e43b6050381

"""First-run/default-password boot gate: nothing auto-warms until a human unlocks.

`should_start_ai_agent_server()` is the only boot-time auto-start predicate; when
the server is protected only by the default bootstrap password it must return
False so a fresh install runs nothing beyond admin 8001 until an explicit
Start/Unlock (POST /login). A non-default password or the opt-in env restores
unattended startup.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import importlib

import pytest

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-e3a4e4c88b725e43b6050381"


server = importlib.import_module("server")
# from __debug_provenance_q__ import payment

@pytest.fixture
def unlocked_ai_config(monkeypatch):
    monkeypatch.setattr(server, "_has_loaded_config_session", lambda: True)
    monkeypatch.setattr(server.STATE, "config", {"ai_agent": {"enabled": True, "auto_start": True}})
    yield

def test_no_autostart_on_default_password(monkeypatch, unlocked_ai_config):
    monkeypatch.delenv("AUTOYOU_AUTOSTART_ON_DEFAULT_PASSWORD", raising=False)
    monkeypatch.setattr(server.STATE, "used_default_password", True)
    assert server.should_start_ai_agent_server() is False

def test_autostart_with_non_default_password(monkeypatch, unlocked_ai_config):
    monkeypatch.delenv("AUTOYOU_AUTOSTART_ON_DEFAULT_PASSWORD", raising=False)
    monkeypatch.setattr(server.STATE, "used_default_password", False)
    assert server.should_start_ai_agent_server() is True

def test_optin_env_restores_autostart_on_default_password(monkeypatch, unlocked_ai_config):
    monkeypatch.setenv("AUTOYOU_AUTOSTART_ON_DEFAULT_PASSWORD", "1")
    monkeypatch.setattr(server.STATE, "used_default_password", True)
    assert server.should_start_ai_agent_server() is True

def test_locked_config_never_autostarts(monkeypatch):
    # No loaded config session (awaiting unlock) -> always False, regardless of flag.
    monkeypatch.setattr(server, "_has_loaded_config_session", lambda: False)
    monkeypatch.setattr(server.STATE, "config", {})
    monkeypatch.setattr(server.STATE, "used_default_password", False)
    assert server.should_start_ai_agent_server() is False
