// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

const els = {
  watchButton: document.getElementById("watch-ad-btn"),
  status: document.getElementById("watch-status"),
  fallbackShell: document.getElementById("web-fallback-shell"),
  fallbackFrame: document.getElementById("web-fallback-frame"),
  celebration: document.getElementById("reward-celebration"),
  rewardAmount: document.getElementById("reward-amount"),
  rewardClose: document.getElementById("reward-close"),
};

const PENDING_CREDITS_PER_SECOND = 10;
const MAX_WATCHED_SECONDS = 120;
let completionPollTimer = null;
let watchRequestPending = false;
let lastCompletionKey = "";
let completionBaselineKey = "";
let lastCelebrationKey = "";
const shouldAutoStart = new URLSearchParams(window.location.search).get("auto_start") === "1";
let autoStartTriggered = false;

function rewardedAdHints() {
  const params = new URLSearchParams(window.location.search);
  return {
    session_id: params.get("session_id") || "",
    owner_key: params.get("owner_key") || "",
    control_id: params.get("control_id") || "",
  };
}

function apiPath(path) {
  const url = new URL(path, window.location.href);
  const hints = rewardedAdHints();
  for (const [name, value] of Object.entries(hints)) {
    if (value) url.searchParams.set(name, value);
  }
  return url.pathname + (url.search ? `${url.search}` : "") + (url.hash || "");
}

function setStatus(message, state = "") {
  if (!els.status) return;
  els.status.textContent = message || "";
  els.status.dataset.state = state;
}

function setWatchEnabled(enabled) {
  if (els.watchButton) {
    els.watchButton.disabled = !enabled || watchRequestPending || Boolean(completionPollTimer);
  }
}

function completionKey(completion = {}) {
  const controlId = String(completion.control_id || "").trim();
  if (controlId) return `control:${controlId}`;
  return [
    completion.session_id || "",
    completion.control_id || "",
    completion.timestamp_ms || "",
  ].join(":");
}

function pendingCreditAmount(watchedSeconds) {
  const seconds = Number(watchedSeconds);
  if (!Number.isFinite(seconds) || seconds <= 0) return 0;
  const bounded = Math.min(MAX_WATCHED_SECONDS, Math.max(0, seconds));
  return Math.max(1, Math.round(bounded * PENDING_CREDITS_PER_SECOND));
}

function showCelebration(watchedSeconds, key = "") {
  const amount = pendingCreditAmount(watchedSeconds);
  if (!els.celebration || !els.rewardAmount || amount <= 0 || key === lastCelebrationKey) return;
  lastCelebrationKey = key;
  els.rewardAmount.textContent = `+${amount}`;
  els.celebration.hidden = false;
  els.celebration.classList.remove("is-visible");
  requestAnimationFrame(() => els.celebration.classList.add("is-visible"));
}

function hideCelebration() {
  if (!els.celebration) return;
  els.celebration.classList.remove("is-visible");
  els.celebration.hidden = true;
}

function stopCompletionPolling() {
  if (completionPollTimer) clearInterval(completionPollTimer);
  completionPollTimer = null;
  setWatchEnabled(true);
}

function renderCompletionPreview(completion = {}, options = {}) {
  if (!completion || completion.event !== "rewarded_ad_completed") return false;
  const key = completionKey(completion);
  if (key && key !== lastCompletionKey) {
    lastCompletionKey = key;
    showCelebration(completion.watched_seconds, key);
    if (options.announce !== false) setStatus("Thanks for watching.", "ready");
  }
  return true;
}

