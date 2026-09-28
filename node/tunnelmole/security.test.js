// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-O-68747470733a2f2f6275792e-fee64470c8491a71c3ad9bdb

const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const test = require('node:test');
const toml = require('toml');
const qs = require('qs');

test('patched parsers bound hostile input and retain ordinary configuration', () => {
    assert.equal(toml.parse('port = 8022').port, 8022);
    assert.throws(() => toml.parse('x = ' + '['.repeat(600) + '0' + ']'.repeat(600)), /Maximum nesting depth/);
    assert.equal(Object.prototype.polluted, undefined);
    try { toml.parse('[__proto__]\npolluted = true'); } catch {}
    assert.equal(Object.prototype.polluted, undefined);
    assert.equal(qs.parse('name=synthetic').name, 'synthetic');
    assert.throws(() => qs.parse('a[]=1,2,3', {
        comma: true, arrayLimit: 2, throwOnLimitExceeded: true,
    }), /Array limit exceeded/);
    // Bound the historical zero-size generator hang outside the test process.
    execFileSync(process.execPath, ['-e', `
        const assert = require('node:assert/strict');
        const { nanoid, customAlphabet } = require('nanoid');
        assert.equal(customAlphabet('abc', 0)(), '');
        assert.equal(nanoid().length, 21);
    `], { cwd: __dirname, timeout: 2000 });
});

test('Tunnelmole retains the package entrypoints used by the local launcher', () => {
    const fs = require('node:fs');
    const path = require('node:path');
    const root = path.dirname(require.resolve('tunnelmole/package.json'));
    for (const file of ['dist/config.js', 'dist/src/index.js']) {
        assert.ok(fs.existsSync(path.join(root, file)), file);
    }
    assert.equal(typeof require('multer')().single, 'function');
});

test('multipart input rejects excessive field nesting and still accepts ordinary fields', async () => {
    const { createServer } = require('node:http');
    const multer = require('multer');
    const upload = multer({ limits: { fieldNestingDepth: 8 } }).none();
    const server = createServer((req, res) => upload(req, res, (error) => {
        res.writeHead(error ? 400 : 200);
        res.end(error ? error.code : req.body.name);
    }));
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    try {
        const url = `http://127.0.0.1:${server.address().port}`;
        const malformed = new FormData();
        malformed.set('x' + '[y]'.repeat(100), 'synthetic');
        const rejected = await fetch(url, { method: 'POST', body: malformed });
        assert.equal(rejected.status, 400);
        assert.equal(await rejected.text(), 'LIMIT_FIELD_NESTING');
        const valid = new FormData();
        valid.set('name', 'synthetic');
        const accepted = await fetch(url, { method: 'POST', body: valid });
        assert.equal(accepted.status, 200);
        assert.equal(await accepted.text(), 'synthetic');
    } finally {
        await new Promise((resolve) => server.close(resolve));
    }
});
