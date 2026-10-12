// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

'use strict';

var B = window.__BOOTSTRAP__ || {};
var agentName = B.agent_name || 'website_agent';
var auth = B.auth || {};

var selectedAgent = null;
var isSending = false;
var sessionId = null;
var previewPollTimer = null;

function $(id) { return document.getElementById(id); }

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

// ── Session token (Bearer auth for local website access) ──────────────────────
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
    body: JSON.stringify(body),
  }).then(function(r) { return r.json(); });
}
function apiGet(path) {
  var t = _storedToken();
  var opts = t ? { headers: { 'Authorization': 'Bearer ' + t } } : undefined;
  return fetch(path, opts).then(function(r) { return r.json(); });
}
function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
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
function ensureSessionId() {
  if (!sessionId) sessionId = 'website-' + Math.random().toString(36).slice(2) + Date.now();
  return sessionId;
}

// ── Theme ─────────────────────────────────────────────────────────────────────

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

// ── Auth ──────────────────────────────────────────────────────────────────────

function _applyAuthenticated() {
  auth.authenticated = true;
  var chip = $('auth-chip');
  if (chip) { chip.textContent = 'Authenticated'; chip.className = 'status-chip authenticated'; }
  $('login-gate').classList.add('hidden');
  $('main-layout').classList.remove('hidden');
  loadAgentList();
}

function initAuth() {
  var chip = $('auth-chip');
  if (auth.authenticated) {
    chip.textContent = 'Authenticated';
    chip.className = 'status-chip authenticated';
    $('login-gate').classList.add('hidden');
    $('main-layout').classList.remove('hidden');
    loadAgentList();
  } else if (_storedToken()) {
    // Validate stored token against server before showing UI
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
}

function _showLoginGate(chip) {
  if (chip) { chip.textContent = 'Locked'; chip.className = 'status-chip'; }
  $('login-gate').classList.remove('hidden');
  $('main-layout').classList.add('hidden');
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
        errEl.textContent = data.error || 'Invalid code.';
        errEl.classList.remove('hidden');
        btn.disabled = false;
        input.value = '';
        focusWithComfortScroll(input, otpScrollTarget);
      }
    }).catch(function() {
      errEl.textContent = 'Network error.';
      errEl.classList.remove('hidden');
      btn.disabled = false;
    });
  }
  btn.addEventListener('click', attempt);
  input.addEventListener('keydown', function(e) { if (e.key === 'Enter') attempt(); });
}

// ── Agent picker ──────────────────────────────────────────────────────────────

function loadAgentList() {
  apiGet('./api/agents/list').then(function(data) {
    if (!data.success) return;
    var sel = $('agent-select');
    var prev = sel.value;
    while (sel.options.length > 1) sel.remove(1);
    (data.agents || []).forEach(function(a) {
      var opt = document.createElement('option');
      opt.value = a.name;
      opt.textContent = a.name + (a.published ? ' ✓' : '');
      sel.appendChild(opt);
    });
    if (prev && sel.querySelector('option[value="' + prev + '"]')) {
      sel.value = prev;
    }
  }).catch(console.error);
}

$('agent-select').addEventListener('change', function() {
  selectedAgent = this.value || null;
  updatePublishButtons();
  if (selectedAgent) {
    startPreviewPolling();
  } else {
    stopPreviewPolling();
    showPlaceholder();
  }
});

function updatePublishButtons() {
  var has = !!selectedAgent;
  $('publish-btn').disabled = !has;
  $('unpublish-btn').disabled = !has;
}

$('publish-btn').addEventListener('click', function() {
  if (!selectedAgent) return;
  var btn = this;
  btn.disabled = true;
  apiPost('./api/publish/' + encodeURIComponent(selectedAgent), {}).then(function(data) {
    addBubble('agent', data.message || (data.success ? 'Published!' : (data.error || 'Failed.')));
    setPublishStatus(data.success);
    loadAgentList();
    if (data.success) setTimeout(checkPreview, 1500);
  }).catch(function() {
    addBubble('error', 'Network error during publish.');
  }).finally(function() { btn.disabled = false; });
});

