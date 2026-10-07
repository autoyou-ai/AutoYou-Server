// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

'use strict';

// Bootstrap variables injected by backend
var B = window.__BOOTSTRAP__ || {};
var agentName = B.agent_name || 'remote_desktop_agent';
var auth = B.auth || {};

// Main Client State
var state = {
  activeMode: 'view_desktop', // view_desktop | control_desktop | view_app | view_mobile_video
  monitors: [],
  windows: [],
  selectedMonitor: 0,
  selectedWindow: null,
  scale: 0.35,
  quality: 35,
  fps: 4,
  ws: null,
  lastFrameTime: null,
  frameTimes: [],
  wsReconnectTimer: null,
  mouseThrottleTimer: null,
  isControlFocused: false,
  inlineFullscreen: false,
  isStreamDesired: true,
  streamKind: 'screen',
  hasMobileVideoFrame: false,
  nativeKeyboardConnected: false,
  nativeKeyboardOpen: false,
  nativeKeyboardState: 'inactive',
  nativeKeyboardControlId: '',
  nativeKeyboardRequestPending: false,
  nativeKeyboardStatusTimer: null,
  nativeWebsite: false,
  nativeFrame: null,
  nativeControl: null,
  nativeControlPending: false,
  nativeConfigToken: '',
  nativeConfigKey: '',
  nativeConfigCounter: 0,
  nativeRendering: false,
  monitorLoadAttempts: 0,
  windowLoadAttempts: 0,
  viewportKeyboardOpen: false,
  keyboardBubbleCollapsed: false
};

// ── DOM Helpers ──────────────────────────────────────────────────────────────
function $(id) { return document.getElementById(id); }

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
  if (idx < 0) return '';
  return pathname.slice(0, idx);
}

function resolveProxyPath(path) {
  var raw = String(path || '').trim();
  if (!raw) return raw;
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.startsWith('//')) return raw;
  if (!raw.startsWith('/')) return raw;
  var root = proxyRootPrefix();
  if (!root) return raw;
  if (raw === root || raw.startsWith(root + '/')) return raw;
  return root + raw;
}

function resolveAgentRoute(agent) {
  return resolveProxyPath('/agent/' + encodeURIComponent(String(agent || '').trim()) + '/');
}

function hydrateTopbarLinks() {
  document.querySelectorAll('[data-agent-link]').forEach(function(el) {
    var targetAgent = String(el.getAttribute('data-agent-link') || '').trim();
    if (!targetAgent) return;
    el.href = resolveAgentRoute(targetAgent);
  });
}

// Mobile WebViews can resize the visual viewport before the native keyboard
// control lease/status catches up. Keep the page height and the layout state
// tied to the viewport so the canvas never shares the space with stale chrome.
function syncViewportMetrics() {
  var visualViewport = window.visualViewport;
  var visualHeight = visualViewport && Number.isFinite(visualViewport.height)
    ? visualViewport.height
    : window.innerHeight;
  if (visualHeight > 0) {
    document.documentElement.style.setProperty('--app-height', Math.round(visualHeight) + 'px');
  }

  var layoutHeight = Math.max(window.innerHeight || 0, visualHeight || 0);
  var keyboardThreshold = Math.max(120, layoutHeight * 0.18);
  var keyboardOpen = !!(visualViewport && layoutHeight - visualHeight > keyboardThreshold);
  state.viewportKeyboardOpen = keyboardOpen;
  if (document.body) {
    document.body.classList.toggle('viewport-keyboard-open', keyboardOpen);
  }
}

function initViewportMetrics() {
  syncViewportMetrics();
  window.addEventListener('resize', syncViewportMetrics);
  window.addEventListener('orientationchange', function() {
    window.setTimeout(syncViewportMetrics, 100);
  });
  if (window.visualViewport && window.visualViewport.addEventListener) {
    window.visualViewport.addEventListener('resize', syncViewportMetrics);
    window.visualViewport.addEventListener('scroll', syncViewportMetrics);
  }
}

// ── Authentication & Network Operations ──────────────────────────────────────
var _SESSION_TOKEN_KEY = 'autoyou_chat_token_' + agentName;
function _storedToken() { try { return localStorage.getItem(_SESSION_TOKEN_KEY) || ''; } catch(_) { return ''; } }
function _saveToken(t) { try { if (t) localStorage.setItem(_SESSION_TOKEN_KEY, t); else localStorage.removeItem(_SESSION_TOKEN_KEY); } catch(_) {} }

function _authHeaders() {
  var t = _storedToken();
  return t ? { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + t }
           : { 'Content-Type': 'application/json' };
}

function apiPost(path, body) {
  return fetch(path, {
    method: 'POST',
    headers: _authHeaders(),
    body: JSON.stringify(body)
  }).then(function(r) { return r.json(); });
}

function apiGet(path) {
  var t = _storedToken();
  var opts = t ? { headers: { 'Authorization': 'Bearer ' + t } } : undefined;
  return fetch(path, opts).then(function(r) { return r.json(); });
}

function updateNativeKeyboardUi(message, stateName) {
  var status = $('native-keyboard-status');
  var statusText = $('native-keyboard-status-text');
  var connected = state.nativeKeyboardConnected;
  var active = state.nativeKeyboardOpen;
  var hidden = state.nativeKeyboardState === 'hidden';
  if (document.body) {
    document.body.classList.toggle('native-keyboard-open', active);
  }
  if (status) {
    status.dataset.state = stateName || (active ? 'active' : (hidden ? 'hidden' : (connected ? 'connected' : 'disconnected')));
  }
  if (statusText) {
    statusText.textContent = message || (active ? 'Native keyboard active' : (hidden ? 'Native keyboard hidden - tap to reopen' : (connected ? 'Native client ready' : 'Native client unavailable')));
  }
  updateFloatingKeyboardBubbleUi();
}

function pollNativeKeyboardStatus() {
  if (!auth.authenticated) return;
  var viewId = state.nativeFrame && state.nativeFrame.source.call_id;
  apiGet('./api/remote_desktop/native-keyboard/status' + (viewId ? '?view_id=' + encodeURIComponent(viewId) : '')).then(function(data) {
    if (data && data.native_transport) state.nativeWebsite = true;
    var proof = data && data.native_keyboard;
    state.nativeKeyboardConnected = !!(data && data.success && proof && proof.connected);
    if (!state.nativeKeyboardConnected) {
      state.nativeKeyboardOpen = false;
      state.nativeKeyboardState = 'inactive';
      state.nativeKeyboardControlId = '';
    } else {
      var keyboardState = String((proof && proof.keyboard_state) || '').trim().toLowerCase();
      if (keyboardState === 'visible' || keyboardState === 'hidden' || keyboardState === 'inactive') {
        state.nativeKeyboardState = keyboardState;
        state.nativeKeyboardOpen = keyboardState === 'visible';
        if (keyboardState === 'inactive') state.nativeKeyboardControlId = '';
      }
    }
    var message = state.nativeKeyboardConnected
      ? (state.nativeKeyboardOpen ? 'Native keyboard active' : (state.nativeKeyboardState === 'hidden' ? 'Native keyboard hidden - tap to reopen' : 'Native client ready'))
      : 'Native client unavailable';
    updateNativeKeyboardUi(message);
  }).catch(function() {
    state.nativeKeyboardConnected = false;
    state.nativeKeyboardOpen = false;
    state.nativeKeyboardState = 'inactive';
    state.nativeKeyboardControlId = '';
    updateNativeKeyboardUi('Native client unavailable', 'disconnected');
  });
}

