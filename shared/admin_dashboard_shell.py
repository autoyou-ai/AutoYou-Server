# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-42952dcaa04d90725f16d9dd

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import html

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-42952dcaa04d90725f16d9dd"


_NAV_GROUPS = (
    (
        "Control Center",
        (
            ("dashboard-overview-card", "Overview"),
            ("server-settings-card", "Server"),
            ("password-settings-card", "Password"),
        ),
    ),
    (
        "AI and Agents",
        (
            ("ollama-settings-card", "Providers"),
            ("model-library-card", "Model Library"),
            ("agent-studio-card", "Agent Studio"),
            ("agent-instructions-card", "Agent Instructions"),
            ("agent-control-card", "Restart AI Agents"),
            ("autoyou-page-settings-card", "Page"),
            ("speech-settings-card", "Speech"),
        ),
    ),
    (
        "Connectivity",
        (
            ("autoyou-cloud-card", "AutoYou Cloud Pair"),
            ("tunnelmole-settings-card", "Tunnelmole"),
            ("rtc-settings-card", "WebRTC"),
            ("admin-frontend-proxy-card", "Browser Proxies"),
            ("datachannel-status-card", "Datachannels"),
            ("scheduler-queue-card", "Notification Queue"),
        ),
    ),
    (
        "Messaging",
        (
            ("telegram-settings-card", "Telegram"),
            ("signal-settings-card", "Signal"),
            ("whatsapp-settings-card", "WhatsApp"),
            ("security-settings-card", "Security"),
        ),
    ),
    (
        "Clients and Utilities",
        (
            ("qr-provision-card", "Provision QR"),
            ("minigame-card", "Boot Sweep"),
            ("jailbreak-settings-card", "Jailbreak"),
            ("help-guides-card", "Help"),
        ),
    ),
)


def _build_nav_groups(*, show_setup_wizard: bool) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    groups = []
    for group_label, items in _NAV_GROUPS:
        next_items = list(items)
        if group_label == "Control Center" and show_setup_wizard:
            next_items.insert(1, ("wizard-launcher-card", "Setup Wizard"))
        groups.append((group_label, tuple(next_items)))
    return tuple(groups)


def build_admin_search_card_html() -> str:
    return """
    <div class='card admin-command-card' id='admin-search-card'>
      <div class='admin-command-shell'>
        <div class='admin-search-bar-row'>
          <div class='admin-search-wrap'>
            <button
              type='button'
              id='admin-mobile-sections-toggle'
              class='admin-search-menu-btn'
              data-no-loading='1'
              aria-controls='admin-mobile-sections-sheet'
              aria-expanded='false'
              aria-label='Open sections navigator'
              title='Open sections navigator'
            >
              <span class='admin-search-menu-icon' aria-hidden='true'>&#9776;</span>
            </button>
            <button type='button' id='admin-search-icon-btn' class='admin-search-icon' title='Search settings' aria-label='Search settings'>&#128269;</button>
            <input
              type='text'
              id='admin-settings-search'
              class='admin-search-field'
              placeholder='Search settings, providers, ports, voices, security, tunnels...'
              autocomplete='off'
              spellcheck='false'
            >
            <div class='admin-search-tools'>
              <button type='button' id='admin-search-reset' class='admin-search-reset' hidden title='Clear search' aria-label='Clear search'>&#10005;</button>
              <button type='button' id='admin-search-toggle' class='admin-search-toggle' aria-expanded='false' aria-controls='admin-search-panel' title='Expand search help and quick jumps'>
                <span class='admin-search-toggle-icon' aria-hidden='true'>&#9662;</span>
              </button>
            </div>
          </div>
        </div>
        <div id='admin-search-panel' class='admin-search-panel' hidden>
          <div class='admin-command-head'>
            <div class='admin-command-copy'>
              <h2>Search Settings</h2>
              <p class='muted'>Optional quick jumps and search results. Press Enter in the search bar to jump to the top match without opening this panel.</p>
            </div>
            <div class='admin-jump-grid' id='admin-search-jumps' aria-label='Quick jump sections'></div>
          </div>
          <div id='admin-search-results' class='admin-search-results' aria-live='polite'>
            <div class='admin-search-empty'>Start typing to search sections, labels, selected values, and buttons across the admin dashboard.</div>
          </div>
        </div>
      </div>
    </div>
    """


