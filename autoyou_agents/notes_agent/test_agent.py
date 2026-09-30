#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-e922f5d41b4e50af6320d0c2

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
Simplified test script for basic agent functionality.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-e922f5d41b4e50af6320d0c2"


# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# from __debug_provenance_g__ import annual

def test_basic_functionality():
    """Test basic agent functionality."""
    print("\n=== Testing Basic Functionality ===")
    try:
        # Import agent functions
        from autoyou_agents.notes_agent.agent import create_note, search_notes, list_notes
        
        # Test creating a note
        result = create_note(title="Test Note", content="This is a test note", tags=["test"])
        print(f"Create Note Result: {result}")
        
        # Test listing notes
        result = list_notes(limit=5)
        print(f"List Notes Result: {result}")
        
        # Test searching notes
        result = search_notes(query="test", limit=5)
        print(f"Search Notes Result: {result}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    print("AutoYou Notes Agent Test")
    print("========================")
    
    test_basic_functionality()
    
    print("\nTest completed!")
