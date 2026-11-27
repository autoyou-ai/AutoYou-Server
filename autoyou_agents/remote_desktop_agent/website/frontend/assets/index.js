// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

'use strict';

var B = window.__BOOTSTRAP__ || {};
var agentName = B.agent_name || 'remote_desktop_agent';
var auth = B.auth || {};

var state = {
  activeMode: 'view_desktop',
  touchMode: 'direct', // direct | trackpad
  monitors: [],
  windows: [],
  selectedMonitor: 0,
  selectedWindow: null,
  scale: 0.50,
  quality: 50,
  fps: 6,
  ws: null,
  wsConnected: false,
  hasRenderedFrame: false,
  lastFrameTime: null,
  frameTimes: [],
  wsReconnectTimer: null,
  snapshotFallbackTimer: null,
  snapshotPollTimer: null,
  snapshotInFlight: false,
  usingSnapshotFallback: false,
  mouseThrottleTimer: null,
  isControlFocused: false,
  inlineFullscreen: false,
  isStreamDesired: true,
  streamKind: 'screen',
  hasMobileVideoFrame: false,
  captureIssue: null, // { reason, message, can_wake, permission_required }
  nativeKeyboardConnected: false,
  nativeKeyboardOpen: false,
  nativeKeyboardState: 'inactive',
  nativeKeyboardControlId: '',
  nativeKeyboardRequestPending: false,
  nativeKeyboardStatusTimer: null,
  monitorLoadAttempts: 0,
  windowLoadAttempts: 0,
  viewportKeyboardOpen: false,
  keyboardBubbleCollapsed: false,
};

function $(id) { return document.getElementById(id); }
function showEl(el) { if (el) el.classList.remove('hidden'); }
function hideEl(el) { if (el) el.classList.add('hidden'); }