def build_admin_dashboard_shell_open_html(server_name: str, *, show_setup_wizard: bool = True) -> str:
    server_name_display = html.escape(str(server_name or "").strip() or "AutoYou", quote=True)
    # from __debug_provenance_i__ import or

    nav_markup = []
    for group_label, items in _build_nav_groups(show_setup_wizard=show_setup_wizard):
        link_markup = []
        for section_id, section_label in items:
            wizard_attr = " data-admin-wizard-link='1'" if section_id == "wizard-launcher-card" else ""
            link_markup.append(
                f"<a href='#{section_id}' class='admin-nav-link' data-admin-section-link='{section_id}'"
                f"{wizard_attr}>{html.escape(section_label)}</a>"
            )
        links = "".join(link_markup)
        nav_markup.append(
            f"""
            <section class='admin-nav-group'>
              <div class='admin-nav-group-label'>{html.escape(group_label)}</div>
              <div class='admin-nav-links'>{links}</div>
            </section>
            """
        )
    nav_groups_html = ''.join(nav_markup)

    return f"""
    <div class='admin-dashboard-shell' id='admin-dashboard-shell'>
      <div class='admin-mobile-sections-sheet' id='admin-mobile-sections-sheet' hidden>
        <button type='button' class='admin-mobile-sections-backdrop' id='admin-mobile-sections-backdrop' data-no-loading='1' aria-label='Close sections navigator'></button>
        <section class='admin-mobile-sections-panel' role='dialog' aria-modal='true' aria-labelledby='admin-mobile-sections-title'>
          <div class='admin-mobile-sections-head'>
            <div class='admin-mobile-sections-title-wrap'>
              <div class='admin-mobile-sections-title' id='admin-mobile-sections-title'>Sections</div>
            </div>
            <button type='button' id='admin-mobile-sections-close' class='admin-mobile-sections-close' data-no-loading='1'>Close</button>
          </div>
          <div class='admin-mobile-sections-scroll' id='admin-mobile-sections-scroll'>
            <nav class='admin-mobile-nav' aria-label='Admin sections'>
              {nav_groups_html}
            </nav>
          </div>
        </section>
      </div>
      <div class='admin-main-panels'>
    """


def build_admin_dashboard_shell_close_html() -> str:
    return """
      </div>
    </div>
    """


