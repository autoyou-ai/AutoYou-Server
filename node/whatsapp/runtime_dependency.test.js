import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { Client, parseList } from 'basic-ftp';

test('patched FTP dependency retains listing and client cleanup APIs', () => {
    const rows = parseList('-rw-r--r-- 1 fixture fixture 7 Jan 01 2026 synthetic.txt\r\n');
    assert.equal(rows.length, 1);
    assert.equal(rows[0].name, 'synthetic.txt');
    assert.equal(rows[0].size, 7);
    const client = new Client();
    client.close();
    assert.equal(client.closed, true);
});

test('malformed directory listing cannot pin the parser process', () => {
    const code = `
        import { parseList } from 'basic-ftp';
        const malformed = '-rw-r--r-- 1 ' + 'synthetic '.repeat(16000) + 'invalid';
        const valid = '-rw-r--r-- 1 fixture fixture 7 Jan 01 2026 synthetic.txt';
        const rows = parseList(malformed + '\\r\\n' + valid + '\\r\\n');
        if (rows.length !== 1 || rows[0].name !== 'synthetic.txt') process.exit(1);
    `;
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', code], {
        cwd: fileURLToPath(new URL('.', import.meta.url)), timeout: 5000, encoding: 'utf8',
    });
    assert.equal(child.error, undefined, child.error?.message);
    assert.equal(child.status, 0, child.stderr);
});

test('IPv6 zone rejection is bounded and retains SOCKS byte-array conversion', () => {
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', `
        import assert from 'node:assert/strict';
        import { Address6 } from 'ip-address';
        for (const suffix of ['\\n', '\\r']) {
            const value = '%'.repeat(100000) + suffix;
            assert.equal(Address6.isValid(value), false);
            assert.throws(() => new Address6(value));
        }
        assert.equal(new Address6('2001:db8::1').correctForm(), '2001:db8::1');
        assert.equal(Address6.fromByteArray([0x20, 1, 0x0d, 0xb8, 0, 0, 0, 0,
            0, 0, 0, 0, 0, 0, 0, 1]).correctForm(), '2001:db8::1');
    `], { cwd: fileURLToPath(new URL('.', import.meta.url)), timeout: 2000, encoding: 'utf8' });
    assert.equal(child.error, undefined, child.error?.message);
    assert.equal(child.status, 0, child.stderr);
});