function ensureTrailingSlash(url) {
  var raw = String(url || '');
  if (!raw) return raw;
  return /\/([?#].*)?$/.test(raw) ? raw : raw.replace(/([?#].*)?$/, '/$1');
}

function buildRelativeUrl(path) {
  return new URL(String(path || '').replace(/^\.\//, ''), ensureTrailingSlash(window.location.href));
}

function buildScreenWebSocketUrl() {
  var wsUrl = buildRelativeUrl('ws/screen');
  wsUrl.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return wsUrl.toString();
}

function buildMobileVideoWebSocketUrl() {
  var wsUrl = buildRelativeUrl('ws/mobile-video');
  wsUrl.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return wsUrl.toString();
}

function streamKindForMode(mode) {
  return mode === 'view_mobile_video' ? 'mobile_video' : 'screen';
}

function proxyRootPrefix() {
  var pathname = String((window.location && window.location.pathname) || '');
  var marker = '/agent/';
  var idx = pathname.indexOf(marker);
  return idx < 0 ? '' : pathname.slice(0, idx);
}

function resolveProxyPath(path) {
  var raw = String(path || '').trim();
  if (!raw) return raw;
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.startsWith('//') || !raw.startsWith('/')) return raw;
  var root = proxyRootPrefix();
  if (!root || raw === root || raw.startsWith(root + '/')) return raw;
  return root + raw;
}

function resolveAgentRoute(agent) {
  return resolveProxyPath('/agent/' + encodeURIComponent(String(agent || '').trim()) + '/');
}

function hydrateTopbarLinks() {
  document.querySelectorAll('[data-agent-link]').forEach(function(el) {
    var targetAgent = String(el.getAttribute('data-agent-link') || '').trim();
    if (targetAgent) el.href = resolveAgentRoute(targetAgent);
  });
}

// ── Viewport Metrics ────────────────────────────────────────────────────────
function syncViewportMetrics() {
  var vv = window.visualViewport;
  var vh = vv && Number.isFinite(vv.height) ? vv.height : window.innerHeight;
  if (vh > 0) document.documentElement.style.setProperty('--app-height', Math.round(vh) + 'px');
  var lh = Math.max(window.innerHeight || 0, vh || 0);
  var keyboardOpen = !!(vv && (lh - vh) > Math.max(120, lh * 0.18));
  state.viewportKeyboardOpen = keyboardOpen;
  if (document.body) document.body.classList.toggle('viewport-keyboard-open', keyboardOpen);
}

function initViewportMetrics() {
  syncViewportMetrics();
  window.addEventListener('resize', syncViewportMetrics);
  window.addEventListener('orientationchange', function() { window.setTimeout(syncViewportMetrics, 120); });
  if (window.visualViewport && window.visualViewport.addEventListener) {
    window.visualViewport.addEventListener('resize', syncViewportMetrics);
    window.visualViewport.addEventListener('scroll', syncViewportMetrics);
  }
}

// ── Auth & API Helpers ──────────────────────────────────────────────────────
var _SESSION_TOKEN_KEY = 'autoyou_chat_token_' + agentName;
function _storedToken() { try { return localStorage.getItem(_SESSION_TOKEN_KEY) || ''; } catch (_) { return ''; } }
function _saveToken(t) { try { if (t) localStorage.setItem(_SESSION_TOKEN_KEY, t); else localStorage.removeItem(_SESSION_TOKEN_KEY); } catch (_) {} }

function _authHeaders(includeJson) {
  var h = {};
  if (includeJson !== false) h['Content-Type'] = 'application/json';
  var t = _storedToken();
  if (t) h['Authorization'] = 'Bearer ' + t;
  return h;
}

function apiPost(path, body) {
  return fetch(path, {
    method: 'POST',
    headers: _authHeaders(true),
    body: JSON.stringify(body || {}),
  }).then(function(r) { return r.json(); });
}

function apiGet(path) {
  var t = _storedToken();
  var opts = t ? { headers: { 'Authorization': 'Bearer ' + t } } : undefined;
  return fetch(path, opts).then(function(r) { return r.json(); });
}

// ── Theme ───────────────────────────────────────────────────────────────────
function _getTheme() { try { return localStorage.getItem('autoyou.ui.theme') || 'dark'; } catch (_) { return 'dark'; } }
function _setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('autoyou.ui.theme', t); } catch (_) {}
}
function _initTheme() {
  _setTheme(_getTheme());
  var btn = $('theme-toggle');
  if (btn) btn.addEventListener('click', function() {
    _setTheme(_getTheme() === 'dark' ? 'light' : 'dark');
  });
}

// ── Native Keyboard Bridge ──────────────────────────────────────────────────
function updateNativeKeyboardUi(message, stateName) {
  var status = $('native-keyboard-status');
  var statusText = $('native-keyboard-status-text');
  var connected = state.nativeKeyboardConnected;
  var active = state.nativeKeyboardOpen;
  var hidden = state.nativeKeyboardState === 'hidden';
  if (document.body) document.body.classList.toggle('native-keyboard-open', active);
  if (status) {
    status.dataset.state = stateName || (active ? 'active' : (hidden ? 'hidden' : (connected ? 'connected' : 'disconnected')));
  }
  if (statusText) {
    statusText.textContent = message || (active ? 'Keyboard active' : (hidden ? 'Keyboard hidden' : (connected ? 'Keyboard ready' : 'Keyboard idle')));
  }
  updateFloatingKeyboardBubbleUi();
}

function pollNativeKeyboardStatus() {
  if (!auth.authenticated) return;
  apiGet('./api/remote_desktop/native-keyboard/status').then(function(data) {
    var proof = data && data.native_keyboard;
    state.nativeKeyboardConnected = !!(data && data.success && proof && proof.connected);
    if (!state.nativeKeyboardConnected) {
      state.nativeKeyboardOpen = false;
      state.nativeKeyboardState = 'inactive';
      state.nativeKeyboardControlId = '';
    } else {
      var ks = String((proof && proof.keyboard_state) || '').trim().toLowerCase();
      if (ks === 'visible' || ks === 'hidden' || ks === 'inactive') {
        state.nativeKeyboardState = ks;
        state.nativeKeyboardOpen = ks === 'visible';
        if (ks === 'inactive') state.nativeKeyboardControlId = '';
      }
    }
    updateNativeKeyboardUi();
  }).catch(function() {
    state.nativeKeyboardConnected = false;
    state.nativeKeyboardOpen = false;
    state.nativeKeyboardState = 'inactive';
    state.nativeKeyboardControlId = '';
    updateNativeKeyboardUi('Keyboard idle', 'disconnected');
  });
}

function requestNativeKeyboard(action) {
  if (state.nativeKeyboardRequestPending) return Promise.resolve(false);
  if (action === 'show' && (!state.nativeKeyboardConnected || state.activeMode !== 'control_desktop')) {
    return Promise.resolve(false);
  }
  state.nativeKeyboardRequestPending = true;
  state.nativeKeyboardState = action === 'show' ? 'visible' : 'inactive';
  if (action === 'hide') {
    state.nativeKeyboardOpen = false;
    state.nativeKeyboardControlId = '';
  }
  updateNativeKeyboardUi(action === 'show' ? 'Opening keyboard…' : 'Closing keyboard…');
  return apiPost('./api/remote_desktop/native-keyboard', {
    action: action,
    control_id: state.nativeKeyboardControlId,
  }).then(function(data) {
    if (!data || !data.success) throw new Error((data && (data.reason || data.error)) || 'Keyboard unavailable');
    if (action === 'show') {
      state.nativeKeyboardOpen = true;
      state.nativeKeyboardState = 'visible';
      state.nativeKeyboardControlId = String(data.control_id || state.nativeKeyboardControlId || '');
    } else {
      state.nativeKeyboardOpen = false;
      state.nativeKeyboardState = 'inactive';
      state.nativeKeyboardControlId = '';
    }
    updateNativeKeyboardUi();
    return true;
  }).catch(function() {
    state.nativeKeyboardOpen = false;
    state.nativeKeyboardState = 'inactive';
    state.nativeKeyboardControlId = '';
    updateNativeKeyboardUi('Keyboard idle', 'disconnected');
    return false;
  }).finally(function() {
    state.nativeKeyboardRequestPending = false;
    updateNativeKeyboardUi();
  });
}

function initNativeKeyboardHandler() {
  updateNativeKeyboardUi();
  if (state.nativeKeyboardStatusTimer) clearInterval(state.nativeKeyboardStatusTimer);
  state.nativeKeyboardStatusTimer = setInterval(pollNativeKeyboardStatus, 3500);
}

// ── Floating Keyboard Bubble ────────────────────────────────────────────────
var _KB_COLLAPSED_KEY = 'autoyou.ui.keyboardBubbleCollapsed';
var _KB_POS_KEY = 'autoyou.ui.keyboardBubblePos';

function updateFloatingKeyboardBubbleUi() {
  var bubble = $('floating-keyboard-bubble');
  var handle = $('floating-keyboard-handle');
  var mainBtn = $('floating-keyboard-main');
  if (!bubble || !handle || !mainBtn) return;
  var eligible = state.activeMode === 'control_desktop' && state.nativeKeyboardConnected;
  bubble.classList.toggle('hidden', !eligible || state.keyboardBubbleCollapsed);
  handle.classList.toggle('hidden', !eligible || !state.keyboardBubbleCollapsed);
  mainBtn.disabled = !state.nativeKeyboardConnected || state.nativeKeyboardRequestPending;
  mainBtn.setAttribute('aria-pressed', state.nativeKeyboardOpen ? 'true' : 'false');
  mainBtn.classList.toggle('active', state.nativeKeyboardOpen);
}

function initFloatingKeyboardBubble() {
  var wrapper = $('canvas-wrapper');
  var bubble = $('floating-keyboard-bubble');
  var mainBtn = $('floating-keyboard-main');
  var collapseBtn = $('floating-keyboard-collapse');
  var handle = $('floating-keyboard-handle');
  if (!wrapper || !bubble || !mainBtn || !collapseBtn || !handle) return;

  try { state.keyboardBubbleCollapsed = localStorage.getItem(_KB_COLLAPSED_KEY) === '1'; } catch (_) {}

  var EDGE = 14;
  function applyPos(xr, yr) {
    var r = wrapper.getBoundingClientRect();
    var w = bubble.offsetWidth || 50;
    var h = bubble.offsetHeight || 50;
    var maxX = Math.max(0, r.width - w - EDGE * 2);
    var maxY = Math.max(0, r.height - h - EDGE * 2);
    bubble.style.left = (EDGE + Math.max(0, Math.min(maxX, xr * maxX))) + 'px';
    bubble.style.top = (EDGE + Math.max(0, Math.min(maxY, yr * maxY))) + 'px';
    bubble.style.right = 'auto';
    bubble.style.bottom = 'auto';
  }

  try {
    var saved = JSON.parse(localStorage.getItem(_KB_POS_KEY) || 'null');
    if (saved && typeof saved.xr === 'number' && typeof saved.yr === 'number') applyPos(saved.xr, saved.yr);
    else applyPos(0, 1);
  } catch (_) { applyPos(0, 1); }

  var drag = null;
  bubble.addEventListener('pointerdown', function(e) {
    if (e.target === collapseBtn || collapseBtn.contains(e.target)) return;
    drag = { sx: e.clientX, sy: e.clientY, ox: bubble.offsetLeft, oy: bubble.offsetTop, moved: false };
    try { bubble.setPointerCapture(e.pointerId); } catch (_) {}
  });
  bubble.addEventListener('pointermove', function(e) {
    if (!drag) return;
    var dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
    if (!drag.moved && Math.hypot(dx, dy) < 8) return;
    drag.moved = true;
    var r = wrapper.getBoundingClientRect();
    var w = bubble.offsetWidth || 50, h = bubble.offsetHeight || 50;
    bubble.style.left = Math.max(EDGE, Math.min(r.width - w - EDGE, drag.ox + dx)) + 'px';
    bubble.style.top = Math.max(EDGE, Math.min(r.height - h - EDGE, drag.oy + dy)) + 'px';
  });
  function endDrag(e) {
    if (!drag) return;
    var wasMoved = drag.moved;
    try { bubble.releasePointerCapture(e.pointerId); } catch (_) {}
    drag = null;
    if (wasMoved) {
      var r = wrapper.getBoundingClientRect();
      var maxX = Math.max(1, r.width - (bubble.offsetWidth || 50) - EDGE * 2);
      var maxY = Math.max(1, r.height - (bubble.offsetHeight || 50) - EDGE * 2);
      try {
        localStorage.setItem(_KB_POS_KEY, JSON.stringify({
          xr: Math.max(0, Math.min(1, (bubble.offsetLeft - EDGE) / maxX)),
          yr: Math.max(0, Math.min(1, (bubble.offsetTop - EDGE) / maxY)),
        }));
      } catch (_) {}
    }
  }
  bubble.addEventListener('pointerup', endDrag);
  bubble.addEventListener('pointercancel', endDrag);

  mainBtn.addEventListener('click', function() {
    if (drag && drag.moved) return;
    requestNativeKeyboard(state.nativeKeyboardOpen ? 'hide' : 'show');
  });
  collapseBtn.addEventListener('click', function(e) {
    e.stopPropagation();
    state.keyboardBubbleCollapsed = true;
    try { localStorage.setItem(_KB_COLLAPSED_KEY, '1'); } catch (_) {}
    updateFloatingKeyboardBubbleUi();
  });
  handle.addEventListener('click', function() {
    state.keyboardBubbleCollapsed = false;
    try { localStorage.setItem(_KB_COLLAPSED_KEY, '0'); } catch (_) {}
    updateFloatingKeyboardBubbleUi();
  });
  updateFloatingKeyboardBubbleUi();
}

// ── Auth Gate ───────────────────────────────────────────────────────────────
function _applyAuthenticated() {
  auth.authenticated = true;
  var chip = $('auth-chip');
  if (chip) { chip.textContent = 'Ready'; chip.className = 'status-chip authenticated'; }
  if (auth.required !== false) showEl($('logout-btn'));
  hideEl($('login-gate'));
  showEl($('main-layout'));
  loadMonitors();
  loadWindows();
  pollNativeKeyboardStatus();
  startScreenStream();
}

function _showLoginGate(chip) {
  if (chip) { chip.textContent = 'Locked'; chip.className = 'status-chip locked'; }
  hideEl($('logout-btn'));
  showEl($('login-gate'));
  hideEl($('main-layout'));
  var input = $('otp-input'), btn = $('otp-submit'), errEl = $('otp-error');
  function attempt() {
    var code = (input.value || '').trim();
    if (code.length < 6) return;
    btn.disabled = true;
    apiPost('./api/auth/login', { code: code }).then(function(data) {
      if (data.success) {
        if (data.token) _saveToken(data.token);
        _applyAuthenticated();
      } else {
        errEl.textContent = data.error || 'Invalid OTP code. Try again.';
        showEl(errEl);
        btn.disabled = false;
        input.value = '';
        input.focus();
      }
    }).catch(function() {
      errEl.textContent = 'Verification error. Please retry.';
      showEl(errEl);
      btn.disabled = false;
    });
  }
  btn.onclick = attempt;
  input.onkeydown = function(e) { if (e.key === 'Enter') attempt(); };
}

function initAuth() {
  var chip = $('auth-chip');
  if (auth.authenticated || auth.required === false) {
    _applyAuthenticated();
  } else if (_storedToken()) {
    apiGet('./api/auth/status').then(function(data) {
      if (data && data.auth && data.auth.authenticated) _applyAuthenticated();
      else { _saveToken(''); _showLoginGate(chip); }
    }).catch(function() { _showLoginGate(chip); });
  } else {
    _showLoginGate(chip);
  }

  var logoutBtn = $('logout-btn');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', function() {
      apiPost('./api/auth/logout', {}).finally(function() {
        _saveToken('');
        window.location.reload();
      });
    });
  }
}

