// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

'use strict';

var B = window.__BOOTSTRAP__ || {};
var agentName = B.agent_name || 'agent_builder_agent';
var auth = B.auth || {};

var allAgents = [];
var agentDetails = {};
var selectedAgent = null;
var activeFilter = 'all';
var searchQuery = '';
var isSending = false;
var sessionId = null;

var BUILTIN_AGENTS = [
  'admin_agent', 'notes_agent', 'internet_agent', 'music_agent', 'page_agent',
  'memory_agent', 'agent_builder_agent', 'website_agent', 'coding_agent',
  'tasks_agent', 'reminders_agent', 'ads_watching_agent', 'diary_agent',
  'games_agent', 'remote_desktop_agent', 'assistant_agent',
];

function $(id) { return document.getElementById(id); }
function showEl(id) { var el = $(id); if (el) el.classList.remove('hidden'); }
function hideEl(id) { var el = $(id); if (el) el.classList.add('hidden'); }
function escHtml(str) {
  return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function proxyRootPrefix() {
  var pathname = String((window.location && window.location.pathname) || '');
  var idx = pathname.indexOf('/agent/');
  return idx < 0 ? '' : pathname.slice(0, idx);
}

function resolveProxyPath(path) {
  var raw = String(path || '').trim();
  if (!raw || /^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.startsWith('//') || !raw.startsWith('/')) return raw;
  var root = proxyRootPrefix();
  if (!root || raw === root || raw.startsWith(root + '/')) return raw;
  return root + raw;
}

function resolveAgentRoute(agent) {
  return resolveProxyPath('/agent/' + encodeURIComponent(String(agent || '').trim()) + '/');
}

function hydrateTopbarLinks() {
  document.querySelectorAll('[data-agent-link]').forEach(function(el) {
    var target = String(el.getAttribute('data-agent-link') || '').trim();
    if (target) el.href = resolveAgentRoute(target);
  });
}

// ── Auth & Fetch ────────────────────────────────────────────────────────────
var _SESSION_TOKEN_KEY = 'autoyou_chat_token_' + agentName;
function _storedToken() { try { return localStorage.getItem(_SESSION_TOKEN_KEY) || ''; } catch (_) { return ''; } }
function _saveToken(t) { try { if (t) localStorage.setItem(_SESSION_TOKEN_KEY, t); else localStorage.removeItem(_SESSION_TOKEN_KEY); } catch (_) {} }

function _authHeaders() {
  var t = _storedToken();
  return t ? { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + t }
           : { 'Content-Type': 'application/json' };
}

function apiPost(path, body) {
  return fetch(path, { method: 'POST', headers: _authHeaders(), body: JSON.stringify(body || {}) })
    .then(function(r) { return r.json(); });
}

function apiGet(path) {
  var t = _storedToken();
  var opts = t ? { headers: { 'Authorization': 'Bearer ' + t } } : undefined;
  return fetch(path, opts).then(function(r) { return r.json(); });
}

function ensureSessionId() {
  if (!sessionId) {
    sessionId = 'builder-' + ((window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : String(Date.now()));
  }
  return sessionId;
}

// ── Theme & Mobile Panes ────────────────────────────────────────────────────
function _getTheme() { try { return localStorage.getItem('autoyou.ui.theme') || 'dark'; } catch (_) { return 'dark'; } }
function _setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('autoyou.ui.theme', t); } catch (_) {}
}
function _initTheme() {
  _setTheme(_getTheme());
  var btn = $('theme-toggle');
  if (btn) btn.addEventListener('click', function() { _setTheme(_getTheme() === 'dark' ? 'light' : 'dark'); });
}

function setMobilePane(pane) {
  var layout = $('main-layout');
  if (layout) layout.setAttribute('data-mobile-pane', pane);
  document.querySelectorAll('.mobile-tab').forEach(function(tab) {
    var active = tab.getAttribute('data-mobile-pane') === pane;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });
}

// ── Auth Gate ───────────────────────────────────────────────────────────────
function _applyAuthenticated() {
  auth.authenticated = true;
  var chip = $('auth-chip');
  if (chip) { chip.textContent = 'Ready'; chip.className = 'status-chip authenticated'; }
  hideEl('login-gate');
  $('login-gate').inert = true;
  showEl('main-layout');
  $('main-layout').inert = false;
  showEl('mobile-tabs');
  loadAgents();
}

