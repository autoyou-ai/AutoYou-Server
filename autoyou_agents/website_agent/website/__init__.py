# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-d53c259aab39184a41d2b55f

"""Managed Website Builder frontend package."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-5671323872316d5337695030-d53c259aab39184a41d2b55f"


from autoyou_agents.shared_tools.website_scaffold import (
    DEFAULT_FRONTEND_STACK,
    REACT_TYPESCRIPT_STACK,
)

SUPPORTED_FRONTEND_STACKS = (DEFAULT_FRONTEND_STACK, REACT_TYPESCRIPT_STACK)
