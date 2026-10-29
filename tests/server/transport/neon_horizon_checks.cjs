const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const html = fs.readFileSync(process.argv[2], 'utf8');
const source = html.match(/<script\b[^>]*>([\s\S]*?)<\/script[^>]*>/i)[1];

function game(options = {}) {
  let clock = 0;
  const elements = new Map(), storage = options.storage || new Map();
  const gradient = {addColorStop() {}};
  const context = new Proxy({}, {get: (_, name) => name.startsWith('create') ? () => gradient : () => {}});
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', hidden: false, style: {}, dataset: {}, events: new Map(),
      addEventListener(name, callback) {
        if (!this.events.has(name)) this.events.set(name, []);
        this.events.get(name).push(callback);
      },
      setAttribute() {}, setPointerCapture() {}, getContext: () => context,
    });
    return elements.get(id);
  }
  const controls = ['left', 'jump', 'right'].map(name => {
    const button = element(name); button.dataset.action = name; return button;
  });
  const document = element('document');
  let created = 0;
  Object.assign(document, {
    querySelector: element, querySelectorAll: () => controls,
    createElement: () => element(`created-${created++}`),
    documentElement: element('html'),
    body: {hasAttribute: () => html.includes('<body data-offline>')},
  });
  const window = element('window');
  Object.assign(window, options.native || {});
  const sandbox = {
    document, window, innerWidth: 420, innerHeight: 840, devicePixelRatio: 1,
    location: {pathname: '/horizon.html'}, performance: {now: () => clock},
    localStorage: {
      getItem(key) { if (options.blockStorage) throw Error('denied'); return storage.get(key) ?? null; },
      setItem(key, value) { if (options.blockStorage) throw Error('denied'); storage.set(key, value); },
    },
    Audio: class {play() {return Promise.resolve();}},
    btoa: text => Buffer.from(text, 'binary').toString('base64'),
    requestAnimationFrame() {}, setTimeout() {}, clearTimeout() {},
    addEventListener: window.addEventListener.bind(window),
  };
  vm.createContext(sandbox);
  // The inspection hook exists only in the VM copy, never in a shipped page.
  vm.runInContext(source.replace('    best = loadBest();', '    globalThis.inspect = text => eval(text);\n    best = loadBest();'), sandbox);
  const run = sandbox.inspect;
  const emit = (id, name, values = {}) => {
    const target = element(id);
    for (const callback of target.events.get(name) || []) callback({
      preventDefault() {}, pointerId: 1, clientX: 200, clientY: 500,
      currentTarget: target, ...values,
    });
  };
  return {
    run, emit, element, storage, window, sandbox,
    start() {run('start(); spawnTime = 10000');},
    step(dt) {clock += dt * 1000; run(`update(${dt})`);},
    sensor(vector, seconds = 0.033) {clock += seconds * 1000; window.autoYouGameTilt(...vector);},
    remoteFrame(frame, seconds = 0.033) {
      clock += seconds * 1000;
      run(`handleRemoteFrame(${JSON.stringify({event: 'game_input', session_id: 'synthetic-player', ...frame})})`);
    },
    remote(points) {run(`handleRemoteFrame(${JSON.stringify({event: 'game_input', input_type: 'touch', session_id: 'synthetic-player', points})})`);},
  };
}

test('the game keeps an explicit viewport height through resize and rotation', () => {
  const g = game();
  for (const [width, height, dpr] of [[420, 840, 1], [360, 720, 2], [720, 360, 3]]) {
    Object.assign(g.sandbox, {innerWidth: width, innerHeight: height, devicePixelRatio: dpr});
    g.emit('window', 'resize');
    assert.equal(g.element('html').style.height, `${height}px`);
    assert.equal(g.element('#game').width, width * Math.min(dpr, 2));
    assert.equal(g.element('#game').height, height * Math.min(dpr, 2));
    assert.equal(g.run('width'), width);
    assert.equal(g.run('height'), height);
  }
});

test('a zero-width initial layout cannot hang the render loop', () => {
  const g = game();
  Object.assign(g.sandbox, {innerWidth: 0, innerHeight: 0});
  g.emit('window', 'resize');
  vm.runInContext('inspect("frame(0)")', g.sandbox, {timeout: 1000});
});

