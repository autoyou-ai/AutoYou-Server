// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

'use strict';

// Bootstrap injected by backend into window.__BOOTSTRAP__
var B = window.__BOOTSTRAP__ || {};
var agentName = B.agent_name || 'skills_agent';
var auth = B.auth || {};

// Main State Manager
var state = {
  skills: [],
  selectedSkillName: null,
  savedSkillData: null,    // Fresh copy from backend
  currentSkillData: null,  // Active editable copy
  activeFile: 'SKILL.md',  // Currently editing
  isNewSkill: false
};

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

// ── Bearer Token Authentication (WebRTC fallback) ───────────────────────────
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

function apiDelete(path) {
  var t = _storedToken();
  var opts = { method: 'DELETE', headers: t ? { 'Authorization': 'Bearer ' + t } : undefined };
  return fetch(path, opts).then(function(r) { return r.json(); });
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

// ── Auth Gate Verification ───────────────────────────────────────────────────

function _applyAuthenticated() {
  auth.authenticated = true;
  var chip = $('auth-chip');
  if (chip) { chip.textContent = 'Authenticated'; chip.className = 'status-chip authenticated'; }
  showEl('logout-btn');
  hideEl('login-gate');
  showEl('main-layout');
  loadSkills();
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
  if (chip) { chip.textContent = 'Locked'; chip.className = 'status-chip'; }
  hideEl('logout-btn');
  showEl('login-gate');
  hideEl('main-layout');
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

// ── Skills Loader ────────────────────────────────────────────────────────────

function loadSkills(selectNameAfterLoad) {
  apiGet('./api/skills').then(function(data) {
    if (!data.success) return;
    state.skills = data.skills || [];
    renderSkillsList();
    if (selectNameAfterLoad) {
      selectSkill(selectNameAfterLoad);
    } else if (state.selectedSkillName) {
      // Re-highlight current selection
      selectSkill(state.selectedSkillName);
    }
  }).catch(console.error);
}

function renderSkillsList() {
  var list = $('skill-list');
  var searchVal = $('skill-search').value.toLowerCase().trim();
  
  var filtered = state.skills.filter(function(s) {
    return s.name.toLowerCase().indexOf(searchVal) !== -1 ||
           (s.description || '').toLowerCase().indexOf(searchVal) !== -1;
  });

  if (!filtered.length) {
    list.innerHTML = '<div class="empty-state">No skills found.</div>';
    return;
  }

  list.innerHTML = '';
  filtered.forEach(function(skill) {
    var item = document.createElement('div');
    item.className = 'skill-item' + (state.selectedSkillName === skill.name ? ' active' : '');
    item.innerHTML =
      '<div class="skill-item-name">' + escHtml(skill.name) + '</div>' +
      '<div class="skill-item-desc">' + escHtml(skill.description || 'No description') + '</div>';
    item.addEventListener('click', function() {
      if (isDirty()) {
        if (!confirm('You have unsaved changes. Discard them and select another skill?')) return;
      }
      selectSkill(skill.name);
    });
    list.appendChild(item);
  });
}

// ── Skill Details Selection ──────────────────────────────────────────────────

function selectSkill(name) {
  state.selectedSkillName = name;
  state.isNewSkill = false;

  // Highlight selection in list
  document.querySelectorAll('.skill-item').forEach(function(el) {
    var nameEl = el.querySelector('.skill-item-name');
    el.classList.toggle('active', nameEl && nameEl.textContent === name);
  });

  hideEl('editor-welcome');
  showEl('skill-workspace');
  $('delete-skill-btn').disabled = false;
  $('skill-name-input').disabled = true; // Lock name for saved skills

  apiGet('./api/skills/' + encodeURIComponent(name)).then(function(data) {
    if (!data.success) {
      alert('Failed to load skill: ' + (data.error || 'unknown error'));
      return;
    }

    // Filter out root level SKILL.md from files to avoid duplicates (instructions handles SKILL.md body)
    var cleanedFiles = {};
    if (data.files) {
      for (var f in data.files) {
        if (f !== 'SKILL.md') {
          cleanedFiles[f] = data.files[f];
        }
      }
    }

    state.savedSkillData = {
      name: data.name,
      description: data.description,
      instructions: data.instructions || '',
      files: cleanedFiles
    };

    // Deep copy to current working state
    state.currentSkillData = JSON.parse(JSON.stringify(state.savedSkillData));
    state.activeFile = 'SKILL.md';

    $('skill-name-input').value = state.currentSkillData.name;
    $('skill-desc-input').value = state.currentSkillData.description;

    renderTabs();
    loadActiveFileContent();
    updateScriptSelector();
    clearConsole();
  }).catch(function(err) {
    alert('Network error loading skill details: ' + err);
  });
}

// ── New Skill Flow ───────────────────────────────────────────────────────────

function setupNewSkill() {
  if (isDirty()) {
    if (!confirm('You have unsaved changes. Discard them and create a new skill?')) return;
  }

  state.selectedSkillName = null;
  state.isNewSkill = true;

  // Clear selections in sidebar
  document.querySelectorAll('.skill-item').forEach(function(el) {
    el.classList.remove('active');
  });

  hideEl('editor-welcome');
  showEl('skill-workspace');
  $('delete-skill-btn').disabled = true;
  var nameInput = $('skill-name-input');
  nameInput.disabled = false;
  nameInput.value = '';
  nameInput.focus();
  $('skill-desc-input').value = '';

  state.savedSkillData = {
    name: '',
    description: '',
    instructions: '# User Guide\nDescribe how to use this skill here.',
    files: {}
  };
  state.currentSkillData = JSON.parse(JSON.stringify(state.savedSkillData));
  state.activeFile = 'SKILL.md';

  renderTabs();
  loadActiveFileContent();
  updateScriptSelector();
  clearConsole();
}

$('new-skill-btn').addEventListener('click', setupNewSkill);
$('welcome-new-btn').addEventListener('click', setupNewSkill);

// ── Dirty State Checking ─────────────────────────────────────────────────────

function isDirty() {
  if (!state.currentSkillData || !state.savedSkillData) return false;
  
  // Check metadata
  if (state.currentSkillData.name !== state.savedSkillData.name) return true;
  if (state.currentSkillData.description !== state.savedSkillData.description) return true;
  if (state.currentSkillData.instructions !== state.savedSkillData.instructions) return true;

  // Compare files
  var savedKeys = Object.keys(state.savedSkillData.files);
  var currentKeys = Object.keys(state.currentSkillData.files);
  if (savedKeys.length !== currentKeys.length) return true;

  for (var i = 0; i < currentKeys.length; i++) {
    var k = currentKeys[i];
    if (state.currentSkillData.files[k] !== state.savedSkillData.files[k]) return true;
  }

  return false;
}

// ── Tab Management & File CRUD ───────────────────────────────────────────────

function renderTabs() {
  var tabsContainer = $('editor-tabs');
  tabsContainer.innerHTML = '';

  if (!state.currentSkillData) return;

  // Add SKILL.md tab
  var skillMdTab = document.createElement('div');
  skillMdTab.className = 'tab' + (state.activeFile === 'SKILL.md' ? ' active' : '');
  skillMdTab.textContent = 'SKILL.md';
  skillMdTab.addEventListener('click', function() { switchTab('SKILL.md'); });
  tabsContainer.appendChild(skillMdTab);

  // Add other files
  var sortedFiles = Object.keys(state.currentSkillData.files).sort();
  sortedFiles.forEach(function(fpath) {
    var fileTab = document.createElement('div');
    fileTab.className = 'tab' + (state.activeFile === fpath ? ' active' : '');
    fileTab.textContent = fpath;
    fileTab.addEventListener('click', function() { switchTab(fpath); });
    tabsContainer.appendChild(fileTab);
  });

  // Toggle active file delete button (cannot delete SKILL.md)
  if (state.activeFile === 'SKILL.md') {
    hideEl('delete-file-btn');
  } else {
    showEl('delete-file-btn');
  }
}

function switchTab(fpath) {
  // Save current textarea content to active file in memory first
  saveTextareaToMemory();

  state.activeFile = fpath;
  renderTabs();
  loadActiveFileContent();
}

function saveTextareaToMemory() {
  if (!state.currentSkillData) return;
  var code = $('code-editor-textarea').value;
  if (state.activeFile === 'SKILL.md') {
    state.currentSkillData.instructions = code;
  } else if (state.currentSkillData.files[state.activeFile] !== undefined) {
    state.currentSkillData.files[state.activeFile] = code;
  }
}

function loadActiveFileContent() {
  var textarea = $('code-editor-textarea');
  if (!state.currentSkillData) {
    textarea.value = '';
    return;
  }

  if (state.activeFile === 'SKILL.md') {
    textarea.value = state.currentSkillData.instructions;
  } else {
    textarea.value = state.currentSkillData.files[state.activeFile] || '';
  }

  updateLineNumbers();
}

// Add File Modal Flow
$('add-file-btn').addEventListener('click', function() {
  showEl('new-file-modal');
  $('new-file-path-input').value = '';
  $('new-file-path-input').focus();
});

$('new-file-cancel').addEventListener('click', function() {
  hideEl('new-file-modal');
});

$('new-file-submit').addEventListener('click', attemptAddFile);
$('new-file-path-input').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') attemptAddFile();
});

