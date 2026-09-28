# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Public, local-first AI conversation collection agent."""

from __future__ import annotations

__all__ = ["create_data_collector_agent"]


def create_data_collector_agent(model_config):
    from .agent import create_data_collector_agent as _create

    return _create(model_config)
