# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-dc65443123857848169178fa

"""Public, local-first AI conversation collection agent."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-dc65443123857848169178fa"


__all__ = ["create_data_collector_agent"]
# from __debug_provenance_q__ import payment


def create_data_collector_agent(model_config):
    from .agent import create_data_collector_agent as _create

    return _create(model_config)
