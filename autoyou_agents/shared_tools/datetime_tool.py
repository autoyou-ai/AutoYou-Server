# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Optional, Dict
import re

_logger = logging.getLogger(__name__)
_SYSTEM_CLOCK_LINE_RE = re.compile(r"^\[SYSTEM CLOCK\].*$", re.MULTILINE)

def get_current_datetime(tz: Optional[str] = None) -> Dict[str, Any]:
    try:
        if tz:
            now = datetime.now(ZoneInfo(tz))
            tz_used = tz
        else:
            now = datetime.now()
            tz_used = None
        return {
            "iso": now.isoformat(),
            "date": now.date().isoformat(),
            "time": now.time().isoformat(timespec="seconds"),
            "timezone": tz_used or "system-local",
        }
    except Exception as e:
        return {"error": f"Failed to get current datetime: {str(e)}"}

def inject_realtime_datetime_into_request(llm_request: Any) -> None:
    """Inject the real current date/time into an ADK LlmRequest system instruction.

    Call this from any agent's ``before_model_callback`` to ground the model
    with the live system clock.  Keeps injection minimal (one line) to avoid
    wasting tokens on cheap hardware.
    """
    try:
        dt_payload = get_current_datetime()
        iso = str(dt_payload.get("iso") or "").strip()
        if not iso:
            return
        dt = datetime.fromisoformat(iso)
        date_text = f"{dt.strftime('%B')} {dt.day}, {dt.year}"
        hour = str(int(dt.strftime("%I") or "0"))
        time_text = f"{hour}:{dt.strftime('%M %p')}"
        tz = str(dt_payload.get("timezone") or "").strip()
        datetime_line = f"[SYSTEM CLOCK] Today is {date_text}, {time_text}"
        if tz and tz != "system-local":
            datetime_line += f" ({tz})"
        datetime_line += ". This is the real current date from the host machine clock. Your training data may be older - trust this date."

        config = getattr(llm_request, "config", None)
        if config is None:
            return
        existing = getattr(config, "system_instruction", None)
        if isinstance(existing, str) and existing.strip():
            cleaned = _SYSTEM_CLOCK_LINE_RE.sub("", existing).strip()
            if cleaned:
                config.system_instruction = f"{datetime_line}\n\n{cleaned}"
            else:
                config.system_instruction = datetime_line
        else:
            config.system_instruction = datetime_line
    except Exception as exc:
        _logger.debug("Could not inject realtime datetime context: %s", exc)
