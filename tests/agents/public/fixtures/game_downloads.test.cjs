'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const {webcrypto, createHash} = require('node:crypto');
const {verifiedDownload, boundedBytes} = require('../../../../autoyou_agents/game_agent/website/frontend/assets/game_downloads.js');
const bytes = Uint8Array.from([1, 2, 3, 4]);
const entry = {id: 'unreal-plugin', filename: 'AutoYouGameInput.zip', bytes: bytes.length,
  sha256: createHash('sha256').update(bytes).digest('hex')};
function response(parts = [bytes], header = entry.sha256, type = 'application/zip') {
  let cancelled = false, released = false, reads = 0;
  const reader = {async read() { reads++; return parts.length ? {value: parts.shift(), done: false} : {done: true}; },
    async cancel() { cancelled = true; }, releaseLock() { released = true; }};
  const value = {ok: true, headers: new Headers({'X-AutoYou-Game-SHA256': header, 'Content-Type': type}),
    body: {getReader() { return reader; }, async cancel() { cancelled = true; }}};
  return {value, reader, state: () => ({cancelled, released, reads})};
}
test('exact bytes verified with the real SHA-256 implementation', async () => {
  const r = response([bytes.slice(0, 1), bytes.slice(1)]);
  assert.deepEqual(await verifiedDownload(r.value, entry, webcrypto), bytes);
  assert.equal(r.state().released, true);
});
test('tampered body is denied', async () => {
  await assert.rejects(verifiedDownload(response([Uint8Array.from([1,2,3,5])]).value, entry, webcrypto), /changed/);
});
test('wrong header is denied before opening the body', async () => {
  const r = response([bytes], '0'.repeat(64));
  await assert.rejects(verifiedDownload(r.value, entry, webcrypto), /verify/);
  assert.deepEqual(r.state(), {cancelled: true, released: false, reads: 0});
});
test('oversized chunk cancels and joins the reader', async () => {
  const r = response([new Uint8Array(5)]);
  await assert.rejects(verifiedDownload(r.value, entry, webcrypto), /size/);
  assert.deepEqual(r.state(), {cancelled: true, released: true, reads: 1});
});
test('truncation is denied after releasing the reader', async () => {
  const r = response([bytes.slice(0,2)]);
  await assert.rejects(verifiedDownload(r.value, entry, webcrypto), /incomplete/);
  assert.equal(r.state().released, true);
});
test('invalid metadata and unavailable crypto never become downloads', async () => {
  for (const change of [{bytes: 2*1024*1024+1}, {filename: '../synthetic.zip'}, {sha256: 'bad'}]) {
    await assert.rejects(verifiedDownload(response().value, {...entry, ...change}, webcrypto), /verify/);
  }
  await assert.rejects(verifiedDownload(response().value, entry, {}), /verify/);
  await assert.rejects(verifiedDownload(response([bytes],entry.sha256,'text/html').value, entry, webcrypto), /Invalid/);
});
test('cancellation while awaiting a chunk joins and releases the reader', async () => {
  const controller = new AbortController(), r = response();
  r.reader.read = async () => { controller.abort(); return {value: bytes, done: false}; };
  await assert.rejects(verifiedDownload(r.value, entry, webcrypto, controller.signal), /cancelled/);
  assert.equal(r.state().cancelled, true); assert.equal(r.state().released, true);
});
test('cancellation during hash cannot publish returned bytes', async () => {
  const controller = new AbortController();
  const crypto = {subtle: {async digest(...args) { controller.abort(); return webcrypto.subtle.digest(...args); }}};
  await assert.rejects(verifiedDownload(response().value, entry, crypto, controller.signal), /cancelled/);
});
test('catalog reader rejects excess bytes and preserves a bounded exact response', async () => {
  const r = response([new Uint8Array(4097)]);
  await assert.rejects(boundedBytes(r.value, 4096), /size/);
  assert.equal(r.state().cancelled, true);
  assert.deepEqual(await boundedBytes(response().value,4096), bytes);
});
