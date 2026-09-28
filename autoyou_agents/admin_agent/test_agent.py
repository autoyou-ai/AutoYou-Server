# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-646472657373202d20334163-b8935ebb1d27671b3565384e

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
Simplified test script for the admin agent's restart tool.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-646472657373202d20334163-b8935ebb1d27671b3565384e"


import logging
import os
import sys
from pathlib import Path

import pytest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if os.environ.get("AUTOYOU_RUN_LIVE_ADMIN_AGENT_TESTS") != "1":
    pytest.skip(
        "Manual admin-agent live restart smoke test. Set AUTOYOU_RUN_LIVE_ADMIN_AGENT_TESTS=1 to run.",
        allow_module_level=True,
    )

def test_restart_whatsapp():
    print("\n=== Testing WhatsApp Restart ===")
    try:
        # Ensure project root is importable when running directly
        try:
            PROJECT_ROOT = Path(__file__).resolve().parents[2]
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
        except Exception:
            pass

        from autoyou_agents.admin_agent.agent import restart_whatsapp

        # Show which Admin host/port will be used
        admin_host = os.environ.get("ADMIN_WEB_SERVICE_HOST", "localhost")
        admin_port = os.environ.get("ADMIN_WEB_SERVICE_PORT", "8001")
        print(f"Admin App target: http://{admin_host}:{admin_port}/api/whatsapp/restart")

        result = restart_whatsapp()
        print(f"Restart WhatsApp Result: {result}")
        if isinstance(result, dict) and result.get("url"):
            print(f"Request URL used: {result['url']}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    print("AutoYou Admin Agent Test")
    print("=========================")

    test_restart_whatsapp()

    print("\nTest completed!")