function requestNativeKeyboard(action) {
  if (state.nativeKeyboardRequestPending) return Promise.resolve(false);
  if (action === 'show' && (!state.nativeKeyboardConnected || state.activeMode !== 'control_desktop')) {
    return Promise.resolve(false);
  }
  state.nativeKeyboardRequestPending = true;
  var controlId = state.nativeKeyboardControlId;
  var payload = { action: action, control_id: controlId };
  if (state.nativeWebsite) {
    if (action === 'show') {
      if (!currentWebsiteFrame()) { state.nativeKeyboardRequestPending = false; return Promise.resolve(false); }
      controlId = crypto.randomUUID();
      payload = Object.assign(payload, { control_id: controlId, view_id: state.nativeFrame.source.call_id,
        source: state.nativeFrame.source, frame_sequence: state.nativeFrame.sequence });
    } else if (state.nativeControl) payload.view_id = state.nativeControl.view_id;
    state.nativeControl = null;
  }
  state.nativeKeyboardState = action === 'show' ? 'visible' : 'inactive';
  if (action === 'hide') {
    // Remove the page's secondary chrome immediately. The native client may
    // animate its own keyboard away after the HTTP request resolves.
    state.nativeKeyboardOpen = false;
    state.nativeKeyboardControlId = '';
  }
  updateNativeKeyboardUi(action === 'show' ? 'Opening native keyboard…' : 'Closing native keyboard…');
  var token = state.nativeConfigToken;
  return apiPost('./api/remote_desktop/native-keyboard', payload).then(function(data) {
    if (!data || !data.success) {
      throw new Error((data && (data.reason || data.error)) || 'Native keyboard control was not accepted.');
    }
    if (action === 'show') {
      if (state.nativeWebsite) {
        if (token !== state.nativeConfigToken || !currentWebsiteFrame() || data.view_id !== state.nativeFrame.source.call_id) {
          apiPost('./api/remote_desktop/native-keyboard', { action: 'hide', view_id: data.view_id, control_id: data.control_id });
          return false;
        }
        state.nativeControl = Object.assign(data, { writer: 'keyboard' });
      }
      state.nativeKeyboardOpen = true;
      state.nativeKeyboardState = 'visible';
      state.nativeKeyboardControlId = String(data.control_id || state.nativeKeyboardControlId || '');
    } else {
      state.nativeKeyboardOpen = false;
      state.nativeKeyboardState = 'inactive';
      state.nativeKeyboardControlId = '';
    }
    updateNativeKeyboardUi(action === 'show' ? 'Native keyboard active' : 'Native client ready');
    return true;
  }).catch(function(error) {
    console.warn('Native keyboard control failed:', error);
    state.nativeKeyboardOpen = false;
    state.nativeKeyboardState = 'inactive';
    state.nativeKeyboardControlId = '';
    updateNativeKeyboardUi(error.message || 'Native keyboard unavailable', 'disconnected');
    return false;
  }).finally(function() {
    state.nativeKeyboardRequestPending = false;
    updateNativeKeyboardUi();
  });
}

function initNativeKeyboardHandler() {
  // The toolbar keyboard button is gone; the floating bubble is the sole toggle.
  // The connection status poll must still run so the bubble knows when a native
  // client is connected and reflects the sticky show/hide state, so it is no
  // longer gated on a separate header control.
  updateNativeKeyboardUi();
  if (state.nativeKeyboardStatusTimer) clearInterval(state.nativeKeyboardStatusTimer);
  state.nativeKeyboardStatusTimer = setInterval(pollNativeKeyboardStatus, 3000);
}

// ── Floating keyboard bubble ─────────────────────────────────────────────────
// A self-view-style floating control that lives inside the canvas viewport
// (so it survives inline fullscreen, where the whole .viewer-header — and
// with it the toolbar Keyboard button — is hidden). Tapping it is the ONLY
// thing that opens/closes the native keyboard now; canvas taps used to also
// auto-request it on every single touch in control_desktop mode, which is
// what made the keyboard pop up on every remote-desktop touch. The bubble can
// be dragged anywhere in the viewport and collapsed to a small edge handle
// (like a minimized video-call self-view), independent of whether the
// keyboard itself is currently shown or hidden.
var _KEYBOARD_BUBBLE_COLLAPSED_KEY = 'autoyou.ui.keyboardBubbleCollapsed';
var _KEYBOARD_BUBBLE_POS_KEY = 'autoyou.ui.keyboardBubblePos';
var DRAG_TOLERANCE_PX = 8;

function _loadKeyboardBubbleCollapsed() {
  try { return localStorage.getItem(_KEYBOARD_BUBBLE_COLLAPSED_KEY) === '1'; } catch (_) { return false; }
}
function _saveKeyboardBubbleCollapsed(collapsed) {
  try { localStorage.setItem(_KEYBOARD_BUBBLE_COLLAPSED_KEY, collapsed ? '1' : '0'); } catch (_) {}
}
function _loadKeyboardBubblePos() {
  try {
    var raw = localStorage.getItem(_KEYBOARD_BUBBLE_POS_KEY);
    if (!raw) return null;
    var parsed = JSON.parse(raw);
    if (parsed && typeof parsed.xr === 'number' && typeof parsed.yr === 'number') return parsed;
  } catch (_) {}
  return null;
}
function _saveKeyboardBubblePos(xr, yr) {
  try { localStorage.setItem(_KEYBOARD_BUBBLE_POS_KEY, JSON.stringify({ xr: xr, yr: yr })); } catch (_) {}
}

function updateFloatingKeyboardBubbleUi() {
  var bubble = $('floating-keyboard-bubble');
  var handle = $('floating-keyboard-handle');
  var mainBtn = $('floating-keyboard-main');
  if (!bubble || !handle || !mainBtn) return;

  var eligible = state.activeMode === 'control_desktop';
  var connected = state.nativeKeyboardConnected;
  var active = state.nativeKeyboardOpen;

  bubble.classList.toggle('hidden', !eligible || state.keyboardBubbleCollapsed);
  handle.classList.toggle('hidden', !eligible || !state.keyboardBubbleCollapsed);

  mainBtn.disabled = !connected || state.nativeKeyboardRequestPending;
  mainBtn.setAttribute('aria-pressed', active ? 'true' : 'false');
  mainBtn.classList.toggle('active', active);
  mainBtn.title = connected
    ? (active ? 'Hide the connected phone keyboard' : 'Show the connected phone keyboard')
    : 'Connect an iOS or Android AutoYou client to enable the native keyboard';
  mainBtn.setAttribute('aria-label', mainBtn.title);
}

function initFloatingKeyboardBubble() {
  var wrapper = $('canvas-wrapper');
  var bubble = $('floating-keyboard-bubble');
  var mainBtn = $('floating-keyboard-main');
  var collapseBtn = $('floating-keyboard-collapse');
  var handle = $('floating-keyboard-handle');
  if (!wrapper || !bubble || !mainBtn || !collapseBtn || !handle) return;

  state.keyboardBubbleCollapsed = _loadKeyboardBubbleCollapsed();

  // Keep a fixed margin off the wrapper edges so the bubble never sits flush
  // against a device notch/home-indicator; CSS env(safe-area-inset-*) can't
  // help here since the bubble is positioned with inline styles for dragging,
  // and inline styles always win over any CSS rule.
  var EDGE_MARGIN_PX = 14;

  var savedPos = _loadKeyboardBubblePos();
  function applyPosition(xr, yr) {
    var rect = wrapper.getBoundingClientRect();
    var w = bubble.offsetWidth || 56;
    var h = bubble.offsetHeight || 56;
    var maxX = Math.max(0, rect.width - w - EDGE_MARGIN_PX * 2);
    var maxY = Math.max(0, rect.height - h - EDGE_MARGIN_PX * 2);
    var x = EDGE_MARGIN_PX + Math.max(0, Math.min(maxX, xr * maxX));
    var y = EDGE_MARGIN_PX + Math.max(0, Math.min(maxY, yr * maxY));
    bubble.style.left = x + 'px';
    bubble.style.top = y + 'px';
    bubble.style.right = 'auto';
    bubble.style.bottom = 'auto';
  }

  if (savedPos) {
    applyPosition(savedPos.xr, savedPos.yr);
  } else {
    // Default: bottom-left, clear of the zoom controls which live bottom-right.
    applyPosition(0, 1);
  }

  var dragState = null; // { startX, startY, originLeft, originTop, moved }

  function currentXR() {
    var rect = wrapper.getBoundingClientRect();
    var w = bubble.offsetWidth || 56;
    var maxX = Math.max(0, rect.width - w - EDGE_MARGIN_PX * 2);
    if (maxX <= 0) return 0;
    return Math.max(0, Math.min(1, (bubble.offsetLeft - EDGE_MARGIN_PX) / maxX));
  }
  function currentYR() {
    var rect = wrapper.getBoundingClientRect();
    var h = bubble.offsetHeight || 56;
    var maxY = Math.max(0, rect.height - h - EDGE_MARGIN_PX * 2);
    if (maxY <= 0) return 0;
    return Math.max(0, Math.min(1, (bubble.offsetTop - EDGE_MARGIN_PX) / maxY));
  }

  bubble.addEventListener('pointerdown', function(e) {
    if (e.target === collapseBtn) return;
    dragState = {
      startX: e.clientX,
      startY: e.clientY,
      originLeft: bubble.offsetLeft,
      originTop: bubble.offsetTop,
      moved: false
    };
    try { bubble.setPointerCapture(e.pointerId); } catch (_) {}
  });

  bubble.addEventListener('pointermove', function(e) {
    if (!dragState) return;
    var dx = e.clientX - dragState.startX;
    var dy = e.clientY - dragState.startY;
    if (!dragState.moved && Math.hypot(dx, dy) < DRAG_TOLERANCE_PX) return;
    dragState.moved = true;
    var rect = wrapper.getBoundingClientRect();
    var w = bubble.offsetWidth || 56;
    var h = bubble.offsetHeight || 56;
    var x = Math.max(EDGE_MARGIN_PX, Math.min(rect.width - w - EDGE_MARGIN_PX, dragState.originLeft + dx));
    var y = Math.max(EDGE_MARGIN_PX, Math.min(rect.height - h - EDGE_MARGIN_PX, dragState.originTop + dy));
    bubble.style.left = x + 'px';
    bubble.style.top = y + 'px';
    bubble.style.right = 'auto';
    bubble.style.bottom = 'auto';
  });

  function endDrag(e) {
    if (!dragState) return;
    var wasDrag = dragState.moved;
    try { bubble.releasePointerCapture(e.pointerId); } catch (_) {}
    dragState = null;
    if (wasDrag) {
      _saveKeyboardBubblePos(currentXR(), currentYR());
    }
  }
  bubble.addEventListener('pointerup', endDrag);
  bubble.addEventListener('pointercancel', endDrag);

  mainBtn.addEventListener('click', function() {
    if (dragState && dragState.moved) return;
    requestNativeKeyboard(state.nativeKeyboardOpen ? 'hide' : 'show');
  });

  collapseBtn.addEventListener('click', function(e) {
    e.stopPropagation();
    state.keyboardBubbleCollapsed = true;
    _saveKeyboardBubbleCollapsed(true);
    updateFloatingKeyboardBubbleUi();
  });

  handle.addEventListener('click', function() {
    state.keyboardBubbleCollapsed = false;
    _saveKeyboardBubbleCollapsed(false);
    updateFloatingKeyboardBubbleUi();
  });

  window.addEventListener('resize', function() {
    applyPosition(currentXR(), currentYR());
  });

  updateFloatingKeyboardBubbleUi();
}

