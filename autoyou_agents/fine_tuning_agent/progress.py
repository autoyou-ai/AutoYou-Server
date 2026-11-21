# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The line protocol a trainer uses to tell the job supervisor how far it is.

Progress used to be a guess: the supervisor assumed a total duration from the
sample count and drew a bar from elapsed time, so a job that was slow, stuck or
nearly done looked identical. The trainer knows the real step; this module is
how it says so.

Each update is one line, ``AUTOYOU_PROGRESS {json}``, printed to the log (so it
survives in the sealed training log) and optionally appended to a progress file
the supervisor can read without parsing a large log.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import json
import math
import re
from typing import Any, Dict, Iterable, Optional

PROGRESS_PREFIX = "AUTOYOU_PROGRESS "

# mlx-lm's own log lines, so its progress feeds the same protocol.
_NUMBER = r"[-+]?(?:nan|inf|[0-9.]+(?:[eE][-+]?[0-9]+)?)"
_MLX_TRAIN_RE = re.compile(
    r"Iter\s+(?P<step>\d+):\s+Train loss\s+(?P<loss>" + _NUMBER + r"),\s+Learning Rate\s+(?P<lr>" + _NUMBER + r")"
    r"(?:,\s+It/sec\s+(?P<its>" + _NUMBER + r"))?(?:,\s+Tokens/sec\s+(?P<tps>" + _NUMBER + r"))?"
    r"(?:,.*?Peak mem\s+(?P<mem>" + _NUMBER + r")\s*GB)?",
    re.IGNORECASE,
)
_MLX_VAL_RE = re.compile(r"Iter\s+(?P<step>\d+):\s+Val loss\s+(?P<loss>" + _NUMBER + r")", re.IGNORECASE)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def format_progress(*, step: int, total_steps: int, loss: Optional[float] = None, eval_loss: Optional[float] = None,
                    learning_rate: Optional[float] = None, grad_norm: Optional[float] = None,
                    tokens_per_second: Optional[float] = None, memory_gib: Optional[float] = None,
                    seconds_per_step: Optional[float] = None, phase: str = "train") -> str:
    """One progress line. Non-finite numbers are reported as null, never as NaN."""
    payload: Dict[str, Any] = {"phase": phase, "step": int(step), "total_steps": int(total_steps)}
    for key, value in (("loss", loss), ("eval_loss", eval_loss), ("learning_rate", learning_rate),
                       ("grad_norm", grad_norm), ("tokens_per_second", tokens_per_second),
                       ("memory_gib", memory_gib), ("seconds_per_step", seconds_per_step)):
        if value is not None:
            payload[key] = _finite(value)
    if payload.get("loss") is None and loss is not None:
        payload["non_finite_loss"] = True
    if seconds_per_step and total_steps > step:
        payload["eta_seconds"] = int(max(0, total_steps - step) * float(seconds_per_step))
    return PROGRESS_PREFIX + json.dumps(payload, separators=(",", ":"))


def parse_progress_line(line: str) -> Optional[Dict[str, Any]]:
    text = str(line or "")
    index = text.find(PROGRESS_PREFIX)
    if index < 0:
        return None
    try:
        payload = json.loads(text[index + len(PROGRESS_PREFIX):].strip())
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) and "step" in payload else None


def last_progress(lines: Iterable[str]) -> Optional[Dict[str, Any]]:
    """The newest progress update in ``lines`` (any iterable of log lines)."""
    newest = None
    for line in lines:
        parsed = parse_progress_line(line)
        if parsed is not None:
            newest = parsed
    return newest


def progress_percent(update: Optional[Dict[str, Any]], *, floor: float = 2.0, ceiling: float = 95.0) -> Optional[float]:
    """Percent complete for a bar, kept inside the range reserved for training
    (the rest belongs to loading before and conversion after)."""
    if not update:
        return None
    total = int(update.get("total_steps") or 0)
    if total <= 0:
        return None
    fraction = min(1.0, max(0.0, int(update.get("step") or 0) / total))
    return round(floor + fraction * (ceiling - floor), 1)


def from_mlx_log_line(line: str, *, total_steps: int) -> Optional[Dict[str, Any]]:
    """Translate one mlx-lm log line into a progress update, or None."""
    text = str(line or "")
    match = _MLX_TRAIN_RE.search(text)
    if match:
        its = _finite(match.group("its"))
        update: Dict[str, Any] = {
            "phase": "train", "step": int(match.group("step")), "total_steps": int(total_steps),
            "loss": _finite(match.group("loss")), "learning_rate": _finite(match.group("lr")),
        }
        if its:
            update["seconds_per_step"] = round(1.0 / its, 3)
            update["eta_seconds"] = int(max(0, total_steps - update["step"]) / its)
        for key, group in (("tokens_per_second", "tps"), ("memory_gib", "mem")):
            value = _finite(match.group(group))
            if value is not None:
                update[key] = value
        if update["loss"] is None:
            update["non_finite_loss"] = True
        return update
    match = _MLX_VAL_RE.search(text)
    if match:
        return {"phase": "eval", "step": int(match.group("step")), "total_steps": int(total_steps),
                "eval_loss": _finite(match.group("loss"))}
    return None
