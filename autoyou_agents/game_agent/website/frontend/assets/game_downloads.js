/* Verify a selected authenticated Game Studio download before saving it. */
(function (root) {
  'use strict';
  const maximumBytes = 2 * 1024 * 1024;
  const filenames = {'unreal-plugin': 'AutoYouGameInput.zip', 'python-adapter': 'game_input_client.py'};
  async function boundedBytes(response, limit, signal) {
    if (!response.body) throw new Error('Download is unavailable.');
    const reader = response.body.getReader();
    const data = new Uint8Array(limit);
    let offset = 0;
    try {
      while (true) {
        const part = await reader.read();
        if (signal && signal.aborted) throw new Error('Download cancelled.');
        if (part.done) break;
        if (!(part.value instanceof Uint8Array) || part.value.length > data.length - offset) {
          throw new Error('Game download exceeds its declared size.');
        }
        data.set(part.value, offset);
        offset += part.value.length;
      }
      return data.subarray(0, offset);
    } catch (error) {
      await reader.cancel().catch(() => {});
      throw error;
    } finally { reader.releaseLock(); }
  }
  async function verifiedDownload(response, entry, crypto, signal) {
    const current = () => { if (signal && signal.aborted) throw new Error('Download cancelled.'); };
    current();
    if (!entry || filenames[entry.id] !== entry.filename || !Number.isSafeInteger(entry.bytes) ||
        entry.bytes < 1 || entry.bytes > maximumBytes || !/^[0-9a-f]{64}$/.test(entry.sha256 || '') ||
        !crypto || !crypto.subtle || !response.ok || !response.body ||
        response.headers.get('X-AutoYou-Game-SHA256') !== entry.sha256) {
      await response.body?.cancel().catch(() => {});
      throw new Error('Could not verify the game download. Try again.');
    }
    const type = (response.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
    if (type !== (entry.id === 'unreal-plugin' ? 'application/zip' : 'text/x-python')) {
      await response.body.cancel().catch(() => {});
      throw new Error('Invalid game download.');
    }
    const data = await boundedBytes(response, entry.bytes, signal);
    if (data.length !== entry.bytes) throw new Error('Game download is incomplete.');
    const digest = await crypto.subtle.digest('SHA-256', data);
    current();
    const actual = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
    if (actual !== entry.sha256) throw new Error('Game download changed. Try again.');
    return data;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {verifiedDownload, boundedBytes};
  else root.AutoYouGameDownloads = {verifiedDownload, boundedBytes};
}(typeof window === 'undefined' ? globalThis : window));
