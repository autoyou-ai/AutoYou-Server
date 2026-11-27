// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

"use strict";

const watchButton = document.getElementById("watch-ad-btn");
const watchStatus = document.getElementById("watch-status");
const webFallbackShell = document.getElementById("web-fallback-shell");
const webFallbackFrame = document.getElementById("web-fallback-frame");
const celebration = document.getElementById("reward-celebration");
const celebrationAmount = document.getElementById("reward-amount");
const celebrationClose = document.getElementById("reward-close");
const themeToggle = document.getElementById("theme-toggle");

const PENDING_CREDITS_PER_SECOND = 10;
const CONFIRM_OPEN_TIMEOUT_MS = 14000;

let latestStatus = null;
let lastCelebratedKey = "";
let completionPollTimer = null;
let celebrationHideTimer = null;
let watchRequestPending = false;
let activeControlId = "";
let activeControlSentAt = 0;
let activeControlOpened = false;
let webFallbackOrigin = "";

if (themeToggle) {
  themeToggle.addEventListener("click", () => {
    try {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      localStorage.setItem("autoyou.ui.theme", next);
    } catch (_) {}
  });
}

function setStatus(message, state = "") {
  if (!watchStatus) return;
  watchStatus.textContent = message || "";
  if (state) {
    watchStatus.dataset.state = state;
  } else {
    delete watchStatus.dataset.state;
  }
}

function pageQueryParams() {
  const params = new URLSearchParams(window.location.search || "");
  const forwarded = new URLSearchParams();
  for (const key of ["session_id", "owner_key"]) {
    const value = String(params.get(key) || "").trim();
    if (value) forwarded.set(key, value);
  }
  return forwarded;
}

