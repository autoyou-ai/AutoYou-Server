# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License. See LICENSE.

"""Clipboard action for one-time-code fields on proxied agent pages."""

# A browser gesture reads clipboard text into the selected field. Verification
# remains the responsibility of the agent's existing authentication form.
OTP_PASTE_SCRIPT = r"""<script>
(() => {
  if (window.__autoyouOtpPasteInstalled) return;
  window.__autoyouOtpPasteInstalled = true;
  const selector = 'input[autocomplete="one-time-code"]';
  const attached = new WeakSet();
  function attach(input) {
    if (attached.has(input) || input.maxLength === 1) return;
    attached.add(input);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'autoyou-paste-code';
    button.textContent = 'Paste code';
    button.style.cssText = 'min-height:44px;margin:.25rem 0;padding:.4rem .7rem;';
    const status = document.createElement('small');
    status.setAttribute('role', 'status');
    status.style.display = 'block';
    const anchor = input.form || input.closest('label') || input;
    anchor.after(button, status);
    function usable() {
      return input.isConnected && input.getClientRects().length && !input.disabled && !input.readOnly
        && getComputedStyle(input).visibility === 'visible';
    }
    button.addEventListener('click', async (event) => {
      if (!event.isTrusted || !usable()) return;
      status.textContent = '';
      try {
        const raw = await navigator.clipboard.readText();
        if (!usable()) return;
        const code = raw.replace(/[\s-]/g, '');
        if (!/^(?:[0-9]{6}|[0-9]{8})$/.test(code) || (input.maxLength > 0 && code.length > input.maxLength)) {
          status.textContent = 'Copy a code from your authenticator and try again.';
          return;
        }
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, code);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      } catch (_) {
        if (!input.isConnected) return;
        input.focus();
        status.textContent = 'Use Paste in the keyboard or browser menu.';
      }
    });
  }
  function scan(root) {
    if (root.matches?.(selector)) attach(root);
    root.querySelectorAll?.(selector).forEach(attach);
  }
  document.addEventListener('DOMContentLoaded', () => {
    scan(document);
    new MutationObserver(records => {
      for (const record of records) for (const child of record.addedNodes) scan(child);
    }).observe(document.body, { childList: true, subtree: true });
  }, { once: true });
})();
</script>"""
