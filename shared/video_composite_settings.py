# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Settings for arranging sources within one outbound video track."""

from typing import Any, Dict


def normalize_video_composite_config(raw_value: Any, *, strict: bool = False) -> Dict[str, Any]:
    raw = raw_value if isinstance(raw_value, dict) else {}
    layout = str(raw.get("layout") or "grid").strip().lower()
    if layout not in {"grid", "below", "beside"}:
        if strict:
            raise ValueError("Video layout must be grid, below, or beside.")
        layout = "grid"
    try:
        size_percent = int(raw.get("secondary_size_percent", 25))
    except (TypeError, ValueError, OverflowError):
        if strict:
            raise ValueError("Camera / file size must be a number from 10 to 50 percent.")
        size_percent = 25
    return {"layout": layout, "secondary_size_percent": max(10, min(50, size_percent))}
