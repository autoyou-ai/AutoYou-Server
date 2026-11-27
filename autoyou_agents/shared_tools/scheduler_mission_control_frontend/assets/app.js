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
    taskMode: 'recurring',
    activeView: 'items',
    statusFilter: 'all',
    searchQuery: '',
};

var REFRESH_INTERVAL_MS = 15000;

var SESSION_TOKEN_KEY = (function () {
    var kind = String(((window.__MISSION_CONTROL_BOOTSTRAP__ || {}).agent_kind) || 'mission').toLowerCase();
    return 'autoyou_mission_token_' + kind;
})();

function proxyRootPrefix() {
    var pathname = String((window.location && window.location.pathname) || '');
    var idx = pathname.indexOf('/agent/');
    return idx < 0 ? '' : pathname.slice(0, idx);
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
    if (!raw || /^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.startsWith('//') || !raw.startsWith('/')) return raw;
    var root = proxyRootPrefix();
    if (!root || raw === root || raw.startsWith(root + '/')) return raw;
    return root + raw;
}

function resolveAgentRoute(agent) {
    return resolveProxyPath('/agent/' + encodeURIComponent(String(agent || '').trim()) + '/');
}

function hydrateTopbarLinks() {
    document.querySelectorAll('[data-agent-link]').forEach(function (el) {
        var target = String(el.getAttribute('data-agent-link') || '').trim();
        if (target) el.href = resolveAgentRoute(target);
    });
}

function _storedToken() { try { return localStorage.getItem(SESSION_TOKEN_KEY) || ''; } catch (_) { return ''; } }
function _saveToken(t) { try { if (t) localStorage.setItem(SESSION_TOKEN_KEY, t); else localStorage.removeItem(SESSION_TOKEN_KEY); } catch (_) {} }

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

function _getTheme() { try { return localStorage.getItem('autoyou.ui.theme') || 'dark'; } catch (_) { return 'dark'; } }
function _setTheme(t) {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem('autoyou.ui.theme', t); } catch (_) {}
}
function _initTheme() {
    _setTheme(_getTheme());
    var btn = document.getElementById('theme-toggle');
    if (btn) btn.addEventListener('click', function () { _setTheme(_getTheme() === 'dark' ? 'light' : 'dark'); });
}

function esc(v) {
    return String(v || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function readJson(r) { return r.json().catch(function () { return {}; }); }

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

// ── Mobile Composer Sheet ───────────────────────────────────────────────────
function openComposerSheet() {
    var drawer = document.getElementById('composer-drawer');
    var backdrop = document.getElementById('composer-backdrop');
    if (drawer) drawer.classList.add('open');
    if (backdrop && window.innerWidth <= 940) backdrop.classList.remove('hidden');
}

function closeComposerSheet() {
    var drawer = document.getElementById('composer-drawer');
    var backdrop = document.getElementById('composer-backdrop');
    if (drawer) drawer.classList.remove('open');
    if (backdrop) backdrop.classList.add('hidden');
}

function switchView(viewName) {
    state.activeView = viewName;
    document.querySelectorAll('.view-tab').forEach(function (tab) {
        var active = tab.getAttribute('data-view') === viewName;
        tab.classList.toggle('active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    document.querySelectorAll('.view-pane').forEach(function (pane) {
        pane.classList.toggle('active', pane.id === 'view-' + viewName);
    });
    var controls = document.getElementById('items-controls');
    if (controls) controls.classList.toggle('hidden', viewName !== 'items');
}

// ── Auth & Login Gate ───────────────────────────────────────────────────────
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
        setAuthChip('Ready', 'ok');
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

    function attempt() {
        var code = (input.value || '').trim();
        if (code.length < 6) return;
        btn.disabled = true;
        if (errEl) { errEl.classList.add('hidden'); errEl.textContent = ''; }
        fetch('./api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ totp_code: code }),
        }).then(readJson).then(function (data) {
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
                input.focus();
            }
        }).catch(function () {
            if (errEl) { errEl.textContent = 'Network error. Please try again.'; errEl.classList.remove('hidden'); }
            btn.disabled = false;
        });
    }

    btn.addEventListener('click', attempt);
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') attempt(); });
}

