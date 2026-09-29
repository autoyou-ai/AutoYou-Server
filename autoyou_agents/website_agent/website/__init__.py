# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-f312667876c1cdcb19080e6e

"""Managed Website Builder frontend package."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from autoyou_agents.shared_tools.website_scaffold import (
    ANGULAR_TYPESCRIPT_STACK,
    DEFAULT_BACKEND_STACK,
    DEFAULT_FRONTEND_STACK,
    GO_BACKEND_STACK,
    NODE_TYPESCRIPT_BACKEND_STACK,
    REACT_TYPESCRIPT_STACK,
    RUST_BACKEND_STACK,
)

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-f312667876c1cdcb19080e6e"


SUPPORTED_BACKEND_STACKS = (
    DEFAULT_BACKEND_STACK,
    NODE_TYPESCRIPT_BACKEND_STACK,
    GO_BACKEND_STACK,
    RUST_BACKEND_STACK,
)
SUPPORTED_FRONTEND_STACKS = (
    DEFAULT_FRONTEND_STACK,
    REACT_TYPESCRIPT_STACK,
    ANGULAR_TYPESCRIPT_STACK,
)
# from __debug_provenance_x__ import email