// ── Monitors, Windows & Wake Display ────────────────────────────────────────
function applyDiagnostics(diag) {
  if (!diag || typeof diag !== 'object') return;
  if (diag.ok === false) {
    state.captureIssue = {
      reason: diag.reason || 'capture_unavailable',
      message: diag.message || 'Screen capture is temporarily unavailable.',
      can_wake: !!diag.can_wake,
      permission_required: !!diag.permission_required,
    };
  } else if (diag.ok === true) {
    state.captureIssue = null;
  }
  updateOverlayState();
}

function loadMonitors() {
  apiGet('./api/remote_desktop/monitors').then(function(data) {
    state.monitorLoadAttempts = 0;
    var sel = $('monitor-select');
    if (data && data.diagnostics) applyDiagnostics(data.diagnostics);
    if (!data || !data.success) {
      state.monitors = [];
      if (sel) sel.innerHTML = '<option value="">Display unavailable</option>';
      state.captureIssue = {
        reason: (data && data.diagnostics && data.diagnostics.reason) || 'capture_unavailable',
        message: (data && (data.message || (data.diagnostics && data.diagnostics.message))) || 'Desktop capture is unavailable on this host.',
        can_wake: !!(data && data.diagnostics && data.diagnostics.can_wake),
        permission_required: !!(data && data.diagnostics && data.diagnostics.permission_required),
      };
      updateOverlayState();
      return;
    }
    state.monitors = data.monitors || [];
    if (sel) {
      sel.innerHTML = '';
      state.monitors.forEach(function(mon) {
        var opt = document.createElement('option');
        opt.value = mon.id;
        opt.textContent = mon.name;
        sel.appendChild(opt);
      });
    }
    if (state.monitors.length > 0) {
      var exists = state.monitors.some(function(m) { return Number(m.id) === Number(state.selectedMonitor); });
      if (!exists) state.selectedMonitor = state.monitors[0].id;
      if (sel) sel.value = String(state.selectedMonitor);
    }
    updateStreamConfig();
  }).catch(function() {
    state.monitorLoadAttempts += 1;
    if (state.monitorLoadAttempts <= 3) {
      setTimeout(loadMonitors, 1200 * state.monitorLoadAttempts);
    }
  });
}

function loadWindows() {
  var select = $('app-select');
  if (!select) return;
  select.innerHTML = '<option value="">Choose an application window…</option>';
  apiGet('./api/remote_desktop/windows').then(function(data) {
    state.windowLoadAttempts = 0;
    if (!data || !data.success) {
      select.innerHTML = '<option value="">Windows unavailable</option>';
      return;
    }
    state.windows = data.windows || [];
    state.windows.forEach(function(win) {
      var opt = document.createElement('option');
      opt.value = win.id;
      opt.textContent = (win.title || 'Window') + ' (' + win.width + '×' + win.height + ')';
      select.appendChild(opt);
    });
  }).catch(function() {
    state.windowLoadAttempts += 1;
    if (state.windowLoadAttempts <= 2) setTimeout(loadWindows, 1500);
  });
}

function triggerWakeDisplay() {
  var wakeBtns = [$('overlay-wake-btn'), $('wake-display-sidebar-btn')];
  wakeBtns.forEach(function(b) { if (b) b.disabled = true; });
  apiPost('./api/remote_desktop/wake', {}).then(function(data) {
    if (data && data.diagnostics) applyDiagnostics(data.diagnostics);
    loadMonitors();
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) startScreenStream();
    else updateStreamConfig();
  }).catch(function() {}).finally(function() {
    wakeBtns.forEach(function(b) { if (b) b.disabled = false; });
  });
}

