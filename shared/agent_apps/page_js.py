# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Behaviour of the Agent Apps store page.

Plain ES5-style script, no build step, no external request: it runs unchanged in
a phone WebView, a desktop browser and a browser on the home network.

Touch model (one finger):
  touch / slide    -> the details dock follows the app under the finger
  swipe down / up   -> collapse the dock to its grabber / show it again
  hold or press    -> the app lifts and can be dragged to a new place
  tap              -> opens the app
Two fingers resize the icons and text. Mouse: hover previews, drag moves.
Keyboard: arrows move focus, Shift+arrows move the app, Enter opens.

The layout (order, sort, size, usage, last focus) is kept in this browser's
localStorage. The iOS in-app browser keeps no web storage between launches, so
it also mirrors the layout to the native shell when that offers the
``autoyouAppsLayout`` message handler.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


JS = r"""
(function () {
  'use strict';
  var doc = document, root = doc.documentElement, win = window;
  var NS = 'http://www.w3.org/2000/svg', XLINK = 'http://www.w3.org/1999/xlink';
  function $(id) { return doc.getElementById(id); }
  function now() { return Date.now(); }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  var shell = $('shell'), bar = $('bar'), grid = $('grid'), dock = $('dock');
  var qInput = $('q'), qClear = $('q-clear'), chipsEl = $('chips'), subEl = $('sub');
  var emptyEl = $('empty'), noteEl = $('note'), liveEl = $('live'), toastEl = $('toast');
  var sheet = $('sheet'), viewBtn = $('btn-view'), refreshBtn = $('btn-refresh');
  var KEY = 'autoyou.agentApps.v2';
  var SCALES = [0.86, 1, 1.2, 1.45, 1.75];
  var HOLD_MS = 420, HOLD_MOUSE_MS = 380, SLOP_TOUCH = 9, SLOP_MOUSE = 4;
  var reduceMotion = false;
  try { reduceMotion = win.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (_) {}
  var canHover = false;
  try { canHover = win.matchMedia('(hover: hover) and (pointer: fine)').matches; } catch (_) {}

  var boot = {};
  try { boot = JSON.parse($('boot').textContent) || {}; } catch (_) {}

  /* ---------- text helpers ---------- */
  var NON_WORD;
  try { NON_WORD = new RegExp('[^\\p{L}\\p{N}]+', 'gu'); } catch (_) { NON_WORD = /[^a-z0-9]+/g; }
  function norm(s) {
    s = String(s || '').toLowerCase();
    try { s = s.normalize('NFKD').replace(/[̀-ͯ]/g, ''); } catch (_) {}
    return s.replace(NON_WORD, ' ').replace(/\s+/g, ' ').trim();
  }

  /* ---------- model ---------- */
  var apps = Object.create(null);
  var cells = Object.create(null);
  var lastSig = '';

  function indexApp(a) {
    a._name = norm(a.title + ' ' + String(a.name).replace(/_/g, ' '));
    a._kw = norm((a.app.keywords || []).join(' ') + ' ' + (a.app.category_label || ''));
    a._desc = norm(a.description);
    return a;
  }
  function fallbackApp(name) {
    var h = 0, i;
    for (i = 0; i < name.length; i++) { h = (h * 31 + name.charCodeAt(i)) >>> 0; }
    return { glyph: 'globe', category: 'other', category_label: 'More', tone: h % 12, colors: ['#4cc3ff', '#2f5bea'], keywords: [], rank: 100 };
  }
  function launchable(u) { return /^\/(?!\/)/.test(u) || /^https?:\/\//i.test(u) ? u : ''; }
  function toApp(f) {
    var open = launchable(String(f.open_url || f.launch_url || f.launch_path || '').trim());
    var name = String(f.agent_name || '').trim();
    return indexApp({
      name: name,
      title: String(f.title || f.display_name || name || 'App').trim(),
      description: String(f.description || '').trim(),
      open_url: open,
      ready: !!(f.frontend_port_registered && open),
      route_mode: String(f.route_mode || 'path_proxy'),
      port: f.direct_forward_port || f.proxy_port || 0,
      app: f.app && f.app.colors ? f.app : fallbackApp(name)
    });
  }
  function signature(list) {
    return JSON.stringify(list.map(function (a) {
      return [a.name, a.title, a.description, a.open_url, a.ready, a.route_mode, a.port, a.app.glyph, a.app.colors, a.app.category, a.app.rank];
    }));
  }
  (boot.apps || []).forEach(function (a) { apps[a.name] = indexApp(a); });
  lastSig = signature(Object.keys(apps).map(function (n) { return apps[n]; }));

  /* ---------- saved layout ---------- */
  var state = { v: 2, t: 0, order: [], sort: 'custom', scale: 1, usage: {}, focus: '', hint: 1 };
  function sanitize(raw) {
    if (!raw || typeof raw !== 'object') { return null; }
    var out = { v: 2, t: Number(raw.t) || 0, order: [], sort: 'custom', scale: 1, usage: {}, focus: '', hint: raw.hint === 0 ? 0 : 1 };
    if (Array.isArray(raw.order)) {
      raw.order.slice(0, 400).forEach(function (n) { if (typeof n === 'string' && n.length < 120) { out.order.push(n); } });
    }
    if (raw.sort === 'usage' || raw.sort === 'name') { out.sort = raw.sort; }
    out.scale = clamp(Number(raw.scale) || 1, 0.8, 1.75);
    if (raw.usage && typeof raw.usage === 'object') {
      Object.keys(raw.usage).slice(0, 400).forEach(function (n) {
        var u = raw.usage[n];
        if (u && typeof u === 'object') { out.usage[n] = { n: Math.max(0, Number(u.n) | 0), t: Number(u.t) || 0 }; }
      });
    }
    if (typeof raw.focus === 'string') { out.focus = raw.focus.slice(0, 120); }
    return out;
  }
  function parse(json) { try { return sanitize(JSON.parse(json)); } catch (_) { return null; } }
  function loadLocal() { try { return parse(win.localStorage.getItem(KEY)); } catch (_) { return null; } }

  var nativeLayout = null;
  try { nativeLayout = (win.webkit && win.webkit.messageHandlers && win.webkit.messageHandlers.autoyouAppsLayout) || null; } catch (_) {}
  function nativeSet(json) {
    if (!nativeLayout) { return; }
    try { var p = nativeLayout.postMessage({ op: 'set', value: json }); if (p && p.catch) { p.catch(function () {}); } } catch (_) {}
  }
  function nativeGet() {
    if (!nativeLayout) { return Promise.resolve(null); }
    var wait = new Promise(function (resolve) { setTimeout(function () { resolve(null); }, 500); });
    var ask;
    try { ask = Promise.resolve(nativeLayout.postMessage({ op: 'get' })); } catch (_) { return Promise.resolve(null); }
    return Promise.race([ask, wait]).then(function (v) { return typeof v === 'string' ? v : null; }, function () { return null; });
  }

  var saveTimer = 0;
  function flush() {
    clearTimeout(saveTimer); saveTimer = 0;
    state.t = now();
    var json = JSON.stringify(state);
    try { win.localStorage.setItem(KEY, json); } catch (_) {}
    nativeSet(json);
  }
  function save() { if (!saveTimer) { saveTimer = setTimeout(flush, 220); } }
  function adopt(next) {
    state = next;
    setScale(state.scale, true);
    paintSortUI();
  }
  var local = loadLocal();
  if (local) { state = local; }
  if (nativeLayout) { root.classList.add('is-pending'); }

  /* ---------- ordering and filtering ---------- */
  var view = { query: '', cat: 'all', remote: null, remoteQ: '' };

  function baseOrder() {
    return Object.keys(apps).sort(function (a, b) {
      var A = apps[a], B = apps[b];
      return (A.app.rank - B.app.rank) || A.title.localeCompare(B.title);
    });
  }
  function customOrder() {
    var seen = Object.create(null), out = [];
    state.order.forEach(function (n) { if (apps[n] && !seen[n]) { seen[n] = 1; out.push(n); } });
    baseOrder().forEach(function (n) { if (!seen[n]) { seen[n] = 1; out.push(n); } });
    return out;
  }
  function uses(n) { return state.usage[n] ? state.usage[n].n : 0; }
  function lastUse(n) { return state.usage[n] ? state.usage[n].t : 0; }
  function sortedNames() {
    var base = customOrder();
    if (state.sort === 'name') {
      return base.slice().sort(function (a, b) { return apps[a].title.localeCompare(apps[b].title); });
    }
    if (state.sort === 'usage') {
      var at = {};
      base.forEach(function (n, i) { at[n] = i; });
      return base.slice().sort(function (a, b) { return (uses(b) - uses(a)) || (lastUse(b) - lastUse(a)) || (at[a] - at[b]); });
    }
    return base;
  }
  function localMatches(query) {
    var tokens = norm(query).split(' ').filter(Boolean);
    if (!tokens.length) { return null; }
    var order = sortedNames(), at = {};
    order.forEach(function (n, i) { at[n] = i; });
    var hits = [];
    order.forEach(function (n) {
      var a = apps[n], score = 0, ok = true, i, t, v, words;
      words = a._name.split(' ');
      for (i = 0; i < tokens.length; i++) {
        t = tokens[i]; v = 0;
        if (words.indexOf(t) >= 0) { v = 9; }
        else if (words.some(function (w) { return w.indexOf(t) === 0; })) { v = 7; }
        else if (a._name.indexOf(t) >= 0) { v = 5; }
        else if (a._kw.indexOf(t) >= 0) { v = 3.5; }
        else if (a._desc.indexOf(t) >= 0) { v = 2; }
        if (!v) { ok = false; break; }
        score += v;
      }
      if (ok) { hits.push({ n: n, s: score }); }
    });
    hits.sort(function (a, b) { return (b.s - a.s) || (at[a.n] - at[b.n]); });
    return hits.map(function (h) { return h.n; });
  }
  function visibleNames() {
    var names, q = norm(view.query);
    if (q) {
      var local = localMatches(q) || [];
      if (view.remote && view.remoteQ === q) {
        var seen = Object.create(null);
        names = [];
        view.remote.concat(local).forEach(function (n) { if (apps[n] && !seen[n]) { seen[n] = 1; names.push(n); } });
      } else { names = local; }
    } else { names = sortedNames(); }
    if (view.cat !== 'all') { names = names.filter(function (n) { return apps[n].app.category === view.cat; }); }
    return names;
  }
  function canReorder() { return !norm(view.query) && view.cat === 'all'; }

  /* ---------- cells ---------- */
  function el(tag, cls) { var e = doc.createElement(tag); if (cls) { e.className = cls; } return e; }
  function svgGlyph(name) {
    var s = doc.createElementNS(NS, 'svg'), u = doc.createElementNS(NS, 'use');
    s.setAttribute('class', 'glyph'); s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true');
    u.setAttribute('href', '#g-' + name); u.setAttributeNS(XLINK, 'xlink:href', '#g-' + name);
    s.appendChild(u);
    return s;
  }
  function paintIcon(icon, a) {
    icon.style.setProperty('--a', a.app.colors[0]);
    icon.style.setProperty('--b', a.app.colors[1]);
    while (icon.firstChild) { icon.removeChild(icon.firstChild); }
    icon.appendChild(svgGlyph(a.app.glyph));
  }
  function paintCell(li, a) {
    li.setAttribute('data-name', a.name);
    li.classList.toggle('is-wait', !a.ready);
    var link = li.querySelector('a.app');
    link.setAttribute('href', a.open_url || '#');
    link.setAttribute('aria-describedby', 'd-' + a.name);
    link.setAttribute('aria-label', a.title + (a.ready ? '' : ', not ready'));
    paintIcon(li.querySelector('.icon'), a);
    li.querySelector('.label').textContent = a.title;
    var d = li.querySelector('.sr');
    if (!d) { d = el('span', 'sr'); li.appendChild(d); }
    d.id = 'd-' + a.name;
    d.textContent = a.description || a.title;
  }
  function makeCell(a) {
    var li = el('li', 'cell'), link = el('a', 'app'), icon = el('span', 'icon'), label = el('span', 'label'), d = el('span', 'sr');
    link.setAttribute('draggable', 'false');
    link.appendChild(icon); link.appendChild(label);
    li.appendChild(link); li.appendChild(d);
    paintCell(li, a);
    return li;
  }
  function adoptServerCells() {
    Array.prototype.forEach.call(grid.children, function (li) {
      var n = li.getAttribute('data-name');
      if (n && apps[n]) { cells[n] = li; } else { li.parentNode.removeChild(li); }
    });
  }
  function visibleCells() {
    return Array.prototype.filter.call(grid.children, function (li) { return !li.hidden; });
  }
  function measure(list) {
    var out = Object.create(null);
    list.forEach(function (li) { out[li.getAttribute('data-name')] = li.getBoundingClientRect(); });
    return out;
  }
  function stopMotion(list) {
    list.forEach(function (li) { if (li.getAnimations) { li.getAnimations().forEach(function (an) { an.cancel(); }); } });
  }
  function flip(first, list) {
    if (reduceMotion || !list.length || list.length > 90 || !list[0].animate) { return; }
    stopMotion(list);
    list.forEach(function (li) {
      var a = first[li.getAttribute('data-name')];
      if (!a) { return; }
      var b = li.getBoundingClientRect(), dx = a.left - b.left, dy = a.top - b.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) { return; }
      li.animate([{ transform: 'translate(' + dx + 'px,' + dy + 'px)' }, { transform: 'none' }],
        { duration: 260, easing: 'cubic-bezier(0.2, 0.85, 0.2, 1)' });
    });
  }

  var pendingRender = false;
  function render(opts) {
    if (G && G.mode === 'drag') { pendingRender = true; return; }
    opts = opts || {};
    var names = visibleNames(), show = Object.create(null), before = null, i;
    names.forEach(function (n) { show[n] = 1; });
    if (opts.animate) { before = measure(visibleCells()); }
    Object.keys(apps).forEach(function (n) { if (!cells[n]) { cells[n] = makeCell(apps[n]); } });
    Object.keys(cells).forEach(function (n) {
      if (!apps[n]) { if (cells[n].parentNode) { cells[n].parentNode.removeChild(cells[n]); } delete cells[n]; }
    });
    for (i = 0; i < names.length; i++) {
      var li = cells[names[i]];
      if (grid.children[i] !== li) { grid.insertBefore(li, grid.children[i] || null); }
      li.hidden = false;
    }
    Object.keys(cells).forEach(function (n) {
      if (!show[n]) { cells[n].hidden = true; if (cells[n].parentNode !== grid || grid.lastChild !== cells[n]) { grid.appendChild(cells[n]); } }
    });
    if (before) { flip(before, names.map(function (n) { return cells[n]; })); }
    var total = Object.keys(apps).length;
    subEl.textContent = total + (total === 1 ? ' app' : ' apps') + (boot.server_name ? ' on ' + boot.server_name : '');
    dock.hidden = !total;
    paintChips();
    var none = !names.length;
    emptyEl.hidden = !none;
    if (none) { paintEmpty(total); }
    ensureFocus(names);
  }
  function paintEmpty(total) {
    var q = view.query.trim();
    emptyEl.querySelector('strong').textContent = !total ? 'No apps are ready yet' : (q ? 'Nothing matches "' + q + '"' : 'Nothing in this section');
    emptyEl.querySelector('span').textContent = !total
      ? 'Turn a website on in the admin page and it will appear here.'
      : 'Try another word, or ask for what you need, like "music" or "private notes".';
  }

  /* ---------- chips ---------- */
  function paintChips() {
    var counts = Object.create(null), labels = Object.create(null), order = [];
    Object.keys(apps).forEach(function (n) {
      var c = apps[n].app.category;
      if (!counts[c]) { counts[c] = 0; labels[c] = apps[n].app.category_label; order.push(c); }
      counts[c] += 1;
    });
    var ranking = (boot.categories || []).map(function (c) { return c.id; });
    order.sort(function (a, b) { return ranking.indexOf(a) - ranking.indexOf(b); });
    if (view.cat !== 'all' && !counts[view.cat]) { view.cat = 'all'; }
    var want = ['all'].concat(order).join('|') + '#' + order.map(function (c) { return counts[c]; }).join(',');
    if (chipsEl.getAttribute('data-sig') !== want) {
      chipsEl.setAttribute('data-sig', want);
      chipsEl.textContent = '';
      var add = function (id, label, count) {
        var b = el('button', 'chip'); b.type = 'button'; b.setAttribute('data-cat', id);
        b.appendChild(doc.createTextNode(label));
        if (count) { var s = el('b'); s.textContent = String(count); b.appendChild(s); }
        chipsEl.appendChild(b);
      };
      add('all', 'All', Object.keys(apps).length);
      order.forEach(function (c) { add(c, labels[c], counts[c]); });
    }
    Array.prototype.forEach.call(chipsEl.children, function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-cat') === view.cat ? 'true' : 'false');
    });
    chipsEl.hidden = order.length < 2;
  }
  chipsEl.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('.chip') : null;
    if (!b) { return; }
    view.cat = b.getAttribute('data-cat') || 'all';
    render({ animate: true });
  });

  /* ---------- focus + details dock ---------- */
  var dockName = $('dock-name'), dockCat = $('dock-cat'), dockDesc = $('dock-desc'), dockIcon = $('dock-icon');
  var dockOpen = $('dock-open'), dockCopy = $('dock-copy'), dockMeta = $('dock-meta'), dockHint = $('dock-hint');
  var dockBody = $('dock-body'), dockGrab = dock.querySelector('.dock-grab');
  var dockSwipe = null, suppressDockGrabClickUntil = 0;
  var shownName = '', shownKey = '';

  function pill(text, cls) { var p = el('span', 'pill' + (cls ? ' ' + cls : '')); p.textContent = text; return p; }
  function paintDock(a) {
    var key = [a.name, a.title, a.description, a.open_url, a.ready, a.app.glyph, a.app.colors.join()].join('|');
    shownName = a.name;
    if (key === shownKey) { return; }
    shownKey = key;
    dockName.textContent = a.title;
    dockCat.textContent = a.app.category_label;
    dockDesc.textContent = a.description || 'This app has not added a description yet.';
    paintIcon(dockIcon, a);
    var url = resolveLaunchTarget(a.open_url);
    dockOpen.setAttribute('href', url || '#');
    dockOpen.setAttribute('aria-disabled', a.ready ? 'false' : 'true');
    dockOpen.lastChild.nodeValue = a.ready ? 'Open' : 'Not ready yet';
    dockMeta.textContent = '';
    dockMeta.appendChild(pill(a.route_mode === 'direct_forward' ? 'Own port' + (a.port ? ' ' + a.port : '') : 'Through this link'));
    dockMeta.appendChild(pill(a.ready ? 'Ready' : 'Starting', a.ready ? 'ok' : 'warn'));
    if (dockIcon.animate && !reduceMotion) {
      dockIcon.animate([{ transform: 'scale(0.84)', opacity: 0.6 }, { transform: 'scale(1)', opacity: 1 }], { duration: 200, easing: 'cubic-bezier(0.2, 0.9, 0.3, 1.3)' });
    }
  }
  function setRoving(name) {
    Object.keys(cells).forEach(function (n) {
      var link = cells[n].querySelector('a.app');
      if (link) { link.setAttribute('tabindex', n === name ? '0' : '-1'); }
    });
  }
  function setFocus(name) {
    var a = apps[name];
    if (!a || !cells[name]) { return; }
    if (state.focus !== name) {
      var prev = cells[state.focus];
      if (prev) { prev.classList.remove('is-focus'); }
      state.focus = name;
      save();
    }
    cells[name].classList.add('is-focus');
    paintDock(a);
    setRoving(name);
  }
  function ensureFocus(names) {
    var cur = state.focus;
    var want = (cur && apps[cur] && cells[cur] && !cells[cur].hidden) ? cur : (names[0] || '');
    if (!want) { shownKey = ''; return; }
    Object.keys(cells).forEach(function (n) { cells[n].classList.toggle('is-focus', n === want); });
    state.focus = want;
    paintDock(apps[want]);
    setRoving(want);
  }
  function setDockCollapsed(collapsed) {
    dock.setAttribute('data-collapsed', collapsed ? '1' : '0');
    dockGrab.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    dockGrab.setAttribute('aria-label', collapsed ? 'Show selected app details' : 'Hide selected app details');
    sizeDock();
  }
  function dockIsFixed() {
    try { return win.getComputedStyle(dock).position === 'fixed'; } catch (_) { return false; }
  }
  dock.addEventListener('click', function (e) {
    var open = e.target.closest ? e.target.closest('#dock-open') : null;
    if (open) { e.preventDefault(); openApp(shownName); return; }
    if (e.target.closest && e.target.closest('#dock-copy')) { copyLink(shownName); return; }
    if (e.target.closest && e.target.closest('.dock-grab')) {
      if (now() < suppressDockGrabClickUntil) { e.preventDefault(); return; }
      setDockCollapsed(dock.getAttribute('data-collapsed') !== '1');
      return;
    }
    if (e.target.closest && e.target.closest('.dock-head')) {
      dock.setAttribute('data-expanded', dock.getAttribute('data-expanded') === '1' ? '0' : '1');
      sizeDock();
    }
  });
  dock.addEventListener('touchstart', function (e) {
    dockSwipe = null;
    if (e.touches.length !== 1 || !dockIsFixed()) { return; }
    var target = e.target.closest ? e.target.closest('.dock-desc, .dock-grab') : null;
    var collapsed = dock.getAttribute('data-collapsed') === '1';
    if (collapsed && target && target.classList.contains('dock-grab')) {
      dockSwipe = { y: e.touches[0].clientY, collapsed: true };
    } else if (!collapsed && target && (
      target.classList.contains('dock-grab') ||
      (target.classList.contains('dock-desc') && dockBody.scrollTop <= 0)
    )) {
      dockSwipe = { y: e.touches[0].clientY, collapsed: false };
    }
  }, { passive: true });
  dock.addEventListener('touchmove', function (e) {
    if (!dockSwipe) { return; }
    if (e.touches.length !== 1) { dockSwipe = null; return; }
    var dy = e.touches[0].clientY - dockSwipe.y;
    if ((dockSwipe.collapsed && dy < -24) || (!dockSwipe.collapsed && dy > 24)) {
      if (e.cancelable) { e.preventDefault(); }
      setDockCollapsed(!dockSwipe.collapsed);
      dockSwipe = null;
      suppressDockGrabClickUntil = now() + 500;
    }
  }, { passive: false });
  function clearDockSwipe() { dockSwipe = null; }
  dock.addEventListener('touchend', clearDockSwipe, { passive: true });
  dock.addEventListener('touchcancel', clearDockSwipe, { passive: true });
  function sizeDock() {
    var fixed = false;
    try { fixed = win.getComputedStyle(dock).position === 'fixed'; } catch (_) {}
    root.style.setProperty('--dock-h', fixed ? (dock.offsetHeight + 14) + 'px' : '0px');
  }
  if (win.ResizeObserver) { new win.ResizeObserver(sizeDock).observe(dock); }
  win.addEventListener('resize', sizeDock);
  function paintHint() {
    dockHint.hidden = !state.hint;
    dockHint.textContent = canHover
      ? 'Hover to preview. Drag an app to rearrange.'
      : 'Slide to preview. Hold to move. Pinch to resize.';
  }

  /* ---------- launching ---------- */
  function resolveLaunchTarget(target) {
    var raw = String(target || '').trim();
    if (!raw) { return ''; }
    try {
      var origin = win.location.origin || '';
      var resolved = new URL(raw, origin);
      var base = String(boot.base || '').trim();
      if (base) {
        var cfg = new URL(base, origin), loop = ['127.0.0.1', 'localhost', '::1', '[::1]'];
        if (loop.indexOf((resolved.hostname || '').toLowerCase()) >= 0 && loop.indexOf((cfg.hostname || '').toLowerCase()) >= 0 &&
            resolved.port === cfg.port && cfg.port) {
          return new URL((resolved.pathname || '/') + (resolved.search || '') + (resolved.hash || ''), origin).toString();
        }
      }
      return resolved.toString();
    } catch (_) { return raw; }
  }
  function openApp(name) {
    var a = apps[name];
    if (!a) { return; }
    if (!a.ready) { toast(a.title + ' is not ready yet.'); return; }
    var url = resolveLaunchTarget(a.open_url);
    if (!url) { return; }
    var u = state.usage[name] || { n: 0, t: 0 };
    state.usage[name] = { n: u.n + 1, t: now() };
    flush();
    if (cells[name]) { cells[name].classList.add('is-opening'); }
    announce('Opening ' + a.title);
    win.location.assign(url);
  }
  win.addEventListener('pageshow', function (e) {
    Object.keys(cells).forEach(function (n) { cells[n].classList.remove('is-opening'); });
    if (e.persisted) { refresh(); }
  });
  win.addEventListener('pagehide', flush);
  doc.addEventListener('visibilitychange', function () { if (doc.visibilityState === 'hidden') { flush(); } else { refresh(); } });
  function copyLink(name) {
    var a = apps[name];
    if (!a) { return; }
    var url = resolveLaunchTarget(a.open_url), done = function () { toast('Link copied'); };
    var fallback = function () {
      try {
        var t = el('textarea'); t.value = url; t.setAttribute('readonly', ''); t.style.cssText = 'position:fixed;opacity:0;top:0';
        doc.body.appendChild(t); t.select();
        var ok = doc.execCommand('copy'); doc.body.removeChild(t);
        if (ok) { done(); return; }
      } catch (_) {}
      win.prompt('Copy this link', url);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(url).then(done, fallback); } else { fallback(); }
  }

  /* ---------- feedback ---------- */
  var toastTimer = 0;
  function toast(text) {
    toastEl.textContent = text; toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, 2300);
  }
  function announce(text) { liveEl.textContent = ''; setTimeout(function () { liveEl.textContent = text; }, 30); }
  function haptic(kind) {
    try { if (navigator.vibrate && navigator.vibrate(kind === 'lift' ? 16 : 8)) { return; } } catch (_) {}
    try { var h = win.webkit && win.webkit.messageHandlers && win.webkit.messageHandlers.autoyouHaptic; if (h) { h.postMessage(kind); } } catch (_) {}
  }

  /* ---------- gestures ---------- */
  var G = null, pinch = null, suppressUntil = 0, lastPointerType = 'mouse', dragRule = null;
  function cellOf(node) { return node && node.closest ? node.closest('.cell') : null; }

  // iOS shows a link preview on a long press unless the element is not a link.
  function stashHref(g) {
    if (g.link && g.link.hasAttribute('href')) { g.href = g.link.getAttribute('href'); g.link.removeAttribute('href'); }
  }
  function restoreHref(g) {
    if (g.link && g.href !== null && g.href !== undefined) { g.link.setAttribute('href', g.href); g.href = null; }
  }

  grid.addEventListener('pointerdown', function (e) {
    lastPointerType = e.pointerType || 'mouse';
    if (pinch) { return; }
    if (G) { if (e.pointerId !== G.id) { endGesture(true); } return; }
    if (e.pointerType === 'mouse' && (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey)) { return; }
    var li = cellOf(e.target);
    if (!li || li.hidden) { return; }
    var name = li.getAttribute('data-name');
    if (!apps[name]) { return; }
    var touchLike = e.pointerType !== 'mouse';
    G = {
      id: e.pointerId, type: e.pointerType || 'mouse', cell: li, name: name, link: li.querySelector('a.app'), href: null,
      x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY, mode: 'press', timer: 0, raf: 0, minP: 1, maxP: 0, hover: name
    };
    li.classList.add('is-down');
    if (e.pointerType === 'touch') { stashHref(G); }
    setFocus(name);
    G.timer = setTimeout(function () { if (G && G.mode === 'press') { lift(); } }, touchLike ? HOLD_MS : HOLD_MOUSE_MS);
  });
  win.addEventListener('pointermove', function (e) {
    if (!G || e.pointerId !== G.id) { return; }
    G.x = e.clientX; G.y = e.clientY;
    var dx = G.x - G.x0, dy = G.y - G.y0, dist = Math.sqrt(dx * dx + dy * dy);
    if (e.pressure > 0) { G.maxP = Math.max(G.maxP, e.pressure); G.minP = Math.min(G.minP, e.pressure); }
    if (G.mode === 'press') {
      if (G.type === 'touch' && e.pressure >= 0.78 && (G.maxP - G.minP) >= 0.3 && dist < SLOP_TOUCH) { lift(); return; }
      if (dist > (G.type === 'touch' ? SLOP_TOUCH : SLOP_MOUSE)) {
        clearTimeout(G.timer);
        if (G.type === 'touch') { G.mode = 'scrub'; G.cell.classList.remove('is-down'); } else { lift(); }
      }
    }
    if (G && G.mode === 'scrub') { scrub(G.x, G.y); }
    else if (G && G.mode === 'drag') { dragTo(); }
  }, { passive: true });
  win.addEventListener('pointerup', function (e) { if (G && e.pointerId === G.id) { endGesture(false); } });
  win.addEventListener('pointercancel', function (e) { if (G && e.pointerId === G.id) { endGesture(true); } });

  // Force Touch devices report a hard press as a force change.
  grid.addEventListener('touchforcechange', function (e) {
    var t = e.touches && e.touches[0];
    if (G && G.mode === 'press' && t && t.force > 0.62) { lift(); }
  }, { passive: true });

  function scrub(x, y) {
    var li = cellOf(doc.elementFromPoint(x, y));
    if (li && !li.hidden && li.getAttribute('data-name') !== G.hover) {
      G.hover = li.getAttribute('data-name');
      setFocus(G.hover);
    }
  }
  function lift() {
    if (!G || G.mode !== 'press') { return; }
    clearTimeout(G.timer);
    G.cell.classList.remove('is-down');
    if (!canReorder()) {
      G.mode = 'blocked';
      haptic('tick');
      toast('Clear the search and filter to rearrange apps.');
      return;
    }
    G.mode = 'drag';
    beginDrag();
    if (G.type !== 'touch') { try { grid.setPointerCapture(G.id); } catch (_) {} }
  }
  function ensureDragRule() {
    if (dragRule) { return; }
    var st = el('style');
    st.textContent = '.drag-ghost{transform:translate3d(0,0,0) scale(1.12)}';
    doc.head.appendChild(st);
    try { dragRule = st.sheet.cssRules[0]; } catch (_) { dragRule = null; }
  }
  function placeGhost(left, top) {
    var t = 'translate3d(' + left.toFixed(1) + 'px,' + top.toFixed(1) + 'px,0) scale(1.12)';
    if (dragRule) { dragRule.style.transform = t; } else if (G && G.ghost) { G.ghost.style.transform = t; }
  }
  function beginDrag() {
    var li = G.cell, r = li.getBoundingClientRect(), list = visibleCells();
    var sx = win.pageXOffset || 0, sy = win.pageYOffset || 0;
    G.list = list; G.from = list.indexOf(li); G.at = G.from;
    G.slots = list.map(function (c) { var b = c.getBoundingClientRect(); return { x: b.left + b.width / 2 + sx, y: b.top + b.height / 2 + sy }; });
    G.offX = G.x0 - r.left; G.offY = G.y0 - r.top;
    var ghost = li.cloneNode(true);
    ghost.classList.remove('is-focus', 'is-down', 'is-placeholder', 'is-opening');
    ghost.classList.add('drag-ghost');
    ghost.removeAttribute('data-name');
    ghost.setAttribute('aria-hidden', 'true');
    Array.prototype.forEach.call(ghost.querySelectorAll('[id]'), function (n) { n.removeAttribute('id'); });
    var gl = ghost.querySelector('a'); gl.removeAttribute('href'); gl.setAttribute('tabindex', '-1');
    ghost.style.width = r.width + 'px';
    doc.body.appendChild(ghost);
    G.ghost = ghost;
    ensureDragRule();
    placeGhost(G.x - G.offX, G.y - G.offY);
    li.classList.add('is-placeholder');
    root.classList.add('is-dragging');
    dockHint.hidden = false;
    dockHint.textContent = 'Drag to a new place, then let go.';
    haptic('lift');
    announce('Picked up ' + apps[G.name].title + '. Drag to move it.');
    G.raf = win.requestAnimationFrame(autoScroll);
  }
  function dragTo() {
    placeGhost(G.x - G.offX, G.y - G.offY);
    retarget();
  }
  function retarget() {
    var px = G.x + (win.pageXOffset || 0), py = G.y + (win.pageYOffset || 0), best = -1, bd = Infinity, i;
    for (i = 0; i < G.slots.length; i++) {
      var dx = G.slots[i].x - px, dy = G.slots[i].y - py, d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = i; }
    }
    if (best >= 0 && best !== G.at) { moveTo(best); }
  }
  function moveTo(index) {
    var li = G.cell, before = measure(G.list), i;
    G.list.splice(G.at, 1);
    G.list.splice(index, 0, li);
    G.at = index;
    for (i = 0; i < G.list.length; i++) {
      if (grid.children[i] !== G.list[i]) { grid.insertBefore(G.list[i], grid.children[i] || null); }
    }
    flip(before, G.list);
  }
  function autoScroll() {
    if (!G || G.mode !== 'drag') { return; }
    var vh = win.innerHeight || 600, top = bar.getBoundingClientRect().bottom + 34, bottom = vh - 34, dy = 0;
    try { if (win.getComputedStyle(dock).position === 'fixed') { bottom -= dock.offsetHeight + 14; } } catch (_) {}
    if (G.y < top) { dy = -Math.min(24, (top - G.y) * 0.35 + 3); }
    else if (G.y > bottom) { dy = Math.min(24, (G.y - bottom) * 0.35 + 3); }
    if (dy) {
      var before = win.pageYOffset;
      win.scrollBy(0, dy);
      if (win.pageYOffset !== before) { retarget(); }
    }
    G.raf = win.requestAnimationFrame(autoScroll);
  }
  function endGesture(cancelled) {
    if (!G) { return; }
    var g = G;
    G = null;
    clearTimeout(g.timer);
    if (g.raf) { win.cancelAnimationFrame(g.raf); }
    g.cell.classList.remove('is-down');
    restoreHref(g);
    try { if (grid.hasPointerCapture && grid.hasPointerCapture(g.id)) { grid.releasePointerCapture(g.id); } } catch (_) {}
    if (g.mode === 'drag') { finishDrag(g, cancelled); suppressUntil = now() + 450; }
    else if (g.mode === 'scrub' || g.mode === 'blocked') { suppressUntil = now() + 450; }
    else if (!cancelled && g.mode === 'press' && g.type === 'touch') {
      suppressUntil = now() + 450; openApp(g.name);
    }
  }
  function finishDrag(g, cancelled) {
    var li = g.cell, ghost = g.ghost, list = g.list;
    root.classList.remove('is-dragging');
    paintHint();
    stopMotion(list);
    if (cancelled && g.at !== g.from) {
      var back = list.slice();
      back.splice(g.at, 1);
      back.splice(g.from, 0, li);
      back.forEach(function (c, i) { if (grid.children[i] !== c) { grid.insertBefore(c, grid.children[i] || null); } });
      list = back;
    } else if (!cancelled) {
      state.order = list.map(function (c) { return c.getAttribute('data-name'); });
      state.sort = 'custom';
      state.hint = 0;
      paintSortUI();
      flush();
      haptic('drop');
      announce('Moved ' + apps[g.name].title + ' to position ' + (g.at + 1) + ' of ' + list.length + '.');
    }
    var dest = li.getBoundingClientRect();
    var finish = function () {
      if (ghost.parentNode) { ghost.parentNode.removeChild(ghost); }
      li.classList.remove('is-placeholder');
      if (pendingRender) { pendingRender = false; render({ animate: true }); }
    };
    if (reduceMotion || !ghost.animate) { finish(); return; }
    var from = 'translate3d(' + (g.x - g.offX).toFixed(1) + 'px,' + (g.y - g.offY).toFixed(1) + 'px,0) scale(1.12)';
    var to = 'translate3d(' + dest.left.toFixed(1) + 'px,' + dest.top.toFixed(1) + 'px,0) scale(1)';
    var an = ghost.animate([{ transform: from }, { transform: to }], { duration: 190, easing: 'cubic-bezier(0.2, 0.9, 0.3, 1)', fill: 'forwards' });
    an.onfinish = finish;
    an.oncancel = finish;
  }
  function abortGesture() { if (G) { endGesture(true); } }
  win.addEventListener('blur', abortGesture);

  // The click that follows a gesture must not open the app.
  grid.addEventListener('click', function (e) {
    var link = e.target.closest ? e.target.closest('a.app') : null;
    if (!link) { return; }
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) { return; }
    e.preventDefault();
    if (now() < suppressUntil) { return; }
    var li = cellOf(link);
    if (li) { openApp(li.getAttribute('data-name')); }
  });
  grid.addEventListener('pointerover', function (e) {
    if (e.pointerType !== 'mouse' || G || root.classList.contains('is-dragging')) { return; }
    var li = cellOf(e.target);
    if (li && !li.hidden) { setFocus(li.getAttribute('data-name')); }
  });
  grid.addEventListener('dragstart', function (e) { e.preventDefault(); });
  grid.addEventListener('selectstart', function (e) { e.preventDefault(); });
  doc.addEventListener('contextmenu', function (e) {
    var inGrid = e.target.closest && e.target.closest('.grid');
    if (G || (inGrid && (lastPointerType === 'touch' || lastPointerType === 'pen'))) { e.preventDefault(); }
  }, true);

  /* ---------- two fingers resize ---------- */
  function span(t) { var dx = t[0].clientX - t[1].clientX, dy = t[0].clientY - t[1].clientY; return Math.sqrt(dx * dx + dy * dy) || 1; }
  shell.addEventListener('touchstart', function (e) {
    if (e.touches.length === 2) {
      abortGesture();
      pinch = { d0: span(e.touches), s0: state.scale, moved: false };
      if (e.cancelable) { e.preventDefault(); }
    }
  }, { passive: false });
  shell.addEventListener('touchmove', function (e) {
    if (pinch && e.touches.length >= 2) {
      if (e.cancelable) { e.preventDefault(); }
      setScale(clamp(pinch.s0 * span(e.touches) / pinch.d0, 0.8, 1.75), false);
      pinch.moved = true;
      return;
    }
    if (G && G.mode === 'drag' && e.cancelable) { e.preventDefault(); }
  }, { passive: false });
  function endPinch(e) {
    if (pinch && e.touches.length < 2) {
      if (pinch.moved) { save(); }
      pinch = null;
      suppressUntil = now() + 500;
    }
  }
  shell.addEventListener('touchend', endPinch, { passive: true });
  shell.addEventListener('touchcancel', endPinch, { passive: true });
  shell.addEventListener('gesturestart', function (e) { e.preventDefault(); });
  shell.addEventListener('gesturechange', function (e) { e.preventDefault(); });

  /* ---------- appearance sheet ---------- */
  var sizeBtns = Array.prototype.slice.call(doc.querySelectorAll('#sizes button'));
  var sortBtns = Array.prototype.slice.call(doc.querySelectorAll('#sorts button'));
  var resetBtn = $('btn-reset'), resetTimer = 0;
  function setScale(s, silent) {
    state.scale = clamp(s, 0.8, 1.75);
    root.style.setProperty('--s', state.scale.toFixed(3));
    var best = 0, bd = 9;
    SCALES.forEach(function (v, i) { var d = Math.abs(v - state.scale); if (d < bd) { bd = d; best = i; } });
    sizeBtns.forEach(function (b, i) { b.setAttribute('aria-checked', i === best && bd < 0.07 ? 'true' : 'false'); });
    if (!silent) { sizeDock(); }
  }
  function paintSortUI() {
    sortBtns.forEach(function (b) { b.setAttribute('aria-checked', b.getAttribute('data-sort') === state.sort ? 'true' : 'false'); });
  }
  function setSheet(open) {
    sheet.hidden = !open;
    viewBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  viewBtn.addEventListener('click', function () { setSheet(sheet.hidden); });
  doc.addEventListener('pointerdown', function (e) {
    if (!sheet.hidden && !sheet.contains(e.target) && !viewBtn.contains(e.target)) { setSheet(false); }
  });
  sizeBtns.forEach(function (b, i) {
    b.addEventListener('click', function () { setScale(SCALES[i], false); save(); });
  });
  sortBtns.forEach(function (b) {
    b.addEventListener('click', function () {
      var next = b.getAttribute('data-sort');
      state.sort = next; paintSortUI(); save(); render({ animate: true });
      announce('Sorted: ' + b.textContent);
    });
  });
  resetBtn.addEventListener('click', function () {
    if (!resetBtn.classList.contains('armed')) {
      resetBtn.classList.add('armed'); resetBtn.textContent = 'Tap again to reset';
      clearTimeout(resetTimer);
      resetTimer = setTimeout(function () { resetBtn.classList.remove('armed'); resetBtn.textContent = 'Reset layout'; }, 3200);
      return;
    }
    resetBtn.classList.remove('armed'); resetBtn.textContent = 'Reset layout';
    state.order = []; state.sort = 'custom'; state.usage = {}; state.hint = 1;
    setScale(1, false); paintSortUI(); paintHint(); flush(); render({ animate: true });
    toast('Layout reset');
  });

  /* ---------- keyboard ---------- */
  function columnCount(list) {
    if (!list.length) { return 1; }
    var top = list[0].getBoundingClientRect().top, n = 0;
    for (var i = 0; i < list.length; i++) { if (Math.abs(list[i].getBoundingClientRect().top - top) < 4) { n++; } else { break; } }
    return Math.max(1, n);
  }
  grid.addEventListener('keydown', function (e) {
    var li = cellOf(e.target);
    if (!li) { return; }
    var list = visibleCells(), i = list.indexOf(li), cols = columnCount(list), j = i;
    if (e.key === ' ') { e.preventDefault(); openApp(li.getAttribute('data-name')); return; }
    if (e.key === 'ArrowRight') { j = i + 1; } else if (e.key === 'ArrowLeft') { j = i - 1; }
    else if (e.key === 'ArrowDown') { j = i + cols; } else if (e.key === 'ArrowUp') { j = i - cols; }
    else if (e.key === 'Home') { j = 0; } else if (e.key === 'End') { j = list.length - 1; }
    else { return; }
    e.preventDefault();
    j = clamp(j, 0, list.length - 1);
    if (j === i) { return; }
    if (e.shiftKey && e.key.indexOf('Arrow') === 0) {
      if (!canReorder()) { toast('Clear the search and filter to rearrange apps.'); return; }
      var before = measure(list);
      list.splice(i, 1); list.splice(j, 0, li);
      list.forEach(function (c, k) { if (grid.children[k] !== c) { grid.insertBefore(c, grid.children[k] || null); } });
      flip(before, list);
      state.order = list.map(function (c) { return c.getAttribute('data-name'); });
      state.sort = 'custom'; state.hint = 0; paintSortUI(); paintHint(); save();
      announce('Moved ' + apps[li.getAttribute('data-name')].title + ' to position ' + (j + 1) + ' of ' + list.length + '.');
      li.querySelector('a.app').focus();
      return;
    }
    var next = list[j];
    setFocus(next.getAttribute('data-name'));
    next.querySelector('a.app').focus();
  });
  doc.addEventListener('keydown', function (e) {
    var typing = e.target && /^(input|textarea)$/i.test(e.target.tagName || '');
    if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); qInput.focus(); qInput.select(); return; }
    if (e.key !== 'Escape') { return; }
    if (G && G.mode === 'drag') { endGesture(true); return; }
    if (!sheet.hidden) { setSheet(false); viewBtn.focus(); return; }
    if (qInput.value) { qInput.value = ''; onQuery(); }
  });

  /* ---------- search: instant here, ranked by the server's QUERY ---------- */
  var searchTimer = 0, searchSerial = 0, searchAbort = null;
  function slowLink() {
    try {
      var c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      return !!c && (!!c.saveData || /(^|-)2g$/.test(String(c.effectiveType || '')));
    } catch (_) { return false; }
  }
  function onQuery() {
    view.query = qInput.value;
    qClear.hidden = !qInput.value;
    clearTimeout(searchTimer);
    searchSerial += 1;
    if (searchAbort) { searchAbort.abort(); searchAbort = null; }
    view.remote = null;
    var q = norm(view.query);
    render({ animate: true });
    if (q.length < 2) { return; }
    searchTimer = setTimeout(function () { runQuery(view.query.trim(), q); }, slowLink() ? 520 : 200);
  }
  function runQuery(text, q) {
    var serial = ++searchSerial;
    var init = { method: 'QUERY', headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ query: text, limit: 50 }) };
    if (win.AbortController) { searchAbort = new win.AbortController(); init.signal = searchAbort.signal; }
    fetch('/api/websites', init).then(function (r) {
      if (!r.ok) { throw new Error('HTTP ' + r.status); }
      return r.json();
    }).then(function (p) {
      if (serial !== searchSerial || !p || !Array.isArray(p.frontends)) { return; }
      view.remote = p.frontends.map(function (f) { return String(f.agent_name || ''); });
      view.remoteQ = q;
      render({ animate: true });
    }).catch(function (err) {
      if (err && err.name === 'AbortError') { return; }
    });
  }
  qInput.addEventListener('input', onQuery);
  qInput.addEventListener('search', onQuery);
  qInput.closest('form').addEventListener('submit', function (e) {
    e.preventDefault();
    qInput.blur();
    var first = visibleNames()[0];
    if (first) { setFocus(first); }
  });
  qClear.addEventListener('click', function () { qInput.value = ''; onQuery(); qInput.focus(); });
  emptyEl.querySelector('button').addEventListener('click', function () {
    qInput.value = ''; view.cat = 'all'; onQuery();
  });

  /* ---------- keeping the list live ---------- */
  var refreshing = false, offline = false, pollTimer = 0;
  function setOffline(flag) {
    offline = flag;
    noteEl.hidden = !flag;
  }
  function applyPayload(p) {
    if (!p || !Array.isArray(p.frontends)) { return; }
    if (p.autoyou_browser_base_url) { boot.base = p.autoyou_browser_base_url; }
    if (p.server_name !== undefined) { boot.server_name = p.server_name; }
    if (Array.isArray(p.categories)) { boot.categories = p.categories; }
    var list = p.frontends.map(toApp).filter(function (a) { return a.name; });
    var sig = signature(list);
    if (sig === lastSig) { return; }
    lastSig = sig;
    var next = Object.create(null);
    list.forEach(function (a) { next[a.name] = a; });
    apps = next;
    Object.keys(cells).forEach(function (n) { if (apps[n]) { paintCell(cells[n], apps[n]); } });
    shownKey = '';
    render({ animate: true });
  }
  function refresh() {
    if (refreshing) { return Promise.resolve(); }
    refreshing = true;
    refreshBtn.classList.add('is-busy');
    return fetch('/api/websites', { headers: { 'Accept': 'application/json' }, cache: 'no-store' }).then(function (r) {
      if (!r.ok) { throw new Error('HTTP ' + r.status); }
      return r.json();
    }).then(function (p) { setOffline(false); applyPayload(p); }, function () { setOffline(true); }).then(function () {
      refreshing = false;
      refreshBtn.classList.remove('is-busy');
      schedulePoll();
    });
  }
  function schedulePoll() {
    clearTimeout(pollTimer);
    if (slowLink() || doc.visibilityState === 'hidden') { return; }
    pollTimer = setTimeout(function () { if (!G && !pinch) { refresh(); } else { schedulePoll(); } }, offline ? 8000 : 25000);
  }
  refreshBtn.addEventListener('click', function () { refresh().then(function () { toast(offline ? 'Could not reach the computer.' : 'Apps are up to date.'); }); });

  /* ---------- the search bar gets a backing once it sticks ---------- */
  var stuck = false;
  function checkStuck() {
    var now = bar.getBoundingClientRect().top <= 1 && (win.pageYOffset || 0) > 4;
    if (now !== stuck) { stuck = now; bar.classList.toggle('is-stuck', stuck); }
  }
  win.addEventListener('scroll', checkStuck, { passive: true });

  /* ---------- start ---------- */
  function start() {
    adoptServerCells();
    setScale(state.scale, true);
    paintSortUI();
    paintHint();
    var preset = String(boot.query || '');
    if (preset) { qInput.value = preset; view.query = preset; qClear.hidden = false; }
    render({});
    sizeDock();
    if (preset) { onQuery(); }
    setTimeout(refresh, 450);
  }
  start();
  if (nativeLayout) {
    nativeGet().then(function (raw) {
      var remote = parse(raw), mine = loadLocal();
      if (remote && (!mine || remote.t > mine.t)) { adopt(remote); render({ animate: false }); }
      else if (!remote && mine) { nativeSet(JSON.stringify(mine)); }
      root.classList.remove('is-pending');
    });
    setTimeout(function () { root.classList.remove('is-pending'); }, 700);
  }
})();
"""