function attemptAddFile() {
  var rawPath = $('new-file-path-input').value.trim();
  if (!rawPath) return;

  // Normalize path
  var cleanPath = rawPath.replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
  var parts = cleanPath.split('/').filter(function(p) { return p !== '' && p !== '.' && p !== '..'; });

  if (parts.length < 2 || ['scripts', 'references', 'assets'].indexOf(parts[0]) === -1) {
    alert("Invalid relative path. All custom skill files must reside under scripts/, references/, or assets/.\nExample: scripts/run.py");
    return;
  }

  var finalPath = parts.join('/');
  if (state.currentSkillData.files[finalPath] !== undefined) {
    alert("A file with this path already exists inside this skill folder.");
    return;
  }

  // Pre-populate standard starter content based on extension
  var initialCode = '';
  if (finalPath.endsWith('.py')) {
    initialCode = '#!/usr/bin/env python\n# -*- coding: utf-8 -*-\n\nprint("Hello from custom skill script!")\n';
  } else if (finalPath.endsWith('.sh') || finalPath.endsWith('.bash')) {
    initialCode = '#!/bin/bash\n\necho "Hello from custom skill shell script!"\n';
  } else if (finalPath.endsWith('.md')) {
    initialCode = '# Reference Documentation\n\nExplain how this script works or map utility steps here.';
  }

  // Save active file
  saveTextareaToMemory();

  // Add to working copy
  state.currentSkillData.files[finalPath] = initialCode;
  state.activeFile = finalPath;

  hideEl('new-file-modal');
  renderTabs();
  loadActiveFileContent();
  updateScriptSelector();
}

