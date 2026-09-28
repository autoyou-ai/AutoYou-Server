// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

'use strict';

var state = {
    bootstrap: window.__MISSION_CONTROL_BOOTSTRAP__ || {},
    dashboard: null,
    editingItemId: '',
    preservedTarget: null,
    refreshTimer: null,
    taskMode: 'recurring',  // 'recurring' | 'once'
};

var REFRESH_INTERVAL_MS = 15000;

// ── Session token (Bearer auth for WebRTC tunnel) ─────────────────────────
var SESSION_TOKEN_KEY = (function () {
    var kind = String(((window.__MISSION_CONTROL_BOOTSTRAP__ || {}).agent_kind) || 'mission').toLowerCase();
    return 'autoyou_mission_token_' + kind;
})();

function proxyRootPrefix() {
    var pathname = String((window.location && window.location.pathname) || '');
    var marker = '/agent/';
    var idx = pathname.indexOf(marker);
    if (idx < 0) return '';
    return pathname.slice(0, idx);
}

function adminLoginUrl() {
    var pathname = String((window.location && window.location.pathname) || '');
    var routePath = proxyRootPrefix() + '/agent/admin_agent/login';
    if (pathname.indexOf('/agent/') >= 0 && window.location && window.location.origin) {
        return window.location.origin + routePath;
    }
    return routePath;
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
    document.querySelectorAll('[data-agent-link]').forEach(function (el) {
        var targetAgent = String(el.getAttribute('data-agent-link') || '').trim();
        if (!targetAgent) return;
        el.href = resolveAgentRoute(targetAgent);
    });
}

function _storedToken() { try { return localStorage.getItem(SESSION_TOKEN_KEY) || ''; } catch (_) { return ''; } }
function _saveToken(t) { try { if (t) localStorage.setItem(SESSION_TOKEN_KEY, t); else localStorage.removeItem(SESSION_TOKEN_KEY); } catch (_) {} }
function _clearToken() { _saveToken(''); }

function apiFetch(url, options) {
    var token = _storedToken();
    if (!token) return fetch(url, options);
    var merged = Object.assign({}, options);
    var base = merged.headers instanceof Headers
        ? Object.fromEntries(merged.headers.entries())
        : Object.assign({}, merged.headers || {});
    if (!base['Authorization']) base['Authorization'] = 'Bearer ' + token;
    merged.headers = base;
    return fetch(url, merged);
}

// ── Theme ─────────────────────────────────────────────────────────────────
function _getTheme() { try { return localStorage.getItem('autoyou.ui.theme') || 'dark'; } catch (_) { return 'dark'; } }
function _setTheme(t) {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem('autoyou.ui.theme', t); } catch (_) {}
}
function _initTheme() {
    _setTheme(_getTheme());
    var btn = document.getElementById('theme-toggle');
    if (btn) btn.addEventListener('click', function () {
        _setTheme(_getTheme() === 'dark' ? 'light' : 'dark');
    });
}