// ── Overlay & Status ────────────────────────────────────────────────────────
function updateOverlayState() {
  var overlay = $('canvas-overlay');
  var iconEl = $('overlay-icon');
  var titleEl = $('overlay-title');
  var msgEl = $('overlay-message');
  var wakeBtn = $('overlay-wake-btn');
  var retryBtn = $('overlay-retry-btn');
  var dot = $('viewer-status-dot');
  if (!overlay) return;

  hideEl(wakeBtn);
  hideEl(retryBtn);

  if (!state.isStreamDesired) {
    showEl(overlay);
    if (iconEl) iconEl.textContent = '⏸';
    if (titleEl) titleEl.textContent = 'Stream Paused';
    if (msgEl) msgEl.textContent = 'Tap Start in the toolbar to resume live display streaming.';
    return;
  }

  if (state.activeMode === 'view_mobile_video') {
    if (state.hasMobileVideoFrame) hideEl(overlay);
    else {
      showEl(overlay);
      if (iconEl) iconEl.textContent = '📹';
      if (titleEl) titleEl.textContent = 'Waiting for Mobile Camera';
      if (msgEl) msgEl.textContent = 'Start a video call from your paired iOS or Android device to view its feed here.';
    }
    return;
  }

  if (state.activeMode === 'view_app' && state.selectedWindow === null) {
    showEl(overlay);
    if (iconEl) iconEl.textContent = '🪟';
    if (titleEl) titleEl.textContent = 'Choose a Window';
    if (msgEl) msgEl.textContent = 'Open the sidebar menu and pick an application window to stream.';
    return;
  }

  if (state.captureIssue) {
    showEl(overlay);
    if (dot) dot.className = 'status-indicator warning';
    if (state.captureIssue.permission_required) {
      if (iconEl) iconEl.textContent = '🔐';
      if (titleEl) titleEl.textContent = 'Screen Recording Permission Needed';
      if (msgEl) msgEl.textContent = state.captureIssue.message;
      showEl(retryBtn);
    } else if (state.captureIssue.can_wake || state.captureIssue.reason === 'display_asleep') {
      if (iconEl) iconEl.textContent = '🌙';
      if (titleEl) titleEl.textContent = 'Mac Display is Asleep';
      if (msgEl) msgEl.textContent = state.captureIssue.message;
      showEl(wakeBtn);
      showEl(retryBtn);
    } else {
      if (iconEl) iconEl.textContent = '⚠️';
      if (titleEl) titleEl.textContent = 'Display Capture Unavailable';
      if (msgEl) msgEl.textContent = state.captureIssue.message;
      showEl(wakeBtn);
      showEl(retryBtn);
    }
    return;
  }

  if (!state.hasRenderedFrame) {
    showEl(overlay);
    if (iconEl) iconEl.textContent = '🖥️';
    if (titleEl) titleEl.textContent = state.wsConnected ? 'Receiving First Frame…' : 'Connecting to Host…';
    if (msgEl) msgEl.textContent = 'Establishing live display stream from your computer.';
    showEl(retryBtn);
    return;
  }

  hideEl(overlay);
}

// ── Frame Decoding & Rendering ──────────────────────────────────────────────
var decodeBusy = false;
var latestFrameBlob = null;
var activeDecodeGen = 0;

function renderFrameBytes(arrayBufferOrBlob, transportName) {
  latestFrameBlob = arrayBufferOrBlob instanceof Blob
    ? arrayBufferOrBlob
    : new Blob([arrayBufferOrBlob], { type: 'image/jpeg' });
  drainFrameQueue(transportName);
}

function drainFrameQueue(transportName) {
  if (decodeBusy || !latestFrameBlob) return;
  decodeBusy = true;
  var gen = activeDecodeGen;
  var blob = latestFrameBlob;
  latestFrameBlob = null;
  var url = URL.createObjectURL(blob);
  var img = new Image();
  var canvas = $('screen-canvas');
  var ctx = canvas ? canvas.getContext('2d') : null;

  function finish() {
    URL.revokeObjectURL(url);
    decodeBusy = false;
    if (latestFrameBlob) drainFrameQueue(transportName);
  }

  img.onload = function() {
    try {
      if (gen !== activeDecodeGen || !canvas || !ctx) return;
      if (canvas.width !== img.width || canvas.height !== img.height) {
        canvas.width = img.width;
        canvas.height = img.height;
        resetCanvasView();
      }
      ctx.drawImage(img, 0, 0);
      state.hasRenderedFrame = true;
      state.captureIssue = null;
      if (state.streamKind === 'mobile_video') state.hasMobileVideoFrame = true;
      updateOverlayState();

      var now = performance.now();
      if (state.lastFrameTime !== null) {
        var diff = now - state.lastFrameTime;
        state.frameTimes.push(diff);
        if (state.frameTimes.length > 20) state.frameTimes.shift();
        var sum = state.frameTimes.reduce(function(a, b) { return a + b; }, 0);
        var fps = sum > 0 ? Math.round(1000 * state.frameTimes.length / sum) : 0;
        if ($('diag-fps')) $('diag-fps').textContent = fps + ' FPS';
        if ($('header-fps-badge')) $('header-fps-badge').textContent = fps + ' FPS';
        if ($('diag-latency')) $('diag-latency').textContent = Math.round(diff) + ' ms';
      }
      state.lastFrameTime = now;
      if ($('diag-transport')) $('diag-transport').textContent = transportName || 'WebSocket';
      var dot = $('viewer-status-dot');
      if (dot) dot.className = 'status-indicator connected';
    } finally {
      finish();
    }
  };
  img.onerror = finish;
  img.src = url;
}

// ── Snapshot HTTP Fallback (if WS binary frames stall over a proxy) ─────────
function stopSnapshotFallback() {
  state.usingSnapshotFallback = false;
  if (state.snapshotFallbackTimer) { clearTimeout(state.snapshotFallbackTimer); state.snapshotFallbackTimer = null; }
  if (state.snapshotPollTimer) { clearTimeout(state.snapshotPollTimer); state.snapshotPollTimer = null; }
}

function pollSnapshotOnce() {
  if (!state.isStreamDesired || !state.usingSnapshotFallback || state.activeMode === 'view_mobile_video') return;
  if (state.snapshotInFlight) return;
  state.snapshotInFlight = true;
  var targetType = state.activeMode === 'view_app' ? 'window' : 'monitor';
  var targetId = state.activeMode === 'view_app' ? (state.selectedWindow || '') : String(state.selectedMonitor);
  var qs = '?target_type=' + encodeURIComponent(targetType)
    + '&target_id=' + encodeURIComponent(targetId)
    + '&scale=' + encodeURIComponent(String(state.scale))
    + '&quality=' + encodeURIComponent(String(state.quality))
    + '&_t=' + Date.now();
  var headers = _authHeaders(false);
  fetch('./api/remote_desktop/snapshot' + qs, { headers: headers })
    .then(function(resp) {
      if (resp.ok) {
        return resp.blob().then(function(blob) {
          renderFrameBytes(blob, 'HTTP Snapshot');
        });
      }
      return resp.json().catch(function() { return {}; }).then(function(err) {
        if (err && err.diagnostics) applyDiagnostics(err.diagnostics);
      });
    })
    .catch(function() {})
    .finally(function() {
      state.snapshotInFlight = false;
      if (state.usingSnapshotFallback && state.isStreamDesired) {
        var delayMs = Math.max(250, Math.round(1000 / Math.min(4, state.fps)));
        state.snapshotPollTimer = setTimeout(pollSnapshotOnce, delayMs);
      }
    });
}