// Delete Active File
$('delete-file-btn').addEventListener('click', function() {
  if (state.activeFile === 'SKILL.md') return;
  if (!confirm('Are you sure you want to delete the file "' + state.activeFile + '"? This will take effect on save.')) return;

  delete state.currentSkillData.files[state.activeFile];
  state.activeFile = 'SKILL.md';
  renderTabs();
  loadActiveFileContent();
  updateScriptSelector();
});

// ── Line Numbers Renderer ────────────────────────────────────────────────────

function updateLineNumbers() {
  var textarea = $('code-editor-textarea');
  var lineNumbersDiv = $('editor-line-numbers');
  
  var lines = textarea.value.split('\n');
  var html = '';
  for (var i = 1; i <= lines.length; i++) {
    html += '<div>' + i + '</div>';
  }
  lineNumbersDiv.innerHTML = html;
}

$('code-editor-textarea').addEventListener('input', function() {
  updateLineNumbers();
});

$('code-editor-textarea').addEventListener('scroll', function() {
  $('editor-line-numbers').scrollTop = this.scrollTop;
});

// ── Save / Delete / Discard Buttons ──────────────────────────────────────────

$('save-skill-btn').addEventListener('click', function() {
  if (!state.currentSkillData) return;

  // Sync active file
  saveTextareaToMemory();

  // If new skill, get name from inputs
  var name = state.isNewSkill ? $('skill-name-input').value.trim() : state.currentSkillData.name;
  var description = $('skill-desc-input').value.trim();

  if (!name) {
    alert("Skill name is required.");
    return;
  }
  if (!description) {
    alert("Skill description is required.");
    return;
  }

  var btn = this;
  btn.disabled = true;
  btn.textContent = 'Saving…';

  var payload = {
    name: name,
    description: description,
    instructions: state.currentSkillData.instructions,
    files: state.currentSkillData.files
  };

  apiPost('./api/skills', payload).then(function(data) {
    btn.disabled = false;
    btn.textContent = 'Save Changes';
    
    if (data.success) {
      state.isNewSkill = false;
      loadSkills(name);
      setTimeout(function() {
        alert("Skill '" + name + "' saved successfully!");
      }, 50);
    } else {
      alert("Failed to save skill: " + (data.error || 'unknown error'));
    }
  }).catch(function(err) {
    btn.disabled = false;
    btn.textContent = 'Save Changes';
    alert("Network error saving skill: " + err);
  });
});