// ── Helpers ───────────────────────────────────────────────────────────────
function esc(v) {
    return String(v || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function readJson(r) { return r.json().catch(function () { return {}; }); }

function scrollElementIntoComfortView(el) {
    if (!el || !el.scrollIntoView) return;
    var isMobile = window.matchMedia("(max-width: 768px)").matches || /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent);
    try {
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
    window.setTimeout(function () { scrollElementIntoComfortView(el); }, 90);
    window.setTimeout(function () { scrollElementIntoComfortView(el); }, 280);
}

function focusWithComfortScroll(input, scrollTarget) {
    if (!input) return;
    var target = scrollTarget || input;
    keepElementVisibleAfterViewportSettles(target);
    try {
        input.focus({ preventScroll: true });
    } catch (_) {
        input.focus();
    }
    keepElementVisibleAfterViewportSettles(target);
}

function bindKeyboardAwareFocus(input, scrollTarget) {
    if (!input || input.dataset.keyboardAwareFocusBound === '1') return;
    input.dataset.keyboardAwareFocusBound = '1';
    var target = scrollTarget || input;
    var keepVisible = function () { keepElementVisibleAfterViewportSettles(target); };
    input.addEventListener('focus', keepVisible);
    input.addEventListener('click', keepVisible);
    if (window.visualViewport && window.visualViewport.addEventListener) {
        window.visualViewport.addEventListener('resize', function () {
            if (document.activeElement === input) keepVisible();
        });
    }
}

function appMeta() { return (state.dashboard && state.dashboard.meta) || state.bootstrap.meta || {}; }
function authState() { return (state.dashboard && state.dashboard.auth) || state.bootstrap.auth || {}; }
function dashData() { return state.dashboard || state.bootstrap || {}; }
function isTasksBoard() { return String((dashData().agent_kind || '')).toLowerCase() === 'tasks'; }

function formatTs(s) {
    if (!s) return '-';
    var d = new Date(Number(s) * 1000);
    if (isNaN(d.getTime())) return String(s);
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(d);
}

function formatDur(s) {
    var n = Math.max(0, Math.round(Number(s) || 0));
    if (n < 60) return n + 's';
    if (n < 3600) return Math.floor(n / 60) + 'm';
    if (n < 86400) return Math.floor(n / 3600) + 'h';
    return Math.floor(n / 86400) + 'd';
}

function toLocalDTInput(s) {
    if (!s) return '';
    var d = new Date(Number(s) * 1000);
    if (isNaN(d.getTime())) return '';
    var pad = function (n) { return String(n).padStart(2, '0'); };
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

// ── Auth chip & login gate ─────────────────────────────────────────────────
function setAuthChip(text, cls) {
    var chip = document.getElementById('auth-chip');
    if (!chip) return;
    chip.textContent = text;
    chip.className = 'status-chip' + (cls ? ' ' + cls : '');
}

function renderLogin() {
    var auth = authState();
    var gate = document.getElementById('login-gate');
    var layout = document.getElementById('main-layout');
    if (!gate || !layout) return;

    if (auth.authenticated) {
        gate.classList.add('hidden');
        layout.classList.remove('hidden');
        setAuthChip('Authenticated', 'ok');
        return;
    }

    layout.classList.add('hidden');
    gate.classList.remove('hidden');
    setAuthChip('Locked', '');

    var copy = document.getElementById('login-copy');
    var otpRow = document.getElementById('otp-row');
    var adminLink = document.getElementById('admin-login-link');
    if (adminLink) adminLink.href = adminLoginUrl();

    if (auth.auth_mode === 'open') {
        if (copy) copy.textContent = 'This board is configured as open on localhost.';
        if (otpRow) otpRow.style.display = 'none';
    } else if (auth.totp_configured || auth.totp_configured == null) {
        if (copy) copy.textContent = 'Enter your 6-digit authenticator code to unlock this board.';
        if (otpRow) otpRow.style.display = '';
    } else {
        if (copy) copy.textContent = 'Sign in through the main AutoYou admin UI, then return here.';
        if (otpRow) otpRow.style.display = 'none';
    }
}

function setupOtpLogin() {
    var input = document.getElementById('otp-input');
    var btn = document.getElementById('otp-submit-btn');
    var errEl = document.getElementById('otp-error');
    if (!btn || !input) return;
    var otpScrollTarget = (input.closest && input.closest('.otp-row')) || input;
    bindKeyboardAwareFocus(input, otpScrollTarget);

    function attempt() {
        var code = input.value.trim();
        if (!code || code.length < 6) return;
        btn.disabled = true;
        if (errEl) { errEl.classList.add('hidden'); errEl.textContent = ''; }
        fetch('./api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ totp_code: code }),
        }).then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (data) {
              if (data.success) {
                  if (data.token) _saveToken(data.token);
                  state.dashboard = Object.assign({}, state.dashboard || state.bootstrap || {}, {
                      auth: data.auth || { authenticated: true, via: 'mission_session' },
                  });
                  renderLogin();
                  loadDashboard();
                  loadFallbackSettings();
              } else {
                  if (errEl) { errEl.textContent = data.error || 'Authentication failed.'; errEl.classList.remove('hidden'); }
                  btn.disabled = false;
                  input.value = '';
                  focusWithComfortScroll(input, otpScrollTarget);
              }
          }).catch(function () {
              if (errEl) { errEl.textContent = 'Network error. Please try again.'; errEl.classList.remove('hidden'); }
              btn.disabled = false;
          });
    }

    btn.addEventListener('click', attempt);
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') attempt(); });
}

// ── Header / topbar ───────────────────────────────────────────────────────
function renderHeader() {
    var meta = appMeta();
    var iconEl = document.getElementById('topbar-icon');
    var titleEl = document.getElementById('topbar-title');
    var peerLink = document.getElementById('peer-link');

    if (iconEl) iconEl.textContent = meta.topbar_icon || (isTasksBoard() ? '⏱' : '🔔');
    if (titleEl) titleEl.textContent = meta.title || 'Mission Control';
    if (peerLink) {
        peerLink.textContent = meta.peer_label || (isTasksBoard() ? 'Notify' : 'Tasks');
        peerLink.href = resolveAgentRoute(meta.peer_agent || (isTasksBoard() ? 'notify_agent' : 'tasks_agent'));
    }
}

// ── Task mode toggle ──────────────────────────────────────────────────────
function setTaskMode(mode) {
    state.taskMode = mode;
    document.querySelectorAll('.mode-btn').forEach(function (btn) {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
    var intervalField = document.getElementById('task-interval-field');
    var runAtField = document.getElementById('task-run-at-field');
    var enabledField = document.getElementById('task-enabled-field');
    if (intervalField) intervalField.classList.toggle('hidden', mode === 'once');
    if (runAtField) runAtField.classList.toggle('hidden', mode !== 'once');
    if (enabledField) enabledField.classList.toggle('hidden', mode === 'once');
}

// ── Stats ─────────────────────────────────────────────────────────────────
function renderStats() {
    var grid = document.getElementById('stats-grid');
    if (!grid) return;
    var stats = dashData().stats || [];
    if (!stats.length) {
        grid.innerHTML = '<div class="placeholder-text">No metrics yet.</div>';
        return;
    }
    grid.innerHTML = stats.map(function (s) {
        return '<div class="stat-card">'
            + '<span class="stat-label">' + esc(s.label || '') + '</span>'
            + '<span class="stat-value">' + esc(s.value || '0') + '</span>'
            + '<span class="stat-detail">' + esc(s.detail || '') + '</span>'
            + '</div>';
    }).join('');
}

// ── Composer ──────────────────────────────────────────────────────────────
function currentTargets() { return (dashData().targets && dashData().targets.options) || []; }

function findTargetById(id) { return currentTargets().find(function (t) { return String(t.id) === String(id); }) || null; }

function selectedTargetPayload() {
    var sel = document.getElementById('target-select');
    if (!sel) return null;
    if (sel.value === '__preserved__') return state.preservedTarget;
    return findTargetById(sel.value);
}

function targetOptionsHtml(selectedId) {
    var opts = currentTargets().map(function (t) {
        var detail = t.detail ? ' · ' + t.detail : '';
        var sel = String(t.id) === String(selectedId) ? ' selected' : '';
        return '<option value="' + esc(t.id) + '"' + sel + '>' + esc((t.label || t.transport) + detail) + '</option>';
    }).join('');
    if (state.preservedTarget && !findTargetById(selectedId)) {
        return '<option value="__preserved__" selected>' + esc((state.preservedTarget.label || 'Saved target') + ' · unavailable') + '</option>' + opts;
    }
    return '<option value="">Choose a delivery target</option>' + opts;
}

function renderComposer() {
    var meta = appMeta();
    var composerTitle = document.getElementById('composer-title');
    var itemsTitle = document.getElementById('items-title');
    var deleteAllBtn = document.getElementById('delete-all-button');
    var roleTitle = document.getElementById('role-title');
    var roleCopy = document.getElementById('role-copy');
    var rolePoints = document.getElementById('role-points');

    if (composerTitle) composerTitle.textContent = meta.composer_title || 'New Item';
    if (itemsTitle) itemsTitle.textContent = meta.items_title || 'Scheduled Items';
    if (deleteAllBtn) deleteAllBtn.textContent = meta.delete_all_label || 'Delete All';
    if (roleTitle) roleTitle.textContent = meta.role_title || 'About';
    if (roleCopy) roleCopy.textContent = meta.role_copy || '';
    if (rolePoints) rolePoints.textContent = meta.role_points || '';

    document.querySelectorAll('.task-only').forEach(function (el) { el.classList.toggle('hidden', !isTasksBoard()); });
    document.querySelectorAll('.reminder-only').forEach(function (el) { el.classList.toggle('hidden', isTasksBoard()); });

    var sel = document.getElementById('target-select');
    if (sel) {
        var prevId = sel.value === '__preserved__' ? '__preserved__' : sel.value;
        sel.innerHTML = targetOptionsHtml(prevId || (state.preservedTarget ? '__preserved__' : ''));
    }

    if (isTasksBoard()) setTaskMode(state.taskMode);
}

// ── Items ─────────────────────────────────────────────────────────────────
function renderItems() {
    var container = document.getElementById('items-list');
    if (!container) return;
    var items = dashData().items || [];
    if (!items.length) {
        container.innerHTML = '<div class="empty-copy">Nothing scheduled on this board yet.</div>';
        return;
    }

    container.innerHTML = items.map(function (item) {
        if (isTasksBoard()) {
            var isRunOnce = item.run_once || item.interval_minutes === 0;
            var nextRun = isRunOnce
                ? (item.enabled ? 'Pending (one-time)' : 'Completed')
                : (item.next_run_at_s ? 'Next ' + formatTs(item.next_run_at_s) : 'Paused');
            var statusPill = item.enabled
                ? '<span class="pill ok">Enabled</span>'
                : '<span class="pill">Disabled</span>';
            var typePill = isRunOnce
                ? '<span class="pill warn">One-Time</span>'
                : '<span class="pill">Every ' + esc(String(item.interval_minutes || 0)) + ' min</span>';
            var preview = item.last_result_preview
                ? '<p class="card-body">' + esc(item.last_result_preview) + '</p>' : '';
            var lastResultPill = item.last_result_at_s
                ? '<span class="pill">Last result ' + esc(formatTs(item.last_result_at_s)) + '</span>' : '';
            return '<article class="item-card" data-item-id="' + esc(item.id) + '">'
                + '<div class="card-head"><div><h3>' + esc(item.instruction || 'Untitled task') + '</h3>'
                + '<div class="meta-row">' + statusPill + typePill
                + '<span class="pill">' + esc(item.delivery_target_label || 'No target') + '</span></div></div>'
                + '<span class="pill warn">' + esc(nextRun) + '</span></div>'
                + preview
                + '<div class="meta-row">'
                + '<span class="pill">Runs: ' + esc(String(item.run_counter || 0)) + '</span>'
                + '<span class="pill">Owner: ' + esc(item.owner_key || 'unknown') + '</span>'
                + lastResultPill
                + '</div>'
                + '<div class="card-actions">'
                + '<button class="btn btn-sm btn-ghost" data-action="edit" data-item-id="' + esc(item.id) + '">Edit</button>'
                + (!isRunOnce ? '<button class="btn btn-sm btn-ghost" data-action="toggle" data-item-id="' + esc(item.id) + '">' + (item.enabled ? 'Pause' : 'Resume') + '</button>' : '')
                + '<button class="btn btn-sm btn-danger" data-action="delete" data-item-id="' + esc(item.id) + '">Delete</button>'
                + '</div></article>';
        }

        return '<article class="item-card" data-item-id="' + esc(item.id) + '">'
            + '<div class="card-head"><div><h3>' + esc(item.message || 'Untitled notification') + '</h3>'
            + '<div class="meta-row">'
            + '<span class="pill warn">' + esc(formatTs(item.timestamp_s)) + '</span>'
            + '<span class="pill">' + esc(item.delivery_target_label || 'No target') + '</span>'
            + '</div></div></div>'
            + '<div class="meta-row"><span class="pill">Owner: ' + esc(item.owner_key || 'unknown') + '</span></div>'
            + '<div class="card-actions">'
            + '<button class="btn btn-sm btn-ghost" data-action="edit" data-item-id="' + esc(item.id) + '">Edit</button>'
            + '<button class="btn btn-sm btn-danger" data-action="delete" data-item-id="' + esc(item.id) + '">Delete</button>'
            + '</div></article>';
    }).join('');
}

// ── Queue ─────────────────────────────────────────────────────────────────
function renderQueue() {
    var note = document.getElementById('queue-note');
    var list = document.getElementById('queue-list');
    if (!note || !list) return;
    var queue = dashData().queue || {};
    var items = Array.isArray(queue.items) ? queue.items : [];
    if (!items.length) {
        note.textContent = '';
        list.innerHTML = '<div class="empty-copy">The delivery queue is clear.</div>';
        return;
    }
    note.textContent = (queue.pending_count || items.length) + ' queued, ' + (queue.ready_count || 0) + ' ready.';
    list.innerHTML = items.map(function (item) {
        return '<article class="queue-card">'
            + '<div class="card-head"><div><h3>' + esc(item.source_label || item.source || 'Scheduler') + '</h3>'
            + '<p class="card-body">' + esc(item.message_preview || '') + '</p></div>'
            + '<span class="pill ' + (item.status === 'ready' ? 'ok' : 'warn') + '">' + esc(item.status || 'queued') + '</span></div>'
            + '<div class="meta-row">'
            + '<span class="pill">Owner: ' + esc(item.owner_key || 'unknown') + '</span>'
            + '<span class="pill">Age: ' + esc(formatDur(item.age_seconds || 0)) + '</span>'
            + '</div></article>';
    }).join('');
}

// ── Activity ──────────────────────────────────────────────────────────────
function renderActivity() {
    var list = document.getElementById('activity-list');
    if (!list) return;
    var activity = dashData().activity || [];
    if (!activity.length) {
        list.innerHTML = '<div class="empty-copy">No activity recorded yet.</div>';
        return;
    }
    list.innerHTML = activity.map(function (e) {
        return '<article class="activity-card">'
            + '<div class="card-head"><div><h3>' + esc(e.event_type || 'event') + '</h3>'
            + '<p class="card-body">' + esc(e.message || '') + '</p></div>'
            + '<span class="pill">' + esc(formatTs(e.timestamp_s)) + '</span></div>'
            + '<div class="meta-row"><span class="pill">' + esc(e.status || 'unknown') + '</span>'
            + (e.item_id ? '<span class="pill">ID: ' + esc(e.item_id) + '</span>' : '')
            + '</div></article>';
    }).join('');
}

// ── Form actions ──────────────────────────────────────────────────────────
function currentItems() { return dashData().items || []; }
function currentItemById(id) { return currentItems().find(function (i) { return String(i.id) === String(id); }) || null; }

function resetForm() {
    state.editingItemId = '';
    state.preservedTarget = null;
    var hiddenId = document.getElementById('editing-item-id');
    if (hiddenId) hiddenId.value = '';
    if (isTasksBoard()) {
        var instr = document.getElementById('task-instruction');
        var interval = document.getElementById('task-interval');
        var enabled = document.getElementById('task-enabled');
        var runAt = document.getElementById('task-run-at');
        if (instr) instr.value = '';
        if (interval) interval.value = '60';
        if (enabled) enabled.checked = true;
        if (runAt) runAt.value = '';
        setTaskMode('recurring');
    } else {
        var msg = document.getElementById('reminder-message');
        var time = document.getElementById('reminder-time');
        if (msg) msg.value = '';
        if (time) time.value = '';
    }
    var sel = document.getElementById('target-select');
    if (sel) { sel.value = ''; sel.innerHTML = targetOptionsHtml(''); }
}

function fillFormForEdit(itemId) {
    var item = currentItemById(itemId);
    if (!item) return;
    state.editingItemId = String(item.id);
    var hiddenId = document.getElementById('editing-item-id');
    if (hiddenId) hiddenId.value = state.editingItemId;
    state.preservedTarget = (item.delivery_target && Object.keys(item.delivery_target).length)
        ? { id: '__preserved__', label: item.delivery_target_label || 'Saved target', reply_target: item.delivery_target, owner_key: item.owner_key || '' }
        : null;
    if (isTasksBoard()) {
        var isRunOnce = item.run_once || item.interval_minutes === 0;
        var instr = document.getElementById('task-instruction');
        var interval = document.getElementById('task-interval');
        var enabled = document.getElementById('task-enabled');
        if (instr) instr.value = item.instruction || '';
        if (!isRunOnce) {
            if (interval) interval.value = String(item.interval_minutes || 60);
            if (enabled) enabled.checked = Boolean(item.enabled);
            setTaskMode('recurring');
        } else {
            setTaskMode('once');
        }
    } else {
        var msg = document.getElementById('reminder-message');
        var time = document.getElementById('reminder-time');
        if (msg) msg.value = item.message || '';
        if (time) time.value = toLocalDTInput(item.timestamp_s);
    }
    renderComposer();
    scrollEditFormIntoView();
}

function scrollEditFormIntoView() {
    var field = isTasksBoard()
        ? document.getElementById('task-instruction-field')
        : document.getElementById('reminder-message-field');
    var input = isTasksBoard()
        ? document.getElementById('task-instruction')
        : document.getElementById('reminder-message');
    var target = field || document.getElementById('item-form') || input;
    window.requestAnimationFrame(function () {
        focusWithComfortScroll(input, target);
    });
}

async function submitItemForm(event) {
    event.preventDefault();
    var target = selectedTargetPayload();
    if (!target || !target.reply_target) {
        setAuthChip('Choose a delivery target', 'danger');
        return;
    }

    var payload = { owner_key: target.owner_key || '', delivery_target: target.reply_target };

    if (isTasksBoard()) {
        var instr = document.getElementById('task-instruction');
        payload.instruction = instr ? instr.value.trim() : '';
        if (state.taskMode === 'once') {
            payload.run_once = true;
            payload.interval_minutes = 0;
            var runAt = document.getElementById('task-run-at');
            var rawAt = runAt ? runAt.value : '';
            payload.run_once_at_iso = rawAt ? new Date(rawAt).toISOString() : null;
        } else {
            var interval = document.getElementById('task-interval');
            var enabled = document.getElementById('task-enabled');
            payload.interval_minutes = Number((interval ? interval.value : '60') || '60');
            payload.enabled = enabled ? enabled.checked : true;
        }
    } else {
        var msg = document.getElementById('reminder-message');
        var time = document.getElementById('reminder-time');
        payload.message = msg ? msg.value.trim() : '';
        var rawTime = time ? time.value : '';
        payload.target_time_iso = rawTime ? new Date(rawTime).toISOString() : '';
    }

    var editingId = state.editingItemId;
    var url = editingId ? ('./api/items/' + encodeURIComponent(editingId)) : './api/items';
    var method = editingId ? 'PATCH' : 'POST';
    var r = await apiFetch(url, { method: method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    var result = await readJson(r);
    if (!r.ok || !result.success) {
        setAuthChip(result.error || 'Failed to save.', 'danger');
        return;
    }
    resetForm();
    await loadDashboard();
    setAuthChip(editingId ? 'Updated.' : 'Created.', 'ok');
}

async function handleItemActions(event) {
    var btn = event.target.closest('[data-action]');
    if (!btn) return;
    var action = btn.getAttribute('data-action');
    var itemId = btn.getAttribute('data-item-id');
    if (!itemId) return;

    if (action === 'edit') { fillFormForEdit(itemId); return; }

    if (action === 'toggle') {
        var item = currentItemById(itemId);
        if (!item) return;
        var r = await apiFetch('./api/items/' + encodeURIComponent(itemId), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: !item.enabled }),
        });
        var result = await readJson(r);
        if (!r.ok || !result.success) { setAuthChip(result.error || 'Update failed.', 'danger'); return; }
        await loadDashboard();
        setAuthChip(item.enabled ? 'Paused.' : 'Resumed.', 'ok');
        return;
    }

    if (action === 'delete') {
        if (!window.confirm('Delete this item?')) return;
        var r = await apiFetch('./api/items/' + encodeURIComponent(itemId), { method: 'DELETE' });
        var result = await readJson(r);
        if (!r.ok || !result.success) { setAuthChip(result.error || 'Delete failed.', 'danger'); return; }
        if (state.editingItemId === itemId) resetForm();
        await loadDashboard();
        setAuthChip('Deleted.', 'ok');
    }
}

async function deleteAllItems() {
    if (!window.confirm('Delete everything on this board?')) return;
    var r = await apiFetch('./api/items/delete-all', { method: 'POST' });
    var result = await readJson(r);
    if (!r.ok || !result.success) { setAuthChip(result.error || 'Failed.', 'danger'); return; }
    resetForm();
    await loadDashboard();
    setAuthChip('Board cleared.', 'ok');
}

// ── Dashboard load ────────────────────────────────────────────────────────
async function loadDashboard() {
    var r = await apiFetch('./api/dashboard');
    var payload = await readJson(r);
    if (r.status === 401) {
        state.dashboard = Object.assign({}, state.dashboard || state.bootstrap || {}, { auth: payload.auth || {} });
        render();
        return false;
    }
    if (!r.ok || !payload.success) throw new Error(payload.error || 'Failed to load dashboard.');
    state.dashboard = payload;
    render();
    return true;
}

// ── Full render ───────────────────────────────────────────────────────────
function render() {
    renderHeader();
    renderLogin();
    var auth = authState();
    if (!auth.authenticated) return;
    renderStats();
    renderComposer();
    renderItems();
    renderQueue();
    renderActivity();
}

async function loadFallbackSettings() {
    try {
        var r = await apiFetch('./api/fallback-settings');
        var data = await readJson(r);
        if (data.success) {
            var modeSelect = document.getElementById('fallback-mode-select');
            var timeoutInput = document.getElementById('fallback-timeout-input');
            if (modeSelect) modeSelect.value = data.fallback_mode || 'default';
            if (timeoutInput) timeoutInput.value = String(Number(data.fallback_max_age_seconds || 43200) / 3600);
        }
    } catch (_) {}
}

async function submitFallbackSettingsForm(event) {
    event.preventDefault();
    var modeSelect = document.getElementById('fallback-mode-select');
    var timeoutInput = document.getElementById('fallback-timeout-input');
    if (!modeSelect || !timeoutInput) return;

    var payload = {
        fallback_mode: modeSelect.value,
        fallback_max_age_seconds: Number(timeoutInput.value || 12) * 3600
    };

    var btn = document.getElementById('save-fallback-btn');
    if (btn) btn.disabled = true;

    try {
        var r = await apiFetch('./api/fallback-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        var result = await readJson(r);
        if (r.ok && result.success) {
            setAuthChip('Fallback settings updated.', 'ok');
        } else {
            setAuthChip(result.error || 'Failed to update settings.', 'danger');
        }
    } catch (_) {
        setAuthChip('Network error updating settings.', 'danger');
    } finally {
        if (btn) btn.disabled = false;
    }
}

// ── Auto-refresh ──────────────────────────────────────────────────────────
function startAutoRefresh() {
    if (state.refreshTimer) window.clearInterval(state.refreshTimer);
    state.refreshTimer = window.setInterval(function () {
        loadDashboard().catch(function () {});
    }, REFRESH_INTERVAL_MS);
}

// ── Events ────────────────────────────────────────────────────────────────
function attachEvents() {
    var form = document.getElementById('item-form');
    if (form) form.addEventListener('submit', submitItemForm);

    var clearBtn = document.getElementById('clear-form-button');
    if (clearBtn) clearBtn.addEventListener('click', resetForm);

    var deleteAll = document.getElementById('delete-all-button');
    if (deleteAll) deleteAll.addEventListener('click', deleteAllItems);

    var itemsList = document.getElementById('items-list');
    if (itemsList) itemsList.addEventListener('click', handleItemActions);

    document.querySelectorAll('.mode-btn').forEach(function (btn) {
        btn.addEventListener('click', function () { setTaskMode(btn.dataset.mode); });
    });

    var fallbackForm = document.getElementById('fallback-settings-form');
    if (fallbackForm) fallbackForm.addEventListener('submit', submitFallbackSettingsForm);
}

// ── Init ──────────────────────────────────────────────────────────────────
async function init() {
    _initTheme();
    hydrateTopbarLinks();
    attachEvents();
    render();
    startAutoRefresh();
    var auth = authState();
    if (auth.authenticated || _storedToken()) {
        try { 
            await loadDashboard(); 
            await loadFallbackSettings();
        } catch (err) {
            setAuthChip('Load error', 'danger');
        }
    }
    setupOtpLogin();
}

init();
