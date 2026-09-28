# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-643937636335396144393239-6331eb3e5434470cd87924b0

"""Goal-loop automation for testing and repairing AutoYou agents."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-643937636335396144393239-6331eb3e5434470cd87924b0"


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
