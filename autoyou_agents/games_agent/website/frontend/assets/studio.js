// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

'use strict';

(function () {
  function $(id) { return document.getElementById(id); }

  var sessionId = '';
  var publishBearerToken = '';
  var cachedProtocol = null;

  // ── Theme ───────────────────────────────────────────────────────────────
  function getTheme() { try { return localStorage.getItem('autoyou.ui.theme') || 'dark'; } catch (_) { return 'dark'; } }
  function setTheme(t) {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem('autoyou.ui.theme', t); } catch (_) {}
  }
  setTheme(getTheme());
  if ($('theme-toggle')) {
    $('theme-toggle').addEventListener('click', function () { setTheme(getTheme() === 'dark' ? 'light' : 'dark'); });
  }

  // ── Tabs ────────────────────────────────────────────────────────────────
  function switchTab(tabName) {
    document.querySelectorAll('.studio-tab').forEach(function (btn) {
      var active = btn.getAttribute('data-tab') === tabName;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    document.querySelectorAll('.tab-pane').forEach(function (pane) {
      pane.classList.toggle('active', pane.id === 'pane-' + tabName);
    });
    if (tabName === 'publish') checkPublishAuth();
    if (tabName === 'host') refreshHostStatus();
  }

  document.querySelectorAll('.studio-tab').forEach(function (btn) {
    btn.addEventListener('click', function () { switchTab(btn.getAttribute('data-tab') || 'arcade'); });
  });

  if ($('goto-create-btn')) {
    $('goto-create-btn').addEventListener('click', function () {
      switchTab('create');
      if ($('chat-input')) $('chat-input').focus();
    });
  }

  // ── In-Page Arcade Player ───────────────────────────────────────────────
  function openPlayer(url, title) {
    var modal = $('player-modal');
    var iframe = $('player-iframe');
    var titleEl = $('player-title');
    if (!modal || !iframe) return;
    if (titleEl) titleEl.textContent = title || 'Playing Game';
    iframe.src = url;
    modal.classList.remove('hidden');
  }

  function closePlayer() {
    var modal = $('player-modal');
    var iframe = $('player-iframe');
    if (modal) modal.classList.add('hidden');
    if (iframe) iframe.src = 'about:blank';
    try { if (document.fullscreenElement) document.exitFullscreen(); } catch (_) {}
  }

  if ($('player-close-btn')) $('player-close-btn').addEventListener('click', closePlayer);
  if ($('player-fullscreen-btn')) {
    $('player-fullscreen-btn').addEventListener('click', function () {
      var modal = $('player-modal');
      try {
        if (document.fullscreenElement) document.exitFullscreen();
        else if (modal && modal.requestFullscreen) modal.requestFullscreen().catch(function () {});
      } catch (_) {}
    });
  }

  window.addEventListener('message', function (ev) {
    if (ev && ev.data && ev.data.type === 'autoyou_game_quit') closePlayer();
  });

  window.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && $('player-modal') && !$('player-modal').classList.contains('hidden')) {
      closePlayer();
    }
  });

  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-play-url]');
    if (!btn) return;
    openPlayer(btn.getAttribute('data-play-url'), btn.getAttribute('data-play-title'));
  });

  if ($('quick-play-btn')) {
    $('quick-play-btn').addEventListener('click', function () { openPlayer('./play', 'Neon Horizon'); });
  }

  // ── Game Package Library ────────────────────────────────────────────────
  function escHtml(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  async function refreshPackages() {
    var list = $('package-list');
    if (!list) return;
    try {
      var response = await fetch('./api/game/packages', { credentials: 'same-origin' });
      if (!response.ok) {
        list.innerHTML = '<div class="empty-card">Sign in to view your offline game library.</div>';
        return;
      }
      var data = await response.json();
      var games = Array.isArray(data.games) ? data.games : [];
      if (!games.length) {
        list.innerHTML = '<div class="empty-card">No custom games published yet. Use <b>AI Game Maker</b> or <b>Publish HTML</b> to add your first game!</div>';
        return;
      }
      list.innerHTML = games.map(function (g) {
        var id = String(g.id || '');
        var title = String(g.title || id);
        var ver = String(g.version || '1');
        var playUrl = './api/game/packages/' + encodeURIComponent(id) + '/play';
        var dlUrl = './api/game/packages/' + encodeURIComponent(id) + '/download';
        return '<article class="game-card">'
          + '<div class="game-banner banner-pkg">'
            + '<span class="game-badge">v' + escHtml(ver) + ' · Offline Ready</span>'
            + '<span class="game-glyph" aria-hidden="true">🕹️</span>'
          + '</div>'
          + '<div class="game-body">'
            + '<h3>' + escHtml(title) + '</h3>'
            + '<p>Synced to your computer and ready to play here or download offline in the AutoYou iOS &amp; Android apps.</p>'
            + '<div class="card-actions">'
              + '<button class="btn btn-primary btn-sm" type="button" data-play-url="' + escHtml(playUrl) + '" data-play-title="' + escHtml(title) + '">Play Now</button>'
              + '<a class="btn btn-ghost btn-sm" href="' + escHtml(dlUrl) + '" download="' + escHtml(id) + '.aygame">Download .aygame</a>'
            + '</div>'
          + '</div>'
        + '</article>';
      }).join('');
    } catch (_) {
      list.innerHTML = '<div class="empty-card">Could not load game packages right now.</div>';
    }
  }

  if ($('refresh-packages-btn')) $('refresh-packages-btn').addEventListener('click', refreshPackages);

  // ── Host Input Status & Protocol ────────────────────────────────────────
  async function refreshHostStatus() {
    var pill = $('engine-status-pill');
    var modeVal = $('host-mode-val');
    var engVal = $('host-engine-val');
    try {
      var resp = await fetch('./api/game/native-input/status', { credentials: 'same-origin' });
      if (!resp.ok) throw new Error('status');
      var data = await resp.json();
      var avail = !!data.available;
      var connected = !!data.engine_connected;
      if (pill) {
        pill.textContent = connected ? 'Engine Live' : (avail ? 'Host Ready' : 'Offline Arcade');
        pill.className = 'engine-pill' + (connected || avail ? ' live' : '');
      }
      if (modeVal) modeVal.textContent = avail ? 'Enabled' : 'Disabled in Admin';
      if (engVal) engVal.textContent = connected ? 'Connected' : 'Waiting for engine';
    } catch (_) {
      if (pill) { pill.textContent = 'Arcade Ready'; pill.className = 'engine-pill'; }
      if (modeVal) modeVal.textContent = 'Unavailable';
      if (engVal) engVal.textContent = 'Not connected';
    }
  }

  if ($('toggle-protocol-btn')) {
    $('toggle-protocol-btn').addEventListener('click', async function () {
      var box = $('protocol-json');
      if (!box) return;
      if (!box.classList.contains('hidden')) {
        box.classList.add('hidden');
        return;
      }
      box.classList.remove('hidden');
      if (cachedProtocol) {
        box.textContent = JSON.stringify(cachedProtocol, null, 2);
        return;
      }
      box.textContent = 'Loading protocol…';
      try {
        var r = await fetch('./api/game/protocol', { credentials: 'same-origin' });
        cachedProtocol = await r.json();
        box.textContent = JSON.stringify(cachedProtocol, null, 2);
      } catch (_) {
        box.textContent = 'Could not fetch protocol JSON.';
      }
    });
  }

  // ── AI Game Maker Chat ──────────────────────────────────────────────────
  var chatForm = $('chat-form');
  var chatLog = $('chat-log');
  var chatInput = $('chat-input');

  function appendChat(role, text) {
    if (!chatLog) return;
    var empty = chatLog.querySelector('.chat-empty');
    if (empty) empty.remove();
    var div = document.createElement('div');
    div.className = 'chat-msg ' + role;
    div.textContent = text;
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  document.querySelectorAll('.idea-chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
      if (chatInput) {
        chatInput.value = chip.getAttribute('data-idea') || '';
        chatInput.focus();
      }
    });
  });

  if (chatForm) {
    chatForm.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      var message = (chatInput.value || '').trim();
      if (!message) return;
      chatInput.value = '';
      var btn = chatForm.querySelector('button[type="submit"]');
      if (btn) btn.disabled = true;
      appendChat('user', message);
      try {
        var body = { message: 'Go to autoyou_games_agent. ' + message };
        if (sessionId) body.session_id = sessionId;
        var resp = await fetch('./api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify(body),
        });
        var payload = await resp.json();
        if (!resp.ok || payload.success === false) throw new Error(payload.error || 'The agent could not respond.');
        sessionId = payload.session_id || sessionId;
        appendChat('agent', payload.response || payload.message || '(no response)');
        refreshPackages();
      } catch (err) {
        appendChat('agent', 'Error: ' + err.message);
      } finally {
        if (btn) btn.disabled = false;
        chatInput.focus();
      }
    });
  }

  // ── Publish HTML Game + Preflight + OTP ─────────────────────────────────
  var publishForm = $('publish-form');
  var fileInput = $('game-file');
  var preflightBox = $('preflight-box');
  var publishStatus = $('publish-status');
  var dropzone = $('dropzone');

  async function checkPublishAuth() {
    var otpBox = $('publish-otp-box');
    if (!otpBox) return;
    try {
      var headers = publishBearerToken ? { 'Authorization': 'Bearer ' + publishBearerToken } : undefined;
      var resp = await fetch('./api/game/publish-auth/status', { credentials: 'same-origin', headers: headers });
      if (!resp.ok) return;
      var data = await resp.json();
      otpBox.classList.toggle('hidden', !(data.required && !data.authenticated));
    } catch (_) {}
  }

  if ($('publish-otp-btn')) {
    $('publish-otp-btn').addEventListener('click', async function () {
      var inp = $('publish-otp-input');
      var errEl = $('publish-otp-error');
      var code = (inp && inp.value || '').trim();
      if (code.length < 6) return;
      try {
        var r = await fetch('./api/game/publish-auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ code: code }),
        });
        var data = await r.json();
        if (r.ok && data.success) {
          publishBearerToken = data.token || '';
          if (errEl) errEl.classList.add('hidden');
          if ($('publish-otp-box')) $('publish-otp-box').classList.add('hidden');
        } else {
          if (errEl) { errEl.textContent = data.error || 'Invalid code.'; errEl.classList.remove('hidden'); }
        }
      } catch (_) {
        if (errEl) { errEl.textContent = 'Network error.'; errEl.classList.remove('hidden'); }
      }
    });
  }

  async function validateSelectedFile(file) {
    if (!preflightBox || !file) return;
    if ($('file-label')) $('file-label').textContent = file.name + ' (' + Math.max(1, Math.round(file.size / 1024)) + ' KB)';
    var issues = [];
    if (file.size > 8 * 1024 * 1024 - 1024) issues.push('File exceeds the 8 MiB single-file HTML limit.');
    try {
      var text = await file.text();
      if (!/<head[\s>]/i.test(text.slice(0, 4096))) issues.push('Missing <head> tag near the top of the HTML.');
      if (text.indexOf('AutoYouGame.quit') < 0) issues.push('Missing AutoYouGame.quit() call for the in-game Quit button.');
    } catch (_) {
      issues.push('Could not read file as UTF-8 text.');
    }
    preflightBox.classList.remove('hidden', 'ok', 'err');
    if (issues.length) {
      preflightBox.classList.add('err');
      preflightBox.textContent = 'Needs fix before publishing: ' + issues.join(' ');
    } else {
      preflightBox.classList.add('ok');
      preflightBox.textContent = 'Looks great! Valid UTF-8 HTML with <head> and AutoYouGame.quit(). Ready to publish.';
    }
  }

  if (fileInput) {
    fileInput.addEventListener('change', function () {
      if (fileInput.files && fileInput.files[0]) validateSelectedFile(fileInput.files[0]);
    });
  }

  if (dropzone) {
    ['dragenter', 'dragover'].forEach(function (evName) {
      dropzone.addEventListener(evName, function (e) { e.preventDefault(); dropzone.classList.add('dragover'); });
    });
    ['dragleave', 'drop'].forEach(function (evName) {
      dropzone.addEventListener(evName, function (e) { e.preventDefault(); dropzone.classList.remove('dragover'); });
    });
    dropzone.addEventListener('drop', function (e) {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0] && fileInput) {
        fileInput.files = e.dataTransfer.files;
        validateSelectedFile(fileInput.files[0]);
      }
    });
  }

  if (publishForm) {
    publishForm.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      var file = fileInput && fileInput.files && fileInput.files[0];
      if (!file) return;
      var btn = $('publish-submit-btn');
      if (btn) btn.disabled = true;
      publishStatus.className = 'status-banner';
      publishStatus.textContent = 'Publishing game…';
      try {
        var id = file.name.replace(/\.html$/i, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
        if (!id || id.length > 64) throw new Error('Choose a short alphanumeric HTML filename.');
        var headers = { 'Content-Type': 'text/html; charset=utf-8' };
        if (publishBearerToken) headers['Authorization'] = 'Bearer ' + publishBearerToken;
        var resp = await fetch('./api/game/publish/' + encodeURIComponent(id), {
          method: 'POST',
          headers: headers,
          credentials: 'same-origin',
          body: file,
        });
        var payload = await resp.json();
        if (!resp.ok || !payload.success) {
          if (resp.status === 401) checkPublishAuth();
          throw new Error(payload.error || 'Could not publish game.');
        }
        publishStatus.textContent = id + ' is live! Find it in Arcade or reload Games in the AutoYou mobile app.';
        refreshPackages();
      } catch (err) {
        publishStatus.className = 'status-banner error';
        publishStatus.textContent = err.message;
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  }

  if ($('download-starter-btn')) {
    $('download-starter-btn').addEventListener('click', function () {
      var starter = [
        '<!doctype html>',
        '<html lang="en">',
        '<head>',
        '  <meta charset="utf-8">',
        '  <meta name="viewport" content="width=device-width,initial-scale=1">',
        '  <title>Star Tap Starter</title>',
        '  <style>body{margin:0;background:#081020;color:#fff;font:600 16px system-ui;display:grid;place-items:center;min-height:100vh}button{padding:12px 20px;border-radius:12px;border:0;font:inherit;cursor:pointer;margin:6px}.play{background:#10b981;color:#052116}.quit{background:#334155;color:#fff}</style>',
        '</head>',
        '<body>',
        '  <div style="text-align:center">',
        '    <h1>Star Tap</h1>',
        '    <p>Score: <span id="score">0</span></p>',
        '    <button class="play" onclick="document.getElementById(\'score\').textContent=++window.s">Tap to Score</button>',
        '    <button class="quit" onclick="window.AutoYouGame && AutoYouGame.quit()">Quit (X)</button>',
        '  </div>',
        '  <script>window.s=0;</script>',
        '</body>',
        '</html>',
      ].join('\n');
      var blob = new Blob([starter], { type: 'text/html;charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = 'star-tap-starter.html';
      a.click();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    });
  }

  // ── Boot ────────────────────────────────────────────────────────────────
  refreshPackages();
  refreshHostStatus();
})();