function _showLoginGate(chip) {
  if (chip) { chip.textContent = 'Locked'; chip.className = 'status-chip'; }
  showEl('login-gate');
  $('login-gate').inert = false;
  hideEl('main-layout');
  $('main-layout').inert = true;
  hideEl('mobile-tabs');
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
        errEl.textContent = data.error || 'Invalid code. Try again.';
        showEl('otp-error');
        btn.disabled = false;
        input.value = '';
        input.focus();
      }
    }).catch(function() {
      errEl.textContent = 'Network error. Please retry.';
      showEl('otp-error');
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
}

// ── Agent Catalog ───────────────────────────────────────────────────────────
function classifyAgent(name, detail) {
  var isBuiltin = !!(detail.is_builtin || BUILTIN_AGENTS.indexOf(name) !== -1);
  var kind = detail.status_kind;
  if (!kind) {
    if (isBuiltin) kind = 'builtin';
    else if (detail.is_live) kind = 'live';
    else if (detail.has_staged_draft) kind = 'custom_draft';
    else kind = 'available';
  }
  var labelMap = { builtin: 'Built-in', live: 'Live', custom_draft: 'Draft', available: 'Available' };
  var clsMap = { builtin: 'builtin', live: 'live', custom_draft: 'draft', available: 'available' };
  return {
    kind: kind,
    label: labelMap[kind] || 'Available',
    cls: clsMap[kind] || 'available',
    title: detail.display_name || name.replace(/_agent$/, '').replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); }),
    desc: detail.description || '',
  };
}

function loadAgents() {
  apiGet('./api/draft-state').then(function(data) {
    if (!data || !data.success) return;
    agentDetails = {};
    (data.agent_details || []).forEach(function(d) { agentDetails[d.name] = d; });
    allAgents = (data.all_agents || []).slice();
    if ($('mobile-agents-count')) $('mobile-agents-count').textContent = String(allAgents.length);
    renderAgentList();
    if (selectedAgent && agentDetails[selectedAgent]) updateInspector(selectedAgent);
  }).catch(console.error);
}

function renderAgentList() {
  var list = $('agent-list');
  if (!list) return;
  var q = searchQuery.toLowerCase();

  var filtered = allAgents.filter(function(name) {
    var detail = agentDetails[name] || {};
    var meta = classifyAgent(name, detail);
    if (activeFilter === 'live' && !(meta.kind === 'live' || meta.kind === 'builtin')) return false;
    if (activeFilter === 'available' && meta.kind !== 'available') return false;
    if (activeFilter === 'draft' && meta.kind !== 'custom_draft') return false;
    if (q) {
      var hay = (name + ' ' + meta.title + ' ' + meta.desc).toLowerCase();
      if (hay.indexOf(q) < 0) return false;
    }
    return true;
  }).sort(function(a, b) {
    var order = { custom_draft: 0, live: 1, builtin: 2, available: 3 };
    var ma = classifyAgent(a, agentDetails[a] || {});
    var mb = classifyAgent(b, agentDetails[b] || {});
    if (order[ma.kind] !== order[mb.kind]) return order[ma.kind] - order[mb.kind];
    return ma.title.localeCompare(mb.title);
  });

  if (!filtered.length) {
    list.innerHTML = '<div class="placeholder-text">No matching agents. Try another filter or create a new one in chat.</div>';
    return;
  }

  list.innerHTML = '';
  filtered.forEach(function(name) {
    var detail = agentDetails[name] || {};
    var meta = classifyAgent(name, detail);
    var item = document.createElement('div');
    item.className = 'agent-item' + (selectedAgent === name ? ' selected' : '');
    item.innerHTML =
      '<div class="agent-row-top">'
        + '<div class="agent-title-wrap"><span class="agent-dot ' + meta.cls + '"></span><span class="agent-title">' + escHtml(meta.title) + '</span></div>'
        + '<span class="agent-badge-tag ' + meta.cls + '">' + escHtml(meta.label) + '</span>'
      + '</div>'
      + '<div class="agent-sub-row">'
        + '<span class="agent-name">' + escHtml(name) + '</span>'
        + (detail.has_website ? '<span class="ui-pill">Website</span>' : '')
      + '</div>'
      + (meta.desc ? '<div class="agent-desc">' + escHtml(meta.desc) + '</div>' : '');
    item.addEventListener('click', function() { selectAgent(name); });
    list.appendChild(item);
  });
}

function updateInspector(name) {
  var detail = agentDetails[name] || {};
  var meta = classifyAgent(name, detail);

  if ($('selected-agent-title')) $('selected-agent-title').textContent = meta.title;
  if ($('selected-agent-name')) $('selected-agent-name').textContent = name;
  if ($('selected-agent-desc')) $('selected-agent-desc').textContent = meta.desc || 'Manage or customize this agent using Agent Studio.';
  var badge = $('selected-agent-badge');
  if (badge) { badge.textContent = meta.label; badge.className = 'agent-badge-tag ' + meta.cls; }

  showEl('draft-actions');
  hideEl('no-selection-hint');

  var publishBtn = $('publish-btn');
  var discardBtn = $('discard-btn');
  var gotoBtn = $('goto-btn');
  var wsBtn = $('website-studio-btn');

  if (meta.kind === 'custom_draft') {
    publishBtn.textContent = 'Publish Draft → Live';
    publishBtn.disabled = false;
    showEl('publish-btn');
    discardBtn.disabled = false;
    showEl('discard-btn');
  } else if (meta.kind === 'available') {
    publishBtn.textContent = 'Enable Agent';
    publishBtn.disabled = false;
    showEl('publish-btn');
    discardBtn.disabled = true;
    hideEl('discard-btn');
  } else {
    publishBtn.textContent = 'Active';
    publishBtn.disabled = true;
    hideEl('publish-btn');
    discardBtn.disabled = true;
    hideEl('discard-btn');
  }

  if (detail.go_to_path) {
    gotoBtn.href = resolveProxyPath(detail.go_to_path);
    showEl('goto-btn');
  } else {
    hideEl('goto-btn');
  }

  if (wsBtn) {
    wsBtn.href = resolveProxyPath(detail.website_builder_path || ('/agent/website_agent/?agent=' + encodeURIComponent(name)));
    showEl('website-studio-btn');
  }
}

