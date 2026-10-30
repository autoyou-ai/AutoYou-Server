// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-I-or-af1867f7b1363935e691a6de

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

test('comma query serialization handles shadowed coercion methods', () => {
    for (const encodeValuesOnly of [false, true]) {
        assert.doesNotThrow(() => qs.stringify({ a: [{ toString: 'synthetic' }] }, {
            arrayFormat: 'comma', encodeValuesOnly,
        }));
    }
    assert.equal(qs.stringify({ a: [1, 2] }, { arrayFormat: 'comma' }), 'a=1%2C2');
});

test('TOML rejects oversized integers and retains ordinary configuration', () => {
    execFileSync(process.execPath, ['-e', `
        const assert = require('node:assert/strict');
        const toml = require('toml');
        assert.throws(() => toml.parse('value = ' + '9'.repeat(250000)));
        const normal = toml.parse('port = 8022\\nvalues = [1, 2]');
        assert.equal(normal.port, 8022);
        assert.deepEqual(normal.values, [1, 2]);
    `], { cwd: __dirname, timeout: 5000 });
});

test('TOML resolves long dotted paths within a bound', () => {
    execFileSync(process.execPath, ['-e', `
        const assert = require('node:assert/strict');
        const toml = require('toml');
        const depth = 12000;
        let value = toml.parse('a.'.repeat(depth) + 'value = 1');
        for (let index = 0; index < depth; index++) value = value.a;
        assert.equal(value.value, 1);
    `], { cwd: __dirname, timeout: 5000 });
});

test('TOML bounds bookkeeping for distinct array-of-table names', () => {
    execFileSync(process.execPath, ['-e', `
        const assert = require('node:assert/strict');
        const toml = require('toml');
        const count = 30000;
        const input = Array.from({ length: count }, (_, index) =>
            '[[entry' + index + ']]\\nvalue = 1\\n').join('');
        const tables = toml.parse(input);
        assert.equal(Object.keys(tables).length, count);
        assert.equal(tables['entry' + (count - 1)][0].value, 1);
    `], { cwd: __dirname, timeout: 5000 });
});

test('a pending upload filter cannot crash a process after parser teardown', () => {
    execFileSync(process.execPath, ['-e', `
        const assert = require('node:assert/strict');
        const { PassThrough } = require('node:stream');
        const multer = require('multer');
        const request = new PassThrough();
        const boundary = 'SyntheticTeardownBoundary';
        const part = name => '--' + boundary + '\\r\\nContent-Disposition: form-data; name="' + name +
            '"; filename="synthetic.txt"\\r\\nContent-Type: text/plain\\r\\n\\r\\nx';
        const body = part('first') + '\\r\\n' + part('second');
        request.headers = { 'content-type': 'multipart/form-data; boundary=' + boundary,
            'content-length': Buffer.byteLength(body) };
        let resume;
        let calls = 0;
        const rejected = new Error('synthetic rejection');
        const upload = multer({ fileHwm: 1, fileFilter(req, file, done) {
            if (file.fieldname === 'first') resume = done;
            else req.once('end', () => setImmediate(() => done(rejected)));
        } }).any();
        upload(request, {}, error => {
            assert.equal(error, rejected);
            assert.equal(++calls, 1);
            assert.equal(typeof resume, 'function');
            setImmediate(() => {
                resume(null, true);
                setTimeout(() => { assert.equal(calls, 1); process.exit(0); }, 40);
            });
        });
        request.end(body);
        setTimeout(() => process.exit(2), 1500);
    `], { cwd: __dirname, timeout: 3000 });
});

test('compressed JSON with trailing garbage cannot crash the HTTP parser', async () => {
    const express = require('express');
    const { gzipSync } = require('node:zlib');
    const app = express();
    app.use(express.json());
    app.post('/', (req, res) => res.json(req.body));
    app.use((error, _req, res, _next) => res.status(error.status || 400).end('Rejected'));
    const server = app.listen(0, '127.0.0.1');
    await new Promise(resolve => server.once('listening', resolve));
    try {
        const url = `http://127.0.0.1:${server.address().port}`;
        const valid = gzipSync(Buffer.from('{"name":"synthetic"}'));
        const rejected = await fetch(url, { method: 'POST', headers: {
            'Content-Type': 'application/json', 'Content-Encoding': 'gzip',
        }, body: Buffer.concat([valid, Buffer.from('invalid-trailing-bytes')]) });
        assert.equal(rejected.status, 400);
        const accepted = await fetch(url, { method: 'POST', headers: {
            'Content-Type': 'application/json', 'Content-Encoding': 'gzip',
        }, body: valid });
        assert.equal(accepted.status, 200);
        assert.deepEqual(await accepted.json(), { name: 'synthetic' });
    } finally {
        await new Promise(resolve => server.close(resolve));
    }
});

test('multipart input rejects excessive field nesting and still accepts ordinary fields', async () => {
    const { createServer } = require('node:http');
    const multer = require('multer');
    const upload = multer({ limits: { fieldNestingDepth: 8 } }).none();
    const server = createServer((req, res) => upload(req, res, (error) => {
        res.writeHead(error ? 400 : 200, { 'Content-Type': 'text/plain; charset=utf-8' });
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

test('deepmerge drops own __proto__ keys and still merges ordinary configuration', () => {
    const deepmerge = require('deepmerge');
    const merged = deepmerge(
        { hostip: { endpoint: 'wss://a' }, list: [1] },
        { hostip: { port: 443 }, list: [2] },
    );
    assert.deepEqual(merged, { hostip: { endpoint: 'wss://a', port: 443 }, list: [1, 2] });
    const hostile = JSON.parse('{"__proto__": {"polluted": true}, "nested": {"__proto__": {"polluted": true}}}');
    for (const result of [deepmerge({}, hostile), deepmerge(hostile, {}), deepmerge({ nested: {} }, hostile)]) {
        assert.equal(Object.getPrototypeOf(result), Object.prototype);
        assert.equal(result.polluted, undefined);
        assert.equal(result.nested.polluted, undefined);
    }
    assert.equal({}.polluted, undefined);
});
