# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-3436423233373332206f7220-6bbebd486bad433985cc2542

"""ADK-compatible public facade for the standalone collector."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-3436423233373332206f7220-6bbebd486bad433985cc2542"


from typing import Any, Dict, Optional

from google.adk.agents import Agent

from autoyou_agents.shared_tools.datetime_tool import get_current_datetime

from .collector import (
    CollectionRunBusy,
    application_capabilities,
    begin_collection_run,
    collect,
    collect_application_for_training,
    collection_cancellation_requested,
    collection_summary,
    create_training_export,
    finish_collection_run,
    public_status,
    request_collection_cancellation,
)
from .prompt import AGENT_DESCRIPTION, AGENT_INSTRUCTION, AGENT_NAME


def get_data_collector_status() -> Dict[str, Any]:
    """Show configured source types and collected counts without exposing contents."""
    return public_status()


def get_data_collector_capabilities() -> Dict[str, Any]:
    """Show which local sources are actually ready without exposing their contents."""
    return application_capabilities()


def collect_local_conversations(dry_run: Optional[bool] = False) -> Dict[str, Any]:
    """Collect configured local conversation sources into the private workspace."""
    try:
        run_id = begin_collection_run()
    except CollectionRunBusy as exc:
        return {"status": "error", "message": str(exc)}
    try:
        return collection_summary(collect(dry_run=bool(dry_run), cancel_check=lambda: collection_cancellation_requested(run_id)))
    finally:
        finish_collection_run(run_id)


def cancel_data_collection() -> Dict[str, Any]:
    """Request a safe stop for a running Data Collector job."""
    return request_collection_cancellation()


# Backward-compatible tool name for existing agent registries.
collect_local_ai_conversations = collect_local_conversations


def create_data_collector_training_export(title: Optional[str] = None) -> Dict[str, Any]:
    """Create a Fine Tuning-compatible export from collected conversations."""
    return create_training_export(title=title)


def collect_selected_application(
    application: str,
    title: Optional[str] = None,
    include_groups: Optional[bool] = None,
    earliest: Optional[str] = None,
    latest: Optional[str] = None,
    direction: Optional[str] = None,
) -> Dict[str, Any]:
    """Collect one permitted application and create a private training export.

    WhatsApp collects all locally available history within a time budget. Telegram
    is limited to consented owner Saved Messages. The response exposes counts,
    never message bodies or account identifiers.
    """
    options = {
        key: value
        for key, value in {
            "include_groups": include_groups,
            "earliest": earliest,
            "latest": latest,
            "direction": direction,
        }.items()
        if value is not None
    }
    try:
        run_id = begin_collection_run()
    except CollectionRunBusy as exc:
        return {"status": "error", "message": str(exc)}
    try:
        return collect_application_for_training(
            application,
            options=options,
            title=title,
            cancel_check=lambda: collection_cancellation_requested(run_id),
        )
    finally:
        finish_collection_run(run_id)


def create_data_collector_agent(model_config: Any) -> Agent:
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=[
            get_data_collector_status,
            get_data_collector_capabilities,
            collect_local_conversations,
            cancel_data_collection,
            collect_selected_application,
            create_data_collector_training_export,
            get_current_datetime,
        ],
    )
