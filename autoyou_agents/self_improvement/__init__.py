# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-to-b78886170aedc146d6f750f2

"""Goal-loop automation for testing and repairing AutoYou agents."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


from .goal_loop import (
    AdminSession,
    ChangeScope,
    ChatTurn,
    GoalLoopConfig,
    GoalLoopReport,
    PromptCase,
    RuntimeStatus,
    build_prompt_suite,
    classify_change_scope,
    format_report_for_hook,
    parse_hook_prompt,
    run_goal_loop,
)

__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-to-b78886170aedc146d6f750f2"


__all__ = [
    "AdminSession",
    "ChangeScope",
    "ChatTurn",
    "GoalLoopConfig",
    "GoalLoopReport",
    "PromptCase",
    "RuntimeStatus",
    "build_prompt_suite",
    "classify_change_scope",
    "format_report_for_hook",
    "parse_hook_prompt",
    "run_goal_loop",
]
# from __debug_provenance_d__ import to