$('delete-skill-btn').addEventListener('click', function() {
  if (!state.selectedSkillName) return;
  if (!confirm("Are you sure you want to delete '" + state.selectedSkillName + "'? All skill files, scripts, and instructions will be deleted permanently.")) return;

  var btn = this;
  btn.disabled = true;

  apiDelete('./api/skills/' + encodeURIComponent(state.selectedSkillName)).then(function(data) {
    btn.disabled = false;
    if (data.success) {
      state.selectedSkillName = null;
      state.savedSkillData = null;
      state.currentSkillData = null;
      hideEl('skill-workspace');
      showEl('editor-welcome');
      loadSkills();
      setTimeout(function() {
        alert("Skill deleted successfully.");
      }, 50);
    } else {
      alert("Failed to delete skill: " + (data.error || 'unknown error'));
    }
  }).catch(function(err) {
    btn.disabled = false;
    alert("Network error deleting skill: " + err);
  });
});

$('discard-skill-changes-btn').addEventListener('click', function() {
  if (!state.currentSkillData) return;
  if (!isDirty()) return;
  if (!confirm("Discard all unsaved edits for this skill?")) return;

  // Restore copy
  state.currentSkillData = JSON.parse(JSON.stringify(state.savedSkillData));
  
  if (state.isNewSkill) {
    setupNewSkill();
  } else {
    $('skill-name-input').value = state.currentSkillData.name;
    $('skill-desc-input').value = state.currentSkillData.description;
    state.activeFile = 'SKILL.md';
    renderTabs();
    loadActiveFileContent();
    updateScriptSelector();
    clearConsole();
  }
});

// ── Kebab-case validation for new skills ─────────────────────────────────────

$('skill-name-input').addEventListener('input', function() {
  if (!state.isNewSkill) return;
  var val = this.value;
  // Auto normalize spaces and uppercase characters
  var clean = val.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-_]/g, '');
  if (val !== clean) {
    this.value = clean;
  }
});

// ── Runner / Console Terminal Drawer ─────────────────────────────────────────

function updateScriptSelector() {
  var select = $('script-selector');
  select.innerHTML = '';

  if (!state.currentSkillData) {
    hideEl('script-selector-container');
    return;
  }

  var runnableFiles = [];
  for (var fpath in state.currentSkillData.files) {
    if (fpath.startsWith('scripts/') && (fpath.endsWith('.py') || fpath.endsWith('.sh') || fpath.endsWith('.bash'))) {
      runnableFiles.push(fpath);
    }
  }

  if (!runnableFiles.length) {
    hideEl('script-selector-container');
    return;
  }

  showEl('script-selector-container');
  runnableFiles.sort().forEach(function(f) {
    var opt = document.createElement('option');
    opt.value = f;
    opt.textContent = f;
    select.appendChild(opt);
  });
}

