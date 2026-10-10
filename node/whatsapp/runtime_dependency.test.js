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
