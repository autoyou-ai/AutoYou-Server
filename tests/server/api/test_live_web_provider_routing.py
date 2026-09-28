# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-68747470733a2f2f6275792e-08a8d9c8518b0baebf039f98


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-68747470733a2f2f6275792e-08a8d9c8518b0baebf039f98"

from types import SimpleNamespace

import rest_api


def test_live_web_request_stays_in_the_autoyou_agent_graph():
    request = SimpleNamespace(
        message="Find current public status information online.",
        metadata={},
    )

    assert rest_api._request_requires_autoyou_agent_graph(request) is True


def test_scheduled_provider_classifier_uses_the_original_instruction():
    request = SimpleNamespace(
        message=(
            "Write a short joke.\n\nRecurring task execution rules:\n"
            "For current online information, verify it during this run."
        ),
        metadata={
            "source": "scheduler",
            "scheduled_task_instruction": "Write a short joke.",
        },
    )

    assert rest_api._request_requires_autoyou_agent_graph(request) is False


def test_ordinary_provider_chat_keeps_the_direct_gateway_path():
    request = SimpleNamespace(
        message="Say hello to the test runner.",
        metadata={},
    )

    assert rest_api._request_requires_autoyou_agent_graph(request) is False