function scheduleSnapshotFallback() {
  if (state.snapshotFallbackTimer) clearTimeout(state.snapshotFallbackTimer);
  if (state.activeMode === 'view_mobile_video') return;
  state.snapshotFallbackTimer = setTimeout(function() {
    if (!state.isStreamDesired || state.hasRenderedFrame || state.captureIssue) return;
    state.usingSnapshotFallback = true;
    pollSnapshotOnce();
  }, 3500);
}

// ── WebSocket Stream ────────────────────────────────────────────────────────
function startScreenStream() {
  if (!state.isStreamDesired) return;
  stopSnapshotFallback();

  if (state.ws) {
    var old = state.ws;
    state.ws = null;
    try { old.onclose = null; old.close(); } catch (_) {}
  }

  var streamKind = streamKindForMode(state.activeMode);
  var wsUrl = streamKind === 'mobile_video' ? buildMobileVideoWebSocketUrl() : buildScreenWebSocketUrl();
  state.streamKind = streamKind;
  state.wsConnected = false;
  state.hasRenderedFrame = false;
  state.hasMobileVideoFrame = false;
  state.lastFrameTime = null;
  state.frameTimes = [];
  activeDecodeGen += 1;
  latestFrameBlob = null;
  updateOverlayState();
  scheduleSnapshotFallback();

  var indicator = $('viewer-status-dot');
  var connText = $('diag-connection');
  var socket = new WebSocket(wsUrl);
  socket.binaryType = 'arraybuffer';
  state.ws = socket;

  socket.onopen = function() {
    if (state.ws !== socket) return;
    state.wsConnected = true;
    if (indicator) indicator.className = 'status-indicator connected';
    if (connText) { connText.textContent = 'Connected'; connText.className = 'info-val status-connected'; }
    if (state.wsReconnectTimer) { clearTimeout(state.wsReconnectTimer); state.wsReconnectTimer = null; }
    var token = _storedToken();
    if (token) {
      try { socket.send(JSON.stringify({ type: 'auth', token: token })); } catch (_) {}
    }
    updateStreamConfig();
    updateOverlayState();
  };

  socket.onmessage = function(event) {
    if (state.ws !== socket) return;
    if (typeof event.data === 'string') {
      var msg = null;
      try { msg = JSON.parse(event.data); } catch (_) {}
      if (!msg) return;
      if (msg.type === 'capture_status') {
        if (msg.ok) {
          state.captureIssue = null;
        } else {
          state.captureIssue = {
            reason: msg.reason || 'capture_unavailable',
            message: msg.message || 'Screen capture returned a blank frame.',
            can_wake: !!msg.can_wake,
            permission_required: !!msg.permission_required,
          };
        }
        updateOverlayState();
      } else if (msg.type === 'feed_state' && !msg.active) {
        activeDecodeGen += 1;
        latestFrameBlob = null;
        state.hasMobileVideoFrame = false;
        state.hasRenderedFrame = false;
        state.lastFrameTime = null;
        state.frameTimes = [];
        var canvas = $('screen-canvas');
        if (canvas) {
          var ctx = canvas.getContext('2d');
          ctx.fillStyle = '#04070d';
          ctx.fillRect(0, 0, canvas.width, canvas.height);
        }
        if ($('diag-fps')) $('diag-fps').textContent = '0 FPS';
        if ($('header-fps-badge')) $('header-fps-badge').textContent = '0 FPS';
        if ($('diag-latency')) $('diag-latency').textContent = '-- ms';
        updateOverlayState();
      }
      return;
    }

    stopSnapshotFallback();
    renderFrameBytes(event.data, 'WebSocket');
  };

  socket.onclose = function() {
    if (state.ws !== socket) return;
    state.wsConnected = false;
    if (indicator) indicator.className = 'status-indicator';
    if (connText) { connText.textContent = 'Reconnecting…'; connText.className = 'info-val status-disconnected'; }
    if (state.streamKind === 'mobile_video') state.hasMobileVideoFrame = false;
    if (state.isStreamDesired && !state.usingSnapshotFallback && state.activeMode !== 'view_mobile_video') {
      state.usingSnapshotFallback = true;
      pollSnapshotOnce();
    }
    if (state.isStreamDesired && !state.wsReconnectTimer) {
      state.wsReconnectTimer = setTimeout(function() {
        state.wsReconnectTimer = null;
        startScreenStream();
      }, 3000);
    }
  };
}

function updateStreamConfig() {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
  if (state.activeMode === 'view_mobile_video') {
    try { state.ws.send(JSON.stringify({ fps: state.fps, paused: false })); } catch (_) {}
    return;
  }
  var payload = {
    scale: state.scale,
    quality: state.quality,
    fps: state.fps,
    focus: false,
    target_type: state.activeMode === 'view_app' ? 'window' : 'monitor',
    target_id: state.activeMode === 'view_app' ? state.selectedWindow : state.selectedMonitor,
    paused: state.activeMode === 'view_app' && state.selectedWindow === null,
  };
  try { state.ws.send(JSON.stringify(payload)); } catch (_) {}
}

// ── Mode & Controls ─────────────────────────────────────────────────────────
function isTouchDevice() {
  return /Android|webOS|iPhone|iPad|iPod/i.test(navigator.userAgent || '') || Number(navigator.maxTouchPoints || 0) > 0;
}

