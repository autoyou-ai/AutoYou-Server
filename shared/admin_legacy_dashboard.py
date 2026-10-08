# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-fifteenpercent-810759384cd5a7ceb91a63e8

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from typing import Any, Mapping

__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-fifteenpercent-810759384cd5a7ceb91a63e8"


def bind_dashboard_dependencies(dependencies: Mapping[str, Any]) -> None:
    """Bind server runtime dependencies used by the legacy dashboard renderer.

    The long-page dashboard is intentionally isolated from server.py, but it
    still reads live runtime objects and helper functions until those surfaces
    are decomposed into narrower view models.
    """
    protected = {
        "Any",
        "Mapping",
        "bind_dashboard_dependencies",
        "build_legacy_dashboard_html",
    }
    for name, value in dependencies.items():
        if name == "__file__":
            globals()[name] = value
            continue
        if name.startswith("__") or name in protected:
            continue
        globals()[name] = value


_QR_PROVISION_SCRIPT_HTML = """<script>
(function() {
  var _qrCountdownTimer = null;

  window.showQrProvisionConfirm = function() {
    document.getElementById('qr-provision-confirm-modal').style.display = 'flex';
  };
  window.hideQrProvisionConfirm = function() {
    document.getElementById('qr-provision-confirm-modal').style.display = 'none';
  };
  window.closeQrProvisionDisplay = function() {
    document.getElementById('qr-provision-display-modal').style.display = 'none';
    if (_qrCountdownTimer) { clearInterval(_qrCountdownTimer); _qrCountdownTimer = null; }
  };

  window.generateProvisionQr = function() {
    hideQrProvisionConfirm();
    fetch('/api/settings/export-qr', {method:'GET', credentials:'same-origin'})
      .then(function(r){ return r.json(); })
      .then(function(data) {
        if (!data.qr_url) { alert('Failed to generate QR: ' + (data.error || 'Unknown error')); return; }
        document.getElementById('qr-provision-img').src = data.qr_url;
        document.getElementById('qr-provision-display-modal').style.display = 'flex';
        var expiresAt = Date.now() + 5 * 60 * 1000;
        if (_qrCountdownTimer) clearInterval(_qrCountdownTimer);
        _qrCountdownTimer = setInterval(function() {
          var rem = Math.max(0, Math.round((expiresAt - Date.now()) / 1000));
          var m = Math.floor(rem / 60), s = rem % 60;
          var el = document.getElementById('qr-provision-countdown');
          if (el) el.textContent = 'Expires in ' + m + ':' + (s < 10 ? '0' : '') + s;
          if (rem === 0) {
            clearInterval(_qrCountdownTimer);
            closeQrProvisionDisplay();
            alert('The provision QR has expired. Please generate a new one.');
          }
        }, 1000);
      })
      .catch(function(e){ alert('Network error: ' + e); });
  };
})();
</script>"""


def _build_jailbreak_card_html() -> str:
    """Build the Prompt Override admin UI card HTML.

    The card is always shown at the bottom of the dashboard.  Its appearance
    changes depending on whether Prompt Override is currently active.
    """
    active = is_jailbreak_active(anchor=__file__)
    jailbreak_dir = get_jailbreak_data_dir(anchor=__file__)
    compiled = is_compiled()

    active_badge = (
        "<span style='color:#ef4444;font-weight:700;font-size:12px;padding:2px 8px;"
        "border:1px solid #ef4444;border-radius:4px;'>ACTIVE</span>"
        if active
        else "<span style='color:#6b7280;font-weight:700;font-size:12px;padding:2px 8px;"
        "border:1px solid #374151;border-radius:4px;'>INACTIVE</span>"
    )

    # Prompt editor shown only when Prompt Override is active
    prompt_editor_html = ""
    if active:
        prompt_file = jailbreak_dir / JAILBREAK_ROOT_PROMPT_FILENAME
        try:
            from shared.secure_storage import SecureStorageError, read_secure_file

            prompt_bytes = read_secure_file(prompt_file) if prompt_file.exists() else b""
            prompt_text = prompt_bytes.decode("utf-8")
        except SecureStorageError:
            raise
        except Exception:
            prompt_text = ""
        current_prompt = html.escape(
            prompt_text,
            quote=True,
        )
        prompt_editor_html = f"""
      <div id='jailbreak-prompt-section' style='margin-top:18px;'>
        <label style='font-weight:600;display:block;margin-bottom:6px;color:#f59e0b;'>
          Root Agent Override Prompt
        </label>
        <p class='muted' style='margin:0 0 10px 0;font-size:13px;'>
          This prompt overrides the compiled root agent instructions at server startup.
          Leave blank to use the default compiled instructions.
          Changes take effect after restarting the AI agent runtime.
        </p>
        <textarea id='jailbreak-prompt-textarea'
          style='width:100%;min-height:180px;font-family:monospace;font-size:13px;
                 background:#0b1321;color:#e2e8f0;border:1px solid #7c3aed;border-radius:8px;
                 padding:12px;resize:vertical;' rows='10'>{current_prompt}</textarea>
        <div style='display:flex;gap:10px;margin-top:10px;align-items:center;'>
          <button type='button' onclick='saveJailbreakPrompt()'
            style='background:#7c3aed;color:#fff;border:none;padding:8px 18px;
                   border-radius:6px;cursor:pointer;font-weight:600;'>
            Save Prompt
          </button>
          <button type='button' onclick='deactivateJailbreak()'
            style='background:transparent;color:#ef4444;border:1px solid #ef4444;
                   padding:8px 18px;border-radius:6px;cursor:pointer;font-weight:600;'>
            Deactivate Override
          </button>
          <span id='jailbreak-save-status' class='muted' style='font-size:13px;'></span>
        </div>
      </div>"""

    # Activation form shown when not yet active
    activation_form_html = ""
    if not active:
        activation_form_html = f"""
      <details style='margin-top:16px;'>
        <summary style='cursor:pointer;color:#f59e0b;font-weight:600;font-size:14px;
                        user-select:none;padding:6px 0;'>
          Enable Prompt Override (click to expand)
        </summary>
        <div style='margin-top:14px;padding:16px;border:1px solid #7c3aed;
                    border-radius:10px;background:rgba(124,58,237,0.07);'>
          <p style='color:#f59e0b;font-weight:600;margin:0 0 10px 0;'>⚠️ Warning - Read carefully</p>
          <ul style='color:#94a3b8;font-size:13px;line-height:1.7;margin:0 0 14px 0;padding-left:18px;'>
            <li>Prompt Override lets you edit the root agent instructions used by this AutoYou runtime.</li>
            <li>Any custom prompt you enter takes <strong>full precedence</strong> over the built-in
                safety guidelines and behavioural guardrails.</li>
            <li>You are solely responsible for the agent's behaviour while Prompt Override is active.</li>
            <li>AutoYou, OpenStorey LLC, the AutoYou owner board, and contributors
                disclaim warranties and limit liability to the maximum extent permitted by law
                for misuse or unintended behaviour caused by custom prompts.</li>
            <li>The acknowledgement is stored locally on this device.</li>
          </ul>
          <label style='display:flex;align-items:flex-start;gap:10px;cursor:pointer;
                         color:#e2e8f0;font-size:14px;margin-bottom:14px;'>
            <input type='checkbox' id='jailbreak-ack-checkbox'
              style='margin-top:3px;accent-color:#7c3aed;width:16px;height:16px;flex-shrink:0;'>
            I have read and understood the above warnings. I accept full responsibility for
            any behaviour changes caused by custom root agent prompts.
          </label>
          <button type='button' id='jailbreak-activate-btn'
            onclick='activateJailbreak()'
            disabled
            style='background:#7c3aed;color:#fff;border:none;padding:9px 22px;
                   border-radius:6px;cursor:pointer;font-weight:700;opacity:0.5;'>
            Enable Prompt Override
          </button>
          <span id='jailbreak-activate-status' class='muted' style='font-size:13px;margin-left:12px;'></span>
        </div>
      </details>"""

    jailbreak_script = """
    <script>
      (function() {
        // Wire up checkbox → enable button
        var ackBox = document.getElementById('jailbreak-ack-checkbox');
        var activateBtn = document.getElementById('jailbreak-activate-btn');
        if (ackBox && activateBtn) {
          ackBox.addEventListener('change', function() {
            activateBtn.disabled = !ackBox.checked;
            activateBtn.style.opacity = ackBox.checked ? '1' : '0.5';
            activateBtn.style.cursor = ackBox.checked ? 'pointer' : 'not-allowed';
          });
        }
      })();

      async function activateJailbreak() {
        var status = document.getElementById('jailbreak-activate-status');
        try {
          var resp = await fetch('/api/jailbreak/activate', {method:'POST', credentials:'same-origin'});
          var data = await resp.json();
          if (data.success) {
            if (status) status.textContent = 'Prompt Override enabled. Reloading...';
            setTimeout(function(){ window.location.reload(); }, 1200);
          } else {
            if (status) status.textContent = 'Error: ' + (data.error || 'unknown');
          }
        } catch(e) {
          if (status) status.textContent = 'Error: ' + e.message;
        }
      }

      async function deactivateJailbreak() {
        if (!confirm('Disable Prompt Override? The custom prompt will be kept on disk but will no longer be loaded.')) return;
        var status = document.getElementById('jailbreak-save-status');
        try {
          var resp = await fetch('/api/jailbreak/deactivate', {method:'POST', credentials:'same-origin'});
          var data = await resp.json();
          if (data.success) {
            if (status) status.textContent = 'Deactivated. Reloading...';
            setTimeout(function(){ window.location.reload(); }, 1200);
          } else {
            if (status) status.textContent = 'Error: ' + (data.error || 'unknown');
          }
        } catch(e) {
          if (status) status.textContent = 'Error: ' + e.message;
        }
      }

      async function saveJailbreakPrompt() {
        var textarea = document.getElementById('jailbreak-prompt-textarea');
        var status = document.getElementById('jailbreak-save-status');
        if (!textarea) return;
        try {
          if (status) status.textContent = 'Saving...';
          var resp = await fetch('/api/jailbreak/prompt', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({prompt: textarea.value}),
          });
          var data = await resp.json();
          if (data.success) {
            if (status) status.textContent = '✓ Saved. Restart AutoYou AI to apply.';
          } else {
            if (status) status.textContent = 'Error: ' + (data.error || 'unknown');
          }
        } catch(e) {
          if (status) status.textContent = 'Error: ' + e.message;
        }
      }
    </script>"""

    return f"""
    <div class='card' id='jailbreak-settings-card'
         style='border-color:{"rgba(124,58,237,0.5)" if active else "rgba(51,65,85,0.7)"};'>
      <div style='display:flex;align-items:center;gap:10px;margin-bottom:8px;'>
        <h2 style='margin:0;'>⚠️ Prompt Override</h2>
        {active_badge}
        {"<span style='color:#6b7280;font-size:12px;'>(packaged build)</span>" if compiled else ""}
      </div>
      <p class='muted' style='margin:0 0 6px 0;font-size:14px;'>
        Allows advanced editing of the root agent instructions at your own risk.
        {"A custom root prompt override is active and will be loaded on next AutoYou AI restart." if active else
         ("When inactive, agent instructions are read-only (compiled into the binary)." if compiled else
          "Python launcher mode keeps these files scoped to the current workspace so they do not reuse the packaged app's overrides.")}
      </p>
      <p class='muted' style='margin:0;font-size:12px;'>
        Prompt override storage directory: <code class='mono'>{html.escape(str(jailbreak_dir))}</code>
      </p>
      {prompt_editor_html}
      {activation_form_html}
      {jailbreak_script}
    </div>"""


def _build_help_guides_card_html() -> str:
    guide_links = [
        ("/guides/bootstrap", "Bootstrap Guide", "First-run setup, staging workflow, and the main desktop bootstrap path."),
        ("/guides/connectivity", "Connectivity Guide", "Public links, connection helpers, and remote access patterns."),
        ("/guides/ollama-install", "Ollama Install", "Local model setup for the default privacy-first runtime."),
        ("/guides/speech", "Speech Guide", "System voices, cloud TTS providers, and STT model downloads."),
        ("/guides/telegram", "Telegram Setup", "Pair the Telegram bot and keep access limited to approved users."),
        ("/guides/signal", "Signal Pairing", "Signal QR pairing and recovery steps for the local Docker-backed service."),
        ("/guides/whatsapp", "WhatsApp Pairing", "WhatsApp QR setup through the repository-native runtime."),
    ]
    faq_items = [
        (
            "When do I need connection helpers?",
            "<p>Start with the built-in browser tunnel. Add connection helpers only when remote clients can sign in but calls or live sessions still cannot connect, especially on mobile data, hotel Wi-Fi, corporate networks, or strict home routers.</p>"
            "<p>The <a href='/guides/connectivity' target='_blank'>Connectivity Guide</a> explains the safe order: browser tunnel first, then add connection helpers only if direct pairing still fails.</p>",
        ),
        (
            "Why is the Signal service not running?",
            "<p>The most common reason is that Docker is not running, because the Signal bridge depends on the local Docker runtime. It can also stay down when Signal is disabled in the admin settings, the configured port is already in use, or the container needs to be paired again.</p>"
            "<p>Check the <a href='#signal-settings-card'>Signal section</a>, confirm Docker Desktop or your local Docker daemon is up, then reopen the Signal QR flow if the device pairing went stale.</p>",
        ),
        (
            "What should I check if remote pairing works locally but not off-device?",
            "<p>Confirm the public tunnel URL is reachable, verify your connection helper list, and make sure the client is not trying to reuse an expired one-time code. Local same-machine tests can pass even when remote calls still need an added helper.</p>"
            "<p>If the admin page is on port 8001 and the remote browser view is on another local port, that is expected. The client-side browser port should stay separate from the admin UI.</p>",
        ),
        (
            "Why does the public link start but the remote browser does not appear?",
            "<p>Usually the OTP expired, the pairing was single-use and already consumed, or the client connected but the browser proxy was opened on a different local port than you expected. That separate client browser port is normal and should not match the admin port.</p>"
            "<p>Refresh the pairing from the server, confirm the browser tunnel is enabled, and retry with a fresh client session.</p>",
        ),
        (
            "Which ports matter most during setup?",
            "<p>The main admin UI is typically on port 8001, the AI agent runtime is on 8081, and the auth service is kept separate. Client browser proxy ports should be different from the admin UI and can safely live alongside it.</p>"
            "<p>Use the runtime cards in the dashboard to confirm the current values before exposing the server beyond localhost.</p>",
        ),
    ]

    guide_markup = "".join(
        (
            f"<a class='help-guide-link' href='{html.escape(path, quote=True)}' target='_blank'>"
            f"<strong>{html.escape(title)}</strong>"
            f"<span>{html.escape(description)}</span>"
            f"</a>"
        )
        for path, title, description in guide_links
    )
    faq_markup = "".join(
        (
            f"<details class='help-faq-item'>"
            f"<summary>{html.escape(question)}</summary>"
            f"<div class='help-faq-answer'>{answer_html}</div>"
            f"</details>"
        )
        for question, answer_html in faq_items
    )

    return f"""
    <div class='card' id='help-guides-card'>
      <div class='help-card-grid'>
        <div>
          <div class='wizard-kicker'>Help</div>
          <h2 style='margin-top:12px'>Guides, setup notes, and troubleshooting</h2>
          <p class='muted'>Keep the heavyweight packaging guides out of the main dashboard, while keeping everyday setup links and practical troubleshooting close to the sections they support.</p>
        </div>
        <div class='help-guide-grid'>
          {guide_markup}
        </div>
        <section class='help-faq-shell'>
          <div class='help-faq-head'>
            <div>
              <h3>System FAQ</h3>
              <p>Expand a single answer, or open the whole troubleshooting set when you are diagnosing pairing, Signal, or remote-connection issues.</p>
            </div>
            <div class='help-faq-actions'>
              <button type='button' class='admin-feature-btn alt' data-no-loading='1' onclick='toggleHelpFaqs(true)'>Expand All</button>
              <button type='button' class='admin-feature-btn ghost' data-no-loading='1' onclick='toggleHelpFaqs(false)'>Collapse All</button>
            </div>
          </div>
          <div id='help-faq-list' class='help-faq-list'>
            {faq_markup}
          </div>
        </section>
      </div>
      <script>
        function toggleHelpFaqs(expand) {{
          document.querySelectorAll('#help-faq-list details').forEach((item) => {{
            item.open = !!expand;
          }});
        }}
      </script>
    </div>
    """


def _build_agent_studio_css() -> str:
    return textwrap.dedent(
        """
        .agent-studio-shell{display:grid;grid-template-columns:minmax(300px,340px) minmax(0,1fr);gap:18px;align-items:start;min-width:0}
        .agent-studio-sidebar,.agent-studio-main{display:grid;gap:16px;min-width:0}
        .agent-studio-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap}
        .agent-studio-head h3{margin:0;color:#f8fafc}
        .agent-studio-head p{margin:4px 0 0;color:#9fb4cf;line-height:1.65;max-width:62ch}
        .agent-studio-head-actions,.agent-studio-runtime-strip,.agent-studio-selection-badges,.agent-studio-selection-actions,.agent-studio-owner-meta,.agent-studio-form-actions,.agent-studio-tabbar,.agent-studio-live-actions,.agent-studio-summary-grid{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
        .agent-studio-runtime-strip{margin-top:14px}
        .agent-studio-banner{margin:14px 0 0;padding:12px 14px;border-radius:14px;border:1px solid rgba(96,165,250,.22);background:rgba(59,130,246,.08);color:var(--text)}
        .agent-studio-sidebar .agent-card-shell,.agent-studio-main .agent-card-shell,.agent-studio-sidebar .agent-builder-scaffold{min-width:0}
        .agent-studio-summary-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
        .agent-studio-stat-card{padding:14px;border-radius:16px;border:1px solid rgba(71,85,105,.7);background:rgba(8,15,28,.72)}
        .agent-studio-stat-card strong{display:block;color:#f8fafc;font-size:.92rem}
        .agent-studio-stat-card span{display:block;margin-top:4px;color:#9fb4cf;font-size:.84rem;line-height:1.55}
        .agent-studio-search{width:100%;min-height:46px;padding:12px 14px;border-radius:14px;border:1px solid #27384f;background:#06101d;color:#eef6ff;box-sizing:border-box;box-shadow:inset 0 1px 0 rgba(148,163,184,.04)}
        .agent-studio-search::placeholder{color:#6d839f}
        .agent-studio-list{display:grid;gap:10px;margin-top:14px}
        .agent-studio-list-item{width:100%;padding:14px;border-radius:18px;border:1px solid rgba(51,65,85,.72);background:linear-gradient(180deg,rgba(7,14,27,.94),rgba(5,11,21,.9));text-align:left;color:inherit;cursor:pointer;transition:transform .16s ease,border-color .16s ease,box-shadow .16s ease}
        .agent-studio-list-item:hover{transform:translateY(-1px);border-color:rgba(56,189,248,.28);box-shadow:0 12px 24px rgba(2,6,23,.18)}
        .agent-studio-list-item.is-active{border-color:rgba(56,189,248,.42);box-shadow:0 0 0 1px rgba(56,189,248,.18),0 16px 26px rgba(2,6,23,.22)}
        .agent-studio-list-top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
        .agent-studio-list-title{display:grid;gap:4px;min-width:0}
        .agent-studio-list-title strong{color:#f8fafc;font-size:.96rem;overflow-wrap:anywhere}
        .agent-studio-list-title code{display:block;max-width:100%;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}
        .agent-studio-list-title span,.agent-studio-list-copy,.agent-studio-path-grid,.agent-studio-owner-card span,.agent-studio-empty,.agent-studio-note,.agent-studio-inline-copy{color:#9fb4cf;line-height:1.55}
        .agent-studio-list-copy{margin:10px 0 0;overflow-wrap:anywhere}
        .agent-studio-list-meta{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px}
        .agent-studio-empty,.agent-studio-note{padding:14px;border-radius:16px;border:1px dashed rgba(71,85,105,.72);background:rgba(8,15,28,.54)}
        .agent-studio-selection-shell{display:grid;gap:16px}
        .agent-studio-selection-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}
        .agent-studio-selection-copy{display:grid;gap:8px;min-width:0}
        .agent-studio-selection-copy h3{margin:0;color:#f8fafc;overflow-wrap:anywhere}
        .agent-studio-selection-copy p{margin:0;color:#9fb4cf;line-height:1.65;overflow-wrap:anywhere}
        .agent-studio-selection-actions{justify-content:flex-end}
        .agent-studio-selection-meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;font-size:.86rem}
        .agent-studio-meta-row{display:grid;gap:6px;min-width:0;padding:12px 14px;border-radius:16px;border:1px solid rgba(51,65,85,.7);background:rgba(8,15,28,.62)}
        .agent-studio-meta-row strong{display:block;color:#8ea5c2;font-size:.73rem;letter-spacing:.04em;text-transform:uppercase}
        .agent-studio-meta-row span{color:#dce9f8;line-height:1.55;overflow-wrap:anywhere;word-break:break-word}
        .agent-studio-selection-meta code,.agent-studio-inline-code,.agent-studio-file-list code,.agent-studio-path-grid code,.agent-studio-meta-row code{font-family:Consolas,"Cascadia Code","SFMono-Regular",monospace;display:block;max-width:100%;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}
        .agent-studio-tabbar{margin-top:-2px}
        .agent-studio-tab{min-height:42px}
        .agent-studio-panel-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
        .agent-studio-panel-card,.agent-studio-owner-card,.agent-studio-status-block{padding:16px;border-radius:18px;border:1px solid rgba(51,65,85,.72);background:linear-gradient(180deg,rgba(5,12,23,.94),rgba(7,16,30,.9));display:grid;gap:10px;min-width:0}
        .agent-studio-panel-card strong,.agent-studio-owner-card strong,.agent-studio-status-block strong{color:#f8fafc}
        .agent-studio-owner-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
        .agent-studio-owner-card small{color:#8ea5c2}
        .agent-studio-codeblock{margin-top:12px;padding:14px;border-radius:16px;border:1px solid rgba(51,65,85,.72);background:#06101d;color:#dce9f8;font-family:Consolas,"Cascadia Code","SFMono-Regular",monospace;font-size:.84rem;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}
        .agent-studio-field-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
        .agent-studio-field-grid > *, .agent-studio-panel-grid > *{min-width:0}
        .agent-studio-field label{display:block;margin-bottom:6px;color:#eef6ff;font-weight:700;font-size:.85rem}
        .agent-studio-field label span{color:#8ea5c2;font-weight:500}
        .agent-studio-field input,.agent-studio-field textarea,.agent-studio-field select{width:100%;min-width:0;padding:12px 14px;border-radius:14px;border:1px solid #27384f;background:#06101d;color:#eef6ff;box-sizing:border-box;box-shadow:inset 0 1px 0 rgba(148,163,184,.04)}
        .agent-studio-field textarea{min-height:180px;line-height:1.65;resize:vertical;font-family:Consolas,"Cascadia Code","SFMono-Regular",monospace}
        .agent-studio-field textarea.readonly{background:#0b1625;color:#a9bdd6}
        .agent-studio-field small{display:block;margin-top:6px;color:#8ea5c2;line-height:1.55}
        .agent-studio-panel-actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
        .agent-studio-path-grid{display:grid;gap:6px;font-size:.84rem;overflow-wrap:anywhere;word-break:break-word}
        .agent-studio-file-list{display:grid;gap:8px;max-height:260px;overflow:auto;padding-right:2px}
        .agent-studio-file-item{padding:10px 12px;border-radius:14px;border:1px solid rgba(51,65,85,.68);background:rgba(8,15,28,.68);color:#dce9f8;font-size:.84rem;overflow-wrap:anywhere;word-break:break-word}
        .agent-studio-checkbox{display:flex;gap:10px;align-items:flex-start;color:#dce9f8}
        .agent-studio-checkbox input{margin-top:4px}
        .agent-studio-checkbox span{line-height:1.5}
        #agent-studio-card,.agent-studio-sidebar section,.agent-studio-main section,.agent-studio-selection-shell,.agent-studio-selection-meta,.agent-studio-status-block,.agent-studio-panel-card,.agent-studio-path-grid{min-width:0}
        :root[data-theme="light"] .agent-studio-head h3,
        :root[data-theme="light"] .agent-studio-list-title strong,
        :root[data-theme="light"] .agent-studio-selection-copy h3,
        :root[data-theme="light"] .agent-studio-panel-card strong,
        :root[data-theme="light"] .agent-studio-owner-card strong,
        :root[data-theme="light"] .agent-studio-status-block strong,
        :root[data-theme="light"] .agent-studio-stat-card strong,
        :root[data-theme="light"] .agent-studio-field label{color:#111827}
        :root[data-theme="light"] .agent-studio-head p,
        :root[data-theme="light"] .agent-studio-list-title span,
        :root[data-theme="light"] .agent-studio-list-copy,
        :root[data-theme="light"] .agent-studio-selection-copy p,
        :root[data-theme="light"] .agent-studio-selection-meta,
        :root[data-theme="light"] .agent-studio-note,
        :root[data-theme="light"] .agent-studio-empty,
        :root[data-theme="light"] .agent-studio-inline-copy,
        :root[data-theme="light"] .agent-studio-path-grid,
        :root[data-theme="light"] .agent-studio-owner-card span,
        :root[data-theme="light"] .agent-studio-owner-card small,
        :root[data-theme="light"] .agent-studio-stat-card span,
        :root[data-theme="light"] .agent-studio-field small{color:#334155}
        :root[data-theme="light"] .agent-studio-banner,
        :root[data-theme="light"] .agent-studio-stat-card,
        :root[data-theme="light"] .agent-studio-list-item,
        :root[data-theme="light"] .agent-studio-panel-card,
        :root[data-theme="light"] .agent-studio-owner-card,
        :root[data-theme="light"] .agent-studio-status-block,
        :root[data-theme="light"] .agent-studio-file-item,
        :root[data-theme="light"] .agent-studio-empty,
        :root[data-theme="light"] .agent-studio-note{background:linear-gradient(180deg,#ffffff,#f8fafc);border-color:#dbe3ee;box-shadow:0 12px 24px rgba(148,163,184,.1)}
        :root[data-theme="light"] .agent-studio-list-item.is-active{background:linear-gradient(180deg,#eff6ff,#dbeafe);border-color:#93c5fd}
        :root[data-theme="light"] .agent-studio-search,
        :root[data-theme="light"] .agent-studio-field input,
        :root[data-theme="light"] .agent-studio-field textarea,
        :root[data-theme="light"] .agent-studio-field select{background:#ffffff;border-color:#cbd5e1;color:#111827;box-shadow:inset 0 1px 0 rgba(255,255,255,.88)}
        :root[data-theme="light"] .agent-studio-field textarea.readonly,
        :root[data-theme="light"] .agent-studio-codeblock{background:#f8fafc;border-color:#dbe3ee;color:#111827}
        :root[data-theme="light"] .agent-studio-checkbox{color:#111827}
        :root[data-theme="light"] .agent-studio-meta-row{background:linear-gradient(180deg,#ffffff,#f8fafc);border-color:#dbe3ee;box-shadow:0 10px 20px rgba(148,163,184,.08)}
        :root[data-theme="light"] .agent-studio-meta-row strong{color:#475569}
        :root[data-theme="light"] .agent-studio-meta-row span,
        :root[data-theme="light"] .agent-studio-meta-row code{color:#111827}
        @media (max-width: 1320px){
          .agent-studio-shell{grid-template-columns:1fr}
        }
        @media (max-width: 1040px){
          .agent-studio-selection-head{flex-direction:column;align-items:stretch}
          .agent-studio-selection-actions{justify-content:flex-start}
        }
        @media (max-width: 920px){
          .agent-studio-panel-grid,
          .agent-studio-field-grid,
          .agent-studio-summary-grid,
          .agent-studio-owner-grid{grid-template-columns:1fr}
        }
        @media (max-width: 720px){
          #agent-studio-card{padding:14px !important}
          .agent-studio-head-actions,
          .agent-studio-runtime-strip,
          .agent-studio-selection-actions,
          .agent-studio-form-actions,
          .agent-studio-tabbar,
          .agent-studio-live-actions{flex-direction:column;align-items:stretch}
          .agent-studio-selection-meta{grid-template-columns:1fr}
          .agent-studio-tabbar .admin-feature-btn,
          .agent-studio-selection-actions .admin-feature-btn,
          .agent-studio-form-actions .admin-feature-btn,
          .agent-studio-live-actions .admin-feature-btn{width:100%}
        }
        @media (max-width: 560px){
          .agent-studio-stat-card,
          .agent-studio-panel-card,
          .agent-studio-owner-card,
          .agent-studio-status-block,
          .agent-studio-meta-row{padding:13px}
          .agent-studio-list-item{padding:12px}
          .agent-studio-search,
          .agent-studio-field input,
          .agent-studio-field textarea,
          .agent-studio-field select{font-size:16px}
        }
        """
    )


def _build_agent_studio_panel_html() -> str:
    return textwrap.dedent(
        '''
        <!-- ===== Agent Studio Panel ===== -->
        <div class='card' style='margin-top: 16px; padding: 16px;' id='agent-studio-card' data-search-aliases='Agent Studio, Manage Agents, install agent, uninstall agent, live agents, drafts, AutoYou builder, coding agent, website agent, restart AutoYou AI'>
          <div class='agent-studio-head'>
            <div>
              <h3>Agent Studio</h3>
              <p id='agent-studio-summary'>Keep the main admin page light while managing agent drafts through dedicated builder, instructions, and website workbenches.</p>
            </div>
            <div class='agent-studio-head-actions'>
              <button id='agent-studio-refresh-btn' class='admin-feature-btn ghost' onclick='refreshAgentStudio(true)'>Refresh Studio</button>
              <button id='agent-studio-reload-btn' class='admin-feature-btn alt' onclick='reloadAgentStudioRuntime()'>Restart AI Runtime</button>
            </div>
          </div>

          <div id='agent-studio-banner' class='agent-studio-banner' style='display:none;'></div>

          <div class='agent-studio-nav-links' style='display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 6px;'>
            <a href='/agent/agent_builder_agent/' target='_blank' class='admin-feature-btn ghost' style='font-size:13px;padding:5px 12px;text-decoration:none;'>Agent Builder Website</a>
            <a href='/agent/website_agent/' target='_blank' class='admin-feature-btn ghost' style='font-size:13px;padding:5px 12px;text-decoration:none;'>Website Builder</a>
            <a href='/agent/tasks_agent/' target='_blank' class='admin-feature-btn ghost' style='font-size:13px;padding:5px 12px;text-decoration:none;'>Tasks Website</a>
            <a href='/agent/notify_agent/' target='_blank' class='admin-feature-btn ghost' style='font-size:13px;padding:5px 12px;text-decoration:none;'>Notify Website</a>
          </div>

          <div class='agent-studio-runtime-strip'>
            <span id='agent-studio-runtime-mode' class='agent-badge agent-badge-live'>Loading runtime…</span>
            <span id='agent-studio-count-pill' class='agent-badge agent-badge-available'>0 agents</span>
          </div>

          <div class='agent-studio-shell'>
            <div class='agent-studio-sidebar'>
              <section class='agent-card-shell'>
                <div style='display:grid; gap:14px;'>
                  <div>
                    <div class='wizard-kicker'>Overview</div>
                    <h4 style='margin:8px 0 0 0;'>Live Agents and Drafts</h4>
                    <p id='agent-studio-runtime-note' class='muted' style='margin:8px 0 0 0;'>Loading runtime policy…</p>
                  </div>
                  <div class='agent-studio-summary-grid'>
                    <div class='agent-studio-stat-card'>
                      <strong id='agent-studio-stat-installed'>0 installed</strong>
                      <span>Loaded into the runtime after restart.</span>
                    </div>
                    <div class='agent-studio-stat-card'>
                      <strong id='agent-studio-stat-drafts'>0 drafts</strong>
                      <span>Workspace drafts tracked separately from live packages.</span>
                    </div>
                    <div class='agent-studio-stat-card'>
                      <strong id='agent-studio-stat-available'>0 available</strong>
                      <span>Live packages not currently installed.</span>
                    </div>
                    <div class='agent-studio-stat-card'>
                      <strong id='agent-studio-stat-total'>0 total</strong>
                      <span id='agent-studio-drafts-root'>Draft root loading…</span>
                    </div>
                  </div>
                  <div>
                    <label for='agent-studio-search' style='display:block; margin-bottom:6px; font-weight:700;'>Search Agents</label>
                    <input id='agent-studio-search' class='agent-studio-search' type='search' placeholder='Search drafts, built-ins, or website-capable agents' oninput='renderAgentStudioList()'>
                  </div>
                  <div id='agent-studio-list' class='agent-studio-list'>
                    <div class='agent-studio-empty'>Loading agent studio…</div>
                  </div>
                </div>
              </section>

              <section class='agent-builder-scaffold'>
                <div class='agent-builder-scaffold-header'>
                  <div class='agent-builder-scaffold-title'>
                    <h4>Builder Workbench</h4>
                    <p>Create a new workspace draft. This no longer mutates the live runtime directly.</p>
                  </div>
                  <span id='agent-studio-builder-pill' class='agent-builder-pill'>Checking builder agent…</span>
                </div>
                <p id='agent-studio-builder-help' class='agent-builder-help'>Drafts are isolated under the writable workspace and only become live after publish.</p>
                <div id='agent-studio-builder-form' class='agent-builder-form-shell'>
                  <div class='agent-builder-grid'>
                    <div class='agent-builder-field'>
                      <label>Agent Name <span>e.g. weather_agent</span></label>
                      <input id='agent-studio-new-name' type='text' placeholder='weather_agent'>
                    </div>
                    <div class='agent-builder-field'>
                      <label>First Tool Name <span>e.g. get_weather</span></label>
                      <input id='agent-studio-new-tool-name' type='text' placeholder='get_weather'>
                    </div>
                    <div class='agent-builder-field'>
                      <label>Agent Description</label>
                      <input id='agent-studio-new-description' type='text' placeholder='Fetches weather data from a provider.'>
                    </div>
                    <div class='agent-builder-field'>
                      <label>Tool Description</label>
                      <input id='agent-studio-new-tool-description' type='text' placeholder='Return the current weather for a city.'>
                    </div>
                  </div>
                  <div class='agent-builder-actions'>
                    <button id='agent-studio-create-btn' class='admin-feature-btn primary' onclick='createAgentStudioDraft()'>Create Workspace Draft</button>
                    <span id='agent-studio-create-status' class='agent-builder-status'></span>
                  </div>
                </div>
              </section>
            </div>

            <div class='agent-studio-main'>
              <section class='agent-editor-card' id='agent-studio-overview-card'>
                <div class='agent-studio-selection-shell'>
                  <div class='agent-studio-selection-head'>
                    <div class='agent-studio-selection-copy'>
                      <div class='wizard-kicker'>Selected Agent</div>
                      <h3 id='agent-studio-selected-name'>Select an agent</h3>
                      <p id='agent-studio-selected-desc'>Choose a live agent or workspace draft from the list to open its dedicated workbenches.</p>
                      <div id='agent-studio-selected-badges' class='agent-studio-selection-badges'></div>
                    </div>
                    <div id='agent-studio-selected-actions' class='agent-studio-selection-actions'></div>
                  </div>
                  <div id='agent-studio-selected-meta' class='agent-studio-selection-meta'></div>
                </div>
              </section>

              <div class='agent-studio-builder-links' style='background:#1a2035;border:1px solid #2a3048;border-radius:8px;padding:16px;margin-top:12px;'>
                <h4 style='margin:0 0 8px;font-size:14px;color:#e4e8f0;'>Dedicated Builder Websites</h4>
                <p style='color:#8b95aa;font-size:13px;margin-bottom:12px;'>Agent scaffolding, website design, and publish controls have moved to dedicated websites accessible below.</p>
                <div style='display:flex;gap:10px;flex-wrap:wrap;'>
                  <a href='/agent/agent_builder_agent/' target='_blank' class='admin-feature-btn ghost' style='font-size:13px;padding:6px 14px;text-decoration:none;'>⚙ Open Agent Builder ↗</a>
                  <a href='/agent/website_agent/' target='_blank' class='admin-feature-btn ghost' style='font-size:13px;padding:6px 14px;text-decoration:none;'>🌐 Open Website Builder ↗</a>
                </div>
              </div>
            </div>
          </div>
        </div>

        <script>
          window._agentStudioPayload = null;
          window._agentStudioDetail = null;
          window._agentStudioSelectedAgent = null;
          window._agentStudioRestartRequired = false;

          function escapeAgentStudioHtml(value) {
            return String(value || '')
              .replaceAll('&', '&amp;')
              .replaceAll('<', '&lt;')
              .replaceAll('>', '&gt;')
              .replaceAll('"', '&quot;')
              .replaceAll("'", '&#39;');
          }

          function openAgentFrontend(url) {
            if (!url) return;
            try {
              const resolved = new URL(url, window.location.href);
              const host = (resolved.hostname || '').toLowerCase();
              if (host === 'localhost' || host === '127.0.0.1' || host === '::1') {
                window.location.assign(resolved.toString());
                return;
              }
            } catch (_) {
            }
            window.open(url, '_blank', 'noopener,noreferrer');
          }

          async function copyAgentFrontendUrl(url) {
            if (!url) return;
            try {
              await navigator.clipboard.writeText(url);
              setAgentStudioBanner('success', 'Copied path to clipboard.');
            } catch (_) {
              window.prompt('Copy this value', url);
            }
          }

          function agentStudioBadge(label, variant) {
            return '<span class="agent-badge ' + escapeAgentStudioHtml(variant || 'agent-badge-disabled') + '">' + escapeAgentStudioHtml(label) + '</span>';
          }

          function agentStudioActionButton(label, onclick, variant, disabled, options) {
            options = options || {};
            const tone = variant || 'ghost';
            const disabledAttr = disabled ? ' disabled aria-disabled="true"' : '';
            const noLoadingAttr = options.noLoading ? ' data-no-loading="1"' : '';
            const titleAttr = options.title ? ' title="' + escapeAgentStudioHtml(options.title) + '"' : '';
            return '<button type="button" class="admin-feature-btn ' + escapeAgentStudioHtml(tone) + '"' + disabledAttr + noLoadingAttr + titleAttr + ' onclick="' + (disabled ? '' : escapeAgentStudioHtml(onclick || '')) + '">' + escapeAgentStudioHtml(label) + '</button>';
          }

          function agentStudioMetaRow(label, value, options) {
            const opts = options || {};
            const safeValue = escapeAgentStudioHtml(value || '');
            const body = opts.code
              ? '<code>' + safeValue + '</code>'
              : '<span>' + safeValue + '</span>';
            return '<div class="agent-studio-meta-row"><strong>' + escapeAgentStudioHtml(label) + '</strong>' + body + '</div>';
          }

          function refreshAdminSearchFromAgentStudio() {
            if (typeof adminBuildSearchIndex === 'function') {
              adminBuildSearchIndex();
            }
            if (typeof adminRenderJumpChips === 'function') {
              adminRenderJumpChips();
            }
            if (typeof adminHandleSearchInput === 'function') {
              adminHandleSearchInput();
            }
          }

          function handleAgentStudioSelect(buttonEl) {
            if (!buttonEl) return;
            const agentName = String(buttonEl.getAttribute('data-agent-name') || '').trim();
            if (!agentName) return;
            loadAgentWorkbenchDetail(agentName);
          }

          function setAgentStudioBanner(tone, message) {
            const banner = document.getElementById('agent-studio-banner');
            if (!banner) return;
            if (!message) {
              banner.style.display = 'none';
              banner.textContent = '';
              return;
            }
            const tones = {
              info: { border: 'rgba(96,165,250,0.28)', background: 'rgba(59,130,246,0.08)', color: 'var(--text)' },
              success: { border: 'rgba(34,197,94,0.3)', background: 'rgba(34,197,94,0.12)', color: 'var(--text)' },
              warning: { border: 'rgba(251,191,36,0.3)', background: 'rgba(250,204,21,0.10)', color: 'var(--text)' },
              danger: { border: 'rgba(248,113,113,0.32)', background: 'rgba(127,29,29,0.22)', color: 'var(--text)' }
            };
            const palette = tones[tone] || tones.info;
            banner.style.display = 'block';
            banner.style.borderColor = palette.border;
            banner.style.background = palette.background;
            banner.style.color = palette.color;
            banner.textContent = message;
          }

          function setAgentStudioRestartRequired(required, message) {
            window._agentStudioRestartRequired = !!required;
            const btn = document.getElementById('agent-studio-reload-btn');
            if (btn) {
              btn.className = required ? 'admin-feature-btn primary' : 'admin-feature-btn alt';
              btn.textContent = required ? 'Restart AI Runtime to Apply' : 'Restart AI Runtime';
            }
            if (message) {
              setAgentStudioBanner(required ? 'warning' : 'success', message);
            }
          }

          function currentAgentStudioDetail() {
            return window._agentStudioDetail || null;
          }

          function applyAgentStudioPayload(payload) {
            window._agentStudioPayload = payload || null;
            const data = payload || {};
            const counts = data.overview_counts || {};
            const builderInstalled = !!data.builder_installed;

            const builderPill = document.getElementById('agent-studio-builder-pill');
            const builderHelp = document.getElementById('agent-studio-builder-help');
            if (builderPill) {
              builderPill.textContent = builderInstalled ? 'Builder runtime installed' : 'Builder runtime optional';
              builderPill.classList.toggle('is-ready', builderInstalled);
            }
            if (builderHelp) {
              builderHelp.textContent = builderInstalled
                ? 'The builder runtime is installed. Draft creation still remains isolated until you explicitly publish.'
                : 'Draft creation works from the admin UI even if agent_builder_agent is not currently installed in the runtime.';
            }

            const runtimeMode = document.getElementById('agent-studio-runtime-mode');
            if (runtimeMode) {
              runtimeMode.textContent = data.packaged_runtime ? 'Packaged Runtime' : 'Source Checkout';
              runtimeMode.className = 'agent-badge ' + (data.packaged_runtime ? 'agent-badge-live' : 'agent-badge-installed');
            }
            const countPill = document.getElementById('agent-studio-count-pill');
            if (countPill) {
              countPill.textContent = String(counts.total || 0) + ' agents tracked';
            }
            const runtimeNote = document.getElementById('agent-studio-runtime-note');
            if (runtimeNote) runtimeNote.textContent = data.runtime_policy_note || '';
            const draftsRoot = document.getElementById('agent-studio-drafts-root');
            if (draftsRoot) draftsRoot.textContent = data.workspace_drafts_root || 'Draft root unavailable';
            const statInstalled = document.getElementById('agent-studio-stat-installed');
            const statDrafts = document.getElementById('agent-studio-stat-drafts');
            const statAvailable = document.getElementById('agent-studio-stat-available');
            const statTotal = document.getElementById('agent-studio-stat-total');
            if (statInstalled) statInstalled.textContent = String(counts.installed || 0) + ' installed';
            if (statDrafts) statDrafts.textContent = String(counts.drafts || 0) + ' drafts';
            if (statAvailable) statAvailable.textContent = String(counts.available || 0) + ' available';
            if (statTotal) statTotal.textContent = String(counts.total || 0) + ' total';

            renderAgentStudioList();
            refreshAdminSearchFromAgentStudio();
          }

          function renderAgentStudioList() {
            const container = document.getElementById('agent-studio-list');
            if (!container) return;
            const payload = window._agentStudioPayload || {};
            const entries = Array.isArray(payload.agent_overview) ? payload.agent_overview.slice() : [];
            const filter = String((document.getElementById('agent-studio-search') || {}).value || '').trim().toLowerCase();
            const filtered = entries.filter((entry) => {
              if (!filter) return true;
              const haystack = [
                entry.agent_name,
                entry.display_name,
                entry.description,
                entry.state_label
              ].join(' ').toLowerCase();
              return haystack.includes(filter);
            });
            if (!filtered.length) {
              container.innerHTML = '<div class="agent-studio-empty">No agents match the current filter.</div>';
              return;
            }
            container.innerHTML = filtered.map((entry) => {
              const badges = [];
              badges.push(agentStudioBadge(entry.state_label || 'Available', entry.installed ? 'agent-badge-installed' : (entry.draft_only ? 'agent-badge-disabled' : 'agent-badge-available')));
              if (entry.draft_exists) badges.push(agentStudioBadge('Draft', 'agent-badge-disabled'));
              if (entry.has_frontend) badges.push(agentStudioBadge(entry.frontend_enabled ? 'Website On' : 'Website', 'agent-badge-frontend'));
              if (entry.frontend_route_mode) badges.push(agentStudioBadge(entry.frontend_route_mode === 'direct_forward' ? 'Direct same-port' : 'Primary browser path', entry.frontend_route_mode === 'direct_forward' ? 'agent-badge-live' : 'agent-badge-frontend'));
              if (entry.runtime_blocked) badges.push(agentStudioBadge('Packaged Block', 'agent-badge-disabled'));
              if (entry.builtin_agent) badges.push(agentStudioBadge('Built-in', 'agent-badge-live'));
              return (
                '<button type="button" class="agent-studio-list-item ' + (window._agentStudioSelectedAgent === entry.agent_name ? 'is-active' : '') + '" data-agent-name="' + escapeAgentStudioHtml(entry.agent_name || '') + '" onclick="handleAgentStudioSelect(this)">' +
                  '<div class="agent-studio-list-top">' +
                    '<div class="agent-studio-list-title">' +
                      '<strong>' + escapeAgentStudioHtml(entry.display_name || entry.agent_name || 'Unknown agent') + '</strong>' +
                      '<span><code>' + escapeAgentStudioHtml(entry.agent_name || '') + '</code></span>' +
                    '</div>' +
                  '</div>' +
                  '<p class="agent-studio-list-copy">' + escapeAgentStudioHtml(entry.description || 'No description available yet.') + '</p>' +
                  '<div class="agent-studio-list-meta">' + badges.join('') + '</div>' +
                '</button>'
              );
            }).join('');
          }

          function renderAgentStudioOwnerCard(label, ownerState) {
            const state = ownerState || {};
            return (
              '<div class="agent-studio-owner-card">' +
                '<strong>' + escapeAgentStudioHtml(label) + '</strong>' +
                '<span>Status: ' + escapeAgentStudioHtml(state.status || 'idle') + '</span>' +
                '<small>' + escapeAgentStudioHtml(state.updated_at || 'No changes recorded yet.') + '</small>' +
              '</div>'
            );
          }

          function renderAgentStudioSelection(detail) {
            const selectedName = document.getElementById('agent-studio-selected-name');
            const selectedDesc = document.getElementById('agent-studio-selected-desc');
            const selectedBadges = document.getElementById('agent-studio-selected-badges');
            const selectedMeta = document.getElementById('agent-studio-selected-meta');
            const selectedActions = document.getElementById('agent-studio-selected-actions');
            if (!selectedName || !selectedDesc || !selectedBadges || !selectedMeta || !selectedActions) return;

            if (!detail) {
              selectedName.textContent = 'Select an agent';
              selectedDesc.textContent = 'Choose a live agent or workspace draft from the list to open its dedicated workbenches.';
              selectedBadges.innerHTML = '';
              selectedMeta.innerHTML = '';
              selectedActions.innerHTML = '';
              return;
            }

            selectedName.textContent = detail.display_name || detail.agent_name || 'Unknown agent';
            selectedDesc.textContent = detail.description || 'No description available yet.';
            const badges = [];
            badges.push(agentStudioBadge(detail.installed ? 'Installed' : 'Not Installed', detail.installed ? 'agent-badge-installed' : 'agent-badge-available'));
            badges.push(agentStudioBadge(detail.builtin_agent ? 'Built-in' : 'Workspace', detail.builtin_agent ? 'agent-badge-live' : 'agent-badge-disabled'));
            if (detail.draft_exists) badges.push(agentStudioBadge(detail.draft_only ? 'Draft Only' : 'Draft Ready', 'agent-badge-disabled'));
            if (detail.live_instruction && detail.live_instruction.read_only) badges.push(agentStudioBadge('Read-only Live Prompt', 'agent-badge-frontend'));
            if (detail.frontend_control) badges.push(agentStudioBadge(detail.frontend_control.enabled ? 'Website Enabled' : 'Website Disabled', detail.frontend_control.enabled ? 'agent-badge-installed' : 'agent-badge-disabled'));
            if (detail.frontend_control && detail.frontend_control.route_mode) badges.push(agentStudioBadge(detail.frontend_control.route_mode === 'direct_forward' ? 'Direct same-port' : 'Primary browser path', detail.frontend_control.route_mode === 'direct_forward' ? 'agent-badge-live' : 'agent-badge-frontend'));
            selectedBadges.innerHTML = badges.join('');

            const meta = [];
            meta.push(agentStudioMetaRow('Runtime Name', detail.runtime_agent_name || detail.agent_name || '', { code: true }));
            meta.push(agentStudioMetaRow('Live Source', (detail.live_source || {}).source_kind || (detail.live_exists ? 'available' : 'not published yet')));
            meta.push(agentStudioMetaRow('Workspace Root', detail.workspace_agents_root || '', { code: true }));
            meta.push(agentStudioMetaRow('Draft Root', detail.workspace_drafts_root || '', { code: true }));
            if (detail.runtime_block_reason) {
              meta.push(agentStudioMetaRow('Runtime Policy', detail.runtime_block_reason));
            }
            selectedMeta.innerHTML = meta.join('');

            const actions = [];
            if (detail.can_install) {
              actions.push(agentStudioActionButton('Install Live Agent', 'installSelectedAgent()', 'primary', false));
            } else if (detail.can_uninstall) {
              actions.push(agentStudioActionButton('Uninstall Live Agent', 'uninstallSelectedAgent()', 'ghost', false));
            }
            const frontend = detail.frontend || detail.frontend_manifest || null;
            const launchUrl = frontend && (frontend.open_url || frontend.launch_url || frontend.launch_path)
              ? (frontend.open_url || frontend.launch_url || frontend.launch_path)
              : '';
            const copyValue = frontend && (frontend.proxy_path || frontend.launch_path || launchUrl)
              ? (frontend.proxy_path || frontend.launch_path || launchUrl)
              : '';
            if (launchUrl && !detail.runtime_blocked) {
              actions.push(agentStudioActionButton('Open website', 'openAgentFrontend(' + JSON.stringify(launchUrl) + ')', 'alt', false, { noLoading: true }));
            }
            if (copyValue && !detail.runtime_blocked) {
              actions.push(agentStudioActionButton('Copy Path', 'copyAgentFrontendUrl(' + JSON.stringify(copyValue) + ')', 'ghost', false, { noLoading: true }));
            }
            if (detail.frontend_control && !detail.runtime_blocked) {
              const nextMode = detail.frontend_control.route_mode === 'direct_forward' ? 'path_proxy' : 'direct_forward';
              const modeLabel = nextMode === 'direct_forward' ? 'Use direct port' : 'Use browser path';
              actions.push(agentStudioActionButton(modeLabel, 'setAgentWebsiteRouteMode(' + JSON.stringify(nextMode) + ')', 'ghost', false));
            }
            actions.push(agentStudioActionButton('Restart AI Runtime', 'reloadAgentStudioRuntime()', window._agentStudioRestartRequired ? 'primary' : 'alt', false));
            selectedActions.innerHTML = actions.join('');
          }


          async function installSelectedAgent() {
            const agentName = window._agentStudioSelectedAgent;
            if (!agentName) return;
            const actionsEl = document.getElementById('agent-studio-selected-actions');
            const btns = actionsEl ? Array.from(actionsEl.querySelectorAll('button')) : [];
            btns.forEach((b) => { b.disabled = true; });
            try {
              const data = await postAgentStudioJson('/api/agents/install', { agent_name: agentName });
              await applyAgentStudioMutationResponse(data, agentName);
            } catch (error) {
              setAgentStudioBanner('danger', 'Install failed: ' + error);
              btns.forEach((b) => { b.disabled = false; });
            }
          }

          async function uninstallSelectedAgent() {
            const agentName = window._agentStudioSelectedAgent;
            if (!agentName) return;
            const actionsEl = document.getElementById('agent-studio-selected-actions');
            const btns = actionsEl ? Array.from(actionsEl.querySelectorAll('button')) : [];
            btns.forEach((b) => { b.disabled = true; });
            try {
              const data = await postAgentStudioJson('/api/agents/uninstall', { agent_name: agentName });
              await applyAgentStudioMutationResponse(data, agentName);
            } catch (error) {
              setAgentStudioBanner('danger', 'Uninstall failed: ' + error);
              btns.forEach((b) => { b.disabled = false; });
            }
          }

          async function setAgentWebsiteRouteMode(routeMode) {
            const agentName = window._agentStudioSelectedAgent;
            if (!agentName) return;
            const actionsEl = document.getElementById('agent-studio-selected-actions');
            const btns = actionsEl ? Array.from(actionsEl.querySelectorAll('button')) : [];
            btns.forEach((b) => { b.disabled = true; });
            try {
              const data = await postAgentStudioJson('/api/agents/frontend', {
                agent_name: agentName,
                route_mode: routeMode
              });
              await applyAgentStudioMutationResponse(data, agentName);
            } catch (error) {
              setAgentStudioBanner('danger', 'Website route update failed: ' + error);
              btns.forEach((b) => { b.disabled = false; });
            }
          }

          function renderAgentStudioDetail(detail) {
            window._agentStudioDetail = detail || null;
            if (detail && detail.agent_name) {
              window._agentStudioSelectedAgent = detail.agent_name;
            }
            renderAgentStudioSelection(detail);
            renderAgentStudioList();
            refreshAdminSearchFromAgentStudio();
          }

          function loadAgentWorkbenchDetail(agentName, silent) {
            if (!agentName) return;
            window._agentStudioSelectedAgent = agentName;
            renderAgentStudioList();
            const payload = window._agentStudioPayload || {};
            const detail = ((payload.agent_details || {})[agentName]) || null;
            renderAgentStudioDetail(detail);
            if (!silent && detail) {
              setAgentStudioBanner('info', 'Opened ' + (detail.display_name || agentName) + ' in the agent studio.');
            }
          }

          async function refreshAgentStudio(showNotice) {
            const refreshBtn = document.getElementById('agent-studio-refresh-btn');
            if (refreshBtn) {
              refreshBtn.disabled = true;
              refreshBtn.textContent = 'Refreshing…';
            }
            try {
              const response = await fetch('/api/agents/manage');
              const data = await response.json();
              if (!response.ok || data.status === 'error') {
                throw new Error(data.error || data.message || ('HTTP ' + response.status));
              }
              applyAgentStudioPayload(data);
              const entries = Array.isArray(data.agent_overview) ? data.agent_overview : [];
              const selectedAgent = entries.some((item) => item.agent_name === window._agentStudioSelectedAgent)
                ? window._agentStudioSelectedAgent
                : (entries[0] ? entries[0].agent_name : null);
              if (selectedAgent) {
                await loadAgentWorkbenchDetail(selectedAgent, true);
              } else {
                renderAgentStudioDetail(null);
              }
              if (showNotice) {
                setAgentStudioBanner('info', 'Agent studio refreshed from disk.');
              }
            } catch (error) {
              setAgentStudioBanner('danger', 'Failed to refresh agent studio: ' + error);
            } finally {
              if (refreshBtn) {
                refreshBtn.disabled = false;
                refreshBtn.textContent = 'Refresh Studio';
              }
            }
          }

          async function applyAgentStudioMutationResponse(data, fallbackAgentName) {
            if (data.payload) {
              applyAgentStudioPayload(data.payload);
            }
            if (data.requires_restart) {
              setAgentStudioRestartRequired(true, data.message || 'Restart AutoYou AI to apply this change.');
            } else if (data.message) {
              setAgentStudioBanner('success', data.message);
            }
            if (data.detail) {
              renderAgentStudioDetail(data.detail);
              return;
            }
            const candidate = data.agent_name || fallbackAgentName || window._agentStudioSelectedAgent;
            const payload = window._agentStudioPayload || {};
            const overview = Array.isArray(payload.agent_overview) ? payload.agent_overview : [];
            if (candidate && overview.some((item) => item.agent_name === candidate)) {
              await loadAgentWorkbenchDetail(candidate, true);
              return;
            }
            if (overview.length) {
              await loadAgentWorkbenchDetail(overview[0].agent_name, true);
            } else {
              renderAgentStudioDetail(null);
            }
          }

          async function postAgentStudioJson(url, body) {
            const response = await fetch(url, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(body || {})
            });
            const data = await response.json();
            if (!response.ok || !data.success) {
              throw new Error(data.error || ('HTTP ' + response.status));
            }
            return data;
          }

          async function createAgentStudioDraft() {
            const name = String((document.getElementById('agent-studio-new-name') || {}).value || '').trim();
            const description = String((document.getElementById('agent-studio-new-description') || {}).value || '').trim();
            const toolName = String((document.getElementById('agent-studio-new-tool-name') || {}).value || '').trim();
            const toolDescription = String((document.getElementById('agent-studio-new-tool-description') || {}).value || '').trim();
            const status = document.getElementById('agent-studio-create-status');
            if (!name || !description) {
              if (status) {
                status.textContent = 'Name and description are required.';
                status.style.color = 'var(--danger)';
              }
              return;
            }
            const btn = document.getElementById('agent-studio-create-btn');
            if (btn) {
              btn.disabled = true;
              btn.textContent = 'Creating…';
            }
            try {
              const data = await postAgentStudioJson('/api/builder/create', {
                name,
                description,
                tool_name: toolName || 'handle_request',
                tool_description: toolDescription || description
              });
              if (status) {
                status.textContent = data.message || 'Draft created.';
                status.style.color = 'var(--success)';
              }
              await applyAgentStudioMutationResponse(data, data.agent_name || name);
            } catch (error) {
              if (status) {
                status.textContent = 'Create failed: ' + error;
                status.style.color = 'var(--danger)';
              }
              setAgentStudioBanner('danger', 'Failed to create draft: ' + error);
            } finally {
              if (btn) {
                btn.disabled = false;
                btn.textContent = 'Create Workspace Draft';
              }
            }
          }

          async function reloadAgentStudioRuntime() {
            const btn = document.getElementById('agent-studio-reload-btn');
            if (btn) {
              btn.disabled = true;
              btn.textContent = 'Restarting…';
            }
            try {
              const response = await fetch('/api/ai/restart', { method: 'POST' });
              const data = await response.json();
              if (!response.ok || !data.success) {
                throw new Error(data.error || ('HTTP ' + response.status));
              }
              setAgentStudioRestartRequired(false, data.message || 'AutoYou AI restarted successfully.');
              await refreshAgentStudio(false);
            } catch (error) {
              setAgentStudioBanner('danger', 'Failed to restart AutoYou AI: ' + error);
            } finally {
              if (btn) {
                btn.disabled = false;
                if (!window._agentStudioRestartRequired) {
                  btn.className = 'admin-feature-btn alt';
                  btn.textContent = 'Restart AI Runtime';
                }
              }
            }
          }

          refreshAgentStudio(false);
        </script>
        '''
    )


# TODO(refactor): Continue decomposing the remaining dashboard section bodies.
# The shared dashboard shell, section navigation, and search UI now live in
# `shared.admin_dashboard_shell`, but the section markup below is still large
# enough that it should be split into per-surface renderers (AI, messaging,
# connectivity, security, speech, and utilities) to keep server.py focused on
# routing/runtime logic.
async def build_legacy_dashboard_html(bot_status: str = "Unknown", bot_name: str = "-", signal_status: str = "Unknown", signal_name: str = "-", whatsapp_status: str = "Unknown", whatsapp_name: str = "-", banner_html: str = "",
                        ollama_status: str = "Unknown", ollama_status_color: str = "#6b7280", ollama_api_base: str = "Unknown", ollama_models_count: int = 0, ollama_selected_model: str = "None",
                        google_status: str = "Unknown", google_status_color: str = "#6b7280", use_google_api: bool = False, google_model: str = "Unknown", google_api_key_status: str = "Unknown", ai_provider_summary: str = "Unknown", ai_agent_status: str = "Unknown",
                        tunnelmole_status: str = "Unknown", tunnelmole_url: str = "-", wizard_payload_json: str = "{}", show_onboarding_wizard: bool = False,
                        cloud_config: Optional[Dict[str, Any]] = None, cloud_connected: bool = False,
                        cloud_status: Optional[Dict[str, Any]] = None) -> str:
    """Generate the admin dashboard HTML.

    WARNING: This is a 1,877-line inline HTML template using Python f-strings.
    See TODO above for the recommended refactoring path.
    """
    agent_studio_css = _build_agent_studio_css()
    telegram_cfg = STATE.config.get("telegram", {}) if STATE.config else {}
    token = telegram_cfg.get("bot_token") or ""
    rtc_json = json.dumps(STATE.config.get("rtc", {"iceServers": []}), indent=2) if STATE.config else json.dumps({"iceServers": []}, indent=2)
    # Prepare ACL usernames value for UI
    acl_list = telegram_cfg.get("acl_usernames", [])
    acl_sender_list = [
        sender_id
        for sender_id in (
            _normalize_telegram_sender_id(entry)
            for entry in (telegram_cfg.get("acl_sender_ids", []) or [])
        )
        if sender_id
    ]
    telegram_access_gate_enabled = bool(telegram_cfg.get("access_gate_enabled", False))
    telegram_silent_unapproved_messages = bool(telegram_cfg.get("silent_unapproved_messages", False))
    # Prepare Cloud configuration values
    _cloud_cfg = cloud_config if cloud_config is not None else ((STATE.config or {}).get("cloud", {}))
    _cloud_status = dict(cloud_status or {})
    _cloud_enrolled = bool(_cloud_status.get("enrolled", bool(_cloud_cfg.get("server_token", ""))))
    _cloud_registered = bool(_cloud_status.get("registered", _cloud_enrolled))
    _cloud_email = html.escape(str(_cloud_status.get("email", _cloud_cfg.get("email", "")) or ""))
    _cloud_server_id = html.escape(str(_cloud_status.get("server_id", _cloud_cfg.get("server_id", "")) or ""))
    _cloud_is_active = _cloud_status.get("is_active")
    _cloud_token_rejected = bool(_cloud_status.get("token_rejected", False))
    _cloud_sse_connected = bool(_cloud_status.get("sse_connected", cloud_connected))
    _cloud_connected = bool(_cloud_status.get("connected", _cloud_registered and _cloud_sse_connected))
    _cloud_info_unreachable = bool(_cloud_status.get("info_unreachable", False))
    _cloud_status_message = html.escape(str(_cloud_status.get("status_message") or ""))
    _cloud_activate_url = html.escape(str(_cloud_status.get("activate_url") or ""), quote=True)
    _cloud_reregister_url = html.escape(str(_cloud_status.get("reregister_url") or ""), quote=True)
    acl_value = ", ".join([f"@{u}" if u and not u.startswith("@") else u for u in acl_list]) if acl_list else ""
    acl_sender_value = ", ".join(acl_sender_list) if acl_sender_list else ""
    # Prepare Signal configuration values
    signal_config = STATE.config.get("signal", {}) if STATE.config else {}
    signal_enabled = signal_config.get("enabled", False)
    signal_port = signal_config.get("port", 8082)
    signal_device_name = signal_config.get("device_name", "signal-api")
    signal_paired = signal_config.get("paired", False)
    signal_phone_number = signal_config.get("phone_number", "")
    signal_shutdown_docker = signal_config.get("shutdown_docker_on_exit", True)
    
    # Prepare WhatsApp configuration values
    whatsapp_config = STATE.config.get("whatsapp", {}) if STATE.config else {}
    whatsapp_enabled = whatsapp_config.get("enabled", False)
    whatsapp_port = whatsapp_config.get("websocket_port", 8083)
    whatsapp_device_name = whatsapp_config.get("device_name", "AutoYou-WhatsApp")
    whatsapp_paired = whatsapp_config.get("paired", False)
    whatsapp_phone_number = whatsapp_config.get("phone_number", "")
    
    # Prepare Ollama configuration values
    ollama_config  = STATE.config.get("ollama", {}) if STATE.config else {}
    ai_prov_config = STATE.config.get("ai_provider", {}) if STATE.config else {}
    ollama_model   = ollama_config.get("model", os.getenv("OLLAMA_MODEL", DEFAULT_WIZARD_MODEL))

    # Active provider for radio buttons
    _active_provider = str(ai_prov_config.get("provider") or os.getenv("AI_PROVIDER", "ollama")).lower()
    if _active_provider not in {"ollama", "ollama_gateway", "odysseus", "openclaw", "hermes", "litellm", "google", "apple_intelligence"}:
        _active_provider = "google" if ollama_config.get("use_google_api", False) else "ollama"
    from shared.apple_intelligence import helper_path as apple_model_helper
    _apple_provider_card = ""
    if apple_model_helper() is not None or _active_provider == "apple_intelligence":
        _apple_provider_card = f"""<label class='prov-card-lbl {"active" if _active_provider == "apple_intelligence" else ""}' id='prov-lbl-apple_intelligence'>
            <input type='radio' name='ai_provider' value='apple_intelligence' {'checked' if _active_provider == 'apple_intelligence' else ''} onchange='syncProviderPanels(this.value)'>
            <span class='prov-check-badge'>&#10003;</span>
            <div class='prov-name'>Apple Intelligence + Agents</div>
            <div class='prov-tag'>Text generation on this Mac</div></label>"""

    # OpenClaw provider settings for template
    oc_port_val  = ai_prov_config.get("openclaw_port",  18789)
    oc_token_raw = ai_prov_config.get("openclaw_token", "") or ""
    oc_model_val = ai_prov_config.get("openclaw_model", "openclaw/default") or "openclaw/default"
    oc_token_display = MASKED_SECRET_PLACEHOLDER if oc_token_raw else ""

    # OpenClaw sub-agent settings for template
    oca_port_val  = ai_prov_config.get("openclaw_agent_port",  18789)
    oca_token_raw = ai_prov_config.get("openclaw_agent_token", "") or ""
    oca_model_val = ai_prov_config.get("openclaw_agent_model", "openclaw/default") or "openclaw/default"
    oca_token_display = MASKED_SECRET_PLACEHOLDER if oca_token_raw else ""

    # Hermes Agent settings for template
    hm_port_val  = ai_prov_config.get("hermes_port",  8642)
    hm_token_raw = ai_prov_config.get("hermes_token", "") or ""
    hm_model_val = ai_prov_config.get("hermes_model", "hermes-agent") or "hermes-agent"
    hm_token_display = MASKED_SECRET_PLACEHOLDER if hm_token_raw else ""

    # LiteLLM cloud settings for template
    ll_model_val    = ai_prov_config.get("litellm_model",    "") or ""
    ll_api_key_raw  = ai_prov_config.get("litellm_api_key",  "") or ""
    ll_api_base_val = ai_prov_config.get("litellm_api_base", "") or ""
    ll_api_key_display = MASKED_SECRET_PLACEHOLDER if ll_api_key_raw else ""

    # Odysseus remains an external companion service. Its saved token is only
    # represented by the existing masked-placeholder convention in this UI.
    odysseus_api_base_val = ai_prov_config.get("odysseus_api_base", "http://127.0.0.1:7000") or "http://127.0.0.1:7000"
    odysseus_model_val = ai_prov_config.get("odysseus_model", "") or ""
    odysseus_token_display = MASKED_SECRET_PLACEHOLDER if ai_prov_config.get("odysseus_token", "") else ""

    # Raw Google API key for reveal toggle; masked in input by default
    google_api_key_raw = ollama_config.get("google_api_key", "")
    google_api_key_display = MASKED_SECRET_PLACEHOLDER if google_api_key_status == "Valid" else ""

    # Prepare Speech configuration values
    speech_config = _speech_config(STATE.config if STATE.config else {})
    speech_summary = _speech_summary(STATE.config if STATE.config else {})
    speech_tts = speech_config["tts"]
    speech_stt = speech_config["stt"]
    speech_tts_provider = speech_tts.get("provider", "system")
    speech_tts_rate = speech_tts.get("rate", 1.0)
    speech_system_voice = speech_tts.get("system_voice", "")
    speech_openai = speech_tts.get("openai", {})
    speech_openai_api_key_raw = speech_openai.get("api_key", "")
    speech_openai_api_key_display = MASKED_SECRET_PLACEHOLDER if speech_openai_api_key_raw else ""
    speech_azure = speech_tts.get("azure", {})
    speech_azure_key_raw = speech_azure.get("speech_key", "")
    speech_azure_key_display = MASKED_SECRET_PLACEHOLDER if speech_azure_key_raw else ""
    system_tts_voices = list_system_tts_voices() if callable(list_system_tts_voices) else []

    speech_system_voice_options = []
    seen_voice_ids = set()
    speech_system_voice_options.append(
        "<option value=''>Default system voice</option>"
    )
    for voice in system_tts_voices:
        voice_id = str(voice.get("id") or "").strip()
        if not voice_id:
            continue
        seen_voice_ids.add(voice_id)
        voice_label = str(voice.get("name") or voice_id)
        voice_meta = " | ".join(
            [part for part in [voice.get("languages"), voice.get("gender")] if part]
        )
        if voice_meta:
            voice_label = f"{voice_label} ({voice_meta})"
        selected = " selected" if voice_id == speech_system_voice else ""
        speech_system_voice_options.append(
            f"<option value='{html.escape(voice_id, quote=True)}'{selected}>"
            f"{html.escape(voice_label)}</option>"
        )
    if speech_system_voice and speech_system_voice not in seen_voice_ids:
        speech_system_voice_options.append(
            f"<option value='{html.escape(speech_system_voice, quote=True)}' selected>"
            f"Current custom voice: {html.escape(speech_system_voice)}</option>"
        )
    speech_system_voice_options_html = "".join(speech_system_voice_options)
    openai_model_options_html = "".join(
        f"<option value='{html.escape(model, quote=True)}'"
        f"{' selected' if model == speech_openai.get('model') else ''}>"
        f"{html.escape(model)}</option>"
        for model in OPENAI_TTS_MODELS
    )
    openai_voice_options_html = "".join(
        f"<option value='{html.escape(voice_name, quote=True)}'></option>"
        for voice_name in OPENAI_TTS_VOICES
    )
    stt_model_datalist_html = "".join(
        f"<option value='{html.escape(model_name, quote=True)}'></option>"
        for model_name in STT_MODEL_SUGGESTIONS
    )
    stt_compute_type_datalist_html = "".join(
        f"<option value='{html.escape(compute_type, quote=True)}'></option>"
        for compute_type in STT_COMPUTE_TYPE_SUGGESTIONS
    )
    stt_device_datalist_html = "".join(
        f"<option value='{html.escape(device_name, quote=True)}'></option>"
        for device_name in STT_DEVICE_SUGGESTIONS
    )

    # Prepare AI Agent configuration values
    ai_agent_config = STATE.config.get("ai_agent", {}) if STATE.config else {}
    ai_agent_enabled = ai_agent_config.get("enabled", True)
    ai_agent_auto_start = ai_agent_config.get("auto_start", True)
    ai_agent_record_messages = ai_agent_config.get("record_messages_in_database", True)
    ai_agent_memory_backend = str(ai_agent_config.get("memory_backend") or "legacy").strip().lower()
    ai_agent_port = ai_agent_config.get("port", 8081)
    
    # Prepare AutoYou Page configuration values
    autoyou_config = STATE.config.get("autoyou_page", {}) if STATE.config else {}
    autoyou_page_port = _get_autoyou_page_service_port(STATE.config or {})
    autoyou_page_auto_start = autoyou_config.get("auto_start", True)
    autoyou_page_timeline_days = int(autoyou_config.get("timeline_days", 7))
    autoyou_page_theme = _read_autoyou_ui_theme()
    autoyou_forward_enabled = autoyou_config.get("custom_forward_enabled", False)
    autoyou_forward_port = autoyou_config.get("custom_forward_port", autoyou_page_port)
    autoyou_primary_browser_port = _get_autoyou_browser_forward_port(STATE.config or {})
    autoyou_advertised_websites = _get_autoyou_advertised_websites(STATE.config or {})
    autoyou_advertised_websites_json = html.escape(
        json.dumps(autoyou_advertised_websites).replace("<", "\\u003c"),
        quote=True,
    )
    autoyou_bookmarks = _get_autoyou_bookmarks(STATE.config or {})
    autoyou_bookmarks_json = html.escape(
        json.dumps(autoyou_bookmarks).replace("<", "\\u003c"),
        quote=True,
    )
    autoyou_reserved_browser_ports_json = html.escape(
        json.dumps(sorted(_get_reserved_browser_port_routes(STATE.config or {}))).replace("<", "\\u003c"),
        quote=True,
    )

    # Prepare Admin website access configuration value
    admin_frontend_proxy_enabled = _get_agent_frontend_enabled("admin_agent", cfg=(STATE.config or {}))
    try:
        admin_frontend_route_mode = _get_agent_frontend_route_mode("admin_agent", cfg=(STATE.config or {}))
    except Exception:
        admin_frontend_route_mode = "direct_forward"
    admin_frontend_route_label = (
        "Direct same-port" if admin_frontend_route_mode == "direct_forward" else "Primary browser path"
    )

    # Prepare public reverse proxy configuration values
    tunnelmole_config = STATE.config.get("tunnelmole", {}) if STATE.config else {}
    tunnelmole_enabled = tunnelmole_config.get("enabled", True)
    tunnelmole_auth_port = _get_tunnelmole_target_port()
    tunnelmole_timeout = tunnelmole_config.get("timeout_minutes", 5)
    tunnelmole_otp_timeout = tunnelmole_config.get("otp_timeout_minutes", 5)
    # Default False = single-use (OTP consumed on first /auth; tunnel stays up
    # just long enough to finish signaling, then stops once the DataChannel opens)
    tunnelmole_otp_multiuse = tunnelmole_config.get("otp_multiuse", False)
    tunnelmole_pair_code_mode = _normalize_tunnelmole_pair_code_mode(
        tunnelmole_config.get("pair_code_mode")
    )
    tunnelmole_connection_mode = _normalize_tunnelmole_connection_mode(
        tunnelmole_config.get("connection_mode")
    )
    pairing_totp_secret = _get_pairing_totp_secret(STATE.config or {}) or ""
    pairing_totp_secret_display = MASKED_SECRET_PLACEHOLDER if pairing_totp_secret else ""

    # Get public reverse proxy status
    tunnelmole_status_info = get_tunnelmole_status()
    tunnelmole_status = tunnelmole_status_info.get("status", "Unknown")
    tunnelmole_url = tunnelmole_status_info.get("public_url", "-") or "-"
    
    # Use the whatsapp_status parameter passed from _whatsapp_status() function
    # Don't override it with local logic to maintain consistency
    default_warn = "" if not STATE.used_default_password else "<div class='card warn'><b>Warning:</b> Default password in use. Please change it immediately.</div>"
    server_name = (STATE.config.get("server", {}).get("name") or "AutoYou-Server") if STATE.config else "AutoYou-Server"
    server_name_display = html.escape(server_name)
    # from __debug_provenance_j__ import fifteenpercent
    wizard_payload_safe_json = (wizard_payload_json or "{}").replace("<", "\\u003c")
    wizard_payload_bootstrap_html = f"<script>window.__AUTOYOU_ONBOARDING__ = {wizard_payload_safe_json};</script>"
    wizard_overlay_style = "" if show_onboarding_wizard else "display:none;"
    setup_wizard_styles = """
      <style>
        .setup-hero-card{position:relative;overflow:hidden;border:1px solid rgba(56,189,248,.16);background:
          radial-gradient(circle at top left,rgba(56,189,248,.14),transparent 28%),
          radial-gradient(circle at top right,rgba(34,197,94,.12),transparent 26%),
          linear-gradient(180deg,rgba(7,14,26,.96),rgba(12,20,34,.96))}
        .setup-hero-card h1{margin-bottom:8px}
        .setup-hero-actions,.wizard-inline-actions,.model-library-toolbar,.wizard-topbar-actions{display:flex;gap:10px;flex-wrap:wrap}
        .wizard-action-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}
        .wizard-action-grid > .secondary-btn,
        .wizard-action-grid > .primary-btn,
        .wizard-action-grid > .ghost,
        .wizard-action-grid > .admin-feature-btn,
        .wizard-action-grid > a,
        .wizard-action-grid > button{width:100%;justify-content:center}
        .wizard-launcher-action{min-width:188px}
        .setup-pill-row,.wizard-stat-grid,.partner-grid,.model-runtime-grid,.download-job-list,.model-results-grid,.wizard-choice-grid{display:grid;gap:12px}
        .setup-pill-row,.model-runtime-grid{grid-template-columns:repeat(auto-fit,minmax(160px,1fr))}
        .wizard-stat-grid,.partner-grid,.wizard-choice-grid{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
        .setup-pill,.wizard-mini-card,.partner-card,.download-job,.model-result-card,.local-model-card,.wizard-choice-card{padding:14px 16px;border-radius:18px;border:1px solid rgba(71,85,105,.65);background:rgba(8,15,28,.9)}
        .setup-pill strong,.wizard-mini-card strong,.partner-card strong,.wizard-choice-card strong{display:block;margin-bottom:6px;color:#f8fafc}
        .setup-pill span,.wizard-mini-card span,.partner-card span,.wizard-choice-card span{display:block;color:#9fb3cf;line-height:1.55}
        .setup-wizard-overlay{position:fixed;inset:0;z-index:1600;padding:20px;background:rgba(2,6,23,.78);backdrop-filter:blur(10px);overflow:auto}
        .setup-wizard-frame{width:min(1220px,100%);margin:0 auto;border-radius:30px;border:1px solid rgba(56,189,248,.16);background:
          radial-gradient(circle at top left,rgba(56,189,248,.12),transparent 28%),
          linear-gradient(180deg,rgba(6,12,24,.98),rgba(11,18,31,.98));box-shadow:0 28px 90px rgba(2,6,23,.62);display:grid;grid-template-columns:280px minmax(0,1fr);overflow:hidden}
        .setup-wizard-sidebar{padding:24px;border-right:1px solid rgba(51,65,85,.75);background:linear-gradient(180deg,rgba(7,14,24,.98),rgba(10,18,30,.98))}
        .setup-wizard-main{padding:24px 24px 18px}
        .wizard-kicker{display:inline-flex;padding:7px 11px;border-radius:999px;background:rgba(14,165,233,.12);color:#7dd3fc;font-size:.79rem;letter-spacing:.08em;text-transform:uppercase}
        .wizard-lede{color:#9fb3cf;line-height:1.65}
        .wizard-step-list{display:grid;gap:8px;margin:20px 0}
        .wizard-step-btn{width:100%;text-align:left;padding:11px 12px;border-radius:16px;border:1px solid transparent;background:rgba(15,23,42,.58);color:#cbd5e1;cursor:pointer}
        .wizard-step-btn.active{background:rgba(14,165,233,.12);border-color:rgba(56,189,248,.22);color:#f8fafc}
        .wizard-topbar{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:18px}
        .wizard-topbar h2{margin:0 0 8px}
        .wizard-step-panel{display:none}
        .wizard-step-panel.active{display:block}
        .wizard-callout{margin:14px 0;padding:15px 16px;border-radius:18px;border:1px solid rgba(245,158,11,.18);background:rgba(41,25,8,.55);color:#fde68a;line-height:1.6}
        .wizard-note{padding:14px 16px;border-radius:16px;background:rgba(8,15,28,.7);border:1px solid rgba(51,65,85,.72);color:#cbd5e1;line-height:1.6}
        .wizard-progress-shell{margin:14px 0 22px}
        .wizard-progress-label{display:flex;justify-content:space-between;color:#94a3b8;font-size:.88rem;margin-bottom:10px}
        .wizard-progress-track{height:10px;border-radius:999px;background:#07101d;border:1px solid #1e293b;overflow:hidden}
        .wizard-progress-fill{height:100%;width:12.5%;background:linear-gradient(90deg,#22c55e,#38bdf8);transition:width .28s ease}
        .wizard-link{display:inline-flex;align-items:center;gap:8px;color:#7dd3fc;text-decoration:none}
        .wizard-link:hover{text-decoration:underline}
        .wizard-prompt-preview{width:100%;min-height:210px;border-radius:18px;border:1px solid rgba(51,65,85,.78);background:#07101d;color:#dbeafe;padding:14px;font-family:Consolas,monospace;font-size:.88rem}
        .wizard-status-line{display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap;color:#cbd5e1}
        .wizard-badge{display:inline-flex;align-items:center;gap:8px;padding:6px 10px;border-radius:999px;border:1px solid rgba(56,189,248,.18);background:rgba(8,15,28,.74);font-size:.83rem}
        .wizard-badge.success{border-color:rgba(34,197,94,.22);color:#bbf7d0}
        .wizard-badge.warn{border-color:rgba(245,158,11,.22);color:#fde68a}
        .wizard-badge.muted{border-color:rgba(71,85,105,.42);color:#cbd5e1}
        .wizard-badge.premium{border-color:rgba(168,85,247,.28);background:rgba(126,34,206,.08);color:#e9d5ff}
        .wizard-step-btn.completed:not(.active){border-color:rgba(34,197,94,.28);background:rgba(34,197,94,.08);color:#bbf7d0}
        .wizard-inline-shell{padding:12px;border:1px solid #1e293b;border-radius:16px;background:#0a1220;margin-bottom:10px}
        .wizard-inline-shell-title{font-weight:700;margin-bottom:8px;font-size:.88em;color:#f8fafc}
        .wizard-inline-checkbox{display:flex;align-items:flex-start;gap:12px;padding:14px 16px;border-radius:18px;border:1px solid rgba(51,65,85,.72);background:rgba(8,15,28,.78);color:#dbeafe}
        .wizard-inline-checkbox input{margin-top:3px}
        .wizard-inline-checkbox strong{display:block;margin-bottom:4px;color:#f8fafc}
        .wizard-inline-checkbox span{display:block;color:#9fb3cf;line-height:1.55}
        .wizard-textarea{width:100%;min-height:170px;background:#07101d;border:1px solid #1f2937;border-radius:18px;color:#e2e8f0;padding:14px;font-family:Consolas,monospace}
        .model-library-card{position:relative;overflow:hidden;border:1px solid rgba(56,189,248,.16);background:
          radial-gradient(circle at top left,rgba(56,189,248,.16),transparent 30%),
          radial-gradient(circle at bottom right,rgba(14,165,233,.12),transparent 32%),
          linear-gradient(180deg,rgba(6,14,28,.98),rgba(8,17,32,.98));box-shadow:0 18px 48px rgba(2,6,23,.22),inset 0 1px 0 rgba(148,163,184,.04)}
        .model-library-card::before{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(90deg,rgba(255,255,255,.03),transparent 18%,transparent 82%,rgba(56,189,248,.04))}
        .model-library-hero{display:grid;gap:18px;align-items:start;margin-bottom:22px}
        .model-library-heading{max-width:880px;display:grid;gap:10px}
        .model-library-kicker{margin-bottom:12px}
        .model-library-title{margin:0 0 10px}
        .model-library-subtitle{margin:0;color:#a8bdd8;line-height:1.72}
        .model-library-actions{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;align-items:stretch;min-width:0}
        .model-toolbar-btn,.model-action-btn,.admin-feature-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:48px;padding:0 16px;border-radius:16px;border:1px solid rgba(71,85,105,.72);background:linear-gradient(180deg,rgba(10,18,33,.95),rgba(5,12,24,.95));color:#f8fafc;font-weight:700;cursor:pointer;text-decoration:none;transition:transform .16s ease,border-color .16s ease,box-shadow .16s ease,background .16s ease}
        .model-toolbar-btn:hover,.model-action-btn:hover,.admin-feature-btn:hover{transform:translateY(-1px);border-color:rgba(56,189,248,.3);box-shadow:0 12px 24px rgba(2,6,23,.28)}
        .model-toolbar-btn.primary,.model-action-btn.primary,.admin-feature-btn.primary{background:linear-gradient(135deg,#22c55e,#06b6d4);border-color:rgba(94,234,212,.22);color:#03110d;box-shadow:0 14px 34px rgba(6,182,212,.20)}
        .model-toolbar-btn.ghost,.model-action-btn.ghost,.admin-feature-btn.ghost{background:rgba(6,14,26,.86)}
        .model-toolbar-btn.alt,.admin-feature-btn.alt{background:linear-gradient(135deg,rgba(14,165,233,.18),rgba(37,99,235,.18));border-color:rgba(56,189,248,.3)}
        .model-toolbar-btn{flex:1 1 220px;max-width:none;min-height:72px;padding:14px 16px;border-radius:18px;align-items:flex-start;justify-content:flex-start;flex-direction:column}
        .model-toolbar-label{display:block;font-size:1rem;line-height:1.15}
        .model-toolbar-meta{display:block;font-size:.82rem;font-weight:500;color:#b7c9df;line-height:1.5}
        .model-toolbar-btn.primary .model-toolbar-meta{color:#03231d}
        .model-runtime-grid-enhanced{margin-bottom:18px}
        .model-runtime-card{padding:16px 18px;border-radius:20px;background:linear-gradient(180deg,rgba(4,11,22,.96),rgba(7,16,30,.92));border:1px solid rgba(51,65,85,.8);box-shadow:inset 0 1px 0 rgba(148,163,184,.04)}
        .model-runtime-card strong{color:#f8fafc}
        .model-runtime-card span{color:#d6e7f8}
        .model-library-grid{display:grid;gap:18px}
        .model-browser-panel,.model-installed-shell{display:grid;gap:16px;align-content:start}
        .model-installed-shell{padding:18px;border-radius:24px;border:1px solid rgba(51,65,85,.72);background:linear-gradient(180deg,rgba(4,11,22,.88),rgba(6,14,27,.9));box-shadow:inset 0 1px 0 rgba(148,163,184,.03)}
        .model-installed-header{display:grid;gap:6px}
        .model-installed-header h3{margin:0;color:#f8fafc;font-size:1rem}
        .model-installed-header p{margin:0;color:#9fb4cf;line-height:1.6}
        .model-browser-panel{padding:18px;border-radius:24px;border:1px solid rgba(51,65,85,.72);background:linear-gradient(180deg,rgba(4,11,22,.86),rgba(6,14,27,.9));box-shadow:inset 0 1px 0 rgba(148,163,184,.03)}
        .model-panel-heading h3{margin:0 0 6px;color:#f8fafc;font-size:1rem}
        .model-panel-heading p{margin:0;color:#94a3b8;line-height:1.6}
        .model-source-tabs{display:inline-flex;gap:8px;flex-wrap:wrap;padding:6px;border-radius:999px;border:1px solid rgba(51,65,85,.74);background:rgba(5,12,23,.82);margin-bottom:0}
        .model-source-tab{padding:10px 16px;border-radius:999px;border:1px solid transparent;background:transparent;cursor:pointer;color:#b9cee6;font-weight:700}
        .model-source-tab.active{background:linear-gradient(135deg,rgba(14,165,233,.22),rgba(37,99,235,.2));border-color:rgba(56,189,248,.28);color:#f8fafc;box-shadow:0 0 0 1px rgba(56,189,248,.12) inset}
        .model-search-shell{display:grid;gap:14px;padding:16px;border-radius:22px;border:1px solid rgba(51,65,85,.72);background:
          linear-gradient(180deg,rgba(5,12,23,.92),rgba(6,15,28,.88)),
          radial-gradient(circle at top,rgba(56,189,248,.10),transparent 46%)}
        .model-search-input-wrap{position:relative}
        .model-search-shell input[type='search']{width:100%;min-height:56px;border-radius:16px;border:1px solid rgba(51,65,85,.82);background:rgba(3,9,19,.9);color:#f8fafc;padding:0 18px;box-shadow:inset 0 1px 0 rgba(148,163,184,.05)}
        .model-search-shell input[type='search']::placeholder{color:#7187a2}
        .model-search-controls{display:flex;gap:12px;flex-wrap:wrap;align-items:stretch;justify-content:space-between}
        .model-cloud-toggle{display:flex;align-items:center;gap:14px;padding:12px 14px;border-radius:18px;border:1px solid rgba(51,65,85,.72);background:rgba(8,15,28,.78);color:#d6e7f8;min-height:60px;flex:1 1 260px}
        .model-cloud-toggle input{accent-color:#38bdf8;width:18px;height:18px;flex:0 0 auto}
        .model-cloud-toggle-copy{display:grid;gap:3px}
        .model-cloud-toggle-copy strong{font-size:1rem;line-height:1.15;color:#f8fafc}
        .model-cloud-toggle-copy small{font-size:.8rem;line-height:1.45;color:#93aeca}
        .model-search-submit{min-width:164px;align-self:stretch}
        .model-results-grid{display:grid;grid-template-columns:1fr;gap:14px}
        .model-result-shell{display:grid;gap:12px}
        .model-result-card{position:relative;padding:18px;border-radius:22px;border:1px solid rgba(51,65,85,.82);background:linear-gradient(180deg,rgba(3,10,20,.96),rgba(6,15,28,.92));box-shadow:inset 0 1px 0 rgba(148,163,184,.04);cursor:pointer;transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease,background .18s ease;color:#f8fafc;text-align:left}
        .model-result-card:hover{transform:translateY(-2px);border-color:rgba(56,189,248,.34);box-shadow:0 18px 32px rgba(2,6,23,.32),inset 0 1px 0 rgba(148,163,184,.06)}
        .model-result-card.active{border-color:rgba(56,189,248,.44);background:linear-gradient(180deg,rgba(7,17,31,.98),rgba(8,20,34,.95));box-shadow:0 0 0 1px rgba(56,189,248,.16),0 22px 36px rgba(2,6,23,.34)}
        .model-result-card.is-loading{border-color:rgba(125,211,252,.44);box-shadow:0 0 0 1px rgba(125,211,252,.12),0 22px 36px rgba(2,6,23,.30)}
        .model-result-card.is-loading::after{content:"";position:absolute;top:18px;right:18px;width:16px;height:16px;border-radius:999px;border:2px solid rgba(125,211,252,.28);border-top-color:#7dd3fc;border-right-color:#22c55e;animation:spin .85s linear infinite}
        .model-result-card.is-loading .model-result-title{padding-right:26px}
        .model-result-title{display:block;margin-bottom:8px;color:#f8fafc;font-size:1rem}
        .model-result-summary{display:block;color:#dbeafe;line-height:1.7}
        .model-cap-badges,.model-meta-row,.job-progress-meta,.model-card-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
        .model-chip{display:inline-flex;align-items:center;padding:6px 10px;border-radius:999px;background:rgba(30,41,59,.82);border:1px solid rgba(51,65,85,.6);color:#dbeafe;font-size:.78rem;font-weight:600}
        .model-chip.cloud{background:rgba(99,102,241,.16);color:#c7d2fe;border-color:rgba(99,102,241,.22)}
        .model-chip.local{background:rgba(34,197,94,.12);color:#bbf7d0;border-color:rgba(34,197,94,.22)}
        .model-chip.selected{background:rgba(37,99,235,.18);color:#dbeafe;border-color:rgba(96,165,250,.3)}
        .model-meta-row{margin-top:12px;color:#8fa7c4;font-size:.82rem}
        .model-meta-row span{display:inline-flex;padding:5px 10px;border-radius:999px;background:rgba(8,15,28,.72);border:1px solid rgba(30,41,59,.86)}
        .model-inline-detail{display:grid;gap:14px;padding:18px;border-radius:22px;border:1px solid rgba(56,189,248,.22);background:linear-gradient(180deg,rgba(5,13,24,.95),rgba(7,18,32,.94));box-shadow:0 18px 36px rgba(2,6,23,.24), inset 0 1px 0 rgba(148,163,184,.04)}
        .model-inline-detail[hidden]{display:none !important}
        .model-detail-card,.download-job{padding:18px;border-radius:24px;border:1px solid rgba(51,65,85,.76);background:linear-gradient(180deg,rgba(4,11,22,.94),rgba(7,16,30,.92));box-shadow:inset 0 1px 0 rgba(148,163,184,.04)}
        .model-detail-header,.model-inline-detail-header,.model-detail-list,.model-detail-loading,.model-variant-list{display:grid;gap:12px}
        .model-detail-loading{justify-items:center;text-align:center;padding:24px 8px}
        .model-detail-loading-spinner{width:34px;height:34px;border-radius:999px;border:3px solid rgba(56,189,248,.18);border-top-color:#38bdf8;border-right-color:#22c55e;animation:spin .85s linear infinite}
        .model-inline-actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
        .local-model-list{display:grid;gap:12px}
        .local-model-card{display:grid;gap:10px;align-content:start;padding:16px;border-radius:20px;border:1px solid rgba(51,65,85,.72);background:linear-gradient(180deg,rgba(3,10,20,.92),rgba(6,14,27,.92));box-shadow:inset 0 1px 0 rgba(148,163,184,.03)}
        .local-model-card.selected{border-color:rgba(56,189,248,.34);background:linear-gradient(180deg,rgba(7,17,31,.98),rgba(8,20,34,.95));box-shadow:0 0 0 1px rgba(56,189,248,.14),inset 0 1px 0 rgba(148,163,184,.05)}
        .local-model-title{display:block;color:#f8fafc;font-size:.98rem}
        .local-model-meta{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
        .local-model-copy{display:block;color:#c7d2fe;line-height:1.6}
        .model-empty-state{padding:30px;border-radius:20px;border:1px dashed rgba(71,85,105,.64);text-align:center;color:#9bb1cc;background:rgba(4,10,19,.58)}
        .job-progress-track{height:10px;border-radius:999px;background:#09111d;border:1px solid #1f2937;overflow:hidden;margin-top:14px}
        .job-progress-fill{height:100%;background:linear-gradient(90deg,#22c55e,#38bdf8)}
        .job-progress-fill.failed{background:linear-gradient(90deg,#ef4444,#f97316)}
        .model-load-more-wrap{display:flex;justify-content:center}
        .help-card-grid{display:grid;gap:18px}
        .help-guide-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
        .help-guide-link{display:grid;gap:8px;padding:16px 18px;border-radius:18px;border:1px solid rgba(51,65,85,.72);background:linear-gradient(180deg,rgba(4,11,22,.92),rgba(7,16,30,.9));color:#dbeafe;text-decoration:none;box-shadow:inset 0 1px 0 rgba(148,163,184,.04);transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}
        .help-guide-link strong{color:#f8fafc;font-size:1rem}
        .help-guide-link span{color:#9fb4cf;line-height:1.6}
        .help-guide-link:hover{transform:translateY(-1px);border-color:rgba(56,189,248,.32);box-shadow:0 14px 28px rgba(2,6,23,.2), inset 0 1px 0 rgba(148,163,184,.05)}
        .help-faq-shell{padding:18px;border-radius:24px;border:1px solid rgba(51,65,85,.72);background:linear-gradient(180deg,rgba(4,11,22,.9),rgba(7,16,30,.9));box-shadow:inset 0 1px 0 rgba(148,163,184,.03)}
        .help-faq-head{display:flex;gap:12px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap}
        .help-faq-head h3{margin:0 0 6px;color:#f8fafc}
        .help-faq-head p{margin:0;color:#9fb4cf;line-height:1.6}
        .help-faq-actions{display:flex;gap:10px;flex-wrap:wrap}
        .help-faq-list{display:grid;gap:12px;margin-top:18px}
        .help-faq-item{border:1px solid rgba(51,65,85,.72);border-radius:18px;background:linear-gradient(180deg,rgba(3,10,20,.94),rgba(7,16,30,.9));overflow:hidden}
        .help-faq-item summary{list-style:none;cursor:pointer;padding:14px 18px;font-weight:700;color:#f8fafc;display:flex;align-items:center;justify-content:space-between;gap:12px}
        .help-faq-item summary::-webkit-details-marker{display:none}
        .help-faq-item summary::after{content:"+";font-size:1.15rem;color:#7dd3fc;line-height:1}
        .help-faq-item[open] summary::after{content:"-"}
        .help-faq-answer{padding:0 18px 16px;color:#cfe2f7;line-height:1.65}
        .help-faq-answer p{margin:0 0 10px}
        .help-faq-answer p:last-child{margin-bottom:0}
        .agent-editor-card,.speech-settings-shell{position:relative;overflow:hidden;border:1px solid rgba(56,189,248,.14);border-radius:24px;background:
          radial-gradient(circle at top left,rgba(56,189,248,.10),transparent 30%),
          linear-gradient(180deg,rgba(7,14,27,.96),rgba(5,11,21,.96));box-shadow:0 20px 44px rgba(2,6,23,.24),inset 0 1px 0 rgba(148,163,184,.04)}
        .agent-editor-card{margin-top:18px;padding:20px}
        .agent-editor-header,.speech-settings-header,.speech-section-header{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}
        .agent-editor-title,.speech-section-title{display:grid;gap:8px}
        .agent-editor-title h3,.speech-settings-shell h3,.speech-settings-shell h2,.speech-section-title h3{margin:0;color:#f8fafc}
        .agent-editor-title p,.speech-section-title p{margin:0;color:#9fb4cf;line-height:1.65;max-width:62ch}
        .agent-editor-meta,.speech-inline-metrics,.speech-guide-actions,.speech-section-actions,.agent-editor-actions,.speech-model-actions,.speech-form-actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
        .agent-editor-meta span,.speech-inline-metrics span{display:inline-flex;align-items:center;padding:8px 12px;border-radius:999px;border:1px solid rgba(51,65,85,.74);background:rgba(8,15,28,.78);color:#dce9f8;font-size:.82rem}
        .agent-editor-shell{display:grid;gap:16px;margin-top:18px;padding:16px;border-radius:22px;border:1px solid rgba(51,65,85,.72);background:linear-gradient(180deg,rgba(6,13,25,.92),rgba(4,10,19,.92))}
        .agent-structure-bar{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap;padding:16px;border-radius:20px;border:1px solid rgba(51,65,85,.72);background:rgba(8,15,28,.76)}
        .agent-structure-copy{display:grid;gap:6px;max-width:70ch}
        .agent-structure-copy strong{color:#f8fafc}
        .agent-structure-copy span{color:#9fb4cf;line-height:1.6}
        .agent-section-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
        .agent-section-actions{display:flex;justify-content:flex-end}
        .agent-section-card{display:grid;gap:12px;padding:16px;border-radius:20px;border:1px solid rgba(51,65,85,.72);background:linear-gradient(180deg,rgba(5,12,23,.94),rgba(7,16,30,.90))}
        .agent-section-card.readonly{background:linear-gradient(180deg,rgba(8,14,26,.84),rgba(8,14,26,.78))}
        .agent-section-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}
        .agent-section-head strong{color:#f8fafc}
        .agent-section-head p{margin:6px 0 0;color:#9fb4cf;font-size:.88rem;line-height:1.55;max-width:44ch}
        .agent-section-badge{display:inline-flex;align-items:center;padding:6px 10px;border-radius:999px;border:1px solid rgba(51,65,85,.74);background:rgba(8,15,28,.76);font-size:.78rem;color:#dce9f8}
        .agent-section-badge.editable{border-color:rgba(56,189,248,.28);background:rgba(14,165,233,.12);color:#dbeafe}
        .agent-section-badge.managed{border-color:rgba(245,158,11,.24);background:rgba(245,158,11,.10);color:#fde68a}
        .agent-section-input{width:100%;min-height:152px;padding:14px 16px;border-radius:18px;border:1px solid #27384f;background:#06101d;color:#eef6ff;font-family:Consolas,"Cascadia Code","SFMono-Regular",monospace;font-size:13px;line-height:1.6;resize:vertical}
        .agent-section-input[readonly]{background:#0b1625;color:#a9bdd6}
        .agent-section-empty{padding:18px;border-radius:18px;border:1px dashed rgba(51,65,85,.84);background:rgba(8,15,28,.62);color:#9fb4cf;line-height:1.6}
        .agent-editor-toolbar{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}
        .agent-editor-toolbar-bottom{justify-content:flex-end}
        .agent-editor-caption{color:#8ea5c2;font-size:.88rem;line-height:1.55;max-width:58ch}
        .agent-editor-textarea{width:100%;min-height:340px;padding:18px 20px;border-radius:20px;border:1px solid #27384f;background:#06101d;color:#eef6ff;font-family:Consolas,"Cascadia Code","SFMono-Regular",monospace;font-size:14px;line-height:1.72;resize:vertical;box-shadow:inset 0 1px 0 rgba(148,163,184,.04)}
        .agent-editor-textarea::placeholder{color:#6d839f}
        .agent-builder-scaffold{margin-top:16px;padding:18px;border-radius:22px;border:1px solid rgba(56,189,248,.16);background:linear-gradient(180deg,rgba(8,17,31,.94),rgba(6,13,25,.92));box-shadow:0 16px 32px rgba(2,6,23,.22),inset 0 1px 0 rgba(148,163,184,.04)}
        .agent-builder-scaffold-header{display:flex;flex-wrap:wrap;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:10px}
        .agent-builder-scaffold-title h4{margin:0;color:#f8fafc}
        .agent-builder-scaffold-title p{margin:4px 0 0;color:#9fb4cf;line-height:1.6;max-width:62ch}
        .agent-builder-pill{display:inline-flex;align-items:center;min-height:30px;padding:0 12px;border-radius:999px;border:1px solid rgba(248,113,113,.28);background:rgba(127,29,29,.22);color:#fecaca;font-size:.78rem;font-weight:800}
        .agent-builder-pill.is-ready{border-color:rgba(34,197,94,.26);background:rgba(34,197,94,.12);color:#bbf7d0}
        .agent-builder-help{margin:0 0 14px;color:#9fb4cf;line-height:1.6}
        .agent-builder-form-shell{position:relative;display:grid;gap:14px;transition:opacity .16s ease}
        .agent-builder-form-shell.is-disabled{opacity:.68}
        .agent-builder-form-shell.is-disabled::after{content:"";position:absolute;inset:-2px;border-radius:20px;background:rgba(148,163,184,.12);border:1px dashed rgba(148,163,184,.18);pointer-events:none}
        .agent-builder-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
        .agent-builder-field label{display:block;font-size:.85rem;margin-bottom:6px;color:#eef6ff;font-weight:700}
        .agent-builder-field label span{color:#8ea5c2;font-weight:500}
        .agent-builder-field input{width:100%;min-height:46px;padding:10px 12px;border-radius:12px;border:1px solid #27384f;background:#06101d;color:#eef6ff;box-sizing:border-box;box-shadow:inset 0 1px 0 rgba(148,163,184,.04)}
        .agent-builder-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
        .agent-builder-status{font-size:.85rem;color:#8ea5c2}
        .speech-settings-shell{margin-top:16px;padding:20px}
        .speech-settings-header{margin-bottom:18px}
        .speech-settings-subtitle{margin:0;color:#a8bdd8;line-height:1.72;max-width:70ch}
        .speech-summary-card,.speech-surface-card,.speech-stt-library,.speech-job-card{width:100%;max-width:100%;min-width:0;padding:18px;border-radius:22px;border:1px solid rgba(51,65,85,.76);background:linear-gradient(180deg,rgba(4,11,22,.95),rgba(7,16,30,.92));box-shadow:inset 0 1px 0 rgba(148,163,184,.04)}
        .speech-summary-card{margin-bottom:18px}
        .speech-summary-lead{display:block;margin-bottom:8px;color:#f8fafc}
        .speech-guide-actions{justify-content:flex-end}
        .speech-form-grid{display:grid;gap:18px}
        .speech-form-grid > *,.speech-field-grid > *,#speech-model-catalog > *,.speech-installed-list > *{min-width:0}
        .speech-field-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
        .speech-settings-shell label{display:block;margin-bottom:8px;font-weight:700;color:#e8f2ff}
        .speech-settings-shell input,.speech-settings-shell select,.speech-settings-shell textarea{width:100%;max-width:100%;min-width:0;min-height:52px;padding:12px 14px;border-radius:16px;border:1px solid #27384f;background:#081222;color:#f8fafc;box-shadow:inset 0 1px 0 rgba(148,163,184,.05)}
        .speech-settings-shell textarea{min-height:120px;line-height:1.65}
        .speech-settings-shell small{color:#96acc8;line-height:1.55}
        .speech-inline-note{display:flex;gap:12px;align-items:flex-start;padding:14px 16px;border-radius:18px;border:1px solid rgba(56,189,248,.18);background:rgba(8,15,28,.72);color:#d8e8fa;line-height:1.6}
        .speech-inline-note > div,.speech-section-title,.speech-section-title h3,.speech-section-title p,.speech-quick-save .muted{min-width:0;overflow-wrap:anywhere;word-break:break-word}
        .speech-dot{width:10px;height:10px;margin-top:6px;border-radius:999px;background:#38bdf8;box-shadow:0 0 18px rgba(56,189,248,.55);flex:0 0 auto}
        .speech-guide-actions .admin-feature-btn,.speech-section-actions .admin-feature-btn,.agent-editor-actions .admin-feature-btn,.speech-form-actions .admin-feature-btn,.speech-model-actions .admin-feature-btn{min-height:46px}
        .speech-quick-save{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-top:18px;padding-top:16px;border-top:1px solid rgba(51,65,85,.72)}
        .speech-model-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin-top:14px}
        .speech-model-card{display:grid;gap:12px;min-width:0;padding:16px;border-radius:20px;border:1px solid rgba(51,65,85,.72);background:linear-gradient(180deg,rgba(3,10,20,.94),rgba(6,14,27,.92));box-shadow:inset 0 1px 0 rgba(148,163,184,.03)}
        .speech-model-card > div{min-width:0}
        .speech-model-card strong{color:#f8fafc}
        .speech-model-card p{margin:0;color:#d7e6f8;line-height:1.6;overflow-wrap:anywhere;word-break:break-word}
        .speech-model-path{font-size:.88rem;overflow-wrap:anywhere;word-break:break-all}
        .speech-model-meta{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
        .speech-model-actions{display:flex;gap:10px;flex-wrap:wrap;align-items:stretch}
        .speech-model-actions .admin-feature-btn{flex:1 1 180px;min-width:0}
        .speech-model-chip{display:inline-flex;align-items:center;padding:6px 10px;border-radius:999px;border:1px solid rgba(51,65,85,.74);background:rgba(8,15,28,.76);font-size:.79rem;color:#dce9f8}
        .speech-model-chip.active{border-color:rgba(56,189,248,.28);background:rgba(14,165,233,.14);color:#e0f2fe}
        .speech-model-chip.local{border-color:rgba(34,197,94,.22);background:rgba(34,197,94,.12);color:#bbf7d0}
        .speech-model-chip.warn{border-color:rgba(245,158,11,.22);background:rgba(245,158,11,.10);color:#fde68a}
        .speech-installed-list,.speech-job-list{display:grid;gap:12px;margin-top:16px}
        .speech-job-copy{display:grid;gap:4px}
        .speech-job-copy strong{color:#f8fafc}
        .speech-job-copy span{color:#cfe2f7;line-height:1.55}
        .rtc-settings-shell{position:relative;overflow:hidden;border:1px solid rgba(56,189,248,.14);border-radius:24px;background:
          radial-gradient(circle at top left,rgba(56,189,248,.10),transparent 28%),
          linear-gradient(180deg,rgba(7,14,27,.95),rgba(5,11,21,.96));box-shadow:0 20px 44px rgba(2,6,23,.24),inset 0 1px 0 rgba(148,163,184,.04)}
        .rtc-import-card{margin-bottom:16px;padding:18px;border-radius:20px;border:1px solid rgba(51,65,85,.76);background:linear-gradient(180deg,rgba(9,17,30,.94),rgba(8,14,25,.92));box-shadow:inset 0 1px 0 rgba(148,163,184,.04)}
        .rtc-import-card label,.rtc-settings-shell label{display:block;margin-bottom:8px;font-weight:700;color:#e8f2ff}
        .rtc-import-card textarea,.rtc-settings-shell textarea{width:100%;border-radius:16px;border:1px solid #27384f;background:#081222;color:#f8fafc;padding:14px;box-shadow:inset 0 1px 0 rgba(148,163,184,.05)}
        .rtc-import-card textarea{min-height:160px}
        .rtc-settings-shell .muted{color:#9fb4cf}
        .rtc-save-actions{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-top:10px}
        .rtc-save-actions .secondary-btn{min-height:46px}
        .wizard-hidden{display:none !important}
        @media (max-width: 980px){
          .setup-wizard-frame,.model-library-grid{grid-template-columns:1fr}
          .setup-wizard-sidebar{border-right:none;border-bottom:1px solid rgba(51,65,85,.75)}
          .model-library-actions{grid-template-columns:1fr;min-width:0}
          .model-toolbar-btn{flex:1 1 100%}
          .agent-section-grid,
          .speech-field-grid{grid-template-columns:1fr}
          .speech-settings-header,
          .speech-section-header{flex-direction:column;align-items:stretch}
          .speech-section-header > *,
          .speech-settings-header > *,
          .speech-inline-metrics,
          .speech-inline-metrics span{min-width:0;max-width:100%}
          .speech-inline-metrics{display:grid;grid-template-columns:1fr;align-items:stretch}
          .speech-inline-metrics span{width:100%;overflow-wrap:anywhere;word-break:break-word}
          .speech-model-actions .admin-feature-btn,
          .speech-section-actions .admin-feature-btn,
          .speech-guide-actions .admin-feature-btn,
          .speech-form-actions .admin-feature-btn{flex:1 1 100%}
        }
      </style>
    """
    setup_wizard_styles = setup_wizard_styles.replace(
        "      </style>",
        f"{agent_studio_css}\n      </style>",
        1,
    )
    wizard_launcher_html = """
      <div class='card setup-hero-card' id='wizard-launcher-card'>
        <div class='setup-hero-actions' style='justify-content:space-between;align-items:flex-start'>
          <div style='max-width:760px'>
            <div class='wizard-kicker'>Guided Setup</div>
            <h1 style='margin-top:12px'>First-run wizard, model downloads, and connectivity checks</h1>
            <p class='muted' style='max-width:66ch'>Walk through password safety, AI provider setup, model downloads, messaging partner pairing, browser tunnel and secure relay setup, speech, and the final setup check without typing model names into AI Agent Settings.</p>
          </div>
          <div class='wizard-inline-actions'>
            <button type='button' class='secondary-btn wizard-launcher-action' onclick='openSetupWizard()'>Open Setup Wizard</button>
            <a class='secondary-btn wizard-launcher-action' href='/?advanced=1' style='display:inline-flex;align-items:center;text-decoration:none'>Advanced Settings</a>
          </div>
        </div>
        <div class='setup-pill-row' style='margin-top:16px'>
          <div class='setup-pill'><strong>Password</strong><span id='wizard-launcher-password'>Checking...</span></div>
          <div class='setup-pill'><strong>AI Provider</strong><span id='wizard-launcher-ollama'>Checking...</span></div>
          <div class='setup-pill'><strong>Messaging</strong><span id='wizard-launcher-messaging'>Checking...</span></div>
          <div class='setup-pill'><strong>Connectivity</strong><span id='wizard-launcher-connectivity'>Checking...</span></div>
        </div>
      </div>
    """ if show_onboarding_wizard else ""
    wizard_overlay_html = """
      <div id='setup-wizard-overlay' class='setup-wizard-overlay' style='__DISPLAY__'>
        <div class='setup-wizard-frame'>
          <aside class='setup-wizard-sidebar'>
            <div class='wizard-kicker'>Initial Boot Guide</div>
            <h2 style='margin-top:14px'>AutoYou setup wizard</h2>
            <p class='wizard-lede'>Local-first onboarding that keeps the existing advanced page intact while guiding the first-time flow.</p>
            <div class='wizard-progress-shell'>
              <div class='wizard-progress-label'>
                <span id='wizard-step-label'>Step 1 of 8</span>
                <span id='wizard-overall-status'>Needs setup</span>
              </div>
              <div class='wizard-progress-track'><div id='wizard-progress-fill' class='wizard-progress-fill'></div></div>
            </div>
            <div class='wizard-step-list'>
              <button class='wizard-step-btn active' type='button' data-step='0' onclick='showWizardStep(0)'>1. Security Basics</button>
              <button class='wizard-step-btn' type='button' data-step='1' onclick='showWizardStep(1)'>2. AI Provider</button>
              <button class='wizard-step-btn' type='button' data-step='2' onclick='showWizardStep(2)'>3. Choose Model</button>
              <button class='wizard-step-btn' type='button' data-step='3' onclick='showWizardStep(3)'>4. Messaging Partner</button>
              <button class='wizard-step-btn' type='button' data-step='4' onclick='showWizardStep(4)'>5. Connectivity Setup</button>
              <button class='wizard-step-btn' type='button' data-step='5' onclick='showWizardStep(5)'>6. Secure Mode & 2FA</button>
              <button class='wizard-step-btn' type='button' data-step='6' onclick='showWizardStep(6)'>7. Speech Settings</button>
              <button class='wizard-step-btn' type='button' data-step='7' onclick='showWizardStep(7)'>8. Finish Setup</button>
            </div>
            <div class='wizard-inline-actions'>
              <button type='button' class='secondary-btn' data-no-loading='1' onclick='skipToAdvancedSettings()'>Skip to Advanced Settings</button>
              <button type='button' class='secondary-btn' onclick='completeSetupWizard()'>Mark Complete</button>
            </div>
          </aside>
          <section class='setup-wizard-main'>
            <div class='wizard-topbar'>
              <div>
                <h2 id='wizard-panel-title'>Security Basics</h2>
                <p id='wizard-panel-subtitle' class='muted'>Change the default password before exposing AutoYou beyond localhost.</p>
              </div>
              <div class='wizard-topbar-actions'>
                <button type='button' class='secondary-btn' onclick='showWizardStep(window.autoyouWizardStep - 1)' id='wizard-prev-btn'>Previous</button>
                <button type='button' class='secondary-btn' onclick='showWizardStep(window.autoyouWizardStep + 1)' id='wizard-next-btn'>Next</button>
              </div>
            </div>

            <div class='wizard-step-panel active' data-step='0'>
              <div class='wizard-callout'>AutoYou intentionally boots with the default password on first run so the admin UI is reachable. Keeping <code>autoyou123</code> after setup is risky once any remote path is enabled.</div>
              <div class='wizard-stat-grid'>
                <div class='wizard-mini-card'><strong>Password status</strong><span id='wizard-password-status'>Checking...</span></div>
                <div class='wizard-mini-card'><strong>Security mode</strong><span id='wizard-security-status'>Checking...</span></div>
                <div class='wizard-mini-card'><strong>2FA</strong><span id='wizard-totp-status'>Checking...</span></div>
              </div>
              <div class='wizard-inline-actions' style='margin-top:16px'>
                <button type='button' class='secondary-btn' onclick='openAdvancedSection("password-settings-card")'>Open Password Settings</button>
                <button type='button' class='secondary-btn' onclick='openAdvancedSection("security-settings-card")'>Open Security Settings</button>
              </div>
              <p class='wizard-note' style='margin-top:16px'>Secure mode with a changed password is the default baseline for local-first use. For maximum protection, configure Secure Professional mode and enroll an authenticator app in the next steps.</p>
            </div>

            <div class='wizard-step-panel' data-step='1'>
              <p class='wizard-lede' style='margin:0 0 18px'>AutoYou supports four AI backends. <strong>Ollama is the recommended local default</strong> - it runs entirely on your device with no internet access required. Cloud providers are available for higher capability or when Ollama is unavailable.</p>

              <!-- Provider option tiles -->
              <style>
              .wiz-prov-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-bottom:24px}}
              .wiz-prov-tile{{
                display:flex;flex-direction:column;gap:6px;
                padding:16px 18px;
                border:1px solid var(--stroke);
                border-radius:14px;
                background:var(--card-nested, rgba(15,23,42,.5));
                cursor:default;transition:border-color .18s ease,box-shadow .18s ease;
              }}
              .wiz-prov-tile:hover{{border-color:var(--accent-soft,rgba(56,189,248,.35));box-shadow:0 4px 14px rgba(56,189,248,.06)}}
              .wiz-prov-tile-icon{{font-size:1.6em;line-height:1;font-family:"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif}}
              .wiz-prov-tile-name{{font-weight:700;font-size:.93em;color:var(--text,#f1f5f9);margin-top:2px}}
              .wiz-prov-tile-desc{{font-size:.8em;color:var(--muted,#94a3b8);line-height:1.55;margin-top:2px}}
              @media(max-width:480px){{.wiz-prov-grid{{grid-template-columns:1fr}}}}
              /* ── Ollama Quick Setup panel ── */
              .wiz-ollama-quick{{
                border:1px solid rgba(56,189,248,.16);
                border-radius:14px;
                padding:18px 20px;
                margin-bottom:16px;
              }}
              .wiz-ollama-quick-title{{
                font-weight:700;font-size:.95em;
                color:var(--text,#f1f5f9);
                margin-bottom:14px;
                display:flex;align-items:center;gap:8px;
              }}
              :root[data-theme="light"] .wiz-prov-tile{{
                background:linear-gradient(180deg,rgba(248,250,252,.98),rgba(241,245,249,.96));
                border-color:rgba(148,163,184,.3);
                box-shadow:0 2px 8px rgba(148,163,184,.08);
              }}
              :root[data-theme="light"] .wiz-prov-tile:hover{{
                border-color:rgba(37,99,235,.35);box-shadow:0 4px 14px rgba(37,99,235,.07);
              }}
              :root[data-theme="light"] .wiz-ollama-quick{{
                background:linear-gradient(135deg,rgba(219,234,254,.22),rgba(239,246,255,.18));
                border-color:rgba(96,165,250,.28);
              }}
              </style>
              <div class='wiz-prov-grid'>
                <div class='wiz-prov-tile'>
                  <div class='wiz-prov-tile-icon'>&#x1F5A5;&#xFE0F;</div>
                  <div class='wiz-prov-tile-name'>Ollama <span style='color:#22c55e;font-size:.75em;font-weight:400'>(recommended)</span></div>
                  <div class='wiz-prov-tile-desc'>Runs locally on your device. Install Ollama first, then choose or download a model in the next step. No API key is needed for local-only use.</div>
                </div>
                <div class='wiz-prov-tile'>
                  <div class='wiz-prov-tile-icon'>&#x1F52D;</div>
                  <div class='wiz-prov-tile-name'>OpenClaw</div>
                  <div class='wiz-prov-tile-desc'>Local gateway that routes to Ollama, Anthropic, or other backends you control.</div>
                </div>
                <div class='wiz-prov-tile'>
                  <div class='wiz-prov-tile-icon'>&#x2601;&#xFE0F;</div>
                  <div class='wiz-prov-tile-name'>LiteLLM Cloud</div>
                  <div class='wiz-prov-tile-desc'>Hosted model providers such as Anthropic, OpenAI, Mistral, DeepSeek, and xAI. Needs an API key.</div>
                </div>
                <div class='wiz-prov-tile'>
                  <div class='wiz-prov-tile-icon'>&#x1F537;</div>
                  <div class='wiz-prov-tile-name'>Google Gemini</div>
                  <div class='wiz-prov-tile-desc'>Google AI integration with Gemini models through Google AI Studio or Vertex AI.</div>
                </div>
              </div>

              <!-- Provider readiness panel -->
              <div class='wiz-ollama-quick'>
                <div class='wiz-ollama-quick-title'>&#x26A1; Provider readiness</div>
                <div class='wizard-stat-grid'>
                  <div class='wizard-mini-card'><strong>Active provider</strong><span id='wizard-active-provider'>Checking...</span></div>
                  <div class='wizard-mini-card'><strong>Runtime or gateway</strong><span id='wizard-ollama-runtime-status'>Checking...</span></div>
                  <div class='wizard-mini-card'><strong>Endpoint</strong><span id='wizard-ollama-api-base'>Checking...</span></div>
                </div>
                <div class='wizard-inline-actions' style='margin-top:14px;gap:8px'>
                  <button type='button' class='secondary-btn' onclick='openAdvancedSection("ollama-settings-card")'>Configure AI Provider</button>
                  <button type='button' class='secondary-btn' onclick='openAdvancedSection("model-library-card")'>Open Model Library</button>
                  <button type='button' class='secondary-btn' onclick='refreshWizardStatus()'>Refresh</button>
                </div>
              </div>

              <p class='wizard-note' style='margin-top:14px;padding:12px 14px;background:rgba(56,189,248,.06);border-radius:8px;border-left:3px solid rgba(56,189,248,.24)'><strong style='color:#bae6fd'>💡 Pro tip:</strong> Confirm Ollama is installed and at least one local model exists before proceeding. Using a cloud provider instead? Enter your API key in AI Provider Settings and skip the Ollama steps.</p>
            </div>

            <div class='wizard-step-panel' data-step='2'>
              <div style='margin-bottom:20px'>
                <p class='wizard-note' style='margin:0 0 14px 0;font-size:.88em'>Choose your remote connection method. AutoYou Cloud Pair is the premium option for seamless iOS & Android connectivity.</p>
              </div>
              
              <div style='background:rgba(168,85,247,.04);border:1px solid rgba(168,85,247,.15);border-radius:14px;padding:16px;margin-bottom:18px'>
                <div style='display:flex;align-items:center;gap:8px;margin-bottom:12px'>
                  <strong style='color:#e9d5ff'>AutoYou Cloud Pair</strong>
                  <span class='wizard-badge premium'>PREMIUM</span>
                </div>
                <span class='wizard-note' style='margin-bottom:12px;display:block'>Secure cloud-based pairing. Direct connection from iOS & Android without message switching. One-click access to your computer.</span>
                <span id='wizard-cloud-pair-status' style='display:block;margin-bottom:12px;font-size:.85em'>Checking...</span>
                <div class='wizard-inline-actions' style='margin-top:8px'>
                  <a href='https://autoyou.me/' target='_blank' class='secondary-btn' style='display:inline-flex;align-items:center;text-decoration:none'>Visit AutoYou.me</a>
                </div>
              </div>
              
              <p class='wizard-note' style='margin:0 0 10px 0;font-size:.85em;color:#9fb3cf'>Or use free messaging partners:</p>
              <div class='partner-grid'>
                <div class='partner-card'><strong>Telegram bot</strong><span id='wizard-telegram-status'>Checking...</span><div class='wizard-inline-actions' style='margin-top:10px'><button type='button' class='secondary-btn' onclick='openAdvancedSection("telegram-settings-card")'>Open Telegram</button><a class='wizard-link' href='/guides/telegram' target='_blank'>Guide</a></div></div>
                <div class='partner-card'><strong>Signal</strong><span id='wizard-signal-status'>Checking...</span><div class='wizard-inline-actions' style='margin-top:10px'><button type='button' class='secondary-btn' onclick='openAdvancedSection("signal-settings-card")'>Open Signal</button><a class='wizard-link' href='/guides/signal' target='_blank'>Guide</a></div></div>
                <div class='partner-card'><strong>WhatsApp</strong><span id='wizard-whatsapp-status'>Checking...</span><div class='wizard-inline-actions' style='margin-top:10px'><button type='button' class='secondary-btn' onclick='openAdvancedSection("whatsapp-settings-card")'>Open WhatsApp</button><a class='wizard-link' href='/guides/whatsapp' target='_blank'>Guide</a></div></div>
              </div>
              <div class='wizard-callout'>AutoYou needs at least one working connection method for remote control from Android or iOS. Cloud Pair, messaging partners, or local-only access through the local web interface are all valid approaches.</div>
              <div class='wizard-choice-grid'>
                <div class='wizard-choice-card'><strong>AutoYou Cloud Pair</strong><span>Cloud Pair removes the complexity of messaging partner setup and provides direct, secure access from iOS & Android without swapping apps.</span></div>
                <div class='wizard-choice-card'><strong>Local-only fallback</strong><span>You can still use AutoYou via your messaging partners or or via AutoYou iOS/Android app or by using AutoYou Connect Windows/macOS app or just the local web interface at<span id='wizard-local-adk-url'>http://127.0.0.1:8081/dev-ui/</span>.</span></div>
              </div>
            </div>

            <div class='wizard-step-panel' data-step='3'>
              <div class='wizard-stat-grid'>
                <div class='wizard-mini-card'><strong>Browser tunnel</strong><span id='wizard-tunnelmole-status'>Checking...</span></div>
                <div class='wizard-mini-card'><strong>Public URL</strong><span id='wizard-tunnelmole-url'>Checking...</span></div>
                <div class='wizard-mini-card'><strong>Call connectivity servers saved</strong><span id='wizard-ice-count'>Checking...</span></div>
              </div>
              <p class='wizard-note' style='margin-top:16px'>Start with the public link so the app can reach your server remotely. If your host blocks direct call setup, add a connection helper bundle from your provider below.</p>
              <form method='post' action='/save-config' class='wizard-inline-shell' onsubmit='persistWizardResume(window.autoyouWizardStep)'>
                <input type='hidden' name='kind' value='tunnelmole'>
                <input type='hidden' name='tunnelmole_timeout' value='__WIZARD_TM_TIMEOUT__'>
                <input type='hidden' name='tunnelmole_otp_timeout' value='__WIZARD_TM_OTP_TIMEOUT__'>
                __WIZARD_TM_MULTIUSE__
                <input type='hidden' name='tunnelmole_pair_code_mode' value='__WIZARD_TM_PAIR_CODE_MODE__'>
                <input type='hidden' name='tunnelmole_connection_mode' value='__WIZARD_TM_CONNECTION_MODE__'>
                <label class='wizard-inline-checkbox'>
                  <input type='checkbox' name='tunnelmole_enabled' value='true' __WIZARD_TM_ENABLED__>
                  <span>
                    <strong>Enable Public Reverse Proxy</strong>
                    <span>Turns on the public browser tunnel so iPhone, Android, and other remote clients can reach this server before you need extra connection helper details.</span>
                  </span>
                </label>
                <div class='wizard-action-grid' style='margin-top:12px'>
                  <button type='submit' class='secondary-btn'>Save Browser Tunnel</button>
                  <button type='button' class='secondary-btn' onclick='openAdvancedSection("tunnelmole-settings-card")'>Open Tunnel Settings</button>
                </div>
              </form>
              <textarea id='wizard-ice-input' class='wizard-textarea' placeholder='Paste provider details, helper JSON, an env var assignment, or connection server URLs here.'></textarea>
              <div class='wizard-action-grid' style='margin-top:14px'>
                <button type='button' class='secondary-btn' onclick='applyRtcBundleFromWizard("append")'>Merge With Current Config</button>
                <button type='button' class='secondary-btn' onclick='applyRtcBundleFromWizard("replace")'>Replace Current Config</button>
                <a class='secondary-btn' href='/guides/connectivity' target='_blank' style='display:inline-flex;align-items:center;text-decoration:none'>Connectivity Guide</a>
              </div>
              <div id='wizard-rtc-feedback' class='wizard-note' style='margin-top:16px'>No connection helper bundle processed yet.</div>
            </div>

            <div class='wizard-step-panel' data-step='4'>
              <div class='wizard-callout'>Messaging partners can still observe the connection-establishment messages that bootstrap AutoYou. Secure mode helps, and secure professional mode adds 2FA for the strongest setup.</div>
              <div class='wizard-stat-grid'>
                <div class='wizard-mini-card'><strong>Current mode</strong><span id='wizard-security-mode'>Checking...</span></div>
                <div class='wizard-mini-card'><strong>Changed password</strong><span id='wizard-security-password-copy'>Checking...</span></div>
                <div class='wizard-mini-card'><strong>Authenticator ready</strong><span id='wizard-security-totp-copy'>Checking...</span></div>
              </div>
              <div class='wizard-inline-actions' style='margin-top:16px'>
                <button type='button' class='secondary-btn' onclick='openAdvancedSection("security-settings-card")'>Configure Security Mode</button>
                <button type='button' class='secondary-btn' onclick='openAdvancedSection("password-settings-card")'>Change Password</button>
              </div>
              <p class='wizard-note' style='margin-top:16px'>Telegram bot users should also restrict bot replies to their own username in normal settings, even if they stay in normal security mode.</p>
            </div>

            <div class='wizard-step-panel' data-step='5'>
              <p class='wizard-lede' style='margin:0 0 14px'>Select a model to power AutoYou. <strong>Local models are recommended</strong> - they run on your device with no subscription or internet access required.</p>
              <div class='wizard-stat-grid'>
                <div class='wizard-mini-card'><strong>Selected model</strong><span id='wizard-selected-model'>Checking...</span></div>
                <div class='wizard-mini-card'><strong>Installed models</strong><span id='wizard-installed-models'>Checking...</span></div>
                <div class='wizard-mini-card'><strong>Recommended starter</strong><span id='wizard-default-model-status'>Checking...</span></div>
              </div>
              <div class='wizard-inline-actions' style='margin-top:16px'>
                <button type='button' class='secondary-btn' id='wizard-download-recommended-btn' onclick='downloadRecommendedModel()'>Download model</button>
                <button type='button' class='secondary-btn' onclick='openAdvancedSection("model-library-card")'>Browse Model Library</button>
                <button type='button' class='secondary-btn' onclick='refreshModelLibraryLocal()'>Refresh Local Models</button>
              </div>
              <div id='wizard-download-status' class='download-job' style='margin-top:12px;padding:0;border:none;background:none'>
                <span id='wizard-download-copy' style='font-size:.85em;color:var(--muted,#94a3b8)'>No download running yet.</span>
                <div class='job-progress-track' style='margin-top:8px'><div id='wizard-download-fill' class='job-progress-fill' style='width:0%'></div></div>
              </div>
              <div style='background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.16);border-radius:8px;padding:12px 14px;margin-top:16px'>
                <span style='font-size:.85em;color:#94a3b8;display:block;line-height:1.5'><strong style='color:#fbbf24'>⚠️ Cloud models notice:</strong> Ollama cloud variants require signing into Ollama.com at the command line. For the best experience, choose a local model instead. Local models download once and run on your device without any subscription.</span>
              </div>
              <div id='wizard-local-model-preview' class='wizard-note' style='margin-top:12px'>No local models discovered yet.</div>
            </div>

            <div class='wizard-step-panel' data-step='6'>
              <div class='wizard-choice-grid'>
                <div class='wizard-choice-card'><strong>Current speech stack</strong><span>Keep the existing speech settings as-is for now, or open the speech section to choose TTS / STT providers and pre-download a faster-whisper STT model locally.</span></div>
                <div class='wizard-choice-card'><strong>Custom voices later</strong><span>You can install system voices or cloud voices separately and select them in Speech Settings. The speech guide now covers Windows, macOS, Linux, Ubuntu, and Raspberry Pi voice setup.</span></div>
              </div>
              <div class='wizard-inline-actions' style='margin-top:16px'>
                <button type='button' class='secondary-btn' onclick='openAdvancedSection("speech-settings-card")'>Open Speech Settings</button>
                <a class='secondary-btn' href='/guides/speech' target='_blank' style='display:inline-flex;align-items:center;text-decoration:none'>Speech guide</a>
              </div>
            </div>

            <div class='wizard-step-panel' data-step='7'>
              <div class='wizard-note'>Review the setup status, then finish the wizard when the local path is ready. Advanced prompt editing stays available in AI settings.</div>
              <textarea id='wizard-prompt-preview' class='wizard-prompt-preview' readonly placeholder='Loading current prompt...'></textarea>
              <div class='wizard-inline-actions' style='margin-top:14px'>
                <button type='button' class='secondary-btn' onclick='openAdvancedSection("agent-instructions-card")'>Open AI Settings</button>
                <button type='button' class='secondary-btn' onclick='completeSetupWizard()'>Finish Wizard</button>
              </div>
            </div>
          </section>
        </div>
      </div>
    """.replace("__DISPLAY__", wizard_overlay_style).replace(
      "Ollama cloud variants require signing into Ollama.com at the command line. For the best experience, choose a local model instead. Local models download once and run on your device without any subscription.",
      "Ollama Cloud variants run remotely and require <code>ollama signin</code>. For the best experience, choose a local model instead. Local models download once and stay on your device.",
    ).replace(
      "AutoYou supports four AI backends. <strong>Ollama is the recommended local default</strong> - it runs entirely on your device with no internet access required. Cloud providers are available for higher capability or when Ollama is unavailable.",
      "Choose where AutoYou should run its AI replies. <strong>Ollama is the recommended local default</strong> for private, on-device use. If you prefer OpenClaw, LiteLLM, or Google, set that provider here first, then choose the model in the next step.",
    ).replace(
      "Confirm Ollama is installed and at least one local model exists before proceeding. Using a cloud provider instead? Enter your API key in AI Provider Settings and skip the Ollama steps.",
      "Pick the provider first. Once the runtime or gateway is reachable, continue to the next step to choose the actual model AutoYou should use for replies.",
    ).replace(
      "Start with the public link so the app can reach your server remotely. If your host blocks direct call setup, add a connection helper bundle from your provider below.",
      "Start with the public link so the app can reach your server remotely. If your network blocks direct call setup, add a connection helper bundle from your provider.",
    ).replace(
      "__WIZARD_TM_TIMEOUT__",
      html.escape(str(tunnelmole_timeout), quote=True),
    ).replace(
      "__WIZARD_TM_OTP_TIMEOUT__",
      html.escape(str(tunnelmole_otp_timeout), quote=True),
    ).replace(
      "__WIZARD_TM_MULTIUSE__",
      '<input type="hidden" name="tunnelmole_otp_multiuse" value="true">' if tunnelmole_otp_multiuse else '',
    ).replace(
        "__WIZARD_TM_PAIR_CODE_MODE__",
        html.escape(tunnelmole_pair_code_mode, quote=True),
    ).replace(
        "__WIZARD_TM_CONNECTION_MODE__",
        html.escape(tunnelmole_connection_mode, quote=True),
    ).replace(
      "__WIZARD_TM_ENABLED__",
      'checked' if tunnelmole_enabled else '',
    )
    model_library_card_html = """
      <div class='card model-library-card' id='model-library-card'>
        <div class='model-library-hero'>
          <div class='model-library-heading'>
            <div class='wizard-kicker model-library-kicker'>Model Workspace</div>
            <h2 class='model-library-title'>Model Library & Downloads</h2>
            <p class='model-library-subtitle'>Search Ollama's public catalog and Hugging Face GGUF repos, download through the local Ollama runtime, and switch models without manually typing names into AI Agent Settings.</p>
          </div>
          <div class='model-library-actions'>
            <button type='button' class='model-toolbar-btn primary' onclick='refreshModelLibraryLocal()'>
              <span class='model-toolbar-label'>Refresh Installed Models</span>
              <span class='model-toolbar-meta'>Sync local Ollama runtime state and installed tags.</span>
            </button>
            <a class='model-toolbar-btn alt' href='/guides/ollama-install' target='_blank'>
              <span class='model-toolbar-label'>Install Ollama</span>
              <span class='model-toolbar-meta'>Open the local-first guide and first-run checklist.</span>
            </a>
          </div>
        </div>

        <div class='model-runtime-grid model-runtime-grid-enhanced'>
          <div class='wizard-mini-card model-runtime-card'><strong>Runtime</strong><span id='model-runtime-status'>Checking...</span></div>
          <div class='wizard-mini-card model-runtime-card'><strong>API base</strong><span id='model-runtime-base'>Checking...</span></div>
          <div class='wizard-mini-card model-runtime-card'><strong>Selected model</strong><span id='model-runtime-selected'>Checking...</span></div>
          <div class='wizard-mini-card model-runtime-card'><strong>Installed count</strong><span id='model-runtime-count'>Checking...</span></div>
        </div>

        <div id='model-download-jobs' class='download-job-list' style='margin-top:18px'></div>

        <div class='model-library-grid' style='margin-top:18px'>
          <section class='model-installed-shell'>
            <div class='model-installed-header'>
              <h3>Installed in Ollama</h3>
              <p>Keep the models that already exist on this machine visible before browsing new downloads or switching the active runtime model.</p>
            </div>
            <div id='local-model-list' class='local-model-list'></div>
          </section>

          <section class='model-browser-panel'>
            <div class='model-panel-heading'>
              <div>
                <h3>Browse catalogs</h3>
                <p>Search Ollama's library or pull Ollama-compatible GGUF references from Hugging Face. Select a single result to open its download choices directly beneath that card.</p>
              </div>
            </div>
            <div class='model-source-tabs'>
              <button type='button' class='model-source-tab active' data-source='ollama' onclick='setModelSource("ollama")'>Ollama Library</button>
              <button type='button' class='model-source-tab' data-source='huggingface' onclick='setModelSource("huggingface")'>Hugging Face GGUF</button>
            </div>
            <div class='model-search-shell'>
              <div class='model-search-input-wrap'>
                <input id='model-search-input' type='search' placeholder='Search models, repos, or families'>
              </div>
              <div class='model-search-controls'>
                <label class='model-cloud-toggle' title='Cloud models require signing into Ollama.com via CLI'>
                  <input id='model-include-cloud' type='checkbox' onchange='onCloudModelToggle(this.checked)'>
                  <span class='model-cloud-toggle-copy'>
                    <strong>Include cloud imports</strong>
                    <small>⚠️ Show cloud models (requires Ollama.com sign-in at command line). Prefer local models for hassle-free setup.</small>
                  </span>
                </label>
                <button type='button' class='model-action-btn primary model-search-submit' onclick='searchModelCatalog(false)'>Search Catalog</button>
              </div>
            </div>
            <div id='model-catalog-results' class='model-results-grid'></div>
            <div class='model-load-more-wrap' style='margin-top:16px'>
              <button type='button' id='model-load-more-btn' class='model-action-btn ghost wizard-hidden' onclick='searchModelCatalog(true)'>Load More</button>
            </div>
          </section>
        </div>
      </div>
    """.replace(
      "Cloud models require signing into Ollama.com via CLI",
      "Cloud models run remotely and require ollama signin",
    ).replace(
      "Show cloud models (requires Ollama.com sign-in at command line). Prefer local models for hassle-free setup.",
      "Show Ollama Cloud models. Run <code>ollama signin</code> first, and prefer local models when you want everything to stay on-device.",
    )
    # Prepare script separately to avoid f-string brace conflicts
    script_html = """
      <script>
       // Live Signal status updates
       let signalStatusInterval;
       let whatsappStatusInterval;
       let aiAgentStatusInterval;
       let autoYouPageStatusInterval;
       let datachannelStatusInterval;
       let notificationQueueStatusInterval;
       let signalQrRefreshTimer;
       let signalQrRequestPromise = null;
       let signalQrAutoRefreshEnabled = false;
       let signalQrLastFetchedAt = 0;
       let whatsappQrRequestPromise = null;
       let whatsappQrLastFetchedAt = 0;
       let signalStatusRequestInFlight = false;
       let whatsappStatusRequestInFlight = false;
       let aiAgentStatusRequestInFlight = false;
       let autoYouPageStatusRequestInFlight = false;
       let datachannelStatusRequestInFlight = false;
       let notificationQueueStatusRequestInFlight = false;
       let liveStatusStartupTimers = [];
       const SIGNAL_QR_REFRESH_MS = 18000;
       const WHATSAPP_QR_REFRESH_MS = 18000;
       const LIVE_STATUS_POLL_MS = 15000;
       const LIVE_STATUS_STAGGER_MS = 1200;

        function liveStatusPollingEnabled() {
          return !document.hidden;
        }

        function clearLiveStatusIntervals() {
          while (liveStatusStartupTimers.length > 0) {
            clearTimeout(liveStatusStartupTimers.pop());
          }
          if (signalStatusInterval) {
            clearInterval(signalStatusInterval);
            signalStatusInterval = null;
          }
          if (whatsappStatusInterval) {
            clearInterval(whatsappStatusInterval);
            whatsappStatusInterval = null;
          }
          if (aiAgentStatusInterval) {
            clearInterval(aiAgentStatusInterval);
            aiAgentStatusInterval = null;
          }
          if (autoYouPageStatusInterval) {
            clearInterval(autoYouPageStatusInterval);
            autoYouPageStatusInterval = null;
          }
          if (datachannelStatusInterval) {
            clearInterval(datachannelStatusInterval);
            datachannelStatusInterval = null;
          }
          if (notificationQueueStatusInterval) {
            clearInterval(notificationQueueStatusInterval);
            notificationQueueStatusInterval = null;
          }
        }

        function scheduleStatusPoll(startDelayMs, updateFn, assignInterval) {
          const timerId = window.setTimeout(() => {
            if (!liveStatusPollingEnabled()) {
              return;
            }
            updateFn();
            assignInterval(window.setInterval(() => {
              if (!liveStatusPollingEnabled()) {
                return;
              }
              updateFn();
            }, LIVE_STATUS_POLL_MS));
          }, startDelayMs);
          liveStatusStartupTimers.push(timerId);
        }

        function startLiveStatusPolling() {
          clearLiveStatusIntervals();
          if (!liveStatusPollingEnabled()) {
            return;
          }
          scheduleStatusPoll(0, updateSignalStatus, (id) => { signalStatusInterval = id; });
          scheduleStatusPoll(LIVE_STATUS_STAGGER_MS, updateWhatsAppStatus, (id) => { whatsappStatusInterval = id; });
          if (typeof updateAiAgentServerStatus === 'function') {
            scheduleStatusPoll(LIVE_STATUS_STAGGER_MS * 2, updateAiAgentServerStatus, (id) => { aiAgentStatusInterval = id; });
          }
          if (typeof updateAutoYouPageServiceStatus === 'function') {
            scheduleStatusPoll(LIVE_STATUS_STAGGER_MS * 3, updateAutoYouPageServiceStatus, (id) => { autoYouPageStatusInterval = id; });
          }
          scheduleStatusPoll(LIVE_STATUS_STAGGER_MS * 4, updateDatachannelStatus, (id) => { datachannelStatusInterval = id; });
          scheduleStatusPoll(LIVE_STATUS_STAGGER_MS * 5, refreshNotificationQueueStatus, (id) => { notificationQueueStatusInterval = id; });
        }

        function clearSignalQrRefreshTimer() {
          if (signalQrRefreshTimer) {
            clearTimeout(signalQrRefreshTimer);
            signalQrRefreshTimer = null;
          }
        }

        function scheduleSignalQrRefresh(immediate = false) {
          clearSignalQrRefreshTimer();
          if (!signalQrAutoRefreshEnabled) {
            return;
          }

          signalQrRefreshTimer = setTimeout(() => {
            signalQrRefreshTimer = null;
            if (signalQrAutoRefreshEnabled) {
              showSignalQR(true);
            }
          }, immediate ? 0 : SIGNAL_QR_REFRESH_MS);
        }
        
        async function updateSignalStatus() {
          if (!liveStatusPollingEnabled() || signalStatusRequestInFlight) {
            return;
          }
          signalStatusRequestInFlight = true;
          try {
            const response = await fetch('/api/signal/status');
            const status = await response.json();
            
            // Update status display
            const statusElement = document.querySelector('.signal-status-text');
            const qrBtn = document.getElementById('signal-qr-btn');
            const cleanupBtn = document.getElementById('signal-cleanup-btn');
            
            if (statusElement) {
              let statusText = 'Unknown';
              let statusDetail = '-';
              
              // Check if Signal is paired (has registered numbers/accounts)
              const isPaired = status.registered_numbers && status.registered_numbers.length > 0;
              const isRunning = status.status === 'running';
              const isHealthy = status.api_status === 'healthy';
              
              if (status.error) {
                statusText = 'Error';
                statusDetail = status.error.length > 50 ? status.error.substring(0, 50) + '...' : status.error;
              } else if (isPaired && isRunning && isHealthy) {
                statusText = 'Connected';
                statusDetail = status.device_name || 'Unknown';
              } else if (isRunning && isHealthy) {
                statusText = 'Ready for pairing';
                statusDetail = '-';
              } else if (status.status === 'starting') {
                statusText = 'Starting...';
                statusDetail = '-';
              } else if (status.status === 'stopped' || status.status === 'disabled') {
                statusText = 'Disabled';
                statusDetail = '-';
              } else {
                statusText = status.status || 'Unknown';
                statusDetail = '-';
              }
              
              statusElement.textContent = `Status: ${statusText} - ${statusDetail}`;
              
              // Update status indicator color
              statusElement.className = 'muted signal-status-text';
              if (isPaired && isRunning && isHealthy) {
                statusElement.style.color = '#28a745'; // Green for connected
              } else if (isRunning && isHealthy) {
                statusElement.style.color = '#ffc107'; // Yellow for ready
              } else if (status.error) {
                statusElement.style.color = '#dc3545'; // Red for error
              } else {
                statusElement.style.color = '#6c757d'; // Gray for disabled/unknown
              }
            }
            
            // Dynamic button management based on status
            if (qrBtn && cleanupBtn) {
              const isPaired = status.registered_numbers && status.registered_numbers.length > 0;
              const isRunning = status.status === 'running';
              const isHealthy = status.api_status === 'healthy';
              
              if (isPaired && isRunning && isHealthy) {
                // Connected: Hide QR button, show cleanup
                qrBtn.style.display = 'none';
                cleanupBtn.style.display = 'inline-block';
              } else if (isRunning && isHealthy) {
                // Ready for pairing: Show both buttons
                qrBtn.style.display = 'inline-block';
                cleanupBtn.style.display = 'inline-block';
              } else if (status.status === 'starting') {
                // Starting: Hide both buttons
                qrBtn.style.display = 'none';
                cleanupBtn.style.display = 'none';
              } else {
                // Disabled: Hide both buttons
                qrBtn.style.display = 'none';
                cleanupBtn.style.display = 'none';
              }
            }
            
            // Auto-show QR code if running but not paired
            const isPaired = status.registered_numbers && status.registered_numbers.length > 0;
            const isRunning = status.status === 'running';
            const isHealthy = status.api_status === 'healthy';
            
            const qrContainer = document.getElementById('signal-qr-container');
            
            signalQrAutoRefreshEnabled = isRunning && isHealthy && !isPaired;

            if (signalQrAutoRefreshEnabled) {
              if (qrContainer) {
                const hasQrImage = !!qrContainer.querySelector('img');
                if (!hasQrImage && !signalQrRequestPromise) {
                  showSignalQR(true);
                } else if (!signalQrRefreshTimer && !signalQrRequestPromise) {
                  scheduleSignalQrRefresh(false);
                }
              }
            } else {
              clearSignalQrRefreshTimer();
            }

            if (isPaired && qrContainer && qrContainer.innerHTML.trim()) {
              // Clear QR code when device becomes paired (Connected)
              qrContainer.innerHTML = '<p class="success" style="color: #28a745; margin: 10px 0;">Signal successfully connected and paired!</p>';
              signalQrLastFetchedAt = 0;
            } else if (!signalQrAutoRefreshEnabled && qrContainer && status.status !== 'running') {
              qrContainer.innerHTML = '';
              signalQrLastFetchedAt = 0;
            }
            
          } catch (e) {
            console.error('Failed to update Signal status:', e);
          } finally {
            signalStatusRequestInFlight = false;
          }
        }
        
       // DataChannel status polling
       async function updateDatachannelStatus() {
         if (!liveStatusPollingEnabled() || datachannelStatusRequestInFlight) {
           return;
         }
         datachannelStatusRequestInFlight = true;
         try {
           const response = await fetch('/api/datachannel-status');
           const data = await response.json();
           const indicator = document.getElementById('dc-indicator');
           const statusText = document.getElementById('dc-status-text');
           const metrics = document.getElementById('dc-metrics');
           if (!indicator || !statusText || !metrics) return;
           const sessions = Number(data.active_sessions || 0);
           if (sessions > 0) {
             indicator.style.backgroundColor = '#22c55e';
             indicator.style.borderColor = '#22c55e';
             statusText.textContent = `Connected Clients: ${sessions}`;
             const tsValues = data.last_ping_timestamps ? Object.values(data.last_ping_timestamps) : [];
             let lastPingStr = '-';
             if (tsValues && tsValues.length > 0) {
               const latest = Math.max(...tsValues.map(v => Number(v) || 0));
               if (latest > 0) lastPingStr = new Date(latest * 1000).toLocaleTimeString();
             }
             metrics.textContent = `Last check-in: ${lastPingStr}`;
           } else {
             indicator.style.backgroundColor = '#0b1220';
             indicator.style.borderColor = '#334155';
             statusText.textContent = 'No connected clients';
             metrics.textContent = '';
           }
         } catch (e) {
           try { console.error('Failed to update DataChannel status:', e); } catch {}
         } finally {
           datachannelStatusRequestInFlight = false;
         }
       }

        function schedulerQueueEscapeHtml(value) {
          return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
        }

        function schedulerQueueFormatDuration(totalSeconds) {
          const seconds = Math.max(0, Math.round(Number(totalSeconds) || 0));
          if (!seconds) {
            return '0s';
          }
          if (seconds < 60) {
            return `${seconds}s`;
          }
          if (seconds < 3600) {
            const minutes = Math.floor(seconds / 60);
            const remainderSeconds = seconds % 60;
            return remainderSeconds ? `${minutes}m ${remainderSeconds}s` : `${minutes}m`;
          }
          if (seconds < 86400) {
            const hours = Math.floor(seconds / 3600);
            const remainderMinutes = Math.floor((seconds % 3600) / 60);
            return remainderMinutes ? `${hours}h ${remainderMinutes}m` : `${hours}h`;
          }
          const days = Math.floor(seconds / 86400);
          const remainderHours = Math.floor((seconds % 86400) / 3600);
          return remainderHours ? `${days}d ${remainderHours}h` : `${days}d`;
        }

        function schedulerQueueFormatTime(timestampSeconds) {
          const timestamp = Number(timestampSeconds || 0);
          if (!timestamp) {
            return 'not scheduled';
          }
          try {
            return new Date(timestamp * 1000).toLocaleString([], {
              month: 'short',
              day: 'numeric',
              hour: 'numeric',
              minute: '2-digit'
            });
          } catch (_) {
            return 'not scheduled';
          }
        }

        function schedulerQueueStatusMeta(item) {
          const attempts = Number(item?.attempt_count || 0);
          const status = String(item?.status || '').toLowerCase();
          if (status === 'ready') {
            return { label: attempts > 0 ? 'Retry now' : 'Ready now', tone: attempts > 0 ? 'retry' : 'ready' };
          }
          if (attempts > 0) {
            return { label: 'Waiting to retry', tone: 'retry' };
          }
          return { label: 'Waiting', tone: 'waiting' };
        }

        function renderNotificationQueueStatus(payload) {
          const queue = payload && payload.queue ? payload.queue : (payload || {});
          const pendingEl = document.getElementById('scheduler-queue-pending');
          const pendingCopyEl = document.getElementById('scheduler-queue-pending-copy');
          const readyEl = document.getElementById('scheduler-queue-ready');
          const readyCopyEl = document.getElementById('scheduler-queue-ready-copy');
          const retryingEl = document.getElementById('scheduler-queue-retrying');
          const retryingCopyEl = document.getElementById('scheduler-queue-retrying-copy');
          const oldestEl = document.getElementById('scheduler-queue-oldest');
          const oldestCopyEl = document.getElementById('scheduler-queue-oldest-copy');
          const noteEl = document.getElementById('scheduler-queue-note');
          const listEl = document.getElementById('scheduler-queue-list');
          const updatedEl = document.getElementById('scheduler-queue-last-updated');
          if (!pendingEl || !pendingCopyEl || !readyEl || !readyCopyEl || !retryingEl || !retryingCopyEl || !oldestEl || !oldestCopyEl || !noteEl || !listEl || !updatedEl) {
            return;
          }

          const pendingCount = Number(queue.pending_count || 0);
          const readyCount = Number(queue.ready_count || 0);
          const retryingCount = Number(queue.retrying_count || 0);
          const waitingCount = Number(queue.waiting_count || 0);
          const oldestAgeSeconds = Number(queue.oldest_age_seconds || 0);
          const nextRetryAt = Number(queue.next_retry_at_s || 0);
          const displayedCount = Number(queue.displayed_count || 0);
          const maxAgeSeconds = Number(queue.max_age_seconds || 0);
          const items = Array.isArray(queue.items) ? queue.items : [];

          pendingEl.textContent = String(pendingCount);
          pendingCopyEl.textContent = pendingCount ? `${waitingCount} waiting for the next delivery window` : 'No queued reminder or task notifications';
          readyEl.textContent = String(readyCount);
          readyCopyEl.textContent = readyCount ? 'Can deliver on the next scheduler sweep' : 'Nothing is ready right now';
          retryingEl.textContent = String(retryingCount);
          retryingCopyEl.textContent = retryingCount ? 'Previous attempts failed and are backing off' : 'No retries are backing off';
          oldestEl.textContent = pendingCount ? schedulerQueueFormatDuration(oldestAgeSeconds) : '0s';
          oldestCopyEl.textContent = maxAgeSeconds ? `Broadcast fallback after ${schedulerQueueFormatDuration(maxAgeSeconds)}` : 'Queue will fall back to broadcast if it expires';
          updatedEl.textContent = `Updated ${schedulerQueueFormatTime(queue.generated_at_s || Date.now() / 1000)}`;

          if (!pendingCount) {
            noteEl.textContent = 'No queued notifications. Reminders and scheduled tasks are either delivering immediately or nothing is waiting for a live client or partner chat.';
            listEl.innerHTML = "<div class='scheduler-queue-empty'>Queued reminder and task deliveries will appear here when AutoYou has to wait for a connected client or a saved messaging target.</div>";
            return;
          }

          let noteText = `Showing ${displayedCount} of ${pendingCount} queued notification${pendingCount === 1 ? '' : 's'}.`;
          if (nextRetryAt) {
            const secondsUntilRetry = Math.max(0, nextRetryAt - Number(queue.generated_at_s || 0));
            noteText += ` Next retry window opens in ${schedulerQueueFormatDuration(secondsUntilRetry)} (${schedulerQueueFormatTime(nextRetryAt)}).`;
          }
          noteEl.textContent = noteText;

          listEl.innerHTML = items.map((item) => {
            const statusMeta = schedulerQueueStatusMeta(item);
            const metaPills = [];
            if (item.owner_key || item.canonical_user_id) {
              metaPills.push('Paired client');
            }
            if (item.reply_target_label) {
              metaPills.push(`Primary ${item.reply_target_label}`);
            }
            metaPills.push(`Attempts ${Number(item.attempt_count || 0)}`);
            metaPills.push(`Age ${schedulerQueueFormatDuration(item.age_seconds || 0)}`);
            if (item.status === 'ready') {
              metaPills.push('Delivery window open');
            } else {
              metaPills.push(`Next ${schedulerQueueFormatDuration(item.next_attempt_in_seconds || 0)}`);
            }
            const deliveryTags = Array.isArray(item.delivery_tags) ? item.delivery_tags : [];
            const errorHtml = item.last_error
              ? `<div class='scheduler-queue-item-error'><strong>Last error:</strong> ${schedulerQueueEscapeHtml(item.last_error)}</div>`
              : '';
            return `
              <div class='scheduler-queue-item'>
                <div class='scheduler-queue-item-head'>
                  <div class='scheduler-queue-item-title'>
                    <span class='scheduler-queue-source'>${schedulerQueueEscapeHtml(item.source_label || item.source || 'Scheduler')}</span>
                    <p class='scheduler-queue-message'>${schedulerQueueEscapeHtml(item.message_preview || '(missing message)')}</p>
                  </div>
                  <span class='scheduler-queue-status-pill ${schedulerQueueEscapeHtml(statusMeta.tone)}'>${schedulerQueueEscapeHtml(statusMeta.label)}</span>
                </div>
                <div class='scheduler-queue-meta'>
                  ${metaPills.map((pill) => `<span>${schedulerQueueEscapeHtml(pill)}</span>`).join('')}
                </div>
                <div class='scheduler-queue-tags'>
                  ${deliveryTags.map((tag) => `<span>${schedulerQueueEscapeHtml(tag)}</span>`).join('')}
                </div>
                ${errorHtml}
              </div>
            `;
          }).join('');

          if (queue.has_more) {
            const remaining = Math.max(0, pendingCount - displayedCount);
            listEl.insertAdjacentHTML('beforeend', `<div class='scheduler-queue-empty'>${remaining} additional queued notification${remaining === 1 ? '' : 's'} remain hidden to keep the admin view compact on mobile.</div>`);
          }
        }

        async function refreshNotificationQueueStatus(force = false, triggerEl = null) {
          if ((!force && !liveStatusPollingEnabled()) || notificationQueueStatusRequestInFlight) {
            return;
          }
          notificationQueueStatusRequestInFlight = true;
          if (triggerEl && typeof btnLoading === 'function') {
            btnLoading(triggerEl, { disable: true });
          }
          try {
            const response = await fetch('/api/scheduler/notification-queue');
            const data = await response.json();
            if (!response.ok || data.success === false) {
              throw new Error(data.error || 'Failed to load scheduled notification queue');
            }
            renderNotificationQueueStatus(data);
          } catch (e) {
            const noteEl = document.getElementById('scheduler-queue-note');
            const listEl = document.getElementById('scheduler-queue-list');
            if (noteEl) {
              noteEl.textContent = 'Unable to load queued notification status right now.';
            }
            if (listEl) {
              listEl.innerHTML = `<div class='scheduler-queue-error'>${schedulerQueueEscapeHtml(e && e.message ? e.message : String(e))}</div>`;
            }
          } finally {
            notificationQueueStatusRequestInFlight = false;
            if (triggerEl && typeof btnDone === 'function') {
              btnDone(triggerEl);
            }
          }
        }

       // Start live status updates when page loads
       document.addEventListener('DOMContentLoaded', function() {
         startLiveStatusPolling();
        });

        document.addEventListener('visibilitychange', function() {
          if (liveStatusPollingEnabled()) {
            startLiveStatusPolling();
          } else {
            clearLiveStatusIntervals();
            clearSignalQrRefreshTimer();
          }
        });
        
        // Stop updates when page unloads
        window.addEventListener('beforeunload', function() {
         clearLiveStatusIntervals();
         clearSignalQrRefreshTimer();
       });
        
        // Signal QR code handling
        async function showSignalQR(forceRefresh = false) {
          const qrContainer = document.getElementById('signal-qr-container');
          if (!qrContainer) {
            return null;
          }

          if (signalQrRequestPromise) {
            return signalQrRequestPromise;
          }

          const hasQrImage = !!qrContainer.querySelector('img');
          const qrAgeMs = Date.now() - signalQrLastFetchedAt;
          if (!forceRefresh && hasQrImage && qrAgeMs < SIGNAL_QR_REFRESH_MS) {
            return null;
          }

          signalQrRequestPromise = (async () => {
            try {
              const response = await fetch('/api/signal/qr');
              const data = await response.json();
              if (data.success && data.qr_url) {
                qrContainer.innerHTML =
                  '<img src="' + data.qr_url + '" alt="Signal QR Code" style="max-width:300px;border:1px solid #ccc;padding:10px;background:white;">' +
                  '<p class="muted">Scan this QR code with your Signal app to pair this device.</p>';
                signalQrLastFetchedAt = Date.now();
              } else {
                qrContainer.innerHTML =
                  '<p class="error">Failed to generate QR code: ' + (data.error || 'Unknown error') + '</p>';
              }
            } catch (e) {
              qrContainer.innerHTML =
                '<p class="error">Error fetching QR code: ' + e.message + '</p>';
            } finally {
              signalQrRequestPromise = null;
              if (signalQrAutoRefreshEnabled) {
                scheduleSignalQrRefresh(false);
              }
            }
          })();

          return signalQrRequestPromise;
        }
        
        async function cleanupSignal() {
          if (confirm('This will remove all Signal pairing data and require re-pairing. Continue?')) {
            try {
              const response = await fetch('/api/signal/cleanup', {method: 'POST'});
              const data = await response.json();
              if (data.success) {
                alert('Signal configuration cleaned up successfully. Please refresh the page.');
                location.reload();
              } else {
                alert('Failed to cleanup Signal: ' + (data.error || 'Unknown error'));
              }
            } catch (e) {
              alert('Error cleaning up Signal: ' + e.message);
            }
          }
        }
        // Live WhatsApp status updates
       async function updateWhatsAppStatus() {
          if (!liveStatusPollingEnabled() || whatsappStatusRequestInFlight) {
            return;
          }
          whatsappStatusRequestInFlight = true;
          try {
            const response = await fetch('/api/whatsapp/status');
            const status = await response.json();
            
            // Declare status variables at function level to avoid scope issues
            const controlChannelHealthy = status.websocket_connected !== false;
            const hasPhoneNumber = !!(status.phone_number && String(status.phone_number).trim());
            const isConnected = controlChannelHealthy && (
              status.connection_healthy === true ||
              status.paired === true ||
              ((status.status === 'connected') && (status.ready === true || hasPhoneNumber)) ||
              ((status.client_state === 'READY') && hasPhoneNumber)
            );
            const isRecovering = (status.status === 'recovering') || ((status.node_process_running === true) && !controlChannelHealthy);
            const isRunning = (status.node_process_running === true) && controlChannelHealthy;
            const isEnabled = status.enabled !== false;
            const shouldOfferQr = isEnabled && !isConnected && (
              status.last_qr_available === true ||
              status.status === 'starting' ||
              status.status === 'pairing' ||
              status.status === 'connecting' ||
              status.client_state === 'OPENING' ||
              status.client_state === 'PAIRING'
            );
            
            // Update status display with comprehensive information
            const statusElement = document.querySelector('.whatsapp-status-text');
            const qrBtn = document.getElementById('whatsapp-qr-btn');
            const cleanupBtn = document.getElementById('whatsapp-cleanup-btn');
            
            if (statusElement) {
              let statusText = 'Unknown';
              let statusDetail = '-';
              let statusColor = '#6c757d'; // Default gray
              
              if (status.error) {
                statusText = 'Error';
                statusDetail = status.error;
                statusColor = '#dc3545'; // Red for error
              } else if (!isEnabled) {
                statusText = 'Disabled';
                statusDetail = '-';
                statusColor = '#6c757d'; // Gray for disabled
              } else if (isRecovering) {
                statusText = 'Recovering';
                const details = [];
                if (status.client_state && status.client_state !== 'UNLAUNCHED') details.push(`State: ${status.client_state}`);
                details.push(status.node_process_running ? 'Reconnecting control channel' : 'Restarting service');
                if (status.last_qr_available) details.push('QR Available');
                statusDetail = details.length > 0 ? details.join(' | ') : 'Recovering service state';
                statusColor = '#17a2b8';
              } else if (isConnected) {
                statusText = 'Connected';
                // Show comprehensive connection details
                const details = [];
                if (status.phone_number) details.push(`📱 ${status.phone_number}`);
                if (status.client_state && status.client_state !== 'CONNECTED') details.push(`State: ${status.client_state}`);
                if (status.websocket_connected === false) details.push('⚠️ WebSocket disconnected');
                if (status.connection_healthy === false) details.push('⚠️ Connection unhealthy');
                if (status.reconnection_attempts > 0) details.push(`🔄 Reconnects: ${status.reconnection_attempts}`);
                if (status.loading_screen_active) details.push('⏳ Loading');
                if (status.battery_info && status.battery_info.battery < 20) details.push(`🔋 ${status.battery_info.battery}%`);
                
                statusDetail = details.length > 0 ? details.join(' | ') : (status.phone_number || 'Ready');
                statusColor = '#28a745'; // Green for connected
              } else if (isRunning) {
                statusText = 'Ready for pairing';
                const details = [];
                if (status.client_state) details.push(`State: ${status.client_state}`);
                if (status.last_qr_available) details.push('QR Available');
                if (status.websocket_connected === false) details.push('⚠️ WebSocket disconnected');
                if (status.loading_screen_active) details.push('⏳ Loading');
                
                statusDetail = details.length > 0 ? details.join(' | ') : 'Waiting for QR scan';
                statusColor = '#ffc107'; // Yellow for ready
              } else if (status.status === 'starting' || status.status === 'restarting') {
                statusText = status.status === 'restarting' ? 'Restarting' : 'Starting up';
                const details = [];
                if (status.client_state && status.client_state !== 'UNLAUNCHED') details.push(`State: ${status.client_state}`);
                if (status.node_process_running === false) details.push('Node process stopped');
                
                statusDetail = details.length > 0 ? details.join(' | ') : 'Initializing...';
                statusColor = '#17a2b8'; // Blue for starting
              } else {
                statusText = 'Stopped';
                const details = [];
                if (status.client_state && status.client_state !== 'UNLAUNCHED') details.push(`Last state: ${status.client_state}`);
                if (status.node_process_running === false) details.push('Node process not running');
                if (status.websocket_connected === false) details.push('WebSocket disconnected');
                
                statusDetail = details.length > 0 ? details.join(' | ') : 'Service not running';
                statusColor = '#dc3545'; // Red for stopped
              }
              
              statusElement.innerHTML = `Status: <strong style="color: ${statusColor}">${statusText}</strong> - ${statusDetail}`;
            }
            
            // Dynamic button management based on status
            if (qrBtn && cleanupBtn) {
              if (isConnected) {
                // Connected: Hide QR button, show cleanup
                qrBtn.style.display = 'none';
                cleanupBtn.style.display = 'inline-block';
              } else if (shouldOfferQr) {
                // Ready for pairing: Show both buttons
                qrBtn.style.display = 'inline-block';
                cleanupBtn.style.display = 'inline-block';
              } else if (isRecovering || status.status === 'starting' || status.status === 'restarting') {
                // Starting: Hide both buttons
                qrBtn.style.display = 'none';
                cleanupBtn.style.display = 'none';
              } else {
                // Disabled/Stopped: Hide both buttons
                qrBtn.style.display = 'none';
                cleanupBtn.style.display = 'none';
              }
            }

            // Auto-show QR code if running but not paired
            const qrContainer = document.getElementById('whatsapp-qr-container');
            const deviceNameInput = document.getElementById('whatsapp_device_name');
            if (deviceNameInput) {
              try {
                deviceNameInput.readOnly = !!isConnected;
                if (isConnected) {
                  deviceNameInput.style.backgroundColor = '#1a1a1a';
                  deviceNameInput.style.color = '#888';
                } else {
                  deviceNameInput.style.backgroundColor = '';
                  deviceNameInput.style.color = '';
                }
              } catch (_) {}
            }
            
            if (shouldOfferQr) {
              if (qrContainer) {
                const hasQrImage = !!document.getElementById('whatsapp-qr-img');
                const qrAgeMs = Date.now() - whatsappQrLastFetchedAt;
                if (!hasQrImage || qrAgeMs >= WHATSAPP_QR_REFRESH_MS) {
                  showWhatsAppQR(hasQrImage);
                }
              }
            } else if (isConnected && qrContainer && qrContainer.innerHTML.trim()) {
              // Clear QR code when device becomes paired (Connected)
              qrContainer.innerHTML = '<p class="success" style="color: #28a745; margin: 10px 0;">✓ WhatsApp successfully connected and paired!</p>';
              whatsappQrLastFetchedAt = 0;
            }
            
          } catch (e) {
            console.error('Failed to update WhatsApp status:', e);
          } finally {
            whatsappStatusRequestInFlight = false;
          }
        }
        
        // WhatsApp QR code handling
        async function showWhatsAppQR(forceRefresh = false) {
          const container = document.getElementById('whatsapp-qr-container');
          if (!container) return null;

          if (whatsappQrRequestPromise) {
            return whatsappQrRequestPromise;
          }

          const hasQrImage = !!container.querySelector('#whatsapp-qr-img');
          const qrAgeMs = Date.now() - whatsappQrLastFetchedAt;
          if (!forceRefresh && hasQrImage && qrAgeMs < WHATSAPP_QR_REFRESH_MS) {
            return null;
          }

          whatsappQrRequestPromise = (async () => {
            try {
              const response = await fetch('/api/whatsapp/qr');
              const data = await response.json();
              if (data.success && data.qr_url) {
                let img = document.getElementById('whatsapp-qr-img');
                if (!img) {
                  container.innerHTML = '<img id="whatsapp-qr-img" src="' + data.qr_url + '" alt="WhatsApp QR Code" style="max-width:300px;border:1px solid #ccc;padding:10px;background:white;">' +
                    '<p class="muted">Scan this QR code with your WhatsApp mobile app to pair this device.</p>';
                } else {
                  img.src = data.qr_url;
                }
                whatsappQrLastFetchedAt = Date.now();
              } else if (data.pending) {
                container.innerHTML = '<p class="muted">WhatsApp is still initializing. QR code will appear automatically.</p>';
              } else {
                container.innerHTML = '<p class="error">Failed to generate QR code: ' + (data.error || 'Unknown error') + '</p>';
              }
            } catch (e) {
              container.innerHTML = '<p class="error">Error fetching QR code: ' + e.message + '</p>';
            } finally {
              whatsappQrRequestPromise = null;
            }
          })();

          return whatsappQrRequestPromise;
        }
        
        async function cleanupWhatsApp() {
          if (confirm('This will remove all WhatsApp pairing data and require re-pairing. Continue?')) {
            try {
              const response = await fetch('/api/whatsapp/reset', {method: 'POST'});
              const data = await response.json();
              if (data.success) {
                alert(data.restarted
                  ? 'WhatsApp configuration cleaned up successfully. The service restarted and QR pairing should reappear after reload.'
                  : 'WhatsApp configuration cleaned up successfully.');
                location.reload();
              } else {
                alert('Failed to cleanup WhatsApp: ' + (data.error || 'Unknown error'));
              }
            } catch (e) {
              alert('Error cleaning up WhatsApp: ' + e.message);
            }
          }
        }
        
        function showRestartModal() {
          const modal = document.getElementById('restart-modal');
          if (modal) {
            modal.style.display = 'flex';
          }
        }
        
        function hideRestartModal() {
          const modal = document.getElementById('restart-modal');
          if (modal) {
            modal.style.display = 'none';
          }
        }
        
        async function confirmRestartWhatsApp() {
          hideRestartModal();
          
          try {
            const response = await fetch('/api/whatsapp/restart', {method: 'POST'});
            const data = await response.json();
            if (data.success) {
              alert('WhatsApp service restarted successfully. Please refresh the page.');
              location.reload();
            } else {
              alert('Failed to restart WhatsApp service: ' + (data.error || 'Unknown error'));
            }
          } catch (e) {
            alert('Error restarting WhatsApp service: ' + e.message);
          }
        }
        
        // Legacy function name for backward compatibility
        function restartWhatsApp() {
          showRestartModal();
        }

        // Signal restart modal handlers
        function showSignalRestartModal() {
          const modal = document.getElementById('signal-restart-modal');
          if (modal) {
            modal.style.display = 'flex';
          }
        }
        function hideSignalRestartModal() {
          const modal = document.getElementById('signal-restart-modal');
          if (modal) {
            modal.style.display = 'none';
          }
        }
        async function confirmRestartSignal() {
          hideSignalRestartModal();
          try {
            const response = await fetch('/api/signal/restart', {method: 'POST'});
            const data = await response.json();
            if (data.success) {
              alert('Signal service restarted successfully. Please refresh the page.');
              location.reload();
            } else {
              alert('Failed to restart Signal service: ' + (data.error || 'Unknown error'));
            }
          } catch (e) {
            alert('Error restarting Signal service: ' + e.message);
          }
        }
        function restartSignal() {
          showSignalRestartModal();
        }
        
        // Server shutdown confirmation dialog
        function confirmShutdown() {
          if (confirm('Are you sure you want to shutdown the AutoYou Server?\\n\\nThis will:\\n• Stop all server processes\\n• Disconnect all clients\\n• Shut down Signal Docker container (if configured)\\n\\nClick OK to proceed with shutdown.')) {
            shutdownServer();
          }
        }
        
        async function shutdownServer() {
          try {
            const response = await fetch('/shutdown', {method: 'POST'});
            if (response.ok) {
              alert('Server shutdown initiated. The server will stop in a few seconds.');
              // Redirect to a goodbye page or close the window
              setTimeout(() => {
                window.location.href = 'about:blank';
              }, 2000);
            } else {
              alert('Failed to shutdown server. Please try again or check server logs.');
            }
          } catch (e) {
            alert('Error initiating server shutdown: ' + e.message);
          }
        }

        async function triggerCloudAction(url, successMessage) {
          try {
            const response = await fetch(url, {method: 'POST'});
            const contentType = response.headers.get('content-type') || '';
            const data = contentType.includes('application/json') ? await response.json() : {success: response.ok, error: await response.text()};
            if (!response.ok) {
              throw new Error(data.error || data.detail || 'Cloud action failed');
            }
            alert((data && (data.message || data.status)) || successMessage || 'Cloud action completed.');
            location.reload();
          } catch (e) {
            alert('Cloud action failed: ' + e.message);
          }
        }

        async function sendCloudOfflineNotification() {
          try {
            const titleEl = document.getElementById('cloud-notify-title');
            const bodyEl = document.getElementById('cloud-notify-body');
            const categoryEl = document.getElementById('cloud-notify-category');
            const response = await fetch('/api/cloud/notify-client', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                title: titleEl ? titleEl.value : 'AutoYou',
                body: bodyEl ? bodyEl.value : '',
                category: categoryEl ? categoryEl.value : 'admin'
              })
            });
            const contentType = response.headers.get('content-type') || '';
            const data = contentType.includes('application/json') ? await response.json() : {success: response.ok, error: await response.text()};
            if (!response.ok) {
              throw new Error(data.error || data.detail || 'Notification failed');
            }
            const rails = [];
            if (data.sse_delivered) rails.push('live cloud link');
            if (Number(data.apns_sent || 0) > 0) rails.push('Apple devices ' + data.apns_sent);
            if (Number(data.fcm_sent || 0) > 0) rails.push('Android devices ' + data.fcm_sent);
            if (rails.length) {
              alert('Notification sent via ' + rails.join(', ') + '.');
              return;
            }
            // No rail actually carried it. Say why instead of implying success -
            // "accepted by AutoYou Cloud" for a delivery to nobody is how this
            // stayed broken unnoticed.
            let why = 'no device received it.';
            if (data.offline_allowed === false) {
              why = 'your plan does not include offline notifications - upgrade to Always-On Connection or higher.';
            } else if (data.push_registry_available === false) {
              why = 'AutoYou Cloud could not reach its push-token registry. Try again shortly.';
            } else if (Number(data.devices || 0) === 0) {
              why = 'no device has registered for offline notifications yet. Turn on "Offline Notifications" in the AutoYou app on your phone (Settings), then retry.';
            } else if (Array.isArray(data.errors) && data.errors.length) {
              why = 'the push provider rejected it (' + data.errors.join(', ') + ').';
            }
            alert('Notification was NOT delivered: ' + why);
          } catch (e) {
            alert('Cloud notification failed: ' + e.message);
          }
        }
        
        // Connection helper configuration functions
        function parseStunTurnUrls(input) {
          if (!input || !input.trim()) {
            return [];
          }
          
          // Split by comma or space, filter empty strings
          const urls = input.split(/[,\\s]+/).filter(url => url.trim());
          const servers = [];
          const errors = [];
          
          for (const url of urls) {
            const trimmedUrl = url.trim();
            if (!trimmedUrl) continue;
            
            try {
              if (trimmedUrl.startsWith('stun:')) {
                // Connection helper validation
                if (!trimmedUrl.match(/^stun:[a-zA-Z0-9.-]+:\\d+$/)) {
                  errors.push(`Invalid connection server URL format: ${trimmedUrl}. Expected format: scheme:hostname:port`);
                  continue;
                }
                servers.push({
                  urls: [trimmedUrl]
                });
              } else if (trimmedUrl.startsWith('turn:')) {
                // Connection helper may have credentials in URL or separate
                const urlObj = new URL(trimmedUrl);
                const server = { urls: [trimmedUrl] };
                
                // Validate connection helper URL format
                if (!urlObj.hostname || !urlObj.port) {
                  errors.push(`Invalid connection server URL format: ${trimmedUrl}. Expected format: scheme:hostname:port, with username and password when your provider requires them`);
                  continue;
                }
                
                // Extract credentials from URL parameters if present
                const username = urlObj.searchParams.get('username');
                const password = urlObj.searchParams.get('password');
                
                if (username && password) {
                  server.username = username;
                  server.credential = password;
                  // Clean URL by removing credentials from query params
                  urlObj.searchParams.delete('username');
                  urlObj.searchParams.delete('password');
                  server.urls = [urlObj.toString()];
                } else if (username || password) {
                  errors.push(`Incomplete connection helper credentials for ${trimmedUrl}. Both username and password are required.`);
                  continue;
                }
                
                servers.push(server);
              } else {
                errors.push(`Unsupported connection server URL format: ${trimmedUrl}. Only supported connection server URLs can be used here.`);
              }
            } catch (e) {
              errors.push(`Error parsing URL ${trimmedUrl}: ${e.message}`);
            }
          }
          
          return { servers, errors };
        }
        
        function validateJsonConfiguration(jsonStr) {
          try {
            const config = JSON.parse(jsonStr);
            
            if (!config.iceServers || !Array.isArray(config.iceServers)) {
              return { valid: false, error: 'Connection helper JSON must include an "iceServers" list' };
            }
            
            for (let i = 0; i < config.iceServers.length; i++) {
              const server = config.iceServers[i];
              
              if (!server.urls || !Array.isArray(server.urls) || server.urls.length === 0) {
                return { valid: false, error: `Server at index ${i} must have a non-empty "urls" array` };
              }
              
              // Check if helper has credentials when needed
              const hasTurnUrl = server.urls.some(url => url.startsWith('turn:'));
              if (hasTurnUrl && (!server.username || !server.credential)) {
                console.warn(`Connection helper at index ${i} may need username and credential for authentication`);
              }
            }
            
            return { valid: true };
          } catch (e) {
            return { valid: false, error: `Invalid JSON: ${e.message}` };
          }
        }
        
        async function applyRtcBundle(mode, inputId = 'stun_turn_input', feedbackId = '') {
          const inputEl = document.getElementById(inputId);
          const textarea = document.getElementById('rtc_json_textarea');
          const feedbackEl = feedbackId ? document.getElementById(feedbackId) : null;
          if (!inputEl || !textarea) {
            alert('Connection helper controls are missing from the page.');
            return;
          }

          const input = inputEl.value.trim();
          if (!input) {
            alert('Please paste a connection helper bundle, JSON snippet, or server URLs.');
            return;
          }

          let existingConfig = {};
          if (mode !== 'replace' && textarea.value.trim()) {
            const validation = validateJsonConfiguration(textarea.value);
            if (!validation.valid) {
              alert('Existing JSON configuration is invalid: ' + validation.error);
              return;
            }
            existingConfig = JSON.parse(textarea.value);
          }

          try {
            const response = await fetch('/api/rtc/parse', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                text: input,
                mode: mode,
                existing: existingConfig
              })
            });
            const data = await response.json();
            if (!data.success) {
              throw new Error(data.error || 'Connection helper parsing failed');
            }

            textarea.value = JSON.stringify(data.config, null, 2);
            inputEl.value = '';

            const warningText = (data.warnings && data.warnings.length > 0)
              ? ` Warnings: ${data.warnings.join(' | ')}`
              : '';
            const message = `Imported ${data.added_count} server(s) from ${data.detected_source || 'your input'}. Total configured: ${data.total_count}.${warningText}`;
            if (feedbackEl) {
              feedbackEl.textContent = message;
            }
            alert(message);
          } catch (e) {
            if (feedbackEl) {
              feedbackEl.textContent = 'Connection helper parsing failed: ' + e.message;
            }
            alert('Error processing configuration: ' + e.message);
          }
        }

        function addToExistingServers() {
          applyRtcBundle('append');
        }
        
        function replaceServers() {
          applyRtcBundle('replace');
        }

        function applyRtcBundleFromWizard(mode) {
          applyRtcBundle(mode, 'wizard-ice-input', 'wizard-rtc-feedback');
        }
        
        // Ollama status refresh function
        async function refreshOllamaStatus() {
          try {
            const response = await fetch('/ollama-status');
            const data = await response.json();
            
            if (data.error) {
              console.error('Error fetching Ollama status:', data.error);
              return;
            }
            
            // Update Ollama status
            const ollamaStatusElement = document.querySelector('#ollama-status');
            if (ollamaStatusElement) {
              const statusIndicator = ollamaStatusElement.querySelector('span[style*="background-color"]');
              const statusText = ollamaStatusElement.querySelector('strong').nextSibling.nextElementSibling;
              const statusDetails = ollamaStatusElement.querySelector('div[style*="margin-left: 20px"]');
              
              if (statusIndicator) {
                statusIndicator.style.backgroundColor = data.ollama.status_color;
              }
              if (statusText) {
                statusText.textContent = data.ollama.status;
              }
              if (statusDetails) {
                statusDetails.innerHTML = `API Base: ${data.ollama.api_base}<br>Available Models: ${data.ollama.models_count}<br>Selected Model: ${data.ollama.selected_model}`;
              }
            }
            
            // Update Google API status
            const googleStatusElement = document.querySelector('#google-api-status');
            if (googleStatusElement) {
              const statusIndicator = googleStatusElement.querySelector('span[style*="background-color"]');
              const statusText = googleStatusElement.querySelector('strong').nextSibling.nextElementSibling;
              const statusDetails = googleStatusElement.querySelector('div[style*="margin-left: 20px"]');
              
              if (statusIndicator) {
                statusIndicator.style.backgroundColor = data.google.status_color;
              }
              if (statusText) {
                statusText.textContent = data.google.status;
              }
              if (statusDetails) {
                statusDetails.innerHTML = `Use Google API: ${data.google.use_google_api}<br>Google Model: ${data.google.model}<br>API Key Status: ${data.google.api_key_status}`;
              }
            }
            
            // Update AI provider summary
            const summaryElement = document.querySelector('div[style*="background: #1e293b"] strong').parentElement;
            if (summaryElement) {
              summaryElement.innerHTML = `<strong>Current Configuration:</strong> ${data.ai_provider_summary}`;
            }
            
          } catch (e) {
            console.error('Error refreshing Ollama status:', e);
          }
        }
        
        // AI Agent Server control functions
        async function startAiAgentServer() {
          try {
            const response = await fetch('/ai-agent-server/start', { method: 'POST' });
            const data = await response.json();
            updateAiAgentStatus(data.status || 'Starting...');
          } catch (e) {
            console.error('Error starting AI Agent Server:', e);
            updateAiAgentStatus('Error starting server');
          }
        }
        
        async function stopAiAgentServer() {
          try {
            const response = await fetch('/ai-agent-server/stop', { method: 'POST' });
            const data = await response.json();
            updateAiAgentStatus(data.status || 'Stopped');
          } catch (e) {
            console.error('Error stopping AI Agent Server:', e);
            updateAiAgentStatus('Error stopping server');
          }
        }
        
        async function restartAiAgentServer() {
          try {
            updateAiAgentStatus('Restarting...');
            const response = await fetch('/ai-agent-server/restart', { method: 'POST' });
            const data = await response.json();
            updateAiAgentStatus(data.status || 'Restarted');
          } catch (e) {
            console.error('Error restarting AI Agent Server:', e);
            updateAiAgentStatus('Error restarting server');
          }
        }
        
        function setAgentInstructionStructureState(data) {
          const stateEl = document.getElementById('agent-instructions-structure-state');
          const hintEl = document.getElementById('agent-instructions-structure-hint');
          const saveSectionsBtn = document.getElementById('save-prompt-sections-btn');
          if (saveSectionsBtn) {
            saveSectionsBtn.disabled = !data?.section_builder_available;
          }
          if (!stateEl || !hintEl) {
            return;
          }
          if (!data?.section_builder_available) {
            stateEl.textContent = 'Raw prompt only';
            hintEl.textContent = 'AutoYou could not resolve the expected prompt sections from prompt.py, so the raw prompt editor is the safe fallback right now.';
            return;
          }
          if (data.literal_matches_sections) {
            stateEl.textContent = 'Section builder synced';
            hintEl.textContent = 'Saving builder sections keeps the composable prompt variables and the live AGENT_INSTRUCTION literal aligned without requiring any AI provider.';
            return;
          }
          stateEl.textContent = 'Custom raw prompt active';
          hintEl.textContent = 'The live AGENT_INSTRUCTION literal currently differs from the prompt sections. Save Builder Sections to restore the composable fallback, or keep editing the raw prompt below.';
        }

        function renderAgentPromptSections(data) {
          const container = document.getElementById('agent-prompt-section-grid');
          if (!container) {
            return;
          }
          const sections = Array.isArray(data?.sections) ? data.sections : [];
          if (!sections.length) {
            container.innerHTML = "<div class='agent-section-empty'>Prompt sections are unavailable. Use the raw prompt editor below.</div>";
            return;
          }
          container.innerHTML = sections.map(section => {
            const variable = String(section?.variable || '');
            const readOnly = !data?.section_builder_available || !!section?.managed || section?.found === false;
            const badgeClass = readOnly ? 'managed' : 'editable';
            const badgeText = readOnly
              ? (section?.managed ? 'Managed by agent builder' : 'Read only')
              : 'Editable section';
            const description = String(section?.description || '');
            const label = String(section?.label || variable || 'Prompt section');
            const value = String(section?.value || '');
            return `
              <section class='agent-section-card ${readOnly ? "readonly" : ""}'>
                <div class='agent-section-head'>
                  <div>
                    <strong>${escapeHtml(label)}</strong>
                    <p>${escapeHtml(description)}</p>
                  </div>
                  <span class='agent-section-badge ${badgeClass}'>${escapeHtml(badgeText)}</span>
                </div>
                <textarea
                  class='agent-section-input'
                  data-variable='${escapeHtml(variable)}'
                  placeholder='${escapeHtml(readOnly ? "Managed by the prompt composer" : "Edit this prompt section directly")}'
                  ${readOnly ? 'readonly' : ''}
                >${escapeHtml(value)}</textarea>
              </section>
            `;
          }).join('');
        }

        function collectEditablePromptSections() {
          return Array.from(document.querySelectorAll('.agent-section-input[data-variable]:not([readonly])')).map(input => ({
            variable: input.dataset.variable,
            value: input.value || '',
          }));
        }

        function refreshAgentInstructionMetrics() {
          const textarea = document.getElementById('agent-instructions');
          if (!textarea) {
            return;
          }
          const value = textarea.value || '';
          const lineCount = value ? value.split(/\\r?\\n/).length : 0;
          const lineEl = document.getElementById('agent-instructions-lines');
          const charEl = document.getElementById('agent-instructions-chars');
          if (lineEl) {
            lineEl.textContent = `Lines: ${lineCount}`;
          }
          if (charEl) {
            charEl.textContent = `Characters: ${value.length}`;
          }
        }

        async function loadAgentInstructions() {
          try {
            const response = await fetch('/api/agent-instructions');
            const data = await response.json();

            if (data.success) {
              const textarea = document.getElementById('agent-instructions');
              if (textarea) {
                textarea.value = data.instructions;
                textarea.readOnly = !!data.read_only;
              }
              const wizardPreview = document.getElementById('wizard-prompt-preview');
              if (wizardPreview) {
                wizardPreview.value = data.instructions;
              }
              // Show/hide read-only banner
              const existingBanner = document.getElementById('agent-instructions-readonly-banner');
              if (existingBanner) existingBanner.remove();
              if (data.read_only_notice) {
                const card = document.getElementById('agent-instructions-card');
                if (card) {
                  const banner = document.createElement('div');
                  banner.id = 'agent-instructions-readonly-banner';
                  const isJailbreak = !!data.jailbreak_active;
                  banner.style.cssText = 'padding:8px 14px;border-radius:6px;font-size:13px;margin-bottom:10px;' +
                    (isJailbreak
                      ? 'background:#fff3cd;border:1px solid #ffc107;color:#856404;'
                      : 'background:#e8f4f8;border:1px solid #bee5eb;color:#0c5460;');
                  banner.textContent = (isJailbreak ? '⚠️ ' : '🔒 ') + data.read_only_notice;
                  card.insertBefore(banner, card.firstChild);
                }
              }
              // Disable/enable save buttons based on read_only
              const saveButtons = ['save-prompt-sections-btn'];
              saveButtons.forEach(function(id) {
                const btn = document.getElementById(id);
                if (btn) btn.disabled = !!data.read_only;
              });
              // Disable raw prompt save/revert buttons if read_only
              const editorToolbar = document.getElementById('agent-instructions-raw-actions');
              if (editorToolbar) {
                editorToolbar.querySelectorAll('button').forEach(function(btn) {
                  if (btn.textContent.trim() !== 'Reload') {
                    btn.disabled = !!data.read_only;
                  }
                });
              }
              setAgentInstructionStructureState(data);
              renderAgentPromptSections(data);
              refreshAgentInstructionMetrics();
            } else {
              alert('Error loading agent instructions: ' + (data.error || 'Unknown error'));
            }
          } catch (e) {
            console.error('Error loading agent instructions:', e);
            setAgentInstructionStructureState({ section_builder_available: false });
            renderAgentPromptSections({ sections: [] });
            alert('Error loading agent instructions: ' + e.message);
          }
        }

        async function savePromptSections() {
          try {
            const sections = collectEditablePromptSections();
            if (!sections.length) {
              alert('No editable prompt sections are available right now.');
              return;
            }
            const response = await fetch('/api/agent-instructions/sections', {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
              },
              body: JSON.stringify({ sections: sections })
            });
            const data = await response.json();
            if (data.success) {
              await loadAgentInstructions();
              alert('Prompt builder sections saved successfully!');
            } else {
              alert('Error saving prompt sections: ' + (data.error || 'Unknown error'));
            }
          } catch (e) {
            console.error('Error saving prompt sections:', e);
            alert('Error saving prompt sections: ' + e.message);
          }
        }
        
        async function updateAgentInstructions() {
          try {
            const textarea = document.getElementById('agent-instructions');
            if (!textarea) {
              alert('Agent instructions textarea not found');
              return;
            }
            
            const instructions = textarea.value.trim();
            if (!instructions) {
              alert('Instructions cannot be empty');
              return;
            }
            
            const response = await fetch('/api/agent-instructions', {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
              },
              body: JSON.stringify({ instructions: instructions })
            });
            
            const data = await response.json();
            
            if (data.success) {
              await loadAgentInstructions();
              refreshAgentInstructionMetrics();
              alert('Raw prompt updated successfully!');
            } else {
              alert('Error updating agent instructions: ' + (data.error || 'Unknown error'));
            }
          } catch (e) {
            console.error('Error updating agent instructions:', e);
            alert('Error updating agent instructions: ' + e.message);
          }
        }

        async function revertAgentInstructions() {
          try {
            if (!confirm('Revert agent instructions to the original default?')) {
              return;
            }
            const response = await fetch('/api/agent-instructions/revert', { method: 'POST' });
            const data = await response.json();
            if (data.success) {
              await loadAgentInstructions();
              refreshAgentInstructionMetrics();
              alert('Agent instructions reverted to the composable fallback successfully!');
            } else {
              alert('Error reverting agent instructions: ' + (data.error || 'Unknown error'));
            }
          } catch (e) {
            console.error('Error reverting agent instructions:', e);
            alert('Error reverting agent instructions: ' + e.message);
          }
        }
        
        async function updateAiAgentServerStatus() {
          if (!liveStatusPollingEnabled() || aiAgentStatusRequestInFlight) {
            return;
          }
          aiAgentStatusRequestInFlight = true;
          try {
            const response = await fetch('/api/ai-agent-server/status');
            const data = await response.json();
            updateAiAgentStatus(data.status || 'Unknown');
          } catch (e) {
            console.error('Error fetching AI Agent Server status:', e);
            updateAiAgentStatus('Error');
          } finally {
            aiAgentStatusRequestInFlight = false;
          }
        }
        
        function updateAiAgentStatus(status) {
          const statusElement = document.getElementById('ai-agent-status');
          if (statusElement) {
            statusElement.textContent = `Status: ${status}`;
          }
        }
        
        // Websites & Browser settings functions
        async function startAutoYouPageService() {
          try {
            const response = await fetch('/autoyou-page-service/start', { method: 'POST' });
            const data = await response.json();
            updateAutoYouPageStatus(data.status || 'Starting...');
          } catch (e) {
            console.error('Error starting Websites & Browser:', e);
            updateAutoYouPageStatus('Error starting service');
          }
        }
        
        async function stopAutoYouPageService() {
          try {
            const response = await fetch('/autoyou-page-service/stop', { method: 'POST' });
            const data = await response.json();
            updateAutoYouPageStatus(data.status || 'Stopped');
          } catch (e) {
            console.error('Error stopping Websites & Browser:', e);
            updateAutoYouPageStatus('Error stopping service');
          }
        }
        
        async function restartAutoYouPageService() {
          try {
            updateAutoYouPageStatus('Restarting...');
            const response = await fetch('/autoyou-page-service/restart', { method: 'POST' });
            const data = await response.json();
            updateAutoYouPageStatus(data.status || 'Restarted');
          } catch (e) {
            console.error('Error restarting Websites & Browser:', e);
            updateAutoYouPageStatus('Error restarting service');
          }
        }
        
        async function updateAutoYouPageServiceStatus() {
          if (!liveStatusPollingEnabled() || autoYouPageStatusRequestInFlight) {
            return;
          }
          autoYouPageStatusRequestInFlight = true;
          try {
            const response = await fetch('/api/autoyou-page-service/status');
            const data = await response.json();
            updateAutoYouPageStatus(data.status || 'Unknown');
          } catch (e) {
            console.error('Error fetching Websites & Browser status:', e);
            updateAutoYouPageStatus('Error');
          } finally {
            autoYouPageStatusRequestInFlight = false;
          }
        }
        
        function updateAutoYouPageStatus(status) {
          const statusElement = document.getElementById('autoyou-page-status');
          if (statusElement) {
            statusElement.textContent = `Status: ${status}`;
          }
        }

                const autoyouReservedPortsInput = document.getElementById('autoyou_reserved_browser_ports');
                const autoyouAdvertisedWebsitesInput = document.getElementById('autoyou_advertised_websites');
                const autoyouAdvertisedWebsitesList = document.getElementById('autoyou-advertised-websites-list');
                const autoyouAdvertisedWebsitesEmpty = document.getElementById('autoyou-advertised-websites-empty');
                const autoyouBookmarksInput = document.getElementById('autoyou_bookmarks');
                const autoyouBookmarksList = document.getElementById('autoyou-bookmarks-list');
                const autoyouBookmarksEmpty = document.getElementById('autoyou-bookmarks-empty');
                let autoyouAdvertisedWebsitesReserved = new Set();
                let autoyouAdvertisedWebsites = [];
                let autoyouBookmarks = [];

        try {
                    autoyouAdvertisedWebsitesReserved = new Set(JSON.parse(autoyouReservedPortsInput?.value || '[]'));
        } catch (_) {
                    autoyouAdvertisedWebsitesReserved = new Set();
        }

                function escapeAdvertisedWebsiteHtml(value) {
          return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
        }

        function normalizeAdvertisedForwardUrl(rawValue) {
          const trimmed = String(rawValue || '').trim();
          if (!trimmed) {
            return '';
          }
          const candidate = /^[a-z][a-z0-9+.-]*:\\/\\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
          try {
            return new URL(candidate).toString();
          } catch (_) {
            return '';
          }
        }

        function formatAdvertisedForwardTarget(targetUrl, port) {
          const fallback = `127.0.0.1:${port}`;
          if (!targetUrl) {
            return fallback;
          }
          try {
            const parsed = new URL(targetUrl);
            const pathname = parsed.pathname && parsed.pathname !== '/' ? parsed.pathname : '';
            return `${parsed.host}${pathname}`;
          } catch (_) {
            return fallback;
          }
        }

                function syncAdvertisedWebsitesField() {
                    if (autoyouAdvertisedWebsitesInput) {
                        autoyouAdvertisedWebsitesInput.value = JSON.stringify(autoyouAdvertisedWebsites);
          }
        }

        function syncBookmarksField() {
          if (autoyouBookmarksInput) {
            autoyouBookmarksInput.value = JSON.stringify(autoyouBookmarks);
          }
        }

                function renderAdvertisedWebsites() {
                    if (!autoyouAdvertisedWebsitesList) {
            return;
          }
                    if (!autoyouAdvertisedWebsites.length) {
                        autoyouAdvertisedWebsitesList.innerHTML = '';
                        if (autoyouAdvertisedWebsitesEmpty) {
                            autoyouAdvertisedWebsitesEmpty.style.display = '';
            }
            return;
          }
                    if (autoyouAdvertisedWebsitesEmpty) {
                        autoyouAdvertisedWebsitesEmpty.style.display = 'none';
          }
                    autoyouAdvertisedWebsitesList.innerHTML = autoyouAdvertisedWebsites.map((entry, index) => {
            const description = entry.description
                            ? `<div class='muted' style='font-size: 12px;'>${escapeAdvertisedWebsiteHtml(entry.description)}</div>`
              : '';
            return `
              <div style="display: flex; align-items: center; gap: 12px; padding: 12px; background: #111827; border: 1px solid #1f2937; border-radius: 8px;">
                <label class='checkbox-container' style='margin: 0;'>
                  <input type='checkbox' data-autoyou-port-toggle='${index}' ${entry.enabled !== false ? 'checked' : ''}>
                  <span class='checkmark'></span>
                </label>
                <div style='flex: 1; min-width: 0;'>
                  <div style='display: flex; align-items: center; gap: 10px; flex-wrap: wrap;'>
                    <strong style='color: #22c55e;'>:${entry.port}</strong>
                                        <span>${escapeAdvertisedWebsiteHtml(entry.label || `Website :${entry.port}`)}</span>
                  </div>
                                    <div class='muted' style='font-size: 12px;'>Forwards to ${escapeAdvertisedWebsiteHtml(formatAdvertisedForwardTarget(entry.target_url, entry.port))}</div>
                  ${description}
                </div>
                <button type='button' class='secondary-btn' data-autoyou-port-remove='${index}'>Remove</button>
              </div>
            `;
          }).join('');
        }

                function loadAdvertisedWebsites() {
                    if (!autoyouAdvertisedWebsitesInput) {
            return;
          }
          try {
                        const parsed = JSON.parse(autoyouAdvertisedWebsitesInput.value || '[]');
                        autoyouAdvertisedWebsites = Array.isArray(parsed) ? parsed : [];
          } catch (_) {
                        autoyouAdvertisedWebsites = [];
          }
                    syncAdvertisedWebsitesField();
                    renderAdvertisedWebsites();
        }

        function renderBookmarks() {
          if (!autoyouBookmarksList) {
            return;
          }
          if (!autoyouBookmarks.length) {
            autoyouBookmarksList.innerHTML = '';
            if (autoyouBookmarksEmpty) {
              autoyouBookmarksEmpty.style.display = '';
            }
            return;
          }
          if (autoyouBookmarksEmpty) {
            autoyouBookmarksEmpty.style.display = 'none';
          }
          autoyouBookmarksList.innerHTML = autoyouBookmarks.map((entry, index) => {
            const description = entry.description
              ? `<div class='muted' style='font-size: 12px;'>${escapeAdvertisedWebsiteHtml(entry.description)}</div>`
              : '';
            return `
              <div style="display: flex; align-items: center; gap: 12px; padding: 12px; background: #111827; border: 1px solid #1f2937; border-radius: 8px;">
                <label class='checkbox-container' style='margin: 0;'>
                  <input type='checkbox' data-autoyou-bookmark-toggle='${index}' ${entry.enabled !== false ? 'checked' : ''}>
                  <span class='checkmark'></span>
                </label>
                <div style='flex: 1; min-width: 0;'>
                  <div style='display: grid; gap: 8px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));'>
                    <input type='text' data-autoyou-bookmark-field='title' data-autoyou-bookmark-index='${index}' value='${escapeAdvertisedWebsiteHtml(entry.title || '')}' placeholder='Title'>
                    <input type='text' data-autoyou-bookmark-field='url' data-autoyou-bookmark-index='${index}' value='${escapeAdvertisedWebsiteHtml(entry.url || '')}' placeholder='https://autoyou.me'>
                  </div>
                  <input type='text' data-autoyou-bookmark-field='description' data-autoyou-bookmark-index='${index}' value='${escapeAdvertisedWebsiteHtml(entry.description || '')}' placeholder='Description' style='margin-top: 8px; width: 100%;'>
                </div>
                ${entry.url ? `<a class='secondary-btn' href='${escapeAdvertisedWebsiteHtml(entry.url)}' target='_blank' rel='noreferrer'>Open</a>` : ''}
                <button type='button' class='secondary-btn' data-autoyou-bookmark-remove='${index}'>Remove</button>
              </div>
            `;
          }).join('');
        }

        function loadBookmarks() {
          if (!autoyouBookmarksInput) {
            return;
          }
          try {
            const parsed = JSON.parse(autoyouBookmarksInput.value || '[]');
            autoyouBookmarks = Array.isArray(parsed) ? parsed : [];
          } catch (_) {
            autoyouBookmarks = [];
          }
          syncBookmarksField();
          renderBookmarks();
        }

        function normalizeBookmarkUrl(rawValue) {
          const trimmed = String(rawValue || '').trim();
          if (!trimmed) {
            return '';
          }
          const candidate = /^[a-z][a-z0-9+.-]*:\\/\\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
          try {
            const parsed = new URL(candidate);
            if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
              return '';
            }
            return parsed.toString();
          } catch (_) {
            return '';
          }
        }

        function addBookmark() {
          const titleField = document.getElementById('autoyou_bookmark_title');
          const urlField = document.getElementById('autoyou_bookmark_url');
          const descriptionField = document.getElementById('autoyou_bookmark_description');
          const title = String(titleField?.value || '').trim();
          const rawUrl = String(urlField?.value || '').trim();
          const url = normalizeBookmarkUrl(rawUrl);
          const description = String(descriptionField?.value || '').trim();
          if (!url) {
            alert('Enter a valid http(s) bookmark URL.');
            return;
          }
          autoyouBookmarks.push({ title: title || url, url, description, enabled: true });
          if (titleField) titleField.value = '';
          if (urlField) urlField.value = '';
          if (descriptionField) descriptionField.value = '';
          syncBookmarksField();
          renderBookmarks();
        }

                function addAdvertisedWebsite() {
                    const portField = document.getElementById('autoyou_advertised_website_number');
                    const labelField = document.getElementById('autoyou_advertised_website_label');
                    const descriptionField = document.getElementById('autoyou_advertised_website_description');
                    const targetUrlField = document.getElementById('autoyou_advertised_website_target_url');
          const port = parseInt(portField?.value || '', 10);
          const label = String(labelField?.value || '').trim();
          const description = String(descriptionField?.value || '').trim();
          const targetUrlRaw = String(targetUrlField?.value || '').trim();
          const targetUrl = normalizeAdvertisedForwardUrl(targetUrlRaw);

          if (!Number.isInteger(port) || port < 1 || port > 65535) {
                        alert('Enter a valid advertised website port between 1 and 65535.');
            return;
          }
                    if (autoyouAdvertisedWebsitesReserved.has(port)) {
                        alert(`Port ${port} is already managed automatically by AutoYou. Choose a different website port.`);
            return;
          }
                    if (autoyouAdvertisedWebsites.some((entry) => Number(entry.port) === port)) {
                        alert(`Port ${port} is already in the advertised websites list.`);
            return;
          }
          if (targetUrlRaw && !targetUrl) {
            alert('Enter a valid forward URL, host:port pair, or leave it blank.');
            return;
          }

                    autoyouAdvertisedWebsites.push({
            port,
                        label: label || `Website :${port}`,
            description,
            target_url: targetUrl,
            enabled: true,
          });
          if (portField) portField.value = '';
          if (labelField) labelField.value = '';
          if (descriptionField) descriptionField.value = '';
          if (targetUrlField) targetUrlField.value = '';
                    syncAdvertisedWebsitesField();
                    renderAdvertisedWebsites();
        }

                if (autoyouAdvertisedWebsitesList) {
                    autoyouAdvertisedWebsitesList.addEventListener('click', (event) => {
            const target = event.target instanceof HTMLElement ? event.target : null;
            const removeIndex = target?.getAttribute('data-autoyou-port-remove');
            if (removeIndex == null) {
              return;
            }
            const index = parseInt(removeIndex, 10);
                        if (!Number.isInteger(index) || index < 0 || index >= autoyouAdvertisedWebsites.length) {
              return;
            }
                        autoyouAdvertisedWebsites.splice(index, 1);
                        syncAdvertisedWebsitesField();
                        renderAdvertisedWebsites();
          });
                    autoyouAdvertisedWebsitesList.addEventListener('change', (event) => {
            const target = event.target instanceof HTMLElement ? event.target : null;
            const toggleIndex = target?.getAttribute('data-autoyou-port-toggle');
            if (toggleIndex == null || !(target instanceof HTMLInputElement)) {
              return;
            }
            const index = parseInt(toggleIndex, 10);
                        if (!Number.isInteger(index) || index < 0 || index >= autoyouAdvertisedWebsites.length) {
              return;
            }
                        autoyouAdvertisedWebsites[index].enabled = target.checked;
                        syncAdvertisedWebsitesField();
          });
        }

        if (autoyouBookmarksList) {
          autoyouBookmarksList.addEventListener('click', (event) => {
            const target = event.target instanceof HTMLElement ? event.target : null;
            const removeIndex = target?.getAttribute('data-autoyou-bookmark-remove');
            if (removeIndex == null) {
              return;
            }
            const index = parseInt(removeIndex, 10);
            if (!Number.isInteger(index) || index < 0 || index >= autoyouBookmarks.length) {
              return;
            }
            autoyouBookmarks.splice(index, 1);
            syncBookmarksField();
            renderBookmarks();
          });
          autoyouBookmarksList.addEventListener('change', (event) => {
            const target = event.target instanceof HTMLElement ? event.target : null;
            const toggleIndex = target?.getAttribute('data-autoyou-bookmark-toggle');
            const fieldName = target?.getAttribute('data-autoyou-bookmark-field');
            if (toggleIndex == null || !(target instanceof HTMLInputElement)) {
              if (fieldName == null || !(target instanceof HTMLInputElement)) {
                return;
              }
              const fieldIndex = parseInt(target.getAttribute('data-autoyou-bookmark-index') || '', 10);
              if (!Number.isInteger(fieldIndex) || fieldIndex < 0 || fieldIndex >= autoyouBookmarks.length) {
                return;
              }
              autoyouBookmarks[fieldIndex][fieldName] = target.value;
              syncBookmarksField();
              return;
            }
            const index = parseInt(toggleIndex, 10);
            if (!Number.isInteger(index) || index < 0 || index >= autoyouBookmarks.length) {
              return;
            }
            autoyouBookmarks[index].enabled = target.checked;
            syncBookmarksField();
          });
        }

                const addAdvertisedWebsiteButton = document.getElementById('autoyou-add-advertised-website-btn');
                if (addAdvertisedWebsiteButton) {
                    addAdvertisedWebsiteButton.addEventListener('click', addAdvertisedWebsite);
        }

        const addBookmarkButton = document.getElementById('autoyou-add-bookmark-btn');
        if (addBookmarkButton) {
          addBookmarkButton.addEventListener('click', addBookmark);
        }

        const autoyouPageSettingsForm = document.getElementById('autoyou-page-settings-form');
        if (autoyouPageSettingsForm) {
                    autoyouPageSettingsForm.addEventListener('submit', () => {
                      syncAdvertisedWebsitesField();
                      syncBookmarksField();
                    });
        }

                loadAdvertisedWebsites();
        loadBookmarks();
        
        // Public reverse proxy control functions
        async function refreshPublicProxyStatus() {
          try {
            const response = await fetch('/api/tunnelmole/status');
            const data = await response.json();

            const statusElement = document.getElementById('tunnelmole-status');
            if (statusElement) {
              statusElement.textContent = data.status || 'Unknown';
            }

            const urlElement = document.getElementById('tunnelmole-url');
            if (urlElement) {
              urlElement.textContent = data.public_url || 'Not available';
            }

            console.log('Public link status refreshed:', data);
          } catch (e) {
            console.error('Error refreshing public link status:', e);
          }
        }

        async function startPublicProxy() {
            try {
                const response = await fetch('/tunnelmole/start', { method: 'POST' });
                const data = await response.json();

                alert(data.status || 'Public link started');
                await refreshPublicProxyStatus();
            } catch (e) {
                console.error('Error starting public link:', e);
                alert('Error starting public link: ' + e.message);
            }
        }
        
        async function stopPublicProxy() {
          try {
            if (!confirm('Are you sure you want to stop the public link?')) {
              return;
            }
            
            const response = await fetch('/tunnelmole/stop', { method: 'POST' });
            const data = await response.json();
            
            alert(data.status || 'Public link stopped');
            
            // Refresh status after stopping
            await refreshPublicProxyStatus();
          } catch (e) {
            console.error('Error stopping public link:', e);
            alert('Error stopping public link: ' + e.message);
          }
        }
      </script>
    """
    # Header bar with server name, Shutdown, and Logout
    header_brand_html = _build_admin_brand_markup(server_name)
    header = f"""
    <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;gap:16px;flex-wrap:wrap'>
      <div class='admin-header-brand'>{header_brand_html}</div>
      <div style='display:flex;gap:12px;align-items:center'>
        <button onclick='confirmShutdown()' class='danger-btn header-action-btn' title='Shutdown AutoYou Server'>
          Shutdown Server
        </button>
        <a href='/logout' class='secondary-btn header-action-btn' title='Logout'>Logout</a>
      </div>
    </div>
    """
    
    # AutoYou Cloud card
    _cloud_conn_color = "#22c55e" if _cloud_connected else "#f59e0b"
    _cloud_conn_text = "Connected" if _cloud_connected else "Disconnected"
    if _cloud_enrolled:
      if _cloud_token_rejected:
        _cloud_registered_detail = "<p style='margin:8px 0'><span style='color:#f59e0b;font-weight:600'>&#x26A0; Re-registration required</span>"
      elif _cloud_is_active is False:
        _cloud_registered_detail = "<p style='margin:8px 0'><span style='color:#f59e0b;font-weight:600'>&#x26A0; Linked, but not receiving client requests</span>"
      elif _cloud_registered:
        _cloud_registered_detail = "<p style='margin:8px 0'><span style='color:#22c55e;font-weight:600'>&#x2705; Registered</span>"
      else:
        _cloud_registered_detail = "<p style='margin:8px 0'><span style='color:#93c5fd;font-weight:600'>&#x2601;&#xFE0F; Linked</span>"
      if _cloud_email:
        _cloud_registered_detail += f" &mdash; <span class='muted'>{_cloud_email}</span>"
      _cloud_registered_detail += "</p>"
      if _cloud_server_id:
        _cloud_registered_detail += f"<p class='muted' style='font-size:0.85em'>Server ID: {_cloud_server_id}</p>"

      if _cloud_token_rejected:
        _cloud_registered_detail += (
          f"<p class='muted' style='margin-top:8px;font-size:0.9em'>{_cloud_status_message or 'This server is no longer the active registered server for this account. Re-register it from AutoYou Cloud to receive relay traffic again.'}</p>"
        )
        if _cloud_reregister_url:
          _cloud_registered_detail += (
            f"<div style='margin-top:12px'><a href='{_cloud_reregister_url}' target='_blank' rel='noopener' class='btn' style='background:#007aff;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:600;display:inline-block'>Re-register This Server</a></div>"
          )
      elif _cloud_is_active is False:
        _cloud_registered_detail += (
          f"<p class='muted' style='margin-top:8px;font-size:0.9em'>{_cloud_status_message or 'Another linked server is active. Cloud Pair client requests are routed only to that server until you make this server active.'}</p>"
        )
        if _cloud_sse_connected:
          _cloud_registered_detail += (
            "<p class='muted' style='margin-top:8px;font-size:0.85em'>A saved listener may still be open, but active-server routing prevents this server from receiving new Cloud Pair requests.</p>"
          )
        if _cloud_activate_url:
          _cloud_registered_detail += (
            f"<div style='margin-top:12px'><button type='button' class='btn' style='background:#007aff;color:#fff;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-size:14px' onclick=\"triggerCloudAction('{_cloud_activate_url}', 'This server is now the active AutoYou Cloud server. New Cloud Pair requests will come here.');\">Make This Server Active</button></div>"
          )
      else:
        _cloud_registered_detail += f"<p style='margin:8px 0;font-size:0.9em'>Cloud Relay: <span style='color:{_cloud_conn_color};font-weight:600'>{_cloud_conn_text}</span></p>"
        if _cloud_info_unreachable:
          _cloud_registered_detail += (
            f"<p class='muted' style='margin-top:8px;font-size:0.85em'>{_cloud_status_message or 'Active-server status could not be refreshed right now. Local relay state is shown from this server process.'}</p>"
          )
        if _cloud_connected:
          _cloud_registered_detail += "<form method='post' action='/api/cloud/push-client' style='margin-top:8px'><button type='submit' class='btn' style='background:#007aff;color:#fff;border:none;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:14px'>&#x1F4F2; Request Client to Connect</button></form>"
        else:
          _cloud_registered_detail += "<div style='margin-top:12px'><button type='button' class='btn' style='background:#007aff;color:#fff;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-size:14px' onclick=\"triggerCloudAction('/api/cloud/reregister', 'Cloud relay restart requested.');\">Reconnect Cloud Relay</button></div>"

      _cloud_registered_detail += (
        "<div style='margin-top:16px;padding-top:14px;border-top:1px solid rgba(148,163,184,.24)'>"
        "<h3 style='margin:0 0 8px;font-size:1rem'>Offline Notification Test</h3>"
        "<p class='muted' style='font-size:0.86em;margin:0 0 10px'>Sends through the linked AutoYou account. Client devices must opt in, and background push requires the offline notification tier.</p>"
        "<label for='cloud-notify-title' style='display:block;font-size:0.82em;color:#94a3b8;margin-bottom:4px'>Title</label>"
        "<input id='cloud-notify-title' type='text' value='AutoYou' style='width:100%;box-sizing:border-box;margin-bottom:8px;padding:8px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0'>"
        "<label for='cloud-notify-body' style='display:block;font-size:0.82em;color:#94a3b8;margin-bottom:4px'>Message</label>"
        "<textarea id='cloud-notify-body' rows='3' style='width:100%;box-sizing:border-box;margin-bottom:8px;padding:8px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0'>Notification from your AutoYou server.</textarea>"
        "<label for='cloud-notify-category' style='display:block;font-size:0.82em;color:#94a3b8;margin-bottom:4px'>Category</label>"
        "<input id='cloud-notify-category' type='text' value='admin' style='width:100%;box-sizing:border-box;margin-bottom:10px;padding:8px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0'>"
        "<button type='button' class='btn' style='background:#007aff;color:#fff;border:none;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:14px' onclick='sendCloudOfflineNotification();'>Send Notification</button>"
        "</div>"
      )
      _cloud_registered_detail += "<form method='post' action='/api/cloud/unregister' style='margin-top:12px'><button type='submit' class='danger-btn' onclick=\"return confirm('Disconnect this server from AutoYou Cloud?')\">Disconnect</button></form>"
    else:
        _cloud_registered_detail = "<div style='margin-top:12px'><a href='/api/cloud/link-start' target='_blank' rel='noopener' class='btn' style='background:#007aff;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:600;display:inline-block'>&#x1F517; Link to AutoYou Cloud</a></div><p class='muted' style='margin-top:8px;font-size:0.9em'>Opens the AutoYou Cloud sign-in flow in a new tab so this local admin page stays open.</p>"
    _cloud_card_html = f"""<div class='card' id='autoyou-cloud-card' data-search-aliases='AutoYou Cloud Pair, cloud pair, remote pairing, relay reconnect, mobile pairing'>
     <h2>&#x2601;&#xFE0F; AutoYou Cloud</h2>
     <p class='muted'>Register this server with the same paid AutoYou account used on your iPhone, Android device, web dashboard, and desktop clients.</p>
     {_cloud_registered_detail}
   </div>"""

    # Signal configuration UI
    signal_ui = f"""
      <div class='card' id='signal-settings-card'>
        <div class='signal-header'>
          <h2>Messaging Partner Settings - Signal</h2>
          <button type='button' id='signal-restart-btn-header' data-no-loading='1' onclick='restartSignal()' class='restart-btn' {'style="display:inline-flex"' if signal_enabled else 'style="display:none"'}>
            <svg class='restart-icon' viewBox='0 0 24 24'>
              <path d='M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z'/>
            </svg>
            Restart Service
          </button>
        </div>
        <p class='muted signal-status-text'>Status: {signal_status} - {signal_name}</p>
        {f'<p class="muted">Paired Phone Number: <b>{signal_phone_number}</b></p>' if signal_paired and signal_phone_number else ''}
        <form method='post' action='/save-config'>
          <input type='hidden' name='kind' value='signal'>
          <div class='row'>
            <div class='col'>
              <label class='checkbox-container'>
                <input type='checkbox' name='signal_enabled' value='true' {'checked' if signal_enabled else ''}>
                <span class='checkmark'></span>
                <span class='checkbox-label'>Enable Signal Messaging Partner</span>
              </label>
            </div>
          </div>
          <div class='row'>
            <div class='col'>
              <label class='checkbox-container'>
                <input type='checkbox' name='signal_shutdown_docker' value='true' {'checked' if signal_shutdown_docker else ''}>
                <span class='checkmark'></span>
                <span class='checkbox-label'>Shut down Signal Messaging Service (Docker) upon Server Shutdown</span>
              </label>
            </div>
          </div>
          <div class='row'>
            <div class='col'>
              <label for='signal_port'>Signal API Port</label>
              <input type='number' id='signal_port' name='signal_port' placeholder='Port' value='{signal_port}' min='1' max='65535'>
            </div>
            <div class='col'>
              <label for='signal_device_name'>Device Name</label>
              <input type='text' id='signal_device_name' name='signal_device_name' placeholder='Device Name' value='{signal_device_name}' {'readonly' if signal_paired else ''} {'style="background-color: #1a1a1a; color: #888;"' if signal_paired else ''}>
              {f'<small class="muted">Device name is read-only when paired. Current name from Signal API.</small>' if signal_paired else '<small class="muted">Device name can only be set before pairing.</small>'}
            </div>
          </div>
          <div style='margin-top:16px'>
            <button type='submit' class='primary-btn'>Save Signal Configuration</button>
            <button type='button' id='signal-qr-btn' onclick='showSignalQR()' class='secondary-btn' {'style="display:none"' if signal_paired else ''}>Show QR Code</button>
            <button type='button' id='signal-cleanup-btn' onclick='cleanupSignal()' class='danger-btn'>Cleanup & Re-pair</button>
          </div>
        </form>
        <div id='signal-qr-container' style='margin-top:12px;text-align:center;'></div>
      </div>
      <script>
        // Auto-render QR code if Signal is enabled but not paired
        document.addEventListener('DOMContentLoaded', function() {{
          if ({str(signal_enabled).lower()} && {json.dumps(signal_status)} !== "Connected" && !{str(signal_paired).lower()}) {{
            signalQrAutoRefreshEnabled = true;
            scheduleSignalQrRefresh(true);
          }}
          
          // Update device name from API if paired
          if ({str(signal_paired).lower()}) {{
            updateDeviceNameFromAPI();
          }}
        }});
        
        async function updateDeviceNameFromAPI() {{
          try {{
            const response = await fetch('/api/signal/device-name');
            if (response.ok) {{
              const data = await response.json();
              if (data.success && data.device_name) {{
                document.getElementById('signal_device_name').value = data.device_name;
              }}
            }}
          }} catch (e) {{
            console.error('Failed to fetch device name from API:', e);
          }}
        }}
      </script>
    """

    admin_search_card_html = build_admin_search_card_html()
    admin_ui_button_loading_script_html = """
    <script>
    /* ── Universal button-loading utility ────────────────────────────── *
     * Covers:                                                             *
     *   1. All <form> submit events (traditional full-page-reload forms) *
     *   2. Explicit fetch-based actions via btnLoading(el) / btnDone(el) *
     * Works transparently through DataChannel HTTP tunnelling because the *
     * spinner is pure CSS driven by a class on the button element.        *
     * ─────────────────────────────────────────────────────────────────── */
    (function () {
      'use strict';

      // Restore a btn after navigation restores the page (bfcache / back-button)
      window.addEventListener('pageshow', function (e) {
        if (e.persisted) {
          document.querySelectorAll('button.loading, button[data-loading]').forEach(function (btn) {
            btn.classList.remove('loading');
            btn.removeAttribute('data-loading');
            btn.disabled = false;
          });
        }
      });

      // Public helpers consumed by async action JS throughout the admin page
      window.btnLoading = function (el, opts) {
        if (!el) return;
        opts = opts || {};
        el.classList.add('loading');
        el.setAttribute('data-loading', '1');
        if (opts.disable !== false) el.disabled = true;
        if (opts.label) el._btnOrigLabel = el.textContent;
      };
      window.btnDone = function (el) {
        if (!el) return;
        el.classList.remove('loading');
        el.removeAttribute('data-loading');
        el.disabled = false;
      };

      // Intercept ALL form submits - mark the submit button as loading.
      // The state clears automatically when the browser navigates away.
      document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form || form.tagName !== 'FORM') return;
        // Find the button that triggered the submit (Chrome 15+)
        var trigger = form.querySelector('button[type="submit"]:not([disabled])') ||
                      form.querySelector('button:not([type]):not([disabled])') ||
                      (document.activeElement && document.activeElement.closest &&
                       document.activeElement.closest('form') === form &&
                       document.activeElement.tagName === 'BUTTON' ? document.activeElement : null);
        if (!trigger) return;
        // Don't double-start if already loading
        if (trigger.classList.contains('loading') || trigger.getAttribute('data-loading')) return;
        window.btnLoading(trigger, { disable: true });
        // Safety net: always clear after 18 s so a failed XHR never locks the page
        window.setTimeout(function () { window.btnDone(trigger); }, 18000);
      }, true);

      // ── Global click delegation for type="button" async actions ──────────
      // Attaches a spinner to any button that doesn't match the skip list.
      // Pure-UI interactions (modals, nav, search) are excluded by selector
      // or onclick-attribute pattern so they don't get locked.
      (function () {
        // CSS selectors whose matched buttons never trigger a server round-trip
        var SKIP_SEL = [
          '[data-no-loading]',
          '[data-step]',
          '.admin-search-toggle',
          '.admin-search-reset',
          '#admin-search-icon-btn',
          '.admin-jump-chip',
          '[data-admin-jump]',
          '.agent-studio-tab',
          '.wizard-step-btn',
          '.modal-btn-cancel',
          '.note-modal-close',
          '.note-modal-backdrop',
          '.model-source-tab',
          '.model-result-card',
        ].join(',');

        // onclick attribute patterns that are pure UI (no fetch/XHR)
        var SKIP_ONCLICK = /\\b(hide\\w+|showRestartModal|showSignalRestartModal|openSetupWizard|openAdvancedSection|skipToAdvanced|skipToAdvancedSettings|showWizardStep|restartWhatsApp|restartSignal|showQrProvisionConfirm|closeQrProvisionDisplay|openAgentFrontend|copyAgentFrontendUrl|toggleSecretInput|adminSearch\\w*|adminJump\\w*|adminMg\\w*|setModal\\w*|closeNote\\w*|configureFrontends\\w*)\\b/i;

        document.addEventListener('click', function (e) {
          var btn = e.target && e.target.closest ? e.target.closest('button') : null;
          if (!btn || btn.type === 'submit') return;    // form submits handled above
          if (btn.disabled || btn.classList.contains('loading') || btn.hasAttribute('data-loading')) return;
          try { if (btn.matches(SKIP_SEL)) return; } catch (_) {}
          var oc = btn.getAttribute('onclick') || '';
          if (SKIP_ONCLICK.test(oc)) return;
          window.btnLoading(btn, { disable: false });
          var t = window.setTimeout(function () {
            if (btn._btnCT === t) window.btnDone(btn);
          }, 10000);
          btn._btnCT = t;
        }, true);

        // Patch btnDone to also clear the auto-reset timer
        var _origBtnDone = window.btnDone;
        window.btnDone = function (el) {
          _origBtnDone(el);
          if (el && el._btnCT) { clearTimeout(el._btnCT); el._btnCT = null; }
        };
      })();
    })();
    </script>
    """
    admin_search_script_html = (
        build_admin_search_script_html()
        + build_admin_navigation_script_html()
        + admin_ui_button_loading_script_html
    )
    agent_studio_panel_html = _build_agent_studio_panel_html()
    
    return f"""
    {setup_wizard_styles}
    {wizard_overlay_html}
    {header}
    {banner_html}
    {default_warn}
    {wizard_launcher_html}
    {admin_search_card_html}
    {build_admin_dashboard_shell_open_html(server_name, show_setup_wizard=show_onboarding_wizard)}

    <div class='card setup-hero-card' id='dashboard-overview-card'>
      <h1>{server_name_display} Admin</h1>
      <p class='muted'>Manage Messaging Partners, Call Servers, Public Web Proxies,and other integration settings along with your name and password.</p>
      <p class='muted'>Server name: <b>{server_name_display}</b></p>
    </div>

    <div class='card' id='server-settings-card'>
      <h2>Server Settings - Display Name</h2>
      <form method='post' action='/save-config'>
        <input type='hidden' name='kind' value='server'>
        <input type='text' name='server_name' placeholder='Server name' value='{server_name_display}'>
        <div style='margin-top:8px'>
          <button type='submit'>Save Server Name</button>
        </div>
      </form>
    </div>

    <div class='card' id='ai-agent-settings-card' data-search-aliases='AutoYou AI, AI agent restart, restart AutoYou AI, start agent, stop agent'>
      <h2>AI Agent Server Control</h2>
      <p class='muted'>Manage the AI Agent Server running on port {AI_AGENT_SERVER_PORT}</p>

    <div class='card' id='ollama-settings-card' style='margin-top: 12px; padding: 12px;'>
      <h3 style='margin:0;'>AI Provider Settings</h3>
      <p class='muted'>Select the AI backend for AutoYou. Only one provider is active at a time. Ollama is the preferred local default.</p>

      <!-- Active provider summary banner -->
      <div class='provider-summary-bar'>
        <strong>Active:</strong> <span id='provider-summary-text'>{ai_provider_summary}</span>
        <button type='button' onclick='refreshOllamaStatus()' class='secondary-btn' style='margin-left:12px; padding:2px 10px; font-size:0.82em;'>Refresh</button>
      </div>

      <!-- Provider radio selector -->
      <form method='post' action='/save-config' id='ai-provider-form'>
        <input type='hidden' name='kind' value='ollama'>

        <!-- Provider card CSS (scoped to this form) -->
        <style>
        .prov-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:18px}}
        .prov-card-lbl{{position:relative;display:block;padding:14px 16px;border:2px solid rgba(30,41,59,.92);border-radius:16px;cursor:pointer;background:linear-gradient(180deg,rgba(13,22,38,.96),rgba(10,18,32,.92));transition:border-color .2s,background .2s,box-shadow .2s;user-select:none}}
        .prov-card-lbl:hover{{border-color:#334155;background:linear-gradient(180deg,rgba(17,30,48,.98),rgba(13,24,40,.94))}}
        .prov-card-lbl.active{{border-color:#3b82f6;background:linear-gradient(180deg,rgba(12,31,64,.98),rgba(11,28,54,.94));box-shadow:0 0 0 1px rgba(59,130,246,.25),0 10px 20px rgba(2,6,23,.18)}}
        .prov-card-lbl input[type=radio]{{position:absolute;opacity:0;width:0;height:0;pointer-events:none}}
        .prov-check-badge{{display:none;position:absolute;top:10px;right:10px;width:20px;height:20px;background:#3b82f6;border-radius:50%;color:#fff;font-size:11px;text-align:center;line-height:20px;font-weight:bold}}
        .prov-card-lbl.active .prov-check-badge{{display:block}}
        .prov-icon{{font-size:1.45em;margin-bottom:5px;line-height:1;font-family:"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif}}
        .prov-name{{font-weight:700;font-size:.91em;color:#e2e8f0;margin-bottom:2px}}
        .prov-card-lbl.active .prov-name{{color:#93c5fd}}
        .prov-tag{{font-size:.76em;color:#475569;line-height:1.3}}
        .prov-card-lbl.active .prov-tag{{color:#7dd3fc}}
        @media(max-width:540px){{.prov-grid{{grid-template-columns:1fr}}}}
        </style>

        <div style='margin-bottom:18px;'>
          <label style='display:block; font-weight:600; margin-bottom:10px; color:#e2e8f0;'>Select AI Provider</label>
          <div class='prov-grid'>
            {_apple_provider_card}

            <label class='prov-card-lbl {"active" if _active_provider == "ollama" else ""}' id='prov-lbl-ollama'>
              <input type='radio' name='ai_provider' value='ollama' {'checked' if _active_provider == 'ollama' else ''} onchange='syncProviderPanels(this.value)'>
              <span class='prov-check-badge'>&#10003;</span>
              <div class='prov-icon'>&#129433;</div>
              <div class='prov-name'>Ollama</div>
              <div class='prov-tag'>Local &middot; Privacy-first (Local-only) &middot; No internet</div>
            </label>

            <label class='prov-card-lbl {"active" if _active_provider == "ollama_gateway" else ""}' id='prov-lbl-ollama_gateway'>
              <input type='radio' name='ai_provider' value='ollama_gateway' {'checked' if _active_provider == 'ollama_gateway' else ''} onchange='syncProviderPanels(this.value)'>
              <span class='prov-check-badge'>&#10003;</span>
              <div class='prov-icon'>&#128268;</div>
              <div class='prov-name'>Ollama Native Gateway</div>
              <div class='prov-tag'>Direct Ollama chat &middot; No AutoYou AI worker</div>
            </label>

            <label class='prov-card-lbl {"active" if _active_provider == "odysseus" else ""}' id='prov-lbl-odysseus'>
              <input type='radio' name='ai_provider' value='odysseus' {'checked' if _active_provider == 'odysseus' else ''} onchange='syncProviderPanels(this.value)'>
              <span class='prov-check-badge'>&#10003;</span>
              <div class='prov-icon'>&#127756;</div>
              <div class='prov-name'>Odysseus</div>
              <div class='prov-tag'>External companion service &middot; Direct sessions</div>
            </label>

            <label class='prov-card-lbl {"active" if _active_provider == "openclaw" else ""}' id='prov-lbl-openclaw'>
              <input type='radio' name='ai_provider' value='openclaw' {'checked' if _active_provider == 'openclaw' else ''} onchange='syncProviderPanels(this.value)'>
              <span class='prov-check-badge'>&#10003;</span>
              <div class='prov-icon'>&#129438;</div>
              <div class='prov-name'>OpenClaw</div>
              <div class='prov-tag'>Local gateway &middot; Flexible routing</div>
            </label>

            <label class='prov-card-lbl {"active" if _active_provider == "hermes" else ""}' id='prov-lbl-hermes'>
              <input type='radio' name='ai_provider' value='hermes' {'checked' if _active_provider == 'hermes' else ''} onchange='syncProviderPanels(this.value)'>
              <span class='prov-check-badge'>&#10003;</span>
              <div class='prov-icon'>&#129303;</div>
              <div class='prov-name'>Hermes Agent</div>
              <div class='prov-tag'>Local gateway &middot; NousResearch</div>
            </label>

            <label class='prov-card-lbl {"active" if _active_provider == "litellm" else ""}' id='prov-lbl-litellm'>
              <input type='radio' name='ai_provider' value='litellm' {'checked' if _active_provider == 'litellm' else ''} onchange='syncProviderPanels(this.value)'>
              <span class='prov-check-badge'>&#10003;</span>
              <div class='prov-icon'>&#x2601;&#xFE0F;</div>
              <div class='prov-name'>LiteLLM Cloud</div>
              <div class='prov-tag'>Anthropic &middot; OpenAI &middot; Mistral &middot; DeepSeek</div>
            </label>

            <label class='prov-card-lbl {"active" if _active_provider == "google" else ""}' id='prov-lbl-google'>
              <input type='radio' name='ai_provider' value='google' {'checked' if _active_provider == 'google' else ''} onchange='syncProviderPanels(this.value)'>
              <span class='prov-check-badge'>&#10003;</span>
              <div class='prov-icon'>&#x1F537;</div>
              <div class='prov-name'>Google Gemini</div>
              <div class='prov-tag'>Google AI &middot; Gemini Flash &amp; Pro</div>
            </label>

          </div>
        </div>

        <!-- ── Ollama panel ──────────────────────────────────────────── -->
        <div id='panel-ollama' class='provider-panel' style='display:{"block" if _active_provider in ("ollama", "ollama_gateway") else "none"};'>
          <div style='display:flex; align-items:center; margin-bottom:10px;'>
            <span style='display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:8px; background-color:{ollama_status_color};'></span>
            <strong>Ollama:</strong>&nbsp;<span style='color:#94a3b8;'>{ollama_status} &mdash; {ollama_models_count} model(s) &mdash; {ollama_selected_model}</span>
          </div>
          <div style='margin-bottom:12px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>API Base URL</label>
            <input type='text' name='ollama_api_base' placeholder='http://localhost:11434' value='{ollama_api_base}' style='width:100%;'>
            <small class='muted'>URL where Ollama is running</small>
          </div>
          <div style='margin-bottom:4px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Model</label>
            <input type='text' name='ollama_model' placeholder='ministral-3:8b' value='{ollama_model}' style='width:100%;'>
            <small class='muted'>Model name to use (e.g. ministral-3:8b, llama3.2, mistral)</small>
          </div>
        </div>

        <!-- ── Odysseus native gateway panel ────────────────────────── -->
        <div id='panel-odysseus' class='provider-panel' style='display:{"block" if _active_provider=="odysseus" else "none"};'>
          <p style='margin:0 0 10px; color:#94a3b8; font-size:0.88em;'>
            AutoYou sends chat directly through Odysseus's authenticated companion API. Its own model endpoints and sessions remain authoritative; AutoYou AI workers are not used for these replies.
          </p>
          <div style='margin-bottom:12px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Odysseus API Base URL</label>
            <input type='text' name='odysseus_api_base' placeholder='http://127.0.0.1:7000' value='{odysseus_api_base_val}' style='width:100%;'>
          </div>
          <div style='margin-bottom:12px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Odysseus Model <small style='font-weight:normal;'>(optional)</small></label>
            <input type='text' name='odysseus_model' placeholder='Leave blank for Odysseus default' value='{odysseus_model_val}' style='width:100%;'>
          </div>
          <div style='margin-bottom:4px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Chat API Token</label>
            <input type='password' name='odysseus_token' placeholder='Leave blank to keep saved token' value='{odysseus_token_display}' style='width:100%;'>
            <small class='muted'>Use a chat-scoped Odysseus token. It remains in protected AutoYou configuration.</small>
          </div>
        </div>

        <!-- ── OpenClaw provider panel ──────────────────────────────── -->
        <div id='panel-openclaw' class='provider-panel' style='display:{"block" if _active_provider=="openclaw" else "none"};'>
          <p style='margin:0 0 10px; color:#94a3b8; font-size:0.88em;'>
            Routes all LLM inference through the local OpenClaw Gateway. AutoYou&#39;s own sub-agents (notes, internet, memory...) remain fully functional &mdash; OpenClaw handles token generation only.
            <a href='https://docs.openclaw.ai/gateway/openai-http-api' target='_blank' style='color:#60a5fa;'>OpenClaw docs &#x2197;</a>
          </p>
          <p style='margin:0 0 10px; padding:8px 10px; background:#1c2333; border-left:3px solid #3b82f6; border-radius:0 4px 4px 0; color:#93c5fd; font-size:0.85em;'>
            <b>Note:</b> The <code>autoyou_openclaw_agent</code> sub-agent (Agent Management) is a separate integration.
            When OpenClaw is already your provider, installing the sub-agent adds no benefit unless you set its
            model alias to <code>openclaw:main</code> &mdash; which engages OpenClaw&#39;s own tool ecosystem
            (smart home, music, calendar, browser). For pure LLM routing you don&#39;t need the sub-agent at all.
          </p>
          <div id='openclaw-provider-status' class='provider-inline-status'>
            Checking OpenClaw...
          </div>
          <div style='margin-bottom:12px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Gateway Port</label>
            <input type='number' name='openclaw_port' placeholder='18789' value='{oc_port_val}' min='1024' max='65535' style='width:160px;'>
            <small class='muted'>OpenClaw HTTP API port (default: 18789)</small>
          </div>
          <div style='margin-bottom:12px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Bearer Token</label>
            <div style='display:flex; gap:8px;'>
              <input type='password' id='oc_prov_token' name='openclaw_token' placeholder='Leave blank for no auth' value='{oc_token_display}' data-raw='{oc_token_raw}' style='flex:1;'>
              <button type='button' class='secondary-btn' onclick='toggleSecret("oc_prov_token")'>Show</button>
            </div>
            <small class='muted'>Leave empty when OpenClaw is local-only</small>
          </div>
          <div style='margin-bottom:4px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Model / Agent Alias</label>
            <select name='{"openclaw_model" if oc_model_val in ("openclaw/default","nadirclaw/eco","openclaw:main","") else "_oc_model_sel_unused"}' id='oc_prov_model_select' onchange='syncOcModelInput("oc_prov_model_select","oc_prov_model_custom")' style='width:100%; margin-bottom:6px;'>
              <option value='openclaw/default' {'selected' if oc_model_val == 'openclaw/default' else ''}>openclaw/default &mdash; Fast inference</option>
              <option value='nadirclaw/eco' {'selected' if oc_model_val == 'nadirclaw/eco' else ''}>nadirclaw/eco &mdash; Capable inference</option>
              <option value='openclaw:main' {'selected' if oc_model_val == 'openclaw:main' else ''}>openclaw:main &mdash; OpenClaw full agent</option>
              <option value='custom' {'selected' if oc_model_val not in ("openclaw/default","nadirclaw/eco","openclaw:main","") else ''}>Custom...</option>
            </select>
            <input type='text' id='oc_prov_model_custom' name='{"_oc_model_custom_unused" if oc_model_val in ("openclaw/default","nadirclaw/eco","openclaw:main","") else "openclaw_model"}' placeholder='e.g. openclaw/default' value='{oc_model_val}'
              style='width:100%; display:{"none" if oc_model_val in ("openclaw/default","nadirclaw/eco","openclaw:main","") else "block"};'>
            <small class='muted'>Use openclaw/default for fastest responses; openclaw:main invokes OpenClaw&#39;s own agent pipeline</small>
          </div>
          <button type='button' class='secondary-btn' style='margin-top:10px;' onclick='checkOpenClawStatus()'>Check OpenClaw Status</button>
        </div>

        <!-- ── Hermes Agent panel ──────────────────────────────────────── -->
        <div id='panel-hermes' class='provider-panel' style='display:{"block" if _active_provider=="hermes" else "none"};'>
          <p style='margin:0 0 10px; color:#94a3b8; font-size:0.88em;'>
            Routes all LLM inference through the local <a href='https://github.com/NousResearch/hermes-agent' target='_blank' style='color:#60a5fa;'>NousResearch Hermes Agent</a> gateway.
            Hermes is an OpenAI-compatible local agent runtime with its own tool ecosystem.
            Start it with <code>hermes gateway</code>.
          </p>
          <div id='hermes-provider-status' class='provider-inline-status'>
            Checking Hermes...
          </div>
          <div style='margin-bottom:12px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Gateway Port</label>
            <input type='number' name='hermes_port' placeholder='8642' value='{hm_port_val}' min='1024' max='65535' style='width:160px;'>
            <small class='muted'>Hermes HTTP API port (default: 8642). Start with: <code>hermes gateway</code></small>
          </div>
          <div style='margin-bottom:12px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Bearer Token</label>
            <div style='display:flex; gap:8px;'>
              <input type='password' id='hm_prov_token' name='hermes_token' placeholder='Leave blank for no auth' value='{hm_token_display}' data-raw='{hm_token_raw}' style='flex:1;'>
              <button type='button' class='secondary-btn' onclick='toggleSecret("hm_prov_token")'>Show</button>
            </div>
            <small class='muted'>Set <code>API_SERVER_KEY</code> env var in Hermes to enable auth. Leave empty for local-only use.</small>
          </div>
          <div style='margin-bottom:4px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Model Name</label>
            <select name='{"hermes_model" if hm_model_val in ("hermes-agent","") else "_hm_model_sel_unused"}' id='hm_prov_model_select' onchange='syncHmModelInput()' style='width:100%; margin-bottom:6px;'>
              <option value='hermes-agent' {'selected' if hm_model_val == 'hermes-agent' else ''}>hermes-agent &mdash; Default Hermes model</option>
              <option value='custom' {'selected' if hm_model_val not in ("hermes-agent","") else ''}>Custom...</option>
            </select>
            <input type='text' id='hm_prov_model_custom' name='{"_hm_model_custom_unused" if hm_model_val in ("hermes-agent","") else "hermes_model"}' placeholder='e.g. hermes-agent' value='{hm_model_val}'
              style='width:100%; display:{"none" if hm_model_val in ("hermes-agent","") else "block"};'>
            <small class='muted'>Configurable via <code>API_SERVER_MODEL_NAME</code> env var in Hermes</small>
          </div>
          <button type='button' class='secondary-btn' style='margin-top:10px;' onclick='checkHermesStatus()'>Check Hermes Status</button>
        </div>

        <!-- ── LiteLLM Cloud panel ───────────────────────────────────── -->
        <div id='panel-litellm' class='provider-panel' style='display:{"block" if _active_provider=="litellm" else "none"};'>
          <p style='margin:0 0 10px; color:#94a3b8; font-size:0.88em;'>
            Supports Anthropic, OpenAI, Mistral, DeepSeek, xAI and more via LiteLLM.
            <a href='https://docs.litellm.ai/docs/providers' target='_blank' style='color:#60a5fa;'>LiteLLM providers &#x2197;</a>
          </p>
          <div style='margin-bottom:12px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Model</label>
            <select name='{"litellm_model" if ll_model_val in ("anthropic/claude-opus-4-5","anthropic/claude-sonnet-4-5","anthropic/claude-haiku-4-5","openai/gpt-4o","openai/gpt-4o-mini","mistral/mistral-large-latest","deepseek/deepseek-chat","xai/grok-3","") else "_ll_model_sel_unused"}' id='ll_model_select' onchange='syncLlModelInput()' style='width:100%; margin-bottom:6px;'>
              <option value='anthropic/claude-opus-4-5' {'selected' if ll_model_val == 'anthropic/claude-opus-4-5' else ''}>anthropic/claude-opus-4-5</option>
              <option value='anthropic/claude-sonnet-4-5' {'selected' if ll_model_val == 'anthropic/claude-sonnet-4-5' else ''}>anthropic/claude-sonnet-4-5 (recommended)</option>
              <option value='anthropic/claude-haiku-4-5' {'selected' if ll_model_val == 'anthropic/claude-haiku-4-5' else ''}>anthropic/claude-haiku-4-5 (fast)</option>
              <option value='openai/gpt-4o' {'selected' if ll_model_val == 'openai/gpt-4o' else ''}>openai/gpt-4o</option>
              <option value='openai/gpt-4o-mini' {'selected' if ll_model_val == 'openai/gpt-4o-mini' else ''}>openai/gpt-4o-mini (fast)</option>
              <option value='mistral/mistral-large-latest' {'selected' if ll_model_val == 'mistral/mistral-large-latest' else ''}>mistral/mistral-large-latest</option>
              <option value='deepseek/deepseek-chat' {'selected' if ll_model_val == 'deepseek/deepseek-chat' else ''}>deepseek/deepseek-chat</option>
              <option value='xai/grok-3' {'selected' if ll_model_val == 'xai/grok-3' else ''}>xai/grok-3</option>
              <option value='custom' {'selected' if ll_model_val and ll_model_val not in ("anthropic/claude-opus-4-5","anthropic/claude-sonnet-4-5","anthropic/claude-haiku-4-5","openai/gpt-4o","openai/gpt-4o-mini","mistral/mistral-large-latest","deepseek/deepseek-chat","xai/grok-3") else ''}>Custom...</option>
            </select>
            <input type='text' id='ll_model_custom' name='{"_ll_model_custom_unused" if ll_model_val in ("anthropic/claude-opus-4-5","anthropic/claude-sonnet-4-5","anthropic/claude-haiku-4-5","openai/gpt-4o","openai/gpt-4o-mini","mistral/mistral-large-latest","deepseek/deepseek-chat","xai/grok-3","") else "litellm_model"}' placeholder='e.g. anthropic/claude-sonnet-4-5'
              value='{ll_model_val}'
              style='width:100%; display:{"none" if ll_model_val in ("anthropic/claude-opus-4-5","anthropic/claude-sonnet-4-5","anthropic/claude-haiku-4-5","openai/gpt-4o","openai/gpt-4o-mini","mistral/mistral-large-latest","deepseek/deepseek-chat","xai/grok-3","") else "block"};'>
            <small class='muted'>Format: provider/model-name (e.g. anthropic/claude-sonnet-4-5)</small>
          </div>
          <div style='margin-bottom:12px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>API Key</label>
            <div style='display:flex; gap:8px;'>
              <input type='password' id='ll_api_key' name='litellm_api_key' placeholder='sk-...' value='{ll_api_key_display}' data-raw='{ll_api_key_raw}' style='flex:1;'>
              <button type='button' class='secondary-btn' onclick='toggleSecret("ll_api_key")'>Show</button>
            </div>
            <small class='muted'>Provider API key (Anthropic, OpenAI, Mistral, etc.)</small>
          </div>
          <div style='margin-bottom:4px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Custom Base URL <small style='font-weight:normal;'>(optional)</small></label>
            <input type='text' name='litellm_api_base' placeholder='https://openrouter.ai/api/v1' value='{ll_api_base_val}' style='width:100%;'>
            <small class='muted'>Use for OpenRouter, Helicone, self-hosted proxies, or corporate gateways</small>
          </div>
        </div>

        <!-- ── Google Gemini panel ───────────────────────────────────── -->
        <div id='panel-google' class='provider-panel' style='display:{"block" if _active_provider=="google" else "none"};'>
          <div style='display:flex; align-items:center; margin-bottom:10px;'>
            <span style='display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:8px; background-color:{google_status_color};'></span>
            <strong>Google API:</strong>&nbsp;<span style='color:#94a3b8;'>{google_status} &mdash; API Key: {google_api_key_status}</span>
          </div>
          <div style='margin-bottom:12px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Google Model</label>
            <input type='text' name='google_model' placeholder='gemini-2.5-flash' value='{google_model}' style='width:100%;'>
            <small class='muted'>Gemini model name (e.g. gemini-2.5-flash, gemini-2.5-pro)</small>
          </div>
          <div style='margin-bottom:4px;'>
            <label style='display:block; margin-bottom:4px; font-weight:bold;'>Google API Key</label>
            <div style='display:flex; gap:8px;'>
              <input type='password' id='google_api_key' name='google_api_key' placeholder='AIza...' value='{google_api_key_display}' data-api-key='{google_api_key_raw}' style='flex:1;'>
              <button type='button' id='google_api_key_toggle' class='secondary-btn' onclick='toggleSecret("google_api_key")'>Show</button>
            </div>
            <small class='muted'>Google AI Studio API key for Gemini access</small>
          </div>
        </div>

        <!-- ── OpenClaw Sub-Agent settings ───────────────────────────── -->
        <div id='panel-openclaw-bridge' class='provider-panel' style='display:{"block" if _active_provider=="openclaw" else "none"};'>
        <details style='margin-bottom:14px;'>
          <summary style='cursor:pointer; font-weight:bold; color:#94a3b8; padding:6px 0;'>
            OpenClaw Sub-Agent Settings <small style='font-weight:normal;'>(optional &mdash; for native OpenClaw tool integrations)</small>
          </summary>
          <div style='border:1px solid #374151; border-radius:8px; padding:14px; margin-top:8px; background:#0f172a;'>
            <p style='margin:0 0 10px; color:#94a3b8; font-size:0.88em;'>
              These settings control the <code>autoyou_openclaw_agent</code> sub-agent, which is <b>independent of the root LLM provider</b>.
              Install it from Agent Management to delegate smart-home, music, calendar, and browser tasks
              to OpenClaw&#39;s native tool ecosystem &mdash; regardless of whether Ollama, Google, or OpenClaw is
              your root provider.
            </p>
            <p style='margin:0 0 10px; padding:8px 10px; background:#1c2333; border-left:3px solid #f59e0b; border-radius:0 4px 4px 0; color:#fcd34d; font-size:0.83em;'>
              <b>When to install:</b> Use this sub-agent when you want OpenClaw to execute actions using its
              own tool integrations (Hue, Spotify, Reminders, etc.). Set the model alias to
              <code>openclaw:main</code> to invoke OpenClaw&#39;s full agent pipeline.<br>
              <b>When NOT to install:</b> If OpenClaw is already your root LLM provider and you only need
              its inference capability, skip this sub-agent &mdash; the provider routing already handles that.
            </p>
            <div style='margin-bottom:12px;'>
              <label style='display:block; margin-bottom:4px; font-weight:bold;'>Sub-Agent Gateway Port</label>
              <input type='number' name='openclaw_agent_port' placeholder='18789' value='{oca_port_val}' min='1024' max='65535' style='width:160px;'>
            </div>
            <div style='margin-bottom:12px;'>
              <label style='display:block; margin-bottom:4px; font-weight:bold;'>Sub-Agent Bearer Token</label>
              <div style='display:flex; gap:8px;'>
                <input type='password' id='oca_token' name='openclaw_agent_token' placeholder='Leave blank for no auth' value='{oca_token_display}' data-raw='{oca_token_raw}' style='flex:1;'>
                <button type='button' class='secondary-btn' onclick='toggleSecret("oca_token")'>Show</button>
              </div>
            </div>
            <div style='margin-bottom:4px;'>
              <label style='display:block; margin-bottom:4px; font-weight:bold;'>Sub-Agent Model / Agent Alias</label>
              <input type='text' name='openclaw_agent_model' placeholder='openclaw/default' value='{oca_model_val}' style='width:100%;'>
              <small class='muted'>Use openclaw/default for fast responses or openclaw:main for the full OpenClaw agent</small>
            </div>
          </div>
        </details>
        </div>

        <div style='margin-top:16px;'>
          <button type='submit' style='margin-right:8px;'>Save AI Provider Settings</button>
          <button type='button' onclick='refreshOllamaStatus()' class='secondary-btn'>Refresh Status</button>
        </div>
      </form>

      <script>
        (function() {{
          // Show/hide secret fields
          window.toggleSecret = function(inputId) {{
            var input = document.getElementById(inputId);
            if (!input) return;
            var raw = input.getAttribute('data-raw') || input.getAttribute('data-api-key') || '';
            var btn = input.nextElementSibling;
            if (input.type === 'password') {{
              input.type = 'text';
              if (raw) input.value = raw;
              if (btn) btn.textContent = 'Hide';
            }} else {{
              input.type = 'password';
              if (raw) input.value = '{MASKED_SECRET_PLACEHOLDER}';
              if (btn) btn.textContent = 'Show';
            }}
          }};

          // Provider panel toggle + card active state
          window.syncProviderPanels = function(provider) {{
            ['ollama','ollama_gateway','odysseus','openclaw','hermes','litellm','google','apple_intelligence'].forEach(function(p) {{
              // Show/hide detail panels
              var panel = document.getElementById('panel-' + p);
              if (panel) panel.style.display = (p === 'ollama' ? (provider === 'ollama' || provider === 'ollama_gateway') : p === provider) ? 'block' : 'none';
              // Update card selection state (works with both old and new card layout)
              var lbl = document.getElementById('prov-lbl-' + p);
              if (lbl) {{
                if (p === provider) {{
                  lbl.classList.add('active');
                }} else {{
                  lbl.classList.remove('active');
                }}
              }}
            }});
            var openclawBridgePanel = document.getElementById('panel-openclaw-bridge');
            if (openclawBridgePanel) {{
              openclawBridgePanel.style.display = (provider === 'openclaw') ? 'block' : 'none';
            }}
            if (provider === 'openclaw') checkOpenClawStatus();
            if (provider === 'hermes') checkHermesStatus();
          }};

          // OpenClaw model selector/custom input sync
          window.syncOcModelInput = function(selId, inputId) {{
            var sel = document.getElementById(selId);
            var inp = document.getElementById(inputId);
            if (!sel || !inp) return;
            if (sel.value === 'custom') {{
              inp.style.display = 'block';
              inp.name = 'openclaw_model';
              sel.name = '_openclaw_model_sel_unused';
            }} else {{
              inp.style.display = 'none';
              inp.name = '_openclaw_model_custom_unused';
              sel.name = 'openclaw_model';
            }}
          }};

          // LiteLLM model selector/custom input sync
          window.syncLlModelInput = function() {{
            var sel = document.getElementById('ll_model_select');
            var inp = document.getElementById('ll_model_custom');
            if (!sel || !inp) return;
            if (sel.value === 'custom') {{
              inp.style.display = 'block';
              inp.name = 'litellm_model';
              sel.name = '_ll_model_sel_unused';
            }} else {{
              inp.style.display = 'none';
              inp.name = '_ll_model_custom_unused';
              sel.name = 'litellm_model';
            }}
          }};

          // OpenClaw status check
          window.checkOpenClawStatus = async function() {{
            var el = document.getElementById('openclaw-provider-status');
            if (el) el.textContent = 'Checking OpenClaw Gateway...';
            try {{
              var resp = await fetch('/api/ai/openclaw/status');
              var data = await resp.json();
              if (el) {{
                if (data.running) {{
                  var models = (data.models || []).join(', ') || '(none listed)';
                  el.innerHTML = '&#x1F7E2; OpenClaw running on port ' + data.port + ' &mdash; models: ' + models;
                  el.style.color = '#4ade80';
                }} else {{
                  el.innerHTML = '&#x1F534; OpenClaw not running on port ' + data.port + ' &mdash; ' + (data.error || '');
                  el.style.color = '#f87171';
                }}
              }}
            }} catch(e) {{
              if (el) {{ el.innerHTML = 'Error checking status: ' + e; el.style.color = '#f87171'; }}
            }}
          }};

          // Hermes model selector/custom input sync
          window.syncHmModelInput = function() {{
            var sel = document.getElementById('hm_prov_model_select');
            var inp = document.getElementById('hm_prov_model_custom');
            if (!sel || !inp) return;
            if (sel.value === 'custom') {{
              inp.style.display = 'block';
              inp.name = 'hermes_model';
              sel.name = '_hm_model_sel_unused';
            }} else {{
              inp.style.display = 'none';
              inp.name = '_hm_model_custom_unused';
              sel.name = 'hermes_model';
            }}
          }};

          // Hermes status check
          window.checkHermesStatus = async function() {{
            var el = document.getElementById('hermes-provider-status');
            if (el) el.textContent = 'Checking Hermes Gateway...';
            try {{
              var resp = await fetch('/api/ai/hermes/status');
              var data = await resp.json();
              if (el) {{
                if (data.running) {{
                  var models = (data.models || []).join(', ') || '(none listed)';
                  el.innerHTML = '&#x1F7E2; Hermes running on port ' + data.port + ' &mdash; models: ' + models;
                  el.style.color = '#4ade80';
                }} else {{
                  el.innerHTML = '&#x1F534; Hermes not running on port ' + data.port + ' &mdash; ' + (data.error || '');
                  el.style.color = '#f87171';
                }}
              }}
            }} catch(e) {{
              if (el) {{ el.innerHTML = 'Error checking status: ' + e; el.style.color = '#f87171'; }}
            }}
          }};

          // Initialize on page load
          document.addEventListener('DOMContentLoaded', function() {{
            var activeProvider = '{_active_provider}';
            // Run initial status check if relevant provider is active
            if (activeProvider === 'openclaw') checkOpenClawStatus();
            if (activeProvider === 'hermes') checkHermesStatus();
            // Initialize model select sync
            syncOcModelInput('oc_prov_model_select', 'oc_prov_model_custom');
            syncLlModelInput();
            syncHmModelInput();
          }});
        }})();
      </script>
    </div>

    {model_library_card_html}

      <div class='card' style='margin-top: 12px; padding: 12px;'>
        <div class='row' style='display:flex; align-items:center; justify-content:space-between;'>
          <div>
            <h3 style='margin:0;'>Internet Searches</h3>
            <p class='muted' style='margin:4px 0;'>Allow the internet agent to run web searches.</p>
          </div>
          <div>
            <button id='internet-toggle-btn' class='btn btn-sm btn-secondary'>Loading…</button>
          </div>
        </div>
        <p id='internet-state-text' class='muted' style='margin-top:8px;'>State: …</p>
      </div>


      <script>
        async function refreshInternetState() {{
          try {{
            const resp = await fetch('/api/ai/internet/search_enabled');
            const data = await resp.json();
            const enabled = !!data.enabled;
            const installed = data.installed !== false;
            const btn = document.getElementById('internet-toggle-btn');
            const txt = document.getElementById('internet-state-text');
            if (!btn || !txt) return;
            let help = document.getElementById('internet-state-help');
            if (!help) {{
              help = document.createElement('p');
              help.id = 'internet-state-help';
              help.className = 'muted';
              help.style.marginTop = '6px';
              txt.insertAdjacentElement('afterend', help);
            }}
            btn.dataset.enabled = String(enabled);
            btn.dataset.installed = String(installed);
            if (!installed) {{
              btn.disabled = true;
              btn.textContent = 'Install internet_agent first';
              btn.className = 'btn btn-sm btn-secondary';
              txt.textContent = 'State: Unavailable';
              help.textContent = 'Install internet_agent and restart the AI agent runtime to use live web-search controls.';
              return;
            }}
            btn.disabled = false;
            btn.textContent = enabled ? 'Disable Internet Searches' : 'Enable Internet Searches';
            btn.className = enabled ? 'btn btn-sm btn-danger' : 'btn btn-sm btn-success';
            txt.textContent = 'State: ' + (enabled ? 'Enabled' : 'Disabled');
            help.textContent = 'This same live control is mirrored on the Internet agent card in Agent Studio.';
          }} catch (e) {{
            console.error('Failed to refresh internet state', e);
          }}
        }}
        async function toggleInternetState() {{
          try {{
            const btn = document.getElementById('internet-toggle-btn');
            if (!btn || btn.dataset.installed === 'false') return;
            const current = (btn && btn.dataset.enabled === 'true');
            const desired = !current;
            await fetch('/api/ai/internet/search_enabled', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ enabled: desired }})
            }});
            await refreshInternetState();
          }} catch (e) {{
            console.error('Failed to toggle internet state', e);
          }}
        }}
        (function(){{
            const btn = document.getElementById('internet-toggle-btn');
            if (btn) btn.addEventListener('click', toggleInternetState);
            refreshInternetState();
        }})();
      </script>

      <!-- ===== Model Behavior Panel ===== -->
      <div class='card' style='margin-top: 16px; padding: 16px;' id='model-behavior-section'>
        <div style='display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;'>
          <div>
            <h3 style='margin:0;'>🎛️ Model Behavior</h3>
            <p class='muted' style='margin:4px 0;'>Control LiteLlm parameters that affect response style, creativity, and tool-calling reliability. Changes take effect immediately via a hot-reload.</p>
          </div>
        </div>

        <!-- Mode cards -->
        <div id='mb-mode-grid' style='display:grid; grid-template-columns: repeat(auto-fill, minmax(160px,1fr)); gap:10px; margin-bottom:16px;'></div>

        <!-- Advanced overrides collapsible -->
        <details style='margin-bottom:14px;'>
          <summary style='cursor:pointer; font-weight:600; padding:6px 0; user-select:none;'>⚙ Advanced Overrides <span class='muted' style='font-weight:400; font-size:0.85em;'>(override the selected mode)</span></summary>
          <div style='display:grid; grid-template-columns: repeat(auto-fill, minmax(180px,1fr)); gap:10px; margin-top:10px;'>
            <div>
              <label style='font-size:0.85em; color:var(--text-muted);'>temperature</label>
              <input id='mb-temperature' type='number' step='0.05' min='0' max='2' placeholder='e.g. 0.7'
                     style='width:100%; padding:5px 8px; border:1px solid var(--border); border-radius:6px; background:var(--card-bg); color:var(--text);'>
              <small class='muted'>0 = deterministic, 1.2 = creative</small>
            </div>
            <div>
              <label style='font-size:0.85em; color:var(--text-muted);'>top_p</label>
              <input id='mb-top_p' type='number' step='0.05' min='0' max='1' placeholder='e.g. 0.9'
                     style='width:100%; padding:5px 8px; border:1px solid var(--border); border-radius:6px; background:var(--card-bg); color:var(--text);'>
              <small class='muted'>nucleus sampling probability</small>
            </div>
            <div>
              <label style='font-size:0.85em; color:var(--text-muted);'>top_k</label>
              <input id='mb-top_k' type='number' step='1' min='0' max='200' placeholder='e.g. 40'
                     style='width:100%; padding:5px 8px; border:1px solid var(--border); border-radius:6px; background:var(--card-bg); color:var(--text);'>
              <small class='muted'>top-K token candidates</small>
            </div>
            <div>
              <label style='font-size:0.85em; color:var(--text-muted);'>repeat_penalty</label>
              <input id='mb-repeat_penalty' type='number' step='0.05' min='0.5' max='2' placeholder='e.g. 1.1'
                     style='width:100%; padding:5px 8px; border:1px solid var(--border); border-radius:6px; background:var(--card-bg); color:var(--text);'>
              <small class='muted'>&gt;1 reduces repetition</small>
            </div>
            <div>
              <label style='font-size:0.85em; color:var(--text-muted);'>num_ctx (context tokens)</label>
              <input id='mb-num_ctx' type='number' step='512' min='512' max='131072' placeholder='e.g. 8192'
                     style='width:100%; padding:5px 8px; border:1px solid var(--border); border-radius:6px; background:var(--card-bg); color:var(--text);'>
              <small class='muted'>Higher = more context, more RAM</small>
            </div>
          </div>
          <button onclick='clearAdvancedOverrides()' class='btn btn-sm' style='margin-top:10px; background:transparent; border:1px solid var(--border); color:var(--text-muted); padding:4px 10px; border-radius:6px; cursor:pointer;'>Clear All Overrides</button>
        </details>

        <div style='display:flex; align-items:center; gap:10px;'>
          <button id='mb-apply-btn' onclick='applyModelBehavior()' class='btn btn-sm btn-primary' style='padding:7px 18px; border-radius:8px; font-weight:600;'>Apply &amp; Reload Agent</button>
          <span id='mb-status' style='font-size:0.85em; color:var(--text-muted);'></span>
        </div>
      </div>

      <script>
        (function() {{
          let _mbCurrentMode = 'none';
          const _mbModes = {{}};

          const modeColors = {{
            none:     {{ bg:'rgba(255,255,255,0.04)', border:'var(--border)',      label:'var(--text)' }},
            accurate: {{ bg:'rgba(52,211,153,0.12)',  border:'#34d399',            label:'#34d399'     }},
            human:    {{ bg:'rgba(96,165,250,0.12)',   border:'#60a5fa',            label:'#60a5fa'     }},
            creative: {{ bg:'rgba(251,146,60,0.12)',   border:'#fb923c',            label:'#fb923c'     }},
          }};
          const modeEmoji = {{ none:'○', accurate:'🎯', human:'💬', creative:'✨' }};

          async function loadModelBehavior() {{
            try {{
              const r = await fetch('/api/model-behavior');
              const d = await r.json();
              _mbCurrentMode = d.mode || 'none';
              Object.assign(_mbModes, d.modes || {{}});
              renderModeGrid(d);
              // Fill advanced overrides
              const adv = d.advanced_overrides || {{}};
              ['temperature','top_p','top_k','repeat_penalty','num_ctx'].forEach(k => {{
                const el = document.getElementById('mb-' + k);
                if (el) el.value = (adv[k] != null) ? adv[k] : '';
              }});
            }} catch(e) {{
              document.getElementById('mb-status').textContent = 'Error loading config: ' + e;
            }}
          }}

          function renderModeGrid(d) {{
            const grid = document.getElementById('mb-mode-grid');
            if (!grid) return;
            grid.innerHTML = Object.entries(d.modes).map(([key, m]) => {{
              const col = modeColors[key] || modeColors.none;
              const active = key === _mbCurrentMode;
              const params = m.params && Object.keys(m.params).length
                ? Object.entries(m.params).map(([k,v]) => `<span style="color:var(--text-muted);font-size:0.75em;">${{k}}=${{v}}</span>`).join('<br>')
                : '<span style="color:var(--text-muted);font-size:0.75em;">no overrides</span>';
              return `<div onclick="selectMode('${{key}}')" data-mode="${{key}}" style="cursor:pointer;padding:10px 12px;border-radius:10px;border:2px solid ${{active ? col.border : 'var(--border)'}};background:${{active ? col.bg : 'transparent'}};transition:all 0.15s;">
                <div style="font-size:1em;font-weight:700;color:${{col.label}};margin-bottom:3px;">${{modeEmoji[key]||''}} ${{m.label}}</div>
                <div style="font-size:0.78em;color:var(--text-muted);margin-bottom:6px;line-height:1.3;">${{m.description}}</div>
                ${{params}}
              </div>`;
            }}).join('');
          }}

          window.selectMode = function(key) {{
            _mbCurrentMode = key;
            // Re-render highlights without a fetch
            document.querySelectorAll('#mb-mode-grid [data-mode]').forEach(el => {{
              const k = el.dataset.mode;
              const col = modeColors[k] || modeColors.none;
              const active = k === _mbCurrentMode;
              el.style.borderColor = active ? col.border : 'var(--border)';
              el.style.background = active ? col.bg : 'transparent';
            }});
          }};

          window.clearAdvancedOverrides = function() {{
            ['temperature','top_p','top_k','repeat_penalty','num_ctx'].forEach(k => {{
              const el = document.getElementById('mb-' + k);
              if (el) el.value = '';
            }});
          }};

          window.applyModelBehavior = async function() {{
            const btn = document.getElementById('mb-apply-btn');
            const status = document.getElementById('mb-status');
            btn.disabled = true; btn.textContent = 'Applying…';
            status.textContent = '';
            const payload = {{ mode: _mbCurrentMode }};
            ['temperature','top_p','top_k','repeat_penalty','num_ctx'].forEach(k => {{
              const el = document.getElementById('mb-' + k);
              const v = el && el.value.trim();
              payload[k] = v !== '' ? Number(v) : null;
            }});
            try {{
              const r = await fetch('/api/model-behavior', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify(payload)
              }});
              const d = await r.json();
              if (d.success) {{
                status.style.color = 'var(--success)';
                status.textContent = '✓ Applied. Agent reloaded.';
              }} else {{
                status.style.color = 'var(--danger)';
                status.textContent = 'Error: ' + (d.error || JSON.stringify(d));
              }}
            }} catch(e) {{
              status.style.color = 'var(--danger)';
              status.textContent = 'Network error: ' + e;
            }}
            btn.disabled = false; btn.textContent = 'Apply & Reload Agent';
          }};

          loadModelBehavior();
        }})();
      </script>

      {agent_studio_panel_html}

      <form method='post' action='/save-config'>

        <input type='hidden' name='kind' value='ai_agent'>
        
        <div style='margin-bottom: 16px;'>
          <label class='checkbox-container'>
            <input type='checkbox' name='ai_agent_enabled' value='1' {'checked' if ai_agent_enabled else ''}>
            <span class='checkmark'></span>
            <span class='checkbox-label'>Enable AI Agent Server</span>
          </label>
          <small class='muted'>Enable or disable the AI Agent Server that handles /api/chat and other AI endpoints</small>
        </div>
        
        <div style='margin-bottom: 16px;'>
          <label class='checkbox-container'>
            <input type='checkbox' name='ai_agent_auto_start' value='1' {'checked' if ai_agent_auto_start else ''}>
            <span class='checkmark'></span>
            <span class='checkbox-label'>Auto-start on server initialization</span>
          </label>
          <small class='muted'>Automatically start the AI Agent Server when the main server starts</small>
        </div>
        
        <div style='margin-bottom: 16px;'>
          <label class='checkbox-container'>
            <input type='checkbox' name='ai_agent_record_messages' value='1' {'checked' if ai_agent_record_messages else ''}>
            <span class='checkmark'></span>
            <span class='checkbox-label'>Record messages in Database</span>
          </label>
          <small class='muted'>Save message data and session events to database for memory integration</small>
        </div>

        <div style='margin-bottom: 16px;'>
          <label for='ai_agent_memory_backend' style='display: block; margin-bottom: 4px; font-weight: bold;'>Memory backend:</label>
          <select name='ai_agent_memory_backend' id='ai_agent_memory_backend'>
            <option value='legacy' {'selected' if ai_agent_memory_backend != 'cognee' else ''}>AutoYou SQLite</option>
            <option value='cognee' {'selected' if ai_agent_memory_backend == 'cognee' else ''}>Cognee self-hosted</option>
          </select>
          <small class='muted'>Use AutoYou SQLite for low-resource machines, or Cognee for graph/vector memory.</small>
        </div>
        
        <div style='margin-bottom: 16px;'>
          <label for='ai_agent_port' style='display: block; margin-bottom: 4px; font-weight: bold;'>AI Agent Server Port:</label>
          <input type='number' name='ai_agent_port' id='ai_agent_port' value='{ai_agent_port}' min='1024' max='65535' style='width: 100px;'>
          <small class='muted'>Port for the AI Agent Server (requires restart to take effect)</small>
        </div>
        
        <div style='margin-bottom: 16px;'>
          <button type='submit' style='margin-right: 8px;'>Save AI Agent Settings</button>
        </div>
      </form>
      
      <div id='agent-instructions-card' class='agent-editor-card'>
        <div class='agent-editor-header'>
          <div class='agent-editor-title'>
            <h3>Agent Instructions</h3>
            <p>Edit the live root prompt in a larger, easier-to-scan workspace. Use the section builder to stay aligned with the composable `prompt.py` layout, or drop into the raw prompt below for full-control edits.</p>
          </div>
          <div class='agent-editor-meta'>
            <span id='agent-instructions-lines'>Lines: --</span>
            <span id='agent-instructions-chars'>Characters: --</span>
          </div>
        </div>
        <div class='agent-editor-shell'>
          <div class='agent-structure-bar'>
            <div class='agent-structure-copy'>
              <strong id='agent-instructions-structure-state'>Loading prompt structure...</strong>
              <span id='agent-instructions-structure-hint'>The section builder updates the main prompt directly, so it still works even when no local or cloud AI provider is configured.</span>
            </div>
          </div>
          <div id='agent-prompt-section-grid' class='agent-section-grid'>
            <div class='agent-section-empty'>Loading prompt sections...</div>
          </div>
          <div class='agent-section-actions'>
            <div class='agent-editor-actions'>
              <button type='button' id='save-prompt-sections-btn' onclick='savePromptSections()' class='admin-feature-btn alt'>Save Builder Sections</button>
            </div>
          </div>
          <div class='agent-editor-toolbar'>
            <div class='agent-editor-caption'>The raw editor below shows the exact live `AGENT_INSTRUCTION` literal. Use it for full custom prompts; use “Revert to Fallback” to restore the composed section baseline safely.</div>
          </div>
          <textarea id='agent-instructions' class='agent-editor-textarea' placeholder='Loading agent instructions...'></textarea>
          <div class='agent-editor-toolbar agent-editor-toolbar-bottom'>
            <div class='agent-editor-actions' id='agent-instructions-raw-actions'>
              <button type='button' onclick='updateAgentInstructions()' class='admin-feature-btn primary'>Save Raw Prompt</button>
              <button type='button' onclick='loadAgentInstructions()' class='admin-feature-btn alt'>Reload</button>
              <button type='button' onclick='revertAgentInstructions()' class='admin-feature-btn ghost'>Revert to Fallback</button>
            </div>
          </div>
        </div>
      </div>

      <div class='card' id='agent-control-card' style='margin-top: 16px; padding: 16px; background: #0f172a; border: 1px solid #1f2937; border-radius: 8px;'>
        <h3 style='margin-top: 0; color: #f8fafc;'>AI Agent Control</h3>
        <div style='margin-top: 12px;'>
          <button type='button' onclick='startAiAgentServer()' class='secondary-btn' style='margin-right: 8px;'>Start Agent</button>
          <button type='button' onclick='stopAiAgentServer()' class='secondary-btn' style='margin-right: 8px;'>Stop Agent</button>
          <button type='button' onclick='restartAiAgentServer()' class='secondary-btn'>Restart Agent</button>
        </div>
        <div id='ai-agent-status' style='margin-top: 12px; padding: 8px; background: #1f2937; border-radius: 4px; font-family: monospace; font-size: 12px;'>
          Status: {ai_agent_status}
        </div>
      </div>
    </div>

    <div class='card' id='autoyou-page-settings-card'>
      <h2>Websites &amp; Browser</h2>
      <p class='muted'>Manage websites and browser access running on port {autoyou_page_port}</p>
      <div style='margin-bottom: 12px; color: #94a3b8;'>
        Host local websites and browser routes for AutoYou clients while this server is running.
      </div>

      <form method='post' action='/save-config' id='autoyou-page-settings-form' style='margin-bottom: 16px;'>
        <input type='hidden' name='kind' value='autoyou_page'>
                <input type='hidden' name='autoyou_advertised_websites' id='autoyou_advertised_websites' value='{autoyou_advertised_websites_json}'>
        <input type='hidden' name='autoyou_bookmarks' id='autoyou_bookmarks' value='{autoyou_bookmarks_json}'>
        <input type='hidden' id='autoyou_reserved_browser_ports' value='{autoyou_reserved_browser_ports_json}'>
        <div style='margin-bottom: 16px;'>
          <label for='autoyou_page_port' style='display: block; margin-bottom: 4px; font-weight: bold;'>Websites &amp; Browser Port:</label>
          <input type='number' name='autoyou_page_port' id='autoyou_page_port' value='{autoyou_page_port}' min='1024' max='65535' style='width: 100px;'>
          <small class='muted'>Default 8067; used by AutoYou Call Browser to view web pages served on this port when enabled</small>
        </div>
        <div style='margin-bottom: 16px;'>
          <label class='checkbox-container'>
            <input type='checkbox' name='autoyou_page_auto_start' value='1' {'checked' if autoyou_page_auto_start else ''}>
            <span class='checkmark'></span>
            <span class='checkbox-label'>Auto enable Websites &amp; Browser on startup</span>
          </label>
          <small class='muted'>Automatically start this local website when the main server starts</small>
        </div>
        <div style='margin-bottom: 16px;'>
          <label for='autoyou_page_timeline_days' style='display: block; margin-bottom: 4px; font-weight: bold;'>Feed Timeline Window (days):</label>
          <input type='number' name='autoyou_page_timeline_days' id='autoyou_page_timeline_days' value='{autoyou_page_timeline_days}' min='0' max='3650' style='width: 100px;'>
          <small class='muted'>Days shown in the feed by default (0-3650). Set to 0 to always show the entire timeline.</small>
        </div>
        <div style='margin-bottom: 16px;'>
          <label for='autoyou_page_theme' style='display: block; margin-bottom: 4px; font-weight: bold;'>Shared UI Theme:</label>
          <select name='autoyou_page_theme' id='autoyou_page_theme' style='width: 220px;'>
            <option value='dark' {'selected' if autoyou_page_theme == 'dark' else ''}>Dark (default)</option>
            <option value='light' {'selected' if autoyou_page_theme == 'light' else ''}>Light</option>
          </select>
          <small class='muted'>Shared across AutoYou Page, Agent Websites, Notes Library, and this Admin UI.</small>
        </div>
        <div style='margin-bottom: 16px; padding: 12px; background: #0f172a; border: 1px solid #1f2937; border-radius: 8px;'>
          <h3 style='margin-top: 0; color: #f8fafc;'>Custom Web Forwarding</h3>
          <p class='muted' style='margin-bottom: 8px;'>Use your own localhost web server for forwarding web traffic. When enabled, this takes priority over the Websites &amp; Browser port for remote web requests handled via AutoYou calls.</p>
          <div style='margin-bottom: 10px;'>
            <label class='checkbox-container'>
              <input type='checkbox' name='autoyou_custom_forward_enabled' value='1' {'checked' if autoyou_forward_enabled else ''}>
              <span class='checkmark'></span>
              <span class='checkbox-label'>Enable Custom Forwarding</span>
            </label>
          </div>
          <div>
            <label for='autoyou_custom_forward_port' style='display: block; margin-bottom: 4px;'>Custom Forward Port:</label>
            <input type='number' id='autoyou_custom_forward_port' name='autoyou_custom_forward_port' value='{autoyou_forward_port}' min='1024' max='65535' style='width: 100px;'>
            <small class='muted'>Default 8067; used by AutoYou Call Browser to view web pages served on this port when enabled</small>
          </div>
        </div>
        <div style='margin-bottom: 16px; padding: 12px; background: #0f172a; border: 1px solid #1f2937; border-radius: 8px;'>
          <h3 style='margin-top: 0; color: #f8fafc;'>Bookmarks</h3>
          <p class='muted' style='margin-bottom: 8px;'>
            Add browser bookmarks that appear in the Website Shortcuts list on desktop, iOS, and Android clients.
          </p>
          <div id='autoyou-bookmarks-empty' class='muted' style='display: none; margin-bottom: 12px;'>
            No bookmarks added yet.
          </div>
          <div id='autoyou-bookmarks-list' style='display: grid; gap: 8px; margin-bottom: 16px;'></div>
          <div style='border: 1px solid #1f2937; border-radius: 8px; padding: 12px; background: #111827;'>
            <h4 style='margin-top: 0; color: #f8fafc;'>Add a bookmark</h4>
            <div style='display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); margin-bottom: 12px;'>
              <div>
                <label for='autoyou_bookmark_title' style='display: block; margin-bottom: 4px; font-weight: bold;'>Title</label>
                <input type='text' id='autoyou_bookmark_title' placeholder='e.g. Project Docs' style='width: 100%;'>
              </div>
              <div>
                <label for='autoyou_bookmark_url' style='display: block; margin-bottom: 4px; font-weight: bold;'>URL</label>
                <input type='text' id='autoyou_bookmark_url' placeholder='https://autoyou.me' style='width: 100%;'>
              </div>
            </div>
            <div style='margin-bottom: 12px;'>
              <label for='autoyou_bookmark_description' style='display: block; margin-bottom: 4px; font-weight: bold;'>Description (optional)</label>
              <input type='text' id='autoyou_bookmark_description' placeholder='Short description shown to clients' style='width: 100%;'>
            </div>
            <button type='button' id='autoyou-add-bookmark-btn' class='secondary-btn'>+ Add Bookmark</button>
          </div>
        </div>
        <div style='margin-bottom: 16px; padding: 12px; background: #0f172a; border: 1px solid #1f2937; border-radius: 8px;'>
                    <h3 style='margin-top: 0; color: #f8fafc;'>Advertised Websites</h3>
          <p class='muted' style='margin-bottom: 8px;'>
                        Add extra local websites or HTTP services that clients should discover as 1:1 same-port web services.
                        The primary AutoYou browser port (<code>{autoyou_primary_browser_port}</code>) and agent website routes are published automatically.
          </p>
                    <div id='autoyou-advertised-websites-empty' class='muted' style='display: none; margin-bottom: 12px;'>
                        No extra websites added yet.
          </div>
                    <div id='autoyou-advertised-websites-list' style='display: grid; gap: 8px; margin-bottom: 16px;'></div>
          <div style='border: 1px solid #1f2937; border-radius: 8px; padding: 12px; background: #111827;'>
                        <h4 style='margin-top: 0; color: #f8fafc;'>Add a website</h4>
            <div style='display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); margin-bottom: 12px;'>
              <div>
                                <label for='autoyou_advertised_website_number' style='display: block; margin-bottom: 4px; font-weight: bold;'>Website port</label>
                                <input type='number' id='autoyou_advertised_website_number' min='1' max='65535' placeholder='e.g. 3000' style='width: 100%;'>
              </div>
              <div>
                                <label for='autoyou_advertised_website_label' style='display: block; margin-bottom: 4px; font-weight: bold;'>Label</label>
                                <input type='text' id='autoyou_advertised_website_label' placeholder='e.g. Local Dashboard' style='width: 100%;'>
              </div>
            </div>
            <div style='margin-bottom: 12px;'>
                            <label for='autoyou_advertised_website_description' style='display: block; margin-bottom: 4px; font-weight: bold;'>Description (optional)</label>
                            <input type='text' id='autoyou_advertised_website_description' placeholder='Short description shown to clients' style='width: 100%;'>
            </div>
            <div style='margin-bottom: 12px;'>
                            <label for='autoyou_advertised_website_target_url' style='display: block; margin-bottom: 4px; font-weight: bold;'>Forward URL (optional)</label>
                            <input type='text' id='autoyou_advertised_website_target_url' placeholder='http://127.0.0.1:3000 or http://server.local:3000' style='width: 100%;'>
              <small class='muted'>Leave blank to forward to the same port on 127.0.0.1. You can also enter <code>localhost:3000</code> or any reachable host/IP.</small>
            </div>
                        <button type='button' id='autoyou-add-advertised-website-btn' class='secondary-btn'>+ Add Website</button>
          </div>
        </div>
        <div style='margin-bottom: 16px;'>
          <button type='submit' style='margin-right: 8px;'>Save Websites &amp; Browser Settings</button>
        </div>
      </form>
      
      <div style='margin-top: 16px; padding: 16px; background: #0f172a; border: 1px solid #1f2937; border-radius: 8px;'>
        <h3 style='margin-top: 0; color: #f8fafc;'>Websites &amp; Browser Control</h3>
        <div style='margin-top: 12px;'>
          <button type='button' onclick='startAutoYouPageService()' class='secondary-btn' style='margin-right: 8px;'>Start Service</button>
          <button type='button' onclick='stopAutoYouPageService()' class='secondary-btn' style='margin-right: 8px;'>Stop Service</button>
          <button type='button' onclick='restartAutoYouPageService()' class='secondary-btn'>Restart Service</button>
        </div>
        <div id='autoyou-page-status' style='margin-top: 12px; padding: 8px; background: #1f2937; border-radius: 4px; font-family: monospace; font-size: 12px;'>
          Status: Loading...
        </div>
      </div>
    </div>

    <div class='card' id='admin-frontend-proxy-card'>
      <h2>Admin Website Remote Access</h2>
      <p class='muted'>Allow connected browser clients (iOS / Android / Python) to access this admin website remotely. Current route: <code>{admin_frontend_route_label}</code>.</p>
      <form method='post' action='/save-config'>
        <input type='hidden' name='kind' value='admin_frontend'>
        <div style='margin-bottom: 16px;'>
          <label class='checkbox-container'>
            <input type='checkbox' name='admin_frontend_proxy_enabled' value='1' {'checked' if admin_frontend_proxy_enabled else ''}>
            <span class='checkmark'></span>
            <span class='checkbox-label'>Enable Admin website via AutoYou App Browser</span>
          </label>
          <small class='muted' style='display: block; margin-top: 4px;'>When enabled, paired AutoYou App browser devices can reach the full admin interface through a direct same-port route. Disable to restrict admin access to localhost only. Disabled by default.</small>
        </div>
        <div style='margin-bottom: 16px;'>
          <button type='submit'>Save Admin Access Settings</button>
        </div>
      </form>
    </div>

    <div class='card' id='tunnelmole-settings-card'>
      <h2>Public Reverse Proxy Connection Settings</h2>
      <p class='muted'>
        Status: <span id='tunnelmole-status'>{tunnelmole_status}</span>
        &mdash; <span id='tunnelmole-url'>{tunnelmole_url}</span>
      </p>
      <p class='muted' style='margin-top:4px; font-size:0.85em;'>
        Auth / signaling port: <code>{tunnelmole_auth_port}</code>
      </p>

      <form method='post' action='/save-config'>
        <input type='hidden' name='kind' value='tunnelmole'>

        <div class='row'>
          <div class='col'>
            <label class='checkbox-container'>
              <input type='checkbox' name='tunnelmole_enabled' value='true' {'checked' if tunnelmole_enabled else ''}>
              <span class='checkmark'></span>
              <span class='checkbox-label'>Enable Public Reverse Proxy</span>
            </label>
            <small class='muted' style='display: block; margin-top: 4px;'>
              Allows remote clients to connect via the public proxy for pairing
            </small>
          </div>
        </div>

        <div style='display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; margin-top: 16px;'>
          <div>
            <label for='tunnelmole_timeout'>Proxy Timeout (minutes):</label>
                        <input type='number' id='tunnelmole_timeout' name='tunnelmole_timeout' value='{tunnelmole_timeout}' min='{_TUNNELMOLE_TIMEOUT_MINUTES_MIN}' max='{_TUNNELMOLE_TIMEOUT_MINUTES_MAX}'>
            <small class='muted' style='display: block; margin-top: 4px;'>
                            How long AutoYou keeps the public pairing and signaling URL open. On AutoYou's shared public proxy, free sessions may stop after about {_TUNNELMOLE_SHARED_PROXY_FREE_LIMIT_MINUTES} minutes even if you request longer; longer windows are intended for premium or self-hosted deployments.
            </small>
          </div>
          <div>
            <label for='tunnelmole_otp_timeout'>OTP Valid Duration (minutes):</label>
                        <input type='number' id='tunnelmole_otp_timeout' name='tunnelmole_otp_timeout' value='{tunnelmole_otp_timeout}' min='{_TUNNELMOLE_TIMEOUT_MINUTES_MIN}' max='{_TUNNELMOLE_TIMEOUT_MINUTES_MAX}'>
            <small class='muted' style='display: block; margin-top: 4px;'>
                            How long the current pairing code stays valid. In single-use mode it works once; in multi-use mode it remains reusable until this timer expires.
            </small>
          </div>
        </div>

        <div style='border:1px solid rgba(128,128,128,0.25); border-radius:6px; padding:14px; margin-bottom:16px;'>
          <label class='checkbox-container' style='align-items:flex-start; gap:10px;'>
            <input type='checkbox' name='tunnelmole_otp_multiuse' value='true' {'checked' if tunnelmole_otp_multiuse else ''}>
            <span class='checkmark' style='margin-top:2px; flex-shrink:0;'></span>
            <span>
              <span class='checkbox-label'>Multi-use OTP <small class='muted'>(default: off - single-use)</small></span>
              <small class='muted' style='display:block; margin-top:6px; font-size:0.83em; line-height:1.6;'>
                Leave this off for normal one-person pairing. The code works once, then the timed connection policy can close the public proxy as soon as the first client finishes connecting.<br><br>
                Turn it on only when you want the same pairing code to stay reusable for the full timeout window. This is separate from the public link lifetime mode below.
              </small>
            </span>
          </label>
        </div>

                <div style='display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px;'>
                    <div style='border:1px solid rgba(56,189,248,0.22); border-radius:6px; padding:14px; background:rgba(14,165,233,0.04);'>
                        <label for='tunnelmole_pair_code_mode'>Pair Code Mode</label>
                        <select id='tunnelmole_pair_code_mode' name='tunnelmole_pair_code_mode'>
                            <option value='random_otp' {'selected' if tunnelmole_pair_code_mode == 'random_otp' else ''}>Random pairing code</option>
                            <option value='authenticator' {'selected' if tunnelmole_pair_code_mode == 'authenticator' else ''}>Authenticator code</option>
                        </select>
                        <small class='muted' style='display:block; margin-top:6px; font-size:0.83em; line-height:1.6;'>
                            <strong>Random OTP</strong> keeps the existing <code>/pair</code> flow.<br><br>
                            <strong>Authenticator</strong> lets a device connect right away using your authenticator app's current code instead of waiting for a fresh one-time code. This only applies in Secure Professional mode.
                        </small>
                    </div>
                    <div style='border:1px solid rgba(34,197,94,0.20); border-radius:6px; padding:14px; background:rgba(34,197,94,0.04);'>
                        <label for='tunnelmole_connection_mode'>Connection Lifetime</label>
                        <select id='tunnelmole_connection_mode' name='tunnelmole_connection_mode'>
                            <option value='timed' {'selected' if tunnelmole_connection_mode == 'timed' else ''}>Timed</option>
                            <option value='unmanaged' {'selected' if tunnelmole_connection_mode == 'unmanaged' else ''}>Unmanaged</option>
                        </select>
                        <small class='muted' style='display:block; margin-top:6px; font-size:0.83em; line-height:1.6;'>
                            <strong>Timed</strong> keeps the current timer-driven shutdown rules and still respects single-use versus reusable pairing codes.<br><br>
                            <strong>Unmanaged</strong> keeps the public URL up until you stop it manually or the tunnel connection is cut.
                        </small>
                    </div>
        </div>

        <div style='margin-top: 16px;'>
          <button type='submit' style='margin-right: 8px;'>Save Proxy Settings</button>
          <button type='button' onclick='startPublicProxy()' class='secondary-btn'>Start Service</button>
          <button type='button' onclick='refreshPublicProxyStatus()' class='secondary-btn'>Refresh Status</button>
          <button type='button' onclick='stopPublicProxy()' class='secondary-btn' style='margin-left: 8px;'>Stop Service</button>
        </div>
      </form>
    </div>

    <div class='card speech-settings-shell' id='speech-settings-card'>
      <div class='speech-settings-header'>
        <div class='speech-section-title'>
          <h2>Speech Settings - Voice Calls</h2>
          <p class='speech-settings-subtitle'>Configure live speech-to-text and text-to-speech behavior for voice calls. Saving applies to new replies immediately and reloads active call speech handlers where possible.</p>
        </div>
        <div class='speech-guide-actions'>
          <a class='admin-feature-btn primary' href='/guides/speech' target='_blank'>Voice Install Guide</a>
          <a class='admin-feature-btn ghost' href='/guides/speech#stt-models' target='_blank'>STT Model Guide</a>
        </div>
      </div>

      <div class='speech-summary-card'>
        <strong class='speech-summary-lead'>Current Speech Configuration</strong>
        <div class='muted'>{html.escape(speech_summary)}</div>
        <div class='speech-inline-metrics' style='margin-top: 12px;'>
          <span>Detected system voices: {len(system_tts_voices)}</span>
          <span>Current STT model: {html.escape(str(speech_stt.get("model") or ""), quote=False)}</span>
        </div>
      </div>

      <form method='post' action='/save-config' class='speech-form-grid'>
        <input type='hidden' name='kind' value='speech'>

        <div class='speech-surface-card'>
          <div class='speech-section-header'>
            <div class='speech-section-title'>
              <h3>Text to Speech</h3>
              <p>Choose how the server synthesizes spoken replies back to iOS, Android, Windows, or macOS clients.</p>
            </div>
            <div class='speech-section-actions'>
              <a class='admin-feature-btn alt' href='/guides/speech#system-voices' target='_blank'>Install / manage voices</a>
            </div>
          </div>

          <div class='speech-inline-note' style='margin-top: 16px;'>
            <span class='speech-dot'></span>
            <div>Use <strong>Custom cloned voice</strong> for the local voice prepared by the Voice Training agent, or <strong>System voice</strong> for voices already installed on this <strong>{html.escape(server_name)}</strong> host.</div>
          </div>

          <div class='speech-field-grid' style='margin-top: 16px;'>
            <div>
              <label for='speech_tts_provider'>TTS Provider</label>
              <select id='speech_tts_provider' name='speech_tts_provider'>
                <option value='system' {'selected' if speech_tts_provider == 'system' else ''}>System voice (pyttsx3)</option>
                <option value='custom' {'selected' if speech_tts_provider == 'custom' else ''}>Custom cloned voice</option>
                <option value='openai' {'selected' if speech_tts_provider == 'openai' else ''}>OpenAI TTS</option>
                <option value='azure' {'selected' if speech_tts_provider == 'azure' else ''}>Azure Speech</option>
              </select>
            </div>
            <div>
              <label for='speech_tts_rate'>Speech Rate</label>
              <input type='number' id='speech_tts_rate' name='speech_tts_rate' value='{speech_tts_rate}' min='0.25' max='4.0' step='0.05'>
              <small>1.0 is normal speed. Applies across providers where supported.</small>
            </div>
          </div>

          <div id='speech-system-provider' style='margin-top: 16px;'>
            <label for='speech_tts_system_voice'>System Voice</label>
            <select id='speech_tts_system_voice' name='speech_tts_system_voice'>
              {speech_system_voice_options_html}
            </select>
            <small>Uses voices installed on this {html.escape(server_name)} host. This is the most portable local/offline option across Windows, macOS, Linux, and Raspberry Pi deployments.</small>
          </div>

          <div id='speech-openai-provider' style='margin-top: 16px;'>
            <div class='speech-field-grid'>
              <div>
                <label for='speech_openai_tts_model'>OpenAI TTS Model</label>
                <select id='speech_openai_tts_model' name='speech_openai_tts_model'>
                  {openai_model_options_html}
                </select>
              </div>
              <div>
                <label for='speech_openai_tts_voice'>OpenAI Voice</label>
                <input type='text' id='speech_openai_tts_voice' name='speech_openai_tts_voice' list='speech-openai-voice-options' value='{html.escape(str(speech_openai.get("voice") or ""), quote=True)}' placeholder='alloy'>
                <datalist id='speech-openai-voice-options'>
                  {openai_voice_options_html}
                </datalist>
                <small>Use a built-in voice like alloy, ash, marin, or cedar, or enter a custom OpenAI voice ID.</small>
              </div>
            </div>
            <div style='margin-top: 16px;'>
              <label for='speech_openai_base_url'>OpenAI Base URL</label>
              <input type='text' id='speech_openai_base_url' name='speech_openai_base_url' value='{html.escape(str(speech_openai.get("base_url") or ""), quote=True)}' placeholder='https://api.openai.com/v1'>
              <small>Leave default for OpenAI. Override only for a compatible gateway.</small>
            </div>
            <div style='margin-top: 16px;'>
              <label for='speech_openai_api_key'>OpenAI API Key</label>
              <div class='speech-field-grid' style='grid-template-columns:minmax(0,1fr) auto;align-items:end;'>
                <input type='password' id='speech_openai_api_key' name='speech_openai_api_key' value='{speech_openai_api_key_display}' data-api-key='{html.escape(speech_openai_api_key_raw, quote=True)}' placeholder='Enter OpenAI API key'>
                <button type='button' id='speech_openai_api_key_toggle' class='admin-feature-btn ghost' title='Show/Hide OpenAI API key'>Show</button>
              </div>
            </div>
            <div style='margin-top: 16px;'>
              <label for='speech_openai_tts_instructions'>OpenAI Voice Instructions</label>
              <textarea id='speech_openai_tts_instructions' name='speech_openai_tts_instructions' rows='4' placeholder='Optional style instructions for how the voice should sound'>{html.escape(str(speech_openai.get("instructions") or ""))}</textarea>
              <small>Optional. Use this to shape delivery style when the selected OpenAI model supports instructions.</small>
            </div>
          </div>

          <div id='speech-azure-provider' style='margin-top: 16px;'>
            <div class='speech-field-grid'>
              <div>
                <label for='speech_azure_region'>Azure Speech Region</label>
                <input type='text' id='speech_azure_region' name='speech_azure_region' value='{html.escape(str(speech_azure.get("speech_region") or ""), quote=True)}' placeholder='eastus'>
              </div>
              <div>
                <label for='speech_azure_voice'>Azure Voice Name</label>
                <input type='text' id='speech_azure_voice' name='speech_azure_voice' value='{html.escape(str(speech_azure.get("voice") or ""), quote=True)}' placeholder='en-US-AvaMultilingualNeural'>
              </div>
            </div>
            <div style='margin-top: 16px;'>
              <label for='speech_azure_key'>Azure Speech Key</label>
              <div class='speech-field-grid' style='grid-template-columns:minmax(0,1fr) auto;align-items:end;'>
                <input type='password' id='speech_azure_key' name='speech_azure_key' value='{speech_azure_key_display}' data-api-key='{html.escape(speech_azure_key_raw, quote=True)}' placeholder='Enter Azure Speech key'>
                <button type='button' id='speech_azure_key_toggle' class='admin-feature-btn ghost' title='Show/Hide Azure Speech key'>Show</button>
              </div>
            </div>
            <div style='margin-top: 16px;'>
              <label for='speech_azure_endpoint_id'>Azure Custom Endpoint ID</label>
              <input type='text' id='speech_azure_endpoint_id' name='speech_azure_endpoint_id' value='{html.escape(str(speech_azure.get("endpoint_id") or ""), quote=True)}' placeholder='Optional custom voice deployment endpoint ID'>
              <small>Optional. Use only if you have a custom Azure Speech deployment.</small>
            </div>
          </div>

          <div class='speech-quick-save'>
            <div class='muted'>Save the current TTS provider, voice, and rate here without scrolling past the STT model cache section.</div>
            <button type='submit' class='admin-feature-btn primary'>Save Voice Settings</button>
          </div>
        </div>

        <div class='speech-surface-card'>
          <div class='speech-section-header'>
            <div class='speech-section-title'>
              <h3>Speech to Text</h3>
              <p>The live voice-call transcription path continues to use RealtimeSTT with faster-whisper underneath. These settings control the local model and VAD behavior on the server.</p>
            </div>
            <div class='speech-section-actions'>
              <a class='admin-feature-btn alt' href='/guides/speech#stt-models' target='_blank'>Download model guidance</a>
            </div>
          </div>

          <div class='speech-field-grid' style='margin-top: 16px;'>
            <div>
              <label for='speech_stt_model'>STT Model</label>
              <input type='text' id='speech_stt_model' name='speech_stt_model' list='speech-stt-model-options' value='{html.escape(str(speech_stt.get("model") or ""), quote=True)}' placeholder='tiny.en'>
              <datalist id='speech-stt-model-options'>
                {stt_model_datalist_html}
              </datalist>
              <small>Examples: tiny.en, base.en, small.en, medium.en, large-v3, distil-large-v3.</small>
            </div>
            <div>
              <label for='speech_stt_language'>STT Language</label>
              <input type='text' id='speech_stt_language' name='speech_stt_language' value='{html.escape(str(speech_stt.get("language") or ""), quote=True)}' placeholder='en'>
              <small>Use an ISO language code such as en, en-US, hi, fr, or de. Leave blank to let the model auto-detect.</small>
            </div>
          </div>

          <div class='speech-field-grid' style='margin-top: 16px;'>
            <div>
              <label for='speech_stt_device'>STT Device</label>
              <input type='text' id='speech_stt_device' name='speech_stt_device' list='speech-stt-device-options' value='{html.escape(str(speech_stt.get("device") or ""), quote=True)}' placeholder='cpu'>
              <datalist id='speech-stt-device-options'>
                {stt_device_datalist_html}
              </datalist>
              <small>Use <code>cpu</code> for portable installs, <code>cuda</code> for NVIDIA GPU, or <code>auto</code> to detect at runtime.</small>
            </div>
            <div>
              <label for='speech_stt_compute_type'>STT Compute Type</label>
              <input type='text' id='speech_stt_compute_type' name='speech_stt_compute_type' list='speech-stt-compute-options' value='{html.escape(str(speech_stt.get("compute_type") or ""), quote=True)}' placeholder='float32'>
              <datalist id='speech-stt-compute-options'>
                {stt_compute_type_datalist_html}
              </datalist>
              <small>Examples: float32, float16, int8. Use settings supported by your server hardware.</small>
            </div>
          </div>

          <div class='speech-field-grid' style='margin-top: 16px;'>
            <div>
              <label for='speech_stt_silero_sensitivity'>VAD Sensitivity</label>
              <input type='number' id='speech_stt_silero_sensitivity' name='speech_stt_silero_sensitivity' value='{speech_stt.get("silero_sensitivity", 0.4)}' min='0.0' max='1.0' step='0.05'>
              <small>Higher values detect speech more aggressively.</small>
            </div>
            <div></div>
          </div>

          <div style='margin-top: 16px;'>
            <label for='speech_stt_post_speech_silence_duration'>Post-speech Silence Duration</label>
            <input type='number' id='speech_stt_post_speech_silence_duration' name='speech_stt_post_speech_silence_duration' value='{speech_stt.get("post_speech_silence_duration", 0.6)}' min='0.1' max='5.0' step='0.1'>
            <small>How much silence to wait before the server treats an utterance as complete.</small>
          </div>

          <div class='speech-stt-library' style='margin-top: 22px;'>
            <div class='speech-section-header'>
              <div class='speech-section-title'>
                <h3>Local STT Model Cache</h3>
                <p>Pre-download faster-whisper models into the local Hugging Face cache so the first live call does not need to fetch them in the background.</p>
              </div>
              <div class='speech-inline-metrics'>
                <span id='speech-model-cache-dir'>Cache: Checking...</span>
              </div>
            </div>
            <div id='speech-model-support-note' class='speech-inline-note' style='margin-top: 16px;'>
              <span class='speech-dot'></span>
              <div>Loading local STT model cache status...</div>
            </div>
            <div id='speech-model-catalog' class='speech-model-grid'></div>
            <div id='speech-model-jobs' class='speech-job-list'></div>
            <div id='speech-model-installed' class='speech-installed-list'></div>
          </div>
        </div>

        <div class='speech-form-actions'>
          <button type='submit' class='admin-feature-btn primary'>Save Speech Settings</button>
          <a class='admin-feature-btn ghost' href='/guides/speech' target='_blank'>Open Speech Guide</a>
        </div>
      </form>

      <script>
        function toggleSecretInput(inputId, buttonId) {{
          const input = document.getElementById(inputId);
          const button = document.getElementById(buttonId);
          if (!input || !button) return;
          let shown = false;
          const realValue = input.getAttribute('data-api-key') || '';
          button.addEventListener('click', function() {{
            shown = !shown;
            if (shown) {{
              input.type = 'text';
              if (realValue) input.value = realValue;
              button.textContent = 'Hide';
            }} else {{
              input.type = 'password';
              input.value = (realValue ? '{MASKED_SECRET_PLACEHOLDER}' : '');
              button.textContent = 'Show';
            }}
          }});
        }}

        function updateSpeechProviderUi() {{
          const provider = document.getElementById('speech_tts_provider')?.value || 'system';
          const systemSection = document.getElementById('speech-system-provider');
          const openaiSection = document.getElementById('speech-openai-provider');
          const azureSection = document.getElementById('speech-azure-provider');
          if (systemSection) systemSection.style.display = provider === 'system' ? '' : 'none';
          if (openaiSection) openaiSection.style.display = provider === 'openai' ? '' : 'none';
          if (azureSection) azureSection.style.display = provider === 'azure' ? '' : 'none';
        }}

        window.autoyouSpeechModels = window.autoyouSpeechModels || {{
          statusInterval: null,
          jobsInterval: null,
        }};

        function copySpeechModelToInput(modelName) {{
          const input = document.getElementById('speech_stt_model');
          if (input) {{
            input.value = modelName || '';
            input.focus();
          }}
        }}

        function renderSpeechModelJobs(jobs) {{
          const container = document.getElementById('speech-model-jobs');
          if (!container) return;
          if (!jobs || jobs.length === 0) {{
            container.innerHTML = '';
            return;
          }}
          container.innerHTML = jobs.map(job => {{
            const failed = job.status === 'failed';
            const progressWidth = typeof job.progress_percent === 'number' ? `${{job.progress_percent}}%` : '0%';
            return `
              <div class='speech-job-card'>
                <div class='speech-job-copy'>
                  <strong>${{escapeHtml(job.title || job.model_name || 'STT model')}}</strong>
                  <span>${{escapeHtml(job.message || job.status || 'Queued')}}${{failed && job.error ? ' - ' + escapeHtml(job.error) : ''}}</span>
                </div>
                <div class='speech-inline-metrics'>
                  <span>Status: ${{escapeHtml(job.status || 'queued')}}</span>
                  <span>${{escapeHtml(job.completed_bytes_human || '-') }} / ${{escapeHtml(job.total_bytes_human || '-')}}</span>
                </div>
                <div class='job-progress-track'><div class='job-progress-fill${{failed ? ' failed' : ''}}' style='width:${{progressWidth}}'></div></div>
              </div>
            `;
          }}).join('');
        }}

        function renderSpeechModelStatus(data) {{
          const note = document.getElementById('speech-model-support-note');
          const cacheDirEl = document.getElementById('speech-model-cache-dir');
          const catalog = document.getElementById('speech-model-catalog');
          const installed = document.getElementById('speech-model-installed');
          if (!note || !cacheDirEl || !catalog || !installed) return;

          if (!data || !data.supported) {{
            cacheDirEl.textContent = 'Cache: unavailable';
            note.innerHTML = "<span class='speech-dot'></span><div>Speech model downloads are unavailable because faster-whisper or Hugging Face support is missing in this Python environment.</div>";
            catalog.innerHTML = '';
            installed.innerHTML = '';
            return;
          }}

          cacheDirEl.textContent = `Cache: ${{data.cache_dir || 'Default Hugging Face cache'}}`;
          const selectedModel = data.selected_model || '';
          note.innerHTML = `<span class='speech-dot'></span><div>Selected STT model: <strong>${{escapeHtml(selectedModel || 'Not set')}}</strong>. Downloading here pre-warms the exact local cache faster-whisper already uses during calls.</div>`;

          const models = Array.isArray(data.models) ? data.models : [];
          catalog.innerHTML = models.map(item => `
            <div class='speech-model-card'>
              <div>
                <strong>${{escapeHtml(item.label || item.model || 'STT model')}}</strong>
                <p>${{escapeHtml(item.summary || '')}}</p>
              </div>
              <div class='speech-model-meta'>
                <span class='speech-model-chip'>${{escapeHtml(item.model || '')}}</span>
                ${{item.selected ? "<span class='speech-model-chip active'>Active field</span>" : ''}}
                ${{item.installed ? "<span class='speech-model-chip local'>Cached</span>" : "<span class='speech-model-chip warn'>Not cached</span>"}}
                ${{item.size_on_disk_human && item.size_on_disk_human !== '-' ? `<span class='speech-model-chip'>${{escapeHtml(item.size_on_disk_human)}}</span>` : ''}}
              </div>
              <p>${{escapeHtml(item.profile || '')}}</p>
              <div class='speech-model-actions'>
                <button type='button' class='admin-feature-btn ghost' onclick='copySpeechModelToInput(${{JSON.stringify(item.model || "")}})'>Use in STT Field</button>
                <button type='button' class='admin-feature-btn ${{item.installed ? "alt" : "primary"}}' onclick='downloadSpeechModel(${{JSON.stringify(item.model || "")}})'>${{item.installed ? "Re-download" : "Download Locally"}}</button>
              </div>
            </div>
          `).join('');

          const installedModels = Array.isArray(data.installed_models) ? data.installed_models : [];
          if (!installedModels.length) {{
            installed.innerHTML = `
              <div class='speech-job-card'>
                <div class='speech-job-copy'>
                  <strong>No cached STT models yet</strong>
                  <span>Download one model below to avoid a surprise first-call fetch.</span>
                </div>
              </div>
            `;
            return;
          }}

          installed.innerHTML = `
            <div class='speech-job-card'>
              <div class='speech-job-copy'>
                <strong>Installed locally</strong>
                <span>These faster-whisper models are already cached and ready for local transcription.</span>
              </div>
              <div class='speech-model-grid'>
                ${{installedModels.map(item => `
                  <div class='speech-model-card'>
                    <div>
                      <strong>${{escapeHtml(item.label || item.model || 'STT model')}}</strong>
                      <p class='speech-model-path'>${{escapeHtml(item.repo_path || '')}}</p>
                    </div>
                    <div class='speech-model-meta'>
                      <span class='speech-model-chip local'>Cached</span>
                      <span class='speech-model-chip'>${{escapeHtml(item.size_on_disk_human || '-')}}</span>
                    </div>
                    <div class='speech-model-actions'>
                      <button type='button' class='admin-feature-btn ghost' onclick='copySpeechModelToInput(${{JSON.stringify(item.model || "")}})'>Use in STT Field</button>
                    </div>
                  </div>
                `).join('')}}
              </div>
            </div>
          `;
        }}

        async function refreshSpeechModelStatus() {{
          try {{
            const response = await fetch('/api/speech-models/status');
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Failed to load speech model status');
            renderSpeechModelStatus(data);
          }} catch (e) {{
            const note = document.getElementById('speech-model-support-note');
            if (note) {{
              note.innerHTML = `<span class='speech-dot'></span><div>Unable to load local STT model status: ${{escapeHtml(e.message || String(e))}}</div>`;
            }}
          }}
        }}

        async function downloadSpeechModel(modelName) {{
          try {{
            const response = await fetch('/api/speech-models/download', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ model: modelName }})
            }});
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Failed to start speech model download');
            await pollSpeechModelJobs();
            setTimeout(refreshSpeechModelStatus, 1200);
          }} catch (e) {{
            alert('Unable to start STT model download: ' + (e.message || e));
          }}
        }}

        async function pollSpeechModelJobs() {{
          try {{
            const response = await fetch('/api/speech-models/downloads');
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Failed to load speech model jobs');
            renderSpeechModelJobs(data.jobs || []);
          }} catch (e) {{
            console.error('Failed to load speech model jobs:', e);
          }}
        }}

        document.addEventListener('DOMContentLoaded', function() {{
          const providerSelect = document.getElementById('speech_tts_provider');
          if (providerSelect) providerSelect.addEventListener('change', updateSpeechProviderUi);
          toggleSecretInput('speech_openai_api_key', 'speech_openai_api_key_toggle');
          toggleSecretInput('speech_azure_key', 'speech_azure_key_toggle');
          updateSpeechProviderUi();
          refreshSpeechModelStatus();
          pollSpeechModelJobs();
          window.autoyouSpeechModels.statusInterval = setInterval(refreshSpeechModelStatus, 15000);
          window.autoyouSpeechModels.jobsInterval = setInterval(pollSpeechModelJobs, 4000);
        }});
      </script>
    </div>

    <div class='card rtc-settings-shell' id='rtc-settings-card'>
      <h2>Call Connectivity Settings</h2>
      <p class='muted'>Configure connection helpers for voice-call reachability.</p>
      
      <div class='rtc-import-card'>
        <label for='stun_turn_input'>Paste provider details, connection helper JSON, or connection server URLs here:</label>
        <textarea id='stun_turn_input' rows='6' placeholder='Paste provider helper JSON, an env-style variable assignment, or connection server URLs here.' style='margin-bottom: 8px; font-family: Consolas, monospace;'></textarea>
        <small class='muted'>AutoYou can parse provider helper arrays, JS or JSON payloads, env-style variables, and connection server URLs. This makes copied provider bundles easier to merge.</small>
        <div class='rtc-save-actions'>
          <button type='button' onclick='addToExistingServers()' class='secondary-btn'>Add to Existing</button>
          <button type='button' onclick='replaceServers()' class='secondary-btn'>Replace</button>
          <a class='secondary-btn' href='/guides/connectivity' target='_blank' style='display:inline-flex;align-items:center;text-decoration:none'>Connectivity Guide</a>
        </div>
      </div>
      
      <form method='post' action='/save-config'>
        <input type='hidden' name='kind' value='rtc'>
        <textarea id='rtc_json_textarea' name='rtc_json' rows='8' class='mono'>{rtc_json}</textarea>
        <div class='rtc-save-actions'>
          <button type='submit'>Save Call Connectivity Settings</button>
          <span class='muted'>Need a connection helper provider? <a href='https://www.metered.ca/tools/openrelay/#-credentials' target='_blank'>Open provider page</a>.</span>
        </div>
     </form>
   </div>

   <div class='card' id='datachannel-status-card'>
     <h2>Connected Clients</h2>
     <div style='display:flex;align-items:center;gap:8px;'>
       <span id='dc-indicator' class='checkmark' style='border-color:#334155;background:#0b1220;'></span>
       <span id='dc-status-text' class='muted'>Loading...</span>
     </div>
     <p id='dc-metrics' class='mono small'></p>
   </div>

   <div class='card' id='scheduler-queue-card' data-search-aliases='scheduled notification queue, queued reminders, queued tasks, bot initiated messages, notification delivery backlog'>
     <div class='scheduler-queue-header'>
       <div>
         <h2>Scheduled Notification Queue</h2>
         <p class='muted'>Reminder and task messages wait here when AutoYou needs a connected client or a saved messaging target before it can deliver them directly.</p>
       </div>
       <div class='scheduler-queue-toolbar'>
         <div class='speech-inline-metrics'>
           <span id='scheduler-queue-last-updated'>Updating...</span>
         </div>
         <button type='button' class='admin-feature-btn ghost' data-no-loading='1' onclick='refreshNotificationQueueStatus(true, this)'>Refresh Queue</button>
       </div>
     </div>
     <div class='scheduler-queue-summary-grid'>
       <div class='scheduler-queue-stat'>
         <span class='scheduler-queue-label'>Pending</span>
         <span id='scheduler-queue-pending' class='scheduler-queue-value'>0</span>
         <span id='scheduler-queue-pending-copy' class='scheduler-queue-copy'>Loading queued notifications...</span>
       </div>
       <div class='scheduler-queue-stat'>
         <span class='scheduler-queue-label'>Ready Now</span>
         <span id='scheduler-queue-ready' class='scheduler-queue-value'>0</span>
         <span id='scheduler-queue-ready-copy' class='scheduler-queue-copy'>Waiting for queue data...</span>
       </div>
       <div class='scheduler-queue-stat'>
         <span class='scheduler-queue-label'>Retrying</span>
         <span id='scheduler-queue-retrying' class='scheduler-queue-value'>0</span>
         <span id='scheduler-queue-retrying-copy' class='scheduler-queue-copy'>Waiting for queue data...</span>
       </div>
       <div class='scheduler-queue-stat'>
         <span class='scheduler-queue-label'>Oldest Age</span>
         <span id='scheduler-queue-oldest' class='scheduler-queue-value'>0s</span>
         <span id='scheduler-queue-oldest-copy' class='scheduler-queue-copy'>Waiting for queue data...</span>
       </div>
     </div>
     <div id='scheduler-queue-note' class='scheduler-queue-note'>Loading queued notifications...</div>
     <div id='scheduler-queue-list' class='scheduler-queue-list'></div>
   </div>

   {_cloud_card_html}

   <div class='card' id='telegram-settings-card'>
     <h2>Messaging Partner Settings - Telegram Bot</h2>
     <p class='muted'>Status: {bot_status} - {bot_name}</p>
      <form method='post' action='/save-config'>
        <input type='hidden' name='kind' value='telegram'>
        <input type='text' name='bot_token' placeholder='HTTP API Token' value='{token}'>
        <input type='text' name='acl_usernames' placeholder='Allowed Telegram usernames (comma or space separated, with or without @)' value='{acl_value}' title='Enter comma or space-separated Telegram usernames (e.g., @myself, @friend). Works only for users who have set a username. To restrict responses to specific users (like yourself), set a Telegram username in Settings and enter it here.'>
        <label class='checkbox-container' style='margin-top:12px'>
          <input type='checkbox' name='telegram_access_gate_enabled' value='true' {'checked' if telegram_access_gate_enabled else ''}>
          <span class='checkmark'></span>
          <span class='checkbox-label'>Require Telegram sender approval (`/allow` flow)</span>
        </label>
        <label class='checkbox-container' style='margin-top:8px'>
          <input type='checkbox' name='telegram_silent_unapproved_messages' value='true' {'checked' if telegram_silent_unapproved_messages else ''}>
          <span class='checkmark'></span>
          <span class='checkbox-label'>Silently ignore unapproved senders</span>
        </label>
        <input type='text' name='acl_sender_ids' placeholder='Allowed Telegram user IDs (comma or space separated)' value='{acl_sender_value}' title='Preferred durable Telegram allowlist. Enter numeric Telegram user IDs, with or without telegram:/tg: prefixes. When set, sender IDs take precedence over username-based access control.'>
        <small class='muted' style='display:block;margin-top:6px'>Allowed Telegram usernames and user IDs both accept comma or space separated values. Sender IDs are the preferred durable allowlist and take precedence over username matching. When sender approval is enabled, unauthorized users see their Telegram user ID and can redeem a one-time <code>/allow XXXXXXXX</code> code generated below. If silent ignore is enabled, unapproved messages are dropped without a reply, but <code>/allow XXXXXXXX</code> still works.</small>
        <div style='margin-top:8px'>
          <button type='submit'>Save Telegram</button>
          <span class='muted' style='margin-left:12px'>Need help? <a href='https://core.telegram.org/bots/tutorial#obtain-bot-token' target='_blank'>Obtain bot token</a> or <a href='https://core.telegram.org/bots/features#creating-a-new-bot' target='_blank'>create a new bot</a>.</span>
        </div>
      </form>
      <form method='post' action='/admin/telegram/allow-code' style='margin-top:12px'>
        <button type='submit' class='secondary-btn'>Generate /allow Code</button>
        <span class='muted' style='margin-left:12px'>Creates a one-time approval code and arms Telegram sender locking until an approved account redeems it.</span>
      </form>
    </div>

    {signal_ui}

    <div class='card' id='whatsapp-settings-card'>
      <div class='whatsapp-header'>
        <h2>Messaging Partner Settings - WhatsApp</h2>
          <button type='button' id='whatsapp-restart-btn-header' data-no-loading='1' onclick='restartWhatsApp()' class='restart-btn' {'style="display:inline-flex"' if whatsapp_enabled else 'style="display:none"'}>
         <svg class='restart-icon' viewBox='0 0 24 24'>
           <path d='M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z'/>
         </svg>
         Restart Service
       </button>
      </div>
      <p class='muted whatsapp-status-text'>Status: {whatsapp_status} - {whatsapp_name}</p>
      {f'<p class="muted">Paired Phone Number: <b>{whatsapp_phone_number}</b></p>' if whatsapp_paired and whatsapp_phone_number else ''}
      <form method='post' action='/save-config'>
        <input type='hidden' name='kind' value='whatsapp'>
        <div class='row'>
          <div class='col'>
            <label class='checkbox-container'>
              <input type='checkbox' name='whatsapp_enabled' value='true' {'checked' if whatsapp_enabled else ''}>
              <span class='checkmark'></span>
              <span class='checkbox-label'>Enable WhatsApp Messaging Partner</span>
            </label>
          </div>
        </div>
        <div class='row'>
          <div class='col'>
            <label for='whatsapp_port'>WhatsApp WebSocket Port</label>
            <input type='number' id='whatsapp_port' name='whatsapp_port' placeholder='Port' value='{whatsapp_port}' min='1' max='65535'>
          </div>
          <div class='col'>
            <label for='whatsapp_device_name'>Device Name</label>
            <input type='text' id='whatsapp_device_name' name='whatsapp_device_name' placeholder='Device Name' value='{whatsapp_device_name}' {'readonly' if whatsapp_paired else ''} {'style="background-color: #1a1a1a; color: #888;"' if whatsapp_paired else ''}>
            {f'<small class="muted">Device name is read-only when paired. Current name from WhatsApp API.</small>' if whatsapp_paired else '<small class="muted">Device name can only be set before pairing.</small>'}
          </div>
        </div>
        <div style='margin-top:16px'>
          <button type='submit' class='primary-btn'>Save WhatsApp Configuration</button>
          <button type='button' id='whatsapp-qr-btn' onclick='showWhatsAppQR()' class='secondary-btn' {'style="display:none"' if whatsapp_paired else ''}>Show QR Code</button>
          <button type='button' id='whatsapp-cleanup-btn' onclick='cleanupWhatsApp()' class='danger-btn'>Cleanup & Re-pair</button>
        </div>
      </form>
      <div id='whatsapp-qr-container' style='margin-top:12px;text-align:center;'></div>
    </div>
    <script>
      // Auto-render QR code if WhatsApp is enabled but not paired
      document.addEventListener('DOMContentLoaded', function() {{
        if ({str(whatsapp_enabled).lower()} && '{whatsapp_status}' !== 'Connected' && !{str(whatsapp_paired).lower()}) {{
          showWhatsAppQR();
        }}
    }});
    </script>

    <div class='card' id='security-settings-card'>
    <h2>Security Mode & 2FA</h2>
        <p class='muted'>Default is <b>Secure Mode</b>. Secure Professional Maximus is the strongest protection for saved sessions, agent data, websites, notes, and settings on this computer. Your existing remote viewer/editor/admin controls continue to apply.</p>
      <form method='post' action='/save-security'>
        <div class='row'>
          <div class='col'>
            <label class='checkbox-container'>
              <input type='checkbox' name='security_mode' value='normal' onchange='handleSecurityModeChange(this)' {'checked' if get_security_mode() == 'normal' else ''}>
              <span class='checkmark'></span>
              <span class='checkbox-label'>Normal</span>
            </label>
            <label class='checkbox-container'>
              <input type='checkbox' name='security_mode' value='secure' onchange='handleSecurityModeChange(this)' {'checked' if get_security_mode() == 'secure' else ''}>
              <span class='checkmark'></span>
              <span class='checkbox-label'>Secure Mode</span>
            </label>
            <label class='checkbox-container'>
              <input type='checkbox' name='security_mode' value='secure_professional' onchange='handleSecurityModeChange(this)' {'checked' if get_security_mode() == 'secure_professional' else ''}>
              <span class='checkmark'></span>
                            <span class='checkbox-label'>Secure Professional (Password + 2FA)</span>
            </label>
            <label class='checkbox-container'>
              <input type='checkbox' name='security_mode' value='secure_professional_maximus' onchange='handleSecurityModeChange(this)' {'checked' if get_security_mode() == 'secure_professional_maximus' else ''}>
              <span class='checkmark'></span>
              <span class='checkbox-label'>Secure Professional Maximus (strongest local protection)</span>
            </label>
          </div>
        </div>
        <div style='margin-top:12px'><button type='submit' class='primary-btn'>Save Security Mode</button></div>
      </form>
      <div id='security-help' style='margin-top:12px'>
        <div class='card'>
          <h3>How security modes work</h3>
          <div id='mode-help-normal' style='{'' if get_security_mode() == 'normal' else 'display:none;'}'>
            <p class='muted'>Normal mode keeps pairing messages readable. Use only for deliberate trusted-local testing.</p>
          </div>
          <div id='mode-help-secure' style='{'' if get_security_mode() == 'secure' else 'display:none;'}'>
            <p class='muted'>Secure mode encrypts pairing with the server password while keeping setup simple.</p>
            <p class='muted'>Use this as the default baseline for local-first installs.</p>
          </div>
          <div id='mode-help-pro' style='{'' if get_security_mode() == 'secure_professional' else 'display:none;'}'>
                        <p class='muted'>Secure Professional adds one shared 2FA setup key to encrypted pairing. Use it for public links, authenticator-based setup, and admin-agent elevation.</p>
                        <p class='muted'>Configure the shared 2FA setup key below, then re-export client setup details after changing it.</p>
          </div>
          <div id='mode-help-maximus' style='{'' if get_security_mode() == 'secure_professional_maximus' else 'display:none;'}'>
                        <p class='muted'>Secure Professional Maximus keeps saved sessions, agent data, websites, notes, and settings protected on this computer while retaining secure pairing.</p>
                        <p class='muted'>Remote viewer, editor, and admin permissions remain enforced exactly as configured.</p>
          </div>
        </div>
      </div>
      <div id='totp-section' style='margin-top:16px'>
        <h3>Two-Factor Authentication (2FA)</h3>
        <p class='muted'>This one shared 2FA setup key is used for Secure Professional pairing, authenticator-based public URL pairing, and admin-agent elevation. The main web admin login does not use 2FA.</p>
        <div class='card'>
          <p class='muted'>Clients can scan and save this one shared 2FA setup key for encrypted reconnects, authenticator-based pairing, and admin-agent verification.</p>
          <label for='pair-totp-secret'>Saved 2FA setup key</label>
          <div style='display:flex; gap:8px;'>
            <input type='password' id='pair-totp-secret' class='mono' readonly value='{html.escape(pairing_totp_secret_display, quote=True)}' data-raw='{html.escape(pairing_totp_secret, quote=True)}' placeholder='No 2FA setup key saved' style='flex:1;'>
            <button type='button' id='pair-totp-secret-toggle' class='ghost' data-no-loading='1' data-show-label='&#128065;' data-hide-label='Hide' title='Show or hide the saved 2FA setup key' onclick="toggleSecret('pair-totp-secret')">&#128065;</button>
          </div>
          <label for='pair-totp-import' style='margin-top:12px;display:block;'>Import existing authenticator setup link or key</label>
          <textarea id='pair-totp-import' class='mono' rows='3' placeholder='Paste an authenticator setup link or setup key from another AutoYou server'></textarea>
          <div style='margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;'>
            <button type='button' class='secondary-btn' data-no-loading='1' onclick="generateTotpSecret()">Generate New</button>
            <button type='button' class='ghost' data-no-loading='1' onclick="showSavedTotpSecret()">Show QR</button>
            <button type='button' class='ghost' data-no-loading='1' onclick="showTotpCode()">Show Code</button>
            <button type='button' class='ghost' data-no-loading='1' onclick="importTotpSecret()">Use Existing</button>
            <button type='button' class='danger-btn' data-no-loading='1' onclick="deleteTotpSecret()">Remove</button>
          </div>
          <div id='pair-totp-result' style='margin-top:12px'></div>
        </div>
      </div>
    </div>
""" + """
      <script>
        function handleSecurityModeChange(selected) {
          const inputs = document.querySelectorAll('input[name="security_mode"]');
          inputs.forEach(i => { if (i !== selected) i.checked = false; });
          updateSecurityModeHelp();
        }

        function updateSecurityModeHelp() {
          const inputs = document.querySelectorAll('input[name="security_mode"]');
          const sel = Array.from(inputs).find(i => i.checked)?.value || 'normal';
          const helpNormal = document.getElementById('mode-help-normal');
          const helpSecure = document.getElementById('mode-help-secure');
          const helpPro = document.getElementById('mode-help-pro');
          const helpMaximus = document.getElementById('mode-help-maximus');
          if (!helpNormal || !helpSecure || !helpPro || !helpMaximus) return;
          helpNormal.style.display = (sel === 'normal') ? '' : 'none';
          helpSecure.style.display = (sel === 'secure') ? '' : 'none';
          helpPro.style.display = (sel === 'secure_professional') ? '' : 'none';
          helpMaximus.style.display = (sel === 'secure_professional_maximus') ? '' : 'none';
        }

        function escapeHtml(value) {
          return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
        }

        function hideTotpCard(buttonEl) {
          const card = buttonEl && buttonEl.closest ? buttonEl.closest('.totp-generated-card') : null;
          if (card && card.parentElement) card.parentElement.innerHTML = '';
        }

        function getTotpUi() {
          return {
            target: 'pairing',
            secret: document.getElementById('pair-totp-secret'),
            toggle: document.getElementById('pair-totp-secret-toggle'),
            importValue: document.getElementById('pair-totp-import'),
            result: document.getElementById('pair-totp-result'),
          };
        }

        function buildTotpQrHtml(data, heading) {
          const enc = encodeURIComponent(data.otpauth);
          const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${enc}`;
          const issuer = escapeHtml(data.issuer || 'AutoYou-Server');
          const accountName = escapeHtml(data.account_name || '2FA account');
          const secret = escapeHtml(data.secret);
          const otpauth = escapeHtml(data.otpauth);
          const title = heading ? `<div class='card-title' style='font-size:.92rem;margin-bottom:10px'>${escapeHtml(heading)}</div>` : '';
          return `
            <div class='card totp-qr-card'>
              ${title}
              <p class='totp-qr-meta'>
                <b>Account:</b> <code class='mono'>${accountName}</code>
                <span class='totp-qr-issuer'>Issuer: ${issuer}</span>
              </p>
              <img src='${qrUrl}' alt='2FA QR Code' class='totp-qr-image'>
              <details class='totp-qr-details'>
                <summary>Show setup key and authenticator setup link</summary>
                <p style='margin-top:8px'><b>Setup key:</b> <code class='mono'>${secret}</code></p>
                <p><b>Authenticator setup link:</b> <code class='mono' style='word-break:break-all'>${otpauth}</code></p>
              </details>
            </div>`;
        }

        function showTotpPayload(data, successMessage) {
          const ui = getTotpUi();
          if (!ui.result) return;
          if (ui.secret) {
            ui.secret.type = 'password';
            ui.secret.setAttribute('data-raw', data.secret || '');
            ui.secret.value = data.secret ? '{MASKED_SECRET_PLACEHOLDER}' : '';
          }
          if (ui.toggle) {
            ui.toggle.innerHTML = ui.toggle.getAttribute('data-show-label') || 'Show';
          }
          ui.result.innerHTML = `
            <div class='card totp-generated-card'>
              <div class='totp-client-row' style='padding:0;border-bottom:0'>
                <h4 style='margin:0'>2FA QR</h4>
                <button type='button' class='ghost' data-no-loading='1' onclick='hideTotpCard(this)'>Hide</button>
              </div>
              <p class='muted' style='margin:6px 0 0 0;font-size:0.9em'>${escapeHtml(successMessage || 'Scan this QR in your authenticator app. It stays valid until you replace or remove the secret.')}</p>
              ${buildTotpQrHtml(data, '')}
            </div>`;
        }

        function generateTotpSecret() {
          const ui = getTotpUi();
          if (ui.result) ui.result.innerHTML = `<div class='card muted'>Generating&hellip;</div>`;
          fetch('/admin/security/totp/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target: 'pairing' }),
          }).then(r => r.json()).then(data => {
            if (!data.success) {
              if (ui.result) ui.result.innerHTML = `<div class='card danger'>${escapeHtml(data.error || 'Error generating 2FA')}</div>`;
              return;
            }
            showTotpPayload(data, 'Generated a new 2FA setup key.');
          }).catch(err => {
            if (ui.result) ui.result.innerHTML = `<div class='card danger'>${escapeHtml(String(err || 'Error generating 2FA'))}</div>`;
          });
        }

        function showSavedTotpSecret() {
          const ui = getTotpUi();
          if (ui.result) ui.result.innerHTML = `<div class='card muted'>Loading&hellip;</div>`;
          fetch('/admin/security/totp/show', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target: 'pairing' }),
          }).then(r => r.json()).then(data => {
            if (!data.success) {
              if (ui.result) ui.result.innerHTML = `<div class='card danger'>${escapeHtml(data.error || 'Error loading 2FA')}</div>`;
              return;
            }
            showTotpPayload(data, 'Saved 2FA setup key.');
          }).catch(err => {
            if (ui.result) ui.result.innerHTML = `<div class='card danger'>${escapeHtml(String(err || 'Network error'))}</div>`;
          });
        }

        let _totpCodeTimer = null;
        function showTotpCode() {
          const ui = getTotpUi();
          if (_totpCodeTimer) { clearInterval(_totpCodeTimer); _totpCodeTimer = null; }
          if (ui.result) ui.result.innerHTML = `<div class='card muted'>Loading&hellip;</div>`;
          function fetchAndRender() {
            fetch('/admin/security/totp/current-code').then(r => r.json()).then(data => {
              if (!data.success) {
                if (_totpCodeTimer) { clearInterval(_totpCodeTimer); _totpCodeTimer = null; }
                if (ui.result) ui.result.innerHTML = `<div class='card danger'>${escapeHtml(data.error || 'No 2FA code available')}</div>`;
                return;
              }
              const secs = data.seconds_remaining || 30;
              const pct = Math.round((secs / (data.period || 30)) * 100);
              if (ui.result) ui.result.innerHTML = `
                <div class='card totp-code-card' style='text-align:center;padding:18px 20px;'>
                  <div style='font-size:0.78rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:var(--muted);margin-bottom:8px'>Current 2FA Code</div>
                  <div id='totp-live-code' style='font-size:2.4rem;font-weight:700;letter-spacing:0.38em;font-family:monospace;color:var(--accent)'>${escapeHtml(data.code)}</div>
                  <div style='margin-top:12px;display:flex;align-items:center;justify-content:center;gap:10px;'>
                    <div style='flex:1;max-width:140px;height:6px;background:var(--stroke-soft,rgba(71,85,105,.3));border-radius:3px;overflow:hidden;'>
                      <div id='totp-code-bar' style='height:100%;background:var(--accent);border-radius:3px;transition:width 1s linear;width:${pct}%'></div>
                    </div>
                    <span id='totp-code-secs' style='font-size:0.82rem;color:var(--muted);min-width:24px'>${secs}s</span>
                    <button class='ghost' data-no-loading='1' style='font-size:0.8rem;padding:3px 8px;' onclick='showTotpCode()'>Refresh</button>
                    <button class='ghost' data-no-loading='1' style='font-size:0.8rem;padding:3px 8px;' onclick='hideTotpCodeCard()'>Close</button>
                  </div>
                </div>`;
              let remaining = secs;
              if (_totpCodeTimer) clearInterval(_totpCodeTimer);
              _totpCodeTimer = setInterval(function() {
                remaining -= 1;
                if (remaining <= 0) {
                  clearInterval(_totpCodeTimer);
                  _totpCodeTimer = null;
                  fetchAndRender();
                  return;
                }
                const p = Math.round((remaining / (data.period || 30)) * 100);
                const bar = document.getElementById('totp-code-bar');
                const secsEl = document.getElementById('totp-code-secs');
                if (bar) bar.style.width = p + '%';
                if (secsEl) secsEl.textContent = remaining + 's';
              }, 1000);
            }).catch(err => {
              if (_totpCodeTimer) { clearInterval(_totpCodeTimer); _totpCodeTimer = null; }
              if (ui.result) ui.result.innerHTML = `<div class='card danger'>${escapeHtml(String(err || 'Network error'))}</div>`;
            });
          }
          fetchAndRender();
        }
        function hideTotpCodeCard() {
          if (_totpCodeTimer) { clearInterval(_totpCodeTimer); _totpCodeTimer = null; }
          const ui = getTotpUi();
          if (ui.result) ui.result.innerHTML = '';
        }

        function importTotpSecret() {
          const ui = getTotpUi();
          const value = ui.importValue?.value?.trim() || '';
          if (!value) {
            if (ui.result) ui.result.innerHTML = `<div class='card danger'>Paste an authenticator setup link or setup key first.</div>`;
            return;
          }
          if (ui.result) ui.result.innerHTML = `<div class='card muted'>Importing&hellip;</div>`;
          fetch('/admin/security/totp/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target: 'pairing', value }),
          }).then(r => r.json()).then(data => {
            if (!data.success) {
              if (ui.result) ui.result.innerHTML = `<div class='card danger'>${escapeHtml(data.error || 'Error importing 2FA')}</div>`;
              return;
            }
            if (ui.importValue) ui.importValue.value = '';
            showTotpPayload(data, 'Imported and saved the 2FA setup key.');
          }).catch(err => {
            if (ui.result) ui.result.innerHTML = `<div class='card danger'>${escapeHtml(String(err || 'Error importing 2FA'))}</div>`;
          });
        }

        function deleteTotpSecret() {
          const ui = getTotpUi();
          if (!confirm('Remove the 2FA setup key?')) return;
          fetch('/admin/security/totp/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target: 'pairing' }),
          }).then(r => r.json()).then(data => {
            if (!data.success) {
              if (ui.result) ui.result.innerHTML = `<div class='card danger'>${escapeHtml(data.error || 'Failed to delete')}</div>`;
              return;
            }
            if (ui.secret) {
              ui.secret.type = 'password';
              ui.secret.value = '';
              ui.secret.setAttribute('data-raw', '');
            }
            if (ui.toggle) ui.toggle.innerHTML = ui.toggle.getAttribute('data-show-label') || 'Show';
            if (ui.importValue) ui.importValue.value = '';
            if (ui.result) ui.result.innerHTML = `<div class='card muted'>Removed the 2FA setup key.</div>`;
          });
        }

        const ONBOARDING_INITIAL_STATE = window.__AUTOYOU_ONBOARDING__ || {};
        window.autoyouWizardState = ONBOARDING_INITIAL_STATE || {};
        window.autoyouWizardStep = 0;
        window.autoyouModelLibrary = {
          source: 'ollama',
          page: 1,
          hasMore: false,
          selectedId: '',
          selectedSource: 'ollama',
          jobsInterval: null,
          statusInterval: null,
          currentDownloadJobId: '',
          detailRequestToken: 0,
        };

        function escapeSetupHtml(value) {
          return String(value || '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
        }

        function setSetupText(id, value) {
          const el = document.getElementById(id);
          if (el) el.textContent = value;
        }

        function setSetupHtml(id, value) {
          const el = document.getElementById(id);
          if (!el) return;
          el.innerHTML = value;
        }

        function formatOllamaLocalCountText(ollama) {
          const localCount = Math.max(0, Number((ollama || {}).local_model_count || 0));
          const cloudCount = Math.max(0, Number((ollama || {}).cloud_model_count || 0));
          if (cloudCount > 0) return `${localCount} local / ${cloudCount} cloud`;
          return `${localCount} local model(s)`;
        }

        function formatOllamaInstalledCountText(ollama) {
          const localCount = Math.max(0, Number((ollama || {}).local_model_count || 0));
          const cloudCount = Math.max(0, Number((ollama || {}).cloud_model_count || 0));
          const installedCount = Math.max(
            localCount + cloudCount,
            Math.max(0, Number((ollama || {}).installed_model_count || 0)),
          );
          if (installedCount <= 0) return '0 installed';
          if (cloudCount > 0) return `${installedCount} installed (${localCount} local, ${cloudCount} cloud)`;
          return `${installedCount} local model(s)`;
        }

        function setCatalogCardLoading(card, isLoading, requestToken) {
          if (!card) return;
          const tokenText = requestToken == null ? '' : String(requestToken);
          if (isLoading) {
            card.classList.add('is-loading');
            card.setAttribute('aria-busy', 'true');
            if (tokenText) card.dataset.detailRequestToken = tokenText;
            return;
          }
          if (tokenText && card.dataset.detailRequestToken && card.dataset.detailRequestToken !== tokenText) {
            return;
          }
          card.classList.remove('is-loading');
          card.removeAttribute('aria-busy');
          delete card.dataset.detailRequestToken;
        }

        const WIZARD_STEP_SEQUENCE = [0, 1, 5, 2, 3, 4, 6, 7];
        const WIZARD_RESUME_KEY = 'autoyouWizardResume';

        function setAdvancedMode(enabled) {
          const url = new URL(window.location.href);
          if (enabled) {
            url.searchParams.set('advanced', '1');
          } else {
            url.searchParams.delete('advanced');
          }
          history.replaceState({}, '', url.toString());
        }

        function persistWizardResume(step) {
          try {
            const safeStep = Math.max(0, Number(step) || 0);
            sessionStorage.setItem(WIZARD_RESUME_KEY, JSON.stringify({
              step: safeStep,
              savedAt: Date.now(),
            }));
          } catch (error) {
            console.debug('Unable to persist wizard resume state:', error);
          }
        }

        function clearWizardResume() {
          try {
            sessionStorage.removeItem(WIZARD_RESUME_KEY);
          } catch (error) {
            console.debug('Unable to clear wizard resume state:', error);
          }
        }

        function maybeResumeWizardFromStorage() {
          try {
            const raw = sessionStorage.getItem(WIZARD_RESUME_KEY);
            if (!raw) {
              return;
            }
            clearWizardResume();
            const payload = JSON.parse(raw);
            if (!payload || (window.autoyouWizardState || {}).wizard_completed) {
              return;
            }
            const savedAt = Number(payload.savedAt || 0);
            if (savedAt && (Date.now() - savedAt) > 30 * 60 * 1000) {
              return;
            }
            openSetupWizard();
            showWizardStep(payload.step || 0);
          } catch (error) {
            clearWizardResume();
            console.debug('Unable to resume wizard state:', error);
          }
        }

        function openSetupWizard() {
          setAdvancedMode(false);
          const overlay = document.getElementById('setup-wizard-overlay');
          if (overlay) overlay.style.display = 'block';
        }

        function hideSetupWizard() {
          const overlay = document.getElementById('setup-wizard-overlay');
          if (overlay) overlay.style.display = 'none';
        }

        function skipToAdvancedSettings() {
          clearWizardResume();
          setAdvancedMode(true);
          hideSetupWizard();
        }

        function openAdvancedSection(id) {
          persistWizardResume(window.autoyouWizardStep || 0);
          setAdvancedMode(true);
          hideSetupWizard();
          const target = document.getElementById(id);
          if (target) {
            adminRevealNode(target);
            window.requestAnimationFrame(() => {
              window.setTimeout(() => {
                if (typeof adminScrollTargetIntoView === 'function') {
                  adminScrollTargetIntoView(target, { behavior: 'smooth' });
                } else {
                  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
                adminHighlightNode(target);
                try {
                  target.setAttribute('tabindex', '-1');
                  target.focus({ preventScroll: true });
                } catch (error) {
                  console.debug('Advanced section focus skipped:', error);
                }
              }, 70);
            });
          }
        }

        async function completeSetupWizard() {
          try {
            const response = await fetch('/api/wizard/complete', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ completed: true })
            });
            const data = await response.json();
            if (!data.success) {
              throw new Error(data.error || 'Failed to mark wizard complete');
            }
            window.autoyouWizardState.wizard_completed = true;
            clearWizardResume();
            setAdvancedMode(false);
            setSetupText('wizard-overall-status', 'Completed');
            syncWizardLauncherVisibility();
            hideSetupWizard();
          } catch (e) {
            alert('Failed to complete wizard: ' + e.message);
          }
        }

        function syncWizardLauncherVisibility() {
          const state = window.autoyouWizardState || {};
          const shouldShow = !state.wizard_completed;
          const launcherCard = document.getElementById('wizard-launcher-card');
          if (launcherCard) {
            launcherCard.hidden = !shouldShow;
          }
          const wizardNavLink = document.querySelector('[data-admin-wizard-link="1"]');
          if (wizardNavLink) {
            wizardNavLink.hidden = !shouldShow;
          }
          if (typeof adminBuildSearchIndex === 'function') {
            adminBuildSearchIndex();
          }
          if (typeof adminRenderJumpChips === 'function') {
            adminRenderJumpChips();
          }
        }

        function showWizardStep(nextStep) {
          const panels = Array.from(document.querySelectorAll('.wizard-step-panel'));
          const buttons = Array.from(document.querySelectorAll('.wizard-step-btn'));
          const maxStep = Math.max(0, WIZARD_STEP_SEQUENCE.length - 1);
          const safeStep = Math.min(Math.max(Number(nextStep) || 0, 0), maxStep);
          const visiblePanelKey = WIZARD_STEP_SEQUENCE[safeStep] ?? safeStep;
          const state = window.autoyouWizardState || {};
          const recommendedModel = (state.ollama || {}).recommended_model || 'default model';
          const titles = [
            ['Security Basics', 'Change the default password before exposing AutoYou beyond localhost.'],
            ['AI Provider', 'Choose the local runtime, gateway, or cloud provider that should power AutoYou.'],
            ['Choose Model', `Pick ${recommendedModel} or switch to another local or downloaded model.`],
            ['Messaging Partner', 'Connect Telegram, WhatsApp, or Signal so AutoYou can be reached remotely.'],
            ['Connectivity Setup', 'Enable the browser tunnel first, then add secure relay servers only if your network needs them.'],
            ['Secure Mode & 2FA', 'Harden the messaging bootstrap path after the password is changed.'],
            ['Speech Settings', 'Review TTS and STT without changing the existing speech stack by default.'],
            ['Finish Setup', 'Review setup status and finish the wizard when the local path is ready.']
          ];
          window.autoyouWizardStep = safeStep;
          panels.forEach((panel) => panel.classList.toggle('active', Number(panel.dataset.step) === visiblePanelKey));
          buttons.forEach((button, index) => button.classList.toggle('active', index === safeStep));
          setSetupText('wizard-step-label', `Step ${safeStep + 1} of ${WIZARD_STEP_SEQUENCE.length}`);
          const progressEl = document.getElementById('wizard-progress-fill');
          if (progressEl) progressEl.style.width = `${((safeStep + 1) / WIZARD_STEP_SEQUENCE.length) * 100}%`;
          setSetupText('wizard-panel-title', titles[safeStep][0]);
          setSetupText('wizard-panel-subtitle', titles[safeStep][1]);
          const prevBtn = document.getElementById('wizard-prev-btn');
          const nextBtn = document.getElementById('wizard-next-btn');
          if (prevBtn) prevBtn.disabled = safeStep === 0;
          if (nextBtn) nextBtn.disabled = safeStep === maxStep;
        }

        function wizardStepCompletion(state) {
          const wizardState = state || {};
          const ollama = wizardState.ollama || {};
          const connectivity = wizardState.connectivity || {};
          const providerSummary = String(wizardState.ai_provider_summary || '').trim();
          const selectedModel = String(ollama.selected_model || '').trim();
          const localModelCount = Number(ollama.local_model_count || ollama.installed_model_count || 0);
          const providerUsesRemoteModels = /google|litellm|openclaw|hermes/i.test(providerSummary);
          return [
            Boolean(wizardState.password_changed),
            Boolean(providerSummary && providerSummary !== 'Unknown'),
            Boolean(selectedModel) && (providerUsesRemoteModels || localModelCount > 0),
            Boolean(wizardState.remote_partner_ready),
            Boolean(connectivity.tunnelmole_enabled || Number(connectivity.ice_server_count || 0) > 0),
            Boolean(wizardState.password_changed) && (!['secure_professional', 'secure_professional_maximus'].includes(String(wizardState.security_mode || 'secure')) || Number(wizardState.totp_client_count || 0) > 0),
            true,
            true,
          ];
        }

        function renderWizardStatus() {
          const state = window.autoyouWizardState || {};
          syncWizardLauncherVisibility();
          const passwordText = state.password_changed
            ? 'Password changed'
            : 'Default password still active';
          const ollama = state.ollama || {};
          const connectivity = state.connectivity || {};
          const messaging = state.messaging || {};
          const securityMode = state.security_mode || 'secure';
          const stepCompletion = wizardStepCompletion(state);
          const completedStepCount = stepCompletion.filter(Boolean).length;
          const tunnelStatus = connectivity.tunnelmole_enabled
            ? `${connectivity.tunnelmole_status || 'Unknown'} - enabled`
            : `${connectivity.tunnelmole_status || 'Unknown'} - disabled`;

          setSetupText('wizard-launcher-password', passwordText);
          // Show active provider in the launcher pill, fall back to Ollama detail
          var aiProviderPillText = (state.ai_provider_summary && state.ai_provider_summary !== 'Unknown')
            ? state.ai_provider_summary
            : (ollama.detail || 'Checking...');
          setSetupText('wizard-launcher-ollama', aiProviderPillText);
          setSetupText('wizard-launcher-messaging', state.remote_partner_ready ? 'At least one connection method is ready' : 'Cloud Pair or messaging partners needed');
          setSetupText('wizard-launcher-connectivity', tunnelStatus);

          setSetupText('wizard-password-status', passwordText);
          setSetupText('wizard-security-status', `Mode: ${securityMode}`);
          setSetupText('wizard-totp-status', Number(state.totp_client_count || 0) > 0 ? 'Configured' : 'Not configured');

          const installedPreviewModels = ollama.installed_models || ollama.local_models || [];
          setSetupText('wizard-active-provider', aiProviderPillText);
          setSetupText('wizard-ollama-runtime-status', ollama.detail || 'Checking...');
          setSetupText('wizard-ollama-api-base', ollama.api_base || 'Uses provider defaults');
          setSetupText('wizard-ollama-model-count', formatOllamaLocalCountText(ollama));
          setSetupText('wizard-default-model-status', ollama.recommended_model_installed ? `${ollama.recommended_model} already installed` : `${ollama.recommended_model} still needs download`);
          setSetupText('wizard-download-recommended-btn', `Download ${ollama.recommended_model}`);
          setSetupText('wizard-example-model', ollama.recommended_model);

          setSetupText('wizard-telegram-status', `${messaging.telegram?.status || 'Unknown'}${messaging.telegram?.label ? ' - ' + messaging.telegram.label : ''}`);
          setSetupText('wizard-signal-status', `${messaging.signal?.status || 'Unknown'}${messaging.signal?.label ? ' - ' + messaging.signal.label : ''}`);
          setSetupText('wizard-whatsapp-status', `${messaging.whatsapp?.status || 'Unknown'}${messaging.whatsapp?.label ? ' - ' + messaging.whatsapp.label : ''}`);
          setSetupText('wizard-cloud-pair-status', 'Premium service at autoyou.me - sign up for seamless iOS & Android access');

          setSetupText('wizard-tunnelmole-status', tunnelStatus);
          setSetupText('wizard-tunnelmole-url', connectivity.tunnelmole_url || 'No public URL yet');
          setSetupText('wizard-ice-count', `${connectivity.ice_server_count || 0} call connectivity server(s) saved`);
          setSetupText('wizard-local-adk-url', connectivity.local_adk_url || 'http://127.0.0.1:8081/dev-ui/');

          setSetupText('wizard-security-mode', securityMode);
          setSetupText('wizard-security-password-copy', state.password_changed ? 'Changed already' : 'Change password first');
          setSetupText('wizard-security-totp-copy', Number(state.totp_client_count || 0) > 0 ? 'Configured' : 'Not configured');

          setSetupText('wizard-selected-model', ollama.selected_model || 'No model selected');
          setSetupText('wizard-installed-models', formatOllamaInstalledCountText({ ...ollama, installed_model_count: ollama.installed_model_count || installedPreviewModels.length }));
          setSetupText('wizard-cloud-copy', ollama.cloud_disabled ? 'Cloud models unavailable' : 'Run ollama signin for cloud models');

          const localPreview = installedPreviewModels.slice(0, 6).map(model => {
            const currentBadge = model.name === ollama.selected_model ? "<span class='model-chip selected'>Selected</span>" : "";
            return `<div class='local-model-card'><strong class='local-model-title'>${escapeSetupHtml(model.name)}</strong><div class='local-model-meta'><span class='local-model-copy'>${escapeSetupHtml(model.size_human || '-')}</span>${currentBadge}</div></div>`;
          }).join('');
          setSetupHtml('wizard-local-model-preview', localPreview || 'No Ollama models discovered yet.');

          document.querySelectorAll('.wizard-step-btn').forEach((button, index) => {
            button.classList.toggle('completed', Boolean(stepCompletion[index]));
          });

          const overall = state.wizard_completed
            ? 'Completed'
            : (completedStepCount === stepCompletion.length ? 'Ready to finish' : `${completedStepCount} of ${stepCompletion.length} steps ready`);
          setSetupText('wizard-overall-status', overall);
        }

        async function refreshWizardStatus() {
          try {
            const response = await fetch('/api/wizard/status');
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Failed to load wizard status');
            window.autoyouWizardState = data;
            renderWizardStatus();
          } catch (e) {
            console.error('Failed to refresh wizard status:', e);
          }
        }

        function setModelSource(source) {
          window.autoyouModelLibrary.source = source;
          window.autoyouModelLibrary.page = 1;
          window.autoyouModelLibrary.selectedId = '';
          window.autoyouModelLibrary.selectedSource = source;
          document.querySelectorAll('.model-source-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.source === source);
          });
          const cloudToggle = document.getElementById('model-include-cloud');
          if (cloudToggle) cloudToggle.disabled = source !== 'ollama';
          searchModelCatalog(false);
        }

        function onCloudModelToggle(enabled) {
          if (enabled) {
            alert([
              '⚠️ Ollama Cloud Models Notice:',
              '',
              'Cloud models run remotely and require signing into your Ollama.com account via the command line:',
              '',
              '  ollama signin',
              '',
              "If you haven't set this up, stick with local models for now. They download once and stay on your device with no sign-in required.",
              '',
              'Learn more: https://docs.ollama.com/cloud'
            ].join('\\n'));
          }
        }

        function buildModelResultKey(source, id) {
          return `${String(source || '')}::${String(id || '')}`;
        }

        function collapseModelSelection(options = {}) {
          const preserveKey = options.preserveKey || '';
          document.querySelectorAll('.model-result-card.active').forEach((card) => {
            if (preserveKey && card.dataset.modelResultKey === preserveKey) {
              return;
            }
            card.classList.remove('active');
          });
          document.querySelectorAll('[data-model-inline-details]').forEach((panel) => {
            if (preserveKey && panel.getAttribute('data-model-inline-details') === preserveKey) {
              return;
            }
            panel.hidden = true;
            panel.innerHTML = '';
          });
          if (!preserveKey) {
            window.autoyouModelLibrary.selectedId = '';
            window.autoyouModelLibrary.selectedSource = '';
          }
        }

        function renderLocalModelList(models, selectedModel) {
          const container = document.getElementById('local-model-list');
          if (!container) return;
          if (!models || models.length === 0) {
            container.innerHTML = "<div class='model-empty-state'>No Ollama model references installed yet.</div>";
            return;
          }
          container.innerHTML = models.map(model => {
            const selected = model.name === selectedModel;
            return `<div class='local-model-card${selected ? ' selected' : ''}'>
              <strong class='local-model-title'>${escapeSetupHtml(model.name)}</strong>
              <span class='local-model-copy'>${escapeSetupHtml(model.size_human || '-')} ${model.quantization_level ? ' | ' + escapeSetupHtml(model.quantization_level) : ''}</span>
              <div class='model-card-actions' style='margin-top:4px'>
                <button type='button' class='model-action-btn ${selected ? 'primary' : 'ghost'}' onclick='useModel(${JSON.stringify(model.name)})'>${selected ? 'Selected Model' : 'Use Model'}</button>
                ${model.is_huggingface ? "<span class='model-chip'>HF</span>" : ""}
                ${model.is_cloud ? "<span class='model-chip cloud'>Cloud</span>" : "<span class='model-chip local'>Local</span>"}
              </div>
            </div>`;
          }).join('');
        }

        async function refreshModelLibraryLocal() {
          try {
            const response = await fetch('/api/model-library/local');
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Failed to load local models');
            const runtime = data.runtime || {};
            const models = data.models || [];
            const runtimeForUi = {
              ...runtime,
              local_model_count: Number(runtime.local_model_count || models.filter(model => !model.is_cloud).length),
              cloud_model_count: Number(runtime.cloud_model_count || models.filter(model => model.is_cloud).length),
              installed_model_count: Number(runtime.installed_model_count || models.length),
            };
            setSetupText('model-runtime-status', runtime.detail || 'Unknown');
            setSetupText('model-runtime-base', runtime.api_base || 'http://localhost:11434');
            setSetupText('model-runtime-selected', data.selected_model || 'No model selected');
            setSetupText('model-runtime-count', formatOllamaInstalledCountText(runtimeForUi));
            renderLocalModelList(models, data.selected_model || '');
            if (window.autoyouWizardState && window.autoyouWizardState.ollama) {
              window.autoyouWizardState.ollama.local_models = models;
              window.autoyouWizardState.ollama.installed_models = models;
              window.autoyouWizardState.ollama.local_model_count = runtimeForUi.local_model_count;
              window.autoyouWizardState.ollama.cloud_model_count = runtimeForUi.cloud_model_count;
              window.autoyouWizardState.ollama.installed_model_count = runtimeForUi.installed_model_count;
              window.autoyouWizardState.ollama.selected_model = data.selected_model || '';
              window.autoyouWizardState.ollama.detail = runtime.detail || window.autoyouWizardState.ollama.detail || '';
              renderWizardStatus();
            }
          } catch (e) {
            console.error('Failed to refresh model library local state:', e);
          }
        }

        function renderCatalogResults(items, append) {
          const container = document.getElementById('model-catalog-results');
          if (!container) return;
          if (!append) {
            container.innerHTML = '';
            collapseModelSelection();
          }
          if (!items || items.length === 0) {
            if (!append) container.innerHTML = "<div class='model-empty-state'>No matching models were found for this query.</div>";
            return;
          }
          const html = items.map(item => {
            const sourceValue = String(item.source || window.autoyouModelLibrary.source || '');
            const idValue = String(item.id || '');
            const resultKey = buildModelResultKey(sourceValue, idValue);
            const badges = (item.capabilities || []).map(cap => `<span class='model-chip ${cap.toLowerCase() === 'cloud' ? 'cloud' : ''}'>${escapeSetupHtml(cap)}</span>`).join('')
              + (item.sizes || []).slice(0, 5).map(size => `<span class='model-chip'>${escapeSetupHtml(size)}</span>`).join('');
            const meta = [];
            if (item.pull_count) meta.push(`${escapeSetupHtml(item.pull_count)} pulls`);
            if (item.downloads) meta.push(`${escapeSetupHtml(item.downloads)} downloads`);
            if (item.likes) meta.push(`${escapeSetupHtml(item.likes)} likes`);
            if (item.updated) meta.push(`updated ${escapeSetupHtml(item.updated)}`);
            if (item.last_modified) meta.push(`updated ${escapeSetupHtml(item.last_modified)}`);
            return `<article class='model-result-shell' data-model-result-key='${escapeSetupHtml(resultKey)}'>
              <button type='button' class='model-result-card' data-no-loading='1' data-model-result-key='${escapeSetupHtml(resultKey)}' data-title='${escapeSetupHtml(String(item.title || item.id || ""))}' data-source='${escapeSetupHtml(sourceValue)}' data-id='${escapeSetupHtml(idValue)}' onclick='selectCatalogItem(${JSON.stringify(sourceValue)}, ${JSON.stringify(idValue)})'>
                <strong class='model-result-title'>${escapeSetupHtml(item.title || item.id)}</strong>
                <span class='model-result-summary'>${escapeSetupHtml(item.summary || '')}</span>
                <div class='model-cap-badges' style='margin-top:10px'>${badges}</div>
                <div class='model-meta-row' style='margin-top:10px;color:#94a3b8;font-size:.82rem'>${meta.map(m => `<span>${m}</span>`).join('')}</div>
              </button>
              <div class='model-inline-detail' data-model-inline-details='${escapeSetupHtml(resultKey)}' hidden></div>
            </article>`;
          }).join('');
          container.insertAdjacentHTML('beforeend', html);
        }

        async function searchModelCatalog(append) {
          const source = window.autoyouModelLibrary.source;
          const searchInput = document.getElementById('model-search-input');
          const includeCloud = document.getElementById('model-include-cloud');
          const nextPage = append ? (window.autoyouModelLibrary.page + 1) : 1;
          try {
            const url = new URL('/api/model-library/catalog', window.location.origin);
            url.searchParams.set('source', source);
            url.searchParams.set('page', String(nextPage));
            if (searchInput && searchInput.value.trim()) {
              url.searchParams.set('q', searchInput.value.trim());
            }
            if (source === 'ollama' && includeCloud && includeCloud.checked) {
              url.searchParams.set('include_cloud', 'true');
            }
            const response = await fetch(url.toString());
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Failed to search model catalog');
            renderCatalogResults(data.items || [], append);
            window.autoyouModelLibrary.page = nextPage;
            window.autoyouModelLibrary.hasMore = !!data.has_more;
            const loadMoreBtn = document.getElementById('model-load-more-btn');
            if (loadMoreBtn) loadMoreBtn.classList.toggle('wizard-hidden', !data.has_more);
          } catch (e) {
            console.error('Model catalog search failed:', e);
            const container = document.getElementById('model-catalog-results');
            if (container && !append) container.innerHTML = `<div class='model-empty-state'>${escapeSetupHtml(e.message)}</div>`;
          }
        }

        function renderInlineModelDetailLoading(panel, label) {
          if (!panel) return;
          const safeLabel = escapeSetupHtml(label || 'selected model');
          panel.hidden = false;
          panel.innerHTML = `<div class='model-detail-loading'><div class='model-detail-loading-spinner' aria-hidden='true'></div><strong>Loading ${safeLabel}</strong><span>Fetching variants, quantizations, and installation status.</span></div>`;
        }

        async function selectCatalogItem(source, id) {
          const resultKey = buildModelResultKey(source, id);
          const activeCard = Array.from(document.querySelectorAll('.model-result-card')).find(card => card.dataset.modelResultKey === resultKey);
          const detailPanel = Array.from(document.querySelectorAll('[data-model-inline-details]')).find(
            (panel) => panel.getAttribute('data-model-inline-details') === resultKey
          );
          const isSameSelection = window.autoyouModelLibrary.selectedSource === source
            && window.autoyouModelLibrary.selectedId === id
            && activeCard
            && activeCard.classList.contains('active');

          if (isSameSelection) {
            window.autoyouModelLibrary.detailRequestToken = (window.autoyouModelLibrary.detailRequestToken || 0) + 1;
            collapseModelSelection();
            return;
          }

          window.autoyouModelLibrary.selectedId = id;
          window.autoyouModelLibrary.selectedSource = source;
          collapseModelSelection({ preserveKey: resultKey });
          if (activeCard) activeCard.classList.add('active');
          const requestToken = (window.autoyouModelLibrary.detailRequestToken || 0) + 1;
          window.autoyouModelLibrary.detailRequestToken = requestToken;
          setCatalogCardLoading(activeCard, true, requestToken);
          renderInlineModelDetailLoading(detailPanel, activeCard ? activeCard.dataset.title : id);
          try {
            const url = new URL('/api/model-library/details', window.location.origin);
            url.searchParams.set('source', source);
            url.searchParams.set('id', id);
            const response = await fetch(url.toString());
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Failed to load details');
            if (requestToken !== window.autoyouModelLibrary.detailRequestToken) return;
            renderModelDetails(detailPanel, data);
          } catch (e) {
            console.error('Failed to load model details:', e);
            if (requestToken === window.autoyouModelLibrary.detailRequestToken && detailPanel) {
              detailPanel.hidden = false;
              detailPanel.innerHTML = `<div class='model-detail-card'><strong>Model details</strong><span>${escapeSetupHtml(e.message)}</span></div>`;
            }
          } finally {
            setCatalogCardLoading(activeCard, false, requestToken);
          }
        }

        function renderModelDetails(container, data) {
          if (!container) return;
          container.hidden = false;

          if (data.source === 'ollama') {
            const hasCloudVariants = (data.variants || []).some(v => v.is_cloud);
            const variants = (data.variants || []).map(variant => {
              const variantBadges = [
                variant.is_cloud ? "<span class='model-chip cloud'>Cloud</span>" : "<span class='model-chip local'>Local</span>",
                variant.installed ? "<span class='model-chip local'>Installed</span>" : ""
              ].join('');
              const downloadLabel = variant.is_cloud ? 'Pull Cloud Reference' : 'Download';
              const useLabel = variant.is_cloud ? 'Use Cloud Model' : 'Use Model';
              const cloudWarning = variant.is_cloud ? "<div style='font-size:.75em;color:#fbbf24;margin-top:6px;padding:6px;background:rgba(245,158,11,.08);border-radius:4px'>⚠️ Requires signing into Ollama.com via CLI</div>" : "";
              const normalizedCloudWarning = variant.is_cloud ? "<div style='font-size:.75em;color:#fbbf24;margin-top:6px;padding:6px;background:rgba(245,158,11,.08);border-radius:4px'>Runs remotely via Ollama Cloud after <code>ollama signin</code>.</div>" : cloudWarning;
              return `<div class='local-model-card'>
                <strong class='local-model-title'>${escapeSetupHtml(variant.display_name || variant.name)}</strong>
                <span class='local-model-copy'>${escapeSetupHtml([variant.size, variant.context, variant.input_type].filter(Boolean).join(' | ') || 'Variant')}</span>
                <div class='model-cap-badges' style='margin-top:10px'>${variantBadges}</div>
                ${normalizedCloudWarning}
                <div class='model-card-actions' style='margin-top:${variant.is_cloud ? "8" : "4"}px'>
                  <button type='button' class='model-action-btn ghost' onclick='startModelDownload({ source: "ollama", reference: ${JSON.stringify(variant.name)}, title: ${JSON.stringify(variant.name)} })'>${downloadLabel}</button>
                  <button type='button' class='model-action-btn primary' onclick='useModel(${JSON.stringify(variant.name)})'>${useLabel}</button>
                </div>
              </div>`;
            }).join('');
            const cloudBanner = hasCloudVariants ? "<div style='background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.16);border-radius:6px;padding:10px 12px;margin-bottom:12px;font-size:.8em;color:#b891f9'><strong style='color:#fbbf24'>💡 Tip:</strong> Local models are recommended. They download once and run on your device. Cloud models require signing into Ollama first.</div>" : "";
            const normalizedCloudBanner = hasCloudVariants ? "<div style='background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.16);border-radius:10px;padding:10px 12px;font-size:.8em;color:#b891f9'><strong style='color:#fbbf24'>Tip:</strong> Local models stay on this device. Ollama Cloud variants run remotely after <code>ollama signin</code>.</div>" : cloudBanner;
            container.innerHTML = `${normalizedCloudBanner}<div class='model-inline-detail-header'><strong>${escapeSetupHtml(data.title || data.id)}</strong><span>${escapeSetupHtml(data.summary || '')}</span></div><div class='model-inline-actions'><a class='model-toolbar-btn ghost' href='${escapeSetupHtml(data.url || '#')}' target='_blank'>Open on Ollama</a></div><div class='model-variant-list'>${variants || "<div class='model-empty-state'>No tags discovered for this model.</div>"}</div>`;
            return;
          }

          const files = (data.gguf_files || []).map(file => {
            const badges = [
              "<span class='model-chip local'>GGUF</span>",
              file.installed ? "<span class='model-chip local'>Installed</span>" : ""
            ].join('');
            return `<div class='local-model-card'>
              <strong class='local-model-title'>${escapeSetupHtml(file.quantization || file.filename)}</strong>
              <span class='local-model-copy'>${escapeSetupHtml(file.size_human || '-')} | ${escapeSetupHtml(file.filename)}</span>
              <div class='model-cap-badges' style='margin-top:10px'>${badges}</div>
              <div class='model-card-actions' style='margin-top:4px'>
                <button type='button' class='model-action-btn ghost' onclick='startModelDownload({ source: "huggingface", repo_id: ${JSON.stringify(data.id)}, quantization: ${JSON.stringify(file.quantization)}, title: ${JSON.stringify(file.ollama_reference)} })'>Download via Ollama</button>
                <button type='button' class='model-action-btn primary' onclick='useModel(${JSON.stringify(file.ollama_reference)})'>Use This Reference</button>
              </div>
            </div>`;
          }).join('');
          container.innerHTML = `<div class='model-inline-detail-header'><strong>${escapeSetupHtml(data.title || data.id)}</strong><span>Public GGUF repo for Ollama-compatible downloads. Ollama will fetch this repo locally using the <code>hf.co/...</code> reference.</span></div><div class='model-inline-actions'><a class='model-toolbar-btn ghost' href='${escapeSetupHtml(data.url || '#')}' target='_blank'>Open on Hugging Face</a></div><div class='model-variant-list'>${files || "<div class='model-empty-state'>No GGUF files were found in this repo.</div>"}</div>`;
        }

        async function startModelDownload(payload) {
          try {
            const response = await fetch('/api/model-library/download', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Failed to start download');
            window.autoyouModelLibrary.currentDownloadJobId = data.job.job_id;
            pollDownloadJobs();
          } catch (e) {
            alert('Failed to start model download: ' + e.message);
          }
        }

        async function useModel(modelName) {
          try {
            const response = await fetch('/api/model-library/select', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ model: modelName, restart_ai: true })
            });
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Failed to select model');
            // Sync the Ollama model input in AI Agent Server Control
            var ollamaModelInput = document.querySelector('input[name="ollama_model"]');
            if (ollamaModelInput) ollamaModelInput.value = modelName;
            // Ensure Ollama is selected as the active provider
            var ollamaRadio = document.querySelector('input[name="ai_provider"][value="ollama"]');
            if (ollamaRadio && !ollamaRadio.checked) {
              ollamaRadio.checked = true;
              if (typeof window.syncProviderPanels === 'function') window.syncProviderPanels('ollama');
            }
            await refreshModelLibraryLocal();
            await refreshWizardStatus();
            alert(`Selected ${modelName}${data.restarted_ai ? ' and restarted the AI agent.' : '.'}`);
          } catch (e) {
            alert('Failed to switch model: ' + e.message);
          }
        }

        function renderDownloadJobs(jobs) {
          const container = document.getElementById('model-download-jobs');
          if (!container) return;
          if (!jobs || jobs.length === 0) {
            container.innerHTML = '';
            return;
          }
          container.innerHTML = jobs.map(job => {
            const percent = Number(job.progress_percent || 0);
            const failed = job.status === 'failed';
            return `<div class='download-job'>
              <strong>${escapeSetupHtml(job.title || job.model_name)}</strong>
              <span>${escapeSetupHtml(job.message || job.status)}</span>
              <div class='job-progress-meta' style='margin-top:8px;color:#94a3b8;font-size:.82rem'>
                <span>${escapeSetupHtml(job.status || 'queued')}</span>
                <span>${escapeSetupHtml(job.completed_bytes_human || '-')} / ${escapeSetupHtml(job.total_bytes_human || '-')}</span>
                <span>${percent.toFixed(1)}%</span>
              </div>
              <div class='job-progress-track'><div class='job-progress-fill ${failed ? 'failed' : ''}' style='width:${Math.min(Math.max(percent, 0), 100)}%'></div></div>
              ${job.error ? `<div class='wizard-note' style='margin-top:12px'>${escapeSetupHtml(job.error)}</div>` : ''}
            </div>`;
          }).join('');

          const currentJob = jobs.find(job => job.job_id === window.autoyouModelLibrary.currentDownloadJobId) || jobs[0];
          if (currentJob) {
            setSetupText('wizard-download-copy', `${currentJob.message || currentJob.status} (${currentJob.progress_percent || 0}%)`);
            const fill = document.getElementById('wizard-download-fill');
            if (fill) {
              fill.style.width = `${Math.min(Math.max(Number(currentJob.progress_percent || 0), 0), 100)}%`;
              fill.classList.toggle('failed', currentJob.status === 'failed');
            }
            if (currentJob.status === 'completed') {
              refreshModelLibraryLocal();
              refreshWizardStatus();
            }
          }
        }

        async function pollDownloadJobs() {
          try {
            const response = await fetch('/api/model-library/downloads');
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Failed to poll downloads');
            renderDownloadJobs(data.jobs || []);
          } catch (e) {
            console.error('Failed to poll model downloads:', e);
          }
        }

        function downloadRecommendedModel() {
          const ollama = (window.autoyouWizardState || {}).ollama || {};
          const modelName = ollama.recommended_model || 'ministral-3:8b';
          startModelDownload({ source: 'ollama', reference: modelName, title: modelName });
        }

        function runAdminInitStep(label, callback) {
          try {
            const result = callback();
            if (result && typeof result.catch === 'function') {
              result.catch((error) => {
                console.error(label + ' failed:', error);
              });
            }
            return result;
          } catch (error) {
            console.error(label + ' failed:', error);
            return null;
          }
        }

        // Toggle TOTP section based on selected mode
        document.addEventListener('DOMContentLoaded', function() {
          renderWizardStatus();
          showWizardStep(0);
          runAdminInitStep('updateSecurityModeHelp', updateSecurityModeHelp);
          const agentInstructionsInput = document.getElementById('agent-instructions');
          if (agentInstructionsInput) {
            agentInstructionsInput.addEventListener('input', refreshAgentInstructionMetrics);
          }
          runAdminInitStep('refreshAgentInstructionMetrics', refreshAgentInstructionMetrics);
          runAdminInitStep('loadAgentInstructions', loadAgentInstructions);
          runAdminInitStep('refreshWizardStatus', async () => {
            await refreshWizardStatus();
            maybeResumeWizardFromStorage();
          });
          runAdminInitStep('refreshModelLibraryLocal', refreshModelLibraryLocal);
          runAdminInitStep('searchModelCatalog', () => searchModelCatalog(false));
          runAdminInitStep('pollDownloadJobs', pollDownloadJobs);
          const modelSearchInput = document.getElementById('model-search-input');
          if (modelSearchInput) {
            modelSearchInput.addEventListener('keydown', function(event) {
              if (event.key === 'Enter') {
                event.preventDefault();
                searchModelCatalog(false);
              }
            });
          }
          window.autoyouModelLibrary.jobsInterval = setInterval(pollDownloadJobs, 3000);
          window.autoyouModelLibrary.statusInterval = setInterval(refreshWizardStatus, 7000);
        });
      </script>
""" + f"""

    <div class='card' id='password-settings-card'>
      <h2>Change Server Password</h2>
      <div class='muted' style='margin-bottom:10px'>
        Current password is intentionally never displayed by the admin UI.
      </div>
      <form method='post' action='/change-password'>
        <div class='row'>
          <div class='col'><input type='password' name='new_password' placeholder='New password' required></div>
          <div class='col'><input type='password' name='confirm_password' placeholder='Confirm new password' required></div>
        </div>
        <div style='margin-top:12px'><button class='danger' type='submit'>Update Password</button></div>
      </form>
      {wizard_payload_bootstrap_html}
      {script_html}
    </div>

    <div class='card' id='qr-provision-card'>
      <h2>📱 Export Settings as QR Code</h2>
      <p class='muted'>Generate a QR code containing all your server settings. Scan it from the AutoYou mobile app to instantly configure a new device - no manual setup required.</p>
      <p class='muted' style='color:#f59e0b;margin-top:6px'>⚠️ This QR code contains your server password and all credentials. Only scan it on your own device in a private location.</p>
      <div style='margin-top:12px'>
        <button type='button' class='primary-btn' data-no-loading='1' onclick='showQrProvisionConfirm()'>Generate Provision QR</button>
      </div>
    </div>

    <!-- QR Provision Confirm Modal -->
    <div id='qr-provision-confirm-modal' class='modal'>
      <div class='modal-content'>
        <div class='modal-header'>
          <svg viewBox='0 0 24 24' width='24' height='24' fill='currentColor'>
            <path d='M3 11h2v2H3zm0-4h2v2H3zm0 8h2v2H3zm4-4h2v2H7zm0-4h2v2H7zm0 8h2v2H7zM3 3v4h4V3H3zm1 3V4h2v2H4zm3 14v-4H3v4h4zm-3-1v-2h2v2H4zm7-13h2v2h-2zm0 4h2v2h-2zm0 4h2v2h-2zm4-8h2v2h-2zm0 4h2v2h-2zm0 4h2v2h-2zM11 3v4h4V3h-4zm1 3V4h2v2h-2zm3 14v-4h-4v4h4zm-3-1v-2h2v2h-2zm3-10h4v4h-4v2h2v2h-2v2h4v-2h-2v-2h2v-4h-4V9zm4-6h-4v4h4V3zm-1 3h-2V4h2v2z'/>
          </svg>
          <h3 class='modal-title'>Generate Provision QR?</h3>
        </div>
        <div class='modal-body'>
          <p>This QR code will contain:</p>
          <ul style='margin:8px 0;padding-left:20px'>
            <li>Server password</li>
            <li>Security mode &amp; 2FA setup key</li>
            <li>Messaging partner configuration (Telegram, WhatsApp, Signal)</li>
            <li>Connection helper configuration</li>
            <li>Server name</li>
          </ul>
          <p style='color:#f59e0b;font-weight:500'>Only scan this QR on your own trusted device in a private location.</p>
        </div>
        <div class='modal-actions'>
          <button type='button' class='modal-btn modal-btn-cancel' onclick='hideQrProvisionConfirm()'>Cancel</button>
          <button type='button' class='modal-btn modal-btn-confirm' style='background:#22c55e;color:#fff' onclick='generateProvisionQr()'>Generate QR</button>
        </div>
      </div>
    </div>

    <!-- QR Provision Display Modal -->
    <div id='qr-provision-display-modal' class='modal'>
      <div class='modal-content' style='max-width:420px;text-align:center'>
        <div class='modal-header' style='justify-content:center'>
          <h3 class='modal-title'>📱 Scan to Configure AutoYou</h3>
        </div>
        <div class='modal-body'>
          <p class='muted' style='margin-bottom:16px'>Point your AutoYou mobile app at this QR code. The app will automatically apply all settings.</p>
          <div id='qr-provision-img-wrap' style='display:flex;justify-content:center;margin-bottom:12px'>
            <img id='qr-provision-img' src='' alt='Provision QR' style='width:260px;height:260px;border:4px solid #22c55e;border-radius:8px'>
          </div>
          <p class='muted' style='font-size:0.8em;color:#f59e0b'>This QR expires after 5 minutes. Keep it private.</p>
          <div id='qr-provision-countdown' style='font-size:0.85em;color:#888;margin-top:4px'></div>
        </div>
        <div class='modal-actions' style='justify-content:center'>
          <button type='button' class='modal-btn modal-btn-cancel' data-no-loading='1' onclick='closeQrProvisionDisplay()'>Close</button>
        </div>
      </div>
    </div>

    {_QR_PROVISION_SCRIPT_HTML}

    <section id='minigame-card'>
      {_build_boot_sweep_widget_html(
        title='&#127918; Boot Sweep Mini Game',
        heading_tag='h2',
        description='Click signal pings as fast as you can. Score = (clicks &divide; seconds) &times; 100. Also playable on the login screen during startup.',
        board_id='admin-mg-board',
        score_id='admin-mg-score',
        best_score_id='admin-mg-best',
        best_score_initial='&mdash;',
        note='Best score persists across restarts. Shared with the login screen game.',
        actions_html='''
          <button type="button" class="secondary-btn" id="admin-mg-start-btn" data-no-loading="1">&#9654; Play</button>
          <button type="button" class="ghost" id="admin-mg-stop-btn" data-no-loading="1" disabled>&#9646;&#9646; Stop</button>
          <button type="button" class="ghost" id="admin-mg-reset-btn" data-no-loading="1" title="Clear the saved high score">&#128465; Reset Score</button>
        ''',
        status_id='admin-mg-status',
        best_label='Best score (clicks/s &times; 100)',
        history_table_id='admin-mg-history',
        history_title='Top 10 high scores',
        history_caption='Saved locally with score, date/time, clicks, and seconds.',
      )}
      <div id='admin-mg-reset-status' class='boot-sweep-status'></div>
    </section>
    {_build_boot_sweep_runtime_script({
      'instanceName': 'adminBootSweepGame',
      'boardId': 'admin-mg-board',
      'scoreId': 'admin-mg-score',
      'bestScoreId': 'admin-mg-best',
      'historyTableId': 'admin-mg-history',
      'statusId': 'admin-mg-status',
      'startButtonId': 'admin-mg-start-btn',
      'stopButtonId': 'admin-mg-stop-btn',
      'resetButtonId': 'admin-mg-reset-btn',
      'resetStatusId': 'admin-mg-reset-status',
      'emptyBestText': '-',
      'nodeTitle': 'Click signal ping',
    })}

    {_build_jailbreak_card_html()}
    {_build_help_guides_card_html()}
    {build_admin_dashboard_shell_close_html()}
    {admin_search_script_html}
    """
