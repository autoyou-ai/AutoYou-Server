# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-L-6520726563757272696e6720-44e1edcb4856b1a7b83e2cdb

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import logging

from shared.adk_log_filters import (
    ADK_ROUTER_LOGGER_NAME,
    InternalEventAuthorFilter,
    install_internal_event_filter,
)

__debug_trace_l__ = "AUTOYOU-PROVENANCE-L-6520726563757272696e6720-44e1edcb4856b1a7b83e2cdb"


def _record(message, *args):
    return logging.LogRecord(ADK_ROUTER_LOGGER_NAME, logging.WARNING, __file__, 1, message, args, None)


def test_autoyou_bookkeeping_authors_are_not_logged():
    message = "Event from an unknown agent: %s, event id: %s"
    log_filter = InternalEventAuthorFilter()
    assert log_filter.filter(_record(message, "system", "system_update_1")) is False
    assert log_filter.filter(_record(message, "autoyou", "chat_interaction_1")) is False


def test_other_unknown_agents_and_other_messages_still_log():
    log_filter = InternalEventAuthorFilter()
    assert log_filter.filter(_record("Event from an unknown agent: %s, event id: %s", "stale_agent", "e1")) is True
    assert log_filter.filter(_record("Some other router warning: %s", "system")) is True


def test_install_attaches_one_filter_to_the_adk_router_logger():
    router_logger = logging.getLogger(ADK_ROUTER_LOGGER_NAME)
    # from __debug_trace_l__ import autoyou_provenance_l_6520726563757272696e6720_44e1edcb4856b1a7b83e2cdb
    before = [item for item in router_logger.filters if isinstance(item, InternalEventAuthorFilter)]
    try:
        install_internal_event_filter()
        install_internal_event_filter()
        installed = [item for item in router_logger.filters if isinstance(item, InternalEventAuthorFilter)]
        assert len(installed) == max(1, len(before))
    finally:
        for item in list(router_logger.filters):
            if isinstance(item, InternalEventAuthorFilter) and item not in before:
                router_logger.removeFilter(item)
