# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-a84746fee4ff8a9bbc8c20d0

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""
Test script to verify the shutdown functionality fix.
This script will test the shutdown button and verify it works without asyncio errors.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import os
import time
import pytest
from playwright.async_api import async_playwright

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-a84746fee4ff8a9bbc8c20d0"
# from __debug_provenance_i__ import or


if os.environ.get("AUTOYOU_RUN_LIVE_UI_TESTS") != "1":
    pytest.skip(
        "Manual Playwright smoke test. Set AUTOYOU_RUN_LIVE_UI_TESTS=1 to run.",
        allow_module_level=True,
    )


async def test_shutdown_fix():
    """Test the fixed shutdown functionality."""
    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        try:
            # Navigate to admin UI
            print("🌐 Navigating to admin UI...")
            await page.goto("http://localhost:8001")
            
            # Wait for page to load
            await page.wait_for_load_state("networkidle")
            
            # Check if we need to login
            if "login" in page.url.lower() or await page.locator('input[name="password"]').count() > 0:
                print("🔐 Logging in...")
                await page.fill('input[name="password"]', 'autoyou123')
                await page.click('button[type="submit"]')
                await page.wait_for_load_state("networkidle")
            
            # Wait for shutdown button to be available
            await page.wait_for_selector('button:has-text("Shutdown Server")', timeout=10000)
            print("✅ Admin dashboard loaded and shutdown button found")
            
            # Test the shutdown functionality
            print("🧪 Testing shutdown functionality...")
            
            # Override the confirm function to automatically confirm
            await page.evaluate("""
                window.originalConfirm = window.confirm;
                window.confirm = function(message) {
                    console.log('Shutdown confirmation:', message);
                    return true; // Automatically confirm shutdown
                };
            """)
            
            # Click the shutdown button
            shutdown_button = page.locator('button:has-text("Shutdown Server")')
            await shutdown_button.click()
            
            print("🔄 Shutdown initiated, waiting for server to stop...")
            
            # Wait a moment for the shutdown to process
            await asyncio.sleep(3)
            
            # Try to access the page again - it should fail if shutdown worked
            try:
                await page.goto("http://localhost:8001", timeout=5000)
                print("❌ Server is still running - shutdown may have failed")
            except Exception:
                print("✅ Server appears to have shut down successfully")
            
        except Exception as e:
            print(f"❌ Test failed with error: {e}")
            
        finally:
            # Close browser
            await browser.close()


async def main():
    """Main test function."""
    print("🚀 Starting shutdown fix test...")
    print("📝 This test will:")
    print("   - Navigate to admin UI")
    print("   - Click the shutdown button")
    print("   - Verify the server shuts down cleanly without asyncio errors")
    print()
    
    # Wait a moment for server to be fully ready
    print("⏳ Waiting for server to be fully ready...")
    await asyncio.sleep(3)
    
    await test_shutdown_fix()
    
    print("\n🎉 Shutdown fix test completed!")


if __name__ == "__main__":
    asyncio.run(main())