async function getJson(path, options = {}) {
  const response = await fetch(apiPath(path), {
    credentials: "include",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload?.result?.reason || payload.error || payload.detail || response.statusText;
    throw new Error(message);
  }
  return payload;
}

async function refreshStatus(options = {}) {
  try {
    const payload = await getJson("./api/status", { method: "GET", headers: {} });
    const status = payload.status || {};
    const units = status.live_ad_units || {};
    const fallback = status.web_fallback || {};
    const renderedCompletion = renderCompletionPreview(payload.rewarded_ad_completion || {}, {
      announce: options.announceCompletion !== false,
    });
    const readyToWatch = Boolean(
      fallback.available || (status.connection_proof?.connected && (units.ios_rewarded || units.android_rewarded)),
    );
    setWatchEnabled(readyToWatch);
    if (!renderedCompletion && !completionPollTimer && options.showUnavailable && !readyToWatch) {
      setStatus("Ad unavailable right now.", "blocked");
    } else if (!renderedCompletion && !completionPollTimer && readyToWatch) {
      setStatus("", "");
    }
    if (readyToWatch && shouldAutoStart && !autoStartTriggered && !completionPollTimer) {
      autoStartTriggered = true;
      setTimeout(() => watchAd(), 120);
    }
  } catch (err) {
    setWatchEnabled(false);
    if (options.showUnavailable) setStatus("Ad unavailable right now.", "blocked");
  }
}

function openWebFallback(result) {
  const watchUrl = String(result.watch_url || "").trim();
  if (!watchUrl) return false;
  if (result.mode === "gpt_rewarded" && els.fallbackShell && els.fallbackFrame) {
    els.fallbackFrame.src = watchUrl;
    els.fallbackShell.hidden = false;
    setStatus("Ad opened.", "ready");
    return true;
  }
  const opened = window.open(watchUrl, "_blank", "noopener,noreferrer");
  if (!opened) window.location.assign(watchUrl);
  setStatus("Ad opened in a new window.", "ready");
  return true;
}

async function persistWebCompletion(data) {
  return getJson("./api/web-rewarded-completed", {
    method: "POST",
    body: JSON.stringify({
      control_id: String(data.control_id || "").slice(0, 128),
      watched_seconds: data.watched_seconds,
      timestamp_ms: data.timestamp_ms,
    }),
  });
}

async function watchAd() {
  if (!els.watchButton || watchRequestPending || completionPollTimer) return;
  watchRequestPending = true;
  setWatchEnabled(false);
  setStatus("Opening ad...", "pending");
  const hints = rewardedAdHints();
  const requestPayload = hints.control_id ? { control_id: hints.control_id } : {};
  try {
    const response = await getJson("./api/watch-ad", {
      method: "POST",
      body: JSON.stringify(requestPayload),
    });
    const result = response.result || {};
    const count = Number(result.triggered_count || 0);
    if (response.success && count > 0) {
      setStatus("Ad opened.", "ready");
      startCompletionPolling();
    } else if (response.success && result.status === "already_active") {
      setStatus("Ad already open.", "pending");
      startCompletionPolling();
    } else if (response.success && result.status === "web_fallback" && openWebFallback(result)) {
      // The public page owns the Google ad UI; this shell only hosts an approved
      // GPT rewarded page and never renders a generic AdSense unit in an iframe.
    } else {
      setStatus("Ad unavailable right now.", "blocked");
    }
  } catch (err) {
    setStatus("Ad unavailable right now.", "blocked");
  } finally {
    watchRequestPending = false;
    setWatchEnabled(!completionPollTimer);
  }
}

function startCompletionPolling() {
  if (completionPollTimer) clearInterval(completionPollTimer);
  completionBaselineKey = lastCompletionKey;
  let attempts = 0;
  completionPollTimer = setInterval(async () => {
    attempts += 1;
    await refreshStatus();
    if ((lastCompletionKey && lastCompletionKey !== completionBaselineKey) || attempts >= 20) {
      stopCompletionPolling();
    }
  }, 1500);
}

window.addEventListener("message", async (event) => {
  if (els.fallbackFrame && event.source !== els.fallbackFrame.contentWindow) return;
  const data = event.data || {};
  if (data.type !== "autoyouRewardedAdCompleted") return;
  const controlId = String(data.control_id || "").trim();
  const key = controlId ? `control:${controlId}` : `web:${data.timestamp_ms || Date.now()}`;
  try {
    await persistWebCompletion(data);
    showCelebration(data.watched_seconds, key);
    setStatus("Thanks for watching.", "ready");
  } catch (err) {
    setStatus("Thanks for watching. Local credit sync is pending.", "ready");
  }
});

els.rewardClose?.addEventListener("click", hideCelebration);
els.watchButton?.addEventListener("click", watchAd);
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => refreshStatus({ showUnavailable: true }));
} else {
  refreshStatus({ showUnavailable: true });
}
