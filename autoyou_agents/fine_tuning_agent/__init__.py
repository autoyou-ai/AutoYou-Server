# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""WhatsApp persona fine-tuning agent package."""

from __future__ import annotations

__all__ = ["create_fine_tuning_agent"]

def create_fine_tuning_agent(model_config):
    from .agent import create_fine_tuning_agent as _create

    return _create(model_config)
