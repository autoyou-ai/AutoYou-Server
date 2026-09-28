// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import puppeteer from 'puppeteer';
import pkg from 'whatsapp-web.js';

test('pinned WhatsApp destroy closes the upgraded browser', {
    skip: !process.env.AUTOYOU_TEST_CHROMIUM_PATH && 'Set AUTOYOU_TEST_CHROMIUM_PATH for the isolated browser check',
}, async () => {
    const profile = await mkdtemp(path.join(os.tmpdir(), 'autoyou-browser-test-'));
    let browser;
    try {
        browser = await puppeteer.launch({ executablePath: process.env.AUTOYOU_TEST_CHROMIUM_PATH,
            headless: true, userDataDir: profile });
        assert.equal(browser.isConnected(), true);
        const page = await browser.newPage();
        await page.setContent('<title>Synthetic browser check</title>');
        assert.equal(await page.title(), 'Synthetic browser check');
        let destroyed = false;
        await pkg.Client.prototype.destroy.call({ pupBrowser: browser,
            authStrategy: { destroy: async () => { destroyed = true; } } });
        assert.equal(browser.connected, false);
        assert.equal(destroyed, true);
    } finally {
        await browser?.close();
        await rm(profile, { recursive: true, force: true });
    }
});

test('pinned WhatsApp logout completes its auth cleanup with the new connection API', async () => {
    class TestBrowser { isConnected() { return false; } async close() {} }
    let loggedOut = false;
    await pkg.Client.prototype.logout.call({ pupBrowser: new TestBrowser(),
        pupPage: { isClosed: () => false, evaluate: async () => {} },
        authStrategy: { logout: async () => { loggedOut = true; } } });
    assert.equal(loggedOut, true);
});
