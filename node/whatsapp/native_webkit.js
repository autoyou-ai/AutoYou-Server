// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-f4195cb5fd4e3b9ca4fba915

// Public WebKit transport for whatsapp-web.js. Messaging/injection remains in
// the pinned library; only its Chromium launch and Chrome HTML cache change.
import { spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export class WebKitBrowser {
    constructor(executable) {
        this.pending = new Map();
        this.sequence = 0;
        this.connected = true;
        this.page = new WebKitPage(this);
        this.child = spawn(executable, [], { stdio: ['pipe', 'pipe', 'pipe'] });
        // WebKit diagnostics stay off the JSON channel and do not include page
        // messages, QR credentials or account identifiers in service logs.
        this.child.stderr.resume();
        this.lines = createInterface({ input: this.child.stdout });
        this.lines.on('line', line => {
            let message;
            try { message = JSON.parse(line); } catch { return; }
            if (message.event === 'binding') {
                Promise.resolve().then(() => {
                    const fn = this.page.bindings.get(message.name);
                    if (!fn) throw new Error('Unknown page binding');
                    return fn(...message.args);
                }).then(value => this.call('bindingResult', { call: message.call, value: value ?? null }),
                    error => this.call('bindingResult', { call: message.call, error: String(error) }))
                    .catch(() => {});
            } else if (message.event === 'navigation') {
                this.page.currentURL = message.url;
                this.page.emit('framenavigated', { url: () => message.url, parentFrame: () => null });
            } else if (message.event === 'closed') {
                this.fail(new Error('Browser content process stopped'));
                this.child.kill();
            } else {
                const request = this.pending.get(message.id);
                if (!request) return;
                clearTimeout(request.timer);
                this.pending.delete(message.id);
                if (message.error) request.reject(new Error(message.error));
                else request.resolve(message.result);
            }
        });
        this.child.on('error', error => this.fail(error));
        this.child.on('exit', () => this.fail(new Error('Browser closed')));
        this.child.stdin.on('error', error => this.fail(error));
    }

    fail(error) {
        this.connected = false;
        for (const request of this.pending.values()) { clearTimeout(request.timer); request.reject(error); }
        this.pending.clear();
        this.page.emit('close');
    }

    call(method, params = {}, timeout = 120000) {
        if (!this.connected) return Promise.reject(new Error('Browser closed'));
        const id = ++this.sequence;
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
                this.pending.delete(id);
                reject(new Error(`Runtime.callFunctionOn timed out (${method})`));
            }, timeout);
            this.pending.set(id, { resolve, reject, timer });
            this.child.stdin.write(JSON.stringify({ id, method, params }) + '\n');
        });
    }

    static async launch(executable, options = {}) {
        const browser = new WebKitBrowser(executable);
        try {
            let profile;
            if (options.userDataDir) {
                fs.mkdirSync(options.userDataDir, { recursive: true, mode: 0o700 });
                const file = path.join(options.userDataDir, 'webkit-profile.json');
                if (fs.existsSync(file)) profile = JSON.parse(fs.readFileSync(file, 'utf8')).id;
                else {
                    profile = randomUUID();
                    fs.writeFileSync(file, JSON.stringify({ id: profile }) + '\n', { flag: 'wx', mode: 0o600 });
                }
                if (!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(profile)) throw new Error('Invalid saved browser profile');
            }
            await browser.call('open', { profile, headless: options.headless !== false,
                bindingOrigins: options.bindingOrigins ?? ['https://web.whatsapp.com'] });
            return browser;
        } catch (error) { browser.child.kill(); throw error; }
    }

    isConnected() { return this.connected; }
    async pages() { return [this.page]; }
    async close() {
        try { if (this.connected) await this.call('close', {}, 5000); }
        finally {
            this.child.stdin.end();
            const timer = setTimeout(() => this.child.kill(), 3000);
            timer.unref();
            this.connected = false;
        }
    }
}

class WebKitPage extends EventEmitter {
    constructor(browser) { super(); this.browser = browser; this.bindings = new Map(); this.currentURL = 'about:blank'; }
    isClosed() { return !this.browser.isConnected(); }
    url() { return this.currentURL; }
    async evaluate(fn, ...args) {
        const source = typeof fn === 'function' ? `(${fn.toString()})(...args)` : `(${fn})`;
        const result = await this.browser.call('evaluate', {
            body: `try { const result = await (${source}); return JSON.stringify({value: result === undefined ? null : result}); } catch(error) { return JSON.stringify({error: String(error)}); }`,
            arguments: { args: args.map(value => value === undefined ? null : value) },
        });
        const response = JSON.parse(result);
        if (response.error) throw new Error(response.error);
        return response.value;
    }
    async goto(url, options = {}) {
        this.currentURL = await this.browser.call('goto', { url, referer: options.referer }, options.timeout || 120000);
    }
    async reload(options = {}) { await this.goto(this.currentURL, options); }
    async exposeFunction(name, fn) {
        this.bindings.set(name, fn);
        await this.browser.call('expose', { name });
    }
    async evaluateOnNewDocument(fn, ...args) {
        await this.browser.call('script', { source: typeof fn === 'function' ? `(${fn})(...${JSON.stringify(args)})` : fn });
    }
    async waitForFunction(fn, { timeout = 30000, signal } = {}, ...args) {
        const deadline = Date.now() + (timeout || 120000);
        do {
            signal?.throwIfAborted();
            if (this.isClosed()) throw new Error('Browser closed');
            const value = await this.evaluate(fn, ...args);
            if (value) return { jsonValue: async () => value, dispose: async () => {} };
            await new Promise(resolve => setTimeout(resolve, 100));
        } while (Date.now() < deadline);
        throw new Error('Waiting for page function timed out');
    }
    waitForNavigation({ timeout = 30000 } = {}) {
        return new Promise((resolve, reject) => {
            const finish = frame => { clearTimeout(timer); this.off('close', closed); resolve(frame); };
            const closed = () => { clearTimeout(timer); this.off('framenavigated', finish); reject(new Error('Browser closed')); };
            const timer = setTimeout(() => { this.off('framenavigated', finish); this.off('close', closed); reject(new Error('Navigation timed out')); }, timeout || 120000);
            this.once('framenavigated', finish); this.once('close', closed);
        });
    }
}

export function webKitClient(BaseClient, executable) {
    return class extends BaseClient {
        async initialize() {
            await this.authStrategy.beforeBrowserInitialized();
            this.pupBrowser = await WebKitBrowser.launch(executable, this.options.puppeteer);
            this.pupPage = this.pupBrowser.page;
            try {
                await this.authStrategy.afterBrowserInitialized();
                if (this.options.evalOnNewDoc !== undefined) await this.pupPage.evaluateOnNewDocument(this.options.evalOnNewDoc);
                await this.pupPage.goto('https://web.whatsapp.com/', { referer: 'https://whatsapp.com/' });
                this._registerFramenavigatedHandler();
                await this.inject();
            } catch (error) { await this.pupBrowser.close(); throw error; }
        }
        // Safari and Chromium receive different HTML. Keep WebKit's own HTTP
        // cache and the live WhatsApp version; never replay cached Chrome HTML.
        async initWebVersionCache() {}
    };
}