function showEl(el) { if (el) el.classList.remove('hidden'); }
function hideEl(el) { if (el) el.classList.add('hidden'); }
function scrollElementIntoComfortView(el) {
  if (!el || !el.scrollIntoView) return;
  try {
    var isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || 
                   ('ontouchstart' in window) || (navigator.maxTouchPoints > 0);
    if (isMobile) {
      el.scrollIntoView({ behavior: 'auto', block: 'nearest', inline: 'nearest' });
    } else {
      el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
    }
  } catch (_) {
    try { el.scrollIntoView(true); } catch (__) {}
  }
}
function keepElementVisibleAfterViewportSettles(el) {
  if (!el) return;
  scrollElementIntoComfortView(el);
  window.setTimeout(function() { scrollElementIntoComfortView(el); }, 90);
  window.setTimeout(function() { scrollElementIntoComfortView(el); }, 280);
}
function focusWithComfortScroll(input, scrollTarget) {
  if (!input) return;
  var target = scrollTarget || input;
  keepElementVisibleAfterViewportSettles(target);
  try { input.focus({ preventScroll: true }); } catch (_) { input.focus(); }
  keepElementVisibleAfterViewportSettles(target);
}
function bindKeyboardAwareFocus(input, scrollTarget) {
  if (!input || input.dataset.keyboardAwareFocusBound === '1') return;
  input.dataset.keyboardAwareFocusBound = '1';
  var target = scrollTarget || input;
  var keepVisible = function() { keepElementVisibleAfterViewportSettles(target); };
  input.addEventListener('focus', keepVisible);
  input.addEventListener('click', keepVisible);
  if (window.visualViewport && window.visualViewport.addEventListener) {
    window.visualViewport.addEventListener('resize', function() {
      if (document.activeElement === input) keepVisible();
    });
  }
}

// ── Theme Manager ────────────────────────────────────────────────────────────
function _getTheme() {
  try { return localStorage.getItem('autoyou.ui.theme') || 'dark'; } catch(_) { return 'dark'; }
}
function _setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('autoyou.ui.theme', t); } catch(_) {}
  var btn = $('theme-toggle');
  if (btn) btn.title = t === 'light' ? 'Switch to dark mode' : 'Switch to light mode';
}
function _initTheme() {
  _setTheme(_getTheme());
  var btn = $('theme-toggle');
  if (btn) btn.addEventListener('click', function() {
    _setTheme(_getTheme() === 'dark' ? 'light' : 'dark');
  });
}

// ── Auth Gate Actions ────────────────────────────────────────────────────────
function _applyAuthenticated() {
  auth.authenticated = true;
  var chip = $('auth-chip');
  if (chip) { chip.textContent = 'Authenticated'; chip.className = 'status-chip authenticated'; }
  showEl($('logout-btn'));
  hideEl($('login-gate'));
  showEl($('main-layout'));
  
  // Start backend discovery calls
  loadMonitors();
  loadWindows();
  pollNativeKeyboardStatus();
  startScreenStream();
}

function initAuth() {
  var chip = $('auth-chip');
  if (auth.authenticated) {
    _applyAuthenticated();
  } else if (_storedToken()) {
    apiGet('./api/auth/status').then(function(data) {
      if (data && data.auth && data.auth.authenticated) {
        _applyAuthenticated();
      } else {
        _saveToken('');
        _showLoginGate(chip);
      }
    }).catch(function() { _showLoginGate(chip); });
  } else {
    _showLoginGate(chip);
  }

  // Logout wiring
  $('logout-btn').addEventListener('click', function() {
    apiPost('./api/auth/logout', {}).then(function() {
      _saveToken('');
      window.location.reload();
    }).catch(function() {
      _saveToken('');
      window.location.reload();
    });
  });
}

function _showLoginGate(chip) {
  if (chip) { chip.textContent = 'Locked'; chip.className = 'status-chip locked'; }
  hideEl($('logout-btn'));
  showEl($('login-gate'));
  hideEl($('main-layout'));
  setupOtpLogin();
}

function setupOtpLogin() {
  var input = $('otp-input');
  var btn = $('otp-submit');
  var errEl = $('otp-error');
  var otpScrollTarget = (input && input.closest && input.closest('.otp-row')) || input;
  bindKeyboardAwareFocus(input, otpScrollTarget);

  function attempt() {
    var code = input.value.trim();
    if (!code || code.length < 6) return;
    btn.disabled = true;
    apiPost('./api/auth/login', { code: code }).then(function(data) {
      if (data.success) {
        if (data.token) _saveToken(data.token);
        _applyAuthenticated();
      } else {
        errEl.textContent = data.error || 'Invalid OTP code. Try again.';
        errEl.classList.remove('hidden');
        btn.disabled = false;
        input.value = '';
        focusWithComfortScroll(input, otpScrollTarget);
      }
    }).catch(function() {
      errEl.textContent = 'Verification error. Please retry.';
      errEl.classList.remove('hidden');
      btn.disabled = false;
    });
  }

  btn.addEventListener('click', attempt);
  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') attempt();
  });
}

// ── Dropdowns Populating ────────────────────────────────────────────────────
function showMonitorLoadFallback(message) {
  var sel = $('monitor-select');
  if (sel) {
    sel.innerHTML = '';
    var opt = document.createElement('option');
    opt.value = '';
    opt.textContent = message;
    sel.appendChild(opt);
  }
  showEl($('canvas-overlay'));
  $('overlay-message').textContent = message;
}

function loadMonitors() {
  apiGet('./api/remote_desktop/monitors').then(function(data) {
    state.monitorLoadAttempts = 0;
    var sel = $('monitor-select');
    if (!data.success) {
      state.monitors = [];
      sel.innerHTML = '';
      var opt = document.createElement('option');
      opt.value = '';
      opt.textContent = data.message || 'Desktop capture unavailable';
      sel.appendChild(opt);
      showEl($('canvas-overlay'));
      $('overlay-message').textContent = data.message || 'Desktop capture is unavailable on this host.';
      return;
    }
    state.monitors = data.monitors || [];
    sel.innerHTML = '';
    
    state.monitors.forEach(function(mon) {
      var opt = document.createElement('option');
      opt.value = mon.id;
      opt.textContent = mon.name;
      sel.appendChild(opt);
    });

    if (state.monitors.length > 0) {
      state.selectedMonitor = state.monitors[0].id;
    }
    updateStreamConfig();
  }).catch(function(e) {
    state.monitorLoadAttempts += 1;
    var retrying = state.monitorLoadAttempts <= 3;
    showMonitorLoadFallback(retrying
      ? 'Remote Desktop is still connecting. Retrying monitor list...'
      : 'Remote Desktop monitor list is unavailable. Refresh to try again.');
    console.warn('Error loading monitors list:', e);
    if (retrying) {
      setTimeout(loadMonitors, 1000 + (state.monitorLoadAttempts * 750));
    }
  });
}

