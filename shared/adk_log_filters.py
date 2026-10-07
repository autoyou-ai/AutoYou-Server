# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Log filters for noise that Google ADK emits about AutoYou's own bookkeeping."""

from __future__ import annotations

import logging

__all__ = ["InternalEventAuthorFilter", "install_internal_event_filter"]

ADK_ROUTER_LOGGER_NAME = "google_adk.google.adk.agents._agent_router"

# session_utils appends state-only events with these authors to every session.
INTERNAL_EVENT_AUTHORS = frozenset({"system", "autoyou"})


class InternalEventAuthorFilter(logging.Filter):
    """Drop ADK's "Event from an unknown agent" warning for AutoYou's own events.

    While choosing which agent answers next, ADK's router scans the session's
    history and warns about every event whose author is not an agent in the
    tree. AutoYou stores its state deltas as events authored "system" and
    "autoyou", so each chat turn logged two or three of these warnings. They are
    harmless: the router skips those events and falls back to the root agent.
    A warning about any other unknown author is still logged.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if "unknown agent" not in str(record.msg):
            return True
        args = record.args
        if isinstance(args, tuple) and args and args[0] in INTERNAL_EVENT_AUTHORS:
            return False
        return True


def install_internal_event_filter() -> None:
    """Attach the filter to ADK's router logger once, however often this is called."""
    router_logger = logging.getLogger(ADK_ROUTER_LOGGER_NAME)
    if not any(isinstance(item, InternalEventAuthorFilter) for item in router_logger.filters):
        router_logger.addFilter(InternalEventAuthorFilter())
