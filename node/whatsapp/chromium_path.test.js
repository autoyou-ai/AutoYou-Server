// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-M-of-8047a999514474c0d1d038a7

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
    allowCustomBrowserEnabled,
    resolveChromiumExecutable,
} from './chromium_path.js';


function makeTempDir(t) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'autoyou-whatsapp-chromium-'));
    t.after(() => {
        fs.rmSync(dir, { recursive: true, force: true });
    });
    return dir;
}


test('allowCustomBrowserEnabled parses truthy developer overrides', () => {
    assert.equal(allowCustomBrowserEnabled('true'), true);
    assert.equal(allowCustomBrowserEnabled(' YES '), true);
    assert.equal(allowCustomBrowserEnabled('off'), false);
});


test('resolveChromiumExecutable rejects explicit executables with unexpected basenames', (t) => {
    const dir = makeTempDir(t);
    const disallowed = path.join(dir, 'powershell.exe');
    fs.writeFileSync(disallowed, '');

    const warnings = [];
    const resolved = resolveChromiumExecutable(disallowed, {
        allowCustomBrowser: false,
        warn: (message) => warnings.push(message),
        error: () => {},
    });

    assert.equal(resolved, undefined);
    assert.equal(warnings.length, 1);
    assert.match(warnings[0], /Refusing to launch PUPPETEER_EXECUTABLE_PATH/);
});


test('resolveChromiumExecutable accepts explicit Chromium-family executables', (t) => {
    const dir = makeTempDir(t);
    const chrome = path.join(dir, 'chrome.exe');
    fs.writeFileSync(chrome, '');

    const resolved = resolveChromiumExecutable(chrome, {
        allowCustomBrowser: false,
        warn: () => {},
        error: () => {},
    });

    assert.equal(resolved, path.resolve(chrome));
});


test('resolveChromiumExecutable finds allowed binaries inside Chromium bundle directories', (t) => {
    const dir = makeTempDir(t);
    const bundleChrome = path.join(dir, 'chromium-123456', 'chrome-win', 'chrome.exe');
    fs.mkdirSync(path.dirname(bundleChrome), { recursive: true });
    fs.writeFileSync(bundleChrome, '');

    const resolved = resolveChromiumExecutable(dir, {
        allowCustomBrowser: false,
        warn: () => {},
        error: () => {},
    });

    assert.equal(resolved, bundleChrome);
});


test('resolveChromiumExecutable honors the developer override for custom browsers', (t) => {
    const dir = makeTempDir(t);
    const customBrowser = path.join(dir, 'my-browser.exe');
    fs.writeFileSync(customBrowser, '');

    const resolved = resolveChromiumExecutable(customBrowser, {
        allowCustomBrowser: true,
        warn: () => {},
        error: () => {},
    });

    assert.equal(resolved, path.resolve(customBrowser));
});
