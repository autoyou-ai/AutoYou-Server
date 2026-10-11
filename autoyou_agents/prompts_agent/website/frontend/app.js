// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// My Prompt App - Interactive SPA with Obsidian-style Mind Map, Multi-Device Copy, Favorites & Annotations

(function () {
  'use strict';

  // ─── State Management ───────────────────────────────────────────────────────
  const state = {
    currentView: 'stream',
    softwareFilter: 'all',
    dateFilter: 'all',
    searchQuery: '',
    hasOutputFilter: false,
    favoritesOnly: false,
    sortOrder: 'newest',
    page: 1,
    limit: 25,
    totalPrompts: 0,
    prompts: [],
    projects: [],
    activeSessionId: null,
    isSyncing: false,
    settings: {},
    graphData: null,
  };

  // ─── DOM References ────────────────────────────────────────────────────────
  const dom = {
    // Header & Tabs
    syncStatusText: document.getElementById('sync-status-text'),
    syncBadge: document.getElementById('sync-status-badge'),
    btnSyncNow: document.getElementById('btn-sync-now'),
    btnOpenSettings: document.getElementById('btn-open-settings'),
    tabStream: document.getElementById('tab-stream'),
    tabGraph: document.getElementById('tab-graph'),
    tabProjects: document.getElementById('tab-projects'),
    viewStream: document.getElementById('view-stream'),
    viewGraph: document.getElementById('view-graph'),
    viewProjects: document.getElementById('view-projects'),

    // Search & Filters
    inputSearch: document.getElementById('input-search'),
    btnClearSearch: document.getElementById('btn-clear-search'),
    softwareFilters: document.getElementById('software-filters'),
    selectDate: document.getElementById('select-date-filter'),
    selectSort: document.getElementById('select-sort'),
    checkHasOutput: document.getElementById('check-has-output'),

    // Stream View
    statShowingCount: document.getElementById('stat-showing-count'),
    statsPills: document.getElementById('stats-pills'),
    promptsList: document.getElementById('prompts-list'),
    paginationBar: document.getElementById('stream-pagination'),
    btnPrevPage: document.getElementById('btn-prev-page'),
    btnNextPage: document.getElementById('btn-next-page'),
    pageIndicator: document.getElementById('page-indicator'),

    // Graph View
    canvas: document.getElementById('mindmap-canvas'),
    graphTooltip: document.getElementById('graph-tooltip'),
    graphLegend: document.getElementById('graph-legend'),
    btnZoomIn: document.getElementById('btn-graph-zoom-in'),
    btnZoomOut: document.getElementById('btn-graph-zoom-out'),
    btnResetGraph: document.getElementById('btn-graph-reset'),

    // Projects View
    projectsGrid: document.getElementById('projects-grid'),

    // Chain Drawer
    chainDrawer: document.getElementById('chain-drawer'),
    drawerTitle: document.getElementById('drawer-session-title'),
    drawerMeta: document.getElementById('drawer-session-meta'),
    drawerBadge: document.getElementById('drawer-sw-badge'),
    chainTimeline: document.getElementById('chain-timeline'),
    btnCloseDrawer: document.getElementById('btn-close-drawer'),

    // Settings Modal
    settingsModal: document.getElementById('settings-modal'),
    btnCloseSettings: document.getElementById('btn-close-settings'),
    settingsUnlockedView: document.getElementById('settings-unlocked-view'),
    settingCaptureOutputs: document.getElementById('setting-capture-outputs'),
    settingCaptureAttachments: document.getElementById('setting-capture-attachments'),
    settingSyncInterval: document.getElementById('setting-sync-interval'),
    settingEncryptedStorage: document.getElementById('setting-encrypted-storage'),
    btnSaveSettings: document.getElementById('btn-save-settings'),
    btnForceFullResync: document.getElementById('btn-force-full-resync'),
    btnCreateHook: document.getElementById('btn-create-hook'),
    newHookBox: document.getElementById('new-hook-box'),
    hookTokenVal: document.getElementById('hook-token-val'),
    btnCopyHookToken: document.getElementById('btn-copy-hook-token'),

    // Toasts
    toastContainer: document.getElementById('toast-container'),
  };

  // ─── API Helpers (Relative URLs for Web Proxy Compatibility) ────────────────
  const API_BASE = (function () {
    const loc = window.location.pathname;
    if (loc.includes('/agent/prompts_agent')) {
      return loc.endsWith('/') ? loc.slice(0, -1) : loc;
    }
    return '';
  })();

  async function apiCall(endpoint, options = {}) {
    const url = `${API_BASE}${endpoint}`;
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    };
    try {
      const resp = await fetch(url, { ...options, credentials: 'same-origin', headers });
      const data = await resp.json().catch(() => ({}));
      if (resp.status === 401) {
        window.location.reload();
      }
      if (!resp.ok) {
        throw new Error(data.detail || data.message || `HTTP ${resp.status}`);
      }
      return data;
    } catch (err) {
      console.warn(`API call ${endpoint} error:`, err);
      throw err;
    }
  }

  // ─── Toast System ──────────────────────────────────────────────────────────
  function showToast(message, type = 'success') {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `<span>${type === 'success' ? '✓' : type === 'warning' ? '⚠' : '✕'}</span> <span>${message}</span>`;
    dom.toastContainer.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(10px)';
      el.style.transition = 'all 0.2s ease';
      setTimeout(() => el.remove(), 200);
    }, 2800);
  }

  // ─── Universal Clipboard Copy (Mobile & WebView Safe) ──────────────────────
  async function copyToClipboard(text, label = 'Prompt') {
    if (!text) {
      showToast('Nothing to copy', 'warning');
      return false;
    }

    let copied = false;
    // 1. Try modern async clipboard API
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        copied = true;
      } catch (err) {
        console.warn('Clipboard API failed, trying fallback:', err);
      }
    }

    // 2. Fallback using execCommand for Android/iOS WebViews
    if (!copied) {
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        ta.style.top = '0';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        copied = document.execCommand('copy');
        document.body.removeChild(ta);
      } catch (err) {
        console.error('Fallback copy failed:', err);
      }
    }

    if (copied) {
      showToast(`Copied ${label} to clipboard!`, 'success');
      return true;
    } else {
      showToast('Could not copy to clipboard', 'error');
      return false;
    }
  }

  // ─── Date Formatter ────────────────────────────────────────────────────────
  function formatRelativeDate(isoStr) {
    if (!isoStr) return '';
    try {
      const date = new Date(isoStr);
      const now = new Date();
      const diffSec = Math.floor((now - date) / 1000);
      if (diffSec < 60) return 'just now';
      if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
      if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
      if (diffSec < 604800) return `${Math.floor(diffSec / 86400)}d ago`;
      return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    } catch {
      return isoStr;
    }
  }

  // ─── View Switching ────────────────────────────────────────────────────────
  function switchView(viewName) {
    state.currentView = viewName;
    document.querySelectorAll('.tab-btn, .mobile-tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.view === viewName);
    });
    dom.viewStream.classList.toggle('active', viewName === 'stream');
    dom.viewGraph.classList.toggle('active', viewName === 'graph');
    dom.viewProjects.classList.toggle('active', viewName === 'projects');

    if (viewName === 'graph') {
      loadGraphData();
    } else if (viewName === 'projects') {
      loadProjects();
    }
  }

  // ─── Load & Render Prompts Stream ──────────────────────────────────────────
  async function loadPrompts() {
    dom.statShowingCount.textContent = 'Loading prompts...';
    try {
      const params = new URLSearchParams({
        limit: state.limit,
        offset: (state.page - 1) * state.limit,
        sort: state.sortOrder,
      });

      if (state.softwareFilter !== 'all') params.set('software', state.softwareFilter);
      if (state.searchQuery.trim()) params.set('q', state.searchQuery.trim());
      if (state.hasOutputFilter) params.set('has_output', 'true');
      if (state.favoritesOnly) params.set('is_favorite', 'true');

      // Date filtering
      if (state.dateFilter === 'today') {
        const d = new Date();
        d.setHours(0, 0, 0, 0);
        params.set('date_from', d.toISOString());
      } else if (state.dateFilter === 'week') {
        const d = new Date(Date.now() - 7 * 86400 * 1000);
        params.set('date_from', d.toISOString());
      } else if (state.dateFilter === 'month') {
        const d = new Date(Date.now() - 30 * 86400 * 1000);
        params.set('date_from', d.toISOString());
      }

      const res = await apiCall(`/api/prompts?${params.toString()}`);
      state.prompts = res.prompts || [];
      state.totalPrompts = res.total || 0;
      renderPromptsList();
      updatePagination();
    } catch (err) {
      dom.statShowingCount.textContent = 'Failed to load prompts';
      showToast(err.message, 'error');
    }
  }

  function renderPromptsList() {
    dom.promptsList.innerHTML = '';
    const totalPages = Math.ceil(state.totalPrompts / state.limit) || 1;
    dom.statShowingCount.textContent = `Showing ${state.prompts.length} of ${state.totalPrompts} prompts (Page ${state.page} of ${totalPages})`;

    if (state.prompts.length === 0) {
      dom.promptsList.innerHTML = `
        <div class="empty-state" style="text-align: center; padding: 3rem 1rem; color: var(--text-muted);">
          <div style="font-size: 2.5rem; margin-bottom: 0.5rem;">✨</div>
          <h3>No prompts found</h3>
          <p style="font-size: 0.85rem; margin-top: 0.25rem;">Try adjusting your search keywords, software filter, or click <strong>Sync Now</strong>.</p>
        </div>
      `;
      return;
    }

    state.prompts.forEach(p => {
      const card = document.createElement('div');
      card.className = 'prompt-card';
      card.dataset.promptId = p.id;

      const sw = p.software || 'custom_hook';
      const isFav = Boolean(p.is_favorite);
      const hasOutput = Boolean(p.output_text);
      const note = p.annotation || '';

      card.innerHTML = `
        <div class="prompt-card-header">
          <div class="card-meta-left">
            <span class="software-tag ${sw}">${sw}</span>
            <span class="project-tag" title="Click to filter by project">${escapeHtml(p.project_name || 'General')}</span>
            <span class="time-tag" title="${p.timestamp}">${formatRelativeDate(p.timestamp)}</span>
          </div>
          <div class="card-meta-right">
            <button class="btn-star ${isFav ? 'starred' : ''}" title="${isFav ? 'Remove Favorite' : 'Star Favorite'}" style="background: none; border: none; cursor: pointer; font-size: 1.1rem; color: ${isFav ? '#f59e0b' : 'var(--text-dim)'};">
              ${isFav ? '★' : '☆'}
            </button>
            <span class="turn-tag">Turn ${p.turn_index + 1}</span>
          </div>
        </div>

        <div class="prompt-body">
          <div class="prompt-text-container">${escapeHtml(p.prompt_text || '')}</div>
        </div>

        ${hasOutput ? `
          <div class="output-accordion">
            <div class="output-header">
              <span class="output-badge">🤖 Final Task Output / Response</span>
              <button class="btn-copy btn-copy-output" data-text="${escapeAttr(p.output_text)}" style="padding: 0.2rem 0.5rem; font-size: 0.72rem;">📋 Copy Output</button>
            </div>
            <div class="output-content">${escapeHtml(p.output_text || '')}</div>
          </div>
        ` : ''}

        ${note ? `
          <div class="annotation-box" style="margin-top: 0.6rem; padding: 0.45rem 0.75rem; background: rgba(99, 102, 241, 0.08); border-left: 3px solid var(--primary); border-radius: 4px; font-size: 0.8rem; color: #cbd5e1;">
            <strong>📝 Note:</strong> ${escapeHtml(note)}
          </div>
        ` : ''}

        <div class="card-actions">
          <div class="action-left">
            <button class="btn-copy btn-copy-prompt" data-text="${escapeAttr(p.prompt_text)}">
              <span>📋</span> Copy Prompt
            </button>
            <button class="btn btn-xs btn-outline btn-annotate" title="Add personal note to this prompt">
              <span>📝</span> ${note ? 'Edit Note' : 'Add Note'}
            </button>
          </div>
          <div class="action-right">
            <button class="btn btn-xs btn-secondary btn-view-chain" data-session="${p.session_id}">
              <span>🔗</span> Linked Chain
            </button>
          </div>
        </div>
      `;

      // Event Listeners for this card
      card.querySelector('.btn-copy-prompt').addEventListener('click', (e) => {
        const btn = e.currentTarget;
        copyToClipboard(btn.dataset.text, 'prompt');
        btn.classList.add('copied');
        setTimeout(() => btn.classList.remove('copied'), 1500);
      });

      if (hasOutput) {
        card.querySelector('.btn-copy-output').addEventListener('click', (e) => {
          e.stopPropagation();
          copyToClipboard(e.currentTarget.dataset.text, 'output response');
        });
      }

      // Star / Favorite Toggle
      card.querySelector('.btn-star').addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        try {
          const res = await apiCall(`/api/prompts/${p.id}/favorite`, { method: 'POST' });
          p.is_favorite = res.is_favorite;
          btn.textContent = res.is_favorite ? '★' : '☆';
          btn.style.color = res.is_favorite ? '#f59e0b' : 'var(--text-dim)';
          showToast(res.is_favorite ? 'Added to favorites ⭐' : 'Removed from favorites', 'success');
        } catch (err) {
          showToast(err.message, 'error');
        }
      });

      // Annotate Note
      card.querySelector('.btn-annotate').addEventListener('click', () => {
        promptForAnnotation(p);
      });

      // Project filter click
      card.querySelector('.project-tag').addEventListener('click', () => {
        dom.inputSearch.value = p.project_name;
        state.searchQuery = p.project_name;
        state.page = 1;
        loadPrompts();
      });

      // View Chain Drawer
      card.querySelector('.btn-view-chain').addEventListener('click', () => {
        openChainDrawer(p.session_id);
      });

      dom.promptsList.appendChild(card);
    });
  }

  function updatePagination() {
    const totalPages = Math.ceil(state.totalPrompts / state.limit) || 1;
    dom.paginationBar.style.display = totalPages > 1 ? 'flex' : 'none';
    dom.pageIndicator.textContent = `Page ${state.page} of ${totalPages}`;
    dom.btnPrevPage.disabled = state.page <= 1;
    dom.btnNextPage.disabled = state.page >= totalPages;
  }

  // ─── Annotation Modal Prompt ───────────────────────────────────────────────
  async function promptForAnnotation(promptItem) {
    const existing = promptItem.annotation || '';
    const note = window.prompt(`Add or edit notes for this prompt turn:`, existing);
    if (note === null) return; // User cancelled

    try {
      await apiCall(`/api/prompts/${promptItem.id}/annotate`, {
        method: 'POST',
        body: JSON.stringify({ annotation: note.trim() }),
      });
      promptItem.annotation = note.trim();
      renderPromptsList();
      showToast('Annotation saved! 📝', 'success');
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // ─── Linked Chain Drawer ───────────────────────────────────────────────────
  async function openChainDrawer(sessionId) {
    state.activeSessionId = sessionId;
    dom.chainDrawer.style.display = 'flex';
    dom.chainTimeline.innerHTML = '<div style="padding: 2rem; color: var(--text-muted); text-align: center;">Loading conversation turns...</div>';

    try {
      const res = await apiCall(`/api/sessions/${sessionId}`);
      const s = res.session || {};
      dom.drawerTitle.textContent = s.title || 'Session Details';
      dom.drawerMeta.textContent = `Project: ${s.project_name || 'General'} | Model: ${s.model || 'Unknown'}`;
      dom.drawerBadge.textContent = (s.software || 'Codex').toUpperCase();
      dom.drawerBadge.className = `drawer-badge software-tag ${s.software || 'codex'}`;

      renderChainTimeline(s.turns || []);
    } catch (err) {
      dom.chainTimeline.innerHTML = `<div class="error-banner">Failed to load session: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderChainTimeline(turns) {
    dom.chainTimeline.innerHTML = '';
    if (!turns || turns.length === 0) {
      dom.chainTimeline.innerHTML = '<div style="padding: 2rem; color: var(--text-muted); text-align: center;">No turns found in this session.</div>';
      return;
    }

    turns.forEach((t, i) => {
      const item = document.createElement('div');
      item.className = `turn-item ${t.role}`;
      const hasOutput = Boolean(t.output_text);

      item.innerHTML = `
        <div class="turn-item-node">${i + 1}</div>
        <div class="turn-box">
          <div class="turn-box-header">
            <span><strong>${t.role.toUpperCase()}</strong> • ${formatRelativeDate(t.timestamp)}</span>
            <div style="display: flex; gap: 0.35rem;">
              <button class="btn btn-xs btn-outline btn-copy-turn" data-text="${escapeAttr(t.prompt_text)}">📋 Copy</button>
            </div>
          </div>
          <div class="turn-box-text">${escapeHtml(t.prompt_text || '')}</div>

          ${hasOutput ? `
            <div class="output-accordion" style="margin-top: 0.6rem;">
              <div class="output-header">
                <span class="output-badge">🤖 Assistant Output</span>
                <button class="btn-copy btn-copy-turn-output" data-text="${escapeAttr(t.output_text)}" style="padding: 0.15rem 0.45rem; font-size: 0.7rem;">📋 Copy</button>
              </div>
              <div class="output-content">${escapeHtml(t.output_text || '')}</div>
            </div>
          ` : ''}
        </div>
      `;

      item.querySelector('.btn-copy-turn').addEventListener('click', (e) => {
        copyToClipboard(e.currentTarget.dataset.text, 'prompt');
      });

      if (hasOutput) {
        item.querySelector('.btn-copy-turn-output').addEventListener('click', (e) => {
          copyToClipboard(e.currentTarget.dataset.text, 'output response');
        });
      }

      dom.chainTimeline.appendChild(item);
    });
  }

  function closeChainDrawer() {
    dom.chainDrawer.style.display = 'none';
    state.activeSessionId = null;
  }

  // ─── Projects & Worktrees View ─────────────────────────────────────────────
  async function loadProjects() {
    dom.projectsGrid.innerHTML = '<div style="padding: 2rem; color: var(--text-muted); text-align: center;">Loading projects...</div>';
    try {
      const res = await apiCall('/api/projects');
      state.projects = res.projects || [];
      renderProjectsGrid();
    } catch (err) {
      dom.projectsGrid.innerHTML = `<div class="error-banner">Failed to load projects: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderProjectsGrid() {
    dom.projectsGrid.innerHTML = '';
    if (state.projects.length === 0) {
      dom.projectsGrid.innerHTML = '<div style="padding: 2rem; color: var(--text-muted); text-align: center;">No projects detected yet.</div>';
      return;
    }

    state.projects.forEach(p => {
      const card = document.createElement('div');
      card.className = 'project-card';
      const sw = p.software || 'codex';

      card.innerHTML = `
        <div class="project-card-top">
          <span class="software-tag ${sw}">${sw}</span>
          <span class="time-tag">${formatRelativeDate(p.last_activity)}</span>
        </div>
        <div class="project-card-title">${escapeHtml(p.project_name || 'General')}</div>
        <div class="project-card-counts">
          <span>📝 ${p.prompt_count} prompts</span>
          <span>💬 ${p.session_count} sessions</span>
        </div>
        <div class="project-card-footer">
          Click to filter prompts for this project →
        </div>
      `;

      card.addEventListener('click', () => {
        dom.inputSearch.value = p.project_name;
        state.searchQuery = p.project_name;
        state.softwareFilter = 'all';
        document.querySelectorAll('.software-filters .pill-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.software === 'all'));
        state.page = 1;
        switchView('stream');
        loadPrompts();
      });

      dom.projectsGrid.appendChild(card);
    });
  }

  // ─── Mind Map / Obsidian Visual Graph (HTML5 Canvas Force Graph) ───────────
  let graphNodes = [];
  let graphLinks = [];
  let graphZoom = 1.0;
  let graphPan = { x: 0, y: 0 };
  let draggedNode = null;
  let hoveredNode = null;
  let animFrameId = null;

  async function loadGraphData() {
    try {
      const res = await apiCall(`/api/graph?software=${state.softwareFilter}`);
      const g = res.graph || { nodes: [], links: [] };
      state.graphData = g;
      initGraphSimulation(g);
    } catch (err) {
      console.warn('Failed to load graph data:', err);
    }
  }

  function initGraphSimulation(data) {
    const canvas = dom.canvas;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * window.devicePixelRatio;
    canvas.height = rect.height * window.devicePixelRatio;
    const ctx = canvas.getContext('2d');
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);

    const width = rect.width;
    const height = rect.height;

    graphZoom = 1.0;
    graphPan = { x: width / 2, y: height / 2 };

    // Initialize node positions radially around center
    const nodeMap = new Map();
    graphNodes = (data.nodes || []).map((n, idx) => {
      const angle = (idx / (data.nodes.length || 1)) * 2 * Math.PI;
      const radius = n.group === 'root' ? 0 : n.group === 'software' ? 120 : n.group === 'project' ? 240 : 360;
      const node = {
        ...n,
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        radius: n.val || 8,
      };
      nodeMap.set(n.id, node);
      return node;
    });

    graphLinks = (data.links || []).map(l => ({
      source: nodeMap.get(l.source),
      target: nodeMap.get(l.target),
      type: l.type,
    })).filter(l => l.source && l.target);

    // Render legend
    dom.graphLegend.innerHTML = `
      <div class="legend-item"><span class="legend-dot" style="background: #6366f1;"></span> Hub</div>
      <div class="legend-item"><span class="legend-dot" style="background: #10a37f;"></span> Codex</div>
      <div class="legend-item"><span class="legend-dot" style="background: #d97706;"></span> Claude</div>
      <div class="legend-item"><span class="legend-dot" style="background: #8b5cf6;"></span> Cursor</div>
      <div class="legend-item"><span class="legend-dot" style="background: #3b82f6;"></span> Antigravity</div>
    `;

    if (animFrameId) cancelAnimationFrame(animFrameId);
    animateGraph();
  }

  function animateGraph() {
    // Basic force-directed relaxation step
    const repulsion = 800;
    const springLen = 70;
    const springK = 0.04;
    const damping = 0.85;

    // Node-node repulsion
    for (let i = 0; i < graphNodes.length; i++) {
      for (let j = i + 1; j < graphNodes.length; j++) {
        const a = graphNodes[i];
        const b = graphNodes[j];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const distSq = dx * dx + dy * dy + 10;
        const dist = Math.sqrt(distSq);
        const force = repulsion / distSq;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;

        if (a !== draggedNode) { a.vx -= fx; a.vy -= fy; }
        if (b !== draggedNode) { b.vx += fx; b.vy += fy; }
      }
    }

    // Link spring tension
    for (const link of graphLinks) {
      const a = link.source;
      const b = link.target;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = (dist - springLen) * springK;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;

      if (a !== draggedNode) { a.vx += fx; a.vy += fy; }
      if (b !== draggedNode) { b.vx -= fx; b.vy -= fy; }
    }

    // Center gravity
    for (const n of graphNodes) {
      if (n !== draggedNode) {
        n.vx -= n.x * 0.005;
        n.vy -= n.y * 0.005;
        n.vx *= damping;
        n.vy *= damping;
        n.x += n.vx;
        n.y += n.vy;
      }
    }

    drawGraph();
    animFrameId = requestAnimationFrame(animateGraph);
  }

  function drawGraph() {
    const canvas = dom.canvas;
    const ctx = canvas.getContext('2d');
    const width = canvas.width / window.devicePixelRatio;
    const height = canvas.height / window.devicePixelRatio;

    ctx.clearRect(0, 0, width, height);
    ctx.save();
    ctx.translate(graphPan.x, graphPan.y);
    ctx.scale(graphZoom, graphZoom);

    // Draw Links
    ctx.lineWidth = 1.2;
    for (const l of graphLinks) {
      ctx.strokeStyle = 'rgba(75, 85, 99, 0.35)';
      ctx.beginPath();
      ctx.moveTo(l.source.x, l.source.y);
      ctx.lineTo(l.target.x, l.target.y);
      ctx.stroke();
    }

    // Draw Nodes
    for (const n of graphNodes) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.radius, 0, 2 * Math.PI);
      ctx.fillStyle = n.color || '#6366f1';
      ctx.shadowColor = n.color || '#6366f1';
      ctx.shadowBlur = (n === hoveredNode || n === draggedNode) ? 14 : 4;
      ctx.fill();
      ctx.shadowBlur = 0;

      // Label
      if (graphZoom >= 0.7 || n.group === 'root' || n.group === 'software' || n === hoveredNode) {
        ctx.fillStyle = '#f1f5f9';
        ctx.font = `${n.group === 'root' ? 12 : 10}px sans-serif`;
        ctx.textAlign = 'center';
        ctx.fillText(n.label, n.x, n.y + n.radius + 12);
      }
    }

    ctx.restore();
  }

  // Canvas Mouse & Touch Interactivity
  let isPanning = false;
  let startPan = { x: 0, y: 0 };

  function getCanvasCoords(evt) {
    const rect = dom.canvas.getBoundingClientRect();
    const clientX = evt.touches ? evt.touches[0].clientX : evt.clientX;
    const clientY = evt.touches ? evt.touches[0].clientY : evt.clientY;
    return {
      x: (clientX - rect.left - graphPan.x) / graphZoom,
      y: (clientY - rect.top - graphPan.y) / graphZoom,
      rawX: clientX - rect.left,
      rawY: clientY - rect.top,
    };
  }

  function findNodeUnder(x, y) {
    for (let i = graphNodes.length - 1; i >= 0; i--) {
      const n = graphNodes[i];
      const dist = Math.hypot(n.x - x, n.y - y);
      if (dist <= n.radius + 6) return n;
    }
    return null;
  }

  dom.canvas.addEventListener('mousedown', (e) => {
    const coords = getCanvasCoords(e);
    const node = findNodeUnder(coords.x, coords.y);
    if (node) {
      draggedNode = node;
    } else {
      isPanning = true;
      startPan = { x: e.clientX - graphPan.x, y: e.clientY - graphPan.y };
    }
  });

  window.addEventListener('mousemove', (e) => {
    if (draggedNode) {
      const coords = getCanvasCoords(e);
      draggedNode.x = coords.x;
      draggedNode.y = coords.y;
    } else if (isPanning) {
      graphPan.x = e.clientX - startPan.x;
      graphPan.y = e.clientY - startPan.y;
    } else if (dom.viewGraph.classList.contains('active')) {
      const coords = getCanvasCoords(e);
      const node = findNodeUnder(coords.x, coords.y);
      hoveredNode = node;
      if (node) {
        dom.graphTooltip.style.display = 'block';
        dom.graphTooltip.style.left = `${coords.rawX + 15}px`;
        dom.graphTooltip.style.top = `${coords.rawY + 15}px`;
        dom.graphTooltip.innerHTML = `<strong>${escapeHtml(node.label)}</strong><div style="font-size: 0.72rem; color: var(--text-dim); margin-top: 2px;">Type: ${node.group}</div>`;
      } else {
        dom.graphTooltip.style.display = 'none';
      }
    }
  });

  window.addEventListener('mouseup', () => {
    if (draggedNode && !isPanning) {
      // If clicked on session node, open chain drawer
      if (draggedNode.group === 'session' && draggedNode.meta?.raw_id) {
        openChainDrawer(draggedNode.meta.raw_id);
      } else if (draggedNode.group === 'software') {
        state.softwareFilter = draggedNode.id.replace('sw_', '');
        document.querySelectorAll('.software-filters .pill-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.software === state.softwareFilter));
        switchView('stream');
        loadPrompts();
      }
    }
    draggedNode = null;
    isPanning = false;
  });

  // Wheel Zoom
  dom.canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.15 : 0.88;
    graphZoom = Math.max(0.2, Math.min(3.5, graphZoom * factor));
  }, { passive: false });

  dom.btnZoomIn.addEventListener('click', () => { graphZoom = Math.min(3.5, graphZoom * 1.25); });
  dom.btnZoomOut.addEventListener('click', () => { graphZoom = Math.max(0.2, graphZoom * 0.8); });
  dom.btnResetGraph.addEventListener('click', () => {
    const rect = dom.canvas.getBoundingClientRect();
    graphZoom = 1.0;
    graphPan = { x: rect.width / 2, y: rect.height / 2 };
  });

  // ─── Settings Modal & OTP Security ─────────────────────────────────────────
  async function openSettingsModal() {
    dom.settingsModal.style.display = 'flex';
    try {
      const res = await apiCall('/api/settings');
      state.settings = res.settings || {};
      showCollectionSettings();
    } catch (err) {
      dom.settingsModal.style.display = 'none';
      showToast(err.message, 'error');
    }
  }

  function showCollectionSettings() {
    dom.settingsUnlockedView.style.display = 'block';

    dom.settingCaptureOutputs.checked = Boolean(state.settings.capture_outputs);
    dom.settingCaptureAttachments.checked = Boolean(state.settings.capture_attachments);
    dom.settingEncryptedStorage.checked = Boolean(state.settings.encrypted_storage);
    dom.settingSyncInterval.value = state.settings.sync_interval || 'hourly';
  }

  async function saveSettings() {
    try {
      const payload = {
        capture_outputs: dom.settingCaptureOutputs.checked,
        capture_attachments: dom.settingCaptureAttachments.checked,
        sync_interval: dom.settingSyncInterval.value,
      };

      await apiCall('/api/settings', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      showToast('Settings saved successfully! ✓', 'success');
      dom.settingsModal.style.display = 'none';
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  async function generatePushHook() {
    try {
      const res = await apiCall('/api/hooks/generate', {
        method: 'POST',
        body: JSON.stringify({ client_name: `Hook-${Date.now()}` }),
      });
      dom.newHookBox.style.display = 'block';
      dom.hookTokenVal.textContent = res.token;
      showToast('Push token generated! Copy it now.', 'success');
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // ─── Sync Status & Polling ─────────────────────────────────────────────────
  async function checkSyncStatus() {
    try {
      const res = await apiCall('/api/sync/status');
      state.isSyncing = res.is_syncing;
      dom.syncBadge.classList.toggle('syncing', state.isSyncing);

      if (state.isSyncing) {
        dom.syncStatusText.textContent = 'Syncing prompts...';
      } else if (res.last_sync?.finished_at) {
        dom.syncStatusText.textContent = `Synced ${formatRelativeDate(res.last_sync.finished_at)}`;
      } else {
        dom.syncStatusText.textContent = 'Ready';
      }
    } catch {
      dom.syncStatusText.textContent = 'Online';
    }
  }

  async function triggerManualSync(fullResync = false) {
    try {
      showToast(fullResync ? 'Starting full historical resync...' : 'Harvesting prompts in background...', 'success');
      await apiCall(`/api/sync?full_resync=${fullResync}`, { method: 'POST' });
      dom.syncBadge.classList.add('syncing');
      dom.syncStatusText.textContent = 'Syncing...';
      setTimeout(() => {
        loadPrompts();
        checkSyncStatus();
      }, 2500);
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // ─── Utilities ─────────────────────────────────────────────────────────────
  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function escapeAttr(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // ─── Event Listeners Setup ─────────────────────────────────────────────────
  function setupEventListeners() {
    // Tab switching
    document.querySelectorAll('.tab-btn, .mobile-tab-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const view = e.currentTarget.dataset.view;
        if (view) switchView(view);
      });
    });

    // Mobile settings button
    document.getElementById('btn-mobile-settings')?.addEventListener('click', openSettingsModal);

    // Sync button
    dom.btnSyncNow.addEventListener('click', () => triggerManualSync(false));
    dom.btnOpenSettings.addEventListener('click', openSettingsModal);
    dom.btnCloseSettings.addEventListener('click', () => { dom.settingsModal.style.display = 'none'; });

    // Drawer close
    dom.btnCloseDrawer.addEventListener('click', closeChainDrawer);
    dom.chainDrawer.addEventListener('click', (e) => {
      if (e.target === dom.chainDrawer) closeChainDrawer();
    });

    // Search input (with debounce)
    let searchDebounce = null;
    dom.inputSearch.addEventListener('input', (e) => {
      clearTimeout(searchDebounce);
      dom.btnClearSearch.style.display = e.target.value ? 'block' : 'none';
      searchDebounce = setTimeout(() => {
        state.searchQuery = e.target.value;
        state.page = 1;
        loadPrompts();
      }, 300);
    });

    dom.btnClearSearch.addEventListener('click', () => {
      dom.inputSearch.value = '';
      dom.btnClearSearch.style.display = 'none';
      state.searchQuery = '';
      state.page = 1;
      loadPrompts();
    });

    // Keyboard shortcut: '/' to focus search
    window.addEventListener('keydown', (e) => {
      if (e.key === '/' && document.activeElement !== dom.inputSearch && document.activeElement !== dom.inputPin) {
        e.preventDefault();
        dom.inputSearch.focus();
      } else if (e.key === 'Escape') {
        if (dom.chainDrawer.style.display === 'flex') closeChainDrawer();
        if (dom.settingsModal.style.display === 'flex') dom.settingsModal.style.display = 'none';
      }
    });

    // Software filter pills
    dom.softwareFilters.addEventListener('click', (e) => {
      if (e.target.classList.contains('pill-btn')) {
        dom.softwareFilters.querySelectorAll('.pill-btn').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        state.softwareFilter = e.target.dataset.software;
        state.page = 1;
        loadPrompts();
        if (state.currentView === 'graph') loadGraphData();
      }
    });

    // Secondary filters
    dom.selectDate.addEventListener('change', (e) => {
      state.dateFilter = e.target.value;
      state.page = 1;
      loadPrompts();
    });

    dom.selectSort.addEventListener('change', (e) => {
      state.sortOrder = e.target.value;
      state.page = 1;
      loadPrompts();
    });

    dom.checkHasOutput.addEventListener('change', (e) => {
      state.hasOutputFilter = e.target.checked;
      state.page = 1;
      loadPrompts();
    });

    // Pagination
    dom.btnPrevPage.addEventListener('click', () => {
      if (state.page > 1) {
        state.page--;
        loadPrompts();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    });

    dom.btnNextPage.addEventListener('click', () => {
      const totalPages = Math.ceil(state.totalPrompts / state.limit) || 1;
      if (state.page < totalPages) {
        state.page++;
        loadPrompts();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    });

    dom.btnSaveSettings.addEventListener('click', saveSettings);
    dom.btnForceFullResync.addEventListener('click', () => {
      if (confirm('Run full historical resync? This sweeps all historical logs across all worktrees and tools.')) {
        triggerManualSync(true);
        dom.settingsModal.style.display = 'none';
      }
    });

    dom.btnCreateHook.addEventListener('click', generatePushHook);
    dom.btnCopyHookToken.addEventListener('click', () => {
      copyToClipboard(dom.hookTokenVal.textContent, 'push hook token');
    });
  }

  // ─── Initialization ────────────────────────────────────────────────────────
  function init() {
    setupEventListeners();
    loadPrompts();
    checkSyncStatus();
    setInterval(checkSyncStatus, 20000); // Poll status every 20s
  }

  document.addEventListener('DOMContentLoaded', init);
})();