function selectAgent(name) {
  selectedAgent = name;
  renderAgentList();
  updateInspector(name);
  if (window.innerWidth <= 820) setMobilePane('build');
}

// ── Actions ─────────────────────────────────────────────────────────────────
$('refresh-agents-btn').addEventListener('click', loadAgents);

$('agent-search').addEventListener('input', function() {
  searchQuery = this.value || '';
  renderAgentList();
});

document.querySelectorAll('.filter-pill').forEach(function(pill) {
  pill.addEventListener('click', function() {
    activeFilter = pill.getAttribute('data-filter') || 'all';
    document.querySelectorAll('.filter-pill').forEach(function(p) { p.classList.toggle('active', p === pill); });
    renderAgentList();
  });
});

document.querySelectorAll('.mobile-tab').forEach(function(tab) {
  tab.addEventListener('click', function() { setMobilePane(tab.getAttribute('data-mobile-pane') || 'build'); });
});

document.querySelectorAll('.blueprint-card').forEach(function(card) {
  card.addEventListener('click', function() {
    var input = $('chat-input');
    if (input) {
      input.value = card.getAttribute('data-prompt') || '';
      input.focus();
    }
  });
});

if ($('customize-btn')) {
  $('customize-btn').addEventListener('click', function() {
    if (!selectedAgent) return;
    var input = $('chat-input');
    if (input) {
      input.value = 'Inspect ' + selectedAgent + ' and help me improve its instructions and capabilities.';
      input.focus();
    }
  });
}

$('publish-btn').addEventListener('click', function() {
  if (!selectedAgent) return;
  var btn = this;
  var origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Working…';
  apiPost('./api/agents/' + encodeURIComponent(selectedAgent) + '/publish', {}).then(function(data) {
    addBubble('agent', data.message || (data.success ? selectedAgent + ' is now live!' : (data.error || 'Failed to publish.')));
    loadAgents();
  }).catch(function() {
    addBubble('error', 'Network error while enabling agent.');
  }).finally(function() {
    btn.textContent = origText;
  });
});

$('discard-btn').addEventListener('click', function() {
  if (!selectedAgent) return;
  if (!confirm('Discard custom draft for ' + selectedAgent + '? This cannot be undone.')) return;
  var btn = this;
  btn.disabled = true;
  apiPost('./api/agents/' + encodeURIComponent(selectedAgent) + '/discard', {}).then(function(data) {
    addBubble('agent', data.message || (data.success ? 'Draft discarded.' : (data.error || 'Failed.')));
    selectedAgent = null;
    hideEl('draft-actions');
    showEl('no-selection-hint');
    loadAgents();
  }).catch(function() {
    addBubble('error', 'Network error while discarding draft.');
    btn.disabled = false;
  });
});

// ── Chat ────────────────────────────────────────────────────────────────────
function addBubble(role, text) {
  var msgs = $('chat-messages');
  var welcome = msgs.querySelector('.chat-welcome');
  if (welcome) welcome.remove();

  var wrap = document.createElement('div');
  wrap.className = 'chat-bubble ' + (role === 'user' ? 'bubble-user' : (role === 'error' ? 'bubble-error' : 'bubble-agent'));

  var label = document.createElement('div');
  label.className = 'bubble-label';
  label.textContent = role === 'user' ? 'You' : (role === 'error' ? 'Error' : 'Agent Studio');
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
  var text = (input.value || '').trim();
  if (!text) return;

  isSending = true;
  input.value = '';
  input.style.height = 'auto';
  $('send-btn').disabled = true;

  addBubble('user', text);
  var thinking = addBubble('agent', 'Working on your agent…');
  thinking.wrap.classList.add('bubble-thinking');

  apiPost('./api/chat', {
    message: text,
    user_id: 'builder-user',
    session_id: ensureSessionId(),
    agent_name: 'autoyou',
  }).then(function(data) {
    thinking.wrap.remove();
    var reply = (typeof data === 'string') ? data
      : (data.response || data.text || data.message || (data.error ? 'Error: ' + data.error : JSON.stringify(data, null, 2)));
    addBubble('agent', reply);
    setTimeout(loadAgents, 1000);
  }).catch(function(err) {
    thinking.wrap.remove();
    addBubble('error', 'Failed to send: ' + String(err));
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

// ── Boot ────────────────────────────────────────────────────────────────────
_initTheme();
setMobilePane('build');
hydrateTopbarLinks();
initAuth();
