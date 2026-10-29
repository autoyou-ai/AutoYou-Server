// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization; see LICENSE.

// Render Neon Horizon's soundtrack to the loop the computer streams to players of the hosted game.
// The synthesis is the game's own (between music:begin and music:end in play.html), so the streamed
// music matches what the phones play locally: a calmer pass, then the full arrangement.
// Usage: node scripts/render_neon_horizon_music.cjs

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const page = path.join(root, 'autoyou_agents/game_agent/website/frontend/play.html');
const output = path.join(root, 'autoyou_agents/game_agent/website/frontend/assets/audio/stream-loop.wav');
const html = fs.readFileSync(page, 'utf8');
const begin = html.indexOf('/* music:begin'), end = html.indexOf('/* music:end */');
if (begin < 0 || end < 0) throw new Error('play.html has no music:begin / music:end block');
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(html.slice(begin, end) + '\nthis.api = {renderStem, stemLevels, MUSIC_STEMS};', sandbox);
const {renderStem, stemLevels, MUSIC_STEMS} = sandbox.api;

const rate = 16000;
const stems = Object.fromEntries(MUSIC_STEMS.map(name => [name, renderStem(name, rate)]));
const loop = stems.kick.length;
const passes = [stemLevels(0.42, 0), stemLevels(1, 0.25)];
const mix = new Float32Array(loop * passes.length);
passes.forEach((levels, pass) => {
  for (const name of MUSIC_STEMS) {
    const data = stems[name], level = levels[name];
    for (let i = 0; i < loop; i++) mix[pass * loop + i] += data[i] * level;
  }
});
const bytes = Buffer.alloc(44 + mix.length * 2);
bytes.write('RIFF', 0, 'ascii'); bytes.writeUInt32LE(bytes.length - 8, 4); bytes.write('WAVE', 8, 'ascii');
bytes.write('fmt ', 12, 'ascii'); bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20); bytes.writeUInt16LE(1, 22);
bytes.writeUInt32LE(rate, 24); bytes.writeUInt32LE(rate * 2, 28); bytes.writeUInt16LE(2, 32); bytes.writeUInt16LE(16, 34);
bytes.write('data', 36, 'ascii'); bytes.writeUInt32LE(mix.length * 2, 40);
for (let i = 0; i < mix.length; i++) bytes.writeInt16LE(Math.round(Math.tanh(mix[i] * 0.75) * 30000), 44 + i * 2);
fs.writeFileSync(output, bytes);
console.log(`Updated ${path.relative(root, output)} (${(mix.length / rate).toFixed(1)} s)`);
