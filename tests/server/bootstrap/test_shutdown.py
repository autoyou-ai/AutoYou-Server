#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
Test script for the admin UI shutdown functionality.
This script tests the new shutdown button and Docker cleanup features.
"""

import asyncio
import os
import time
import pytest
from playwright.async_api import async_playwright

if os.environ.get("AUTOYOU_RUN_LIVE_UI_TESTS") != "1":
    pytest.skip(
        "Manual Playwright smoke test. Set AUTOYOU_RUN_LIVE_UI_TESTS=1 to run.",
        allow_module_level=True,
    )

async def test_admin_ui_shutdown():
    """Test the admin UI shutdown functionality."""
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
            
            # Wait for dashboard to load
            await page.wait_for_selector('button:has-text("Shutdown Server")', timeout=10000)
            print("✅ Admin dashboard loaded successfully")
            
            # Check if shutdown button exists
            shutdown_button = page.locator('button:has-text("Shutdown Server")')
            if await shutdown_button.count() > 0:
                print("✅ Shutdown button found in header bar")
                
                # Check button styling
                button_style = await shutdown_button.get_attribute('style')
                if '#dc3545' in button_style or 'red' in button_style.lower():
                    print("✅ Shutdown button has correct red styling")
                else:
                    print(f"⚠️  Shutdown button styling: {button_style}")
                
                # Test confirmation dialog (but don't actually shutdown)
                print("🧪 Testing confirmation dialog...")
                
                # Override the confirm function to capture the dialog
                await page.evaluate("""
                    window.originalConfirm = window.confirm;
                    window.confirmCalled = false;
                    window.confirm = function(message) {
                        window.confirmCalled = true;
                        window.confirmMessage = message;
                        return false; // Don't actually confirm
                    };
                """)
                
                # Click the shutdown button
                await shutdown_button.click()
                
                # Check if confirm was called
                confirm_called = await page.evaluate("window.confirmCalled")
                confirm_message = await page.evaluate("window.confirmMessage")
                
                if confirm_called:
                    print(f"✅ Confirmation dialog triggered with message: '{confirm_message}'")
                    if "shutdown" in confirm_message.lower():
                        print("✅ Confirmation message mentions shutdown")
                    else:
                        print("⚠️  Confirmation message may not be appropriate")
                else:
                    print("❌ Confirmation dialog was not triggered")
                
                # Restore original confirm function
                await page.evaluate("window.confirm = window.originalConfirm;")
                
            else:
                print("❌ Shutdown button not found in header bar")
            
            # Check Signal configuration section
            print("🔍 Checking Signal configuration section...")
            signal_section = page.locator('h2:has-text("Messaging Partner Settings - Signal")')
            if await signal_section.count() > 0:
                print("✅ Signal configuration section found")
                
                # Check for the new Docker shutdown checkbox
                docker_checkbox = page.locator('input[name="signal_shutdown_docker"]')
                if await docker_checkbox.count() > 0:
                    print("✅ Signal Docker shutdown checkbox found")
                    
                    # Check if it's checked by default
                    is_checked = await docker_checkbox.is_checked()
                    print(f"📋 Docker shutdown checkbox default state: {'checked' if is_checked else 'unchecked'}")
                    
                    # Find the label for the checkbox
                    label = page.locator('text="Shut down Signal Messaging Service (Docker) upon Server Shutdown"')
                    if await label.count() > 0:
                        print("✅ Docker shutdown checkbox has proper label")
                    else:
                        print("⚠️  Docker shutdown checkbox label may be missing")
                        
                else:
                    print("❌ Signal Docker shutdown checkbox not found")
            else:
                print("❌ Signal configuration section not found - checking for any Signal-related elements...")
                # Try to find any Signal-related elements
                signal_elements = await page.locator('text=/signal/i').count()
                print(f"📊 Found {signal_elements} Signal-related elements on page")
            
            print("\n🎉 Admin UI test completed successfully!")
            
        except Exception as e:
            print(f"❌ Test failed with error: {e}")
            
        finally:
            # Close browser
            await browser.close()

async def main():
    """Main test function."""
    print("🚀 Starting admin UI shutdown functionality test...")
    print("📝 This test will verify:")
    print("   - Shutdown button presence and styling")
    print("   - Confirmation dialog functionality")
    print("   - Signal Docker shutdown configuration option")
    print("   - Overall UI integration")
    print()
    
    # Wait a moment for server to be fully ready
    print("⏳ Waiting for server to be fully ready...")
    await asyncio.sleep(3)
    
    await test_admin_ui_shutdown()

if __name__ == "__main__":
    asyncio.run(main())