function loadWindows() {
  var select = $('app-select');
  select.innerHTML = '<option value="">-- Choose window to stream --</option>';
  
  apiGet('./api/remote_desktop/windows').then(function(data) {
    state.windowLoadAttempts = 0;
    if (!data.success) {
      var unavailable = document.createElement('option');
      unavailable.value = '';
      unavailable.textContent = data.message || 'Application windows unavailable';
      select.appendChild(unavailable);
      return;
    }
    state.windows = data.windows || [];
    
    state.windows.forEach(function(win) {
      var opt = document.createElement('option');
      opt.value = win.id;
      opt.textContent = win.title + ' (' + win.width + 'x' + win.height + ')';
      select.appendChild(opt);
    });
  }).catch(function(e) {
    state.windowLoadAttempts += 1;
    var retrying = state.windowLoadAttempts <= 3;
    var unavailable = document.createElement('option');
    unavailable.value = '';
    unavailable.textContent = retrying
      ? 'Applications still connecting...'
      : 'Applications unavailable';
    select.innerHTML = '';
    select.appendChild(unavailable);
    console.warn('Error loading active applications:', e);
    if (retrying) {
      setTimeout(loadWindows, 1000 + (state.windowLoadAttempts * 750));
    }
  });
}

// ── Tab & Mode Selectors ─────────────────────────────────────────────────────
function initModeHandlers() {
  var modeButtons = [
    $('tab-view-desktop'),
    $('tab-control-desktop'),
    $('tab-view-app'),
    $('tab-view-mobile-video')
  ];

  modeButtons.forEach(function(btn) {
    if (!btn) return;
    btn.addEventListener('click', function() {
      // Toggle button highlights
      modeButtons.forEach(function(b) { b.classList.remove('active'); });
      btn.classList.add('active');
      
      // Update local mode state
      state.activeMode = btn.getAttribute('data-mode');
      if (state.activeMode !== 'control_desktop') clearWebsiteControl();
      
      // Handle overlays, visibility grids
      var container = $('canvas-wrapper');
      
      var nextStreamKind = streamKindForMode(state.activeMode);

      if (state.activeMode === 'view_desktop') {
        container.classList.remove('control-mode');
        showEl($('monitor-selection-panel'));
        hideEl($('app-selection-panel'));
        $('viewer-title').textContent = 'Active Monitor Caster';
      } else if (state.activeMode === 'control_desktop') {
        container.classList.add('control-mode');
        showEl($('monitor-selection-panel'));
        hideEl($('app-selection-panel'));
        $('viewer-title').textContent = 'Live Remote Monitor Controller';
        focusCanvas();
      } else if (state.activeMode === 'view_app') {
        container.classList.remove('control-mode');
        hideEl($('monitor-selection-panel'));
        showEl($('app-selection-panel'));
        $('viewer-title').textContent = 'Active Application Streamer';
      } else if (state.activeMode === 'view_mobile_video') {
        container.classList.remove('control-mode');
        hideEl($('monitor-selection-panel'));
        hideEl($('app-selection-panel'));
        $('viewer-title').textContent = 'Mobile Camera Stream';
      }

      var zoomControls = $('zoom-controls');
      if (zoomControls) zoomControls.classList.toggle('hidden', state.activeMode !== 'control_desktop');
      resetCanvasView();

      if (state.activeMode !== 'control_desktop' && (state.nativeKeyboardOpen || state.nativeKeyboardState === 'hidden')) {
        requestNativeKeyboard('hide');
      }
      updateNativeKeyboardUi();
      
      updateOverlayState();
      if (state.ws && state.streamKind !== nextStreamKind) {
        startScreenStream();
      } else {
        updateStreamConfig();
      }
    });
  });

  // A phone or tablet is normally being used to control the streamed Mac,
  // not merely observe it.  Start that touch-first path in control mode so
  // the first canvas tap is not silently discarded by the view-only default.
  if (isMobileKeyboardViewer()) {
    var initialControlButton = $('tab-control-desktop');
    if (initialControlButton) initialControlButton.click();
  }

  // Wire refresh button for applications
  $('refresh-apps-btn').addEventListener('click', loadWindows);
  
  // Wire dropdown selections
  $('monitor-select').addEventListener('change', function(e) {
    state.selectedMonitor = parseInt(e.target.value, 10);
    updateStreamConfig();
    if (window.innerWidth <= 768) {
      closeSidebar();
    }
  });

  $('app-select').addEventListener('change', function(e) {
    state.selectedWindow = e.target.value || null;
    updateOverlayState();
    updateStreamConfig();
    if (window.innerWidth <= 768 && state.selectedWindow !== null) {
      closeSidebar();
    }
  });
}

function updateOverlayState() {
  var overlay = $('canvas-overlay');
  if (!state.isStreamDesired) {
    showEl(overlay);
    $('overlay-message').textContent = 'Stream paused. Click "Start Stream" in the top right to resume casting.';
    return;
  }

  if (state.activeMode === 'view_mobile_video') {
    if (state.hasMobileVideoFrame) {
      hideEl(overlay);
    } else {
      showEl(overlay);
      $('overlay-message').textContent = 'Waiting for a connected iOS video call camera feed.';
    }
    return;
  }
  
  if (state.activeMode === 'view_app') {
    if (state.selectedWindow !== null) {
      hideEl(overlay);
    } else {
      showEl(overlay);
      $('overlay-message').textContent = 'View Application stream. Select a running app window from the left dropdown.';
    }
  } else {
    hideEl(overlay);
  }
}

// ── Sliders Adjustments ───────────────────────────────────────────────────────
function initSliderHandlers() {
  var scaleSlider = $('scale-slider');
  var scaleVal = $('scale-val');
  scaleSlider.addEventListener('input', function(e) {
    state.scale = parseFloat(e.target.value) / 100.0;
    scaleVal.textContent = e.target.value + '%';
    updateStreamConfig();
  });

  var qualitySlider = $('quality-slider');
  var qualityVal = $('quality-val');
  qualitySlider.addEventListener('input', function(e) {
    state.quality = parseInt(e.target.value, 10);
    qualityVal.textContent = e.target.value + '%';
    updateStreamConfig();
  });

  var fpsSlider = $('fps-slider');
  var fpsVal = $('fps-val');
  fpsSlider.addEventListener('input', function(e) {
    state.fps = parseInt(e.target.value, 10);
    fpsVal.textContent = e.target.value + ' FPS';
    updateStreamConfig();
  });
}