// ── Header & Metadata ───────────────────────────────────────────────────────
function renderHeader() {
    var meta = appMeta();
    var boardKind = isTasksBoard() ? 'tasks' : 'notify';
    document.documentElement.dataset.board = boardKind;

    var iconEl = document.getElementById('topbar-icon');
    var titleEl = document.getElementById('topbar-title');
    var peerLink = document.getElementById('peer-link');
    var eyebrow = document.getElementById('board-eyebrow');
    var subtitle = document.getElementById('board-subtitle');
    var ctaLabel = document.getElementById('new-item-cta-label');
    var tabLabel = document.getElementById('items-tab-label');

    if (iconEl) iconEl.textContent = meta.topbar_icon || (isTasksBoard() ? '⏱' : '🔔');
    if (titleEl) titleEl.textContent = meta.title || (isTasksBoard() ? 'Tasks' : 'Notify');
    if (eyebrow) eyebrow.textContent = meta.eyebrow || (isTasksBoard() ? 'AI Workflows' : 'Timed Notifications');
    if (subtitle) subtitle.textContent = meta.subtitle || '';
    if (ctaLabel) ctaLabel.textContent = meta.composer_title || (isTasksBoard() ? 'New Task' : 'New Reminder');
    if (tabLabel) tabLabel.textContent = isTasksBoard() ? 'Tasks' : 'Reminders';
    if (peerLink) {
        peerLink.textContent = (meta.peer_label || (isTasksBoard() ? 'Notify' : 'Tasks')) + ' →';
        peerLink.href = resolveAgentRoute(meta.peer_agent || (isTasksBoard() ? 'reminders_agent' : 'tasks_agent'));
    }
}

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

// ── Compact KPI Strip ───────────────────────────────────────────────────────
function renderStats() {
    var grid = document.getElementById('stats-grid');
    if (!grid) return;
    var stats = dashData().stats || [];
    if (!stats.length) {
        grid.innerHTML = '';
        return;
    }
    grid.innerHTML = stats.map(function (s) {
        return '<div class="stat-pill" title="' + esc(s.detail || '') + '">'
            + '<span class="stat-label">' + esc(s.label || '') + '</span>'
            + '<span class="stat-value">' + esc(s.value || '0') + '</span>'
            + '</div>';
    }).join('');
}

// ── Targets & Composer ──────────────────────────────────────────────────────
function currentTargets() { return (dashData().targets && dashData().targets.options) || []; }
function findTargetById(id) { return currentTargets().find(function (t) { return String(t.id) === String(id); }) || null; }

function selectedTargetPayload() {
    var sel = document.getElementById('target-select');
    if (!sel) return null;
    if (sel.value === '__preserved__') return state.preservedTarget;
    return findTargetById(sel.value);
}

function targetOptionsHtml(selectedId) {
    var targets = currentTargets();
    var effectiveSelected = selectedId || (targets.length === 1 ? String(targets[0].id) : '');
    var opts = targets.map(function (t) {
        var detail = t.detail ? ' · ' + t.detail : '';
        var sel = String(t.id) === String(effectiveSelected) ? ' selected' : '';
        return '<option value="' + esc(t.id) + '"' + sel + '>' + esc((t.label || t.transport) + detail) + '</option>';
    }).join('');
    if (state.preservedTarget && !findTargetById(selectedId)) {
        return '<option value="__preserved__" selected>' + esc((state.preservedTarget.label || 'Saved target') + ' · unavailable') + '</option>' + opts;
    }
    return '<option value="">Choose a delivery target…</option>' + opts;
}

