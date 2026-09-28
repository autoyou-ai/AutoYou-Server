# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Tests for the Hermes Agent *sub-agent* (autoyou_agents/hermes_agent).

Hermes already exists as an LLM provider (see test_hermes_e2e.py). This module
covers the sub-agent that the root ADK agent can route to: its tools
(query_hermes / check_hermes_status) against a mock gateway, the agent factory,
and its registration in the root agent + install registry.
"""

from __future__ import annotations

import socket

import pytest

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

# Reuse the in-process mock Hermes gateway from the provider e2e module.
from tests.server.e2e.hermes.test_hermes_e2e import mock_hermes_gateway, HERMES_CANNED_REPLY


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# -- Tools against the mock gateway -------------------------------------------


def test_query_hermes_returns_reply(monkeypatch):
    from autoyou_agents.hermes_agent.agent import query_hermes

    port = _free_port()
    monkeypatch.setenv("HERMES_PORT", str(port))
    monkeypatch.delenv("HERMES_AGENT_PORT", raising=False)
    monkeypatch.setenv("HERMES_TOKEN", "")
    with mock_hermes_gateway(port=port, canned_reply="sub-agent hermes reply"):
        result = query_hermes("ping from hermes sub-agent test")
    assert result == "sub-agent hermes reply", f"unexpected reply: {result!r}"


def test_check_hermes_status_running(monkeypatch):
    from autoyou_agents.hermes_agent.agent import check_hermes_status

    port = _free_port()
    monkeypatch.setenv("HERMES_PORT", str(port))
    monkeypatch.delenv("HERMES_AGENT_PORT", raising=False)
    with mock_hermes_gateway(port=port):
        status = check_hermes_status()
    assert "running" in status.lower()
    assert str(port) in status
    assert "hermes-agent" in status


def test_query_hermes_gateway_unavailable(monkeypatch):
    from autoyou_agents.hermes_agent.agent import query_hermes

    # Point at a free port with nothing listening.
    port = _free_port()
    monkeypatch.setenv("HERMES_PORT", str(port))
    monkeypatch.delenv("HERMES_AGENT_PORT", raising=False)
    result = query_hermes("this should fail gracefully")
    assert "not running" in result.lower()
    assert str(port) in result


# -- Agent factory ------------------------------------------------------------


def test_create_hermes_agent_builds():
    from autoyou_agents.hermes_agent.agent import (
        create_hermes_agent,
        query_hermes,
        check_hermes_status,
    )
    from autoyou_agents.hermes_agent.prompt import AGENT_NAME

    agent = create_hermes_agent("gemini-2.5-flash")
    assert agent.name == AGENT_NAME == "autoyou_hermes_agent"
    tool_fns = {getattr(t, "__name__", getattr(t, "name", "")) for t in agent.tools}
    assert "query_hermes" in tool_fns
    assert "check_hermes_status" in tool_fns


# -- Registration in the root agent + install registry ------------------------


def test_hermes_agent_registered_in_factory_map():
    import autoyou_agents.agent as root_agent_mod

    assert "hermes_agent" in root_agent_mod._STATIC_AGENT_FACTORY_MAP
    from autoyou_agents.hermes_agent.agent import create_hermes_agent
    assert root_agent_mod._STATIC_AGENT_FACTORY_MAP["hermes_agent"] is create_hermes_agent


def test_hermes_agent_in_install_registry_defaults():
    from autoyou_agents.shared_tools.agent_install_registry import (
        DEFAULT_AGENT_INSTALL_STATES,
        BUILTIN_AGENT_PACKAGE_NAMES,
    )

    assert "hermes_agent" in DEFAULT_AGENT_INSTALL_STATES
    # Optional by default (install manually when the gateway is running).
    assert DEFAULT_AGENT_INSTALL_STATES["hermes_agent"] is False
    assert "hermes_agent" in BUILTIN_AGENT_PACKAGE_NAMES


def test_hermes_routing_aliases_present():
    import autoyou_agents.agent as root_agent_mod

    aliases = {
        alias
        for tup in root_agent_mod._EXPLICIT_ROUTE_ALIASES.values()
        for alias in tup
    }
    assert "hermes" in aliases
    assert "hermes_agent" in aliases