function impact(g, kind, extra = '') {
  g.run(`objects = [{kind: '${kind}', lane: 0, z: 0.898, passed: false ${extra}}];`);
  g.step(0.02);
}

test('a long swipe crosses exactly one lane, including reversals and release', () => {
  const g = game(); g.start(); g.run('lane = visualLane = -1');
  g.emit('#game', 'pointerdown', {clientX: 30});
  for (const clientX of [70, 170, 350, 80]) g.emit('#game', 'pointermove', {clientX});
  g.emit('#game', 'pointerup', {clientX: 400});
  assert.equal(g.run('lane'), 0);
  g.emit('#game', 'pointerdown', {clientX: 50});
  g.emit('#game', 'pointerup', {clientX: 390});
  assert.equal(g.run('lane'), 1);
});

test('left swipes are single-step; cancel and downward swipes do not tap', () => {
  const g = game(); g.start(); g.run('lane = visualLane = 1');
  g.emit('#game', 'pointerdown', {clientX: 390});
  for (const clientX of [330, 160, 30]) g.emit('#game', 'pointermove', {clientX});
  g.emit('#game', 'pointerup', {clientX: 30});
  assert.equal(g.run('lane'), 0);
  g.emit('#game', 'pointerdown'); g.emit('#game', 'pointercancel'); g.emit('#game', 'pointerup');
  assert.equal(g.run('jumpTime'), 0);
  g.emit('#game', 'pointerdown'); g.emit('#game', 'pointerup', {clientY: 700});
  assert.equal(g.run('jumpTime'), 0);
});

test('remote touch waits to distinguish tap from swipe and consumes the swipe', () => {
  const g = game(); g.start(); g.run('lane = visualLane = -1');
  g.remote([{id: 0, x: 0.1, y: 0.6}]); assert.equal(g.run('lane'), -1);
  for (const x of [0.25, 0.5, 0.85]) g.remote([{id: 0, x, y: 0.6}]);
  g.remote([]); assert.equal(g.run('lane'), 0);
  g.remote([{id: 1, x: 0.9, y: 0.6}]); g.remote([]);
  assert.equal(g.run('lane'), 1);
});

test('up swipe, tap, keyboard and held button each produce only their intended action', () => {
  const g = game(); g.start();
  g.emit('#game', 'pointerdown'); g.emit('#game', 'pointermove', {clientY: 430});
  g.run('jumpTime = 0');
  g.emit('#game', 'pointermove', {clientY: 320}); g.emit('#game', 'pointerup', {clientY: 280});
  assert.equal(g.run('jumpTime'), 0);
  g.emit('right', 'pointerdown');
  for (let i = 0; i < 30; i++) g.step(0.03);
  assert.equal(g.run('lane'), 1);
  g.emit('window', 'keydown', {key: 'ArrowLeft', repeat: false});
  g.emit('window', 'keydown', {key: 'ArrowLeft', repeat: true});
  assert.equal(g.run('lane'), 0);
  g.emit('#game', 'pointerdown'); g.emit('#game', 'pointerup');
  assert.ok(g.run('jumpTime') > 0);
});

test('hurdles require real height at collision, walls always require a dodge', () => {
  for (const remaining of [0, 0.81, 0.04]) {
    const g = game(); g.start(); g.run(`jumpTime = ${remaining}`); impact(g, 'hurdle');
    assert.equal(g.run('mode'), 'over', `remaining ${remaining} cannot clear a hurdle`);
  }
  const g = game(); g.start(); g.run('jumpTime = 0.45'); impact(g, 'hurdle');
  assert.equal(g.run('mode'), 'running'); assert.equal(g.run('clears'), 1); assert.ok(g.run('score') >= 50);
  impact(g, 'wall'); assert.equal(g.run('mode'), 'over');
});

test('collision follows the visible lane and the interpolated jump, not the target', () => {
  const g = game(); g.start(); g.run("action('right')"); impact(g, 'wall');
  assert.equal(g.run('mode'), 'over');
  const safe = game(); safe.start(); safe.run("action('right')");
  for (let i = 0; i < 12; i++) safe.step(1 / 60);
  impact(safe, 'wall'); assert.equal(safe.run('mode'), 'running');
});