$('unpublish-btn').addEventListener('click', function() {
  if (!selectedAgent) return;
  var btn = this;
  btn.disabled = true;
  apiPost('./api/unpublish/' + encodeURIComponent(selectedAgent), {}).then(function(data) {
    addBubble('agent', data.message || (data.success ? 'Unpublished.' : (data.error || 'Failed.')));
    setPublishStatus(false);
    showPlaceholder();
    loadAgentList();
  }).catch(function() {
    addBubble('error', 'Network error during unpublish.');
  }).finally(function() { btn.disabled = false; });
});

function setPublishStatus(published) {
  var el = $('publish-status');
  el.textContent = published ? 'Published' : '';
  el.className = 'publish-status' + (published ? ' published' : '');
}

// ── Preview panel ─────────────────────────────────────────────────────────────

function showPlaceholder() {
  $('preview-placeholder').classList.remove('hidden');
  $('preview-iframe').classList.add('hidden');
  $('preview-iframe').src = '';
}

function showIframe(path) {
  $('preview-placeholder').classList.add('hidden');
  var iframe = $('preview-iframe');
  iframe.classList.remove('hidden');
  var resolved = resolveProxyPath(path);
  if (iframe.src !== resolved) iframe.src = resolved;
  setPublishStatus(true);
}

function checkPreview() {
  if (!selectedAgent) return;
  apiGet('./api/preview/' + encodeURIComponent(selectedAgent)).then(function(data) {
    if (data.available && data.path) {
      showIframe(data.path);
    } else {
      showPlaceholder();
    }
  }).catch(console.error);
}

function startPreviewPolling() {
  stopPreviewPolling();
  checkPreview();
  previewPollTimer = setInterval(checkPreview, 5000);
}

function stopPreviewPolling() {
  if (previewPollTimer) { clearInterval(previewPollTimer); previewPollTimer = null; }
}

// ── Chat ──────────────────────────────────────────────────────────────────────

function addBubble(role, text) {
  var msgs = $('chat-messages');
  var welcome = msgs.querySelector('.chat-welcome');
  if (welcome) welcome.remove();

  var wrap = document.createElement('div');
  wrap.className = 'chat-bubble ' + (
    role === 'user' ? 'bubble-user' :
    role === 'error' ? 'bubble-error' :
    'bubble-agent'
  );
  var label = document.createElement('div');
  label.className = 'bubble-label';
  label.textContent = role === 'user' ? 'You' : (role === 'error' ? 'Error' : 'Website Builder');
  wrap.appendChild(label);
  var body = document.createElement('div');
  body.textContent = text;
  wrap.appendChild(body);
  msgs.appendChild(wrap);
  msgs.scrollTop = msgs.scrollHeight;
  return { wrap: wrap, body: body };
}

function extractReply(data) {
  if (typeof data === 'string') return data;
  if (data.response) return data.response;
  if (data.text) return data.text;
  if (data.message) return data.message;
  if (data.error) return 'Error: ' + data.error;
  return JSON.stringify(data, null, 2);
}

function sendMessage() {
  if (isSending) return;
  var input = $('chat-input');
  var text = input.value.trim();
  if (!text) return;
  isSending = true;
  input.value = '';
  $('send-btn').disabled = true;
  addBubble('user', text);
  var thinking = addBubble('agent', '…');
  thinking.wrap.classList.add('bubble-thinking');

  apiPost('./api/chat', {
    message: text,
    user_id: 'website-user',
    session_id: ensureSessionId(),
    agent_name: 'autoyou',
  }).then(function(data) {
    thinking.wrap.remove();
    addBubble('agent', extractReply(data));
    // Refresh agent list and preview after a build step
    setTimeout(function() { loadAgentList(); if (selectedAgent) checkPreview(); }, 1500);
  }).catch(function(err) {
    thinking.wrap.remove();
    addBubble('error', 'Failed: ' + String(err));
  }).finally(function() {
    isSending = false;
    $('send-btn').disabled = false;
    input.focus();
  });
}

$('send-btn').addEventListener('click', sendMessage);
$('chat-input').addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
$('chat-input').addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 140) + 'px';
});

// ── Init ──────────────────────────────────────────────────────────────────────
_initTheme();
hydrateTopbarLinks();
initAuth();