function initModeHandlers() {
  var modeButtons = [$('tab-view-desktop'), $('tab-control-desktop'), $('tab-view-app'), $('tab-view-mobile-video')];

  modeButtons.forEach(function(btn) {
    if (!btn) return;
    btn.addEventListener('click', function() {
      modeButtons.forEach(function(b) { if (b) b.classList.remove('active'); });
      btn.classList.add('active');
      state.activeMode = btn.getAttribute('data-mode');
      var container = $('canvas-wrapper');
      var nextKind = streamKindForMode(state.activeMode);
      var isControl = state.activeMode === 'control_desktop';

      container.classList.toggle('control-mode', isControl);
      $('monitor-selection-panel').classList.toggle('hidden', state.activeMode === 'view_app' || state.activeMode === 'view_mobile_video');
      $('app-selection-panel').classList.toggle('hidden', state.activeMode !== 'view_app');
      $('zoom-controls').classList.toggle('hidden', !isControl);
      $('quick-keys-bar').classList.toggle('hidden', !isControl);
      $('touch-mode-toggle').classList.toggle('hidden', !isControl);

      var titleMap = {
        view_desktop: 'View Desktop',
        control_desktop: 'Control Desktop',
        view_app: 'App Window',
        view_mobile_video: 'Mobile Camera',
      };
      $('viewer-title').textContent = titleMap[state.activeMode] || 'Desktop Stream';
      if (isControl) focusCanvas();
      resetCanvasView();

      if (!isControl && (state.nativeKeyboardOpen || state.nativeKeyboardState === 'hidden')) {
        requestNativeKeyboard('hide');
      }
      updateNativeKeyboardUi();
      updateOverlayState();

      if (state.ws && state.streamKind !== nextKind) startScreenStream();
      else updateStreamConfig();

      if (window.innerWidth <= 820 && state.activeMode !== 'view_app') closeSidebar();
    });
  });

  // Default to control mode on touch devices & desktop so interaction works right away.
  var defaultControlBtn = $('tab-control-desktop');
  if (defaultControlBtn) defaultControlBtn.click();

  document.querySelectorAll('[data-touch-mode]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      state.touchMode = btn.getAttribute('data-touch-mode') || 'direct';
      document.querySelectorAll('[data-touch-mode]').forEach(function(b) {
        b.classList.toggle('active', b === btn);
      });
    });
  });

  if ($('refresh-apps-btn')) $('refresh-apps-btn').addEventListener('click', loadWindows);
  if ($('wake-display-sidebar-btn')) $('wake-display-sidebar-btn').addEventListener('click', triggerWakeDisplay);
  if ($('overlay-wake-btn')) $('overlay-wake-btn').addEventListener('click', triggerWakeDisplay);
  if ($('overlay-retry-btn')) $('overlay-retry-btn').addEventListener('click', function() {
    state.captureIssue = null;
    loadMonitors();
    startScreenStream();
  });

  $('monitor-select').addEventListener('change', function(e) {
    state.selectedMonitor = parseInt(e.target.value, 10) || 0;
    updateStreamConfig();
    if (window.innerWidth <= 820) closeSidebar();
  });

  $('app-select').addEventListener('change', function(e) {
    state.selectedWindow = e.target.value || null;
    updateOverlayState();
    updateStreamConfig();
    if (window.innerWidth <= 820 && state.selectedWindow !== null) closeSidebar();
  });
}

function initSliderHandlers() {
  var scaleSlider = $('scale-slider'), qualitySlider = $('quality-slider'), fpsSlider = $('fps-slider');
  function applyValues(scalePct, qualPct, fps) {
    state.scale = scalePct / 100.0;
    state.quality = qualPct;
    state.fps = fps;
    scaleSlider.value = String(scalePct);
    qualitySlider.value = String(qualPct);
    fpsSlider.value = String(fps);
    $('scale-val').textContent = scalePct + '%';
    $('quality-val').textContent = qualPct + '%';
    $('fps-val').textContent = fps + ' FPS';
    updateStreamConfig();
  }

  var presets = {
    mobile: [35, 35, 4],
    balanced: [50, 50, 6],
    sharp: [80, 75, 10],
  };
  document.querySelectorAll('[data-preset]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var p = presets[btn.getAttribute('data-preset')];
      if (!p) return;
      document.querySelectorAll('[data-preset]').forEach(function(b) { b.classList.toggle('active', b === btn); });
      applyValues(p[0], p[1], p[2]);
    });
  });

  scaleSlider.addEventListener('input', function(e) {
    state.scale = parseFloat(e.target.value) / 100.0;
    $('scale-val').textContent = e.target.value + '%';
    updateStreamConfig();
  });
  qualitySlider.addEventListener('input', function(e) {
    state.quality = parseInt(e.target.value, 10);
    $('quality-val').textContent = e.target.value + '%';
    updateStreamConfig();
  });
  fpsSlider.addEventListener('input', function(e) {
    state.fps = parseInt(e.target.value, 10);
    $('fps-val').textContent = e.target.value + ' FPS';
    updateStreamConfig();
  });
}

// ── Quick Keys & Text Input ─────────────────────────────────────────────────
function initQuickKeys() {
  document.querySelectorAll('.key-chip[data-key]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      transmitInputEvent({ type: 'keypress', key: btn.getAttribute('data-key') });
    });
  });
  document.querySelectorAll('.key-chip[data-hotkey]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var keys = String(btn.getAttribute('data-hotkey') || '').split(',').filter(Boolean);
      if (keys.length) transmitInputEvent({ type: 'hotkey', keys: keys });
    });
  });
  var toggleBtn = $('quick-type-toggle');
  var form = $('quick-type-form');
  var input = $('quick-type-input');
  if (toggleBtn && form && input) {
    toggleBtn.addEventListener('click', function() {
      form.classList.toggle('hidden');
      if (!form.classList.contains('hidden')) input.focus();
    });
    form.addEventListener('submit', function(e) {
      e.preventDefault();
      var text = input.value;
      if (!text) return;
      transmitInputEvent({ type: 'type', text: text });
      input.value = '';
    });
  }
}

// ── Input Capture & Canvas Zoom/Pan ─────────────────────────────────────────
function transmitInputEvent(payload) {
  if (state.activeMode === 'view_app') {
    if (state.selectedWindow === null) return;
    payload.target_type = 'window';
    payload.target_id = state.selectedWindow;
  } else {
    payload.target_type = 'monitor';
    payload.target_id = state.selectedMonitor;
  }
  apiPost('./api/remote_desktop/input', payload).catch(function() {});
}

function focusCanvas() {
  var canvas = $('screen-canvas');
  if (canvas) {
    canvas.tabIndex = 1000;
    try { canvas.focus({ preventScroll: true }); } catch (_) { canvas.focus(); }
    state.isControlFocused = true;
  }
}

function calculateRatios(event, canvas) {
  var rect = canvas.getBoundingClientRect();
  var x = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
  var y = Math.max(0, Math.min(1, (event.clientY - rect.top) / Math.max(1, rect.height)));
  return { x: x, y: y };
}

