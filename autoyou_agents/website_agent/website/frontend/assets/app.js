// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

'use strict';

var B = window.__BOOTSTRAP__ || {};
var agentName = B.agent_name || 'website_agent';
var auth = B.auth || {};

var agents = [];
var selectedAgent = null;
var currentPreviewUrl = '';
var isSending = false;
var previewPollTimer = null;
var sessionId = null;

function $(id) { return document.getElementById(id); }
function showEl(id) { var el = $(id); if (el) el.classList.remove('hidden'); }
function hideEl(id) { var el = $(id); if (el) el.classList.add('hidden'); }

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

// ── Session token & API ─────────────────────────────────────────────────────
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
    sessionId = 'website-' + ((window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : String(Date.now()));
  }
  return sessionId;
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
  if (btn) btn.addEventListener('click', function() { _setTheme(_getTheme() === 'dark' ? 'light' : 'dark'); });
}

// ── Mobile Tabs & Viewport Switcher ─────────────────────────────────────────
function setMobilePane(pane) {
  var layout = $('main-layout');
  if (layout) layout.setAttribute('data-mobile-pane', pane);
  document.querySelectorAll('.mobile-tab').forEach(function(tab) {
    var active = tab.getAttribute('data-mobile-pane') === pane;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });
}

function initWorkspaceControls() {
  setMobilePane('preview');
  document.querySelectorAll('.mobile-tab').forEach(function(tab) {
    tab.addEventListener('click', function() { setMobilePane(tab.getAttribute('data-mobile-pane') || 'preview'); });
  });

  document.querySelectorAll('.vp-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var vp = btn.getAttribute('data-viewport') || 'full';
      var panel = $('preview-panel');
      if (panel) panel.setAttribute('data-viewport', vp);
      document.querySelectorAll('.vp-btn').forEach(function(b) { b.classList.toggle('active', b === btn); });
    });
  });

  if ($('placeholder-chat-btn')) {
    $('placeholder-chat-btn').addEventListener('click', function() {
      setMobilePane('chat');
      if ($('chat-input')) $('chat-input').focus();
    });
  }

  if ($('placeholder-publish-btn')) {
    $('placeholder-publish-btn').addEventListener('click', function() {
      if ($('publish-btn') && !$('publish-btn').disabled) $('publish-btn').click();
    });
  }

  if ($('reload-preview-btn')) {
    $('reload-preview-btn').addEventListener('click', function() { reloadCurrentIframe(); });
  }

  if ($('clear-chat-btn')) {
    $('clear-chat-btn').addEventListener('click', function() {
      sessionId = null;
      var msgs = $('chat-messages');
      if (msgs) {
        msgs.innerHTML =
          '<div class="chat-welcome">'
          + '<h3>Design &amp; Deploy Agent UIs</h3>'
          + '<p>Ask Website Studio to build, redesign, or optimize any agent\'s frontend for desktop and mobile.</p>'
          + '</div>';
      }
    });
  }

  document.querySelectorAll('.starter-chip').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var prompt = btn.getAttribute('data-prompt') || '';
      if (selectedAgent && prompt.indexOf('selected agent') >= 0) {
        prompt = prompt.replace('selected agent', selectedAgent);
      }
      var input = $('chat-input');
      if (input) {
        input.value = prompt;
        input.focus();
      }
    });
  });

  var iframe = $('preview-iframe');
  if (iframe) {
    iframe.addEventListener('load', function() {
      hideEl('preview-loading');
    });
  }
}

// ── Auth ────────────────────────────────────────────────────────────────────
function _applyAuthenticated() {
  auth.authenticated = true;
  var chip = $('auth-chip');
  if (chip) { chip.textContent = 'Ready'; chip.className = 'status-chip authenticated'; }
  hideEl('login-gate');
  showEl('main-layout');
  showEl('mobile-tabs');
  loadAgents(true);
}

