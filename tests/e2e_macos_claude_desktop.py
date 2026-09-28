# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-534454206164647265737320-7a01c07154246c9a96e40897


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-534454206164647265737320-7a01c07154246c9a96e40897"

import os
import subprocess
import sys
import pytest

@pytest.mark.skipif(sys.platform != "darwin", reason="Claude Desktop E2E tests are macOS specific")
def test_macos_claude_desktop_e2e_via_goal_loop():
    """
    Live E2E test to verify claude_desktop_agent works through the natural language /api/chat.
    It uses goal_loop.py to send sequential prompts:
    1. 'go to claude desktop agent'
    2. 'using claude_desktop_agent , send prompt - "Is there anything left pending?"'
    3. 'send prompt'
    """
    # Check if Claude is actually installed/running to avoid failing in environments without it
    # But since it's an E2E test specifically requested, we'll try to run it.
    
    script_path = os.path.join("autoyou_agents", "self_improvement", "goal_loop.py")
    
    # Run the goal loop with the specified prompts
    cmd = [
        sys.executable, script_path,
        "go to claude desktop agent",
        "--prompt", "using claude_desktop_agent , send prompt - \"Is there anything left pending?\"",
        "--prompt", "send prompt",
        "--request-timeout", "300.0"
    ]
    
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    
    # We use a longer timeout for the test to prevent it from hanging indefinitely in CI
    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=600
        )
        
        # Verify goal_loop.py passed (exited with 0)
        assert result.returncode == 0, f"goal_loop.py failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        
        # Optionally, verify specific output indicating success
        assert "PASS" in result.stdout or "AutoYou goal loop passed" in result.stdout, "goal_loop.py did not report success."
        
    except subprocess.TimeoutExpired as e:
        pytest.fail(f"goal_loop.py timed out after 600 seconds. Output so far:\n{e.stdout}")