var canvasView = { scale: 1, tx: 0, ty: 0 };
function clampCanvasZoom(s) { return Math.max(1, Math.min(4, s)); }
function clampCanvasPan(wrapper) {
  var r = wrapper.getBoundingClientRect();
  var maxTx = (r.width * (canvasView.scale - 1)) / 2;
  var maxTy = (r.height * (canvasView.scale - 1)) / 2;
  canvasView.tx = Math.max(-maxTx, Math.min(maxTx, canvasView.tx));
  canvasView.ty = Math.max(-maxTy, Math.min(maxTy, canvasView.ty));
}
function applyCanvasViewTransform(canvas) {
  canvas.style.transform = (canvasView.scale === 1 && canvasView.tx === 0 && canvasView.ty === 0)
    ? ''
    : 'translate(' + canvasView.tx.toFixed(1) + 'px,' + canvasView.ty.toFixed(1) + 'px) scale(' + canvasView.scale.toFixed(3) + ')';
  if ($('zoom-level')) $('zoom-level').textContent = Math.round(canvasView.scale * 100) + '%';
  if ($('zoom-reset-btn')) $('zoom-reset-btn').classList.toggle('hidden', canvasView.scale <= 1.001 && canvasView.tx === 0 && canvasView.ty === 0);
}
function resetCanvasView() {
  canvasView.scale = 1; canvasView.tx = 0; canvasView.ty = 0;
  var c = $('screen-canvas');
  if (c) applyCanvasViewTransform(c);
}
function initZoomControls() {
  function step(delta) {
    var c = $('screen-canvas'), w = $('canvas-wrapper');
    if (!c || !w) return;
    canvasView.scale = clampCanvasZoom(canvasView.scale + delta);
    if (canvasView.scale <= 1.001) { canvasView.tx = 0; canvasView.ty = 0; }
    clampCanvasPan(w);
    applyCanvasViewTransform(c);
  }
  if ($('zoom-in-btn')) $('zoom-in-btn').addEventListener('click', function() { step(0.5); });
  if ($('zoom-out-btn')) $('zoom-out-btn').addEventListener('click', function() { step(-0.5); });
  if ($('zoom-reset-btn')) $('zoom-reset-btn').addEventListener('click', resetCanvasView);
}

function spawnTapRipple(wrapper, clientX, clientY, isLongPress) {
  var rect = wrapper.getBoundingClientRect();
  var el = document.createElement('div');
  el.className = 'tap-ripple' + (isLongPress ? ' long-press' : '');
  el.style.left = (clientX - rect.left) + 'px';
  el.style.top = (clientY - rect.top) + 'px';
  wrapper.appendChild(el);
  setTimeout(function() { el.remove(); }, 500);
}

