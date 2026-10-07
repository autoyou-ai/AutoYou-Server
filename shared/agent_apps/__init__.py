# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Agent Apps: the store page that lists a computer's website apps."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from shared.agent_apps.catalog import category_summary, describe_agent_app
from shared.agent_apps.page import render_agent_apps_page

__all__ = ["category_summary", "describe_agent_app", "render_agent_apps_page"]