function _showLoginGate(chip) {
  if (chip) { chip.textContent = 'Locked'; chip.className = 'status-chip'; }
  showEl('login-gate');
  hideEl('main-layout');
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

// ── Agents & Preview ────────────────────────────────────────────────────────
function labelForAgent(a) {
  var title = a.display_name || a.name.replace(/_agent$/, '').replace(/_/g, ' ');
  var tag = a.published ? 'Live' : (a.has_website ? 'Unpublished' : 'No UI');
  return title + ' (' + a.name + ') · ' + tag;
}

function loadAgents(autoSelect) {
  apiGet('./api/agents/list').then(function(data) {
    if (!data || !data.success) return;
    agents = (data.agents || []).slice().sort(function(a, b) {
      if (a.published !== b.published) return a.published ? -1 : 1;
      if (a.has_website !== b.has_website) return a.has_website ? -1 : 1;
      return (a.display_name || a.name).localeCompare(b.display_name || b.name);
    });
    renderAgentSelect();

    var urlParam = '';
    try { urlParam = new URLSearchParams(window.location.search).get('agent') || ''; } catch (_) {}
    if (autoSelect && !selectedAgent) {
      if (urlParam && agents.some(function(a) { return a.name === urlParam; })) {
        selectAgent(urlParam);
      } else {
        var preferred = agents.find(function(a) { return a.published && a.name !== 'website_agent'; });
        if (preferred) selectAgent(preferred.name);
      }
    } else if (selectedAgent) {
      updateSelectedMeta();
    }
  }).catch(console.error);
}

function renderAgentSelect() {
  var sel = $('agent-select');
  if (!sel) return;
  var prev = selectedAgent || sel.value;
  sel.innerHTML = '<option value="">Choose an agent website…</option>';

  var groups = [
    { label: 'Published Websites', items: agents.filter(function(a) { return a.published; }) },
    { label: 'Unpublished Websites', items: agents.filter(function(a) { return !a.published && a.has_website; }) },
    { label: 'Agents Without Website', items: agents.filter(function(a) { return !a.has_website; }) },
  ];

  groups.forEach(function(g) {
    if (!g.items.length) return;
    var og = document.createElement('optgroup');
    og.label = g.label;
    g.items.forEach(function(a) {
      var opt = document.createElement('option');
      opt.value = a.name;
      opt.textContent = labelForAgent(a);
      og.appendChild(opt);
    });
    sel.appendChild(og);
  });

  if (prev) sel.value = prev;
}

function updateSelectedMeta() {
  var info = agents.find(function(a) { return a.name === selectedAgent; });
  var pubBtn = $('publish-btn');
  var unpubBtn = $('unpublish-btn');
  var statusEl = $('publish-status');
  var openBtn = $('open-tab-btn');
  var reloadBtn = $('reload-preview-btn');
  var chatSub = $('chat-target-sub');
  var phPublishBtn = $('placeholder-publish-btn');

  if (!info) {
    pubBtn.disabled = true;
    unpubBtn.disabled = true;
    reloadBtn.disabled = true;
    hideEl('open-tab-btn');
    hideEl('placeholder-publish-btn');
    statusEl.textContent = 'Select agent';
    statusEl.className = 'publish-pill';
    if (chatSub) chatSub.textContent = 'Select an agent or describe any new UI';
    return;
  }

  pubBtn.disabled = !info.has_website;
  unpubBtn.disabled = !info.published;
  if (chatSub) chatSub.textContent = 'Editing ' + (info.display_name || info.name) + ' (' + info.name + ')';

  if (info.published && info.preview_path) {
    statusEl.textContent = 'Published';
    statusEl.className = 'publish-pill published';
    var resolved = resolveProxyPath(info.preview_path);
    openBtn.href = resolved;
    showEl('open-tab-btn');
    reloadBtn.disabled = false;
    hideEl('placeholder-publish-btn');
    showPreviewIframe(resolved);
  } else if (info.has_website) {
    statusEl.textContent = 'Unpublished';
    statusEl.className = 'publish-pill unpublished';
    hideEl('open-tab-btn');
    reloadBtn.disabled = true;
    showEl('placeholder-publish-btn');
    showPreviewPlaceholder(
      (info.display_name || info.name) + ' is not published yet',
      'Click Publish to start this agent\'s website server and preview it live here.'
    );
  } else {
    statusEl.textContent = 'No Website';
    statusEl.className = 'publish-pill';
    hideEl('open-tab-btn');
    reloadBtn.disabled = true;
    hideEl('placeholder-publish-btn');
    showPreviewPlaceholder(
      'No website yet for ' + (info.display_name || info.name),
      'Switch to Builder Chat and describe the interface you want Website Studio to build.'
    );
  }
}

function selectAgent(name) {
  selectedAgent = name || null;
  var sel = $('agent-select');
  if (sel && sel.value !== (name || '')) sel.value = name || '';
  if (previewPollTimer) { clearInterval(previewPollTimer); previewPollTimer = null; }

  if (!selectedAgent) {
    currentPreviewUrl = '';
    updateSelectedMeta();
    showPreviewPlaceholder(
      'Preview Any Agent Website',
      'Choose a published agent from the picker above to inspect it live, or ask Website Studio to design a new UI.'
    );
    return;
  }

  updateSelectedMeta();
  checkPreview();
  previewPollTimer = setInterval(checkPreview, 8000);
}

function checkPreview() {
  if (!selectedAgent) return;
  apiGet('./api/preview/' + encodeURIComponent(selectedAgent)).then(function(data) {
    if (!data || !data.success) return;
    var path = data.preview_path || data.path;
    if ((data.published || data.available) && path) {
      var resolved = resolveProxyPath(path);
      showPreviewIframe(resolved);
      $('publish-status').textContent = 'Published';
      $('publish-status').className = 'publish-pill published';
      $('unpublish-btn').disabled = false;
      $('reload-preview-btn').disabled = false;
      $('open-tab-btn').href = resolved;
      showEl('open-tab-btn');
      hideEl('placeholder-publish-btn');
    }
  }).catch(function() {});
}

function showPreviewIframe(resolvedPath) {
  hideEl('preview-placeholder');
  var iframe = $('preview-iframe');
  showEl('preview-iframe');
  // Compare normalized logical path instead of iframe.src (which is always absolute),
  // preventing the old infinite 5-second reload loop.
  if (currentPreviewUrl !== resolvedPath) {
    currentPreviewUrl = resolvedPath;
    showEl('preview-loading');
    iframe.src = resolvedPath;
  }
}

function reloadCurrentIframe() {
  var iframe = $('preview-iframe');
  if (!iframe || !currentPreviewUrl) return;
  showEl('preview-loading');
  var sep = currentPreviewUrl.indexOf('?') >= 0 ? '&' : '?';
  iframe.src = currentPreviewUrl + sep + '_r=' + Date.now();
}

function showPreviewPlaceholder(title, msg) {
  currentPreviewUrl = '';
  hideEl('preview-loading');
  hideEl('preview-iframe');
  var iframe = $('preview-iframe');
  if (iframe) iframe.src = 'about:blank';
  if ($('placeholder-title')) $('placeholder-title').textContent = title || 'Preview Any Agent Website';
  if ($('placeholder-copy')) $('placeholder-copy').textContent = msg || '';
  showEl('preview-placeholder');
}

$('agent-select').addEventListener('change', function() { selectAgent(this.value); });

$('publish-btn').addEventListener('click', function() {
  if (!selectedAgent) return;
  var btn = this;
  btn.disabled = true;
  btn.textContent = 'Publishing…';
  apiPost('./api/publish/' + encodeURIComponent(selectedAgent), {}).then(function(data) {
    addBubble('agent', data.message || (data.success ? 'Published ' + selectedAgent + '!' : (data.error || 'Publish failed.')));
    if (data.success && (data.path || data.preview_path)) {
      currentPreviewUrl = '';
      showPreviewIframe(resolveProxyPath(data.path || data.preview_path));
    }
    loadAgents(false);
  }).catch(function() {
    addBubble('error', 'Network error during publish.');
  }).finally(function() {
    btn.textContent = 'Publish';
    updateSelectedMeta();
  });
});

$('unpublish-btn').addEventListener('click', function() {
  if (!selectedAgent) return;
  if (!confirm('Stop and unpublish the website for ' + selectedAgent + '?')) return;
  var btn = this;
  btn.disabled = true;
  apiPost('./api/unpublish/' + encodeURIComponent(selectedAgent), {}).then(function(data) {
    addBubble('agent', data.message || (data.success ? 'Unpublished ' + selectedAgent + '.' : (data.error || 'Failed.')));
    showPreviewPlaceholder('Website unpublished', 'Click Publish anytime to bring it back online.');
    loadAgents(false);
  }).catch(function() {
    addBubble('error', 'Network error during unpublish.');
  }).finally(function() {
    updateSelectedMeta();
  });
});

// ── Builder Chat ────────────────────────────────────────────────────────────
function addBubble(role, text) {
  var msgs = $('chat-messages');
  var welcome = msgs.querySelector('.chat-welcome');
  if (welcome) welcome.remove();

  var wrap = document.createElement('div');
  wrap.className = 'chat-bubble ' + (role === 'user' ? 'bubble-user' : (role === 'error' ? 'bubble-error' : 'bubble-agent'));

  var label = document.createElement('div');
  label.className = 'bubble-label';
  label.textContent = role === 'user' ? 'You' : (role === 'error' ? 'Error' : 'Website Studio');
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
  var thinking = addBubble('agent', 'Working on your website…');
  thinking.wrap.classList.add('bubble-thinking');

  apiPost('./api/chat', {
    message: text,
    user_id: 'website-user',
    session_id: ensureSessionId(),
    agent_name: 'autoyou',
  }).then(function(data) {
    thinking.wrap.remove();
    var reply = (typeof data === 'string') ? data
      : (data.response || data.text || data.message || (data.error ? 'Error: ' + data.error : JSON.stringify(data, null, 2)));
    addBubble('agent', reply);
    setTimeout(function() {
      loadAgents(false);
      if (selectedAgent) reloadCurrentIframe();
    }, 1000);
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
  this.style.height = Math.min(this.scrollHeight, 130) + 'px';
});

// ── Boot ────────────────────────────────────────────────────────────────────
_initTheme();
hydrateTopbarLinks();
initWorkspaceControls();
initAuth();