test('diamonds reward streaks, airborne timing and an earned single-use shield', () => {
  const g = game(); g.start();
  for (let i = 0; i < 8; i++) impact(g, 'shard');
  assert.equal(g.run('diamonds'), 8); assert.equal(g.run('multiplier()'), 2);
  assert.equal(g.run('shield'), true); assert.equal(g.run('charge'), 0);
  impact(g, 'wall'); assert.equal(g.run('mode'), 'running'); assert.equal(g.run('shield'), false);
  assert.equal(g.run('streak'), 0);
  g.step(0.81); impact(g, 'wall'); assert.equal(g.run('mode'), 'over');
  const air = game(); air.start(); impact(air, 'shard', ', air: true');
  assert.equal(air.run('diamonds'), 0);
  air.run('jumpTime = 0.45'); impact(air, 'shard', ', air: true');
  assert.equal(air.run('diamonds'), 1); assert.ok(air.run('score') >= 60);
  impact(air, 'shard'); assert.equal(air.run('diamonds'), 1);
});

test('missing diamonds resets the streak but preserves shield charge', () => {
  const g = game(); g.start();
  for (let i = 0; i < 6; i++) impact(g, 'shard');
  g.run('lane = visualLane = 1'); impact(g, 'shard');
  assert.equal(g.run('multiplier()'), 1); assert.equal(g.run('charge'), 6);
});

test('best score survives reload, restart, backgrounding and a lower run', () => {
  const g = game(); g.start(); g.run('score = 1234.8'); g.emit('window', 'pagehide');
  const reloaded = game({storage: g.storage}); assert.equal(reloaded.run('best'), 1234);
  assert.match(reloaded.element('#best').textContent, /1234/);
  reloaded.start(); reloaded.run("score = 10; finish('test')");
  assert.equal(game({storage: g.storage}).run('best'), 1234);
  reloaded.start(); reloaded.run('score = 1500'); reloaded.emit('window', 'blur');
  assert.equal(reloaded.run('mode'), 'paused'); reloaded.step(2);
  assert.equal(reloaded.run('score'), 1500);
  assert.equal(game({storage: g.storage}).run('best'), 1500);
});

test('invalid or blocked storage cannot break the game; native records persist', () => {
  for (const value of ['NaN', '-3', '99999999999999999', '{}', '2.5']) {
    const g = game({storage: new Map([['autoyou.neon-horizon.best.v2', value]])});
    assert.equal(g.run('best'), 0); g.start(); g.step(0.01);
  }
  const memory = game({blockStorage: true}); memory.start(); memory.run("score = 70; finish('test')");
  assert.match(memory.element('#best').textContent, /SESSION/);
  let record = 80;
  const native = {AutoYouGameScores: {readBest: () => record, saveBest: value => { record = Math.max(record, value); }}};
  const android = game({blockStorage: true, native}); assert.equal(android.run('best'), 80);
  android.start(); android.run('score = 200; saveBest()');
  assert.equal(game({blockStorage: true, native}).run('best'), 200);
  const ios = game({blockStorage: true, native: {autoYouGameBest: 200, webkit: {messageHandlers: {gameScore: {postMessage: value => {record = value;}}}}}});
  ios.start(); ios.run('score = 300; saveBest()'); assert.equal(record, 300);
});

for (const gravity of [[0, 0, 9.80665], [0, 9.80665, 0], [9.80665, 0, 0], [0, 0, -9.80665], [0, -9.80665, 0], [0, 6.93435, 6.93435]]) {
  test(`lift jumps once after calibration in orientation ${gravity}`, () => {
    const g = game(); g.start();
    for (let i = 0; i < 24; i++) g.sensor(gravity);
    const lifted = gravity.map(value => value * 1.5);
    g.sensor(lifted); assert.equal(g.run('jumpTime'), 0);
    g.sensor(lifted); assert.ok(g.run('jumpTime') > 0);
    assert.equal(g.run('lane'), 0);
    g.run('jumpTime = 0');
    for (let i = 0; i < 40; i++) g.sensor(lifted);
    assert.equal(g.run('jumpTime'), 0);
    for (let i = 0; i < 30; i++) g.sensor(gravity);
    g.sensor(lifted); g.sensor(lifted); assert.ok(g.run('jumpTime') > 0);
  });
}