// ── High Performance WebSocket Binary JPEG Loop ─────────────────────────────
function startScreenStream() {
  if (!state.isStreamDesired) return;
  clearWebsiteControl(); state.nativeFrame = null; state.nativeRendering = false;

  if (state.ws) {
    var oldSocket = state.ws;
    state.ws = null;
    try { oldSocket.onclose = null; oldSocket.close(); } catch(_) {}
  }
  
  var streamKind = streamKindForMode(state.activeMode);
  var wsUrl = streamKind === 'mobile_video' ? buildMobileVideoWebSocketUrl() : buildScreenWebSocketUrl();
  state.streamKind = streamKind;
  state.hasMobileVideoFrame = false;
  updateOverlayState();
  
  console.log('Connecting remote stream WebSocket channel:', wsUrl);
  
  var canvas = $('screen-canvas');
  var ctx = canvas.getContext('2d');
  var indicator = $('viewer-status-dot');
  var connText = $('diag-connection');
  
  var socket = new WebSocket(wsUrl);
  socket.binaryType = 'arraybuffer';
  state.ws = socket;
  
  socket.onopen = function() {
    console.log('Remote screen channel successfully established.');
    indicator.className = 'status-indicator connected';
    connText.textContent = 'Connected';
    connText.className = 'info-val status-connected';
    
    if (state.wsReconnectTimer) {
      clearTimeout(state.wsReconnectTimer);
      state.wsReconnectTimer = null;
    }
    
    var token = _storedToken();
    if (token) {
      try {
        socket.send(JSON.stringify({ type: 'auth', token: token }));
      } catch(e) {
        console.error('Failed to send screen WebSocket auth message:', e);
      }
    }

    // Transmit parameters immediately after the auth frame, if one is needed.
    updateStreamConfig();
  };
  
  socket.onmessage = function(event) {
    if (state.ws !== socket) return;
    // Text frames carry stream state; binary frames carry JPEG images.
    if (typeof event.data === 'string') {
      var control = null;
      try { control = JSON.parse(event.data); } catch(_) {}
      if (control && control.event === 'native_website_frame') {
        state.nativeWebsite = true;
        if (!isMobileKeyboardViewer()) {
          canvas.contentEditable = 'plaintext-only';
          canvas.setAttribute('role', 'textbox');
          canvas.setAttribute('aria-label', 'Keyboard input on the remote computer');
        }
        var source = control.source;
        if (control.version === 1 && control.config_token === state.nativeConfigToken && source &&
            source.source_id === 6 && source.media_generation === 1 && typeof source.call_id === 'string' && typeof source.lease_id === 'string' &&
            Number.isSafeInteger(control.sequence) && control.sequence > 0 &&
            Number.isInteger(control.width) && Number.isInteger(control.height) && control.width > 0 && control.width <= 4096 &&
            control.height > 0 && control.height <= 4096 && control.width * control.height * 4 <= 32 * 1024 * 1024 &&
            control.expires_in_ms > 0 && control.expires_in_ms <= 3000) {
          socket.nativePicture = Object.assign(control, { deadline: performance.now() + control.expires_in_ms });
        } else socket.nativePicture = null;
      } else if (control && control.event === 'native_website_denied') {
        clearWebsiteControl(); state.nativeFrame = null;
      }
      if (control && control.type === 'feed_state' && !control.active) {
        // The phone camera stopped: blank the stale frame like the
        // streaming site does instead of freezing on the last image.
        state.hasMobileVideoFrame = false;
        state.lastFrameTime = null;
        state.frameTimes = [];
        ctx.fillStyle = '#000';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        $('diag-fps').textContent = '0 FPS';
        $('diag-latency').textContent = '-- ms';
        updateOverlayState();
      }
      return;
    }

    var now = performance.now();
    var receipt = socket.nativePicture; socket.nativePicture = null;
    if (state.nativeWebsite && state.streamKind === 'screen' && (!receipt || receipt.deadline <= now || state.nativeRendering || event.data.byteLength > 8 * 1024 * 1024)) return;
    if (receipt) state.nativeRendering = true;

    // Latency & FPS diagnostics
    if (state.lastFrameTime) {
      var diff = now - state.lastFrameTime;
      state.frameTimes.push(diff);
      if (state.frameTimes.length > 30) {
        state.frameTimes.shift();
      }
      
      var avgDiff = state.frameTimes.reduce(function(a, b) { return a + b; }, 0) / state.frameTimes.length;
      var curFps = Math.round(1000.0 / avgDiff);
      $('diag-fps').textContent = curFps + ' FPS';
      $('diag-latency').textContent = Math.round(diff) + ' ms';
    }
    state.lastFrameTime = now;
    if (state.streamKind === 'mobile_video') {
      state.hasMobileVideoFrame = true;
      updateOverlayState();
    }
    
    // Render the binary frame
    var blob = new Blob([event.data], { type: 'image/jpeg' });
    var url = URL.createObjectURL(blob);
    var img = new Image();
    
    img.onload = function() {
      if (receipt) {
        state.nativeRendering = false;
        if (state.ws !== socket || receipt.config_token !== state.nativeConfigToken || receipt.deadline <= performance.now() ||
            img.width !== receipt.width || img.height !== receipt.height) { URL.revokeObjectURL(url); return; }
        if (state.nativeFrame && state.nativeFrame.source.call_id !== receipt.source.call_id) clearWebsiteControl();
        state.nativeFrame = receipt;
      }
      // Adjust canvas resolution dynamically to match actual frame size
      if (canvas.width !== img.width || canvas.height !== img.height) {
        canvas.width = img.width;
        canvas.height = img.height;
        // A resolution change (e.g. switching monitors) invalidates any local
        // pinch-zoom/pan the viewer had applied against the old frame size.
        resetCanvasView();
      }
      ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(url);
    };
    img.onerror = function() {
      if (receipt) { state.nativeRendering = false; clearWebsiteControl(); state.nativeFrame = null; }
      URL.revokeObjectURL(url);
    };
    img.src = url;
  };
  
  socket.onclose = function() {
    if (state.ws !== socket) return;
    clearWebsiteControl(); state.nativeFrame = null;
    console.warn('Remote screen stream disconnected.');
    indicator.className = 'status-indicator';
    connText.textContent = 'Disconnected';
    connText.className = 'info-val status-disconnected';
    if (state.streamKind === 'mobile_video') {
      state.hasMobileVideoFrame = false;
      updateOverlayState();
    }
    
    // Schedule reconnect ONLY if still desired
    if (state.isStreamDesired && !state.wsReconnectTimer) {
      state.wsReconnectTimer = setTimeout(function() {
        startScreenStream();
      }, 3000);
    }
  };
  
  socket.onerror = function(err) {
    console.error('WebSocket screen error:', err);
  };
}

function updateStreamConfig() {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
  
  var payload = {
    scale: state.scale,
    quality: state.quality,
    fps: state.fps,
    focus: false
  };

  function scopeConfig() {
    var key = JSON.stringify(payload);
    if (key !== state.nativeConfigKey) {
      clearWebsiteControl(); state.nativeFrame = null;
      state.nativeConfigKey = key; state.nativeConfigToken = String(++state.nativeConfigCounter);
    }
    payload.config_token = state.nativeConfigToken;
  }

  if (state.activeMode === 'view_mobile_video') {
    try {
      state.ws.send(JSON.stringify({
        fps: state.fps,
        paused: false
      }));
    } catch(e) {
      console.error('Failed to send mobile video config update to WS:', e);
    }
    return;
  }
  
  if (state.activeMode === 'view_app') {
    payload.target_type = 'window';
    if (state.selectedWindow === null) {
      payload.target_id = null;
      payload.paused = true;
      scopeConfig();
      try {
        state.ws.send(JSON.stringify(payload));
      } catch(e) {
        console.error('Failed to send config update to WS:', e);
      }
      return;
    }
    payload.target_id = state.selectedWindow;
    payload.paused = false;
  } else {
    payload.target_type = 'monitor';
    payload.target_id = state.selectedMonitor;
    payload.paused = false;
  }
  scopeConfig();
  
  try {
    state.ws.send(JSON.stringify(payload));
  } catch(e) {
    console.error('Failed to send config update to WS:', e);
  }
}

// ── Interactive Cursor Controls & Inputs Capture ──────────────────────────
function transmitInputEvent(payload) {
  if (state.nativeWebsite) {
    var grant = state.nativeControl;
    if (grant && grant.writer === 'keyboard') return;
    if (!grant || grant.writer !== 'browser' || !currentWebsiteFrame() || grant.view_id !== state.nativeFrame.source.call_id ||
        performance.now() >= grant.deadline || state.activeMode !== 'control_desktop') { clearWebsiteControl(); return; }
    var value = { event: 'remote_desktop_input' };
    if (payload.type === 'mousemove') Object.assign(value, { input_type: 'move', coordinate_mode: 'absolute', x: payload.x, y: payload.y });
    else if (payload.type === 'scroll') Object.assign(value, { input_type: 'scroll', dy: payload.dy });
    else if (/^(click|doubleclick|mousedown|mouseup)$/.test(payload.type)) Object.assign(value, { input_type: 'button', button: payload.button || 'left',
      phase: {click:'click', doubleclick:'double_click', mousedown:'down', mouseup:'up'}[payload.type], x: payload.x, y: payload.y });
    else if (/^(keydown|keyup|keypress)$/.test(payload.type)) value = { event:'remote_desktop_keyboard', action:'key', key:payload.key,
      phase:{keydown:'down',keyup:'up',keypress:'press'}[payload.type] };
    else if (payload.type === 'text') value = { event:'remote_desktop_keyboard', action:'input', text:payload.text };
    else return;
    value.control_id = grant.control_id;
    value.native_website = { version: 1, view_id: grant.view_id };
    var created = performance.now();
    if (++grant.pending > 64) { clearWebsiteControl(); return; }
    grant.queue = grant.queue.then(function() {
      if (state.nativeControl !== grant || !currentWebsiteFrame() || performance.now() - created >= 200) throw new Error('Website input expired');
      value.native_input = Object.assign({}, grant.native_input, { sequence: ++grant.sequence, elapsed_ms: Math.floor(created - grant.received) });
      return apiPost('./api/remote_desktop/input', value).then(function(data) { if (!data.success) throw new Error('Website input denied'); });
    }).catch(function() { if (state.nativeControl === grant) clearWebsiteControl(); }).finally(function() { --grant.pending; });
    return;
  }
  // Inject target mappings
  if (state.activeMode === 'view_app') {
    if (state.selectedWindow === null) return;
    payload.target_type = 'window';
    payload.target_id = state.selectedWindow;
  } else {
    payload.target_type = 'monitor';
    payload.target_id = state.selectedMonitor;
  }
  
  // Post payload to backend
  apiPost('./api/remote_desktop/input', payload).catch(function(err) {
    console.error('Input transmit failed:', err);
  });
}