def build_admin_search_script_html() -> str:
    return """
    <script>
      let adminSearchIndex = [];
      let adminSearchResults = [];
      let adminSearchRefreshTimer = null;
      let adminSearchHighlightTimer = null;
      let adminSearchPinnedOpen = false;

      function adminNormalizeSearch(text) {
        return String(text || "").replace(/\\s+/g, " ").trim().toLowerCase();
      }

      function adminGetTopCards() {
        const scope = document.querySelector('.admin-main-panels') || document.querySelector('.container');
        if (!scope) {
          return [];
        }
        return Array.from(scope.querySelectorAll(':scope > .card[id], :scope > section[id], :scope > .agent-editor-card[id]')).filter((card) => {
          if (!card || card.id === 'admin-search-card') {
            return false;
          }
          if (card.hidden || card.getAttribute('aria-hidden') === 'true') {
            return false;
          }
          if (card.classList.contains('success') || card.classList.contains('warn')) {
            return false;
          }
          return true;
        });
      }

      function adminCardHeading(card) {
        if (!card) {
          return 'Section';
        }
        const heading = card.querySelector(':scope > h1, :scope > h2, :scope > h3') || card.querySelector('h1, h2, h3');
        if (heading && heading.textContent) {
          return heading.textContent.replace(/\\s+/g, ' ').trim();
        }
        return (card.id || 'Section').replace(/[-_]+/g, ' ');
      }

      function adminCardSearchAliases(card) {
        return String((card && card.getAttribute && card.getAttribute('data-search-aliases')) || '')
          .replace(/\\s+/g, ' ')
          .trim();
      }

      function adminFindTopCard(node) {
        if (!node) {
          return null;
        }
        return adminGetTopCards().find((card) => card.contains(node)) || node.closest('.card[id], section[id], .agent-editor-card[id]');
      }

      function adminExtractHeadingText(node) {
        if (!node) {
          return '';
        }
        return (node.textContent || '').replace(/\\s+/g, ' ').trim();
      }

      function adminFindAssociatedLabel(node) {
        if (!node || !node.id) {
          return null;
        }
        const safeId = typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
          ? CSS.escape(node.id)
          : node.id.replace(/"/g, '\\"');
        return document.querySelector(`label[for="${safeId}"]`);
      }

      function adminReadableControlValue(node) {
        if (!node) {
          return '';
        }
        if (node.matches('input[type="password"]')) {
          return '';
        }
        if (node.tagName === 'SELECT') {
          return Array.from(node.selectedOptions || [])
            .map((option) => adminExtractHeadingText(option))
            .filter(Boolean)
            .join(' ');
        }
        if (node.matches('input[type="checkbox"], input[type="radio"]')) {
          return node.checked ? 'enabled selected checked on' : 'disabled unchecked off';
        }
        if (node.matches('button, summary')) {
          return adminExtractHeadingText(node);
        }
        return String(node.value || '').trim().slice(0, 180);
      }

      function adminControlSearchMeta(node) {
        if (!node) {
          return { title: '', copy: '' };
        }
        const label = adminFindAssociatedLabel(node);
        const labelText = adminExtractHeadingText(label);
        const placeholder = String(node.getAttribute('placeholder') || '').trim();
        const ariaLabel = String(node.getAttribute('aria-label') || '').trim();
        const title = labelText || adminExtractHeadingText(node) || placeholder || ariaLabel || node.name || node.id || 'Setting';
        const copyParts = [
          labelText,
          placeholder,
          ariaLabel,
          String(node.name || ''),
          String(node.id || ''),
          adminReadableControlValue(node),
        ].filter(Boolean);
        return {
          title,
          copy: copyParts.join(' '),
        };
      }

      function adminResolveSearchTarget(node) {
        if (!node) {
          return null;
        }
        if (node.matches('label[for]')) {
          const control = document.getElementById(node.getAttribute('for'));
          if (control) {
            return control;
          }
        }
        if (node.matches('input, select, textarea, button, summary')) {
          return node;
        }
        return node.matches('summary') ? node : (node.closest('.card, section, .agent-editor-card') || node);
      }

      function adminCreateSearchEntry(sectionCard, node, title, copy, kind) {
        const section = adminCardHeading(sectionCard);
        const target = adminResolveSearchTarget(node);
        const finalTitle = String(title || copy || section || '').trim();
        const finalCopy = String(copy || '').trim();
        const normalizedTitle = adminNormalizeSearch(finalTitle);
        const normalizedBody = adminNormalizeSearch([section, finalTitle, finalCopy].join(' '));
        if (!target || !normalizedBody) {
          return null;
        }
        return {
          section,
          title: finalTitle,
          copy: finalCopy,
          kind,
          target,
          normalizedTitle,
          normalizedBody,
        };
      }

      function adminBuildSearchIndex() {
        const entries = [];
        const seen = new Set();
        adminGetTopCards().forEach((card, sectionIndex) => {
          const sectionTitle = adminCardHeading(card);
          const sectionAliases = adminCardSearchAliases(card);
          const sectionContext = [sectionTitle, sectionAliases].filter(Boolean).join(' ');
          const sectionKey = ['section', card.id, sectionTitle].join('|');
          if (!seen.has(sectionKey)) {
            seen.add(sectionKey);
            const sectionEntry = adminCreateSearchEntry(card, card, sectionTitle, sectionAliases, 'section');
            if (sectionEntry) {
              sectionEntry.rankBias = 220 - sectionIndex;
              entries.push(sectionEntry);
            }
          }

          const headingCandidates = Array.from(card.querySelectorAll('h3, h4, details > summary, .prov-name'));
          headingCandidates.forEach((node, index) => {
            if (!node || node.closest('#admin-search-card')) {
              return;
            }
            const title = adminExtractHeadingText(node);
            if (!title || title === sectionTitle) {
              return;
            }
            const entry = adminCreateSearchEntry(card, node, title, sectionContext, 'section');
            if (!entry) {
              return;
            }
            const dedupeKey = [entry.kind, entry.section, entry.title, entry.target.id || entry.target.name || entry.target.tagName, entry.copy].join('|');
            if (seen.has(dedupeKey)) {
              return;
            }
            seen.add(dedupeKey);
            entry.rankBias = Math.max(0, 180 - index);
            entries.push(entry);
          });

          const interactionNodes = Array.from(card.querySelectorAll('label[for], button, .checkbox-label, .model-source-tab, input, select, textarea'));
          interactionNodes.forEach((node, index) => {
            if (!node || node.closest('#admin-search-card')) {
              return;
            }
            if (node.matches('input[type="hidden"]')) {
              return;
            }
            const meta = adminControlSearchMeta(node);
            if (!meta.title) {
              return;
            }
            const entry = adminCreateSearchEntry(
              card,
              node,
              meta.title,
              [meta.copy || sectionTitle, sectionAliases].filter(Boolean).join(' '),
              'control',
            );
            if (!entry) {
              return;
            }
            const dedupeKey = [entry.kind, entry.section, entry.title, entry.target.id || entry.target.name || entry.target.tagName, entry.copy].join('|');
            if (seen.has(dedupeKey)) {
              return;
            }
            seen.add(dedupeKey);
            entry.rankBias = Math.max(0, 140 - index);
            entries.push(entry);
          });
        });
        adminSearchIndex = entries;
        return entries;
      }

      function adminSearchScore(entry, query) {
        if (!entry || !query) {
          return -1;
        }
        if (entry.normalizedTitle === query) {
          return 1200 + (entry.rankBias || 0);
        }
        if (entry.normalizedTitle.startsWith(query)) {
          return 1080 + (entry.rankBias || 0);
        }
        if (entry.normalizedBody.startsWith(query)) {
          return 960 + (entry.rankBias || 0);
        }
        const titleIndex = entry.normalizedTitle.indexOf(query);
        if (titleIndex !== -1) {
          return 900 - Math.min(titleIndex, 240) + (entry.rankBias || 0);
        }
        const bodyIndex = entry.normalizedBody.indexOf(query);
        if (bodyIndex !== -1) {
          return 760 - Math.min(bodyIndex, 260) + (entry.rankBias || 0);
        }
        return -1;
      }

      function adminEscapeHtml(text) {
        return String(text || '').replace(/[&<>"]/g, (char) => ({
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
        }[char] || char));
      }

      function adminSearchSetExpanded(expanded, options = {}) {
        const panel = document.getElementById('admin-search-panel');
        const toggle = document.getElementById('admin-search-toggle');
        const card = document.getElementById('admin-search-card');
        if (!panel || !toggle || !card) {
          return;
        }
        panel.hidden = !expanded;
        toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        card.classList.toggle('expanded', expanded);
        if (options.manual) {
          adminSearchPinnedOpen = expanded;
        }
      }

      function adminSearchSyncTools(query) {
        const resetButton = document.getElementById('admin-search-reset');
        if (!resetButton) {
          return;
        }
        const hasQuery = Boolean(query);
        resetButton.hidden = !hasQuery;
      }

      function adminRenderSearchResults(query) {
        const resultsNode = document.getElementById('admin-search-results');
        if (!resultsNode) {
          return;
        }

        if (!query) {
          adminSearchResults = [];
          adminSearchSyncTools('');
          resultsNode.innerHTML = "<div class='admin-search-empty'>Start typing to jump to cards, field labels, buttons, selected values, and nested settings across the admin dashboard.</div>";
          return;
        }

        const ranked = adminSearchIndex
          .map((entry) => ({ entry, score: adminSearchScore(entry, query) }))
          .filter((entry) => entry.score >= 0)
          .sort((left, right) => right.score - left.score || left.entry.title.length - right.entry.title.length)
          .slice(0, 8);

        adminSearchResults = ranked.map((item) => item.entry);
        adminSearchSyncTools(query);

        if (!adminSearchResults.length) {
          resultsNode.innerHTML = "<div class='admin-search-empty'>No matching settings found. Try a section title, provider name, button label, field label, model name, voice, tunnel, or port.</div>";
          return;
        }

        resultsNode.innerHTML = adminSearchResults.map((entry, index) => `
          <button type="button" class="admin-search-result" data-search-result-index="${index}">
            <span class="admin-search-result-main">
              ${entry.section !== entry.title ? `<span class="admin-search-result-section">${adminEscapeHtml(entry.section)}</span>` : ''}
              <span class="admin-search-result-title">${adminEscapeHtml(entry.title)}</span>
            </span>
          </button>
        `).join('');
      }

      function adminRenderJumpChips() {
        const chipsNode = document.getElementById('admin-search-jumps');
        if (!chipsNode) {
          return;
        }
        const cards = adminGetTopCards().slice(0, 10);
        chipsNode.innerHTML = cards.map((card) => {
          const title = adminCardHeading(card);
          return `<button type="button" class="admin-jump-chip" data-admin-jump="${adminEscapeHtml(card.id)}">${adminEscapeHtml(title)}</button>`;
        }).join('');
      }

      function adminHighlightNode(node) {
        const highlightNode = adminFindTopCard(node) || node.closest('.card, .agent-editor-card, .wizard-mini-card, .speech-surface-card, .speech-summary-card, .local-model-card, .model-result-card, .setup-pill, .partner-card, .download-job, .wizard-choice-card') || node;
        if (!highlightNode) {
          return;
        }
        document.querySelectorAll('.admin-search-hit').forEach((existing) => existing.classList.remove('admin-search-hit'));
        highlightNode.classList.add('admin-search-hit');
        clearTimeout(adminSearchHighlightTimer);
        adminSearchHighlightTimer = window.setTimeout(() => {
          highlightNode.classList.remove('admin-search-hit');
        }, 1800);
      }

      function adminRevealNode(node) {
        if (!node) {
          return;
        }
        let current = node;
        while (current) {
          if (current.tagName === 'DETAILS') {
            current.open = true;
          }
          current = current.parentElement;
        }
        const providerPanel = node.closest('[id^="panel-"]');
        if (providerPanel && providerPanel.id && providerPanel.style.display === 'none' && typeof window.syncProviderPanels === 'function') {
          const providerName = providerPanel.id.replace(/^panel-/, '');
          window.syncProviderPanels(providerName);
        }
      }

      function adminPreferredScrollBehavior() {
        try {
          const prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
          const compactViewport = window.matchMedia && window.matchMedia('(max-width: 900px)').matches;
          const touchDevice = Number(navigator.maxTouchPoints || 0) > 0;
          if (prefersReducedMotion) {
            return 'auto';
          }
          return (compactViewport || touchDevice) ? 'instant' : 'smooth';
        } catch (error) {
          return 'instant';
        }
      }

      function adminScrollTargetIntoView(node, options = {}) {
        if (!node) {
          return null;
        }
        const scrollNode = adminFindTopCard(node) || node.closest('.card, .agent-editor-card, .wizard-mini-card, .speech-surface-card, .speech-summary-card, .local-model-card, .model-result-card, .setup-pill, .partner-card, .download-job, .wizard-choice-card') || node;
        const offset = Number(options.offset || document.getElementById('admin-search-card')?.offsetHeight || 18);
        const top = Math.max(0, window.scrollY + scrollNode.getBoundingClientRect().top - offset - 18);
        const behavior = options.behavior || adminPreferredScrollBehavior();
        if (behavior === 'instant') {
          window.scrollTo(0, top);
        } else {
          window.scrollTo({
            top,
            behavior,
            left: 0,
          });
        }
        return scrollNode;
      }

      function adminJumpToNode(node) {
        if (!node) {
          return;
        }
        adminRevealNode(node);
        window.requestAnimationFrame(() => {
          adminScrollTargetIntoView(node, { behavior: adminPreferredScrollBehavior() });
          window.setTimeout(() => {
            try {
              if (node.matches('input, select, textarea, button, summary')) {
                node.focus({ preventScroll: true });
              }
            } catch (error) {
              console.debug('Admin search focus skipped:', error);
            }
            adminScrollTargetIntoView(node, { behavior: 'instant' });
            adminHighlightNode(node);
          }, 260);
        });
      }

      function adminHandleSearchInput() {
        const input = document.getElementById('admin-settings-search');
        if (!input) {
          return;
        }
        const query = adminNormalizeSearch(input.value);
        clearTimeout(adminSearchRefreshTimer);
        adminSearchRefreshTimer = window.setTimeout(() => {
          adminBuildSearchIndex();
          adminRenderSearchResults(query);
        }, 100);
      }

      function initAdminSearch() {
        const input = document.getElementById('admin-settings-search');
        const resetButton = document.getElementById('admin-search-reset');
        const toggleButton = document.getElementById('admin-search-toggle');
        const iconButton = document.getElementById('admin-search-icon-btn');
        const resultsNode = document.getElementById('admin-search-results');
        const chipsNode = document.getElementById('admin-search-jumps');
        const commandCard = document.getElementById('admin-search-card');
        if (!input || !resetButton || !toggleButton || !resultsNode || !chipsNode) {
          return;
        }

        adminBuildSearchIndex();
        adminSearchSetExpanded(false);
        adminRenderSearchResults('');
        adminRenderJumpChips();
        adminSearchSyncTools('');

        input.addEventListener('input', adminHandleSearchInput);
        input.addEventListener('keydown', (event) => {
          if (event.key === 'Escape') {
            input.value = '';
            if (!adminSearchPinnedOpen) {
              adminSearchSetExpanded(false);
            }
            adminRenderSearchResults('');
          } else if (event.key === 'Enter' && adminSearchResults.length) {
            event.preventDefault();
            adminJumpToNode(adminSearchResults[0].target);
            if (!adminSearchPinnedOpen) {
              adminSearchSetExpanded(false);
            }
          }
        });

        if (iconButton) {
          iconButton.addEventListener('click', () => {
            input.focus();
            if (input.value.trim()) {
              const query = adminNormalizeSearch(input.value);
              if (adminSearchResults.length) {
                adminJumpToNode(adminSearchResults[0].target);
                if (!adminSearchPinnedOpen) adminSearchSetExpanded(false);
              } else {
                adminSearchSetExpanded(true, { manual: false });
                adminRenderSearchResults(query);
              }
            } else {
              adminSearchSetExpanded(true, { manual: false });
              adminRenderJumpChips();
            }
          });
        }
        resetButton.addEventListener('click', () => {
          input.value = '';
          input.focus();
          if (!adminSearchPinnedOpen) {
            adminSearchSetExpanded(false);
          }
          adminRenderSearchResults('');
        });

        toggleButton.addEventListener('click', () => {
          const expanded = toggleButton.getAttribute('aria-expanded') !== 'true';
          adminSearchSetExpanded(expanded, { manual: true });
          if (expanded) {
            adminRenderJumpChips();
            adminRenderSearchResults(adminNormalizeSearch(input.value));
          }
        });

        resultsNode.addEventListener('pointerdown', (event) => {
          const button = event.target.closest('[data-search-result-index]');
          if (!button) {
            return;
          }
          event.preventDefault();
          const index = Number(button.getAttribute('data-search-result-index'));
          const match = adminSearchResults[index];
          if (match) {
            if (!adminSearchPinnedOpen) {
              adminSearchSetExpanded(false);
            }
            adminJumpToNode(match.target);
          }
        });

        chipsNode.addEventListener('pointerdown', (event) => {
          const chip = event.target.closest('[data-admin-jump]');
          if (!chip) {
            return;
          }
          event.preventDefault();
          const target = document.getElementById(chip.getAttribute('data-admin-jump'));
          if (target) {
            if (!adminSearchPinnedOpen) {
              adminSearchSetExpanded(false);
            }
            adminJumpToNode(target);
          }
        });

        document.addEventListener('pointerdown', (event) => {
          if (!commandCard || commandCard.contains(event.target)) {
            return;
          }
          if (!adminSearchPinnedOpen) {
            adminSearchSetExpanded(false);
          }
        });
      }

      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAdminSearch);
      } else {
        initAdminSearch();
      }
    </script>
    """