function renderComposer() {
    var meta = appMeta();
    var composerTitle = document.getElementById('composer-title');
    var itemsTitle = document.getElementById('items-title');
    var deleteAllBtn = document.getElementById('delete-all-button');
    var roleTitle = document.getElementById('role-title');
    var roleCopy = document.getElementById('role-copy');
    var rolePoints = document.getElementById('role-points');

    if (composerTitle) {
        composerTitle.textContent = state.editingItemId
            ? (isTasksBoard() ? 'Edit Task' : 'Edit Reminder')
            : (meta.composer_title || (isTasksBoard() ? 'New Task' : 'New Reminder'));
    }
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

// ── Items, Queue & Activity ─────────────────────────────────────────────────
function renderItems() {
    var container = document.getElementById('items-list');
    var badge = document.getElementById('items-count-badge');
    if (!container) return;

    var allItems = dashData().items || [];
    if (badge) badge.textContent = String(allItems.length);

    var q = state.searchQuery.toLowerCase();
    var items = allItems.filter(function (item) {
        if (isTasksBoard()) {
            if (state.statusFilter === 'enabled' && !item.enabled) return false;
            if (state.statusFilter === 'paused' && item.enabled) return false;
        }
        if (q) {
            var hay = String(item.instruction || item.message || '') + ' ' + String(item.delivery_target_label || '');
            if (hay.toLowerCase().indexOf(q) < 0) return false;
        }
        return true;
    });

    if (!items.length) {
        var noun = isTasksBoard() ? 'scheduled tasks' : 'reminders';
        var cta = isTasksBoard() ? 'Create Your First Task' : 'Create Your First Reminder';
        container.innerHTML = '<div class="empty-state">'
            + '<div class="empty-icon">' + (isTasksBoard() ? '⏱' : '🔔') + '</div>'
            + '<h3>No ' + noun + (allItems.length ? ' match this filter' : ' yet') + '</h3>'
            + '<p>' + (isTasksBoard()
                ? 'Schedule recurring or one-time AI jobs that run automatically and message you the result.'
                : 'Set a timed notification and AutoYou will deliver the exact message to your device right on schedule.') + '</p>'
            + (!allItems.length ? '<button class="btn btn-primary btn-sm" type="button" data-open-composer="1">' + cta + '</button>' : '')
            + '</div>';
        return;
    }

    container.innerHTML = items.map(function (item) {
        if (isTasksBoard()) {
            var isRunOnce = item.run_once || item.interval_minutes === 0;
            var nextRun = isRunOnce
                ? (item.enabled ? 'Pending (one-time)' : 'Completed')
                : (item.next_run_at_s ? 'Next ' + formatTs(item.next_run_at_s) : 'Paused');
            var statusPill = item.enabled ? '<span class="pill ok">Active</span>' : '<span class="pill">Paused</span>';
            var typePill = isRunOnce
                ? '<span class="pill accent">One-Time</span>'
                : '<span class="pill accent">Every ' + esc(String(item.interval_minutes || 0)) + 'm</span>';
            var preview = item.last_result_preview
                ? '<p class="card-body">' + esc(item.last_result_preview) + '</p>' : '';
            var lastResultPill = item.last_result_at_s
                ? '<span class="pill">Ran ' + esc(formatTs(item.last_result_at_s)) + '</span>' : '';
            return '<article class="item-card" data-item-id="' + esc(item.id) + '">'
                + '<div class="card-head"><div><h3>' + esc(item.instruction || 'Untitled task') + '</h3></div>'
                + '<span class="pill warn">' + esc(nextRun) + '</span></div>'
                + '<div class="meta-row">' + statusPill + typePill
                + '<span class="pill">' + esc(item.delivery_target_label || 'No target') + '</span>'
                + '<span class="pill">Runs: ' + esc(String(item.run_counter || 0)) + '</span>'
                + lastResultPill + '</div>'
                + preview
                + '<div class="card-actions">'
                + '<button class="btn btn-sm btn-ghost" data-action="edit" data-item-id="' + esc(item.id) + '" type="button">Edit</button>'
                + (!isRunOnce ? '<button class="btn btn-sm btn-ghost" data-action="toggle" data-item-id="' + esc(item.id) + '" type="button">' + (item.enabled ? 'Pause' : 'Resume') + '</button>' : '')
                + '<button class="btn btn-sm btn-danger-soft" data-action="delete" data-item-id="' + esc(item.id) + '" type="button">Delete</button>'
                + '</div></article>';
        }

        return '<article class="item-card" data-item-id="' + esc(item.id) + '">'
            + '<div class="card-head"><div><h3>' + esc(item.message || 'Untitled reminder') + '</h3></div>'
            + '<span class="pill accent">' + esc(formatTs(item.timestamp_s)) + '</span></div>'
            + '<div class="meta-row">'
            + '<span class="pill">' + esc(item.delivery_target_label || 'No target') + '</span>'
            + (item.owner_key ? '<span class="pill">' + esc(item.owner_key) + '</span>' : '')
            + '</div>'
            + '<div class="card-actions">'
            + '<button class="btn btn-sm btn-ghost" data-action="edit" data-item-id="' + esc(item.id) + '" type="button">Edit</button>'
            + '<button class="btn btn-sm btn-danger-soft" data-action="delete" data-item-id="' + esc(item.id) + '" type="button">Delete</button>'
            + '</div></article>';
    }).join('');
}

function renderQueue() {
    var note = document.getElementById('queue-note');
    var list = document.getElementById('queue-list');
    var badge = document.getElementById('queue-count-badge');
    if (!note || !list) return;
    var queue = dashData().queue || {};
    var items = Array.isArray(queue.items) ? queue.items : [];
    if (badge) badge.textContent = String(items.length);
    if (!items.length) {
        note.textContent = '';
        list.innerHTML = '<div class="empty-state"><div class="empty-icon">✓</div><h3>Delivery queue is clear</h3><p>All pending outbound messages have been delivered.</p></div>';
        return;
    }
    note.textContent = (queue.pending_count || items.length) + ' queued · ' + (queue.ready_count || 0) + ' ready.';
    list.innerHTML = items.map(function (item) {
        return '<article class="queue-card">'
            + '<div class="card-head"><h3>' + esc(item.source_label || item.source || 'Scheduler') + '</h3>'
            + '<span class="pill ' + (item.status === 'ready' ? 'ok' : 'warn') + '">' + esc(item.status || 'queued') + '</span></div>'
            + (item.message_preview ? '<p class="card-body">' + esc(item.message_preview) + '</p>' : '')
            + '<div class="meta-row"><span class="pill">Age: ' + esc(formatDur(item.age_seconds || 0)) + '</span></div>'
            + '</article>';
    }).join('');
}

function renderActivity() {
    var list = document.getElementById('activity-list');
    if (!list) return;
    var activity = dashData().activity || [];
    if (!activity.length) {
        list.innerHTML = '<div class="empty-state"><div class="empty-icon">📜</div><h3>No activity recorded yet</h3><p>Execution events and deliveries will appear here.</p></div>';
        return;
    }
    list.innerHTML = activity.map(function (e) {
        return '<article class="activity-card">'
            + '<div class="card-head"><h3>' + esc(e.event_type || 'event') + '</h3>'
            + '<span class="pill">' + esc(formatTs(e.timestamp_s)) + '</span></div>'
            + (e.message ? '<p class="card-body">' + esc(e.message) + '</p>' : '')
            + '<div class="meta-row"><span class="pill">' + esc(e.status || 'unknown') + '</span></div>'
            + '</article>';
    }).join('');
}

// ── Form CRUD ───────────────────────────────────────────────────────────────
function currentItems() { return dashData().items || []; }
function currentItemById(id) { return currentItems().find(function (i) { return String(i.id) === String(id); }) || null; }

function resetForm() {
    state.editingItemId = '';
    state.preservedTarget = null;
    var hiddenId = document.getElementById('editing-item-id');
    if (hiddenId) hiddenId.value = '';
    if (isTasksBoard()) {
        if (document.getElementById('task-instruction')) document.getElementById('task-instruction').value = '';
        if (document.getElementById('task-interval')) document.getElementById('task-interval').value = '60';
        if (document.getElementById('task-enabled')) document.getElementById('task-enabled').checked = true;
        if (document.getElementById('task-run-at')) document.getElementById('task-run-at').value = '';
        setTaskMode('recurring');
    } else {
        if (document.getElementById('reminder-message')) document.getElementById('reminder-message').value = '';
        if (document.getElementById('reminder-time')) document.getElementById('reminder-time').value = '';
    }
    var sel = document.getElementById('target-select');
    if (sel) sel.innerHTML = targetOptionsHtml('');
    renderComposer();
}

function fillFormForEdit(itemId) {
    var item = currentItemById(itemId);
    if (!item) return;
    state.editingItemId = String(item.id);
    if (document.getElementById('editing-item-id')) document.getElementById('editing-item-id').value = state.editingItemId;
    state.preservedTarget = (item.delivery_target && Object.keys(item.delivery_target).length)
        ? { id: '__preserved__', label: item.delivery_target_label || 'Saved target', reply_target: item.delivery_target, owner_key: item.owner_key || '' }
        : null;
    if (isTasksBoard()) {
        var isRunOnce = item.run_once || item.interval_minutes === 0;
        if (document.getElementById('task-instruction')) document.getElementById('task-instruction').value = item.instruction || '';
        if (!isRunOnce) {
            if (document.getElementById('task-interval')) document.getElementById('task-interval').value = String(item.interval_minutes || 60);
            if (document.getElementById('task-enabled')) document.getElementById('task-enabled').checked = Boolean(item.enabled);
            setTaskMode('recurring');
        } else {
            setTaskMode('once');
        }
    } else {
        if (document.getElementById('reminder-message')) document.getElementById('reminder-message').value = item.message || '';
        if (document.getElementById('reminder-time')) document.getElementById('reminder-time').value = toLocalDTInput(item.timestamp_s);
    }
    renderComposer();
    openComposerSheet();
    var focusEl = isTasksBoard() ? document.getElementById('task-instruction') : document.getElementById('reminder-message');
    if (focusEl) focusEl.focus();
}

async function submitItemForm(event) {
    event.preventDefault();
    var target = selectedTargetPayload();
    if (!target || !target.reply_target) {
        setAuthChip('Choose delivery target', 'danger');
        return;
    }

    var payload = { owner_key: target.owner_key || '', delivery_target: target.reply_target };
    if (isTasksBoard()) {
        payload.instruction = (document.getElementById('task-instruction').value || '').trim();
        if (state.taskMode === 'once') {
            payload.run_once = true;
            payload.interval_minutes = 0;
            var rawAt = document.getElementById('task-run-at').value || '';
            payload.run_once_at_iso = rawAt ? new Date(rawAt).toISOString() : null;
        } else {
            payload.interval_minutes = Number(document.getElementById('task-interval').value || '60');
            payload.enabled = document.getElementById('task-enabled').checked;
        }
    } else {
        payload.message = (document.getElementById('reminder-message').value || '').trim();
        var rawTime = document.getElementById('reminder-time').value || '';
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
    closeComposerSheet();
    await loadDashboard();
    setAuthChip(editingId ? 'Updated' : 'Saved', 'ok');
}

async function handleItemActions(event) {
    var openBtn = event.target.closest('[data-open-composer]');
    if (openBtn) { resetForm(); openComposerSheet(); return; }

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
        if (!r.ok || !result.success) { setAuthChip(result.error || 'Update failed', 'danger'); return; }
        await loadDashboard();
        setAuthChip(item.enabled ? 'Paused' : 'Resumed', 'ok');
        return;
    }

    if (action === 'delete') {
        if (!window.confirm('Delete this item?')) return;
        var rDel = await apiFetch('./api/items/' + encodeURIComponent(itemId), { method: 'DELETE' });
        var resDel = await readJson(rDel);
        if (!rDel.ok || !resDel.success) { setAuthChip(resDel.error || 'Delete failed', 'danger'); return; }
        if (state.editingItemId === itemId) resetForm();
        await loadDashboard();
        setAuthChip('Deleted', 'ok');
    }
}

async function deleteAllItems() {
    if (!window.confirm('Delete everything on this board?')) return;
    var r = await apiFetch('./api/items/delete-all', { method: 'POST' });
    var result = await readJson(r);
    if (!r.ok || !result.success) { setAuthChip(result.error || 'Failed', 'danger'); return; }
    resetForm();
    await loadDashboard();
    setAuthChip('Board cleared', 'ok');
}

// ── Dashboard & Fallback Settings ───────────────────────────────────────────
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

function render() {
    renderHeader();
    renderLogin();
    if (!authState().authenticated) return;
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
            if (document.getElementById('fallback-mode-select')) document.getElementById('fallback-mode-select').value = data.fallback_mode || 'default';
            if (document.getElementById('fallback-timeout-input')) document.getElementById('fallback-timeout-input').value = String(Number(data.fallback_max_age_seconds || 43200) / 3600);
        }
    } catch (_) {}
}