test('sensor noise, sideways motion, rotation, a downward dip and one spike do not jump', () => {
  const g = game(); g.start();
  for (let i = 0; i < 24; i++) g.sensor([0, 0, 9.80665]);
  for (let i = 0; i < 60; i++) g.sensor([Math.sin(i) * 0.1, 0, 9.80665 + Math.sin(i) * 0.3]);
  assert.equal(g.run('jumpTime'), 0);
  g.sensor([0, 0, 15]); g.sensor([0, 0, 9.80665]); assert.equal(g.run('jumpTime'), 0);
  g.sensor([0, 0, 6]); g.sensor([0, 0, 15]); g.sensor([0, 0, 15]); assert.equal(g.run('jumpTime'), 0);
  for (let i = 0; i < 30; i++) g.sensor([0, 0, 9.80665]);
  for (let i = 0; i < 10; i++) g.sensor([7, 0, 9.80665]);
  assert.equal(g.run('jumpTime'), 0);
  for (let i = 0; i <= 30; i++) g.sensor([0, Math.sin(i / 30 * Math.PI / 2) * 9.80665, Math.cos(i / 30 * Math.PI / 2) * 9.80665]);
  assert.equal(g.run('jumpTime'), 0);
});

test('lift toggle, stale samples and idle sensors cannot start or restart a run', () => {
  const g = game();
  for (let i = 0; i < 30; i++) g.sensor([0, 0, 10]);
  assert.equal(g.run('mode'), 'ready');
  g.start(); for (let i = 0; i < 24; i++) g.sensor([0, 0, 9.80665]);
  g.sensor([0, 0, 15], 2); g.sensor([0, 0, 15]);
  assert.equal(g.run('jumpTime'), 0);
  g.emit('#lift', 'click');
  for (let i = 0; i < 30; i++) g.sensor([0, 0, 9.80665]);
  g.sensor([0, 0, 15]); g.sensor([0, 0, 15]); assert.equal(g.run('jumpTime'), 0);
  g.run("finish('test')"); g.sensor([0, 0, 15]); assert.equal(g.run('mode'), 'over');
});

test('existing two-axis tilt remains edge-triggered and re-arms near neutral', () => {
  const g = game(); g.start(); g.window.autoYouGameTilt(0, 9.8);
  for (let i = 0; i < 20; i++) g.window.autoYouGameTilt(3, 9.8);
  assert.equal(g.run('lane'), 1);
  g.window.autoYouGameTilt(0, 9.8); g.window.autoYouGameTilt(-3, 9.8);
  assert.equal(g.run('lane'), 0);
});

test('hosted native sensors and controls use the same motion and single-step rules', () => {
  const g = game();
  g.remoteFrame({input_type: 'session_start'}); assert.equal(g.run('mode'), 'ready');
  g.remoteFrame({input_type: 'button', button: 'action_a', phase: 'down'});
  assert.equal(g.run('mode'), 'running');
  g.remoteFrame({input_type: 'button', button: 'action_a', phase: 'down'});
  assert.equal(g.run('jumpTime'), 0);
  g.remoteFrame({input_type: 'button', button: 'action_a', phase: 'up'});
  for (let i = 0; i < 24; i++) g.remoteFrame({input_type: 'sensor', sensor: 'accelerometer', x: 0, y: -9.80665, z: 0});
  for (let i = 0; i < 2; i++) g.remoteFrame({input_type: 'sensor', sensor: 'accelerometer', x: 0, y: -15, z: 0});
  assert.ok(g.run('jumpTime') > 0);
  g.run('lane = visualLane = -1');
  for (let i = 0; i < 15; i++) g.remoteFrame({input_type: 'axis', axis: 'left_x', value: 0.9});
  assert.equal(g.run('lane'), 0);
  g.remoteFrame({input_type: 'axis', axis: 'left_x', value: 0});
  g.remoteFrame({input_type: 'axis', axis: 'left_x', value: 0.9});
  assert.equal(g.run('lane'), 1);
  g.run('pause()');
  g.remoteFrame({input_type: 'button', button: 'action_a', phase: 'down'});
  assert.equal(g.run('mode'), 'running');
  g.remoteFrame({input_type: 'state_reset', all_sessions: true});
  assert.equal(g.run('remotePlayers.size'), 0);
});