def build_admin_navigation_script_html() -> str:
    return """
    <script>
      (() => {
        function getAllAdminNavLinks() {
          return Array.from(document.querySelectorAll('[data-admin-section-link]')).filter((link) => !link.hidden);
        }

        function getAdminNavTargets() {
          const seen = new Set();
          return getAllAdminNavLinks()
            .map((link) => {
              const targetId = link.getAttribute('data-admin-section-link');
              if (!targetId || seen.has(targetId)) {
                return null;
              }
              const target = document.getElementById(targetId);
              if (!target) {
                return null;
              }
              seen.add(targetId);
              return { link, target, targetId };
            })
            .filter(Boolean);
        }

        function getSectionsToggle() {
          return document.getElementById('admin-mobile-sections-toggle');
        }

        function getSectionsSheet() {
          return document.getElementById('admin-mobile-sections-sheet');
        }

        function getSectionsScroll() {
          return document.getElementById('admin-mobile-sections-scroll');
        }

        function getSectionsClose() {
          return document.getElementById('admin-mobile-sections-close');
        }

        function getSectionsBackdrop() {
          return document.getElementById('admin-mobile-sections-backdrop');
        }

        function adminNavigationScrollBehavior() {
          try {
            const prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            const compactViewport = window.matchMedia && window.matchMedia('(max-width: 900px)').matches;
            const touchDevice = Number(navigator.maxTouchPoints || 0) > 0;
            if (prefersReducedMotion) {
              return 'auto';
            }
            return (compactViewport || touchDevice) ? 'instant' : 'smooth';
          } catch (error) {
            return 'instant';
          }
        }

        function lockMobileBackgroundScroll() {
          const body = document.body;
          const root = document.documentElement;
          if (!body || body.dataset.adminMobileScrollLocked === '1') {
            return;
          }
          const scrollTop = Math.max(0, window.scrollY || window.pageYOffset || 0);
          body.dataset.adminMobileScrollLocked = '1';
          body.dataset.adminMobileScrollTop = String(scrollTop);
          root.classList.add('admin-mobile-sections-open');
          body.classList.add('admin-mobile-sections-open');
          body.style.position = 'fixed';
          body.style.top = `-${scrollTop}px`;
          body.style.left = '0';
          body.style.right = '0';
          body.style.width = '100%';
        }

        function unlockMobileBackgroundScroll() {
          const body = document.body;
          const root = document.documentElement;
          if (!body) {
            return;
          }
          if (body.dataset.adminMobileScrollLocked !== '1') {
            root.classList.remove('admin-mobile-sections-open');
            body.classList.remove('admin-mobile-sections-open');
            return;
          }
          const scrollTop = Number(body.dataset.adminMobileScrollTop || 0) || 0;
          delete body.dataset.adminMobileScrollLocked;
          delete body.dataset.adminMobileScrollTop;
          root.classList.remove('admin-mobile-sections-open');
          body.classList.remove('admin-mobile-sections-open');
          body.style.position = '';
          body.style.top = '';
          body.style.left = '';
          body.style.right = '';
          body.style.width = '';
          window.scrollTo(0, scrollTop);
        }

        function setActiveLink(activeId) {
          getAllAdminNavLinks().forEach((link) => {
            const isActive = link.getAttribute('data-admin-section-link') === activeId;
            link.classList.toggle('active', isActive);
            if (isActive) {
              link.setAttribute('aria-current', 'true');
              if (link.offsetParent !== null) {
                try {
                  link.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: adminNavigationScrollBehavior() });
                } catch (error) {
                  console.debug('Nav scroll skipped:', error);
                }
              }
            } else {
              link.removeAttribute('aria-current');
            }
          });
        }

        function setSectionsOpen(open, options = {}) {
          const toggle = getSectionsToggle();
          const sheet = getSectionsSheet();
          if (!toggle || !sheet) {
            return;
          }
          const nextOpen = Boolean(open);
          const focusTarget = options && options.focusTarget ? options.focusTarget : null;
          if (nextOpen) {
            lockMobileBackgroundScroll();
          }
          sheet.hidden = !nextOpen;
          if (!nextOpen) {
            unlockMobileBackgroundScroll();
          }
          toggle.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
          toggle.classList.toggle('is-open', nextOpen);
          toggle.setAttribute('aria-label', nextOpen ? 'Close sections navigator' : 'Open sections navigator');
          toggle.setAttribute('title', nextOpen ? 'Close sections navigator' : 'Open sections navigator');
          if (nextOpen) {
            const scroll = getSectionsScroll();
            const active = sheet.querySelector('.admin-nav-link.active') || sheet.querySelector('.admin-nav-link');
            if (scroll && active) {
              try {
                active.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: adminNavigationScrollBehavior() });
              } catch (error) {
                console.debug('Mobile sections scroll skipped:', error);
              }
            }
          } else if (focusTarget) {
            window.requestAnimationFrame(() => {
              window.requestAnimationFrame(() => jumpToAdminSection(focusTarget));
            });
          }
        }

        function adminViewportAnchorOffset() {
          const commandCard = document.getElementById('admin-search-card');
          const commandRect = commandCard ? commandCard.getBoundingClientRect() : null;
          return Math.max(0, commandRect ? commandRect.bottom : 0) + 14;
        }

        function refreshActiveLinkFromViewport(targets) {
          const navTargets = Array.isArray(targets) && targets.length ? targets : getAdminNavTargets();
          if (!navTargets.length) {
            return;
          }
          const anchor = adminViewportAnchorOffset();
          let current = null;
          navTargets.forEach(({ target, targetId }) => {
            const rect = target.getBoundingClientRect();
            if (rect.top <= anchor && rect.bottom > anchor) {
              if (!current || rect.top > current.top) {
                current = { targetId, top: rect.top };
              }
            }
          });
          if (current) {
            setActiveLink(current.targetId);
            return;
          }
          const nearest = navTargets
            .map(({ target, targetId }) => ({
              targetId,
              top: target.getBoundingClientRect().top,
            }))
            .filter((item) => item.top > -window.innerHeight)
            .sort((left, right) => Math.abs(left.top - anchor) - Math.abs(right.top - anchor))[0];
          if (nearest) {
            setActiveLink(nearest.targetId);
          }
        }

        function jumpToAdminSection(target) {
          if (!target) {
            return;
          }
          try {
            if (document.activeElement && typeof document.activeElement.blur === 'function') {
              document.activeElement.blur();
            }
          } catch (error) {
            console.debug('Active element blur skipped:', error);
          }
          const offset = Math.max(24, adminViewportAnchorOffset() - 8);
          const scrollToTarget = (behavior) => {
            target.scrollIntoView({ block: 'start', inline: 'nearest', behavior: behavior === 'instant' ? 'auto' : behavior });
            const top = Math.max(0, window.scrollY + target.getBoundingClientRect().top - offset);
            if (behavior === 'instant') {
              window.scrollTo(0, top);
            } else {
              window.scrollTo({ top, behavior });
            }
          };
          window.requestAnimationFrame(() => {
            scrollToTarget(adminNavigationScrollBehavior());
            window.setTimeout(() => scrollToTarget('instant'), 90);
            window.setTimeout(() => refreshActiveLinkFromViewport(), 180);
          });
          target.classList.add('admin-search-hit');
          window.setTimeout(() => target.classList.remove('admin-search-hit'), 1600);
        }

        function openWizardOverlay() {
          const overlay = document.getElementById('setup-wizard-overlay');
          if (!overlay) {
            return;
          }
          overlay.style.display = '';
          if (typeof window.showWizardStep === 'function') {
            try {
              window.showWizardStep(Number(window.autoyouWizardStep || 0));
            } catch (error) {
              console.debug('Wizard launch skipped:', error);
            }
          }
          overlay.scrollIntoView({ behavior: adminNavigationScrollBehavior(), block: 'start' });
        }

        function initAdminNavigation() {
          const sectionsToggle = getSectionsToggle();
          const sectionsClose = getSectionsClose();
          const sectionsBackdrop = getSectionsBackdrop();
          const targets = getAdminNavTargets();
          if (sectionsToggle) {
            sectionsToggle.addEventListener('click', () => {
              const nextOpen = sectionsToggle.getAttribute('aria-expanded') !== 'true';
              setSectionsOpen(nextOpen);
            });
          }
          if (sectionsClose) {
            sectionsClose.addEventListener('click', () => setSectionsOpen(false));
          }
          if (sectionsBackdrop) {
            sectionsBackdrop.addEventListener('click', () => setSectionsOpen(false));
          }
          document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
              setSectionsOpen(false);
            }
          });

          if (!targets.length) {
            return;
          }

          getAllAdminNavLinks().forEach((link) => {
            link.addEventListener('click', (event) => {
              const targetId = link.getAttribute('data-admin-section-link');
              const target = targetId ? document.getElementById(targetId) : null;
              if (!target) {
                return;
              }
              event.preventDefault();
              try {
                link.blur();
              } catch (error) {
                console.debug('Nav blur skipped:', error);
              }
              setActiveLink(targetId);
              setSectionsOpen(false, { focusTarget: target });
            });
          });

          document.querySelectorAll('[data-admin-open-wizard]').forEach((button) => {
            button.addEventListener('click', openWizardOverlay);
          });

          let refreshPending = false;
          const queueViewportRefresh = () => {
            if (refreshPending) {
              return;
            }
            refreshPending = true;
            window.requestAnimationFrame(() => {
              refreshPending = false;
              refreshActiveLinkFromViewport(targets);
            });
          };

          window.addEventListener('scroll', queueViewportRefresh, { passive: true });
          window.addEventListener('resize', queueViewportRefresh, { passive: true });
          queueViewportRefresh();
        }

        if (document.readyState === 'loading') {
          document.addEventListener('DOMContentLoaded', initAdminNavigation);
        } else {
          initAdminNavigation();
        }
      })();
    </script>
    """