function currentWebsiteFrame() {
  return state.nativeFrame && !document.hidden && state.nativeFrame.config_token === state.nativeConfigToken && performance.now() < state.nativeFrame.deadline;
}

function clearWebsiteControl() {
  var grant = state.nativeControl; state.nativeControl = null;
  if (grant) apiPost('./api/remote_desktop/' + (grant.writer === 'keyboard' ? 'native-keyboard' : 'control'), {
    action: grant.writer === 'keyboard' ? 'hide' : 'stop', view_id: grant.view_id, control_id: grant.control_id
  }).catch(function() {});
}

function requestWebsiteControl() {
  if (!state.nativeWebsite || state.nativeControl || state.nativeControlPending || !currentWebsiteFrame()) return;
  var frame = state.nativeFrame, token = state.nativeConfigToken;
  state.nativeControlPending = true;
  apiPost('./api/remote_desktop/control', { action:'approve', control_id:crypto.randomUUID(), view_id:frame.source.call_id,
    source:frame.source, frame_sequence:frame.sequence }).then(function(data) {
    if (!data.success) return;
    if (token !== state.nativeConfigToken || !currentWebsiteFrame() || state.nativeFrame.source.call_id !== data.view_id || state.activeMode !== 'control_desktop') {
      apiPost('./api/remote_desktop/control', {action:'stop',view_id:data.view_id,control_id:data.control_id}).catch(function() {}); return;
    }
    state.nativeControl = Object.assign(data, { writer:'browser', sequence:0, pending:0, queue:Promise.resolve(), received:performance.now(),
      deadline:performance.now() + Math.max(0, Math.min(60000,data.expires_at_ms - data.input_clock_ms)) });
  }).finally(function() { state.nativeControlPending = false; }).catch(function() {});
}

function transmitWebsiteText(text) {
  if (typeof text !== 'string' || text.length > 4096) { clearWebsiteControl(); return; }
  var points = Array.from(text);
  if (points.some(function(point) { var value = point.codePointAt(0); return value >= 0xd800 && value <= 0xdfff; })) { clearWebsiteControl(); return; }
  for (var index = 0; index < points.length; index += 256) transmitInputEvent({type:'text',text:points.slice(index,index+256).join('')});
}

function focusCanvas() {
  var canvas = $('screen-canvas');
  if (canvas) {
    canvas.tabIndex = 1000; // Enable element key focus
    canvas.focus();
    state.isControlFocused = true;
  }
}

function isMobileKeyboardViewer(event) {
  var pointerType = String((event && event.pointerType) || '').toLowerCase();
  return pointerType === 'touch' || pointerType === 'pen' ||
    /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent || '') ||
    Number(navigator.maxTouchPoints || 0) > 0;
}

function calculateRatios(event, canvas) {
  var rect = canvas.getBoundingClientRect();
  var x_ratio = (event.clientX - rect.left) / rect.width;
  var y_ratio = (event.clientY - rect.top) / rect.height;
  
  // Clamp boundaries to 0.0 - 1.0 to guarantee safety
  x_ratio = Math.max(0.0, Math.min(1.0, x_ratio));
  y_ratio = Math.max(0.0, Math.min(1.0, y_ratio));
  
  return { x: x_ratio, y: y_ratio };
}

// ── Canvas Zoom / Pan ─────────────────────────────────────────────────────
// Purely a local view transform (CSS only) so a small phone screen can pinch
// in on a high-resolution remote desktop; getBoundingClientRect() already
// accounts for the CSS transform, so calculateRatios() needs no changes.
var canvasView = { scale: 1, tx: 0, ty: 0 };
var CANVAS_MIN_ZOOM = 1;
var CANVAS_MAX_ZOOM = 4;

function clampCanvasZoom(scale) {
  return Math.max(CANVAS_MIN_ZOOM, Math.min(CANVAS_MAX_ZOOM, scale));
}

function clampCanvasPan(wrapper) {
  var rect = wrapper.getBoundingClientRect();
  var maxTx = (rect.width * (canvasView.scale - 1)) / 2;
  var maxTy = (rect.height * (canvasView.scale - 1)) / 2;
  canvasView.tx = Math.max(-maxTx, Math.min(maxTx, canvasView.tx));
  canvasView.ty = Math.max(-maxTy, Math.min(maxTy, canvasView.ty));
}

function updateZoomUi() {
  var level = $('zoom-level');
  var resetBtn = $('zoom-reset-btn');
  if (level) level.textContent = Math.round(canvasView.scale * 100) + '%';
  if (resetBtn) resetBtn.classList.toggle('hidden', canvasView.scale <= 1.001 && canvasView.tx === 0 && canvasView.ty === 0);
}

function applyCanvasViewTransform(canvas) {
  canvas.style.transform = (canvasView.scale === 1 && canvasView.tx === 0 && canvasView.ty === 0)
    ? ''
    : 'translate(' + canvasView.tx.toFixed(1) + 'px,' + canvasView.ty.toFixed(1) + 'px) scale(' + canvasView.scale.toFixed(3) + ')';
  updateZoomUi();
}

function resetCanvasView() {
  canvasView.scale = 1;
  canvasView.tx = 0;
  canvasView.ty = 0;
  var canvas = $('screen-canvas');
  if (canvas) applyCanvasViewTransform(canvas);
}

function setCanvasZoomStep(delta) {
  var canvas = $('screen-canvas');
  var wrapper = $('canvas-wrapper');
  if (!canvas || !wrapper) return;
  canvasView.scale = clampCanvasZoom(canvasView.scale + delta);
  if (canvasView.scale <= 1.001) { canvasView.tx = 0; canvasView.ty = 0; }
  clampCanvasPan(wrapper);
  applyCanvasViewTransform(canvas);
}

function initZoomControls() {
  var inBtn = $('zoom-in-btn');
  var outBtn = $('zoom-out-btn');
  var resetBtn = $('zoom-reset-btn');
  if (inBtn) inBtn.addEventListener('click', function() { setCanvasZoomStep(0.5); });
  if (outBtn) outBtn.addEventListener('click', function() { setCanvasZoomStep(-0.5); });
  if (resetBtn) resetBtn.addEventListener('click', resetCanvasView);
}

