/* Shared by every offline AutoYou game runtime. No server connection is used. */
(() => {
  if (window.AutoYouGame) return;
  const request = async (statements, asset) => {
    const reply = await fetch('/__autoyou/db', {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({statements, asset})});
    const value = await reply.json();
    if (!reply.ok || value.error) throw new Error(value.error || 'Game database request failed');
    return value.results;
  };
  const execute = async (sql, params = []) => (await request([{sql, params}]))[0];
  const listeners = new Map(), keys = new Set(), pointers = new Map();
  let motion = null, nativePads = null, pinch = null, paused = false, focused = document.hasFocus?.() ?? true;
  const emit = (type, value) => {
    const event = Object.freeze({type, time: performance.now(), ...value});
    for (const listener of listeners.get(type) || []) { try { listener(event); } catch (error) { console.error(error); } }
    dispatchEvent(new CustomEvent('autoyougameinput', {detail: event}));
  };
  const pads = () => paused || !focused || document.hidden ? [] : nativePads || Array.from(navigator.getGamepads?.() || []).filter(Boolean).map(pad => ({
    index: pad.index, id: pad.id, connected: pad.connected, mapping: pad.mapping, axes: Array.from(pad.axes),
    buttons: Array.from(pad.buttons, button => ({value: button.value, pressed: button.pressed, touched: button.touched}))}));
  const input = Object.freeze({
    on(type, listener) { if (typeof listener !== 'function') throw new TypeError('Input listener must be a function');
      if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(listener);
      return () => listeners.get(type)?.delete(listener); },
    snapshot: () => ({keys: [...keys], pointers: [...pointers.values()].map(p => ({...p})), motion,
      gamepads: pads()}),
    gamepads: pads,
    capture(element) { const oldTouch = element.style.touchAction, oldSelect = element.style.userSelect, oldTab = element.getAttribute('tabindex');
      element.style.touchAction = 'none'; element.style.userSelect = 'none'; element.tabIndex = 0;
      const focus = event => { element.focus({preventScroll: true}); try { element.setPointerCapture(event.pointerId); } catch (_) {} };
      const menu = event => event.preventDefault(); element.addEventListener('pointerdown', focus); element.addEventListener('contextmenu', menu);
      return () => { element.style.touchAction = oldTouch; element.style.userSelect = oldSelect;
        if (oldTab === null) element.removeAttribute('tabindex'); else element.setAttribute('tabindex', oldTab);
        element.removeEventListener('pointerdown', focus); element.removeEventListener('contextmenu', menu); }; }
  });
  for (const name of ['keydown', 'keyup']) addEventListener(name, event => {
    if (name === 'keydown') keys.add(event.code); else keys.delete(event.code);
    emit('key', {phase: name === 'keydown' ? 'down' : 'up', code: event.code, key: event.key, repeat: event.repeat,
      alt: event.altKey, ctrl: event.ctrlKey, shift: event.shiftKey, meta: event.metaKey});
  });
  for (const name of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel']) addEventListener(name, event => {
    const previous = pointers.get(event.pointerId), phase = name.slice(7);
    const value = {id: event.pointerId, kind: event.pointerType, phase, x: event.clientX, y: event.clientY,
      dx: previous ? event.clientX - previous.x : 0, dy: previous ? event.clientY - previous.y : 0,
      buttons: event.buttons, pressure: event.pressure};
    if (phase === 'down' || phase === 'move' && previous) pointers.set(event.pointerId, {...value,
      startX: previous?.startX ?? value.x, startY: previous?.startY ?? value.y, started: previous?.started ?? performance.now(), multi: previous?.multi ?? false});
    else pointers.delete(event.pointerId);
    emit('pointer', value);
    if (phase === 'up' && previous && !previous.multi) {
      const dx = value.x - previous.startX, dy = value.y - previous.startY, duration = performance.now() - previous.started;
      if (Math.hypot(dx, dy) < 12 && duration < 400) emit('gesture', {gesture: 'tap', x: value.x, y: value.y});
      else if (Math.hypot(dx, dy) > 40 && duration < 600) emit('gesture', {gesture: 'swipe', dx, dy, duration,
        direction: Math.abs(dx) > Math.abs(dy) ? dx > 0 ? 'right' : 'left' : dy > 0 ? 'down' : 'up'});
    }
    const points = [...pointers.values()].filter(p => p.kind === 'touch' || p.kind === 'pen').slice(0, 2);
    if (points.length === 2) {
      points.forEach(point => { point.multi = true; });
      const [a, b] = points, distance = Math.hypot(b.x - a.x, b.y - a.y), angle = Math.atan2(b.y - a.y, b.x - a.x);
      const ids = points.map(p => p.id).join(':'); if (pinch && pinch.ids !== ids) { emit('gesture', {gesture: 'pinch', phase: 'end'}); pinch = null; }
      const first = !pinch; if (first) pinch = {distance: Math.max(1, distance), angle, ids};
      emit('gesture', {gesture: 'pinch', phase: first ? 'start' : 'change', scale: distance / pinch.distance,
        rotation: angle - pinch.angle, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2});
    } else if (pinch) { emit('gesture', {gesture: 'pinch', phase: 'end'}); pinch = null; }
  }, {passive: true});
  addEventListener('wheel', event => emit('wheel', {x: event.clientX, y: event.clientY, dx: event.deltaX, dy: event.deltaY, dz: event.deltaZ, mode: event.deltaMode}), {passive: true});
  const reset = () => { keys.clear(); pointers.clear(); pinch = null; emit('reset', {}); };
  addEventListener('focus', () => { focused = true; }); addEventListener('blur', () => { focused = false; reset(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) reset(); });
  let lastPads = '';
  const poll = () => { if (!document.hidden) { const current = pads(), signature = JSON.stringify(current);
    if (signature !== lastPads) { lastPads = signature; emit('gamepad', {gamepads: current}); } } requestAnimationFrame(poll); };
  requestAnimationFrame(poll);
  Object.defineProperty(window, '__autoYouGameInput', {value: (type, value) => {
    if (type === 'gamepad') { paused = false; nativePads = value.gamepads?.length ? value.gamepads : null; return; }
    if (type === 'reset') { paused = true; nativePads = null; motion = null; reset(); return; }
    if (type !== 'motion') return;
    const {accelerationSign = 1, ...sample} = value;
    const vector = value => value ? {x: value.x * accelerationSign, y: value.y * accelerationSign, z: value.z * accelerationSign} : undefined;
    paused = false; motion = {...sample, acceleration: vector(sample.acceleration), accelerationIncludingGravity: vector(sample.accelerationIncludingGravity),
      orientation: globalThis.screen?.orientation?.angle ?? window.orientation ?? 0}; emit('motion', motion);
    const acceleration = value.accelerationIncludingGravity;
    if (acceleration && typeof window.autoYouGameTilt === 'function') window.autoYouGameTilt(acceleration.x, acceleration.y, acceleration.z);
    if (typeof DeviceMotionEvent === 'function') dispatchEvent(new DeviceMotionEvent('devicemotion', {acceleration: motion.acceleration,
      accelerationIncludingGravity: motion.accelerationIncludingGravity, rotationRate: motion.rotationRate, interval: motion.interval}));
  }});
  window.AutoYouGame = Object.freeze({version: 1, db: Object.freeze({execute,
    transaction: statements => request(statements),
    readAsset: async (path, sql, params = []) => (await request([{sql, params}], path))[0]}), input});
  dispatchEvent(new Event('autoyougameready'));
})();