function apiUrl(path, extraParams) {
  const url = new URL(path, window.location.href);
  for (const [key, value] of pageQueryParams().entries()) {
    url.searchParams.set(key, value);
  }
  if (extraParams) {
    for (const [key, value] of Object.entries(extraParams)) {
      if (value) url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

function webFallbackAvailable(status) {
  return Boolean(status && status.web_fallback && status.web_fallback.available);
}

function nativeMobileTriggerAvailable(status) {
  const liveUnits = status && status.live_ad_units ? status.live_ad_units : {};
  return Boolean(liveUnits.ios_rewarded || liveUnits.android_rewarded);
}

function canAttemptWatch(status) {
  return nativeMobileTriggerAvailable(status) || webFallbackAvailable(status);
}

function isWebFallbackVisible() {
  return Boolean(webFallbackShell && !webFallbackShell.hidden);
}

function showWebFallback(watchUrl) {
  if (!watchUrl || !webFallbackShell || !webFallbackFrame) return false;
  try {
    webFallbackOrigin = new URL(watchUrl, window.location.href).origin;
  } catch {
    webFallbackOrigin = "";
  }
  webFallbackFrame.src = watchUrl;
  webFallbackShell.hidden = false;
  return true;
}

function hideWebFallback() {
  if (!webFallbackShell || !webFallbackFrame) return;
  webFallbackShell.hidden = true;
  webFallbackFrame.removeAttribute("src");
  webFallbackOrigin = "";
}

function syncButtonState(status) {
  if (!watchButton) return;
  watchButton.disabled = watchRequestPending || !canAttemptWatch(status);
}

function completionKey(completion) {
  if (!completion || typeof completion !== "object") return "";
  const controlId = String(completion.control_id || "").trim();
  if (controlId) return `control:${controlId}`;
  const ts = Number(completion.timestamp_ms || 0);
  return ts > 0 ? `ts:${ts}` : "";
}

function pendingCreditsForCompletion(completion) {
  const rawSeconds = Number(completion && completion.watched_seconds);
  const seconds = Number.isFinite(rawSeconds) && rawSeconds > 0
    ? Math.max(1, Math.min(120, rawSeconds))
    : 30;
  return Math.max(1, Math.round(seconds * PENDING_CREDITS_PER_SECOND));
}

function hideRewardCelebration() {
  if (!celebration) return;
  if (celebrationHideTimer) {
    clearTimeout(celebrationHideTimer);
    celebrationHideTimer = null;
  }
  celebration.classList.remove("is-visible");
  celebration.hidden = true;
}

function showRewardCelebration(amount) {
  if (!celebration || !celebrationAmount) return;
  celebrationAmount.textContent = `+${amount}`;
  celebration.hidden = false;
  celebration.classList.add("is-visible");
  if (celebrationHideTimer) clearTimeout(celebrationHideTimer);
  celebrationHideTimer = setTimeout(hideRewardCelebration, 2800);
}

function handleCompletion(completion, { silent = false } = {}) {
  const key = completionKey(completion);
  if (!key || key === lastCelebratedKey) return false;
  lastCelebratedKey = key;
  if (silent) return false;
  activeControlId = "";
  activeControlOpened = false;
  stopCompletionPolling();
  syncButtonState(latestStatus);
  showRewardCelebration(pendingCreditsForCompletion(completion));
  setStatus("Thanks for watching.", "ready");
  return true;
}

function handleNativeAdStatus(adStatus) {
  if (!adStatus || typeof adStatus !== "object") return false;
  if (activeControlId && adStatus.control_id && adStatus.control_id !== activeControlId) {
    return false;
  }
  const state = String(adStatus.state || "").trim().toLowerCase();
  const reason = String(adStatus.reason || "").trim();

  if (state === "opening" || state === "sent") {
    if (!activeControlOpened) {
      setStatus("Opening ad...", "pending");
    }
    return false;
  }
  if (state === "opened") {
    activeControlOpened = true;
    setStatus("Ad opened.", "ready");
    return false;
  }
  if (state === "failed") {
    activeControlId = "";
    activeControlOpened = false;
    stopCompletionPolling();
    syncButtonState(latestStatus);
    setStatus(reason || "The ad could not open on your phone. Please try again.", "blocked");
    return true;
  }
  if (state === "dismissed") {
    activeControlId = "";
    activeControlOpened = false;
    stopCompletionPolling();
    syncButtonState(latestStatus);
    setStatus("Ad closed before completion.", "pending");
    return true;
  }
  return false;
}

function stopCompletionPolling() {
  if (completionPollTimer) {
    clearInterval(completionPollTimer);
    completionPollTimer = null;
  }
}

function startCompletionPolling() {
  stopCompletionPolling();
  let polls = 0;
  completionPollTimer = setInterval(async () => {
    polls += 1;
    if (polls > 75) {
      activeControlId = "";
      activeControlOpened = false;
      stopCompletionPolling();
      syncButtonState(latestStatus);
      return;
    }

    if (activeControlId) {
      try {
        const res = await fetch(apiUrl("./api/watch-ad/status", { control_id: activeControlId }), {
          headers: { Accept: "application/json" },
        });
        if (res.ok) {
          const data = await res.json();
          if (data && data.rewarded_ad_completion) {
            if (handleCompletion(data.rewarded_ad_completion)) return;
          }
          if (data && data.ad_status) {
            if (handleNativeAdStatus(data.ad_status)) return;
          }
        }
      } catch (_) {}

      // If the remote phone never confirmed opening the ad within the timeout,
      // never leave the user stuck or falsely tell them the ad opened.
      if (!activeControlOpened && activeControlSentAt && (Date.now() - activeControlSentAt) > CONFIRM_OPEN_TIMEOUT_MS) {
        activeControlId = "";
        stopCompletionPolling();
        syncButtonState(latestStatus);
        setStatus("Your phone did not confirm opening the ad. Make sure AutoYou is open on your phone and try again.", "blocked");
        return;
      }
    } else {
      await refreshStatus({ celebrateCompletion: true });
    }
  }, 1000);
}

async function refreshStatus({ celebrateCompletion = false, showUnavailable = false } = {}) {
  try {
    const response = await fetch(apiUrl("./api/status"), {
      headers: { Accept: "application/json" },
    });
    const payload = await response.json();
    if (!response.ok || !payload || !payload.status) {
      throw new Error("Ad unavailable right now.");
    }
    latestStatus = payload.status;
    syncButtonState(latestStatus);
    if (payload.rewarded_ad_completion) {
      handleCompletion(payload.rewarded_ad_completion, { silent: !celebrateCompletion });
    }
    if (showUnavailable && !canAttemptWatch(latestStatus)) {
      setStatus("Ad unavailable right now.", "blocked");
    } else if (showUnavailable && canAttemptWatch(latestStatus) && !activeControlId) {
      setStatus("");
    }
  } catch {
    if (watchButton) watchButton.disabled = true;
    if (showUnavailable) setStatus("Ad unavailable right now.", "blocked");
  }
}

async function triggerWatchAd() {
  if (!watchButton || watchButton.disabled || watchRequestPending) return;
  watchRequestPending = true;
  activeControlId = "";
  activeControlOpened = false;
  syncButtonState(latestStatus);
  setStatus("Opening ad...", "pending");
  try {
    const query = pageQueryParams();
    const requestedControlId = `ad-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    const body = { control_id: requestedControlId };
    if (query.get("session_id")) body.session_id = query.get("session_id");
    if (query.get("owner_key")) body.owner_key = query.get("owner_key");

    const response = await fetch(apiUrl("./api/watch-ad"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    const result = payload && payload.result ? payload.result : {};
    const status = String(result.status || "").trim().toLowerCase();
    if (response.ok && payload && payload.success && status === "web_fallback" && result.watch_url) {
      showWebFallback(result.watch_url);
      setStatus("Ad opened.", "ready");
      return;
    }
    if (!response.ok || !payload || !payload.success || (Number(result.triggered_count || 0) <= 0 && status !== "already_active")) {
      throw new Error("Ad unavailable right now.");
    }
    hideWebFallback();
    activeControlId = String(result.control_id || requestedControlId || "").trim();
    activeControlSentAt = Date.now();
    activeControlOpened = status === "already_active";
    if (status === "already_active") {
      setStatus("Ad opened.", "ready");
    } else {
      // Wait for the mobile client to confirm presentation instead of falsely
      // claiming "Ad opened." before the phone actually opens the ad.
      setStatus("Opening ad...", "pending");
    }
    startCompletionPolling();
  } catch {
    setStatus("Ad unavailable right now.", "blocked");
    await refreshStatus();
  } finally {
    watchRequestPending = false;
    syncButtonState(latestStatus);
  }
}

async function handleWebFallbackMessage(event) {
  if (!isWebFallbackVisible()) return;
  if (!webFallbackFrame || event.source !== webFallbackFrame.contentWindow) return;
  if (webFallbackOrigin && event.origin !== webFallbackOrigin) return;
  const data = event.data;
  if (!data || typeof data !== "object" || data.type !== "autoyou_web_rewarded_completed") {
    return;
  }
  const controlId = String(data.control_id || "").trim();
  const watchedSeconds = Number(data.watched_seconds || 30);
  try {
    const response = await fetch(apiUrl("./api/web-rewarded-completed"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        control_id: controlId,
        watched_seconds: watchedSeconds,
      }),
    });
    const payload = await response.json();
    if (response.ok && payload && payload.success) {
      handleCompletion({
        control_id: controlId || `web-${Date.now()}`,
        watched_seconds: payload.reward && payload.reward.watched_seconds ? payload.reward.watched_seconds : watchedSeconds,
      });
    }
  } catch (_) {}
}

if (watchButton) {
  watchButton.addEventListener("click", triggerWatchAd);
}
if (celebrationClose) {
  celebrationClose.addEventListener("click", hideRewardCelebration);
}
window.addEventListener("message", handleWebFallbackMessage);

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    refreshStatus({ showUnavailable: true });
  });
} else {
  refreshStatus({ showUnavailable: true });
}