function initInputCapture() {
  var canvas = $('screen-canvas');
  var wrapper = $('canvas-wrapper');
  var pointers = new Map();
  var tap = null, dragActive = false, dragMoved = false, pinch = null, longPressTimer = null;
  var lastTapAt = 0, lastTapPos = null, lastTrackpadPos = null;

  function clearLongPress() { if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; } }
  function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
  function mid(a, b) { return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }

  function reportCursor(cx, cy) {
    var r = calculateRatios({ clientX: cx, clientY: cy }, canvas);
    if ($('cursor-coords')) {
      $('cursor-coords').textContent = 'X: ' + Math.round(r.x * canvas.width) + ', Y: ' + Math.round(r.y * canvas.height)
        + ' (' + r.x.toFixed(2) + ', ' + r.y.toFixed(2) + ')';
    }
    return r;
  }

  function sendMove(cx, cy) {
    var r = reportCursor(cx, cy);
    if (state.activeMode !== 'control_desktop') return;
    if (!state.mouseThrottleTimer) {
      state.mouseThrottleTimer = setTimeout(function() {
        state.mouseThrottleTimer = null;
        transmitInputEvent({ type: 'mousemove', x: r.x, y: r.y });
      }, 50);
    }
  }

  canvas.addEventListener('pointerdown', function(e) {
    if (state.activeMode !== 'control_desktop') return;
    if (e.pointerType === 'mouse' && e.button === 2) {
      focusCanvas();
      var r = calculateRatios(e, canvas);
      transmitInputEvent({ type: 'click', x: r.x, y: r.y, button: 'right' });
      return;
    }
    if (e.pointerType === 'mouse' && e.button !== 0) return;

    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    try { canvas.setPointerCapture(e.pointerId); } catch (_) {}

    if (pointers.size === 1) {
      dragActive = false;
      dragMoved = false;
      tap = { x: e.clientX, y: e.clientY, pointerId: e.pointerId, pointerType: e.pointerType };
      lastTrackpadPos = { x: e.clientX, y: e.clientY };
      clearLongPress();
      if (e.pointerType === 'touch') {
        longPressTimer = setTimeout(function() {
          longPressTimer = null;
          if (tap && !dragMoved) {
            focusCanvas();
            if (state.touchMode === 'trackpad') {
              transmitInputEvent({ type: 'click', button: 'right' });
            } else {
              var r = calculateRatios({ clientX: tap.x, clientY: tap.y }, canvas);
              transmitInputEvent({ type: 'click', x: r.x, y: r.y, button: 'right' });
            }
            spawnTapRipple(wrapper, tap.x, tap.y, true);
            tap = null;
          }
        }, 460);
      }
    } else if (pointers.size === 2) {
      clearLongPress();
      tap = null;
      dragActive = false;
      var pts = Array.from(pointers.values());
      pinch = {
        startDist: Math.max(1, dist(pts[0], pts[1])),
        startScale: canvasView.scale,
        startMid: mid(pts[0], pts[1]),
        startTx: canvasView.tx,
        startTy: canvasView.ty,
        lastMid: mid(pts[0], pts[1]),
      };
    }
  });

  canvas.addEventListener('pointermove', function(e) {
    if (!pointers.has(e.pointerId)) {
      if (e.pointerType === 'mouse') sendMove(e.clientX, e.clientY);
      return;
    }
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (pointers.size >= 2 && pinch) {
      var pts = Array.from(pointers.values());
      var d = Math.max(1, dist(pts[0], pts[1]));
      var m = mid(pts[0], pts[1]);
      var nextScale = clampCanvasZoom(pinch.startScale * (d / pinch.startDist));
      var dy = m.y - pinch.lastMid.y;
      if (Math.abs(nextScale - canvasView.scale) < 0.015 && Math.abs(dy) > 5 && canvasView.scale <= 1.02) {
        transmitInputEvent({ type: 'scroll', dy: dy > 0 ? -60 : 60 });
      }
      canvasView.scale = nextScale;
      canvasView.tx = pinch.startTx + (m.x - pinch.startMid.x);
      canvasView.ty = pinch.startTy + (m.y - pinch.startMid.y);
      clampCanvasPan(wrapper);
      applyCanvasViewTransform(canvas);
      pinch.lastMid = m;
      return;
    }

    if (pointers.size === 1 && tap) {
      var dx = e.clientX - tap.x, dy = e.clientY - tap.y;
      if (!dragMoved && Math.hypot(dx, dy) > 8) {
        dragMoved = true;
        clearLongPress();
        if (state.touchMode === 'direct' && canvasView.scale <= 1.02) {
          dragActive = true;
          focusCanvas();
          var r = calculateRatios({ clientX: tap.x, clientY: tap.y }, canvas);
          transmitInputEvent({ type: 'mousedown', x: r.x, y: r.y, button: 'left' });
        }
      }
      if (state.touchMode === 'trackpad' && tap.pointerType === 'touch' && dragMoved) {
        var stepX = (e.clientX - lastTrackpadPos.x) * 2.1;
        var stepY = (e.clientY - lastTrackpadPos.y) * 2.1;
        lastTrackpadPos = { x: e.clientX, y: e.clientY };
        if (Math.hypot(stepX, stepY) >= 2) {
          transmitInputEvent({ type: 'moverel', dx: stepX, dy: stepY });
        }
      } else if (dragActive) {
        sendMove(e.clientX, e.clientY);
      } else if (dragMoved && canvasView.scale > 1.02) {
        canvasView.tx += (e.movementX || 0);
        canvasView.ty += (e.movementY || 0);
        clampCanvasPan(wrapper);
        applyCanvasViewTransform(canvas);
      } else {
        reportCursor(e.clientX, e.clientY);
      }
    }
  });

  function finishPointer(e) {
    clearLongPress();
    var wasTap = !!(tap && tap.pointerId === e.pointerId);
    pointers.delete(e.pointerId);
    try { canvas.releasePointerCapture(e.pointerId); } catch (_) {}
    if (pointers.size < 2) pinch = null;

    if (wasTap) {
      if (dragActive) {
        var rUp = calculateRatios(e, canvas);
        transmitInputEvent({ type: 'mouseup', x: rUp.x, y: rUp.y, button: 'left' });
      } else if (!dragMoved) {
        var now = Date.now();
        var isDouble = lastTapPos && (now - lastTapAt) < 320 && dist(lastTapPos, { x: e.clientX, y: e.clientY }) < 24;
        focusCanvas();
        if (state.touchMode === 'trackpad' && tap.pointerType === 'touch') {
          transmitInputEvent({ type: isDouble ? 'doubleclick' : 'click', button: 'left' });
        } else {
          var rClk = calculateRatios(e, canvas);
          transmitInputEvent({ type: isDouble ? 'doubleclick' : 'click', x: rClk.x, y: rClk.y, button: 'left' });
        }
        lastTapAt = isDouble ? 0 : now;
        lastTapPos = isDouble ? null : { x: e.clientX, y: e.clientY };
        spawnTapRipple(wrapper, e.clientX, e.clientY, false);
      }
      tap = null;
    }
    dragActive = false;
    dragMoved = false;
  }

  canvas.addEventListener('pointerup', finishPointer);
  canvas.addEventListener('pointercancel', finishPointer);
  canvas.addEventListener('contextmenu', function(e) { if (state.activeMode === 'control_desktop') e.preventDefault(); });
  canvas.addEventListener('wheel', function(e) {
    if (state.activeMode !== 'control_desktop') return;
    e.preventDefault();
    var dy = e.deltaY ? (e.deltaY < 0 ? 120 : -120) : 0;
    if (dy) transmitInputEvent({ type: 'scroll', dy: dy });
  }, { passive: false });

  window.addEventListener('keydown', function(e) {
    if (state.activeMode !== 'control_desktop' || !state.isControlFocused) return;
    if (document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
    if (e.key === 'Escape' || e.key === 'Esc') {
      state.isControlFocused = false;
      canvas.blur();
      return;
    }
    e.preventDefault();
    transmitInputEvent({ type: 'keydown', key: e.key === ' ' ? 'space' : e.key });
  });

  window.addEventListener('keyup', function(e) {
    if (state.activeMode !== 'control_desktop' || !state.isControlFocused) return;
    if (document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
    e.preventDefault();
    transmitInputEvent({ type: 'keyup', key: e.key === ' ' ? 'space' : e.key });
  });

  document.addEventListener('click', function(e) {
    if (e.target !== canvas && !canvas.contains(e.target)) state.isControlFocused = false;
  });
}

// ── Fullscreen, Stream Toggle & Sidebar ─────────────────────────────────────
function initFullscreenHandler() {
  var btn = $('toggle-fullscreen-btn'), exitBtn = $('fullscreen-exit-btn'), shell = $('viewer-shell');
  function setFs(on) {
    state.inlineFullscreen = !!on;
    document.body.classList.toggle('inline-viewer-fullscreen', state.inlineFullscreen);
    syncViewportMetrics();
    if (btn) btn.innerHTML = state.inlineFullscreen ? '<span>✕</span><span class="btn-label">Exit</span>' : '<span>⛶</span><span class="btn-label">Full</span>';
  }
  btn.addEventListener('click', function() {
    if (state.inlineFullscreen) {
      setFs(false);
      try { if (document.fullscreenElement) document.exitFullscreen(); } catch (_) {}
      return;
    }
    setFs(true);
    try { if (shell.requestFullscreen) shell.requestFullscreen().catch(function() {}); } catch (_) {}
  });
  if (exitBtn) exitBtn.addEventListener('click', function() {
    setFs(false);
    try { if (document.fullscreenElement) document.exitFullscreen(); } catch (_) {}
  });
  document.addEventListener('fullscreenchange', function() {
    if (!document.fullscreenElement && state.inlineFullscreen) setFs(false);
  });
}

function initStreamToggleHandler() {
  var btn = $('stream-toggle-btn');
  if (!btn) return;
  btn.addEventListener('click', function() {
    state.isStreamDesired = !state.isStreamDesired;
    if (state.isStreamDesired) {
      btn.className = 'btn-pill btn-stream streaming';
      btn.innerHTML = '<span class="btn-icon">⏸</span><span class="btn-label">Stop</span>';
      updateOverlayState();
      startScreenStream();
    } else {
      btn.className = 'btn-pill btn-stream stopped';
      btn.innerHTML = '<span class="btn-icon">▶</span><span class="btn-label">Start</span>';
      stopSnapshotFallback();
      if (state.ws) { try { state.ws.close(); } catch (_) {} state.ws = null; }
      if (state.wsReconnectTimer) { clearTimeout(state.wsReconnectTimer); state.wsReconnectTimer = null; }
      state.wsConnected = false;
      if ($('viewer-status-dot')) $('viewer-status-dot').className = 'status-indicator';
      if ($('diag-connection')) { $('diag-connection').textContent = 'Paused'; $('diag-connection').className = 'info-val status-disconnected'; }
      if ($('diag-fps')) $('diag-fps').textContent = '0 FPS';
      if ($('header-fps-badge')) $('header-fps-badge').textContent = '0 FPS';
      updateOverlayState();
    }
  });
}

function openSidebar() {
  if ($('sidebar')) $('sidebar').classList.add('active');
  if ($('sidebar-backdrop')) $('sidebar-backdrop').classList.add('active');
}

function closeSidebar() {
  if ($('sidebar')) $('sidebar').classList.remove('active');
  if ($('sidebar-backdrop')) $('sidebar-backdrop').classList.remove('active');
}

function initMobileSidebarHandler() {
  var toggleBtn = $('toggle-sidebar-btn'), closeBtn = $('close-sidebar-btn'), backdrop = $('sidebar-backdrop');
  if (toggleBtn) toggleBtn.addEventListener('click', function(e) {
    e.stopPropagation();
    if ($('sidebar').classList.contains('active')) closeSidebar(); else openSidebar();
  });
  if (closeBtn) closeBtn.addEventListener('click', closeSidebar);
  if (backdrop) backdrop.addEventListener('click', closeSidebar);
}

// ── Boot ────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
  _initTheme();
  initViewportMetrics();
  hydrateTopbarLinks();
  initAuth();
  initModeHandlers();
  initSliderHandlers();
  initQuickKeys();
  initInputCapture();
  initZoomControls();
  initFloatingKeyboardBubble();
  initNativeKeyboardHandler();
  initFullscreenHandler();
  initStreamToggleHandler();
  initMobileSidebarHandler();
});
