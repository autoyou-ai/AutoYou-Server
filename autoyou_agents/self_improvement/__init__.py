# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Goal-loop automation for testing and repairing AutoYou agents."""

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
