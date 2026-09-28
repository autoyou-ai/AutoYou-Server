// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

'use strict';

// Bootstrap injected by backend into window.__BOOTSTRAP__
var B = window.__BOOTSTRAP__ || {};
var agentName = B.agent_name || 'agent_builder_agent';
var auth = B.auth || {};

var selectedAgent = null;
var agentDetails = {};
var isSending = false;
var sessionId = null;

// ── Helpers ──────────────────────────────────────────────────────────────────

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

function showEl(id) { $(id).classList.remove('hidden'); }
function hideEl(id) { $(id).classList.add('hidden'); }
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
  if (!sessionId) {
    sessionId = 'builder-' + Math.random().toString(36).slice(2) + Date.now();
  }
  return sessionId;
}

// ── Auth gate ─────────────────────────────────────────────────────────────────

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

function _applyAuthenticated() {
  auth.authenticated = true;
  var chip = $('auth-chip');
  if (chip) { chip.textContent = 'Authenticated'; chip.className = 'status-chip authenticated'; }
  hideEl('login-gate');
  $('login-gate').inert = true;
  showEl('main-layout');
  $('main-layout').inert = false;
  loadAgents();
}

function initAuth() {
  var chip = $('auth-chip');
  if (auth.authenticated || auth.required === false) {
    auth.authenticated = true;
    chip.textContent = 'Authenticated';
    chip.className = 'status-chip authenticated';
    hideEl('login-gate');
    $('login-gate').inert = true;
    showEl('main-layout');
    $('main-layout').inert = false;
    loadAgents();
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
  showEl('login-gate');
  $('login-gate').inert = false;
  hideEl('main-layout');
  $('main-layout').inert = true;
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
        errEl.textContent = data.error || 'Invalid code. Try again.';
        errEl.classList.remove('hidden');
        btn.disabled = false;
        input.value = '';
        focusWithComfortScroll(input, otpScrollTarget);
      }
    }).catch(function() {
      errEl.textContent = 'Network error. Please retry.';
      errEl.classList.remove('hidden');
      btn.disabled = false;
    });
  }

  btn.addEventListener('click', attempt);
  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') attempt();
  });
}

// ── Agent list ────────────────────────────────────────────────────────────────

function loadAgents() {
  apiGet('./api/draft-state').then(function(data) {
    if (!data.success) return;
    agentDetails = {};
    (data.agent_details || []).forEach(function(d) { agentDetails[d.name] = d; });
    renderAgentList(data);
  }).catch(console.error);
}

var BUILTIN_AGENTS = [
  'admin_agent','notes_agent','internet_agent','audio_agent','page_agent',
  'memory_agent','agent_builder_agent','website_agent','coding_agent',
  'tasks_agent','notify_agent',
];

function renderAgentList(data) {
  var list = $('agent-list');
  var agents = data.all_agents || [];
  if (!agents.length) {
    list.innerHTML = '<div class="placeholder-text">No agents found.</div>';
    return;
  }
  list.innerHTML = '';
  agents.forEach(function(name) {
    var detail = agentDetails[name] || {};
    var isBuiltin = BUILTIN_AGENTS.indexOf(name) !== -1;
    var isLive = detail.is_live || isBuiltin;
    var isDraft = detail.is_draft && !isBuiltin;
    var dot = isBuiltin ? 'builtin' : (isLive ? 'live' : 'draft');
    var tag = isBuiltin ? 'built-in' : (isLive ? 'live' : 'draft');
    var tagClass = isBuiltin ? '' : (isLive ? 'live' : 'draft');

    var item = document.createElement('div');
    item.className = 'agent-item' + (selectedAgent === name ? ' selected' : '');
    item.innerHTML =
      '<div class="agent-dot ' + dot + '"></div>' +
      '<span class="agent-name">' + escHtml(name) + '</span>' +
      '<span class="agent-badge-tag ' + tagClass + '">' + tag + '</span>';
    item.addEventListener('click', function() { selectAgent(name); });
    list.appendChild(item);
  });
}

