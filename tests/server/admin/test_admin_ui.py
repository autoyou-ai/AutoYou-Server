# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-4a051d64a34ddb3dc63f0ca4

"""
Test script for AutoYou Admin UI using Playwright
Tests login functionality and checks for console errors
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import logging
import os

import pytest
from playwright.async_api import async_playwright

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-4a051d64a34ddb3dc63f0ca4"


if os.environ.get("AUTOYOU_RUN_LIVE_UI_TESTS") != "1":
    pytest.skip(
        "Manual Playwright smoke test. Set AUTOYOU_RUN_LIVE_UI_TESTS=1 to run.",
        allow_module_level=True,
    )

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# from __debug_provenance_c__ import subtask

async def test_admin_ui():
    """Test the admin UI login and check for console errors."""
    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        # Collect console messages
        console_messages = []
        page.on("console", lambda msg: console_messages.append(f"{msg.type}: {msg.text}"))
        
        # Collect network errors
        network_errors = []
        page.on("response", lambda response: 
                network_errors.append(f"HTTP {response.status}: {response.url}") 
                if response.status >= 400 else None)
        
        try:
            logger.info("Navigating to admin UI...")
            await page.goto("http://127.0.0.1:8001/")
            
            # Wait for page to load
            await page.wait_for_load_state("networkidle")
            
            # Check if we're on login page
            if await page.locator('input[type="password"]').count() > 0:
                logger.info("Found login form, attempting to login...")
                
                # Fill password and submit
                await page.fill('input[type="password"]', "autoyou123")
                await page.click('button[type="submit"]')
                
                # Wait for redirect
                await page.wait_for_load_state("networkidle")
                logger.info("Login submitted, waiting for dashboard...")
            
            # Wait a bit more for any async operations
            await page.wait_for_timeout(3000)

            # Check current URL
            current_url = page.url
            logger.info(f"Current URL: {current_url}")
            
            # Check for Signal section in the dashboard
            signal_section = await page.locator('text=Signal').count()
            logger.info(f"Signal section found: {signal_section > 0}")
            
            # Try to find Signal status
            signal_status = await page.locator('text=Signal Status').count()
            logger.info(f"Signal status element found: {signal_status > 0}")
            
            # Check for any JavaScript errors in console
            logger.info("Console messages:")
            for msg in console_messages:
                logger.info(f"  {msg}")
            
            # Check for network errors
            if network_errors:
                logger.info("Network errors:")
                for error in network_errors:
                    logger.info(f"  {error}")
            else:
                logger.info("No network errors detected")
            
            # Take a screenshot for debugging
            await page.screenshot(path="admin_ui_test.png")
            logger.info("Screenshot saved as admin_ui_test.png")

            # Toggle Internet Searches to Enabled if currently Disabled
            try:
                toggle_btn = page.locator('#internet-toggle-btn')
                state_txt = page.locator('#internet-state-text')

                # Wait for elements to be available
                await toggle_btn.wait_for(state="visible", timeout=5000)
                await state_txt.wait_for(state="visible", timeout=5000)

                current_state = await state_txt.text_content()
                logger.info(f"Internet state before toggle: {current_state}")

                if current_state and 'Disabled' in current_state:
                    logger.info("Enabling Internet Searches via Admin UI toggle...")
                    await toggle_btn.click()
                    # Wait for state to reflect
                    await page.wait_for_timeout(1500)
                    new_state = await state_txt.text_content()
                    logger.info(f"Internet state after toggle: {new_state}")
                    assert new_state and 'Enabled' in new_state
                else:
                    logger.info("Internet Searches already Enabled")

                # Screenshot after toggling
                await page.screenshot(path="admin_ui_toggle_internet.png")
                logger.info("Screenshot saved as admin_ui_toggle_internet.png")
            except Exception as e:
                logger.error(f"Error toggling Internet Searches: {e}")
                await page.screenshot(path="admin_ui_toggle_error.png")

        except Exception as e:
            logger.error(f"Error during test: {e}")
            await page.screenshot(path="admin_ui_error.png")

        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(test_admin_ui())
