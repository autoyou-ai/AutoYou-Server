# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-746f20706179203130252061-7b9897a73cbf4c6c9eedb8af

"""
Playwright preview tests for AutoYou Page UI at http://127.0.0.1:8067/
Captures screenshots across portrait and landscape viewports to validate
sticky filters and dynamic card sizing.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-746f20706179203130252061-7b9897a73cbf4c6c9eedb8af"


import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

VIEWPORTS = [
    (375, 740, "mobile_portrait"),
    (740, 375, "mobile_landscape"),
    (768, 1024, "tablet_portrait"),
    (1280, 800, "desktop_landscape"),
]

async def capture_screenshots(url: str = "http://127.0.0.1:8067/"):
    out_dir = Path("screenshots")
    out_dir.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-web-security", "--disable-features=VizDisplayCompositor"]
        )
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_load_state("networkidle")

        for w, h, name in VIEWPORTS:
            await page.set_viewport_size({"width": w, "height": h})
            # Give layout time to recompute based on JS
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(out_dir / f"autoyou_{name}.png"), full_page=True)

        # Also scroll to last item and capture footer visibility
        try:
            # Click the last dot if present
            last_dot = page.locator("#dot-nav button").last
            if await last_dot.count() > 0:
                await last_dot.click()
                await page.wait_for_timeout(500)
                await page.screenshot(path=str(out_dir / "autoyou_last_item.png"), full_page=True)
        except Exception:
            pass

        await browser.close()

async def main():
    await capture_screenshots()

if __name__ == "__main__":
    asyncio.run(main())