function pointDistance(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
function pointMidpoint(a, b) { return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }

function spawnTapRipple(wrapper, clientX, clientY, isLongPress) {
  var rect = wrapper.getBoundingClientRect();
  var el = document.createElement('div');
  el.className = 'tap-ripple' + (isLongPress ? ' long-press' : '');
  el.style.left = (clientX - rect.left) + 'px';
  el.style.top = (clientY - rect.top) + 'px';
  wrapper.appendChild(el);
  window.setTimeout(function() { el.remove(); }, 650);
}

// ── Interactive Cursor / Touch Gesture Capture ─────────────────────────────
// A single Pointer Events pipeline drives mouse *and* touch: tap = click,
// press-and-hold (touch) or right mouse button = right-click, drag = move
// the remote cursor (and drag-select once past a small movement threshold),
// double-tap = double-click, two fingers vertically = scroll, pinch = local
// zoom. This replaces the old separate mouse-only listeners (which required
// holding Ctrl to drag and had no touch equivalent at all).
function initInputCapture() {
  var canvas = $('screen-canvas');
  var wrapper = $('canvas-wrapper');
  var nativeComposition = false, finishedComposition = null;
  var nativeKeyOwners = new Map();
  canvas.addEventListener('compositionstart', function() { nativeComposition = true; finishedComposition = null; });
  canvas.addEventListener('compositionend', function(e) {
    nativeComposition = false;
    if (state.nativeWebsite && !isMobileKeyboardViewer() && e.isTrusted) {
      transmitWebsiteText(e.data || ''); finishedComposition = e.data || ''; canvas.textContent = '';
    }
  });
  canvas.addEventListener('beforeinput', function(e) {
    if (!state.nativeWebsite || isMobileKeyboardViewer() || !e.isTrusted) return;
    if (nativeComposition || e.isComposing) return;
    e.preventDefault();
    if (e.inputType === 'insertText' && typeof e.data === 'string') {
      if (e.data !== finishedComposition) transmitWebsiteText(e.data);
      finishedComposition = null;
    }
    canvas.textContent = '';
  });
  canvas.addEventListener('blur', function() {
    state.isControlFocused = false;
    if (state.nativeControl && state.nativeControl.writer === 'browser') clearWebsiteControl();
  });
  document.addEventListener('visibilitychange', function() {
    if (document.hidden) { clearWebsiteControl(); state.nativeFrame = null; }
  });

  var pointers = new Map();
  var tap = null;   // { x, y, pointerId } for the primary (first) touch/pointer
  var dragActive = false;
  var dragMoved = false;
  var pinch = null; // { startDist, startScale, startMid, startTx, startTy, lastMid }
  var longPressTimer = null;
  var lastTapAt = 0;
  var lastTapPos = null;
  var LONG_PRESS_MS = 480;
  var TAP_TOLERANCE_PX = 10;
  var DOUBLE_TAP_MS = 320;

  function clearLongPress() {
    if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
  }

  function reportCursor(clientX, clientY) {
    var ratios = calculateRatios({ clientX: clientX, clientY: clientY }, canvas);
    var pixelX = Math.round(ratios.x * canvas.width);
    var pixelY = Math.round(ratios.y * canvas.height);
    $('cursor-coords').textContent = 'X: ' + pixelX + ', Y: ' + pixelY + ' (' + ratios.x.toFixed(2) + ', ' + ratios.y.toFixed(2) + ')';
    return ratios;
  }

  function sendMove(clientX, clientY) {
    var ratios = reportCursor(clientX, clientY);
    if (state.activeMode !== 'control_desktop') return;
    if (!state.mouseThrottleTimer) {
      state.mouseThrottleTimer = setTimeout(function() {
        state.mouseThrottleTimer = null;
        transmitInputEvent({ type: 'mousemove', x: ratios.x, y: ratios.y });
      }, 60); // Throttle at ~16 Hz to preserve throughput.
    }
  }

  function sendClick(clientX, clientY, button) {
    var ratios = calculateRatios({ clientX: clientX, clientY: clientY }, canvas);
    transmitInputEvent({ type: 'click', x: ratios.x, y: ratios.y, button: button || 'left' });
  }

  function sendDoubleClick(clientX, clientY) {
    var ratios = calculateRatios({ clientX: clientX, clientY: clientY }, canvas);
    transmitInputEvent({ type: 'doubleclick', x: ratios.x, y: ratios.y });
  }

  function sendMouseDown(clientX, clientY) {
    var ratios = calculateRatios({ clientX: clientX, clientY: clientY }, canvas);
    transmitInputEvent({ type: 'mousedown', x: ratios.x, y: ratios.y, button: 'left' });
  }

  function sendMouseUp(clientX, clientY) {
    var ratios = calculateRatios({ clientX: clientX, clientY: clientY }, canvas);
    transmitInputEvent({ type: 'mouseup', x: ratios.x, y: ratios.y, button: 'left' });
  }

  canvas.addEventListener('pointerdown', function(e) {
    if (state.activeMode !== 'control_desktop') return;
    if (e.isTrusted) requestWebsiteControl();

    if (e.pointerType === 'mouse' && e.button === 2) {
      focusCanvas();
      sendClick(e.clientX, e.clientY, 'right');
      return;
    }
    if (e.pointerType === 'mouse' && e.button !== 0) return; // ignore middle/other buttons

    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    try { canvas.setPointerCapture(e.pointerId); } catch (_) {}

    if (pointers.size === 1) {
      dragActive = false;
      dragMoved = false;
      tap = { x: e.clientX, y: e.clientY, pointerId: e.pointerId };
      clearLongPress();
      if (e.pointerType === 'touch') {
        longPressTimer = setTimeout(function() {
          longPressTimer = null;
          if (tap && !dragMoved) {
            focusCanvas();
            sendClick(tap.x, tap.y, 'right');
            spawnTapRipple(wrapper, tap.x, tap.y, true);
            tap = null;
          }
        }, LONG_PRESS_MS);
      }
    } else if (pointers.size === 2) {
      clearLongPress();
      tap = null;
      dragActive = false;
      var pts = Array.from(pointers.values());
      pinch = {
        startDist: Math.max(1, pointDistance(pts[0], pts[1])),
        startScale: canvasView.scale,
        startMid: pointMidpoint(pts[0], pts[1]),
        startTx: canvasView.tx,
        startTy: canvasView.ty,
        lastMid: pointMidpoint(pts[0], pts[1]),
      };
    }
  });

  canvas.addEventListener('pointermove', function(e) {
    if (!pointers.has(e.pointerId)) {
      // Hover with no button pressed (desktop mouse only) still previews the
      // cursor position and, while in control mode, live-drives the remote cursor.
      if (e.pointerType === 'mouse') sendMove(e.clientX, e.clientY);
      return;
    }
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (pointers.size >= 2 && pinch) {
      var pts = Array.from(pointers.values());
      var dist = Math.max(1, pointDistance(pts[0], pts[1]));
      var mid = pointMidpoint(pts[0], pts[1]);
      var nextScale = clampCanvasZoom(pinch.startScale * (dist / pinch.startDist));
      var verticalDrift = mid.y - pinch.lastMid.y;
      // Two fingers moving together (not pinching) at default zoom = scroll.
      if (Math.abs(nextScale - canvasView.scale) < 0.015 && Math.abs(verticalDrift) > 5 && canvasView.scale <= 1.02) {
        transmitInputEvent({ type: 'scroll', dy: verticalDrift > 0 ? -60 : 60 });
      }
      canvasView.scale = nextScale;
      canvasView.tx = pinch.startTx + (mid.x - pinch.startMid.x);
      canvasView.ty = pinch.startTy + (mid.y - pinch.startMid.y);
      clampCanvasPan(wrapper);
      applyCanvasViewTransform(canvas);
      pinch.lastMid = mid;
      return;
    }

    if (pointers.size === 1 && tap) {
      var dx = e.clientX - tap.x;
      var dy = e.clientY - tap.y;
      if (!dragMoved && Math.hypot(dx, dy) > TAP_TOLERANCE_PX) {
        dragMoved = true;
        clearLongPress();
        if (canvasView.scale <= 1.02) {
          dragActive = true;
          focusCanvas();
          sendMouseDown(tap.x, tap.y);
        }
      }
      if (dragActive) {
        sendMove(e.clientX, e.clientY);
      } else if (dragMoved && canvasView.scale > 1.02) {
        // Zoomed in: a one-finger drag pans the local view, not the remote cursor.
        canvasView.tx += (e.movementX || 0);
        canvasView.ty += (e.movementY || 0);
        clampCanvasPan(wrapper);
        applyCanvasViewTransform(canvas);
      } else if (!dragMoved) {
        reportCursor(e.clientX, e.clientY);
      }
    }
  });

  function finishPointer(e) {
    clearLongPress();
    var wasTapPointer = !!(tap && tap.pointerId === e.pointerId);
    pointers.delete(e.pointerId);
    try { canvas.releasePointerCapture(e.pointerId); } catch (_) {}
    if (pointers.size < 2) pinch = null;

    if (wasTapPointer) {
      if (dragActive) {
        sendMouseUp(e.clientX, e.clientY);
      } else if (!dragMoved) {
        var now = Date.now();
        if (lastTapPos && (now - lastTapAt) < DOUBLE_TAP_MS && pointDistance(lastTapPos, { x: e.clientX, y: e.clientY }) < TAP_TOLERANCE_PX * 2) {
          sendDoubleClick(e.clientX, e.clientY);
          lastTapAt = 0;
          lastTapPos = null;
        } else {
          focusCanvas();
          sendClick(e.clientX, e.clientY, 'left');
          lastTapAt = now;
          lastTapPos = { x: e.clientX, y: e.clientY };
        }
        spawnTapRipple(wrapper, e.clientX, e.clientY, false);
      }
      tap = null;
    }
    dragActive = false;
    dragMoved = false;
  }

  canvas.addEventListener('pointerup', finishPointer);
  canvas.addEventListener('pointercancel', finishPointer);

  // Right-click / long-press already dispatch their own click above; just
  // suppress the native context menu so it doesn't cover the canvas.
  canvas.addEventListener('contextmenu', function(e) {
    if (state.activeMode === 'control_desktop') e.preventDefault();
  });

  // Desktop mouse wheel / trackpad scroll (two-finger touch scroll is handled
  // in the pointermove pinch branch above).
  canvas.addEventListener('wheel', function(e) {
    if (state.activeMode !== 'control_desktop') return;
    e.preventDefault();
    var dy = e.deltaY ? (e.deltaY < 0 ? 120 : -120) : 0;
    if (dy !== 0) transmitInputEvent({ type: 'scroll', dy: dy });
  }, { passive: false });

  // Physical keyboard capture for a desktop viewer. On a touch device this
  // never fires (nothing focuses a software keyboard on a <canvas>), which is
  // why the "Keyboard" button drives typing from a connected phone instead.
  window.addEventListener('keydown', function(e) {
    if (state.activeMode !== 'control_desktop' || !state.isControlFocused) return;
    if (state.nativeWebsite) {
      if (e.isComposing || nativeComposition || !e.isTrusted) return;
      finishedComposition = null;
      if (Array.from(e.key).length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) return;
      nativeKeyOwners.set(e.code || e.key, state.nativeControl && state.nativeControl.control_id);
    }

    // Relinquish focus with Escape key
    if (e.key === 'Escape' || e.key === 'Esc') {
      state.isControlFocused = false;
      canvas.blur();
      console.log('Relinquished control input focus.');
      return;
    }

    e.preventDefault();

    var key = e.key;
    // Map standard spacing/modifiers
    if (key === ' ') key = 'space';

    transmitInputEvent({
      type: 'keydown',
      key: key
    });
  });

  window.addEventListener('keyup', function(e) {
    if (state.activeMode !== 'control_desktop' || !state.isControlFocused) return;
    if (state.nativeWebsite) {
      var keyOwner = nativeKeyOwners.get(e.code || e.key); nativeKeyOwners.delete(e.code || e.key);
      if (e.isComposing || nativeComposition || !e.isTrusted || !keyOwner || !state.nativeControl || keyOwner !== state.nativeControl.control_id) return;
    }
    e.preventDefault();

    var key = e.key;
    if (key === ' ') key = 'space';

    transmitInputEvent({
      type: 'keyup',
      key: key
    });
  });

  // Track if clicks outside canvas drop control focus
  document.addEventListener('click', function(e) {
    if (e.target !== canvas && !canvas.contains(e.target)) {
      state.isControlFocused = false;
      if (state.nativeControl && state.nativeControl.writer === 'browser') clearWebsiteControl();
    }
  });
}

// ── Fullscreen Layout Toggle ─────────────────────────────────────────────────
// Uses a CSS-class-driven "inline fullscreen" instead of relying solely on the
// browser Fullscreen API, since requestFullscreen() is frequently blocked or
// silently unsupported inside the iOS/Android in-app browser-tunnel WebView
// that this page is normally viewed through.
function initFullscreenHandler() {
  var btn = $('toggle-fullscreen-btn');
  var exitBtn = $('fullscreen-exit-btn');
  var shell = $('viewer-shell');

  function syncFullscreenButton() {
    btn.innerHTML = state.inlineFullscreen ? '<span>▣</span> Exit Full' : '<span>⛶</span> Fullscreen';
  }

  function requestNativeFullscreen() {
    if (!shell) return;
    try {
      var result = shell.requestFullscreen ? shell.requestFullscreen() :
        (shell.webkitRequestFullscreen ? shell.webkitRequestFullscreen() : null);
      if (result && typeof result.catch === 'function') {
        result.catch(function() {});
      }
    } catch (_) {}
  }

  function exitNativeFullscreen() {
    try {
      var result = document.exitFullscreen ? document.exitFullscreen() :
        (document.webkitExitFullscreen ? document.webkitExitFullscreen() : null);
      if (result && typeof result.catch === 'function') {
        result.catch(function() {});
      }
    } catch (_) {}
  }

  function setInlineFullscreen(enabled) {
    state.inlineFullscreen = !!enabled;
    document.body.classList.toggle('inline-viewer-fullscreen', state.inlineFullscreen);
    syncViewportMetrics();
    syncFullscreenButton();
  }

  function exitInlineFullscreen() {
    setInlineFullscreen(false);
    exitNativeFullscreen();
  }

  btn.addEventListener('click', function() {
    if (state.inlineFullscreen) {
      exitInlineFullscreen();
      return;
    }
    setInlineFullscreen(true);
    requestNativeFullscreen();
  });

  if (exitBtn) {
    exitBtn.addEventListener('click', exitInlineFullscreen);
  }

  document.addEventListener('fullscreenchange', function() {
    if (!document.fullscreenElement && state.inlineFullscreen) setInlineFullscreen(false);
    else syncFullscreenButton();
  });

  document.addEventListener('webkitfullscreenchange', function() {
    if (!document.webkitFullscreenElement && state.inlineFullscreen) setInlineFullscreen(false);
    else syncFullscreenButton();
  });

  window.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && state.inlineFullscreen) {
      exitInlineFullscreen();
    }
  });

  syncFullscreenButton();
}