function selectAgent(name) {
  selectedAgent = name;
  var detail = agentDetails[name] || {};
  var isBuiltin = BUILTIN_AGENTS.indexOf(name) !== -1;

  // Update selected highlight
  document.querySelectorAll('.agent-item').forEach(function(el) {
    el.classList.toggle('selected', el.querySelector('.agent-name').textContent === name);
  });

  $('selected-agent-name').textContent = name;
  showEl('draft-actions');
  hideEl('no-selection-hint');

  var publishBtn = $('publish-btn');
  var discardBtn = $('discard-btn');
  var gotoBtn = $('goto-btn');

  if (isBuiltin) {
    publishBtn.disabled = true;
    discardBtn.disabled = true;
  } else if (detail.is_draft) {
    publishBtn.disabled = false;
    discardBtn.disabled = false;
  } else {
    publishBtn.disabled = true;
    discardBtn.disabled = true;
  }

  if (detail.go_to_path) {
    gotoBtn.href = resolveProxyPath(detail.go_to_path);
    gotoBtn.classList.remove('hidden');
  } else {
    gotoBtn.classList.add('hidden');
  }
}

$('refresh-agents-btn').addEventListener('click', loadAgents);

$('publish-btn').addEventListener('click', function() {
  if (!selectedAgent) return;
  var btn = this;
  btn.disabled = true;
  btn.textContent = 'Publishing…';
  apiPost('./api/agents/' + encodeURIComponent(selectedAgent) + '/publish', {}).then(function(data) {
    addBubble('agent', data.message || (data.success ? 'Agent published!' : (data.error || 'Failed.')));
    loadAgents();
    btn.textContent = 'Publish Draft → Live';
  }).catch(function() {
    addBubble('agent', 'Network error during publish.');
    btn.disabled = false;
    btn.textContent = 'Publish Draft → Live';
  });
});

$('discard-btn').addEventListener('click', function() {
  if (!selectedAgent) return;
  if (!confirm('Discard draft for ' + selectedAgent + '? This cannot be undone.')) return;
  var btn = this;
  btn.disabled = true;
  apiPost('./api/agents/' + encodeURIComponent(selectedAgent) + '/discard', {}).then(function(data) {
    addBubble('agent', data.message || (data.success ? 'Draft discarded.' : (data.error || 'Failed.')));
    selectedAgent = null;
    hideEl('draft-actions');
    showEl('no-selection-hint');
    loadAgents();
  }).catch(function() {
    addBubble('agent', 'Network error during discard.');
    btn.disabled = false;
  });
});

// ── Chat ──────────────────────────────────────────────────────────────────────

function addBubble(role, text) {
  var msgs = $('chat-messages');
  var welcome = msgs.querySelector('.chat-welcome');
  if (welcome) welcome.remove();

  var wrap = document.createElement('div');
  wrap.className = 'chat-bubble ' + (role === 'user' ? 'bubble-user' : (role === 'error' ? 'bubble-error' : 'bubble-agent'));

  var label = document.createElement('div');
  label.className = 'bubble-label';
  label.textContent = role === 'user' ? 'You' : (role === 'error' ? 'Error' : 'Agent Builder');
  wrap.appendChild(label);

  var body = document.createElement('div');
  body.textContent = text;
  wrap.appendChild(body);

  msgs.appendChild(wrap);
  msgs.scrollTop = msgs.scrollHeight;
  return { wrap: wrap, body: body };
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

  var sid = ensureSessionId();
  var userId = 'builder-user';

  apiPost('./api/chat', {
    message: text,
    user_id: userId,
    session_id: sid,
    agent_name: 'autoyou',
  }).then(function(data) {
    thinking.wrap.remove();
    var reply = extractReply(data);
    addBubble('agent', reply);
    // Reload agent list in case agent was created
    setTimeout(loadAgents, 1000);
  }).catch(function(err) {
    thinking.wrap.remove();
    addBubble('error', 'Failed to send message: ' + String(err));
  }).finally(function() {
    isSending = false;
    $('send-btn').disabled = false;
    input.focus();
  });
}

function extractReply(data) {
  if (typeof data === 'string') return data;
  if (data.response) return data.response;
  if (data.text) return data.text;
  if (data.message) return data.message;
  if (data.error) return 'Error: ' + data.error;
  return JSON.stringify(data, null, 2);
}

$('send-btn').addEventListener('click', sendMessage);
$('chat-input').addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Auto-grow textarea
$('chat-input').addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 160) + 'px';
});

// ── Utilities ─────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Init ──────────────────────────────────────────────────────────────────────

_initTheme();
hydrateTopbarLinks();
initAuth();