function clearConsole() {
  $('console-output').innerHTML = '<div class="console-line system">Terminal ready. Choose a script from your skill and click Run Script.</div>';
}

$('clear-console-btn').addEventListener('click', clearConsole);

// Console Collapse / Toggle
$('console-toggle').addEventListener('click', function() {
  var drawer = $('console-drawer');
  drawer.classList.toggle('collapsed');
  this.textContent = drawer.classList.contains('collapsed') ? '▲' : '▼';
});

function appendConsoleLine(text, kind) {
  var out = $('console-output');
  var line = document.createElement('div');
  line.className = 'console-line ' + (kind || 'stdout');
  line.textContent = text;
  out.appendChild(line);
  out.scrollTop = out.scrollHeight;
}

// Run Script Action
$('run-script-btn').addEventListener('click', function() {
  if (!state.currentSkillName && !state.selectedSkillName) return;
  
  var name = state.selectedSkillName;
  if (!name) {
    alert("Please save the skill first before running scripts.");
    return;
  }

  if (isDirty()) {
    alert("You have unsaved changes. Please save the skill before running scripts.");
    return;
  }

  var selector = $('script-selector');
  var scriptPath = selector.value;
  if (!scriptPath) {
    alert("No scripts found to run.");
    return;
  }

  var btn = this;
  btn.disabled = true;
  btn.textContent = '⚡ Running…';

  var drawer = $('console-drawer');
  drawer.classList.remove('collapsed');
  $('console-toggle').textContent = '▼';

  appendConsoleLine('=== Running ' + scriptPath + ' ===', 'system');

  apiPost('./api/skills/' + encodeURIComponent(name) + '/run', { script: scriptPath }).then(function(data) {
    btn.disabled = false;
    btn.textContent = '⚡ Run Script';

    if (data.success) {
      if (data.stdout) {
        data.stdout.split('\n').forEach(function(line) {
          if (line.trim() !== '') appendConsoleLine(line, 'stdout');
        });
      }
      if (data.stderr) {
        data.stderr.split('\n').forEach(function(line) {
          if (line.trim() !== '') appendConsoleLine(line, 'stderr');
        });
      }
      appendConsoleLine('Run finished with exit code ' + data.exit_code, data.exit_code === 0 ? 'success' : 'error');
    } else {
      appendConsoleLine('Run failed: ' + (data.error || 'unknown error'), 'error');
    }
  }).catch(function(err) {
    btn.disabled = false;
    btn.textContent = '⚡ Run Script';
    appendConsoleLine('Network error while running script: ' + err, 'error');
  });
});

// Search input keydown handler
$('skill-search').addEventListener('input', function() {
  renderSkillsList();
});

// ── Mobile Sidebar Drawer Handler ───────────────────────────────────────────
function initMobileSidebarHandler() {
  var toggleBtn = $('toggle-sidebar-btn');
  var sidebar = $('sidebar');
  var backdrop = $('sidebar-backdrop');
  
  if (!toggleBtn || !sidebar || !backdrop) return;
  
  function openSidebar() {
    sidebar.classList.add('active');
    backdrop.classList.add('active');
  }
  
  function closeSidebar() {
    sidebar.classList.remove('active');
    backdrop.classList.remove('active');
  }
  
  toggleBtn.addEventListener('click', function(e) {
    e.stopPropagation();
    if (sidebar.classList.contains('active')) {
      closeSidebar();
    } else {
      openSidebar();
    }
  });
  
  backdrop.addEventListener('click', closeSidebar);
  
  $('new-skill-btn').addEventListener('click', function() {
    if (window.innerWidth <= 768) {
      closeSidebar();
    }
  });
  
  var skillList = $('skill-list');
  if (skillList) {
    skillList.addEventListener('click', function(e) {
      if (window.innerWidth <= 768) {
        if (e.target.closest('.skill-item')) {
          closeSidebar();
        }
      }
    });
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────

_initTheme();
hydrateTopbarLinks();
initAuth();
initMobileSidebarHandler();
