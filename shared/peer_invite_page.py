# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-467c5b7e9d2fa369ac526aeb

"""The invite landing page.

This is what someone sees when they tap an AutoYou invite link. It has one job:
get them into the app with the invitation intact, whether or not they already
have it installed.

Everything sensitive stays on the device
---------------------------------------

The invitation passphrase lives in the URL fragment. Browsers never transmit a
fragment, so it does not reach this server - and this page keeps it that way:

* the fragment is read only by client-side script and is never put in a request,
  a form, an image URL or a link that leaves the page;
* it is never written to ``document.title`` or anywhere the browser would send
  to a referrer;
* the "Open in AutoYou" action hands it to the app through the ``autoyou://``
  scheme, which stays local to the device.

The inviter's display name is attacker-authorable text, so it is only ever
written with ``textContent``. Nothing on this page interpolates it into HTML.

The page renders entirely from the fragment and needs no server round trip, so
it works before the invitee has any relationship with this server at all.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from typing import Optional

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-467c5b7e9d2fa369ac526aeb"


#: Where someone without the app is sent. Kept as a constant so the page and the
#: docs cannot drift apart on it.
DOWNLOAD_URL = "https://www.autoyou.me/downloads/"
# from __debug_provenance_m__ import of


def render_invite_landing_page(*, stylesheet_href: str = "/assets/admin-ui.css") -> str:
    """Return the standalone invite landing page.

    Reuses the admin stylesheet so the page inherits the product's theme tokens,
    dark mode and responsive behaviour rather than approximating them.
    """
    return _PAGE.replace("__STYLESHEET__", stylesheet_href).replace(
        "__DOWNLOAD_URL__", DOWNLOAD_URL
    )


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="referrer" content="no-referrer">
<title>AutoYou Invitation</title>
<script>
  // The admin theme is attribute-driven (`:root[data-theme="dark"]`) and is
  // normally set by admin-ui.js, which this standalone page does not load.
  // Without this the page would render light for someone whose device is in
  // dark mode - and this page is often a new user's very first impression.
  // Runs before the stylesheet paints, so there is no flash of the wrong theme.
  (function () {
    try {
      var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
      document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    } catch (ignored) {
      document.documentElement.setAttribute("data-theme", "light");
    }
  })();
</script>
<link rel="stylesheet" href="__STYLESHEET__">
<style>
  /* Layout only. Every colour, radius and font comes from the admin theme
     tokens, so this page follows light/dark and any future rebrand for free. */
  body {
    margin: 0;
    min-height: 100dvh;
    display: grid;
    place-items: center;
    padding: 24px;
    background:
      radial-gradient(1200px 600px at 15% -10%, var(--ayu-bg-accent), transparent 60%),
      radial-gradient(900px 500px at 110% 10%, var(--ayu-bg-accent-soft), transparent 55%),
      linear-gradient(160deg, var(--ayu-bg-start), var(--ayu-bg-end));
    color: var(--ayu-text);
    font-family: var(--ayu-font-body);
  }
  .invite-shell { width: min(520px, 100%); display: grid; gap: 18px; }
  .invite-card {
    background: var(--ayu-surface-strong);
    border: 1px solid var(--ayu-border);
    border-radius: var(--ayu-radius-xl);
    box-shadow: var(--ayu-shadow);
    padding: clamp(22px, 5vw, 34px);
    display: grid;
    gap: 18px;
    text-align: center;
  }
  .invite-art { display: grid; place-items: center; }
  .invite-art svg { width: min(280px, 100%); height: auto; }
  .invite-eyebrow {
    font-family: var(--ayu-font-mono);
    font-size: 11px;
    letter-spacing: .16em;
    text-transform: uppercase;
    color: var(--ayu-faint);
  }
  .invite-title {
    font-family: var(--ayu-font-display);
    font-size: clamp(22px, 5vw, 28px);
    line-height: 1.2;
    margin: 0;
    text-wrap: balance;
  }
  .invite-copy { color: var(--ayu-muted); margin: 0; font-size: 15.5px; line-height: 1.6; }
  .invite-name {
    font-weight: 600;
    color: var(--ayu-text);
    overflow-wrap: anywhere;
  }
  .invite-actions { display: grid; gap: 10px; }
  .invite-meta {
    font-size: 13px;
    color: var(--ayu-faint);
    display: flex;
    gap: 8px;
    align-items: center;
    justify-content: center;
    flex-wrap: wrap;
  }
  .invite-privacy {
    border-top: 1px solid var(--ayu-border);
    padding-top: 14px;
    font-size: 13px;
    color: var(--ayu-muted);
    line-height: 1.55;
    text-align: left;
  }
  .invite-privacy b { color: var(--ayu-text); font-weight: 600; }
  [hidden] { display: none !important; }
  /* The connection line only animates once the invite is known to be valid,
     and never for someone who asked for reduced motion. */
  @media (prefers-reduced-motion: no-preference) {
    .invite-shell[data-state="ready"] .invite-pulse {
      animation: invite-pulse 2.4s ease-in-out infinite;
    }
  }
  @keyframes invite-pulse {
    0%, 100% { opacity: .35; }
    50%      { opacity: 1; }
  }
</style>
</head>
<body>
<main class="invite-shell" id="shell" data-state="loading">
  <section class="invite-card">
    <div class="invite-art" aria-hidden="true">
      <!-- Two devices, one link. Drawn from theme tokens so it themes with the page. -->
      <svg viewBox="0 0 280 120" role="presentation">
        <rect x="12" y="24" width="66" height="96" rx="12"
              fill="var(--ayu-surface-muted)" stroke="var(--ayu-border-strong)" stroke-width="2"/>
        <rect x="202" y="24" width="66" height="96" rx="12"
              fill="var(--ayu-surface-muted)" stroke="var(--ayu-border-strong)" stroke-width="2"/>
        <circle cx="45" cy="62" r="13" fill="var(--ayu-primary-soft)" stroke="var(--ayu-primary)" stroke-width="2"/>
        <circle cx="235" cy="62" r="13" fill="var(--ayu-primary-soft)" stroke="var(--ayu-primary)" stroke-width="2"/>
        <path class="invite-pulse" d="M86 66 H194" stroke="var(--ayu-primary)" stroke-width="3"
              stroke-linecap="round" stroke-dasharray="10 9"/>
        <circle class="invite-pulse" cx="140" cy="66" r="7" fill="var(--ayu-primary)"/>
      </svg>
    </div>

    <div id="state-loading">
      <p class="invite-eyebrow">AutoYou</p>
      <h1 class="invite-title">Checking this invitation…</h1>
    </div>

    <div id="state-ready" hidden>
      <p class="invite-eyebrow">You have been invited</p>
      <h1 class="invite-title"><span id="inviter-name">Someone</span> wants to connect on AutoYou</h1>
      <p class="invite-copy">
        Accepting links your devices directly. Your messages and calls travel
        between the two of you, not through AutoYou.
      </p>
      <div class="invite-actions">
        <a class="ayu-btn ayu-btn-primary" id="open-app" href="#">Open in AutoYou</a>
        <a class="ayu-btn ayu-btn-ghost" href="__DOWNLOAD_URL__" id="get-app">I don't have AutoYou yet</a>
      </div>
      <p class="invite-meta">
        <span class="ayu-badge ayu-badge-purple">Expires in 10 minutes</span>
        <span class="ayu-badge ayu-badge-gray">Can be used once</span>
      </p>
    </div>

    <div id="state-invalid" hidden>
      <p class="invite-eyebrow">AutoYou</p>
      <h1 class="invite-title">This invitation cannot be opened</h1>
      <p class="invite-copy" id="invalid-reason">
        It may have expired, already been used, or been shortened by the app it
        was sent through. Ask for a new link.
      </p>
      <div class="invite-actions">
        <a class="ayu-btn ayu-btn-ghost" href="__DOWNLOAD_URL__">Get AutoYou</a>
      </div>
    </div>

    <p class="invite-privacy">
      <b>This page cannot read your invitation.</b> The part after the
      <code>#</code> never leaves your device — not to this page, not to any
      server, not into a log. It is handed straight to the app.
    </p>
  </section>
</main>

<script>
(function () {
  "use strict";

  var shell = document.getElementById("shell");
  var PATTERNS = {
    id: /^[A-Za-z0-9_-]{16,128}$/,
    key: /^[A-Za-z0-9_-]{43}$/,
    rendezvous: /^(cloud|https:\\/\\/[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?)$/
  };

  function show(state, reason) {
    ["loading", "ready", "invalid"].forEach(function (name) {
      var node = document.getElementById("state-" + name);
      if (node) { node.hidden = name !== state; }
    });
    shell.setAttribute("data-state", state);
    if (state === "invalid" && reason) {
      // textContent, never innerHTML: this string is derived from an
      // attacker-authorable link.
      document.getElementById("invalid-reason").textContent = reason;
    }
  }

  function readPairs(source) {
    var out = {};
    if (!source) { return out; }
    source.replace(/^[#?]/, "").split("&").forEach(function (chunk) {
      if (!chunk) { return; }
      var index = chunk.indexOf("=");
      if (index < 0) { return; }
      var key = decodeURIComponent(chunk.slice(0, index));
      var value = decodeURIComponent(chunk.slice(index + 1).replace(/\\+/g, " "));
      // First value wins; a duplicated key is ambiguous.
      if (!Object.prototype.hasOwnProperty.call(out, key)) { out[key] = value; }
    });
    return out;
  }

  function sanitizeName(raw) {
    if (!raw) { return ""; }
    // Replace, do not delete: dropping a newline would join two words.
    var flattened = String(raw).replace(/[\\u0000-\\u001f\\u007f-\\u009f]/g, " ");
    return flattened.replace(/\\s+/g, " ").trim().slice(0, 48);
  }

  try {
    var query = readPairs(window.location.search);
    var fragment = readPairs(window.location.hash);

    var invitationID = query.i || "";
    var rendezvous = (query.r || "cloud").replace(/\\/+$/, "");
    var key = fragment.k || "";

    if (!key) {
      show("invalid",
        "The link is missing the part after the # symbol. Some apps shorten " +
        "links and cut it off — ask for it again as plain text.");
      return;
    }
    if (!PATTERNS.id.test(invitationID) ||
        !PATTERNS.key.test(key) ||
        !PATTERNS.rendezvous.test(rendezvous)) {
      show("invalid", "This link is not a valid AutoYou invitation.");
      return;
    }

    var name = sanitizeName(fragment.n);
    if (name) {
      document.getElementById("inviter-name").textContent = name;
    }

    // Hand the whole invite to the app over the custom scheme. This stays on
    // the device; the fragment is not transmitted anywhere.
    var appURL = "autoyou://peer/add?i=" + encodeURIComponent(invitationID) +
                 "&r=" + encodeURIComponent(rendezvous) +
                 "#k=" + encodeURIComponent(key) +
                 (name ? "&n=" + encodeURIComponent(name) : "");
    document.getElementById("open-app").setAttribute("href", appURL);

    // Someone without the app needs the invite to survive the install, so stash
    // it locally for the app to pick up on first launch. Session storage only:
    // it should not outlive the browser session.
    try {
      window.sessionStorage.setItem("autoyou.pendingInvite", appURL);
    } catch (ignored) {
      // Private browsing or blocked storage. The link still works if they
      // return to it, so this is a convenience rather than a requirement.
    }

    show("ready");
  } catch (error) {
    show("invalid", "This link could not be read.");
  }
})();
</script>
</body>
</html>
"""


__all__ = ["DOWNLOAD_URL", "render_invite_landing_page"]
