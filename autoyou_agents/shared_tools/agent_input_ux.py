# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Shared focus and visual-viewport behavior for agent websites."""

import re

from fastapi.responses import HTMLResponse


INPUT_UX = r'''<style id="autoyou-input-ux">
:root { --autoyou-viewport-height: 100dvh; --autoyou-viewport-top: 0px; }
:where(input:not([type=range]):not([type=checkbox]):not([type=radio]):not([type=color]), textarea, select, [contenteditable=true]) {
  scroll-margin-block: 28px;
}
:where(input, textarea, select, [contenteditable=true]):focus-visible {
  outline: 3px solid Highlight !important; outline-offset: 3px;
}
@media (pointer: coarse) {
  :where(input:not([type=range]):not([type=checkbox]):not([type=radio]):not([type=color]), textarea, select, [contenteditable=true]) {
    font-size: max(16px, 1rem) !important; min-height: 44px;
  }
  html[data-autoyou-editing=true] :where([role=dialog], [role=alertdialog]) {
    max-height: calc(var(--autoyou-viewport-height) - 16px);
    overflow-y: auto; scroll-padding-block: 24px;
  }
  html[data-autoyou-editing=true] :where(.sheet-shell, .confirm-shell, #autoyou-auth-gate) {
    top: var(--autoyou-viewport-top); bottom: auto;
    height: var(--autoyou-viewport-height);
  }
  [data-autoyou-keyboard-overlay=true] {
    top: var(--autoyou-viewport-top) !important; bottom: auto !important;
    height: var(--autoyou-viewport-height) !important;
    max-height: var(--autoyou-viewport-height) !important; overflow-y: auto !important;
  }
}
</style><script>
(function () {
  if (window.__autoyouInputUx) return;
  window.__autoyouInputUx = true;
  var root = document.documentElement;
  var pending;
  function editable(node) {
    return node instanceof HTMLElement && node.matches('textarea, select, [contenteditable="true"], input:not([type=hidden]):not([type=button]):not([type=submit]):not([type=range]):not([type=checkbox]):not([type=radio]):not([type=color]):not([type=file])') && !node.disabled && !node.readOnly;
  }
  function sync(reveal) {
    var viewport = window.visualViewport;
    var height = viewport ? viewport.height : window.innerHeight;
    var top = viewport ? viewport.offsetTop : 0;
    root.style.setProperty('--autoyou-viewport-height', height + 'px');
    root.style.setProperty('--autoyou-viewport-top', top + 'px');
    var field = document.activeElement;
    root.dataset.autoyouEditing = String(editable(field));
    document.querySelectorAll('[data-autoyou-keyboard-overlay]').forEach(function (node) {
      if (!editable(field) || !node.contains(field)) delete node.dataset.autoyouKeyboardOverlay;
    });
    if (editable(field) && window.matchMedia('(pointer: coarse)').matches && height < window.innerHeight - 100) {
      for (var ancestor = field.parentElement; ancestor && ancestor !== document.body; ancestor = ancestor.parentElement) {
        var rect = ancestor.getBoundingClientRect();
        if (getComputedStyle(ancestor).position === 'fixed' && rect.height > height && rect.width > window.innerWidth * .75) {
          ancestor.dataset.autoyouKeyboardOverlay = 'true';
        }
      }
    }
    if (!reveal || !editable(field) || (viewport && viewport.scale > 1.05)) return;
    var box = field.getBoundingClientRect();
    if (box.top < top + 24 || box.bottom > top + height - 24) {
      field.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
    }
  }
  function changed() {
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(function () { sync(true); });
  }
  document.addEventListener('focusin', function () {
    changed();
    setTimeout(changed, 350);
  });
  document.addEventListener('focusout', function () { setTimeout(function () { sync(false); }, 0); });
  window.addEventListener('resize', changed, {passive: true});
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', changed, {passive: true});
    window.visualViewport.addEventListener('scroll', changed, {passive: true});
  }
  sync(false);
})();
</script>'''


def inject_agent_input_ux(content: str) -> str:
    if 'id="autoyou-input-ux"' in content or not re.search(r"<head\b", content, re.I):
        return content
    return re.sub(r"</head\s*>", lambda _: INPUT_UX + "</head>", content, count=1, flags=re.I)


class AgentHTMLResponse(HTMLResponse):
    def render(self, content) -> bytes:
        return super().render(inject_agent_input_ux(content) if isinstance(content, str) else content)
