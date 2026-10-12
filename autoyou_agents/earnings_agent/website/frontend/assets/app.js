// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

// AutoYou Earnings — coming-soon page. One fetch on load + manual refresh.
// Reads the local pending ad-credit tally only; never polls, never calls the cloud.

(function () {
  "use strict";

  var els = {
    count: document.getElementById("credit-count"),
    rate: document.getElementById("credit-rate"),
    copy: document.getElementById("credit-copy"),
    syncPill: document.getElementById("sync-pill"),
    lastEvent: document.getElementById("last-event"),
    refresh: document.getElementById("refresh-btn"),
    signinCopy: document.getElementById("signin-copy"),
    signinLink: document.getElementById("signin-link"),
    signinPill: document.getElementById("signin-pill"),
  };

  function formatWhen(timestampMs) {
    if (!timestampMs) { return ""; }
    try {
      var date = new Date(timestampMs);
      if (isNaN(date.getTime())) { return ""; }
      return date.toLocaleString();
    } catch (err) {
      return "";
    }
  }

  function animateCount(target) {
    var current = parseInt(els.count.textContent, 10) || 0;
    if (current === target) { return; }
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || Math.abs(target - current) > 400) {
      els.count.textContent = String(target);
      return;
    }
    var step = target > current ? 1 : -1;
    var timer = setInterval(function () {
      current += step;
      els.count.textContent = String(current);
      if (current === target) { clearInterval(timer); }
    }, Math.max(12, 600 / Math.abs(target - current)));
  }

  function render(payload) {
    var credits = (payload && payload.credits) || {};
    var cloud = (payload && payload.cloud) || {};
    animateCount(parseInt(credits.pending_credits, 10) || 0);
    var watchedSeconds = Number(credits.watched_seconds) || 0;
    var multiplier = parseInt(credits.credit_multiplier, 10) || 10;
    if (els.rate) {
      els.rate.textContent = watchedSeconds.toFixed(1).replace(/\.0$/, "") +
        " watched seconds × " + multiplier + " credits/second";
    }

    var events = parseInt(credits.events, 10) || 0;
    els.copy.textContent = events
      ? "Collected from " + events + " watched ad" + (events === 1 ? "" : "s") + " on your connected devices."
      : "Watch a support ad on your phone or desktop to collect your first pending credit.";

    var last = credits.last_event || null;
    if (last && last.timestamp_ms) {
      var when = formatWhen(last.timestamp_ms);
      els.lastEvent.textContent = "Last ad: " + (last.platform || "device") + (when ? " · " + when : "");
      els.lastEvent.hidden = false;
    } else {
      els.lastEvent.hidden = true;
    }

    if (cloud.signed_in) {
      els.signinPill.hidden = false;
      els.signinPill.classList.add("good");
      els.signinLink.hidden = true;
      els.signinCopy.textContent =
        "This server is signed in. Pending credits remain local and may be reviewed for future program eligibility; nothing is transferred or confirmed here.";
    } else {
      els.signinPill.hidden = true;
      if (cloud.sign_in_url) {
        els.signinLink.href = cloud.sign_in_url;
        els.signinLink.hidden = false;
      }
    }
  }

  function load() {
    els.syncPill.textContent = "Checking local credits…";
    fetch("./api/pending-credits", { headers: { Accept: "application/json" } })
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        if (payload && payload.success) {
          render(payload);
          els.syncPill.textContent = "Synced over your private connection";
        } else {
          els.syncPill.textContent = "Local credits unavailable right now";
        }
      })
      .catch(function () {
        els.syncPill.textContent = "Local credits unavailable right now";
      });
  }

  if (els.refresh) { els.refresh.addEventListener("click", load); }
  load();
})();
