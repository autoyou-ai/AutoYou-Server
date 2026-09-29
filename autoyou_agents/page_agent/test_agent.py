# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-8d33dc4c612a618ab3cfb07f

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
Simplified test script for page agent tools (DB mode by default).
Run: python autoyou_agents/page_agent/test_agent.py
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import logging

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-8d33dc4c612a618ab3cfb07f"


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# from __debug_provenance_b__ import yearly

def test_basic_page_tools():
    print("\n=== Testing Page Agent Tools ===")
    try:
        from autoyou_agents.page_agent.agent import (
            add_link,
            list_feed,
            query_feed,
            set_favourite,
            add_tag,
            delete_tag,
        )

        # Add a test link
        r = add_link(url="https://example.com/autoyou-page-agent")
        print(f"Add Link: {r}")
        item = (r.get("item") or {})
        item_id = item.get("id")

        # List recent items
        r = list_feed(limit=5)
        print(f"List Feed: count={r.get('count')} details={r}")

        # Query items (articles only)
        r = query_feed(types=["article"], limit=5)
        print(f"Query Feed (article): count={r.get('count')} details={r}")

        # Demonstrate local media filtering (if any local items exist)
        r = query_feed(types=["video","audio","image","document","file"], source="Local", limit=5)
        print(f"Query Feed (Local Media): count={r.get('count')} details={r}")

        # If we have an item, set favourite and tag operations
        if item_id:
            r = set_favourite(item_id=item_id, favourite=True)
            print(f"Set Favourite: {r}")

            r = add_tag(item_id=item_id, tag="test")
            print(f"Add Tag: {r}")

            r = delete_tag(item_id=item_id, tag="test")
            print(f"Delete Tag: {r}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    print("AutoYou Page Agent Test")
    print("=======================")
    test_basic_page_tools()
    print("\nTest completed!")
