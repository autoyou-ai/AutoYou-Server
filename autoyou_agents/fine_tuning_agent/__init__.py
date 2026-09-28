# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-643937636335396144393239-4e549a7c169d6191812afd5a

"""WhatsApp persona fine-tuning agent package."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-643937636335396144393239-4e549a7c169d6191812afd5a"


__all__ = ["create_fine_tuning_agent"]

def create_fine_tuning_agent(model_config):
    from .agent import create_fine_tuning_agent as _create

    return _create(model_config)