async function submitFallbackSettingsForm(event) {
    event.preventDefault();
    var modeSelect = document.getElementById('fallback-mode-select');
    var timeoutInput = document.getElementById('fallback-timeout-input');
    var btn = document.getElementById('save-fallback-btn');
    if (btn) btn.disabled = true;
    try {
        var r = await apiFetch('./api/fallback-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                fallback_mode: modeSelect.value,
                fallback_max_age_seconds: Number(timeoutInput.value || 12) * 3600,
            }),
        });
        var result = await readJson(r);
        setAuthChip(r.ok && result.success ? 'Settings saved' : (result.error || 'Save failed'), r.ok && result.success ? 'ok' : 'danger');
    } catch (_) {
        setAuthChip('Network error', 'danger');
    } finally {
        if (btn) btn.disabled = false;
    }
}

// ── Events & Presets ────────────────────────────────────────────────────────
function attachEvents() {
    document.getElementById('item-form').addEventListener('submit', submitItemForm);
    document.getElementById('clear-form-button').addEventListener('click', resetForm);
    document.getElementById('delete-all-button').addEventListener('click', deleteAllItems);
    document.getElementById('items-list').addEventListener('click', handleItemActions);
    document.getElementById('fallback-settings-form').addEventListener('submit', submitFallbackSettingsForm);

    document.querySelectorAll('.mode-btn').forEach(function (btn) {
        btn.addEventListener('click', function () { setTaskMode(btn.dataset.mode); });
    });

    document.querySelectorAll('.view-tab').forEach(function (tab) {
        tab.addEventListener('click', function () { switchView(tab.getAttribute('data-view') || 'items'); });
    });

    document.querySelectorAll('[data-status-filter]').forEach(function (chip) {
        chip.addEventListener('click', function () {
            state.statusFilter = chip.getAttribute('data-status-filter') || 'all';
            document.querySelectorAll('[data-status-filter]').forEach(function (c) { c.classList.toggle('active', c === chip); });
            renderItems();
        });
    });

    var search = document.getElementById('item-search');
    if (search) search.addEventListener('input', function () { state.searchQuery = this.value || ''; renderItems(); });

    var openBtn = document.getElementById('open-composer-btn');
    if (openBtn) openBtn.addEventListener('click', function () {
        resetForm();
        openComposerSheet();
        var focusEl = isTasksBoard() ? document.getElementById('task-instruction') : document.getElementById('reminder-message');
        if (focusEl) focusEl.focus();
    });

    var closeBtn = document.getElementById('close-composer-btn');
    if (closeBtn) closeBtn.addEventListener('click', closeComposerSheet);
    var backdrop = document.getElementById('composer-backdrop');
    if (backdrop) backdrop.addEventListener('click', closeComposerSheet);

    // Quick task interval presets
    document.querySelectorAll('[data-interval]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var val = btn.getAttribute('data-interval');
            var inp = document.getElementById('task-interval');
            if (inp) inp.value = val;
            document.querySelectorAll('[data-interval]').forEach(function (b) { b.classList.toggle('active', b === btn); });
        });
    });

    // Quick reminder time presets
    document.querySelectorAll('[data-offset-min], [data-preset-time]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var inp = document.getElementById('reminder-time');
            if (!inp) return;
            var d = new Date();
            if (btn.hasAttribute('data-offset-min')) {
                d = new Date(Date.now() + Number(btn.getAttribute('data-offset-min')) * 60000);
            } else if (btn.getAttribute('data-preset-time') === 'tomorrow9') {
                d.setDate(d.getDate() + 1);
                d.setHours(9, 0, 0, 0);
            }
            inp.value = toLocalDTInput(d.getTime() / 1000);
        });
    });
}

async function init() {
    _initTheme();
    hydrateTopbarLinks();
    attachEvents();
    render();
    if (state.refreshTimer) window.clearInterval(state.refreshTimer);
    state.refreshTimer = window.setInterval(function () { loadDashboard().catch(function () {}); }, REFRESH_INTERVAL_MS);
    if (authState().authenticated || _storedToken()) {
        try {
            await loadDashboard();
            await loadFallbackSettings();
        } catch (_) {
            setAuthChip('Load error', 'danger');
        }
    }
    setupOtpLogin();
}

init();
