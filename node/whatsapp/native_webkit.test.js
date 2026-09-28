import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { WebKitBrowser, webKitClient } from './native_webkit.js';
import pkg from 'whatsapp-web.js';

const executable = process.env.AUTOYOU_WEBKIT_TEST_EXECUTABLE;

test('native WebKit transports promises, page callbacks and navigation', { skip: !executable }, async () => {
    const server = http.createServer((_, response) => response.end('<html><title>AutoYou</title><p>synthetic page</p></html>'));
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const url = `http://127.0.0.1:${server.address().port}`;
    const browser = await WebKitBrowser.launch(executable, { bindingOrigins: [url] });
    try {
        const page = browser.page;
        await page.goto(url);
        await page.exposeFunction('autoyouTest', async value => ({ result: value + 1 }));
        assert.deepEqual(await page.evaluate(() => window.autoyouTest(41)), { result: 42 });
        const handle = await page.waitForFunction(() => ({ ready: document.readyState === 'complete' }));
        assert.deepEqual(await handle.jsonValue(), { ready: true });
        await page.reload();
        assert.deepEqual(await page.evaluate(() => window.autoyouTest(9)), { result: 10 });
        await page.exposeFunction('autoyouError', () => { throw new Error('synthetic failure'); });
        await assert.rejects(page.evaluate(() => window.autoyouError()), /synthetic failure/);
        await page.goto('about:blank');
        assert.equal(await page.evaluate(() => typeof window.autoyouTest), 'undefined');
    } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
});

test('WhatsApp produces its QR with local WebKit and no linked account', {
    skip: !executable || process.env.AUTOYOU_TEST_WHATSAPP_WEB !== '1', timeout: 90000,
}, async () => {
    const Client = webKitClient(pkg.Client, executable);
    const client = new Client({ userAgent: false, browserName: 'Safari', authTimeoutMs: 60000,
        webVersionCache: { type: 'none' }, puppeteer: { headless: true } });
    let timer;
    try {
        const qr = new Promise((resolve, reject) => {
            client.once('qr', value => resolve(typeof value === 'string' && value.length > 20));
            client.once('auth_failure', () => reject(new Error('WhatsApp initialization failed')));
            timer = setTimeout(() => reject(new Error('WhatsApp QR timed out')), 80000);
        });
        assert.equal(await Promise.race([qr, client.initialize().then(() => qr)]), true);
    } finally { clearTimeout(timer); await client.destroy(); }
});