test('patterns offer a clear or jumpable route and rewards do not accelerate difficulty', () => {
  const g = game(); g.start();
  for (let i = 0; i < 120; i++) {
    g.run('objects = []; spawnRow()');
    assert.ok(g.run("[-1, 0, 1].some(value => !objects.some(item => item.lane === value && item.kind === 'wall'))"));
    assert.ok(g.run("objects.filter(item => item.kind === 'shard').every(item => !objects.some(other => other.kind === 'wall' && item.lane === other.lane))"));
  }
  const a = game(), b = game(); a.start(); b.start(); b.run('score = 100000');
  a.step(0.02); b.step(0.02);
  assert.equal(a.run('distance'), b.run('distance'));
  g.run('frame(1000)'); // Exercise actual drawing calls, including all obstacle kinds.
});

test('settings toggle music, tilt steering and lift jump, and persist', () => {
  const g = game(); g.start();
  g.emit('#music', 'click'); g.emit('#tilt', 'click');
  assert.equal(g.run('settings.music'), false);
  const saved = JSON.parse(g.storage.get('autoyou.neon-horizon.settings.v1'));
  assert.equal(saved.music, false); assert.equal(saved.tilt, false);
  g.window.autoYouGameTilt(0, 9.8);
  for (let i = 0; i < 20; i++) g.window.autoYouGameTilt(3, 9.8);
  assert.equal(g.run('lane'), 0, 'tilt steering is off');
  const reloaded = game({storage: g.storage});
  assert.equal(reloaded.run('settings.music'), false); assert.equal(reloaded.run('settings.tilt'), false);
  reloaded.emit('#lift', 'click'); assert.equal(reloaded.run('liftEnabled'), false);
  assert.equal(game({storage: g.storage}).run('liftEnabled'), false);
});

test('the settings panel pauses the run and blocks gameplay input until closed', () => {
  const g = game(); g.start();
  g.emit('#settings-open', 'click');
  assert.equal(g.run('mode'), 'paused'); assert.equal(g.run('settingsOpen'), true);
  g.emit('window', 'keydown', {key: 'ArrowLeft', repeat: false});
  g.remote([{id: 0, x: 0.5, y: 0.5}]); g.remote([]);
  assert.equal(g.run('mode'), 'paused'); assert.equal(g.run('lane'), 0);
  g.emit('window', 'keydown', {key: 'Escape', repeat: false});
  assert.equal(g.run('settingsOpen'), false);
  g.emit('#start', 'click'); assert.equal(g.run('mode'), 'running');
  g.remoteFrame({input_type: 'button', button: 'select', phase: 'down'});
  assert.equal(g.run('settingsOpen'), true);
});

test('delete high scores needs a confirmation and survives a native store that keeps its maximum', () => {
  let record = 900;
  const native = {AutoYouGameScores: {readBest: () => record, saveBest: value => { record = Math.max(record, value); }}};
  const storage = new Map();
  const g = game({native, storage}); assert.equal(g.run('best'), 900);
  g.emit('#reset-best', 'click'); assert.equal(g.run('best'), 900, 'first press only arms the button');
  g.emit('#reset-best', 'click'); assert.equal(g.run('best'), 0);
  const reloaded = game({native, storage}); assert.equal(reloaded.run('best'), 0);
  reloaded.start(); reloaded.run('score = 1200; saveBest()');
  assert.equal(game({native, storage}).run('best'), 1200);
});

test('power-ups: magnet gathers every lane, surge smashes obstacles, double scales points', () => {
  const g = game(); g.start(); g.run('magnet = 5');
  impact(g, 'shard', ', lane: 1'); impact(g, 'shard', ', air: true');
  assert.equal(g.run('diamonds'), 2);
  const s = game(); s.start(); s.run('surge = 3');
  impact(s, 'wall'); impact(s, 'hurdle');
  assert.equal(s.run('mode'), 'running'); assert.equal(s.run('run.smash'), 2);
  const d = game(); d.start(); d.run('doubled = 5; score = 0'); impact(d, 'shard');
  assert.ok(d.run('score') >= 50);
  const p = game(); p.start(); impact(p, 'power', ", power: 'magnet'");
  assert.ok(p.run('magnet') > 0); assert.equal(p.run('run.powers'), 1);
});