function initStreamToggleHandler() {
  var btn = $('stream-toggle-btn');
  if (!btn) return;

  btn.addEventListener('click', function() {
    state.isStreamDesired = !state.isStreamDesired;
    
    if (state.isStreamDesired) {
      btn.className = 'btn btn-outline btn-sm btn-icon btn-connect streaming';
      btn.innerHTML = '<span class="btn-icon">⏸</span> Stop Stream';
      
      updateOverlayState();
      startScreenStream();
    } else {
      btn.className = 'btn btn-outline btn-sm btn-icon btn-connect stopped';
      btn.innerHTML = '<span class="btn-icon">▶</span> Start Stream';
      
      if (state.ws) {
        try { state.ws.close(); } catch(_) {}
        state.ws = null;
      }
      if (state.wsReconnectTimer) {
        clearTimeout(state.wsReconnectTimer);
        state.wsReconnectTimer = null;
      }
      
      updateOverlayState();
      
      var indicator = $('viewer-status-dot');
      var connText = $('diag-connection');
      if (indicator) indicator.className = 'status-indicator';
      if (connText) {
        connText.textContent = 'Disconnected';
        connText.className = 'info-val status-disconnected';
      }
      $('diag-fps').textContent = '0 FPS';
      $('diag-latency').textContent = '-- ms';
    }
  });
}

function openSidebar() {
  var sidebar = $('sidebar');
  var backdrop = $('sidebar-backdrop');
  if (sidebar && backdrop) {
    sidebar.classList.add('active');
    backdrop.classList.add('active');
  }
}

function closeSidebar() {
  var sidebar = $('sidebar');
  var backdrop = $('sidebar-backdrop');
  if (sidebar && backdrop) {
    sidebar.classList.remove('active');
    backdrop.classList.remove('active');
  }
}

function initMobileSidebarHandler() {
  var toggleBtn = $('toggle-sidebar-btn');
  var sidebar = $('sidebar');
  var backdrop = $('sidebar-backdrop');
  
  if (!toggleBtn || !sidebar || !backdrop) return;
  
  toggleBtn.addEventListener('click', function(e) {
    e.stopPropagation();
    if (sidebar.classList.contains('active')) {
      closeSidebar();
    } else {
      openSidebar();
    }
  });
  
  backdrop.addEventListener('click', closeSidebar);
  
  // Close sidebar on mobile when appropriate casting mode button is tapped
  var tabButtons = document.querySelectorAll('.tab-btn');
  tabButtons.forEach(function(btn) {
    btn.addEventListener('click', function() {
      if (window.innerWidth <= 768) {
        var mode = btn.getAttribute('data-mode');
        // Keep sidebar open so user can select an application from the dropdown
        if (mode !== 'view_app') {
          closeSidebar();
        }
      }
    });
  });
}

// ── Application Startup Bootstrap ───────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
  _initTheme();
  initViewportMetrics();
  hydrateTopbarLinks();
  initAuth();
  initModeHandlers();
  initSliderHandlers();
  initInputCapture();
  initZoomControls();
  initNativeKeyboardHandler();
  initFloatingKeyboardBubble();
  initFullscreenHandler();
  initMobileSidebarHandler();
  initStreamToggleHandler();
});
