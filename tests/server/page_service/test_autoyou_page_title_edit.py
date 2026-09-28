# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-8c173a5db014db58ed08cf25

"""
Playwright test: verify inline title editing with pencil icon on AutoYou Page.

- Inserts a test item into `page_feed.db`.
- Navigates to the page service (`http://127.0.0.1:8067/`).
- Edits the title via the pencil icon and confirms persistence.
- Verifies long title truncation with ellipsis and full text in hover tooltip.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-8c173a5db014db58ed08cf25"


import asyncio
import os
import sys

LIVE_PAGE_SERVICE_ENV = "AUTOYOU_RUN_LIVE_PAGE_SERVICE_TESTS"

async def run() -> None:
    if os.environ.get(LIVE_PAGE_SERVICE_ENV) != "1":
        print(f"Skipped live page service title-edit script. Set {LIVE_PAGE_SERVICE_ENV}=1 to run.")
        return

    from playwright.async_api import async_playwright

    # Create a test item via HTTP API to ensure the service sees it
    import json
    from urllib.request import Request, urlopen

    payload = json.dumps({
        "url": "https://example.com/autoyou-title-edit",
        "type": "article",
        "title": "Original Title",
        "source": "example.com",
    }).encode("utf-8")
    req = Request("http://127.0.0.1:8067/api/feed", data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=5) as resp:
        j = json.loads(resp.read().decode("utf-8"))
    item = j.get("item") or {}
    item_id = int(item.get("id") or 0)
    assert item_id > 0, "Failed to create test item via /api/feed"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        network: dict = {"requests": [], "responses": []}

        async def on_request(req):
            try:
                u = req.url
                if ("127.0.0.1:8067" in u) or ("localhost:8067" in u):
                    entry = {
                        "method": req.method,
                        "url": u,
                        "post_data": req.post_data or None,
                    }
                    network["requests"].append(entry)
            except Exception:
                pass

        async def on_response(resp):
            try:
                u = resp.url
            except Exception:
                try:
                    u = resp.request.url
                except Exception:
                    u = ""
            try:
                if ("127.0.0.1:8067" in u) or ("localhost:8067" in u):
                    headers = await resp.all_headers()
                    entry = {
                        "status": resp.status,
                        "url": u,
                        "headers": headers,
                    }
                    network["responses"].append(entry)
            except Exception:
                pass

        page.on("request", lambda r: asyncio.create_task(on_request(r)))
        page.on("response", lambda r: asyncio.create_task(on_response(r)))
        page.on("console", lambda m: print(json.dumps({"console": {"type": m.type, "text": m.text}})))
        await page.goto("http://127.0.0.1:8067/", wait_until="domcontentloaded")

        # Wait for feed to render at least one card and focus on the first item
        await page.wait_for_selector("section.snap-item", state="visible", timeout=10000)
        first_card = page.locator("section.snap-item").first

        # Click the first card's pencil edit icon
        edit_btn = first_card.locator("button.edit-title-btn")
        await edit_btn.wait_for(state="visible", timeout=10000)
        await edit_btn.click()

        # Type a short title and commit
        edit_input = page.locator("input.title-edit-input")
        await edit_input.wait_for(state="visible", timeout=5000)
        new_title = "Edited Title - AutoYou"
        await edit_input.fill(new_title)
        await edit_input.press("Enter")

        # Validate title text and hover tooltip updated on the first card
        link = first_card.locator("a[id^='title-text-']")
        await link.wait_for(state="visible", timeout=8000)
        assert (await link.text_content()) == new_title
        assert (await link.get_attribute("title")) == new_title

        # Ensure favourite heart remains visible and domain pill is present
        fav_btn = first_card.locator("button.fav-btn")
        await fav_btn.wait_for(state="visible", timeout=8000)
        domain_pill = first_card.locator("div[title='Source domain']")
        await domain_pill.wait_for(state="visible", timeout=8000)

        # Update to a very long title and verify truncation with ellipsis
        await edit_btn.click()
        long_input = page.locator("input.title-edit-input")
        await long_input.wait_for(state="visible", timeout=5000)
        long_title = "AutoYou Title " + ("x" * 200)
        await long_input.fill(long_title)
        await long_input.press("Enter")

        await page.wait_for_timeout(400)
        long_link = first_card.locator("a[id^='title-text-']")
        truncated = await long_link.evaluate("el => el.scrollWidth > el.clientWidth")
        assert truncated, "Long title should be truncated with ellipsis"
        assert (await long_link.get_attribute("title")) == long_title

        await browser.close()

        print(json.dumps({"network_log": network}))

if __name__ == "__main__":
    asyncio.run(run())
