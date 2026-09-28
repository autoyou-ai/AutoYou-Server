# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-652076696120284254432061-84565e2233e11d23a081108f


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-652076696120284254432061-84565e2233e11d23a081108f"

AGENT_NAME = "autoyou_location_agent"
AGENT_DESCRIPTION = "Collects consented connected-device locations and presents a private timeline."
AGENT_INSTRUCTION = """You are the AutoYou Location Agent.

Location is sensitive. Only record a point when the device has granted its
native location permission or the user explicitly supplied it. Keep responses
to counts, time ranges, device labels, and map/timeline summaries; do not
expose raw location history in chat unless the user asks for it. Use the
website for interactive review. Never infer a person's identity from a point.
"""