test('close calls, perfect jumps and overdrive reward skill without changing speed', () => {
  const g = game(); g.start();
  g.run("action('right')"); for (let i = 0; i < 12; i++) g.step(1 / 60);
  impact(g, 'wall');
  assert.equal(g.run('mode'), 'running'); assert.equal(g.run('run.close'), 1);
  assert.ok(g.run('drive') > 0.3);
  g.run('jumpTime = JUMP_DURATION / 2'); impact(g, 'hurdle', ', lane: 1');
  assert.equal(g.run('run.perfect'), 1);
  g.run('drive = 0.99; addDrive(0.05)'); assert.ok(g.run('overdrive') > 0);
  const a = game(), b = game(); a.start(); b.start(); b.run('overdrive = 5; doubled = 5; magnet = 5');
  a.step(0.02); b.step(0.02); assert.equal(a.run('distance'), b.run('distance'));
});

test('slam ends a jump early with a continuous fall', () => {
  const g = game(); g.start(); g.run("action('jump')");
  g.step(0.1); const before = g.run('jumpHeight()');
  g.emit('window', 'keydown', {key: 's', repeat: false});
  assert.ok(Math.abs(g.run('jumpHeight()') - before) < 1e-9);
  let steps = 0; while (g.run('jumpTime') > 0 && steps < 100) { g.step(0.02); steps++; }
  assert.ok(steps < 12, `slam landed after ${steps} steps`);
});

test('missions complete, persist and raise the rank', () => {
  const g = game(); g.start();
  g.run("progress.missions = [{type: 'clears', target: 1}, {type: 'diamonds', target: 1}, {type: 'close', target: 50}]");
  g.run('jumpTime = 0.45'); impact(g, 'hurdle'); g.run('jumpTime = 0'); impact(g, 'shard');
  assert.equal(g.run('progress.stars'), 2);
  g.run("progress.missions[0] = {type: 'diamonds', target: 1}"); g.step(0.01);
  assert.equal(g.run('progress.rank'), 2); assert.equal(g.run('progress.stars'), 0);
  assert.equal(game({storage: g.storage}).run('progress.rank'), 2);
  assert.ok(game({storage: g.storage}).run('rankBonus()') > 1);
});

test('the soundtrack stems loop seamlessly, are audible, and react to energy and danger', () => {
  const g = game();
  for (const name of g.run('MUSIC_STEMS')) {
    const data = g.run(`renderStem('${name}', 8000)`);
    assert.equal(data.length, Math.round(g.run('MUSIC_LOOP') * 8000));
    let peak = 0; for (const value of data) { assert.ok(Number.isFinite(value)); peak = Math.max(peak, Math.abs(value)); }
    assert.ok(peak > 0.02 && peak < 2, `${name} peak ${peak}`);
  }
  const calm = g.run('stemLevels(0, 0)'), full = g.run('stemLevels(1, 1)');
  assert.equal(calm.lead, 0); assert.equal(calm.arp, 0); assert.equal(calm.tension, 0);
  assert.ok(full.lead > 0.5 && full.arp === 1 && full.tension === 1);
});

test('graphics tiers step down on slow frames only in auto mode', () => {
  const g = game(); g.start();
  assert.equal(g.run('tier'), 2);
  for (let i = 0; i < 200; i++) g.run('tuneQuality(0.02, 1 / 30)');
  assert.ok(g.run('tier') < 2);
  g.emit('#quality', 'click'); assert.equal(g.run('settings.quality'), 'high'); assert.equal(g.run('tier'), 2);
  for (let i = 0; i < 200; i++) g.run('tuneQuality(0.02, 1 / 30)');
  assert.equal(g.run('tier'), 2);
  g.run("objects = []; for (let i = 0; i < 6; i++) spawnRow(); objects.push({kind: 'power', power: 'surge', lane: 0, z: 0.5}); surge = 2; emit(100, 100, 20, '#fff')");
  for (const t of [0, 1, 2]) { g.run(`setTier(${t}); frame(2000 + ${t} * 16)`); }
});
